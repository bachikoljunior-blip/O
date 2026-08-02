// buildings.js — 2.5D procedural buildings baked into cached canvases.

import { makeCanvas, makeRNG, TAU, clamp, roundRect, lerp } from '../core/util.js';
import { TILE } from '../world/worldgen.js';

const cache = new Map();

const PALETTES = {
  house: [
    { wall: [226, 214, 190], beam: [92, 66, 42], roof: [148, 74, 58] },
    { wall: [214, 200, 176], beam: [72, 52, 36], roof: [96, 92, 104] },
    { wall: [206, 190, 158], beam: [104, 74, 44], roof: [122, 96, 62] },
    { wall: [230, 222, 206], beam: [60, 48, 40], roof: [78, 100, 96] },
  ],
  inn: [{ wall: [222, 198, 156], beam: [96, 62, 34], roof: [162, 96, 54] }],
  shop: [{ wall: [214, 206, 186], beam: [80, 58, 40], roof: [104, 122, 96] }],
  smith: [{ wall: [176, 168, 158], beam: [58, 48, 42], roof: [86, 80, 76] }],
  alchemist: [{ wall: [206, 196, 214], beam: [72, 56, 78], roof: [96, 72, 128] }],
  temple: [{ wall: [238, 232, 214], beam: [176, 152, 96], roof: [212, 196, 140] }],
  guild: [{ wall: [206, 192, 172], beam: [70, 54, 40], roof: [120, 82, 60] }],
  castle: [{ wall: [188, 184, 176], beam: [110, 106, 100], roof: [86, 74, 108] }],
};

export const BUILDING_LABEL = {
  house: '民家', inn: '宿屋', shop: '道具屋', smith: '鍛冶屋',
  alchemist: '錬金術師', temple: '神殿', guild: '冒険者ギルド', castle: '城',
};

function shingles(ctx, x, y, w, h, base, rnd, shape = 'tile') {
  const rowH = shape === 'thatch' ? 13 : 10;
  const rows = Math.max(3, Math.round(h / rowH));
  const rh = h / rows;
  const cw = shape === 'thatch' ? 30 : 16;
  const tone = (k) => `rgb(${clamp(base[0] * k, 0, 255) | 0},${clamp(base[1] * k, 0, 255) | 0},${clamp(base[2] * k, 0, 255) | 0})`;
  for (let r = 0; r < rows; r++) {
    const ry = y + r * rh;
    const k = 0.66 + (r / rows) * 0.58;      // darker at the ridge, lighter at the eaves
    ctx.fillStyle = tone(k);
    ctx.fillRect(x, ry, w, rh + 1);
    const offset = (r % 2) * cw * 0.5;
    for (let cx2 = x - offset; cx2 < x + w; cx2 += cw) {
      const j = 0.90 + rnd() * 0.19;
      ctx.fillStyle = tone(k * j);
      if (shape === 'thatch') {
        ctx.beginPath();
        ctx.moveTo(cx2, ry);
        ctx.lineTo(cx2 + cw - 1, ry);
        ctx.quadraticCurveTo(cx2 + cw / 2, ry + rh * 1.5, cx2, ry + rh);
        ctx.closePath();
        ctx.fill();
      } else {
        roundRect(ctx, cx2 + 0.8, ry + 0.6, cw - 1.6, rh * 1.25, 3.2);
        ctx.fill();
      }
    }
    // course shadow
    ctx.fillStyle = 'rgba(0,0,0,0.26)';
    ctx.fillRect(x, ry + rh - 1.6, w, 1.8);
    ctx.fillStyle = 'rgba(255,250,235,0.10)';
    ctx.fillRect(x, ry, w, 1);
  }
}

function window_(ctx, x, y, w, h, lit) {
  ctx.fillStyle = '#3a3026';
  roundRect(ctx, x - 1, y - 1, w + 2, h + 2, 2); ctx.fill();
  ctx.fillStyle = lit ? '#ffd98a' : '#2c3a48';
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = 'rgba(58,48,38,0.9)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x + w / 2, y); ctx.lineTo(x + w / 2, y + h);
  ctx.moveTo(x, y + h / 2); ctx.lineTo(x + w, y + h / 2);
  ctx.stroke();
  if (lit) {
    ctx.fillStyle = 'rgba(255,214,130,0.22)';
    ctx.fillRect(x - 3, y - 3, w + 6, h + 6);
  }
}

