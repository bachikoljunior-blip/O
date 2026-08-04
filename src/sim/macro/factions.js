// factions.js — autonomous faction agents.
// L2.
//
// Factions act whether or not the player is watching. In every reference
// title, war is set dressing: territory never changes hands unless a script
// says so. Here an agenda ticks forward on its own resources.

import { clamp } from '../../core/math.js';
import { hash3 } from '../../core/noise.js';
import { streamSeed } from '../../core/rng.js';
import { S as STREAM } from '../determinism/streams.js';

export const AGENDA = { HOLD: 0, EXPAND: 1, RAID: 2, FORTIFY: 3, TRADE: 4 };
export const AGENDA_NAME = ['守勢', '拡張', '略奪', '築城', '交易'];

export function createFactions(defs) {
  return defs.map((d, i) => ({
    id: d.id,
    index: i,
    name: d.name,
    territory: new Set(d.territory || []),   // point-queried only, never iterated in sim
    territoryList: (d.territory || []).slice().sort(),  // the iterable form
    treasury: d.treasury ?? 400,
    strength: d.strength ?? 50,
    agenda: AGENDA.HOLD,
    agendaProgress: 0,
    grievances: [],
    standing: new Float32Array(defs.length),
  }));
}

function chooseAgenda(w, f, hours) {
  const seed = streamSeed(w.seed, STREAM.MACRO_FACTION);
  const r = hash3(f.index, w.macro.hour, 0, seed);
  if (f.strength > 70 && f.treasury > 600) return r < 0.6 ? AGENDA.EXPAND : AGENDA.RAID;
  if (f.strength < 30) return r < 0.7 ? AGENDA.FORTIFY : AGENDA.HOLD;
  if (f.treasury < 150) return AGENDA.TRADE;
  return r < 0.4 ? AGENDA.TRADE : AGENDA.HOLD;
}

export function factionTick(w, hours = 1) {
  const macro = w.macro;
  const factions = macro.factions;

  for (let i = 0; i < factions.length; i++) {
    const f = factions[i];

    // Income scales with held territory; upkeep with standing army.
    f.treasury += f.territoryList.length * 1.8 * hours - f.strength * 0.12 * hours;
    f.treasury = clamp(f.treasury, 0, 100000);
    f.strength = clamp(f.strength + (f.treasury > 300 ? 0.25 : -0.35) * hours, 5, 100);

    if (f.agendaProgress <= 0) {
      f.agenda = chooseAgenda(w, f, hours);
      f.agendaProgress = 24 + (f.index * 7) % 40;
    }
    f.agendaProgress -= hours;

    switch (f.agenda) {
      case AGENDA.EXPAND: {
        if (f.treasury < 300) break;
        // Take a neighbouring settlement not already held.
        const target = macro.settlementIds.find((sid) => !f.territory.has(sid));
        if (target !== undefined && f.strength > 60) {
          const holder = factions.find((o) => o.territory.has(target));
          if (holder && holder.index !== f.index && holder.strength >= f.strength) break;
          if (holder) {
            holder.territory.delete(target);
            holder.territoryList = holder.territoryList.filter((x) => x !== target);
            holder.grievances.push({ against: f.index, hour: macro.hour, kind: 'seized' });
          }
          f.territory.add(target);
          f.territoryList.push(target);
          f.territoryList.sort();
          f.treasury -= 300;
          f.strength -= 18;
          macro.pending.push({
            importance: 4, kind: 'territory',
            text: `${f.name}が${target}を占拠した。`,
          });
        }
        break;
      }
      case AGENDA.FORTIFY:
        f.strength += 0.5 * hours;
        f.treasury -= 2 * hours;
        break;
      case AGENDA.TRADE:
        f.treasury += 3.5 * hours;
        break;
      case AGENDA.RAID:
        // Raids are SAMPLED in worldEvents, not simulated here; this only sets
        // the pressure that makes them more likely.
        macro.raidPressure = clamp((macro.raidPressure || 0) + 0.02 * hours, 0, 1);
        break;
    }

    // Grievances decay, so old slights stop driving behaviour forever.
    if (f.grievances.length > 8) f.grievances.splice(0, f.grievances.length - 8);
  }
}
