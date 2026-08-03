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

/**
 * マスの中から、水鳥が浮けるだけの水面を探す。
 *
 * マスの中心だけを見ると、190m より小さい池や川がぜんぶ陸に見える。
 * 実測で、周囲 49 マスの中心がすべて陸（16〜39m）と出て、水鳥が
 * 一度も湧かなかった。
 */
const _wetCache = new Map();
function waterSpotIn(cx, cz, game) {
  // 地形は動かないので、一度調べたら覚えておく。
  // 毎フレーム 49 マス × 7 点 × 5 回の高さ引きを回して、鳥の更新が
  // 77µs から 342µs に膨らんだ
  const key = `${cx},${cz}`;
  if (_wetCache.has(key)) return _wetCache.get(key);
  const found = findWaterSpot(cx, cz, game);
  if (_wetCache.size > 4000) _wetCache.clear();
  _wetCache.set(key, found);
  return found;
}

function findWaterSpot(cx, cz, game) {
  const sea = game.seaLevel;
  for (let i = 0; i < 7; i++) {
    const x = (cx + 0.12 + hash2(cx * 7 + i, cz, 811) * 0.76) * CELL;
    const z = (cz + 0.12 + hash2(cx, cz * 7 + i, 823) * 0.76) * CELL;
    if (game.world.height(x, z) > sea - 1.2) continue;
    let openN = 0;
    for (const [ox, oz] of [[9, 0], [-9, 0], [0, 9], [0, -9]]) {
      if (game.world.height(x + ox, z + oz) < sea - 0.3) openN++;
    }
    if (openN >= 3) return { x, z };
  }
  return null;
}

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
    // 近いマスを埋める。
    //
    // 端から順に埋めると、陸のマスだけで枠が尽きて水鳥が一度も出なかった
    // （500 地点を巡って 0 件）。候補を先に集め、水の枠を確保してから配る。
    // 枠が埋まっているなら候補を集める必要はない
    if (this.flocks.size >= MAX_FLOCKS) {
      for (const f of this.flocks.values()) this.stepFlock(f, dt, game, act);
      return;
    }
    const cand = [];
    for (let dz = -VIEW; dz <= VIEW; dz++) {
      for (let dx = -VIEW; dx <= VIEW; dx++) {
        const cx = pcx + dx, cz = pcz + dz;
        const key = `${cx},${cz}`;
        if (this.flocks.has(key)) continue;
        if (hash2(cx, cz, 4801) > 0.55 * act) continue;
        cand.push({ cx, cz, key, d: dx * dx + dz * dz,
          water: !!waterSpotIn(cx, cz, game) });
      }
    }
    cand.sort((a, b) => a.d - b.d);
    const water = cand.filter((c) => c.water);
    const dry = cand.filter((c) => !c.water);
    let onWater = 0;
    for (const f of this.flocks.values()) if (f.water) onWater++;
    // 水の枠は 2 つまで先に埋める
    const order = [...water.slice(0, Math.max(0, 2 - onWater)), ...dry, ...water];
    for (const c of order) {
      if (this.flocks.size >= MAX_FLOCKS) break;
      if (this.flocks.has(c.key)) continue;
      const f = this.makeFlock(c.cx, c.cz, game);
      if (f) this.flocks.set(c.key, f);
    }
    for (const f of this.flocks.values()) this.stepFlock(f, dt, game, act);
  }

  makeFlock(cx, cz, game) {
    // 水の上にも降りる。水鳥は浮いて休み、驚くと水面を蹴って飛び立つ。
    // 池や川はこの格子（190m）より小さいので、マスの中を探して見つける
    const wet = waterSpotIn(cx, cz, game);
    let x, z, water = false;
    if (wet) { x = wet.x; z = wet.z; water = true; } else {
      x = (cx + 0.25 + hash2(cx, cz, 17) * 0.5) * CELL;
      z = (cz + 0.25 + hash2(cx, cz, 29) * 0.5) * CELL;
      if (game.world.params(x, z).underwater) return null;
    }
    const h = water ? game.seaLevel : game.world.height(x, z);
    if (!water && h < 1) return null;
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
    return { cx, cz, x, z, groundY: h, birds, state: PERCH, t: 0, water,
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
        if (f.water) {
          // 水面を蹴る。土煙ではなく飛沫が上がる
          for (const b of f.birds) game.fx?.splash(b.x, game.seaLevel, b.z, 5, 0.35);
        } else {
          game.fx?.dust(f.x, f.groundY + 0.2, f.z, 6);
        }
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
        // 水鳥は浮かぶ。波にわずかに上下する
        ty = f.water
          ? game.seaLevel + 0.10 + Math.sin(this.t * 1.4 + b.ph) * 0.06
          : game.groundHeight(tx, tz) + 0.12;
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
        const rest = f.water ? game.seaLevel + 0.10 : f.groundY;
        ty = f.state === LAND
          ? rest + Math.max(0.12, (1 - clamp01(f.t / 2.4)) * (b.lift + 3))
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
