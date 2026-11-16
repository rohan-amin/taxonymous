// Taxonymous used to live at the site's root, with its offline layer registered here. It moved
// to /app/ and the root became the landing page. A browser set up before the move checks this
// file for an update and gets this one: it removes the old version's files (the old names
// only: the app's own are "taxonymous-app-..."), unregisters itself and reloads its pages, so
// they come from the site. The landing page never registers it.
"use strict";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) {
    if (k.startsWith("taxonymous-") && !k.startsWith("taxonymous-app-")) await caches.delete(k);
  }
  await self.registration.unregister();
  for (const c of await self.clients.matchAll({ type: "window" })) c.navigate(c.url);
})()));
