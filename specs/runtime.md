# Runtime

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers current app runtime wiring in:

- `app.py`;
- `src/app_initializer.py`;
- `src/runtime_paths.py`;
- `src/config.py`;
- `core/constants.py`;
- `src/constants.py`;
- `src/interactive_gate.py`;
- `src/host_docker_access.py`;
- `core/middleware.py`;
- all route setup functions registered from `app.py`, including canonical
  `routes/admin_wipe/`, `routes/cleanup/`, `routes/compare/`, `routes/contacts/`, `routes/document/`, `routes/gallery/`, `routes/history/`, `routes/mcp/`, `routes/memory/`, `routes/note/`, `routes/research/`, `routes/search/`, `routes/task/`, `routes/vault/`, and `routes/webhook/` packages plus top-level compatibility shims;
- `routes/prefs_routes.py`, `routes/workspace_routes.py`, and `companion/routes.py`;
- `src/generated_images.py` for generated-media file resolution;
- `launcher.py`, `Odysseus.spec`, and platform launcher scripts where frozen/native startup changes runtime paths;
- static entrypoints in `static/index.html`, `static/login.html`, and `static/app.js`.

## App Orchestrator

`app.py` owns process-level startup and HTTP composition. It configures MIME types, `.env` loading, logging under `DATA_DIR/logs`, CORS, gzip compression, auth middleware, request timeout middleware, static files, generated-image serving, router registration, SPA HTML routes, health/readiness/runtime endpoints, and lifespan hooks. Its console, rotating-file, and direct-uvicorn logging levels use the existing `LOG_LEVEL` environment toggle and default to `INFO`; invalid levels also fall back to `INFO`. `core/middleware.py` owns security headers, admin helpers, and internal-tool token constants.

`src/app_initializer.initialize_managers()` owns shared manager construction. It creates memory, skills, sessions, uploads, personal docs, API keys, presets, chat processor/handler, research handler, model discovery, and optional memory vector store. Route modules receive these dependencies from `app.py`; they should not recreate manager singletons.

`app.py` separately owns runtime singletons and integration hooks for auth, vector RAG, TTS/STT, webhooks, scheduled tasks, MCP, assistant log globals, event bus wiring, AI interaction globals, API-token cache invalidation, and foreground activity tracking. `src.runtime_paths` owns source-versus-frozen app/data path resolution; `src.constants` derives `DATA_DIR` from `ODYSSEUS_DATA_DIR` or that runtime default. `core/constants.py` and `src/constants.py` are both live import paths and are not fully identical today, so new constants need explicit placement/compatibility decisions.

The shared upload handler is also installed on the session manager and tool
helper, and `app.py` injects it into attachment-bearing route factories so
durable writers and cleanup use one lifecycle owner.

## Routes And Static Serving

Current router call sites include:

- auth, uploads, emoji, sessions, admin wipe, memory, skills, chat, workspace, research, history, search, presets, diagnostics, cleanup, personal docs, embeddings, model endpoints;
- TTS/STT, documents, signatures, gallery, editor drafts, scheduled tasks, assistant, calendar, shell, Cookbook, HW Fit, compare, preferences, backup, fonts, Copilot and ChatGPT Subscription auth;
- MCP, webhooks, API tokens, notes, email, Codex/Claude scoped APIs, vault, contacts, and companion routes.

Admin wipe, cleanup, compare, contacts, documents, gallery, history, MCP, memory, notes, research, search, tasks, vault, and webhooks have canonical subpackage modules. Their old top-level route modules replace their `sys.modules` entries with the canonical module object so legacy imports, `importlib`, and monkeypatch tests target the same module that `app.py` uses. `app.py` imports task setup from `routes.task.task_routes`.

The SPA routes `/`, `/notes`, `/calendar`, `/cookbook`, `/email`, `/memory`, `/gallery`, `/tasks`, and `/library` all serve `static/index.html`. `static/` is served with revalidation for `.js`, `.css`, and `.html` because the frontend ships raw browser modules with no hashed build output.

Direct app-owned endpoints include `/api/generated-image/{filename}`, `/backgrounds`, `/login`, `/api/version`, `/api/health`, `/api/ready`, `/api/runtime`, and `/api/activity/heartbeat`. `/backgrounds` points at `static/backgrounds.html`; if that file is absent or the route remains auth-gated, that is route/static drift rather than an intentional public contract.

`/static/*` is auth-exempt and public. SPA HTML routes are auth-gated except `/login`, and they are nonce-injected dynamic `HTMLResponse` values outside the static mount. Generated images and videos are served from `data/generated_images` through the generated-image resolver with immutable/nosniff caching.

## Runtime Security Boundaries

Effective middleware order matters. CORS, `SecurityHeadersMiddleware`, `_RequestTimeoutMiddleware`, and GZip middleware are added before `AuthMiddleware`; auth short-circuit responses can therefore bypass downstream app handlers and should be tested when changing response headers or auth behavior. Text responses can be compressed when they pass through the app stack.

Security headers include HSTS and a restrictive `Permissions-Policy` that disables camera/geolocation and only allows microphone from self.

`_TIMEOUT_EXEMPT_PREFIXES` owns hard-timeout bypass policy. It is prefix-based and currently exempts all subroutes under `/api/chat`, `/api/shell/stream`, `/api/research`, `/api/model/download`, `/api/model/probe`, `/api/model-endpoints`, `/api/cookbook/setup`, `/api/upload`, `/api/image`, and `/api/memory/audit`. Memory audit has its own longer inactivity timeout.

