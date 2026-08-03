// 三人称カメラ（追従・ロックオン・地形衝突・シェイク）

import { m4, v3, clamp, clamp01, lerp, damp, dampAngle, angDelta, TAU } from '../core/math.js';

export class Camera {
  constructor() {
    this.yaw = 0;
    this.pitch = 0.26;
    this.distance = 6.1;
    this.targetDistance = 6.1;
    this.height = 1.72;
    this.shoulder = 0.62;      // 肩越し視点の横オフセット
    this.pos = v3.new(0, 5, 10);
    this.look = v3.new();
    this.view = m4.new();
    this.proj = m4.new();
    this.viewProj = m4.new();
    this.invViewProj = m4.new();
    this.fov = 60 * Math.PI / 180;
    this.baseFov = 60 * Math.PI / 180;
    this.trauma = 0;
    this.shakeOffset = v3.new();
    this.kickV = v3.new();      // 打撃方向の突き（速度）
    this.kickP = v3.new();      // その積分（位置のずれ）
    this.aspect = 1;
    this.near = 0.15;
    this.far = 1400;
    this.smooth = v3.new();
    this.initialized = false;
    this.freeLook = 0;
  }

  shake(amount) { this.trauma = Math.min(1.4, this.trauma + amount); }

  /**
   * ワールド座標を画面上の位置に落とす。
   * out に [0..1, 0..1] を書き、画面の外／背後なら false を返す。
   */
  project(x, y, z, out) {
    const m = this.viewProj;
    const cx = m[0] * x + m[4] * y + m[8] * z + m[12];
    const cy = m[1] * x + m[5] * y + m[9] * z + m[13];
    const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (cw <= 0.0001) return false;
    out[0] = (cx / cw) * 0.5 + 0.5;
    out[1] = 1 - ((cy / cw) * 0.5 + 0.5);
    return out[0] >= -0.1 && out[0] <= 1.1 && out[1] >= -0.1 && out[1] <= 1.1;
  }

  /** 着地の沈み込み。真下へ一度落として戻す */
  drop(amount) { this.kickV[1] -= amount; }

  /**
   * 打撃の向きに合わせてカメラを突く。
   * 等方的な揺れだけでは「どちらから殴ったか」が絵に出ない。
   */
  kick(dx, dz, amount) {
    const l = Math.hypot(dx, dz) || 1;
    this.kickV[0] += (dx / l) * amount;
    this.kickV[2] += (dz / l) * amount;
    this.kickV[1] -= amount * 0.35;
  }

  update(dt, game) {
    const p = game.player;
    const inp = game.input;

    // 入力による回転
    if (!p.lockTarget) {
      this.yaw -= inp.look.x;
      this.pitch = clamp(this.pitch + inp.look.y, -0.55, 1.15);
    } else {
      // ロックオン中は対象を画面中央に
      const dx = p.lockTarget.x - p.x, dz = p.lockTarget.z - p.z;
      const want = Math.atan2(dx, dz);
      this.yaw = dampAngle(this.yaw, want, 7, dt);
      const dy = (p.lockTarget.y + p.lockTarget.height * 0.6) - (p.y + 1.4);
      const dist = Math.hypot(dx, dz);
      const wantPitch = clamp(-Math.atan2(dy, dist) + 0.22, -0.4, 0.85);
      this.pitch = damp(this.pitch, wantPitch, 5, dt);
      this.yaw -= inp.look.x * 0.35;
    }
    if (inp.wheel) {
      this.targetDistance = clamp(this.targetDistance + inp.wheel * 0.6, 2.2, 11);
      inp.wheel = 0;
    }

    // 注視点
    let tx = p.x, ty = p.y + this.height, tz = p.z;
    if (p.lockTarget) {
      tx = lerp(p.x, p.lockTarget.x, 0.22);
      tz = lerp(p.z, p.lockTarget.z, 0.22);
      ty = p.y + this.height + 0.15;
    }
    if (!this.initialized) {
      v3.set(this.smooth, tx, ty, tz);
      this.initialized = true;
    }
    const lag = p.dead ? 1.6 : 12;
    this.smooth[0] = damp(this.smooth[0], tx, lag, dt);
    this.smooth[1] = damp(this.smooth[1], ty, lag * 0.7, dt);
    this.smooth[2] = damp(this.smooth[2], tz, lag, dt);

    // 走行時に少し引いて FOV を広げる
    const sprintK = p.sprinting ? 1 : 0;
    const bossK = game.activeBoss ? 1 : 0;
    const wantDist = this.targetDistance + sprintK * 0.7 + bossK * 1.4;
    this.distance = damp(this.distance, wantDist, 4, dt);
    this.fov = damp(this.fov, this.baseFov + sprintK * 0.09 + (p.dead ? -0.1 : 0), 4, dt);

    // 位置計算
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const dirX = Math.sin(this.yaw) * cp;
    const dirZ = Math.cos(this.yaw) * cp;
    // 肩越しに少しずらす（真後ろだとキャラで視界が塞がるため）
    const sideX = Math.cos(this.yaw), sideZ = -Math.sin(this.yaw);
    const side = this.shoulder * (p.lockTarget ? 0.45 : 1);
    const pivotX = this.smooth[0] + sideX * side * 0.55;
    const pivotY = this.smooth[1];
    const pivotZ = this.smooth[2] + sideZ * side * 0.55;
    let camX = pivotX - dirX * this.distance + sideX * side * 0.45;
    let camZ = pivotZ - dirZ * this.distance + sideZ * side * 0.45;
    let camY = pivotY + sp * this.distance + 0.35;

    // 地形との衝突：視線に沿ってサンプリングし、埋まるなら手前へ
    const steps = 7;
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      const sx = lerp(pivotX, camX, t);
      const sz = lerp(pivotZ, camZ, t);
      const sy = lerp(pivotY, camY, t);
      const gh = game.groundHeight(sx, sz) + 0.55;
      // 木や岩に潜り込まないよう、障害物にぶつかったら手前で止める
      if (game.collide(sx, sz, 0.75, _probe)) {
        camX = lerp(pivotX, camX, Math.max(0.25, t * 0.85));
        camZ = lerp(pivotZ, camZ, Math.max(0.25, t * 0.85));
        camY = lerp(pivotY, camY, Math.max(0.25, t * 0.85));
        break;
      }
      if (sy < gh) {
        camX = lerp(pivotX, camX, t * 0.92);
        camZ = lerp(pivotZ, camZ, t * 0.92);
        camY = Math.max(lerp(pivotY, camY, t * 0.92), gh);
        break;
      }
    }
    const minY = game.groundHeight(camX, camZ) + 0.5;
    if (camY < minY) camY = minY;

