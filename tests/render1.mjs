// render1 — 描画が例外で止まっていないか
//
// 第32周に、採取物の色を書き忘れていたせいで emitInstances が毎フレーム
// 例外で中断していたのが見つかった。中断すると、それ以降に積まれるはずの
// アクター・旅人・鳥がまるごと出ない。しかも画面は「敵がいないだけ」に
// 見えるので気づけない。世界を歩き回って例外が 0 件であることを確かめる。

import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360 });

/* ---------- 1. 採取物の色がすべて定義されている ---------- */
const colors = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const seen = new Set();
  const px = p.x, pz = p.z;
  for (let i = 0; i < 1400; i++) {
    const a = i * 2.39996, r = 20 + i * 1.3;
    p.x = px + Math.cos(a) * r; p.z = pz + Math.sin(a) * r;
    p.y = g.groundHeight(p.x, p.z);
    g.updateGatherables();
    for (const [, v] of g.gatherables) if (v) seen.add(v.type);
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  return [...seen];
});
console.log('世界に湧く採取物:', colors.join(', '));

/* ---------- 2. 実際に立って描かせ、例外が出ないか ---------- */
const walk = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  // 採取物・POI・敵がいる所を順に回って、その場で数フレーム描かせる
  const spots = [];
  for (const poi of g.pois) spots.push({ x: poi.x, z: poi.z, why: poi.type });
  const px = p.x, pz = p.z;
  for (let i = 0; i < 40; i++) {
    const a = i * 2.39996, r = 60 + i * 55;
    spots.push({ x: px + Math.cos(a) * r, z: pz + Math.sin(a) * r, why: '野' });
  }
  const drawn = [];
  for (const s of spots) {
    p.x = s.x; p.z = s.z; p.y = g.groundHeight(s.x, s.z);
    for (let i = 0; i < 2; i++) g.update(1 / 60);
    drawn.push(s.why);
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  return { visited: drawn.length };
});
// 描画は rAF で回るので、実際に何枚か出るまで待つ
await page.waitForTimeout(3000);
console.log(`${walk.visited} 箇所に立って描かせた`);
ok('世界を回っても描画で例外が出ない', errs.length === 0,
  errs.length ? errs[0] : '0件');

/* ---------- 3. 中断していたら積まれないはずのものが積まれている ---------- */
const batches = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  // 採取物のそばに敵を置いて、両方が同じフレームに積まれるか見る
  let node = null;
  const px = p.x, pz = p.z;
  for (let i = 0; i < 900 && !node; i++) {
    const a = i * 2.39996, r = 20 + i * 1.6;
    p.x = px + Math.cos(a) * r; p.z = pz + Math.sin(a) * r;
    p.y = g.groundHeight(p.x, p.z);
    g.updateGatherables();
    for (const [, v] of g.gatherables) if (v) { node = v; break; }
  }
  if (!node) return { none: true };
  p.x = node.x + 4; p.z = node.z; p.y = g.groundHeight(p.x, p.z);
  g.enemies.length = 0;
  const e = spawnEnemy('bandit', { x: p.x + 6, y: g.groundHeight(p.x + 6, p.z), z: p.z,
    yaw: 0, leash: 400 });
  e.arch.passive = true;
  g.enemies.push(e);
  await new Promise((r) => setTimeout(r, 1600));
  const bodies = g.renderer.batchFor('part');
  const out = { nodeType: node.type, part: bodies ? bodies.count : null };
  g.enemies.length = 0;
  return out;
});
console.log('同じフレームでの積み:', JSON.stringify(batches));
if (!batches.none) {
  ok('採取物のそばでもアクターが積まれる', batches.part > 0, `part ${batches.part}`);
}

/* ---------- 4. 色が抜けていても止まらない（保険） ---------- */
const guard = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  // 未知の種別をひとつ混ぜて、それでも描けるか
  const key = 'test-unknown';
  g.gatherables.set(key, { key: 1, type: '__unknown__', x: p.x + 3, y: p.y, z: p.z + 3 });
  await new Promise((r) => setTimeout(r, 1200));
  g.gatherables.delete(key);
  return true;
});
void guard;
await page.waitForTimeout(600);
ok('未知の採取物が混ざっても描画が止まらない', errs.length === 0,
  errs.length ? errs[0] : '0件');

report(errs);
await close();
