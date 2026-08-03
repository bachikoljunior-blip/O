// perf1 — 1 フレームの CPU 内訳
//
// SwiftShader では GPU 側が測れない（2fps しか出ない）。ここで測るのは CPU 側だけ——
// 更新（天候・自機・地形ストリーミング・敵・村人・鳥・魚・旅人…）と、
// 描画準備（カリング・インスタンス書き込み・光源集め）の内訳。
//
// 守っていること：
//   - 数値は window.__game から取る。画面は撮らない
//   - 計測フックは game.profile() で入れる。既定はオフで、オフのときに増えるのは
//     「null かどうかの分岐」だけ。その負担も 3. で実測している
//   - 実測はぶれるので、同じ計測を何度か取っていちばん速かった回を使う

import { open, ok, report } from './harness.mjs';

const N = 400;          // 1 回の計測で回すフレーム数
const WARM = 150;       // JIT の暖機
// 同じ計測を REP 回取って「いちばん速かった回」を採る。
// この検証機は他の作業と同居していて、混んでいる回は 2 倍以上に膨らむ。
// 知りたいのは「この処理はどれだけ掛かるか」であって「今どれだけ待たされたか」
// ではないので、混雑の混ざっていない最小値を見る（中央値だと混雑を拾う）。
const REP = 5;

const { page, close, errs } = await open({ width: 640, height: 360 });

/* 昼・晴れに固定する。夜や嵐だと鳥も草も減って、内訳が軽く出る */
await page.evaluate(() => {
  const g = window.__game;
  g.sky.skipToHour(11);
  g.sky.update(1 / 60, g);
  g.sky.rainAmount = 0; g.sky.snowAmount = 0;
});

const show = (title, m) => {
  console.log(`${title} 1 フレーム ${m.wall}µs（内訳の合計 ${m.rep.total}µs）`);
  for (const [k, v] of m.rep.rows) {
    if (v >= 1) console.log(`    ${k.padEnd(20)} ${String(v).padStart(7)}µs`);
  }
  const rest = m.rep.rows.filter(([, v]) => v < 1).map(([k]) => k);
  if (rest.length) console.log(`    （1µs 未満：${rest.join('・')}）`);
};

/* ---------- 1. 更新の内訳 ---------- */
const upd = await page.evaluate(({ N, WARM, REP }) => {
  const g = window.__game;
  const runs = [];
  for (let k = 0; k < REP; k++) {
    for (let i = 0; i < WARM; i++) g.update(1 / 60);
    g.profile(true);
    const t = performance.now();
    for (let i = 0; i < N; i++) g.update(1 / 60);
    const wall = (performance.now() - t) / N * 1000;
    runs.push({ wall: +wall.toFixed(1), rep: g.profileReport() });
    g.profile(false);
  }
  runs.sort((a, b) => a.rep.total - b.rep.total);
  return { ...runs[0], all: runs.map((r) => r.rep.total),
    enemies: g.enemies.length, npcs: g.npcs.length,
    birds: g.wildlife.count, chunks: g.terrain.visible.length };
}, { N, WARM, REP });

console.log(`敵 ${upd.enemies} / 村人 ${upd.npcs} / 鳥 ${upd.birds} / 可視チャンク ${upd.chunks}`);
show('更新', upd);
console.log(`  （${REP} 回の合計を小さい順に: ${upd.all.join(' / ')}µs——上は最小の回の内訳）`);

/* ---------- 2. 描画準備の内訳 ---------- */
// renderer.render() をそのまま回すと GL（SwiftShader で 1 枚 500ms）に埋もれるので、
// CPU 側の収集だけを取り出して回す。
const draw = await page.evaluate(({ N, WARM, REP }) => {
  const g = window.__game, r = g.renderer;
  const collect = () => {
    r.frustum.fromMatrix(g.camera.viewProj);
    r.begin();
    g.emitInstances(r);
    g.collectLights(g.quality.maxLights);
  };
  const runs = [];
  for (let k = 0; k < REP; k++) {
    for (let i = 0; i < WARM; i++) collect();
    g.profile(true);
    const t = performance.now();
    // frames を数えるのは update なので、ここでは手で進める
    for (let i = 0; i < N; i++) { g._prof.frames++; collect(); }
    const wall = (performance.now() - t) / N * 1000;
    runs.push({ wall: +wall.toFixed(1), rep: g.profileReport() });
    g.profile(false);
  }
  runs.sort((a, b) => a.rep.total - b.rep.total);
  let inst = 0;
  for (const b of r.batches.values()) inst += b.count;
  return { ...runs[0], all: runs.map((x) => x.rep.total), inst, batches: r.batches.size };
}, { N, WARM, REP });

