import test from 'node:test';
import assert from 'node:assert/strict';
import { accessSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { generateWorld, WORLD_W, WORLD_H } from '../src/world/worldgen.js';
import { QuestLog } from '../src/game/quests.js';
import { Game, DIFFICULTY_PROFILES } from '../src/game/game.js';
import { HUD } from '../src/ui/hud.js';
import { Input } from '../src/core/input.js';
import { Player, Enemy } from '../src/game/entities.js';
import { ACHIEVEMENTS, achievementProgress } from '../src/game/achievements.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function buildWorld(seed) {
  let world = null;
  for (const step of generateWorld(seed)) if (step.world) world = step.world;
  assert.ok(world, `seed ${seed} did not produce a world`);
  return world;
}

function fingerprint(world) {
  let hash = 2166136261;
  for (let i = 0; i < world.biome.length; i += 997) {
    hash ^= world.biome[i] + world.overlay[i] * 31;
    hash = Math.imul(hash, 16777619);
  }
  return [
    hash >>> 0,
    world.settlements.map((s) => `${s.id}:${s.x}:${s.y}`).join('|'),
    world.pois.map((p) => p.id).join('|'),
  ];
}

test('world generation is deterministic and structurally complete', () => {
  const first = buildWorld(12345);
  const second = buildWorld(12345);
  assert.deepEqual(fingerprint(first), fingerprint(second));
  assert.equal(first.w, WORLD_W);
  assert.equal(first.h, WORLD_H);
  assert.equal(first.biome.length, WORLD_W * WORLD_H);
  assert.equal(first.overlay.length, WORLD_W * WORLD_H);
  assert.equal(first.settlements.length, 9);
  assert.ok(first.pois.length >= 54);
  assert.equal(new Set(first.pois.map((p) => p.id)).size, first.pois.length);
  assert.equal(first.isSolidTile(Math.floor(first.start.x / 32), Math.floor(first.start.y / 32)), false);
});

test('several seeds always create a playable start and the critical POIs', () => {
  for (const seed of [1, 20260811, 0xffffffff]) {
    const world = buildWorld(seed);
    const counts = Object.groupBy(world.pois, (p) => p.kind);
    assert.ok(counts.shrine?.length >= 12, `seed ${seed}: shrines`);
    assert.ok(counts.dungeon?.length >= 8, `seed ${seed}: dungeons`);
    assert.ok(counts.camp?.length >= 16, `seed ${seed}: camps`);
    assert.equal(counts.lair?.length, 1, `seed ${seed}: final lair`);
    assert.equal(world.isSolidTile(Math.floor(world.start.x / 32), Math.floor(world.start.y / 32)), false);
  }
});

test('main objectives resolve to the nearest relevant world target', () => {
  const game = {
    player: { x: 0, y: 0 },
    npcs: [
      { role: 'guildmaster', x: 300, y: 100 },
      { role: 'guildmaster', x: 40, y: 20 },
    ],
    world: {
      pois: [
        { id: 'camp-near', kind: 'camp', x: 90, y: 0, cleared: false },
        { id: 'camp-far', kind: 'camp', x: 500, y: 0, cleared: false },
        { id: 'shrine', kind: 'shrine', x: 80, y: 80, activated: false },
        { id: 'dungeon', kind: 'dungeon', x: 120, y: 0, cleared: false },
        { id: 'lair', kind: 'lair', x: 900, y: 0, cleared: false },
      ],
    },
    campState: new Map(),
    toast() {},
    audio: { sfx() {} },
  };
  const quests = new QuestLog(game);
  assert.deepEqual(quests.markers()[0], {
    x: 40, y: 20, main: true, title: '第一章・目覚め', questId: 'mq0', tracked: true,
  });

  quests.active = [{ type: 'clearCamp', title: '野盗', main: true, state: 'active' }];
  assert.equal(quests.markers()[0].x, 90);
  game.campState.set('camp-near', { cleared: true });
  assert.equal(quests.markers()[0].x, 500);

  quests.active = [{ type: 'dungeonBoss', title: '迷宮', main: true, state: 'active' }];
  assert.equal(quests.markers()[0].x, 120);
  quests.active = [{ type: 'killBoss', boss: 'dragon', title: '竜', main: true, state: 'active' }];
  assert.equal(quests.markers()[0].x, 900);
});

