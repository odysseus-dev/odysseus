# Technology Stack

**Analysis Date:** 2026-06-03

Odysseus is a self-hostable, local-first AI assistant / agent platform: a FastAPI
backend serving a server-rendered + static-asset web UI, with a large agent
tooling layer, RAG, email/calendar/contacts, image generation, and MCP support.

## Languages

**Primary:**
- Python 3.12 - Entire backend. Container targets `python:3.12-slim` (`Dockerfile:1`). Local dev observed on Python 3.14.5, but 3.12 is the supported/shipped runtime.

**Secondary:**
- JavaScript (vanilla, browser) - Frontend assets under `static/` (no build step / framework).
- Shell - Bootstrap and launcher scripts: `start-macos.sh`, `build-macos-app.sh`, `install-service.sh`, `docker/entrypoint.sh`.
- PowerShell / Batch - Windows launchers: `launch-windows.ps1`, `update_windows.bat`.

## Runtime

**Environment:**
- Python 3.12 (Docker). Served by Uvicorn (ASGI): `uvicorn app:app --host 0.0.0.0 --port 7000` (`Dockerfile` CMD).
- Default app port: `7000` (overridable via `APP_PORT`).

**Package Manager:**
- pip - Python dependencies. Lockfile: none (unpinned `requirements.txt`; only `markitdown` is version-pinned).
- npm - Node dependencies (minimal). Lockfile: `package-lock.json` present.

## Frameworks

**Core:**
- FastAPI - HTTP API + routing. App constructed in `app.py:81` (`FastAPI(title="AI Chat Application", version="1.0.0")`). ~45 routers wired via `app.include_router(...)` from `routes/`.
- Uvicorn - ASGI server / process entrypoint.
- Pydantic v2 (`pydantic>=2.0`) + pydantic-settings (`>=2.0`) - Request models (`src/request_models.py`) and env-driven config (`src/config.py`).
- SQLAlchemy - ORM + declarative models (`core/database.py`, `core/models.py`).
- Starlette middleware - CORS, security headers, request timeout, auth (`app.py`, `core/middleware.py`).

**Testing:**
- pytest + pytest-asyncio - Config in `pyproject.toml` (`testpaths = ["tests"]`, `asyncio_mode = "auto"`). Tests live under `tests/`.
- @antithesishq/bombadil (npm devDependency `^0.3.2`) - Antithesis autonomous-testing tooling.

**Build/Dev:**
- No frontend bundler. Static assets in `static/` are served directly.
- Docker / Docker Compose - Primary deployment path (`Dockerfile`, `docker-compose.yml`, plus GPU overlays `docker/gpu.nvidia.yml`, `docker/gpu.amd.yml`).
- `setup.py` is a first-run bootstrap script (creates dirs, DB, admin user) — NOT a packaging manifest.

## Key Dependencies

