// trav1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/* ---------- 1. 街道の折れ線が残っている ---------- */
const routes = await page.evaluate(() => {
  const g = window.__game;
  const R = g.world.routes || [];
  const lens = R.map(r => Math.round(r.length)).sort((a,b)=>a-b);
  // 折れ線が本当に道の上にあるか（道の濃さを見る）
  let on = 0, off = 0;
  for (const r of R) for (const pt of r.pts) {
    (g.world.roadAt(pt.x, pt.z) > 0.3 ? on++ : off++);
  }
  return { n: R.length, pts: R.reduce((s,r)=>s+r.pts.length,0),
    minLen: lens[0], medLen: lens[lens.length>>1], maxLen: lens[lens.length-1],
    on, off, sample: R.slice(0,3).map(r=>`${r.fromName}→${r.toName}(${Math.round(r.length)}m,${r.pts.length}点)`) };
});
console.log(`街道 ${routes.n} 本 / 節点 ${routes.pts}　長さ ${routes.minLen}〜${routes.maxLen}m（中央 ${routes.medLen}）`);
console.log('  例:', routes.sample.join(' / '));
console.log(`  節点が道の上にある: ${routes.on} / ${routes.on + routes.off}`);
ok('街道の経路が残っている', routes.n > 5, `${routes.n} 本`);
ok('経路の点は本当に道の上にある',
  routes.on / (routes.on + routes.off) > 0.9,
  `${Math.round(routes.on/(routes.on+routes.off)*100)}%`);
ok('拠点と拠点を結んでいる', routes.medLen > 80 && routes.maxLen <= 900,
  `${routes.minLen}〜${routes.maxLen}m`);

/* ---------- 2. 旅人が湧いて歩く ---------- */
const walk = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  g.sky.skipToHour(11); g.sky.update(1/60, g);
  g.sky.rainAmount = 0;
  g.travellers.list.length = 0;
  g.travellers.cd = 0;
  // 街道のそばに立つ
  const r = g.world.routes.find(rr => rr.pts.length > 6);
  const at = r.pts[2];
  p.x = at.x; p.z = at.z; p.y = g.groundHeight(at.x, at.z);
  let spawned = 0, maxAlive = 0;
  const dists = [];
  for (let i = 0; i < 60 * 120; i++) {
    const before = g.travellers.count;
    g.travellers.update(1/60, g);
    if (g.travellers.count > before) {
      spawned++;
      const t = g.travellers.list[g.travellers.list.length - 1];
      dists.push(+Math.hypot(t.x - p.x, t.z - p.z).toFixed(0));
    }
    maxAlive = Math.max(maxAlive, g.travellers.count);
  }
  // いま居る者を 60 秒歩かせて移動距離を見る
  const alive = g.travellers.list.slice();
  const before = alive.map(t => ({ x: t.x, z: t.z, seg: t.seg }));
  let path = alive.map(() => 0);
  for (let i = 0; i < 60 * 60; i++) {
    const prev = alive.map(t => ({ x: t.x, z: t.z }));
    g.travellers.update(1/60, g);
    alive.forEach((t, k) => { path[k] += Math.hypot(t.x - prev[k].x, t.z - prev[k].z); });
  }
  const moved = alive.map((t, k) => ({
    kind: t.kind, name: t.npcName, dest: t.destination,
    path: +path[k].toFixed(1), segFrom: before[k].seg, segTo: t.seg,
    still: g.travellers.list.includes(t),
    onRoad: g.world.roadAt(t.x, t.z) > 0.2 })).filter(m => m.still);
  return { spawned, maxAlive, dists, moved };
});
console.log(`2 分で ${walk.spawned} 人が湧いた（同時最大 ${walk.maxAlive} 人）　湧いた距離: ${walk.dists.join(',')}m`);
for (const m of walk.moved)
  console.log(`  ${m.name}（${m.kind}）→ ${m.dest}　60秒で ${m.path}m　区間 ${m.segFrom}→${m.segTo}　道の上 ${m.onRoad}`);
ok('街道に旅人が現れる', walk.spawned > 0, `${walk.spawned}人`);
ok('同時に出過ぎない', walk.maxAlive <= 3, `最大 ${walk.maxAlive}人`);
ok('姿が見えない距離から歩いてくる',
  walk.dists.every(d => d >= 105), `最短 ${Math.min(...walk.dists)}m`);
ok('残っている旅人は足踏みしていない',
  walk.moved.every(m => m.path > 15),
  walk.moved.length ? walk.moved.map(m=>`${m.path}m`).join(' ') : '（残らず）');
ok('60 秒でまとまった距離を歩く',
  walk.moved.length === 0 || walk.moved.some(m => m.path > 50),
  walk.moved.length ? `最長 ${Math.max(...walk.moved.map(m=>m.path))}m` : '（残らず）');
ok('道の上を進む',
  walk.moved.filter(m => m.onRoad).length >= walk.moved.length * 0.6,
  `${walk.moved.filter(m=>m.onRoad).length}/${walk.moved.length}`);
ok('進んだぶんだけ区間が進む（立ち止まって目印だけ送らない）',
  walk.moved.every(m => m.segTo === m.segFrom || m.path > (Math.abs(m.segTo - m.segFrom)) * 8),
  walk.moved.map(m=>`${m.segFrom}→${m.segTo}(${m.path}m)`).join(' ') || '（残らず）');

