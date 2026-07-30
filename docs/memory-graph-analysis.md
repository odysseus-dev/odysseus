# Memory Graph View — Repository Analysis

Status: research only at the time this document was originally written. No application code, dependencies, or database state was modified to produce it. **Since then, implementation has begun** — see the "Post-implementation addendum" at the end of this document, plus `docs/progress.md`, `docs/todos.md`, and `docs/handoff.md` for current status.

Scope: this document inventories the parts of the Odysseus codebase relevant to building an interactive, Obsidian-like Memory Graph View, and identifies the safest points to extend the system. A companion document, `docs/memory-graph-design.md`, proposes the actual design based on these findings.

## 1. Frontend architecture

- No bundler, no build step. `static/js/package.json` sets `{ "type": "module" }` so the browser loads native ES modules directly. There is no webpack/vite/esbuild config anywhere in the repo. The root `package.json` has no `scripts` block; its only `devDependency` is `@antithesishq/bombadil`, a browser-fuzzing spec library unrelated to bundling.
- Entry point: `static/index.html` loads `static/app.js` via `<script type="module">`. `app.js` statically imports ~30 feature modules, each a plain ES module with a default export object (e.g. `import memoryModule from './js/memory.js?v=20260722memoryloading1'`). Cache-busting is done manually via query-string suffixes on the import path.
- Cross-module reachability: some modules are also hung off `window` (`window.themeModule`, `window.sessionModule`, `window.uiModule`, `window.adminModule`, `window.cookbookModule`) so unrelated modules can call into them without a formal event bus.
- `static/js/` has ~100 flat files plus subfolders for cohesive subsystems: `editor/` (canvas-based image editor), `compare/`, `research/`, `calendar/`, `emailLibrary/`, `markdown/`, `model/`, `color/`, `util/`. `static/js/MODULE_SUMMARY.md` is the authoritative, actively maintained architecture index and should be updated alongside any new module.
- Backend communication is plain `fetch()` returning JSON, plus one Server-Sent Events (SSE) stream for chat (`chat.js` posts to `/api/chat_stream`, reads via `res.body.getReader()`, parses `data: {...}` lines by `type`). No WebSockets exist anywhere in the codebase.
- `app.js` patches `window.fetch` globally so any `401` response redirects to `/login` — any new module's fetches automatically inherit this behavior.

## 2. Routing

- There is no client-side SPA router (no history-based route table, no hash router library). Odysseus is a single persistent DOM (`index.html`) where "navigation" means opening/closing modals and full-screen panels, not swapping views by URL.
- A lightweight deep-link opener exists in `app.js`: a `_routeOpen` map keyed by `window.location.pathname` (entries for `/notes`, `/calendar`, `/cookbook`, `/email`, `/memory`, `/gallery`, `/tasks`, `/library`). The server presumably serves `index.html` for these paths (catch-all), and this map simulates the click that would normally open the corresponding modal. A new `/memory-graph` entry point would follow this exact pattern, or the graph could live as a new tab inside the existing memory modal instead of a new top-level route.
- Everything else (Memory, Calendar, Gallery, Documents, Tasks, Compare, Cookbook, Settings) is a `.modal` element in `index.html` toggled by `modalManager.js`. There is no hash-based view dispatch beyond an ad hoc entity-hash regex in `init.js` used only for composer-restore behavior.

## 3. UI component library / design system

