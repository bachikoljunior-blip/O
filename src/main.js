// main.js — bootstrap: world generation with a loading screen, then the game loop.

import { Game } from './game/game.js';
import { audio } from './core/audio.js';
import { clamp, lerp, TAU, roundRect } from './core/util.js';
import { pickWorldSeed } from './core/seed.js';
import { FONT, COL } from './ui/ui.js';

const canvas = document.getElementById('game');
const game = new Game(canvas);
window.__game = game;
let fatal = false;

function showFatal(error) {
  if (fatal) return;
  if (game.state === 'play') {
    try { game.save(true); } catch (e) {}
  }
  fatal = true;
  console.error(error);
  const splash = document.getElementById('boot');
  const panel = document.getElementById('fatal');
  if (splash) splash.hidden = true;
  if (panel) {
    panel.hidden = false;
    const detail = panel.querySelector('[data-error]');
    if (detail) detail.textContent = '通信状態を確認して「再読み込み」を押してください。';
  }
}

addEventListener('error', (e) => showFatal(e.error || e.message));
addEventListener('unhandledrejection', (e) => showFatal(e.reason));

// ————————————————————————————————————————————————— sizing

let dpr = 1;
let renderScale = 1;          // adaptive: dialled down when frames get expensive
let viewW = 0, viewH = 0;

function resize() {
  viewW = Math.max(320, window.innerWidth);
  viewH = Math.max(320, window.innerHeight);
  const style = getComputedStyle(document.documentElement);
  const cssPx = (name) => Math.max(0, parseFloat(style.getPropertyValue(name)) || 0);
  game.safeArea = {
    top: cssPx('--safe-top'), right: cssPx('--safe-right'),
    bottom: cssPx('--safe-bottom'), left: cssPx('--safe-left'),
  };
  const area = viewW * viewH;
  // keep the backing store sane on huge screens / low-end phones
  const maxDpr = area > 1_300_000 ? 1.5 : 2;
  const base = clamp(window.devicePixelRatio || 1, 1, game.settings.hq ? maxDpr : 1.25);
  dpr = Math.max(0.6, base * renderScale);
  game.renderer.resize(viewW, viewH, dpr);
  game.input.dpr = dpr;
}

// Drop resolution when the device can't keep up, restore it when it can.
const SCALES = [1, 0.86, 0.72, 0.6];
let scaleIdx = 0, slowT = 0, fastT = 0;
function adaptQuality(dt) {
  if (game.state !== 'play') return;
  const fps = game.fps;
  if (fps < 45 && scaleIdx < SCALES.length - 1) {
    slowT += dt; fastT = 0;
    if (slowT > 2.5) { slowT = 0; scaleIdx++; renderScale = SCALES[scaleIdx]; resize(); }
  } else if (fps > 57 && scaleIdx > 0) {
    fastT += dt; slowT = 0;
    if (fastT > 8) { fastT = 0; scaleIdx--; renderScale = SCALES[scaleIdx]; resize(); }
  } else { slowT = Math.max(0, slowT - dt * 0.5); fastT = Math.max(0, fastT - dt * 0.5); }
}
addEventListener('resize', resize);
addEventListener('orientationchange', () => setTimeout(resize, 250));
addEventListener('aetheria-qualitychange', resize);
if (window.visualViewport) window.visualViewport.addEventListener('resize', resize);
resize();

// ————————————————————————————————————————————————— loading screen

const loadState = { phase: '大地を形づくる', t: 0, shown: 0 };

function drawLoading() {
  const ctx = game.renderer.ctx;
  const w = game.renderer.w, h = game.renderer.h;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, '#0b1220');
  g.addColorStop(0.6, '#16182a');
  g.addColorStop(1, '#0a0a12');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);

  const t = performance.now() / 1000;
  // drifting constellation
  ctx.save();
  ctx.globalAlpha = 0.5;
  for (let i = 0; i < 60; i++) {
    const x = ((i * 137.5 + t * 6) % (w + 40)) - 20;
    const y = ((i * 71.3) % (h * 0.8));
    ctx.fillStyle = `rgba(200,220,255,${0.2 + (i % 5) * 0.12})`;
    ctx.beginPath();
    ctx.arc(x, y, 0.6 + (i % 3) * 0.6, 0, TAU);
    ctx.fill();
  }
  ctx.restore();

  ctx.textAlign = 'center';
  ctx.fillStyle = '#e8c874';
  ctx.font = `800 ${Math.min(42, w * 0.10)}px ${FONT}`;
  ctx.fillText('アエテリア戦記', w / 2, h * 0.42);
  ctx.fillStyle = 'rgba(230,222,205,0.75)';
  ctx.font = `600 14px ${FONT}`;
  ctx.fillText(loadState.phase + ' …', w / 2, h * 0.5);

  loadState.shown = lerp(loadState.shown, loadState.t, 0.12);
  const bw = Math.min(320, w - 80), bx = (w - bw) / 2, by = h * 0.55;
  ctx.fillStyle = 'rgba(255,255,255,0.12)';
  roundRect(ctx, bx, by, bw, 8, 4); ctx.fill();
  ctx.fillStyle = '#e8c874';
  roundRect(ctx, bx, by, bw * clamp(loadState.shown, 0, 1), 8, 4); ctx.fill();

  ctx.fillStyle = 'rgba(220,210,190,0.4)';
  ctx.font = `500 12px ${FONT}`;
  ctx.fillText('広大な大陸を生成しています', w / 2, h * 0.62);
}

