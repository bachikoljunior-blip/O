// fishing1 — 釣り
//
// 手を入れる前に測った数字：
//   世界の 25.7% が水面。なのに岸 30 点の半径 25m にある「用」（採取点・POI）は
//   平均 0.2 件で、うち 24 点はゼロだった（陸は 0.63 件、ゼロは 13 点）。
//   採取点 317 のうち水辺 12m 以内は 26 件（8.2%）。岸に立って「調べる」に出るものは
//   14 箇所すべてでゼロ。魚は 90 秒で 45 回跳ねるが、手を出す方法はなかった。
//
// 見るのは、水辺に「留まる理由」ができたかどうか。
//   1. 竿を持って岸に立つと糸を垂れられる（持っていなければ出ない）
//   2. 待ち → 当たり → 合わせ、に窓がある。早合わせも遅れも逃げられる
//   3. 取り込みに読み合いがある（走っているあいだ巻けば切れる／緩めば持つ）
//   4. 魚影の濃いところ・朝夕ほど早く食う。夜にしか出ない魚がいる
//   5. 釣果が調合に繋がっている
//   6. 動けば糸を上げる。地下では釣れない
//   7. 毎フレームの負担
//
// 時間ではなく状態で進める。update を自分で刻んで送るので rAF には依らない。

import { open, ok, report, median, until } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/* ---------------------------------------------------- 下ごしらえ */
// 岸へ運び、カメラを水のほうへ向ける。以降の測定はすべてここから
const spot = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  const px = p.x, pz = p.z;
  for (let i = 0; i < 2000; i++) {
    const a = i * 2.39996, r = 60 + i * 3.0;
    const x = px + Math.cos(a) * r, z = pz + Math.sin(a) * r;
    if (Math.hypot(x, z) > g.worldRadius - 60) continue;
    if (g.world.height(x, z) > g.seaLevel - 2.5) continue;
    for (let k = 0; k < 260; k++) {
      const b = k * 0.5, rr = 7 + k * 0.5;
      const sx = x + Math.cos(b) * rr, sz = z + Math.sin(b) * rr;
      const h = g.world.height(sx, sz);
      if (h > g.seaLevel + 0.3 && h < g.seaLevel + 4) {
        p.x = sx; p.z = sz; p.y = h;
        p.swimming = false; p.dead = false;
        g.camera.yaw = Math.atan2(x - sx, z - sz);
        return { shore: { x: +sx.toFixed(1), z: +sz.toFixed(1), h: +h.toFixed(1) },
          water: { x: Math.round(x), z: Math.round(z) } };
      }
    }
  }
  return null;
});
console.log('岸:', JSON.stringify(spot));
ok('立てる岸がある', !!spot, spot ? '見つかった' : 'なし');

/* ---------- 1. 竿がないと出ない・あると出る ---------- */
const gate = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  p.inventory.fishing_rod = 0;
  g.fishing.hinted = false;
  g.updateGatherables();
  g.updateInteract();
  const without = g.interact ? g.interact.type : null;
  p.inventory.fishing_rod = 1;
  g.updateInteract();
  const withRod = g.interact ? g.interact.type : null;
  const label = g.interact ? g.interact.label : null;
  // 岸を離れたらどうか（内陸へ 400m）
  const px = p.x, pz = p.z;
  let far = 'nowater';
  for (let i = 0; i < 3000; i++) {
    const a = i * 2.39996, r = 80 + i * 2;
    const x = px + Math.cos(a) * r, z = pz + Math.sin(a) * r;
    if (Math.hypot(x, z) > g.worldRadius - 60) continue;
    if (g.world.height(x, z) < g.seaLevel + 14) continue;
    p.x = x; p.z = z; p.y = g.groundHeight(x, z);
    g.updateInteract();
    far = g.interact ? g.interact.type : null;
    break;
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  return { without, withRod, label, far };
});
console.log(`岸で「調べる」: 竿なし ${gate.without} → 竿あり ${gate.withRod}（${gate.label}）　内陸 ${gate.far}`);
ok('竿がなければ釣りは出ない', gate.without !== 'fishing', String(gate.without));
ok('竿を持って岸に立つと糸を垂れられる', gate.withRod === 'fishing', gate.label || 'なし');
ok('水のない内陸では出ない', gate.far !== 'fishing', String(gate.far));

