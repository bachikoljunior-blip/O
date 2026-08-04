// archetypes.js — spawn templates. Which columns get written, and to what.
// L2.

import { spawn } from './store.js';
import { C, S } from './components.js';
import { ENEMIES, ENEMY_IDS } from '../data/enemies.js';
import { STAMINA } from '../data/tuning.js';
import { deref } from '../core/handles.js';

export const KIND_PLAYER = 65535;

export function spawnPlayer(w, x, z) {
  const s = w.store;
  const h = spawn(s, KIND_PLAYER,
    C.TRANSFORM | C.VELOCITY | C.HEALTH | C.COMBAT | C.PLAYER | C.GROUNDED | C.PERSISTENT);
  if (h === -1) return -1;
  const e = deref(s, h);

  s.px[e] = x; s.pz[e] = z;
  s.py[e] = w.terrain ? w.terrain.heightAt(x, z) : 0;
  s.radius[e] = 0.4; s.height[e] = 1.8;
  s.hp[e] = s.hpMax[e] = 240;
  s.poise[e] = s.poiseMax[e] = 60;
  s.stance[e] = s.stanceMax[e] = 140;
  s.stamina[e] = s.staminaMax[e] = STAMINA.max;
  s.atk[e] = 14; s.def[e] = 6; s.crit[e] = 0.08;
  s.level[e] = 1;
  s.stateId[e] = S.IDLE;
  s.persistId[e] = 1;
  w.player = h;
  w.stats.spawned++;
  return h;
}

export function spawnEnemy(w, id, x, z, level = 1) {
  const def = ENEMIES[id];
  if (!def) return -1;
  const s = w.store;
  const h = spawn(s, def.index,
    C.TRANSFORM | C.VELOCITY | C.HEALTH | C.COMBAT | C.AI | C.GROUNDED);
  if (h === -1) return -1;
  const e = deref(s, h);

  const scale = 1 + (level - 1) * 0.14;
  s.px[e] = x; s.pz[e] = z;
  s.py[e] = w.terrain ? w.terrain.heightAt(x, z) : 0;
  s.homeX[e] = x; s.homeZ[e] = z;
  s.radius[e] = def.radius; s.height[e] = def.height;
  s.hp[e] = s.hpMax[e] = def.hp * scale;
  s.poise[e] = s.poiseMax[e] = def.poise;
  s.stance[e] = s.stanceMax[e] = def.stance;
  s.stamina[e] = s.staminaMax[e] = 0;   // enemies are not stamina-gated
  s.atk[e] = def.atk * scale;
  s.def[e] = def.def;
  s.level[e] = level;
  s.stateId[e] = S.IDLE;
  s.aiTarget[e] = -1;
  w.stats.spawned++;
  return h;
}

export const enemyIdOf = (kindIndex) => ENEMY_IDS[kindIndex];