console.log(`インスタンス ${draw.inst} / バッチ ${draw.batches}`);
show('描画準備', draw);
console.log(`  （${REP} 回の合計を小さい順に: ${draw.all.join(' / ')}µs——上は最小の回の内訳）`);

/* ---------- 3. 計測フック自体の負担 ---------- */
// 測るために重くしたのでは本末転倒。オフのまま回した時間と比べる。
//
// 比べる相手に update は使わない。update は世界を進めてしまうので、
// 400 フレーム回すあいだに敵が湧いたり隊商が襲われたりして、
// その変化がまるごと「フックの負担」に化ける（実際に +64% と出たことがある）。
// 描画準備は世界を進めないので、何度回しても同じ仕事量になる。
// さらに オフ→オン→オン→オフ の順で取り、機械の混み具合が
// 一方向にずれても打ち消されるようにする。
const hook = await page.evaluate(({ N, WARM }) => {
  const g = window.__game, r = g.renderer;
  const collect = () => {
    r.frustum.fromMatrix(g.camera.viewProj);
    r.begin();
    g.emitInstances(r);
    g.collectLights(g.quality.maxLights);
  };
  const run = (n) => {
    const t = performance.now();
    for (let i = 0; i < n; i++) collect();
    return (performance.now() - t) / n * 1000;
  };
  for (let i = 0; i < WARM; i++) collect();
  // 隣り合う 2 本を 1 組にして差を取る。離れた 2 本を比べると、
  // その間に入った他の作業の分まで差に乗る
  const off = [], on = [], diff = [];
  for (let k = 0; k < 8; k++) {
    let a, b;
    if (k % 2 === 0) {                       // 順番の偏りも消す
      g.profile(false); a = run(N);
      g.profile(true); b = run(N); g.profileReport();
    } else {
      g.profile(true); b = run(N); g.profileReport();
      g.profile(false); a = run(N);
    }
    off.push(a); on.push(b); diff.push(b - a);
  }
  g.profile(false);
  const med = (l) => [...l].sort((x, y) => x - y)[l.length >> 1];
  return { off: +med(off).toFixed(1), on: +med(on).toFixed(1), diff: +med(diff).toFixed(1) };
}, { N, WARM });
console.log(`計測フック（描画準備で比較）: オフ ${hook.off}µs → オン ${hook.on}µs`
  + `（8 組の差の中央値 ${hook.diff}µs）`);

/* ---------- 4. 平方距離に置き換えた判定が元と一致する ---------- */
// 更新と描画準備の各所で Math.hypot(dx,dz) < R を dx²+dz² < R² に置き換えた。
// 1 回 70ns の hypot が毎フレーム 500 回通っていたため。
// 置き換えで判定が変わらないことを、実際に使っている閾値で確かめる。
// （距離の値そのものが要る所は hypot のままにしてある。sqrt(dx²+dz²) は
//  hypot と最終ビットが 4e-16 ずれるので、そこは触っていない）
const eq = await page.evaluate(() => {
  const Rs = [2.6, 3.2, 3.4, 4.5, 26, 40, 52, 60, 70, 90, 130, 140, 150, 190, 200, 210, 220, 300];
  let flips = 0, n = 0;
  for (const R of Rs) {
    for (let i = 0; i < 40000; i++) {
      // 境目の付近を厚めに引く
      const a = Math.random() * Math.PI * 2;
      const d = R * (0.97 + Math.random() * 0.06);
      const dx = Math.cos(a) * d, dz = Math.sin(a) * d;
      if ((Math.hypot(dx, dz) < R) !== (dx * dx + dz * dz < R * R)) flips++;
      n++;
    }
  }
  return { flips, n };
});
console.log(`平方距離への置き換え: 判定の食い違い ${eq.flips} / ${eq.n} 件`);
ok('平方距離の判定が hypot と一致する', eq.flips === 0, `${eq.flips}/${eq.n} 件`);

