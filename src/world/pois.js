// ランドマーク生成：篝火・集落・敵陣・遺跡・ボス闘技場・望楼、および街道網

import { makeRng, hash2 } from '../core/noise.js';
import { WORLD_RADIUS } from './worldgen.js';

let uid = 1;

/** 手作りの主要拠点（メインクエストの舞台） */
const HANDMADE = [
  { type: 'shrine', name: '始まりの篝火', x: 40, z: 300, tag: 'start' },
  { type: 'village', name: '灰かぶりの村', x: -120, z: 430, tag: 'hub', size: 9 },
  { type: 'shrine', name: '村外れの篝火', x: -190, z: 520 },
  { type: 'tower', name: '見張り塔・草原', x: 300, z: 120 },
  { type: 'boss', name: '朽ちた巨兵', x: 380, z: 640, boss: 'colossus', tag: 'boss_tutorial' },

  { type: 'shrine', name: '森の入口', x: -560, z: -180 },
  { type: 'village', name: '木漏れ日の集落', x: -700, z: -260, size: 6 },
  { type: 'boss', name: '森の主 オルグレン', x: -880, z: -620, boss: 'orgren', tag: 'boss_wood' },
  { type: 'tower', name: '見張り塔・森', x: -520, z: -700 },

  { type: 'shrine', name: '湿原の残り火', x: -880, z: 760 },
  { type: 'boss', name: '湿原の魔女 セラフィナ', x: -1040, z: 960, boss: 'witch', tag: 'boss_fen' },

  { type: 'shrine', name: '黄金の道標', x: 1050, z: 470 },
  { type: 'village', name: '穂波の町', x: 1180, z: 560, size: 12 },
  { type: 'boss', name: '黄金の将 レオンハルト', x: 1380, z: 300, boss: 'leonhart', tag: 'boss_gold' },

  { type: 'shrine', name: '灰の関所', x: 800, z: -760 },
  { type: 'boss', name: '灰の騎士 ガルヴァン', x: 1040, z: -1080, boss: 'galvan', tag: 'boss_cinder' },
  { type: 'tower', name: '見張り塔・灰燼', x: 900, z: -640 },

  { type: 'shrine', name: '雪解けの祠', x: 380, z: -1320 },
  { type: 'boss', name: '蒼天の竜 ヴァルドリス', x: 430, z: -1660, boss: 'dragon', tag: 'boss_sky' },

  { type: 'shrine', name: '裂け目の縁', x: -300, z: 1300 },
  { type: 'boss', name: '深淵の王 ノクトゥルヌス', x: -340, z: 1560, boss: 'nocturnus', tag: 'boss_final' },

  { type: 'shrine', name: '潮鳴りの岩場', x: 1450, z: 1280 },
  { type: 'village', name: '磯場の宿', x: 1560, z: 1400, size: 5 },
];

const CAMP_NAMES = ['野盗の陣', '略奪者の宿営', '崩れた砦', '狩人の廃屋', '傭兵の駐屯地', '密猟者の隠れ家'];
const RUIN_NAMES = ['忘れられた列柱', '崩れた聖堂', '古き門', '沈黙の墓所', '風化した回廊'];
const GRAVE_NAMES = ['地下墓所', '骸の穴', '朽ちた霊廟'];