/* ---------- 2. 待ち → 当たり → 合わせ ---------- */
/**
 * 釣りを一回まわす。
 * hookDelay: 当たりが出てから合わせるまでの秒（null なら合わせない）
 * pull: 取り込み中の巻き方 'smart'（走っていないときだけ）/ 'greedy'（ずっと巻く）/ 'never'
 */
const runOnce = async (o) => page.evaluate((o) => {
  const g = window.__game, p = g.player;
  const dt = 1 / 60;
  // 入力を差し替える。ハーネスからボタンを押せるようにする
  const held = { down: false, press: false };
  const oDown = g.input.down.bind(g.input);
  const oPress = g.input.pressed.bind(g.input);
  g.input.down = (n) => (n === 'interact' ? held.down : oDown(n));
  g.input.pressed = (n) => {
    if (n !== 'interact') return oPress(n);
    const v = held.press; held.press = false; return v;
  };
  const tap = () => { held.press = true; held.down = true; };

  p.inventory.fishing_rod = 1;
  p.stamina = p.maxStamina;
  const before = { ...p.inventory };
  g.fishing.stop(g, 'quit');
  g.fishing.start(g);
  const log = { states: [], nibbles: 0, biteAt: null, hookedAt: null,
    strainMax: 0, phases: 0 };
  let t = 0, biteT = null, sawNib = false, hooked = false;
  let prevPhase = null;
  for (let i = 0; i < 60 * 90 && g.fishing.active; i++) {
    const f = g.fishing;
    if (f.state === 'wait' && f.nib > 0 && !sawNib) { sawNib = true; log.nibbles++; }
    if (f.state === 'wait' && f.nib <= 0) sawNib = false;
    if (f.state === 'bite' && biteT === null) { biteT = t; log.biteAt = +t.toFixed(2); }
    if (f.state === 'fight' && prevPhase !== f.phase) { log.phases++; prevPhase = f.phase; }
    log.strainMax = Math.max(log.strainMax, f.strain);

    // 合わせ
    if (o.hookDelay !== null && !hooked && biteT !== null && t - biteT >= o.hookDelay) {
      tap(); hooked = true; log.hookedAt = +(t - biteT).toFixed(2);
    }
    // 前アタリで合わせる（早合わせ）
    if (o.hookOnNibble && f.state === 'wait' && f.nib > 0) { tap(); hooked = true; }
    // 取り込み
    if (f.state === 'fight') {
      held.down = o.pull === 'greedy' ? true
        : o.pull === 'never' ? false
          : f.phase === 'calm';        // smart: 走っていないときだけ巻く
    } else if (!held.press) held.down = false;

    g.fishing.update(dt, g);
    // 浮きの水面下は fish.update と同じ刻みで進める
    t += dt;
  }
  g.input.down = oDown; g.input.pressed = oPress;
  const gained = {};
  for (const k of Object.keys(p.inventory)) {
    const d = (p.inventory[k] || 0) - (before[k] || 0);
    if (d > 0 && k !== 'fishing_rod') gained[k] = d;
  }
  return { ...log, landed: g.fishing.landed, lost: g.fishing.lost, gained,
    strainMax: +log.strainMax.toFixed(2), total: +t.toFixed(2) };
}, o);

const resetCounters = () => page.evaluate(() => {
  const g = window.__game;
  g.fishing.landed = 0; g.fishing.lost = 0;
});

// 朝・魚影の濃いところへ寄せる
await page.evaluate(() => {
  const g = window.__game;
  g.sky.skipToHour(5.5); g.sky.update(1 / 60, g);
  g.fish.schools.clear();
  for (let i = 0; i < 300; i++) g.fish.update(1 / 60, g);
});

