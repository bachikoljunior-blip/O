// landmarks.js — the landmark grammar, expressed as pure numbers.
// L1.
//
// EVERY HORIZON MUST CONTAIN A SILHOUETTE.
//
// A landmark's whole job is done at 500-2000 m, where it is twenty pixels of
// black against the sky. So the grammar is written for the SILHOUETTE first:
// each kind is a small stack of primitives whose outline is unmistakable at a
// glance — a ring of teeth, a snapped cylinder, a gate, a ribcage, a lattice.
// Surface detail below about two metres is invisible at that range and is
// therefore simply not modelled.
//
// The recipes live in the DATA layer rather than in gfx/ on purpose: they are
// parameter tables, they contain no engine types, and keeping them here means
// the placement pass (sim/world/vantage.js) can reason about a landmark's
// HEIGHT and FOOTPRINT without ever touching three.js.
//
// Part convention, shared with gfx/landmarks.js:
//   t = CENTRE of the primitive, in metres, origin at ground level
//   r = XYZ Euler rotation, radians
//   k = shade slot (index into SHADE below) — multiplied by the per-instance
//       region tint, so one geometry serves all three cultures
//   far = 1 if the part survives into the distant LOD

import { TAU, PI } from '../core/math.js';

/**
 * NEW RNG STREAM ID.
 *
 * This mirrors the entry that belongs in src/sim/determinism/streams.js as
 * `WG_LANDMARK: 60` (append only, never renumber). It is declared here because
 * this agent does not own streams.js; the integrator should add the line there
 * and this constant can then be deleted in favour of `S.WG_LANDMARK`. The
 * VALUE is what matters for determinism, and 60 sits above every id in use.
 */
export const LANDMARK_STREAM = 60;

/** Kind ids. Append only — the placement stream picks by index. */
export const LK = {
  MONOLITH: 0,
  TOWER: 1,
  TORII: 2,
  PAGODA: 3,
  DEADTREE: 4,
  HULK: 5,
  MAST: 6,
};

/**
 * Shade slots. Multipliers over the per-instance region colour, so a monolith
 * in the ash kingdom and one in the rust waste share geometry but not palette.
 * Slot 3 is the "accent" — rust bleed, lacquer, moss — pushed warm enough to
 * read as a different material rather than as shading.
 */
export const SHADE = [
  [1.00, 1.00, 1.00],
  [0.62, 0.63, 0.66],
  [1.22, 1.18, 1.10],
  [1.05, 0.52, 0.30],
];

const Z3 = [0, 0, 0];
const box = (s, t, r, k, far) => ({ p: 0, s, t, r: r || Z3, k, far: far ? 1 : 0 });
const cyl = (rt, rb, h, seg, t, r, k, far) =>
  ({ p: 1, s: [rt, rb, h, seg], t, r: r || Z3, k, far: far ? 1 : 0 });
const cone = (r0, h, seg, t, r, k, far) =>
  ({ p: 2, s: [r0, h, seg], t, r: r || Z3, k, far: far ? 1 : 0 });
const ico = (r0, d, t, k, far) => ({ p: 3, s: [r0, d], t, r: Z3, k, far: far ? 1 : 0 });

// ————————————————————————————————— the seven silhouettes

/** Standing stones: a broken ring of teeth. Reads at any distance, any angle. */
function monolith() {
  const p = [];
  for (let i = 0; i < 7; i++) {
    const a = (i / 7) * TAU + 0.21;
    const rr = 10.5 + (i % 3) * 1.6;
    const x = Math.cos(a) * rr, z = Math.sin(a) * rr;
    if (i === 2 || i === 5) {                       // two have toppled
      p.push(box([3.0, 2.3, 15 + i], [x, 1.15, z], [0, -a, 0.07], 1, 0));
      continue;
    }
    const h = 14 + ((i * 4.13) % 10);
    p.push(box([3.1, h, 2.2], [x, h / 2, z],
      [(i % 2 ? 1 : -1) * 0.05, -a, ((i % 3) - 1) * 0.06], i === 0 ? 2 : 0, 1));
  }
  p.push(box([7.4, 1.2, 4.6], [0, 0.6, 0], [0, 0.42, 0], 1, 0));
  return p;
}

