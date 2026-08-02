// util.js — math, RNG, noise, small helpers. No dependencies.

export const TAU = Math.PI * 2;
export const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
export const lerp = (a, b, t) => a + (b - a) * t;
export const invLerp = (a, b, v) => (b - a === 0 ? 0 : (v - a) / (b - a));
export const smoothstep = (t) => t * t * (3 - 2 * t);
export const smootherstep = (t) => t * t * t * (t * (t * 6 - 15) + 10);
export const dist2 = (ax, ay, bx, by) => {
  const dx = bx - ax, dy = by - ay;
  return dx * dx + dy * dy;
};
export const dist = (ax, ay, bx, by) => Math.sqrt(dist2(ax, ay, bx, by));
export const angleTo = (ax, ay, bx, by) => Math.atan2(by - ay, bx - ax);
export const angleDiff = (a, b) => {
  let d = (b - a) % TAU;
  if (d > Math.PI) d -= TAU;
  if (d < -Math.PI) d += TAU;
  return d;
};
export const approach = (v, target, step) =>
  v < target ? Math.min(v + step, target) : Math.max(v - step, target);
export const sign = Math.sign;
export const wrap = (v, n) => ((v % n) + n) % n;

/** Convert a direction vector into one of 4 facing indices: 0=down 1=left 2=right 3=up */
export function dir4(dx, dy) {
  if (Math.abs(dx) > Math.abs(dy)) return dx < 0 ? 1 : 2;
  return dy < 0 ? 3 : 0;
}

/** Mulberry32 — small, fast, deterministic PRNG. */
export function makeRNG(seed) {
  let a = seed >>> 0;
  const rnd = () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  rnd.int = (n) => Math.floor(rnd() * n);
  rnd.range = (lo, hi) => lo + rnd() * (hi - lo);
  rnd.irange = (lo, hi) => lo + Math.floor(rnd() * (hi - lo + 1));
  rnd.pick = (arr) => arr[Math.floor(rnd() * arr.length)];
  rnd.chance = (p) => rnd() < p;
  rnd.sign = () => (rnd() < 0.5 ? -1 : 1);
  rnd.shuffle = (arr) => {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(rnd() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  };
  /** weighted pick: entries [{w, ...}] */
  rnd.weighted = (entries, key = 'w') => {
    let total = 0;
    for (const e of entries) total += e[key] ?? 1;
    let r = rnd() * total;
    for (const e of entries) {
      r -= e[key] ?? 1;
      if (r <= 0) return e;
    }
    return entries[entries.length - 1];
  };
  return rnd;
}

/** Deterministic hash of integer coords -> [0,1) */
export function hash2(x, y, seed = 0) {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(seed | 0, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

export function hash3(x, y, z, seed = 0) {
  let h =
    Math.imul(x | 0, 374761393) ^
    Math.imul(y | 0, 668265263) ^
    Math.imul(z | 0, 1442695041) ^
    Math.imul(seed | 0, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Value noise with smootherstep interpolation. */
export function valueNoise2(x, y, seed = 0) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = smootherstep(xf), v = smootherstep(yf);
  const a = hash2(xi, yi, seed);
  const b = hash2(xi + 1, yi, seed);
  const c = hash2(xi, yi + 1, seed);
  const d = hash2(xi + 1, yi + 1, seed);
  return lerp(lerp(a, b, u), lerp(c, d, u), v);
}

/** Fractal Brownian motion over value noise. Returns ~[0,1]. */
export function fbm(x, y, seed = 0, octaves = 5, lacunarity = 2, gain = 0.5) {
  let amp = 1, freq = 1, sum = 0, norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += amp * valueNoise2(x * freq, y * freq, seed + i * 1013);
    norm += amp;
    amp *= gain;
    freq *= lacunarity;
  }
  return sum / norm;
}

/** Ridged multifractal — good for mountain ridges. */
export function ridge(x, y, seed = 0, octaves = 4) {
  let amp = 1, freq = 1, sum = 0, norm = 0;
  for (let i = 0; i < octaves; i++) {
    const n = 1 - Math.abs(valueNoise2(x * freq, y * freq, seed + i * 733) * 2 - 1);
    sum += amp * n * n;
    norm += amp;
    amp *= 0.5;
    freq *= 2;
  }
  return sum / norm;
}

/** Cheap worley/cellular noise: returns distance to nearest feature point. */
export function worley(x, y, seed = 0) {
  const xi = Math.floor(x), yi = Math.floor(y);
  let best = 9;
  for (let oy = -1; oy <= 1; oy++) {
    for (let ox = -1; ox <= 1; ox++) {
      const cx = xi + ox, cy = yi + oy;
      const px = cx + hash2(cx, cy, seed);
      const py = cy + hash2(cx, cy, seed + 7919);
      const d = dist2(x, y, px, py);
      if (d < best) best = d;
    }
  }
  return Math.sqrt(best);
}

// ————————————————————————————————————————————————— colour helpers

export function rgb(r, g, b) {
  return `rgb(${r | 0},${g | 0},${b | 0})`;
}
export function rgba(r, g, b, a) {
  return `rgba(${r | 0},${g | 0},${b | 0},${a})`;
}
export function mixRGB(c1, c2, t) {
  return [lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t)];
}
export function shade(c, amt) {
  return [clamp(c[0] * amt, 0, 255), clamp(c[1] * amt, 0, 255), clamp(c[2] * amt, 0, 255)];
}
export function css(c, a = 1) {
  return a >= 1 ? rgb(c[0], c[1], c[2]) : rgba(c[0], c[1], c[2], a);
}
export function hsl(h, s, l) {
  return `hsl(${h} ${s}% ${l}%)`;
}
/** hsl (0..360, 0..1, 0..1) -> [r,g,b] */
export function hsl2rgb(h, s, l) {
  h = wrap(h, 360) / 360;
  let r, g, b;
  if (s === 0) {
    r = g = b = l;
  } else {
    const hue2rgb = (p, q, t) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  return [r * 255, g * 255, b * 255];
}

// ————————————————————————————————————————————————— canvas helpers

export function makeCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.ceil(w));
  c.height = Math.max(1, Math.ceil(h));
  const ctx = c.getContext('2d');
  return { canvas: c, ctx };
}

export function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

/** Draw a soft radial "blob" — used all over for lights, shadows, glows. */
export function radial(ctx, x, y, r, inner, outer) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, Math.max(0.01, r));
  g.addColorStop(0, inner);
  g.addColorStop(1, outer);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, TAU);
  ctx.fill();
}

/** Simple object pool for particles/projectiles. */
export class Pool {
  constructor(factory, reset) {
    this.factory = factory;
    this.reset = reset;
    this.free = [];
    this.active = [];
  }
  spawn(...args) {
    const o = this.free.pop() || this.factory();
    this.reset(o, ...args);
    this.active.push(o);
    return o;
  }
  release(i) {
    const o = this.active[i];
    this.active[i] = this.active[this.active.length - 1];
    this.active.pop();
    this.free.push(o);
  }
  clear() {
    while (this.active.length) this.release(this.active.length - 1);
  }
}

/** Format a number compactly (1200 -> 1.2k) */
export function fmt(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e4) return (n / 1e3).toFixed(1) + 'k';
  return String(Math.round(n));
}

export function nowMs() {
  return performance.now();
}
