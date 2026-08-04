// damage.js — damage resolution, poise, stance, parry, i-frames.
// L2.
//
// The entire presentation coupling in this file is one emit() call. Compare
// the previous build, where Player.damage() inlined audio.sfx, FX.blood,
// g.pt.text, g.shake and g.vibrate directly into the damage path — that single
// method is why the old simulation could not be tested headless.

import { emit } from '../../core/bus.js';
import { flen, fatan2 } from '../../core/fixed.js';
import { angleDiff } from '../../core/math.js';
import { EV } from '../../data/events.js';
import { S, HIT_FLAG } from '../components.js';
import { attackByIndex } from '../../data/attacks.js';
import { markHit, wasHit } from '../store.js';
import { DAMAGE, STANCE, PARRY, IFRAME, HITSTOP } from '../../data/tuning.js';
import { drainOnBlock, refundFull } from './stamina.js';
import { bufferClear } from './buffer.js';
import { hash3 } from '../../core/noise.js';
import { streamSeed } from '../../core/rng.js';
import { S as STREAM } from '../determinism/streams.js';

/** Sentinel: contact was made but no damage got through a guard. */
export const BLOCKED = -1;

/**
 * Is this actor currently untouchable?
 *
 * The dodge's window is DERIVED FROM STATE rather than stamped into a counter.
 * A counter written by the dodging entity and read by the attacker only works
 * if the dodger happens to iterate first — so it silently depended on slot
 * order, and a respawn into a recycled slot would have shifted the window by a
 * frame. Deriving it is order-independent and lets the sandbox ask the same
 * question the damage path asks.
 */
export function isInvulnerable(s, e) {
  if (s.iframeT[e] > 0) return true;
  if (s.stateId[e] === S.DODGE) {
    const a = attackByIndex(s.attackId[e]);
    if (!a.iframeFrames) return false;
    const ph = s.phaseT[e];
    return ph >= a.iframes[0] && ph < a.iframes[0] + a.iframeFrames;
  }
  return false;
}

/**
 * Resolve one hit from `src` onto `dst`.
 *
 * Returns damage dealt, 0 for no contact (dead / already hit / i-framed /
 * parried), or BLOCKED. Callers need that distinction: a swing stopped by
 * i-frames is a WHIFF and must not earn the hit-confirm recovery discount,
 * while a swing stopped by a shield is a connect and should.
 * Every branch that changes what the player sees emits an event; nothing here
 * touches audio, particles or the camera.
 */
export function applyHit(w, src, dst, atk, nx, nz) {
  const s = w.store;
  if (s.stateId[dst] === S.DEAD) return 0;
  if (wasHit(s, src, dst)) return 0;
  markHit(s, src, dst);

  // A correct read converts into total invulnerability.
  if (isInvulnerable(s, dst)) return 0;

  let flags = 0;
  let dmg = atk.dmg ? atk.dmg.base * (s.atk[src] || 1) : 0;

  // ——— parry / guard ———
  const incoming = fatan2(-nz, -nx);
  const facingHit = Math.abs(angleDiff(s.yaw[dst], incoming)) < PARRY.guardConeRad;

  if (s.stateId[dst] === S.PARRY && facingHit) {
    const window = PARRY.windowTicks + w.inputProfile.parryWindowBonus;
    if (s.phaseT[dst] < window) {
      // Parried. The attacker's stance takes the hit instead of the defender.
      s.stance[src] *= STANCE.parryStanceMul;
      s.stanceIdleT[src] = 0;
      refundFull(s, dst);
      // Arm the riposte. Consumed by the state machine in IDLE.
      s.riposteT[dst] = PARRY.riposteWindowTicks;
      w.hitstopT = Math.max(w.hitstopT, PARRY.hitstopTicks);
      w.stats.parries++;
      emit(w.events, EV.PARRY, src, dst,
        s.px[dst], s.py[dst] + 1.1, s.pz[dst], 0, 0.8, 0, atk.index, HIT_FLAG.PARRIED);
      if (s.stance[src] <= 0) breakStance(w, src, dst);
      return 0;
    }
  }

  if (s.stateId[dst] === S.BLOCK && facingHit) {
    flags |= HIT_FLAG.BLOCKED;
    dmg *= PARRY.guardDamageMul;
    drainOnBlock(s, dst, PARRY.guardStaminaCost);
    s.iframeT[dst] = IFRAME.onBlockedHit;
    emit(w.events, EV.BLOCK, src, dst,
      s.px[dst], s.py[dst] + 1.1, s.pz[dst], 0, atk.weight * 0.6, 0, atk.index, flags);
    // Guard break: out of stamina means the guard fails outright.
    if (s.stamina[dst] <= 0) {
      enterHurt(w, dst, 30);
    }
  }

  // ——— mitigation ———
  const def = s.def[dst] || 0;
  dmg = dmg * (100 / (100 + def * DAMAGE.defScale));

  // Crit is keyed on (attacker, tick) rather than drawn from a sequence, so
  // adding a system that draws a number cannot shift the crit stream.
  if (s.crit[src] > 0) {
    const roll = hash3(src, w.tick, 0, streamSeed(w.seed, STREAM.CRIT));
    if (roll < s.crit[src]) { dmg *= 1.8; flags |= HIT_FLAG.CRIT; }
  }

  if (s.vulnerableT[dst] > 0) {
    dmg *= STANCE.brokenDamageMul;
    flags |= HIT_FLAG.VULNERABLE;
  }

  dmg = Math.max((flags & HIT_FLAG.BLOCKED) ? 0 : DAMAGE.minDamage, Math.round(dmg));
  const wasBlocked = (flags & HIT_FLAG.BLOCKED) !== 0;

  // ——— apply ———
  if (dmg > 0) {
    s.hp[dst] -= dmg;
    w.stats.hits++;
  }

  if (!(flags & HIT_FLAG.BLOCKED)) {
    s.iframeT[dst] = IFRAME.onHit;

    // Poise: per-hit stagger resistance.
    s.poise[dst] -= atk.poise;
    if (s.poise[dst] <= 0 && s.hp[dst] > 0) {
      s.poise[dst] = s.poiseMax[dst];
      flags |= HIT_FLAG.STAGGER;
      enterHurt(w, dst, 22);
      emit(w.events, EV.STAGGER, src, dst,
        s.px[dst], s.py[dst] + 1.1, s.pz[dst], 0, atk.weight, 0, atk.index, flags);
    }
  }

  // Stance: the break meter. Accumulates from blocked and heavy hits alike.
  s.stance[dst] -= atk.stance;
  s.stanceIdleT[dst] = 0;
  if (s.stance[dst] <= 0 && s.hp[dst] > 0) {
    flags |= HIT_FLAG.STANCE_BREAK;
    breakStance(w, dst, src);
  }

  // Knockback, withheld for a few frames by hitstop so it snaps rather than slides.
  const kb = atk.knock ? atk.knock.impulse : 0;
  s.kx[dst] += nx * kb;
  s.kz[dst] += nz * kb;
  s.ky[dst] += atk.knock ? atk.knock.up : 0;

  if (atk.hitstop > 0 && dmg > 0) {
    w.hitstopT = Math.max(w.hitstopT, atk.hitstop);
  }

  if (s.hp[dst] <= 0) {
    s.hp[dst] = 0;
    flags |= HIT_FLAG.KILL;
    kill(w, dst, src);
  }

  const speed = flen(s.vx[src], s.vz[src]);
  emit(w.events, EV.HIT, src, dst,
    dmg, nx, 0.35, nz, atk.weight, speed, atk.index, flags);
  return wasBlocked ? BLOCKED : dmg;
}

