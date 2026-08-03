// 街道の往来
//
// 村と村を結ぶ道はあるのに、その上を誰も歩いていなかった。
// 世界で動くのは敵と、自分の村から出ない村人だけ。
// 道の上に旅人を置く。行商・巡礼・狩人・傭兵が、拠点から拠点へ向かって
// 歩いていく。夜は減り、敵を見れば来た道を引き返す。

import { Actor, TEAM } from '../game/actor.js';
import { clamp01, TAU } from '../core/math.js';

const KIND = {
  pedlar: { name: '行商人', tint: [0.45, 0.32, 0.52], speed: 1.5, weapon: null,
    lines: ['荷が重くてな。……次の村までは、まだ遠い。',
      '道は物騒だ。あんたも気をつけな。'] },
  pilgrim: { name: '巡礼者', tint: [0.74, 0.72, 0.66], speed: 1.3, weapon: null,
    lines: ['篝火から篝火へ。それだけの旅さ。',
      '残響の音が、日ごとに大きくなっている気がする。'] },
  hunter: { name: '流れの狩人', tint: [0.36, 0.32, 0.22], speed: 1.9, weapon: 'w_bow',
    lines: ['この先の茂みに獣の跡があった。',
      '狼は群れで来る。一匹見たら三匹だ。'] },
  sellsword: { name: '傭兵', tint: [0.38, 0.36, 0.40], speed: 1.7, weapon: 'w_sword',
    lines: ['雇い主を探している。金があるなら話は別だが。',
      'north の砦は落ちたと聞いた。行くならひとりでは行くな。'] },
};
const KINDS = Object.keys(KIND);

const SPAWN_MIN = 110;     // これより近くには湧かせない（湧く瞬間を見せない）
const SPAWN_MAX = 190;
const DESPAWN = 260;
const MAX = 3;

export class Traveller extends Actor {
  constructor(kind, route, dir, t, opts) {
    const k = KIND[kind];
    super({
      rig: 'humanoid', team: TEAM.NEUTRAL, radius: 0.42, height: 1.8,
      tint: k.tint, hp: 99999, poise: 99999,
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

export class Travellers {
  constructor() {
    this.list = [];
    this.cd = 4;
  }

  /** 夜は往来が絶える。嵐でも減る */
  traffic(game) {
    const night = game.sky.isNight ? 0.15 : 1;
    const rain = 1 - clamp01((game.sky.rainAmount || 0) * 0.7);
    return night * rain;
  }

  update(dt, game) {
    if (game.dungeon) { this.list.length = 0; return; }
    const p = game.player;
    // 遠くへ行った者・着いた者は畳む
    for (let i = this.list.length - 1; i >= 0; i--) {
      const t = this.list[i];
      const d = Math.hypot(t.x - p.x, t.z - p.z);
      if (t.arrived || d > DESPAWN) { this.list.splice(i, 1); continue; }
      t.update(dt, game);
    }
    this.cd -= dt;
    if (this.cd > 0) return;
    this.cd = 5 + Math.random() * 9;
    if (this.list.length >= MAX) return;
    if (Math.random() > this.traffic(game)) return;
    const t = this.spawn(game);
    if (t) this.list.push(t);
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
}

export { KIND as TRAVELLER_KINDS, TAU };
