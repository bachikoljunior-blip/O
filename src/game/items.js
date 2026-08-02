// items.js — item database, procedural equipment, loot tables and crafting.

import { makeRNG, clamp, lerp } from '../core/util.js';

export const RARITY = [
  { key: 'common', name: '並', color: '#d7d2c8', mult: 1.0, affixes: 0 },
  { key: 'uncommon', name: '上質', color: '#7fd77f', mult: 1.18, affixes: 1 },
  { key: 'rare', name: '希少', color: '#6fb6ff', mult: 1.42, affixes: 2 },
  { key: 'epic', name: '英雄', color: '#c58cff', mult: 1.75, affixes: 3 },
  { key: 'legend', name: '伝説', color: '#ffab40', mult: 2.2, affixes: 4 },
];

export const WEAPON_BASES = [
  { kind: 'dagger', names: ['小刀', '短剣', '刺剣', '影牙'], atk: 6, spd: 1.35, reach: 26, crit: 0.12, twoH: false, len: 12 },
  { kind: 'sword', names: ['銅の剣', '鉄の剣', '鋼の剣', '銀月の剣', '竜牙剣'], atk: 10, spd: 1.0, reach: 34, crit: 0.06, len: 18 },
  { kind: 'axe', names: ['手斧', '戦斧', '重斧', '断罪の斧'], atk: 14, spd: 0.76, reach: 32, crit: 0.05, len: 17 },
  { kind: 'spear', names: ['木槍', '鉄槍', '長槍', '天穿の槍'], atk: 11, spd: 0.9, reach: 46, crit: 0.07, len: 24 },
  { kind: 'mace', names: ['棍棒', '鉄槌', '聖印槌', '破邪の鎚'], atk: 13, spd: 0.8, reach: 30, crit: 0.04, len: 16 },
  { kind: 'staff', names: ['木の杖', '魔道杖', '賢者の杖', '星詠みの杖'], atk: 7, spd: 0.95, reach: 30, crit: 0.05, mag: 8, len: 22 },
  { kind: 'bow', names: ['狩弓', '長弓', '複合弓', '疾風の弓'], atk: 9, spd: 1.0, reach: 300, crit: 0.10, ranged: true, len: 16 },
];

export const ARMOR_BASES = [
  { slot: 'body', names: ['布の服', '革鎧', '鎖帷子', '鉄鎧', '鋼鎧', '竜鱗鎧'], def: 4, hp: 6 },
  { slot: 'head', names: ['布帽子', '革兜', '鉄兜', '騎士兜', '竜牙兜'], def: 2, hp: 3 },
  { slot: 'off', names: ['木の盾', '丸盾', '鉄盾', '塔盾', '竜鱗盾'], def: 3, hp: 4, shield: 'round' },
];

export const TRINKETS = [
  { name: '力の指輪', stats: { atk: 4 } },
  { name: '守りの護符', stats: { def: 4 } },
  { name: '生命の首飾り', stats: { hp: 24 } },
  { name: '賢者の耳飾り', stats: { mp: 20, mag: 4 } },
  { name: '疾風の腕輪', stats: { spd: 14 } },
  { name: '猫目の指輪', stats: { crit: 0.08 } },
  { name: '不屈の紋章', stats: { hp: 14, def: 2, sta: 15 } },
];

const PREFIX = [
  { n: '炎の', s: { atk: 3, fire: 4 } }, { n: '氷の', s: { atk: 2, ice: 4 } },
  { n: '雷の', s: { atk: 3, shock: 4 } }, { n: '鋭い', s: { crit: 0.05, atk: 2 } },
  { n: '重い', s: { atk: 5, spd: -6 } }, { n: '軽やかな', s: { spd: 10 } },
  { n: '頑健な', s: { def: 3, hp: 12 } }, { n: '神秘の', s: { mag: 5, mp: 12 } },
  { n: '吸血の', s: { leech: 0.06 } }, { n: '守護の', s: { def: 4 } },
  { n: '英雄の', s: { atk: 4, def: 2, hp: 10 } }, { n: '星霜の', s: { mag: 4, crit: 0.04 } },
];
const SUFFIX = [
  { n: '・獅子', s: { atk: 4 } }, { n: '・大地', s: { def: 4, hp: 14 } },
  { n: '・疾風', s: { spd: 12 } }, { n: '・深淵', s: { mag: 6 } },
  { n: '・黎明', s: { hp: 18, mp: 10 } }, { n: '・血盟', s: { leech: 0.05, atk: 2 } },
  { n: '・不滅', s: { def: 3, hp: 20 } }, { n: '・流星', s: { crit: 0.06, atk: 3 } },
];

