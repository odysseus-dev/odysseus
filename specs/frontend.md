# Frontend

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers the current browser app in:

- static serving and SPA routes in `app.py`;
- CSP/security headers in `core/middleware.py`;
- `static/index.html`;
- `static/login.html`;
- `static/app.js`;
- `static/style.css`;
- `static/js/*.js` and `static/js/*/*.js`;
- vendor libraries under `static/lib/*`;
- custom fonts and static assets under `static/fonts/*`;
- `static/sw.js` and `static/manifest.json`;
- frontend-oriented tests in `tests/*_js.py`, `tests/*.mjs`, `tests/bombadil-spec.ts`, static DOM/CSS/source-shape tests, and app/static tests such as `tests/test_app_static_mime.py`.

`/backgrounds` currently targets `static/backgrounds.html`; if that route remains, the file must exist or the route should be removed.

`static/manifest.json` and `static/index.html` reference PWA icon files under `static/icons/`; the current 192px, 512px, and maskable icon files exist and should stay aligned with those references.

## Current Call Sites Include

- `static/index.html` script tags and modulepreloads;
- `static/sw.js` `PRECACHE`;
- app-owned SPA deep links for notes, calendar, cookbook, email, memory, gallery, tasks, and library;
- `/login` and app-owned static/HTML routes;
- `/api/activity/heartbeat` browser visibility pings used by the foreground activity gate;
- `static/app.js` route opener/sidebar/tool-window wiring;
- frontend JS helper tests and static HTML/CSS/source-shape regressions;
- CDN dependencies, local vendor libraries, service worker, and PWA manifest.

## Runtime Shape

The frontend is a raw static SPA served by FastAPI. There is no Vite, React, TypeScript, bundler, or generated build output.

`app.py` owns:

- stable `.js`/`.mjs` MIME registration;
- the `/static` mount;
- no-cache headers for `.js`, `.css`, and `.html` static source files;
- nonce-injected SPA/login HTML serving;
- SPA deep-link routes.

`static/index.html` owns the DOM shell and script loading order. It loads browser ES modules directly. Current boot order includes nonce-bearing inline boot scripts, self-hosted highlight.js, modulepreloads, ordered module script tags, `static/app.js`, `static/js/init.js`, `static/js/a11y.js`, workspace/chat helpers, provider device-flow helpers, and service-worker registration. KaTeX and Mermaid are vendored under `static/lib` and injected only on first real math/diagram use rather than loading in the initial HTML.

The two first-paint Fira Code faces are preloaded so the shell does not wait for later CSS discovery. `static/js/startupShell.js` lets the visible shell initialize before session loading completes; session/transcript hydration is deferred and coordinated by `static/js/sessions.js` plus history/session routes rather than blocking first paint.

Exact script URL identity matters. Versioned script tags, unversioned imports, and service-worker precache entries must stay aligned. `static/sw.js` deliberately separates first-paint `PRECACHE` from lazy `PANEL_PRECACHE`; the latter currently contains the image-editor module graph so an editor never opened online can still open offline. KaTeX scripts/styles/fonts are also precached. Current service-worker coverage is not a generated full module-graph manifest, so changes still need direct verification.

## Security Policy

`core/middleware.py` owns CSP and security headers. `app.py` injects the per-request nonce into served HTML. New inline scripts or external scripts/styles/images/media must fit the CSP contract or explicitly update it.

`/static/*` is public/auth-exempt. Frontend privilege gates are display-only; backend routes enforce authorization.

XSS/DOM policy:

- prefer DOM construction, `textContent`, and shared escaping helpers;
- Markdown raw HTML preservation must remain constrained through sanitizer helpers;
- remote email `body_html` must pass through the email-library sanitizer before insertion;
- Mermaid, code-runner iframe `srcdoc`, visual reports, remote media, and scattered `innerHTML` templates require explicit review.
- Visual report Markdown HTML is server-rendered and should be treated as security-sensitive alongside frontend entry points and remote media.

Storage/secrets policy:

- localStorage/sessionStorage are for preferences, UI state, offline caches, and user-switch sentinels;
- `static/js/init.js` owns user-switch storage cleanup;
- raw API tokens, provider keys, HF tokens, and other credentials must not be persisted in browser storage unless a feature documents masking/stripping and backend storage ownership.