test('completed quest details survive a save round-trip', () => {
  const game = { toast() {}, audio: { sfx() {} } };
  const quests = new QuestLog(game);
  quests.done = [{ id: 'sq-1', title: '薬草の採集', desc: '薬草を集めた。', state: 'done' }];
  const restored = new QuestLog(game);
  restored.load(quests.save());
  assert.equal(restored.done[0].title, '薬草の採集');
  assert.equal(restored.done[0].desc, '薬草を集めた。');
});

test('the selected quest stays first and survives a save round-trip', () => {
  const game = { toast() {}, audio: { sfx() {} } };
  const quests = new QuestLog(game);
  const side = { id: 'sq-track', title: '追跡する依頼', state: 'active', marker: { x: 12, y: 34 } };
  quests.active.push(side);
  assert.equal(quests.track(side.id), true);
  assert.equal(quests.orderedActive()[0], side);
  assert.equal(quests.markers()[0].tracked, true);
  assert.equal(quests.markers()[0].questId, side.id);

  const restored = new QuestLog(game);
  restored.load(quests.save());
  assert.equal(restored.trackedId, side.id);
  assert.equal(restored.orderedActive()[0].id, side.id);
});

test('legacy saves cannot get stuck between main chapters', () => {
  const game = { toast() {}, audio: { sfx() {} } };
  const quests = new QuestLog(game);
  quests.load({ active: [], done: ['mq0'], chapter: 0, counters: {} });
  assert.equal(quests.mainQuest?.id, 'mq1');
  assert.equal(quests.chapter, 1);
});

test('structurally corrupt saves are rejected before they can break loading', () => {
  const valid = {
    seed: 42,
    player: { x: 10, y: 20, inv: [{ id: 'bread', n: 2 }], knownSpells: ['fire'], spellSlots: ['fire'] },
    quests: { active: [], done: [] },
    pois: [], settlements: [], camps: [],
  };
  const check = (save) => Game.prototype.validSave.call({}, save);
  assert.equal(check(valid), true);
  assert.equal(check({ ...valid, player: { ...valid.player, inv: {} } }), false);
  assert.equal(check({ ...valid, player: { ...valid.player, inv: [null] } }), false);
  assert.equal(check({ ...valid, quests: { active: {}, done: [] } }), false);
  assert.equal(check({ ...valid, camps: [['broken']] }), false);
  assert.equal(check({ ...valid, achievements: {} }), false);
  assert.equal(check({ ...valid, waypoint: { x: 'broken', y: 10 } }), false);
});

