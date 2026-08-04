// defects.test.js — regression tests for the gaps found in the first audit.
//
// Every one of these covers something that was DECLARED in data but had no
// implementation behind it. That failure mode is invisible in play — the move
// simply does nothing — so it needs a test rather than a play-through.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createWorld } from '../../src/sim/world.js';
import { tick } from '../../src/sim/schedule.js';
import { spawnPlayer, spawnEnemy } from '../../src/sim/archetypes.js';
import { attachMacro } from '../../src/sim/macro/init.js';
import { createIntent } from '../../src/sim/systems/intent.js';
import { deref, NULL_HANDLE } from '../../src/core/handles.js';
import { C, S, ACT, BTN } from '../../src/sim/components.js';
import { clearEvents } from '../../src/core/bus.js';
import { PARRY, TARGETING, LOCOMOTION } from '../../src/data/tuning.js';
import { ATTACKS } from '../../src/data/attacks.js';
import { observePlayer, createHabitModel, habitBias } from '../../src/sim/ops/habits.js';
import { EV } from '../../src/data/events.js';
import { applyHit } from '../../src/sim/ops/damage.js';
import { startAttack } from '../../src/sim/ops/attack.js';

function newWorld(seed = 777) {
  const w = createWorld(seed);
  attachMacro(w);
  // A flat floor keeps these tests about combat rather than terrain.
  w.terrain = { heightAt: () => 0, normalAt: (x, z, o) => o, isWater: () => false, seaLevel: -100 };
  return w;
}
const run = (w, n, intent = null) => {
  for (let i = 0; i < n; i++) { tick(w, intent); clearEvents(w.events); }
};

test('a parry arms the riposte window and the riposte outdamages a light hit', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  const eh = spawnEnemy(w, 'skeleton', 1.5, 0);
  const e = deref(w.store, eh);

  // Force a parry by hand rather than waiting for the AI to swing.
  w.store.stateId[p] = S.PARRY;
  w.store.phaseT[p] = 0;
  w.store.yaw[p] = 0;                       // facing +x, toward the enemy
  applyHit(w, e, p, ATTACKS.skeleton_slash, -1, 0);

  assert.equal(w.store.riposteT[p], PARRY.riposteWindowTicks,
    'a successful parry did not arm the riposte window');

  // Cash it.
  w.store.stateId[p] = S.IDLE;
  w.store.phaseT[p] = 0;
  const intent = createIntent();
  intent.pressed = BTN.ATTACK;
  tick(w, intent);

  assert.equal(w.store.attackId[p], ATTACKS.player_riposte.index,
    'attacking inside the riposte window produced an ordinary light attack');
  assert.equal(w.store.riposteT[p], 0, 'the window was not consumed');
  assert.ok(ATTACKS.player_riposte.dmg.base > ATTACKS.player_light_1.dmg.base * 2,
    'the riposte is not worth the parry');
});

test('a ranged attack actually spawns a projectile that travels', () => {
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'machine_stag', 12, 0);
  const e = deref(w.store, eh);

  const before = w.store.count;
  w.store.yaw[e] = Math.PI;                 // aim back at the player
  startAttack(w, e, 'machine_beam');

  // Run through the wind-up into the active frames.
  run(w, ATTACKS.machine_beam.windup + 3);
  assert.ok(w.store.count > before, 'no projectile entity was created');

  // Find it and confirm it is moving.
  let proj = -1;
  for (let k = 0; k < w.store.liveN; k++) {
    const i = w.store.live[k];
    if (w.store.mask[i] & C.PROJECTILE) { proj = i; break; }
  }
  assert.notEqual(proj, -1, 'projectile component never set');
  const x0 = w.store.px[proj];
  run(w, 3);
  assert.notEqual(w.store.px[proj], x0, 'the projectile did not move');
});

test('projectiles expire rather than leaking entities', () => {
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'machine_stag', 40, 0);
  const baseline = w.store.count;
  const e = deref(w.store, eh);
  w.store.yaw[e] = 0;                        // fire away from everyone
  startAttack(w, e, 'machine_beam');
  run(w, 60 * 4);
  assert.equal(w.store.count, baseline, 'projectile entities leaked');
});

test('lock-on selects a target and releases it on a second press', () => {
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'skeleton', 6, 0);

  const intent = createIntent();
  intent.pressed = BTN.LOCK;
  tick(w, intent);
  assert.notEqual(w.lockTarget, NULL_HANDLE, 'lock-on did not acquire a target');
  assert.equal(deref(w.store, w.lockTarget), deref(w.store, eh));

  clearEvents(w.events);
  intent.pressed = BTN.LOCK;
  tick(w, intent);
  assert.equal(w.lockTarget, NULL_HANDLE, 'a second press did not release the lock');
});

test('lock-on drops a target that dies', () => {
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'skeleton', 6, 0);
  const intent = createIntent();
  intent.pressed = BTN.LOCK;
  tick(w, intent);
  assert.notEqual(w.lockTarget, NULL_HANDLE);

  w.store.stateId[deref(w.store, eh)] = S.DEAD;
  intent.pressed = 0;
  tick(w, intent);
  assert.equal(w.lockTarget, NULL_HANDLE, 'the lock survived the target dying');
});

test('footstep cadence tracks distance travelled, not the tick counter', () => {
  // The original implementation used (tick * speed) % 42, which makes stride
  // rate a function of time rather than motion.
  const stepsAtSpeed = (speed) => {
    const w = newWorld();
    const ph = spawnPlayer(w, 0, 0);
    const p = deref(w.store, ph);
    let steps = 0;
    for (let i = 0; i < 600; i++) {
      w.store.vx[p] = speed; w.store.vz[p] = 0;
      tick(w, null);
      for (let k = 0; k < w.events.n; k++) if (w.events.type[k] === EV.STEP) steps++;
      clearEvents(w.events);
    }
    return steps;
  };
  const slow = stepsAtSpeed(2.0);
  const fast = stepsAtSpeed(6.0);
  assert.ok(slow > 0 && fast > 0, 'no footsteps were emitted at all');
  // Three times the speed over the same duration must produce materially more
  // footfalls — but fewer than 3x, because stride lengthens with speed.
  assert.ok(fast > slow * 1.5, `cadence did not scale with speed (${slow} -> ${fast})`);
  assert.ok(fast < slow * 3.0, `stride length did not grow with speed (${slow} -> ${fast})`);
});

test('the habit model accumulates and shifts move weights', () => {
  const w = newWorld();
  w.habits = createHabitModel();
  const atkLongWindup = ATTACKS.skeleton_sweep;

  assert.equal(habitBias(w.habits, 'skeleton', atkLongWindup), 1,
    'the model should stay neutral before it has samples');

  for (let i = 0; i < 40; i++) observePlayer(w, ACT.DODGE, 'skeleton', 0.2);
  assert.ok(w.habits.skeleton.samples >= 40, 'observations were not recorded');
  assert.ok(habitBias(w.habits, 'skeleton', atkLongWindup) > 1,
    'an early-dodging player did not shift the AI toward long wind-ups');
});

test('habit counts stay bounded over a long session', () => {
  const w = newWorld();
  w.habits = createHabitModel();
  for (let i = 0; i < 5000; i++) observePlayer(w, ACT.BLOCK, 'skeleton', 0.5);
  assert.ok(w.habits.skeleton.samples <= 400,
    `habit counters grew unbounded (${w.habits.skeleton.samples})`);
});
