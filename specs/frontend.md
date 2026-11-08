# Frontend

Last updated: dev@c74f475 | 2026-10-15

## Scope

This spec covers the current browser app in:

- static serving and SPA routes in `app.py`;
- CSP/security headers in `core/middleware.py`;
- `static/index.html`;
- `static/login.html`;
- `static/app.js`;
- ordered stylesheets under `static/css/`;
- `static/js/*.js` and `static/js/*/*.js`;
- vendor libraries under `static/lib/*`;
- custom fonts and static assets under `static/fonts/*`;
- `static/sw.js` and `static/manifest.json`;
- frontend-oriented tests in `tests/*_js.py`, `tests/*.mjs`, `tests/bombadil-spec.ts`, static DOM/CSS/source-shape tests, and app/static tests such as `tests/test_app_static_mime.py`.

`/backgrounds` remains registered in `app.py` but targets the absent `static/backgrounds.html`. Authentication middleware also gates it when auth is enabled, despite its handler docstring describing a public prototype page. This is a known route/static gap; the page is not a working frontend surface at the checked revision.

`static/manifest.json`, `static/index.html`, and `static/login.html` reference first-party PWA icon files under `static/icons/` (192px, 512px, and maskable). At runtime, route-specific manifests can also be generated as Blob URLs when supported, and per-route favicon links use generated inline SVG.

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

The browser runtime is a static native-ES-module SPA served by FastAPI, without a runtime Node/build dependency. Incremental maintained sources now live under `frontend/src/`, with explicit source-to-deployment mappings in `frontend/modules.json`; the map publishes `shared/actionMenuOrder.js`, the Notes feature graph and shared confetti to committed `static/js/` outputs. `scripts/build_frontend.py` publishes/checks those bytes during development, never application startup. Legacy modules remain authored under `static/`; there is no Vite/React/TypeScript bundle.

`app.py` owns:

- stable `.js`/`.mjs` MIME registration;
- the `/static` mount;
- no-cache headers for `.js`, `.css`, and `.html` static source files;
- nonce-injected SPA/login HTML serving;
- SPA deep-link routes.

`static/index.html` owns the DOM shell and script loading order. It loads browser ES modules directly. Current boot order includes nonce-bearing inline boot scripts, self-hosted highlight.js, modulepreloads, ordered module script tags, `static/app.js`, `static/js/init.js`, `static/js/a11y.js`, workspace/chat helpers, provider device-flow helpers, and service-worker registration. KaTeX and Mermaid are vendored under `static/lib` and injected only on first real math/diagram use rather than loading in the initial HTML.

The two first-paint Fira Code faces are preloaded so the shell does not wait for later CSS discovery. `static/js/startupShell.js` lets the visible shell initialize before session loading completes; session/transcript hydration is deferred and coordinated by `static/js/sessions.js` plus history/session routes rather than blocking first paint.

Exact script URL identity matters. Versioned script tags, unversioned imports, and service-worker precache entries must stay aligned. `static/sw.js` deliberately separates first-paint `PRECACHE` from lazy `PANEL_PRECACHE`; the latter contains the image-editor module graph so an editor never opened online can still open offline. The Notes feature graph and shared confetti are explicitly in `PRECACHE`, together with their exact legacy helper URLs. KaTeX scripts/styles/fonts are also precached. Current service-worker coverage is not a generated full module-graph manifest, so changes still need direct verification.

`static/style.css` has been removed. `static/index.html` loads numbered `static/css/00-tokens.css` through `17-research.css` in order, followed by the three domain panel stylesheets. `static/sw.js` precaches those exact versioned URLs; cascade order remains part of behavior.