**Critical (core agent / chat path):**
- `httpx` - All outbound HTTP (LLM providers, integrations, search, embeddings).
- `mcp` - Model Context Protocol client/servers (`src/mcp_manager.py`, `mcp_servers/`).
- `@anthropic-ai/sdk` (npm `^0.98.0`) - Anthropic SDK (Node side). Python Anthropic calls are done via `httpx` against the Anthropic API in `src/llm_core.py`.
- `numpy` - Vector math for embeddings / RAG.
- `chromadb-client` - Lightweight HTTP client to a standalone ChromaDB vector store (`src/chroma_client.py`).
- `fastembed` - Local ONNX embeddings fallback (`src/embeddings.py`). Also pulls `onnxruntime` (used by markitdown's magika).
- `SQLAlchemy` - Persistence for sessions, messages, documents, gallery, email accounts, tasks, etc.

**Document / content processing:**
- `pypdf` - Core PDF text extraction (MIT).
- `beautifulsoup4`, `charset-normalizer` - HTML parsing / encoding detection.
- `markdown` - Research report rendering (`src/visual_report.py`).
- `youtube-transcript-api` - YouTube transcript ingestion (`src/youtube_handler.py`, `services/youtube/`).

**Calendar / scheduling:**
- `icalendar` - .ics import/export (`routes/calendar_routes.py`).
- `python-dateutil` - Recurrence (RRULE) expansion.
- `caldav` - CalDAV sync against Radicale / Nextcloud / Apple / Fastmail (`src/caldav_sync.py`, `src/caldav_writeback.py`).
- `croniter` - Cron expression scheduling for tasks (`src/task_scheduler.py`).

**Security / auth:**
- `bcrypt` - Password hashing (`core/auth.py`).
- `pyotp` - TOTP 2FA (`core/auth.py`).
- `qrcode[pil]` - TOTP QR provisioning.
- `cryptography` - Fernet encryption at rest for secrets (`src/secret_storage.py`, `core/database.py` `EncryptedText`).

**Optional (graceful degradation when absent — `requirements-optional.txt`):**
- `faster-whisper` - Local CPU/GPU speech-to-text (`services/stt/stt_service.py`).
- `duckduckgo-search` - DuckDuckGo search provider option.
- `PyMuPDF` (AGPL-3.0) - PDF form-filling (`src/pdf_forms.py`, `src/pdf_form_doc.py`). Quarantined as optional to keep MIT core; see `ACKNOWLEDGMENTS.md`.
- `markitdown[docx,pptx,xlsx,xls]==0.1.5` - Office/EPUB → Markdown extraction (`src/markitdown_runtime.py`). Only version-pinned dependency.

**Infrastructure (bundled via Docker Compose, not pip):**
- ChromaDB (`docker.io/chromadb/chroma:latest`) - Vector store service.
- SearXNG (`docker.io/searxng/searxng:2026.5.31-7159b8aed`) - Self-hosted metasearch.
- ntfy (`docker.io/binwiederhier/ntfy`) - Push notifications.

## Configuration

**Environment:**
- Loaded via `python-dotenv` + pydantic-settings. Template: `.env.example`; runtime file: `.env` (present, not committed).
- Config object: `src/config.py` (`AppConfig` with nested `DataConfig`/`LLMConfig`/`SearchConfig`/`SecurityConfig`, env prefixes `DATA_`, `LLM_`, `SEARCH_`, `SECURITY_`).
- Database URL via `DATABASE_URL` env (`core/database.py:27`), defaulting to `sqlite:///./data/app.db`.
- Runtime user settings persisted to JSON: `data/settings.json` (`src/settings.py`), plus `data/user_prefs.json`, `data/auth.json`, `data/sessions.json`.

**Key configs required:**
- `LLM_HOST` / `LLM_HOSTS` / `OLLAMA_BASE_URL` / `LM_STUDIO_URL` - LLM endpoints (model discovery).
- `OPENAI_API_KEY` - Only if using OpenAI-hosted models.
- `SEARXNG_INSTANCE` - Web search backend.
- `CHROMADB_HOST` / `CHROMADB_PORT` - Vector store.
- `EMBEDDING_URL` / `EMBEDDING_MODEL` / `FASTEMBED_MODEL` - RAG embeddings.
- `AUTH_ENABLED`, `LOCALHOST_BYPASS`, `SECURE_COOKIES`, `ALLOWED_ORIGINS` - Auth/security.

**Build:**
- `Dockerfile` - System deps (`build-essential`, `cmake`, `git`, `tmux`, `openssh-client`, `nodejs`/`npm` for `npx` MCP servers, `gosu` for privilege drop).
- `docker-compose.yml` + `docker/gpu.nvidia.yml` / `docker/gpu.amd.yml` (selected via `COMPOSE_FILE`).
- `pyproject.toml` - pytest config only.

## Platform Requirements

**Development:**
- Python 3.12 (3.14 works locally), pip.
- `tmux` recommended (Cookbook background downloads/model serves; `setup.py` warns if missing).
- Optional GPU userspace (CUDA / ROCm) for local model serving and GPU STT.

**Production:**
- Docker / Docker Compose (loopback-bound by default for safety; reverse-proxy for LAN/HTTPS).
- Cross-platform launchers for macOS (`start-macos.sh`, `build-macos-app.sh`) and Windows (`launch-windows.ps1`); systemd unit `odysseus-ui.service` + `install-service.sh` for Linux.
- Persistent volume for `/app/data` (SQLite DB, uploads, chroma, vectors, generated images, caches) and `/app/logs`.

---

*Stack analysis: 2026-06-03*
