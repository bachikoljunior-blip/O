// worldgen.js — procedural continent: elevation, climate, biomes, rivers,
// settlements, roads and points of interest. Fully deterministic from a seed.

import { fbm, ridge, valueNoise2, hash2, clamp, lerp, smoothstep, invLerp, makeRNG, dist } from '../core/util.js';

export const TILE = 32;
export const WORLD_W = 768;
export const WORLD_H = 768;
export const SEA = 0.42;

export const B = {
  DEEP: 0, OCEAN: 1, SHALLOW: 2, BEACH: 3, GRASS: 4, MEADOW: 5, FOREST: 6,
  DARKWOOD: 7, TAIGA: 8, SNOW: 9, TUNDRA: 10, DESERT: 11, SAVANNA: 12,
  SWAMP: 13, ROCK: 14, PEAK: 15, ASH: 16, FARM: 17, ROAD: 18, RIVER: 19, FLOOR: 20,
};

export const BIOME_NAME = {
  [B.DEEP]: '深海', [B.OCEAN]: '海', [B.SHALLOW]: '浅瀬', [B.BEACH]: '砂浜',
  [B.GRASS]: '草原', [B.MEADOW]: '花畑', [B.FOREST]: '森', [B.DARKWOOD]: '暗い森',
  [B.TAIGA]: '針葉樹林', [B.SNOW]: '雪原', [B.TUNDRA]: 'ツンドラ', [B.DESERT]: '砂漠',
  [B.SAVANNA]: 'サバンナ', [B.SWAMP]: '湿地', [B.ROCK]: '岩山', [B.PEAK]: '霊峰',
  [B.ASH]: '灰の荒野', [B.FARM]: '農地', [B.ROAD]: '街道', [B.RIVER]: '川', [B.FLOOR]: '街',
};

// Tiles that block movement outright.
export const SOLID = new Set([B.DEEP, B.OCEAN, B.PEAK]);
// Tiles that slow you down / count as water.
export const WATER = new Set([B.DEEP, B.OCEAN, B.SHALLOW, B.RIVER]);

const NAMES_A = ['アル', 'ヴェル', 'エル', 'ソル', 'ミル', 'カル', 'ノル', 'リン', 'ゼフ', 'グラン', 'テル', 'オル', 'ファル', 'ヘイム', 'イス'];
const NAMES_B = ['ハイム', 'ガルド', 'ドール', 'ヴィア', 'モント', 'ブルク', 'テア', 'リア', 'ネス', 'ハーレ', 'フェン', 'スト', 'ダール'];

export class WorldData {
  constructor(seed) {
    this.seed = seed >>> 0;
    this.w = WORLD_W;
    this.h = WORLD_H;
    this.height = new Float32Array(this.w * this.h);
    this.moist = new Float32Array(this.w * this.h);
    this.biome = new Uint8Array(this.w * this.h);
    /** overlay: 0 none, 1 road, 2 river, 3 farm, 4 town floor, 5 bridge, 6 plaza */
    this.overlay = new Uint8Array(this.w * this.h);
    this.settlements = [];
    this.pois = [];
    this.roadNodes = [];
  }
  idx(x, y) { return y * this.w + x; }
  inBounds(x, y) { return x >= 0 && y >= 0 && x < this.w && y < this.h; }
  heightAt(x, y) {
    if (!this.inBounds(x, y)) return 0;
    return this.height[y * this.w + x];
  }
  biomeAt(x, y) {
    if (!this.inBounds(x, y)) return B.DEEP;
    return this.biome[y * this.w + x];
  }
  overlayAt(x, y) {
    if (!this.inBounds(x, y)) return 0;
    return this.overlay[y * this.w + x];
  }
  isSolidTile(x, y) {
    if (!this.inBounds(x, y)) return true;
    const o = this.overlay[y * this.w + x];
    if (o === 1 || o === 3 || o === 4 || o === 5 || o === 6) return false;
    return SOLID.has(this.biome[y * this.w + x]);
  }
  isWaterTile(x, y) {
    if (!this.inBounds(x, y)) return true;
    const o = this.overlay[y * this.w + x];
    if (o === 5) return false; // bridge
    return WATER.has(this.biome[y * this.w + x]);
  }
}

