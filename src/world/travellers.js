// 街道の往来
//
// 村と村を結ぶ道はあるのに、その上を誰も歩いていなかった。
// 世界で動くのは敵と、自分の村から出ない村人だけ。
// 道の上に旅人を置く。行商・巡礼・狩人・傭兵が、拠点から拠点へ向かって
// 歩いていく。夜は減り、敵を見れば来た道を引き返す。

import { Actor, TEAM } from '../game/actor.js';
import { Enemy, spawnEnemy } from '../game/enemies.js';
import { ENEMIES } from '../game/data.js';
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
    return steerAround(this, game, dx, dz, probe, tx, tz);
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
    // 荷を持つ者は戦わない。護衛の陰へ回る
    if (threat && this.kind !== 'guard' && !threat.dead) {
      const c = this.caravan;
      const ax = c.cartX - threat.x, az = c.cartZ - threat.z;
      const al = Math.hypot(ax, az) || 1;
      const hx = c.cartX + (ax / al) * 6, hz = c.cartZ + (az / al) * 6;
      const dx2 = hx - this.x, dz2 = hz - this.z;
      const d2 = Math.hypot(dx2, dz2);
      if (d2 > 0.8) {
        const sp = this.walkSpeed * 2.0;
        const off = this.steer(game, dx2, dz2, sp * dt, hx, hz);
        const a2 = Math.atan2(dx2, dz2) + off;
        this.moveOnGround(dt, game, Math.sin(a2), Math.cos(a2), sp);
        this.faceTowards(this.x + dx2, this.z + dz2, dt, 5);
        this.updatePhysics(dt, game);
        this.updateAnim(dt, sp);
        return;
      }
      this.faceTowards(threat.x, threat.z, dt, 4);
      this.updatePhysics(dt, game);
      this.updateAnim(dt, 0);
      return;
    }
    const tx = this.tx, tz = this.tz;
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
 * 進む向きを決める（旅人と護衛で共用）。
 *
 * 経路探索は持たないので、一歩先を扇状に試して「進めて、かつ目的地に
 * 近づく」向きを選ぶ。護衛を作り直したときにこれを落としてしまい、
 * 道端の岩に引っかかって列が 26m まで伸びた。
 */
function steerAround(actor, game, dx, dz, probe, tx, tz) {
  const base = Math.atan2(dx, dz);
  const sx = actor.x, sz = actor.z;
  const test = (off) => {
    actor.x = sx; actor.z = sz;
    const a = base + off;
    actor.tryMove(game, sx + Math.sin(a) * probe, sz + Math.cos(a) * probe);
    return { moved: Math.hypot(actor.x - sx, actor.z - sz),
      d: Math.hypot(actor.x - tx, actor.z - tz) };
  };
  if (test(0).moved > probe * 0.80) { actor.x = sx; actor.z = sz; actor.detour = 0; return 0; }
  let best = null, bestScore = Infinity;
  for (const off of [0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 2.0, -2.0, 2.6, -2.6]) {
    const r = test(off);
    if (r.moved < probe * 0.55) continue;
    let score = r.d + Math.abs(off) * 0.25;
    if (actor.detour && Math.sign(off) !== Math.sign(actor.detour)) score += 1.2;
    if (score < bestScore) { bestScore = score; best = off; }
  }
  actor.x = sx; actor.z = sz;
  actor.detour = best ?? 0;
  return actor.detour;
}

/**
 * 隊商の護衛。
 *
 * 自作の粗いタイマーで戦わせていたが、20 周かけて調整した敵の型とは
 * 別物で、間合いも怯みも噛み合っていなかった。Enemy をそのまま中立の
 * 陣営で使い、戦いは同じ機構に任せる。ここが持つのは
 * 「誰と戦うか」と「戦っていないときどこに立つか」だけ。
 */
export class Guard extends Enemy {
  constructor(caravan, slot, at) {
    super(ENEMIES.caravan_guard, {
      team: TEAM.NEUTRAL, x: at.x, y: at.y, z: at.z, yaw: at.yaw, leash: 1e9,
    });
    this.caravan = caravan;
    this.slot = slot;
    this.kind = 'guard';
    this.npcName = ENEMIES.caravan_guard.name;
    this.title = '隊商の護衛';
    this.role = 'traveller';
    this.lines = ['荷に近づくな。……見るだけならいい。',
      'この道で三度襲われた。次は無い、と親方は言うがね。'];
    this.talking = false;
    this.interactLabel = '話す';
    this.walkSpeed = 1.4;
    // 踏み込み権はプレイヤーを囲む敵を絞るための仕組み。
    // 護衛は game.enemies に入らないので配られず、一度も振らなかった
    this.token = true;
  }

