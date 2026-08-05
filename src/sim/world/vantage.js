// vantage.js — where the player will stand, and what must be on the horizon.
// L2.
//
// THE DIFFERENCE BETWEEN "SCATTERED" AND "COMPOSED"
//
// Scattering props by density gives a world where every direction looks the
// same, so no direction is worth walking in. Composition is the opposite
// operation: work out where the player will STOP AND LOOK — the prominent
// points — then guarantee each of those views has something to aim at in every
// quadrant. So this file extracts vantage points from the eroded macro field,
// and places landmarks so every 90-degree sector of every vantage holds at
// least one silhouette that is big enough and unoccluded.
//
// Everything is a pure function of the seed. The RNG comes from one dedicated
// stream (LANDMARK_STREAM, which belongs in sim/determinism/streams.js as
// WG_LANDMARK: 60) and every candidate draws a FIXED number of values, so
// tuning a score weight cannot shift the draw sequence for a different sector.
//
// NOTE: the caller must have installed the macro field (installMacroField) or
// accept that heightfield.js will lazily build it — both are deterministic.

import { makeRNG, streamSeed } from '../../core/rng.js';
import { TAU, clamp, clamp01 } from '../../core/math.js';
import { flen, fsin, fcos, fatan2 } from '../../core/fixed.js';
import {
  macroHeight, sampleHeight, sampleNormal, seaLevelMetres, isRiverTile,
  TILE, WORLD_TILES, WORLD_SIZE, HEIGHT_SCALE, SEA_LEVEL,
} from './heightfield.js';
import { regionWeights, dominantRegion } from '../../data/regions.js';
import { LANDMARK_KINDS, REGION_KIND_W, LANDMARK_STREAM, COMPOSE } from '../../data/landmarks.js';

export const SECTORS = COMPOSE.sectors;
const QUARTER = TAU / SECTORS;
const _w = new Float32Array(3);
const _n = new Float32Array(3);
const _kw = new Float32Array(LANDMARK_KINDS.length);

/** Which 90-degree sector a direction falls in. Sector 0 starts at +x. */
export function sectorOf(dx, dz) {
  let a = fatan2(dz, dx);
  if (a < 0) a += TAU;
  const s = (a / QUARTER) | 0;
  return s < 0 ? 0 : s >= SECTORS ? SECTORS - 1 : s;
}

/**
 * Macro elevation in metres, bilinear. Deliberately NOT sampleHeight: the
 * horizon test marches dozens of samples per candidate and only cares about
 * landforms, which live entirely in the macro field.
 */
function macroMetres(x, z, seed) {
  const tx = x / TILE, tz = z / TILE;
  const x0 = Math.floor(tx), z0 = Math.floor(tz);
  const fx = tx - x0, fz = tz - z0;
  const h00 = macroHeight(x0, z0, seed), h10 = macroHeight(x0 + 1, z0, seed);
  const h01 = macroHeight(x0, z0 + 1, seed), h11 = macroHeight(x0 + 1, z0 + 1, seed);
  const a = h00 + (h10 - h00) * fx;
  const b = h01 + (h11 - h01) * fx;
  return (a + (b - a) * fz) * HEIGHT_SCALE;
}

/**
 * Does the top of a thing at (lx, lz, topY) clear the intervening ground as
 * seen from (ex, ez, eyeY)?
 *
 * This is what makes "silhouette" mean something. Comparing elevation ANGLES
 * rather than heights is what lets it work looking downhill as well as up: a
 * landmark on a valley floor is still a silhouette against the far bank, and a
 * landmark behind the next ridge is not a silhouette at all.
 */
function clearsHorizon(seed, ex, ez, eyeY, lx, lz, topY, d) {
  const need = (topY - eyeY) / d;
  const steps = clamp(Math.round(d / 44), 4, 56);
  const dx = (lx - ex) / steps, dz = (lz - ez) / steps;
  for (let i = 1; i < steps; i++) {
    const t = i / steps;
    const h = macroMetres(ex + dx * i, ez + dz * i, seed);
    if ((h - eyeY) / (d * t) >= need) return false;
  }
  return true;
}

