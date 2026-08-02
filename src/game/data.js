// ゲームデータ定義：ステータス / 武器 / 防具 / 魔法 / アイテム / 敵 / ボス

/* ============================================================ ステータス */
export const STAT_KEYS = ['vit', 'end', 'str', 'dex', 'arc', 'fth'];
export const STAT_NAMES = {
  vit: '生命力', end: '持久力', str: '筋力', dex: '技量', arc: '神秘', fth: '信仰',
};
export const STAT_DESC = {
  vit: 'HP が伸びる。被弾に耐える生存力。',
  end: 'スタミナと装備重量の上限が伸びる。',
  str: '重量武器の攻撃力が伸びる。',
  dex: '軽量武器・弓の攻撃力が伸びる。',
  arc: '魔術の威力と FP が伸びる。',
  fth: '奇跡の威力と状態異常耐性が伸びる。',
};

/** レベルアップに必要な残響（通貨） */
export function levelCost(level) {
  return Math.floor(72 + Math.pow(level, 1.72) * 6.2);
}
export function maxHP(vit) { return Math.floor(180 + vit * 15 + Math.pow(vit, 1.5) * 1.0); }
export function maxStamina(end) { return Math.floor(90 + end * 5.2); }
export function maxFP(arc, fth) { return Math.floor(40 + arc * 5.5 + fth * 3.2); }
export function equipLoad(end, str) { return 38 + end * 1.5 + str * 0.7; }

/** 能力補正: S=1.0, A=.85, B=.65, C=.45, D=.28, E=.14 */
export const SCALE = { S: 1.0, A: 0.85, B: 0.65, C: 0.45, D: 0.28, E: 0.14, '-': 0 };

/** 能力補正曲線（軟上限つき） */
export function scalingFactor(stat) {
  if (stat <= 0) return 0;
  if (stat <= 18) return (stat / 18) * 0.42;
  if (stat <= 40) return 0.42 + ((stat - 18) / 22) * 0.44;
  if (stat <= 60) return 0.86 + ((stat - 40) / 20) * 0.10;
  return Math.min(1.05, 0.96 + (stat - 60) * 0.0025);
}

/* ================================================================ 武器 */
/**
 * moveset: 軽攻撃 (l1,l2,l3) / 強攻撃 (h1,h2) / ダッシュ (run) / ローリング (roll)
 * 各モーション: windup 予備動作 / active 判定 / recover 硬直（秒）
 */
const M = (windup, active, recover, dmg, range, arc, poise, stam, motion, opts = {}) =>
  ({ windup, active, recover, dmg, range, arc, poise, stam, motion, ...opts });