  /** 荷車のそばに来た敵のうち、他の護衛が手を取っていない一番近い者 */
  pickFoe(game) {
    const c = this.caravan;
    let mine = null, md = 1e9;
    for (const e of game.enemies) {
      if (e.dead || !e.aggro || e.team !== TEAM.ENEMY) continue;
      if (Math.hypot(e.x - c.cartX, e.z - c.cartZ) > 40) continue;
      const taken = c.members.some((m) => m !== this && m.foe === e);
      const score = Math.hypot(e.x - this.x, e.z - this.z) + (taken ? 14 : 0);
      if (score < md) { md = score; mine = e; }
    }
    return mine;
  }

  update(dt, game) {
    if (this.dead) { super.update(dt, game); return; }
    if (this.talking) {
      this.faceTowards(game.player.x, game.player.z, dt, 6);
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      return;
    }
    this.foe = this.pickFoe(game);
    this.homeX = this.caravan.cartX; this.homeZ = this.caravan.cartZ;
    if (this.foe) {
      this.aggro = true;
      this.token = true;
      if (this.aiState === 'idle' || this.aiState === 'return') this.aiState = 'chase';
      super.update(dt, game);
      return;
    }
    // 戦っていないときは持ち場へ。ここは隊列の仕事で、AI の仕事ではない
    this.aggro = false;
    this.aiState = 'idle';
    this.attack = null;
    const dx = (this.tx ?? this.x) - this.x, dz = (this.tz ?? this.z) - this.z;
    const d = Math.hypot(dx, dz);
    let speed = 0;
    if (d > 14) {
      this.x = this.tx; this.z = this.tz; this.y = game.world.height(this.x, this.z);
    } else if (d > 0.55) {
      speed = Math.min(this.walkSpeed * (1 + Math.min(2.6, d / 4)), d / Math.max(dt, 1e-3));
      const off = steerAround(this, game, dx, dz, speed * dt, this.tx, this.tz);
      const a = Math.atan2(dx, dz) + off;
      const x0 = this.x, z0 = this.z;
      this.faceTowards(this.x + Math.sin(a), this.z + Math.cos(a), dt, 4);
      this.moveOnGround(dt, game, Math.sin(a), Math.cos(a), speed);
      if (Math.hypot(this.x - x0, this.z - z0) < speed * dt * 0.35) this.slotStuck = (this.slotStuck || 0) + dt;
      else this.slotStuck = 0;
      if (this.slotStuck > 1.5) {
        this.x = this.tx; this.z = this.tz; this.y = game.world.height(this.x, this.z);
        this.slotStuck = 0;
      }
    } else {
      this.faceTowards(this.x + Math.sin(this.caravan.yaw), this.z + Math.cos(this.caravan.yaw), dt, 3);
    }
    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);
  }

  /** 護衛は残響を落とさない */
  sfxAt() { return { x: this.x, z: this.z }; }
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
      const spot = { x: at.x, y: game.world.height(at.x, at.z), z: at.z, yaw: at.yaw };
      const t = role.kind === 'guard'
        ? new Guard(this, role, spot)
        : new Traveller(role.kind, route, dir, seg, spot);
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
    for (const m of this.members) {
      if (m.dead) { m.update(dt, game); continue; }
      if (m instanceof Guard) m.update(dt, game);
      else m.followCaravan(dt, game, threat);
    }
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

/* ==================================================== 村の衛兵と、村の襲撃 */

const WATCH_NAMES = ['ボリス', 'ヤン', 'ヘルガ', 'ウルフ', 'マーテル', 'イーヴォ', 'グレタ', 'ダルク'];

/** 衛兵が敵を見つける範囲（村の中心から）。ここを出た喧嘩は追わない */
const WATCH_REACH = 62;

