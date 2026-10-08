// static/sw.js — Odysseus PWA Service Worker
// Strategy:
//   - HTML (navigation): network-first, cache fallback. Code graph updates
//     need the current app shell on the reload the user actually performs.
//   - JS/CSS (/static/*.js|.css): network-first, cache fallback for offline.
//     (So code/style edits show up on a normal reload, no manual cache clear.)
//   - Other static assets (images/fonts/libs): cache-first with bg refresh.
//   - API / non-GET: never cached.
// Bump CACHE_NAME whenever the precache list or SW logic changes.
const CACHE_NAME = 'odysseus-v659-gallery-detail-scroll';

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
  '/static/css/00-tokens.css?v=20261007detailscroll1',
  '/static/css/01-agent-chat.css?v=20261007detailscroll1',
  '/static/css/02-compare.css?v=20261007detailscroll1',
  '/static/css/03-agent-chat.css?v=20261007detailscroll1',
  '/static/css/04-memory.css?v=20261007detailscroll1',
  '/static/css/05-documents.css?v=20261007detailscroll1',
  '/static/css/06-admin-settings.css?v=20261007detailscroll1',
  '/static/css/07-documents.css?v=20261007detailscroll1',
  '/static/css/08-skills.css?v=20261007detailscroll1',
  '/static/css/09-gallery.css?v=20261007detailscroll1',
  '/static/css/10-cookbook.css?v=20261007detailscroll1',
  '/static/css/11-tasks.css?v=20261007detailscroll1',
  '/static/css/12-gallery.css?v=20261007detailscroll1',
  '/static/css/13-image-editor.css?v=20261007detailscroll1',
  '/static/css/14-email.css?v=20261007detailscroll1',
  '/static/css/15-notes.css?v=20261007detailscroll1',
  '/static/css/16-calendar.css?v=20261007detailscroll1',
  '/static/css/17-research.css?v=20261007detailscroll1',
  '/static/css/documents-gallery-editor.css?v=20261007detailscroll1',
  '/static/css/email-calendar-notes-tasks.css?v=20261007detailscroll1',
  '/static/css/cookbook-research-memory-settings.css?v=20261007detailscroll1',
  '/static/app.js?v=20261007detailscroll1',
  '/static/js/storage.js',
  '/static/js/appConfig.js',
  '/static/js/ui.js?v=20260916largetoolscroll1',
  '/static/js/markdown.js',
  '/static/js/dragSort.js',
  '/static/js/sessions.js',
  '/static/js/memory.js',
  '/static/js/skills.js?v=20260909kebabconsistency1',
  '/static/js/skillsMetrics.js?v=20260908autonomousskills1',
  '/static/js/tourHints.js',
  '/static/js/fileHandler.js?v=20260909mobileattachmentedit1',
  '/static/js/voiceRecorder.js',
  '/static/js/actionMenuOrder.js',
  '/static/js/models.js',
  '/static/js/rag.js',
  '/static/js/presets.js?v=20260908personaname1',
  '/static/js/search.js',
  '/static/js/spinner.js',
  '/static/js/tts-ai.js',
  '/static/js/document.js?v=20260916docctx2',
  '/static/js/gallery.js?v=20260910promptcopy1',
  '/static/js/chatRenderer.js?v=20260914metricssummary1',
  '/static/js/codeRunner.js?v=20260831richtexttools91',
  '/static/js/chatStream.js?v=20260914pdfstrip1',
  '/static/js/chat.js?v=20260917toolttft1',
  '/static/js/cookbook.js',
  '/static/js/search-chat.js',
  '/static/js/compare/index.js?v=20260909mobilepaneaddscroll1',
  '/static/js/compare/vote.js?v=20260828resendcaldrag1',
  '/static/js/colorPicker.js?v=20260910eyedropper1',
  '/static/js/panels.js?v=20260909movepicklayer1',
  '/static/js/theme.js?v=20260911organsrain1',
  '/static/js/censor.js',
  '/static/js/settings.js?v=20260912writingstyle3',
  '/static/js/admin.js?v=20261007detailscroll1',
  '/static/js/chatgptSubscriptionUsage.js',
  '/static/js/init.js?v=20260829chatstyle12',
  '/static/js/slashCommands.js?v=20260921chatgptusage1',
  '/static/js/research/jobs.js?v=20260910researcherrorpersist1',
  '/static/js/emailInbox.js?v=20260914aireply4',
  '/static/js/emailLibrary/index.js',
  '/static/js/emailLibrary/aiReply.js',
  '/static/js/emailLibrary/attachments.js',
  '/static/js/emailLibrary/bodyRender.js',
  '/static/js/emailLibrary/menus.js',
  '/static/js/emailLibrary/reader.js',
  '/static/js/emailLibrary/settingsPage.js',
  '/static/js/emailLibrary/unsubscribe.js',
  '/static/js/emailLibrary/utils.js',
  '/static/js/emailLibrary/signatureFold.js',
  '/static/js/emailLibrary/state.js',
  // emailInbox.js has always imported this one; it was never precached.
  '/static/js/emailLibrary/replyRecipients.js',
  '/static/js/notes.js?v=20260911notesselectioncancel1',
  '/static/js/tasks.js?v=20260914taskmodel1',
  '/static/js/calendar.js?v=20260914emailsource11',
  '/static/js/calendar/utils.js',
  '/static/js/calendar/reminders.js',
  '/static/js/group.js',
  '/static/js/keyboard-shortcuts.js?v=20260829chatstyle12',
  '/static/js/sidebar-layout.js?v=20260910sidebarbounce1',
  '/static/js/tileManager.js?v=20260910responsivebounds1',
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
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-maskable-512.png',
];

