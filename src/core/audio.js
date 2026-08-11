// audio.js — fully procedural WebAudio: adaptive score + synthesized SFX.

import { makeRNG, clamp, lerp } from './util.js';

const NOTE = (n) => 440 * Math.pow(2, (n - 69) / 12);
const MUSIC_REVERB_SEND = 0.5;
const SFX_REVERB_SEND = 0.22;

// scale degrees (semitones) for the modes we use
const MODES = {
  aeolian: [0, 2, 3, 5, 7, 8, 10],
  dorian: [0, 2, 3, 5, 7, 9, 10],
  ionian: [0, 2, 4, 5, 7, 9, 11],
  lydian: [0, 2, 4, 6, 7, 9, 11],
  phrygian: [0, 1, 3, 5, 7, 8, 10],
};

const MOODS = {
  explore: { root: 57, mode: 'dorian', bpm: 74, prog: [0, 5, 3, 4], pad: 0.16, mel: 0.5, perc: 0 },
  town: { root: 60, mode: 'ionian', bpm: 96, prog: [0, 3, 4, 0], pad: 0.14, mel: 0.75, perc: 0.2 },
  combat: { root: 52, mode: 'phrygian', bpm: 138, prog: [0, 0, 5, 6], pad: 0.13, mel: 0.55, perc: 0.9 },
  night: { root: 55, mode: 'aeolian', bpm: 62, prog: [0, 6, 3, 5], pad: 0.2, mel: 0.32, perc: 0 },
  dungeon: { root: 48, mode: 'phrygian', bpm: 68, prog: [0, 1, 0, 6], pad: 0.22, mel: 0.2, perc: 0.15 },
  boss: { root: 50, mode: 'phrygian', bpm: 152, prog: [0, 6, 5, 6], pad: 0.16, mel: 0.7, perc: 1 },
  peace: { root: 62, mode: 'lydian', bpm: 66, prog: [0, 4, 5, 3], pad: 0.18, mel: 0.6, perc: 0 },
};

export class Audio {
  constructor() {
    this.ctx = null;
    this.ready = false;
    this.musicVol = 0.55;
    this.sfxVol = 0.8;
    this.muted = false;
    this.mood = 'explore';
    this.nextMood = 'explore';
    this.rng = makeRNG(20260802);
    this.step = 0;
    this.nextTime = 0;
    this._lastFoot = 0;
  }

