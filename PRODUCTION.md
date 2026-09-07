# Production deployment guide — AnythingLLM + Claude + SharePoint (Ubuntu)

Step-by-step runbook to move the assistant into production on an **Ubuntu server**, after
testing locally on Windows. Nothing is taken for granted. Differences between **Windows test**
and **Ubuntu production** are called out in `▶ Windows vs Ubuntu` boxes.

---

## 0. Key idea: how SharePoint becomes a local folder

The assistant does not talk to SharePoint via API: it **sees a local folder** kept in sync with
SharePoint. A **OneDrive client** performs the sync in both environments — only *which* client
differs:

| Aspect | Windows test | Ubuntu production |
|---|---|---|
| Who syncs SharePoint ↔ local folder | **OneDrive** client (Microsoft, already installed) | **abraunegg/onedrive** (open-source Linux client) |
| Sync type | real local sync (files on disk) | real local sync (files on disk) |
| Host folder | `<local OneDrive sync folder>` | `/mnt/sharepoint` |
| `SHAREPOINT_MOUNT_PATH` in `.env` | that Windows path | `/mnt/sharepoint` |
| Bind-mount into the container | same | same |
| Path seen by the agent in the container | `/app/server/storage/anythingllm-fs/sharepoint` | same |
| "File System" skill config in the UI | toggle **On** only | toggle **On** only (identical) |

> **Why abraunegg and not rclone**: rclone gives a FUSE *network mount* (files fetched on
> demand). The abraunegg client does a **real bidirectional sync** — files live on local disk,
> so reads by the File System skill are fast and there is no FUSE/`allow_other` plumbing. Cost:
> disk space equal to the synced set (limit it with a `sync_list`).

> **How the File System skill works on Docker**: it operates **only** inside
> `/app/server/storage/anythingllm-fs/`. On Docker there is **no folder picker and no read/write
> toggle in the UI** (that only exists on Desktop, v1.12.0+). The accessible folders are
> **exactly the bind-mounts** you place under `anythingllm-fs/`, and read/write is decided by the
> **volume** (`:ro` = read-only; no suffix = read/write). So: in the UI you just flip the toggle;
> the `sharepoint` folder is already accessible because it is bind-mounted. Only *who syncs it*
> with SharePoint changes (Microsoft OneDrive on Windows, abraunegg on Ubuntu).

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

### 1.3 Install the OneDrive client (abraunegg)
Microsoft ships **no** official OneDrive client for Linux. Use **abraunegg/onedrive** (the
de-facto Linux client; supports SharePoint document libraries). Install the up-to-date build
from the OpenSuSE Build Service repo (the Ubuntu-archive package is often stale):
```bash
wget -qO - https://download.opensuse.org/repositories/home:/npreining:/debian-ubuntu-onedrive/xUbuntu_$(lsb_release -rs)/Release.key | sudo gpg --dearmor -o /usr/share/keyrings/obs-onedrive.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/obs-onedrive.gpg] https://download.opensuse.org/repositories/home:/npreining:/debian-ubuntu-onedrive/xUbuntu_$(lsb_release -rs)/ ./" | sudo tee /etc/apt/sources.list.d/onedrive.list
sudo apt update && sudo apt install -y onedrive
onedrive --version    # expect v2.5.x or newer
```

---

## 2. OneDrive client: sync the SharePoint library

> ▶ **Windows vs Ubuntu**: this whole section **exists only in production**. On Windows the
> Microsoft OneDrive client already syncs the folder — nothing to install.

### 2.1 Prepare the sync folder
The client (running as user `ubuntu`) must own the sync folder:
```bash
sudo mkdir -p /mnt/sharepoint
sudo chown ubuntu:ubuntu /mnt/sharepoint
```

### 2.2 Authenticate (headless)
The server has no browser, so OAuth is done from a PC with a browser.
```bash
onedrive
```
- The client prints a long **login URL**. Open it on a PC/Mac with a browser.
- Sign in with the **dedicated M365 service account** (the one with access to the library).
- After consent the browser lands on a **blank page** — copy the full URL from the address bar
  and paste it back into the terminal.

> ⚠️ Requires the **admin/service account** credentials + tenant consent. If the account is not
> ready, do this step later; everything after depends on it.

### 2.3 Find the SharePoint library drive_id
The default sync targets the account's *personal* OneDrive. To sync a **SharePoint document
library** instead, get its `drive_id`:
```bash
onedrive --get-O365-drive-id 'Rewave - Information Technology'
```
Copy the `drive_id` value from the output.

