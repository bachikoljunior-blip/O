// 地形チャンクのストリーミング（LOD・スカート・散布物のプリベイク）

import { Geometry, INST_FLOATS } from '../core/gl.js';
import { hash2, makeRng } from '../core/noise.js';
import { writeInstance } from '../render/instance.js';
import { clamp01, lerp } from '../core/math.js';

export const CHUNK = 64;                 // チャンク一辺（m）
const LOD_RES = [32, 16, 8, 4];          // LOD ごとの分割数
const LOD_DIST = [110, 230, 440];        // LOD 切替距離（m）

const TREE_KINDS = ['pine', 'oak', 'birch', 'willow', 'dead'];

export class Chunk {
  constructor(cx, cz, lod) {
    this.cx = cx; this.cz = cz; this.lod = lod;
    this.x0 = cx * CHUNK; this.z0 = cz * CHUNK;
    this.geo = null;
    this.props = null;      // Map<modelName, Float32Array>
    // props を描画側の Batch と組にして焼いたもの [Batch, データ, ...]。
    // 初めて描かれたときに作られる。LOD が変われば Chunk ごと作り直されるので、
    // 古い対応が残ることはない
    this.emitList = null;
    this.grass = null;      // Float32Array
    this.colliders = null;  // Float32Array [x,z,r, ...]
    this.minY = 0; this.maxY = 0;
    this.center = [0, 0, 0];
    this.radius = CHUNK;
    this.ready = false;
    this.lastUsed = 0;
  }
  dispose() {
    if (this.batch) { this.batch.dispose(); this.batch = null; }
    if (this.geo) this.geo.dispose();
    this.geo = null;
    this.props = null;
    this.emitList = null;
    this.grass = null;
  }
}

export class Terrain {
  constructor(gl, world, quality) {
    this.gl = gl;
    this.world = world;
    this.q = quality;
    this.chunks = new Map();
    this.pending = [];
    this.visible = [];
    this.grassChunks = [];
    this._hgrid = null;
    this._hgridRes = -1;
  }

  key(cx, cz) { return cx * 100003 + cz; }

  /** @param dist2 距離の平方（毎フレーム 289 回通るので平方根を取らない）*/
  lodFor(dist2) {
    for (let i = 0; i < LOD_DIST.length; i++) {
      const r = LOD_DIST[i] * this.q.lodScale;
      if (dist2 < r * r) return i;
    }
    return LOD_DIST.length;
  }

  /** 毎フレーム：可視チャンクの決定と生成キューの更新 */
  update(px, pz, time, budgetMs = 6) {
    const R = this.q.viewChunks;
    const pcx = Math.floor(px / CHUNK), pcz = Math.floor(pz / CHUNK);
    this.visible.length = 0;
    this.grassChunks.length = 0;
    this.pending.length = 0;

    // 距離は「見える範囲か」「どの LOD か」「近い順」にしか使わないので、
    // 平方のまま持ち回る。ここは毎フレーム (2R+1)^2 ＝ 289 回通り、
    // Math.hypot だとそれだけで 13µs 掛かっていた
    const cut = R * CHUNK + CHUNK;
    const cut2 = cut * cut;
    const grass2 = this.q.grassDist * this.q.grassDist;
    for (let dz = -R; dz <= R; dz++) {
      for (let dx = -R; dx <= R; dx++) {
        const cx = pcx + dx, cz = pcz + dz;
        const ccx = cx * CHUNK + CHUNK / 2, ccz = cz * CHUNK + CHUNK / 2;
        const ex = ccx - px, ez = ccz - pz;
        const dist = ex * ex + ez * ez;
        if (dist > cut2) continue;
        const lod = this.lodFor(dist);
        const k = this.key(cx, cz);
        let c = this.chunks.get(k);
        if (!c || c.lod !== lod) {
          if (c) { c.dispose(); this.chunks.delete(k); }
          c = new Chunk(cx, cz, lod);
          this.chunks.set(k, c);
        }
        c.lastUsed = time;
        if (!c.ready) {
          this.pending.push({ c, dist });
        } else {
          this.visible.push(c);
          if (lod === 0 && dist < grass2) this.grassChunks.push(c);
        }
      }
    }

    // 近い順に時間予算いっぱいまで生成（平方でも順序は同じ）
    this.pending.sort((a, b) => a.dist - b.dist);
    const t0 = performance.now();
    for (const p of this.pending) {
      if (performance.now() - t0 > budgetMs) break;
      this.buildChunk(p.c);
      this.visible.push(p.c);
      if (p.c.lod === 0 && p.dist < grass2) this.grassChunks.push(p.c);
    }

    // 遠くなったチャンクを解放
    if ((time * 2 | 0) % 4 === 0) {
      const limit = (R + 2) * CHUNK;
      const limit2 = limit * limit;
      for (const [k, c] of this.chunks) {
        const ccx = c.x0 + CHUNK / 2 - px, ccz = c.z0 + CHUNK / 2 - pz;
        if (ccx * ccx + ccz * ccz > limit2) {
          c.dispose();
          this.chunks.delete(k);
        }
      }
    }
  }

