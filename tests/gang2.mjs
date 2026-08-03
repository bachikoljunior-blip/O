// gang2
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360 });

await page.evaluate(() => {
  const g = window.__game, p = g.player;
  for (let i = 0; i < 8000; i++) {
    const a = i * 2.39996, r = 30 + i * 0.25;
    const x = p.x + Math.cos(a) * r, z = p.z + Math.sin(a) * r;
    if (g.blockers.at(x, z)) continue;
    const out = [0, 0];
    if (g.collide(x, z, 3.5, out)) continue;
    const h0 = g.groundHeight(x, z);
    let flat = true;
    for (let d = 1; d <= 26; d += 2) if (Math.abs(g.groundHeight(x + d, z) - h0) > 0.9) { flat = false; break; }
    if (!flat || h0 < 1) continue;
    p.x = x; p.z = z; p.y = h0; return;
  }
});

const r = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const DT = 1 / 60;

  /**
   * 完璧に読む回避手。
   * いちばん早く判定が届く一撃を見て、その判定が始まる瞬間に転がる。
   * 人間には無理な精度なので、ここで食らう分は「腕では避けられない」分。
   */
  const perfect = (kind, n, T, roll) => {
    g.enemies.length = 0;
    const cx = p.x, cz = p.z, cy = p.y;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      const x = cx + Math.cos(a) * 7, z = cz + Math.sin(a) * 7;
      const e = spawnEnemy(kind, { x, y: g.groundHeight(x, z), z, yaw: a + Math.PI, leash: 900 });
      e.aggro = true; e.aiState = 'chase';
      g.enemies.push(e);
    }
    const realMax = p.maxHp;
    p.maxHp = realMax * 1000; p.hp = p.maxHp;
    p.x = cx; p.z = cz; p.y = cy;
    let hits = 0, rolls = 0, blockedByGrace = 0;
    let hp0 = p.hp;
    for (let i = 0; i < T * 60; i++) {
      if (roll && p.canAct() && p.stamina > 25) {
        // いちばん早く当たる一撃の、判定開始までの残り時間
        let soon = 1e9;
        for (const e of g.enemies) {
          if (e.dead || e.state !== 'attack' || !e.attack) continue;
          const d = Math.hypot(e.x - p.x, e.z - p.z);
          if (d > e.attack.range + 1.2) continue;
          const left = e.attack.windup - e.animT;
          if (left >= -0.02 && left < soon) soon = left;
        }
        // 判定が始まる直前に転がる（無敵 0.34 秒で覆う）
        if (soon <= DT * 1.5) {
          const a = Math.random() * Math.PI * 2;
          p.doDodge(Math.sin(a), Math.cos(a), 1, g);
          rolls++;
        }
      }
      for (const e of g.enemies) e.update(DT, g);
      g.updateAggroTokens(DT);
      p.update(DT, g);
      if (p.hp < hp0) { hits++; hp0 = p.hp; }
      void blockedByGrace;
      // 逃げ切らないよう、離れすぎたら戻す
      if (Math.hypot(p.x - cx, p.z - cz) > 6) { p.x = cx; p.z = cz; p.y = cy; }
    }
    const dmg = (p.maxHp - p.hp) / 1000;
    g.enemies.length = 0;
    p.maxHp = realMax; p.hp = realMax; p.stamina = p.maxStamina;
    return { kind, n, hits, rolls, dmgPerSec: +(dmg / T * 1000 / realMax * 100).toFixed(1) };
  };

  const out = [];
  for (const [kind, n] of [['bandit', 1], ['bandit', 3], ['bandit', 5], ['wolf', 4], ['knight', 3]]) {
    const stand = perfect(kind, n, 20, false);
    const dodge = perfect(kind, n, 20, true);
    out.push({ kind, n, standHits: stand.hits, dodgeHits: dodge.hits, rolls: dodge.rolls,
      standPct: stand.dmgPerSec, dodgePct: dodge.dmgPerSec });
  }
  return out;
});

console.log('20秒、完璧に読んで転がった場合（被弾猶予あり＝実際の設定）:');
console.log('  相手          棒立ち被弾   完璧回避で被弾   減った割合   毎秒の体力減（%）');
for (const f of r) {
  const cut = f.standHits ? Math.round((1 - f.dodgeHits / f.standHits) * 100) : 0;
  console.log(`  ${(f.kind+' '+f.n+'体').padEnd(13)} ${String(f.standHits).padStart(6)}回 `
    + `${String(f.dodgeHits).padStart(12)}回 ${String(cut).padStart(10)}% `
    + `${String(f.standPct).padStart(8)} → ${f.dodgePct}`);
}
report(errs);
await close();
