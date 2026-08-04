// worldEvents.js — discrete world events, SAMPLED rather than simulated.
// L2.
//
// The catch-up trick that makes a 30-day absence cheap: never loop hour by
// hour. Draw the COUNT once from a binomial, then place the occurrences by
// hash. Ten thousand coin flips become one draw — and because both the live
// path and the catch-up path are hash-driven off the same stream, the event
// COUNT matches exactly, which is what tools/test/macro.test.js asserts.

import { hash3 } from '../../core/noise.js';
import { powInt } from '../../core/fixed.js';
import { streamSeed } from '../../core/rng.js';
import { S as STREAM } from '../determinism/streams.js';
import { clamp } from '../../core/math.js';

export const KIND = {
  RAID: 'raid',
  CARAVAN: 'caravan',
  FESTIVAL: 'festival',
  BLOCKADE: 'blockade',
  HERD: 'herd',
};

/** Per-hour probabilities. Tuned so a week produces a handful of events. */
const BASE_RATE = {
  [KIND.RAID]: 0.010,
  [KIND.CARAVAN]: 0.035,
  [KIND.FESTIVAL]: 0.004,
  [KIND.BLOCKADE]: 0.005,
  [KIND.HERD]: 0.012,
};

/**
 * Exact inverse-CDF binomial draw. `u` is a SINGLE uniform in [0,1).
 *
 * No normal-approximation branch: that would need Math.log, which the spec
 * leaves implementation-defined and which would therefore make event counts
 * differ between iOS and Android. The exact sum is O(trials) arithmetic, but
 * "trials" here is hours (<= 720) and this runs once per event kind per
 * catch-up — a few thousand multiplies, not 720 simulation steps. The saving
 * the shortcut was ever about is skipping the SIMULATION, not the arithmetic.
 */
export function binomial(trials, p, u) {
  if (trials <= 0 || p <= 0) return 0;
  if (p >= 1) return trials;
  const ratio = p / (1 - p);
  let cum = 0;
  let term = powInt(1 - p, trials);   // deterministic: integer exponent
  for (let k = 0; k <= trials; k++) {
    cum += term;
    if (u < cum) return k;
    term *= ((trials - k) / (k + 1)) * ratio;
    if (!Number.isFinite(term)) break;
  }
  return trials;
}

/**
 * Produce every event that occurred across `hours`.
 * Identical whether called once with 720 or 720 times with 1.
 */
export function sampleEvents(w, hours) {
  const macro = w.macro;
  const seed = streamSeed(w.seed, STREAM.MACRO_EVENT);
  const out = [];
  const kinds = Object.keys(BASE_RATE).sort();

  for (let ki = 0; ki < kinds.length; ki++) {
    const kind = kinds[ki];
    let rate = BASE_RATE[kind];
    if (kind === KIND.RAID) rate *= 1 + (macro.raidPressure || 0) * 3;

    const u = hash3(ki, macro.hour, 0, seed);
    const count = binomial(hours, clamp(rate, 0, 0.95), u);

    for (let i = 0; i < count; i++) {
      const offset = Math.floor(hash3(ki, macro.hour, 100 + i, seed) * hours);
      const where = Math.floor(hash3(ki, macro.hour, 200 + i, seed) * macro.settlementIds.length);
      out.push(makeEvent(w, kind, macro.settlementIds[where], macro.hour + offset));
    }
  }
  // Total order so the chronicle reads chronologically and replays identically.
  out.sort((a, b) => (a.hour - b.hour) || (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : 0));
  return out;
}

function makeEvent(w, kind, place, hour) {
  const macro = w.macro;
  const idx = macro.settlementIds.indexOf(place);
  switch (kind) {
    case KIND.RAID: {
      // A raid is not a log line: it removes stock, which moves prices, which
      // surfaces as a problem the quest generator can hand to the player.
      const mk = macro.market[idx];
      if (mk) { mk.stock[0] *= 0.55; mk.stock[3] *= 0.7; }
      macro.raidPressure = Math.max(0, (macro.raidPressure || 0) - 0.15);
      return { hour, importance: 4, kind, place, text: `${place}が襲撃を受けた。` };
    }
    case KIND.BLOCKADE: {
      const roads = macro.roads;
      if (roads.length) {
        const r = roads[Math.floor(hash3(idx, hour, 7, w.seed) * roads.length)];
        r.blocked = true;
        r.blockedUntil = hour + 48;
      }
      return { hour, importance: 3, kind, place, text: `${place}へ至る道が塞がれた。` };
    }
    case KIND.CARAVAN: {
      const mk = macro.market[idx];
      if (mk) for (let g = 0; g < mk.stock.length; g++) {
        mk.stock[g] = Math.min(mk.cap[g], mk.stock[g] + 8);
      }
      return { hour, importance: 1, kind, place, text: `${place}に隊商が着いた。` };
    }
    case KIND.FESTIVAL:
      return { hour, importance: 2, kind, place, text: `${place}で祭が催された。` };
    default:
      return { hour, importance: 1, kind, place, text: `${place}の近くで鉄の獣が動いた。` };
  }
}

/** Lift expired blockades. Cheap enough to run every macro tick. */
export function expireBlockades(w) {
  const roads = w.macro.roads;
  for (let i = 0; i < roads.length; i++) {
    if (roads[i].blocked && w.macro.hour >= (roads[i].blockedUntil || 0)) {
      roads[i].blocked = false;
    }
  }
}
