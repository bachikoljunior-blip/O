// village1 — 村の衛兵と、村が襲われること
//
// 事前の計測（第38周）：
//   ・村は4つ、村人は 25 人。どの村にも常駐の敵は定義されていない
//   ・村の中央に 600 秒立っても、いちばん近い敵で 64m。60m 以内は 0 体
//     （徘徊の湧きが集落から 60m を避けるので、構造上ありえない）
//   ・村の広場に賊6人を置いてプレイヤーが 100m 先で 60 秒見物しても、
//     傷ついた村人 0 人・賊が村人を狙った回数 0。村人の体力は 99999、
//     賊の相手は 6 体とも player のままだった
//   ・衛兵は 0 人
// つまり村は「脅かされうる」状態ですらなかった。ここではそこを見る。

import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/* ---------- 0. 衛兵が村に立っている ---------- */
const posted = await page.evaluate(() => {
  const g = window.__game;
  const vs = g.pois.filter((p) => p.type === 'village');
  return {
    total: g.watch.length,
    villages: vs.map((v) => ({
      name: v.name, size: v.size, R: +v.plateauR.toFixed(1),
      n: g.watch.filter((w) => w.poi === v).length,
      // 持ち場が建物の中に来ていないか。中に立てば永久に出られない
      inWall: g.watch.filter((w) => w.poi === v)
        .reduce((s, w) => s + w.beat.filter((b) => !!g.blockers.at(b.x, b.z)).length, 0),
      far: g.watch.filter((w) => w.poi === v)
        .reduce((s, w) => s + w.beat.filter((b) => Math.hypot(b.x - v.x, b.z - v.z) > v.plateauR).length, 0),
    })),
    hp: g.watch[0]?.maxHp, team: g.watch[0]?.team, token: g.watch.every((w) => w.token),
    // 村人が game.npcs に、衛兵が game.watch に分かれていること
    npcRoles: [...new Set(g.npcs.map((n) => n.role))],
  };
});
console.log('衛兵:', JSON.stringify(posted));
ok('村に衛兵が立つ', posted.total >= 4, `${posted.total}人`);
ok('大きな村ほど多い',
  posted.villages.every((v) => v.n >= (v.size >= 9 ? 3 : v.size >= 6 ? 2 : 1)),
  posted.villages.map((v) => `${v.name}:${v.n}`).join(' '));
ok('持ち場が建物の中に無い', posted.villages.every((v) => v.inWall === 0),
  posted.villages.map((v) => v.inWall).join(','));
ok('持ち場は村の内側', posted.villages.every((v) => v.far === 0));
ok('衛兵は中立で、踏み込み権を最初から持つ（第37周）',
  posted.team === 2 || posted.token, `team=${posted.team} token=${posted.token}`);
ok('村人の側に衛兵は混ざらない', !posted.npcRoles.includes('watch'), posted.npcRoles.join(','));

/* ---------- 1. 持ち場を巡回する ---------- */
const patrol = await page.evaluate(() => {
  const g = window.__game;
  const v = g.pois.filter((p) => p.type === 'village').sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  const ws = g.watch.filter((w) => w.poi === v);
  g.player.x = v.x; g.player.z = v.z; g.player.y = g.world.height(v.x, v.z);
  g.enemies.length = 0;
  g.sky.paused = true; g.sky.time = 13 / 24; g.sky.update(0.016, g);
  const start = ws.map((w) => ({ x: w.x, z: w.z }));
  const seen = ws.map(() => new Set());
  let walked = ws.map(() => 0);
  let maxOut = 0;
  for (let i = 0; i < 120 * 30; i++) {
    ws.forEach((w, k) => {
      const x0 = w.x, z0 = w.z;
      w.update(1 / 30, g);
      walked[k] += Math.hypot(w.x - x0, w.z - z0);
      seen[k].add(w.postIdx);
      maxOut = Math.max(maxOut, Math.hypot(w.x - v.x, w.z - v.z));
    });
  }
  g.sky.paused = false;
  return {
    village: v.name, R: +v.plateauR.toFixed(1), n: ws.length,
    walked: walked.map((d) => +d.toFixed(1)),
    posts: seen.map((s) => s.size),
    moved: ws.map((w, k) => +Math.hypot(w.x - start[k].x, w.z - start[k].z).toFixed(1)),
    maxOut: +maxOut.toFixed(1),
    stuck: ws.filter((w) => w.stuck > 2).length,
  };
});
console.log('巡回 120 秒:', JSON.stringify(patrol));
ok('衛兵は持ち場を歩く', patrol.walked.every((d) => d > 20), `${patrol.walked.join('m, ')}m`);
ok('持ち場を二つ以上まわる', patrol.posts.every((n) => n >= 2), patrol.posts.join(','));
ok('村から出て行かない', patrol.maxOut < patrol.R * 1.15,
  `最遠 ${patrol.maxOut}m（村の半径 ${patrol.R}m）`);

