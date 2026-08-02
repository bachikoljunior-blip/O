// particles.js — particle systems, floating combat text and screen-space effects.

import { TAU, clamp, lerp, makeRNG, radial } from '../core/util.js';

const rng = makeRNG(0xC0FFEE);

export class Particles {
  constructor(max = 1400) {
    this.a = [];
    this.max = max;
    this.texts = [];
    this.rings = [];
    this.beams = [];
  }

  clear() { this.a.length = 0; this.texts.length = 0; this.rings.length = 0; this.beams.length = 0; }

  spawn(o) {
    if (this.a.length >= this.max) this.a.shift();
    this.a.push({
      x: o.x, y: o.y, z: o.z || 0, vx: o.vx || 0, vy: o.vy || 0, vz: o.vz || 0,
      g: o.g ?? 0, life: o.life ?? 0.6, t: 0, r: o.r ?? 3, r2: o.r2 ?? null,
      col: o.col || '#fff', col2: o.col2 || null, glow: o.glow || 0, drag: o.drag ?? 0.94,
      shape: o.shape || 'circle', rot: o.rot || 0, vr: o.vr || 0, fade: o.fade ?? 1,
    });
  }

  burst(x, y, n, opt = {}) {
    for (let i = 0; i < n; i++) {
      const a = opt.dir != null ? opt.dir + (rng() - 0.5) * (opt.spread ?? 1.2) : rng() * TAU;
      const sp = (opt.speed ?? 60) * (0.4 + rng() * 0.9);
      this.spawn({
        x: x + (rng() - 0.5) * (opt.jitter ?? 4),
        y: y + (rng() - 0.5) * (opt.jitter ?? 4),
        z: opt.z ?? 6,
        vx: Math.cos(a) * sp, vy: Math.sin(a) * sp * 0.6,
        vz: opt.vz ?? (rng() * 40 + 10),
        g: opt.g ?? 120, life: (opt.life ?? 0.6) * (0.6 + rng() * 0.7),
        r: (opt.r ?? 3) * (0.6 + rng() * 0.8), col: opt.col || '#fff', col2: opt.col2,
        glow: opt.glow || 0, drag: opt.drag ?? 0.93, shape: opt.shape || 'circle',
        rot: rng() * TAU, vr: (rng() - 0.5) * 8,
      });
    }
  }

  text(x, y, str, opt = {}) {
    this.texts.push({
      x, y, z: opt.z ?? 24, str, t: 0, life: opt.life ?? 1.0,
      col: opt.col || '#fff', size: opt.size || 18, vy: opt.vy ?? -34,
      vx: opt.vx ?? (rng() - 0.5) * 20, crit: !!opt.crit, weight: opt.weight || 700,
    });
  }

  ring(x, y, opt = {}) {
    this.rings.push({
      x, y, t: 0, life: opt.life ?? 0.4, r0: opt.r0 ?? 4, r1: opt.r1 ?? 48,
      col: opt.col || 'rgba(255,255,255,0.7)', w: opt.w ?? 3, squash: opt.squash ?? 0.45,
    });
  }

  beam(x1, y1, x2, y2, opt = {}) {
    this.beams.push({ x1, y1, x2, y2, t: 0, life: opt.life ?? 0.2, col: opt.col || '#fff', w: opt.w ?? 4, jag: opt.jag ?? 0 });
  }

