// NMMS service worker — enables "Add to Home Screen" / installable app behavior
// on iOS, Android, iPad and desktop. Kept deliberately light: this app is
// data-live (orders, payments, meal cutoffs) so we do NOT cache HTML pages or
// API responses — only static, versioned assets that never change per-request.
const CACHE_VERSION = 'nmms-static-v1';
const PRECACHE_URLS = [
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Only handle same-origin GET requests for static assets. Everything else
  // (pages, /student/*, /manager/*, /admin/*, payment callbacks, POSTs) goes
  // straight to the network untouched so data is always fresh and correct.
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith('/static/')) return;

  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
