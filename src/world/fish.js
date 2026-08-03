// 水の生き物：魚の群れ
//
// 世界の 27% が水面なのに、そこには何もいなかった。敵も、村人も、旅人も、
// 鳥さえ水の上には出ない。四分の一が、動くもののない板だった。
//
// 水面のすぐ下を群れが回り、ときどき一匹が跳ねる。跳ねた瞬間だけが
// 水面から見える全部なので、そこに音と飛沫を寄せてある。

import { hash2 } from '../core/noise.js';
import { clamp01, TAU } from '../core/math.js';

const CELL = 150;
const VIEW = 3;
const MAX_SCHOOLS = 5;
const DEPTH = 1.1;         // 水面からどれだけ下を泳ぐか

export class Fish {
  constructor() {
    this.schools = new Map();
    this.t = 0;
  }

  /**
   * 魚が動く度合い。
   * 朝夕がいちばん跳ね、真昼と夜は沈む。雨の日はよく出る。
   */
  activity(game) {
    const sky = game.sky;
    const h = (sky.time || 0) * 24;
    // 5時と19時に山、正午と深夜に谷
    const dawn = Math.exp(-((h - 5.5) ** 2) / 8);
    const dusk = Math.exp(-((h - 19) ** 2) / 8);
    const base = 0.25 + Math.max(dawn, dusk) * 0.75;
    return clamp01(base + (sky.rainAmount || 0) * 0.3);
  }

  update(dt, game) {
    if (game.dungeon) { this.schools.clear(); return; }
    this.t += dt;
    const p = game.player;
    const pcx = Math.floor(p.x / CELL), pcz = Math.floor(p.z / CELL);

    for (const [k, s] of this.schools) {
      if (Math.abs(s.cx - pcx) > VIEW + 1 || Math.abs(s.cz - pcz) > VIEW + 1) this.schools.delete(k);
    }
    for (let dz = -VIEW; dz <= VIEW; dz++) {
      for (let dx = -VIEW; dx <= VIEW; dx++) {
        if (this.schools.size >= MAX_SCHOOLS) break;
        const cx = pcx + dx, cz = pcz + dz;
        const key = `${cx},${cz}`;
        if (this.schools.has(key)) continue;
        if (hash2(cx, cz, 6203) > 0.62) continue;
        const s = this.makeSchool(cx, cz, game);
        if (s) this.schools.set(key, s);
      }
    }
    const act = this.activity(game);
    for (const s of this.schools.values()) this.stepSchool(s, dt, game, act);
  }

  makeSchool(cx, cz, game) {
    const x = (cx + 0.3 + hash2(cx, cz, 19) * 0.4) * CELL;
    const z = (cz + 0.3 + hash2(cx, cz, 37) * 0.4) * CELL;
    // 足のつかない深さがいる。浅瀬では跳ねても絵にならない
    const h = game.world.height(x, z);
    if (h > game.seaLevel - 2.2) return null;
    // 群れが回れるだけの広さがあるか、四方を見る
    for (const [ox, oz] of [[14, 0], [-14, 0], [0, 14], [0, -14]]) {
      if (game.world.height(x + ox, z + oz) > game.seaLevel - 0.6) return null;
    }
    const n = 5 + ((hash2(cx, cz, 83) * 8) | 0);
    const surf = game.seaLevel;
    const fish = [];
    for (let i = 0; i < n; i++) {
      const a = hash2(cx * 29 + i, cz, 107) * TAU;
      const rr = 2 + hash2(cx, cz * 13 + i, 109) * 8;
      fish.push({
        rr, ph: a, speed: 0.28 + hash2(i, cx, 211) * 0.22,
        depth: DEPTH + hash2(i * 5, cz, 307) * 1.4,
        wag: hash2(i * 11, cx, 401) * TAU,
        x: x + Math.cos(a) * rr, y: surf - DEPTH, z: z + Math.sin(a) * rr,
        yaw: a, jump: 0, jumpT: 0,
      });
    }
    return { cx, cz, x, z, surf, fish, spin: hash2(cx, cz, 503) < 0.5 ? 1 : -1,
      cd: 1 + hash2(cx, cz, 601) * 5, startle: 0 };
  }

