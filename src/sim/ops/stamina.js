// stamina.js — THE ONLY FILE PERMITTED TO WRITE store.stamina.
// L2.
//
// Everything else calls spend()/refund(). This is enforced by a grep-level
// lint check, and it is the difference between a resource system you can
// reason about and one that becomes a mystery three months in.

import { STAMINA } from '../../data/tuning.js';

/** Advance regeneration. Uses the RAW clock, so hitstop never stalls recovery. */
export function staminaSystem(w, _dt) {
  const s = w.store;
  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    if (s.staminaMax[e] <= 0) continue;
    if (s.staRegenDelay[e] > 0) {
      s.staRegenDelay[e] -= 1;
      continue;
    }
    if (s.stamina[e] < s.staminaMax[e]) {
      s.stamina[e] += STAMINA.regenPerTick;
      if (s.stamina[e] > s.staminaMax[e]) s.stamina[e] = s.staminaMax[e];
    }
  }
}

export const hasStamina = (s, e, n) => s.stamina[e] >= n;

/** Deduct and arm the post-spend delay. Returns false if it could not be paid. */
export function spend(s, e, n) {
  if (s.stamina[e] < n) return false;
  s.stamina[e] -= n;
  if (s.staRegenDelay[e] < STAMINA.delayAfterSpend) {
    s.staRegenDelay[e] = STAMINA.delayAfterSpend;
  }
  return true;
}

/** Blocked hits carry a longer lockout than voluntary spending. */
export function drainOnBlock(s, e, n) {
  s.stamina[e] -= n;
  if (s.stamina[e] < 0) s.stamina[e] = 0;
  if (s.staRegenDelay[e] < STAMINA.delayAfterBlockedHit) {
    s.staRegenDelay[e] = STAMINA.delayAfterBlockedHit;
  }
}

export function refundFull(s, e) {
  s.stamina[e] = s.staminaMax[e];
  s.staRegenDelay[e] = 0;
}

export function setMax(s, e, max) {
  s.staminaMax[e] = max;
  s.stamina[e] = max;
}
