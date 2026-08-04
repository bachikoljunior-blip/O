// heightfield.js — sample the continent's elevation at any scale.
// L2.
//
// Three layers, in order:
//   MACRO   — the eroded 768x768 field from macrofield.js (built once per seed)
//   TERRACE — stratification applied where the ground is steep and high, which
//             is what actually produces cliffs and mesas
//   DETAIL  — per-sample octaves at the scales a walking player perceives
//
// This is the ONE function the renderer, the collider and the AI all agree on.
// If they ever disagree, characters sink into the ground — so nothing may
// shortcut it.

import { valueNoise2, fbm } from '../../core/noise.js';
import { clamp, clamp01, lerp, smoothstep, invLerp } from '../../core/math.js';
import { buildMacroField, buildMacroFieldSync, WORLD_TILES, SEA_LEVEL } from './macrofield.js';

export { WORLD_TILES, SEA_LEVEL, buildMacroField };

/** One macro tile is 8 metres. 768 tiles -> a 6.1 km continent. */
export const TILE = 8;
export const WORLD_SIZE = TILE * WORLD_TILES;   // 6144 m
/** Normalised height 0..1 maps to this many metres. */
export const HEIGHT_SCALE = 460;

/** Terracing: how many strata, and where they bite. */
const TERRACE_STEPS = 26;
const TERRACE_MIN_SLOPE = 0.020;   // below this the ground stays smooth
const TERRACE_MAX_SLOPE = 0.075;

/**
 * The installed macro field. Module-level so `sampleHeight(x, z, seed)` keeps
 * a signature the renderer can call a hundred thousand times per chunk without
 * threading a handle through every layer.
 */
let FIELD = null;
let FIELD_SEED = -1;

export function installMacroField(field, seed) {
  FIELD = field;
  FIELD_SEED = seed;
}

/** Lazily build if nobody installed one (tests, tools). */
function field(seed) {
  if (FIELD && FIELD_SEED === seed) return FIELD;
  FIELD = buildMacroFieldSync(seed);
  FIELD_SEED = seed;
  return FIELD;
}

/** Raw macro elevation at integer tile coordinates, clamped at the edges. */
export function macroHeight(tx, tz, seed) {
  const f = field(seed);
  const x = tx < 0 ? 0 : tx >= f.w ? f.w - 1 : tx | 0;
  const z = tz < 0 ? 0 : tz >= f.w ? f.w - 1 : tz | 0;
  return f.h[z * f.w + x];
}

export function isRiverTile(tx, tz, seed) {
  const f = field(seed);
  if (tx < 0 || tz < 0 || tx >= f.w || tz >= f.w) return false;
  return f.water[(tz | 0) * f.w + (tx | 0)] === 1;
}

/** Moisture in [0,1]. Drives biome choice and foliage density. */
export function macroMoisture(tx, tz, seed, elevation) {
  const m = fbm(tx * 0.0075, tz * 0.0075, seed + 211, 4);
  const regional = fbm(tx * 0.0021, tz * 0.0021, seed + 233, 3);
  let v = 0.5 + (m - 0.5) * 2.35 * 0.5 + (regional - 0.5) * 0.4;
  v -= Math.max(0, elevation - SEA_LEVEL) * 0.30;   // crude rain shadow
  return clamp01(v);
}

/** Temperature in [0,1]: latitude band, regional noise, and a lapse rate. */
export function macroTemperature(tx, tz, seed, elevation) {
  const lat = 1 - Math.abs(tz / WORLD_TILES - 0.5) * 1.55;
  const regional = (fbm(tx * 0.0031, tz * 0.0031, seed + 307, 3) - 0.5) * 0.30;
  return clamp01(lat + regional - Math.max(0, elevation - SEA_LEVEL) * 1.05);
}

/**
 * Elevation in METRES at world coordinates.
 *
 * The terracing term is the single highest-value line in this file. Quantising
 * height into strata WHERE THE SLOPE IS ALREADY STEEP converts a smooth
 * gradient into a stack of flat benches separated by near-vertical risers —
 * i.e. cliffs and mesas — for the cost of a round(). Doing it only on steep
 * ground is what stops the gentle country turning into a wedding cake.
 */
