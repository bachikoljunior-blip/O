// conformance.test.js — the combat layer matches the written spec.
//
// Every test here corresponds to a divergence found by audit rather than by
// play. That is the point: none of these were visible while playing. A swing
// that whiffs on invulnerability frames while still shortening its own
// recovery feels *correct* — it just quietly halves your damage.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createWorld } from '../../src/sim/world.js';
import { tick } from '../../src/sim/schedule.js';
import { spawnPlayer, spawnEnemy } from '../../src/sim/archetypes.js';
import { attachMacro } from '../../src/sim/macro/init.js';
import { createIntent } from '../../src/sim/systems/intent.js';
import { deref } from '../../src/core/handles.js';
import { S, ACT, BTN, BUFFER_SLOTS } from '../../src/sim/components.js';
import { clearEvents } from '../../src/core/bus.js';
import { ATTACKS } from '../../src/data/attacks.js';
import { DODGE, STAMINA, STANCE, HITSTOP, IFRAME } from '../../src/data/tuning.js';
import { applyHit, BLOCKED, isInvulnerable } from '../../src/sim/ops/damage.js';
import { startAttack } from '../../src/sim/ops/attack.js';
import { spend } from '../../src/sim/ops/stamina.js';
import { EV } from '../../src/data/events.js';

const FLAT = { heightAt: () => 0, normalAt: (x, z, o) => o, isWater: () => false, seaLevel: -100 };
function newWorld(seed = 31337) {
  const w = createWorld(seed);
  attachMacro(w);
  w.terrain = FLAT;
  return w;
}
const run = (w, n, intent = null) => {
  for (let i = 0; i < n; i++) { tick(w, intent); clearEvents(w.events); }
};
/** Keep an enemy present but passive, so it is a dummy and not an opponent. */
const passive = (w, h) => { w.store.cooldown[deref(w.store, h)] = 1e6; };

test('the parry is reachable from input at all', () => {
  // It was not. player_parry, the whole parry branch of applyHit, the riposte
  // window and player_riposte were correct and completely unreachable: nothing
  // ever called startAttack with 'player_parry'.
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);

  const intent = createIntent();
  intent.pressed = BTN.BLOCK;
  intent.held = BTN.BLOCK;
  tick(w, intent);

  assert.equal(w.store.stateId[p], S.PARRY,
    'pressing guard did not start a parry');
  assert.equal(w.store.attackId[p], ATTACKS.player_parry.index);
});

test('holding guard after the parry recovers becomes a block', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  const intent = createIntent();
  intent.pressed = BTN.BLOCK; intent.held = BTN.BLOCK;
  tick(w, intent);
  intent.pressed = 0;
  run(w, ATTACKS.player_parry.recovery + 3, intent);
  assert.equal(w.store.stateId[p], S.BLOCK,
    'a held guard did not settle into a block after the parry window');
});

test('the second hit of the light chain can actually land', () => {
  // IFRAME.onHit was 25 ticks — longer than the combo cadence — so hit 2 of
  // the three-hit chain landed inside the invulnerability granted by hit 1
  // and could never connect. Damage throughput was half what the data implied.
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'troll', 1.8, 0);   // big, survives the whole chain
  passive(w, eh);
  const e = deref(w.store, eh);

  const intent = createIntent();
  let hits = 0;
  for (let i = 0; i < 120; i++) {
    intent.pressed = (i % 6 === 0) ? BTN.ATTACK : 0;
    tick(w, intent);
    for (let k = 0; k < w.events.n; k++) {
      if (w.events.type[k] === EV.HIT && w.events.f[k * 6] > 0) hits++;
    }
    clearEvents(w.events);
  }
  assert.ok(hits >= 4, `only ${hits} hits landed in 2 seconds of attacking`);
  assert.ok(IFRAME.onHit < 19,
    'on-hit invulnerability is long enough to eat the combo again');
});

