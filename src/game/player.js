// プレイヤー：操作・ステータス・装備・インベントリ・戦闘アクション

import { Actor, TEAM, inAttackArc, isBehind } from './actor.js';
import {
  WEAPONS, SHIELDS, ARMORS, TALISMANS, SPELLS, ITEMS, UPGRADE, SCALE, ARTS,
  scalingFactor, maxHP, maxStamina, maxFP, equipLoad, levelCost, STAT_KEYS,
} from './data.js';
import { clamp, clamp01, lerp, angDelta, rotateTowards, TAU, v3 } from '../core/math.js';

const _tip = v3.new();

export class Player extends Actor {
  constructor(opts = {}) {
    super({ rig: 'humanoid', team: TEAM.PLAYER, radius: 0.4, height: 1.8, hitGrace: 0.42, ...opts });
    this.name = opts.name || '灰の落人';
    this.level = 1;
    this.stats = { vit: 12, end: 11, str: 12, dex: 12, arc: 8, fth: 8 };
    this.echo = 0;
    this.lostEcho = null;

    this.equip = {
      weapon: 'broken_sword', offhand: 'wooden_shield',
      head: 'hood', body: 'rags',
      talisman: [null, null],
      spell: null,
      item: 'herb',
    };
    this.upgrades = { broken_sword: 0 };
    this.knownSpells = [];
    this.inventory = { herb: 3, arrow: 20, throwing_knife: 3 };
    this.flask = { hp: 5, hpMax: 5, fp: 2, fpMax: 2 };

    this.stamina = 100;
    this.fp = 40;
    this.staminaDelay = 0;
    this.comboIndex = 0;
    this.comboTimer = 0;
    this.lockTarget = null;
    this.sprinting = false;
    this.parryWindow = 0;
    this.riposteTarget = null;
    this.interactTarget = null;
    this.lastShrine = null;
    this.playtime = 0;
    this.kills = 0;
    this.deaths = 0;
    this.discovered = new Set();   // 地図で知っている
    this.reached = new Set();      // 自分の足でたどり着いた
    this.flags = {};

    this.recalc();
    this.hp = this.maxHp;
    this.stamina = this.maxStamina;
    this.fp = this.maxFP;
  }

  /* ------------------------------------------------------ 派生ステータス */
  recalc() {
    const s = this.stats;
    let hpMul = 1, defMul = 1, staminaRegenMul = 1, critMul = 1, echoMul = 1, spellMul = 1;
    for (const tid of this.equip.talisman) {
      const t = TALISMANS[tid];
      if (!t) continue;
      const e = t.effect;
      if (e.hpMul) hpMul *= e.hpMul;
      if (e.defMul) defMul *= e.defMul;
      if (e.staminaRegen) staminaRegenMul *= e.staminaRegen;
      if (e.critMul) critMul *= e.critMul;
      if (e.echoMul) echoMul *= e.echoMul;
      if (e.spellMul) spellMul *= e.spellMul;
    }
    this.mods = { hpMul, defMul, staminaRegenMul, critMul, echoMul, spellMul };

    const prevMax = this.maxHp;
    this.maxHp = Math.floor(maxHP(s.vit) * hpMul);
    this.maxStamina = maxStamina(s.end);
    this.maxFP = maxFP(s.arc, s.fth);
    if (prevMax && this.hp > 0) this.hp = Math.min(this.hp + (this.maxHp - prevMax), this.maxHp);

    const head = ARMORS[this.equip.head], body = ARMORS[this.equip.body];
    const shield = SHIELDS[this.equip.offhand];
    this.def = (head?.def || 0) + (body?.def || 0) + s.vit * 0.35 + this.level * 0.25;
    this.defBuff = defMul;
    this.shieldData = shield || null;
    this.shieldModel = shield ? shield.model : null;
    this.shieldTint = shield ? shield.tint : null;

    const w = WEAPONS[this.equip.weapon];
    const wlv = this.upgrades[this.equip.weapon] || 0;
    const tier = UPGRADE.tier(wlv);
    this.weaponModel = w ? w.model : null;
    this.weaponTier = tier;
    // 強化した刃は白く冴え、上の段では熱を帯びて光る。
    // 数字だけの強化では、振っていて強くなった気がしない
    const bt = w ? w.tint : [0.8, 0.8, 0.85];
    this.weaponTint = [
      lerp(bt[0], 1.00, tier.keen * 0.55),
      lerp(bt[1], 0.97, tier.keen * 0.50),
      lerp(bt[2], 0.94, tier.keen * 0.42),
    ];
    this.weaponGlow = (w?.emissive || 0) + tier.glow;
    this.weaponScale = (w?.cls === '大剣' ? 1.05 : 1) * (1 + tier.keen * 0.035);
    this.tint = body ? body.tint.slice() : [0.5, 0.45, 0.4];
    this.maxPoise = 18 + (body?.weight || 0) * 0.9 + (head?.weight || 0) * 0.8;
    this.poise = Math.min(this.poise, this.maxPoise);

    // 軌跡も段階で冴える
    const tc = w?.emissive ? [w.tint[0] + 0.3, w.tint[1] + 0.3, w.tint[2] + 0.4]
      : [0.88, 0.93, 1.0];
    this.trailColor = [
      tc[0] + tier.keen * 0.45, tc[1] + tier.keen * 0.40, tc[2] + tier.keen * 0.30,
    ];
    this.buildEquipVisuals(head, body, shield);

    const load = (w?.weight || 0) + (shield?.weight || 0) + (head?.weight || 0) + (body?.weight || 0);
    this.load = load;
    this.loadMax = equipLoad(s.end, s.str);
    this.loadRatio = load / this.loadMax;
    this.rollType = this.loadRatio < 0.3 ? 'fast' : this.loadRatio < 0.7 ? 'normal' : this.loadRatio < 1 ? 'heavy' : 'over';
    this.speed = 3.6 * (this.loadRatio > 1 ? 0.6 : 1);
    this.runSpeed = 6.6 * (this.loadRatio > 1 ? 0.6 : this.loadRatio > 0.7 ? 0.9 : 1);
  }

