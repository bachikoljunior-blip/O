// 手続き的ワールド生成：大陸・地方（リージョン）・河川・湖・街道・平坦化

import { Noise, makeRng, hash2 } from '../core/noise.js';
import { clamp, clamp01, lerp, smoothstep } from '../core/math.js';

export const WORLD_RADIUS = 2100;   // 大陸の外縁（m）
export const SEA_LEVEL = 0;

/**
 * 地方定義。中心からの距離で滑らかにブレンドされ、地形パラメータと
 * 植生・色・危険度が決まる。ここを編集すれば世界の性格が変わる。
 */
export const REGIONS = [
  {
    id: 'downs', name: '白樺の草原', en: 'Verdant Downs',
    cx: 0, cz: 320, r: 620, danger: 1,
    base: 7, landAmp: 26, hillAmp: 7.5, ridgeAmp: 14, ridgeSharp: 2.6, rough: 1.0,
    temp: 0.62, moist: 0.55,
    grass: [0.30, 0.48, 0.22], grass2: [0.42, 0.55, 0.24], rock: [0.42, 0.41, 0.38],
    tree: 'birch', treeDensity: 0.35, bushDensity: 0.5, grassDensity: 1.0,
    terrace: 0.00, terraceStep: 6,  mesa: 0.00, canyon: 0.00, snowLine: 360,
    fog: [0.62, 0.72, 0.82], fogDensity: 0.9,
  },
  {
    id: 'gloomwood', name: '常闇の森', en: 'Gloomwood',
    cx: -760, cz: -520, r: 660, danger: 3,
    base: 10, landAmp: 30, hillAmp: 11, ridgeAmp: 26, ridgeSharp: 2.2, rough: 1.25,
    temp: 0.48, moist: 0.85,
    grass: [0.14, 0.26, 0.15], grass2: [0.19, 0.33, 0.16], rock: [0.30, 0.31, 0.30],
    tree: 'pine', treeDensity: 1.0, bushDensity: 0.7, grassDensity: 0.6,
    terrace: 0.22, terraceStep: 7,  mesa: 0.00, canyon: 0.00, snowLine: 260,
    fog: [0.36, 0.44, 0.42], fogDensity: 1.9,
  },
  {
    id: 'cinder', name: '灰燼の荒野', en: 'Cinderwaste',
    cx: 980, cz: -980, r: 700, danger: 7,
    base: 14, landAmp: 44, hillAmp: 14, ridgeAmp: 46, ridgeSharp: 1.9, rough: 1.6,
    temp: 0.95, moist: 0.08,
    grass: [0.36, 0.20, 0.14], grass2: [0.46, 0.26, 0.15], rock: [0.28, 0.20, 0.19],
    tree: 'dead', treeDensity: 0.16, bushDensity: 0.15, grassDensity: 0.25,
    terrace: 0.62, terraceStep: 9,  mesa: 0.85, canyon: 0.25, snowLine: 900,
    fog: [0.62, 0.32, 0.22], fogDensity: 2.4,
  },
  {
    id: 'mistfen', name: '霧の湿原', en: 'Mistfen',
    cx: -980, cz: 860, r: 600, danger: 4,
    base: 1.5, landAmp: 9, hillAmp: 3.2, ridgeAmp: 5, ridgeSharp: 3.0, rough: 0.8,
    temp: 0.55, moist: 1.0,
    grass: [0.24, 0.31, 0.20], grass2: [0.32, 0.36, 0.19], rock: [0.30, 0.32, 0.30],
    tree: 'willow', treeDensity: 0.45, bushDensity: 0.9, grassDensity: 1.3,
    terrace: 0.00, terraceStep: 4,  mesa: 0.00, canyon: 0.00, snowLine: 340,
    fog: [0.58, 0.64, 0.60], fogDensity: 3.2,
  },
  {
    id: 'skyspire', name: '蒼天嶺', en: 'Skyspire Range',
    cx: 420, cz: -1560, r: 760, danger: 6,
    base: 40, landAmp: 70, hillAmp: 16, ridgeAmp: 150, ridgeSharp: 1.55, rough: 1.5,
    temp: 0.12, moist: 0.45,
    grass: [0.34, 0.37, 0.32], grass2: [0.44, 0.46, 0.42], rock: [0.48, 0.49, 0.52],
    tree: 'pine', treeDensity: 0.4, bushDensity: 0.2, grassDensity: 0.35,
    terrace: 0.70, terraceStep: 15, mesa: 0.20, canyon: 0.15, snowLine: 82,
    fog: [0.74, 0.80, 0.90], fogDensity: 1.2,
  },
  {
    id: 'goldreach', name: '黄金平原', en: 'Goldreach',
    cx: 1180, cz: 520, r: 640, danger: 2,
    base: 6, landAmp: 18, hillAmp: 5, ridgeAmp: 8, ridgeSharp: 2.8, rough: 0.75,
    temp: 0.75, moist: 0.4,
    grass: [0.52, 0.47, 0.20], grass2: [0.62, 0.56, 0.24], rock: [0.46, 0.43, 0.37],
    tree: 'oak', treeDensity: 0.18, bushDensity: 0.3, grassDensity: 1.5,
    terrace: 0.10, terraceStep: 6,  mesa: 0.12, canyon: 0.00, snowLine: 900,
    fog: [0.78, 0.74, 0.60], fogDensity: 0.8,
  },
  {
    id: 'riftvale', name: '裂罅の谷', en: 'Riftvale',
    cx: -320, cz: 1420, r: 600, danger: 5,
    base: 16, landAmp: 38, hillAmp: 18, ridgeAmp: 62, ridgeSharp: 1.7, rough: 1.9,
    temp: 0.7, moist: 0.25,
    grass: [0.45, 0.33, 0.22], grass2: [0.55, 0.42, 0.26], rock: [0.52, 0.38, 0.30],
    tree: 'dead', treeDensity: 0.12, bushDensity: 0.25, grassDensity: 0.4,
    terrace: 0.45, terraceStep: 10, mesa: 0.35, canyon: 1.00, snowLine: 620,
    fog: [0.80, 0.66, 0.52], fogDensity: 1.1,
  },
  {
    id: 'coast', name: '海鳴りの岸', en: 'Sundering Coast',
    cx: 1520, cz: 1360, r: 560, danger: 3,
    base: 3, landAmp: 22, hillAmp: 9, ridgeAmp: 30, ridgeSharp: 2.0, rough: 1.2,
    temp: 0.68, moist: 0.7,
    grass: [0.30, 0.44, 0.28], grass2: [0.38, 0.50, 0.30], rock: [0.50, 0.48, 0.44],
    tree: 'oak', treeDensity: 0.3, bushDensity: 0.45, grassDensity: 0.9,
    terrace: 0.55, terraceStep: 8,  mesa: 0.05, canyon: 0.00, snowLine: 400,
    fog: [0.68, 0.78, 0.86], fogDensity: 1.0,
  },
];

