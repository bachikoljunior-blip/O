// 入力：タッチ（仮想スティック + ボタン）/ キーボード + マウス / ゲームパッド

const KEY_MAP = {
  KeyW: 'up', KeyS: 'down', KeyA: 'left', KeyD: 'right',
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  Space: 'dodge', ShiftLeft: 'sprint', ShiftRight: 'sprint',
  KeyJ: 'attack', KeyK: 'heavy', KeyL: 'block', KeyQ: 'lock',
  KeyE: 'interact', KeyR: 'item', KeyF: 'spell', KeyC: 'jump', KeyG: 'mount',
  KeyV: 'art',
  Escape: 'menu', KeyM: 'map', Tab: 'inventory',
};

/**
 * 先行入力を受ける行動。
 *
 * 弱攻撃を1回振ると再び動けるまで 31〜44 フレーム（0.5〜0.7 秒）掛かる。
 * 指で遊ぶと、その間に押した2撃目はまるごと消えていた（+2f〜+30f の
 * どこで押しても出た攻撃は 1 回きり）。押した事実を短いあいだ持っておき、
 * 動けた最初のフレームで一度だけ出す。
 *
 * 受けるのは「動けるときだけ実行される」行動に限る。lock / mount / interact /
 * menu / map は毎フレーム見られていたり呼び口が複数あったりするので入れない。
 */
// 先行入力に入れるもの。
// 受け（block/パリィ）は入れない。あれは「見てから合わせる」入力で、
// 早すぎた押しは失敗すべきもの。緩衝すると、外した押しが最大 0.25 秒後に
// 成功に化けて、第27周で整えた読み合いが消える。
const BUFFERED = new Set(['attack', 'heavy', 'dodge', 'spell', 'art', 'item', 'jump']);

// 先行入力の寿命。フレーム数と実時間の両方で切る。
// SwiftShader だと 1 フレームが 0.5 秒あるので、フレーム数だけだと持ちすぎる。
const BUF_FRAMES = 15;   // 60fps で 0.25 秒
const BUF_MS = 250;

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
    // スティックを掴める側。左利き配置では操作ボタンが左に来るので右へ移す
    this.stickZone = 0.45;
    this.stickRight = false;

    this.frameNo = 0;
    this._buf = new Map();      // name -> { f: 期限フレーム, t: 期限時刻 }
    this._holdArmed = new Map(); // ボタン名 -> { act, fired }

    this._bindKeyboard();
    this._bindMouse();
    this._bindTouch();
  }

  down(n) { return this.btn.has(n); }
  released(n) { return this.btnReleased.has(n); }

  /**
   * 押されたか。先行入力を持っていればここで消費する（1回だけ真を返す）。
   */
  pressed(n) {
    const b = this._buf.get(n);
    if (b) {
      this._buf.delete(n);
      if (this.frameNo <= b.f && performance.now() <= b.t) {
        for (const h of this._holdArmed.values()) if (h.act === n) h.fired = true;
        return true;
      }
    }
    return this.btnPressed.has(n);
  }

  press(n) {
    const fresh = !this.btn.has(n);
    this.btn.add(n);
    if (!fresh) return;   // ゲームパッドは押しっぱなしでも毎フレーム press() を呼ぶ
    this.btnPressed.add(n);
    if (BUFFERED.has(n)) {
      this._buf.set(n, { f: this.frameNo + BUF_FRAMES, t: performance.now() + BUF_MS });
    }
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
      // 裏に回っている間の先行入力は捨てる。戻った瞬間に振り始めない
      this._buf.clear();
      this._holdArmed.clear();
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
    const zone = innerWidth * this.stickZone;
    for (const t of e.changedTouches) {
      const onStickSide = this.stickRight
        ? t.clientX > innerWidth - zone
        : t.clientX < zone;
      if (onStickSide && !this.stick.active) {
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
        if (d > r) {
          // 半径を越えたら台座が指に引きずられる。倒しきったまま走っていても、
          // 少し戻すだけで向きが変わる（追わないと 200px 倒した分を戻すまで
          // 入力が振り切れたままになる）
          const k = (d - r) / d;
          this.stick.ox += dx * k;
          this.stick.oy += dy * k;
          dx -= dx * k; dy -= dy * k;
          this.onStickBase?.(this.stick.ox, this.stick.oy);
        }
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
            // 押している間は先行入力として持ち続ける。弱攻撃の硬直（0.73 秒）が
            // 明けるまで待てないと、長押しの強攻撃は一度も出ない
            this._holdArmed.set(name, { act: opts.hold, fired: false });
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
        this._holdArmed.delete(name);
        this._buf.delete(opts.hold);
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
    this.frameNo++;

    // 長押し中の行動は、離すまで先行入力を生かしておく（出るのは一度だけ）
    for (const [name, h] of this._holdArmed) {
      if (!this.btn.has(name)) { this._holdArmed.delete(name); continue; }
      if (!h.fired) {
        this._buf.set(h.act, { f: this.frameNo, t: performance.now() + 1e6 });
      }
    }

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
        [9, 'menu'], [8, 'map'], [12, 'interact'], [13, 'mount'],
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
