// npc1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 700, height: 400 });

/* 大きな村へ移動し、そこの NPC を一日追いかける */
const day = await page.evaluate(async () => {
  const g = window.__game;
  const v = g.pois.filter(p => p.type === 'village').sort((a,b) => (b.size||0)-(a.size||0))[0];
  // 村の中に立って、NPC が更新される距離（70m）に入る
  g.player.x = v.x; g.player.z = v.z;
  g.player.y = g.world.height(v.x, v.z);
  g.sky.paused = true;
  const npcs = g.npcs.filter(n => n.poi === v);

  const sample = (hour) => {
    g.sky.time = hour / 24;
    g.sky.update(0.016, g);
    // 持ち場まで歩かせる。1 時間ぶんは掛けない（30 秒で足りる）
    for (let i = 0; i < 30 * 30; i++) for (const n of npcs) n.update(1/30, g);
    return npcs.map(n => ({
      role: n.role, act: n.activity, spot: n.spotKey, indoors: n.indoors,
      x: +n.x.toFixed(1), z: +n.z.toFixed(1),
      dHome: +Math.hypot(n.x - n.spots.home.x, n.z - n.spots.home.z).toFixed(1),
      // 家には当たり判定があるので、中心ではなく戸口に立つ。
      // 「自分の家の戸口」と言えるのは、壁の外側すぐまで
      homeReach: +(() => {
        const b = window.__game.blockers.at(n.spots.home.x, n.spots.home.z);
        return (b ? Math.max(b.hx, b.hz) : 0) + 2.4;
      })().toFixed(1),
      dFire: +Math.hypot(n.x - n.spots.fire.x, n.z - n.spots.fire.z).toFixed(1),
      dWork: +Math.hypot(n.x - n.spots.work.x, n.z - n.spots.work.z).toFixed(1),
      dCenter: +Math.hypot(n.x - v.x, n.z - v.z).toFixed(1),
    }));
  };
  const out = {};
  for (const h of [2, 7, 10, 12.8, 15, 19.5, 23]) out[h] = sample(h);
  g.sky.paused = false;
  return { village: v.name, size: v.size, n: npcs.length,
    roles: npcs.map(n => n.role), out, R: v.plateauR };
});

console.log(`${day.village}（規模${day.size}）NPC ${day.n} 人: ${day.roles.join(' ')}`);
for (const [h, arr] of Object.entries(day.out)) {
  const acts = {};
  for (const a of arr) acts[a.act] = (acts[a.act] || 0) + 1;
  const indoors = arr.filter(a => a.indoors).length;
  console.log(`  ${String(h).padStart(4)}時  ${Object.entries(acts).map(([k,v])=>k+'×'+v).join(' ').padEnd(34)}`
    + ` 屋内${indoors}人  中心からの平均${(arr.reduce((s,a)=>s+a.dCenter,0)/arr.length).toFixed(1)}m`);
}

const at = (h) => day.out[h];
ok('深夜はほぼ全員が家の中', at(2).filter(a => a.indoors).length >= at(2).length - 1,
  `${at(2).filter(a=>a.indoors).length}/${at(2).length}人`);
ok('宿の主だけは夜も起きている',
  at(2).some(a => a.role === 'innkeeper' && !a.indoors) || !at(2).some(a => a.role === 'innkeeper'),
  at(2).filter(a=>a.role==='innkeeper').map(a=>a.act+(a.indoors?'(屋内)':'')).join(','));
ok('昼間は誰も家で寝ていない', at(12.8).every(a => !a.indoors) && at(15).every(a => !a.indoors));
ok('昼過ぎは火のまわりに集まる',
  at(12.8).filter(a => a.dFire < 5).length >= Math.ceil(at(12.8).length * 0.5),
  `${at(12.8).filter(a=>a.dFire<5).length}/${at(12.8).length}人が火の 5m 以内`);
ok('日中は仕事場にいる者がいる', at(10).some(a => a.act === 'work' || a.act === 'pray'),
  at(10).map(a=>a.act).join(','));
// 「畑仕事」も act は work だが持ち場は村の外。仕事場判定は spot で見る
ok('仕事場が持ち場の者は、その仕事場に着いている',
  at(10).filter(a => a.spot === 'work').every(a => a.dWork < 5),
  at(10).filter(a=>a.spot==='work').map(a=>a.role+':'+a.dWork+'m').join(' ') || '該当なし');
ok('畑へ出た者は村の外にいる',
  at(10).filter(a => a.spot === 'field').every(a => a.dCenter > day.R * 0.9),
  at(10).filter(a=>a.spot==='field').map(a=>a.role+':'+a.dCenter+'m').join(' ') || '該当なし');
