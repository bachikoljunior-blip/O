// 屋内ダンジョン：部屋と通路の生成、壁・床・天井のインスタンス化、松明と宝

import { makeRng } from '../core/noise.js';
import { INST_FLOATS } from '../core/gl.js';
import { writeInstance } from '../render/instance.js';
import { TAU } from '../core/math.js';

/** ダンジョンはワールドから遠く離れた座標空間に置く（POI と干渉させないため） */
export const DUNGEON_ORIGIN = 50000;

const CELL = 6.4;          // セル一辺（m）
const CEIL = 4.2;          // 天井高
const WALL_T = 0.6;        // 壁の厚み

const STONE = [0.40, 0.39, 0.37];
const STONE_DARK = [0.27, 0.26, 0.25];
const FLOOR = [0.33, 0.31, 0.29];
const MOSS = [0.24, 0.30, 0.22];

const THEMES = {
  catacomb: {
    name: '地下墓所', floor: FLOOR, wall: STONE, accent: MOSS,
    torchColor: [1.0, 0.62, 0.24], enemies: ['skeleton', 'wraith', 'crawler'],
    fog: [0.05, 0.055, 0.07],
  },
  ruin: {
    name: '沈んだ遺構', floor: [0.30, 0.32, 0.33], wall: [0.36, 0.39, 0.40],
    accent: [0.20, 0.30, 0.32],
    torchColor: [0.55, 0.80, 1.0], enemies: ['wraith', 'gargoyle', 'skeleton'],
    fog: [0.04, 0.06, 0.08],
  },
  mine: {
    name: '崩れた坑道', floor: [0.34, 0.29, 0.25], wall: [0.38, 0.32, 0.27],
    accent: [0.45, 0.30, 0.18],
    torchColor: [1.0, 0.55, 0.20], enemies: ['imp', 'crawler', 'brute'],
    fog: [0.07, 0.05, 0.04],
  },
};

export class Dungeon {
  /**
   * @param poi   入口となる POI
   * @param depth 階層の深さ（難度と規模に影響）
   */
  constructor(poi, seed, depth = 1) {
    this.poi = poi;
    this.depth = depth;
    this.theme = THEMES[poi.dungeonTheme] || THEMES.catacomb;
    this.name = `${poi.name}・${this.theme.name}`;
    const rng = makeRng(seed | 0);
    this.rng = rng;

    this.gw = 18 + Math.min(8, depth * 2);
    this.gh = 18 + Math.min(8, depth * 2);
    this.grid = new Uint8Array(this.gw * this.gh);   // 0=岩盤 1=部屋 2=通路
    this.rooms = [];
    this.generate(rng);

    this.torches = [];
    this.chests = [];
    this.spawns = [];
    this.props = new Map();       // model -> Float32Array
    this.colliderGrid = this.grid; // 壁判定はグリッドをそのまま使う
    this.build(rng);
  }

  idx(cx, cz) { return cz * this.gw + cx; }
  open(cx, cz) {
    if (cx < 0 || cz < 0 || cx >= this.gw || cz >= this.gh) return false;
    return this.grid[this.idx(cx, cz)] !== 0;
  }

  /** セル座標 → ワールド座標（ダンジョン空間） */
  toWorld(cx, cz) {
    return [DUNGEON_ORIGIN + (cx - this.gw / 2) * CELL, DUNGEON_ORIGIN + (cz - this.gh / 2) * CELL];
  }
  toCell(x, z) {
    return [
      Math.floor((x - DUNGEON_ORIGIN) / CELL + this.gw / 2),
      Math.floor((z - DUNGEON_ORIGIN) / CELL + this.gh / 2),
    ];
  }

  /* ------------------------------------------------------------ 生成 */
  generate(rng) {
    const tries = 90;
    const want = 6 + Math.min(6, this.depth * 2);
    for (let t = 0; t < tries && this.rooms.length < want; t++) {
      const w = 3 + (rng() * 4 | 0);
      const h = 3 + (rng() * 4 | 0);
      const x = 1 + (rng() * (this.gw - w - 2) | 0);
      const z = 1 + (rng() * (this.gh - h - 2) | 0);
      let ok = true;
      for (const r of this.rooms) {
        if (x < r.x + r.w + 1 && x + w + 1 > r.x && z < r.z + r.h + 1 && z + h + 1 > r.z) { ok = false; break; }
      }
      if (!ok) continue;
      const room = { x, z, w, h, cx: x + (w >> 1), cz: z + (h >> 1) };
      this.rooms.push(room);
      for (let j = z; j < z + h; j++) {
        for (let i = x; i < x + w; i++) this.grid[this.idx(i, j)] = 1;
      }
    }
    // 部屋を順に L 字通路でつなぐ
    for (let i = 1; i < this.rooms.length; i++) {
      const a = this.rooms[i - 1], b = this.rooms[i];
      this.carveCorridor(a.cx, a.cz, b.cx, b.cz, rng() < 0.5);
    }
    // 環状にして行き止まりを減らす
    if (this.rooms.length > 3) {
      const a = this.rooms[this.rooms.length - 1], b = this.rooms[0];
      this.carveCorridor(a.cx, a.cz, b.cx, b.cz, rng() < 0.5);
    }
    this.entry = this.rooms[0];
    this.bossRoom = this.rooms[this.rooms.length - 1];
  }