  /** 装備に応じた見た目パーツ（兜・肩当て・マント・帯）を組み立てる */
  buildEquipVisuals(head, body, shield) {
    const ex = [];
    if (head) {
      const t = head.tint;
      const heavy = head.weight >= 4;
      ex.push({
        joint: 'head', shape: heavy ? 'part' : 'partSlim',
        offset: [0, 0.13, heavy ? -0.01 : 0],
        size: heavy ? [0.44, 0.46, 0.44] : [0.40, 0.36, 0.40],
        tint: t, emissive: head.emissive || 0,
      });
      if (heavy) {
        // 面頬（前面の暗いスリット）
        ex.push({
          joint: 'head', shape: 'part', offset: [0, 0.10, 0.19],
          size: [0.30, 0.10, 0.10], tint: [0.06, 0.06, 0.08],
        });
      }
      if (head.id === 'crown') {
        for (let i = 0; i < 5; i++) {
          const a = (i / 5) * Math.PI * 2;
          ex.push({
            joint: 'head', shape: 'spike',
            offset: [Math.sin(a) * 0.17, 0.28, Math.cos(a) * 0.17],
            size: [0.07, 0.22, 0.07], tint: t, emissive: 0.5,
          });
        }
      }
    }
    if (body) {
      const t = body.tint;
      const heavy = body.weight >= 10;
      if (heavy) {
        for (const side of ['shoulderL', 'shoulderR']) {
          ex.push({
            joint: side, shape: 'part', offset: [0, -0.03, 0],
            size: [0.30, 0.22, 0.30], tint: [t[0] * 1.08, t[1] * 1.08, t[2] * 1.08],
          });
        }
        ex.push({
          joint: 'chest', shape: 'part', offset: [0, 0.24, 0.02],
          size: [0.58, 0.60, 0.36], tint: [t[0] * 1.05, t[1] * 1.05, t[2] * 1.05],
          emissive: body.emissive || 0,
        });
      }
      // 帯
      ex.push({
        joint: 'pelvis', shape: 'part', offset: [0, 0.02, 0],
        size: [0.48, 0.12, 0.34], tint: [t[0] * 0.6, t[1] * 0.55, t[2] * 0.5],
      });
      // 腰布・マント（重装ほど長い）
      const segs = heavy ? 3 : 2;
      const cloth = body.id === 'rags' ? [0.36, 0.33, 0.28]
        : [t[0] * 0.72, t[1] * 0.68, t[2] * 0.66];
      for (let i = 0; i < segs; i++) {
        ex.push({
          joint: 'pelvis', shape: 'partSlim',
          offset: [0, -0.06 - i * 0.26, -0.16 - i * 0.03],
          size: [0.46 - i * 0.05, 0.30, 0.10],
          tint: cloth, cloth: true, seg: i,
        });
      }
    }
    if (shield && shield.weight >= 10) {
      ex.push({
        joint: 'shoulderL', shape: 'part', offset: [0, -0.05, 0],
        size: [0.28, 0.20, 0.28], tint: shield.tint,
      });
    }
    this.extras = ex;
  }

  weapon() { return WEAPONS[this.equip.weapon]; }
  weaponLevel() { return this.upgrades[this.equip.weapon] || 0; }

  /** 現在の武器での攻撃力（モーション倍率適用前） */
  weaponAttackPower() {
    return this.attackPowerOf(this.equip.weapon);
  }

  /**
   * 任意の武器を装備したときの攻撃力。
   * 装備画面で「持ち替えたらどうなるか」を出すために使う。
   */
  attackPowerOf(id) {
    const w = WEAPONS[id];
    if (!w) return 20;
    const atk = w.base * UPGRADE.mul(this.upgrades[id] || 0);
    let bonus = 0;
    for (const [stat, grade] of Object.entries(w.scale || {})) {
      bonus += w.base * SCALE[grade] * scalingFactor(this.stats[stat]) * 1.15;
    }
    // 必要能力を満たさないと大幅減衰
    let penalty = 1;
    for (const [stat, req] of Object.entries(w.req || {})) {
      if (this.stats[stat] < req) penalty *= 0.55;
    }
    // 戦技「澄まし」の間は打撃が重くなる
    const art = (this.artBuff && this.artBuff.t > 0) ? this.artBuff.atk : 1;
    return (atk + bonus) * penalty * art;
  }

  /** 必要能力を満たしていない項目（装備画面の警告用） */
  unmetReqs(def) {
    const out = [];
    for (const [stat, req] of Object.entries(def?.req || {})) {
      if (this.stats[stat] < req) out.push({ stat, req, have: this.stats[stat] });
    }
    return out;
  }

