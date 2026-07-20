# Architecture Runtime Inventory

Last updated: dev@e57f60b | 2026-07-20

> Purpose: current runtime/module inventory for codebase readability work
> originally discussed around #4071/#4082. This is a source snapshot, not a
> refactor plan. Recompute metrics against current `dev` before treating any
> count as authoritative.

This document maps the current runtime module structure, high-risk boundaries,
and behavior-preserving refactor candidates. It does not move files, change
imports, or alter runtime behavior.

## Current Structure

```
odysseus/
├── app.py                    # FastAPI app entrypoint and route registry
├── conf/                     # Configuration helpers
├── core/                     # database, auth, middleware, session helpers
├── routes/                   # HTTP routes plus selected domain subpackages
│   ├── contacts/             # canonical contacts/CardDAV route package
│   ├── gallery/              # canonical gallery route/helper package
│   ├── history/              # canonical chat history route package
│   ├── memory/               # canonical memory route package
│   ├── note/                 # canonical notes/reminders route package
│   └── research/             # canonical research route package
├── src/                      # agent/model/runtime services and facades
│   ├── agent_tools/          # native tool handler classes
│   ├── model_capability_readers/ # provider model-metadata normalization
│   ├── search/               # compatibility aliases for services.search
│   └── tools/                # split do_* tool implementation domains
├── services/                 # service facades and canonical search/youtube paths
├── mcp_servers/              # built-in MCP server implementations
├── scripts/                  # local CLI tools and one-shot scripts
├── static/                   # no-build browser SPA
├── tests/                    # pytest, Node, and static source-shape tests
└── specs/                    # implementation-truth notes
```

### Directory Flatness

| Directory | Flat `.py` Files | Subdirectories | Current Concern |
|-----------|------------------|----------------|-----------------|
| `src/` | 100 | `agent_tools/`, `model_capability_readers/`, `search/`, `tools/` | Still broad, but tool handlers, capability readers, and do_* implementations now have packages. |
| `routes/` | 54 | `contacts/`, `gallery/`, `history/`, `memory/`, `note/`, `research/` | Route grouping has started; most domains remain flat top-level route files. |
| `core/` | 11 | none | Manageable count, but `database.py` remains oversized and highly imported. |

## Largest Runtime Modules

### Python Backend

| File | Lines | Notes |
|------|-------|-------|
| `routes/email_routes.py` | 5,226 | Largest HTTP domain; route, cache, compose, OAuth, and mutation behavior. |
| `src/agent_loop.py` | 4,529 | Agent orchestration, tool rounds, prompt/context assembly, recovery. |
| `routes/cookbook_routes.py` | 4,386 | Cookbook setup/download/serve/state flows. |
| `src/llm_core.py` | 2,869 | Provider payloads, streaming, fallbacks, provider quirks. |
| `src/builtin_actions.py` | 2,776 | Scheduler/background built-in action helpers. |
| `routes/model_routes.py` | 2,657 | Endpoint CRUD, probing, catalog cache, provider auth links. |
| `src/task_scheduler.py` | 2,627 | Task runner, runs, chained/event/webhook execution. |
| `core/database.py` | 2,562 | SQLAlchemy models plus manual SQLite migrations. |
| `routes/gallery/gallery_routes.py` | 1,966 | Canonical gallery/media route package. |
| `routes/note/note_routes.py` | 937 | Canonical notes/reminders route package. |
| `routes/contacts/contacts_routes.py` | 916 | Canonical contacts/CardDAV route package. |
| `routes/research/research_routes.py` | 783 | Canonical research route package. |
| `routes/history/history_routes.py` | 794 | Canonical chat history route package. |
| `routes/memory/memory_routes.py` | 552 | Canonical memory route package. |
| `src/tool_implementations.py` | 115 | Compatibility facade over `src/tools/*` and admin tool handlers. |

### Frontend