- No component framework (no React/Vue/etc.), no CSS framework (no Tailwind/Bootstrap). `static/style.css` is a single hand-written stylesheet (tens of thousands of lines) using CSS custom properties for theming (`:root { --bg; --fg; --red; ... }`), a `:root.light` override, density variants (`.density-compact`, `.density-spacious`), and a UI-scale zoom mechanism.
- The "component" pattern is plain JS factory functions building DOM via `document.createElement` and manual event wiring — no virtual DOM, no templating engine. `static/js/memory.js` builds each memory-list row this way.
- Modals share a common infrastructure:
  - `static/js/modalManager.js` — central open/minimize/close/dock manager. Public API: `Modals.register(id, { railBtnId, restoreFn, closeFn })`, `Modals.toggle(id)`. Owns a draggable "minimized dock" tray with FLIP animations and magnetic close-on-drag-to-trash behavior. Each modal type has a label+icon entry in `_LABELS`, including an existing `'memory-modal': { label: 'Brain', icon: ... }` entry. **A new Memory Graph View, if its own modal/window, should register here** to get consistent minimize/dock/restore/z-order behavior for free.
  - `static/js/modalSnap.js` — edge-docking/snap-to-zone logic, imported by `modalManager.js`.
  - `static/js/tileManager.js` — desktop window tiling/snap-to-edge, used by `memory.js` for `snapModalToZone`.
  - `static/js/windowDrag.js` — generic `makeWindowDraggable(modal, opts)` helper, used to make the memory modal's header draggable.
  - `static/js/toolWindowZOrder.js` — monotonically increasing z-index (`nextToolWindowZ()`) so the most-recently-focused tool window stacks on top.
- `static/modal-control-variants.html`, `static/wave-variants.html`, `static/whirlpool-variants.html` are standalone design-exploration/prototyping pages, not wired into the live app bundle.
- Third-party libraries that are needed client-side are vendored directly into `static/lib/` (e.g. `docx.umd.min.js`, `highlight.min.js`, `xlsx.full.min.js`) rather than installed via npm — this is the established pattern for adding any graph-rendering library, since there is no bundler to run `npm install` through.

## 4. Existing memory UI

- `static/js/memory.js` (~1550 lines) implements the entire "Brain" modal. No graph, timeline, or relationship view exists today.
- Modal structure (`index.html`): `#memory-modal` with tabs — Browse, Skills, Add, Settings.
- Browse tab is a flat list view (`#memory-list`, `.memory-item` rows), not a card grid. Each row shows text, category badge, pinned badge, source (`auto`/`manual`), use-count, relative timestamp, and a kebab menu (Pin/Select/Edit/Delete).
- Supported interactions: free-text client-side search, category filter chips, sort dropdown (newest/oldest/A-Z/most-used) with pinned items floated to top, bulk multi-select with bulk-delete, inline double-click-to-edit, an AI-driven "Tidy" (dedupe/audit) action, import from file with LLM-extracted suggestion review, and JSON export.
- Categories are a fixed client-side set: `MEMORY_CATEGORIES = ['fact','identity','preference','contact','project','goal','task']`.
- The backend already exposes `GET /api/memory/timeline`, sorted by timestamp with resolved session names, but **no frontend module calls it today** — it is the closest existing "structured" memory endpoint and a plausible seed for a graph-view layout, but it is currently dead code from the UI's perspective.

## 5. Existing graph or visualization components

- None. An explicit search across `static/`, `src/`, `routes/`, and the whole repo for `d3.`, `cytoscape`, `vis-network`, `vis.js`, `sigma.js`, `force-graph`, `d3-force`, `three.js`, and `networkx` returned zero matches.
- The only `<canvas>` usage in the codebase is the image editor (`static/js/editor/*`, `static/js/galleryEditor.js`) for pixel/layer compositing — unrelated to node-link rendering.
- Conclusion: a Memory Graph View is a greenfield UI addition. There is no library, canvas renderer, or "relations" concept in the data model to build on top of.

## 6. Backend architecture

