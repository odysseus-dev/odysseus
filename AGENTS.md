# AGENTS.md — Odysseus

Self-hosted AI workspace (Python 3.11+ / FastAPI), AGPL-3.0-or-later. Full agent guide lives in `CLAUDE.md` and `src/agent_discipline.md`; this file is the distilled, verified quick-reference for AI coding agents.

## Project Structure

- `app.py` — Main FastAPI entry point (~52 KB), wiring layer
- `launcher.py` — Process/service entry point
- `core/` — Auth, database, sessions, models, middleware, log_safety.py (secret/PII redaction), atomic_io.py (crash-safe writes), platform_compat.py (OS glue)
- `src/` — LLM core, agent loop, agent tools, chat processor, search modules
- `routes/` — HTTP route handlers (chat, session, document, memory, model, etc.)
- `services/` — Business logic services (docs, memory, search, hwfit/Cookbook, etc.)
- `companion/` — Side process for pairing (routes, pairing)
- `mcp_servers/` — One file per MCP tool server (email, image_gen, memory, rag)
- `integrations/` — External adapters (Claude, Codex)
- `config/` — Non-secret config (e.g., searxng)
- `docker/` — Build helpers, GPU compose fragments
- `docs/` — Setup guide, landing page, screenshots
- `static/` — Frontend (index.html, app.js, style.css, js/ modules)
- `data/` — User data (gitignored): app.db, memory.json, settings.json, uploads/, chroma/, logs/
- `tests/` — Pytest suite with taxonomy markers (area_*, sub_*)

## Development Setup

### Docker (recommended)
```bash
cp .env.example .env
docker compose up -d --build
# UI: http://localhost:7000
# First admin password: docker compose logs odysseus
```

### Native Linux/macOS
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

### Native Windows
```powershell
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
# Or use the one-command launcher:
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

### Apple Silicon (GPU)
```bash
./start-macos.sh  # Serves on port 7860 (AirPlay holds 7000)
```

### Requirements
- Python 3.11+ (Docker uses 3.14-slim)
- `tmux` for Cookbook background downloads/serves (Linux/macOS/Windows Git Bash)
- Git for Windows (provides `bash.exe`) for full Cookbook/agent-shell parity on Windows

## Tests (verified)

- Config lives in `pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths=["tests"]`, `asyncio_mode="auto"`)
- Full run: `pytest -q` (needs `./data` dir present: `mkdir -p data`)
- Focused runs via `tests/run_focus.py`:
  ```bash
  ./venv/Scripts/python.exe tests/run_focus.py --area security
  ./venv/Scripts/python.exe tests/run_focus.py --area services --sub-area cookbook
  ./venv/Scripts/python.exe tests/run_focus.py --fast            # excludes slow tests
  ./venv/Scripts/python.exe tests/run_focus.py --durations 25    # show slowest tests
  ```
- CI's pytest job is `continue-on-error: true` (known flaky/isolation/embedding-model issues). Don't treat a green local run as proof and don't assume CI must pass.
- JS syntax check: `node --check` on `static/app.js` and `static/js/**/*.js` only — skip vendored `static/lib`.

## Key Conventions (observed, not assumed)

- **Atomic writes:** Use `core/atomic_io.py` for ALL state files (notes, settings, sessions). Never write files directly.
- **Logging safety:** Route ALL logging through `core/log_safety.py` (redacts secrets/PII). Never use raw `print` or `logger.info(f"...{secret}...")`.
- **Platform differences:** Put Windows/macOS/Linux differences in `core/platform_compat.py`, not scattered conditionals.
- **Paths:** Use named constants from `src/constants.py` (e.g., `AUTH_FILE`, `SETTINGS_FILE`, `DATA_DIR`). Never hardcode `Path(__file__)...`, `/app/...`, or relative `"data/..."` strings. If a data path lacks a constant, add one to `src/constants.py`.
- **Internal URLs:** Use `internal_api_base()` from `src.constants` (honors `ODYSSEUS_INTERNAL_BASE` / `APP_PORT`). Never hardcode `http://localhost:7000`.
- **Auth:** Custom exceptions in `core/exceptions.py`, surfaced as HTTP responses via `core/middleware.py`. Auth helpers in `core/auth.py`.
- **Non-admin tool blocking:** Tool enforcement in `src/tool_security.py:NON_ADMIN_BLOCKED_TOOLS`. Any `mcp__*` tool is blocked for non-admins.
- **Prompt injection defense:** External content (web results, emails, memories, skills) wrapped via `src/prompt_security.py:untrusted_context_message()`. Never inject untrusted content into system role.
- **Secrets:** Never commit `.env`. Do not expose raw model/service ports publicly (see `docs/setup.md`, `THREAT_MODEL.md`).
- **AGPL-3.0:** Distributed modifications must be open-sourced.

## Common Pitfalls

