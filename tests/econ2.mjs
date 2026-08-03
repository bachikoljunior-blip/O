// econ2
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/* ---------- 1. 世界にどの鉱脈がどこに湧くか ---------- */
const survey = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const px = p.x, pz = p.z;
  const seen = new Map();          // key -> {type, h, region}
  for (let i = 0; i < 2600; i++) {
    const a = i * 2.39996, rr = 20 + i * 0.72;
    p.x = px + Math.cos(a) * rr;
    p.z = pz + Math.sin(a) * rr;
    p.y = g.groundHeight(p.x, p.z);
    g.updateGatherables();
    for (const [k, v] of g.gatherables) {
      if (!v || seen.has(k)) continue;
      seen.set(k, { type: v.type, h: Math.round(v.y),
        region: g.world.params(v.x, v.z).region.id });
    }
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  const counts = {}, heights = {}, regions = {};
  for (const v of seen.values()) {
    counts[v.type] = (counts[v.type] || 0) + 1;
    (heights[v.type] = heights[v.type] || []).push(v.h);
    regions[v.type] = regions[v.type] || new Set();
    regions[v.type].add(v.region);
  }
  const out = {};
  for (const [k, n] of Object.entries(counts)) {
    const hs = heights[k].sort((a, b) => a - b);
    out[k] = { n, medH: hs[Math.floor(hs.length / 2)], minH: hs[0], maxH: hs[hs.length-1],
      regions: [...regions[k]].join(',') };
  }
  return { out, total: seen.size };
});
console.log(`採取点 ${survey.total} 箇所:`);
for (const [k, v] of Object.entries(survey.out).sort((a,b)=>b[1].n-a[1].n)) {
  console.log(`  ${k.padEnd(14)} ${String(v.n).padStart(4)}箇所  標高 ${String(v.minH).padStart(4)}〜${String(v.maxH).padStart(4)}m（中央${v.medH}）  ${v.regions}`);
}
const S = survey.out;
const N = (k) => S[k] || { n: 0, medH: 0, regions: '' };
ok('銀鉱石が世界に湧く', !!S.ore_silver && S.ore_silver.n > 20, `${S.ore_silver?.n || 0}箇所`);
ok('古き欠片が世界に湧く', !!S.shard_ancient && S.shard_ancient.n > 0, `${S.shard_ancient?.n || 0}箇所`);
ok('骨灰が世界に湧く', !!S.bone_ash && S.bone_ash.n > 10, `${S.bone_ash?.n || 0}箇所`);
ok('良い鉱脈ほど数が少ない',
  N('ore_iron').n > N('ore_silver').n && N('ore_silver').n > N('shard_ancient').n,
  `鉄${N('ore_iron').n} > 銀${N('ore_silver').n} > 古${N('shard_ancient').n}`);
ok('良い鉱脈ほど高いところにある',
  N('shard_ancient').medH > N('ore_silver').medH && N('ore_silver').medH > N('ore_iron').medH,
  `鉄${N('ore_iron').medH}m < 銀${N('ore_silver').medH}m < 古${N('shard_ancient').medH}m`);
ok('骨灰は死者の地にだけ出る',
  ['gloomwood','riftvale','mistfen'].some(r => N('bone_ash').regions.includes(r))
  && !N('bone_ash').regions.includes('goldreach'),
  N('bone_ash').regions);

/* ---------- 2. 実際に採れる ---------- */
const take = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const sfx = [];
  const oPlay = g.audio.play.bind(g.audio);
  g.audio.play = (n, o) => { sfx.push(n); return oPlay(n, o); };
  const px = p.x, pz = p.z;
  const before = { ...p.inventory };
  let picked = 0;
  for (let i = 0; i < 2600; i++) {
    const a = i * 2.39996, rr = 20 + i * 0.72;
    p.x = px + Math.cos(a) * rr;
    p.z = pz + Math.sin(a) * rr;
    p.y = g.groundHeight(p.x, p.z);
    g.updateGatherables();
    for (const [key, v] of g.gatherables) {
      if (!v || g.harvested.has(key)) continue;
      // その場に立って採る
      p.x = v.x; p.z = v.z; p.y = v.y;
      g.updateInteract();
      const it = g.interact;
      if (it && it.type === 'gather') { g.doInteract(it); picked++; }
    }
  }
  p.x = px; p.z = pz; p.y = g.groundHeight(px, pz);
  g.audio.play = oPlay;
  const gained = {};
  for (const k of new Set([...Object.keys(before), ...Object.keys(p.inventory)])) {
    const d = (p.inventory[k] || 0) - (before[k] || 0);
    if (d) gained[k] = d;
  }
  return { picked, gained, plant: sfx.filter(n => n === 'gather_plant').length,
    ore: sfx.filter(n => n === 'gather_ore').length };
});
console.log(`採取 ${take.picked} 回:`, JSON.stringify(take.gained));
console.log(`  音: 草 ${take.plant} 回 / 鉱脈 ${take.ore} 回`);
ok('採取が成立する', take.picked > 100, `${take.picked}回`);
ok('銀鉱石が実際に手に入る', (take.gained.ore_silver || 0) > 0, String(take.gained.ore_silver));
ok('古き欠片も手に入る', (take.gained.shard_ancient || 0) > 0, String(take.gained.shard_ancient));
ok('草と鉱脈で音が違う', take.plant > 0 && take.ore > 0,
  `草${take.plant} 鉱脈${take.ore}`);
ok('鉱脈のほうが 1 回あたり少ない',
  (take.gained.ore_silver || 0) / Math.max(1, take.gained.ore_iron || 1) < 1);

/* ---------- 3. 強化の道が閉じているか ---------- */
const gate = await page.evaluate(async () => {
  const D = await import('/src/game/data.js');
  const g = window.__game, p = g.player;
  const need = D.UPGRADE.toMax(0);
  const have = { ore_iron: p.inventory.ore_iron || 0, ore_silver: p.inventory.ore_silver || 0,
    shard_ancient: p.inventory.shard_ancient || 0, bone_ash: p.inventory.bone_ash || 0 };
  // 調合で不足を埋められるか
  const canCraftSilver = D.RECIPES.find(r => r.out === 'ore_silver');
  const canCraftShard = D.RECIPES.find(r => r.out === 'shard_ancient');
  return { need: need.mats, needEcho: need.echo, have,
    craftSilver: canCraftSilver ? canCraftSilver.need : null,
    craftShard: canCraftShard ? canCraftShard.need : null };
});
console.log('極みまで:', JSON.stringify(gate.need), `+ ${gate.needEcho}残響`);
console.log('一巡の採取で手元に:', JSON.stringify(gate.have));
ok('一度世界を巡れば鉄は足りる', gate.have.ore_iron >= gate.need.ore_iron,
  `${gate.have.ore_iron} / 必要 ${gate.need.ore_iron}`);
ok('銀も足りる', gate.have.ore_silver >= gate.need.ore_silver,
  `${gate.have.ore_silver} / 必要 ${gate.need.ore_silver}`);
ok('古き欠片は足りないぶんを調合で作れる',
  !!gate.craftShard && gate.have.bone_ash > 0,
  `古き欠片 ${gate.have.shard_ancient}/${gate.need.shard_ancient}、骨灰 ${gate.have.bone_ash}`);

report(errs);
await close();
