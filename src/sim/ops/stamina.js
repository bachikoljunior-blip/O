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

/**
 * Deduct and arm the post-spend delay.
 *
 * Drains to ZERO rather than refusing. `minStamina` lets a move start on a
 * sliver — a deliberate affordance — but the old early return meant those
 * moves cost nothing at all AND left the regen lockout unarmed, so every light
 * attack in the 6-11 stamina band was free. Overspending must still empty the
 * pool and still lock out regen, otherwise the affordance is a exploit.
 */
export function spend(s, e, n) {
  const paidInFull = s.stamina[e] >= n;
  s.stamina[e] = Math.max(0, s.stamina[e] - n);
  if (s.staRegenDelay[e] < STAMINA.delayAfterSpend) {
    s.staRegenDelay[e] = STAMINA.delayAfterSpend;
  }
  return paidInFull;
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
