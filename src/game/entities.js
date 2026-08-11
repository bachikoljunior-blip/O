// entities.js — actors: player, enemies (AI), NPCs, projectiles and drops.

import {
  TAU, clamp, lerp, dist, dist2, angleTo, angleDiff, dir4, makeRNG, approach,
} from '../core/util.js';
import { FX } from '../render/particles.js';
import { audio } from '../core/audio.js';
import { rollLoot, effStats, ITEMS, displayName } from './items.js';
import { TILE } from '../world/worldgen.js';

// ————————————————————————————————————————————————— base

export class Actor {
  constructor(x, y) {
    this.x = x; this.y = y;
    this.vx = 0; this.vy = 0;
    this.kx = 0; this.ky = 0;         // knockback velocity
    this.r = 9;
    this.dir = 0;
    this.face = Math.PI / 2;
    this.animT = Math.random() * 10;
    this.moving = false;
    this.alive = true;
    this.hp = 10; this.maxHp = 10;
    this.hurtT = 0;
    this.flash = 0;
    this.scale = 1;
    this.phase = Math.random() * TAU;
    this.z = 0;
  }

  get sortY() { return this.y; }

  knock(dx, dy, power) {
    const m = Math.hypot(dx, dy) || 1;
    this.kx += (dx / m) * power;
    this.ky += (dy / m) * power;
  }

  moveWithCollision(g, dt, vx, vy) {
    const steps = Math.max(1, Math.ceil((Math.hypot(vx, vy) * dt) / 6));
    const sdt = dt / steps;
    for (let i = 0; i < steps; i++) {
      const nx = this.x + vx * sdt;
      if (!g.blocked(nx, this.y, this.r, this)) this.x = nx;
      else { this.kx *= 0.4; vx *= 0.2; }
      const ny = this.y + vy * sdt;
      if (!g.blocked(this.x, ny, this.r, this)) this.y = ny;
      else { this.ky *= 0.4; vy *= 0.2; }
    }
  }

  applyKnock(g, dt) {
    if (Math.abs(this.kx) > 1 || Math.abs(this.ky) > 1) {
      this.moveWithCollision(g, dt, this.kx, this.ky);
      const d = Math.pow(0.0016, dt);
      this.kx *= d; this.ky *= d;
    } else { this.kx = 0; this.ky = 0; }
  }
}

// ————————————————————————————————————————————————— player

export const SPELLS = {
  fire: { id: 'fire', name: 'ファイア', icon: 'fire', mp: 10, cast: 0.28, cool: 0.55, color: '#ff8a3a', desc: '火球を放ち、着弾で爆発する。', level: 1 },
  ice: { id: 'ice', name: 'アイスショット', icon: 'ice', mp: 12, cast: 0.3, cool: 0.7, color: '#8fe0ff', desc: '氷片を3つ放ち、敵を鈍足にする。', level: 4 },
  bolt: { id: 'bolt', name: 'サンダー', icon: 'bolt', mp: 20, cast: 0.45, cool: 1.1, color: '#ffe14a', desc: '雷を落とし、周囲を痺れさせる。', level: 8 },
  heal: { id: 'heal', name: 'ヒール', icon: 'heal', mp: 22, cast: 0.5, cool: 1.6, color: '#8fe8a8', desc: 'HPを回復する。', level: 3 },
  gust: { id: 'gust', name: 'ゲイル', icon: 'wind', mp: 14, cast: 0.25, cool: 0.8, color: '#bff0d8', desc: '突風で敵を吹き飛ばす。', level: 6 },
};

export class Player extends Actor {
  constructor(x, y) {
    super(x, y);
    this.type = 'player';
    this.r = 8.5;
    this.level = 1;
    this.xp = 0;
    this.gold = 60;
    this.baseAtk = 8;
    this.baseDef = 2;
    this.baseMag = 5;
    this.maxHp = 120; this.hp = 120;
    this.maxMp = 50; this.mp = 50;
    this.maxSta = 100; this.sta = 100;
    this.staDelay = 0;
    this.equip = { weapon: null, head: null, body: null, off: null, trinket: null };
    this.inv = [];
    this.state = 'idle';
    this.stateT = 0;
    this.combo = 0;
    this.comboT = 0;
    this.flow = 0;
    this.flowT = 0;
    this.hitChain = 0;
    this.hitChainT = 0;
    this.highestChain = 0;
    this.perfectDodges = 0;
    this.parries = 0;
    this.rollPerfect = false;
    this.hitSet = new Set();
    this.iframes = 0;
    this.spellSlots = ['fire', null, null, null];
    this.knownSpells = ['fire'];
    this.spellCool = {};
    this.buff = { atk: 0, def: 0, t: 0 };
    this.poison = 0;
    this.slow = 0;
    this.charge = 0;
    this.blockT = 0;
    this.attackQueued = false;
    this.deathT = 0;
    this.speedBase = 122;
    this.pal = {
      skin: '#e8b98e', hair: '#54331f', hairStyle: 1, shirt: '#4a6f8a', pants: '#3d4450',
      boots: '#4a3423', cloak: '#7a3f3a', accent: '#d8b04a', belt: '#5a4020',
    };
    this.stats = {};
    this.recompute();
    this.mount = null;
    this.stealth = 0;
    this.playtime = 0;
    this.kills = 0;
  }

  get xpNext() { return Math.round(60 * Math.pow(this.level, 1.62)); }

  recompute() {
    const s = { atk: this.baseAtk + this.level * 2.6, def: this.baseDef + this.level * 1.1, mag: this.baseMag + this.level * 1.8, hp: 0, mp: 0, spd: 0, crit: 0.04, leech: 0, fire: 0, ice: 0, shock: 0, sta: 0 };
    for (const k in this.equip) {
      const it = this.equip[k];
      if (!it) continue;
      const es = effStats(it);
      for (const st in es) s[st] = (s[st] || 0) + es[st];
    }
    s.atk += this.buff.atk;
    s.def += this.buff.def;
    this.stats = s;
    const newMaxHp = Math.round(100 + this.level * 22 + (s.hp || 0));
    const newMaxMp = Math.round(40 + this.level * 8 + (s.mp || 0));
    const newMaxSta = Math.round(100 + (s.sta || 0));
    if (newMaxHp !== this.maxHp) { const r = this.hp / this.maxHp; this.maxHp = newMaxHp; this.hp = Math.min(this.maxHp, Math.max(1, Math.round(this.maxHp * r))); }
    this.maxMp = newMaxMp;
    this.maxSta = newMaxSta;
    this.mp = Math.min(this.mp, this.maxMp);
    this.sta = Math.min(this.sta, this.maxSta);
    // appearance from equipment
    const w = this.equip.weapon;
    this.appearance = {
      weapon: w ? w.weaponKind : 'none',
      weaponColor: w ? (w.rarity >= 3 ? '#d8b4ff' : w.rarity === 2 ? '#9fd0ff' : '#dfe6ee') : '#dfe6ee',
      weaponLen: w ? w.weaponLen || 17 : 17,
      shield: this.equip.off ? (this.equip.off.shieldKind || 'round') : null,
      shieldColor: this.equip.off ? (this.equip.off.rarity >= 3 ? '#a878d8' : '#8a6a3a') : '#8a6a3a',
    };
    this.pal.armor = this.equip.body ? (this.equip.body.rarity >= 3 ? '#8a6ac0' : this.equip.body.rarity === 2 ? '#5a7fa8' : '#8b8f96') : null;
    this.pal.helm = this.equip.head ? (this.equip.head.rarity >= 3 ? '#a888d8' : '#9aa4ae') : null;
    this.pal.plume = this.equip.head && this.equip.head.rarity >= 2 ? '#c8384a' : null;
  }

  get speed() {
    let s = this.speedBase + (this.stats.spd || 0) * 0.5;
    if (this.slow > 0) s *= 0.55;
    if (this.state === 'block') s *= 0.42;
    if (this.state === 'cast') s *= 0.4;
    if (this.mount) s *= 1.9;
    if (this.flowT > 0) s *= 1.1;
    return s;
  }