/* ---------- 賊を並べる下ごしらえ ---------- */
/** watch=true ならプレイヤーは遠くで見物 */
const stage = async (n, watch) => page.evaluate(({ n, watch }) => {
  const g = window.__game, p = g.player;
  const v = g.pois.filter((q) => q.type === 'village').sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  const npcs = g.npcs.filter((x) => x.poi === v);
  const ws = g.watch.filter((x) => x.poi === v);
  // 前の節を引きずらない
  g.enemies.length = 0;
  if (g.raids.active) { g.raids.active.poi.underRaid = false; g.raids.active = null; }
  v.underRaid = false; v.lastRaidDay = -99;
  for (const w of ws) { w.dead = false; w.hp = w.maxHp; w.poise = w.maxPoise; w.state = 'idle'; w.foe = null; }
  for (const x of npcs) {
    x.dead = false; x.hp = x.maxHp; x.poise = x.maxPoise; x.state = 'idle';
    x.entryIdx = -1; x.indoors = false;
  }
  g.sky.paused = true; g.sky.time = 22.5 / 24; g.sky.update(0.016, g);
  // 村人を日課の持ち場に着かせてから始める
  p.x = v.x; p.z = v.z; p.y = g.groundHeight(p.x, p.z);
  for (let i = 0; i < 30 * 20; i++) for (const x of npcs) x.update(1 / 30, g);
  p.x = v.x + (watch ? 120 : 4); p.z = v.z; p.y = g.groundHeight(p.x, p.z);
  // 襲撃の知らせは begin() の中で出るので、ここで受けておく
  const toasts = [];
  const oToast = g.ui.toast.bind(g.ui);
  g.ui.toast = (t, cl) => { toasts.push(t); return oToast(t, cl); };
  const raid = g.raids.begin(v, g, n);
  g.ui.toast = oToast;
  return raid ? {
    village: v.name, raiders: raid.raiders.length, npcs: npcs.length, watch: ws.length,
    // 賊は村の外から来る
    spawnD: raid.raiders.map((e) => +Math.hypot(e.x - v.x, e.z - v.z).toFixed(0)),
    npcHp: npcs.map((x) => x.hp), watchHp: ws.map((x) => x.hp), toasts,
  } : null;
}, { n, watch });

/** 秒数ぶん回す。経過中に起きたことも拾う */
const run = async (sec) => page.evaluate((sec) => {
  const g = window.__game;
  const v = g.pois.filter((q) => q.type === 'village').sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  const npcs = g.npcs.filter((x) => x.poi === v);
  const ws = g.watch.filter((x) => x.poi === v);
  const foeKinds = new Set();
  let sawFlee = false, sawWatchFight = false, sawHidden = 0, resolved = null;
  const oToast = g.ui.toast.bind(g.ui);
  const toasts = [];
  g.ui.toast = (t, cl) => { toasts.push(t); return oToast(t, cl); };
  const oResolve = g.raids.resolve.bind(g.raids);
  g.raids.resolve = (gg, how) => { resolved = oResolve(gg, how); return resolved; };
  for (let i = 0; i < sec * 60; i++) {
    g.raids.update(1 / 60, g);
    g.updateAggroTokens(1 / 60);
    for (const e of g.enemies) e.update(1 / 60, g);
    for (const w of ws) w.update(1 / 60, g);
    for (const x of npcs) x.update(1 / 60, g);
    for (const e of g.enemies) if (e.foe) foeKinds.add(e.foe.role || 'other');
    for (const w of ws) if (w.foe) sawWatchFight = true;
    for (const x of npcs) if (x.activity === 'flee') sawFlee = true;
    sawHidden = Math.max(sawHidden, npcs.filter((x) => x.indoors).length);
  }
  g.ui.toast = oToast;
  g.raids.resolve = oResolve;
  return {
    foes: [...foeKinds], sawFlee, sawWatchFight, hidden: sawHidden,
    npcHp: npcs.map((x) => Math.round(x.hp)), npcDown: npcs.filter((x) => x.dead).length,
    watchHp: ws.map((x) => Math.round(x.hp)), watchDown: ws.filter((x) => x.dead).length,
    enemyHp: g.enemies.map((e) => Math.round(e.hp)), enemyDead: g.enemies.filter((e) => e.dead).length,
    active: !!g.raids.active, underRaid: !!v.underRaid, resolved, toasts,
  };
}, sec);

