// attack.js — begin an attack.
// L2 (ops).
//
// Both the player state machine and the enemy AI start attacks, so this cannot
// live in either of them without one system importing the other.

import { S } from '../components.js';
import { clearHitMask } from '../store.js';
import { ATTACKS } from '../../data/attacks.js';
import { hasStamina, spend } from './stamina.js';
import { emit } from '../../core/bus.js';
import { EV } from '../../data/events.js';

export function startAttack(w, e, atkName) {
  const s = w.store;
  const atk = ATTACKS[atkName];
  if (!atk) return false;

  if (atk.stamina > 0 && !hasStamina(s, e, atk.minStamina ?? atk.stamina)) {
    // The "you tried and could not" cue. Silence here reads as a dropped input.
    emit(w.events, EV.EXHAUSTED, e, e, atk.stamina - s.stamina[e], 0, 0, 0, 0.2, 0, 0, 0);
    return false;
  }
  if (atk.stamina > 0) spend(s, e, atk.stamina);

  s.attackId[e] = atk.index;
  s.phaseT[e] = 0;
  s.hitConfirm[e] = 0;
  clearHitMask(s, e);

  if (atk.kind === 'dodge') {
    s.stateId[e] = S.DODGE;
    emit(w.events, EV.DODGE, e, e, s.px[e], s.py[e], s.pz[e], 0, atk.weight, 0, atk.index, 0);
  } else if (atk.kind === 'parry') {
    s.stateId[e] = S.PARRY;
  } else {
    s.stateId[e] = S.WINDUP;
  }
  return true;
}
