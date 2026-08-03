// caravan1 — 隊商
//
// 街道を3分走って会うのは常に単独の一人で、荷も護衛もなく、交易の気配が
// 無かった。荷車を挟んだ一団が通るか、列を保つか、敵を見て逃げずに
// 護衛が前へ出るか、親方と取引できるかを見る。

import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/** 隊商をひとつ、街道に出す */
const summon = async () => page.evaluate(() => {
  const g = window.__game, p = g.player;
  g.sky.skipToHour(11); g.sky.update(1 / 60, g);
  g.sky.rainAmount = 0;
  g.travellers.caravans.length = 0;
  const rt = g.world.routes.filter((r) => r.pts.length >= 10)
    .sort((a, b) => b.length - a.length)[0];
  if (!rt) return null;
  const at = rt.pts[3];
  p.x = at.x + 150; p.z = at.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 60 * 6; i++) {
    g.travellers.caravanCd = 0;
    g.travellers.update(1 / 60, g);
    if (g.travellers.caravans.length) break;
  }
  const c = g.travellers.caravans[0];
  if (!c) return null;
  return { n: c.members.length, kinds: c.members.map((m) => m.kind),
    route: `${c.route.fromName}→${c.route.toName}`, dest: c.destination };
});

const born = await summon();
console.log('隊商:', JSON.stringify(born));
ok('街道に隊商が出る', !!born, born ? `${born.n}人` : 'なし');
if (born) {
  ok('親方と護衛で組む', born.kinds[0] === 'pedlar' && born.kinds.filter((k) => k === 'guard').length >= 2,
    born.kinds.join(','));
}

/* ---------- 1. 列を保って歩く ---------- */
const march = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const c = g.travellers.caravans[0];
  if (!c) return { none: true };
  // 離れて見物する（近づくと会話で止まる）
  p.x = c.x + 60; p.z = c.z + 60; p.y = g.groundHeight(p.x, p.z);
  const x0 = c.x, z0 = c.z, seg0 = c.seg;
  const spreads = [], cartGaps = [], onRoad = [];
  for (let i = 0; i < 60 * 60; i++) {
    g.travellers.update(1 / 60, g);
    if (i % 60 === 0) {
      let far = 0;
      for (const m of c.members) far = Math.max(far, Math.hypot(m.x - c.x, m.z - c.z));
      spreads.push(+far.toFixed(1));
      cartGaps.push(+Math.hypot(c.cartX - c.x, c.cartZ - c.z).toFixed(1));
      onRoad.push(g.world.roadAt(c.x, c.z) > 0.2);
    }
  }
  const moved = Math.hypot(c.x - x0, c.z - z0);
  return { moved: +moved.toFixed(1), seg0, seg: c.seg,
    spreadMax: Math.max(...spreads), spreadMed: spreads.sort((a, b) => a - b)[spreads.length >> 1],
    cartGap: cartGaps[cartGaps.length - 1],
    onRoad: onRoad.filter(Boolean).length, ticks: onRoad.length };
});
if (!march.none) {
  console.log(`60秒: ${march.moved}m 進み 区間 ${march.seg0}→${march.seg}　列の広がり 中央 ${march.spreadMed}m（最大 ${march.spreadMax}m）　荷車の位置 ${march.cartGap}m　道の上 ${march.onRoad}/${march.ticks}`);
  ok('隊商が街道を進む', march.moved > 30, `${march.moved}m`);
  ok('区間を進めている', march.seg !== march.seg0, `${march.seg0}→${march.seg}`);
  ok('列が崩れない（団子にも散り散りにもならない）',
    march.spreadMax < 18 && march.spreadMed > 2, `中央 ${march.spreadMed}m 最大 ${march.spreadMax}m`);
  ok('荷車は列の中にある', march.cartGap > 3 && march.cartGap < 9, `${march.cartGap}m`);
  ok('道の上を進む', march.onRoad >= march.ticks * 0.7, `${march.onRoad}/${march.ticks}`);
}

/* ---------- 2. 敵を見ると止まり、護衛が前へ出る ---------- */
const stand = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const c = g.travellers.caravans[0];
  if (!c) return { none: true };
  const gap = (m) => Math.hypot(m.x - c.cartX, m.z - c.cartZ);
  // 敵が来る前の、護衛と敵側の位置関係
  const ex = c.cartX + 16, ez = c.cartZ;
  g.enemies.length = 0;
  const e = spawnEnemy('bandit', { x: ex, y: g.groundHeight(ex, ez), z: ez, yaw: 0, leash: 900 });
  e.aggro = true; e.arch.passive = true;
  const before = { x: c.x, z: c.z };
  // 護衛が敵側にいるか（敵方向への射影）
  const proj = () => {
    const ax = e.x - c.cartX, az = e.z - c.cartZ;
    const al = Math.hypot(ax, az) || 1;
    return c.members.filter((m) => m.kind === 'guard')
      .map((m) => +(((m.x - c.cartX) * ax + (m.z - c.cartZ) * az) / al).toFixed(1));
  };
  const projBefore = proj();
  g.enemies.push(e);
  for (let i = 0; i < 60 * 6; i++) g.travellers.update(1 / 60, g);
  const projAfter = proj();
  const merchantGap = gap(c.merchant);
  const moved = Math.hypot(c.x - before.x, c.z - before.z);
  g.enemies.length = 0;
  return { stopped: c.stopped > 0, moved: +moved.toFixed(2),
    projBefore, projAfter, merchantGap: +merchantGap.toFixed(1) };
});
if (!stand.none) {
  console.log(`敵が来た: 足を止めた ${stand.stopped}（6秒で ${stand.moved}m）　護衛の敵方向への位置 ${stand.projBefore.join(',')} → ${stand.projAfter.join(',')}`);
  ok('敵を見ると足を止める', stand.stopped && stand.moved < 2, `${stand.moved}m`);
  // 最後尾の護衛は荷車の反対側から回るので、間に合わないことがある。
  // 「全員が敵側へ寄る」＋「少なくとも二人は荷と敵のあいだに立つ」で見る
  ok('護衛が敵側へ寄る',
    stand.projAfter.every((v, i) => v > stand.projBefore[i]),
    `${stand.projBefore.join(',')} → ${stand.projAfter.join(',')}`);
  ok('少なくとも二人が荷と敵のあいだに立つ',
    stand.projAfter.filter((v) => v > 3.5).length >= 2,
    stand.projAfter.join(','));
  ok('逃げ散らない（隊商は引き返さない）', stand.moved < 2, `${stand.moved}m`);
}