/* ---------- 2. 賊が村に押し寄せ、衛兵が戦う ---------- */
const s1 = await stage(4, true);
ok('襲撃を起こせる', !!s1, s1 ? `賊 ${s1.raiders} 人 / 衛兵 ${s1.watch} 人` : 'なし');
console.log('下ごしらえ:', JSON.stringify(s1));
ok('賊は村の外から来る', s1.spawnD.every((d) => d >= 50), `${s1.spawnD.join(',')}m`);
const r1 = await run(45);
console.log('見物 45 秒（賊4）:', JSON.stringify({ ...r1, toasts: r1.toasts.slice(0, 3) }));
ok('賊が村の者を狙う', r1.foes.some((f) => f === 'watch' || f === 'villager' || f === 'elder'
  || f === 'smith' || f === 'merchant' || f === 'herbalist' || f === 'hunter'
  || f === 'priest' || f === 'innkeeper'), r1.foes.join(','));
ok('衛兵が斬り返す', r1.sawWatchFight, String(r1.sawWatchFight));
ok('斬り合いになる（どちらかは血を流す）',
  r1.watchHp.some((h, i) => h < s1.watchHp[i]) || r1.enemyDead > 0 || r1.enemyHp.some((h) => h < 160),
  `衛兵 ${r1.watchHp.join(',')} / 賊 ${r1.enemyHp.join(',')}`);
ok('襲撃が知らされる', s1.toasts.some((t) => t.includes('賊が押し寄せてくる')),
  s1.toasts.join(' / '));

/* ---------- 3. 村人は戦わずに逃げ込む ---------- */
ok('村人は日課をやめて逃げる', r1.sawFlee, String(r1.sawFlee));
ok('逃げ込めた者がいる', r1.hidden > 0, `${r1.hidden}/${s1.npcs}人`);

/* ---------- 4. 放っておけば村は無事では済まない ---------- */
const s2 = await stage(6, true);
const r2 = await run(90);
console.log('見物 90 秒（賊6）:', JSON.stringify({
  npcDown: r2.npcDown, watchDown: r2.watchDown, watchHp: r2.watchHp,
  enemyDead: r2.enemyDead, resolved: r2.resolved,
}));
ok('見ているだけでは村の誰かが倒れる',
  r2.npcDown + r2.watchDown > 0 || r2.watchHp.some((h, i) => h < s2.watchHp[i] * 0.6),
  `倒れた村人 ${r2.npcDown} / 倒れた衛兵 ${r2.watchDown} / 衛兵の体力 ${r2.watchHp.join(',')}`);
ok('賊も無傷では済まない', r2.enemyDead > 0 || r2.enemyHp.some((h) => h < 160),
  r2.enemyHp.join(','));

/* ---------- 5. 加勢したかどうかで結果が変わる ---------- */
// (a) 衛兵だけで追い払った場合：礼は出ない
await stage(3, true);
const noHelp = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  const v = g.pois.filter((q) => q.type === 'village').sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  const echo0 = p.echo;
  const toasts = [];
  const oToast = g.ui.toast.bind(g.ui);
  g.ui.toast = (t, cl) => { toasts.push(t); return oToast(t, cl); };
  // 衛兵が斬ったことにする
  for (const e of g.raids.active.raiders) e.takeDamage(99999, { source: g.watch[0] }, g);
  for (let i = 0; i < 120; i++) g.raids.update(1 / 60, g);
  g.ui.toast = oToast;
  return { gained: p.echo - echo0, active: !!g.raids.active,
    underRaid: !!v.underRaid, raids: v.raids || 0, toasts };
});
console.log('衛兵だけで追い払った:', JSON.stringify(noHelp));
// 賊を斃せば残響そのものは入る（誰が斬っても敵は残響を落とす）。
// ここで見るのは「村からの礼」が出ないこと
ok('手を貸さなければ礼は出ない',
  !noHelp.toasts.some((t) => t.includes('恩に着る')), noHelp.toasts.join(' / '));