  spellPower(spell) {
    const st = spell.scale === 'fth' ? this.stats.fth : this.stats.arc;
    return (spell.dmg || spell.heal || 0) * (0.55 + scalingFactor(st) * 1.35) * this.mods.spellMul;
  }

  /* --------------------------------------------------------- 更新 */
  update(dt, game) {
    this.playtime += dt;
    if (this.dead) {
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      return;
    }
    const inp = game.input;
    this.prevState = this.state;
    this.aimPitch = -game.camera.pitch * 0.85;
    this.updateStatus(dt, game);

    // 騎乗中は馬が位置と向きを制御する
    if (this.riding) {
      this.staminaDelay = Math.max(0, this.staminaDelay - dt);
      this.stamina = Math.min(this.maxStamina, this.stamina + 30 * dt);
      this.blocking = false;
      this.sprinting = false;
      if (this.state !== 'ride') this.state = 'ride';
      if (inp.pressed('jump')) this.riding.dismount(game);
      else if (inp.pressed('item')) this.useItem(game);
      this.updateAnim(dt, 0);
      return;
    }

    if (this.frostSlow > 0) this.frostSlow -= dt;

    // ---- スタミナ ----
    this.staminaDelay = Math.max(0, this.staminaDelay - dt);
    if (this.staminaDelay <= 0 && !this.sprinting) {
      const rate = (this.blocking ? 14 : 34) * this.mods.staminaRegenMul;
      this.stamina = Math.min(this.maxStamina, this.stamina + rate * dt);
    }
    this.comboTimer = Math.max(0, this.comboTimer - dt);
    if (this.comboTimer <= 0) this.comboIndex = 0;
    this.parryWindow = Math.max(0, this.parryWindow - dt);

    // ---- ロックオン ----
    if (inp.pressed('lock')) this.toggleLock(game);
    if (this.lockTarget && (this.lockTarget.dead ||
      Math.hypot(this.lockTarget.x - this.x, this.lockTarget.z - this.z) > 26)) {
      this.lockTarget = null;
    }

    // ---- 移動入力 ----
    let mx = inp.move.x, mz = inp.move.y;
    const mag = Math.min(1, Math.hypot(mx, mz));
    const camYaw = game.camera.yaw;
    let wx = 0, wz = 0;
    if (mag > 0.08) {
      const fx = Math.sin(camYaw), fz = Math.cos(camYaw);
      const rx = Math.cos(camYaw), rz = -Math.sin(camYaw);
      wx = fx * (-mz) + rx * mx;
      wz = fz * (-mz) + rz * mx;
      const l = Math.hypot(wx, wz) || 1;
      wx /= l; wz /= l;
    }

    // ---- 行動 ----
    const canAct = this.canAct();
    this.sprinting = false;

    // 釣っているあいだは竿から手が離せない。
    // 動く・振る・避ける——どれをしても、そこで糸を上げることになる
    const fishing = !!game.fishing?.active;
    if (fishing) {
      this.blocking = false;
      if (mag > 0.35 || inp.pressed('attack') || inp.pressed('heavy')
        || inp.pressed('dodge') || inp.pressed('jump') || inp.pressed('spell')
        || inp.pressed('art') || inp.pressed('mount')) {
        game.fishing.stop(game, 'cancel');
      }
    }

    if (canAct && !fishing) {
      this.blocking = inp.down('block') && !!this.shieldData && this.stamina > 2;

      if (inp.pressed('dodge')) {
        this.doDodge(wx, wz, mag, game);
      } else if (inp.down('dodge') && mag > 0.3 && this.stamina > 4) {
        this.sprinting = true;
      }

      if (inp.pressed('attack')) this.doAttack('light', game);
      if (inp.pressed('heavy')) this.doAttack('heavy', game);
      if (inp.pressed('block') && this.shieldData) this.doParry(game);
      if (inp.pressed('spell')) this.castSpell(game);
      if (inp.pressed('art')) this.useArt(game);
      if (inp.pressed('item')) this.useItem(game);
      if (inp.pressed('jump') && this.grounded && this.stamina > 12) {
        this.vy = 7.2;
        this.grounded = false;
        this.stamina -= 12;
        this.staminaDelay = 0.4;
        game.audio.play('jump');
      }
    }

    // ---- 移動 ----
    let speed = 0;
    if (this.state === 'roll' || this.state === 'backstep') {
      const p = clamp01(this.animT / this.motionDur);
      const push = (1 - p) * (this.state === 'roll' ? 9.5 : 7.0);
      const dir = this.rollDir;
      this.tryMove(game, this.x + dir[0] * push * dt, this.z + dir[1] * push * dt);
      speed = push * 0.4;
    } else if (this.state === 'attack') {
      const a = this.attack;
      if (a?.dash && !this.attackDashDone && this.animT >= a.windup * 0.6) {
        this.attackDashDone = true;
      }
      const ext = this.animExtra;
      if (ext?.lunge) {
        this.tryMove(game, this.x + Math.sin(this.yaw) * ext.lunge * 9 * dt,
          this.z + Math.cos(this.yaw) * ext.lunge * 9 * dt);
      }
    } else if (canAct && !fishing && mag > 0.08) {
      let base = this.sprinting ? this.runSpeed : this.speed * lerp(0.45, 1, mag);
      if (this.blocking) base *= 0.55;
      if (this.frostSlow > 0) base *= 0.75;
      // 水は深くなるほど足を取られ、泳ぎに変わると一定速になる
      if (this.swimming) {
        base = 2.4;
        this.sprinting = false;
      } else if (this.wading) {
        base *= lerp(0.92, 0.5, clamp01(this.waterDepth / (this.height * 0.8)));
      }
      speed = base;
      this.moveOnGround(dt, game, wx, wz, base);
      if (this.sprinting) {
        this.stamina -= 13 * dt;
        this.staminaDelay = 0.5;
        if (this.stamina <= 0) { this.stamina = 0; this.sprinting = false; }
      }
      // 向き
      if (this.lockTarget && !this.sprinting) {
        this.faceTowards(this.lockTarget.x, this.lockTarget.z, dt, 12);
      } else {
        const want = Math.atan2(wx, wz);
        this.yaw = rotateTowards(this.yaw, want, (this.sprinting ? 9 : 14) * dt);
      }
    } else if (this.lockTarget && canAct) {
      this.faceTowards(this.lockTarget.x, this.lockTarget.z, dt, 10);
    }

    // 攻撃中の判定
    if (this.state === 'attack') this.processAttack(dt, game);

    this.updatePhysics(dt, game);
    this.updateSlide(dt, game);
    this.updateAnim(dt, speed);

    // 滑り降りている間は擦れる音と土煙
    if (this.sliding > 0.05) {
      this._slideT = (this._slideT || 0) + dt;
      if (this._slideT > 0.12) {
        this._slideT = 0;
        const sf = game.surfaceAt(this.x, this.z);
        game.audio.footstep(sf.surface, 0.18 + this.sliding * 0.35);
        game.fx.dust(this.x, this.y + 0.05, this.z, 2);
      }
    }

    // 足音
    if (speed > 0.6 && this.grounded) {
      this._step = (this._step || 0) + dt * (2.0 + this.speed01 * 3.2);
      if (this._step > 1) {
        this._step = 0;
        const s = game.surfaceAt(this.x, this.z);
        game.audio.footstep(this.wading ? 'water' : s.surface, this.sprinting ? 0.6 : 0.36);
        if (s.surface === 'grass' || s.surface === 'marsh') {
          game.fx.dust(this.x, this.y + 0.05, this.z, 2);
        }
      }
    }
  }

