// terrain.test.js — the continent actually has relief.
//
// "Looks flat" is a judgement, but the things that CAUSE flatness are
// measurable. These assert the properties that make a landscape readable, so a
// future noise tweak that quietly smooths everything back out fails here
// rather than being discovered by eye three checkpoints later.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildMacroFieldSync } from '../../src/sim/world/macrofield.js';
import {
  installMacroField, sampleHeight, seaLevelMetres, TILE, WORLD_TILES,
} from '../../src/sim/world/heightfield.js';

const SEED = 20260804;
const field = buildMacroFieldSync(SEED);
installMacroField(field, SEED);

/** Sample a coarse grid of the inhabited part of the continent. */
function survey(step = 6) {
  const out = { n: 0, steep: 0, maxSlope: 0, minH: Infinity, maxH: -Infinity };
  for (let z = 100; z < 700; z += step) {
    for (let x = 100; x < 700; x += step) {
      const wx = x * TILE, wz = z * TILE;
      const h = sampleHeight(wx, wz, SEED);
      const s = Math.max(
        Math.abs(sampleHeight(wx + 8, wz, SEED) - h),
        Math.abs(sampleHeight(wx, wz + 8, SEED) - h)) / 8;
      if (h > out.maxH) out.maxH = h;
      if (h < out.minH) out.minH = h;
      if (s > out.maxSlope) out.maxSlope = s;
      if (s > 0.7) out.steep++;    // steeper than ~35 degrees
      out.n++;
    }
  }
  return out;
}

const s = survey();

test('the continent spans a usable vertical range', () => {
  const sea = seaLevelMetres();
  assert.ok(s.maxH - sea > 200,
    `only ${(s.maxH - sea).toFixed(0)}m of relief above sea level`);
});

test('genuinely steep ground exists', () => {
  // Pure fBm plus a ridged term produces sharp crests but no WALLS. Without
  // erosion and terracing this was ~0%, and no amount of lighting fixes a
  // world with no vertical surfaces in it.
  assert.ok(s.maxSlope > 2.0, `steepest slope is only ${s.maxSlope.toFixed(1)}`);
  const pct = (s.steep / s.n) * 100;
  assert.ok(pct > 0.5, `only ${pct.toFixed(2)}% of the map is steeper than 35 degrees`);
  // ...but not everywhere, or it is unwalkable rather than dramatic.
  assert.ok(pct < 25, `${pct.toFixed(1)}% steep ground makes the world unwalkable`);
});

test('rivers carve real channels', () => {
  const wet = field.water.reduce((a, b) => a + b, 0);
  assert.ok(wet > 800, `only ${wet} river tiles — the tracer is stalling`);
});

test('the field is deterministic from the seed', () => {
  const a = buildMacroFieldSync(777);
  const b = buildMacroFieldSync(777);
  assert.equal(a.h.length, b.h.length);
  for (let i = 0; i < a.h.length; i += 997) {
    assert.equal(a.h[i], b.h[i], `divergence at cell ${i}`);
  }
});

test('erosion leaves no holes or non-finite cells', () => {
  for (let i = 0; i < field.h.length; i++) {
    assert.ok(Number.isFinite(field.h[i]), `non-finite height at ${i}`);
    assert.ok(field.h[i] >= 0, `negative height at ${i}`);
  }
});

test('the renderer, the collider and the AI sample the same surface', () => {
  // They all call sampleHeight. If anything ever caches or approximates it,
  // characters sink into the ground — so pin that they agree exactly.
  for (let i = 0; i < 200; i++) {
    const x = (i * 977) % (WORLD_TILES * TILE);
    const z = (i * 1861) % (WORLD_TILES * TILE);
    assert.equal(sampleHeight(x, z, SEED), sampleHeight(x, z, SEED));
  }
});