/* ---------- 3. 親方と取引できる ---------- */
const trade = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const c = g.travellers.caravans[0];
  if (!c) return { none: true };
  g.enemies.length = 0;
  c.stopped = 0;
  const m = c.merchant;
  p.x = m.x + 1.4; p.z = m.z; p.y = g.groundHeight(p.x, p.z);
  g.updateInteract();
  const lead = g.interact;
  const toasts = [];
  const oToast = g.ui.toast.bind(g.ui);
  g.ui.toast = (t2, cl) => { toasts.push(t2); return oToast(t2, cl); };
  if (lead && lead.type === 'pedlar_shop') g.doInteract(lead);
  await new Promise((r) => setTimeout(r, 700));
  const overlay = g.ui.overlay.innerText;
  const shopOpen = g.ui.overlayOpen && /品揃え/.test(overlay);
  g.ui.closeOverlay();
  // 護衛は取引ではなく会話
  const gd = c.members.find((x) => x.kind === 'guard');
  p.x = gd.x + 1.2; p.z = gd.z; p.y = g.groundHeight(p.x, p.z);
  g.updateInteract();
  const guardIt = g.interact;
  g.ui.toast = oToast;
  return { leadType: lead?.type, leadLabel: lead?.label, toasts, shopOpen,
    guardType: guardIt?.type, guardLabel: guardIt?.label };
});
if (!trade.none) {
  console.log('取引:', JSON.stringify({ lead: trade.leadLabel, shop: trade.shopOpen, guard: trade.guardLabel }));
  ok('親方とは取引になる', trade.leadType === 'pedlar_shop', String(trade.leadType));
  ok('品揃えが開く', trade.shopOpen, String(trade.shopOpen));
  ok('行き先を言う', trade.toasts.some((t2) => t2.includes('まで運ぶ荷だ')), trade.toasts[0] || '');
  ok('護衛は取引ではなく会話', trade.guardType === 'traveller', String(trade.guardType));
}

/* ---------- 4. 出過ぎない・地下にはいない ---------- */
const limits = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  let maxVans = 0;
  for (let i = 0; i < 60 * 400; i++) {
    g.travellers.update(1 / 60, g);
    maxVans = Math.max(maxVans, g.travellers.caravans.length);
  }
  const before = g.travellers.caravans.length;
  const poi = g.pois.find((q) => q.dungeonTheme);
  g.enterDungeon(poi);
  g.travellers.update(1 / 60, g);
  const inDungeon = g.travellers.caravans.length;
  g.exitDungeon();
  void p;
  return { maxVans, before, inDungeon };
});
console.log('上限:', JSON.stringify(limits));
ok('隊商は同時に一つまで', limits.maxVans <= 1, `最大 ${limits.maxVans}`);
ok('地下に隊商はいない', limits.inDungeon === 0, `${limits.inDungeon}`);

/* ---------- 5. 重さ ---------- */
const cost = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  // 上限の節で隊商が去っているので、測る前に呼び戻す。
  // 誰もいないまま測ると 0µs で通ってしまい、何も確かめたことにならない
  g.travellers.caravans.length = 0;
  const rt = g.world.routes.filter((r) => r.pts.length >= 10)
    .sort((a, b) => b.length - a.length)[0];
  const at = rt.pts[3];
  p.x = at.x + 150; p.z = at.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 60 * 8 && !g.travellers.caravans.length; i++) {
    g.travellers.caravanCd = 0;
    g.travellers.update(1 / 60, g);
  }
  const t0 = performance.now();
  for (let i = 0; i < 600; i++) g.travellers.update(1 / 60, g);
  return { us: +((performance.now() - t0) / 600 * 1000).toFixed(0),
    people: g.travellers.memberCount, vans: g.travellers.caravans.length };
});
console.log(`1 フレーム ${cost.us}µs（道にいる人 ${cost.people} / 隊商 ${cost.vans}）`);
ok('測るあいだ道に人がいる（空の測定にしない）', cost.people >= 4, `${cost.people}人`);
ok('毎フレームの負担が小さい', cost.us < 400, `${cost.us}µs`);

report(errs);
await close();
