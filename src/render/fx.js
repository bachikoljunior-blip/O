// パーティクル & エフェクト

import { DynamicVAO } from '../core/gl.js';
import { clamp01, lerp } from '../core/math.js';

const MAX = 2600;
const FLOATS = 12;   // posSize(4) color(4) extra(4)

const KIND = { SOFT: 0, SPARK: 1, SMOKE: 2, HARD: 3 };

class P {
  constructor() { this.life = 0; }
}

export class FX {
  constructor(gl) {
    this.gl = gl;
    this.pool = [];
    this.active = [];
    for (let i = 0; i < MAX; i++) this.pool.push(new P());
    const quad = new Float32Array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]);
    this.vao = new DynamicVAO(gl, quad, [
      { loc: 1, size: 4, offset: 0 },
      { loc: 2, size: 4, offset: 4 },
      { loc: 3, size: 4, offset: 8 },
    ], MAX, FLOATS);
    this.shockwaves = [];
  }

  spawn(o) {
    const p = this.pool.pop();
    if (!p) return null;
    p.x = o.x; p.y = o.y; p.z = o.z;
    p.vx = o.vx || 0; p.vy = o.vy || 0; p.vz = o.vz || 0;
    p.life = p.maxLife = o.life || 0.7;
    p.size = o.size || 0.2;
    p.sizeEnd = o.sizeEnd ?? p.size;
    p.r = o.r ?? 1; p.g = o.g ?? 1; p.b = o.b ?? 1;
    p.a = o.a ?? 1;
    p.kind = o.kind ?? KIND.SOFT;
    p.glow = o.glow || 0;
    p.gravity = o.gravity ?? 0;
    p.drag = o.drag ?? 0.6;
    p.rot = o.rot ?? Math.random() * 6.28;
    p.rotV = o.rotV ?? 0;
    p.stretch = o.stretch || 0;
    p.fade = o.fade ?? 1;
    p.ground = o.ground ?? false;
    this.active.push(p);
    return p;
  }

  update(dt, game) {
    const a = this.active;
    for (let i = a.length - 1; i >= 0; i--) {
      const p = a[i];
      p.life -= dt;
      if (p.life <= 0) {
        a[i] = a[a.length - 1];
        a.pop();
        this.pool.push(p);
        continue;
      }
      p.vy -= p.gravity * dt;
      const d = Math.exp(-p.drag * dt);
      p.vx *= d; p.vy *= d; p.vz *= d;
      p.x += p.vx * dt; p.y += p.vy * dt; p.z += p.vz * dt;
      p.rot += p.rotV * dt;
      if (p.ground && game) {
        const h = game.groundHeight(p.x, p.z);
        if (p.y < h) { p.y = h; p.vx *= 0.3; p.vz *= 0.3; p.vy = 0; p.gravity = 0; }
      }
    }
    for (let i = this.shockwaves.length - 1; i >= 0; i--) {
      const s = this.shockwaves[i];
      s.t += dt;
      if (s.t > s.dur) this.shockwaves.splice(i, 1);
    }
  }

  /** 描画用インスタンスを構築 */
  build(camX, camY, camZ) {
    const v = this.vao;
    v.reset();
    const d = v.data;
    for (const p of this.active) {
      const o = v.alloc();
      if (o < 0) break;
      const t = 1 - p.life / p.maxLife;
      const size = lerp(p.size, p.sizeEnd, t);
      d[o] = p.x; d[o + 1] = p.y; d[o + 2] = p.z; d[o + 3] = size;
      d[o + 4] = p.r; d[o + 5] = p.g; d[o + 6] = p.b;
      d[o + 7] = p.a * Math.pow(clamp01(p.life / p.maxLife), p.fade);
      d[o + 8] = p.rot; d[o + 9] = p.kind; d[o + 10] = p.stretch; d[o + 11] = p.glow;
    }
    void camX; void camY; void camZ;
    return v;
  }

  /* ------------------------------------------------------------- 効果 */
  hitSpark(target, dmg, blocked, opts) {
    const x = target.x, y = target.y + target.height * 0.6, z = target.z;
    if (blocked) return this.sparks(x, y, z, 10, [1, 0.95, 0.7]);
    const n = Math.min(26, 8 + dmg * 0.12);
    if (opts?.type === 'fire') return this.emberBurst(x, y, z, n);
    this.blood(x, y, z, n, target.tint);
    this.sparks(x, y, z, 6, [1, 0.9, 0.75]);
  }

  blood(x, y, z, n = 14, tint = null) {
    const r = tint ? lerp(0.55, tint[0], 0.25) : 0.55;
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.28, s = 1 + Math.random() * 4.5;
      this.spawn({
        x, y, z,
        vx: Math.cos(a) * s, vy: 1.5 + Math.random() * 4, vz: Math.sin(a) * s,
        life: 0.5 + Math.random() * 0.6, size: 0.06 + Math.random() * 0.1, sizeEnd: 0.02,
        r, g: 0.08, b: 0.09, a: 0.95, kind: KIND.HARD, gravity: 14, drag: 0.4, ground: true,
      });
    }
  }

  sparks(x, y, z, n = 10, color = [1, 0.9, 0.6]) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.28, s = 3 + Math.random() * 7;
      this.spawn({
        x, y, z,
        vx: Math.cos(a) * s, vy: 1 + Math.random() * 5, vz: Math.sin(a) * s,
        life: 0.22 + Math.random() * 0.3, size: 0.05, sizeEnd: 0.01,
        r: color[0], g: color[1], b: color[2], a: 1, kind: KIND.SPARK,
        glow: 1, gravity: 9, drag: 1.2, stretch: 1.4, rot: a,
      });
    }
  }

  dust(x, y, z, n = 5) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.28, s = 0.4 + Math.random() * 1.4;
      this.spawn({
        x: x + (Math.random() - 0.5) * 0.4, y, z: z + (Math.random() - 0.5) * 0.4,
        vx: Math.cos(a) * s, vy: 0.3 + Math.random() * 0.7, vz: Math.sin(a) * s,
        life: 0.5 + Math.random() * 0.5, size: 0.18, sizeEnd: 0.55,
        r: 0.62, g: 0.58, b: 0.5, a: 0.30, kind: KIND.SMOKE, drag: 1.4,
      });
    }
  }

  /** 武器の軌跡（前フレームの刃先から現在の刃先へ弧を描く） */
  weaponTrail(px, py, pz, x, y, z, bx, by, bz, color, power = 1) {
    const seg = 4;
    for (let i = 1; i <= seg; i++) {
      const t = i / seg;
      const tx = px + (x - px) * t, ty = py + (y - py) * t, tz = pz + (z - pz) * t;
      // 刃先から柄元へ向かって細くなる帯
      for (let u = 0; u < 3; u++) {
        const k = u / 3;
        const wx = tx + (bx - tx) * k, wy = ty + (by - ty) * k, wz = tz + (bz - tz) * k;
        this.spawn({
          x: wx, y: wy, z: wz,
          life: 0.10 + 0.06 * (1 - k),
          size: (0.26 - k * 0.10) * power, sizeEnd: 0.02,
          r: color[0], g: color[1], b: color[2], a: 0.55 * (1 - k * 0.45),
          kind: KIND.SOFT, glow: 1.1, drag: 4, fade: 0.7,
        });
      }
    }
  }

  slash(x, y, z, yaw) {
    for (let i = 0; i < 8; i++) {
      const t = i / 8;
      this.spawn({
        x: x + Math.sin(yaw + t) * 0.2, y: y + (t - 0.5) * 0.4, z: z + Math.cos(yaw + t) * 0.2,
        vx: 0, vy: 0.4, vz: 0,
        life: 0.16, size: 0.22, sizeEnd: 0.05,
        r: 1, g: 1, b: 0.95, a: 0.7, kind: KIND.SOFT, glow: 0.6, drag: 3,
      });
    }
  }

  impact(x, y, z) {
    this.sparks(x, y, z, 8, [1, 0.85, 0.6]);
    this.dust(x, y, z, 3);
  }

  heal(x, y, z) {
    for (let i = 0; i < 22; i++) {
      const a = Math.random() * 6.28, r = 0.3 + Math.random() * 0.5;
      this.spawn({
        x: x + Math.cos(a) * r, y: y - 0.6 + Math.random() * 0.4, z: z + Math.sin(a) * r,
        vx: Math.cos(a) * 0.3, vy: 1.4 + Math.random() * 1.4, vz: Math.sin(a) * 0.3,
        life: 0.8 + Math.random() * 0.5, size: 0.09, sizeEnd: 0.02,
        r: 1, g: 0.92, b: 0.55, a: 0.9, kind: KIND.SOFT, glow: 1.4, drag: 0.5,
      });
    }
  }

  emberBurst(x, y, z, n = 12) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.28, s = 1 + Math.random() * 3;
      this.spawn({
        x, y, z,
        vx: Math.cos(a) * s, vy: 1.5 + Math.random() * 3, vz: Math.sin(a) * s,
        life: 0.5 + Math.random() * 0.6, size: 0.09, sizeEnd: 0.02,
        r: 1, g: 0.45 + Math.random() * 0.3, b: 0.12, a: 1, kind: KIND.SOFT,
        glow: 1.6, gravity: -1.5, drag: 1.0,
      });
    }
  }

  fireJet(x, y, z) {
    this.spawn({
      x, y, z,
      vx: (Math.random() - 0.5) * 1.5, vy: 0.8 + Math.random(), vz: (Math.random() - 0.5) * 1.5,
      life: 0.35 + Math.random() * 0.3, size: 0.5, sizeEnd: 1.3,
      r: 1, g: 0.42 + Math.random() * 0.3, b: 0.10, a: 0.55, kind: KIND.SMOKE,
      glow: 1.2, drag: 1.2,
    });
  }

  deathBurst(x, y, z, tint) {
    for (let i = 0; i < 30; i++) {
      const a = Math.random() * 6.28, s = 1 + Math.random() * 3.5;
      this.spawn({
        x, y, z,
        vx: Math.cos(a) * s, vy: 1 + Math.random() * 4, vz: Math.sin(a) * s,
        life: 0.9 + Math.random() * 0.8, size: 0.1, sizeEnd: 0.02,
        r: lerp(tint[0], 1, 0.5), g: lerp(tint[1], 0.9, 0.5), b: lerp(tint[2], 0.6, 0.5),
        a: 0.9, kind: KIND.SOFT, glow: 1.2, gravity: -0.8, drag: 0.7,
      });
    }
  }

  voidBurst(x, y, z) {
    for (let i = 0; i < 24; i++) {
      const a = Math.random() * 6.28, s = 2 + Math.random() * 5;
      this.spawn({
        x, y, z,
        vx: Math.cos(a) * s, vy: (Math.random() - 0.3) * 4, vz: Math.sin(a) * s,
        life: 0.4 + Math.random() * 0.4, size: 0.24, sizeEnd: 0.05,
        r: 0.35, g: 0.15, b: 0.55, a: 0.9, kind: KIND.SMOKE, glow: 1.0, drag: 1.6,
      });
    }
  }

  bossPhase(x, y, z, color) {
    for (let i = 0; i < 90; i++) {
      const a = Math.random() * 6.28, r = Math.random() * 2;
      const s = 4 + Math.random() * 9;
      this.spawn({
        x: x + Math.cos(a) * r, y: y + Math.random() * 2, z: z + Math.sin(a) * r,
        vx: Math.cos(a) * s, vy: 2 + Math.random() * 6, vz: Math.sin(a) * s,
        life: 0.8 + Math.random() * 0.9, size: 0.2, sizeEnd: 0.03,
        r: color[0], g: color[1], b: color[2], a: 1, kind: KIND.SOFT,
        glow: 1.8, drag: 1.0, gravity: 2,
      });
    }
    this.shockwave(x, y, z, 8);
  }

  shockwave(x, y, z, radius) {
    this.shockwaves.push({ x, y, z, r: radius, t: 0, dur: 0.45 });
    for (let i = 0; i < 34; i++) {
      const a = (i / 34) * 6.28;
      this.spawn({
        x, y: y + 0.15, z,
        vx: Math.cos(a) * radius * 2.6, vy: 1.5 + Math.random(), vz: Math.sin(a) * radius * 2.6,
        life: 0.42, size: 0.3, sizeEnd: 0.9,
        r: 0.7, g: 0.62, b: 0.5, a: 0.5, kind: KIND.SMOKE, drag: 2.6,
      });
    }
  }

  /* ------------------------------------------------------- 環境演出 */
  rain(camX, camY, camZ, dt, intensity, world) {
    const n = Math.floor(intensity * 90 * dt * 60 / 60);
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.28, r = Math.random() * 26;
      const x = camX + Math.cos(a) * r, z = camZ + Math.sin(a) * r;
      this.spawn({
        x, y: camY + 12 + Math.random() * 6, z,
        vx: 1.2, vy: -26, vz: 0.4,
        life: 0.75, size: 0.035, sizeEnd: 0.035,
        r: 0.62, g: 0.72, b: 0.85, a: 0.5, kind: KIND.SPARK, stretch: 5.5, rot: 0,
        drag: 0, ground: true,
      });
    }
    void world;
  }

  snow(camX, camY, camZ, dt, intensity) {
    const n = Math.floor(intensity * 40 * dt * 60 / 60);
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.28, r = Math.random() * 24;
      this.spawn({
        x: camX + Math.cos(a) * r, y: camY + 10 + Math.random() * 6, z: camZ + Math.sin(a) * r,
        vx: (Math.random() - 0.5) * 1.4, vy: -1.4 - Math.random(), vz: (Math.random() - 0.5) * 1.4,
        life: 5, size: 0.06, sizeEnd: 0.05,
        r: 1, g: 1, b: 1, a: 0.75, kind: KIND.HARD, drag: 0.1,
      });
    }
  }

  ambient(camX, camY, camZ, dt, kind, intensity) {
    if (Math.random() > intensity * dt * 8) return;
    const a = Math.random() * 6.28, r = 4 + Math.random() * 18;
    const x = camX + Math.cos(a) * r, z = camZ + Math.sin(a) * r;
    if (kind === 'firefly') {
      this.spawn({
        x, y: camY - 1 + Math.random() * 2.5, z,
        vx: (Math.random() - 0.5) * 0.4, vy: (Math.random() - 0.5) * 0.3, vz: (Math.random() - 0.5) * 0.4,
        life: 3 + Math.random() * 3, size: 0.055, sizeEnd: 0.03,
        r: 0.85, g: 1, b: 0.4, a: 0.9, kind: KIND.SOFT, glow: 2.0, drag: 0.4, fade: 0.5,
      });
    } else if (kind === 'ash') {
      this.spawn({
        x, y: camY + 3 + Math.random() * 6, z,
        vx: (Math.random() - 0.3) * 1.2, vy: -0.5 - Math.random() * 0.6, vz: (Math.random() - 0.5) * 1.2,
        life: 6, size: 0.05, sizeEnd: 0.04,
        r: 1, g: 0.5, b: 0.2, a: 0.7, kind: KIND.SOFT, glow: 1.2, drag: 0.2,
      });
    } else if (kind === 'leaf') {
      this.spawn({
        x, y: camY + 2 + Math.random() * 6, z,
        vx: (Math.random() - 0.2) * 1.6, vy: -0.8 - Math.random() * 0.5, vz: (Math.random() - 0.5) * 1.6,
        life: 6, size: 0.09, sizeEnd: 0.08,
        r: 0.5, g: 0.55, b: 0.2, a: 0.7, kind: KIND.HARD, drag: 0.3, rotV: 2,
      });
    } else if (kind === 'spore') {
      this.spawn({
        x, y: camY - 1 + Math.random() * 3, z,
        vx: (Math.random() - 0.5) * 0.3, vy: 0.15 + Math.random() * 0.2, vz: (Math.random() - 0.5) * 0.3,
        life: 7, size: 0.05, sizeEnd: 0.03,
        r: 0.7, g: 0.85, b: 0.75, a: 0.5, kind: KIND.SOFT, glow: 0.5, drag: 0.2,
      });
    }
  }

  campfireEmbers(x, y, z, dt) {
    if (Math.random() > dt * 22) return;
    this.spawn({
      x: x + (Math.random() - 0.5) * 0.4, y: y + 0.3, z: z + (Math.random() - 0.5) * 0.4,
      vx: (Math.random() - 0.5) * 0.5, vy: 1.2 + Math.random() * 1.4, vz: (Math.random() - 0.5) * 0.5,
      life: 1.1 + Math.random(), size: 0.06, sizeEnd: 0.01,
      r: 1, g: 0.55, b: 0.15, a: 1, kind: KIND.SOFT, glow: 1.8, gravity: -0.6, drag: 0.6,
    });
  }

  shrineGlow(x, y, z, dt) {
    if (Math.random() > dt * 14) return;
    const a = Math.random() * 6.28, r = Math.random() * 1.2;
    this.spawn({
      x: x + Math.cos(a) * r, y: y + 0.2, z: z + Math.sin(a) * r,
      vx: 0, vy: 0.7 + Math.random() * 0.8, vz: 0,
      life: 1.8, size: 0.07, sizeEnd: 0.01,
      r: 1, g: 0.85, b: 0.4, a: 0.9, kind: KIND.SOFT, glow: 2.2, drag: 0.5,
    });
  }
}
