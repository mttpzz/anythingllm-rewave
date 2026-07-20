# Rewave Office Assistant — AnythingLLM + Claude + SharePoint

Internal chat assistant for employees. Multi-user chat on **AnythingLLM** (self-hosted, Docker on Ubuntu), **Claude Sonnet 4.6** via the Anthropic API, **web search**, and access to a **SharePoint folder** (synced to a local folder by a OneDrive client + the native File System agent).

## Architecture

```
Employees (browser) ─https─▶ Caddy proxy (any.rewave.local) ─▶ AnythingLLM (Docker) ─OpenAI API─▶ RouteLLM ─┬─ strong model (complex) ─▶ Anthropic
                                ├─ web-browsing skill ─▶ web search (DuckDuckGo)                              └─ weak model  (simple)  ─▶ Anthropic (cheap)
                                └─ File System skill  ─▶ /app/server/storage/anythingllm-fs/sharepoint
                                                          ▲ bind-mount
Ubuntu:  /mnt/sharepoint  ◀─ onedrive client (abraunegg) sync ─ SharePoint / M365
```

**LLM routing**: AnythingLLM does not call Anthropic directly. It points at **RouteLLM** (an OpenAI-compatible gateway, built from [`routellm/`](routellm/)), which scores each prompt's complexity and routes it to a **strong** model (complex tasks) or a **weak/cheap** model (simple tasks). See [LLM routing (RouteLLM)](#llm-routing-routellm) below.

Tools (web + files) run **only in agent mode**: type `@agent ...` in chat. Plain chat only answers over documents uploaded into the workspace.

## Quick test on Windows (Docker Desktop)

For the test phase the SharePoint folder is already synced by the OneDrive client:
`C:\Users\Matteo\Rewave Srl\Rewave - Information Technology`. The Microsoft OneDrive client
already does the sync — just bind-mount that folder.

```bash
# .env: ANTHROPIC_API_KEY + SHAREPOINT_MOUNT_PATH = the Windows path (no trailing slash)
docker compose up -d
# UI at http://localhost:3001  — or https://any.rewave.local via the bundled Caddy proxy (see below)
```
- `docker-compose.yml` uses the long volume syntax so the Windows path (drive-letter `C:` and spaces) works.
- If Docker Desktop rejects backslashes, use forward slashes in `.env`: `C:/Users/Matteo/Rewave Srl/Rewave - Information Technology`.
- Make sure the folder is shared in Docker Desktop → Settings → Resources → File Sharing.
- Files the agent creates land in the local folder and OneDrive syncs them up to SharePoint.

### Domain access via Caddy (optional, mirrors production)
`docker compose up -d` also starts a **Caddy** reverse proxy (`caddy/Caddyfile`) so the UI is reachable at **`https://any.rewave.local`** instead of `localhost:3001`, with TLS from Caddy's internal CA. One-time client setup:
```powershell
# 1. resolve the domain to localhost (admin PowerShell)
Add-Content "$env:SystemRoot\System32\drivers\etc\hosts" "`n127.0.0.1`tany.rewave.local"
# 2. extract Caddy's internal CA and trust it (admin PowerShell, no dialog)
docker cp caddy-any:/data/caddy/pki/authorities/local/root.crt ./caddy/caddy-root.crt
certutil -addstore -f Root "caddy\caddy-root.crt"
```
Restart the browser, open `https://any.rewave.local`. Caddy listens on **443 only** here (port 80 is reserved by Windows `http.sys` and not needed for internal-CA TLS). The extracted CA (`caddy/caddy-root.crt`) is git-ignored.

Then follow **UI configuration** below.

> 📘 **Production**: for the full Ubuntu deploy (Docker, OneDrive sync, skills, HTTPS, checklist, and Windows/Ubuntu differences) follow **[PRODUCTION.md](PRODUCTION.md)**. The section below is the summary.

## Prerequisites (production, Ubuntu server)
- Docker + Docker Compose
- `onedrive` (abraunegg client — no official Microsoft Linux client exists; see PRODUCTION.md §1.3)
- A dedicated Microsoft 365 service account with access **only** to the target library/folder
- Anthropic API key (console.anthropic.com)

## Setup (production Ubuntu)

### 1. OneDrive client: sync SharePoint
```bash
sudo mkdir -p /mnt/sharepoint && sudo chown ubuntu:ubuntu /mnt/sharepoint
onedrive                                        # headless auth: open printed URL on a PC, sign in, paste response URL
onedrive --get-O365-drive-id 'Rewave - Information Technology'   # get the library drive_id
# put sync_dir=/mnt/sharepoint + drive_id in ~/.config/onedrive/config (see deploy/onedrive-config.example)
onedrive --sync --verbose                       # first full sync
sudo cp deploy/onedrive-sharepoint.service /etc/systemd/system/
# adjust User/Group and --confdir in the unit; keep UMask=0022
sudo systemctl daemon-reload
sudo systemctl enable --now onedrive-sharepoint
ls /mnt/sharepoint     # must show the library files
```