const BLEND_KEYS = ['base', 'landAmp', 'hillAmp', 'ridgeAmp', 'ridgeSharp', 'rough',
  'temp', 'moist', 'treeDensity', 'bushDensity', 'grassDensity', 'fogDensity', 'danger',
  'terrace', 'terraceStep', 'mesa', 'canyon', 'snowLine'];
const BLEND_VEC = ['grass', 'grass2', 'rock', 'fog'];
/** 地方パラメータをキャッシュする格子間隔（m）。地方は大きいので粗くて足りる */
const PARAM_GRID = 28;

export class WorldGen {
  constructor(seed = 20260802) {
    this.seed = seed;
    this.n = new Noise(seed);
    this.nWarp = new Noise(seed + 991);
    this.nRiver = new Noise(seed + 4242);
    this.nDetail = new Noise(seed + 777);
    this.rng = makeRng(seed);

    // 平坦化領域（集落・街道）を格納する一様グリッド
    this.flatCell = 96;
    this.flatGrid = new Map();
    this.roads = [];

    this._p = this._makeP();
    this._hp = {
      base: 0, landAmp: 0, hillAmp: 0, ridgeAmp: 0, ridgeSharp: 0, rough: 0,
      terrace: 0, terraceStep: 6, mesa: 0, canyon: 0,
    };
    /**
     * 名のある峰。地方の平均を大きく超える高さを持たせ、
     * どこからでも見える「目印」にする。遠景の骨格はこれで決まる。
     */
    this.landmarks = [
      { name: '天衝の尖塔', x: 300, z: -1700, r: 300, h: 300, sharp: 2.4 },
      { name: '双子の岩', x: 700, z: -1380, r: 200, h: 205, sharp: 2.0 },
      { name: '灰かぶりの角', x: 1150, z: -1080, r: 230, h: 165, sharp: 1.7 },
      { name: '裂罅の背', x: -430, z: 1560, r: 250, h: 150, sharp: 1.8 },
      { name: '常闇の丘', x: -900, z: -640, r: 260, h: 130, sharp: 1.5 },
    ];
    for (const l of this.landmarks) { l.r2 = l.r * l.r; l.invR = 1 / l.r; }
    this._bl = {
      n00: null, n10: null, n01: null, n11: null,
      w00: 0, w10: 0, w01: 0, w11: 0, tx: 0, tz: 0,
    };
    this.pcache = new Map();
  }