export const WEAPONS = {
  broken_sword: {
    id: 'broken_sword', name: '欠けた直剣', model: 'w_sword', cls: '直剣',
    base: 32, scale: { str: 'D', dex: 'D' }, weight: 3.5, req: { str: 8, dex: 8 },
    crit: 1.1, tint: [0.62, 0.62, 0.66], desc: '刃こぼれした剣。それでも武器は武器だ。',
    moveset: {
      l1: M(0.16, 0.10, 0.26, 1.00, 2.4, 1.5, 16, 14, 'slash_r'),
      l2: M(0.15, 0.10, 0.32, 1.05, 2.4, 1.6, 16, 14, 'slash_l'),
      l3: M(0.20, 0.12, 0.42, 1.25, 2.6, 2.0, 22, 18, 'thrust'),
      h1: M(0.42, 0.14, 0.52, 1.75, 2.9, 1.8, 34, 28, 'overhead'),
      h2: M(0.38, 0.16, 0.55, 1.90, 3.0, 2.6, 38, 30, 'spin'),
      run: M(0.18, 0.12, 0.34, 1.30, 2.8, 1.4, 24, 20, 'thrust'),
      roll: M(0.14, 0.10, 0.30, 1.15, 2.5, 1.4, 20, 16, 'slash_r'),
    },
  },
  longsword: {
    id: 'longsword', name: '騎士の長剣', model: 'w_sword', cls: '直剣',
    base: 44, scale: { str: 'C', dex: 'C' }, weight: 5.0, req: { str: 12, dex: 10 },
    crit: 1.1, tint: [0.78, 0.80, 0.86], desc: '均整の取れた王国騎士の標準装備。',
    moveset: {
      l1: M(0.17, 0.11, 0.27, 1.00, 2.6, 1.6, 20, 15, 'slash_r'),
      l2: M(0.16, 0.11, 0.33, 1.05, 2.6, 1.7, 20, 15, 'slash_l'),
      l3: M(0.21, 0.13, 0.44, 1.30, 2.9, 2.1, 26, 19, 'thrust'),
      h1: M(0.44, 0.15, 0.54, 1.85, 3.1, 1.9, 40, 30, 'overhead'),
      h2: M(0.40, 0.18, 0.58, 2.00, 3.2, 2.8, 44, 32, 'spin'),
      run: M(0.19, 0.13, 0.35, 1.35, 3.0, 1.5, 28, 21, 'thrust'),
      roll: M(0.15, 0.11, 0.31, 1.20, 2.7, 1.5, 24, 17, 'slash_r'),
    },
  },
  dagger: {
    id: 'dagger', name: '狩人の短刀', model: 'w_dagger', cls: '短剣',
    base: 30, scale: { dex: 'B' }, weight: 1.5, req: { str: 6, dex: 12 },
    crit: 1.6, tint: [0.85, 0.86, 0.9], desc: '致命の一撃に長ける。速いが間合いは短い。',
    moveset: {
      l1: M(0.11, 0.08, 0.18, 0.85, 1.9, 1.4, 10, 9, 'slash_r'),
      l2: M(0.10, 0.08, 0.20, 0.88, 1.9, 1.4, 10, 9, 'slash_l'),
      l3: M(0.12, 0.09, 0.28, 1.05, 2.0, 1.4, 14, 12, 'thrust'),
      h1: M(0.28, 0.10, 0.36, 1.45, 2.1, 1.4, 20, 20, 'thrust'),
      h2: M(0.26, 0.12, 0.40, 1.55, 2.2, 2.0, 22, 22, 'spin'),
      run: M(0.13, 0.09, 0.24, 1.10, 2.2, 1.3, 16, 14, 'thrust'),
      roll: M(0.10, 0.08, 0.22, 1.00, 1.9, 1.3, 12, 11, 'slash_r'),
    },
  },
  greatsword: {
    id: 'greatsword', name: '灰鉄の大剣', model: 'w_greatsword', cls: '大剣',
    base: 72, scale: { str: 'B' }, weight: 12.5, req: { str: 20, dex: 10 },
    crit: 1.0, tint: [0.7, 0.7, 0.74], desc: '重い一撃で敵の体勢を崩す。',
    moveset: {
      l1: M(0.30, 0.16, 0.44, 1.00, 3.4, 2.2, 48, 26, 'overhead'),
      l2: M(0.32, 0.18, 0.50, 1.06, 3.4, 2.8, 50, 28, 'spin'),
      l3: M(0.36, 0.18, 0.62, 1.30, 3.6, 2.4, 58, 32, 'overhead'),
      h1: M(0.62, 0.20, 0.72, 2.10, 3.8, 2.2, 80, 44, 'overhead'),
      h2: M(0.58, 0.24, 0.78, 2.30, 4.0, 3.4, 86, 48, 'spin'),
      run: M(0.34, 0.18, 0.50, 1.45, 3.7, 2.0, 56, 34, 'overhead'),
      roll: M(0.28, 0.16, 0.46, 1.25, 3.3, 2.0, 44, 28, 'overhead'),
    },
  },
  axe: {
    id: 'axe', name: '樵の戦斧', model: 'w_axe', cls: '斧',
    base: 56, scale: { str: 'B', dex: 'E' }, weight: 8.0, req: { str: 16, dex: 8 },
    crit: 1.0, tint: [0.75, 0.72, 0.68], desc: '振り抜きの重さで防御ごと削る。',
    moveset: {
      l1: M(0.24, 0.13, 0.36, 1.00, 2.9, 2.0, 34, 20, 'slash_r'),
      l2: M(0.24, 0.14, 0.42, 1.06, 2.9, 2.2, 34, 21, 'slash_l'),
      l3: M(0.30, 0.16, 0.52, 1.28, 3.1, 2.4, 44, 26, 'overhead'),
      h1: M(0.52, 0.17, 0.62, 1.95, 3.3, 2.0, 62, 36, 'overhead'),
      h2: M(0.48, 0.20, 0.66, 2.10, 3.4, 3.0, 66, 38, 'spin'),
      run: M(0.26, 0.15, 0.42, 1.38, 3.1, 1.9, 40, 26, 'slash_r'),
      roll: M(0.22, 0.13, 0.38, 1.18, 2.8, 1.9, 32, 22, 'slash_r'),
    },
  },
  spear: {
    id: 'spear', name: '衛兵の長槍', model: 'w_spear', cls: '槍',
    base: 40, scale: { str: 'D', dex: 'B' }, weight: 6.0, req: { str: 12, dex: 14 },
    crit: 1.15, tint: [0.8, 0.8, 0.84], desc: '長い間合いから刺突する。盾を構えたままでも扱える。',
    moveset: {
      l1: M(0.18, 0.10, 0.28, 1.00, 3.6, 0.9, 18, 14, 'thrust'),
      l2: M(0.17, 0.10, 0.32, 1.04, 3.6, 0.9, 18, 14, 'thrust'),
      l3: M(0.22, 0.12, 0.44, 1.28, 3.9, 1.0, 24, 18, 'thrust'),
      h1: M(0.44, 0.14, 0.54, 1.80, 4.4, 0.9, 36, 28, 'thrust'),
      h2: M(0.40, 0.18, 0.58, 1.92, 3.6, 2.6, 40, 30, 'spin'),
      run: M(0.18, 0.12, 0.34, 1.36, 4.2, 0.9, 26, 20, 'thrust'),
      roll: M(0.15, 0.10, 0.30, 1.16, 3.4, 0.9, 20, 16, 'thrust'),
    },
  },
  scythe: {
    id: 'scythe', name: '墓守の大鎌', model: 'w_scythe', cls: '大鎌',
    base: 58, scale: { str: 'C', dex: 'B' }, weight: 9.0, req: { str: 15, dex: 15 },
    crit: 1.2, tint: [0.66, 0.68, 0.72], bleed: 20,
    desc: '刈り取るための刃。横薙ぎが広く、間合いの外から巻き込む。',
    moveset: {
      l1: M(0.22, 0.14, 0.32, 1.00, 3.4, 2.6, 30, 20, 'slash_l'),
      l2: M(0.20, 0.14, 0.36, 1.06, 3.4, 2.8, 30, 20, 'slash_r'),
      l3: M(0.28, 0.18, 0.52, 1.34, 3.7, 3.4, 40, 26, 'spin'),
      h1: M(0.50, 0.18, 0.60, 1.90, 3.9, 3.0, 58, 36, 'spin'),
      h2: M(0.46, 0.20, 0.66, 2.05, 4.1, 3.8, 62, 40, 'spin'),
      run: M(0.24, 0.16, 0.40, 1.40, 3.8, 2.4, 38, 26, 'slash_l'),
      roll: M(0.20, 0.14, 0.36, 1.20, 3.3, 2.4, 30, 22, 'slash_r'),
    },
  },
  katana: {
    id: 'katana', name: '流麗なる打刀', model: 'w_sword', cls: '刺剣',
    base: 48, scale: { dex: 'A' }, weight: 5.5, req: { str: 11, dex: 18 },
    crit: 1.25, tint: [0.9, 0.92, 0.95], bleed: 28,
    desc: '出血を誘う鋭い刃。技量に強く反応する。',
    moveset: {
      l1: M(0.14, 0.10, 0.24, 1.00, 2.7, 1.7, 18, 13, 'slash_r'),
      l2: M(0.13, 0.10, 0.28, 1.05, 2.7, 1.8, 18, 13, 'slash_l'),
      l3: M(0.18, 0.12, 0.40, 1.32, 2.9, 2.2, 24, 17, 'spin'),
      h1: M(0.38, 0.14, 0.48, 1.80, 3.1, 2.0, 36, 26, 'slash_l'),
      h2: M(0.34, 0.16, 0.52, 1.95, 3.2, 2.9, 40, 28, 'spin'),
      run: M(0.16, 0.12, 0.30, 1.34, 3.0, 1.6, 26, 19, 'thrust'),
      roll: M(0.13, 0.10, 0.28, 1.18, 2.6, 1.6, 22, 15, 'slash_r'),
    },
  },
  bow: {
    id: 'bow', name: '狩人の短弓', model: 'w_bow', cls: '弓', ranged: true,
    base: 34, scale: { dex: 'B' }, weight: 3.0, req: { str: 9, dex: 14 },
    crit: 1.0, tint: [0.7, 0.55, 0.35], desc: '遠距離から敵を釣り出せる。矢を消費する。',
    moveset: {
      l1: M(0.40, 0.05, 0.34, 1.00, 60, 0.2, 12, 12, 'shoot', { projectile: 'arrow' }),
      l2: M(0.40, 0.05, 0.34, 1.00, 60, 0.2, 12, 12, 'shoot', { projectile: 'arrow' }),
      l3: M(0.40, 0.05, 0.34, 1.00, 60, 0.2, 12, 12, 'shoot', { projectile: 'arrow' }),
      h1: M(0.85, 0.05, 0.42, 1.85, 90, 0.2, 22, 24, 'shoot', { projectile: 'arrow_heavy' }),
      h2: M(0.85, 0.05, 0.42, 1.85, 90, 0.2, 22, 24, 'shoot', { projectile: 'arrow_heavy' }),
      run: M(0.40, 0.05, 0.34, 1.00, 60, 0.2, 12, 12, 'shoot', { projectile: 'arrow' }),
      roll: M(0.40, 0.05, 0.34, 1.00, 60, 0.2, 12, 12, 'shoot', { projectile: 'arrow' }),
    },
  },
  staff: {
    id: 'staff', name: '穿光の杖', model: 'w_staff', cls: '杖', catalyst: true,
    base: 18, scale: { arc: 'A' }, weight: 3.2, req: { str: 7, dex: 8, arc: 12 },
    crit: 1.0, tint: [0.6, 0.75, 1.0], desc: '魔術を励起させる触媒。物理攻撃には向かない。',
    moveset: {
      l1: M(0.22, 0.10, 0.34, 0.75, 2.2, 1.6, 12, 12, 'slash_r'),
      l2: M(0.22, 0.10, 0.36, 0.78, 2.2, 1.6, 12, 12, 'slash_l'),
      l3: M(0.26, 0.12, 0.44, 0.95, 2.3, 1.8, 16, 15, 'overhead'),
      h1: M(0.46, 0.14, 0.50, 1.30, 2.5, 1.8, 22, 22, 'overhead'),
      h2: M(0.44, 0.14, 0.52, 1.35, 2.5, 2.4, 24, 24, 'spin'),
      run: M(0.24, 0.12, 0.36, 1.00, 2.4, 1.6, 16, 16, 'thrust'),
      roll: M(0.20, 0.10, 0.34, 0.90, 2.2, 1.6, 14, 14, 'slash_r'),
    },
  },
  moonblade: {
    id: 'moonblade', name: '月喰みの刃', model: 'w_sword', cls: '直剣',
    base: 52, scale: { dex: 'C', arc: 'B' }, weight: 6.0, req: { str: 12, dex: 14, arc: 14 },
    crit: 1.15, tint: [0.68, 0.78, 1.0], magic: 24, emissive: 0.5,
    desc: '月光を宿す剣。魔力ダメージを追加で与える。',
    moveset: {
      l1: M(0.16, 0.11, 0.26, 1.00, 2.7, 1.7, 20, 15, 'slash_r'),
      l2: M(0.15, 0.11, 0.30, 1.06, 2.7, 1.8, 20, 15, 'slash_l'),
      l3: M(0.20, 0.13, 0.42, 1.32, 3.0, 2.2, 26, 19, 'spin'),
      h1: M(0.44, 0.15, 0.52, 1.90, 3.2, 2.0, 40, 30, 'overhead'),
      h2: M(0.40, 0.18, 0.56, 2.05, 3.3, 3.0, 44, 32, 'spin'),
      run: M(0.18, 0.13, 0.34, 1.38, 3.1, 1.6, 28, 21, 'thrust'),
      roll: M(0.14, 0.11, 0.30, 1.20, 2.7, 1.6, 24, 17, 'slash_r'),
    },
  },
  kings_blade: {
    id: 'kings_blade', name: '簒奪王の大剣', model: 'w_greatsword', cls: '大剣',
    base: 92, scale: { str: 'A', fth: 'C' }, weight: 16, req: { str: 26, dex: 12 },
    crit: 1.0, tint: [0.85, 0.7, 0.35], emissive: 0.35, holy: 30,
    desc: 'ノクトゥルヌスを討った者に許される王の剣。',
    moveset: {
      l1: M(0.30, 0.17, 0.44, 1.00, 3.6, 2.4, 56, 28, 'overhead'),
      l2: M(0.32, 0.19, 0.50, 1.08, 3.6, 3.0, 58, 30, 'spin'),
      l3: M(0.36, 0.20, 0.62, 1.34, 3.8, 2.6, 66, 34, 'overhead'),
      h1: M(0.60, 0.22, 0.70, 2.20, 4.0, 2.4, 92, 46, 'overhead'),
      h2: M(0.56, 0.26, 0.76, 2.40, 4.2, 3.6, 98, 50, 'spin'),
      run: M(0.34, 0.19, 0.50, 1.50, 3.9, 2.2, 64, 36, 'overhead'),
      roll: M(0.28, 0.17, 0.46, 1.30, 3.5, 2.2, 52, 30, 'overhead'),
    },
  },
};