  update(dt) {
    for (let i = this.a.length - 1; i >= 0; i--) {
      const p = this.a[i];
      p.t += dt;
      if (p.t >= p.life) { this.a.splice(i, 1); continue; }
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.z += p.vz * dt;
      p.vz -= p.g * dt;
      p.rot += p.vr * dt;
      if (p.z < 0) { p.z = 0; p.vz *= -0.42; p.vx *= 0.7; p.vy *= 0.7; }
      const d = Math.pow(p.drag, dt * 60);
      p.vx *= d; p.vy *= d;
    }
    for (let i = this.texts.length - 1; i >= 0; i--) {
      const t = this.texts[i];
      t.t += dt;
      if (t.t >= t.life) { this.texts.splice(i, 1); continue; }
      t.z += -t.vy * dt;
      t.x += t.vx * dt;
      t.vx *= 0.94;
      t.vy *= 0.98;
    }
    for (let i = this.rings.length - 1; i >= 0; i--) {
      const r = this.rings[i];
      r.t += dt;
      if (r.t >= r.life) this.rings.splice(i, 1);
    }
    for (let i = this.beams.length - 1; i >= 0; i--) {
      const b = this.beams[i];
      b.t += dt;
      if (b.t >= b.life) this.beams.splice(i, 1);
    }
  }