  /**
   * その場所の魚影の濃さ（0〜1）。釣りが「どこで釣れるか」を決めるのに使う。
   *
   * はじめ「近くの群れまでの距離」だけで測ったら、実際の岸 10 箇所すべてで 0 に
   * なった。群れは 150m 格子に 38% しか置かれないので、糸の届く岸から最寄りの
   * 群れまでは中央値 197m ある。それでは大魚も月光魚も永久に出ない。
   *
   * 直しかたは、群れの居場所を探すのをやめて、群れが場所を選ぶときの条件
   * ——足のつかない深さと、まわりがどれだけ水か——を投げた先で測ること。
   * 実測で、ふつうの岸は 0.37〜0.79（中央 0.45）、群れの真上は 0.83〜1.00 に
   * 割れた。群れが本当に近ければ、その分を上乗せする。
   */
  richnessAt(game, x, z) {
    const depth = game.seaLevel - game.world.height(x, z);
    if (depth <= 0) return 0;
    let open = 0, tot = 0;
    for (let a = 0; a < 12; a++) {
      const ang = a * (TAU / 12), sx = Math.sin(ang), sz = Math.cos(ang);
      for (let k = 0; k < 3; k++) {
        const rr = 8 + k * 9;
        tot++;
        if (game.world.height(x + sx * rr, z + sz * rr) < game.seaLevel - 0.4) open++;
      }
    }
    const q = clamp01(clamp01((depth - 0.6) / 3.0) * 0.5 + (open / tot) * 0.55);
    let near = 0;
    for (const s of this.schools.values()) {
      const d = Math.hypot(s.x - x, s.z - z);
      if (d > 45) continue;
      near = Math.max(near, clamp01(1 - d / 45) * clamp01(s.fish.length / 11));
    }
    return clamp01(q + near * 0.35);
  }

  /** 水面が乱れた。近くの群れが散って、しばらく跳ねなくなる */
  startle(x, z, r = 20) {
    for (const s of this.schools.values()) {
      if (Math.hypot(s.x - x, s.z - z) > r) continue;
      s.startle = Math.max(s.startle || 0, 1.6);
      s.cd = Math.max(s.cd, 3);
    }
  }

  stepSchool(s, dt, game, act) {
    const p = game.player;
    const dp = Math.hypot(p.x - s.x, p.z - s.z);
    if (s.startle > 0) s.startle = Math.max(0, s.startle - dt);
    // 人が近いと散って深くへ。跳ねもしない。水面を乱されたときも同じ
    const spooked = Math.max(dp < 12 ? clamp01((12 - dp) / 8) : 0, clamp01(s.startle));

    s.cd -= dt * act;
    if (s.cd <= 0 && !spooked) {
      s.cd = 2.5 + Math.random() * 7;
      const f = s.fish[(Math.random() * s.fish.length) | 0];
      if (!f.jump) { f.jump = 1; f.jumpT = 0; }
    }

    for (const f of s.fish) {
      f.ph += f.speed * dt * s.spin * (1 + spooked * 1.6);
      const rr = f.rr * (1 + spooked * 0.5);
      const tx = s.x + Math.cos(f.ph) * rr;
      const tz = s.z + Math.sin(f.ph) * rr;
      const k = 1 - Math.exp(-4 * dt);
      const x0 = f.x, z0 = f.z;
      f.x += (tx - f.x) * k;
      f.z += (tz - f.z) * k;

      if (f.jump) {
        // 放物線。水面から出て、また入る
        f.jumpT += dt;
        const T = 0.9;
        const u = f.jumpT / T;
        if (u >= 1) {
          f.jump = 0; f.jumpT = 0;
          f.y = s.surf - f.depth;
          game.fx?.splash(f.x, s.surf, f.z, 10, 0.5);
          game.audio?.play('splash', { pitch: 1.35 + Math.random() * 0.3, x: f.x, z: f.z });
        } else {
          const hgt = 0.9 + f.rr * 0.05;
          f.y = s.surf + Math.sin(u * Math.PI) * hgt - 0.15;
          if (u < 0.06 && f.jumpT - dt <= 0) {
            game.fx?.splash(f.x, s.surf, f.z, 8, 0.4);
            game.audio?.play('splash', { pitch: 1.5 + Math.random() * 0.3, x: f.x, z: f.z });
          }
        }
      } else {
        const want = s.surf - f.depth - spooked * 1.2;
        f.y += (want - f.y) * (1 - Math.exp(-3 * dt));
      }

      const mx = f.x - x0, mz = f.z - z0;
      if (mx * mx + mz * mz > 1e-7) f.yaw = Math.atan2(mx, mz);
      f.wag += dt * (f.jump ? 16 : 7);
    }
  }

  /** 描画用。跳ねている魚は水面より上にいる */
  forEach(fn) {
    for (const s of this.schools.values()) {
      for (const f of s.fish) fn(f, f.y > s.surf);
    }
  }

  get count() {
    let n = 0;
    for (const s of this.schools.values()) n += s.fish.length;
    return n;
  }

  /** いま跳ねている数 */
  get jumping() {
    let n = 0;
    for (const s of this.schools.values()) for (const f of s.fish) if (f.jump) n++;
    return n;
  }
}
