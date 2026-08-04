// math.js — pure scalar helpers. No DOM, no data tables, no allocation.
// L0: may not import anything.

export const TAU = 6.283185307179586;
export const PI = 3.141592653589793;
export const HALF_PI = 1.5707963267948966;
export const DEG = 0.017453292519943295;

export const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
export const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
export const lerp = (a, b, t) => a + (b - a) * t;
export const invLerp = (a, b, v) => (b - a === 0 ? 0 : (v - a) / (b - a));
export const smoothstep = (t) => t * t * (3 - 2 * t);
export const smootherstep = (t) => t * t * t * (t * (t * 6 - 15) + 10);
export const sign = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);
export const wrap = (v, n) => ((v % n) + n) % n;

/** Shortest signed difference between two angles, in (-PI, PI]. */
export function angleDiff(a, b) {
  let d = (a - b) % TAU;
  if (d > PI) d -= TAU;
  else if (d < -PI) d += TAU;
  return d;
}

/** Move `v` toward `target` by at most `step`. */
export function approach(v, target, step) {
  if (v < target) return v + step < target ? v + step : target;
  if (v > target) return v - step > target ? v - step : target;
  return v;
}

/**
 * Rotate `from` toward `to` by at most `maxStep` radians, taking the short way.
 * Used for attack turn-caps and camera follow.
 */
export function approachAngle(from, to, maxStep) {
  const d = angleDiff(to, from);
  if (d > maxStep) return from + maxStep;
  if (d < -maxStep) return from - maxStep;
  return to;
}

export const dist2 = (ax, ay, bx, by) => {
  const dx = bx - ax, dy = by - ay;
  return dx * dx + dy * dy;
};

export const dist3sq = (ax, ay, az, bx, by, bz) => {
  const dx = bx - ax, dy = by - ay, dz = bz - az;
  return dx * dx + dy * dy + dz * dz;
};

/**
 * Frame-rate-correct exponential decay factor.
 * ONLY for the presentation layer — the sim runs at a fixed dt, so sim code
 * multiplies by a constant instead (see core/fixed.js notes on Math.pow).
 */
export const decay = (rate, dt) => Math.pow(rate, dt * 60);
