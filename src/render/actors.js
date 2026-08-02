// actors.js — immediate-mode painting of characters and creatures.
// Local space: origin at the feet, -y is up. Renderer translates/scales.

import { TAU, clamp, lerp, roundRect, radial } from '../core/util.js';

// ————————————————————————————————————————————————— shared bits

export function drawShadow(ctx, rx, ry = null, alpha = 0.34) {
  ctx.fillStyle = `rgba(0,0,0,${alpha})`;
  ctx.beginPath();
  ctx.ellipse(0, 0, rx, ry ?? rx * 0.42, 0, 0, TAU);
  ctx.fill();
}

function limb(ctx, x1, y1, x2, y2, w, col) {
  ctx.strokeStyle = col;
  ctx.lineWidth = w;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}

// ————————————————————————————————————————————————— weapons

function drawSword(ctx, len, col) {
  ctx.fillStyle = '#6b4a2a';
  roundRect(ctx, -2, 0, 4, 9, 2); ctx.fill();
  ctx.fillStyle = '#d8b04a';
  ctx.fillRect(-5, -2, 10, 3);
  const g = ctx.createLinearGradient(-2.4, 0, 2.4, 0);
  g.addColorStop(0, '#dfe6ee'); g.addColorStop(0.45, col); g.addColorStop(1, '#7f8992');
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.moveTo(-2.4, -2); ctx.lineTo(2.4, -2); ctx.lineTo(1.9, -len); ctx.lineTo(0, -len - 3.4); ctx.lineTo(-1.9, -len);
  ctx.closePath(); ctx.fill();
}

function drawAxe(ctx, len, col) {
  ctx.fillStyle = '#6b4a2a';
  roundRect(ctx, -2, 2, 4, -len, 2); ctx.fill();
  ctx.fillStyle = col;
  ctx.beginPath();
  ctx.moveTo(0, -len + 2);
  ctx.quadraticCurveTo(12, -len + 1, 10, -len + 12);
  ctx.lineTo(0, -len + 10);
  ctx.closePath(); ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,0.4)';
  ctx.beginPath(); ctx.moveTo(0, -len + 3); ctx.quadraticCurveTo(9, -len + 2, 8, -len + 8); ctx.lineTo(0, -len + 7); ctx.closePath(); ctx.fill();
}

function drawSpear(ctx, len, col) {
  ctx.strokeStyle = '#7a5a34'; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(0, 6); ctx.lineTo(0, -len); ctx.stroke();
  ctx.fillStyle = col;
  ctx.beginPath();
  ctx.moveTo(0, -len - 9); ctx.lineTo(3.6, -len + 2); ctx.lineTo(0, -len + 5); ctx.lineTo(-3.6, -len + 2);
  ctx.closePath(); ctx.fill();
}

function drawStaff(ctx, len, col) {
  ctx.strokeStyle = '#6b5636'; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(0, 6); ctx.lineTo(0, -len); ctx.stroke();
  ctx.fillStyle = col;
  ctx.beginPath(); ctx.arc(0, -len - 4, 4.6, 0, TAU); ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.beginPath(); ctx.arc(-1.4, -len - 5.4, 1.8, 0, TAU); ctx.fill();
}

function drawMace(ctx, len, col) {
  ctx.strokeStyle = '#5c4020'; ctx.lineWidth = 3.4;
  ctx.beginPath(); ctx.moveTo(0, 4); ctx.lineTo(0, -len); ctx.stroke();
  ctx.fillStyle = col;
  ctx.beginPath(); ctx.arc(0, -len - 3, 5.4, 0, TAU); ctx.fill();
  ctx.fillStyle = 'rgba(0,0,0,0.25)';
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * TAU;
    ctx.beginPath(); ctx.arc(Math.cos(a) * 4, -len - 3 + Math.sin(a) * 4, 1.5, 0, TAU); ctx.fill();
  }
}

function drawDagger(ctx, len, col) {
  ctx.fillStyle = '#4a3a26';
  roundRect(ctx, -1.6, 0, 3.2, 6, 1.6); ctx.fill();
  ctx.fillStyle = col;
  ctx.beginPath(); ctx.moveTo(-2, -1); ctx.lineTo(2, -1); ctx.lineTo(0, -len); ctx.closePath(); ctx.fill();
}

function drawBow(ctx, len, col, draw = 0) {
  ctx.strokeStyle = col; ctx.lineWidth = 2.6;
  ctx.beginPath();
  ctx.moveTo(0, -len / 2);
  ctx.quadraticCurveTo(-len * 0.42, 0, 0, len / 2);
  ctx.stroke();
  ctx.strokeStyle = 'rgba(240,240,230,0.8)'; ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, -len / 2); ctx.lineTo(draw * 7, 0); ctx.lineTo(0, len / 2);
  ctx.stroke();
}

export const WEAPON_DRAW = {
  sword: drawSword, axe: drawAxe, spear: drawSpear, staff: drawStaff,
  mace: drawMace, dagger: drawDagger, bow: drawBow, none: () => {},
};

