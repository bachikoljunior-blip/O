// hud.js — DOM overlay HUD and touch controls.
// L5.
//
// DOM rather than canvas: the browser composites it on its own layer, so the
// HUD costs nothing in the 3D draw budget and stays crisp at any render scale.
//
// Layout rule that matters more than it sounds: NEVER put readable state under
// the thumbs. Stamina and health sit high-left; the buttons own the lower
// right. On a phone your hands cover the bottom corners at all times.

import { S, STATE_NAME } from '../sim/components.js';
import { STAMINA } from '../data/tuning.js';

/**
 * Offsets in CSS pixels from a thumb pivot near the bottom-right corner,
 * scaled for small screens. Fractional-of-width placement overflowed the edge
 * on a narrow phone, and a button you cannot fully press is worse than one
 * that is slightly small.
 */
const BTNS = [
  { id: 'attack',   label: '攻', ox: 0,    oy: 0,    r: 44 },
  { id: 'dodge',    label: '回', ox: -6,   oy: -92,  r: 36 },
  { id: 'heavy',    label: '強', ox: -92,  oy: -6,   r: 36 },
  { id: 'block',    label: '守', ox: -72,  oy: -76,  r: 32 },
  { id: 'interact', label: '調', ox: -152, oy: -34,  r: 28 },
  { id: 'lock',     label: '注', ox: -52,  oy: -150, r: 26 },
];

export function createHUD(root) {
  root.innerHTML = `
    <div class="hud">
      <div class="vitals">
        <div class="bar hp"><i></i><span></span></div>
        <div class="bar sta"><i></i></div>
      </div>
      <div class="topright">
        <div class="clock"></div>
        <div class="region"></div>
      </div>
      <div class="chronicle"></div>
      <div class="ftext"></div>
      <div class="debug"></div>
      <div class="buttons"></div>
    </div>`;

  const el = {
    hp: root.querySelector('.hp i'),
    hpText: root.querySelector('.hp span'),
    sta: root.querySelector('.sta i'),
    clock: root.querySelector('.clock'),
    region: root.querySelector('.region'),
    chronicle: root.querySelector('.chronicle'),
    ftext: root.querySelector('.ftext'),
    debug: root.querySelector('.debug'),
    buttons: root.querySelector('.buttons'),
  };

  for (const b of BTNS) {
    const d = document.createElement('div');
    d.className = 'btn';
    d.dataset.id = b.id;
    d.textContent = b.label;
    d.style.width = d.style.height = `${b.r * 2}px`;
    el.buttons.appendChild(d);
    b.el = d;
  }

  return { el, btns: BTNS, lastChronicleSeq: -1 };
}

/**
 * Refresh touch hit-zones in CSS pixels. The input layer owns hit-testing, so
 * the drawn circle and the pressable area can never drift apart.
 * Zones are 12% larger than the visual: fingers are imprecise and a near-miss
 * on the attack button is indistinguishable from a dropped frame.
 */
export function layoutHUD(hud, input, w, h) {
  const k = Math.min(1, Math.min(w, h) / 380);
  const pivotX = w - 86 * k;
  const pivotY = h - 96 * k;

  input.touchZones.length = 0;
  for (const b of hud.btns) {
    const r = b.r * k;
    const x = pivotX + b.ox * k;
    const y = pivotY + b.oy * k;
    b.el.style.width = b.el.style.height = `${r * 2}px`;
    b.el.style.fontSize = `${Math.round(r * 0.42)}px`;
    b.el.style.left = `${x - r}px`;
    b.el.style.top = `${y - r}px`;
    input.touchZones.push({ id: b.id, x, y, r: r * 1.12 });
  }
}

export function updateHUD(hud, world, gfx, dir, extra) {
  const s = world.store;
  const p = extra.playerIndex;
  if (p === -1) return;

  const hpFrac = Math.max(0, s.hp[p] / Math.max(1, s.hpMax[p]));
  const staFrac = Math.max(0, s.stamina[p] / Math.max(1, s.staminaMax[p]));
  hud.el.hp.style.width = `${hpFrac * 100}%`;
  hud.el.sta.style.width = `${staFrac * 100}%`;
  hud.el.hpText.textContent = `${Math.ceil(s.hp[p])} / ${Math.ceil(s.hpMax[p])}`;
  // Flash the stamina bar while it is locked out, so the pause is legible
  // rather than feeling like the button stopped working.
  hud.el.sta.parentElement.classList.toggle('locked', s.staRegenDelay[p] > 0);

  const hour = (world.gameMinutes / 60) % 24;
  const hh = Math.floor(hour), mm = Math.floor((hour - hh) * 60);
  hud.el.clock.textContent = `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
  hud.el.region.textContent = extra.regionName || '';

  for (const b of hud.btns) {
    b.el.classList.toggle('down', extra.input.buttons.has(b.id));
  }

  // Floating combat text, projected from world space.
  if (dir.texts.length || hud.el.ftext.childElementCount) {
    const html = [];
    for (const t of dir.texts) {
      const v = extra.project(t.x, t.y, t.z);
      if (!v) continue;
      const a = Math.max(0, t.life / t.max);
      html.push(`<b class="t-${t.style}" style="left:${v.x}px;top:${v.y}px;opacity:${a}">${t.value}</b>`);
    }
    hud.el.ftext.innerHTML = html.join('');
  }

  if (extra.showDebug) {
    hud.el.debug.style.display = 'block';
    hud.el.debug.textContent =
      `${extra.fps.toFixed(0)}fps  scale ${extra.scaleIdx}  draw ${gfx.drawCalls}  ` +
      `tri ${(gfx.triangles / 1000).toFixed(0)}k  ent ${s.count}  ` +
      `state ${STATE_NAME[s.stateId[p]]}  phase ${s.phaseT[p].toFixed(0)}  ` +
      `sub ${extra.substeps}`;
  } else {
    hud.el.debug.style.display = 'none';
  }
}

/** Show what changed while the player was away. The chronicle is the payoff. */
export function showChronicle(hud, entries) {
  if (!entries.length) { hud.el.chronicle.innerHTML = ''; return; }
  hud.el.chronicle.innerHTML = entries
    .slice(0, 5)
    .map((e) => `<div class="ch i${Math.min(4, e.importance)}">${e.text}</div>`)
    .join('');
  clearTimeout(hud._chTimer);
  hud._chTimer = setTimeout(() => { hud.el.chronicle.innerHTML = ''; }, 9000);
}