- Framework: FastAPI (`app.py`), served by Uvicorn. Two entrypoints: `app.py` (standard dev/server) and `launcher.py` (Windows portable/frozen build with a tray icon, same Uvicorn server underneath).
- Route registration is manual, not auto-discovered: every `routes/*.py` module exposes a `setup_*_routes(...)` factory that builds and returns an `APIRouter` with dependencies passed as plain constructor args (not FastAPI `Depends()`). `app.py` explicitly imports and `include_router()`s over 40 of these factories.
- Startup uses the modern FastAPI `lifespan` context manager. `src/app_initializer.py::initialize_managers()` is a pure component factory invoked once at startup: it builds `MemoryManager`, `SkillsManager`, `SessionManager`, `UploadHandler`, `PersonalDocsManager`, `APIKeyManager`, `PresetManager`, `MemoryVectorStore` (Chroma-backed, degrades gracefully if unhealthy), wraps memory in a `MemoryProviderRegistry`, and builds `ChatProcessor`/`ChatHandler`/`ModelDiscovery`.
- Route handlers are `async def`, but database access uses classic synchronous SQLAlchemy (`SessionLocal()`), occasionally wrapped in `asyncio.to_thread`. The process is single-instance by convention (log rotation is explicitly not multi-process safe).
- Dependency injection is a manual "component bag" pattern, not `Depends()`: singletons are built once in `app.py`/`app_initializer.py` and closed over by each router factory. `Request.state` carries auth-derived values (`current_user`, `api_token`, etc.) set by middleware and read directly inside handlers.

## 7. API routes

- Auth is enforced by `AuthMiddleware` (inside `app.py`, conditional on `AUTH_ENABLED=true`) plus per-route calls into `src/auth_helpers.py`: `get_current_user(request)` (soft), `require_user(request)` (401 if unauthenticated), `require_privilege(request, "can_manage_memory")` (401/403 by privilege flag), `effective_user(request)` (resolves the real owner behind a Bearer API token).
- Ownership is enforced manually per route via helpers like `_assert_session_owner`/`_verify_memory_owner` in `routes/memory/memory_routes.py`, all raising 404 (not 403) on cross-owner access to avoid confirming another user's resource exists.
- Request/response validation is inconsistent by design across routes: some use Pydantic models from `src/request_models.py` (`MemoryAddRequest`, etc., with lenient field validators that clamp/default rather than hard-reject), others use raw `Form(...)` parameters. Pick per-route based on whether the caller posts JSON or a browser form.
- Error handling is two-layered: routes raise `HTTPException` directly and inline; a small set of domain exceptions (`SessionNotFoundError`, `InvalidFileUploadError`, `LLMServiceError`, `WebSearchError`) are registered globally in `core/exceptions.py` / `app.py` with fixed JSON shapes and status codes.
- Rate limiting (`src/rate_limiter.py`) is a simple in-memory sliding-window limiter, wired only into `routes/auth_routes.py` (login/setup) — there is no global rate-limit middleware.
- Pagination has no single shared convention. History uses offset/limit with a "default to most recent page" fallback; documents use `Query(0, ge=0)`/`Query(20, ge=1, le=50)`; **memory routes have no pagination at all** — `GET /api/memory` and `/timeline` return the full owner-scoped list every time. A graph endpoint returning potentially hundreds of nodes/edges will need new pagination/limiting that doesn't exist in the memory API today.

## 8. Database schema

- Engine: SQLite by default (`DATABASE_URL=sqlite:///{DATA_DIR}/app.db`), synchronous SQLAlchemy declarative ORM. A custom `EncryptedText` `TypeDecorator` transparently Fernet-encrypts sensitive columns at the ORM layer.
- Migrations: no Alembic. Hand-rolled, idempotent `_migrate_add_*` functions run in a fixed sequence from `init_db()`, each checking `PRAGMA table_info(table)` before `ALTER TABLE ... ADD COLUMN`, safe to re-run every startup. `init_db()` also calls `Base.metadata.create_all(bind=engine)` for any brand-new tables.
- Key tables (all in `core/database.py`): `sessions`, `chat_messages`, `memories` (see discrepancy below), `documents`/`document_versions`, `gallery_albums`/`gallery_images`, `notes`, `calendars`/`calendar_events`/`caldav_deleted_events`, `email_accounts`, `scheduled_tasks`/`task_runs`, plus supporting tables (`model_endpoints`, `api_tokens`, `mcp_servers`, `comparisons`, `signatures`, `webhooks`, `user_tools`, `crew_members`, `editor_drafts`, `integrations`).
- Users/auth are **not** in the SQL database — `core/auth.py::AuthManager` reads/writes `data/auth.json`. Ownership on DB rows is a plain `owner` string column (username), not a foreign key to a `users` table; `NULL` conventionally means "legacy/shared, visible to everyone."
- **Important discrepancy**: a `memories` SQL table exists with proper schema and a `session_id` FK, but the runtime memory store actually used everywhere (routes, chat context injection, agent tool) is `src/memory.py::MemoryManager`, which persists to a flat JSON file `data/memory.json`, not this table. The SQL `memories` table appears to be unused/legacy. Any Memory Graph View must treat `memory.json` (+ its Chroma vector index) as the real source of truth, not the SQL table.

