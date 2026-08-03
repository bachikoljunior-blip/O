// 街道の往来
//
// 村と村を結ぶ道はあるのに、その上を誰も歩いていなかった。
// 世界で動くのは敵と、自分の村から出ない村人だけ。
// 道の上に旅人を置く。行商・巡礼・狩人・傭兵が、拠点から拠点へ向かって
// 歩いていく。夜は減り、敵を見れば来た道を引き返す。

import { Actor, TEAM } from '../game/actor.js';
import { Enemy } from '../game/enemies.js';
import { ENEMIES } from '../game/data.js';
import { clamp01, damp, TAU } from '../core/math.js';

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
 * 並び：先頭に親方、その後ろに駄馬、駄馬が牽く荷車、左右と後ろに護衛。
 * 位置は進行方向を基準に決めるので、道が曲がれば列も曲がる。
 */
const CARAVAN_ROLES = [
  { kind: 'pedlar', along: 0, across: 0, lead: true },
  // 護衛の持ち場は動かさない。駄馬（中心線・幅 0.7m）とは重ならないし、
  // 20 周かけて釣り合わせた戦いを、見た目の都合で揺らす理由がない
  { kind: 'guard', along: -3.2, across: 2.0 },
  { kind: 'guard', along: -3.2, across: -2.0 },
  { kind: 'guard', along: -9.5, across: 0 },
];
// 隊列の背骨。荷車の轅の先が駄馬の肩に届くように決めてある：
// 荷車の原点から轅の先まで 3.5m（モデル）× 1.15（倍率）＝ 4.0m、
// 荷車 -6.6 + 4.0 ＝ -2.6 で、駄馬（-3.0）の肩のあたりに来る。
const CART_ALONG = -6.6;
const DRAY_ALONG = -3.0;
export const CART_SCALE = 1.15;

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

/* ------------------------------------------------------------ 駄馬 */

const DRAY_HIDE = [0.33, 0.25, 0.19];
const DRAY_MANE = [0.17, 0.13, 0.10];
const HARNESS = [0.28, 0.19, 0.13];   // 革具
const COLLAR = [0.44, 0.32, 0.19];    // 首輪

/**
 * 荷車を牽く駄馬。
 *
 * 荷車だけが浮いて動いていたので、牽くものを置く。乗れないし戦わない
 * ので、mount.js の馬より簡素でよい：獣リグをそのまま使い、位置と向きは
 * 隊商が決める（自分では歩かない）。自前で経路を追わせると、轅との
 * あいだが開いて「繋がっていない」のがすぐ分かってしまう。
 *
 * 歩調は隊商の実速度から作るので、隊商が止まれば脚も止まる。
 * 止まっているときは、脅威で止まったなら首を上げて耳を伏せ、そうでなければ
 * 首を下げて草を食む。
 */
export class Dray extends Actor {
  constructor(caravan, at) {
    super({
      rig: 'beast', team: TEAM.NEUTRAL, name: '駄馬',
      hp: 260, poise: 60, def: 8,
      // runSpeed は「速さ 1 とみなす基準」。隊商は 1.35m/s で歩くので、
      // これを 3.0 にしておくと歩きの振り幅が半分ほど出る
      speed: 1.5, runSpeed: 3.0,
      tint: DRAY_HIDE, scale: 1.32, radius: 0.62, height: 1.9,
      x: at.x, y: at.y, z: at.z, yaw: at.yaw,
    });
    this.caravan = caravan;
    this.kind = 'dray';
    this.role = 'dray';
    this.rest = 1;     // 0=歩いている 1=止まっている（なめらかに動く）
    this.graze = 0;    // 首を下げて草を食んでいる量
    this.alert = 0;    // 脅威を見て顔を上げている量
    this.restT = 0;    // 止まってからの秒数
    this.chew = Math.random() * 10;
    this.speedNow = 0;
    this.extras = [
      // 頸木に掛かる首輪。ここが荷車の轅の先と重なる
      { joint: 'withers', shape: 'partSlim', offset: [0, 0.10, 0.12], size: [0.60, 0.42, 0.22], tint: COLLAR },
      // 背当てと腹帯
      { joint: 'body', shape: 'partSlim', offset: [0, 0.23, 0.04], size: [0.56, 0.13, 0.44], tint: HARNESS },
      { joint: 'body', shape: 'slab', offset: [0, 0.00, 0.08], size: [0.54, 0.50, 0.09], tint: HARNESS },
      // たてがみと尾毛
      { joint: 'neck', shape: 'partSlim', offset: [0, 0.15, 0.08], size: [0.10, 0.20, 0.44], tint: DRAY_MANE },
      { joint: 'head', shape: 'partSlim', offset: [0, 0.09, 0.05], size: [0.08, 0.16, 0.28], tint: DRAY_MANE },
      // 尻がけの革。歩くと後ろへなびくので、止まっているかどうかが遠目にも出る
      { joint: 'rump', shape: 'partSlim', offset: [0, 0.08, -0.14], size: [0.42, 0.30, 0.08], tint: HARNESS, cloth: true, seg: 0 },
    ];
    // poseActor は毎フレーム姿勢を上書きするので、足すなら解く直前しかない。
    // Pose.solve を包んで、そこで首と耳と尾を足す
    const pose = this.pose;
    const solve = pose.solve.bind(pose);
    pose.solve = (rootMat) => { this.restPose(pose); solve(rootMat); };
  }

