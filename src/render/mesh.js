// 手続き的メッシュ生成（プリミティブ + モデルライブラリ）

import { Geometry, VERT_FLOATS } from '../core/gl.js';
import { m4, quat, v3, lerp } from '../core/math.js';
import { makeRng } from '../core/noise.js';

const _q = quat.new();
const _t = v3.new();
const _s = v3.new(1, 1, 1);

export class MeshBuilder {
  constructor() {
    this.v = [];
    this.i = [];
    this.stack = [m4.identity(m4.new())];
  }
  get m() { return this.stack[this.stack.length - 1]; }

  push() {
    const n = m4.copy(m4.new(), this.m);
    this.stack.push(n);
    return this;
  }
  pop() { this.stack.pop(); return this; }
  reset() { this.stack.length = 1; m4.identity(this.stack[0]); return this; }

  apply(q, t, s) {
    const m = m4.fromRTS(m4.new(), q, t, s);
    m4.mul(this.m, this.m, m);
    return this;
  }
  translate(x, y, z) {
    return this.apply(quat.identity(_q), v3.set(_t, x, y, z), v3.set(_s, 1, 1, 1));
  }
  rotate(rx, ry, rz) {
    return this.apply(quat.fromEuler(_q, rx, ry, rz), v3.set(_t, 0, 0, 0), v3.set(_s, 1, 1, 1));
  }
  scale(sx, sy = sx, sz = sx) {
    return this.apply(quat.identity(_q), v3.set(_t, 0, 0, 0), v3.set(_s, sx, sy, sz));
  }

  vert(x, y, z, nx, ny, nz, c) {
    const m = this.m;
    const px = m[0] * x + m[4] * y + m[8] * z + m[12];
    const py = m[1] * x + m[5] * y + m[9] * z + m[13];
    const pz = m[2] * x + m[6] * y + m[10] * z + m[14];
    // 法線は線形部分で変換（非一様スケールは近似）
    let ax = m[0] * nx + m[4] * ny + m[8] * nz;
    let ay = m[1] * nx + m[5] * ny + m[9] * nz;
    let az = m[2] * nx + m[6] * ny + m[10] * nz;
    const l = Math.hypot(ax, ay, az) || 1;
    this.v.push(px, py, pz, ax / l, ay / l, az / l, c[0], c[1], c[2]);
    return this.v.length / VERT_FLOATS - 1;
  }
  tri(a, b, c) { this.i.push(a, b, c); return this; }
  quad(a, b, c, d) { this.i.push(a, b, c, a, c, d); return this; }

  /* ------------------------------------------------------- プリミティブ */
  /** 中心 (0,0,0)、サイズ sx,sy,sz の直方体（フラットシェーディング）*/
  box(sx, sy, sz, c, shadeVariation = 0.06) {
    const hx = sx / 2, hy = sy / 2, hz = sz / 2;
    const faces = [
      [[1, 0, 0], [[hx, -hy, -hz], [hx, -hy, hz], [hx, hy, hz], [hx, hy, -hz]]],
      [[-1, 0, 0], [[-hx, -hy, hz], [-hx, -hy, -hz], [-hx, hy, -hz], [-hx, hy, hz]]],
      [[0, 1, 0], [[-hx, hy, -hz], [hx, hy, -hz], [hx, hy, hz], [-hx, hy, hz]]],
      [[0, -1, 0], [[-hx, -hy, hz], [hx, -hy, hz], [hx, -hy, -hz], [-hx, -hy, -hz]]],
      [[0, 0, 1], [[-hx, -hy, hz], [-hx, hy, hz], [hx, hy, hz], [hx, -hy, hz]]],
      [[0, 0, -1], [[hx, -hy, -hz], [hx, hy, -hz], [-hx, hy, -hz], [-hx, -hy, -hz]]],
    ];
    let fi = 0;
    for (const [n, ps] of faces) {
      const k = 1 + (fi % 3 - 1) * shadeVariation;
      const cc = [c[0] * k, c[1] * k, c[2] * k];
      const idx = ps.map((p) => this.vert(p[0], p[1], p[2], n[0], n[1], n[2], cc));
      this.quad(idx[0], idx[1], idx[2], idx[3]);
      fi++;
    }
    return this;
  }

