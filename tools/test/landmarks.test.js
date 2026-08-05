// landmarks.test.js — "every horizon must contain a silhouette", as an assertion.
//
// The composition rule is the only thing separating this system from scattered
// props, and it is exactly the kind of rule that decays silently: someone
// raises the slope limit, six sectors quietly lose their landmark, and nobody
// notices until a play session three weeks later feels aimless. So the rule is
// checked here by a verifier that shares no code path with the placer — the
// placer records what it BELIEVES it covered, and sectorReport() independently
// re-derives coverage from the finished list plus the terrain.
//
// Deliberately sim-layer only: no three.js, no GPU. The rule is geometry.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildMacroFieldSync } from '../../src/sim/world/macrofield.js';
import {
  installMacroField, sampleHeight, sampleNormal, seaLevelMetres,
  isRiverTile, TILE, WORLD_SIZE,
} from '../../src/sim/world/heightfield.js';
import {
  placeLandmarks, findVantagePoints, sectorReport, sectorOf, SECTORS,
} from '../../src/sim/world/vantage.js';
import { LANDMARK_KINDS, COMPOSE } from '../../src/data/landmarks.js';

const SEED = 20260804;
installMacroField(buildMacroFieldSync(SEED), SEED);

const first = placeLandmarks(SEED);
const { landmarks, vantages } = first;

// ————————————————————————————————— the grammar

test('every kind produces a silhouette with real vertical extent', () => {
  for (const k of LANDMARK_KINDS) {
    const parts = k.build();
    assert.ok(parts.length >= 4, `${k.name}: ${parts.length} parts is not a silhouette`);
    assert.ok(parts.some((p) => p.far), `${k.name}: nothing survives into the far LOD`);

    // The far LOD must be a real reduction, or the distance LOD is a lie.
    const far = parts.filter((p) => p.far).length;
    assert.ok(far < parts.length, `${k.name}: far LOD keeps every part`);

    // Declared height has to match the geometry, since PLACEMENT sizes the
    // apparent angle from it: overstate it and landmarks vanish at range.
    let top = 0;
    for (const p of parts) {
      const h = p.p === 0 ? p.s[1] : p.p === 1 ? p.s[2] : p.p === 2 ? p.s[1] : p.s[0] * 2;
      top = Math.max(top, p.t[1] + h / 2);
    }
    assert.ok(top > k.height * 0.75 && top < k.height * 1.4,
      `${k.name}: declared height ${k.height} but geometry tops out at ${top.toFixed(1)}`);
    assert.ok(k.height >= 20, `${k.name}: under 20 m never reads at a kilometre`);
  }
});

test('a 24 m landmark still subtends the legibility floor at a kilometre', () => {
  // This is the number the whole system rests on. If minAngular ever creeps up,
  // the shortest kind stops qualifying anywhere useful and placement starves.
  const shortest = Math.min(...LANDMARK_KINDS.map((k) => k.height * k.scale[0]));
  assert.ok(shortest / 1000 >= COMPOSE.minAngular,
    `the shortest landmark (${shortest.toFixed(0)} m) fails the legibility floor at 1 km`);
});

// ————————————————————————————————— determinism

test('placement is deterministic from the seed', () => {
  const again = placeLandmarks(SEED);
  assert.equal(again.landmarks.length, landmarks.length);
  for (let i = 0; i < landmarks.length; i++) {
    const a = landmarks[i], b = again.landmarks[i];
    assert.equal(a.kind, b.kind, `kind diverged at ${i}`);
    assert.equal(a.x, b.x, `x diverged at ${i}`);
    assert.equal(a.z, b.z, `z diverged at ${i}`);
    assert.equal(a.y, b.y, `y diverged at ${i}`);
    assert.equal(a.scale, b.scale, `scale diverged at ${i}`);
    assert.equal(a.rot, b.rot, `rot diverged at ${i}`);
  }
  assert.deepEqual(again.vantages.map((v) => [v.x, v.z, v.covered]),
    vantages.map((v) => [v.x, v.z, v.covered]));
});

test('vantage extraction is deterministic and independent of placement', () => {
  const a = findVantagePoints(SEED);
  const b = findVantagePoints(SEED);
  assert.deepEqual(a.map((v) => [v.x, v.z]), b.map((v) => [v.x, v.z]));
  assert.deepEqual(a.map((v) => [v.x, v.z]), vantages.map((v) => [v.x, v.z]));
});

test('a different seed produces a different composition', () => {
  const other = 4242;
  installMacroField(buildMacroFieldSync(other), other);
  const o = placeLandmarks(other);
  assert.ok(o.landmarks.length > 0);
  const same = o.landmarks.length === landmarks.length
    && o.landmarks.every((l, i) => l.x === landmarks[i].x && l.z === landmarks[i].z);
  assert.ok(!same, 'two seeds produced the same landmark layout');
  // Restore the field this file's other tests share.
  installMacroField(buildMacroFieldSync(SEED), SEED);
});

