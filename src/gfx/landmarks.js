// landmarks.js — draw the horizon.
// L4.
//
// The whole budget for this system is about fifteen draw calls, and it has to
// cover two to three kilometres. That rules out one mesh per landmark, so:
//
//   * each kind is MERGED ONCE into a single BufferGeometry from primitives —
//     no textures exist in this project and none may be added, so form and
//     baked vertex colour are the only tools;
//   * there are two of those per kind, a near form and a stripped SILHOUETTE
//     form, selected per instance by distance;
//   * every visible landmark of a kind and LOD goes into one InstancedMesh, so
//     the cost is 7 kinds x 2 LODs = 14 calls no matter how many are on screen;
//   * region colour rides on instanceColor, which is why one geometry can serve
//     an ash-kingdom monolith and a rust-waste one.
//
// The far LOD is geometry, not a billboard impostor: a billboard needs an
// atlas, an atlas needs a render-to-texture pass, and a render-to-texture pass
// on a tile GPU costs more than the eighty triangles it would save.
//
// THE FLOATING-LANDMARK PROBLEM. Terrain streams to RINGS=[1,3,7], about 900 m.
// A landmark at 1.5 km is the entire point of this system but would hang in
// empty fog with no ground under it. So anything beyond the terrain rings gets
// a cheap radial GROUND CAP sampled from the same heightfield, skirted steeply
// downward so it reads as a distant landform rather than a floating plate. All
// of them merge into ONE extra draw call.

import * as THREE from '../../vendor/three.module.js';
import { LANDMARK_KINDS, SHADE, COMPOSE } from '../data/landmarks.js';
import { blendPalette } from '../data/regions.js';
import { sampleHeight } from '../sim/world/heightfield.js';
import { sectorReport, SECTORS } from '../sim/world/vantage.js';
import { CHUNK_SIZE, RINGS } from './terrain.js';
import { clamp01, TAU } from '../core/math.js';

export { SECTORS };

/** Beyond this the near form is not worth its triangles; it is also the shadow range. */
const NEAR_R = 250;
/** Ground caps: radial rings in metres, then a skirt that dives into the fog. */
const CAP_RINGS = [42, 88, 138];
const CAP_SEG = 10;
const CAP_SKIRT = 78;
const CAP_SINK = 3;

const _m4 = new THREE.Matrix4();
const _q = new THREE.Quaternion();
const _e = new THREE.Euler();
const _v3 = new THREE.Vector3();
const _sc = new THREE.Vector3(1, 1, 1);
const _one = new THREE.Vector3(1, 1, 1);
const _col = new THREE.Color();
const _rgb = new Float32Array(3);
const UP = new THREE.Vector3(0, 1, 0);

// ————————————————————————————————— geometry

/**
 * Distance LOD thickens as well as simplifies.
 *
 * A 0.55 m pylon leg at 2 km is a quarter of a pixel wide: it does not
 * antialias, it FLICKERS, and the lattice reads as noise instead of as a
 * tower. Clamping every thin dimension up in the far form is the standard
 * silhouette-preserving trick and it is the difference between a landmark that
 * works at range and one that sparkles. Nothing is close enough for the lie to
 * be visible.
 */
const FAR_MIN = 1.35;
const fat = (v, far) => (far && v < FAR_MIN ? FAR_MIN : v);

function primitive(part, far) {
  const s = part.s;
  if (part.p === 0) {
    return new THREE.BoxGeometry(fat(s[0], far), fat(s[1], far), fat(s[2], far));
  }
  if (part.p === 1) {
    const r = far ? FAR_MIN * 0.55 : 0;
    return new THREE.CylinderGeometry(Math.max(s[0], r), Math.max(s[1], r), s[2],
      far ? Math.min(s[3], 5) : s[3], 1);
  }
  if (part.p === 2) {
    return new THREE.ConeGeometry(far ? Math.max(s[0], FAR_MIN * 0.7) : s[0], s[1],
      far ? Math.min(s[2], 5) : s[2]);
  }
  return new THREE.IcosahedronGeometry(s[0], far ? 0 : s[1]);
}