let uid = 1;
export function nextUid() { return uid++; }
export function setUid(v) { uid = Math.max(uid, v | 0); }

function addStats(dst, src, mult = 1) {
  for (const k in src) dst[k] = (dst[k] || 0) + src[k] * (k === 'crit' || k === 'leech' ? 1 : mult);
  return dst;
}

/** Roll a piece of equipment for a given level and rarity index. */
export function rollEquip(rng, level, rarityIdx = null, forceType = null) {
  const r = rarityIdx ?? pickRarity(rng, level);
  const rar = RARITY[r];
  const type = forceType || rng.pick(['weapon', 'weapon', 'body', 'head', 'off', 'trinket']);
  const tier = clamp(Math.floor(level / 5), 0, 5);
  const lvlMult = 1 + level * 0.16;

  if (type === 'trinket') {
    const t = rng.pick(TRINKETS);
    const stats = addStats({}, t.stats, lvlMult * rar.mult * 0.8);
    if (rar.affixes) addStats(stats, rng.pick(PREFIX).s, lvlMult * 0.5);
    return finish({
      id: 'tr' + nextUid(), name: (rar.affixes ? rng.pick(PREFIX).n : '') + t.name,
      type: 'trinket', slot: 'trinket', icon: 'ring', rarity: r, level, stats,
    });
  }
  if (type === 'weapon') {
    const base = rng.pick(WEAPON_BASES);
    const name = base.names[clamp(tier, 0, base.names.length - 1)];
    const stats = {
      atk: Math.round(base.atk * lvlMult * rar.mult),
      spd: 0, crit: base.crit,
    };
    if (base.mag) stats.mag = Math.round(base.mag * lvlMult * rar.mult * 0.7);
    let full = name;
    for (let i = 0; i < rar.affixes; i++) {
      const a = i % 2 === 0 ? rng.pick(PREFIX) : rng.pick(SUFFIX);
      addStats(stats, a.s, lvlMult * 0.45);
      full = i % 2 === 0 ? a.n + full : full + a.n;
    }
    return finish({
      id: 'wp' + nextUid(), name: full, type: 'weapon', slot: 'weapon', icon: base.kind,
      weaponKind: base.kind, atkSpeed: base.spd, reach: base.reach, ranged: !!base.ranged,
      weaponLen: base.len, rarity: r, level, stats,
    });
  }
  const base = ARMOR_BASES.find((b) => b.slot === type);
  const name = base.names[clamp(tier, 0, base.names.length - 1)];
  const stats = {
    def: Math.round(base.def * lvlMult * rar.mult),
    hp: Math.round(base.hp * lvlMult * rar.mult),
  };
  let full = name;
  for (let i = 0; i < rar.affixes; i++) {
    const a = i % 2 === 0 ? rng.pick(PREFIX) : rng.pick(SUFFIX);
    addStats(stats, a.s, lvlMult * 0.4);
    full = i % 2 === 0 ? a.n + full : full + a.n;
  }
  return finish({
    id: 'ar' + nextUid(), name: full, type: 'armor', slot: base.slot,
    icon: base.slot === 'off' ? 'shield' : base.slot === 'head' ? 'helm' : 'body',
    shieldKind: base.shield, rarity: r, level, stats,
  });
}

function finish(it) {
  for (const k in it.stats) it.stats[k] = k === 'crit' || k === 'leech' ? +it.stats[k].toFixed(3) : Math.round(it.stats[k]);
  it.value = Math.max(4, Math.round((it.level * 6 + 12) * RARITY[it.rarity].mult * 1.4));
  it.stack = 1;
  return it;
}

function pickRarity(rng, level) {
  const r = rng();
  const lucky = clamp(level / 60, 0, 0.35);
  if (r < 0.02 + lucky * 0.12) return 4;
  if (r < 0.08 + lucky * 0.22) return 3;
  if (r < 0.24 + lucky * 0.3) return 2;
  if (r < 0.55) return 1;
  return 0;
}