### 2.4 Configure
Create `~/.config/onedrive/config` (template in the repo: `deploy/onedrive-config.example`):
```ini
sync_dir = "/mnt/sharepoint"
drive_id = "<drive_id from 2.3>"
```
Optional: limit which subfolders sync (saves disk on large libraries) with
`~/.config/onedrive/sync_list` — one path per line.

### 2.5 First sync (by hand, once)
Always dry-run first, then the real sync:
```bash
onedrive --sync --dry-run --verbose      # review what it WOULD do
onedrive --sync --verbose                # first full sync (can take a while)
ls -la /mnt/sharepoint                    # must show the real library files
```

### 2.6 Persistent sync via systemd
The repo contains `deploy/onedrive-sharepoint.service` (runs `onedrive --monitor`). Adjust it:
- `User=` / `Group=` = the server user that ran auth (e.g. `ubuntu`)
- `--confdir=` path = that user's `~/.config/onedrive`
- keep `UMask=0022` (so synced files are `644`/`755` and the container can read them)

Then:
```bash
sudo cp deploy/onedrive-sharepoint.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now onedrive-sharepoint
systemctl status onedrive-sharepoint     # must be active (running)
journalctl -u onedrive-sharepoint -f     # watch sync activity (Ctrl-C to exit)
```
Write test (critical — needed for file creation by the agent):
```bash
echo "test $(date)" > /mnt/sharepoint/_od-write-test.txt
# wait for the monitor cycle, then confirm the file appears on SharePoint web,
# then remove it:
rm /mnt/sharepoint/_od-write-test.txt
```
If writes don't propagate: check the service is running, the account has write permission on the
library, and there are no `skip_*` rules excluding the file.

### 2.7 Permissions of the OneDrive config/token
The refresh token lives under `~/.config/onedrive/`. Lock it down:
```bash
chmod 700 ~/.config/onedrive
chmod 600 ~/.config/onedrive/refresh_token 2>/dev/null || true
```

---

## 3. Deploy AnythingLLM (Docker)

### 3.1 Bring the project onto the server
Clone/copy the project folder (`docker-compose.yml`, `deploy/`, etc.) onto the server,
e.g. into `/opt/anythingllm-rewave`.

### 3.2 Create the production `.env`
> ▶ **Windows vs Ubuntu**: the test `.env` (Windows path) **must not be copied to prod**. In
> production `SHAREPOINT_MOUNT_PATH` is the onedrive sync folder.

```bash
cat > .env <<'EOF'
# Signs login session tokens. MUST be set here: AnythingLLM otherwise stores it in the
# container's internal /app/server/.env, which is wiped on every recreate/rebuild -> login then
# fails ("Could not validate login"). Setting it in compose makes it survive. Generate once with
# `openssl rand -hex 32` and paste the result below.
JWT_SECRET=REPLACE_WITH_openssl_rand_hex_32
# Anthropic API key (console.anthropic.com) — read by RouteLLM (litellm) to call the models
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx
# SharePoint folder synced by the OneDrive client
SHAREPOINT_MOUNT_PATH=/mnt/sharepoint
# --- RouteLLM (complexity-based routing) — see §5
ROUTELLM_ROUTER=bert                           # bert (fully local) | mf (needs OPENAI_API_KEY)
ROUTELLM_THRESHOLD=0.11593                      # share of traffic to the strong model (calibrate, §5)
ROUTELLM_STRONG_MODEL=anthropic/claude-sonnet-4-6   # complex tasks
ROUTELLM_WEAK_MODEL=anthropic/claude-haiku-4-5      # simple/cheap tasks
ROUTELLM_TEMPERATURE=0.3                             # fixed temperature forced on every request
ROUTELLM_API_KEY=change-me-routellm-shared-secret   # shared secret AnythingLLM <-> RouteLLM
OPENAI_API_KEY=                                 # only for the mf router embeddings; empty if bert
GENERIC_OPEN_AI_MODEL_TOKEN_LIMIT=200000
GENERIC_OPEN_AI_MAX_TOKENS=4096
AGENT_AUTO_APPROVED_SKILLS=<all>                # auto-approve agent tools (no per-call confirm)
EOF
chmod 600 .env
```
On Ubuntu the path has no `C:` and no spaces, so the short volume syntax would also work; the
`docker-compose.yml` uses the long syntax anyway (compatible with both).

### 3.3 Start
```bash
docker compose pull                 # pulls the anythingllm + caddy images
docker compose up -d --build        # --build compiles the local routellm image (routellm/)
docker compose logs -f              # check it starts without errors (Ctrl-C to exit)
```
UI reachable at `http://<server-IP>:3001` (LAN only for now). The bundled Caddy service already
fronts it over HTTPS at the configured domain — see step 8 to finalize the domain, CA/cert, and
firewall.

