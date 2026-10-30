// Keeps the panel's page shell so it opens quickly and installs on a phone. It never answers /api, /login or /logout from a cache.
const SHELL = "minty-shell-v9";
const FILES = ["/", "/static/app.css", "/static/app.js", "/static/lib.js", "/static/events.js", "/static/runner.js", "/static/palette.js", "/static/pinpad.js", "/static/statusbar.js", "/static/settings-schema.js", "/static/icon.svg", "/static/fonts/Antonio.woff2"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api") || url.pathname === "/login" || url.pathname === "/logout") return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok && (url.pathname === "/" || url.pathname.startsWith("/static/"))) {
          const copy = response.clone();
          caches.open(SHELL).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request)),
  );
});
