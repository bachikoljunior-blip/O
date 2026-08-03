// dun3
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/* ---------- 1. 取り分の釣り合い ---------- */
const econ = await page.evaluate(async () => {
  const D = await import('/src/game/data.js');
  const { Dungeon } = await import('/src/world/dungeon.js');
  const g = window.__game;
  const T = D.CHEST_TABLE;
  const src = g.pois.filter(p => p.dungeonTheme);
  const dives = [];
  const pairs = [];        // 同じ階の「脇の箱」と「あるじの宝」
  for (const poi of src) {
    let echo = 0; const items = {};
    for (let depth = 1; depth <= 3; depth++) {
      const d = new Dungeon(poi, (poi.id * 977 + depth * 31) | 0, depth, 1 + ((poi.danger / 3) | 0));
      const side = d.chests.filter(c => !c.boss).map(c => c.tier);
      const boss = d.chests.find(c => c.boss);
      if (boss) pairs.push({ rank: d.rank, boss: boss.tier, sideMax: side.length ? Math.max(...side) : 0,
        rooms: d.rooms.length, side: side.length });
      for (const c of d.chests) {
        const t = Math.max(1, Math.min(5, c.tier));
        echo += T[t].echo;
        for (const [i, n] of T[t].items) items[i] = (items[i] || 0) + n;
      }
      for (const s of d.spawns) echo += (D.ENEMIES[s.kind]?.echo || 0) * (s.count || 1);
      const b = D.BOSSES?.[d.bossId]; if (b) echo += b.echo || 0;
    }
    dives.push({ echo, shard: items.shard_ancient || 0, silver: items.ore_silver || 0 });
  }
  let surf = 0;
  for (const p of g.pois) {
    if (p.chest) surf += T[Math.max(1, Math.min(5, p.chest.tier))].echo;
    surf += D.discoverReward(p);
  }
  let lv30 = 0; for (let L = 1; L < 30; L++) lv30 += D.levelCost(L);
  return { dives, pairs, surf, lv30, need: D.UPGRADE.toMax(0).mats };
});
const med = (l) => { const s=[...l].sort((a,b)=>a-b); return s[Math.floor(s.length/2)]; };
const mEcho = med(econ.dives.map(d => d.echo));
const mShard = med(econ.dives.map(d => d.shard));
const mSilver = med(econ.dives.map(d => d.silver));
console.log(`地下ひとつ（3層）中央値 ${mEcho} 残響／地上ぜんぶ ${econ.surf}（比 ${(mEcho/econ.surf).toFixed(2)}）`);
console.log(`  古き欠片 ${mShard}（極みに要る ${econ.need.shard_ancient}）／銀 ${mSilver}（要 ${econ.need.ore_silver}）`);
ok('地下ひとつが地上ぜんぶを超えない', mEcho < econ.surf * 0.6, `比 ${(mEcho/econ.surf).toFixed(2)} < 0.60`);
ok('それでも潜る価値はある', mEcho > econ.surf * 0.25, `比 ${(mEcho/econ.surf).toFixed(2)} > 0.25`);
ok('古き欠片が余らない（武器ひとつぶん）',
  mShard >= econ.need.shard_ancient && mShard <= econ.need.shard_ancient * 2,
  `${mShard}（要 ${econ.need.shard_ancient}）`);
ok('銀だけでは極みに届かない', mSilver < econ.need.ore_silver, `${mSilver} < ${econ.need.ore_silver}`);

/* ---------- 2. 主の間がその階でいちばんになる ---------- */
const worse = econ.pairs.filter(p => p.boss <= p.sideMax);
console.log(`階 ${econ.pairs.length} 層を検分：あるじの宝 ≦ 脇の箱 だった層 ${worse.length}`);
const byRank = {};
for (const p of econ.pairs) byRank[p.rank] = byRank[p.rank] || { boss: p.boss, side: p.sideMax };
console.log('  格ごと（あるじ／脇の最大）:', Object.entries(byRank)
  .sort((a,b)=>a[0]-b[0]).map(([r,v])=>`格${r}:${v.boss}/${v.side}`).join(' '));
ok('あるじの宝が常にその階の最上', worse.length === 0, `${worse.length}層`);
ok('脇の箱は最上等級にならない',
  econ.pairs.every(p => p.sideMax <= 4), `最大 ${Math.max(...econ.pairs.map(p=>p.sideMax))}`);