| File | Lines | Notes |
|------|-------|-------|
| `static/style.css` | 40,453 | App-wide CSS remains the largest frontend risk. |
| `static/js/document.js` | 11,038 | Large document editor/library coordinator. |
| `static/js/emailLibrary.js` | 7,784 | Email library UI and cache behavior. |
| `static/js/settings.js` | 5,795 | Settings modal and provider/admin-adjacent wiring. |
| `static/js/chat.js` | 5,457 | Main chat streaming/UI coordinator. |
| `static/app.js` | 4,389 | SPA orchestration and compatibility bridges. |

## Import Dependency Snapshot

| Relationship | Count | Notes |
|--------------|-------|-------|
| `core.database` importers | 118 | Highest-risk split target; routes, services, tests, and helpers depend on it. |
| `src.tool_implementations` importers | 22 | Still a live facade even after the tool split. |
| `src.agent_loop` importers | 32 | Agent loop is an orchestration hub. |
| `src/` import lines referencing `routes` | 36 | Mostly function-local compatibility/runtime coupling. |
| `routes/` import lines referencing `src` | 391 | Expected route-to-service direction. |
| `routes/` import lines referencing `core` | 139 | Expected DB/auth/session dependencies. |

Recompute examples:

```bash
find src -maxdepth 1 -name '*.py' | wc -l
find routes -maxdepth 1 -name '*.py' | wc -l
find tests -name 'test_*.py' | wc -l
wc -l app.py core/database.py src/agent_loop.py src/tool_implementations.py
rg -l '(^| )from core.database|(^| )import core.database' --glob '*.py' | wc -l
rg -n '(^| )from routes|(^| )import routes' src --glob '*.py' | wc -l
```

## Route Ownership Map

Route modules are still mostly flat, with six landed domain packages:

- `routes/gallery/gallery_routes.py` and `routes/gallery/gallery_helpers.py`
  are canonical. `routes/gallery_routes.py` and `routes/gallery_helpers.py`
  are compatibility shims that replace their `sys.modules` entries with the
  canonical module object.
- `routes/memory/memory_routes.py` is canonical. `routes/memory_routes.py` is
  a compatibility shim.
- `routes/research/research_routes.py` is canonical. `routes/research_routes.py`
  is a compatibility shim.
- `routes/history/history_routes.py` is canonical. `routes/history_routes.py`
  is a compatibility shim.
- `routes/contacts/contacts_routes.py` is canonical.
  `routes/contacts_routes.py` is a compatibility shim.
- `routes/note/note_routes.py` is canonical. `routes/note_routes.py` is a
  compatibility shim.

Other major domains remain top-level route modules:

| Domain | Primary Route Files | Current Risk |
|--------|---------------------|--------------|
| Email | `email_routes.py`, `email_helpers.py`, `email_pollers.py` | High: largest route surface and many side tables/caches. |
| Chat / Agent | `chat_routes.py`, `chat_helpers.py`, `shell_routes.py`, `codex_routes.py`, `skills_routes.py` | High: cross-cuts sessions, tools, research, compare, uploads. |
| Cookbook | `cookbook_routes.py`, `cookbook_helpers.py`, `cookbook_output.py` | Medium-high: code execution, SSH, model serving, state. |
| Model / LLM | `model_routes.py`, `assistant_routes.py`, `copilot_routes.py`, `chatgpt_subscription_routes.py` | Medium-high: secrets, endpoint ownership, provider auth. |
| Calendar / Contacts | `calendar_routes.py`, `contacts/contacts_routes.py` plus shim | Medium: remote sync/writeback and credential handling. |
| Documents | `document_routes.py`, `document_helpers.py`, `personal_routes.py`, `upload_routes.py` | Medium: files, ownership, optional renderers, RAG. |
| Auth / Admin | `auth_routes.py`, `api_token_routes.py`, `backup_routes.py`, `diagnostics_routes.py`, `admin_wipe_routes.py` | Medium: security-critical but more modular. |

## Tool Registry And Implementation Boundaries

Tool execution is no longer concentrated in one 4k-line module.

