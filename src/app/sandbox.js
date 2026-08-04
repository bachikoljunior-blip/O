// sandbox.js — the combat tuning harness.
// L7.
//
// Runs the REAL fixed-step loop and the REAL systems against a flat plane and
// one enemy. Nothing here reimplements combat; if it did, we would be tuning a
// thing the game does not run.
//
// The loop it drives is app/loop.js with `timeScale`/`paused`/`stepRequested`
// set — same accumulator, same substep clamp, same interpolation.

import * as THREE from '../../vendor/three.module.js';

import { createWorld } from '../sim/world.js';
import { spawnPlayer, spawnEnemy } from '../sim/archetypes.js';
import { despawn } from '../sim/store.js';
import { attachMacro } from '../sim/macro/init.js';
import { createIntent } from '../sim/systems/intent.js';
import { deref } from '../core/handles.js';
import { S } from '../sim/components.js';
import { ENEMY_IDS } from '../data/enemies.js';
import { ATTACKS, finalizeAttacks, attackByIndex } from '../data/attacks.js';
import { PARRY } from '../data/tuning.js';

import { createRenderer, resize, render } from '../gfx/renderer.js';
import { createCameraRig, updateCamera, addTrauma, addFovKick } from '../gfx/camera.js';
import { createActorPool, syncActors } from '../gfx/scene.js';
import { createDirector, consumeEvents } from '../present/director.js';
import { createParticles, updateParticles, renderParticles } from '../present/particles.js';
import { createAudio, unlock, makeAudioFacade } from '../present/audio/engine.js';
import { createInput, beginFrame, sampleForTick, endFrame, usesTouch } from '../platform/input.js';
import { createHUD, layoutHUD, updateHUD } from '../ui/hud.js';
import { createFrameData, updateFrameData, recordLatency } from '../ui/framedata.js';
import { createLoopState, startLoop } from './loop.js';
import { installPlatformGlue, createHaptics } from './platformGlue.js';

const FLAT_Y = 0;
/** A dead-flat floor: terrain must not be a variable while tuning combat. */
const FLAT_TERRAIN = {
  heightAt: () => FLAT_Y,
  normalAt: (x, z, o) => { o[0] = 0; o[1] = 1; o[2] = 0; return o; },
  isWater: () => false,
  seaLevel: -1000,
};

export function bootSandbox(canvas, hudRoot, fdRoot, ctlRoot) {
  const world = createWorld(4242);
  world.terrain = FLAT_TERRAIN;
  attachMacro(world);
  spawnPlayer(world, 0, 0);

  const gfx = createRenderer(canvas);
  gfx.scene.fog = null;                       // clarity beats atmosphere here
  gfx.renderer.setClearColor(0x1b2028, 1);

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(200, 200, 40, 40),
    new THREE.MeshLambertMaterial({ color: 0x555f52, wireframe: false })
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  gfx.scene.add(floor);
  const grid = new THREE.GridHelper(200, 100, 0x445048, 0x2a3230);
  gfx.scene.add(grid);

  const camRig = createCameraRig();
  const actors = createActorPool(gfx);
  const particles = createParticles(gfx);
  const dir = createDirector();
  const audioState = createAudio();
  const audio = makeAudioFacade(audioState);
  const input = createInput(canvas);
  const hud = createHUD(hudRoot);
  const fd = createFrameData(fdRoot);
  const ls = createLoopState(world.store);
  const intent = createIntent();
  const proj = new THREE.Vector3();

  // Telegraph ring: expands through the enemy's wind-up, and turns a different
  // colour for the delayed variant so the read is visible while tuning it.
  const ringMat = new THREE.MeshBasicMaterial({
    color: 0xff6a4a, transparent: true, opacity: 0.55, side: THREE.DoubleSide,
  });
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.9, 1.15, 40), ringMat);
  ring.rotation.x = -Math.PI / 2;
  ring.visible = false;
  gfx.scene.add(ring);

  const state = {
    enemyId: 'skeleton',
    enemyHandle: -1,
    forceTouchProfile: false,
    prevPlayerState: S.IDLE,
  };

  const respawnEnemy = () => {
    const s = world.store;
    const old = deref(s, state.enemyHandle);
    if (old !== -1) despawn(s, old);
    state.enemyHandle = spawnEnemy(world, state.enemyId, 4.5, 0, 1);
  };

  const relayout = () => {
    resize(gfx, canvas);
    layoutHUD(hud, input, canvas.clientWidth, canvas.clientHeight);
  };
  installPlatformGlue({
    canvas, onResize: relayout,
    onAudioUnlock: () => unlock(audioState), onHidden: () => {},
  });
  relayout();

  const project = (x, y, z) => {
    proj.set(x, y, z).project(gfx.camera);
    return (proj.z > 1) ? null : {
      x: (proj.x * 0.5 + 0.5) * canvas.clientWidth,
      y: (-proj.y * 0.5 + 0.5) * canvas.clientHeight,
    };
  };

  const ctx = {
    particles, audio, haptics: createHaptics(), world,
    shake: (a) => addTrauma(camRig, a),
    fovKick: (a) => addFovKick(camRig, a),
  };

  buildControls(ctlRoot, world, ls, state, respawnEnemy, fd);
  respawnEnemy();

  startLoop(world, ls, {
    beginFrame: () => {
      beginFrame(input);
      world.inputProfile.parryWindowBonus =
        (state.forceTouchProfile || usesTouch(input)) ? PARRY.touchBonusTicks : 0;
    },
    sampleIntent: (substep) => sampleForTick(input, intent, substep),
    afterTick: () => consumeEvents(dir, world.events, ctx),
    endFrame: () => endFrame(input),
    render: (alpha, dt) => {
      const s = world.store;
      const p = deref(s, world.player);
      const e = deref(s, state.enemyHandle);

      // End-to-end latency: finger-down timestamp -> the frame in which the
      // player VISIBLY commits to an action. Measuring to the sim tick instead
      // would hide the render and compositing cost, which the hand still feels.
      if (input.latencyPending && s.stateId[p] !== state.prevPlayerState
          && s.stateId[p] !== S.IDLE) {
        recordLatency(fd, performance.now() - input.rawPressAt);
        input.latencyPending = false;
      }
      state.prevPlayerState = s.stateId[p];

      camRig.targetYaw = input.camYaw;
      camRig.targetPitch = input.camPitch;
      const lockPos = e === -1 ? null : { x: s.px[e], y: s.py[e], z: s.pz[e] };
      updateCamera(camRig, gfx, { x: s.px[p], y: s.py[p], z: s.pz[p] },
        dt, world.terrain, lockPos);

      // Telegraph.
      if (e !== -1 && s.stateId[e] === S.WINDUP) {
        const a = attackByIndex(s.attackId[e]);
        const delayed = s.phaseT[e] < 0;
        const k = Math.max(0, Math.min(1, s.phaseT[e] / Math.max(1, a.windup)));
        ring.visible = true;
        ring.position.set(s.px[e], 0.05, s.pz[e]);
        ring.scale.setScalar(0.6 + k * 2.4);
        ringMat.color.setHex(delayed ? 0x9a6aff : 0xff6a4a);
        ringMat.opacity = 0.25 + k * 0.5;
      } else {
        ring.visible = false;
      }

      syncActors(actors, gfx, world, alpha, dt, ls.prev);
      updateParticles(particles, dt, () => FLAT_Y);
      renderParticles(particles, gfx.camera);

      updateHUD(hud, world, gfx, dir, {
        playerIndex: p, input, project, fps: ls.fps,
        scaleIdx: gfx.scaleIdx, substeps: ls.substeps,
        showDebug: false, regionName: '',
      });
      updateFrameData(fd, world, p, {
        enemyIndex: e, paused: ls.paused, timeScale: ls.timeScale, fps: ls.fps,
      });

      render(gfx);
    },
  });

  return { world, ls, state, gfx };
}

