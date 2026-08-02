// 手続き的リグとアニメーション（ボーン階層を CPU で解いてインスタンス化）

import { m4, quat, v3, clamp01, lerp, smoothstep, TAU } from '../core/math.js';

/**
 * パーツ定義
 *  name    : 関節名
 *  parent  : 親関節（null = ルート）
 *  offset  : 親関節からの位置
 *  shape   : 描画メッシュ名（null なら描画しない純粋な関節）
 *  size    : メッシュのスケール
 *  center  : 関節ローカルでのメッシュ中心
 *  shade   : 色の明暗（1=基準）
 */
function P(name, parent, offset, shape, size, center, shade = 1, opts = {}) {
  return { name, parent, offset, shape, size, center, shade, ...opts };
}

const HUMANOID = [
  P('root', null, [0, 0, 0], null, null, null),
  P('pelvis', 'root', [0, 0.92, 0], 'part', [0.42, 0.30, 0.30], [0, 0, 0], 0.9),
  P('chest', 'pelvis', [0, 0.16, 0], 'part', [0.50, 0.62, 0.33], [0, 0.27, 0], 1.0),
  P('shoulders', 'chest', [0, 0.44, 0], 'partSlim', [0.72, 0.26, 0.34], [0, 0, 0], 1.02),
  P('neck', 'chest', [0, 0.50, 0], 'partSlim', [0.20, 0.20, 0.20], [0, 0.02, 0], 0.94, { skin: true }),
  P('head', 'chest', [0, 0.58, 0], 'ball', [0.35, 0.40, 0.36], [0, 0.13, 0], 1.08, { skin: true }),
  P('shoulderL', 'chest', [-0.32, 0.42, 0], 'partSlim', [0.19, 0.50, 0.19], [0, -0.21, 0], 0.95),
  P('elbowL', 'shoulderL', [0, -0.42, 0], 'partSlim', [0.165, 0.46, 0.165], [0, -0.19, 0], 0.92, { skin: true }),
  P('handL', 'elbowL', [0, -0.40, 0], 'part', [0.17, 0.18, 0.15], [0, -0.05, 0], 0.9, { skin: true }),
  P('shoulderR', 'chest', [0.32, 0.42, 0], 'partSlim', [0.19, 0.50, 0.19], [0, -0.21, 0], 0.95),
  P('elbowR', 'shoulderR', [0, -0.42, 0], 'partSlim', [0.165, 0.46, 0.165], [0, -0.19, 0], 0.92, { skin: true }),
  P('handR', 'elbowR', [0, -0.40, 0], 'part', [0.17, 0.18, 0.15], [0, -0.05, 0], 0.9, { skin: true }),
  P('hipL', 'pelvis', [-0.16, -0.14, 0], 'partSlim', [0.22, 0.56, 0.22], [0, -0.23, 0], 0.88),
  P('kneeL', 'hipL', [0, -0.46, 0], 'partSlim', [0.19, 0.52, 0.19], [0, -0.22, 0], 0.85),
  P('footL', 'kneeL', [0, -0.43, 0], 'part', [0.20, 0.14, 0.34], [0, -0.05, 0.07], 0.75),
  P('hipR', 'pelvis', [0.16, -0.14, 0], 'partSlim', [0.22, 0.56, 0.22], [0, -0.23, 0], 0.88),
  P('kneeR', 'hipR', [0, -0.46, 0], 'partSlim', [0.19, 0.52, 0.19], [0, -0.22, 0], 0.85),
  P('footR', 'kneeR', [0, -0.43, 0], 'part', [0.20, 0.14, 0.34], [0, -0.05, 0.07], 0.75),
];

