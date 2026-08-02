// 敵 AI とボス戦

import { Actor, TEAM, inAttackArc } from './actor.js';
import { ENEMIES, BOSSES, DROPS } from './data.js';
import { clamp, clamp01, lerp, angDelta, rotateTowards, TAU, v3 } from '../core/math.js';

const RIG_FOR = { humanoid: 'humanoid', beast: 'beast', imp: 'imp', wraith: 'wraith', dragon: 'dragon' };
const _tip = v3.new();

/** 敵の見た目を種別ごとに味付けする（フード・兜・肩当て・襤褸など） */
function buildEnemyExtras(def) {
  const ex = [];
  const t = def.tint;
  const dark = [t[0] * 0.45, t[1] * 0.45, t[2] * 0.48];
  const cloth = (i, tint, joint = 'pelvis', segs = 2) => {
    for (let k = 0; k < segs; k++) {
      ex.push({
        joint, shape: 'partSlim',
        offset: [0, -0.06 - k * 0.24, -0.14 - k * 0.03],
        size: [0.42 - k * 0.05, 0.28, 0.09],
        tint, cloth: true, seg: k + i,
      });
    }
  };
  switch (def.id) {
    case 'bandit':
      ex.push({ joint: 'head', shape: 'partSlim', offset: [0, 0.15, -0.03], size: [0.40, 0.36, 0.42], tint: dark });
      ex.push({ joint: 'head', shape: 'part', offset: [0, 0.05, 0.15], size: [0.28, 0.12, 0.12], tint: [0.5, 0.42, 0.3] });
      cloth(0, [t[0] * 0.7, t[1] * 0.62, t[2] * 0.55]);
      break;
    case 'archer':
      ex.push({ joint: 'head', shape: 'partSlim', offset: [0, 0.15, -0.03], size: [0.40, 0.34, 0.42], tint: dark });
      ex.push({ joint: 'chest', shape: 'partSlim', offset: [-0.14, 0.34, -0.18], size: [0.14, 0.42, 0.14], tint: [0.42, 0.30, 0.20], rot: [0.3, 0, 0.3] });
      cloth(0, [t[0] * 0.75, t[1] * 0.7, t[2] * 0.6], 'pelvis', 1);
      break;
    case 'knight':
      ex.push({ joint: 'head', shape: 'part', offset: [0, 0.14, -0.01], size: [0.42, 0.44, 0.42], tint: [t[0] * 1.1, t[1] * 1.1, t[2] * 1.12] });
      ex.push({ joint: 'head', shape: 'part', offset: [0, 0.11, 0.19], size: [0.28, 0.09, 0.10], tint: [0.05, 0.05, 0.07] });
      for (const s of ['shoulderL', 'shoulderR']) {
        ex.push({ joint: s, shape: 'part', offset: [0, -0.03, 0], size: [0.30, 0.22, 0.30], tint: [t[0] * 1.12, t[1] * 1.12, t[2] * 1.15] });
      }
      cloth(0, [0.42, 0.16, 0.18], 'pelvis', 3);
      break;
    case 'brute':
      for (const s of ['shoulderL', 'shoulderR']) {
        ex.push({ joint: s, shape: 'part', offset: [0, -0.04, 0], size: [0.34, 0.26, 0.34], tint: [0.30, 0.26, 0.24] });
        ex.push({ joint: s, shape: 'spike', offset: [0, 0.06, 0], size: [0.12, 0.20, 0.12], tint: [0.55, 0.5, 0.45] });
      }
      ex.push({ joint: 'head', shape: 'part', offset: [0, 0.12, 0.10], size: [0.34, 0.26, 0.24], tint: [0.55, 0.52, 0.48] });
      cloth(0, [0.30, 0.25, 0.20], 'pelvis', 2);
      break;
    case 'skeleton':
      ex.push({ joint: 'head', shape: 'part', offset: [0, 0.06, 0.14], size: [0.20, 0.12, 0.14], tint: [0.08, 0.07, 0.06] });
      ex.push({ joint: 'chest', shape: 'partSlim', offset: [0, 0.28, 0.14], size: [0.36, 0.40, 0.10], tint: [t[0] * 0.9, t[1] * 0.9, t[2] * 0.86] });
      cloth(0, [0.34, 0.31, 0.26], 'pelvis', 2);
      break;
    case 'mage':
      ex.push({ joint: 'head', shape: 'partSlim', offset: [0, 0.16, -0.04], size: [0.44, 0.42, 0.46], tint: dark });
      cloth(0, [t[0] * 1.2, t[1] * 1.15, t[2] * 1.3], 'pelvis', 3);
      break;
    case 'wraith':
      cloth(0, [t[0] * 0.8, t[1] * 0.85, t[2] * 1.0], 'chest', 2);
      break;
    case 'troll':
      for (const s of ['shoulderL', 'shoulderR']) {
        ex.push({ joint: s, shape: 'part', offset: [0, -0.05, 0], size: [0.40, 0.30, 0.40], tint: [t[0] * 0.8, t[1] * 0.8, t[2] * 0.8] });
      }
      ex.push({ joint: 'head', shape: 'spike', offset: [-0.10, 0.22, 0], size: [0.12, 0.26, 0.12], tint: [0.72, 0.70, 0.62] });
      ex.push({ joint: 'head', shape: 'spike', offset: [0.10, 0.22, 0], size: [0.12, 0.26, 0.12], tint: [0.72, 0.70, 0.62] });
      cloth(0, [0.28, 0.26, 0.20], 'pelvis', 1);
      break;
    case 'imp':
      cloth(0, [0.30, 0.16, 0.12], 'pelvis', 1);
      break;
    case 'deer':
      for (const sx of [-1, 1]) {
        ex.push({ joint: 'head', shape: 'spike', offset: [sx * 0.09, 0.16, -0.02], size: [0.05, 0.30, 0.05], tint: [0.62, 0.55, 0.42], rot: [0, 0, -sx * 0.35] });
        ex.push({ joint: 'head', shape: 'spike', offset: [sx * 0.16, 0.30, 0.02], size: [0.04, 0.18, 0.04], tint: [0.62, 0.55, 0.42], rot: [0.3, 0, -sx * 0.7] });
      }
      ex.push({ joint: 'rump', shape: 'partSlim', offset: [0, 0.06, -0.20], size: [0.22, 0.20, 0.10], tint: [0.86, 0.82, 0.74] });
      break;
    default:
      break;
  }
  return ex;
}