New extracted owners include `static/js/turnRendering.js` for live/canonical turn DOM reconciliation, `backgroundToolJobs.js` for deduplicated background deliveries and research status cards, `emailReplyStream.js` for email reply streams, and `chatgptSubscriptionUsage.js` for account usage UI. Settings panel modules now also include `agent.js`, `api.js`, `imageModels.js`, `speech.js`, and `writingStyle.js`.

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
- only `/` navigation uses network-first with cached-shell fallback; other navigation URLs are not replaced with the app shell;
- JS/CSS use network-first behavior;
- other static assets use cache-first with background refresh;
- `CACHE_NAME` bumps and `PRECACHE` updates must accompany cache policy or shell asset changes.

`static/manifest.json` owns default PWA metadata, referencing the restored icons under `static/icons/`. Route-specific manifests can be generated as Blob URLs when supported; dynamic icons use the route's generated inline SVG, while the default manifest uses the static icons.

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

`static/js/emailLibrary.js` is a compatibility wrapper over one unversioned package entry and explicit leaves for reader, menus, attachments, reply, settings and unsubscribe behavior; the final wrapper has ten named exports. `tests/test_email_library_module_graph_js.py` compares the unversioned public surface, imports immediate package modules in synthetic browser shells and checks quoted service-worker URLs. It does not prove every callback, nested module, load order or offline behavior. `tests/helpers/js_modules.py` uses immediate entry-first file enumeration and column-zero source extraction; its package-name binding regex is not lexical-scope analysis.

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

## Notes Feature Ownership

`frontend/src/features/notes/index.js` composes one canonical Notes instance from domain rules/recurrence, application operations/state, infrastructure ports and UI factories. `frontend/modules.json` publishes its source modules byte-for-byte under `static/js/features/notes/` and shared confetti under `static/js/shared/ui/`. `static/js/notes.js` is the compatibility facade: it reexports the canonical methods/default, assigns `window.notesModule` and starts idempotent background reminders. Versioned/unversioned facade imports converge on the unversioned canonical entry. `static/app.js` retains the versioned facade caller; Compare voting imports and reexports shared confetti rather than owning it.

State mutations belong to `application/state.js`; UI gets live read-only views, and operation writes unwrap nested views to keep stored note data cloneable. The composition injects HTTP, persistence, clock/scheduler, notification and shared platform callbacks. Panel disposal releases owned portals, document/window listeners, gesture state and masonry observers; detached/closed-panel checks prevent late completions from recreating observers. Tablet reorder rewiring replaces stale binding sentinels. Reminder scheduling is page-owned and survives close/minimize; custom reminder date initialization uses DOM value assignment rather than HTML interpolation. Domain-specific drafts/reminder/selection contracts and remaining races are in `calendar-tasks-notes.md`.

## UI Policy

- New runtime code remains native browser ES modules; manifest-selected maintained sources use the development publication/check step without adding a startup build.
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

`tests/test_css_computed_style_snapshot.py` pins `getComputedStyle` for a fixed element inventory across pages, viewports, themes and density modes, so a stylesheet cascade restructuring that changes which declaration wins fails a test instead of shipping; see `tests/css_snapshot/README.md` for what it does and does not cover.

Recent focused coverage includes model-key matching under Node, document-library counters, chat resend/delete/mobile Enter/ArrowUp, scoped approval continuation and compare routing, route provenance, live-thinking throttling, startup shell/history hydration, shared app-config caching/invalidation, settings registry/navigation/finder/lifecycle, lazy panel loading/offline editor precache, vendored lazy KaTeX/Mermaid rendering, email read dedup/prewarm, Markdown restoration, malformed keybinds, currency-safe inline math, notes/calendar/modal/manifest/admin-log behavior, Markdown XSS helpers, and CardDAV unchanged-password handling.

Areas that still need explicit behavior verification include:

- SPA route/static auth and no-cache headers;
- CSP header contents and nonce injection for `/` and `/login`;
- service-worker API/non-GET bypass and cache strategy;
- complete service-worker precache membership versus HTML scripts/modules: stylesheet parity is covered by `tests/test_static_stylesheet_manifest.py`, and `tests/test_frontend_module_version_parity.py` checks selected app/index/SW versions plus regex-discovered JS/index URL identities, but neither proves complete graph evaluation;
- ongoing manifest/icon reference drift; `sw.js` now explicitly precaches the default manifest and three PWA icons;
- complete legacy module graph/load-order behavior beyond maintained-source architecture gates and the email-library browser graph;
- degraded vendor-library/browser API behavior, including Pyodide's remaining CDN path.

