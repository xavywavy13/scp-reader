// SCP Field Reader service worker: app shell offline, article data cached as you read.
const SHELL = "shell-v20";
const DATA = "data-v3";      // bump when the article archive is rebuilt
const FONTS = "fonts-v1";
const IMG = "img-v1";        // pictures saved for offline reading (filled by the app, not here)
const SHELL_FILES = ["./", "./index.html", "./manifest.webmanifest", "./icon-192.png", "./apple-touch-icon.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  const keep = [SHELL, DATA, FONTS, IMG];
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => !keep.includes(k)).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Article archive: cache first, it never changes within a data version.
  // The article list, canon list and 001-proposal chunk can be refreshed by GitHub Actions,
  // so check the network for those first and fall back to the saved copy offline.
  if (url.origin === location.origin && /\/data\/(index\.gz|canons\.gz|links\.gz|audio\.gz|threads\.gz|c\/900\.gz)$/.test(url.pathname)) {
    e.respondWith(fetch(req, {cache: "no-cache"}).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(DATA).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req, {ignoreSearch: true})));
    return;
  }
  if (url.origin === location.origin && url.pathname.includes("/data/")) {
    e.respondWith(caches.open(DATA).then(async c => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) {
        const key = url.searchParams.has("fresh") ? req.url.replace(/&fresh=\d+/, "") : req.url;
        await c.put(key, res.clone());
        // drop older versions of the same chunk so the offline store doesn't grow
        for (const k of await c.keys()) {
          const ku = new URL(k.url);
          if (ku.pathname === url.pathname && k.url !== key) c.delete(k);
        }
      }
      return res;
    }));
    return;
  }
  // App shell: network first so updates arrive, cache when offline.
  if (url.origin === location.origin) {
    e.respondWith(fetch(req, {cache: "no-store"}).then(res => {
      if (res.ok) caches.open(SHELL).then(c => c.put(req, res.clone()));
      return res;
    }).catch(() => caches.match(req).then(r => r || caches.match("./index.html"))));
    return;
  }
  // Google Fonts: stale while revalidate.
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    e.respondWith(caches.open(FONTS).then(async c => {
      const hit = await c.match(req);
      const net = fetch(req).then(res => { c.put(req, res.clone()); return res; }).catch(() => hit);
      return hit || net;
    }));
  }
  // Wiki pictures: use the saved copy when the app has saved one, otherwise the network.
  if (req.destination === "image" && url.origin !== location.origin) {
    e.respondWith(caches.open(IMG).then(async c => (await c.match(req.url)) || fetch(req)));
  }
});