function shopSign(ctx, x, y, type) {
  ctx.save();
  ctx.strokeStyle = '#3a3026'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(x, y - 12); ctx.lineTo(x, y); ctx.stroke();
  ctx.fillStyle = '#6b5636';
  roundRect(ctx, x - 13, y, 26, 18, 3); ctx.fill();
  ctx.translate(x, y + 9);
  ctx.lineWidth = 2; ctx.lineCap = 'round';
  switch (type) {
    case 'inn':
      ctx.fillStyle = '#e8c874';
      ctx.beginPath(); ctx.ellipse(0, 2, 7, 4, 0, Math.PI, TAU); ctx.fill();
      ctx.fillRect(-7, 2, 14, 2);
      break;
    case 'smith':
      ctx.fillStyle = '#d0d4da';
      ctx.beginPath(); ctx.moveTo(-7, 2); ctx.lineTo(1, 2); ctx.lineTo(6, -3); ctx.lineTo(2, -6); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = '#8a6238'; ctx.beginPath(); ctx.moveTo(-7, 3); ctx.lineTo(-2, -4); ctx.stroke();
      break;
    case 'alchemist':
      ctx.fillStyle = '#8fe0a0';
      ctx.beginPath(); ctx.moveTo(-3, -6); ctx.lineTo(3, -6); ctx.lineTo(6, 5); ctx.lineTo(-6, 5); ctx.closePath(); ctx.fill();
      break;
    case 'guild':
      ctx.fillStyle = '#e0c060';
      ctx.beginPath();
      for (let i = 0; i < 5; i++) {
        const a = -Math.PI / 2 + (i / 5) * TAU;
        const r1 = 7, r2 = 3.2;
        ctx[i ? 'lineTo' : 'moveTo'](Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a + TAU / 10) * r2, Math.sin(a + TAU / 10) * r2);
      }
      ctx.closePath(); ctx.fill();
      break;
    default:
      ctx.fillStyle = '#d8b878';
      ctx.beginPath(); ctx.arc(0, 0, 6, 0, TAU); ctx.fill();
      ctx.fillStyle = '#6b5636';
      ctx.beginPath(); ctx.arc(0, 0, 2.4, 0, TAU); ctx.fill();
  }
  ctx.restore();
}

