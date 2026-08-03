// アクター基底：物理・状態機械・ダメージ処理

import { m4, quat, v3, clamp, clamp01, lerp, angDelta, rotateTowards, TAU } from '../core/math.js';
import { Pose, poseActor, RIGS } from '../render/rig.js';
import { writeInstanceMat } from '../render/instance.js';

const _q = quat.new();
const _t = v3.new();
const _s = v3.new(1, 1, 1);
const _m = m4.new();
const _m2 = m4.new();
const _tmp = [0, 0];
const _tip = v3.new();

export const TEAM = { PLAYER: 0, ENEMY: 1, NEUTRAL: 2 };

/**
 * 足の運び。
 *
 * 積もった雪は足を取り、濡れた地面は蹴りが逃げる。
 * 地下には天候が無いので効かない。踏むのは敵も同じ。
 */
export function footing(game) {
  if (!game || game.dungeon || !game.sky) return 1;
  const snow = game.sky.snowCover || 0;
  const wet = game.sky.wetness || 0;
  return 1 - snow * 0.22 - wet * 0.07;
}

let nextId = 1;

export class Actor {
  constructor(opts = {}) {
    this.id = nextId++;
    this.name = opts.name || '';
    this.rigName = opts.rig || 'humanoid';
    this.pose = new Pose(this.rigName);
    this.scale = opts.scale || 1;
    this.tint = opts.tint || [0.7, 0.7, 0.75];
    this.emissive = opts.emissive || 0;
    this.team = opts.team ?? TEAM.ENEMY;

    this.x = opts.x || 0; this.y = opts.y || 0; this.z = opts.z || 0;
    this.vx = 0; this.vy = 0; this.vz = 0;
    this.yaw = opts.yaw || 0;
    this.grounded = true;
    this.radius = (opts.radius || 0.42) * this.scale;
    this.height = (opts.height || 1.8) * this.scale;

    this.maxHp = opts.hp || 100;
    this.hp = this.maxHp;
    this.maxPoise = opts.poise || 20;
    this.poise = this.maxPoise;
    this.poiseTimer = 0;
    this.def = opts.def || 0;

    this.speed = opts.speed || 3.2;
    this.runSpeed = opts.runSpeed || 5.2;

    this.state = 'idle';
    this.animT = 0;
    this.motion = 'slash_r';
    this.motionDur = 0.5;
    this.gait = 0;
    this.speed01 = 0;
    this.blocking = false;
    this.dead = false;
    this.deathT = 0;
    this.removeAt = 0;

    this.attack = null;
    this.attackHits = null;
    this.hitFlash = 0;
    this.iframes = 0;
    /**
     * 被弾直後の猶予（秒）。
     * これが無いと、複数の敵に囲まれた瞬間にハメ殺される。
     * プレイヤーだけに与える。
     */
    this.hitGrace = opts.hitGrace || 0;
    /** 体勢崩し直後の猶予。連続で崩されてハメられるのを防ぐ */
    this.staggerImmune = 0;
    this.weaponModel = opts.weapon || null;
    this.weaponTint = opts.weaponTint || [0.8, 0.82, 0.86];
    this.weaponScale = opts.weaponScale || 1;
    this.shieldModel = opts.shield ? 'w_shield' : null;

    this.status = { bleed: 0, poison: 0, frost: 0, fire: 0 };
    this.statusTimers = { poison: 0, burn: 0 };
    this.knock = { x: 0, z: 0, t: 0 };
    this.floating = !!RIGS[this.rigName]?.float;
    this.animExtra = null;
    this.rootMat = m4.new();
    this.bodyYaw = this.yaw;
  }

  get alive() { return !this.dead; }
  get eyeY() { return this.y + this.height * 0.85; }

  setState(state, dur = 0.4, motion = null) {
    this.state = state;
    this.animT = 0;
    this.motionDur = dur;
    if (motion) this.motion = motion;
  }