  init() {
    if (this.ctx) {
      if (this.ctx.state === 'suspended') {
        // Mobile browsers may suspend Web Audio when the app is backgrounded.
        // Resume on the next interaction and discard the elapsed sequencer gap.
        this.nextTime = this.ctx.currentTime + 0.05;
        const resumed = this.ctx.resume();
        resumed?.catch?.(() => {});
      }
      return;
    }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    const ctx = new AC();
    this.ctx = ctx;
    this.master = ctx.createGain();
    this.master.gain.value = this.muted ? 0 : 0.9;
    this.master.connect(ctx.destination);

    // shared reverb
    this.reverb = ctx.createConvolver();
    const len = ctx.sampleRate * 2.4;
    const buf = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < len; i++) {
        d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2.6) * 0.55;
      }
    }
    this.reverb.buffer = buf;
    this.revGain = ctx.createGain();
    this.revGain.gain.value = 0.32;
    this.reverb.connect(this.revGain);
    this.revGain.connect(this.master);

    this.musicGain = ctx.createGain();
    this.musicGain.gain.value = this.musicVol;
    this.musicGain.connect(this.master);
    this.musicSend = ctx.createGain();
    // Reverb sends bypass the dry category gains, so scale them by the same
    // user setting. Otherwise a 0% slider still leaves an audible wet signal.
    this.musicSend.gain.value = MUSIC_REVERB_SEND * this.musicVol;
    this.musicSend.connect(this.reverb);

    this.sfxGain = ctx.createGain();
    this.sfxGain.gain.value = this.sfxVol;
    this.sfxGain.connect(this.master);
    this.sfxSend = ctx.createGain();
    this.sfxSend.gain.value = SFX_REVERB_SEND * this.sfxVol;
    this.sfxSend.connect(this.reverb);

    // noise buffer for percussive sfx
    const nlen = ctx.sampleRate * 1.2;
    this.noise = ctx.createBuffer(1, nlen, ctx.sampleRate);
    const nd = this.noise.getChannelData(0);
    for (let i = 0; i < nlen; i++) nd[i] = Math.random() * 2 - 1;

    this.ready = true;
    this.nextTime = ctx.currentTime + 0.1;
  }

  setMuted(m) {
    const wasMuted = this.muted;
    this.muted = !!m;
    if (this.master) this.master.gain.setTargetAtTime(this.muted ? 0 : 0.9, this.ctx.currentTime, 0.05);
    // Do not catch up every beat that elapsed while muted. Resuming from the
    // current audio clock avoids a burst of stale scheduling and CPU work.
    if (wasMuted && !this.muted && this.ctx) this.nextTime = this.ctx.currentTime + 0.05;
  }
  setMusicVol(v) {
    const wasSilent = this.musicVol <= 0.0001;
    this.musicVol = clamp(Number(v) || 0, 0, 1);
    if (this.musicGain) this.musicGain.gain.setTargetAtTime(this.musicVol, this.ctx.currentTime, 0.1);
    if (this.musicSend) this.musicSend.gain.setTargetAtTime(MUSIC_REVERB_SEND * this.musicVol, this.ctx.currentTime, 0.1);
    if (wasSilent && this.musicVol > 0.0001 && this.ctx) this.nextTime = this.ctx.currentTime + 0.05;
  }
  setSfxVol(v) {
    this.sfxVol = clamp(Number(v) || 0, 0, 1);
    if (this.sfxGain) this.sfxGain.gain.setTargetAtTime(this.sfxVol, this.ctx.currentTime, 0.05);
    if (this.sfxSend) this.sfxSend.gain.setTargetAtTime(SFX_REVERB_SEND * this.sfxVol, this.ctx.currentTime, 0.05);
  }

  setMood(m) {
    if (MOODS[m]) this.nextMood = m;
  }

  // ————————————————————————————————————————— music scheduler

  update() {
    if (!this.ready || this.muted) return;
    const ctx = this.ctx;
    if (ctx.state === 'suspended') return;
    if (this.musicVol <= 0.0001) {
      // Keep the sequencer clock current without constructing inaudible Web
      // Audio nodes every beat. This matters for battery and thermals on phones.
      this.nextTime = ctx.currentTime + 0.1;
      return;
    }
    const mood = MOODS[this.mood] || MOODS.explore;
    const spb = 60 / mood.bpm;
    const stepDur = spb / 2;              // eighth notes
    // requestAnimationFrame stops while a tab is hidden, while the audio clock
    // can continue. Never synthesize the missed beats in a burst on return.
    if (this.nextTime < ctx.currentTime - stepDur) this.nextTime = ctx.currentTime + 0.05;
    let guard = 0;
    while (this.nextTime < ctx.currentTime + 0.25 && guard++ < 32) {
      this._schedule(this.nextTime, mood);
      this.nextTime += stepDur;
      this.step++;
      if (this.step % 32 === 0 && this.mood !== this.nextMood) this.mood = this.nextMood;
    }
  }

  _schedule(t, mood) {
    const scale = MODES[mood.mode];
    const bar = Math.floor(this.step / 8) % mood.prog.length;
    const deg = mood.prog[bar];
    const rootN = mood.root + scale[deg % 7] + (deg >= 7 ? 12 : 0);
    const s = this.step % 8;

    // ——— pad (once per bar)
    if (s === 0) {
      const chord = [0, 2, 4].map((i) => mood.root + scale[(deg + i) % 7] + (deg + i >= 7 ? 12 : 0));
      for (const n of chord) {
        this._tone({
          freq: NOTE(n + 12), type: 'triangle', t, dur: (60 / mood.bpm) * 4.2,
          a: 0.9, d: 1.4, gain: mood.pad, detune: this.rng() * 8 - 4, send: 0.7, filter: 1400,
        });
        this._tone({
          freq: NOTE(n), type: 'sine', t, dur: (60 / mood.bpm) * 4.2,
          a: 1.1, d: 1.4, gain: mood.pad * 0.7, send: 0.6, filter: 900,
        });
      }
    }
    // ——— bass
    if (s % 2 === 0) {
      this._tone({
        freq: NOTE(rootN - 12), type: 'sine', t, dur: 0.42,
        a: 0.01, d: 0.34, gain: 0.24, filter: 420, send: 0.1,
      });
    }
    // ——— melody
    if (this.rng() < mood.mel && s % 2 === 1) {
      const oct = this.rng() < 0.25 ? 24 : 12;
      const n = mood.root + scale[(deg + Math.floor(this.rng() * 5)) % 7] + oct;
      this._tone({
        freq: NOTE(n), type: 'triangle', t: t + this.rng() * 0.01, dur: 0.5,
        a: 0.012, d: 0.42, gain: 0.13, send: 0.6, filter: 3200,
      });
    }
    // ——— percussion
    if (mood.perc > 0) {
      if (s % 4 === 0) this._noise({ t, dur: 0.14, gain: 0.22 * mood.perc, filter: 220, type: 'lowpass' });
      if (s % 4 === 2) this._noise({ t, dur: 0.10, gain: 0.13 * mood.perc, filter: 2600, type: 'highpass' });
      if (mood.perc > 0.7 && s % 8 === 7) this._noise({ t, dur: 0.18, gain: 0.16, filter: 1800, type: 'highpass' });
    }
  }

  _tone({ freq, type = 'sine', t, dur = 0.3, a = 0.005, d = 0.2, gain = 0.2, detune = 0, send = 0.2, filter = 0, bend = 0, target = null }) {
    const ctx = this.ctx;
    const o = ctx.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    if (bend) o.frequency.exponentialRampToValueAtTime(Math.max(20, freq * bend), t + dur);
    o.detune.value = detune;
    let node = o;
    if (filter) {
      const f = ctx.createBiquadFilter();
      f.type = 'lowpass';
      f.frequency.value = filter;
      f.Q.value = 0.7;
      o.connect(f);
      node = f;
    }
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), t + a);
    g.gain.exponentialRampToValueAtTime(0.0001, t + a + d);
    node.connect(g);
    const dst = target || this.musicGain;
    g.connect(dst);
    const sendNode = target ? this.sfxSend : this.musicSend;
    if (send > 0) {
      const sg = ctx.createGain();
      sg.gain.value = send;
      g.connect(sg);
      sg.connect(sendNode);
    }
    o.start(t);
    o.stop(t + a + d + 0.05);
  }

  _noise({ t, dur = 0.2, gain = 0.2, filter = 1000, type = 'lowpass', target = null, Q = 1, sweep = 0 }) {
    const ctx = this.ctx;
    const src = ctx.createBufferSource();
    src.buffer = this.noise;
    src.playbackRate.value = 0.8 + Math.random() * 0.5;
    const f = ctx.createBiquadFilter();
    f.type = type;
    f.frequency.setValueAtTime(filter, t);
    if (sweep) f.frequency.exponentialRampToValueAtTime(Math.max(60, filter * sweep), t + dur);
    f.Q.value = Q;
    const g = ctx.createGain();
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(f); f.connect(g);
    g.connect(target || this.musicGain);
    src.start(t);
    src.stop(t + dur + 0.02);
  }

  // ————————————————————————————————————————— SFX

  sfx(name, opt = {}) {
    if (!this.ready || this.muted || this.sfxVol <= 0.0001) return;
    const ctx = this.ctx;
    const t = ctx.currentTime + 0.001;
    const out = this.sfxGain;
    const v = opt.vol ?? 1;
    switch (name) {
      case 'swing':
        this._noise({ t, dur: 0.16, gain: 0.18 * v, filter: 1800, type: 'bandpass', target: out, Q: 1.4, sweep: 2.6 });
        break;
      case 'hit':
        this._noise({ t, dur: 0.12, gain: 0.34 * v, filter: 900, type: 'lowpass', target: out, sweep: 0.3 });
        this._tone({ freq: 180, type: 'square', t, dur: 0.1, a: 0.002, d: 0.09, gain: 0.16 * v, target: out, bend: 0.5, send: 0.1 });
        break;
      case 'crit':
        this._noise({ t, dur: 0.18, gain: 0.4 * v, filter: 1400, type: 'bandpass', target: out, Q: 2, sweep: 0.4 });
        this._tone({ freq: 660, type: 'square', t, dur: 0.16, a: 0.002, d: 0.14, gain: 0.16 * v, target: out, bend: 0.4, send: 0.3 });
        break;
      case 'hurt':
        this._tone({ freq: 300, type: 'sawtooth', t, dur: 0.24, a: 0.004, d: 0.2, gain: 0.2 * v, target: out, bend: 0.35, send: 0.2, filter: 1200 });
        break;
      case 'enemyHurt':
        this._tone({ freq: 220, type: 'square', t, dur: 0.14, a: 0.003, d: 0.12, gain: 0.12 * v, target: out, bend: 0.6, filter: 1600, send: 0.1 });
        break;
      case 'die':
        this._tone({ freq: 420, type: 'sawtooth', t, dur: 0.6, a: 0.01, d: 0.55, gain: 0.2 * v, target: out, bend: 0.18, filter: 1400, send: 0.4 });
        this._noise({ t: t + 0.02, dur: 0.4, gain: 0.16 * v, filter: 800, type: 'lowpass', target: out, sweep: 0.2 });
        break;
      case 'step': {
        const now = performance.now();
        if (now - this._lastFoot < 190) return;
        this._lastFoot = now;
        this._noise({ t, dur: 0.07, gain: 0.075 * v, filter: opt.water ? 900 : 420, type: 'lowpass', target: out, sweep: 0.5 });
        break;
      }
      case 'pickup':
        this._tone({ freq: 880, type: 'triangle', t, dur: 0.1, a: 0.004, d: 0.09, gain: 0.14 * v, target: out, send: 0.3 });
        this._tone({ freq: 1320, type: 'triangle', t: t + 0.06, dur: 0.12, a: 0.004, d: 0.1, gain: 0.12 * v, target: out, send: 0.3 });
        break;
      case 'gold':
        for (let i = 0; i < 3; i++)
          this._tone({ freq: 1200 + i * 340, type: 'triangle', t: t + i * 0.035, dur: 0.1, a: 0.002, d: 0.09, gain: 0.09 * v, target: out, send: 0.4 });
        break;
      case 'levelup':
        [0, 4, 7, 12, 16].forEach((n, i) =>
          this._tone({ freq: NOTE(72 + n), type: 'triangle', t: t + i * 0.09, dur: 0.5, a: 0.01, d: 0.45, gain: 0.16 * v, target: out, send: 0.7 }));
        break;
      case 'quest':
        [0, 5, 9].forEach((n, i) =>
          this._tone({ freq: NOTE(69 + n), type: 'sine', t: t + i * 0.12, dur: 0.6, a: 0.02, d: 0.5, gain: 0.14 * v, target: out, send: 0.8 }));
        break;
      case 'ui':
        this._tone({ freq: 640, type: 'sine', t, dur: 0.06, a: 0.002, d: 0.05, gain: 0.09 * v, target: out, send: 0.1 });
        break;
      case 'uiBig':
        this._tone({ freq: 320, type: 'triangle', t, dur: 0.14, a: 0.003, d: 0.12, gain: 0.12 * v, target: out, send: 0.2 });
        break;
      case 'error':
        this._tone({ freq: 180, type: 'square', t, dur: 0.16, a: 0.003, d: 0.14, gain: 0.11 * v, target: out, filter: 900 });
        break;
      case 'fire':
        this._noise({ t, dur: 0.5, gain: 0.24 * v, filter: 700, type: 'lowpass', target: out, sweep: 0.35 });
        this._tone({ freq: 140, type: 'sawtooth', t, dur: 0.4, a: 0.01, d: 0.36, gain: 0.14 * v, target: out, bend: 0.5, filter: 800, send: 0.4 });
        break;
      case 'ice':
        this._tone({ freq: 1600, type: 'triangle', t, dur: 0.3, a: 0.004, d: 0.28, gain: 0.12 * v, target: out, bend: 0.55, send: 0.6 });
        this._noise({ t, dur: 0.25, gain: 0.12 * v, filter: 4200, type: 'highpass', target: out });
        break;
      case 'bolt':
        this._noise({ t, dur: 0.3, gain: 0.3 * v, filter: 3000, type: 'highpass', target: out, sweep: 0.2 });
        this._tone({ freq: 90, type: 'square', t, dur: 0.24, a: 0.002, d: 0.22, gain: 0.16 * v, target: out, bend: 0.4, send: 0.5 });
        break;
      case 'heal':
        [0, 7, 12].forEach((n, i) =>
          this._tone({ freq: NOTE(76 + n), type: 'sine', t: t + i * 0.07, dur: 0.5, a: 0.02, d: 0.44, gain: 0.12 * v, target: out, send: 0.8 }));
        break;
      case 'bow':
        this._noise({ t, dur: 0.12, gain: 0.2 * v, filter: 2400, type: 'bandpass', target: out, Q: 2, sweep: 1.8 });
        break;
      case 'arrow':
        this._noise({ t, dur: 0.08, gain: 0.14 * v, filter: 3200, type: 'bandpass', target: out, Q: 3 });
        break;
      case 'block':
        this._noise({ t, dur: 0.1, gain: 0.3 * v, filter: 2600, type: 'bandpass', target: out, Q: 3, sweep: 0.5 });
        this._tone({ freq: 520, type: 'square', t, dur: 0.12, a: 0.002, d: 0.1, gain: 0.1 * v, target: out, bend: 0.7, send: 0.4 });
        break;
      case 'parry':
        this._tone({ freq: 1400, type: 'triangle', t, dur: 0.3, a: 0.002, d: 0.28, gain: 0.16 * v, target: out, bend: 0.6, send: 0.8 });
        break;
      case 'dodge':
        this._noise({ t, dur: 0.2, gain: 0.1 * v, filter: 1200, type: 'bandpass', target: out, Q: 1, sweep: 2.4 });
        break;
      case 'drink':
        this._tone({ freq: 300, type: 'sine', t, dur: 0.3, a: 0.02, d: 0.26, gain: 0.12 * v, target: out, bend: 1.6, send: 0.3 });
        break;
      case 'chest':
        this._noise({ t, dur: 0.3, gain: 0.16 * v, filter: 1200, type: 'lowpass', target: out, sweep: 0.4 });
        [0, 4, 7, 11].forEach((n, i) =>
          this._tone({ freq: NOTE(72 + n), type: 'triangle', t: t + 0.12 + i * 0.06, dur: 0.4, a: 0.01, d: 0.34, gain: 0.11 * v, target: out, send: 0.7 }));
        break;
      case 'warp':
        this._tone({ freq: 200, type: 'sine', t, dur: 1.0, a: 0.2, d: 0.8, gain: 0.18 * v, target: out, bend: 6, send: 0.9, filter: 3000 });
        break;
      case 'roar':
        this._tone({ freq: 70, type: 'sawtooth', t, dur: 1.2, a: 0.08, d: 1.1, gain: 0.3 * v, target: out, bend: 1.6, filter: 600, send: 0.7 });
        this._noise({ t, dur: 1.0, gain: 0.22 * v, filter: 500, type: 'lowpass', target: out, sweep: 1.6 });
        break;
      case 'craft':
        this._noise({ t, dur: 0.2, gain: 0.18 * v, filter: 1600, type: 'bandpass', target: out, Q: 1.5, sweep: 0.6 });
        this._tone({ freq: 900, type: 'triangle', t: t + 0.1, dur: 0.3, a: 0.01, d: 0.26, gain: 0.12 * v, target: out, send: 0.6 });
        break;
      case 'gate':
        this._noise({ t, dur: 0.7, gain: 0.2 * v, filter: 500, type: 'lowpass', target: out, sweep: 0.5 });
        break;
    }
  }
}

export const audio = new Audio();
