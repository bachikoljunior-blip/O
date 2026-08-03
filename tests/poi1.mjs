// poi1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 960, height: 470 });

/* ---------- 1. 構造物のモデルがすべて実在するか ---------- */
const models = await page.evaluate(() => {
  const g = window.__game;
  const used = new Set();
  for (const p of g.pois) for (const s of p.structures) used.add(s.model);
  const have = Object.keys(g.renderer.models);
  const missing = [...used].filter(m => !have.includes(m));
  // 頂点が空のモデルが無いか
  const empty = [...used].filter(m => g.renderer.models[m] && g.renderer.models[m].count === 0);
  return { used: used.size, have: have.length, missing, empty };
});
console.log('models:', JSON.stringify(models));
ok('全構造物のモデルが実在する', models.missing.length === 0, models.missing.join(','));
ok('空のモデルが無い', models.empty.length === 0, models.empty.join(','));

/* ---------- 2. 拠点の種類と配置 ---------- */
const pois = await page.evaluate(() => {
  const g = window.__game;
  const byType = {}, byRegion = {};
  let underwater = 0, steep = 0, structs = 0;
  const bad = [];
  for (const p of g.pois) {
    byType[p.type] = (byType[p.type] || 0) + 1;
    (byRegion[p.type] ||= new Set()).add(p.region);
    structs += p.structures.length;
    const h = g.world.height(p.x, p.z), s = g.world.slope(p.x, p.z);
    if (h < 0.5) { underwater++; bad.push(p.name + '(水没)'); }
    if (s > 0.9) { steep++; bad.push(p.name + '(急斜面)'); }
    // 構造物が地面から浮いていないか
    for (const st of p.structures) {
      const gh = g.world.height(st.x, st.z);
      if (Math.abs(st.y - gh) > 0.6) bad.push(`${p.name}:${st.model} 地面から${(st.y-gh).toFixed(1)}m`);
    }
  }
  const out = {};
  for (const [k, v] of Object.entries(byRegion)) out[k] = [...v].join(',');
  return { total: g.pois.length, byType, regions: out, structs, underwater, steep, bad: bad.slice(0, 8) };
});
console.log('POI 総数', pois.total, ' 構造物', pois.structs);
for (const [t, n] of Object.entries(pois.byType)) {
  console.log(`   ${t.padEnd(12)} ${String(n).padStart(2)}  ${pois.regions[t] || ''}`);
}
ok('拠点が増えている', pois.total >= 58, 'total=' + pois.total);
ok('新種別が 6 つとも存在する',
  ['stones','farm','battlefield','mine','wreck','hermit'].every(t => pois.byType[t] > 0),
  ['stones','farm','battlefield','mine','wreck','hermit'].filter(t => !pois.byType[t]).join(',') || 'ok');
ok('拠点が水没していない', pois.underwater === 0, pois.bad.join(' / '));
ok('構造物が地面から浮いていない', !pois.bad.some(b => /浮|地面から/.test(b)), pois.bad.slice(0,3).join(' / '));

/* ---------- 3. 地方らしさ（難破船は岸だけ、農場は穀倉だけ） ---------- */
ok('難破船は岸にしかない', (pois.regions.wreck || '') === 'coast', pois.regions.wreck);
ok('農場は穀倉地帯にしかない',
  (pois.regions.farm || '').split(',').every(r => ['downs','goldreach','coast'].includes(r)), pois.regions.farm);
ok('坑道は岩の地方にしかない',
  (pois.regions.mine || '').split(',').every(r => ['cinder','skyspire','riftvale'].includes(r)), pois.regions.mine);

/* ---------- 4. 隠者の庵には一人だけ住んでいる ---------- */
const herm = await page.evaluate(() => {
  const g = window.__game;
  const h = g.pois.find(p => p.type === 'hermit');
  if (!h) return { none: true };
  const npcs = g.npcs.filter(n => n.poi === h);
  return { name: h.name, npcs: npcs.length, roles: npcs.map(n => n.role),
    dist: npcs.map(n => +Math.hypot(n.x - h.x, n.z - h.z).toFixed(1)) };
});
console.log('hermit:', JSON.stringify(herm));
ok('隠者の庵に住人が一人いる', herm.npcs === 1, `${herm.npcs}人 ${herm.roles}`);

/* ---------- 5. 廃坑に潜れる ---------- */
const mine = await page.evaluate(() => {
  const g = window.__game;
  const m = g.pois.find(p => p.type === 'mine');
  if (!m) return { none: true };
  g.player.x = m.x + 2; g.player.z = m.z; g.player.y = g.world.height(g.player.x, g.player.z);
  return { name: m.name, theme: m.dungeonTheme };
});
await page.waitForTimeout(1200);
const minePrompt = await page.evaluate(() => window.__game.interact?.label);
console.log('mine:', JSON.stringify(mine), 'prompt:', minePrompt);
ok('廃坑のダンジョンテーマが坑道', mine.theme === 'mine', mine.theme);
ok('廃坑に潜れる', /潜る/.test(minePrompt || ''), minePrompt);
const entered = await page.evaluate(() => {
  const g = window.__game;
  g.doInteract(g.interact);
  return { inDungeon: !!g.dungeon, name: g.dungeon?.name, theme: g.dungeon?.themeKey };
});
console.log('entered:', JSON.stringify(entered));
ok('潜ると坑道テーマのダンジョンになる', entered.inDungeon && entered.theme === 'mine', entered.name);
await page.evaluate(() => window.__game.exitDungeon());
await page.waitForTimeout(800);

/* ---------- 6. 各拠点を撮る ---------- */
const shots = ['stones', 'farm', 'battlefield', 'wreck', 'hermit', 'mine', 'village'];
for (const t of shots) {
  const info = await page.evaluate((t) => {
    const g = window.__game;
    const p = g.pois.find(q => q.type === t);
    if (!p) return null;
    const d = t === 'village' ? 34 : t === 'farm' ? 30 : 20;
    // 拠点を見下ろせる位置に立つ
    let best = null;
    for (let k = 0; k < 24; k++) {
      const a = k / 24 * Math.PI * 2;
      const px = p.x + Math.sin(a) * d, pz = p.z + Math.cos(a) * d;
      const h = g.world.height(px, pz);
      if (g.world.slope(px, pz) > 0.7) continue;
      if (!best || h > best.h) best = { x: px, z: pz, h };
    }
    const s = best || { x: p.x + d, z: p.z, h: g.world.height(p.x + d, p.z) };
    g.player.x = s.x; g.player.z = s.z; g.player.y = s.h;
    g.player.yaw = Math.atan2(p.x - s.x, p.z - s.z);
    g.camera.yaw = g.player.yaw; g.camera.pitch = 0.22; g.camera.distance = 7.5; g.camera.height = 2.4;
    g.mount.x = s.x + 900; g.mount.z = s.z + 900;
    g.enemies.length = 0;
    g.sky.time = 0.42; g.sky.rainAmount = 0; g.sky.snowAmount = 0;
    g.terrain.chunks.forEach(c => c.dispose()); g.terrain.chunks.clear();
    return { name: p.name, region: p.region, structs: p.structures.length };
  }, t);
  if (!info) { console.log(`  ${t}: 該当なし`); continue; }
  await page.waitForTimeout(11000);
  await page.screenshot({ path: `/tmp/p_${t}.png` });
  const fps = await page.evaluate(() => Math.round(window.__game.time.fps));
  console.log(`  ${t.padEnd(12)} ${info.name}（${info.region}） 構造物${info.structs} fps=${fps}`);
}

report(errs);
await close();