## 9. Memory system

- Canonical storage: `src/memory.py::MemoryManager` — a JSON file (`data/memory.json`) holding flat entries: `id`, `text`, `category` (default `"fact"`), `source`, `owner`, `timestamp`, optional `session_id`, `pinned`, `uses`. **There is no relationship/edge field** — no `related_ids`, `links`, or similar. Any graph view must derive edges rather than read stored ones.
- `services/memory/memory.py` and `services/memory/memory_vector.py` are thin backward-compatibility shims re-exporting the canonical `src/memory.py` / `src/memory_vector.py` implementations.
- Backend routes (`routes/memory/memory_routes.py`, prefix `/api/memory`; `routes/memory_routes.py` is a compat shim re-exporting the same router object): `POST /add`, `GET ""` (list), `POST /search`, `GET /timeline`, `GET /by-session/{session_id}`, `POST /extract` (LLM suggestion extraction from a chat session), `POST /audit` (dedupe), `POST /import` (file-based extraction), `POST /{id}/pin`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}`.
- Memory-to-session relationship is informational provenance only (`session_id` records which chat session a memory was extracted from) and is not a structural graph edge.
- Two independent paths connect memory to the agent/chat pipeline:
  1. **Automatic read path** — `src/chat_processor.py::ChatProcessor.build_context_preface(...)` runs on every chat/agent turn when `use_memory=True` (default): loads the owner's memories, selects relevant pinned + hybrid-retrieved (BM25 + vector) memories up to a context limit, injects them as untrusted-context messages before the LLM ever sees the turn, and bumps each injected memory's `uses` counter.
  2. **Explicit write path** — the model can emit a `manage_memory` tool call (`src/ai_interaction.py::do_manage_memory`), supporting `list|add|edit|delete|search`, mutating `MemoryManager` + `MemoryVectorStore` directly and firing a `memory_added` event.
- `src/memory_provider.py` defines a `MemoryProvider` ABC / `MemoryProviderRegistry` intended to let alternate memory backends plug in behind the same interface; only `NativeMemoryProvider` is currently registered. This is the natural extension seam if a graph-capable provider is ever needed, though the design in this analysis targets the native provider directly since that's what all current UI and agent paths use.

## 10. ChromaDB integration / vector search

- `src/chroma_client.py` uses `chromadb.HttpClient(host, port)` — a real HTTP client against a **separate `chromadb` container**, not an embedded/local persistent client. Config: `CHROMADB_HOST` (default `localhost`), `CHROMADB_PORT` (default `8100`); docker-compose sets these to `chromadb`/`8000` for in-network container-to-container access. A TCP probe fails fast before constructing the client; `client.heartbeat()` verifies liveness before the singleton is cached.
- Collection names: `odysseus_memories` (`src/memory_vector.py::MemoryVectorStore.COLLECTION_NAME`) and `odysseus_rag` (`src/rag_vector.py`). Both are created lazily through `build_embedding_lanes()` → `chroma_client.get_or_create_collection(...)` (`src/embedding_lanes.py`), which supports multiple simultaneous embedding backends ("lanes": local FastEmbed ONNX, or a configured custom embedding endpoint), each with its own Chroma sub-collection, searched in parallel and de-duplicated.
- `MemoryVectorStore` exposes `add(memory_id, text)`, `remove(memory_id)`, `search(query, k)`, and a `healthy` flag. This is the natural, already-available source of pairwise similarity scores for auto-generating graph edges between memories, since the memory schema itself has no explicit relation data.
- Memories and personal-doc RAG chunks live in separate Chroma collections and are not cross-linked or queried together today.

## 11. Document storage

- Two distinct subsystems:
  - "Living documents" (AI-editable canvas documents): `routes/document_routes.py`, backed entirely by the relational DB (`documents`/`document_versions` tables) — content lives in `Text` columns, not on disk. `Document.owner` is stamped independently of `session_id` so a document survives its parent session's deletion.
  - "Personal Docs" (RAG document library): `src/personal_docs.py::PersonalDocsManager` walks a directory on disk, extracts text (PDF via `pypdf`, Office via `markitdown`), chunks it, and indexes into the `odysseus_rag` Chroma collection. Files stay on disk; there is no separate SQL metadata table — the Chroma collection's metadata is the catalog.
- Not directly part of the Memory Graph View's data model, but a candidate future edge type ("memory ⟷ document it was extracted from") if the design is extended later.

## 12. Conversation storage

- Sessions and messages are relational DB rows (`sessions`, `chat_messages`), managed exclusively through `core/session_manager.py::SessionManager`.
- `SessionManager` keeps an in-memory cache but only loads the 100 most-recently-accessed, non-archived, non-empty sessions' metadata at boot (not messages) to bound memory; message history is hydrated on demand.
- `routes/history/history_routes.py` supports offset/limit paging directly against `chat_messages`, independent of the in-memory cache, and lazily "hydrates" a session's in-RAM history from the DB if it's found to be behind.
- `sessions.owner` (nullable username string) is the ownership authority that memory, documents, and other per-session resources ultimately trace back to via their own `owner` column or `session_id` FK.

## 13. Agent architecture

- `src/agent_loop.py::stream_agent_loop(...)` is the central async-generator agent loop: streams SSE events (`delta`, `tool_start`, `tool_output`, `agent_step`, `metrics`, `[DONE]`), assembles a dynamic system prompt, and handles plan-mode, tool policies, and per-model quirks.
- `src/tool_execution.py` dispatches model-emitted tool calls either to MCP servers (`src/mcp_manager.py`) or native Python implementations under `src/agent_tools/`. Sensitive filesystem tools are admin-only and path-confined via deny/allow lists.
- `src/agent_runs.py` lets an SSE stream survive a browser disconnect by draining the generator server-side into a replay buffer per session id (does not survive a server restart) — this is the closest existing pattern to a "live push channel," and is the template to follow if the graph view needs live updates (see §14).
- Memory interacts with the agent loop via the two paths described in §9 (automatic context injection, explicit `manage_memory` tool), not via any structural graph traversal today.

## 14. Real-time/event infrastructure

- `src/event_bus.py::fire_event(event_name, owner)` is a **task-automation trigger bus**, not a pub/sub-to-frontend mechanism. It matches `ScheduledTask` rows with `trigger_type == "event"` against the fired event name and runs the matching automation once a threshold count is hit. It has no subscriber API that a browser tab could listen to.
- Publishers of `memory_added`: `routes/memory/memory_routes.py` (on add), `services/memory/memory_extractor.py`, `src/ai_interaction.py` (memory captured during chat). The only built-in consumer is the "Memory Tidy" automation (fires a `consolidate_memory` task every 5 adds). There is currently no `memory_updated` or `memory_deleted` event fired anywhere.
- Genuine per-session pub/sub does exist, but scoped to a single request's own output stream: `routes/chat_routes.py` + `src/agent_runs.py` implement `AgentRun.subscribers` as a `set` of `asyncio.Queue` (one per connected client), fed into a `StreamingResponse(media_type="text/event-stream")`. Similar SSE streaming exists in `routes/shell_routes.py`, `routes/model_routes.py`, `routes/research/research_routes.py`.
- Implication: there is no ready-made mechanism today to push "a memory changed elsewhere" to an open Memory Graph View. The design doc proposes either polling or a new lightweight per-owner SSE channel modeled directly on the `agent_runs.py` queue-per-subscriber pattern.

## 15. Authentication

- `core/auth.py::AuthManager` is JSON-file-backed (`data/auth.json` for users, `data/sessions.json` for session tokens), not a DB table.
- Two callable-facing auth mechanisms, both resolved in `AuthMiddleware` (in `app.py`, gated by `AUTH_ENABLED=true`):
  - Cookie session auth: bcrypt-password-gated, 7-day TTL, sets `request.state.current_user` to the resolved username.
  - Bearer API-token auth (`Authorization: Bearer ody_...`): matched against bcrypt-hashed `ApiToken` rows with scopes (`routes/api_token_routes.py` already defines `memory:read`/`memory:write` scopes); sets `request.state.current_user = "api"` plus `api_token_owner`/`api_token_scopes`.
  - An internal-tool loopback path lets the agent's own HTTP tool calls reach admin-gated routes via `X-Odysseus-Internal-Token` / `X-Odysseus-Owner`.
- Multi-user support is real: `AuthManager.users` is a dict keyed by lowercase username, with admin-gated create/delete/rename, and reserved usernames that can never be created (`internal-tool`, `api`, `demo`, `system`).
- Privilege model: `DEFAULT_PRIVILEGES` includes `can_manage_memory` among others (`can_use_agent`, `can_use_documents`, `can_use_bash`, etc.). Admins get all privileges unconditionally. **Read-only memory routes (`GET /api/memory`, `/timeline`) do not require `can_manage_memory`** — only mutation (add/import) does. Per `THREAT_MODEL.md`, memory management is explicitly listed as available to both admins and non-admins, unlike shell/email/MCP/tokens/settings which are admin-only.
- Ownership pattern to reuse for a graph endpoint: resolve `owner = get_current_user(request)` (or `effective_user(request)` for Bearer-token callers), filter the memory load by that owner, and re-verify ownership on any caller-supplied id via the existing `_verify_memory_owner` helper rather than trusting the id in isolation.

## 16. Docker configuration

- Two-stage `Dockerfile` on `python:3.14-slim`, final image installs `build-essential, cmake, curl, git, nodejs, npm, chromium, tmux, openssh-client, gosu, libgl1, ...` plus the static Docker CLI (no daemon; host Docker socket is bind-mounted separately if enabled). Exposes port 7000; default CMD is `uvicorn app:app --host 0.0.0.0 --port 7000`, wrapped by `docker/entrypoint.sh`.
- `docker-compose.yml` composes four services:
  - `odysseus` — the app container; volumes for `data`, `logs`, `.ssh`, `.cache/huggingface`, `.local`; huge environment block (LLM endpoints, embeddings, auth flags, upload limits, PUID/PGID); `depends_on: searxng (healthy), chromadb (started)`.
  - `chromadb` — **separate container**, image `chromadb/chroma:latest`, bound `127.0.0.1:8100:8000` on the host, named volume `chromadb-data`, `ANONYMIZED_TELEMETRY=FALSE`. No healthcheck (app relies on its own TCP probe + `heartbeat()` instead).
  - `searxng` — pinned version, custom entrypoint templating `settings.yml`, healthcheck via Python urlopen, minimal Linux capabilities (`cap_drop: ALL` + a small `cap_add` set).
  - `ntfy` — push-notification relay, bound `127.0.0.1:8091:80`.
- `docker/entrypoint.sh` implements the standard PUID/PGID drop-privilege pattern (create/reuse group+user matching host `PUID`/`PGID`, chown a bounded set of data directories, then `exec gosu $ODY_USER "$@"` so signals reach uvicorn directly).
- GPU overlays (`docker/gpu.nvidia.yml`, `docker/gpu.amd.yml`) and an opt-in `docker/host-docker.yml` (mounts the Docker socket) are pure compose overlays, not relevant to the Memory Graph View itself but relevant if it ever needs a background job (e.g. embedding recompute) that benefits from GPU access.
- Nothing about the graph feature requires new Docker services: no new database, no new container. It rides on the existing `odysseus` app container and the existing `chromadb` container.

## 17. Testing framework

- Backend: pytest, configured in `pyproject.toml` (`testpaths=["tests"]`, `asyncio_mode="auto"`, a fixed set of `area_*` taxonomy markers registered in `tests/_taxonomy.py`).
- `tests/conftest.py` is intentionally thin (repo convention: "prefer explicit local setup over hidden global fixtures", per `tests/README.md`). It forces `DATABASE_URL=sqlite:///:memory:` before any `core.database` import, pre-imports modules so later per-file mocking doesn't poison the real ORM for other tests, and stubs a fixed list of optional third-party deps if not installed. There is no shared `TestClient`/seeded-DB fixture.
- Route-testing convention (seen in `tests/test_memory_owner_isolation.py`, `tests/test_memory_routes_session_owner.py`): tests do not spin up a FastAPI `TestClient`/ASGI app. Instead they call the `setup_*_routes(...)` factory directly with real or `MagicMock()` dependencies, look up the target endpoint function off `router.routes` by path+method, and call it directly with a hand-built `Request` stand-in (`SimpleNamespace(state=SimpleNamespace(current_user=...))`). Auth is bypassed by monkeypatching `get_current_user`/`require_user`/`require_privilege` directly on the route module. Ownership tests assert `HTTPException(404)` on cross-owner access and that returned payloads only contain the caller's own data.
- `tests/helpers/` provides `sqlite_db.make_temp_sqlite` and `db_stubs.make_core_db_stub` for tests needing a real file-backed SQLite DB. `tests/run_focus.py` supports marker-based selective runs (e.g. `-m area_routes`).
- Frontend: no Jest/Vitest/Mocha/Playwright/Cypress. Pure-logic JS files are unit-tested via Node's built-in `node:test` runner, invoked as a subprocess from a thin pytest wrapper (e.g. `tests/test_streaming_segmenter_js.py` shells out to `node --test tests/streaming/*.test.mjs`), or via a `vm.createContext()` sandbox that string-shims `import`/`export` out of a production file before running it headlessly (`tests/markdown_codefence_placeholder_regression.mjs`). `tests/bombadil-spec.ts` is a separate fuzzing/property spec (Antithesis-style) for full-app exploration, not part of the normal pytest/CI gate. CI (`.github/workflows/ci.yml`) runs `node --check` (syntax only) over all frontend JS, plus `pytest -q` for everything else.
- Implication for the Memory Graph View: any pure-JS graph-layout/edge-derivation logic should get a `.test.mjs` file run via `node --test`, wrapped by a thin pytest shim, following the existing pattern. DOM/rendering behavior remains manually verified against the running app — there is no automated DOM test harness in this repo today.

