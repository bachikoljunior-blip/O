// scene.js — bind simulation entities to visual rigs, and scatter vegetation.
// L4.

import * as THREE from '../../vendor/three.module.js';
import { buildHumanoid, buildMachineBeast, animateRig } from './actors.js';
import { CHUNK_SIZE } from './terrain.js';
import { sampleHeight, seaLevelMetres } from '../sim/world/heightfield.js';
import { regionWeights, dominantRegion, R } from '../data/regions.js';
import { ENEMY_IDS, ENEMIES } from '../data/enemies.js';
import { KIND_PLAYER } from '../sim/archetypes.js';
import { attackByIndex } from '../data/attacks.js';
import { hash2 } from '../core/noise.js';
import { clamp01 } from '../core/math.js';

const _w = new Float32Array(3);

export function createActorPool(gfx) {
  return { rigs: new Map(), group: new THREE.Group(), pool: [] , scene: gfx.scene };
}

function rigFor(store, e) {
  const kind = store.kind[e];
  if (kind === KIND_PLAYER) return buildHumanoid(R.ASH, 1.0);
  const def = ENEMIES[ENEMY_IDS[kind]];
  if (!def) return buildHumanoid(R.ASH, 1.0);
  if (def.parts) return buildMachineBeast(def.height / 2.3);
  return buildHumanoid(def.region, def.height / 1.8);
}

/**
 * Sync visual rigs to the live entity set, then animate each from its own
 * simulation state. Interpolation between fixed steps happens here: the sim
 * runs at 60Hz and the renderer draws between those snapshots.
 */
export function syncActors(pool, gfx, world, alpha, dt, prev) {
  const s = world.store;
  const seen = new Set();

  for (let k = 0; k < s.liveN; k++) {
    const e = s.live[k];
    if ((s.mask[e] & 0x1) === 0) continue;   // C.TRANSFORM
    seen.add(e);

    let rig = pool.rigs.get(e);
    if (!rig || rig._kind !== s.kind[e]) {
      if (rig) gfx.scene.remove(rig.root);
      rig = rigFor(s, e);
      rig._kind = s.kind[e];
      gfx.scene.add(rig.root);
      pool.rigs.set(e, rig);
    }

    // Render-time interpolation from the previous fixed step.
    const px = prev ? prev.px[e] + (s.px[e] - prev.px[e]) * alpha : s.px[e];
    const py = prev ? prev.py[e] + (s.py[e] - prev.py[e]) * alpha : s.py[e];
    const pz = prev ? prev.pz[e] + (s.pz[e] - prev.pz[e]) * alpha : s.pz[e];

    rig.root.position.set(px, py, pz);
    // Sim yaw is measured in the xz plane from +x; three.js yaw is about +y.
    rig.root.rotation.y = -s.yaw[e] + Math.PI / 2;

    const speed = Math.sqrt(s.vx[e] * s.vx[e] + s.vz[e] * s.vz[e]);
    const atk = attackByIndex(s.attackId[e]);
    const phaseDur = Math.max(1,
      s.stateId[e] === 1 ? atk.windup : s.stateId[e] === 2 ? atk.active : atk.recovery);
    animateRig(rig, dt, speed, s.stateId[e], clamp01(s.phaseT[e] / phaseDur));
  }

  for (const [e, rig] of Array.from(pool.rigs.entries())) {
    if (!seen.has(e)) {
      gfx.scene.remove(rig.root);
      pool.rigs.delete(e);
    }
  }
}

// ————————————————————————————————— vegetation

/**
 * GPU-instanced scatter. One draw call per species per chunk ring, which is
 * the only way a 150-draw-call budget survives a forest.
 */
export function createVegetation(gfx, seed) {
  const trunkGeo = new THREE.CylinderGeometry(0.13, 0.20, 2.6, 5);
  const crownGeo = new THREE.ConeGeometry(1.25, 3.2, 6);
  const rockGeo = new THREE.DodecahedronGeometry(0.55, 0);
  const grassGeo = new THREE.PlaneGeometry(0.30, 0.40);

  const MAX = { tree: 900, rock: 500, grass: 3000 };

  const mk = (geo, matOpts, max) => {
    const m = new THREE.InstancedMesh(geo, new THREE.MeshLambertMaterial(matOpts), max);
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    m.castShadow = false;
    m.receiveShadow = false;
    m.frustumCulled = false;
    m.count = 0;
    gfx.scene.add(m);
    return m;
  };

  return {
    seed,
    trunk: mk(trunkGeo, { color: 0x5e4830 }, MAX.tree),
    crown: mk(crownGeo, { color: 0x527a44 }, MAX.tree),
    rock: mk(rockGeo, { color: 0x8b877e }, MAX.rock),
    grass: mk(grassGeo, { color: 0x88a85c, side: THREE.DoubleSide, transparent: true, opacity: 0.78 }, MAX.grass),
    MAX,
    lastCx: 9999, lastCz: 9999, lastPx: -1e9, lastPz: -1e9,
    _m: new THREE.Matrix4(),
    _q: new THREE.Quaternion(),
    _p: new THREE.Vector3(),
    _s: new THREE.Vector3(),
  };
}