### 3.4 Verify the bind-mount inside the container
```bash
docker exec -it anythingllm ls -la /app/server/storage/anythingllm-fs/sharepoint
```
Must show the same files as `/mnt/sharepoint`. If empty or permission-denied: the container user
can't read the files → confirm `UMask=0022` on the onedrive service and re-sync.

---

## 4. First access: admin and multi-user

> ▶ **Windows vs Ubuntu**: **identical** in both environments. There is no "create admin" wizard.

> ⚠️ **Set `JWT_SECRET` in `.env` (§3.2).** AnythingLLM keeps its login-token secret in the
> container's internal `/app/server/.env`, which a `--force-recreate`/rebuild wipes → login then
> fails with "Could not validate login" (users/passwords survive in `./storage`; only the token
> secret is lost). Providing it via compose env makes it stable across recreates.

1. Open the UI.
2. **Settings** menu (gear icon, bottom-left).
3. **Security** section.
4. Toggle **Multi-User Mode** on.
5. The **username** and **password** fields for the first **admin** appear → fill and save.
6. ⚠️ Multi-user is **irreversible**: enable it only when ready for the team.
7. Later (step 7) create the employee accounts (Admin / Manager / Default roles).

---

## 5. LLM provider (RouteLLM — complexity-based routing)

> ▶ **Windows vs Ubuntu**: identical. Everything comes from `.env`; nothing to pick in the UI.

AnythingLLM does **not** call Anthropic directly. It points at the **`routellm`** service (built from
`routellm/`, no official image), an OpenAI-compatible gateway that scores each prompt and routes it
to the **strong** model (complex tasks) or the **weak/cheap** model (simple tasks).

**Data flow**: `AnythingLLM (generic-openai → http://routellm:6060/v1)` → `RouteLLM` → `litellm` →
`Anthropic`. RouteLLM is internal only (port `6060`, not published); AnythingLLM reaches it by the
container name on the compose network.

### 5.1 Configure (`.env`)
| Var | Meaning |
|---|---|
| `ROUTELLM_ROUTER` | `bert` (fully local, no OpenAI — current default) · `mf` (needs `OPENAI_API_KEY`) · `sw_ranking` · `causal_llm` |
| `ROUTELLM_THRESHOLD` | Routing threshold. **For `bert`: HIGHER = more traffic to the WEAK/cheap model** (§5.2). Router-specific. |
| `ROUTELLM_STRONG_MODEL` | litellm id, complex tasks — e.g. `anthropic/claude-sonnet-4-6` |
| `ROUTELLM_WEAK_MODEL` | litellm id, simple tasks — e.g. `anthropic/claude-haiku-4-5` (or a local `ollama/...` = free) |
| `ROUTELLM_TEMPERATURE` | Fixed temperature forced on every request (default `0.3`), overriding the client; `top_p` cleared server-side (`routellm/patch_schema.py`). |
| `ROUTELLM_API_KEY` | Value for AnythingLLM's "API Key" field. RouteLLM does **not** validate inbound auth (internal-only), so any non-empty string. **Not** passed to RouteLLM as `--api-key` (would override the provider keys). |
| `OPENAI_API_KEY` | Only for the `mf`/`sw_ranking` router embeddings (cheap). Empty with `bert`. |

AnythingLLM requests the model `router-${ROUTELLM_ROUTER}-${ROUTELLM_THRESHOLD}` (e.g. `router-bert-0.46514`).

### 5.2 Calibrate the threshold
Calibrated `bert` thresholds (this deploy defaults to `0.46514` ≈ 30% strong / 70% weak):

| Target strong-% | threshold |
|---|---|
| 20% | `0.50944` |
| 30% | `0.46514` (default) |
| 40% | `0.43431` |
| 50% | `0.4066` |

Recalibrate (runs in the container; `pandarallel` is baked in the image):
```bash
docker exec -it routellm python -m routellm.calibrate_threshold \
  --task calibrate --routers bert --strong-model-pct 0.3 --config /app/config.yaml
# prints the threshold for ~30% strong calls -> set ROUTELLM_THRESHOLD, then reconfigure AnythingLLM's model name
```

### 5.3 Verify
1. `docker compose ps` → `routellm` reaches STATUS **`healthy`** (bert loads a HuggingFace model on
   boot, **~2 min**). AnythingLLM waits for this via `depends_on: condition: service_healthy`, so it
   only starts once the router is serving — no more failed first chats. Watch with `docker compose logs -f routellm`.
2. In chat: a trivial prompt should hit the **weak** model; a hard one the **strong** model
   (watch the `routellm` logs to see which model each request went to).