await resetCounters();
const good = [];
for (let i = 0; i < 8; i++) good.push(await runOnce({ hookDelay: 0.3, pull: 'smart' }));
const landedGood = good.filter((r) => Object.keys(r.gained).length).length;
console.log(`合わせ 0.30 秒・走りを避けて巻く: ${landedGood}/8 取り込み　当たりまで 中央 ${median(good.map((r) => r.biteAt))}s　張り最大 中央 ${median(good.map((r) => r.strainMax))}`);
ok('読んで合わせれば釣れる', landedGood >= 6, `${landedGood}/8`);
ok('取り込みに山と谷がある（走る／寄せる）',
  median(good.map((r) => r.phases)) >= 2, `位相の切り替わり 中央 ${median(good.map((r) => r.phases))} 回`);

await resetCounters();
const late = [];
for (let i = 0; i < 6; i++) late.push(await runOnce({ hookDelay: 0.9, pull: 'smart' }));
const landedLate = late.filter((r) => Object.keys(r.gained).length).length;
console.log(`合わせ 0.90 秒（窓 0.55 秒を過ぎる）: ${landedLate}/6 取り込み`);
ok('遅れれば持っていかれる', landedLate === 0, `${landedLate}/6`);

await resetCounters();
const early = [];
for (let i = 0; i < 14; i++) early.push(await runOnce({ hookDelay: null, hookOnNibble: true, pull: 'smart' }));
const nibbled = early.filter((r) => r.nibbles > 0);
const landedEarly = early.filter((r) => Object.keys(r.gained).length).length;
console.log(`前アタリで合わせた: 前アタリの出た回 ${nibbled.length}/14　取り込み ${landedEarly}`);
ok('前アタリが出る', nibbled.length > 0, `${nibbled.length}/14`);
ok('前アタリで合わせると逃げられる', landedEarly === 0, `${landedEarly} 匹`);

await resetCounters();
const greedy = [];
for (let i = 0; i < 8; i++) greedy.push(await runOnce({ hookDelay: 0.3, pull: 'greedy' }));
const landedGreedy = greedy.filter((r) => Object.keys(r.gained).length).length;
// 張りは update の前に読んでいるので、1.0 を跨いで切れた瞬間の値は取れない
// （切れた同じフレームで 0 に畳まれる）。1 フレームぶん手前までしか見えないので、
// 「1 以上」ではなく「振り切る直前まで行っている」ことと、
// こらえて巻いたときとの差で見る。
const greedyStrain = median(greedy.map((r) => r.strainMax));
const goodStrain = median(good.map((r) => r.strainMax));
console.log(`ずっと巻いた: ${landedGreedy}/8 取り込み　張り最大 中央 ${greedyStrain}（こらえたとき ${goodStrain}）`);
ok('走っているのに巻き続けると糸が切れる', landedGreedy <= 2, `${landedGreedy}/8`);
ok('切れるのは張りが振り切れたとき', greedyStrain >= 0.95,
  `張り最大 中央 ${greedyStrain}`);
ok('こらえて巻けば張りに余裕が残る', goodStrain < greedyStrain - 0.1,
  `${goodStrain} < ${greedyStrain}`);

await resetCounters();
const never = await runOnce({ hookDelay: 0.3, pull: 'never' });
console.log(`一度も巻かなかった: 取り込み ${Object.keys(never.gained).length ? 'した' : 'していない'}（${never.total}s）`);
ok('巻かなければ寄らない', !Object.keys(never.gained).length, `${never.total}s`);

