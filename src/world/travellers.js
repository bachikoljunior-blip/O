// 街道の往来
//
// 村と村を結ぶ道はあるのに、その上を誰も歩いていなかった。
// 世界で動くのは敵と、自分の村から出ない村人だけ。
// 道の上に旅人を置く。行商・巡礼・狩人・傭兵が、拠点から拠点へ向かって
// 歩いていく。夜は減り、敵を見れば来た道を引き返す。

import { Actor, TEAM } from '../game/actor.js';
import { clamp01, TAU } from '../core/math.js';

const KIND = {
  pedlar: { name: '行商人', tint: [0.45, 0.32, 0.52], speed: 1.5, weapon: null, hp: 90,
    lines: ['荷が重くてな。……次の村までは、まだ遠い。',
      '道は物騒だ。あんたも気をつけな。'] },
  pilgrim: { name: '巡礼者', tint: [0.74, 0.72, 0.66], speed: 1.3, weapon: null, hp: 80,
    lines: ['篝火から篝火へ。それだけの旅さ。',
      '残響の音が、日ごとに大きくなっている気がする。'] },
  hunter: { name: '流れの狩人', tint: [0.36, 0.32, 0.22], speed: 1.9, weapon: 'w_bow', hp: 130,
    lines: ['この先の茂みに獣の跡があった。',
      '狼は群れで来る。一匹見たら三匹だ。'] },
  sellsword: { name: '傭兵', tint: [0.38, 0.36, 0.40], speed: 1.7, weapon: 'w_sword', hp: 200,
    lines: ['雇い主を探している。金があるなら話は別だが。',
      'north の砦は落ちたと聞いた。行くならひとりでは行くな。'] },
  guard: { name: '隊商護衛', tint: [0.33, 0.34, 0.38], speed: 1.4, weapon: 'w_spear', hp: 210,
    lines: ['荷に近づくな。……見るだけならいい。',
      'この道で三度襲われた。次は無い、と親方は言うがね。'] },
};
const KINDS = Object.keys(KIND);

/**
 * 隊商。
 *
 * 街道を3分走って会うのは常に単独の一人で、荷も護衛もなく、交易の気配が
 * 無かった。荷車を挟んで隊商が通ると、道が「どこかとどこかを結んでいる」
 * ものに見える。
 *
 * 並び：先頭に商人、その後ろに荷車、左右と後ろに護衛。
 * 位置は進行方向を基準に決めるので、道が曲がれば列も曲がる。
 */
const CARAVAN_ROLES = [
  { kind: 'pedlar', along: 0, across: 0, lead: true },
  { kind: 'guard', along: -3.2, across: 2.0 },
  { kind: 'guard', along: -3.2, across: -2.0 },
  { kind: 'guard', along: -9.5, across: 0 },
];
const CART_ALONG = -6.0;

const SPAWN_MIN = 110;     // これより近くには湧かせない（湧く瞬間を見せない）
const SPAWN_MAX = 190;
const DESPAWN = 260;
const MAX = 3;

export class Traveller extends Actor {
  constructor(kind, route, dir, t, opts) {
    const k = KIND[kind];
    super({
      rig: 'humanoid', team: TEAM.NEUTRAL, radius: 0.42, height: 1.8,
      // 無敵の置物にしない。守れなければ失われるからこそ、加勢に意味が出る
      tint: k.tint, hp: k.hp || 100, poise: 30, def: 6,
      x: opts.x, y: opts.y, z: opts.z, yaw: opts.yaw || 0,
    });
    this.kind = kind;
    this.npcName = k.name;
    this.title = '旅の者';
    this.role = 'traveller';
    this.lines = k.lines;
    this.walkSpeed = k.speed;
    this.weaponModel = k.weapon;
    this.route = route;
    this.dir = dir;            // +1: pts の順に進む / -1: 逆
    this.seg = t;              // 今どの区間にいるか
    this.talking = false;
    this.interactLabel = '話す';
    this.fleeing = 0;
    this.arrived = false;
    this.detour = 0;
    this.stuck = 0;
    this.skips = 0;
  }