/* ================================================================ 盾 */
export const SHIELDS = {
  wooden_shield: {
    id: 'wooden_shield', name: '樫の丸盾', model: 'w_shield', weight: 3.0,
    block: 0.62, stability: 34, req: { str: 9 }, tint: [0.55, 0.42, 0.30],
    desc: '軽く扱いやすい。完全には受けきれない。',
  },
  kite_shield: {
    id: 'kite_shield', name: '騎士の凧盾', model: 'w_shield', weight: 6.0,
    block: 0.82, stability: 52, req: { str: 14 }, tint: [0.62, 0.64, 0.7],
    desc: '安定した受け。パリィの構えも取りやすい。',
  },
  tower_shield: {
    id: 'tower_shield', name: '大盾・城壁', model: 'w_shield', weight: 12.0,
    block: 0.95, stability: 78, req: { str: 22 }, tint: [0.5, 0.5, 0.55],
    desc: 'ほぼ全ての物理を受け止めるが、重い。',
  },
  stone_shield: {
    id: 'stone_shield', name: '石翼の盾', model: 'w_shield', weight: 8.5,
    block: 0.88, stability: 66, req: { str: 17 }, tint: [0.44, 0.47, 0.5],
    desc: '番人の翼を削り出した盾。衝撃をよく殺す。',
  },
};

/* ============================================================== 防具 */
export const ARMORS = {
  rags: { id: 'rags', name: '落人のぼろ布', slot: 'body', weight: 1.5, def: 4, tint: [0.42, 0.38, 0.32] },
  leather: { id: 'leather', name: '狩人の革鎧', slot: 'body', weight: 6, def: 14, tint: [0.38, 0.26, 0.18] },
  chain: { id: 'chain', name: '鎖帷子', slot: 'body', weight: 12, def: 24, tint: [0.5, 0.52, 0.56] },
  knight_plate: { id: 'knight_plate', name: '騎士の板金鎧', slot: 'body', weight: 22, def: 40, tint: [0.6, 0.62, 0.68] },
  ash_plate: { id: 'ash_plate', name: '灰鉄の重鎧', slot: 'body', weight: 30, def: 54, tint: [0.42, 0.36, 0.34] },
  hood: { id: 'hood', name: '旅人のフード', slot: 'head', weight: 0.8, def: 3, tint: [0.34, 0.32, 0.3] },
  leather_cap: { id: 'leather_cap', name: '革兜', slot: 'head', weight: 2, def: 7, tint: [0.36, 0.26, 0.18] },
  knight_helm: { id: 'knight_helm', name: '騎士兜', slot: 'head', weight: 6, def: 16, tint: [0.6, 0.62, 0.68] },
  crown: { id: 'crown', name: '簒奪王の冠', slot: 'head', weight: 3, def: 20, tint: [0.85, 0.7, 0.3], emissive: 0.4 },
};

/* ============================================================== 護符 */
export const TALISMANS = {
  ring_vigor: { id: 'ring_vigor', name: '生命の指輪', desc: '最大HPが8%上がる。', effect: { hpMul: 1.08 } },
  ring_wind: { id: 'ring_wind', name: '疾風の指輪', desc: 'スタミナ回復が25%速くなる。', effect: { staminaRegen: 1.25 } },
  ring_steel: { id: 'ring_steel', name: '鋼の指輪', desc: '受けるダメージが8%減る。', effect: { defMul: 0.92 } },
  ring_hunter: { id: 'ring_hunter', name: '狩人の指輪', desc: '致命の一撃の威力が20%上がる。', effect: { critMul: 1.2 } },
  ring_echo: { id: 'ring_echo', name: '残響の指輪', desc: '獲得する残響が15%増える。', effect: { echoMul: 1.15 } },
  ring_moon: { id: 'ring_moon', name: '月影の指輪', desc: '魔術の威力が12%上がる。', effect: { spellMul: 1.12 } },
  ring_delver: { id: 'ring_delver', name: '坑夫の指輪', desc: '最大HPが5%、獲得する残響が10%増える。', effect: { hpMul: 1.05, echoMul: 1.10 } },
};

/* ============================================================== 魔法 */
export const SPELLS = {
  fireball: {
    id: 'fireball', name: '火球', fp: 14, cast: 0.55, recover: 0.42, type: 'arc',
    dmg: 70, scale: 'arc', projectile: 'fire', desc: '前方に火球を放つ。着弾で小爆発。',
  },
  soul_dart: {
    id: 'soul_dart', name: '魂の矢', fp: 8, cast: 0.38, recover: 0.30, type: 'arc',
    dmg: 52, scale: 'arc', projectile: 'soul', desc: '低燃費で素早い魔力の矢。',
  },
  ice_lance: {
    id: 'ice_lance', name: '氷槍', fp: 20, cast: 0.72, recover: 0.5, type: 'arc',
    dmg: 96, scale: 'arc', projectile: 'ice', frost: 26, desc: '貫通する氷の槍。冷気を蓄積させる。',
  },
  heal: {
    id: 'heal', name: '癒しの光', fp: 24, cast: 0.9, recover: 0.5, type: 'fth',
    heal: 130, scale: 'fth', desc: '自身のHPを回復する。',
  },
  bolt: {
    id: 'bolt', name: '雷の投槍', fp: 26, cast: 0.7, recover: 0.55, type: 'fth',
    dmg: 118, scale: 'fth', projectile: 'bolt', desc: '雷の槍を投げる。竜に効く。',
  },
  barrier: {
    id: 'barrier', name: '光の障壁', fp: 18, cast: 0.6, recover: 0.4, type: 'fth',
    buff: { def: 0.6, dur: 14 }, desc: '一定時間、被ダメージを大きく軽減する。',
  },
};

/* ============================================================ アイテム */
export const ITEMS = {
  flask: { id: 'flask', name: '癒しの雫', kind: 'flask', desc: '篝火で補充される。HPを回復。' },
  ash_flask: { id: 'ash_flask', name: '灰の雫', kind: 'flask_fp', desc: '篝火で補充される。FPを回復。' },
  herb: { id: 'herb', name: '薬草', kind: 'consumable', heal: 70, use: 0.9, desc: 'その場で噛み砕く。少量回復。', stack: 20 },
  antidote: { id: 'antidote', name: '毒消し草', kind: 'consumable', cure: 'poison', use: 0.8, desc: '毒を中和する。', stack: 12 },
  throwing_knife: { id: 'throwing_knife', name: '投げナイフ', kind: 'throw', dmg: 45, use: 0.45, desc: '遠くの敵を釣る。', stack: 30 },
  firebomb: { id: 'firebomb', name: '火炎壺', kind: 'throw', dmg: 110, splash: 3.2, fire: true, use: 0.7, desc: '着弾点で爆発する。', stack: 15 },
  arrow: { id: 'arrow', name: '矢', kind: 'ammo', desc: '弓の弾。', stack: 99 },
  bone: { id: 'bone', name: '帰家の骨片', kind: 'tool', use: 1.6, desc: '最後に休息した篝火へ戻る。', stack: 5 },
  ore_iron: { id: 'ore_iron', name: '鉄鉱石', kind: 'material', desc: '武器強化に使う。', stack: 99 },
  ore_silver: { id: 'ore_silver', name: '銀鉱石', kind: 'material', desc: '上位の強化に使う。', stack: 99 },
  shard_ancient: { id: 'shard_ancient', name: '古き欠片', kind: 'material', desc: '伝説級の強化に使う。', stack: 99 },
  crystal: { id: 'crystal', name: '魔力の結晶', kind: 'material', desc: '魔術の触媒。', stack: 99 },
  beast_bone: { id: 'beast_bone', name: '獣の骨', kind: 'material', desc: '獣から得られる素材。', stack: 99 },
  blood_flower: { id: 'blood_flower', name: '血赤花', kind: 'material', desc: '湿原に咲く赤い花。', stack: 99 },
  bone_ash: { id: 'bone_ash', name: '骨灰', kind: 'material', desc: '墓所の主から得られる灰。強化の触媒。', stack: 99 },
  echo_shard: { id: 'echo_shard', name: '残響の欠片', kind: 'valuable', echo: 400, desc: '砕くと残響が得られる。', stack: 20 },
};

