// runtime.js — streaming chunk content: props, resource nodes, terrain bakes,
// plus the enemy spawn director.

import { B, TILE, WATER } from './worldgen.js';
import { CHUNK, bakeChunk } from '../render/tiles.js';
import { PROP_COLLIDE, PROP_SWAY } from '../render/props.js';
import { hash2, clamp, dist, makeRNG, lerp } from '../core/util.js';

const PROP_TABLES = {
  [B.GRASS]: [['oak', 0.010], ['bush', 0.022], ['flowers', 0.05], ['rock', 0.012], ['herb', 0.009], ['stump', 0.004]],
  [B.MEADOW]: [['flowers', 0.14], ['oak', 0.008], ['bush', 0.032], ['herb', 0.018], ['birch', 0.006]],
  [B.FOREST]: [['oak', 0.055], ['birch', 0.028], ['bush', 0.055], ['mushroom', 0.022], ['stump', 0.010], ['herb', 0.014], ['rock', 0.008]],
  [B.DARKWOOD]: [['pine', 0.065], ['deadTree', 0.030], ['mushroom', 0.05], ['bush', 0.05], ['rock', 0.012], ['herb', 0.009]],
  [B.TAIGA]: [['pine', 0.060], ['snowRock', 0.02], ['bush', 0.03], ['stump', 0.008], ['herb', 0.006]],
  [B.SNOW]: [['pine', 0.028], ['snowRock', 0.045], ['deadTree', 0.010]],
  [B.TUNDRA]: [['rock', 0.045], ['bush', 0.028], ['deadTree', 0.008], ['herb', 0.005]],
  [B.DESERT]: [['cactus', 0.022], ['rock', 0.020], ['deadTree', 0.005]],
  [B.SAVANNA]: [['oak', 0.012], ['deadTree', 0.010], ['rock', 0.016], ['bush', 0.028], ['herb', 0.005]],
  [B.SWAMP]: [['reeds', 0.11], ['deadTree', 0.035], ['mushroom', 0.045], ['bush', 0.03], ['herb', 0.013]],
  [B.BEACH]: [['palm', 0.018], ['rock', 0.010], ['reeds', 0.014]],
  [B.ROCK]: [['rock', 0.085], ['crystal', 0.008], ['ore', 0.013], ['deadTree', 0.005]],
  [B.PEAK]: [['snowRock', 0.06]],
  [B.ASH]: [['ashRock', 0.07], ['deadTree', 0.022], ['crystal', 0.005], ['ore', 0.007]],
  [B.SHALLOW]: [['reeds', 0.035]],
  [B.RIVER]: [['reeds', 0.02]],
};

const HARVEST = { herb: 'herb', ore: 'ore', bush: 'berry', mushroom: 'shroom', crystal: 'crystal' };

export class WorldRuntime {
  constructor(world) {
    this.world = world;
    this.bakes = new Map();       // "cx,cy" -> canvas
    this.props = new Map();       // "cx,cy" -> array
    this.bakeOrder = [];
    this.harvested = new Map();   // propId -> respawnAtGameTime
    this.maxBakes = 220;
    this.settlementProps = new Map();
    for (const s of world.settlements) {
      for (const p of s.props) {
        const cx = Math.floor(p.x / (CHUNK * TILE)), cy = Math.floor(p.y / (CHUNK * TILE));
        const k = cx + ',' + cy;
        if (!this.settlementProps.has(k)) this.settlementProps.set(k, []);
        this.settlementProps.get(k).push({
          kind: p.kind, variant: (p.hue ? Math.floor(p.hue * 8) : Math.floor(hash2(p.x | 0, p.y | 0, 7) * 6)),
          x: p.x, y: p.y, r: PROP_COLLIDE[p.kind] ?? 0, sway: PROP_SWAY.has(p.kind),
          id: 'sp' + (p.x | 0) + '_' + (p.y | 0), fixed: true,
        });
      }
    }
    for (const poi of world.pois) {
      const kind = poi.kind === 'shrine' ? 'shrine' : (poi.kind === 'dungeon' || poi.kind === 'lair') ? 'caveMouth' : null;
      if (!kind) continue;
      const cx = Math.floor(poi.x / (CHUNK * TILE)), cy = Math.floor(poi.y / (CHUNK * TILE));
      const k = cx + ',' + cy;
      if (!this.settlementProps.has(k)) this.settlementProps.set(k, []);
      this.settlementProps.get(k).push({
        kind, variant: 0, x: poi.x, y: poi.y, r: 0, sway: false, id: poi.id, poi, fixed: true,
      });
    }
  }

  getBake(cx, cy) {
    const k = cx + ',' + cy;
    let c = this.bakes.get(k);
    if (!c) {
      c = bakeChunk(this.world, cx, cy);
      this.bakes.set(k, c);
      this.bakeOrder.push(k);
      if (this.bakeOrder.length > this.maxBakes) {
        const old = this.bakeOrder.shift();
        this.bakes.delete(old);
      }
    }
    return c;
  }

