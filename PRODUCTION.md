# Production deployment guide — AnythingLLM + Claude + SharePoint (Ubuntu)

Step-by-step runbook to move the assistant into production on an **Ubuntu server**, after
testing locally on Windows. Nothing is taken for granted. Differences between **Windows test**
and **Ubuntu production** are called out in `▶ Windows vs Ubuntu` boxes.

---

## 0. Key idea: how SharePoint becomes a local folder

The assistant does not talk to SharePoint via API: it **sees a local folder** kept in sync with
SharePoint. What performs the sync differs between the two environments:

| Aspect | Windows test | Ubuntu production |
|---|---|---|
| Who syncs SharePoint ↔ local folder | **OneDrive** client (already installed) | **rclone** (FUSE mount) |
| Host folder | `C:\Users\Matteo\Rewave Srl\Rewave - Information Technology` | `/mnt/sharepoint` |
| `SHAREPOINT_MOUNT_PATH` in `.env` | that Windows path | `/mnt/sharepoint` |
| Bind-mount into the container | same | same |
| Path seen by the agent in the container | `/app/server/storage/anythingllm-fs/sharepoint` | same |
| "File System" skill config in the UI | toggle **On** only | toggle **On** only (identical) |

> **How the File System skill works on Docker**: it operates **only** inside
> `/app/server/storage/anythingllm-fs/`. On Docker there is **no folder picker and no read/write
> toggle in the UI** (that only exists on Desktop, v1.12.0+). The accessible folders are
> **exactly the bind-mounts** you place under `anythingllm-fs/`, and read/write is decided by the
> **volume** (`:ro` = read-only; no suffix = read/write). So: in the UI you just flip the toggle;
> the `sharepoint` folder is already accessible because it is bind-mounted. Only *who syncs it*
> with SharePoint changes (OneDrive on Windows, rclone on Ubuntu).

---

## 1. Ubuntu server preparation

Assumptions: Ubuntu 22.04/24.04 LTS, `sudo` access, user `ubuntu` (adjust if different).

### 1.1 Update the system
```bash
sudo apt update && sudo apt upgrade -y
```

### 1.2 Install Docker Engine + Compose plugin
```bash
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```
Add the user to the docker group (avoids `sudo` on docker commands):
```bash
sudo usermod -aG docker $USER
newgrp docker    # or log out/in
docker run hello-world   # verify
```

### 1.3 Install FUSE (needed by rclone mount)
```bash
sudo apt install -y fuse3
# enable allow_other (the container needs to read the mounted folder):
sudo sed -i 's/^#user_allow_other/user_allow_other/' /etc/fuse.conf
grep user_allow_other /etc/fuse.conf   # must be UNcommented
```

---

## 2. rclone: mount the SharePoint library

> ▶ **Windows vs Ubuntu**: this whole section **exists only in production**. On Windows rclone
> is not needed: the folder is already synced by the OneDrive client.

### 2.1 Install rclone
```bash
curl https://rclone.org/install.sh | sudo bash
rclone version
```

### 2.2 Configure the SharePoint remote (headless)
The server has no browser, so OAuth authorization is done from a PC with a browser.

On Ubuntu:
```bash
rclone config
```
- `n` (new remote) → name: `rewaveSP`
- Storage: search for **OneDrive** (`onedrive`)
- `client_id` / `client_secret`: leave empty (uses rclone defaults) — or enter those of a
  dedicated Entra app if IT requires it
- Region: `1` (Microsoft Cloud Global)
- `Edit advanced config?` → `n`
- `Use auto config?` → **`n`** (headless)
- rclone prints a command like: `rclone authorize "onedrive"`

On a PC (Windows/Mac) with rclone installed **and** a browser, run that command, sign in with
**the dedicated M365 service account**, and copy the JSON token rclone prints. Paste it back
into the wizard on Ubuntu.

