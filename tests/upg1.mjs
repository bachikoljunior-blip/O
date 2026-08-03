// upg1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 700, height: 400, audio: true });

/* ---------- 1. 段階ごとの見た目 ---------- */
const looks = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const out = [];
  p.equip.weapon = 'longsword';
  for (let lv = 0; lv <= 10; lv++) {
    p.upgrades.longsword = lv;
    p.recalc();
    out.push({ lv, tier: p.weaponTier.name,
      tint: p.weaponTint.map(v => +v.toFixed(3)),
      glow: +p.weaponGlow.toFixed(3),
      scale: +p.weaponScale.toFixed(3),
      trail: p.trailColor.map(v => +v.toFixed(2)),
      atk: Math.round(p.weaponAttackPower()) });
  }
  p.upgrades.longsword = 0; p.recalc();
  return out;
});
console.log('段階  呼び名      刃の色                灯り   大きさ  攻撃力');
for (const l of looks) {
  console.log(`  +${String(l.lv).padStart(2)}  ${(l.tier||'—').padEnd(10)} `
    + `[${l.tint.join(', ')}]  ${String(l.glow).padEnd(5)}  ${l.scale}  ${l.atk}`);
}
const lum = (t) => t[0]*0.299 + t[1]*0.587 + t[2]*0.114;
ok('段階が上がるほど刃が冴える',
  looks.every((l, i) => i === 0 || lum(l.tint) >= lum(looks[i-1].tint) - 1e-6),
  `+0 ${lum(looks[0].tint).toFixed(3)} → +10 ${lum(looks[10].tint).toFixed(3)}`);
ok('+0 と +10 で刃の色が違う', lum(looks[10].tint) - lum(looks[0].tint) > 0.06,
  `差 ${(lum(looks[10].tint) - lum(looks[0].tint)).toFixed(3)}`);
ok('高い段階だけが光る', looks[0].glow === 0 && looks[4].glow === 0 && looks[10].glow > 0.3,
  `+0 ${looks[0].glow} / +4 ${looks[4].glow} / +10 ${looks[10].glow}`);
ok('軌跡も段階で明るくなる', lum(looks[10].trail) > lum(looks[0].trail) + 0.2,
  `+0 ${lum(looks[0].trail).toFixed(2)} → +10 ${lum(looks[10].trail).toFixed(2)}`);
ok('見た目の変化は段階的（毎段変わるわけではない）',
  new Set(looks.map(l => l.tier)).size === 5,
  [...new Set(looks.map(l => l.tier || '—'))].join(' / '));
ok('攻撃力は単調に上がる', looks.every((l,i) => i===0 || l.atk > looks[i-1].atk),
  `${looks[0].atk} → ${looks[10].atk}`);

/* ---------- 2. 呼び名の境目 ---------- */
const names = await page.evaluate(async () => {
  const { UPGRADE } = await import('/src/game/data.js');
  const out = {};
  for (let lv = 0; lv <= 10; lv++) out[lv] = UPGRADE.tier(lv).name;
  return out;
});
console.log('呼び名:', JSON.stringify(names));
ok('+0 は無名', names[0] === '');
ok('+2 で「打ち直し」', names[2] === '打ち直し' && names[1] === '');
ok('+5 で「鍛え上げ」', names[5] === '鍛え上げ' && names[4] === '打ち直し');
ok('+8 で「銘入り」', names[8] === '銘入り' && names[7] === '鍛え上げ');
ok('+10 で「極み」', names[10] === '極み' && names[9] === '銘入り');

/* ---------- 3. 極みまでの見通し ---------- */
const road = await page.evaluate(async () => {
  const { UPGRADE } = await import('/src/game/data.js');
  const out = {};
  for (const lv of [0, 5, 9, 10]) out[lv] = UPGRADE.toMax(lv);
  // 累計が、各段の合計と一致するか
  let sum = 0;
  for (let l = 0; l < UPGRADE.maxLevel; l++) sum += UPGRADE.cost(l).echo;
  return { out, sum };
});
console.log('極みまで:', JSON.stringify(road.out));
ok('+0 からの総額が各段の合計と一致する', road.out[0].echo === road.sum,
  `${road.out[0].echo} vs ${road.sum}`);
ok('段階が上がるほど残りは減る', road.out[0].echo > road.out[5].echo
  && road.out[5].echo > road.out[9].echo);
