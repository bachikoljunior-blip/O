// world.js — the simulation container.
// L2.
//
// A BAG OF DATA. No methods. Any pull request that adds a method here, or adds
// a parameter to tick(), is the early warning that the 1,295-line god object
// from the previous build is re-forming, and should be treated as a review
// block rather than a style note.

import { createStore } from './store.js';
import { createEventQueue, createNoticeQueue } from '../core/bus.js';
import { createGrid } from '../core/spatial.js';
import { makeRNG, streamSeed } from '../core/rng.js';
import { MAX_ENTITIES } from './components.js';
import { S, STATEFUL_STREAMS } from './determinism/streams.js';

export function createWorld(seed = 1337) {
  const world = {
    seed: seed >>> 0,

    // ——— clocks ———
    // An INTEGER tick counter. Seconds are derived as tick/60. Ten hours of
    // `+= 1/60` accumulates float drift; an integer does not.
    tick: 0,
    /** 0 during hitstop, 1 otherwise. Freezes MOTION, never time. */
    motionScale: 1,
    hitstopT: 0,
    /** In-game clock, advanced from tick. */
    gameMinutes: 8 * 60,
    macroAccum: 0,

    store: createStore(MAX_ENTITIES),
    events: createEventQueue(512),
    notices: createNoticeQueue(),
    grid: createGrid(8, MAX_ENTITIES, 1024),

    player: -1,          // handle
    lockTarget: -1,      // handle
    lockHoldT: 0,
    lockSwitchT: 0,

    /** Stateful RNG streams, keyed by stream id. */
    rng: {},

    /** Scratch buffers so systems never allocate mid-tick. */
    scratch: {
      query: new Int32Array(MAX_ENTITIES),
      vec2: new Float64Array(2),
      candidates: new Int32Array(64),
    },

    /** Terrain sampler, installed by the world layer once generated. */
    terrain: null,

    /** Input profile: touch gets a wider parry window (device accommodation). */
    inputProfile: { parryWindowBonus: 0 },

    /** Counters the soak tests assert on. */
    stats: { spawned: 0, killed: 0, hits: 0, parries: 0, breaks: 0 },
  };

  for (const id of STATEFUL_STREAMS) {
    world.rng[id] = makeRNG(streamSeed(world.seed, id));
  }
  return world;
}

/** Seconds elapsed, derived rather than accumulated. */
export const worldSeconds = (w) => w.tick / 60;

/** In-game hour in [0,24). Drives the sky curve and NPC schedules. */
export const worldHour = (w) => (w.gameMinutes / 60) % 24;