function pickBiome(e, m, t, world) {
  if (e < SEA - 0.10) return B.DEEP;
  if (e < SEA - 0.025) return B.OCEAN;
  if (e < SEA) return B.SHALLOW;
  if (e < SEA + 0.018) return B.BEACH;
  if (e > 0.85) return B.PEAK;
  if (e > 0.72) return B.ROCK;
  if (t < 0.20) return e > 0.62 ? B.SNOW : m > 0.45 ? B.TAIGA : B.TUNDRA;
  if (t < 0.34) return m > 0.55 ? B.TAIGA : m > 0.3 ? B.FOREST : B.TUNDRA;
  if (t > 0.78) {
    if (m < 0.28) return B.DESERT;
    if (m < 0.52) return B.SAVANNA;
    return B.SWAMP;
  }
  if (m < 0.24) return B.SAVANNA;
  if (m > 0.78) return e < SEA + 0.06 ? B.SWAMP : B.DARKWOOD;
  if (m > 0.58) return B.FOREST;
  if (m > 0.42) return B.MEADOW;
  return B.GRASS;
}

/**
 * Generates the world in incremental steps so a loading screen can breathe.
 * Usage: `for (const p of generateWorld(seed)) { showProgress(p) }`
 */
export function* generateWorld(seed) {
  const world = new WorldData(seed);
  const { w, h } = world;
  const S = world.seed;
  const rng = makeRNG(S ^ 0x9e3779b9);

  // ————————————————————————————————————— 1. elevation + climate
  const F1 = 1 / 170, F2 = 1 / 260, F3 = 1 / 90;
  const rows = 24;
  for (let y0 = 0; y0 < h; y0 += rows) {
    for (let y = y0; y < Math.min(h, y0 + rows); y++) {
      for (let x = 0; x < w; x++) {
        const nx = x / w - 0.5, ny = y / h - 0.5;
        const base = fbm(x * F1, y * F1, S, 6);
        const rg = ridge(x * F2, y * F2, S + 4001, 5);
        const mountainMask = smoothstep(clamp(invLerp(0.40, 0.70, fbm(x * F2 * 0.7, y * F2 * 0.7, S + 777, 3)), 0, 1));
        // inland elevation sits comfortably above sea level, with ranges on top
        let land = 0.52 + (base - 0.5) * 0.62
          + rg * 0.54 * mountainMask
          + (fbm(x * F3, y * F3, S + 22, 3) - 0.5) * 0.10;

        // continental mask — an island continent with a wiggly coast
        let d = Math.sqrt(nx * nx * 1.05 + ny * ny * 1.18) * 2;
        d += (fbm(x * 0.0090, y * 0.0090, S + 91, 4) - 0.5) * 0.80;
        d += (valueNoise2(x * 0.03, y * 0.03, S + 55) - 0.5) * 0.12;
        const mask = 1 - smoothstep(clamp(invLerp(0.74, 1.26, d), 0, 1));
        let e = lerp(0.06, land, mask);

        e = clamp(e, 0, 1);
        world.height[y * w + x] = e;

        // moisture: stretched around the mean so wet/dry extremes actually occur
        let m = fbm(x * F1 * 1.25 + 500, y * F1 * 1.25 - 320, S + 1337, 5);
        m = (m - 0.5) * 2.35 + 0.5;
        m += (fbm(x * 0.004 - 900, y * 0.004 + 210, S + 6151, 3) - 0.5) * 0.7;
        m = clamp(m - Math.max(0, e - SEA) * 0.30, 0, 1);
        world.moist[y * w + x] = m;
      }
    }
    yield { phase: '大地を形づくる', t: (y0 / h) * 0.42 };
  }

  // ————————————————————————————————————— 2. biomes
  for (let y0 = 0; y0 < h; y0 += 64) {
    for (let y = y0; y < Math.min(h, y0 + 64); y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x;
        const e = world.height[i];
        const m = world.moist[i];
        // temperature: latitude band + altitude + regional noise
        let t = 1 - Math.abs(y / h - 0.5) * 1.55;
        t += ((fbm(x * 0.005, y * 0.005, S + 4242, 3) - 0.5) * 2) * 0.30;
        t = clamp(t - Math.max(0, e - SEA) * 1.05, 0, 1);
        world.biome[i] = pickBiome(e, m, t, world);
      }
    }
    yield { phase: '気候を巡らせる', t: 0.42 + (y0 / h) * 0.14 };
  }

  // volcanic region: one hot ashen zone around the highest inland peak
  {
    let bx = 0, by = 0, best = -1;
    for (let y = 60; y < h - 60; y += 3) {
      for (let x = 60; x < w - 60; x += 3) {
        const e = world.height[y * w + x];
        if (e > best) { best = e; bx = x; by = y; }
      }
    }
    world.volcano = { x: bx, y: by };
    const R = 46;
    for (let y = by - R; y <= by + R; y++) {
      for (let x = bx - R; x <= bx + R; x++) {
        if (!world.inBounds(x, y)) continue;
        const d = dist(x, y, bx, by) + (valueNoise2(x * 0.08, y * 0.08, S + 3) - 0.5) * 16;
        if (d > R) continue;
        const i = y * w + x;
        if (world.biome[i] === B.DEEP || world.biome[i] === B.OCEAN) continue;
        if (d < 8) world.biome[i] = B.PEAK;
        else world.biome[i] = B.ASH;
      }
    }
  }
  yield { phase: '火山を灯す', t: 0.58 };

  // ————————————————————————————————————— 3. rivers
  const riverCount = 22;
  for (let r = 0; r < riverCount; r++) {
    // start high
    let sx = 0, sy = 0, bestE = -1;
    for (let k = 0; k < 220; k++) {
      const x = rng.irange(24, w - 25), y = rng.irange(24, h - 25);
      const e = world.height[y * w + x];
      if (e > bestE && e < 0.9 && e > 0.62) { bestE = e; sx = x; sy = y; }
    }
    if (bestE < 0) continue;
    let x = sx, y = sy;
    const path = [];
    for (let step = 0; step < 900; step++) {
      path.push([x, y]);
      const i = y * w + x;
      if (WATER.has(world.biome[i]) && world.biome[i] !== B.RIVER) break;
      // steepest descent with a wander term
      let bx = x, by = y, bh = world.height[i] + 0.0008;
      for (let oy = -1; oy <= 1; oy++) {
        for (let ox = -1; ox <= 1; ox++) {
          if (!ox && !oy) continue;
          const nx2 = x + ox, ny2 = y + oy;
          if (!world.inBounds(nx2, ny2)) continue;
          const hh = world.height[ny2 * w + nx2] + (valueNoise2(nx2 * 0.2, ny2 * 0.2, S + r) - 0.5) * 0.02;
          if (hh < bh) { bh = hh; bx = nx2; by = ny2; }
        }
      }
      if (bx === x && by === y) {
        // local minimum: carve onward in last direction, or stop
        const a = rng() * Math.PI * 2;
        bx = clamp(x + Math.round(Math.cos(a) * 2), 1, w - 2);
        by = clamp(y + Math.round(Math.sin(a) * 2), 1, h - 2);
        if (step > 400) break;
      }
      x = bx; y = by;
    }
    if (path.length < 40) continue;
    // carve with widening downstream
    for (let p = 0; p < path.length; p++) {
      const [px, py] = path[p];
      const wdt = 0.6 + (p / path.length) * 2.1;
      const rad = Math.ceil(wdt);
      for (let oy = -rad; oy <= rad; oy++) {
        for (let ox = -rad; ox <= rad; ox++) {
          const nx2 = px + ox, ny2 = py + oy;
          if (!world.inBounds(nx2, ny2)) continue;
          if (Math.hypot(ox, oy) > wdt) continue;
          const i = ny2 * w + nx2;
          if (world.biome[i] === B.DEEP || world.biome[i] === B.OCEAN) continue;
          world.biome[i] = B.RIVER;
          world.overlay[i] = 2;
          world.height[i] = Math.min(world.height[i], SEA + 0.004);
          world.moist[i] = 1;
        }
      }
    }
  }
  yield { phase: '川を流す', t: 0.66 };

  // ————————————————————————————————————— 4. settlements
  const settleDefs = [
    { type: 'capital', label: '王都', size: 30, minDist: 0 },
    { type: 'town', label: '町', size: 21, minDist: 90 },
    { type: 'town', label: '町', size: 20, minDist: 90 },
    { type: 'village', label: '村', size: 15, minDist: 70 },
    { type: 'village', label: '村', size: 15, minDist: 70 },
    { type: 'village', label: '村', size: 14, minDist: 70 },
    { type: 'hamlet', label: '集落', size: 11, minDist: 60 },
    { type: 'hamlet', label: '集落', size: 11, minDist: 60 },
    { type: 'hamlet', label: '集落', size: 10, minDist: 60 },
  ];

  const flatness = (cx, cy, rad) => {
    let lo = 9, hi = -9, waterN = 0, n = 0, near = 0;
    for (let y = cy - rad; y <= cy + rad; y += 2) {
      for (let x = cx - rad; x <= cx + rad; x += 2) {
        if (!world.inBounds(x, y)) return null;
        const i = y * w + x;
        const e = world.height[i];
        const b = world.biome[i];
        if (WATER.has(b)) { waterN++; if (b === B.RIVER || b === B.SHALLOW) near++; }
        if (b === B.PEAK || b === B.ROCK || b === B.ASH) return null;
        lo = Math.min(lo, e); hi = Math.max(hi, e); n++;
      }
    }
    if (waterN / n > 0.16) return null;
    return { relief: hi - lo, water: near, n };
  };

  for (const def of settleDefs) {
    let best = null;
    for (let k = 0; k < 2600; k++) {
      const x = rng.irange(50, w - 51), y = rng.irange(50, h - 51);
      const i = y * w + x;
      const b = world.biome[i];
      if (WATER.has(b) || b === B.PEAK || b === B.ROCK || b === B.ASH || b === B.BEACH) continue;
      let tooClose = false;
      for (const s of world.settlements) {
        if (dist(x, y, s.x, s.y) < Math.max(def.minDist, s.size * 3)) { tooClose = true; break; }
      }
      if (tooClose) continue;
      const f = flatness(x, y, def.size + 4);
      if (!f) continue;
      let score = 1 / (0.03 + f.relief) + f.water * 1.2;
      if (b === B.GRASS || b === B.MEADOW) score *= 1.35;
      if (b === B.SWAMP || b === B.DESERT || b === B.SNOW) score *= 0.6;
      if (world.settlements.length === 0) {
        // capital prefers world centre for a friendly starting area
        score *= 1.6 - dist(x, y, w / 2, h / 2) / (w * 0.55);
      }
      if (!best || score > best.score) best = { x, y, score, biome: b };
    }
    if (!best) continue;
    const name = rng.pick(NAMES_A) + rng.pick(NAMES_B);
    world.settlements.push(buildSettlement(world, best.x, best.y, def, name, makeRNG(S + world.settlements.length * 7717)));
    yield { phase: '街を興す', t: 0.66 + (world.settlements.length / settleDefs.length) * 0.16 };
  }

  // ————————————————————————————————————— 5. roads between settlements
  const linked = new Set();
  const list = world.settlements;
  for (let i = 0; i < list.length; i++) {
    // connect each settlement to its two nearest neighbours
    const others = list
      .map((s, j) => ({ s, j, d: dist(list[i].x, list[i].y, s.x, s.y) }))
      .filter((o) => o.j !== i)
      .sort((a, b) => a.d - b.d)
      .slice(0, 2);
    for (const o of others) {
      const key = Math.min(i, o.j) + ':' + Math.max(i, o.j);
      if (linked.has(key)) continue;
      linked.add(key);
      carveRoad(world, list[i], o.s);
    }
    yield { phase: '街道を敷く', t: 0.82 + (i / list.length) * 0.10 };
  }

  // ————————————————————————————————————— 6. points of interest
  placePOIs(world, rng);
  yield { phase: '秘密を隠す', t: 0.97 };

  world.start = findStart(world);
  yield { phase: '完了', t: 1, world };
  return world;
}

