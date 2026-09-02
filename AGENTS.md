# AGENTS.md — Odysseus

This file is written for AI coding agents that need to understand the Odysseus project quickly. It assumes no prior knowledge of the codebase. Everything below is grounded in the actual files in this repository; if a detail drifts, trust the source file over this summary.

## Project overview

Odysseus is a self-hosted AI workspace. The backend is a Python FastAPI application, and the frontend is vanilla HTML/CSS/JavaScript served as static assets. It is designed to run locally or on a private server, connecting to local models (Ollama, vLLM, llama.cpp, LM Studio, etc.) and/or remote API providers (OpenAI, etc.).

Major feature areas:

- Chat + agents with tools, memory, skills, MCP, file uploads, and shell access.
- Cookbook — hardware-aware model recommendations, downloads, and local serving.
- Deep Research — multi-step web research with source reading and report generation.
- Compare — blind side-by-side model testing.
- Documents — writing-first editor with AI edits and Markdown/HTML/CSV support.
- Email — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- Notes, tasks, calendar, and CalDAV/CardDAV sync.
- Gallery/image editor, themes, presets, web search, TTS/STT, and 2FA.

Repository branches:

- `dev` — default branch; latest changes land here. Open PRs against `dev`.
- `main` — curated, stable branch; fast-forwarded from `dev` at releases.

License: AGPL-3.0-or-later (see `LICENSE` and `ACKNOWLEDGMENTS.md`).

## Repository layout

| Path | Purpose |
|------|---------|
| `app.py` | FastAPI entry point and application orchestrator (~1280 lines). Registers middleware, auth, static files, exception handlers, and all routers. |
| `setup.py` | First-time setup script: creates data directories, initializes the SQLite database, creates the initial admin user, and copies `.env.example` to `.env`. Idempotent. |
| `core/` | Foundational runtime: database (`database.py`, SQLAlchemy models), auth (`auth.py`), session manager, middleware, exceptions, atomic I/O, platform compat. |
| `src/` | Domain logic and services. Flat package with ~100 modules covering the agent loop, tools, LLM core, model discovery, memory/RAG, research, scheduler, cookbook, MCP manager, etc. |
| `routes/` | HTTP route handlers. Mostly flat; a few features are grouped into sub-packages (`admin_wipe/`, `cleanup/`, `compare/`, `contacts/`, `gallery/`, `history/`, `memory/`, `note/`, `research/`). Each module exposes a `setup_*_routes(...)` factory. |
| `services/` | Domain service implementations consumed by `src/` and `routes/`: memory, search, research, shell, STT, TTS, YouTube, hardware fitting (`hwfit`), docs, faces. |
| `mcp_servers/` | Built-in Model Context Protocol server implementations (email, image generation, memory, RAG). |
| `companion/` | LAN companion bridge (`/api/companion/*`) for pairing a mobile client to a server. |
| `integrations/` | Integration assets for external agent CLIs (`claude/`, `codex/`) — skills and scripts. |
| `swift/` | `odysseus-mlx-image-bridge` — a Swift Package for Apple Silicon MLX image generation. |
| `scripts/` | CLI tooling. `scripts/odysseus` is a git-style dispatcher for `scripts/odysseus-*` subcommands (mail, tasks, skills, notes, etc.). Shared CLI helpers live in `scripts/_lib/`. |
| `static/` | Frontend assets. `static/index.html` is the SPA shell, `static/app.js` is the top-level JS, `static/js/` contains feature modules, and `static/style.css` is the single app stylesheet. |
| `tests/` | Pytest suite (~730 files). Flat today with a phased target structure described in `tests/TESTING_STANDARD.md`. Shared helpers in `tests/helpers/`. |
| `docker/` | Docker entrypoint and helper scripts (e.g. Real-ESRGAN wheel patching). |
| `config/searxng/` | Bundled SearXNG configuration template. |
| `docs/` | User-facing documentation and setup guide (`docs/setup.md`). |
| `specs/` | Architectural snapshot (`specs/architecture-runtime-inventory.md`), including import relationships and a large-module risk map. |
| `data/` | Runtime data directory (gitignored). Database, auth file, uploads, caches, models, etc. |
| `logs/` | Runtime logs directory. |

## Technology stack

- **Language:** Python 3.11+ (Docker image currently uses Python 3.14; CI uses 3.11; macOS installer targets 3.11+).
- **Web framework:** FastAPI on Starlette, served by Uvicorn.
- **Database:** SQLAlchemy ORM. Default database is SQLite (`data/app.db`); `DATABASE_URL` can point to another SQLAlchemy-compatible backend.
- **Vector store / embeddings:** ChromaDB (external container in Docker; optional manual host) with `fastembed` as a local ONNX embedding fallback.
- **Frontend:** Plain HTML/CSS/ES modules. No bundler, no React/Vue, no npm build step for the app itself. The root `package.json` only declares the Bombadil dev dependency for JS testing.
- **Container:** Docker + Docker Compose. Base Compose stack (`docker-compose.yml`) includes Odysseus, ChromaDB, SearXNG, and ntfy.
- **Local model serving:** Cookbook drives `llama.cpp`, `vLLM`, `SGLang`, etc., usually inside `tmux` sessions. Optional GPU overlays are in `docker-compose.gpu-nvidia.yml` and `docker-compose.gpu-amd.yml`.

