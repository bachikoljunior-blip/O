// ranged2
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 700, height: 400 });

await page.evaluate(() => {
  const g = window.__game, p = g.player;
  for (let i = 0; i < 8000; i++) {
    const a = i * 2.39996, r = 30 + i * 0.25;
    const x = p.x + Math.cos(a) * r, z = p.z + Math.sin(a) * r;
    if (g.blockers.at(x, z)) continue;
    const out = [0, 0];
    if (g.collide(x, z, 2.5, out)) continue;
    const h0 = g.groundHeight(x, z);
    let flat = true;
    for (let d = 1; d <= 40; d += 2) if (Math.abs(g.groundHeight(x + d, z) - h0) > 1.2) { flat = false; break; }
    if (!flat || h0 < 1) continue;
    p.x = x; p.z = z; p.y = h0; return;
  }
});

/* ---------- 1. 速い飛翔体がすり抜けない ---------- */
const tunnel = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const { TEAM } = await import('/src/game/actor.js');
  const shoot = (dist, speed) => {
    g.enemies.length = 0; g.projectiles.length = 0;
    const e = spawnEnemy('bandit', { x: p.x, y: p.y, z: p.z + dist, yaw: 0, leash: 400 });
    e.hp = e.maxHp = 99999; e.arch.passive = true;
    g.enemies.push(e);
    g.spawnProjectile({ kind: 'arrow', owner: p, damage: 100,
      x: p.x, y: p.y + 1.35, z: p.z, yaw: 0, pitch: -0.02, speed, team: TEAM.PLAYER });
    const hp0 = e.hp;
    for (let i = 0; i < 60 * 6; i++) {
      g.updateProjectiles(1/60);
      if (e.hp < hp0) return true;
      if (!g.projectiles.length) return false;
    }
    return false;
  };
  const out = [];
  for (const sp of [26, 34, 46, 90, 200, 400]) {
    let hits = 0;
    for (let k = 0; k < 6; k++) hits += shoot(8, sp) ? 1 : 0;
    out.push({ speed: sp, hits });
  }
  g.enemies.length = 0; g.projectiles.length = 0;
  return out;
});
console.log('すり抜け（8m の的へ 6 射）:', tunnel.map(v => `${v.speed}:${v.hits}/6`).join(' '));
ok('実際に使う速度では必ず当たる',
  tunnel.filter(v => v.speed <= 46).every(v => v.hits === 6),
  tunnel.filter(v=>v.speed<=46).map(v=>`${v.speed}:${v.hits}`).join(' '));
ok('どれだけ速くてもすり抜けない',
  tunnel.every(v => v.hits === 6),
  tunnel.map(v=>`${v.speed}:${v.hits}`).join(' '));

/* ---------- 2. 着弾点の予測が実際の着弾と一致する ---------- */
const predict = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { TEAM } = await import('/src/game/actor.js');
  g.enemies.length = 0;
  p.equip.weapon = 'bow'; p.recalc();
  const out = [];
  for (const pitch of [-0.25, -0.1, 0, 0.1, 0.25]) {
    p.yaw = 0; p.aimPitch = pitch;
    const w = [0, 0, 0];
    const kind = p.predictImpact(g, w);
    // 同じ構えで実際に撃つ
    g.projectiles.length = 0;
    g.spawnProjectile({ kind: 'arrow', owner: p, damage: 1,
      x: p.x, y: p.y + 1.35, z: p.z, yaw: 0, pitch, speed: 46, team: TEAM.PLAYER });
    // 予測は「地面を割った点」を返す。実測を最後に記録した点と比べると、
    // 常に 1 ステップぶん（46m/s × 1/60 ＝ 0.77m、粗い刻みなら倍）ずれる。
    // 実測側も地面との交点まで詰めて、同じものどうしを比べる
    let last = null, prev = null;
    for (let i = 0; i < 60 * 8; i++) {
      const b = g.projectiles[0];
      if (b) { prev = last; last = [b.x, b.y, b.z]; }
      g.updateProjectiles(1/60);
      if (!g.projectiles.length) break;
    }
    let land = last;
    if (last && prev) {
      // 最後の一歩を、地面を割るところで線形に切る
      const gh0 = g.groundHeight(prev[0], prev[2]);
      const gh1 = g.groundHeight(last[0], last[2]);
      const d0 = prev[1] - gh0, d1 = last[1] - gh1;
      if (d0 > 0 && d1 !== d0) {
        const u = Math.min(1.6, Math.max(0, d0 / (d0 - d1)));
        land = [prev[0] + (last[0] - prev[0]) * u, 0, prev[2] + (last[2] - prev[2]) * u];
      }
    }
    out.push({ pitch, kind,
      predicted: [+w[0].toFixed(1), +w[2].toFixed(1)],
      actual: land ? [+land[0].toFixed(1), +land[2].toFixed(1)] : null,
      errM: land ? +Math.hypot(w[0] - land[0], w[2] - land[2]).toFixed(2) : null,
      distM: +Math.hypot(w[0] - p.x, w[2] - p.z).toFixed(1) });
  }
  p.equip.weapon = 'longsword'; p.recalc();
  return out;
});
console.log('予測と実測:');
for (const v of predict) {
  console.log(`  仰角${String(v.pitch).padStart(6)}  予測 ${v.distM}m 先(${v.kind})  ずれ ${v.errM}m`);
}
// 残る誤差は刻み（遠くは 1/30 秒）の積分誤差で、距離に比例して伸びる。
// 190m 先で 0.7〜1.1m ＝ 0.4%。絶対値ではなく飛距離に対する割合で見る
ok('着弾点の予測が実際とほぼ一致する',
  predict.every(v => v.errM !== null && v.errM < Math.max(0.5, v.distM * 0.01)),
  predict.map(v=>`${v.errM}m/${v.distM}m`).join(' '));
