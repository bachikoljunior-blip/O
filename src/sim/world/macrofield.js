// macrofield.js — the continent's macro elevation, built once per seed.
// L2.
//
// WHY THIS EXISTS
// The previous field was pure noise evaluated per sample. Noise alone cannot
// make a cliff, a ridgeline or a valley floor: fBm is smooth by construction
// and a ridged multifractal only produces sharp CRESTS, never sharp WALLS.
// The result read as a putting green from eye height no matter what the
// lighting did.
//
// Erosion is what turns noise into landscape, and erosion is inherently
// iterative — it needs neighbours, so it cannot be evaluated per sample. So we
// build the whole 768x768 field once, in the loading screen, and sample it
// afterwards. 2.36 MB resident, never saved: still a pure function of the seed.

import { fbm, ridge, hash2 } from '../../core/noise.js';
import { clamp, clamp01, lerp, smoothstep, invLerp } from '../../core/math.js';
import { flen } from '../../core/fixed.js';
import { makeRNG, streamSeed } from '../../core/rng.js';
import { S as STREAM } from '../determinism/streams.js';

export const WORLD_TILES = 768;
export const SEA_LEVEL = 0.42;

const F1 = 1 / 170, F2 = 1 / 260, F3 = 1 / 90;

/** Raw noise elevation, before erosion. Normalised [0,1]. */
function baseHeight(tx, tz, seed) {
  const base = fbm(tx * F1, tz * F1, seed + 11, 5);
  const broad = fbm(tx * F2 + 400, tz * F2 - 220, seed + 29, 4);

  // A SHARP mountain mask. The old smoothstep over [0.40, 0.70] spread the
  // ridged term thinly across half the map, so nothing was ever tall enough to
  // read as a range. Narrowing the band and raising the weight concentrates
  // the same material into fewer, much bigger landforms.
  const maskRaw = fbm(tx * F2 * 0.6, tz * F2 * 0.6, seed + 53, 3);
  const mountainMask = smoothstep(clamp01(invLerp(0.48, 0.62, maskRaw)));
  const rid = ridge(tx * F3, tz * F3, seed + 71, 5);

  // A second, finer ridge layer gives foothills leading up to the peaks
  // instead of a range that erupts straight out of flat ground.
  const rid2 = ridge(tx * F3 * 2.6 + 90, tz * F3 * 2.6 - 40, seed + 83, 3);

  let land = 0.46
    + (base - 0.5) * 0.52
    + rid * 1.05 * mountainMask
    + rid2 * 0.16 * mountainMask
    + (broad - 0.5) * 0.10;

  // Continental mask: elliptical, perturbed twice so the coast is ragged.
  const nx = (tx / WORLD_TILES) * 2 - 1;
  const nz = (tz / WORLD_TILES) * 2 - 1;
  let d = flen(nx * 1.025, nz * 1.086) * 2;
  d += (fbm(tx * 0.006, tz * 0.006, seed + 97, 3) - 0.5) * 0.55;
  d += (fbm(tx * 0.018, tz * 0.018, seed + 131, 2) - 0.5) * 0.22;
  const mask = 1 - smoothstep(clamp01(invLerp(0.74, 1.26, d)));

  return clamp(lerp(0.05, land, mask), 0, 1.35);
}

/**
 * Thermal (talus) erosion.
 *
 * Any slope steeper than the talus angle sheds material downhill. Two things
 * come out of this that noise cannot produce: valley floors FILL and flatten,
 * and the shoulders above them steepen into something the eye reads as a cliff.
 * It is the cheapest erosion model there is — four neighbour comparisons per
 * cell — and it buys most of the silhouette.
 */
function* thermalErode(h, w, iterations, talus, rate, t0, t1) {
  const delta = new Float32Array(h.length);
  for (let it = 0; it < iterations; it++) {
    delta.fill(0);
    for (let z = 1; z < w - 1; z++) {
      const row = z * w;
      for (let x = 1; x < w - 1; x++) {
        const i = row + x;
        const hi = h[i];
        // Steepest descending neighbour only: moving material to every
        // downhill neighbour at once smears the terrain into mush.
        let bestIdx = -1, bestDrop = talus;
        const n0 = h[i - 1], n1 = h[i + 1], n2 = h[i - w], n3 = h[i + w];
        if (hi - n0 > bestDrop) { bestDrop = hi - n0; bestIdx = i - 1; }
        if (hi - n1 > bestDrop) { bestDrop = hi - n1; bestIdx = i + 1; }
        if (hi - n2 > bestDrop) { bestDrop = hi - n2; bestIdx = i - w; }
        if (hi - n3 > bestDrop) { bestDrop = hi - n3; bestIdx = i + w; }
        if (bestIdx === -1) continue;
        const move = (bestDrop - talus) * rate;
        delta[i] -= move;
        delta[bestIdx] += move;
      }
    }
    for (let i = 0; i < h.length; i++) h[i] += delta[i];
    yield { phase: '浸食', t: t0 + (t1 - t0) * ((it + 1) / iterations) };
  }
}

