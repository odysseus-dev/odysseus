// static/sw.js — Odysseus PWA Service Worker
// Strategy:
//   - HTML (navigation): stale-while-revalidate. Instant open from cache,
//     background refresh so the next open has latest HTML.
//   - JS/CSS (/static/*.js|.css): network-first, cache fallback for offline.
//     (So code/style edits show up on a normal reload, no manual cache clear.)
//   - Other static assets (images/fonts/libs): cache-first with bg refresh.
//   - API / non-GET: never cached.
// Bump CACHE_NAME whenever the precache list or SW logic changes.
const CACHE_NAME = 'odysseus-v380-shared-config-image-editor-lazy-katex-mermaid';

// KaTeX resolves these from its own stylesheet, so caching the CSS without them
// gives offline math fallback glyphs instead of proper typesetting.
const KATEX_FONTS = [
  'AMS-Regular', 'Caligraphic-Bold', 'Caligraphic-Regular',
  'Fraktur-Bold', 'Fraktur-Regular',
  'Main-Bold', 'Main-BoldItalic', 'Main-Italic', 'Main-Regular',
  'Math-BoldItalic', 'Math-Italic',
  'SansSerif-Bold', 'SansSerif-Italic', 'SansSerif-Regular',
  'Script-Regular',
  'Size1-Regular', 'Size2-Regular', 'Size3-Regular', 'Size4-Regular',
  'Typewriter-Regular',
].map(name => `/static/lib/katex/fonts/KaTeX_${name}.woff2`);


// Two lists, two jobs — they are no longer the same set and must not be
// "resynced" back into one:
//
//   PRECACHE       = the app shell. Mirrors the <script type="module"> tags
//                    and <link rel="stylesheet"> in index.html — i.e. what
//                    loads before first paint.
//   PANEL_PRECACHE = modules that index.html deliberately does NOT load,
//                    because js/panels.js imports them on first use. They are
//                    off the critical path, not out of the offline manifest:
//                    without them here, a panel the user never opened while
//                    online could not open offline at all.
//
// Both are fetched at install time, in the background. Entries must match the
// exact URL the browser requests, query string included.
const PRECACHE = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/js/storage.js',
  '/static/js/appConfig.js',
  '/static/js/ui.js',
  '/static/js/markdown.js',
  '/static/js/dragSort.js',
  '/static/js/sessions.js',
  '/static/js/memory.js',
  '/static/js/skills.js',
  '/static/js/tourHints.js',
  '/static/js/fileHandler.js',
  '/static/js/voiceRecorder.js',
  '/static/js/models.js',
  '/static/js/rag.js',
  '/static/js/presets.js',
  '/static/js/search.js',
  '/static/js/spinner.js',
  '/static/js/tts-ai.js',
  '/static/js/document.js',
  '/static/js/gallery.js',
  '/static/js/chatRenderer.js',
  '/static/js/codeRunner.js',
  '/static/js/chatStream.js',
  '/static/js/chat.js',
  '/static/js/cookbook.js',
  '/static/js/search-chat.js',
  '/static/js/compare/index.js',
  '/static/js/theme.js',
  '/static/js/censor.js',
  '/static/js/settings.js',
  '/static/js/admin.js',
  '/static/js/init.js',
  '/static/js/slashCommands.js',
  '/static/js/emailInbox.js',
  '/static/js/emailLibrary/utils.js',
  '/static/js/emailLibrary/signatureFold.js',
  '/static/js/emailLibrary/state.js',
  '/static/js/notes.js',
  '/static/js/tasks.js',
  '/static/js/calendar.js',
  '/static/js/calendar/utils.js',
  '/static/js/calendar/reminders.js',
  '/static/js/group.js',
  '/static/js/keyboard-shortcuts.js',
  '/static/js/sidebar-layout.js',
  '/static/js/section-management.js',
  '/static/lib/highlight.min.js',
  // Math turns up in ordinary answers and KaTeX is small, so precaching it and
  // its fonts keeps formulas typeset offline. Mermaid is deliberately NOT
  // precached: at 3.5 MB it would re-download on every CACHE_NAME bump, a poor
  // trade for a library most sessions never touch. The cache-first rule below
  // picks it up the first time a diagram renders, which is also when it starts
  // mattering offline.
  '/static/lib/katex/katex.min.js',
  '/static/lib/katex/katex.min.css',
  ...KATEX_FONTS,
];

