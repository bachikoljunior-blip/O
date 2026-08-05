// sky.js — time-of-day colour curve.
// L1.
//
// Ten keyframes with smoothstep interpolation, carried over from the 2D build
// where they were tuned by eye. In 3D they drive the sun colour, ambient,
// fog and the sky dome gradient, so one table controls the whole mood.

import { lerp, smoothstep, clamp } from '../core/math.js';

export const SKY = [
  { t: 0,    zenith: [0x10, 0x16, 0x2e], horizon: [0x1a, 0x20, 0x3a], sun: [0x28, 0x3c, 0x6e], amb: [0x1a, 0x22, 0x3e], dark: 0.72, sunI: 0.05 },
  { t: 5,    zenith: [0x24, 0x2c, 0x52], horizon: [0x46, 0x42, 0x60], sun: [0x78, 0x64, 0x8c], amb: [0x3a, 0x3c, 0x60], dark: 0.55, sunI: 0.15 },
  { t: 6.5,  zenith: [0x4c, 0x68, 0xa6], horizon: [0xe6, 0xa0, 0x82], sun: [0xff, 0xbe, 0x8c], amb: [0xa6, 0x84, 0x7c], dark: 0.22, sunI: 0.75 },
  { t: 9,    zenith: [0x5a, 0x88, 0xc8], horizon: [0xba, 0xcd, 0xe0], sun: [0xff, 0xfa, 0xeb], amb: [0xa8, 0xb4, 0xc4], dark: 0.02, sunI: 1.15 },
  { t: 13,   zenith: [0x4e, 0x84, 0xd0], horizon: [0xc4, 0xd8, 0xe8], sun: [0xff, 0xff, 0xfa], amb: [0xb4, 0xc0, 0xcc], dark: 0.00, sunI: 1.30 },
  { t: 17,   zenith: [0x54, 0x82, 0xc0], horizon: [0xd8, 0xcc, 0xb0], sun: [0xff, 0xe2, 0xb4], amb: [0xb8, 0xae, 0x9c], dark: 0.05, sunI: 1.05 },
  { t: 19,   zenith: [0x3e, 0x50, 0x8c], horizon: [0xf4, 0x96, 0x6e], sun: [0xff, 0xaa, 0x6e], amb: [0x9c, 0x76, 0x6c], dark: 0.26, sunI: 0.62 },
  { t: 20.5, zenith: [0x24, 0x2c, 0x5a], horizon: [0x78, 0x58, 0x78], sun: [0x8c, 0x64, 0x96], amb: [0x54, 0x48, 0x64], dark: 0.48, sunI: 0.24 },
  { t: 22,   zenith: [0x14, 0x1a, 0x38], horizon: [0x28, 0x2e, 0x54], sun: [0x3c, 0x46, 0x78], amb: [0x28, 0x2e, 0x54], dark: 0.66, sunI: 0.08 },
  { t: 24,   zenith: [0x10, 0x16, 0x2e], horizon: [0x1a, 0x20, 0x3a], sun: [0x28, 0x3c, 0x6e], amb: [0x1a, 0x22, 0x3e], dark: 0.72, sunI: 0.05 },
];

const mix3 = (a, b, t, out) => {
  out[0] = lerp(a[0], b[0], t) / 255;
  out[1] = lerp(a[1], b[1], t) / 255;
  out[2] = lerp(a[2], b[2], t) / 255;
  return out;
};

/** Interpolated sky state for an hour in [0,24). Writes into `out`, no alloc. */
export function skyAt(hour, out) {
  const h = ((hour % 24) + 24) % 24;
  let a = SKY[0], b = SKY[SKY.length - 1];
  for (let i = 0; i < SKY.length - 1; i++) {
    if (h >= SKY[i].t && h <= SKY[i + 1].t) { a = SKY[i]; b = SKY[i + 1]; break; }
  }
  const k = clamp((h - a.t) / Math.max(0.001, b.t - a.t), 0, 1);
  const s = smoothstep(k);
  mix3(a.zenith, b.zenith, s, out.zenith);
  mix3(a.horizon, b.horizon, s, out.horizon);
  mix3(a.sun, b.sun, s, out.sun);
  mix3(a.amb, b.amb, s, out.amb);
  out.dark = lerp(a.dark, b.dark, s);
  out.sunI = lerp(a.sunI, b.sunI, s);
  // Sun elevation: below the horizon at night so shadows fade out honestly.
  out.sunElev = Math.sin(((h - 6) / 12) * Math.PI);
  out.sunAzim = ((h / 24) * Math.PI * 2) - Math.PI * 0.5;
  return out;
}

export function createSkyState() {
  return {
    zenith: new Float32Array(3), horizon: new Float32Array(3),
    sun: new Float32Array(3), amb: new Float32Array(3),
    dark: 0, sunI: 1, sunElev: 0.5, sunAzim: 0,
  };
}

/**
 * Weather presets. Fog density is what sells depth on a small screen.
 *
 *   fogH     reciprocal scale height of the fog slab, in 1/m. Bigger means the
 *            haze hugs the valleys and the peaks come out of it clean.
 *   scatter  how much the fog picks up the sun's colour when you look toward
 *            it. This is the term that makes distance read as distance.
 *   cover    cloud-noise threshold. LOWER means MORE cloud.
 *   shadow   how deep the cloud shadows cut into the direct sun on the ground.
 *   dome     opacity of the cloud layer painted on the sky dome.
 */
