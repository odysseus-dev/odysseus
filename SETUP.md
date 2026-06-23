# Odysseus — Complete Setup & Usage Guide

This is the single document you can follow **every time** you set up Odysseus —
on this machine, on a new laptop, or after moving the project across machines. It
covers what the project is, how to run it (Docker and native), every feature and
how to use it, the command-line tools, backup/restore (portability), building
distributables, and troubleshooting.

> The shorter official guide is `docs/setup.md`. This file is the long-form,
> everything-in-one-place version maintained for personal use.

---

## 1. What Odysseus Is

Odysseus is a **self-hosted AI workspace** — a single web app that bundles chat,
agents, research, documents, email, calendar, notes, tasks, memory, an image
gallery, and local-model serving.

| Layer | Technology |
|---|---|
| Backend | **Python 3.11+**, **FastAPI**, served by **uvicorn** (entry point `app.py`) |
| Frontend | Static **HTML + JavaScript + CSS** in `static/` (no build step) |
| Primary database | **SQLite** at `data/app.db` (via SQLAlchemy) |
| Vector store | **ChromaDB** (semantic memory + RAG) |
| Web search | **SearXNG** (self-hosted metasearch) |
| Push notifications | **ntfy** |
| Local model serving | **Cookbook** (llama.cpp / vLLM / SGLang / Ollama) |

The backend registers **50+ route modules** (`routes/`) and a set of services
(`services/`). All user data lives in `data/` (gitignored).

### Architecture map
```
app.py                 # FastAPI entry point — wires routers, middleware, startup
launcher.py            # Optional desktop launcher wrapper
setup.py               # First-time setup (dirs, .env, DB, admin user) — idempotent
core/                  # auth, database, middleware, constants, atomic IO
src/                   # llm core, agent loop/tools, chat, RAG, memory, research…
routes/                # HTTP/API endpoints (chat, email, calendar, cookbook, …)
services/              # docs, memory, search, research, stt, tts, shell, hwfit
mcp_servers/           # built-in MCP servers (image gen, RAG)
scripts/               # CLI tools (odysseus-mail, odysseus-cookbook, …) + helpers
static/                # web UI (index.html, app.js, js/ modules, style.css)
docker/                # entrypoint.sh + GPU overlays (gpu.nvidia.yml, gpu.amd.yml)
integrations/          # Claude + Codex skill/plugin integrations
companion/             # companion app
data/  logs/           # runtime data + logs (gitignored)
```

---

## 2. Prerequisites

Pick **one** of two ways to run Odysseus: **Docker** (simplest, isolated) or
**native** (best for GPU model serving on your own hardware).

### For Docker
- **Docker Desktop** (Windows/macOS) or Docker Engine + Compose plugin (Linux).
- That's it — everything else runs inside containers.