// Lazily-imported panel modules (js/panels.js). Not in index.html by design;
// precached so the panel still opens with no network.
const PANEL_PRECACHE = [
  // Image editor — galleryEditor.js and its js/editor/ graph.
  '/static/js/galleryEditor.js?v=20260909movepicklayer1',
  '/static/js/editor/ai-inpaint.js?v=20260708match1',
  '/static/js/editor/ai-models.js',
  '/static/js/editor/ai-operation.js',
  '/static/js/editor/ai-rembg.js',
  '/static/js/editor/ai-tool-runner.js',
  '/static/js/editor/ai-tools-misc.js',
  '/static/js/editor/build/controls.js?v=20260830editor2',
  '/static/js/editor/build/popups.js',
  '/static/js/editor/build/right-panel.js',
  '/static/js/editor/build/toolbar.js?v=20260830editor2',
  '/static/js/editor/build/topbar.js',
  '/static/js/editor/build/transform-popup.js',
  '/static/js/editor/canvas-coords.js',
  '/static/js/editor/canvas-events.js',
  '/static/js/editor/canvas-navigation.js',
  '/static/js/editor/canvas-transforms.js',
  '/static/js/editor/brush-engine.js',
  '/static/js/editor/brush-presets.js',
  '/static/js/editor/checkerboard.js',
  '/static/js/editor/clipboard-and-drop.js',
  '/static/js/editor/composite-helpers.js',
  '/static/js/editor/document-codec.js',
  '/static/js/editor/adjustment-layer.js',
  '/static/js/editor/adjustments-worker.js',
  '/static/js/editor/thumbnail-worker.js',
  '/static/js/editor/serialization-worker.js',
  '/static/js/editor/effects.js',
  '/static/js/editor/gradient-stops.js',
  '/static/js/editor/render-cancellation.js',
  '/static/js/editor/effects-worker.js',
  '/static/js/editor/document-geometry.js',
  '/static/js/editor/export-dialog.js',
  '/static/js/editor/selection-mask.js',
  '/static/js/editor/selection-modifiers.js',
  '/static/js/editor/tool-shortcuts.js',
  '/static/js/editor/filters/blur.js',
  '/static/js/editor/filters/edge-feather.js',
  '/static/js/editor/fx/adj-popup.js',
  '/static/js/editor/fx/filter-string.js',
  '/static/js/editor/fx/histogram.js',
  '/static/js/editor/fx/pixel-pass.js',
  '/static/js/editor/harmonize-masks.js',
  '/static/js/editor/history-panel.js',
  '/static/js/editor/history-budget.js',
  '/static/js/editor/keyboard-shortcuts.js',
  '/static/js/editor/layer-helpers.js',
  '/static/js/editor/layer-groups.js',
  '/static/js/editor/layer-clipping.js',
  '/static/js/editor/multi-transform.js',
  '/static/js/editor/direct-manipulation-session.js',
  '/static/js/editor/placed-layer.js',
  '/static/js/editor/layer-selection.js',
  '/static/js/editor/layer-geometry.js',
  '/static/js/editor/precision-guides.js',
  '/static/js/editor/layer-panel.js',
  '/static/js/editor/layer-style-menu.js',
  '/static/js/editor/layer-styles.js',
  '/static/js/editor/mask-utils.js',
  '/static/js/editor/shortcuts-popover.js',
  '/static/js/editor/slider-ux.js',
  '/static/js/editor/snap.js',
  '/static/js/editor/state.js',
  '/static/js/editor/stroke-pipeline.js',
  '/static/js/editor/stroke-tool-sliders.js',
  '/static/js/editor/text-layer.js',
  '/static/js/editor/text-edit-overlay.js',
  '/static/js/editor/shape-layer.js',
  '/static/js/editor/transform-frame-geometry.js',
  '/static/js/editor/tools/clone.js',
  '/static/js/editor/tools/eyedropper.js',
  '/static/js/editor/tools/crop.js',
  '/static/js/editor/tools/flood-fill.js',
  '/static/js/editor/tools/gradient.js',
  '/static/js/editor/tools/lasso-mask.js',
  '/static/js/editor/tools/lasso.js',
  '/static/js/editor/tools/marquee.js',
  '/static/js/editor/tools/move.js',
  '/static/js/editor/tools/pen-selection.js',
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
  '/static/js/editor/wire-view-menu.js',
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

  // HTML navigation: network-first app shell — but ONLY for the SPA root.
  // Other navigations (e.g. a deep-linked /static/*.html page) must go to the
  // network/static handlers below; otherwise every navigation was served the
  // app index, replacing the page the user actually asked for.
  if (e.request.mode === 'navigate' && url.pathname === '/') {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match('/');
        return fetch(e.request).then(res => {
          if (res && res.ok) cache.put('/', res.clone());
          return res;
        }).catch(() => cached);
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
