// wx2
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360 });

await page.evaluate(() => {
  const g = window.__game, p = g.player;
  for (let i = 0; i < 8000; i++) {
    const a = i * 2.39996, r = 30 + i * 0.25;
    const x = p.x + Math.cos(a) * r, z = p.z + Math.sin(a) * r;
    if (g.blockers.at(x, z)) continue;
    const out = [0, 0];
    if (g.collide(x, z, 2.5, out)) continue;
    const h0 = g.groundHeight(x, z);
    let flat = true;
    for (let d = 1; d <= 30; d += 2) if (Math.abs(g.groundHeight(x + d, z) - h0) > 0.8) { flat = false; break; }
    if (!flat || h0 < 1) continue;
    p.x = x; p.z = z; p.y = h0; return;
  }
});

/* ---------- 1. 足元 ---------- */
const foot = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const sky = g.sky;
  const set = (o) => { sky.wetness = o.wet ?? 0; sky.snowCover = o.cover ?? 0;
    sky.rainAmount = o.rain ?? 0; sky.snowAmount = o.snow ?? 0; };
  const run = (actor, o) => {
    set(o);
    const x0 = actor.x, z0 = actor.z, y0 = actor.y;
    for (let i = 0; i < 180; i++) actor.moveOnGround(1/60, g, 0, 1, actor.runSpeed);
    const d = Math.hypot(actor.x - x0, actor.z - z0);
    actor.x = x0; actor.z = z0; actor.y = y0;
    return +d.toFixed(2);
  };
  const dry = run(p, {});
  const wet = run(p, { wet: 1, rain: 0.9 });
  const snow = run(p, { cover: 1, snow: 0.9 });
  // 敵も同じように足を取られる
  g.enemies.length = 0;
  const e = spawnEnemy('bandit', { x: p.x + 3, y: p.y, z: p.z, yaw: 0, leash: 400 });
  const eDry = run(e, {});
  const eSnow = run(e, { cover: 1, snow: 0.9 });
  g.enemies.length = 0;
  // 地下では効かない
  const poi = g.pois.find(q => q.dungeonTheme);
  g.enterDungeon(poi);
  const dunDry = run(p, {});
  const dunSnow = run(p, { cover: 1, snow: 0.9 });
  g.exitDungeon();
  set({});
  return { dry, wet, snow, eDry, eSnow, dunDry, dunSnow };
});
console.log(`3秒走る: 乾 ${foot.dry}m / 濡 ${foot.wet}m / 雪 ${foot.snow}m`);
console.log(`  敵も: 乾 ${foot.eDry}m → 雪 ${foot.eSnow}m　地下: ${foot.dunDry}m → ${foot.dunSnow}m`);
ok('濡れた地面では少し進みにくい', foot.wet < foot.dry * 0.99 && foot.wet > foot.dry * 0.9,
  `${foot.wet} / ${foot.dry}m`);
ok('積雪ではっきり足を取られる', foot.snow < foot.dry * 0.85 && foot.snow > foot.dry * 0.7,
  `${foot.snow} / ${foot.dry}m（${Math.round((1-foot.snow/foot.dry)*100)}%減）`);
ok('雪は濡れより重い', foot.snow < foot.wet, `雪 ${foot.snow} < 濡 ${foot.wet}`);
ok('敵も同じように足を取られる', foot.eSnow < foot.eDry * 0.9, `${foot.eSnow} / ${foot.eDry}m`);
ok('地下には天候が届かない', Math.abs(foot.dunSnow - foot.dunDry) < 0.01,
  `${foot.dunDry} / ${foot.dunSnow}m`);

/* ---------- 2. 斜面で滑る ---------- */
const slip = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const sky = g.sky;
  // ちょうど乾いていれば踏ん張れる程度の斜面を探す
  let spot = null;
  for (let i = 0; i < 20000 && !spot; i++) {
    const a = i * 2.39996, r = 20 + i * 0.35;
    const x = p.x + Math.cos(a) * r, z = p.z + Math.sin(a) * r;
    const gx = (g.groundHeight(x + 0.6, z) - g.groundHeight(x - 0.6, z)) / 1.2;
    const gz = (g.groundHeight(x, z + 0.6) - g.groundHeight(x, z - 0.6)) / 1.2;
    const grad = Math.hypot(gx, gz);
    if (grad > 1.00 && grad < 1.14) spot = { x, z, grad: +grad.toFixed(3) };
  }
  if (!spot) return { spot: null };
  const test = (o) => {
    sky.wetness = o.wet ?? 0; sky.snowCover = o.cover ?? 0;
    p.x = spot.x; p.z = spot.z; p.y = g.groundHeight(spot.x, spot.z);
    p.grounded = true; p.vy = 0;
    let moved = 0;
    for (let i = 0; i < 60; i++) {
      const x0 = p.x, z0 = p.z;
      p.updateSlide(1/60, g);
      moved += Math.hypot(p.x - x0, p.z - z0);
    }
    return { slid: +moved.toFixed(2), sliding: +(p.sliding || 0).toFixed(2) };
  };
  const dry = test({});
  const wet = test({ wet: 1 });
  const snow = test({ cover: 1 });
  sky.wetness = 0; sky.snowCover = 0;
  return { spot, dry, wet, snow };
});
if (slip.spot) {
  console.log(`傾き ${slip.spot.grad} の斜面で 1 秒立つ: 乾 ${slip.dry.slid}m / 濡 ${slip.wet.slid}m / 雪 ${slip.snow.slid}m`);
  ok('乾いていれば踏ん張れる斜面', slip.dry.slid < 0.05, `${slip.dry.slid}m`);
  ok('濡れると滑り出す', slip.wet.slid > 0.5, `${slip.wet.slid}m`);
  ok('雪ではもっと滑る', slip.snow.slid > slip.wet.slid, `雪 ${slip.snow.slid} > 濡 ${slip.wet.slid}m`);
} else {
  console.log('該当する傾きの斜面が見つからなかった');
}