  /** 角丸ボックス（キャラクターのパーツ用） */
  roundedBox(sx, sy, sz, radius, c, n = 3) {
    const hx = sx / 2 - radius, hy = sy / 2 - radius, hz = sz / 2 - radius;
    const ax = Math.max(0, hx), ay = Math.max(0, hy), az = Math.max(0, hz);
    const grid = [];
    // 立方体表面をパラメトリックに走査して Minkowski 和（箱⊕球）を作る
    const faceDirs = [
      [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
      [[-1, 0, 0], [0, 1, 0], [0, 0, -1]],
      [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
      [[0, -1, 0], [0, 0, -1], [1, 0, 0]],
      [[0, 0, 1], [1, 0, 0], [0, 1, 0]],
      [[0, 0, -1], [-1, 0, 0], [0, 1, 0]],
    ];
    for (const [nd, u, w] of faceDirs) {
      const base = [];
      for (let j = 0; j <= n; j++) {
        for (let i = 0; i <= n; i++) {
          const su = (i / n) * 2 - 1, sw = (j / n) * 2 - 1;
          const px = nd[0] + u[0] * su + w[0] * sw;
          const py = nd[1] + u[1] * su + w[1] * sw;
          const pz = nd[2] + u[2] * su + w[2] * sw;
          const cx = Math.max(-1, Math.min(1, px)) * ax;
          const cy = Math.max(-1, Math.min(1, py)) * ay;
          const cz = Math.max(-1, Math.min(1, pz)) * az;
          let dx = px * (ax + radius) - cx;
          let dy = py * (ay + radius) - cy;
          let dz = pz * (az + radius) - cz;
          const l = Math.hypot(dx, dy, dz) || 1;
          dx /= l; dy /= l; dz /= l;
          base.push(this.vert(cx + dx * radius, cy + dy * radius, cz + dz * radius, dx, dy, dz, c));
        }
      }
      grid.push(base);
    }
    for (const base of grid) {
      for (let j = 0; j < n; j++) {
        for (let i = 0; i < n; i++) {
          const a = base[j * (n + 1) + i], b = base[j * (n + 1) + i + 1];
          const d = base[(j + 1) * (n + 1) + i], e = base[(j + 1) * (n + 1) + i + 1];
          this.quad(a, b, e, d);
        }
      }
    }
    return this;
  }

  /** Y軸に沿った円柱・円錐台。底面 y=0, 上面 y=h */
  cylinder(r0, r1, h, seg, c, caps = true, taperColor = null) {
    const ring0 = [], ring1 = [];
    for (let i = 0; i < seg; i++) {
      const a = (i / seg) * Math.PI * 2;
      const cx = Math.cos(a), sz = Math.sin(a);
      const slope = (r0 - r1) / (h || 1);
      const nl = Math.hypot(1, slope) || 1;
      const nx = cx / nl, ny = slope / nl, nz = sz / nl;
      ring0.push(this.vert(cx * r0, 0, sz * r0, nx, ny, nz, c));
      ring1.push(this.vert(cx * r1, h, sz * r1, nx, ny, nz, taperColor || c));
    }
    for (let i = 0; i < seg; i++) {
      const j = (i + 1) % seg;
      this.quad(ring0[i], ring0[j], ring1[j], ring1[i]);
    }
    if (caps) {
      if (r1 > 0.001) {
        const top = [];
        for (let i = 0; i < seg; i++) {
          const a = (i / seg) * Math.PI * 2;
          top.push(this.vert(Math.cos(a) * r1, h, Math.sin(a) * r1, 0, 1, 0, taperColor || c));
        }
        for (let i = 1; i < seg - 1; i++) this.tri(top[0], top[i], top[i + 1]);
      }
      if (r0 > 0.001) {
        const bot = [];
        for (let i = 0; i < seg; i++) {
          const a = (i / seg) * Math.PI * 2;
          bot.push(this.vert(Math.cos(a) * r0, 0, Math.sin(a) * r0, 0, -1, 0, c));
        }
        for (let i = 1; i < seg - 1; i++) this.tri(bot[0], bot[i + 1], bot[i]);
      }
    }
    return this;
  }

  /** UV 球（中心 0）。displace(x,y,z,i) で凹凸を付けられる */
  sphere(r, segU, segV, c, displace = null) {
    const rows = [];
    for (let j = 0; j <= segV; j++) {
      const row = [];
      const phi = (j / segV) * Math.PI;
      for (let i = 0; i <= segU; i++) {
        const th = (i / segU) * Math.PI * 2;
        const nx = Math.sin(phi) * Math.cos(th);
        const ny = Math.cos(phi);
        const nz = Math.sin(phi) * Math.sin(th);
        let rr = r;
        if (displace) rr = r * displace(nx, ny, nz, j * (segU + 1) + i);
        row.push(this.vert(nx * rr, ny * rr, nz * rr, nx, ny, nz, c));
      }
      rows.push(row);
    }
    for (let j = 0; j < segV; j++) {
      for (let i = 0; i < segU; i++) {
        this.quad(rows[j][i], rows[j][i + 1], rows[j + 1][i + 1], rows[j + 1][i]);
      }
    }
    return this;
  }

  /** XY平面の板（両面）*/
  plane(w, h, c, cTop = null) {
    const hw = w / 2;
    const c0 = c, c1 = cTop || c;
    const a = this.vert(-hw, 0, 0, 0, 0, 1, c0);
    const b = this.vert(hw, 0, 0, 0, 0, 1, c0);
    const d = this.vert(hw, h, 0, 0, 0, 1, c1);
    const e = this.vert(-hw, h, 0, 0, 0, 1, c1);
    this.quad(a, b, d, e);
    const a2 = this.vert(-hw, 0, 0, 0, 0, -1, c0);
    const b2 = this.vert(hw, 0, 0, 0, 0, -1, c0);
    const d2 = this.vert(hw, h, 0, 0, 0, -1, c1);
    const e2 = this.vert(-hw, h, 0, 0, 0, -1, c1);
    this.quad(a2, e2, d2, b2);
    return this;
  }

  /** 先細りの葉（草・羽根）。根元 w、先端はほぼ点 */
  blade(w, h, c, cTop = null, curve = 0.12) {
    const c1 = cTop || c;
    const seg = 3;
    let prevL = -1, prevR = -1;
    for (let i = 0; i <= seg; i++) {
      const t = i / seg;
      const hw = (w / 2) * (1 - t * 0.92);
      const y = h * t;
      const z = curve * t * t * h;
      const col = [lerp(c[0], c1[0], t), lerp(c[1], c1[1], t), lerp(c[2], c1[2], t)];
      const l = this.vert(-hw, y, z, 0, 0.25, 0.97, col);
      const r = this.vert(hw, y, z, 0, 0.25, 0.97, col);
      if (prevL >= 0) this.quad(prevL, prevR, r, l);
      prevL = l; prevR = r;
    }
    // 裏面
    prevL = -1; prevR = -1;
    for (let i = 0; i <= seg; i++) {
      const t = i / seg;
      const hw = (w / 2) * (1 - t * 0.92);
      const y = h * t;
      const z = curve * t * t * h;
      const col = [lerp(c[0], c1[0], t) * 0.82, lerp(c[1], c1[1], t) * 0.82, lerp(c[2], c1[2], t) * 0.82];
      const l = this.vert(-hw, y, z, 0, 0.25, -0.97, col);
      const r = this.vert(hw, y, z, 0, 0.25, -0.97, col);
      if (prevL >= 0) this.quad(prevL, l, r, prevR);
      prevL = l; prevR = r;
    }
    return this;
  }

  /** 三角柱（切妻屋根） */
  prism(w, h, d, c) {
    const hw = w / 2, hd = d / 2;
    const n = [0, 0.7071, 0.7071];
    const a = this.vert(-hw, 0, hd, 0, 0.4, 0.9, c), b = this.vert(hw, 0, hd, 0, 0.4, 0.9, c);
    const t1 = this.vert(hw, h, 0, 0, 0.4, 0.9, c), t0 = this.vert(-hw, h, 0, 0, 0.4, 0.9, c);
    this.quad(a, b, t1, t0);
    const e = this.vert(hw, 0, -hd, 0, 0.4, -0.9, c), f = this.vert(-hw, 0, -hd, 0, 0.4, -0.9, c);
    const t3 = this.vert(-hw, h, 0, 0, 0.4, -0.9, c), t2 = this.vert(hw, h, 0, 0, 0.4, -0.9, c);
    this.quad(e, f, t3, t2);
    const g0 = this.vert(-hw, 0, hd, -1, 0, 0, c), g1 = this.vert(-hw, 0, -hd, -1, 0, 0, c);
    const g2 = this.vert(-hw, h, 0, -1, 0, 0, c);
    this.tri(g0, g2, g1);
    const h0 = this.vert(hw, 0, hd, 1, 0, 0, c), h1 = this.vert(hw, 0, -hd, 1, 0, 0, c);
    const h2 = this.vert(hw, h, 0, 1, 0, 0, c);
    this.tri(h0, h1, h2);
    void n;
    return this;
  }

  build(gl) {
    return new Geometry(gl, new Float32Array(this.v), this.i);
  }
}

/* --------------------------------------------------------- カラーパレット */
const C = {
  bark: [0.28, 0.21, 0.15], barkDark: [0.20, 0.15, 0.11], birch: [0.84, 0.83, 0.78],
  leafA: [0.20, 0.42, 0.18], leafB: [0.26, 0.50, 0.20], leafPine: [0.13, 0.30, 0.19],
  leafDry: [0.48, 0.42, 0.18], leafWillow: [0.30, 0.44, 0.22],
  rock: [0.54, 0.52, 0.48], rockDark: [0.40, 0.39, 0.37],
  wood: [0.42, 0.30, 0.19], woodDark: [0.30, 0.21, 0.13],
  plaster: [0.72, 0.68, 0.58], thatch: [0.55, 0.44, 0.22], roof: [0.36, 0.22, 0.18],
  stone: [0.56, 0.55, 0.52], stoneDark: [0.40, 0.39, 0.37],
  metal: [0.62, 0.64, 0.70], metalDark: [0.34, 0.36, 0.42], gold: [0.85, 0.68, 0.28],
  cloth: [0.60, 0.20, 0.18], white: [1, 1, 1], dark: [0.15, 0.15, 0.17],
  grassLow: [0.16, 0.30, 0.12], grassTop: [0.46, 0.62, 0.24],
  ember: [1.0, 0.55, 0.18],
};

/* --------------------------------------------------------------- 樹木 */
function treePine(b, rng) {
  const h = 6 + rng() * 4.5;
  b.cylinder(0.34, 0.16, h * 0.55, 6, C.barkDark, true, C.bark);
  const tiers = 4 + (rng() * 2 | 0);
  for (let i = 0; i < tiers; i++) {
    const t = i / tiers;
    const y = h * (0.22 + t * 0.62);
    const r = (2.0 - t * 1.35) * (0.9 + rng() * 0.25);
    b.push().translate(0, y, 0);
    const k = 0.85 + t * 0.35;
    b.cylinder(r, r * 0.15, h * 0.30, 7, [C.leafPine[0] * k, C.leafPine[1] * k, C.leafPine[2] * k], false);
    b.pop();
  }
}
/** 枝分かれする幹を再帰的に生成する */
function branch(b, rng, len, r0, depth, leaf, leafSize) {
  const r1 = r0 * (0.62 + rng() * 0.12);
  b.cylinder(r0, r1, len, depth > 1 ? 6 : 4, C.barkDark, true, C.bark);
  if (depth <= 0) {
    if (leaf) {
      b.push().translate(0, len, 0).scale(1, 0.72 + rng() * 0.3, 1);
      const k = 0.82 + rng() * 0.36;
      b.sphere(leafSize * (0.8 + rng() * 0.5), 7, 5, [leaf[0] * k, leaf[1] * k, leaf[2] * k]);
      b.pop();
    }
    return;
  }
  const n = 2 + ((rng() * 2) | 0);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + rng() * 0.9;
    b.push()
      .translate(0, len * (0.72 + rng() * 0.26), 0)
      .rotate(0, a, 0)
      .rotate(0.42 + rng() * 0.46, 0, 0);
    branch(b, rng, len * (0.62 + rng() * 0.18), r1 * 0.78, depth - 1, leaf, leafSize * 0.82);
    b.pop();
  }
}

function treeOak(b, rng) {
  const h = 3.0 + rng() * 1.6;
  b.push().rotate((rng() - 0.5) * 0.08, rng() * 6.28, (rng() - 0.5) * 0.08);
  branch(b, rng, h, 0.40 + rng() * 0.12, 2, C.leafA, 1.9);
  b.pop();
}

function treeBirch(b, rng) {
  const h = 4.4 + rng() * 2.2;
  b.push().rotate((rng() - 0.5) * 0.1, 0, (rng() - 0.5) * 0.1);
  b.cylinder(0.19, 0.11, h, 6, C.birch, true, [0.92, 0.92, 0.88]);
  // 樹皮の黒い横縞
  for (let i = 0; i < 4; i++) {
    const y = h * (0.15 + rng() * 0.7);
    b.push().translate(0, y, 0)
      .cylinder(0.20 - y / h * 0.07, 0.20 - y / h * 0.07, 0.05 + rng() * 0.05, 6, [0.22, 0.21, 0.20], false).pop();
  }
  for (let i = 0; i < 3; i++) {
    const a = rng() * Math.PI * 2;
    b.push().translate(0, h * (0.55 + rng() * 0.3), 0).rotate(0, a, 0).rotate(0.7 + rng() * 0.4, 0, 0);
    branch(b, rng, 1.0 + rng() * 0.7, 0.07, 1, C.leafB, 1.35);
    b.pop();
  }
  b.push().translate(0, h * 0.96, 0).scale(1, 0.7, 1);
  b.sphere(1.3 + rng() * 0.5, 7, 5, C.leafB);
  b.pop();
  b.pop();
}
function treeWillow(b, rng) {
  const h = 4.4 + rng() * 2.4;
  b.cylinder(0.5, 0.3, h * 0.5, 6, C.barkDark, true, C.bark);
  b.push().translate(0, h * 0.55, 0).scale(1, 0.6, 1);
  b.sphere(2.6, 8, 5, C.leafWillow);
  b.pop();
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 + rng() * 0.4;
    const rr = 1.6 + rng() * 0.8;
    b.push().translate(Math.cos(a) * rr, h * 0.55, Math.sin(a) * rr);
    b.cylinder(0.12, 0.02, -(1.6 + rng() * 1.4), 4, C.leafWillow, false);
    b.pop();
  }
}
function treeDead(b, rng) {
  const h = 3.4 + rng() * 2.4;
  b.push().rotate((rng() - 0.5) * 0.12, rng() * 6.28, (rng() - 0.5) * 0.12);
  branch(b, rng, h, 0.34 + rng() * 0.10, 2, null, 0);
  b.pop();
}