/* ============================================================ 強化 */
export const UPGRADE = {
  maxLevel: 10,
  cost(level) {
    return {
      echo: Math.floor(120 * Math.pow(1.55, level)),
      mat: level < 4 ? 'ore_iron' : level < 8 ? 'ore_silver' : 'shard_ancient',
      matCount: level < 4 ? 2 + level : level < 8 ? 2 + (level - 4) : 1 + (level - 8),
    };
  },
  /** 強化段階ごとの攻撃力倍率 */
  mul(level) { return 1 + level * 0.14; },
};

/* ============================================================ 調合 */
export const RECIPES = [
  { id: 'herb', out: 'herb', count: 2, need: { blood_flower: 1 }, echo: 20 },
  { id: 'firebomb', out: 'firebomb', count: 2, need: { crystal: 1, beast_bone: 1 }, echo: 60 },
  { id: 'throwing_knife', out: 'throwing_knife', count: 5, need: { ore_iron: 1 }, echo: 40 },
  { id: 'arrow', out: 'arrow', count: 20, need: { beast_bone: 1 }, echo: 30 },
  { id: 'antidote', out: 'antidote', count: 2, need: { herb: 1, crystal: 1 }, echo: 30 },
  { id: 'ore_silver', out: 'ore_silver', count: 1, need: { bone_ash: 1, ore_iron: 3 }, echo: 220 },
  { id: 'shard_ancient', out: 'shard_ancient', count: 1, need: { bone_ash: 2, ore_silver: 2 }, echo: 900 },
];

/* ============================================================== 敵 */
/**
 * 体型: parts で procedural に組む。h=身長スケール。
 * attacks: 各攻撃の間合い・威力・予備動作。AI が状況で選ぶ。
 */
