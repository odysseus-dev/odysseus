# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Odysseus is a self-hosted, local-first AI workspace (a privacy-focused alternative to the ChatGPT/Claude web UI): chat with any local/API model, an agentic mode, hardware-aware model serving (Cookbook), deep research, memory/RAG, email, calendar, notes/tasks, and a document editor. It is a **FastAPI monolith** (`app.py`) serving a **no-build vanilla-JS frontend** (`static/`), backed by SQLite + JSON files. Python 3.11+.

## Commands

```bash
# Run the dev server (manual install). setup.py is a first-run config wizard, not packaging.
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # requirements-optional.txt unlocks STT/PDF/Office/DDG
python setup.py                          # first run only
python -m uvicorn app:app --host 127.0.0.1 --port 7000

# Docker (recommended for normal testing)
cp .env.example .env
docker compose up -d --build
docker compose logs --tail=120 odysseus  # first-boot admin password is printed here

# Tests — use the venv interpreter; system python3 may lack pinned deps (e.g. nh3) and fail collection
.venv/bin/python -m pytest                       # full suite
.venv/bin/python -m pytest tests/test_foo.py     # one file
.venv/bin/python -m pytest tests/test_foo.py::test_bar   # one test
.venv/bin/python -m pytest -m area_security              # taxonomy slice (see below)
.venv/bin/python -m pytest -m "area_services and sub_cookbook"

# Lint / syntax checks (what CI runs — there is no formatter/linter gate)
python -m compileall -q app.py core routes src services scripts tests   # Python syntax
node --check static/app.js                                              # JS syntax (our files only, not static/lib/)
docker compose config                                                   # validate compose changes
```

CI (`.github/workflows/ci.yml`) runs `compileall`, `node --check` over `static/app.js` + `static/js/**/*.js`, and `pytest`. The pytest job is **informational only** (`continue-on-error`) — known flaky/env-dependent failures, so a green local run on the files you touched matters more than the CI checkmark.

Tests are auto-tagged at collection time (`tests/conftest.py` + `tests/_taxonomy.py`) with an `area_*` marker and a finer `sub_*` marker derived from the filename — no config needed to use `-m`. The root `conftest.py` defaults `DATABASE_URL` to in-memory SQLite; a test needing a file-backed DB must opt in via `tests.helpers.sqlite_db.make_temp_sqlite`.

## Architecture

