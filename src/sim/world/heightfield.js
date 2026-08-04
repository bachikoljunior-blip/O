// heightfield.js — the continent's elevation, sampled at any scale.
// L2.
//
// Two layers. The MACRO field is the 2D build's generator, ported to fixed
// math: three-frequency fbm plus a ridged multifractal gated by a mountain
// mask, all multiplied by a radial continental mask so the coast is wiggly
// rather than circular. The DETAIL layer adds per-chunk octaves at mesh-build
// time, so a coarse macro grid still yields walkable ground.
//
// Nothing here is ever saved. The whole world is a pure function of the seed,
// which is what lets the delta save stay tiny and the world stay unbounded.

import { fbm, ridge, valueNoise2 } from '../../core/noise.js';
import { clamp, clamp01, lerp, smoothstep, invLerp } from '../../core/math.js';
import { flen } from '../../core/fixed.js';

/** One macro tile is 8 metres. 768 tiles -> a 6.1 km continent. */
export const TILE = 8;
export const WORLD_TILES = 768;
export const WORLD_SIZE = TILE * WORLD_TILES;   // 6144 m
export const SEA_LEVEL = 0.42;

/** Vertical scale: normalised height 0..1 maps to this many metres. */
export const HEIGHT_SCALE = 420;

const F1 = 1 / 170, F2 = 1 / 260, F3 = 1 / 90;

/** Macro elevation in [0,1] at TILE coordinates (not metres). */
export function macroHeight(tx, tz, seed) {
  const base = fbm(tx * F1, tz * F1, seed + 11, 5);
  const broad = fbm(tx * F2 + 400, tz * F2 - 220, seed + 29, 4);

  // Ranges only appear where the mask says so, which keeps mountains in
  // coherent chains instead of scattering peaks everywhere.
  const maskRaw = fbm(tx * F2 * 0.6, tz * F2 * 0.6, seed + 53, 3);
  const mountainMask = smoothstep(clamp01(invLerp(0.40, 0.70, maskRaw)));
  const rid = ridge(tx * F3, tz * F3, seed + 71, 4);

  let land = 0.52 + (base - 0.5) * 0.62 + rid * 0.54 * mountainMask + (broad - 0.5) * 0.10;

  // Continental mask: elliptical, perturbed twice so the coastline is ragged.
  const nx = (tx / WORLD_TILES) * 2 - 1;
  const nz = (tz / WORLD_TILES) * 2 - 1;
  let d = flen(nx * 1.025, nz * 1.086) * 2;
  d += (fbm(tx * 0.006, tz * 0.006, seed + 97, 3) - 0.5) * 0.55;
  d += (fbm(tx * 0.018, tz * 0.018, seed + 131, 2) - 0.5) * 0.22;
  const mask = 1 - smoothstep(clamp01(invLerp(0.74, 1.26, d)));

  return clamp(lerp(0.06, land, mask), 0, 1);
}

/** Moisture in [0,1]. Drives biome choice and foliage density. */
export function macroMoisture(tx, tz, seed, elevation) {
  const m = fbm(tx * 0.0075, tz * 0.0075, seed + 211, 4);
  const regional = fbm(tx * 0.0021, tz * 0.0021, seed + 233, 3);
  // Stretch around the mean so genuinely wet and dry extremes occur.
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
 * Bilinear macro sample plus detail octaves, in METRES.
 * This is the function the renderer, the collider and the AI all agree on;
 * if they ever disagree, characters sink into the ground.
 */
export function sampleHeight(x, z, seed) {
  const tx = x / TILE, tz = z / TILE;
  const x0 = Math.floor(tx), z0 = Math.floor(tz);
  const fx = tx - x0, fz = tz - z0;

  const h00 = macroHeight(x0, z0, seed);
  const h10 = macroHeight(x0 + 1, z0, seed);
  const h01 = macroHeight(x0, z0 + 1, seed);
  const h11 = macroHeight(x0 + 1, z0 + 1, seed);

  const u = smoothstep(fx), v = smoothstep(fz);
  let h = lerp(lerp(h00, h10, u), lerp(h01, h11, u), v);

  // Detail is suppressed under water and on flat lowland so beaches stay
  // readable and the sea stays flat.
  //
  // The frequencies matter more than they look. The macro field was authored
  // when one tile was 32 metres; at 8 metres its features span well over a
  // kilometre, which reads as a putting green from eye height. These four
  // octaves put relief back at the scales a walking player actually perceives:
  // roughly 220 m, 55 m, 17 m and 5 m.
  const above = Math.max(0, h - SEA_LEVEL);
  const amp = Math.min(1, above * 4);
  if (amp > 0.001) {
    const d0 = fbm(x * 0.0045, z * 0.0045, seed + 397, 3) - 0.5;
    const d1 = valueNoise2(x * 0.018, z * 0.018, seed + 401) - 0.5;
    const d2 = valueNoise2(x * 0.060, z * 0.060, seed + 409) - 0.5;
    const d3 = valueNoise2(x * 0.180, z * 0.180, seed + 419) - 0.5;
    h += (d0 * 0.100 + d1 * 0.034 + d2 * 0.011 + d3 * 0.0035) * amp;
  }

  return Math.max(SEA_LEVEL * HEIGHT_SCALE - 12, h * HEIGHT_SCALE);
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
 * The terrain interface the rest of the simulation sees. Installed on the
 * world at boot; everything else calls heightAt() and never knows about noise.
 */
export function createTerrain(seed) {
  return {
    seed,
    size: WORLD_SIZE,
    seaLevel: seaLevelMetres(),
    heightAt: (x, z) => sampleHeight(x, z, seed),
    normalAt: (x, z, out) => sampleNormal(x, z, seed, out),
    isWater: (x, z) => sampleHeight(x, z, seed) <= seaLevelMetres() + 0.05,
  };
}