Core Python dependencies (see `requirements.txt`):

- `fastapi`, `uvicorn`, `python-multipart`, `python-dotenv`, `httpx`, `httpcore`, `pydantic>=2.13.4`, `pydantic-settings`
- `SQLAlchemy`, `bcrypt`, `pyotp`, `cryptography`, `qrcode[pil]`
- `chromadb-client`, `fastembed` (core deps — RAG, semantic memory, and tool selection are core paths)
- `caldav`, `icalendar`, `python-dateutil`
- `pypdf`, `beautifulsoup4`, `markdown`, `nh3`, `youtube-transcript-api`, `charset-normalizer`, `numpy`
- `mcp`, `croniter`
- `pytest`, `pytest-asyncio`, `httpx2` (test-client only; runtime code uses `httpx`)

Optional dependencies are listed in `requirements-optional.txt`: `faster-whisper` (local STT), `ddgs` (DuckDuckGo search provider), `PyMuPDF` (PDF form filling; AGPL — install only via the `INSTALL_OPTIONAL` build arg), `markitdown[docx,pptx,xlsx,xls]` (Office/EPUB extraction). The app degrades gracefully when optional packages are missing.

## Configuration

- Copy `.env.example` to `.env` and edit values there. The app loads `.env` with UTF-8-sig encoding to tolerate BOMs from Windows editors.
- All deployment-level overrides live in `.env`: `APP_BIND`, `APP_PORT`, `DATABASE_URL`, `AUTH_ENABLED`, `LOCALHOST_BYPASS`, `SECURE_COOKIES`, LLM/search endpoints, OAuth credentials, upload limits, etc. `docker-compose.yml` shows the full set of supported environment variables with their defaults.
- `ODYSSEUS_DATA_DIR` moves the entire writable tree (`data/`). All persisted paths are defined as constants in `src/constants.py`; `core/constants.py` re-exports them for backward compatibility. Use these constants instead of building paths from `__file__` or hardcoding `data/`.
- Internal loopback calls use `src.constants.internal_api_base()`, which respects `ODYSSEUS_INTERNAL_BASE` / `APP_PORT`.

## Runtime architecture

1. `app.py` builds the FastAPI app, installs middleware (CORS, gzip, security headers, request timeout, interactive-activity gating, slow-request logging), and configures auth.
2. Components are initialized by `src.app_initializer.initialize_managers()` and attached to `app.state`.
3. Routers from `routes/` are included via their `setup_*_routes(...)` factories (`app.include_router(...)`).
4. The lifespan context manager runs startup tasks: default task reconciliation, skill owner backfill, MCP connection, optional warmups, background job monitor, scheduled task runner, nightly skill audit, cookbook serve lifecycle, and a periodic null-owner sweep.
5. Shutdown cancels upload cleanup, stops the task scheduler, closes webhooks, and disconnects MCP servers.

Auth stack:

- `AUTH_ENABLED=true` by default.
- `LOCALHOST_BYPASS=true` lets direct loopback requests skip auth; keep it `false` for any network-exposed deployment (this is the Docker default).
- Session cookie auth for browser users; Bearer `ody_*` API tokens with scopes for external integrations; an internal loopback token for in-process agent tools.
- Owner scoping is enforced throughout: users see only their own rows or legacy null-owner rows.

## Module divisions

### `core/`
Foundational, widely imported modules. `core/database.py` (~2500 lines) defines the SQLAlchemy `Base`, engine, `SessionLocal`, and most ORM models. It is the highest-risk file to refactor because over 100 files import it.

### `src/`
Domain logic. Notable groupings:

- Agent execution: `agent_loop.py`, `builtin_actions.py`, `action_intents.py`, `teacher_escalation.py`, `bg_monitor.py`.
- Tools: `tool_schemas.py`, `tool_index.py`, `tool_implementations.py`, `tool_security.py`, `tool_policy.py`, `tool_utils.py`, `tool_execution.py`, `tool_parsing.py`, plus `src/agent_tools/` (document, filesystem, subprocess, web helpers).
- LLM: `llm_core.py`, `model_discovery.py`, `model_context.py`, `endpoint_resolver.py`, `chat_handler.py`, `chat_processor.py`.
- Memory/RAG: `memory.py`, `memory_provider.py`, `memory_vector.py`, `rag_manager.py`, `rag_singleton.py`, `rag_vector.py`, `personal_docs.py`, `chroma_client.py`, `embedding_lanes.py`, `embeddings.py`.
- Research: `deep_research.py`, `research_handler.py`, `research_utils.py`, `visual_report.py`.
- Scheduling/background: `task_scheduler.py`, `task_endpoint.py`, `task_action_policy.py`, `event_bus.py`, `cookbook_serve_lifecycle.py`, `bg_jobs.py`.
- Cookbook: `cookbook_serve_lifecycle.py`, plus route helpers in `routes/cookbook_*.py`.
- Settings/config: `config.py`, `settings.py`, `settings_scrub.py`, `constants.py`, `runtime_paths.py`.
- Security: `url_security.py`, `url_safety.py`, `prompt_security.py`, `rate_limiter.py`, `secret_storage.py`, `upload_limits.py`, `auth_helpers.py`.

### `routes/`
HTTP handlers. Most modules export a `setup_*_routes(...)` function returning an `APIRouter`. Some large domains have helper modules (e.g. `email_helpers.py`, `email_pollers.py`, `cookbook_helpers.py`, `chat_helpers.py`). A few route modules are thin shims that import the real implementation from a sub-package (e.g. `routes/memory/memory_routes.py`).

### `services/`
Self-contained domain services imported by the rest of the app: `services.memory`, `services.search`, `services.research`, `services.shell`, `services.stt`, `services.tts`, `services.youtube`, `services.hwfit`, `services.docs`, `services.faces`.

### `mcp_servers/`
Standalone MCP server scripts used by the MCP manager.

### `companion/`
LAN client pairing and discovery routes.

### `scripts/`
Command-line wrappers. `scripts/odysseus` discovers and dispatches to `scripts/odysseus-<name>` siblings the way `git` finds `git-foo`. Subcommands include `mail`, `tasks`, `skills`, `notes`, `sessions`, `preset`, `theme`, `cookbook`, `research`, `personal`, `contacts`, `calendar`, `webhook`, `mcp`, `gallery`, `memory`, `backup`, `docs`, `logs`, and `signature`. The directory also contains one-off maintenance/migration scripts (e.g. `claim_ownerless.py`, `migrate_faiss_to_chroma.py`, `update_database.py`).

## Build / run / deploy

### Docker (recommended for most users)

```bash
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus
cp .env.example .env        # edit as needed
docker compose up -d --build
```

Open `http://localhost:7000` once healthy. The first admin password is printed in `docker compose logs odysseus`.

Optional overlays (pick one GPU file, optionally combine with host Docker):

```bash
# NVIDIA GPU
COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml docker compose up -d --build

# AMD ROCm
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml docker compose up -d --build

# Host Docker socket access (explicit opt-in)
COMPOSE_FILE=docker-compose.yml:docker/host-docker.yml docker compose up -d --build
```

There are also standalone GPU Compose files (`docker-compose.gpu-nvidia.yml`, `docker-compose.gpu-amd.yml`) for stack UIs that do not honor `COMPOSE_FILE`.

To include optional AGPL dependencies (PyMuPDF, etc.) in the image:

```bash
docker compose build --build-arg INSTALL_OPTIONAL=true
docker compose up -d
```

### Native Linux / macOS

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Cookbook needs `tmux` for background downloads/serves.

### macOS (Apple Silicon, GPU-accelerated)

```bash
./start-macos.sh
```

Runs on `http://127.0.0.1:7860` by default because macOS AirPlay Receiver holds port 7000. The script installs Homebrew dependencies, builds `venv/`, and launches the server.

### Windows

A portable launcher is built via `build-windows-portable.ps1`/`launcher.py`. Native Windows Python installs are not actively tested; Docker on Linux/WSL is the safer path.

### Systemd

`install-service.sh` installs `odysseus-ui.service`. Edit the service file to match your user and working directory before running it.

## Development workflow