export const ENEMIES = {
  wolf: {
    id: 'wolf', name: '飢えた狼', tier: 1, hp: 110, poise: 22, def: 2,
    speed: 4.6, runSpeed: 6.4, aggro: 22, echo: 45, body: 'beast',
    tint: [0.42, 0.38, 0.34], h: 0.8, weapon: null, group: 3,
    attacks: [
      { id: 'bite', windup: 0.34, active: 0.12, recover: 0.5, dmg: 34, range: 2.2, arc: 1.2, poise: 14, motion: 'lunge' },
      { id: 'pounce', windup: 0.5, active: 0.2, recover: 0.72, dmg: 52, range: 5.0, arc: 1.0, poise: 22, motion: 'pounce', dash: 5.5 },
    ],
    ai: { aggression: 0.75, circling: 0.5, retreat: 0.25, guard: 0 },
  },
  deer: {
    id: 'deer', name: '林の鹿', tier: 1, hp: 90, poise: 12, def: 0,
    speed: 3.0, runSpeed: 8.4, aggro: 0, echo: 30, body: 'beast',
    tint: [0.52, 0.40, 0.28], h: 0.95, weapon: null, group: 3, passive: true,
    attacks: [
      { id: 'kick', windup: 0.4, active: 0.12, recover: 0.7, dmg: 22, range: 2.0, arc: 1.2, poise: 10, motion: 'lunge' },
    ],
    ai: { aggression: 0.05, circling: 0.2, retreat: 1.0, guard: 0 },
  },
  boar: {
    id: 'boar', name: '荒れ猪', tier: 1, hp: 165, poise: 40, def: 5,
    speed: 3.2, runSpeed: 7.0, aggro: 18, echo: 60, body: 'beast',
    tint: [0.32, 0.26, 0.22], h: 0.9, weapon: null, group: 1,
    attacks: [
      { id: 'charge', windup: 0.7, active: 0.35, recover: 0.9, dmg: 68, range: 9, arc: 0.8, poise: 40, motion: 'charge', dash: 9 },
      { id: 'gore', windup: 0.36, active: 0.14, recover: 0.6, dmg: 42, range: 2.4, arc: 1.4, poise: 20, motion: 'lunge' },
    ],
    ai: { aggression: 0.6, circling: 0.15, retreat: 0.35, guard: 0 },
  },
  bandit: {
    id: 'bandit', name: '野盗', tier: 2, hp: 160, poise: 30, def: 6,
    speed: 3.4, runSpeed: 5.4, aggro: 20, echo: 85, body: 'humanoid',
    tint: [0.42, 0.34, 0.26], h: 1.0, weapon: 'w_sword', shield: false, group: 2,
    attacks: [
      { id: 'slash', windup: 0.42, active: 0.13, recover: 0.55, dmg: 40, range: 2.6, arc: 1.6, poise: 20, motion: 'slash_r' },
      { id: 'combo', windup: 0.38, active: 0.12, recover: 0.30, dmg: 34, range: 2.6, arc: 1.6, poise: 18, motion: 'slash_l', chain: 'slash' },
      { id: 'kick', windup: 0.34, active: 0.12, recover: 0.62, dmg: 22, range: 2.2, arc: 1.2, poise: 42, motion: 'thrust', guardBreak: true },
    ],
    ai: { aggression: 0.6, circling: 0.4, retreat: 0.3, guard: 0.25 },
  },
  archer: {
    id: 'archer', name: '野盗の射手', tier: 2, hp: 120, poise: 22, def: 4,
    speed: 3.2, runSpeed: 5.2, aggro: 34, echo: 90, body: 'humanoid',
    tint: [0.36, 0.36, 0.28], h: 0.98, weapon: 'w_bow', group: 1, ranged: true, preferDist: 12,
    attacks: [
      { id: 'shoot', windup: 0.75, active: 0.05, recover: 0.55, dmg: 44, range: 40, arc: 0.2, poise: 12, motion: 'shoot', projectile: 'arrow' },
    ],
    ai: { aggression: 0.35, circling: 0.6, retreat: 0.8, guard: 0 },
  },
  brute: {
    id: 'brute', name: '大鉈の巨漢', tier: 3, hp: 360, poise: 70, def: 10,
    speed: 2.7, runSpeed: 4.4, aggro: 20, echo: 190, body: 'humanoid',
    tint: [0.36, 0.30, 0.28], h: 1.35, weapon: 'w_axe', group: 1,
    attacks: [
      { id: 'chop', windup: 0.72, active: 0.18, recover: 0.85, dmg: 86, range: 3.4, arc: 1.8, poise: 60, motion: 'overhead' },
      { id: 'sweep', windup: 0.6, active: 0.22, recover: 0.9, dmg: 74, range: 3.6, arc: 3.2, poise: 55, motion: 'spin' },
      { id: 'slam', windup: 0.9, active: 0.2, recover: 1.1, dmg: 110, range: 4.2, arc: 2.4, poise: 80, motion: 'overhead', shock: 4.5 },
    ],
    ai: { aggression: 0.55, circling: 0.15, retreat: 0.1, guard: 0.1 },
  },
  skeleton: {
    id: 'skeleton', name: '朽ちた兵', tier: 2, hp: 135, poise: 20, def: 4,
    speed: 3.0, runSpeed: 5.0, aggro: 18, echo: 80, body: 'humanoid',
    tint: [0.78, 0.76, 0.68], h: 0.98, weapon: 'w_sword', group: 3, revive: true,
    attacks: [
      { id: 'slash', windup: 0.4, active: 0.13, recover: 0.5, dmg: 36, range: 2.5, arc: 1.6, poise: 18, motion: 'slash_r' },
      { id: 'thrust', windup: 0.46, active: 0.12, recover: 0.6, dmg: 44, range: 2.9, arc: 1.0, poise: 22, motion: 'thrust' },
    ],
    ai: { aggression: 0.65, circling: 0.3, retreat: 0.15, guard: 0.1 },
  },
  wraith: {
    id: 'wraith', name: '嘆きの亡霊', tier: 3, hp: 200, poise: 18, def: 8,
    speed: 3.6, runSpeed: 5.6, aggro: 24, echo: 160, body: 'wraith',
    tint: [0.42, 0.52, 0.7], h: 1.1, weapon: null, group: 2, float: true, emissive: 0.35,
    attacks: [
      { id: 'claw', windup: 0.42, active: 0.14, recover: 0.55, dmg: 46, range: 2.6, arc: 1.8, poise: 20, motion: 'slash_l' },
      { id: 'wail', windup: 0.8, active: 0.3, recover: 0.9, dmg: 58, range: 6.0, arc: 3.4, poise: 30, motion: 'cast', frost: 20 },
    ],
    ai: { aggression: 0.5, circling: 0.6, retreat: 0.4, guard: 0 },
  },
  knight: {
    id: 'knight', name: '亡国の騎士', tier: 4, hp: 420, poise: 60, def: 16,
    speed: 3.2, runSpeed: 5.2, aggro: 22, echo: 320, body: 'humanoid',
    tint: [0.5, 0.52, 0.58], h: 1.08, weapon: 'w_sword', shield: true, group: 1,
    attacks: [
      { id: 'slash', windup: 0.44, active: 0.13, recover: 0.5, dmg: 58, range: 2.7, arc: 1.6, poise: 26, motion: 'slash_r', chain: 'slash2' },
      { id: 'slash2', windup: 0.32, active: 0.13, recover: 0.62, dmg: 62, range: 2.7, arc: 1.7, poise: 26, motion: 'slash_l' },
      { id: 'thrust', windup: 0.5, active: 0.12, recover: 0.66, dmg: 70, range: 3.2, arc: 1.0, poise: 30, motion: 'thrust' },
      { id: 'shield_bash', windup: 0.38, active: 0.12, recover: 0.7, dmg: 30, range: 2.2, arc: 1.4, poise: 60, motion: 'thrust', guardBreak: true },
    ],
    ai: { aggression: 0.55, circling: 0.45, retreat: 0.25, guard: 0.55 },
  },
  mage: {
    id: 'mage', name: '灰の術士', tier: 3, hp: 155, poise: 16, def: 6,
    speed: 3.0, runSpeed: 4.8, aggro: 32, echo: 210, body: 'humanoid',
    tint: [0.3, 0.28, 0.42], h: 1.0, weapon: 'w_staff', group: 1, ranged: true, preferDist: 11,
    attacks: [
      { id: 'dart', windup: 0.62, active: 0.05, recover: 0.55, dmg: 52, range: 30, arc: 0.2, poise: 14, motion: 'cast', projectile: 'soul' },
      { id: 'nova', windup: 1.0, active: 0.25, recover: 0.9, dmg: 78, range: 5.5, arc: 3.4, poise: 30, motion: 'cast' },
    ],
    ai: { aggression: 0.3, circling: 0.7, retreat: 0.85, guard: 0 },
  },
  imp: {
    id: 'imp', name: '灰の小鬼', tier: 2, hp: 105, poise: 14, def: 3,
    speed: 4.4, runSpeed: 6.2, aggro: 20, echo: 70, body: 'imp',
    tint: [0.5, 0.24, 0.18], h: 0.65, weapon: 'w_dagger', group: 4,
    attacks: [
      { id: 'stab', windup: 0.28, active: 0.1, recover: 0.4, dmg: 26, range: 1.9, arc: 1.4, poise: 10, motion: 'thrust' },
      { id: 'leap', windup: 0.44, active: 0.16, recover: 0.6, dmg: 38, range: 4.5, arc: 1.2, poise: 16, motion: 'pounce', dash: 5 },
    ],
    ai: { aggression: 0.85, circling: 0.35, retreat: 0.3, guard: 0 },
  },
  crawler: {
    id: 'crawler', name: '這うもの', tier: 2, hp: 95, poise: 10, def: 2,
    speed: 4.8, runSpeed: 7.2, aggro: 16, echo: 65, body: 'beast',
    tint: [0.30, 0.26, 0.30], h: 0.55, weapon: null, group: 4,
    attacks: [
      { id: 'bite', windup: 0.26, active: 0.10, recover: 0.38, dmg: 28, range: 1.9, arc: 1.3, poise: 10, motion: 'lunge' },
      { id: 'leap', windup: 0.40, active: 0.16, recover: 0.56, dmg: 40, range: 5.0, arc: 1.0, poise: 16, motion: 'pounce', dash: 6 },
    ],
    ai: { aggression: 0.9, circling: 0.25, retreat: 0.2, guard: 0 },
  },
  gargoyle: {
    id: 'gargoyle', name: '石像鬼', tier: 4, hp: 330, poise: 90, def: 22,
    speed: 2.9, runSpeed: 5.0, aggro: 15, echo: 280, body: 'humanoid',
    tint: [0.40, 0.41, 0.43], h: 1.25, weapon: null, group: 1,
    attacks: [
      { id: 'claw', windup: 0.52, active: 0.16, recover: 0.62, dmg: 74, range: 3.0, arc: 2.0, poise: 50, motion: 'slash_r', chain: 'claw2' },
      { id: 'claw2', windup: 0.34, active: 0.16, recover: 0.75, dmg: 78, range: 3.0, arc: 2.2, poise: 50, motion: 'slash_l' },
      { id: 'slam', windup: 0.82, active: 0.20, recover: 0.95, dmg: 108, range: 3.8, arc: 2.4, poise: 70, motion: 'overhead', shock: 4.5 },
    ],
    ai: { aggression: 0.6, circling: 0.2, retreat: 0.1, guard: 0 },
  },
  troll: {
    id: 'troll', name: '岩喰いのトロル', tier: 4, hp: 640, poise: 110, def: 14,
    speed: 2.4, runSpeed: 4.6, aggro: 24, echo: 520, body: 'humanoid',
    tint: [0.34, 0.38, 0.32], h: 1.9, weapon: null, group: 1,
    attacks: [
      { id: 'smash', windup: 0.95, active: 0.22, recover: 1.2, dmg: 130, range: 4.6, arc: 2.2, poise: 90, motion: 'overhead', shock: 6 },
      { id: 'sweep', windup: 0.75, active: 0.26, recover: 1.0, dmg: 104, range: 5.0, arc: 3.6, poise: 80, motion: 'spin' },
      { id: 'stomp', windup: 0.7, active: 0.2, recover: 0.9, dmg: 88, range: 3.4, arc: 6.28, poise: 70, motion: 'overhead', shock: 5 },
    ],
    ai: { aggression: 0.5, circling: 0.1, retreat: 0.05, guard: 0 },
  },
};

