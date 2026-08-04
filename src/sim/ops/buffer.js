// buffer.js — the per-actor input buffer.
// L2.
//
// Lives in the SIM, in ticks, so a recorded intent log replays bit-exactly.
// Three rules, all from the spec:
//   * TTL per action kind (attack 9 ticks, dodge 12)
//   * at most two of the same action queued — the fix for Elden Ring's
//     documented over-buffering, where a panicked mash spends itself long
//     after the moment has passed
//   * WIPED ENTIRELY on hit-stun, so a buffered swing never fires out of a
//     stagger you were supposed to be punished for

import { BUFFER_SLOTS, ACT } from '../components.js';
import { BUFFER } from '../../data/tuning.js';

const ttlFor = (act) => {
  if (act === ACT.DODGE) return BUFFER.dodgeTicks;
  if (act === ACT.BLOCK) return BUFFER.blockTicks;
  return BUFFER.attackTicks;
};

export function bufferPush(s, e, act) {
  if (act === ACT.NONE) return;
  const base = e * BUFFER_SLOTS;
  let same = 0;
  let freeSlot = -1;
  for (let k = 0; k < BUFFER_SLOTS; k++) {
    const a = s.bufAct[base + k];
    if (a === act) same++;
    if (a === ACT.NONE && freeSlot === -1) freeSlot = k;
  }
  if (same >= BUFFER.maxSameAction) return;
  if (freeSlot === -1) return;
  s.bufAct[base + freeSlot] = act;
  s.bufTtl[base + freeSlot] = ttlFor(act);
}

/** True if the action is queued (does not consume it). */
export function bufferHas(s, e, act) {
  const base = e * BUFFER_SLOTS;
  for (let k = 0; k < BUFFER_SLOTS; k++) {
    if (s.bufAct[base + k] === act) return true;
  }
  return false;
}

/** Consume the oldest matching entry. Returns true if one was found. */
export function bufferTake(s, e, act) {
  const base = e * BUFFER_SLOTS;
  let best = -1, bestTtl = 127;
  for (let k = 0; k < BUFFER_SLOTS; k++) {
    if (s.bufAct[base + k] === act && s.bufTtl[base + k] < bestTtl) {
      bestTtl = s.bufTtl[base + k];
      best = k;
    }
  }
  if (best === -1) return false;
  s.bufAct[base + best] = ACT.NONE;
  s.bufTtl[base + best] = 0;
  return true;
}

export function bufferClear(s, e) {
  const base = e * BUFFER_SLOTS;
  for (let k = 0; k < BUFFER_SLOTS; k++) {
    s.bufAct[base + k] = ACT.NONE;
    s.bufTtl[base + k] = 0;
  }
}

/** Age every entry on the RAW clock — hitstop must not extend a buffer. */
export function bufferSystem(w, _dt) {
  const s = w.store;
  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    const base = e * BUFFER_SLOTS;
    for (let b = 0; b < BUFFER_SLOTS; b++) {
      if (s.bufAct[base + b] === ACT.NONE) continue;
      if (--s.bufTtl[base + b] <= 0) {
        s.bufAct[base + b] = ACT.NONE;
        s.bufTtl[base + b] = 0;
      }
    }
  }
}
