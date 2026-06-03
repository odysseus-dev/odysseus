# Architecture Research

**Domain:** Large FastAPI modular monolith — safe decomposition and target structure
**Researched:** 2026-06-03
**Confidence:** HIGH (patterns verified against FastAPI official docs + codebase analysis)

---

## Standard Architecture (Target State)

This section describes where Odysseus should land, built as an extension of its existing patterns.

```
┌─────────────────────────────────────────────────────────────────┐
│               Browser SPA (static/, vanilla JS)                  │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP / SSE
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  FastAPI Orchestrator  app.py                    │
│  Lifespan wiring only — no endpoints, no business logic          │
│  app.include_router(setup_*_routes(deps))  ×N                    │
└──────────────┬──────────────────────┬──────────────────────────┘
               │                      │
               ▼                      ▼
┌──────────────────────┐  ┌──────────────────────────────────────┐
│  Route Layer         │  │  Route Layer (split domains)          │
│  routes/             │  │  routes/email/                        │
│   <domain>_routes.py │  │   ├── routes.py  (thin factory)       │
│   <domain>_helpers.py│  │   ├── crud_helpers.py                 │
│  (thin + focused)    │  │   ├── send_helpers.py                 │
└────────┬─────────────┘  │   └── polling.py                     │
         │                └────────────────────┬─────────────────┘
         │                                     │
         ▼                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│              Handler / Manager / Service Layer                   │
│  src/*_handler.py  src/*_manager.py  services/*/service.py      │
│  (stateful; wired once in initialize_managers())                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Persistence Layer                               │
│  core/database.py  (SQLAlchemy ORM + SessionLocal)              │
│  JSON files via core/atomic_io.py · ChromaDB vector stores      │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities (target)

| Component | Responsibility | Implementation Pattern |
|-----------|---------------|------------------------|
| `app.py` | Wiring only — lifespan, middleware, include_router | No endpoints, no logic; delegates to factories |
| `setup_<domain>_routes(deps)` | Register endpoints; validate/auth-scope; shape response | Factory closure; thin; delegates to helpers or handlers |
| `<domain>_helpers.py` | Heavy per-route logic, db queries, formatting | Pure functions or async functions; no FastAPI deps |
| `src/<feature>_handler.py` | Stateful orchestration for a feature domain | Class wired once in `initialize_managers()` |
| `services/<name>/service.py` | Self-contained capability (clean async interface) | Independent; re-exported via `services/__init__.py` |
| `core/` | Cross-cutting infra — DB, auth, session, middleware | Shared foundations; imported by all layers |

---

## Decomposition Boundaries — What to Split and Into What

### Priority 1: `src/tool_implementations.py` (~4100 lines)

**Problem:** A single file holding every `do_*` tool implementation — search, email, calendar, shell, RAG, image gen, notes, cookbook, memory, etc. Any change creates full-file diff noise and CI-lock risk.

**Target structure — convert to a package:**

```
src/tool_implementations/
├── __init__.py          # Re-export all do_* symbols (back-compat facade)
├── search.py            # do_web_search, do_research_search, ...
├── email.py             # do_send_email, do_read_email, ...
├── calendar.py          # do_create_event, do_list_events, ...
├── shell.py             # do_shell_command, do_python_exec, ...
├── rag.py               # do_rag_query, do_add_to_memory, ...
├── image.py             # do_generate_image, do_describe_image, ...
├── notes.py             # do_create_note, do_list_notes, ...
├── cookbook.py          # do_search_recipe, do_add_recipe, ...
└── _shared.py           # Common imports/helpers used across families
```

The `__init__.py` facade preserves the existing import surface: `from src.tool_implementations import do_web_search` continues to work unchanged. This exactly mirrors the existing `src/agent_tools.py` facade precedent (which re-exports `tool_parsing`, `tool_schemas`, `tool_execution`, `tool_implementations`).

**Grouping heuristic:** One submodule per tool family (corresponds roughly to one external integration or one data domain). Target 200-500 lines per submodule.

### Priority 2: `routes/email_routes.py` (~3200 lines)

**Problem:** One route factory containing account CRUD, folder listing, message fetch/send, attachment handling, search, threading, sync triggers, and poller management.

**Target structure — split into sub-router helpers:**

```
routes/email/
├── __init__.py          # Re-export setup_email_routes (back-compat)
├── routes.py            # setup_email_routes(deps) — thin orchestrator, composes sub-routers
├── account_helpers.py   # Account CRUD, settings
├── message_helpers.py   # Read/fetch/search/thread messages
├── send_helpers.py      # Compose, send, attachments
├── sync_helpers.py      # Sync triggers, poller controls
└── folder_helpers.py    # Folder listing, move/copy operations
```

`setup_email_routes()` assembles `APIRouter`s from each sub-module using `router.include_router(sub_router)`. The public `prefix="/api/email"` and tags stay the same — clients see zero change.

### Priority 3: `routes/cookbook_routes.py` (~2200 lines)

**Problem:** Recipe CRUD, ingredient tracking, hardware-fit scoring, import/export, and search all in one file.

**Target structure:**

```
routes/cookbook/
├── __init__.py
├── routes.py            # setup_cookbook_routes() orchestrator
├── recipe_helpers.py    # CRUD and search
├── ingredient_helpers.py
├── hwfit_helpers.py     # Hardware-fit scoring (wraps services/hwfit)
└── import_export_helpers.py
```

### Priority 4: `routes/model_routes.py` (~1800 lines) and `routes/gallery_routes.py` (~1800 lines)

Same pattern: extract from a single route file into a `<domain>/` package. Model routes split into `endpoint_helpers.py`, `provider_helpers.py`, `benchmark_helpers.py`. Gallery routes split into `upload_helpers.py`, `generation_helpers.py`, `metadata_helpers.py`.

### Priority 5: `src/agent_loop.py` (~2300 lines)

Lower urgency than the above (it's a streaming loop, not a god-route), but benefits from helper extraction:

```
src/agent_loop/
├── __init__.py          # Re-export stream_agent_loop (back-compat)
├── loop.py              # Main streaming driver
├── prompt_builder.py    # System prompt assembly, context injection
├── tool_dispatch.py     # Bridge to tool_execution
└── _cache.py            # _cached_base_prompt and invalidation
```

---

## Safe Extraction Sequencing (Behavior-Preserving Protocol)

The ordering principle is: **cover → extract → verify → repeat**. Never extract code with thin coverage.

### Step 0: Establish a coverage baseline

Before any extraction, run:
```bash
pytest --cov=src --cov=routes --cov-report=term-missing
```
Record which lines in each target file are covered. This is the "before" snapshot. Areas with zero direct coverage get characterization tests written first.

### Step 1: Coverage-first for god-files with thin tests

Per `CONCERNS.md`, `src/tool_implementations.py` and `routes/email_routes.py` have no dedicated test files. The rule is: **fill the coverage gap before the first extraction in that file.**

Write characterization tests — tests that pin the current behavior without judging it. They are fast to write (capture inputs/outputs of existing functions), and they make the subsequent extraction safe.

### Step 2: Extract-and-delegate (one function group at a time)

The safest unit of extraction is a function group (one tool family or one HTTP resource sub-group).

**Protocol per extraction:**

1. Copy the target functions verbatim into the new submodule.
2. Replace the original implementations with delegation imports:
   ```python
   # src/tool_implementations.py — during transition
   from src.tool_implementations.search import do_web_search, do_research_search
   # ... all other groups still live here until extracted
   ```
3. Run the full test suite — it must still be green.
4. Once all groups are delegating, convert the original file into a proper `__init__.py` re-export facade:
   ```python
   # src/tool_implementations/__init__.py — final state
   from .search import do_web_search, do_research_search
   from .email import do_send_email, do_read_email
   # ...
   __all__ = ["do_web_search", "do_research_search", ...]
   ```
5. Delete the now-empty original file. Green suite confirms nothing broke.

This is the identical approach that produced `src/agent_tools.py` and its four submodules — extend the pattern, don't invent a new one.

### Step 3: Sub-router composition for route god-files

For `routes/email_routes.py` and `routes/cookbook_routes.py`:

1. Create `routes/email/` directory with `__init__.py` re-exporting `setup_email_routes`.
2. Move helper functions into sub-helper files one group at a time (each move = one commit with a passing suite).
3. Once all helpers are extracted, the `routes.py` factory becomes thin: it creates sub-routers, passes deps, and calls `router.include_router(sub_router)` for each group. No HTTP prefix or response contract changes.
4. The `routes/email_routes.py` original file becomes a one-liner re-export:
   ```python
   # routes/email_routes.py — back-compat shim
   from routes.email import setup_email_routes  # noqa: F401
   ```
5. In a final cleanup commit, update `app.py`'s import to `from routes.email import setup_email_routes` and remove the shim.

### Extraction Ordering (recommended sequence)

| Order | Target | Rationale |
|-------|--------|-----------|
| 1 | Write characterization tests for `tool_implementations.py` | Unblock the highest-value extraction |
| 2 | `src/tool_implementations` → package (search + email families first) | Biggest file; most tool coverage needed |
| 3 | Write characterization tests for `routes/email_routes.py` | Cover before split |
| 4 | `routes/email/` sub-router split | Second biggest; high regression risk |
| 5 | `routes/cookbook/` sub-router split | Third; lower risk (helpers already exist) |
| 6 | `routes/model/` and `routes/gallery/` splits | Similar size and pattern |
| 7 | `src/agent_loop` → package | After tool system is clean |
| 8 | `src/task_scheduler.py` → helpers | Extract cron logic from I/O orchestration |

---

## Dependency Injection Migration Path (Incremental)

### Current state

Module-level singletons wired by setter functions (`set_session_manager`, `set_task_scheduler`, `set_memory_manager`) in `app.py`. Singletons like `mcp_manager`, `task_scheduler`, and `webhook_manager` are module-global. Tests work around this with lazy imports inside test functions.

### Target state (incremental, no big-bang)

The target is: **all long-lived objects flow through `initialize_managers()` and the route factory's `deps` argument, not through module-level setters.** FastAPI's native `Depends()` is used only where it adds testability value (auth, db sessions, request-scoped state). Heavy application singletons stay in `initialize_managers()` — do not push them into `Depends()` chains.

### Phase A: Stop adding new setters (immediate, no migration cost)

Any new singleton introduced must be wired through `initialize_managers()` and the route factory signature. Never add a new `set_*` function. This stops the anti-pattern from spreading.

### Phase B: Migrate low-risk setters one at a time

Migrate each `set_*` call by:

1. Adding the dependency to the `initialize_managers()` return dict.
2. Adding it as a parameter to the affected `setup_*_routes(deps)` factory.
3. Deleting the `set_*` call from `app.py` startup.
4. Deleting the `set_*` function and the global it wrote to.
5. Run suite — green confirms the wiring is equivalent.

**Recommended first candidates** (fewest downstream callers, lowest risk):
- `set_memory_manager` (used in search/memory routes)
- `set_webhook_manager`
- `set_task_scheduler` in non-chat routes

**Defer:** `set_session_manager` touches the agent loop and streaming path — migrate last, after the tool system is stable.

### Phase C: FastAPI `Depends()` for request-scoped concerns only

Use `Depends()` for things that are genuinely per-request (database sessions, auth/owner scoping, request-body parsing). The pattern in Odysseus that is already idiomatic and should be extended:

```python
# Already exists — extend this, don't replace it
async def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# In a route factory:
@router.post("/email/send")
async def send_email(
    request: Request,
    db: Session = Depends(get_db),
    owner: str = Depends(require_owner),  # existing auth pattern
):
    return await _do_send(db, owner, await request.json())