## Service Worker And PWA

`static/sw.js` owns PWA cache behavior:

- API and non-GET requests are bypassed;
- root navigation uses stale-while-revalidate;
- JS/CSS use network-first behavior;
- other static assets use cache-first with background refresh;
- `CACHE_NAME` bumps and `PRECACHE` updates must accompany cache policy or shell asset changes.

`static/manifest.json` owns default PWA metadata. Route-specific manifests can be generated as Blob URLs when supported. Current default icon references must match real files under `static/icons/`.

KaTeX and Mermaid are self-hosted and lazy-loaded through memoized, retry-after-failure promises in `static/js/markdown.js`; math placeholders preserve source until KaTeX arrives, detached PDF export renders its own container, and Mermaid fetches only when a diagram exists. Pyodide remains a jsDelivr-loaded optional runtime, so offline/PWA behavior is not fully self-contained.

## Module Ownership

Current major frontend areas include:

- chat, stream handling, rendering, sessions, markdown, uploads, voice recorder, TTS, and keyboard shortcuts;
- models, provider setup, pure model-key matching helpers, model picker, presets, search, RAG, settings, and admin;
- settings shell modules under `static/js/settings/`: registry metadata, navigation, finder search, lifecycle/docking, DOM helpers, and persisted sidebar collapse/resize behavior;
- compare modules under `static/js/compare/`, including sanitized popup/search/image handling;
- document editor/library in `static/js/document.js` and `static/js/documentLibrary.js`;
- image editor integration in `static/js/galleryEditor.js` plus leaves under `static/js/editor/`;
- gallery, email inbox/library, calendar, research panel/jobs/synapse, notes/tasks, assistant, memory/skills, Cookbook/HW Fit, workspace picker, provider device flow, composer ArrowUp recall, theme, modal/window utilities, storage, and accessibility helpers.

Coordinator ownership:

- `static/app.js` owns late orchestration, global fetch 401 redirects, sidebar/tool route wiring, and many `window.*` compatibility bridges;
- `static/js/init.js` owns post-load cleanup, user-switch storage wipe, and cosmetic privilege gates;
- `static/js/storage.js` owns shared key constants and safe JSON helpers;
- feature modules own feature state where possible.

`static/js/appConfig.js` owns one invalidatable promise cache for `GET /api/auth/settings` and `GET /api/tools`, including one-shot login-page settings prefetch, retry after rejected fetches, and explicit invalidation after settings/tool writes. Consumers treat resolved objects as read-only. `static/js/panels.js` owns memoized first-use panel imports; its current registry contains the image editor, shares in-flight imports, and evicts failed imports so a later online retry can succeed.

`static/js/MODULE_SUMMARY.md` is a refreshed ownership/navigation map for the no-build frontend. The current `static/js/` tree, `static/app.js`, `static/index.html`, and executable behavior remain the authority when the summary drifts.

Current small frontend helper contracts include `static/js/model/matchKey.js` for longest-substring model info/pricing matches, `static/js/models.js` for in-flight `/api/models` request sharing, `static/js/providerDeviceFlow.js` for Copilot/ChatGPT Subscription device-flow polling UI, `static/js/composerArrowUpRecall.js` for prompt recall from an empty composer, `static/js/fileHandler.js` for capped pending-file state and collapsed attachment-chip display, `static/js/streamingSegmenter.js` for incremental markdown/code-fence segmentation, `static/js/emojiShortcodes.js` for shortcode replacement, `static/js/documentLibrary.js` for keeping document counters/language chips in sync after archive/delete, `static/js/keyboard-shortcuts.js` for rejecting empty or non-string persisted keybinds before combo parsing, `static/js/modalSnap.js` for reusable desktop modal edge docking, `static/js/toolWindowZOrder.js` for shared portal/window z-index allocation, and `static/js/emailShared.js` for common email UI helpers.