function drawWeapon(ctx, kind, len, col, extra) {
  (WEAPON_DRAW[kind] || drawSword)(ctx, len, col, extra);
}

function drawShield(ctx, kind, col, accent) {
  ctx.fillStyle = col;
  if (kind === 'kite') {
    ctx.beginPath();
    ctx.moveTo(-6, -8); ctx.lineTo(6, -8); ctx.lineTo(6, 2); ctx.quadraticCurveTo(0, 10, -6, 2);
    ctx.closePath(); ctx.fill();
  } else {
    ctx.beginPath(); ctx.arc(0, 0, 7.4, 0, TAU); ctx.fill();
  }
  ctx.fillStyle = accent;
  ctx.beginPath(); ctx.arc(0, -1, 2.6, 0, TAU); ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.35)'; ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.arc(0, 0, 6, -2.4, -0.6); ctx.stroke();
}

// ————————————————————————————————————————————————— humanoid

/**
 * @param a {dir, animT, moving, pal, equip, action, scale, undead}
 * dir: 0 down, 1 left, 2 right, 3 up
 */
export function drawHumanoid(ctx, a) {
  const p = a.pal;
  const dir = a.dir | 0;
  const t = a.animT || 0;
  const walk = a.moving ? Math.sin(t * 9) : 0;
  const walk2 = a.moving ? Math.sin(t * 9 + Math.PI) : 0;
  const bob = a.moving ? Math.abs(Math.sin(t * 9)) * 1.6 : Math.sin(t * 2.1) * 0.5;
  const s = a.scale || 1;
  const act = a.action;         // {type:'attack'|'cast'|'block'|'roll'|'bow', p:0..1, kind}
  ctx.save();
  ctx.scale(s, s);
  ctx.translate(0, -bob);

  const legTop = -12, torsoTop = -25, headY = -31;
  const back = dir === 3;
  const side = dir === 1 || dir === 2;
  const flip = dir === 1 ? -1 : 1;

  // ——— cloak (behind)
  if (p.cloak && !back) {
    ctx.fillStyle = p.cloak;
    ctx.beginPath();
    ctx.moveTo(-7, torsoTop + 1);
    ctx.quadraticCurveTo(-9 - walk * 1.2, legTop + 3, -5, legTop + 5);
    ctx.lineTo(5, legTop + 5);
    ctx.quadraticCurveTo(9 - walk * 1.2, legTop + 3, 7, torsoTop + 1);
    ctx.closePath();
    ctx.fill();
  }

  // ——— legs
  const legCol = a.undead ? '#d8d2c2' : p.pants;
  if (side) {
    limb(ctx, 0, legTop, walk * 4 * flip, 0, 5, legCol);
    limb(ctx, 0, legTop, walk2 * 4 * flip, 0, 5, legCol);
  } else {
    limb(ctx, -3.2, legTop, -3.2 + walk * 1.4, walk * 1.2, 5, legCol);
    limb(ctx, 3.2, legTop, 3.2 + walk2 * 1.4, walk2 * 1.2, 5, legCol);
  }
  // boots
  ctx.fillStyle = p.boots || '#4a3423';
  if (side) {
    ctx.fillRect(walk * 4 * flip - 3, -2.5, 6.5, 3);
    ctx.fillRect(walk2 * 4 * flip - 3, -2.5, 6.5, 3);
  } else {
    ctx.fillRect(-6, -2.5, 6, 3);
    ctx.fillRect(0, -2.5, 6, 3);
  }

  // ——— torso
  const torsoH = torsoTop - legTop;
  ctx.fillStyle = a.undead ? '#e6e0cf' : p.shirt;
  ctx.beginPath();
  ctx.moveTo(-6.4, legTop + 1);
  ctx.quadraticCurveTo(-7.6, torsoTop + 4, -5.6, torsoTop);
  ctx.lineTo(5.6, torsoTop);
  ctx.quadraticCurveTo(7.6, torsoTop + 4, 6.4, legTop + 1);
  ctx.closePath();
  ctx.fill();
  if (a.undead) {
    ctx.strokeStyle = 'rgba(120,110,90,0.85)'; ctx.lineWidth = 1;
    for (let i = 0; i < 3; i++) {
      ctx.beginPath();
      ctx.moveTo(-4.6, legTop - 1 - i * 3.4);
      ctx.quadraticCurveTo(0, legTop + 1 - i * 3.4, 4.6, legTop - 1 - i * 3.4);
      ctx.stroke();
    }
  }
  // armour plate
  if (p.armor) {
    ctx.fillStyle = p.armor;
    ctx.beginPath();
    ctx.moveTo(-6.2, legTop + 1);
    ctx.lineTo(-5.4, torsoTop + 2);
    ctx.lineTo(5.4, torsoTop + 2);
    ctx.lineTo(6.2, legTop + 1);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,0.22)';
    ctx.fillRect(-5, torsoTop + 3, 3, torsoH - 4);
    if (p.accent) {
      ctx.fillStyle = p.accent;
      ctx.fillRect(-1.4, torsoTop + 3, 2.8, torsoH - 5);
    }
    // pauldrons
    ctx.fillStyle = p.armor;
    ctx.beginPath(); ctx.ellipse(-6.4, torsoTop + 3, 3.4, 2.6, -0.3, 0, TAU); ctx.fill();
    ctx.beginPath(); ctx.ellipse(6.4, torsoTop + 3, 3.4, 2.6, 0.3, 0, TAU); ctx.fill();
  } else if (p.belt) {
    ctx.fillStyle = p.belt;
    ctx.fillRect(-6.4, legTop - 1.5, 12.8, 3);
  }

  // ——— arms & held items
  const skin = a.undead ? '#ddd7c6' : p.skin;
  const shoulderY = torsoTop + 4;
  let swingA = 0, swingReach = 0;
  if (act && (act.type === 'attack' || act.type === 'cast')) {
    const pr = clamp(act.p, 0, 1);
    swingA = act.type === 'attack'
      ? lerp(-2.2, 1.5, pr < 0.28 ? (pr / 0.28) * 0.22 : 0.22 + ((pr - 0.28) / 0.72) * 0.78)
      : Math.sin(pr * Math.PI) * -1.1;
    swingReach = act.type === 'attack' ? Math.sin(pr * Math.PI) * 3.2 : 0;
  }

  const wKind = (a.equip && a.equip.weapon) || 'none';
  const wCol = (a.equip && a.equip.weaponColor) || '#cfd6dd';
  const wLen = (a.equip && a.equip.weaponLen) || 17;

  // back arm
  if (!back) {
    limb(ctx, -5.6, shoulderY, -7.4 + walk2 * 2.2, legTop - 1, 3.6, skin);
  }

  // shield arm
  if (a.equip && a.equip.shield && !back) {
    ctx.save();
    const bx = dir === 2 ? -8.5 : dir === 1 ? 8.5 : -9.5;
    ctx.translate(bx, legTop - 5 + (act && act.type === 'block' ? -2 : 0));
    if (act && act.type === 'block') ctx.translate(dir === 1 ? -3 : 3, 0);
    drawShield(ctx, a.equip.shield, a.equip.shieldColor || '#8a6a3a', p.accent || '#d8b04a');
    ctx.restore();
  }

  // weapon arm
  ctx.save();
  const handX = side ? 5.4 * flip : dir === 3 ? -5.4 : 6.2;
  ctx.translate(handX + swingReach * (side ? flip : 0), shoulderY + 3 - swingReach * 0.4);
  if (wKind !== 'none') {
    ctx.save();
    let baseRot = side ? (flip > 0 ? 0.62 : -0.62) : dir === 3 ? -0.72 : 0.78;
    if (act && act.type === 'bow') baseRot = side ? (flip > 0 ? 1.57 : -1.57) : 0;
    ctx.rotate(baseRot + swingA * (side ? flip : 1) * (dir === 3 ? -1 : 1));
    if (wKind === 'bow') drawBow(ctx, wLen + 6, wCol, act && act.type === 'bow' ? act.p : 0);
    else drawWeapon(ctx, wKind, wLen, wCol);
    ctx.restore();
  }
  ctx.restore();
  // front arm on top of body
  limb(ctx, 5.6, shoulderY, handX + swingReach * (side ? flip : 0), shoulderY + 4 - swingReach * 0.4, 3.6, skin);

  // ——— head
  ctx.save();
  ctx.translate(0, headY + 6);
  const hr = 6.4;
  if (a.undead) {
    // skull
    ctx.fillStyle = '#e8e2d0';
    ctx.beginPath(); ctx.arc(0, 0, hr, 0, TAU); ctx.fill();
    ctx.fillStyle = '#2a2620';
    if (!back) {
      const ex = side ? flip * 1.6 : 0;
      ctx.beginPath(); ctx.ellipse(-2.4 + ex, -0.6, 1.5, 1.9, 0, 0, TAU); ctx.fill();
      ctx.beginPath(); ctx.ellipse(2.4 + ex, -0.6, 1.5, 1.9, 0, 0, TAU); ctx.fill();
      ctx.fillRect(-2 + ex, 2.6, 4, 1.6);
      if (a.glowEyes) {
        ctx.fillStyle = a.glowEyes;
        ctx.beginPath(); ctx.arc(-2.4 + ex, -0.6, 1.1, 0, TAU); ctx.fill();
        ctx.beginPath(); ctx.arc(2.4 + ex, -0.6, 1.1, 0, TAU); ctx.fill();
      }
    }
  } else {
    ctx.fillStyle = skin;
    ctx.beginPath(); ctx.arc(0, 0, hr, 0, TAU); ctx.fill();
    // face
    if (!back) {
      const ex = side ? flip * 1.8 : 0;
      ctx.fillStyle = '#2a231c';
      ctx.beginPath(); ctx.ellipse(-2.1 + ex, -0.4, 0.95, 1.25, 0, 0, TAU); ctx.fill();
      if (!side) { ctx.beginPath(); ctx.ellipse(2.1, -0.4, 0.95, 1.25, 0, 0, TAU); ctx.fill(); }
      ctx.fillStyle = 'rgba(180,110,100,0.5)';
      ctx.beginPath(); ctx.ellipse(ex, 2.6, 1.6, 0.7, 0, 0, TAU); ctx.fill();
    }
    // hair
    ctx.fillStyle = p.hair;
    const st = p.hairStyle | 0;
    ctx.beginPath();
    if (st === 0) {           // short
      ctx.arc(0, -0.6, hr + 0.6, Math.PI, TAU);
      ctx.lineTo(hr + 0.6, 0.6); ctx.lineTo(hr - 1.4, 0.2);
      ctx.lineTo(-hr + 1.4, 0.2); ctx.lineTo(-hr - 0.6, 0.6);
    } else if (st === 1) {    // long
      ctx.arc(0, -0.6, hr + 0.8, Math.PI, TAU);
      ctx.lineTo(hr + 0.8, 5.5); ctx.lineTo(hr - 2, 5.2); ctx.lineTo(hr - 2, 0);
      ctx.lineTo(-hr + 2, 0); ctx.lineTo(-hr + 2, 5.2); ctx.lineTo(-hr - 0.8, 5.5);
    } else if (st === 2) {    // spiky
      ctx.moveTo(-hr - 1, 0.4);
      for (let i = 0; i <= 5; i++) {
        const x = -hr + (i / 5) * hr * 2;
        ctx.lineTo(x - 0.9, -hr - 1.6 - (i % 2 ? 1.8 : 0));
        ctx.lineTo(x + 0.9, -hr + 0.4);
      }
      ctx.lineTo(hr + 1, 0.4);
    } else {                  // hooded / bald-ish
      ctx.arc(0, -0.4, hr + 1.2, Math.PI * 0.92, TAU * 1.04);
    }
    ctx.closePath();
    ctx.fill();
    if (p.helm) {
      ctx.fillStyle = p.helm;
      ctx.beginPath();
      ctx.arc(0, -0.8, hr + 1.3, Math.PI, TAU);
      ctx.lineTo(hr + 1.3, 0.4); ctx.lineTo(-hr - 1.3, 0.4);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.25)';
      ctx.fillRect(-hr, -hr - 0.4, 2.4, hr);
      if (p.plume) {
        ctx.fillStyle = p.plume;
        ctx.beginPath();
        ctx.moveTo(0, -hr - 1.6);
        ctx.quadraticCurveTo(4, -hr - 8, 1, -hr - 9);
        ctx.quadraticCurveTo(-1, -hr - 5, -1.4, -hr - 1.6);
        ctx.closePath(); ctx.fill();
      }
    }
  }
  ctx.restore();
  ctx.restore();
}

