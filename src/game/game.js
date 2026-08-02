// ゲーム本体：ループ・エンティティ管理・スポーン・インタラクション・セーブ

import { initGL } from '../core/gl.js';
import { Input } from '../core/input.js';
import { AudioEngine } from '../core/audio.js';
import { hash2, makeRng } from '../core/noise.js';
import { clamp, clamp01, lerp, v3, TAU } from '../core/math.js';

import { WorldGen, WORLD_RADIUS } from '../world/worldgen.js';
import { Terrain, CHUNK } from '../world/terrain.js';
import { generatePOIs } from '../world/pois.js';
import { Sky } from '../world/sky.js';

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
    cloudShadows: false, godRays: false, rayStrength: 0,
  },
  medium: {
    name: '標準', renderScale: 0.82, viewChunks: 8, lodScale: 1.0, shadows: true, shadowSize: 1536,
    shadowRange: 58, grassDensity: 0.85, grassDist: 84, grassShadow: false, treeDensity: 0.85,
    bloom: true, bloomPasses: 2, bloomStrength: 0.55, water: true, skyQuality: 1, maxEnemies: 18,
    cloudShadows: true, godRays: true, rayStrength: 0.55,
  },
  high: {
    name: '高品質', renderScale: 1.0, viewChunks: 11, lodScale: 1.3, shadows: true, shadowSize: 2048,
    shadowRange: 78, grassDensity: 1.15, grassDist: 108, grassShadow: true, treeDensity: 1.0,
    bloom: true, bloomPasses: 3, bloomStrength: 0.6, water: true, skyQuality: 1, maxEnemies: 26,
    cloudShadows: true, godRays: true, rayStrength: 0.7,
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
    this.activeBoss = null;
    this.bossPOI = null;
    this.unlocked = { weapons: new Set(['broken_sword']), armors: new Set(['rags', 'hood']), shields: new Set(['wooden_shield']), talismans: new Set(), spells: new Set() };

    // 村に NPC を配置
    for (const p of this.pois) {
      if (p.type === 'village') this.npcs.push(...populateVillage(p, this.world));
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
    this.renderer.render(this);
  }

  /* ============================================================ 更新 */
  update(dt) {
    const p = this.player;

    this.sky.update(dt, this);
    this.sky.applyRegion(this.world.params(p.x, p.z));

    this.camera.update(dt, this);
    if (this.input.pressed('mount')) this.handleMountButton();
    p.update(dt, this);
    if (p.state === 'cast') p.processSpell(dt, this);

    // 地形ストリーミング
    this.terrain.update(p.x, p.z, this.time.now, this.quality.viewChunks > 8 ? 7 : 5);

    // エンティティ
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
    this.mount.update(dt, this);
    this.updateProjectiles(dt);
    this.updateSpawning(dt);
    this.updateGatherables();
    this.updateBoss(dt);
    this.updateAmbient(dt);
    this.fx.update(dt, this);
    this.quests.update(this);
    this.updateInteract();
    this.updateDiscovery();
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
    if (r.talisman) this.unlockTalisman(r.talisman);
    if (r.spell) this.unlockSpell(r.spell);
    if (r.item) { this.player.addItem(r.item, r.count || 1); this.ui.itemGain(r.item, r.count || 1); }
    if (boss.bossDef.final) this.ui.showEnding(this);
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

    for (const n of this.npcs) {
      const d = Math.hypot(n.x - p.x, n.z - p.z);
      if (d < bestD) { bestD = d; best = { type: 'npc', obj: n, label: `${n.npcName}（${n.title}）と話す` }; }
    }
    for (const poi of this.pois) {
      const d = Math.hypot(poi.x - p.x, poi.z - p.z);
      if (poi.type === 'shrine' && d < 3.2) {
        if (d < bestD) { bestD = d; best = { type: 'shrine', obj: poi, label: `${poi.name}で休息する` }; }
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
      this.fx.rain(cam.pos[0], cam.pos[1], cam.pos[2], dt, this.sky.rainAmount, this.world);
    }
    if (this.sky.snowAmount > 0.02 || (params.temp < 0.22 && p.y > 60)) {
      this.fx.snow(cam.pos[0], cam.pos[1], cam.pos[2], dt, Math.max(this.sky.snowAmount, 0.35));
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
      else if (poi.type === 'village' || poi.type === 'camp') {
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
    this.audio.setMode(this.player.dead ? 'none' : combat ? 'combat' : 'explore');
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

  /* -------------------------------------------------- インスタンス出力 */
  emitInstances(renderer) {
    const p = this.player;
    const time = this.time.now;
    const px = p.x, pz = p.z;
    const frustum = renderer.frustum;

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
      if (Math.hypot(n.x - px, n.z - pz) > 90) continue;
      if (!frustum.sphere(n.x, n.y + n.height * 0.5, n.z, n.height)) continue;
      n.emit(renderer, time, this);
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
      const data = {
        v: 3, seed: this.seed,
        player: this.player.serialize(),
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