  _makeP() {
    const o = { region: REGIONS[0] };
    for (const k of BLEND_KEYS) o[k] = 0;
    for (const k of BLEND_VEC) o[k] = [0, 0, 0];
    return o;
  }

  /* -------------------------------------------------- リージョンブレンド */
  /** 生のブレンド計算（8地方を走査するため重い） */
  paramsRaw(x, z, out) {
    // 境界を歪ませて自然な形にする
    const wx = x + this.nWarp.n2(x * 0.0006, z * 0.0006) * 220;
    const wz = z + this.nWarp.n2(x * 0.0006 + 31.7, z * 0.0006 - 12.3) * 220;
    let total = 0;
    for (const k of BLEND_KEYS) out[k] = 0;
    for (const k of BLEND_VEC) { out[k][0] = 0; out[k][1] = 0; out[k][2] = 0; }
    let best = -1, bestRegion = REGIONS[0];
    for (const R of REGIONS) {
      const dx = (wx - R.cx) / R.r, dz = (wz - R.cz) / R.r;
      const d2 = dx * dx + dz * dz;
      const w = Math.exp(-d2 * 1.35) + 1e-4;
      if (w > best) { best = w; bestRegion = R; }
      total += w;
      for (const k of BLEND_KEYS) out[k] += R[k] * w;
      for (const k of BLEND_VEC) {
        out[k][0] += R[k][0] * w; out[k][1] += R[k][1] * w; out[k][2] += R[k][2] * w;
      }
    }
    const inv = 1 / total;
    for (const k of BLEND_KEYS) out[k] *= inv;
    for (const k of BLEND_VEC) { out[k][0] *= inv; out[k][1] *= inv; out[k][2] *= inv; }
    out.region = bestRegion;
    return out;
  }

  /** 粗いグリッド上でキャッシュした地方パラメータを双一次補間する */
  _node(ix, iz) {
    const k = ix * 100003 + iz;
    let n = this.pcache.get(k);
    if (!n) {
      n = this.paramsRaw(ix * PARAM_GRID, iz * PARAM_GRID, this._makeP());
      this.pcache.set(k, n);
    }
    return n;
  }

  /** 双一次補間の重みと 4 ノードを求める（内部用） */
  _bilinear(x, z) {
    const fx = x / PARAM_GRID, fz = z / PARAM_GRID;
    const ix = Math.floor(fx), iz = Math.floor(fz);
    const tx = fx - ix, tz = fz - iz;
    const b = this._bl;
    b.n00 = this._node(ix, iz); b.n10 = this._node(ix + 1, iz);
    b.n01 = this._node(ix, iz + 1); b.n11 = this._node(ix + 1, iz + 1);
    b.w00 = (1 - tx) * (1 - tz); b.w10 = tx * (1 - tz);
    b.w01 = (1 - tx) * tz; b.w11 = tx * tz;
    b.tx = tx; b.tz = tz;
    return b;
  }

  /** 高さ計算に必要な 6 値だけを補間する高速版 */
  heightParams(x, z) {
    const b = this._bilinear(x, z);
    const { n00, n10, n01, n11, w00, w10, w01, w11 } = b;
    const o = this._hp;
    o.base = n00.base * w00 + n10.base * w10 + n01.base * w01 + n11.base * w11;
    o.landAmp = n00.landAmp * w00 + n10.landAmp * w10 + n01.landAmp * w01 + n11.landAmp * w11;
    o.hillAmp = n00.hillAmp * w00 + n10.hillAmp * w10 + n01.hillAmp * w01 + n11.hillAmp * w11;
    o.ridgeAmp = n00.ridgeAmp * w00 + n10.ridgeAmp * w10 + n01.ridgeAmp * w01 + n11.ridgeAmp * w11;
    o.ridgeSharp = n00.ridgeSharp * w00 + n10.ridgeSharp * w10 + n01.ridgeSharp * w01 + n11.ridgeSharp * w11;
    o.rough = n00.rough * w00 + n10.rough * w10 + n01.rough * w01 + n11.rough * w11;
    o.terrace = n00.terrace * w00 + n10.terrace * w10 + n01.terrace * w01 + n11.terrace * w11;
    o.terraceStep = n00.terraceStep * w00 + n10.terraceStep * w10
      + n01.terraceStep * w01 + n11.terraceStep * w11;
    o.mesa = n00.mesa * w00 + n10.mesa * w10 + n01.mesa * w01 + n11.mesa * w11;
    o.canyon = n00.canyon * w00 + n10.canyon * w10 + n01.canyon * w01 + n11.canyon * w11;
    return o;
  }