// ————————————————————————————————— siting

test('nothing is drowned, on a cliff, in a river, or off the edge', () => {
  const sea = seaLevelMetres();
  const n = new Float32Array(3);
  for (const l of landmarks) {
    assert.ok(l.y > sea, `landmark ${l.index} at y=${l.y.toFixed(1)} is under water`);
    // Bases settle DOWN into sloped ground so nothing floats, but only ever
    // down, and never far enough to swallow the silhouette.
    const surface = sampleHeight(l.x, l.z, SEED);
    assert.ok(surface >= sea + COMPOSE.seaClearance - 0.001,
      `landmark ${l.index} stands on ground below sea level + clearance`);
    assert.ok(l.y <= surface + 1e-6, `landmark ${l.index} floats above the heightfield`);
    assert.ok(l.y >= surface - l.height * 0.27,
      `landmark ${l.index} is buried ${(surface - l.y).toFixed(1)} m into the hillside`);
    assert.ok(!isRiverTile(l.x / TILE, l.z / TILE, SEED),
      `landmark ${l.index} is standing in a river`);
    sampleNormal(l.x, l.z, SEED, n, 4);
    assert.ok(n[1] >= COMPOSE.retrySlopeNy - 0.001,
      `landmark ${l.index} is on a ${(Math.acos(n[1]) * 57.3).toFixed(0)}-degree slope`);
    const m = COMPOSE.edgeMargin;
    assert.ok(l.x >= m && l.z >= m && l.x <= WORLD_SIZE - m && l.z <= WORLD_SIZE - m,
      `landmark ${l.index} is off the edge of the world`);
  }
});

test('no two silhouettes merge into one blob', () => {
  const floor = COMPOSE.minSeparation * COMPOSE.retrySepMul - 0.001;
  for (let i = 0; i < landmarks.length; i++) {
    for (let j = i + 1; j < landmarks.length; j++) {
      const dx = landmarks[i].x - landmarks[j].x, dz = landmarks[i].z - landmarks[j].z;
      assert.ok(Math.sqrt(dx * dx + dz * dz) >= floor,
        `landmarks ${i} and ${j} are ${Math.sqrt(dx * dx + dz * dz).toFixed(0)} m apart`);
    }
  }
});

test('the world is composed, not covered — the count stays in budget', () => {
  // Too few and the horizon is empty; too many and it is wallpaper, and the
  // instanced draw budget starts to matter.
  assert.ok(landmarks.length >= 12, `only ${landmarks.length} landmarks placed`);
  assert.ok(landmarks.length <= 220, `${landmarks.length} landmarks is scatter, not composition`);
  assert.ok(vantages.length >= 8, `only ${vantages.length} vantage points found`);
});

// ————————————————————————————————— THE COMPOSITION RULE

test('sectorOf partitions the circle into four equal quadrants', () => {
  const seen = new Set();
  for (let i = 0; i < 360; i++) {
    const a = (i / 360) * Math.PI * 2;
    const s = sectorOf(Math.cos(a) * 500, Math.sin(a) * 500);
    assert.ok(s >= 0 && s < SECTORS, `bearing ${i} gave sector ${s}`);
    seen.add(s);
  }
  assert.equal(seen.size, SECTORS, 'some quadrant is unreachable');
});

test('from every vantage point, every sector the placer claimed really is covered', () => {
  // The load-bearing test. sectorReport() re-derives coverage from the finished
  // list and the terrain, so a placer that mis-measures visibility fails here
  // rather than shipping a horizon that is empty in one direction.
  const failures = [];
  for (const v of vantages) {
    const r = sectorReport(landmarks, v.x, v.z, SEED, v.eyeY);
    for (let s = 0; s < SECTORS; s++) {
      if ((v.land & (1 << s)) && !r.covered[s]) {
        failures.push(`(${v.x},${v.z}) sector ${s}`);
      }
    }
  }
  assert.deepEqual(failures, [],
    'the placer believes these sectors are covered but the verifier disagrees');
});

/**
 * An INDEPENDENT re-implementation of "can a landmark here be seen from there".
 *
 * The placer marches the macro field for speed. This marches the full-detail
 * sampleHeight instead — different data, different code — so the two agreeing
 * is evidence rather than tautology.
 */
function siteWouldBeVisible(ex, ez, eyeY, x, z, topY, d) {
  const need = (topY - eyeY) / d;
  const steps = Math.max(6, Math.min(40, Math.round(d / 50)));
  for (let i = 1; i < steps; i++) {
    const t = i / steps;
    const h = sampleHeight(ex + (x - ex) * t, ez + (z - ez) * t, SEED);
    if ((h - eyeY) / (d * t) >= need) return false;
  }
  return true;
}