  carveCorridor(x0, z0, x1, z1, horizFirst) {
    const set = (i, j) => { if (this.grid[this.idx(i, j)] === 0) this.grid[this.idx(i, j)] = 2; };
    if (horizFirst) {
      for (let i = Math.min(x0, x1); i <= Math.max(x0, x1); i++) set(i, z0);
      for (let j = Math.min(z0, z1); j <= Math.max(z0, z1); j++) set(x1, j);
    } else {
      for (let j = Math.min(z0, z1); j <= Math.max(z0, z1); j++) set(x0, j);
      for (let i = Math.min(x0, x1); i <= Math.max(x0, x1); i++) set(i, z1);
    }
  }

  /* -------------------------------------------------- ジオメトリ構築 */
  build(rng) {
    const th = this.theme;
    const lists = new Map();
    const push = (model, d) => {
      let l = lists.get(model);
      if (!l) { l = []; lists.set(model, l); }
      l.push(d);
    };

    for (let cz = 0; cz < this.gh; cz++) {
      for (let cx = 0; cx < this.gw; cx++) {
        if (!this.open(cx, cz)) continue;
        const [wx, wz] = this.toWorld(cx, cz);
        const room = this.grid[this.idx(cx, cz)] === 1;
        const shade = 0.86 + rng() * 0.28;

        // 床
        push('slab', [wx, -0.25, wz, 0, CELL, 0.5, CELL,
          th.floor[0] * shade, th.floor[1] * shade, th.floor[2] * shade, 0, 0]);
        // 天井
        push('slab', [wx, CEIL + 0.3, wz, 0, CELL, 0.6, CELL,
          th.wall[0] * 0.55, th.wall[1] * 0.55, th.wall[2] * 0.55, 0, 0]);

        // 壁（隣が岩盤なら塞ぐ）
        const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
        for (const [dx, dz] of dirs) {
          if (this.open(cx + dx, cz + dz)) continue;
          const ox = dx * (CELL / 2 - WALL_T / 2);
          const oz = dz * (CELL / 2 - WALL_T / 2);
          const sx = dx !== 0 ? WALL_T : CELL;
          const sz = dz !== 0 ? WALL_T : CELL;
          const k = 0.9 + rng() * 0.22;
          push('slab', [wx + ox, CEIL / 2, wz + oz, 0, sx, CEIL, sz,
            th.wall[0] * k, th.wall[1] * k, th.wall[2] * k, 0, 0]);
        }

        // 部屋の隅に柱
        if (room && rng() < 0.16) {
          push('pillar', [wx + (rng() - 0.5) * CELL * 0.5, 0, wz + (rng() - 0.5) * CELL * 0.5,
            rng() * TAU, 1.1, CEIL / 3.4, 1.1, th.wall[0], th.wall[1], th.wall[2], 0, 0]);
        }
        // 苔むした瓦礫
        if (rng() < 0.22) {
          const s = 0.5 + rng() * 0.9;
          push(`rock_${(rng() * 3) | 0}`, [
            wx + (rng() - 0.5) * CELL * 0.6, -0.1, wz + (rng() - 0.5) * CELL * 0.6,
            rng() * TAU, s, s * 0.7, s,
            (th.wall[0] * 0.6 + th.accent[0] * 0.5), (th.wall[1] * 0.6 + th.accent[1] * 0.5),
            (th.wall[2] * 0.6 + th.accent[2] * 0.5), 0, 0]);
        }

        // 松明（壁沿い）
        if (rng() < 0.18) {
          for (const [dx, dz] of dirs) {
            if (this.open(cx + dx, cz + dz)) continue;
            const tx = wx + dx * (CELL / 2 - 0.9);
            const tz = wz + dz * (CELL / 2 - 0.9);
            push('slab', [tx, 2.1, tz, 0, 0.16, 0.9, 0.16, 0.24, 0.19, 0.14, 0, 0]);
            this.torches.push({ x: tx, y: 2.7, z: tz, color: th.torchColor });
            break;
          }
        }
      }
    }

    // 部屋ごとの中身
    this.rooms.forEach((r, i) => {
      const [wx, wz] = this.toWorld(r.cx, r.cz);
      if (i === 0) {
        // 入口の間：戻る階段
        push('shrine', [wx, 0, wz, 0, 0.9, 0.9, 0.9, 0.9, 0.85, 0.6, 0, 0.7]);
        this.exitPoint = { x: wx, z: wz };
        return;
      }
      const boss = i === this.rooms.length - 1;
      if (boss) {
        push('statue', [wx, 0, wz - CELL, Math.PI, 1.1, 1.1, 1.1,
          this.theme.wall[0] * 0.8, this.theme.wall[1] * 0.8, this.theme.wall[2] * 0.8, 0, 0]);
        this.chests.push({ x: wx + 1.6, z: wz + 1.6, tier: Math.min(5, 3 + this.depth), opened: false, id: `d${i}` });
        this.spawns.push({
          kind: this.theme.enemies[0], count: 2 + this.depth, radius: r.w * CELL * 0.4,
          x: wx, z: wz, elite: true,
        });
      } else {
        if (this.rng() < 0.55) {
          this.chests.push({ x: wx, z: wz, tier: Math.min(5, 2 + this.depth), opened: false, id: `d${i}` });
        }
        this.spawns.push({
          kind: this.theme.enemies[(this.rng() * this.theme.enemies.length) | 0],
          count: 1 + (this.rng() * (1 + this.depth) | 0),
          radius: r.w * CELL * 0.35, x: wx, z: wz,
        });
      }
      // 灯り
      if (this.rng() < 0.7) {
        push('campfire', [wx + (this.rng() - 0.5) * 2, 0, wz + (this.rng() - 0.5) * 2, 0, 1, 1, 1,
          1, 0.8, 0.5, 0, 0.7]);
        this.torches.push({ x: wx, y: 0.9, z: wz, color: this.theme.torchColor, big: true });
      }
    });

    // インスタンス配列に焼く
    for (const [model, arr] of lists) {
      const data = new Float32Array(arr.length * INST_FLOATS);
      let o = 0;
      // slab（床・壁・天井）は近接フェードさせず、岩肌ディテールを乗せる
      // d[10]=wind, d[11]=emissive
      const detail = model === 'slab' ? 1 : 0.5;
      const colorMix = model === 'slab' ? 1 : 0;
      for (const d of arr) {
        o = writeInstance(data, o, d[0], d[1], d[2], d[3], d[4], d[5], d[6],
          d[7], d[8], d[9], 1, d[10] || 0, d[11] || 0, colorMix, detail);
      }
      this.props.set(model, data);
    }

    const [ex, ez] = this.toWorld(this.entry.cx, this.entry.cz);
    this.spawnPoint = { x: ex + 2.5, z: ez + 2.5 };
  }