  /* ------------------------------------------------------------ 回避 */
  doDodge(wx, wz, mag, game) {
    const cost = this.rollType === 'heavy' ? 30 : 22;
    if (this.stamina < cost) { game.audio.play('fail'); return; }
    this.stamina -= cost;
    this.staminaDelay = 0.55;
    if (mag > 0.2) {
      this.rollDir = [wx, wz];
      this.yaw = Math.atan2(wx, wz);
      const dur = this.rollType === 'fast' ? 0.52 : this.rollType === 'normal' ? 0.6 : 0.75;
      this.setState('roll', dur);
      this.iframes = this.rollType === 'fast' ? 0.42 : this.rollType === 'normal' ? 0.34 : 0.22;
    } else {
      this.rollDir = [-Math.sin(this.yaw), -Math.cos(this.yaw)];
      this.setState('backstep', 0.42);
      this.iframes = 0.14;
    }
    game.audio.play('roll');
    game.fx.dust(this.x, this.y + 0.05, this.z, 8);
  }

  /* ------------------------------------------------------------ 攻撃 */
  doAttack(kind, game) {
    const w = this.weapon();
    if (!w) return;
    const ms = w.moveset;
    let key;
    if (kind === 'heavy') {
      key = this.comboIndex > 0 && this.comboTimer > 0 ? 'h2' : 'h1';
    } else if (this.sprinting) {
      key = 'run';
    } else if (this.state === 'roll' || (this.prevState === 'roll' && this.animT < 0.25)) {
      key = 'roll';
    } else {
      key = this.comboIndex === 0 ? 'l1' : this.comboIndex === 1 ? 'l2' : 'l3';
    }
    const def = ms[key];
    if (!def) return;
    if (w.ranged && (this.inventory.arrow || 0) <= 0) {
      game.ui.toast('矢が足りない');
      game.audio.play('fail');
      return;
    }
    if (this.stamina < def.stam * 0.5) { game.audio.play('fail'); return; }

    // 致命の一撃（背後・パリィ後）
    const crit = this.findCritTarget(game);
    if (crit && kind === 'light') {
      this.executeCritical(crit.target, crit.type, game);
      return;
    }

    this.stamina -= def.stam;
    this.staminaDelay = 0.45;
    this.comboIndex = kind === 'heavy' ? 1 : Math.min(2, this.comboIndex + 1);
    this.comboTimer = def.windup + def.active + def.recover + 0.45;
    this.startAttack(def, game);
    if (w.ranged) this.pendingShot = def;
  }

  findCritTarget(game) {
    if (this.riposteTarget && !this.riposteTarget.dead &&
      Math.hypot(this.riposteTarget.x - this.x, this.riposteTarget.z - this.z) < 2.6) {
      return { target: this.riposteTarget, type: 'riposte' };
    }
    for (const e of game.enemies) {
      if (e.dead || e.boss) continue;
      const d = Math.hypot(e.x - this.x, e.z - this.z);
      if (d < 1.9 && isBehind(this, e, 0.95) && e.state !== 'stagger') {
        return { target: e, type: 'backstab' };
      }
    }
    return null;
  }