  get weaponReach() {
    const w = this.equip.weapon;
    return (w ? w.reach : 30) * 1.0;
  }
  get atkSpeed() {
    const w = this.equip.weapon;
    return (w ? w.atkSpeed : 1.0) * (1 + (this.stats.spd || 0) * 0.0016);
  }
  get isRanged() { return !!(this.equip.weapon && this.equip.weapon.ranged); }

  get combatMultiplier() { return this.flowT > 0 ? 1.28 : 1; }

  gainFlow(g, amount) {
    if (!Number.isFinite(amount) || amount <= 0) return;
    if (this.flowT > 0) {
      this.flowT = Math.min(10, this.flowT + amount * 0.012);
      return;
    }
    this.flow = Math.min(100, this.flow + amount);
    if (this.flow < 100) return;
    this.flow = 0;
    this.flowT = 8;
    this.mp = Math.min(this.maxMp, this.mp + this.maxMp * 0.25);
    g.toast('アエテリア・フロウ発動！', '#ffe08a');
    g.pt.ring(this.x, this.y, { r0: 10, r1: 100, life: 0.7, col: 'rgba(120,225,255,0.9)', w: 6, squash: 0.55 });
    g.pt.burst(this.x, this.y - 8, 28, { speed: 150, life: 0.8, r: 3.5, col: '#bff5ff', col2: '#ffe08a', glow: 1, vz: 80, g: 70 });
    audio.sfx('levelup', { vol: 0.75 });
    g.shake(6, 0.32);
    g.vibrate([18, 28, 18]);
  }

  recordHit(g, dealt, crit = false) {
    if (!(dealt > 0)) return;
    this.hitChain++;
    this.hitChainT = 2.2;
    this.highestChain = Math.max(this.highestChain, this.hitChain);
    this.gainFlow(g, 3 + (crit ? 3 : 0) + Math.min(3, dealt / 35));
  }

  perfectDodge(g, attacker) {
    if (this.rollPerfect) return;
    this.rollPerfect = true;
    this.perfectDodges++;
    this.sta = Math.min(this.maxSta, this.sta + 18);
    this.gainFlow(g, 22);
    g.pt.text(this.x, this.y - 34, 'PERFECT', { col: '#bff5ff', size: 21, life: 1.15 });
    g.pt.ring(this.x, this.y, { r0: 5, r1: 58, life: 0.32, col: 'rgba(180,245,255,0.9)', w: 4, squash: 0.7 });
    if (attacker?.stagger) attacker.stagger(attacker.boss ? 0.35 : 0.7);
    audio.sfx('parry', { vol: 0.7 });
    g.hitStop(0.1);
    g.vibrate([10, 18, 10]);
  }

  addXp(n, g) {
    this.xp += n;
    let leveled = false;
    while (this.xp >= this.xpNext) {
      this.xp -= this.xpNext;
      this.level++;
      leveled = true;
      for (const id in SPELLS) {
        if (SPELLS[id].level === this.level && !this.knownSpells.includes(id)) {
          this.knownSpells.push(id);
          const slot = this.spellSlots.indexOf(null);
          if (slot >= 0) this.spellSlots[slot] = id;
          g.toast(`新しい魔法を習得: ${SPELLS[id].name}`, '#c58cff');
        }
      }
    }
    if (leveled) {
      this.recompute();
      this.hp = this.maxHp; this.mp = this.maxMp;
      FX.levelUp(g.pt, this.x, this.y);
      audio.sfx('levelup');
      g.toast(`レベル ${this.level} になった！`, '#ffd76a');
      g.shake(6, 0.4);
    }
  }

  damage(g, amount, srcX, srcY, opt = {}) {
    if (!this.alive) return 0;
    const difficulty = g.difficultyProfile?.() || null;
    if (difficulty) amount *= difficulty.incoming;
    if (this.iframes > 0) {
      if (this.state === 'roll') this.perfectDodge(g, opt.attacker);
      return 0;
    }
    let dmg = amount;
    let blocked = false;
    if (this.state === 'block') {
      const a = angleTo(this.x, this.y, srcX, srcY);
      if (Math.abs(angleDiff(this.face, a)) < 1.25) {
        blocked = true;
        if (this.blockT < (difficulty?.parryWindow ?? 0.22)) {         // parry
          dmg = 0;
          this.parries++;
          this.gainFlow(g, 18);
          this.sta = Math.min(this.maxSta, this.sta + 12);
          audio.sfx('parry');
          g.pt.ring(this.x, this.y - 16, { r0: 6, r1: 60, life: 0.35, col: 'rgba(255,240,180,0.9)', w: 4, squash: 0.8 });
          g.pt.text(this.x, this.y - 30, 'パリィ！', { col: '#ffe8a0', size: 20 });
          g.hitStop(0.14);
          if (opt.attacker && opt.attacker.stagger) opt.attacker.stagger(1.1);
        } else {
          dmg *= 0.22;
          this.sta -= 14;
          this.staDelay = 0.5;
          audio.sfx('block');
          if (this.sta < 0) { this.sta = 0; this.state = 'hurt'; this.stateT = 0.5; }
        }
      }
    }
    dmg = Math.max(blocked ? 0 : 1, Math.round(dmg * (100 / (100 + this.stats.def * 3.2))));
    if (dmg <= 0) return 0;
    this.flow = Math.max(0, this.flow - 20);
    this.hitChain = 0;
    this.hitChainT = 0;
    this.hp -= dmg;
    this.flash = 0.18;
    this.hurtT = 0.3;
    this.iframes = blocked ? 0.15 : 0.42;
    g.pt.text(this.x, this.y - 28, String(dmg), { col: '#ff6a6a', size: 20 });
    FX.blood(g.pt, this.x, this.y, angleTo(srcX, srcY, this.x, this.y));
    this.knock(this.x - srcX, this.y - srcY, blocked ? 60 : 150);
    audio.sfx('hurt');
    g.shake(blocked ? 3 : 7, 0.25);
    g.vibrate(blocked ? 12 : 32);
    if (!blocked) { this.state = 'hurt'; this.stateT = 0.22; }
    if (this.hp <= 0) {
      this.hp = 0;
      this.alive = false;
      this.state = 'dead';
      this.deathT = 0;
      audio.sfx('die');
      g.onPlayerDeath();
    }
    return dmg;
  }

  heal(g, n) {
    const before = this.hp;
    this.hp = Math.min(this.maxHp, this.hp + n);
    const d = Math.round(this.hp - before);
    if (d > 0) {
      g.pt.text(this.x, this.y - 30, '+' + d, { col: '#8fe8a8', size: 18 });
      FX.heal(g.pt, this.x, this.y);
    }
  }

  tryAttack(g) {
    if (!this.alive) return;
    if (this.state === 'attack' && this.stateT < this.attackDur * 0.55) { this.attackQueued = true; return; }
    if (this.state === 'roll' || this.state === 'cast' || this.state === 'hurt') return;
    if (this.sta < 6) { audio.sfx('error', { vol: 0.5 }); return; }
    if (this.isRanged) { this.startBowDraw(g); return; }
    this.startSwing(g);
  }

  startSwing(g) {
    const target = g.aimTarget(this.x, this.y, this.face, this.weaponReach + 22,
      g.input.usingTouch ? 1.35 : 0.9);
    if (target) {
      this.face = angleTo(this.x, this.y, target.x, target.y);
      this.dir = dir4(Math.cos(this.face), Math.sin(this.face));
    }
    const sp = this.atkSpeed;
    this.combo = this.comboT > 0 ? (this.combo + 1) % 3 : 0;
    this.comboT = 0.55;
    this.state = 'attack';
    this.stateT = 0;
    this.attackDur = (this.combo === 2 ? 0.46 : 0.34) / sp;
    this.attackHit = false;
    this.hitSet.clear();
    this.sta -= 6;
    this.staDelay = 0.35;
    this.attackQueued = false;
    audio.sfx('swing', { vol: 0.7 });
  }