  /* ----------------------------------------------------------- 問い合わせ */
  groundHeight() { return 0; }

  /** 壁との円判定（押し出し） */
  collide(x, z, radius, out) {
    out[0] = x; out[1] = z;
    const [cx, cz] = this.toCell(x, z);
    let hit = false;
    for (let j = cz - 1; j <= cz + 1; j++) {
      for (let i = cx - 1; i <= cx + 1; i++) {
        if (this.open(i, j)) continue;
        const [wx, wz] = this.toWorld(i, j);
        const half = CELL / 2;
        // 軸並行ボックスとの押し出し
        const nx = Math.max(wx - half, Math.min(out[0], wx + half));
        const nz = Math.max(wz - half, Math.min(out[1], wz + half));
        const dx = out[0] - nx, dz = out[1] - nz;
        const d2 = dx * dx + dz * dz;
        if (d2 < radius * radius) {
          const d = Math.sqrt(d2);
          if (d < 1e-4) {
            // ボックス内部：最も近い面へ押し出す
            const px = out[0] - wx, pz = out[1] - wz;
            if (Math.abs(px) > Math.abs(pz)) out[0] = wx + Math.sign(px || 1) * (half + radius);
            else out[1] = wz + Math.sign(pz || 1) * (half + radius);
          } else {
            out[0] = nx + (dx / d) * radius;
            out[1] = nz + (dz / d) * radius;
          }
          hit = true;
        }
      }
    }
    return hit;
  }

  /** ダンジョン内で歩ける座標か */
  isOpenAt(x, z) {
    const [cx, cz] = this.toCell(x, z);
    return this.open(cx, cz);
  }

  /** 部屋の中のランダムな地点 */
  randomPointIn(room, rng) {
    const [wx, wz] = this.toWorld(room.cx, room.cz);
    return {
      x: wx + (rng() - 0.5) * (room.w - 1) * CELL,
      z: wz + (rng() - 0.5) * (room.h - 1) * CELL,
    };
  }
}

export { CELL as DUNGEON_CELL, CEIL as DUNGEON_CEIL };
