// 環境の生き物：鳥の群れ
//
// 世界に動くものが、敵と村人しかいなかった。荒野を歩いているあいだ、
// 目に入るのは草と岩だけで、生き物の気配がない。
// 鳥を置く。地面に降りていて、近づくと一斉に飛び立ち、しばらく旋回して、
// また降りる。遠くの空にも、ただ渡っていく群れがいる。

import { hash2 } from '../core/noise.js';
import { clamp01, TAU } from '../core/math.js';

const CELL = 190;          // 群れが湧く格子の目
const VIEW = 3;            // 自分のまわり何マスぶんを持つか
const MAX_FLOCKS = 7;

/** 状態：地に降りている → 飛び立つ → 旋回 → 降りる */
const PERCH = 0, FLUSH = 1, CIRCLE = 2, LAND = 3;

export class Wildlife {
  constructor() {
    this.flocks = new Map();   // "cx,cz" -> flock
    this.t = 0;
  }

  /** 夜と嵐は鳥が出ない。雨でも数が減る */
  activity(game) {
    const sky = game.sky;
    const night = sky.isNight ? 0.12 : 1;
    const rain = 1 - clamp01((sky.rainAmount || 0) * 0.9);
    const snow = 1 - clamp01((sky.snowAmount || 0) * 1.0);
    return night * rain * snow;
  }

  update(dt, game) {
    if (game.dungeon) { this.flocks.clear(); return; }
    this.t += dt;
    const p = game.player;
    const act = this.activity(game);
    const pcx = Math.floor(p.x / CELL), pcz = Math.floor(p.z / CELL);

    // 遠いマスは捨てる。
    // 日が落ちる・嵐になると、ねぐらへ帰って画面から消える
    // （湧かなくするだけでは、すでに出ている群れが夜通し飛び続ける）
    for (const [k, f] of this.flocks) {
      const far = Math.abs(f.cx - pcx) > VIEW + 1 || Math.abs(f.cz - pcz) > VIEW + 1;
      const roost = act < 0.25 && f.state === PERCH && f.t > 1.5;
      if (far || roost) this.flocks.delete(k);
    }
    // 近いマスを埋める
    for (let dz = -VIEW; dz <= VIEW; dz++) {
      for (let dx = -VIEW; dx <= VIEW; dx++) {
        if (this.flocks.size >= MAX_FLOCKS) break;
        const cx = pcx + dx, cz = pcz + dz;
        const key = `${cx},${cz}`;
        if (this.flocks.has(key)) continue;
        const h = hash2(cx, cz, 4801);
        if (h > 0.55 * act) continue;
        const f = this.makeFlock(cx, cz, game);
        if (f) this.flocks.set(key, f);
      }
    }
    for (const f of this.flocks.values()) this.stepFlock(f, dt, game, act);
  }

  makeFlock(cx, cz, game) {
    const x = (cx + 0.25 + hash2(cx, cz, 17) * 0.5) * CELL;
    const z = (cz + 0.25 + hash2(cx, cz, 29) * 0.5) * CELL;
    const params = game.world.params(x, z);
    if (params.underwater) return null;
    const h = game.world.height(x, z);
    if (h < 1) return null;
    const n = 4 + ((hash2(cx, cz, 71) * 7) | 0);
    const birds = [];
    for (let i = 0; i < n; i++) {
      const a = hash2(cx * 31 + i, cz, 101) * TAU;
      const r = 0.6 + hash2(cx, cz * 17 + i, 103) * 3.4;
      birds.push({
        ox: Math.cos(a) * r, oz: Math.sin(a) * r,
        oy: 0,
        // 群れの中の持ち場（旋回時の半径と位相）
        rr: 3 + hash2(i, cx + cz, 211) * 7,
        ph: hash2(i * 7, cz, 307) * TAU,
        lift: 2 + hash2(i, cx * 3, 401) * 9,
        flap: hash2(i * 13, cz * 5, 503) * TAU,
        x: x + Math.cos(a) * r, y: h + 0.12, z: z + Math.sin(a) * r,
        yaw: a,
      });
    }
    return { cx, cz, x, z, groundY: h, birds, state: PERCH, t: 0,
      cruiseY: h + 14 + hash2(cx, cz, 601) * 16,
      spin: hash2(cx, cz, 733) < 0.5 ? 1 : -1 };
  }

  stepFlock(f, dt, game, act) {
    const p = game.player;
    f.t += dt;
    const dp = Math.hypot(p.x - f.x, p.z - f.z);

    if (f.state === PERCH) {
      // 近づかれたら一斉に飛び立つ
      if (dp < 14 || act < 0.25) {
        f.state = FLUSH; f.t = 0;
        game.audio?.play('flush', { x: f.x, z: f.z });
        game.fx?.dust(f.x, f.groundY + 0.2, f.z, 6);
      }
    } else if (f.state === FLUSH) {
      if (f.t > 1.6) { f.state = CIRCLE; f.t = 0; }
    } else if (f.state === CIRCLE) {
      // 人が離れて、しばらく経ったら降りる
      if (f.t > 9 && dp > 26 && act > 0.25) { f.state = LAND; f.t = 0; }
      if (f.t > 40) { f.state = LAND; f.t = 0; }
    } else if (f.state === LAND) {
      if (f.t > 2.4) { f.state = PERCH; f.t = 0; }
      if (dp < 14) { f.state = CIRCLE; f.t = 0; }
    }

    const flying = f.state !== PERCH;
    const wind = game.sky.wind || 0;
    for (const b of f.birds) {
      let tx, ty, tz;
      if (f.state === PERCH) {
        tx = f.x + b.ox; tz = f.z + b.oz;
        ty = game.groundHeight(tx, tz) + 0.12;
      } else if (f.state === FLUSH) {
        // まっすぐ上へ散る
        const k = clamp01(f.t / 1.6);
        tx = f.x + b.ox * (1 + k * 2.5);
        tz = f.z + b.oz * (1 + k * 2.5);
        ty = f.groundY + k * b.lift;
      } else {
        // 旋回。風下へ少し流されながら回る
        const a = b.ph + f.t * (0.5 + 1.6 / b.rr) * f.spin;
        tx = f.x + Math.cos(a) * b.rr + (game.sky.windDir?.[0] || 0) * wind * 2;
        tz = f.z + Math.sin(a) * b.rr + (game.sky.windDir?.[1] || 0) * wind * 2;
        ty = f.state === LAND
          ? f.groundY + Math.max(0.12, (1 - clamp01(f.t / 2.4)) * (b.lift + 3))
          : f.cruiseY + Math.sin(f.t * 0.6 + b.ph) * 1.6;
      }
      const k = 1 - Math.exp(-(flying ? 2.6 : 5.0) * dt);
      const px0 = b.x, pz0 = b.z;
      b.x += (tx - b.x) * k;
      b.y += (ty - b.y) * k;
      b.z += (tz - b.z) * k;
      const mx = b.x - px0, mz = b.z - pz0;
      if (mx * mx + mz * mz > 1e-6) b.yaw = Math.atan2(mx, mz);
      // 羽ばたき。飛んでいる間だけ速く打つ
      b.flap += dt * (flying ? 13 : 2.2);
      b.speed = Math.hypot(mx, mz) / Math.max(dt, 1e-4);
    }
  }

  /** 描画用に、今いる鳥を順に返す */
  forEach(fn) {
    for (const f of this.flocks.values()) {
      const flying = f.state !== PERCH;
      for (const b of f.birds) fn(b, flying);
    }
  }

  get count() {
    let n = 0;
    for (const f of this.flocks.values()) n += f.birds.length;
    return n;
  }
}
