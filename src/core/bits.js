// bits.js — bitset helpers.
// L0.
//
// Fog of war is the motivating case. The 2D build stored it as a base64 WebP
// data URL embedded in a JSON string in localStorage — a canvas encode plus
// base64 inflation plus a synchronous main-thread write, every 60 seconds.
// A 768x768 tile grid is 73,728 bytes as a bitmask, which structured-clones
// into IndexedDB for free.

export const bitsetBytes = (count) => (count + 7) >> 3;
export const createBitset = (count) => new Uint8Array(bitsetBytes(count));

export const getBit = (bs, i) => (bs[i >> 3] >> (i & 7)) & 1;
export const setBit = (bs, i) => { bs[i >> 3] |= 1 << (i & 7); };
export const clearBit = (bs, i) => { bs[i >> 3] &= ~(1 << (i & 7)); };

/** Returns 1 if the bit flipped from 0 to 1, else 0 — lets callers count new reveals. */
export function setBitIfNew(bs, i) {
  const byte = i >> 3, mask = 1 << (i & 7);
  if (bs[byte] & mask) return 0;
  bs[byte] |= mask;
  return 1;
}

export function popcount(bs) {
  let n = 0;
  for (let i = 0; i < bs.length; i++) {
    let v = bs[i];
    v = v - ((v >> 1) & 0x55);
    v = (v & 0x33) + ((v >> 2) & 0x33);
    n += (v + (v >> 4)) & 0x0f;
  }
  return n;
}

/**
 * Knowledge is a fixed-size bitmask over a catalogue of facts, so an NPC's
 * knowledge costs O(1) forever and rumour spread is a bitwise OR along the
 * settlement graph — cheap, and naturally idempotent (you cannot learn the
 * same fact twice, so propagation converges instead of oscillating).
 */
export const KNOWLEDGE_WORDS = 4;             // 128 facts
export const createKnowledge = () => new Uint32Array(KNOWLEDGE_WORDS);
export const knows = (k, fact) => (k[fact >> 5] >>> (fact & 31)) & 1;
export const learn = (k, fact) => { k[fact >> 5] |= 1 << (fact & 31); };

/** OR `from` into `to`; returns how many facts were newly learned. */
export function shareKnowledge(to, from) {
  let gained = 0;
  for (let w = 0; w < KNOWLEDGE_WORDS; w++) {
    const fresh = from[w] & ~to[w];
    if (fresh) {
      to[w] |= fresh;
      let v = fresh;
      while (v) { v &= v - 1; gained++; }
    }
  }
  return gained;
}