/**
 * 村の衛兵。
 *
 * 隊商の護衛（第37周）と同じ作り。Enemy を中立の陣営で使い、戦いは
 * 同じ機構に任せる。ここが持つのは「誰と戦うか」と
 * 「戦っていないときどこを歩くか」だけ。
 *
 * 護衛との違いは持ち場が動かないこと。隊列の代わりに巡回路を持つ。
 */
export class Watchman extends Enemy {
  constructor(poi, beat, at, name) {
    super(ENEMIES.village_watch, {
      team: TEAM.NEUTRAL, x: at.x, y: at.y, z: at.z, yaw: at.yaw, leash: 1e9,
    });
    this.poi = poi;
    this.kind = 'watch';
    this.role = 'watch';
    this.npcName = name;
    this.title = '村の衛兵';
    this.lines = [`${poi.name}の見張りだ。夜は出歩かんことだ。`,
      '賊は月の無い晩に来る。……今夜がそうでないといいがな。',
      '灯りの届く所にいろ。それだけで半分は助かる。'];
    this.talking = false;
    this.interactLabel = '話す';
    this.walkSpeed = 1.35;
    // 踏み込み権は game.enemies を対象に配られる。
    // 中立は配られないので、自分で持っていないと一度も振らない（第37周）
    this.token = true;
    this.beat = beat;            // 巡回路。外周を二点と、村を横切る一点
    this.postIdx = 0;
    this.waitT = 2 + Math.random() * 8;
    this.stuck = 0;
    this.detour = 0;
    this.alerted = false;
    this.indoors = false;        // 衛兵は家に入らない（描画と会話の判定を村人と揃える）
    // 兜と村の外套。遠目に村人と見分けがつくように
    this.extras = [
      { joint: 'head', shape: 'part', offset: [0, 0.14, -0.01], size: [0.44, 0.42, 0.44], tint: [0.40, 0.43, 0.40] },
      { joint: 'head', shape: 'part', offset: [0, 0.10, 0.20], size: [0.28, 0.07, 0.10], tint: [0.05, 0.05, 0.06] },
      { joint: 'chest', shape: 'partSlim', offset: [0, 0.28, 0.14], size: [0.36, 0.44, 0.10], tint: [0.44, 0.30, 0.20] },
    ];
  }

  /** 会話・描画まわりで NPC と同じ顔をするための最低限 */
  questMark() { return null; }
  activityLabel() { return this.foe ? '身構えている' : '見張っている'; }

  /** 村のそばに来た敵のうち、他の衛兵が手を取っていない一番近い者 */
  pickFoe(game) {
    const c = this.poi;
    const mates = game.watch || [];
    let mine = null, md = 1e9;
    for (const e of game.enemies) {
      // 「まだこちらに気づいていない賊」も相手にする。気づくのを待つと、
      // 村に入り込んだ賊の前で衛兵が突っ立ったまま斬られる
      if (e.dead || e.team !== TEAM.ENEMY || e.arch?.passive) continue;
      if (Math.hypot(e.x - c.x, e.z - c.z) > WATCH_REACH) continue;
      // 自分から 60m を超える相手は指さない。quarry() がそこで手を離すので、
      // 指したまま渡すと「相手が居ない」まま戦闘へ入って落ちる
      if (Math.hypot(e.x - this.x, e.z - this.z) > 54) continue;
      const taken = mates.some((w) => w !== this && w.poi === c && w.foe === e);
      // 自分を狙っている者を優先する。近い者から順に取ると、三人が同じ賊に
      // 群がって、残りの賊が誰にも咎められないまま村に入る
      const score = Math.hypot(e.x - this.x, e.z - this.z)
        + (taken ? 14 : 0) - (e.foe === this ? 12 : 0);
      if (score < md) { md = score; mine = e; }
    }
    return mine;
  }

  update(dt, game) {
    if (this.dead) { super.update(dt, game); return; }
    if (this.talking) {
      this.faceTowards(game.player.x, game.player.z, dt, 6);
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      return;
    }
    this.foe = this.pickFoe(game);
    this.homeX = this.poi.x; this.homeZ = this.poi.z;
    if (this.foe) {
      if (!this.alerted) { this.alerted = true; game.onWatchAlarm?.(this, this.foe); }
      this.aggro = true;
      this.token = true;
      if (this.aiState === 'idle' || this.aiState === 'return') this.aiState = 'chase';
      super.update(dt, game);
      return;
    }
    this.alerted = false;
    this.aggro = false;
    this.aiState = 'idle';
    this.attack = null;
    // 襲撃中は持ち場を離れ、賊の来ている側へ寄る。
    // 各自が持ち場を守ると、一人ずつ順に討たれる
    // （実測：持ち場のままだと 3 人が 45 秒で 2 人失われ、賊はほぼ無傷だった）
    const call = this.poi.underRaid ? nearestRaider(game, this.poi) : null;
    if (call) { this.march(dt, game, call); return; }
    this.patrol(dt, game);
  }

