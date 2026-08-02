// tiles.js — terrain palette, chunk baking (blended colour + hillshade + detail marks).

import { B, TILE, SEA } from '../world/worldgen.js';
import { hash2, clamp, lerp, css, mixRGB, TAU, makeCanvas, valueNoise2 } from '../core/util.js';

export const CHUNK = 16; // tiles per chunk side
export const CHUNK_PX = CHUNK * TILE;

export const BIOME_COLOR = {
  [B.DEEP]: [16, 38, 74],
  [B.OCEAN]: [26, 66, 112],
  [B.SHALLOW]: [64, 132, 158],
  [B.BEACH]: [226, 210, 162],
  [B.GRASS]: [104, 150, 74],
  [B.MEADOW]: [126, 168, 84],
  [B.FOREST]: [74, 118, 62],
  [B.DARKWOOD]: [48, 82, 60],
  [B.TAIGA]: [66, 100, 82],
  [B.SNOW]: [232, 240, 248],
  [B.TUNDRA]: [152, 158, 138],
  [B.DESERT]: [226, 200, 138],
  [B.SAVANNA]: [180, 170, 100],
  [B.SWAMP]: [82, 102, 72],
  [B.ROCK]: [128, 122, 114],
  [B.PEAK]: [242, 246, 252],
  [B.ASH]: [78, 66, 62],
  [B.FARM]: [138, 110, 72],
  [B.ROAD]: [156, 134, 100],
  [B.RIVER]: [58, 118, 152],
  [B.FLOOR]: [170, 162, 148],
};

const OVERLAY_COLOR = {
  1: [158, 136, 102],   // road
  3: [142, 112, 74],    // farm
  4: [172, 164, 150],   // town floor
  5: [126, 96, 64],     // bridge
  6: [190, 182, 166],   // plaza
};

/** Base colour for a tile including per-tile variation, overlays and hillshade. */
export function tileColor(world, x, y) {
  const i = y * world.w + x;
  const b = world.biome[i];
  const ov = world.overlay[i];
  let c = OVERLAY_COLOR[ov] || BIOME_COLOR[b] || [120, 120, 120];
  const e = world.height[i];

  // per-tile hue/value variation
  const n = hash2(x, y, world.seed) - 0.5;
  const n2 = valueNoise2(x * 0.14, y * 0.14, world.seed + 3) - 0.5;
  let v = 1 + n * 0.10 + n2 * 0.16;

  if (b === B.DEEP || b === B.OCEAN || b === B.SHALLOW || b === B.RIVER) {
    // water depth shading
    const depth = clamp((SEA - e) * 6, 0, 1);
    c = mixRGB([86, 168, 180], c, clamp(depth * 1.4, 0, 1));
    v = 1 + n2 * 0.06;
  } else if (!ov) {
    // altitude tinting: higher = paler & cooler
    const alt = clamp((e - SEA) * 1.5, 0, 1);
    c = mixRGB(c, [206, 212, 216], alt * 0.16);
  }

  // hillshade — light from the north-west
  const hl = world.heightAt(x - 1, y), hr = world.heightAt(x + 1, y);
  const hu = world.heightAt(x, y - 1), hd = world.heightAt(x, y + 1);
  const gx = (hr - hl) * 26, gy = (hd - hu) * 26;
  let shade = 1 + (-gx * 0.62 - gy * 0.62);
  // ambient occlusion in dips
  const avg = (hl + hr + hu + hd) / 4;
  shade -= clamp((avg - e) * 9, 0, 0.35);
  shade = clamp(shade, 0.42, 1.55);

  const k = v * shade;
  return [clamp(c[0] * k, 0, 255), clamp(c[1] * k, 0, 255), clamp(c[2] * k, 0, 255)];
}

// ————————————————————————————————————————————————— detail marks

function grassTuft(ctx, x, y, s, col, sway) {
  ctx.strokeStyle = col;
  ctx.lineWidth = 1.1;
  ctx.beginPath();
  for (let i = -1; i <= 1; i++) {
    ctx.moveTo(x + i * 1.9, y);
    ctx.quadraticCurveTo(x + i * 2.4 + sway * 0.5, y - s * 0.6, x + i * 3.1 + sway, y - s);
  }
  ctx.stroke();
}