/**
 * Merge one kind's parts into a single geometry.
 *
 * The vertical AO ramp is the highest-value line here: an untextured mass lit
 * by one directional light and a hemisphere is a flat blob, and darkening the
 * base is what makes it read as standing ON something. It costs nothing at run
 * time because it is baked into the vertex colours at build time.
 */
function buildKindGeometry(kind, far) {
  const parts = kind.build();
  const P = [], N = [], C = [], I = [];
  const top = kind.height * 0.8;
  let base = 0;

  for (let pi = 0; pi < parts.length; pi++) {
    const part = parts[pi];
    if (far && !part.far) continue;
    const g = primitive(part, far);
    _e.set(part.r[0], part.r[1], part.r[2]);
    _v3.set(part.t[0], part.t[1], part.t[2]);
    g.applyMatrix4(_m4.compose(_v3, _q.setFromEuler(_e), _one));

    const pos = g.attributes.position.array;
    const nrm = g.attributes.normal.array;
    const sh = SHADE[part.k] || SHADE[0];
    for (let i = 0; i < pos.length; i += 3) {
      P.push(pos[i], pos[i + 1], pos[i + 2]);
      N.push(nrm[i], nrm[i + 1], nrm[i + 2]);
      const ao = 0.40 + 0.60 * clamp01(pos[i + 1] / top);
      C.push(sh[0] * ao, sh[1] * ao, sh[2] * ao);
    }

    const n = pos.length / 3;
    if (g.index) {
      const ia = g.index.array;
      for (let i = 0; i < ia.length; i++) I.push(base + ia[i]);
    } else {
      for (let i = 0; i < n; i++) I.push(base + i);
    }
    base += n;
    g.dispose();
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(P), 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(N), 3));
  geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(C), 3));
  geo.setIndex(new THREE.BufferAttribute(
    base > 65535 ? new Uint32Array(I) : new Uint16Array(I), 1));
  // The lean is applied last, about the base, so a ruin tilts instead of being
  // modelled pre-tilted part by part — and so the AO ramp stays vertical.
  _e.set(kind.lean[0], 0, kind.lean[1]);
  geo.applyMatrix4(_m4.makeRotationFromEuler(_e));
  geo.computeBoundingSphere();
  return geo;
}

/**
 * Per-landmark region tint, resolved once from the blended culture weights the
 * placer recorded. Raw 0..1 values, matching the convention terrain.js already
 * uses for its baked vertex colours.
 */
function tintOf(l) {
  blendPalette(l.w || [1, 0, 0], LANDMARK_KINDS[l.kind].tint, _rgb);
  return new THREE.Color().setRGB(_rgb[0] / 255, _rgb[1] / 255, _rgb[2] / 255);
}

// ————————————————————————————————— construction

/**
 * `opts.patchMaterial` lets the caller run these two materials through whatever
 * shared stylised-lighting patch the rest of gfx/ uses, so landmarks are lit by
 * exactly the same model as the terrain. Defaults to plain Lambert, which is
 * what the rest of the build used before any such patch existed.
 */
export function createLandmarks(gfx, seed, landmarks, opts = {}) {
  const patch = opts.patchMaterial || ((m) => m);
  const material = patch(new THREE.MeshLambertMaterial({ vertexColors: true }));
  const capMaterial = patch(new THREE.MeshLambertMaterial({
    vertexColors: true, side: THREE.DoubleSide,
  }));

  const counts = new Array(LANDMARK_KINDS.length).fill(0);
  for (let i = 0; i < landmarks.length; i++) counts[landmarks[i].kind]++;

  const near = [], far = [];
  for (let k = 0; k < LANDMARK_KINDS.length; k++) {
    const kind = LANDMARK_KINDS[k];
    const cap = Math.max(2, counts[k]);
    const mk = (geo, max, shadow) => {
      const m = new THREE.InstancedMesh(geo, material, max);
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      m.setColorAt(0, _col.setRGB(1, 1, 1));      // allocate instanceColor
      m.castShadow = shadow;
      m.receiveShadow = false;
      // Instances are scattered across kilometres, so the per-object bounding
      // sphere is meaningless — cull per instance in updateLandmarks instead.
      m.frustumCulled = false;
      m.count = 0;
      m.visible = false;
      gfx.scene.add(m);
      return m;
    };
    near.push(mk(buildKindGeometry(kind, false), Math.min(cap, 10), true));
    far.push(mk(buildKindGeometry(kind, true), cap, false));
  }

  const caps = new THREE.Mesh(new THREE.BufferGeometry(), capMaterial);
  caps.castShadow = false;
  caps.receiveShadow = false;
  caps.visible = false;
  gfx.scene.add(caps);

  return {
    seed, landmarks, near, far, caps, material, capMaterial,
    tints: landmarks.map(tintOf),
    capCache: new Array(landmarks.length).fill(null),
    farR: 2300, quality: 1,
    lastX: 1e9, lastZ: 1e9, capSig: -1,
    visibleCount: 0,
  };
}