export class Enemy extends Actor {
  constructor(def, opts = {}) {
    const rig = RIG_FOR[def.body] || 'humanoid';
    super({
      rig, team: TEAM.ENEMY, name: def.name,
      hp: Math.round(def.hp * (opts.hpMul || 1)),
      poise: def.poise, def: def.def,
      speed: def.speed, runSpeed: def.runSpeed,
      tint: def.tint.slice(), emissive: def.emissive || 0,
      scale: def.h * (opts.scaleMul || 1),
      radius: 0.42 * def.h, height: 1.8 * def.h,
      weapon: def.weapon, shield: def.shield,
      x: opts.x || 0, y: opts.y || 0, z: opts.z || 0, yaw: opts.yaw || 0,
    });
    this.arch = def;   // アーキタイプ定義（Actor.def は数値の防御力なので別名にする）
    this.archetype = def.id;
    this.echo = Math.round((def.echo || 50) * (opts.echoMul || 1));
    this.homeX = this.x; this.homeZ = this.z;
    this.leash = opts.leash || 42;
    this.aiState = 'idle';
    this.aggro = false;
    this.target = null;
    this.think = 0;
    this.attackCd = 0.6 + Math.random() * 0.8;
    this.strafeDir = Math.random() < 0.5 ? 1 : -1;
    this.strafeTimer = 0;
    this.patrolAngle = Math.random() * TAU;
    this.patrolTimer = 0;
    this.ai = def.ai || { aggression: 0.5, circling: 0.4, retreat: 0.3, guard: 0 };
    this.shieldData = def.shield ? { block: 0.7, stability: 40 } : null;
    this.stamina = 100;
    this.chainNext = null;
    this.poi = opts.poi || null;
    this.superArmor = def.h >= 1.6;
    this.spawnFade = 0;
    this.extras = buildEnemyExtras(def);
    this.trailColor = def.emissive ? [0.7, 0.85, 1.0] : [0.9, 0.85, 0.78];
  }