// ————————————————————————————————————————————————————————— settlements

function buildSettlement(world, cx, cy, def, name, rng) {
  const s = {
    x: cx, y: cy, type: def.type, label: def.label, name, size: def.size,
    buildings: [], props: [], npcSpawns: [], discovered: false,
    id: 'settle_' + cx + '_' + cy,
  };
  const w = world.w;
  const R = def.size;

  // flatten & floor the town area
  let sum = 0, n = 0;
  for (let y = cy - R; y <= cy + R; y++) {
    for (let x = cx - R; x <= cx + R; x++) {
      if (!world.inBounds(x, y)) continue;
      if (dist(x, y, cx, cy) > R) continue;
      sum += world.height[y * w + x]; n++;
    }
  }
  const avg = sum / Math.max(1, n);
  for (let y = cy - R - 2; y <= cy + R + 2; y++) {
    for (let x = cx - R - 2; x <= cx + R + 2; x++) {
      if (!world.inBounds(x, y)) continue;
      const d = dist(x, y, cx, cy);
      if (d > R + 2) continue;
      const i = y * w + x;
      const t = clamp(1 - (d - R * 0.6) / (R * 0.5), 0, 1);
      world.height[i] = lerp(world.height[i], avg, t * 0.92);
      if (d <= R && !WATER.has(world.biome[i])) {
        world.overlay[i] = 4;
      }
    }
  }
  // central plaza
  const plazaR = Math.max(3, Math.floor(R * 0.26));
  for (let y = cy - plazaR; y <= cy + plazaR; y++) {
    for (let x = cx - plazaR; x <= cx + plazaR; x++) {
      if (!world.inBounds(x, y)) continue;
      if (dist(x, y, cx, cy) > plazaR) continue;
      world.overlay[y * w + x] = 6;
    }
  }

  // radial streets
  const streets = def.type === 'capital' ? 6 : def.type === 'town' ? 5 : 4;
  for (let k = 0; k < streets; k++) {
    const a = (k / streets) * Math.PI * 2 + rng() * 0.4;
    for (let d = plazaR - 1; d < R + 3; d += 0.5) {
      const x = Math.round(cx + Math.cos(a) * d);
      const y = Math.round(cy + Math.sin(a) * d);
      for (let oy = -1; oy <= 1; oy++) {
        for (let ox = -1; ox <= 1; ox++) {
          if (!world.inBounds(x + ox, y + oy)) continue;
          const i = (y + oy) * w + (x + ox);
          if (WATER.has(world.biome[i])) continue;
          world.overlay[i] = 1;
        }
      }
    }
    s.gates = s.gates || [];
    s.gates.push({ x: cx + Math.cos(a) * (R + 2), y: cy + Math.sin(a) * (R + 2) });
  }

  // buildings: ring placement, rejection sampled
  const buildingTypes = [];
  if (def.type === 'capital') {
    buildingTypes.push('castle', 'inn', 'shop', 'smith', 'alchemist', 'temple', 'guild');
  } else if (def.type === 'town') {
    buildingTypes.push('inn', 'shop', 'smith', 'alchemist', 'guild');
  } else if (def.type === 'village') {
    buildingTypes.push('inn', 'shop', 'smith');
  } else {
    buildingTypes.push('shop');
  }
  const houseCount = def.type === 'capital' ? 22 : def.type === 'town' ? 14 : def.type === 'village' ? 9 : 5;
  for (let i = 0; i < houseCount; i++) buildingTypes.push('house');

  const placed = [];
  for (const type of buildingTypes) {
    const big = type === 'castle' || type === 'temple';
    const bw = big ? rng.irange(8, 10) : type === 'house' ? rng.irange(4, 6) : rng.irange(5, 7);
    const bh = big ? rng.irange(7, 9) : type === 'house' ? rng.irange(4, 5) : rng.irange(4, 6);
    let ok = false;
    for (let attempt = 0; attempt < 200 && !ok; attempt++) {
      const a = rng() * Math.PI * 2;
      const rad = big ? rng.range(plazaR + 2, R * 0.55) : rng.range(plazaR + 2.5, R - 3);
      const bx = Math.round(cx + Math.cos(a) * rad - bw / 2);
      const by = Math.round(cy + Math.sin(a) * rad - bh / 2);
      // must be on land, inside town, not overlapping
      let valid = true;
      for (let y = by - 1; y <= by + bh + 1 && valid; y++) {
        for (let x = bx - 1; x <= bx + bw + 1 && valid; x++) {
          if (!world.inBounds(x, y)) valid = false;
          else if (WATER.has(world.biome[y * w + x])) valid = false;
          else if (dist(x, y, cx, cy) > R - 0.5) valid = false;
        }
      }
      if (!valid) continue;
      for (const p of placed) {
        if (bx < p.tx + p.tw + 2 && bx + bw + 2 > p.tx && by < p.ty + p.th + 2 && by + bh + 2 > p.ty) { valid = false; break; }
      }
      if (!valid) continue;
      // door faces the plaza
      const towards = Math.atan2(cy - (by + bh / 2), cx - (bx + bw / 2));
      const b = {
        type, tx: bx, ty: by, tw: bw, th: bh,
        x: (bx + bw / 2) * TILE, y: (by + bh / 2) * TILE,
        doorSide: Math.abs(Math.cos(towards)) > Math.abs(Math.sin(towards)) ? (Math.cos(towards) > 0 ? 'e' : 'w') : (Math.sin(towards) > 0 ? 's' : 'n'),
        style: rng.int(4), roofHue: rng.range(0, 1), id: s.id + '_b' + placed.length,
      };
      b.doorX = b.doorSide === 'e' ? bx + bw : b.doorSide === 'w' ? bx - 0.2 : bx + bw / 2;
      b.doorY = b.doorSide === 's' ? by + bh : b.doorSide === 'n' ? by - 0.2 : by + bh / 2;
      placed.push(b);
      s.buildings.push(b);
      ok = true;
    }
  }

  // clear floor under/around buildings and add a path to each door
  for (const b of s.buildings) {
    for (let y = b.ty - 1; y <= b.ty + b.th + 1; y++) {
      for (let x = b.tx - 1; x <= b.tx + b.tw + 1; x++) {
        if (!world.inBounds(x, y)) continue;
        const i = y * w + x;
        if (WATER.has(world.biome[i])) continue;
        if (world.overlay[i] !== 1) world.overlay[i] = 4;
      }
    }
    // path from door toward plaza
    let px = b.doorX, py = b.doorY;
    for (let k = 0; k < 40; k++) {
      const a = Math.atan2(cy - py, cx - px);
      px += Math.cos(a); py += Math.sin(a);
      const gx = Math.round(px), gy = Math.round(py);
      if (!world.inBounds(gx, gy)) break;
      if (dist(gx, gy, cx, cy) < plazaR) break;
      const i = gy * w + gx;
      if (!WATER.has(world.biome[i])) world.overlay[i] = 1;
    }
  }

  // farms outside the walls
  if (def.type !== 'hamlet') {
    const farmCount = def.type === 'capital' ? 7 : 4;
    for (let k = 0; k < farmCount; k++) {
      const a = rng() * Math.PI * 2;
      const rad = R + rng.range(4, 13);
      const fx = Math.round(cx + Math.cos(a) * rad);
      const fy = Math.round(cy + Math.sin(a) * rad);
      const fw = rng.irange(6, 11), fh = rng.irange(5, 9);
      let valid = true;
      for (let y = fy; y < fy + fh && valid; y++)
        for (let x = fx; x < fx + fw && valid; x++)
          if (!world.inBounds(x, y) || WATER.has(world.biome[y * w + x]) || world.biome[y * w + x] === B.ROCK) valid = false;
      if (!valid) continue;
      for (let y = fy; y < fy + fh; y++)
        for (let x = fx; x < fx + fw; x++) world.overlay[y * w + x] = 3;
    }
  }

  // decorations: wells, lamps, market stalls, benches, trees
  s.props.push({ kind: 'well', x: cx * TILE + TILE / 2, y: cy * TILE + TILE / 2 });
  for (let k = 0; k < streets * 2; k++) {
    const a = (k / (streets * 2)) * Math.PI * 2;
    const rad = (plazaR + 1.4) * TILE;
    s.props.push({ kind: 'lamp', x: (cx + 0.5) * TILE + Math.cos(a) * rad, y: (cy + 0.5) * TILE + Math.sin(a) * rad });
  }
  const stalls = def.type === 'capital' ? 6 : def.type === 'town' ? 4 : 2;
  for (let k = 0; k < stalls; k++) {
    const a = rng() * Math.PI * 2, rad = rng.range(1.2, plazaR - 0.6) * TILE;
    s.props.push({ kind: 'stall', x: (cx + 0.5) * TILE + Math.cos(a) * rad, y: (cy + 0.5) * TILE + Math.sin(a) * rad, hue: rng() });
  }
  for (let k = 0; k < R; k++) {
    const a = rng() * Math.PI * 2, rad = rng.range(plazaR + 2, R - 1) * TILE;
    const px = (cx + 0.5) * TILE + Math.cos(a) * rad, py = (cy + 0.5) * TILE + Math.sin(a) * rad;
    const tx = Math.floor(px / TILE), ty = Math.floor(py / TILE);
    if (!world.inBounds(tx, ty)) continue;
    if (world.overlay[ty * world.w + tx] !== 4) continue;
    let clear = true;
    for (const b of s.buildings) {
      if (tx >= b.tx - 1 && tx <= b.tx + b.tw && ty >= b.ty - 1 && ty <= b.ty + b.th) { clear = false; break; }
    }
    if (clear) s.props.push({ kind: rng.chance(0.7) ? 'townTree' : 'barrel', x: px, y: py });
  }
  return s;
}