const TREE_FN = { pine: treePine, oak: treeOak, birch: treeBirch, willow: treeWillow, dead: treeDead };

/* --------------------------------------------------------------- 岩・草 */
function rockMesh(b, rng, size) {
  const seedA = rng() * 100, seedB = rng() * 100;
  b.push().scale(1, 0.72 + rng() * 0.4, 1);
  b.sphere(size, 7, 5, C.rock, (x, y, z) =>
    0.78 + 0.34 * Math.abs(Math.sin(x * 3.1 + seedA) * Math.cos(z * 2.7 + seedB) * Math.cos(y * 2.2)));
  b.pop();
}

function grassTuft(b, rng) {
  // 細い草の葉を数枚。頂点の高さに応じて風になびく
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2 + rng() * 0.7;
    const hh = 0.30 + rng() * 0.34;
    const k = 0.8 + rng() * 0.45;
    const off = 0.04 + rng() * 0.10;
    b.push()
      .rotate(0, a, 0)
      .translate(off, 0, 0)
      .rotate((rng() - 0.5) * 0.3, 0, (rng() - 0.5) * 0.4);
    b.blade(0.11 + rng() * 0.05, hh,
      [C.grassLow[0] * k, C.grassLow[1] * k, C.grassLow[2] * k],
      [C.grassTop[0] * k, C.grassTop[1] * k, C.grassTop[2] * k],
      0.10 + rng() * 0.22);
    b.pop();
  }
}