  startBowDraw(g) {
    this.state = 'bow';
    this.stateT = 0;
    this.charge = 0;
    audio.sfx('bow', { vol: 0.6 });
  }

  releaseBow(g) {
    if (this.state !== 'bow') return;
    const power = clamp(this.charge / 0.7, 0.25, 1);
    const dmg = (this.stats.atk * (0.6 + power * 1.1)) * this.combatMultiplier;
    const target = g.aimTarget(this.x, this.y, this.face, 390, g.input.usingTouch ? 1.0 : 0.65);
    const a = target ? angleTo(this.x, this.y, target.x, target.y) : this.face;
    this.face = a;
    this.dir = dir4(Math.cos(a), Math.sin(a));
    g.spawnProjectile({
      x: this.x + Math.cos(a) * 12, y: this.y - 14 + Math.sin(a) * 12,
      vx: Math.cos(a) * (320 + power * 340), vy: Math.sin(a) * (320 + power * 340),
      dmg, owner: 'player', kind: 'arrow', life: 1.4, crit: this.stats.crit,
    });
    this.sta -= 8;
    this.staDelay = 0.35;
    this.state = 'idle';
    this.charge = 0;
    audio.sfx('arrow');
  }

  tryRoll(g) {
    const difficulty = g.difficultyProfile?.() || null;
    const cost = difficulty?.dodgeCost ?? 24;
    if (!this.alive || this.state === 'roll' || this.sta < cost) {
      if (this.sta < cost) audio.sfx('error', { vol: 0.4 });
      return;
    }
    this.state = 'roll';
    this.stateT = 0;
    this.rollPerfect = false;
    this.sta -= cost;
    this.staDelay = 0.5;
    this.iframes = difficulty?.rollIframes ?? 0.30;
    const mv = g.input.moveVector();
    const a = mv.m > 0.2 ? Math.atan2(mv.y, mv.x) : this.face;
    this.face = a;
    this.dir = dir4(Math.cos(a), Math.sin(a));
    this.rollVX = Math.cos(a) * 330;
    this.rollVY = Math.sin(a) * 330;
    audio.sfx('dodge');
    FX.dust(g.pt, this.x, this.y, 8);
    g.vibrate(10);
  }

  castSpell(g, slot) {
    const id = this.spellSlots[slot];
    if (!id || !this.alive) return;
    const sp = SPELLS[id];
    if ((this.spellCool[id] || 0) > 0) { audio.sfx('error', { vol: 0.4 }); return; }
    if (this.mp < sp.mp) { audio.sfx('error', { vol: 0.5 }); g.toast('MPが足りない', '#88a'); return; }
    if (this.state === 'roll' || this.state === 'cast') return;
    this.mp -= sp.mp;
    this.spellCool[id] = sp.cool;
    this.state = 'cast';
    this.stateT = 0;
    this.castDur = sp.cast;
    this.castId = id;
    this.castDone = false;
    FX.cast(g.pt, this.x, this.y, sp.color);
  }

  finishCast(g) {
    const id = this.castId;
    const sp = SPELLS[id];
    const target = id === 'heal' ? null : g.aimTarget(this.x, this.y, this.face, 320,
      g.input.usingTouch ? 1.05 : 0.7);
    const a = target ? angleTo(this.x, this.y, target.x, target.y) : this.face;
    this.face = a;
    this.dir = dir4(Math.cos(a), Math.sin(a));
    const mag = this.stats.mag;
    const power = this.combatMultiplier;
    switch (id) {
      case 'fire':
        g.spawnProjectile({
          x: this.x + Math.cos(a) * 14, y: this.y - 16 + Math.sin(a) * 14,
          vx: Math.cos(a) * 300, vy: Math.sin(a) * 300,
          dmg: (mag * 1.9 + this.stats.atk * 0.3) * power, owner: 'player', kind: 'fireball', life: 1.6, aoe: 46,
        });
        audio.sfx('fire');
        break;
      case 'ice':
        for (let i = -1; i <= 1; i++) {
          const aa = a + i * 0.24;
          g.spawnProjectile({
            x: this.x + Math.cos(aa) * 12, y: this.y - 16 + Math.sin(aa) * 12,
            vx: Math.cos(aa) * 380, vy: Math.sin(aa) * 380,
            dmg: mag * 0.95 * power, owner: 'player', kind: 'ice', life: 1.1, slow: 2.2,
          });
        }
        audio.sfx('ice');
        break;
      case 'bolt': {
        const target = g.nearestEnemy(this.x + Math.cos(a) * 90, this.y + Math.sin(a) * 90, 170) ;
        const tx = target ? target.x : this.x + Math.cos(a) * 110;
        const ty = target ? target.y : this.y + Math.sin(a) * 110;
        g.pt.beam(tx, ty - 300, tx, ty, { col: '#ffe14a', w: 7, life: 0.24, jag: 16 });
        g.pt.ring(tx, ty, { r0: 6, r1: 78, life: 0.4, col: 'rgba(255,240,140,0.85)', w: 5 });
        g.pt.burst(tx, ty, 20, { speed: 130, life: 0.5, r: 3, col: '#ffe14a', glow: 1, g: 120, vz: 60, z: 4 });
        g.areaDamage(tx, ty, 74, mag * 2.3 * power, { stun: 1.1, src: 'player', color: '#ffe14a' });
        audio.sfx('bolt');
        g.shake(7, 0.3);
        break;
      }
      case 'heal':
        this.heal(g, (mag * 3.2 + this.maxHp * 0.12) * (this.flowT > 0 ? 1.18 : 1));
        audio.sfx('heal');
        break;
      case 'gust': {
        g.pt.ring(this.x + Math.cos(a) * 40, this.y + Math.sin(a) * 40, { r0: 10, r1: 80, life: 0.35, col: 'rgba(200,255,230,0.7)', w: 5 });
        g.pt.burst(this.x + Math.cos(a) * 30, this.y - 14 + Math.sin(a) * 30, 22, { dir: a, spread: 1.0, speed: 260, life: 0.4, r: 3, col: '#bff0d8', glow: 1, g: 0 });
        g.coneEffect(this.x, this.y, a, 110, 1.0, (e) => {
          const dealt = e.damage(g, mag * 1.1 * power, this.x, this.y, { knock: 340, src: 'player' });
          this.recordHit(g, dealt);
        });
        audio.sfx('bolt', { vol: 0.5 });
        break;
      }
    }
  }

