// boot.js — WIRING ONLY.
// L7.
//
// No control flow lives here beyond the frame hooks. tools/lint-layers.js
// counts branches in this file and fails the build past a handful, because
// "just one if in boot" is exactly how a 1,295-line god object starts.

import * as THREE from '../../vendor/three.module.js';

import { createWorld, worldHour } from '../sim/world.js';
import { createTerrain } from '../sim/world/heightfield.js';
import { spawnPlayer, spawnEnemy } from '../sim/archetypes.js';
import { attachMacro } from '../sim/macro/init.js';
import { recentChronicle } from '../sim/macro/chronicle.js';
import { createIntent } from '../sim/systems/intent.js';
import { deref } from '../core/handles.js';
import { makeRNG } from '../core/rng.js';
import { regionWeights, dominantRegion, REGION_NAME } from '../data/regions.js';
import { macroMoisture, macroTemperature, macroHeight, TILE } from '../sim/world/heightfield.js';

import { createRenderer, resize, adaptQuality, render, installContextLossHandling } from '../gfx/renderer.js';
import { createTerrainManager, updateTerrain } from '../gfx/terrain.js';
import { createSky, updateSky, stepWeather } from '../gfx/sky.js';
import { createCameraRig, updateCamera, addTrauma, addFovKick } from '../gfx/camera.js';
import { createActorPool, syncActors, createVegetation, updateVegetation } from '../gfx/scene.js';

import { createDirector, consumeEvents } from '../present/director.js';
import { createParticles, updateParticles, renderParticles } from '../present/particles.js';
import { createAudio, unlock, makeAudioFacade, setListener } from '../present/audio/engine.js';

import { createInput, beginFrame, sampleForTick, endFrame } from '../platform/input.js';
import { createHUD, layoutHUD, updateHUD, showChronicle } from '../ui/hud.js';
import { createLoopState, startLoop } from './loop.js';
import { installPlatformGlue, createHaptics } from './platformGlue.js';

export function boot(canvas, hudRoot, seedParam) {
  const seed = (seedParam >>> 0) || 20260804;

  // ——— simulation ———
  const world = createWorld(seed);
  world.terrain = createTerrain(seed);
  attachMacro(world);
  const spawn = findSpawn(seed);
  spawnPlayer(world, spawn.x, spawn.z);
  seedEncounters(world, spawn);

  // ——— presentation ———
  const gfx = createRenderer(canvas);
  const tm = createTerrainManager(gfx, seed);
  const sky = createSky(gfx);
  const camRig = createCameraRig();
  const actors = createActorPool(gfx);
  const veg = createVegetation(gfx, seed);
  const particles = createParticles(gfx);
  const dir = createDirector();
  const audioState = createAudio();
  const audio = makeAudioFacade(audioState);
  const haptics = createHaptics();

  // ——— platform ———
  const input = createInput(canvas);
  const hud = createHUD(hudRoot);
  const ls = createLoopState(world.store);
  const intent = createIntent();
  const weatherRng = makeRNG(seed ^ 0x5eed);
  const rw = new Float32Array(3);
  const proj = new THREE.Vector3();

  const relayout = () => {
    resize(gfx, canvas);
    layoutHUD(hud, input, canvas.clientWidth, canvas.clientHeight);
  };

  installPlatformGlue({
    canvas,
    onResize: relayout,
    onAudioUnlock: () => unlock(audioState),
    onHidden: () => {},
  });
  installContextLossHandling(gfx, canvas, relayout);
  relayout();

  const project = (x, y, z) => {
    proj.set(x, y, z).project(gfx.camera);
    return (proj.z > 1) ? null : {
      x: (proj.x * 0.5 + 0.5) * canvas.clientWidth,
      y: (-proj.y * 0.5 + 0.5) * canvas.clientHeight,
    };
  };

  const ctx = {
    particles, audio, haptics, world,
    shake: (a) => addTrauma(camRig, a),
    fovKick: (a) => addFovKick(camRig, a),
  };
  const state = { showDebug: true, regionName: '' };

  startLoop(world, ls, {
    beginFrame: () => beginFrame(input),
    sampleIntent: (substep) => sampleForTick(input, intent, substep),
    afterTick: () => consumeEvents(dir, world.events, ctx),
    onFps: (fps, dt) => adaptQuality(gfx, canvas, dt, fps),
    endFrame: () => endFrame(input),
    render: (alpha, dt) => {
      const p = deref(world.store, world.player);
      const s = world.store;
      const px = s.px[p], py = s.py[p], pz = s.pz[p];

      camRig.targetYaw = input.camYaw;
      camRig.targetPitch = input.camPitch;
      // Lock-on: when a target is held, the camera frames both combatants
      // instead of sitting behind the player.
      const lock = deref(s, world.lockTarget);
      const lockPos = lock === -1 ? null
        : { x: s.px[lock], y: s.py[lock], z: s.pz[lock] };
      updateCamera(camRig, gfx, { x: px, y: py, z: pz }, dt, world.terrain, lockPos);
      // Keep the manual orbit in sync while locked, so releasing the lock does
      // not snap the camera back to wherever the thumb last left it.
      if (lockPos) input.camYaw = camRig.targetYaw;

      updateTerrain(tm, gfx, px, pz);
      updateVegetation(veg, px, pz, 1 - gfx.scaleIdx * 0.2);

      regionWeights(px, pz, seed, rw);
      state.regionName = REGION_NAME[dominantRegion(rw)];

      const tx = px / TILE, tz = pz / TILE;
      const elev = macroHeight(tx, tz, seed);
      stepWeather(sky, weatherRng, dt,
        macroMoisture(tx, tz, seed, elev), macroTemperature(tx, tz, seed, elev));
      updateSky(sky, gfx, worldHour(world), px, pz, seed, dt);

      syncActors(actors, gfx, world, alpha, dt, ls.prev);
      updateParticles(particles, dt, (x, z) => world.terrain.heightAt(x, z));
      renderParticles(particles, gfx.camera);
      setListener(audioState, gfx.camera);

      updateHUD(hud, world, gfx, dir, {
        playerIndex: p, input, project, fps: ls.fps,
        scaleIdx: gfx.scaleIdx, substeps: ls.substeps,
        showDebug: state.showDebug, regionName: state.regionName,
      });

      render(gfx);
    },
  });

  showChronicle(hud, recentChronicle(world, 4, 2));
  return { world, gfx, state, seed };
}

/** Walk inland from the map centre until we find dry, gently sloped ground. */
function findSpawn(seed) {
  const step = 24;
  for (let r = 0; r < 240; r++) {
    const tx = 384 + Math.round(Math.cos(r * 0.7) * r * 0.9);
    const tz = 384 + Math.round(Math.sin(r * 0.7) * r * 0.9);
    const h = macroHeight(tx, tz, seed);
    if (h > 0.46 && h < 0.62) return { x: tx * TILE, z: tz * TILE };
  }
  return { x: 384 * TILE, z: 384 * TILE };
}

/** A handful of encounters within sight of the start, so combat is immediate. */
function seedEncounters(world, spawn) {
  const ring = [
    ['skeleton', 26, 8], ['skeleton', 34, -14], ['skeleton', -22, 30],
    ['musha', 58, 40], ['machine_stag', -70, -52], ['troll', 96, -88],
  ];
  ring.forEach(([id, dx, dz]) => spawnEnemy(world, id, spawn.x + dx, spawn.z + dz, 1));
}
