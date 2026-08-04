// rumor.js — knowledge as a simulated object.
// L2.
//
// The high-water mark across the reference titles is Kingdom Come's
// per-settlement reputation. None model propagation with latency, none model
// distortion in transit, and none let an NPC tell you WHO they heard it from.
//
// Facts are a fixed catalogue, so an NPC's knowledge is a 128-bit mask: O(1)
// storage forever, and spread is a bitwise OR along the settlement graph —
// cheap and idempotent, so propagation converges instead of oscillating.

import { shareKnowledge, knows, learn, createKnowledge } from '../../core/bits.js';
import { hash3 } from '../../core/noise.js';
import { streamSeed } from '../../core/rng.js';
import { S as STREAM } from '../determinism/streams.js';

/** Fact catalogue. Append only — the bit index is persisted in saves. */
export const FACT = {
  PLAYER_KILLED_TROLL: 0,
  PLAYER_CLEARED_CAMP: 1,
  ROAD_BLOCKED_NORTH: 2,
  GRAIN_SHORTAGE: 3,
  IRON_SHORTAGE: 4,
  FACTION_SEIZED_TOWN: 5,
  PLAYER_MURDERED_NPC: 6,
  PLAYER_SAVED_VILLAGE: 7,
  SHRINE_AWAKENED: 8,
  MACHINE_HERD_MOVED: 9,
};

export const FACT_TEXT = {
  [FACT.PLAYER_KILLED_TROLL]: '灰の巨兵が討たれたそうだ',
  [FACT.PLAYER_CLEARED_CAMP]: '野盗の砦が焼かれたらしい',
  [FACT.ROAD_BLOCKED_NORTH]: '北の街道が塞がれている',
  [FACT.GRAIN_SHORTAGE]: '麦が足りていない',
  [FACT.IRON_SHORTAGE]: '鉄が入ってこない',
  [FACT.FACTION_SEIZED_TOWN]: 'town が奪われたと聞いた',
  [FACT.PLAYER_MURDERED_NPC]: '旅人が人を殺めたという話だ',
  [FACT.PLAYER_SAVED_VILLAGE]: '村を救った者がいるらしい',
  [FACT.SHRINE_AWAKENED]: '古い祠に灯がともったとか',
  [FACT.MACHINE_HERD_MOVED]: '鉄の獣の群れが移動している',
};

/** A fact distorts as it travels. Each retelling can flip it to a neighbour. */
const DISTORTION = {
  [FACT.PLAYER_KILLED_TROLL]: FACT.PLAYER_SAVED_VILLAGE,
  [FACT.PLAYER_CLEARED_CAMP]: FACT.PLAYER_MURDERED_NPC,
  [FACT.GRAIN_SHORTAGE]: FACT.IRON_SHORTAGE,
};

export function createRumorState(settlementCount) {
  const known = [];
  for (let i = 0; i < settlementCount; i++) known.push(createKnowledge());
  return {
    known,
    /** Per settlement, per fact: when and from where it arrived. */
    source: new Map(),
  };
}

/** Seed a fact at its origin. Everything else is propagation. */
export function seedFact(w, settlementIdx, fact) {
  const r = w.macro.rumor;
  if (knows(r.known[settlementIdx], fact)) return false;
  learn(r.known[settlementIdx], fact);
  r.source.set(`${settlementIdx}:${fact}`, { hour: w.macro.hour, from: -1 });
  return true;
}

/**
 * Spread along the road graph at roughly the speed people travel.
 * A blocked road stops rumour as surely as it stops grain.
 */
export function rumorTick(w, hours = 1) {
  const macro = w.macro;
  const r = macro.rumor;
  const roads = macro.roads || [];
  const seed = streamSeed(w.seed, STREAM.MACRO_RUMOR);

  for (let i = 0; i < roads.length; i++) {
    const road = roads[i];
    if (road.blocked) continue;
    // Travel time gates how often a road can carry news.
    const chance = Math.min(1, (hours / Math.max(1, road.hours || 6)));
    const roll = hash3(i, macro.hour, 0, seed);
    if (roll > chance) continue;

    const a = r.known[road.a], b = r.known[road.b];
    if (!a || !b) continue;

    // Record provenance for whatever is genuinely new, before the OR.
    for (const factName of Object.keys(FACT)) {
      const f = FACT[factName];
      if (knows(a, f) && !knows(b, f)) {
        r.source.set(`${road.b}:${f}`, { hour: macro.hour, from: road.a });
      } else if (knows(b, f) && !knows(a, f)) {
        r.source.set(`${road.a}:${f}`, { hour: macro.hour, from: road.b });
      }
    }

    shareKnowledge(b, a);
    shareKnowledge(a, b);

    // Distortion: the retelling sometimes turns into a different fact.
    const dRoll = hash3(i, macro.hour, 1, seed);
    if (dRoll < 0.08) {
      for (const key of Object.keys(DISTORTION)) {
        const from = key | 0;
        if (knows(b, from)) {
          learn(b, DISTORTION[from]);
          r.source.set(`${road.b}:${DISTORTION[from]}`, { hour: macro.hour, from: road.a, garbled: true });
          break;
        }
      }
    }
  }
}

/** What does this settlement believe, and where did it hear it? */
export function rumorsAt(w, settlementIdx) {
  const r = w.macro.rumor;
  const out = [];
  for (const factName of Object.keys(FACT)) {
    const f = FACT[factName];
    if (!knows(r.known[settlementIdx], f)) continue;
    const src = r.source.get(`${settlementIdx}:${f}`);
    out.push({
      fact: f,
      text: FACT_TEXT[f],
      hour: src ? src.hour : 0,
      from: src ? src.from : -1,
      garbled: src ? !!src.garbled : false,
    });
  }
  return out;
}
