const CACHE = 'aetheria-v18-resilient-backup-load-20260811';
const NETWORK_TIMEOUT_MS = 3500;
const CORE = [
  './',
  './index.html',
  './styles/main.css',
  './manifest.webmanifest',
  './icon.svg',
  './icon-180.png',
  './icon-192.png',
  './icon-512.png',
  './src/main.js',
  './src/core/audio.js',
  './src/core/input.js',
  './src/core/seed.js',
  './src/core/util.js',
  './src/game/dialogue.js',
  './src/game/achievements.js',
  './src/game/entities.js',
  './src/game/game.js',
  './src/game/items.js',
  './src/game/quests.js',
  './src/render/actors.js',
  './src/render/buildings.js',
  './src/render/icons.js',
  './src/render/particles.js',
  './src/render/props.js',
  './src/render/renderer.js',
  './src/render/tiles.js',
  './src/ui/hud.js',
  './src/ui/menus.js',
  './src/ui/ui.js',
  './src/world/dungeon.js',
  './src/world/runtime.js',
  './src/world/worldgen.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function fetchWithTimeout(request, timeoutMs = NETWORK_TIMEOUT_MS) {
  const controller = new AbortController();
  let timer;
  const timeout = new Promise((resolve, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error('network timeout'));
    }, timeoutMs);
  });
  // The explicit race matters on WebKit builds where aborting a service-worker
  // fetch does not always settle the fetch promise until the server responds.
  return Promise.race([fetch(request, { signal: controller.signal }), timeout])
    .finally(() => clearTimeout(timer));
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  const scopePath = new URL(self.registration.scope).pathname;
  const relativePath = url.pathname.startsWith(scopePath) ? url.pathname.slice(scopePath.length) : '';
  // The repository also hosts a separate /hoshikuzu demo. Never rewrite its navigation.
  if (request.mode === 'navigate' && relativePath && relativePath !== 'index.html') return;
  const cacheKey = request.mode === 'navigate'
    ? new URL('./index.html', self.registration.scope).href
    : request;
  event.respondWith(
    caches.match(cacheKey)
      .then(async (cached) => {
        // CORE is populated atomically under a versioned cache during install,
        // so a cached module belongs to the same release as every dependency.
        // Return it immediately instead of stacking a timeout at each level of
        // the ES-module graph. Navigations still make a bounded freshness check.
        if (cached && request.mode !== 'navigate') return cached;
        try {
          const response = await (cached ? fetchWithTimeout(request) : fetch(request));
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(cacheKey, copy));
          }
          return response;
        } catch (error) {
          // Never put a deadline on an uncached first visit. On later launches,
          // however, weak mobile data must not leave a fully cached game hanging
          // indefinitely. Fall back after a short bound and keep the app playable.
          return cached || (request.mode === 'navigate'
            ? caches.match('./index.html')
            : Response.error());
        }
      }),
  );
});