// ————————————————————————————————————————————————— creatures

function ellipseFill(ctx, x, y, rx, ry, col, rot = 0) {
  ctx.fillStyle = col;
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, rot, 0, TAU);
  ctx.fill();
}

export function drawSlime(ctx, e, t) {
  const s = e.scale || 1;
  ctx.save(); ctx.scale(s, s);
  const squish = 1 + Math.sin(t * 5 + e.phase) * 0.10;
  const col = e.tint || '#5cc46a';
  const h = 14 / squish, w = 15 * squish;
  ctx.fillStyle = col;
  ctx.beginPath();
  ctx.moveTo(-w, 0);
  ctx.bezierCurveTo(-w, -h * 1.5, w, -h * 1.5, w, 0);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,0.4)';
  ctx.beginPath(); ctx.ellipse(-w * 0.35, -h * 0.7, w * 0.22, h * 0.2, -0.4, 0, TAU); ctx.fill();
  ctx.fillStyle = '#1b2a1e';
  ctx.beginPath(); ctx.arc(-4.5, -h * 0.62, 1.9, 0, TAU); ctx.fill();
  ctx.beginPath(); ctx.arc(4.5, -h * 0.62, 1.9, 0, TAU); ctx.fill();
  ctx.strokeStyle = '#1b2a1e'; ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.arc(0, -h * 0.42, 3, 0.2, Math.PI - 0.2); ctx.stroke();
  ctx.restore();
}

