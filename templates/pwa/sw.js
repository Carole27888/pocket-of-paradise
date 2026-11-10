/* Pocket of Paradise Travel Company service worker — hand written, no Workbox. Version {{ version }} */
const VERSION = "{{ version|escapejs }}";
const CACHE = "ds-shell-" + VERSION;
const STATIC_PREFIX = "{{ static_prefix|escapejs }}";
const PRECACHE = {{ precache|safe }};
const OFFLINE_URL = "/offline/";
// Never cache: auth, admin, API, exports/PDFs, media (personal/payment data) and non-GET requests.
const NEVER = [/^\/accounts\/(login|logout)/, /^\/django-admin\//, /^\/api\//, /^\/media\//, /[?&]export=/, /\.pdf($|\?)/, /\/download\//, /^\/notifications\/push\//];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      Promise.all(PRECACHE.map((url) => cache.add(new Request(url, { cache: "reload" })).catch(() => null)))
    )
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("ds-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;             // map tiles etc. go straight to network
  if (NEVER.some((re) => re.test(url.pathname + url.search))) return;

  // Static files: cache first.
  if (url.pathname.startsWith(STATIC_PREFIX)) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      }))
    );
    return;
  }

  // Pages and HTMX requests: network first, offline page for navigations. Pages are not stored.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (req.headers.get("HX-Request")) {
    event.respondWith(fetch(req).catch(() => new Response(
      '<div class="alert alert-warning">You are offline. Check your connection.</div>',
      { status: 200, headers: { "Content-Type": "text/html" } })));
  }
});

// Web push: payload {title, body, url}
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = { title: "Pocket of Paradise", body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "Pocket of Paradise", {
    body: data.body || "", data: { url: data.url || "/" },
    icon: STATIC_PREFIX + "pwa/icon-192.png", badge: STATIC_PREFIX + "pwa/icon-96.png",
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ("focus" in c) { c.navigate(target); return c.focus(); } }
    return self.clients.openWindow(target);
  }));
});