ok('それでも一件として閉じる', !noHelp.active && !noHelp.underRaid && noHelp.raids > 0,
  `raids=${noHelp.raids}`);

// (b) プレイヤーが賊を斬った場合：礼が返る
await stage(3, false);
const helped = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  const v = g.pois.filter((q) => q.type === 'village').sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  p.x = v.x + 4; p.z = v.z; p.y = g.groundHeight(p.x, p.z);
  const echo0 = p.echo;
  const toasts = [];
  const oToast = g.ui.toast.bind(g.ui);
  g.ui.toast = (t, cl) => { toasts.push(t); return oToast(t, cl); };
  for (const e of g.raids.active.raiders) e.takeDamage(99999, { source: p }, g);
  const wasHelped = !!g.raids.active.helped;
  for (let i = 0; i < 120; i++) g.raids.update(1 / 60, g);
  // 二度払われないこと
  const before = p.echo;
  for (let i = 0; i < 120; i++) g.raids.update(1 / 60, g);
  g.ui.toast = oToast;
  return { helped: wasHelped, gained: before - echo0, again: p.echo - before,
    defended: v.defended || 0, toasts };
});
console.log('加勢:', JSON.stringify(helped));
ok('加勢したと記録される', helped.helped, String(helped.helped));
ok('礼が返る', helped.gained > 0, `残響 +${helped.gained}`);
ok('礼の言葉がある', helped.toasts.some((t) => t.includes('恩に着る')),
  helped.toasts.find((t) => t.includes('恩に着る')) || helped.toasts.join(' / '));
ok('守り抜いたと知らされる', helped.toasts.some((t) => t.includes('守り抜かれた')) && helped.defended > 0,
  `defended=${helped.defended}`);
ok('二度は払われない', helped.again === 0, `${helped.again}`);

/* ---------- 6. 倒れた村人は翌朝起き上がる ---------- */
const recover = await page.evaluate(() => {
  const g = window.__game;
  const v = g.pois.filter((q) => q.type === 'village').sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  const n = g.npcs.filter((x) => x.poi === v)[0];
  g.sky.paused = true; g.sky.time = 23 / 24; g.sky.update(0.016, g);
  n.dead = false; n.hp = n.maxHp; n.poise = n.maxPoise;
  n.takeDamage(99999, { source: g.enemies[0] || null }, g);
  const down = { dead: n.dead, day: n.downDay };
  // 倒れている間は歩き出さない。倒れた勢い（のけぞり）が収まってから測る
  for (let i = 0; i < 60; i++) n.update(1 / 60, g);
  const x0 = n.x, z0 = n.z;
  for (let i = 0; i < 60 * 5; i++) n.update(1 / 60, g);
  const still = +Math.hypot(n.x - x0, n.z - z0).toFixed(2);
  // 翌朝
  g.sky.day += 1; g.sky.time = 9 / 24; g.sky.update(0.016, g);
  for (let i = 0; i < 60 * 5; i++) n.update(1 / 60, g);
  const up = { dead: n.dead, hp: n.hp, act: n.activity };
  // 落ちても倒れない（落下の痛手は受けない）
  n.hp = n.maxHp;
  const fall = n.takeDamage(9999, { type: 'fall' }, g);
  g.sky.paused = false;
  return { down, still, up, fall, hpAfterFall: n.hp };
});
console.log('倒れた村人:', JSON.stringify(recover));
ok('村人は打たれれば倒れる', recover.down.dead, String(recover.down.dead));
ok('倒れたら歩き出さない', recover.still < 0.2, `${recover.still}m`);
ok('翌朝には起き上がる', !recover.up.dead && recover.up.hp > 0,
  `dead=${recover.up.dead} hp=${recover.up.hp}`);
ok('落ちただけでは倒れない', recover.fall === 0 && recover.hpAfterFall === 110,
  `damage=${recover.fall} hp=${recover.hpAfterFall}`);

