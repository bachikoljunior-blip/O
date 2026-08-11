import test from 'node:test';
import assert from 'node:assert/strict';
import { accessSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { generateWorld, WORLD_W, WORLD_H } from '../src/world/worldgen.js';
import { QuestLog } from '../src/game/quests.js';
import { Game } from '../src/game/game.js';
import { HUD } from '../src/ui/hud.js';
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
});

test('a corrupt primary save automatically falls back to the valid backup', () => {
  const backup = {
    seed: 77,
    player: { x: 30, y: 40, inv: [], knownSpells: ['fire'], spellSlots: ['fire'] },
    quests: { active: [], done: [] },
    pois: [], settlements: [], camps: [],
  };
  const values = new Map([
    ['aetheria_save_v1', JSON.stringify({ seed: 77, player: { x: 30, y: 40, inv: {} } })],
    ['aetheria_save_backup_v1', JSON.stringify(backup)],
  ]);
  const previousStorage = globalThis.localStorage;
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  try {
    const context = { validSave: Game.prototype.validSave, saveRecovered: false };
    const restored = Game.prototype.readSave.call(context);
    assert.deepEqual(restored, backup);
    assert.equal(context.saveRecovered, true);
    assert.deepEqual(JSON.parse(values.get('aetheria_save_v1')), backup);
  } finally {
    if (previousStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousStorage;
  }
});

test('all touch controls remain separated in portrait, landscape and left-handed mode', () => {
  const hud = new HUD({});
  for (const [w, h] of [[375, 667], [667, 375], [320, 568]]) {
    for (const leftHanded of [false, true]) {
      const layout = hud.layout(w, h, {}, { leftHanded });
      const controls = [layout.attack, layout.dodge, layout.block, layout.interact, layout.item,
        ...layout.spells, layout.menu, layout.map];
      for (let i = 0; i < controls.length; i++) {
        for (let j = i + 1; j < controls.length; j++) {
          const a = controls[i], b = controls[j];
          const gap = Math.hypot(a.x - b.x, a.y - b.y) - a.r - b.r;
          assert.ok(gap >= -0.001, `${w}x${h} ${leftHanded ? 'left' : 'right'}: controls ${i}/${j} overlap by ${-gap}px`);
        }
      }
    }
    const normal = hud.layout(w, h, {}, { leftHanded: false });
    const mirrored = hud.layout(w, h, {}, { leftHanded: true });
    assert.equal(Math.round(normal.attack.x + mirrored.attack.x), w);
  }
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