const IMP = [
  P('root', null, [0, 0, 0], null, null, null),
  P('pelvis', 'root', [0, 0.62, 0], 'part', [0.34, 0.24, 0.26], [0, 0, 0], 0.9),
  P('chest', 'pelvis', [0, 0.16, 0], 'part', [0.42, 0.42, 0.30], [0, 0.18, 0], 1.0),
  P('head', 'chest', [0, 0.42, 0], 'ball', [0.36, 0.36, 0.34], [0, 0.10, 0], 1.1),
  P('hornL', 'head', [-0.12, 0.22, 0], 'spike', [0.10, 0.28, 0.10], [0, 0.12, 0], 0.7),
  P('hornR', 'head', [0.12, 0.22, 0], 'spike', [0.10, 0.28, 0.10], [0, 0.12, 0], 0.7),
  P('shoulderL', 'chest', [-0.25, 0.30, 0], 'partSlim', [0.14, 0.34, 0.14], [0, -0.15, 0], 0.95),
  P('elbowL', 'shoulderL', [0, -0.32, 0], 'partSlim', [0.12, 0.30, 0.12], [0, -0.14, 0], 0.92),
  P('handL', 'elbowL', [0, -0.30, 0], 'part', [0.13, 0.13, 0.12], [0, -0.04, 0], 0.9),
  P('shoulderR', 'chest', [0.25, 0.30, 0], 'partSlim', [0.14, 0.34, 0.14], [0, -0.15, 0], 0.95),
  P('elbowR', 'shoulderR', [0, -0.32, 0], 'partSlim', [0.12, 0.30, 0.12], [0, -0.14, 0], 0.92),
  P('handR', 'elbowR', [0, -0.30, 0], 'part', [0.13, 0.13, 0.12], [0, -0.04, 0], 0.9),
  P('hipL', 'pelvis', [-0.13, -0.12, 0], 'partSlim', [0.16, 0.30, 0.16], [0, -0.14, 0], 0.88),
  P('kneeL', 'hipL', [0, -0.30, 0], 'partSlim', [0.14, 0.28, 0.14], [0, -0.13, 0], 0.85),
  P('footL', 'kneeL', [0, -0.27, 0], 'part', [0.15, 0.10, 0.24], [0, -0.04, 0.05], 0.75),
  P('hipR', 'pelvis', [0.13, -0.12, 0], 'partSlim', [0.16, 0.30, 0.16], [0, -0.14, 0], 0.88),
  P('kneeR', 'hipR', [0, -0.30, 0], 'partSlim', [0.14, 0.28, 0.14], [0, -0.13, 0], 0.85),
  P('footR', 'kneeR', [0, -0.27, 0], 'part', [0.15, 0.10, 0.24], [0, -0.04, 0.05], 0.75),
];

const BEAST = [
  P('root', null, [0, 0, 0], null, null, null),
  P('body', 'root', [0, 0.66, 0], 'partSlim', [0.50, 0.46, 1.02], [0, 0, -0.04], 1.0),
  P('rump', 'body', [0, 0.02, -0.42], 'partSlim', [0.48, 0.46, 0.42], [0, 0, 0], 0.96),
  P('withers', 'body', [0, 0.10, 0.38], 'partSlim', [0.50, 0.42, 0.40], [0, 0, 0], 1.02),
  P('neck', 'body', [0, 0.14, 0.50], 'partSlim', [0.30, 0.30, 0.44], [0, 0.02, 0.16], 0.96),
  P('head', 'neck', [0, 0.02, 0.32], 'partSlim', [0.29, 0.27, 0.42], [0, 0, 0.16], 1.05),
  P('snout', 'head', [0, -0.05, 0.32], 'partSlim', [0.19, 0.17, 0.26], [0, 0, 0.11], 0.98),
  P('jaw', 'head', [0, -0.11, 0.28], 'partSlim', [0.17, 0.09, 0.26], [0, 0, 0.11], 0.82),
  P('earL', 'head', [-0.11, 0.14, -0.02], 'spike', [0.10, 0.20, 0.10], [0, 0.09, 0], 0.8),
  P('earR', 'head', [0.11, 0.14, -0.02], 'spike', [0.10, 0.20, 0.10], [0, 0.09, 0], 0.8),
  P('tail', 'rump', [0, 0.12, -0.24], 'spike', [0.15, 0.62, 0.15], [0, -0.28, 0], 0.85),
  P('flHip', 'withers', [-0.20, -0.14, 0.04], 'partSlim', [0.17, 0.38, 0.17], [0, -0.17, 0], 0.9),
  P('flKnee', 'flHip', [0, -0.32, 0], 'partSlim', [0.145, 0.34, 0.145], [0, -0.15, 0], 0.86),
  P('flPaw', 'flKnee', [0, -0.29, 0], 'partSlim', [0.16, 0.11, 0.24], [0, -0.04, 0.03], 0.78),
  P('frHip', 'withers', [0.20, -0.14, 0.04], 'partSlim', [0.17, 0.38, 0.17], [0, -0.17, 0], 0.9),
  P('frKnee', 'frHip', [0, -0.32, 0], 'partSlim', [0.145, 0.34, 0.145], [0, -0.15, 0], 0.86),
  P('frPaw', 'frKnee', [0, -0.29, 0], 'partSlim', [0.16, 0.11, 0.24], [0, -0.04, 0.03], 0.78),
  P('blHip', 'rump', [-0.20, -0.12, -0.04], 'partSlim', [0.21, 0.40, 0.22], [0, -0.17, 0], 0.9),
  P('blKnee', 'blHip', [0, -0.32, 0], 'partSlim', [0.155, 0.34, 0.155], [0, -0.15, 0], 0.86),
  P('blPaw', 'blKnee', [0, -0.29, 0], 'partSlim', [0.17, 0.11, 0.24], [0, -0.04, 0.03], 0.78),
  P('brHip', 'rump', [0.20, -0.12, -0.04], 'partSlim', [0.21, 0.40, 0.22], [0, -0.17, 0], 0.9),
  P('brKnee', 'brHip', [0, -0.32, 0], 'partSlim', [0.155, 0.34, 0.155], [0, -0.15, 0], 0.86),
  P('brPaw', 'brKnee', [0, -0.29, 0], 'partSlim', [0.17, 0.11, 0.24], [0, -0.04, 0.03], 0.78),
];

