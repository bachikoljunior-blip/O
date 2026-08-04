// tuning.js — every combat number, in one place.
// L1: imports core only.
//
// All durations are TICKS at 60Hz. The spec is written in frames ("i-frames
// 3-17", "actionable from f24") and ticks are the sim's native unit, so
// keeping them in ticks removes a conversion that would otherwise drift.

export const TICK_RATE = 60;
export const DT = 1 / TICK_RATE;

/** Convert milliseconds to ticks. Used by data authors, not at runtime. */
export const ms = (v) => Math.round((v * TICK_RATE) / 1000);

export const STAMINA = {
  max: 100,
  regenPerSec: 35,
  regenPerTick: 35 / TICK_RATE,
  // The DELAY, not the pool size, is what creates the tension. Souls-likes
  // feel tight because you cannot immediately act again, not because 100 is
  // a small number.
  delayAfterSpend: ms(600),      // 36 ticks
  delayAfterBlockedHit: ms(1200), // 72 ticks
};

export const BUFFER = {
  attackTicks: ms(150),   // 9
  dodgeTicks: ms(200),    // 12
  blockTicks: ms(150),
  // Elden Ring's buffer is widely criticised for queueing too much. Two of the
  // same action is the cap, and the whole buffer is wiped on hit-stun so a
  // panicked mash cannot spend itself after you have already been punished.
  maxSameAction: 2,
};

export const STANCE = {
  idleBeforeRegen: ms(3000),  // 180
  regenFractionPerSec: 0.25,
  brokenTicks: ms(2000),      // 120
  brokenDamageMul: 1.5,       // DD2's documented knockdown multiplier
  parryStanceMul: 0.5,
};

export const PARRY = {
  windowTicks: ms(100),        // 6
  touchBonusTicks: ms(50),     // +3 -> 150ms, an input-device accommodation
  hitstopTicks: ms(200),       // 12
  riposteWindowTicks: ms(750), // 45
  guardDamageMul: 0.22,
  guardStaminaCost: 14,
  guardConeRad: 1.25,
};

export const HITSTOP = {
  light: 4,
  heavy: 8,
  parry: 12,
  stanceBreak: 12,
  // Knockback reads as a snap rather than a slide if the impulse is withheld
  // for a few frames after the freeze starts.
  preMoveTicks: 3,
};

export const IFRAME = {
  // Mercy invulnerability after taking a hit. Deliberately SHORT: per-swing
  // double-hits are already prevented by the hit mask, so this only needs to
  // stop several simultaneous sources landing on the same frame. At the old
  // 420ms it outlasted the player's own combo cadence and hit 2 of the
  // three-hit chain could never connect.
  onHit: ms(130),
  onBlockedHit: ms(100),
};

export const SHAKE = {
  // Trauma model: shake = trauma^2, driven by 2D value noise. Using
  // Math.random() here reads as buzz; noise reads as impact. That difference
  // is most of why cheap hits feel cheap.
  lightTrauma: 0.2,
  heavyTrauma: 0.45,
  decayPerSec: 1.6,
  maxOffset: 0.16,   // world units at the camera
  maxRoll: 0.021,    // radians (~1.2 degrees)
  frequency: 22,
};

export const TARGETING = {
  // Sticky with hysteresis so the camera does not flick between enemies.
  switchAngleRad: 0.436,       // 25 degrees of held intent
  switchHoldTicks: ms(150),
  reacquireConeRad: 0.698,     // 40 degrees
  reacquireDelayTicks: ms(250),
  maxLockDistance: 22,
  // Touch has no analogue stick precision, so the swing leans toward a target.
  // Past 30 degrees the player stops feeling like they aimed it.
  magnetConeRad: 0.524,        // 30 degrees
  magnetMaxTurnRad: 0.436,     // 25 degrees
  magnetTurnTicks: 4,          // over ~60ms
  magnetRangeMul: 1.4,
};

/**
 * Enemy telegraph budget. Enforced by tools/validate-data.js, not by good
 * intentions — a design rule that is only written down always drifts.
 *
 * Mobile browsers carry 60-100ms of end-to-end input latency versus 30-50ms
 * for a wired pad: a 4-6 frame tax. The fix is to LENGTHEN telegraphs, not to
 * widen the player's windows. Widening windows makes combat mushy; lengthening
 * telegraphs keeps it precise.
 */
export const TELEGRAPH = {
  minWindupTicks: 23,          // 380ms
  minDistinctWindups: 3,
  delayVariantHoldTicks: 15,   // +250ms on the same pose
  requireDelayVariant: true,
};

export const CANCEL = {
  combo: 0.45,
  dodge: 0.55,
  block: 0.70,
  // Hit-confirm reward: recovery is 30% shorter when you connect. This single
  // rule does more for how aggressive the game feels than any other number.
  hitRecoveryMul: 0.70,
};

export const DODGE = {
  totalTicks: 33,          // 550ms
  // Expressed as START + COUNT. "frames 3..17" was stated as 14 frames in the
  // spec but is arithmetically 15, and the implementation delivered 16 — three
  // different numbers for one window. A count cannot be miscounted.
  iframeStart: 3,
  iframeFrames: 14,        // covers phaseT 3..16
  actionableFrom: 24,
  stamina: 22,
  distance: 4.2,
};

export const DAMAGE = {
  // Diminishing returns, carried over from the 2D build where it was tuned.
  defScale: 3.2,
  minDamage: 1,
};

export const LOCOMOTION = {
  walkSpeed: 2.6,
  runSpeed: 6.1,
  sprintSpeed: 8.4,
  accel: 42,
  decel: 54,
  airAccel: 8,
  gravity: -22,
  jumpSpeed: 7.2,
  turnRateRad: 12,
  slopeLimitCos: 0.55,
  waterDragMul: 0.55,
};

export const CAMERA = {
  // Pulled back and flattened for portrait phones: at 5.4m and 0.35 rad the
  // horizon sits off the top of a tall screen and the world reads as a
  // featureless floor.
  distance: 7.2,
  height: 1.95,
  pitchMin: -0.35,
  pitchMax: 1.05,
  followLag: 9,
  lookahead: 0.9,
  fov: 62,
  fovKickHeavy: 3.5,
  collisionRadius: 0.35,
};

/** Day length in real seconds. One in-game hour is 1/24th of this. */
export const DAY_LENGTH = 780;
export const MACRO_TICKS_PER_HOUR = Math.round((DAY_LENGTH / 24) * TICK_RATE);
/** Returning to a world you no longer recognise is bad game feel, not just slow. */
export const MAX_CATCHUP_HOURS = 720;
/** Real elapsed time is compressed so an overnight absence is not catastrophic. */
export const OFFLINE_HOURS_PER_REAL_HOUR = 4;