function bushMesh(b, rng) {
  for (let i = 0; i < 3; i++) {
    const a = rng() * Math.PI * 2;
    b.push().translate(Math.cos(a) * 0.35, 0.35 + rng() * 0.2, Math.sin(a) * 0.35).scale(1, 0.8, 1);
    const k = 0.8 + rng() * 0.35;
    b.sphere(0.55 + rng() * 0.3, 6, 4, [C.leafA[0] * k, C.leafA[1] * k, C.leafA[2] * k]);
    b.pop();
  }
}

/* --------------------------------------------------------------- 建築物 */
function house(b, rng, w = 6, d = 5, wallH = 3.2) {
  b.push().translate(0, wallH / 2, 0);
  b.box(w, wallH, d, C.plaster);
  b.pop();
  // 梁
  for (const sx of [-1, 1]) {
    b.push().translate(sx * (w / 2 - 0.1), wallH / 2, 0).box(0.22, wallH, 0.3, C.woodDark).pop();
  }
  b.push().translate(0, wallH + 0.05, 0);
  b.prism(w + 0.9, 2.2 + rng() * 0.6, d + 0.9, C.roof);
  b.pop();
  // ドア
  b.push().translate(0, 1.05, d / 2 + 0.02).box(1.1, 2.1, 0.14, C.woodDark).pop();
  // 窓
  for (const sx of [-1, 1]) {
    b.push().translate(sx * w * 0.28, 2.1, d / 2 + 0.02).box(0.8, 0.8, 0.12, [0.16, 0.14, 0.12]).pop();
  }
}

