// seed.js — world-slot keys and deterministic, testable seed selection.

export const SAVE_KEY = 'aetheria_save_v1';
export const SAVE_BACKUP_KEY = 'aetheria_save_backup_v1';
export const LAST_SEED_KEY = 'aetheria_last_seed_v1';

function stored(storage, key) {
  try { return storage?.getItem(key) ?? null; }
  catch (error) { return null; }
}

export function pickWorldSeed(search = '', storage = globalThis.localStorage, random = Math.random) {
  const params = new URLSearchParams(search);
  const requested = params.get('seed');
  if (requested != null && requested !== '') {
    const decimal = /^\d+$/.test(requested) ? Number(requested) : NaN;
    const parsed = Number.isFinite(decimal) ? decimal : parseInt(requested, 36);
    return (Number.isFinite(parsed) ? parsed : 12345) >>> 0;
  }

  // Missing Web Storage values are null. Number(null) is 0, so checking the
  // raw value first is essential: otherwise every first-time player receives
  // the same seed-0 world instead of a freshly generated continent.
  const rawLast = stored(storage, LAST_SEED_KEY);
  if (rawLast !== null) {
    const last = Number(rawLast);
    if (Number.isInteger(last) && last >= 0 && last <= 0xffffffff) return last >>> 0;
  }

  for (const key of [SAVE_KEY, SAVE_BACKUP_KEY]) {
    try {
      const raw = stored(storage, key);
      if (!raw) continue;
      const data = JSON.parse(raw);
      if (data && Number.isInteger(data.seed) && data.seed >= 0 && data.seed <= 0xffffffff && data.player) {
        return data.seed >>> 0;
      }
    } catch (error) {}
  }

  return Math.floor(random() * 0x100000000) >>> 0;
}