ok('深く潜るほどあるじの宝が良くなる',
  Object.entries(byRank).sort((a,b)=>a[0]-b[0]).every((e,i,a)=>i===0||e[1].boss>=a[i-1][1].boss),
  Object.entries(byRank).sort((a,b)=>a[0]-b[0]).map(([r,v])=>v.boss).join('→'));
const density = econ.pairs.map(p => +(p.side / Math.max(1, p.rooms - 2)).toFixed(2));
console.log(`  脇の箱の密度（部屋あたり）: 中央 ${med(density)}`);
ok('部屋ごとに宝箱があるわけではない', med(density) < 0.5, `${med(density)}`);

/* ---------- 3. 実際に潜って開けられる ---------- */
const dive = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const sfx = []; const toasts = [];
  const oPlay = g.audio.play.bind(g.audio); g.audio.play = (n, o) => { sfx.push(n); return oPlay(n, o); };
  const oToast = g.ui.toast.bind(g.ui); g.ui.toast = (t, c) => { toasts.push(t); return oToast(t, c); };
  const poi = g.pois.find(q => q.dungeonTheme);
  g.enterDungeon(poi);
  const d = g.dungeon;
  d.bossDefeated = true;        // あるじの宝は討伐後にしか開かない
  const e0 = p.echo;
  const opened = [];
  for (const c of d.chests) {
    p.x = c.x; p.z = c.z; p.y = 0;
    g.updateInteract();
    const it = g.interact;
    if (it && it.type === 'dungeon_chest') { const before = p.echo; g.doInteract(it);
      opened.push({ boss: !!c.boss, gained: p.echo - before }); }
  }
  const inside = !!g.dungeon;
  g.exitDungeon();
  g.audio.play = oPlay; g.ui.toast = oToast;
  return { name: d.name, chests: d.chests.length, opened: opened.length,
    bossGain: opened.find(o => o.boss)?.gained || 0,
    sideMax: Math.max(0, ...opened.filter(o => !o.boss).map(o => o.gained)),
    echo: p.echo - e0, inside, out: !g.dungeon,
    bossToast: toasts.some(t => t.startsWith('あるじの宝')),
    sideToast: toasts.some(t => t.startsWith('宝箱：')),
    levelup: sfx.filter(s => s === 'levelup').length, chestSfx: sfx.filter(s => s === 'chest').length };
});
console.log(`潜行: ${dive.name}　宝箱 ${dive.chests} 個中 ${dive.opened} 個を開け ${dive.echo} 残響`);
console.log(`  あるじの宝 ${dive.bossGain} / 脇の最大 ${dive.sideMax}`);
ok('地下に入って出られる', dive.inside && dive.out);
ok('地下の宝箱が開く', dive.opened > 0, `${dive.opened}個`);
ok('あるじの宝が脇の箱より多い', dive.bossGain > dive.sideMax, `${dive.bossGain} > ${dive.sideMax}`);
ok('あるじの宝は呼び方が違う', dive.bossToast && dive.sideToast,
  `あるじ=${dive.bossToast} 脇=${dive.sideToast}`);
ok('あるじの宝は音も違う', dive.levelup > 0 && dive.chestSfx > 0,
  `levelup ${dive.levelup} / chest ${dive.chestSfx}`);

/* ---------- 4. 地下でしか手に入らないものが残っている ---------- */
const uniq = await page.evaluate(async () => {
  const D = await import('/src/game/data.js');
  const { Dungeon } = await import('/src/world/dungeon.js');
  const g = window.__game;
  const ids = [...new Set(g.pois.filter(p => p.dungeonTheme)
    .map(p => new Dungeon(p, 1, 1, 1).bossId))];
  const drops = ids.map(id => ({ id, d: D.BOSSES[id]?.reward || null }));
  const shopHas = new Set((D.SHOP || []).map(s => s.weapon || s.shield || s.armor || s.item));
  return { drops, leaked: drops.filter(x => x.d &&
    shopHas.has(x.d.weapon || x.d.shield || x.d.talisman)).map(x => x.id) };
});
console.log('地下のあるじの落とし物:', uniq.drops
  .map(x => `${x.id}→${JSON.stringify(x.d)}`).join(' / '));
ok('地下のあるじは固有の品を落とす',
  uniq.drops.every(x => x.d && (x.d.weapon || x.d.shield || x.d.talisman)),
  `${uniq.drops.length}体`);
ok('その品は店では買えない', uniq.leaked.length === 0, uniq.leaked.join(',') || 'なし');

report(errs);
await close();
