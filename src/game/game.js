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
import { Wildlife } from '../world/wildlife.js';
import { Travellers, CART_SCALE, VillageRaids, postWatch } from '../world/travellers.js';
import { Fish } from '../world/fish.js';
import { Fishing } from '../world/fishing.js';
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
  discoverReward,
} from './data.js';

const _wind = [0, 0];

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
    /**
     * 1 フレームの内訳計測。既定は null＝オフ。
     * オフのときに増えるのは「null かどうかの分岐」だけで、関数呼び出しも
     * performance.now() も起きない（計測フック自体の負担は tests/perf1 で測っている）。
     */
    this._prof = null;
  }

  /* -------------------------------------------------- 内訳計測（既定オフ）*/
  /**
   * 内訳の記録を始める／やめる。
   * @param on false で止める
   * @returns 記録簿
   */
  profile(on = true) {
    if (!on) { this._prof = null; return null; }
    this._prof = { acc: Object.create(null), frames: 0, _t: 0 };
    return this._prof;
  }

  /** 直前の区切りから今までを name に足し、区切りを進める（連続した区間用）*/
  _mark(name) {
    const P = this._prof;
    const t = performance.now();
    P.acc[name] = (P.acc[name] || 0) + (t - P._t);
    P._t = t;
  }

  /** t0 から今までを name に足す（区切りの連なりから外れた単発の区間用）*/
  _span(name, t0) {
    const P = this._prof;
    P.acc[name] = (P.acc[name] || 0) + (performance.now() - t0);
  }

  /**
   * 1 フレームあたりの内訳（µs）。多い順。
   * @param reset true なら読んだあと記録を空にする
   */
  profileReport(reset = true) {
    const P = this._prof;
    if (!P || !P.frames) return null;
    const n = P.frames;
    const rows = [];
    let total = 0;
    for (const k in P.acc) {
      const us = P.acc[k] / n * 1000;
      rows.push([k, +us.toFixed(1)]);
      total += us;
    }
    rows.sort((a, b) => b[1] - a[1]);
    const out = { frames: n, total: +total.toFixed(1), rows };
    if (reset) { P.acc = Object.create(null); P.frames = 0; }
    return out;
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
    // 開始地点が篝火の中にならないようにする（そのままだと一歩目で弾き飛ぶ）
    this.placeSafely(this.player, 40, 300);
    this.quests = new QuestLog();

    this.enemies = [];
    this.npcs = [];
    this.watch = [];            // 村の衛兵（中立。game.enemies には入れない）
    this.wildlife = new Wildlife();
    this.travellers = new Travellers();
    this.raids = new VillageRaids();
    this.fish = new Fish();
    this.fishing = new Fishing();
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
    // 村に衛兵を立てる（建物の当たり判定ができた後でないと持ち場が家の中に来る）
    for (const p of this.pois) this.watch.push(...postWatch(p, this));
    // 相棒の馬
    this.mount = new Mount({ x: this.player.x + 5, z: this.player.z + 4 });
    this.mount.y = this.world.height(this.mount.x, this.mount.z);

    // 開始地点の篝火
    const start = this.pois.find((p) => p.tag === 'start');
    // 立っている場所を「発見」とは言わない。報いも出さず、静かに既知にしておく
    if (start) {
      this.player.lastShrine = start;
      start.discovered = true; start.reached = true;
      this.player.discovered.add(start.id); this.player.reached.add(start.id);
    }

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
    // 討伐の余韻は実時間で進める（自分で掛けたスローに引きずられないように）
    this.updateVictory(raw);
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
  /**
   * その場に「立てる」位置へ置く。
   *
   * 篝火や建物には当たり判定があるので、拠点の座標へそのまま置くと
   * 物の中に立つことになり、一歩動いた瞬間に押し出されて飛ぶ。
   * 開始・復活・転送は必ずここを通す。
   */
  placeSafely(actor, x, z) {
    const out = [x, z];
    if (!this.dungeon && this.blockers) {
      for (let k = 0; k < 4; k++) {
        if (!this.blockers.resolve(out[0], out[1], actor.radius + 0.25, out)) break;
      }
    }
    actor.x = out[0]; actor.z = out[1];
    actor.y = this.groundHeight(actor.x, actor.z);
    return actor;
  }

  surfaceAt(x, z) {
    if (this.dungeon) return { h: 0, slope: 0, surface: 'rock', road: 0, underwater: false };
    return this.world.sample(x, z);
  }

  /** 近い順に点光源を集める（松明・篝火・魔法・炎） */
  collectLights(max) {
    const _t0 = this._prof ? performance.now() : 0;
    const cam = this.camera.pos;
    const cand = this._lightCand;
    cand.length = 0;
    // 距離は「届くか」と「近い順」にしか使わないので、平方のまま扱う。
    // Math.hypot は 1 回 70ns 掛かり、ここは毎フレーム 60〜90 回通る
    const add = (x, y, z, r, cr, cg, cb, phase) => {
      const dx = x - cam[0], dz = z - cam[2];
      const d = dx * dx + dz * dz;
      const reach = r + 40;
      if (d > reach * reach) return;
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
        const dx = poi.x - cam[0], dz = poi.z - cam[2];
        if (dx * dx + dz * dz > 60 * 60) continue;
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
          const dx = h.x - cam[0], dz = h.z - cam[2];
          if (dx * dx + dz * dz > 52 * 52) continue;
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
    if (this._prof) this._span('光源集め', _t0);
    return this._lights;
  }

  /* ============================================================ 更新 */
  update(dt) {
    const p = this.player;
    const P = this._prof;
    if (P) { P.frames++; P._t = performance.now(); }

    this.sky.update(dt, this);
    if (this.dungeon) this.applyDungeonAtmosphere();
    else this.sky.applyRegion(this.world.params(p.x, p.z));
    if (P) this._mark('天候');

    this.camera.update(dt, this);
    // 聴き手はカメラ。音の距離と向きはここを基準に決まる
    this.audio.setListener(this.camera.pos[0], this.camera.pos[2], this.camera.yaw);
    if (P) this._mark('カメラ');
    if (this.input.pressed('mount')) this.handleMountButton();
    p.update(dt, this);
    if (p.state === 'cast') p.processSpell(dt, this);
    if (P) this._mark('自機');

    // 地形ストリーミング（ダンジョン内では止める）
    if (this.dungeon) {
      this.terrain.visible.length = 0;
      this.terrain.grassChunks.length = 0;
    } else {
      this.terrain.update(p.x, p.z, this.time.now, this.quality.viewChunks > 8 ? 7 : 5);
    }
    if (P) this._mark('地形ストリーミング');

    // エンティティ
    // 村の襲撃は踏み込み権の前に。狙う相手と権利をここで決めてから敵を動かす
    if (!this.dungeon) this.raids.update(dt, this);
    else if (this.raids.raiding) this.raids.abandon(this);
    this.updateAggroTokens(dt);
    if (P) this._mark('踏み込み権');
    for (let i = this.enemies.length - 1; i >= 0; i--) {
      const e = this.enemies[i];
      const ex = e.x - p.x, ez = e.z - p.z;
      const d2 = ex * ex + ez * ez;
      if (e.dead && e.removeAt > 6) { this.enemies.splice(i, 1); continue; }
      if (!e.boss && d2 > 190 * 190) { this.enemies.splice(i, 1); continue; }
      if (d2 < 150 * 150) e.update(dt, this);
    }
    if (P) this._mark('敵');
    for (const n of this.npcs) {
      const nx = n.x - p.x, nz = n.z - p.z;
      if (nx * nx + nz * nz < 70 * 70) n.update(dt, this);
    }
    if (P) this._mark('村人');
    // 衛兵は地上の村にしかいない。地下にいる間は動かさない
    // （地下の敵と座標が重なると、見えない所で斬り合いが始まる）
    if (!this.dungeon) {
      for (const w of this.watch) {
        const wx = w.x - p.x, wz = w.z - p.z;
        if (wx * wx + wz * wz < 90 * 90) w.update(dt, this);
      }
    }
    if (P) this._mark('衛兵');
    if (!this.dungeon) this.mount.update(dt, this);
    if (P) this._mark('馬');
    this.wildlife.update(dt, this);
    if (P) this._mark('鳥');
    this.travellers.update(dt, this);
    this.checkRescue();
    if (P) this._mark('旅人');
    this.fish.update(dt, this);
    if (P) this._mark('魚');
    this.updateProjectiles(dt);
    if (P) this._mark('飛翔体');
    this.updateSwimming(dt);
    if (P) this._mark('水');
    // 釣りは地上だけ。地下に入ったら自分で畳むので、分岐の外で回す
    this.fishing.update(dt, this);
    if (P) this._mark('釣り');
    if (this.dungeon) {
      this.updateDungeon(dt);
      if (P) this._mark('地下');
    } else {
      this.updateSpawning(dt);
      if (P) this._mark('湧き');
      this.updateGatherables();
      if (P) this._mark('採取点');
      this.updateBoss(dt);
      if (P) this._mark('ボス');
      this.updateAmbient(dt);
      if (P) this._mark('環境演出');
    }
    this.fx.update(dt, this);
    if (P) this._mark('粒子');
    this.quests.update(this);
    if (P) this._mark('依頼');
    this.updateInteract();
    if (P) this._mark('interact');
    if (!this.dungeon) this.updateDiscovery();
    if (P) this._mark('発見');
    this.updateMusic();
    if (P) this._mark('曲');
    this.audio.update(dt, this);
    this.audio.updateAmbience(dt, this);
    if (P) this._mark('音');

    // バフ
    if (p.spellBuff) {
      p.spellBuff.t -= dt;
      p.defBuff = p.mods.defMul * p.spellBuff.def;
      if (p.spellBuff.t <= 0) { p.spellBuff = null; p.defBuff = p.mods.defMul; }
    }
    // 戦技の自己強化
    if (p.artBuff) {
      p.artBuff.t -= dt;
      if (p.artBuff.t <= 0) { p.artBuff = null; p.recalc(); }
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
      const dx = poi.x - p.x, dz = poi.z - p.z;
      if (dx * dx + dz * dz > 130 * 130 || poi.spawned) continue;
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
      if (!poi.spawned) continue;
      const dx = poi.x - p.x, dz = poi.z - p.z;
      if (dx * dx + dz * dz > 210 * 210) poi.spawned = false;
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
      // 風を受けるのは矢だけ。術は自分で飛ぶ
      windK: (o.kind === 'arrow' || o.kind === 'arrow_heavy') ? 1 : 0,
    });
  }

  /**
   * 矢を横へ流す風の加速度。
   *
   * 天候が絵と音だけで、手元に何も返っていなかった。
   * 強い横風の日には、遠くを狙うほど風下へ持っていかれる。
   * 地下では吹かない。
   */
  windAccel(out) {
    if (this.dungeon || !this.sky) { out[0] = 0; out[1] = 0; return out; }
    const w = this.sky.wind || 0;
    const d = this.sky.windDir || [1, 0];
    const a = w * 1.6;
    out[0] = d[0] * a; out[1] = d[1] * a;
    return out;
  }

  updateProjectiles(dt) {
    const P = this.projectiles;
    for (let i = P.length - 1; i >= 0; i--) {
      const b = P[i];
      b.life -= dt;
      b.vy -= b.gravity * dt;
      if (b.windK) {
        this.windAccel(_wind);
        b.vx += _wind[0] * b.windK * dt;
        b.vz += _wind[1] * b.windK * dt;
      }
      const nx = b.x + b.vx * dt, ny = b.y + b.vy * dt, nz = b.z + b.vz * dt;

      // 軌跡
      const col = PROJ_COLOR[b.kind] || [1, 1, 1];
      this.fx.spawn({
        x: b.x, y: b.y, z: b.z, life: 0.22, size: b.kind === 'firebomb' ? 0.2 : 0.11, sizeEnd: 0.02,
        r: col[0], g: col[1], b: col[2], a: 0.9, kind: 0, glow: 1.4, drag: 2,
      });

      // 当たり判定は「今いる点」ではなく「この一歩ぶんの線分」で見る。
      // 点で見ると、速い矢は細い的をまたいで通り抜ける
      // （実測で秒速 200 では 6 射すべてすり抜けた）
      let hit = null;
      let hitT = 1;
      const targets = b.team === TEAM.PLAYER ? this.enemies : [this.player];
      const sx = nx - b.x, sy = ny - b.y, sz = nz - b.z;
      for (const t of targets) {
        if (t.dead) continue;
        const r = t.radius + 0.35;
        const cy = t.y + t.height * 0.55;
        const hh = t.height * 0.6;
        // 水平面での線分と円の交差
        const ox = b.x - t.x, oz = b.z - t.z;
        const a = sx * sx + sz * sz;
        const bq = 2 * (ox * sx + oz * sz);
        const c = ox * ox + oz * oz - r * r;
        let tt = null;
        if (a < 1e-9) { if (c <= 0) tt = 0; }
        else {
          const disc = bq * bq - 4 * a * c;
          if (disc >= 0) {
            const sq = Math.sqrt(disc);
            const t0 = (-bq - sq) / (2 * a), t1 = (-bq + sq) / (2 * a);
            if (t0 >= 0 && t0 <= 1) tt = t0;
            else if (t1 >= 0 && t1 <= 1) tt = 0;   // 始点が既に内側
          }
        }
        if (tt === null) continue;
        const yAt = b.y + sy * tt;
        if (Math.abs(cy - yAt) > hh) continue;
        if (tt < hitT) { hitT = tt; hit = t; }
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
    this.audio.play('splash', { pitch: 0.85 + Math.random() * 0.35, ...(actor?.sfxAt?.() || {}) });
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

    // ---- 囲みの持ち場を配る ----
    // これが無いと、待っている者まで全員がプレイヤーの正面に寄り、
    // 群れが「前に固まった塊」になる。背後を取られる怖さが出ない。
    // 現在の方位の順に並べてから割り当てるので、無駄な横断が起きない
    const bear = (e) => Math.atan2(e.x - p.x, e.z - p.z);
    const ordered = list.slice().sort((a, b) => bear(a) - bear(b));
    const step = TAU / ordered.length;
    // 群れ全体の向きを基準にして、毎フレーム持ち場が入れ替わらないようにする
    let sx = 0, sz = 0;
    for (const e of ordered) { sx += Math.sin(bear(e)); sz += Math.cos(bear(e)); }
    const base = Math.atan2(sx, sz) - step * (ordered.length - 1) * 0.5;
    ordered.forEach((e, i) => { e.slotAngle = base + step * i; });

    // ---- 差し込み権 ----
    // 隙を見せた相手には権利が無くても寄れるようにしてあるが、
    // 上限が無いと、こちらが一度怯んだ瞬間に群れ全員が殺到する。
    // 「今なら差し込める」と判断してよいのは、いちばん近い 1 体だけにする
    for (const e of list) e.punisher = false;
    let nearest = null, nd = 1e18;
    for (const e of list) {
      if (e.token) continue;
      const dx = e.x - p.x, dz = e.z - p.z;
      const d = dx * dx + dz * dz;      // 近さの比較だけなので平方のまま
      if (d < nd) { nd = d; nearest = e; }
    }
    if (nearest) nearest.punisher = true;

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

    // 近い順。平方のままでも順序は変わらない
    const d2 = (e) => { const dx = e.x - p.x, dz = e.z - p.z; return dx * dx + dz * dz; };
    list.sort((a, b) => d2(a) - d2(b));
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
      if (this.bossDeathT > 4.2) this.endBoss();
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
    this.ui.bossIntro(b.name, b.title || '');
    this.audio.setMode(b.bossDef.music === 'final' ? 'final' : 'boss');
    this.audio.play('boss_phase');
    poi.discovered = true;
    this.player.discovered.add(poi.id);
  }

  /**
   * 討伐の余韻。
   *
   * これまでは名前が出て終わりだった。倒した瞬間だけは、
   * 世界がひと呼吸止まって見えるようにする。実時間で進めるので、
   * 自分で掛けたスローモーションに引きずられない。
   */
  startVictory(boss) {
    this.victoryT = 0;
    this.victoryName = boss.name;
    this.victory = 0;
    this.audio.setMode('none');            // 曲を切る。静けさが効く
    this.audio.play('kill_blow');
    this.audio.play('boss_fell', { delay: 0.25 });
    this.time.hitstop(0.30);
    this.camera.shake(0.9);
  }

  updateVictory(rawDt) {
    if (this.victoryT === null || this.victoryT === undefined) return;
    const t0 = this.victoryT;
    this.victoryT += rawDt;
    const t = this.victoryT;

    // 時間の伸び縮み：沈む → ためる → 戻る
    let scale = 1;
    if (t < 0.35) scale = lerp(1, 0.30, t / 0.35);
    else if (t < 1.9) scale = 0.30;
    else if (t < 2.8) scale = lerp(0.30, 1, (t - 1.9) / 0.9);
    this.time.scale = scale;

    // 画面：彩度を落として、周辺を締める
    this.victory = t < 2.2 ? Math.min(1, t / 0.4) : Math.max(0, 1 - (t - 2.2) / 1.0);

    if (t0 < 0.55 && t >= 0.55) this.ui.bossFelled(this.victoryName);
    if (t0 < 1.5 && t >= 1.5) this.audio.play('victory');
    if (t0 < 2.6 && t >= 2.6) this.audio.setMode('explore');
    if (t >= 3.6) {
      this.victoryT = null;
      this.victory = 0;
      this.time.scale = 1;
      // 後片付けは演出の終わりに合わせる。別の時計に任せると、
      // フレームが遅い環境で名乗りだけが残る
      if (this.activeBoss && this.activeBoss.dead) this.endBoss();
    }
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
    this.startVictory(boss);
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
        const rid = params.region.id;
        let type = 'herb';
        if (params.moist > 0.85) type = 'blood_flower';
        else if (s.surface === 'rock' || rid === 'skyspire') type = 'ore_iron';
        else if (rid === 'cinder' || rid === 'riftvale') type = 'crystal';
        else if (hash2(gx, gz, 17) < 0.18) type = 'ore_iron';

        // ---- 鉱脈の階層 ----
        // 銀鉱石と古き欠片は、これまで世界のどこにも湧かなかった。
        // 特定の敵と高位の宝箱からしか出ないので、強化の道が細すぎた。
        // 高いところ・険しいところほど良い鉱脈が出るようにして、
        // 登る理由と、強化の見通しを繋ぐ
        // 採取点は傾斜 0.75 までにしか湧かないので、実際に出るのは
        // 標高 190m あたりまで。閾値はその範囲に合わせてある
        const rare = hash2(gx, gz, 41);
        if (type === 'ore_iron' || type === 'crystal') {
          if (s.h > 105 && rare > 0.58) type = 'shard_ancient';
          else if (s.h > 48 && rare < 0.34) type = 'ore_silver';
        }
        // 骨灰は死者の地に眠る（常闇の森・裂罅の谷・霧の湿原）
        if (type === 'herb' && (rid === 'gloomwood' || rid === 'riftvale' || rid === 'mistfen')
          && hash2(gx, gz, 73) < 0.12) type = 'bone_ash';

        this.gatherables.set(key, { key, x, y: s.h, z, type });
      }
    }
    for (const k of [...this.gatherables.keys()]) {
      if (!seen.has(k)) this.gatherables.delete(k);
    }
  }

  /* ------------------------------------------------- インタラクション */
  /**
   * 手の届く先を決める。
   *
   * 距離は「いちばん近いものを選ぶ」ためにしか使わないので、平方のまま比べる
   * （順序も閾値も変わらない）。ここは村人・旅人・POI・宝箱・採取点を毎フレーム
   * なめるので、Math.hypot だと 170 回ぶん（約 12µs）になっていた。
   */
  updateInteract() {
    const p = this.player;
    const px = p.x, pz = p.z;
    let best = null, bestD = 3.4 * 3.4;      // 以下 bestD はすべて距離の平方
    const d2 = (x, z) => { const dx = x - px, dz = z - pz; return dx * dx + dz * dz; };

    if (this.dungeon) {
      const ex = this.dungeon.exitPoint;
      const de = d2(ex.x, ex.z);
      if (de < 3.4 * 3.4) { bestD = de; best = { type: 'dungeon_exit', obj: ex, label: '地上へ戻る' }; }
      for (const c of this.dungeon.chests) {
        if (c.opened) continue;
        if (c.boss && !this.dungeon.bossDefeated) continue;
        const d = d2(c.x, c.z);
        if (d < bestD) { bestD = d; best = { type: 'dungeon_chest', obj: c, label: '宝箱を開ける' }; }
      }
      const st = this.dungeon.stairPoint;
      if (st && this.dungeon.bossDefeated && !this.activeBoss) {
        const d = d2(st.x, st.z);
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

    // 釣っているあいだは「調べる」を釣りが握る。他の候補は探さない
    if (this.fishing.active) {
      this.interact = null;
      this.ui.setPrompt(this.fishing.prompt());
      return;
    }

    for (const n of this.npcs) {
      if (n.indoors || n.dead) continue;    // 家の中で寝ている相手・倒れた相手には話しかけられない
      const d = d2(n.x, n.z);
      if (d < bestD) { bestD = d; best = { type: 'npc', obj: n, label: `${n.npcName}（${n.title}）と話す` }; }
    }
    for (const w of this.watch) {
      if (w.dead || w.foe) continue;        // 斬り合っている最中に話しかけられても困る
      const d = d2(w.x, w.z);
      if (d < bestD) { bestD = d; best = { type: 'watchman', obj: w, label: `${w.npcName}（${w.title}）に声をかける` }; }
    }
    for (const t of this.travellers.everyone()) {
      const d = d2(t.x, t.z);
      if (d >= bestD) continue;
      bestD = d;
      // 隊商の親方とは取引ができる
      best = t.caravan && t.lead
        ? { type: 'pedlar_shop', obj: t, label: `${t.npcName}と取引する` }
        : { type: 'traveller', obj: t, label: `${t.npcName}に声をかける` };
    }
    for (const poi of this.pois) {
      // 拠点そのものへの用は最大でも 4.5m。遠い拠点は宝箱だけ見る
      const d = d2(poi.x, poi.z);
      if (d < 4.5 * 4.5) {
        if (poi.type === 'shrine' && d < 3.2 * 3.2) {
          if (d < bestD) { bestD = d; best = { type: 'shrine', obj: poi, label: `${poi.name}で休息する` }; }
        }
        if (poi.type === 'grave' || poi.type === 'ruin' || poi.type === 'mine') {
          if (d < bestD) { bestD = d; best = { type: 'dungeon_enter', obj: poi, label: `${poi.name}へ潜る` }; }
        }
        if (poi.type === 'tower' && !poi.climbed) {
          if (d < bestD) { bestD = d; best = { type: 'tower', obj: poi, label: '望楼に登る' }; }
        }
      }
      if (poi.chest && !this.openedChests.has(poi.id)) {
        const cd = d2(poi.chest.x, poi.chest.z);
        if (cd < bestD) { bestD = cd; best = { type: 'chest', obj: poi, label: '宝箱を開ける' }; }
      }
    }
    for (const g of this.gatherables.values()) {
      if (!g || this.harvested.has(g.key)) continue;
      const d = d2(g.x, g.z);
      if (d < bestD) { bestD = d; best = { type: 'gather', obj: g, label: `${ITEMS[g.type].name}を採る` }; }
    }
    if (!p.riding && !this.mount.dead) {
      const d = d2(this.mount.x, this.mount.z);
      if (d < bestD) { bestD = d; best = { type: 'mount', obj: this.mount, label: '灰毛に騎乗する' }; }
    }
    if (p.lostEcho) {
      const d = d2(p.lostEcho.x, p.lostEcho.z);
      if (d < 2.6 * 2.6) { bestD = d; best = { type: 'echo', obj: p.lostEcho, label: `残響を回収する（${p.lostEcho.echo}）` }; }
    }
    // 水辺。岸はどこでも糸を垂れられるが、他に用があるならそちらが先
    if (!best && !p.riding && !p.swimming) {
      const spot = this.fishing.canStart(this);
      if (spot) {
        if (this.fishing.hasRod(this)) {
          best = { type: 'fishing', obj: spot, label: '釣り糸を垂れる' };
        } else if (!this.fishing.hinted) {
          // 竿を持たない者にも、一度だけ水面の報せを出す。
          // ここは毎フレーム通るので、魚影を測り直したりはしない
          this.fishing.hinted = true;
          this.ui.toast('水面に魚影が差している —— 釣り竿があれば');
        }
      }
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
      case 'pedlar_shop': {
        const tv = t.obj;
        tv.talking = true;
        this.ui.toast(`${tv.npcName}「${tv.destination} まで運ぶ荷だ。要るものがあれば言いな」`, 'gold');
        this.audio.play('ui_on');
        this.ui.openShop();
        clearTimeout(tv._talkT);
        tv._talkT = setTimeout(() => { tv.talking = false; }, 6000);
        break;
      }
      case 'watchman': {
        // 衛兵は用件を持たない。持ち場のことを一言だけ返す
        const w = t.obj;
        w.talking = true;
        this.ui.toast(`${w.npcName}「${w.lines[(Math.random() * w.lines.length) | 0]}」`, 'gold');
        this.audio.play('ui_on');
        clearTimeout(w._talkT);
        w._talkT = setTimeout(() => { w.talking = false; }, 3200);
        break;
      }
      case 'traveller': {
        // 旅の者は用件を持たない。足を止めて一言だけ交わして、また歩き出す
        const tv = t.obj;
        tv.talking = true;
        const line = tv.lines[(Math.random() * tv.lines.length) | 0];
        this.ui.toast(`${tv.npcName}「${line}」`, 'gold');
        this.ui.toast(`${tv.destination} へ向かうという`);
        this.audio.play('ui_on');
        clearTimeout(tv._talkT);
        tv._talkT = setTimeout(() => { tv.talking = false; }, 3200);
        break;
      }
      case 'shrine': {
        const poi = t.obj;
        this.reachPOI(poi);
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
        // 望楼から見えるのは地図だけ。報いは自分の足で行った者に出る
        let known = 0;
        for (const poi of this.pois) {
          if (Math.hypot(poi.x - t.obj.x, poi.z - t.obj.z) < 380) {
            if (!poi.discovered) known++;
            poi.discovered = true;
            p.discovered.add(poi.id);
          }
        }
        this.ui.toast(known
          ? `${t.obj.name}：周辺の地図を得た（未踏の地 ${known}）`
          : `${t.obj.name}：周辺の地図を得た`);
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
        this.audio.play('chest', { x: poi.chest.x, z: poi.chest.z });
        this.fx.heal(poi.chest.x, this.world.height(poi.chest.x, poi.chest.z) + 0.6, poi.chest.z);
        break;
      }
      case 'gather': {
        const g = t.obj;
        this.harvested.add(g.key);
        // 良い鉱脈ほど一度に採れる量は少ない
        const rich = g.type === 'shard_ancient' ? 0 : g.type === 'ore_silver' ? 0.2 : 0.35;
        const n = 1 + (Math.random() < rich ? 1 : 0);
        p.addItem(g.type, n);
        this.ui.itemGain(g.type, n);
        // 草を摘むのと鉱脈を割るのでは音が違う
        const ore = g.type === 'ore_iron' || g.type === 'ore_silver'
          || g.type === 'shard_ancient' || g.type === 'crystal';
        this.audio.play(ore ? 'gather_ore' : 'gather_plant',
          { pitch: 0.9 + Math.random() * 0.25, x: g.x, z: g.z });
        this.fx.dust(g.x, g.y + 0.3, g.z, ore ? 10 : 6);
        if (ore) this.camera.kick(g.x - p.x, g.z - p.z, 0.12);
        break;
      }
      case 'mount':
        this.mount.mount(this);
        break;
      case 'fishing':
        this.fishing.start(this, t.obj);
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
        // 主の間の宝は、その階でいちばんの取り分。同じ言い方で流さない
        this.ui.toast(c.boss ? `あるじの宝：残響 +${loot.echo}` : `宝箱：残響 +${loot.echo}`,
          c.boss ? 'big gold' : '');
        this.audio.play(c.boss ? 'levelup' : 'chest', { x: c.x, z: c.z });
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

  /**
   * その場に足を運んだときの報い。
   *
   * 望楼から地図で知るのと、自分の足でたどり着くのは別のこと。
   * 残響が入るのは後者だけにしてある（reached）。地図で知った場所へ
   * 改めて行けば、そのときちゃんと報われる。
   */
  reachPOI(poi) {
    const p = this.player;
    if (p.reached.has(poi.id)) return 0;
    p.reached.add(poi.id);
    poi.reached = true;
    const first = !poi.discovered;
    poi.discovered = true;
    p.discovered.add(poi.id);
    const echo = discoverReward(poi);
    p.echo += echo;
    this.ui.discover(poi, echo, first);
    this.audio.play('discover');
    return echo;
  }

  /* ---------------------------------------------------------- 発見 */
  updateDiscovery() {
    const p = this.player;
    const key = `${Math.floor(p.x / 48)},${Math.floor(p.z / 48)}`;
    this.visited.add(key);
    for (const poi of this.pois) {
      if (poi.reached) continue;
      const dx = poi.x - p.x, dz = poi.z - p.z;
      const r = poi.type === 'village' || poi.type === 'boss' ? 70 : 42;
      if (dx * dx + dz * dz < r * r) this.reachPOI(poi);
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
      const dx = poi.x - p.x, dz = poi.z - p.z;
      if (dx * dx + dz * dz > 40 * 40) continue;
      if (poi.type === 'shrine') this.fx.shrineGlow(poi.x, poi.y + 1.2, poi.z, dt);
      else if (poi.type === 'village' || poi.type === 'camp'
        || poi.type === 'hermit' || poi.type === 'wreck') {
        this.fx.campfireEmbers(poi.x, poi.y + 0.4, poi.z, dt);
      }
    }
  }

  updateMusic() {
    if (this.activeBoss && !this.activeBoss.dead) return;
    // 討伐の余韻の間は曲を触らない。切った静けさを上書きしてしまう
    if (this.victoryT !== null && this.victoryT !== undefined) return;
    let combat = false;
    const p = this.player;
    for (const e of this.enemies) {
      if (e.dead || !e.aggro) continue;
      const dx = e.x - p.x, dz = e.z - p.z;
      if (dx * dx + dz * dz < 26 * 26) { combat = true; break; }
    }
    // 地下では専用の、遅く低く沈んだ曲に切り替える
    const calm = this.dungeon ? 'dungeon' : 'explore';
    this.audio.setMode(this.player.dead ? 'none' : combat ? 'combat' : calm);
  }

  /* ------------------------------------------------------ コールバック */
  onDamage(target, dmg, opts, blocked) {
    this.ui.damageNumber(target, dmg, blocked, opts);
    this.impact(target, dmg, opts, blocked);
    if (target === this.player) {
      if (this.player.riding && dmg > this.player.maxHp * 0.10) this.mount.dismount(this, true);
      this.damageFlash = Math.min(1, (this.damageFlash || 0) + dmg / this.player.maxHp * 2.4);
    }
  }

  /**
   * 手応え。打撃ひとつぶんの「止め・揺れ・突き・音」をここだけで決める。
   *
   * 短剣で小突くのと大剣を叩きつけるのが同じ手応えでは、武器を持ち替える
   * 意味が薄い。重さ（武器や技の崩し値）と、その一撃が相手に何を起こしたか
   * （受けられた／体勢を崩した／仕留めた）で強さを変える。
   */
  impact(target, dmg, opts, blocked) {
    const src = opts?.source;
    const mine = src === this.player;          // こちらが殴った
    const onMe = target === this.player;       // こちらが殴られた
    if (!mine && !onMe) return;                // 見えない所での殴り合いは揺らさない
    if (opts?.type === 'poison' || opts?.type === 'fire' || opts?.noFlinch) return;

    // ---- 重さ 0..1 ----
    // 武器の重量、または敵の技の崩し値から取る
    let weight;
    if (opts?.impactWeight !== undefined) weight = opts.impactWeight;
    else if (opts?.poise) weight = clamp01((opts.poise - 8) / 70);
    else weight = 0.35;
    weight = clamp01(weight);

    // ---- その一撃が何を起こしたか ----
    const killed = target.dead || target.hp <= 0;
    const broke = target.state === 'stagger' && !blocked;
    const crit = opts?.type === 'critical';
    // 相手の体力に対してどれだけ削ったか
    const sev = clamp01(dmg / Math.max(1, target.maxHp * 0.16));

    let stop, shake, kick;
    if (blocked) { stop = 0.030 + weight * 0.030; shake = 0.10 + weight * 0.12; kick = 0.5 + weight * 0.6; }
    else if (crit) { stop = 0.16 + weight * 0.06; shake = 0.42; kick = 2.6; }
    else if (killed) { stop = 0.11 + weight * 0.07; shake = 0.26 + weight * 0.16; kick = 1.5 + weight * 1.2; }
    else if (broke) { stop = 0.10 + weight * 0.07; shake = 0.28 + weight * 0.18; kick = 1.8 + weight * 1.4; }
    else { stop = 0.026 + weight * 0.060 + sev * 0.030; shake = 0.09 + weight * 0.16 + sev * 0.10; kick = 0.6 + weight * 1.5 + sev * 0.8; }

    // 殴られた側の視点は、殴った側より大きく揺れる
    if (onMe) { shake *= 1.5; kick *= 1.5; stop *= 0.8; }

    this.time.hitstop(stop);
    this.camera.shake(shake);
    if (src) this.camera.kick(target.x - src.x, target.z - src.z, kick * 0.28);

    // ---- 音 ----
    if (blocked) return;                        // 受けの音は takeDamage 側で鳴る
    const mat = onMe ? 'armor' : (target.arch?.mat || target.mat || 'flesh');
    const pitch = onMe ? 0.82 : (1.12 - weight * 0.26);
    // 自分が受けた音は中央で、他者に当てた音はその者の場所から
    const at = onMe ? {} : { x: target.x, z: target.z };
    this.audio.play(`hit_${mat}`, { weight, pitch, ...at });
    if (opts?.keen) this.audio.play('keen', { weight, delay: 0.02, pitch: 1 + weight * 0.12, ...at });
    if (broke) this.audio.play('poise_break', { delay: 0.03, ...at });
    if (killed && !onMe) this.audio.play('kill_blow', { delay: 0.05, ...at });
  }

  /**
   * 街道での襲撃。
   *
   * 賊が道の者を狙い始めた瞬間に一度だけ知らせる。遠くの喧嘩が
   * 自分と関係あるものになるかどうかは、気づけるかどうかで決まる。
   */
  onRoadAmbush(enemy, victim) {
    const p = this.player;
    const d = Math.hypot(enemy.x - p.x, enemy.z - p.z);
    if (d > 140) return;
    const van = victim.caravan;
    if (van) {
      if (van.raided) return;
      van.raided = true;
      van.raidT = 0;
      this.ui.toast('街道で悲鳴が上がった', 'gold');
    } else {
      if (victim.raided) return;
      victim.raided = true;
      this.ui.toast(`${victim.npcName}が襲われている`, 'gold');
    }
    this.audio.play('aggro', { pitch: 0.7, x: enemy.x, z: enemy.z });
  }

  /**
   * 衛兵が賊を見つけた。
   * 同じ喧嘩で何度も叫ばせない。遠くの持ち場での発見は知らせない。
   */
  onWatchAlarm(watchman, foe) {
    if (Math.hypot(watchman.x - this.player.x, watchman.z - this.player.z) > 90) return;
    if (this.time.now - (this._alarmT ?? -99) < 8) return;
    this._alarmT = this.time.now;
    this.ui.toast(`${watchman.npcName}「賊だ！ 備えろ！」`, 'gold');
    this.audio.play('aggro', { pitch: 0.75, x: watchman.x, z: watchman.z });
    void foe;
  }

  /**
   * 加勢の礼。
   * 襲われた隊商のそばで賊が絶えたら、親方が礼を寄越す。
   */
  checkRescue() {
    for (const van of this.travellers.caravans) {
      if (!van.raided || van.rewarded) continue;
      const hostile = this.enemies.some((e) => !e.dead && e.aggro
        && Math.hypot(e.x - van.x, e.z - van.z) < 70);
      if (hostile) continue;
      // プレイヤーが近くにいて、実際に手を貸したときだけ
      const near = Math.hypot(this.player.x - van.x, this.player.z - van.z) < 60;
      van.rewarded = true;
      if (!near || !van.helped) continue;
      const echo = 600 + Math.floor(Math.random() * 500);
      this.player.echo += echo;
      this.player.addItem('herb', 2);
      this.ui.itemGain('herb', 2);
      this.ui.toast(`${van.merchant.npcName}「助かった。……これは礼だ」　残響 +${echo}`, 'big gold');
      this.audio.play('levelup');
    }
  }

  onActorDeath(actor, opts) {
    if (actor instanceof Enemy && !actor.boss) this.quests.onKill(actor.archetype);
    // 加勢したかどうかは「誰が斬ったか」で決まる。
    // 護衛が自力で追い払ったのに礼を受け取るのは違う
    if (actor instanceof Enemy && opts?.source === this.player) {
      for (const van of this.travellers.caravans) {
        if (!van.raided) continue;
        if (Math.hypot(actor.x - van.x, actor.z - van.z) < 70) van.helped = true;
      }
      this.raids?.onKill(actor);
    }
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
    const P = this._prof;
    if (P) P._t = performance.now();

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

    // 地形の散布物。
    //
    // ここが描画準備でいちばん重い（可視 248 チャンク・548 種ぶん）。
    // Map を毎フレーム分割代入で舐めると 1 件 60ns（配列とイテレータの割り当て）、
    // さらに batchFor の文字列引きが 1 件 27ns 掛かる。どちらもチャンクごとに
    // 一度決まれば変わらないので、初めて描くときに [Batch, データ] の平坦な配列に
    // 焼いて持たせる。チャンクは LOD が変われば作り直されるので古い対応は残らない。
    const vis = this.terrain.visible;
    for (let i = 0; i < vis.length; i++) {
      const c = vis[i];
      if (!c.props) continue;
      if (!frustum.sphere(c.center[0], c.center[1], c.center[2], c.radius + 8)) continue;
      let list = c.emitList;
      if (!list) {
        list = c.emitList = [];
        for (const [model, data] of c.props) {
          const b = renderer.batchFor(model);
          if (b) list.push(b, data);
        }
      }
      for (let k = 0; k < list.length; k += 2) list[k].append(list[k + 1]);
    }
    if (P) this._mark('emit:地形散布');

    // POI の建造物
    for (const poi of this.pois) {
      const ddx = poi.x - px, ddz = poi.z - pz;
      const d = ddx * ddx + ddz * ddz;      // 平方のまま比べる
      if (d > 300 * 300) continue;
      for (const s of poi.structures) {
        if (!frustum.sphere(s.x, s.y + 3, s.z, 12)) continue;
        const b = renderer.batchFor(s.model);
        if (!b) continue;
        const o = b.alloc();
        writeInstance(b.data, o, s.x, s.y, s.z, s.rotY, s.sx, s.sy, s.sz,
          s.r, s.g, s.b, 1, s.wind, s.emissive, 0, 0.5);
      }
      // 宝箱
      if (poi.chest && !this.openedChests.has(poi.id) && d < 200 * 200) {
        const b = renderer.batchFor('chest');
        if (b) {
          const o = b.alloc();
          const cy = this.world.height(poi.chest.x, poi.chest.z);
          writeInstance(b.data, o, poi.chest.x, cy, poi.chest.z, 0, 1, 1, 1,
            1, 0.95, 0.7, 1, 0, 0.12 + Math.sin(time * 2) * 0.05, 0, 0.5);
        }
      }
    }
    if (P) this._mark('emit:POI');

    // 鳥
    {
      const bb = renderer.batchFor('bird');
      if (bb) {
        this.wildlife.forEach((bird, flying) => {
          const o = bb.alloc();
          // 羽ばたきは翼（X）を振り、胴（Y）を逆位相で沈ませる。
          // 地に降りているあいだは翼をたたむ
          const f = Math.sin(bird.flap);
          const wing = flying ? 0.55 + f * 0.45 : 0.30;
          const body = flying ? 1 - f * 0.10 : 1;
          writeInstance(bb.data, o, bird.x, bird.y, bird.z, bird.yaw,
            wing, body, 1, 1, 1, 1, 1, 0, 0, 0, 0.5);
        });
      }
    }

    // 魚。水面下は水越しに見えるので、少し暗く沈ませる
    {
      const fb = renderer.batchFor('fish');
      if (fb) {
        this.fish.forEach((f, out) => {
          const o = fb.alloc();
          const w = Math.sin(f.wag);
          const k = out ? 1 : 0.55;              // 水中は暗く
          writeInstance(fb.data, o, f.x, f.y, f.z, f.yaw,
            1 + w * 0.22, 1 - Math.abs(w) * 0.08, 1,
            k, k * 1.02, k * 1.1, out ? 1 : 0.72, 0, out ? 0.15 : 0, 0, 0.5);
        });
      }
    }
    if (P) this._mark('emit:鳥と魚');

    // 釣り：糸と浮き
    // 線分は yaw しか回せないので、糸は小さな玉を並べて見せている
    if (this.fishing.active) {
      const b = renderer.batchFor('ball');
      if (b) {
        const f = this.fishing;
        const bob = f.bob;
        const wave = Math.sin(time * 2.1 + bob.x * 0.3) * 0.05;
        const by = this.seaLevel + 0.06 + wave - f.dip;
        const sag = f.state === 'fight' && f.pulling ? 0.06 : 0.34;
        for (let i = 1; i <= 6; i++) {
          const u = i / 7;
          const o = b.alloc();
          writeInstance(b.data, o,
            lerp(f.from.x, bob.x, u),
            lerp(f.from.y, by + 0.1, u) - Math.sin(u * Math.PI) * sag,
            lerp(f.from.z, bob.z, u), 0,
            0.035, 0.035, 0.035, 0.95, 0.95, 0.9, 0.8, 0, 0.05, 0, 0.5);
        }
        const o = b.alloc();
        const hot = f.state === 'bite' || (f.state === 'fight' && f.strain > 0.72);
        writeInstance(b.data, o, bob.x, by, bob.z, 0, 0.13, 0.16, 0.13,
          1, hot ? 0.34 : 0.58, hot ? 0.28 : 0.46, 1, 0, hot ? 0.95 : 0.30, 0, 0.5);
      }
    }

    // 採取物
    for (const g of this.gatherables.values()) {
      if (!g || this.harvested.has(g.key)) continue;
      const model = g.type === 'ore_iron' || g.type === 'crystal' ? 'crystal' : 'bush_1';
      const b = renderer.batchFor(model);
      if (!b) continue;
      const o = b.alloc();
      const c = GATHER_COLOR[g.type] || GATHER_FALLBACK;
      writeInstance(b.data, o, g.x, g.y, g.z, g.key % 6, 0.6, 0.7, 0.6,
        c[0], c[1], c[2], 1, g.type === 'herb' || g.type === 'blood_flower' ? 0.6 : 0,
        0.35 + Math.sin(time * 1.7 + g.key) * 0.12, 0.55, 0.5);
    }
    if (P) this._mark('emit:採取物');

    // アクター。届く範囲かどうかしか見ないので、距離は平方のまま
    const near2 = (x, z, r) => { const dx = x - px, dz = z - pz; return dx * dx + dz * dz <= r * r; };
    p.emit(renderer, time, this);
    {
      const dx = this.mount.x - px, dz = this.mount.z - pz;
      if (dx * dx + dz * dz < 140 * 140) this.mount.emit(renderer, time, this);
    }
    for (const e of this.enemies) {
      if (!near2(e.x, e.z, 140)) continue;
      if (!frustum.sphere(e.x, e.y + e.height * 0.5, e.z, e.height)) continue;
      e.emit(renderer, time, this);
    }
    if (P) this._mark('emit:自機と敵');
    for (const t of this.travellers.everyone()) {
      if (!near2(t.x, t.z, 200)) continue;
      if (!frustum.sphere(t.x, t.y + t.height * 0.5, t.z, t.height)) continue;
      t.emit(renderer, time, this);
    }
    // 隊商の荷車と、積荷と、それを牽く駄馬
    for (const c of this.travellers.caravans) {
      if (!near2(c.cartX, c.cartZ, 220)) continue;
      const s = CART_SCALE;
      const b = renderer.batchFor('cart');
      if (b) {
        const o = b.alloc();
        writeInstance(b.data, o, c.cartX, c.cartY, c.cartZ, c.yaw, s, s, s,
          0.52, 0.40, 0.28, 1, 0, 0, 0, 0.5);
      }
      // 積荷は荷車と同じ座標に重ねる。村に置かれた空の荷車と分けるため別モデル
      const lb = renderer.batchFor('cart_load');
      if (lb) {
        const o = lb.alloc();
        writeInstance(lb.data, o, c.cartX, c.cartY, c.cartZ, c.yaw, s, s, s,
          0.86, 0.82, 0.76, 1, 0, 0, 0, 0.5);
      }
      if (c.dray) c.dray.emit(renderer, time, this);
    }
    if (P) this._mark('emit:旅人');
    for (const w of this.watch) {
      if (!near2(w.x, w.z, 140)) continue;
      if (!frustum.sphere(w.x, w.y + w.height * 0.5, w.z, w.height)) continue;
      w.emit(renderer, time, this);
    }
    for (const n of this.npcs) {
      if (n.indoors) continue;
      if (!near2(n.x, n.z, 90)) continue;
      if (!frustum.sphere(n.x, n.y + n.height * 0.5, n.z, n.height)) continue;
      n.emit(renderer, time, this);
      if (n.dead) continue;                 // 倒れた者は印を出さない
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
    if (P) this._mark('emit:村人');

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
    if (P) this._mark('emit:飛翔体と印');
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
        if (this.player.reached.has(poi.id)) poi.reached = true;
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
  // 第24回で足した鉱脈。色を書き忘れていて、これらが視界に入るたびに
  // 描画が例外で中断し、それ以降のアクター・旅人・鳥が出ていなかった
  ore_silver: [0.86, 0.88, 0.92], shard_ancient: [0.95, 0.82, 0.45],
  bone_ash: [0.72, 0.70, 0.66],
};

/** 色を書き忘れても描画を止めない */
const GATHER_FALLBACK = [0.8, 0.8, 0.8];

function frame() {
  return new Promise((r) => requestAnimationFrame(() => r()));
}

export { clamp, clamp01, lerp, v3, CHUNK, makeRng, NPC };
