// fixed.js — determinism-safe replacements for the transcendental functions.
// L0: may not import anything except math.js constants.
//
// WHY THIS FILE EXISTS
// --------------------
// IEEE-754 guarantees exactly-rounded, identical results across every JS engine
// for: + - * /, Math.sqrt, Math.abs, Math.floor, Math.imul, comparisons and bit
// ops. It guarantees NOTHING for Math.sin/cos/tan/atan/atan2/exp/log/pow/hypot
// — the ECMAScript spec explicitly marks those implementation-defined. Engines
// mostly agree today, but pow and hypot have historically diverged, and older
// iOS Safari differs from current.
//
// Everything under src/sim/** must therefore use the functions below instead.
// tools/test/determinism.test.js statically scans for violations.
//
// Accuracy is deliberately traded for reproducibility: fatan2 carries ~1e-5 rad
// (0.0006 degrees) of error, which is invisible in a game but is the SAME error
// on every device — and that is the property we actually need.

import { PI, TAU, HALF_PI } from './math.js';

/** Euclidean length. Math.sqrt IS exactly specified, so this is safe as-is. */
export const flen = (x, y) => Math.sqrt(x * x + y * y);
export const flen3 = (x, y, z) => Math.sqrt(x * x + y * y + z * z);
export const fdist = (ax, ay, bx, by) => flen(bx - ax, by - ay);

// fdlibm __kernel_sin minimax coefficients. Exact on any engine because the
// evaluation is nothing but multiply and add.
const S1 = -1.66666666666666324348e-01;
const S2 = 8.33333333332248946124e-03;
const S3 = -1.98412698298579493134e-04;
const S4 = 2.75573137070700676789e-06;
const S5 = -2.50507602534068634195e-08;
const S6 = 1.58969099521155010221e-10;

/**
 * sin(x), reproducible bit-for-bit across engines.
 * Range-reduces to [-PI/2, PI/2] then evaluates an odd degree-13 polynomial.
 */
export function fsin(x) {
  // Reduce to [-PI, PI). Math.floor is exactly specified, so this is stable.
  let k = x * (1 / TAU);
  k = k - Math.floor(k);
  let t = k * TAU;
  if (t > PI) t -= TAU;

  // Fold into [-PI/2, PI/2] using sin(PI - t) === sin(t).
  if (t > HALF_PI) t = PI - t;
  else if (t < -HALF_PI) t = -PI - t;

  const z = t * t;
  return t + t * z * (S1 + z * (S2 + z * (S3 + z * (S4 + z * (S5 + z * S6)))));
}

export function fcos(x) {
  return fsin(x + HALF_PI);
}

// Hastings minimax coefficients for atan on [-1, 1]. Max error ~1.2e-5 rad.
const A1 = 0.9998660;
const A2 = -0.3302995;
const A3 = 0.1801410;
const A4 = -0.0851330;
const A5 = 0.0208351;

/** atan(x) for |x| <= 1. */
function atanUnit(x) {
  const z = x * x;
  return x * (A1 + z * (A2 + z * (A3 + z * (A4 + z * A5))));
}

/** atan(x) over the whole real line, via atan(x) = PI/2 - atan(1/x). */
export function fatan(x) {
  if (x >= 0) {
    if (x <= 1) return atanUnit(x);
    return HALF_PI - atanUnit(1 / x);
  }
  if (x >= -1) return atanUnit(x);
  return -HALF_PI - atanUnit(1 / x);
}

/** atan2(y, x), reproducible. Returns a value in (-PI, PI]. */
export function fatan2(y, x) {
  if (x === 0) {
    if (y > 0) return HALF_PI;
    if (y < 0) return -HALF_PI;
    return 0;
  }
  const a = fatan(y / x);
  if (x > 0) return a;
  return y >= 0 ? a + PI : a - PI;
}

/** Angle from (ax,ay) to (bx,by). The sim's replacement for Math.atan2. */
export const fangleTo = (ax, ay, bx, by) => fatan2(by - ay, bx - ax);

/**
 * b raised to a NON-NEGATIVE INTEGER power, by binary exponentiation.
 *
 * This is what makes the offline macro catch-up both fast and deterministic:
 * relaxing a price over 720 in-game hours is ~17 multiplies rather than 720
 * iterations, and unlike Math.pow the result is identical on every engine.
 */
export function powInt(b, n) {
  n = n | 0;
  if (n <= 0) return 1;
  let result = 1;
  let base = b;
  while (n > 0) {
    if (n & 1) result *= base;
    base *= base;
    n >>= 1;
  }
  return result;
}

/**
 * Normalise a 2D vector in place-free style; returns length so callers can
 * branch on the zero case without a second sqrt.
 */
export function fnorm2(x, y, out) {
  const m = flen(x, y);
  if (m === 0) { out[0] = 0; out[1] = 0; return 0; }
  const inv = 1 / m;
  out[0] = x * inv;
  out[1] = y * inv;
  return m;
}