// ————————————————————————————————— per-frame

/**
 * Select LOD and rebuild the instance buffers. Rebuilds are gated on player
 * movement, not on the frame: the landmark set is static world data and only
 * changes when a distance threshold is crossed.
 */
export function updateLandmarks(lm, px, pz, quality = 1) {
  const dx = px - lm.lastX, dz = pz - lm.lastZ;
  // A quality-ladder step changes the draw distance, so it has to force a
  // rebuild even when the player is standing still.
  if (dx * dx + dz * dz < 26 * 26 && Math.abs(quality - lm.quality) < 0.05) return lm.visibleCount;
  lm.lastX = px; lm.lastZ = pz; lm.quality = quality;
  lm.farR = 1500 + 800 * clamp01(quality);

  const nN = new Array(lm.near.length).fill(0);
  const nF = new Array(lm.far.length).fill(0);
  const farSet = [];
  let sig = 0;

  const ccx = Math.floor(px / CHUNK_SIZE), ccz = Math.floor(pz / CHUNK_SIZE);
  const nearR2 = NEAR_R * NEAR_R, farR2 = lm.farR * lm.farR;

  for (let i = 0; i < lm.landmarks.length; i++) {
    const l = lm.landmarks[i];
    const ex = l.x - px, ez = l.z - pz;
    const d2 = ex * ex + ez * ez;
    if (d2 > farR2) continue;

    const isNear = d2 < nearR2;
    const mesh = isNear ? lm.near[l.kind] : lm.far[l.kind];
    const slot = isNear ? nN : nF;
    const idx = slot[l.kind];
    if (idx >= mesh.instanceMatrix.count) continue;

    _v3.set(l.x, l.y, l.z);
    _q.setFromAxisAngle(UP, l.rot);
    _m4.compose(_v3, _q, _sc.setScalar(l.scale));
    mesh.setMatrixAt(idx, _m4);
    mesh.setColorAt(idx, lm.tints[i]);
    slot[l.kind]++;

    // Beyond the terrain rings there is no ground to stand on, so give it one.
    const cheb = Math.max(Math.abs(Math.floor(l.x / CHUNK_SIZE) - ccx),
      Math.abs(Math.floor(l.z / CHUNK_SIZE) - ccz));
    if (cheb > RINGS[RINGS.length - 1]) { farSet.push(i); sig = (sig * 31 + i + 1) | 0; }
  }

  let total = 0;
  for (let k = 0; k < lm.near.length; k++) {
    total += applyCount(lm.near[k], nN[k]) + applyCount(lm.far[k], nF[k]);
  }
  lm.visibleCount = total;

  if (sig !== lm.capSig) { lm.capSig = sig; rebuildCaps(lm, farSet); }
  return total;
}

function applyCount(mesh, n) {
  mesh.count = n;
  mesh.visible = n > 0;
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  return n;
}

// ————————————————————————————————— distant ground caps

