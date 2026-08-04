// locomotion.js — movement, gravity, ground clamping, knockback decay.
// L2.

import { C, S } from '../components.js';
import { LOCOMOTION, HITSTOP } from '../../data/tuning.js';
import { flen, fatan2 } from '../../core/fixed.js';
import { emit } from '../../core/bus.js';
import { EV } from '../../data/events.js';
import { gridClear, gridInsert } from '../../core/spatial.js';

const DT = 1 / 60;

/** Rebuild the broad-phase grid. Must run before anything that queries it. */
export function spatialSystem(w) {
  const s = w.store;
  gridClear(w.grid);
  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    gridInsert(w.grid, e, s.px[e], s.pz[e]);
  }
}

export function locomotionSystem(w, _dt) {
  const s = w.store;
  const motion = w.motionScale;
  const terrain = w.terrain;

  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    if ((s.mask[e] & C.TRANSFORM) === 0) continue;

    // Knockback is withheld during the opening frames of hitstop so the hit
    // reads as a snap rather than a slide.
    const kbActive = w.hitstopT <= HITSTOP.preMoveTicks;
    if (kbActive) {
      s.px[e] += s.kx[e] * DT * motion;
      s.pz[e] += s.kz[e] * DT * motion;
      s.vy[e] += s.ky[e];
      s.ky[e] = 0;
    }
    // Frame-rate-correct decay is a constant here because dt is fixed.
    s.kx[e] *= 0.86;
    s.kz[e] *= 0.86;

    s.px[e] += s.vx[e] * DT * motion;
    s.pz[e] += s.vz[e] * DT * motion;

    if (s.mask[e] & C.GROUNDED) {
      const ground = terrain ? terrain.heightAt(s.px[e], s.pz[e]) : 0;
      s.vy[e] += LOCOMOTION.gravity * DT * motion;
      s.py[e] += s.vy[e] * DT * motion;
      if (s.py[e] <= ground) {
        if (s.vy[e] < -6) {
          emit(w.events, EV.LAND, e, -1, s.px[e], ground, s.pz[e], 0,
            Math.min(1, -s.vy[e] / 18), 0, 0, 0);
        }
        s.py[e] = ground;
        s.vy[e] = 0;
      }
    }
  }
}

/** Footfall events, so surface-aware audio does not need an animation system. */
export function footstepSystem(w) {
  const s = w.store;
  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    if ((s.mask[e] & C.GROUNDED) === 0) continue;
    const spd = flen(s.vx[e], s.vz[e]);
    if (spd < 0.6) { s.aiTimer[e] = s.aiTimer[e]; continue; }
    // Stride cadence scales with speed; stored in the unused stateT slot for
    // non-combat actors would collide, so we use a derived phase instead.
    const phase = (w.tick * spd) % 42;
    if (phase < spd) {
      emit(w.events, EV.STEP, e, -1, s.px[e], s.py[e], s.pz[e], 0,
        Math.min(1, spd / LOCOMOTION.runSpeed), 0, 0, 0);
    }
  }
}
