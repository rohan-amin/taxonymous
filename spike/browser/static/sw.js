// Offline: what setup kept is served from this computer; the page itself is kept as it loads.
"use strict";
const SHELL = "taxonymous-shell";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["./", "index.html"])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET" || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith((async () => {
    const path = new URL(e.request.url).pathname.replace(/^\//, "") || "index.html";
    if (path === "manifest.json") {  // the newest when online (an update), the kept one when not
      try { return await fetch(e.request, { cache: "no-store" }); } catch { /* offline */ }
    }
    const kept = await caches.match(path, { ignoreSearch: true });
    return kept || fetch(e.request);
  })());
});
