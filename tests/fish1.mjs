// fish1 — 水の生き物
//
// 世界の 27% が水面なのに、そこには敵も村人も旅人も鳥もいなかった。
// 魚の群れと、水面に浮く水鳥を置いたので、それが本当に水の上にいるか、
// 跳ねるか、近づくと散るか、夜と地下でどうなるかを見る。

import { open, ok, report, median } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/** 群れの居る水辺へプレイヤーを運ぶ */
const findWater = async () => page.evaluate(() => {
  const g = window.__game, p = g.player;
  const px = p.x, pz = p.z;
  for (let i = 0; i < 1200; i++) {
    const a = i * 2.39996, r = 60 + i * 4.5;
    const x = px + Math.cos(a) * r, z = pz + Math.sin(a) * r;
    if (Math.hypot(x, z) > g.worldRadius - 60) continue;
    if (g.world.height(x, z) > g.seaLevel - 3) continue;
    // 岸を探して、そこに立つ
    for (let k = 0; k < 200; k++) {
      const b = k * 0.5, rr = 6 + k * 0.9;
      const sx = x + Math.cos(b) * rr, sz = z + Math.sin(b) * rr;
      const h = g.world.height(sx, sz);
      if (h > g.seaLevel + 0.4 && h < g.seaLevel + 5) {
        p.x = sx; p.z = sz; p.y = h;
        return { water: { x: Math.round(x), z: Math.round(z) },
          shore: { x: Math.round(sx), z: Math.round(sz), h: +h.toFixed(1) } };
      }
    }
  }
  return null;
});

const spot = await findWater();
console.log('水辺:', JSON.stringify(spot));
ok('世界に立てる水辺がある', !!spot, spot ? '見つかった' : 'なし');

/* ---------- 1. 水面下に群れがいる ---------- */
const school = await page.evaluate(async () => {
  const g = window.__game;
  g.sky.skipToHour(6); g.sky.update(1 / 60, g);
  g.fish.schools.clear();
  for (let i = 0; i < 60 * 6; i++) g.fish.update(1 / 60, g);
  const depths = [], overWater = [];
  g.fish.forEach((f) => {
    depths.push(+(g.seaLevel - f.y).toFixed(2));
    overWater.push(g.world.height(f.x, f.z) < g.seaLevel);
  });
  return { schools: g.fish.schools.size, n: g.fish.count, depths,
    onWater: overWater.filter(Boolean).length, surf: g.seaLevel };
});
console.log(`群れ ${school.schools} / 魚 ${school.n} 匹　水面下の深さ 中央 ${median(school.depths)}m`);
ok('水辺に魚がいる', school.n > 0, `${school.n}匹`);
ok('魚は水の上にしかいない', school.onWater === school.n, `${school.onWater}/${school.n}`);
ok('ふだんは水面下を泳ぐ', median(school.depths) > 0.5 && median(school.depths) < 3.5,
  `${median(school.depths)}m`);

/* ---------- 2. 跳ねる ---------- */
const leap = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const sfx = []; const oPlay = g.audio.play.bind(g.audio);
  g.audio.play = (n, o) => { sfx.push(n); return oPlay(n, o); };
  let splashes = 0;
  const oSplash = g.fx.splash.bind(g.fx);
  g.fx.splash = (...a) => { splashes++; return oSplash(...a); };
  // 群れから離れて見物する（近いと散って跳ねない）
  const s = [...g.fish.schools.values()][0];
  const px = p.x, pz = p.z;
  if (s) { p.x = s.x + 30; p.z = s.z; p.y = g.groundHeight(p.x, p.z); }
  let jumped = 0, maxAbove = 0;
  for (let i = 0; i < 60 * 90; i++) {
    g.fish.update(1 / 60, g);
    jumped = Math.max(jumped, g.fish.jumping);
    g.fish.forEach((f, out) => { if (out) maxAbove = Math.max(maxAbove, f.y - g.seaLevel); });
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  g.audio.play = oPlay; g.fx.splash = oSplash;
  return { jumped, maxAbove: +maxAbove.toFixed(2), splashes,
    sfx: sfx.filter((n) => n === 'splash').length };
});
console.log(`90 秒見物: 同時に跳んだ最大 ${leap.jumped} 匹　水面から ${leap.maxAbove}m　飛沫 ${leap.splashes} 回　音 ${leap.sfx} 回`);
ok('魚が跳ねる', leap.maxAbove > 0.3, `${leap.maxAbove}m`);
ok('跳ねると飛沫が立つ', leap.splashes > 0, `${leap.splashes}回`);
ok('跳ねると音がする', leap.sfx > 0, `${leap.sfx}回`);
ok('跳ねすぎない（水面が沸騰しない）', leap.jumped <= 3, `同時 ${leap.jumped}匹`);

/* ---------- 3. 近づくと散って沈む ---------- */
const spook = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const s = [...g.fish.schools.values()][0];
  if (!s) return { none: true };
  const px = p.x, pz = p.z;
  const spread = () => {
    let sum = 0;
    for (const f of s.fish) sum += Math.hypot(f.x - s.x, f.z - s.z);
    return +(sum / s.fish.length).toFixed(2);
  };
  const depth = () => {
    let sum = 0;
    for (const f of s.fish) sum += g.seaLevel - f.y;
    return +(sum / s.fish.length).toFixed(2);
  };
  p.x = s.x + 40; p.z = s.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 240; i++) g.fish.update(1 / 60, g);
  const calm = { spread: spread(), depth: depth() };
  // 群れの真上へ
  p.x = s.x; p.z = s.z; p.y = g.seaLevel + 1;
  for (let i = 0; i < 240; i++) g.fish.update(1 / 60, g);
  const near = { spread: spread(), depth: depth() };
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  return { calm, near };
});
if (!spook.none) {
  console.log(`落ち着き: 広がり ${spook.calm.spread}m 深さ ${spook.calm.depth}m → 真上に立つと 広がり ${spook.near.spread}m 深さ ${spook.near.depth}m`);
  ok('近づくと散る', spook.near.spread > spook.calm.spread * 1.15,
    `${spook.calm.spread} → ${spook.near.spread}m`);
  ok('近づくと深く沈む', spook.near.depth > spook.calm.depth + 0.3,
    `${spook.calm.depth} → ${spook.near.depth}m`);
}