  update(dt, game) {
    if (this.dead) {
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      this.removeAt += dt;
      return;
    }
    this.updateStatus(dt, game);
    this.attackCd -= dt;
    this.strafeTimer -= dt;
    this.think -= dt;

    const p = game.player;
    const dx = p.x - this.x, dz = p.z - this.z;
    const dist = Math.hypot(dx, dz);

    // ---- 温厚な動物は逃げる ----
    if (this.arch.passive) {
      let speed = 0;
      const flee = this.hp < this.maxHp ? 26 : 13;
      if (dist < flee && !p.dead) {
        this.fleeing = 3.5;
        this.aggro = false;
      }
      if (this.fleeing > 0) {
        this.fleeing -= dt;
        const ang = Math.atan2(this.x - p.x, this.z - p.z);
        this.yaw = rotateTowards(this.yaw, ang, 6 * dt);
        this.moveOnGround(dt, game, Math.sin(this.yaw), Math.cos(this.yaw), this.runSpeed);
        speed = this.runSpeed;
      } else {
        speed = this.idle(dt, game);
      }
      this.updatePhysics(dt, game);
      this.updateAnim(dt, speed);
      return;
    }

    // ---- 索敵 ----
    if (!this.aggro && !p.dead) {
      const range = this.arch.aggro * (game.sky.isNight ? 1.25 : 1) * (p.sprinting ? 1.35 : 1);
      if (dist < range) {
        this.aggro = true;
        this.aiState = 'chase';
        game.audio.play('aggro', { pitch: 0.85 + Math.random() * 0.3 });
        game.onAggro?.(this);
      }
    }
    if (this.aggro && (p.dead || Math.hypot(this.x - this.homeX, this.z - this.homeZ) > this.leash + 18)) {
      this.aggro = false;
      this.aiState = 'return';
      this.target = null;
    }

    let moveSpeed = 0;
    if (this.canAct()) {
      this.blocking = false;
      if (this.aggro) moveSpeed = this.combat(dt, game, dist, dx, dz);
      else if (this.aiState === 'return') moveSpeed = this.returnHome(dt, game);
      else moveSpeed = this.idle(dt, game);
    } else if (this.state === 'attack') {
      moveSpeed = this.processAttack(dt, game, dist);
    }

    this.updatePhysics(dt, game);
    this.updateAnim(dt, moveSpeed);

    // 連携攻撃
    if (this.chainNext && this.state === 'idle') {
      const next = this.attackById(this.chainNext);
      this.chainNext = null;
      if (next) this.beginAttack(next, game);
    }
  }

  idle(dt, game) {
    this.patrolTimer -= dt;
    if (this.patrolTimer <= 0) {
      this.patrolTimer = 2.5 + Math.random() * 4;
      this.patrolAngle = Math.random() * TAU;
      this.patrolMoving = Math.random() < 0.55;
    }
    if (!this.patrolMoving) return 0;
    const tx = this.homeX + Math.sin(this.patrolAngle) * 8;
    const tz = this.homeZ + Math.cos(this.patrolAngle) * 8;
    const dx = tx - this.x, dz = tz - this.z;
    if (Math.hypot(dx, dz) < 1.2) { this.patrolMoving = false; return 0; }
    this.faceTowards(tx, tz, dt, 3);
    this.moveOnGround(dt, game, dx, dz, this.speed * 0.4);
    return this.speed * 0.4;
  }

