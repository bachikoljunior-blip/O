// input.js — pointer, touch, keyboard and gamepad -> raw state.
// L6.
//
// The virtual stick maths is ported verbatim from the 2D build. It is the best
// eighteen lines in that codebase: a floating origin that DRAGS when the finger
// passes the rim, so the stick never pins at full deflection, plus a radial
// deadzone with rescale so small inputs are usable instead of snapping.
//
// The binding table is new. Previously key and pad bindings were hardcoded at
// each call site, so there was no rebinding and no single source of truth.

import { BTN } from '../sim/systems/intent.js';

const STICK_R = 62;
const DEADZONE = 6;

/** One place where every device maps to the same action bits. */
export const BINDINGS = {
  attack:   { keys: ['j', 'mouse0'], pad: [2], bit: BTN.ATTACK },
  heavy:    { keys: ['k'],           pad: [3], bit: BTN.HEAVY },
  dodge:    { keys: ['shift', ' '],  pad: [1], bit: BTN.DODGE },
  block:    { keys: ['l'],           pad: [6], bit: BTN.BLOCK },
  interact: { keys: ['e'],           pad: [0], bit: BTN.INTERACT },
  jump:     { keys: ['q'],           pad: [7], bit: BTN.JUMP },
  lock:     { keys: ['tab'],         pad: [10], bit: BTN.LOCK },
  sprint:   { keys: ['control'],     pad: [8], bit: BTN.SPRINT },
};

export function createInput(canvas) {
  const inp = {
    canvas,
    dpr: 1,
    pointers: new Map(),
    stick: { active: false, id: -1, ox: 0, oy: 0, x: 0, y: 0 },
    look: { id: -1, lastX: 0, lastY: 0, dx: 0, dy: 0 },
    keys: new Set(),
    buttons: new Set(),   // touch button ids currently pressed
    touchZones: [],       // {id, x, y, r} in CSS pixels, refreshed by the HUD

    held: 0, pressed: 0, released: 0, prevHeld: 0,
    camYaw: 0, camPitch: 0.20,
    hasTouch: false,
    _padPrev: [],
  };

  const pos = (e) => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  const hitButton = (x, y) => {
    for (const z of inp.touchZones) {
      const dx = x - z.x, dy = y - z.y;
      if (dx * dx + dy * dy <= z.r * z.r) return z.id;
    }
    return null;
  };

  const onDown = (e) => {
    inp.hasTouch = inp.hasTouch || e.pointerType === 'touch';
    const p = pos(e);
    canvas.setPointerCapture?.(e.pointerId);

    // Role is assigned ONCE on press and is sticky, so a finger cannot steal
    // the stick from another mid-drag.
    const btn = hitButton(p.x, p.y);
    if (btn) {
      inp.pointers.set(e.pointerId, { role: 'btn', id: btn });
      inp.buttons.add(btn);
    } else if (p.x < canvas.clientWidth * 0.5) {
      inp.pointers.set(e.pointerId, { role: 'stick' });
      inp.stick.active = true;
      inp.stick.id = e.pointerId;
      inp.stick.ox = p.x; inp.stick.oy = p.y;
      inp.stick.x = 0; inp.stick.y = 0;
    } else {
      inp.pointers.set(e.pointerId, { role: 'look' });
      inp.look.id = e.pointerId;
      inp.look.lastX = p.x; inp.look.lastY = p.y;
    }
    e.preventDefault();
  };

  const onMove = (e) => {
    const rec = inp.pointers.get(e.pointerId);
    if (!rec) return;
    const p = pos(e);

    if (rec.role === 'stick') {
      let dx = p.x - inp.stick.ox;
      let dy = p.y - inp.stick.oy;
      const d = Math.hypot(dx, dy);
      // Origin drag: past the rim, pull the origin along instead of clamping.
      if (d > STICK_R) {
        inp.stick.ox += (dx / d) * (d - STICK_R);
        inp.stick.oy += (dy / d) * (d - STICK_R);
        dx = p.x - inp.stick.ox;
        dy = p.y - inp.stick.oy;
      }
      const m = Math.hypot(dx, dy);
      if (m < DEADZONE) { inp.stick.x = 0; inp.stick.y = 0; }
      else {
        // Radial deadzone WITH rescale, so the usable range starts at zero.
        const k = Math.min(1, (m - DEADZONE) / (STICK_R - DEADZONE));
        inp.stick.x = (dx / m) * k;
        inp.stick.y = (dy / m) * k;
      }
    } else if (rec.role === 'look') {
      inp.look.dx += p.x - inp.look.lastX;
      inp.look.dy += p.y - inp.look.lastY;
      inp.look.lastX = p.x; inp.look.lastY = p.y;
    }
    e.preventDefault();
  };

  const onUp = (e) => {
    const rec = inp.pointers.get(e.pointerId);
    if (rec) {
      if (rec.role === 'stick') { inp.stick.active = false; inp.stick.x = 0; inp.stick.y = 0; }
      if (rec.role === 'btn') inp.buttons.delete(rec.id);
      if (rec.role === 'look') inp.look.id = -1;
      inp.pointers.delete(e.pointerId);
    }
    e.preventDefault();
  };

  canvas.addEventListener('pointerdown', onDown, { passive: false });
  canvas.addEventListener('pointermove', onMove, { passive: false });
  canvas.addEventListener('pointerup', onUp, { passive: false });
  canvas.addEventListener('pointercancel', onUp, { passive: false });
  canvas.addEventListener('lostpointercapture', onUp, { passive: false });

  window.addEventListener('keydown', (e) => {
    inp.keys.add(e.key.toLowerCase());
    if ([' ', 'tab'].includes(e.key.toLowerCase())) e.preventDefault();
  });
  window.addEventListener('keyup', (e) => inp.keys.delete(e.key.toLowerCase()));
  window.addEventListener('blur', () => { inp.keys.clear(); inp.buttons.clear(); });

  return inp;
}