  canAct() {
    return !this.dead && this.state !== 'attack' && this.state !== 'roll' &&
      this.state !== 'hit' && this.state !== 'stagger' && this.state !== 'cast' &&
      this.state !== 'drink' && this.state !== 'backstep' && this.state !== 'parry';
  }

  /* ------------------------------------------------------------ 物理 */
  /**
   * 着地。落ちた勢いに応じて土煙と音を出す。
   * 高さの実感は、落ちている間ではなく着いた瞬間に出る。
   */
  onLand(impact, game) {
    if (!game) return;
    const k = clamp01((impact - 4.0) / 14);
    const s = game.surfaceAt(this.x, this.z);
    game.audio.footstep(this.wading ? 'water' : s.surface, 0.30 + k * 1.0,
      this.team === TEAM.PLAYER ? null : this);
    // 土煙と揺れは、はっきり落ちたときだけ（1.8m 以上）
    if (impact > 9) {
      game.fx.dust(this.x, this.y + 0.05, this.z, 3 + Math.floor(k * 12));
    }
    if (this.team === TEAM.PLAYER) {
      game.camera.drop(0.05 + k * 0.42);
      if (k > 0.35) game.camera.shake(k * 0.5);
    }
  }

  moveOnGround(dt, game, dx, dz, speed) {
    const len = Math.hypot(dx, dz);
    if (len > 1e-4) {
      dx /= len; dz /= len;
      const step = speed * dt;
      const nx = this.x + dx * step;
      const nz = this.z + dz * step;
      // 坂は登るほど遅く、下るほど少し速い。
      // 平地と同じ速さで斜面を駆け上がれると、地形の起伏が意味を失う
      const grade = (game.groundHeight(nx, nz) - game.groundHeight(this.x, this.z))
        / Math.max(step, 1e-4);
      let k = grade > 0 ? 1 / (1 + grade * 1.15) : 1 + Math.min(0.20, -grade * 0.22);
      this.slopeK = k;
      k *= footing(game);
      this.tryMove(game, this.x + dx * step * k, this.z + dz * step * k);
    }
  }

  /**
   * 登れない急斜面では踏ん張れず、滑り落ちる。
   * 壁のように止められるより、落ちたほうが世界が続いて見える。
   */
  updateSlide(dt, game) {
    if (!this.grounded || this.swimming || this.floating || game.dungeon) {
      this.sliding = 0;
      return;
    }
    const h = game.groundHeight(this.x, this.z);
    const gx = (game.groundHeight(this.x + 0.6, this.z) - game.groundHeight(this.x - 0.6, this.z)) / 1.2;
    const gz = (game.groundHeight(this.x, this.z + 0.6) - game.groundHeight(this.x, this.z - 0.6)) / 1.2;
    const g = Math.hypot(gx, gz);
    void h;
    // 濡れた斜面・雪の斜面は、乾いていれば踏ん張れる角度でも滑り出す
    const wet = game.sky ? (game.sky.wetness || 0) : 0;
    const snow = game.sky ? (game.sky.snowCover || 0) : 0;
    const hold = 1.15 - wet * 0.16 - snow * 0.22;
    if (g < hold) { this.sliding = 0; return; }
    const k = Math.min(1, (g - hold) / 0.85);
    this.sliding = k;
    const sp = (2.2 + k * 6.5) * (1 + wet * 0.22 + snow * 0.30);
    this.tryMove(game, this.x - (gx / g) * sp * dt, this.z - (gz / g) * sp * dt);
  }