/**
 * Carve river valleys by steepest descent from high ground.
 *
 * Rivers are what put a NEGATIVE feature into the landscape. Erosion alone only
 * lowers and flattens; a carved channel with raised banks is what makes a
 * valley read as a valley and gives the eye somewhere to be led.
 */
function carveRivers(h, w, seed, count) {
  const rng = makeRNG(streamSeed(seed, STREAM.WG_RIVERS));
  const water = new Uint8Array(h.length);

  for (let r = 0; r < count; r++) {
    // Source: best of a handful of samples in the highlands.
    let sx = 0, sz = 0, best = -1;
    for (let a = 0; a < 40; a++) {
      const cx = 40 + rng.int(w - 80);
      const cz = 40 + rng.int(w - 80);
      const e = h[cz * w + cx];
      if (e > best && e < 1.1) { best = e; sx = cx; sz = cz; }
    }
    if (best < SEA_LEVEL + 0.16) continue;

    // TRACE FIRST, CARVE SECOND.
    //
    // Carving as we walk digs a pit under the cursor, and the descent then
    // looks for a neighbour lower than the hole it just made — finds none,
    // stalls, and digs the same spot until it punches through to sea level.
    // Separating the passes also lets width scale with the ACTUAL path length
    // rather than a guessed maximum.
    const px = [], pz = [];
    let x = sx, z = sz;
    for (let step = 0; step < 1200; step++) {
      px.push(x); pz.push(z);
      if (h[z * w + x] <= SEA_LEVEL) break;

      let bx = x, bz = z, bh = h[z * w + x];
      for (let dz = -1; dz <= 1; dz++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (dx === 0 && dz === 0) continue;
          const nx = x + dx, nz = z + dz;
          if (nx < 1 || nz < 1 || nx >= w - 1 || nz >= w - 1) continue;
          // A wander term so channels meander instead of running dead straight
          // down the gradient.
          const jitter = (hash2(nx, nz, seed + 5501) - 0.5) * 0.004;
          const nh = h[nz * w + nx] + jitter;
          if (nh < bh) { bh = nh; bx = nx; bz = nz; }
        }
      }
      if (bx === x && bz === z) {
        // A genuine basin. Step out along a random direction and carry on;
        // the carve pass will leave a lake-ish flat here.
        x = clamp(x + rng.irange(-3, 3), 1, w - 2);
        z = clamp(z + rng.irange(-3, 3), 1, w - 2);
      } else { x = bx; z = bz; }
    }

    const len = px.length;
    if (len < 12) continue;
    for (let s = 0; s < len; s++) {
      const t = s / len;
      // Headwaters are gullies, the lower reaches are broad valleys.
      const width = 1.6 + t * 5.0;
      const depth = 0.014 + t * 0.026;
      const wi = Math.ceil(width);
      const cx = px[s], cz = pz[s];
      for (let dz = -wi; dz <= wi; dz++) {
        for (let dx = -wi; dx <= wi; dx++) {
          const nx = cx + dx, nz = cz + dz;
          if (nx < 0 || nz < 0 || nx >= w || nz >= w) continue;
          const d = flen(dx, dz);
          if (d > width) continue;
          const j = nz * w + nx;
          // Squared falloff: deepest at the centre, banks left standing.
          const k = 1 - (d / width);
          h[j] -= depth * k * k;
          if (d < width * 0.5) water[j] = 1;
        }
      }
    }
  }
  return water;
}

/**
 * Build the whole macro field. A GENERATOR: it yields progress so the loading
 * screen can stay responsive rather than the tab going white for a second.
 */
export function* buildMacroField(seed) {
  const w = WORLD_TILES;
  const h = new Float32Array(w * w);

  for (let z = 0; z < w; z++) {
    const row = z * w;
    for (let x = 0; x < w; x++) h[row + x] = baseHeight(x, z, seed);
    if ((z & 63) === 0) yield { phase: '大陸', t: z / w * 0.45 };
  }

  // Two passes at different talus angles: the coarse pass cuts the broad
  // valleys, the fine pass cleans up the scree without re-smoothing them.
  yield* thermalErode(h, w, 14, 0.012, 0.35, 0.45, 0.68);
  yield* thermalErode(h, w, 8, 0.028, 0.25, 0.68, 0.80);

  const water = carveRivers(h, w, seed, 26);
  yield { phase: '河川', t: 0.94 };

  for (let i = 0; i < h.length; i++) if (h[i] < 0) h[i] = 0;
  yield { phase: '完了', t: 1, field: { h, water, w } };
  return { h, water, w };
}

/** Synchronous convenience for tests and tools. */
export function buildMacroFieldSync(seed) {
  const gen = buildMacroField(seed);
  let r = gen.next();
  while (!r.done) r = gen.next();
  return r.value;
}
