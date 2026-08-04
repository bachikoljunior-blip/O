// director.js — drain simulation events and turn them into feedback.
// L3.
//
// Generic. It reads the REACTORS table and knows nothing about combat. The
// simulation emitted `{type, src, dst, f0..f5, i0, i1}` and never called into
// audio, particles or the camera — which is why the sim runs headless and why
// retuning feel does not risk changing what actually happened in the fight.

import { REACTORS, byWeight } from './reactors.js';
import { EV } from '../data/events.js';
import { burst } from './particles.js';

export function createDirector() {
  return {
    texts: [],          // floating combat text, drained by the HUD
    lastStep: 0,
    muted: false,
  };
}

/**
 * @param ctx { shake(amount), fovKick(amount), particles, audio, haptics, world }
 *
 * Camera response arrives as plain callbacks rather than an import: the
 * presentation layer describes WHAT should happen, and the graphics layer
 * decides how. That keeps this file testable without a renderer.
 */
export function consumeEvents(dir, q, ctx) {
  for (let k = 0; k < q.n; k++) {
    const type = q.type[k];
    const rx = REACTORS[type];
    if (!rx) continue;

    const o = k * 6, p = k * 2;
    const weight = q.f[o + 4];
    const flags = q.i[p + 1];

    // Event payload layout differs per type; HIT carries the impact normal in
    // f1..f3 while positional events carry the position in f0..f2.
    let px, py, pz, nx = 0, ny = 1, nz = 0;
    if (type === EV.HIT) {
      const dst = q.dst[k];
      const s = ctx.world.store;
      px = s.px[dst]; py = s.py[dst] + 1.1; pz = s.pz[dst];
      nx = q.f[o + 1]; ny = q.f[o + 2]; nz = q.f[o + 3];
    } else {
      px = q.f[o]; py = q.f[o + 1]; pz = q.f[o + 2];
    }

    if (rx.shake && ctx.shake) ctx.shake(byWeight(rx.shake.amp, weight));
    if (rx.fovKick && ctx.fovKick) ctx.fovKick(byWeight(rx.fovKick, weight));
    if (rx.haptic && ctx.haptics) {
      ctx.haptics(Math.round(byWeight(rx.haptic.ms, weight)));
    }
    if (rx.sfx && ctx.audio) {
      ctx.audio.play(rx.sfx.id, {
        pitch: byWeight(rx.sfx.pitch, weight),
        vol: byWeight(rx.sfx.vol, weight),
        x: px, y: py, z: pz,
      });
    }
    if (rx.fx && ctx.particles) {
      for (const fx of rx.fx) {
        if (fx.unless && (flags & fx.unless)) continue;
        const count = Math.round(byWeight(fx.count, weight));
        burst(ctx.particles, fx.preset, px, py, pz, count,
          fx.alongNormal ? nx : 0, fx.alongNormal ? ny : 1, fx.alongNormal ? nz : 0);
      }
    }
    if (rx.text) {
      const value = rx.text.literal !== undefined
        ? rx.text.literal
        : Math.round(q.f[o + (rx.text.value === 'f0' ? 0 : 0)]);
      if (value !== 0 && value !== '') {
        const style = (rx.text.altFlag && (flags & rx.text.altFlag))
          ? rx.text.altStyle : rx.text.style;
        dir.texts.push({ value, style, x: px, y: py, z: pz, life: 0.9, max: 0.9 });
      }
    }
  }

  // Floating text ages on real time; it is pure presentation.
  for (let i = dir.texts.length - 1; i >= 0; i--) {
    dir.texts[i].life -= 1 / 60;
    dir.texts[i].y += 0.02;
    if (dir.texts[i].life <= 0) dir.texts.splice(i, 1);
  }
  if (dir.texts.length > 24) dir.texts.splice(0, dir.texts.length - 24);
}