| Component | Owner | Role |
|-----------|-------|------|
| Native handler registry | `src/agent_tools/__init__.py` | Maps native tool names to handler classes for bash/python/web/files/documents/interaction/model/session/background/admin tools. |
| Low-level native handlers | `src/agent_tools/*.py` | Filesystem, subprocess, web, document, interaction, model interaction, background job, session, and admin handler classes. |
| Domain do_* implementations | `src/tools/*.py` | Calendar, contacts, Cookbook, image, notes, research, search, system, and vault do_* functions. |
| Compatibility facade | `src/tool_implementations.py` | Re-exports old do_* names and lazy-loads admin do_* symbols so legacy imports and tests keep working. |
| Tool schemas | `src/tool_schemas.py` | Native OpenAI-style schemas and native-call conversion. |
| Tool retrieval | `src/tool_index.py` | Built-in and MCP tool retrieval text/indexing. |
| Tool parsing | `src/tool_parsing.py` | Prompted/fenced tool-call parsing and aliases. |
| Tool execution gates | `src/tool_execution.py` | Dispatch, path confinement, admin/non-admin gates, MCP dispatch, truncation, and UI formatting. |

Current `src/tools/*` do_* domain counts:

| Domain File | Count |
|-------------|-------|
| `calendar.py` | 1 |
| `contacts.py` | 2 |
| `cookbook.py` | 13 |
| `image.py` | 1 |
| `notes.py` | 1 |
| `research.py` | 2 |
| `search.py` | 1 |
| `system.py` | 4 |
| `vault.py` | 3 |

Admin manage tools for endpoints, MCP, webhooks, tokens, and settings live in
`src/agent_tools/admin_tools.py` and are registered through `ADMIN_TOOL_HANDLERS`.

## Risk Ranking For Future Refactors

| Priority | Target | Risk | Notes |
|----------|--------|------|-------|
| 1 | Remaining route domains into packages | Medium | Do one domain per PR with `sys.modules` compatibility shims and route-import tests. |
| 2 | `src/agent_loop.py` submodules | Medium-high | Extract prompt/context assembly, classification, verification/recovery, and stream-round helpers without changing behavior. |
| 3 | Email route/service split | High | Valuable but risky because account ownership, IMAP cache, side DBs, OAuth, and compose/send are intertwined. |
| 4 | Cookbook route/service split | Medium-high | Preserve command validation, shell/SSH boundaries, and state semantics. |
| 5 | `core/database.py` model/migration split | High | Most imported module; should be late and shim-heavy. |
| 6 | Frontend CSS/large coordinator splits | Medium | Requires browser/module-order verification, not just source movement. |

Already-landed structure that should not be treated as future work:

- `src/tool_implementations.py` has already been split behind a facade.
- Gallery, research, memory, history, contacts, and note route packages already have canonical subpackage
  locations plus top-level compatibility shims.

## Safety Guardrails For Follow-Up Work

- One domain/slice per PR.
- No behavior changes mixed with file moves.
- Keep compatibility shims for existing import paths until all call sites and
  tests are intentionally migrated.
- Add import-parity tests for every moved module.
- Validate with focused tests for the moved domain plus compile checks.
- Do not start with `core/database.py` unless the change is a small migration or
  helper extraction with dedicated tests.
- Avoid packaging/runtime/tooling migration inside route or module moves.

## Validation Commands

```bash
python -m compileall app.py core routes src conf services
python -m pytest tests/ -x -q
python -c "from src.tool_implementations import do_search_chats; print('tool facade OK')"
python -c "import routes.gallery_routes as g; import routes.gallery.gallery_routes as c; print(g is c)"
python -c "import routes.memory_routes as m; import routes.memory.memory_routes as c; print(m is c)"
python -c "import routes.research_routes as r; import routes.research.research_routes as c; print(r is c)"
python -c "import routes.history_routes as h; import routes.history.history_routes as c; print(h is c)"
python -c "import routes.contacts_routes as c0; import routes.contacts.contacts_routes as c1; print(c0 is c1)"
python -c "import routes.note_routes as n; import routes.note.note_routes as c; print(n is c)"
```
