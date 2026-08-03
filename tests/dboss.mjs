// dboss
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 960, height: 470 });


// --- 1. ダンジョン侵入 ---
let info = await page.evaluate(() => {
  const g = window.__game;
  // プレイヤーを強化しておく（検証用）
  g.player.stats.vit = 40; g.player.stats.str = 40; g.player.stats.end = 40; g.player.recalc();
  g.player.hp = g.player.maxHp;
  g.unlockWeapon('greatsword'); g.player.equip.weapon = 'greatsword';
  g.player.upgrades.greatsword = 8; g.player.recalc();
  const poi = g.pois.find(p => p.dungeonTheme === 'catacomb') || g.pois.find(p => p.type === 'grave');
  g.enterDungeon(poi);
  const d = g.dungeon;
  return { name: d.name, depth: d.depth, tier: d.tier, rank: d.rank, rooms: d.rooms.length,
    bossId: d.bossId, gates: d.gates.length, arena: !!d.bossArena, stair: !!d.stairPoint,
    bossW: d.bossRoom.w, bossH: d.bossRoom.h, atk: Math.round(g.player.weaponAttackPower()) };
});
console.log('enter:', JSON.stringify(info));
ok('第1層から始まる', info.depth === 1);
ok('霧の門がある', info.gates > 0);
ok('主の間がある', info.arena && info.stair);
ok('ボス定義がある', !!info.bossId);

await page.waitForTimeout(2500);
await page.screenshot({ path: '/tmp/db_enter.png' });

// --- 2. 霧の門の手前まで移動して見る ---
await page.evaluate(() => {
  const g = window.__game;
  const gt = g.dungeon.gates[0];
  g.player.x = gt.x + Math.sin(gt.yaw) * 5; g.player.z = gt.z + Math.cos(gt.yaw) * 5;
  g.player.y = 0;
  g.camera.yaw = gt.yaw; g.camera.snap?.(g);
});
await page.waitForTimeout(2500);
await page.screenshot({ path: '/tmp/db_gate.png' });
let gs = await page.evaluate(() => ({ boss: !!window.__game.activeBoss }));
ok('門の外ではボスが湧かない', gs.boss === false);

// --- 3. 主の間に踏み込む → ボス起動 ---
let bs = await page.evaluate(() => {
  const g = window.__game;
  const a = g.dungeon.bossArena;
  g.player.x = a.x; g.player.z = a.z + 6; g.player.y = 0;
  return true;
});
await page.waitForTimeout(2600);
bs = await page.evaluate(() => {
  const g = window.__game; const b = g.activeBoss;
  return { started: !!b, name: b?.name, hp: b?.maxHp, phases: b?.bossDef.phases.length,
    music: g.audio.mode, bar: !g.ui.bossbar.classList.contains('hidden'),
    dungeonBoss: !!b?.dungeonBoss, powerMul: b?.powerMul, arenaR: Math.round(b?.arenaR) };
});
console.log('boss:', JSON.stringify(bs));
ok('主の間でボスが起動', bs.started);
ok('ボスHPバー表示', bs.bar);
ok('ボス曲に切替', bs.music === 'boss');
await page.waitForTimeout(2500);
await page.screenshot({ path: '/tmp/db_boss.png' });