  executeCritical(target, type, game) {
    const w = this.weapon();
    const base = this.weaponAttackPower() * (w?.crit || 1.1) * this.mods.critMul;
    const dmg = base * (type === 'riposte' ? 3.4 : 2.9);
    this.setState('attack', 0.85, type === 'riposte' ? 'thrust' : 'overhead');
    this.attack = null;
    this.stamina -= 16;
    this.riposteTarget = null;
    target.iframes = 0;
    game.audio.play('critical');
    setTimeout(() => {
      if (target.dead) return;
      target.takeDamage(dmg, { source: this, poise: 999, ignoreIFrames: true, type: 'critical',
        impactWeight: 1 }, game);
      game.fx.blood(target.x, target.y + target.height * 0.6, target.z, 34);
    }, 260);
    game.ui.toast(type === 'riposte' ? '致命の一撃' : '背面攻撃');
  }

  processAttack(dt, game) {
    const a = this.attack;
    if (!a) return;
    if (a.art) { this.processArt(dt, game); return; }
    const phase = this.attackPhase();
    const w = this.weapon();

    if (w?.ranged && this.pendingShot && this.animT >= a.windup) {
      this.pendingShot = null;
      this.inventory.arrow = Math.max(0, (this.inventory.arrow || 0) - 1);
      const power = this.weaponAttackPower() * a.dmg;
      game.spawnProjectile({
        kind: a.projectile || 'arrow', owner: this, damage: power,
        x: this.x, y: this.y + 1.35, z: this.z,
        yaw: this.yaw, pitch: this.aimPitch || -0.03, speed: 46, team: TEAM.PLAYER,
      });
      game.audio.play('bow');
      return;
    }

    if (phase !== 'active') return;
    const power = this.weaponAttackPower() * a.dmg;
    for (const e of game.enemies) {
      if (e.dead || this.attackHits.has(e.id)) continue;
      if (!inAttackArc(this, e, a.range, a.arc)) continue;
      this.attackHits.add(e.id);
      const opts = {
        source: this, poise: a.poise, type: 'physical',
        bleed: w?.bleed, magic: w?.magic, holy: w?.holy,
        // 手応えは武器の重量から。短剣と大剣が同じ止まり方をしないように
        // 短剣 1.5 〜 王剣 16。いちばん軽い得物でも手応えを 0 にはしない
        impactWeight: 0.12 + clamp01(((w?.weight || 5) - 1.5) / 14) * 0.88,
        // 銘入り以上の刃は、当たった音に澄んだ余韻が乗る
        keen: this.weaponTier?.ring ? 1 : 0,
      };
      let dmg = power;
      if (w?.magic) dmg += w.magic * (0.6 + scalingFactor(this.stats.arc));
      if (w?.holy) dmg += w.holy * (0.6 + scalingFactor(this.stats.fth));
      e.takeDamage(dmg, opts, game);
      this.weaponTip(_tip);
      game.fx.slash(_tip[0], _tip[1], _tip[2], this.yaw);
      // 止め・揺れ・音は game.impact() が一手に引き受ける
    }
    // 破壊可能オブジェクト
    game.hitProps(this, a);
  }

  /* ---------------------------------------------------------- パリィ */
  doParry(game) {
    if (!this.shieldData || this.stamina < 14) return;
    this.stamina -= 14;
    this.staminaDelay = 0.5;
    this.setState('parry', 0.55);
    this.parryWindow = 0.22;
    game.audio.play('parry_swing');
  }

  onParrySuccess(attacker, game) {
    this.riposteTarget = attacker;
    attacker.setState('stagger', 1.6);
    attacker.poise = attacker.maxPoise;
    game.audio.play('parry');
    game.time.hitstop(0.16);
    game.camera.shake(0.4);
    game.camera.kick(this.x - attacker.x, this.z - attacker.z, 0.16);
    game.fx.sparks(attacker.x, attacker.y + attacker.height * 0.6, attacker.z, 22, [1, 0.95, 0.7]);
    game.ui.toast('パリィ成功！ 致命の一撃を狙え');
    setTimeout(() => { if (this.riposteTarget === attacker) this.riposteTarget = null; }, 2600);
  }

  /* -------------------------------------------------------- 遠隔の照準 */
  /** 今の構えで放ったら、どこへ落ちるか。矢と魔法で弾道が違う */
  projectileSpec() {
    const w = this.weapon();
    if (w?.ranged) return { speed: 46, gravity: 5.5, windK: 1 };
    const sp = SPELLS[this.equip.spell];
    if (sp?.projectile && (sp.type !== 'arc' || w?.catalyst)) {
      return { speed: sp.projectile === 'ice' ? 34 : 26, gravity: 0, windK: 0 };
    }
    return null;
  }