test('a swing stopped by i-frames does not earn the hit-confirm discount', () => {
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'skeleton', 1.6, 0);
  passive(w, eh);
  const p = deref(w.store, w.player);
  const e = deref(w.store, eh);

  w.store.iframeT[e] = 60;                    // untouchable dummy
  startAttack(w, p, 'player_light_1');
  run(w, ATTACKS.player_light_1.windup + ATTACKS.player_light_1.active + 1);
  assert.equal(w.store.hitConfirm[p], 0,
    'whiffing on invulnerability was counted as a connect');
});

test('a blocked hit still counts as contact', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  const eh = spawnEnemy(w, 'skeleton', 1.6, 0);
  const e = deref(w.store, eh);

  w.store.stateId[p] = S.BLOCK;
  w.store.yaw[p] = 0;
  const r = applyHit(w, e, p, ATTACKS.skeleton_slash, -1, 0);
  assert.equal(r, BLOCKED, 'a guarded hit should report BLOCKED, not zero');
});

test('enemy wind-ups last exactly as long as the data declares', () => {
  // combatSystem advances phaseT at the top of its loop, so an attack started
  // by an earlier system in the same tick lost its first frame. Every enemy
  // telegraph shipped one frame shorter than attacks.js says — while
  // validate-data.js polices the declared number.
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'skeleton', 2.0, 0);
  const e = deref(w.store, eh);

  let started = -1, active = -1;
  for (let i = 0; i < 400 && active < 0; i++) {
    tick(w, null);
    clearEvents(w.events);
    if (started < 0 && w.store.stateId[e] === S.WINDUP) started = i;
    else if (started >= 0 && w.store.stateId[e] === S.ACTIVE) active = i;
  }
  assert.ok(started >= 0 && active > started, 'the enemy never wound up');
  const declared = ATTACKS[Object.keys(ATTACKS)
    .find((k) => ATTACKS[k].index === w.store.attackId[e])].windup;
  assert.equal(active - started, declared,
    `wind-up ran ${active - started} ticks, data says ${declared}`);
});

test('hitstop holds knockback instead of decaying it away', () => {
  // The impulse was withheld while frozen but the 0.86 decay was not, so a
  // 12-tick freeze ate 84% of it: the heavier the hit, the LESS it knocked back.
  const travel = (hitstop) => {
    const w = newWorld();
    const ph = spawnPlayer(w, 0, 0);
    const p = deref(w.store, ph);
    w.store.kx[p] = 8;
    w.hitstopT = hitstop;
    const x0 = w.store.px[p];
    run(w, 40);
    return w.store.px[p] - x0;
  };
  const frozen = travel(12);
  const free = travel(0);
  assert.ok(frozen > free * 0.75,
    `hitstop ate the knockback: ${frozen.toFixed(3)}m vs ${free.toFixed(3)}m`);
});

test('a dodge can be cancelled into an attack at the actionable frame', () => {
  // The cancel required `atk.next.length > 0`; a dodge has no follow-up, so
  // the input was dropped at exactly the documented actionable frame.
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  const intent = createIntent();

  intent.pressed = BTN.DODGE;
  tick(w, intent);
  intent.pressed = 0;
  while (w.store.phaseT[p] < DODGE.actionableFrom) { tick(w, intent); clearEvents(w.events); }

  intent.pressed = BTN.ATTACK;
  tick(w, intent);
  intent.pressed = 0;
  run(w, 2, intent);
  assert.equal(w.store.stateId[p], S.WINDUP,
    'attacking out of a dodge at the actionable frame did nothing');
});

test('a dodge attempted without stamina keeps the input and reports it', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  w.store.stamina[p] = 5;                     // below the dodge cost

  const intent = createIntent();
  intent.pressed = BTN.DODGE;
  tick(w, intent);

  assert.equal(w.store.stateId[p], S.IDLE);
  let queued = 0;
  for (let k = 0; k < BUFFER_SLOTS; k++) {
    if (w.store.bufAct[p * BUFFER_SLOTS + k] === ACT.DODGE) queued++;
  }
  assert.equal(queued, 1, 'the unaffordable dodge was silently eaten');
});