- Connection type: choose **SharePoint site** (`Sharepoint site name or URL` /
  "Search for a Sharepoint site"), look up the site that contains the
  **Rewave - Information Technology** library
- Select the correct **drive** (document library) from the list
- Confirm and quit (`q`)

### 2.3 Verify access
```bash
rclone lsd rewaveSP:               # list top-level folders of the library
rclone ls rewaveSP: | head         # list files
```
If you need to point at a specific subfolder, note the path (e.g. `rewaveSP:` for the library
root, or `rewaveSP:SubFolder`).

### 2.4 Persistent mount via systemd
The repo contains `deploy/rclone-sharepoint.service`. Adjust it:
- `User=` / `Group=` = the server user (e.g. `ubuntu`)
- `Environment=RCLONE_CONFIG=` = path to `rclone.conf` (check with `rclone config file`)
- in the `ExecStart` line, replace `rewaveSP:"Documenti/CartellaTarget"` with the real path
  verified in 2.3 (e.g. just `rewaveSP:`)

Then:
```bash
sudo mkdir -p /mnt/sharepoint
sudo cp deploy/rclone-sharepoint.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rclone-sharepoint
systemctl status rclone-sharepoint      # must be active (running)
ls -la /mnt/sharepoint                   # must show the real library files
```
Write test (critical — needed for file creation):
```bash
echo "test $(date)" | sudo tee /mnt/sharepoint/_rclone-write-test.txt
# wait a few seconds and confirm the file appears on SharePoint web,
# then remove it:
rm /mnt/sharepoint/_rclone-write-test.txt
```
If the write fails: check that the mount uses `--vfs-cache-mode writes` and that the service
account has write permission on the library.

### 2.5 Permissions of the rclone config file (holds the token)
```bash
chmod 600 ~/.config/rclone/rclone.conf
```

---

## 3. Deploy AnythingLLM (Docker)

### 3.1 Bring the project onto the server
Clone/copy the project folder (`docker-compose.yml`, `deploy/`, etc.) onto the server,
e.g. into `/opt/anythingllm-rewave`.

### 3.2 Create the production `.env`
> ▶ **Windows vs Ubuntu**: the test `.env` (Windows path) **must not be copied to prod**. In
> production `SHAREPOINT_MOUNT_PATH` is the rclone mount.

```bash
cat > .env <<'EOF'
# Anthropic API key (console.anthropic.com)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx
# Default model
ANTHROPIC_MODEL_PREF=claude-sonnet-4-6
# SharePoint folder mounted by rclone
SHAREPOINT_MOUNT_PATH=/mnt/sharepoint
EOF
chmod 600 .env
```
On Ubuntu the path has no `C:` and no spaces, so the short volume syntax would also work; the
`docker-compose.yml` uses the long syntax anyway (compatible with both).

### 3.3 Start
```bash
docker compose pull
docker compose up -d
docker compose logs -f          # check it starts without errors (Ctrl-C to exit)
```
UI reachable at `http://<server-IP>:3001` (LAN only for now; HTTPS at step 8).

### 3.4 Verify the bind-mount inside the container
```bash
docker exec -it anythingllm ls -la /app/server/storage/anythingllm-fs/sharepoint
```
Must show the same files as `/mnt/sharepoint`.

---

## 4. First access: admin and multi-user

> ▶ **Windows vs Ubuntu**: **identical** in both environments. There is no "create admin" wizard.

1. Open the UI.
2. **Settings** menu (gear icon, bottom-left).
3. **Security** section.
4. Toggle **Multi-User Mode** on.
5. The **username** and **password** fields for the first **admin** appear → fill and save.
6. ⚠️ Multi-user is **irreversible**: enable it only when ready for the team.
7. Later (step 7) create the employee accounts (Admin / Manager / Default roles).

---

## 5. LLM provider (Anthropic)

> ▶ **Windows vs Ubuntu**: identical. The model comes from `.env` (`ANTHROPIC_MODEL_PREF`).