  tryMove(game, nx, nz) {
    // 急斜面は登れない
    const h0 = game.groundHeight(this.x, this.z);
    const h1 = game.groundHeight(nx, nz);
    const dist = Math.hypot(nx - this.x, nz - this.z) || 1e-4;
    const grade = (h1 - h0) / dist;
    if (grade > 1.35) {
      // 壁沿いに滑らせる
      const sx = nx - this.x, sz = nz - this.z;
      const hx = game.groundHeight(this.x + sx, this.z);
      const hz = game.groundHeight(this.x, this.z + sz);
      if ((hx - h0) / (Math.abs(sx) || 1e-4) <= 1.35) { nz = this.z; }
      else if ((hz - h0) / (Math.abs(sz) || 1e-4) <= 1.35) { nx = this.x; }
      else return false;
    }
    // 障害物
    if (game.collide(nx, nz, this.radius, _tmp)) {
      nx = _tmp[0]; nz = _tmp[1];
    }
    // 世界の縁（屋外のみ）
    if (!game.dungeon) {
      const d = Math.hypot(nx, nz);
      if (d > game.worldRadius) {
        nx = (nx / d) * game.worldRadius;
        nz = (nz / d) * game.worldRadius;
      }
    }
    this.x = nx; this.z = nz;
    return true;
  }

  updatePhysics(dt, game) {
    const gh = game.groundHeight(this.x, this.z);
    // 水深（足元から水面まで）。地下では水は無い
    const prevWade = this.waterDepth || 0;
    this.waterDepth = game.dungeon ? 0 : Math.max(0, game.seaLevel - gh);
    this.wading = this.waterDepth > 0.25;
    this.swimming = this.waterDepth > this.height * 0.82;
    // 入水・出水の瞬間だけ大きく跳ねさせる
    if (game.onWaterCross && (prevWade > 0.25) !== this.wading) {
      game.onWaterCross(this, this.wading);
    }
    const target = this.floating ? gh + 0.65 : gh;
    if (this.swimming) {
      // 泳いでいるあいだは重力ではなく浮力で位置が決まる。
      // 落下処理と綱引きさせると、水面より下で釣り合ってしまう
      const surf = game.seaLevel - this.height * 0.30;
      this.y = lerp(this.y, surf, 1 - Math.exp(-6 * dt));
      this.vy = 0;
      this.grounded = true;
    } else if (this.floating) {
      this.y = lerp(this.y, target, 1 - Math.exp(-6 * dt));
      this.grounded = true;
    } else if (this.y > target + 0.02 || this.vy > 0) {
      this.vy -= 22 * dt;
      this.y += this.vy * dt;
      if (this.y <= target) {
        const impact = -this.vy;
        this.y = target; this.vy = 0; this.grounded = true;
        // 1.4 m/s は 4cm の段差でも出る。歩いているだけで鳴らないよう、
        // 36cm ぶん（4.0 m/s）落ちてからにする
        if (impact > 4.0) this.onLand(impact, game);
        if (impact > 14 && this.team === TEAM.PLAYER) {
          this.takeDamage(Math.min(this.maxHp * 0.9, (impact - 14) * 14), { type: 'fall' }, game);
        }
      } else {
        this.grounded = false;
      }
    } else {
      this.y = target;
      this.grounded = true;
    }

    // ノックバック
    if (this.knock.t > 0) {
      const k = Math.min(dt, this.knock.t);
      this.tryMove(game, this.x + this.knock.x * k, this.z + this.knock.z * k);
      this.knock.t -= dt;
    }
  }

  updateStatus(dt, game) {
    for (const k of ['bleed', 'poison', 'frost', 'fire']) {
      this.status[k] = Math.max(0, this.status[k] - dt * 6);
    }
    if (this.statusTimers.poison > 0) {
      this.statusTimers.poison -= dt;
      this._poisonAcc = (this._poisonAcc || 0) + dt;
      if (this._poisonAcc > 1) {
        this._poisonAcc = 0;
        this.takeDamage(this.maxHp * 0.014 + 4, { type: 'poison', noFlinch: true }, game);
      }
    }
    if (this.statusTimers.burn > 0) {
      this.statusTimers.burn -= dt;
      this._burnAcc = (this._burnAcc || 0) + dt;
      if (this._burnAcc > 0.5) {
        this._burnAcc = 0;
        this.takeDamage(this.maxHp * 0.012 + 6, { type: 'fire', noFlinch: true }, game);
        game.fx.emberBurst(this.x, this.y + this.height * 0.5, this.z, 3);
      }
    }
    this.hitFlash = Math.max(0, this.hitFlash - dt * 3.5);
    this.iframes = Math.max(0, this.iframes - dt);
    this.staggerImmune = Math.max(0, this.staggerImmune - dt);
    if (this.poiseTimer > 0) {
      this.poiseTimer -= dt;
      if (this.poiseTimer <= 0) this.poise = this.maxPoise;
    }
  }