/** Apparent height in radians, or 0 if this landmark does not qualify at all. */
function silhouetteAngle(l, ex, ez, eyeY, seed) {
  const dx = l.x - ex, dz = l.z - ez;
  const d = flen(dx, dz);
  if (d < COMPOSE.minR || d > COMPOSE.maxR) return 0;
  const ang = l.height / d;
  if (ang < COMPOSE.minAngular) return 0;
  if (!clearsHorizon(seed, ex, ez, eyeY, l.x, l.z, l.y + l.height, d)) return 0;
  return ang;
}

/**
 * THE VERIFICATION HELPER the harness calls.
 *
 * Given a world position, report which 90-degree sectors contain a landmark
 * silhouette, and which landmark is doing the work in each. This is the
 * composition rule made executable — see tools/test/landmarks.test.js.
 */
export function sectorReport(landmarks, x, z, seed, eyeY) {
  const eye = (eyeY === undefined ? sampleHeight(x, z, seed) + 1.7 : eyeY);
  const covered = new Array(SECTORS).fill(false);
  const best = new Array(SECTORS).fill(null);
  const bestAng = new Array(SECTORS).fill(0);
  let n = 0;
  for (let i = 0; i < landmarks.length; i++) {
    const l = landmarks[i];
    const ang = silhouetteAngle(l, x, z, eye, seed);
    if (ang <= 0) continue;
    const s = sectorOf(l.x - x, l.z - z);
    n++;
    if (ang > bestAng[s]) { bestAng[s] = ang; best[s] = l; covered[s] = true; }
  }
  let all = true;
  for (let s = 0; s < SECTORS; s++) if (!covered[s]) { all = false; break; }
  return { covered, best, angles: bestAng, count: n, all, eyeY: eye };
}

// ————————————————————————————————— vantage extraction

/**
 * Prominent points of the continent, most commanding first.
 *
 * A STRICT local maximum is the obvious definition and the wrong one: thermal
 * erosion deliberately flattens summits, so this continent has about fifteen of
 * them and half a dozen survive a separation filter. What we want is "ground
 * that stands above its surroundings" — a CONTINUOUS quantity, elevation minus
 * the local mean, penalised by anything nearby that towers over it. That yields
 * as many ranked viewpoints as asked for and degrades gracefully.
 */