### 2. AnythingLLM (Docker)
```bash
# .env: ANTHROPIC_API_KEY + SHAREPOINT_MOUNT_PATH=/mnt/sharepoint + RouteLLM vars
#       (ROUTELLM_STRONG_MODEL / ROUTELLM_WEAK_MODEL / ROUTELLM_API_KEY — see "LLM routing" below)
docker compose up -d --build            # --build compiles the local routellm image
# UI at http://SERVER:3001 — the bundled Caddy proxy fronts HTTPS (LAN/VPN access only); on a
# real domain drop `tls internal` in caddy/Caddyfile so Caddy auto-fetches a Let's Encrypt cert

```
**Admin and multi-user**: there is no first-boot wizard. Go to **Settings (gear, bottom-left) → Security → Multi-User Mode**, toggle it on: the username/password fields for the **first admin** appear. From there create the employee accounts (Admin/Manager/Default roles). Note: multi-user is **irreversible**.

### 3. UI configuration
- **LLM**: nothing to pick in the UI — the provider is **Generic OpenAI → RouteLLM**, set entirely via env (see [LLM routing (RouteLLM)](#llm-routing-routellm)). Just confirm chat responds.
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

## LLM routing (RouteLLM)

AnythingLLM talks to **one** OpenAI-compatible endpoint — the `routellm` service — instead of Anthropic directly. RouteLLM scores each prompt and sends it to the **strong** model (complex) or the **weak/cheap** model (simple), cutting cost without a big quality drop.

**How it fits together**
- `routellm/` — `Dockerfile` + `config.yaml` + `patch_schema.py` build the router (no official image exists).
- `docker-compose.yml` — the `routellm` service (internal only, port `6060`, not published); AnythingLLM uses `LLM_PROVIDER=generic-openai` pointing at `http://routellm:6060/v1`.
- `.env` — all tunables (`ROUTELLM_*`, keys, limits).

**Configure it (`.env`)**
| Var | Meaning |
|---|---|
| `ROUTELLM_ROUTER` | `bert` (fully local, no OpenAI key — current default) · `mf` (needs `OPENAI_API_KEY` for embeddings) · `sw_ranking` · `causal_llm` |
| `ROUTELLM_THRESHOLD` | Routing threshold. **For `bert`: HIGHER = more traffic to the WEAK/cheap model** (see calibrated values below). Router-specific — recalibrate if you change `ROUTELLM_ROUTER`. |
| `ROUTELLM_STRONG_MODEL` | litellm id for complex tasks, e.g. `anthropic/claude-sonnet-4-6` |
| `ROUTELLM_WEAK_MODEL` | litellm id for simple tasks, e.g. `anthropic/claude-haiku-4-5` (or any litellm model — a local `ollama/...` model = free) |
| `ROUTELLM_TEMPERATURE` | Fixed temperature forced on **every** request (default `0.3`), overriding whatever AnythingLLM sends; `top_p` is cleared server-side. See `routellm/patch_schema.py`. |
| `ROUTELLM_API_KEY` | Value AnythingLLM puts in its "API Key" field. RouteLLM does **not** validate inbound auth (internal-only service), so any non-empty string. **Not** passed to RouteLLM as `--api-key` (that would override the provider keys). |
| `OPENAI_API_KEY` | Only for the `mf`/`sw_ranking` router embeddings (cheap). Leave empty with `bert`. |

The model AnythingLLM requests is built as `router-${ROUTELLM_ROUTER}-${ROUTELLM_THRESHOLD}` (e.g. `router-bert-0.46514`).

**Calibrated `bert` thresholds** (this deploy defaults to `0.46514` ≈ 30% strong / 70% weak):

| Target strong-% | threshold |
|---|---|
| 20% | `0.50944` |
| 30% | `0.46514` (default) |
| 40% | `0.43431` |
| 50% | `0.4066` |

Recalibrate (e.g. after changing router/models) — runs inside the container (`pandarallel` is baked in the image):
```bash
docker exec -it routellm python -m routellm.calibrate_threshold \
  --task calibrate --routers bert --strong-model-pct 0.3 --config /app/config.yaml
# prints the threshold for ~30% strong calls → put it in ROUTELLM_THRESHOLD
```

**Verify routing works**
```bash
docker compose up -d --build        # anythingllm waits for routellm to be healthy (bert load ~2 min)
docker compose ps                   # routellm should reach STATUS "healthy"
docker compose logs -f routellm     # optional: watch the routing decisions
# then in AnythingLLM chat: a trivial prompt should hit the weak model, a hard one the strong model
```
> ⚠️ **Agent mode / tool calls**: the whole point of this deploy is `@agent` file/web tools. Those need tool-calling to survive the AnythingLLM → RouteLLM → litellm → Anthropic hops. **Test `@agent` explicitly** after switching.

**Gotchas already handled in this repo** (documented so nobody re-debugs them):
- **`LITELLM_DROP_PARAMS=True`** (compose) — AnythingLLM sends `presence_penalty`/`frequency_penalty`, which Anthropic rejects; this drops them.
- **`temperature`+`top_p` both sent** — RouteLLM's request schema defaults both to `1.0`, and newer Anthropic models reject the two together. The `routellm/Dockerfile` patches those defaults to `None` so they're forwarded only when the client sends them.
- **No `--api-key`** — in RouteLLM that flag is the *provider* key for all LLM calls; setting it breaks Anthropic auth. Provider keys come from the env (`ANTHROPIC_API_KEY`).
- **Agent tool-calling → 422** — RouteLLM's request schema is too strict for real OpenAI tool-calling: `tools`/`tool_choice` reject nested function schemas, and `messages` rejects tool-calling turns (assistant with `tool_calls`, `content: null`, role `tool`). AnythingLLM **agent mode** (`@agent`) hits both — first on the tool call, then on the follow-up with tool results. `routellm/patch_schema.py` (run at build) relaxes `messages`, `tools`, `tool_choice` to permissive types. Full loop + streaming verified.
- **bert startup ~2 min** — it downloads/loads a HuggingFace model on boot. Handled: the `routellm` service has a **healthcheck** (probes `/docs`, `start_period: 240s`) and AnythingLLM waits on `depends_on: condition: service_healthy`, so the app only starts once the router is actually serving.

### Optional: put LiteLLM in front (only if you need ops features)

RouteLLM already calls the providers (it uses litellm internally), so **for a small internal deploy it is enough on its own**. Add a **LiteLLM proxy** in front only if you need production/ops plumbing RouteLLM does not provide:

- **Virtual keys** — issue fake keys to AnythingLLM, hide the real provider keys
- **Budget / spend tracking** — spend cap per user/team, cost reports
- **Rate limiting** — cap requests
- **Caching (Redis)** — repeated answers not recomputed
- **Logging / observability** — audit of every call
- **Fallback / load-balance** — across multiple deployments/keys
- **Single auth** — one entry point for AnythingLLM

Chain would be `AnythingLLM → LiteLLM (keys/budget/logs) → RouteLLM (strong-vs-weak) → providers`. Not set up here — add a `litellm` service later if these needs appear.

## Security and constraints
- **Shared identity**: all employees act with the permissions of the OneDrive service account. Limit that account to the target SharePoint folder only.
- **Read + create**: the native AnythingLLM single read/write mount is used. The read/write File System agent can also **modify/delete** existing files — there is no "create-only" level; the constraint is enforced via the system prompt.
- **Tool auto-approval**: `AGENT_AUTO_APPROVED_SKILLS=<all>` (in `.env`) runs every agent tool **without** the per-call confirmation, including file writes to the SharePoint-synced folder. Convenient but removes the human check — the no-modify/no-delete rule then relies only on the system prompt. Narrow it to specific skills (e.g. `filesystem-write-text-file`) for tighter control. To save into the folder the model must write under `sharepoint/` in text/CSV (`create-excel-file` only produces downloads).
- **Secrets**: `.env` (mode 600) and `~/.config/onedrive/` (mode 700, holds the refresh token) out of the repo. HTTPS on the reverse proxy, LAN/VPN access only. Rotate the API key periodically; re-auth onedrive if the account credentials rotate.
- **Sync/latency**: created files appear on SharePoint after the next onedrive monitor cycle; avoid concurrent edits on the same file (can produce conflict copies).

## Project files
- `docker-compose.yml` — AnythingLLM + RouteLLM router + SharePoint bind-mount + Caddy reverse proxy
- `routellm/` — RouteLLM router build: `Dockerfile`, `config.yaml`, `patch_schema.py` (schema fixes for AnythingLLM/tool-calling)
- `caddy/Caddyfile` — Caddy config: `any.rewave.local` → `anythingllm:3001`, internal-CA TLS
- `.env` — variables (Anthropic key, RouteLLM routing, mount path); not committed
- `deploy/onedrive-sharepoint.service` — systemd unit for the onedrive sync (--monitor)
- `deploy/onedrive-config.example` — example `~/.config/onedrive/config`
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
- Very large libraries: the onedrive client syncs everything to local disk — use a `sync_list` to limit scope and watch disk usage.