/* ---------- 3. 場所と時刻で釣果が変わる ---------- */
const place = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  const f = g.fishing;
  const px = p.x, pz = p.z;
  const spot = f.findSpot(g);
  const rich = spot ? g.fish.richnessAt(g, spot.x, spot.z) : 0;
  // 待ちの長さを、魚影と時刻を振って集める（乱数の揺れは中央値で潰す）
  const sample = (r, hour, n = 60) => {
    g.sky.skipToHour(hour); g.sky.update(1 / 60, g);
    const out = [];
    for (let i = 0; i < n; i++) { f.rich = r; f.planBite(g); out.push(f.waitDur); }
    out.sort((a, b) => a - b);
    return +out[n >> 1].toFixed(1);
  };
  const res = {
    rich: +rich.toFixed(2),
    dawnRich: sample(0.9, 5.5), dawnPoor: sample(0.0, 5.5),
    noonRich: sample(0.9, 12), nightRich: sample(0.9, 22),
  };
  // 釣果の内訳
  const pick = (r, hour, n = 400) => {
    g.sky.skipToHour(hour); g.sky.update(1 / 60, g);
    f.rich = r;
    const c = {};
    for (let i = 0; i < n; i++) { const id = f.pickCatch(g); c[id] = (c[id] || 0) + 1; }
    return c;
  };
  res.catchPoorDay = pick(0.05, 12);
  res.catchRichDay = pick(0.95, 12);
  res.catchRichNight = pick(0.95, 22);
  p.x = px; p.z = pz;
  return res;
});
console.log(`この岸の魚影 ${place.rich}`);
console.log(`当たりまでの中央値: 朝×濃 ${place.dawnRich}s / 朝×薄 ${place.dawnPoor}s / 昼×濃 ${place.noonRich}s / 夜×濃 ${place.nightRich}s`);
console.log(`釣果 昼×薄: ${JSON.stringify(place.catchPoorDay)}`);
console.log(`釣果 昼×濃: ${JSON.stringify(place.catchRichDay)}`);
console.log(`釣果 夜×濃: ${JSON.stringify(place.catchRichNight)}`);
ok('魚影が濃いほど早く食う', place.dawnRich < place.dawnPoor * 0.7,
  `${place.dawnRich}s < ${place.dawnPoor}s`);
ok('朝夕のほうが早く食う', place.dawnRich < place.noonRich * 0.75,
  `朝 ${place.dawnRich}s / 昼 ${place.noonRich}s`);
ok('薄い水では小魚しか出ない',
  !place.catchPoorDay.fish_big && !place.catchPoorDay.fish_moon,
  JSON.stringify(place.catchPoorDay));
ok('濃い水では大魚が混じる', (place.catchRichDay.fish_big || 0) > 20,
  `${place.catchRichDay.fish_big || 0}/400`);
ok('月光魚は夜だけ',
  !place.catchRichDay.fish_moon && (place.catchRichNight.fish_moon || 0) > 20,
  `昼 ${place.catchRichDay.fish_moon || 0} / 夜 ${place.catchRichNight.fish_moon || 0}`);

/* ---------- 3b. 世界の岸で、魚影は本当に散らばるか ----------
 *
 * はじめ「最寄りの群れまでの距離」で魚影を測ったら、実際の岸 10 箇所すべてで
 * 0 になった（群れまで中央 197m）。それでは大魚も月光魚も永久に出ない。
 * 群れが場所を選ぶ条件そのもの（深さ・水の広がり）で測り直したので、
 * ふつうの岸と群れの真上が別の値になっているかを見る。
 */
