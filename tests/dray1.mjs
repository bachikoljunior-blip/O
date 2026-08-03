// dray1 — 荷車を牽く駄馬
//
// 隊商（第34周）の荷車は、牽くものが無いまま浮いて動いていた。しかも
// モデルの長手が +X だったので、進行方向に対して 90 度横を向いたまま
// 転がっていた（第38周に計測：モデル長手と進行方向の角度 90.0 度）。
//
// 駄馬が轅（ながえ）の先に繋がって見えるか、歩調が隊商と合っているか、
// 止まれば止まるか、地面から浮いたり沈んだりしないか、荷が積まれて
// 見えるか、面々の見た目に差が付いたか、そして重さを見る。

import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360 });

/** 隊商をひとつ、いちばん長い街道に出して、そばで見物する */
const summon = async () => page.evaluate(() => {
  const g = window.__game, p = g.player;
  g.sky.skipToHour(11); g.sky.update(1 / 60, g);
  g.sky.rainAmount = 0; g.sky.snowAmount = 0;
  g.enemies.length = 0;
  g.travellers.caravans.length = 0;
  const rt = g.world.routes.filter((r) => r.pts.length >= 10)
    .sort((a, b) => b.length - a.length)[0];
  if (!rt) return null;
  const at = rt.pts[3];
  p.x = at.x + 150; p.z = at.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 60 * 8 && !g.travellers.caravans.length; i++) {
    g.travellers.caravanCd = 0;
    g.travellers.update(1 / 60, g);
  }
  const c = g.travellers.caravans[0];
  if (!c) return null;
  // 近づくと会話で止まるので、離れて見る
  p.x = c.x + 60; p.z = c.z + 60; p.y = g.groundHeight(p.x, p.z);
  return { hasDray: !!c.dray, name: c.dray ? c.dray.name : null,
    rig: c.dray ? c.dray.rigName : null };
});

const born = await summon();
console.log('隊商:', JSON.stringify(born));
ok('隊商に駄馬がいる', !!born && born.hasDray, born ? String(born.name) : 'なし');
if (born) ok('四つ足で組んである', born.rig === 'beast', String(born.rig));

/* ---------- 1. 荷車の前に、繋がって立っている ---------- */
const hitch = await page.evaluate(async () => {
  const g = window.__game;
  const { MODEL_BOUNDS } = await import('/src/render/mesh.js');
  const c = g.travellers.caravans[0];
  if (!c || !c.dray) return { none: true };
  const d = c.dray;
  const samples = [];
  for (let i = 0; i < 60 * 20; i++) {
    g.travellers.update(1 / 60, g);
    if (i % 30) continue;
    const fx = Math.sin(c.yaw), fz = Math.cos(c.yaw);
    const rx = fz, rz = -fx;
    const h = c.hitchPoint();
    samples.push({
      // 進行方向で見た、駄馬と荷車の前後関係
      ahead: (d.x - c.cartX) * fx + (d.z - c.cartZ) * fz,
      // 轅の先（頸木）と駄馬の間
      gap: Math.hypot(h.x - d.x, h.z - d.z),
      // 隊列の中心線からの左右のずれ
      side: Math.abs((d.x - c.x) * rx + (d.z - c.z) * rz),
      // 荷車の向きと駄馬の向きの差（度）
      yaw: Math.abs(((d.yaw - c.yaw + Math.PI * 3) % (Math.PI * 2)) - Math.PI) * 180 / Math.PI,
    });
  }
  const max = (k) => +Math.max(...samples.map((s) => s[k])).toFixed(2);
  const min = (k) => +Math.min(...samples.map((s) => s[k])).toFixed(2);
  const cb = MODEL_BOUNDS.cart;
  return { aheadMin: min('ahead'), aheadMax: max('ahead'),
    gapMax: max('gap'), sideMax: max('side'), yawMax: max('yaw'),
    // モデルの向き：長手が +Z を向いていること
    spanX: +(cb.maxX - cb.minX).toFixed(2), spanZ: +(cb.maxZ - cb.minZ).toFixed(2),
    n: samples.length };
});
if (!hitch.none) {
  console.log(`繋がり: 荷車の前に ${hitch.aheadMin}〜${hitch.aheadMax}m　頸木との間 最大 ${hitch.gapMax}m　左右のずれ 最大 ${hitch.sideMax}m　向きの差 最大 ${hitch.yawMax}度　荷車モデル X${hitch.spanX} × Z${hitch.spanZ}`);
  ok('荷車のモデルが進行方向（+Z）に長い', hitch.spanZ > hitch.spanX * 1.5,
    `X${hitch.spanX} × Z${hitch.spanZ}`);
  ok('駄馬は荷車の前にいる', hitch.aheadMin > 2.5 && hitch.aheadMax < 5.5,
    `${hitch.aheadMin}〜${hitch.aheadMax}m`);
  ok('轅の先と離れない（繋がって見える）', hitch.gapMax < 1.0, `最大 ${hitch.gapMax}m`);
  ok('隊列の中心から外れない', hitch.sideMax < 0.6, `最大 ${hitch.sideMax}m`);
  ok('荷車と同じ向きを向く', hitch.yawMax < 8, `最大 ${hitch.yawMax}度`);
}