const DRAGON = [
  ...BEAST.map((p) => ({ ...p })),
  P('wingL', 'body', [-0.30, 0.26, 0.05], 'part', [1.6, 0.10, 0.9], [-0.8, 0, 0], 0.8),
  P('wingL2', 'wingL', [-1.55, 0, 0], 'part', [1.4, 0.08, 0.7], [-0.7, 0, -0.1], 0.75),
  P('wingR', 'body', [0.30, 0.26, 0.05], 'part', [1.6, 0.10, 0.9], [0.8, 0, 0], 0.8),
  P('wingR2', 'wingR', [1.55, 0, 0], 'part', [1.4, 0.08, 0.7], [0.7, 0, -0.1], 0.75),
  P('hornL', 'head', [-0.14, 0.16, -0.02], 'spike', [0.11, 0.42, 0.11], [0, 0.20, 0], 0.7),
  P('hornR', 'head', [0.14, 0.16, -0.02], 'spike', [0.11, 0.42, 0.11], [0, 0.20, 0], 0.7),
];

const WRAITH = [
  P('root', null, [0, 0, 0], null, null, null),
  P('pelvis', 'root', [0, 1.05, 0], 'spike', [0.5, 1.1, 0.5], [0, -0.55, 0], 0.7),
  P('chest', 'pelvis', [0, 0.16, 0], 'part', [0.52, 0.56, 0.32], [0, 0.24, 0], 1.0),
  P('head', 'chest', [0, 0.54, 0], 'ball', [0.34, 0.40, 0.34], [0, 0.12, 0], 1.1),
  P('hood', 'head', [0, 0.06, -0.02], 'part', [0.44, 0.36, 0.42], [0, 0.06, 0], 0.7),
  P('shoulderL', 'chest', [-0.32, 0.40, 0], 'partSlim', [0.16, 0.44, 0.16], [0, -0.20, 0], 0.9),
  P('elbowL', 'shoulderL', [0, -0.42, 0], 'partSlim', [0.14, 0.40, 0.14], [0, -0.19, 0], 0.88),
  P('handL', 'elbowL', [0, -0.40, 0], 'part', [0.15, 0.15, 0.13], [0, -0.05, 0], 0.86),
  P('shoulderR', 'chest', [0.32, 0.40, 0], 'partSlim', [0.16, 0.44, 0.16], [0, -0.20, 0], 0.9),
  P('elbowR', 'shoulderR', [0, -0.42, 0], 'partSlim', [0.14, 0.40, 0.14], [0, -0.19, 0], 0.88),
  P('handR', 'elbowR', [0, -0.40, 0], 'part', [0.15, 0.15, 0.13], [0, -0.05, 0], 0.86),
];

export const RIGS = {
  humanoid: { parts: HUMANOID, weaponMount: 'handR', shieldMount: 'handL', biped: true, eye: 1.55 },
  imp: { parts: IMP, weaponMount: 'handR', shieldMount: 'handL', biped: true, eye: 1.1 },
  beast: { parts: BEAST, weaponMount: null, quadruped: true, eye: 1.0 },
  dragon: { parts: DRAGON, weaponMount: null, quadruped: true, eye: 1.4 },
  wraith: { parts: WRAITH, weaponMount: 'handR', shieldMount: null, biped: true, float: true, eye: 1.5 },
};

for (const key of Object.keys(RIGS)) {
  const r = RIGS[key];
  r.index = new Map();
  r.parts.forEach((p, i) => r.index.set(p.name, i));
  r.parentIdx = r.parts.map((p) => (p.parent ? r.index.get(p.parent) : -1));
}