/* ---------- 3. 声をかけられる ---------- */
const talk = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  if (!g.travellers.count) return { none: true };
  // いちばん近い者が選ばれるので、選ばれた本人を見る
  const first = g.travellers.list[0];
  p.x = first.x + 1.5; p.z = first.z; p.y = g.groundHeight(p.x, p.z);
  g.updateInteract();
  const it = g.interact;
  const t = (it && it.type === 'traveller') ? it.obj : first;
  const toasts = [];
  const oToast = g.ui.toast.bind(g.ui);
  g.ui.toast = (m, c) => { toasts.push(m); return oToast(m, c); };
  const x0 = t.x, z0 = t.z;
  if (it && it.type === 'traveller') g.doInteract(it);
  const talking = t.talking;
  for (let i = 0; i < 60; i++) g.travellers.update(1/60, g);
  const stayed = Math.hypot(t.x - x0, t.z - z0) < 0.6;
  g.ui.toast = oToast;
  return { label: it?.label, type: it?.type, toasts, talking, stayed };
});
console.log('声をかける:', JSON.stringify(talk));
if (!talk.none) {
  ok('旅人に声をかけられる', talk.type === 'traveller', String(talk.type));
  ok('名を呼んで案内が出る', /に声をかける/.test(talk.label || ''), String(talk.label));
  ok('一言返ってくる', talk.toasts.some(m => m.includes('「')), talk.toasts[0] || '');
  ok('行き先を言う', talk.toasts.some(m => m.includes('へ向かう')), talk.toasts[1] || '');
  ok('話しているあいだは足を止める', talk.talking && talk.stayed, `${talk.talking}/${talk.stayed}`);
}

/* ---------- 4. 敵から逃げる ---------- */
const flee = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  if (!g.travellers.count) return { none: true };
  const t = g.travellers.list[0];
  t.talking = false; t.fleeing = 0;
  const dir0 = t.dir;
  g.enemies.length = 0;
  // 旅人が向かっている先に敵を置く（横に置くと、引き返しても距離が縮む）
  const w = t.waypoint();
  const ax = w.x - t.x, az = w.z - t.z, al = Math.hypot(ax, az) || 1;
  const ex = t.x + ax / al * 14, ez = t.z + az / al * 14;
  const e = spawnEnemy('bandit', { x: ex, y: g.groundHeight(ex, ez), z: ez,
    yaw: 0, leash: 900 });
  e.aggro = true;
  g.enemies.push(e);
  const x0 = t.x, z0 = t.z;
  const d0 = Math.hypot(e.x - t.x, e.z - t.z);
  for (let i = 0; i < 60 * 5; i++) { g.travellers.update(1/60, g); }
  const d1 = Math.hypot(e.x - t.x, e.z - t.z);
  const moved = Math.hypot(t.x - x0, t.z - z0);
  g.enemies.length = 0;
  return { dir0, dir1: t.dir, fleeing: t.fleeing > 0, d0: +d0.toFixed(1), d1: +d1.toFixed(1),
    moved: +moved.toFixed(1) };
});
console.log('敵を見たら:', JSON.stringify(flee));
if (!flee.none) {
  ok('敵を見ると引き返す', flee.dir1 === -flee.dir0, `${flee.dir0} → ${flee.dir1}`);
  ok('逃げの状態になる', flee.fleeing);
  ok('実際に距離を取る', flee.d1 > flee.d0, `${flee.d0} → ${flee.d1}m`);
}

/* ---------- 5. 夜は往来が絶える ---------- */
const night = await page.evaluate(async () => {
  const g = window.__game;
  const runs = (h) => {
    g.travellers.list.length = 0;
    g.sky.skipToHour(h); g.sky.update(1/60, g);
    g.sky.rainAmount = 0;
    g.travellers.cd = 0;
    // 上限（同時 3 人）で頭打ちにならないよう、湧いたそばから畳んで
    // 「湧いた回数」そのものを数える
    let n = 0;
    for (let i = 0; i < 60 * 300; i++) {
      const b = g.travellers.count;
      g.travellers.update(1/60, g);
      if (g.travellers.count > b) { n++; g.travellers.list.length = 0; }
    }
    return n;
  };
  const day = runs(12);
  const nite = runs(2);
  g.sky.skipToHour(12); g.sky.update(1/60, g);
  // 地下では消える
  g.travellers.list.length = 0; g.travellers.cd = 0;
  for (let i = 0; i < 60 * 60; i++) g.travellers.update(1/60, g);
  const before = g.travellers.count;
  const poi = g.pois.find(q => q.dungeonTheme);
  g.enterDungeon(poi);
  g.travellers.update(1/60, g);
  const inDungeon = g.travellers.count;
  g.exitDungeon();
  return { day, nite, before, inDungeon };
});
console.log(`5 分で湧いた人数: 昼 ${night.day} / 夜 ${night.nite}　地下に入ると ${night.before} → ${night.inDungeon}`);
ok('夜は往来が減る', night.nite < night.day * 0.5, `昼 ${night.day} → 夜 ${night.nite}`);
ok('地下に旅人はいない', night.inDungeon === 0, `${night.inDungeon}人`);

report(errs);
await close();