// ————————————————————————————————————————————————— fixed items

export const ITEMS = {};
function def(o) { ITEMS[o.id] = o; return o; }

def({ id: 'potion_s', name: '傷薬', type: 'consumable', icon: 'potion', color: '#e0524a', value: 18, use: { hp: 45 }, desc: 'HPを45回復する。', stackable: true });
def({ id: 'potion_m', name: '上薬', type: 'consumable', icon: 'potion', color: '#e0524a', value: 52, use: { hp: 130 }, desc: 'HPを130回復する。', stackable: true });
def({ id: 'potion_l', name: '秘薬', type: 'consumable', icon: 'potion', color: '#ff7a6a', value: 140, use: { hp: 380 }, desc: 'HPを380回復する。', stackable: true });
def({ id: 'ether_s', name: '魔力の雫', type: 'consumable', icon: 'potion', color: '#5aa8ff', value: 28, use: { mp: 40 }, desc: 'MPを40回復する。', stackable: true });
def({ id: 'ether_m', name: '魔力の泉', type: 'consumable', icon: 'potion', color: '#5aa8ff', value: 76, use: { mp: 110 }, desc: 'MPを110回復する。', stackable: true });
def({ id: 'elixir', name: 'エリクサー', type: 'consumable', icon: 'potion', color: '#ffd24a', value: 420, use: { hp: 9999, mp: 9999, cure: true }, desc: 'HPとMPを全回復し状態異常を治す。', stackable: true });
def({ id: 'antidote', name: '解毒草', type: 'consumable', icon: 'herb', color: '#7fd77f', value: 20, use: { cure: true }, desc: '毒を治す。', stackable: true });
def({ id: 'bread', name: '黒パン', type: 'consumable', icon: 'food', color: '#c89a5a', value: 8, use: { hp: 18, sta: 40 }, desc: '少し回復し、スタミナを満たす。', stackable: true });
def({ id: 'meat', name: '焼き肉', type: 'consumable', icon: 'food', color: '#b4644a', value: 24, use: { hp: 60, sta: 60 }, desc: 'HPとスタミナを回復する。', stackable: true });
def({ id: 'stew', name: '猟師の鍋', type: 'consumable', icon: 'food', color: '#d0a050', value: 60, use: { hp: 120, sta: 100, buffAtk: 8, buffTime: 180 }, desc: '3分間、攻撃力+8。', stackable: true });
def({ id: 'tonic', name: '剛力の霊薬', type: 'consumable', icon: 'potion', color: '#ff9a3a', value: 90, use: { buffAtk: 14, buffTime: 150 }, desc: '150秒間、攻撃力+14。', stackable: true });
def({ id: 'ward', name: '守りの霊薬', type: 'consumable', icon: 'potion', color: '#8ad0ff', value: 90, use: { buffDef: 12, buffTime: 150 }, desc: '150秒間、防御力+12。', stackable: true });
def({ id: 'wayfare', name: '帰還の巻物', type: 'consumable', icon: 'scroll', color: '#e8dcc0', value: 120, use: { warp: true }, desc: '最後に祈った祠へ転移する。', stackable: true });

const mat = (id, name, color, value, desc) =>
  def({ id, name, type: 'material', icon: 'mat', color, value, desc, stackable: true });
mat('herb', '薬草', '#7fd77f', 6, '調合の基本となる草。');
mat('bloom', '月光花', '#c58cff', 24, '夜にだけ咲く花。');
mat('ore_iron', '鉄鉱石', '#9aa4ae', 14, '武具の材料。');
mat('ore_silver', '銀鉱石', '#dfe6ee', 40, '上質な武具の材料。');
mat('ore_myth', '霊銀鉱', '#8ff0ff', 120, '伝説の金属。');
mat('wood', '堅木', '#a3763f', 8, '丈夫な木材。');
mat('hide', '獣の皮', '#b08a5a', 12, 'なめせば革になる。');
mat('fang', '鋭い牙', '#efe9dd', 18, '獣の牙。');
mat('bone', '古びた骨', '#ddd7c6', 10, '不死者の残骸。');
mat('jelly', 'ぷるぷるゼリー', '#5cc46a', 7, 'スライムの体液。');
mat('silk', '妖蜘蛛の糸', '#e4e0d4', 22, '軽くて丈夫。');
mat('ectoplasm', '霊子', '#bfe4ff', 34, '霊体の残滓。');
mat('core', '魔石', '#c58cff', 70, '魔力の結晶。');
mat('scale', '竜の鱗', '#e07a4a', 180, '灼熱に耐える。');
mat('emberheart', '燃える心臓', '#ff7a2e', 400, '竜の魂の欠片。');