// ————————————————————————————————————————————————— boot

let gen = null;

function startGeneration() {
  try { gen = game.generate(pickWorldSeed(location.search)); }
  catch (e) { showFatal(e); }
}
startGeneration();

function stepGeneration(budgetMs = 22) {
  const t0 = performance.now();
  while (performance.now() - t0 < budgetMs) {
    let r;
    try { r = gen.next(); }
    catch (e) { showFatal(e); gen = null; return true; }
    if (r.done) {
      game.state = 'title';
      game.menus.open('title');
      const status = document.getElementById('a11y-status');
      if (status) status.textContent = 'ゲームの準備ができました。';
      return true;
    }
    if (r.value) {
      loadState.phase = r.value.phase || loadState.phase;
      loadState.t = r.value.t ?? loadState.t;
    }
  }
  return false;
}

// ————————————————————————————————————————————————— loop

let last = performance.now();
let acc = 0, frames = 0;
addEventListener('pageshow', () => { last = performance.now(); resize(); });

function frame(now) {
  requestAnimationFrame(frame);
  if (fatal) return;
  let dt = (now - last) / 1000;
  last = now;
  if (dt > 0.1) dt = 0.1;

  acc += dt; frames++;
  if (acc > 0.5) { game.fps = frames / acc; acc = 0; frames = 0; }

  if (game.state === 'loading') {
    if (gen) stepGeneration();
    drawLoading();
    game.input.endFrame();
    return;
  }

  adaptQuality(dt);
  game.update(dt);
  game.render(dt);
  game.input.endFrame();
}
requestAnimationFrame(frame);

// ————————————————————————————————————————————————— platform glue

// unlock audio on the first interaction
const unlock = () => {
  audio.init();
  audio.setMusicVol(game.settings.music);
  audio.setSfxVol(game.settings.sfx);
};
['pointerdown', 'keydown', 'touchstart'].forEach((ev) =>
  addEventListener(ev, unlock, { once: true, passive: true }));

// keep the screen awake on mobile where supported
let wakeLock = null;
async function requestWake() {
  try {
    if (!('wakeLock' in navigator) || wakeLock || document.visibilityState !== 'visible') return;
    const lock = await navigator.wakeLock.request('screen');
    wakeLock = lock;
    lock.addEventListener('release', () => {
      if (wakeLock === lock) wakeLock = null;
    }, { once: true });
  } catch (e) {}
}
async function releaseWake() {
  const lock = wakeLock;
  wakeLock = null;
  try { await lock?.release(); } catch (e) {}
}
addEventListener('pointerdown', requestWake, { once: true });
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') requestWake();
  else {
    releaseWake();
    game.input.cancelActive();
    if (game.state === 'play') {
      game.pauseForInterruption();
      game.save(true);
    }
  }
});
addEventListener('pagehide', () => {
  releaseWake();
  game.input.cancelActive();
  if (game.state === 'play') {
    game.pauseForInterruption();
    game.save(true);
  }
});
document.querySelector('#fatal button')?.addEventListener('click', () => location.reload());

// Installable and offline after the first successful visit.
if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) {
  addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js', { scope: './' })
      .then((registration) => {
        registration.update().catch(() => {});
        registration.addEventListener('updatefound', () => {
          const worker = registration.installing;
          worker?.addEventListener('statechange', () => {
            if (worker.state === 'installed' && navigator.serviceWorker.controller && game.state === 'play') {
              game.toast('更新を取得しました。次の起動で反映します', '#8fd0ff');
            }
          });
        });
      })
      .catch(() => {});
  }, { once: true });
}

// block browser gestures that fight with the controls
document.addEventListener('gesturestart', (e) => e.preventDefault());
document.addEventListener('dblclick', (e) => e.preventDefault(), { passive: false });
document.body.addEventListener('touchmove', (e) => { if (e.cancelable) e.preventDefault(); }, { passive: false });

// remove the boot splash
const splash = document.getElementById('boot');
if (splash) splash.remove();
