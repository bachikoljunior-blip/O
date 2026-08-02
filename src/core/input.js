// 入力：タッチ（仮想スティック + ボタン）/ キーボード + マウス / ゲームパッド

const KEY_MAP = {
  KeyW: 'up', KeyS: 'down', KeyA: 'left', KeyD: 'right',
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  Space: 'dodge', ShiftLeft: 'sprint', ShiftRight: 'sprint',
  KeyJ: 'attack', KeyK: 'heavy', KeyL: 'block', KeyQ: 'lock',
  KeyE: 'interact', KeyR: 'item', KeyF: 'spell', KeyC: 'jump',
  Escape: 'menu', KeyM: 'map', Tab: 'inventory',
};

export class Input {
  constructor(canvas) {
    this.canvas = canvas;
    this.move = { x: 0, y: 0 };
    this.look = { x: 0, y: 0 };
    this.btn = new Set();
    this.btnPressed = new Set();
    this.btnReleased = new Set();
    this.keys = new Set();
    this.stick = { active: false, id: -1, ox: 0, oy: 0, x: 0, y: 0, radius: 62 };
    this.lookTouch = { active: false, id: -1, lx: 0, ly: 0 };
    this.lookSensitivity = 0.0055;
    this.enabled = true;
    this.pointerLock = false;
    this.holdTimers = new Map();
    this.touchMode = false;

    this._bindKeyboard();
    this._bindMouse();
    this._bindTouch();
  }

  down(n) { return this.btn.has(n); }
  pressed(n) { return this.btnPressed.has(n); }
  released(n) { return this.btnReleased.has(n); }

  press(n) {
    if (!this.btn.has(n)) this.btnPressed.add(n);
    this.btn.add(n);
  }
  release(n) {
    if (this.btn.has(n)) this.btnReleased.add(n);
    this.btn.delete(n);
  }

  endFrame() {
    this.btnPressed.clear();
    this.btnReleased.clear();
    this.look.x = 0;
    this.look.y = 0;
  }

  /* ------------------------------------------------------ キーボード */
  _bindKeyboard() {
    addEventListener('keydown', (e) => {
      if (e.repeat) return;
      const m = KEY_MAP[e.code];
      if (!m) return;
      if (e.code === 'Tab' || e.code === 'Space') e.preventDefault();
      this.keys.add(m);
      if (['up', 'down', 'left', 'right'].includes(m)) return;
      if (m === 'sprint') { this.press('dodge'); this._sprintKey = true; return; }
      this.press(m);
    });
    addEventListener('keyup', (e) => {
      const m = KEY_MAP[e.code];
      if (!m) return;
      this.keys.delete(m);
      if (['up', 'down', 'left', 'right'].includes(m)) return;
      if (m === 'sprint') { this.release('dodge'); this._sprintKey = false; return; }
      this.release(m);
    });
    addEventListener('blur', () => {
      this.keys.clear();
      for (const b of [...this.btn]) this.release(b);
    });
  }

  _bindMouse() {
    const c = this.canvas;
    c.addEventListener('mousedown', (e) => {
      if (this.touchMode) return;
      if (e.button === 0) this.press('attack');
      if (e.button === 2) this.press('block');
      if (!this.pointerLock && c.requestPointerLock) {
        c.requestPointerLock();
      }
    });
    addEventListener('mouseup', (e) => {
      if (this.touchMode) return;
      if (e.button === 0) this.release('attack');
      if (e.button === 2) this.release('block');
    });
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    addEventListener('mousemove', (e) => {
      if (!this.pointerLock || this.touchMode) return;
      this.look.x += e.movementX * 0.0022;
      this.look.y += e.movementY * 0.0022;
    });
    document.addEventListener('pointerlockchange', () => {
      this.pointerLock = document.pointerLockElement === c;
    });
    c.addEventListener('wheel', (e) => {
      this.wheel = (this.wheel || 0) + Math.sign(e.deltaY);
      e.preventDefault();
    }, { passive: false });
  }

  /* ---------------------------------------------------------- タッチ */
  _bindTouch() {
    const c = this.canvas;
    const opts = { passive: false };
    c.addEventListener('touchstart', (e) => this._touchStart(e), opts);
    c.addEventListener('touchmove', (e) => this._touchMove(e), opts);
    c.addEventListener('touchend', (e) => this._touchEnd(e), opts);
    c.addEventListener('touchcancel', (e) => this._touchEnd(e), opts);
  }

