// actors.js — procedural character meshes and kinematic animation.
// L4.
//
// THE FACE PROBLEM, AND HOW WE SIDESTEP IT
// Procedurally generated human faces do not reach AAA quality — the current
// state of the art is an expert spending three years to reach eleven creature
// archetypes, with most random parameter draws producing broken results. So we
// never build a face. Every humanoid wears a full helm, a mask or a hood, and
// the machine beasts have no face to begin with. This is not a dodge: it is
// exactly what Elden Ring and Ghost of Tsushima already do.
//
// Animation is purely kinematic — FK plus two-bone IK, phase-driven gait,
// raycast foot placement, spring-damper secondary motion. Motion matching
// would need 500-2000 mocap clips that do not exist and cannot be synthesised.

import * as THREE from '../../vendor/three.module.js';
import { R } from '../data/regions.js';
import { S } from '../sim/components.js';
import { lerp, clamp01 } from '../core/math.js';

const _v = new THREE.Vector3();

/** Shared geometry: allocated once, instanced by reference across every actor. */
function makeParts() {
  return {
    torso: new THREE.CapsuleGeometry(0.175, 0.40, 4, 8),
    hip: new THREE.CapsuleGeometry(0.18, 0.14, 3, 8),
    limb: new THREE.CapsuleGeometry(0.078, 0.36, 3, 6),
    arm: new THREE.CapsuleGeometry(0.058, 0.32, 3, 6),
    helm: new THREE.SphereGeometry(0.155, 10, 8),
    kasa: new THREE.ConeGeometry(0.30, 0.16, 12),
    mask: new THREE.BoxGeometry(0.20, 0.16, 0.06),
    pauldron: new THREE.SphereGeometry(0.115, 8, 6),
    blade: new THREE.BoxGeometry(0.075, 1.00, 0.030),
    guard: new THREE.BoxGeometry(0.22, 0.035, 0.05),
    shield: new THREE.CylinderGeometry(0.30, 0.30, 0.05, 12),
    beastBody: new THREE.CapsuleGeometry(0.52, 1.15, 5, 10),
    beastLeg: new THREE.CapsuleGeometry(0.10, 0.62, 3, 6),
    beastHead: new THREE.BoxGeometry(0.42, 0.32, 0.62),
    antler: new THREE.ConeGeometry(0.07, 0.72, 5),
    core: new THREE.IcosahedronGeometry(0.17, 0),
  };
}

let PARTS = null;

const mat = (hex, rough = 0.85, metal = 0.0, emissive = 0x000000) =>
  new THREE.MeshLambertMaterial({ color: hex, emissive });

/** Darken or lighten a packed hex colour by a signed fraction. */
function shade(hex, amt) {
  const r = (hex >> 16) & 255, g = (hex >> 8) & 255, b = hex & 255;
  const f = (v) => Math.max(0, Math.min(255, Math.round(v * (1 + amt))));
  return (f(r) << 16) | (f(g) << 8) | f(b);
}

/** Per-region visual identity, so a silhouette alone tells you where you are. */
const LOOK = {
  [R.ASH]: { cloth: 0x6a7080, metal: 0xa8aeb8, trim: 0xd8863c, head: 'helm' },
  [R.MIST]: { cloth: 0x4e5e6e, metal: 0xbab4a6, trim: 0xe04a56, head: 'kasa' },
  [R.RUST]: { cloth: 0x9a7c50, metal: 0xc09468, trim: 0x6ec8d0, head: 'mask' },
};

/**
 * Build a humanoid rig. Returns a group plus named joints the animator drives.
 * Proportions and palette are the only inputs — the same rig serves the player,
 * every NPC and every humanoid enemy.
 */