/* ------------------------------------------------------------- ポーズ */
export class Pose {
  constructor(rigName) {
    this.setRig(rigName);
  }
  setRig(rigName) {
    this.rigName = rigName;
    this.rig = RIGS[rigName] || RIGS.humanoid;
    const n = this.rig.parts.length;
    this.rot = [];
    this.pos = [];
    this.world = [];
    for (let i = 0; i < n; i++) {
      this.rot.push(new Float32Array([0, 0, 0]));   // euler XYZ
      this.pos.push(new Float32Array(3));           // 追加平行移動
      this.world.push(m4.new());
    }
    this._q = quat.new();
    this._t = v3.new();
    this._s = v3.new(1, 1, 1);
    this._m = m4.new();
  }
  idx(name) { return this.rig.index.get(name); }
  set(name, x, y, z) {
    const i = this.rig.index.get(name);
    if (i === undefined) return;
    const r = this.rot[i];
    r[0] = x; r[1] = y; r[2] = z;
  }
  add(name, x, y, z) {
    const i = this.rig.index.get(name);
    if (i === undefined) return;
    const r = this.rot[i];
    r[0] += x; r[1] += y; r[2] += z;
  }
  offset(name, x, y, z) {
    const i = this.rig.index.get(name);
    if (i === undefined) return;
    const p = this.pos[i];
    p[0] = x; p[1] = y; p[2] = z;
  }
  clear() {
    for (let i = 0; i < this.rot.length; i++) {
      this.rot[i][0] = 0; this.rot[i][1] = 0; this.rot[i][2] = 0;
      this.pos[i][0] = 0; this.pos[i][1] = 0; this.pos[i][2] = 0;
    }
  }
  /** ルート行列から全関節のワールド行列を求める */
  solve(rootMat) {
    const parts = this.rig.parts, pidx = this.rig.parentIdx;
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      const q = quat.fromEuler(this._q, this.rot[i][0], this.rot[i][1], this.rot[i][2]);
      v3.set(this._t, p.offset[0] + this.pos[i][0], p.offset[1] + this.pos[i][1], p.offset[2] + this.pos[i][2]);
      m4.fromRTS(this._m, q, this._t, v3.set(this._s, 1, 1, 1));
      const parent = pidx[i] >= 0 ? this.world[pidx[i]] : rootMat;
      m4.mul(this.world[i], parent, this._m);
    }
  }
}