/* ---------- 2. 隊商が動けば動き、止まれば止まる ---------- */
const gaitTest = await page.evaluate(() => {
  const g = window.__game;
  const c = g.travellers.caravans[0];
  if (!c || !c.dray) return { none: true };
  const d = c.dray;
  // 歩いているとき
  const x0 = d.x, z0 = d.z, cx0 = c.x, cz0 = c.z, g0 = d.gait;
  const speeds = [];
  for (let i = 0; i < 60 * 10; i++) {
    g.travellers.update(1 / 60, g);
    speeds.push(d.speedNow);
  }
  const walk = {
    drayMoved: Math.hypot(d.x - x0, d.z - z0),
    vanMoved: Math.hypot(c.x - cx0, c.z - cz0),
    gait: d.gait - g0,
    speedMed: speeds.sort((a, b) => a - b)[speeds.length >> 1],
    rest: d.rest,
  };
  // 止まっているとき（脅威で足を止めるのと同じ状態にする）
  const x1 = d.x, z1 = d.z, g1 = d.gait;
  for (let i = 0; i < 60 * 6; i++) { c.stopped = 1; g.travellers.update(1 / 60, g); }
  const stop = {
    drayMoved: Math.hypot(d.x - x1, d.z - z1),
    gait: d.gait - g1,
    speed: d.speedNow,
    rest: d.rest,
  };
  c.stopped = 0;
  const r = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, +v.toFixed(3)]));
  return { walk: r(walk), stop: r(stop) };
});
if (!gaitTest.none) {
  console.log(`歩き: 駄馬 ${gaitTest.walk.drayMoved}m / 隊商 ${gaitTest.walk.vanMoved}m　歩調 ${gaitTest.walk.gait}rad　速さ中央 ${gaitTest.walk.speedMed}m/s`);
  console.log(`止まり: 駄馬 ${gaitTest.stop.drayMoved}m　歩調 ${gaitTest.stop.gait}rad　速さ ${gaitTest.stop.speed}m/s`);
  ok('隊商が進めば駄馬も同じだけ進む',
    Math.abs(gaitTest.walk.drayMoved - gaitTest.walk.vanMoved) < 1.0,
    `${gaitTest.walk.drayMoved}m / ${gaitTest.walk.vanMoved}m`);
  ok('歩調が隊商の速さから出ている',
    gaitTest.walk.speedMed > 1.0 && gaitTest.walk.speedMed < 2.0,
    `${gaitTest.walk.speedMed}m/s`);
  ok('歩いているあいだは脚が回る', gaitTest.walk.gait > 40, `${gaitTest.walk.gait}rad/10秒`);
  ok('止まれば駄馬も止まる', gaitTest.stop.drayMoved < 0.5, `${gaitTest.stop.drayMoved}m`);
  ok('止まっているあいだ足踏みしない', gaitTest.stop.gait === 0, `${gaitTest.stop.gait}rad`);
}