  params(x, z, out = this._p) {
    const b = this._bilinear(x, z);
    const { n00, n10, n01, n11, w00, w10, w01, w11, tx, tz } = b;
    out.base = n00.base * w00 + n10.base * w10 + n01.base * w01 + n11.base * w11;
    out.landAmp = n00.landAmp * w00 + n10.landAmp * w10 + n01.landAmp * w01 + n11.landAmp * w11;
    out.hillAmp = n00.hillAmp * w00 + n10.hillAmp * w10 + n01.hillAmp * w01 + n11.hillAmp * w11;
    out.ridgeAmp = n00.ridgeAmp * w00 + n10.ridgeAmp * w10 + n01.ridgeAmp * w01 + n11.ridgeAmp * w11;
    out.ridgeSharp = n00.ridgeSharp * w00 + n10.ridgeSharp * w10 + n01.ridgeSharp * w01 + n11.ridgeSharp * w11;
    out.rough = n00.rough * w00 + n10.rough * w10 + n01.rough * w01 + n11.rough * w11;
    out.temp = n00.temp * w00 + n10.temp * w10 + n01.temp * w01 + n11.temp * w11;
    out.moist = n00.moist * w00 + n10.moist * w10 + n01.moist * w01 + n11.moist * w11;
    out.treeDensity = n00.treeDensity * w00 + n10.treeDensity * w10 + n01.treeDensity * w01 + n11.treeDensity * w11;
    out.bushDensity = n00.bushDensity * w00 + n10.bushDensity * w10 + n01.bushDensity * w01 + n11.bushDensity * w11;
    out.grassDensity = n00.grassDensity * w00 + n10.grassDensity * w10 + n01.grassDensity * w01 + n11.grassDensity * w11;
    out.fogDensity = n00.fogDensity * w00 + n10.fogDensity * w10 + n01.fogDensity * w01 + n11.fogDensity * w11;
    out.danger = n00.danger * w00 + n10.danger * w10 + n01.danger * w01 + n11.danger * w11;
    out.snowLine = n00.snowLine * w00 + n10.snowLine * w10
      + n01.snowLine * w01 + n11.snowLine * w11;
    for (let vi = 0; vi < 4; vi++) {
      const k = BLEND_VEC[vi];
      const o = out[k], a = n00[k], b1 = n10[k], c = n01[k], d = n11[k];
      o[0] = a[0] * w00 + b1[0] * w10 + c[0] * w01 + d[0] * w11;
      o[1] = a[1] * w00 + b1[1] * w10 + c[1] * w01 + d[1] * w11;
      o[2] = a[2] * w00 + b1[2] * w10 + c[2] * w01 + d[2] * w11;
    }
    out.region = (tx < 0.5 ? (tz < 0.5 ? n00 : n01) : (tz < 0.5 ? n10 : n11)).region;
    return out;
  }

  regionAt(x, z) { return this.params(x, z).region; }
  dangerAt(x, z) { return this.params(x, z).danger; }

  /* ---------------------------------------------------------- 高さ関数 */
  coastMask(x, z) {
    const d = Math.hypot(x, z);
    const wob = this.n.fbm(x * 0.0012, z * 0.0012, 3) * 260;
    return 1 - smoothstep(WORLD_RADIUS - 420 + wob, WORLD_RADIUS + 120 + wob, d);
  }

