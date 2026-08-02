// props.js — procedurally painted world props (trees, rocks, ruins, town objects).
// Every sprite is drawn once into a cached canvas keyed by kind+variant.

import { makeCanvas, makeRNG, TAU, clamp, lerp, hsl2rgb, css, radial, roundRect } from '../core/util.js';

const cache = new Map();

function irregular(ctx, x, y, rx, ry, rnd, wob = 0.22, points = 9) {
  // smooth closed blob through jittered radii (Catmull-Rom-ish via midpoint quads)
  const pts = [];
  for (let i = 0; i < points; i++) {
    const a = (i / points) * TAU;
    const k = 1 + (rnd() - 0.5) * wob * 2;
    pts.push([x + Math.cos(a) * rx * k, y + Math.sin(a) * ry * k]);
  }
  ctx.beginPath();
  const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  let prev = pts[points - 1];
  let start = mid(prev, pts[0]);
  ctx.moveTo(start[0], start[1]);
  for (let i = 0; i < points; i++) {
    const cur = pts[i];
    const next = pts[(i + 1) % points];
    const m = mid(cur, next);
    ctx.quadraticCurveTo(cur[0], cur[1], m[0], m[1]);
  }
  ctx.closePath();
  ctx.fill();
}

function foliage(ctx, x, y, r, hue, sat, light, rnd, layers = 4) {
  for (let i = 0; i < layers; i++) {
    const t = i / layers;
    ctx.fillStyle = `hsl(${hue + rnd() * 12 - 6} ${sat}% ${light - 12 + t * 22}%)`;
    const rr = r * (1 - t * 0.34);
    irregular(ctx, x + (rnd() - 0.5) * r * 0.3 - t * r * 0.14, y + (rnd() - 0.5) * r * 0.22 - t * r * 0.2, rr, rr * 0.82, rnd, 0.26, 11);
  }
  // dappled highlights
  ctx.fillStyle = `hsla(${hue + 14} ${sat}% ${light + 26}%,0.55)`;
  for (let i = 0; i < 7; i++) {
    const a = rnd() * TAU, d = rnd() * r * 0.62;
    ctx.beginPath();
    ctx.ellipse(x + Math.cos(a) * d - r * 0.18, y + Math.sin(a) * d - r * 0.24, r * 0.14, r * 0.10, rnd() * 3, 0, TAU);
    ctx.fill();
  }
}

function trunk(ctx, x, yBase, h, w, hue = 26, light = 26) {
  const g = ctx.createLinearGradient(x - w, 0, x + w, 0);
  g.addColorStop(0, `hsl(${hue} 32% ${light - 8}%)`);
  g.addColorStop(0.45, `hsl(${hue} 30% ${light + 8}%)`);
  g.addColorStop(1, `hsl(${hue} 34% ${light - 12}%)`);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.moveTo(x - w, yBase);
  ctx.quadraticCurveTo(x - w * 0.6, yBase - h * 0.6, x - w * 0.42, yBase - h);
  ctx.lineTo(x + w * 0.42, yBase - h);
  ctx.quadraticCurveTo(x + w * 0.6, yBase - h * 0.6, x + w, yBase);
  ctx.closePath();
  ctx.fill();
}

