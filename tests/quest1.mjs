// quest1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 960, height: 470 });

/* ---------- 1. 台詞プールの規模と文脈反応 ---------- */
await page.addScriptTag({ type: 'module', content: `
  import { makeContext, pickLine, lineCount, POOL_NAMES } from '/src/game/dialogue.js';
  import { QUESTS } from '/src/game/quests.js';
  window.__d = { makeContext, pickLine, lineCount, POOL_NAMES };
  window.__QUESTS = QUESTS;
`});
await page.waitForTimeout(500);
const pools = await page.evaluate(() => {
  const d = window.__d;
  const counts = {}; let total = 0;
  for (const p of d.POOL_NAMES) { counts[p] = d.lineCount(p); total += counts[p]; }
  return { counts, total, quests: Object.keys(window.__QUESTS).length };
});
console.log('pools:', JSON.stringify(pools.counts));
ok('台詞総数が十分', pools.total >= 90, 'total=' + pools.total);
ok('クエストが 15 本以上', pools.quests >= 15, 'quests=' + pools.quests);

// 同じ NPC が状況で違うことを言うか
const ctxTest = await page.evaluate(() => {
  const g = window.__game, d = window.__d;
  const npc = g.npcs.find(n => n.role === 'innkeeper') || g.npcs[0];
  const said = new Set();
  const seen = {};
  const scenarios = [
    ['昼・晴れ', () => { g.sky.time = 0.5; g.sky.rainAmount = 0; g.player.hp = g.player.maxHp; }],
    ['夜', () => { g.sky.time = 0.95; g.sky.rainAmount = 0; }],
    ['雨', () => { g.sky.time = 0.5; g.sky.rainAmount = 0.8; }],
    ['手負い', () => { g.sky.rainAmount = 0; g.player.hp = g.player.maxHp * 0.2; }],
    ['大金持ち', () => { g.player.hp = g.player.maxHp; g.player.echo = 20000; }],
  ];
  for (const [name, setup] of scenarios) {
    setup();
    const lines = [];
    for (let s = 0; s < 6; s++) {
      const c = d.makeContext(npc, g);
      lines.push(d.pickLine('smalltalk', c, s));
    }
    seen[name] = lines[0];
    lines.forEach(l => said.add(l));
  }
  g.player.echo = 0; g.player.hp = g.player.maxHp; g.sky.rainAmount = 0; g.sky.time = 0.5;
  return { distinct: said.size, samples: seen };
});
console.log('context samples:');
for (const [k, v] of Object.entries(ctxTest.samples)) console.log(`   ${k.padEnd(8)} ${v}`);
ok('状況で台詞が変わる', ctxTest.distinct >= 8, 'distinct=' + ctxTest.distinct);
ok('雨の日に雨の話をする', /雨|濡/.test(ctxTest.samples['雨']), ctxTest.samples['雨']);
ok('夜に夜の話をする', /夜|時間|寝|遅/.test(ctxTest.samples['夜']), ctxTest.samples['夜']);
ok('手負いを気にかける', /怪我|血|薬|休/.test(ctxTest.samples['手負い']), ctxTest.samples['手負い']);

/* ---------- 2. 会話ツリーが全役割で組めるか ---------- */
const trees = await page.evaluate(() => {
  const g = window.__game;
  const roles = {};
  const walk = (node, depth = 0) => {
    if (!node || depth > 8) return { nodes: 0, leaves: 0 };
    let nodes = 1, leaves = 0;
    for (const o of node.options || []) {
      if (o.next) { const r = walk(o.next, depth + 1); nodes += r.nodes; leaves += r.leaves; }
      else leaves++;
    }
    return { nodes, leaves };
  };
  for (const n of g.npcs) {
    if (roles[n.role]) continue;
    const t = n.dialogue(g);
    roles[n.role] = { text: (t.text || '').slice(0, 22), opts: (t.options || []).length, ...walk(t) };
  }
  return roles;
});
console.log('dialogue trees:');
for (const [r, v] of Object.entries(trees)) console.log(`   ${r.padEnd(11)} opts=${v.opts} nodes=${v.nodes}  「${v.text}…」`);
ok('全役割の会話が組める', Object.keys(trees).length >= 7, Object.keys(trees).join(','));
ok('全役割に選択肢がある', Object.values(trees).every(v => v.opts >= 1));

/* ---------- 3. 連鎖クエスト（鍛冶屋） ---------- */
const chain = await page.evaluate(() => {
  const g = window.__game, q = g.quests, p = g.player;
  const smith = g.npcs.find(n => n.role === 'smith');
  if (!smith) return { skip: true };
  const out = {};
  out.mark0 = smith.questMark(g);
  // 1本目を受けて完了させる
  q.start('smith');
  p.inventory.ore_silver = 2;
  q.update(g);
  out.stage1 = q.stageOf('smith');
  out.markReady = smith.questMark(g);
  q.flags.smithTurnedIn = true;
  q.update(g);
  out.done1 = q.isDone('smith');
  out.gotLongsword = g.unlocked.weapons.has('longsword');
  // 2本目が出るか
  out.mark1 = smith.questMark(g);
  const t = smith.dialogue(g);
  out.hasFollowUp = (t.options || []).some(o => /良い鉄/.test(o.label));
  q.start('smith2');
  p.inventory.shard_ancient = 1;
  q.update(g);
  q.flags.smith2TurnedIn = true;
  q.update(g);
  out.done2 = q.isDone('smith2');
  out.gotGreatsword = g.unlocked.weapons.has('greatsword');
  return out;
});
console.log('smith chain:', JSON.stringify(chain));
ok('1本目が完了し長剣が出る', chain.done1 && chain.gotLongsword);
ok('完了後に続きの依頼が出る', chain.hasFollowUp && chain.mark1 === 'new');
ok('2本目が完了し大剣が出る', chain.done2 && chain.gotGreatsword);
ok('受注前は金の印、報告可能なら白の印', chain.mark0 === 'new' && chain.markReady === 'turnin',
  `${chain.mark0}/${chain.markReady}`);

