// icons.js — small cached icon canvases for items, spells and UI glyphs.

import { makeCanvas, TAU, roundRect } from '../core/util.js';
import { rarityColor, effStats } from '../game/items.js';

const cache = new Map();
const S = 44;

function drawIcon(ctx, icon, color, extra = {}) {
  const c = S / 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  switch (icon) {
    case 'sword': {
      ctx.strokeStyle = '#6b4a2a'; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(c - 8, c + 12); ctx.lineTo(c - 5, c + 9); ctx.stroke();
      ctx.fillStyle = '#d8b04a';
      ctx.save(); ctx.translate(c, c); ctx.rotate(-Math.PI / 4);
      ctx.fillRect(-7, 5, 14, 3.4);
      const g = ctx.createLinearGradient(-3, 0, 3, 0);
      g.addColorStop(0, '#f2f6fa'); g.addColorStop(0.5, color); g.addColorStop(1, '#8f99a3');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.moveTo(-3, 5); ctx.lineTo(3, 5); ctx.lineTo(2.6, -12); ctx.lineTo(0, -17); ctx.lineTo(-2.6, -12); ctx.closePath(); ctx.fill();
      ctx.restore();
      break;
    }
    case 'dagger': {
      ctx.save(); ctx.translate(c, c); ctx.rotate(-Math.PI / 4);
      ctx.fillStyle = '#4a3a26'; roundRect(ctx, -2, 4, 4, 9, 2); ctx.fill();
      ctx.fillStyle = '#d8b04a'; ctx.fillRect(-5, 2, 10, 3);
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(-3, 2); ctx.lineTo(3, 2); ctx.lineTo(0, -14); ctx.closePath(); ctx.fill();
      ctx.restore();
      break;
    }
    case 'axe': {
      ctx.save(); ctx.translate(c, c); ctx.rotate(0.4);
      ctx.strokeStyle = '#6b4a2a'; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(0, 15); ctx.lineTo(0, -12); ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(0, -12); ctx.quadraticCurveTo(14, -12, 12, 2); ctx.lineTo(0, -1); ctx.closePath(); ctx.fill();
      ctx.beginPath(); ctx.moveTo(0, -12); ctx.quadraticCurveTo(-14, -12, -12, 2); ctx.lineTo(0, -1); ctx.closePath(); ctx.fill();
      ctx.restore();
      break;
    }
    case 'spear': {
      ctx.save(); ctx.translate(c, c); ctx.rotate(-Math.PI / 4);
      ctx.strokeStyle = '#7a5a34'; ctx.lineWidth = 3.4;
      ctx.beginPath(); ctx.moveTo(0, 16); ctx.lineTo(0, -8); ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(0, -18); ctx.lineTo(4.4, -6); ctx.lineTo(0, -3); ctx.lineTo(-4.4, -6); ctx.closePath(); ctx.fill();
      ctx.restore();
      break;
    }
    case 'mace': {
      ctx.save(); ctx.translate(c, c); ctx.rotate(-Math.PI / 4);
      ctx.strokeStyle = '#5c4020'; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(0, 15); ctx.lineTo(0, -4); ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(0, -9, 7.4, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(0,0,0,0.3)';
      for (let i = 0; i < 5; i++) { const a = (i / 5) * TAU; ctx.beginPath(); ctx.arc(Math.cos(a) * 5, -9 + Math.sin(a) * 5, 2, 0, TAU); ctx.fill(); }
      ctx.restore();
      break;
    }
    case 'staff': {
      ctx.strokeStyle = '#6b5636'; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(c + 8, c + 15); ctx.lineTo(c - 4, c - 8); ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(c - 5, c - 11, 7, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      ctx.beginPath(); ctx.arc(c - 7, c - 13, 2.6, 0, TAU); ctx.fill();
      break;
    }
    case 'bow': {
      ctx.strokeStyle = color; ctx.lineWidth = 3.4;
      ctx.beginPath(); ctx.moveTo(c + 6, c - 15); ctx.quadraticCurveTo(c - 14, c, c + 6, c + 15); ctx.stroke();
      ctx.strokeStyle = '#e8e2d0'; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(c + 6, c - 15); ctx.lineTo(c + 6, c + 15); ctx.stroke();
      ctx.strokeStyle = '#8a6238'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(c - 8, c); ctx.lineTo(c + 14, c); ctx.stroke();
      break;
    }
    case 'shield': {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(c - 12, c - 13); ctx.lineTo(c + 12, c - 13); ctx.lineTo(c + 12, c + 2);
      ctx.quadraticCurveTo(c, c + 17, c - 12, c + 2); ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.3)';
      ctx.beginPath(); ctx.moveTo(c - 12, c - 13); ctx.lineTo(c, c - 13); ctx.lineTo(c, c + 10); ctx.quadraticCurveTo(c - 8, c + 4, c - 12, c + 2); ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#d8b04a';
      ctx.beginPath(); ctx.arc(c, c - 2, 3.4, 0, TAU); ctx.fill();
      break;
    }
    case 'body': {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(c - 13, c - 8); ctx.lineTo(c - 7, c - 13); ctx.lineTo(c + 7, c - 13); ctx.lineTo(c + 13, c - 8);
      ctx.lineTo(c + 9, c - 2); ctx.lineTo(c + 10, c + 14); ctx.lineTo(c - 10, c + 14); ctx.lineTo(c - 9, c - 2);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.24)';
      ctx.fillRect(c - 7, c - 2, 5, 15);
      ctx.fillStyle = 'rgba(0,0,0,0.2)';
      ctx.fillRect(c - 10, c + 6, 20, 2.4);
      break;
    }
    case 'helm': {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(c, c - 2, 12, Math.PI, TAU);
      ctx.lineTo(c + 12, c + 8); ctx.lineTo(c + 6, c + 8); ctx.lineTo(c + 6, c + 2);
      ctx.lineTo(c - 6, c + 2); ctx.lineTo(c - 6, c + 8); ctx.lineTo(c - 12, c + 8);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.26)';
      ctx.fillRect(c - 10, c - 10, 4, 14);
      break;
    }
    case 'ring': {
      ctx.strokeStyle = '#e0c060'; ctx.lineWidth = 4.4;
      ctx.beginPath(); ctx.arc(c, c + 3, 9, 0, TAU); ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(c, c - 15); ctx.lineTo(c + 6, c - 8); ctx.lineTo(c, c - 1); ctx.lineTo(c - 6, c - 8);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      ctx.beginPath(); ctx.moveTo(c, c - 13); ctx.lineTo(c + 3, c - 8); ctx.lineTo(c, c - 4); ctx.closePath(); ctx.fill();
      break;
    }
    case 'potion': {
      ctx.fillStyle = 'rgba(230,240,250,0.35)';
      ctx.beginPath();
      ctx.moveTo(c - 5, c - 14); ctx.lineTo(c + 5, c - 14); ctx.lineTo(c + 5, c - 7);
      ctx.quadraticCurveTo(c + 12, c + 2, c + 8, c + 12);
      ctx.lineTo(c - 8, c + 12);
      ctx.quadraticCurveTo(c - 12, c + 2, c - 5, c - 7);
      ctx.closePath(); ctx.fill();
      ctx.save(); ctx.clip();
      ctx.fillStyle = color;
      ctx.fillRect(c - 13, c - 3, 26, 18);
      ctx.fillStyle = 'rgba(255,255,255,0.35)';
      ctx.beginPath(); ctx.ellipse(c, c - 3, 9, 2.4, 0, 0, TAU); ctx.fill();
      ctx.restore();
      ctx.fillStyle = '#7a5a34';
      ctx.fillRect(c - 6, c - 17, 12, 5);
      ctx.fillStyle = 'rgba(255,255,255,0.5)';
      ctx.beginPath(); ctx.ellipse(c - 5, c + 4, 2, 5, 0.3, 0, TAU); ctx.fill();
      break;
    }
    case 'herb': {
      ctx.strokeStyle = '#5d9a4a'; ctx.lineWidth = 3;
      for (let i = -2; i <= 2; i++) {
        ctx.beginPath(); ctx.moveTo(c, c + 14); ctx.quadraticCurveTo(c + i * 5, c + 2, c + i * 9, c - 10); ctx.stroke();
      }
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(c, c - 12, 4.4, 0, TAU); ctx.fill();
      break;
    }
    case 'food': {
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.ellipse(c, c + 2, 14, 9, -0.2, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.25)';
      ctx.beginPath(); ctx.ellipse(c - 3, c - 2, 7, 3.4, -0.3, 0, TAU); ctx.fill();
      ctx.strokeStyle = 'rgba(90,60,30,0.6)'; ctx.lineWidth = 1.6;
      for (let i = -1; i <= 1; i++) { ctx.beginPath(); ctx.moveTo(c + i * 6 - 3, c - 3); ctx.lineTo(c + i * 6 + 2, c + 1); ctx.stroke(); }
      break;
    }
    case 'scroll': {
      ctx.fillStyle = color;
      roundRect(ctx, c - 12, c - 12, 24, 24, 3); ctx.fill();
      ctx.fillStyle = 'rgba(120,90,50,0.6)';
      for (let i = 0; i < 4; i++) ctx.fillRect(c - 7, c - 7 + i * 5, 14 - (i % 2) * 5, 2);
      ctx.fillStyle = '#a8804a';
      ctx.fillRect(c - 14, c - 14, 28, 4); ctx.fillRect(c - 14, c + 10, 28, 4);
      break;
    }
    case 'mat': {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(c, c - 13); ctx.lineTo(c + 12, c - 5); ctx.lineTo(c + 8, c + 12);
      ctx.lineTo(c - 8, c + 12); ctx.lineTo(c - 12, c - 5);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.28)';
      ctx.beginPath(); ctx.moveTo(c, c - 13); ctx.lineTo(c + 12, c - 5); ctx.lineTo(c, c + 1); ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(0,0,0,0.18)';
      ctx.beginPath(); ctx.moveTo(c, c + 1); ctx.lineTo(c + 8, c + 12); ctx.lineTo(c - 8, c + 12); ctx.closePath(); ctx.fill();
      break;
    }
    case 'key': {
      ctx.strokeStyle = color; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.arc(c - 5, c - 5, 6, 0, TAU); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(c - 1, c - 1); ctx.lineTo(c + 11, c + 12); ctx.stroke();
      ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(c + 5, c + 6); ctx.lineTo(c + 9, c + 2); ctx.stroke();
      break;
    }
    case 'gem': {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(c, c - 14); ctx.lineTo(c + 12, c - 4); ctx.lineTo(c + 7, c + 13);
      ctx.lineTo(c - 7, c + 13); ctx.lineTo(c - 12, c - 4);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.45)';
      ctx.beginPath(); ctx.moveTo(c, c - 14); ctx.lineTo(c + 12, c - 4); ctx.lineTo(c, c - 1); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,0.5)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(c - 12, c - 4); ctx.lineTo(c, c - 1); ctx.lineTo(c + 7, c + 13); ctx.stroke();
      break;
    }
    case 'gold': {
      for (let i = 0; i < 3; i++) {
        ctx.fillStyle = '#e8b93a';
        ctx.beginPath(); ctx.ellipse(c + (i - 1) * 6, c + 6 - i * 4, 9, 5, 0, 0, TAU); ctx.fill();
        ctx.fillStyle = '#ffd76a';
        ctx.beginPath(); ctx.ellipse(c + (i - 1) * 6, c + 4 - i * 4, 9, 5, 0, 0, TAU); ctx.fill();
      }
      break;
    }
    // ——— spells
    case 'fire': {
      ctx.fillStyle = '#ff7a2e';
      ctx.beginPath();
      ctx.moveTo(c, c - 16);
      ctx.quadraticCurveTo(c + 12, c - 2, c + 8, c + 6);
      ctx.quadraticCurveTo(c + 6, c + 15, c, c + 15);
      ctx.quadraticCurveTo(c - 6, c + 15, c - 8, c + 6);
      ctx.quadraticCurveTo(c - 12, c - 2, c, c - 16);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#ffd76a';
      ctx.beginPath();
      ctx.moveTo(c, c - 4); ctx.quadraticCurveTo(c + 6, c + 4, c, c + 13);
      ctx.quadraticCurveTo(c - 6, c + 4, c, c - 4); ctx.closePath(); ctx.fill();
      break;
    }
    case 'ice': {
      ctx.strokeStyle = '#9fe8ff'; ctx.lineWidth = 3;
      for (let i = 0; i < 3; i++) {
        const a = (i / 3) * Math.PI;
        ctx.beginPath();
        ctx.moveTo(c - Math.cos(a) * 14, c - Math.sin(a) * 14);
        ctx.lineTo(c + Math.cos(a) * 14, c + Math.sin(a) * 14);
        ctx.stroke();
      }
      ctx.lineWidth = 2;
      for (let i = 0; i < 6; i++) {
        const a = (i / 6) * TAU;
        const x = c + Math.cos(a) * 9, y = c + Math.sin(a) * 9;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + Math.cos(a + 0.7) * 5, y + Math.sin(a + 0.7) * 5); ctx.stroke();
      }
      break;
    }
    case 'bolt': {
      ctx.fillStyle = '#ffe14a';
      ctx.beginPath();
      ctx.moveTo(c + 4, c - 16); ctx.lineTo(c - 9, c + 2); ctx.lineTo(c - 1, c + 2);
      ctx.lineTo(c - 5, c + 16); ctx.lineTo(c + 10, c - 3); ctx.lineTo(c + 1, c - 3);
      ctx.closePath(); ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,0.7)'; ctx.lineWidth = 1.4; ctx.stroke();
      break;
    }
    case 'heal': {
      ctx.fillStyle = '#7fe89a';
      roundRect(ctx, c - 4, c - 14, 8, 28, 3); ctx.fill();
      roundRect(ctx, c - 14, c - 4, 28, 8, 3); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.55)';
      roundRect(ctx, c - 2, c - 12, 3, 24, 2); ctx.fill();
      break;
    }
    case 'wind': {
      ctx.strokeStyle = '#bff0d8'; ctx.lineWidth = 3; ctx.lineCap = 'round';
      for (let i = 0; i < 3; i++) {
        const y = c - 8 + i * 8;
        ctx.beginPath();
        ctx.moveTo(c - 13, y);
        ctx.quadraticCurveTo(c + 6, y - 4, c + 8, y + 2);
        ctx.quadraticCurveTo(c + 10, y + 7, c + 3, y + 5);
        ctx.stroke();
      }
      break;
    }
    default: {
      ctx.fillStyle = color || '#888';
      ctx.beginPath(); ctx.arc(c, c, 12, 0, TAU); ctx.fill();
    }
  }
}

export function getIcon(icon, color = '#cfd6dd') {
  const key = icon + '|' + color;
  let cv = cache.get(key);
  if (!cv) {
    const { canvas, ctx } = makeCanvas(S, S);
    drawIcon(ctx, icon, color);
    cv = canvas;
    cache.set(key, cv);
  }
  return cv;
}

export function iconFor(item) {
  if (!item) return null;
  const color = item.color || (item.type === 'weapon' || item.type === 'armor' ? rarityColor(item) : '#cfd6dd');
  return getIcon(item.icon || 'mat', color);
}
