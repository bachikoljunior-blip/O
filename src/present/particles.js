// particles.js — pooled particle system.
// L3.
//
// Ported from the 2D build's simulation, which already integrated full 3D
// state (x,y,z / vx,vy,vz with gravity and a ground bounce) — that part was
// always renderer-agnostic. What changed: a fixed pool instead of splicing out
// of a 1400-element array, and a single instanced draw instead of per-particle
// canvas calls.
//
// Particles are NOT simulation entities. They live outside the deterministic
// world entirely, so their count can scale with device quality without
// changing what happens in the fight.

import * as THREE from '../../vendor/three.module.js';

const MAX = 1400;

export function createParticles(gfx) {
  const geo = new THREE.PlaneGeometry(1, 1);
  const mat = new THREE.MeshBasicMaterial({
    vertexColors: true, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
  });
  const mesh = new THREE.InstancedMesh(geo, mat, MAX);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(MAX * 3), 3);
  mesh.frustumCulled = false;
  mesh.count = 0;
  gfx.scene.add(mesh);

  const f = () => new Float32Array(MAX);
  return {
    mesh, n: 0,
    x: f(), y: f(), z: f(),
    vx: f(), vy: f(), vz: f(),
    life: f(), maxLife: f(), size: f(),
    r: f(), g: f(), b: f(),
    grav: f(), drag: f(), additive: f(),
    _m: new THREE.Matrix4(),
    _p: new THREE.Vector3(),
    _q: new THREE.Quaternion(),
    _s: new THREE.Vector3(),
  };
}

export const PRESETS = {
  sparks:    { life: [0.18, 0.42], size: [0.05, 0.13], speed: [3.5, 9], grav: -16, drag: 0.90, col: [1.0, 0.82, 0.45] },
  blood:     { life: [0.30, 0.70], size: [0.06, 0.16], speed: [2.0, 6], grav: -22, drag: 0.94, col: [0.62, 0.09, 0.10] },
  dust:      { life: [0.40, 0.90], size: [0.14, 0.42], speed: [0.8, 2.6], grav: -2.5, drag: 0.88, col: [0.62, 0.58, 0.50] },
  heal:      { life: [0.60, 1.10], size: [0.06, 0.14], speed: [1.0, 2.4], grav: 4.0, drag: 0.95, col: [0.45, 0.95, 0.62] },
  spark_ring:{ life: [0.24, 0.40], size: [0.07, 0.15], speed: [6, 11], grav: -4, drag: 0.86, col: [1.0, 0.93, 0.70] },
  shockwave: { life: [0.30, 0.30], size: [0.9, 1.6], speed: [0, 0], grav: 0, drag: 1.0, col: [1.0, 0.85, 0.55] },
  rain:      { life: [0.5, 0.8], size: [0.02, 0.03], speed: [0, 0], grav: -55, drag: 1.0, col: [0.62, 0.72, 0.85] },
  snow:      { life: [2.0, 3.4], size: [0.05, 0.09], speed: [0.2, 0.6], grav: -1.2, drag: 0.97, col: [0.95, 0.97, 1.0] },
  ash:       { life: [1.6, 3.0], size: [0.04, 0.10], speed: [0.3, 0.9], grav: -1.6, drag: 0.96, col: [0.42, 0.38, 0.34] },
};

const rnd = () => Math.random();   // presentation only — never the simulation
const range = (r) => r[0] + (r[1] - r[0]) * rnd();

export function burst(ps, preset, x, y, z, count, nx = 0, ny = 1, nz = 0) {
  const P = PRESETS[preset];
  if (!P) return;
  for (let i = 0; i < count; i++) {
    if (ps.n >= MAX) return;
    const k = ps.n++;
    const spd = range(P.speed);
    // Cone around the impact normal, widened so it does not look like a laser.
    const ax = nx + (rnd() - 0.5) * 1.5;
    const ay = ny + (rnd() - 0.5) * 1.5 + 0.4;
    const az = nz + (rnd() - 0.5) * 1.5;
    const m = Math.sqrt(ax * ax + ay * ay + az * az) || 1;

    ps.x[k] = x; ps.y[k] = y; ps.z[k] = z;
    ps.vx[k] = (ax / m) * spd;
    ps.vy[k] = (ay / m) * spd;
    ps.vz[k] = (az / m) * spd;
    ps.maxLife[k] = ps.life[k] = range(P.life);
    ps.size[k] = range(P.size);
    ps.grav[k] = P.grav;
    ps.drag[k] = P.drag;
    ps.r[k] = P.col[0]; ps.g[k] = P.col[1]; ps.b[k] = P.col[2];
  }
}

/** Integrate and compact. Swap-remove keeps the pool contiguous with no churn. */
export function updateParticles(ps, dt, groundAt) {
  let i = 0;
  while (i < ps.n) {
    ps.life[i] -= dt;
    if (ps.life[i] <= 0) {
      const last = --ps.n;
      if (i !== last) {
        for (const c of ['x','y','z','vx','vy','vz','life','maxLife','size','r','g','b','grav','drag']) {
          ps[c][i] = ps[c][last];
        }
      }
      continue;
    }
    ps.vy[i] += ps.grav[i] * dt;
    const d = ps.drag[i];
    ps.vx[i] *= d; ps.vy[i] *= d; ps.vz[i] *= d;
    ps.x[i] += ps.vx[i] * dt;
    ps.y[i] += ps.vy[i] * dt;
    ps.z[i] += ps.vz[i] * dt;

    if (groundAt) {
      const g = groundAt(ps.x[i], ps.z[i]);
      if (ps.y[i] < g) { ps.y[i] = g; ps.vy[i] *= -0.42; ps.vx[i] *= 0.7; ps.vz[i] *= 0.7; }
    }
    i++;
  }
}

/** Billboard every live particle toward the camera in one instanced draw. */
export function renderParticles(ps, camera) {
  const q = camera.quaternion;
  for (let i = 0; i < ps.n; i++) {
    const t = ps.life[i] / ps.maxLife[i];
    const s = ps.size[i] * (0.4 + t * 0.6);
    ps._p.set(ps.x[i], ps.y[i], ps.z[i]);
    ps._s.set(s, s, s);
    ps._m.compose(ps._p, q, ps._s);
    ps.mesh.setMatrixAt(i, ps._m);
    ps.mesh.instanceColor.setXYZ(i, ps.r[i] * t, ps.g[i] * t, ps.b[i] * t);
  }
  ps.mesh.count = ps.n;
  ps.mesh.instanceMatrix.needsUpdate = true;
  ps.mesh.instanceColor.needsUpdate = true;
}