  /**
   * 卓状台地（メサ）。平たい頂と切り立った側面を持つ孤立した高台。
   * 中心をセルの内側に閉じ込め、半径をセルの 22% 以下に抑えることで、
   * 自分のセルだけ見れば済むようにしてある（height は毎フレーム大量に呼ばれる）。
   */
  mesaAt(x, z, amount) {
    const C = 380;
    const cx = Math.floor(x / C), cz = Math.floor(z / C);
    const pick = hash2(cx, cz, this.seed + 313);
    if (pick > amount * 0.42) return 0;                 // このセルにはメサが無い
    const ox = 0.28 + hash2(cx, cz, this.seed + 5) * 0.44;
    const oz = 0.28 + hash2(cx, cz, this.seed + 9) * 0.44;
    const mx = (cx + ox) * C, mz = (cz + oz) * C;
    const r = C * (0.10 + hash2(cx, cz, this.seed + 17) * 0.11);
    const d = Math.hypot(x - mx, z - mz);
    if (d > r) return 0;
    // 縁は急に、頂は平らに
    const t = 1 - smoothstep(r * 0.72, r, d);
    const height = 22 + hash2(cx, cz, this.seed + 23) * 40;
    return t * t * (3 - 2 * t) * height;
  }

  /**
   * 溝状の峡谷。尾根ノイズの「谷側」を細く深く抜き取る。
   * 裂罅の谷でだけ本気で効かせる。
   */
  canyonAt(x, z, amount) {
    const v = 1 - Math.abs(this.nRiver.n2(x * 0.00062 - 91.3, z * 0.00062 + 55.7));
    const band = smoothstep(0.972, 0.9995, v);
    if (band <= 0) return 0;
    return band * band * (26 + 34 * amount) * amount;
  }

  /**
   * 名のある峰の盛り上がり。
   * height は毎フレーム何万回も呼ばれるので、まず軸並行の矩形で弾く。
   * ほとんどの地点は比較 2 回で抜ける。
   */
  landmarkAt(x, z) {
    let add = 0;
    const L = this.landmarks;
    for (let i = 0; i < L.length; i++) {
      const l = L[i];
      const dx = x - l.x;
      if (dx > l.r || dx < -l.r) continue;
      const dz = z - l.z;
      if (dz > l.r || dz < -l.r) continue;
      const d2 = dx * dx + dz * dz;
      if (d2 >= l.r2) continue;
      const t = 1 - Math.sqrt(d2) * l.invR;
      add += Math.pow(t, l.sharp) * l.h;
    }
    return add;
  }

  /** 平坦化・街道を含まない素の地形 */
  heightBase(x, z) {
    const p = this.heightParams(x, z);
    const land = this.n.fbm(x * 0.00085, z * 0.00085, 5, 2.05, 0.5);
    const hills = this.n.fbm(x * 0.0055, z * 0.0055, 4, 2.1, 0.5);
    const detail = this.nDetail.fbm(x * 0.031, z * 0.031, 3, 2.2, 0.45);

    let ridge = this.n.ridged(x * 0.0016, z * 0.0016, 5, 2.03, 0.52);
    ridge = Math.pow(clamp01(ridge), p.ridgeSharp);

    let h = p.base + land * p.landAmp + hills * p.hillAmp * p.rough
      + ridge * p.ridgeAmp + detail * 1.4 * p.rough;

    // 名のある峰（遠景の骨格）
    h += this.landmarkAt(x, z);

    // 卓状台地
    if (p.mesa > 0.06) h += this.mesaAt(x, z, p.mesa);

    // 段丘：高さを段に量子化して、平らな棚と切り立った段差をつくる。
    // 一様にかけると人工物になるので、丘ノイズで濃淡をつける。
    if (p.terrace > 0.01) {
      const step = p.terraceStep;
      const mask = clamp01(hills * 1.6 + 0.55);
      const q = Math.floor(h / step) * step;
      const frac = (h - q) / step;
      const shaped = q + step * smoothstep(0.34, 0.66, frac);
      h = lerp(h, shaped, p.terrace * mask);
    }

    // 峡谷（段丘の後に抜く。段差の途中を割って落とす方が険しく見える）
    if (p.canyon > 0.10) h -= this.canyonAt(x, z, p.canyon);

    // 海岸へ向けて落ち込む
    const cm = this.coastMask(x, z);
    h = h * cm - (1 - cm) * 26;

    // 湖
    const lake = this.n.fbm(x * 0.0021 + 77, z * 0.0021 - 41, 3);
    if (lake > 0.42 && h < 42) {
      const t = smoothstep(0.42, 0.62, lake) * clamp01((42 - h) / 24);
      h = lerp(h, -3.5 - t * 4, t);
    }

    // 河川（低地のみ谷を刻む。高地では乾いた峡谷になる）
    const rv = 1 - Math.abs(this.nRiver.n2(x * 0.00105 + 13.3, z * 0.00105 - 7.1));
    const band = smoothstep(0.955, 0.999, rv);
    if (band > 0) {
      const alt = clamp01(1 - (h - 8) / 90);
      const depth = band * (7 + 9 * alt) * clamp01(h / 6 + 0.3);
      h -= depth;
      // 低地の川床は掘りすぎないよう底上げする。
      // 高度の閾値で on/off すると河床に垂直の壁ができるので滑らかに混ぜ、
      // さらに川の影響帯（band）で重みを掛ける。
      // band が立ち上がった瞬間に全力で底上げすると、川の縁が 12m の崖になる。
      const bedK = smoothstep(0.55, 0.75, alt) * band;
      if (bedK > 0 && h < -2.2) h = lerp(h, -2.2 + (h + 2.2) * 0.25, bedK);
    }
    return h;
  }

