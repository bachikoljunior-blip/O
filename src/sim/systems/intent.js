// intent.js — turn a frame's Intent into buffered actions and movement.
// L2.
//
// The Intent is an immutable value produced once per substep by the platform
// layer. The sim never reads the DOM, which is what lets the whole simulation
// run under `node --test` with no shims.

import { C, S, ACT, BTN, BUFFER_SLOTS } from '../components.js';
import { bufferPush } from '../ops/buffer.js';
import { driveActor } from '../ops/motion.js';
import { deref } from '../../core/handles.js';
import { LOCOMOTION } from '../../data/tuning.js';
import { fsin, fcos } from '../../core/fixed.js';

export { BTN };

export function createIntent() {
  return { moveX: 0, moveZ: 0, camYaw: 0, camPitch: 0, pressed: 0, held: 0, released: 0 };
}

export function intentSystem(w, intent) {
  const s = w.store;
  const p = deref(s, w.player);
  if (p === -1 || !intent) return;

  if (s.stateId[p] === S.DEAD) return;

  // Edge-triggered actions go into the buffer; the state machine decides when
  // they are allowed to fire.
  if (intent.pressed & BTN.ATTACK) bufferPush(s, p, ACT.ATTACK);
  if (intent.pressed & BTN.HEAVY) bufferPush(s, p, ACT.HEAVY);
  if (intent.pressed & BTN.DODGE) bufferPush(s, p, ACT.DODGE);
  if (intent.pressed & BTN.INTERACT) bufferPush(s, p, ACT.INTERACT);

  // Guard is a HELD state, so it must be able to end; the PRESS EDGE of the
  // same button is the parry. Tap to parry, hold to guard — one button, which
  // is what the touch layout can afford.
  if (intent.pressed & BTN.BLOCK) bufferPush(s, p, ACT.PARRY);
  if (intent.held & BTN.BLOCK) {
    bufferPush(s, p, ACT.BLOCK);
  } else {
    const base = p * BUFFER_SLOTS;
    for (let k = 0; k < BUFFER_SLOTS; k++) {
      if (s.bufAct[base + k] === ACT.BLOCK) { s.bufAct[base + k] = ACT.NONE; s.bufTtl[base + k] = 0; }
    }
  }

  // Movement is camera-relative: pushing "up" means away from the camera.
  const c = fcos(intent.camYaw), sn = fsin(intent.camYaw);
  const wx = intent.moveX * c - intent.moveZ * sn;
  const wz = intent.moveX * sn + intent.moveZ * c;

  const mag = Math.min(1, Math.sqrt(wx * wx + wz * wz));
  const sprinting = (intent.held & BTN.SPRINT) !== 0 && s.stamina[p] > 5;
  const top = sprinting ? LOCOMOTION.sprintSpeed
    : LOCOMOTION.walkSpeed + (LOCOMOTION.runSpeed - LOCOMOTION.walkSpeed) * mag;

  driveActor(w, p, wx, wz, top * mag);
}
