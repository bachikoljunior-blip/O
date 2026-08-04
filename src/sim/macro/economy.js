// economy.js — commodity flow across the settlement graph.
// L2.
//
// Every reference title uses a static price table with, at most, a merchant
// gold cap. None simulate production, transport, stock or price discovery, so
// none can produce the chain this file exists for: burn the granary, bread
// price spikes, hunger spreads, banditry rises.

import { powInt } from '../../core/fixed.js';
import { clamp } from '../../core/math.js';
import { S as STREAM } from '../determinism/streams.js';

export const GOODS = ['grain', 'iron', 'wood', 'cloth', 'medicine', 'fish'];
export const GOODS_N = GOODS.length;

/** How fast a price relaxes back to equilibrium, per in-game hour. */
const RELAX = 0.96;
/** Stock regrowth toward capacity, per hour. */
const REGROW = 0.97;

export function createMarket(settlements) {
  const m = [];
  for (let i = 0; i < settlements.length; i++) {
    const st = settlements[i];
    m.push({
      id: st.id,
      stock: new Float32Array(GOODS_N),
      cap: new Float32Array(GOODS_N),
      price: new Float32Array(GOODS_N),
      basePrice: new Float32Array(GOODS_N),
      produces: st.produces || [],
      consumes: st.consumes || [],
      lastHour: 0,
    });
    const mk = m[m.length - 1];
    for (let g = 0; g < GOODS_N; g++) {
      const scale = 1 + (st.size || 1) * 0.6;
      mk.cap[g] = 60 * scale;
      mk.stock[g] = mk.cap[g] * 0.7;
      mk.basePrice[g] = [8, 26, 5, 14, 40, 7][g];
      mk.price[g] = mk.basePrice[g];
    }
  }
  return m;
}

/** Price implied by how full the stockpile is. Scarcity is the only driver. */
function equilibriumPrice(mk, g) {
  const fill = clamp(mk.stock[g] / Math.max(1, mk.cap[g]), 0.02, 2);
  // Cheap when overflowing, steep when nearly empty.
  return mk.basePrice[g] * clamp(1.6 - fill, 0.35, 3.5);
}

export function economyTick(w, hours = 1) {
  const macro = w.macro;
  const market = macro.market;

  for (let i = 0; i < market.length; i++) {
    const mk = market[i];
    for (let g = 0; g < GOODS_N; g++) {
      // Production and consumption.
      if (mk.produces.indexOf(GOODS[g]) !== -1) mk.stock[g] += 2.2 * hours;
      if (mk.consumes.indexOf(GOODS[g]) !== -1) mk.stock[g] -= 1.4 * hours;
      mk.stock[g] -= 0.25 * hours;                       // baseline draw
      if (mk.stock[g] < 0) mk.stock[g] = 0;
      if (mk.stock[g] > mk.cap[g]) mk.stock[g] = mk.cap[g];

      const eq = equilibriumPrice(mk, g);
      mk.price[g] = eq + (mk.price[g] - eq) * powInt(RELAX, hours);
    }
  }

  // Caravans move goods along the road graph. A cut road means the goods
  // simply do not arrive, and the price downstream tells the player so.
  const roads = macro.roads || [];
  for (let r = 0; r < roads.length; r++) {
    const road = roads[r];
    if (road.blocked) continue;
    const a = market[road.a], b = market[road.b];
    if (!a || !b) continue;
    for (let g = 0; g < GOODS_N; g++) {
      const diff = a.stock[g] - b.stock[g];
      const flow = clamp(diff * 0.04 * hours, -6 * hours, 6 * hours);
      a.stock[g] -= flow;
      b.stock[g] += flow;
    }
  }
}

/**
 * Closed-form advance for a long absence. Everything here is exponential decay
 * toward a target, so 720 hours costs ~17 multiplies rather than 720 loops.
 */
export function economyCatchUp(w, hours) {
  const market = w.macro.market;
  const relax = powInt(RELAX, hours);
  const regrow = powInt(REGROW, hours);

  for (let i = 0; i < market.length; i++) {
    const mk = market[i];
    for (let g = 0; g < GOODS_N; g++) {
      const produces = mk.produces.indexOf(GOODS[g]) !== -1;
      const consumes = mk.consumes.indexOf(GOODS[g]) !== -1;
      // Stock relaxes toward the level implied by net production.
      const net = (produces ? 2.2 : 0) - (consumes ? 1.4 : 0) - 0.25;
      const target = clamp(mk.cap[g] * (0.5 + net * 0.25), 0, mk.cap[g]);
      mk.stock[g] = target + (mk.stock[g] - target) * regrow;

      const eq = equilibriumPrice(mk, g);
      mk.price[g] = eq + (mk.price[g] - eq) * relax;
    }
  }
}

export function priceOf(w, settlementIdx, good) {
  const g = GOODS.indexOf(good);
  if (g < 0) return 0;
  return w.macro.market[settlementIdx].price[g];
}

export function stockOf(w, settlementIdx, good) {
  const g = GOODS.indexOf(good);
  if (g < 0) return 0;
  return w.macro.market[settlementIdx].stock[g];
}
