// projectile.js — move projectiles and resolve their contacts.
// L2.
//
// Ranged attacks were declared in data (`machine_beam`, the PROJECTILE
// component bit) but nothing implemented them, so the machine beast could
// select a move that silently did nothing. This closes that.

import { C, S } from '../components.js';
import { despawn } from '../store.js';
import { deref } from '../../core/handles.js';
import { attackByIndex } from '../../data/attacks.js';
import { gridQuery } from '../../core/spatial.js';
import { flen } from '../../core/fixed.js';
import { applyHit } from '../ops/damage.js';
import { emit } from '../../core/bus.js';
import { EV } from '../../data/events.js';

const DT = 1 / 60;

export function projectileSystem(w, _dt) {
  const s = w.store;
  const motion = w.motionScale;
  const terrain = w.terrain;
  const q = w.scratch.query;

  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    if ((s.mask[e] & C.PROJECTILE) === 0) continue;

    // Lifetime runs on the RAW clock: hitstop should not extend a bolt's flight.
    if (--s.stateT[e] <= 0) { despawn(s, e); continue; }

    s.px[e] += s.vx[e] * DT * motion;
    s.py[e] += s.vy[e] * DT * motion;
    s.pz[e] += s.vz[e] * DT * motion;

    const atk = attackByIndex(s.attackId[e]);
    const ownerIdx = deref(s, s.owner[e]);

    // Terrain impact.
    if (terrain && s.py[e] <= terrain.heightAt(s.px[e], s.pz[e])) {
      emit(w.events, EV.PROJECTILE_HIT, s.owner[e], -1,
        s.px[e], s.py[e], s.pz[e], 0, atk.weight * 0.5, 0, atk.index, 0);
      despawn(s, e);
      continue;
    }

    // Actor impact. Owner-side entities are skipped so a beast cannot shoot
    // its own herd and the player cannot shoot themselves point-blank.
    const n = gridQuery(w.grid, s.px[e], s.pz[e], s.radius[e] + 1.4, q, s);
    let hit = false;
    for (let j = 0; j < n; j++) {
      const t = q[j];
      if (t === e || t === ownerIdx) continue;
      if ((s.mask[t] & C.PROJECTILE) !== 0) continue;
      if (s.stateId[t] === S.DEAD) continue;
      if (ownerIdx !== -1) {
        const bothAI = (s.mask[ownerIdx] & C.AI) && (s.mask[t] & C.AI);
        const bothPlayer = (s.mask[ownerIdx] & C.PLAYER) && (s.mask[t] & C.PLAYER);
        if (bothAI || bothPlayer) continue;
      }

      const dx = s.px[t] - s.px[e], dz = s.pz[t] - s.pz[e];
      const reach = s.radius[e] + s.radius[t];
      if (dx * dx + dz * dz > reach * reach) continue;
      const dy = s.py[t] + s.height[t] * 0.5 - s.py[e];
      if (Math.abs(dy) > s.height[t] * 0.5 + s.radius[e]) continue;

      const m = flen(dx, dz) || 1;
      // The projectile is the attacker for hit-dedup purposes, so a single
      // bolt cannot register twice on the same target.
      applyHit(w, e, t, atk, dx / m, dz / m);
      emit(w.events, EV.PROJECTILE_HIT, s.owner[e], t,
        s.px[e], s.py[e], s.pz[e], 0, atk.weight, 0, atk.index, 0);
      hit = true;
      break;
    }
    if (hit) despawn(s, e);
  }
}
