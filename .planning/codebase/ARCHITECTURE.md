<!-- refreshed: 2026-06-03 -->
# Architecture

**Analysis Date:** 2026-06-03

## System Overview

Odysseus is a self-hosted, single-process AI workspace built on **FastAPI** (async, ASGI). A single `app.py` orchestrator wires together middleware, a SQLAlchemy/SQLite persistence layer, ~45 route modules, an in-process service layer, and a streaming **agent loop** that drives any local or remote LLM with tool calls.

```text
┌──────────────────────────────────────────────────────────────────────┐
│                  Browser SPA (static/, vanilla JS)                     │
│   `static/index.html` · `static/app.js` · `static/js/*.js` (65 modules)│
└───────────────────────────────┬────────────────────────────────────────┘
                                 │ HTTP / SSE (fetch + EventStream)
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     FastAPI Orchestrator  `app.py`                     │
│  Middleware stack: CORS → SecurityHeaders → RequestTimeout → Auth      │
│  Lifespan: startup tasks (MCP connect, tool-index warmup, schedulers)  │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │ app.include_router(setup_*_routes(...))
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Route Layer  `routes/*.py`                         │
│  chat · email · calendar · gallery · documents · model · tasks · ...   │
│  Each module exposes `setup_<name>_routes(deps) -> APIRouter`          │
└───────┬──────────────────────────────┬────────────────────┬───────────┘
        │                              │                    │
        ▼                              ▼                    ▼
┌─────────────────┐   ┌──────────────────────────┐   ┌──────────────────┐
│  Agent / LLM    │   │  Handlers & Managers      │   │  Service Layer   │
│  `src/agent_*`  │   │  `src/*_handler.py`       │   │  `services/*`    │
│  `src/llm_core` │   │  `src/*_manager.py`       │   │  search/docs/    │
│  `src/tool_*`   │   │  `core/session_manager`   │   │  research/memory/│
└───────┬─────────┘   └────────────┬─────────────┘   │  shell/tts/stt/  │
        │                          │                  │  hwfit/youtube   │
        │                          │                  └────────┬─────────┘
        ▼                          ▼                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│   Persistence + External I/O                                           │
│   SQLite via SQLAlchemy `core/database.py` (data/app.db)               │
│   ChromaDB vector store · JSON files in `data/` · LLM HTTP endpoints   │
│   MCP servers `mcp_servers/*` · IMAP/SMTP · CalDAV · web search        │
└──────────────────────────────────────────────────────────────────────┘
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

**Overall:** Modular monolith — a single FastAPI process with layered separation (routes → handlers/services → persistence) and dependency injection via constructor-style factory functions.

**Key Characteristics:**
- **Factory-based route registration.** Each route module exports `setup_<domain>_routes(deps...) -> APIRouter`; `app.py` calls each and `app.include_router(...)`. No global app decorators in route modules.
- **Constructor dependency injection.** `src/app_initializer.initialize_managers()` builds every manager/handler once and returns a dict; `app.py` unpacks it and passes the instances into route factories.
- **Streaming-first.** Chat and agent responses are Server-Sent-Event streams (`StreamingResponse` + async generators), not request/response JSON.
- **Local-first / degrade-gracefully.** Optional subsystems (ChromaDB RAG, MCP servers, vector memory) are initialized lazily and return `None`/503 cleanly when unavailable instead of failing startup.
- **Facade + split-module refactor.** Large subsystems are split into focused submodules with a thin facade for back-compat (`agent_tools.py` re-exports `tool_parsing`/`tool_schemas`/`tool_execution`/`tool_implementations`).

## Layers

**Presentation (SPA):**
- Purpose: Browser UI — chat, documents, email, calendar, gallery, cookbook, research.
- Location: `static/` (`index.html`, `app.js`, `js/*.js`)
- Contains: Vanilla JS modules (no build step / framework), CSS, fonts, service worker.
- Depends on: HTTP/SSE API surface.
- Used by: End user.

**Route layer:**
- Purpose: HTTP endpoints, request validation, auth scoping, response shaping.
- Location: `routes/*.py`
- Contains: `setup_*_routes()` factories returning `APIRouter`; `*_helpers.py` companions for heavy logic.
- Depends on: handlers, managers, services, `core.database`.
- Used by: `app.py` orchestrator.

**Domain / handler layer:**
- Purpose: Business logic for chat, research, uploads, documents, memory.
- Location: `src/*_handler.py`, `src/*_processor.py`, `src/*_manager.py`
- Contains: Stateful manager classes and orchestration functions.
- Depends on: LLM core, tool system, service layer, persistence.
- Used by: Route layer, agent loop.

**Agent / LLM layer:**
- Purpose: Drive an LLM through multi-round tool-calling.
- Location: `src/agent_loop.py`, `src/llm_core.py`, `src/tool_*.py`, `src/ai_interaction.py`
- Contains: Streaming loop, provider adapters, prompt assembly, tool dispatch.
- Depends on: tool implementations, MCP manager, model context utils.
- Used by: `routes/chat_routes.py`, `routes/assistant_routes.py`, `src/task_scheduler.py`.

**Service layer:**
- Purpose: Self-contained capabilities ("does one thing well, exposes a clean async interface, can run in-process or as standalone HTTP service").
- Location: `services/*` (each subdir = one service with `__init__.py` + `service.py`).
- Contains: `SearchService`, `DocsService`, `ResearchService`, `MemoryService`, `ShellService`, plus `tts`, `stt`, `hwfit`, `youtube`, `faces`.
- Depends on: external APIs, ChromaDB, subprocess, embedding models.
- Used by: handlers, tool implementations, routes.

**Persistence layer:**
- Purpose: Durable state.
- Location: `core/database.py` (SQLAlchemy, SQLite at `data/app.db`), plus JSON files in `data/` (`sessions.json`, `memory.json`, `settings.json`, `auth.json`), and ChromaDB collections under `data/chroma`, `data/memory_vectors`.
- Used by: every layer via `SessionLocal()`.

## Data Flow

### Primary Request Path — agent chat stream

1. Browser POSTs to `/api/chat_stream` (`routes/chat_routes.py`, `setup_chat_routes` → `chat_stream`).
2. Auth middleware validates the session cookie / API token (`app.py` `AuthMiddleware`, `core/auth.py`).
3. Route resolves owner + session, loads history (`core/session_manager.py`, `core/database.py`).
4. Message is preprocessed — URL/YouTube extraction, image/vision handling, context injection (`src/chat_handler.py`, `src/document_processor.py`).
5. `stream_agent_loop(...)` runs the agent (`src/agent_loop.py`): builds the system prompt, retrieves top-K tools (`src/tool_index.py`), calls `stream_llm()`.
6. LLM core dispatches to the right provider (Ollama/vLLM/OpenAI/Anthropic/OpenRouter) with retry + fallback (`src/llm_core.py`).
7. Tool blocks in the response are parsed and executed; results fed back for the next round (`src/tool_execution.py`, `src/tool_implementations.py`, `src/mcp_manager.py`).
8. Tokens stream back to the browser as SSE; the final turn is persisted to `chat_messages` (`core/database.py`).

### Background automation flow

1. An event fires via `fire_event(name, owner)` (`src/event_bus.py`) or a cron tick occurs (`src/task_scheduler.py`).
2. Scheduler matches `ScheduledTask` rows for the owner (`scheduled_tasks` table).
3. Matching tasks invoke the agent loop or service calls; results recorded in `task_runs`.

### Startup flow

1. `app.py` registers MIME types, loads `.env`, builds the `FastAPI` app + middleware.
2. `get_rag_manager()` lazily probes ChromaDB; `initialize_managers()` builds all components.
3. `setup_*_routes(...)` factories register routers.
4. Lifespan `_startup_event()` launches background tasks: incognito purge, background-job monitor, MCP connection, tool-index warmup, endpoint warmup/keepalive, default-task reconciliation, nightly skill audit.

**State Management:**
- Durable: SQLite (`data/app.db`) via SQLAlchemy; JSON files in `data/`; ChromaDB vector stores.
- In-memory: module-level singletons (managers held by `app.py`, caches in `llm_core`, the global `mcp_manager`/`task_scheduler`/`webhook_manager`).

## Key Abstractions

**`setup_*_routes(deps) -> APIRouter`:**
- Purpose: One factory per HTTP domain; receives its dependencies explicitly.
- Examples: `routes/chat_routes.py`, `routes/email_routes.py`, `routes/model_routes.py`.
- Pattern: Closure over injected deps; inner `async def` endpoint functions registered on a local `APIRouter`.

**Manager / Handler classes:**
- Purpose: Encapsulate stateful domain logic.
- Examples: `SessionManager` (`core/session_manager.py`), `ChatHandler` (`src/chat_handler.py`), `MemoryManager` (`src/memory.py`), `McpManager` (`src/mcp_manager.py`).
- Pattern: Instantiated once in `initialize_managers()`; injected downstream.

**Tool (`ToolBlock`) pipeline:**
- Purpose: Represent and execute an LLM-emitted tool call.
- Examples: parse (`src/tool_parsing.py`), schema (`src/tool_schemas.py`), dispatch (`src/tool_execution.py`), `do_*` implementations (`src/tool_implementations.py`).
- Pattern: LLM writes a fenced code block → parsed into `ToolBlock` → dispatched to a native `do_*` function or an MCP server.

**Service classes:**
- Purpose: Pluggable capability with a clean async interface.
- Examples: `SearchService`, `DocsService`, `MemoryService` (`services/*/service.py`).
- Pattern: Public dataclasses + a service class re-exported from `services/__init__.py`.

**SQLAlchemy ORM models:**
- Purpose: Typed access to the 24 SQLite tables.
- Examples: `Session`, `ChatMessage`, `Document`, `ModelEndpoint`, `ScheduledTask`, `Memory`, `Note`, `CalendarEvent` (`core/database.py`).
- Pattern: `declarative_base()` + `TimestampMixin`; obtain a unit of work with `db = SessionLocal()`.

## Entry Points

**Web server:**
- Location: `app.py` (`app = FastAPI(...)`); launched via uvicorn (see `setup.py`, `start-macos.sh`, `launch-windows.ps1`, `Dockerfile`).
- Triggers: Process start.
- Responsibilities: Build app, wire components, register routes, run lifespan.

**MCP tool servers:**
- Location: `mcp_servers/*.py` (`email_server.py`, `image_gen_server.py`, `memory_server.py`, `rag_server.py`).
- Triggers: Spawned/connected by `McpManager` during startup.
- Responsibilities: Expose tools to the agent over MCP.

**Companion pairing:**
- Location: `companion/routes.py`, `companion/pairing.py`.
- Triggers: Mounted via its router.
- Responsibilities: Device-pairing surface for a companion client.

**CLI / ops scripts:**
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

**What happens:** Several route modules are enormous — `routes/email_routes.py` (~151 KB), `routes/cookbook_routes.py` (~116 KB), `routes/model_routes.py` (~78 KB), `routes/gallery_routes.py` (~77 KB) — and `src/tool_implementations.py` is ~184 KB.
**Why it's wrong:** Hard to navigate, review, and test; high merge-conflict risk; obscures the clean factory pattern.
**Do this instead:** Follow the existing split convention — push logic into a `*_helpers.py` companion (as `routes/chat_helpers.py`, `routes/email_helpers.py`, `routes/cookbook_helpers.py` already do) and keep the `setup_*_routes` factory thin.

### Decorating the global `app` inside the orchestrator

**What happens:** A few endpoints (e.g. `/api/generated-image/{filename}`) are defined with `@app.get(...)` directly in `app.py` rather than in a route module.
**Why it's wrong:** Bypasses the `setup_*_routes` factory pattern, mixing concerns into the orchestrator.
**Do this instead:** Add new endpoints in (or create) a domain route module and register it via `app.include_router(setup_<domain>_routes(...))`.

### Implicit cross-module wiring via setters

**What happens:** Singletons are connected through free functions like `set_session_manager()` / `set_task_scheduler()` called at startup.
**Why it's wrong:** Wiring order is implicit; forgetting a setter yields `None`-deref bugs at runtime, not import time.
**Do this instead:** Prefer passing dependencies through `initialize_managers()` and the route factories where practical.

## Error Handling

**Strategy:** Custom typed exceptions mapped to HTTP responses via FastAPI exception handlers; `HTTPException` for direct route errors.

**Patterns:**
- Domain exceptions in `core/exceptions.py` and `src/exceptions.py` (`SessionNotFoundError`, `InvalidFileUploadError`, `LLMServiceError`, `WebSearchError`).
- Registered handlers in `app.py` return structured JSON `{"error": CODE, "message": ...}`.
- Non-critical startup work is wrapped in broad `try/except` that logs a warning and continues (graceful degradation).
- Upstream LLM errors formatted by `_format_upstream_error()` in `src/llm_core.py`.

## Cross-Cutting Concerns

**Logging:** Stdlib `logging` with `logger = logging.getLogger(__name__)` per module; logs written under `logs/`.
**Validation:** Pydantic request models in `src/request_models.py`; per-route `ValidationError` handling. Path/filename allow-listing via regex for security-sensitive endpoints.
**Authentication:** Cookie session + API token via `AuthMiddleware` in `app.py`, `core/auth.py`, `src/auth_helpers.py`; per-row owner scoping (`owner_filter`) enforced in route handlers. Security headers added by `core/middleware.py`. See `THREAT_MODEL.md`, `SECURITY.md`.

---

*Architecture analysis: 2026-06-03*