  /**
   * 進む向きを決める。
   *
   * これが無いと、道の脇の岩や木にぶつかった所で止まったまま歩かなくなる
   * （実測：湧いてから 16 秒で動かなくなった）。経路探索は持たないので、
   * 一歩先を扇状に試して「進めて、かつ次の点に近づく」向きを選ぶ。
   */
  steer(game, dx, dz, probe, tx, tz) {
    const base = Math.atan2(dx, dz);
    const sx = this.x, sz = this.z;
    const test = (off) => {
      this.x = sx; this.z = sz;
      const a = base + off;
      this.tryMove(game, sx + Math.sin(a) * probe, sz + Math.cos(a) * probe);
      return { moved: Math.hypot(this.x - sx, this.z - sz),
        d: Math.hypot(this.x - tx, this.z - tz) };
    };
    if (test(0).moved > probe * 0.80) { this.x = sx; this.z = sz; this.detour = 0; return 0; }
    let best = null, bestScore = Infinity;
    for (const off of [0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 2.0, -2.0, 2.6, -2.6]) {
      const r = test(off);
      if (r.moved < probe * 0.55) continue;
      let score = r.d + Math.abs(off) * 0.25;
      if (this.detour !== 0 && Math.sign(off) !== Math.sign(this.detour)) score += 1.2;
      if (score < bestScore) { bestScore = score; best = off; }
    }
    this.x = sx; this.z = sz;
    this.detour = best ?? 0;
    return this.detour;
  }

  /** 目的地の名前 */
  get destination() {
    return this.dir > 0 ? this.route.toName : this.route.fromName;
  }

  /** 今向かうべき道の点 */
  waypoint() {
    const p = this.route.pts;
    const i = Math.max(0, Math.min(p.length - 1, this.seg));
    return p[i];
  }