## 18. Security posture (relevant to a new data-exposing endpoint)

- `THREAT_MODEL.md` frames Odysseus as a privileged local-access console for trusted users on a private network, not a public multi-tenant SaaS — but multi-user ownership isolation is still a real, tested boundary (see `tests/test_memory_owner_isolation.py`).
- `core/middleware.py`'s `SecurityHeadersMiddleware` already sets `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, HSTS (when HTTPS), and a nonce-based CSP (`script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net`) on all normal routes. A new graph view page doesn't need a special CSP branch unless it renders inline HTML/iframe content the way the visual-report/tool-render pages do.
- CORS is configured via `CORSMiddleware` with `allow_origins` from `ALLOWED_ORIGINS` (default `http://localhost,http://127.0.0.1`) and `allow_credentials=True`.
- Known gaps documented in `THREAT_MODEL.md` (no shell/filesystem sandbox for agent tools, an SSRF gap in chat `base_url`, coarse API token scopes) are not directly implicated by a read-only graph endpoint, but the "coarse token scopes" gap matters if the graph is ever exposed over a Bearer API token — it should reuse the existing `memory:read` scope rather than inventing a new one.

## 19. Safest extension points, summarized

Ranked from safest/most isolated to most invasive:

1. **New read-only backend route** `GET /api/memory/graph` inside the existing `routes/memory/memory_routes.py` module (or a new `routes/memory/memory_graph_routes.py` included the same way), reusing `_owner(request)` scoping and the existing `MemoryManager`/`MemoryVectorStore` singletons already wired up in `app_initializer.py`. No new tables, no new services, no new containers.
2. **New frontend module** `static/js/memoryGraph.js`, registered with `Modals.register(...)` the same way `memory-modal` already is, either as a new tab inside the existing Brain modal or a sibling modal reachable from it. No changes to `app.js`'s routing map are strictly required if it's a tab; one new `_routeOpen` entry if it's a standalone deep-linkable view.
3. **A vendored graph-rendering library** dropped into `static/lib/`, following the existing vendoring convention (no npm/bundler involvement).
4. **Optional: a new SSE channel** for live updates, modeled directly on `src/agent_runs.py`'s per-subscriber `asyncio.Queue` pattern, with new `memory_updated`/`memory_deleted` `fire_event()` calls added at the existing pin/update/delete call sites. This is additive and does not touch the existing task-automation consumer of `memory_added`.
5. **Not recommended as a first step**: touching the dormant SQL `memories` table, since it is not the runtime source of truth today and reconciling it would be a separate, larger migration unrelated to shipping a graph view.

