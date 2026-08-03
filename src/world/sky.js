// 時間帯・天候・大気パラメータ

import { clamp, clamp01, lerp, smoothstep, TAU } from '../core/math.js';
import { makeRng } from '../core/noise.js';

/** 実時間 何秒で 1 日か */
export const DAY_LENGTH = 20 * 60;

const WEATHERS = {
  clear: { cloud: 0.12, rain: 0, snow: 0, fogMul: 0.85, wind: 0.5, name: '快晴' },
  fair: { cloud: 0.35, rain: 0, snow: 0, fogMul: 1.0, wind: 0.7, name: '晴れ' },
  cloudy: { cloud: 0.72, rain: 0, snow: 0, fogMul: 1.35, wind: 0.9, name: '曇り' },
  rain: { cloud: 0.92, rain: 0.7, snow: 0, fogMul: 2.0, wind: 1.3, name: '雨' },
  storm: { cloud: 1.0, rain: 1.0, snow: 0, fogMul: 2.6, wind: 2.0, name: '嵐' },
  fog: { cloud: 0.5, rain: 0, snow: 0, fogMul: 4.2, wind: 0.35, name: '濃霧' },
  snow: { cloud: 0.85, rain: 0, snow: 0.8, fogMul: 2.2, wind: 1.0, name: '雪' },
};
const ORDER = ['clear', 'fair', 'cloudy', 'rain', 'storm', 'fog', 'snow'];

export class Sky {
  constructor(seed = 7) {
    this.rng = makeRng(seed);
    this.time = 0.30;          // 0=真夜中, 0.25=夜明け, 0.5=正午, 0.75=日没
    this.clock = 0;            // 起動からの経過秒（風のうねり用。日をまたいでも切れない）
    this.paused = false;
    this.weather = 'fair';
    this.nextWeather = 'fair';
    this.blend = 1;
    this.weatherTimer = 120;
    this.lightning = 0;
    this.lightningTimer = 4;
    // 天候が世界に残す痕跡。降っている量ではなく、蓄積した量
    this.wetness = 0;      // 地面や物体の濡れ 0..1
    this.snowCover = 0;    // 積雪 0..1
    this.visibility = 1;   // 視程 0..1（霧・雨・雪で落ちる）
    this.gust = 1;         // 突風の倍率
    this.windDir = [1, 0.35];

    this.sunDir = [0, 1, 0];
    this.moonDir = [0, -1, 0];
    this.sunColor = [1, 1, 1];
    this.ambSky = [0.3, 0.35, 0.42];
    this.ambGnd = [0.15, 0.14, 0.12];
    this.zenith = [0.25, 0.45, 0.85];
    this.horizon = [0.7, 0.8, 0.9];
    this.fogDensity = 0.004;
    this.cloud = 0.3;
    this.wind = 0.7;
    this.night = 0;
    this.exposure = 1.15;
    this.grade = [1, 1, 1];
    this.update(0, null);
  }

  get isNight() { return this.night > 0.5; }
  /** time 0.25 = 日の出(6時), 0.5 = 正午 */
  get hour() { return (this.time * 24) % 24; }
  timeLabel() {
    const h = this.hour;
    const hh = Math.floor(h).toString().padStart(2, '0');
    const mm = Math.floor((h % 1) * 60).toString().padStart(2, '0');
    return `${hh}:${mm}`;
  }
  phaseLabel() {
    const h = this.hour;
    if (h < 5) return '深夜';
    if (h < 7) return '夜明け';
    if (h < 11) return '朝';
    if (h < 15) return '昼';
    if (h < 18) return '午後';
    if (h < 20) return '夕暮れ';
    return '夜';
  }
  weatherLabel() { return WEATHERS[this.weather].name; }

  skipToMorning() {
    this.time = 0.27;
  }

  /** 指定した時刻まで進める（篝火で時を待つ） */
  skipToHour(h) {
    const t = ((h % 24) + 24) % 24 / 24;
    // 一日ぶんは必ず進める（同じ時刻を指定したときに止まらないように）
    this.time = t;
  }

  setWeather(id, instant = false) {
    if (!WEATHERS[id]) return;
    this.nextWeather = id;
    if (instant) { this.weather = id; this.blend = 1; }
    else this.blend = 0;
    this.weatherTimer = 150 + this.rng() * 260;
  }

  /** 地方に応じた天候の傾向 */
  pickWeather(regionId) {
    const r = this.rng();
    switch (regionId) {
      case 'mistfen': return r < 0.45 ? 'fog' : r < 0.75 ? 'rain' : 'cloudy';
      case 'skyspire': return r < 0.4 ? 'snow' : r < 0.65 ? 'cloudy' : 'clear';
      case 'cinder': return r < 0.6 ? 'clear' : r < 0.85 ? 'fair' : 'cloudy';
      case 'gloomwood': return r < 0.35 ? 'fog' : r < 0.6 ? 'cloudy' : r < 0.85 ? 'rain' : 'fair';
      case 'coast': return r < 0.3 ? 'rain' : r < 0.5 ? 'storm' : r < 0.8 ? 'fair' : 'clear';
      default: return ORDER[(r * ORDER.length) | 0];
    }
  }