  _touchStart(e) {
    this.touchMode = true;
    e.preventDefault();
    for (const t of e.changedTouches) {
      if (t.clientX < innerWidth * 0.45 && !this.stick.active) {
        this.stick.active = true;
        this.stick.id = t.identifier;
        this.stick.ox = t.clientX;
        this.stick.oy = t.clientY;
        this.stick.x = 0; this.stick.y = 0;
        this.onStickShow?.(t.clientX, t.clientY);
      } else if (!this.lookTouch.active) {
        this.lookTouch.active = true;
        this.lookTouch.id = t.identifier;
        this.lookTouch.lx = t.clientX;
        this.lookTouch.ly = t.clientY;
        this.lookTouch.moved = 0;
        this.lookTouch.t0 = performance.now();
      }
    }
  }

  _touchMove(e) {
    e.preventDefault();
    for (const t of e.changedTouches) {
      if (this.stick.active && t.identifier === this.stick.id) {
        let dx = t.clientX - this.stick.ox;
        let dy = t.clientY - this.stick.oy;
        const d = Math.hypot(dx, dy);
        const r = this.stick.radius;
        if (d > r) { dx = (dx / d) * r; dy = (dy / d) * r; }
        this.stick.x = dx / r;
        this.stick.y = dy / r;
        this.onStickMove?.(dx, dy);
      } else if (this.lookTouch.active && t.identifier === this.lookTouch.id) {
        const dx = t.clientX - this.lookTouch.lx;
        const dy = t.clientY - this.lookTouch.ly;
        this.look.x += dx * this.lookSensitivity;
        this.look.y += dy * this.lookSensitivity;
        this.lookTouch.moved += Math.hypot(dx, dy);
        this.lookTouch.lx = t.clientX;
        this.lookTouch.ly = t.clientY;
      }
    }
  }

  _touchEnd(e) {
    e.preventDefault();
    for (const t of e.changedTouches) {
      if (this.stick.active && t.identifier === this.stick.id) {
        this.stick.active = false;
        this.stick.id = -1;
        this.stick.x = 0; this.stick.y = 0;
        this.onStickHide?.();
      } else if (this.lookTouch.active && t.identifier === this.lookTouch.id) {
        this.lookTouch.active = false;
        this.lookTouch.id = -1;
      }
    }
  }

  /** HUD ボタンをバインド。長押しで hold アクションを発火できる */
  bindButton(el, name, opts = {}) {
    if (!el) return;
    const start = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      this.press(name);
      el.classList.add('active');
      if (opts.hold) {
        const timer = setTimeout(() => {
          if (this.btn.has(name)) {
            this.press(opts.hold);
            this.holdFired = this.holdFired || new Set();
            this.holdFired.add(name);
            el.classList.add('hold');
          }
        }, opts.holdMs || 260);
        this.holdTimers.set(name, timer);
      }
    };
    const end = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      this.release(name);
      if (opts.hold) {
        clearTimeout(this.holdTimers.get(name));
        this.release(opts.hold);
        this.holdFired?.delete(name);
      }
      el.classList.remove('active', 'hold');
    };
    el.addEventListener('touchstart', start, { passive: false });
    el.addEventListener('touchend', end, { passive: false });
    el.addEventListener('touchcancel', end, { passive: false });
    el.addEventListener('mousedown', (e) => { if (!this.touchMode) start(e); });
    el.addEventListener('mouseup', (e) => { if (!this.touchMode) end(e); });
    el.addEventListener('mouseleave', (e) => { if (!this.touchMode && this.btn.has(name)) end(e); });
  }

  /* ------------------------------------------------------ 毎フレーム */
  update() {
    // 移動ベクトル
    if (this.stick.active) {
      this.move.x = this.stick.x;
      this.move.y = this.stick.y;
    } else {
      let x = 0, y = 0;
      if (this.keys.has('left')) x -= 1;
      if (this.keys.has('right')) x += 1;
      if (this.keys.has('up')) y -= 1;
      if (this.keys.has('down')) y += 1;
      const l = Math.hypot(x, y);
      if (l > 1) { x /= l; y /= l; }
      this.move.x = x; this.move.y = y;
    }

    // ゲームパッド
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    for (const p of pads) {
      if (!p) continue;
      const dz = (v) => (Math.abs(v) < 0.18 ? 0 : v);
      const ax = dz(p.axes[0] || 0), ay = dz(p.axes[1] || 0);
      if (ax || ay) { this.move.x = ax; this.move.y = ay; }
      this.look.x += dz(p.axes[2] || 0) * 0.05;
      this.look.y += dz(p.axes[3] || 0) * 0.05;
      const map = [
        [0, 'jump'], [1, 'dodge'], [2, 'item'], [3, 'spell'],
        [4, 'block'], [5, 'attack'], [6, 'lock'], [7, 'heavy'],
        [9, 'menu'], [8, 'map'], [12, 'interact'],
      ];
      for (const [i, name] of map) {
        const b = p.buttons[i];
        if (!b) continue;
        if (b.pressed) this.press(name); else this.release(name);
      }
      break;
    }
  }
}
