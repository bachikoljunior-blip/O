// combat.js — the attack state machine.
// L2.
//
// Contains no per-move logic. Every timing, window and threshold comes from
// data/attacks.js, which is why a designer can retune the whole game from one
// file and why tools/validate-data.js can enforce the telegraph budget.

import { S, ACT, C } from '../components.js';
import { clearHitMask } from '../store.js';
import { attackByIndex, ATTACKS } from '../../data/attacks.js';
import { CANCEL, DODGE, TARGETING } from '../../data/tuning.js';
import { hasStamina } from '../ops/stamina.js';
import { bufferTake, bufferHas, bufferClear } from '../ops/buffer.js';
import { applyHit } from '../ops/damage.js';
import { startAttack } from '../ops/attack.js';
import { emit } from '../../core/bus.js';
import { EV } from '../../data/events.js';
import { fsin, fcos, flen, fatan2 } from '../../core/fixed.js';
import { angleDiff, approachAngle, lerp, clamp01 } from '../../core/math.js';
import { gridQuery } from '../../core/spatial.js';
import { deref } from '../../core/handles.js';

/**
 * Roll-motion curve, sampled by normalised phase. Front-loaded so the dodge
 * covers ground during the invulnerable frames rather than the recovery.
 */
function rollSpeed(t) {
  if (t < 0.06) return lerp(0, 1, t / 0.06);
  if (t < 0.55) return 1;
  return Math.max(0, 1 - (t - 0.55) / 0.45) * 0.85;
}

/**
 * Turn the swing toward a nearby target on touch input.
 * Capped at 25 degrees: past that the player stops feeling like they aimed it.
 */
function applyTouchMagnet(w, e, atk) {
  const s = w.store;
  const reach = (atk.hitbox ? atk.hitbox.to[2] : 2) * TARGETING.magnetRangeMul;
  const q = w.scratch.query;
  const n = gridQuery(w.grid, s.px[e], s.pz[e], reach, q, s);
  let best = -1, bestDelta = TARGETING.magnetConeRad;
  for (let k = 0; k < n; k++) {
    const t = q[k];
    if (t === e || s.stateId[t] === S.DEAD) continue;
    if ((s.mask[t] & C.AI) === 0) continue;
    const a = fatan2(s.pz[t] - s.pz[e], s.px[t] - s.px[e]);
    const d = Math.abs(angleDiff(a, s.yaw[e]));
    if (d < bestDelta) { bestDelta = d; best = t; }
  }
  if (best === -1) return;
  const target = fatan2(s.pz[best] - s.pz[e], s.px[best] - s.px[e]);
  const step = TARGETING.magnetMaxTurnRad / TARGETING.magnetTurnTicks;
  s.yaw[e] = approachAngle(s.yaw[e], target, step);
}

/** Sweep the attack capsule and resolve every fresh contact. */
function resolveActive(w, e, atk) {
  const s = w.store;
  if (!atk.hitbox) return;
  const t = atk.active > 0 ? clamp01(s.phaseT[e] / atk.active) : 0;
  const hb = atk.hitbox;

  // Local-space capsule point, swept from `from` to `to`, rotated into world.
  // Local +z is FORWARD and local +x is RIGHT, matching how driveActor derives
  // yaw (forward = (cos yaw, sin yaw) in the xz plane).
  const lx = lerp(hb.from[0], hb.to[0], t);
  const ly = lerp(hb.from[1], hb.to[1], t);
  const lz = lerp(hb.from[2], hb.to[2], t);
  const c = fcos(s.yaw[e]), sn = fsin(s.yaw[e]);
  const wx = s.px[e] + lx * sn + lz * c;
  const wz = s.pz[e] - lx * c + lz * sn;
  const wy = s.py[e] + ly;

  const arc = (hb.arcDeg || 120) * 0.008726646;  // half-arc in radians
  const q = w.scratch.query;
  const n = gridQuery(w.grid, wx, wz, hb.r + 1.2, q, s);
  for (let k = 0; k < n; k++) {
    const target = q[k];
    if (target === e) continue;
    if (s.stateId[target] === S.DEAD) continue;
    // Friendly fire is off: players do not hit players, AI does not hit AI.
    const bothAI = (s.mask[e] & C.AI) && (s.mask[target] & C.AI);
    const bothPlayer = (s.mask[e] & C.PLAYER) && (s.mask[target] & C.PLAYER);
    if (bothAI || bothPlayer) continue;

    const dx = s.px[target] - wx, dz = s.pz[target] - wz;
    const reach = hb.r + s.radius[target];
    if (dx * dx + dz * dz > reach * reach) continue;

    // Vertical overlap: a low sweep should miss something tall and airborne.
    const dy = s.py[target] + s.height[target] * 0.5 - wy;
    if (Math.abs(dy) > s.height[target] * 0.5 + hb.r) continue;

    // Arc test relative to the attacker, so a 360 stomp hits behind and a
    // narrow thrust does not.
    if (arc < 3.1) {
      const a = fatan2(s.pz[target] - s.pz[e], s.px[target] - s.px[e]);
      if (Math.abs(angleDiff(a, s.yaw[e])) > arc) continue;
    }

    const m = flen(dx, dz) || 1;
    const dealt = applyHit(w, e, target, atk, dx / m, dz / m);
    if (dealt > 0 || s.iframeT[target] > 0) s.hitConfirm[e] = 1;
  }
}