  update(dt, game) {
    if (!this.paused) this.time = (this.time + dt / DAY_LENGTH) % 1;
    this.clock += dt;

    // ---- 天候遷移 ----
    if (game) {
      this.weatherTimer -= dt;
      if (this.weatherTimer <= 0) {
        const region = game.world.regionAt(game.player.x, game.player.z);
        this.setWeather(this.pickWeather(region.id));
      }
      if (this.blend < 1) {
        this.blend = Math.min(1, this.blend + dt / 12);
        if (this.blend >= 1) this.weather = this.nextWeather;
      }
    }

    const wA = WEATHERS[this.weather];
    const wB = WEATHERS[this.nextWeather];
    const t = this.blend;
    const cloud = lerp(wA.cloud, wB.cloud, t);
    const rain = lerp(wA.rain, wB.rain, t);
    const snow = lerp(wA.snow, wB.snow, t);
    const fogMul = lerp(wA.fogMul, wB.fogMul, t);
    this.cloud = cloud;
    this.rainAmount = rain;
    this.snowAmount = snow;

    // ---- 風（一定ではなく、うねりと突風を持たせる）----
    const baseWind = lerp(wA.wind, wB.wind, t);
    const wt = this.clock;
    this.gust = 1 + 0.28 * Math.sin(wt * 0.21) + 0.16 * Math.sin(wt * 0.73 + 1.9)
      + 0.10 * Math.sin(wt * 1.9 + 4.1);
    this.wind = baseWind * this.gust;
    const wa = wt * 0.013;
    this.windDir = [Math.cos(wa), Math.sin(wa)];

    // ---- 太陽 ----
    const ang = (this.time - 0.25) * TAU;
    const sy = Math.sin(ang);
    const sx = Math.cos(ang) * 0.42;
    const sz = Math.cos(ang) * 0.86;
    const l = Math.hypot(sx, sy, sz) || 1;
    this.sunDir = [sx / l, sy / l, sz / l];
    this.moonDir = [-sx / l, -sy / l, -sz / l];

    const above = clamp01(this.sunDir[1]);
    this.night = 1 - smoothstep(-0.14, 0.10, this.sunDir[1]);
    const dusk = smoothstep(0.0, 0.22, this.sunDir[1]) * (1 - smoothstep(0.22, 0.45, this.sunDir[1]));

    // ---- 色 ----
    const dayZenith = [0.20, 0.42, 0.88];
    const dayHorizon = [0.72, 0.82, 0.92];
    const duskZenith = [0.24, 0.24, 0.52];
    const duskHorizon = [1.05, 0.52, 0.28];
    const nightZenith = [0.020, 0.030, 0.075];
    const nightHorizon = [0.055, 0.070, 0.13];

    const mix3 = (a, b, k) => [lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)];
    let zen = mix3(dayZenith, duskZenith, dusk);
    let hor = mix3(dayHorizon, duskHorizon, dusk);
    zen = mix3(zen, nightZenith, this.night);
    hor = mix3(hor, nightHorizon, this.night);

    // 曇天は彩度を落とし灰色へ
    const gray = cloud * 0.55;
    const grayC = [0.42, 0.44, 0.47];
    zen = mix3(zen, grayC, gray * (1 - this.night * 0.6));
    hor = mix3(hor, [0.55, 0.56, 0.58], gray * (1 - this.night * 0.6));

    this.zenith = zen;
    this.horizon = hor;

    // 太陽光
    const sunWarm = [1.0, 0.55, 0.25];
    const sunDay = [1.05, 1.0, 0.92];
    let sc = mix3(sunWarm, sunDay, smoothstep(0.02, 0.35, this.sunDir[1]));
    const sunStrength = clamp01(above * 1.9) * 1.55 * (1 - cloud * 0.55) * (1 - this.night);
    this.sunColor = [sc[0] * sunStrength, sc[1] * sunStrength, sc[2] * sunStrength];

    // 月光
    const moonUp = clamp01(this.moonDir[1]);
    const moonK = moonUp * this.night * 0.20 * (1 - cloud * 0.5);
    this.sunColor[0] += 0.55 * moonK;
    this.sunColor[1] += 0.62 * moonK;
    this.sunColor[2] += 0.85 * moonK;
    if (this.night > 0.5 && moonUp > 0.05) {
      // 夜は月を主光源として扱う
      this.sunDir = [this.moonDir[0], this.moonDir[1], this.moonDir[2]];
    }