const spread = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  const px = p.x, pz = p.z;
  const shores = [], atSchool = [];
  for (let s = 0; s < 1400 && shores.length < 14; s++) {
    const a = s * 2.39996, rr = 80 + s * 14;
    const x = px + Math.cos(a) * rr, z = pz + Math.sin(a) * rr;
    if (Math.hypot(x, z) > g.worldRadius - 60) continue;
    if (g.world.height(x, z) > g.seaLevel - 2.5) continue;
    let sh = null;
    for (let k = 0; k < 260 && !sh; k++) {
      const b = k * 0.5, r2 = 7 + k * 0.5;
      const sx = x + Math.cos(b) * r2, sz = z + Math.sin(b) * r2;
      const h = g.world.height(sx, sz);
      if (h > g.seaLevel + 0.3 && h < g.seaLevel + 4) sh = { sx, sz, h };
    }
    if (!sh) continue;
    p.x = sh.sx; p.z = sh.sz; p.y = sh.h; p.swimming = false;
    g.camera.yaw = Math.atan2(x - sh.sx, z - sh.sz);
    g.fish.schools.clear();
    for (let i = 0; i < 200; i++) g.fish.update(1 / 60, g);
    const sp = g.fishing.findSpot(g);
    if (!sp) continue;
    shores.push(+g.fish.richnessAt(g, sp.x, sp.z).toFixed(2));
    for (const sc of g.fish.schools.values()) {
      if (atSchool.length < 10) atSchool.push(+g.fish.richnessAt(g, sc.x, sc.z).toFixed(2));
    }
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  return { shores, atSchool };
});
console.log(`岸 ${spread.shores.length} 箇所の魚影: ${spread.shores.join(' ')}`);
console.log(`群れの真上の魚影: ${spread.atSchool.join(' ')}`);
ok('ふつうの岸でも魚影が立つ', spread.shores.every((v) => v > 0),
  `最小 ${Math.min(...spread.shores)}`);
ok('岸によって魚影が違う',
  Math.max(...spread.shores) - Math.min(...spread.shores) > 0.12,
  `${Math.min(...spread.shores)}〜${Math.max(...spread.shores)}`);
ok('群れの居る場所がいちばん濃い',
  median(spread.atSchool) > median(spread.shores) + 0.2,
  `岸 中央 ${median(spread.shores)} / 群れ 中央 ${median(spread.atSchool)}`);

/* ---------- 4. 釣果が調合に繋がる ---------- */
const econ = await page.evaluate(async () => {
  const m = await import('/src/game/data.js');
  const g = window.__game, p = g.player;
  const catches = ['fish_small', 'fish_big', 'fish_moon'];
  const used = {};
  for (const rc of m.RECIPES) {
    for (const k of Object.keys(rc.need)) {
      if (catches.includes(k)) (used[k] = used[k] || []).push(rc.out);
    }
  }
  // 実際に作れるか。焼き魚を一つ通す
  p.inventory.fish_small = 4; p.echo = 5000;
  const rc = m.RECIPES.find((r) => r.id === 'grilled_fish');
  const okNeed = Object.entries(rc.need).every(([id, n]) => (p.inventory[id] || 0) >= n);
  for (const [id, n] of Object.entries(rc.need)) p.inventory[id] -= n;
  p.addItem(rc.out, rc.count);
  const made = p.inventory.grilled_fish || 0;
  // 炙り魚を食べると HP と息が戻るか
  p.hp = 50; p.stamina = 5;
  p.consume('grilled_fish', g);
  await new Promise((r) => setTimeout(r, 420));
  const after = { hp: Math.round(p.hp), st: Math.round(p.stamina), max: p.maxStamina };
  // 竿そのものが作れるか
  const rodRecipe = !!m.RECIPES.find((r) => r.out === 'fishing_rod');
  const rodShop = !!m.SHOP.find((s) => s.item === 'fishing_rod');
  return { used, okNeed, made, after, rodRecipe, rodShop,
    names: catches.map((c) => m.ITEMS[c] && m.ITEMS[c].name) };
});
console.log(`釣果の行き先: ${JSON.stringify(econ.used)}`);
console.log(`炙り魚 ${econ.made} 個を作って食べた → HP ${econ.after.hp} / スタミナ ${econ.after.st}（最大 ${econ.after.max}）`);
ok('釣った魚に名前がある', econ.names.every((n) => !!n), econ.names.join('・'));
ok('小魚は料理になる', (econ.used.fish_small || []).includes('grilled_fish'),
  JSON.stringify(econ.used.fish_small));
ok('大魚は骨（矢・火炎壺の素）にもなる', (econ.used.fish_big || []).includes('beast_bone'),
  JSON.stringify(econ.used.fish_big));