/* ---------- 4. 時刻で活きが変わる ---------- */
const clock = await page.evaluate(async () => {
  const g = window.__game;
  const at = (h) => { g.sky.skipToHour(h); g.sky.update(1 / 60, g); return +g.fish.activity(g).toFixed(2); };
  return { dawn: at(5.5), noon: at(12), dusk: at(19), night: at(1) };
});
console.log('活きの度合い:', JSON.stringify(clock));
ok('朝夕によく動く', clock.dawn > clock.noon && clock.dusk > clock.noon,
  `朝 ${clock.dawn} / 昼 ${clock.noon} / 夕 ${clock.dusk}`);
ok('真昼と深夜は静か', clock.noon < 0.5 && clock.night < 0.5,
  `昼 ${clock.noon} / 夜 ${clock.night}`);

/* ---------- 5. 水鳥が浮いている ---------- */
const fowl = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  g.sky.skipToHour(10); g.sky.update(1 / 60, g);
  g.sky.rainAmount = 0;
  // 水の上の群れが出るまで、水辺を巡る
  let found = null;
  const px = p.x, pz = p.z;
  for (let s = 0; s < 500 && !found; s++) {
    const a = s * 2.39996, r = 80 + s * 24;
    const x = px + Math.cos(a) * r, z = pz + Math.sin(a) * r;
    if (Math.hypot(x, z) > g.worldRadius - 40) continue;
    p.x = x; p.z = z; p.y = Math.max(g.seaLevel + 1, g.groundHeight(x, z));
    g.wildlife.flocks.clear();
    for (let i = 0; i < 40; i++) g.wildlife.update(1 / 60, g);
    for (const f of g.wildlife.flocks.values()) if (f.water) { found = f; break; }
  }
  if (!found) { p.x = px; p.z = pz; p.y = g.groundHeight(px, pz); return { none: true }; }
  for (let i = 0; i < 120; i++) g.wildlife.update(1 / 60, g);
  const ys = found.birds.map((b) => +(b.y - g.seaLevel).toFixed(2));
  const overWater = found.birds.filter((b) => g.world.height(b.x, b.z) < g.seaLevel).length;
  // 近づくと水面を蹴って飛び立つ
  let splashes = 0;
  const oSplash = g.fx.splash.bind(g.fx);
  g.fx.splash = (...a) => { splashes++; return oSplash(...a); };
  p.x = found.x + 8; p.z = found.z; p.y = g.seaLevel + 1;
  for (let i = 0; i < 30; i++) g.wildlife.update(1 / 60, g);
  const state = found.state;
  g.fx.splash = oSplash;
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  return { n: found.birds.length, ys, overWater, state, splashes };
});
if (fowl.none) console.log('水の上の群れが見つからなかった');
else {
  console.log(`水鳥 ${fowl.n} 羽　水面からの高さ ${fowl.ys.join(',')}m　水上 ${fowl.overWater}/${fowl.n}　近づくと状態 ${fowl.state}（飛沫 ${fowl.splashes}）`);
  ok('水鳥が水面に浮いている',
    fowl.ys.every((y) => y > -0.1 && y < 0.5), `${Math.min(...fowl.ys)}〜${Math.max(...fowl.ys)}m`);
  ok('水鳥は水の上にいる', fowl.overWater === fowl.n, `${fowl.overWater}/${fowl.n}`);
  ok('近づくと水面を蹴って飛び立つ', fowl.state !== 0 && fowl.splashes > 0,
    `状態 ${fowl.state} / 飛沫 ${fowl.splashes}`);
}

/* ---------- 6. 地下にはいない・描かれている ---------- */
const rest = await page.evaluate(async () => {
  const g = window.__game;
  const before = g.fish.count;
  const poi = g.pois.find((q) => q.dungeonTheme);
  g.enterDungeon(poi);
  g.fish.update(1 / 60, g);
  const inDungeon = g.fish.count;
  g.exitDungeon();
  await new Promise((r) => setTimeout(r, 1500));
  const b = g.renderer.batchFor('fish');
  return { before, inDungeon, model: !!b };
});
console.log('地下:', JSON.stringify(rest));
ok('地下に魚はいない', rest.inDungeon === 0, `${rest.inDungeon}匹`);
ok('魚のモデルがある', rest.model);

/* ---------- 7. 重さ ---------- */
const cost = await page.evaluate(() => {
  const g = window.__game;
  for (let i = 0; i < 60; i++) g.fish.update(1 / 60, g);
  const t0 = performance.now();
  for (let i = 0; i < 600; i++) g.fish.update(1 / 60, g);
  return { us: +((performance.now() - t0) / 600 * 1000).toFixed(0), n: g.fish.count };
});
console.log(`1 フレーム ${cost.us}µs（${cost.n} 匹）`);
ok('毎フレームの負担が小さい', cost.us < 300, `${cost.us}µs`);

report(errs);
await close();