function tower(b) {
  b.cylinder(2.4, 2.0, 9, 10, C.stoneDark, true, C.stone);
  b.push().translate(0, 9, 0).cylinder(2.6, 2.6, 0.6, 10, C.stone).pop();
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    b.push().translate(Math.cos(a) * 2.3, 9.6, Math.sin(a) * 2.3).rotate(0, -a, 0)
      .box(0.7, 0.9, 0.5, C.stone).pop();
  }
}

function ruinArch(b) {
  for (const sx of [-1, 1]) {
    b.push().translate(sx * 1.8, 2.0, 0).box(0.9, 4.0, 0.9, C.stoneDark).pop();
  }
  b.push().translate(0, 4.3, 0).box(4.8, 0.7, 1.0, C.stone).pop();
}

function pillar(b, rng) {
  const h = 2.5 + rng() * 3;
  b.cylinder(0.55, 0.45, h, 8, C.stoneDark, true, C.stone);
  b.push().translate(0, h, 0).box(1.4, 0.35, 1.4, C.stone).pop();
}

function tent(b) {
  b.push().translate(0, 0, 0).prism(3.4, 2.4, 4.0, [0.42, 0.34, 0.24]).pop();
  b.push().translate(0, 1.2, -2.1).box(0.14, 2.4, 0.14, C.woodDark).pop();
}