  /* ------------------------------------------------------- メッシュ生成 */
  buildChunk(c) {
    const w = this.world;
    const res = LOD_RES[c.lod];
    const step = CHUNK / res;
    const n = res + 1;

    // 法線用に 1 セル分の外周を含めた高さグリッド
    const hg = new Float32Array((n + 2) * (n + 2));
    for (let j = -1; j <= res + 1; j++) {
      for (let i = -1; i <= res + 1; i++) {
        hg[(j + 1) * (n + 2) + (i + 1)] = w.height(c.x0 + i * step, c.z0 + j * step);
      }
    }
    const H = (i, j) => hg[(j + 1) * (n + 2) + (i + 1)];

    const vcount = n * n;
    const verts = new Float32Array((vcount + n * 4) * 9);
    const idx = [];
    const col = [0, 0, 0];
    let minY = 1e9, maxY = -1e9;
    let o = 0;

    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        const x = c.x0 + i * step, z = c.z0 + j * step;
        const y = H(i, j);
        const hL = H(i - 1, j), hR = H(i + 1, j), hD = H(i, j - 1), hU = H(i, j + 1);
        let nx = hL - hR, ny = 2 * step, nz = hD - hU;
        const l = Math.hypot(nx, ny, nz) || 1;
        nx /= l; ny /= l; nz /= l;
        const slope = Math.hypot(hR - hL, hU - hD) / (2 * step);
        w.surfaceColor(x, z, y, slope, col);
        verts[o++] = x; verts[o++] = y; verts[o++] = z;
        verts[o++] = nx; verts[o++] = ny; verts[o++] = nz;
        verts[o++] = col[0]; verts[o++] = col[1]; verts[o++] = col[2];
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }

    for (let j = 0; j < res; j++) {
      for (let i = 0; i < res; i++) {
        const a = j * n + i, b = a + 1, d = a + n, e = d + 1;
        // 対角線を短い方に合わせて段差を目立たなくする
        if (Math.abs(H(i, j) - H(i + 1, j + 1)) < Math.abs(H(i + 1, j) - H(i, j + 1))) {
          idx.push(a, d, e, a, e, b);
        } else {
          idx.push(a, d, b, b, d, e);
        }
      }
    }

    // スカート（LOD 境界の隙間を隠す）
    const skirtDrop = 3 + c.lod * 4;
    let sv = vcount;
    const addSkirt = (i, j) => {
      const src = (j * n + i) * 9;
      const p = sv * 9;
      verts[p] = verts[src]; verts[p + 1] = verts[src + 1] - skirtDrop; verts[p + 2] = verts[src + 2];
      verts[p + 3] = verts[src + 3]; verts[p + 4] = verts[src + 4]; verts[p + 5] = verts[src + 5];
      verts[p + 6] = verts[src + 6] * 0.8; verts[p + 7] = verts[src + 7] * 0.8; verts[p + 8] = verts[src + 8] * 0.8;
      return sv++;
    };
    const edges = [
      { get: (t) => [t, 0], flip: false },
      { get: (t) => [t, res], flip: true },
      { get: (t) => [0, t], flip: true },
      { get: (t) => [res, t], flip: false },
    ];
    for (const e of edges) {
      let prevTop = -1, prevBot = -1;
      for (let t = 0; t <= res; t++) {
        const [i, j] = e.get(t);
        const top = j * n + i;
        const bot = addSkirt(i, j);
        if (prevTop >= 0) {
          if (e.flip) idx.push(prevTop, prevBot, bot, prevTop, bot, top);
          else idx.push(prevTop, bot, prevBot, prevTop, top, bot);
        }
        prevTop = top; prevBot = bot;
      }
    }