  update(dt, g) {
    this.playtime += dt;
    if (!this.alive) { this.deathT += dt; return; }
    this.animT += dt;
    this.iframes = Math.max(0, this.iframes - dt);
    this.flash = Math.max(0, this.flash - dt);
    this.hurtT = Math.max(0, this.hurtT - dt);
    this.comboT = Math.max(0, this.comboT - dt);
    this.hitChainT = Math.max(0, this.hitChainT - dt);
    if (this.hitChainT <= 0) this.hitChain = 0;
    const wasFlowing = this.flowT > 0;
    this.flowT = Math.max(0, this.flowT - dt);
    if (wasFlowing && this.flowT <= 0) g.toast('アエテリア・フロウ終了', '#a8c8d8');
    this.slow = Math.max(0, this.slow - dt);
    for (const k in this.spellCool) this.spellCool[k] = Math.max(0, this.spellCool[k] - dt);
    if (this.buff.t > 0) {
      this.buff.t -= dt;
      if (this.buff.t <= 0) { this.buff.atk = 0; this.buff.def = 0; this.recompute(); g.toast('効果が切れた', '#aaa'); }
    }
    if (this.poison > 0) {
      this.poison -= dt;
      this.poisonTick = (this.poisonTick || 0) + dt;
      if (this.poisonTick > 1) {
        this.poisonTick = 0;
        this.hp = Math.max(1, this.hp - Math.round(this.maxHp * 0.02));
        g.pt.text(this.x, this.y - 26, '毒', { col: '#a8e05a', size: 14 });
      }
    }

    // stamina
    this.staDelay = Math.max(0, this.staDelay - dt);
    if (this.staDelay <= 0 && this.state !== 'block') {
      this.sta = Math.min(this.maxSta, this.sta + 42 * dt);
    } else if (this.state === 'block') {
      this.sta = Math.min(this.maxSta, this.sta + 8 * dt);
    }
    this.mp = Math.min(this.maxMp, this.mp + (this.flowT > 0 ? 5.2 : 1.6) * dt);

    const input = g.input;
    const mv = input.moveVector();
    this.stateT += dt;

    // ——— state machine
    if (this.state === 'roll') {
      const k = clamp(this.stateT / 0.36, 0, 1);
      const sp = lerp(1, 0.15, k * k);
      this.moveWithCollision(g, dt, this.rollVX * sp, this.rollVY * sp);
      if (this.stateT > 0.36) this.state = 'idle';
      this.moving = true;
    } else if (this.state === 'attack') {
      const k = this.stateT / this.attackDur;
      if (!this.attackHit && k > 0.34) {
        this.attackHit = true;
        this.doSwingHit(g);
      }
      // small lunge
      if (k < 0.5) this.moveWithCollision(g, dt, Math.cos(this.face) * 60, Math.sin(this.face) * 60);
      if (this.stateT >= this.attackDur) {
        if (this.attackQueued && this.sta >= 6) this.startSwing(g);
        else this.state = 'idle';
      }
      this.moving = false;
    } else if (this.state === 'cast') {
      if (!this.castDone && this.stateT >= this.castDur) {
        this.castDone = true;
        this.finishCast(g);
      }
      if (this.stateT >= this.castDur + 0.18) this.state = 'idle';
      this.moving = mv.m > 0.1;
      if (this.moving) this.walk(g, dt, mv);
    } else if (this.state === 'bow') {
      this.charge += dt;
      this.moving = mv.m > 0.1;
      if (this.moving) this.walk(g, dt, mv, 0.5);
    } else if (this.state === 'hurt') {
      if (this.stateT > 0.22) this.state = 'idle';
      this.moving = false;
    } else {
      if (this.state === 'block') this.blockT += dt;
      this.moving = mv.m > 0.12;
      if (this.moving) this.walk(g, dt, mv);
      else { this.vx = 0; this.vy = 0; }
    }

    this.applyKnock(g, dt);

    // footsteps
    if (this.moving && this.state !== 'roll') {
      this.stepT = (this.stepT || 0) + dt * (this.mount ? 2.6 : 1);
      if (this.stepT > 0.32) {
        this.stepT = 0;
        const water = g.isWater(this.x, this.y);
        audio.sfx('step', { vol: 0.5, water });
        if (water) FX.splash(g.pt, this.x, this.y);
        else if (Math.random() < 0.4) FX.dust(g.pt, this.x, this.y, 2);
      }
    }
  }

  walk(g, dt, mv, mult = 1) {
    const sp = this.speed * mv.m * mult * (g.isWater(this.x, this.y) ? 0.55 : 1) * (g.isRoad(this.x, this.y) ? 1.12 : 1);
    this.vx = mv.x * sp; this.vy = mv.y * sp;
    this.moveWithCollision(g, dt, this.vx, this.vy);
    if (this.state !== 'block') {
      this.face = Math.atan2(mv.y, mv.x);
      this.dir = dir4(mv.x, mv.y);
    }
  }

  doSwingHit(g) {
    const reach = this.weaponReach;
    const arc = this.combo === 2 ? 1.5 : 1.0;
    const mult = this.combo === 2 ? 1.55 : this.combo === 1 ? 1.12 : 1;
    let hitAny = false;
    for (const e of g.enemies) {
      if (!e.alive || this.hitSet.has(e)) continue;
      const d = dist(this.x, this.y, e.x, e.y);
      if (d > reach + e.r) continue;
      const a = angleTo(this.x, this.y, e.x, e.y);
      if (Math.abs(angleDiff(this.face, a)) > arc) continue;
      this.hitSet.add(e);
      const crit = Math.random() < this.stats.crit;
      let dmg = this.stats.atk * mult * this.combatMultiplier * (0.92 + Math.random() * 0.16);
      if (crit) dmg *= 1.85;
      const dealt = e.damage(g, dmg, this.x, this.y, { crit, knock: this.combo === 2 ? 260 : 130, src: 'player' });
      this.recordHit(g, dealt, crit);
      if (this.stats.leech > 0 && dealt > 0) this.heal(g, dealt * this.stats.leech);
      hitAny = true;
    }
    // breakables & resources
    g.hitProps(this.x, this.y, this.face, reach + 8, arc);
    FX.slash(g.pt, this.x + Math.cos(this.face) * 8, this.y + Math.sin(this.face) * 8, this.face, reach * 0.9,
      this.combo === 2 ? 'rgba(255,220,150,0.95)' : 'rgba(255,255,255,0.8)');
    if (hitAny) {
      g.hitStop(this.combo === 2 ? 0.07 : 0.04);
      g.shake(this.combo === 2 ? 5 : 2.5, 0.16);
      g.vibrate(this.combo === 2 ? 26 : 14);
    }
  }
}

// ————————————————————————————————————————————————— enemies

export const ENEMY_TYPES = {
  slime: { name: 'スライム', draw: 'slime', hp: 34, atk: 7, def: 1, spd: 40, r: 11, aggro: 190, range: 22, windup: 0.55, cool: 1.4, xp: 9, gold: [2, 8], behav: 'hop', tint: '#5cc46a' },
  bat: { name: 'ケイヴバット', draw: 'bat', hp: 26, atk: 8, def: 0, spd: 96, r: 10, aggro: 230, range: 20, windup: 0.3, cool: 0.9, xp: 11, gold: [3, 9], behav: 'flyer', fly: true, tint: '#4a3f52' },
  wolf: { name: 'ダイアウルフ', draw: 'wolf', hp: 56, atk: 12, def: 2, spd: 118, r: 12, aggro: 300, range: 26, windup: 0.36, cool: 1.1, xp: 18, gold: [4, 12], behav: 'pack', tint: '#6d6f78' },
  boar: { name: 'あばれ猪', draw: 'boar', hp: 78, atk: 14, def: 4, spd: 74, r: 13, aggro: 220, range: 26, windup: 0.7, cool: 1.8, xp: 22, gold: [6, 14], behav: 'charger' },
  spider: { name: '妖蜘蛛', draw: 'spider', hp: 46, atk: 10, def: 2, spd: 88, r: 12, aggro: 250, range: 200, windup: 0.5, cool: 1.6, xp: 20, gold: [5, 13], behav: 'ranged', proj: 'web', tint: '#37303a' },
  bandit: { name: '野盗', draw: 'human', hp: 72, atk: 15, def: 5, spd: 104, r: 10, aggro: 280, range: 30, windup: 0.42, cool: 1.2, xp: 26, gold: [12, 32], behav: 'melee', humanoid: 'bandit' },
  archer: { name: '野盗の弓兵', draw: 'human', hp: 58, atk: 13, def: 3, spd: 96, r: 10, aggro: 340, range: 240, windup: 0.7, cool: 1.9, xp: 28, gold: [12, 34], behav: 'ranged', proj: 'arrow', humanoid: 'archer' },
  skeleton: { name: 'スケルトン', draw: 'human', hp: 66, atk: 16, def: 6, spd: 82, r: 10, aggro: 260, range: 30, windup: 0.5, cool: 1.35, xp: 30, gold: [8, 20], behav: 'melee', humanoid: 'skeleton', undead: true, night: true },
  ghost: { name: '彷徨う霊', draw: 'ghost', hp: 54, atk: 17, def: 3, spd: 70, r: 11, aggro: 240, range: 26, windup: 0.6, cool: 1.5, xp: 32, gold: [0, 0], behav: 'phase', fly: true, undead: true, night: true },
  imp: { name: 'インプ', draw: 'imp', hp: 62, atk: 18, def: 4, spd: 108, r: 10, aggro: 300, range: 190, windup: 0.55, cool: 1.5, xp: 36, gold: [14, 36], behav: 'ranged', proj: 'fire', fly: true, tint: '#b4433a' },
  golem: { name: 'ストーンゴーレム', draw: 'golem', hp: 190, atk: 26, def: 14, spd: 52, r: 16, aggro: 220, range: 36, windup: 0.9, cool: 2.1, xp: 68, gold: [20, 50], behav: 'melee', heavy: true },
  wraith: { name: 'レイス', draw: 'wraith', hp: 130, atk: 28, def: 8, spd: 92, r: 13, aggro: 320, range: 34, windup: 0.55, cool: 1.4, xp: 74, gold: [0, 0], behav: 'phase', fly: true, undead: true },
  drake: { name: 'ドレイク', draw: 'drake', hp: 210, atk: 30, def: 12, spd: 96, r: 16, aggro: 340, range: 200, windup: 0.8, cool: 2.0, xp: 96, gold: [40, 90], behav: 'ranged', proj: 'fire', fly: true },
  troll: { name: 'トロール', draw: 'troll', hp: 420, atk: 38, def: 16, spd: 66, r: 20, aggro: 300, range: 44, windup: 1.0, cool: 2.2, xp: 220, gold: [80, 160], behav: 'melee', heavy: true, boss: true, scale: 1 },
  dragon: { name: '灰燼竜ヴォルガ', draw: 'dragon', hp: 1500, atk: 52, def: 22, spd: 78, r: 30, aggro: 500, range: 260, windup: 0.9, cool: 1.5, xp: 1500, gold: [600, 900], behav: 'boss', proj: 'fire', boss: true, fly: false },
};