test('the uncovered sectors had nowhere to put anything', () => {
  // A hilltop with a quadrant of open sea, or one walled off by a higher ridge,
  // is the ONLY excuse the composition rule accepts. So sweep every uncovered
  // sector five times more densely than the placer does and assert it holds
  // essentially nowhere a landmark could stand AND be seen. If a dense sweep
  // finds plenty of sites, the placer simply gave up — that is the bug this
  // test exists to catch.
  const sea = seaLevelMetres();
  const n = new Float32Array(3);
  const tallest = Math.max(...LANDMARK_KINDS.map((k) => k.height * k.scale[1]));
  const crowd = (COMPOSE.minSeparation * COMPOSE.retrySepMul) ** 2;

  for (const v of vantages) {
    for (let s = 0; s < SECTORS; s++) {
      if (v.land & (1 << s)) continue;
      let sites = 0;
      for (let ai = 0; ai < 24; ai++) {
        for (let ri = 0; ri < 16; ri++) {
          const a = (s + (ai + 0.5) / 24) * (Math.PI / 2);
          const d = COMPOSE.minR + 50 + ri * 62;
          const x = v.x + Math.cos(a) * d, z = v.z + Math.sin(a) * d;
          if (x < COMPOSE.edgeMargin || z < COMPOSE.edgeMargin) continue;
          if (x > WORLD_SIZE - COMPOSE.edgeMargin || z > WORLD_SIZE - COMPOSE.edgeMargin) continue;
          const y = sampleHeight(x, z, SEED);
          if (y < sea + COMPOSE.seaClearance) continue;
          if (isRiverTile(x / TILE, z / TILE, SEED)) continue;
          sampleNormal(x, z, SEED, n, 4);
          if (n[1] < COMPOSE.retrySlopeNy) continue;
          if (landmarks.some((o) => (o.x - x) ** 2 + (o.z - z) ** 2 < crowd)) continue;
          if (!siteWouldBeVisible(v.x, v.z, v.eyeY, x, z, y + tallest, d)) continue;
          sites++;
        }
      }
      assert.ok(sites <= 12,
        `vantage (${v.x},${v.z}) sector ${s} has ${sites} valid, visible sites ` +
        `in a 384-sample sweep and still got no landmark — placement gave up`);
    }
  }
});

test('most sectors of most vantages hold a silhouette', () => {
  let covered = 0;
  for (const v of vantages) {
    const r = sectorReport(landmarks, v.x, v.z, SEED, v.eyeY);
    for (let s = 0; s < SECTORS; s++) if (r.covered[s]) covered++;
  }
  const ratio = covered / (vantages.length * SECTORS);
  assert.ok(ratio >= 0.75,
    `only ${(ratio * 100).toFixed(0)}% of vantage sectors have a silhouette`);
});

test('ordinary walkable ground can see something too', () => {
  // Vantages are where the rule is enforced; this checks it generalises off the
  // hilltops, which is what stops the placer degenerating into four rings of
  // landmarks around a handful of summits.
  const sea = seaLevelMetres();
  let n = 0, sum = 0, blind = 0;
  for (let i = 1; n < 120 && i < 40000; i++) {
    const x = ((i * 7919) % (WORLD_SIZE - 1200)) + 600;
    const z = ((i * 104729) % (WORLD_SIZE - 1200)) + 600;
    if (sampleHeight(x, z, SEED) < sea + 3) continue;
    const r = sectorReport(landmarks, x, z, SEED);
    for (let s = 0; s < SECTORS; s++) if (r.covered[s]) sum++;
    if (r.count === 0) blind++;
    n++;
  }
  assert.ok(n > 40, `only found ${n} dry sample positions`);
  assert.ok(sum / n >= 2.0,
    `average land position sees only ${(sum / n).toFixed(2)} of ${SECTORS} sectors filled`);
  assert.ok(blind / n <= 0.12,
    `${((blind / n) * 100).toFixed(0)}% of land positions can see no landmark at all`);
});

test('the verifier agrees with itself and reports a usable best-in-sector', () => {
  const v = vantages[0];
  const a = sectorReport(landmarks, v.x, v.z, SEED, v.eyeY);
  const b = sectorReport(landmarks, v.x, v.z, SEED, v.eyeY);
  assert.deepEqual(a.covered, b.covered);
  assert.equal(a.count, b.count);
  for (let s = 0; s < SECTORS; s++) {
    if (!a.covered[s]) { assert.equal(a.best[s], null); continue; }
    const l = a.best[s];
    assert.equal(sectorOf(l.x - v.x, l.z - v.z), s, 'best landmark is in the wrong sector');
    assert.ok(a.angles[s] >= COMPOSE.minAngular, 'reported a sub-threshold silhouette');
  }
});