No area inspected requires a new database engine, a schema migration to an existing hot-path table, or a new Docker service.

## 20. Post-implementation addendum

Everything above was written before any code existed. Implementation has since started (Milestones 1–2 of `docs/memory-graph-design.md`); this section records what that process confirmed, corrected, or added to the picture above. Full detail lives in `docs/progress.md` / `docs/handoff.md` — this is a short pointer, not a duplicate.

- **§3/§19 confirmed in practice**: the "tab inside the Brain modal" recommendation was *not* what got built — the user's Milestone 2 instructions explicitly asked for a dedicated top-level nav item and a dedicated page, which is what exists now (a new `#tool-memory-graph-btn` in the sidebar Tools section, opening its own `memory-graph-modal`). Worth knowing if you re-read the original §19 recommendation and wonder why it doesn't match the code.
- **§3 correction**: the "Brain"/"Calendar" modals looked, from `index.html`'s static markup, like they might be simple declarative panels. In practice their real open/close/register logic lives in `app.js` (Brain) or the module itself (`calendar.js`'s `_getModal()`), and `calendar.js`'s pattern — lazily build the modal element in JS, append to `document.body` once, register with `modalManager.js`'s `Modals.register(...)` — turned out to be the cleanest, most self-contained template to copy for a brand-new modal, requiring zero edits to `app.js`'s older hardcoded Escape-key modal-id arrays. `memoryGraph.js` follows this template exactly.
- **§17 (testing framework) confirmed useful**: the "call the endpoint function directly" convention was followed for all new route tests, plus one deliberate exception — a real `fastapi.testclient.TestClient` test for the route-ordering fix specifically, because that convention *cannot* catch an ordering bug (it never exercises actual Starlette path matching). See `tests/test_memory_graph_route_ordering.py`.
- **New finding, not in the original analysis**: `src/agent_loop.py` has a pre-existing bug (missing `from typing import Any`) that hard-crashes `app.py` at import time in this sandbox's Python 3.11/3.14 environment. Confirmed via `git stash` to reproduce identically on a clean `dev` checkout — unrelated to this feature, but blocking enough that it had to be patched locally just to launch the app for a demo. See `docs/handoff.md` → Risks for what to do about it.
- **New finding, not in the original analysis**: the local ChromaDB instance's collections (`odysseus_memories`, `odysseus_rag`) are shared/global regardless of which `ODYSSEUS_DATA_DIR` the app process points at — only the JSON file store and the SQL `owner` column are per-deployment/per-owner. This matters for anyone manually testing against a real Chroma instance: seeded test data must be cleaned up via the real `DELETE` endpoints (which correctly call `memory_vector.remove()`), not just by discarding a scratch data directory.
