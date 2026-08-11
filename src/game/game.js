// game.js — the game hub: state, systems, interactions, save/load.

import {
  clamp, lerp, dist, dist2, angleTo, angleDiff, makeRNG, TAU, makeCanvas, hash2, smoothstep,
} from '../core/util.js';
import { Input } from '../core/input.js';
import { audio } from '../core/audio.js';
import { SAVE_KEY, SAVE_BACKUP_KEY, LAST_SEED_KEY } from '../core/seed.js';
import { generateWorld, TILE, B, WATER, BIOME_NAME, SEA } from '../world/worldgen.js';
import { WorldRuntime, regionLevel, pickSpawnKind } from '../world/runtime.js';
import { Dungeon } from '../world/dungeon.js';
import { Renderer, skyAt } from '../render/renderer.js';
import { Particles, FX } from '../render/particles.js';
import { mapColor } from '../render/tiles.js';
import { drawHumanoid } from '../render/actors.js';
import { Player, Enemy, NPC, Projectile, Drop, ENEMY_TYPES, SPELLS } from './entities.js';
import {
  ITEMS, RECIPES, rollEquip, rollLoot, shopStock, upgradeCost, effStats, displayName, setUid, nextUid,
} from './items.js';
import { QuestLog, MAIN_CHAPTERS } from './quests.js';
import { ACHIEVEMENTS, achievementProgress } from './achievements.js';
import { greeting, options as dlgOptions, rumor } from '../game/dialogue.js';
import { UI } from '../ui/ui.js';
import { HUD } from '../ui/hud.js';
import { Menus } from '../ui/menus.js';

const SAVE_EXPORT_FORMAT = 'aetheria-chronicle-save';
const SAVE_EXPORT_VERSION = 1;
const MAX_SAVE_TEXT_LENGTH = 5 * 1024 * 1024;
const DAY_LENGTH = 780;          // seconds per in-game day

function slotKey(base, seed) {
  return `${base}_${seed >>> 0}`;
}

export const DIFFICULTY_PROFILES = Object.freeze({
  story: Object.freeze({
    id: 'story', name: '物語', incoming: 0.68, outgoing: 1.25, reward: 1,
    dodgeCost: 18, rollIframes: 0.36, parryWindow: 0.28, deathLoss: 0.05,
  }),
  adventure: Object.freeze({
    id: 'adventure', name: '冒険者', incoming: 1, outgoing: 1, reward: 1,
    dodgeCost: 24, rollIframes: 0.30, parryWindow: 0.22, deathLoss: 0.15,
  }),
  legend: Object.freeze({
    id: 'legend', name: '伝説', incoming: 1.22, outgoing: 0.9, reward: 1.25,
    dodgeCost: 26, rollIframes: 0.27, parryWindow: 0.18, deathLoss: 0.20,
  }),
});

