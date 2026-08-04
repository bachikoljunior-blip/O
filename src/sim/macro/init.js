// init.js — build the macro layer from a generated world.
// L2.

import { createMarket } from './economy.js';
import { createFactions } from './factions.js';
import { createRumorState } from './rumor.js';
import { createChronicle } from './chronicle.js';

/**
 * Attach the macro simulation to a world.
 * `settlements` and `roadPairs` come from the world generator; if none are
 * supplied (unit tests, sandbox) a small synthetic graph stands in.
 */
export function attachMacro(w, settlements, roadPairs) {
  const st = settlements && settlements.length ? settlements : defaultSettlements();
  const roads = (roadPairs && roadPairs.length ? roadPairs : defaultRoads(st.length))
    .map((r) => ({ a: r.a, b: r.b, hours: r.hours ?? 6, blocked: false, blockedUntil: 0 }));

  w.macro = {
    hour: 0,
    settlements: st,
    settlementIds: st.map((s) => s.id),
    market: createMarket(st),
    factions: createFactions(defaultFactions(st)),
    rumor: createRumorState(st.length),
    chronicle: createChronicle(),
    roads,
    raidPressure: 0,
    pending: [],
  };
  return w.macro;
}

function defaultSettlements() {
  return [
    { id: '灰王都', size: 3, produces: ['iron', 'cloth'], consumes: ['grain', 'wood'] },
    { id: 'ヴェルハイム', size: 2, produces: ['grain'], consumes: ['iron', 'cloth'] },
    { id: '霧ノ里', size: 2, produces: ['fish', 'wood'], consumes: ['iron', 'medicine'] },
    { id: '棚田の村', size: 1, produces: ['grain', 'medicine'], consumes: ['iron'] },
    { id: '銹の宿場', size: 1, produces: ['iron'], consumes: ['grain', 'fish'] },
  ];
}

function defaultRoads(n) {
  const out = [];
  for (let i = 0; i < n - 1; i++) out.push({ a: i, b: i + 1, hours: 5 + (i % 3) });
  if (n > 2) out.push({ a: 0, b: n - 1, hours: 9 });
  return out;
}

function defaultFactions(st) {
  const ids = st.map((s) => s.id);
  return [
    { id: 'ash_crown', name: '灰の王冠', territory: ids.slice(0, 2), treasury: 800, strength: 62 },
    { id: 'mist_clan', name: '霧衆', territory: ids.slice(2, 4), treasury: 450, strength: 48 },
    { id: 'rust_nomads', name: '銹の遊牧民', territory: ids.slice(4), treasury: 260, strength: 35 },
  ];
}
