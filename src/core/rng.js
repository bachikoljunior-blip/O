// rng.js — seeded random. All integer ops, so identical on every engine.
// L0.

/**
 * mulberry32. Ported unchanged from the 2D build, where it was the substrate
 * for the whole world generator — keeping it identical means old seeds still
 * describe recognisable continents.
 */
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
      const tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
    }
    return arr;
  };
  /** Weighted pick over [{w, ...}]. Iterates an ARRAY, so order is defined. */
  rnd.weighted = (entries, key = 'w') => {
    let total = 0;
    for (let i = 0; i < entries.length; i++) total += entries[i][key] ?? 1;
    let r = rnd() * total;
    for (let i = 0; i < entries.length; i++) {
      r -= entries[i][key] ?? 1;
      if (r <= 0) return entries[i];
    }
    return entries[entries.length - 1];
  };
  /** Expose the raw state so a save can resume a stateful stream mid-flight. */
  rnd.state = () => a >>> 0;
  rnd.setState = (s) => { a = s >>> 0; };
  return rnd;
}

/** splitmix32 finaliser — a strong integer avalanche. */
export function mix32(x) {
  x = x | 0;
  x = Math.imul(x ^ (x >>> 16), 0x21f0aaad);
  x = Math.imul(x ^ (x >>> 15), 0x735a2d97);
  return (x ^ (x >>> 15)) >>> 0;
}

/**
 * Derive an independent seed for a named stream.
 *
 * The failure this prevents: with one global RNG, adding a system that draws
 * one extra number shifts every later draw, silently changing every existing
 * world. Streams are derived from (worldSeed, streamId) so appending a new
 * stream id perturbs nothing that already exists.
 */
export function streamSeed(worldSeed, streamId, salt = 0) {
  return mix32(mix32((worldSeed | 0) ^ Math.imul(streamId | 0, 0x9e3779b1)) ^ (salt | 0));
}

/** FNV-1a over a Uint32 view. Used for determinism hash trails. */
export function hashU32(view, seed = 0x811c9dc5) {
  let h = seed >>> 0;
  for (let i = 0; i < view.length; i++) {
    h ^= view[i];
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * Hash an arbitrary TypedArray by reinterpreting its buffer as Uint32.
 * Float32Array of NaN would hash inconsistently, but the soak tests assert
 * there are no NaNs, so this is safe in practice and very fast.
 */
export function hashBuffer(ta, seed = 0x811c9dc5) {
  const bytes = ta.byteLength - (ta.byteLength % 4);
  const u32 = new Uint32Array(ta.buffer, ta.byteOffset, bytes / 4);
  return hashU32(u32, seed);
}
