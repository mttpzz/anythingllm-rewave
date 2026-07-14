# Rewave Office Assistant — AnythingLLM + Claude + SharePoint

Internal chat assistant for employees. Multi-user chat on **AnythingLLM** (self-hosted, Docker on Ubuntu), **Claude Sonnet 4.6** via the Anthropic API, **web search**, and access to a **SharePoint folder** (mounted as a local folder via `rclone` + the native File System agent).

## Architecture

```
Employees (browser) ─login─▶ AnythingLLM (Docker) ─API─▶ Anthropic (claude-sonnet-4-6)
                                ├─ web-browsing skill ─▶ web search (DuckDuckGo)
                                └─ File System skill  ─▶ /app/server/storage/anythingllm-fs/sharepoint
                                                          ▲ bind-mount
Ubuntu:  /mnt/sharepoint  ◀─ rclone mount ─ SharePoint / M365
```

Tools (web + files) run **only in agent mode**: type `@agent ...` in chat. Plain chat only answers over documents uploaded into the workspace.

## Quick test on Windows (Docker Desktop)

For the test phase the SharePoint folder is already synced by the OneDrive client:
`C:\Users\Matteo\Rewave Srl\Rewave - Information Technology`. **No rclone** — OneDrive already
does the sync. Just bind-mount that folder.

```bash
# .env: ANTHROPIC_API_KEY + SHAREPOINT_MOUNT_PATH = the Windows path (no trailing slash)
docker compose up -d
# UI at http://localhost:3001
```
- `docker-compose.yml` uses the long volume syntax so the Windows path (drive-letter `C:` and spaces) works.
- If Docker Desktop rejects backslashes, use forward slashes in `.env`: `C:/Users/Matteo/Rewave Srl/Rewave - Information Technology`.
- Make sure the folder is shared in Docker Desktop → Settings → Resources → File Sharing.
- Files the agent creates land in the local folder and OneDrive syncs them up to SharePoint.

Then follow **UI configuration** below.

> 📘 **Production**: for the full Ubuntu deploy (Docker, rclone, skills, HTTPS, checklist, and Windows/Ubuntu differences) follow **[PRODUCTION.md](PRODUCTION.md)**. The section below is the summary.

## Prerequisites (production, Ubuntu server)
- Docker + Docker Compose
- `rclone` (`curl https://rclone.org/install.sh | sudo bash`)
- A dedicated Microsoft 365 service account with access **only** to the target library/folder
- Anthropic API key (console.anthropic.com)

## Setup (production Ubuntu)

### 1. rclone: mount SharePoint
```bash
rclone config          # remote "rewaveSP", type OneDrive/SharePoint, pick site/library
                       # headless host: rclone authorize "onedrive" from a PC with a browser
sudo mkdir -p /mnt/sharepoint
# enable user_allow_other in /etc/fuse.conf (for --allow-other)
sudo cp deploy/rclone-sharepoint.service /etc/systemd/system/
# adjust User/Group, rclone.conf path, and the "Documenti/CartellaTarget" path in the unit
sudo systemctl daemon-reload
sudo systemctl enable --now rclone-sharepoint
ls /mnt/sharepoint     # must show the library files
```

### 2. AnythingLLM (Docker)
```bash
# .env: ANTHROPIC_API_KEY + SHAREPOINT_MOUNT_PATH=/mnt/sharepoint
docker compose up -d
# UI at http://SERVER:3001  (put an HTTPS reverse proxy in front, LAN/VPN access only)
```
**Admin and multi-user**: there is no first-boot wizard. Go to **Settings (gear, bottom-left) → Security → Multi-User Mode**, toggle it on: the username/password fields for the **first admin** appear. From there create the employee accounts (Admin/Manager/Default roles). Note: multi-user is **irreversible**.

### 3. UI configuration
- **LLM**: Settings → LLM Preference → Anthropic → confirm model `claude-sonnet-4-6`.
- **Web search**: Admin → Agent Skills → enable **Web Search / web-browsing** (DuckDuckGo, no key).
- **File System**: Admin → Agent Skills → enable **File System** (toggle On only). On Docker there is **no** folder picker in the UI: the accessible folder is the `sharepoint` bind-mount, and read/write depends on the volume (no `:ro` = read/write).
- **Workspace**: create "Office Assistant", assign the model, write the system prompt, assign users.

### 4. System prompt (draft)
```
You are Rewave's internal assistant. For files, use the SharePoint folder through the
File System agent; for up-to-date information, use web search. Never invent file names or
paths: verify first with a search. Do NOT modify or delete existing files: you may only read
them, summarize them, and create new files. Before creating a file, show a summary of the
content and path and ask for confirmation. Reply in Italian.
```

## Usage (for employees)
In chat, prefix file/web operations with **`@agent`** (you can write in Italian):
- `@agent search SharePoint for the 2026 contracts and summarize the key points`
- `@agent summarize the file offer-clientX.pdf`
- `@agent create meeting-notes.md with this content: ... in the Projects folder`

Chat without `@agent` only answers over documents already uploaded into the workspace.

## Security and constraints
- **Shared identity**: all employees act with the permissions of the rclone service account. Limit that account to the target SharePoint folder only.
- **Read + create**: the native AnythingLLM single read/write mount is used. The read/write File System agent can also **modify/delete** existing files — there is no "create-only" level; the constraint is enforced via the system prompt.
- **Secrets**: `.env` and `rclone.conf` (mode 600) out of the repo. HTTPS on the reverse proxy, LAN/VPN access only. Rotate the API key and rclone token periodically.
- **Sync/latency**: created files appear on SharePoint after the rclone cache flush; avoid concurrent edits on the same file.

## Project files
- `docker-compose.yml` — AnythingLLM + SharePoint folder bind-mount
- `.env` — variables (Anthropic key, model, mount path); not committed
- `deploy/rclone-sharepoint.service` — systemd unit for the rclone mount
- `PRODUCTION.md` — full production deployment guide (Ubuntu)
- `README.md` — this file

## End-to-end verification
1. `ls /mnt/sharepoint` shows the real files; a test file created there appears on SharePoint web.
2. Basic chat responds (Anthropic provider OK).
3. `@agent search the web ...` → results with sources.
4. `@agent summarize <file>` → coherent summary.
5. `@agent create test.txt ...` → the file appears in `/mnt/sharepoint` and on SharePoint.
6. Login with a second non-admin user → workspace and tools work.

## Notes / limitations
- No structured editing inside existing DOCX/XLSX: the skill creates and reads files, it does not modify the internal content of Office documents.
- DuckDuckGo (default) is free but variable in quality: consider a keyed search provider.
- Very large libraries: tune `--dir-cache-time` and watch out for recursive search cost.
