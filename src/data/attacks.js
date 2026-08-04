// attacks.js — every attack as data. The state machine that consumes this
// contains no per-move logic at all.
// L1.
//
// Durations are TICKS at 60Hz. Hitboxes are local-space capsules, y-up,
// swept from `from` to `to` across the ACTIVE phase.

import { CANCEL, DODGE, PARRY, HITSTOP, TELEGRAPH } from './tuning.js';

export const ATTACKS = {
  // ——————————————————————————————— player: light chain
  player_light_1: {
    kind: 'melee',
    windup: 8, active: 4, recovery: 16,
    stamina: 12, minStamina: 6,
    next: ['player_light_2'],
    hitbox: { r: 0.55, from: [0.30, 1.10, 0.60], to: [0.30, 1.00, 2.10], arcDeg: 110 },
    dmg: { base: 1.0, scale: 'atk', type: 'slash' },
    poise: 18, stance: 12, hitstop: HITSTOP.light,
    knock: { impulse: 2.6, up: 0.2 },
    weight: 0.30,
    lunge: { ticks: 6, speed: 3.2 },
    turnCap: 0.14,
    anim: 'slash_r', sfxSwing: 'swing_l', sfxHit: 'hit_slash', vfx: 'trail_light',
  },
  player_light_2: {
    kind: 'melee',
    windup: 7, active: 4, recovery: 17,
    stamina: 12, minStamina: 6,
    next: ['player_light_3'],
    hitbox: { r: 0.55, from: [-0.30, 1.10, 0.60], to: [-0.30, 1.00, 2.10], arcDeg: 120 },
    dmg: { base: 1.05, scale: 'atk', type: 'slash' },
    poise: 20, stance: 13, hitstop: HITSTOP.light,
    knock: { impulse: 2.8, up: 0.2 },
    weight: 0.34,
    lunge: { ticks: 6, speed: 3.4 },
    turnCap: 0.12,
    anim: 'slash_l', sfxSwing: 'swing_l', sfxHit: 'hit_slash', vfx: 'trail_light',
  },
  player_light_3: {
    kind: 'melee',
    windup: 11, active: 5, recovery: 26,
    stamina: 18, minStamina: 6,
    next: [],
    hitbox: { r: 0.62, from: [0.0, 1.45, 0.55], to: [0.0, 0.85, 2.30], arcDeg: 150 },
    dmg: { base: 1.55, scale: 'atk', type: 'slash' },
    poise: 34, stance: 24, hitstop: HITSTOP.heavy,
    knock: { impulse: 4.6, up: 0.5 },
    weight: 0.62,
    lunge: { ticks: 8, speed: 4.1 },
    turnCap: 0.08,
    anim: 'slash_spin', sfxSwing: 'swing_h', sfxHit: 'hit_heavy', vfx: 'trail_heavy',
  },

  // ——————————————————————————————— player: heavy
  player_heavy: {
    kind: 'melee',
    windup: 22, active: 6, recovery: 32,
    stamina: 26, minStamina: 10,
    next: [],
    hitbox: { r: 0.70, from: [0.0, 2.05, 0.50], to: [0.0, 0.55, 2.15], arcDeg: 90 },
    dmg: { base: 2.1, scale: 'atk', type: 'blunt' },
    poise: 60, stance: 40, hitstop: HITSTOP.heavy,
    knock: { impulse: 6.2, up: 0.9 },
    weight: 0.88,
    lunge: { ticks: 10, speed: 2.4 },
    turnCap: 0.05,
    anim: 'overhead', sfxSwing: 'swing_h', sfxHit: 'hit_heavy', vfx: 'trail_heavy',
  },

  // The payoff for a parry. Fast, huge, and only available inside the riposte
  // window — without it the parry is a damage-prevention button rather than an
  // offensive read, which is the whole point of the mechanic.
  player_riposte: {
    kind: 'melee',
    windup: 6, active: 5, recovery: 22,
    stamina: 8, minStamina: 0,
    next: [],
    hitbox: { r: 0.65, from: [0.0, 1.30, 0.50], to: [0.0, 0.95, 2.00], arcDeg: 90 },
    dmg: { base: 3.2, scale: 'atk', type: 'pierce' },
    poise: 70, stance: 55, hitstop: HITSTOP.heavy,
    knock: { impulse: 5.0, up: 0.4 },
    weight: 1.0,
    lunge: { ticks: 6, speed: 6.0 },
    turnCap: 0.16,
    anim: 'riposte', sfxSwing: 'swing_h', sfxHit: 'hit_heavy', vfx: 'trail_heavy',
  },

  // ——————————————————————————————— player: defensive
  player_dodge: {
    kind: 'dodge',
    windup: 2, active: 0, recovery: DODGE.totalTicks - 2,
    iframes: [DODGE.iframeStart, DODGE.iframeStart + DODGE.iframeFrames - 1],
    iframeFrames: DODGE.iframeFrames,
    actionableFrom: DODGE.actionableFrom,
    stamina: DODGE.stamina,
    motion: { curve: 'roll', dist: DODGE.distance },
    weight: 0.15,
    anim: 'roll', sfxSwing: 'dodge',
  },
  player_parry: {
    kind: 'parry',
    windup: 0, active: 0, recovery: 18,
    parryWindow: PARRY.windowTicks,
    onSuccess: {
      attackerStanceMul: 0.5,
      hitstop: PARRY.hitstopTicks,
      staminaRefund: 'full',
      riposteWindow: PARRY.riposteWindowTicks,
    },
    weight: 0.80,
    anim: 'parry', sfxSwing: 'parry',
  },

  // ——————————————————————————————— 灰王国 / undead
  skeleton_slash: {
    kind: 'melee',
    windup: 26, active: 4, recovery: 24,
    tell: 'arm_raise',
    hitbox: { r: 0.5, from: [0.3, 1.2, 0.4], to: [0.3, 1.0, 1.9], arcDeg: 100 },
    dmg: { base: 0.9, scale: 'atk', type: 'slash' },
    poise: 14, stance: 8, hitstop: HITSTOP.light,
    knock: { impulse: 2.2, up: 0.1 },
    weight: 0.28,
    turnCap: 0.09,
    anim: 'slash_r', sfxHit: 'hit_slash',
  },
  skeleton_thrust: {
    kind: 'melee',
    windup: 34, active: 3, recovery: 30,
    tell: 'crouch_back',
    hitbox: { r: 0.42, from: [0.0, 1.1, 0.5], to: [0.0, 1.1, 2.6], arcDeg: 40 },
    dmg: { base: 1.2, scale: 'atk', type: 'pierce' },
    poise: 22, stance: 14, hitstop: HITSTOP.light,
    knock: { impulse: 3.4, up: 0.1 },
    weight: 0.45,
    lunge: { ticks: 5, speed: 5.5 },
    turnCap: 0.06,
    anim: 'thrust', sfxHit: 'hit_pierce',
    delayVariant: { holdTicks: TELEGRAPH.delayVariantHoldTicks, tell: 'crouch_hold' },
  },
  skeleton_sweep: {
    kind: 'melee',
    windup: 45, active: 6, recovery: 38,
    tell: 'wind_low',
    hitbox: { r: 0.6, from: [-0.9, 0.5, 1.2], to: [0.9, 0.5, 1.2], arcDeg: 200 },
    dmg: { base: 1.4, scale: 'atk', type: 'slash' },
    poise: 30, stance: 20, hitstop: HITSTOP.heavy,
    knock: { impulse: 4.8, up: 0.6 },
    weight: 0.66,
    turnCap: 0.03,
    anim: 'sweep', sfxHit: 'hit_heavy',
  },

  // ——————————————————————————————— 灰王国 / troll boss
  troll_overhead: {
    kind: 'melee',
    windup: 34, active: 5, recovery: 40,
    tell: 'overhead_raise',
    hitbox: { r: 1.1, from: [0.0, 3.4, 1.0], to: [0.0, 0.3, 2.8], arcDeg: 80 },
    dmg: { base: 2.2, scale: 'atk', type: 'blunt' },
    poise: 55, stance: 34, hitstop: HITSTOP.heavy,
    knock: { impulse: 8.0, up: 1.4 },
    weight: 0.95,
    turnCap: 0.04,
    anim: 'overhead', sfxHit: 'hit_heavy',
    // The delayed variant is what turns memorisation into reading. It costs
    // exactly one number and is the highest-value line in this whole file.
    delayVariant: { holdTicks: TELEGRAPH.delayVariantHoldTicks, tell: 'overhead_hold' },
  },
  troll_sweep: {
    kind: 'melee',
    windup: 27, active: 6, recovery: 34,
    tell: 'sweep_wind',
    hitbox: { r: 1.0, from: [-2.2, 1.1, 1.4], to: [2.2, 1.1, 1.4], arcDeg: 220 },
    dmg: { base: 1.7, scale: 'atk', type: 'blunt' },
    poise: 44, stance: 26, hitstop: HITSTOP.heavy,
    knock: { impulse: 6.4, up: 0.8 },
    weight: 0.78,
    turnCap: 0.05,
    anim: 'sweep', sfxHit: 'hit_heavy',
  },
  troll_stomp: {
    kind: 'melee',
    windup: 52, active: 4, recovery: 46,
    tell: 'leg_raise',
    hitbox: { r: 2.0, from: [0.0, 0.2, 1.2], to: [0.0, 0.2, 1.2], arcDeg: 360 },
    dmg: { base: 2.6, scale: 'atk', type: 'blunt' },
    poise: 80, stance: 50, hitstop: HITSTOP.heavy,
    knock: { impulse: 9.5, up: 2.0 },
    weight: 1.0,
    turnCap: 0.0,
    anim: 'stomp', sfxHit: 'hit_quake',
  },

  // ——————————————————————————————— 霧ノ國 / musha
  musha_iai: {
    kind: 'melee',
    windup: 40, active: 3, recovery: 28,
    tell: 'sheathe_crouch',
    hitbox: { r: 0.5, from: [-1.2, 1.1, 1.0], to: [1.2, 1.1, 1.6], arcDeg: 170 },
    dmg: { base: 2.0, scale: 'atk', type: 'slash' },
    poise: 40, stance: 34, hitstop: HITSTOP.heavy,
    knock: { impulse: 3.0, up: 0.2 },
    weight: 0.85,
    lunge: { ticks: 4, speed: 7.0 },
    turnCap: 0.05,
    anim: 'iai', sfxHit: 'hit_slash',
    delayVariant: { holdTicks: TELEGRAPH.delayVariantHoldTicks, tell: 'sheathe_hold' },
  },
  musha_kesa: {
    kind: 'melee',
    windup: 24, active: 4, recovery: 22,
    tell: 'blade_high',
    hitbox: { r: 0.5, from: [0.7, 2.0, 0.8], to: [-0.5, 0.6, 2.0], arcDeg: 120 },
    dmg: { base: 1.3, scale: 'atk', type: 'slash' },
    poise: 26, stance: 18, hitstop: HITSTOP.light,
    knock: { impulse: 3.2, up: 0.2 },
    weight: 0.52,
    turnCap: 0.10,
    anim: 'kesa', sfxHit: 'hit_slash',
  },
  musha_tsuki: {
    kind: 'melee',
    windup: 30, active: 3, recovery: 26,
    tell: 'blade_point',
    hitbox: { r: 0.38, from: [0.0, 1.2, 0.6], to: [0.0, 1.2, 2.7], arcDeg: 35 },
    dmg: { base: 1.5, scale: 'atk', type: 'pierce' },
    poise: 30, stance: 22, hitstop: HITSTOP.light,
    knock: { impulse: 3.8, up: 0.1 },
    weight: 0.58,
    lunge: { ticks: 5, speed: 6.0 },
    turnCap: 0.07,
    anim: 'thrust', sfxHit: 'hit_pierce',
  },

  // ——————————————————————————————— 銹の荒野 / machine beast
  machine_gore: {
    kind: 'melee',
    windup: 29, active: 5, recovery: 30,
    tell: 'head_lower',
    hitbox: { r: 0.9, from: [0.0, 1.2, 0.8], to: [0.0, 1.2, 3.0], arcDeg: 70 },
    dmg: { base: 1.6, scale: 'atk', type: 'pierce' },
    poise: 46, stance: 28, hitstop: HITSTOP.heavy,
    knock: { impulse: 7.0, up: 1.1 },
    weight: 0.80,
    lunge: { ticks: 12, speed: 9.0 },
    turnCap: 0.03,
    anim: 'charge', sfxHit: 'hit_metal',
  },
  machine_beam: {
    kind: 'ranged',
    windup: 48, active: 8, recovery: 36,
    tell: 'core_glow',
    projectile: { speed: 26, life: 1.4, radius: 0.3, kind: 'beam' },
    dmg: { base: 1.4, scale: 'atk', type: 'shock' },
    poise: 24, stance: 16, hitstop: HITSTOP.light,
    knock: { impulse: 2.0, up: 0.1 },
    weight: 0.55,
    turnCap: 0.06,
    anim: 'beam', sfxHit: 'hit_shock',
    delayVariant: { holdTicks: TELEGRAPH.delayVariantHoldTicks, tell: 'core_hold' },
  },
  machine_slam: {
    kind: 'melee',
    windup: 38, active: 5, recovery: 40,
    tell: 'rear_up',
    hitbox: { r: 1.5, from: [0.0, 0.3, 1.5], to: [0.0, 0.3, 1.5], arcDeg: 360 },
    dmg: { base: 2.0, scale: 'atk', type: 'blunt' },
    poise: 62, stance: 42, hitstop: HITSTOP.heavy,
    knock: { impulse: 8.2, up: 1.6 },
    weight: 0.92,
    turnCap: 0.0,
    anim: 'slam', sfxHit: 'hit_quake',
  },
};

