// tick.js — the macro simulation driver and offline catch-up.
// L2.
//
// Runs once per in-game hour, independent of the 60Hz loop. This is the layer
// that keeps the world moving while nobody is watching — the thing none of the
// reference titles do, because their hand-authored content cannot survive a
// world that changes without a script saying so.

import { MAX_CATCHUP_HOURS, OFFLINE_HOURS_PER_REAL_HOUR } from '../../data/tuning.js';
import { economyTick, economyCatchUp } from './economy.js';
import { factionTick } from './factions.js';
import { rumorTick } from './rumor.js';
import { sampleEvents } from './worldEvents.js';
import { chronicleCompact, record } from './chronicle.js';

/** Fixed order. Same rule as sim/schedule.js: it lives in exactly one place. */
export function macroTick(w, hours = 1) {
  if (!w.macro) return;
  economyTick(w, hours);
  factionTick(w, hours);
  rumorTick(w, hours);
  const events = sampleEvents(w, hours);
  for (let i = 0; i < events.length; i++) record(w, events[i]);
  w.macro.hour += hours;
  if (w.macro.hour % (24 * 7) < hours) chronicleCompact(w);
}

/**
 * Advance the macro world by `realSeconds` of absence.
 *
 * Three buckets, and the classification is the whole design:
 *
 *   (a) ANALYTIC — about 80% of macro state is decay toward a target (price
 *       relaxation, opinion decay, stock regrowth, wealth accrual). Evaluated
 *       in closed form with powInt: ~17 multiplies for 720 hours instead of
 *       720 iterations, and deterministic where Math.pow would not be.
 *
 *   (b) COARSENED — faction agendas and rumour diffusion are stateful graph
 *       processes over 9-20 nodes. Not skippable, but run in super-steps of up
 *       to 24 hours with rates scaled accordingly. Thirty in-game days is 30
 *       iterations over a 20-node graph: microseconds.
 *
 *   (c) SAMPLED — discrete events (raids, caravans, festivals) must never be
 *       looped hour by hour. Draw the COUNT once from a binomial, then place
 *       the occurrences by hash. Ten thousand coin flips become one draw, and
 *       because both paths are hash-driven the counts match the iterative path
 *       exactly, which is what makes it testable rather than merely fast.
 */
export function catchUp(w, realSeconds) {
  if (!w.macro) return { hours: 0, events: [] };

  const rawHours = (realSeconds / 3600) * OFFLINE_HOURS_PER_REAL_HOUR;
  const hours = Math.min(Math.floor(rawHours), MAX_CATCHUP_HOURS);
  if (hours <= 0) return { hours: 0, events: [] };

  // (a) analytic
  economyCatchUp(w, hours);

  // (b) coarsened super-steps
  let remaining = hours;
  while (remaining > 0) {
    const step = Math.min(remaining, 24);
    factionTick(w, step);
    rumorTick(w, step);
    remaining -= step;
  }

  // (c) sampled
  const events = sampleEvents(w, hours);
  for (let i = 0; i < events.length; i++) record(w, events[i]);

  w.macro.hour += hours;
  chronicleCompact(w);

  if (hours >= MAX_CATCHUP_HOURS) {
    record(w, {
      tick: w.tick, importance: 3, kind: 'long_absence',
      text: `${Math.floor(hours / 24)}日が過ぎた。`,
    });
  }
  return { hours, events };
}
