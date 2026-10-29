// The installed app's service worker. It caches nothing but the page below.
//
// Every request goes to the server, always: a cached copy of the app would keep
// running after an update, which is the stale-build problem the server's
// no-cache headers exist to prevent. The one thing it adds is an honest page
// when the server cannot be reached - a phone away from home, or Tailscale off -
// instead of the browser's own error.
const OFFLINE = '/offline.html';
const CACHE = 'nice-assistant-offline-v1';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.add(OFFLINE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  // Only opening a page; everything the app asks for while running is untouched.
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)));
});