/* ============================================================== ボス */
export const BOSSES = {
  colossus: {
    id: 'colossus', name: '朽ちた巨兵', title: '草原に眠る番人',
    hp: 1100, poise: 130, def: 10, speed: 2.5, runSpeed: 4.6, echo: 1600,
    body: 'humanoid', tint: [0.42, 0.44, 0.40], h: 2.4, weapon: null, scaleWeapon: 2,
    music: 'boss', reward: { weapon: 'axe', item: 'ore_iron', count: 4 },
    phases: [
      {
        hpAbove: 0.5,
        attacks: [
          { id: 'smash', windup: 1.0, active: 0.22, recover: 1.15, dmg: 120, range: 5.2, arc: 2.0, poise: 100, motion: 'overhead', shock: 6 },
          { id: 'sweep', windup: 0.78, active: 0.26, recover: 1.0, dmg: 104, range: 5.6, arc: 3.4, poise: 90, motion: 'spin' },
          { id: 'stomp', windup: 0.72, active: 0.2, recover: 0.95, dmg: 96, range: 4.0, arc: 6.28, poise: 80, motion: 'overhead', shock: 5.5 },
        ],
      },
      {
        hpAbove: 0,
        enrage: true, speedMul: 1.22, dmgMul: 1.15,
        attacks: [
          { id: 'smash2', windup: 0.85, active: 0.22, recover: 0.95, dmg: 132, range: 5.4, arc: 2.2, poise: 110, motion: 'overhead', shock: 7 },
          { id: 'combo', windup: 0.62, active: 0.2, recover: 0.5, dmg: 92, range: 5.0, arc: 3.0, poise: 80, motion: 'spin', chain: 'smash2' },
          { id: 'quake', windup: 1.3, active: 0.4, recover: 1.3, dmg: 150, range: 9.0, arc: 6.28, poise: 130, motion: 'overhead', shock: 10 },
        ],
      },
    ],
  },
  orgren: {
    id: 'orgren', name: '森の主 オルグレン', title: '常闇に潜む牙',
    hp: 1900, poise: 90, def: 12, speed: 4.4, runSpeed: 7.4, echo: 2600,
    body: 'beast', tint: [0.22, 0.28, 0.20], h: 1.9, weapon: null,
    music: 'boss', reward: { item: 'beast_bone', count: 6, talisman: 'ring_hunter' },
    phases: [
      {
        hpAbove: 0.55,
        attacks: [
          { id: 'bite', windup: 0.4, active: 0.14, recover: 0.55, dmg: 78, range: 3.2, arc: 1.4, poise: 40, motion: 'lunge' },
          { id: 'pounce', windup: 0.55, active: 0.22, recover: 0.8, dmg: 104, range: 9, arc: 1.2, poise: 60, motion: 'pounce', dash: 10 },
          { id: 'claw', windup: 0.44, active: 0.16, recover: 0.6, dmg: 86, range: 3.4, arc: 2.6, poise: 50, motion: 'slash_l', chain: 'claw2' },
          { id: 'claw2', windup: 0.3, active: 0.16, recover: 0.7, dmg: 90, range: 3.4, arc: 2.6, poise: 50, motion: 'slash_r' },
        ],
      },
      {
        hpAbove: 0, enrage: true, speedMul: 1.3, dmgMul: 1.2,
        attacks: [
          { id: 'frenzy', windup: 0.3, active: 0.14, recover: 0.26, dmg: 72, range: 3.4, arc: 2.4, poise: 40, motion: 'slash_r', chain: 'frenzy2' },
          { id: 'frenzy2', windup: 0.26, active: 0.14, recover: 0.28, dmg: 74, range: 3.4, arc: 2.4, poise: 40, motion: 'slash_l', chain: 'pounce2' },
          { id: 'pounce2', windup: 0.42, active: 0.24, recover: 0.7, dmg: 120, range: 12, arc: 1.2, poise: 70, motion: 'pounce', dash: 13 },
          { id: 'howl', windup: 0.9, active: 0.3, recover: 1.0, dmg: 90, range: 8, arc: 6.28, poise: 90, motion: 'cast', shock: 6 },
        ],
      },
    ],
  },
  witch: {
    id: 'witch', name: '湿原の魔女 セラフィナ', title: '霧を編む者',
    hp: 1750, poise: 45, def: 14, speed: 3.6, runSpeed: 6.0, echo: 3000,
    body: 'humanoid', tint: [0.30, 0.36, 0.34], h: 1.15, weapon: 'w_staff', emissive: 0.25,
    music: 'boss', reward: { spell: 'ice_lance', item: 'crystal', count: 5 },
    phases: [
      {
        hpAbove: 0.6,
        attacks: [
          { id: 'dart', windup: 0.55, active: 0.05, recover: 0.5, dmg: 62, range: 30, arc: 0.2, poise: 20, motion: 'cast', projectile: 'soul', volley: 3 },
          { id: 'nova', windup: 0.9, active: 0.25, recover: 0.8, dmg: 88, range: 6.5, arc: 6.28, poise: 40, motion: 'cast' },
          { id: 'blink', windup: 0.3, active: 0.0, recover: 0.35, dmg: 0, range: 0, arc: 0, poise: 0, motion: 'cast', teleport: 9 },
        ],
      },
      {
        hpAbove: 0, enrage: true, speedMul: 1.15, dmgMul: 1.25,
        attacks: [
          { id: 'ice_storm', windup: 0.8, active: 0.08, recover: 0.6, dmg: 74, range: 32, arc: 0.2, poise: 30, motion: 'cast', projectile: 'ice', volley: 5, frost: 22 },
          { id: 'grasp', windup: 1.0, active: 0.3, recover: 0.9, dmg: 116, range: 8, arc: 6.28, poise: 60, motion: 'cast', shock: 5 },
          { id: 'blink', windup: 0.26, active: 0.0, recover: 0.3, dmg: 0, range: 0, arc: 0, poise: 0, motion: 'cast', teleport: 11 },
          { id: 'staff', windup: 0.4, active: 0.14, recover: 0.6, dmg: 80, range: 3.0, arc: 2.2, poise: 40, motion: 'slash_r' },
        ],
      },
    ],
  },
  galvan: {
    id: 'galvan', name: '灰の騎士 ガルヴァン', title: '燃え残りの誓約',
    hp: 2600, poise: 100, def: 20, speed: 3.8, runSpeed: 6.4, echo: 4200,
    body: 'humanoid', tint: [0.44, 0.26, 0.20], h: 1.3, weapon: 'w_greatsword', emissive: 0.3,
    music: 'boss', reward: { weapon: 'greatsword', armor: 'ash_plate' },
    phases: [
      {
        hpAbove: 0.5,
        attacks: [
          { id: 'cleave', windup: 0.62, active: 0.18, recover: 0.7, dmg: 104, range: 4.0, arc: 2.2, poise: 70, motion: 'overhead', chain: 'cleave2' },
          { id: 'cleave2', windup: 0.34, active: 0.18, recover: 0.85, dmg: 110, range: 4.0, arc: 3.0, poise: 70, motion: 'spin' },
          { id: 'dash_thrust', windup: 0.55, active: 0.16, recover: 0.8, dmg: 118, range: 8.0, arc: 1.0, poise: 80, motion: 'thrust', dash: 8 },
        ],
      },
      {
        hpAbove: 0, enrage: true, speedMul: 1.25, dmgMul: 1.2, fire: true,
        attacks: [
          { id: 'flame_cleave', windup: 0.5, active: 0.2, recover: 0.62, dmg: 120, range: 4.6, arc: 2.6, poise: 80, motion: 'overhead', fire: 30, chain: 'flame_cleave2' },
          { id: 'flame_cleave2', windup: 0.3, active: 0.2, recover: 0.7, dmg: 124, range: 4.6, arc: 3.4, poise: 80, motion: 'spin', fire: 30 },
          { id: 'eruption', windup: 1.1, active: 0.35, recover: 1.1, dmg: 168, range: 8.5, arc: 6.28, poise: 120, motion: 'overhead', fire: 50, shock: 8 },
          { id: 'charge', windup: 0.6, active: 0.3, recover: 0.9, dmg: 132, range: 14, arc: 1.4, poise: 100, motion: 'thrust', dash: 15 },
        ],
      },
    ],
  },
  leonhart: {
    id: 'leonhart', name: '黄金の将 レオンハルト', title: '穂波を守る剣',
    hp: 2300, poise: 85, def: 18, speed: 4.0, runSpeed: 6.8, echo: 3600,
    body: 'humanoid', tint: [0.72, 0.60, 0.28], h: 1.12, weapon: 'w_sword', shield: true, emissive: 0.15,
    music: 'boss', reward: { weapon: 'katana', talisman: 'ring_steel' },
    phases: [
      {
        hpAbove: 0.45,
        attacks: [
          { id: 'combo1', windup: 0.34, active: 0.12, recover: 0.26, dmg: 66, range: 3.0, arc: 1.7, poise: 40, motion: 'slash_r', chain: 'combo2' },
          { id: 'combo2', windup: 0.26, active: 0.12, recover: 0.28, dmg: 70, range: 3.0, arc: 1.8, poise: 40, motion: 'slash_l', chain: 'combo3' },
          { id: 'combo3', windup: 0.34, active: 0.14, recover: 0.7, dmg: 84, range: 3.2, arc: 2.4, poise: 50, motion: 'spin' },
          { id: 'bash', windup: 0.4, active: 0.12, recover: 0.66, dmg: 40, range: 2.4, arc: 1.4, poise: 80, motion: 'thrust', guardBreak: true },
        ],
      },
      {
        hpAbove: 0, enrage: true, speedMul: 1.3, dmgMul: 1.18, holy: true,
        attacks: [
          { id: 'sun1', windup: 0.28, active: 0.12, recover: 0.22, dmg: 72, range: 3.2, arc: 1.8, poise: 45, motion: 'slash_r', holy: 24, chain: 'sun2' },
          { id: 'sun2', windup: 0.24, active: 0.12, recover: 0.24, dmg: 76, range: 3.2, arc: 1.8, poise: 45, motion: 'slash_l', holy: 24, chain: 'sun3' },
          { id: 'sun3', windup: 0.44, active: 0.18, recover: 0.8, dmg: 108, range: 4.2, arc: 3.4, poise: 70, motion: 'spin', holy: 40 },
          { id: 'judgement', windup: 1.2, active: 0.3, recover: 1.1, dmg: 156, range: 7.0, arc: 6.28, poise: 110, motion: 'overhead', holy: 60, shock: 7 },
        ],
      },
    ],
  },
  dragon: {
    id: 'dragon', name: '蒼天の竜 ヴァルドリス', title: '嶺を統べる翼',
    hp: 3800, poise: 150, def: 24, speed: 3.4, runSpeed: 6.0, echo: 7000,
    body: 'dragon', tint: [0.36, 0.46, 0.62], h: 2.8, weapon: null,
    music: 'boss', reward: { spell: 'bolt', item: 'shard_ancient', count: 3 },
    phases: [
      {
        hpAbove: 0.55,
        attacks: [
          { id: 'bite', windup: 0.6, active: 0.16, recover: 0.8, dmg: 120, range: 5.2, arc: 1.6, poise: 90, motion: 'lunge' },
          { id: 'tail', windup: 0.7, active: 0.24, recover: 0.9, dmg: 108, range: 6.5, arc: 4.0, poise: 90, motion: 'spin' },
          { id: 'breath', windup: 1.2, active: 0.6, recover: 1.2, dmg: 42, range: 14, arc: 0.9, poise: 60, motion: 'cast', fire: 40, tick: true },
        ],
      },
      {
        hpAbove: 0, enrage: true, speedMul: 1.2, dmgMul: 1.2,
        attacks: [
          { id: 'sky_breath', windup: 1.1, active: 0.8, recover: 1.3, dmg: 52, range: 18, arc: 1.4, poise: 80, motion: 'cast', fire: 55, tick: true },
          { id: 'wing', windup: 0.8, active: 0.28, recover: 1.0, dmg: 130, range: 8.0, arc: 6.28, poise: 120, motion: 'spin', shock: 9 },
          { id: 'dive', windup: 1.0, active: 0.35, recover: 1.2, dmg: 175, range: 18, arc: 2.0, poise: 140, motion: 'pounce', dash: 18 },
        ],
      },
    ],
  },
  nocturnus: {
    id: 'nocturnus', name: '深淵の王 ノクトゥルヌス', title: '世界を喰らう影',
    hp: 5400, poise: 140, def: 28, speed: 4.2, runSpeed: 7.2, echo: 12000,
    body: 'humanoid', tint: [0.14, 0.12, 0.22], h: 1.7, weapon: 'w_greatsword', emissive: 0.45,
    music: 'final', final: true, reward: { weapon: 'kings_blade', armor: 'crown' },
    phases: [
      {
        hpAbove: 0.66,
        attacks: [
          { id: 'rend', windup: 0.44, active: 0.15, recover: 0.5, dmg: 108, range: 4.0, arc: 2.4, poise: 70, motion: 'slash_r', chain: 'rend2' },
          { id: 'rend2', windup: 0.3, active: 0.15, recover: 0.62, dmg: 112, range: 4.0, arc: 2.6, poise: 70, motion: 'slash_l' },
          { id: 'void_bolt', windup: 0.7, active: 0.06, recover: 0.55, dmg: 92, range: 28, arc: 0.2, poise: 40, motion: 'cast', projectile: 'void', volley: 3 },
        ],
      },
      {
        hpAbove: 0.33, speedMul: 1.15, dmgMul: 1.12,
        attacks: [
          { id: 'shadow_step', windup: 0.24, active: 0, recover: 0.28, dmg: 0, range: 0, arc: 0, poise: 0, motion: 'cast', teleport: 12 },
          { id: 'abyss_slash', windup: 0.36, active: 0.16, recover: 0.44, dmg: 116, range: 4.4, arc: 2.8, poise: 80, motion: 'spin', chain: 'abyss_slash2' },
          { id: 'abyss_slash2', windup: 0.28, active: 0.16, recover: 0.6, dmg: 120, range: 4.4, arc: 3.2, poise: 80, motion: 'slash_l' },
          { id: 'void_rain', windup: 1.1, active: 0.5, recover: 1.0, dmg: 78, range: 12, arc: 6.28, poise: 90, motion: 'cast', tick: true },
        ],
      },
      {
        hpAbove: 0, enrage: true, speedMul: 1.35, dmgMul: 1.28,
        attacks: [
          { id: 'end1', windup: 0.26, active: 0.14, recover: 0.2, dmg: 104, range: 4.4, arc: 2.6, poise: 70, motion: 'slash_r', chain: 'end2' },
          { id: 'end2', windup: 0.22, active: 0.14, recover: 0.22, dmg: 108, range: 4.4, arc: 2.6, poise: 70, motion: 'slash_l', chain: 'end3' },
          { id: 'end3', windup: 0.4, active: 0.2, recover: 0.7, dmg: 148, range: 5.2, arc: 3.6, poise: 100, motion: 'spin' },
          { id: 'annihilate', windup: 1.4, active: 0.4, recover: 1.4, dmg: 240, range: 14, arc: 6.28, poise: 160, motion: 'overhead', shock: 12 },
          { id: 'shadow_step', windup: 0.2, active: 0, recover: 0.24, dmg: 0, range: 0, arc: 0, poise: 0, motion: 'cast', teleport: 14 },
        ],
      },
    ],
  },
};