  /**
   * 着弾点を先に解いておく。
   *
   * 弓は 25m を超えると水平では届かない（実測）。落ちる量を目で
   * 測れという設計にはできるが、手がかりが何も無いのは不親切なので、
   * 同じ弾道を先に走らせて落ちる場所を出す。
   */
  predictImpact(game, out) {
    const spec = this.projectileSpec();
    if (!spec) return null;
    const cp = Math.cos(this.aimPitch || 0);
    let x = this.x, y = this.y + 1.35, z = this.z;
    let vx = Math.sin(this.yaw) * cp * spec.speed;
    let vy = Math.sin(this.aimPitch || 0) * spec.speed;
    let vz = Math.cos(this.yaw) * cp * spec.speed;
    // 近くは細かく、遠くは粗く刻む。
    // 全部を実飛翔と同じ刻みで追うと、上を向いたときに 1 回 700µs 近くかかり、
    // 毎フレーム走らせるには重すぎた。当たり判定が要るのは手前だけなので、
    // 敵を調べる範囲を超えたら刻みを 5 倍にする
    const NEAR2 = 70 * 70;
    // 実際に飛ぶ矢と同じ風を受けさせる。ここで無視すると、
    // 強風の日に印だけが正直でなくなる
    const wind = [0, 0];
    if (spec.windK !== 0) game.windAccel(wind);
    for (let i = 0; i < 200; i++) {
      const dx0 = x - this.x, dz0 = z - this.z;
      // 距離で打ち切ると、高い弾道の着弾点が 30m もずれた。
      // 刻みを粗くするだけで足りる（反復上限 200 が実質の歯止め）
      const near = dx0 * dx0 + dz0 * dz0 < NEAR2;
      const dt = near ? 1 / 60 : 1 / 30;
      vy -= spec.gravity * dt;
      vx += wind[0] * dt; vz += wind[1] * dt;
      const nx = x + vx * dt, ny = y + vy * dt, nz = z + vz * dt;
      if (near) {
        for (const e of game.enemies) {
          if (e.dead) continue;
          const dx = e.x - nx, dz = e.z - nz;
          const dy = (e.y + e.height * 0.55) - ny;
          if (dx * dx + dz * dz < (e.radius + 0.35) ** 2 && Math.abs(dy) < e.height * 0.6) {
            out[0] = nx; out[1] = ny; out[2] = nz;
            return 'enemy';
          }
        }
      }
      const gh = game.groundHeight(nx, nz);
      if (ny <= gh) {
        out[0] = nx; out[1] = gh + 0.05; out[2] = nz;
        return 'ground';
      }
      x = nx; y = ny; z = nz;
    }
    out[0] = x; out[1] = y; out[2] = z;
    return 'far';
  }

  /* ---------------------------------------------------------- 戦技 */
  /** 今の武器が持つ戦技 */
  artOf() {
    const w = this.weapon();
    return w?.art ? ARTS[w.art] : null;
  }

  useArt(game) {
    const art = this.artOf();
    if (!art) { game.ui.toast('この武器に戦技は無い'); game.audio.play('fail'); return false; }
    if (this.fp < art.fp) { game.ui.toast('FPが足りない'); game.audio.play('fail'); return false; }
    if (this.stamina < art.stam) { game.ui.toast('スタミナが足りない'); game.audio.play('fail'); return false; }
    if (!this.canAct()) return false;
    this.fp -= art.fp;
    this.stamina -= art.stam;
    this.staminaDelay = 0.6;
    this.attackHits = new Set();
    this.artDone = false;
    this.attack = { ...art, art: true };
    this.setState('attack', art.windup + art.active + art.recover, art.motion);
    this.artActive = art;
    game.audio.play('cast_start', { pitch: 1.25 });
    game.ui.toast(art.name);
    return true;
  }

  /** 戦技の当たり判定と効果。processAttack から呼ばれる */
  processArt(dt, game) {
    const a = this.attack;
    if (!a || !a.art) return false;
    const phase = this.attackPhase();

    // 踏み込み：予備動作の終わりから前へ出る
    if (a.dash && phase === 'active') {
      this.moveOnGround(dt, game, Math.sin(this.yaw), Math.cos(this.yaw), a.dash);
    }
    if (phase !== 'active') return true;

    // 自己強化：判定ではなく自分に掛ける
    if (a.kind === 'stance') {
      if (!this.artDone) {
        this.artDone = true;
        this.artBuff = { t: a.buffDur, atk: a.buffAtk, poise: a.buffPoise };
        game.fx.heal(this.x, this.y + 1, this.z);
        game.audio.play('buff');
      }
      return true;
    }
    // 矢を束ねて放つ
    if (a.kind === 'volley') {
      if (this.artDone) return true;
      this.artDone = true;
      const n = a.shots || 3;
      const power = this.weaponAttackPower() * a.dmg;
      for (let i = 0; i < n; i++) {
        game.spawnProjectile({
          kind: 'arrow', owner: this, damage: power,
          x: this.x, y: this.y + 1.35, z: this.z,
          yaw: this.yaw + (i - (n - 1) / 2) * 0.11,
          pitch: this.aimPitch || -0.03, speed: 46, team: TEAM.PLAYER,
        });
      }
      this.inventory.arrow = Math.max(0, (this.inventory.arrow || 0) - n);
      game.audio.play('bow');
      return true;
    }

    // 打撃系
    const w = this.weapon();
    const power = this.weaponAttackPower() * a.dmg;
    for (const e of game.enemies) {
      if (e.dead || this.attackHits.has(e.id)) continue;
      if (!inAttackArc(this, e, a.range, a.arc)) continue;
      this.attackHits.add(e.id);
      e.takeDamage(power, {
        source: this, poise: a.poise, type: 'physical',
        bleed: (w?.bleed || 0) + (a.bleed || 0), magic: w?.magic, holy: w?.holy,
        impactWeight: 1,
        keen: this.weaponTier?.ring ? 1 : 0,
      }, game);
      this.weaponTip(_tip);
      game.fx.slash(_tip[0], _tip[1], _tip[2], this.yaw);
    }
    // 衝撃波：地面を叩いて周囲へ
    if (a.shock && !this.artDone) {
      this.artDone = true;
      game.fx.shockwave(this.x, this.y + 0.1, this.z, a.shock);
      game.camera.shake(0.7);
      game.audio.play('slam');
    }
    return true;
  }