export function findVantagePoints(seed, opts = {}) {
  const stride = opts.stride || COMPOSE.vantageStride;
  const R = COMPOSE.vantageRing;
  const margin = Math.round(COMPOSE.edgeMargin / TILE);
  const n = Math.floor((WORLD_TILES - margin * 2) / stride);
  const g = new Float32Array(n * n);
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) g[j * n + i] = macroHeight(margin + i * stride, margin + j * stride, seed);
  }

  const cand = [];
  for (let j = R; j < n - R; j++) {
    for (let i = R; i < n - R; i++) {
      const h = g[j * n + i];
      if (h < SEA_LEVEL + 0.025) continue;
      let sum = 0, cnt = 0, lo = h, hi = h;
      for (let dj = -R; dj <= R; dj++) {
        for (let di = -R; di <= R; di++) {
          const v = g[(j + dj) * n + i + di];
          sum += v; cnt++;
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
      // Above the local mean, rewarded for the drop below and punished hard for
      // anything overlooking it — a shoulder under a peak is not a viewpoint.
      const prom = (h - sum / cnt) + (h - lo) * 0.5 - (hi - h) * 1.4;
      if (prom <= 0) continue;
      cand.push({ tx: margin + i * stride, tz: margin + j * stride, prom });
    }
  }

  // Deterministic order: prominence, then position. Never rely on scan order
  // alone — two equally prominent hills must still sort the same way.
  cand.sort((a, b) => (b.prom - a.prom) || (a.tz - b.tz) || (a.tx - b.tx));

  const sep = opts.separation || COMPOSE.vantageSeparation;
  const want = opts.count || COMPOSE.vantageCount;
  const minProm = opts.minProm !== undefined ? opts.minProm : COMPOSE.vantageMinProm;
  const out = [];
  for (let i = 0; i < cand.length && out.length < want; i++) {
    const c = cand[i];
    // Sorted descending, so the first shortfall ends the search. The floor is
    // load-bearing: a "viewpoint" with a couple of metres of prominence is a
    // hollow with a ridge across half its horizon, and demanding four covered
    // sectors from there asks placement to do something impossible.
    if (c.prom < minProm) break;
    const x = c.tx * TILE, z = c.tz * TILE;
    let ok = true;
    for (let k = 0; k < out.length; k++) {
      if (flen(out[k].x - x, out[k].z - z) < sep) { ok = false; break; }
    }
    if (!ok) continue;
    sampleNormal(x, z, seed, _n, 4);
    if (_n[1] < COMPOSE.vantageMinNy) continue;
    const y = sampleHeight(x, z, seed);
    out.push({ x, z, y, eyeY: y + 1.7, prom: c.prom, covered: 0, land: 0 });
  }
  return out;
}

// ————————————————————————————————— placement

/** Everything that disqualifies a piece of ground for building on. */
function siteIsBuildable(x, z, seed, kind, list, minNy, sepMul) {
  const m = COMPOSE.edgeMargin;
  if (x < m || z < m || x > WORLD_SIZE - m || z > WORLD_SIZE - m) return -1;
  const y = sampleHeight(x, z, seed);
  if (y < seaLevelMetres() + COMPOSE.seaClearance) return -1;
  if (isRiverTile(x / TILE, z / TILE, seed)) return -1;
  sampleNormal(x, z, seed, _n, 4);
  if (_n[1] < minNy) return -1;
  for (let i = 0; i < list.length; i++) {
    const o = list[i];
    // Two silhouettes closer than this merge into one blob on the horizon,
    // which is a worse read than either of them alone.
    const sep = Math.max(COMPOSE.minSeparation, kind.radius + o.radius + 45) * sepMul;
    if (flen(o.x - x, o.z - z) < sep) return -1;
  }
  return y;
}

/**
 * Settle a footprint into sloped ground. A 30 m base placed at the height of
 * its centre floats by metres on the downhill side, and a gap under a monolith
 * is the most obvious tell that something was dropped in procedurally. Sinking
 * to the lowest ground under the footprint (bounded, so nothing swallows
 * itself) buries the uphill side instead, which is what a ruin does anyway.
 */
function settleY(x, z, r, y, height, seed) {
  let lo = y;
  const s = r * 0.72;
  for (let i = 0; i < 4; i++) {
    const a = i * QUARTER + 0.4;
    const h = sampleHeight(x + fcos(a) * s, z + fsin(a) * s, seed);
    if (h < lo) lo = h;
  }
  const floor = Math.max(y - height * 0.26, seaLevelMetres() + 1);
  return lo < floor ? floor : lo;
}

/**
 * How good a silhouette this site would make, seen from the vantage.
 * Local prominence dominates: a landmark on a shoulder disappears into the
 * hillside behind it, and no amount of size fixes that.
 */
function siteScore(x, z, y, seed) {
  let lo = y;
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * TAU;
    const h = macroMetres(x + fcos(a) * 90, z + fsin(a) * 90, seed);
    if (h < lo) lo = h;
  }
  return clamp01((y - lo) / 55);
}

/**
 * Pick a kind from the three cultures blended by weight, driven by an
 * ALREADY-DRAWN uniform so the draw count per candidate is fixed.
 */
function pickKind(w, r) {
  const K = LANDMARK_KINDS.length;
  let total = 0;
  for (let k = 0; k < K; k++) {
    let acc = 0;
    for (let g = 0; g < REGION_KIND_W.length; g++) acc += w[g] * REGION_KIND_W[g][k];
    _kw[k] = acc;
    total += acc;
  }
  if (total <= 0) return 0;
  let t = r * total;
  for (let k = 0; k < K; k++) {
    t -= _kw[k];
    if (t <= 0) return k;
  }
  return K - 1;
}

/**
 * Search one sector of one vantage for the best buildable, visible site.
 *
 * The grid is FIXED (radii x angles) and EVERY candidate draws its five random
 * values before any rejection test runs, deliberately: retuning a score weight,
 * a slope limit or the sea clearance then cannot shift the draw sequence, so an
 * existing world keeps the landmarks it had except where the rule changed.
 */