test('overspending stamina still empties the pool and arms the lockout', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  w.store.stamina[p] = 6;
  const paid = spend(w.store, p, 12);
  assert.equal(paid, false);
  assert.equal(w.store.stamina[p], 0, 'an overspend cost nothing');
  assert.equal(w.store.staRegenDelay[p], STAMINA.delayAfterSpend,
    'an overspend left regen unlocked');
});

test('the delayed variant holds the pose without extending the lunge', () => {
  // A negative phaseT trivially satisfied `phaseT < lunge.ticks`, so the
  // delayed variant also slid the enemy 3.5x further — wrecking the spacing
  // read the mechanic exists to teach.
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'musha', 6, 0);
  const e = deref(w.store, eh);

  startAttack(w, e, 'musha_iai');
  w.store.phaseT[e] = -ATTACKS.musha_iai.delayVariant.holdTicks;
  const x0 = w.store.px[e];
  run(w, ATTACKS.musha_iai.delayVariant.holdTicks + ATTACKS.musha_iai.windup);
  const delayed = Math.abs(w.store.px[e] - x0);

  const w2 = newWorld();
  spawnPlayer(w2, 0, 0);
  const eh2 = spawnEnemy(w2, 'musha', 6, 0);
  const e2 = deref(w2.store, eh2);
  startAttack(w2, e2, 'musha_iai');
  const x2 = w2.store.px[e2];
  run(w2, ATTACKS.musha_iai.windup);
  const plain = Math.abs(w2.store.px[e2] - x2);

  assert.ok(delayed < plain * 1.6,
    `delayed variant travelled ${delayed.toFixed(2)}m vs ${plain.toFixed(2)}m`);
});

test('a ranged attack keeps its full recovery', () => {
  // hitConfirm was reused as an "already fired" latch, so every beam got the
  // 30% hit-confirm discount and quietly lost 11 frames of punish window.
  const w = newWorld();
  spawnPlayer(w, 0, 0);
  const eh = spawnEnemy(w, 'machine_stag', 14, 0);
  const e = deref(w.store, eh);

  startAttack(w, e, 'machine_beam');
  const a = ATTACKS.machine_beam;
  run(w, a.windup + a.active + 2);
  assert.equal(w.store.stateId[e], S.RECOVERY);
  assert.equal(w.store.hitConfirm[e], 0,
    'firing a projectile was counted as confirming a hit');
});

test('poise regenerates when left alone', () => {
  // It never did: poise only ever decreased, so late in a fight every actor
  // staggered from anything.
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  w.store.poise[p] = 5;
  w.store.stanceIdleT[p] = STANCE.idleBeforeRegen;
  run(w, 120);
  assert.ok(w.store.poise[p] > 5, 'poise never recovered');
});

test('a frozen actor takes no footsteps', () => {
  const w = newWorld();
  const ph = spawnPlayer(w, 0, 0);
  const p = deref(w.store, ph);
  let steps = 0;
  for (let i = 0; i < 200; i++) {
    w.store.vx[p] = 6; w.store.vz[p] = 0;
    w.hitstopT = 50;                          // keep it frozen throughout
    tick(w, null);
    for (let k = 0; k < w.events.n; k++) if (w.events.type[k] === EV.STEP) steps++;
    clearEvents(w.events);
  }
  assert.equal(steps, 0, `emitted ${steps} footsteps while frozen`);
});

test('cancel thresholds are whole ticks', () => {
  for (const a of Object.values(ATTACKS)) {
    if (!a._cancel) continue;
    for (const set of a._cancel) {
      for (const [k, v] of Object.entries(set)) {
        assert.equal(v, Math.round(v), `${a.id}._cancel.${k} = ${v} is fractional`);
      }
    }
  }
});
