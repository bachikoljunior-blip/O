// dungeon.js — procedural interiors (caves, crypts, the dragon's lair).

import { makeRNG, clamp, lerp, TAU, makeCanvas, hash2, dist, roundRect, valueNoise2, mixRGB } from '../core/util.js';
import { TILE } from './worldgen.js';
import { getProp } from '../render/props.js';
import { drawShadow } from '../render/actors.js';

const W = 0, F = 1, DOOR = 2, EXIT = 3, HOLE = 4;

const THEMES = {
  cave: { floor: [92, 82, 72], floor2: [72, 64, 56], wall: [52, 46, 42], wallTop: [78, 70, 62], torch: '#ff9a3a', props: ['rock', 'crystal', 'mushroom'] },
  crypt: { floor: [104, 100, 92], floor2: [82, 80, 74], wall: [58, 56, 52], wallTop: [88, 86, 80], torch: '#8fe0ff', props: ['gravestone', 'ruinPillar', 'bone'] },
  ice: { floor: [176, 198, 216], floor2: [148, 172, 196], wall: [96, 122, 148], wallTop: [140, 170, 196], torch: '#9fe8ff', props: ['snowRock', 'crystal'] },
  ruin: { floor: [126, 118, 100], floor2: [104, 96, 82], wall: [74, 68, 58], wallTop: [104, 96, 84], torch: '#ffc46a', props: ['ruinPillar', 'rock', 'ruinWall'] },
  lair: { floor: [86, 58, 48], floor2: [64, 40, 34], wall: [46, 30, 26], wallTop: [72, 44, 36], torch: '#ff7a2e', props: ['ashRock', 'deadTree'] },
};

export class Dungeon {
  constructor(poi, floor, seed) {
    this.poi = poi;
    this.floor = floor;
    this.name = poi.name + (poi.floors > 1 ? ` ${floor + 1}層` : '');
    this.level = poi.level + floor * 2;
    this.boss = !!poi.boss || floor === (poi.floors - 1);
    const rng = makeRNG((seed ^ (floor * 7919)) >>> 0);
    this.rng = rng;
    this.themeKey = poi.boss ? 'lair'
      : poi.name.includes('氷') ? 'ice'
      : poi.name.includes('墓') ? 'crypt'
      : poi.name.includes('遺構') || poi.name.includes('回廊') ? 'ruin' : 'cave';
    this.theme = THEMES[this.themeKey];
    this.w = 44; this.h = 44;
    this.tiles = new Uint8Array(this.w * this.h);
    this.rooms = [];
    this.props = [];
    this.spawns = [];
    this.chests = [];
    this.generate(rng);
    this.bake = null;
  }

