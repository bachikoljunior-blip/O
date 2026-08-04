// spatial.js — uniform hash grid for broad-phase queries.
// L0.
//
// Rebuilt from scratch every tick. At a few hundred entities that is cheaper
// than maintaining incremental buckets, and — more importantly — it makes
// query results depend only on entity index order, never on insertion history.
// Determinism first; the old O(n^2) separation pass is what this replaces.

export function createGrid(cellSize = 8, maxEntities = 512, buckets = 1024) {
  return {
    cellSize,
    inv: 1 / cellSize,
    buckets,
    // Bucketed singly-linked lists stored in flat arrays: head[b] -> next[e] -> ...
    head: new Int32Array(buckets).fill(-1),
    next: new Int32Array(maxEntities).fill(-1),
    cellOf: new Int32Array(maxEntities),
  };
}

const hashCell = (cx, cz, buckets) => {
  const h = (Math.imul(cx | 0, 92837111) ^ Math.imul(cz | 0, 689287499)) >>> 0;
  return h % buckets;
};

export function gridClear(g) {
  g.head.fill(-1);
}

export function gridInsert(g, e, x, z) {
  const cx = Math.floor(x * g.inv);
  const cz = Math.floor(z * g.inv);
  const b = hashCell(cx, cz, g.buckets);
  g.next[e] = g.head[b];
  g.head[b] = e;
  g.cellOf[e] = b;
}

/**
 * Collect entities within `radius` of (x,z) into `out` (an Int32Array scratch).
 * Returns the count. Results are NOT distance-sorted — callers that care sort
 * with a total-order comparator that tie-breaks on index.
 */
export function gridQuery(g, x, z, radius, out, store) {
  const r2 = radius * radius;
  const minX = Math.floor((x - radius) * g.inv);
  const maxX = Math.floor((x + radius) * g.inv);
  const minZ = Math.floor((z - radius) * g.inv);
  const maxZ = Math.floor((z + radius) * g.inv);
  let n = 0;
  for (let cz = minZ; cz <= maxZ; cz++) {
    for (let cx = minX; cx <= maxX; cx++) {
      const b = hashCell(cx, cz, g.buckets);
      for (let e = g.head[b]; e !== -1; e = g.next[e]) {
        const dx = store.px[e] - x;
        const dz = store.pz[e] - z;
        if (dx * dx + dz * dz <= r2) {
          if (n >= out.length) return n;
          out[n++] = e;
        }
      }
    }
  }
  return n;
}