    // 環境光（低めに抑え、太陽光との明暗差でコントラストを出す）
    this.ambSky = [
      lerp(0.048, 0.165, above) + 0.020 + this.night * 0.028,
      lerp(0.058, 0.192, above) + 0.023 + this.night * 0.034,
      lerp(0.085, 0.258, above) + 0.032 + this.night * 0.054,
    ];
    // 地面からの照り返し（暖色）。日陰が青く沈みすぎるのを防ぐ
    const bounce = 0.55 + above * 0.25;
    this.ambGnd = [
      this.ambSky[0] * bounce * 1.25 + 0.010,
      this.ambSky[1] * bounce * 1.05 + 0.009,
      this.ambSky[2] * bounce * 0.72 + 0.008,
    ];
    const cloudAmb = 1 + cloud * 0.42;
    for (let i = 0; i < 3; i++) { this.ambSky[i] *= cloudAmb; this.ambGnd[i] *= cloudAmb; }

    // 霧
    this.baseFog = lerp(0.0022, 0.0042, cloud) * fogMul;
    this.fogDensity = this.baseFog;

    // ---- 濡れ・積雪の蓄積 ----
    // 降り始めてすぐ全面が濡れるわけではなく、止んでもすぐには乾かない。
    // 乾きは日射と風に比例する（曇りや夜は長く濡れたまま）。
    if (rain > 0.02) {
      const soak = 1 - Math.exp(-dt * (0.020 + rain * 0.055));
      this.wetness += (clamp01(rain * 1.35) - this.wetness) * soak;
    } else {
      // 快晴の正午でおよそ 4 分、夜や曇天では 15 分以上かかる
      const dry = 0.0006 + above * 0.0038 * (1 - cloud * 0.55) + this.wind * 0.0006;
      this.wetness = Math.max(0, this.wetness - dry * dt);
    }
    if (snow > 0.02) {
      const pile = 1 - Math.exp(-dt * (0.010 + snow * 0.028));
      this.snowCover += (clamp01(snow * 1.15) - this.snowCover) * pile;
      // 雪が積もっている面は濡れとして扱わない
      this.wetness = Math.min(this.wetness, 1 - this.snowCover * 0.8);
    } else {
      // 雪は水たまりより長く残る
      const melt = 0.0004 + above * 0.0022 * (1 - cloud * 0.5);
      this.snowCover = Math.max(0, this.snowCover - melt * dt);
    }

    // ---- 視程 ----
    // 霧が最も効き、次いで雪、雨。索敵距離と遠景の見え方に使う
    this.visibility = clamp(
      1 - (fogMul - 0.85) * 0.155 - rain * 0.18 - snow * 0.24, 0.30, 1);

    // 露出・グレーディング
    this.exposure = lerp(1.40, 0.80, above) * lerp(1, 0.94, cloud);
    this.grade = [
      lerp(1.02, 0.94, this.night),
      lerp(1.00, 0.97, this.night),
      lerp(0.96, 1.10, this.night),
    ];

    // ---- 雷 ----
    this.lightning = Math.max(0, this.lightning - dt * 5);
    if (this.weather === 'storm' || this.nextWeather === 'storm') {
      this.lightningTimer -= dt;
      if (this.lightningTimer <= 0) {
        this.lightningTimer = 4 + this.rng() * 12;
        this.lightning = 1;
        game?.audio.play('thunder', { delay: 0.4 + this.rng() * 1.6 });
      }
    }
    if (this.lightning > 0) {
      const k = this.lightning * 0.9;
      this.sunColor[0] += k; this.sunColor[1] += k; this.sunColor[2] += k * 1.1;
      this.ambSky[0] += k * 0.6; this.ambSky[1] += k * 0.6; this.ambSky[2] += k * 0.7;
    }
  }

  /** 地方ごとの霧の色・濃さを適用 */
  applyRegion(params) {
    this.fogDensity = this.baseFog * (0.55 + params.fogDensity * 0.55);
    const tint = params.fog;
    const k = 0.30;
    this.horizon = [
      lerp(this.horizon[0], tint[0], k),
      lerp(this.horizon[1], tint[1], k),
      lerp(this.horizon[2], tint[2], k),
    ];
  }

  serialize() {
    return { time: this.time, weather: this.weather,
      wetness: +this.wetness.toFixed(3), snowCover: +this.snowCover.toFixed(3) };
  }
  deserialize(d) {
    if (!d) return;
    this.time = d.time ?? this.time;
    this.setWeather(d.weather || 'fair', true);
    this.wetness = clamp01(d.wetness ?? 0);
    this.snowCover = clamp01(d.snowCover ?? 0);
  }
}

export { clamp, clamp01, lerp };
