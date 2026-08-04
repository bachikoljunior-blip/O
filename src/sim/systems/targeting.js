// targeting.js — lock-on with hysteresis.
// L2.
//
// `world.lockTarget` existed but was never assigned, so the camera was always
// called with `null` and lock-on did not exist. Without it a third-person
// fight on a phone is unplayable: you cannot orbit the camera and attack with
// the same thumb.
//
// Sticky by design. Switching requires sustained directional INTENT, not a
// flick — otherwise the camera snaps between enemies every time two of them
// cross, which is worse than no lock at all.

import { C, S, BTN } from '../components.js';
import { deref, NULL_HANDLE, makeHandle } from '../../core/handles.js';
import { TARGETING } from '../../data/tuning.js';
import { gridQuery } from '../../core/spatial.js';
import { flen, fatan2 } from '../../core/fixed.js';
import { angleDiff } from '../../core/math.js';

/** Nearest valid enemy within the cone, or -1. */
function findTarget(w, p, aimYaw, cone) {
  const s = w.store;
  const q = w.scratch.query;
  const n = gridQuery(w.grid, s.px[p], s.pz[p], TARGETING.maxLockDistance, q, s);

  let best = -1, bestScore = Infinity;
  for (let k = 0; k < n; k++) {
    const t = q[k];
    if (t === p) continue;
    if ((s.mask[t] & C.AI) === 0) continue;
    if (s.stateId[t] === S.DEAD) continue;

    const dx = s.px[t] - s.px[p], dz = s.pz[t] - s.pz[p];
    const dist = flen(dx, dz);
    if (dist > TARGETING.maxLockDistance) continue;

    const a = fatan2(dz, dx);
    const off = Math.abs(angleDiff(a, aimYaw));
    if (off > cone) continue;

    // Prefer close and centred. Tie-break on index so the choice is
    // deterministic when two enemies are equidistant.
    const score = dist + off * 12;
    if (score < bestScore || (score === bestScore && t < best)) {
      bestScore = score; best = t;
    }
  }
  return best;
}

export function targetingSystem(w, intent) {
  const s = w.store;
  const p = deref(s, w.player);
  if (p === -1) { w.lockTarget = NULL_HANDLE; return; }

  const cur = deref(s, w.lockTarget);

  // Drop a target that died, or that walked out of range.
  if (cur !== -1) {
    const dead = s.stateId[cur] === S.DEAD;
    const far = flen(s.px[cur] - s.px[p], s.pz[cur] - s.pz[p])
      > TARGETING.maxLockDistance * 1.35;
    if (dead || far) {
      w.lockTarget = NULL_HANDLE;
      // Re-acquire after a beat, so the camera does not snap the instant a
      // target dies mid-swing.
      w.lockHoldT = dead ? TARGETING.reacquireDelayTicks : 0;
      return;
    }
  }

  if (w.lockHoldT > 0) { w.lockHoldT -= 1; return; }
  if (!intent) return;

  // Toggle.
  if (intent.pressed & BTN.LOCK) {
    if (cur !== -1) { w.lockTarget = NULL_HANDLE; return; }
    const t = findTarget(w, p, intent.camYaw, Math.PI);
    w.lockTarget = t === -1 ? NULL_HANDLE : makeHandle(t, s.gen[t]);
    return;
  }

  if (cur === -1) return;

  // Switch targets on sustained stick intent perpendicular to the lock.
  const mag = Math.sqrt(intent.moveX * intent.moveX + intent.moveZ * intent.moveZ);
  if (mag < 0.6) { w.lockSwitchT = 0; return; }

  const want = fatan2(intent.moveZ, intent.moveX) + intent.camYaw;
  const toCur = fatan2(s.pz[cur] - s.pz[p], s.px[cur] - s.px[p]);
  if (Math.abs(angleDiff(want, toCur)) < TARGETING.switchAngleRad) {
    w.lockSwitchT = 0;
    return;
  }

  w.lockSwitchT = (w.lockSwitchT || 0) + 1;
  if (w.lockSwitchT < TARGETING.switchHoldTicks) return;
  w.lockSwitchT = 0;

  const t = findTarget(w, p, want, TARGETING.reacquireConeRad);
  if (t !== -1 && t !== cur) w.lockTarget = makeHandle(t, s.gen[t]);
}