def({ id: 'key_old', name: '錆びた鍵', type: 'quest', icon: 'key', color: '#c8a860', value: 0, desc: '古い扉を開けられそうだ。', stackable: true });
def({ id: 'relic', name: '曙光の欠片', type: 'quest', icon: 'gem', color: '#ffe08a', value: 0, desc: '封印の要となる聖遺物。', stackable: true });

// ————————————————————————————————————————————————— crafting

export const RECIPES = [
  { out: 'potion_s', n: 1, in: { herb: 2 }, station: 'any', name: '傷薬' },
  { out: 'potion_m', n: 1, in: { herb: 3, jelly: 1 }, station: 'any', name: '上薬' },
  { out: 'potion_l', n: 1, in: { herb: 4, bloom: 2, core: 1 }, station: 'alchemist', name: '秘薬' },
  { out: 'ether_s', n: 1, in: { bloom: 1, jelly: 1 }, station: 'any', name: '魔力の雫' },
  { out: 'ether_m', n: 1, in: { bloom: 2, core: 1 }, station: 'alchemist', name: '魔力の泉' },
  { out: 'antidote', n: 1, in: { herb: 1, silk: 1 }, station: 'any', name: '解毒草' },
  { out: 'meat', n: 1, in: { hide: 1 }, station: 'fire', name: '焼き肉' },
  { out: 'stew', n: 1, in: { hide: 2, herb: 1 }, station: 'fire', name: '猟師の鍋' },
  { out: 'tonic', n: 1, in: { fang: 2, herb: 2, core: 1 }, station: 'alchemist', name: '剛力の霊薬' },
  { out: 'ward', n: 1, in: { bone: 2, ore_iron: 1, herb: 1 }, station: 'alchemist', name: '守りの霊薬' },
  { out: 'elixir', n: 1, in: { bloom: 3, core: 2, ore_myth: 1 }, station: 'alchemist', name: 'エリクサー' },
  { out: 'wayfare', n: 1, in: { ectoplasm: 2, core: 1 }, station: 'alchemist', name: '帰還の巻物' },
];

/** Smith upgrade cost for +N */
export function upgradeCost(item) {
  const n = item.plus || 0;
  return {
    gold: Math.round((40 + item.level * 12) * Math.pow(1.55, n)),
    mats: n < 3 ? { ore_iron: 2 + n } : n < 6 ? { ore_silver: 2 + (n - 3) } : { ore_myth: 1 + (n - 6) },
  };
}

export function itemPower(item) {
  if (!item || !item.stats) return 0;
  const s = item.stats;
  return (s.atk || 0) * 2 + (s.def || 0) * 2 + (s.hp || 0) * 0.3 + (s.mag || 0) * 1.6 + (s.mp || 0) * 0.2 + (s.crit || 0) * 60 + (s.spd || 0) * 0.4;
}

export function displayName(item) {
  if (!item) return '';
  const p = item.plus ? ` +${item.plus}` : '';
  return item.name + p;
}

export function rarityColor(item) {
  if (item.rarity == null) return '#d7d2c8';
  return RARITY[item.rarity].color;
}

/** Effective stats including upgrade level. */
export function effStats(item) {
  if (!item || !item.stats) return {};
  const p = item.plus || 0;
  if (!p) return item.stats;
  const out = {};
  for (const k in item.stats) {
    out[k] = k === 'crit' || k === 'leech' ? item.stats[k] * (1 + p * 0.05) : Math.round(item.stats[k] * (1 + p * 0.12));
  }
  return out;
}

// ————————————————————————————————————————————————— loot tables