  returnHome(dt, game) {
    const dx = this.homeX - this.x, dz = this.homeZ - this.z;
    const d = Math.hypot(dx, dz);
    if (d < 2) { this.aiState = 'idle'; this.hp = Math.min(this.maxHp, this.hp + this.maxHp * 0.35); return 0; }
    this.faceTowards(this.homeX, this.homeZ, dt, 5);
    this.moveOnGround(dt, game, dx, dz, this.speed);
    return this.speed;
  }

  /** 戦闘中の意思決定 */
  combat(dt, game, dist, dx, dz) {
    const p = game.player;
    const ai = this.ai;
    const ranged = this.arch.ranged;
    const preferred = ranged ? this.arch.preferDist : this.bestRange() * 0.85;

    this.faceTowards(p.x, p.z, dt, ranged ? 4 : 6.5);

    // 攻撃判断
    if (this.attackCd <= 0 && this.canAct()) {
      const options = this.attackList().filter((a) => dist <= a.range * 1.05 + this.radius);
      if (options.length) {
        const roll = Math.random();
        if (roll < 0.35 + ai.aggression * 0.6) {
          const a = options[(Math.random() * options.length) | 0];
          this.beginAttack(a, game);
          return 0;
        }
      }
    }

    // 盾を構える
    if (ai.guard > 0 && dist < 4.5 && Math.random() < ai.guard * dt * 3) {
      this.blocking = true;
    }

    // 位置取り
    let mx = 0, mz = 0, speed = this.speed;
    const l = Math.hypot(dx, dz) || 1;
    const fx = dx / l, fz = dz / l;
    if (dist > preferred + 1.2) {
      mx = fx; mz = fz;
      speed = dist > 9 ? this.runSpeed : this.speed;
    } else if (dist < preferred - 1.4) {
      mx = -fx; mz = -fz;
      speed = this.speed * (ranged ? 1.0 : 0.75);
    } else {
      if (this.strafeTimer <= 0) {
        this.strafeTimer = 1.0 + Math.random() * 1.6;
        if (Math.random() < 0.4) this.strafeDir *= -1;
      }
      if (Math.random() < ai.circling) {
        mx = -fz * this.strafeDir; mz = fx * this.strafeDir;
        speed = this.speed * 0.7;
      }
    }
    if (this.blocking) speed *= 0.5;
    if (this.frostSlow > 0) { this.frostSlow -= dt; speed *= 0.7; }
    if (mx || mz) this.moveOnGround(dt, game, mx, mz, speed);
    else return 0;
    return speed;
  }

  attackList() { return this.arch.attacks; }
  attackById(id) { return this.attackList().find((a) => a.id === id); }
  bestRange() {
    let r = 2;
    for (const a of this.attackList()) r = Math.max(r, a.range);
    return r;
  }

  beginAttack(a, game) {
    this.currentAttack = a;
    this.startAttack({
      windup: a.windup / (this.speedMul || 1), active: a.active,
      recover: a.recover / (this.speedMul || 1), motion: a.motion, dmg: 1,
      range: a.range, arc: a.arc, poise: a.poise,
    }, game);
    this.attack.src = a;
    this.attackCd = 0.5 + Math.random() * 1.2 + a.recover;
    if (a.teleport) this.doTeleport(a.teleport, game);
    if (a.dash) this.dashPending = a.dash;
    if (a.volley) this.volleyLeft = a.volley;
    game.onEnemyAttack?.(this, a);
  }

  doTeleport(range, game) {
    const p = game.player;
    const ang = Math.random() * TAU;
    const r = range * (0.5 + Math.random() * 0.5);
    const nx = p.x + Math.sin(ang) * r;
    const nz = p.z + Math.cos(ang) * r;
    game.fx.voidBurst(this.x, this.y + this.height * 0.5, this.z);
    this.x = nx; this.z = nz;
    this.y = game.world.height(nx, nz);
    this.faceTowards(p.x, p.z, 1, 99);
    game.fx.voidBurst(this.x, this.y + this.height * 0.5, this.z);
    game.audio.play('teleport');
  }

