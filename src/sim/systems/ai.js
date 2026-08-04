// ai.js — enemy behaviour selection.
// L2.
//
// Includes the habit-learning layer: per-archetype online statistics on how
// the player actually behaves, fed into move-selection weights. It is about a
// hundred lines and produces "the game read me" moments that none of the
// reference titles deliver, because their bosses are fixed move tables with
// aggro-token gating.

import { C, S, ACT } from '../components.js';
import { ENEMIES, ENEMY_IDS, enemyByIndex } from '../../data/enemies.js';
import { ATTACKS } from '../../data/attacks.js';
import { startAttack } from '../ops/attack.js';
import { driveActor } from '../ops/motion.js';
import { deref, NULL_HANDLE } from '../../core/handles.js';
import { flen, fatan2, fsin } from '../../core/fixed.js';
import { hash3 } from '../../core/noise.js';
import { streamSeed } from '../../core/rng.js';
import { S as STREAM } from '../determinism/streams.js';
import { angleDiff } from '../../core/math.js';

export const AI = { IDLE: 0, WANDER: 1, CHASE: 2, STRAFE: 3, ATTACK: 4, RETREAT: 5, LEASH: 6 };

/**
 * Per-archetype behaviour model of the PLAYER, accumulated online.
 * Counts are small integers so the whole thing serialises trivially.
 */
export function createHabitModel() {
  const m = {};
  for (const id of ENEMY_IDS) {
    m[id] = {
      dodgesEarly: 0,   // player dodged before the active frames
      dodgesLate: 0,    // player dodged into the active frames
      punishAfter: 0,   // player attacked immediately after our recovery
      blocks: 0,
      samples: 0,
    };
  }
  return m;
}

/** Bias move selection toward what has been working on this player. */
function habitBias(model, enemyId, move) {
  const h = model[enemyId];
  if (!h || h.samples < 8) return 1;
  const early = h.dodgesEarly / h.samples;
  const atk = ATTACKS[move.atk];
  // A player who dodges early is punished by the delayed variant, and by long
  // wind-ups generally; a player who blocks is punished by stance damage.
  if (early > 0.5 && atk.windup > 30) return 1.6;
  if (h.blocks / h.samples > 0.5 && atk.stance > 25) return 1.5;
  return 1;
}

function pickMove(w, e, def, dist) {
  const s = w.store;
  const seed = streamSeed(w.seed, STREAM.AI_DECIDE);
  let total = 0;
  const weights = w.scratch.candidates;
  const hpFrac = s.hp[e] / Math.max(1, s.hpMax[e]);

  for (let i = 0; i < def.moveset.length; i++) {
    const mv = def.moveset[i];
    let ok = dist >= mv.range[0] && dist <= mv.range[1];
    if (ok && mv.maxHpFrac !== undefined && hpFrac > mv.maxHpFrac) ok = false;
    if (ok && mv.minHpFrac !== undefined && hpFrac < mv.minHpFrac) ok = false;
    const wgt = ok ? mv.w * habitBias(w.habits, def.id, mv) : 0;
    weights[i] = wgt * 1000;
    total += weights[i];
  }
  if (total <= 0) return -1;

  // Keyed on (entity, tick): stateless, so adding a system that draws a random
  // number cannot shift what this enemy decides.
  let r = hash3(e, w.tick, 1, seed) * total;
  for (let i = 0; i < def.moveset.length; i++) {
    r -= weights[i];
    if (r <= 0) return i;
  }
  return def.moveset.length - 1;
}

export function aiSystem(w, _dt) {
  const s = w.store;
  if (!w.habits) w.habits = createHabitModel();

  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    if ((s.mask[e] & C.AI) === 0) continue;
    if (s.stateId[e] === S.DEAD) continue;

    const def = enemyByIndex(s.kind[e]);
    if (!def) continue;

    // Only IDLE actors decide; everything else is committed to an animation.
    const committed = s.stateId[e] !== S.IDLE && s.stateId[e] !== S.BLOCK;

    let target = deref(s, s.aiTarget[e]);
    if (target === -1 && w.player !== NULL_HANDLE) {
      const p = deref(s, w.player);
      if (p !== -1) {
        const d = flen(s.px[p] - s.px[e], s.pz[p] - s.pz[e]);
        if (d < def.aggroRange) {
          s.aiTarget[e] = w.player;
          target = p;
          s.aiState[e] = AI.CHASE;
        }
      }
    }

    if (target === -1) {
      s.aiState[e] = AI.IDLE;
      if (!committed) driveActor(w, e, 0, 0, 0);
      continue;
    }

    const dx = s.px[target] - s.px[e];
    const dz = s.pz[target] - s.pz[e];
    const dist = flen(dx, dz);

    // Leash: give up and go home rather than trailing across the continent.
    const homeDist = flen(s.px[e] - s.homeX[e], s.pz[e] - s.homeZ[e]);
    if (homeDist > 60) {
      s.aiState[e] = AI.LEASH;
      s.aiTarget[e] = NULL_HANDLE;
      if (!committed) {
        driveActor(w, e, s.homeX[e] - s.px[e], s.homeZ[e] - s.pz[e], def.speed);
      }
      continue;
    }

    if (committed) continue;

    if (s.cooldown[e] <= 0 && dist <= def.keepDistance + 1.6) {
      const mi = pickMove(w, e, def, dist);
      if (mi >= 0) {
        const mv = def.moveset[mi];
        s.cooldown[e] = mv.cooldown;
        s.moveIdx[e] = mi;
        // The delayed variant: same pose, held longer. One number, and it is
        // what converts pattern memorisation into actual reading.
        const roll = hash3(e, w.tick, 2, streamSeed(w.seed, STREAM.AI_DELAY));
        const atkDef = ATTACKS[mv.atk];
        if (roll < (def.delayedChance || 0) && atkDef.delayVariant) {
          startAttack(w, e, mv.atk);
          s.phaseT[e] = -atkDef.delayVariant.holdTicks;
        } else {
          startAttack(w, e, mv.atk);
        }
        s.aiState[e] = AI.ATTACK;
        continue;
      }
    }

    // Approach, back off, or circle. The strafe jitter is phase-offset per
    // entity so a pack spreads out instead of stacking into one silhouette.
    const want = def.keepDistance;
    if (dist > want + 0.8) {
      s.aiState[e] = AI.CHASE;
      const jitter = fsin(w.tick * 0.023 + e * 1.7) * 0.28;
      driveActor(w, e, dx + -dz * jitter, dz + dx * jitter, def.speed);
    } else if (dist < want * 0.7) {
      s.aiState[e] = AI.RETREAT;
      driveActor(w, e, -dx, -dz, def.speed * 0.7);
    } else {
      s.aiState[e] = AI.STRAFE;
      const side = (e & 1) ? 1 : -1;
      driveActor(w, e, -dz * side, dx * side, def.speed * 0.55);
    }
  }
}

/** Observe what the player did, so the habit model has something to learn from. */
export function observePlayer(w, act, enemyId, phaseFrac) {
  if (!w.habits || !w.habits[enemyId]) return;
  const h = w.habits[enemyId];
  h.samples++;
  if (act === ACT.DODGE) {
    if (phaseFrac < 0.6) h.dodgesEarly++; else h.dodgesLate++;
  } else if (act === ACT.BLOCK) {
    h.blocks++;
  }
  // Bound the counts so old behaviour decays out rather than dominating forever.
  if (h.samples > 200) {
    h.samples >>= 1; h.dodgesEarly >>= 1; h.dodgesLate >>= 1;
    h.punishAfter >>= 1; h.blocks >>= 1;
  }
}