/** Sample every source into one held-bit mask. Called once per frame. */
export function beginFrame(inp) {
  let held = 0;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const pad = pads && pads[0];

  for (const [name, b] of Object.entries(BINDINGS)) {
    let on = inp.buttons.has(name);
    if (!on) for (const k of b.keys) if (inp.keys.has(k)) { on = true; break; }
    if (!on && pad) for (const i of b.pad) if (pad.buttons[i] && pad.buttons[i].pressed) { on = true; break; }
    if (on) held |= b.bit;
  }

  inp.pressed = held & ~inp.prevHeld;
  inp.released = ~held & inp.prevHeld;
  inp.held = held;
  inp.prevHeld = held;

  // Camera look, from drag or the right pad stick.
  inp.camYaw -= inp.look.dx * 0.005;
  inp.camPitch = Math.max(-0.35, Math.min(1.05, inp.camPitch + inp.look.dy * 0.004));
  inp.look.dx = 0; inp.look.dy = 0;
  if (pad && pad.axes.length >= 4) {
    const ax = pad.axes[2], ay = pad.axes[3];
    if (Math.hypot(ax, ay) > 0.18) {
      inp.camYaw -= ax * 0.045;
      inp.camPitch = Math.max(-0.35, Math.min(1.05, inp.camPitch + ay * 0.03));
    }
  }
}

/**
 * Produce the immutable Intent for one fixed substep.
 *
 * Edge bits go to substep 0 ONLY. Without that, a frame that runs three
 * substeps fires a single button press three times — the classic fixed-step
 * input bug.
 */
export function sampleForTick(inp, intent, substep) {
  let mx = inp.stick.x, my = inp.stick.y;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const pad = pads && pads[0];
  if (pad && pad.axes.length >= 2 && Math.hypot(pad.axes[0], pad.axes[1]) > 0.18) {
    mx = pad.axes[0]; my = pad.axes[1];
  }
  if (!inp.stick.active && !pad) {
    // Keyboard fallback.
    mx = (inp.keys.has('d') ? 1 : 0) - (inp.keys.has('a') ? 1 : 0);
    my = (inp.keys.has('s') ? 1 : 0) - (inp.keys.has('w') ? 1 : 0);
  }

  intent.moveX = mx;
  intent.moveZ = my;
  intent.camYaw = inp.camYaw;
  intent.camPitch = inp.camPitch;
  intent.held = inp.held;
  intent.pressed = substep === 0 ? inp.pressed : 0;
  intent.released = substep === 0 ? inp.released : 0;
  return intent;
}

export function endFrame(inp) {
  inp.pressed = 0;
  inp.released = 0;
}
