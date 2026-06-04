# Runtime

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers current app runtime wiring in:

- `app.py`;
- `src/app_initializer.py`;
- `src/config.py`;
- `core/constants.py`;
- `src/constants.py`;
- `core/middleware.py`;
- all `routes/*_routes.py` setup functions registered from `app.py`;
- `routes/note_routes.py`, `routes/prefs_routes.py`, `routes/workspace_routes.py`, and `companion/routes.py`;
- `src/generated_images.py` for generated-media file resolution;
- static entrypoints in `static/index.html`, `static/login.html`, and `static/app.js`.

## App Orchestrator

`app.py` owns process-level startup and HTTP composition. It configures MIME types, `.env` loading, CORS, auth middleware, request timeout middleware, static files, generated-image serving, router registration, SPA HTML routes, health/readiness/runtime endpoints, and lifespan hooks. `core/middleware.py` owns security headers, admin helpers, and internal-tool token constants.

`src/app_initializer.initialize_managers()` owns shared manager construction. It creates memory, skills, sessions, uploads, personal docs, API keys, presets, chat processor/handler, research handler, model discovery, and optional memory vector store. Route modules receive these dependencies from `app.py`; they should not recreate manager singletons.

`app.py` separately owns runtime singletons and integration hooks for auth, vector RAG, TTS/STT, webhooks, scheduled tasks, MCP, assistant log globals, event bus wiring, AI interaction globals, and API-token cache invalidation. `core/constants.py` and `src/constants.py` are both live import paths and are not fully identical today, so new constants need explicit placement/compatibility decisions.

## Routes And Static Serving

Current router call sites include:

- auth, uploads, emoji, sessions, admin wipe, memory, skills, chat, workspace, research, history, search, presets, diagnostics, cleanup, personal docs, embeddings, model endpoints;
- TTS/STT, documents, signatures, gallery, editor drafts, scheduled tasks, assistant, calendar, shell, Cookbook, HW Fit, compare, preferences, backup, fonts, Copilot auth;
- MCP, webhooks, API tokens, notes, email, Codex/Claude scoped APIs, vault, contacts, and companion routes.

The SPA routes `/`, `/notes`, `/calendar`, `/cookbook`, `/email`, `/memory`, `/gallery`, `/tasks`, and `/library` all serve `static/index.html`. `static/` is served with revalidation for `.js`, `.css`, and `.html` because the frontend ships raw browser modules with no hashed build output.

Direct app-owned endpoints include `/api/generated-image/{filename}`, `/backgrounds`, `/login`, `/api/version`, `/api/health`, `/api/ready`, and `/api/runtime`. `/backgrounds` points at `static/backgrounds.html`; if that file is absent or the route remains auth-gated, that is route/static drift rather than an intentional public contract.

`/static/*` is auth-exempt and public. SPA HTML routes are auth-gated except `/login`, and they are nonce-injected dynamic `HTMLResponse` values outside the static mount. Generated images and videos are served from `data/generated_images` through the generated-image resolver with immutable/nosniff caching.

## Runtime Security Boundaries

Effective middleware order matters. CORS, `SecurityHeadersMiddleware`, and `_RequestTimeoutMiddleware` are added before `AuthMiddleware`; auth short-circuit responses can therefore bypass downstream app handlers and should be tested when changing response headers or auth behavior.

`_TIMEOUT_EXEMPT_PREFIXES` owns hard-timeout bypass policy. It is prefix-based and currently exempts all subroutes under `/api/chat`, `/api/shell/stream`, `/api/research`, `/api/model/download`, `/api/model/probe`, `/api/model-endpoints`, `/api/cookbook/setup`, `/api/upload`, and `/api/image`.

Generated-image path resolution fails closed for invalid names, path escape, and missing files. Ownership checks are best-effort when a current user exists: gallery rows owned by a different user return 404, rowless generated files are allowed, and DB/helper failures fail open. See `auth-security.md` for `LOCALHOST_BYPASS`, internal-tool loopback, proxy-header exclusion, and owner-impersonation policy.

## Runtime Behavior

- Request hard timeout applies to non-exempt paths that reach `_RequestTimeoutMiddleware`.
- YouTube support is initialized through `services.youtube.init_youtube()`.
- Vector document RAG is initialized lazily through `src.rag_singleton.get_rag_manager()` and may be unavailable at startup.
- `routes.workspace_routes` lets the browser choose a server directory for agent turns; execution confinement is enforced below the route layer by tool execution.

## Lifespan Startup

Startup purges leftover incognito sessions, reconciles default scheduled tasks before the task runner starts, and backfills legacy skill owners when possible.

Startup fire-and-forget work includes upload cleanup, background-job monitoring, MCP built-in registration and user-server connection, tool-index warmup, model-endpoint warmup, endpoint keepalive, Cookbook serve lifecycle monitoring, hourly null-owner sweeps, and nightly skill audit. The in-process task scheduler is gated by `ODYSSEUS_INPROCESS_TASKS`; email polling is started from email route setup and gated separately by `ODYSSEUS_INPROCESS_POLLERS`.

Shutdown cancels upload cleanup, stops the task scheduler, closes the webhook manager, and disconnects MCP servers.

## Degraded And Platform Behavior

- On Windows, HuggingFace symlink warnings are disabled so model files copy instead of symlink on network/UNC paths.
- `.env` is loaded with `utf-8-sig` to tolerate Notepad BOM files.
- Process-wide MIME registration forces stable `.js` and `.mjs` types across native platforms.
- Docker detection in `/api/runtime` selects `host.docker.internal` as the Ollama default inside containers and `127.0.0.1` natively. Compose sets Chroma to `chromadb:8000`; native Chroma defaults live in `src/chroma_client.py`.
- Chroma-backed consumers degrade independently: personal-doc RAG can return route-level 503s, semantic memory vectors can be dropped from chat/memory wiring, and the tool index can fall back when vector retrieval is unavailable.
- RAG startup failure is throttled so failed clients do not poison later retries.
- MCP startup is asynchronous and non-critical. User-server connection is bounded, failures surface through MCP status routes, and builtin MCP calls can reconnect after crashes.
- `/api/health` is liveness only. `/api/ready` checks database reachability, writable data dir, and local-first storage metadata; it does not prove optional subsystem health for RAG, Chroma, MCP, memory vectors, tool index, or endpoint warmups.

## Current Gaps

- `app.py` is still a large route registry and runtime orchestrator. There is no generated route manifest or smaller runtime composition layer yet.
- Long-running route timeout exemptions are manual and prefix-based; new SSE/proxy/task paths can be missed, while broad prefixes can exempt more routes than intended.
- Runtime tests cover small helper slices, but not full app import/TestClient behavior for mounted static cache headers, generated-image serving, timeout middleware, middleware order, lifespan startup wiring, or route/static drift.
- There is no aggregate degraded-state endpoint for optional subsystems; degraded state is mostly logged or exposed per route.