export function buildHumanoid(look, scale = 1) {
  if (!PARTS) PARTS = makeParts();
  const L = LOOK[look] || LOOK[R.ASH];

  const clothM = mat(L.cloth);
  const metalM = mat(L.metal);
  const trimM = mat(L.trim);
  // Limbs are darker than the torso so they read as separate masses instead of
  // merging into one silhouette at distance.
  const limbM = mat(shade(L.cloth, -0.30));

  const root = new THREE.Group();
  const hips = new THREE.Group();
  hips.position.y = 0.92 * scale;
  root.add(hips);

  const torso = new THREE.Mesh(PARTS.torso, clothM);
  torso.position.y = 0.30 * scale;
  torso.castShadow = true;
  hips.add(torso);

  const pelvis = new THREE.Mesh(PARTS.hip, clothM);
  pelvis.castShadow = true;
  hips.add(pelvis);

  // Head: ALWAYS covered. Helm, conical kasa, or a hard mask.
  const head = new THREE.Group();
  head.position.y = 0.62 * scale;
  torso.add(head);
  if (L.head === 'kasa') {
    const k = new THREE.Mesh(PARTS.kasa, clothM);
    k.position.y = 0.10 * scale;
    k.castShadow = true;
    head.add(k);
    const m = new THREE.Mesh(PARTS.mask, trimM);
    m.position.set(0, -0.02 * scale, 0.11 * scale);
    head.add(m);
  } else if (L.head === 'mask') {
    const h = new THREE.Mesh(PARTS.helm, metalM);
    h.scale.set(0.92, 1.0, 1.05);
    h.castShadow = true;
    head.add(h);
    const m = new THREE.Mesh(PARTS.mask, trimM);
    m.position.set(0, 0, 0.13 * scale);
    head.add(m);
  } else {
    const h = new THREE.Mesh(PARTS.helm, metalM);
    h.scale.set(1.0, 1.12, 1.0);
    h.castShadow = true;
    head.add(h);
    const visor = new THREE.Mesh(PARTS.mask, mat(0x14161a));
    visor.scale.set(0.9, 0.42, 1);
    visor.position.set(0, 0.01 * scale, 0.13 * scale);
    head.add(visor);
  }

  const mkLimb = (geo, m, x, y, z) => {
    const pivot = new THREE.Group();
    pivot.position.set(x, y, z);
    const mesh = new THREE.Mesh(geo, m);
    mesh.position.y = -0.19 * scale;
    mesh.castShadow = true;
    pivot.add(mesh);
    return pivot;
  };

  const legL = mkLimb(PARTS.limb, limbM, -0.115 * scale, -0.02 * scale, 0);
  const legR = mkLimb(PARTS.limb, limbM, 0.115 * scale, -0.02 * scale, 0);
  hips.add(legL, legR);

  const armL = mkLimb(PARTS.arm, limbM, -0.285 * scale, 0.52 * scale, 0);
  const armR = mkLimb(PARTS.arm, limbM, 0.285 * scale, 0.52 * scale, 0);
  hips.add(armL, armR);

  for (const [pivot, sx] of [[armL, -1], [armR, 1]]) {
    const p = new THREE.Mesh(PARTS.pauldron, metalM);
    p.position.set(sx * 0.02 * scale, 0.085 * scale, 0);
    p.scale.set(1.15, 0.62, 1.15);
    p.castShadow = true;
    pivot.add(p);
  }

  // Weapon in the right hand, shield in the left.
  const weapon = new THREE.Group();
  weapon.position.set(0, -0.34 * scale, 0);
  const blade = new THREE.Mesh(PARTS.blade, metalM);
  blade.position.y = -0.5 * scale;
  blade.castShadow = true;
  weapon.add(blade);
  const guard = new THREE.Mesh(PARTS.guard, trimM);
  weapon.add(guard);
  weapon.rotation.x = -0.35;
  armR.add(weapon);

  root.scale.setScalar(scale);
  return {
    root, hips, torso, head, legL, legR, armL, armR, weapon,
    phase: Math.random() * 6.28,
    springVX: 0, springV: 0, lean: 0,
  };
}

