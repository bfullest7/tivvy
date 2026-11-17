// Background helper: whenever the app is opened, get the newest page from the
// server instead of the phone's saved copy (which can be up to 10 minutes old).
// If the phone is offline, fall back to the last copy we saw.
const CACHE = "wtw-pages";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// Posters and cast photos: iPhone doesn't reliably keep them between launches, so they were
// downloaded again every time the app opened (1-2 seconds). Keep our own copy: the first
// download is saved, and later launches get it instantly. The newest ~600 are kept.
const POSTERS = "tivvy-posters";
const POSTER_HOST = /^https:\/\/image\.tmdb\.org\/t\/p\//;
const MAX_POSTERS = 600;
let savedSinceTrim = 0;
let cannotSave = false; // the poster server refused a saved copy once: stop trying until the app restarts
async function trimPosters(cache) {
  const keys = await cache.keys(); // oldest first
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_POSTERS))) await cache.delete(k);
}
async function poster(e) {
  const url = e.request.url;
  let cache;
  try {
    cache = await caches.open(POSTERS);
    const hit = await cache.match(url);
    if (hit) return hit;
  } catch { /* no storage: just download */ }
  if (cannotSave) return fetch(e.request);
  try {
    const res = await fetch(url, { mode: "cors", credentials: "omit" });
    if (res.ok && cache) {
      const copy = res.clone();
      e.waitUntil(cache.put(url, copy).then(() => (++savedSinceTrim >= 50 ? (savedSinceTrim = 0, trimPosters(cache)) : null)).catch(() => {}));
    }
    return res;
  } catch {
    cannotSave = true;
    return fetch(e.request); // the server didn't allow a saved copy: load it the usual way
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method === "GET" && POSTER_HOST.test(req.url)) { e.respondWith(poster(e)); return; }
  if (req.mode !== "navigate" || req.method !== "GET") return; // everything else as normal
  e.respondWith((async () => {
    try {
      // "no-cache" = always check with the server (cheap if nothing changed)
      // (a navigation request can't be re-issued with options, so fetch by address)
      let res = await fetch(req.url, { cache: "no-cache", credentials: "same-origin" });
      // Safari refuses a redirected response for a page load; hand back a clean copy
      if (res.redirected) res = new Response(await res.blob(), { status: res.status, statusText: res.statusText, headers: res.headers });
      if (res.ok) {
        const copy = res.clone();
        e.waitUntil(caches.open(CACHE).then((c) => c.put("page", copy)));
      }
      return res;
    } catch (err) {
      const saved = await (await caches.open(CACHE)).match("page");
      if (saved) return saved;
      throw err;
    }
  })());
});