  /** 賊の来ている側へ駆けつける */
  march(dt, game, to) {
    const dx = to.x - this.x, dz = to.z - this.z;
    const speed = this.runSpeed;
    const off = steerAround(this, game, dx, dz, speed * dt, to.x, to.z);
    const a = Math.atan2(dx, dz) + off;
    this.faceTowards(this.x + Math.sin(a), this.z + Math.cos(a), dt, 5);
    this.moveOnGround(dt, game, Math.sin(a), Math.cos(a), speed);
    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);
  }

  /** 持ち場を順に歩く。着いたらしばらく立って、次の持ち場へ */
  patrol(dt, game) {
    const post = this.beat[this.postIdx % this.beat.length];
    const dx = post.x - this.x, dz = post.z - this.z;
    const d = Math.hypot(dx, dz);
    let speed = 0;
    if (d < 1.3) {
      this.waitT -= dt;
      // 持ち場では外を向いて立つ（村の中心に背を向ける）
      this.faceTowards(this.x + (this.x - this.poi.x), this.z + (this.z - this.poi.z), dt, 2);
      if (this.waitT <= 0) {
        this.postIdx = (this.postIdx + 1) % this.beat.length;
        this.waitT = 8 + Math.random() * 10;
        this.stuck = 0;
      }
    } else {
      speed = this.walkSpeed;
      const off = steerAround(this, game, dx, dz, speed * dt, post.x, post.z);
      const a = Math.atan2(dx, dz) + off;
      const x0 = this.x, z0 = this.z;
      this.faceTowards(this.x + Math.sin(a), this.z + Math.cos(a), dt, 4);
      this.moveOnGround(dt, game, Math.sin(a), Math.cos(a), speed);
      // 進めない所に挟まったら、その持ち場は諦めて次へ。
      // 村の中で瞬間移動させると、見ている前で消えて現れることになる
      if (Math.hypot(this.x - x0, this.z - z0) < speed * dt * 0.35) this.stuck += dt;
      else this.stuck = Math.max(0, this.stuck - dt * 2);
      if (this.stuck > 2.5) {
        this.stuck = 0;
        this.postIdx = (this.postIdx + 1) % this.beat.length;
        this.waitT = 6 + Math.random() * 8;
      }
    }
    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);
  }

  /** 衛兵は残響を落とさない */
  sfxAt() { return { x: this.x, z: this.z }; }
}

/** 村へ来ている賊のうち、いちばん近い者の居場所 */
function nearestRaider(game, poi) {
  let best = null, bd = 1e9;
  for (const e of game.enemies) {
    if (e.dead || e.team !== TEAM.ENEMY || e.arch?.passive) continue;
    const d = Math.hypot(e.x - poi.x, e.z - poi.z);
    if (d > WATCH_REACH + 30) continue;
    if (d < bd) { bd = d; best = e; }
  }
  return best;
}

/** 建物の中に持ち場を作らない（家の中に立つ衛兵を出さないため） */
function clearSpot(poi, r, a, game) {
  let x = poi.x + Math.cos(a) * r, z = poi.z + Math.sin(a) * r;
  const bk = game.blockers;
  if (bk) {
    const o = [x, z];
    for (let k = 0; k < 4; k++) if (!bk.resolve(o[0], o[1], 1.0, o)) break;
    x = o[0]; z = o[1];
  }
  return { x, z };
}

/**
 * 村に衛兵を立てる。
 *
 * 数は村の規模で決める。大きな村ほど雇える。庵と小屋には置かない
 * （そこに衛兵がいるなら、それはもう村である）。
 */
