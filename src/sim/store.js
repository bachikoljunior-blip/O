// store.js — struct-of-arrays entity storage.
// L2.
//
// Not a general ECS. At 50-200 active entities an archetype graph or sparse
// sets cost more bookkeeping than they save, and both add a determinism
// surface (iteration order depending on archetype/insertion history).
//
// The reasons this beats an array of plain objects are not raw speed — at this
// scale that difference is under a fifth of a millisecond. They are:
//   * zero steady-state allocation, so no GC pauses on a phone
//   * NaN sweeps and delta hashing become one tight loop over typed arrays
//   * a Float32Array structured-clones into IndexedDB essentially for free
//   * the previous build's Actor -> Player/Enemy/NPC hierarchy grew inheritance
//     and cross-references within a single file, and this shape cannot

import { MAX_ENTITIES, BUFFER_SLOTS } from './components.js';
import { makeHandle } from '../core/handles.js';

export function createStore(n = MAX_ENTITIES) {
  const f = () => new Float32Array(n);
  const s = {
    n,
    liveN: 0,
    count: 0,

    gen: new Uint16Array(n),
    mask: new Uint32Array(n),
    kind: new Uint16Array(n),
    live: new Int32Array(n),      // ALWAYS ascending — the determinism anchor
    free: new Int32Array(n),
    freeTop: 0,

    // transform / motion
    px: f(), py: f(), pz: f(), yaw: f(), radius: f(), height: f(),
    vx: f(), vy: f(), vz: f(),
    kx: f(), ky: f(), kz: f(),    // knockback impulse, decays separately

    // vitals
    hp: f(), hpMax: f(),
    poise: f(), poiseMax: f(),                    // per-hit stagger resistance
    stance: f(), stanceMax: f(), stanceIdleT: f(),// the break meter
    stamina: f(), staminaMax: f(), staRegenDelay: f(),
    iframeT: f(), vulnerableT: f(),

    // gait: distance-driven stride phase, so footsteps track movement not ticks
    gaitPhase: f(),
    // riposte: set on a successful parry, counts down on the RAW clock
    riposteT: f(),

    // combat state machine
    stateId: new Uint8Array(n),
    attackId: new Uint16Array(n),
    phaseT: f(),                  // TICKS into the current phase, scaled by motionScale
    stateT: f(),                  // TICKS in the current state, raw
    comboIdx: new Uint8Array(n),
    hitConfirm: new Uint8Array(n),
    fired: new Uint8Array(n),      // ranged: projectile already launched
    blockT: f(),
    hitMask: new Uint32Array(n * 16),   // 512 bits: "already hit by this swing"

    // input buffer
    bufAct: new Uint8Array(n * BUFFER_SLOTS),
    bufTtl: new Int8Array(n * BUFFER_SLOTS),

    // ai
    aiState: new Uint8Array(n),
    aiTimer: f(),
    aiTarget: new Int32Array(n),  // handle, not index
    aggro: f(),
    homeX: f(), homeZ: f(),
    cooldown: f(),
    moveIdx: new Uint8Array(n),

    // stats
    atk: f(), def: f(), crit: f(),
    level: new Uint16Array(n),

    // projectiles: who fired this, as a HANDLE (never a raw index — the
    // shooter can die and have their slot recycled mid-flight)
    owner: new Int32Array(n),

    // world binding
    chunkKey: new Int32Array(n),
    persistId: new Uint32Array(n),  // 0 = ephemeral, regenerated from seed
  };
  // Free list starts full, highest index first so allocation walks upward.
  for (let i = 0; i < n; i++) s.free[i] = n - 1 - i;
  s.freeTop = n;
  return s;
}

/** Allocate a slot. Returns a HANDLE, or NULL_HANDLE (-1) when full. */
export function spawn(store, kind, mask) {
  if (store.freeTop === 0) return -1;
  const i = store.free[--store.freeTop];
  store.mask[i] = mask >>> 0;
  store.kind[i] = kind;
  store.count++;
  resetSlot(store, i);
  return makeHandle(i, store.gen[i]);
}

