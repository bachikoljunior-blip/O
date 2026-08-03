// bird1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/* ---------- 1. 世界に鳥がいる ---------- */
const live = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  g.sky.skipToHour(10);
  g.sky.rainAmount = 0; g.sky.snowAmount = 0;
  const seen = [];
  const px = p.x, pz = p.z;
  for (let s = 0; s < 26; s++) {
    const a = s * 2.39996, r = 120 + s * 130;
    p.x = px + Math.cos(a) * r; p.z = pz + Math.sin(a) * r;
    p.y = g.groundHeight(p.x, p.z);
    for (let i = 0; i < 20; i++) g.wildlife.update(1/60, g);
    // その場のまわり 7x7 マスのうち、鳥が降りられる陸地がどれだけあるか
    let land = 0;
    for (let dz = -3; dz <= 3; dz++) for (let dx = -3; dx <= 3; dx++) {
      const cx = Math.floor(p.x / 190) + dx, cz = Math.floor(p.z / 190) + dz;
      const wx = (cx + 0.5) * 190, wz = (cz + 0.5) * 190;
      if (!g.world.params(wx, wz).underwater && g.world.height(wx, wz) >= 1) land++;
    }
    seen.push({ flocks: g.wildlife.flocks.size, birds: g.wildlife.count, land });
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  const withBirds = seen.filter(s => s.birds > 0).length;
  const inland = seen.filter(s => s.land >= 25);       // まわりの過半が陸
  return { spots: seen.length, withBirds, dry: inland.length,
    dryWithBirds: inland.filter(s => s.birds > 0).length,
    emptyLand: seen.filter(s => s.birds === 0).map(s => s.land),
    medFlocks: seen.map(s=>s.flocks).sort((a,b)=>a-b)[seen.length>>1],
    maxBirds: Math.max(...seen.map(s=>s.birds)),
    medBirds: seen.map(s=>s.birds).sort((a,b)=>a-b)[seen.length>>1] };
});
console.log(`26 か所を回った: 鳥がいた ${live.withBirds} か所　群れ 中央 ${live.medFlocks}　鳥 中央 ${live.medBirds}（最大 ${live.maxBirds}）`);
console.log(`  （まわりの過半が陸だった ${live.dry} か所中 ${live.dryWithBirds} か所に鳥）`);
console.log(`  鳥がいなかった所の「まわり 49 マス中の陸地」: ${live.emptyLand.join(', ')}`);
ok('内陸ならどこにでも鳥がいる', live.dryWithBirds >= live.dry * 0.9,
  `${live.dryWithBirds}/${live.dry} か所`);
ok('数が暴走しない', live.maxBirds < 90, `最大 ${live.maxBirds} 羽`);

/* ---------- 2. 近づくと飛び立つ ---------- */
const flush = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const sfx = []; const oPlay = g.audio.play.bind(g.audio);
  g.audio.play = (n, o) => { sfx.push(n); return oPlay(n, o); };
  g.sky.skipToHour(10);
  // 地に降りている群れを探す
  let f = null;
  const px = p.x, pz = p.z;
  for (let s = 0; s < 400 && !f; s++) {
    const a = s * 2.39996, r = 150 + s * 90;
    p.x = px + Math.cos(a) * r; p.z = pz + Math.sin(a) * r;
    p.y = g.groundHeight(p.x, p.z);
    for (let i = 0; i < 30; i++) g.wildlife.update(1/60, g);
    for (const fl of g.wildlife.flocks.values()) {
      if (fl.state === 0 && Math.hypot(fl.x - p.x, fl.z - p.z) > 30) { f = fl; break; }
    }
  }
  if (!f) { g.audio.play = oPlay; return { found: false }; }
  const yPerch = f.birds.reduce((s, b) => s + b.y, 0) / f.birds.length;
  const groundY = f.groundY;
  // 群れのそばへ歩み寄る
  sfx.length = 0;
  p.x = f.x + 8; p.z = f.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 30; i++) g.wildlife.update(1/60, g);
  const stateNear = f.state;
  const flushed = sfx.filter(n => n === 'flush').length;
  // 2 秒待つと空へ
  for (let i = 0; i < 150; i++) g.wildlife.update(1/60, g);
  const yUp = f.birds.reduce((s, b) => s + b.y, 0) / f.birds.length;
  const stateUp = f.state;
  const flapping = f.birds.filter(b => b.speed > 0.5).length;
  // 離れて待つと降りてくる
  p.x = f.x + 90; p.z = f.z; p.y = g.groundHeight(p.x, p.z);
  let landed = false, yLand = null;
  for (let i = 0; i < 60 * 60; i++) {
    g.wildlife.update(1/60, g);
    if (f.state === 0) {
      // 降りきるまで少し待ってから測る
      for (let k = 0; k < 60; k++) g.wildlife.update(1/60, g);
      landed = true; yLand = f.birds.reduce((s,b)=>s+b.y,0)/f.birds.length; break;
    }
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  g.audio.play = oPlay;
  return { found: true, yPerch: +yPerch.toFixed(2), groundY: +groundY.toFixed(2),
    stateNear, flushed, yUp: +yUp.toFixed(2), stateUp, flapping, n: f.birds.length,
    landed, yLand: yLand === null ? null : +yLand.toFixed(2), cruiseY: +f.cruiseY.toFixed(1) };
});
if (!flush.found) { console.log('地に降りている群れが見つからなかった'); }
else {
  console.log(`群れ ${flush.n} 羽: 地上 ${flush.yPerch}m（地面 ${flush.groundY}m）→ 近づくと状態 ${flush.stateNear}`
    + `（羽音 ${flush.flushed} 回）→ 2 秒後 ${flush.yUp}m（巡航 ${flush.cruiseY}m）→ 降りた ${flush.landed} ${flush.yLand}m`);
  ok('地に降りているときは地面すれすれ', flush.yPerch - flush.groundY < 0.5,
    `${(flush.yPerch - flush.groundY).toFixed(2)}m`);
  ok('近づくと飛び立つ', flush.stateNear !== 0, `状態 ${flush.stateNear}`);
  ok('飛び立つ音が鳴る', flush.flushed > 0, `${flush.flushed}回`);
  ok('空へ上がる', flush.yUp > flush.yPerch + 3, `${flush.yPerch} → ${flush.yUp}m`);
  ok('飛んでいるあいだは動いている', flush.flapping > flush.n * 0.5, `${flush.flapping}/${flush.n}羽`);
  ok('離れればまた降りてくる', flush.landed && flush.yLand - flush.groundY < 1.0,
    `${flush.landed} ${flush.yLand}m`);
}

