# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Odysseus is a self-hosted AI workspace — a FastAPI backend with a vanilla-JS frontend. No build step for the frontend; JS modules are loaded via `<script>` tags in `static/index.html`.

## Running the App

**macOS (native, recommended for Apple Silicon GPU):**
```bash
./start-macos.sh          # installs deps, starts uvicorn on :7860
```

**Linux/macOS manual:**
```bash
source venv/bin/activate
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

**Docker:**
```bash
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

First boot prints a temporary admin password to stdout (or `docker compose logs odysseus`).

## Testing

Always activate the venv before running tests — the system `python3` may be missing pinned deps.

```bash
# Full suite
python -m pytest

# Focused by taxonomy area (security | routes | services | cli | js | helpers | unit)
python3 tests/run_focus.py --area services
python3 tests/run_focus.py --area services --sub-area cookbook
python3 tests/run_focus.py --fast          # excludes slow-marked tests
python3 tests/run_focus.py --last-failed

# Raw pytest markers also work
python -m pytest -m area_security
python -m pytest -m "area_services and sub_cookbook"

# Single file
python -m pytest tests/test_foo.py
```

Pre-commit checks:
```bash
git diff --check
python3 -m py_compile <changed .py files>
node --check static/js/<changed file>.js
```

## Architecture

```
app.py          FastAPI entry point; mounts routes, runs lifespan startup
core/           auth, database (SQLAlchemy), middleware, atomic I/O, session manager
src/            all application logic (llm_core, agent_loop, agent_tools, chat_processor,
                memory, embeddings, tool_execution, mcp_manager, settings, constants, …)
routes/         one file per feature area (chat, session, email, calendar, memory, …)
services/       heavier service modules (stt/, tts/, search/, memory/, hwfit/Cookbook, …)
mcp_servers/    built-in MCP servers (email, image gen, memory, RAG)
static/         index.html + app.js + style.css + js/ (vanilla JS modules)
tests/          pytest suite; helpers/ contains shared import-state and DB helpers
```

**Constants — single source of truth:** `src/constants.py` defines every persisted path and data-directory constant. `core/constants.py` is a re-export shim only. Never re-derive paths inline with `os.path.join(DATA_DIR, "x.json")` — import the named constant. If no constant exists yet, add one to `src/constants.py`.

**Internal API URLs:** use `internal_api_base()` from `src.constants` (honors `ODYSSEUS_INTERNAL_BASE` / `APP_PORT`). Never hardcode `http://localhost:7000`.

**Auth:** `core/auth.py` — bcrypt + TOTP, multi-user, config in `data/auth.json`. Per-user privilege flags control tool access.

**Frontend:** vanilla ES modules, no bundler. CSS variables (`--fg`, `--bg`, `--card`, `--border`, `--red`, …) are the design tokens — reuse them, don't introduce new color values. No Unicode emoji in UI or code; use inline SVG. Primary font is Fira Code (monospaced). Dark theme is the default.

**Data:** everything lives under `data/` (gitignored). Key stores: `app.db` (SQLite via SQLAlchemy), `auth.json`, `settings.json`, `memory.json`, `chroma/` (ChromaDB vectors).

## Code Conventions

**Commits:** Conventional Commits — `type(scope): summary` (e.g. `fix(search): …`, `feat(notes): …`). Keep subject short and imperative; put the "why" in the body when not obvious.

**Branches:** PRs go to `dev` (default branch, active development). `main` is the curated stable branch.

**Tests:** behavior-first — call the function/route and assert outcomes. Avoid source-text/AST assertions unless the behavior truly cannot be driven at runtime. Never mark a test `slow` without duration evidence from `--durations`. Do not mutate `sys.modules`, `os.environ`, or CWD without cleanup (use `monkeypatch` or the helpers in `tests/helpers/import_state.py`).

**PRs:** one change type per PR. Don't mix file moves with logic changes, or helper extraction with assertion changes.