3. ⚠️ **Test `@agent` explicitly** (file/web tools). Tool-calling must survive the
   AnythingLLM → RouteLLM → litellm → Anthropic hops.

### 5.4 Gotchas already handled in this repo
Documented so nobody re-debugs them:
- **`LITELLM_DROP_PARAMS=True`** (compose) — AnythingLLM sends `presence_penalty`/`frequency_penalty`; Anthropic rejects them → dropped.
- **`temperature`+`top_p` both sent** — RouteLLM defaults both to `1.0`; newer Anthropic models reject the pair. `routellm/Dockerfile` patches those schema defaults to `None`.
- **No `--api-key`** — in RouteLLM that flag is the *provider* key for all calls; setting it breaks Anthropic auth. Provider keys come from the env.
- **Agent tool-calling → 422** — RouteLLM's schema is too strict for OpenAI tool-calling: `tools`/`tool_choice` reject nested function schemas AND `messages` rejects tool turns (assistant `tool_calls`, role `tool`). `@agent` hits both. `routellm/patch_schema.py` (run at build) relaxes all three (full loop + streaming verified).
- **bert startup ~2 min** — see §5.3 step 1.

### 5.5 Optional: LiteLLM in front (only if you need ops features)
RouteLLM already calls the providers (uses litellm internally), so on a small internal deploy it is
**enough on its own**. Add a **LiteLLM proxy** in front only if you need plumbing RouteLLM lacks:

- **Virtual keys** — issue fake keys to AnythingLLM, hide the real provider keys
- **Budget / spend tracking** — spend cap per user/team, cost reports
- **Rate limiting** — cap requests
- **Caching (Redis)** — repeated answers not recomputed
- **Logging / observability** — audit of every call
- **Fallback / load-balance** — across multiple deployments/keys
- **Single auth** — one entry point for AnythingLLM

Chain: `AnythingLLM → LiteLLM (keys/budget/logs) → RouteLLM (strong-vs-weak) → providers`.
Not configured here — add a `litellm` service when these needs appear.

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
> bind-mount. Only what fills it changes (Microsoft OneDrive on Windows, abraunegg on Ubuntu).

> ⚠️ **Read + create vs modify/delete**: a read/write volume also allows modifying and deleting
> existing files; there is no "create-only" level (neither in the UI nor via the volume, if write
> is needed). The "do not modify/delete" constraint is enforced via the **system prompt**
> (step 7). To truly lock it down you would need a `:ro` mount of the library plus a separate
> writable subfolder (out of current scope).

---

## 7. Workspace, system prompt, and users

> ▶ **Windows vs Ubuntu**: identical.

1. Create a **Workspace** "Office Assistant".
2. Leave the workspace model on the system default (Generic OpenAI → RouteLLM); routing is handled by RouteLLM, §5.
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

## 8. Reverse proxy + HTTPS (Caddy, bundled)

> ▶ **Windows vs Ubuntu**: the **Caddy** service is part of `docker-compose.yml` in **both**
> environments — no separate host install. What differs is only the cert source: a `.local`
> domain uses Caddy's **internal CA** (self-signed, trust it on each client); a **real public
> domain** lets Caddy fetch a **Let's Encrypt** cert automatically (no manual trust).

The proxy config lives in `caddy/Caddyfile`:
```
any.rewave.local {
    tls internal
    reverse_proxy anythingllm:3001
}
```
- `reverse_proxy anythingllm:3001` uses the **container name** on the compose network — not
  `localhost` (Caddy runs in its own container).
- **Windows test**: port **443 only** is published (port 80 is reserved by Windows `http.sys`);
  internal-CA TLS needs no port 80. Point the domain at the host and trust the CA once:
  ```powershell
  Add-Content "$env:SystemRoot\System32\drivers\etc\hosts" "`n127.0.0.1`tany.rewave.local"
  docker cp caddy-any:/data/caddy/pki/authorities/local/root.crt ./caddy/caddy-root.crt
  certutil -addstore -f Root "caddy\caddy-root.crt"    # admin PowerShell
  ```
- **Production (real domain)**: change the domain in `caddy/Caddyfile` and **remove
  `tls internal`** so Caddy auto-provisions a Let's Encrypt cert; re-add the `"80:80"` port
  mapping in `docker-compose.yml` (needed for the HTTP-01 challenge and the HTTP→HTTPS redirect).
