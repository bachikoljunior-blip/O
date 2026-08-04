// renderer.js — device setup over the vendored engine.
// L4.
//
// Every choice here follows from mobile GPU architecture rather than taste:
//
//  * CLUSTERED FORWARD, never deferred. Apple/Mali/Adreno are tile-based; on
//    native you keep a G-buffer in tile memory via imageblocks, subpasses or
//    pixel local storage, but WebGL2 and WebGPU expose none of those. A web
//    G-buffer must round-trip through DRAM, and the bandwidth kills you.
//  * MSAA rather than post-process AA — it resolves inside tile memory, while
//    FXAA/TAA need a full-resolution dependent read.
//  * No depth pre-pass — the tiler already does hidden-surface removal, so a
//    pre-pass just pays for the vertex work twice.
//  * No readPixels mid-frame, ever. It flushes the tiler.

import * as THREE from '../../vendor/three.module.js';
import { CAMERA } from '../data/tuning.js';

/** Resolution ladder. Asymmetric hysteresis, carried over from the 2D build. */
const SCALES = [1.0, 0.86, 0.72, 0.60, 0.50];

export function createRenderer(canvas) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,          // MSAA: cheap on tile GPUs
    alpha: false,
    depth: true,
    stencil: false,
    powerPreference: 'high-performance',
    // Never ask for a preserved buffer: it forces an off-tile copy each frame.
    preserveDrawingBuffer: false,
  });

  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(CAMERA.fov, 1, 0.35, 2600);

  // Two "cascades" done the affordable way: one tight shadow camera that
  // follows the player, plus baked ambient occlusion in the terrain vertex
  // colours for everything beyond it. Three or four 2048 cascades is a console
  // budget, not a phone-browser one.
  const sun = new THREE.DirectionalLight(0xffffff, 1.2);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 180;
  sun.shadow.camera.left = -48;
  sun.shadow.camera.right = 48;
  sun.shadow.camera.top = 48;
  sun.shadow.camera.bottom = -48;
  sun.shadow.bias = -0.0012;
  sun.shadow.normalBias = 0.035;
  scene.add(sun);
  scene.add(sun.target);

  const hemi = new THREE.HemisphereLight(0x8fa6c4, 0x3a3428, 0.85);
  scene.add(hemi);

  scene.fog = new THREE.FogExp2(0x8899aa, 0.0006);

  const state = {
    renderer, scene, camera, sun, hemi,
    scaleIdx: 0,
    baseDpr: 1,
    lowFpsT: 0,
    highFpsT: 0,
    drawCalls: 0,
    triangles: 0,
    /** WebGL2 is the floor; WebGPU would be a separate backend, not a flag. */
    isWebGL2: renderer.capabilities.isWebGL2,
    maxTextureSize: renderer.capabilities.maxTextureSize,
  };

  resize(state, canvas);
  return state;
}

export function resize(g, canvas) {
  const w = canvas.clientWidth || window.innerWidth;
  const h = canvas.clientHeight || window.innerHeight;

  // Never render at devicePixelRatio 3 on a phone. Above ~1.3 megapixels the
  // cap drops again — fill rate, not geometry, is the binding constraint.
  const raw = Math.min(window.devicePixelRatio || 1, 2);
  const px = w * h * raw * raw;
  g.baseDpr = px > 1_300_000 ? Math.min(raw, 1.5) : raw;

  const dpr = Math.max(0.5, g.baseDpr * SCALES[g.scaleIdx]);
  g.renderer.setPixelRatio(dpr);
  g.renderer.setSize(w, h, false);
  g.camera.aspect = w / Math.max(1, h);
  g.camera.updateProjectionMatrix();
}

/**
 * Dynamic resolution with asymmetric hysteresis: drop fast, recover slowly.
 * Symmetric thresholds oscillate, which reads worse than simply being lower.
 */
export function adaptQuality(g, canvas, dt, fps) {
  const before = g.scaleIdx;
  if (fps < 45) {
    g.lowFpsT += dt; g.highFpsT = 0;
    if (g.lowFpsT > 2.5 && g.scaleIdx < SCALES.length - 1) { g.scaleIdx++; g.lowFpsT = 0; }
  } else if (fps > 57) {
    g.highFpsT += dt; g.lowFpsT = 0;
    if (g.highFpsT > 8 && g.scaleIdx > 0) { g.scaleIdx--; g.highFpsT = 0; }
  } else {
    g.lowFpsT *= 0.9; g.highFpsT *= 0.9;
  }
  if (g.scaleIdx !== before) {
    resize(g, canvas);
    // Shadows are the next thing to go after resolution.
    g.sun.castShadow = g.scaleIdx < 3;
  }
  return g.scaleIdx !== before;
}

export function render(g) {
  g.renderer.render(g.scene, g.camera);
  const info = g.renderer.info.render;
  g.drawCalls = info.calls;
  g.triangles = info.triangles;
}

/**
 * WebGL context loss is a real event on iOS under memory pressure, not a
 * theoretical one. The seed+delta model means every GPU resource can be
 * rebuilt from CPU data, which turns a fatal error into a hitch.
 */
export function installContextLossHandling(g, canvas, onRestore) {
  canvas.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();
    console.warn('[gfx] WebGL context lost — rebuilding from CPU state on restore');
  }, false);
  canvas.addEventListener('webglcontextrestored', () => {
    console.warn('[gfx] WebGL context restored');
    if (onRestore) onRestore();
  }, false);
}