/* ---------- 5. 地形散布の焼き込みが元の Map と一致する ---------- */
// 描画準備でいちばん重かったのは、チャンクごとの props（Map）を毎フレーム
// 分割代入で舐めて batchFor で引き直していたところ。初回に [Batch, データ] の
// 平坦な配列へ焼くようにした。焼いたものが元と食い違えば木が消えるので、確かめる。
const baked = await page.evaluate(() => {
  const g = window.__game, r = g.renderer;
  r.frustum.fromMatrix(g.camera.viewProj);
  r.begin();
  g.emitInstances(r);
  let checked = 0, bad = 0, models = 0;
  for (const c of g.terrain.visible) {
    if (!c.props || !c.emitList) continue;
    checked++;
    // 焼いた側にある Float32Array は、すべて props の値でなければならない
    const vals = new Set(c.props.values());
    let want = 0;
    for (const [m, d] of c.props) { if (r.batchFor(m)) { want++; void d; } }
    if (c.emitList.length !== want * 2) bad++;
    for (let i = 0; i < c.emitList.length; i += 2) {
      if (!vals.has(c.emitList[i + 1])) bad++;
      if (typeof c.emitList[i].append !== 'function') bad++;
    }
    models += want;
  }
  return { checked, bad, models };
});
console.log(`焼き込み: ${baked.checked} チャンク・${baked.models} 種を照合`);
ok('焼き込んだ散布物が元の props と一致する', baked.bad === 0 && baked.checked > 0,
  `食い違い ${baked.bad} 件 / ${baked.checked} チャンク`);

/* ---------- 判定 ---------- */
// 上限の決め方：
//   1 フレームは 60fps で 16700µs。この検証機（SwiftShader を積んだ容器）は
//   スマホの CPU よりおよそ 3〜4 倍速い。ここで 2000µs 使うと、スマホでは
//   6〜8ms ＝ 1 フレームの半分近くを CPU だけで食う。そこを越えないことを見る。
//   第38周の実測（5 回の最小）は 更新 420〜600µs・描画準備 75〜130µs。
//   幅があるのは湧いている敵の数と、検証機が他の作業と同居しているため
//   （負荷 1 のときと 8 のときで 2 倍以上ちがう）。上限は実測のおよそ
//   2.5 倍に取る。ここを越えたら、混み具合ではなく作りの問題と見てよい。
ok('更新 1 フレームの合計が上限に収まる', upd.rep.total < 1600,
  `${upd.rep.total}µs < 1600µs`);
ok('描画準備 1 フレームの合計が上限に収まる', draw.rep.total < 400,
  `${draw.rep.total}µs < 400µs`);
const cpu = +(upd.rep.total + draw.rep.total).toFixed(1);
ok('CPU 側の合計が上限に収まる', cpu < 2000, `${cpu}µs < 2000µs`);
// 内訳の合計が実測とかけ離れていないこと（計測漏れの検出）。
// 取りこぼしていれば内訳だけ小さく出て、上の上限が意味を失う
ok('内訳が実測を取りこぼしていない', upd.rep.total > upd.wall * 0.5,
  `内訳 ${upd.rep.total}µs / 実測 ${upd.wall}µs`);
// 計測フックの負担。描画準備には区切りが 9 つあり、1 つにつき
// performance.now() が 1 回。実測で 1 区切り 0.2〜0.4µs、9 つで 2〜4µs。
// 上限はその 5 倍の 20µs に取る（混み具合で数µs は動く）。
// オフのときはこの 9 回が「null かどうかの分岐」に変わるだけで、
// これよりさらに 2 桁小さい
ok('計測フックの負担が小さい', hook.diff < 20, `+${hook.diff}µs`);

report(errs);
await close();