/** A snapped keep tower. The jagged crown is the whole read. */
function tower() {
  const p = [];
  p.push(cyl(4.6, 6.6, 30, 10, [0, 15, 0], [0, 0, 0.018], 0, 1));
  p.push(cyl(7.0, 7.8, 3.2, 10, [0, 1.6, 0], null, 1, 1));
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * TAU;
    const hh = 1.6 + ((i * 5.3) % 8);
    p.push(box([2.5, hh, 2.1], [Math.cos(a) * 4.0, 30 + hh / 2, Math.sin(a) * 4.0],
      [0, -a, 0], i % 2 ? 2 : 0, i < 4 ? 1 : 0));
  }
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * TAU + 0.5;
    p.push(box([2.3, 17, 4.8], [Math.cos(a) * 6.7, 8.5, Math.sin(a) * 6.7],
      [0, -a, 0.085], 1, i === 0 ? 1 : 0));
  }
  p.push(box([4.2, 12, 4.2], [10.5, 4.4, -6.5], [0.5, 0.7, 0.9], 1, 0));
  return p;
}

/** A great weathered gate. Two verticals and two horizontals: unmistakable. */
function torii() {
  const p = [];
  for (const sx of [-1, 1]) {
    p.push(cyl(1.35, 1.8, 21, 8, [sx * 8.4, 10.5, 0], [0, 0, -sx * 0.035], 0, 1));
    p.push(cyl(2.4, 2.8, 1.5, 8, [sx * 8.8, 0.75, 0], null, 1, 0));
    p.push(box([4.2, 1.05, 2.9], [sx * 13.6, 23.15, 0], [0, 0, sx * 0.11], 2, 0));
  }
  p.push(box([25.5, 2.0, 2.7], [0, 21.6, 0], null, 0, 1));      // kasagi
  p.push(box([27.0, 1.0, 3.2], [0, 22.9, 0], null, 2, 1));      // shimaki
  p.push(box([19.0, 1.35, 2.0], [0, 17.4, 0], null, 0, 1));     // nuki
  p.push(box([1.6, 3.6, 1.6], [0, 19.7, 0], null, 3, 0));       // gakuzuka
  return p;
}

/** A pagoda with its top storey lying in the yard. Stepped, so it reads tiny. */
function pagoda() {
  const p = [];
  const tiers = [[9.4, 5.4, 0], [8.0, 4.7, 6.8], [6.7, 4.2, 12.7], [5.4, 3.6, 18.0]];
  for (let i = 0; i < tiers.length; i++) {
    const [w, h, y] = tiers[i];
    p.push(box([w, h, w], [0, y + h / 2, 0], [0, i * 0.05, 0], i % 2 ? 0 : 1, 1));
    p.push(cone(w * 1.24, 2.5, 4, [0, y + h + 1.05, 0], [0, PI / 4 + i * 0.05, 0], 3, 1));
  }
  p.push(box([4.8, 4.2, 4.8], [10.8, 2.1, 5.6], [0.42, 0.6, 0.25], 1, 0));
  p.push(cone(5.6, 2.3, 4, [12.8, 3.7, 7.8], [0.95, 0.4, 0.3], 3, 0));
  p.push(cyl(0.35, 0.55, 8.5, 6, [0.9, 25.2, 0.5], [0.10, 0, 0.16], 2, 1));
  return p;
}

/** A dead giant. Bare branches are the most legible organic silhouette there is. */
function deadTree() {
  const p = [];
  p.push(cyl(2.1, 3.6, 12, 8, [0, 6, 0], [0, 0, 0.03], 1, 1));
  p.push(cyl(1.4, 2.1, 10, 7, [0.7, 16.4, 0.3], [0, 0, 0.07], 1, 1));
  p.push(cyl(0.8, 1.4, 8.5, 6, [2.0, 25.2, 0.8], [0, 0, 0.11], 0, 1));
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * TAU + 0.4;
    const len = 12 - (i % 4) * 2.1;
    const y = 12.5 + (i % 4) * 4.3;
    const lean = 0.58 + (i % 3) * 0.17;
    p.push(cyl(0.22, 0.9, len, 5,
      [Math.cos(a) * len * 0.33, y + len * 0.26, Math.sin(a) * len * 0.33],
      [Math.sin(a) * lean, 0, -Math.cos(a) * lean], 1, i < 4 ? 1 : 0));
  }
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * TAU;
    p.push(cyl(0.3, 1.2, 5, 4, [Math.cos(a) * 2.7, 1.0, Math.sin(a) * 2.7],
      [Math.sin(a) * 1.2, 0, -Math.cos(a) * 1.2], 1, 0));
  }
  return p;
}