/**
 * Fill defaults and PRE-COMPUTE the cancel thresholds for both the hit and the
 * whiff case, so the state machine does zero arithmetic per tick.
 * Called once at module load.
 */
export function finalizeAttacks(table = ATTACKS) {
  const ids = Object.keys(table).sort();   // sorted: index assignment is deterministic
  const byIndex = [];
  ids.forEach((id, idx) => {
    const a = table[id];
    a.id = id;
    a.index = idx;
    a.windup = a.windup | 0;
    a.active = a.active | 0;
    a.recovery = a.recovery | 0;
    a.stamina = a.stamina || 0;
    a.poise = a.poise || 0;
    a.stance = a.stance || 0;
    a.hitstop = a.hitstop || 0;
    a.weight = a.weight ?? 0.3;
    a.turnCap = a.turnCap ?? 0.1;
    a.next = a.next || [];
    a.cancel = a.cancel || { combo: CANCEL.combo, dodge: CANCEL.dodge, block: CANCEL.block };
    if (a.recoveryOnHit === undefined) {
      a.recoveryOnHit = Math.round(a.recovery * CANCEL.hitRecoveryMul);
    }
    // _cancel[0] = whiff thresholds, _cancel[1] = hit thresholds, in ticks.
    // Pre-ROUNDED to whole ticks. phaseT is an integer, so comparing it to a
    // fractional threshold silently ceils and the same 0.45 becomes anywhere
    // from 46% to 50% depending on the recovery length.
    const th = (rec) => ({
      combo: Math.round(rec * a.cancel.combo),
      dodge: Math.round(rec * a.cancel.dodge),
      block: Math.round(rec * a.cancel.block),
    });
    a._cancel = [th(a.recovery), th(a.recoveryOnHit)];
    byIndex[idx] = a;
  });
  return { ids, byIndex };
}

export const ATTACK_INDEX = finalizeAttacks(ATTACKS);
export const attackByIndex = (i) => ATTACK_INDEX.byIndex[i];
export const attackId = (name) => ATTACKS[name].index;
