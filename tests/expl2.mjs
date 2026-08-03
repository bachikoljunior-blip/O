// expl2
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 700, height: 400, audio: true });

/* ---------- 1. 宝箱の等級が距離で伸びる ---------- */
const chests = await page.evaluate(async () => {
  const D = await import('/src/game/data.js');
  const g = window.__game;
  const list = [];
  for (const poi of g.pois) {
    if (!poi.chest) continue;
    list.push({ type: poi.type, region: poi.region, danger: poi.danger,
      tier: Math.max(1, Math.min(5, poi.chest.tier)),
      rim: Math.round(Math.hypot(poi.x, poi.z)) });
  }
  let echo = 0;
  for (const c of list) echo += D.CHEST_TABLE[c.tier].echo;
  return { list, echo };
});
const byTier = {};
for (const c of chests.list) (byTier[c.tier] = byTier[c.tier] || []).push(c);
console.log(`宝箱 ${chests.list.length} 個、合計 ${chests.echo} 残響`);
for (const t of [1,2,3,4,5]) {
  const l = byTier[t] || [];
  if (!l.length) { console.log(`  等級${t}  0個`); continue; }
  const rs = l.map(c=>c.rim).sort((a,b)=>a-b);
  console.log(`  等級${t}  ${String(l.length).padStart(3)}個  大陸中心から ${rs[0]}〜${rs[rs.length-1]}m（中央${rs[Math.floor(rs.length/2)]}）`);
}
const inner = chests.list.filter(c=>c.rim < 1150), outer = chests.list.filter(c=>c.rim > 1640);
const avg = (l)=> l.length ? +(l.reduce((a,c)=>a+c.tier,0)/l.length).toFixed(2) : 0;
console.log(`内側(<1150m) ${inner.length}個 平均等級 ${avg(inner)} / 外縁(>1640m) ${outer.length}個 平均等級 ${avg(outer)}`);
ok('最上等級の宝箱が世界に存在する', (byTier[5]||[]).length > 0, `${(byTier[5]||[]).length}個`);
ok('外縁ほど良い宝箱がある', avg(outer) > avg(inner) + 0.4, `内側 ${avg(inner)} → 外縁 ${avg(outer)}`);
ok('等級がすべての段階で使われる', [1,2,3,4,5].every(t => (byTier[t]||[]).length > 0),
  [1,2,3,4,5].map(t=>`${t}:${(byTier[t]||[]).length}`).join(' '));
ok('外縁でも等級だけで埋め尽くされない', avg(outer) < 5, String(avg(outer)));