function pebble(ctx, x, y, r, col) {
  ctx.fillStyle = col;
  ctx.beginPath();
  ctx.ellipse(x, y, r, r * 0.72, 0, 0, TAU);
  ctx.fill();
}

/** Draws small decorative marks for one tile onto the chunk canvas. */
function tileDetail(ctx, world, x, y, px, py) {
  const i = y * world.w + x;
  const b = world.biome[i];
  const ov = world.overlay[i];
  const r1 = hash2(x, y, world.seed + 11);
  const r2 = hash2(x, y, world.seed + 29);
  const r3 = hash2(x, y, world.seed + 53);
  const ox = px + r2 * TILE, oy = py + r3 * TILE;

  if (ov === 1 || ov === 5) {
    // road gravel & wheel ruts
    if (r1 < 0.55) pebble(ctx, ox, oy, 1 + r2 * 1.8, `rgba(${110 + r1 * 40},${96 + r2 * 30},${72 + r3 * 26},0.55)`);
    if (r1 > 0.9) pebble(ctx, ox + 6, oy + 4, 1.4, 'rgba(90,78,60,0.4)');
    if (r2 > 0.72) {
      ctx.strokeStyle = 'rgba(126,104,74,0.30)';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(px, py + 8 + r3 * 14);
      ctx.quadraticCurveTo(px + TILE / 2, py + 6 + r1 * 18, px + TILE, py + 8 + r2 * 14);
      ctx.stroke();
    }
    return;
  }
  if (ov === 3) {
    // ploughed furrows
    ctx.strokeStyle = 'rgba(96,72,44,0.45)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let k = 0; k < 4; k++) {
      ctx.moveTo(px, py + k * 8 + 4);
      ctx.lineTo(px + TILE, py + k * 8 + 4);
    }
    ctx.stroke();
    if (r1 > 0.5) {
      ctx.fillStyle = `hsl(${90 + r2 * 30} 45% ${34 + r3 * 12}%)`;
      for (let k = 0; k < 3; k++) {
        ctx.beginPath();
        ctx.ellipse(px + 6 + k * 10, py + 6 + (k % 2) * 16, 3.2, 2.2, 0, 0, TAU);
        ctx.fill();
      }
    }
    return;
  }
  if (ov === 4 || ov === 6) {
    // cobblestones — four irregular stones per tile
    for (let k = 0; k < 4; k++) {
      const hx = hash2(x * 3 + k, y * 5 - k, world.seed + 61 + k * 13);
      const hy = hash2(x * 7 - k, y * 11 + k, world.seed + 89 + k * 17);
      const sx = px + (k % 2) * 16 + 4 + hx * 5;
      const sy = py + ((k / 2) | 0) * 16 + 4 + hy * 5;
      if (hx < 0.18) continue;                       // gaps keep it from looking woven
      const l = 116 + hx * 30;
      ctx.fillStyle = `rgba(${l},${l - 6},${l - 18},0.24)`;
      ctx.beginPath();
      ctx.ellipse(sx + hy * 3 - 1.5, sy + hx * 3 - 1.5, 4.6 + hy * 2.6, 3.6 + hx * 2.2, hx * 3, 0, TAU);
      ctx.fill();
      ctx.fillStyle = `rgba(255,250,236,0.055)`;
      ctx.beginPath();
      ctx.ellipse(sx - 1 + hy * 3, sy - 1.4 + hx * 3, 3 + hy, 2, hx * 3, 0, TAU);
      ctx.fill();
    }
    if (r1 > 0.94) {
      ctx.fillStyle = 'rgba(96,120,72,0.30)';
      ctx.beginPath(); ctx.ellipse(ox, oy, 4, 2.4, r2 * 3, 0, TAU); ctx.fill();
    }
    return;
  }

  switch (b) {
    case B.GRASS:
    case B.MEADOW: {
      if (r1 < 0.75) grassTuft(ctx, ox, oy + 6, 6 + r2 * 5, `rgba(${58 + r2 * 40},${104 + r3 * 50},${44 + r1 * 30},0.7)`, (r3 - 0.5) * 3);
      if (b === B.MEADOW && r1 > 0.66) {
        const hue = [350, 48, 280, 200, 20][(r2 * 5) | 0];
        ctx.fillStyle = `hsl(${hue} 78% 68%)`;
        for (let k = 0; k < 3; k++) {
          const a = k * 2.1 + r3 * 6;
          ctx.beginPath();
          ctx.arc(ox + Math.cos(a) * 5, oy + Math.sin(a) * 5, 1.7, 0, TAU);
          ctx.fill();
        }
      }
      break;
    }
    case B.FOREST:
    case B.DARKWOOD:
    case B.TAIGA: {
      if (r1 < 0.6) grassTuft(ctx, ox, oy + 5, 5 + r2 * 4, `rgba(${40 + r2 * 26},${80 + r3 * 40},${40 + r1 * 24},0.6)`, (r3 - 0.5) * 2);
      if (r1 > 0.86) pebble(ctx, ox, oy, 2 + r2 * 2, 'rgba(60,52,40,0.35)');
      break;
    }
    case B.BEACH:
    case B.DESERT: {
      ctx.strokeStyle = 'rgba(190,168,116,0.4)';
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(px + 2, oy);
      ctx.quadraticCurveTo(px + TILE / 2, oy - 4 + r1 * 8, px + TILE - 2, oy);
      ctx.stroke();
      if (r1 > 0.9) pebble(ctx, ox, oy + 8, 1.6, 'rgba(150,132,92,0.5)');
      break;
    }
    case B.SAVANNA: {
      if (r1 < 0.7) grassTuft(ctx, ox, oy + 6, 7 + r2 * 5, `rgba(${150 + r2 * 40},${140 + r3 * 30},${70 + r1 * 30},0.65)`, (r3 - 0.5) * 3);
      break;
    }
    case B.SWAMP: {
      if (r1 < 0.4) {
        ctx.fillStyle = 'rgba(48,72,52,0.5)';
        ctx.beginPath();
        ctx.ellipse(ox, oy, 7 + r2 * 5, 4 + r3 * 3, r1 * 3, 0, TAU);
        ctx.fill();
      }
      if (r1 > 0.7) grassTuft(ctx, ox, oy + 6, 9 + r2 * 6, 'rgba(78,96,58,0.7)', (r3 - 0.5) * 4);
      break;
    }
    case B.SNOW:
    case B.PEAK: {
      if (r1 > 0.7) {
        ctx.fillStyle = 'rgba(255,255,255,0.8)';
        ctx.beginPath();
        ctx.ellipse(ox, oy, 4 + r2 * 5, 2.6 + r3 * 2, 0, 0, TAU);
        ctx.fill();
      }
      if (r1 < 0.12) pebble(ctx, ox, oy, 2.4, 'rgba(150,158,170,0.55)');
      break;
    }
    case B.TUNDRA: {
      if (r1 < 0.5) pebble(ctx, ox, oy, 1.6 + r2 * 2, 'rgba(120,124,110,0.45)');
      if (r1 > 0.8) grassTuft(ctx, ox, oy + 4, 4, 'rgba(126,132,104,0.6)', 0);
      break;
    }
    case B.ROCK: {
      if (r1 < 0.65) {
        ctx.fillStyle = `rgba(${96 + r2 * 44},${92 + r2 * 40},${86 + r2 * 38},0.55)`;
        ctx.beginPath();
        const n = 5;
        for (let k = 0; k < n; k++) {
          const a = (k / n) * TAU;
          const rr = 4 + hash2(x * 7 + k, y * 13, world.seed) * 7;
          ctx[k ? 'lineTo' : 'moveTo'](ox + Math.cos(a) * rr, oy + Math.sin(a) * rr * 0.7);
        }
        ctx.closePath();
        ctx.fill();
      }
      break;
    }
    case B.ASH: {
      if (r1 < 0.5) pebble(ctx, ox, oy, 2 + r2 * 3, 'rgba(40,32,30,0.6)');
      if (r1 > 0.92) {
        ctx.fillStyle = 'rgba(220,90,30,0.5)';
        ctx.beginPath();
        ctx.ellipse(ox, oy, 3 + r2 * 4, 2, 0, 0, TAU);
        ctx.fill();
      }
      break;
    }
  }
}