function campfire(b) {
  b.push().translate(0, 0.08, 0).cylinder(0.9, 0.85, 0.16, 10, C.rockDark).pop();
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2;
    b.push().translate(Math.cos(a) * 0.75, 0.12, Math.sin(a) * 0.75).rotate(0, -a, 0)
      .box(0.34, 0.24, 0.24, C.rock).pop();
  }
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + 0.4;
    b.push().translate(Math.cos(a) * 0.22, 0.35, Math.sin(a) * 0.22)
      .rotate(Math.cos(a) * 0.7, 0, Math.sin(a) * 0.7)
      .cylinder(0.09, 0.06, 0.9, 4, C.woodDark).pop();
  }
  b.push().translate(0, 0.22, 0).sphere(0.32, 6, 4, C.ember).pop();
}

/** 篝火（チェックポイント）：黄金の剣が地面に刺さる */
function shrine(b) {
  b.push().translate(0, 0.12, 0).cylinder(1.5, 1.35, 0.24, 12, C.stoneDark, true, C.stone).pop();
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2;
    b.push().translate(Math.cos(a) * 1.25, 0.3, Math.sin(a) * 1.25).rotate(0, -a, 0)
      .box(0.4, 0.36, 0.3, C.stone).pop();
  }
  b.push().translate(0, 0.3, 0);
  b.cylinder(0.09, 0.06, 1.5, 5, C.metalDark);     // 柄
  b.push().translate(0, 1.5, 0).box(0.55, 0.1, 0.14, C.gold).pop(); // 鍔
  b.push().translate(0, 1.6, 0).box(0.16, 1.6, 0.05, C.gold).pop(); // 刃
  b.pop();
}

function chest(b) {
  b.push().translate(0, 0.35, 0).box(1.2, 0.7, 0.8, C.wood).pop();
  b.push().translate(0, 0.75, 0).scale(1, 0.55, 1).cylinder(0.42, 0.42, 0, 8, C.woodDark).pop();
  b.push().translate(0, 0.78, 0).rotate(Math.PI / 2, 0, 0).cylinder(0.42, 0.42, 1.2, 8, C.woodDark).pop();
  b.push().translate(0, 0.5, 0.42).box(0.24, 0.3, 0.08, C.gold).pop();
}

function fence(b) {
  b.push().translate(0, 0.6, 0).box(0.16, 1.2, 0.16, C.woodDark).pop();
  b.push().translate(1.0, 0.85, 0).box(2.0, 0.12, 0.1, C.wood).pop();
  b.push().translate(1.0, 0.45, 0).box(2.0, 0.12, 0.1, C.wood).pop();
}

function signpost(b) {
  b.cylinder(0.09, 0.08, 2.0, 5, C.woodDark);
  b.push().translate(0.45, 1.75, 0).rotate(0, 0, -0.12).box(1.2, 0.34, 0.08, C.wood).pop();
}