  /* ---------------------------------------------------------- 被ダメ */
  takeDamage(amount, opts = {}, game) {
    if (this.dead) return 0;
    if (this.iframes > 0 && !opts.ignoreIFrames && opts.type !== 'poison' && opts.type !== 'fire') return 0;

    let dmg = amount;
    if (opts.type !== 'poison' && opts.type !== 'fire' && opts.type !== 'fall') {
      dmg = amount * (120 / (120 + this.def));
    }
    if (this.defBuff) dmg *= this.defBuff;

    // 受け（盾）
    let blocked = false;
    if (this.blocking && opts.source && !opts.guardBreak) {
      const dx = opts.source.x - this.x, dz = opts.source.z - this.z;
      const ang = Math.atan2(dx, dz);
      if (Math.abs(angDelta(this.yaw, ang)) < 1.15) {
        blocked = true;
        const shield = this.shieldData || { block: 0.6, stability: 30 };
        const staminaCost = Math.max(6, dmg * 0.42 * (1 - shield.stability / 140));
        if (this.stamina !== undefined && this.stamina < staminaCost) {
          // ガードブレイク
          this.stamina = 0;
          this.setState('stagger', 1.1);
          this.blocking = false;
          if (game) game.fx.sparks(this.x, this.y + 1.0, this.z, 14, [1, 0.9, 0.6]);
          if (game) game.audio.play('guardbreak', this.sfxAt());
        } else {
          if (this.stamina !== undefined) this.stamina -= staminaCost;
          dmg *= 1 - shield.block;
          if (game) {
            game.fx.sparks(this.x + dx * 0.02, this.y + 1.1, this.z + dz * 0.02, 8, [1, 0.95, 0.7]);
            game.audio.play('block', this.sfxAt());
          }
        }
      }
    }

    dmg = Math.max(1, Math.round(dmg));
    this.hp -= dmg;
    this.hitFlash = 1;
    // 立て続けの被弾に短い猶予を挟む（囲まれてのハメ殺し防止）
    if (this.hitGrace > 0 && !opts.noFlinch && opts.type !== 'poison' && opts.type !== 'fire') {
      this.iframes = Math.max(this.iframes, this.hitGrace);
    }

    if (opts.bleed) this.status.bleed = Math.min(100, this.status.bleed + opts.bleed);
    if (opts.frost) this.status.frost = Math.min(100, this.status.frost + opts.frost);
    if (opts.poison) this.status.poison = Math.min(100, this.status.poison + opts.poison);
    if (opts.fire) { this.status.fire = Math.min(100, this.status.fire + opts.fire); }

    if (this.status.bleed >= 100) {
      this.status.bleed = 0;
      this.hp -= this.maxHp * 0.13 + 60;
      if (game) game.fx.blood(this.x, this.y + this.height * 0.6, this.z, 40);
    }
    if (this.status.fire >= 100) { this.status.fire = 0; this.statusTimers.burn = 6; }
    if (this.status.poison >= 100) { this.status.poison = 0; this.statusTimers.poison = 18; }
    if (this.status.frost >= 100) {
      this.status.frost = 0;
      this.hp -= this.maxHp * 0.08 + 30;
      this.frostSlow = 6;
    }

    // 体勢（ポイズ）
    if (!blocked && !opts.noFlinch) {
      const poiseDmg = opts.poise || dmg * 0.5;
      this.poise -= poiseDmg;
      this.poiseTimer = 2.4;
      // 体勢を崩された直後は、続けざまに崩されない。
      // これが無いと、崩し値の高い相手に一度捕まった時点で
      // 硬直が上書きされ続け、二度と動けなくなる
      if (this.poise <= 0 && this.staggerImmune > 0) {
        this.poise = this.maxPoise * 0.5;
      } else if (this.poise <= 0) {
        this.poise = this.maxPoise;
        this.poiseTimer = 0;
        this.staggerImmune = this.team === TEAM.PLAYER ? 2.2 : 1.2;
        this.setState('stagger', this.boss ? 1.4 : 1.0);
        if (opts.source) {
          const dx = this.x - opts.source.x, dz = this.z - opts.source.z;
          const l = Math.hypot(dx, dz) || 1;
          this.knock = { x: (dx / l) * 3.2, z: (dz / l) * 3.2, t: 0.25 };
        }
      } else if (this.state !== 'stagger' && this.state !== 'attack' && !this.superArmor) {
        this.setState('hit', 0.34);
      } else if (this.state === 'attack' && !this.superArmor && !this.boss && Math.random() < 0.35) {
        this.setState('hit', 0.34);
      }
    }

    if (game) {
      game.fx.hitSpark(this, dmg, blocked, opts);
      game.onDamage?.(this, dmg, opts, blocked);
    }

    if (this.hp <= 0) {
      this.hp = 0;
      this.die(game, opts);
    }
    return dmg;
  }

