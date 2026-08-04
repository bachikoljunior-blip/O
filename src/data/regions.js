// regions.js — the three culture regions, as a pure function of position.
// L1.
//
// Region membership is NEVER STORED. Six consumers read this one function —
// terrain palette, prop tables, settlement architecture, enemy spawn tables,
// music mood, faction territory — so "three cultures bleeding into each other"
// becomes automatic instead of an authored seam somebody has to hand-place.

import { fbm } from '../core/noise.js';
import { clamp01 } from '../core/math.js';

export const R = { ASH: 0, MIST: 1, RUST: 2 };
export const REGION_COUNT = 3;

export const REGION_NAME = ['灰王国', '霧ノ國', '銹の荒野'];

/** Colour identity per region: the player should know where they are instantly. */
export const REGION_PALETTE = [
  { // 灰王国 — dark fantasy: desaturated, cold, heavy
    grass: [0x7a, 0x86, 0x6e], rock: [0x8e, 0x8a, 0x86], soil: [0x66, 0x5a, 0x4c],
    sand: [0x6d, 0x66, 0x55], snow: [0xc8, 0xcc, 0xd4],
    fog: [0x39, 0x3f, 0x4a], sun: [0xc9, 0xba, 0xa4], ambient: [0x39, 0x40, 0x50],
    foliage: [0x5c, 0x6e, 0x54], accent: [0xd8, 0x8a, 0x4c],
  },
  { // 霧ノ國 — feudal Japan: green, humid, high contrast
    grass: [0x8c, 0xac, 0x62], rock: [0x9a, 0x94, 0x8a], soil: [0x76, 0x5e, 0x46],
    sand: [0x9a, 0x8c, 0x6e], snow: [0xe4, 0xe8, 0xee],
    fog: [0x8d, 0x9c, 0x9a], sun: [0xff, 0xe2, 0xba], ambient: [0x62, 0x76, 0x74],
    foliage: [0x6c, 0x9c, 0x54], accent: [0xe4, 0x5f, 0x6a],
  },
  { // 銹の荒野 — rusted machine wasteland: ochre, dry, metallic
    grass: [0xb6, 0xa8, 0x70], rock: [0xa8, 0x86, 0x64], soil: [0x96, 0x70, 0x50],
    sand: [0xb0, 0x96, 0x66], snow: [0xd8, 0xd2, 0xc4],
    fog: [0xa8, 0x8e, 0x6e], sun: [0xff, 0xd0, 0x92], ambient: [0x78, 0x68, 0x54],
    foliage: [0xa2, 0xa4, 0x70], accent: [0x78, 0xd0, 0xd8],
  },
];

/**
 * Continuous weights over the three cultures, summing to 1.
 * `out` is a Float32Array(3) the caller owns — no allocation per sample.
 *
 * Because the lobes are noise fields rather than a partition, the boundary is
 * a bleed zone hundreds of metres wide: ruins, enemies and music all mix
 * gradually rather than switching at a line.
 */
export function regionWeights(x, z, seed, out) {
  const a = fbm(x * 0.00021, z * 0.00021, seed + 8101, 3);
  const b = fbm(x * 0.00019 + 700, z * 0.00019 - 400, seed + 8102, 3);

  // Three lobes placed so no single culture dominates the map.
  const wAsh = clamp01(1.15 - Math.abs(a - 0.30) * 3.4);
  const wMist = clamp01(1.15 - Math.abs(a - 0.62) * 3.1 - Math.abs(b - 0.45) * 1.2);
  const wRust = clamp01(1.15 - Math.abs(b - 0.72) * 3.2);

  let sum = wAsh + wMist + wRust;
  if (sum < 0.0001) { out[0] = 1; out[1] = 0; out[2] = 0; return out; }
  const inv = 1 / sum;
  out[0] = wAsh * inv;
  out[1] = wMist * inv;
  out[2] = wRust * inv;
  return out;
}

/** Index of the dominant culture, for discrete choices (which enemy, which roof). */
export function dominantRegion(weights) {
  let best = 0;
  if (weights[1] > weights[best]) best = 1;
  if (weights[2] > weights[best]) best = 2;
  return best;
}

/** Blend a named palette entry across the active cultures. */
export function blendPalette(weights, key, out) {
  out[0] = out[1] = out[2] = 0;
  for (let r = 0; r < REGION_COUNT; r++) {
    const w = weights[r];
    if (w <= 0.001) continue;
    const c = REGION_PALETTE[r][key];
    out[0] += c[0] * w;
    out[1] += c[1] * w;
    out[2] += c[2] * w;
  }
  return out;
}
