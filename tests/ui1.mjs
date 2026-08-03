// ui1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 960, height: 470 });

/* ---------- 1. 方位帯 ---------- */
const comp = await page.evaluate(() => {
  const g = window.__game, ui = g.ui;
  const c = document.querySelector('#compass');
  const nonEmpty = () => {
    const ctx = c.getContext('2d');
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 8) n++;
    return n;
  };
  g.camera.yaw = 0; ui.drawCompass(g);
  const north = nonEmpty();
  // 中央に「北」が来ているか（上向きのときの中央付近の描画量）
  const centerAt = (yaw) => {
    g.camera.yaw = yaw; ui.drawCompass(g);
    const ctx = c.getContext('2d');
    // 目盛りは alpha 最大 0.44、方角の文字は 0.97。明るい画素だけ数えて文字を分離する
    const d = ctx.getImageData(c.width/2 - 12, 18, 24, 24).data;
    let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 200) n++;
    return n;
  };
  const atN = centerAt(0), atNE = centerAt(Math.PI/4), atE = centerAt(Math.PI/2);
  g.camera.yaw = 0;
  return { visible: !c.classList.contains('hidden'), pixels: north, atN, atNE, atE,
    w: c.width, h: c.height };
});
console.log('compass:', JSON.stringify(comp));
ok('方位帯が表示される', comp.visible);
ok('方位帯が描画されている', comp.pixels > 400, 'px=' + comp.pixels);
ok('方角の文字が中央に来る', comp.atN > comp.atNE && comp.atE > comp.atNE,
  `北=${comp.atN} 北東=${comp.atNE} 東=${comp.atE}`);

// 目印（クエスト目的地）が出るか
const marks = await page.evaluate(() => {
  const g = window.__game, ui = g.ui;
  g.quests.flags.talkedElder = false;
  g.quests.start('main');
  const hint = ui.questHintPOI(g);
  if (!hint) return { hint: null };
  // 目的地の方角を向く
  g.camera.yaw = Math.atan2(hint.x - g.player.x, hint.z - g.player.z);
  ui.drawCompass(g);
  const c = document.querySelector('#compass');
  const d = c.getContext('2d').getImageData(c.width/2 - 10, 4, 20, 14).data;
  // 金色（R高 G中 B低）の画素を数える
  let gold = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i+3] > 100 && d[i] > 200 && d[i+1] > 160 && d[i+2] < 160) gold++;
  }
  return { hint: hint.name, gold };
});
console.log('quest mark:', JSON.stringify(marks));
ok('クエスト目的地の印が出る', marks.hint && marks.gold > 6, `${marks.hint} gold=${marks.gold}`);

/* ---------- 2. クエスト追跡に距離が出る ---------- */
const track = await page.evaluate(() => {
  const g = window.__game;
  g.ui.updateQuestTrack(g);
  const t = document.querySelector('.questtrack');
  return { hidden: t.classList.contains('hidden'),
    name: t.querySelector('.qname').textContent,
    stage: t.querySelector('.qstage').textContent };
});
console.log('quest track:', JSON.stringify(track));
ok('追跡に距離が出る', /m$|km$/.test(track.stage.trim()), track.stage);

/* ---------- 3. 固定した相手の体力バー ---------- */
await page.addScriptTag({ type: 'module', content: `
  import { spawnEnemy } from '/src/game/enemies.js';
  window.__spawn = (k, x, z) => { const g = window.__game;
    const e = spawnEnemy(k, { x, y: g.groundHeight(x,z), z, yaw: 0, leash: 400 });
    if (e) { e.aggro = true; g.enemies.push(e); } return e; };
`});
await page.waitForTimeout(400);
const tb = await page.evaluate(() => {
  const g = window.__game;
  g.enemies.length = 0;
  const e = window.__spawn('halberdier', g.player.x + 3, g.player.z);
  g.player.lockTarget = e;
  g.ui.updateTargetBar(0.016, g);
  const bar = document.querySelector('#targetbar');
  const before = { hidden: bar.classList.contains('hidden'),
    name: bar.querySelector('.tname').textContent,
    w: bar.querySelector('.tbar b').style.width };
  e.hp = e.maxHp * 0.4;
  g.ui.updateTargetBar(0.016, g);
  const after = { w: bar.querySelector('.tbar b').style.width,
    lag: bar.querySelector('.tbar i').style.width };
  e.dead = true;
  g.ui.updateTargetBar(0.016, g);
  const gone = bar.classList.contains('hidden');
  g.enemies.length = 0; g.player.lockTarget = null;
  return { before, after, gone };
});
console.log('target bar:', JSON.stringify(tb));
ok('固定した相手のバーが出る', !tb.before.hidden && tb.before.name.length > 0, tb.before.name);
ok('体力が減るとバーも減る', parseFloat(tb.after.w) < 45, tb.after.w);
ok('残像バーが遅れて追う', parseFloat(tb.after.lag) > parseFloat(tb.after.w), tb.after.lag);
ok('倒すとバーが消える', tb.gone);