// ————————————————————————————————————————————————————————— roads

function carveRoad(world, a, b) {
  // A* on a coarse grid with terrain costs, then paint a 3-wide road.
  const STEP = 3;
  const gw = Math.ceil(world.w / STEP), gh = Math.ceil(world.h / STEP);
  const sx = Math.floor(a.x / STEP), sy = Math.floor(a.y / STEP);
  const gx = Math.floor(b.x / STEP), gy = Math.floor(b.y / STEP);
  const cost = (x, y) => {
    const tx = x * STEP, ty = y * STEP;
    if (!world.inBounds(tx, ty)) return Infinity;
    const i = ty * world.w + tx;
    const bi = world.biome[i];
    if (bi === B.DEEP || bi === B.OCEAN) return Infinity;
    if (bi === B.PEAK) return 90;
    if (bi === B.ROCK) return 14;
    if (bi === B.RIVER) return 26; // bridge
    if (bi === B.SHALLOW) return 30;
    if (bi === B.SWAMP) return 9;
    if (bi === B.ASH) return 20;
    if (world.overlay[i] === 1) return 0.6;
    return 2.4;
  };
  const H = (x, y) => (Math.abs(x - gx) + Math.abs(y - gy)) * 2.4;
  const open = [{ x: sx, y: sy, g: 0, f: H(sx, sy) }];
  const gScore = new Map();
  const came = new Map();
  const key = (x, y) => y * gw + x;
  gScore.set(key(sx, sy), 0);
  let found = false, guard = 0;
  while (open.length && guard++ < 200000) {
    let bi2 = 0;
    for (let i = 1; i < open.length; i++) if (open[i].f < open[bi2].f) bi2 = i;
    const cur = open.splice(bi2, 1)[0];
    if (cur.x === gx && cur.y === gy) { found = true; break; }
    for (let oy = -1; oy <= 1; oy++) {
      for (let ox = -1; ox <= 1; ox++) {
        if (!ox && !oy) continue;
        const nx = cur.x + ox, ny = cur.y + oy;
        if (nx < 0 || ny < 0 || nx >= gw || ny >= gh) continue;
        const c = cost(nx, ny);
        if (!isFinite(c)) continue;
        // slope penalty keeps roads out of cliffs
        const h1 = world.heightAt(cur.x * STEP, cur.y * STEP);
        const h2 = world.heightAt(nx * STEP, ny * STEP);
        const slope = Math.abs(h2 - h1) * 190;
        const diag = ox && oy ? 1.42 : 1;
        const ng = cur.g + (c + slope) * diag;
        const k = key(nx, ny);
        if (ng < (gScore.get(k) ?? Infinity)) {
          gScore.set(k, ng);
          came.set(k, cur);
          open.push({ x: nx, y: ny, g: ng, f: ng + H(nx, ny) });
        }
      }
    }
  }
  if (!found) return;
  // rebuild path
  const path = [];
  let node = { x: gx, y: gy };
  let safety = 0;
  while (node && safety++ < 20000) {
    path.push([node.x * STEP, node.y * STEP]);
    node = came.get(key(node.x, node.y));
  }
  path.reverse();
  // smooth + paint
  for (let p = 0; p < path.length - 1; p++) {
    const [x1, y1] = path[p], [x2, y2] = path[p + 1];
    const steps = Math.ceil(Math.hypot(x2 - x1, y2 - y1));
    for (let s = 0; s <= steps; s++) {
      const t = s / Math.max(1, steps);
      const px = Math.round(lerp(x1, x2, t)), py = Math.round(lerp(y1, y2, t));
      for (let oy = -1; oy <= 1; oy++) {
        for (let ox = -1; ox <= 1; ox++) {
          const tx = px + ox, ty = py + oy;
          if (!world.inBounds(tx, ty)) continue;
          const i = ty * world.w + tx;
          const bi = world.biome[i];
          if (bi === B.DEEP || bi === B.OCEAN) continue;
          if (bi === B.RIVER || bi === B.SHALLOW) { world.overlay[i] = 5; continue; }
          if (world.overlay[i] === 4 || world.overlay[i] === 6) continue;
          if (bi === B.PEAK) world.biome[i] = B.ROCK;
          world.overlay[i] = 1;
        }
      }
    }
  }
  world.roadNodes.push(path);
}