/** Reload data/attacks.js from disk and mutate the live table in place. */
async function hotReloadAttacks(log) {
  try {
    const mod = await import(`../data/attacks.js?t=${Date.now()}`);
    let n = 0;
    for (const [id, next] of Object.entries(mod.ATTACKS)) {
      const cur = ATTACKS[id];
      if (!cur) continue;
      // Mutate in place: every system holds a reference to this object, so
      // replacing it would leave them all pointing at the stale table.
      for (const k of Object.keys(next)) {
        if (k === 'index' || k === 'id' || k === '_cancel') continue;
        cur[k] = next[k];
      }
      n++;
    }
    finalizeAttacks(ATTACKS);
    log(`攻撃データを再読込: ${n}件`);
  } catch (err) {
    log(`再読込に失敗: ${err.message}`);
  }
}

function buildControls(root, world, ls, state, respawnEnemy, fd) {
  root.innerHTML = `
    <div class="ctl">
      <div class="ctl-row">
        <label>敵</label>
        <select class="c-enemy">${ENEMY_IDS.map((id) => `<option>${id}</option>`).join('')}</select>
        <button class="c-respawn">再配置</button>
      </div>
      <div class="ctl-row">
        <label>時間</label>
        <button class="c-speed" data-v="1">×1</button>
        <button class="c-speed" data-v="0.25">×¼</button>
        <button class="c-speed" data-v="0.125">×⅛</button>
        <button class="c-pause">⏸</button>
        <button class="c-step">▶|</button>
      </div>
      <div class="ctl-row">
        <label>入力</label>
        <button class="c-profile">パッド猶予</button>
        <button class="c-reload">攻撃データ再読込</button>
      </div>
      <div class="ctl-log"></div>
    </div>`;

  const log = (msg) => { root.querySelector('.ctl-log').textContent = msg; };

  root.querySelector('.c-enemy').addEventListener('change', (ev) => {
    state.enemyId = ev.target.value;
    respawnEnemy();
    log(`敵を ${state.enemyId} に変更`);
  });
  root.querySelector('.c-respawn').addEventListener('click', () => {
    respawnEnemy(); log('敵を再配置');
  });
  for (const b of root.querySelectorAll('.c-speed')) {
    b.addEventListener('click', () => {
      ls.timeScale = parseFloat(b.dataset.v);
      ls.paused = false;
      log(`再生速度 ×${ls.timeScale}`);
    });
  }
  root.querySelector('.c-pause').addEventListener('click', () => {
    ls.paused = !ls.paused;
    log(ls.paused ? '一時停止' : '再開');
  });
  root.querySelector('.c-step').addEventListener('click', () => {
    ls.paused = true;
    ls.stepRequested += 1;
  });
  const prof = root.querySelector('.c-profile');
  prof.addEventListener('click', () => {
    state.forceTouchProfile = !state.forceTouchProfile;
    prof.textContent = state.forceTouchProfile ? 'タッチ猶予' : 'パッド猶予';
    log(state.forceTouchProfile
      ? `パリィ窓 ${PARRY.windowTicks + PARRY.touchBonusTicks}T（タッチ）`
      : `パリィ窓 ${PARRY.windowTicks}T（パッド）`);
  });
  root.querySelector('.c-reload').addEventListener('click', () => hotReloadAttacks(log));
}