function build(type, tw, th, style, lit) {
  const rnd = makeRNG(tw * 7919 + th * 104729 + style * 31 + type.length * 17);
  const w = tw * TILE, h = th * TILE;
  const pal = (PALETTES[type] || PALETTES.house)[style % (PALETTES[type] || PALETTES.house).length];
  const OVER = 14;              // roof overhang
  const RISE = type === 'castle' ? 96 : type === 'temple' ? 74 : 44 + th * 4; // roof height above plan
  const facadeH = Math.min(h * 0.5, 46);
  const W = w + OVER * 2, H = h + RISE + 8;
  const { canvas, ctx } = makeCanvas(W, H);
  const ox = W / 2, oy = H;           // anchor: bottom centre of footprint
  const planTop = RISE + 8;           // y of footprint top edge in sprite space
  const left = OVER, right = OVER + w;
  const wallTop = planTop + h - facadeH;

  // ——— facade
  const wg = ctx.createLinearGradient(0, wallTop, 0, H);
  wg.addColorStop(0, `rgb(${pal.wall[0]},${pal.wall[1]},${pal.wall[2]})`);
  wg.addColorStop(1, `rgb(${pal.wall[0] * 0.74 | 0},${pal.wall[1] * 0.74 | 0},${pal.wall[2] * 0.74 | 0})`);
  ctx.fillStyle = wg;
  ctx.fillRect(left, wallTop, w, facadeH);
  // side walls hint (darker strips)
  ctx.fillStyle = 'rgba(0,0,0,0.18)';
  ctx.fillRect(left, wallTop, 4, facadeH);
  ctx.fillRect(right - 4, wallTop, 4, facadeH);

  if (type === 'castle') {
    // stone blocks
    for (let y = wallTop; y < H - 2; y += 9) {
      for (let x = left; x < right; x += 16) {
        const j = 0.9 + rnd() * 0.2;
        ctx.fillStyle = `rgba(${pal.wall[0] * j | 0},${pal.wall[1] * j | 0},${pal.wall[2] * j | 0},1)`;
        ctx.fillRect(x + ((y / 9) % 2 ? 8 : 0), y, 15, 8);
      }
    }
  } else if (type === 'smith' || type === 'temple') {
    for (let y = wallTop; y < H - 2; y += 11) {
      ctx.fillStyle = 'rgba(0,0,0,0.06)';
      ctx.fillRect(left, y, w, 1.5);
    }
  } else {
    // half-timbering
    ctx.fillStyle = `rgb(${pal.beam[0]},${pal.beam[1]},${pal.beam[2]})`;
    const bw = 5;
    ctx.fillRect(left, wallTop, w, bw);
    ctx.fillRect(left, H - bw - 2, w, bw);
    const posts = Math.max(2, Math.round(w / 44));
    for (let i = 0; i <= posts; i++) {
      const x = left + (i / posts) * (w - bw);
      ctx.fillRect(x, wallTop, bw, facadeH);
    }
    if (style % 2) {
      ctx.save();
      ctx.globalAlpha = 0.85;
      for (let i = 0; i < posts; i++) {
        const x0 = left + (i / posts) * w, x1 = left + ((i + 1) / posts) * w;
        ctx.strokeStyle = `rgb(${pal.beam[0]},${pal.beam[1]},${pal.beam[2]})`;
        ctx.lineWidth = 4;
        ctx.beginPath(); ctx.moveTo(x0 + 4, H - 6); ctx.lineTo((x0 + x1) / 2, wallTop + 6); ctx.lineTo(x1 - 4, H - 6); ctx.stroke();
      }
      ctx.restore();
    }
  }

  // ——— door + windows
  const doorW = 22, doorH = Math.min(facadeH - 8, 34);
  const doorX = ox - doorW / 2 + (rnd() - 0.5) * (w * 0.2);
  const doorY = H - doorH - 2;
  ctx.fillStyle = '#4a3722';
  roundRect(ctx, doorX - 2, doorY - 2, doorW + 4, doorH + 2, 4); ctx.fill();
  const dg = ctx.createLinearGradient(doorX, 0, doorX + doorW, 0);
  dg.addColorStop(0, '#6b4a2a'); dg.addColorStop(0.5, '#8a6238'); dg.addColorStop(1, '#5c3f22');
  ctx.fillStyle = dg;
  roundRect(ctx, doorX, doorY, doorW, doorH, 3); ctx.fill();
  ctx.fillStyle = '#d8b04a';
  ctx.beginPath(); ctx.arc(doorX + doorW - 5, doorY + doorH / 2, 2, 0, TAU); ctx.fill();

  const winY = wallTop + 10;
  const winH = Math.min(16, facadeH - 20);
  if (winH > 6) {
    const slots = Math.max(1, Math.floor(w / 46));
    for (let i = 0; i < slots; i++) {
      const x = left + 10 + (i + 0.5) * ((w - 20) / slots) - 9;
      if (Math.abs(x + 9 - (doorX + doorW / 2)) < 22) continue;
      window_(ctx, x, winY, 18, winH, lit);
    }
  }

  // ——— roof
  ctx.save();
  const roofBottom = wallTop + 4;
  const ridgeY = planTop - RISE + 16;
  const gable = type !== 'castle';
  if (gable) {
    // two slopes: left/right of ridge (ridge runs horizontally along the top)
    ctx.beginPath();
    ctx.moveTo(left - OVER, roofBottom);
    ctx.lineTo(right + OVER, roofBottom);
    ctx.lineTo(right + OVER - 6, ridgeY);
    ctx.lineTo(left - OVER + 6, ridgeY);
    ctx.closePath();
    ctx.save(); ctx.clip();
    shingles(ctx, left - OVER, ridgeY, w + OVER * 2, roofBottom - ridgeY, pal.roof, rnd, type === 'house' && style === 2 ? 'thatch' : 'tile');
    // sun from the upper-left
    const rg = ctx.createLinearGradient(0, ridgeY, 0, roofBottom);
    rg.addColorStop(0, 'rgba(255,246,220,0.20)');
    rg.addColorStop(0.55, 'rgba(0,0,0,0)');
    rg.addColorStop(1, 'rgba(0,0,0,0.22)');
    ctx.fillStyle = rg;
    ctx.fillRect(left - OVER, ridgeY, w + OVER * 2, roofBottom - ridgeY);
    ctx.restore();
    // ridge cap
    ctx.fillStyle = `rgb(${pal.roof[0] * 0.6 | 0},${pal.roof[1] * 0.6 | 0},${pal.roof[2] * 0.6 | 0})`;
    ctx.fillRect(left - OVER + 6, ridgeY - 3, w + OVER * 2 - 12, 5);
    // eave shadow on wall
    ctx.fillStyle = 'rgba(0,0,0,0.28)';
    ctx.fillRect(left, roofBottom, w, 6);
  } else {
    // castle: crenellated block + towers
    ctx.fillStyle = `rgb(${pal.wall[0] * 0.9 | 0},${pal.wall[1] * 0.9 | 0},${pal.wall[2] * 0.9 | 0})`;
    ctx.fillRect(left - 4, ridgeY, w + 8, roofBottom - ridgeY);
    ctx.fillStyle = 'rgba(0,0,0,0.20)';
    for (let y = ridgeY; y < roofBottom; y += 10) ctx.fillRect(left - 4, y, w + 8, 1.5);
    for (let x = left - 4; x < right + 4; x += 18) {
      ctx.fillStyle = `rgb(${pal.wall[0] | 0},${pal.wall[1] | 0},${pal.wall[2] | 0})`;
      ctx.fillRect(x, ridgeY - 10, 11, 12);
    }
    // corner towers with conical roofs
    for (const tx of [left - 10, right - 12]) {
      ctx.fillStyle = `rgb(${pal.wall[0] * 0.96 | 0},${pal.wall[1] * 0.96 | 0},${pal.wall[2] * 0.96 | 0})`;
      ctx.fillRect(tx, ridgeY - 34, 24, roofBottom - ridgeY + 34);
      ctx.fillStyle = `rgb(${pal.roof[0]},${pal.roof[1]},${pal.roof[2]})`;
      ctx.beginPath();
      ctx.moveTo(tx - 6, ridgeY - 34); ctx.lineTo(tx + 12, ridgeY - 70); ctx.lineTo(tx + 30, ridgeY - 34);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(0,0,0,0.2)';
      ctx.beginPath();
      ctx.moveTo(tx + 12, ridgeY - 70); ctx.lineTo(tx + 30, ridgeY - 34); ctx.lineTo(tx + 12, ridgeY - 34);
      ctx.closePath(); ctx.fill();
    }
  }
  ctx.restore();

  // chimney
  if (type !== 'castle' && type !== 'temple' && th >= 4) {
    const cx2 = left + w * (0.24 + rnd() * 0.5);
    ctx.fillStyle = '#6e5a4a';
    ctx.fillRect(cx2, ridgeY - 22, 14, 26);
    ctx.fillStyle = '#5a4838';
    ctx.fillRect(cx2 - 2, ridgeY - 24, 18, 5);
  }
  // temple dome
  if (type === 'temple') {
    ctx.fillStyle = '#e8dcae';
    ctx.beginPath(); ctx.arc(ox, ridgeY, w * 0.26, Math.PI, TAU); ctx.fill();
    ctx.fillStyle = '#c9b878';
    ctx.fillRect(ox - 2, ridgeY - w * 0.26 - 14, 4, 16);
  }
  // shop sign
  if (type !== 'house' && type !== 'castle') {
    shopSign(ctx, left + w - 16, wallTop + 8, type);
  }

  return { canvas, ox, oy, w: W, h: H, planTop, facadeH, chimney: type !== 'castle' && th >= 4 };
}

export function getBuilding(b, lit) {
  const key = `${b.type}_${b.tw}_${b.th}_${b.style}_${lit ? 1 : 0}`;
  let s = cache.get(key);
  if (!s) { s = build(b.type, b.tw, b.th, b.style, lit); cache.set(key, s); }
  return s;
}
