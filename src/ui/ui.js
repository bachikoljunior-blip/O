// ui.js — tiny immediate-mode canvas UI toolkit (panels, buttons, scroll lists).

import { clamp, lerp, roundRect, TAU } from '../core/util.js';
import { audio } from '../core/audio.js';

export const FONT = '"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", Meiryo, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export const COL = {
  ink: '#f2ece0',
  ink2: '#bdb4a4',
  dim: '#8a8377',
  gold: '#e8c874',
  panel: 'rgba(26,22,20,0.92)',
  panel2: 'rgba(38,32,28,0.96)',
  edge: 'rgba(228,206,160,0.35)',
  edge2: 'rgba(228,206,160,0.18)',
  hp: '#d9483f',
  mp: '#4f8ede',
  sta: '#c8b45a',
  xp: '#8fd0ff',
  good: '#7fd77f',
  bad: '#e0524a',
};

export class UI {
  constructor() {
    this.scrolls = new Map();
    this.hot = null;
    this.activeScroll = null;
  }

  begin(ctx, input, w, h) {
    this.ctx = ctx; this.input = input; this.w = w; this.h = h;
    this.hot = null;
    this.clicked = false;
  }

  // ——— primitives

  font(size, weight = 600) {
    this.ctx.font = `${weight} ${size}px ${FONT}`;
  }

  text(str, x, y, o = {}) {
    const ctx = this.ctx;
    ctx.save();
    this.font(o.size || 16, o.weight || 600);
    ctx.textAlign = o.align || 'left';
    ctx.textBaseline = o.baseline || 'middle';
    if (o.shadow !== false) {
      ctx.lineWidth = o.shadowW || 3.5;
      ctx.strokeStyle = o.shadowCol || 'rgba(0,0,0,0.55)';
      ctx.lineJoin = 'round';
      ctx.strokeText(str, x, y);
    }
    ctx.fillStyle = o.col || COL.ink;
    ctx.globalAlpha = o.alpha ?? 1;
    ctx.fillText(str, x, y);
    ctx.restore();
  }

  measure(str, size = 16, weight = 600) {
    this.font(size, weight);
    return this.ctx.measureText(str).width;
  }

  wrap(str, maxW, size = 16, weight = 600) {
    this.font(size, weight);
    const ctx = this.ctx;
    const out = [];
    let line = '';
    for (const ch of str) {
      if (ch === '\n') { out.push(line); line = ''; continue; }
      const test = line + ch;
      if (ctx.measureText(test).width > maxW && line) { out.push(line); line = ch; }
      else line = test;
    }
    if (line) out.push(line);
    return out;
  }

  paragraph(str, x, y, maxW, o = {}) {
    const lines = this.wrap(str, maxW, o.size || 16, o.weight || 500);
    const lh = o.lh || (o.size || 16) * 1.55;
    lines.forEach((l, i) => this.text(l, x, y + i * lh, o));
    return lines.length * lh;
  }

  panel(x, y, w, h, o = {}) {
    const ctx = this.ctx;
    ctx.save();
    const r = o.r ?? 14;
    roundRect(ctx, x, y, w, h, r);
    ctx.fillStyle = o.fill || COL.panel;
    ctx.fill();
    if (o.grad !== false) {
      const g = ctx.createLinearGradient(0, y, 0, y + h);
      g.addColorStop(0, 'rgba(255,240,210,0.07)');
      g.addColorStop(0.5, 'rgba(255,240,210,0.01)');
      g.addColorStop(1, 'rgba(0,0,0,0.12)');
      ctx.fillStyle = g;
      ctx.fill();
    }
    ctx.lineWidth = o.lw ?? 1.5;
    ctx.strokeStyle = o.edge || COL.edge;
    ctx.stroke();
    ctx.restore();
  }

  bar(x, y, w, h, k, col, o = {}) {
    const ctx = this.ctx;
    ctx.save();
    roundRect(ctx, x, y, w, h, h / 2);
    ctx.fillStyle = o.bg || 'rgba(0,0,0,0.55)';
    ctx.fill();
    if (o.ghost != null && o.ghost > k) {
      roundRect(ctx, x + 1, y + 1, Math.max(0, (w - 2) * clamp(o.ghost, 0, 1)), h - 2, (h - 2) / 2);
      ctx.fillStyle = 'rgba(255,255,255,0.28)';
      ctx.fill();
    }
    if (k > 0) {
      roundRect(ctx, x + 1, y + 1, Math.max(2, (w - 2) * clamp(k, 0, 1)), h - 2, (h - 2) / 2);
      const g = ctx.createLinearGradient(0, y, 0, y + h);
      g.addColorStop(0, o.top || 'rgba(255,255,255,0.4)');
      g.addColorStop(0.4, col);
      g.addColorStop(1, o.bot || 'rgba(0,0,0,0.25)');
      ctx.fillStyle = col;
      ctx.fill();
      ctx.fillStyle = g;
      ctx.globalCompositeOperation = 'overlay';
      ctx.fill();
      ctx.globalCompositeOperation = 'source-over';
    }
    ctx.strokeStyle = 'rgba(255,255,255,0.14)';
    ctx.lineWidth = 1;
    roundRect(ctx, x, y, w, h, h / 2);
    ctx.stroke();
    ctx.restore();
  }