export function postWatch(poi, game) {
  if (poi.type !== 'village') return [];
  const size = poi.size || 6;
  const n = size >= 9 ? 3 : size >= 6 ? 2 : 1;
  const R = poi.plateauR || 24;
  const out = [];
  for (let i = 0; i < n; i++) {
    const b0 = (i / n) * TAU + 0.4;
    const span = (TAU / n) * 0.4;
    const beat = [
      clearSpot(poi, R * 0.86, b0 - span, game),
      clearSpot(poi, R * 0.86, b0 + span, game),
      clearSpot(poi, R * 0.32, b0, game),
    ];
    const at = beat[0];
    out.push(new Watchman(poi, beat,
      { x: at.x, y: game.world.height(at.x, at.z), z: at.z, yaw: b0 },
      WATCH_NAMES[(i + poi.id) % WATCH_NAMES.length]));
  }
  return out;
}

/**
 * 村の襲撃。
 *
 * 村人は日課をこなすだけで、村は決して脅かされなかった。徘徊の湧きは
 * 集落から 60m 以内を避けるので、村の中で敵に会うことが構造上ありえない
 * （実測：村の中央に 600 秒立って、いちばん近い敵で 64m）。
 *
 * 起こしすぎないことのほうが難しい。夜・村のそば・長い間隔の三つで絞る。
 */
const RAID = {
  FIRST: 300,        // 旅の始めからこれだけは起こさない
  COOLDOWN: 420,     // 一件終わってから、次を考えるまで
  CHECK: 20,         // 起こすかどうかを見に行く間隔
  CHANCE: 0.14,      // その一回で起きる確率（夜の村で平均 2 分半）
  FROM: 19,          // 日が落ちてから
  TILL: 5,           // 夜明け前まで
  NEAR: 60,          // プレイヤーが村のこれだけ内にいるとき
  RING: [56, 70],    // 賊が現れる距離。村の外、姿の見えにくい所から
  DAYS: 2,           // 同じ村は何日か空ける
  LIMIT: 150,        // 一件の長さの上限。これを過ぎたら賊は引き上げる
  ASSIGN: 0.4,       // 誰を狙うかを見直す間隔
  SEEK: 52,          // 賊が村人を見つける距離。quarry() の 60m より内に収める
  TAKEOVER: 22,      // プレイヤーがこれだけ寄れば、賊はそちらへ向き直る
};

export class VillageRaids {
  constructor() {
    this.cd = RAID.FIRST;
    this.check = RAID.CHECK;
    this.active = null;
  }

  get raiding() { return !!this.active; }

  update(dt, game) {
    if (game.dungeon) return;      // 地下にいるあいだは地上の事件を進めない
    if (this.active) { this.tick(dt, game); return; }
    this.cd -= dt;
    if (this.cd > 0) return;
    this.check -= dt;
    if (this.check > 0) return;
    this.check = RAID.CHECK;
    // 日が落ちてから夜明け前まで。isNight（夜の濃さ 0.5 超）だと 22 時を回り、
    // 村人が全員家に入ったあとにしか起きない。それでは村人が襲われる余地が無い
    const h = game.sky.hour;
    if (h < RAID.FROM && h >= RAID.TILL) return;
    if (Math.random() > RAID.CHANCE) return;
    const poi = this.pick(game);
    if (poi) this.begin(poi, game);
  }

  /** 襲える村：プレイヤーがそばにいて、しばらく襲われていない村 */
  pick(game) {
    const p = game.player;
    for (const poi of game.pois) {
      if (poi.type !== 'village') continue;
      if (Math.hypot(poi.x - p.x, poi.z - p.z) > RAID.NEAR) continue;
      if (game.sky.day - (poi.lastRaidDay ?? -99) < RAID.DAYS) continue;
      return poi;
    }
    return null;
  }