  getProps(cx, cy) {
    const k = cx + ',' + cy;
    let list = this.props.get(k);
    if (list) return list;
    list = [];
    const w = this.world;
    const seed = w.seed;
    for (let ty = cy * CHUNK; ty < (cy + 1) * CHUNK; ty++) {
      for (let tx = cx * CHUNK; tx < (cx + 1) * CHUNK; tx++) {
        if (!w.inBounds(tx, ty)) continue;
        const i = ty * w.w + tx;
        if (w.overlay[i]) continue;             // roads/town/farm stay clear
        const b = w.biome[i];
        const table = PROP_TABLES[b];
        if (!table) continue;
        const r = hash2(tx, ty, seed + 991);
        let acc = 0;
        for (const [kind, dens] of table) {
          acc += dens;
          if (r < acc) {
            const jx = hash2(tx, ty, seed + 17), jy = hash2(tx, ty, seed + 23);
            const x = (tx + 0.15 + jx * 0.7) * TILE;
            const y = (ty + 0.15 + jy * 0.7) * TILE;
            list.push({
              kind, variant: Math.floor(hash2(tx, ty, seed + 41) * 8),
              x, y, r: PROP_COLLIDE[kind] ?? 0, sway: PROP_SWAY.has(kind),
              id: tx + '_' + ty, harvest: HARVEST[kind] || null,
              phase: jx * 6.28,
            });
            break;
          }
        }
      }
    }
    const extra = this.settlementProps.get(k);
    if (extra) list = list.concat(extra);
    list.sort((a, b2) => a.y - b2.y);
    this.props.set(k, list);
    if (this.props.size > 400) {
      const firstKey = this.props.keys().next().value;
      this.props.delete(firstKey);
    }
    return list;
  }

  /** Props overlapping a circle — used for collision and interaction. */
  forEachPropNear(x, y, rad, fn) {
    const c0x = Math.floor((x - rad) / (CHUNK * TILE)), c1x = Math.floor((x + rad) / (CHUNK * TILE));
    const c0y = Math.floor((y - rad) / (CHUNK * TILE)), c1y = Math.floor((y + rad) / (CHUNK * TILE));
    for (let cy = c0y; cy <= c1y; cy++) {
      for (let cx = c0x; cx <= c1x; cx++) {
        const list = this.getProps(cx, cy);
        for (const p of list) {
          const dx = p.x - x, dy = p.y - y;
          if (dx * dx + dy * dy <= (rad + 24) * (rad + 24)) fn(p);
        }
      }
    }
  }

  isHarvested(p, now) {
    const t = this.harvested.get(p.id);
    return t != null && t > now;
  }
  harvest(p, now) {
    this.harvested.set(p.id, now + 240);
  }
}

// ————————————————————————————————————————————————— danger & spawn tables

export function regionLevel(world, x, y) {
  const cap = world.settlements[0];
  const tx = x / TILE, ty = y / TILE;
  const d = cap ? dist(tx, ty, cap.x, cap.y) : 200;
  let lvl = 1 + d / 34;
  const b = world.biomeAt(tx | 0, ty | 0);
  if (b === B.ASH) lvl += 9;
  else if (b === B.PEAK || b === B.ROCK) lvl += 4;
  else if (b === B.DARKWOOD) lvl += 3;
  else if (b === B.SNOW || b === B.TAIGA) lvl += 3;
  else if (b === B.SWAMP) lvl += 2;
  else if (b === B.DESERT) lvl += 2;
  return clamp(Math.round(lvl), 1, 30);
}

const BIOME_SPAWNS = {
  [B.GRASS]: ['slime', 'wolf', 'boar', 'bandit'],
  [B.MEADOW]: ['slime', 'boar', 'wolf'],
  [B.FOREST]: ['wolf', 'spider', 'boar', 'bandit'],
  [B.DARKWOOD]: ['spider', 'ghost', 'wraith', 'wolf'],
  [B.TAIGA]: ['wolf', 'bandit', 'golem'],
  [B.SNOW]: ['wolf', 'golem', 'wraith'],
  [B.TUNDRA]: ['wolf', 'bandit', 'skeleton'],
  [B.DESERT]: ['skeleton', 'spider', 'bandit', 'drake'],
  [B.SAVANNA]: ['boar', 'wolf', 'bandit', 'archer'],
  [B.SWAMP]: ['slime', 'spider', 'ghost', 'skeleton'],
  [B.ROCK]: ['golem', 'bat', 'drake', 'troll'],
  [B.PEAK]: ['drake', 'golem'],
  [B.ASH]: ['imp', 'drake', 'wraith', 'troll'],
  [B.BEACH]: ['slime', 'spider'],
};

const NIGHT_EXTRA = ['skeleton', 'ghost', 'wraith', 'bat'];

export function pickSpawnKind(world, tx, ty, night, rng, level) {
  const b = world.biomeAt(tx, ty);
  let pool = BIOME_SPAWNS[b];
  if (!pool) return null;
  pool = pool.slice();
  if (night) pool = pool.concat(NIGHT_EXTRA);
  // filter by level appropriateness
  const tiers = { slime: 1, bat: 1, wolf: 2, boar: 3, spider: 3, bandit: 4, archer: 5, skeleton: 6, ghost: 7, imp: 9, golem: 11, wraith: 13, drake: 15, troll: 18 };
  const ok = pool.filter((k) => (tiers[k] || 1) <= level + 4);
  if (!ok.length) return pool[0];
  return rng.pick(ok);
}