export function drawWolf(ctx, e, t) {
  const s = e.scale || 1;
  const flip = e.dir === 1 ? -1 : 1;
  ctx.save(); ctx.scale(s * flip, s);
  const run = e.moving ? Math.sin(t * 13 + e.phase) : 0;
  const body = e.tint || '#6d6f78';
  const dark = e.tint2 || '#4a4c54';
  // legs
  limb(ctx, -6, -9, -8 + run * 3, 0, 3, dark);
  limb(ctx, 6, -9, 8 - run * 3, 0, 3, dark);
  limb(ctx, -4, -9, -5 - run * 3, 0, 3, body);
  limb(ctx, 7, -9, 9 + run * 3, 0, 3, body);
  // tail
  ctx.strokeStyle = body; ctx.lineWidth = 4; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(-8, -13); ctx.quadraticCurveTo(-15, -16 + run * 2, -14, -22 + run * 2); ctx.stroke();
  ellipseFill(ctx, 0, -14, 11, 6.4, body);
  ellipseFill(ctx, -2, -12, 9, 4.6, dark);
  // head
  ellipseFill(ctx, 9, -18, 6.2, 5, body);
  ctx.fillStyle = body;
  ctx.beginPath(); ctx.moveTo(11, -20); ctx.lineTo(16, -17); ctx.lineTo(11, -15); ctx.closePath(); ctx.fill();
  ctx.fillStyle = dark;
  ctx.beginPath(); ctx.moveTo(6, -22); ctx.lineTo(8, -27); ctx.lineTo(10, -21); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(10, -22); ctx.lineTo(13, -26); ctx.lineTo(13, -20); ctx.closePath(); ctx.fill();
  ctx.fillStyle = e.glow || '#ffd24a';
  ctx.beginPath(); ctx.arc(11, -19.4, 1.5, 0, TAU); ctx.fill();
  ctx.fillStyle = '#eae6dc';
  ctx.beginPath(); ctx.moveTo(14, -16.4); ctx.lineTo(15.4, -14.6); ctx.lineTo(13, -15.4); ctx.closePath(); ctx.fill();
  ctx.restore();
}