```

Do NOT push application-lifetime singletons (session_manager, mcp_manager, task_scheduler) into `Depends()`. They are wired once at startup by `initialize_managers()` and injected via route factory closure — that is already the correct pattern.

### Phase D: `app.state` as the holding point during transition

For singletons that are currently globals in `app.py` but not yet wired through `initialize_managers()`, use `app.state` as an intermediate holding point during migration:

```python
# app.py lifespan — intermediate step
app.state.webhook_manager = components["webhook_manager"]

# Route factory — reads from app.state via Request
@router.post("/webhook/register")
async def register_webhook(request: Request):
    wm = request.app.state.webhook_manager
    ...
```

This is a safe stepping-stone: it moves the value off a bare global without requiring the full `initialize_managers()` + factory-param plumbing in one commit. The final step is to wire it explicitly through the factory and remove the `app.state` reference.

### DI migration priority

| Setter | Risk | Recommended phase |
|--------|------|-------------------|
| New code | Zero | Never add setters — use initialize_managers() immediately |
| `set_memory_manager` | Low | Phase B, sprint 1 |
| `set_webhook_manager` | Low | Phase B, sprint 1 |
| `set_task_scheduler` (non-agent paths) | Medium | Phase B, sprint 2 |
| `set_session_manager` (agent/streaming path) | High | Phase B, last |
| DB session (`SessionLocal`) | Already good | Formalize as `Depends(get_db)` in new routes |

---

## Module-Boundary and Layering Guidance

### Layer rules (extend, do not break existing conventions)

```
app.py
  └── wires only; may read app.state; no business logic

