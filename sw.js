const CACHE = 'aetheria-v5-seed-save-slots-20260811';
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
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(cacheKey, copy));
        }
        return response;
      })
      .catch(async () => (await caches.match(cacheKey)) || caches.match('./index.html')),
  );
});
