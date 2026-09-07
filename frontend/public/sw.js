// Minimal service worker — exists mainly to satisfy PWA installability, not to
// cache the app. Prices, orders and auth state change too often (and auth
// rides on HttpOnly cookies) to risk ever serving them stale, so this SW never
// caches a page or an /api/ response. It only pre-caches the offline fallback
// and static icons, and falls back to that page when navigation has no network.

const CACHE = "smart-orders-shell-v1";
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  // Page navigations: go to the network; only reach for the cached offline
  // page if the network is actually unreachable.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  // Everything else (API calls, data fetches) is left untouched — this SW
  // does not intercept them, so they always hit the network as normal.
});
