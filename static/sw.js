// static/sw.js — Odysseus PWA Service Worker
// Strategy:
//   - HTML (navigation): stale-while-revalidate. Instant open from cache,
//     background refresh so the next open has latest HTML.
//   - JS/CSS (/static/*.js|.css): network-first, cache fallback for offline.
//     (So code/style edits show up on a normal reload, no manual cache clear.)
//   - Other static assets (images/fonts/libs): cache-first with bg refresh.
//   - API / non-GET: never cached.
// Bump CACHE_NAME whenever the precache list or SW logic changes.
const CACHE_NAME = 'odysseus-v327';

// Derive base path from URL for subdirectory support
const _basePath = (() => {
  const p = location.pathname;
  if (p.endsWith('/sw.js')) {
    return p.substring(0, p.length - '/sw.js'.length);
  }
  return '';
})();

// Shorthand for paths that include base path
const _bp = (p) => _basePath + p;

// Precache paths with base path for subdirectory support
const PRECACHE = [
  _bp('/'),
  _bp('/static/style.css'),
  _bp('/static/app.js'),
  _bp('/static/js/base-path.js'),
  _bp('/static/js/storage.js'),
  _bp('/static/js/ui.js'),
  _bp('/static/js/markdown.js'),
  _bp('/static/js/dragSort.js'),
  _bp('/static/js/sessions.js'),
  _bp('/static/js/memory.js'),
  _bp('/static/js/skills.js'),
  _bp('/static/js/tourHints.js'),
  _bp('/static/js/fileHandler.js'),
  _bp('/static/js/voiceRecorder.js'),
  _bp('/static/js/models.js'),
  _bp('/static/js/rag.js'),
  _bp('/static/js/presets.js'),
  _bp('/static/js/search.js'),
  _bp('/static/js/spinner.js'),
  _bp('/static/js/tts-ai.js'),
  _bp('/static/js/document.js'),
  _bp('/static/js/gallery.js'),
  _bp('/static/js/chatRenderer.js'),
  _bp('/static/js/codeRunner.js'),
  _bp('/static/js/chatStream.js'),
  _bp('/static/js/chat.js'),
  _bp('/static/js/cookbook.js'),
  _bp('/static/js/search-chat.js'),
  _bp('/static/js/compare/index.js'),
  _bp('/static/js/theme.js'),
  _bp('/static/js/censor.js'),
  _bp('/static/js/settings.js'),
  _bp('/static/js/admin.js'),
  _bp('/static/js/init.js'),
  _bp('/static/js/slashCommands.js'),
  _bp('/static/js/emailInbox.js'),
  _bp('/static/js/emailLibrary/utils.js'),
  _bp('/static/js/emailLibrary/signatureFold.js'),
  _bp('/static/js/emailLibrary/state.js'),
  _bp('/static/js/notes.js'),
  _bp('/static/js/tasks.js'),
  _bp('/static/js/calendar.js'),
  _bp('/static/js/calendar/utils.js'),
  _bp('/static/js/calendar/reminders.js'),
  _bp('/static/js/group.js'),
  _bp('/static/js/keyboard-shortcuts.js'),
  _bp('/static/js/sidebar-layout.js'),
  _bp('/static/js/section-management.js'),
  _bp('/static/lib/highlight.min.js'),
];

// Root paths used in cache matching
const _root = (p) => p === '/' ? '/' : p;  // '' becomes '/', '/odysseus' stays
const _staticBase = _bp('/static/');

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      // addAll is atomic — if any item fails, none are cached. Use individual
      // puts so a single 404 can't block the whole install.
      Promise.all(
        PRECACHE.map(url =>
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
  // Uses base path to support subdirectory deployments.
  const rootPath = _bp('/');
  if (e.request.mode === 'navigate' && (url.pathname === '/' || url.pathname === rootPath || url.pathname === _basePath || url.pathname === _basePath + '/')) {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match(rootPath);
        const network = fetch(e.request).then(res => {
          if (res && res.ok) cache.put(rootPath, res.clone());
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // JS/CSS: network-first — always try the network so code/style edits show up
  // on a normal reload; fall back to cache only when offline.
  // Uses base path to support subdirectory deployments.
  if (url.pathname.startsWith(_staticBase) && /\.(js|css)(\?|$)/.test(url.pathname + url.search)) {
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
  // Uses base path to support subdirectory deployments.
  if (url.pathname.startsWith(_staticBase)) {
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