    // シェイク
    this.trauma = Math.max(0, this.trauma - dt * 1.6);
    const s = this.trauma * this.trauma;
    if (s > 0.0001) {
      const t = game.time.now * 34;
      this.shakeOffset[0] = Math.sin(t * 1.7) * s * 0.42;
      this.shakeOffset[1] = Math.sin(t * 2.3 + 1.7) * s * 0.35;
      this.shakeOffset[2] = Math.sin(t * 1.3 + 3.1) * s * 0.42;
    } else {
      v3.set(this.shakeOffset, 0, 0, 0);
    }

    // 突きはばね的に戻す（行って戻る動きにして、ずれっぱなしにしない）
    // damp は math.js からの import 名なので、ここでは別名にする
    // 減衰は臨界（2√62 ≒ 15.7）より弱くして、行って少し戻る反動を残す
    const kStiff = 62, kDamp = 9;
    for (let i = 0; i < 3; i++) {
      this.kickV[i] += (-kStiff * this.kickP[i] - kDamp * this.kickV[i]) * dt;
      this.kickP[i] += this.kickV[i] * dt;
      if (Math.abs(this.kickP[i]) < 1e-4 && Math.abs(this.kickV[i]) < 1e-3) {
        this.kickP[i] = 0; this.kickV[i] = 0;
      }
    }

    v3.set(this.pos,
      camX + this.shakeOffset[0] + this.kickP[0],
      camY + this.shakeOffset[1] + this.kickP[1],
      camZ + this.shakeOffset[2] + this.kickP[2]);
    v3.set(this.look, pivotX, pivotY + 0.25, pivotZ);

    m4.lookAt(this.view, this.pos, this.look, UP);
    m4.perspective(this.proj, this.fov, this.aspect, this.near, this.far);
    m4.mul(this.viewProj, this.proj, this.view);
    m4.invert(this.invViewProj, this.viewProj);
  }

  resize(w, h) { this.aspect = w / h; }

  /** ワールド座標 → 画面座標（0..1）。z<0 なら背後 */
  project(x, y, z, out) {
    const m = this.viewProj;
    const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (cw <= 0.0001) { out[2] = -1; return out; }
    out[0] = (m[0] * x + m[4] * y + m[8] * z + m[12]) / cw * 0.5 + 0.5;
    out[1] = 1 - ((m[1] * x + m[5] * y + m[9] * z + m[13]) / cw * 0.5 + 0.5);
    out[2] = cw;
    return out;
  }
}

const UP = v3.new(0, 1, 0);
const _probe = [0, 0];

export { clamp01, angDelta, TAU };