export class Enemy extends Actor {
  constructor(kind, x, y, level) {
    super(x, y);
    const T = ENEMY_TYPES[kind];
    this.type = 'enemy';
    this.kind = kind;
    this.T = T;
    this.level = Math.max(1, level | 0);
    const k = 1 + (this.level - 1) * 0.30;
    this.maxHp = Math.round(T.hp * k);
    this.hp = this.maxHp;
    this.atk = T.atk * (1 + (this.level - 1) * 0.26);
    this.def = T.def * (1 + (this.level - 1) * 0.20);
    this.r = T.r;
    this.speed = T.spd;
    this.state = 'idle';
    this.stateT = 0;
    this.cool = Math.random() * 0.8;
    this.homeX = x; this.homeY = y;
    this.wanderA = Math.random() * TAU;
    this.wanderT = 0;
    this.stun = 0;
    this.slow = 0;
    this.scale = T.scale || 1;
    this.fly = !!T.fly;
    this.undead = !!T.undead;
    this.boss = !!T.boss;
    this.maxPoise = this.boss ? 180 : T.heavy ? 92 : 48;
    this.poise = 0;
    this.poiseT = 0;
    this.aggroT = 0;
    this.tint = T.tint;
    this.hitOnce = false;
    this.enraged = false;
    this.specialIndex = Math.floor(Math.random() * 3);
    if (T.humanoid) {
      const rng = makeRNG((x * 7919 + y * 104729) | 0);
      const sets = {
        bandit: { skin: '#c89a72', hair: '#2e2119', hairStyle: 2, shirt: '#5c4a38', pants: '#3a3028', cloak: '#4a3a2a', boots: '#2e241c' },
        archer: { skin: '#d0a878', hair: '#4a3a22', hairStyle: 0, shirt: '#4a5c3a', pants: '#3a4030', cloak: '#3c4a30', boots: '#2e281c' },
        skeleton: { skin: '#e8e2d0', hair: '#8a8474', hairStyle: 3, shirt: '#8a8474', pants: '#6a6458', boots: '#4a4438' },
      };
      this.pal = { ...sets[T.humanoid] };
      this.equipVis = {
        weapon: T.humanoid === 'archer' ? 'bow' : rng.pick(['sword', 'axe', 'mace', 'dagger']),
        weaponColor: '#b9c2cb', weaponLen: 16,
        shield: T.humanoid === 'bandit' && rng.chance(0.3) ? 'round' : null,
        shieldColor: '#6b5636',
      };
    }
  }

  get sortY() { return this.y; }

  stagger(t) {
    this.stun = Math.max(this.stun, t);
    this.poise = 0;
    this.state = 'stagger';
    this.stateT = 0;
  }

  damage(g, amount, srcX, srcY, opt = {}) {
    if (!this.alive) return 0;
    const difficulty = g.difficultyProfile?.() || null;
    if (difficulty) amount *= difficulty.outgoing;
    const vulnerable = this.state === 'stagger';
    if (vulnerable) amount *= 1.25;
    const dmg = Math.max(1, Math.round(amount * (100 / (100 + this.def * 3.0))));
    this.hp -= dmg;
    this.flash = 0.14;
    this.aggroT = 8;
    this.target = g.player;
    if (this.state === 'idle' || this.state === 'wander') this.state = 'chase';
    const a = angleTo(srcX, srcY, this.x, this.y);
    if (!this.T.heavy || opt.crit) this.knock(this.x - srcX, this.y - srcY, (opt.knock || 120) * (this.T.heavy ? 0.35 : 1));
    if (opt.stun) this.stagger(opt.stun);
    if (opt.slow) this.slow = Math.max(this.slow, opt.slow);
    if (!opt.stun && !vulnerable && this.hp > 0) {
      this.poiseT = 2.6;
      const impact = Math.min(this.maxPoise * 0.42, Math.max(5, amount * (opt.crit ? 1.7 : 1)));
      this.poise = Math.min(this.maxPoise, this.poise + impact);
      if (this.poise >= this.maxPoise) {
        this.stagger(this.boss ? 0.85 : 1.35);
        g.pt.text(this.x, this.y - this.r - 34, 'BREAK', { col: '#ffe08a', size: this.boss ? 25 : 21, life: 1.1 });
        g.pt.ring(this.x, this.y, { r0: 5, r1: this.r * 4.5, life: 0.38, col: 'rgba(255,225,140,0.9)', w: 4, squash: 0.55 });
        g.player?.gainFlow(g, this.boss ? 18 : 10);
        g.hitStop(this.boss ? 0.14 : 0.1);
        audio.sfx('parry', { vol: 0.65 });
      }
    }
    g.pt.text(this.x + (Math.random() - 0.5) * 10, this.y - this.r - 18, String(dmg), {
      col: opt.crit ? '#ffd76a' : opt.color || '#ffffff', size: opt.crit ? 25 : 18, crit: opt.crit,
    });
    FX.hit(g.pt, this.x, this.y, a, opt.color || '#ffe6a0');
    if (!this.undead) FX.blood(g.pt, this.x, this.y, a, this.T.blood || '#a8323c');
    audio.sfx(opt.crit ? 'crit' : 'enemyHurt', { vol: 0.7 });
    if (this.hp <= 0) this.die(g, opt);
    return dmg;
  }

  die(g, opt = {}) {
    this.alive = false;
    this.hp = 0;
    FX.death(g.pt, this.x, this.y, this.tint || '#8a7a6a');
    audio.sfx('die', { vol: 0.6 });
    g.onEnemyKilled(this, opt);
  }