export const LOOT = {
  slime: [{ id: 'jelly', c: 0.8, n: [1, 2] }, { id: 'herb', c: 0.25, n: [1, 1] }],
  bat: [{ id: 'hide', c: 0.4 }, { id: 'fang', c: 0.3 }],
  wolf: [{ id: 'hide', c: 0.7 }, { id: 'fang', c: 0.5 }],
  boar: [{ id: 'hide', c: 0.9, n: [1, 2] }, { id: 'fang', c: 0.3 }],
  spider: [{ id: 'silk', c: 0.7, n: [1, 2] }, { id: 'fang', c: 0.2 }],
  bandit: [{ id: 'ore_iron', c: 0.35 }, { id: 'potion_s', c: 0.3 }, { id: 'bread', c: 0.3 }],
  skeleton: [{ id: 'bone', c: 0.8, n: [1, 2] }, { id: 'ore_iron', c: 0.2 }],
  ghost: [{ id: 'ectoplasm', c: 0.6 }],
  wraith: [{ id: 'ectoplasm', c: 0.8, n: [1, 2] }, { id: 'core', c: 0.25 }],
  golem: [{ id: 'ore_iron', c: 0.7, n: [1, 3] }, { id: 'ore_silver', c: 0.35 }, { id: 'core', c: 0.2 }],
  imp: [{ id: 'core', c: 0.4 }, { id: 'ectoplasm', c: 0.3 }],
  drake: [{ id: 'scale', c: 0.6 }, { id: 'fang', c: 0.5 }, { id: 'core', c: 0.3 }],
  troll: [{ id: 'hide', c: 0.9, n: [2, 3] }, { id: 'core', c: 0.5 }, { id: 'ore_silver', c: 0.4 }],
  dragon: [{ id: 'scale', c: 1, n: [3, 6] }, { id: 'emberheart', c: 1 }, { id: 'ore_myth', c: 1, n: [1, 3] }],
};

export function rollLoot(rng, kind, level, luck = 0) {
  const out = [];
  const table = LOOT[kind] || [];
  for (const e of table) {
    if (rng() < e.c + luck * 0.05) {
      const n = e.n ? rng.irange(e.n[0], e.n[1]) : 1;
      out.push({ id: e.id, n });
    }
  }
  // equipment drop
  const eqChance = kind === 'dragon' ? 1 : 0.14 + luck * 0.02;
  if (rng() < eqChance) out.push({ equip: rollEquip(rng, Math.max(1, level), kind === 'dragon' ? 4 : null) });
  return out;
}

/** Generate a merchant's stock. */
export function shopStock(rng, kind, level) {
  const items = [];
  if (kind === 'shop') {
    items.push({ id: 'potion_s', n: 99 }, { id: 'ether_s', n: 99 }, { id: 'antidote', n: 99 }, { id: 'bread', n: 99 });
    if (level > 4) items.push({ id: 'potion_m', n: 99 }, { id: 'ether_m', n: 99 });
    if (level > 10) items.push({ id: 'potion_l', n: 99 }, { id: 'wayfare', n: 99 });
    for (let i = 0; i < 3; i++) items.push({ equip: rollEquip(rng, level + rng.int(3), rng.int(2)) });
  } else if (kind === 'smith') {
    for (let i = 0; i < 6; i++) items.push({ equip: rollEquip(rng, level + rng.int(4), 1 + rng.int(2), rng.pick(['weapon', 'body', 'head', 'off'])) });
    items.push({ id: 'ore_iron', n: 99 }, { id: 'ore_silver', n: 99 });
  } else if (kind === 'alchemist') {
    items.push({ id: 'potion_m', n: 99 }, { id: 'ether_m', n: 99 }, { id: 'tonic', n: 99 }, { id: 'ward', n: 99 }, { id: 'herb', n: 99 }, { id: 'bloom', n: 99 });
    if (level > 8) items.push({ id: 'elixir', n: 99 });
    for (let i = 0; i < 2; i++) items.push({ equip: rollEquip(rng, level + 2, 2, 'trinket') });
  } else if (kind === 'inn') {
    items.push({ id: 'bread', n: 99 }, { id: 'meat', n: 99 }, { id: 'stew', n: 99 });
  }
  return items;
}
