// enemies.js — archetypes and movesets.
// L1.
//
// Every entry is validated by tools/validate-data.js against the telegraph
// budget in tuning.js: at least three DISTINCT windup durations, none shorter
// than 380ms, and at least one delayed variant. An enemy that fails those
// checks fails CI rather than shipping as an unreadable mess.

export const REGION = { ASH: 0, MIST: 1, RUST: 2 };

export const ENEMIES = {
  // ——————————————————————————————— 灰王国
  skeleton: {
    name: '骸の兵',
    region: REGION.ASH,
    tier: 1,
    hp: 42, atk: 8, def: 2,
    poise: 30, stance: 60,
    radius: 0.42, height: 1.75, speed: 3.1,
    aggroRange: 15, keepDistance: 1.6,
    xp: 12, gold: 6,
    undead: true,
    moveset: [
      { atk: 'skeleton_slash',  w: 4, range: [0, 2.2], cooldown: 40 },
      { atk: 'skeleton_thrust', w: 3, range: [1.4, 3.2], cooldown: 70 },
      { atk: 'skeleton_sweep',  w: 2, range: [0, 2.6], cooldown: 110 },
    ],
    delayedChance: 0.22,
  },
  troll: {
    name: '灰の巨兵',
    region: REGION.ASH,
    tier: 6,
    hp: 620, atk: 22, def: 9,
    poise: 140, stance: 320,
    radius: 1.3, height: 4.2, speed: 2.7,
    aggroRange: 26, keepDistance: 2.4,
    xp: 400, gold: 220,
    boss: true,
    moveset: [
      { atk: 'troll_overhead', w: 3, range: [0, 3.4], cooldown: 90 },
      { atk: 'troll_sweep',    w: 4, range: [0, 4.2], cooldown: 60 },
      { atk: 'troll_stomp',    w: 2, range: [0, 2.4], cooldown: 150, minHpFrac: 0, maxHpFrac: 0.6 },
    ],
    delayedChance: 0.30,
  },

  // ——————————————————————————————— 霧ノ國
  musha: {
    name: '落武者',
    region: REGION.MIST,
    tier: 3,
    hp: 130, atk: 15, def: 5,
    poise: 55, stance: 120,
    radius: 0.45, height: 1.8, speed: 3.9,
    aggroRange: 18, keepDistance: 2.0,
    xp: 46, gold: 28,
    // Fights at range then closes: the stance-matching pressure comes from the
    // iai, which is slow enough to read but punishing enough to respect.
    moveset: [
      { atk: 'musha_kesa', w: 4, range: [0, 2.4], cooldown: 45 },
      { atk: 'musha_tsuki', w: 3, range: [1.2, 3.0], cooldown: 65 },
      { atk: 'musha_iai',  w: 2, range: [2.0, 6.0], cooldown: 140 },
    ],
    delayedChance: 0.28,
  },

  // ——————————————————————————————— 銹の荒野
  machine_stag: {
    name: '銹の角獣',
    region: REGION.RUST,
    tier: 4,
    hp: 210, atk: 17, def: 11,
    poise: 80, stance: 180,
    radius: 0.9, height: 2.3, speed: 5.2,
    aggroRange: 24, keepDistance: 4.5,
    xp: 95, gold: 55,
    // Component-tag combat: parts are child hitboxes with tags, and removing
    // one changes behaviour. This is the archetype where procedural generation
    // is strongest, because a machine has no face to fall into the valley.
    parts: [
      { id: 'core',    tag: 'canister', hp: 40, mul: 2.4, onBreak: 'explode' },
      { id: 'antler',  tag: 'weapon',   hp: 30, mul: 1.6, onBreak: 'disable:machine_gore' },
      { id: 'legL',    tag: 'mobility', hp: 35, mul: 1.2, onBreak: 'slow' },
      { id: 'legR',    tag: 'mobility', hp: 35, mul: 1.2, onBreak: 'slow' },
    ],
    moveset: [
      { atk: 'machine_gore', w: 4, range: [3.0, 9.0], cooldown: 90 },
      { atk: 'machine_beam', w: 3, range: [5.0, 18.0], cooldown: 130 },
      { atk: 'machine_slam', w: 2, range: [0, 2.6], cooldown: 110 },
    ],
    delayedChance: 0.25,
  },
};

export const ENEMY_IDS = Object.keys(ENEMIES).sort();
ENEMY_IDS.forEach((id, i) => { ENEMIES[id].id = id; ENEMIES[id].index = i; });
export const enemyByIndex = (i) => ENEMIES[ENEMY_IDS[i]];