  update(dt, g) {
    if (!this.alive) return;
    this.animT += dt;
    this.flash = Math.max(0, this.flash - dt);
    this.stun = Math.max(0, this.stun - dt);
    this.slow = Math.max(0, this.slow - dt);
    this.poiseT = Math.max(0, this.poiseT - dt);
    if (this.poiseT <= 0 && this.poise > 0) this.poise = Math.max(0, this.poise - this.maxPoise * 0.18 * dt);
    this.cool = Math.max(0, this.cool - dt);
    this.stateT += dt;
    this.aggroT = Math.max(0, this.aggroT - dt);

    const p = g.player;
    const d = p.alive ? dist(this.x, this.y, p.x, p.y) : 1e9;
    const T = this.T;
    const canSee = p.alive && (d < T.aggro * (g.isNight() && T.night ? 1.4 : 1) || this.aggroT > 0);

    if (this.kind === 'dragon' && !this.enraged && this.hp <= this.maxHp * 0.5) {
      this.enraged = true;
      this.atk *= 1.12;
      this.speed *= 1.15;
      this.cool = 0;
      this.stagger(0.7);
      g.showBanner('第二形態・灰燼の翼', 'ヴォルガの炎が夜を焼く。');
      g.pt.ring(this.x, this.y, { r0: 20, r1: 180, life: 0.9, col: 'rgba(255,90,40,0.9)', w: 8, squash: 0.55 });
      g.pt.burst(this.x, this.y - 18, 48, { speed: 220, life: 1, r: 5, col: '#ffb04a', col2: '#a82018', glow: 1, vz: 120, g: 110 });
      g.shake(12, 0.8);
      audio.sfx('fire');
    }

    if (this.stun > 0) { this.state = 'stagger'; }

    switch (this.state) {
      case 'idle':
      case 'wander': {
        this.moving = false;
        this.wanderT -= dt;
        if (this.wanderT <= 0) {
          this.wanderT = 1.4 + Math.random() * 2.6;
          this.wanderA = Math.random() * TAU;
          this.state = Math.random() < 0.55 ? 'wander' : 'idle';
        }
        if (this.state === 'wander') {
          const sp = this.speed * 0.32;
          const back = dist(this.x, this.y, this.homeX, this.homeY) > 140
            ? angleTo(this.x, this.y, this.homeX, this.homeY) : this.wanderA;
          this.moveWithCollision(g, dt, Math.cos(back) * sp, Math.sin(back) * sp);
          this.face = back;
          this.dir = dir4(Math.cos(back), Math.sin(back));
          this.moving = true;
        }
        if (canSee) { this.state = 'chase'; this.aggroT = 6; if (Math.random() < 0.3) g.enemyAlert(this); }
        break;
      }
      case 'chase': {
        if (!canSee && this.aggroT <= 0) { this.state = 'wander'; break; }
        const a = angleTo(this.x, this.y, p.x, p.y);
        this.face = a;
        this.dir = dir4(Math.cos(a), Math.sin(a));
        if (this.kind === 'dragon' && this.cool <= 0 && d < 380) {
          this.beginDragonSpecial(g, a);
          break;
        }
        const wantDist = T.behav === 'ranged' ? Math.min(T.range * 0.6, 150) : T.range * 0.7;
        let sp = this.speed * (this.slow > 0 ? 0.5 : 1);
        if (T.behav === 'hop') {
          this.hopT = (this.hopT || 0) + dt;
          const hop = Math.max(0, Math.sin(this.hopT * 4.5));
          sp *= hop * 1.9;
          this.z = hop * 8;
        }
        let mvA = a;
        if (d < wantDist * 0.8 && T.behav === 'ranged') mvA = a + Math.PI;
        else if (d < wantDist) sp = 0;
        // strafe a little so groups don't stack
        if (T.behav !== 'charger') mvA += Math.sin(this.animT * 1.4 + this.phase) * 0.28;
        this.moveWithCollision(g, dt, Math.cos(mvA) * sp, Math.sin(mvA) * sp);
        this.moving = sp > 4;
        if (this.cool <= 0 && d < T.range + this.r + p.r) {
          this.state = 'windup';
          this.stateT = 0;
        }
        if (T.behav === 'charger' && this.cool <= 0 && d < 220 && d > 60) {
          this.state = 'charge'; this.stateT = 0; this.chargeA = a;
        }
        break;
      }
      case 'windup': {
        this.moving = false;
        const a = angleTo(this.x, this.y, p.x, p.y);
        if (this.stateT < T.windup * 0.5) { this.face = a; this.dir = dir4(Math.cos(a), Math.sin(a)); }
        if (this.stateT >= T.windup) {
          this.state = 'attack';
          this.stateT = 0;
          this.hitOnce = false;
          this.doAttack(g);
        }
        break;
      }
      case 'attack': {
        this.moving = false;
        if (this.stateT > 0.3) {
          this.state = 'chase';
          this.cool = T.cool * (0.8 + Math.random() * 0.4);
        }
        break;
      }
      case 'charge': {
        const k = clamp(this.stateT / 0.9, 0, 1);
        const sp = this.speed * 2.4 * (1 - k * 0.4);
        this.moveWithCollision(g, dt, Math.cos(this.chargeA) * sp, Math.sin(this.chargeA) * sp);
        this.moving = true;
        this.face = this.chargeA;
        this.dir = dir4(Math.cos(this.chargeA), Math.sin(this.chargeA));
        if (d < this.r + p.r + 12 && !this.hitOnce) {
          this.hitOnce = true;
          p.damage(g, this.atk * 1.5, this.x, this.y, { attacker: this });
          p.knock(p.x - this.x, p.y - this.y, 300);
        }
        if (this.stateT > 0.9) { this.state = 'chase'; this.cool = T.cool * 1.4; this.hitOnce = false; }
        break;
      }
      case 'stagger': {
        this.moving = false;
        if (this.stun <= 0 && this.stateT > 0.25) this.state = 'chase';
        break;
      }
      case 'bossBreath': {
        this.moving = false;
        if (this.stateT < 0.55) {
          this.face = angleTo(this.x, this.y, p.x, p.y);
          this.dir = dir4(Math.cos(this.face), Math.sin(this.face));
        }
        if (!this.specialFired && this.stateT >= 0.76) {
          this.specialFired = true;
          const shots = this.enraged ? 9 : 7;
          for (let i = 0; i < shots; i++) {
            const spread = this.enraged ? 1.28 : 1.04;
            const a = this.face + (i / Math.max(1, shots - 1) - 0.5) * spread;
            g.spawnProjectile({
              x: this.x + Math.cos(a) * 28, y: this.y - 24 + Math.sin(a) * 28,
              vx: Math.cos(a) * 290, vy: Math.sin(a) * 290,
              dmg: this.atk * 0.62, owner: 'enemy', kind: 'fire', life: 2,
              aoe: 30, attacker: this,
            });
          }
          audio.sfx('fire');
          g.shake(7, 0.35);
        }
        if (this.stateT > 1.38) this.finishDragonSpecial();
        break;
      }
      case 'bossMeteor': {
        this.moving = false;
        if (!this.specialFired && this.stateT >= 1.04) {
          this.specialFired = true;
          for (const spot of this.specialTargets || []) {
            g.pt.beam(spot.x, spot.y - 360, spot.x, spot.y, { col: '#ffb04a', w: 12, life: 0.28, jag: 9 });
            g.pt.ring(spot.x, spot.y, { r0: 8, r1: 82, life: 0.45, col: 'rgba(255,110,45,0.95)', w: 7, squash: 0.55 });
            g.pt.burst(spot.x, spot.y, 34, { speed: 190, life: 0.72, r: 4.5, col: '#ffc06a', col2: '#9a241c', glow: 1, vz: 110, g: 150 });
            g.areaDamage(spot.x, spot.y, 54, this.atk * 0.86, { src: 'enemy', attacker: this, color: '#ff7a38' });
          }
          audio.sfx('fire');
          g.shake(11, 0.5);
        }
        if (this.stateT > 1.48) this.finishDragonSpecial();
        break;
      }
      case 'bossRush': {
        this.moving = this.stateT >= 0.48;
        if (this.stateT >= 0.48 && this.stateT < 1.02) {
          const sp = this.speed * (this.enraged ? 4.5 : 3.8);
          this.moveWithCollision(g, dt, Math.cos(this.rushA) * sp, Math.sin(this.rushA) * sp);
          if (!this.hitOnce && dist(this.x, this.y, p.x, p.y) < this.r + p.r + 18) {
            this.hitOnce = true;
            p.damage(g, this.atk * 1.35, this.x, this.y, { attacker: this });
            p.knock(p.x - this.x, p.y - this.y, 360);
          }
          if (Math.random() < 0.45) g.pt.spawn({ x: this.x, y: this.y, life: 0.35, r: 6, r2: 1, col: '#ff8a42', glow: 1 });
        }
        if (this.stateT > 1.1) this.finishDragonSpecial();
        break;
      }
    }

    // flying bob
    if (this.fly) this.z = 6 + Math.sin(this.animT * 2.2 + this.phase) * 3;

    this.applyKnock(g, dt);
    // separation from other enemies
    if (g.frame % 2 === 0) {
      for (const o of g.enemies) {
        if (o === this || !o.alive) continue;
        const dd = dist2(this.x, this.y, o.x, o.y);
        const min = (this.r + o.r) * 0.9;
        if (dd < min * min && dd > 0.01) {
          const dl = Math.sqrt(dd);
          const push = (min - dl) * 0.5;
          const ax = (this.x - o.x) / dl, ay = (this.y - o.y) / dl;
          this.x += ax * push; this.y += ay * push;
        }
      }
    }
  }