/** Move the actor along an attack's lunge or a dodge's roll curve. */
function applyAttackMotion(w, e, atk, motion) {
  const s = w.store;
  if (atk.kind === 'dodge') {
    const total = atk.windup + atk.recovery;
    const t = clamp01(s.phaseT[e] / total);
    const speed = (atk.motion.dist / (total / 60)) * rollSpeed(t);
    s.vx[e] = fcos(s.yaw[e]) * speed * motion;
    s.vz[e] = fsin(s.yaw[e]) * speed * motion;
    return;
  }
  if (atk.lunge && s.phaseT[e] < atk.lunge.ticks) {
    s.vx[e] = fcos(s.yaw[e]) * atk.lunge.speed * motion;
    s.vz[e] = fsin(s.yaw[e]) * atk.lunge.speed * motion;
  } else if (atk.kind === 'melee') {
    // Animation commitment: you do not steer out of a swing.
    s.vx[e] *= 0.75;
    s.vz[e] *= 0.75;
  }
}

/** Try to consume a buffered action during recovery or dodge-actionable frames. */
function tryCancel(w, e, atk, thresholds) {
  const s = w.store;
  if (bufferHas(s, e, ACT.DODGE) && s.phaseT[e] >= thresholds.dodge
      && hasStamina(s, e, DODGE.stamina)) {
    bufferTake(s, e, ACT.DODGE);
    return startAttack(w, e, 'player_dodge');
  }
  if (bufferHas(s, e, ACT.ATTACK) && s.phaseT[e] >= thresholds.combo && atk.next.length > 0) {
    bufferTake(s, e, ACT.ATTACK);
    const nextName = atk.next[Math.min(s.comboIdx[e], atk.next.length - 1)];
    s.comboIdx[e]++;
    return startAttack(w, e, nextName);
  }
  if (bufferHas(s, e, ACT.HEAVY) && s.phaseT[e] >= thresholds.combo) {
    bufferTake(s, e, ACT.HEAVY);
    return startAttack(w, e, 'player_heavy');
  }
  return false;
}

