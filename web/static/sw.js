// Offline: the files of the version you set up are served from this computer. And the
// workspace UI's requests (/api/...) never leave it either: each goes to the page that made
// it, which has the home engine answer it (boot.js, home.py).
//  - setup's own downloads (?fresh) always go to the site: they are what gets checked and kept
//  - the file list (manifest.json) comes from the site when it's reachable: it says whether
//    there's a new version
//  - the page and everything else come from the version that's set up ("current"), never a
//    mix of versions; until one is set up whole (the page among its files), from the site
"use strict";
const SHELL = "taxonymous-app-shell";
const CURRENT = "taxonymous-app-current";  // one entry: the name of the version cache in use
const BASE = new URL("./", location.href).pathname;  // where the app lives on the site (/app/)

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["./", "index.html"])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// the page is cross-origin isolated (its shared memory needs it), and then a worker only
// starts if its own script says so too: everything served from here carries the headers
function isolated(res) {
  if (!res || res.type === "error") return res;
  const headers = new Headers(res.headers);
  headers.set("Cross-Origin-Embedder-Policy", "require-corp");
  headers.set("Cross-Origin-Opener-Policy", "same-origin");
  headers.set("Cross-Origin-Resource-Policy", "same-origin");
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}

async function currentCache() {
  const r = await (await caches.open(CURRENT)).match("version");
  return r ? caches.open(await r.text()) : null;
}

async function api(e, url) {
  const client = await self.clients.get(e.clientId);
  if (!client) return new Response("no page to answer", { status: 503 });
  const body = ["GET", "HEAD"].includes(e.request.method) ? null : await e.request.arrayBuffer();
  const channel = new MessageChannel();
  const answer = new Promise((resolve) => { channel.port1.onmessage = (m) => resolve(m.data); });
  client.postMessage({ type: "api", method: e.request.method, path: url.pathname, query: url.search.slice(1),
                       headers: [...e.request.headers], body }, [channel.port2, ...(body ? [body] : [])]);
  const a = await answer;
  const empty = a.status === 204 || a.status === 304;
  return isolated(new Response(empty ? null : a.body, { status: a.status, headers: a.headers }));
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return e.respondWith(api(e, url));
  if (e.request.method !== "GET") return;
  if (!url.pathname.startsWith(BASE)) return;  // the rest of the site (the landing page)
  if (url.searchParams.has("fresh")) return;  // setup: straight to the site
  e.respondWith((async () => {
    const path = url.pathname.slice(BASE.length) || "index.html";
    const page = path === "index.html" || e.request.mode === "navigate";
    const cache = await currentCache();
    const whole = cache && await cache.match("index.html");  // a version set up with its page
    if (path !== "manifest.json" && whole) {
      const kept = page ? whole : await cache.match(path);
      if (kept) return isolated(kept);
    }
    try {
      // the page by its address (a navigation's own request can't be refetched with options)
      const live = page ? await fetch(new URL("index.html", location.href), { cache: "no-store" })
        : await fetch(e.request, path === "manifest.json" ? { cache: "no-store" } : undefined);
      if (live.ok && page) (await caches.open(SHELL)).put("index.html", live.clone());
      return page ? isolated(live) : live;
    } catch (err) {  // offline
      const kept = (cache && await cache.match(path)) || (page && await (await caches.open(SHELL)).match("index.html"));
      if (kept) return isolated(kept);
      throw err;
    }
  })());
});