export const WEATHER = {
  clear: { fog: 0.00018, fogBoost: [1, 1, 1], particles: 0, wind: 0.4, sunMul: 1.0,
           fogH: 0.0090, scatter: 0.85, cover: 0.62, shadow: 0.30, dome: 0.55 },
  cloud: { fog: 0.00042, fogBoost: [0.94, 0.95, 1.0], particles: 0, wind: 0.7, sunMul: 0.62,
           fogH: 0.0075, scatter: 0.55, cover: 0.40, shadow: 0.52, dome: 0.90 },
  rain:  { fog: 0.00085, fogBoost: [0.78, 0.84, 0.92], particles: 1, wind: 1.1, sunMul: 0.38,
           fogH: 0.0050, scatter: 0.30, cover: 0.24, shadow: 0.60, dome: 1.00 },
  fog:   { fog: 0.00220, fogBoost: [1.02, 1.02, 1.04], particles: 0, wind: 0.25, sunMul: 0.45,
           fogH: 0.0180, scatter: 0.65, cover: 0.34, shadow: 0.34, dome: 0.95 },
  snow:  { fog: 0.00110, fogBoost: [1.05, 1.06, 1.10], particles: 2, wind: 0.8, sunMul: 0.55,
           fogH: 0.0110, scatter: 0.45, cover: 0.30, shadow: 0.42, dome: 0.95 },
  ash:   { fog: 0.00140, fogBoost: [0.92, 0.82, 0.72], particles: 3, wind: 0.6, sunMul: 0.42,
           fogH: 0.0130, scatter: 0.70, cover: 0.36, shadow: 0.48, dome: 0.92 },
};

// ————————————————————————————————— colour grade

/**
 * Time-of-day grade, applied per fragment AFTER the ACES tone map.
 *
 * A post-process pass is not available to us (a full-resolution dependent read
 * is bandwidth murder on a tile GPU), so the grade rides along inside every
 * forward material instead. It is the cheapest possible version of one:
 *
 *   out = pow( c * gain + lift * (1 - c), 1 / gamma )
 *
 *   lift   added to the shadows only — this is where the BLUE goes at dawn and
 *          at night, and it is what stops "dark" from meaning "grey".
 *   gamma  midtone bend; >1 opens the midtones up.
 *   gain   multiplies the highlights — where the warmth of a low sun goes.
 *
 * The hours line up with SKY above so the two tables are read as one mood.
 */
export const GRADE = [
  { t: 0,    lift: [0.010, 0.019, 0.046], gamma: [0.92, 0.95, 1.07], gain: [0.72, 0.80, 1.02] },
  { t: 5,    lift: [0.012, 0.021, 0.050], gamma: [0.94, 0.97, 1.09], gain: [0.80, 0.87, 1.07] },
  { t: 6.5,  lift: [0.004, 0.013, 0.039], gamma: [1.00, 1.00, 1.03], gain: [1.09, 1.01, 0.92] },
  { t: 9,    lift: [0.002, 0.006, 0.021], gamma: [1.02, 1.01, 1.00], gain: [1.04, 1.01, 0.98] },
  { t: 13,   lift: [0.000, 0.002, 0.011], gamma: [1.00, 1.00, 1.00], gain: [1.00, 1.00, 1.00] },
  { t: 17,   lift: [0.006, 0.005, 0.015], gamma: [1.02, 1.00, 0.98], gain: [1.05, 1.00, 0.95] },
  { t: 19,   lift: [0.017, 0.009, 0.021], gamma: [1.05, 1.00, 0.94], gain: [1.13, 0.99, 0.87] },
  { t: 20.5, lift: [0.018, 0.014, 0.041], gamma: [1.00, 0.99, 1.03], gain: [0.94, 0.90, 1.00] },
  { t: 22,   lift: [0.012, 0.019, 0.047], gamma: [0.94, 0.96, 1.06], gain: [0.76, 0.83, 1.03] },
  { t: 24,   lift: [0.010, 0.019, 0.046], gamma: [0.92, 0.95, 1.07], gain: [0.72, 0.80, 1.02] },
];

export function createGradeState() {
  return {
    lift: new Float32Array(3),
    invGamma: new Float32Array(3),
    gain: new Float32Array(3),
  };
}

/**
 * Interpolated grade for an hour. Gamma is returned already RECIPROCATED so
 * the shader can hand it straight to pow() without a divide per fragment.
 */
export function gradeAt(hour, out) {
  const h = ((hour % 24) + 24) % 24;
  let a = GRADE[0], b = GRADE[GRADE.length - 1];
  for (let i = 0; i < GRADE.length - 1; i++) {
    if (h >= GRADE[i].t && h <= GRADE[i + 1].t) { a = GRADE[i]; b = GRADE[i + 1]; break; }
  }
  const s = smoothstep(clamp((h - a.t) / Math.max(0.001, b.t - a.t), 0, 1));
  for (let i = 0; i < 3; i++) {
    out.lift[i] = lerp(a.lift[i], b.lift[i], s);
    out.gain[i] = lerp(a.gain[i], b.gain[i], s);
    out.invGamma[i] = 1 / Math.max(0.05, lerp(a.gamma[i], b.gamma[i], s));
  }
  return out;
}