/* ---------- 3. 止まりと歩きで様子が変わる ---------- */
const look = await page.evaluate(async () => {
  const g = window.__game;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const c = g.travellers.caravans[0];
  if (!c || !c.dray) return { none: true };
  const d = c.dray;
  const hi = d.pose.rig.index.get('head');
  // 頭の高さは、姿勢を解いて初めて出る。描画を待たずに自分で解く
  const head = () => {
    d.emit(g.renderer, performance.now() / 1000, g);
    return d.pose.world[hi][13] - d.y;
  };
  g.enemies.length = 0;
  c.stopped = 0;
  for (let i = 0; i < 60 * 6; i++) g.travellers.update(1 / 60, g);
  const walking = { head: head(), graze: d.graze, alert: d.alert };

  // 脅威で足を止める → 顔を上げて耳を伏せる。
  // 護衛が賊を仕留めてしまうと脅威が消えて測っている状態が変わるので、
  // ここでは倒れない賊にする（見たいのは戦いではなく駄馬の様子）
  const ex = c.x + 15, ez = c.z;
  const e = spawnEnemy('bandit', { x: ex, y: g.groundHeight(ex, ez), z: ez, yaw: 0, leash: 900 });
  e.aggro = true; e.arch.passive = true; e.maxHp = 1e7; e.hp = 1e7;
  g.enemies.push(e);
  for (let i = 0; i < 60 * 6; i++) g.travellers.update(1 / 60, g);
  const alerted = { head: head(), graze: d.graze, alert: d.alert,
    dist: Math.hypot(e.x - c.x, e.z - c.z) };
  g.enemies.length = 0;

  // 脅威なしで足を止める → 草を食む
  const grazeHeads = [], grazeVals = [];
  for (let i = 0; i < 60 * 14; i++) {
    c.stopped = 1;
    g.travellers.update(1 / 60, g);
    if (i % 30 === 0 && i > 60 * 5) { grazeHeads.push(head()); grazeVals.push(d.graze); }
  }
  const grazing = { head: Math.min(...grazeHeads), graze: Math.max(...grazeVals),
    alert: d.alert };
  c.stopped = 0;
  const r = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, +v.toFixed(3)]));
  return { walking: r(walking), grazing: r(grazing), alerted: r(alerted) };
});
if (!look.none) {
  console.log(`頭の高さ: 歩き ${look.walking.head}m / 止まって草を食む ${look.grazing.head}m / 脅威を見る ${look.alerted.head}m`);
  console.log(`草を食む ${look.grazing.graze} / 顔を上げる ${look.alerted.alert}（賊まで ${look.alerted.dist}m）`);
  ok('止まると首を下げて草を食む', look.grazing.graze > 0.5, `${look.grazing.graze}`);
  ok('歩いているときより頭が低い', look.grazing.head < look.walking.head - 0.15,
    `${look.walking.head}m → ${look.grazing.head}m`);
  ok('歩き出せば首を戻す', look.walking.graze < 0.1, `${look.walking.graze}`);
  ok('脅威で止まったときは草を食まない', look.alerted.graze < 0.1, `${look.alerted.graze}`);
  ok('脅威を見ると顔を上げる', look.alerted.alert > 0.8 &&
    look.alerted.head > look.grazing.head + 0.25,
    `${look.alerted.alert} / ${look.grazing.head}m → ${look.alerted.head}m`);
}