### For native install
- **Python 3.11–3.13** (the Docker image uses 3.14; 3.11+ is the floor).
- **Git**.
- **Windows only:** [Git for Windows](https://git-scm.com/download/win) — provides
  `bash.exe`, needed for full Cookbook background downloads and the agent shell
  tool. The core app runs without it.
- **macOS/Linux only:** `tmux` — Cookbook uses it for background downloads/serves.
- **Optional (local models):** [Ollama](https://ollama.com/download) is the easiest
  local-model path on Windows. GPU serving of vLLM/SGLang needs Linux or WSL2.

---

## 3. Quick Start (Docker — recommended)

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
docker compose up -d --build
```

Then:
1. Wait for containers to report healthy: `docker compose ps`
2. Open **http://localhost:7000**
3. Get the first admin password from the logs:
   ```bash
   docker compose logs odysseus | findstr Temporary     # Windows
   docker compose logs odysseus | grep Temporary        # macOS/Linux
   ```
4. Log in as `admin` with that password, then change it in **Settings**.

To include optional extras in the image (PDF viewer, Office text extraction —
includes AGPL PyMuPDF), build with:
```bash
docker compose build --build-arg INSTALL_OPTIONAL=true
docker compose up -d
```

**What Compose starts:** `odysseus`, `chromadb`, `searxng`, `ntfy`. All ports bind
to `127.0.0.1` by default (host-only, not exposed to your LAN).

Common Docker commands:
```bash
docker compose ps                      # status
docker compose logs -f odysseus        # follow app logs
docker compose down                    # stop (keeps data volumes)
docker compose up -d --build           # rebuild + restart after code changes
docker compose pull                    # update bundled service images
```

---

## 4. Running Natively

### 4a. Windows (your primary machine)

**One command** — creates the venv, installs deps, runs setup, starts the server.
Safe to re-run:
```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```
Optional flags: `-Port 7000 -BindHost 127.0.0.1`.

**Or by hand:**
```powershell
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```
If `python` is an older interpreter, use `py -3.12` (or another installed 3.11+)
for the venv step. Open **http://localhost:7000**.

### 4b. Linux / macOS (Intel)
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

### 4c. Apple Silicon (M-series Mac)
Docker can't reach the Metal GPU, so for GPU-accelerated Cookbook run natively:
```bash
./start-macos.sh                 # installs brew deps, venv, setup, starts uvicorn
```
It launches at **http://127.0.0.1:7860** (port 7000 is often held by AirPlay).
`ODYSSEUS_HOST=0.0.0.0 ./start-macos.sh` exposes it over a trusted LAN/Tailscale.
`./build-macos-app.sh` builds a clickable `.app` wrapper.

> **Port summary:** Docker / native Windows / native Linux → **7000**.
> macOS via `start-macos.sh` → **7860**.

---

## 5. First Login & Admin Account

On first setup Odysseus creates an admin account and a temporary password:

- **Username:** `admin` (override with `ODYSSEUS_ADMIN_USER`).
- **Password:** printed in the terminal (native) or in `docker compose logs odysseus`
  (Docker). Pre-seed your own with `ODYSSEUS_ADMIN_PASSWORD` in `.env` before first
  boot.
- **Interactive native runs** prompt you for username + password directly.

Change the password in **Settings** after first login. The account lives in
`data/auth.json`. Review it after first boot: disable open signup unless you want
it, and keep only your own account admin.

---

## 6. Configuration (`.env`)

Defaults work out of the box. `.env` is for **deployment-level** overrides and
secrets you want present **before first boot**. Everything else is configured
in-app via **Settings** or the `/setup` page. `setup.py` copies `.env.example`
to `.env` automatically if it's missing.

### Most-used variables

| Variable | Default | Purpose |
|---|---|---|
| `APP_BIND` | `127.0.0.1` | Docker host bind address. `0.0.0.0` only for intentional LAN access. |
| `APP_PORT` | `7000` | Docker host port for the web UI. |
| `APP_DATA_DIR` | `./data` | Docker host directory bound to app data. |
| `APP_LOGS_DIR` | `./logs` | Docker host directory bound to logs. |
| `ODYSSEUS_DATA_DIR` | `./data` | **Native**: relocate ALL runtime data to another path (e.g. `D:\odysseus-data`). |
| `AUTH_ENABLED` | `true` | Enable/disable login. Keep `true` for any networked deployment. |
| `LOCALHOST_BYPASS` | `false` | Dev-only auth bypass for loopback. Keep `false` for anything shared. |
| `SECURE_COOKIES` | `false` | Set `true` when served over HTTPS via a trusted proxy. |
| `ALLOWED_ORIGINS` | `http://localhost,http://127.0.0.1` | Exact allowed cross-origin clients. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Database connection string. |
| `ODYSSEUS_ADMIN_USER` / `ODYSSEUS_ADMIN_PASSWORD` | — | Pre-seed the first admin. |
| `LLM_HOST` / `LLM_HOSTS` | `localhost` | LLM server host(s) for model discovery. |
| `OLLAMA_BASE_URL` | — | Host Ollama URL (Docker: `http://host.docker.internal:11434/v1`). |
| `OPENAI_API_KEY` | — | Optional; prefer adding providers in-app. |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | Web-search backend (Docker overrides to `http://searxng:8080`). |
| `EMBEDDING_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY` | — | OpenAI-compatible embeddings endpoint for RAG. |
| `FASTEMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local ONNX fallback embeddings (~50 MB, auto-download). |
| `CHROMADB_HOST` / `CHROMADB_PORT` | `localhost` / `8100` | Vector store (Docker overrides to `chromadb:8000`). |

Upload-size caps (`ODYSSEUS_*_MAX_BYTES`) and several search-provider API keys
(`TAVILY_API_KEY`, `SERPER_API_KEY`, `DATA_BRAVE_API_KEY`, `GOOGLE_API_KEY` +
`GOOGLE_PSE_CX`) are also set here. See `.env.example` for the full annotated list.

### Default ports (keep internal-only)

| Port | Service |
|---|---|
| `7000` | Odysseus web/API |
| `8080` | SearXNG |
| `8091` | ntfy |
| `8100` | ChromaDB (host access) |
| `11434` | Ollama |
| `8000–8020` | Common local model/provider APIs |

---

## 7. GPU Support (Docker)

CPU works with no extra setup. To pass a host GPU into the container, add one line
to `.env` (`COMPOSE_FILE` is a native Compose feature — colon-separated on
Linux/macOS, **semicolon-separated on Windows**):

```bash
# NVIDIA (needs NVIDIA Container Toolkit on the host)
COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
# Windows:
# COMPOSE_FILE=docker-compose.yml;docker/gpu.nvidia.yml

# AMD ROCm (needs ROCm drivers + render group GID)
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml
RENDER_GID=989
```

Diagnostics (read-only by default — install nothing, never edit `.env`):
```bash
scripts/check-docker-gpu.sh             # NVIDIA passthrough diagnosis
scripts/check-docker-amd-gpu.sh         # AMD passthrough diagnosis
```
Stack UIs (Portainer/Coolify) that accept only one file: use the standalone
`docker-compose.gpu-nvidia.yml` or `docker-compose.gpu-amd.yml` instead.

> GPU passthrough ≠ a CUDA-enabled inference engine. After `nvidia-smi` works
> inside the container, still install a CUDA/ROCm build of vLLM / llama-cpp-python
> via **Cookbook → Dependencies** before models serve on GPU.

---

## 8. Features & How to Use Each

Almost everything is driven from the **web UI**. Configure providers, search,
email, and keys under **Settings** (or the in-app `/setup` page). Each feature
below also has a matching `scripts/odysseus-*` CLI (see §9).

| Feature | What it does | How to use |
|---|---|---|
| **Chat + Agents** | Talk to local/API models with tools, files, shell, MCP, skills, and memory. Agents run multi-step tool loops. | Open the **Chat** tab. Pick a model, attach files, enable tools/skills. Add providers/models in **Settings → Models**. |
| **Cookbook** | Hardware-aware model recommendations, downloads, and local serving (llama.cpp/vLLM/SGLang). | **Cookbook** tab → browse recommended models for your hardware → download → **Serve**. Background work needs `tmux` (native) / Git Bash (Windows). |
| **Deep Research** | Multi-step web research: plans, reads sources, writes a cited report. | **Research** tab → enter a topic → run. Needs SearXNG (bundled) and a configured LLM. Reports render as sanitized HTML. |
| **Compare** | Blind side-by-side testing of multiple models, plus synthesis. | **Compare** tab → pick 2+ models → send one prompt → vote/synthesize. |
| **Documents** | Writing-first editor with AI edits/suggestions; Markdown, HTML, CSV, syntax highlighting. | **Documents** tab → create/upload → use inline AI actions. Optional `PyMuPDF` adds a PDF side-viewer + form filling. |
| **Email** | IMAP/SMTP inbox: triage, tags, summaries, reminders, AI reply drafts. | **Settings → Email** to add an account (IMAP/SMTP user+password). Note: Outlook/M365 need OAuth (not yet supported — see `docs/email-outlook.md`). |
| **Calendar** | Events + reminders with CalDAV two-way sync; `.ics` import/export. | **Calendar** tab. Add a CalDAV account in Settings (Radicale/Nextcloud/Apple/Fastmail). |
| **Contacts** | Address book backing email + calendar. | **Contacts** tab, or `odysseus-contacts` CLI. |
| **Notes / Tasks** | Notes, todos, and **scheduled agent tasks** (run a prompt/script on a cron). | **Notes** / **Tasks** tabs. Scheduled tasks run in-process by default (`ODYSSEUS_INPROCESS_TASKS=1`). |
| **Memory** | Semantic + structured long-term memory across chats (ChromaDB-backed). | Auto-used by chat/agents. Manage/import/export under **Settings → Memory** or `odysseus-memory`. |
| **Gallery / Image editor** | Generate, store, and transform images. | **Gallery** tab. Image generation uses the built-in `mcp_servers/image_gen_server.py` / configured providers. |
| **MCP** | Register Model Context Protocol servers (tools/resources) for agents. | **Settings → MCP** (admin). Built-in browser MCP needs `npx -y @playwright/mcp@latest --version` once. |
| **Skills** | Import/run reusable agent skills. | **Settings → Skills** or `odysseus-skills`. Claude/Codex skill integrations live in `integrations/`. |
| **Webhooks** | Trigger flows from external events. | **Settings → Webhooks** (admin). Create one token per integration. |
| **API tokens** | Programmatic API access. | **Settings → API tokens** (admin). One token per integration; delete unused ones. |
| **Backup / Vault** | Encrypted backup + restore of all data. | **Settings → Backup**, or the `odysseus-backup` CLI (see §10). |
| **Speech (STT/TTS)** | Mic → text and text → speech. | Enable in Settings. Local STT needs optional `faster-whisper`. |
| **Web search** | SearXNG plus optional Brave/Google/Tavily/Serper/DuckDuckGo. | Configure in **Settings → Search**. Provider keys go in `.env`. |
| **Presets / Sessions / Themes / 2FA** | Saved prompt presets, session management, UI themes, TOTP 2FA. | All under **Settings**. 2FA uses `pyotp` + a QR code. |

---

## 9. Command-Line Tools (`scripts/odysseus-*`)

Every major feature also has a shell CLI — useful for automation, cron jobs, and
scripted backups. They run under the project venv.

**Umbrella dispatcher** (lists and forwards to every subcommand, like `git`):
```bash
scripts/odysseus                 # list all subcommands + 1-line help
scripts/odysseus help <name>     # show that tool's --help
scripts/odysseus mail list       # == scripts/odysseus-mail list
```

**Windows note:** these have Unix shebangs and the dispatcher looks for
`venv/bin/python`. On Windows, run them through the venv Python or Git Bash:
```powershell
venv\Scripts\python scripts\odysseus-mail --help
```
or, from Git Bash / WSL with a Unix-style venv, `scripts/odysseus mail --help`.

Available subcommands include: `mail`, `calendar`, `contacts`, `cookbook`,
`backup`, `docs`, `gallery`, `memory`, `notes`, `tasks`, `research`, `sessions`,
`skills`, `theme`, `webhook`, `mcp`, `signature`, `preset`, `personal`, `logs`.

---

## 10. Moving to a New Laptop / Reinstalling (Portability)

Everything personal lives in two places: **`data/`** (database, settings, auth,
uploads, memory, chroma, generated media) and **`.env`** (config + keys). Both are
gitignored, so cloning the repo gives you a clean app — you bring your data with you.

### Option A — copy the data directory (simplest)
1. On the **old** machine, stop Odysseus.
2. Copy the whole **`data/`** folder and your **`.env`** to the new machine
   (USB, sync, scp — your choice).
3. On the **new** machine: clone the repo, drop `data/` and `.env` back into the
   project root, then run the normal native or Docker start. Your account,
   history, and settings come back as-is.

### Option B — encrypted backup/restore (recommended for transfers)
Use the built-in backup tool, which produces a single portable (optionally
encrypted) archive:
```bash
scripts/odysseus-backup create            # write a backup archive
scripts/odysseus-backup restore <file>    # restore it on the new machine
```
See **`docs/backup-restore.md`** for the full archive contents and options.

### Relocating data without moving the project
Set `ODYSSEUS_DATA_DIR` in `.env` to point at any path (e.g. an external/D: drive):
```
ODYSSEUS_DATA_DIR=D:\odysseus-data
```

> Before pushing any fork or sharing the folder, run `git status --short` and
> confirm nothing from `.env`, `data/`, `logs/`, uploads, or local databases is
> staged. They are gitignored by default — keep them that way.

---

## 11. Updating

```bash
git pull
# Docker:
docker compose up -d --build
# Native:
venv\Scripts\Activate.ps1            # (source venv/bin/activate on Unix)
pip install -r requirements.txt      # pick up new deps
python setup.py                      # apply any new dirs / migrations (idempotent)
```
Windows has a convenience `update_windows.bat`. Database migrations, when needed,
are handled by `scripts/update_database.py`.

---

## 12. Optional Dependencies

`requirements-optional.txt` unlocks extra features and is **not** installed by
default (kept out so the core image stays MIT-licensed).

| Package | Unlocks |
|---|---|
| `faster-whisper` | Local speech-to-text (the "local" STT provider). |
| `ddgs` | DuckDuckGo as a search provider. |
| `PyMuPDF` | PDF page rendering in the side viewer + form filling. *(AGPL-3.0)* |
| `markitdown` | Office/EPUB → Markdown text extraction (`.docx/.xlsx/.pptx/.epub`). |

Install: `pip install -r requirements-optional.txt` (native) or
`docker compose build --build-arg INSTALL_OPTIONAL=true` (Docker).

---

## 13. Building Distributables (optional)

| Artifact | Command |
|---|---|
| Windows portable build | `build-windows-portable.ps1` |
| macOS `.app` bundle | `./build-macos-app.sh` |
| PyInstaller spec (any OS) | `Odysseus.spec` (`pyinstaller Odysseus.spec`) |
| Linux systemd service | `install-service.sh` + `odysseus-ui.service` |

---

## 14. Testing (for contributors)

```bash
pip install -r requirements.txt      # pytest + pytest-asyncio are included
pytest                               # run the suite
pytest -m "not slow"                 # fast lane
pytest -m area_routes                # by taxonomy marker (see tests/README.md)
```
Markers and the test taxonomy are defined in `pyproject.toml` and `tests/`.

---

## 15. Troubleshooting

| Symptom | Fix |
|---|---|
| Port 7000 already in use | Set `APP_PORT=7001` in `.env` and recreate the container, or pass `-Port 7001` to the Windows launcher. |
| Can't find first password | `docker compose logs odysseus \| findstr Temporary`. Or pre-seed `ODYSSEUS_ADMIN_PASSWORD`. |
| `chromadb-client` vs embedded ChromaDB conflict | `pip uninstall chromadb-client -y` then `pip install --force-reinstall chromadb`. |
| Cookbook can't download/serve (native) | Install `tmux` (Unix) or Git for Windows (`bash.exe`). |
| GPU not detected in Docker | Run `scripts/check-docker-gpu.sh`; confirm `docker compose exec odysseus nvidia-smi -L`. |
| Health check status | `docker compose ps`; `docker compose logs --tail=120 odysseus`. |
| Vector store degraded | `docker compose logs odysseus \| grep -E 'ChromaDB\|MemoryVectorStore\|DEGRADED'`. |
| Apple Silicon "incompatible architecture" | Rebuild the venv with an arm64 Python (`/opt/homebrew/bin/python3.11`); `start-macos.sh` does this. |

---

## 16. Security Checklist

Odysseus has shell access, file uploads, model downloads, email, and API tokens —
treat it like an admin console.

- Keep `AUTH_ENABLED=true` and `LOCALHOST_BYPASS=false` for anything networked.
- Use `SECURE_COOKIES=true` when behind HTTPS via a trusted reverse proxy.
- Never expose raw service ports (ChromaDB, SearXNG, ntfy, Ollama, model APIs) —
  expose only the authenticated Odysseus entrypoint, behind a proxy.
- Keep `.env`, `data/`, `logs/`, databases, uploads, and tokens out of Git/shares.
- Review `data/auth.json` after first boot; disable open signup unless intended.
- Rotate any key ever pasted into a chat, screenshot, demo, or log.
- For LAN/Tailscale + HTTPS, generate local certs with `mkcert` and run uvicorn
  with `--ssl-certfile`/`--ssl-keyfile` (see `docs/setup.md`).

---

## 17. Polyglot Dev Stack (`dev-stack/`) — Java + Python + Kafka + DBs

Odysseus itself is a Python app, so its `Dockerfile`/`docker-compose.yml` build
only the Python service. For your **own** Java + Python development (and the
extra stacks that strengthen web/AI engineering — Kafka, PostgreSQL, Redis,
MongoDB), there is a **separate, opt-in** environment under `dev-stack/`. It does
not run Odysseus and does not touch the app's Docker files.

```
dev-stack/
├─ docker-compose.dev.yml   # profile-gated services (nothing starts by default)
├─ .env.example            # ports + local-dev credentials
├─ java/   Dockerfile      # JDK 21 + Maven + Gradle   (+ workspace/demo sample)
├─ python/ Dockerfile      # Python 3.12 + uv          (+ workspace/hello.py)
└─ README.md               # full usage guide
```

Start only what you need (run from inside `dev-stack/`):
```bash
cd dev-stack
cp .env.example .env                                          # optional
docker compose -f docker-compose.dev.yml --profile dev up -d  # Java + Python
docker compose -f docker-compose.dev.yml --profile kafka up -d
docker compose -f docker-compose.dev.yml --profile db up -d   # Postgres+Redis+Mongo
docker compose -f docker-compose.dev.yml --profile all up -d  # everything
```

Dev-stack ports are chosen to **not collide** with Odysseus, so both can run at
once: Kafka `9092`, Kafka UI `8085`, Postgres `5432`, pgAdmin `5050`, Redis
`6379`, Mongo `27017`, Mongo Express `8081`. Full details, credentials, and
"connect from Java/Python" examples are in **`dev-stack/README.md`**.

---

## Quick Reference Card

```bash
# Docker (fresh machine)
git clone <repo> && cd odysseus
cp .env.example .env
docker compose up -d --build
docker compose logs odysseus | findstr Temporary   # first password
# → http://localhost:7000

# Native Windows (fresh machine)
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
# → http://localhost:7000

# Native Unix (fresh machine)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000

# Move data to a new laptop
scripts/odysseus-backup create        # old machine
scripts/odysseus-backup restore <f>   # new machine
```