  /**
   * 隊商の一員としての歩き。
   * 自分で経路を追わず、隊列に割り当てられた場所へ寄る。
   * 敵が出たら、護衛は荷と敵のあいだへ立つ。
   */
  followCaravan(dt, game, threat) {
    if (this.talking) {
      this.faceTowards(game.player.x, game.player.z, dt, 6);
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      return;
    }
    if (threat && this.kind === 'guard' && !threat.dead) {
      // 間合いに入っていれば斬る。斬っているあいだは動かない
      if (this.fight(dt, game, threat)) {
        this.faceTowards(threat.x, threat.z, dt, 6);
        this.updatePhysics(dt, game);
        if (this.state !== 'attack') this.updateAnim(dt, 0);
        return;
      }
    }
    let tx = this.tx, tz = this.tz;
    if (threat && this.kind === 'guard') {
      const c = this.caravan;
      const dToFoe = Math.hypot(threat.x - this.x, threat.z - this.z);
      if (dToFoe < 9) {
        // 手が届くところまで来たら踏み込む。線を守るだけでは斬れない
        const ax = this.x - threat.x, az = this.z - threat.z;
        const al = Math.hypot(ax, az) || 1;
        tx = threat.x + (ax / al) * 1.9;
        tz = threat.z + (az / al) * 1.9;
      } else {
        // 遠いうちは、荷車と敵を結んだ線の荷車寄りに立つ
        const ax = threat.x - c.cartX, az = threat.z - c.cartZ;
        const al = Math.hypot(ax, az) || 1;
        const side = (this.slot.across || 0) * 0.5;
        tx = c.cartX + (ax / al) * 5.5 - (az / al) * side;
        tz = c.cartZ + (az / al) * 5.5 + (ax / al) * side;
      }
    }
    const dx = tx - this.x, dz = tz - this.z;
    const d = Math.hypot(dx, dz);
    let speed = 0;
    if (d > 22) {
      // ここまで離れたら、もう列ではない。道端で引っかかった者を置き去りに
      // するより、持ち場へ戻す（実測で列が 66m まで伸びた）
      this.x = tx; this.z = tz; this.y = game.world.height(tx, tz);
      this.stuck = 0;
    } else if (d > 0.55) {
      // 離れるほど急ぐ。列の中では歩く
      const hurry = 1 + Math.min(2.6, d / 4);
      speed = Math.min(this.walkSpeed * hurry, d / Math.max(dt, 1e-3));
      const off = this.steer(game, dx, dz, speed * dt, tx, tz);
      const a = Math.atan2(dx, dz) + off;
      const x0 = this.x, z0 = this.z;
      this.moveOnGround(dt, game, Math.sin(a), Math.cos(a), speed);
      // 進めない場所に挟まったままにしない
      if (Math.hypot(this.x - x0, this.z - z0) < speed * dt * 0.35) this.stuck += dt;
      else this.stuck = Math.max(0, this.stuck - dt * 2);
      if (this.stuck > 1.5) {
        this.x = tx; this.z = tz; this.y = game.world.height(tx, tz);
        this.stuck = 0;
      }
    }
    // 向き：敵がいれば敵を、無ければ進む先を見る
    if (threat) this.faceTowards(threat.x, threat.z, dt, 5);
    else if (d > 0.55) this.faceTowards(this.x + dx, this.z + dz, dt, 4);
    else this.faceTowards(this.x + Math.sin(this.caravan.yaw), this.z + Math.cos(this.caravan.yaw), dt, 3);
    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);
  }

  /**
   * 護衛の反撃。
   *
   * 立ちはだかるだけでは、賊が荷を切り崩すのを眺めることになる。
   * 一種類だけ、素直な薙ぎを持たせる。強くはない——プレイヤーが
   * 加勢すれば形勢が変わる、くらいに留める。
   */
  fight(dt, game, foe) {
    this.swingCd = (this.swingCd || 0) - dt;
    if (this.state === 'attack') {
      this.animT += dt;
      const a = this.attackDef;
      if (this.animT >= a.windup && !this.attackApplied) {
        this.attackApplied = true;
        const d = Math.hypot(foe.x - this.x, foe.z - this.z);
        if (d < a.range + foe.radius) {
          foe.takeDamage(a.dmg, { source: this, poise: a.poise }, game);
          game.fx?.hitSpark?.(foe.x, foe.y + foe.height * 0.6, foe.z);
          game.audio?.play('hit_flesh', { weight: 0.4, ...this.sfxAt() });
        }
      }
      if (this.animT >= a.windup + a.recover) { this.state = 'idle'; this.attack = null; }
      return true;
    }
    const d = Math.hypot(foe.x - this.x, foe.z - this.z);
    if (d < 2.6 && this.swingCd <= 0) {
      this.swingCd = 1.5 + Math.random() * 1.2;
      this.attackDef = { windup: 0.42, recover: 0.55, dmg: 26, range: 2.6, poise: 22 };
      this.attackApplied = false;
      this.setState('attack', 0.97, 'slash_r');
      this.attack = this.attackDef;
      game.audio?.play('swing', { pitch: 1.0, ...this.sfxAt() });
      return true;
    }
    return false;
  }

  update(dt, game) {
    if (this.talking) {
      this.faceTowards(game.player.x, game.player.z, dt, 6);
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      return;
    }
    // 敵が近ければ、まず相手と反対へ走る。落ち着いたら来た道を引き返す
    if (this.fleeing > 0) this.fleeing -= dt;
    else {
      for (const e of game.enemies) {
        if (e.dead || !e.aggro) continue;
        if (Math.hypot(e.x - this.x, e.z - this.z) < 22) {
          this.dir = -this.dir;
          this.seg = Math.max(0, Math.min(this.route.pts.length - 1, this.seg + this.dir));
          this.fleeing = 6;
          this.threat = { x: e.x, z: e.z };
          break;
        }
      }
    }

    // 逃げ始めの数秒は、道の形に関わらず相手から離れる向きへ。
    // 「来た道を戻る」だけだと、道が曲がっている所では相手に近づいてしまう
    let w = this.waypoint();
    if (this.fleeing > 3.5 && this.threat) {
      const ax = this.x - this.threat.x, az = this.z - this.threat.z;
      const al = Math.hypot(ax, az) || 1;
      w = { x: this.x + (ax / al) * 30, z: this.z + (az / al) * 30 };
    }
    const dx = w.x - this.x, dz = w.z - this.z;
    const dist = Math.hypot(dx, dz);
    if (dist < 3.2 && !(this.fleeing > 3.5 && this.threat)) {
      const next = this.seg + this.dir;
      if (next < 0 || next >= this.route.pts.length) {
        this.arrived = true;              // 端まで着いた。畳んでよい
      } else this.seg = next;
    }
    const speed = this.walkSpeed * (this.fleeing > 0 ? 1.9 : 1);
    const x0 = this.x, z0 = this.z;
    const off = this.steer(game, dx, dz, speed * dt, w.x, w.z);
    const a = Math.atan2(dx, dz) + off;
    const mx = Math.sin(a), mz = Math.cos(a);
    this.faceTowards(this.x + mx, this.z + mz, dt, 4);
    this.moveOnGround(dt, game, mx, mz, speed);
    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);
    // それでも進めないときは、その点を諦めて次へ。
    // 街道の脇に入り込んだまま永久に止まる者を残さない
    if (Math.hypot(this.x - x0, this.z - z0) < speed * dt * 0.4) this.stuck += dt;
    else this.stuck = Math.max(0, this.stuck - dt * 2);
    if (this.stuck > 3) {
      this.stuck = 0;
      // 何度飛ばしても抜けられないなら、その者は道を外れている。
      // 目印だけ先へ送って足踏みを続けさせるより、畳んで別の者を出す
      if (++this.skips >= 3) { this.arrived = true; return; }
      const next = this.seg + this.dir;
      if (next < 0 || next >= this.route.pts.length) this.arrived = true;
      else this.seg = next;
    }
  }
}

