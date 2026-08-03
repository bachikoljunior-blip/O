// オフライン対応（キャッシュファースト、更新はバックグラウンドで取得）

const CACHE = 'aetheria-v32';
const ASSETS = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icon.svg',
  './styles/main.css',
  './src/main.js',
  './src/core/math.js',
  './src/core/noise.js',
  './src/core/gl.js',
  './src/core/input.js',
  './src/core/audio.js',
  './src/world/worldgen.js',
  './src/world/terrain.js',
  './src/world/pois.js',
  './src/world/sky.js',
  './src/world/dungeon.js',
  './src/render/shaders.js',
  './src/render/mesh.js',
  './src/render/instance.js',
  './src/render/renderer.js',
  './src/render/camera.js',
  './src/render/rig.js',
  './src/render/fx.js',
  './src/game/game.js',
  './src/game/data.js',
  './src/game/actor.js',
  './src/game/player.js',
  './src/game/enemies.js',
  './src/game/npc.js',
  './src/game/quests.js',
  './src/game/dialogue.js',
  './src/game/mount.js',
  './src/ui/ui.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(ASSETS))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  e.respondWith(
    caches.match(req).then((hit) => {
      const net = fetch(req)
        .then((res) => {
          if (res && res.ok && new URL(req.url).origin === location.origin) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => hit);
      return hit || net;
    }),
  );
});