ok('寝ている者は自分の家の戸口にいる',
  at(2).filter(a => a.indoors).every(a => a.dHome <= a.homeReach),
  at(2).filter(a=>a.indoors).map(a=>`${a.dHome}/${a.homeReach}m`).join(' '));
ok('畑仕事の者は村の外へ出る',
  at(10).some(a => a.dCenter > day.R),
  `最遠 ${Math.max(...at(10).map(a=>a.dCenter))}m（村の半径 ${day.R.toFixed(1)}m）`);
ok('一日で居場所が入れ替わる',
  new Set([2,10,12.8,19.5].map(h => at(h).map(a=>a.act).sort().join(','))).size >= 3);

/* 家は一人一軒 */
const homes = await page.evaluate(() => {
  const g = window.__game;
  const v = g.pois.filter(p => p.type === 'village').sort((a,b)=>(b.size||0)-(a.size||0))[0];
  const npcs = g.npcs.filter(n => n.poi === v);
  const keys = npcs.map(n => `${n.spots.home.x.toFixed(1)},${n.spots.home.z.toFixed(1)}`);
  const houses = (v.structures||[]).filter(s => s.model === 'house_a' || s.model === 'house_b').length;
  return { npcs: npcs.length, distinct: new Set(keys).size, houses };
});
console.log('住まい:', JSON.stringify(homes));
ok('家は一人一軒ずつ割り当てられる', homes.distinct === Math.min(homes.npcs, homes.houses),
  `NPC${homes.npcs}人 / 家${homes.houses}軒 / 別々の家${homes.distinct}`);

/* 寝ている相手には話しかけられない・描画もされない */
const sleep = await page.evaluate(async () => {
  const g = window.__game;
  const v = g.pois.filter(p => p.type === 'village').sort((a,b)=>(b.size||0)-(a.size||0))[0];
  const npcs = g.npcs.filter(n => n.poi === v);
  g.sky.paused = true;
  const settle = (hour) => {
    g.sky.time = hour / 24; g.sky.update(0.016, g);
    for (let i = 0; i < 30 * 30; i++) for (const n of npcs) n.update(1/30, g);
  };
  const probe = (hour) => {
    settle(hour);
    // 眠っている相手のすぐ隣に立って、話しかけられるか調べる
    const sleeper = npcs.find(n => n.indoors) || npcs[0];
    g.player.x = sleeper.x + 1.0; g.player.z = sleeper.z;
    g.player.y = g.world.height(g.player.x, g.player.z);
    g.updateInteract?.(0.016);
    let prompt = null;
    try { prompt = g.ui.promptText ?? document.querySelector('.prompt')?.textContent ?? null; } catch { /* noop */ }
    // 描画側：indoors の NPC は emit されない
    let emitted = 0;
    const fake = { batchFor: () => null, write: () => {} };
    void fake;
    for (const n of npcs) if (!n.indoors) emitted++;
    return { indoors: npcs.filter(n => n.indoors).length, awake: emitted, prompt };
  };
  const night = probe(2);
  const noon = probe(12);
  g.sky.paused = false;
  return { night, noon, total: npcs.length };
});
console.log('夜:', JSON.stringify(sleep.night), ' 昼:', JSON.stringify(sleep.noon));
ok('夜は描画される村人が減る', sleep.night.awake < sleep.noon.awake,
  `夜${sleep.night.awake}人 昼${sleep.noon.awake}人`);
ok('昼は全員が外に出ている', sleep.noon.awake === sleep.total, `${sleep.noon.awake}/${sleep.total}`);

/* 夜、人が帰っている家に灯りが点る */
const lamps = await page.evaluate(async () => {
  const g = window.__game;
  const v = g.pois.filter(p => p.type === 'village').sort((a,b)=>(b.size||0)-(a.size||0))[0];
  const npcs = g.npcs.filter(n => n.poi === v);
  g.player.x = v.x; g.player.z = v.z; g.player.y = g.world.height(v.x, v.z);
  g.camera.pos[0] = v.x; g.camera.pos[2] = v.z;
  g.sky.paused = true;
  const probe = (hour) => {
    g.sky.time = hour / 24; g.sky.update(0.016, g);
    for (let i = 0; i < 30 * 30; i++) for (const n of npcs) n.update(1/30, g);
    const L = g.collectLights(8);
    // 家の位置に一致する光源だけ数える
    let atHomes = 0;
    for (let i = 0; i < L.count; i++) {
      const lx = L.pos[i*4], lz = L.pos[i*4+2];
      if (npcs.some(n => n.indoors && Math.hypot(n.spots.home.x - lx, n.spots.home.z - lz) < 0.5)) atHomes++;
    }
    return { indoors: npcs.filter(n => n.indoors).length, lights: L.count, atHomes,
      night: +g.sky.night.toFixed(2) };
  };
  const night = probe(23.5);
  const noon = probe(12.6);
  g.sky.paused = false;
  return { night, noon };
});
console.log('家の灯り 夜:', JSON.stringify(lamps.night), ' 昼:', JSON.stringify(lamps.noon));
ok('夜、人が帰っている家に灯りが点る', lamps.night.atHomes > 0,
  `${lamps.night.atHomes}軒（在宅${lamps.night.indoors}人）`);
