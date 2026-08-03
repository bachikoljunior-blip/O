// 手続き的オーディオ：WebAudio による効果音合成と適応型 BGM（音源ファイル不要）

const A4 = 440;
const note = (semi) => A4 * Math.pow(2, semi / 12);

/** 各モードの音階（半音オフセット） */
const SCALES = {
  explore: [-24, -17, -12, -10, -5, 0, 3, 7, 12],       // 静かな自然短音階
  tension: [-24, -18, -12, -11, -6, -1, 2, 6],
  combat: [-24, -19, -12, -8, -5, -1, 4, 7],
  boss: [-27, -22, -15, -13, -8, -3, 1, 6],
  final: [-29, -24, -17, -16, -10, -5, 2, 7],
  dungeon: [-31, -24, -19, -17, -12, -7, -5, 0],
};

/**
 * 旋法（教会旋法）。主音からの音度を半音で並べたもの。
 * どの旋法を使うかで土地の性格が変わる。
 *   エオリアン＝物悲しい / ドリアン＝古風で凛とした / フリギアン＝異国めいた不安
 *   リディアン＝浮遊感のある明るさ / イオニアン＝素直な明るさ
 */
const MODES = {
  aeolian: [0, 2, 3, 5, 7, 8, 10],
  dorian: [0, 2, 3, 5, 7, 9, 10],
  phrygian: [0, 1, 3, 5, 7, 8, 10],
  lydian: [0, 2, 4, 6, 7, 9, 11],
  ionian: [0, 2, 4, 5, 7, 9, 11],
};

/**
 * 和音進行。数字は旋法上の音度（0 = 主和音）。
 * 1 小節にひとつ進み、低音とパッドと旋律がこれを共有する。
 */
const PROGRESSIONS = {
  explore: [0, 5, 3, 4],
  combat: [0, 6, 5, 0, 3, 4, 4, 0],
  boss: [0, 0, 5, 5, 3, 3, 4, 6],
  final: [0, 1, 0, 1, 5, 6, 4, 4],
  dungeon: [0, 0, 6, 0, 3, 3, 1, 0],
};

/**
 * 地方ごとの調と旋法。探索中の音楽の色を決める。
 * root は半音単位の移調量。
 */
export const REGION_TONE = {
  downs: { root: 0, mode: 'dorian', bright: 0.65 },
  gloomwood: { root: -3, mode: 'aeolian', bright: 0.25 },
  cinder: { root: -1, mode: 'phrygian', bright: 0.20 },
  mistfen: { root: -5, mode: 'aeolian', bright: 0.30 },
  skyspire: { root: 2, mode: 'lydian', bright: 0.80 },
  goldreach: { root: 5, mode: 'ionian', bright: 0.90 },
  riftvale: { root: -6, mode: 'phrygian', bright: 0.15 },
  coast: { root: 3, mode: 'dorian', bright: 0.70 },
};

/** 旋法と音度から和音（三和音）の構成音を作る */
function chordOf(mode, degree, root) {
  const sc = MODES[mode] || MODES.aeolian;
  const at = (i) => {
    const oct = Math.floor(i / sc.length) * 12;
    return sc[((i % sc.length) + sc.length) % sc.length] + oct + root;
  };
  return [at(degree), at(degree + 2), at(degree + 4)];
}

export class AudioEngine {
  constructor() {
    this.ctx = null;
    this.ready = false;
    this.master = 0.8;
    this.sfxVol = 0.9;
    this.musicVol = 0.5;
    this.mode = 'explore';
    this.nextNote = 0;
    this.beat = 0;
    this.intensity = 0;
    this.muted = false;
  }

  init() {
    if (this.ctx) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    this.ctx = new AC();
    this.masterGain = this.ctx.createGain();
    this.masterGain.gain.value = this.master;
    this.masterGain.connect(this.ctx.destination);

    this.sfxGain = this.ctx.createGain();
    this.sfxGain.gain.value = this.sfxVol;
    this.sfxGain.connect(this.masterGain);

    this.musicGain = this.ctx.createGain();
    this.musicGain.gain.value = 0;
    this.musicGain.connect(this.masterGain);

    // 残響
    this.reverb = this.ctx.createConvolver();
    this.reverb.buffer = this._impulse(2.2, 2.4);
    this.reverbGain = this.ctx.createGain();
    this.reverbGain.gain.value = 0.24;
    this.reverbGain.connect(this.masterGain);
    this.reverb.connect(this.reverbGain);

    this.noiseBuf = this._noise(2);
    this.ready = true;
  }

  resume() {
    this.init();
    if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume();
  }