test('a corrupt primary save automatically falls back to the valid backup', () => {
  const backup = {
    seed: 77,
    player: { x: 30, y: 40, inv: [], knownSpells: ['fire'], spellSlots: ['fire'] },
    quests: { active: [], done: [] },
    pois: [], settlements: [], camps: [],
  };
  const values = new Map([
    ['aetheria_save_v1_77', JSON.stringify({ seed: 77, player: { x: 30, y: 40, inv: {} } })],
    ['aetheria_save_backup_v1_77', JSON.stringify(backup)],
  ]);
  const previousStorage = globalThis.localStorage;
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  try {
    const context = { seed: 77, validSave: Game.prototype.validSave, saveRecovered: false };
    const restored = Game.prototype.readSave.call(context);
    assert.deepEqual(restored, backup);
    assert.equal(context.saveRecovered, true);
    assert.deepEqual(JSON.parse(values.get('aetheria_save_v1_77')), backup);
  } finally {
    if (previousStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousStorage;
  }
});

test('autosaves throttle fog image encoding without delaying gameplay saves', () => {
  const values = new Map();
  let encodes = 0;
  const previousStorage = globalThis.localStorage;
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const context = {
    state: 'play', seed: 9, interior: null,
    player: {
      x: 10, y: 20, level: 1, xp: 0, gold: 0, hp: 10, mp: 5, sta: 5,
      inv: [], equip: {}, spellSlots: ['fire'], knownSpells: ['fire'], kills: 0, playtime: 0,
      flow: 0, flowT: 0, hitChain: 0, hitChainT: 0, highestChain: 0, perfectDodges: 0, parries: 0,
    },
    world: { pois: [], settlements: [] }, quests: { save: () => ({ active: [], done: [] }) },
    campState: new Map(), onboarding: null, achievements: new Set(), waypoint: null, lastShrine: null,
    fogCanvas: { toDataURL: () => { encodes++; return 'data:image/webp;base64,fog'; } },
    fogSnapshot: null, fogSnapshotAt: 0, fogDirty: true,
    validSave: Game.prototype.validSave,
    toast() {},
  };
  try {
    Game.prototype.save.call(context, true);
    assert.equal(encodes, 1);
    assert.ok(values.has('aetheria_save_v1_9'));

    context.player.x = 30;
    context.fogDirty = true;
    Game.prototype.save.call(context, true);
    assert.equal(encodes, 1, 'a second autosave should reuse the recent fog snapshot');
    assert.equal(JSON.parse(values.get('aetheria_save_v1_9')).player.x, 30);

    Game.prototype.save.call(context, false);
    assert.equal(encodes, 2, 'a manual save should capture the newest exploration mask');
  } finally {
    if (previousStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousStorage;
  }
});

test('shared seed worlds keep independent saves and cannot erase one another', () => {
  const save = (seed, x) => ({
    seed,
    player: { x, y: 20, inv: [], knownSpells: ['fire'], spellSlots: ['fire'] },
    quests: { active: [], done: [] },
    pois: [], settlements: [], camps: [],
  });
  const values = new Map([
    ['aetheria_save_v1_11', JSON.stringify(save(11, 110))],
    ['aetheria_save_backup_v1_11', JSON.stringify(save(11, 100))],
    ['aetheria_save_v1_22', JSON.stringify(save(22, 220))],
  ]);
  const previousStorage = globalThis.localStorage;
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  try {
    const context = { seed: 11, validSave: Game.prototype.validSave, saveRecovered: false };
    assert.equal(Game.prototype.readSave.call(context).player.x, 110);
    context.seed = 22;
    assert.equal(Game.prototype.readSave.call(context).player.x, 220);

    Game.prototype.clearCurrentSave.call(context);
    assert.equal(values.has('aetheria_save_v1_22'), false);
    assert.equal(values.has('aetheria_save_v1_11'), true);
    assert.equal(values.has('aetheria_save_backup_v1_11'), true);
  } finally {
    if (previousStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousStorage;
  }
});

test('a legacy single-slot save migrates into its seed slot', () => {
  const legacy = {
    seed: 33,
    player: { x: 330, y: 20, inv: [], knownSpells: ['fire'], spellSlots: ['fire'] },
    quests: { active: [], done: [] },
    pois: [], settlements: [], camps: [],
  };
  const values = new Map([['aetheria_save_v1', JSON.stringify(legacy)]]);
  const previousStorage = globalThis.localStorage;
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  try {
    const context = { seed: 33, validSave: Game.prototype.validSave, saveRecovered: false };
    assert.equal(Game.prototype.readSave.call(context).player.x, 330);
    assert.deepEqual(JSON.parse(values.get('aetheria_save_v1_33')), legacy);
  } finally {
    if (previousStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousStorage;
  }
});

test('all touch controls remain separated at every size in portrait, landscape and left-handed mode', () => {
  const hud = new HUD({});
  for (const [w, h] of [[375, 667], [667, 375], [320, 568]]) {
    for (const controlScale of [0.82, 1, 1.18]) {
      for (const leftHanded of [false, true]) {
        const layout = hud.layout(w, h, {}, { leftHanded, controlScale });
        const controls = [layout.attack, layout.dodge, layout.block, layout.interact, layout.item,
          ...layout.spells, layout.menu, layout.map];
        for (let i = 0; i < controls.length; i++) {
          for (let j = i + 1; j < controls.length; j++) {
            const a = controls[i], b = controls[j];
            const gap = Math.hypot(a.x - b.x, a.y - b.y) - a.r - b.r;
            assert.ok(gap >= -0.001, `${w}x${h} scale ${controlScale} ${leftHanded ? 'left' : 'right'}: controls ${i}/${j} overlap by ${-gap}px`);
          }
        }
      }
    }
    const normal = hud.layout(w, h, {}, { leftHanded: false });
    const mirrored = hud.layout(w, h, {}, { leftHanded: true });
    assert.equal(Math.round(normal.attack.x + mirrored.attack.x), w);
  }
});

test('difficulty profiles change damage, dodge timing and rewards coherently', () => {
  assert.ok(DIFFICULTY_PROFILES.story.incoming < DIFFICULTY_PROFILES.adventure.incoming);
  assert.ok(DIFFICULTY_PROFILES.legend.incoming > DIFFICULTY_PROFILES.adventure.incoming);
  assert.ok(DIFFICULTY_PROFILES.story.rollIframes > DIFFICULTY_PROFILES.adventure.rollIframes);
  assert.ok(DIFFICULTY_PROFILES.legend.reward > DIFFICULTY_PROFILES.adventure.reward);

  const damageTaken = (id) => {
    const player = new Player(0, 0);
    player.hp = player.maxHp = 1000;
    const game = {
      difficultyProfile: () => DIFFICULTY_PROFILES[id],
      pt: { text() {}, ring() {}, burst() {} },
      shake() {}, vibrate() {}, hitStop() {}, onPlayerDeath() {},
    };
    return player.damage(game, 100, 10, 0);
  };
  assert.ok(damageTaken('story') < damageTaken('adventure'));
  assert.ok(damageTaken('legend') > damageTaken('adventure'));

  for (const id of Object.keys(DIFFICULTY_PROFILES)) {
    const player = new Player(0, 0);
    player.sta = 100;
    const game = {
      difficultyProfile: () => DIFFICULTY_PROFILES[id],
      input: { moveVector: () => ({ x: 1, y: 0, m: 1 }) },
      pt: { spawn() {}, burst() {} }, vibrate() {},
    };
    player.tryRoll(game);
    assert.equal(player.sta, 100 - DIFFICULTY_PROFILES[id].dodgeCost);
    assert.equal(player.iframes, DIFFICULTY_PROFILES[id].rollIframes);
  }
});

test('a custom map waypoint takes navigation priority and clears safely', () => {
  const calls = [];
  const context = {
    world: {
      w: 20, h: 20,
      inBounds: (x, y) => x >= 0 && y >= 0 && x < 20 && y < 20,
      isSolidTile: () => false,
      isWaterTile: () => false,
    },
    interior: null,
    waypoint: null,
    quests: { markers: () => [{ x: 900, y: 900, main: true, tracked: true }] },
    toast: (message) => calls.push(message),
    save: () => calls.push('save'),
  };
  assert.equal(Game.prototype.setWaypoint.call(context, 160, 224), true);
  const markers = Game.prototype.navigationMarkers.call(context);
  assert.equal(markers[0].custom, true);
  assert.equal(markers[0].tracked, true);
  assert.equal(markers[0].title, '地図の目印');
  assert.equal(Game.prototype.clearWaypoint.call(context), true);
  assert.equal(context.waypoint, null);
  assert.ok(calls.includes('save'));
});

test('backgrounding clears every held control before mobile play resumes', () => {
  const input = Object.create(Input.prototype);
  input.keys = new Set(['w']);
  input.keysPressed = new Set(['w']);
  input.pointers = new Map([[1, { role: 'stick' }]]);
  input.buttonDownIds = new Set(['block']);
  input.buttonPressedIds = new Set(['attack']);
  input.buttonReleasedIds = new Set(['menu']);
  input.stick = { active: true, x: 0.8, y: -0.3, id: 1 };
  input.ui = { down: true, pressed: true, released: true, dragX: 10, dragY: -12, id: 2, moved: true };
  input.anyPress = true;

  input.cancelActive();

  assert.equal(input.keys.size, 0);
  assert.equal(input.keysPressed.size, 0);
  assert.equal(input.pointers.size, 0);
  assert.equal(input.buttonDownIds.size, 0);
  assert.equal(input.buttonPressedIds.size, 0);
  assert.equal(input.buttonReleasedIds.size, 0);
  assert.deepEqual({ active: input.stick.active, x: input.stick.x, y: input.stick.y, id: input.stick.id },
    { active: false, x: 0, y: 0, id: null });
  assert.deepEqual({ down: input.ui.down, pressed: input.ui.pressed, released: input.ui.released,
    dragX: input.ui.dragX, dragY: input.ui.dragY, id: input.ui.id, moved: input.ui.moved },
  { down: false, pressed: false, released: false, dragX: 0, dragY: 0, id: null, moved: false });
  assert.equal(input.anyPress, false);
});

test('mobile interruptions pause active play exactly once', () => {
  const opened = [];
  const context = {
    state: 'play', player: { alive: true },
    menus: { isOpen: false, open: (name) => { opened.push(name); context.menus.isOpen = true; } },
    input: { setUIMode: (on) => opened.push(on ? 'ui' : 'game') },
  };
  assert.equal(Game.prototype.pauseForInterruption.call(context), true);
  assert.deepEqual(opened, ['pause', 'ui']);
  assert.equal(Game.prototype.pauseForInterruption.call(context), false);
  assert.deepEqual(opened, ['pause', 'ui']);
});

test('perfect dodges build Flow once per roll and reward precise timing', () => {
  const player = new Player(0, 0);
  let staggered = 0;
  const game = {
    toast() {}, shake() {}, hitStop() {}, vibrate() {},
    pt: { text() {}, ring() {}, burst() {} },
  };
  const attacker = { boss: false, stagger: (t) => { staggered = t; } };
  player.state = 'roll';
  player.iframes = 0.2;
  player.sta = 10;
  assert.equal(player.damage(game, 50, 10, 0, { attacker }), 0);
  assert.equal(player.perfectDodges, 1);
  assert.ok(player.flow >= 22);
  assert.ok(player.sta > 10);
  assert.ok(staggered >= 0.7);
  player.damage(game, 50, 10, 0, { attacker });
  assert.equal(player.perfectDodges, 1, 'one roll cannot score twice');

  player.flow = 99;
  player.gainFlow(game, 2);
  assert.equal(player.flowT, 8);
  assert.equal(player.flow, 0);
});

test('repeated pressure breaks enemy poise without killing it', () => {
  const enemy = new Enemy('bandit', 20, 0, 1);
  const game = {
    player: { gainFlow() {} },
    pt: { text() {}, ring() {}, burst() {} },
    hitStop() {}, onEnemyKilled() {},
  };
  enemy.damage(game, 20, 0, 0, { src: 'player' });
  enemy.damage(game, 20, 0, 0, { src: 'player' });
  enemy.damage(game, 20, 0, 0, { src: 'player' });
  assert.equal(enemy.alive, true);
  assert.equal(enemy.state, 'stagger');
  assert.ok(enemy.stun >= 1.35);
});

test('dragon specials have readable normal and enraged patterns', () => {
  const dragon = new Enemy('dragon', 0, 0, 1);
  const game = { player: { x: 100, y: 20, face: 0 } };
  dragon.specialIndex = 1;
  dragon.beginDragonSpecial(game, 0);
  assert.equal(dragon.state, 'bossMeteor');
  assert.equal(dragon.specialTargets.length, 2);
  dragon.enraged = true;
  dragon.specialIndex = 1;
  dragon.beginDragonSpecial(game, 0);
  assert.equal(dragon.specialTargets.length, 3);
});

test('achievement progress is derived from durable gameplay statistics', () => {
  const game = {
    onboarding: null,
    player: { kills: 100, highestChain: 15, perfectDodges: 5, parries: 5, inv: [] },
    world: { settlements: [], pois: [] },
    quests: { chapter: 6, counters: { dungeonBoss: 1 } },
  };
  for (const id of ['journey', 'hunter', 'flow_master', 'untouchable', 'parry_master', 'delver', 'legend']) {
    const def = ACHIEVEMENTS.find((a) => a.id === id);
    assert.equal(achievementProgress(def, game).complete, true, id);
  }
});

test('the install manifest and every offline core asset are present', () => {
  const manifest = JSON.parse(readFileSync(resolve(root, 'manifest.webmanifest'), 'utf8'));
  assert.equal(manifest.start_url, './');
  assert.ok(manifest.icons.some((icon) => icon.sizes === '192x192'));
  assert.ok(manifest.icons.some((icon) => icon.sizes === '512x512'));
  for (const icon of manifest.icons) accessSync(resolve(root, icon.src));

  const worker = readFileSync(resolve(root, 'sw.js'), 'utf8');
  const coreBlock = worker.match(/const CORE = \[([\s\S]*?)\];/)?.[1] || '';
  const assets = [...coreBlock.matchAll(/'\.\/(.*?)'/g)].map((match) => match[1] || 'index.html');
  assert.ok(assets.length > 20);
  for (const asset of assets) accessSync(resolve(root, asset));
});
