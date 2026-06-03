# Codebase Structure

**Analysis Date:** 2026-06-03

## Directory Layout

```
odysseus/
├── app.py                  # FastAPI orchestrator: app, middleware, lifespan, wiring (~44 KB)
├── setup.py                # Package/launch entry
├── requirements.txt        # Python deps (FastAPI, SQLAlchemy, chromadb, ...)
├── requirements-optional.txt
├── pyproject.toml          # Minimal project metadata
├── package.json            # JS deps (@anthropic-ai/sdk, antithesis test harness)
├── Dockerfile              # Container build
├── docker-compose*.yml      # CPU + NVIDIA + AMD GPU compose variants
│
├── core/                   # Foundational infra: DB, auth, sessions, middleware
│   ├── database.py         # SQLAlchemy models (24 tables) + engine + SessionLocal
│   ├── session_manager.py  # Chat session lifecycle
│   ├── auth.py             # AuthManager, tokens
│   ├── middleware.py       # SecurityHeaders + internal-tool header
│   ├── models.py           # ChatMessage dataclass + session-manager hook
│   ├── constants.py        # BASE_DIR, paths, env defaults
│   ├── exceptions.py       # Domain exceptions
│   ├── atomic_io.py        # Safe file writes
│   └── platform_compat.py  # OS-specific shims
│
├── routes/                 # HTTP/SSE endpoints — one setup_*_routes() per domain (~45 files)
│   ├── chat_routes.py / chat_helpers.py
│   ├── email_routes.py / email_helpers.py / email_pollers.py
│   ├── cookbook_routes.py / cookbook_helpers.py
│   ├── model_routes.py · gallery_routes.py · document_routes.py
│   ├── calendar_routes.py · contacts_routes.py · note_routes.py
│   ├── task_routes.py · assistant_routes.py · research_routes.py
│   ├── auth_routes.py · session_routes.py · memory_routes.py · skills_routes.py
│   └── ... (admin, backup, mcp, webhook, tts, stt, vault, etc.)
│
├── src/                    # Domain logic, agent, LLM, tools, processors
│   ├── agent_loop.py       # Streaming multi-round tool-calling loop (~144 KB)
│   ├── llm_core.py         # Provider-agnostic LLM calls (~70 KB)
│   ├── ai_interaction.py   # AI-to-AI tools (chat_with_model, pipeline, ...)
│   ├── agent_tools.py      # Facade re-exporting the four tool submodules
│   ├── tool_parsing.py · tool_schemas.py · tool_execution.py · tool_implementations.py
│   ├── tool_index.py       # RAG-based top-K tool selection
│   ├── *_handler.py        # chat_handler, research_handler, upload_handler, youtube_handler
│   ├── *_manager.py        # mcp_manager, preset_manager, webhook_manager, rag_manager, api_key_manager
│   ├── *_processor.py      # chat_processor, document_processor, context_compactor
│   ├── app_initializer.py  # Builds + returns all components
│   ├── config.py · settings.py · constants.py
│   ├── deep_research.py · task_scheduler.py · visual_report.py
│   └── search/             # In-src search submodule (core, providers, ranking, query, cache)
│
├── services/               # Pluggable in-process capabilities (clean async interfaces)
│   ├── __init__.py         # Re-exports Search/Docs/Research/Memory/Shell services
│   ├── search/             # core.py, providers.py, ranking.py, content.py, service.py
│   ├── docs/ · research/ · memory/ · shell/
│   ├── tts/ · stt/         # speech services
│   ├── hwfit/              # hardware fit scoring (cookbook)
│   ├── youtube/ · faces/
│
├── mcp_servers/            # Built-in MCP tool servers
│   ├── email_server.py · image_gen_server.py · memory_server.py · rag_server.py
│
├── companion/              # Companion-device pairing surface (routes.py, pairing.py)
│
├── static/                 # Browser SPA — vanilla JS, no build step
│   ├── index.html · login.html · landing.html · app.js · style.css · sw.js
│   └── js/                 # 65 JS modules in 8 subdirs (calendar/ color/ compare/
│                           #   editor/ emailLibrary/ markdown/ research/ util/)
│
├── config/                 # Runtime config (e.g. config/searxng/)
├── scripts/                # Ops/CLI scripts (diffusion_server, hwfit, completion, demo_email)
├── docker/                 # Docker support assets
├── tests/                  # 355 pytest files + JS/TS regression tests
├── docs/                   # README media + docs/index.html
├── data/                   # RUNTIME DATA (gitignored): app.db, chroma, uploads, caches
└── logs/                   # RUNTIME logs (gitignored)
```

## Directory Purposes

**`core/`:**
- Purpose: Foundational, framework-level infrastructure shared by everything.
- Contains: SQLAlchemy ORM + engine, auth, session manager, middleware, constants, exceptions.
- Key files: `core/database.py`, `core/session_manager.py`, `core/auth.py`, `core/middleware.py`.

**`routes/`:**
- Purpose: HTTP/SSE API surface; one module per feature domain.
- Contains: `setup_<domain>_routes(deps) -> APIRouter` factories and `*_helpers.py` companions.
- Key files: `routes/chat_routes.py`, `routes/email_routes.py`, `routes/model_routes.py`.

**`src/`:**
- Purpose: Domain logic — the agent loop, LLM adapters, tool system, handlers, managers, processors.
- Contains: ~80 modules; the heart of the application.
- Key files: `src/agent_loop.py`, `src/llm_core.py`, `src/tool_implementations.py`, `src/app_initializer.py`.