  die(game, opts) {
    if (this.dead) return;
    this.dead = true;
    this.deathT = 0;
    this.setState('death', 1.2);
    this.blocking = false;
    this.attack = null;
    this.onDeath?.(game, opts);
    game?.onActorDeath?.(this, opts);
  }

  /**
   * 音を鳴らす位置。
   *
   * 自分の所作は中央で鳴らす（自分の剣は自分の耳のすぐ横にある）。
   * 他者の音は、その者が立っている場所から聴こえるようにする。
   */
  sfxAt() { return this.team === TEAM.PLAYER ? {} : { x: this.x, z: this.z }; }

  /* ------------------------------------------------------- 攻撃の進行 */
  startAttack(def, game) {
    this.attack = def;
    this.attackHits = new Set();
    this.attackApplied = false;
    this.setState('attack', def.windup + def.active + def.recover, def.motion);
    this.attackDashDone = false;
    game?.audio.play('swing', { pitch: 0.9 + Math.random() * 0.2, ...this.sfxAt() });
  }

  attackPhase() {
    const a = this.attack;
    if (!a) return 'none';
    if (this.animT < a.windup) return 'windup';
    if (this.animT < a.windup + a.active) return 'active';
    return 'recover';
  }

  /* --------------------------------------------------- アニメーション */
  updateAnim(dt, moveSpeed) {
    this.animT += dt;
    const target = clamp01(moveSpeed / this.runSpeed);
    this.speed01 = lerp(this.speed01, target, 1 - Math.exp(-14 * dt));
    this.gait += dt * (4.5 + this.speed01 * 6.5) * (0.4 + this.speed01);
    if (this.gait > TAU * 64) this.gait -= TAU * 64;

    if (this.state === 'death') {
      this.deathT += dt;
    } else if (this.animT >= this.motionDur &&
      ['attack', 'roll', 'hit', 'stagger', 'cast', 'drink', 'backstep', 'parry'].includes(this.state)) {
      this.state = 'idle';
      this.attack = null;
    }
  }