// ————————————————————————————————————————————————————————— POIs

function placePOIs(world, rng) {
  const w = world.w, h = world.h;
  const far = (x, y, minD) => {
    for (const s of world.settlements) if (dist(x, y, s.x, s.y) < minD) return false;
    for (const p of world.pois) if (dist(x, y, p.tx, p.ty) < 26) return false;
    return true;
  };
  const add = (kind, tx, ty, extra = {}) => {
    world.pois.push({
      kind, tx, ty, x: (tx + 0.5) * TILE, y: (ty + 0.5) * TILE,
      discovered: false, cleared: false, id: kind + '_' + tx + '_' + ty, ...extra,
    });
  };

  // shrines — fast travel points, on hills & near roads
  let tries = 0;
  let shrines = 0;
  while (shrines < 12 && tries++ < 40000) {
    const x = rng.irange(20, w - 21), y = rng.irange(20, h - 21);
    const b = world.biome[y * w + x];
    if (WATER.has(b) || b === B.PEAK) continue;
    if (world.overlay[y * w + x] === 4 || world.overlay[y * w + x] === 6) continue;
    if (!far(x, y, 46)) continue;
    add('shrine', x, y, { name: rng.pick(NAMES_A) + 'の祠' });
    shrines++;
  }
  // dungeons — cave mouths in rocky terrain
  let dungeons = 0; tries = 0;
  const dungeonNames = ['忘れられた坑道', '朽ちた地下墓所', '氷結の洞', '砂中の遺構', '苔むす回廊', '深淵の巣', '嘆きの牢', '灰燼の炉'];
  while (dungeons < 8 && tries++ < 60000) {
    const x = rng.irange(20, w - 21), y = rng.irange(20, h - 21);
    const b = world.biome[y * w + x];
    if (b !== B.ROCK && b !== B.ASH && b !== B.SNOW && b !== B.DARKWOOD) continue;
    if (!far(x, y, 42)) continue;
    // must be reachable-ish: has a non-solid neighbour
    let open = 0;
    for (let oy = -2; oy <= 2; oy++) for (let ox = -2; ox <= 2; ox++)
      if (!world.isSolidTile(x + ox, y + oy)) open++;
    if (open < 8) continue;
    add('dungeon', x, y, {
      name: dungeonNames[dungeons % dungeonNames.length],
      level: 1 + dungeons * 2 + rng.int(3),
      floors: 2 + rng.int(2), seed: (rng() * 1e9) | 0,
    });
    dungeons++;
  }
  // bandit camps
  let camps = 0; tries = 0;
  while (camps < 16 && tries++ < 40000) {
    const x = rng.irange(20, w - 21), y = rng.irange(20, h - 21);
    const b = world.biome[y * w + x];
    if (WATER.has(b) || b === B.PEAK || b === B.ROCK) continue;
    if (!far(x, y, 40)) continue;
    add('camp', x, y, { level: 1 + rng.int(10), seed: (rng() * 1e9) | 0 });
    camps++;
  }
  // ruins & treasure
  let ruins = 0; tries = 0;
  while (ruins < 18 && tries++ < 40000) {
    const x = rng.irange(20, w - 21), y = rng.irange(20, h - 21);
    const b = world.biome[y * w + x];
    if (WATER.has(b) || b === B.PEAK) continue;
    if (!far(x, y, 34)) continue;
    add('ruin', x, y, { seed: (rng() * 1e9) | 0 });
    ruins++;
  }
  // the volcano lair (final dungeon)
  if (world.volcano) {
    let vx = world.volcano.x, vy = world.volcano.y;
    for (let r = 6; r < 30; r++) {
      let done = false;
      for (let a = 0; a < 32; a++) {
        const ang = (a / 32) * Math.PI * 2;
        const x = Math.round(vx + Math.cos(ang) * r), y = Math.round(vy + Math.sin(ang) * r);
        if (world.inBounds(x, y) && world.biome[y * w + x] === B.ASH) {
          add('lair', x, y, { name: '竜の火口', level: 22, floors: 1, boss: true, seed: 12345 });
          done = true; break;
        }
      }
      if (done) break;
    }
  }
}

function findStart(world) {
  const cap = world.settlements[0];
  if (cap) {
    return { x: (cap.x + 0.5) * TILE, y: (cap.y + cap.size * 0.45) * TILE };
  }
  return { x: (world.w / 2) * TILE, y: (world.h / 2) * TILE };
}
