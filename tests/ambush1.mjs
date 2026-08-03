// ambush1 — 街道の襲撃
//
// 賊は隊商の脇を 120m 通り過ぎてプレイヤーへ向かっていた。道に脅威と
// 旅人が同居しているのに、互いに存在しない扱いだった。
// 賊が道の者を狙い、護衛が斬り返し、放っておけば失われ、加勢すれば
// 礼が返るところまでを見る。

import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/** 隊商と賊を並べる。watch=true ならプレイヤーは遠くで見物 */
const stage = async (nBandits, watch) => page.evaluate(async ({ nBandits, watch }) => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  g.sky.skipToHour(11); g.sky.update(1 / 60, g);
  g.travellers.caravans.length = 0;
  g.travellers.list.length = 0;
  g.enemies.length = 0;
  const rt = g.world.routes.filter((x) => x.pts.length >= 10)
    .sort((a, b) => b.length - a.length)[0];
  const at = rt.pts[3];
  p.x = at.x + 150; p.z = at.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 60 * 8 && !g.travellers.caravans.length; i++) {
    g.travellers.caravanCd = 0;
    g.travellers.update(1 / 60, g);
  }
  const c = g.travellers.caravans[0];
  if (!c) return null;
  for (let i = 0; i < nBandits; i++) {
    const bx = c.x + 13 + i, bz = c.z + (i - 1) * 3;
    const e = spawnEnemy('bandit', { x: bx, y: g.groundHeight(bx, bz), z: bz, yaw: 0, leash: 900 });
    e.aggro = true; e.aiState = 'chase';
    g.enemies.push(e);
  }
  p.x = c.x + (watch ? 110 : 6); p.z = c.z; p.y = g.groundHeight(p.x, p.z);
  return { members: c.members.length, hp: c.members.map((m) => m.hp) };
}, { nBandits, watch });

/** 秒数ぶん回す */
const run = async (sec) => page.evaluate((sec) => {
  const g = window.__game;
  // 終わったあとの状態だけ見ると、決着済みで「狙っていない・止まっていない」
  // と出る。経過中に起きたかどうかを拾う
  const sawFoes = new Set();
  let sawStopped = false;
  for (let i = 0; i < sec * 60; i++) {
    for (const e of g.enemies) e.update(1 / 60, g);
    g.updateAggroTokens(1 / 60);
    g.travellers.update(1 / 60, g);
    g.checkRescue();
    const cc = g.travellers.caravans[0];
    if (cc && cc.stopped > 0) sawStopped = true;
    for (const e of g.enemies) if (e.foe) sawFoes.add(e.foe.kind);
  }
  const c = g.travellers.caravans[0];
  return {
    memberHp: c ? c.members.map((m) => Math.round(m.hp)) : [],
    memberDead: c ? c.members.filter((m) => m.dead).length : 0,
    enemyHp: g.enemies.map((e) => Math.round(e.hp)),
    enemyDead: g.enemies.filter((e) => e.dead).length,
    raided: !!c?.raided, stopped: sawStopped,
    foes: [...sawFoes],
  };
}, sec);

/* ---------- 1. 賊が道の者を狙う ---------- */
const set1 = await stage(3, true);
ok('隊商と賊を並べられる', !!set1, set1 ? `${set1.members}人` : 'なし');
const r1 = await run(25);
console.log('見物 25 秒:', JSON.stringify(r1));
ok('賊が道の者を狙う', r1.foes.some((f) => f === 'guard' || f === 'pedlar'), r1.foes.join(','));
ok('襲撃として知らされる', r1.raided, String(r1.raided));
ok('隊商が足を止める', r1.stopped, String(r1.stopped));
ok('斬り合いになる（どちらかは血を流す）',
  r1.memberHp.some((h, i) => h < set1.hp[i]) || r1.enemyDead > 0,
  `護衛 ${r1.memberHp.join(',')} / 賊 ${r1.enemyHp.join(',')}`);

/* ---------- 2. 放っておけば隊商は無事では済まない ---------- */
const set2 = await stage(5, true);
const r2 = await run(60);
console.log('見物 60 秒（賊5）:', JSON.stringify(r2));
const hurt = r2.memberHp.filter((h, i) => h < set2.hp[i]).length;
ok('見ているだけでは隊商が傷つく', hurt > 0 || r2.memberDead > 0,
  `傷ついた ${hurt} 人 / 倒れた ${r2.memberDead} 人`);