/* ---------- 2. 発見で残響が入る ---------- */
const disc = await page.evaluate(async () => {
  const D = await import('/src/game/data.js');
  const g = window.__game, p = g.player;
  const sfx = []; const oPlay = g.audio.play.bind(g.audio);
  g.audio.play = (n, o) => { sfx.push(n); return oPlay(n, o); };
  const px = p.x, pz = p.z;
  const before = p.echo;
  const gains = [];
  // 未踏の場所へ順に立つ
  const targets = g.pois.filter(q => !q.reached).slice();
  for (const q of targets) {
    p.x = q.x; p.z = q.z; p.y = g.groundHeight(q.x, q.z);
    const e0 = p.echo;
    g.updateDiscovery();
    if (p.echo > e0) gains.push({ type: q.type, danger: q.danger, echo: p.echo - e0 });
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  g.audio.play = oPlay;
  const total = p.echo - before;
  // 二度目は入らない
  const e1 = p.echo;
  for (const q of g.pois) { p.x = q.x; p.z = q.z; g.updateDiscovery(); }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  const again = p.echo - e1;
  // 参考：レベル 1→30 に要る残響
  let lv = 0;
  for (let L = 1; L < 30; L++) lv += D.levelCost(L);
  // 敵 1 体の残響（最小・最大）と比べる
  const es = Object.values(D.ENEMIES).map(e => e.echo).filter(Boolean);
  return { total, n: gains.length, again, gains, lv30: lv,
    enemyMin: Math.min(...es), enemyMax: Math.max(...es),
    sound: sfx.filter(s => s === 'discover').length,
    reached: p.reached.size, poi: g.pois.length };
});
const byType = {};
for (const gn of disc.gains) {
  byType[gn.type] = byType[gn.type] || { n: 0, min: 1e9, max: 0 };
  const b = byType[gn.type]; b.n++; b.min = Math.min(b.min, gn.echo); b.max = Math.max(b.max, gn.echo);
}
console.log(`踏破 ${disc.n} 箇所で 残響 ${disc.total}（レベル30 までに要る ${disc.lv30} の ${Math.round(disc.total/disc.lv30*100)}%）`);
for (const [k, v] of Object.entries(byType).sort((a,b)=>b[1].max-a[1].max))
  console.log(`  ${k.padEnd(12)} ${String(v.n).padStart(3)}箇所  ${v.min}〜${v.max}残響`);
ok('踏破すると残響が入る', disc.total > 0, `${disc.total}残響`);
ok('世界のすべての地に報いがある', disc.n >= disc.poi - 1, `${disc.n} / ${disc.poi}`);
ok('二度目には入らない', disc.again === 0, `${disc.again}残響`);
ok('危険な地ほど多く報われる',
  (byType.camp?.max || 0) < (byType.boss?.max || 0), `野営地 ${byType.camp?.max} < ボス ${byType.boss?.max}`);
ok('発見の音が鳴る', disc.sound >= disc.n, `${disc.sound}回`);
// 1 箇所ぶんの報いは「敵を数体倒した」くらいに収める。戦いを食わない
const one = disc.gains.map(g0 => g0.echo).sort((a,b)=>a-b);
console.log(`  1 箇所あたり ${one[0]}〜${one[one.length-1]}（敵 1 体は ${disc.enemyMin}〜${disc.enemyMax}）`);
ok('1 箇所の報いが戦いを食わない',
  one[one.length-1] <= disc.enemyMax * 2 && one[0] >= disc.enemyMin,
  `最大 ${one[one.length-1]} ≦ 敵最大 ${disc.enemyMax}×2`);
ok('探索の報いは宝箱より小さい', disc.total < chests.echo,
  `踏破 ${disc.total} < 宝箱 ${chests.echo}`);
ok('踏破が記録される', disc.reached >= disc.n, `${disc.reached}箇所`);

/* ---------- 3. 望楼は地図だけ。報いは足で稼ぐ ---------- */
const tower = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  // 状態を巻き戻す
  p.reached.clear(); p.discovered.clear();
  for (const q of g.pois) { q.reached = false; q.discovered = false; }
  const t = g.pois.find(q => q.type === 'tower');
  const near = g.pois.filter(q => Math.hypot(q.x - t.x, q.z - t.z) < 380 && q !== t);
  const px = p.x, pz = p.z;
  // 望楼から離れた場所に立って登る（近接発見が同時に走らないように）
  p.x = t.x + 5000; p.z = t.z;
  const e0 = p.echo;
  g.doInteract({ type: 'tower', obj: t });
  const echoFromTower = p.echo - e0;
  const knownAfter = near.filter(q => q.discovered).length;
  const reachedAfter = near.filter(q => q.reached).length;
  // そのうちの 1 つへ実際に行く
  const q = near.find(n => n.discovered && !n.reached);
  let echoOnArrival = 0, wasFirst = null;
  if (q) {
    const oDisc = g.ui.discover.bind(g.ui);
    g.ui.discover = (poi, echo, first) => { wasFirst = first; return oDisc(poi, echo, first); };
    p.x = q.x; p.z = q.z; p.y = g.groundHeight(q.x, q.z);
    const e1 = p.echo;
    g.updateDiscovery();
    echoOnArrival = p.echo - e1;
    g.ui.discover = oDisc;
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  return { nearN: near.length, echoFromTower, knownAfter, reachedAfter,
    echoOnArrival, wasFirst, name: q?.name };
});
console.log(`望楼から見えた ${tower.nearN} 箇所: 地図に載った ${tower.knownAfter} / 踏破扱い ${tower.reachedAfter}`);
console.log(`  そのうち「${tower.name}」へ行くと 残響 +${tower.echoOnArrival}（発見扱い=${tower.wasFirst}）`);
ok('望楼は周りを地図に載せる', tower.knownAfter > 0, `${tower.knownAfter}箇所`);
ok('地図に載っただけでは踏破にならない', tower.reachedAfter === 0, `${tower.reachedAfter}箇所`);
ok('望楼で残響は湧かない', tower.echoFromTower === 0, `${tower.echoFromTower}残響`);
ok('地図で知った地も、行けばちゃんと報われる', tower.echoOnArrival > 0, `${tower.echoOnArrival}残響`);
ok('先に地図で知っていた地は「到達」と呼ぶ', tower.wasFirst === false, String(tower.wasFirst));

/* ---------- 4. 記録画面に出る ---------- */
const st = await page.evaluate(async () => {
  const g = window.__game;
  g.ui.openMenu('quests');
  await new Promise(r => setTimeout(r, 600));
  const txt = g.ui.overlay.innerText;
  const m = txt.match(/踏破した地 — (\d+) \/ (\d+)(（地図で知る (\d+)）)?/);
  return { found: !!m, line: m ? m[0] : null };
});
console.log('記録:', st.line);
ok('記録に踏破数が出る', st.found, String(st.line));
ok('地図で知るだけの数も分かる', !!st.line && st.line.includes('地図で知る'), String(st.line));

/* ---------- 5. 保存して読み直しても消えない ---------- */
const save = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const r0 = p.reached.size, d0 = p.discovered.size, e0 = p.echo;
  g.save();
  p.reached.clear(); p.discovered.clear();
  for (const q of g.pois) { q.reached = false; q.discovered = false; }
  g.load();
  const anyReached = g.pois.filter(q => q.reached).length;
  return { r0, d0, r1: p.reached.size, d1: p.discovered.size, anyReached, e0, e1: p.echo };
});
console.log('保存:', JSON.stringify(save));
ok('踏破の記録が保存される', save.r1 === save.r0 && save.r0 > 0, `${save.r0} → ${save.r1}`);
ok('地図の記録も保存される', save.d1 === save.d0, `${save.d0} → ${save.d1}`);
ok('読み直した地に踏破の印が戻る', save.anyReached === save.r0, `${save.anyReached}`);

report(errs);
await close();