/* ---------- 4. 地面から浮かない・沈まない ---------- */
const ground = await page.evaluate(() => {
  const g = window.__game;
  const c = g.travellers.caravans[0];
  if (!c || !c.dray) return { none: true };
  const d = c.dray;
  c.stopped = 0;
  let worst = 0, cartWorst = 0;
  for (let i = 0; i < 60 * 30; i++) {
    g.travellers.update(1 / 60, g);
    worst = Math.max(worst, Math.abs(d.y - g.world.height(d.x, d.z)));
    cartWorst = Math.max(cartWorst, Math.abs(c.cartY - g.world.height(c.cartX, c.cartZ)));
  }
  // 地下では隊商ごと畳まれる
  const poi = g.pois.find((q) => q.dungeonTheme);
  g.enterDungeon(poi);
  g.travellers.update(1 / 60, g);
  const inDungeon = g.travellers.caravans.length;
  g.exitDungeon();
  return { worst: +worst.toFixed(3), cartWorst: +cartWorst.toFixed(3), inDungeon };
});
if (!ground.none) {
  console.log(`地面との差: 駄馬 最大 ${ground.worst}m / 荷車 最大 ${ground.cartWorst}m　地下の隊商 ${ground.inDungeon}`);
  ok('駄馬が地面に埋まらない・浮かない', ground.worst < 0.05, `最大 ${ground.worst}m`);
  ok('地下に駄馬はいない（隊商ごと畳まれる）', ground.inDungeon === 0, `${ground.inDungeon}`);
}

/* ---------- 5. 荷が積まれて見える・描画に積まれている ---------- */
const draw = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { MODEL_BOUNDS } = await import('/src/render/mesh.js');
  // 描かれるところまで近づく（会話で止まらない距離）
  g.travellers.caravans.length = 0;
  const rt = g.world.routes.filter((r) => r.pts.length >= 10)
    .sort((a, b) => b.length - a.length)[0];
  const at = rt.pts[3];
  p.x = at.x + 150; p.z = at.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 60 * 8 && !g.travellers.caravans.length; i++) {
    g.travellers.caravanCd = 0;
    g.travellers.update(1 / 60, g);
  }
  const c = g.travellers.caravans[0];
  if (!c) return { none: true };
  p.x = c.x + 14; p.z = c.z + 6; p.y = g.groundHeight(p.x, p.z);
  g.camera.yaw = Math.atan2(c.x - p.x, c.z - p.z);
  // 描画の道筋を通っているかは、数を数えるより「呼ばれたか」で見るほうが確か。
  // 画面の絵を比べる測定は、この環境ではあてにならない（tests/README.md）
  let called = 0;
  const orig = c.dray.emit.bind(c.dray);
  c.dray.emit = (...a) => { called++; return orig(...a); };
  await new Promise((r) => setTimeout(r, 1600));
  c.dray.emit = orig;
  const cnt = (n) => { const b = g.renderer.batchFor(n); return b ? b.count : null; };
  // 駄馬ひとつが積むインスタンスの数
  const slimB = g.renderer.batchFor('partSlim');
  const before = slimB.count;
  orig(g.renderer, 0, g);
  const drayParts = slimB.count - before;
  const lb = MODEL_BOUNDS.cart_load;
  return {
    cart: cnt('cart'), load: cnt('cart_load'), called, drayParts,
    loadH: +lb.maxY.toFixed(2), loadZ: +(lb.maxZ - lb.minZ).toFixed(2),
    loadX: +(lb.maxX - lb.minX).toFixed(2),
    slim: cnt('partSlim'),
  };
});
if (!draw.none) {
  console.log(`描画: cart=${draw.cart} cart_load=${draw.load} partSlim=${draw.slim}　駄馬の描画呼び出し ${draw.called}回・${draw.drayParts}インスタンス　荷の寸法 X${draw.loadX} × Z${draw.loadZ} × 高さ ${draw.loadH}`);
  ok('荷のモデルがある', draw.load !== null);
  ok('荷が描画に積まれている', draw.load > 0, `${draw.load} インスタンス`);
  ok('荷が荷台に収まっている', draw.loadX < 1.25 && draw.loadZ < 2.4,
    `X${draw.loadX} × Z${draw.loadZ}`);
  ok('荷が側板より高く積まれている（載っているのが見える）', draw.loadH > 1.28,
    `${draw.loadH}m`);
  ok('駄馬が毎フレーム描かれている', draw.called > 0, `${draw.called}回`);
  ok('駄馬が実際にインスタンスを積む', draw.drayParts >= 20, `partSlim +${draw.drayParts}`);
}

