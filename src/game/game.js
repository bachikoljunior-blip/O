// ゲーム本体：ループ・エンティティ管理・スポーン・インタラクション・セーブ

import { initGL } from '../core/gl.js';
import { Input } from '../core/input.js';
import { AudioEngine } from '../core/audio.js';
import { hash2, makeRng } from '../core/noise.js';
import { clamp, clamp01, lerp, v3, TAU } from '../core/math.js';

import { WorldGen, WORLD_RADIUS } from '../world/worldgen.js';
import { Terrain, CHUNK } from '../world/terrain.js';
import { generatePOIs } from '../world/pois.js';
import { Blockers } from '../world/blockers.js';
import { Sky } from '../world/sky.js';
import { Dungeon } from '../world/dungeon.js';

import { Renderer } from '../render/renderer.js';
import { Camera } from '../render/camera.js';
import { FX } from '../render/fx.js';
import { writeInstance } from '../render/instance.js';

import { Player } from './player.js';
import { Enemy, spawnEnemy, spawnBoss } from './enemies.js';
import { NPC, populateVillage } from './npc.js';
import { QuestLog } from './quests.js';
import { Mount } from './mount.js';
import { TEAM } from './actor.js';
import {
  SPAWN_TABLE, CHEST_TABLE, WEAPONS, ARMORS, SHIELDS, TALISMANS, SPELLS, ITEMS,
} from './data.js';

export const QUALITY_PRESETS = {
  low: {
    name: '軽量', renderScale: 0.62, viewChunks: 6, lodScale: 0.7, shadows: true, shadowSize: 1024,
    shadowRange: 42, grassDensity: 0.55, grassDist: 60, grassShadow: false, treeDensity: 0.6,
    bloom: false, bloomPasses: 1, bloomStrength: 0.5, water: true, skyQuality: 0, maxEnemies: 12,
    cloudShadows: false, godRays: false, rayStrength: 0, maxLights: 4,
  },
  medium: {
    name: '標準', renderScale: 0.82, viewChunks: 8, lodScale: 1.0, shadows: true, shadowSize: 1536,
    shadowRange: 58, grassDensity: 0.85, grassDist: 84, grassShadow: false, treeDensity: 0.85,
    bloom: true, bloomPasses: 2, bloomStrength: 0.55, water: true, skyQuality: 1, maxEnemies: 18,
    cloudShadows: true, godRays: true, rayStrength: 0.55, maxLights: 6,
  },
  high: {
    name: '高品質', renderScale: 1.0, viewChunks: 11, lodScale: 1.3, shadows: true, shadowSize: 2048,
    shadowRange: 78, grassDensity: 1.15, grassDist: 108, grassShadow: true, treeDensity: 1.0,
    bloom: true, bloomPasses: 3, bloomStrength: 0.6, water: true, skyQuality: 1, maxEnemies: 26,
    cloudShadows: true, godRays: true, rayStrength: 0.7, maxLights: 8,
  },
};

class Time {
  constructor() {
    this.now = 0; this.dt = 0; this.raw = 0;
    this.stop = 0; this.scale = 1;
    this.frames = 0; this.fps = 60; this._acc = 0; this._n = 0;
  }
  hitstop(s) { this.stop = Math.max(this.stop, s); }
  step(rawDt) {
    this.raw = rawDt;
    this._acc += rawDt; this._n++;
    if (this._acc > 0.4) { this.fps = this._n / this._acc; this._acc = 0; this._n = 0; }
    let dt = rawDt * this.scale;
    if (this.stop > 0) {
      const use = Math.min(this.stop, rawDt);
      this.stop -= use;
      dt *= 0.06;
    }
    this.dt = Math.min(dt, 0.05);
    this.now += this.dt;
    this.frames++;
    return this.dt;
  }
}

const _proj = [0, 0, 0];

export class Game {
  constructor(canvas, ui) {
    this.canvas = canvas;
    this.ui = ui;
    this.gl = initGL(canvas);
    if (!this.gl) throw new Error('WebGL2 not supported');
    this.quality = { ...QUALITY_PRESETS.medium };
    this.time = new Time();
    this.input = new Input(canvas);
    this.audio = new AudioEngine();
    this.paused = false;
    this.running = false;
    this.worldRadius = WORLD_RADIUS - 20;
    this.exposureMul = 1;
    /** 水面の高さ。アクターの水深判定と描画で共有する */
    this.seaLevel = 0;
  }

  async init(opts = {}) {
    const seed = opts.seed || 20260802;
    this.seed = seed;
    this.world = new WorldGen(seed);
    this.ui.progress('大地を編んでいます…', 0.1);
    await frame();

    this.pois = generatePOIs(this.world);
    this.ui.progress('拠点と街道を敷いています…', 0.35);
    await frame();

    this.terrain = new Terrain(this.gl, this.world, this.quality);
    this.sky = new Sky(seed + 3);
    this.fx = new FX(this.gl);
    this.camera = new Camera();
    this.renderer = new Renderer(this.gl, this.quality);
    // 建物の当たり判定はメッシュの寸法から作るので、描画側の初期化の後
    this.blockers = new Blockers().build(this.pois);
    this.ui.progress('世界を描き出しています…', 0.6);
    await frame();

    this.player = new Player({ x: 40, z: 300 });
    this.player.y = this.world.height(40, 300);
    this.quests = new QuestLog();

    this.enemies = [];
    this.npcs = [];
    this.projectiles = [];
    this.gatherables = new Map();
    this.harvested = new Set();
    this.openedChests = new Set();
    this.clearedPOIs = new Set();
    this.dungeonProgress = new Map();   // POI id -> 踏破した最深層
    this.deepestDepth = 0;
    this.activeBoss = null;
    this.bossPOI = null;
    this.dungeon = null;
    this.lightPos = new Float32Array(8 * 4);
    this.lightCol = new Float32Array(8 * 4);
    this._lights = { count: 0, pos: this.lightPos, col: this.lightCol };
    this._lightCand = [];
    this.unlocked = { weapons: new Set(['broken_sword']), armors: new Set(['rags', 'hood']), shields: new Set(['wooden_shield']), talismans: new Set(), spells: new Set() };

    // 村に NPC を配置
    for (const p of this.pois) {
      if (p.type === 'village') this.npcs.push(...populateVillage(p, this.world));
      else if (p.type === 'hermit') this.npcs.push(...populateVillage(p, this.world));
    }
    // 相棒の馬
    this.mount = new Mount({ x: this.player.x + 5, z: this.player.z + 4 });
    this.mount.y = this.world.height(this.mount.x, this.mount.z);

    // 開始地点の篝火
    const start = this.pois.find((p) => p.tag === 'start');
    if (start) { this.player.lastShrine = start; start.discovered = true; this.player.discovered.add(start.id); }

    // 全図をあらかじめ焼いておく（ミニマップが最初から使えるように）
    for (let i = 0; i < 8; i++) {
      this.ui.bakeMap(this, 24);
      this.ui.progress('地図を写しています…', 0.7 + i * 0.02);
      await frame();
    }

    this.ui.progress('残響が満ちるのを待っています…', 0.88);
    await frame();

    this.mapBake = { canvas: null, row: 0, size: 176, done: false };
    this.visited = new Set();

    this.resize();
    addEventListener('resize', () => this.resize());
    this.ui.bind(this);
    this.ui.progress('', 1);

    this.spawnTimer = 0;
    this.lastSave = 0;
    return this;
  }

  resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2.4);
    const w = Math.round(innerWidth * dpr);
    const h = Math.round(innerHeight * dpr);
    this.canvas.width = w;
    this.canvas.height = h;
    this.canvas.style.width = innerWidth + 'px';
    this.canvas.style.height = innerHeight + 'px';
    this.camera.resize(w, h);
    this.renderer.resize(w, h);
  }

  setQuality(preset) {
    Object.assign(this.quality, QUALITY_PRESETS[preset]);
    this.quality.preset = preset;
    this.renderer.applyQuality();
    // チャンクを作り直す
    for (const [, c] of this.terrain.chunks) c.dispose();
    this.terrain.chunks.clear();
  }

  start() {
    this.running = true;
    let last = performance.now();
    const loop = (now) => {
      if (!this.running) return;
      requestAnimationFrame(loop);
      const raw = Math.min((now - last) / 1000, 0.1);
      last = now;
      this.frame(raw);
    };
    requestAnimationFrame(loop);
  }

  frame(raw) {
    const dt = this.time.step(raw);
    this.input.update();

    if (this.ui.modalOpen) {
      this.ui.update(dt, this);
      this.input.endFrame();
      this.renderFrame();
      return;
    }
    if (!this.paused) this.update(dt);
    this.ui.update(dt, this);
    this.input.endFrame();
    this.renderFrame();
  }

  renderFrame() {
    this.renderer.damage = lerp(this.renderer.damage, this.damageFlash || 0, 0.2);
    this.damageFlash = Math.max(0, (this.damageFlash || 0) - this.time.raw * 2.4);
    this.renderer.aberration = this.activeBoss ? 0.022 : 0;
    // 水面をくぐる瞬間に切り替わらないよう、なめらかに混ぜる
    this.renderer.underwaterMix = lerp(this.renderer.underwaterMix || 0,
      this.renderer.underwater || 0, 0.18);
    this.renderer.render(this);
  }

  /* ------------------------------------------- 地面・衝突のディスパッチ */
  groundHeight(x, z) {
    return this.dungeon ? this.dungeon.groundHeight(x, z) : this.world.height(x, z);
  }
  collide(x, z, radius, out) {
    if (this.dungeon) return this.dungeon.collide(x, z, radius, out);
    const hitTerrain = this.terrain.collide(x, z, radius, out);
    // 木や岩を避けた後の位置から、さらに建物を避ける
    const hitBuild = this.blockers
      ? this.blockers.resolve(out[0], out[1], radius, out) : false;
    return hitTerrain || hitBuild;
  }
  surfaceAt(x, z) {
    if (this.dungeon) return { h: 0, slope: 0, surface: 'rock', road: 0, underwater: false };
    return this.world.sample(x, z);
  }

  /** 近い順に点光源を集める（松明・篝火・魔法・炎） */
  collectLights(max) {
    const cam = this.camera.pos;
    const cand = this._lightCand;
    cand.length = 0;
    const add = (x, y, z, r, cr, cg, cb, phase) => {
      const d = Math.hypot(x - cam[0], z - cam[2]);
      if (d > r + 40) return;
      cand.push({ x, y, z, r, cr, cg, cb, phase, d });
    };
    if (this.dungeon) {
      // 手持ちの灯り（足元が見えないと遊べないので常設）
      add(this.player.x, this.player.y + 1.5, this.player.z, 9.5,
        0.62, 0.50, 0.36, 0);
      for (const t of this.dungeon.torches) {
        const c = t.color;
        const r = t.arena ? 22 : t.big ? 14 : 10;
        const k = t.arena ? 1.85 : t.big ? 1.5 : 1.15;
        add(t.x, t.y, t.z, r, c[0] * k, c[1] * k, c[2] * k, t.x * 0.13 + t.z * 0.07);
      }
      // 開放された下り階段
      const st = this.dungeon.bossDefeated ? this.dungeon.stairPoint : null;
      if (st) {
        const c = this.dungeon.theme.torchColor;
        add(st.x, 1.6, st.z, 13, c[0] * 1.4, c[1] * 1.4, c[2] * 1.5, 3.3);
      }
    } else {
      const night = 0.35 + this.sky.night * 0.9;
      for (const poi of this.pois) {
        if (Math.hypot(poi.x - cam[0], poi.z - cam[2]) > 60) continue;
        if (poi.type === 'shrine') {
          add(poi.x, poi.y + 1.5, poi.z, 13, 1.5 * night, 1.15 * night, 0.55 * night, poi.id);
        } else if (poi.type === 'village' || poi.type === 'camp'
          || poi.type === 'hermit' || poi.type === 'wreck') {
          add(poi.x, poi.y + 0.9, poi.z, 12, 1.6 * night, 0.95 * night, 0.4 * night, poi.id);
        } else if (poi.type === 'crystal') {
          add(poi.x, poi.y + 1.2, poi.z, 10, 0.5 * night, 0.85 * night, 1.5 * night, poi.id);
        }
      }
      // 人が帰っている家には灯りが点る。夜の村が無人に見えないように
      if (this.sky.night > 0.15) {
        const k = this.sky.night * 1.25;
        for (const n of this.npcs) {
          if (!n.indoors) continue;
          const h = n.spots?.home;
          if (!h) continue;
          if (Math.hypot(h.x - cam[0], h.z - cam[2]) > 52) continue;
          add(h.x, this.groundHeight(h.x, h.z) + 1.7, h.z, 7.5,
            1.15 * k, 0.72 * k, 0.32 * k, n.id * 0.37);
        }
      }
    }
    // 飛翔中の魔法弾も光る
    for (const b of this.projectiles) {
      const c = PROJ_COLOR[b.kind];
      if (!c || b.kind === 'arrow' || b.kind === 'arrow_heavy' || b.kind === 'knife') continue;
      add(b.x, b.y, b.z, 8, c[0] * 1.6, c[1] * 1.6, c[2] * 1.6, b.x);
    }
    cand.sort((a, b) => a.d - b.d);
    const n = Math.min(max, 8, cand.length);
    for (let i = 0; i < n; i++) {
      const l = cand[i];
      this.lightPos[i * 4] = l.x; this.lightPos[i * 4 + 1] = l.y;
      this.lightPos[i * 4 + 2] = l.z; this.lightPos[i * 4 + 3] = l.r;
      this.lightCol[i * 4] = l.cr; this.lightCol[i * 4 + 1] = l.cg;
      this.lightCol[i * 4 + 2] = l.cb; this.lightCol[i * 4 + 3] = l.phase % 10;
    }
    this._lights.count = n;
    return this._lights;
  }

  /* ============================================================ 更新 */
  update(dt) {
    const p = this.player;

    this.sky.update(dt, this);
    if (this.dungeon) this.applyDungeonAtmosphere();
    else this.sky.applyRegion(this.world.params(p.x, p.z));

    this.camera.update(dt, this);
    if (this.input.pressed('mount')) this.handleMountButton();
    p.update(dt, this);
    if (p.state === 'cast') p.processSpell(dt, this);

    // 地形ストリーミング（ダンジョン内では止める）
    if (this.dungeon) {
      this.terrain.visible.length = 0;
      this.terrain.grassChunks.length = 0;
    } else {
      this.terrain.update(p.x, p.z, this.time.now, this.quality.viewChunks > 8 ? 7 : 5);
    }

    // エンティティ
    this.updateAggroTokens(dt);
    for (let i = this.enemies.length - 1; i >= 0; i--) {
      const e = this.enemies[i];
      const d = Math.hypot(e.x - p.x, e.z - p.z);
      if (e.dead && e.removeAt > 6) { this.enemies.splice(i, 1); continue; }
      if (!e.boss && d > 190) { this.enemies.splice(i, 1); continue; }
      if (d < 150) e.update(dt, this);
    }
    for (const n of this.npcs) {
      if (Math.hypot(n.x - p.x, n.z - p.z) < 70) n.update(dt, this);
    }
    if (!this.dungeon) this.mount.update(dt, this);
    this.updateProjectiles(dt);
    this.updateSwimming(dt);
    if (this.dungeon) {
      this.updateDungeon(dt);
    } else {
      this.updateSpawning(dt);
      this.updateGatherables();
      this.updateBoss(dt);
      this.updateAmbient(dt);
    }
    this.fx.update(dt, this);
    this.quests.update(this);
    this.updateInteract();
    if (!this.dungeon) this.updateDiscovery();
    this.updateMusic();
    this.audio.update(dt, this);
    this.audio.updateAmbience(dt, this);

    // バフ
    if (p.spellBuff) {
      p.spellBuff.t -= dt;
      p.defBuff = p.mods.defMul * p.spellBuff.def;
      if (p.spellBuff.t <= 0) { p.spellBuff = null; p.defBuff = p.mods.defMul; }
    }

    // オートセーブ
    this.lastSave += dt;
    if (this.lastSave > 30) { this.lastSave = 0; this.save(); }
  }

  /** 馬ボタン：近ければ騎乗、遠ければ口笛で呼ぶ */
  handleMountButton() {
    const p = this.player;
    if (p.riding) { this.mount.dismount(this); return; }
    if (p.dead || !p.canAct()) return;
    const d = Math.hypot(this.mount.x - p.x, this.mount.z - p.z);
    if (d < 4.2 && !this.mount.dead) {
      this.mount.mount(this);
      return;
    }
    // 口笛で呼び寄せる
    this.audio.play('whistle');
    if (this.mount.dead) {
      this.mount.dead = false;
      this.mount.hp = this.mount.maxHp;
      this.mount.state = 'idle';
      this.mount.deathT = 0;
    }
    const a = this.camera.yaw + Math.PI + (Math.random() - 0.5) * 1.2;
    const spot = this.world.findFlat(p.x + Math.sin(a) * 11, p.z + Math.cos(a) * 11, 8, 12, 0.7, 0.5);
    this.mount.x = spot.x; this.mount.z = spot.z;
    this.mount.y = this.world.height(spot.x, spot.z);
    this.mount.yaw = Math.atan2(p.x - spot.x, p.z - spot.z);
    this.fx.dust(this.mount.x, this.mount.y + 0.1, this.mount.z, 8);
    this.ui.toast('灰毛を呼んだ');
  }

  /* -------------------------------------------------------- スポーン */
  updateSpawning(dt) {
    const p = this.player;
    this.spawnTimer -= dt;

    // POI 常駐の敵
    for (const poi of this.pois) {
      if (!poi.spawns.length || this.clearedPOIs.has(poi.id)) continue;
      const d = Math.hypot(poi.x - p.x, poi.z - p.z);
      if (d > 130 || poi.spawned) continue;
      poi.spawned = true;
      for (const s of poi.spawns) {
        for (let i = 0; i < s.count; i++) {
          const a = Math.random() * TAU, r = Math.random() * s.radius;
          const x = poi.x + Math.cos(a) * r, z = poi.z + Math.sin(a) * r;
          const e = spawnEnemy(s.kind, {
            x, y: this.world.height(x, z), z, yaw: Math.random() * TAU,
            poi, leash: s.radius + 22,
            hpMul: 1 + poi.danger * 0.12, echoMul: 1 + poi.danger * 0.2,
          });
          if (e) this.enemies.push(e);
        }
      }
    }
    // POI から離れたらリセット
    for (const poi of this.pois) {
      if (poi.spawned && Math.hypot(poi.x - p.x, poi.z - p.z) > 210) poi.spawned = false;
    }

    // 徘徊する敵
    if (this.spawnTimer > 0) return;
    this.spawnTimer = 1.6;
    const roamers = this.enemies.filter((e) => !e.poi && !e.boss && !e.dead).length;
    const params = this.world.params(p.x, p.z);
    const want = Math.min(this.quality.maxEnemies, Math.round(3 + params.danger * 0.9));
    if (roamers >= want) return;

    const table = SPAWN_TABLE[params.region.id] || SPAWN_TABLE.downs;
    let total = 0;
    for (const [, w] of table) total += w;
    let r = Math.random() * total;
    let kind = table[0][0];
    for (const [k, w] of table) { r -= w; if (r <= 0) { kind = k; break; } }

    for (let tries = 0; tries < 12; tries++) {
      const a = Math.random() * TAU;
      const dist = 62 + Math.random() * 48;
      const x = p.x + Math.cos(a) * dist, z = p.z + Math.sin(a) * dist;
      if (Math.hypot(x, z) > this.worldRadius - 30) continue;
      const s = this.world.sample(x, z);
      if (s.h < 1.5 || s.slope > 0.7) continue;
      // 集落の近くには湧かない
      let near = false;
      for (const poi of this.pois) {
        if (poi.type !== 'village' && poi.type !== 'shrine') continue;
        if (Math.hypot(poi.x - x, poi.z - z) < 60) { near = true; break; }
      }
      if (near) continue;

      const def = SPAWN_TABLE[params.region.id] ? kind : 'wolf';
      const group = Math.random() < 0.5 ? 1 : 1 + (Math.random() * 2 | 0);
      for (let i = 0; i < group; i++) {
        const ox = x + (Math.random() - 0.5) * 8, oz = z + (Math.random() - 0.5) * 8;
        const e = spawnEnemy(def, {
          x: ox, y: this.world.height(ox, oz), z: oz, yaw: Math.random() * TAU,
          leash: 46, hpMul: 1 + params.danger * 0.14, echoMul: 1 + params.danger * 0.22,
        });
        if (e) this.enemies.push(e);
      }
      break;
    }
  }

  respawnWorld() {
    // 篝火で休むと敵が復活する
    for (let i = this.enemies.length - 1; i >= 0; i--) {
      const e = this.enemies[i];
      if (e.boss && !e.dead) continue;
      this.enemies.splice(i, 1);
    }
    for (const poi of this.pois) poi.spawned = false;
    this.harvested.clear();
    if (this.activeBoss && this.activeBoss.dead) this.endBoss();
  }

  /* ------------------------------------------------------ 発射物 */
  spawnProjectile(o) {
    const cp = Math.cos(o.pitch || 0);
    this.projectiles.push({
      x: o.x, y: o.y, z: o.z,
      vx: Math.sin(o.yaw) * cp * o.speed,
      vy: Math.sin(o.pitch || 0) * o.speed,
      vz: Math.cos(o.yaw) * cp * o.speed,
      kind: o.kind, damage: o.damage, team: o.team, owner: o.owner,
      life: 5, gravity: o.gravity ?? (o.kind === 'arrow' || o.kind === 'arrow_heavy' ? 5.5 : 0),
      splash: o.splash || 0, frost: o.frost, fire: o.fire, poise: 14,
    });
  }

  updateProjectiles(dt) {
    const P = this.projectiles;
    for (let i = P.length - 1; i >= 0; i--) {
      const b = P[i];
      b.life -= dt;
      b.vy -= b.gravity * dt;
      const nx = b.x + b.vx * dt, ny = b.y + b.vy * dt, nz = b.z + b.vz * dt;

      // 軌跡
      const col = PROJ_COLOR[b.kind] || [1, 1, 1];
      this.fx.spawn({
        x: b.x, y: b.y, z: b.z, life: 0.22, size: b.kind === 'firebomb' ? 0.2 : 0.11, sizeEnd: 0.02,
        r: col[0], g: col[1], b: col[2], a: 0.9, kind: 0, glow: 1.4, drag: 2,
      });

      let hit = null;
      const targets = b.team === TEAM.PLAYER ? this.enemies : [this.player];
      for (const t of targets) {
        if (t.dead) continue;
        const dx = t.x - nx, dz = t.z - nz;
        const dy = (t.y + t.height * 0.55) - ny;
        if (dx * dx + dz * dz < (t.radius + 0.35) ** 2 && Math.abs(dy) < t.height * 0.6) {
          hit = t; break;
        }
      }
      const gh = this.world.height(nx, nz);
      if (!hit && ny <= gh) hit = 'ground';

      if (hit || b.life <= 0) {
        if (b.splash > 0) {
          this.explode(nx, Math.max(ny, gh + 0.2), nz, b.splash, b.damage, b.team, b);
        } else if (hit && hit !== 'ground') {
          hit.takeDamage(b.damage, {
            source: b.owner, poise: b.poise, frost: b.frost, fire: b.fire, type: 'projectile',
          }, this);
        } else {
          this.fx.sparks(nx, ny, nz, 5, col);
        }
        P.splice(i, 1);
        continue;
      }
      b.x = nx; b.y = ny; b.z = nz;
    }
  }

  explode(x, y, z, radius, damage, team, b) {
    this.fx.emberBurst(x, y, z, 34);
    this.fx.shockwave(x, y, z, radius * 0.7);
    this.audio.play('slam');
    this.camera.shake(0.4);
    const targets = team === TEAM.PLAYER ? this.enemies : [this.player];
    for (const t of targets) {
      if (t.dead) continue;
      const d = Math.hypot(t.x - x, t.z - z);
      if (d > radius) continue;
      const k = 1 - d / radius;
      t.takeDamage(damage * (0.5 + k * 0.5), {
        source: b?.owner, poise: 30 * k, fire: b?.fire, type: 'fire',
      }, this);
    }
  }

  /** 攻撃で樽などを壊す（簡易） */
  hitProps() { /* 予約：破壊可能オブジェクト */ }

  /* ---------------------------------------------------- ダンジョン */
  /**
   * ダンジョンに入る（または下層へ降りる）。
   * @param poi   入口の POI
   * @param depth 階層。省略時は 1（最上層）
   */
  enterDungeon(poi, depth = 1) {
    const p = this.player;
    // 降下中は最初の地上座標を保持し続ける（何層潜っても地上へ一度で戻れる）
    if (!this.dungeon) this.outsideReturn = { x: p.x, y: p.y, z: p.z, yaw: p.yaw };
    const d = Math.max(1, depth);
    const tier = 1 + Math.min(3, Math.floor((poi.danger || 1) / 3));
    this.dungeon = new Dungeon(poi, (poi.x * 7919 + poi.z * 104729 + this.seed + d * 6151) | 0, d, tier);
    this.dungeonPOI = poi;
    this.enemies.length = 0;
    this.activeBoss = null;
    this.bossPOI = null;
    this.ui.hideBoss();
    this.audio.setMode('explore');
    p.x = this.dungeon.spawnPoint.x;
    p.z = this.dungeon.spawnPoint.z;
    p.y = 0;
    p.lockTarget = null;
    if (p.riding) this.mount.dismount(this);
    this.dungeonSpawned = false;
    this.quests.start('delve');
    this.audio.play('discover');
    this.ui.showRegion({ name: this.dungeon.name, en: `DUNGEON B${d}F` });
    this.ui.toast(d > 1 ? `さらに深く潜った（第${d}層）` : '松明の灯りだけが頼りだ');
    this.deepestDepth = Math.max(this.deepestDepth || 0, d);
  }

  /** 主を倒したあとの下り階段 */
  descendDungeon() {
    const poi = this.dungeonPOI;
    const next = this.dungeon.depth + 1;
    this.enterDungeon(poi, next);
  }

  exitDungeon() {
    const p = this.player;
    const r = this.outsideReturn;
    this.dungeon = null;
    this.dungeonPOI = null;
    this.enemies.length = 0;
    this.activeBoss = null;
    this.bossPOI = null;
    this.ui.hideBoss();
    this.audio.setMode('explore');
    if (r) { p.x = r.x; p.z = r.z; p.yaw = r.yaw; p.y = this.world.height(r.x, r.z); }
    p.lockTarget = null;
    for (const poi of this.pois) poi.spawned = false;
    this.audio.play('ui_off');
    this.ui.showRegion(this.world.regionAt(p.x, p.z));
  }

  /** ダンジョン内の大気：太陽を消し、濃い闇と霧にする */
  applyDungeonAtmosphere() {
    const sky = this.sky;
    const f = this.dungeon.theme.fog;
    sky.zenith = [f[0], f[1], f[2]];
    sky.horizon = [f[0] * 1.2, f[1] * 1.2, f[2] * 1.25];
    sky.sunColor = [0, 0, 0];
    sky.ambSky = [f[0] * 3.2 + 0.048, f[1] * 3.2 + 0.050, f[2] * 3.2 + 0.062];
    sky.ambGnd = [f[0] * 2.0 + 0.030, f[1] * 2.0 + 0.031, f[2] * 2.0 + 0.036];
    sky.fogDensity = 0.023;
    sky.night = 1;
    sky.cloud = 0;
    sky.rainAmount = 0;
    sky.snowAmount = 0;
    sky.wind = 0.15;
    sky.visibility = 1;
    sky.exposure = 1.16;
    sky.grade = [1.03, 0.99, 0.97];
  }

  updateDungeon(dt) {
    const d = this.dungeon;
    const p = this.player;
    if (!this.dungeonSpawned) {
      this.dungeonSpawned = true;
      const rng = makeRng((d.rooms.length * 7919 + this.seed) | 0);
      for (const sp of d.spawns) {
        for (let i = 0; i < sp.count; i++) {
          const a = rng() * TAU, r = rng() * sp.radius;
          const x = sp.x + Math.cos(a) * r, z = sp.z + Math.sin(a) * r;
          if (!d.isOpenAt(x, z)) continue;
          const e = spawnEnemy(sp.kind, {
            x, y: 0, z, yaw: rng() * TAU, leash: 200,
            hpMul: 1 + d.rank * 0.25 + (sp.elite ? 0.5 : 0),
            echoMul: 1.5 + d.rank * 0.4 + (sp.elite ? 1 : 0),
            scaleMul: sp.elite ? 1.15 : 1,
          });
          if (e) this.enemies.push(e);
        }
      }
    }
    this.updateDungeonBoss(dt);

    // 松明の火の粉
    for (const t of d.torches) {
      if (Math.hypot(t.x - p.x, t.z - p.z) > 26) continue;
      this.fx.campfireEmbers(t.x, t.y - 0.2, t.z, dt * (t.big ? 1 : 0.5));
    }
    // 霧の門から立ち上る霧
    if (!d.bossDefeated) {
      for (const g of d.gates) {
        if (Math.hypot(g.x - p.x, g.z - p.z) > 22) continue;
        this.fx.fogGate(g.x, g.z, g.yaw, dt, d.theme.torchColor);
      }
    }
  }

  /** 主の間に踏み込んだら階層ボスを起こす */
  updateDungeonBoss(dt) {
    const d = this.dungeon;
    const p = this.player;
    if (!d.bossArena || !d.bossId) return;

    if (!this.activeBoss) {
      if (!d.bossDefeated && !p.dead && d.inBossRoom(p.x, p.z)) this.startDungeonBoss();
      return;
    }
    const b = this.activeBoss;
    if (b.dead) {
      this.bossDeathT = (this.bossDeathT || 0) + dt;
      if (this.bossDeathT > 3.2) {
        this.activeBoss = null;
        this.bossPOI = null;
        this.ui.hideBoss();
      }
      return;
    }
    // 主の間から大きく離れる／死ぬと戦闘リセット
    const dist = Math.hypot(b.x - d.bossArena.x, b.z - d.bossArena.z);
    const pd = Math.hypot(p.x - d.bossArena.x, p.z - d.bossArena.z);
    void dist;
    if (p.dead || pd > d.bossArena.r + 30) {
      const i = this.enemies.indexOf(b);
      if (i >= 0) this.enemies.splice(i, 1);
      this.activeBoss = null;
      this.bossPOI = null;
      this.ui.hideBoss();
      this.audio.setMode('explore');
    }
  }

  startDungeonBoss() {
    const d = this.dungeon;
    const p = this.player;
    const a = d.bossArena;
    // 深度に応じて硬く・重くする
    const k = 1 + (d.rank - 1) * 0.24;
    const b = spawnBoss(d.bossId, {
      x: a.x, y: 0, z: a.z,
      yaw: Math.atan2(p.x - a.x, p.z - a.z),
      arenaR: a.r,
      hpMul: k,
      echoMul: 1 + (d.rank - 1) * 0.35,
      powerMul: 1 + (d.rank - 1) * 0.10,
      scaleMul: 1 + Math.min(0.18, (d.rank - 1) * 0.04),
    });
    if (!b) return;
    b.aggro = true;
    b.aiState = 'chase';
    b.dungeonBoss = true;
    this.activeBoss = b;
    this.bossPOI = null;
    this.bossDeathT = 0;
    this.enemies.push(b);
    this.ui.showBoss(b);
    this.audio.setMode('boss');
    this.audio.play('boss_phase');
    this.fx.bossPhase(b.x, b.y, b.z, [0.6, 0.7, 1]);
    this.camera.shake(0.6);
  }

  /* ---------------------------------------------------------- 水 */
  /** 水際を跨いだ瞬間の飛沫 */
  onWaterCross(actor, entering) {
    if (Math.hypot(actor.x - this.player.x, actor.z - this.player.z) > 60) return;
    this.fx.splash(actor.x, this.seaLevel, actor.z, entering ? 22 : 12, actor.radius);
    this.audio.play('splash', { pitch: 0.85 + Math.random() * 0.35 });
  }

  /**
   * 水の中の移動。浅瀬は歩けるが遅く、深いところでは泳ぐことになる。
   * 泳いでいるあいだは武器を振れず、スタミナを食う。
   */
  updateSwimming(dt) {
    const p = this.player;
    if (this.dungeon) {
      p.waterDepth = 0; p.wading = false; p.swimming = false;
      this.renderer.underwater = 0;   // 水中に潜ったまま地下へ入っても青みを残さない
      return;
    }

    // 波紋と飛沫（歩いているときだけ）
    if (p.wading && !p.dead) {
      const speed = Math.hypot(p.x - (this._wx ?? p.x), p.z - (this._wz ?? p.z)) / Math.max(dt, 1e-4);
      if (speed > 0.6) {
        this._splashAcc = (this._splashAcc || 0) + dt * Math.min(speed, 8);
        if (this._splashAcc > 0.55) {
          this._splashAcc = 0;
          this.fx.splash(p.x, this.seaLevel, p.z, 5, p.radius * 0.8);
          this.audio.footstep('water', 0.4);
        }
      }
    }
    this._wx = p.x; this._wz = p.z;

    if (p.swimming) {
      // 泳ぎ：構えも攻撃も解く
      if (p.state === 'attack' || p.state === 'block') p.setState('idle', 0.1);
      p.blocking = false;
      // 泳いでいるあいだはスタミナが回復しない。
      // これを止めないと回復量（34/秒）が消費量を上回って永遠に泳げてしまう
      p.staminaDelay = Math.max(p.staminaDelay, 0.25);
      p.stamina = Math.max(0, p.stamina - 8.5 * dt);
      if (p.stamina <= 0) {
        // 力尽きると溺れる
        this._drown = (this._drown || 0) + dt;
        if (this._drown > 1.0) {
          this._drown = 0;
          p.takeDamage(p.maxHp * 0.10 + 8, { type: 'fall', noFlinch: true }, this);
          this.fx.splash(p.x, this.seaLevel, p.z, 10, 0.5);
        }
      } else this._drown = 0;
    } else {
      this._drown = 0;
    }

    // 水面より下にカメラがあるなら画面を水中にする
    const cam = this.camera.pos;
    this.renderer.underwater = (!this.dungeon && cam[1] < this.seaLevel) ? 1 : 0;
  }

  /* ------------------------------------------------ 群れの間合い管理 */
  /**
   * 同時に踏み込める敵の数を絞る。
   * 全員が一斉に殴りかかると読み合いにならないので、
   * 「踏み込み権」を近い者から数体に配り、残りは待ちの輪で牽制させる。
   * 権利は数秒保持したあと手放し、別の個体に回る。
   */
  updateAggroTokens(dt) {
    const p = this.player;
    const list = this._tokenList || (this._tokenList = []);
    list.length = 0;
    for (const e of this.enemies) {
      if (e.dead || e.boss) { if (e) e.token = !!e.boss; continue; }
      if (!e.aggro || e.arch?.passive) { e.token = false; e.tokenT = 0; continue; }
      if (e.arch?.ranged) { e.token = true; continue; }   // 射手は常に自由
      list.push(e);
    }
    if (!list.length) return;

    // ボス戦中は取り巻きを 1 体に絞る
    const max = this.activeBoss ? 1 : list.length > 5 ? 3 : 2;
    const contested = list.length > max;

    let held = 0;
    for (const e of list) {
      e.tokenCd -= dt;
      if (!e.token) continue;
      e.tokenT -= dt;
      if (e.tokenT <= 0) {
        // 順番待ちがいなければ持ち続ける（1対1で急に引くのは不自然）
        if (!contested) { e.tokenT = 2.5; held++; continue; }
        e.token = false;
        e.tokenCd = 0.9 + Math.random() * 1.4;
      } else held++;
    }
    if (held >= max) return;

    list.sort((a, b) => (
      Math.hypot(a.x - p.x, a.z - p.z) - Math.hypot(b.x - p.x, b.z - p.z)
    ));
    for (const e of list) {
      if (held >= max) break;
      if (e.token || e.tokenCd > 0) continue;
      e.token = true;
      e.tokenT = 2.2 + Math.random() * 2.2;
      held++;
    }
  }

  /** 屍術士が死者を起こす */
  summonMinions(caster, kind, count) {
    const alive = this.enemies.filter((e) => !e.dead && e.summonedBy === caster).length;
    if (alive >= 4) return;
    let spawned = 0;
    for (let i = 0; i < count; i++) {
      const a = Math.random() * TAU;
      const r = 2.4 + Math.random() * 3.2;
      const x = caster.x + Math.sin(a) * r;
      const z = caster.z + Math.cos(a) * r;
      if (this.dungeon && !this.dungeon.isOpenAt(x, z)) continue;
      const e = spawnEnemy(kind, {
        x, y: this.groundHeight(x, z), z, yaw: Math.atan2(this.player.x - x, this.player.z - z),
        leash: 200, hpMul: 0.7, echoMul: 0.35,
      });
      if (!e) continue;
      e.summonedBy = caster;
      e.aggro = true;
      e.aiState = 'chase';
      e.spawnFade = 0.6;
      this.enemies.push(e);
      this.fx.voidBurst(x, this.groundHeight(x, z) + 0.6, z);
      spawned++;
    }
    if (spawned) {
      this.audio.play('teleport');
      this.ui.toast('屍術士が死者を起こした');
    }
  }

  /** 群れの長が遠吠えで仲間を鼓舞する */
  rallyAllies(howler, radius) {
    let n = 0;
    for (const e of this.enemies) {
      if (e.dead || e === howler) continue;
      if (Math.hypot(e.x - howler.x, e.z - howler.z) > radius) continue;
      e.rally = 8;
      if (!e.aggro && !e.arch?.passive) { e.aggro = true; e.aiState = 'chase'; }
      n++;
    }
    howler.rally = 8;
    this.fx.shockwave(howler.x, howler.y, howler.z, radius * 0.35);
    this.audio.play('aggro', { pitch: 0.6 });
    this.camera.shake(0.3);
    if (n) this.ui.toast('遠吠えに群れが応えた');
  }

  /* -------------------------------------------------------- ボス */
  updateBoss(dt) {
    const p = this.player;
    if (!this.activeBoss) {
      for (const poi of this.pois) {
        if (poi.type !== 'boss' || poi.cleared) continue;
        const d = Math.hypot(poi.x - p.x, poi.z - p.z);
        if (d < (poi.arenaR || 26) - 3) {
          this.startBoss(poi);
          break;
        }
      }
      return;
    }
    const b = this.activeBoss;
    const d = Math.hypot(b.x - p.x, b.z - p.z);
    if (b.dead) {
      this.bossDeathT = (this.bossDeathT || 0) + dt;
      if (this.bossDeathT > 3.2) this.endBoss();
    } else if (d > (this.bossPOI.arenaR || 26) + 26 || p.dead) {
      this.endBoss(true);
    }
  }

  startBoss(poi) {
    const def = poi.boss;
    const b = spawnBoss(def, {
      x: poi.x, y: this.world.height(poi.x, poi.z), z: poi.z,
      yaw: Math.atan2(this.player.x - poi.x, this.player.z - poi.z),
      arenaR: poi.arenaR,
    });
    if (!b) return;
    b.aggro = true;
    b.aiState = 'chase';
    this.activeBoss = b;
    this.bossPOI = poi;
    this.bossDeathT = 0;
    this.enemies.push(b);
    this.ui.showBoss(b);
    this.audio.setMode(b.bossDef.music === 'final' ? 'final' : 'boss');
    this.audio.play('boss_phase');
    poi.discovered = true;
    this.player.discovered.add(poi.id);
  }

  endBoss(reset = false) {
    if (reset && this.activeBoss && !this.activeBoss.dead) {
      const i = this.enemies.indexOf(this.activeBoss);
      if (i >= 0) this.enemies.splice(i, 1);
    }
    this.activeBoss = null;
    this.bossPOI = null;
    this.ui.hideBoss();
  }

  onBossDefeated(boss) {
    const poi = this.bossPOI;
    if (poi) { poi.cleared = true; this.clearedPOIs.add(poi.id); }
    this.quests.onBossDefeated(boss.bossDef.id);
    this.ui.bossDefeated(boss.name);
    this.audio.setMode('explore');
    const r = boss.bossDef.reward || {};
    if (r.weapon) this.unlockWeapon(r.weapon);
    if (r.armor) this.unlockArmor(r.armor);
    if (r.shield) this.unlockShield(r.shield);
    if (r.talisman) this.unlockTalisman(r.talisman);
    if (r.spell) this.unlockSpell(r.spell);
    if (r.item) { this.player.addItem(r.item, r.count || 1); this.ui.itemGain(r.item, r.count || 1); }
    if (boss.bossDef.final) this.ui.showEnding(this);

    // 階層ボス：宝箱と下り階段を開放し、到達記録を残す
    if (boss.dungeonBoss && this.dungeon) {
      const d = this.dungeon;
      d.bossDefeated = true;
      const id = this.dungeonPOI?.id;
      if (id) {
        const prev = this.dungeonProgress.get(id) || 0;
        if (d.depth > prev) this.dungeonProgress.set(id, d.depth);
      }
      this.quests.count.dungeonBosses = (this.quests.count.dungeonBosses || 0) + 1;
      this.quests.start('depths');
      this.ui.toast('奥に下りの階段が現れた', 'gold');
      if (d.stairPoint) this.fx.bossPhase(d.stairPoint.x, 0.6, d.stairPoint.z, [0.75, 0.85, 1.0]);
    }
    this.save();
  }

  unlockWeapon(id) {
    if (!WEAPONS[id] || this.unlocked.weapons.has(id)) return;
    this.unlocked.weapons.add(id);
    this.player.upgrades[id] = this.player.upgrades[id] || 0;
    this.ui.toast(`武器を手に入れた：${WEAPONS[id].name}`);
    this.audio.play('discover');
  }
  unlockArmor(id) {
    if (!ARMORS[id] || this.unlocked.armors.has(id)) return;
    this.unlocked.armors.add(id);
    this.ui.toast(`防具を手に入れた：${ARMORS[id].name}`);
  }
  unlockShield(id) {
    if (!SHIELDS[id] || this.unlocked.shields.has(id)) return;
    this.unlocked.shields.add(id);
    this.ui.toast(`盾を手に入れた：${SHIELDS[id].name}`);
  }
  unlockTalisman(id) {
    if (!TALISMANS[id] || this.unlocked.talismans.has(id)) return;
    this.unlocked.talismans.add(id);
    this.ui.toast(`護符を手に入れた：${TALISMANS[id].name}`);
  }
  unlockSpell(id) {
    if (!SPELLS[id] || this.unlocked.spells.has(id)) return;
    this.unlocked.spells.add(id);
    if (!this.player.equip.spell) this.player.equip.spell = id;
    this.ui.toast(`魔法を覚えた：${SPELLS[id].name}`);
  }

  /* ---------------------------------------------------- 採取ポイント */
  updateGatherables() {
    const p = this.player;
    const CELL = 34;
    const R = 3;
    const cx = Math.floor(p.x / CELL), cz = Math.floor(p.z / CELL);
    const seen = new Set();
    for (let j = -R; j <= R; j++) {
      for (let i = -R; i <= R; i++) {
        const gx = cx + i, gz = cz + j;
        const key = gx * 100003 + gz;
        seen.add(key);
        if (this.gatherables.has(key)) continue;
        const h = hash2(gx, gz, this.seed + 55);
        if (h > 0.30) { this.gatherables.set(key, null); continue; }
        const x = (gx + 0.2 + hash2(gx, gz, 3) * 0.6) * CELL;
        const z = (gz + 0.2 + hash2(gx, gz, 9) * 0.6) * CELL;
        const s = this.world.sample(x, z);
        if (s.h < 1.4 || s.slope > 0.75) { this.gatherables.set(key, null); continue; }
        const params = this.world.params(x, z);
        let type = 'herb';
        if (params.moist > 0.85) type = 'blood_flower';
        else if (s.surface === 'rock' || params.region.id === 'skyspire') type = 'ore_iron';
        else if (params.region.id === 'cinder' || params.region.id === 'riftvale') type = 'crystal';
        else if (hash2(gx, gz, 17) < 0.18) type = 'ore_iron';
        this.gatherables.set(key, { key, x, y: s.h, z, type });
      }
    }
    for (const k of [...this.gatherables.keys()]) {
      if (!seen.has(k)) this.gatherables.delete(k);
    }
  }

  /* ------------------------------------------------- インタラクション */
  updateInteract() {
    const p = this.player;
    let best = null, bestD = 3.4;

    if (this.dungeon) {
      const ex = this.dungeon.exitPoint;
      const de = Math.hypot(ex.x - p.x, ex.z - p.z);
      if (de < 3.4) { bestD = de; best = { type: 'dungeon_exit', obj: ex, label: '地上へ戻る' }; }
      for (const c of this.dungeon.chests) {
        if (c.opened) continue;
        if (c.boss && !this.dungeon.bossDefeated) continue;
        const d = Math.hypot(c.x - p.x, c.z - p.z);
        if (d < bestD) { bestD = d; best = { type: 'dungeon_chest', obj: c, label: '宝箱を開ける' }; }
      }
      const st = this.dungeon.stairPoint;
      if (st && this.dungeon.bossDefeated && !this.activeBoss) {
        const d = Math.hypot(st.x - p.x, st.z - p.z);
        if (d < bestD) {
          bestD = d;
          best = { type: 'dungeon_descend', obj: st, label: `第${this.dungeon.depth + 1}層へ降りる` };
        }
      }
      this.interact = best;
      this.ui.setPrompt(best ? best.label : null);
      if (best && this.input.pressed('interact')) this.doInteract(best);
      return;
    }

    for (const n of this.npcs) {
      if (n.indoors) continue;              // 家の中で寝ている相手には話しかけられない
      const d = Math.hypot(n.x - p.x, n.z - p.z);
      if (d < bestD) { bestD = d; best = { type: 'npc', obj: n, label: `${n.npcName}（${n.title}）と話す` }; }
    }
    for (const poi of this.pois) {
      const d = Math.hypot(poi.x - p.x, poi.z - p.z);
      if (poi.type === 'shrine' && d < 3.2) {
        if (d < bestD) { bestD = d; best = { type: 'shrine', obj: poi, label: `${poi.name}で休息する` }; }
      }
      if ((poi.type === 'grave' || poi.type === 'ruin' || poi.type === 'mine') && d < 4.5) {
        if (d < bestD) { bestD = d; best = { type: 'dungeon_enter', obj: poi, label: `${poi.name}へ潜る` }; }
      }
      if (poi.type === 'tower' && d < 4.5 && !poi.climbed) {
        if (d < bestD) { bestD = d; best = { type: 'tower', obj: poi, label: '望楼に登る' }; }
      }
      if (poi.chest && !this.openedChests.has(poi.id)) {
        const cd = Math.hypot(poi.chest.x - p.x, poi.chest.z - p.z);
        if (cd < bestD) { bestD = cd; best = { type: 'chest', obj: poi, label: '宝箱を開ける' }; }
      }
    }
    for (const g of this.gatherables.values()) {
      if (!g || this.harvested.has(g.key)) continue;
      const d = Math.hypot(g.x - p.x, g.z - p.z);
      if (d < bestD) { bestD = d; best = { type: 'gather', obj: g, label: `${ITEMS[g.type].name}を採る` }; }
    }
    if (!p.riding && !this.mount.dead) {
      const d = Math.hypot(this.mount.x - p.x, this.mount.z - p.z);
      if (d < bestD) { bestD = d; best = { type: 'mount', obj: this.mount, label: '灰毛に騎乗する' }; }
    }
    if (p.lostEcho) {
      const d = Math.hypot(p.lostEcho.x - p.x, p.lostEcho.z - p.z);
      if (d < 2.6) { bestD = d; best = { type: 'echo', obj: p.lostEcho, label: `残響を回収する（${p.lostEcho.echo}）` }; }
    }

    this.interact = best;
    this.ui.setPrompt(best ? best.label : null);
    if (best && this.input.pressed('interact')) this.doInteract(best);
  }

  doInteract(t) {
    const p = this.player;
    switch (t.type) {
      case 'npc':
        this.ui.openDialogue(t.obj);
        break;
      case 'shrine': {
        const poi = t.obj;
        if (!poi.discovered) {
          poi.discovered = true;
          p.discovered.add(poi.id);
        }
        this.audio.play('shrine');
        this.ui.openShrine(poi);
        break;
      }
      case 'tower': {
        t.obj.climbed = true;
        this.quests.start('towers');
        this.quests.onTower();
        this.revealAround(t.obj.x, t.obj.z, 380);
        this.audio.play('discover');
        this.ui.toast(`${t.obj.name}：周辺の地図を得た`);
        for (const poi of this.pois) {
          if (Math.hypot(poi.x - t.obj.x, poi.z - t.obj.z) < 380) {
            poi.discovered = true;
            p.discovered.add(poi.id);
          }
        }
        break;
      }
      case 'chest': {
        const poi = t.obj;
        this.openedChests.add(poi.id);
        const tier = clamp(poi.chest.tier, 1, CHEST_TABLE.length - 1);
        const loot = CHEST_TABLE[tier];
        p.echo += loot.echo;
        for (const [item, n] of loot.items) { p.addItem(item, n); this.ui.itemGain(item, n); }
        this.ui.toast(`宝箱：残響 +${loot.echo}`);
        this.audio.play('chest');
        this.fx.heal(poi.chest.x, this.world.height(poi.chest.x, poi.chest.z) + 0.6, poi.chest.z);
        break;
      }
      case 'gather': {
        const g = t.obj;
        this.harvested.add(g.key);
        const n = 1 + (Math.random() < 0.35 ? 1 : 0);
        p.addItem(g.type, n);
        this.ui.itemGain(g.type, n);
        this.audio.play('ui_confirm');
        this.fx.dust(g.x, g.y + 0.3, g.z, 6);
        break;
      }
      case 'mount':
        this.mount.mount(this);
        break;
      case 'dungeon_enter':
        this.enterDungeon(t.obj);
        break;
      case 'dungeon_exit':
        this.exitDungeon();
        break;
      case 'dungeon_descend':
        this.descendDungeon();
        break;
      case 'dungeon_chest': {
        const c = t.obj;
        c.opened = true;
        const loot = CHEST_TABLE[clamp(c.tier, 1, CHEST_TABLE.length - 1)];
        p.echo += loot.echo;
        for (const [item, n] of loot.items) { p.addItem(item, n); this.ui.itemGain(item, n); }
        this.ui.toast(`宝箱：残響 +${loot.echo}`);
        this.audio.play('chest');
        this.fx.heal(c.x, 0.6, c.z);
        this.quests.count.dungeonChests = (this.quests.count.dungeonChests || 0) + 1;
        break;
      }
      case 'echo': {
        p.echo += p.lostEcho.echo;
        this.ui.toast(`残響を取り戻した（+${p.lostEcho.echo}）`);
        this.audio.play('levelup');
        this.fx.heal(p.x, p.y + 1, p.z);
        p.lostEcho = null;
        break;
      }
    }
  }

  /* ---------------------------------------------------------- 発見 */
  updateDiscovery() {
    const p = this.player;
    const key = `${Math.floor(p.x / 48)},${Math.floor(p.z / 48)}`;
    this.visited.add(key);
    for (const poi of this.pois) {
      if (poi.discovered) continue;
      const d = Math.hypot(poi.x - p.x, poi.z - p.z);
      const r = poi.type === 'village' || poi.type === 'boss' ? 70 : 42;
      if (d < r) {
        poi.discovered = true;
        p.discovered.add(poi.id);
        this.ui.discover(poi);
        this.audio.play('discover');
      }
    }
    // 地方の踏破
    const region = this.world.regionAt(p.x, p.z);
    if (this.currentRegion !== region.id) {
      this.currentRegion = region.id;
      this.ui.showRegion(region);
    }
  }

  revealAround(x, z, radius) {
    const step = 48;
    for (let dz = -radius; dz <= radius; dz += step) {
      for (let dx = -radius; dx <= radius; dx += step) {
        if (dx * dx + dz * dz > radius * radius) continue;
        this.visited.add(`${Math.floor((x + dx) / 48)},${Math.floor((z + dz) / 48)}`);
      }
    }
  }

  /* ----------------------------------------------------- 環境演出 */
  updateAmbient(dt) {
    const cam = this.camera;
    const p = this.player;
    const params = this.world.params(p.x, p.z);
    if (this.sky.rainAmount > 0.02) {
      this.fx.rain(cam.pos[0], cam.pos[1], cam.pos[2], dt, this.sky.rainAmount, this.world,
        this.sky.windDir);
    }
    if (this.sky.snowAmount > 0.02 || (params.temp < 0.22 && p.y > 60)) {
      this.fx.snow(cam.pos[0], cam.pos[1], cam.pos[2], dt, Math.max(this.sky.snowAmount, 0.35),
        this.sky.windDir, this.sky.wind);
    }
    const rid = params.region.id;
    if (rid === 'cinder') this.fx.ambient(p.x, p.y + 1.5, p.z, dt, 'ash', 0.5);
    else if (rid === 'mistfen') this.fx.ambient(p.x, p.y + 1.5, p.z, dt, 'spore', 0.4);
    else if (this.sky.isNight && (rid === 'downs' || rid === 'gloomwood')) {
      this.fx.ambient(p.x, p.y + 1.2, p.z, dt, 'firefly', 0.35);
    } else if (this.sky.wind > 0.8) {
      this.fx.ambient(p.x, p.y + 1.5, p.z, dt, 'leaf', 0.22);
    }
    // 篝火・焚き火の火の粉
    for (const poi of this.pois) {
      const d = Math.hypot(poi.x - p.x, poi.z - p.z);
      if (d > 40) continue;
      if (poi.type === 'shrine') this.fx.shrineGlow(poi.x, poi.y + 1.2, poi.z, dt);
      else if (poi.type === 'village' || poi.type === 'camp'
        || poi.type === 'hermit' || poi.type === 'wreck') {
        this.fx.campfireEmbers(poi.x, poi.y + 0.4, poi.z, dt);
      }
    }
  }

  updateMusic() {
    if (this.activeBoss && !this.activeBoss.dead) return;
    let combat = false;
    for (const e of this.enemies) {
      if (e.dead || !e.aggro) continue;
      if (Math.hypot(e.x - this.player.x, e.z - this.player.z) < 26) { combat = true; break; }
    }
    // 地下では専用の、遅く低く沈んだ曲に切り替える
    const calm = this.dungeon ? 'dungeon' : 'explore';
    this.audio.setMode(this.player.dead ? 'none' : combat ? 'combat' : calm);
  }

  /* ------------------------------------------------------ コールバック */
  onDamage(target, dmg, opts, blocked) {
    this.ui.damageNumber(target, dmg, blocked, opts);
    if (target === this.player) {
      if (this.player.riding && dmg > this.player.maxHp * 0.10) this.mount.dismount(this, true);
      this.damageFlash = Math.min(1, (this.damageFlash || 0) + dmg / this.player.maxHp * 2.4);
      this.camera.shake(0.3 + Math.min(0.5, dmg / 120));
      this.audio.play('hit', { pitch: 0.8 });
    }
  }

  onActorDeath(actor) {
    if (actor instanceof Enemy && !actor.boss) this.quests.onKill(actor.archetype);
  }

  onPlayerDeath() {
    this.audio.setMode('none');
    this.audio.play('death');
    this.ui.showDeath();
  }

  /** 霧の門と、討伐後に現れる下り階段 */
  emitDungeonGates(renderer, time) {
    const d = this.dungeon;
    const tc = d.theme.torchColor;
    for (const g of d.gates) {
      const frame = renderer.batchFor('fog_gate');
      if (frame) {
        const o = frame.alloc();
        writeInstance(frame.data, o, g.x, 0, g.z, g.yaw, 1, 1, 1,
          d.theme.wall[0] * 0.9, d.theme.wall[1] * 0.9, d.theme.wall[2] * 0.9, 1, 0, 0, 0, 0.5);
      }
      if (d.bossDefeated) continue;    // 討伐後は霧が晴れる
      const veil = renderer.batchFor('fog_veil');
      if (veil) {
        const o = veil.alloc();
        // 濃いめのアルファ（ディザが目立たない）＋ 近づくと溶ける
        const shimmer = 0.80 + Math.sin(time * 1.6 + g.x * 0.31) * 0.05;
        writeInstance(veil.data, o, g.x, 0, g.z, g.yaw, 1, 1, 1,
          0.46 + tc[0] * 0.20, 0.52 + tc[1] * 0.20, 0.64 + tc[2] * 0.16,
          shimmer, 0, 0.13, 0, 0.5);
      }
    }
    if (d.bossDefeated && d.stairPoint) {
      const b = renderer.batchFor('stairs_down');
      if (b) {
        const o = b.alloc();
        writeInstance(b.data, o, d.stairPoint.x, 0, d.stairPoint.z, 0, 1, 1, 1,
          d.theme.wall[0], d.theme.wall[1], d.theme.wall[2], 1, 0, 0, 0, 0.5);
      }
      // 階段口の淡い光（見落とさないように）
      const ball = renderer.batchFor('ball');
      if (ball) {
        const o = ball.alloc();
        const s = 0.3 + Math.sin(time * 2.2) * 0.05;
        writeInstance(ball.data, o, d.stairPoint.x, 1.5 + Math.sin(time * 0.9) * 0.12, d.stairPoint.z,
          time, s, s, s, tc[0], tc[1], tc[2], 1, 0, 1.6, 0, 0);
      }
    }
  }

  /* -------------------------------------------------- インスタンス出力 */
  emitInstances(renderer) {
    const p = this.player;
    const time = this.time.now;
    const px = p.x, pz = p.z;
    const frustum = renderer.frustum;

    // ダンジョン内はダンジョンのジオメトリだけを出す
    if (this.dungeon) {
      for (const [model, data] of this.dungeon.props) {
        const b = renderer.batchFor(model);
        if (b) b.append(data);
      }
      for (const c of this.dungeon.chests) {
        if (c.opened) continue;
        if (c.boss && !this.dungeon.bossDefeated) continue;
        const b = renderer.batchFor('chest');
        if (!b) continue;
        const o = b.alloc();
        writeInstance(b.data, o, c.x, 0, c.z, 0, 1, 1, 1,
          1, 0.95, 0.7, 1, 0, 0.12 + Math.sin(time * 2) * 0.05, 0, 0.5);
      }
      this.emitDungeonGates(renderer, time);
      p.emit(renderer, time, this);
      for (const e of this.enemies) {
        if (Math.hypot(e.x - px, e.z - pz) > 90) continue;
        e.emit(renderer, time, this);
      }
      for (const b of this.projectiles) {
        const model = b.kind === 'arrow' || b.kind === 'arrow_heavy' ? 'w_dagger' : 'ball';
        const batch = renderer.batchFor(model);
        if (!batch) continue;
        const o = batch.alloc();
        const c = PROJ_COLOR[b.kind] || [1, 1, 1];
        const s = b.kind === 'firebomb' ? 0.5 : 0.32;
        writeInstance(batch.data, o, b.x, b.y, b.z, Math.atan2(b.vx, b.vz), s, s, s,
          c[0], c[1], c[2], 1, 0, 1.2, 0.6);
      }
      if (p.lockTarget && !p.lockTarget.dead) {
        const t = p.lockTarget;
        const b = renderer.batchFor('ball');
        if (b) {
          const o = b.alloc();
          const s = 0.16 + Math.sin(time * 5) * 0.02;
          writeInstance(b.data, o, t.x, t.y + t.height * 1.16, t.z, time, s, s, s,
            1, 0.85, 0.5, 1, 0, 1.5, 0.8);
        }
      }
      return;
    }

    // 地形の散布物
    for (const c of this.terrain.visible) {
      if (!c.props) continue;
      if (!frustum.sphere(c.center[0], c.center[1], c.center[2], c.radius + 8)) continue;
      for (const [model, data] of c.props) {
        const b = renderer.batchFor(model);
        if (b) b.append(data);
      }
    }

    // POI の建造物
    for (const poi of this.pois) {
      const d = Math.hypot(poi.x - px, poi.z - pz);
      if (d > 300) continue;
      for (const s of poi.structures) {
        if (!frustum.sphere(s.x, s.y + 3, s.z, 12)) continue;
        const b = renderer.batchFor(s.model);
        if (!b) continue;
        const o = b.alloc();
        writeInstance(b.data, o, s.x, s.y, s.z, s.rotY, s.sx, s.sy, s.sz,
          s.r, s.g, s.b, 1, s.wind, s.emissive, 0, 0.5);
      }
      // 宝箱
      if (poi.chest && !this.openedChests.has(poi.id) && d < 200) {
        const b = renderer.batchFor('chest');
        if (b) {
          const o = b.alloc();
          const cy = this.world.height(poi.chest.x, poi.chest.z);
          writeInstance(b.data, o, poi.chest.x, cy, poi.chest.z, 0, 1, 1, 1,
            1, 0.95, 0.7, 1, 0, 0.12 + Math.sin(time * 2) * 0.05, 0, 0.5);
        }
      }
    }

    // 採取物
    for (const g of this.gatherables.values()) {
      if (!g || this.harvested.has(g.key)) continue;
      const model = g.type === 'ore_iron' || g.type === 'crystal' ? 'crystal' : 'bush_1';
      const b = renderer.batchFor(model);
      if (!b) continue;
      const o = b.alloc();
      const c = GATHER_COLOR[g.type];
      writeInstance(b.data, o, g.x, g.y, g.z, g.key % 6, 0.6, 0.7, 0.6,
        c[0], c[1], c[2], 1, g.type === 'herb' || g.type === 'blood_flower' ? 0.6 : 0,
        0.35 + Math.sin(time * 1.7 + g.key) * 0.12, 0.55, 0.5);
    }

    // アクター
    p.emit(renderer, time, this);
    if (Math.hypot(this.mount.x - px, this.mount.z - pz) < 140) this.mount.emit(renderer, time, this);
    for (const e of this.enemies) {
      if (Math.hypot(e.x - px, e.z - pz) > 140) continue;
      if (!frustum.sphere(e.x, e.y + e.height * 0.5, e.z, e.height)) continue;
      e.emit(renderer, time, this);
    }
    for (const n of this.npcs) {
      if (n.indoors) continue;
      if (Math.hypot(n.x - px, n.z - pz) > 90) continue;
      if (!frustum.sphere(n.x, n.y + n.height * 0.5, n.z, n.height)) continue;
      n.emit(renderer, time, this);
      // 用件のある相手の頭上に印を出す（受注は金、報告は白）
      const mark = n.questMark(this);
      if (!mark) continue;
      const b = renderer.batchFor('spike');
      if (!b) continue;
      const bob = Math.sin(time * 2.2 + n.id) * 0.07;
      const gold = mark === 'new';
      const c = gold ? [1.0, 0.82, 0.32] : [0.85, 0.95, 1.0];
      const o = b.alloc();
      writeInstance(b.data, o, n.x, n.y + n.height * 1.24 + bob, n.z, time * 1.2,
        0.20, 0.34, 0.20, c[0], c[1], c[2], 1, 0, 1.7, 0, 0);
      const o2 = b.alloc();
      writeInstance(b.data, o2, n.x, n.y + n.height * 1.24 + bob + 0.30, n.z, time * 1.2,
        0.20, -0.22, 0.20, c[0], c[1], c[2], 1, 0, 1.7, 0, 0);
    }

    // 発射物
    for (const b of this.projectiles) {
      const model = b.kind === 'arrow' || b.kind === 'arrow_heavy' ? 'w_dagger' : 'ball';
      const batch = renderer.batchFor(model);
      if (!batch) continue;
      const o = batch.alloc();
      const c = PROJ_COLOR[b.kind] || [1, 1, 1];
      const yaw = Math.atan2(b.vx, b.vz);
      const s = b.kind === 'firebomb' ? 0.5 : 0.32;
      writeInstance(batch.data, o, b.x, b.y, b.z, yaw, s, s, s, c[0], c[1], c[2], 1, 0, 1.2, 0.6);
    }

    // ロックオンマーカー
    if (p.lockTarget && !p.lockTarget.dead) {
      const t = p.lockTarget;
      const b = renderer.batchFor('ball');
      if (b) {
        const o = b.alloc();
        const s = 0.16 + Math.sin(time * 5) * 0.02;
        writeInstance(b.data, o, t.x, t.y + t.height * 1.16, t.z, time, s, s, s,
          1, 0.85, 0.5, 1, 0, 1.5, 0.8);
      }
    }
  }

  /* ---------------------------------------------------------- セーブ */
  save() {
    try {
      const ps = this.player.serialize();
      if (this.dungeon && this.outsideReturn) {
        ps.x = this.outsideReturn.x; ps.y = this.outsideReturn.y; ps.z = this.outsideReturn.z;
      }
      const data = {
        v: 5, seed: this.seed,
        player: ps,
        delve: [...this.dungeonProgress],
        deepest: this.deepestDepth || 0,
        quests: this.quests.serialize(),
        sky: this.sky.serialize(),
        opened: [...this.openedChests],
        cleared: [...this.clearedPOIs],
        climbed: this.pois.filter((p) => p.climbed).map((p) => p.id),
        unlocked: {
          weapons: [...this.unlocked.weapons], armors: [...this.unlocked.armors],
          shields: [...this.unlocked.shields], talismans: [...this.unlocked.talismans],
          spells: [...this.unlocked.spells],
        },
        visited: [...this.visited].slice(-5000),
        quality: this.quality.preset || 'medium',
      };
      localStorage.setItem('aetheria_save', JSON.stringify(data));
      return true;
    } catch (e) {
      console.warn('save failed', e);
      return false;
    }
  }

  static hasSave() {
    try { return !!localStorage.getItem('aetheria_save'); } catch { return false; }
  }

  load() {
    try {
      const raw = localStorage.getItem('aetheria_save');
      if (!raw) return false;
      const d = JSON.parse(raw);
      if (d.seed !== this.seed) return false;
      this.player.deserialize(d.player, this);
      this.quests.deserialize(d.quests);
      this.sky.deserialize(d.sky);
      this.openedChests = new Set(d.opened || []);
      this.clearedPOIs = new Set(d.cleared || []);
      this.dungeonProgress = new Map(d.delve || []);
      this.deepestDepth = d.deepest || 0;
      for (const id of d.climbed || []) {
        const poi = this.pois.find((p) => p.id === id);
        if (poi) poi.climbed = true;
      }
      for (const k of ['weapons', 'armors', 'shields', 'talismans', 'spells']) {
        this.unlocked[k] = new Set(d.unlocked?.[k] || [...this.unlocked[k]]);
      }
      this.visited = new Set(d.visited || []);
      for (const poi of this.pois) {
        if (this.player.discovered.has(poi.id)) poi.discovered = true;
        if (this.clearedPOIs.has(poi.id)) poi.cleared = true;
      }
      this.player.y = this.world.height(this.player.x, this.player.z);
      return true;
    } catch (e) {
      console.warn('load failed', e);
      return false;
    }
  }

  static clearSave() {
    try { localStorage.removeItem('aetheria_save'); } catch { /* noop */ }
  }
}

const PROJ_COLOR = {
  arrow: [0.85, 0.8, 0.7], arrow_heavy: [0.95, 0.9, 0.75],
  fire: [1, 0.5, 0.15], soul: [0.5, 0.7, 1], ice: [0.6, 0.85, 1],
  bolt: [1, 0.95, 0.5], void: [0.55, 0.25, 0.85], knife: [0.8, 0.82, 0.86],
  firebomb: [1, 0.55, 0.2],
};
const GATHER_COLOR = {
  herb: [0.45, 0.85, 0.4], blood_flower: [0.95, 0.25, 0.3],
  ore_iron: [0.75, 0.7, 0.65], crystal: [0.55, 0.75, 1],
};

function frame() {
  return new Promise((r) => requestAnimationFrame(() => r()));
}

export { clamp, clamp01, lerp, v3, CHUNK, makeRng, NPC };