ok('護衛は一方的に勝たない',
  r2.memberHp.some((h, i) => h < set2.hp[i] * 0.95),
  r2.memberHp.join(','));
ok('賊も無傷では済まない', r2.enemyHp.some((h) => h < 160) || r2.enemyDead > 0,
  r2.enemyHp.join(','));

/* ---------- 2.5 賭け金：守れなければ失われる ---------- */
// 護衛の命中率が 32〜44% に対し賊は 1.5〜13% しか当たっていなかった。
// 賊が振りかぶりながら遠くのプレイヤーへ向き直っていたためで、
// 直したあとは互角に殴り合う。ここではその釣り合いを見る
await stage(4, true);      // 前節で双方全滅しているので並べ直す
const hits = await page.evaluate(async () => {
  const g = window.__game;
  let guardHits = 0, banditHits = 0;
  const undo = [];
  const c = g.travellers.caravans[0];
  for (const m of c.members) {
    const o = m.takeDamage.bind(m); undo.push([m, o]);
    m.takeDamage = (d, opt, gg) => { banditHits++; return o(d, opt, gg); };
  }
  for (const e of g.enemies) {
    const o = e.takeDamage.bind(e); undo.push([e, o]);
    e.takeDamage = (d, opt, gg) => { guardHits++; return o(d, opt, gg); };
  }
  let guardSwings = 0, banditSwings = 0;
  for (let i = 0; i < 45 * 60; i++) {
    for (const e of g.enemies) if (!e.dead) e.update(1 / 60, g);
    g.updateAggroTokens(1 / 60);
    g.travellers.update(1 / 60, g);
    for (const m of c.members) if (m.state === 'attack' && m.animT < 1 / 60 + 1e-6) guardSwings++;
    for (const e of g.enemies) if (e.state === 'attack' && e.animT < 1 / 60 + 1e-6) banditSwings++;
  }
  for (const [a2, o] of undo) a2.takeDamage = o;
  return { guardSwings, guardHits, banditSwings, banditHits,
    guardRate: guardSwings ? +(guardHits / guardSwings).toFixed(2) : 0,
    banditRate: banditSwings ? +(banditHits / banditSwings).toFixed(2) : 0 };
});
console.log(`命中率: 護衛 ${hits.guardHits}/${hits.guardSwings}（${hits.guardRate}） / 賊 ${hits.banditHits}/${hits.banditSwings}（${hits.banditRate}）`);
ok('賊もちゃんと当てる（一方的にならない）',
  hits.banditRate > 0.25, `${hits.banditRate}`);
ok('護衛と賊の命中が同じ桁', Math.abs(hits.guardRate - hits.banditRate) < 0.35,
  `護衛 ${hits.guardRate} / 賊 ${hits.banditRate}`);

// 大勢に襲われれば隊商は失われる
const overrun = await stage(8, true);
const r3 = await run(60);
console.log('賊8 に 60 秒:', JSON.stringify({ dead: r3.memberDead, enemyDead: r3.enemyDead }));
ok('大勢に襲われれば守り切れない', r3.memberDead >= 2,
  `倒れた ${r3.memberDead}/${overrun.members} 人`);

// 少数なら荷は通る（親方は生き延びる）
const small = await stage(3, true);
const r4 = await run(60);
const after4 = await page.evaluate(() => {
  const c = window.__game.travellers.caravans[0];
  if (!c) return null;
  return { merchantAlive: !c.merchant.dead,
    guardHp: c.members.filter((m) => m.kind === 'guard').map((m) => Math.round(m.hp / m.maxHp * 100)) };
});
const enemyLeft = r4.enemyHp.reduce((a, b2) => a + b2, 0);
console.log('賊3 に 60 秒:', JSON.stringify({ dead: r4.memberDead, enemyDead: r4.enemyDead,
  enemyLeft, ...after4 }));
// 護衛を敵と同じ戦闘の型に載せたら、賊3 は隊商が退けられるようになった。
// 全滅しないほうが良い結果なので、主張を「押し返せる」「ただし傷は負う」に改める
ok('少数なら押し返せる', enemyLeft < 160 * 3 * 0.5,
  `賊の残り体力 ${enemyLeft} / 480`);
ok('ただし護衛は傷を負う',
  after4 && after4.guardHp.some((h) => h < 85), `護衛 ${after4?.guardHp.join('%,')}%`);