  at(x, y) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return W;
    return this.tiles[y * this.w + x];
  }
  set(x, y, v) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    this.tiles[y * this.w + x] = v;
  }

  generate(rng) {
    const roomCount = this.boss ? 7 : 9 + rng.int(4);
    // rooms
    for (let i = 0; i < roomCount * 6 && this.rooms.length < roomCount; i++) {
      const big = this.boss && this.rooms.length === roomCount - 1;
      const rw = big ? 16 : 5 + rng.int(7);
      const rh = big ? 14 : 5 + rng.int(6);
      const rx = 2 + rng.int(this.w - rw - 4);
      const ry = 2 + rng.int(this.h - rh - 4);
      let ok = true;
      for (const r of this.rooms) {
        if (rx < r.x + r.w + 2 && rx + rw + 2 > r.x && ry < r.y + r.h + 2 && ry + rh + 2 > r.y) { ok = false; break; }
      }
      if (!ok) continue;
      this.rooms.push({ x: rx, y: ry, w: rw, h: rh, big });
    }
    for (const r of this.rooms) {
      for (let y = r.y; y < r.y + r.h; y++)
        for (let x = r.x; x < r.x + r.w; x++) this.set(x, y, F);
    }
    // corridors (connect in order + a couple of loops)
    const centers = this.rooms.map((r) => ({ x: Math.floor(r.x + r.w / 2), y: Math.floor(r.y + r.h / 2) }));
    const carveH = (x1, x2, y) => { for (let x = Math.min(x1, x2); x <= Math.max(x1, x2); x++) { this.set(x, y, F); this.set(x, y + 1, F); } };
    const carveV = (y1, y2, x) => { for (let y = Math.min(y1, y2); y <= Math.max(y1, y2); y++) { this.set(x, y, F); this.set(x + 1, y, F); } };
    for (let i = 1; i < centers.length; i++) {
      const a = centers[i - 1], b = centers[i];
      if (rng.chance(0.5)) { carveH(a.x, b.x, a.y); carveV(a.y, b.y, b.x); }
      else { carveV(a.y, b.y, a.x); carveH(a.x, b.x, b.y); }
    }
    for (let k = 0; k < 2 && centers.length > 3; k++) {
      const a = centers[rng.int(centers.length)], b = centers[rng.int(centers.length)];
      carveH(a.x, b.x, a.y); carveV(a.y, b.y, b.x);
    }
    // rough the cave walls a bit
    if (this.themeKey === 'cave' || this.themeKey === 'lair') {
      const copy = this.tiles.slice();
      for (let y = 1; y < this.h - 1; y++) {
        for (let x = 1; x < this.w - 1; x++) {
          if (copy[y * this.w + x] === W) continue;
          if (hash2(x, y, 991) < 0.06) this.set(x, y, W);
        }
      }
      for (let y = 1; y < this.h - 1; y++) {
        for (let x = 1; x < this.w - 1; x++) {
          if (copy[y * this.w + x] !== W) continue;
          let n = 0;
          for (let oy = -1; oy <= 1; oy++) for (let ox = -1; ox <= 1; ox++) if (copy[(y + oy) * this.w + x + ox] === F) n++;
          if (n >= 5 && hash2(x, y, 313) < 0.5) this.set(x, y, F);
        }
      }
    }

    // entry & exit
    const first = this.rooms[0];
    this.entry = { x: (first.x + first.w / 2) * TILE, y: (first.y + first.h / 2) * TILE };
    const last = this.rooms[this.rooms.length - 1];
    this.exitTile = { x: Math.floor(last.x + last.w / 2), y: Math.floor(last.y + last.h / 2) };
    this.bossRoom = last;
    if (!this.boss) this.set(this.exitTile.x, this.exitTile.y, EXIT);

    // decorations & torches
    for (const r of this.rooms) {
      const cx = r.x + r.w / 2, cy = r.y + r.h / 2;
      // wall torches
      for (let k = 0; k < Math.max(2, Math.floor((r.w + r.h) / 6)); k++) {
        const side = rng.int(4);
        let tx, ty;
        if (side === 0) { tx = r.x + 1 + rng.int(r.w - 2); ty = r.y; }
        else if (side === 1) { tx = r.x + 1 + rng.int(r.w - 2); ty = r.y + r.h - 1; }
        else if (side === 2) { tx = r.x; ty = r.y + 1 + rng.int(r.h - 2); }
        else { tx = r.x + r.w - 1; ty = r.y + 1 + rng.int(r.h - 2); }
        this.props.push({ kind: 'torch', variant: 0, x: (tx + 0.5) * TILE, y: (ty + 0.9) * TILE, light: true, r: 0 });
      }
      // scatter props
      const n = Math.floor(r.w * r.h * 0.03);
      for (let k = 0; k < n; k++) {
        const kind = rng.pick(this.theme.props.filter((p) => p !== 'bone'));
        const x = (r.x + 1 + rng() * (r.w - 2)) * TILE;
        const y = (r.y + 1 + rng() * (r.h - 2)) * TILE;
        this.props.push({ kind, variant: rng.int(6), x, y, r: kind === 'ruinWall' ? 20 : 11, harvest: kind === 'crystal' ? 'crystal' : null, id: 'd' + k + '_' + (x | 0) });
      }
      // campfire in one room
      if (rng.chance(0.25)) this.props.push({ kind: 'campfire', variant: 0, x: cx * TILE, y: cy * TILE, light: true, fire: true, r: 10 });
      // chest
      if (rng.chance(r === last ? 1 : 0.34)) {
        this.chests.push({
          kind: 'chest', sprite: 'chest', x: (r.x + 1 + rng() * (r.w - 2)) * TILE,
          y: (r.y + 1 + rng() * (r.h - 2)) * TILE, opened: false, level: this.level + (r === last ? 4 : 0),
          id: 'dch_' + this.name + '_' + k(r), big: r === last,
        });
      }
      // enemy spawn points
      if (r !== first) {
        const count = r.big ? 1 : 1 + rng.int(3);
        for (let k2 = 0; k2 < count; k2++) {
          this.spawns.push({
            x: (r.x + 1 + rng() * (r.w - 2)) * TILE,
            y: (r.y + 1 + rng() * (r.h - 2)) * TILE,
            boss: r.big && this.boss,
          });
        }
      }
    }
    function k(r) { return r.x + '_' + r.y; }
  }

  blocked(x, y, r = 8) {
    const t = TILE;
    for (const [ox, oy] of [[-r, 0], [r, 0], [0, -r * 0.6], [0, r * 0.4], [-r * 0.7, r * 0.3], [r * 0.7, r * 0.3]]) {
      const tx = Math.floor((x + ox) / t), ty = Math.floor((y + oy) / t);
      if (this.at(tx, ty) === W) return true;
    }
    return false;
  }

  getBake() {
    if (this.bake) return this.bake;
    const { canvas, ctx } = makeCanvas(this.w * TILE, this.h * TILE);
    const th = this.theme;
    ctx.fillStyle = '#070706';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // floors
    for (let y = 0; y < this.h; y++) {
      for (let x = 0; x < this.w; x++) {
        const t = this.at(x, y);
        if (t === W) continue;
        // smooth, organic floor variation rather than per-tile two-tone
        const n = hash2(x, y, 17);
        const blend = clamp(valueNoise2(x * 0.22, y * 0.22, 31) * 1.25 - 0.12, 0, 1);
        const c = mixRGB(th.floor, th.floor2, blend);
        const k = 0.96 + n * 0.08;
        ctx.fillStyle = `rgb(${(c[0] * k) | 0},${(c[1] * k) | 0},${(c[2] * k) | 0})`;
        ctx.fillRect(x * TILE, y * TILE, TILE, TILE);
        // cracks / tiles
        if (this.themeKey === 'crypt' || this.themeKey === 'ruin') {
          ctx.strokeStyle = 'rgba(0,0,0,0.22)';
          ctx.lineWidth = 1;
          ctx.strokeRect(x * TILE + 0.5, y * TILE + 0.5, TILE - 1, TILE - 1);
        } else if (n > 0.86) {
          ctx.fillStyle = 'rgba(0,0,0,0.16)';
          ctx.beginPath();
          ctx.ellipse(x * TILE + 8 + n * 12, y * TILE + 10 + n * 10, 5 + n * 4, 3 + n * 2, n * 3, 0, TAU);
          ctx.fill();
        }
        // ambient occlusion against neighbouring walls
        const aoN = this.at(x, y - 1) === W, aoS = this.at(x, y + 1) === W;
        const aoW = this.at(x - 1, y) === W, aoE = this.at(x + 1, y) === W;
        if (aoN || aoS || aoW || aoE) {
          const px = x * TILE, py = y * TILE;
          const shade = (gx0, gy0, gx1, gy1) => {
            const gr = ctx.createLinearGradient(gx0, gy0, gx1, gy1);
            gr.addColorStop(0, 'rgba(0,0,0,0.42)');
            gr.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.fillStyle = gr;
            ctx.fillRect(px, py, TILE, TILE);
          };
          if (aoN) shade(0, py, 0, py + 14);
          if (aoS) shade(0, py + TILE, 0, py + TILE - 14);
          if (aoW) shade(px, 0, px + 14, 0);
          if (aoE) shade(px + TILE, 0, px + TILE - 14, 0);
        }
        if (t === EXIT) {
          ctx.fillStyle = 'rgba(20,18,16,0.9)';
          ctx.fillRect(x * TILE + 2, y * TILE + 2, TILE - 4, TILE - 4);
          for (let s = 0; s < 4; s++) {
            ctx.fillStyle = `rgba(${140 - s * 20},${132 - s * 20},${120 - s * 18},1)`;
            ctx.fillRect(x * TILE + 3, y * TILE + 4 + s * 7, TILE - 6, 5);
          }
        }
      }
    }
    // walls
    for (let y = 0; y < this.h; y++) {
      for (let x = 0; x < this.w; x++) {
        if (this.at(x, y) !== W) continue;
        const openBelow = this.at(x, y + 1) !== W;
        const n = hash2(x, y, 31);
        const top = th.wallTop, wall = th.wall;
        const k = 0.85 + n * 0.3;
        ctx.fillStyle = `rgb(${(wall[0] * k) | 0},${(wall[1] * k) | 0},${(wall[2] * k) | 0})`;
        ctx.fillRect(x * TILE, y * TILE, TILE, TILE);
        if (openBelow) {
          // lit wall face
          const gr = ctx.createLinearGradient(0, y * TILE, 0, y * TILE + TILE);
          gr.addColorStop(0, `rgb(${top[0] | 0},${top[1] | 0},${top[2] | 0})`);
          gr.addColorStop(1, `rgb(${(wall[0] * 0.7) | 0},${(wall[1] * 0.7) | 0},${(wall[2] * 0.7) | 0})`);
          ctx.fillStyle = gr;
          ctx.fillRect(x * TILE, y * TILE + 8, TILE, TILE - 8);
          ctx.fillStyle = 'rgba(0,0,0,0.35)';
          ctx.fillRect(x * TILE, y * TILE + TILE - 4, TILE, 4);
          // brick lines
          if (this.themeKey !== 'cave' && this.themeKey !== 'lair') {
            ctx.strokeStyle = 'rgba(0,0,0,0.25)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x * TILE, y * TILE + 20); ctx.lineTo(x * TILE + TILE, y * TILE + 20);
            ctx.moveTo(x * TILE + (n > 0.5 ? 16 : 8), y * TILE + 8); ctx.lineTo(x * TILE + (n > 0.5 ? 16 : 8), y * TILE + 20);
            ctx.stroke();
          }
        }
      }
    }
    this.bake = canvas;
    return canvas;
  }

  drawFloor(ctx, view) {
    ctx.drawImage(this.getBake(), 0, 0);
  }

  collectProps(list, view, g) {
    for (const p of this.props) {
      if (p.x < view.x0 - 100 || p.x > view.x1 + 100 || p.y < view.y0 - 160 || p.y > view.y1 + 100) continue;
      if (p.harvest && g.runtime.isHarvested(p, g.time)) continue;
      list.push({ y: p.y, kind: 'prop', o: p });
    }
  }

  collectLights(add, view, g) {
    for (const p of this.props) {
      if (!p.light) continue;
      if (p.x < view.x0 - 200 || p.x > view.x1 + 200 || p.y < view.y0 - 200 || p.y > view.y1 + 200) continue;
      const fl = 0.86 + Math.sin(g.time * 8 + p.x) * 0.08 + Math.sin(g.time * 17 + p.y) * 0.06;
      add(p.x, p.y - (p.fire ? 6 : 40), (p.fire ? 150 : 120) * fl, [255, 170, 80], 0.85);
    }
  }
}