  processAttack(dt, game, dist) {
    const a = this.attack;
    if (!a) return 0;
    const src = a.src || {};
    const phase = this.attackPhase();
    let moveSpeed = 0;

    if (phase === 'windup' && !this.arch.ranged) {
      // 予備動作中もわずかに追尾
      this.faceTowards(game.player.x, game.player.z, dt, 2.2);
    }

    if (this.dashPending && this.animT >= a.windup * 0.85) {
      const d = this.dashPending;
      this.dashPending = null;
      this.dashV = { x: Math.sin(this.yaw) * d, z: Math.cos(this.yaw) * d, t: 0.32 };
    }
    if (this.dashV) {
      const k = Math.min(dt, this.dashV.t);
      this.tryMove(game, this.x + this.dashV.x * k, this.z + this.dashV.z * k);
      this.dashV.t -= dt;
      moveSpeed = Math.hypot(this.dashV.x, this.dashV.z);
      if (this.dashV.t <= 0) this.dashV = null;
    }

    if (phase === 'active') {
      const p = game.player;
      if (src.projectile) {
        if (!this.attackApplied) {
          this.attackApplied = true;
          const n = src.volley || 1;
          for (let i = 0; i < n; i++) {
            const spread = (i - (n - 1) / 2) * 0.16;
            game.spawnProjectile({
              kind: src.projectile, owner: this, damage: src.dmg * (this.dmgMul || 1),
              x: this.x, y: this.y + this.height * 0.65, z: this.z,
              yaw: this.yaw + spread,
              pitch: Math.atan2((p.y + 1.0) - (this.y + this.height * 0.65), Math.max(1, dist)),
              speed: src.projectile === 'arrow' ? 40 : 22, team: TEAM.ENEMY,
              frost: src.frost, fire: src.fire, splash: src.splash || 0,
            });
          }
          game.audio.play(src.projectile === 'arrow' ? 'bow' : 'cast');
        }
      } else if (src.tick) {
        this._tickAcc = (this._tickAcc || 0) + dt;
        if (this._tickAcc > 0.22) {
          this._tickAcc = 0;
          this.tryHit(game, src, true);
        }
        this.emitBreath(game, src);
      } else if (!this.attackApplied) {
        if (this.tryHit(game, src, false)) this.attackApplied = true;
        else if (this.animT > a.windup + a.active * 0.8) this.attackApplied = true;
      }
      if (src.shock && !this.shockDone) {
        this.shockDone = true;
        game.fx.shockwave(this.x, this.y, this.z, src.shock);
        game.camera.shake(0.55);
        game.audio.play('slam');
      }
    } else if (phase === 'recover') {
      this.shockDone = false;
      if (src.chain && !this.chainQueued && Math.random() < 0.6) {
        this.chainQueued = true;
        this.chainNext = src.chain;
      }
    } else {
      this.chainQueued = false;
    }
    return moveSpeed;
  }

  tryHit(game, src, isTick) {
    const p = game.player;
    if (p.dead) return false;
    if (!inAttackArc(this, p, src.range, src.arc)) return false;

    // パリィ判定
    if (p.parryWindow > 0 && !src.guardBreak && !this.boss && !src.projectile) {
      p.onParrySuccess(this, game);
      this.attackApplied = true;
      return true;
    }
    const dmg = (src.dmg || 20) * (this.dmgMul || 1) * (isTick ? 0.25 : 1);
    p.takeDamage(dmg, {
      source: this, poise: src.poise, guardBreak: src.guardBreak,
      fire: src.fire, frost: src.frost, poison: src.poison, holy: src.holy,
      noFlinch: isTick,
    }, game);
    this.weaponTip(_tip);
    game.fx.impact(_tip[0], _tip[1], _tip[2]);
    return true;
  }

  emitBreath(game, src) {
    const dirX = Math.sin(this.yaw), dirZ = Math.cos(this.yaw);
    const y = this.y + this.height * 0.72;
    for (let i = 0; i < 3; i++) {
      const t = Math.random() * src.range;
      const spread = (Math.random() - 0.5) * src.arc * t * 0.5;
      game.fx.fireJet(
        this.x + dirX * t - dirZ * spread,
        y + (Math.random() - 0.5) * 1.2 - t * 0.05,
        this.z + dirZ * t + dirX * spread);
    }
  }

