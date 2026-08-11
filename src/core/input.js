// input.js — unified touch / mouse / keyboard / gamepad input.

import { clamp, dist } from './util.js';

export class Input {
  constructor(canvas) {
    this.canvas = canvas;
    this.pointers = new Map();
    this.keys = new Set();
    this.keysPressed = new Set();
    this.buttons = [];              // [{id,x,y,r}] registered by the HUD
    this.buttonDownIds = new Set();
    this.buttonPressedIds = new Set();
    this.buttonReleasedIds = new Set();
    this.stick = { x: 0, y: 0, active: false, ox: 0, oy: 0, cx: 0, cy: 0, id: null };
    this.stickRadius = 62;
    this.stickZone = { x: 0, y: 0, w: 0, h: 0 };
    this.dpr = 1;
    /** UI pointer state for menus */
    this.ui = { x: 0, y: 0, down: false, pressed: false, released: false, dragY: 0, dragX: 0, id: null, moved: false, wheel: 0 };
    this.anyPress = false;
    this.usingTouch = false;
    this._bind();
  }

  _pos(e) {
    const r = this.canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - r.left) / r.width) * this.canvas.width / this.dpr,
      y: ((e.clientY - r.top) / r.height) * this.canvas.height / this.dpr,
    };
  }

  _bind() {
    const c = this.canvas;
    const opts = { passive: false };
    c.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      if (e.pointerType === 'touch') this.usingTouch = true;
      c.setPointerCapture?.(e.pointerId);
      const p = this._pos(e);
      const rec = { id: e.pointerId, x: p.x, y: p.y, x0: p.x, y0: p.y, px: p.x, py: p.y, t0: performance.now(), role: null, moved: false };
      this.pointers.set(e.pointerId, rec);
      this.anyPress = true;
      this._assign(rec);
    }, opts);

    c.addEventListener('pointermove', (e) => {
      const rec = this.pointers.get(e.pointerId);
      if (!rec) return;
      e.preventDefault();
      const p = this._pos(e);
      rec.px = rec.x; rec.py = rec.y;
      rec.x = p.x; rec.y = p.y;
      if (dist(rec.x, rec.y, rec.x0, rec.y0) > 8) rec.moved = true;
      if (rec.role === 'stick') this._updateStick(rec);
      if (rec.role === 'ui') {
        this.ui.x = rec.x; this.ui.y = rec.y;
        this.ui.dragY += rec.y - rec.py;
        this.ui.dragX += rec.x - rec.px;
        this.ui.moved = rec.moved;
      }
    }, opts);

    const up = (e) => {
      const rec = this.pointers.get(e.pointerId);
      if (!rec) return;
      e.preventDefault?.();
      if (rec.role === 'stick') {
        this.stick.active = false; this.stick.x = 0; this.stick.y = 0; this.stick.id = null;
      } else if (rec.role && rec.role.startsWith('btn:')) {
        const id = rec.role.slice(4);
        this.buttonDownIds.delete(id);
        this.buttonReleasedIds.add(id);
      } else if (rec.role === 'ui') {
        this.ui.down = false;
        this.ui.released = true;
        this.ui.id = null;
      }
      this.pointers.delete(e.pointerId);
    };
    c.addEventListener('pointerup', up, opts);
    c.addEventListener('pointercancel', up, opts);
    c.addEventListener('lostpointercapture', up, opts);
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    c.addEventListener('wheel', (e) => { e.preventDefault(); this.ui.wheel += e.deltaY; }, opts);

    addEventListener('keydown', (e) => {
      if (e.repeat) return;
      const k = e.key.toLowerCase();
      this.keys.add(k);
      this.keysPressed.add(k);
      this.anyPress = true;
      if ([' ', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'tab'].includes(k)) e.preventDefault();
    });
    addEventListener('keyup', (e) => this.keys.delete(e.key.toLowerCase()));
    // Mobile browsers do not always dispatch pointerup when the app is
    // backgrounded or the system takes over the gesture. Clear every held
    // control so movement, guard, and menu presses cannot remain latched on
    // resume.
    addEventListener('blur', () => this.cancelActive());
  }

  cancelActive() {
    this.keys.clear();
    this.keysPressed.clear();
    this.pointers.clear();
    this.buttonDownIds.clear();
    this.buttonPressedIds.clear();
    this.buttonReleasedIds.clear();
    this.stick.active = false;
    this.stick.x = 0;
    this.stick.y = 0;
    this.stick.id = null;
    this.ui.down = false;
    this.ui.pressed = false;
    this.ui.released = false;
    this.ui.dragX = 0;
    this.ui.dragY = 0;
    this.ui.id = null;
    this.ui.moved = false;
    this.anyPress = false;
    const g = this._gamepad();
    this._gpPrev = g ? g.buttons.map((button) => button.pressed) : null;
  }

  _assign(rec) {
    // buttons first (registered by the HUD)
    for (const b of this.buttons) {
      if (b.enabled === false) continue;
      if (dist(rec.x, rec.y, b.x, b.y) <= b.r) {
        rec.role = 'btn:' + b.id;
        this.buttonDownIds.add(b.id);
        this.buttonPressedIds.add(b.id);
        return;
      }
    }
    if (this.uiMode) {
      rec.role = 'ui';
      this.ui.x = rec.x; this.ui.y = rec.y;
      this.ui.down = true; this.ui.pressed = true; this.ui.id = rec.id;
      this.ui.dragX = this.ui.dragY = 0;
      this.ui.moved = false;
      return;
    }
    const z = this.stickZone;
    if (!this.stick.active && rec.x >= z.x && rec.x <= z.x + z.w && rec.y >= z.y && rec.y <= z.y + z.h) {
      rec.role = 'stick';
      this.stick.active = true;
      this.stick.id = rec.id;
      this.stick.ox = rec.x0; this.stick.oy = rec.y0;
      this.stick.cx = rec.x; this.stick.cy = rec.y;
      this._updateStick(rec);
      return;
    }
    rec.role = 'ui';
    this.ui.x = rec.x; this.ui.y = rec.y;
    this.ui.down = true; this.ui.pressed = true; this.ui.id = rec.id;
    this.ui.dragX = this.ui.dragY = 0;
    this.ui.moved = false;
  }

  _updateStick(rec) {
    const R = this.stickRadius;
    let dx = rec.x - this.stick.ox, dy = rec.y - this.stick.oy;
    const d = Math.hypot(dx, dy);
    if (d > R) {
      // drag the origin so the stick never sticks at the rim
      this.stick.ox += (dx / d) * (d - R);
      this.stick.oy += (dy / d) * (d - R);
      dx = rec.x - this.stick.ox; dy = rec.y - this.stick.oy;
    }
    this.stick.cx = rec.x; this.stick.cy = rec.y;
    const dead = 6;
    const m = Math.hypot(dx, dy);
    if (m < dead) { this.stick.x = 0; this.stick.y = 0; return; }
    const k = clamp((m - dead) / (R - dead), 0, 1);
    this.stick.x = (dx / m) * k;
    this.stick.y = (dy / m) * k;
  }

  /** Called by the HUD each frame to declare touch button hit areas. */
  setButtons(list) { this.buttons = list; }
  setStickZone(x, y, w, h) { this.stickZone = { x, y, w, h }; }
  setStickRadius(radius) { this.stickRadius = clamp(radius, 48, 76); }
  setUIMode(on) { this.uiMode = on; }

  down(id) { return this.buttonDownIds.has(id); }
  pressed(id) { return this.buttonPressedIds.has(id); }
  released(id) { return this.buttonReleasedIds.has(id); }
  key(k) { return this.keys.has(k); }
  keyPressed(k) { return this.keysPressed.has(k); }

  /** Movement vector from stick + keyboard + gamepad. */
  moveVector() {
    let x = this.stick.x, y = this.stick.y;
    let kx = 0, ky = 0;
    if (this.keys.has('a') || this.keys.has('arrowleft')) kx -= 1;
    if (this.keys.has('d') || this.keys.has('arrowright')) kx += 1;
    if (this.keys.has('w') || this.keys.has('arrowup')) ky -= 1;
    if (this.keys.has('s') || this.keys.has('arrowdown')) ky += 1;
    if (kx || ky) {
      const m = Math.hypot(kx, ky);
      x = kx / m; y = ky / m;
    }
    const gp = this._gamepad();
    if (gp) {
      const ax = gp.axes[0] || 0, ay = gp.axes[1] || 0;
      if (Math.hypot(ax, ay) > 0.18) { x = ax; y = ay; }
    }
    const m = Math.hypot(x, y);
    if (m > 1) { x /= m; y /= m; }
    return { x, y, m: Math.min(1, m) };
  }

  _gamepad() {
    if (typeof navigator === 'undefined' || !navigator.getGamepads) return null;
    const list = navigator.getGamepads();
    for (const g of list) if (g && g.connected) return g;
    return null;
  }

  gamepadPressed(index) {
    const g = this._gamepad();
    if (!g) return false;
    const b = g.buttons[index];
    const was = this._gpPrev && this._gpPrev[index];
    return !!(b && b.pressed && !was);
  }
  gamepadDown(index) {
    const g = this._gamepad();
    return !!(g && g.buttons[index] && g.buttons[index].pressed);
  }

  endFrame() {
    this.keysPressed.clear();
    this.buttonPressedIds.clear();
    this.buttonReleasedIds.clear();
    this.ui.pressed = false;
    this.ui.released = false;
    this.ui.dragY = 0;
    this.ui.dragX = 0;
    this.ui.wheel = 0;
    this.anyPress = false;
    const g = this._gamepad();
    this._gpPrev = g ? g.buttons.map((b) => b.pressed) : null;
  }
}
