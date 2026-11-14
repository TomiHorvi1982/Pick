const C = "pick-jam-v5";
// the songs the friend has opened (their tracks, text, chords, tabs) — kept across new versions of the page, so he
// can play them on his own even when the host's computer is off. The oldest go once there are more than SONGS_MAX.
const SONGS = "pick-jam-songs";
const SONGS_MAX = 25;
const FILES = ["/app", "/static/jam-engine.js", "/static/jam-chat.js", "/static/jam-mix.js", "/static/jam-amp.js", "/static/jam-guest.js", "/static/jam-sonobus.js"];
const SONG_PATHS = ["/g/song", "/g/lyrics", "/g/beats", "/g/tabs", "/g/tabfile", "/g/stem", "/g/sections"];
// kept at once, so the very next visit already opens without the host
self.addEventListener("install", (e) => { self.skipWaiting(); e.waitUntil(caches.open(C).then(c => Promise.all(FILES.map(f => c.add(f).catch(() => {}))))); });
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== C && k !== SONGS).map(k => caches.delete(k)))).then(() => self.clients.claim())));
const keep = (key, res) => { if(res && res.ok){ const copy = res.clone(); caches.open(C).then(c => c.put(key, copy)); } return res; };
// a song's file by what it is, without the room's key (it changes from jam to jam)
function songKey(u){ const q = new URLSearchParams(u.search); q.delete("k"); q.sort(); return new Request(u.origin + u.pathname + "?" + q.toString()); }
async function trim(){
  const c = await caches.open(SONGS), ks = await c.keys(), order = [];
  ks.forEach(r => { const n = new URL(r.url).searchParams.get("name"); if(n && !order.includes(n)) order.push(n); });
  const drop = new Set(order.slice(0, Math.max(0, order.length - SONGS_MAX)));
  if(drop.size) await Promise.all(ks.filter(r => drop.has(new URL(r.url).searchParams.get("name"))).map(r => c.delete(r)));
}
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if(e.request.method !== "GET" || u.origin !== location.origin || u.pathname === "/ws") return;
  if(SONG_PATHS.includes(u.pathname)){
    const key = songKey(u);
    e.respondWith(fetch(e.request).then(r => {
      if(r.ok){ const copy = r.clone(); caches.open(SONGS).then(c => c.put(key, copy)).then(() => u.pathname === "/g/stem" && trim()).catch(() => {}); return r; }
      return r.status >= 500 || r.status === 403 ? caches.match(key, {cacheName: SONGS}).then(m => m || r) : r;
    }).catch(() => caches.match(key, {cacheName: SONGS}).then(m => m || new Response(JSON.stringify({error: "Offline — tahle píseň není uložená."}), {status: 503, headers: {"Content-Type": "application/json"}}))));
    return;
  }
  const page = e.request.mode === "navigate", stat = u.pathname.startsWith("/static/");
  if(!(page || stat || ["/g/songs", "/g/playlists", "/g/art"].includes(u.pathname))) return;
  const key = page ? new Request(u.pathname) : stat ? new Request(u.pathname) : e.request;
  const kept = () => caches.match(key, {ignoreSearch: page || stat});
  // the network first (a 5xx is Cloudflare saying the Mac is away), what was kept when that fails
  e.respondWith(fetch(e.request).then(r => r.status >= 500 ? kept().then(m => m || r) : keep(key, r))
    .catch(() => kept().then(m => m || new Response("offline", {status: 503}))));
});