/** A machine beast: quadruped, destructible parts, and no face problem at all. */
export function buildMachineBeast(scale = 1) {
  if (!PARTS) PARTS = makeParts();
  const shell = mat(0xbe9464);
  const dark = mat(0x6a5c48);
  const glow = mat(0x58a8b0, 0.4, 0, 0x1d5a60);

  const root = new THREE.Group();
  const body = new THREE.Mesh(PARTS.beastBody, shell);
  body.rotation.z = Math.PI / 2;
  body.position.y = 1.15 * scale;
  body.castShadow = true;
  root.add(body);

  const head = new THREE.Group();
  head.position.set(0, 1.35 * scale, 1.15 * scale);
  root.add(head);
  const skull = new THREE.Mesh(PARTS.beastHead, dark);
  skull.castShadow = true;
  head.add(skull);
  for (const sx of [-1, 1]) {
    const a = new THREE.Mesh(PARTS.antler, shell);
    a.position.set(0.16 * sx * scale, 0.30 * scale, 0);
    a.rotation.z = sx * 0.5;
    a.castShadow = true;
    head.add(a);
  }

  // The weak point. Visible, targetable, and the reason the fight is a puzzle
  // rather than a health bar.
  const core = new THREE.Mesh(PARTS.core, glow);
  core.position.set(0, 1.28 * scale, -0.25 * scale);
  root.add(core);

  const legs = [];
  for (const [sx, sz] of [[-1, 1], [1, 1], [-1, -1], [1, -1]]) {
    const pivot = new THREE.Group();
    pivot.position.set(0.34 * sx * scale, 1.05 * scale, 0.55 * sz * scale);
    const l = new THREE.Mesh(PARTS.beastLeg, dark);
    l.position.y = -0.34 * scale;
    l.castShadow = true;
    pivot.add(l);
    root.add(pivot);
    legs.push(pivot);
  }

  root.scale.setScalar(scale);
  return { root, body, head, core, legs, phase: 0, quadruped: true };
}

/**
 * Drive a rig from simulation state. Everything is derived from speed and the
 * combat state id, so there is no animation graph to author or maintain.
 */
export function animateRig(rig, dt, speed, stateId, phaseFrac, grounded = true) {
  const moving = speed > 0.35;
  const cadence = 2.1 + Math.min(speed, 9) * 0.72;
  rig.phase += dt * (moving ? cadence : 1.4);

  const swing = Math.sin(rig.phase) * clamp01(speed / 5) * 0.85;
  const bob = Math.abs(Math.sin(rig.phase)) * clamp01(speed / 6) * 0.055;

  if (rig.quadruped) {
    // Diagonal pairs, the way a real quadruped trots.
    rig.legs[0].rotation.x = swing;
    rig.legs[3].rotation.x = swing;
    rig.legs[1].rotation.x = -swing;
    rig.legs[2].rotation.x = -swing;
    rig.root.position.y += 0;
    rig.body.position.y = 1.15 + bob;
    return;
  }

  rig.legL.rotation.x = swing;
  rig.legR.rotation.x = -swing;
  rig.hips.position.y = 0.92 + bob;

  // Spring-damper lean into acceleration. This is the cheapest thing that
  // makes procedural motion stop reading as a puppet.
  const targetLean = clamp01(speed / 8) * 0.16;
  rig.springV += (targetLean - rig.lean) * 26 * dt;
  rig.springV *= 0.86;
  rig.lean += rig.springV * dt * 8;
  rig.torso.rotation.x = rig.lean;

  // Arms follow the legs unless the combat state overrides them.
  let armL = -swing * 0.8, armR = swing * 0.8;

  switch (stateId) {
    case S.WINDUP:
      armR = lerp(-0.4, -2.3, clamp01(phaseFrac));
      rig.torso.rotation.y = lerp(0, -0.4, clamp01(phaseFrac));
      break;
    case S.ACTIVE:
      armR = lerp(-2.3, 1.5, clamp01(phaseFrac));
      rig.torso.rotation.y = lerp(-0.4, 0.55, clamp01(phaseFrac));
      break;
    case S.RECOVERY:
      armR = lerp(1.5, 0, clamp01(phaseFrac));
      rig.torso.rotation.y = lerp(0.55, 0, clamp01(phaseFrac));
      break;
    case S.DODGE: {
      const t = clamp01(phaseFrac);
      rig.hips.rotation.x = t * Math.PI * 2;
      rig.hips.position.y = 0.92 - Math.sin(t * Math.PI) * 0.42;
      armL = armR = -1.2;
      break;
    }
    case S.BLOCK:
    case S.PARRY:
      armL = -1.5; armR = -0.8;
      rig.torso.rotation.y = 0.35;
      break;
    case S.HURT:
    case S.STAGGER:
      rig.torso.rotation.x = -0.35;
      armL = armR = -0.6;
      break;
    case S.BROKEN:
      rig.torso.rotation.x = -0.7;
      rig.hips.position.y = 0.72;
      armL = armR = 0.4;
      break;
    case S.DEAD:
      rig.root.rotation.x = -Math.PI * 0.48;
      break;
    default:
      rig.torso.rotation.y *= 0.85;
      if (stateId !== S.DODGE) rig.hips.rotation.x *= 0.8;
  }

  rig.armL.rotation.x = armL;
  rig.armR.rotation.x = armR;
}