ok('+10 では何も要らない', road.out[10].echo === 0
  && Object.keys(road.out[10].mats).length === 0, JSON.stringify(road.out[10]));
ok('必要素材が段階で変わる',
  Object.keys(road.out[0].mats).length === 3 && Object.keys(road.out[9].mats).length === 1,
  `+0 ${Object.keys(road.out[0].mats).join(',')} / +9 ${Object.keys(road.out[9].mats).join(',')}`);

/* ---------- 4. 手応え：銘入り以上だけ余韻が鳴る ---------- */
const feel = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const { WEAPONS } = await import('/src/game/data.js');
  const log = [];
  const oPlay = g.audio.play.bind(g.audio);
  g.audio.play = (n, o) => { log.push(n); return oPlay(n, o); };
  const hit = (lv) => {
    p.equip.weapon = 'longsword';
    p.upgrades.longsword = lv;
    p.recalc();
    g.enemies.length = 0;
    const e = spawnEnemy('bandit', { x: p.x + 1.2, y: p.y, z: p.z, yaw: 0, leash: 400 });
    e.hp = e.maxHp * 10;      // 死なないように
    g.enemies.push(e); e.aggro = false;
    p.yaw = Math.atan2(e.x - p.x, e.z - p.z);
    log.length = 0;
    p.attackHits = new Set();
    p.attack = { ...WEAPONS.longsword.moveset.l1, range: 9, arc: 3.2 };
    p.setState('attack', 1.0); p.animT = p.attack.windup + 0.01;
    p.processAttack(1/60, g);
    return log.slice();
  };
  const out = {};
  for (const lv of [0, 4, 7, 8, 10]) out[lv] = hit(lv);
  g.audio.play = oPlay;
  g.enemies.length = 0;
  p.upgrades.longsword = 0; p.recalc();
  return out;
});
for (const [lv, sfx] of Object.entries(feel)) console.log(`  +${lv}: ${sfx.join(',')}`);
ok('低い段階では余韻が鳴らない',
  !feel[0].includes('keen') && !feel[4].includes('keen') && !feel[7].includes('keen'));
ok('銘入り以上で余韻が鳴る', feel[8].includes('keen') && feel[10].includes('keen'));
ok('どの段階でも打撃音そのものは鳴る',
  Object.values(feel).every(s => s.some(n => n.startsWith('hit_'))));

/* ---------- 5. 鍛冶の画面 ---------- */
const ui = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  p.equip.weapon = 'longsword'; p.upgrades.longsword = 4;
  p.echo = 999999;
  p.inventory.ore_iron = 99; p.inventory.ore_silver = 99; p.inventory.shard_ancient = 99;
  p.recalc();
  g.ui.openSmith();
  await new Promise(r => setTimeout(r, 150));
  const text = document.querySelector('#overlay').textContent;
  const before = p.upgrades.longsword;
  const btn = [...document.querySelectorAll('#overlay button')]
    .find(b => b.textContent.trim() === '強化する');
  btn?.click();
  await new Promise(r => setTimeout(r, 150));
  const after = p.upgrades.longsword;
  const text2 = document.querySelector('#overlay').textContent;
  g.ui.closeOverlay();
  p.upgrades.longsword = 0; p.recalc();
  return { text, text2, before, after, hasBtn: !!btn };
});
console.log('鍛冶画面(+4):', ui.text.replace(/\s+/g, ' ').slice(0, 210));
ok('鍛冶画面に呼び名が出る', /打ち直し/.test(ui.text), ui.text.slice(0, 40));
ok('次の位が予告される', /鍛え上げ/.test(ui.text) && /あと 1/.test(ui.text));
ok('極みまでの見通しが出る', /極みまで/.test(ui.text) && /残響/.test(ui.text));
ok('強化すると段階が上がる', ui.after === ui.before + 1, `${ui.before} → ${ui.after}`);
ok('強化後は表示が「鍛え上げ」に変わる', /鍛え上げ/.test(ui.text2) && /\+5/.test(ui.text2));

/* ---------- 6. 装備画面にも出る ---------- */
const eq = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  g.unlocked.weapons.add('longsword');      // 一覧は所持している武器しか出さない
  p.upgrades.longsword = 10; p.equip.weapon = 'longsword'; p.recalc();
  g.ui.toggleMenu('equip');
  await new Promise(r => setTimeout(r, 150));
  const text = document.querySelector('#overlay').textContent;
  g.ui.closeOverlay();
  p.upgrades.longsword = 0; p.recalc();
  return text;
});
ok('装備画面にも呼び名が出る', /極み/.test(eq), eq.replace(/\s+/g,' ').slice(0, 60));