/* ---------------------------------------------------- アニメーション */
const A = {
  // 攻撃モーションのキーフレーム（腕の角度）: t は 0..1（全体）
  slash_r: (t) => {
    // 振りかぶり → 右から左へ薙ぐ
    const w = smoothstep(0, 0.45, t), s = smoothstep(0.45, 0.65, t), r = smoothstep(0.65, 1, t);
    return {
      shR: [-2.2 * w + 2.6 * s - 0.5 * r, 0.9 * w - 1.6 * s, -0.7 * w + 1.0 * s],
      elR: [-0.6 * w + 0.3 * s, 0, 0],
      chest: [0, 0.55 * w - 1.1 * s + 0.35 * r, 0],
      pelvis: [0, 0.2 * w - 0.4 * s, 0],
    };
  },
  slash_l: (t) => {
    const w = smoothstep(0, 0.45, t), s = smoothstep(0.45, 0.65, t), r = smoothstep(0.65, 1, t);
    return {
      shR: [-1.9 * w + 2.4 * s - 0.5 * r, -1.1 * w + 1.9 * s, 0.8 * w - 1.2 * s],
      elR: [-0.9 * w + 0.5 * s, 0, 0],
      chest: [0, -0.6 * w + 1.2 * s - 0.4 * r, 0],
      pelvis: [0, -0.22 * w + 0.42 * s, 0],
    };
  },
  overhead: (t) => {
    const w = smoothstep(0, 0.5, t), s = smoothstep(0.5, 0.68, t), r = smoothstep(0.68, 1, t);
    return {
      shR: [-2.9 * w + 3.6 * s - 0.8 * r, 0, 0],
      shL: [-2.4 * w + 3.0 * s - 0.7 * r, 0, 0],
      elR: [-1.1 * w + 1.0 * s, 0, 0],
      chest: [-0.4 * w + 0.85 * s - 0.3 * r, 0, 0],
      pelvis: [-0.15 * w + 0.3 * s, 0, 0],
    };
  },
  thrust: (t) => {
    const w = smoothstep(0, 0.5, t), s = smoothstep(0.5, 0.62, t), r = smoothstep(0.62, 1, t);
    return {
      shR: [-0.6 * w - 0.9 * s + 0.5 * r, 0.4 * w - 0.4 * s, 0],
      elR: [-1.9 * w + 1.85 * s, 0, 0],
      chest: [0, 0.5 * w - 0.55 * s, 0],
      pelvis: [0, 0.2 * w - 0.25 * s, 0],
      lunge: (s - r) * 0.5,
    };
  },
  spin: (t) => {
    const w = smoothstep(0, 0.38, t), s = smoothstep(0.38, 0.72, t), r = smoothstep(0.72, 1, t);
    return {
      shR: [-1.4 * w + 0.6 * s, 1.2 * w - 1.0 * s, -1.3 * w + 0.6 * s],
      elR: [-0.4, 0, 0],
      chest: [0, 0.7 * w - 1.3 * s + 0.5 * r, 0],
      pelvis: [0, 0.25 * w - 0.5 * s, 0],
      spinTurn: s * TAU,
    };
  },
  cast: (t) => {
    const w = smoothstep(0, 0.55, t), r = smoothstep(0.7, 1, t);
    return {
      shR: [-2.5 * w + 1.2 * r, 0, -0.5 * w],
      shL: [-1.0 * w + 0.6 * r, 0, 0.4 * w],
      elR: [-0.8 * w, 0, 0],
      chest: [-0.2 * w + 0.15 * r, 0, 0],
    };
  },
  shoot: (t) => {
    const w = smoothstep(0, 0.6, t), r = smoothstep(0.72, 1, t);
    return {
      shR: [-1.5 * w + 0.5 * r, -0.5 * w, -0.3 * w],
      shL: [-1.55 * w, 0.7 * w, 0.5 * w],
      elR: [-1.5 * w + 1.2 * r, 0, 0],
      chest: [0, -0.5 * w + 0.3 * r, 0],
    };
  },
  lunge: (t) => {
    const w = smoothstep(0, 0.45, t), s = smoothstep(0.45, 0.62, t), r = smoothstep(0.62, 1, t);
    return { neck: [0.5 * w - 1.0 * s + 0.4 * r, 0, 0], jaw: [-0.2 * w + 1.0 * s - 0.7 * r, 0, 0], lunge: (s - r) * 0.6 };
  },
  pounce: (t) => {
    const w = smoothstep(0, 0.4, t), s = smoothstep(0.4, 0.75, t);
    return { body: [-0.5 * w + 0.9 * s, 0, 0], neck: [0.4 * w - 0.7 * s, 0, 0], jaw: [-0.1 + 0.9 * s, 0, 0], airborne: s };
  },
  charge: (t) => {
    const w = smoothstep(0, 0.3, t);
    return { body: [0.25 * w, 0, 0], neck: [-0.3 * w, 0, 0] };
  },
};

/**
 * アクターの状態からポーズを作る。
 * st: { state, animT, motion, motionDur, speed01, blocking, aim, hp01, rig, floating, t }
 */