export class Game {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new Renderer(canvas);
    this.input = new Input(canvas);
    this.audio = audio;
    this.pt = new Particles();
    this.ui = new UI();
    this.hud = new HUD(this.ui);
    this.menus = new Menus(this.ui);
    this.state = 'loading';
    this.seed = (Math.random() * 1e9) | 0;
    this.time = 8 * (DAY_LENGTH / 24);
    this.day = 1;
    this.frame = 0;
    this.enemies = [];
    this.npcs = [];
    this.drops = [];
    this.projectiles = [];
    this.objects = [];
    this.groundMarkers = [];
    this.weather = { rain: 0, snow: 0, fog: 0, wind: 0.4, target: { rain: 0, snow: 0, fog: 0 }, timer: 40 };
    this.ashFall = 0;
    this.hitStopT = 0;
    this.flashT = 0;
    this.fadeT = 0;
    this.interior = null;
    this.activeBoss = null;
    this.settings = {
      music: 0.5, sfx: 0.75, vibrate: true, hq: true, aimAssist: true, showFps: false,
      leftHanded: false,
      reduceMotion: typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches,
      difficulty: 'adventure',
      controlScale: 1, controlOpacity: 0.9,
    };
    this.safeArea = { top: 0, right: 0, bottom: 0, left: 0 };
    this.fps = 60;
    this.spawnTimer = 0;
    this.autosaveT = 0;
    this.campState = new Map();
    this.poiObjects = new Map();
    this.lastShrine = null;
    this.locationBanner = null;
    this.lastBiome = -1;
    this.onboarding = null;
    this.waypoint = null;
    this.achievements = new Set();
    this.achievementCheckT = 0;
    this.victoryPending = 0;
    this.saveRecovered = false;
    this.saveTransferStatus = '';
    this.fogSnapshot = null;
    this.fogSnapshotAt = 0;
    this.fogDirty = true;
    this.loadSettings();
  }

  // ————————————————————————————————————————————————— setup

  *generate(seed) {
    this.seed = seed >>> 0;
    let world = null;
    for (const p of generateWorld(this.seed)) {
      if (p.world) world = p.world;
      yield p;
    }
    this.world = world;
    this.runtime = new WorldRuntime(world);
    yield { phase: '地図を描く', t: 0.98 };
    this.buildMapCanvas();
    this.player = new Player(world.start.x, world.start.y);
    this.quests = new QuestLog(this);
    this.spawnSettlementNPCs();
    this.startingKit();
    this.renderer.cam.x = this.player.x;
    this.renderer.cam.y = this.player.y;
    yield { phase: '完了', t: 1 };
  }

  buildMapCanvas() {
    const w = this.world.w, h = this.world.h;
    const { canvas, ctx } = makeCanvas(w, h);
    const img = ctx.createImageData(w, h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const c = mapColor(this.world, x, y);
        const o = (y * w + x) * 4;
        img.data[o] = c[0]; img.data[o + 1] = c[1]; img.data[o + 2] = c[2]; img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    this.mapCanvas = canvas;
    const fog = makeCanvas(w, h);
    fog.ctx.fillStyle = 'rgba(8,9,12,1)';
    fog.ctx.fillRect(0, 0, w, h);
    this.fogCanvas = fog.canvas;
    this.fogCtx = fog.ctx;
    this.fogCtx.globalCompositeOperation = 'destination-out';
  }

  revealFog(tx, ty, r = 22) {
    if (!this.fogCtx) return;
    const g = this.fogCtx.createRadialGradient(tx, ty, r * 0.35, tx, ty, r);
    g.addColorStop(0, 'rgba(0,0,0,1)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    this.fogCtx.fillStyle = g;
    this.fogCtx.beginPath();
    this.fogCtx.arc(tx, ty, r, 0, TAU);
    this.fogCtx.fill();
    this.fogDirty = true;
  }

  startingKit() {
    const rng = makeRNG(this.seed ^ 0x1234);
    const sword = rollEquip(rng, 1, 0, 'weapon');
    sword.name = '駆け出しの剣';
    sword.weaponKind = 'sword'; sword.icon = 'sword'; sword.reach = 34; sword.atkSpeed = 1; sword.weaponLen = 18;
    const body = rollEquip(rng, 1, 0, 'body');
    this.giveEquip(sword, true);
    this.giveEquip(body, true);
    this.equipItem(sword);
    this.equipItem(body);
    this.giveItem('potion_s', 3, true);
    this.giveItem('bread', 2, true);
  }

  spawnSettlementNPCs() {
    for (const s of this.world.settlements) {
      const rng = makeRNG((s.x * 7919 + s.y * 104729) >>> 0);
      const roleFor = { inn: 'innkeeper', shop: 'merchant', smith: 'smith', alchemist: 'alchemist', guild: 'guildmaster', temple: 'priest', castle: 'elder' };
      for (const b of s.buildings) {
        const role = roleFor[b.type];
        const bx = (b.tx + b.tw / 2) * TILE;
        const by = (b.ty + b.th + 0.8) * TILE;
        if (role) {
          const n = new NPC(bx, by, role, s, rng, { x: bx, y: by });
          n.shopSeed = (rng() * 1e9) | 0;
          n.building = b;
          this.npcs.push(n);
        }
      }
      const villagers = s.type === 'capital' ? 12 : s.type === 'town' ? 8 : s.type === 'village' ? 5 : 3;
      for (let i = 0; i < villagers; i++) {
        const a = rng() * TAU, rad = rng.range(2, s.size - 2) * TILE;
        const x = (s.x + 0.5) * TILE + Math.cos(a) * rad;
        const y = (s.y + 0.5) * TILE + Math.sin(a) * rad;
        const home = s.buildings.length ? rng.pick(s.buildings) : null;
        const role = rng.chance(0.16) ? 'child' : 'villager';
        this.npcs.push(new NPC(x, y, role, s, rng, home ? { x: (home.tx + home.tw / 2) * TILE, y: (home.ty + home.th + 0.6) * TILE } : null));
      }
      const guards = s.type === 'capital' ? 6 : s.type === 'town' ? 4 : 2;
      for (let i = 0; i < guards; i++) {
        const a = (i / guards) * TAU;
        const x = (s.x + 0.5) * TILE + Math.cos(a) * (s.size - 1.5) * TILE;
        const y = (s.y + 0.5) * TILE + Math.sin(a) * (s.size - 1.5) * TILE;
        const n = new NPC(x, y, 'guard', s, rng, null);
        n.post = { x, y };
        this.npcs.push(n);
      }
      // quest board seed
      s.boardSeed = (rng() * 1e9) | 0;
    }
  }

  newGame() {
    // full reset — the world stays, everything the hero touched does not
    this.player = new Player(this.world.start.x, this.world.start.y);
    this.quests = new QuestLog(this);
    this.runtime = new WorldRuntime(this.world);
    this.enemies.length = 0;
    this.npcs.length = 0;
    this.drops.length = 0;
    this.projectiles.length = 0;
    this.objects.length = 0;
    this.pt.clear();
    this.campState.clear();
    this.poiObjects.clear();
    this.interior = null;
    this.activeBoss = null;
    this.waypoint = null;
    this.lastShrine = null;
    this.locationBanner = null;
    this.lastBiome = -1;
    for (const poi of this.world.pois) { poi.discovered = false; poi.activated = false; poi.cleared = false; poi.looted = false; }
    for (const st of this.world.settlements) { st.discovered = false; st.board = null; st.boardDay = -1; }
    this.buildMapCanvas();
    this.fogSnapshot = null;
    this.fogSnapshotAt = 0;
    this.fogDirty = true;
    this.spawnSettlementNPCs();
    this.startingKit();
    this.renderer.cam.x = this.player.x;
    this.renderer.cam.y = this.player.y;
    this.state = 'play';
    this.time = 8 * (DAY_LENGTH / 24);
    this.day = 1;
    this.spawnTimer = 0;
    this.autosaveT = 0;
    this.weather = { rain: 0, snow: 0, fog: 0, wind: 0.4, target: { rain: 0, snow: 0, fog: 0 }, timer: 40 };
    this.clearCurrentSave();
    this.audio.init();
    this.showBanner(MAIN_CHAPTERS[0].title, MAIN_CHAPTERS[0].desc);
    // The first lessons advance only after the player actually performs them.
    this.onboarding = { stage: 0, x: this.player.x, y: this.player.y };
    this.achievements.clear();
    this.achievementCheckT = 0;
    this.victoryPending = 0;
    const cap = this.world.settlements[0];
    if (cap) { cap.discovered = true; this.locationBanner = { name: cap.name + cap.label, sub: '旅の始まり', t: 0 }; }
    // A first-session kill on mobile must not erase the new character.
    this.save(true);
  }

  // ————————————————————————————————————————————————— time & weather

  hour() { return ((this.time % DAY_LENGTH) / DAY_LENGTH) * 24; }
  isNight() { const h = this.hour(); return h < 5.6 || h > 19.4; }
  biomeName() {
    if (this.interior) return this.interior.name;
    const tx = Math.floor(this.player.x / TILE), ty = Math.floor(this.player.y / TILE);
    for (const s of this.world.settlements) {
      if (dist(tx, ty, s.x, s.y) < s.size + 3) return s.name + s.label;
    }
    return BIOME_NAME[this.world.biomeAt(tx, ty)] || '荒野';
  }

  difficultyProfile() {
    return DIFFICULTY_PROFILES[this.settings.difficulty] || DIFFICULTY_PROFILES.adventure;
  }

  setDifficulty(id, announce = true) {
    if (!DIFFICULTY_PROFILES[id]) return false;
    this.settings.difficulty = id;
    this.saveSettings();
    if (announce && this.state === 'play') this.toast(`難易度を「${DIFFICULTY_PROFILES[id].name}」に変更`, '#ffe08a');
    return true;
  }

  cycleDifficulty(step = 1) {
    const ids = Object.keys(DIFFICULTY_PROFILES);
    const current = Math.max(0, ids.indexOf(this.settings.difficulty));
    return this.setDifficulty(ids[(current + step + ids.length) % ids.length], false);
  }

  navigationMarkers() {
    const markers = this.quests?.markers?.() || [];
    if (!this.waypoint || this.interior) return markers;
    return [{
      x: this.waypoint.x, y: this.waypoint.y, title: '地図の目印',
      questId: 'waypoint', custom: true, tracked: true, main: false,
    }, ...markers];
  }

  setWaypoint(x, y) {
    if (!this.world || this.interior || !Number.isFinite(x) || !Number.isFinite(y)) return false;
    let tx = clamp(Math.floor(x / TILE), 0, this.world.w - 1);
    let ty = clamp(Math.floor(y / TILE), 0, this.world.h - 1);
    if (this.world.isSolidTile(tx, ty) || this.world.isWaterTile(tx, ty)) {
      let found = null;
      for (let r = 1; r <= 8 && !found; r++) {
        for (let oy = -r; oy <= r && !found; oy++) {
          for (let ox = -r; ox <= r; ox++) {
            if (Math.abs(ox) !== r && Math.abs(oy) !== r) continue;
            const nx = tx + ox, ny = ty + oy;
            if (!this.world.inBounds(nx, ny) || this.world.isSolidTile(nx, ny) || this.world.isWaterTile(nx, ny)) continue;
            found = { x: nx, y: ny };
            break;
          }
        }
      }
      if (!found) return false;
      tx = found.x; ty = found.y;
    }
    this.waypoint = { x: (tx + 0.5) * TILE, y: (ty + 0.5) * TILE };
    this.toast('地図に目印を置きました', '#8fd0ff');
    this.save(true);
    return true;
  }

  clearWaypoint(announce = true) {
    if (!this.waypoint) return false;
    this.waypoint = null;
    if (announce) this.toast('地図の目印を消しました', '#8fd0ff');
    this.save(true);
    return true;
  }

  pauseForInterruption() {
    if (this.state !== 'play' || this.menus.isOpen || !this.player?.alive) return false;
    this.menus.open('pause');
    this.input.setUIMode(true);
    return true;
  }

  updateWeather(dt) {
    const W = this.weather;
    W.timer -= dt;
    if (W.timer <= 0) {
      W.timer = 60 + Math.random() * 180;
      const tx = Math.floor(this.player.x / TILE), ty = Math.floor(this.player.y / TILE);
      const b = this.world.biomeAt(tx, ty);
      const cold = b === B.SNOW || b === B.TAIGA || b === B.TUNDRA || b === B.PEAK;
      const dry = b === B.DESERT || b === B.ASH || b === B.SAVANNA;
      const wet = b === B.SWAMP || b === B.DARKWOOD || b === B.FOREST;
      const r = Math.random();
      W.target.rain = 0; W.target.snow = 0; W.target.fog = 0;
      if (cold && r < 0.42) W.target.snow = 0.4 + Math.random() * 0.6;
      else if (!dry && r < (wet ? 0.5 : 0.28)) W.target.rain = 0.35 + Math.random() * 0.65;
      else if (r < 0.55) W.target.fog = Math.random() * 0.5;
      W.windTarget = 0.2 + Math.random() * 1.2;
    }
    const k = 1 - Math.pow(0.35, dt);
    W.rain = lerp(W.rain, W.target.rain, k);
    W.snow = lerp(W.snow, W.target.snow, k);
    W.fog = lerp(W.fog, W.target.fog, k);
    W.wind = lerp(W.wind, W.windTarget ?? 0.4, k * 0.5);
    const tx = Math.floor(this.player.x / TILE), ty = Math.floor(this.player.y / TILE);
    this.ashFall = lerp(this.ashFall, this.world.biomeAt(tx, ty) === B.ASH ? 1 : 0, 1 - Math.pow(0.2, dt));
  }

  // ————————————————————————————————————————————————— collision helpers

  blocked(x, y, r, self) {
    if (this.interior) {
      if (this.interior.blocked(x, y, r)) return true;
    } else {
      const w = this.world;
      const t = TILE;
      for (const [ox, oy] of [[-r, 0], [r, 0], [0, -r * 0.5], [0, r * 0.4]]) {
        const tx = Math.floor((x + ox) / t), ty = Math.floor((y + oy) / t);
        if (w.isSolidTile(tx, ty)) return true;
      }
      // buildings
      for (const s of w.settlements) {
        if (Math.abs((s.x + 0.5) * t - x) > (s.size + 14) * t) continue;
        if (Math.abs((s.y + 0.5) * t - y) > (s.size + 14) * t) continue;
        for (const b of s.buildings) {
          const bx = b.tx * t, by = b.ty * t, bw = b.tw * t, bh = b.th * t;
          if (x + r > bx && x - r < bx + bw && y + r * 0.6 > by && y - r * 0.4 < by + bh) return true;
        }
      }
    }
    // props
    let hit = false;
    const list = this.interior ? this.interior.props : null;
    if (list) {
      for (const p of list) {
        if (!p.r) continue;
        if (dist2(x, y, p.x, p.y) < (p.r + r) * (p.r + r)) { hit = true; break; }
      }
    } else {
      this.runtime.forEachPropNear(x, y, r + 24, (p) => {
        if (hit || !p.r) return;
        if (p.harvest && this.runtime.isHarvested(p, this.time)) return;
        if (dist2(x, y, p.x, p.y) < (p.r + r) * (p.r + r)) hit = true;
      });
    }
    if (hit) return true;
    for (const o of this.objects) {
      if (!o.solid) continue;
      if (dist2(x, y, o.x, o.y) < (o.r + r) * (o.r + r)) return true;
    }
    return false;
  }

  blockedPoint(x, y) {
    if (this.interior) return this.interior.blocked(x, y, 3);
    const tx = Math.floor(x / TILE), ty = Math.floor(y / TILE);
    return this.world.isSolidTile(tx, ty);
  }

  isWater(x, y) {
    if (this.interior) return false;
    return this.world.isWaterTile(Math.floor(x / TILE), Math.floor(y / TILE));
  }
  isRoad(x, y) {
    if (this.interior) return false;
    const o = this.world.overlayAt(Math.floor(x / TILE), Math.floor(y / TILE));
    return o === 1 || o === 5 || o === 6 || o === 4;
  }

  // ————————————————————————————————————————————————— combat helpers

  spawnProjectile(o) { this.projectiles.push(new Projectile(o)); }

  areaDamage(x, y, r, dmg, opt = {}) {
    if (opt.src === 'player' || !opt.src) {
      for (const e of this.enemies) {
        if (!e.alive) continue;
        const d = dist(x, y, e.x, e.y);
        if (d > r + e.r) continue;
        const falloff = clamp(1 - (d / (r + e.r)) * 0.5, 0.4, 1);
        const dealt = e.damage(this, dmg * falloff, x, y, { ...opt, knock: 180 });
        if (opt.src === 'player' || !opt.src) this.player.recordHit(this, dealt, !!opt.crit);
      }
    }
    if (opt.src === 'enemy') {
      const p = this.player;
      if (p.alive && dist(x, y, p.x, p.y) < r + p.r) p.damage(this, dmg, x, y, opt);
    }
  }

  coneEffect(x, y, ang, range, halfAngle, fn) {
    for (const e of this.enemies) {
      if (!e.alive) continue;
      const d = dist(x, y, e.x, e.y);
      if (d > range) continue;
      if (Math.abs(angleDiff(ang, angleTo(x, y, e.x, e.y))) > halfAngle) continue;
      fn(e);
    }
  }

  nearestEnemy(x, y, maxD = 400) {
    let best = null, bd = maxD * maxD;
    for (const e of this.enemies) {
      if (!e.alive) continue;
      const d = dist2(x, y, e.x, e.y);
      if (d < bd) { bd = d; best = e; }
    }
    return best;
  }

  /** Soft target selection for one-thumb combat; it never locks the camera. */
  aimTarget(x, y, face, maxD, cone = 1.1) {
    if (!this.settings.aimAssist) return null;
    let best = null, bestScore = Infinity;
    for (const e of this.enemies) {
      if (!e.alive) continue;
      const d = dist(x, y, e.x, e.y);
      if (d > maxD + e.r) continue;
      const delta = Math.abs(angleDiff(face, angleTo(x, y, e.x, e.y)));
      if (delta > cone) continue;
      const score = d + delta * maxD * 0.42;
      if (score < bestScore) { best = e; bestScore = score; }
    }
    return best;
  }

  hitProps(x, y, ang, reach, arc) {
    const strike = (p) => {
      if (!p.harvest) return;
      if (this.runtime.isHarvested(p, this.time)) return;
      const d = dist(x, y, p.x, p.y);
      if (d > reach + 6) return;
      if (Math.abs(angleDiff(ang, angleTo(x, y, p.x, p.y))) > arc) return;
      this.harvestProp(p);
    };
    if (this.interior) this.interior.props.forEach(strike);
    else this.runtime.forEachPropNear(x, y, reach + 20, strike);
  }

  harvestProp(p) {
    const rng = makeRNG((p.x * 31 + p.y * 17 + this.frame) | 0);
    const table = {
      herb: [['herb', 1, 2]], berry: [['herb', 0, 1]], shroom: [['herb', 0, 1], ['bloom', 0, 1]],
      ore: [['ore_iron', 1, 2], ['ore_silver', 0, 1]], crystal: [['core', 1, 1], ['ore_myth', 0, 1]],
    };
    const t = table[p.harvest] || [];
    let got = false;
    for (const [id, lo, hi] of t) {
      const n = rng.irange(lo, hi);
      if (n > 0) { this.giveItem(id, n); got = true; }
    }
    if (p.harvest === 'shroom' && this.isNight()) this.giveItem('bloom', 1);
    this.runtime.harvest(p, this.time);
    FX.dust(this.pt, p.x, p.y, 8);
    this.pt.burst(p.x, p.y - 10, 10, { speed: 70, life: 0.5, r: 2.6, col: '#a8e05a', g: 180 });
    audio.sfx(p.harvest === 'ore' || p.harvest === 'crystal' ? 'craft' : 'pickup');
    if (!got) this.toast('何も採れなかった', '#999');
  }

  enemyAlert(e) {
    this.pt.text(e.x, e.y - e.r * 2.6, '！', { col: '#ff6a4a', size: 18 });
  }

  onEnemyKilled(e, opt) {
    const p = this.player;
    const reward = this.difficultyProfile().reward;
    p.kills++;
    p.addXp(Math.round(e.T.xp * (1 + (e.level - 1) * 0.22) * reward), this);
    const rng = makeRNG((e.x * 7919 + e.y * 104729 + this.frame) | 0);
    const gold = Math.round(rng.irange(e.T.gold[0], e.T.gold[1]) * (1 + Math.floor(e.level / 4)) * reward);
    if (gold > 0) this.drops.push(new Drop(e.x, e.y, { gold }));
    for (const l of rollLoot(rng, e.kind, e.level)) this.drops.push(new Drop(e.x, e.y, l));
    this.quests.onKill(e.kind, e.x, e.y);
    if (e.boss) {
      this.activeBoss = null;
      this.shake(12, 0.8);
      this.flashT = 0.7;
      if (e.kind === 'dragon') {
        if (this.dungeonPoi) this.dungeonPoi.cleared = true;
        this.quests.onBossKill('dragon');
        this.showBanner('灰燼竜、堕つ', '大陸に朝が還った。');
      } else {
        if (this.dungeonPoi) this.dungeonPoi.cleared = true;
        this.quests.onDungeonBoss();
        this.drops.push(new Drop(e.x, e.y, { id: 'relic', n: 1 }));
      }
      this.save(true);
      audio.setMood(this.interior ? 'dungeon' : 'explore');
    }
    if (e.campId) {
      const st = this.campState.get(e.campId);
      if (st) {
        st.alive--;
        if (st.alive <= 0 && !st.cleared) {
          st.cleared = true;
          const poi = this.world.pois.find((q) => q.id === e.campId);
          if (poi) poi.cleared = true;
          this.quests.onCampCleared();
          this.toast('野営地を制圧した！', '#ffd76a');
          this.spawnChest(st.x, st.y, regionLevel(this.world, st.x, st.y) + 2);
          this.save(true);
        }
      }
    }
  }

  onPlayerDeath() {
    const lost = Math.floor(this.player.gold * this.difficultyProfile().deathLoss);
    this.player.gold -= lost;
    this.lostGold = lost;
    audio.setMood('night');
    setTimeout(() => { if (!this.player.alive) this.menus.open('death'); }, 1500);
  }

  respawn() {
    const p = this.player;
    p.alive = true;
    p.hp = p.maxHp * 0.6;
    p.mp = p.maxMp * 0.5;
    p.sta = p.maxSta;
    p.state = 'idle';
    p.deathT = 0;
    this.enemies.length = 0;
    this.activeBoss = null;
    this.interior = null;
    const t = this.lastShrine || this.world.start;
    p.x = t.x; p.y = t.y;
    this.renderer.cam.x = p.x; this.renderer.cam.y = p.y;
    this.fadeT = 1;
    audio.setMood('explore');
    this.save(true);
  }

  // ————————————————————————————————————————————————— inventory

  itemDef(id) { return ITEMS[id]; }
  countItem(id) {
    const e = this.player.inv.find((x) => x.id === id);
    return e ? e.n : 0;
  }
  giveItem(id, n = 1, silent = false) {
    const def = ITEMS[id];
    if (!def) return;
    const e = this.player.inv.find((x) => x.id === id);
    if (e) e.n += n;
    else this.player.inv.push({ id, n });
    if (!silent) this.toast(`${def.name} ×${n}`, def.color || '#cfd6dd');
    this.quests.onCollect(id, n);
  }
  giveEquip(item, silent) {
    this.player.inv.push({ item, n: 1 });
    if (!silent) this.toast(`${displayName(item)} を手に入れた`, '#8fd0ff');
  }
  removeItem(id, n = 1) {
    const i = this.player.inv.findIndex((x) => x.id === id);
    if (i < 0) return false;
    this.player.inv[i].n -= n;
    if (this.player.inv[i].n <= 0) this.player.inv.splice(i, 1);
    return true;
  }
  dropItem(entry) {
    const i = this.player.inv.indexOf(entry);
    if (i >= 0) this.player.inv.splice(i, 1);
  }
  equipItem(item) {
    const p = this.player;
    const slot = item.slot;
    p.equip[slot] = item;
    p.recompute();
    audio.sfx('uiBig');
  }
  unequip(slot) {
    this.player.equip[slot] = null;
    this.player.recompute();
  }
  useItem(id) {
    const def = ITEMS[id];
    if (!def || !def.use) return;
    const p = this.player;
    const u = def.use;
    if (u.hp) p.heal(this, u.hp);
    if (u.mp) { p.mp = Math.min(p.maxMp, p.mp + u.mp); this.pt.text(p.x, p.y - 40, '+MP', { col: '#8fd0ff', size: 15 }); }
    if (u.sta) p.sta = Math.min(p.maxSta, p.sta + u.sta);
    if (u.cure) { p.poison = 0; p.slow = 0; }
    if (u.buffAtk) { p.buff.atk = u.buffAtk; p.buff.t = u.buffTime; p.recompute(); }
    if (u.buffDef) { p.buff.def = u.buffDef; p.buff.t = u.buffTime; p.recompute(); }
    if (u.warp) {
      if (this.lastShrine) this.fastTravel(this.lastShrine.x, this.lastShrine.y, '祠');
      else { this.toast('祈った祠がない', '#aaa'); return; }
    }
    this.removeItem(id, 1);
    audio.sfx(u.warp ? 'warp' : 'drink');
  }
  quickItem() {
    const order = ['potion_s', 'potion_m', 'potion_l', 'elixir', 'meat', 'bread'];
    for (const id of order) {
      const n = this.countItem(id);
      if (n > 0) return { id, n };
    }
    return null;
  }
  collect(payload) {
    if (payload.gold) {
      this.player.gold += payload.gold;
      this.pt.text(this.player.x, this.player.y - 34, `+${payload.gold}G`, { col: '#ffd76a', size: 15 });
      audio.sfx('gold', { vol: 0.6 });
    } else if (payload.equip) {
      this.giveEquip(payload.equip);
      audio.sfx('pickup');
    } else {
      this.giveItem(payload.id, payload.n || 1);
      audio.sfx('pickup', { vol: 0.7 });
    }
  }
  buyPrice(item) { return Math.max(1, Math.round((item.value || 10) * 1.25)); }
  sellPrice(item) { return Math.max(1, Math.floor((item.value || 10) * 0.4)); }
  buyItem(stockEntry) {
    if (!stockEntry) return;
    const item = stockEntry.equip || ITEMS[stockEntry.id];
    const price = this.buyPrice(item);
    if (this.player.gold < price) { this.toast('お金が足りない', '#e0524a'); audio.sfx('error'); return; }
    this.player.gold -= price;
    if (stockEntry.equip) {
      this.giveEquip(stockEntry.equip);
      const list = this.menus.data.stock;
      const i = list.indexOf(stockEntry);
      if (i >= 0) list.splice(i, 1);
      this.menus.sel = null;
    } else {
      this.giveItem(stockEntry.id, 1);
    }
    audio.sfx('gold');
  }
  sellItem(entry) {
    if (!entry) return;
    const it = entry.item || ITEMS[entry.id];
    const price = this.sellPrice(it);
    this.player.gold += price;
    if (entry.item) this.dropItem(entry);
    else this.removeItem(entry.id, 1);
    audio.sfx('gold');
    this.toast(`+${price}G`, '#ffd76a');
  }
  craft(recipe) {
    for (const k in recipe.in) if (this.countItem(k) < recipe.in[k]) return;
    for (const k in recipe.in) this.removeItem(k, recipe.in[k]);
    this.giveItem(recipe.out, recipe.n);
    audio.sfx('craft');
  }
  upgrade(item) {
    const cost = upgradeCost(item);
    if (this.player.gold < cost.gold) { this.toast('お金が足りない', '#e0524a'); return; }
    for (const k in cost.mats) if (this.countItem(k) < cost.mats[k]) { this.toast('素材が足りない', '#e0524a'); return; }
    this.player.gold -= cost.gold;
    for (const k in cost.mats) this.removeItem(k, cost.mats[k]);
    item.plus = (item.plus || 0) + 1;
    this.player.recompute();
    audio.sfx('craft');
    this.toast(`${displayName(item)} に強化した！`, '#ffd76a');
    FX.levelUp(this.pt, this.player.x, this.player.y);
  }
  innCost() { return 10 + this.player.level * 6; }

  // ————————————————————————————————————————————————— interaction

  findInteractable() {
    const p = this.player;
    let best = null, bd = 46;
    for (const n of this.npcs) {
      const d = dist(p.x, p.y, n.x, n.y);
      if (d < bd) { bd = d; best = { kind: 'npc', npc: n, label: `${n.name}（${n.roleLabel}）と話す` }; }
    }
    for (const o of this.objects) {
      const d = dist(p.x, p.y, o.x, o.y);
      if (d < bd + 8) {
        if (o.kind === 'chest' && !o.opened) { bd = d; best = { kind: 'chest', obj: o, label: '宝箱を開ける' }; }
        else if (o.kind === 'campfire') { bd = d; best = { kind: 'fire', obj: o, label: '焚き火で調理する' }; }
      }
    }
    // POIs (shrine / cave)
    if (!this.interior) {
      for (const poi of this.world.pois) {
        const d = dist(p.x, p.y, poi.x, poi.y);
        if (d > 70 || d > bd + 24) continue;
        if (poi.kind === 'shrine') { bd = d; best = { kind: 'shrine', poi, label: poi.activated ? '祠に祈る' : '祠を目覚めさせる' }; }
        else if (poi.kind === 'dungeon' || poi.kind === 'lair') { bd = d; best = { kind: 'cave', poi, label: `${poi.name} に入る` }; }
      }
    } else {
      // exit stairs
      const tx = Math.floor(p.x / TILE), ty = Math.floor(p.y / TILE);
      if (this.interior.at(tx, ty) === 3) best = { kind: 'stairs', label: '次の階へ進む' };
      const ex = this.interior.entry;
      if (dist(p.x, p.y, ex.x, ex.y) < 42) best = { kind: 'exitDungeon', label: '外へ出る' };
    }
    // harvestables
    const strike = (pr) => {
      if (!pr.harvest) return;
      if (this.runtime.isHarvested(pr, this.time)) return;
      const d = dist(p.x, p.y, pr.x, pr.y);
      if (d < bd) { bd = d; best = { kind: 'harvest', prop: pr, label: pr.harvest === 'ore' || pr.harvest === 'crystal' ? '採掘する' : '採取する' }; }
    };
    if (this.interior) this.interior.props.forEach(strike);
    else this.runtime.forEachPropNear(p.x, p.y, 60, strike);
    return best;
  }

  interact() {
    const t = this.interactTarget;
    if (!t) return;
    switch (t.kind) {
      case 'npc': this.talkTo(t.npc); break;
      case 'harvest': this.harvestProp(t.prop); break;
      case 'chest': this.openChest(t.obj); break;
      case 'fire': this.menus.open('craft', { station: 'fire' }); break;
      case 'shrine': this.useShrine(t.poi); break;
      case 'cave': this.enterDungeon(t.poi); break;
      case 'stairs': this.nextFloor(); break;
      case 'exitDungeon': this.exitDungeon(); break;
    }
  }

  talkTo(npc) {
    npc.talkT = 3;
    this.quests.onTalk(npc);
    this.menus.open('dialogue', { npc, text: greeting(npc, this), options: dlgOptions(npc, this) });
  }

  dialogueAction(npc, o) {
    const m = this.menus;
    switch (o.act) {
      case 'close': m.close(); break;
      case 'rumor': m.data.text = rumor(this); m.data.options = dlgOptions(npc, this); break;
      case 'shop': {
        const rng = makeRNG(npc.shopSeed + Math.floor(this.day / 2));
        const lvl = Math.max(1, Math.round(regionLevel(this.world, npc.x, npc.y) * 0.6 + this.player.level * 0.6));
        m.open('shop', { stock: shopStock(rng, o.shop, lvl), shopName: `${npc.name}の${npc.roleLabel}` });
        break;
      }
      case 'craft': m.open('craft', { station: o.station }); break;
      case 'upgrade': m.open('upgrade'); break;
      case 'board': {
        const s = npc.settlement;
        const lvl = Math.max(1, Math.round(regionLevel(this.world, npc.x, npc.y) * 0.7 + this.player.level * 0.5));
        if (!s.board || s.boardDay !== this.day) {
          s.board = this.quests.generateBoard(s, lvl, s.boardSeed + this.day * 31);
          s.boardDay = this.day;
        }
        m.open('board', { board: s.board });
        break;
      }
      case 'rest': {
        const cost = this.innCost();
        if (this.player.gold < cost) { this.toast('お金が足りない', '#e0524a'); audio.sfx('error'); break; }
        this.player.gold -= cost;
        this.rest();
        m.closeAll();
        break;
      }
      case 'pray': {
        this.player.hp = this.player.maxHp;
        this.player.mp = this.player.maxMp;
        this.player.poison = 0;
        FX.heal(this.pt, this.player.x, this.player.y);
        audio.sfx('heal');
        this.toast('女神の加護を受けた', '#8fe8a8');
        m.closeAll();
        break;
      }
      case 'turnin': {
        this.quests.turnIn(o.quest);
        m.data.text = 'よくやってくれた。これは礼だ。';
        m.data.options = dlgOptions(npc, this);
        break;
      }
    }
  }

  rest() {
    const p = this.player;
    p.hp = p.maxHp; p.mp = p.maxMp; p.sta = p.maxSta; p.poison = 0;
    // advance to morning
    const h = this.hour();
    const toMorning = ((30 - h) % 24) * (DAY_LENGTH / 24);
    this.time += Math.max(DAY_LENGTH * 0.2, toMorning);
    this.fadeT = 1.2;
    this.toast('ぐっすり眠った', '#8fe8a8');
    audio.sfx('heal');
    this.enemies.length = 0;
    this.save(true);
  }

  openChest(o) {
    o.opened = true;
    audio.sfx('chest');
    const rng = makeRNG((o.x * 31 + o.y * 17) | 0);
    const lvl = o.level || regionLevel(this.world, o.x, o.y);
    const gold = rng.irange(20, 60) * (1 + Math.floor(lvl / 3));
    this.drops.push(new Drop(o.x, o.y - 10, { gold }));
    const n = o.big ? 3 : 1 + rng.int(2);
    for (let i = 0; i < n; i++) {
      if (rng.chance(0.55)) this.drops.push(new Drop(o.x, o.y - 10, { equip: rollEquip(rng, lvl, o.big ? 2 + rng.int(2) : null) }));
      else this.drops.push(new Drop(o.x, o.y - 10, { id: rng.pick(['potion_m', 'ether_m', 'ore_iron', 'ore_silver', 'herb', 'bloom', 'core']), n: rng.irange(1, 3) }));
    }
    this.pt.burst(o.x, o.y - 10, 26, { speed: 90, life: 0.8, r: 3, col: '#ffd76a', glow: 1, g: 120, vz: 80, z: 8 });
  }

  spawnChest(x, y, level, big) {
    this.objects.push({ kind: 'chest', sprite: 'chest', x, y, opened: false, level, big, r: 12, solid: false, id: 'c' + (x | 0) + '_' + (y | 0) });
  }

  useShrine(poi) {
    if (!poi.activated) {
      poi.activated = true;
      poi.discovered = true;
      this.quests.onShrine();
      this.showBanner(poi.name, '祠が目覚めた。ここへ転移できる。');
      this.pt.ring(poi.x, poi.y, { r0: 10, r1: 200, life: 1.2, col: 'rgba(140,230,255,0.8)', w: 6 });
      audio.sfx('warp');
      this.player.addXp(60, this);
    }
    this.lastShrine = { x: poi.x, y: poi.y + 30 };
    const p = this.player;
    p.hp = p.maxHp; p.mp = p.maxMp; p.sta = p.maxSta; p.poison = 0;
    FX.heal(this.pt, p.x, p.y);
    this.toast('祠の加護で回復した', '#8fe8a8');
    this.save(true);
  }

  fastTravel(x, y, name) {
    this.fadeT = 1.4;
    this.interior = null;
    this.enemies.length = 0;
    this.player.x = x; this.player.y = y;
    this.renderer.cam.x = x; this.renderer.cam.y = y;
    audio.sfx('warp');
    this.locationBanner = { name, sub: '転移した', t: 0 };
    this.save(true);
  }

  enterDungeon(poi) {
    this.outsidePos = { x: this.player.x, y: this.player.y + 40 };
    this.dungeonPoi = poi;
    this.dungeonFloor = 0;
    this.loadFloor();
    this.showBanner(poi.name, `推奨レベル ${poi.level}`);
  }

  loadFloor() {
    const poi = this.dungeonPoi;
    const d = new Dungeon(poi, this.dungeonFloor, poi.seed);
    this.interior = d;
    this.enemies.length = 0;
    this.drops.length = 0;
    this.projectiles.length = 0;
    this.objects = d.chests.map((c) => ({ ...c, r: 12, solid: false }));
    this.player.x = d.entry.x;
    this.player.y = d.entry.y;
    this.renderer.cam.x = this.player.x;
    this.renderer.cam.y = this.player.y;
    this.fadeT = 1.2;
    audio.setMood(d.boss ? 'boss' : 'dungeon');
    // spawn enemies
    const rng = makeRNG((poi.seed ^ (this.dungeonFloor * 977)) >>> 0);
    for (const s of d.spawns) {
      if (s.boss && d.boss) {
        // Cleared bosses stay defeated; each relic must come from a new delve.
        if (poi.cleared) continue;
        const kind = poi.boss ? 'dragon' : 'troll';
        const e = new Enemy(kind, s.x, s.y, d.level + 2);
        e.boss = true;
        this.enemies.push(e);
        this.activeBoss = e;
      } else {
        const pool = d.themeKey === 'crypt' ? ['skeleton', 'ghost', 'wraith']
          : d.themeKey === 'ice' ? ['wolf', 'golem', 'wraith']
          : d.themeKey === 'lair' ? ['imp', 'drake', 'golem']
          : d.themeKey === 'ruin' ? ['skeleton', 'spider', 'bandit']
          : ['bat', 'spider', 'slime', 'golem'];
        const kind = rng.pick(pool);
        this.enemies.push(new Enemy(kind, s.x, s.y, clamp(d.level + rng.irange(-1, 1), 1, 30)));
      }
    }
  }

  nextFloor() {
    if (this.dungeonFloor + 1 < this.dungeonPoi.floors) {
      this.dungeonFloor++;
      this.loadFloor();
      this.toast(`${this.dungeonFloor + 1}層へ降りた`, '#c58cff');
    } else this.exitDungeon();
  }

  exitDungeon() {
    this.interior = null;
    this.enemies.length = 0;
    this.objects.length = 0;
    this.activeBoss = null;
    this.player.x = this.outsidePos.x;
    this.player.y = this.outsidePos.y;
    this.renderer.cam.x = this.player.x;
    this.renderer.cam.y = this.player.y;
    this.fadeT = 1.2;
    audio.setMood('explore');
  }

  // ————————————————————————————————————————————————— spawn director

  updateSpawns(dt) {
    if (this.interior) return;
    const p = this.player;
    // despawn far enemies
    for (let i = this.enemies.length - 1; i >= 0; i--) {
      const e = this.enemies[i];
      if (!e.alive) { this.enemies.splice(i, 1); continue; }
      if (!e.boss && dist2(e.x, e.y, p.x, p.y) > 1500 * 1500) this.enemies.splice(i, 1);
    }
    // POI content streaming
    for (const poi of this.world.pois) {
      const d = dist(p.x, p.y, poi.x, poi.y);
      if (d < 260 && !poi.discovered) {
        poi.discovered = true;
        this.quests.onDiscover(poi);
        const names = { shrine: poi.name || '祠', dungeon: poi.name, camp: '野盗の野営地', ruin: '古代の遺跡', lair: poi.name };
        this.locationBanner = { name: names[poi.kind] || '未知の場所', sub: '発見した', t: 0 };
        this.player.addXp(25, this);
        audio.sfx('quest', { vol: 0.5 });
      }
      if (d < 900 && !this.poiObjects.has(poi.id)) this.buildPOI(poi);
      else if (d > 1600 && this.poiObjects.has(poi.id)) {
        const objs = this.poiObjects.get(poi.id);
        this.objects = this.objects.filter((o) => !objs.includes(o));
        this.poiObjects.delete(poi.id);
      }
    }

    // ambient spawns
    this.spawnTimer -= dt;
    const cap = this.isNight() ? 26 : 20;
    if (this.spawnTimer <= 0 && this.enemies.length < cap) {
      this.spawnTimer = 0.6;
      const rng = makeRNG((this.frame * 2654435761) | 0);
      for (let attempt = 0; attempt < 6; attempt++) {
        const a = rng() * TAU;
        const r = rng.range(460, 820);
        const x = p.x + Math.cos(a) * r, y = p.y + Math.sin(a) * r;
        const tx = Math.floor(x / TILE), ty = Math.floor(y / TILE);
        if (!this.world.inBounds(tx, ty)) continue;
        if (this.world.isWaterTile(tx, ty) || this.world.isSolidTile(tx, ty)) continue;
        const ov = this.world.overlayAt(tx, ty);
        if (ov === 4 || ov === 6 || ov === 3) continue;
        let inTown = false;
        for (const s of this.world.settlements) if (dist(tx, ty, s.x, s.y) < s.size + 12) { inTown = true; break; }
        if (inTown) continue;
        const lvl = regionLevel(this.world, x, y);
        const kind = pickSpawnKind(this.world, tx, ty, this.isNight(), rng, lvl);
        if (!kind) continue;
        const packSize = kind === 'wolf' ? rng.irange(2, 4) : rng.chance(0.3) ? 2 : 1;
        for (let k = 0; k < packSize && this.enemies.length < cap; k++) {
          const ex = x + (rng() - 0.5) * 60, ey = y + (rng() - 0.5) * 60;
          if (this.blocked(ex, ey, 10)) continue;
          this.enemies.push(new Enemy(kind, ex, ey, clamp(lvl + rng.irange(-1, 1), 1, 30)));
        }
        break;
      }
    }
  }

  buildPOI(poi) {
    const objs = [];
    const rng = makeRNG(poi.seed || ((poi.tx * 7919 + poi.ty) | 0));
    if (poi.kind === 'camp') {
      const st = this.campState.get(poi.id) || { alive: 0, cleared: poi.cleared, x: poi.x, y: poi.y };
      objs.push({ kind: 'prop', sprite: 'campfire', x: poi.x, y: poi.y, r: 10, solid: false, light: true, fire: true });
      for (let i = 0; i < 3; i++) {
        const a = (i / 3) * TAU + rng();
        objs.push({ kind: 'prop', sprite: 'tent', x: poi.x + Math.cos(a) * 70, y: poi.y + Math.sin(a) * 70, r: 24, solid: true });
      }
      for (let i = 0; i < 2; i++) {
        const a = rng() * TAU;
        objs.push({ kind: 'prop', sprite: 'barrel', x: poi.x + Math.cos(a) * 46, y: poi.y + Math.sin(a) * 46, r: 11, solid: true });
      }
      if (!st.cleared) {
        const lvl = regionLevel(this.world, poi.x, poi.y);
        const n = 4 + rng.int(3);
        st.alive = n;
        for (let i = 0; i < n; i++) {
          const a = rng() * TAU, r = rng.range(30, 110);
          const e = new Enemy(rng.chance(0.3) ? 'archer' : 'bandit', poi.x + Math.cos(a) * r, poi.y + Math.sin(a) * r, clamp(lvl, 1, 30));
          e.campId = poi.id;
          this.enemies.push(e);
        }
      } else if (!st.chest) {
        st.chest = true;
      }
      this.campState.set(poi.id, st);
    } else if (poi.kind === 'ruin') {
      const n = 4 + rng.int(5);
      for (let i = 0; i < n; i++) {
        const a = rng() * TAU, r = rng.range(30, 120);
        objs.push({
          kind: 'prop', sprite: rng.chance(0.6) ? 'ruinPillar' : 'ruinWall',
          x: poi.x + Math.cos(a) * r, y: poi.y + Math.sin(a) * r, r: 14, solid: true, variant: rng.int(6),
        });
      }
      if (!poi.looted) {
        objs.push({
          kind: 'chest', sprite: 'chest', x: poi.x, y: poi.y, opened: false,
          level: regionLevel(this.world, poi.x, poi.y), r: 12, solid: false, id: poi.id + '_chest',
        });
      }
      const lvl = regionLevel(this.world, poi.x, poi.y);
      if (dist(this.player.x, this.player.y, poi.x, poi.y) > 300) {
        for (let i = 0; i < 2 + rng.int(2); i++) {
          const a = rng() * TAU, r = rng.range(40, 140);
          this.enemies.push(new Enemy(rng.pick(['skeleton', 'ghost', 'spider']), poi.x + Math.cos(a) * r, poi.y + Math.sin(a) * r, clamp(lvl, 1, 30)));
        }
      }
    }
    for (const o of objs) this.objects.push(o);
    this.poiObjects.set(poi.id, objs);
  }

  // ————————————————————————————————————————————————— feedback helpers

  toast(text, col) { this.hud.toast(text, col); }
  showBanner(title, sub) { this.hud.showBanner(title, sub); }
  shake(power, time) { if (!this.settings.reduceMotion) this.renderer.cam.shake(power, time); }
  hitStop(t) { this.hitStopT = Math.max(this.hitStopT, this.settings.reduceMotion ? Math.min(t, 0.035) : t); }
  vibrate(ms) { if (this.settings.vibrate && typeof navigator !== 'undefined' && navigator.vibrate) navigator.vibrate(ms); }

  checkAchievements() {
    let changed = false;
    for (const def of ACHIEVEMENTS) {
      if (this.achievements.has(def.id) || !achievementProgress(def, this).complete) continue;
      this.achievements.add(def.id);
      changed = true;
      this.toast(`実績解除「${def.name}」`, '#ffe08a');
      this.pt.ring(this.player.x, this.player.y, { r0: 8, r1: 72, life: 0.55, col: 'rgba(255,225,130,0.9)', w: 5, squash: 0.55 });
      audio.sfx('quest', { vol: 0.7 });
    }
    if (changed) this.save(true);
  }

  collectLights(add, view) {
    if (this.interior) { this.interior.collectLights(add, view, this); return; }
    const night = this.isNight();
    // town lamps & windows
    for (const s of this.world.settlements) {
      const sx = (s.x + 0.5) * TILE, sy = (s.y + 0.5) * TILE;
      if (Math.abs(sx - this.player.x) > 1800 || Math.abs(sy - this.player.y) > 1800) continue;
      if (night) {
        for (const p of s.props) {
          if (p.kind !== 'lamp') continue;
          const fl = 0.9 + Math.sin(this.time * 6 + p.x) * 0.06;
          add(p.x, p.y - 60, 165 * fl, [255, 200, 120], 1.0);
        }
        for (const b of s.buildings) {
          add((b.tx + b.tw / 2) * TILE, (b.ty + b.th - 0.4) * TILE, 90, [255, 190, 110], 0.55);
        }
      }
    }
    for (const o of this.objects) {
      if (!o.light) continue;
      const fl = 0.86 + Math.sin(this.time * 9 + o.x) * 0.09;
      add(o.x, o.y - 8, 150 * fl, [255, 160, 70], 0.95);
    }
    // shrines
    for (const poi of this.world.pois) {
      if (poi.kind !== 'shrine' || !poi.activated) continue;
      if (Math.abs(poi.x - this.player.x) > 1200 || Math.abs(poi.y - this.player.y) > 1200) continue;
      add(poi.x, poi.y - 50, 150, [120, 220, 255], 0.8);
    }
    // spells & projectiles
    for (const pr of this.projectiles) {
      if (pr.kind === 'fireball' || pr.kind === 'fire') add(pr.x, pr.y, 110, [255, 150, 60], 0.9);
      else if (pr.kind === 'ice') add(pr.x, pr.y, 70, [140, 220, 255], 0.7);
    }
    // volcanic glow
    const tx = Math.floor(this.player.x / TILE), ty = Math.floor(this.player.y / TILE);
    if (this.world.biomeAt(tx, ty) === B.ASH) {
      const fl = 0.8 + Math.sin(this.time * 2) * 0.2;
      add(this.player.x, this.player.y, 500 * fl, [255, 90, 40], 0.25);
    }
  }

  // ————————————————————————————————————————————————— portraits (for menus)

  drawHeroPortrait(ctx, x, y, scale) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    drawHumanoid(ctx, {
      dir: 0, animT: performance.now() / 1000, moving: false,
      pal: this.player.pal, equip: this.player.appearance, scale: 1,
    });
    ctx.restore();
  }
  drawNPCPortrait(ctx, npc, x, y, scale) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    drawHumanoid(ctx, {
      dir: 0, animT: performance.now() / 1000, moving: false,
      pal: npc.pal, equip: npc.equipVis, scale: npc.scale || 1,
    });
    ctx.restore();
  }

  // ————————————————————————————————————————————————— update

  update(dt) {
    this.frame++;
    if (this.state !== 'play') return;
    const menusOpen = this.menus.isOpen;
    this.input.setUIMode(menusOpen);

    if (this.hitStopT > 0) { this.hitStopT -= dt; dt *= 0.12; }
    this.fadeT = Math.max(0, this.fadeT - dt * 1.4);
    this.flashT = Math.max(0, this.flashT - dt * 2.4);

    if (!menusOpen) {
      const prevDay = Math.floor(this.time / DAY_LENGTH);
      this.time += dt;
      if (Math.floor(this.time / DAY_LENGTH) !== prevDay) {
        this.day++;
        this.toast(`${this.day}日目の朝`, '#ffd76a');
      }
      this.updateWeather(dt);
      this.handleInput(dt);
      this.player.update(dt, this);
      for (const e of this.enemies) e.update(dt, this);
      for (const n of this.npcs) {
        if (dist2(n.x, n.y, this.player.x, this.player.y) < 1400 * 1400) n.update(dt, this);
      }
      for (let i = this.projectiles.length - 1; i >= 0; i--) {
        const pr = this.projectiles[i];
        pr.update(dt, this);
        if (pr.dead) this.projectiles.splice(i, 1);
      }
      for (let i = this.drops.length - 1; i >= 0; i--) {
        const d = this.drops[i];
        d.update(dt, this);
        if (d.dead) this.drops.splice(i, 1);
      }
      this.pt.update(dt);
      this.updateSpawns(dt);

      // interaction target
      this.interactTarget = this.findInteractable();
      this.interactPrompt = this.interactTarget ? this.interactTarget.label : null;
      this.nearNPC = this.interactTarget && this.interactTarget.kind === 'npc' ? this.interactTarget.npc : null;

      // boss music / tracking
      if (this.activeBoss && !this.activeBoss.alive) this.activeBoss = null;
      const inCombat = this.enemies.some((e) => e.alive && e.aggroT > 0 && dist2(e.x, e.y, this.player.x, this.player.y) < 400 * 400);
      if (this.activeBoss) audio.setMood('boss');
      else if (inCombat) audio.setMood('combat');
      else if (this.interior) audio.setMood('dungeon');
      else if (this.inSettlement()) audio.setMood('town');
      else if (this.isNight()) audio.setMood('night');
      else audio.setMood('explore');

      // fog of war + location banners
      if (this.frame % 6 === 0 && !this.interior) {
        this.revealFog(this.player.x / TILE, this.player.y / TILE, 26);
        for (const s of this.world.settlements) {
          if (!s.discovered && dist(this.player.x / TILE, this.player.y / TILE, s.x, s.y) < s.size + 16) {
            s.discovered = true;
            this.locationBanner = { name: s.name + s.label, sub: '発見した', t: 0 };
            this.player.addXp(40, this);
            audio.sfx('quest', { vol: 0.6 });
          }
        }
        const b = this.world.biomeAt(Math.floor(this.player.x / TILE), Math.floor(this.player.y / TILE));
        if (b !== this.lastBiome && !this.inSettlement()) {
          this.lastBiome = b;
          if (this.frame > 120) this.locationBanner = { name: BIOME_NAME[b] || '', sub: '', t: 0 };
        }
      }

      // ground quest markers
      this.groundMarkers.length = 0;
      for (const m of this.navigationMarkers()) {
        if (dist2(m.x, m.y, this.player.x, this.player.y) < 420 * 420) this.groundMarkers.push(m);
      }

      if (this.waypoint && !this.interior && dist2(this.waypoint.x, this.waypoint.y, this.player.x, this.player.y) < 44 * 44) {
        this.waypoint = null;
        this.toast('目印に到着しました', '#8fe8a8');
        this.save(true);
      }

      this.updateOnboarding();

      this.achievementCheckT += dt;
      if (this.achievementCheckT >= 0.75) {
        this.achievementCheckT = 0;
        this.checkAchievements();
      }

      this.autosaveT += dt;
      if (this.autosaveT > 20) { this.autosaveT = 0; this.save(true); }
    }

    if (!menusOpen && this.victoryPending > 0) {
      this.victoryPending -= dt;
      if (this.victoryPending <= 0) this.menus.open('victory');
    }

    // camera
    const cam = this.renderer.cam;
    const lead = 26;
    cam.follow(this.player.x + Math.cos(this.player.face) * lead, this.player.y + Math.sin(this.player.face) * lead - 8, dt);
    cam.update(dt);
    audio.update();
  }

  updateOnboarding() {
    const o = this.onboarding;
    if (!o) return;
    if (o.stage === 0 && dist(this.player.x, this.player.y, o.x, o.y) > 44) {
      o.stage = 1;
      this.toast('いい動きだ。次は剣ボタンで攻撃！', '#ffd0a0');
      this.save(true);
    } else if (o.stage === 1 && (this.player.state === 'attack' || this.player.state === 'bow')) {
      o.stage = 2;
      this.toast('次は回転矢印で回避。敵の赤い予告にも有効！', '#bff5ff');
      this.save(true);
    } else if (o.stage === 2 && this.player.state === 'roll') {
      o.stage = 3;
      this.toast('攻撃・完璧回避・パリィでFLOWがたまる', '#ffe08a');
      setTimeout(() => this.toast('★の方角へ進み、ギルド長に話しかけよう', '#8fd0ff'), 1000);
      this.save(true);
    } else if (o.stage === 3 && this.quests.chapter > 0) {
      this.onboarding = null;
      this.toast('冒険の基本を習得した！', '#8fe8a8');
      this.save(true);
    }
  }

  onboardingHint() {
    if (!this.onboarding) return null;
    if (this.onboarding.stage === 0) return `${this.settings.leftHanded ? '右' : '左'}側をドラッグして移動`;
    if (this.onboarding.stage === 1) return `${this.settings.leftHanded ? '左下' : '右下'}の剣ボタンで攻撃`;
    if (this.onboarding.stage === 2) return '回転矢印ボタンで回避';
    return '★の方角へ進み、ギルド長と話す';
  }

  inSettlement() {
    if (this.interior) return false;
    const tx = this.player.x / TILE, ty = this.player.y / TILE;
    for (const s of this.world.settlements) if (dist(tx, ty, s.x, s.y) < s.size + 4) return true;
    return false;
  }

  handleInput(dt) {
    const inp = this.input;
    const p = this.player;
    if (!p.alive) return;

    if (inp.pressed('attack') || inp.keyPressed('j') || inp.gamepadPressed(0)) p.tryAttack(this);
    if (inp.released('attack') && p.state === 'bow') p.releaseBow(this);
    if (!inp.down('attack') && !inp.key('j') && p.state === 'bow' && p.charge > 0.12) p.releaseBow(this);
    if (inp.pressed('dodge') || inp.keyPressed('shift') || inp.gamepadPressed(1)) p.tryRoll(this);
    if (inp.pressed('interact') || inp.keyPressed('e') || inp.gamepadPressed(2)) this.interact();
    if (inp.pressed('item') || inp.keyPressed('q')) {
      const q = this.quickItem();
      if (q) this.useItem(q.id); else { audio.sfx('error'); this.toast('回復アイテムがない', '#aaa'); }
    }
    for (let i = 0; i < 4; i++) {
      if (inp.pressed('spell' + i) || inp.keyPressed(String(i + 1))) p.castSpell(this, i);
    }
    // block (hold)
    const blocking = (inp.down('block') || inp.key('k') || inp.gamepadDown(3)) && p.equip.off;
    if (blocking && (p.state === 'idle' || p.state === 'block')) {
      if (p.state !== 'block') { p.state = 'block'; p.blockT = 0; }
    } else if (p.state === 'block') p.state = 'idle';

    if (inp.pressed('menu') || inp.keyPressed('escape') || inp.keyPressed('tab')) this.menus.open('pause');
    if (inp.pressed('map') || inp.keyPressed('m')) this.menus.open('map');
    if (inp.keyPressed('i')) this.menus.open('inventory');
  }

  // ————————————————————————————————————————————————— render

  render(dt) {
    const r = this.renderer;
    const ctx = r.ctx;
    if (this.state === 'play') {
      r.render(this, dt);
    } else {
      ctx.setTransform(r.dpr, 0, 0, r.dpr, 0, 0);
      ctx.fillStyle = '#0d1526';
      ctx.fillRect(0, 0, r.w, r.h);
    }
    ctx.setTransform(r.dpr, 0, 0, r.dpr, 0, 0);
    this.ui.begin(ctx, this.input, r.w, r.h);
    if (this.state === 'play' && !this.menus.isOpen) {
      this.hud.draw(ctx, this, dt);
      this.hud.registerButtons(this.input, this, this.hud.layout(r.w, r.h, this.safeArea, this.settings));
    } else {
      this.input.setButtons([]);
    }
    this.menus.draw(ctx, this, dt);
    if (this.settings.showFps) {
      this.ui.text(`${Math.round(this.fps)} fps  |  ${this.enemies.length}e ${this.pt.a.length}p`, 8, r.h - 12, { size: 11, col: 'rgba(255,255,255,0.5)' });
    }
  }

  applyQuality() {
    this.renderer.quality = this.settings.hq ? 1 : 0.6;
    this.renderer.lightScale = this.settings.hq ? 0.5 : 0.34;
    this.renderer.resize(this.renderer.w, this.renderer.h, this.renderer.dpr);
    this.saveSettings();
    if (typeof dispatchEvent !== 'undefined' && typeof Event !== 'undefined') {
      dispatchEvent(new Event('aetheria-qualitychange'));
    }
  }

  // ————————————————————————————————————————————————— persistence

  saveSettings() {
    try { localStorage.setItem('aetheria_settings', JSON.stringify(this.settings)); } catch (e) {}
  }
  loadSettings() {
    try {
      const s = JSON.parse(localStorage.getItem('aetheria_settings') || 'null');
      if (s && typeof s === 'object') {
        if (Number.isFinite(s.music)) this.settings.music = clamp(s.music, 0, 1);
        if (Number.isFinite(s.sfx)) this.settings.sfx = clamp(s.sfx, 0, 1);
        if (Number.isFinite(s.controlScale)) this.settings.controlScale = clamp(s.controlScale, 0.82, 1.18);
        if (Number.isFinite(s.controlOpacity)) this.settings.controlOpacity = clamp(s.controlOpacity, 0.45, 1);
        if (typeof s.difficulty === 'string' && DIFFICULTY_PROFILES[s.difficulty]) this.settings.difficulty = s.difficulty;
        for (const k of ['vibrate', 'hq', 'aimAssist', 'showFps', 'leftHanded', 'reduceMotion']) {
          if (typeof s[k] === 'boolean') this.settings[k] = s[k];
        }
      }
    } catch (e) {}
    audio.setMusicVol(this.settings.music);
    audio.setSfxVol(this.settings.sfx);
  }

  validSave(d) {
    if (!d || !Number.isInteger(d.seed) || d.seed < 0 || d.seed > 0xffffffff || !d.player ||
        !Number.isFinite(d.player.x) || !Number.isFinite(d.player.y)) return false;
    const arrays = [d.player.inv, d.player.spellSlots, d.player.knownSpells,
      d.pois, d.settlements, d.camps, d.achievements];
    if (arrays.some((value) => value != null && !Array.isArray(value))) return false;
    if (d.player.inv && !d.player.inv.every((entry) => entry && typeof entry === 'object' &&
      ((entry.item && typeof entry.item === 'object' && typeof entry.item.id === 'string') ||
       (typeof entry.id === 'string' && Number.isFinite(entry.n))))) return false;
    if (d.quests && (typeof d.quests !== 'object' ||
      (d.quests.active != null && !Array.isArray(d.quests.active)) ||
      (d.quests.done != null && !Array.isArray(d.quests.done)))) return false;
    if (d.camps && !d.camps.every((entry) => Array.isArray(entry) && entry.length === 2 &&
      typeof entry[0] === 'string' && entry[1] && typeof entry[1] === 'object')) return false;
    if (d.waypoint != null && (!d.waypoint || !Number.isFinite(d.waypoint.x) || !Number.isFinite(d.waypoint.y))) return false;
    return true;
  }

  exportSaveText() {
    // Export the live snapshot directly while playing. If localStorage is full,
    // reading it after a failed save would silently export stale progress—the
    // exact moment when a portable backup is most valuable.
    const data = this.state === 'play' ? this.createSaveData(false) : this.readSave();
    if (!data) return null;
    return JSON.stringify({
      format: SAVE_EXPORT_FORMAT,
      version: SAVE_EXPORT_VERSION,
      exportedAt: new Date().toISOString(),
      save: data,
    });
  }

  async exportSaveFile() {
    const text = this.exportSaveText();
    if (!text) {
      this.saveTransferStatus = '書き出せるセーブがありません';
      return false;
    }
    const stamp = new Date().toISOString().slice(0, 10);
    const name = `aetheria-${this.seed >>> 0}-${stamp}.json`;
    const blob = new Blob([text], { type: 'application/json' });
    try {
      if (typeof File !== 'undefined' && navigator.share && navigator.canShare) {
        const file = new File([blob], name, { type: blob.type });
        if (navigator.canShare({ files: [file] })) {
          await navigator.share({ title: 'アエテリア戦記 セーブデータ', files: [file] });
          this.saveTransferStatus = 'セーブを書き出しました';
          return true;
        }
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      this.saveTransferStatus = 'セーブを書き出しました';
      return true;
    } catch (error) {
      if (error?.name === 'AbortError') return false;
      this.saveTransferStatus = '書き出しに失敗しました';
      return false;
    }
  }

  importSaveText(text) {
    if (typeof text !== 'string' || text.length === 0 || text.length > MAX_SAVE_TEXT_LENGTH) {
      return { ok: false, reason: 'ファイルのサイズが正しくありません' };
    }
    let payload;
    try { payload = JSON.parse(text); }
    catch (error) { return { ok: false, reason: 'JSONファイルを読み取れません' }; }
    if (payload?.format !== SAVE_EXPORT_FORMAT || payload?.version !== SAVE_EXPORT_VERSION) {
      return { ok: false, reason: 'アエテリア戦記のセーブファイルではありません' };
    }
    const data = payload.save;
    if (!this.validSave(data)) return { ok: false, reason: 'セーブデータが破損しています' };
    const seed = data.seed >>> 0;
    const primaryKey = slotKey(SAVE_KEY, seed);
    const backupKey = slotKey(SAVE_BACKUP_KEY, seed);
    const raw = JSON.stringify(data);
    let previous = null;
    try {
      previous = localStorage.getItem(primaryKey);
      if (previous) {
        try {
          const parsed = JSON.parse(previous);
          if (this.validSave(parsed) && (parsed.seed >>> 0) === seed) localStorage.setItem(backupKey, previous);
        } catch (error) {}
      }
      localStorage.setItem(primaryKey, raw);
      const readBack = JSON.parse(localStorage.getItem(primaryKey) || 'null');
      if (!this.validSave(readBack) || (readBack.seed >>> 0) !== seed) throw new Error('save read-back failed');
      localStorage.setItem(LAST_SEED_KEY, String(seed));
      return { ok: true, seed };
    } catch (error) {
      try {
        if (previous == null) localStorage.removeItem(primaryKey);
        else localStorage.setItem(primaryKey, previous);
      } catch (rollbackError) {}
      return { ok: false, reason: '保存容量が不足しています' };
    }
  }

  requestSaveImport() {
    if (typeof document === 'undefined') return false;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json,application/json';
    input.hidden = true;
    input.addEventListener('change', async () => {
      const file = input.files?.[0];
      if (!file) { input.remove(); return; }
      if (file.size > MAX_SAVE_TEXT_LENGTH) {
        this.saveTransferStatus = 'ファイルが大きすぎます';
        input.remove();
        return;
      }
      try {
        const result = this.importSaveText(await file.text());
        this.saveTransferStatus = result.ok ? '復元しました。再読み込みします' : result.reason;
        const status = document.getElementById('a11y-status');
        if (status) status.textContent = this.saveTransferStatus;
        if (result.ok) {
          setTimeout(() => location.replace(new URL('./', location.href).href), 180);
        }
      } catch (error) {
        this.saveTransferStatus = 'ファイルを読み取れません';
      }
      input.remove();
    }, { once: true });
    document.body.appendChild(input);
    input.click();
    return true;
  }

  readSave() {
    const primaryKey = slotKey(SAVE_KEY, this.seed);
    const backupKey = slotKey(SAVE_BACKUP_KEY, this.seed);
    const candidates = [
      { key: primaryKey, backup: false, legacy: false },
      { key: backupKey, backup: true, legacy: false },
      // Read the old single-slot keys last so existing players migrate without
      // losing progress. They are accepted only when their seed matches.
      { key: SAVE_KEY, backup: false, legacy: true },
      { key: SAVE_BACKUP_KEY, backup: true, legacy: true },
    ];
    for (const candidate of candidates) {
      try {
        const raw = localStorage.getItem(candidate.key);
        if (!raw) continue;
        const data = JSON.parse(raw);
        if (!this.validSave(data) || (data.seed >>> 0) !== (this.seed >>> 0)) continue;
        if (candidate.backup || candidate.legacy) localStorage.setItem(primaryKey, raw);
        if (candidate.backup) {
          this.saveRecovered = true;
        }
        return data;
      } catch (e) {}
    }
    return null;
  }

  hasSave() {
    return !!this.readSave();
  }

  canExportSave() {
    // A live game can always be snapshotted directly, even when localStorage
    // has never accepted a save. Keep the title-screen action gated on an
    // existing durable save because no live player state exists there.
    return this.state === 'play' || this.hasSave();
  }

  saveAndReturnToTitle() {
    // "Save and quit" must never imply success after storage rejected the
    // write. Keep the live session in memory so the player can open Settings
    // and rescue the current snapshot with the portable export.
    if (!this.save()) {
      this.saveTransferStatus = '端末保存に失敗しました。最新進行をファイルへ書き出してください';
      this.toast('終了を取り消しました。設定からセーブを書き出せます', '#e0524a');
      return false;
    }
    this.menus.closeAll();
    this.menus.open('title');
    return true;
  }

  clearCurrentSave() {
    const seed = this.seed >>> 0;
    try {
      localStorage.removeItem(slotKey(SAVE_KEY, seed));
      localStorage.removeItem(slotKey(SAVE_BACKUP_KEY, seed));
      // Remove matching legacy data only. A shared ?seed= link must never
      // erase the player's save from another world.
      for (const key of [SAVE_KEY, SAVE_BACKUP_KEY]) {
        try {
          const data = JSON.parse(localStorage.getItem(key) || 'null');
          if (data && (data.seed >>> 0) === seed) localStorage.removeItem(key);
        } catch (e) {}
      }
    } catch (e) {}
  }

  createSaveData(silent = false) {
    const p = this.player;
    // if we're underground, remember the cave mouth rather than the dungeon coords
    const pos = this.interior && this.outsidePos ? this.outsidePos : { x: p.x, y: p.y };
    // Canvas encoding is synchronous and used to cause a visible hitch on
    // iPhone-class devices every 20-second autosave. Keep saving gameplay at
    // that cadence, but refresh the exploration-mask image at most once per
    // minute (manual saves always capture it immediately).
    const now = Date.now();
    const refreshFog = this.fogCanvas && this.fogDirty &&
      (!this.fogSnapshot || !silent || now - this.fogSnapshotAt >= 60_000);
    if (refreshFog) {
      try {
        this.fogSnapshot = this.fogCanvas.toDataURL('image/webp', 0.5);
        this.fogSnapshotAt = now;
        this.fogDirty = false;
      } catch (e) {}
    }
    const fog = this.fogSnapshot;
    return {
      v: 4, seed: this.seed, time: this.time, day: this.day,
      player: {
        x: pos.x, y: pos.y, level: p.level, xp: p.xp, gold: p.gold, hp: p.hp, mp: p.mp, sta: p.sta,
        inv: p.inv, equip: Object.fromEntries(Object.entries(p.equip).map(([k, v]) => [k, v ? v.id : null])),
        spellSlots: p.spellSlots, knownSpells: p.knownSpells, kills: p.kills, playtime: p.playtime,
        flow: p.flow, flowT: p.flowT, hitChain: p.hitChain, hitChainT: p.hitChainT,
        highestChain: p.highestChain, perfectDodges: p.perfectDodges, parries: p.parries,
      },
      quests: this.quests.save(),
      pois: this.world.pois.map((q) => ({ id: q.id, d: q.discovered, a: !!q.activated, c: !!q.cleared, l: !!q.looted })),
      settlements: this.world.settlements.map((s) => ({ id: s.id, d: s.discovered })),
      lastShrine: this.lastShrine,
      camps: [...this.campState.entries()].map(([k, v]) => [k, { cleared: v.cleared, x: v.x, y: v.y }]),
      onboarding: this.onboarding,
      achievements: [...this.achievements],
      waypoint: this.waypoint,
      fog,
    };
  }

  save(silent = false) {
    if (this.state !== 'play') return;
    const data = this.createSaveData(silent);
    const primaryKey = slotKey(SAVE_KEY, this.seed);
    const backupKey = slotKey(SAVE_BACKUP_KEY, this.seed);
    const raw = JSON.stringify(data);
    try {
      const previous = localStorage.getItem(primaryKey);
      if (previous) {
        try {
          const parsed = JSON.parse(previous);
          if (this.validSave(parsed) && (parsed.seed >>> 0) === (this.seed >>> 0)) {
            // A full storage area can reject a growing backup even though an
            // in-place primary overwrite still fits. Backup rotation is best
            // effort: never let it prevent the newest progress being saved.
            try { localStorage.setItem(backupKey, previous); }
            catch (backupError) { console.warn('save backup rotation failed', backupError); }
          }
        } catch (e) {}
      }
      localStorage.setItem(primaryKey, raw);
      const readBack = JSON.parse(localStorage.getItem(primaryKey) || 'null');
      if (!this.validSave(readBack) || (readBack.seed >>> 0) !== (this.seed >>> 0)) {
        throw new Error('save read-back failed');
      }
      // The world selector is recoverable from the primary slot, so failure to
      // update this small convenience key must not turn a verified save into a
      // reported failure.
      try { localStorage.setItem(LAST_SEED_KEY, String(this.seed >>> 0)); }
      catch (seedError) { console.warn('last seed update failed', seedError); }
      this.saveFailureNotified = false;
      if (!silent) this.toast('セーブしました', '#8fd0ff');
      return true;
    } catch (e) {
      console.warn('save failed', e);
      // Autosaves used to fail only in the console, leaving mobile players to
      // discover the lost progress after a restart. Warn once without spamming
      // the 20-second autosave loop; manual saves always report the failure.
      if (!silent || !this.saveFailureNotified) {
        this.toast('セーブできません。端末の空き容量を確認してください', '#e0524a');
      }
      this.saveFailureNotified = true;
      return false;
    }
  }

  applySave(data) {
    // Loading is a clean session boundary. Recreate every transient system so
    // returning to the title and continuing cannot retain enemies, effects,
    // dungeon state, NPC schedules, or held combat state from the prior run.
    this.player = new Player(this.world.start.x, this.world.start.y);
    this.quests = new QuestLog(this);
    this.runtime = new WorldRuntime(this.world);
    this.enemies.length = 0;
    this.npcs.length = 0;
    this.drops.length = 0;
    this.projectiles.length = 0;
    this.objects.length = 0;
    this.groundMarkers.length = 0;
    this.pt.clear();
    this.poiObjects.clear();
    this.campState.clear();
    this.interior = null;
    this.activeBoss = null;
    this.victoryPending = 0;
    this.spawnTimer = 0;
    this.autosaveT = 0;
    this.hitStopT = 0;
    this.flashT = 0;
    this.fadeT = 1;
    this.ashFall = 0;
    this.lastBiome = -1;
    this.locationBanner = null;
    this.interactTarget = null;
    this.interactPrompt = null;
    this.nearNPC = null;
    this.achievementCheckT = 0;
    this.weather = { rain: 0, snow: 0, fog: 0, wind: 0.4, target: { rain: 0, snow: 0, fog: 0 }, timer: 40 };
    this.input.cancelActive();
    for (const poi of this.world.pois) { poi.discovered = false; poi.activated = false; poi.cleared = false; poi.looted = false; }
    for (const st of this.world.settlements) { st.discovered = false; st.board = null; st.boardDay = -1; }
    this.spawnSettlementNPCs();
    if (this.fogCtx && this.fogCanvas) {
      this.fogCtx.globalCompositeOperation = 'source-over';
      this.fogCtx.fillStyle = 'rgba(8,9,12,1)';
      this.fogCtx.fillRect(0, 0, this.fogCanvas.width, this.fogCanvas.height);
      this.fogCtx.globalCompositeOperation = 'destination-out';
    }
    const p = this.player;
    this.time = Number.isFinite(data.time) ? data.time : 0;
    this.day = Math.max(1, data.day || 1);
    const d = data.player || {};
    p.x = Number.isFinite(d.x) ? d.x : this.world.start.x;
    p.y = Number.isFinite(d.y) ? d.y : this.world.start.y;
    p.level = Math.max(1, d.level || 1);
    p.xp = Math.max(0, d.xp || 0);
    p.gold = Math.max(0, d.gold || 0);
    p.kills = d.kills || 0; p.playtime = d.playtime || 0;
    p.flow = clamp(Number.isFinite(d.flow) ? d.flow : 0, 0, 100);
    p.flowT = clamp(Number.isFinite(d.flowT) ? d.flowT : 0, 0, 10);
    p.hitChain = Math.max(0, d.hitChain || 0);
    p.hitChainT = clamp(Number.isFinite(d.hitChainT) ? d.hitChainT : 0, 0, 2.2);
    p.highestChain = Math.max(p.hitChain, d.highestChain || 0);
    p.perfectDodges = Math.max(0, d.perfectDodges || 0);
    p.parries = Math.max(0, d.parries || 0);
    p.inv = (d.inv || []).map((e) => (e.item ? { item: e.item, n: 1 } : { id: e.id, n: e.n }));
    let maxUid = 0;
    for (const e of p.inv) {
      if (!e.item || !e.item.id) continue;
      const n = parseInt(String(e.item.id).replace(/^[a-z]+/, ''), 10);
      if (n > maxUid) maxUid = n;
    }
    setUid(maxUid + 1);
    p.knownSpells = Array.isArray(d.knownSpells) ? d.knownSpells : ['fire'];
    p.spellSlots = Array.isArray(d.spellSlots) ? d.spellSlots.slice(0, 4) : ['fire', null, null, null];
    while (p.spellSlots.length < 4) p.spellSlots.push(null);
    p.spellSlots = p.spellSlots.map((id) => id && p.knownSpells.includes(id) ? id : null);
    const equipped = d.equip || {};
    for (const slot in p.equip) {
      const id = equipped[slot];
      p.equip[slot] = id ? (p.inv.find((e) => e.item && e.item.id === id) || {}).item || null : null;
    }
    p.recompute();
    const wasDefeated = Number.isFinite(d.hp) && d.hp <= 0;
    p.hp = wasDefeated ? p.maxHp * 0.6 : clamp(Number.isFinite(d.hp) ? d.hp : p.maxHp, 1, p.maxHp);
    p.mp = clamp(Number.isFinite(d.mp) ? d.mp : p.maxMp, 0, p.maxMp);
    p.sta = clamp(Number.isFinite(d.sta) ? d.sta : p.maxSta, 0, p.maxSta);
    this.quests.load(data.quests);
    for (const s of data.pois || []) {
      const poi = this.world.pois.find((q) => q.id === s.id);
      if (poi) { poi.discovered = s.d; poi.activated = s.a; poi.cleared = s.c; poi.looted = s.l; }
    }
    for (const s of data.settlements || []) {
      const st = this.world.settlements.find((q) => q.id === s.id);
      if (st) st.discovered = s.d;
    }
    this.lastShrine = data.lastShrine || null;
    if (wasDefeated) {
      const safe = this.lastShrine || this.world.start;
      p.x = safe.x;
      p.y = safe.y;
      p.state = 'idle';
      p.alive = true;
    }
    this.campState = new Map((data.camps || []).map(([k, v]) => [k, { ...v, alive: 0 }]));
    this.onboarding = data.onboarding && Number.isFinite(data.onboarding.stage)
      ? { ...data.onboarding, x: data.onboarding.x ?? p.x, y: data.onboarding.y ?? p.y }
      : null;
    this.achievements = new Set((data.achievements || []).filter((id) => ACHIEVEMENTS.some((a) => a.id === id)));
    this.waypoint = data.waypoint && Number.isFinite(data.waypoint.x) && Number.isFinite(data.waypoint.y)
      ? {
        x: clamp(data.waypoint.x, TILE * 0.5, (this.world.w - 0.5) * TILE),
        y: clamp(data.waypoint.y, TILE * 0.5, (this.world.h - 0.5) * TILE),
      }
      : null;
    if (data.fog && this.fogCanvas) {
      this.fogSnapshot = data.fog;
      this.fogSnapshotAt = Date.now();
      this.fogDirty = false;
      const img = new Image();
      img.onload = () => {
        this.fogCtx.globalCompositeOperation = 'source-over';
        this.fogCtx.clearRect(0, 0, this.fogCanvas.width, this.fogCanvas.height);
        this.fogCtx.drawImage(img, 0, 0);
        this.fogCtx.globalCompositeOperation = 'destination-out';
      };
      img.src = data.fog;
    } else {
      this.fogSnapshot = null;
      this.fogSnapshotAt = 0;
      this.fogDirty = true;
    }
    this.renderer.cam.x = p.x;
    this.renderer.cam.y = p.y;
  }

  loadGame() {
    try {
      const data = this.readSave();
      if (!data) return false;
      if ((data.seed >>> 0) !== (this.seed >>> 0)) {
        // regenerate the world for the saved seed — handled by main.js
        this.pendingLoad = data;
        return 'reseed';
      }
      this.applySave(data);
      this.state = 'play';
      this.audio.init();
      if (this.saveRecovered) {
        this.toast('バックアップからセーブを復旧しました', '#8fe8a8');
        this.saveRecovered = false;
      }
      this.save(true); // upgrade legacy saves to the current schema
      return true;
    } catch (e) {
      console.warn(e);
      return false;
    }
  }
}