/** Zero every column for a slot so recycled entities never inherit stale data. */
function resetSlot(s, i) {
  s.px[i] = 0; s.py[i] = 0; s.pz[i] = 0; s.yaw[i] = 0;
  s.radius[i] = 0.4; s.height[i] = 1.8;
  s.vx[i] = 0; s.vy[i] = 0; s.vz[i] = 0;
  s.kx[i] = 0; s.ky[i] = 0; s.kz[i] = 0;
  s.hp[i] = 1; s.hpMax[i] = 1;
  s.poise[i] = 0; s.poiseMax[i] = 0;
  s.stance[i] = 0; s.stanceMax[i] = 0; s.stanceIdleT[i] = 0;
  s.stamina[i] = 0; s.staminaMax[i] = 0; s.staRegenDelay[i] = 0;
  s.iframeT[i] = 0; s.vulnerableT[i] = 0;
  s.gaitPhase[i] = 0; s.riposteT[i] = 0;
  s.stateId[i] = 0; s.attackId[i] = 0;
  s.phaseT[i] = 0; s.stateT[i] = 0;
  s.comboIdx[i] = 0; s.hitConfirm[i] = 0; s.fired[i] = 0; s.blockT[i] = 0;
  s.aiState[i] = 0; s.aiTimer[i] = 0; s.aiTarget[i] = -1; s.aggro[i] = 0;
  s.homeX[i] = 0; s.homeZ[i] = 0; s.cooldown[i] = 0; s.moveIdx[i] = 0;
  s.atk[i] = 1; s.def[i] = 0; s.crit[i] = 0; s.level[i] = 1;
  s.owner[i] = -1;
  s.chunkKey[i] = 0; s.persistId[i] = 0;
  clearHitMask(s, i);
  const b = i * BUFFER_SLOTS;
  for (let k = 0; k < BUFFER_SLOTS; k++) { s.bufAct[b + k] = 0; s.bufTtl[b + k] = 0; }
}

export function despawn(store, i) {
  if (store.mask[i] === 0) return;
  store.mask[i] = 0;
  // Bumping the generation is what invalidates every handle still pointing here.
  store.gen[i] = (store.gen[i] + 1) & 0xffff;
  store.free[store.freeTop++] = i;
  store.count--;
}

export function clearHitMask(s, i) {
  const base = i * 16;
  for (let w = 0; w < 16; w++) s.hitMask[base + w] = 0;
}

export const wasHit = (s, attacker, target) =>
  (s.hitMask[attacker * 16 + (target >> 5)] >>> (target & 31)) & 1;

export const markHit = (s, attacker, target) => {
  s.hitMask[attacker * 16 + (target >> 5)] |= 1 << (target & 31);
};

/**
 * Rebuild the live list in ASCENDING slot order.
 *
 * This is the single most important line in the file. One 512-element sweep
 * (~0.5us) makes spawn and despawn order irrelevant to iteration order, which
 * is what lets the order-independence test pass and what stops a despawn
 * during one tick from silently reshuffling AI decisions in the next.
 */
export function rebuildLive(store) {
  let n = 0;
  const { mask, live } = store;
  for (let i = 0; i < store.n; i++) {
    if (mask[i] !== 0) live[n++] = i;
  }
  store.liveN = n;
}

/** Sweep every float column for non-finite values. Used by the soak tests. */
export function findNaN(store) {
  const cols = [
    'px', 'py', 'pz', 'yaw', 'vx', 'vy', 'vz', 'kx', 'ky', 'kz',
    'hp', 'hpMax', 'poise', 'stance', 'stamina', 'staRegenDelay',
    'iframeT', 'vulnerableT', 'phaseT', 'stateT', 'aiTimer', 'aggro',
    'gaitPhase', 'riposteT',
    'cooldown', 'atk', 'def', 'crit', 'blockT', 'stanceIdleT',
  ];
  for (let c = 0; c < cols.length; c++) {
    const arr = store[cols[c]];
    for (let k = 0; k < store.liveN; k++) {
      const i = store.live[k];
      if (!Number.isFinite(arr[i])) return { column: cols[c], entity: i, value: arr[i] };
    }
  }
  return null;
}