/* ---------- 4. 装備比較 ---------- */
const eq = await page.evaluate(() => {
  const g = window.__game;
  g.unlockWeapon('greatsword'); g.unlockWeapon('katana');
  g.unlockArmor('knight_plate'); g.unlockShield('tower_shield');
  g.player.equip.weapon = 'broken_sword'; g.player.recalc();
  g.ui.toggleMenu('equip');
  return true;
});
await page.waitForTimeout(800);
const eqDom = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#overlay .panel .row')];
  const find = (t) => rows.find(r => r.textContent.includes(t));
  const gs = find('灰鉄の大剣');
  const kp = find('騎士の板金鎧');
  const ts = find('大盾・城壁');
  const load = rows.find(r => r.textContent.includes('積載'));
  const meter = document.querySelector('.loadmeter i');
  return {
    upArrows: document.querySelectorAll('#overlay .row .v em.up').length,
    downArrows: document.querySelectorAll('#overlay .row .v em.down').length,
    warns: document.querySelectorAll('#overlay .row .warn').length,
    greatsword: gs?.textContent.replace(/\s+/g, ' ').slice(0, 70),
    plate: kp?.textContent.replace(/\s+/g, ' ').slice(0, 50),
    tower: ts?.textContent.replace(/\s+/g, ' ').slice(0, 46),
    load: load?.textContent.replace(/\s+/g, ' '),
    meterClass: meter?.className, meterW: meter?.style.width,
  };
});
console.log('equip screen:');
for (const [k, v] of Object.entries(eqDom)) console.log(`   ${k}: ${v}`);
ok('強くなる装備に▲が出る', eqDom.upArrows > 0, 'up=' + eqDom.upArrows);
ok('能力不足の警告が出る', eqDom.warns > 0, 'warns=' + eqDom.warns);
ok('積載と転がりが出る', /積載/.test(eqDom.load || ''), eqDom.load);
ok('積載メーターが色分けされる', ['fast','normal','heavy','over'].includes(eqDom.meterClass), eqDom.meterClass);
await page.screenshot({ path: '/tmp/u_equip.png' });

// 実際に持ち替えると数値が変わるか
const swap = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#overlay .panel .row')];
  const before = rows.find(r => r.textContent.includes('攻撃力'))?.textContent;
  const gs = rows.find(r => r.textContent.includes('灰鉄の大剣'));
  gs?.click();
  return { before };
});
await page.waitForTimeout(600);
const after = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  const rows = [...document.querySelectorAll('#overlay .panel .row')];
  const lightRoll = p.rollType;
  // 重装備一式に替えて転がりが鈍るか
  g.unlockArmor('knight_helm');
  p.equip.body = 'knight_plate'; p.equip.head = 'knight_helm';
  p.equip.offhand = 'tower_shield'; p.recalc();
  g.ui.renderOverlay();
  const rows2 = [...document.querySelectorAll('#overlay .panel .row')];
  return { after: rows.find(r => r.textContent.includes('攻撃力'))?.textContent,
    load: rows2.find(r => r.textContent.includes('積載'))?.textContent.replace(/\s+/g,' '),
    weapon: p.equip.weapon, lightRoll, heavyRoll: p.rollType,
    meter: document.querySelector('.loadmeter i')?.className };
});
console.log('after swap:', JSON.stringify(after), 'was', swap.before);
ok('持ち替えで攻撃力が更新される', swap.before !== after.after, `${swap.before} → ${after.after}`);
ok('重装備にすると転がりが鈍る', after.lightRoll === 'fast' && after.heavyRoll !== 'fast',
  `${after.lightRoll} → ${after.heavyRoll} (${after.load})`);