export function combatSystem(w, _dt) {
  const s = w.store;
  const motion = w.motionScale;

  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    if ((s.mask[e] & C.COMBAT) === 0) continue;

    const state = s.stateId[e];
    if (state === S.DEAD) continue;

    // phaseT is MOTION time: hitstop freezes it. stateT is RAW time.
    s.phaseT[e] += motion;
    if (s.stateT[e] > 0) s.stateT[e] -= 1;
    if (s.cooldown[e] > 0) s.cooldown[e] -= 1;
    if (s.blockT[e] > 0) s.blockT[e] -= 1;

    const atk = attackByIndex(s.attackId[e]);

    switch (state) {
      case S.IDLE: {
        if (s.mask[e] & C.PLAYER) {
          if (bufferTake(s, e, ACT.DODGE) && hasStamina(s, e, DODGE.stamina)) {
            startAttack(w, e, 'player_dodge');
          } else if (bufferTake(s, e, ACT.ATTACK)) {
            s.comboIdx[e] = 1;
            applyTouchMagnet(w, e, ATTACKS.player_light_1);
            startAttack(w, e, 'player_light_1');
          } else if (bufferTake(s, e, ACT.HEAVY)) {
            applyTouchMagnet(w, e, ATTACKS.player_heavy);
            startAttack(w, e, 'player_heavy');
          } else if (bufferHas(s, e, ACT.BLOCK)) {
            s.stateId[e] = S.BLOCK;
            s.phaseT[e] = 0;
            s.blockT[e] = 0;
          }
        }
        break;
      }

      case S.WINDUP: {
        // Steering during the wind-up is capped, so a telegraph cannot be
        // rotated onto you at the last instant.
        if (s.aiTarget[e] !== -1 && atk.turnCap > 0) {
          const t = deref(s, s.aiTarget[e]);
          if (t !== -1) {
            const want = fatan2(s.pz[t] - s.pz[e], s.px[t] - s.px[e]);
            s.yaw[e] = approachAngle(s.yaw[e], want, atk.turnCap * motion);
          }
        }
        applyAttackMotion(w, e, atk, motion);
        if (s.phaseT[e] >= atk.windup) {
          s.stateId[e] = S.ACTIVE;
          s.phaseT[e] = 0;
          clearHitMask(s, e);
          emit(w.events, EV.SWING, e, -1,
            s.px[e], s.py[e] + 1.1, s.pz[e], 0, atk.weight, 0, atk.index, 0);
        }
        break;
      }

      case S.ACTIVE: {
        applyAttackMotion(w, e, atk, motion);
        resolveActive(w, e, atk);
        if (s.phaseT[e] >= atk.active) {
          s.stateId[e] = S.RECOVERY;
          s.phaseT[e] = 0;
        }
        break;
      }

      case S.RECOVERY: {
        const hit = s.hitConfirm[e] ? 1 : 0;
        const total = hit ? atk.recoveryOnHit : atk.recovery;
        if (s.mask[e] & C.PLAYER) {
          if (tryCancel(w, e, atk, atk._cancel[hit])) break;
        }
        if (s.phaseT[e] >= total) {
          s.stateId[e] = S.IDLE;
          s.phaseT[e] = 0;
          if (atk.next.length === 0) s.comboIdx[e] = 0;
        }
        break;
      }

      case S.DODGE: {
        const total = atk.windup + atk.recovery;
        const ph = s.phaseT[e];
        s.iframeT[e] = (ph >= atk.iframes[0] && ph <= atk.iframes[1]) ? 2 : s.iframeT[e];
        applyAttackMotion(w, e, atk, motion);
        if (ph >= atk.actionableFrom && (s.mask[e] & C.PLAYER)) {
          if (tryCancel(w, e, atk, { combo: atk.actionableFrom, dodge: atk.actionableFrom })) break;
        }
        if (ph >= total) {
          s.stateId[e] = S.IDLE;
          s.phaseT[e] = 0;
          s.comboIdx[e] = 0;
        }
        break;
      }

      case S.PARRY: {
        s.vx[e] *= 0.6; s.vz[e] *= 0.6;
        if (s.phaseT[e] >= atk.recovery) {
          s.stateId[e] = S.IDLE;
          s.phaseT[e] = 0;
        }
        break;
      }

      case S.BLOCK: {
        s.blockT[e] += 1;
        s.vx[e] *= 0.7; s.vz[e] *= 0.7;
        if (!bufferHas(s, e, ACT.BLOCK)) {
          s.stateId[e] = S.IDLE;
          s.phaseT[e] = 0;
        }
        break;
      }

      case S.HURT:
      case S.STAGGER: {
        s.vx[e] *= 0.85; s.vz[e] *= 0.85;
        if (s.stateT[e] <= 0) {
          s.stateId[e] = S.IDLE;
          s.phaseT[e] = 0;
          s.comboIdx[e] = 0;
        }
        break;
      }

      case S.BROKEN: {
        s.vx[e] *= 0.9; s.vz[e] *= 0.9;
        bufferClear(s, e);
        if (s.vulnerableT[e] <= 0) {
          s.stateId[e] = S.IDLE;
          s.phaseT[e] = 0;
        }
        break;
      }
    }
  }
}

/** Advance the global hitstop clock. Runs BEFORE every other system. */
export function hitstopSystem(w) {
  if (w.hitstopT > 0) {
    w.hitstopT -= 1;
    w.motionScale = 0;
  } else {
    w.motionScale = 1;
  }
}