function statue(b) {
  b.push().translate(0, 0.4, 0).box(2.0, 0.8, 2.0, C.stoneDark).pop();
  b.push().translate(0, 1.0, 0).box(1.2, 0.5, 1.2, C.stone).pop();
  b.push().translate(0, 2.2, 0).roundedBox(0.9, 2.0, 0.6, 0.22, C.stone).pop();
  b.push().translate(0, 3.5, 0).sphere(0.36, 7, 5, C.stone).pop();
  b.push().translate(0.55, 2.4, 0).rotate(0, 0, 0.5).roundedBox(0.28, 1.5, 0.28, 0.12, C.stone).pop();
  b.push().translate(-0.55, 2.4, 0).rotate(0, 0, -0.5).roundedBox(0.28, 1.5, 0.28, 0.12, C.stone).pop();
}

function barrel(b) {
  b.cylinder(0.42, 0.42, 1.0, 8, C.wood);
  b.push().translate(0, 0.25, 0).cylinder(0.45, 0.45, 0.09, 8, C.metalDark).pop();
  b.push().translate(0, 0.7, 0).cylinder(0.45, 0.45, 0.09, 8, C.metalDark).pop();
}

function gate(b) {
  for (const sx of [-1, 1]) {
    b.push().translate(sx * 3.2, 3.0, 0).box(1.6, 6.0, 1.6, C.stoneDark).pop();
    b.push().translate(sx * 3.2, 6.3, 0).box(1.9, 0.6, 1.9, C.stone).pop();
  }
  b.push().translate(0, 5.6, 0).box(8.0, 1.0, 1.2, C.stone).pop();
}

function crystal(b, rng) {
  for (let i = 0; i < 4; i++) {
    const a = rng() * Math.PI * 2;
    const r = rng() * 0.4;
    b.push().translate(Math.cos(a) * r, 0, Math.sin(a) * r)
      .rotate((rng() - 0.5) * 0.4, a, (rng() - 0.5) * 0.4);
    b.cylinder(0.22, 0.02, 1.0 + rng() * 1.2, 5, [0.35, 0.62, 0.95]);
    b.pop();
  }
}

/* --------------------------------------------------------------- 武器 */
function sword(b, len = 1.1, blade = C.metal, guard = C.metalDark) {
  b.push().translate(0, -0.28, 0).cylinder(0.05, 0.045, 0.28, 5, [0.25, 0.16, 0.12]).pop();
  b.push().translate(0, -0.30, 0).sphere(0.07, 5, 4, guard).pop();
  b.push().translate(0, 0.02, 0).box(0.36, 0.07, 0.1, guard).pop();
  b.push().translate(0, len / 2 + 0.05, 0).box(0.11, len, 0.035, blade).pop();
  b.push().translate(0, len + 0.05, 0).rotate(0, Math.PI / 4, 0).box(0.08, 0.16, 0.08, blade).pop();
}
function greatsword(b) {
  b.push().translate(0, -0.42, 0).cylinder(0.06, 0.055, 0.45, 5, [0.22, 0.15, 0.11]).pop();
  b.push().translate(0, 0.03, 0).box(0.62, 0.09, 0.12, C.metalDark).pop();
  b.push().translate(0, 0.95, 0).box(0.19, 1.8, 0.05, [0.66, 0.68, 0.74]).pop();
  b.push().translate(0, 1.9, 0).rotate(0, Math.PI / 4, 0).box(0.13, 0.24, 0.13, [0.66, 0.68, 0.74]).pop();
}
function axeMesh(b) {
  b.push().translate(0, 0.2, 0).cylinder(0.06, 0.05, 1.0, 5, C.woodDark).pop();
  b.push().translate(0.16, 1.0, 0).box(0.42, 0.5, 0.08, C.metal).pop();
  b.push().translate(0.42, 1.0, 0).box(0.12, 0.62, 0.05, [0.75, 0.77, 0.8]).pop();
}
function spearMesh(b) {
  b.push().translate(0, 0.1, 0).cylinder(0.05, 0.045, 2.0, 5, C.wood).pop();
  b.push().translate(0, 2.1, 0).cylinder(0.11, 0.0, 0.5, 5, C.metal).pop();
}
function bowMesh(b) {
  for (let i = 0; i < 8; i++) {
    const t0 = i / 8, t1 = (i + 1) / 8;
    const a0 = (t0 - 0.5) * 2.2, a1 = (t1 - 0.5) * 2.2;
    const x0 = Math.cos(a0) * 0.5 - 0.5, y0 = Math.sin(a0) * 1.0;
    const x1 = Math.cos(a1) * 0.5 - 0.5, y1 = Math.sin(a1) * 1.0;
    b.push().translate((x0 + x1) / 2, (y0 + y1) / 2, 0)
      .rotate(0, 0, Math.atan2(y1 - y0, x1 - x0) - Math.PI / 2)
      .box(0.07, Math.hypot(x1 - x0, y1 - y0) * 1.05, 0.07, C.wood).pop();
  }
  b.push().translate(0, 0, 0).box(0.02, 1.7, 0.02, [0.85, 0.83, 0.78]).pop();
}
function staffMesh(b) {
  b.push().translate(0, -0.2, 0).cylinder(0.055, 0.05, 1.7, 5, C.woodDark).pop();
  b.push().translate(0, 1.6, 0).sphere(0.19, 7, 5, [0.45, 0.72, 1.0]).pop();
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2;
    b.push().translate(Math.cos(a) * 0.17, 1.45, Math.sin(a) * 0.17).rotate(0, 0, 0.4)
      .box(0.05, 0.42, 0.05, C.gold).pop();
  }
}
function shieldMesh(b) {
  b.push().translate(0, 0, 0).scale(1, 1.25, 0.28);
  b.sphere(0.52, 8, 6, [0.45, 0.36, 0.30]);
  b.pop();
  b.push().translate(0, 0, 0.14).cylinder(0.16, 0.13, 0.08, 8, C.metal).pop();
  b.push().translate(0, 0, 0.13).box(0.1, 1.1, 0.05, C.metalDark).pop();
}
function daggerMesh(b) {
  b.push().translate(0, -0.16, 0).cylinder(0.04, 0.035, 0.18, 5, [0.2, 0.14, 0.1]).pop();
  b.push().translate(0, 0.0, 0).box(0.2, 0.05, 0.08, C.metalDark).pop();
  b.push().translate(0, 0.3, 0).box(0.08, 0.55, 0.03, [0.78, 0.8, 0.85]).pop();
}