ok('仰角を上げるほど遠くへ落ちる',
  predict.every((v, i) => i === 0 || v.distM >= predict[i-1].distM - 0.1),
  predict.map(v=>`${v.distM}m`).join(' → '));

/* ---------- 3. 敵に当たる見込みを見分ける ---------- */
const onTarget = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  p.equip.weapon = 'bow'; p.recalc();
  p.yaw = 0; p.aimPitch = -0.05;
  const w = [0, 0, 0];
  g.enemies.length = 0;
  const miss = p.predictImpact(g, w);
  const dist = Math.hypot(w[0] - p.x, w[2] - p.z);
  // その着弾点に敵を立たせる
  // 着弾点そのものではなく、その手前（矢が胴の高さを通る所）に立たせる
  const bx = p.x + (w[0] - p.x) * 0.6, bz = p.z + (w[2] - p.z) * 0.6;
  const e = spawnEnemy('bandit', { x: bx, y: g.groundHeight(bx, bz), z: bz, yaw: 0, leash: 400 });
  e.arch.passive = true;
  g.enemies.push(e);
  const hitK = p.predictImpact(g, w);
  g.enemies.length = 0;
  p.equip.weapon = 'longsword'; p.recalc();
  return { miss, hitK, dist: +dist.toFixed(1) };
});
console.log('見込み:', JSON.stringify(onTarget));
ok('何も無ければ地面に落ちると出る', onTarget.miss === 'ground', onTarget.miss);
ok('着弾点に敵がいれば敵と出る', onTarget.hitK === 'enemy', onTarget.hitK);

/* ---------- 4. 近接武器では照準を出さない ---------- */
const gating = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const w = [0, 0, 0];
  const out = {};
  p.equip.weapon = 'longsword'; p.equip.spell = null; p.recalc();
  out.melee = p.predictImpact(g, w);
  p.equip.weapon = 'bow'; p.recalc();
  out.bow = p.predictImpact(g, w);
  p.equip.weapon = 'staff'; p.equip.spell = 'soul_dart'; p.recalc();
  out.staff = p.predictImpact(g, w);
  p.equip.weapon = 'longsword'; p.recalc();     // 杖でなければ術は撃てない
  out.meleeWithSpell = p.predictImpact(g, w);
  p.equip.spell = null; p.recalc();
  return out;
});
console.log('武器ごとの照準:', JSON.stringify(gating));
ok('近接では照準を出さない', gating.melee === null, String(gating.melee));
ok('弓では照準を出す', gating.bow !== null, String(gating.bow));
ok('杖と術があれば照準を出す', gating.staff !== null, String(gating.staff));
ok('杖でなければ術の照準は出さない', gating.meleeWithSpell === null,
  String(gating.meleeWithSpell));

/* ---------- 5. 画面に印が出る ---------- */
const dom = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  g.unlocked.weapons.add('bow');
  p.equip.weapon = 'bow'; p.recalc();
  p.aimPitch = -0.05; p.yaw = 0;
  // 印は「見ている方向」に出るもの。カメラを向けないと画面外になる
  g.camera.yaw = 0; g.camera.pitch = 0.05;
  g.enemies.length = 0;
  // 2fps だと 900ms では描画が回りきらない。印に位置が入るまで待つ
  const waitDot = async (want) => {
    const el0 = document.querySelector('#aimdot');
    for (let i = 0; i < 60; i++) {
      const shown = !el0.classList.contains('hidden') && !!el0.style.left;
      if (shown === want) return;
      await new Promise(r => setTimeout(r, 150));
    }
  };
  await waitDot(true);
  const el = document.querySelector('#aimdot');
  const shownGround = !el.classList.contains('hidden');
  const posGround = { l: el.style.left, t: el.style.top };
  const onGround = el.classList.contains('on');
  // 着弾点に敵を置く
  const w = [0, 0, 0];
  p.predictImpact(g, w);
  const bx2 = p.x + (w[0] - p.x) * 0.6, bz2 = p.z + (w[2] - p.z) * 0.6;
  const e = spawnEnemy('bandit', { x: bx2, y: g.groundHeight(bx2, bz2), z: bz2, yaw: 0, leash: 400 });
  e.arch.passive = true;
  g.enemies.push(e);
  for (let i = 0; i < 60 && !el.classList.contains('on'); i++)
    await new Promise(r => setTimeout(r, 150));
  const onEnemy = el.classList.contains('on');
  // 近接に持ち替えたら消える
  p.equip.weapon = 'longsword'; p.recalc();
  await waitDot(false);
  const hiddenMelee = el.classList.contains('hidden');
  g.enemies.length = 0;
  return { shownGround, posGround, onGround, onEnemy, hiddenMelee };
});
console.log('画面の印:', JSON.stringify(dom));
ok('弓を構えると印が出る', dom.shownGround, JSON.stringify(dom.posGround));
ok('印が画面内に置かれる',
  parseFloat(dom.posGround.l) > 0 && parseFloat(dom.posGround.l) < 100
  && parseFloat(dom.posGround.t) > 0 && parseFloat(dom.posGround.t) < 100,
  `${dom.posGround.l} , ${dom.posGround.t}`);
ok('地面に落ちるときは白いまま', !dom.onGround);
ok('敵に当たる見込みなら赤くなる', dom.onEnemy);
ok('近接に持ち替えると消える', dom.hiddenMelee);

report(errs);
await close();
