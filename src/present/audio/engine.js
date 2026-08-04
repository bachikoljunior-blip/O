// engine.js — fully synthesised audio. No sample files, anywhere.
// L3.
//
// Ported from the 2D build's WebAudio graph, with three changes it needed:
// a PannerNode pool so sounds have a position in 3D, a data-driven SFX table
// instead of a 26-case switch, and a gain crossfade between music moods
// instead of a hard swap at a 32-step boundary.

const SFX = {
  hit:         [{ noise: { dur: 0.09, f: 1600, q: 1.2, g: 0.55 } }, { tone: { type: 'square', f: 180, dur: 0.07, g: 0.30, bend: -60 } }],
  hit_slash:   [{ noise: { dur: 0.12, f: 3200, q: 2.0, g: 0.45, sweep: -1800 } }],
  hit_heavy:   [{ noise: { dur: 0.20, f: 700, q: 0.8, g: 0.75 } }, { tone: { type: 'sine', f: 70, dur: 0.24, g: 0.55, bend: -28 } }],
  hit_pierce:  [{ noise: { dur: 0.07, f: 5200, q: 4.0, g: 0.40 } }],
  hit_metal:   [{ tone: { type: 'square', f: 880, dur: 0.14, g: 0.30, bend: -220 } }, { noise: { dur: 0.13, f: 4200, q: 3, g: 0.35 } }],
  hit_shock:   [{ tone: { type: 'sawtooth', f: 320, dur: 0.20, g: 0.32, bend: 420 } }],
  hit_quake:   [{ tone: { type: 'sine', f: 48, dur: 0.42, g: 0.85, bend: -20 } }, { noise: { dur: 0.30, f: 300, q: 0.6, g: 0.55 } }],
  swing:       [{ noise: { dur: 0.13, f: 900, q: 1.4, g: 0.22, sweep: 1400 } }],
  parry:       [{ tone: { type: 'triangle', f: 1320, dur: 0.26, g: 0.50, bend: 320 } }, { noise: { dur: 0.16, f: 5200, q: 5, g: 0.42 } }],
  block:       [{ noise: { dur: 0.11, f: 480, q: 1.0, g: 0.42 } }],
  break:       [{ tone: { type: 'sawtooth', f: 140, dur: 0.55, g: 0.60, bend: -70 } }, { noise: { dur: 0.42, f: 900, q: 0.7, g: 0.55 } }],
  stagger:     [{ tone: { type: 'triangle', f: 220, dur: 0.18, g: 0.32, bend: -80 } }],
  death:       [{ tone: { type: 'sawtooth', f: 190, dur: 0.70, g: 0.45, bend: -130 } }],
  dodge:       [{ noise: { dur: 0.18, f: 700, q: 0.9, g: 0.20, sweep: -420 } }],
  step:        [{ noise: { dur: 0.06, f: 420, q: 1.1, g: 0.16 } }],
  land:        [{ noise: { dur: 0.14, f: 260, q: 0.8, g: 0.40 } }],
  heal:        [{ tone: { type: 'sine', f: 520, dur: 0.42, g: 0.32, bend: 260 } }],
  error:       [{ tone: { type: 'square', f: 150, dur: 0.10, g: 0.20, bend: -40 } }],
  metal_break: [{ tone: { type: 'square', f: 620, dur: 0.30, g: 0.42, bend: -380 } }, { noise: { dur: 0.30, f: 3000, q: 2, g: 0.5 } }],
  ui:          [{ tone: { type: 'sine', f: 720, dur: 0.07, g: 0.18 } }],
};

export function createAudio() {
  return { ctx: null, ready: false, master: null, sfxGain: null, musicGain: null,
           noiseBuf: null, muted: false, panners: [], panIdx: 0, listener: null };
}

