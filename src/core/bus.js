// bus.js — the simulation's outbound event queue.
// L0.
//
// Deliberately NOT pub/sub with callbacks. Callbacks re-couple the simulation
// to whatever registered, and make ordering implicit. This is a flat
// struct-of-arrays ring the sim appends to and the presentation layer drains
// once per tick. Zero allocation, deterministic order, and the sim stays
// runnable headless with nothing listening at all.

/** Fixed capacity. Overflow DROPS rather than grows, so the worst case is bounded. */
export function createEventQueue(cap = 512) {
  return {
    n: 0,
    cap,
    dropped: 0,
    type: new Uint8Array(cap),
    src: new Int32Array(cap),   // entity handle
    dst: new Int32Array(cap),   // entity handle
    f: new Float32Array(cap * 6),
    i: new Int32Array(cap * 2),
  };
}

export function emit(q, type, src, dst, f0, f1, f2, f3, f4, f5, i0, i1) {
  if (q.n >= q.cap) { q.dropped++; return -1; }
  const k = q.n++;
  const o = k * 6, p = k * 2;
  q.type[k] = type;
  q.src[k] = src | 0;
  q.dst[k] = dst | 0;
  q.f[o] = f0 || 0; q.f[o + 1] = f1 || 0; q.f[o + 2] = f2 || 0;
  q.f[o + 3] = f3 || 0; q.f[o + 4] = f4 || 0; q.f[o + 5] = f5 || 0;
  q.i[p] = i0 | 0; q.i[p + 1] = i1 | 0;
  return k;
}

export function clearEvents(q) {
  q.n = 0;
}

/**
 * A second, low-frequency bus for things that genuinely want string payloads:
 * quest progress, toasts, chronicle lines. A handful of allocations per second
 * is fine here; forcing these into the SoA queue would be false economy.
 */
export function createNoticeQueue() {
  return { items: [] };
}

export function notify(nq, kind, payload) {
  nq.items.push({ kind, payload });
}

export function drainNotices(nq) {
  const out = nq.items;
  nq.items = [];
  return out;
}