1. **Settings → LLM Preference → Anthropic**.
2. Confirm the API key is picked up from the environment and the model is `claude-sonnet-4-6`.
3. Save. Ask a basic question in chat to confirm it responds.

---

## 6. Agent Skills (manual activation)

> ▶ **Windows vs Ubuntu**: **identical**. Skill activation lives in the DB, not in a config
> file: it must be done by hand in the UI in **both** environments. There is no env var to enable
> them in bulk.

Path: **Settings → Agent Skills**.

Default state after install:
- **On** by default: *RAG & long-term memory*, *View & summarize documents*, *Web browsing*.
- **Off** by default: *File system access*, *Document creation*, *Generate charts*,
  *Real-time web search & browsing*, *SQL connector*.

To enable (turn **On**):

| Skill | Enable? | Notes |
|---|---|---|
| File system access | ✅ **YES (required)** | This is the skill that **searches, reads, and creates files** in the SharePoint folder. On Docker just flip it On: the folder is the bind-mount, no UI config (see below). |
| Document creation | ➕ optional | Generates documents the user can download. It is **not** the one that writes to SharePoint (that is "File system access"). Enable only if you need browser-downloadable files. |
| Generate charts | ➕ optional | Charts/visualizations in responses. |
| Real-time web search & browsing | ➕ optional | "Extended" web search. Basic search is already covered by *Web browsing* (already On). |
| SQL connector | ❌ **NO** | Leave **Off** (no database connected). |

### 6.1 "File system access" on Docker — no folder config in the UI
On Docker there is **no** folder picker and **no** read/write toggle in the UI (those exist only
on Desktop). So:
1. Turn the *File system access* toggle **On**. That's it — no folder panels open.
2. The accessible folder is the one **bind-mounted** in `docker-compose.yml` (`sharepoint`, under
   `anythingllm-fs/`). If it doesn't show up / doesn't work, the problem is the bind-mount, not
   the UI → check §3.4.
3. **Read/write is decided by the volume**, not the UI:
   - no suffix (current config) → **read/write** (the agent creates files);
   - `:ro` on the volume → read-only (no creation).

> ▶ **Windows vs Ubuntu**: **identical**. Both are just the On toggle; the folder comes from the
> bind-mount. Only what fills it changes (OneDrive on Windows, rclone on Ubuntu).

> ⚠️ **Read + create vs modify/delete**: a read/write volume also allows modifying and deleting
> existing files; there is no "create-only" level (neither in the UI nor via the volume, if write
> is needed). The "do not modify/delete" constraint is enforced via the **system prompt**
> (step 7). To truly lock it down you would need a `:ro` mount of the library plus a separate
> writable subfolder (out of current scope).

---

## 7. Workspace, system prompt, and users

> ▶ **Windows vs Ubuntu**: identical.

1. Create a **Workspace** "Office Assistant".
2. Set the model (`claude-sonnet-4-6`).
3. **System prompt** (draft):
   ```
   You are Rewave's internal assistant. For files, use the SharePoint folder through the
   File System agent; for up-to-date information, use web search. Never invent file names or
   paths: verify first with a search in the folder. Do NOT modify or delete existing files: you
   may only read them, summarize them, and create new files. Before creating a file, show a
   summary of the content and path and ask for confirmation. Reply in Italian.
   ```
4. **Users**: Settings → Users → create the employee accounts (**Default** role), assign them to
   the workspace. Reserve **Admin/Manager** for whoever runs the instance.

---

## 8. Reverse proxy + HTTPS (production only)

> ▶ **Windows vs Ubuntu**: in Windows test you use `http://localhost:3001` with no proxy. In
> **production** always put HTTPS in front and do not expose port 3001 in the clear.

- Install a reverse proxy (Caddy recommended for automatic HTTPS, or Nginx + certbot).
- Example `Caddyfile`:
  ```
  assistente.rewave.local {
      reverse_proxy localhost:3001
  }
  ```