export function generatePOIs(world) {
  const rng = makeRng(world.seed + 31337);
  const pois = [];

  const add = (o) => {
    o.id = uid++;
    o.discovered = false;
    o.cleared = false;
    o.structures = [];
    o.spawns = [];
    pois.push(o);
    return o;
  };

  for (const h of HANDMADE) {
    const region = world.regionAt(h.x, h.z);
    add({ ...h, region: region.id, danger: region.danger, y: 0 });
  }

  // --- 手続き的な小拠点 ---
  const CELL = 240;
  const span = Math.ceil(WORLD_RADIUS / CELL);
  for (let cz = -span; cz <= span; cz++) {
    for (let cx = -span; cx <= span; cx++) {
      const r = hash2(cx, cz, world.seed + 71);
      if (r > 0.42) continue;
      const x = (cx + 0.2 + hash2(cx, cz, 11) * 0.6) * CELL;
      const z = (cz + 0.2 + hash2(cx, cz, 23) * 0.6) * CELL;
      if (Math.hypot(x, z) > WORLD_RADIUS - 160) continue;
      const h = world.heightBase(x, z);
      if (h < 3 || h > 170) continue;
      if (world.slope(x, z) > 0.55) continue;
      // 主要拠点の近くには作らない
      let tooClose = false;
      for (const p of pois) {
        if (Math.hypot(p.x - x, p.z - z) < 190) { tooClose = true; break; }
      }
      if (tooClose) continue;

      const region = world.regionAt(x, z);
      const k = hash2(cx, cz, 907);
      let type, name;
      if (k < 0.30) { type = 'camp'; name = CAMP_NAMES[(hash2(cx, cz, 5) * CAMP_NAMES.length) | 0]; }
      else if (k < 0.55) { type = 'ruin'; name = RUIN_NAMES[(hash2(cx, cz, 6) * RUIN_NAMES.length) | 0]; }
      else if (k < 0.70) { type = 'grave'; name = GRAVE_NAMES[(hash2(cx, cz, 7) * GRAVE_NAMES.length) | 0]; }
      else if (k < 0.82) { type = 'shrine'; name = `${region.name}の篝火`; }
      else if (k < 0.92) { type = 'crystal'; name = '結晶の露頭'; }
      else { type = 'camp'; name = '亜人の集落'; }
      add({ type, name, x, z, y: 0, region: region.id, danger: region.danger });
    }
  }

  /* ------------------------------------------------ 平坦化と街道の登録 */
  const MIN_SITE_H = 6;
  for (const p of pois) {
    // 水際・低湿地に拠点ができないよう、必要なら近くの高台へずらす
    if (world.heightBase(p.x, p.z) < MIN_SITE_H) {
      const spot = world.findFlat(p.x, p.z, 130, 64, 0.6, MIN_SITE_H);
      if (world.heightBase(spot.x, spot.z) >= MIN_SITE_H) { p.x = spot.x; p.z = spot.z; }
    }
    const r = p.type === 'village' ? 24 + (p.size || 6) * 1.4
      : p.type === 'boss' ? 24
        : p.type === 'camp' ? 15
          : p.type === 'shrine' ? 5.5 : 10;
    p.plateauR = r;
    // それでも低い場所なら、地面自体を少し持ち上げて浸水を防ぐ
    const siteH = Math.max(world.heightBase(p.x, p.z), MIN_SITE_H);
    world.addPlateau(p.x, p.z, r, r * (p.type === 'shrine' ? 1.1 : 0.7), siteH);
    p.y = world.height(p.x, p.z);
  }

  // 主要拠点を最小全域木で接続 → 街道
  const nodes = pois.filter((p) => ['village', 'shrine', 'boss', 'tower'].includes(p.type));
  if (nodes.length > 1) {
    const inTree = new Set([0]);
    const edges = [];
    while (inTree.size < nodes.length) {
      let best = null, bestD = Infinity;
      for (const i of inTree) {
        for (let j = 0; j < nodes.length; j++) {
          if (inTree.has(j)) continue;
          const d = Math.hypot(nodes[i].x - nodes[j].x, nodes[i].z - nodes[j].z);
          if (d < bestD) { bestD = d; best = [i, j]; }
        }
      }
      if (!best) break;
      inTree.add(best[1]);
      edges.push(best);
    }
    for (const [i, j] of edges) {
      const a = nodes[i], b = nodes[j];
      const dist = Math.hypot(b.x - a.x, b.z - a.z);
      if (dist > 900) continue;      // 遠すぎる接続は道にしない
      const steps = Math.max(2, Math.ceil(dist / 26));
      let px = a.x, pz = a.z;
      for (let s = 1; s <= steps; s++) {
        const t = s / steps;
        // 少し蛇行させる
        const bend = Math.sin(t * Math.PI) * (hash2(i, j, s) - 0.5) * dist * 0.14;
        const nx = -(b.z - a.z) / dist, nz = (b.x - a.x) / dist;
        const qx = a.x + (b.x - a.x) * t + nx * bend;
        const qz = a.z + (b.z - a.z) * t + nz * bend;
        world.addRoad(px, pz, qx, qz, 2.4, 3.5);
        px = qx; pz = qz;
      }
    }
  }

  // --- 建造物とスポーン定義 ---
  for (const p of pois) {
    p.y = world.height(p.x, p.z);
    buildStructures(world, p, makeRng((p.x * 7919 + p.z * 104729) | 0));
  }

  void rng;
  return pois;
}