/* --------------------------------------------------------------- 生成 */
export function buildModels(gl) {
  const models = {};
  const add = (name, fn) => {
    const b = new MeshBuilder();
    fn(b);
    models[name] = b.build(gl);
  };

  // キャラクターのパーツ（白: インスタンス色で着色）
  add('part', (b) => b.roundedBox(1, 1, 1, 0.22, C.white, 3));
  add('slab', (b) => b.box(1, 1, 1, C.white, 0.04));
  add('partSlim', (b) => b.roundedBox(1, 1, 1, 0.36, C.white, 3));
  add('ball', (b) => b.sphere(0.5, 8, 6, C.white));
  add('spike', (b) => b.cylinder(0.5, 0.0, 1.0, 6, C.white));
  add('disc', (b) => b.cylinder(0.5, 0.5, 1.0, 10, C.white));

  // 樹木（種類ごとに 3 バリエーション）
  for (const kind of Object.keys(TREE_FN)) {
    for (let v = 0; v < 3; v++) {
      const rng = makeRng(9000 + v * 31 + kind.length * 7);
      add(`tree_${kind}_${v}`, (b) => TREE_FN[kind](b, rng));
    }
  }
  for (let v = 0; v < 3; v++) {
    const rng = makeRng(4400 + v * 17);
    add(`rock_${v}`, (b) => rockMesh(b, rng, 0.9 + v * 0.75));
    add(`bush_${v}`, (b) => bushMesh(b, makeRng(700 + v * 13)));
  }
  add('grass', (b) => grassTuft(b, makeRng(1234)));
  add('crystal', (b) => crystal(b, makeRng(555)));

  add('house_a', (b) => house(b, makeRng(11), 6, 5, 3.2));
  add('house_b', (b) => house(b, makeRng(12), 8, 6, 3.6));
  add('tower', tower);
  add('ruin_arch', ruinArch);
  add('pillar', (b) => pillar(b, makeRng(31)));
  add('tent', tent);
  add('campfire', campfire);
  add('shrine', shrine);
  add('chest', chest);
  add('fence', fence);
  add('signpost', signpost);
  add('statue', statue);
  add('barrel', barrel);
  add('gate', gate);

  add('w_sword', (b) => sword(b));
  add('w_greatsword', greatsword);
  add('w_axe', axeMesh);
  add('w_spear', spearMesh);
  add('w_bow', bowMesh);
  add('w_staff', staffMesh);
  add('w_shield', shieldMesh);
  add('w_dagger', daggerMesh);

  return models;
}

/** 地形チャンク用の可変メッシュビルダー（頂点カラー） */
export function buildTerrainGeometry(gl, verts, indices) {
  return new Geometry(gl, verts, indices);
}
