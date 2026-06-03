# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Odysseus is a self-hosted, local-first AI workspace (FastAPI backend + vanilla-JS frontend). For install/run/deployment and `.env` config, read `README.md` — this file does not repeat that. For PR/style rules read `CONTRIBUTING.md` (note: agent-generated PRs are unwelcome here — open an issue first, and any change touching the UI must match the existing visual style: reuse CSS vars/classes, no Unicode emoji, `Fira Code` mono, dark theme default).

## Commands

```bash
# Run the app for development (Python 3.11+, venv active)
python -m uvicorn app:app --host 127.0.0.1 --port 7000

# Full test suite (pytest-asyncio in auto mode; testpaths=tests/)
python -m pytest

# A single test file / test / pattern
python -m pytest tests/test_agent_loop.py
python -m pytest tests/test_agent_loop.py::test_name
python -m pytest -k "calendar and recurrence"

# Fast syntax checks (run manually before a PR — there is no CI)
python -m py_compile app.py routes/*.py src/*.py
node --check static/js/<file-you-changed>.js     # frontend is plain JS, no bundler

# JS/TS test artifacts under tests/ run with their own runners
node tests/markdown_codefence_placeholder_regression.mjs
```

There is **no build step and no linter config** for the frontend — `static/` is served as-is. `setup.py` is a first-run bootstrapper (creates `data/` dirs, DB, admin user), not a packaging script. There is **no CI workflow** in the repo: the checks above are run manually and should be mentioned in the PR description (per `CONTRIBUTING.md`). Core Python deps live in `requirements.txt` (each non-obvious one has a rationale comment); feature-unlocking extras are in `requirements-optional.txt` and are not installed by default.

## Test environment specifics

`tests/conftest.py` puts the repo root on `sys.path` and **stubs heavy/optional deps with `MagicMock` only when they are not installed** (sqlalchemy, bcrypt, httpx, fastapi/starlette/pydantic, etc.). It also injects a fake `src.database` module. Consequences when writing tests:
- Real FastAPI/Starlette subpackages are *not* stubbed (route tests import them), but the others may be mocks — don't assume a real `sqlalchemy` is present unless installed.
- Import the symbol under test lazily/inside the test where possible, so the stubbing in `conftest` takes effect first.

## Architecture

**Slim orchestrator pattern.** `app.py` (~1000 lines) is wiring only: it registers MIME types, loads `.env`, builds the FastAPI app + middleware stack, then calls `initialize_managers()` and includes every router. Almost no business logic lives here.

**Three-layer backend:**
- `core/` — cross-cutting infrastructure: `auth.py` (AuthManager, sessions, 2FA), `database.py` (SQLAlchemy models — large, the single source of truth for the schema), `session_manager.py`, `middleware.py`, `models.py`, `platform_compat.py`.
- `src/` — business logic and managers. This is where the real work is. Heavy hitters: `agent_loop.py` (the agentic turn loop), `llm_core.py` (provider-agnostic LLM calls), `ai_interaction.py`, `chat_processor.py`/`chat_handler.py`, `deep_research.py`/`research_handler.py`, `task_scheduler.py` (cron-style tasks), `builtin_actions.py` + `tool_*` (see tool system below).
- `routes/` — thin-ish HTTP layer, one module per feature area. **Every router module exports a `setup_<area>_routes(...)` factory** that receives its manager dependencies and returns an `APIRouter`; `app.py` calls these and `include_router`s the result. Follow this pattern when adding endpoints — do not instantiate managers inside route modules.

**Dependency wiring.** `src/app_initializer.py::initialize_managers()` constructs the singleton managers (memory, skills, session, uploads, presets, chat/research/upload handlers, model discovery) once at startup and returns them in a dict. `app.py` destructures that dict and passes the relevant managers into each `setup_*_routes(...)` factory. To add a new manager: build it in `initialize_managers`, add it to the returned dict, thread it into the route factories that need it.