export function drawBoar(ctx, e, t) {
  const s = e.scale || 1;
  const flip = e.dir === 1 ? -1 : 1;
  ctx.save(); ctx.scale(s * flip, s);
  const run = e.moving ? Math.sin(t * 12 + e.phase) : 0;
  limb(ctx, -5, -8, -6 + run * 2.5, 0, 3.4, '#3d322a');
  limb(ctx, 5, -8, 6 - run * 2.5, 0, 3.4, '#3d322a');
  ellipseFill(ctx, 0, -13, 12, 8, '#6b5a48');
  ellipseFill(ctx, -3, -17, 8, 5, '#5a4a3a');
  ctx.strokeStyle = '#3a2f26'; ctx.lineWidth = 1.6;
  for (let i = -2; i <= 2; i++) { ctx.beginPath(); ctx.moveTo(i * 3, -19); ctx.lineTo(i * 3 + 1, -23); ctx.stroke(); }
  ellipseFill(ctx, 11, -13, 7, 6, '#7a6752');
  ellipseFill(ctx, 16, -11, 3.6, 3, '#8a7360');
  ctx.fillStyle = '#efe9dd';
  ctx.beginPath(); ctx.moveTo(14, -10); ctx.quadraticCurveTo(19, -12, 18, -16); ctx.lineTo(16, -15); ctx.quadraticCurveTo(16, -12, 13, -10.5); ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#2a2018';
  ctx.beginPath(); ctx.arc(12, -15.5, 1.4, 0, TAU); ctx.fill();
  ctx.restore();
}

export function drawBat(ctx, e, t) {
  const s = e.scale || 1;
  ctx.save(); ctx.scale(s, s);
  const flap = Math.sin(t * 16 + e.phase);
  const y = -22 + Math.sin(t * 4 + e.phase) * 3;
  ctx.fillStyle = e.tint || '#4a3f52';
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.quadraticCurveTo(side * 12, y - 6 + flap * 7, side * 20, y + 2 + flap * 5);
    ctx.quadraticCurveTo(side * 12, y + 4 + flap * 2, 0, y + 4);
    ctx.closePath(); ctx.fill();
  }
  ellipseFill(ctx, 0, y + 1, 5.4, 6, e.tint || '#4a3f52');
  ctx.fillStyle = '#2e2636';
  ctx.beginPath(); ctx.moveTo(-4, y - 4); ctx.lineTo(-2, y - 9); ctx.lineTo(0, y - 4); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(4, y - 4); ctx.lineTo(2, y - 9); ctx.lineTo(0, y - 4); ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#ff5a5a';
  ctx.beginPath(); ctx.arc(-2, y, 1.3, 0, TAU); ctx.fill();
  ctx.beginPath(); ctx.arc(2, y, 1.3, 0, TAU); ctx.fill();
  ctx.restore();
}