routes/<domain>_routes.py  or  routes/<domain>/routes.py
  └── HTTP validation, auth scoping, response shaping
  └── delegates to <domain>_helpers.py or src/*_handler.py
  └── must not import from other routes/ modules
  └── must not open DB sessions directly (use Depends(get_db) or handler)

routes/<domain>_helpers.py  or  routes/<domain>/*_helpers.py
  └── heavy per-route logic (queries, formatting, side-effects)
  └── may import from src/ and services/ and core/
  └── pure functions preferred; no FastAPI-specific types

src/*_handler.py / src/*_manager.py
  └── stateful orchestration; wired by initialize_managers()
  └── may import from services/ and core/
  └── must not import from routes/

services/<name>/service.py
  └── self-contained capability; clean async interface
  └── may not import from routes/ or src/ (no upward deps)
  └── may import from core/ for DB access

core/
  └── foundational infra (DB, auth, middleware, exceptions)
  └── may not import from routes/, src/, or services/
```

### Module size heuristics

These are practical targets based on maintainability evidence, not hard rules:

| Module type | Target | Warning threshold | Action |
|-------------|--------|-------------------|--------|
| Route factory (`setup_*_routes`) | < 300 lines | > 500 lines | Extract to `*_helpers.py` |
| Helper module (`*_helpers.py`) | < 600 lines | > 1000 lines | Split by sub-concern |
| Handler/Manager class | < 500 lines | > 800 lines | Split into focused classes |
| Service module | < 400 lines | > 700 lines | Split into service + helpers |
| Tool submodule (post-split) | 200–500 lines | > 800 lines | Further split by tool family |

These numbers align with the community threshold of ~1000 lines as a practical refactor trigger, with lower targets for files that others import heavily (helpers, services).

### Naming conventions for new split modules (extend existing)

- Route subdirectory: `routes/<domain>/routes.py` + `routes/<domain>/*_helpers.py`
- Tool submodule: `src/tool_implementations/<family>.py`
- Shared helpers within a package: `_shared.py` (leading underscore = module-private convention already in use)
- Back-compat shim file: one-liner `from routes.<domain> import setup_<domain>_routes  # noqa: F401`

---

## Architectural Patterns

### Pattern 1: Package-Facade Split (tool_implementations precedent)

**What:** Convert a large module into a package. The `__init__.py` re-exports all public symbols so existing `from src.tool_implementations import do_*` imports continue to work unchanged.

**When to use:** Any module > 1500 lines that groups multiple unrelated concerns.

**Trade-offs:** Clean boundaries, zero import breakage, fully incremental. Adds a directory level. IDE navigation requires knowing which submodule owns a symbol.

**Example:**
```python
# src/tool_implementations/__init__.py
from .search import do_web_search, do_comprehensive_search
from .email import do_send_email, do_read_email
from .calendar import do_create_event, do_list_events
from .shell import do_shell_command
# ... etc
__all__ = ["do_web_search", "do_send_email", ...]
```

### Pattern 2: Sub-Router Composition (email/cookbook target)

**What:** A `setup_*_routes()` factory creates an outer `APIRouter`, then calls `router.include_router(sub_router)` with sub-routers returned by helper factories. Each sub-router handles one resource group.

**When to use:** Route module > 500 lines with distinct resource groups (e.g., email accounts vs. email messages vs. email sync).

**Trade-offs:** Identical HTTP contract (same prefixes, same tags). Factory nesting is shallow — one level only. More files, but each is focused and independently testable.

**Example:**
```python
# routes/email/routes.py
def setup_email_routes(deps) -> APIRouter:
    router = APIRouter(prefix="/api/email", tags=["email"])
    router.include_router(_account_router(deps))
    router.include_router(_message_router(deps))
    router.include_router(_send_router(deps))
    return router

def _message_router(deps) -> APIRouter:
    r = APIRouter(prefix="/messages")
    @r.get("/{account_id}")
    async def list_messages(...):
        return await message_helpers.list(deps["session_manager"], ...)
    return r
```

### Pattern 3: Extract-and-Delegate (brownfield safety)

**What:** Move functions to new locations first, then update the original file to delegate via imports. Run the test suite between each move. The original file becomes a shim, then disappears.

**When to use:** Any extraction where existing tests exercise the original import path and you cannot update all import sites atomically.

**Trade-offs:** Temporary import indirection during transition. Safe because the suite catches any symbol-loss immediately.

**Example (during transition):**
```python
# src/tool_implementations.py — interim state
# do_web_search moved to search.py; delegate for backward compat
from src.tool_implementations.search import do_web_search  # noqa: F401
# ... remaining functions still live here until their extraction
```

### Pattern 4: Setter-to-Factory DI Migration

**What:** Replace `set_X(singleton)` + module-global with passing `X` as a named argument to `setup_*_routes(deps)`. `initialize_managers()` already returns a dict — just add the new key.

**When to use:** Any module that currently receives its dependencies via `set_*` calls.

**Trade-offs:** Explicit wiring makes startup ordering visible. Route factories become slightly more verbose but are clearly testable with mock deps. No functional behavior change.

**Example:**
```python
# Before
webhook_manager = None
def set_webhook_manager(wm): global webhook_manager; webhook_manager = wm

# After — in initialize_managers()
return {
    ...,
    "webhook_manager": WebhookManager(session_manager),
}

# After — in setup_webhook_routes(deps)
def setup_webhook_routes(deps) -> APIRouter:
    wm = deps["webhook_manager"]
    @router.post("/webhook/register")
    async def register(...): return await wm.register(...)
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Big-Bang Rewrite

**What people do:** Stop all feature work, spend 2-3 sprints reorganizing all files at once.
**Why it's wrong:** Massive diffs are impossible to review; merge conflicts with any in-flight work; test failures are hard to trace to a single cause.
**Do this instead:** One extraction per PR. Each PR is reviewable in 30 minutes. Suite must be green before and after.

### Anti-Pattern 2: Pushing Singletons into `Depends()`

**What people do:** Move `session_manager`, `mcp_manager`, `task_scheduler` from `initialize_managers()` into `async def get_session_manager(request: Request)` `Depends()` chains.
**Why it's wrong:** These are application-lifetime objects; recreating or re-resolving them per request is wasteful. It also defeats the existing `initialize_managers()` pattern that already correctly handles their lifecycle.
**Do this instead:** Keep long-lived singletons in `initialize_managers()` and inject via route factory closure. Reserve `Depends()` for per-request concerns (DB sessions, auth tokens, owner scoping).

### Anti-Pattern 3: Breaking the Import Contract During Extraction

**What people do:** Extract `do_web_search` to `src/tool_implementations/search.py` and immediately delete it from `src/tool_implementations.py` without updating `src/agent_tools.py` and `src/tool_execution.py` in the same commit.
**Why it's wrong:** Import errors emerge at runtime, not test time if test coverage is thin.
**Do this instead:** Always maintain the re-export facade. The facade is the last thing removed, after the suite confirms all callers have been updated.

### Anti-Pattern 4: Skipping Coverage Before Extraction

**What people do:** Refactor `email_routes.py` because it's clearly too big, without adding tests first.
**Why it's wrong:** `CONCERNS.md` documents that `routes/email_routes.py` has no dedicated tests. Behavior regressions (MIME parsing edge cases, comma-in-display-name bugs) will not be caught by a passing suite — because the suite never tested them directly.
**Do this instead:** Characterization tests first. They document the current behavior and will fail immediately if extraction changes it.

### Anti-Pattern 5: Creating Competing Module Conventions

**What people do:** Introduce a new `api/` or `handlers/` layer alongside existing `routes/` and `src/`.
**Why it's wrong:** The codebase has clearly established `routes/` (HTTP) + `src/` (domain logic) + `services/` (capabilities) + `core/` (infra). Adding a competing layer creates confusion and duplicate code paths.
**Do this instead:** Extend the existing layers. New HTTP surfaces go in `routes/`. New business logic goes in `src/`. New capabilities go in `services/`.

---

## Build Order (Phase Dependencies)

The four work streams have ordering dependencies. This is the recommended build order:

```
[1] Coverage baseline + characterization tests
       ↓
[2] tool_implementations → package split
   (prerequisite: coverage for tool_implementations)
       ↓
[3] email_routes → package split
   (prerequisite: coverage for email_routes)
       ↓
[4] cookbook / model / gallery route splits
   (no coverage prerequisite; helpers already exist)
       |
       ↓
[5] DI migration: low-risk setters (set_memory_manager, set_webhook_manager)
   (can run in parallel with 3-4 once initialize_managers() pattern is clear)
       ↓
[6] agent_loop → package split
   (after tool system is stable; avoid concurrent changes)
       ↓
[7] DI migration: set_session_manager (last; highest risk)
   (after all route splits are stable)
```

### Parallelism opportunities

- Steps 3 and 4 can proceed in parallel (different route domains, no shared code).
- Step 5 (DI migration for webhook/memory managers) can proceed in parallel with step 3 if done by a different author.
- Step 6 and step 7 must be sequential with each other and sequential after step 2.

### What must NOT be parallelized

- Never split `tool_implementations` and `agent_loop` in the same sprint — they are tightly coupled through `tool_execution.py` and concurrent edits produce merge conflicts.
- Never migrate `set_session_manager` while `email_routes` is mid-split — both touch the session manager and produce tangled diffs.

---

## Integration Points

### Internal Boundaries (layering enforcement)

| Boundary | Permitted direction | Notes |
|----------|---------------------|-------|
| `routes/` → `src/` | YES | Routes call handlers/managers |
| `routes/` → `services/` | YES | Routes may call services directly |
| `routes/` → `core/` | YES | Auth, DB, exceptions |
| `src/` → `services/` | YES | Handlers call services |
| `src/` → `core/` | YES | Handlers use DB, exceptions |
| `services/` → `core/` | YES | Services use DB |
| `services/` → `src/` | NO | Creates upward dependency; use event/callback |
| `routes/` → `routes/` | NO | Route modules must not cross-import |
| `core/` → anything except stdlib | NO | Core is the foundation; no upward deps |

### Existing facade dependencies (must be preserved)

| Facade | Submodules it aggregates | Who imports it |
|--------|--------------------------|----------------|
| `src/agent_tools.py` | `tool_parsing`, `tool_schemas`, `tool_execution`, `tool_implementations` | `src/agent_loop.py`, MCP servers |
| `services/__init__.py` | `search`, `docs`, `research`, `memory`, `shell` | `routes/*`, `src/*_handler.py` |
| `routes/<domain>_routes.py` (shim) | After split: re-exports `setup_*_routes` | `app.py` only |

---

## Scaling Considerations

Odysseus is a single-host self-hosted deployment. Scaling concerns are irrelevant for its use case. The decomposition work is motivated by maintainability, not throughput. The one genuine operational risk is SQLite concurrency — WAL + busy_timeout in `core/database.py` is separate work from the decomposition but should be done early since it reduces the risk of a silent `OperationalError` being introduced by refactored code that increases write frequency.

---

## Sources

- [FastAPI: Bigger Applications - Multiple Files](https://fastapi.tiangolo.com/tutorial/bigger-applications/) — official sub-router and `include_router` patterns (HIGH confidence)
- [FastAPI: Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/) — `Depends()` scope and override patterns (HIGH confidence)
- [FastAPI Best Practices — zhanymkanov](https://github.com/zhanymkanov/fastapi-best-practices) — module structure, DI, service extraction heuristics (MEDIUM confidence, community-maintained)
- [FastAPI DI without global state — GitHub Discussion #8968](https://github.com/fastapi/fastapi/discussions/8968) — `app.state` + `dependency_overrides` migration stepping-stone (MEDIUM confidence)
- [Python FastAPI DI convention gist — cometkim](https://gist.github.com/cometkim/843e86dd6a37e290e759a310e2d08c72) — `bind_external` setter-migration pattern for incremental brownfield DI transition (MEDIUM confidence)
- `.planning/codebase/ARCHITECTURE.md`, `CONVENTIONS.md`, `CONCERNS.md` — authoritative codebase ground truth (HIGH confidence)
- FastAPI official docs via Context7 `/fastapi/fastapi` — router nesting, `include_router` prefix/deps/tags behavior (HIGH confidence)

---
*Architecture research for: Odysseus safe decomposition — FastAPI modular monolith*
*Researched: 2026-06-03*