  onDeath(game, opts) {
    this.removeAt = 0;
    game.player.echo += Math.round(this.echo * game.player.mods.echoMul);
    game.player.kills++;
    game.ui.echoGain(Math.round(this.echo * game.player.mods.echoMul));
    game.fx.deathBurst(this.x, this.y + this.height * 0.5, this.z, this.tint);
    game.audio.play('death');
    const table = DROPS[this.archetype];
    if (table) {
      for (const [item, chance, n] of table) {
        if (Math.random() < chance) {
          game.player.addItem(item, n);
          game.ui.itemGain(item, n);
        }
      }
    }
    void opts;
  }
}

/* ================================================================ ボス */
export class Boss extends Enemy {
  constructor(def, opts = {}) {
    const base = {
      ...def, ai: { aggression: 0.85, circling: 0.35, retreat: 0.1, guard: def.shield ? 0.3 : 0 },
      attacks: def.phases[0].attacks, aggro: 48,
    };
    super(base, { ...opts, leash: 60 });
    this.bossDef = def;
    this.boss = true;
    this.superArmor = true;
    this.phaseIndex = 0;
    this.speedMul = 1;
    this.dmgMul = 1;
    this.name = def.name;
    this.title = def.title;
    this.maxPoise = def.poise;
    this.poise = def.poise;
    this.arenaX = opts.x; this.arenaZ = opts.z;
    this.arenaR = opts.arenaR || 26;
    this.transitioning = 0;
    this.weaponScale = def.scaleWeapon || (def.h > 1.5 ? 1.5 : 1);
  }

  attackList() { return this.bossDef.phases[this.phaseIndex].attacks; }

  update(dt, game) {
    if (!this.dead) {
      const ratio = this.hp / this.maxHp;
      const phases = this.bossDef.phases;
      let target = 0;
      for (let i = 0; i < phases.length; i++) {
        if (ratio > phases[i].hpAbove) { target = i; break; }
        target = i;
      }
      if (target > this.phaseIndex) this.enterPhase(target, game);
    }
    if (this.transitioning > 0) {
      this.transitioning -= dt;
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      this.iframes = Math.max(this.iframes, 0.05);
      return;
    }
    super.update(dt, game);

    // 闘技場外に出さない
    const d = Math.hypot(this.x - this.arenaX, this.z - this.arenaZ);
    if (d > this.arenaR) {
      const k = this.arenaR / d;
      this.x = this.arenaX + (this.x - this.arenaX) * k;
      this.z = this.arenaZ + (this.z - this.arenaZ) * k;
    }
  }

  enterPhase(i, game) {
    this.phaseIndex = i;
    const ph = this.bossDef.phases[i];
    this.speedMul = ph.speedMul || 1;
    this.dmgMul = ph.dmgMul || 1;
    this.transitioning = 1.6;
    this.setState('cast', 1.6, 'cast');
    this.attack = null;
    this.poise = this.maxPoise;
    game.fx.bossPhase(this.x, this.y, this.z, ph.enrage ? [1, 0.5, 0.2] : [0.6, 0.7, 1]);
    game.camera.shake(1.0);
    game.audio.play('boss_phase');
    game.ui.toast(ph.enrage ? `${this.name} が本気を出した` : `${this.name} の気配が変わる`);
    if (ph.fire) this.emissive = 0.6;
  }

  onDeath(game, opts) {
    super.onDeath(game, opts);
    game.onBossDefeated(this);
    void opts;
  }
}

/** アーキタイプIDからエネミーを生成 */
export function spawnEnemy(id, opts) {
  const def = ENEMIES[id];
  if (!def) return null;
  return new Enemy(def, opts);
}

export function spawnBoss(id, opts) {
  const def = BOSSES[id];
  if (!def) return null;
  return new Boss(def, opts);
}

export { clamp, clamp01, lerp, rotateTowards, angDelta };
