// loop.js — the fixed-timestep driver.
// L7.
//
// Two details here are easy to get wrong and expensive to debug later:
//
//  1. snapshotPrev runs before EVERY substep, not once per frame. Snapshot once
//     while three substeps run and you interpolate across three ticks, and
//     everything smears.
//  2. The frame clamp happens BEFORE the accumulator. Feeding a 3-second
//     background-tab delta into the accumulator produces a 180-step catch-up
//     burst, which produces another long frame, which produces another burst.

import { tick } from '../sim/schedule.js';
import { clearEvents } from '../core/bus.js';

const DT = 1 / 60;
const MAX_SUBSTEPS = 5;     // 83ms of catch-up, then we abandon the backlog
const MAX_FRAME = 0.25;

export function createLoopState(store) {
  const n = store.n;
  return {
    acc: 0,
    prevTime: 0,
    frames: 0,
    fpsAcc: 0,
    fps: 60,
    substeps: 0,
    // Previous-tick transforms, for render interpolation.
    prev: {
      px: new Float32Array(n), py: new Float32Array(n), pz: new Float32Array(n),
      yaw: new Float32Array(n),
    },
  };
}

function snapshotPrev(ls, store) {
  ls.prev.px.set(store.px);
  ls.prev.py.set(store.py);
  ls.prev.pz.set(store.pz);
  ls.prev.yaw.set(store.yaw);
}

/**
 * @param hooks {
 *   beginFrame(), sampleIntent(substep), afterTick(), render(alpha, dt), onFps(fps)
 * }
 */
export function startLoop(world, ls, hooks) {
  ls.prevTime = performance.now();

  function frame(now) {
    requestAnimationFrame(frame);

    let real = (now - ls.prevTime) / 1000;
    ls.prevTime = now;
    if (real > MAX_FRAME) real = MAX_FRAME;

    // 2Hz fps sampling — enough for the quality ladder, cheap enough to ignore.
    ls.fpsAcc += real; ls.frames++;
    if (ls.fpsAcc > 0.5) {
      ls.fps = ls.frames / ls.fpsAcc;
      ls.fpsAcc = 0; ls.frames = 0;
      if (hooks.onFps) hooks.onFps(ls.fps, real);
    }

    hooks.beginFrame(real);
    ls.acc += real;

    let steps = 0;
    while (ls.acc >= DT && steps < MAX_SUBSTEPS) {
      snapshotPrev(ls, world.store);
      tick(world, hooks.sampleIntent(steps));
      hooks.afterTick();
      clearEvents(world.events);
      ls.acc -= DT;
      steps++;
    }
    // Never death-spiral: drop the backlog rather than compounding it.
    if (steps === MAX_SUBSTEPS) ls.acc = 0;
    ls.substeps = steps;

    hooks.render(ls.acc / DT, real);
    if (hooks.endFrame) hooks.endFrame();
  }

  requestAnimationFrame(frame);
}