  /** 止まっているときの首まわり。歩いているときは何も足さない */
  restPose(pose) {
    if (this.graze > 0.01) {
      // 首の付け根から一気に下ろす。中途半端に下げると「うつむいている」
      // だけになって、止まっているのか歩いているのか分からない。
      // 獣リグは neck が負で持ち上がり head が正で鼻先が下がる（実測）
      pose.add('neck', 1.00 * this.graze, 0, 0);
      pose.add('head', 0.55 * this.graze, 0, 0);
      pose.add('jaw', 0.24 * this.graze * (0.5 + 0.5 * Math.sin(this.chew * 6)), 0, 0);
    }
    if (this.alert > 0.01) {
      pose.add('neck', -0.30 * this.alert, 0, 0);
      pose.add('head', -0.30 * this.alert, 0, 0);
      pose.add('earL', -0.34 * this.alert, 0, 0);
      pose.add('earR', -0.34 * this.alert, 0, 0);
      pose.add('tail', 0, Math.sin(this.chew * 7) * 0.5 * this.alert, 0);
    }
  }

  update(dt, game, threat) {
    const c = this.caravan;
    const x0 = this.x, z0 = this.z;
    this.x = c.drayX; this.z = c.drayZ; this.y = c.drayY;
    // 実速度から歩調を作る。引っかかりの復帰で瞬間移動したときに
    // 駆け出して見えないよう頭を切る
    const v = Math.min(4, Math.hypot(this.x - x0, this.z - z0) / Math.max(dt, 1e-3));
    this.speedNow = v;
    const moving = v > 0.25;
    this.rest = damp(this.rest, moving ? 0 : 1, 2.4, dt);
    this.restT = moving ? 0 : this.restT + dt;
    this.chew += dt * (moving ? 0.5 : 1.6);

    const g0 = this.gait;
    this.updateAnim(dt, v);
    // 止まれば脚も止まる。待たせているあいだ足踏みさせない
    if (!moving) this.gait = g0;

    // 脅威で止まったなら顔を上げ、そうでなければしばらくして草を食む
    const wantAlert = threat && !threat.dead ? 1 : 0;
    // 草を食むのは「下げっぱなし＋ときどき顔を上げる」。半分下げ続けると
    // どちらとも付かない見た目になる
    const wantGraze = !wantAlert && this.rest > 0.6 && this.restT > 1.2
      ? clamp01((this.restT - 1.2) * 1.4) * (0.78 + 0.22 * Math.sin(this.chew * 0.5))
      : 0;
    this.alert = damp(this.alert, wantAlert, 5, dt);
    this.graze = damp(this.graze, wantGraze, 3, dt);
    // 止まっているあいだは体を少し振る。轅から外れない程度に
    this.yaw = c.yaw + Math.sin(this.chew * 0.7) * 0.07 * this.rest;
  }
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
    let guardNo = 0;
    for (const role of CARAVAN_ROLES) {
      const spot = { x: at.x, y: game.world.height(at.x, at.z), z: at.z, yaw: at.yaw };
      const t = role.kind === 'guard'
        ? new Guard(this, role, spot)
        : new Traveller(role.kind, route, dir, seg, spot);
      t.caravan = this;
      t.slot = role;
      t.lead = !!role.lead;
      if (role.kind === 'guard') dressGuard(t, guardNo++);
      else if (role.lead) dressMaster(t);
      this.members.push(t);
    }
    this.merchant = this.members[0];
    const spot = { x: at.x, y: game.world.height(at.x, at.z), z: at.z, yaw: at.yaw };
    this.dray = new Dray(this, spot);
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
    this.drayX = this.x + fx * DRAY_ALONG;
    this.drayZ = this.z + fz * DRAY_ALONG;
    this.drayY = game.world.height(this.drayX, this.drayZ);
  }

  /** 轅の先（頸木）の位置。駄馬と繋がって見えているかを測るための点 */
  hitchPoint() {
    const tip = CART_ALONG + 3.5 * CART_SCALE;
    return { x: this.x + Math.sin(this.yaw) * tip, z: this.z + Math.cos(this.yaw) * tip };
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
    this.dray.update(dt, game, threat);
    for (const m of this.members) {
      if (m.dead) { m.update(dt, game); continue; }
      if (m instanceof Guard) m.update(dt, game);
      else m.followCaravan(dt, game, threat);
    }
  }
}

