// balance
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360 });
await page.addScriptTag({ type: 'module', content: `
  import { spawnEnemy } from '/src/game/enemies.js';
  window.__spawn = (kind, x, z) => {
    const g = window.__game;
    const e = spawnEnemy(kind, { x, y: g.groundHeight(x,z), z, yaw: 0, leash: 400 });
    if (e) { e.aggro = true; e.aiState = 'chase'; g.enemies.push(e); }
    return e;
  };
  window.__sim = async (seconds, onStep) => {
    const g = window.__game, step = 1/30, n = Math.round(seconds/step);
    for (let i = 0; i < n; i++) { g.time.now += step; g.update(step); if (onStep && onStep(i) === false) return i*step; if (i % 240 === 0) await new Promise(r => setTimeout(r, 0)); }
    return seconds;
  };
`});
await page.waitForTimeout(500);
await page.evaluate(() => { window.__game.running = false; });

/** 何もしない（ガードも回避もしない）プレイヤーが死ぬまでの秒数 */
async function ttk(encounter, level) {
  return page.evaluate(async ({ enc, lv }) => {
    const g = window.__game, p = g.player;
    g.enemies.length = 0; g.projectiles.length = 0; g.spawnTimer = -1e9;
    for (const k of ['vit','end','str','dex','arc','fth']) p.stats[k] = lv;
    p.recalc(); p.hp = p.maxHp; p.stamina = p.maxStamina; p.dead = false;
    p.setState('idle', 0.01); p.blocking = false;
    const px = p.x, pz = p.z;
    let i = 0;
    for (const [kind, n] of enc) {
      for (let k = 0; k < n; k++) {
        const a = (i++ / 6) * Math.PI * 2;
        window.__spawn(kind, px + Math.sin(a) * 7, pz + Math.cos(a) * 7);
      }
    }
    let dmgTaken = 0, hp0 = p.maxHp;
    const t = await window.__sim(60, () => {
      p.x = px; p.z = pz;               // 棒立ちの的
      if (p.dead || p.hp <= 0) return false;
      return true;
    });
    dmgTaken = hp0 - Math.max(0, p.hp);
    return { t: +t.toFixed(1), maxHp: hp0, hpLeft: Math.max(0, Math.round(p.hp)),
      dps: +(dmgTaken / Math.max(t, 0.1)).toFixed(1), dead: p.dead };
  }, { enc: encounter, lv: level });
}

/** プレイヤーが敵を倒すのにかかる秒数（自動で殴り続ける） */
async function killTime(kind, level, weapon, upg) {
  return page.evaluate(async ({ kind, lv, weapon, upg }) => {
    const g = window.__game, p = g.player;
    g.enemies.length = 0; g.spawnTimer = -1e9;
    for (const k of ['vit','end','str','dex','arc','fth']) p.stats[k] = lv;
    g.unlockWeapon(weapon); p.equip.weapon = weapon; p.upgrades[weapon] = upg;
    p.recalc(); p.hp = p.maxHp; p.stamina = p.maxStamina; p.dead = false;
    const e = window.__spawn(kind, p.x + 2.2, p.z);
    const px = p.x, pz = p.z;
    const t = await window.__sim(60, () => {
      p.hp = p.maxHp; p.stamina = p.maxStamina;
      if (!e.dead) {
        const a = Math.atan2(p.x - e.x, p.z - e.z);
        p.x = e.x + Math.sin(a) * 1.7; p.z = e.z + Math.cos(a) * 1.7;
        p.yaw = Math.atan2(e.x - p.x, e.z - p.z);
        if (p.canAct()) p.doAttack('light', g);
      }
      if (e.dead) return false;
      return true;
    });
    void px; void pz;
    return { t: +t.toFixed(1), dead: e.dead, hp: Math.round(e.hp), maxHp: e.maxHp };
  }, { kind, lv: level, weapon, upg });
}

console.log('=== 棒立ちで死ぬまでの秒数（ガード/回避なし）===');
const encs = [
  ['狼1', [['wolf', 1]], 1],
  ['狼3', [['wolf', 3]], 1],
  ['野盗2+射手1', [['bandit', 2], ['archer', 1]], 8],
  ['骸兵3', [['skeleton', 3]], 8],
  ['影追い2', [['stalker', 2]], 14],
  ['長柄兵2', [['halberdier', 2]], 14],
  ['番人1', [['warden', 1]], 20],
  ['群れの長+狼3', [['direwolf', 1], ['wolf', 3]], 20],
  ['屍術士+骸兵2', [['necromancer', 1], ['skeleton', 2]], 20],
  ['騎士2', [['knight', 2]], 25],
];
for (const [name, enc, lv] of encs) {
  const r = await ttk(enc, lv);
  console.log(`  Lv${String(lv).padStart(2)} ${name.padEnd(14)} ${r.dead ? `死亡 ${r.t}s` : `生存 (残${r.hpLeft}/${r.maxHp})`}  被ダメ/秒=${r.dps}`);
}

console.log('=== プレイヤーが倒すのにかかる秒数 ===');
for (const [kind, lv, w, u] of [
  ['wolf', 1, 'broken_sword', 0], ['bandit', 8, 'longsword', 2],
  ['stalker', 14, 'longsword', 3], ['halberdier', 14, 'longsword', 3],
  ['warden', 20, 'greatsword', 4], ['direwolf', 20, 'katana', 4],
  ['necromancer', 20, 'katana', 4], ['knight', 25, 'greatsword', 5],
]) {
  const r = await killTime(kind, lv, w, u);
  console.log(`  Lv${String(lv).padStart(2)} ${kind.padEnd(12)} ${r.dead ? `撃破 ${r.t}s` : `未撃破 (残${r.hp}/${r.maxHp})`}`);
}
report(errs);
await close();