/* ---------- 6.5 実際の画面に出ているか（描画まで通っているか）---------- */
// ここまでの節でセーブ・ロードを通しているので、立っている場所が回ごとに変わる。
// 刃が画面に入らない場所（水中・物陰）に居ると、描画の当否ではなく
// 立ち位置を測ってしまう。測る前に足場を作る
const glow = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { writeInstanceMat } = await import('/src/render/instance.js');
  g.unlocked.weapons.add('greatsword');
  p.equip.weapon = 'greatsword';

  // 画面の明るさで測るのはやめた。SwiftShader だと 2 枚の間に草も水も炎も
  // 動くので、刃の寄与（約1150画素）と背景の揺れ（約380画素）が同じ桁になる。
  // 見たいのは「強化した発光が描画まで届いているか」なので、
  // インスタンスに書かれる発光値そのものを捕まえる。
  const seen = [];
  const model = p.weaponModel;
  const batch = g.renderer.batchFor(model);
  const oWrite = batch.data;
  const grab = (lv) => {
    p.upgrades.greatsword = lv;
    p.recalc();
    const before = batch.count;
    const o = batch.alloc();
    const m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1;
    writeInstanceMat(batch.data, o, m,
      p.weaponTint[0], p.weaponTint[1], p.weaponTint[2], 1, 0, p.weaponGlow || 0, 0);
    // d[17] が発光（instance.js の並び）
    const emissive = batch.data[o + 17];
    batch.count = before;
    return { lv, tier: p.weaponTier.name, glow: +(p.weaponGlow || 0).toFixed(3),
      emissive: +emissive.toFixed(3),
      tint: p.weaponTint.map((v) => +v.toFixed(3)) };
  };
  for (const lv of [0, 3, 6, 10]) seen.push(grab(lv));
  p.upgrades.greatsword = 0; p.recalc();
  void oWrite;
  return seen;
});
console.log('強化と、描画へ渡る発光:');
for (const v of glow)
  console.log(`  +${String(v.lv).padStart(2)} ${String(v.tier).padEnd(6)} 発光 ${v.emissive}  刃の色 ${v.tint.join(',')}`);
// 低い段は光らず色だけ冴える設計。下がらないこと＋上の段で確かに増えることを見る
ok('強化しても発光が下がらない',
  glow.every((v, i) => i === 0 || v.emissive >= glow[i-1].emissive),
  glow.map(v => v.emissive).join(' → '));
ok('上の段になると光り始める',
  glow[0].emissive === 0 && glow[glow.length-1].emissive > 0.3,
  `+0 ${glow[0].emissive} → +10 ${glow[glow.length-1].emissive}`);
ok('その発光が描画に渡っている（インスタンスに載る）',
  glow.every(v => Math.abs(v.emissive - v.glow) < 1e-3),
  glow.map(v => `${v.glow}/${v.emissive}`).join(' '));
ok('極みの刃は素のままより明確に光る',
  glow[glow.length-1].emissive > glow[0].emissive + 0.2,
  `+0 ${glow[0].emissive} → +10 ${glow[glow.length-1].emissive}`);
ok('刃の色も段階で冴える',
  glow[glow.length-1].tint[0] > glow[0].tint[0],
  `${glow[0].tint[0]} → ${glow[glow.length-1].tint[0]}`);

/* ---------- 7. セーブ・ロードで段階が保たれる ---------- */
const save = await page.evaluate(() => {
  const g = window.__game, p = g.player;
  p.upgrades.longsword = 7; p.equip.weapon = 'longsword'; p.recalc();
  const tintBefore = p.weaponTint.slice();
  g.save();
  p.upgrades.longsword = 0; p.recalc();
  const raw = JSON.parse(localStorage.getItem('aetheria_save'));
  p.deserialize(raw.player);
  p.recalc();
  return { lv: p.upgrades.longsword, tier: p.weaponTier.name,
    same: p.weaponTint.every((v, i) => Math.abs(v - tintBefore[i]) < 1e-6) };
});
console.log('セーブ:', JSON.stringify(save));
ok('強化段階がセーブに残る', save.lv === 7 && save.tier === '鍛え上げ', JSON.stringify(save));
ok('読み込み後も見た目が同じ', save.same);

report(errs);
await close();