/* ------------------------------------------- 一団に見せるための身なり */

/**
 * 親方。
 *
 * 親方と護衛が同じ体格・同じ色で並んでいると、一団ではなく同じ人が
 * 四人いるように見える。当たり判定に触れると 20 周ぶん調整してきた
 * 戦いが揺れるので、倍率と体力はそのままに、色と付け物だけで変える。
 */
function dressMaster(m) {
  m.npcName = '隊商の親方';
  m.tint = [0.40, 0.24, 0.20];
  m.extras = [
    // つば広の帽子
    { joint: 'head', shape: 'partSlim', offset: [0, 0.16, -0.01], size: [0.40, 0.26, 0.42], tint: [0.26, 0.18, 0.14] },
    { joint: 'head', shape: 'slab', offset: [0, 0.10, 0.00], size: [0.62, 0.05, 0.62], tint: [0.24, 0.16, 0.13] },
    // 厚い外套。肩から胴まで一回り大きくして、恰幅よく見せる
    { joint: 'chest', shape: 'part', offset: [0, 0.26, -0.02], size: [0.62, 0.66, 0.44], tint: [0.34, 0.20, 0.17] },
    { joint: 'shoulders', shape: 'partSlim', offset: [0, 0.02, 0], size: [0.82, 0.30, 0.40], tint: [0.30, 0.18, 0.15] },
    // 帯と、腰に下げた鍵束
    { joint: 'pelvis', shape: 'slab', offset: [0, 0.06, 0], size: [0.48, 0.14, 0.36], tint: [0.52, 0.40, 0.16] },
    { joint: 'pelvis', shape: 'partSlim', offset: [0.20, -0.06, 0.10], size: [0.08, 0.18, 0.06], tint: [0.72, 0.62, 0.26] },
    // 裾
    { joint: 'pelvis', shape: 'partSlim', offset: [0, -0.10, -0.14], size: [0.46, 0.30, 0.10], tint: [0.30, 0.18, 0.15], cloth: true, seg: 0 },
    { joint: 'pelvis', shape: 'partSlim', offset: [0, -0.32, -0.17], size: [0.40, 0.28, 0.09], tint: [0.28, 0.17, 0.14], cloth: true, seg: 1 },
  ];
}

/** 護衛。三人が同じ色・同じ格好だったので、兜と外套で差を付ける */
function dressGuard(gd, no) {
  const k = [0.92, 1.0, 1.10][no % 3];
  const t = gd.tint;
  gd.tint = [t[0] * k, t[1] * k, t[2] * k];
  const cloak = [[0.36, 0.16, 0.16], [0.20, 0.26, 0.34], [0.30, 0.28, 0.18]][no % 3];
  const ex = [];
  if (no % 3 === 0) {
    // 鉢金と面頬
    ex.push({ joint: 'head', shape: 'part', offset: [0, 0.14, -0.01], size: [0.42, 0.40, 0.42], tint: [0.44, 0.46, 0.52] });
    ex.push({ joint: 'head', shape: 'part', offset: [0, 0.10, 0.19], size: [0.26, 0.08, 0.10], tint: [0.06, 0.06, 0.08] });
  } else if (no % 3 === 1) {
    // 頭巾に鉄の額当て
    ex.push({ joint: 'head', shape: 'partSlim', offset: [0, 0.15, -0.03], size: [0.40, 0.36, 0.44], tint: [0.22, 0.22, 0.26] });
    ex.push({ joint: 'head', shape: 'slab', offset: [0, 0.16, 0.14], size: [0.32, 0.09, 0.10], tint: [0.52, 0.54, 0.58] });
  } else {
    // 兜は無し。代わりに肩当てが厚い
    ex.push({ joint: 'head', shape: 'partSlim', offset: [0, 0.14, -0.02], size: [0.36, 0.22, 0.38], tint: [0.34, 0.30, 0.24] });
  }
  for (const s of ['shoulderL', 'shoulderR']) {
    const big = no % 3 === 2 ? 0.32 : 0.26;
    ex.push({ joint: s, shape: 'part', offset: [0, -0.03, 0], size: [big, big * 0.8, big], tint: [0.40, 0.42, 0.47] });
  }
  for (let i = 0; i < 2; i++) {
    ex.push({ joint: 'pelvis', shape: 'partSlim',
      offset: [0, -0.06 - i * 0.24, -0.14 - i * 0.03], size: [0.42 - i * 0.05, 0.28, 0.09],
      tint: cloak, cloth: true, seg: i });
  }
  gd.extras = ex;
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
    // 駄馬も持ち場へ。ここを飛ばすと、湧いた最初の一歩で轅から離れて見える
    van.dray.x = van.drayX; van.dray.z = van.drayZ; van.dray.y = van.drayY;
    van.dray.yaw = van.yaw;
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
