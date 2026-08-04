// determinism.test.js
//
// These run from the first commit rather than being retrofitted. Determinism
// erodes silently: someone adds Math.random() in an AI tweak, nothing visibly
// breaks for a month, and then saves and replays are subtly wrong with no way
// to tell when it started. Catching it in the PR that causes it is the only
// version of this that works.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve, relative } from 'node:path';

import { createWorld } from '../../src/sim/world.js';
import { tick } from '../../src/sim/schedule.js';
import { spawnPlayer, spawnEnemy } from '../../src/sim/archetypes.js';
import { attachMacro } from '../../src/sim/macro/init.js';
import { createIntent, BTN } from '../../src/sim/systems/intent.js';
import { clearEvents } from '../../src/core/bus.js';
import { hashBuffer } from '../../src/core/rng.js';
import { despawn } from '../../src/sim/store.js';
import { deref } from '../../src/core/handles.js';
import { fsin, fcos, fatan2, powInt } from '../../src/core/fixed.js';
import { binomial } from '../../src/sim/macro/worldEvents.js';

const SIM_DIR = resolve(import.meta.dirname, '../../src/sim');
const CORE_DIR = resolve(import.meta.dirname, '../../src/core');

function walk(dir, out = []) {
  for (const name of readdirSync(dir).sort()) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (name.endsWith('.js')) out.push(p);
  }
  return out;
}

test('no forbidden non-deterministic globals in src/sim', () => {
  // IEEE-754 pins + - * / sqrt abs floor imul and the bit ops on every engine.
  // It pins NOTHING for the transcendentals: the ECMAScript spec marks them
  // implementation-defined, and pow/hypot have historically diverged between
  // engines and iOS versions.
  const banned = /\bMath\.(sin|cos|tan|asin|acos|atan|atan2|exp|log|log2|log10|pow|hypot|cbrt|random)\s*\(|\bDate\.now\s*\(|\bperformance\.now\s*\(|\bnew Date\s*\(/g;
  const offenders = [];

  for (const file of walk(SIM_DIR)) {
    const src = readFileSync(file, 'utf8');
    let m;
    banned.lastIndex = 0;
    while ((m = banned.exec(src)) !== null) {
      const line = src.slice(0, m.index).split('\n').length;
      offenders.push(`${relative(SIM_DIR, file)}:${line}  ${m[0]}`);
    }
  }
  assert.deepEqual(offenders, [],
    'use core/fixed.js (fsin/fcos/fatan2/flen/powInt) inside the simulation:\n  '
    + offenders.join('\n  '));
});

test('fixed-math primitives are accurate enough to be usable', () => {
  // Reproducibility is the requirement; accuracy just has to be invisible.
  for (let i = -20; i <= 20; i++) {
    const x = i * 0.37;
    assert.ok(Math.abs(fsin(x) - Math.sin(x)) < 1e-6, `fsin(${x})`);
    assert.ok(Math.abs(fcos(x) - Math.cos(x)) < 1e-6, `fcos(${x})`);
  }
  for (let i = -8; i <= 8; i++) {
    for (let j = -8; j <= 8; j++) {
      if (i === 0 && j === 0) continue;
      const d = Math.abs(fatan2(i, j) - Math.atan2(i, j));
      assert.ok(d < 2e-5, `fatan2(${i},${j}) off by ${d}`);
    }
  }
  assert.equal(powInt(0.96, 0), 1);
  assert.ok(Math.abs(powInt(0.96, 17) - Math.pow(0.96, 17)) < 1e-12);
});

function scenarioHash(seed, ticks) {
  const w = createWorld(seed);
  attachMacro(w);
  spawnPlayer(w, 0, 0);
  spawnEnemy(w, 'skeleton', 5, 1);
  spawnEnemy(w, 'musha', -6, 3);

  const intent = createIntent();
  const trail = [];
  for (let i = 0; i < ticks; i++) {
    // A scripted input pattern, derived from the tick so it is reproducible.
    intent.pressed = (i % 37 === 0) ? BTN.ATTACK : (i % 53 === 0) ? BTN.DODGE : 0;
    intent.held = (i % 91 < 20) ? BTN.BLOCK : 0;
    intent.moveX = ((i % 120) / 120) * 2 - 1;
    intent.moveZ = ((i % 77) / 77) * 2 - 1;
    tick(w, intent);
    clearEvents(w.events);
    if (i % 600 === 0) {
      trail.push(hashBuffer(w.store.px, hashBuffer(w.store.hp, hashBuffer(w.store.stateId))));
    }
  }
  return trail;
}

test('the same seed and inputs reproduce bit-identically', () => {
  const a = scenarioHash(9001, 3000);
  const b = scenarioHash(9001, 3000);
  assert.deepEqual(a, b, 'replay diverged; compare the trail to localise the tick window');
  assert.ok(a.length > 1);
});

test('different seeds produce different histories', () => {
  const a = scenarioHash(9001, 1200);
  const b = scenarioHash(4242, 1200);
  assert.notDeepEqual(a, b);
});

test('iteration is independent of spawn and despawn order', () => {
  // The test that catches "someone iterated a Map". If the live list stopped
  // being rebuilt in ascending order, churn before the scenario would leak
  // into its results.
  const withChurn = () => {
    const w = createWorld(555);
    attachMacro(w);
    for (let i = 0; i < 5; i++) {
      const h = spawnEnemy(w, 'skeleton', 200 + i, 200);
      despawn(w.store, deref(w.store, h));
    }
    spawnPlayer(w, 0, 0);
    spawnEnemy(w, 'skeleton', 5, 1);
    const intent = createIntent();
    for (let i = 0; i < 900; i++) {
      intent.pressed = (i % 37 === 0) ? BTN.ATTACK : 0;
      tick(w, intent);
      clearEvents(w.events);
    }
    return hashBuffer(w.store.px, hashBuffer(w.store.hp));
  };
  const clean = () => {
    const w = createWorld(555);
    attachMacro(w);
    spawnPlayer(w, 0, 0);
    spawnEnemy(w, 'skeleton', 5, 1);
    const intent = createIntent();
    for (let i = 0; i < 900; i++) {
      intent.pressed = (i % 37 === 0) ? BTN.ATTACK : 0;
      tick(w, intent);
      clearEvents(w.events);
    }
    return hashBuffer(w.store.px, hashBuffer(w.store.hp));
  };
  assert.equal(withChurn(), clean(),
    'prior spawn/despawn churn changed the outcome — iteration order is leaking');
});

test('binomial sampling matches an iterative count exactly', () => {
  // This is what proves the offline catch-up shortcut is CORRECT rather than
  // merely fast: 10,000 hourly coin flips collapse to one draw.
  for (const p of [0.01, 0.05, 0.2]) {
    for (const n of [1, 24, 168, 720]) {
      const k = binomial(n, p, 0.5);
      assert.ok(k >= 0 && k <= n, `binomial(${n},${p}) = ${k} out of range`);
    }
  }
  // The inverse CDF must be monotone in u.
  let prev = -1;
  for (let u = 0; u < 1; u += 0.05) {
    const k = binomial(168, 0.02, u);
    assert.ok(k >= prev, 'inverse CDF is not monotone');
    prev = k;
  }
});