/* ---------- 4. 選択で報いが変わる（帰らぬ弟） ---------- */
const branch = await page.evaluate(() => {
  const run = (answer) => {
    const g = window.__game, q = g.quests, p = g.player;
    // クエスト状態をこの依頼だけリセット
    delete q.state.missing;
    delete q.flags.missingAnswer;
    q.count.dungeonChests = 2;
    const echo0 = p.echo;
    const tal0 = new Set(g.unlocked.talismans);
    q.start('missing');
    q.update(g);
    const stage = q.stageOf('missing');
    q.flags.missingAnswer = answer;
    q.update(g);
    return { done: q.isDone('missing'), stage,
      echoGain: p.echo - echo0,
      newTalisman: [...g.unlocked.talismans].filter(t => !tal0.has(t)) };
  };
  const truth = run('truth');
  const lie = run('lie');
  return { truth, lie };
});
console.log('missing branch:', JSON.stringify(branch));
ok('真実を告げる分岐が成立', branch.truth.done);
ok('嘘をつく分岐が成立', branch.lie.done);
ok('選択で報酬が変わる', branch.truth.echoGain !== branch.lie.echoGain,
  `truth=${branch.truth.echoGain} lie=${branch.lie.echoGain}`);
ok('正直には護符が付く', branch.truth.newTalisman.length > 0, branch.truth.newTalisman.join(','));

/* ---------- 5. 配達クエスト（村をまたぐ） ---------- */
const cour = await page.evaluate(() => {
  const g = window.__game, q = g.quests;
  const merchants = g.npcs.filter(n => n.role === 'merchant');
  const byPoi = {};
  for (const m of merchants) byPoi[m.poi?.id] = m;
  const ids = Object.keys(byPoi);
  if (ids.length < 2) return { skip: true, villages: ids.length };
  const a = byPoi[ids[0]], b = byPoi[ids[1]];
  delete q.state.courier; delete q.flags.letterDelivered; delete q.flags.courierFrom;
  q.start('courier'); q.flags.courierFrom = a.poi.id;
  const sameVillage = (a.dialogue(g).options || []).some(o => /手紙を渡す/.test(o.label));
  const otherVillage = (b.dialogue(g).options || []).some(o => /手紙を渡す/.test(o.label));
  q.flags.letterDelivered = true;
  q.update(g);
  return { villages: ids.length, sameVillage, otherVillage, done: q.isDone('courier') };
});
console.log('courier:', JSON.stringify(cour));
if (!cour.skip) {
  ok('渡し元の村では渡せない', cour.sameVillage === false);
  ok('別の村なら渡せる', cour.otherVillage === true);
  ok('配達で完了する', cour.done);
} else console.log('  SKIP 集落が1つしかない');

/* ---------- 6. 実際に会話を開いて操作できるか ---------- */
const live = await page.evaluate(() => {
  const g = window.__game;
  const npc = g.npcs.find(n => n.role === 'elder') || g.npcs[0];
  g.player.x = npc.x + 1.4; g.player.z = npc.z; g.player.y = g.world.height(g.player.x, g.player.z);
  return { name: npc.npcName, role: npc.role };
});
await page.waitForTimeout(900);
await page.keyboard.press('KeyE');
await page.waitForTimeout(900);
const dlg = await page.evaluate(() => {
  const el = document.querySelector('#dialogue');
  return { open: !el.classList.contains('hidden'),
    name: el.querySelector('.dname')?.textContent,
    text: el.querySelector('.dtext')?.textContent?.slice(0, 30),
    buttons: [...el.querySelectorAll('.dopts button')].map(b => b.textContent) };
});
console.log('live dialogue:', JSON.stringify(dlg));
ok('会話が開く', dlg.open, dlg.name);
ok('選択肢が出る', dlg.buttons.length >= 2, dlg.buttons.join(' / '));
await page.screenshot({ path: '/tmp/q_dialogue.png' });
// 選択肢を押して次のノードへ
if (dlg.buttons.length) {
  await page.evaluate(() => document.querySelectorAll('#dialogue .dopts button')[0].click());
  await page.waitForTimeout(900);
  const after = await page.evaluate(() => {
    const el = document.querySelector('#dialogue');
    return { open: !el.classList.contains('hidden'),
      text: el.querySelector('.dtext')?.textContent?.slice(0, 26),
      buttons: [...el.querySelectorAll('.dopts button')].map(b => b.textContent) };
  });
  console.log('after choice:', JSON.stringify(after));
  ok('選択肢で会話が進む', after.text && after.text.length > 3, after.text);
}
await page.evaluate(() => window.__game.ui.closeDialogue());

/* ---------- 7. クエスト画面に一覧が出るか ---------- */
await page.evaluate(() => { const g = window.__game; g.quests.start('towers'); g.ui.toggleMenu('quests'); });
await page.waitForTimeout(900);
const qui = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#overlay .panel .row')].map(r => r.textContent);
  return { rows: rows.length, sample: rows.slice(0, 4) };
});
console.log('quest UI rows:', qui.rows);
ok('クエスト画面に一覧が出る', qui.rows >= 3);
await page.screenshot({ path: '/tmp/q_log.png' });
await page.evaluate(() => window.__game.ui.closeOverlay?.() ?? window.__game.ui.toggleMenu('quests'));

report(errs);
await close();
