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
import { patchLambert, createWaterMaterial, sharedLighting } from './materials.js';

export const CHUNK_TILES = 16;
export const CHUNK_SIZE = CHUNK_TILES * TILE;   // 128 m

/** Vertices per side by LOD level. Ring 0 is walkable; ring 2 is scenery. */
const LOD_RES = [33, 17, 9, 5];
/** Ring radii in chunks. Load at R, unload at R+1 — hysteresis everywhere. */
export const RINGS = [1, 3, 7];

const _w = new Float32Array(3);
const _c = new Float32Array(3);

const chunkKey = (cx, cz) => ((cx & 0xffff) << 16) | (cz & 0xffff);

/** Water is only meshed per chunk out to this LOD; beyond it the ocean ring
 *  takes over. One draw call per coastal chunk is affordable; 225 is not. */
const WATER_LOD = 1;
/** Half-width of the hole in the ocean ring, in metres. Must stay inside the
 *  near-water coverage, which is at least 3 chunks (384 m) in every direction. */
const OCEAN_HOLE = 320;
const OCEAN_REACH = 3000;

/**
 * Build one chunk's geometry. A pure function of (seed, cx, cz, lod), which is
 * why it could move to a Worker unchanged — no shared state to synchronise,
 * and no determinism risk because the output only feeds rendering.
 *
 * `out`, if given, receives `out.water`: a matching water surface for the wet
 * part of the chunk, or null. It costs nothing to produce — the seabed heights
 * it needs are the same samples the terrain surface just took.
 */
export function buildChunkGeometry(seed, cx, cz, lod, out = null) {
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

  if (out) out.water = lod <= WATER_LOD ? buildWaterGeometry(positions, res, sea) : null;
  return geo;
}

const _blendScratch = new Float32Array(3);
function _c2(weights, key) {
  return blendPalette(weights, key, _blendScratch);
}

/**
 * A flat surface at sea level over the wet cells of a chunk, carrying the
 * water DEPTH per vertex. That attribute is the whole trick: it is what lets
 * the water shader draw a shoreline — foam, opacity and body colour all fall
 * out of how far the seabed is below the surface — with no depth-buffer read,
 * which a tile GPU would make us pay for twice.
 */
function buildWaterGeometry(positions, res, sea) {
  const n = res * res;
  const depth = new Float32Array(n);
  let wet = 0;
  for (let k = 0; k < n; k++) {
    const d = sea - positions[k * 3 + 1];
    if (d > 0) { depth[k] = d; wet++; }
  }
  if (wet === 0) return null;

  const idx = [];
  for (let j = 0; j < res - 1; j++) {
    for (let i = 0; i < res - 1; i++) {
      const a = j * res + i, b = a + 1, c = a + res, d = c + 1;
      // Keep any quad with a wet corner, so the band that carries the foam
      // reaches all the way up to the waterline.
      if (depth[a] + depth[b] + depth[c] + depth[d] <= 0) continue;
      idx.push(a, c, b, b, c, d);
    }
  }
  if (idx.length === 0) return null;

  const pos = new Float32Array(n * 3);
  for (let k = 0; k < n; k++) {
    pos[k * 3] = positions[k * 3];
    pos[k * 3 + 1] = sea;
    pos[k * 3 + 2] = positions[k * 3 + 2];
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('aDepth', new THREE.BufferAttribute(depth, 1));
  geo.setIndex(idx);
  geo.computeBoundingSphere();
  return geo;
}

/**
 * The far ocean: a square annulus rather than a plane, so the shoreline water
 * inside the hole is never seen through a second, deeper surface. Four quads,
 * one draw call, and it sits slightly low so it cannot z-fight the chunk water
 * it overlaps at the seam.
 */
function buildOceanRing(inner, outer) {
  const rects = [
    [-outer, -outer, outer, -inner], [-outer, inner, outer, outer],
    [-outer, -inner, -inner, inner], [inner, -inner, outer, inner],
  ];
  const pos = new Float32Array(rects.length * 12);
  const depth = new Float32Array(rects.length * 4).fill(40);
  const idx = [];
  rects.forEach(([x0, z0, x1, z1], r) => {
    const corners = [x0, z0, x1, z0, x0, z1, x1, z1];
    for (let v = 0; v < 4; v++) {
      const k = (r * 4 + v) * 3;
      pos[k] = corners[v * 2]; pos[k + 1] = 0; pos[k + 2] = corners[v * 2 + 1];
    }
    const b = r * 4;
    idx.push(b, b + 2, b + 1, b + 1, b + 2, b + 3);
  });
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('aDepth', new THREE.BufferAttribute(depth, 1));
  geo.setIndex(idx);
  geo.computeBoundingSphere();
  return geo;
}

export function createTerrainManager(gfx, seed, lit = sharedLighting()) {
  // Vertex colours already carry albedo x baked AO; the patch adds the sky
  // gradient, the wrap, the rim, the cloud shadows and the atmosphere on top
  // WITHOUT darkening anything further.
  const material = patchLambert(new THREE.MeshLambertMaterial({
    vertexColors: true,
    flatShading: false,
  }), lit);

  const group = new THREE.Group();
  group.matrixAutoUpdate = false;
  gfx.scene.add(group);

  const waterMat = createWaterMaterial(lit);
  const water = new THREE.Mesh(buildOceanRing(OCEAN_HOLE, OCEAN_REACH),
    createWaterMaterial(lit, { transparent: false }));
  water.position.y = seaLevelMetres() - 0.45;
  water.receiveShadow = false;
  gfx.scene.add(water);

  return {
    seed, material, group, water, waterMat,
    chunks: new Map(),          // key -> {mesh, wmesh, lod, cx, cz}
    queue: [],
    budgetPerFrame: 2,          // build at most two chunks a frame
  };
}

/** Scratch for buildChunkGeometry's second output. Never escapes this module. */
const _built = { water: null };

function dropChunk(tm, ch) {
  tm.group.remove(ch.mesh);
  ch.mesh.geometry.dispose();
  if (ch.wmesh) { tm.group.remove(ch.wmesh); ch.wmesh.geometry.dispose(); }
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
    if (old) dropChunk(tm, old);

    _built.water = null;
    const geo = buildChunkGeometry(tm.seed, job.cx, job.cz, job.lod, _built);
    const mesh = new THREE.Mesh(geo, tm.material);
    mesh.matrixAutoUpdate = false;
    mesh.updateMatrix();
    mesh.castShadow = false;
    mesh.receiveShadow = job.lod === 0;
    tm.group.add(mesh);

    let wmesh = null;
    if (_built.water) {
      wmesh = new THREE.Mesh(_built.water, tm.waterMat);
      wmesh.matrixAutoUpdate = false;
      wmesh.updateMatrix();
      wmesh.renderOrder = 1;
      tm.group.add(wmesh);
    }
    tm.chunks.set(job.key, { mesh, wmesh, lod: job.lod, cx: job.cx, cz: job.cz });
    built++;
  }

  // Unload one ring further out than we load, so walking a boundary back and
  // forth does not thrash the builder.
  const dropR = maxR + 1;
  for (const [key, ch] of Array.from(tm.chunks.entries())) {
    if (Math.max(Math.abs(ch.cx - ccx), Math.abs(ch.cz - ccz)) > dropR) {
      dropChunk(tm, ch);
      tm.chunks.delete(key);
    }
  }

  tm.water.position.x = px;
  tm.water.position.z = pz;
  return built;
}