- **Branch from `dev`**, not `main`. Open PRs against `dev`.
- Use [Conventional Commits](https://www.conventionalcommits.org): `type(scope): summary` (e.g. `fix(search): ...`, `feat(notes): ...`).
- Keep PRs small and focused: one bug fix or feature per PR. Do not mix file moves, formatting, refactors, and behavior changes.
- If you are an LLM agent, the maintainers prefer that you open an issue describing the problem before opening a bulk-generated PR.

## Code style guidelines

- **Constants and paths:** all writable paths are centralized in `src/constants.py`. Never build data paths from `Path(__file__)`, hardcoded `/app/...`, or literal `"data/..."`. Import `DATA_DIR`, `AUTH_FILE`, `UPLOAD_DIR`, etc. If a new persisted path is needed, add a constant to `src/constants.py`.
- **Internal URLs:** never hardcode `http://localhost:7000`. Use `internal_api_base()` from `src.constants`.
- **Ports, limits, model lists:** reuse existing constants or add new ones in `src/constants.py` rather than duplicating literals.
- **Frontend style:** reuse existing CSS variables (`--red`, `--fg`, `--bg`, `--card`, `--border`, ...), button/input/card classes, and the monochrome inline-SVG icon style. Do not introduce new colors, spacing units, or Unicode emoji in the UI. The default theme is dark; light mode work goes through the existing theme system. Visual changes require screenshots in PRs.
- **Docstrings and comments:** write in English, matching the existing tone.
- **Imports:** prefer top-level imports. Some cross-layer inline imports exist (notably `src/tool_implementations.py` importing from `routes/`) to avoid circular imports; do not introduce new ones without a clear reason.

## Testing

- Run the suite with the project interpreter (`./venv/bin/python -m pytest`), not a system Python that may lack pinned dependencies.
- Pytest config lives in `pyproject.toml` (pytest options only — it is not a build config); `tests/conftest.py` ensures the project root is on `sys.path`, defaults `DATABASE_URL` to an in-memory SQLite, and stubs optional heavy deps when absent. `asyncio_mode = "auto"` is set.
- Tests are classified at collection time with `area_*` and `sub_*` markers (see `tests/_taxonomy.py` and `pyproject.toml`).
- Run focused subsets with `tests/run_focus.py`:

```bash
./venv/bin/python tests/run_focus.py --area security
./venv/bin/python tests/run_focus.py --area services --sub-area cookbook
./venv/bin/python tests/run_focus.py --fast
```

- The fast lane is `not slow`. Mark tests `slow` only with duration evidence from `--durations`.
- JS files can be syntax-checked with `node --check static/js/<file>.js`.
- Before a PR, run at least:
  - `git diff --check`
  - `python -m py_compile <changed .py files>`
  - focused pytest on changed files and neighboring order-sensitive groups
  - `docker compose config` for Docker changes

Detailed testing philosophy is in `tests/TESTING_STANDARD.md`; helper usage is in `tests/README.md`.

## Security considerations

Odysseus is a self-hosted workspace with privileged local tools. Treat it as admin software (see `SECURITY.md` and `THREAT_MODEL.md`):

- Keep `AUTH_ENABLED=true` for any network access.
- Keep `LOCALHOST_BYPASS=false` outside local development.
- Set `SECURE_COOKIES=true` when serving through HTTPS by a trusted reverse proxy.
- Put Odysseus behind a trusted reverse proxy or private access layer (Cloudflare Access, Tailscale, VPN) when exposing it beyond localhost.
- Keep ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp, databases, and raw model/provider APIs internal-only.
- Protect `.env`, `data/`, `logs/`, uploads, generated media, backups, auth/session files, and API keys. Never commit these.
- Reserve admin-only access to shell, Python, file read/write, email send/read, MCP, app API, task/skill/memory management, settings, tokens, and model serving.
- Owner scoping is a core invariant: a user must never see another user's rows. Verify owner scoping when adding routes, tools, or DB queries.
- Report content rendered from LLM output is allowlist-sanitized (`nh3`); keep sanitization on any new rendering path.
- The repository runs secret scanning (`gitleaks`), workflow security linting (`actionlint`, `zizmor`), and dependency review in CI.

For vulnerability reports, see `SECURITY.md`.

## Useful commands

```bash
# Full test suite
./venv/bin/python -m pytest

# Focused test run
./venv/bin/python tests/run_focus.py --area security

# Syntax checks
python -m compileall -q app.py core routes src services scripts tests
node --check static/js/<file>.js

# Start native dev server
python -m uvicorn app:app --host 127.0.0.1 --port 7000

# Docker
docker compose up -d --build
docker compose logs --tail=120 odysseus
docker compose config

# Setup / first-run
python setup.py

# CLI examples
./scripts/odysseus mail list --folder INBOX --limit 5
./scripts/odysseus tasks list
./scripts/odysseus skills list
```

## Common pitfalls

- **Port 7000 on macOS** is used by AirPlay Receiver; use `APP_PORT=7001` or `./start-macos.sh` (defaults to 7860).
- **Apple Silicon + x86 Python** causes "incompatible architecture" crashes when loading compiled wheels. Use an arm64 Homebrew Python (`/opt/homebrew/bin/python3.11`).
- **No frontend build step** — changes to `static/` are served directly. Browsers are told to revalidate `.js`/`.css`/`.html` via `Cache-Control: no-cache` to avoid stale modules across deploys.
- **Tests use an in-memory SQLite by default**; tests that need a file-backed DB must opt in explicitly via `tests.helpers.sqlite_db.make_temp_sqlite`.
- **Optional dependencies** may be absent in CI or a fresh venv. Code must degrade gracefully and tests must not require them.