  /**
   * 襲撃を始める。テストからは直接これを呼ぶ。
   *
   * 人数は衛兵＋1（危険な地方では＋2）。実測した曲線は、衛兵3人に対して
   *   賊3 → 退けるか、二人失って追い払う
   *   賊4 → 衛兵が一〜三人倒れる（賊も全滅することがある）
   *   賊5 → 衛兵が全滅する
   * ＋1 にすると、放っておけば必ず誰か失われ、加勢すれば守り切れる。
   */
  begin(poi, game, count) {
    const guards = (game.watch || []).filter((w) => w.poi === poi && !w.dead);
    const n = count ?? Math.max(3, Math.min(6,
      guards.length + 1 + ((poi.danger || 0) >= 5 ? 1 : 0)));
    const bearing = Math.random() * TAU;
    const raiders = [];
    for (let i = 0; i < n; i++) {
      const a = bearing + (i - (n - 1) / 2) * 0.16;
      let spot = null;
      for (let k = 0; k < 8; k++) {
        const r = RAID.RING[0] + Math.random() * (RAID.RING[1] - RAID.RING[0]);
        const x = poi.x + Math.cos(a) * r, z = poi.z + Math.sin(a) * r;
        const s = game.world.sample(x, z);
        if (s.h < 1.5 || s.slope > 0.75) continue;
        if (game.blockers?.at(x, z)) continue;
        spot = { x, z };
        break;
      }
      if (!spot) continue;
      // 頭数の一人だけは重い。守り切れるかどうかがここで決まる
      const kind = i === 0 && n >= 4 && (poi.danger || 0) >= 4 ? 'brute' : 'bandit';
      const e = spawnEnemy(kind, {
        x: spot.x, y: game.world.height(spot.x, spot.z), z: spot.z,
        yaw: Math.atan2(poi.x - spot.x, poi.z - spot.z), leash: 1e9,
      });
      if (!e) continue;
      // 街道の索敵（第35周）を切る。狙う相手はこちらが毎回指す。
      // 切らないと、プレイヤーが村にいる限り村人を素通りしてしまう
      e.arch = { ...e.arch, raider: false };
      // 村へ向かって歩かせる。狙う相手は、村人が見える所まで来てから決める。
      // 遠くから相手を指しても quarry() が 60m で切るので、
      // 「指した相手を無視して 100m 先のプレイヤーへ走る」ことになる
      e.aggro = false;
      e.aiState = 'return';
      e.homeX = poi.x; e.homeZ = poi.z;
      e.raidFrom = { x: spot.x, z: spot.z };
      game.enemies.push(e);
      raiders.push(e);
    }
    if (!raiders.length) return null;
    poi.underRaid = true;
    poi.lastRaidDay = game.sky.day;
    // 前の晩に倒れてまだ起き上がっていない者を、この一件の被害に数えない
    const down0 = game.npcs.filter((n) => n.poi === poi && n.dead).length
      + (game.watch || []).filter((w) => w.poi === poi && w.dead).length;
    this.active = { poi, raiders, t: 0, assign: 0, helped: false, down0 };
    game.ui?.toast(`${poi.name}に賊が押し寄せてくる`, 'big gold');
    game.audio?.play('aggro', { pitch: 0.6, x: poi.x, z: poi.z });
    return this.active;
  }

  /** プレイヤーが賊を斬ったか（game.onActorDeath から） */
  onKill(actor) {
    if (this.active && this.active.raiders.includes(actor)) this.active.helped = true;
  }

  /** その村で狙える相手：外に出ている村人と、倒れていない衛兵 */
  victims(game, poi) {
    const out = [];
    for (const w of game.watch || []) if (w.poi === poi && !w.dead) out.push(w);
    for (const n of game.npcs) if (n.poi === poi && !n.dead && !n.indoors) out.push(n);
    return out;
  }