ok('積載メーターの色も変わる', after.meter === after.heavyRoll, after.meter);

/* ---------- 5. 設定：左利き・ボタン大きさ・方位帯オフ ---------- */
await page.evaluate(() => { const ui = window.__game.ui; ui.closeOverlay(); ui.openMenu('settings'); });
await page.waitForTimeout(900);
const setRows = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#overlay .panel .row')];
  return rows.map(r => r.textContent.replace(/\s+/g, ' ').slice(0, 30));
});
console.log('settings rows:', setRows.length);
ok('左利き設定がある', setRows.some(r => /左利き/.test(r)), '');
ok('ボタンの大きさ設定がある', setRows.some(r => /ボタンの大きさ/.test(r)));
ok('方位帯の切り替えがある', setRows.some(r => /方位帯/.test(r)));

const lefty = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#overlay .panel .row')];
  const r = rows.find(x => /左利き/.test(x.textContent));
  r.click();
  return { cls: document.body.className, val: window.__game.ui.settings.lefty };
});
await page.waitForTimeout(600);
ok('左利き配置が適用される', lefty.cls.includes('lefty') && lefty.val === true, lefty.cls);
const geom = await page.evaluate(() => {
  const cl = document.querySelector('.cluster').getBoundingClientRect();
  const vt = document.querySelector('.vitals').getBoundingClientRect();
  return { clusterLeft: Math.round(cl.left), vitalsLeft: Math.round(vt.left), w: innerWidth };
});
ok('操作ボタンが左へ、体力表示が右へ移る',
  geom.clusterLeft < geom.w / 2 && geom.vitalsLeft > geom.w / 2, JSON.stringify(geom));
await page.evaluate(() => window.__game.ui.closeOverlay());
await page.waitForTimeout(500);
await page.screenshot({ path: '/tmp/u_lefty.png' });
// 元に戻す
await page.evaluate(() => {
  const ui = window.__game.ui;
  ui.settings.lefty = false; ui.settings.buttonSize = 'big'; ui.applySettings();
});
await page.waitForTimeout(600);
const bigBtn = await page.evaluate(() => {
  const b = document.querySelector('.tb.main.big').getBoundingClientRect();
  return { w: Math.round(b.width), cls: document.body.className };
});
ok('ボタン「大」が効く', bigBtn.w >= 84, 'w=' + bigBtn.w);
await page.screenshot({ path: '/tmp/u_big.png' });
await page.evaluate(() => {
  const ui = window.__game.ui; ui.settings.buttonSize = 'normal'; ui.applySettings();
});

/* ---------- 6. 瀕死の脈動 ---------- */
const low = await page.evaluate(() => {
  const g = window.__game;
  g.player.hp = g.player.maxHp * 0.2;
  g.ui.update(0.016, g);
  const a = document.querySelector('.vitals').classList.contains('low');
  g.player.hp = g.player.maxHp;
  g.ui.update(0.016, g);
  const b = document.querySelector('.vitals').classList.contains('low');
  return { atLow: a, atFull: b };
});
ok('瀕死のとき体力バーが脈打つ', low.atLow && !low.atFull, JSON.stringify(low));

/* ---------- 7. 設定が保存される ---------- */
const saved = await page.evaluate(() => {
  const ui = window.__game.ui;
  ui.settings.lefty = true; ui.settings.compass = false; ui.saveSettings();
  const raw = JSON.parse(localStorage.getItem('aetheria_settings'));
  ui.settings.lefty = false; ui.settings.compass = true; ui.applySettings(); ui.saveSettings();
  return raw;
});
ok('新しい設定が保存される', saved.lefty === true && saved.compass === false, JSON.stringify(saved));

await page.waitForTimeout(1500);
await page.screenshot({ path: '/tmp/u_hud.png' });
report(errs);
await close();