// Lazily-imported panel modules (js/panels.js). Not in index.html by design;
// precached so the panel still opens with no network.
const PANEL_PRECACHE = [
  // Image editor — galleryEditor.js and its js/editor/ graph.
  '/static/js/galleryEditor.js',
  '/static/js/editor/ai-inpaint.js?v=20260708match1',
  '/static/js/editor/ai-models.js',
  '/static/js/editor/ai-rembg.js',
  '/static/js/editor/ai-tool-runner.js',
  '/static/js/editor/ai-tools-misc.js',
  '/static/js/editor/build/controls.js?v=20260708match1',
  '/static/js/editor/build/popups.js',
  '/static/js/editor/build/right-panel.js',
  '/static/js/editor/build/toolbar.js?v=20260708sam3',
  '/static/js/editor/build/topbar.js',
  '/static/js/editor/build/transform-popup.js',
  '/static/js/editor/canvas-coords.js',
  '/static/js/editor/canvas-events.js',
  '/static/js/editor/canvas-transforms.js',
  '/static/js/editor/checkerboard.js',
  '/static/js/editor/clipboard-and-drop.js',
  '/static/js/editor/composite-helpers.js',
  '/static/js/editor/filters/blur.js',
  '/static/js/editor/filters/edge-feather.js',
  '/static/js/editor/fx/adj-popup.js',
  '/static/js/editor/fx/filter-string.js',
  '/static/js/editor/fx/histogram.js',
  '/static/js/editor/fx/pixel-pass.js',
  '/static/js/editor/harmonize-masks.js',
  '/static/js/editor/history-panel.js',
  '/static/js/editor/keyboard-shortcuts.js',
  '/static/js/editor/layer-helpers.js',
  '/static/js/editor/layer-panel.js',
  '/static/js/editor/mask-utils.js',
  '/static/js/editor/shortcuts-popover.js',
  '/static/js/editor/slider-ux.js',
  '/static/js/editor/snap.js',
  '/static/js/editor/state.js',
  '/static/js/editor/stroke-pipeline.js',
  '/static/js/editor/stroke-tool-sliders.js',
  '/static/js/editor/tools/clone.js',
  '/static/js/editor/tools/crop.js',
  '/static/js/editor/tools/flood-fill.js',
  '/static/js/editor/tools/lasso-mask.js',
  '/static/js/editor/tools/lasso.js',
  '/static/js/editor/tools/move.js',
  '/static/js/editor/tools/stroke.js',
  '/static/js/editor/tools/transform-drag.js',
  '/static/js/editor/tools/transform-handles.js',
  '/static/js/editor/tools/transform-session.js',
  '/static/js/editor/tools/wand.js',
  '/static/js/editor/wire-import.js',
  '/static/js/editor/wire-inpaint-controls.js?v=20260708match1',
  '/static/js/editor/wire-merge-buttons.js',
  '/static/js/editor/wire-selection-controls.js',
  '/static/js/editor/wire-topbar-menus.js',
  '/static/js/editor/wire-topbar-overflow.js',
  '/static/js/editor/wire-topbar.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      // addAll is atomic — if any item fails, none are cached. Use individual
      // puts so a single 404 can't block the whole install.
      Promise.all(
        [...PRECACHE, ...PANEL_PRECACHE].map(url =>
          fetch(url, { cache: 'reload' })
            .then(res => res.ok ? cache.put(url, res) : null)
            .catch(() => null)
        )
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // Never touch API calls or non-GET.
  if (url.pathname.startsWith('/api/') || e.request.method !== 'GET') return;

  // HTML navigation: stale-while-revalidate the app shell — but ONLY for the
  // SPA root. Other navigations (e.g. a deep-linked /static/*.html page) must
  // go to the network/static handlers below; otherwise every navigation was
  // served the app index, replacing the page the user actually asked for.
  if (e.request.mode === 'navigate' && url.pathname === '/') {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match('/');
        const network = fetch(e.request).then(res => {
          if (res && res.ok) cache.put('/', res.clone());
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // JS/CSS: network-first — always try the network so code/style edits show up
  // on a normal reload; fall back to cache only when offline.
  if (url.pathname.startsWith('/static/') && /\.(js|css)(\?|$)/.test(url.pathname + url.search)) {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, copy));
        }
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Other static assets (images, fonts, libs): cache-first with background refresh.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match(e.request);
        const fetching = fetch(e.request).then(res => {
          if (res && res.ok) cache.put(e.request, res.clone());
          return res;
        }).catch(() => cached);
        return cached || fetching;
      })
    );
    return;
  }
});