## Rendering, Provider UI And Static Checks

`turnRendering.js` reconciles canonical/live ownership with turn replacement metadata while keeping tool activity. Compact corrected/final events can replace earlier prose; the server completion gate can buffer answer deltas, so this is not a promise of immediate streaming. Ordinary ask-user choices have 1–3 keyboard shortcuts; tool approval cards are excluded. Active document state restores the exact chat-bound document; editor rich-only font controls and stats portal z-order are independent of chat tool authority. The research map SVG has an image role and accessible name, without proving complete accessibility.

Usage ledgers use nested Maps to preserve arbitrary legacy keys as data, while fresh writes require a nonempty session id and reject reserved session/run keys before accounting. JSON conversion preserves wire shape; metrics with a run id replace that run's previous value, while legacy writes without one add to aggregate totals and use per-object recorded/pending guards. Web Locks are used when available and bounded entries fold into totals, so evicted run replay is not forever deduplicated. Admin provider cards hide ChatGPT/recognized Featherless base URLs; optional fingerprints and displayed version/source commit are informational rather than byte attestation. Featherless catalog search is debounced/paginated with bounded results; optimistic pin/tool-mode PATCH saves and load-more handling do not guarantee acknowledged persistence or universal stale-response cancellation. Collapsed account usage cards retain loaded DOM, including errors, rather than proving fresh telemetry.

Markdown SVG uses an empty-sandbox, no-referrer iframe and restrictive CSP; title extraction is flat escaped text rather than parent-DOM parsing. Fenced and complete raw SVG are separate from generic HTML fences, with bounded aspect ratio. This does not confer the different permissions used by document print/run iframes. Remote mail rendering and image thumbnail assignment retain their own sanitizer/network boundaries.

Stylesheet helpers read the shell-ordered complete app cascade, exclude vendored libraries and share cache-token checks. Recursive lexical hygiene rejects deleted-monolith references with bounded exceptions; comment-stripped keyframe counting catches duplicate global names. Document-source helpers enumerate implementation files and scan braces lexically; quoted import/SW membership and minimum callable export tests are narrower than semantic module execution. The fixed computed-style snapshot covers 676 elements across its configured page/viewport/theme/density cases, freezes animation timing and checks a global rule-swap sensitivity count; it is not arbitrary browser/layout coverage. Manual benchmark HTML and historical screenshots are not newly reproduced acceptance.

The website has seven hover/focus/tap preview clips, ambient video, failure placeholders and thresholded swipe navigation. Reduced-motion CSS disables selected animation rather than guaranteeing all video stops. Restored branding/PWA assets do not prove license clearance or decoded-image review; the known `/backgrounds` absent-file/auth gap remains.

## Current Gaps

- The ordered stylesheet cascade and large coordinators remain high-risk owners: `static/js/document.js`, `static/js/settings.js`, `static/js/chat.js`, and `static/app.js`.
- There is no general build-time type checking or complete legacy script-order validation. `scripts/check_frontend.mjs` checks maintained-source imports, layer boundaries, explicit legacy exceptions, deployment URL/precache identity, and dependency cycles; its inventory mode reports legacy static imports/cycles. These gates cover the manifest-selected maintained graph, not every legacy module. Service-worker checks also cover stylesheet URLs and the email-library package.
- Frontend state is mostly module/global/localStorage driven, so cross-session and cross-user behavior needs explicit care.
- `window.*` compatibility bridges remain widespread.
- PWA/static-serving behavior may deserve a separate spec if service worker, manifests, route-specific icons, and cache policy keep growing.
- Static regressions cover stylesheet references, selected script/module URL identities and maintained-output precache membership; complete legacy graph membership/evaluation and ongoing manifest icon drift still require checks.
