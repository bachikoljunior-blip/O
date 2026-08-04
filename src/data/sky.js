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

/** Weather presets. Fog density is what sells depth on a small screen. */
export const WEATHER = {
  clear: { fog: 0.00018, fogBoost: [1, 1, 1], particles: 0, wind: 0.4, sunMul: 1.0 },
  cloud: { fog: 0.00042, fogBoost: [0.94, 0.95, 1.0], particles: 0, wind: 0.7, sunMul: 0.62 },
  rain:  { fog: 0.00085, fogBoost: [0.78, 0.84, 0.92], particles: 1, wind: 1.1, sunMul: 0.38 },
  fog:   { fog: 0.00220, fogBoost: [1.02, 1.02, 1.04], particles: 0, wind: 0.25, sunMul: 0.45 },
  snow:  { fog: 0.00110, fogBoost: [1.05, 1.06, 1.10], particles: 2, wind: 0.8, sunMul: 0.55 },
  ash:   { fog: 0.00140, fogBoost: [0.92, 0.82, 0.72], particles: 3, wind: 0.6, sunMul: 0.42 },
};