- Restrict access to the **internal network / VPN** (firewall: close 3001 from outside, expose
  only the proxy's 443).

---

## 9. Security (production)

- **Shared identity**: all employees act with the permissions of the rclone service account on
  the library. Give that account access **only** to the target library.
- **Secrets**: `.env` and `rclone.conf` with mode `600`, out of the git repo (already in
  `.gitignore`).
- **HTTPS** mandatory; instance reachable from LAN/VPN only.
- **Backups**: back up the `./storage` folder (holds the user DB, workspaces, config).
- **Rotation**: schedule periodic rotation of the Anthropic API key and the rclone token.
- **Updates**: `docker compose pull && docker compose up -d` to update the image; review the
  AnythingLLM release notes before updating in prod.

---

## 10. End-to-end verification (production)

1. `systemctl status rclone-sharepoint` → active; `ls /mnt/sharepoint` → real files.
2. `docker exec -it anythingllm ls /app/server/storage/anythingllm-fs/sharepoint` → same files.
3. Basic chat → OK response (Anthropic, `claude-sonnet-4-6`).
4. `@agent search the web for <recent news>` → results with sources.
5. `@agent search the folder for <topic> and summarize <file>` → coherent summary.
6. `@agent create test-prod.txt with "hello" in the folder` → the file appears in
   `/mnt/sharepoint` and on SharePoint web. Then remove it.
7. Try `@agent delete <file>` → must refuse (guard-rail via system prompt).
8. Login with a **Default** user → workspace accessible and tools work.

---

## ✅ "TO DO IN PRODUCTION" checklist

Things you did not do in Windows test (or did differently) that must be done in prod:

- [ ] Install Docker + Compose on the Ubuntu server (§1.2)
- [ ] Install `fuse3` and enable `user_allow_other` (§1.3)
- [ ] Install rclone and configure the `rewaveSP` remote (headless authorize) with the **M365 service account** (§2.1–2.2)
- [ ] Verify read **and write** on the `/mnt/sharepoint` mount (§2.4)
- [ ] Install and enable the `rclone-sharepoint` systemd service (§2.4)
- [ ] `chmod 600` on `rclone.conf` and `.env` (§2.5, §3.2)
- [ ] Create the **production** `.env` with `SHAREPOINT_MOUNT_PATH=/mnt/sharepoint` (NOT the Windows path) (§3.2)
- [ ] `docker compose up -d` and verify the bind-mount inside the container (§3.3–3.4)
- [ ] Enable multi-user + create admin (§4)
- [ ] Verify the Anthropic provider + model (§5)
- [ ] **Enable the Agent Skills by hand** (File system access = required; the others optional; SQL = Off) (§6)
- [ ] Verify the `sharepoint` bind-mount is visible in the container (on Docker the File System skill has no folder config in the UI: read/write depends on the volume) (§3.4, §6.1)
- [ ] Create the workspace + system prompt with the no-modify/no-delete guard-rail (§7)
- [ ] Create the employee accounts (Default role) (§7)
- [ ] Put a reverse proxy + HTTPS in place, close 3001 from outside (§8)
- [ ] Set up backups of `./storage` and secret rotation (§9)
- [ ] Run the full end-to-end verification (§10)

### (Optional) Make skill activation reproducible
Skills are enabled by hand in the UI (in the DB, not in a file). To avoid redoing them from
memory on every reinstall:
1. Enable them once in the UI.
2. Read the state from the DB: `system_settings` → key `default_agent_skills` in
   `./storage/anythingllm.db` (array of skill ids).
3. Save that value as a reference (or seed script) for future redeploys.

### (Optional) Tool auto-approval
Adding `AGENT_AUTO_APPROVED_SKILLS=<all>` to `.env` makes tools run **without** asking for
confirmation each time. Convenient, but it reduces human control over file creation: consider it
only after validating the behavior in testing.