  setVolumes({ master, sfx, music }) {
    if (master !== undefined) this.master = master;
    if (sfx !== undefined) this.sfxVol = sfx;
    if (music !== undefined) this.musicVol = music;
    if (!this.ready) return;
    this.masterGain.gain.value = this.muted ? 0 : this.master;
    this.sfxGain.gain.value = this.sfxVol;
  }

  _noise(sec) {
    const ctx = this.ctx;
    const len = ctx.sampleRate * sec;
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    return buf;
  }

  _impulse(sec, decay) {
    const ctx = this.ctx;
    const len = Math.floor(ctx.sampleRate * sec);
    const buf = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let c = 0; c < 2; c++) {
      const d = buf.getChannelData(c);
      for (let i = 0; i < len; i++) {
        d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
      }
    }
    return buf;
  }

  /* -------------------------------------------------------- 合成部品 */
  tone({ freq = 440, dur = 0.2, type = 'sine', gain = 0.2, attack = 0.005, decay = null,
    sweep = 0, detune = 0, delay = 0, reverb = 0, dest = null } = {}) {
    if (!this.ready) return;
    const ctx = this.ctx;
    const t0 = ctx.currentTime + delay;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (sweep) osc.frequency.exponentialRampToValueAtTime(Math.max(20, freq * sweep), t0 + dur);
    if (detune) osc.detune.value = detune;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + (decay ?? dur));
    osc.connect(g);
    g.connect(dest || this.sfxGain);
    if (reverb > 0) {
      const rg = ctx.createGain();
      rg.gain.value = reverb;
      g.connect(rg);
      rg.connect(this.reverb);
    }
    osc.start(t0);
    osc.stop(t0 + (decay ?? dur) + 0.05);
  }

  noise({ dur = 0.2, gain = 0.2, freq = 1200, q = 1, type = 'bandpass', sweep = 0,
    delay = 0, reverb = 0 } = {}) {
    if (!this.ready) return;
    const ctx = this.ctx;
    const t0 = ctx.currentTime + delay;
    const src = ctx.createBufferSource();
    src.buffer = this.noiseBuf;
    src.loop = true;
    const f = ctx.createBiquadFilter();
    f.type = type;
    f.frequency.setValueAtTime(freq, t0);
    if (sweep) f.frequency.exponentialRampToValueAtTime(Math.max(60, freq * sweep), t0 + dur);
    f.Q.value = q;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), t0 + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(f); f.connect(g); g.connect(this.sfxGain);
    if (reverb > 0) {
      const rg = ctx.createGain();
      rg.gain.value = reverb;
      g.connect(rg); rg.connect(this.reverb);
    }
    src.start(t0);
    src.stop(t0 + dur + 0.05);
  }

  /* ---------------------------------------------------------- 効果音 */
  play(name, opts = {}) {
    if (!this.ready || this.muted) return;
    const p = opts.pitch || 1;
    const d = opts.delay || 0;
    const w = Math.max(0, Math.min(1, opts.weight ?? 0.4));   // 打撃の重さ
    switch (name) {
      case 'splash':
        // 白色ノイズを高域から低域へ落として「ばしゃっ」を作る
        this.noise({ dur: 0.34, gain: 0.16, freq: 2600 * p, sweep: 0.12, q: 0.5, delay: d });
        this.noise({ dur: 0.5, gain: 0.07, freq: 700 * p, sweep: 0.4, q: 0.8, delay: d + 0.03 });
        break;
      case 'swing':
        this.noise({ dur: 0.16, gain: 0.16, freq: 900 * p, sweep: 0.35, q: 1.4, delay: d });
        break;
      case 'hit':
        this.noise({ dur: 0.12, gain: 0.34, freq: 420 * p, sweep: 0.3, q: 0.9, delay: d, reverb: 0.2 });
        this.tone({ freq: 120 * p, dur: 0.10, type: 'square', gain: 0.16, sweep: 0.4, delay: d });
        break;

      /* ---- 当たった相手の素材で音が変わる。w は打撃の重さ 0..1 ---- */
      case 'hit_flesh':
        // 湿った鈍い音。低いほうに寄せる
        this.noise({ dur: 0.10 + w * 0.10, gain: 0.26 + w * 0.16, freq: 380 * p,
          sweep: 0.26, q: 0.8, delay: d, reverb: 0.18 });
        this.tone({ freq: (96 - w * 26) * p, dur: 0.10 + w * 0.10, type: 'sine',
          gain: 0.14 + w * 0.14, sweep: 0.35, delay: d });
        break;
      case 'hit_armor':
        // 金属の高い立ち上がりと、その下の胴鳴り
        this.noise({ dur: 0.07, gain: 0.20 + w * 0.14, freq: 3200 * p, sweep: 0.55, q: 3.4, delay: d });
        this.tone({ freq: 740 * p, dur: 0.16, type: 'triangle', gain: 0.12 + w * 0.10,
          sweep: 0.9, delay: d, reverb: 0.3 });
        this.tone({ freq: (150 - w * 30) * p, dur: 0.14, type: 'square',
          gain: 0.10 + w * 0.12, sweep: 0.5, delay: d + 0.01 });
        break;
      case 'hit_bone':
        // 乾いた木質の割れ
        this.noise({ dur: 0.055, gain: 0.30 + w * 0.14, freq: 1700 * p, sweep: 0.7, q: 2.2, delay: d });
        this.noise({ dur: 0.09, gain: 0.16, freq: 620 * p, sweep: 0.45, q: 1.2, delay: d + 0.012 });
        this.tone({ freq: 300 * p, dur: 0.07, type: 'square', gain: 0.09 + w * 0.08, sweep: 0.8, delay: d });
        break;
      case 'hit_stone':
        // 石を打つ。減衰が長く、粉が飛ぶ
        this.noise({ dur: 0.05, gain: 0.32 + w * 0.16, freq: 2400 * p, sweep: 0.75, q: 1.8, delay: d });
        this.tone({ freq: (72 - w * 16) * p, dur: 0.26 + w * 0.14, type: 'sawtooth',
          gain: 0.14 + w * 0.14, sweep: 0.30, delay: d, reverb: 0.42 });
        this.noise({ dur: 0.22, gain: 0.08, freq: 900, sweep: 0.3, delay: d + 0.03, reverb: 0.3 });
        break;
      case 'hit_spirit':
        // 実体が無い相手。打撃音というより空気が鳴る
        this.tone({ freq: 620 * p, dur: 0.30, type: 'sine', gain: 0.14 + w * 0.08,
          sweep: 1.5, delay: d, reverb: 0.62 });
        this.noise({ dur: 0.26, gain: 0.10, freq: 1500, sweep: 0.5, q: 0.6, delay: d, reverb: 0.5 });
        break;

      case 'keen':
        // 研ぎ澄まされた刃だけが立てる、当たった直後の澄んだ余韻
        this.tone({ freq: 2100 * p, dur: 0.26, type: 'sine', gain: 0.09 + w * 0.07,
          sweep: 1.7, delay: d, reverb: 0.55 });
        this.tone({ freq: 3150 * p, dur: 0.18, type: 'sine', gain: 0.05 + w * 0.04,
          sweep: 1.5, delay: d + 0.012, reverb: 0.5 });
        break;
      case 'boss_fell':
        // 討伐の一打。低く長く、下へ抜ける
        this.tone({ freq: 52, dur: 1.6, type: 'sine', gain: 0.30, sweep: 0.22,
          delay: d, reverb: 0.85 });
        this.tone({ freq: 78, dur: 1.1, type: 'triangle', gain: 0.16, sweep: 0.30,
          delay: d + 0.05, reverb: 0.75 });
        this.noise({ dur: 0.9, gain: 0.14, freq: 420, sweep: 0.18, delay: d, reverb: 0.7 });
        break;
      case 'victory':
        // 静けさのあとに置く、短い上行の和音
        for (const [i, f] of [220, 277.2, 329.6, 440].entries()) {
          this.tone({ freq: f, dur: 2.2 - i * 0.2, type: 'triangle',
            gain: 0.10 - i * 0.012, sweep: 0.16, delay: d + i * 0.10, reverb: 0.8 });
        }
        break;
      case 'gather_plant':
        // 草を摘む。短い擦れと、茎が切れる小さな音
        this.noise({ dur: 0.16, gain: 0.16, freq: 2600 * p, sweep: 0.45, q: 0.7, delay: d });
        this.noise({ dur: 0.07, gain: 0.10, freq: 900 * p, sweep: 0.6, q: 1.6, delay: d + 0.06 });
        break;
      case 'gather_ore':
        // 鉱脈を割る。硬い一打と、砕けた粒が落ちる音
        this.noise({ dur: 0.06, gain: 0.26, freq: 2200 * p, sweep: 0.7, q: 2.0, delay: d });
        this.tone({ freq: 150 * p, dur: 0.18, type: 'square', gain: 0.12, sweep: 0.5, delay: d });
        this.noise({ dur: 0.30, gain: 0.10, freq: 1400, sweep: 0.35, q: 0.8, delay: d + 0.05, reverb: 0.25 });
        break;
      case 'poise_break':
        // 体勢を崩したときだけの、下へ抜ける音
        this.tone({ freq: 210, dur: 0.34, type: 'sawtooth', gain: 0.22, sweep: 0.30,
          delay: d, reverb: 0.45 });
        this.noise({ dur: 0.18, gain: 0.24, freq: 1100, sweep: 0.35, q: 1.5, delay: d });
        break;
      case 'kill_blow':
        // とどめ。低い一撃と、残響
        this.tone({ freq: 68, dur: 0.55, type: 'sine', gain: 0.26, sweep: 0.35,
          delay: d, reverb: 0.65 });
        this.noise({ dur: 0.30, gain: 0.18, freq: 700, sweep: 0.25, delay: d + 0.02, reverb: 0.5 });
        break;
      case 'block':
        this.noise({ dur: 0.16, gain: 0.3, freq: 2400, sweep: 0.4, q: 3, delay: d });
        this.tone({ freq: 320, dur: 0.14, type: 'triangle', gain: 0.14, sweep: 0.6, delay: d });
        break;
      case 'guardbreak':
        this.tone({ freq: 180, dur: 0.5, type: 'sawtooth', gain: 0.24, sweep: 0.35, delay: d, reverb: 0.4 });
        this.noise({ dur: 0.35, gain: 0.24, freq: 900, sweep: 0.2, delay: d });
        break;
      case 'parry':
        this.tone({ freq: 1500, dur: 0.32, type: 'triangle', gain: 0.26, sweep: 1.6, delay: d, reverb: 0.5 });
        this.tone({ freq: 2400, dur: 0.22, type: 'sine', gain: 0.18, sweep: 1.4, delay: d + 0.02 });
        break;
      case 'parry_swing':
        this.noise({ dur: 0.12, gain: 0.12, freq: 1800, sweep: 0.5, q: 2, delay: d });
        break;
      case 'critical':
        this.tone({ freq: 90, dur: 0.6, type: 'sawtooth', gain: 0.3, sweep: 0.4, delay: d, reverb: 0.6 });
        this.noise({ dur: 0.4, gain: 0.34, freq: 600, sweep: 0.25, delay: d + 0.24, reverb: 0.5 });
        break;
      case 'death':
        this.tone({ freq: 220, dur: 0.9, type: 'sine', gain: 0.2, sweep: 0.3, delay: d, reverb: 0.7 });
        this.noise({ dur: 0.6, gain: 0.16, freq: 500, sweep: 0.25, delay: d });
        break;
      case 'aggro':
        this.tone({ freq: 300 * p, dur: 0.4, type: 'sawtooth', gain: 0.14, sweep: 0.55, delay: d, reverb: 0.4 });
        break;
      case 'roll':
        this.noise({ dur: 0.28, gain: 0.16, freq: 380, sweep: 0.4, q: 0.7, delay: d });
        break;
      case 'jump':
        this.noise({ dur: 0.14, gain: 0.12, freq: 600, sweep: 0.5, delay: d });
        break;
      case 'drink':
        this.tone({ freq: 420, dur: 0.3, type: 'sine', gain: 0.16, sweep: 1.5, delay: d });
        break;
      case 'heal':
        for (let i = 0; i < 3; i++) {
          this.tone({ freq: note(4 + i * 5), dur: 0.6, type: 'sine', gain: 0.1, delay: d + i * 0.07, reverb: 0.6 });
        }
        break;
      case 'buff':
        this.tone({ freq: note(-5), dur: 0.8, type: 'triangle', gain: 0.12, sweep: 1.5, delay: d, reverb: 0.6 });
        break;
      case 'cast_start':
        this.tone({ freq: 300, dur: 0.4, type: 'sine', gain: 0.10, sweep: 2.0, delay: d });
        break;
      case 'cast':
        this.tone({ freq: 900, dur: 0.35, type: 'triangle', gain: 0.16, sweep: 0.35, delay: d, reverb: 0.5 });
        this.noise({ dur: 0.3, gain: 0.12, freq: 2600, sweep: 0.3, q: 2, delay: d });
        break;
      case 'bow':
        this.noise({ dur: 0.12, gain: 0.2, freq: 1600, sweep: 0.25, q: 2.5, delay: d });
        this.tone({ freq: 180, dur: 0.14, type: 'triangle', gain: 0.1, sweep: 0.6, delay: d });
        break;
      case 'throw':
        this.noise({ dur: 0.14, gain: 0.14, freq: 1100, sweep: 0.4, delay: d });
        break;
      case 'slam':
        this.tone({ freq: 70, dur: 0.7, type: 'sine', gain: 0.36, sweep: 0.4, delay: d, reverb: 0.7 });
        this.noise({ dur: 0.5, gain: 0.24, freq: 300, sweep: 0.2, delay: d, reverb: 0.5 });
        break;
      case 'teleport':
        this.tone({ freq: 1200, dur: 0.3, type: 'sine', gain: 0.14, sweep: 0.2, delay: d, reverb: 0.6 });
        break;
      case 'boss_phase':
        this.tone({ freq: 55, dur: 1.6, type: 'sawtooth', gain: 0.3, sweep: 1.2, delay: d, reverb: 0.9 });
        this.noise({ dur: 1.2, gain: 0.2, freq: 200, sweep: 3, delay: d, reverb: 0.8 });
        break;
      case 'thunder':
        this.noise({ dur: 1.8, gain: 0.34, freq: 220, sweep: 0.25, q: 0.4, delay: d, reverb: 0.9 });
        this.tone({ freq: 48, dur: 1.4, type: 'sine', gain: 0.22, sweep: 0.6, delay: d, reverb: 0.9 });
        break;
      case 'shrine':
        for (let i = 0; i < 4; i++) {
          this.tone({ freq: note([-12, -5, 0, 7][i]), dur: 1.6, type: 'sine', gain: 0.09, delay: d + i * 0.12, reverb: 0.9 });
        }
        break;
      case 'discover':
        for (let i = 0; i < 3; i++) {
          this.tone({ freq: note([0, 4, 7][i]), dur: 0.9, type: 'triangle', gain: 0.09, delay: d + i * 0.1, reverb: 0.7 });
        }
        break;
      case 'chest':
        this.noise({ dur: 0.5, gain: 0.16, freq: 400, sweep: 0.5, q: 1, delay: d });
        this.tone({ freq: note(7), dur: 0.7, type: 'sine', gain: 0.1, delay: d + 0.2, reverb: 0.6 });
        break;
      case 'upgrade':
        this.noise({ dur: 0.18, gain: 0.22, freq: 3000, sweep: 0.2, q: 2, delay: d });
        this.tone({ freq: note(12), dur: 0.5, type: 'triangle', gain: 0.12, delay: d + 0.05, reverb: 0.5 });
        break;
      case 'levelup':
        for (let i = 0; i < 5; i++) {
          this.tone({ freq: note([0, 4, 7, 11, 14][i]), dur: 1.2, type: 'sine', gain: 0.1, delay: d + i * 0.09, reverb: 0.8 });
        }
        break;
      case 'echo':
        this.tone({ freq: note(12 + (Math.random() * 4 | 0)), dur: 0.22, type: 'sine', gain: 0.07, delay: d });
        break;
      case 'whistle':
        this.tone({ freq: 1400, dur: 0.20, type: 'sine', gain: 0.09, sweep: 1.5, delay: d, reverb: 0.6 });
        this.tone({ freq: 2100, dur: 0.26, type: 'sine', gain: 0.07, sweep: 0.7, delay: d + 0.18, reverb: 0.7 });
        break;
      case 'mount_up':
        this.noise({ dur: 0.22, gain: 0.16, freq: 300, sweep: 0.5, q: 0.8, delay: d });
        this.tone({ freq: 160, dur: 0.3, type: 'triangle', gain: 0.10, sweep: 0.7, delay: d });
        break;
      case 'ui_on':
        this.tone({ freq: 880, dur: 0.09, type: 'square', gain: 0.06, delay: d });
        break;
      case 'ui_off':
        this.tone({ freq: 520, dur: 0.09, type: 'square', gain: 0.05, delay: d });
        break;
      case 'ui_move':
        this.tone({ freq: 700, dur: 0.05, type: 'square', gain: 0.04, delay: d });
        break;
      case 'ui_confirm':
        this.tone({ freq: 1046, dur: 0.12, type: 'triangle', gain: 0.07, delay: d });
        break;
      case 'fail':
        this.tone({ freq: 180, dur: 0.16, type: 'square', gain: 0.07, sweep: 0.6, delay: d });
        break;
      default:
        break;
    }
  }

  footstep(surface, vol = 0.35) {
    if (!this.ready || this.muted) return;
    const cfg = {
      grass: { f: 1500, q: 0.8, d: 0.10, g: 0.10 },
      dirt: { f: 700, q: 0.7, d: 0.09, g: 0.13 },
      sand: { f: 2400, q: 0.5, d: 0.13, g: 0.09 },
      rock: { f: 1100, q: 2.2, d: 0.07, g: 0.16 },
      snow: { f: 3000, q: 0.6, d: 0.12, g: 0.08 },
      marsh: { f: 500, q: 0.6, d: 0.16, g: 0.14 },
      water: { f: 2100, q: 0.35, d: 0.22, g: 0.15 },
    }[surface] || { f: 1000, q: 1, d: 0.1, g: 0.12 };
    this.noise({ dur: cfg.d, gain: cfg.g * vol * 2.4, freq: cfg.f * (0.85 + Math.random() * 0.3), q: cfg.q, sweep: 0.5 });
  }

  /* ---------------------------------------------------------- 環境音 */
  startAmbience() {
    if (!this.ready || this.ambience) return;
    const ctx = this.ctx;
    const src = ctx.createBufferSource();
    src.buffer = this.noiseBuf;
    src.loop = true;
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 420;
    lp.Q.value = 0.5;
    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 90;
    const g = ctx.createGain();
    g.gain.value = 0;
    src.connect(hp); hp.connect(lp); lp.connect(g); g.connect(this.masterGain);
    src.start();

    // 水辺用の別レイヤ
    const wsrc = ctx.createBufferSource();
    wsrc.buffer = this.noiseBuf;
    wsrc.loop = true;
    const wf = ctx.createBiquadFilter();
    wf.type = 'bandpass';
    wf.frequency.value = 900;
    wf.Q.value = 0.7;
    const wg = ctx.createGain();
    wg.gain.value = 0;
    wsrc.connect(wf); wf.connect(wg); wg.connect(this.masterGain);
    wsrc.start();

    this.ambience = { g, lp, wg, wf, chirp: 2, lfo: 0 };
  }

  /** 風・水音・鳥の声を状況に合わせて鳴らす */
  updateAmbience(dt, game) {
    if (!this.ready || this.muted) return;
    if (!this.ambience) this.startAmbience();
    const a = this.ambience;
    if (!a) return;
    const ctx = this.ctx;
    const sky = game.sky;
    const p = game.player;

    // ---- 地下：風も波も鳥もいない。反響と、遠くの水滴だけ ----
    if (game.dungeon) {
      a.g.gain.setTargetAtTime(0.014 * this.sfxVol, ctx.currentTime, 0.8);
      a.lp.frequency.setTargetAtTime(180, ctx.currentTime, 0.8);
      a.wg.gain.setTargetAtTime(0, ctx.currentTime, 0.6);
      a.drip = (a.drip ?? 3) - dt;
      if (a.drip <= 0) {
        a.drip = 2.4 + Math.random() * 6.5;
        // 水滴：高い音が短く落ちて、長く反響する
        this.tone({
          freq: 900 + Math.random() * 900, dur: 0.09, type: 'sine',
          gain: 0.030, sweep: 0.35, reverb: 1.4,
        });
      }
      a.groan = (a.groan ?? 8) - dt;
      if (a.groan <= 0) {
        a.groan = 9 + Math.random() * 16;
        // 地鳴り
        this.tone({ freq: 42 + Math.random() * 20, dur: 2.6, type: 'sine', gain: 0.035, attack: 0.9, reverb: 1.2 });
      }
      return;
    }

    // 風：天候と標高で強くなる。ゆっくり息づかせる
    a.lfo += dt * 0.23;
    const gust = 0.65 + 0.35 * Math.sin(a.lfo * 1.7) * Math.sin(a.lfo * 0.53 + 1.2);
    const alt = Math.max(0, (p.y - 40) / 160);
    const windAmt = Math.min(1.6, sky.wind * (0.55 + alt * 0.9)) * gust;
    a.g.gain.setTargetAtTime(0.035 * windAmt * this.sfxVol, ctx.currentTime, 0.5);
    a.lp.frequency.setTargetAtTime(360 + windAmt * 700, ctx.currentTime, 0.6);

    // 水辺：近くの海面／湖面
    const gh = game.world.height(p.x, p.z);
    const nearWater = Math.max(0, 1 - Math.max(0, gh) / 7);
    a.wg.gain.setTargetAtTime(0.030 * nearWater * this.sfxVol, ctx.currentTime, 0.7);
    a.wf.frequency.setTargetAtTime(700 + nearWater * 500, ctx.currentTime, 0.8);

    // 雨は高域のホワイトノイズを足す
    if (sky.rainAmount > 0.05) {
      a.wg.gain.setTargetAtTime(
        (0.030 * nearWater + 0.055 * sky.rainAmount) * this.sfxVol, ctx.currentTime, 0.5);
      a.wf.frequency.setTargetAtTime(2400 + sky.rainAmount * 1800, ctx.currentTime, 0.5);
    }

    // 生き物の声
    a.chirp -= dt;
    if (a.chirp <= 0) {
      const region = game.world.regionAt(p.x, p.z);
      const night = sky.isNight;
      a.chirp = 1.6 + Math.random() * 5.5;
      if (sky.rainAmount > 0.4) return;
      if (!night && (region.id === 'downs' || region.id === 'goldreach' || region.id === 'coast')) {
        // 小鳥
        const base = 1800 + Math.random() * 1400;
        for (let i = 0; i < 2 + (Math.random() * 3 | 0); i++) {
          this.tone({
            freq: base * (1 + Math.random() * 0.3), dur: 0.07, type: 'sine',
            gain: 0.022, sweep: 1.4 + Math.random(), delay: i * 0.09, reverb: 0.5,
          });
        }
      } else if (!night && region.id === 'gloomwood') {
        this.tone({ freq: 420 + Math.random() * 160, dur: 0.5, type: 'sine', gain: 0.02, sweep: 0.7, reverb: 0.9 });
      } else if (night) {
        // 虫の音／梟
        if (Math.random() < 0.5) {
          for (let i = 0; i < 6; i++) {
            this.noise({ dur: 0.02, gain: 0.012, freq: 5200, q: 8, delay: i * 0.045 });
          }
        } else {
          this.tone({ freq: 300, dur: 0.35, type: 'sine', gain: 0.018, sweep: 0.85, reverb: 0.8 });
          this.tone({ freq: 300, dur: 0.3, type: 'sine', gain: 0.014, sweep: 0.9, delay: 0.42, reverb: 0.8 });
        }
      }
    }

    // ---- 人里の気配：近くに集落があるとき ----
    this.updateSettlement(dt, game);
  }

  /**
   * 集落が近いと聞こえてくる音。
   * 鍛冶の槌、遠くの話し声、鐘。夜は静まる。
   */
  updateSettlement(dt, game) {
    const a = this.ambience;
    const p = game.player;
    let near = 0, hasSmith = false;
    for (const poi of game.pois) {
      if (poi.type !== 'village' && poi.type !== 'hermit') continue;
      const d = Math.hypot(poi.x - p.x, poi.z - p.z);
      const r = poi.type === 'village' ? 70 : 26;
      if (d > r) continue;
      const w = 1 - d / r;
      if (w > near) { near = w; hasSmith = poi.type === 'village' && (poi.size || 0) >= 6; }
    }
    a.town = (a.town ?? 3) - dt;
    if (near < 0.12 || a.town > 0) return;
    const night = game.sky.isNight;
    a.town = (night ? 5.5 : 2.2) + Math.random() * (night ? 8 : 5);
    const vol = near * (night ? 0.4 : 1);

    const r = Math.random();
    if (hasSmith && !night && r < 0.40) {
      // 鍛冶の槌：不揃いな三連打
      const n = 2 + (Math.random() * 2 | 0);
      for (let i = 0; i < n; i++) {
        this.noise({
          dur: 0.09, gain: 0.045 * vol, freq: 2600 + Math.random() * 900, q: 3.0,
          sweep: 0.35, delay: i * (0.22 + Math.random() * 0.1), reverb: 0.5,
        });
      }
    } else if (r < 0.72) {
      // 遠くの話し声：短い有声音を数個
      const base = 150 + Math.random() * 120;
      for (let i = 0; i < 2 + (Math.random() * 3 | 0); i++) {
        this.tone({
          freq: base * (0.85 + Math.random() * 0.4), dur: 0.11 + Math.random() * 0.1,
          type: 'sawtooth', gain: 0.012 * vol, sweep: 0.8 + Math.random() * 0.4,
          delay: i * (0.16 + Math.random() * 0.12), reverb: 0.7,
        });
      }
    } else if (!night && r < 0.86) {
      // 犬・家畜
      this.tone({ freq: 420, dur: 0.16, type: 'sawtooth', gain: 0.020 * vol, sweep: 0.55, reverb: 0.6 });
    } else if (night) {
      // 夜の鐘（時報）
      for (const h of [1, 2.02, 3.01]) {
        this.tone({ freq: 220 * h, dur: 3.4, type: 'sine', gain: 0.030 * vol / h, attack: 0.02, reverb: 1.2 });
      }
    }
  }

  /* ------------------------------------------------------------ BGM */
  setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    this.nextNote = 0;
  }

  /** 今いる土地の調と旋法。探索中だけ土地の色を反映する */
  toneOf(game) {
    if (!game) return REGION_TONE.downs;
    if (game.dungeon) return { root: -4, mode: 'phrygian', bright: 0.1 };
    const id = game.world?.regionAt(game.player.x, game.player.z)?.id;
    return REGION_TONE[id] || REGION_TONE.downs;
  }

  update(dt, game) {
    if (!this.ready || this.muted) return;
    const ctx = this.ctx;
    const quiet = this.mode === 'explore' || this.mode === 'dungeon';
    const wantVol = this.mode === 'none' ? 0 : this.musicVol * (quiet ? 0.5 : 0.85);
    this.musicGain.gain.setTargetAtTime(wantVol, ctx.currentTime, 0.8);
    if (this.mode === 'none') return;

    const bpm = this.mode === 'boss' ? 112 : this.mode === 'final' ? 128
      : this.mode === 'combat' ? 96 : this.mode === 'dungeon' ? 44 : 54;
    const spb = 60 / bpm;
    this.nextNote -= dt;
    if (this.nextNote > 0) return;
    this.nextNote = spb;
    this.beat++;

    const t = ctx.currentTime;
    const isBoss = this.mode === 'boss' || this.mode === 'final';
    const isCalm = this.mode === 'explore' || this.mode === 'dungeon';

    // 土地の調と旋法（戦闘中は固定の緊迫した色にする）
    const rt = isCalm ? this.toneOf(game) : { root: 0, mode: isBoss ? 'phrygian' : 'aeolian', bright: 0.3 };
    const scale = SCALES[this.mode] || SCALES.explore;
    const base = scale[0] + rt.root;

    // 和音を 1 小節（4 拍）ごとに進める
    const prog = PROGRESSIONS[this.mode] || PROGRESSIONS.explore;
    const bar = Math.floor((this.beat - 1) / 4);
    const degree = prog[bar % prog.length];
    const chord = chordOf(rt.mode, degree, base);
    const chordChanged = (this.beat - 1) % 4 === 0;

    // ---- 低音：小節頭で和音の根音を置く ----
    if (chordChanged || (isBoss && this.beat % 2 === 1)) {
      this.tone({
        freq: note(chord[0]), dur: isBoss ? spb * 1.1 : spb * 4.2,
        type: isBoss ? 'sawtooth' : 'sine',
        gain: isBoss ? 0.16 : 0.10, attack: isBoss ? 0.01 : 0.6,
        dest: this.musicGain, reverb: 0.5,
      });
    }

    // ---- パッド：和音を長く伸ばして響かせる ----
    if (chordChanged) {
      const padGain = isBoss ? 0.028 : this.mode === 'dungeon' ? 0.020 : 0.034;
      const padDur = spb * (isBoss ? 3.4 : 4.6);
      chord.forEach((n, i) => {
        this.tone({
          freq: note(n + 12), dur: padDur, type: isBoss ? 'triangle' : 'sine',
          gain: padGain * (i === 0 ? 1 : 0.72), attack: isBoss ? 0.12 : 0.8,
          detune: (i - 1) * 4, dest: this.musicGain, reverb: 0.9,
        });
      });
    }

    // ---- 打楽器 ----
    if (isBoss) {
      this.noise({ dur: 0.14, gain: 0.10, freq: 120, q: 0.6 });
      if (this.beat % 2 === 0) this.noise({ dur: 0.09, gain: 0.06, freq: 5200, q: 1.2 });
    } else if (this.mode === 'combat' && this.beat % 2 === 1) {
      this.noise({ dur: 0.12, gain: 0.05, freq: 160, q: 0.7 });
    } else if (this.mode === 'dungeon' && this.beat % 8 === 1) {
      // 遠くで何かが落ちる音
      this.noise({ dur: 0.5, gain: 0.030, freq: 180, q: 1.6, sweep: 0.4, reverb: 1.0 });
    }

    // ---- 旋律：和音の構成音を優先し、たまに経過音を混ぜる ----
    const density = isBoss ? 0.85 : this.mode === 'combat' ? 0.5
      : this.mode === 'dungeon' ? 0.18 : 0.34;
    if (Math.random() < density) {
      const useChord = Math.random() < 0.66;
      const n = useChord
        ? chord[(Math.random() * 3) | 0] + 12
        : (MODES[rt.mode][(Math.random() * 7) | 0] + base + 12);
      this.tone({
        freq: note(n), dur: spb * (isBoss ? 1.2 : 3.0),
        type: isBoss ? 'triangle' : 'sine',
        gain: isBoss ? 0.07 : 0.05, attack: isBoss ? 0.02 : 0.35,
        dest: this.musicGain, reverb: 0.8,
      });
    }

    // ---- 高音のきらめき：明るい土地ほどよく鳴る ----
    if (isCalm && this.beat % 8 === 3 && Math.random() < 0.35 + rt.bright * 0.6) {
      const n = chord[1 + ((Math.random() * 2) | 0)] + 24;
      this.tone({
        freq: note(n), dur: 2.4, type: 'sine',
        gain: 0.02 + rt.bright * 0.03, attack: 0.5,
        dest: this.musicGain, reverb: 0.9,
      });
    }
    void t;
  }
}
