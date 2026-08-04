// chronicle.js — the record of what the simulation actually did.
// L2.
//
// THE CHRONICLE IS THE CONTRACT. A macro system that cannot produce a legible
// chronicle entry AND a findable in-world consequence does not ship. Twenty
// interacting systems with no readable output is a soup the player cannot
// perceive, and all that emergence work becomes invisible.
//
// This is also the narrative strategy: the story is the log of what happened,
// which is a category the reference titles do not compete in — rather than
// hand-written prose, which is a category we cannot win.

const HARD_CAP = 2000;

export function createChronicle() {
  return { entries: [], seq: 0 };
}

export function record(w, e) {
  const c = w.macro.chronicle;
  c.entries.push({
    seq: c.seq++,
    hour: e.hour ?? w.macro.hour,
    tick: e.tick ?? w.tick,
    importance: e.importance ?? 1,
    kind: e.kind || 'note',
    place: e.place || '',
    text: e.text || '',
  });
  if (c.entries.length > HARD_CAP * 1.5) chronicleCompact(w);
}

/**
 * Tiered compaction. Simple truncation would destroy the thing that makes
 * emergence legible, so importance and age both matter:
 *   - last 3 in-game days: keep everything
 *   - up to 30 days: keep importance >= 2
 *   - older: keep importance >= 4, and collapse same-kind runs into summaries
 */
export function chronicleCompact(w) {
  const c = w.macro.chronicle;
  const now = w.macro.hour;
  const kept = [];
  const runs = new Map();   // point-queried only

  for (let i = 0; i < c.entries.length; i++) {
    const e = c.entries[i];
    const age = now - e.hour;
    if (age <= 72) { kept.push(e); continue; }
    if (age <= 720) { if (e.importance >= 2) kept.push(e); continue; }
    if (e.importance >= 4) { kept.push(e); continue; }

    const key = `${e.kind}:${e.place}`;
    const r = runs.get(key);
    if (r) { r.count++; r.lastHour = e.hour; }
    else runs.set(key, { count: 1, kind: e.kind, place: e.place, firstHour: e.hour, lastHour: e.hour, seq: e.seq });
  }

  for (const r of Array.from(runs.values()).sort((a, b) => a.seq - b.seq)) {
    if (r.count < 2) continue;
    kept.push({
      seq: r.seq,
      hour: r.lastHour,
      tick: 0,
      importance: 2,
      kind: `${r.kind}_summary`,
      place: r.place,
      text: `${r.place}の${r.kind}×${r.count}（${Math.floor(r.firstHour / 24)}〜${Math.floor(r.lastHour / 24)}日）`,
    });
  }

  kept.sort((a, b) => a.seq - b.seq);
  if (kept.length > HARD_CAP) kept.splice(0, kept.length - HARD_CAP);
  c.entries = kept;
}

/** Most recent entries, newest first — what the player sees on return. */
export function recentChronicle(w, n = 12, minImportance = 1) {
  const c = w.macro.chronicle;
  const out = [];
  for (let i = c.entries.length - 1; i >= 0 && out.length < n; i--) {
    if (c.entries[i].importance >= minImportance) out.push(c.entries[i]);
  }
  return out;
}