- **Windows BOM in `.env`:** If edited in Notepad, `.env` may save with UTF-8 BOM, turning `AUTH_ENABLED` into `﻿AUTH_ENABLED` (never matched). Fix: re-save as UTF-8 without BOM (VS Code: Save with Encoding → UTF-8).
- **`chromadb-client` conflict:** If installed alongside full `chromadb`, ChromaDB silently falls back to HTTP-only mode and fails. Fix: `pip uninstall chromadb-client -y && pip install --force-reinstall chromadb`.
- **macOS port 7000:** AirPlay Receiver holds port 7000. Native macOS runs on 7860 instead. Free port 7000 (System Settings → General → AirDrop & Handoff → turn off AirPlay Receiver) or use 7860.
- **Copy buttons over plain HTTP:** Browsers block `navigator.clipboard` on non-secure origins (HTTP LAN/Tailscale). Must serve HTTPS for clipboard to work.
- **ntfy reminders not reaching phone:** (1) Bundled ntfy binds to loopback — set `NTFY_BIND` to host/Tailscale IP and `NTFY_BASE_URL` in `.env`, recreate container. (2) Android ntfy app needs "Instant delivery" enabled for non-`ntfy.sh` servers.
- **Radicale calendar sync:** Must use full collection URL with trailing slash (e.g., `http://host:5232/<user>/<collection-id>/`), not just server root.
- **Docker GPU ≠ CUDA/ROCm userspace:** `nvidia-smi` inside container confirms passthrough, but llama.cpp also needs `cudart` and CUDA Toolkit. Reinstall serve engine via Cookbook → Dependencies for CUDA build.
- **Windows native launcher ignores `.env` bind:** The launcher binds to `127.0.0.1` and does not read `APP_BIND`/`ODYSSEUS_HOST` from `.env`. Use `-BindHost 0.0.0.0` flag on launcher instead.

## Useful Commands (verified exact invocations)

```bash
# Docker
docker compose up -d --build                          # default
docker compose -f docker-compose.gpu-nvidia.yml up -d --build
docker compose -f docker-compose.gpu-amd.yml up -d --build
docker compose logs --tail=120 odysseus
docker compose exec odysseus nvidia-smi -L            # verify NVIDIA passthrough
docker compose exec odysseus sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls -l /dev/kfd /dev/dri/renderD*'  # verify AMD

# GPU setup (NVIDIA)
scripts/check-docker-gpu.sh                           # read-only diagnostic
scripts/check-docker-gpu.sh --install-nvidia-toolkit  # install toolkit (sudo)
scripts/check-docker-gpu.sh --enable-nvidia-overlay   # enable overlay after passthrough confirmed

# GPU setup (AMD)
scripts/check-docker-amd-gpu.sh                       # read-only diagnostic
# Then add to .env: COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml + RENDER_GID=<gid>

# Native
python -m py_compile app.py core/*.py mcp_servers/*.py companion/*.py  # syntax check
python -m pytest                                    # full test suite
python -m pytest -m "area_security"                 # area filter (via pyproject.toml markers)
node --check static/app.js                          # JS syntax
node --check static/js/<changed-file>.js            # JS module syntax

# Windows native
./venv/Scripts/python.exe -m pytest                 # full test suite
./venv/Scripts/python.exe tests/run_focus.py --area security
./venv/Scripts/python.exe tests/run_focus.py --area services --sub-area cookbook
```

## Directory-Specific Notes

| Directory | Purpose | Key Files to Read |
|-----------|---------|-------------------|
| `app.py` | FastAPI entry, main wiring | Start here for request flow |
| `core/` | Business logic core | `models.py`, `auth.py`, `database.py`, `atomic_io.py`, `log_safety.py`, `platform_compat.py` |
| `src/` | Agent/LLM/runtime | `agent_loop.py`, `agent_tools.py`, `chat_processor.py`, `prompt_security.py` |
| `routes/` | HTTP endpoints | One file per domain (chat, session, document, etc.) |
| `services/` | Long-running services | `docs_service.py`, `memory_service.py`, `search_service.py`, `hwfit/` (Cookbook) |
| `mcp_servers/` | MCP tool servers | One file per tool (email, image_gen, memory, rag) |
| `integrations/` | External adapters | `claude.py`, `codex.py` |
| `static/` | Frontend (modular JS) | `index.html`, `app.js`, `style.css`, `js/` modules |
| `docker/` | Compose fragments | `gpu.nvidia.yml`, `gpu.amd.yml`, `entrypoint.sh` |
| `tests/` | Pytest with taxonomy | `run_focus.py`, `_taxonomy.py`, `conftest.py` |

## Workflow

- Default branch: `dev` (PRs target `dev`; `main` is curated stable)
- PR titles: Conventional Commits `<type>(<scope>): <subject>` (e.g., `fix(auth):`, `docs(setup):`)
- Visual changes: MUST run app locally, attach screenshot/clip, match existing CSS variables (`--red`, `--fg`, `--bg`, `--card`, `--border`), reuse existing button/input/card classes, no Unicode emoji in UI (use inline SVG), monospaced `Fira Code` for primary text, dark theme default.
- Single source of truth for paths/config: `src/constants.py` (re-exported by `core/constants.py` for backward compat)
- Best entry points: fresh-install testing, provider setup bugs, mobile/editor polish, docs, small focused refactors

## Key Files to Read First

- `README.md` — Feature overview + quick start
- `docs/setup.md` — Full install/config guide
- `THREAT_MODEL.md` — Security assumptions & trust boundary
- `CONTRIBUTING.md` — Contribution guidelines, visual style requirements
- `ROADMAP.md` — Planned features
- `app.py` — FastAPI entry (~52 KB)
- `core/` — Start with `models.py`, `auth.py`, `database.py`
- `src/agent_discipline.md` — Agent behavioral guidelines (fused from leaked tool prompts)
- `src/constants.py` — Single source of truth for paths and config constants