/** One landmark's radial ground cap, cached: it is static world geometry. */
function buildCap(lm, i) {
  const l = lm.landmarks[i];
  const seed = lm.seed;
  const nv = 1 + CAP_RINGS.length * CAP_SEG;
  const P = new Float32Array(nv * 3);
  const C = new Float32Array(nv * 3);
  const I = new Uint16Array((CAP_SEG + (CAP_RINGS.length - 1) * CAP_SEG * 2) * 3);

  blendPalette(l.w || [1, 0, 0], 'grass', _rgb);
  const gr = _rgb[0] / 255, gg = _rgb[1] / 255, gb = _rgb[2] / 255;
  blendPalette(l.w || [1, 0, 0], 'rock', _rgb);
  const rr = _rgb[0] / 255, rg = _rgb[1] / 255, rb = _rgb[2] / 255;

  // The true surface, not l.y: a settled base sits below its own ground.
  P[0] = l.x; P[1] = sampleHeight(l.x, l.z, seed) - CAP_SINK; P[2] = l.z;
  C[0] = gr * 0.92; C[1] = gg * 0.92; C[2] = gb * 0.92;

  for (let r = 0; r < CAP_RINGS.length; r++) {
    const outer = r === CAP_RINGS.length - 1;
    for (let s = 0; s < CAP_SEG; s++) {
      const a = (s / CAP_SEG) * TAU;
      const x = l.x + Math.cos(a) * CAP_RINGS[r];
      const z = l.z + Math.sin(a) * CAP_RINGS[r];
      const v = (1 + r * CAP_SEG + s) * 3;
      P[v] = x;
      P[v + 1] = sampleHeight(x, z, seed) - CAP_SINK - (outer ? CAP_SKIRT : 0);
      P[v + 2] = z;
      // The skirt is rock and much darker: it is a shaded flank seen edge-on.
      const t = outer ? 1 : r / CAP_RINGS.length;
      const k = outer ? 0.46 : 0.90 - r * 0.06;
      C[v] = (gr + (rr - gr) * t) * k;
      C[v + 1] = (gg + (rg - gg) * t) * k;
      C[v + 2] = (gb + (rb - gb) * t) * k;
    }
  }

  let t = 0;
  for (let s = 0; s < CAP_SEG; s++) {
    I[t++] = 0; I[t++] = 1 + s; I[t++] = 1 + ((s + 1) % CAP_SEG);
  }
  for (let r = 0; r < CAP_RINGS.length - 1; r++) {
    for (let s = 0; s < CAP_SEG; s++) {
      const a = 1 + r * CAP_SEG + s;
      const b = 1 + r * CAP_SEG + ((s + 1) % CAP_SEG);
      const c = a + CAP_SEG, d = b + CAP_SEG;
      I[t++] = a; I[t++] = c; I[t++] = b;
      I[t++] = b; I[t++] = c; I[t++] = d;
    }
  }
  return { P, C, I, nv };
}

/** Concatenate the caps of every landmark currently outside the terrain rings. */
function rebuildCaps(lm, farSet) {
  if (farSet.length === 0) { lm.caps.visible = false; return; }

  let nv = 0, ni = 0;
  for (let j = 0; j < farSet.length; j++) {
    const i = farSet[j];
    if (!lm.capCache[i]) lm.capCache[i] = buildCap(lm, i);
    nv += lm.capCache[i].nv;
    ni += lm.capCache[i].I.length;
  }

  const P = new Float32Array(nv * 3), C = new Float32Array(nv * 3);
  const I = nv > 65535 ? new Uint32Array(ni) : new Uint16Array(ni);
  let vo = 0, io = 0;
  for (let j = 0; j < farSet.length; j++) {
    const c = lm.capCache[farSet[j]];
    P.set(c.P, vo * 3);
    C.set(c.C, vo * 3);
    for (let i = 0; i < c.I.length; i++) I[io + i] = vo + c.I[i];
    vo += c.nv; io += c.I.length;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(P, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(C, 3));
  geo.setIndex(new THREE.BufferAttribute(I, 1));
  geo.computeVertexNormals();
  geo.computeBoundingSphere();
  lm.caps.geometry.dispose();
  lm.caps.geometry = geo;
  lm.caps.visible = true;
}

// ————————————————————————————————— verification

/**
 * The composition rule, queryable at run time: does each 90-degree sector
 * around this position contain a landmark silhouette?
 *
 * tools/test/landmarks.test.js calls the sim-layer sectorReport() directly;
 * this wrapper exists so the HUD and any in-game debug view can ask the same
 * question of the live state without knowing where the list came from.
 */
export function landmarkSectors(lm, x, z, eyeY) {
  return sectorReport(lm.landmarks, x, z, lm.seed, eyeY);
}

export { COMPOSE };