Generated-image path resolution fails closed for invalid names, path escape, and missing files. Ownership checks are best-effort when a current user exists: gallery rows owned by a different user return 404, rowless generated files are allowed, and DB/helper failures fail open. See `auth-security.md` for `LOCALHOST_BYPASS`, internal-tool loopback, proxy-header exclusion, and owner-impersonation policy.

## Runtime Behavior

- Request hard timeout applies to non-exempt paths that reach `_RequestTimeoutMiddleware`.
- `src.interactive_gate` tracks foreground requests, browser heartbeats, and active chat streams. Background task/email work can wait for a quiet window so scheduled jobs do not compete with visible browser or model activity. Status polling and `/api/email/unread-state` are passive reads: they do not cancel running scheduled work or manufacture foreground pressure.
- YouTube support is initialized through `services.youtube.init_youtube()`.
- Vector document RAG is initialized lazily through `src.rag_singleton.get_rag_manager()` and may be unavailable at startup.
- `routes.workspace_routes` lets the browser choose a server directory for agent turns; execution confinement is enforced below the route layer by tool execution.

## Lifespan Startup

Upload cleanup first snapshots durable chat, document, gallery, note, and
calendar references and aborts on scan or upload-index integrity failure.

Startup purges leftover incognito sessions, reconciles default scheduled tasks before the task runner starts, and backfills legacy skill owners when possible.

Startup fire-and-forget work includes upload cleanup, background-job monitoring, MCP built-in registration and user-server connection, tool-index warmup, model-endpoint warmup, endpoint keepalive, Cookbook serve lifecycle monitoring, hourly null-owner sweeps, and nightly skill audit. The in-process task scheduler is gated by `ODYSSEUS_INPROCESS_TASKS`; email polling is started from email route setup and gated separately by `ODYSSEUS_INPROCESS_POLLERS`. Foreground-gate knobs are `BACKGROUND_TASK_FOREGROUND_GATE`, `BACKGROUND_TASK_QUIET_MS`, `BACKGROUND_TASK_MAX_WAIT_SECONDS`, and `BACKGROUND_TASK_BROWSER_ACTIVE_SECONDS`.

Shutdown cancels upload cleanup, stops the task scheduler, closes the webhook manager, and disconnects MCP servers.

## Degraded And Platform Behavior

- On Windows, HuggingFace symlink warnings are disabled so model files copy instead of symlink on network/UNC paths.
- `.env` is loaded with `utf-8-sig` to tolerate Notepad BOM files.
- Auth and middleware path checks use Starlette's application-relative route path, so a deployment mounted under `root_path` keeps segment-aware auth exemptions, timeout policy, and login redirects instead of comparing proxy prefixes as application routes.
- Process-wide MIME registration forces stable `.js` and `.mjs` types across native platforms.
- Frozen/PyInstaller builds use `src.runtime_paths` so bundled app assets resolve from the executable payload while persistent data defaults to `~/.odysseus/data`; normal source runs still default to the repository `data/` directory unless `ODYSSEUS_DATA_DIR` overrides it.
- Docker detection in `/api/runtime` selects `host.docker.internal` as the Ollama default inside containers and `127.0.0.1` natively. Compose sets Chroma to `chromadb:8000`; native Chroma defaults live in `src/chroma_client.py`.
- `src.host_docker_access` treats host Docker access from inside the container as opt-in. Default Compose does not mount `/var/run/docker.sock`; `docker/host-docker.yml` plus `ODYSSEUS_ENABLE_HOST_DOCKER=true` are required before local container code considers the host Docker daemon available.
- Chroma-backed consumers degrade independently: personal-doc RAG can return route-level 503s, semantic memory vectors can be dropped from chat/memory wiring, and the tool index can fall back when vector retrieval is unavailable.
- RAG startup failure is throttled so failed clients do not poison later retries.
- MCP startup is asynchronous and non-critical. User-server connection is bounded, failures surface through MCP status routes, and builtin MCP calls can reconnect after crashes.
- `/api/health` is liveness only. `/api/ready` checks database reachability, writable data dir, and local-first storage metadata; it does not prove optional subsystem health for RAG, Chroma, MCP, memory vectors, tool index, or endpoint warmups.
- `/api/diagnostics/services` is an admin diagnostics endpoint for optional service health. It reports bounded, non-intrusive checks for ChromaDB, SearXNG, email accounts, ntfy, and model provider endpoints with `ok`/`degraded`/`down`/`disabled` style status values and strips secret-bearing URLs/errors. `/api/diagnostics/logs` returns a bounded tail of the app log for admin troubleshooting.

## Current Gaps

- `app.py` is still a large route registry and runtime orchestrator. There is no generated route manifest or smaller runtime composition layer yet.
- Long-running route timeout exemptions are manual and prefix-based; new SSE/proxy/task paths can be missed, while broad prefixes can exempt more routes than intended.
- Runtime tests cover small helper slices, but not full app import/TestClient behavior for mounted static cache headers, generated-image serving, timeout middleware, middleware order, lifespan startup wiring, or route/static drift.
- The diagnostics service-health endpoint is not a readiness gate and does not cover every optional subsystem.