export function drawSpider(ctx, e, t) {
  const s = e.scale || 1;
  ctx.save(); ctx.scale(s, s);
  const step = Math.sin(t * 14 + e.phase);
  ctx.strokeStyle = '#241d22'; ctx.lineWidth = 2; ctx.lineCap = 'round';
  for (let i = 0; i < 4; i++) {
    for (const side of [-1, 1]) {
      const a = (i - 1.5) * 0.5;
      const k = ((i % 2) ? step : -step) * 2;
      ctx.beginPath();
      ctx.moveTo(0, -10);
      ctx.quadraticCurveTo(side * 10, -16 - i, side * (14 + Math.cos(a) * 4), -2 + k);
      ctx.stroke();
    }
  }
  ellipseFill(ctx, 0, -11, 9, 7.4, e.tint || '#37303a');
  ellipseFill(ctx, 0, -15, 5.4, 4.4, '#2b242e');
  ctx.fillStyle = '#c8384a';
  for (let i = 0; i < 4; i++) ctx.fillRect(-3.6 + i * 2.2, -17, 1.5, 1.5);
  ctx.fillStyle = 'rgba(220,200,120,0.6)';
  ctx.beginPath(); ctx.ellipse(0, -10, 4, 3, 0, 0, TAU); ctx.fill();
  ctx.restore();
}

export function drawGhost(ctx, e, t) {
  const s = e.scale || 1;
  ctx.save(); ctx.scale(s, s);
  const y = -20 + Math.sin(t * 2.4 + e.phase) * 3.4;
  ctx.globalAlpha = 0.82;
  const g = ctx.createLinearGradient(0, y - 16, 0, y + 14);
  g.addColorStop(0, e.tint || 'rgba(190,220,255,0.95)');
  g.addColorStop(1, 'rgba(120,160,220,0.05)');
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.moveTo(-11, y + 10);
  ctx.quadraticCurveTo(-13, y - 16, 0, y - 16);
  ctx.quadraticCurveTo(13, y - 16, 11, y + 10);
  for (let i = 0; i < 4; i++) {
    ctx.quadraticCurveTo(11 - i * 5.5 - 1.4, y + 15 + Math.sin(t * 6 + i) * 2, 11 - (i + 1) * 5.5, y + 10);
  }
  ctx.closePath(); ctx.fill();
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#1a2233';
  ctx.beginPath(); ctx.ellipse(-3.4, y - 5, 1.8, 2.6, 0, 0, TAU); ctx.fill();
  ctx.beginPath(); ctx.ellipse(3.4, y - 5, 1.8, 2.6, 0, 0, TAU); ctx.fill();
  ctx.beginPath(); ctx.ellipse(0, y + 2, 2.4, 3.2, 0, 0, TAU); ctx.fill();
  ctx.restore();
}

export function drawGolem(ctx, e, t) {
  const s = e.scale || 1;
  ctx.save(); ctx.scale(s, s);
  const walk = e.moving ? Math.sin(t * 5 + e.phase) : 0;
  const col = e.tint || '#6f6a62';
  const dark = '#4e4a44';
  ctx.fillStyle = dark;
  roundRect(ctx, -11, -14, 9, 14, 3); ctx.fill();
  roundRect(ctx, 2, -14, 9, 14, 3); ctx.fill();
  ctx.fillStyle = col;
  roundRect(ctx, -14, -36, 28, 24, 6); ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,0.14)';
  roundRect(ctx, -12, -34, 8, 20, 4); ctx.fill();
  ctx.fillStyle = e.glow || '#ffb02e';
  ctx.beginPath(); ctx.arc(0, -25, 3.6 + Math.sin(t * 3) * 0.5, 0, TAU); ctx.fill();
  ctx.fillStyle = col;
  roundRect(ctx, -21 + walk * 2, -34, 8, 22, 4); ctx.fill();
  roundRect(ctx, 13 - walk * 2, -34, 8, 22, 4); ctx.fill();
  roundRect(ctx, -9, -47, 18, 13, 5); ctx.fill();
  ctx.fillStyle = '#2a2622';
  ctx.fillRect(-6, -43, 12, 3.4);
  ctx.fillStyle = e.glow || '#ffb02e';
  ctx.fillRect(-5, -42.6, 3.4, 2.6);
  ctx.fillRect(1.6, -42.6, 3.4, 2.6);
  ctx.restore();
}

export function drawImp(ctx, e, t) {
  const s = e.scale || 1;
  ctx.save(); ctx.scale(s, s);
  const y = -14 + Math.sin(t * 6 + e.phase) * 2;
  const flap = Math.sin(t * 14 + e.phase);
  ctx.fillStyle = 'rgba(40,20,30,0.9)';
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.moveTo(0, y - 4);
    ctx.quadraticCurveTo(side * 12, y - 14 + flap * 4, side * 16, y - 2 + flap * 3);
    ctx.quadraticCurveTo(side * 8, y - 2, 0, y + 2);
    ctx.closePath(); ctx.fill();
  }
  ellipseFill(ctx, 0, y, 7, 8, e.tint || '#b4433a');
  ctx.strokeStyle = e.tint || '#b4433a'; ctx.lineWidth = 2.4; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(0, y + 6); ctx.quadraticCurveTo(6, y + 12, 10, y + 6); ctx.stroke();
  ellipseFill(ctx, 0, y - 9, 6, 5.4, e.tint || '#b4433a');
  ctx.fillStyle = '#2a1a16';
  ctx.beginPath(); ctx.moveTo(-5, y - 12); ctx.lineTo(-7, y - 18); ctx.lineTo(-2.4, y - 13.4); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(5, y - 12); ctx.lineTo(7, y - 18); ctx.lineTo(2.4, y - 13.4); ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#ffe14a';
  ctx.beginPath(); ctx.arc(-2.2, y - 9, 1.5, 0, TAU); ctx.fill();
  ctx.beginPath(); ctx.arc(2.2, y - 9, 1.5, 0, TAU); ctx.fill();
  ctx.restore();
}