/** A beached machine carcass: spine, ribs, cowl. Horizontal mass, unlike the rest. */
function hulk() {
  const p = [];
  p.push(box([4.4, 3.4, 30], [0, 12.6, 0], [0.16, 0, 0], 1, 1));
  for (let i = 0; i < 7; i++) {
    const t = i / 6, z = -13 + t * 26;
    const rr = 6.6 - Math.abs(t - 0.42) * 6.0;
    const y = 12.6 - z * 0.16;
    for (const sx of [-1, 1]) {
      p.push(box([1.6, rr * 2.1, 2.0], [sx * rr * 0.72, y - rr * 0.55, z],
        [0, 0, sx * 0.62], i % 2 ? 0 : 1, i < 4 ? 1 : 0));
      p.push(box([1.4, rr * 1.5, 1.8], [sx * rr * 1.16, y - rr * 1.5, z],
        [0, 0, sx * 1.05], 1, 0));
    }
  }
  p.push(box([5.2, 4.6, 7.2], [0, 15.8, -16.5], [-0.30, 0, 0], 0, 1));
  p.push(cone(2.1, 5.6, 5, [0, 18.4, -19.6], [-1.4, 0, 0], 3, 0));
  p.push(ico(2.4, 0, [0, 10.4, -3.0], 3, 0));
  for (let i = 0; i < 4; i++) {
    const sx = i < 2 ? -1 : 1, sz = i % 2 ? 1 : -1;
    p.push(cyl(1.0, 1.6, 9.0, 6, [sx * 3.3, 4.5, sz * 9.0], [0, 0, sx * 0.14], 1, i < 2 ? 1 : 0));
  }
  return p;
}

/** A leaning lattice mast. The tallest thing in the world, and the cheapest. */
function mast() {
  const p = [];
  const H = 52, BASE = 5.6, TOP = 1.8, d = TOP - BASE;
  for (let i = 0; i < 4; i++) {
    const sx = i < 2 ? -1 : 1, sz = i % 2 ? 1 : -1;
    p.push(cyl(0.55, 1.6, H, 5, [sx * (BASE + d / 2), H / 2, sz * (BASE + d / 2)],
      [sz * d / H, 0, -sx * d / H], 0, 1));
  }
  const rings = [[9, 4.9], [22, 3.9], [36, 2.8], [46, 2.1]];
  for (let i = 0; i < rings.length; i++) {
    const [y, w] = rings[i];
    p.push(box([w * 2.2, 0.7, 0.7], [0, y, w], null, 1, i === 1 ? 1 : 0));
    p.push(box([w * 2.2, 0.7, 0.7], [0, y, -w], null, 1, i === 1 ? 1 : 0));
    p.push(box([0.7, 0.7, w * 2.2], [w, y, 0], null, 1, 0));
    p.push(box([0.7, 0.7, w * 2.2], [-w, y, 0], null, 1, 0));
  }
  p.push(box([23, 1.5, 1.6], [0, 49.5, 0], null, 3, 1));
  for (const sx of [-1, 1]) p.push(box([1.1, 5.2, 1.1], [sx * 10.6, 52.2, 0], null, 3, 0));
  p.push(ico(1.7, 0, [0, 53.6, 0], 2, 1));
  return p;
}

/**
 * The kind table. `height` and `radius` are the numbers the PLACEMENT pass
 * reads: apparent angular size is height/distance, and radius is the exclusion
 * disc that stops two landmarks merging into one blob on the horizon.
 *
 * `lean` tilts the whole recipe about its base — the difference between a
 * structure and a ruin.
 */
export const LANDMARK_KINDS = [
  { id: LK.MONOLITH, name: '立石', build: monolith, height: 24, radius: 15, tint: 'rock', lean: [0, 0], scale: [0.85, 1.55] },
  { id: LK.TOWER, name: '崩塔', build: tower, height: 38, radius: 12, tint: 'rock', lean: [0.012, -0.030], scale: [0.85, 1.45] },
  { id: LK.TORII, name: '大鳥居', build: torii, height: 24, radius: 16, tint: 'accent', lean: [0, 0.014], scale: [0.90, 1.60] },
  { id: LK.PAGODA, name: '五重塔', build: pagoda, height: 29, radius: 14, tint: 'soil', lean: [0.020, 0.026], scale: [0.90, 1.40] },
  { id: LK.DEADTREE, name: '枯大樹', build: deadTree, height: 32, radius: 14, tint: 'soil', lean: [0.014, 0.022], scale: [0.85, 1.50] },
  { id: LK.HULK, name: '機骸', build: hulk, height: 21, radius: 18, tint: 'rock', lean: [0.030, 0.055], scale: [0.90, 1.45] },
  { id: LK.MAST, name: '鉄塔', build: mast, height: 54, radius: 11, tint: 'rock', lean: [0.026, 0.075], scale: [0.80, 1.35] },
];