  /** 平坦化・街道を反映した最終高さ */
  height(x, z) {
    const h = this.heightBase(x, z);
    const f = this.flatAt(x, z);
    return f ? lerp(h, f.h, f.w) : h;
  }

  /** 有限差分による法線 */
  normal(x, z, out) {
    const e = 1.0;
    const hL = this.height(x - e, z), hR = this.height(x + e, z);
    const hD = this.height(x, z - e), hU = this.height(x, z + e);
    const nx = hL - hR, ny = 2 * e, nz = hD - hU;
    const l = Math.hypot(nx, ny, nz) || 1;
    out[0] = nx / l; out[1] = ny / l; out[2] = nz / l;
    return out;
  }

  slope(x, z) {
    const e = 1.2;
    const hL = this.height(x - e, z), hR = this.height(x + e, z);
    const hD = this.height(x, z - e), hU = this.height(x, z + e);
    return Math.hypot(hR - hL, hU - hD) / (2 * e);
  }

  /* --------------------------------------------------------- 平坦化領域 */
  _key(cx, cz) { return cx * 100003 + cz; }

  _addToGrid(item, minX, minZ, maxX, maxZ) {
    const c = this.flatCell;
    for (let cz = Math.floor(minZ / c); cz <= Math.floor(maxZ / c); cz++) {
      for (let cx = Math.floor(minX / c); cx <= Math.floor(maxX / c); cx++) {
        const k = this._key(cx, cz);
        let list = this.flatGrid.get(k);
        if (!list) { list = []; this.flatGrid.set(k, list); }
        list.push(item);
      }
    }
  }

  /** 円形の平坦地（集落・祭壇・ボス闘技場） */
  addPlateau(x, z, radius, feather = 18, height = null) {
    const h = height ?? this.heightBase(x, z);
    const item = { kind: 'disc', x, z, r: radius, f: feather, h };
    this._addToGrid(item, x - radius - feather, z - radius - feather, x + radius + feather, z + radius + feather);
    return item;
  }

  /** 2点を結ぶ街道（幅 w、両端の高さを補間して均す） */
  addRoad(x0, z0, x1, z1, w = 5, feather = 9) {
    const h0 = this.height(x0, z0), h1 = this.height(x1, z1);
    const item = { kind: 'seg', x0, z0, x1, z1, w, f: feather, h0, h1 };
    const pad = w + feather + 2;
    this._addToGrid(item, Math.min(x0, x1) - pad, Math.min(z0, z1) - pad,
      Math.max(x0, x1) + pad, Math.max(z0, z1) + pad);
    this.roads.push(item);
    return item;
  }

  /** (x,z) における平坦化の目標高さと重み */
  flatAt(x, z) {
    const c = this.flatCell;
    const list = this.flatGrid.get(this._key(Math.floor(x / c), Math.floor(z / c)));
    if (!list) return null;
    let bw = 0, bh = 0, road = 0;
    for (let i = 0; i < list.length; i++) {
      const it = list[i];
      let w = 0, h = 0;
      if (it.kind === 'disc') {
        const d = Math.hypot(x - it.x, z - it.z);
        w = 1 - smoothstep(it.r, it.r + it.f, d);
        h = it.h;
      } else {
        const dx = it.x1 - it.x0, dz = it.z1 - it.z0;
        const len2 = dx * dx + dz * dz || 1;
        const t = clamp01(((x - it.x0) * dx + (z - it.z0) * dz) / len2);
        const px = it.x0 + dx * t, pz = it.z0 + dz * t;
        const d = Math.hypot(x - px, z - pz);
        w = 1 - smoothstep(it.w, it.w + it.f, d);
        h = lerp(it.h0, it.h1, t);
        if (w > road) road = w;
      }
      if (w > bw) { bw = w; bh = h; }
      else if (w > 0) { bh = lerp(bh, h, w * 0.4); }
    }
    if (bw <= 0) return null;
    return { w: bw, h: bh, road };
  }