  /* -------------------------------------------------------- 描画出力 */
  /** ポーズを解いてインスタンスを書き出す */
  emit(renderer, time, game) {
    const rig = this.pose.rig;
    const st = {
      state: this.dead ? 'death' : this.state,
      animT: this.dead ? this.deathT : this.animT,
      motion: this.motion,
      motionDur: this.motionDur,
      speed01: this.speed01,
      gait: this.gait,
      blocking: this.blocking,
      gallop: this.riding ? (this.riding.galloping ? 1 : 0.45) : 0,
      t: time + this.id * 0.7,
    };
    const extra = poseActor(this.pose, st);
    this.animExtra = extra;

    let yaw = this.yaw;
    if (extra?.spinTurn) yaw += extra.spinTurn;
    let tilt = 0;
    if (extra?.tumble) tilt = extra.tumble;

    quat.fromEuler(_q, tilt, yaw, 0);
    let y = this.y;
    if (extra?.airborne) y += extra.airborne * 1.2 * this.scale;
    v3.set(_t, this.x, y, this.z);
    v3.set(_s, this.scale, this.scale, this.scale);
    m4.fromRTS(this.rootMat, _q, _t, _s);
    this.pose.solve(this.rootMat);

    const flash = this.hitFlash;
    const r = this.tint[0], g = this.tint[1], b = this.tint[2];
    const parts = rig.parts;
    const world = this.pose.world;

    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (!p.shape) continue;
      const batch = renderer.batchFor(p.shape);
      if (!batch) continue;
      const o = batch.alloc();
      quat.identity(_q);
      v3.set(_t, p.center[0], p.center[1], p.center[2]);
      v3.set(_s, p.size[0], p.size[1], p.size[2]);
      m4.fromRTS(_m, _q, _t, _s);
      m4.mul(_m2, world[i], _m);
      const sh = p.shade;
      writeInstanceMat(batch.data, o, _m2,
        r * sh + flash * 0.9, g * sh + flash * 0.55, b * sh + flash * 0.55,
        this.alpha ?? 1, 0, this.emissive + flash * 0.5, 0);
    }

    // 装備の追加パーツ（兜・肩当て・マント）
    if (this.extras) {
      for (const ex of this.extras) {
        const idx = rig.index.get(ex.joint);
        if (idx === undefined) continue;
        const batch = renderer.batchFor(ex.shape);
        if (!batch) continue;
        let rx = ex.rot ? ex.rot[0] : 0;
        let rz = ex.rot ? ex.rot[2] : 0;
        if (ex.cloth) {
          // 走ると後ろへなびき、微かに揺れる
          const sway = Math.sin(time * 3.1 + this.id + ex.seg * 0.7) * 0.10;
          rx += -0.20 - this.speed01 * (0.55 + ex.seg * 0.32) + sway * (0.4 + ex.seg * 0.3);
          rz += sway * 0.5;
        }
        quat.fromEuler(_q, rx, ex.rot ? ex.rot[1] : 0, rz);
        v3.set(_t, ex.offset[0], ex.offset[1], ex.offset[2]);
        v3.set(_s, ex.size[0], ex.size[1], ex.size[2]);
        m4.fromRTS(_m, _q, _t, _s);
        m4.mul(_m2, world[idx], _m);
        const t = ex.tint;
        // alloc() がバッファを再確保しうるので、先にオフセットを取ってから data を参照する
        const eo = batch.alloc();
        writeInstanceMat(batch.data, eo, _m2,
          t[0] + flash * 0.9, t[1] + flash * 0.5, t[2] + flash * 0.5,
          1, 0, (ex.emissive || 0) + flash * 0.5, 1);
      }
    }

    // 武器の軌跡
    if (game && this.weaponModel && this.state === 'attack' && this.attackPhase() === 'active') {
      this.weaponTip(_tip);
      const mount = rig.weaponMount ? rig.index.get(rig.weaponMount) : undefined;
      const hw = mount !== undefined ? this.pose.world[mount] : null;
      const bx = hw ? hw[12] : this.x, by = hw ? hw[13] : this.y + 1, bz = hw ? hw[14] : this.z;
      if (this._trail) {
        game.fx.weaponTrail(this._trail[0], this._trail[1], this._trail[2],
          _tip[0], _tip[1], _tip[2], bx, by, bz,
          this.trailColor || [0.85, 0.92, 1.0], this.scale);
      } else {
        this._trail = [0, 0, 0];
      }
      this._trail[0] = _tip[0]; this._trail[1] = _tip[1]; this._trail[2] = _tip[2];
    } else {
      this._trail = null;
    }

