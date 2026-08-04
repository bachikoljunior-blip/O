// validate-data.js — turn design rules into CI failures.
//
// The telegraph budget is the highest-leverage check here. "Every enemy needs
// three distinct wind-ups and one delayed variant" is the rule that converts
// pattern memorisation into actual reading — and it is exactly the kind of
// rule that quietly rots unless something red enforces it.

import { ATTACKS } from '../src/data/attacks.js';
import { ENEMIES, ENEMY_IDS } from '../src/data/enemies.js';
import { TELEGRAPH } from '../src/data/tuning.js';

const errors = [];
const warn = [];

// ——— attacks ———
for (const [id, a] of Object.entries(ATTACKS).sort()) {
  if (a.windup + a.active + a.recovery <= 0) errors.push(`${id}: zero total duration`);
  if (a.cancel) {
    for (const [k, v] of Object.entries(a.cancel)) {
      if (v < 0 || v > 1) errors.push(`${id}: cancel.${k}=${v} outside [0,1]`);
    }
  }
  for (const nx of a.next || []) {
    if (!ATTACKS[nx]) errors.push(`${id}: next -> "${nx}" does not resolve`);
  }
  if (a.kind === 'melee' && !a.hitbox) errors.push(`${id}: melee with no hitbox`);
  if (a.hitbox && a.hitbox.r <= 0) errors.push(`${id}: hitbox radius must be positive`);
  if (a.recoveryOnHit > a.recovery) {
    errors.push(`${id}: recoveryOnHit (${a.recoveryOnHit}) exceeds whiff recovery (${a.recovery})`);
  }
  if (a.weight < 0 || a.weight > 1) errors.push(`${id}: weight ${a.weight} outside [0,1]`);
  if (a.kind === 'dodge') {
    if (!a.iframes) errors.push(`${id}: dodge with no i-frame window`);
    else if (a.iframes[1] <= a.iframes[0]) errors.push(`${id}: empty i-frame window`);
  }
}

// ——— enemy telegraph budget ———
for (const id of ENEMY_IDS) {
  const def = ENEMIES[id];
  if (!def.moveset || def.moveset.length === 0) {
    errors.push(`enemy ${id}: no moveset`);
    continue;
  }

  const windups = new Set();
  let hasDelayVariant = false;

  for (const mv of def.moveset) {
    const a = ATTACKS[mv.atk];
    if (!a) { errors.push(`enemy ${id}: move "${mv.atk}" does not resolve`); continue; }
    windups.add(a.windup);
    if (a.delayVariant) hasDelayVariant = true;

    if (a.windup < TELEGRAPH.minWindupTicks) {
      errors.push(
        `enemy ${id}: "${mv.atk}" wind-up ${a.windup}t is under the ${TELEGRAPH.minWindupTicks}t ` +
        `(380ms) floor — unreadable given 60-100ms of touch latency`);
    }
    if (!a.tell) warn.push(`enemy ${id}: "${mv.atk}" has no visual tell`);
    if (mv.range[0] > mv.range[1]) errors.push(`enemy ${id}: "${mv.atk}" inverted range`);
    if (!(mv.cooldown > 0)) errors.push(`enemy ${id}: "${mv.atk}" needs a positive cooldown`);
  }

  if (windups.size < TELEGRAPH.minDistinctWindups) {
    errors.push(
      `enemy ${id}: only ${windups.size} distinct wind-up durations, needs ` +
      `${TELEGRAPH.minDistinctWindups} — a single rhythm is memorised, not read`);
  }
  if (TELEGRAPH.requireDelayVariant && !hasDelayVariant) {
    errors.push(
      `enemy ${id}: no delayed variant — without one, the fight is pattern ` +
      `recall rather than reading`);
  }
  if (def.hp <= 0 || def.atk <= 0) errors.push(`enemy ${id}: non-positive hp/atk`);
  if (def.stance <= 0) errors.push(`enemy ${id}: needs a stance meter`);
}

for (const w of warn) console.warn('  warn: ' + w);

if (errors.length) {
  console.error(`\nData validation FAILED (${errors.length}):\n`);
  for (const e of errors) console.error('  ' + e);
  process.exit(1);
}
console.log(`Data validation passed: ${Object.keys(ATTACKS).length} attacks, ${ENEMY_IDS.length} enemies.`);