export function drawDrake(ctx, e, t) {
  const s = e.scale || 1;
  const flip = e.dir === 1 ? -1 : 1;
  ctx.save(); ctx.scale(s * flip, s);
  const flap = Math.sin(t * 7 + e.phase);
  const col = e.tint || '#8a3a2e';
  const dark = '#5c261e';
  // wings behind
  ctx.fillStyle = 'rgba(60,26,20,0.9)';
  ctx.beginPath();
  ctx.moveTo(-2, -24);
  ctx.quadraticCurveTo(-22, -40 + flap * 8, -30, -20 + flap * 6);
  ctx.quadraticCurveTo(-16, -20, -2, -18);
  ctx.closePath(); ctx.fill();
  limb(ctx, -6, -14, -8, 0, 4, dark);
  limb(ctx, 8, -14, 10, 0, 4, dark);
  ctx.strokeStyle = col; ctx.lineWidth = 6; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(-8, -18); ctx.quadraticCurveTo(-22, -14, -26, -26 + flap * 3); ctx.stroke();
  ellipseFill(ctx, 0, -20, 14, 9, col);
  ellipseFill(ctx, -2, -17, 11, 6, dark);
  ctx.fillStyle = col;
  ctx.beginPath(); ctx.moveTo(10, -26); ctx.quadraticCurveTo(20, -30, 24, -22); ctx.quadraticCurveTo(18, -16, 10, -18); ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#ffd24a';
  ctx.beginPath(); ctx.arc(17, -25, 1.9, 0, TAU); ctx.fill();
  ctx.fillStyle = dark;
  for (let i = 0; i < 4; i++) { ctx.beginPath(); ctx.moveTo(-6 + i * 5, -28); ctx.lineTo(-4 + i * 5, -34); ctx.lineTo(-2 + i * 5, -28); ctx.closePath(); ctx.fill(); }
  ctx.restore();
}

export function drawDragon(ctx, e, t) {
  const s = (e.scale || 1) * 2.1;
  const flip = e.dir === 1 ? -1 : 1;
  ctx.save(); ctx.scale(s * flip, s);
  const flap = Math.sin(t * 3.4 + e.phase);
  const col = e.tint || '#7e2b22';
  const dark = '#4c1712';
  const glow = e.glow || '#ff8a2e';
  // far wing
  ctx.fillStyle = 'rgba(48,16,12,0.92)';
  ctx.beginPath();
  ctx.moveTo(0, -28);
  ctx.quadraticCurveTo(-26, -56 + flap * 10, -44, -30 + flap * 10);
  ctx.quadraticCurveTo(-22, -24, 0, -22);
  ctx.closePath(); ctx.fill();
  // tail
  ctx.strokeStyle = col; ctx.lineWidth = 9; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(-10, -22); ctx.quadraticCurveTo(-34, -18, -42, -34 + flap * 4); ctx.stroke();
  ctx.fillStyle = dark;
  ctx.beginPath(); ctx.moveTo(-42, -34 + flap * 4); ctx.lineTo(-52, -40 + flap * 4); ctx.lineTo(-44, -28 + flap * 4); ctx.closePath(); ctx.fill();
  // legs
  limb(ctx, -8, -18, -12, 0, 6, dark);
  limb(ctx, 10, -18, 14, 0, 6, dark);
  // body
  ellipseFill(ctx, 0, -26, 20, 13, col);
  ellipseFill(ctx, -2, -22, 16, 8, dark);
  // near wing (drawn before the neck so the head stays readable)
  ctx.fillStyle = 'rgba(102,34,26,0.95)';
  ctx.beginPath();
  ctx.moveTo(-4, -32);
  ctx.quadraticCurveTo(-24, -70 - flap * 12, -2, -78 - flap * 12);
  ctx.quadraticCurveTo(8, -54, 2, -30);
  ctx.closePath(); ctx.fill();
  ctx.strokeStyle = 'rgba(30,10,8,0.55)'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(-2, -32); ctx.lineTo(-4, -74 - flap * 12); ctx.stroke();

  // neck + head
  ctx.strokeStyle = col; ctx.lineWidth = 11;
  ctx.beginPath(); ctx.moveTo(12, -30); ctx.quadraticCurveTo(24, -44, 30, -50); ctx.stroke();
  ctx.fillStyle = col;
  ctx.beginPath();
  ctx.moveTo(26, -54); ctx.quadraticCurveTo(44, -58, 46, -48);
  ctx.quadraticCurveTo(38, -42, 26, -44); ctx.closePath(); ctx.fill();
  ctx.fillStyle = dark;
  ctx.beginPath(); ctx.moveTo(28, -58); ctx.lineTo(24, -68); ctx.lineTo(33, -58); ctx.closePath(); ctx.fill();
  ctx.fillStyle = glow;
  ctx.beginPath(); ctx.arc(36, -52, 2.6, 0, TAU); ctx.fill();
  ctx.fillStyle = '#f4ece0';
  ctx.beginPath(); ctx.moveTo(42, -46); ctx.lineTo(44, -42); ctx.lineTo(40, -44); ctx.closePath(); ctx.fill();
  // spines
  ctx.fillStyle = dark;
  for (let i = 0; i < 6; i++) {
    const x = -14 + i * 6;
    ctx.beginPath(); ctx.moveTo(x, -38); ctx.lineTo(x + 2, -48); ctx.lineTo(x + 5, -38); ctx.closePath(); ctx.fill();
  }
  ctx.restore();
}

