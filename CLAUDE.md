<!-- GSD:project-start source:PROJECT.md -->

## Project

**Odysseus — Engineering Modernization**

A behavior-preserving modernization of **Odysseus**, a self-hosted, local-first AI assistant/agent platform (FastAPI backend, vanilla-JS SPA, streaming agent loop with tool-calling, RAG, email/calendar/contacts, image generation, MCP support). This milestone restructures the codebase to modern software-engineering standards — breaking up god-files, adding type safety, reducing global state, and adding automated quality gates — and runs a systematic security audit, **without changing the product's features, HTTP/SSE APIs, or UX**. For the maintainers and contributors of Odysseus.

**Core Value:** The application behaves **identically** after the work — every existing feature and API still works, proven by the existing test suite — while the code underneath is materially easier to change, safer, and enforceably clean.

### Constraints

- **Behavior**: No change to features, HTTP/SSE API contracts, or UX — every refactor must be verifiable against existing behavior via the test suite.
- **Verification**: The existing pytest suite is the primary "nothing broke" signal; areas with thin coverage get tests added before they are refactored.
- **Tech stack**: Stay on the current stack — Python 3.12, FastAPI/Uvicorn, SQLAlchemy/SQLite, vanilla-JS frontend (no build step / framework), Docker Compose. No framework migrations.
- **Conventions**: Extend the codebase's existing patterns (route factories, `initialize_managers` DI, helper-split, optional-subsystem graceful degradation) rather than introducing competing ones.
- **Performance**: No regressions; opportunistic wins only — performance is not a justification for behavior or contract changes.
- **Goals are co-equal**: maintainability, security, and performance-as-guardrail are weighted together; the audit scorecard balances them rather than optimizing one at others' expense.

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

- Python 3.12 - Entire backend. Container targets `python:3.12-slim` (`Dockerfile:1`). Local dev observed on Python 3.14.5, but 3.12 is the supported/shipped runtime.
- JavaScript (vanilla, browser) - Frontend assets under `static/` (no build step / framework).
- Shell - Bootstrap and launcher scripts: `start-macos.sh`, `build-macos-app.sh`, `install-service.sh`, `docker/entrypoint.sh`.
- PowerShell / Batch - Windows launchers: `launch-windows.ps1`, `update_windows.bat`.

## Runtime

- Python 3.12 (Docker). Served by Uvicorn (ASGI): `uvicorn app:app --host 0.0.0.0 --port 7000` (`Dockerfile` CMD).
- Default app port: `7000` (overridable via `APP_PORT`).
- pip - Python dependencies. Lockfile: none (unpinned `requirements.txt`; only `markitdown` is version-pinned).
- npm - Node dependencies (minimal). Lockfile: `package-lock.json` present.

## Frameworks

- FastAPI - HTTP API + routing. App constructed in `app.py:81` (`FastAPI(title="AI Chat Application", version="1.0.0")`). ~45 routers wired via `app.include_router(...)` from `routes/`.
- Uvicorn - ASGI server / process entrypoint.
- Pydantic v2 (`pydantic>=2.0`) + pydantic-settings (`>=2.0`) - Request models (`src/request_models.py`) and env-driven config (`src/config.py`).
- SQLAlchemy - ORM + declarative models (`core/database.py`, `core/models.py`).
- Starlette middleware - CORS, security headers, request timeout, auth (`app.py`, `core/middleware.py`).
- pytest + pytest-asyncio - Config in `pyproject.toml` (`testpaths = ["tests"]`, `asyncio_mode = "auto"`). Tests live under `tests/`.
- @antithesishq/bombadil (npm devDependency `^0.3.2`) - Antithesis autonomous-testing tooling.
- No frontend bundler. Static assets in `static/` are served directly.
- Docker / Docker Compose - Primary deployment path (`Dockerfile`, `docker-compose.yml`, plus GPU overlays `docker/gpu.nvidia.yml`, `docker/gpu.amd.yml`).
- `setup.py` is a first-run bootstrap script (creates dirs, DB, admin user) — NOT a packaging manifest.

## Key Dependencies