export function poseActor(pose, st) {
  pose.clear();
  const rig = pose.rig;
  const t = st.t;

  if (rig.quadruped) return poseQuadruped(pose, st);

  const walk = st.speed01;
  const phase = st.gait;

  // 待機の呼吸
  const breath = Math.sin(t * 1.6) * 0.03;
  pose.set('chest', -0.05 + breath, 0, 0);
  pose.set('head', 0.04 - breath * 0.8, Math.sin(t * 0.7) * 0.08 * (1 - walk), 0);

  // 歩行・走行
  if (walk > 0.01) {
    const amp = lerp(0.42, 1.05, walk);
    const s = Math.sin(phase), c = Math.cos(phase);
    pose.set('hipL', s * amp * 0.72, 0, 0.03);
    pose.set('hipR', -s * amp * 0.72, 0, -0.03);
    pose.set('kneeL', Math.max(0, -s) * amp * 1.1 + 0.1, 0, 0);
    pose.set('kneeR', Math.max(0, s) * amp * 1.1 + 0.1, 0, 0);
    pose.set('footL', -Math.max(0, -s) * amp * 0.5, 0, 0);
    pose.set('footR', -Math.max(0, s) * amp * 0.5, 0, 0);
    pose.add('shoulderL', -s * amp * 0.55, 0, 0.16 + walk * 0.06);
    pose.add('shoulderR', s * amp * 0.55, 0, -0.16 - walk * 0.06);
    pose.add('elbowL', -0.25 - walk * 0.35, 0, 0);
    pose.add('elbowR', -0.25 - walk * 0.35, 0, 0);
    pose.add('chest', walk * 0.16, -s * 0.10 * walk, 0);
    pose.offset('pelvis', 0, Math.abs(c) * 0.05 * walk - 0.02 * walk, 0);
    pose.add('pelvis', 0, s * 0.06 * walk, 0);
  } else {
    pose.set('shoulderL', 0.06, 0, 0.15);
    pose.set('shoulderR', 0.06, 0, -0.15);
    pose.set('elbowL', -0.28, 0, 0);
    pose.set('elbowR', -0.28, 0, 0);
    pose.set('hipL', 0, 0, 0.04);
    pose.set('hipR', 0, 0, -0.04);
    pose.set('kneeL', 0.08, 0, 0);
    pose.set('kneeR', 0.08, 0, 0);
  }

  let extra = null;

  switch (st.state) {
    case 'roll': {
      const p = clamp01(st.animT / st.motionDur);
      pose.offset('pelvis', 0, -0.55 * Math.sin(p * Math.PI), 0);
      pose.set('chest', 1.4 * Math.sin(p * Math.PI), 0, 0);
      pose.set('hipL', 2.0 * Math.sin(p * Math.PI), 0, 0.2);
      pose.set('hipR', 2.0 * Math.sin(p * Math.PI), 0, -0.2);
      pose.set('kneeL', 1.6 * Math.sin(p * Math.PI), 0, 0);
      pose.set('kneeR', 1.6 * Math.sin(p * Math.PI), 0, 0);
      pose.set('shoulderL', -1.2 * Math.sin(p * Math.PI), 0, 0.5);
      pose.set('shoulderR', -1.2 * Math.sin(p * Math.PI), 0, -0.5);
      extra = { tumble: p * TAU };
      break;
    }
    case 'backstep': {
      const p = clamp01(st.animT / st.motionDur);
      pose.add('chest', -0.4 * Math.sin(p * Math.PI), 0, 0);
      pose.add('hipL', -0.5 * Math.sin(p * Math.PI), 0, 0);
      pose.add('hipR', -0.5 * Math.sin(p * Math.PI), 0, 0);
      break;
    }
    case 'attack': {
      const p = clamp01(st.animT / st.motionDur);
      const fn = A[st.motion] || A.slash_r;
      const k = fn(p);
      if (k.shR) pose.add('shoulderR', k.shR[0], k.shR[1], k.shR[2]);
      if (k.shL) pose.add('shoulderL', k.shL[0], k.shL[1], k.shL[2]);
      if (k.elR) pose.add('elbowR', k.elR[0], k.elR[1], k.elR[2]);
      if (k.chest) pose.add('chest', k.chest[0], k.chest[1], k.chest[2]);
      if (k.pelvis) pose.add('pelvis', k.pelvis[0], k.pelvis[1], k.pelvis[2]);
      extra = { lunge: k.lunge || 0, spinTurn: k.spinTurn || 0 };
      break;
    }
    case 'block': {
      pose.set('shoulderL', -1.15, 0.55, 0.30);
      pose.set('elbowL', -1.35, 0, 0);
      pose.add('shoulderR', -0.25, 0, -0.1);
      pose.add('chest', 0.12, 0.28, 0);
      break;
    }
    case 'parry': {
      const p = clamp01(st.animT / st.motionDur);
      pose.set('shoulderL', -1.5 + Math.sin(p * Math.PI) * 1.4, 1.2 - p * 1.8, 0.5);
      pose.set('elbowL', -1.0, 0, 0);
      break;
    }
    case 'hit': {
      const p = clamp01(st.animT / st.motionDur);
      const k = Math.sin(p * Math.PI);
      pose.add('chest', -0.55 * k, 0.25 * k, 0);
      pose.add('head', -0.35 * k, 0, 0);
      pose.add('shoulderL', -0.5 * k, 0, 0.3 * k);
      pose.add('shoulderR', -0.5 * k, 0, -0.3 * k);
      break;
    }
    case 'stagger': {
      const p = clamp01(st.animT / st.motionDur);
      const k = Math.sin(p * Math.PI);
      pose.add('chest', -0.9 * k, 0, 0.3 * Math.sin(p * 12));
      pose.add('head', -0.5 * k, 0, 0);
      pose.add('shoulderL', -1.1 * k, 0, 0.6 * k);
      pose.add('shoulderR', -1.1 * k, 0, -0.6 * k);
      pose.add('hipL', 0.4 * k, 0, 0);
      pose.add('hipR', -0.3 * k, 0, 0);
      break;
    }
    case 'death': {
      const p = clamp01(st.animT / 1.1);
      const e = p * p * (3 - 2 * p);
      pose.offset('pelvis', 0, -0.75 * e, -0.25 * e);
      pose.add('pelvis', 1.45 * e, 0, 0);
      pose.add('chest', 0.4 * e, 0.3 * e, 0);
      pose.add('head', 0.5 * e, 0, 0);
      pose.add('shoulderL', -0.8 * e, 0, 1.0 * e);
      pose.add('shoulderR', -0.8 * e, 0, -1.0 * e);
      pose.add('hipL', -1.2 * e, 0, 0.2 * e);
      pose.add('hipR', -1.1 * e, 0, -0.2 * e);
      pose.add('kneeL', 0.9 * e, 0, 0);
      pose.add('kneeR', 0.8 * e, 0, 0);
      break;
    }
    case 'cast': {
      const p = clamp01(st.animT / st.motionDur);
      const k = A.cast(p);
      pose.add('shoulderR', k.shR[0], k.shR[1], k.shR[2]);
      pose.add('shoulderL', k.shL[0], k.shL[1], k.shL[2]);
      pose.add('elbowR', k.elR[0], k.elR[1], k.elR[2]);
      pose.add('chest', k.chest[0], k.chest[1], k.chest[2]);
      break;
    }
    case 'drink': {
      const p = clamp01(st.animT / st.motionDur);
      const k = Math.sin(p * Math.PI);
      pose.add('shoulderL', -2.1 * k, 0.4 * k, 0.5 * k);
      pose.add('elbowL', -1.9 * k, 0, 0);
      pose.add('head', -0.35 * k, 0, 0);
      break;
    }
    case 'rest': {
      pose.offset('pelvis', 0, -0.42, 0);
      pose.set('hipL', -1.5, 0, 0.25);
      pose.set('hipR', -1.5, 0, -0.25);
      pose.set('kneeL', 1.6, 0, 0);
      pose.set('kneeR', 1.6, 0, 0);
      pose.set('chest', 0.22 + breath, 0, 0);
      pose.set('shoulderL', 0.4, 0, 0.4);
      pose.set('shoulderR', 0.4, 0, -0.4);
      pose.set('head', 0.25, 0, 0);
      break;
    }
    case 'ride': {
      const bob = Math.sin(t * 6.5) * 0.05 * (st.gallop || 0);
      pose.offset('pelvis', 0, -0.05 + bob * 0.8, 0);
      pose.set('pelvis', 0.10 + bob, 0, 0);
      pose.set('chest', 0.14 - bob * 0.6, 0, 0);
      pose.set('head', -0.10, 0, 0);
      pose.set('hipL', -0.62, 0.10, 0.66);
      pose.set('hipR', -0.62, -0.10, -0.66);
      pose.set('kneeL', 1.20, 0, 0);
      pose.set('kneeR', 1.20, 0, 0);
      pose.set('footL', -0.42, 0, 0);
      pose.set('footR', -0.42, 0, 0);
      pose.set('shoulderL', -1.00 + bob * 0.5, 0.22, 0.24);
      pose.set('shoulderR', -1.00 + bob * 0.5, -0.22, -0.24);
      pose.set('elbowL', -0.72, 0, 0);
      pose.set('elbowR', -0.72, 0, 0);
      break;
    }
    case 'fall': {
      pose.set('shoulderL', -2.0, 0, 0.6);
      pose.set('shoulderR', -2.0, 0, -0.6);
      pose.set('hipL', 0.5, 0, 0.15);
      pose.set('hipR', -0.35, 0, -0.15);
      pose.set('kneeL', 0.9, 0, 0);
      break;
    }
  }

  if (st.blocking && st.state !== 'attack' && st.state !== 'roll') {
    pose.set('shoulderL', -1.15, 0.55, 0.30);
    pose.set('elbowL', -1.35, 0, 0);
  }
  if (rig.float) {
    pose.offset('pelvis', 0, Math.sin(t * 1.3) * 0.12, 0);
    pose.set('hipL', 0, 0, 0); pose.set('hipR', 0, 0, 0);
  }
  return extra;
}