**Tool / agent system** (the most interconnected part — touch carefully):
- `src/tool_schemas.py` — JSON-schema definitions of built-in tools (`FUNCTION_TOOL_SCHEMAS`).
- `src/tool_implementations.py` (very large) — the actual tool functions.
- `src/tool_execution.py` — dispatch/execution + result handling.
- `src/tool_index.py` — embedding-based tool *selection* (RAG over tools, pre-warmed at startup so the first turn isn't slow).
- `src/tool_security.py` / `src/tool_parsing.py` — per-user blocking and arg parsing.
- `src/agent_loop.py` orchestrates the multi-step turn over these.
External tools come via MCP: `src/mcp_manager.py` + `src/builtin_mcp.py` register servers from `mcp_servers/` (email, image-gen, memory, rag) at startup.

**Startup lifecycle.** A FastAPI lifespan (`_startup_event`/`_shutdown_event` in `app.py`) runs *after* the server accepts traffic: purges ephemeral incognito sessions, starts the background-job monitor (`src/bg_monitor.py`, auto-continues the agent when a `#!bg` shell job finishes), connects MCP servers, and pre-warms the tool index + LLM endpoints. These are fire-and-forget tasks kept alive via `app.state._startup_tasks` — degrade gracefully (logged as non-critical) rather than blocking boot.

**Data & vector stores.** Relational state is SQLite via SQLAlchemy (`DATABASE_URL`, default `data/app.db`). Semantic memory + RAG use ChromaDB with local ONNX embeddings (fastembed); when ChromaDB is unavailable the app logs `DEGRADED` and falls back instead of crashing — preserve that degrade-don't-die behavior. All user data lives under `data/` (gitignored).

### Database schema & migrations (`core/database.py`)

This single file is the schema's source of truth: ~25 SQLAlchemy models (`Session`, `ChatMessage`, `Document`, `Memory`, `EmailAccount`, `ModelEndpoint`, `McpServer`, `ScheduledTask`, `CalendarEvent`, `Note`, `ApiToken`, `Webhook`, `Integration`, …), all keyed by an `owner` (username) column for multi-user isolation.

- **There is no Alembic / migration framework.** The schema is built with `Base.metadata.create_all`, and schema *changes* are hand-written `_migrate_*()` functions that run `ALTER TABLE` at startup, each guarded so it's a no-op when already applied. **When you add a column to an existing model, you must also add a matching `_migrate_add_<col>_column()` (or extend an existing migrate fn) so existing databases get upgraded** — otherwise old installs crash on the missing column.
- **Secrets at rest** use the `EncryptedText` column type (Fernet via `src/secret_storage.py`, `enc:` prefix). Legacy plaintext rows are auto-encrypted on next write by a startup migration. Use `EncryptedText` for any new column holding passwords, API keys, or tokens — don't store secrets as plain `Text`.

### Auth & multi-user scoping (`core/auth.py`, `src/auth_helpers.py`)

Routes don't re-implement auth. Use the shared helpers:
- `require_user(request)` → username or 403; `effective_user(request)` → resolves the acting user (honors auth-disabled/localhost-bypass dev modes).
- `require_privilege(request, "can_use_research")` → enforces a per-user privilege flag (keys like `can_use_documents`, `can_generate_images`, `can_manage_memory`, …). Admin-only areas are gated separately.
- `owner_filter(query, Model, user)` → the canonical way to scope a query to the caller's rows (with optional shared rows). Always filter user-owned data through this rather than ad-hoc `.filter(Model.owner == ...)`.

### LLM provider abstraction (`src/llm_core.py`, `src/endpoint_resolver.py`)

Providers are **not** a registry of classes — they're detected from the endpoint URL by `_detect_provider()` (openai / anthropic / ollama / openrouter / vLLM / llama.cpp / …). `endpoint_resolver.py` then builds the provider-correct chat URL, models URL, and headers (`build_chat_url` / `build_models_url` / `build_headers`), and `llm_core.py` normalizes the request/response shape (e.g. Anthropic vs OpenAI message format, header auth, orphan-tool-message pruning). To add or fix provider support, change these two files — don't special-case providers inside route or handler code.

**Frontend.** No framework, no build. `static/index.html` (~190KB) + `static/app.js` + `static/style.css` + modular ES modules under `static/js/` (subdirs: `calendar/`, `editor/`, `compare/`, `markdown/`, `research/`, `emailLibrary/`, `color/`, `util/`). Served by a revalidating static handler in `app.py`. Icons are inline monochrome SVG — no emoji.

## Conventions worth matching

- **Degrade, don't crash.** Optional subsystems (ChromaDB, MCP, Playwright MCP, optional pip deps, GPU) are wrapped so failures log a warning and disable the feature. New optional integrations should follow suit.
- **Admin-gating & per-user privileges.** Shell/Python/file tools and admin routes (MCP mgmt, API tokens, webhooks, serving, backups, settings) are privilege-checked via `core/auth.py` + `src/auth_helpers.py`. Re-check privileges when adding sensitive routes/tools.
- **Windows/macOS portability.** `app.py` and `src/embeddings.py` set HF symlink env vars before import; `.env` is read with `utf-8-sig` to tolerate BOM; uvicorn ports differ by platform (`7000` Docker/Linux, `7860` macOS via `start-macos.sh`). Check `core/platform_compat.py` before adding OS-specific code.

## Maintaining this file

This file is read by every agent and contributor working on the repo, so keep it accurate but lean — it documents *mechanisms and conventions that span multiple files*, not feature lists or anything already in `README.md` / `CONTRIBUTING.md`. When a change you make invalidates or extends something here, update CLAUDE.md **in the same PR**. Concretely, update it when you:

- change the route-registration pattern, `initialize_managers` wiring, or the startup/lifespan sequence;
- add/rename a manager, service directory, or a major `src/` subsystem (agent loop, tool system, providers, research, scheduler);
- change how the tool/agent or MCP system is structured (schemas ↔ implementations ↔ execution ↔ index);
- alter the DB-migration convention, the `EncryptedText` secrets approach, or the `owner`/privilege scoping helpers;
- add a new LLM provider path or change `_detect_provider` / `endpoint_resolver`;
- change test bootstrapping (`tests/conftest.py` stubbing) or the commands above.

Do **not** add: per-file walkthroughs, exhaustive route/endpoint lists, transient TODOs, or restated README/CONTRIBUTING content. If a section here ever contradicts the code, fix the section — a stale instruction is worse than a missing one. Prefer describing the *pattern* (so it survives refactors) over naming specific line numbers.