- `httpx` - All outbound HTTP (LLM providers, integrations, search, embeddings).
- `mcp` - Model Context Protocol client/servers (`src/mcp_manager.py`, `mcp_servers/`).
- `@anthropic-ai/sdk` (npm `^0.98.0`) - Anthropic SDK (Node side). Python Anthropic calls are done via `httpx` against the Anthropic API in `src/llm_core.py`.
- `numpy` - Vector math for embeddings / RAG.
- `chromadb-client` - Lightweight HTTP client to a standalone ChromaDB vector store (`src/chroma_client.py`).
- `fastembed` - Local ONNX embeddings fallback (`src/embeddings.py`). Also pulls `onnxruntime` (used by markitdown's magika).
- `SQLAlchemy` - Persistence for sessions, messages, documents, gallery, email accounts, tasks, etc.
- `pypdf` - Core PDF text extraction (MIT).
- `beautifulsoup4`, `charset-normalizer` - HTML parsing / encoding detection.
- `markdown` - Research report rendering (`src/visual_report.py`).
- `youtube-transcript-api` - YouTube transcript ingestion (`src/youtube_handler.py`, `services/youtube/`).
- `icalendar` - .ics import/export (`routes/calendar_routes.py`).
- `python-dateutil` - Recurrence (RRULE) expansion.
- `caldav` - CalDAV sync against Radicale / Nextcloud / Apple / Fastmail (`src/caldav_sync.py`, `src/caldav_writeback.py`).
- `croniter` - Cron expression scheduling for tasks (`src/task_scheduler.py`).
- `bcrypt` - Password hashing (`core/auth.py`).
- `pyotp` - TOTP 2FA (`core/auth.py`).
- `qrcode[pil]` - TOTP QR provisioning.
- `cryptography` - Fernet encryption at rest for secrets (`src/secret_storage.py`, `core/database.py` `EncryptedText`).
- `faster-whisper` - Local CPU/GPU speech-to-text (`services/stt/stt_service.py`).
- `duckduckgo-search` - DuckDuckGo search provider option.
- `PyMuPDF` (AGPL-3.0) - PDF form-filling (`src/pdf_forms.py`, `src/pdf_form_doc.py`). Quarantined as optional to keep MIT core; see `ACKNOWLEDGMENTS.md`.
- `markitdown[docx,pptx,xlsx,xls]==0.1.5` - Office/EPUB → Markdown extraction (`src/markitdown_runtime.py`). Only version-pinned dependency.
- ChromaDB (`docker.io/chromadb/chroma:latest`) - Vector store service.
- SearXNG (`docker.io/searxng/searxng:2026.5.31-7159b8aed`) - Self-hosted metasearch.
- ntfy (`docker.io/binwiederhier/ntfy`) - Push notifications.

## Configuration

- Loaded via `python-dotenv` + pydantic-settings. Template: `.env.example`; runtime file: `.env` (present, not committed).
- Config object: `src/config.py` (`AppConfig` with nested `DataConfig`/`LLMConfig`/`SearchConfig`/`SecurityConfig`, env prefixes `DATA_`, `LLM_`, `SEARCH_`, `SECURITY_`).
- Database URL via `DATABASE_URL` env (`core/database.py:27`), defaulting to `sqlite:///./data/app.db`.
- Runtime user settings persisted to JSON: `data/settings.json` (`src/settings.py`), plus `data/user_prefs.json`, `data/auth.json`, `data/sessions.json`.
- `LLM_HOST` / `LLM_HOSTS` / `OLLAMA_BASE_URL` / `LM_STUDIO_URL` - LLM endpoints (model discovery).
- `OPENAI_API_KEY` - Only if using OpenAI-hosted models.
- `SEARXNG_INSTANCE` - Web search backend.
- `CHROMADB_HOST` / `CHROMADB_PORT` - Vector store.
- `EMBEDDING_URL` / `EMBEDDING_MODEL` / `FASTEMBED_MODEL` - RAG embeddings.
- `AUTH_ENABLED`, `LOCALHOST_BYPASS`, `SECURE_COOKIES`, `ALLOWED_ORIGINS` - Auth/security.
- `Dockerfile` - System deps (`build-essential`, `cmake`, `git`, `tmux`, `openssh-client`, `nodejs`/`npm` for `npx` MCP servers, `gosu` for privilege drop).
- `docker-compose.yml` + `docker/gpu.nvidia.yml` / `docker/gpu.amd.yml` (selected via `COMPOSE_FILE`).
- `pyproject.toml` - pytest config only.

## Platform Requirements

- Python 3.12 (3.14 works locally), pip.
- `tmux` recommended (Cookbook background downloads/model serves; `setup.py` warns if missing).
- Optional GPU userspace (CUDA / ROCm) for local model serving and GPU STT.
- Docker / Docker Compose (loopback-bound by default for safety; reverse-proxy for LAN/HTTPS).
- Cross-platform launchers for macOS (`start-macos.sh`, `build-macos-app.sh`) and Windows (`launch-windows.ps1`); systemd unit `odysseus-ui.service` + `install-service.sh` for Linux.
- Persistent volume for `/app/data` (SQLite DB, uploads, chroma, vectors, generated images, caches) and `/app/logs`.

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Naming Patterns

- `snake_case.py` everywhere — no uppercase letters in module names. Examples: `src/agent_loop.py`, `routes/chat_routes.py`, `core/atomic_io.py`.
- Layer is encoded in the directory, not the filename: `routes/*_routes.py` for HTTP routers, `routes/*_helpers.py` for route-support logic, `services/<name>/*.py` for self-contained subsystems, `src/*.py` for core domain logic, `core/*.py` for cross-cutting infra (auth, DB, middleware).
- `camelCase.js` or lowercase module names under `static/js/` (e.g. `static/js/markdown.js`, `static/js/calendar/utils.js`). ES modules with `import`/`export`.
- `snake_case` for all Python functions and variables.
- Leading underscore (`_helper`) marks module-private / internal helpers. This is heavily used — e.g. `_parse_anthropic_response` (`src/llm_core.py`), `_detect_admin_intent` (`src/agent_loop.py`), `_request_values` (`routes/search_routes.py`). Tests import these private functions directly to unit-test them.
- JS internal helpers also use a leading underscore (`_addDays`, `_shiftDT` in `static/js/calendar/utils.js`).
- `PascalCase`. Custom exceptions end in `Error` (`SessionNotFoundError`, `LLMServiceError` in `core/exceptions.py`). SQLAlchemy models are PascalCase nouns (`Session`, `ChatMessage`, `EmailAccount` in `core/database.py`). Mixins end in `Mixin` (`TimestampMixin`).
- `UPPER_SNAKE_CASE` at module top (`PROVIDER_INFO`, `DATA_DIR`, `DIRS`).

## Code Style

- No autoformatter. ~4-space indentation, double-quoted strings predominate. Keep lines reasonably short but no hard column limit is enforced.
- Trailing commas in multi-line literals/calls are common (e.g. the `comprehensive_web_search(query, return_sources=True, time_filter=time_filter,)` call in `routes/search_routes.py`).
- Section banners using a comment rule are used in larger files and tests to group related code:
- None configured. The only mechanical check used in development is `python -m py_compile` (see CONTRIBUTING.md) and `node --check` for JS files.
- Used on public function signatures, especially route handlers and helpers: `async def _request_values(request: Request) -> Dict[str, Any]:`. Return types like `-> APIRouter`, `-> Dict[str, Any]`, `-> bool` are common.
- `from __future__ import annotations` is used selectively (~9 files, e.g. `core/atomic_io.py`), not project-wide. Don't assume deferred annotations.
- Typing is pragmatic, not strict — many large modules are partially typed. Add hints to new public functions; match the file for internals.

## Import Organization

- Project root is on `sys.path`; imports are absolute from the top-level packages: `src.`, `routes.`, `services.`, `core.`. No path aliases.
- `services/<name>/__init__.py` is a barrel that re-exports the public surface and defines `__all__` (see `services/search/__init__.py`). Import from the package, not the submodule, for public API: `from services.search import comprehensive_web_search`.

## Error Handling

- Custom domain exceptions live in `core/exceptions.py` (and `src/exceptions.py`), each subclassing `Exception`, storing context attributes, and calling `super().__init__(message)`.
- Route handlers favor **degrade-gracefully over raise**: endpoints catch broad `except Exception as e`, log the error, and return a structured payload with an `"error"` key rather than throwing a 500. Example (`routes/search_routes.py`):
- Input parsing is forgiving: handlers accept JSON, form data, or query params (`_request_values`), coerce with `str(...)`/`int(...)` inside `try`/`except`, and fall back to defaults rather than 422-ing.
- Durability matters: any JSON config/state file MUST be written via `core/atomic_io.py` (`atomic_write_json` / `atomic_write_text`) — never raw `open("w") + json.dump`. The tmp-file + fsync + `os.replace` pattern prevents truncation on crash. This applies to `auth.json`, `sessions.json`, `settings.json`, `integrations.json`, `cookbook_state.json`.

## Logging

- f-string interpolation is the house style for log messages (~400+ occurrences): `logger.error(f"Standalone web search failed: {e}")`. The lazy `%`-style is NOT the convention here — match the f-string form.
- Levels: `logger.error(...)` for caught exceptions in handlers, `logger.warning(...)` for recoverable/degraded paths, `logger.info(...)`/`logger.debug(...)` for flow.

## Comments

- Comments explain *why*, not *what* — frequently citing the bug or scenario that motivated the code. Example from `routes/search_routes.py`: the FastAPI `Form(...)` vs JSON 422 rationale. Module docstrings often name the consuming file and the failure mode being guarded against (`core/atomic_io.py`).
- Inline `# Regression:` / `# the bug this PR fixes` notes are common in both code and tests to pin down intent.
- Module-level docstring at the top of nearly every file, one line minimum, often a paragraph explaining purpose and dependencies.
- Function docstrings on public functions and non-obvious helpers; terse imperative style. No enforced docstring format (not Google/NumPy style — freeform prose).

## Function Design

- Keyword-only args after `*` are used to force clarity on optional behavior: `def atomic_write_json(path: str, data: Any, *, indent: Optional[int] = None)`.
- Defaults are simple immutables; optional values default to `None` and are normalized inside the function.
- Route handlers return plain `dict` / list payloads (FastAPI serializes to JSON). Error responses are dicts with an `"error"` key, not exceptions.
- Helpers return concrete types matching their hint; predicates return `bool` and are tested with `is True` / `is False`.

## Module Design

- HTTP routers are built by a `setup_<area>_routes(...)` factory that takes its dependencies as arguments, creates `APIRouter(prefix="/api/...", tags=[...])`, defines handlers as nested `async def`, and returns the router. See `routes/search_routes.py`, `routes/api_token_routes.py`, `routes/auth_routes.py`. Dependencies (e.g. `auth_manager`, `session_manager`, `task_scheduler`) are injected via the factory, not imported globally — this keeps routes testable and wired in `app.py`.
- `services/` subpackages expose a curated public API via `__init__.py` re-exports + `__all__`. Submodules keep internals private with leading underscores.
- Used at the `services/<name>/__init__.py` level. Not used for `src/` or `routes/` — import those modules directly.
- `static/js/` uses native ES modules with explicit `import { x } from './path.js'` and `export function` / `export const`. No bundler step; files are served as-is, so imports use real relative paths with `.js` extensions.

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## System Overview

```text

```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Orchestrator | App construction, middleware, lifespan, component wiring, static mounts | `app.py` |
| Component initializer | Instantiates all managers/handlers, returns a dependency dict | `src/app_initializer.py` |
| Route modules | HTTP/SSE endpoints; one `setup_*_routes()` factory per domain | `routes/*.py` |
| Agent loop | Streaming multi-round LLM-with-tools driver | `src/agent_loop.py` |
| LLM core | Provider-agnostic chat/stream calls, retries, caching, fallback | `src/llm_core.py` |
| Tool system | Parse → schema → dispatch → implement tool calls | `src/tool_parsing.py`, `src/tool_schemas.py`, `src/tool_execution.py`, `src/tool_implementations.py` |
| Tool facade | Back-compat re-export aggregating the four tool submodules | `src/agent_tools.py` |
| Tool index | RAG-based top-K tool selection (ChromaDB) | `src/tool_index.py` |
| Session manager | Chat session lifecycle, history persistence | `core/session_manager.py` |
| Database | SQLAlchemy ORM models + engine + `SessionLocal` factory | `core/database.py` |
| Auth | User auth, token management, session cookies | `core/auth.py`, `routes/auth_routes.py`, `src/auth_helpers.py` |
| Service layer | Pluggable in-process capabilities (search, docs, memory, shell, etc.) | `services/*` |
| MCP manager | Connects to MCP tool servers (stdio/SSE), exposes their tools | `src/mcp_manager.py` |
| Task scheduler | Cron-like background automation + housekeeping defaults | `src/task_scheduler.py` |
| Event bus | Fire-and-forget events that trigger scheduled tasks | `src/event_bus.py` |

## Pattern Overview

- **Factory-based route registration.** Each route module exports `setup_<domain>_routes(deps...) -> APIRouter`; `app.py` calls each and `app.include_router(...)`. No global app decorators in route modules.
- **Constructor dependency injection.** `src/app_initializer.initialize_managers()` builds every manager/handler once and returns a dict; `app.py` unpacks it and passes the instances into route factories.
- **Streaming-first.** Chat and agent responses are Server-Sent-Event streams (`StreamingResponse` + async generators), not request/response JSON.
- **Local-first / degrade-gracefully.** Optional subsystems (ChromaDB RAG, MCP servers, vector memory) are initialized lazily and return `None`/503 cleanly when unavailable instead of failing startup.
- **Facade + split-module refactor.** Large subsystems are split into focused submodules with a thin facade for back-compat (`agent_tools.py` re-exports `tool_parsing`/`tool_schemas`/`tool_execution`/`tool_implementations`).

## Layers

- Purpose: Browser UI — chat, documents, email, calendar, gallery, cookbook, research.
- Location: `static/` (`index.html`, `app.js`, `js/*.js`)
- Contains: Vanilla JS modules (no build step / framework), CSS, fonts, service worker.
- Depends on: HTTP/SSE API surface.
- Used by: End user.
- Purpose: HTTP endpoints, request validation, auth scoping, response shaping.
- Location: `routes/*.py`
- Contains: `setup_*_routes()` factories returning `APIRouter`; `*_helpers.py` companions for heavy logic.
- Depends on: handlers, managers, services, `core.database`.
- Used by: `app.py` orchestrator.
- Purpose: Business logic for chat, research, uploads, documents, memory.
- Location: `src/*_handler.py`, `src/*_processor.py`, `src/*_manager.py`
- Contains: Stateful manager classes and orchestration functions.
- Depends on: LLM core, tool system, service layer, persistence.
- Used by: Route layer, agent loop.
- Purpose: Drive an LLM through multi-round tool-calling.
- Location: `src/agent_loop.py`, `src/llm_core.py`, `src/tool_*.py`, `src/ai_interaction.py`
- Contains: Streaming loop, provider adapters, prompt assembly, tool dispatch.
- Depends on: tool implementations, MCP manager, model context utils.
- Used by: `routes/chat_routes.py`, `routes/assistant_routes.py`, `src/task_scheduler.py`.
- Purpose: Self-contained capabilities ("does one thing well, exposes a clean async interface, can run in-process or as standalone HTTP service").
- Location: `services/*` (each subdir = one service with `__init__.py` + `service.py`).
- Contains: `SearchService`, `DocsService`, `ResearchService`, `MemoryService`, `ShellService`, plus `tts`, `stt`, `hwfit`, `youtube`, `faces`.
- Depends on: external APIs, ChromaDB, subprocess, embedding models.
- Used by: handlers, tool implementations, routes.
- Purpose: Durable state.
- Location: `core/database.py` (SQLAlchemy, SQLite at `data/app.db`), plus JSON files in `data/` (`sessions.json`, `memory.json`, `settings.json`, `auth.json`), and ChromaDB collections under `data/chroma`, `data/memory_vectors`.
- Used by: every layer via `SessionLocal()`.

## Data Flow

### Primary Request Path — agent chat stream

### Background automation flow

### Startup flow

- Durable: SQLite (`data/app.db`) via SQLAlchemy; JSON files in `data/`; ChromaDB vector stores.
- In-memory: module-level singletons (managers held by `app.py`, caches in `llm_core`, the global `mcp_manager`/`task_scheduler`/`webhook_manager`).

## Key Abstractions

- Purpose: One factory per HTTP domain; receives its dependencies explicitly.
- Examples: `routes/chat_routes.py`, `routes/email_routes.py`, `routes/model_routes.py`.
- Pattern: Closure over injected deps; inner `async def` endpoint functions registered on a local `APIRouter`.
- Purpose: Encapsulate stateful domain logic.
- Examples: `SessionManager` (`core/session_manager.py`), `ChatHandler` (`src/chat_handler.py`), `MemoryManager` (`src/memory.py`), `McpManager` (`src/mcp_manager.py`).
- Pattern: Instantiated once in `initialize_managers()`; injected downstream.
- Purpose: Represent and execute an LLM-emitted tool call.
- Examples: parse (`src/tool_parsing.py`), schema (`src/tool_schemas.py`), dispatch (`src/tool_execution.py`), `do_*` implementations (`src/tool_implementations.py`).
- Pattern: LLM writes a fenced code block → parsed into `ToolBlock` → dispatched to a native `do_*` function or an MCP server.
- Purpose: Pluggable capability with a clean async interface.
- Examples: `SearchService`, `DocsService`, `MemoryService` (`services/*/service.py`).
- Pattern: Public dataclasses + a service class re-exported from `services/__init__.py`.
- Purpose: Typed access to the 24 SQLite tables.
- Examples: `Session`, `ChatMessage`, `Document`, `ModelEndpoint`, `ScheduledTask`, `Memory`, `Note`, `CalendarEvent` (`core/database.py`).
- Pattern: `declarative_base()` + `TimestampMixin`; obtain a unit of work with `db = SessionLocal()`.

## Entry Points

- Location: `app.py` (`app = FastAPI(...)`); launched via uvicorn (see `setup.py`, `start-macos.sh`, `launch-windows.ps1`, `Dockerfile`).
- Triggers: Process start.
- Responsibilities: Build app, wire components, register routes, run lifespan.
- Location: `mcp_servers/*.py` (`email_server.py`, `image_gen_server.py`, `memory_server.py`, `rag_server.py`).
- Triggers: Spawned/connected by `McpManager` during startup.
- Responsibilities: Expose tools to the agent over MCP.
- Location: `companion/routes.py`, `companion/pairing.py`.
- Triggers: Mounted via its router.
- Responsibilities: Device-pairing surface for a companion client.
- Location: `scripts/*.py` (e.g. `diffusion_server.py`, `add_hwfit_models.py`, `claim_ownerless.py`).
- Triggers: Manual / install-time.

## Architectural Constraints

- **Threading:** Single-process async event loop (FastAPI/asyncio). CPU-bound or blocking work (DB writes, embeddings, subprocess shell) is offloaded with `asyncio.to_thread(...)`. Background tasks are `asyncio.create_task(...)` with strong refs kept on `app.state._startup_tasks` to avoid premature GC.
- **Global state:** Module-level singletons are pervasive — `mcp_manager`, `task_scheduler`, `webhook_manager` in `app.py`; cross-module wiring via setter functions (`set_session_manager`, `set_task_scheduler`, `set_memory_manager`). The DB engine + `SessionLocal` are global in `core/database.py`.
- **Lazy/local imports:** Many functions import heavy or circular dependencies *inside* the function body (e.g. `from src.settings import get_setting`) to break import cycles and defer cost. This is an established convention, not an accident.
- **SQLite concurrency:** `check_same_thread=False`; rely on short-lived `SessionLocal()` units of work and `db.close()` in `finally`. WAL/pragmas configured via engine event listeners in `core/database.py`.
- **Optional subsystems degrade:** ChromaDB RAG, vector memory, and MCP all init lazily and must tolerate being absent (`get_rag_manager()` may return `None`).

## Anti-Patterns

### God-file route modules

### Decorating the global `app` inside the orchestrator

### Implicit cross-module wiring via setters

## Error Handling

- Domain exceptions in `core/exceptions.py` and `src/exceptions.py` (`SessionNotFoundError`, `InvalidFileUploadError`, `LLMServiceError`, `WebSearchError`).
- Registered handlers in `app.py` return structured JSON `{"error": CODE, "message": ...}`.
- Non-critical startup work is wrapped in broad `try/except` that logs a warning and continues (graceful degradation).
- Upstream LLM errors formatted by `_format_upstream_error()` in `src/llm_core.py`.

## Cross-Cutting Concerns

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
