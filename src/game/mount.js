// 騎乗：呼び出せる馬「灰毛」

import { Actor, TEAM } from './actor.js';
import { clamp01, lerp, rotateTowards, TAU } from '../core/math.js';

const SADDLE = [0.32, 0.20, 0.16];
const MANE = [0.20, 0.16, 0.13];

export class Mount extends Actor {
  constructor(opts = {}) {
    super({
      rig: 'beast', team: TEAM.NEUTRAL, name: '灰毛',
      hp: 400, poise: 200, def: 20,
      speed: 6.5, runSpeed: 12.5,
      tint: [0.40, 0.33, 0.27],
      scale: 1.55, radius: 0.75, height: 2.2,
      x: opts.x || 0, y: opts.y || 0, z: opts.z || 0, yaw: opts.yaw || 0,
    });
    this.mounted = false;
    this.summoned = false;
    this.stamina = 100;
    this.maxStamina = 100;
    this.idleT = 0;
    this.wanderT = 0;
    this.extras = [
      { joint: 'body', shape: 'part', offset: [0, 0.28, 0.05], size: [0.46, 0.14, 0.52], tint: SADDLE },
      { joint: 'body', shape: 'partSlim', offset: [0, 0.20, 0.34], size: [0.50, 0.16, 0.14], tint: SADDLE },
      { joint: 'neck', shape: 'partSlim', offset: [0, 0.16, 0.10], size: [0.10, 0.22, 0.46], tint: MANE },
      { joint: 'head', shape: 'partSlim', offset: [0, 0.10, 0.06], size: [0.09, 0.18, 0.30], tint: MANE },
      { joint: 'rump', shape: 'partSlim', offset: [0, 0.20, -0.16], size: [0.10, 0.16, 0.30], tint: MANE },
    ];
  }

  /** 騎乗位置（鞍の上） */
  saddlePoint(out) {
    const s = this.scale;
    out[0] = this.x - Math.sin(this.yaw) * 0.05 * s;
    out[1] = this.y + 1.02 * s;
    out[2] = this.z - Math.cos(this.yaw) * 0.05 * s;
    return out;
  }

  update(dt, game) {
    if (this.dead) {
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      return;
    }
    this.updateStatus(dt, game);

    if (this.mounted) {
      this.updateRidden(dt, game);
      return;
    }

    // 呼ばれていないときはプレイヤーの近くをうろつく
    let speed = 0;
    const p = game.player;
    const d = Math.hypot(p.x - this.x, p.z - this.z);
    if (d > 26) {
      // 離れすぎたら追いつく
      this.faceTowards(p.x, p.z, dt, 4);
      this.moveOnGround(dt, game, p.x - this.x, p.z - this.z, this.speed);
      speed = this.speed;
    } else {
      this.wanderT -= dt;
      if (this.wanderT <= 0) {
        this.wanderT = 3 + Math.random() * 5;
        this.wanderAngle = Math.random() * TAU;
        this.wanderMove = Math.random() < 0.4;
      }
      if (this.wanderMove) {
        const tx = p.x + Math.sin(this.wanderAngle) * 6;
        const tz = p.z + Math.cos(this.wanderAngle) * 6;
        if (Math.hypot(tx - this.x, tz - this.z) > 1.2) {
          this.faceTowards(tx, tz, dt, 2.5);
          this.moveOnGround(dt, game, tx - this.x, tz - this.z, this.speed * 0.28);
          speed = this.speed * 0.28;
        } else this.wanderMove = false;
      }
    }
    this.stamina = Math.min(this.maxStamina, this.stamina + 26 * dt);
    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);
  }

  updateRidden(dt, game) {
    const inp = game.input;
    const p = game.player;
    const camYaw = game.camera.yaw;

    let mx = inp.move.x, mz = inp.move.y;
    const mag = Math.min(1, Math.hypot(mx, mz));
    let wx = 0, wz = 0;
    if (mag > 0.08) {
      const fx = Math.sin(camYaw), fz = Math.cos(camYaw);
      const rx = Math.cos(camYaw), rz = -Math.sin(camYaw);
      wx = fx * (-mz) + rx * mx;
      wz = fz * (-mz) + rz * mx;
      const l = Math.hypot(wx, wz) || 1;
      wx /= l; wz /= l;
    }

    // 疾走（回避ボタン長押し）
    const wantGallop = inp.down('dodge') && mag > 0.3 && this.stamina > 2;
    this.galloping = wantGallop;
    let speed = 0;
    if (mag > 0.08) {
      speed = wantGallop ? this.runSpeed : this.speed * lerp(0.4, 1, mag);
      // 馬は急には曲がれない
      const want = Math.atan2(wx, wz);
      this.yaw = rotateTowards(this.yaw, want, (wantGallop ? 3.0 : 4.6) * dt);
      const facing = Math.cos(this.yaw - want);
      speed *= clamp01(0.55 + facing * 0.45);
      this.moveOnGround(dt, game, Math.sin(this.yaw), Math.cos(this.yaw), speed);
      if (wantGallop) {
        this.stamina -= 11 * dt;
        if (this.stamina <= 0) { this.stamina = 0; this.galloping = false; }
      } else {
        this.stamina = Math.min(this.maxStamina, this.stamina + 14 * dt);
      }
    } else {
      this.stamina = Math.min(this.maxStamina, this.stamina + 20 * dt);
    }

    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);

    // 騎手を鞍へ
    const s = this.scale;
    p.x = this.x; p.z = this.z;
    p.y = this.y + 0.98 * s;
    p.yaw = this.yaw;
    p.speed01 = 0;
    p.grounded = true;

    // 蹄の音と土煙
    if (speed > 1) {
      this._step = (this._step || 0) + dt * (1.6 + speed * 0.30);
      if (this._step > 1) {
        this._step = 0;
        const sf = game.world.sample(this.x, this.z);
        game.audio.footstep(sf.surface, 0.55);
        game.fx.dust(this.x, this.y + 0.05, this.z, 3);
      }
    }
  }

  mount(game) {
    this.mounted = true;
    const p = game.player;
    p.riding = this;
    p.lockTarget = null;
    p.state = 'ride';
    game.audio.play('mount_up');
    game.ui.toast('灰毛に騎乗した（回避ボタン長押しで疾走）');
  }

  dismount(game, thrown = false) {
    this.mounted = false;
    const p = game.player;
    p.riding = null;
    p.state = 'idle';
    // 横に降りる
    const sx = Math.cos(this.yaw), sz = -Math.sin(this.yaw);
    p.x = this.x + sx * 1.4;
    p.z = this.z + sz * 1.4;
    p.y = game.world.height(p.x, p.z);
    if (thrown) {
      p.setState('hit', 0.6);
      game.camera.shake(0.5);
      game.ui.toast('落馬した');
    }
    game.audio.play('ui_off');
  }
}
