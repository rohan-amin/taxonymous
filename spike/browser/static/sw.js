// Offline: the files of the version you set up are served from this computer.
//  - setup's own downloads (?fresh) always go to the site: they are what gets checked and kept
//  - the page and its file list come from the site when it's reachable, from here when not
//  - everything else comes from the version that's set up ("current"), never a stale mix
"use strict";
const SHELL = "taxonymous-shell";
const CURRENT = "taxonymous-current";  // one entry: the name of the version cache in use

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["./", "index.html"])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

async function currentCache() {
  const r = await (await caches.open(CURRENT)).match("version");
  return r ? caches.open(await r.text()) : null;
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.searchParams.has("fresh")) return;  // setup: straight to the site
  e.respondWith((async () => {
    const path = url.pathname.replace(/^\//, "") || "index.html";
    if (path === "manifest.json" || path === "index.html" || e.request.mode === "navigate") {
      try {
        const live = await fetch(e.request, { cache: "no-store" });
        if (live.ok && path !== "manifest.json") (await caches.open(SHELL)).put(path, live.clone());
        return live;
      } catch { /* offline: what's kept */ }
      const cache = await currentCache();
      return (cache && await cache.match(path)) || (await caches.open(SHELL)).match(path) || Response.error();
    }
    const cache = await currentCache();
    return (cache && await cache.match(path)) || fetch(e.request);
  })());
});