/* ---------- 7. 起きすぎない ---------- */
const gate = await page.evaluate(() => {
  const g = window.__game;
  const v = g.pois.filter((q) => q.type === 'village').sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  g.enemies.length = 0;
  if (g.raids.active) { g.raids.active.poi.underRaid = false; g.raids.active = null; }
  v.lastRaidDay = -99;
  g.sky.paused = true;
  const trial = (hour, dist) => {
    g.raids.active = null;
    g.raids.cd = 0; g.raids.check = 0;
    v.lastRaidDay = -99;
    g.sky.time = hour / 24; g.sky.update(0.016, g);
    g.player.x = v.x + dist; g.player.z = v.z; g.player.y = g.groundHeight(g.player.x, g.player.z);
    let fired = 0;
    // 30 分ぶん（＝夜ひと晩より長い）回して、何件起きるか
    for (let i = 0; i < 30 * 60 * 60; i++) {
      g.raids.update(1 / 60, g);
      if (g.raids.active) {
        fired++;
        for (const e of g.raids.active.raiders) { e.dead = true; }
        g.raids.resolve(g, 'repelled');
        g.enemies.length = 0;
        v.lastRaidDay = -99;      // 日を空ける規則はここでは外して、頻度だけ見る
      }
    }
    g.enemies.length = 0;
    return fired;
  };
  const noon = trial(12, 10);
  const nightNear = trial(23, 10);
  const nightFar = trial(23, 200);
  // 同じ村を続けて襲わない
  g.raids.active = null; g.raids.cd = 0; g.raids.check = 0;
  g.sky.time = 23 / 24; g.sky.update(0.016, g);
  g.player.x = v.x; g.player.z = v.z; g.player.y = g.groundHeight(v.x, v.z);
  v.lastRaidDay = g.sky.day;
  let sameNight = 0;
  for (let i = 0; i < 10 * 60 * 60; i++) {
    g.raids.update(1 / 60, g);
    if (g.raids.active) { sameNight++; for (const e of g.raids.active.raiders) e.dead = true; g.raids.resolve(g, 'repelled'); g.enemies.length = 0; }
  }
  g.enemies.length = 0;
  g.sky.paused = false;
  return { noon, nightNear, nightFar, sameNight };
});
console.log('頻度（30 分ぶん回して何件）:', JSON.stringify(gate));
ok('昼には起きない', gate.noon === 0, `${gate.noon}件`);
ok('村から離れていれば起きない', gate.nightFar === 0, `${gate.nightFar}件`);
ok('夜、村のそばでは起きる', gate.nightNear > 0, `30 分で ${gate.nightNear}件`);
ok('起きすぎない（30 分で 5 件まで）', gate.nightNear <= 5, `${gate.nightNear}件`);
ok('同じ村を続けて襲わない', gate.sameNight === 0, `${gate.sameNight}件`);

/* ---------- 8. 地下では起きない ---------- */
const under = await page.evaluate(() => {
  const g = window.__game;
  const v = g.pois.filter((q) => q.type === 'village').sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  const poi = g.pois.find((q) => q.dungeonTheme);
  g.raids.active = null; g.raids.cd = 0; g.raids.check = 0;
  v.lastRaidDay = -99;
  g.sky.paused = true; g.sky.time = 23 / 24; g.sky.update(0.016, g);
  g.enterDungeon(poi);
  for (let i = 0; i < 60 * 120; i++) g.raids.update(1 / 60, g);
  const started = !!g.raids.active;
  // 襲撃の最中に地下へ降りたら畳む
  g.exitDungeon();
  g.player.x = v.x; g.player.z = v.z; g.player.y = g.groundHeight(v.x, v.z);
  g.raids.begin(v, g, 3);
  const before = !!g.raids.active;
  g.enterDungeon(poi);
  g.raids.update(1 / 60, g);
  g.raids.abandon(g);
  const after = !!g.raids.active;
  g.exitDungeon();
  g.enemies.length = 0;
  g.sky.paused = false;
  return { started, before, after, underRaid: !!v.underRaid };
});
console.log('地下:', JSON.stringify(under));
ok('地下では襲撃が起きない', !under.started, String(under.started));
ok('地下へ降りれば畳まれる', under.before && !under.after && !under.underRaid);

report(errs);
await close();