  /* ---------------------------------------------------------- 魔法 */
  castSpell(game) {
    const id = this.equip.spell;
    const sp = SPELLS[id];
    if (!sp) { game.ui.toast('魔法を装備していない'); return; }
    const w = this.weapon();
    if (sp.type === 'arc' && !w?.catalyst) { game.ui.toast('杖が必要だ'); game.audio.play('fail'); return; }
    if (this.fp < sp.fp) { game.ui.toast('FPが足りない'); game.audio.play('fail'); return; }
    this.fp -= sp.fp;
    this.setState('cast', sp.cast + sp.recover, 'cast');
    this.pendingSpell = { sp, at: sp.cast };
    game.audio.play('cast_start');
  }

  processSpell(dt, game) {
    if (!this.pendingSpell) return;
    if (this.animT < this.pendingSpell.at) return;
    const { sp } = this.pendingSpell;
    this.pendingSpell = null;
    const power = this.spellPower(sp);
    if (sp.heal) {
      this.hp = Math.min(this.maxHp, this.hp + power);
      game.fx.heal(this.x, this.y + 1, this.z);
      game.audio.play('heal');
    } else if (sp.buff) {
      this.spellBuff = { ...sp.buff, t: sp.buff.dur };
      game.fx.heal(this.x, this.y + 1, this.z);
      game.audio.play('buff');
    } else if (sp.projectile) {
      game.spawnProjectile({
        kind: sp.projectile, owner: this, damage: power,
        x: this.x, y: this.y + 1.3, z: this.z,
        yaw: this.yaw, pitch: this.aimPitch || 0, speed: sp.projectile === 'ice' ? 34 : 26,
        team: TEAM.PLAYER, frost: sp.frost, fire: sp.projectile === 'fire' ? 30 : 0,
        splash: sp.projectile === 'fire' ? 3.0 : 0,
      });
      game.audio.play('cast');
    }
  }

  /* --------------------------------------------------------- アイテム */
  useItem(game) {
    // 優先: 回復瓶 → 選択中アイテム
    if (this.flask.hp > 0 && this.hp < this.maxHp * 0.98) return this.drinkFlask(game, 'hp');
    const id = this.equip.item;
    const it = ITEMS[id];
    if (!it || (this.inventory[id] || 0) <= 0) {
      if (this.flask.hp > 0) return this.drinkFlask(game, 'hp');
      game.ui.toast('使えるものがない');
      return;
    }
    this.consume(id, game);
  }

  drinkFlask(game, kind = 'hp') {
    if (kind === 'hp' && this.flask.hp <= 0) return;
    if (kind === 'fp' && this.flask.fp <= 0) return;
    this.setState('drink', 0.95);
    const self = this;
    setTimeout(() => {
      if (self.dead) return;
      if (kind === 'hp') {
        self.flask.hp--;
        self.hp = Math.min(self.maxHp, self.hp + self.maxHp * 0.55 + 60);
      } else {
        self.flask.fp--;
        self.fp = Math.min(self.maxFP, self.fp + self.maxFP * 0.6 + 12);
      }
      game.fx.heal(self.x, self.y + 1, self.z);
      game.audio.play('drink');
    }, 420);
  }

  consume(id, game) {
    const it = ITEMS[id];
    if (!it) return;
    if ((this.inventory[id] || 0) <= 0) return;
    if (it.kind === 'throw') {
      this.setState('attack', 0.55, 'overhead');
      this.attack = null;
      this.inventory[id]--;
      const self = this;
      setTimeout(() => {
        game.spawnProjectile({
          kind: id === 'firebomb' ? 'firebomb' : 'knife', owner: self, damage: it.dmg,
          x: self.x, y: self.y + 1.4, z: self.z, yaw: self.yaw, pitch: 0.14,
          speed: 24, team: TEAM.PLAYER, gravity: 12, splash: it.splash || 0,
          fire: it.fire ? 40 : 0,
        });
      }, 220);
      game.audio.play('throw');
      return;
    }
    this.setState('drink', it.use || 0.8);
    this.inventory[id]--;
    const self = this;
    setTimeout(() => {
      if (it.heal) self.hp = Math.min(self.maxHp, self.hp + it.heal);
      // 食べ物は息も整える（炙り魚）
      if (it.stam) {
        self.stamina = Math.min(self.maxStamina, self.stamina + self.maxStamina * it.stam);
        self.staminaDelay = 0;
      }
      if (it.cure === 'poison') self.statusTimers.poison = 0;
      if (it.echo) { self.echo += it.echo; game.ui.toast(`残響 +${it.echo}`); }
      game.fx.heal(self.x, self.y + 1, self.z);
      game.audio.play('drink');
    }, 300);
  }

  addItem(id, n = 1) {
    this.inventory[id] = (this.inventory[id] || 0) + n;
  }