    c.geo = new Geometry(this.gl, verts.subarray(0, sv * 9), idx);
    c.minY = minY; c.maxY = maxY;
    c.center = [c.x0 + CHUNK / 2, (minY + maxY) / 2, c.z0 + CHUNK / 2];
    c.radius = Math.hypot(CHUNK * 0.75, (maxY - minY) / 2 + skirtDrop);

    // 散布物は高さグリッドを補間して使う（height() の再評価を避ける）
    const clampI = (v) => (v < -1 ? -1 : v > res ? res : v);
    const sample = (x, z) => {
      const fi = (x - c.x0) / step, fj = (z - c.z0) / step;
      const i = clampI(Math.floor(fi)), j = clampI(Math.floor(fj));
      const tx = Math.max(0, Math.min(1, fi - i)), tz = Math.max(0, Math.min(1, fj - j));
      const h00 = H(i, j), h10 = H(i + 1, j), h01 = H(i, j + 1), h11 = H(i + 1, j + 1);
      return lerp(lerp(h00, h10, tx), lerp(h01, h11, tx), tz);
    };
    const slopeAt = (x, z) => Math.hypot(
      sample(x + step, z) - sample(x - step, z),
      sample(x, z + step) - sample(x, z - step)) / (2 * step);

    this.scatter(c, sample, slopeAt);
    c.ready = true;
  }

  /* ----------------------------------------------------------- 散布物 */
  scatter(c, sampleH, sampleSlope) {
    const w = this.world;
    const props = new Map();
    const lists = new Map();
    const colliders = [];
    const rng = makeRng(hash2(c.cx, c.cz, 4711) * 1e9 | 0);

    const pushProp = (model, data) => {
      let l = lists.get(model);
      if (!l) { l = []; lists.set(model, l); }
      l.push(data);
    };

    const detail = c.lod === 0 ? 1 : c.lod === 1 ? 0.75 : c.lod === 2 ? 0.45 : 0.2;
    // --- 樹木 ---
    const cells = 7;
    const cell = CHUNK / cells;
    for (let j = 0; j < cells; j++) {
      for (let i = 0; i < cells; i++) {
        const x = c.x0 + (i + 0.15 + rng() * 0.7) * cell;
        const z = c.z0 + (j + 0.15 + rng() * 0.7) * cell;
        const p = w.params(x, z);
        const density = p.treeDensity * this.q.treeDensity;
        if (rng() > density) continue;
        const h = sampleH(x, z);
        if (h < 2.2 || h > 190) continue;
        if (sampleSlope(x, z) > 0.85) continue;
        const f = w.flatAt(x, z);
        if (f && (f.w > 0.5 || f.road > 0.2)) continue;
        // 地方ごとの主要樹種 + まれに他樹種
        let kind = p.region.tree;
        if (rng() < 0.12) kind = TREE_KINDS[(rng() * TREE_KINDS.length) | 0];
        const v = (rng() * 3) | 0;
        const s = 0.72 + rng() * 0.5;
        const tint = 0.85 + rng() * 0.3;
        pushProp(`tree_${kind}_${v}`, [x, h - 0.3, z, rng() * Math.PI * 2, s, s, s,
          tint, tint * (0.96 + rng() * 0.08), tint, 1, 0.35, 0]);
        colliders.push(x, z, 0.55 * s);
      }
    }

    // --- 岩・茂み ---
    const rcells = 5;
    const rcell = CHUNK / rcells;
    for (let j = 0; j < rcells; j++) {
      for (let i = 0; i < rcells; i++) {
        const x = c.x0 + (i + rng()) * rcell;
        const z = c.z0 + (j + rng()) * rcell;
        const p = w.params(x, z);
        const h = sampleH(x, z);
        if (h < 0.6) continue;
        const slope = sampleSlope(x, z);
        const r = rng();
        if (r < 0.16 + slope * 0.42) {
          const v = (rng() * 3) | 0;
          // 急斜面では巨岩・露頭になり、稜線に表情を与える
          const outcrop = slope > 0.95 && rng() < 0.45;
          const s = outcrop ? 1.5 + rng() * 1.7 : 0.5 + rng() * 1.1;
          const g = (0.8 + rng() * 0.35) * (outcrop ? 0.94 : 1);
          pushProp(`rock_${v}`, [x, h - (outcrop ? 0.9 : 0.25) * s, z, rng() * Math.PI * 2,
            s, s * (outcrop ? 0.85 + rng() * 0.8 : 0.7 + rng() * 0.5), s,
            g, g, g * 1.02, 1, 0, 0]);
          if (s > 0.8) colliders.push(x, z, (outcrop ? 0.62 : 0.8) * s);
          // 尖塔状の岩（荒野・高山帯）
          if (outcrop && p.rough > 1.45 && rng() < 0.35) {
            const ps = 0.8 + rng() * 0.9;
            pushProp('pillar', [x + (rng() - 0.5) * 3, h - 0.4, z + (rng() - 0.5) * 3,
              rng() * Math.PI * 2, ps, ps * (1.1 + rng() * 0.9), ps,
              g * 0.95, g * 0.93, g * 0.92, 1, 0, 0]);
          }
        } else if (rng() < p.bushDensity * 0.35 * detail && slope < 0.7 && h > 1.6) {
          const v = (rng() * 3) | 0;
          const s = 0.7 + rng() * 0.7;
          pushProp(`bush_${v}`, [x, h, z, rng() * Math.PI * 2, s, s, s, 1, 1, 1, 1, 0.5, 0]);
        }
      }
    }

    for (const [model, arr] of lists) {
      const data = new Float32Array(arr.length * INST_FLOATS);
      let o = 0;
      for (const d of arr) {
        o = writeInstance(data, o, d[0], d[1], d[2], d[3], d[4], d[5], d[6],
          d[7], d[8], d[9], d[10], d[11], d[12], 0, 0.5);
      }
      props.set(model, data);
    }
    c.props = props;
    c.colliders = new Float32Array(colliders);

    // --- 草（LOD0 のみ）---
    if (c.lod === 0 && this.q.grassDensity > 0) {
      const gc = Math.round(16 * this.q.grassDensity);
      const gcell = CHUNK / gc;
      const tmp = [];
      const grng = makeRng(hash2(c.cx, c.cz, 9911) * 1e9 | 0);
      for (let j = 0; j < gc; j++) {
        for (let i = 0; i < gc; i++) {
          const x = c.x0 + (i + grng()) * gcell;
          const z = c.z0 + (j + grng()) * gcell;
          const p = w.params(x, z);
          if (grng() > clamp01(p.grassDensity * 0.85)) continue;
          const h = sampleH(x, z);
          if (h < 1.4 || h > 165) continue;
          if (sampleSlope(x, z) > 0.72) continue;
          const f = w.flatAt(x, z);
          if (f && f.road > 0.35) continue;
          const s = 0.72 + grng() * 0.6;
          const tint = 0.78 + grng() * 0.44;
          tmp.push([x, h - 0.04, z, grng() * Math.PI * 2, s, s * (0.85 + grng() * 0.55), s,
            lerp(0.92, 1.08, grng()) * tint, tint, tint * 0.88]);
        }
      }
      const g = new Float32Array(tmp.length * INST_FLOATS);
      let o = 0;
      for (const d of tmp) {
        o = writeInstance(g, o, d[0], d[1], d[2], d[3], d[4], d[5], d[6],
          d[7], d[8], d[9], 1, 1.0, 0, 0, 0.5);
      }
      c.grass = g;
    }
  }

  /** 近傍チャンクの障害物と円判定（プレイヤー・敵の移動用） */
  collide(x, z, radius, out) {
    const pcx = Math.floor(x / CHUNK), pcz = Math.floor(z / CHUNK);
    let hit = false;
    out[0] = x; out[1] = z;
    for (let dz = -1; dz <= 1; dz++) {
      for (let dx = -1; dx <= 1; dx++) {
        const c = this.chunks.get(this.key(pcx + dx, pcz + dz));
        if (!c || !c.colliders) continue;
        const a = c.colliders;
        for (let i = 0; i < a.length; i += 3) {
          const ddx = out[0] - a[i], ddz = out[1] - a[i + 1];
          const r = a[i + 2] + radius;
          const d2 = ddx * ddx + ddz * ddz;
          if (d2 < r * r && d2 > 1e-6) {
            const d = Math.sqrt(d2);
            out[0] = a[i] + (ddx / d) * r;
            out[1] = a[i + 1] + (ddz / d) * r;
            hit = true;
          }
        }
      }
    }
    return hit;
  }
}