function chooseSite(rng, seed, v, s, list, pass) {
  const a0 = s * QUARTER;
  const radii = pass ? COMPOSE.retryRadii : COMPOSE.placeRadii;
  const K = pass ? COMPOSE.retryAngles : COMPOSE.placeAngles;
  const minNy = pass ? COMPOSE.retrySlopeNy : COMPOSE.maxSlopeNy;
  const sepMul = pass ? COMPOSE.retrySepMul : 1;

  let best = null, bestScore = -1;
  for (let ri = 0; ri < radii.length; ri++) {
    for (let ai = 0; ai < K; ai++) {
      const jitterA = (rng() - 0.5) * (QUARTER / K) * 1.6;
      const jitterR = (rng() - 0.5) * 150;
      const kindRoll = rng();
      const scaleRoll = rng();
      const rotRoll = rng();

      // Both are clamped INSIDE the sector and inside the silhouette range, so
      // the site the placer accepts is one sectorReport() will agree with.
      const ang = a0 + clamp(((ai + 0.5) / K) * QUARTER + jitterA, 0.02, QUARTER - 0.02);
      const d = clamp(radii[ri] + jitterR, COMPOSE.minR + 20, COMPOSE.maxR - 20);
      const x = v.x + fcos(ang) * d;
      const z = v.z + fsin(ang) * d;

      // Culture is decided by the SITE, not by the viewer — that is what puts
      // a different skyline on each side of a cultural border.
      regionWeights(x, z, seed, _w);
      const region = dominantRegion(_w);
      const kindId = pickKind(_w, kindRoll);
      const kind = LANDMARK_KINDS[kindId];
      const scale = kind.scale[0] + scaleRoll * (kind.scale[1] - kind.scale[0]);
      const height = kind.height * scale;

      if (height / d < COMPOSE.minAngular) continue;
      const surface = siteIsBuildable(x, z, seed, kind, list, minNy, sepMul);
      if (surface < 0) continue;
      // Settle BEFORE the visibility test, never after: a base that sinks two
      // metres after being judged visible is a base that sectorReport() will
      // later disagree about, and that disagreement is the whole bug class the
      // composition test exists to catch.
      const radius = kind.radius * scale;
      const y = settleY(x, z, radius, surface, height, seed);
      if (!clearsHorizon(seed, v.x, v.z, v.eyeY, x, z, y + height, d)) continue;

      // Prominent ground first, then a comfortable middle distance, then size.
      const score = siteScore(x, z, y, seed) * 1.6
        + (1 - Math.abs(d - 760) / 1400) * 0.5
        + clamp01(height / 70) * 0.45;
      if (score <= bestScore) continue;
      bestScore = score;
      best = {
        kind: kindId, x, z, y, scale, height, radius,
        rot: rotRoll * TAU,
        region, tint: kind.tint,
        // The blended culture weights travel WITH the landmark so the renderer
        // can tint it from REGION_PALETTE without re-sampling the noise field.
        w: [_w[0], _w[1], _w[2]],
      };
    }
  }
  return best;
}

/**
 * Place every landmark in the world. Greedy over vantages: a landmark placed
 * for an early vantage very often already satisfies a later one, which is
 * exactly the behaviour we want — the same tower is the north marker from one
 * hill and the west marker from another. That reuse is why the count stays in
 * the dozens instead of four per vantage.
 */
export function placeLandmarks(seed, opts = {}) {
  const rng = makeRNG(streamSeed(seed, LANDMARK_STREAM));
  const vantages = findVantagePoints(seed, opts);
  const list = [];

  for (let vi = 0; vi < vantages.length; vi++) {
    const v = vantages[vi];
    for (let s = 0; s < SECTORS; s++) {
      let held = false;
      for (let i = 0; i < list.length && !held; i++) {
        const l = list[i];
        if (sectorOf(l.x - v.x, l.z - v.z) !== s) continue;
        if (silhouetteAngle(l, v.x, v.z, v.eyeY, seed) > 0) held = true;
      }
      if (held) { v.covered |= 1 << s; v.land |= 1 << s; continue; }

      const site = chooseSite(rng, seed, v, s, list, 0)
        || chooseSite(rng, seed, v, s, list, 1);
      if (site) {
        site.index = list.length;
        list.push(site);
        v.covered |= 1 << s;
        v.land |= 1 << s;
      }
      // No bit set means even the retry found no buildable, visible ground in
      // this sector — open sea off a coastal hill, almost always. That is the
      // ONE excuse the composition rule accepts, and the test checks it.
    }
  }
  return { landmarks: list, vantages, seed };
}

/** Nearest landmark to a position, for HUD/quest use. Linear: the list is small. */
export function nearestLandmark(landmarks, x, z) {
  let best = null, bd = Infinity;
  for (let i = 0; i < landmarks.length; i++) {
    const l = landmarks[i];
    const d = flen(l.x - x, l.z - z);
    if (d < bd) { bd = d; best = l; }
  }
  return best ? { landmark: best, dist: bd } : null;
}