/* ---------- 3. 風が矢を流す。印もそれを知っている ---------- */
const wind = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { TEAM } = await import('/src/game/actor.js');
  const sky = g.sky;
  g.enemies.length = 0;
  p.equip.weapon = 'bow'; p.recalc();
  p.yaw = 0; p.aimPitch = 0.05;
  const shoot = () => {
    g.projectiles.length = 0;
    g.spawnProjectile({ kind: 'arrow', owner: p, damage: 1,
      x: p.x, y: p.y + 1.35, z: p.z, yaw: 0, pitch: 0.05, speed: 46, team: TEAM.PLAYER });
    let last = null;
    for (let i = 0; i < 60 * 8; i++) {
      const b = g.projectiles[0];
      if (b) last = [b.x, b.y, b.z];
      g.updateProjectiles(1/60);
      if (!g.projectiles.length) break;
    }
    return last;
  };
  const rows = [];
  for (const [name, w, dir] of [['無風', 0, [1, 0]], ['そよ風', 0.6, [1, 0]],
    ['強風（右）', 2.4, [1, 0]], ['強風（左）', 2.4, [-1, 0]]]) {
    sky.wind = w; sky.windDir = dir;
    const mark = [0, 0, 0];
    const kind = p.predictImpact(g, mark);
    const hit = shoot();
    rows.push({ name, wind: w,
      drift: +(hit[0] - p.x).toFixed(2),
      dist: +Math.hypot(hit[0] - p.x, hit[2] - p.z).toFixed(1),
      markErr: +Math.hypot(mark[0] - hit[0], mark[2] - hit[2]).toFixed(2), kind });
  }
  // 術は風を受けない
  p.equip.weapon = 'staff'; p.equip.spell = 'soul_dart'; p.recalc();
  sky.wind = 2.4; sky.windDir = [1, 0];
  g.projectiles.length = 0;
  g.spawnProjectile({ kind: 'soul_dart', owner: p, damage: 1,
    x: p.x, y: p.y + 1.35, z: p.z, yaw: 0, pitch: 0, speed: 26, team: TEAM.PLAYER });
  let sp = null;
  for (let i = 0; i < 60 * 6; i++) {
    const b = g.projectiles[0]; if (b) sp = [b.x, b.z];
    g.updateProjectiles(1/60);
    if (!g.projectiles.length) break;
  }
  const spellDrift = +(sp[0] - p.x).toFixed(2);
  p.equip.weapon = 'longsword'; p.equip.spell = null; p.recalc();
  sky.wind = 0.3;
  return { rows, spellDrift };
});
console.log('風と矢:');
for (const r of wind.rows)
  console.log(`  ${r.name.padEnd(11)} 風${r.wind}　横流れ ${String(r.drift).padStart(6)}m　`
    + `飛距離 ${r.dist}m　印とのずれ ${r.markErr}m`);
console.log(`  術の横流れ: ${wind.spellDrift}m`);
const calm = wind.rows[0], strongR = wind.rows[2], strongL = wind.rows[3];
ok('無風では流れない', Math.abs(calm.drift) < 0.2, `${calm.drift}m`);
ok('強い風で流される', Math.abs(strongR.drift) > 0.8, `${strongR.drift}m`);
ok('風向きどおりに流れる', strongR.drift > 0 && strongL.drift < 0,
  `右 ${strongR.drift} / 左 ${strongL.drift}m`);
ok('流されても当てられなくはない（30m で 2m 以内）',
  Math.abs(strongR.drift) < 2.0, `${strongR.drift}m`);
ok('着弾点の印が風を織り込んでいる',
  wind.rows.every(r => r.markErr < 1.2), wind.rows.map(r=>`${r.markErr}m`).join(' '));
ok('術は風を受けない', Math.abs(wind.spellDrift) < 0.2, `${wind.spellDrift}m`);

/* ---------- 4. 視界は今までどおり効く ---------- */
const vis = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const range = (v) => {
    g.sky.visibility = v;
    g.enemies.length = 0;
    const e = spawnEnemy('bandit', { x: p.x, y: p.y, z: p.z + 100, yaw: 0, leash: 900 });
    g.enemies.push(e);
    let hit = null;
    for (let d = 60; d >= 1; d -= 0.5) {
      e.aggro = false; e.aiState = 'idle';
      e.x = p.x; e.z = p.z + d;
      e.update(1/60, g);
      if (e.aggro) { hit = d; break; }
    }
    g.enemies.length = 0;
    return hit;
  };
  const clear = range(1), fog = range(0.35);
  g.sky.visibility = 1;
  return { clear, fog };
});
console.log(`索敵距離: 晴 ${vis.clear}m → 濃霧 ${vis.fog}m`);
ok('霧の中では見つかりにくい', vis.fog < vis.clear * 0.75, `${vis.fog} / ${vis.clear}m`);

report(errs);
await close();