    // 武器・盾
    if (this.weaponModel && rig.weaponMount && !this.hideWeapon) {
      const idx = rig.index.get(rig.weaponMount);
      if (idx !== undefined) {
        const batch = renderer.batchFor(this.weaponModel);
        if (batch) {
          const o = batch.alloc();
          quat.fromEuler(_q, 1.25, 0, 0);
          v3.set(_t, 0, -0.06, 0.02);
          const ws = this.weaponScale;
          v3.set(_s, ws, ws, ws);
          m4.fromRTS(_m, _q, _t, _s);
          m4.mul(_m2, world[idx], _m);
          writeInstanceMat(batch.data, o, _m2,
            this.weaponTint[0], this.weaponTint[1], this.weaponTint[2],
            1, 0, this.weaponGlow || 0, 0);
        }
      }
    }
    if (this.shieldModel && rig.shieldMount) {
      const idx = rig.index.get(rig.shieldMount);
      if (idx !== undefined) {
        const batch = renderer.batchFor(this.shieldModel);
        if (batch) {
          const o = batch.alloc();
          quat.fromEuler(_q, 1.5, 0, 0);
          v3.set(_t, 0, -0.12, 0.1);
          v3.set(_s, 1, 1, 1);
          m4.fromRTS(_m, _q, _t, _s);
          m4.mul(_m2, world[idx], _m);
          const t = this.shieldTint || [0.6, 0.6, 0.65];
          writeInstanceMat(batch.data, o, _m2, t[0], t[1], t[2], 1, 0, 0, 0);
        }
      }
    }
  }

  /** 武器の刃先ワールド座標（エフェクト用） */
  weaponTip(out) {
    const rig = this.pose.rig;
    const idx = rig.weaponMount ? rig.index.get(rig.weaponMount) : undefined;
    if (idx === undefined) {
      out[0] = this.x + Math.sin(this.yaw) * this.radius * 2;
      out[1] = this.y + this.height * 0.6;
      out[2] = this.z + Math.cos(this.yaw) * this.radius * 2;
      return out;
    }
    const w = this.pose.world[idx];
    const len = 1.4 * this.weaponScale * this.scale;
    out[0] = w[12] + w[8] * len * 0.6 - w[4] * len * 0.2;
    out[1] = w[13] + w[9] * len * 0.6 - w[5] * len * 0.2;
    out[2] = w[14] + w[10] * len * 0.6 - w[6] * len * 0.2;
    return out;
  }

  /** 敵を向く */
  faceTowards(x, z, dt, rate = 8) {
    const ang = Math.atan2(x - this.x, z - this.z);
    this.yaw = rotateTowards(this.yaw, ang, rate * dt);
  }
}

/* ================================================== 攻撃判定ヘルパ */

/** 扇形の判定：攻撃者の前方 arc ラジアン、range 以内 */
export function inAttackArc(attacker, target, range, arc) {
  const dx = target.x - attacker.x, dz = target.z - attacker.z;
  const dy = (target.y + target.height * 0.5) - (attacker.y + attacker.height * 0.5);
  const dist = Math.hypot(dx, dz);
  if (dist > range + target.radius) return false;
  if (Math.abs(dy) > attacker.height * 0.9 + target.height * 0.6) return false;
  if (arc >= 6.2) return true;
  const ang = Math.atan2(dx, dz);
  return Math.abs(angDelta(attacker.yaw, ang)) <= arc / 2 + Math.atan2(target.radius, Math.max(dist, 0.2));
}

/** 背後判定（致命の一撃） */
export function isBehind(attacker, target, tolerance = 1.1) {
  const ang = Math.atan2(attacker.x - target.x, attacker.z - target.z);
  return Math.abs(angDelta(target.yaw + Math.PI, ang)) < tolerance;
}

export { clamp, clamp01, lerp };