  tick(dt, game) {
    const r = this.active;
    r.t += dt;
    r.assign -= dt;
    const alive = r.raiders.filter((e) => !e.dead);
    if (!alive.length) { this.resolve(game, 'repelled'); return; }
    if (r.t > RAID.LIMIT) { this.resolve(game, 'withdraw'); return; }
    if (r.assign > 0) return;
    r.assign = RAID.ASSIGN;
    const targets = this.victims(game, r.poi);
    // 狙える者が誰も残っていない（皆が戸を閉めたか、倒れた）。
    // 150 秒の上限まで村の真ん中で足踏みさせず、荷を漁って引き上げさせる
    const p = game.player;
    const playerHere = !p.dead && Math.hypot(p.x - r.poi.x, p.z - r.poi.z) < 60;
    r.empty = (targets.length || playerHere) ? 0 : (r.empty || 0) + RAID.ASSIGN;
    if (r.empty > 12) { this.resolve(game, 'withdraw'); return; }
    for (const e of alive) {
      // 割って入られたら、そちらが相手（街道の襲撃と同じ扱い）。
      // 相手を外せば quarry() の既定でプレイヤーに戻る
      if (!p.dead && Math.hypot(p.x - e.x, p.z - e.z) < RAID.TAKEOVER) { e.foe = null; continue; }
      let best = null, bd = 1e9;
      for (const t of targets) {
        const d = Math.hypot(t.x - e.x, t.z - e.z);
        if (d > RAID.SEEK) continue;
        // 武器を持つ者を先に。逃げる村人だけを追い回すのは掠奪ではなく虐殺になる。
        // 同じ相手に集らせない——実測で賊全員が同じ衛兵に向き、
        // 残りの衛兵は誰も殴れないまま一人ずつ討たれた（3人中2〜3人が毎回死亡）
        const crowd = alive.filter((o) => o !== e && o.foe === t).length;
        const s = d - (t.role === 'watch' ? 6 : 0) + crowd * 11;
        if (s < bd) { bd = s; best = t; }
      }
      if (best) {
        e.foe = best;
        e.aggro = true;
        if (e.aiState === 'idle' || e.aiState === 'return') e.aiState = 'chase';
        // 中立を相手にするときは踏み込み権を自分で持つ。
        // 権利はプレイヤーを囲む数を絞る仕組みなので、中立が相手だと邪魔になる（第37周）
        e.token = true; e.tokenT = 3;
      } else {
        // 狙える相手が見えない。村の中心へ寄せ直す。
        // 既に村の中なら idle にする（returnHome は着いた所で体力を 35% 回復するので、
        // 村の真ん中に着くたびに全快した賊が出来上がる）
        e.foe = null;
        e.aggro = false;
        e.homeX = r.poi.x; e.homeZ = r.poi.z;
        e.aiState = Math.hypot(e.x - r.poi.x, e.z - r.poi.z) > 20 ? 'return' : 'idle';
      }
    }
  }

  /** 地下へ降りるなど、続けられなくなったとき */
  abandon(game) {
    if (!this.active) return;
    this.active.poi.underRaid = false;
    this.active = null;
    this.cd = RAID.COOLDOWN;
    void game;
  }

  /**
   * 決着。
   *
   * 加勢したかどうかで結果が変わる。隊商（checkRescue）と同じで、
   * 賊を斬ったのが誰かで礼を出すかを決める。衛兵が自力で追い払ったのに
   * 礼を受け取るのは違う。
   */
  resolve(game, how) {
    const r = this.active;
    const poi = r.poi;
    poi.underRaid = false;
    this.active = null;
    this.cd = RAID.COOLDOWN;
    this.check = RAID.CHECK;

    // 引き上げる賊は、来た方角へ帰す
    if (how === 'withdraw') {
      for (const e of r.raiders) {
        if (e.dead) continue;
        const f = e.raidFrom || { x: e.x, z: e.z };
        const a = Math.atan2(f.x - poi.x, f.z - poi.z);
        e.homeX = poi.x + Math.sin(a) * 240;
        e.homeZ = poi.z + Math.cos(a) * 240;
        e.leash = 24;
        e.foe = null;
        e.aggro = false;
        e.aiState = 'return';
      }
    }

    const fallen = game.npcs.filter((n) => n.poi === poi && n.dead).length;
    const guardsDown = (game.watch || []).filter((w) => w.poi === poi && w.dead).length;
    const lost = Math.max(0, fallen + guardsDown - (r.down0 || 0));
    poi.raids = (poi.raids || 0) + 1;
    if (lost) poi.sacked = (poi.sacked || 0) + 1;
    else poi.defended = (poi.defended || 0) + 1;

    const near = Math.hypot(game.player.x - poi.x, game.player.z - poi.z) < 90;
    if (r.helped && near) {
      const echo = 700 + Math.floor(Math.random() * 600);
      game.player.echo += echo;
      game.player.addItem('herb', 2);
      game.ui?.itemGain('herb', 2);
      game.ui?.toast(`${poi.name}「加勢、恩に着る」　残響 +${echo}`, 'big gold');
      game.audio?.play('levelup');
    }
    if (lost) {
      game.ui?.toast(`${poi.name}は荒らされた（倒れた者 ${lost} 人）`, 'gold');
    } else {
      game.ui?.toast(`${poi.name}は守り抜かれた`, 'gold');
    }
    return { how, lost, fallen, guardsDown, helped: r.helped, rewarded: r.helped && near };
  }
}

export { KIND as TRAVELLER_KINDS, TAU };