export function sampleHeight(x, z, seed) {
  const f = field(seed);
  const w = f.w;
  const tx = x / TILE, tz = z / TILE;

  let x0 = Math.floor(tx), z0 = Math.floor(tz);
  if (x0 < 0) x0 = 0; else if (x0 > w - 2) x0 = w - 2;
  if (z0 < 0) z0 = 0; else if (z0 > w - 2) z0 = w - 2;
  const fx = clamp01(tx - x0), fz = clamp01(tz - z0);

  const i00 = z0 * w + x0;
  const h00 = f.h[i00], h10 = f.h[i00 + 1];
  const h01 = f.h[i00 + w], h11 = f.h[i00 + w + 1];

  const u = smoothstep(fx), v = smoothstep(fz);
  let h = lerp(lerp(h00, h10, u), lerp(h01, h11, u), v);

  // Local gradient straight out of the four corners we already fetched.
  const slope = Math.abs(h10 - h00) + Math.abs(h01 - h00) + Math.abs(h11 - h00);

  const above = h - SEA_LEVEL;
  if (above > 0.015) {
    // —— terracing / cliffs ——
    const steep = smoothstep(clamp01(invLerp(TERRACE_MIN_SLOPE, TERRACE_MAX_SLOPE, slope)));
    if (steep > 0.001) {
      // Strata drift with position so the benches are not globally aligned,
      // which would read as a rendering artefact rather than as geology.
      const drift = valueNoise2(x * 0.0016, z * 0.0016, seed + 733) * 0.6;
      const q = (Math.round((h + drift) * TERRACE_STEPS) / TERRACE_STEPS) - drift;
      h = lerp(h, q, steep * 0.80);
    }

    // —— detail ——
    // Roughly 220 m, 55 m, 17 m and 5 m features: the scales a walking player
    // actually perceives. The macro field cannot supply these — at 8 m per
    // tile its own features span hundreds of metres.
    const amp = Math.min(1, above * 4);
    const d0 = fbm(x * 0.0045, z * 0.0045, seed + 397, 3) - 0.5;
    const d1 = valueNoise2(x * 0.018, z * 0.018, seed + 401) - 0.5;
    const d2 = valueNoise2(x * 0.060, z * 0.060, seed + 409) - 0.5;
    const d3 = valueNoise2(x * 0.180, z * 0.180, seed + 419) - 0.5;
    // Detail is suppressed on cliff faces so the strata stay crisp.
    const smoothFace = 1 - steep * 0.55;
    h += (d0 * 0.070 + d1 * 0.026 + d2 * 0.009 + d3 * 0.0030) * amp * smoothFace;
  }

  return Math.max(SEA_LEVEL * HEIGHT_SCALE - 14, h * HEIGHT_SCALE);
}

export const seaLevelMetres = () => SEA_LEVEL * HEIGHT_SCALE;

/** Surface normal by central difference. Used for slope limits and shading. */
export function sampleNormal(x, z, seed, out, eps = 1.5) {
  const hL = sampleHeight(x - eps, z, seed);
  const hR = sampleHeight(x + eps, z, seed);
  const hD = sampleHeight(x, z - eps, seed);
  const hU = sampleHeight(x, z + eps, seed);
  const nx = hL - hR;
  const nz = hD - hU;
  const ny = 2 * eps;
  const len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
  out[0] = nx / len; out[1] = ny / len; out[2] = nz / len;
  return out;
}

/**
 * The terrain interface the rest of the simulation sees. Everything else calls
 * heightAt() and never learns that noise exists.
 */
export function createTerrain(seed) {
  field(seed);
  return {
    seed,
    size: WORLD_SIZE,
    seaLevel: seaLevelMetres(),
    heightAt: (x, z) => sampleHeight(x, z, seed),
    normalAt: (x, z, out) => sampleNormal(x, z, seed, out),
    isWater: (x, z) => sampleHeight(x, z, seed) <= seaLevelMetres() + 0.05,
    isRiver: (x, z) => isRiverTile(x / TILE, z / TILE, seed),
  };
}
