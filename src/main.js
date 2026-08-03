// エントリポイント

import { Game } from './game/game.js';
import { UI } from './ui/ui.js';

const canvas = document.getElementById('view');

function fatal(msg) {
  const l = document.getElementById('loading');
  l.innerHTML = `<div class="logo"><div class="jp">起動できません</div></div>
    <div style="max-width:520px;text-align:center;line-height:1.9;font-size:13px;opacity:.75">${msg}</div>`;
  l.classList.remove('hidden');
}

async function boot() {
  const ui = new UI();
  let game;
  try {
    game = new Game(canvas, ui);
  } catch (e) {
    console.error(e);
    fatal(`このブラウザは WebGL2 に対応していません。<br>
      iOS は Safari 15 以降、Android は Chrome 90 以降でお試しください。<br><br>
      <span style="opacity:.5">${e.message}</span>`);
    return;
  }

  try {
    await game.init();
  } catch (e) {
    console.error(e);
    fatal(`初期化中にエラーが発生しました。<br><span style="opacity:.5">${e.message}</span>`);
    return;
  }

  // 保存された品質設定を反映
  if (ui.settings.quality && ui.settings.quality !== 'medium') {
    game.setQuality(ui.settings.quality);
  } else {
    game.quality.preset = 'medium';
  }
  ui.applySettings();

  const startAudio = () => {
    game.audio.resume();
    ui.applySettings();
  };
  addEventListener('pointerdown', startAudio, { once: true });
  addEventListener('touchstart', startAudio, { once: true });
  addEventListener('keydown', startAudio, { once: true });

  ui.showTitle(Game.hasSave(), {
    new: () => {
      Game.clearSave();
      game.start();
      setTimeout(() => {
        ui.toast('灰かぶりの村を目指せ', 'big gold');
        ui.showRegion(game.world.regionAt(game.player.x, game.player.z));
      }, 700);
    },
    continue: () => {
      game.load();
      game.start();
      setTimeout(() => ui.showRegion(game.world.regionAt(game.player.x, game.player.z)), 500);
    },
  });

  // 画面を離れたら自動で一時停止
  document.addEventListener('visibilitychange', () => {
    if (!game.running) return;
    game.paused = document.hidden;
    if (document.hidden) game.save();
  });
  addEventListener('beforeunload', () => { if (game.running) game.save(); });

  window.__game = game;   // デバッグ用
}

// 真偽で見る。'in' で見ると、配布版で navigator.serviceWorker を消しても
// 判定だけ通ってしまい、undefined.register で毎回落ちる
if (navigator.serviceWorker && location.protocol.startsWith('http')) {
  addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => { /* オフライン非対応でも動作する */ });
  });
}

boot();