// --- 4. 討伐 ---
let guard = 0;
// 形態の到達は「反復のたびに覗く」と、その隙間で削りきったとき見逃す。
// ページ側で毎フレーム記録しておく
await page.evaluate(() => {
  window.__maxPhase = 0;
  const tick = () => {
    const b = window.__game?.activeBoss;
    if (b) window.__maxPhase = Math.max(window.__maxPhase, b.phaseIndex || 0);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});
let sawPhase1 = false;
while (guard++ < 110) {
  const st = await page.evaluate(() => {
    const g = window.__game; const b = g.activeBoss;
    if (!b) return { done: true };
    g.player.hp = g.player.maxHp;
    g.player.stamina = g.player.maxStamina;
    const d = Math.hypot(b.x - g.player.x, b.z - g.player.z);
    if (d > 3.2) { g.player.x = b.x + 2.4; g.player.z = b.z; }
    g.player.yaw = Math.atan2(b.x - g.player.x, b.z - g.player.z);
    g.player.lockTarget = b;
    if (window.__rush) b.hp = Math.min(b.hp, 90);
    return { hp: Math.round(b.hp), max: b.maxHp, phase: b.phaseIndex, dead: b.dead };
  });
  if (st.phase >= 1) sawPhase1 = true;
  if (guard === 42) await page.evaluate(() => { window.__rush = true; });
  if (st.done || st.dead) { console.log('boss down at iter', guard); break; }
  if (guard % 10 === 0) console.log('  boss hp', st.hp + '/' + st.max, 'phase', st.phase);
  await page.keyboard.press('KeyJ');
  await page.waitForTimeout(350);
}
await page.waitForTimeout(14000);
let dr = await page.evaluate(() => {
  const g = window.__game; const d = g.dungeon;
  return { defeated: d.bossDefeated, activeBoss: !!g.activeBoss, chestVisible: !!d.bossChest && !d.bossChest.opened,
    progress: [...g.dungeonProgress], qBosses: g.quests.count.dungeonBosses,
    depthsQuest: !!g.quests.state.depths, shieldUnlocked: g.unlocked.shields.has('stone_shield') || g.unlocked.weapons.has('scythe') || g.unlocked.talismans.has('ring_delver'),
    music: g.audio.mode, echo: g.player.echo };
});
console.log('after kill:', JSON.stringify(dr));
const maxPhase = await page.evaluate(() => window.__maxPhase);
ok('第2形態へ移行した', sawPhase1 || maxPhase >= 1, `最大形態 ${maxPhase}`);
ok('主を討伐した', dr.defeated);
ok('ボス報酬が入った', dr.shieldUnlocked);
ok('踏破記録が残った', dr.progress.length > 0);
ok('『底へ至る道』が始まった', dr.depthsQuest);
ok('ボス曲から戻った', dr.music !== 'boss' && dr.music !== 'final', dr.music);
await page.screenshot({ path: '/tmp/db_cleared.png' });

// --- 5. 宝箱 ---
await page.evaluate(() => {
  const g = window.__game; const c = g.dungeon.bossChest;
  g.player.x = c.x + 1.1; g.player.z = c.z; g.player.y = 0;
});
await page.waitForTimeout(800);
await page.keyboard.press('KeyE');
await page.waitForTimeout(700);
let cr = await page.evaluate(() => ({ opened: window.__game.dungeon.bossChest.opened }));
ok('ボス部屋の宝箱が開く', cr.opened);

// --- 6. 下り階段 ---
await page.evaluate(() => {
  const g = window.__game; const s = g.dungeon.stairPoint;
  g.player.x = s.x + 0.8; g.player.z = s.z; g.player.y = 0;
});
// SwiftShader では実時間 1.6 秒でも数フレームしか進まない。
// 固定待ちではなく、プロンプトが出るまで待つ（最大 20 秒）
let prompt = null;
for (let i = 0; i < 40; i++) {
  prompt = await page.evaluate(() => window.__game.interact?.label);
  if (/降りる/.test(prompt || '')) break;
  await page.waitForTimeout(500);
}
console.log('prompt:', prompt);
ok('降りるプロンプトが出る', /降りる/.test(prompt || ''));
await page.screenshot({ path: '/tmp/db_stairs.png' });
await page.keyboard.press('KeyE');
for (let i = 0; i < 30; i++) {
  const d = await page.evaluate(() => window.__game.dungeon?.depth);
  if (d === 2) break;
  await page.waitForTimeout(500);
}
let d2 = await page.evaluate(() => {
  const g = window.__game; const d = g.dungeon;
  return { depth: d.depth, rank: d.rank, name: d.name, rooms: d.rooms.length, gw: d.gw,
    defeated: d.bossDefeated, deepest: g.deepestDepth, outside: !!g.outsideReturn,
    enemies: g.enemies.length, boss: !!g.activeBoss };
});
console.log('descended:', JSON.stringify(d2));
ok('第2層に降りた', d2.depth === 2);
ok('新しい主が未討伐', d2.defeated === false);
ok('地上への帰還点を保持', d2.outside);
ok('第2層は広い', d2.gw > 20);
await page.waitForTimeout(2500);
await page.screenshot({ path: '/tmp/db_b2.png' });

// --- 7. 第2層のボスは強い ---
let b2 = await page.evaluate(() => {
  const g = window.__game; const a = g.dungeon.bossArena;
  g.player.x = a.x; g.player.z = a.z + 5; g.player.y = 0;
  return true;
});
await page.waitForTimeout(2600);
b2 = await page.evaluate(() => {
  const g = window.__game; const b = g.activeBoss;
  return { hp: b?.maxHp, powerMul: b?.powerMul?.toFixed(2), scale: b?.scale?.toFixed(2) };
});
console.log('B2 boss:', JSON.stringify(b2), 'vs B1 hp', bs.hp);
ok('第2層のボスは硬い', b2.hp > bs.hp);

// --- 8. 地上へ一気に戻る ---
await page.evaluate(() => { const g = window.__game; g.exitDungeon(); });
await page.waitForTimeout(1200);
let ex = await page.evaluate(() => {
  const g = window.__game;
  return { inDungeon: !!g.dungeon, boss: !!g.activeBoss, bar: !g.ui.bossbar.classList.contains('hidden'),
    y: Math.round(g.player.y), h: Math.round(g.world.height(g.player.x, g.player.z)) };
});
console.log('exit:', JSON.stringify(ex));
ok('地上に戻った', !ex.inDungeon && ex.y === ex.h);
ok('ボスUIが消えた', !ex.bar && !ex.boss);

// --- 9. セーブ/ロード ---
await page.evaluate(() => window.__game.save());
const sv = await page.evaluate(() => JSON.parse(localStorage.getItem('aetheria_save')));
ok('セーブに踏破記録', Array.isArray(sv.delve) && sv.delve.length > 0, JSON.stringify(sv.delve));
ok('セーブに最深記録', sv.deepest >= 2, 'deepest=' + sv.deepest);

report(errs);
await close();
