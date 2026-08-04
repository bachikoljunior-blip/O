// terrain.js — chunked terrain with distance LOD.
// L4.
//
// Chunked quadtree rather than geometry clipmaps: clipmaps push the work onto
// the GPU via vertex-shader texture fetch, and that is exactly the path iOS
// 16.4 regressed (three.js #25741). A CPU-built chunk mesh is boring and works
// on every device we care about.
//
// Lighting is BAKED into vertex colours at build time — a procedural world
// cannot ship pre-baked lightmaps, so we bake per chunk as it streams in and
// then read it for free forever after.

import * as THREE from '../../vendor/three.module.js';
import { sampleHeight, seaLevelMetres, TILE } from '../sim/world/heightfield.js';
import { regionWeights, blendPalette } from '../data/regions.js';
import { clamp01, lerp, smoothstep } from '../core/math.js';
import { fbm } from '../core/noise.js';

export const CHUNK_TILES = 16;
export const CHUNK_SIZE = CHUNK_TILES * TILE;   // 128 m

/** Vertices per side by LOD level. Ring 0 is walkable; ring 2 is scenery. */
const LOD_RES = [33, 17, 9, 5];
/** Ring radii in chunks. Load at R, unload at R+1 — hysteresis everywhere. */
export const RINGS = [1, 3, 7];

const _w = new Float32Array(3);
const _c = new Float32Array(3);

const chunkKey = (cx, cz) => ((cx & 0xffff) << 16) | (cz & 0xffff);

/**
 * Build one chunk's geometry. A pure function of (seed, cx, cz, lod), which is
 * why it could move to a Worker unchanged — no shared state to synchronise,
 * and no determinism risk because the output only feeds rendering.
 */