export function breakStance(w, e, by) {
  const s = w.store;
  s.stance[e] = s.stanceMax[e];
  s.stateId[e] = S.BROKEN;
  s.phaseT[e] = 0;
  s.stateT[e] = 0;
  s.vulnerableT[e] = STANCE.brokenTicks;
  bufferClear(s, e);
  w.hitstopT = Math.max(w.hitstopT, HITSTOP.stanceBreak);
  w.stats.breaks++;
  emit(w.events, EV.STANCE_BREAK, by, e,
    s.px[e], s.py[e] + 1.1, s.pz[e], 0, 1.0, 0, 0, HIT_FLAG.STANCE_BREAK);
}

export function enterHurt(w, e, ticks) {
  const s = w.store;
  s.stateId[e] = S.HURT;
  s.phaseT[e] = 0;
  s.stateT[e] = ticks;
  // "Never survive hit-stun" — the buffer is wiped, not just paused.
  bufferClear(s, e);
}

export function kill(w, e, by) {
  const s = w.store;
  s.stateId[e] = S.DEAD;
  s.phaseT[e] = 0;
  s.stateT[e] = 0;
  s.hp[e] = 0;
  bufferClear(s, e);
  w.stats.killed++;
  emit(w.events, EV.DEATH, by, e, s.px[e], s.py[e], s.pz[e], 0, 1.0, 0, s.kind[e], HIT_FLAG.KILL);
}

/** Stance regeneration and the countdown timers that gate it. */
export function stanceSystem(w, _dt) {
  const s = w.store;
  const regenPerTick = STANCE.regenFractionPerSec / 60;
  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    if (s.iframeT[e] > 0) s.iframeT[e] -= 1;
    if (s.vulnerableT[e] > 0) s.vulnerableT[e] -= 1;
    if (s.stanceMax[e] <= 0) continue;

    s.stanceIdleT[e] += 1;
    // `>` would make the wait 179 ticks, not the 180 the table declares.
    if (s.stanceIdleT[e] >= STANCE.idleBeforeRegen) {
      if (s.stance[e] < s.stanceMax[e]) {
        s.stance[e] += s.stanceMax[e] * regenPerTick;
        if (s.stance[e] > s.stanceMax[e]) s.stance[e] = s.stanceMax[e];
      }
      // Poise recovers on the same idle clock. Without this it only ever
      // decreased, so late in a fight everything staggered from a stiff breeze.
      if (s.poise[e] < s.poiseMax[e]) {
        s.poise[e] += s.poiseMax[e] * regenPerTick;
        if (s.poise[e] > s.poiseMax[e]) s.poise[e] = s.poiseMax[e];
      }
    }
  }
}