/** Builds a sprite. Returns {canvas, ox, oy} where (ox,oy) is the anchor at ground centre. */
function build(kind, variant) {
  const rnd = makeRNG(variant * 2654435761 + kind.length * 7919 + kind.charCodeAt(0) * 131);
  let W = 96, H = 128, ox = 48, oy = 120;
  let c, ctx;
  const init = (w, h, axIsCenter = true, baseY = null) => {
    W = w; H = h; ox = w / 2; oy = baseY ?? h - 4;
    ({ canvas: c, ctx } = makeCanvas(W, H));
    return ctx;
  };

  switch (kind) {
    case 'oak': {
      ctx = init(120, 140);
      const hue = 96 + rnd() * 26;
      trunk(ctx, ox, oy, 42 + rnd() * 12, 6.5 + rnd() * 2.5);
      foliage(ctx, ox, oy - 66, 31 + rnd() * 8, hue, 38 + rnd() * 14, 30 + rnd() * 8, rnd, 5);
      break;
    }
    case 'birch': {
      ctx = init(104, 150);
      const th = 74;
      ctx.fillStyle = '#e8e6de';
      ctx.beginPath();
      ctx.moveTo(ox - 5, oy); ctx.lineTo(ox - 3.4, oy - th); ctx.lineTo(ox + 3.4, oy - th); ctx.lineTo(ox + 5, oy);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(60,58,54,0.8)';
      for (let i = 0; i < 9; i++) ctx.fillRect(ox - 4 + rnd() * 5, oy - 10 - rnd() * (th - 14), 3 + rnd() * 4, 1.6);
      foliage(ctx, ox, oy - 90, 26 + rnd() * 6, 82 + rnd() * 20, 44, 40, rnd, 4);
      break;
    }
    case 'pine': {
      ctx = init(96, 156);
      trunk(ctx, ox, oy, 34, 6, 22, 22);
      const hue = 138 + rnd() * 22;
      const layers = 5;
      for (let i = 0; i < layers; i++) {
        const t = i / (layers - 1);
        const y = oy - 26 - t * 96;
        const r = 40 * (1 - t * 0.72) + 6;
        ctx.fillStyle = `hsl(${hue} ${34 + t * 10}% ${16 + t * 18}%)`;
        ctx.beginPath();
        ctx.moveTo(ox - r, y);
        for (let k = 0; k <= 8; k++) {
          const px = ox - r + (r * 2 * k) / 8;
          ctx.lineTo(px, y - (k % 2 ? 4 : 0) - 2);
        }
        ctx.lineTo(ox + r, y);
        ctx.lineTo(ox, y - r * 1.15);
        ctx.closePath();
        ctx.fill();
      }
      break;
    }
    case 'palm': {
      ctx = init(128, 150);
      const lean = (rnd() - 0.5) * 26;
      ctx.strokeStyle = '#8a6a44';
      ctx.lineWidth = 9;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(ox, oy);
      ctx.quadraticCurveTo(ox + lean * 0.6, oy - 50, ox + lean, oy - 92);
      ctx.stroke();
      ctx.strokeStyle = 'rgba(255,255,255,0.12)';
      ctx.lineWidth = 3;
      ctx.stroke();
      const tipX = ox + lean, tipY = oy - 92;
      for (let i = 0; i < 8; i++) {
        const a = (i / 8) * TAU + rnd() * 0.3;
        ctx.strokeStyle = `hsl(${106 + rnd() * 24} 46% ${26 + rnd() * 14}%)`;
        ctx.lineWidth = 6;
        ctx.beginPath();
        ctx.moveTo(tipX, tipY);
        ctx.quadraticCurveTo(tipX + Math.cos(a) * 26, tipY + Math.sin(a) * 18 - 12, tipX + Math.cos(a) * 46, tipY + Math.sin(a) * 30 + 6);
        ctx.stroke();
      }
      ctx.fillStyle = '#a8862f';
      for (let i = 0; i < 3; i++) {
        ctx.beginPath();
        ctx.arc(tipX + (rnd() - 0.5) * 12, tipY + 8 + rnd() * 6, 4, 0, TAU);
        ctx.fill();
      }
      break;
    }
    case 'deadTree': {
      ctx = init(110, 130);
      ctx.strokeStyle = '#4a4038';
      ctx.lineCap = 'round';
      const branch = (x, y, a, len, w2, depth) => {
        if (depth <= 0 || len < 5) return;
        const x2 = x + Math.cos(a) * len, y2 = y + Math.sin(a) * len;
        ctx.lineWidth = w2;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x2, y2); ctx.stroke();
        branch(x2, y2, a - 0.4 - rnd() * 0.3, len * 0.72, w2 * 0.68, depth - 1);
        branch(x2, y2, a + 0.4 + rnd() * 0.3, len * 0.72, w2 * 0.68, depth - 1);
      };
      branch(ox, oy, -Math.PI / 2, 36, 9, 4);
      break;
    }
    case 'bush': {
      ctx = init(72, 64);
      foliage(ctx, ox, oy - 16, 24 + rnd() * 6, 100 + rnd() * 30, 36, 28, rnd, 3);
      if (rnd() < 0.4) {
        ctx.fillStyle = '#c8384a';
        for (let i = 0; i < 5; i++) {
          ctx.beginPath();
          ctx.arc(ox + (rnd() - 0.5) * 30, oy - 16 + (rnd() - 0.5) * 20, 2.4, 0, TAU);
          ctx.fill();
        }
      }
      break;
    }
    case 'cactus': {
      ctx = init(76, 110);
      const g = ctx.createLinearGradient(ox - 12, 0, ox + 12, 0);
      g.addColorStop(0, '#2f6b3a'); g.addColorStop(0.5, '#4f9152'); g.addColorStop(1, '#28532f');
      ctx.fillStyle = g;
      roundRect(ctx, ox - 11, oy - 68, 22, 68, 11); ctx.fill();
      if (rnd() < 0.7) { roundRect(ctx, ox - 30, oy - 52, 19, 34, 9); ctx.fill(); roundRect(ctx, ox - 30, oy - 52, 19, 12, 6); ctx.fill(); }
      if (rnd() < 0.7) { roundRect(ctx, ox + 11, oy - 62, 19, 40, 9); ctx.fill(); }
      ctx.strokeStyle = 'rgba(240,240,210,0.5)';
      ctx.lineWidth = 1;
      for (let i = 0; i < 14; i++) {
        const y = oy - 8 - rnd() * 58;
        ctx.beginPath(); ctx.moveTo(ox - 6, y); ctx.lineTo(ox - 10, y - 3); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(ox + 6, y); ctx.lineTo(ox + 10, y - 3); ctx.stroke();
      }
      break;
    }
    case 'rock':
    case 'snowRock':
    case 'ashRock': {
      const big = variant % 3 === 0;
      ctx = init(big ? 96 : 62, big ? 78 : 54);
      const base = kind === 'snowRock' ? [206, 214, 226] : kind === 'ashRock' ? [66, 58, 56] : [124, 120, 112];
      const r = big ? 34 : 20;
      ctx.fillStyle = `rgb(${base[0] * 0.55},${base[1] * 0.55},${base[2] * 0.55})`;
      irregular(ctx, ox, oy - r * 0.5, r, r * 0.72, rnd, 0.2, 8);
      ctx.fillStyle = `rgb(${base[0]},${base[1]},${base[2]})`;
      irregular(ctx, ox - 2, oy - r * 0.62, r * 0.9, r * 0.62, rnd, 0.2, 8);
      ctx.fillStyle = `rgba(255,255,255,0.28)`;
      irregular(ctx, ox - r * 0.28, oy - r * 0.86, r * 0.42, r * 0.26, rnd, 0.3, 7);
      if (kind === 'ashRock') {
        ctx.fillStyle = 'rgba(230,110,40,0.5)';
        irregular(ctx, ox + r * 0.2, oy - r * 0.4, r * 0.2, r * 0.12, rnd, 0.4, 6);
      }
      break;
    }
    case 'crystal': {
      ctx = init(78, 104);
      const hue = rnd() < 0.5 ? 190 + rnd() * 40 : 280 + rnd() * 40;
      for (let i = 0; i < 3; i++) {
        const bx = ox + (i - 1) * 14 + (rnd() - 0.5) * 6;
        const hgt = 34 + rnd() * 40 - Math.abs(i - 1) * 12;
        ctx.fillStyle = `hsla(${hue} 70% 62%,0.9)`;
        ctx.beginPath();
        ctx.moveTo(bx - 9, oy); ctx.lineTo(bx - 5, oy - hgt); ctx.lineTo(bx + 5, oy - hgt * 0.92); ctx.lineTo(bx + 9, oy);
        ctx.closePath(); ctx.fill();
        ctx.fillStyle = `hsla(${hue} 90% 84%,0.75)`;
        ctx.beginPath();
        ctx.moveTo(bx - 3, oy); ctx.lineTo(bx - 1, oy - hgt * 0.95); ctx.lineTo(bx + 3, oy - hgt * 0.9); ctx.lineTo(bx + 3, oy);
        ctx.closePath(); ctx.fill();
      }
      break;
    }
    case 'stump': {
      ctx = init(56, 44);
      ctx.fillStyle = '#5a4630';
      ctx.beginPath(); ctx.ellipse(ox, oy - 8, 18, 12, 0, 0, TAU); ctx.fill();
      ctx.fillStyle = '#6f5738';
      ctx.beginPath(); ctx.ellipse(ox, oy - 14, 17, 10, 0, 0, TAU); ctx.fill();
      ctx.strokeStyle = 'rgba(60,44,28,0.6)'; ctx.lineWidth = 1;
      for (let i = 1; i < 4; i++) { ctx.beginPath(); ctx.ellipse(ox, oy - 14, 4 * i, 2.4 * i, 0, 0, TAU); ctx.stroke(); }
      break;
    }
    case 'reeds': {
      ctx = init(56, 66);
      for (let i = 0; i < 9; i++) {
        const x = ox + (rnd() - 0.5) * 26;
        const hgt = 24 + rnd() * 22;
        ctx.strokeStyle = `hsl(${70 + rnd() * 30} 34% ${30 + rnd() * 16}%)`;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(x, oy); ctx.quadraticCurveTo(x + 4, oy - hgt * 0.6, x + 9, oy - hgt); ctx.stroke();
        if (rnd() < 0.5) { ctx.fillStyle = '#6b4a2a'; ctx.beginPath(); ctx.ellipse(x + 9, oy - hgt, 2.4, 5, 0.4, 0, TAU); ctx.fill(); }
      }
      break;
    }
    case 'mushroom': {
      ctx = init(48, 48);
      for (let i = 0; i < 3; i++) {
        const x = ox + (rnd() - 0.5) * 20, y = oy - rnd() * 6;
        const s = 0.6 + rnd() * 0.7;
        ctx.fillStyle = '#e6dccb';
        ctx.fillRect(x - 2 * s, y - 10 * s, 4 * s, 10 * s);
        ctx.fillStyle = rnd() < 0.5 ? '#c04434' : '#8a5fc0';
        ctx.beginPath(); ctx.ellipse(x, y - 10 * s, 8 * s, 6 * s, 0, Math.PI, TAU); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,0.75)';
        for (let k = 0; k < 3; k++) { ctx.beginPath(); ctx.arc(x + (rnd() - 0.5) * 10 * s, y - 12 * s, 1.3 * s, 0, TAU); ctx.fill(); }
      }
      break;
    }
    case 'flowers': {
      ctx = init(52, 40);
      const hue = [348, 44, 274, 200, 20, 320][variant % 6];
      for (let i = 0; i < 7; i++) {
        const x = ox + (rnd() - 0.5) * 34, y = oy - rnd() * 12;
        ctx.strokeStyle = '#4e7a3a'; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + (rnd() - 0.5) * 4, y - 9); ctx.stroke();
        ctx.fillStyle = `hsl(${hue} 78% ${62 + rnd() * 14}%)`;
        for (let p = 0; p < 5; p++) {
          const a = (p / 5) * TAU;
          ctx.beginPath(); ctx.ellipse(x + Math.cos(a) * 2.6, y - 9 + Math.sin(a) * 2.6, 2, 1.6, a, 0, TAU); ctx.fill();
        }
        ctx.fillStyle = '#f5d44a';
        ctx.beginPath(); ctx.arc(x, y - 9, 1.4, 0, TAU); ctx.fill();
      }
      break;
    }
    case 'herb': {
      ctx = init(44, 44);
      for (let i = 0; i < 5; i++) {
        const a = -Math.PI / 2 + (i - 2) * 0.42;
        ctx.strokeStyle = '#5d9a4a'; ctx.lineWidth = 3; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(ox, oy - 2); ctx.quadraticCurveTo(ox + Math.cos(a) * 8, oy - 12, ox + Math.cos(a) * 14, oy - 20); ctx.stroke();
      }
      ctx.fillStyle = '#8fe0ff';
      ctx.beginPath(); ctx.arc(ox, oy - 20, 4, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(200,250,255,0.7)';
      ctx.beginPath(); ctx.arc(ox - 1, oy - 21, 2, 0, TAU); ctx.fill();
      break;
    }
    case 'ore': {
      ctx = init(64, 58);
      ctx.fillStyle = '#5c5750';
      irregular(ctx, ox, oy - 12, 22, 15, rnd, 0.24, 8);
      ctx.fillStyle = '#7b7469';
      irregular(ctx, ox - 2, oy - 15, 17, 11, rnd, 0.24, 8);
      const oreHue = [46, 200, 0, 120][variant % 4];
      ctx.fillStyle = `hsl(${oreHue} 70% 58%)`;
      for (let i = 0; i < 5; i++) {
        ctx.beginPath();
        ctx.arc(ox + (rnd() - 0.5) * 24, oy - 8 - rnd() * 14, 2.6 + rnd() * 1.8, 0, TAU);
        ctx.fill();
      }
      break;
    }
    case 'ruinPillar': {
      ctx = init(72, 128);
      const hgt = 40 + rnd() * 60;
      const g = ctx.createLinearGradient(ox - 14, 0, ox + 14, 0);
      g.addColorStop(0, '#8c8a7d'); g.addColorStop(0.5, '#b9b4a4'); g.addColorStop(1, '#7d7a6d');
      ctx.fillStyle = g;
      ctx.fillRect(ox - 13, oy - hgt, 26, hgt);
      ctx.fillStyle = '#a49f8f';
      ctx.fillRect(ox - 18, oy - 8, 36, 8);
      ctx.fillStyle = 'rgba(60,80,50,0.35)';
      for (let i = 0; i < 6; i++) ctx.fillRect(ox - 13 + rnd() * 22, oy - hgt + rnd() * hgt, 5, 3);
      ctx.strokeStyle = 'rgba(50,46,40,0.4)'; ctx.lineWidth = 1;
      for (let i = 1; i < 5; i++) { ctx.beginPath(); ctx.moveTo(ox - 13, oy - (hgt * i) / 5); ctx.lineTo(ox + 13, oy - (hgt * i) / 5); ctx.stroke(); }
      break;
    }
    case 'ruinWall': {
      ctx = init(120, 92);
      ctx.fillStyle = '#a09b8b';
      const wsegs = 5;
      for (let i = 0; i < wsegs; i++) {
        const hgt = 24 + rnd() * 38;
        ctx.fillStyle = `hsl(48 8% ${52 + rnd() * 12}%)`;
        ctx.fillRect(ox - 50 + i * 21, oy - hgt, 20, hgt);
        ctx.fillStyle = 'rgba(0,0,0,0.14)';
        ctx.fillRect(ox - 50 + i * 21, oy - hgt, 20, 4);
      }
      break;
    }
    case 'gravestone': {
      ctx = init(48, 60);
      ctx.fillStyle = '#8e8b82';
      ctx.beginPath();
      ctx.moveTo(ox - 12, oy); ctx.lineTo(ox - 12, oy - 24);
      ctx.arc(ox, oy - 24, 12, Math.PI, 0); ctx.lineTo(ox + 12, oy); ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(0,0,0,0.25)';
      ctx.fillRect(ox - 6, oy - 30, 12, 3);
      ctx.fillRect(ox - 2, oy - 34, 4, 12);
      break;
    }
    case 'campfire': {
      ctx = init(70, 60);
      ctx.fillStyle = '#3a3128';
      ctx.beginPath(); ctx.ellipse(ox, oy - 4, 20, 10, 0, 0, TAU); ctx.fill();
      ctx.strokeStyle = '#5b4a34'; ctx.lineWidth = 5; ctx.lineCap = 'round';
      for (let i = 0; i < 4; i++) {
        const a = (i / 4) * Math.PI + 0.4;
        ctx.beginPath();
        ctx.moveTo(ox - Math.cos(a) * 15, oy - 2 - Math.sin(a) * 6);
        ctx.lineTo(ox + Math.cos(a) * 15, oy - 6 + Math.sin(a) * 6);
        ctx.stroke();
      }
      for (let i = 0; i < 6; i++) {
        ctx.fillStyle = `hsl(${20 + i * 4} 90% ${40 + i * 5}%)`;
        ctx.beginPath(); ctx.arc(ox + (i % 2 ? 6 : -6), oy - 6, 5 - i * 0.5, 0, TAU); ctx.fill();
      }
      break;
    }
    case 'tent': {
      ctx = init(110, 86);
      const hue = 30 + rnd() * 30;
      ctx.fillStyle = `hsl(${hue} 26% 34%)`;
      ctx.beginPath(); ctx.moveTo(ox - 40, oy); ctx.lineTo(ox, oy - 52); ctx.lineTo(ox + 40, oy); ctx.closePath(); ctx.fill();
      ctx.fillStyle = `hsl(${hue} 26% 42%)`;
      ctx.beginPath(); ctx.moveTo(ox - 40, oy); ctx.lineTo(ox, oy - 52); ctx.lineTo(ox - 8, oy); ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(20,16,12,0.75)';
      ctx.beginPath(); ctx.moveTo(ox - 12, oy); ctx.quadraticCurveTo(ox, oy - 34, ox + 12, oy); ctx.closePath(); ctx.fill();
      break;
    }
    case 'barrel': {
      ctx = init(46, 56);
      const g = ctx.createLinearGradient(ox - 14, 0, ox + 14, 0);
      g.addColorStop(0, '#5a3f24'); g.addColorStop(0.4, '#8a6236'); g.addColorStop(1, '#4a341e');
      ctx.fillStyle = g;
      roundRect(ctx, ox - 14, oy - 32, 28, 32, 6); ctx.fill();
      ctx.fillStyle = '#3d3128';
      ctx.fillRect(ox - 15, oy - 26, 30, 4);
      ctx.fillRect(ox - 15, oy - 12, 30, 4);
      ctx.fillStyle = '#9c7143';
      ctx.beginPath(); ctx.ellipse(ox, oy - 32, 14, 5, 0, 0, TAU); ctx.fill();
      break;
    }
    case 'crate': {
      ctx = init(48, 52);
      ctx.fillStyle = '#8a6435';
      ctx.fillRect(ox - 15, oy - 30, 30, 30);
      ctx.fillStyle = '#a37a44';
      ctx.fillRect(ox - 15, oy - 30, 30, 5);
      ctx.strokeStyle = '#5c4222'; ctx.lineWidth = 3;
      ctx.strokeRect(ox - 15, oy - 30, 30, 30);
      ctx.beginPath(); ctx.moveTo(ox - 15, oy - 30); ctx.lineTo(ox + 15, oy); ctx.stroke();
      break;
    }
    case 'chest': {
      ctx = init(56, 52);
      ctx.fillStyle = '#7a5327';
      ctx.fillRect(ox - 18, oy - 20, 36, 20);
      ctx.fillStyle = '#8e6330';
      ctx.beginPath(); ctx.moveTo(ox - 18, oy - 20); ctx.quadraticCurveTo(ox, oy - 40, ox + 18, oy - 20); ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#d8b04a';
      ctx.fillRect(ox - 20, oy - 22, 40, 4);
      ctx.fillRect(ox - 4, oy - 22, 8, 12);
      ctx.fillStyle = '#5a4020';
      ctx.beginPath(); ctx.arc(ox, oy - 14, 2.4, 0, TAU); ctx.fill();
      break;
    }
    case 'well': {
      ctx = init(88, 96);
      ctx.fillStyle = '#7d7870';
      ctx.beginPath(); ctx.ellipse(ox, oy - 10, 26, 14, 0, 0, TAU); ctx.fill();
      ctx.fillStyle = '#28323c';
      ctx.beginPath(); ctx.ellipse(ox, oy - 12, 19, 9, 0, 0, TAU); ctx.fill();
      ctx.fillStyle = '#3f6a86';
      ctx.beginPath(); ctx.ellipse(ox, oy - 11, 15, 7, 0, 0, TAU); ctx.fill();
      ctx.strokeStyle = '#6b4a2a'; ctx.lineWidth = 5;
      ctx.beginPath(); ctx.moveTo(ox - 20, oy - 16); ctx.lineTo(ox - 20, oy - 54); ctx.moveTo(ox + 20, oy - 16); ctx.lineTo(ox + 20, oy - 54); ctx.stroke();
      ctx.fillStyle = '#7a4a2c';
      ctx.beginPath(); ctx.moveTo(ox - 32, oy - 52); ctx.lineTo(ox, oy - 70); ctx.lineTo(ox + 32, oy - 52); ctx.closePath(); ctx.fill();
      break;
    }
    case 'lamp': {
      ctx = init(40, 92);
      ctx.strokeStyle = '#3a3630'; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox, oy - 58); ctx.stroke();
      ctx.fillStyle = '#2e2a24';
      ctx.beginPath(); ctx.moveTo(ox - 9, oy - 58); ctx.lineTo(ox + 9, oy - 58); ctx.lineTo(ox + 6, oy - 76); ctx.lineTo(ox - 6, oy - 76); ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,214,130,0.95)';
      ctx.fillRect(ox - 5, oy - 74, 10, 14);
      break;
    }
    case 'stall': {
      ctx = init(112, 92);
      const hue = variant * 47 % 360;
      ctx.fillStyle = '#6b4a2a';
      ctx.fillRect(ox - 34, oy - 26, 68, 26);
      ctx.fillStyle = '#8a6238';
      ctx.fillRect(ox - 36, oy - 30, 72, 6);
      ctx.strokeStyle = '#5c4020'; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(ox - 32, oy - 30); ctx.lineTo(ox - 32, oy - 60); ctx.moveTo(ox + 32, oy - 30); ctx.lineTo(ox + 32, oy - 60); ctx.stroke();
      for (let i = 0; i < 5; i++) {
        ctx.fillStyle = i % 2 ? `hsl(${hue} 60% 60%)` : '#efe8dc';
        ctx.beginPath();
        ctx.moveTo(ox - 40 + i * 16, oy - 62); ctx.lineTo(ox - 24 + i * 16, oy - 62);
        ctx.lineTo(ox - 32 + i * 16, oy - 50); ctx.closePath(); ctx.fill();
      }
      ctx.fillStyle = '#c8543a';
      for (let i = 0; i < 4; i++) { ctx.beginPath(); ctx.arc(ox - 22 + i * 14, oy - 34, 4, 0, TAU); ctx.fill(); }
      break;
    }
    case 'fence': {
      ctx = init(TILE + 8, 48);
      ctx.fillStyle = '#6b5636';
      ctx.fillRect(ox - 17, oy - 26, 5, 26);
      ctx.fillRect(ox + 12, oy - 26, 5, 26);
      ctx.fillStyle = '#7d6642';
      ctx.fillRect(ox - 20, oy - 22, 40, 4);
      ctx.fillRect(ox - 20, oy - 12, 40, 4);
      break;
    }
    case 'sign': {
      ctx = init(52, 56);
      ctx.fillStyle = '#6b5636';
      ctx.fillRect(ox - 3, oy - 30, 6, 30);
      ctx.fillStyle = '#8a6d45';
      roundRect(ctx, ox - 18, oy - 44, 36, 18, 3); ctx.fill();
      ctx.fillStyle = 'rgba(50,36,20,0.6)';
      ctx.fillRect(ox - 12, oy - 38, 24, 2);
      ctx.fillRect(ox - 12, oy - 33, 16, 2);
      break;
    }
    case 'townTree': {
      ctx = init(96, 118);
      trunk(ctx, ox, oy, 40, 6);
      foliage(ctx, ox, oy - 62, 30, 108, 40, 32, rnd, 4);
      break;
    }
    case 'shrine': {
      ctx = init(132, 148);
      // stone platform
      ctx.fillStyle = '#6d6a62';
      ctx.beginPath(); ctx.ellipse(ox, oy - 8, 46, 20, 0, 0, TAU); ctx.fill();
      ctx.fillStyle = '#847f74';
      ctx.beginPath(); ctx.ellipse(ox, oy - 14, 40, 16, 0, 0, TAU); ctx.fill();
      // arch
      ctx.strokeStyle = '#9a9384'; ctx.lineWidth = 10; ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(ox - 30, oy - 16);
      ctx.quadraticCurveTo(ox - 34, oy - 96, ox, oy - 100);
      ctx.quadraticCurveTo(ox + 34, oy - 96, ox + 30, oy - 16);
      ctx.stroke();
      // rune crystal
      ctx.fillStyle = 'rgba(120,220,255,0.9)';
      ctx.beginPath();
      ctx.moveTo(ox, oy - 74); ctx.lineTo(ox + 11, oy - 56); ctx.lineTo(ox, oy - 36); ctx.lineTo(ox - 11, oy - 56);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.8)';
      ctx.beginPath(); ctx.moveTo(ox, oy - 70); ctx.lineTo(ox + 5, oy - 56); ctx.lineTo(ox, oy - 44); ctx.lineTo(ox - 5, oy - 56); ctx.closePath(); ctx.fill();
      break;
    }
    case 'caveMouth': {
      ctx = init(160, 130);
      ctx.fillStyle = '#5d5a52';
      irregular(ctx, ox, oy - 30, 64, 44, rnd, 0.16, 12);
      ctx.fillStyle = '#7a766c';
      irregular(ctx, ox - 4, oy - 36, 56, 38, rnd, 0.16, 12);
      ctx.fillStyle = '#0d0b0a';
      ctx.beginPath();
      ctx.moveTo(ox - 26, oy - 2);
      ctx.quadraticCurveTo(ox - 28, oy - 52, ox, oy - 54);
      ctx.quadraticCurveTo(ox + 28, oy - 52, ox + 26, oy - 2);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(0,0,0,0.5)';
      irregular(ctx, ox, oy - 4, 30, 8, rnd, 0.3, 8);
      break;
    }
    case 'torch': {
      ctx = init(30, 70);
      ctx.fillStyle = '#4a3a26';
      ctx.fillRect(ox - 3, oy - 42, 6, 42);
      ctx.fillStyle = '#f0a13a';
      ctx.beginPath(); ctx.ellipse(ox, oy - 46, 6, 9, 0, 0, TAU); ctx.fill();
      break;
    }
    case 'banner': {
      ctx = init(46, 96);
      ctx.fillStyle = '#3a3630';
      ctx.fillRect(ox - 2, oy - 76, 4, 76);
      const hue = variant * 63 % 360;
      ctx.fillStyle = `hsl(${hue} 46% 42%)`;
      ctx.beginPath();
      ctx.moveTo(ox - 14, oy - 74); ctx.lineTo(ox + 14, oy - 74); ctx.lineTo(ox + 14, oy - 34);
      ctx.lineTo(ox, oy - 44); ctx.lineTo(ox - 14, oy - 34); ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,235,180,0.85)';
      ctx.beginPath(); ctx.arc(ox, oy - 58, 6, 0, TAU); ctx.fill();
      break;
    }
    default: {
      ctx = init(32, 32);
      ctx.fillStyle = '#f0f';
      ctx.fillRect(0, 0, 32, 32);
    }
  }
  return { canvas: c, ox, oy, w: W, h: H };
}

export function getProp(kind, variant = 0) {
  const key = kind + '#' + variant;
  let p = cache.get(key);
  if (!p) { p = build(kind, variant); cache.set(key, p); }
  return p;
}

/** Approximate collision radius (world px) for a prop kind; 0 = walk-through. */
export const PROP_COLLIDE = {
  oak: 9, birch: 8, pine: 9, palm: 7, deadTree: 7, cactus: 10, rock: 12, snowRock: 12,
  ashRock: 12, crystal: 12, stump: 10, ruinPillar: 12, ruinWall: 22, gravestone: 9,
  well: 22, stall: 26, barrel: 11, crate: 12, tent: 26, lamp: 5, sign: 5, townTree: 9,
  shrine: 0, caveMouth: 0, chest: 12, campfire: 10, fence: 16, torch: 4, banner: 4, ore: 14,
};

/** Props that sway in the wind. */
export const PROP_SWAY = new Set(['oak', 'birch', 'pine', 'palm', 'bush', 'reeds', 'flowers', 'townTree', 'herb']);