ok('月光魚は魔力の結晶になる', (econ.used.fish_moon || []).includes('crystal'),
  JSON.stringify(econ.used.fish_moon));
ok('炙り魚が実際に作れる', econ.okNeed && econ.made > 0, `${econ.made} 個`);
ok('炙り魚でHPと息が戻る', econ.after.hp > 100 && econ.after.st > econ.after.max * 0.4,
  `HP ${econ.after.hp} / ST ${econ.after.st}`);
ok('竿は買えるし作れる', econ.rodRecipe && econ.rodShop,
  `調合 ${econ.rodRecipe} / 商い ${econ.rodShop}`);

/* ---------- 5. 動けば糸を上げる・地下では釣れない ---------- */
const quit = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  const res = {};
  // 歩き出すと終わる
  p.inventory.fishing_rod = 1;
  g.fishing.stop(g, 'quit');
  g.fishing.start(g);
  res.started = g.fishing.active;
  const oMove = g.input.move;
  g.input.move = { x: 1, y: 0 };
  p.update(1 / 60, g);
  res.afterWalk = g.fishing.active;
  g.input.move = oMove;
  // 地下へ入ると畳まれる
  g.fishing.start(g);
  const poi = g.pois.find((q) => q.dungeonTheme);
  g.enterDungeon(poi);
  g.fishing.update(1 / 60, g);
  res.inDungeon = g.fishing.active;
  res.canStartUnderground = !!g.fishing.canStart(g);
  g.exitDungeon();
  return res;
});
console.log('糸を上げる:', JSON.stringify(quit));
ok('投げれば釣りが始まる', quit.started);
ok('歩き出せば糸を上げる', quit.afterWalk === false);
ok('地下に入ると畳まれる', quit.inDungeon === false);
ok('地下では糸を垂れられない', quit.canStartUnderground === false);

/* ---------- 6. 群れが散る ---------- */
const startle = await page.evaluate(() => {
  const g = window.__game;
  const s = [...g.fish.schools.values()][0];
  if (!s) return { none: true };
  const spread = () => {
    let sum = 0;
    for (const f of s.fish) sum += Math.hypot(f.x - s.x, f.z - s.z);
    return +(sum / s.fish.length).toFixed(2);
  };
  const px = g.player.x, pz = g.player.z;
  g.player.x = s.x + 45; g.player.z = s.z;      // 人の気配では散らない距離
  for (let i = 0; i < 240; i++) g.fish.update(1 / 60, g);
  const calm = spread();
  g.fish.startle(s.x, s.z, 20);
  for (let i = 0; i < 60; i++) g.fish.update(1 / 60, g);
  const after = spread();
  g.player.x = px; g.player.z = pz;
  return { calm, after };
});
if (!startle.none) {
  console.log(`釣り上げた直後の群れ: 広がり ${startle.calm}m → ${startle.after}m`);
  ok('水面が乱れると群れが散る', startle.after > startle.calm * 1.1,
    `${startle.calm} → ${startle.after}m`);
}

/* ---------- 6b. 本当のフレームを回して、糸と浮きが描かれるか ----------
 *
 * ここまでの測定は fishing.update を自分で刻んで送っている。描画の道は
 * 一度も通っていない。第32周に、採取物の色を書き忘れて描画が毎フレーム
 * 例外で落ち、それ以降に積まれるものがまるごと消えていたことがある。
 * 絵を見ても「敵がいないだけ」に見えるので、実際に回して確かめる。
 */