export function buildChunkGeometry(seed, cx, cz, lod) {
  const res = LOD_RES[Math.min(lod, LOD_RES.length - 1)];
  const step = CHUNK_SIZE / (res - 1);
  const ox = cx * CHUNK_SIZE, oz = cz * CHUNK_SIZE;

  const count = res * res;
  const positions = new Float32Array(count * 3);
  const normals = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const indices = new Uint16Array((res - 1) * (res - 1) * 6);

  const sea = seaLevelMetres();

  for (let j = 0; j < res; j++) {
    for (let i = 0; i < res; i++) {
      const k = j * res + i;
      const x = ox + i * step;
      const z = oz + j * step;
      const h = sampleHeight(x, z, seed);

      positions[k * 3] = x;
      positions[k * 3 + 1] = h;
      positions[k * 3 + 2] = z;

      // Normal by central difference at the chunk's own resolution, so LOD
      // seams shade consistently instead of showing a lighting crack.
      const e = step;
      const hL = sampleHeight(x - e, z, seed), hR = sampleHeight(x + e, z, seed);
      const hD = sampleHeight(x, z - e, seed), hU = sampleHeight(x, z + e, seed);
      let nx = hL - hR, ny = 2 * e, nz = hD - hU;
      const inv = 1 / (Math.sqrt(nx * nx + ny * ny + nz * nz) || 1);
      nx *= inv; ny *= inv; nz *= inv;
      normals[k * 3] = nx; normals[k * 3 + 1] = ny; normals[k * 3 + 2] = nz;

      // ——— surface colour ———
      regionWeights(x, z, seed, _w);
      const above = h - sea;
      const slope = 1 - ny;                     // 0 flat, 1 vertical

      let key = 'grass';
      if (above < 1.2) key = 'sand';
      else if (slope > 0.55) key = 'rock';
      else if (above > 190) key = 'snow';
      blendPalette(_w, key, _c);

      // Blend toward soil in hollows and rock on steep faces so the ground
      // reads as varied without needing a single texture.
      if (key === 'grass') {
        const patch = fbm(x * 0.008, z * 0.008, seed + 601, 3);
        const alt = _c2(_w, patch > 0.58 ? 'soil' : 'foliage');
        const t = smoothstep(clamp01(Math.abs(patch - 0.5) * 2)) * 0.80;
        _c[0] = lerp(_c[0], alt[0], t);
        _c[1] = lerp(_c[1], alt[1], t);
        _c[2] = lerp(_c[2], alt[2], t);
      }

      // Baked ambient occlusion: valleys darker than ridges. This is most of
      // what makes an untextured surface read as three-dimensional.
      // Baked AO plus a fine break-up octave. Without the second term a
      // Lambert hillside is a single value and the shape disappears.
      const grain = 0.90 + fbm(x * 0.032, z * 0.032, seed + 613, 2) * 0.20;
      const ao = clamp01(0.52 + ny * 0.48) * clamp01(0.72 + above / 240) * grain;
      colors[k * 3] = (_c[0] / 255) * ao;
      colors[k * 3 + 1] = (_c[1] / 255) * ao;
      colors[k * 3 + 2] = (_c[2] / 255) * ao;
    }
  }

  let t = 0;
  for (let j = 0; j < res - 1; j++) {
    for (let i = 0; i < res - 1; i++) {
      const a = j * res + i, b = a + 1, c = a + res, d = c + 1;
      indices[t++] = a; indices[t++] = c; indices[t++] = b;
      indices[t++] = b; indices[t++] = c; indices[t++] = d;
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geo.setIndex(new THREE.BufferAttribute(indices, 1));
  geo.computeBoundingSphere();
  return geo;
}

const _blendScratch = new Float32Array(3);
function _c2(weights, key) {
  return blendPalette(weights, key, _blendScratch);
}

export function createTerrainManager(gfx, seed) {
  const material = new THREE.MeshLambertMaterial({
    vertexColors: true,
    flatShading: false,
  });

  const group = new THREE.Group();
  group.matrixAutoUpdate = false;
  gfx.scene.add(group);

  const water = new THREE.Mesh(
    new THREE.PlaneGeometry(6000, 6000),
    new THREE.MeshLambertMaterial({ color: 0x243b52, transparent: true, opacity: 0.86 })
  );
  water.rotation.x = -Math.PI / 2;
  water.position.y = seaLevelMetres();
  water.receiveShadow = false;
  gfx.scene.add(water);

  return {
    seed, material, group, water,
    chunks: new Map(),          // key -> {mesh, lod, cx, cz}
    queue: [],
    budgetPerFrame: 2,          // build at most two chunks a frame
  };
}

function lodForRing(dist) {
  if (dist <= RINGS[0]) return 0;
  if (dist <= RINGS[1]) return 1;
  if (dist <= RINGS[2]) return 2;
  return 3;
}

/** Stream chunks around a world position. Hysteresis on the unload boundary. */
export function updateTerrain(tm, gfx, px, pz) {
  const ccx = Math.floor(px / CHUNK_SIZE);
  const ccz = Math.floor(pz / CHUNK_SIZE);
  const maxR = RINGS[RINGS.length - 1];

  // Queue anything missing or at the wrong LOD, nearest first.
  tm.queue.length = 0;
  for (let dz = -maxR; dz <= maxR; dz++) {
    for (let dx = -maxR; dx <= maxR; dx++) {
      const cheb = Math.max(Math.abs(dx), Math.abs(dz));
      if (cheb > maxR) continue;
      const cx = ccx + dx, cz = ccz + dz;
      const key = chunkKey(cx, cz);
      const want = lodForRing(cheb);
      const have = tm.chunks.get(key);
      if (!have || have.lod !== want) {
        tm.queue.push({ key, cx, cz, lod: want, d: dx * dx + dz * dz });
      }
    }
  }
  // Deterministic order: distance, then key. Never rely on Map iteration.
  tm.queue.sort((a, b) => (a.d - b.d) || (a.key - b.key));

  let built = 0;
  for (let i = 0; i < tm.queue.length && built < tm.budgetPerFrame; i++) {
    const job = tm.queue[i];
    const old = tm.chunks.get(job.key);
    if (old) { tm.group.remove(old.mesh); old.mesh.geometry.dispose(); }

    const geo = buildChunkGeometry(tm.seed, job.cx, job.cz, job.lod);
    const mesh = new THREE.Mesh(geo, tm.material);
    mesh.matrixAutoUpdate = false;
    mesh.updateMatrix();
    mesh.castShadow = false;
    mesh.receiveShadow = job.lod === 0;
    tm.group.add(mesh);
    tm.chunks.set(job.key, { mesh, lod: job.lod, cx: job.cx, cz: job.cz });
    built++;
  }

  // Unload one ring further out than we load, so walking a boundary back and
  // forth does not thrash the builder.
  const dropR = maxR + 1;
  for (const [key, ch] of Array.from(tm.chunks.entries())) {
    if (Math.max(Math.abs(ch.cx - ccx), Math.abs(ch.cz - ccz)) > dropR) {
      tm.group.remove(ch.mesh);
      ch.mesh.geometry.dispose();
      tm.chunks.delete(key);
    }
  }

  tm.water.position.x = px;
  tm.water.position.z = pz;
  return built;
}
