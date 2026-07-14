// static/sw.js — TITAN PWA Service Worker
// Dev-friendly: NEVER cache JS/CSS/HTML — always load fresh from network.
// Only cache immutable assets (icons/fonts) for offline shell.
// Bump SW_VERSION when SW logic changes (triggers client-side purge).
const SW_VERSION = '400';
const CACHE_NAME = `titan-v${SW_VERSION}`;
const ASSET_CACHE = `titan-assets-v${SW_VERSION}`;

self.addEventListener('install', (e) => {
  e.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== ASSET_CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

function isFreshAsset(pathname) {
  return /\.(js|css|html?|map)(\?|$)/i.test(pathname);
}

function isImmutableAsset(pathname) {
  return /\.(png|jpe?g|gif|webp|ico|svg|woff2?|ttf|eot)(\?|$)/i.test(pathname);
}

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/') || e.request.method !== 'GET') return;

  // JS/CSS/HTML — pass through to network, never intercept (no stale cache).
  if (e.request.mode === 'navigate' || isFreshAsset(url.pathname)) return;

  if (url.pathname.startsWith('/static/') && isImmutableAsset(url.pathname)) {
    e.respondWith(
      caches.open(ASSET_CACHE).then(async (cache) => {
        const cached = await cache.match(e.request);
        const network = fetch(e.request).then((res) => {
          if (res && res.ok) cache.put(e.request, res.clone());
          return res;
        });
        return cached || network;
      })
    );
  }
});