  beginDragonSpecial(g, face) {
    const moves = ['bossBreath', 'bossMeteor', 'bossRush'];
    this.state = moves[this.specialIndex++ % moves.length];
    this.stateT = 0;
    this.face = face;
    this.dir = dir4(Math.cos(face), Math.sin(face));
    this.specialFired = false;
    this.hitOnce = false;
    this.rushA = face;
    if (this.state === 'bossMeteor') {
      const p = g.player;
      const n = this.enraged ? 3 : 2;
      this.specialTargets = [];
      for (let i = 0; i < n; i++) {
        const side = i === 0 ? 0 : (i % 2 ? 1 : -1) * (42 + i * 18);
        const ahead = i * 26;
        this.specialTargets.push({
          x: p.x + Math.cos(p.face) * ahead - Math.sin(p.face) * side,
          y: p.y + Math.sin(p.face) * ahead + Math.cos(p.face) * side,
        });
      }
    } else this.specialTargets = null;
  }

  finishDragonSpecial() {
    this.state = 'chase';
    this.stateT = 0;
    this.cool = this.enraged ? 0.75 : 1.15;
    this.specialTargets = null;
    this.specialFired = false;
    this.hitOnce = false;
  }

  doAttack(g) {
    const p = g.player;
    const T = this.T;
    const a = angleTo(this.x, this.y, p.x, p.y);
    if (T.behav === 'ranged' && dist(this.x, this.y, p.x, p.y) > 44) {
      const kind = T.proj || 'arrow';
      const sp = kind === 'fire' ? 260 : kind === 'web' ? 240 : 340;
      g.spawnProjectile({
        x: this.x + Math.cos(a) * 12, y: this.y - this.r + Math.sin(a) * 12,
        vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
        dmg: this.atk * 0.9, owner: 'enemy', kind, life: 2.2,
        aoe: kind === 'fire' ? 34 : 0, slow: kind === 'web' ? 2.5 : 0, attacker: this,
      });
      audio.sfx(kind === 'fire' ? 'fire' : 'arrow', { vol: 0.5 });
      return;
    }
    if (this.kind === 'troll') {
      g.pt.ring(this.x, this.y, { r0: 12, r1: 112, life: 0.5, col: 'rgba(255,170,90,0.88)', w: 7, squash: 0.5 });
      g.pt.burst(this.x, this.y, 28, { speed: 170, life: 0.65, r: 4, col: '#d8b078', col2: '#6a5140', g: 180, vz: 90 });
      g.areaDamage(this.x, this.y, 78, this.atk, { src: 'enemy', attacker: this });
      g.shake(9, 0.45);
      audio.sfx('crit', { vol: 0.55 });
      return;
    }
    // melee arc
    const reach = T.range + this.r + 6;
    const d = dist(this.x, this.y, p.x, p.y);
    FX.slash(g.pt, this.x + Math.cos(a) * 6, this.y + Math.sin(a) * 6, a, reach * 0.8, 'rgba(255,180,150,0.7)');
    audio.sfx('swing', { vol: 0.45 });
    if (d < reach + p.r && Math.abs(angleDiff(a, angleTo(this.x, this.y, p.x, p.y))) < 1.2) {
      p.damage(g, this.atk * (this.boss ? 1.2 : 1), this.x, this.y, { attacker: this });
    }
    if (this.boss) {
      g.shake(4, 0.2);
      g.areaDamage(this.x + Math.cos(a) * 30, this.y + Math.sin(a) * 30, 46, this.atk * 0.8, { src: 'enemy' });
    }
  }
}

// ————————————————————————————————————————————————— NPCs

const NPC_ROLES = {
  villager: { label: '村人' }, guard: { label: '衛兵' }, merchant: { label: '商人' },
  smith: { label: '鍛冶師' }, alchemist: { label: '錬金術師' }, innkeeper: { label: '宿の主人' },
  guildmaster: { label: 'ギルド長' }, elder: { label: '長老' }, priest: { label: '神官' }, child: { label: '子ども' },
};

const FIRST = ['アイナ', 'ロルフ', 'ミラ', 'ガレス', 'ユナ', 'ドルン', 'セラ', 'ヴィム', 'ノラ', 'ハルト', 'エマ', 'ジーク', 'リオ', 'クレア', 'バルド', 'ティナ', 'オーウェン', 'マリカ'];

export class NPC extends Actor {
  constructor(x, y, role, settlement, rng, home) {
    super(x, y);
    this.type = 'npc';
    this.r = 8.5;
    this.role = role;
    this.settlement = settlement;
    this.home = home;
    this.name = rng.pick(FIRST);
    this.roleLabel = (NPC_ROLES[role] || NPC_ROLES.villager).label;
    this.maxHp = this.hp = 60;
    this.speed = 42;
    this.wanderT = rng() * 3;
    this.wanderA = rng() * TAU;
    this.talkT = 0;
    this.id = settlement.id + '_npc_' + Math.round(x) + '_' + Math.round(y);
    const hairs = ['#3a2418', '#6b4a2a', '#c8a860', '#8a8474', '#2e2119', '#a85a3a'];
    const shirts = ['#8a5c4a', '#4a6f8a', '#6a7a4a', '#7a5c8a', '#8a7a4a', '#5a6a7a', '#a06a5a'];
    this.pal = {
      skin: rng.pick(['#f0c8a0', '#e8b98e', '#d0a070', '#b88a5a', '#8d6748']),
      hair: rng.pick(hairs), hairStyle: rng.int(4),
      shirt: rng.pick(shirts), pants: rng.pick(['#3d4450', '#4a3f34', '#514a3a']),
      boots: '#4a3423',
    };
    if (role === 'guard') {
      this.pal.armor = '#8b8f96'; this.pal.helm = '#9aa4ae'; this.pal.plume = '#c8384a';
      this.equipVis = { weapon: 'spear', weaponColor: '#cfd6dd', weaponLen: 24, shield: 'kite', shieldColor: '#5a6a8a' };
      this.maxHp = this.hp = 200;
    } else if (role === 'priest') {
      this.pal.cloak = '#e8e2d0'; this.pal.hairStyle = 3;
    } else if (role === 'child') {
      this.scale = 0.74;
    } else if (role === 'smith') {
      this.equipVis = { weapon: 'mace', weaponColor: '#8a7a6a', weaponLen: 13 };
      this.pal.shirt = '#6a4a3a';
    }
    this.dialogueSeed = (rng() * 1e9) | 0;
  }

  update(dt, g) {
    this.animT += dt;
    if (this.talkT > 0) {
      this.talkT -= dt;
      this.moving = false;
      const a = angleTo(this.x, this.y, g.player.x, g.player.y);
      this.face = a;
      this.dir = dir4(Math.cos(a), Math.sin(a));
      return;
    }
    // simple schedule: gather at plaza during the day, head home at night
    const hour = g.hour();
    const night = hour < 6 || hour > 20;
    this.wanderT -= dt;
    if (this.wanderT <= 0) {
      this.wanderT = 1.6 + Math.random() * 3.4;
      this.wanderA = Math.random() * TAU;
      this.idleNow = Math.random() < 0.4;
    }
    let tx, ty;
    if (night && this.home) { tx = this.home.x; ty = this.home.y; }
    else if (this.role === 'guard' && this.post) { tx = this.post.x; ty = this.post.y; }
    else { tx = (this.settlement.x + 0.5) * TILE; ty = (this.settlement.y + 0.5) * TILE; }
    const dHome = dist(this.x, this.y, tx, ty);
    let a = this.wanderA;
    let sp = this.speed * 0.4;
    if (dHome > (night ? 26 : 90)) {
      a = angleTo(this.x, this.y, tx, ty) + Math.sin(this.animT * 0.6 + this.phase) * 0.3;
      sp = this.speed * (night ? 0.85 : 0.5);
    } else if (this.idleNow) sp = 0;
    if (sp > 0) {
      this.moveWithCollision(g, dt, Math.cos(a) * sp, Math.sin(a) * sp);
      this.face = a;
      this.dir = dir4(Math.cos(a), Math.sin(a));
      this.moving = true;
    } else this.moving = false;
  }
}