  /** Returns true on release inside the rect. */
  button(x, y, w, h, label, o = {}) {
    const ctx = this.ctx;
    const inp = this.input;
    const inside = inp.ui.x >= x && inp.ui.x <= x + w && inp.ui.y >= y && inp.ui.y <= y + h;
    const held = inside && inp.ui.down;
    const clicked = !o.disabled && inside && inp.ui.released && !inp.ui.moved;
    if (this.clip && (y + h < this.clip.y || y > this.clip.y + this.clip.h)) return false;
    ctx.save();
    const r = o.r ?? 12;
    roundRect(ctx, x, y + (held ? 1.5 : 0), w, h, r);
    const base = o.disabled ? 'rgba(40,36,32,0.7)' : o.primary ? 'rgba(120,86,44,0.95)' : o.danger ? 'rgba(120,40,36,0.9)' : 'rgba(52,45,40,0.95)';
    ctx.fillStyle = held ? 'rgba(90,74,52,0.98)' : base;
    ctx.fill();
    const g = ctx.createLinearGradient(0, y, 0, y + h);
    g.addColorStop(0, 'rgba(255,240,210,0.12)');
    g.addColorStop(1, 'rgba(0,0,0,0.18)');
    ctx.fillStyle = g;
    ctx.fill();
    ctx.strokeStyle = o.disabled ? 'rgba(200,180,140,0.12)' : o.selected ? COL.gold : COL.edge;
    ctx.lineWidth = o.selected ? 2.2 : 1.4;
    ctx.stroke();
    ctx.restore();
    if (label) {
      this.text(label, o.align === 'left' ? x + 14 : x + w / 2, y + h / 2 + (held ? 1.5 : 0), {
        size: o.size || 16, align: o.align || 'center', col: o.disabled ? COL.dim : o.col || COL.ink, weight: 700,
      });
    }
    if (clicked) { audio.sfx(o.big ? 'uiBig' : 'ui'); this.clicked = true; }
    return clicked;
  }

  iconButton(x, y, r, drawFn, o = {}) {
    const ctx = this.ctx;
    const inp = this.input;
    const d = Math.hypot(inp.ui.x - x, inp.ui.y - y);
    const inside = d <= r;
    const held = inside && inp.ui.down;
    const clicked = !o.disabled && inside && inp.ui.released && !inp.ui.moved;
    ctx.save();
    ctx.beginPath(); ctx.arc(x, y, r, 0, TAU);
    ctx.fillStyle = held ? 'rgba(90,74,52,0.95)' : o.fill || 'rgba(34,29,26,0.85)';
    ctx.fill();
    ctx.strokeStyle = o.selected ? COL.gold : COL.edge;
    ctx.lineWidth = o.selected ? 2.2 : 1.4;
    ctx.stroke();
    ctx.translate(x, y);
    drawFn(ctx);
    ctx.restore();
    if (clicked) audio.sfx('ui');
    return clicked;
  }

  /** Scroll container. Call endScroll() after drawing content. Returns scroll offset. */
  beginScroll(id, x, y, w, h, contentH) {
    const ctx = this.ctx;
    let s = this.scrolls.get(id);
    if (!s) { s = { off: 0, v: 0 }; this.scrolls.set(id, s); }
    const inp = this.input;
    const inside = inp.ui.x >= x && inp.ui.x <= x + w && inp.ui.y >= y && inp.ui.y <= y + h;
    const maxOff = Math.max(0, contentH - h);
    if (inside && inp.ui.down && inp.ui.dragY) {
      s.off -= inp.ui.dragY;
      s.v = -inp.ui.dragY * 0.9;
    } else {
      s.off += s.v;
      s.v *= 0.90;
      if (Math.abs(s.v) < 0.2) s.v = 0;
    }
    if (inside && inp.ui.wheel) { s.off += inp.ui.wheel * 0.6; s.v = 0; }
    if (s.off < 0) { s.off = lerp(s.off, 0, 0.35); s.v = 0; }
    if (s.off > maxOff) { s.off = lerp(s.off, maxOff, 0.35); s.v = 0; }
    ctx.save();
    roundRect(ctx, x, y, w, h, 10);
    ctx.clip();
    this.clip = { x, y, w, h };
    this.scrollMax = maxOff;
    this.scrollRect = { x, y, w, h, contentH };
    return s.off;
  }

  endScroll(id) {
    const ctx = this.ctx;
    const s = this.scrolls.get(id);
    const r = this.scrollRect;
    ctx.restore();
    this.clip = null;
    // scrollbar
    if (r && r.contentH > r.h) {
      const k = r.h / r.contentH;
      const bh = Math.max(24, r.h * k);
      const by = r.y + (r.h - bh) * clamp(s.off / Math.max(1, r.contentH - r.h), 0, 1);
      ctx.save();
      roundRect(ctx, r.x + r.w - 5, by, 3, bh, 1.5);
      ctx.fillStyle = 'rgba(230,214,180,0.35)';
      ctx.fill();
      ctx.restore();
    }
  }

  resetScroll(id) {
    const s = this.scrolls.get(id);
    if (s) { s.off = 0; s.v = 0; }
  }

  dim(alpha = 0.6) {
    const ctx = this.ctx;
    ctx.fillStyle = `rgba(6,5,8,${alpha})`;
    ctx.fillRect(0, 0, this.w, this.h);
  }

  /** Decorative header with a rule line. */
  header(str, x, y, w, o = {}) {
    this.text(str, x, y, { size: o.size || 20, col: o.col || COL.gold, weight: 800 });
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = 'rgba(228,206,160,0.25)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, y + (o.size || 20) * 0.75);
    ctx.lineTo(x + w, y + (o.size || 20) * 0.75);
    ctx.stroke();
    ctx.restore();
  }
}