**`services/`:**
- Purpose: Self-contained capabilities with clean async interfaces (in-process or standalone HTTP).
- Contains: One subdir per service, each with `__init__.py` + `service.py` (and helpers).
- Key files: `services/__init__.py`, `services/search/service.py`, `services/memory/memory.py`.

**`mcp_servers/`:**
- Purpose: Built-in Model Context Protocol tool servers exposing tools to the agent.
- Key files: `mcp_servers/email_server.py`, `mcp_servers/rag_server.py`.

**`static/`:**
- Purpose: The browser SPA — no framework, no bundler; scripts loaded directly by `index.html`.
- Key files: `static/index.html`, `static/app.js`, `static/js/chat.js`, `static/js/MODULE_SUMMARY.md` (partial catalog).

**`tests/`:**
- Purpose: Test suite — 355 `test_*.py` pytest files plus `.mjs`/`.ts` JS regression tests.
- Key files: `tests/conftest.py` (path setup + optional-dep stubbing).

**`data/` and `logs/`:**
- Purpose: Runtime state and logs — generated, gitignored, NOT committed.

## Key File Locations

**Entry Points:**
- `app.py`: FastAPI app, middleware, lifespan, route wiring.
- `setup.py` / `start-macos.sh` / `launch-windows.ps1` / `Dockerfile`: launch the server.
- `mcp_servers/*.py`: MCP tool server entry points.

**Configuration:**
- `core/constants.py`, `src/constants.py`: paths + env defaults.
- `src/config.py`, `src/settings.py`: runtime config + persisted settings.
- `.env` / `.env.example`: environment variables (`.env` present — do not read; secrets).
- `data/settings.json`, `data/auth.json`: runtime persisted config.

**Core Logic:**
- `src/agent_loop.py`: agent driver.
- `src/llm_core.py`: LLM provider layer.
- `src/tool_implementations.py` + `src/tool_execution.py`: tool behavior.
- `core/database.py`: data model.

**Testing:**
- `tests/conftest.py`: shared fixtures and import shims.
- `tests/test_*.py`: unit/integration tests (co-located by feature name, not by source dir).

## Naming Conventions

**Files (Python):**
- snake_case modules: `chat_routes.py`, `tool_implementations.py`, `session_manager.py`.
- Route modules: `<domain>_routes.py`; heavy logic siblings: `<domain>_helpers.py`.
- Handlers: `<domain>_handler.py`; managers: `<noun>_manager.py`; processors: `<noun>_processor.py`.
- Services: `services/<name>/service.py` with a re-exporting `__init__.py`.

**Files (JS):**
- camelCase modules: `chatRenderer.js`, `modalManager.js`, `galleryEditor.js`; lowercase single-word for core (`chat.js`, `gallery.js`, `notes.js`).

**Functions/symbols:**
- Route factory: `setup_<domain>_routes(...)`.
- Tool implementations: `do_<action>(...)` (e.g. `do_chat_with_model`, `do_generate_image`).
- Cross-module wiring setters: `set_<thing>(...)` (e.g. `set_session_manager`).
- Module-private helpers: leading underscore `_helper(...)`.

**Database:**
- Tables: plural snake_case (`chat_messages`, `model_endpoints`, `scheduled_tasks`).
- ORM classes: PascalCase singular (`ChatMessage`, `ModelEndpoint`, `ScheduledTask`).

## Where to Add New Code

**New HTTP feature/domain:**
- Route factory: `routes/<domain>_routes.py` exporting `setup_<domain>_routes(deps) -> APIRouter`.
- Heavy logic: `routes/<domain>_helpers.py` (keep the factory thin).
- Register it in `app.py`: `app.include_router(setup_<domain>_routes(...))`.
- If it needs a new manager, build it in `src/app_initializer.initialize_managers()` and inject it.

**New domain logic / manager:**
- Implementation: `src/<feature>_handler.py` or `src/<feature>_manager.py`.
- Wire into `src/app_initializer.py` if it must be a long-lived singleton.

**New capability/service:**
- Create `services/<name>/` with `service.py` + `__init__.py`; re-export from `services/__init__.py`.

**New agent tool:**
- Implementation: add a `do_<action>(...)` in `src/tool_implementations.py`.
- Schema: add to `src/tool_schemas.py`; dispatch wiring in `src/tool_execution.py`.
- Index it for retrieval in `src/tool_index.py` if it should be RAG-selectable.

**New persisted entity:**
- Add a SQLAlchemy model (PascalCase, plural `__tablename__`) in `core/database.py`.

**New UI:**
- Add a module under `static/js/` (or a subdir) and load it from `static/index.html`; update `static/js/MODULE_SUMMARY.md`.

**Tests:**
- Add `tests/test_<feature>.py` (pytest, name-by-feature). JS regressions as `.mjs` in `tests/`.

## Special Directories

**`data/`:**
- Purpose: SQLite DB (`app.db`), ChromaDB collections, uploads, embedding/model caches, generated images.
- Generated: Yes. Committed: No (gitignored).

**`logs/`:**
- Purpose: Application logs. Generated: Yes. Committed: No.

**`static/js/editor/build/`:**
- Purpose: Prebuilt editor assets bundled into the SPA. Committed: Yes (vendored).

**`licenses/`:**
- Purpose: Third-party license texts (the project vendors/adapts several upstream projects).

---

*Structure analysis: 2026-06-03*
