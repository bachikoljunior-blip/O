// handles.js — generational entity references.
// L0.
//
// A raw slot index is not a safe reference: despawn a wolf, spawn a merchant
// into the freed slot, and every AI that was chasing the wolf is now chasing
// the merchant. That bug is nearly impossible to find after the fact, so every
// STORED reference is a handle carrying the generation it was minted at.

export const INDEX_BITS = 20;                 // 1,048,575 slots, far above MAX_ENTITIES
export const INDEX_MASK = (1 << INDEX_BITS) - 1;
export const NULL_HANDLE = -1;

export const makeHandle = (index, gen) => (index & INDEX_MASK) | (gen << INDEX_BITS);
export const handleIndex = (h) => h & INDEX_MASK;
export const handleGen = (h) => h >>> INDEX_BITS;

/**
 * Resolve a handle to a slot index, or -1 if the entity it referred to is gone.
 * Callers branch on -1; they never see a recycled stranger.
 */
export function deref(store, h) {
  if (h === NULL_HANDLE) return -1;
  const i = h & INDEX_MASK;
  if (i >= store.n) return -1;
  return store.gen[i] === (h >>> INDEX_BITS) ? i : -1;
}

export const isAlive = (store, h) => deref(store, h) !== -1;