// ————————————————————————————————————————————————— projectiles

export class Projectile {
  constructor(o) { Object.assign(this, o); this.t = 0; this.dead = false; this.trail = 0; }

  update(dt, g) {
    this.t += dt;
    if (this.t > this.life) { this.dead = true; this.onExpire && this.onExpire(g, this); return; }
    const nx = this.x + this.vx * dt;
    const ny = this.y + this.vy * dt;
    if (this.kind === 'fireball' || this.kind === 'fire') {
      this.trail += dt;
      if (this.trail > 0.02) {
        this.trail = 0;
        g.pt.spawn({ x: this.x, y: this.y, vx: -this.vx * 0.06, vy: -this.vy * 0.06, vz: 12, life: 0.34, r: 5, r2: 1, col: '#ff9a3a', col2: '#ff4a2a', glow: 1, drag: 0.9, g: -20 });
      }
    } else if (this.kind === 'ice') {
      g.pt.spawn({ x: this.x, y: this.y, vx: 0, vy: 0, life: 0.2, r: 3, r2: 0.5, col: '#bfefff', glow: 1 });
    }
    this.x = nx; this.y = ny;

    // world collision
    if (g.blockedPoint(this.x, this.y)) { this.hit(g, null); return; }

    if (this.owner === 'player') {
      for (const e of g.enemies) {
        if (!e.alive) continue;
        if (dist2(this.x, this.y, e.x, e.y - e.z) < (e.r + 6) * (e.r + 6)) { this.hit(g, e); return; }
      }
    } else {
      const p = g.player;
      if (p.alive && dist2(this.x, this.y, p.x, p.y - 14) < (p.r + 8) * (p.r + 8)) { this.hit(g, p); return; }
    }
  }

  hit(g, target) {
    this.dead = true;
    const col = this.kind === 'fireball' || this.kind === 'fire' ? '#ff8a3a' : this.kind === 'ice' ? '#8fe0ff' : this.kind === 'web' ? '#e4e0d4' : '#d8c8a0';
    if (this.aoe) {
      g.pt.ring(this.x, this.y, { r0: 4, r1: this.aoe * 1.6, life: 0.35, col: 'rgba(255,170,80,0.8)', w: 5 });
      g.pt.burst(this.x, this.y, 22, { speed: 160, life: 0.5, r: 4, col: '#ff9a3a', col2: '#6a3a2a', glow: 1, g: 90, vz: 60, z: 4 });
      g.areaDamage(this.x, this.y, this.aoe, this.dmg, { src: this.owner, attacker: this.attacker, color: col });
      audio.sfx('fire', { vol: 0.6 });
      g.shake(4, 0.2);
    } else {
      g.pt.burst(this.x, this.y, 8, { speed: 90, life: 0.3, r: 2.6, col, glow: this.kind !== 'arrow' ? 1 : 0, g: 120 });
      if (target) {
        const crit = this.crit ? Math.random() < this.crit : false;
        const dealt = target.damage(g, this.dmg * (crit ? 1.85 : 1), this.x - this.vx * 0.05, this.y - this.vy * 0.05, {
          crit, knock: 110, slow: this.slow, src: this.owner, attacker: this.attacker, color: col,
        });
        if (this.owner === 'player' && target.type === 'enemy') g.player.recordHit(g, dealt, crit);
      }
    }
  }

  draw(ctx, t) {
    ctx.save();
    ctx.translate(this.x, this.y);
    const a = Math.atan2(this.vy, this.vx);
    switch (this.kind) {
      case 'arrow':
        ctx.rotate(a);
        ctx.fillStyle = '#8a6238';
        ctx.fillRect(-10, -1, 18, 2);
        ctx.fillStyle = '#cfd6dd';
        ctx.beginPath(); ctx.moveTo(8, -3); ctx.lineTo(14, 0); ctx.lineTo(8, 3); ctx.closePath(); ctx.fill();
        ctx.fillStyle = '#e8e2d0';
        ctx.beginPath(); ctx.moveTo(-10, -3); ctx.lineTo(-6, 0); ctx.lineTo(-10, 3); ctx.closePath(); ctx.fill();
        break;
      case 'fireball':
      case 'fire': {
        ctx.globalCompositeOperation = 'lighter';
        const r = this.kind === 'fireball' ? 9 : 7;
        const g1 = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 2);
        g1.addColorStop(0, 'rgba(255,240,180,0.95)');
        g1.addColorStop(0.4, 'rgba(255,140,50,0.8)');
        g1.addColorStop(1, 'rgba(200,40,20,0)');
        ctx.fillStyle = g1;
        ctx.beginPath(); ctx.arc(0, 0, r * 2, 0, TAU); ctx.fill();
        ctx.globalCompositeOperation = 'source-over';
        break;
      }
      case 'ice':
        ctx.rotate(a + t * 6);
        ctx.fillStyle = '#bfefff';
        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const aa = (i / 6) * TAU;
          ctx[i ? 'lineTo' : 'moveTo'](Math.cos(aa) * (i % 2 ? 3 : 8), Math.sin(aa) * (i % 2 ? 3 : 8));
        }
        ctx.closePath(); ctx.fill();
        break;
      case 'web':
        ctx.fillStyle = 'rgba(230,228,216,0.9)';
        ctx.beginPath(); ctx.arc(0, 0, 6, 0, TAU); ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,0.6)'; ctx.lineWidth = 1;
        for (let i = 0; i < 4; i++) {
          const aa = (i / 4) * Math.PI + t * 3;
          ctx.beginPath(); ctx.moveTo(-Math.cos(aa) * 8, -Math.sin(aa) * 8); ctx.lineTo(Math.cos(aa) * 8, Math.sin(aa) * 8); ctx.stroke();
        }
        break;
      default:
        ctx.fillStyle = '#fff';
        ctx.beginPath(); ctx.arc(0, 0, 4, 0, TAU); ctx.fill();
    }
    ctx.restore();
  }
}

// ————————————————————————————————————————————————— ground drops

export class Drop {
  constructor(x, y, payload) {
    this.x = x; this.y = y;
    this.z = 16;
    this.vz = 60 + Math.random() * 50;
    this.vx = (Math.random() - 0.5) * 60;
    this.vy = (Math.random() - 0.5) * 60;
    this.payload = payload;               // {id,n} | {equip} | {gold}
    this.t = 0;
    this.dead = false;
    this.magnet = 0;
  }
  update(dt, g) {
    this.t += dt;
    this.z += this.vz * dt;
    this.vz -= 320 * dt;
    if (this.z <= 0) { this.z = 0; this.vz *= -0.4; this.vx *= 0.6; this.vy *= 0.6; if (Math.abs(this.vz) < 12) this.vz = 0; }
    this.x += this.vx * dt; this.y += this.vy * dt;
    this.vx *= Math.pow(0.02, dt); this.vy *= Math.pow(0.02, dt);
    const p = g.player;
    const d = dist(this.x, this.y, p.x, p.y);
    if (this.t > 0.35 && d < 70) {
      const a = angleTo(this.x, this.y, p.x, p.y);
      const pull = clamp((70 - d) / 70, 0, 1) * 340;
      this.x += Math.cos(a) * pull * dt;
      this.y += Math.sin(a) * pull * dt;
    }
    if (this.t > 0.3 && d < 16) { this.dead = true; g.collect(this.payload); }
    if (this.t > 120) this.dead = true;
  }
}