/* ======================================================= ダンジョンの主 */
/**
 * 階層ボス。地上のボスと違い何度でも挑めるので、
 * 報酬は初回のみ効く解放系＋常に入る素材にしてある。
 * hp / 与ダメージは潜った深度で game 側が倍率を掛ける。
 */
export const DUNGEON_BOSSES = {
  catacomb: 'ossuar',
  ruin: 'stonewing',
  mine: 'gnawer',
};

BOSSES.ossuar = {
  id: 'ossuar', name: '骸の主 オスアール', title: '墓所に坐す王', dungeon: true,
  hp: 1450, poise: 95, def: 16, speed: 3.4, runSpeed: 6.2, echo: 2200,
  body: 'humanoid', tint: [0.74, 0.71, 0.60], h: 1.58, weapon: 'w_scythe', emissive: 0.12,
  music: 'boss', reward: { weapon: 'scythe', item: 'bone_ash', count: 3 },
  phases: [
    {
      hpAbove: 0.5,
      attacks: [
        { id: 'reap', windup: 0.5, active: 0.16, recover: 0.6, dmg: 92, range: 4.4, arc: 2.6, poise: 60, motion: 'slash_r', chain: 'reap2' },
        { id: 'reap2', windup: 0.3, active: 0.16, recover: 0.78, dmg: 96, range: 4.4, arc: 3.0, poise: 60, motion: 'spin' },
        { id: 'grave_thrust', windup: 0.56, active: 0.16, recover: 0.74, dmg: 104, range: 7.0, arc: 1.0, poise: 70, motion: 'thrust', dash: 7 },
        { id: 'bone_shard', windup: 0.62, active: 0.06, recover: 0.5, dmg: 58, range: 26, arc: 0.2, poise: 30, motion: 'cast', projectile: 'soul', volley: 3 },
      ],
    },
    {
      hpAbove: 0, enrage: true, speedMul: 1.24, dmgMul: 1.2,
      attacks: [
        { id: 'dirge', windup: 0.34, active: 0.14, recover: 0.26, dmg: 84, range: 4.4, arc: 2.6, poise: 55, motion: 'slash_l', chain: 'dirge2' },
        { id: 'dirge2', windup: 0.26, active: 0.14, recover: 0.3, dmg: 88, range: 4.4, arc: 2.6, poise: 55, motion: 'slash_r', chain: 'dirge3' },
        { id: 'dirge3', windup: 0.42, active: 0.2, recover: 0.82, dmg: 118, range: 5.0, arc: 3.6, poise: 80, motion: 'spin' },
        { id: 'ossuary', windup: 1.15, active: 0.34, recover: 1.05, dmg: 150, range: 9.0, arc: 6.28, poise: 110, motion: 'overhead', shock: 8 },
        { id: 'crypt_step', windup: 0.24, active: 0, recover: 0.3, dmg: 0, range: 0, arc: 0, poise: 0, motion: 'cast', teleport: 9 },
      ],
    },
  ],
};

