// reactors.js — event type -> juice, AS DATA.
// L3.
//
// Adding a new feedback response is a table edit, not a code change. That is
// the whole point: combat feel needs iteration loops measured in minutes, and
// anything that requires editing a switch statement in three files does not
// get iterated on.
//
// Ranges like [2, 9] are interpolated by the event's `weight` (f4), so one
// entry covers a light jab and a boss overhead without branching.

import { EV } from '../data/events.js';
import { HIT_FLAG } from '../sim/components.js';

export const REACTORS = {
  [EV.HIT]: {
    shake: { amp: [0.12, 0.55], dirFromNormal: true },
    fovKick: [0.3, 2.4],
    haptic: { ms: [10, 30] },              // silently skipped on iOS
    sfx: { id: 'hit', pitch: [1.12, 0.85], vol: [0.5, 1.0] },
    fx: [
      { preset: 'sparks', count: [6, 22], alongNormal: true },
      { preset: 'blood', count: [8, 26], unless: HIT_FLAG.UNDEAD },
    ],
    text: { value: 'f0', style: 'dmg', altFlag: HIT_FLAG.CRIT, altStyle: 'crit' },
  },
  [EV.SWING]: {
    sfx: { id: 'swing', pitch: [1.2, 0.8], vol: [0.35, 0.7] },
  },
  [EV.PARRY]: {
    // The one moment that must land in three senses at once. If a parry is not
    // instantly legible as success, the whole defensive layer feels like luck.
    shake: { amp: [0.3, 0.3] },
    fovKick: [2.0, 2.0],
    haptic: { ms: [45, 45] },
    sfx: { id: 'parry', pitch: [1.0, 1.0], vol: [1.0, 1.0] },
    fx: [{ preset: 'spark_ring', count: [26, 26] }],
    text: { literal: 'パリィ', style: 'parry' },
  },
  [EV.BLOCK]: {
    shake: { amp: [0.08, 0.2] },
    haptic: { ms: [8, 16] },
    sfx: { id: 'block', pitch: [1.05, 0.9], vol: [0.4, 0.8] },
    fx: [{ preset: 'sparks', count: [4, 10] }],
  },
  [EV.STANCE_BREAK]: {
    shake: { amp: [0.6, 0.6] },
    fovKick: [-3.0, -3.0],                  // zoom IN for emphasis
    haptic: { ms: [60, 60] },
    sfx: { id: 'break', pitch: [1, 1], vol: [1, 1] },
    fx: [{ preset: 'shockwave', count: [1, 1] }],
    text: { literal: '体勢崩し', style: 'break' },
  },
  [EV.STAGGER]: {
    shake: { amp: [0.22, 0.4] },
    sfx: { id: 'stagger', pitch: [1, 0.9], vol: [0.6, 0.9] },
  },
  [EV.DEATH]: {
    shake: { amp: [0.25, 0.5] },
    sfx: { id: 'death', pitch: [1, 0.85], vol: [0.7, 1.0] },
    fx: [{ preset: 'dust', count: [18, 34] }],
  },
  [EV.DODGE]: {
    sfx: { id: 'dodge', pitch: [1.1, 1.1], vol: [0.45, 0.45] },
    fx: [{ preset: 'dust', count: [8, 8] }],
  },
  [EV.EXHAUSTED]: {
    // Silence here reads as a dropped input, which is far worse than a buzz.
    sfx: { id: 'error', pitch: [1, 1], vol: [0.35, 0.35] },
    haptic: { ms: [18, 18] },
  },
  [EV.LAND]: {
    shake: { amp: [0.05, 0.30] },
    sfx: { id: 'land', pitch: [1.1, 0.85], vol: [0.3, 0.8] },
    fx: [{ preset: 'dust', count: [4, 16] }],
  },
  [EV.STEP]: {
    sfx: { id: 'step', pitch: [1.05, 0.92], vol: [0.10, 0.26] },
  },
  [EV.HEAL]: {
    sfx: { id: 'heal', pitch: [1, 1], vol: [0.6, 0.6] },
    fx: [{ preset: 'heal', count: [14, 14] }],
    text: { value: 'f0', style: 'heal' },
  },
  [EV.PART_BREAK]: {
    shake: { amp: [0.4, 0.4] },
    fovKick: [1.5, 1.5],
    haptic: { ms: [40, 40] },
    sfx: { id: 'metal_break', pitch: [1, 1], vol: [0.9, 0.9] },
    fx: [{ preset: 'sparks', count: [30, 30] }, { preset: 'shockwave', count: [1, 1] }],
    text: { literal: '部位破壊', style: 'break' },
  },
};

/** Interpolate a [lo, hi] pair by weight. Scalars pass straight through. */
export const byWeight = (range, w) =>
  Array.isArray(range) ? range[0] + (range[1] - range[0]) * w : range;
