// tell3
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360 });

/* ---------- 1. 実際に構えが出る長さ ---------- */
const tells = await page.evaluate(async () => {
  const D = await import('/src/game/data.js');
  const { spawnEnemy, spawnBoss } = await import('/src/game/enemies.js');
  const g = window.__game;
  const rows = [];
  const measure = (e, phase) => {
    if (phase && e.bossDef) {
      e.phaseIndex = phase - 1;
      const ph = e.bossDef.phases[phase - 1];
      e.speedMul = ph.speedMul || 1;
    }
    const chained = new Set(e.attackList().filter(a => a.chain).map(a => a.chain));
    for (const a of e.attackList()) {
      // ゆらぎを止めて素の値を見る
      const oR = Math.random; Math.random = () => 0.5;
      e.beginAttack(a, g, false);
      Math.random = oR;
      rows.push({ owner: e.bossDef?.id || e.arch.id, atk: a.id, phase: phase || 0,
        sm: +(e.speedMul || 1).toFixed(2), dmg: a.dmg || 0,
        chain: chained.has(a.id), tell: +e.attack.windup.toFixed(3),
        base: a.windup });
      e.attack = null; e.aiState = 'idle';
    }
  };
  for (const [id, b] of Object.entries(D.BOSSES)) {
    (b.phases || []).forEach((ph, i) => {
      const e = spawnBoss(id, { x: 0, y: 0, z: 0, yaw: 0 });
      measure(e, i + 1);
    });
  }
  for (const id of Object.keys(D.ENEMIES)) {
    const e = spawnEnemy(id, { x: 0, y: 0, z: 0, yaw: 0, leash: 100 });
    if (e) measure(e, 0);
  }
  g.enemies.length = 0;
  return rows;
});
const dmgRows = tells.filter(t => t.dmg > 0);
const entry = dmgRows.filter(t => !t.chain);
const chainR = dmgRows.filter(t => t.chain);
const q = (l,p)=>{const s=[...l].sort((a,b)=>a-b);return s[Math.floor(s.length*p)];};
console.log(`威力のある技 ${dmgRows.length}（初手 ${entry.length} / 連携 ${chainR.length}）`);
console.log(`初手の構え: 最短 ${Math.min(...entry.map(t=>t.tell))}s 中央 ${q(entry.map(t=>t.tell),0.5)}s 最長 ${Math.max(...entry.map(t=>t.tell))}s`);
console.log(`連携の構え: 最短 ${Math.min(...chainR.map(t=>t.tell))}s 中央 ${q(chainR.map(t=>t.tell),0.5)}s`);
const under = entry.filter(t => t.tell < 0.30 - 1e-6);
console.log('下限を割る初手:', under.map(t=>`${t.owner}:${t.atk}(${t.tell})`).join(' ') || 'なし');
ok('威力のある初手は 0.30 秒を下回らない', under.length === 0, `${under.length}種`);
ok('連携の 2 段目は 0.20 秒を下回らない',
  chainR.every(t => t.tell >= 0.20 - 1e-6), `最短 ${Math.min(...chainR.map(t=>t.tell))}s`);
const zero = tells.filter(t => t.dmg === 0);
console.log(`威力のない所作（瞬間移動など）${zero.length} 種 最短 ${Math.min(...zero.map(t=>t.tell))}s`);
ok('移動だけの所作には下限をかけない',
  zero.some(t => t.tell < 0.30), `最短 ${Math.min(...zero.map(t=>t.tell))}s`);

/* ---------- 2. 激昂は圧を上げるが、構えは潰さない ---------- */
const rage = await page.evaluate(async () => {
  const D = await import('/src/game/data.js');
  const { spawnBoss } = await import('/src/game/enemies.js');
  const g = window.__game;
  const out = [];
  for (const [id, b] of Object.entries(D.BOSSES)) {
    const ph = (b.phases || []).find(p => (p.speedMul || 1) > 1);
    if (!ph) continue;
    const i = b.phases.indexOf(ph);
    const a = ph.attacks.find(x => x.dmg > 0);
    const mk = (phase) => {
      const e = spawnBoss(id, { x: 0, y: 0, z: 0, yaw: 0 });
      e.phaseIndex = phase;
      e.speedMul = b.phases[phase].speedMul || 1;
      const oR = Math.random; Math.random = () => 0.5;
      e.beginAttack(a, g, false);
      Math.random = oR;
      return { tell: +e.attack.windup.toFixed(3), recover: +e.attack.recover.toFixed(3) };
    };
    // 同じ技を平常時と激昂時で比べる（相 0 に同名の技があるとは限らないので素の値と比べる）
    const hot = mk(i);
    out.push({ id, atk: a.id, sm: ph.speedMul, base: a.windup, tell: hot.tell,
      baseRec: a.recover, rec: hot.recover });
  }
  g.enemies.length = 0;
  return out;
});
console.log('激昂時（素の値 → 実際）:');
for (const x of rage)
  console.log(`  ${x.id.padEnd(11)} ×${x.sm}  構え ${x.base}→${x.tell}s　硬直 ${x.baseRec}→${x.rec}s`);