export function drawWraith(ctx, e, t) {
  const s = e.scale || 1;
  ctx.save(); ctx.scale(s, s);
  const y = -24 + Math.sin(t * 2 + e.phase) * 4;
  const col = e.tint || '#2b2340';
  ctx.fillStyle = col;
  ctx.beginPath();
  ctx.moveTo(-13, y + 16);
  ctx.quadraticCurveTo(-16, y - 20, 0, y - 22);
  ctx.quadraticCurveTo(16, y - 20, 13, y + 16);
  for (let i = 0; i < 5; i++) ctx.quadraticCurveTo(13 - i * 5.2 - 1, y + 22 + Math.sin(t * 5 + i) * 3, 13 - (i + 1) * 5.2, y + 16);
  ctx.closePath(); ctx.fill();
  ctx.fillStyle = 'rgba(0,0,0,0.75)';
  ctx.beginPath(); ctx.ellipse(0, y - 8, 8, 9, 0, 0, TAU); ctx.fill();
  ctx.fillStyle = e.glow || '#7ce8ff';
  ctx.beginPath(); ctx.ellipse(-3.4, y - 9, 1.9, 2.8, 0.2, 0, TAU); ctx.fill();
  ctx.beginPath(); ctx.ellipse(3.4, y - 9, 1.9, 2.8, -0.2, 0, TAU); ctx.fill();
  // scythe
  ctx.strokeStyle = '#3a3230'; ctx.lineWidth = 2.6;
  ctx.beginPath(); ctx.moveTo(11, y + 12); ctx.lineTo(15, y - 24); ctx.stroke();
  ctx.fillStyle = '#c8d2dc';
  ctx.beginPath(); ctx.moveTo(15, y - 24); ctx.quadraticCurveTo(30, y - 20, 26, y - 6); ctx.quadraticCurveTo(24, y - 18, 14, y - 20); ctx.closePath(); ctx.fill();
  ctx.restore();
}

export function drawTroll(ctx, e, t) {
  const s = (e.scale || 1) * 1.5;
  ctx.save(); ctx.scale(s, s);
  const walk = e.moving ? Math.sin(t * 5 + e.phase) : 0;
  const col = e.tint || '#5f7a52';
  const dark = '#455c3c';
  limb(ctx, -7, -16, -9 + walk * 3, 0, 7, dark);
  limb(ctx, 7, -16, 9 - walk * 3, 0, 7, dark);
  ellipseFill(ctx, 0, -30, 17, 16, col);
  ellipseFill(ctx, 0, -24, 13, 9, dark);
  limb(ctx, -14, -40, -20 - walk * 2, -14, 7, col);
  limb(ctx, 14, -40, 20 + walk * 2, -14, 7, col);
  ellipseFill(ctx, 0, -50, 11, 10, col);
  ctx.fillStyle = '#f0ead8';
  ctx.beginPath(); ctx.moveTo(-4, -46); ctx.lineTo(-2.4, -41); ctx.lineTo(-1, -46); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(4, -46); ctx.lineTo(2.4, -41); ctx.lineTo(1, -46); ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#ffe14a';
  ctx.beginPath(); ctx.arc(-4, -53, 2.2, 0, TAU); ctx.fill();
  ctx.beginPath(); ctx.arc(4, -53, 2.2, 0, TAU); ctx.fill();
  ctx.fillStyle = '#2a2620';
  ctx.beginPath(); ctx.arc(-4, -53, 1.1, 0, TAU); ctx.fill();
  ctx.beginPath(); ctx.arc(4, -53, 1.1, 0, TAU); ctx.fill();
  ctx.restore();
}

export const CREATURE_DRAW = {
  slime: drawSlime, wolf: drawWolf, boar: drawBoar, bat: drawBat, spider: drawSpider,
  ghost: drawGhost, golem: drawGolem, imp: drawImp, drake: drawDrake, dragon: drawDragon,
  wraith: drawWraith, troll: drawTroll,
};
