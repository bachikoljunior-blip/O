// 拠点の構造物を、通り抜けられない箱として登録する。
//
// 木や岩はチャンクごとの円で既に塞がれているが、家・納屋・櫓といった
// 建物は素通りできてしまっていた。建物は円では表せない（角の隙間が
// おかしくなる）ので、回転を持つ矩形として扱う。
//
// 箱の寸法はメッシュ自身の外接矩形から取る。手で表を書くと、
// メッシュを直したときに必ず食い違う。

import { MODEL_BOUNDS } from '../render/mesh.js';

/** 通り抜けられるもの。踏める・くぐれる・拾える類 */
const PASSABLE = new Set([
  'bones', 'crop_row', 'banner', 'sword_stuck', 'broken_shield', 'scarecrow',
  'fog_gate', 'fog_veil', 'stairs_down', 'grass', 'signpost',
]);

/** 空間ハッシュのセル幅（m） */
const CELL = 16;

export class Blockers {
  constructor() {
    this.list = [];
    this.grid = new Map();
  }

  key(cx, cz) { return `${cx},${cz}`; }

  /**
   * POI 群から箱を作る。
   * 高さが膝より低いものは跨げるものとみなして無視する。
   */
  build(pois) {
    this.list.length = 0;
    this.grid.clear();
    for (const p of pois) {
      for (const st of p.structures || []) {
        const b = MODEL_BOUNDS[st.model];
        if (!b) continue;
        if (PASSABLE.has(st.model)) continue;
        const top = b.maxY * (st.sy || 1);
        if (top < 0.55) continue;                 // 跨げる高さ
        const hx = (b.maxX - b.minX) * 0.5 * (st.sx || 1);
        const hz = (b.maxZ - b.minZ) * 0.5 * (st.sz || 1);
        if (hx < 0.12 || hz < 0.12) continue;
        // メッシュの原点が中心とは限らないので、ずれを回転させて足す
        const ox = (b.maxX + b.minX) * 0.5 * (st.sx || 1);
        const oz = (b.maxZ + b.minZ) * 0.5 * (st.sz || 1);
        const c = Math.cos(st.rotY || 0), s = Math.sin(st.rotY || 0);
        // 実際の見た目より少しだけ細くして、壁に貼りついて見えないようにする
        const k = 0.92;
        this.add({
          x: st.x + ox * c - oz * s,
          z: st.z + ox * s + oz * c,
          hx: hx * k, hz: hz * k,
          cos: c, sin: s,
          r: Math.hypot(hx, hz) * k,
          model: st.model,
        });
      }
    }
    return this;
  }

  add(o) {
    const i = this.list.length;
    this.list.push(o);
    const x0 = Math.floor((o.x - o.r) / CELL), x1 = Math.floor((o.x + o.r) / CELL);
    const z0 = Math.floor((o.z - o.r) / CELL), z1 = Math.floor((o.z + o.r) / CELL);
    for (let cz = z0; cz <= z1; cz++) {
      for (let cx = x0; cx <= x1; cx++) {
        const k = this.key(cx, cz);
        let a = this.grid.get(k);
        if (!a) { a = []; this.grid.set(k, a); }
        a.push(i);
      }
    }
  }

  /** その位置にある箱（当たり判定を持つ構造物）を返す。テストと NPC 用 */
  at(x, z) {
    const a = this.grid.get(this.key(Math.floor(x / CELL), Math.floor(z / CELL)));
    if (!a) return null;
    for (const i of a) {
      const o = this.list[i];
      const dx = x - o.x, dz = z - o.z;
      const lx = dx * o.cos + dz * o.sin;
      const lz = -dx * o.sin + dz * o.cos;
      if (Math.abs(lx) <= o.hx && Math.abs(lz) <= o.hz) return o;
    }
    return null;
  }

  /**
   * 半径 radius の円を箱の外へ押し出す。out に [x, z] を書いて true を返す。
   * 複数に接しているときは順に解くので、角でも詰まらない。
   */
  resolve(x, z, radius, out) {
    out[0] = x; out[1] = z;
    const cx = Math.floor(x / CELL), cz = Math.floor(z / CELL);
    let hit = false;
    // 隣り合う箱に順に押されると、最初の箱の中へ戻されることがある。
    // 二度なぞって、どちらの外にも出た位置に落ち着かせる
    for (let pass = 0; pass < 2; pass++) {
      let any = false;
      for (let dz = -1; dz <= 1; dz++) {
        for (let dx = -1; dx <= 1; dx++) {
          const a = this.grid.get(this.key(cx + dx, cz + dz));
          if (!a) continue;
          for (const i of a) {
            if (this.push(this.list[i], radius, out)) { any = true; hit = true; }
          }
        }
      }
      if (!any) break;
    }
    return hit;
  }

  /** 箱ひとつぶんの押し出し */
  push(o, radius, out) {
    const dx = out[0] - o.x, dz = out[1] - o.z;
    if (dx * dx + dz * dz > (o.r + radius) * (o.r + radius)) return false;
    // 箱のローカル座標へ
    const lx = dx * o.cos + dz * o.sin;
    const lz = -dx * o.sin + dz * o.cos;
    const qx = Math.max(-o.hx, Math.min(o.hx, lx));
    const qz = Math.max(-o.hz, Math.min(o.hz, lz));
    let nx, nz;
    if (qx === lx && qz === lz) {
      // 箱の中。いちばん近い面へ押し出す
      const ex = o.hx - Math.abs(lx), ez = o.hz - Math.abs(lz);
      if (ex < ez) { nx = (lx >= 0 ? 1 : -1) * (o.hx + radius); nz = lz; }
      else { nx = lx; nz = (lz >= 0 ? 1 : -1) * (o.hz + radius); }
    } else {
      const ox = lx - qx, oz = lz - qz;
      const d = Math.hypot(ox, oz);
      if (d >= radius) return false;
      if (d < 1e-5) { nx = qx + radius; nz = qz; }
      else { nx = qx + (ox / d) * radius; nz = qz + (oz / d) * radius; }
    }
    out[0] = o.x + nx * o.cos - nz * o.sin;
    out[1] = o.z + nx * o.sin + nz * o.cos;
    return true;
  }
}