Recent browser behavior contracts include mobile chat Enter inserting newlines while desktop Enter submits; ArrowUp recall only consuming a truly empty composer with the caret at the top, not an unsent multiline prompt; queued prompts preserving mobile behavior; regenerate-from-here versus resend; AI-message delete confirmation; native document tool results opening/updating the editor; and exact tool-approval cards that expose the sealed action/effects/workspace/document identity and submit only opaque task-scope/chat-session-scope/deny decisions without writing synthetic composer text. Chat rendering hides leaked tool JSON/document fences, no longer strips the ordinary word “assistant,” and batches live-thinking DOM updates with bounded timers. Markdown editing/restoration preserves extracted code/math blocks verbatim, including replacement-string `$&` and `$$` text and triple-backtick fences. Session URL hashes are restored, minimized sidebar icon state follows per-tab visibility, detached terminal dots remain centered, and spinner animation starts only when attached.

The Settings finder and navigation are registry-backed, hide admin-only destinations from non-admin users, lazy-load admin panels, and keep the registry synchronized with DOM panels. Email OAuth connect preserves SMTP security and reopens the settings surface; unread message opens use one authoritative backend read/mark-seen request with stale-response guards; email-library prewarm is idle-only, single-flight, bounded to the initial page, and cancelled around visible foreground work.

## UI Policy

- New code must run as browser ES modules without a build step.
- Reuse existing CSS variables, modal/window patterns, icon style, storage helpers, and route conventions.
- Custom font handling includes bundled OpenDyslexic assets plus user-supplied fonts exposed through `/api/fonts/custom`; font and text-size settings must stay coordinated between settings UI, theme helpers, and CSS variables.
- Avoid relying on stale module summaries.
- API shape changes must update the owning JS module and tests.
- Add behavior to large coordinators such as `static/app.js`, `static/js/chat.js`, `static/js/document.js`, or `static/js/settings.js` only when it matches their existing wiring ownership.

## Degraded And Platform Behavior

- Server no-cache applies to `.js`, `.css`, and `.html` source files, not every static asset.
- Service-worker cache changes can affect frontend behavior even when source files revalidate.
- Mobile behavior uses separate CSS/media/hover/safe-area/`100dvh` handling and JS layout code; check it directly.
- Browser APIs such as service workers, Blob route manifests, Web Speech, `getUserMedia`, visual viewport, and storage can be absent or restricted.
- Local libraries and CDN globals degrade differently; document, markdown, math, diagrams, and code runner flows should handle missing globals where possible.
- localStorage migrations and cross-user cleanup are part of compatibility.

## Testing Coverage

Existing frontend coverage is a mix of Node-executed helper tests, `.mjs` tests, static DOM/CSS/source-shape tests, browser exploration specs, and app/static tests. Many tests are useful source-shape regressions but do not replace browser/module-graph execution.

Recent focused coverage includes model-key matching under Node, document-library counters, chat resend/delete/mobile Enter/ArrowUp, scoped approval continuation and compare routing, route provenance, live-thinking throttling, startup shell/history hydration, shared app-config caching/invalidation, settings registry/navigation/finder/lifecycle, lazy panel loading/offline editor precache, vendored lazy KaTeX/Mermaid rendering, email read dedup/prewarm, Markdown restoration, malformed keybinds, currency-safe inline math, notes/calendar/modal/manifest/admin-log behavior, Markdown XSS helpers, and CardDAV unchanged-password handling.

Missing coverage includes:

- SPA route/static auth and no-cache headers;
- CSP header contents and nonce injection for `/` and `/login`;
- service-worker API/non-GET bypass and cache strategy;
- service-worker precache versus `index.html` script/module tags, including query strings;
- ongoing manifest/icon reference drift;
- module graph/load-order validation;
- degraded vendor-library/browser API behavior, including Pyodide's remaining CDN path.

## Current Gaps

- `static/style.css` and large coordinators remain high-risk owners: `static/js/document.js`, `static/js/settings.js`, `static/js/chat.js`, and `static/app.js`.
- There is no build-time type checking, module graph validation, script-order validation, or service-worker precache validation.
- Frontend state is mostly module/global/localStorage driven, so cross-session and cross-user behavior needs explicit care.
- `window.*` compatibility bridges remain widespread.
- PWA/static-serving behavior may deserve a separate spec if service worker, manifests, route-specific icons, and cache policy keep growing.
- A static asset/route manifest regression should verify files referenced by `index.html`, `manifest.json`, `sw.js`, and app-owned HTML routes actually exist.
