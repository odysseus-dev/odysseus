# Frontend

Last updated: dev@a3cb15d | 2026-06-06

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

`static/manifest.json` and `static/index.html` reference PWA icon files that must exist under `static/`; missing icons are static-asset drift.

## Current Call Sites Include

- `static/index.html` script tags and modulepreloads;
- `static/sw.js` `PRECACHE`;
- app-owned SPA deep links for notes, calendar, cookbook, email, memory, gallery, tasks, and library;
- `/login` and app-owned static/HTML routes;
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

`static/index.html` owns the DOM shell and script loading order. It loads browser ES modules directly. Current boot order includes nonce-bearing inline boot scripts, self-hosted highlight.js, async CDN KaTeX/Mermaid, modulepreloads, ordered module script tags, `static/app.js`, `static/js/init.js`, `static/js/a11y.js`, workspace/plan/chat helpers, and service-worker registration.

Exact script URL identity matters. Versioned script tags, unversioned imports, and service-worker precache entries must stay aligned. Current service-worker precache coverage is not a full mirror of the `index.html` module graph, so changes there need direct verification.

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

`static/manifest.json` owns default PWA metadata. Route-specific manifests can be generated as Blob URLs when supported. Current default icon references must match real files under `static/`.

Offline/PWA behavior is not fully self-contained: KaTeX, Mermaid, and Pyodide use jsDelivr paths, while other vendor libraries are self-hosted under `static/lib`.

## Module Ownership

Current major frontend areas include:

- chat, stream handling, rendering, sessions, markdown, uploads, voice recorder, TTS, and keyboard shortcuts;
- models, provider setup, pure model-key matching helpers, model picker, presets, search, RAG, settings, and admin;
- compare modules under `static/js/compare/`, including sanitized popup/search/image handling;
- document editor/library in `static/js/document.js` and `static/js/documentLibrary.js`;
- image editor integration in `static/js/galleryEditor.js` plus leaves under `static/js/editor/`;
- gallery, email inbox/library, calendar, research panel/jobs/synapse, notes/tasks, assistant, memory/skills, Cookbook/HW Fit, workspace picker, plan window, theme, modal/window utilities, storage, and accessibility helpers.

Coordinator ownership:

- `static/app.js` owns late orchestration, global fetch 401 redirects, sidebar/tool route wiring, and many `window.*` compatibility bridges;
- `static/js/init.js` owns post-load cleanup, user-switch storage wipe, and cosmetic privilege gates;
- `static/js/storage.js` owns shared key constants and safe JSON helpers;
- feature modules own feature state where possible.

`static/js/MODULE_SUMMARY.md` is historical and explicitly not authoritative. Use the current `static/js/` tree and script tags as truth.

Current small frontend helper contracts include `static/js/model/matchKey.js` for longest-substring model info/pricing matches, `static/js/models.js` for in-flight `/api/models` request sharing, `static/js/fileHandler.js` for capped pending-file state and collapsed attachment-chip display, `static/js/streamingSegmenter.js` for incremental markdown/code-fence segmentation, `static/js/emojiShortcodes.js` for shortcode replacement, and `static/js/documentLibrary.js` for keeping document counters/language chips in sync after archive/delete.

## UI Policy

- New code must run as browser ES modules without a build step.
- Reuse existing CSS variables, modal/window patterns, icon style, storage helpers, and route conventions.
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

Recent focused coverage includes model-key matching under Node and document-library counter source-shape checks.

Missing coverage includes:

- SPA route/static auth and no-cache headers;
- CSP header contents and nonce injection for `/` and `/login`;
- service-worker API/non-GET bypass and cache strategy;
- service-worker precache versus `index.html` script/module tags, including query strings;
- manifest icon existence;
- module graph/load-order validation;
- degraded vendor/CDN/browser API behavior.

## Current Gaps

- `static/style.css` and large coordinators remain high-risk owners: `static/js/document.js`, `static/js/settings.js`, `static/js/chat.js`, and `static/app.js`.
- There is no build-time type checking, module graph validation, script-order validation, or service-worker precache validation.
- Frontend state is mostly module/global/localStorage driven, so cross-session and cross-user behavior needs explicit care.
- `window.*` compatibility bridges remain widespread.
- PWA/static-serving behavior may deserve a separate spec if service worker, manifests, route-specific icons, and cache policy keep growing.
- A static asset/route manifest regression should verify files referenced by `index.html`, `manifest.json`, `sw.js`, and app-owned HTML routes actually exist.