const drawn = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  // 岸へ戻して、糸を垂れた状態にする
  const px = p.x, pz = p.z;
  for (let i = 0; i < 2000; i++) {
    const a = i * 2.39996, r = 60 + i * 3.0;
    const x = px + Math.cos(a) * r, z = pz + Math.sin(a) * r;
    if (Math.hypot(x, z) > g.worldRadius - 60) continue;
    if (g.world.height(x, z) > g.seaLevel - 2.5) continue;
    let done = false;
    for (let k = 0; k < 260; k++) {
      const b = k * 0.5, rr = 7 + k * 0.5;
      const sx = x + Math.cos(b) * rr, sz = z + Math.sin(b) * rr;
      const h = g.world.height(sx, sz);
      if (h > g.seaLevel + 0.3 && h < g.seaLevel + 4) {
        p.x = sx; p.z = sz; p.y = h; p.swimming = false;
        g.camera.yaw = Math.atan2(x - sx, z - sz);
        done = true; break;
      }
    }
    if (done) break;
  }
  p.inventory.fishing_rod = 1;
  g.fishing.stop(g, 'quit');
  const started = g.fishing.start(g);
  // 浮きの数を数えられるようにしておく
  window.__ballCount = 0;
  const b = g.renderer.batchFor('ball');
  const oAlloc = b.alloc.bind(b);
  b.alloc = () => { window.__ballCount++; return oAlloc(); };
  window.__restoreBall = () => { b.alloc = oAlloc; };
  return { started, frames: g.time.frames };
});
ok('岸で糸を垂れられた（描画の確認用）', drawn.started);
// 時間ではなくフレームが進むのを待つ（SwiftShader では 2fps しか出ない）
await page.evaluate(() => { window.__fr0 = window.__game.time.frames; });
const draw = await until(page,
  () => window.__game.time.frames > window.__fr0 + 4 && window.__ballCount > 0,
  { timeout: 25000 });
const drawRes = await page.evaluate(() => {
  const g = window.__game;
  window.__restoreBall?.();
  const r = { balls: window.__ballCount, frames: g.time.frames,
    active: g.fishing.active, state: g.fishing.state,
    hud: !document.querySelector('#fishing').classList.contains('hidden') };
  g.fishing.stop(g, 'quit');
  return r;
});
console.log(`実フレーム: 浮きと糸の玉 ${drawRes.balls} 個　釣り ${drawRes.state}　HUD ${drawRes.hud ? '出ている' : '出ていない'}`);
ok('実際にフレームが回る', draw, `${drawRes.frames} フレーム`);
ok('糸と浮きが描かれる', drawRes.balls >= 7, `${drawRes.balls} 個`);
ok('釣りの手元が画面に出る', drawRes.hud);
ok('描画中に例外が出ない', errs.length === 0, errs.slice(0, 2).join(' / '));

/* ---------- 7. 重さ ---------- */
const cost = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  p.inventory.fishing_rod = 1;
  g.fishing.stop(g, 'quit');
  g.fishing.start(g);
  for (let i = 0; i < 60; i++) g.fishing.update(1 / 60, g);
  const t0 = performance.now();
  for (let i = 0; i < 1200 && g.fishing.active; i++) g.fishing.update(1 / 60, g);
  const us = +((performance.now() - t0) / 1200 * 1000).toFixed(1);
  // 釣っていないときの負担（毎フレーム呼ばれる）
  g.fishing.stop(g, 'quit');
  const t1 = performance.now();
  for (let i = 0; i < 3000; i++) g.fishing.update(1 / 60, g);
  const idle = +((performance.now() - t1) / 3000 * 1000).toFixed(1);
  // 岸を探す一回の重さ（「調べる」に出すために毎フレーム走る）
  const t2 = performance.now();
  for (let i = 0; i < 400; i++) g.fishing.findSpot(g);
  const find = +((performance.now() - t2) / 400 * 1000).toFixed(1);
  return { us, idle, find };
});
console.log(`1 フレーム 釣り中 ${cost.us}µs / 待機 ${cost.idle}µs / 岸を探す ${cost.find}µs`);
ok('釣り中の負担が小さい', cost.us < 60, `${cost.us}µs`);
ok('釣っていないときはただ同然', cost.idle < 3, `${cost.idle}µs`);
ok('岸を探すのが軽い', cost.find < 200, `${cost.find}µs`);

report(errs);
await close();