/**
 * Bakes one terrain chunk to an offscreen canvas.
 * Colour is painted at 1px/tile then upscaled with smoothing for organic blending,
 * after which crisp detail marks are stamped on top.
 */
export function bakeChunk(world, cx, cy) {
  const pad = 1;
  const n = CHUNK + pad * 2;
  const small = makeCanvas(n, n);
  const img = small.ctx.createImageData(n, n);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const tx = cx * CHUNK + x - pad;
      const ty = cy * CHUNK + y - pad;
      let c;
      if (!world.inBounds(tx, ty)) c = BIOME_COLOR[B.DEEP];
      else c = tileColor(world, tx, ty);
      const o = (y * n + x) * 4;
      img.data[o] = c[0]; img.data[o + 1] = c[1]; img.data[o + 2] = c[2]; img.data[o + 3] = 255;
    }
  }
  small.ctx.putImageData(img, 0, 0);

  const out = makeCanvas(CHUNK_PX, CHUNK_PX);
  const ctx = out.ctx;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(small.canvas, 0, 0, n, n, -pad * TILE, -pad * TILE, n * TILE, n * TILE);

  // detail pass
  ctx.save();
  for (let y = 0; y < CHUNK; y++) {
    for (let x = 0; x < CHUNK; x++) {
      const tx = cx * CHUNK + x, ty = cy * CHUNK + y;
      if (!world.inBounds(tx, ty)) continue;
      const b = world.biome[ty * world.w + tx];
      if (b === B.DEEP || b === B.OCEAN || b === B.SHALLOW || b === B.RIVER) continue;
      tileDetail(ctx, world, tx, ty, x * TILE, y * TILE);
    }
  }
  ctx.restore();

  // cliff edges: dark rims where height drops sharply
  ctx.save();
  ctx.lineCap = 'round';
  for (let y = 0; y < CHUNK; y++) {
    for (let x = 0; x < CHUNK; x++) {
      const tx = cx * CHUNK + x, ty = cy * CHUNK + y;
      if (!world.inBounds(tx, ty)) continue;
      const e = world.heightAt(tx, ty);
      const below = world.heightAt(tx, ty + 1);
      if (e - below > 0.035 && !world.isWaterTile(tx, ty)) {
        const k = clamp((e - below) * 9, 0, 1);
        ctx.fillStyle = `rgba(28,22,18,${0.10 + k * 0.30})`;
        ctx.fillRect(x * TILE, y * TILE + TILE - 5, TILE, 6);
        ctx.fillStyle = `rgba(255,250,235,${0.06 + k * 0.14})`;
        ctx.fillRect(x * TILE, y * TILE, TILE, 3);
      }
    }
  }
  ctx.restore();

  return out.canvas;
}

/** Colour used by the world map screen (no hillshade, flat & readable). */
export function mapColor(world, x, y) {
  const i = y * world.w + x;
  const ov = world.overlay[i];
  if (ov === 1 || ov === 5) return [150, 126, 92];
  if (ov === 4 || ov === 6) return [196, 186, 168];
  if (ov === 3) return [150, 122, 78];
  const c = BIOME_COLOR[world.biome[i]] || [100, 100, 100];
  const e = world.height[i];
  const k = world.biome[i] === B.DEEP || world.biome[i] === B.OCEAN ? 1 : 0.86 + clamp((e - SEA), 0, 0.5) * 0.7;
  return [clamp(c[0] * k, 0, 255), clamp(c[1] * k, 0, 255), clamp(c[2] * k, 0, 255)];
}