/**
 * 隊商ひとつ。
 *
 * 隊列の位置は「先頭が道のどこにいるか」から毎フレーム引き直す。
 * 各人がめいめい経路を追うと、狭い所で列が崩れて団子になる。
 */
export class Caravan {
  constructor(route, dir, seg, at, game) {
    this.route = route;
    this.dir = dir;
    this.seg = seg;
    this.x = at.x; this.z = at.z;
    this.yaw = at.yaw;
    this.stopped = 0;        // 敵を見て足を止めている残り時間
    this.arrived = false;
    this.stuck = 0;
    this.skips = 0;
    this.members = [];
    for (const role of CARAVAN_ROLES) {
      const t = new Traveller(role.kind, route, dir, seg,
        { x: at.x, y: game.world.height(at.x, at.z), z: at.z, yaw: at.yaw });
      t.caravan = this;
      t.slot = role;
      t.lead = !!role.lead;
      this.members.push(t);
    }
    this.merchant = this.members[0];
  }

  get destination() { return this.dir > 0 ? this.route.toName : this.route.fromName; }
  waypoint() {
    const p = this.route.pts;
    return p[Math.max(0, Math.min(p.length - 1, this.seg))];
  }

  /** 隊列の位置を、進行方向を基準に置き直す */
  place(game) {
    const fx = Math.sin(this.yaw), fz = Math.cos(this.yaw);
    const rx = fz, rz = -fx;                 // 右手方向
    for (const m of this.members) {
      const s = m.slot;
      const tx = this.x + fx * s.along + rx * s.across;
      const tz = this.z + fz * s.along + rz * s.across;
      m.tx = tx; m.tz = tz;
    }
    this.cartX = this.x + fx * CART_ALONG;
    this.cartZ = this.z + fz * CART_ALONG;
    this.cartY = game.world.height(this.cartX, this.cartZ);
  }

  update(dt, game) {
    // 敵が近ければ止まる。逃げずに、護衛が荷と敵のあいだに立つ
    if (this.stopped > 0) this.stopped -= dt;
    let threat = null, nd = 1e9;
    for (const e of game.enemies) {
      if (e.dead || !e.aggro) continue;
      const d = Math.hypot(e.x - this.x, e.z - this.z);
      // こちらを狙っている敵は遠くても脅威。ただの通りすがりは 26m まで
      const reach = this.members.includes(e.foe) ? 60 : 26;
      if (d < reach && d < nd) { nd = d; threat = e; }
    }
    if (threat) this.stopped = 2.5;

    if (this.stopped <= 0) {
      const w = this.waypoint();
      const dx = w.x - this.x, dz = w.z - this.z;
      const d = Math.hypot(dx, dz);
      if (d < 3.6) {
        const next = this.seg + this.dir;
        if (next < 0 || next >= this.route.pts.length) this.arrived = true;
        else this.seg = next;
      }
      const sp = 1.35;
      const x0 = this.x, z0 = this.z;
      if (d > 0.01) {
        this.x += dx / d * sp * dt;
        this.z += dz / d * sp * dt;
        const want = Math.atan2(dx, dz);
        let diff = want - this.yaw;
        while (diff > Math.PI) diff -= Math.PI * 2;
        while (diff < -Math.PI) diff += Math.PI * 2;
        this.yaw += diff * Math.min(1, dt * 2.2);
      }
      if (Math.hypot(this.x - x0, this.z - z0) < sp * dt * 0.4) this.stuck += dt;
      else this.stuck = Math.max(0, this.stuck - dt * 2);
      if (this.stuck > 3) {
        this.stuck = 0;
        if (++this.skips >= 3) { this.arrived = true; return; }
        const next = this.seg + this.dir;
        if (next < 0 || next >= this.route.pts.length) this.arrived = true;
        else this.seg = next;
      }
    }
    this.place(game);
    for (const m of this.members) m.followCaravan(dt, game, threat);
  }
}

export class Travellers {
  constructor() {
    this.list = [];
    this.caravans = [];
    this.cd = 4;
    this.caravanCd = 12;
  }

  /** 声をかけられる相手（単独の旅人と、隊商の面々） */
  everyone() {
    const out = this.list.slice();
    for (const c of this.caravans) out.push(...c.members);
    return out;
  }

