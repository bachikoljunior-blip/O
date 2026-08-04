// camera.js — third-person orbit camera with terrain collision and shake.
// L4.

import * as THREE from '../../vendor/three.module.js';
import { CAMERA, SHAKE } from '../data/tuning.js';
import { clamp, lerp } from '../core/math.js';
import { valueNoise2 } from '../core/noise.js';

export function createCameraRig() {
  return {
    yaw: 0, pitch: 0.20,
    targetYaw: 0, targetPitch: 0.20,
    pos: new THREE.Vector3(),
    look: new THREE.Vector3(),
    trauma: 0,
    shakeSeed: 0,
    fovKick: 0,
    distance: CAMERA.distance,
  };
}

/**
 * Trauma model: shake = trauma^2, driven by 2D value NOISE rather than
 * Math.random(). This is not a detail — random per-frame offsets read as
 * buzz or vibration, whereas coherent noise reads as an impact. It is most of
 * why cheap hits feel cheap.
 */
export function addTrauma(rig, amount) {
  rig.trauma = Math.min(1, rig.trauma + amount);
}

export function addFovKick(rig, amount) {
  rig.fovKick = Math.min(8, rig.fovKick + amount);
}

export function updateCamera(rig, gfx, target, dt, terrain, lockTarget) {
  // Smooth the orbit toward player input.
  rig.yaw = lerp(rig.yaw, rig.targetYaw, Math.min(1, dt * 12));
  rig.pitch = clamp(
    lerp(rig.pitch, rig.targetPitch, Math.min(1, dt * 12)),
    CAMERA.pitchMin, CAMERA.pitchMax);

  // Lock-on: frame both combatants rather than snapping behind the player.
  let focusX = target.x, focusZ = target.z;
  if (lockTarget) {
    focusX = lerp(target.x, lockTarget.x, 0.30);
    focusZ = lerp(target.z, lockTarget.z, 0.30);
    const want = Math.atan2(lockTarget.z - target.z, lockTarget.x - target.x);
    rig.targetYaw = -want - Math.PI / 2;
  }

  const cp = Math.cos(rig.pitch);
  const dist = rig.distance;
  let cx = focusX - Math.sin(rig.yaw) * cp * dist;
  let cz = focusZ - Math.cos(rig.yaw) * cp * dist;
  let cy = target.y + CAMERA.height + Math.sin(rig.pitch) * dist;

  // Terrain collision: pull in rather than clipping through a hillside.
  if (terrain) {
    const ground = terrain.heightAt(cx, cz) + CAMERA.collisionRadius + 0.35;
    if (cy < ground) cy = ground;
  }

  // Shake.
  if (rig.trauma > 0) {
    rig.shakeSeed += dt * SHAKE.frequency;
    const s = rig.trauma * rig.trauma;
    const nx = valueNoise2(rig.shakeSeed, 0.0, 1) - 0.5;
    const ny = valueNoise2(0.0, rig.shakeSeed, 2) - 0.5;
    const nr = valueNoise2(rig.shakeSeed, rig.shakeSeed, 3) - 0.5;
    cx += nx * 2 * SHAKE.maxOffset * s;
    cy += ny * 2 * SHAKE.maxOffset * s;
    gfx.camera.rotation.z = nr * 2 * SHAKE.maxRoll * s;
    rig.trauma = Math.max(0, rig.trauma - SHAKE.decayPerSec * dt);
  } else {
    gfx.camera.rotation.z *= 0.85;
  }

  rig.pos.set(cx, cy, cz);
  gfx.camera.position.copy(rig.pos);

  rig.look.set(focusX, target.y + CAMERA.height * 0.75, focusZ);
  gfx.camera.lookAt(rig.look);
  if (rig.trauma > 0) gfx.camera.rotation.z += 0;

  // FOV kick punctuates heavy hits without a post-process pass.
  if (rig.fovKick > 0.01) {
    gfx.camera.fov = CAMERA.fov + rig.fovKick;
    gfx.camera.updateProjectionMatrix();
    rig.fovKick = lerp(rig.fovKick, 0, Math.min(1, dt * 7));
  } else if (gfx.camera.fov !== CAMERA.fov) {
    gfx.camera.fov = CAMERA.fov;
    gfx.camera.updateProjectionMatrix();
  }
}