/**
 * Rebuild the scatter when the player crosses a chunk boundary.
 * Positions come from a stateless hash of the tile coordinate, so nothing is
 * ever stored and the same world always grows the same forest.
 */
export function updateVegetation(veg, px, pz, quality = 1) {
  const ccx = Math.floor(px / CHUNK_SIZE);
  const ccz = Math.floor(pz / CHUNK_SIZE);
  const moved = (px - veg.lastPx) ** 2 + (pz - veg.lastPz) ** 2 > 18 * 18;
  if (ccx === veg.lastCx && ccz === veg.lastCz && !moved) return;
  veg.lastCx = ccx; veg.lastCz = ccz;
  veg.lastPx = px; veg.lastPz = pz;

  const GRASS_R = 42;
  const sea = seaLevelMetres();
  let nTree = 0, nRock = 0, nGrass = 0;
  const RADIUS = 3;
  const density = Math.max(1, Math.round(3 * quality));

  for (let dz = -RADIUS; dz <= RADIUS; dz++) {
    for (let dx = -RADIUS; dx <= RADIUS; dx++) {
      const cx = ccx + dx, cz = ccz + dz;
      const near = Math.max(Math.abs(dx), Math.abs(dz)) <= 1;
      const samples = near ? 34 : 16;

      for (let i = 0; i < samples; i++) {
        const hx = hash2(cx * 91 + i, cz * 71, veg.seed + 5);
        const hz = hash2(cx * 31, cz * 53 + i, veg.seed + 9);
        const x = cx * CHUNK_SIZE + hx * CHUNK_SIZE;
        const z = cz * CHUNK_SIZE + hz * CHUNK_SIZE;
        const y = sampleHeight(x, z, veg.seed);
        if (y < sea + 1.5) continue;

        regionWeights(x, z, veg.seed, _w);
        const region = dominantRegion(_w);
        const roll = hash2(cx * 7 + i, cz * 13, veg.seed + 17);

        // Rusted wasteland is sparse; the mist country is dense.
        const treeChance = region === R.RUST ? 0.18 : region === R.MIST ? 0.62 : 0.42;

        if (roll < treeChance && nTree < veg.MAX.tree) {
          const scale = 0.7 + hash2(i, cx + cz, veg.seed + 23) * 0.9;
          setInstance(veg, veg.trunk, nTree, x, y, z, scale, 0);
          setInstance(veg, veg.crown, nTree, x, y + 2.2 * scale, z, scale, 0);
          nTree++;
        } else if (roll < treeChance + 0.18 && nRock < veg.MAX.rock) {
          const scale = 0.5 + hash2(i * 3, cx, veg.seed + 29) * 1.4;
          setInstance(veg, veg.rock, nRock, x, y + 0.2 * scale, z, scale,
            hash2(i, cz, veg.seed + 31) * 6.28);
          nRock++;
        }

        // Grass only within GRASS_R of the player. Beyond that a 30cm quad is
        // sub-pixel noise that reads as litter, not as ground cover.
        const gdx = x - px, gdz = z - pz;
        if (gdx * gdx + gdz * gdz < GRASS_R * GRASS_R && nGrass < veg.MAX.grass) {
          for (let g = 0; g < density * 4; g++) {
            const gx = x + (hash2(i * 17 + g, cx, veg.seed + 37) - 0.5) * 16;
            const gz = z + (hash2(i * 19 + g, cz, veg.seed + 41) - 0.5) * 16;
            const ddx = gx - px, ddz = gz - pz;
            if (ddx * ddx + ddz * ddz > GRASS_R * GRASS_R) continue;
            const gy = sampleHeight(gx, gz, veg.seed);
            if (gy < sea + 1.0 || nGrass >= veg.MAX.grass) continue;
            setInstance(veg, veg.grass, nGrass, gx, gy + 0.18, gz,
              0.7 + hash2(g, i, veg.seed + 43) * 0.7,
              hash2(g * 5, i, veg.seed + 47) * 6.28);
            nGrass++;
          }
        }
      }
    }
  }

  veg.trunk.count = nTree; veg.trunk.instanceMatrix.needsUpdate = true;
  veg.crown.count = nTree; veg.crown.instanceMatrix.needsUpdate = true;
  veg.rock.count = nRock; veg.rock.instanceMatrix.needsUpdate = true;
  veg.grass.count = nGrass; veg.grass.instanceMatrix.needsUpdate = true;
}

function setInstance(veg, mesh, idx, x, y, z, scale, rotY) {
  veg._p.set(x, y, z);
  veg._q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), rotY);
  veg._s.setScalar(scale);
  veg._m.compose(veg._p, veg._q, veg._s);
  mesh.setMatrixAt(idx, veg._m);
}
