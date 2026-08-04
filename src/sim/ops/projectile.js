// projectile.js — spawn a projectile.
// L2 (ops).
//
// Both the combat state machine (when an attack's ACTIVE phase begins) and any
// future scripted source need this, so it cannot live inside either system.

import { spawn } from '../store.js';
import { C, S } from '../components.js';
import { handleIndex } from '../../core/handles.js';
import { fcos, fsin } from '../../core/fixed.js';
import { emit } from '../../core/bus.js';
import { EV } from '../../data/events.js';

export function spawnProjectile(w, ownerIdx, ownerHandle, atk) {
  const s = w.store;
  const spec = atk.projectile;
  if (!spec) return -1;

  const h = spawn(s, 0, C.TRANSFORM | C.VELOCITY | C.PROJECTILE);
  if (h === -1) return -1;
  const i = handleIndex(h);

  const yaw = s.yaw[ownerIdx];
  const c = fcos(yaw), sn = fsin(yaw);

  // Launch from chest height, slightly ahead, so it does not clip the shooter.
  s.px[i] = s.px[ownerIdx] + c * 1.1;
  s.pz[i] = s.pz[ownerIdx] + sn * 1.1;
  s.py[i] = s.py[ownerIdx] + 1.2;
  s.yaw[i] = yaw;
  s.vx[i] = c * spec.speed;
  s.vz[i] = sn * spec.speed;
  s.vy[i] = 0;
  s.radius[i] = spec.radius;
  s.height[i] = spec.radius * 2;

  s.owner[i] = ownerHandle;
  s.attackId[i] = atk.index;
  s.atk[i] = s.atk[ownerIdx];
  // stateT is the RAW-clock lifetime in ticks.
  s.stateT[i] = Math.round(spec.life * 60);
  s.stateId[i] = S.IDLE;

  emit(w.events, EV.PROJECTILE_SPAWN, ownerHandle, -1,
    s.px[i], s.py[i], s.pz[i], 0, atk.weight, 0, atk.index, 0);
  return h;
}