// 賊3 の勝敗は回ごとに割れる（隊商の完勝も全滅も起きる）。
// 決着そのものは五分でよいので、勝者は主張しない
void small;

/* ---------- 3. 加勢すると礼が返る ---------- */
// 前の節で賊は全滅している。倒す相手がいないと「加勢した」ことにならないので出し直す
await stage(3, true);
const rescue = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const c = g.travellers.caravans[0];
  if (!c) return { none: true };
  c.raided = true; c.rewarded = false; c.helped = false;
  const echo0 = p.echo;
  const toasts = [];
  const oToast = g.ui.toast.bind(g.ui);
  g.ui.toast = (t, cl) => { toasts.push(t); return oToast(t, cl); };
  // プレイヤーが賊を斬る
  p.x = c.x + 6; p.z = c.z; p.y = g.groundHeight(p.x, p.z);
  for (const e of g.enemies) {
    if (e.dead) continue;
    e.takeDamage(99999, { source: p }, g);
  }
  for (let i = 0; i < 60 * 3; i++) { g.travellers.update(1 / 60, g); g.checkRescue(); }
  const gained = p.echo - echo0;
  // もう一度呼んでも二重に払わない
  const before = p.echo;
  for (let i = 0; i < 120; i++) g.checkRescue();
  const again = p.echo - before;
  g.ui.toast = oToast;
  return { helped: !!c.helped, gained, again, toasts };
});
if (!rescue.none) {
  console.log('加勢:', JSON.stringify({ helped: rescue.helped, gained: rescue.gained, again: rescue.again }));
  ok('加勢したと記録される', rescue.helped, String(rescue.helped));
  ok('礼が返る', rescue.gained > 0, `残響 +${rescue.gained}`);
  ok('礼の言葉がある', rescue.toasts.some((t) => t.includes('助かった')), rescue.toasts.slice(-1)[0] || '');
  ok('二度は払われない', rescue.again === 0, `${rescue.again}`);
}

/* ---------- 4. 護衛が追い払っただけでは礼は出ない ---------- */
const noReward = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const c = g.travellers.caravans[0];
  if (!c) return { none: true };
  c.raided = true; c.rewarded = false; c.helped = false;
  g.enemies.length = 0;
  const echo0 = p.echo;
  p.x = c.x + 20; p.z = c.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 120; i++) g.checkRescue();
  return { gained: p.echo - echo0, rewarded: !!c.rewarded };
});
if (!noReward.none) {
  console.log('護衛だけで追い払った場合:', JSON.stringify(noReward));
  ok('手を貸さなければ礼は出ない', noReward.gained === 0, `残響 +${noReward.gained}`);
  ok('それでも一件として閉じる', noReward.rewarded, String(noReward.rewarded));
}

/* ---------- 5. プレイヤーが近くにいれば、そちらが優先 ---------- */
const priority = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const c = g.travellers.caravans[0];
  if (!c) return { none: true };
  g.enemies.length = 0;
  const bx = c.x + 10, bz = c.z;
  const e = spawnEnemy('bandit', { x: bx, y: g.groundHeight(bx, bz), z: bz, yaw: 0, leash: 900 });
  e.aggro = true;
  g.enemies.push(e);
  // 隊商のそばに立つ
  p.x = bx + 3; p.z = bz; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 180; i++) { e.update(1 / 60, g); g.updateAggroTokens(1 / 60); }
  const q = e.quarry(g);
  g.enemies.length = 0;
  return { quarry: q === p ? 'player' : q.kind, foe: e.foe ? e.foe.kind : null };
});
if (!priority.none) {
  console.log('プレイヤーが割って入ると:', JSON.stringify(priority));
  ok('近くにいるプレイヤーが優先される', priority.quarry === 'player', priority.quarry);
}

/* ---------- 6. 地下では起きない ---------- */
const under = await page.evaluate(async () => {
  const g = window.__game;
  const poi = g.pois.find((q) => q.dungeonTheme);
  g.enterDungeon(poi);
  let scanned = false;
  for (const e of g.enemies) { e.roadScan = 0; e.update(1 / 60, g); if (e.foe) scanned = true; }
  g.travellers.update(1 / 60, g);
  const vans = g.travellers.caravans.length;
  g.exitDungeon();
  return { scanned, vans };
});
console.log('地下:', JSON.stringify(under));
ok('地下で道の者を探さない', !under.scanned, String(under.scanned));
ok('地下に隊商はいない', under.vans === 0, `${under.vans}`);

report(errs);
await close();