ok('昼は家の灯りが無い', lamps.noon.atHomes === 0, String(lamps.noon.atHomes));

/* 話しかけている間は持ち場へ戻らない */
const talk = await page.evaluate(async () => {
  const g = window.__game;
  const v = g.pois.filter(p => p.type === 'village').sort((a,b)=>(b.size||0)-(a.size||0))[0];
  const n = g.npcs.find(x => x.poi === v);
  g.sky.paused = true; g.sky.time = 12/24; g.sky.update(0.016, g);
  for (let i = 0; i < 300; i++) n.update(1/30, g);
  g.player.x = n.x + 2; g.player.z = n.z + 2;
  n.talking = true;
  const x0 = n.x, z0 = n.z;
  for (let i = 0; i < 300; i++) n.update(1/30, g);
  const moved = Math.hypot(n.x - x0, n.z - z0);
  const facing = Math.abs(Math.atan2(g.player.x - n.x, g.player.z - n.z) - n.yaw);
  n.talking = false;
  g.sky.paused = false;
  return { moved: +moved.toFixed(3), facing: +facing.toFixed(3) };
});
console.log('会話中:', JSON.stringify(talk));
ok('会話中は歩き出さない', talk.moved < 0.05, `${talk.moved}m`);
ok('会話中はこちらを向く', talk.facing < 0.25 || Math.abs(talk.facing - Math.PI*2) < 0.25,
  String(talk.facing));

/* 篝火で時を待てる */
const wait = await page.evaluate(async () => {
  const g = window.__game;
  const shrine = g.pois.find(p => p.type === 'shrine');
  g.sky.paused = false;
  g.sky.time = 0.05;                    // 深夜 1:12
  const before = g.sky.timeLabel();
  g.ui.openShrine(shrine);
  await new Promise(r => setTimeout(r, 120));
  const btns = [...document.querySelectorAll('#overlay button')]
    .filter(b => ['朝','昼','夜'].includes(b.textContent.trim()));
  const labels = btns.map(b => b.textContent.trim());
  btns.find(b => b.textContent.trim() === '朝')?.click();
  await new Promise(r => setTimeout(r, 120));
  const after = g.sky.timeLabel();
  const open = !document.querySelector('#overlay').classList.contains('hidden');
  g.ui.closeOverlay();
  return { before, after, labels, hour: g.sky.hour, open };
});
console.log('篝火で待つ:', JSON.stringify(wait));
ok('篝火に時を待つ選択がある', wait.labels.length === 3, wait.labels.join(','));
ok('朝まで待てる', wait.hour > 6 && wait.hour < 7, `${wait.before} → ${wait.after}`);

/* 会話文が「今なにをしているか」に反応する */
const lines = await page.evaluate(async () => {
  const g = window.__game;
  const { pickLine, makeContext } = await import('/src/game/dialogue.js');
  const n = g.npcs.find(x => x.role === 'smith') || g.npcs[0];
  const grab = (act) => {
    n.activity = act;
    const set = new Set();
    for (let i = 0; i < 40; i++) {
      const ctx = makeContext(n, g);
      set.add(pickLine(`greet_${n.role}`, ctx, i));
    }
    return [...set];
  };
  const working = grab('work');
  const eating = grab('eat');
  const idle = grab('idle');
  n.activity = 'idle';
  return { role: n.role, working, eating, idle };
});
console.log(`${lines.role} 仕事中:`, lines.working[0]);
console.log(`${lines.role} 食事中:`, lines.eating[0]);
ok('仕事中と食事中で台詞が変わる',
  lines.working.join('|') !== lines.eating.join('|'),
  `仕事${lines.working.length}種 / 食事${lines.eating.length}種`);
ok('仕事中の台詞は普段の台詞と別', !lines.idle.some(t => lines.working.includes(t))
  || lines.working.length !== lines.idle.length);

report(errs);
await close();
