// streams.js — named RNG stream ids.
// L2.
//
// APPEND ONLY. NEVER RENUMBER.
//
// Every id here derives an independent seed via streamSeed(worldSeed, id).
// Renumbering, or inserting in the middle, silently changes every world that
// was ever generated. Adding at the end changes nothing.

export const S = {
  WG_TERRAIN: 1,
  WG_RIVERS: 2,
  WG_SETTLE: 3,
  WG_ROADS: 4,
  WG_POI: 5,
  WG_NAMES: 6,
  WG_REGION: 7,

  CHUNK_SCATTER: 10,
  CHUNK_ENCOUNTER: 11,
  CHUNK_DETAIL: 12,

  SPAWN: 20,
  LOOT: 21,
  CRIT: 22,
  DMG_VAR: 23,

  AI_DECIDE: 30,
  AI_JITTER: 31,
  AI_DELAY: 32,

  MACRO_ECON: 40,
  MACRO_FACTION: 41,
  MACRO_RUMOR: 42,
  MACRO_NPC: 43,
  MACRO_EVENT: 44,

  QUESTGEN: 50,
  SHOP: 51,
  NAMEGEN: 52,
};

/**
 * Which streams are STATEFUL (a running generator) versus STATELESS
 * (positional hash).
 *
 * Stateless is the default and the safer choice: it is immune to call-count
 * drift, so a tweak that makes one system draw one extra number this build
 * does not shift anything downstream. Only genuinely sequential processes —
 * the worldgen passes and the macro tick — get a stateful generator.
 *
 * Rule: any RNG use whose CALL COUNT could plausibly change with a gameplay
 * tweak must be stateless.
 */
export const STATEFUL_STREAMS = [
  S.WG_TERRAIN, S.WG_RIVERS, S.WG_SETTLE, S.WG_ROADS, S.WG_POI, S.WG_NAMES,
  S.MACRO_ECON, S.MACRO_FACTION, S.MACRO_RUMOR, S.MACRO_NPC, S.MACRO_EVENT,
];