/**
 * Per-culture vocabulary. The three regions must NOT share a skyline: seeing a
 * lattice mast on the horizon should mean "that way is the rust waste" without
 * a map, a marker or a line of text. The small overlap weights are deliberate —
 * a hard partition would put a visible seam through the bleed zone that
 * regions.js works so hard to keep continuous.
 */
export const REGION_LANDMARKS = [
  [ // 灰王国 — megalith and masonry
    { kind: LK.MONOLITH, w: 4 }, { kind: LK.TOWER, w: 4 },
    { kind: LK.DEADTREE, w: 2 }, { kind: LK.MAST, w: 0.5 },
  ],
  [ // 霧ノ國 — timber, gates and tiered roofs
    { kind: LK.TORII, w: 4 }, { kind: LK.PAGODA, w: 3.5 },
    { kind: LK.DEADTREE, w: 2 }, { kind: LK.MONOLITH, w: 1 },
  ],
  [ // 銹の荒野 — iron bones
    { kind: LK.MAST, w: 4 }, { kind: LK.HULK, w: 4 },
    { kind: LK.TOWER, w: 1 }, { kind: LK.DEADTREE, w: 0.75 },
  ],
];

/**
 * The same vocabulary as a dense per-kind weight vector, one row per culture.
 *
 * Placement blends these rows by the CONTINUOUS region weights rather than
 * picking the dominant culture, for the same reason terrain.js blends its
 * palette: this generator's cultural lobes overlap heavily, and a hard
 * dominant-culture switch would put a visible vocabulary seam through a bleed
 * zone that is hundreds of metres wide.
 */
export const REGION_KIND_W = REGION_LANDMARKS.map((row) => {
  const v = new Float32Array(LANDMARK_KINDS.length);
  for (const e of row) v[e.kind] = e.w;
  return v;
});

/**
 * Composition rule constants.
 *
 * minAngular is the number that decides whether the whole system works: below
 * roughly 0.75 degrees a silhouette is a smudge, so a 24 m standing stone stops
 * counting past ~1.8 km while a 54 m mast still counts at 2 km. Placement is
 * therefore forced to put TALL things far away and is free to put small things
 * close — which is what composition actually means.
 */
export const COMPOSE = {
  sectors: 4,
  minR: 150,            // closer than this it is a prop, not a horizon element
  maxR: 2000,           // beyond this the fog has eaten it in all but clear weather
  minAngular: 0.013,    // radians of apparent height, ~0.75 degrees
  // Placement rings, in metres. Tuned to the continent this generator actually
  // makes: erosion plus the elliptical mask leaves roughly a 2.2 x 2.5 km
  // landmass, so a 1.5 km ring would spend most of its samples in the sea.
  placeRadii: [190, 310, 450, 620, 850],
  placeAngles: 9,       // samples across each 90-degree sector
  // A denser, more permissive second attempt, run only for sectors the first
  // pass failed. Most of those failures are crowding or an intervening ridge
  // rather than open sea, and a coarse search simply missed the gap.
  retryRadii: [170, 240, 320, 410, 510, 620, 740, 870, 1010, 1170, 1340, 1520],
  retryAngles: 22,
  retrySepMul: 0.60,
  retrySlopeNy: 0.800,
  minSeparation: 115,   // metres between two landmark centres
  maxSlopeNy: 0.855,    // ~31 degrees: buildable ground, not a cliff face
  seaClearance: 7,      // metres above sea level
  edgeMargin: 200,      // keep off the map border
  vantageCount: 36,
  vantageStride: 4,     // macro tiles between vantage grid samples (32 m)
  vantageRing: 3,       // neighbourhood radius, in grid cells, for prominence
  vantageSeparation: 200, // metres between accepted vantage points
  vantageMinNy: 0.70,   // a viewpoint you cannot stand on is not a viewpoint
  vantageMinProm: 0.010, // normalised: ~5 m above the local mean, or it is a hollow
};
