# Odysseus — Handoff Doc

> Architecture & onboarding reference · drafted June 2026  
> Repo: [pewdiepie-archdaemon/odysseus](https://github.com/pewdiepie-archdaemon/odysseus)  
> Default branch: `dev` (active development) · Stable branch: `main` (curated releases)

---

## What Odysseus Is

A **self-hosted AI workspace** — local-first, privacy-first — that combines chat, agents, model management (Cookbook), deep research, documents, memory/skills, email, calendar, notes/tasks, and more in one web UI. Think ChatGPT/Claude desktop experience, but running on your own hardware with your own data.

**License:** AGPL-3.0-or-later

---

## Architecture & Structure

### Project type

**Monolithic web app + API** — single Python/FastAPI backend serving a vanilla ES-module frontend (no React/Vite). Not a monorepo. Optional satellite packages:

| Path | Role |
|---|---|
| `integrations/claude/` | Claude Code skill bundle (downloaded via `/api/claude/plugin.zip`) |
| `integrations/codex/` | Codex plugin bridge docs |
| `companion/` | LAN/mobile companion pairing API docs |

### Tech stack

| Layer | Choice |
|---|---|
| **Backend** | Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, Pydantic v2 |
| **Frontend** | Vanilla JS (ES modules), single-page app in `static/` |
| **Database** | SQLite by default (`data/app.db`); Postgres via `DATABASE_URL` |
| **Vector memory** | ChromaDB (HTTP client) + fastembed (local ONNX embeddings) |
| **Search** | SearXNG (bundled in Docker), optional Brave/Google/Tavily/Serper |
| **Auth** | bcrypt sessions, 2FA (pyotp), scoped API tokens |
| **Agent tools** | Built-in tools + MCP servers (`mcp` package) |
| **Model serving** | vLLM, llama.cpp, SGLang, Ollama (via Cookbook + endpoint config) |
| **Notifications** | ntfy (bundled in Docker), browser, email |
| **Calendar** | Local + CalDAV sync (`caldav`, `icalendar`) |
| **Email** | IMAP/SMTP (per-account) |
| **Deploy** | Docker Compose (recommended), native Linux/macOS/Windows |

**Key Python deps:** `fastapi`, `uvicorn`, `sqlalchemy`, `httpx`, `chromadb-client`, `fastembed`, `caldav`, `mcp`, `bcrypt`, `cryptography`, `pytest`

**Optional extras** (`requirements-optional.txt`): `faster-whisper` (local STT), `duckduckgo-search`, `PyMuPDF` (PDF viewer, AGPL), `markitdown` (Office extraction)

### Directory structure

```
odysseus/
├── app.py                  # FastAPI entry — middleware, lifespan, router wiring
├── setup.py                # First-run: dirs, DB init, admin user
├── requirements.txt
├── requirements-optional.txt
├── pyproject.toml          # pytest config + taxonomy markers
├── docker-compose.yml      # Odysseus + ChromaDB + SearXNG + ntfy
├── Dockerfile
├── .env.example
│
├── core/                   # Shared infrastructure
│   ├── auth.py             # AuthManager, sessions, 2FA
│   ├── database.py         # SQLAlchemy models + SessionLocal
│   ├── middleware.py       # Security headers, CORS helpers
│   ├── constants.py        # Paths, env defaults
│   └── exceptions.py
│
├── routes/                 # HTTP API — one module per feature area (~50 files)
│   ├── chat_routes.py      # /api/chat, /api/chat_stream (SSE)
│   ├── session_routes.py   # Session CRUD
│   ├── memory_routes.py    # /api/memory
│   ├── document_routes.py  # Document editor API
│   ├── email_routes.py     # IMAP/SMTP inbox
│   ├── calendar_routes.py  # Local + CalDAV calendar
│   ├── cookbook_routes.py  # Model download/serve (Cookbook)
│   ├── codex_routes.py     # Scoped agent API (/api/codex/*)
│   ├── mcp_routes.py       # MCP server management
│   └── …
│
├── src/                    # Business logic (not HTTP handlers)
│   ├── llm_core.py         # LLM streaming, provider adapters
│   ├── agent_loop.py       # Multi-round agent + tool execution
│   ├── agent_tools/        # Tool parsing, execution, schemas
│   ├── chat_processor.py   # Chat message pipeline
│   ├── model_discovery.py  # Endpoint probing, model lists
│   ├── task_scheduler.py   # Cron-style scheduled tasks
│   ├── mcp_manager.py      # MCP server lifecycle
│   ├── caldav_sync.py      # CalDAV pull/writeback
│   ├── deep_research.py    # Multi-step research runs
│   └── …
│
├── services/               # Higher-level service modules
│   ├── memory/             # Vector + keyword memory, skills
│   ├── search/             # Web search providers + ranking
│   ├── docs/               # Document service
│   ├── hwfit/              # Cookbook hardware fit scoring
│   ├── research/           # Deep research handler
│   ├── shell/              # Shell execution service
│   ├── stt/ · tts/         # Speech-to-text / text-to-speech
│   └── …
│
├── static/                 # Frontend (served by FastAPI)
│   ├── index.html          # SPA shell (~2300 lines)
│   ├── app.js              # ES module entry — imports js/* modules
│   ├── style.css           # Single large stylesheet (dark theme default)
│   ├── js/                 # ~149 modular JS files
│   │   ├── chat.js · chatStream.js · chatRenderer.js
│   │   ├── cookbook*.js    # Cookbook UI
│   │   ├── document.js · editor/
│   │   ├── emailInbox.js · emailLibrary/
│   │   ├── memory.js · tasks.js · calendar.js
│   │   └── …
│   └── lib/                # Vendored third-party JS
│
├── tests/                  # ~529 Python test files
│   ├── conftest.py         # Taxonomy markers, shared fixtures
│   ├── helpers/            # Shared test utilities
│   ├── run_focus.py        # Focused test runner by area
│   └── TESTING_STANDARD.md
│
├── scripts/                # CLI utilities (GPU checks, migrations, etc.)
├── docs/                   # Landing page + user docs
├── integrations/           # External agent plugin bundles
└── data/                   # Runtime data (gitignored)
    ├── app.db              # SQLite database
    ├── auth.json           # User accounts
    ├── settings.json       # App settings
    ├── memory.json         # Legacy memory (migrating to DB/Chroma)
    ├── uploads/ · personal_docs/ · chroma/
    └── …
```

**Pattern:** Routes are thin HTTP handlers; logic lives in `src/` and `services/`. Routers are registered in `app.py` via `setup_*_routes()` factory functions.

### Entry points

| Entry | Purpose |
|---|---|
| `app.py` | FastAPI app — **primary server entry** (`uvicorn app:app`) |
| `setup.py` | First-run bootstrap (dirs, DB, admin user) |
| `static/index.html` + `static/app.js` | Frontend SPA |
| `docker-compose.yml` | Production-like local stack |
| `launch-windows.ps1` / `start-macos.sh` | Platform launchers |

**Typical dev start:**

```bash
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
# or: docker compose up -d --build
```

Open `http://localhost:7000`. First boot prints a temporary admin password.

---

## Codebase Navigation

### Core modules / components

#### Backend

| Module | Responsibility |
|---|---|
| `app.py` | App factory, middleware stack, router registration, static file serving |
| `core/database.py` | All SQLAlchemy models, `SessionLocal`, encrypted columns |
| `core/auth.py` | Login, sessions, 2FA, privilege checks |
| `src/llm_core.py` | OpenAI-compatible streaming to any configured endpoint |
| `src/agent_loop.py` | Agent mode: multi-round LLM + fenced tool blocks |
| `src/agent_tools/` | Tool registry, parsing, execution, security policy |
| `src/chat_processor.py` | Chat message assembly, context injection, RAG |
| `src/model_discovery.py` | Probe endpoints, list models, health checks |
| `src/task_scheduler.py` | Scheduled tasks, note reminders, cron jobs |
| `src/mcp_manager.py` | Register/start/stop MCP servers |
| `services/memory/` | ChromaDB vector store, skill import/export |
| `services/hwfit/` | Cookbook: hardware scan, model fit scoring |
| `services/search/` | SearXNG + API search providers |

#### Frontend

| Module | Responsibility |
|---|---|
| `static/app.js` | Bootstraps all feature modules |
| `static/js/chat.js` + `chatStream.js` | Chat UI + SSE streaming |
| `static/js/sessions.js` | Session sidebar, folders, CRUD |
| `static/js/cookbook.js` + siblings | Model download/serve UI |
| `static/js/document.js` | Multi-tab document editor |
| `static/js/memory.js` | Memory/skills UI |
| `static/js/emailInbox.js` | Email client |
| `static/js/ui.js` | Shared UI helpers, modals, toasts |
| `static/style.css` | All styling — CSS variables (`--red`, `--fg`, `--bg`, etc.) |

### Data models / schemas

**Primary store:** SQLAlchemy models in `core/database.py`

| Model | Table | Purpose |
|---|---|---|
| `Session` | `sessions` | Chat sessions (model, endpoint, owner, mode) |
| `ChatMessage` | `chat_messages` | Messages per session |
| `Document` / `DocumentVersion` | `documents` | Document editor content + version history |
| `Memory` | `memories` | Persistent user memory entries |
| `Note` | `notes` | Quick notes with reminders |
| `ScheduledTask` | `scheduled_tasks` | Cron-style agent tasks |
| `CalendarCal` / `CalendarEvent` | `calendar_*` | Local calendar + CalDAV sync |
| `EmailAccount` | `email_accounts` | IMAP/SMTP credentials (encrypted) |
| `ModelEndpoint` | `model_endpoints` | LLM provider configs per user |
| `McpServer` | `mcp_servers` | MCP server definitions |
| `ApiToken` | `api_tokens` | Scoped integration tokens |
| `GalleryAlbum` / `GalleryImage` | `gallery_*` | Image gallery |
| `Comparison` | `comparisons` | Blind model compare runs |
| `CrewMember` | `crew_members` | Multi-agent crew configs |

**File-based state** (under `data/`, gitignored):

- `auth.json` — user accounts, privileges
- `settings.json` — global app settings
- `presets.json` — chat presets
- Chroma collections — vector embeddings for memory/RAG

**Pydantic request models:** `src/request_models.py` and inline in route modules.

### APIs & endpoints

All routes live under `/api/*`. Key prefixes:

| Prefix | Feature |
|---|---|
| `/api/auth` | Login, logout, 2FA, signup |
| `/api/chat`, `/api/chat_stream` | Chat + SSE streaming |
| `/api/sessions` | Session CRUD, folders, archive |
| `/api/memory` | Memory CRUD, search, import/export |
| `/api/skills` | Agent skills management |
| `/api/documents` | Document editor |
| `/api/email` | Inbox, compose, send, triage |
| `/api/calendar` | Events, CalDAV sync, .ics import/export |
| `/api/notes` | Notes + checklists |
| `/api/tasks` | Scheduled tasks |
| `/api/cookbook`, `/api/hwfit` | Model download, serve, hardware fit |
| `/api/compare` | Blind model comparison |
| `/api/research` | Deep research runs |
| `/api/mcp` | MCP server management |
| `/api/search` | Web search |
| `/api/tts`, `/api/stt` | Text-to-speech, speech-to-text |
| `/api/gallery` | Image gallery + editor |
| `/api/tokens` | API token CRUD (admin) |
| `/api/codex/*` | **Scoped agent integration API** (todos, email, memory, calendar, docs, cookbook) |
| `/api/claude/*` | Claude Code plugin download |
| `/api/companion/*` | LAN companion pairing |

**Streaming:** Chat, agent, research, shell, and model-probe endpoints use **Server-Sent Events** (`text/event-stream`).

**Agent integration API:** External agents (Claude Code, Codex) must use `/api/codex/*` with a scoped bearer token — never bypass via direct DB/SSH/MCP internals. See `routes/codex_routes.py` and `integrations/claude/README.md`.

### Shared utilities

| Utility | Location | Use |
|---|---|---|
| `src/constants.py` / `core/constants.py` | Paths, file locations, env defaults |
| `src/auth_helpers.py` | `require_user`, `require_admin`, token scope checks |
| `src/app_helpers.py` | Path joining, common helpers |
| `src/secret_storage.py` | Fernet encryption for secrets at rest |
| `src/upload_limits.py` | Validated upload size caps |
| `src/endpoint_resolver.py` | Resolve LLM endpoints per user/session |
| `src/event_bus.py` | Internal pub/sub for UI refresh |
| `core/atomic_io.py` | Atomic file writes |
| `tests/helpers/` | Test stubs, import cleanup, temp SQLite |

---

## Feature-Specific Context

> Fill this section when starting a new feature. Below: how to find the right files and patterns.

### Where a new feature typically fits

| Feature type | Touch these areas |
|---|---|
| New API endpoint | `routes/<feature>_routes.py` → register in `app.py` |
| Business logic | `src/<feature>.py` or `services/<feature>/` |
| UI panel/window | `static/js/<feature>.js` + HTML hooks in `static/index.html` + CSS in `static/style.css` |
| DB persistence | Model in `core/database.py` + migration in startup or `scripts/` |
| Agent tool | `src/agent_tools/` + `src/tool_implementations.py` + schema in `src/tool_schemas.py` |
| External agent access | Scope-gated route in `routes/codex_routes.py` |
| Scheduled/background work | `src/task_scheduler.py` + `routes/task_routes.py` |

### Existing patterns

**Route modules:**

```python
def setup_feature_routes(deps...) -> APIRouter:
    router = APIRouter(prefix="/api/feature", tags=["feature"])

    @router.get("/items")
    async def list_items(request: Request):
        user = require_user(request)
        ...

    return router
```

Registered in `app.py`:

```python
app.include_router(setup_feature_routes(...))
```

**Frontend modules:** Each feature is a default-exported object with `init()` called from `app.js`. Follow existing modules like `memory.js`, `tasks.js`, or `document.js`.

**Owner scoping:** Multi-user data uses `owner` column on models. Filter with `owner_filter` patterns from existing routes. API tokens carry `api_token_owner` in request state.

**Visual conventions (strict — PRs ignoring these get closed):**

- Reuse CSS variables: `--red`, `--fg`, `--bg`, `--card`, `--border`
- Monospace font: Fira Code (`--font-family`)
- Dark theme default; light via existing theme system
- **No Unicode emoji in UI** — use inline SVG icons (see `static/index.html`)
- No parallel components — extend existing widgets
- Attach screenshots for any visual change

**Agent tools:** LLM writes fenced code blocks with tool name as language tag (e.g. ` ```web_search `). Parsed by `src/agent_tools/`, executed by `src/tool_implementations.py`, governed by `src/tool_policy.py` and `src/tool_security.py`.

### Dependencies a feature may need

| Integration | Config location | Notes |
|---|---|---|
| LLM endpoint | Settings UI or `ModelEndpoint` table | OpenAI-compatible `/v1` |
| ChromaDB | `CHROMADB_HOST` / `CHROMADB_PORT` | Degrades to keyword fallback if down |
| SearXNG | `SEARXNG_INSTANCE` | Bundled in Docker on `:8080` |
| Email | Settings → Email accounts | IMAP/SMTP; Outlook needs OAuth (not yet) |
| CalDAV | Settings → Calendar | Radicale, Nextcloud, Apple, Fastmail |
| ntfy | Bundled in Docker on `:8091` | Push notifications |
| MCP servers | Settings → MCP | Playwright browser MCP needs `npx @playwright/mcp` cached |
| API tokens | Settings → Integrations | Scoped access for external agents |

---

## Quality & Standards

### Testing setup

| Item | Detail |
|---|---|
| **Framework** | pytest + pytest-asyncio |
| **Location** | `tests/` (~529 files) |
| **Config** | `pyproject.toml` — taxonomy markers (`area_*`, `sub_*`, `slow`) |
| **Standards doc** | `tests/TESTING_STANDARD.md`, `tests/README.md` |

**Run tests:**

```bash
python -m pytest                          # full suite
python -m pytest -m area_security           # by taxonomy marker
python3 tests/run_focus.py --area services --sub-area cookbook
python3 tests/run_focus.py --fast         # exclude slow tests
```

**Before PR — minimum checks:**

```bash
python -m py_compile app.py routes/*.py src/*.py   # changed files
python -m pytest <changed-test-files>
node --check static/js/<changed-file>.js            # JS syntax
```

**JS tests:** Some exist under `tests/` with `area_js` marker; most frontend validation is manual in-browser.

**CI note:** pytest job in CI is `continue-on-error: true` (known flaky/isolation issues). Syntax checks (Python compileall + node --check) are the reliable gates.

### Linting / formatting

- **No ESLint/Prettier/Ruff configured** in repo
- **Python:** `python -m compileall` for syntax; follow patterns in surrounding code
- **JS:** `node --check` for syntax validation
- **Style guide:** `CONTRIBUTING.md` — especially visual/style rules for UI changes
- **Hard rule:** Don't hardcode values that exist as constants/env vars

### CI/CD

| Workflow | Trigger | What it does |
|---|---|---|
| `.github/workflows/ci.yml` | PR + push to `main` | Python compileall, JS syntax check, pytest (informational) |
| `.github/workflows/docker-publish.yml` | Push to `dev`/`main` | Multi-arch Docker image → GHCR |
| `.github/workflows/pr-description-check.yml` | PR | Validates PR description format |
| `.github/workflows/issue-description-check.yml` | Issues | Validates issue description |

**Branch model:**

- **`dev`** — all PRs land here; may be unstable
- **`main`** — curated stable releases; fast-forwarded from `dev` at release time

**Docker images:**

- `ghcr.io/<owner>/odysseus:dev` — rolling dev
- `ghcr.io/<owner>/odysseus:latest` — stable release

**Deploy pattern:** Docker Compose on localhost (`127.0.0.1:7000`), reverse proxy (Caddy/nginx/Traefik/Cloudflare Access/Tailscale) for HTTPS + LAN access. Never expose raw ports to the public internet without auth + HTTPS.

---

## Product Context

### What exists today (feature map)

| Feature | Status | Key files |
|---|---|---|
| Chat (streaming) | ✅ | `routes/chat_routes.py`, `static/js/chat*.js` |
| Agent mode (tools) | ✅ | `src/agent_loop.py`, `src/agent_tools/` |
| Cookbook (model mgmt) | ✅ (platform-dependent) | `routes/cookbook_routes.py`, `services/hwfit/` |
| Deep Research | ✅ | `src/deep_research.py`, `routes/research_routes.py` |
| Compare (blind) | ✅ | `routes/compare_routes.py`, `static/js/compare/` |
| Documents (editor) | ✅ | `routes/document_routes.py`, `static/js/document.js` |
| Memory + Skills | ✅ | `services/memory/`, `routes/memory_routes.py` |
| Email (IMAP/SMTP) | ✅ (no Outlook OAuth) | `routes/email_routes.py` |
| Calendar (CalDAV) | ✅ | `routes/calendar_routes.py`, `src/caldav_sync.py` |
| Notes + Tasks | ✅ | `routes/note_routes.py`, `routes/task_routes.py` |
| Gallery / image editor | ✅ | `routes/gallery_routes.py` |
| MCP integration | ✅ | `src/mcp_manager.py`, `routes/mcp_routes.py` |
| API tokens + webhooks | ✅ | `routes/api_token_routes.py`, `routes/webhook_routes.py` |
| Claude/Codex agent API | ✅ | `routes/codex_routes.py`, `integrations/` |
| Mobile / PWA | ✅ (needs polish) | Responsive CSS, `static/manifest.json` |
| Companion (LAN pairing) | ✅ | `routes/codex_routes.py` (companion section) |

### Known gaps / roadmap priorities

From `ROADMAP.md`:

- Fresh install smoke tests (Linux, macOS, Windows, Docker, WSL)
- Cookbook reliability across GPUs/platforms
- Agent prompt/context bloat for small local models
- Email performance (IMAP latency)
- CSS cleanup (`static/style.css` is very large)
- Outlook/Office 365 OAuth (currently IMAP password only)
- Better degraded-state reporting (ChromaDB, SearXNG, email, ntfy down)

### Edge cases & constraints

| Area | Constraint |
|---|---|
| **Security** | Treat as admin console — shell, file access, model downloads. Keep `AUTH_ENABLED=true` for any network exposure. |
| **Windows** | Not actively CI-tested. HF symlinks disabled (`HF_HUB_DISABLE_SYMLINKS`). `.env` BOM handled via `utf-8-sig`. |
| **macOS Docker** | No Metal GPU in Docker — use native `./start-macos.sh` for GPU Cookbook. |
| **ChromaDB** | Never install `chromadb-client` alongside full `chromadb` — causes silent HTTP-only fallback failure. |
| **Multi-user** | Owner-scoped data; legacy null-owner rows are shared. API tokens are owner-bound. |
| **Agent integration** | External agents must use scoped `/api/codex/*` — direct DB/SSH bypass is forbidden by design. |
| **Upload limits** | Env-validated caps (e.g. `ODYSSEUS_CHAT_UPLOAD_MAX_BYTES=10MB`). |
| **Visual PRs** | Must include screenshots; must match existing CSS variable system. |

### Design / mockups

- **No Figma** — design is code-first in `static/style.css` + `static/index.html`
- **Theme system:** CSS variables, editable via in-app Theme Editor; persisted to `localStorage`
- **Landing page:** `docs/index.html` with hover-to-play demo clips
- **Icon style:** Monochrome inline SVG, no emoji

---

## How We Can Do This Together

### Repo access

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
git checkout dev          # active development branch
cp .env.example .env      # optional
docker compose up -d --build
# → http://localhost:7000
```

### Recommended onboarding flow

1. **Run it** — Docker Compose, log in, click through Chat, Documents, Cookbook, Settings
2. **Read entry points** — `app.py` (router list), `static/app.js` (frontend boot), `core/database.py` (models)
3. **Pick a feature area** — use the tables above to find routes + src + static/js
4. **Check tests** — `python3 tests/run_focus.py --area routes --sub-area <feature>`
5. **Open PR against `dev`** — small, focused, with test evidence + screenshots for UI

### Planning a new feature

When you're ready to build something specific, provide:

1. **Feature description** — what user action, what outcome
2. **Affected modules** — use the "Where a new feature fits" table above
3. **API shape** — new routes, models, agent tools needed?
4. **External integrations** — LLM, email, calendar, MCP, ChromaDB?
5. **Agent access** — does it need a scoped `/api/codex/*` endpoint?

I'll then produce a step-by-step implementation plan with exact files to touch, patterns to follow, and verification commands.

### External agent setup (Claude Code example)

Add to `odysseus/.env` (see `.env.example` → External agent clients), then set persistent shell env — see `integrations/claude/README.md`.

Windows (recommended, set once):

```powershell
[System.Environment]::SetEnvironmentVariable('ODYSSEUS_URL', 'http://127.0.0.1:7000', 'User')
[System.Environment]::SetEnvironmentVariable('ODYSSEUS_API_TOKEN', '<token-from-settings>', 'User')
```

macOS/Linux:

```bash
export ODYSSEUS_URL=http://127.0.0.1:7000
export ODYSSEUS_API_TOKEN=<token-from-settings>
python3 ~/.claude/skills/odysseus/scripts/odysseus_api.py capabilities
```

Token scopes are enforced server-side — enable toggles in **Settings → Integrations → Claude Agent**.

---

## Quick Reference

```bash
# Dev server (native)
python setup.py && python -m uvicorn app:app --host 127.0.0.1 --port 7000

# Dev server (Docker)
docker compose up -d --build && docker compose logs -f odysseus

# Tests
python -m pytest -q
python3 tests/run_focus.py --area security --fast

# Syntax checks (match CI)
python -m compileall -q app.py core routes src services scripts tests
node --check static/app.js

# Useful diagnostics
docker compose logs odysseus | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

| Resource | Path |
|---|---|
| README | `README.md` |
| Contributing | `CONTRIBUTING.md` |
| Roadmap | `ROADMAP.md` |
| Testing standard | `tests/TESTING_STANDARD.md` |
| Env reference | `.env.example` |
| Claude integration | `integrations/claude/README.md` |
| Companion API | `companion/README.md` |
| Security notes | `README.md` § Security Notes |