function place(p, model, dx, dz, rotY, sx, sy, sz, world, tint = [1, 1, 1], wind = 0, emissive = 0) {
  const x = p.x + dx, z = p.z + dz;
  const y = world.height(x, z);
  p.structures.push({ model, x, y, z, rotY, sx, sy, sz, r: tint[0], g: tint[1], b: tint[2], wind, emissive });
}

function buildStructures(world, p, rng) {
  const TAU = Math.PI * 2;
  switch (p.type) {
    case 'shrine': {
      place(p, 'shrine', 0, 0, 0, 1, 1, 1, world, [1, 0.92, 0.6], 0, 0.9);
      for (let i = 0; i < 3; i++) {
        const a = rng() * TAU, r = 4 + rng() * 3;
        place(p, `rock_${(rng() * 3) | 0}`, Math.cos(a) * r, Math.sin(a) * r, rng() * TAU,
          0.7, 0.6, 0.7, world);
      }
      break;
    }
    case 'village': {
      const size = p.size || 6;
      const houses = size;
      for (let i = 0; i < houses; i++) {
        const a = (i / houses) * TAU + rng() * 0.35;
        const r = 10 + rng() * (6 + size * 0.9);
        place(p, rng() < 0.5 ? 'house_a' : 'house_b',
          Math.cos(a) * r, Math.sin(a) * r, -a + Math.PI / 2, 1, 1, 1, world);
      }
      place(p, 'campfire', 0, 0, 0, 1, 1, 1, world, [1, 0.8, 0.5], 0, 0.6);
      place(p, 'signpost', 5, 3, rng() * TAU, 1, 1, 1, world);
      for (let i = 0; i < 8; i++) {
        const a = rng() * TAU, r = 16 + rng() * 10;
        place(p, 'fence', Math.cos(a) * r, Math.sin(a) * r, -a, 1, 1, 1, world);
      }
      for (let i = 0; i < 4; i++) {
        const a = rng() * TAU, r = 5 + rng() * 8;
        place(p, 'barrel', Math.cos(a) * r, Math.sin(a) * r, rng() * TAU, 1, 1, 1, world);
      }
      if (size >= 9) {
        place(p, 'tower', -14, -12, 0, 1, 1, 1, world);
        place(p, 'statue', 8, -8, rng() * TAU, 1, 1, 1, world);
      }
      p.services = size >= 9 ? ['merchant', 'smith', 'inn'] : ['merchant'];
      break;
    }
    case 'camp': {
      const tents = 2 + (rng() * 3 | 0);
      for (let i = 0; i < tents; i++) {
        const a = (i / tents) * TAU + rng() * 0.5, r = 5 + rng() * 4;
        place(p, 'tent', Math.cos(a) * r, Math.sin(a) * r, rng() * TAU, 1, 1, 1, world);
      }
      place(p, 'campfire', 0, 0, 0, 1, 1, 1, world, [1, 0.75, 0.45], 0, 0.7);
      for (let i = 0; i < 3; i++) {
        const a = rng() * TAU, r = 4 + rng() * 7;
        place(p, rng() < 0.5 ? 'barrel' : 'fence', Math.cos(a) * r, Math.sin(a) * r, rng() * TAU, 1, 1, 1, world);
      }
      place(p, 'chest', -3, 4, rng() * TAU, 1, 1, 1, world, [1, 0.95, 0.7]);
      p.chest = { x: p.x - 3, z: p.z + 4, opened: false, tier: 1 + (p.danger / 3 | 0) };
      p.spawns = [{ kind: 'bandit', count: 2 + (rng() * 3 | 0), radius: 12 }];
      if (p.danger >= 5) p.spawns.push({ kind: 'brute', count: 1, radius: 8 });
      break;
    }
    case 'ruin': {
      p.dungeonTheme = rng() < 0.5 ? 'ruin' : 'mine';
      const n = 4 + (rng() * 4 | 0);
      for (let i = 0; i < n; i++) {
        const a = (i / n) * TAU, r = 7 + rng() * 4;
        place(p, 'pillar', Math.cos(a) * r, Math.sin(a) * r, rng() * TAU, 1, 1, 1, world);
      }
      place(p, 'ruin_arch', 0, -6, rng() * TAU, 1, 1, 1, world);
      place(p, 'chest', 0, 0, rng() * TAU, 1, 1, 1, world, [1, 0.95, 0.7]);
      p.chest = { x: p.x, z: p.z, opened: false, tier: 2 + (p.danger / 3 | 0) };
      p.spawns = [{ kind: 'skeleton', count: 2 + (rng() * 2 | 0), radius: 10 }];
      break;
    }
    case 'grave': {
      p.dungeonTheme = rng() < 0.5 ? 'catacomb' : 'ruin';
      place(p, 'ruin_arch', 0, 0, rng() * TAU, 1.2, 1.2, 1.2, world, [0.7, 0.7, 0.75]);
      for (let i = 0; i < 6; i++) {
        const a = rng() * TAU, r = 4 + rng() * 6;
        place(p, 'pillar', Math.cos(a) * r, Math.sin(a) * r, rng() * TAU, 0.6, 0.6, 0.6, world, [0.6, 0.6, 0.65]);
      }
      place(p, 'chest', 2, 2, rng() * TAU, 1, 1, 1, world, [1, 0.9, 0.6]);
      p.chest = { x: p.x + 2, z: p.z + 2, opened: false, tier: 3 + (p.danger / 3 | 0) };
      p.spawns = [{ kind: 'skeleton', count: 3 + (rng() * 3 | 0), radius: 12 },
      { kind: 'wraith', count: 1 + (rng() * 2 | 0), radius: 10 }];
      break;
    }
    case 'tower': {
      place(p, 'tower', 0, 0, 0, 1.1, 1.15, 1.1, world);
      place(p, 'fence', 4, 4, rng() * TAU, 1, 1, 1, world);
      p.spawns = [{ kind: 'bandit', count: 1 + (rng() * 2 | 0), radius: 10 }];
      break;
    }
    case 'crystal': {
      for (let i = 0; i < 4; i++) {
        const a = rng() * TAU, r = rng() * 5;
        place(p, 'crystal', Math.cos(a) * r, Math.sin(a) * r, rng() * TAU,
          0.8 + rng() * 1.2, 1 + rng() * 1.4, 0.8 + rng() * 1.2, world, [0.5, 0.75, 1], 0, 0.55);
      }
      p.harvest = { kind: 'crystal', count: 3, respawn: 0 };
      break;
    }
    case 'boss': {
      place(p, 'gate', 0, -19, 0, 1, 1, 1, world, [0.55, 0.55, 0.6]);
      const n = 10;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * TAU, r = 19 + rng() * 2;
        place(p, 'pillar', Math.cos(a) * r, Math.sin(a) * r, rng() * 0.4,
          1.3, 1.4 + rng() * 0.8, 1.3, world, [0.6, 0.6, 0.62]);
        if (rng() < 0.5) {
          place(p, `rock_${(rng() * 3) | 0}`, Math.cos(a) * (r - 4), Math.sin(a) * (r - 4),
            rng() * TAU, 1.1, 0.9, 1.1, world);
        }
      }
      place(p, 'statue', 0, 17, Math.PI, 1, 1, 1, world, [0.55, 0.55, 0.58]);
      p.arenaR = 22;
      break;
    }
  }
}