/* ---------- 3. 夜と嵐では出ない ---------- */
const cond = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const count = (setup) => {
    g.wildlife.flocks.clear();
    setup();
    for (let i = 0; i < 60; i++) g.wildlife.update(1/60, g);
    return g.wildlife.count;
  };
  const px = p.x, pz = p.z;
  // skipToHour だけでは night が更新されない。sky を一度回して実際の夜にする
  const setHour = (h) => { g.sky.skipToHour(h); g.sky.update(1/60, g); };
  const day = count(() => { setHour(11); g.sky.rainAmount = 0; g.sky.snowAmount = 0; });
  const night = count(() => { setHour(1); g.sky.rainAmount = 0; g.sky.snowAmount = 0; });
  const rain = count(() => { setHour(11); g.sky.rainAmount = 0.95; g.sky.snowAmount = 0; });
  // 地下では消える
  const poi = g.pois.find(q => q.dungeonTheme);
  g.sky.skipToHour(11); g.sky.rainAmount = 0;
  for (let i = 0; i < 60; i++) g.wildlife.update(1/60, g);
  const before = g.wildlife.count;
  g.enterDungeon(poi);
  g.wildlife.update(1/60, g);
  const inDungeon = g.wildlife.count;
  g.exitDungeon();
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  g.sky.rainAmount = 0; g.sky.snowAmount = 0;
  return { day, night, rain, before, inDungeon };
});
console.log(`鳥の数: 昼 ${cond.day} / 夜 ${cond.night} / 大雨 ${cond.rain}　地下に入ると ${cond.before} → ${cond.inDungeon}`);
ok('夜は鳥が減る', cond.night < cond.day * 0.5, `昼 ${cond.day} → 夜 ${cond.night}`);
ok('大雨でも減る', cond.rain < cond.day, `昼 ${cond.day} → 雨 ${cond.rain}`);
ok('地下には鳥がいない', cond.inDungeon === 0, `${cond.inDungeon}羽`);

/* ---------- 4. 描かれている ---------- */
const draw = await page.evaluate(async () => {
  const g = window.__game;
  g.sky.skipToHour(11); g.sky.rainAmount = 0;
  for (let i = 0; i < 120; i++) g.wildlife.update(1/60, g);
  const before = g.renderer.batchFor('bird');
  await new Promise(r => setTimeout(r, 1200));
  const b = g.renderer.batchFor('bird');
  return { hasModel: !!before, count: b ? b.count : null, birds: g.wildlife.count };
});
console.log('描画:', JSON.stringify(draw));
ok('鳥のモデルがある', draw.hasModel);
ok('実際に描画に積まれている', draw.count > 0, `${draw.count} インスタンス`);

/* ---------- 5. 重さ ---------- */
const cost = await page.evaluate(() => {
  const g = window.__game;
  g.sky.skipToHour(11);
  for (let i = 0; i < 60; i++) g.wildlife.update(1/60, g);
  const t0 = performance.now();
  for (let i = 0; i < 600; i++) g.wildlife.update(1/60, g);
  return { us: +((performance.now() - t0) / 600 * 1000).toFixed(0), birds: g.wildlife.count };
});
console.log(`1 フレームあたり ${cost.us}µs（${cost.birds} 羽）`);
ok('毎フレームの負担が小さい', cost.us < 300, `${cost.us}µs`);

report(errs);
await close();
