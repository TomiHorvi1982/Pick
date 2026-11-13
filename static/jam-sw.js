const C = "pick-jam-v4";
const FILES = ["/app", "/static/jam-engine.js", "/static/jam-chat.js", "/static/jam-mix.js", "/static/jam-amp.js", "/static/jam-guest.js"];
// kept at once, so the very next visit already opens without the host
self.addEventListener("install", (e) => { self.skipWaiting(); e.waitUntil(caches.open(C).then(c => Promise.all(FILES.map(f => c.add(f).catch(() => {}))))); });
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k)))).then(() => self.clients.claim())));
const keep = (key, res) => { if(res && res.ok){ const copy = res.clone(); caches.open(C).then(c => c.put(key, copy)); } return res; };
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if(e.request.method !== "GET" || u.origin !== location.origin || u.pathname === "/ws") return;
  const page = e.request.mode === "navigate", stat = u.pathname.startsWith("/static/");
  if(!(page || stat || ["/g/songs", "/g/playlists", "/g/art"].includes(u.pathname))) return;
  const key = page ? new Request(u.pathname) : stat ? new Request(u.pathname) : e.request;
  const kept = () => caches.match(key, {ignoreSearch: page || stat});
  // the network first (a 5xx is Cloudflare saying the Mac is away), what was kept when that fails
  e.respondWith(fetch(e.request).then(r => r.status >= 500 ? kept().then(m => m || r) : keep(key, r))
    .catch(() => kept().then(m => m || new Response("offline", {status: 503}))));
});
