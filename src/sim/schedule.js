// schedule.js — THE ONLY PLACE TICK ORDER LIVES.
// L2.
//
// If you need to know what runs when, this file is the answer and there is no
// second answer. Systems are free functions; none of them import each other.

import { hitstopSystem, combatSystem } from './systems/combat.js';
import { spatialSystem, locomotionSystem, footstepSystem } from './systems/locomotion.js';
import { staminaSystem } from './ops/stamina.js';
import { bufferSystem } from './ops/buffer.js';
import { stanceSystem } from './ops/damage.js';
import { intentSystem } from './systems/intent.js';
import { aiSystem } from './systems/ai.js';
import { targetingSystem } from './systems/targeting.js';
import { projectileSystem } from './systems/projectile.js';
import { rebuildLive } from './store.js';
import { clearEvents } from '../core/bus.js';
import { DT, MACRO_TICKS_PER_HOUR } from '../data/tuning.js';
import { macroTick } from './macro/tick.js';

/**
 * Advance the simulation exactly one fixed step.
 *
 * The ONLY function that mutates simulation state. Adding a parameter here is
 * the canonical signal that the god object is coming back.
 */
export function tick(w, intent) {
  // Live list first: everything below iterates it, and it must be ascending
  // regardless of what spawned or died last tick.
  rebuildLive(w.store);

  hitstopSystem(w);
  spatialSystem(w);

  targetingSystem(w, intent);
  intentSystem(w, intent);
  aiSystem(w, DT);

  combatSystem(w, DT);
  projectileSystem(w, DT);
  locomotionSystem(w, DT);

  // These three run on the RAW clock, so hitstop never stalls them.
  staminaSystem(w, DT);
  stanceSystem(w, DT);
  bufferSystem(w, DT);

  footstepSystem(w);

  w.tick++;

  // In-game clock, then the macro layer at most once per frame so a rollover
  // can never produce a spike.
  w.gameMinutes += (DT / (MACRO_TICKS_PER_HOUR / 60)) * 60;
  w.macroAccum++;
  if (w.macroAccum >= MACRO_TICKS_PER_HOUR) {
    w.macroAccum = 0;
    macroTick(w, 1);
  }
}

export { clearEvents };
