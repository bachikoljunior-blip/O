// motion.js — the shared "drive an actor from a desired direction" operation.
// L2 (ops: store mutations that several systems need to perform).
//
// Player intent and enemy AI both route through here, so acceleration,
// deceleration and turn rate are identical for everyone. When they diverge,
// enemies start feeling like they are on ice while the player is not.

import { S } from '../components.js';
import { LOCOMOTION } from '../../data/tuning.js';
import { flen, fatan2 } from '../../core/fixed.js';
import { approachAngle } from '../../core/math.js';

const DT = 1 / 60;

export function driveActor(w, e, dirX, dirZ, speed) {
  const s = w.store;
  const st = s.stateId[e];
  // Animation commitment: these states own the actor's motion outright. This
  // is the rule that makes attacks feel like decisions rather than suggestions.
  if (st === S.WINDUP || st === S.ACTIVE || st === S.DODGE
      || st === S.HURT || st === S.STAGGER || st === S.BROKEN || st === S.DEAD) return;

  const m = flen(dirX, dirZ);
  if (m < 0.001) {
    const drag = LOCOMOTION.decel * DT;
    const cur = flen(s.vx[e], s.vz[e]);
    if (cur <= drag) { s.vx[e] = 0; s.vz[e] = 0; }
    else {
      const f = (cur - drag) / cur;
      s.vx[e] *= f; s.vz[e] *= f;
    }
    return;
  }

  const nx = dirX / m, nz = dirZ / m;
  const a = Math.min(1, LOCOMOTION.accel * DT);
  s.vx[e] += (nx * speed - s.vx[e]) * a;
  s.vz[e] += (nz * speed - s.vz[e]) * a;

  const want = fatan2(nz, nx);
  const guarding = st === S.BLOCK || st === S.PARRY;
  s.yaw[e] = approachAngle(s.yaw[e], want, LOCOMOTION.turnRateRad * DT * (guarding ? 0.4 : 1));
}