/* ---------- 6. 面々の見た目に差がある ---------- */
const crew = await page.evaluate(() => {
  const g = window.__game;
  const c = g.travellers.caravans[0];
  if (!c) return { none: true };
  const key = (m) => m.tint.map((v) => v.toFixed(2)).join(',');
  const sig = (m) => (m.extras || []).map((e) => `${e.joint}:${e.shape}`).join('|');
  const people = c.members.map((m) => ({ kind: m.kind, name: m.npcName,
    tint: key(m), extras: (m.extras || []).length, sig: sig(m) }));
  return { people, tints: new Set(people.map((p) => p.tint)).size,
    sigs: new Set(people.map((p) => p.sig)).size };
});
if (!crew.none) {
  console.log('面々:', JSON.stringify(crew.people.map((p) => `${p.name}(${p.tint} 付物${p.extras})`)));
  ok('親方と護衛で色が違う', crew.tints >= 4, `${crew.tints}通り`);
  ok('全員が同じ格好ではない', crew.sigs >= 3, `${crew.sigs}通り`);
  ok('親方は一目で分かる', crew.people[0].name === '隊商の親方' && crew.people[0].extras >= 6,
    `${crew.people[0].name} 付物${crew.people[0].extras}`);
  ok('護衛にも付け物がある', crew.people.slice(1).every((p) => p.extras >= 4),
    crew.people.slice(1).map((p) => p.extras).join(','));
}

/* ---------- 7. 重さ ---------- */
const cost = await page.evaluate(() => {
  const g = window.__game;
  const c = g.travellers.caravans[0];
  if (!c || !c.dray) return { none: true };
  const d = c.dray;
  const t = performance.now.bind(performance);
  const med = (l) => l.sort((a, b) => a - b)[l.length >> 1];
  // 一度に長く測ると、SwiftShader の温まり具合で 20〜130µs と揺れる。
  // 短い塊を交互に測って中央値を取る（片方だけが冷えた回に当たらないように）
  const block = (fn, n) => { const t0 = t(); for (let i = 0; i < n; i++) fn(i); return (t() - t0) / n * 1000; };
  const gd = c.members.find((m) => m.kind === 'guard');
  const upd = [], emit = [], guard = [];
  for (let k = 0; k < 7; k++) {
    upd.push(block(() => d.update(1 / 60, g, null), 600));
    emit.push(block((i) => d.emit(g.renderer, i / 60, g), 600));
    // 比べる相手：もとからいる護衛ひとり。駄馬だけが重いのかを見る
    guard.push(block((i) => gd.emit(g.renderer, i / 60, g), 600));
  }
  const updUs = med(upd), emitUs = med(emit), guardUs = med(guard);
  // 往来ぜんぶ
  const allUs = block(() => g.travellers.update(1 / 60, g), 600);
  return { updUs: +updUs.toFixed(1), emitUs: +emitUs.toFixed(1), allUs: +allUs.toFixed(0),
    guardUs: +guardUs.toFixed(1),
    people: g.travellers.memberCount, vans: g.travellers.caravans.length };
});
if (!cost.none) {
  console.log(`1 フレーム: 駄馬の更新 ${cost.updUs}µs ＋ 描画 ${cost.emitUs}µs（比較：護衛ひとりの描画 ${cost.guardUs}µs）　往来ぜんぶ ${cost.allUs}µs（道にいる人 ${cost.people} / 隊商 ${cost.vans}）`);
  ok('測るあいだ隊商がいる（空の測定にしない）', cost.vans >= 1 && cost.people >= 4,
    `${cost.vans}隊 ${cost.people}人`);
  ok('駄馬の更新が軽い', cost.updUs < 60, `${cost.updUs}µs`);
  // 獣リグは 23 パーツ＋付け物 6、人は 18 パーツ＋付け物。倍前後で収まるはず
  ok('駄馬の描画は人ひとりぶんの範囲に収まる', cost.emitUs < cost.guardUs * 2.5 && cost.emitUs < 300,
    `${cost.emitUs}µs（護衛 ${cost.guardUs}µs）`);
  ok('往来ぜんぶでも毎フレームの負担が小さい', cost.allUs < 400, `${cost.allUs}µs`);
}

report(errs);
await close();
