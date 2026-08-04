// sim.test.js — the simulation runs headless in Node with zero shims.
//
// This suite existing at all is the payoff for the layering rule: src/sim/
// never imports src/platform/, so there is no DOM to fake. If someone breaks
// that rule, these tests fail at import time rather than subtly later.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createWorld } from '../../src/sim/world.js';
import { tick } from '../../src/sim/schedule.js';
import { spawnPlayer, spawnEnemy } from '../../src/sim/archetypes.js';
import { attachMacro } from '../../src/sim/macro/init.js';
import { createIntent, BTN } from '../../src/sim/systems/intent.js';
import { deref } from '../../src/core/handles.js';
import { S, ACT } from '../../src/sim/components.js';
import { DODGE, STAMINA, PARRY } from '../../src/data/tuning.js';
import { clearEvents } from '../../src/core/bus.js';
import { findNaN, despawn } from '../../src/sim/store.js';
import { isInvulnerable } from '../../src/sim/ops/damage.js';
import { bufferPush, bufferHas } from '../../src/sim/ops/buffer.js';
import { enterHurt } from '../../src/sim/ops/damage.js';

function newWorld(seed = 4242) {
  const w = createWorld(seed);
  attachMacro(w);
  return w;
}

function run(w, ticks, intent = null) {
  for (let i = 0; i < ticks; i++) {
    tick(w, intent);
    clearEvents(w.events);
  }
}

test('world boots and ticks without NaN', () => {
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  spawnEnemy(w, 'skeleton', 4, 0);
  run(w, 600);
  assert.equal(findNaN(w.store), null);
  assert.equal(w.tick, 600);
});

test('dodge i-frames are exactly the declared count, and contiguous', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);

  const intent = createIntent();
  intent.pressed = BTN.DODGE;
  tick(w, intent);
  intent.pressed = 0;

  // Sample the way applyHit does, mid-tick semantics included.
  const invulnerable = [];
  for (let f = 0; f < DODGE.totalTicks + 4; f++) {
    invulnerable.push(isInvulnerable(w.store, p));
    tick(w, intent);
    clearEvents(w.events);
  }
  const first = invulnerable.indexOf(true);
  const last = invulnerable.lastIndexOf(true);
  const count = last - first + 1;
  assert.ok(first >= 0, 'dodge produced no invulnerable frames');
  // EXACT, not a lower bound. The previous `>= 14` passed against a buggy 16
  // and would never have caught this drifting again.
  assert.equal(count, DODGE.iframeFrames,
    `window was ${count} frames, expected ${DODGE.iframeFrames}`);
  for (let i = first; i <= last; i++) {
    assert.equal(invulnerable[i], true, `gap in the i-frame window at ${i}`);
  }
});

test('dodge costs exactly its stamina and arms the regen delay', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  const before = w.store.stamina[p];

  const intent = createIntent();
  intent.pressed = BTN.DODGE;
  tick(w, intent);

  assert.equal(w.store.stamina[p], before - DODGE.stamina);
  // The stamina system runs later in the same tick and ages the delay by one,
  // so the observable value is delay-1. Asserting the exact constant here would
  // be asserting tick order, not behaviour.
  assert.equal(w.store.staRegenDelay[p], STAMINA.delayAfterSpend - 1);
});

test('stamina regen is blocked for the delay then resumes', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  const intent = createIntent();
  intent.pressed = BTN.DODGE;
  tick(w, intent);
  intent.pressed = 0;

  const afterSpend = w.store.stamina[p];
  run(w, STAMINA.delayAfterSpend - 2, intent);
  assert.equal(w.store.stamina[p], afterSpend, 'regenerated during the lockout');

  run(w, 30, intent);
  assert.ok(w.store.stamina[p] > afterSpend, 'never resumed regenerating');
});

test('hitstop freezes motion but never freezes cooldowns or stamina regen', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);

  w.store.stamina[p] = 40;
  w.store.staRegenDelay[p] = 0;
  w.hitstopT = 10;

  const before = w.store.stamina[p];
  run(w, 5);
  // motionScale is 0, yet stamina — which runs on the raw clock — still moved.
  assert.equal(w.motionScale, 0);
  assert.ok(w.store.stamina[p] > before,
    'stamina regen stalled during hitstop; the three-clock split is broken');
});

test('a light attack connects and deals damage', () => {
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'skeleton', 1.6, 0);
  const e = deref(w.store, eh);
  const hpBefore = w.store.hp[e];

  const intent = createIntent();
  intent.pressed = BTN.ATTACK;
  tick(w, intent);
  intent.pressed = 0;
  run(w, 40, intent);

  assert.ok(w.store.hp[e] < hpBefore, 'the swing never landed');
  assert.ok(w.stats.hits > 0);
});

test('recovery after a connecting hit is shorter than after a whiff', () => {
  // The hit-confirm reward: 30% shorter recovery on contact. This single rule
  // does more for how aggressive the game feels than any other number.
  const measure = (withTarget) => {
    const w = newWorld();
    spawnPlayer(w, 0, 0);
    if (withTarget) {
      const eh = spawnEnemy(w, 'skeleton', 1.6, 0);
      // Keep the dummy passive: an enemy that swings back would put the player
      // into HURT and we would be timing that instead of the recovery rule.
      w.store.cooldown[deref(w.store, eh)] = 100000;
    }
    const p = deref(w.store, w.player);
    const intent = createIntent();
    intent.pressed = BTN.ATTACK;
    tick(w, intent);
    intent.pressed = 0;
    let t = 1;
    while (w.store.stateId[p] !== S.IDLE && t < 300) { tick(w, intent); clearEvents(w.events); t++; }
    return t;
  };
  const hit = measure(true);
  const whiff = measure(false);
  assert.ok(hit < whiff, `hit recovery ${hit} was not shorter than whiff ${whiff}`);
});

test('the input buffer drops a third identical action and clears on hit-stun', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);

  bufferPush(w.store, p, ACT.ATTACK);
  bufferPush(w.store, p, ACT.ATTACK);
  bufferPush(w.store, p, ACT.ATTACK);
  let queued = 0;
  for (let k = 0; k < 4; k++) if (w.store.bufAct[p * 4 + k] === ACT.ATTACK) queued++;
  assert.equal(queued, 2, 'the buffer accepted more than two of the same action');

  enterHurt(w, p, 20);
  assert.equal(bufferHas(w.store, p, ACT.ATTACK), false,
    'a buffered action survived hit-stun');
});

test('entities are freed and the store returns to baseline', () => {
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const baseline = w.store.count;
  for (let i = 0; i < 200; i++) {
    const h = spawnEnemy(w, 'skeleton', 10 + (i % 7), i % 5);
    run(w, 1);
    despawn(w.store, deref(w.store, h));
  }
  run(w, 5);
  assert.equal(w.store.count, baseline, 'entity slots leaked');
});