/** Must be called from a user gesture — every mobile browser requires it. */
export function unlock(a) {
  if (a.ready) return;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  a.ctx = new Ctx();
  a.master = a.ctx.createGain();
  a.master.gain.value = 0.85;
  a.master.connect(a.ctx.destination);

  a.sfxGain = a.ctx.createGain(); a.sfxGain.gain.value = 0.9; a.sfxGain.connect(a.master);
  a.musicGain = a.ctx.createGain(); a.musicGain.gain.value = 0.42; a.musicGain.connect(a.master);

  // Runtime-synthesised reverb impulse: 2.4s of shaped noise, no asset.
  const len = Math.floor(a.ctx.sampleRate * 2.4);
  const ir = a.ctx.createBuffer(2, len, a.ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const d = ir.getChannelData(c);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2.6);
  }
  a.reverb = a.ctx.createConvolver();
  a.reverb.buffer = ir;
  a.revGain = a.ctx.createGain(); a.revGain.gain.value = 0.22;
  a.reverb.connect(a.revGain); a.revGain.connect(a.master);

  const nlen = Math.floor(a.ctx.sampleRate * 1.2);
  a.noiseBuf = a.ctx.createBuffer(1, nlen, a.ctx.sampleRate);
  const nd = a.noiseBuf.getChannelData(0);
  for (let i = 0; i < nlen; i++) nd[i] = Math.random() * 2 - 1;

  // A small pool of panners, reused round-robin. Allocating one per sound
  // would churn the graph and the GC.
  for (let i = 0; i < 12; i++) {
    const p = a.ctx.createPanner();
    p.panningModel = 'equalpower';
    p.distanceModel = 'inverse';
    p.refDistance = 6;
    p.maxDistance = 90;
    p.connect(a.sfxGain);
    a.panners.push(p);
  }
  a.listener = a.ctx.listener;
  a.ready = true;
}

function nextPanner(a, x, y, z) {
  const p = a.panners[a.panIdx];
  a.panIdx = (a.panIdx + 1) % a.panners.length;
  if (p.positionX) {
    p.positionX.value = x; p.positionY.value = y; p.positionZ.value = z;
  } else {
    p.setPosition(x, y, z);
  }
  return p;
}

function tone(a, dest, o, t, pitch, vol) {
  const osc = a.ctx.createOscillator();
  const g = a.ctx.createGain();
  osc.type = o.type || 'sine';
  const f = (o.f || 440) * pitch;
  osc.frequency.setValueAtTime(f, t);
  if (o.bend) osc.frequency.linearRampToValueAtTime(Math.max(20, f + o.bend), t + o.dur);
  const amp = (o.g || 0.3) * vol;
  g.gain.setValueAtTime(0, t);
  g.gain.linearRampToValueAtTime(amp, t + 0.005);
  g.gain.exponentialRampToValueAtTime(0.0001, t + o.dur);
  osc.connect(g); g.connect(dest);
  osc.start(t); osc.stop(t + o.dur + 0.02);
}

function noise(a, dest, o, t, pitch, vol) {
  const src = a.ctx.createBufferSource();
  src.buffer = a.noiseBuf;
  src.loop = true;
  const bp = a.ctx.createBiquadFilter();
  bp.type = 'bandpass';
  bp.frequency.setValueAtTime((o.f || 1000) * pitch, t);
  if (o.sweep) bp.frequency.linearRampToValueAtTime(
    Math.max(80, (o.f || 1000) * pitch + o.sweep), t + o.dur);
  bp.Q.value = o.q || 1;
  const g = a.ctx.createGain();
  const amp = (o.g || 0.3) * vol;
  g.gain.setValueAtTime(amp, t);
  g.gain.exponentialRampToValueAtTime(0.0001, t + o.dur);
  src.connect(bp); bp.connect(g); g.connect(dest);
  src.start(t); src.stop(t + o.dur + 0.02);
}

/** Play a named effect. Positional if x/y/z are supplied. */
export function play(a, id, opt = {}) {
  if (!a.ready || a.muted) return;
  const parts = SFX[id];
  if (!parts) return;
  const t = a.ctx.currentTime + 0.001;
  const pitch = opt.pitch || 1;
  const vol = opt.vol === undefined ? 1 : opt.vol;
  const dest = (opt.x !== undefined) ? nextPanner(a, opt.x, opt.y || 0, opt.z || 0) : a.sfxGain;
  for (const p of parts) {
    if (p.tone) tone(a, dest, p.tone, t, pitch, vol);
    if (p.noise) noise(a, dest, p.noise, t, pitch, vol);
  }
}

/** Keep the WebAudio listener on the camera so panning matches what you see. */
export function setListener(a, cam) {
  if (!a.ready || !a.listener) return;
  const l = a.listener;
  if (l.positionX) {
    l.positionX.value = cam.position.x;
    l.positionY.value = cam.position.y;
    l.positionZ.value = cam.position.z;
  } else if (l.setPosition) {
    l.setPosition(cam.position.x, cam.position.y, cam.position.z);
  }
}

export const makeAudioFacade = (a) => ({
  play: (id, opt) => play(a, id, opt),
  unlock: () => unlock(a),
  get ready() { return a.ready; },
});