ok('激昂しても構えが人の反応を割らない',
  rage.every(x => x.tell >= 0.25), `最短 ${Math.min(...rage.map(x=>x.tell))}s`);
ok('激昂すると硬直は短くなる（圧は落ちない）',
  rage.every(x => x.rec < x.baseRec), rage.map(x=>`${x.baseRec}→${x.rec}`).join(' '));
ok('それでも激昂は構えを詰める（ただ緩むのではない）',
  rage.some(x => x.tell < x.base), `${rage.filter(x=>x.tell<x.base).length}/${rage.length}体`);

/* ---------- 3. 見てから転がれる ---------- */
const dodge = await page.evaluate(async () => {
  const D = await import('/src/game/data.js');
  const { spawnBoss } = await import('/src/game/enemies.js');
  const g = window.__game, p = g.player;
  const DT = 1 / 120;
  const trial = (id, atkId, phase, delay) => {
    g.enemies.length = 0;
    const e = spawnBoss(id, { x: 0, y: g.groundHeight(0,0), z: 0, yaw: Math.PI });
    e.hp = e.maxHp = 999999;
    e.phaseIndex = phase - 1;
    e.speedMul = e.bossDef.phases[phase-1].speedMul || 1;
    e.dmgMul = (e.bossDef.phases[phase-1].dmgMul || 1) * (e.powerMul || 1);
    g.enemies.push(e);
    p.hp = p.maxHp; p.stamina = p.maxStamina;
    p.x = e.x; p.z = e.z - 2.6; p.y = e.y; p.yaw = 0;
    p.iframes = 0; p.state = 'idle'; p.attack = null;
    const a = e.attackList().find(x => x.id === atkId);
    // 構えのゆらぎだけ止める。更新中の AI の乱数は生かす（実戦に近い）
    const oR = Math.random; Math.random = () => 0.5;
    try {
      e.beginAttack(a, g, false);
      Math.random = oR;
      const until = e.attack.windup + a.active + 0.10;
      const hp0 = p.hp;
      let t = 0, rolled = false;
      while (t <= until) {
        if (!rolled && t >= delay) { p.doDodge(0, -1, 1, g); rolled = true; }
        e.update(DT, g); p.update(DT, g); t += DT;
        if (p.hp < hp0) return false;
      }
      return true;
    } finally { Math.random = oR; }
  };
  // 激昂した各ボスの、いちばん速い威力技
  const out = [];
  for (const [id, b] of Object.entries(D.BOSSES)) {
    const i = (b.phases || []).findIndex(ph => (ph.speedMul || 1) > 1);
    if (i < 0) continue;
    const ph = b.phases[i];
    const chained = new Set(ph.attacks.filter(x => x.chain).map(x => x.chain));
    const cand = ph.attacks.filter(x => x.dmg > 0 && !chained.has(x.id));
    if (!cand.length) continue;
    const a = cand.sort((x, y) => x.windup - y.windup)[0];
    // AI の揺れがあるので、各時刻を 5 回試して過半で避けられれば「避けられる」
    const okAt = [];
    for (let t = 0; t <= 0.8; t += 0.025) {
      let n = 0;
      for (let k = 0; k < 5; k++) if (trial(id, a.id, i + 1, t)) n++;
      if (n >= 3) okAt.push(+t.toFixed(3));
    }
    out.push({ id, atk: a.id, last: okAt.length ? okAt[okAt.length-1] : null,
      width: +(okAt.length * 0.025).toFixed(3) });
  }
  g.enemies.length = 0;
  return out;
});
console.log('激昂中のボスの、いちばん速い初手を見てから転がる:');
for (const x of dodge)
  console.log(`  ${x.id.padEnd(11)} ${x.atk.padEnd(14)} 間に合う最も遅い入力 ${x.last}s（窓 ${x.width}s）`);
ok('激昂中でも初手は見てから転がれる',
  dodge.every(x => x.last !== null && x.last >= 0.25),
  `最短 ${Math.min(...dodge.map(x=>x.last ?? 0))}s`);
ok('窓が目押しを強いるほど狭くない',
  dodge.every(x => x.width >= 0.15), `最狭 ${Math.min(...dodge.map(x=>x.width))}s`);

report(errs);
await close();
