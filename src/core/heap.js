// heap.js — binary min-heap over parallel typed arrays.
// L0.
//
// Replaces the linear-scan open set in the old A* road router, which did an
// O(n) minimum search on every pop. Tolerable inside a loading screen at
// 256x256 nodes; not tolerable once the world gets bigger.

export function createHeap(cap = 4096) {
  return {
    n: 0,
    cap,
    key: new Float64Array(cap),   // priority (f-score)
    val: new Int32Array(cap),     // payload (node index)
  };
}

function grow(h) {
  const cap = h.cap * 2;
  const key = new Float64Array(cap);
  const val = new Int32Array(cap);
  key.set(h.key); val.set(h.val);
  h.key = key; h.val = val; h.cap = cap;
}

export function heapClear(h) { h.n = 0; }
export const heapEmpty = (h) => h.n === 0;

export function heapPush(h, key, val) {
  if (h.n >= h.cap) grow(h);
  let i = h.n++;
  h.key[i] = key;
  h.val[i] = val;
  // sift up
  while (i > 0) {
    const parent = (i - 1) >> 1;
    if (h.key[parent] <= h.key[i]) break;
    const tk = h.key[parent]; h.key[parent] = h.key[i]; h.key[i] = tk;
    const tv = h.val[parent]; h.val[parent] = h.val[i]; h.val[i] = tv;
    i = parent;
  }
}

/** Pops the minimum and returns its payload, or -1 when empty. */
export function heapPop(h) {
  if (h.n === 0) return -1;
  const top = h.val[0];
  h.n--;
  if (h.n > 0) {
    h.key[0] = h.key[h.n];
    h.val[0] = h.val[h.n];
    // sift down
    let i = 0;
    for (;;) {
      const l = i * 2 + 1, r = l + 1;
      let m = i;
      if (l < h.n && h.key[l] < h.key[m]) m = l;
      if (r < h.n && h.key[r] < h.key[m]) m = r;
      if (m === i) break;
      const tk = h.key[m]; h.key[m] = h.key[i]; h.key[i] = tk;
      const tv = h.val[m]; h.val[m] = h.val[i]; h.val[i] = tv;
      i = m;
    }
  }
  return top;
}