- Restrict access to the **internal network / VPN** (firewall: close 3001 from outside, expose
  only the proxy's 443).

---

## 9. Security (production)

- **Shared identity**: all employees act with the permissions of the OneDrive service account on
  the library. Give that account access **only** to the target library.
- **Secrets**: `.env` (mode `600`), and `~/.config/onedrive/` (mode `700`, holds the refresh
  token) out of the git repo (already in `.gitignore`).
- **HTTPS** mandatory; instance reachable from LAN/VPN only.
- **Backups**: back up the `./storage` folder (holds the user DB, workspaces, config).
- **Rotation**: schedule periodic rotation of the Anthropic API key; re-auth the OneDrive client
  if the service-account credentials rotate.
- **Updates**: `docker compose pull && docker compose up -d` to update the image; review the
  AnythingLLM release notes before updating in prod. Update the onedrive client via `apt`.

---

## 10. End-to-end verification (production)

1. `systemctl status onedrive-sharepoint` → active; `ls /mnt/sharepoint` → real files.
2. `docker exec -it anythingllm ls /app/server/storage/anythingllm-fs/sharepoint` → same files.
3. Basic chat → OK response (via RouteLLM; check `docker compose logs routellm` for the routed model).
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
- [ ] Install the abraunegg `onedrive` client (§1.3)
- [ ] Create `/mnt/sharepoint` and `chown` it to the service user (§2.1)
- [ ] Authenticate the onedrive client (headless) with the **M365 service account** (§2.2)
- [ ] Find the SharePoint library `drive_id` and set it in `~/.config/onedrive/config` (§2.3–2.4)
- [ ] Run the first full sync and verify files in `/mnt/sharepoint` (§2.5)
- [ ] Verify write propagation to SharePoint (§2.6)
- [ ] Install and enable the `onedrive-sharepoint` systemd service (keep `UMask=0022`) (§2.6)
- [ ] Lock down `~/.config/onedrive` (`chmod 700`) and `.env` (`chmod 600`) (§2.7, §3.2)
- [ ] Create the **production** `.env` with `SHAREPOINT_MOUNT_PATH=/mnt/sharepoint` (NOT the Windows path) (§3.2)
- [ ] `docker compose up -d` and verify the bind-mount inside the container (§3.3–3.4)
- [ ] Enable multi-user + create admin (§4)
- [ ] Configure RouteLLM (`.env`), calibrate the threshold, verify routing + `@agent` tool-calling (§5)
- [ ] **Enable the Agent Skills by hand** (File system access = required; the others optional; SQL = Off) (§6)
- [ ] Verify the `sharepoint` bind-mount is visible in the container (on Docker the File System skill has no folder config in the UI: read/write depends on the volume) (§3.4, §6.1)
- [ ] Create the workspace + system prompt with the no-modify/no-delete guard-rail (§7)
- [ ] Create the employee accounts (Default role) (§7)
- [ ] Set the domain in `caddy/Caddyfile` + HTTPS (drop `tls internal` and re-add port 80 for a real domain), close 3001 from outside (§8)
- [ ] Set up backups of `./storage` and secret rotation (§9)
- [ ] Run the full end-to-end verification (§10)

### (Optional) Make skill activation reproducible
Skills are enabled by hand in the UI (in the DB, not in a file). To avoid redoing them from
memory on every reinstall:
1. Enable them once in the UI.
2. Read the state from the DB: `system_settings` → key `default_agent_skills` in
   `./storage/anythingllm.db` (array of skill ids).
3. Save that value as a reference (or seed script) for future redeploys.

### Tool auto-approval (enabled)
`AGENT_AUTO_APPROVED_SKILLS` in `.env` controls which agent tools run **without** the per-call
"yes" confirmation. Comma-separated skill names, or `<all>`. **This deploy uses `<all>`** — every
tool (file write/edit/move, charts, web, …) runs silently.
> ⚠️ With `<all>` there is **no human confirmation on file writes** to the SharePoint-synced
> folder; the "do not modify/delete existing files" rule then relies **only** on the workspace
> system prompt (§7). For tighter control, list only specific skills instead, e.g.
> `AGENT_AUTO_APPROVED_SKILLS=filesystem-write-text-file`.

### Saving files into the SharePoint folder (constraints)
- Use the **File System** tool `filesystem-write-text-file`; the model must write to a path under
  **`sharepoint/`** (e.g. `sharepoint/riepilogo.md`) — only that subfolder is bind-mounted/synced.
  A bare `report.md` lands in `anythingllm-fs/` (container storage), **not** SharePoint.
- **Text only** (md/csv/json/txt). `create-excel-file`/charts only produce **downloads**, they do
  not write to the folder, and there is no bridge to move them in. For tabular data → **CSV**.
- Steer this in the workspace **system prompt** (tell it to save under `sharepoint/` with the File
  System tool, in text/CSV; not to use download tools for folder saves).