  /* ------------------------------------------------------- ロックオン */
  toggleLock(game) {
    if (this.lockTarget) { this.lockTarget = null; game.audio.play('ui_off'); return; }
    let best = null, bestScore = Infinity;
    const camYaw = game.camera.yaw;
    for (const e of game.enemies) {
      if (e.dead) continue;
      const dx = e.x - this.x, dz = e.z - this.z;
      const d = Math.hypot(dx, dz);
      if (d > 24) continue;
      const ang = Math.abs(angDelta(camYaw, Math.atan2(dx, dz)));
      if (ang > 1.1) continue;
      const score = d + ang * 9;
      if (score < bestScore) { bestScore = score; best = e; }
    }
    this.lockTarget = best;
    game.audio.play(best ? 'ui_on' : 'fail');
  }

  /* ------------------------------------------------------- 成長・強化 */
  canLevel() { return this.echo >= levelCost(this.level); }
  levelUp(stat) {
    const cost = levelCost(this.level);
    if (this.echo < cost) return false;
    if (!STAT_KEYS.includes(stat)) return false;
    if (this.stats[stat] >= 99) return false;
    this.echo -= cost;
    this.stats[stat]++;
    this.level++;
    this.recalc();
    this.hp = this.maxHp;
    this.stamina = this.maxStamina;
    this.fp = this.maxFP;
    return true;
  }

  upgradeWeapon(game) {
    const id = this.equip.weapon;
    const lv = this.upgrades[id] || 0;
    if (lv >= UPGRADE.maxLevel) { game.ui.toast('これ以上は強化できない'); return false; }
    const c = UPGRADE.cost(lv);
    if (this.echo < c.echo || (this.inventory[c.mat] || 0) < c.matCount) {
      game.ui.toast('素材か残響が足りない');
      return false;
    }
    this.echo -= c.echo;
    this.inventory[c.mat] -= c.matCount;
    this.upgrades[id] = lv + 1;
    this.recalc();
    game.audio.play('upgrade');
    game.ui.toast(`${WEAPONS[id].name} +${lv + 1}`);
    return true;
  }

  restAtShrine(poi, game) {
    this.hp = this.maxHp;
    this.stamina = this.maxStamina;
    this.fp = this.maxFP;
    this.flask.hp = this.flask.hpMax;
    this.flask.fp = this.flask.fpMax;
    this.statusTimers.poison = 0;
    this.statusTimers.burn = 0;
    for (const k in this.status) this.status[k] = 0;
    this.lastShrine = poi;
    game.respawnWorld();
  }

  onDeath(game) {
    this.deaths++;
    this.lostEcho = { x: this.x, y: this.y, z: this.z, echo: this.echo };
    this.echo = 0;
    game.onPlayerDeath();
  }

  /** 死亡後の復活 */
  respawn(game) {
    if (game.dungeon) game.exitDungeon();
    const s = this.lastShrine;
    this.dead = false;
    this.deathT = 0;
    this.hp = this.maxHp;
    this.stamina = this.maxStamina;
    this.fp = this.maxFP;
    this.flask.hp = this.flask.hpMax;
    this.flask.fp = this.flask.fpMax;
    this.state = 'idle';
    this.animT = 0;
    this.statusTimers.poison = 0;
    this.statusTimers.burn = 0;
    for (const k in this.status) this.status[k] = 0;
    if (s) game.placeSafely(this, s.x + 1.5, s.z + 1.5);
    else game.placeSafely(this, 40, 300);
    this.lockTarget = null;
    game.respawnWorld();
  }

  /* ------------------------------------------------------------ 保存 */
  serialize() {
    return {
      level: this.level, stats: this.stats, echo: this.echo, lostEcho: this.lostEcho,
      equip: this.equip, upgrades: this.upgrades, inventory: this.inventory,
      flask: this.flask, knownSpells: this.knownSpells,
      x: this.x, y: this.y, z: this.z, yaw: this.yaw,
      hp: this.hp, playtime: this.playtime, kills: this.kills, deaths: this.deaths,
      shrine: this.lastShrine ? { x: this.lastShrine.x, z: this.lastShrine.z, id: this.lastShrine.id } : null,
      discovered: [...this.discovered], reached: [...this.reached], flags: this.flags,
    };
  }
  deserialize(d, game) {
    Object.assign(this.stats, d.stats || {});
    this.level = d.level || 1;
    this.echo = d.echo || 0;
    this.lostEcho = d.lostEcho || null;
    Object.assign(this.equip, d.equip || {});
    this.upgrades = d.upgrades || {};
    this.inventory = d.inventory || {};
    this.flask = d.flask || this.flask;
    this.knownSpells = d.knownSpells || [];
    this.x = d.x ?? this.x; this.y = d.y ?? this.y; this.z = d.z ?? this.z;
    this.yaw = d.yaw ?? 0;
    this.playtime = d.playtime || 0;
    this.kills = d.kills || 0;
    this.deaths = d.deaths || 0;
    this.discovered = new Set(d.discovered || []);
    // 古い保存には reached が無い。既知の場所は踏破済みとみなす
    this.reached = new Set(d.reached || d.discovered || []);
    this.flags = d.flags || {};
    this.recalc();
    this.hp = Math.min(this.maxHp, d.hp || this.maxHp);
    this.stamina = this.maxStamina;
    this.fp = this.maxFP;
    if (d.shrine && game) {
      this.lastShrine = game.pois.find((p) => p.id === d.shrine.id) || null;
    }
  }
}

export { clamp, clamp01, lerp, TAU };