BOSSES.stonewing = {
  id: 'stonewing', name: '石翼の番人', title: '沈んだ回廊の守り手', dungeon: true,
  hp: 1650, poise: 130, def: 24, speed: 3.0, runSpeed: 6.6, echo: 2600,
  body: 'humanoid', tint: [0.42, 0.45, 0.48], h: 1.6, weapon: null,
  music: 'boss', reward: { shield: 'stone_shield', item: 'ore_iron', count: 4 },
  phases: [
    {
      hpAbove: 0.55,
      attacks: [
        { id: 'rake', windup: 0.5, active: 0.16, recover: 0.6, dmg: 88, range: 3.6, arc: 2.2, poise: 70, motion: 'slash_l', chain: 'rake2' },
        { id: 'rake2', windup: 0.3, active: 0.16, recover: 0.72, dmg: 92, range: 3.6, arc: 2.4, poise: 70, motion: 'slash_r' },
        { id: 'swoop', windup: 0.62, active: 0.24, recover: 0.86, dmg: 110, range: 11, arc: 1.3, poise: 90, motion: 'pounce', dash: 12 },
        { id: 'tail_slam', windup: 0.78, active: 0.22, recover: 0.9, dmg: 100, range: 5.0, arc: 3.6, poise: 80, motion: 'spin', shock: 5 },
      ],
    },
    {
      hpAbove: 0, enrage: true, speedMul: 1.28, dmgMul: 1.22,
      attacks: [
        { id: 'gale', windup: 0.72, active: 0.26, recover: 0.82, dmg: 104, range: 7.0, arc: 6.28, poise: 100, motion: 'spin', shock: 7 },
        { id: 'dive', windup: 0.66, active: 0.28, recover: 0.9, dmg: 132, range: 15, arc: 1.5, poise: 110, motion: 'pounce', dash: 15 },
        { id: 'shred', windup: 0.28, active: 0.14, recover: 0.24, dmg: 78, range: 3.8, arc: 2.4, poise: 60, motion: 'slash_r', chain: 'shred2' },
        { id: 'shred2', windup: 0.24, active: 0.14, recover: 0.62, dmg: 82, range: 3.8, arc: 2.4, poise: 60, motion: 'slash_l' },
      ],
    },
  ],
};

BOSSES.gnawer = {
  id: 'gnawer', name: '坑道喰らい', title: '岩を噛み砕くもの', dungeon: true,
  hp: 1800, poise: 120, def: 14, speed: 4.0, runSpeed: 7.6, echo: 2800,
  body: 'beast', tint: [0.32, 0.24, 0.20], h: 2.0, weapon: null,
  music: 'boss', reward: { talisman: 'ring_delver', item: 'crystal', count: 4 },
  phases: [
    {
      hpAbove: 0.5,
      attacks: [
        { id: 'maw', windup: 0.42, active: 0.15, recover: 0.58, dmg: 86, range: 3.8, arc: 1.6, poise: 50, motion: 'lunge' },
        { id: 'burrow_rush', windup: 0.58, active: 0.24, recover: 0.8, dmg: 112, range: 12, arc: 1.2, poise: 80, motion: 'pounce', dash: 13 },
        { id: 'swipe', windup: 0.46, active: 0.16, recover: 0.6, dmg: 90, range: 3.8, arc: 2.8, poise: 55, motion: 'slash_l', chain: 'swipe2' },
        { id: 'swipe2', windup: 0.3, active: 0.16, recover: 0.72, dmg: 94, range: 3.8, arc: 2.8, poise: 55, motion: 'slash_r' },
      ],
    },
    {
      hpAbove: 0, enrage: true, speedMul: 1.32, dmgMul: 1.24,
      attacks: [
        { id: 'frenzy', windup: 0.26, active: 0.13, recover: 0.24, dmg: 76, range: 3.8, arc: 2.6, poise: 50, motion: 'slash_r', chain: 'frenzy2' },
        { id: 'frenzy2', windup: 0.24, active: 0.13, recover: 0.26, dmg: 80, range: 3.8, arc: 2.6, poise: 50, motion: 'slash_l', chain: 'maw2' },
        { id: 'maw2', windup: 0.34, active: 0.16, recover: 0.66, dmg: 118, range: 4.2, arc: 1.8, poise: 70, motion: 'lunge' },
        { id: 'collapse', windup: 1.2, active: 0.36, recover: 1.1, dmg: 156, range: 9.5, arc: 6.28, poise: 120, motion: 'overhead', shock: 9 },
      ],
    },
  ],
};

/* ==================================================== 地方ごとの湧き表 */
export const SPAWN_TABLE = {
  downs: [['deer', 3], ['wolf', 3], ['boar', 2], ['bandit', 2], ['skeleton', 1]],
  gloomwood: [['wolf', 3], ['deer', 2], ['skeleton', 2], ['bandit', 2], ['wraith', 1], ['troll', 0.4]],
  cinder: [['imp', 4], ['brute', 2], ['mage', 1.5], ['knight', 1]],
  mistfen: [['wraith', 3], ['skeleton', 2], ['boar', 1], ['mage', 1]],
  skyspire: [['wolf', 2], ['knight', 2], ['troll', 1.2], ['archer', 1]],
  goldreach: [['bandit', 3], ['deer', 2.5], ['archer', 2], ['boar', 2], ['knight', 1]],
  riftvale: [['imp', 3], ['wraith', 2], ['brute', 1.5], ['troll', 1]],
  coast: [['bandit', 2], ['deer', 2], ['wolf', 2], ['skeleton', 2], ['archer', 1]],
};

/** 敵の落とすもの */
export const DROPS = {
  wolf: [['beast_bone', 0.55, 1], ['herb', 0.2, 1]],
  deer: [['beast_bone', 0.8, 2], ['herb', 0.3, 1]],
  boar: [['beast_bone', 0.7, 2], ['herb', 0.25, 1]],
  bandit: [['ore_iron', 0.3, 1], ['herb', 0.25, 1], ['throwing_knife', 0.2, 2], ['echo_shard', 0.06, 1]],
  archer: [['arrow', 0.7, 8], ['ore_iron', 0.2, 1]],
  brute: [['ore_iron', 0.6, 2], ['echo_shard', 0.15, 1]],
  skeleton: [['beast_bone', 0.4, 1], ['ore_iron', 0.3, 1]],
  wraith: [['crystal', 0.45, 1], ['echo_shard', 0.12, 1]],
  knight: [['ore_silver', 0.4, 1], ['echo_shard', 0.2, 1]],
  mage: [['crystal', 0.6, 2], ['echo_shard', 0.15, 1]],
  imp: [['ore_iron', 0.25, 1], ['blood_flower', 0.2, 1]],
  troll: [['ore_silver', 0.6, 2], ['shard_ancient', 0.12, 1]],
  crawler: [['beast_bone', 0.5, 1], ['blood_flower', 0.2, 1]],
  gargoyle: [['ore_silver', 0.45, 1], ['shard_ancient', 0.08, 1], ['echo_shard', 0.2, 1]],
};

/** 宝箱の中身（tier ごと） */
export const CHEST_TABLE = [
  null,
  { echo: 260, items: [['herb', 2], ['ore_iron', 2]] },
  { echo: 600, items: [['herb', 3], ['ore_iron', 3], ['firebomb', 2]] },
  { echo: 1200, items: [['ore_silver', 2], ['crystal', 2], ['echo_shard', 1]] },
  { echo: 2400, items: [['ore_silver', 3], ['shard_ancient', 1], ['echo_shard', 2]] },
  { echo: 4200, items: [['shard_ancient', 2], ['echo_shard', 3]] },
];

/** 商人の品揃え */
export const SHOP = [
  { item: 'herb', price: 60 },
  { item: 'antidote', price: 90 },
  { item: 'arrow', price: 12 },
  { item: 'throwing_knife', price: 55 },
  { item: 'firebomb', price: 220 },
  { item: 'bone', price: 300 },
  { item: 'ore_iron', price: 350 },
  { item: 'ore_silver', price: 1400 },
  { weapon: 'longsword', price: 1600 },
  { weapon: 'spear', price: 1800 },
  { weapon: 'bow', price: 1500 },
  { weapon: 'staff', price: 2000 },
  { shield: 'kite_shield', price: 1700 },
  { armor: 'leather', price: 900 },
  { armor: 'chain', price: 2600 },
  { armor: 'leather_cap', price: 500 },
  { talisman: 'ring_vigor', price: 3000 },
  { talisman: 'ring_wind', price: 3000 },
  { spell: 'fireball', price: 2200 },
  { spell: 'heal', price: 2600 },
];