  /** 道路の踏み固め度（0..1）。地表色と足音に使う */
  roadAt(x, z) {
    const f = this.flatAt(x, z);
    return f ? f.road : 0;
  }

  /* --------------------------------------------------------------- 色 */
  surfaceColor(x, z, h, slope, out) {
    const p = this.params(x, z);
    const v = this.nDetail.fbm(x * 0.09, z * 0.09, 2) * 0.5 + 0.5;
    const patch = this.n.fbm(x * 0.012 + 400, z * 0.012 - 220, 3) * 0.5 + 0.5;

    let r = lerp(p.grass[0], p.grass2[0], patch);
    let g = lerp(p.grass[1], p.grass2[1], patch);
    let b = lerp(p.grass[2], p.grass2[2], patch);

    const rocky = smoothstep(0.55, 1.15, slope);
    r = lerp(r, p.rock[0], rocky); g = lerp(g, p.rock[1], rocky); b = lerp(b, p.rock[2], rocky);

    const snowLine = p.snowLine;
    const snow = smoothstep(snowLine, snowLine + 34, h) * (1 - rocky * 0.55);
    r = lerp(r, 0.92, snow); g = lerp(g, 0.95, snow); b = lerp(b, 1.0, snow);

    const sand = (1 - smoothstep(1.0, 4.2, h)) * (1 - smoothstep(0.5, 0.9, slope));
    r = lerp(r, 0.72, sand); g = lerp(g, 0.66, sand); b = lerp(b, 0.50, sand);

    if (h < 0) {
      const t = clamp01(-h / 8);
      r = lerp(r, 0.20, t); g = lerp(g, 0.22, t); b = lerp(b, 0.20, t);
    }

    const road = this.roadAt(x, z);
    if (road > 0.01) {
      const t = smoothstep(0.35, 0.95, road) * 0.88;
      const dirt = this.nDetail.fbm(x * 0.35, z * 0.35, 2) * 0.06;
      r = lerp(r, 0.30 + dirt, t); g = lerp(g, 0.245 + dirt, t); b = lerp(b, 0.185 + dirt, t);
    }

    const shade = 0.86 + v * 0.28;
    out[0] = clamp(r * shade, 0, 1);
    out[1] = clamp(g * shade, 0, 1);
    out[2] = clamp(b * shade, 0, 1);
    return out;
  }

  /** 地面の総合サンプル（ゲームロジック用） */
  sample(x, z) {
    const h = this.height(x, z);
    const s = this.slope(x, z);
    const p = this.params(x, z);
    const road = this.roadAt(x, z);
    let surface = 'grass';
    if (road > 0.4) surface = 'dirt';
    else if (h < 1.6) surface = 'sand';
    else if (s > 0.85) surface = 'rock';
    else if (h > p.snowLine) surface = 'snow';
    else if (p.moist > 0.9) surface = 'marsh';
    return { h, slope: s, region: p.region, danger: p.danger, surface, road, underwater: h < SEA_LEVEL };
  }

  /** 指定範囲内で歩行に適した平地を探す（スポーン配置用） */
  findFlat(x, z, radius, tries = 24, maxSlope = 0.5, minH = 1.5) {
    for (let i = 0; i < tries; i++) {
      const a = hash2(i * 13 + (x | 0), i * 7 + (z | 0), this.seed) * Math.PI * 2;
      const rr = Math.sqrt(hash2(i * 31 + (z | 0), i * 17 + (x | 0), this.seed + 5)) * radius;
      const px = x + Math.cos(a) * rr, pz = z + Math.sin(a) * rr;
      const h = this.height(px, pz);
      if (h > minH && this.slope(px, pz) < maxSlope) return { x: px, z: pz, h };
    }
    const h = this.height(x, z);
    return { x, z, h };
  }
}
