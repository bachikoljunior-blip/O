// habits.js — an online model of how THIS player fights.
// L2 (ops).
//
// Combat AI in every reference title is a fixed move table with aggro-token
// gating. None of them adapt to the individual player. This is about a hundred
// lines and produces "the game read me" moments none of them deliver.
//
// It lives in ops rather than in ai.js because the combat system writes to it
// (observing defensive inputs) while the AI system reads it — and systems are
// not allowed to import each other.

import { ENEMY_IDS } from '../../data/enemies.js';
import { ACT } from '../components.js';

export function createHabitModel() {
  const m = {};
  for (const id of ENEMY_IDS) {
    m[id] = {
      dodgesEarly: 0,   // dodged before the active frames — punishable by a delay
      dodgesLate: 0,
      punishAfter: 0,
      blocks: 0,
      samples: 0,
    };
  }
  return m;
}

/** Record one defensive input, attributed to the archetype that provoked it. */
export function observePlayer(w, act, enemyId, phaseFrac) {
  if (!w.habits || !w.habits[enemyId]) return;
  const h = w.habits[enemyId];
  h.samples++;
  if (act === ACT.DODGE) {
    if (phaseFrac < 0.6) h.dodgesEarly++; else h.dodgesLate++;
  } else if (act === ACT.BLOCK) {
    h.blocks++;
  }
  // Halve the counts periodically so old behaviour decays out. Without this the
  // model calcifies around how you played in the first ten minutes.
  if (h.samples > 200) {
    h.samples >>= 1; h.dodgesEarly >>= 1; h.dodgesLate >>= 1;
    h.punishAfter >>= 1; h.blocks >>= 1;
  }
}

/**
 * Weight multiplier for a move against this player.
 * A player who dodges early is punished by long wind-ups and delayed variants;
 * a player who turtles is punished by stance damage.
 */
export function habitBias(model, enemyId, atk) {
  const h = model && model[enemyId];
  if (!h || h.samples < 8) return 1;
  if (h.dodgesEarly / h.samples > 0.5 && atk.windup > 30) return 1.6;
  if (h.blocks / h.samples > 0.5 && atk.stance > 25) return 1.5;
  return 1;
}