### Request lifecycle & wiring
`app.py` is a slim orchestrator. Startup: load `.env` (with UTF-8-BOM tolerance — a real Windows fix, don't remove), build the FastAPI app, register middleware, build managers via `src/app_initializer.py::initialize_managers()` (memory, session, RAG, chat, research, …), then `app.include_router(...)` for ~45 route modules. The lifespan hook (`app.router.lifespan_context`) wipes ephemeral incognito sessions and launches fire-and-forget background tasks (MCP connects, tool-index warmup, endpoint keepalive, task scheduler, skill/task reconciliation).

**Every route module follows the same factory pattern**: `def setup_<feature>_routes(...managers...) -> APIRouter`, decorate endpoints, `return router`; `app.py` injects the manager dependencies and includes it. To add a feature, write a `setup_*_routes()` and add one `include_router` line. Middleware is registered in reverse order (added last = runs innermost): the auth middleware stamps `request.state.current_user`; routes then call helpers in `src/auth_helpers.py` (`get_current_user`, `require_user`, `effective_user`).

### Subsystem map
| Area | Key files | Notes |
|---|---|---|
| Auth / middleware | `core/auth.py`, `core/middleware.py`, `src/auth_helpers.py` | Users/tokens/TOTP/privileges in `data/auth.json`; bearer `ody_…` tokens cached in-memory |
| LLM / chat | `src/llm_core.py`, `src/chat_processor.py`, `src/endpoint_resolver.py`, `src/model_discovery.py`, `routes/chat_routes.py` | Provider-agnostic; SSE streaming |
| Context mgmt | `src/context_budget.py`, `src/context_compactor.py`, `src/model_context.py` | Auto-summarizes near the context window |
| Agent loop / tools | `src/agent_loop.py`, `src/tool_execution.py`, `src/tool_security.py`, `src/tool_policy.py`, `src/tool_implementations.py` | Multi-round (`MAX_AGENT_ROUNDS`); 3-layer tool gating |
| MCP | `src/mcp_manager.py`, `src/builtin_mcp.py`, `src/mcp_oauth.py`, `mcp_servers/` | 4 built-in stdio servers + optional npx browser server |
| Memory / RAG | `src/memory*.py`, `src/rag*.py`, `src/chroma_client.py`, `src/embeddings.py`, `src/embedding_lanes.py` | ChromaDB + fastembed (ONNX); keyword fallback |
| Search / research | `services/search/`, `src/deep_research.py`, `src/visual_report.py` | SearXNG default; SSRF-guarded fetch |
| Cookbook (serving) | `services/hwfit/`, `routes/cookbook_routes.py`, `src/cookbook_serve_lifecycle.py`, `src/model_discovery.py` | Hardware fit scoring; tmux-detached download/serve |
| Data / config | `src/constants.py`, `core/database.py`, `src/settings.py`, `src/secret_storage.py`, `core/atomic_io.py` | SQLite + JSON; Fernet-encrypted secret columns |
| Feature verticals | email, calendar/CalDAV, tasks/notes, documents, tts/stt, webhooks | All share route → helper/service → data shape |
| Frontend | `static/app.js`, `static/index.html`, `static/js/**`, `static/style.css` | Native ES modules, no bundler |

### Provider abstraction (LLM)
A single OpenAI-compatible abstraction (`src/llm_core.py`) serves vLLM, llama.cpp, Ollama, OpenRouter, OpenAI, Anthropic, GitHub Copilot, and ChatGPT-subscription. Flow: route → `ChatProcessor` (injects RAG/memory/web via hybrid retrieval) → context compaction → `llm_core` detects the provider from the endpoint and normalizes messages → provider adapter. **Convention: everything is `/v1/chat/completions` unless detected otherwise**; Anthropic and Ollama-native have their own payload shapes that `llm_core` converts to/from OpenAI form. Dead endpoints get a cooldown to avoid stalling on connect timeouts.

### Agent tool security (3 layers — read before touching tools)
1. **Per-user privilege gate** — `NON_ADMIN_BLOCKED_TOOLS` (shell/python/file/email/endpoints) blocked for non-admins; MCP is hidden entirely from unprivileged/public users.
2. **Per-turn policy** (`src/tool_policy.py`) — `plan_mode` (read-only allowlist) and `guide_only` mode (all tools off).
3. **Path confinement** — `read_file`/`write_file` resolve realpaths confined to `data/` + `/tmp`; sensitive paths (`.ssh`, etc.) are deny-listed. Checks are **fail-closed** and run *before* execution. MCP tools dispatch by the `mcp__<server>__<tool>` name prefix.

### Storage model
Two layers: **SQLite** (`DATABASE_URL`, default `data/app.db`) for sessions/messages/documents/gallery/endpoints/credentials; **JSON files under `DATA_DIR`** for settings/auth/prefs/presets/memory/etc. Multi-user rows carry an `owner` column; ownership mismatches return `404` (not `403`) to avoid leaking existence. Sensitive DB columns use the `EncryptedText` type (Fernet key at `data/.app_key`). All JSON writes go through `atomic_write_json` (`core/atomic_io.py`).

## Critical conventions

- **Never hardcode paths, ports, internal URLs, or limits.** Every persisted file/dir has a named constant in `src/constants.py` — the single source of truth and the only place `ODYSSEUS_DATA_DIR` is read. Import the constant (e.g. `AUTH_FILE`, `CHROMA_DIR`, `SETTINGS_FILE`); do **not** re-derive with `os.path.join(DATA_DIR, "x.json")`, `Path(__file__)…`, `/app/...`, or a relative `"data/..."`. Use `DATA_DIR` directly only for dynamic paths with no fixed name. For loopback calls to our own API use `internal_api_base()`. If a value has no constant and is used in >1 place, add one to `src/constants.py`. `core/constants.py` is a **pure re-export shim** — never add constants there.
- **The source tree is read-only in Docker** and `/app/...` doesn't exist on native runs — guard directory creation so an unwritable path degrades gracefully instead of crashing at import.
- **Reading config:** use `src.settings.get_setting(key)` / `get_user_setting(key, owner)`, never read the JSON directly. Settings layer as `.env` defaults → in-app `data/settings.json` → per-user prefs (whitelisted keys only).
- **Frontend visual style is enforced and PRs that violate it are closed regardless of correctness.** Reuse existing CSS variables (`--bg`, `--fg`, `--card`, `--border`, `--red`, …) and existing button/input/card classes — never introduce new colors/sizes/spacing or parallel components for an existing widget. **No Unicode emoji anywhere** in UI or code — use inline monochrome SVG matching `static/index.html`. Monospace `Fira Code`, dark theme default (light goes through the theme system). localStorage keys live in `storage.js:KEYS`. Inline `<script>` needs `nonce="{{CSP_NONCE}}"` or it silently fails. Any change to look/feel requires running the app and attaching a screenshot (mobile too).

## Testing standard (`tests/TESTING_STANDARD.md`, `tests/README.md`)

- **Behavior-first**: assert on observable behavior, not source text / AST / `inspect.getsource`. Source-string assertions are allowed only when an invariant can't be driven at runtime — document why in the docstring.
- **Deterministic & isolated**: no wall-clock/network/RNG/order dependence. Never mutate `sys.modules`, `os.environ`, or cwd without a controlled helper and guaranteed cleanup — use `tests/helpers/import_state.py` (`preserve_import_state`, `clear_module`) and `monkeypatch`, not raw assignment. Keep the root `conftest.py` minimal (path, DB-URL default, heavy-dep stubs only).
- **Do not weaken tests to make CI pass** (no `skip`/`xfail`/deleted coverage). Distinguish a stale expectation from a real policy change before editing a failing test — never edit a test to match a regression.
- Extract a shared helper only when duplication is proven; prefer plain functions in `tests/helpers/` over fixtures; each helper documents its limits.

## Contributing

- **PRs target `dev`, not `main`.** `dev` is the default working branch (latest, sometimes unstable); `main` is the curated/stable release branch.
- **Conventional Commits**: `type(scope): summary` (`fix`, `feat`, `refactor`, `docs`, `test`, `chore`, `ci`). Imperative subject; "why" in the body.
- Keep PRs small and single-purpose; don't mix file moves, formatting, refactors, and behavior changes.
- This project does not actively test Windows; Docker-on-Linux or a Linux/macOS manual install is the supported path.

## Gotchas

- Creating/revoking API tokens must bust the in-memory token cache (`app.state.invalidate_token_cache()`) or stale tokens keep authenticating.
- Reserved usernames (`internal-tool`, `api`, `demo`, `system`) are synthetic owners; never let `data/auth.json` contain a real account with those names (`require_admin` would treat it as admin).
- ChromaDB is a separate HTTP service; if unreachable, `get_rag_manager()` returns `None` (throttled retry) and memory/RAG fall back to keyword search — check the availability flag instead of assuming vectors exist.
- Built-in npx MCP servers (e.g. `@playwright/mcp`) only start if already in the npx cache — a fresh install skips them with a log line rather than blocking on a multi-minute download.
- Deep-research / web-fetch URL fetching is SSRF-guarded (blocks private/loopback/metadata IPs). CalDAV blocks private IPs unless `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1`. Configured search-*provider* response URLs are trusted and not re-validated.
- Default ports: app `7000` (macOS `start-macos.sh` uses `7860` because AirPlay holds 7000), SearXNG `8080`, ntfy `8091`, ChromaDB host `8100`, Ollama `11434`. Keep everything but the authenticated Odysseus entrypoint internal-only.