  /** World-space draw (camera transform already applied). */
  draw(ctx, cam) {
    ctx.save();
    for (const r of this.rings) {
      const k = r.t / r.life;
      const rad = lerp(r.r0, r.r1, k * (2 - k));
      ctx.globalAlpha = 1 - k;
      ctx.strokeStyle = r.col;
      ctx.lineWidth = r.w * (1 - k * 0.6);
      ctx.beginPath();
      ctx.ellipse(r.x, r.y, rad, rad * r.squash, 0, 0, TAU);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    for (const p of this.a) {
      const k = p.t / p.life;
      const alpha = p.fade ? clamp(1 - k, 0, 1) : 1;
      const y = p.y - p.z;
      const rr = p.r2 != null ? lerp(p.r, p.r2, k) : p.r * (1 - k * 0.35);
      ctx.globalAlpha = alpha;
      if (p.glow) {
        ctx.globalCompositeOperation = 'lighter';
      }
      ctx.fillStyle = p.col2 ? (k < 0.5 ? p.col : p.col2) : p.col;
      if (p.shape === 'square') {
        ctx.save();
        ctx.translate(p.x, y);
        ctx.rotate(p.rot);
        ctx.fillRect(-rr, -rr, rr * 2, rr * 2);
        ctx.restore();
      } else if (p.shape === 'streak') {
        ctx.save();
        ctx.translate(p.x, y);
        ctx.rotate(Math.atan2(p.vy, p.vx));
        ctx.fillRect(-rr * 3, -rr * 0.4, rr * 6, rr * 0.8);
        ctx.restore();
      } else if (p.shape === 'spark') {
        ctx.strokeStyle = ctx.fillStyle;
        ctx.lineWidth = rr * 0.7;
        ctx.beginPath();
        ctx.moveTo(p.x, y);
        ctx.lineTo(p.x - p.vx * 0.03, y - p.vy * 0.03 + p.vz * 0.03);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.arc(p.x, y, Math.max(0.4, rr), 0, TAU);
        ctx.fill();
      }
      ctx.globalCompositeOperation = 'source-over';
    }
    ctx.globalAlpha = 1;

    for (const b of this.beams) {
      const k = b.t / b.life;
      ctx.globalAlpha = 1 - k;
      ctx.strokeStyle = b.col;
      ctx.lineWidth = b.w * (1 - k * 0.5);
      ctx.lineCap = 'round';
      ctx.globalCompositeOperation = 'lighter';
      ctx.beginPath();
      if (b.jag) {
        const n = 7;
        ctx.moveTo(b.x1, b.y1);
        for (let i = 1; i < n; i++) {
          const t = i / n;
          const nx = lerp(b.x1, b.x2, t) + (rng() - 0.5) * b.jag;
          const ny = lerp(b.y1, b.y2, t) + (rng() - 0.5) * b.jag;
          ctx.lineTo(nx, ny);
        }
        ctx.lineTo(b.x2, b.y2);
      } else {
        ctx.moveTo(b.x1, b.y1);
        ctx.lineTo(b.x2, b.y2);
      }
      ctx.stroke();
      ctx.globalCompositeOperation = 'source-over';
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  drawTexts(ctx) {
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const t of this.texts) {
      const k = t.t / t.life;
      const a = clamp(1 - (k - 0.6) / 0.4, 0, 1);
      const pop = k < 0.12 ? lerp(1.6, 1, k / 0.12) : 1;
      ctx.globalAlpha = a;
      ctx.font = `${t.weight} ${t.size * pop}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      ctx.lineWidth = 4;
      ctx.strokeStyle = 'rgba(0,0,0,0.65)';
      ctx.strokeText(t.str, t.x, t.y - t.z);
      ctx.fillStyle = t.col;
      ctx.fillText(t.str, t.x, t.y - t.z);
    }
    ctx.restore();
  }
}

// ————————————————————————————————————————————————— effect presets

export const FX = {
  hit(pt, x, y, dir, col = '#ffd76a') {
    pt.burst(x, y - 14, 8, { dir, spread: 1.5, speed: 130, life: 0.28, r: 2.6, col, glow: 1, g: 60, shape: 'spark' });
    pt.ring(x, y, { r0: 2, r1: 26, life: 0.22, col: 'rgba(255,255,255,0.5)', w: 2.4 });
  },
  blood(pt, x, y, dir, col = '#c8384a') {
    pt.burst(x, y - 14, 10, { dir, spread: 1.4, speed: 110, life: 0.5, r: 2.4, col, g: 220 });
  },
  slash(pt, x, y, dir, len = 40, col = 'rgba(255,255,255,0.85)') {
    pt.beams.push({ x1: x - Math.cos(dir) * 4, y1: y - Math.sin(dir) * 4 - 14, x2: x + Math.cos(dir) * len, y2: y + Math.sin(dir) * len - 14, t: 0, life: 0.14, col, w: 5 });
  },
  dust(pt, x, y, n = 6) {
    pt.burst(x, y, n, { speed: 34, life: 0.5, r: 4, r2: 9, col: 'rgba(210,200,180,0.55)', g: -10, vz: 6, z: 2, drag: 0.9 });
  },
  splash(pt, x, y) {
    pt.burst(x, y, 8, { speed: 50, life: 0.4, r: 2.4, col: 'rgba(180,225,245,0.8)', g: 260, vz: 60, z: 2 });
    pt.ring(x, y, { r0: 3, r1: 22, life: 0.35, col: 'rgba(200,235,255,0.6)', w: 2 });
  },
  levelUp(pt, x, y) {
    pt.ring(x, y, { r0: 8, r1: 90, life: 0.7, col: 'rgba(255,220,120,0.9)', w: 5 });
    pt.burst(x, y, 34, { speed: 70, life: 1.1, r: 3, col: '#ffd76a', col2: '#fff6c8', glow: 1, g: -40, vz: 40, z: 4 });
  },
  death(pt, x, y, col = '#8a7a6a') {
    pt.burst(x, y - 12, 22, { speed: 90, life: 0.7, r: 3.4, col, g: 140, vz: 50, z: 8 });
    pt.ring(x, y, { r0: 4, r1: 42, life: 0.4, col: 'rgba(255,255,255,0.4)', w: 3 });
  },
  heal(pt, x, y) {
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * TAU;
      pt.spawn({
        x: x + Math.cos(a) * 16, y: y + Math.sin(a) * 8, z: 0,
        vx: Math.cos(a) * 6, vy: Math.sin(a) * 3, vz: 40 + Math.random() * 30,
        g: -6, life: 0.9, r: 3, col: '#8fe8a8', glow: 1, drag: 0.97,
      });
    }
    pt.ring(x, y, { r0: 6, r1: 46, life: 0.5, col: 'rgba(140,240,170,0.8)', w: 3 });
  },
  cast(pt, x, y, col) {
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * TAU;
      pt.spawn({ x: x + Math.cos(a) * 22, y: y - 14 + Math.sin(a) * 11, vx: -Math.cos(a) * 40, vy: -Math.sin(a) * 20, life: 0.4, r: 2.6, col, glow: 1, g: 0, drag: 0.9 });
    }
  },
};