  /** 夜は往来が絶える。嵐でも減る */
  traffic(game) {
    const night = game.sky.isNight ? 0.15 : 1;
    const rain = 1 - clamp01((game.sky.rainAmount || 0) * 0.7);
    return night * rain;
  }

  update(dt, game) {
    // 地下では地上の往来をすべて畳む。隊商を消し忘れると、
    // 見えないまま地中を歩き続ける
    if (game.dungeon) { this.list.length = 0; this.caravans.length = 0; return; }
    const p = game.player;
    // 遠くへ行った者・着いた者は畳む
    for (let i = this.list.length - 1; i >= 0; i--) {
      const t = this.list[i];
      const d = Math.hypot(t.x - p.x, t.z - p.z);
      if (t.arrived || d > DESPAWN) { this.list.splice(i, 1); continue; }
      t.update(dt, game);
    }
    // 隊商
    for (let i = this.caravans.length - 1; i >= 0; i--) {
      const c = this.caravans[i];
      const d = Math.hypot(c.x - p.x, c.z - p.z);
      if (c.arrived || d > DESPAWN + 60) { this.caravans.splice(i, 1); continue; }
      c.update(dt, game);
    }
    this.caravanCd -= dt;
    if (this.caravanCd <= 0) {
      this.caravanCd = 40 + Math.random() * 70;
      if (!this.caravans.length && Math.random() < this.traffic(game) * 0.8) {
        const c = this.spawnCaravan(game);
        if (c) this.caravans.push(c);
      }
    }

    this.cd -= dt;
    if (this.cd > 0) return;
    this.cd = 5 + Math.random() * 9;
    if (this.list.length >= MAX) return;
    if (Math.random() > this.traffic(game)) return;
    const t = this.spawn(game);
    if (t) this.list.push(t);
  }

  /** 隊商は長い道にしか出さない。すぐ着いてしまう道では列を組む意味がない */
  spawnCaravan(game) {
    const p = game.player;
    const cand = [];
    for (const r of game.world.routes) {
      if (r.pts.length < 8) continue;
      for (let i = 2; i < r.pts.length - 2; i++) {
        const d = Math.hypot(r.pts[i].x - p.x, r.pts[i].z - p.z);
        if (d > SPAWN_MIN && d < SPAWN_MAX) cand.push({ r, i });
      }
    }
    if (!cand.length) return null;
    const c = cand[(Math.random() * cand.length) | 0];
    const dir = c.i < c.r.pts.length / 2 ? 1 : -1;
    const at = c.r.pts[c.i];
    if (game.world.height(at.x, at.z) < 1) return null;
    const next = Math.max(0, Math.min(c.r.pts.length - 1, c.i + dir));
    const yaw = Math.atan2(c.r.pts[next].x - at.x, c.r.pts[next].z - at.z);
    const van = new Caravan(c.r, dir, next, { x: at.x, z: at.z, yaw }, game);
    van.place(game);
    for (const m of van.members) { m.x = m.tx; m.z = m.tz; m.y = game.world.height(m.x, m.z); }
    return van;
  }

  /** 近くの街道の、姿が見えない距離にある点から歩き出させる */
  spawn(game) {
    const p = game.player;
    const routes = game.world.routes;
    if (!routes || !routes.length) return null;
    const cand = [];
    for (const r of routes) {
      for (let i = 0; i < r.pts.length; i++) {
        const d = Math.hypot(r.pts[i].x - p.x, r.pts[i].z - p.z);
        if (d > SPAWN_MIN && d < SPAWN_MAX) cand.push({ r, i });
      }
    }
    if (!cand.length) return null;
    const c = cand[(Math.random() * cand.length) | 0];
    // 端に立たせない（すぐ着いてしまう）
    if (c.r.pts.length < 3) return null;
    const dir = c.i < c.r.pts.length / 2 ? 1 : -1;
    const at = c.r.pts[c.i];
    const y = game.world.height(at.x, at.z);
    if (y < 1) return null;
    const kind = KINDS[(Math.random() * KINDS.length) | 0];
    const next = Math.max(0, Math.min(c.r.pts.length - 1, c.i + dir));
    const yaw = Math.atan2(c.r.pts[next].x - at.x, c.r.pts[next].z - at.z);
    return new Traveller(kind, c.r, dir, next, { x: at.x, y, z: at.z, yaw });
  }

  get count() { return this.list.length; }
  get memberCount() {
    let n = this.list.length;
    for (const c of this.caravans) n += c.members.length;
    return n;
  }
}

export { KIND as TRAVELLER_KINDS, TAU };