function poseQuadruped(pose, st) {
  const t = st.t;
  const walk = st.speed01;
  const phase = st.gait;
  const s = Math.sin(phase), c = Math.cos(phase);
  const amp = lerp(0.35, 0.95, walk);

  // 基本姿勢：やや前傾で頭を低く構える
  pose.set('body', 0.06 + Math.sin(t * 1.4) * 0.015 + walk * 0.05, 0, 0);
  pose.set('neck', -0.52 + Math.sin(t * 1.1) * 0.04 + walk * 0.12, 0, 0);
  pose.set('head', 0.44 - walk * 0.10, Math.sin(t * 0.6) * 0.12 * (1 - walk), 0);
  pose.set('jaw', 0.06 + Math.sin(t * 0.9) * 0.03, 0, 0);
  pose.set('tail', 0.35 + Math.sin(t * 2.2 + walk * 4) * 0.2, Math.sin(t * 1.7) * 0.3, 0);
  pose.set('earL', -0.12, 0, -0.18);
  pose.set('earR', -0.12, 0, 0.18);

  // 前脚はやや後傾、後脚は飛節を曲げる
  pose.set('flHip', 0.14 + s * amp, 0, 0);
  pose.set('frHip', 0.14 - s * amp, 0, 0);
  pose.set('blHip', -0.22 - c * amp * 0.9, 0, 0);
  pose.set('brHip', -0.22 + c * amp * 0.9, 0, 0);
  pose.set('flKnee', 0.26 + Math.max(0, -s) * amp * 0.9, 0, 0);
  pose.set('frKnee', 0.26 + Math.max(0, s) * amp * 0.9, 0, 0);
  pose.set('blKnee', 0.62 + Math.max(0, c) * amp * 0.9, 0, 0);
  pose.set('brKnee', 0.62 + Math.max(0, -c) * amp * 0.9, 0, 0);
  pose.set('flPaw', -0.34 - Math.max(0, -s) * amp * 0.5, 0, 0);
  pose.set('frPaw', -0.34 - Math.max(0, s) * amp * 0.5, 0, 0);
  pose.set('blPaw', -0.36 - Math.max(0, c) * amp * 0.5, 0, 0);
  pose.set('brPaw', -0.36 - Math.max(0, -c) * amp * 0.5, 0, 0);
  pose.offset('body', 0, Math.abs(Math.sin(phase)) * 0.06 * walk, 0);

  if (pose.rig.index.has('wingL')) {
    const f = Math.sin(t * 2.2) * 0.3;
    pose.set('wingL', 0, 0, 0.25 + f);
    pose.set('wingR', 0, 0, -0.25 - f);
    pose.set('wingL2', 0, 0.3, 0.4 + f * 0.6);
    pose.set('wingR2', 0, -0.3, -0.4 - f * 0.6);
  }

  let extra = null;
  switch (st.state) {
    case 'attack': {
      const p = clamp01(st.animT / st.motionDur);
      const fn = A[st.motion] || A.lunge;
      const k = fn(p);
      if (k.body) pose.add('body', k.body[0], k.body[1], k.body[2]);
      if (k.neck) pose.add('neck', k.neck[0], k.neck[1], k.neck[2]);
      if (k.jaw) pose.add('jaw', k.jaw[0], k.jaw[1], k.jaw[2]);
      extra = { lunge: k.lunge || 0, airborne: k.airborne || 0 };
      break;
    }
    case 'hit': {
      const p = clamp01(st.animT / st.motionDur);
      const k = Math.sin(p * Math.PI);
      pose.add('body', -0.3 * k, 0.2 * k, 0);
      pose.add('neck', 0.4 * k, 0, 0);
      break;
    }
    case 'stagger': {
      const p = clamp01(st.animT / st.motionDur);
      const k = Math.sin(p * Math.PI);
      pose.add('body', -0.5 * k, 0.35 * Math.sin(p * 10), 0);
      pose.add('neck', 0.6 * k, 0, 0);
      break;
    }
    case 'death': {
      const p = clamp01(st.animT / 1.2);
      const e = p * p * (3 - 2 * p);
      pose.offset('body', 0, -0.42 * e, 0);
      pose.add('body', 0, 0, 1.5 * e);
      pose.add('neck', 0.5 * e, 0, 0);
      pose.add('flHip', -0.9 * e, 0, 0);
      pose.add('frHip', -0.7 * e, 0, 0);
      pose.add('blHip', 0.8 * e, 0, 0);
      pose.add('brHip', 0.6 * e, 0, 0);
      pose.add('flKnee', 0.5 * e, 0, 0);
      pose.add('blKnee', -0.4 * e, 0, 0);
      break;
    }
  }
  void c;
  return extra;
}
