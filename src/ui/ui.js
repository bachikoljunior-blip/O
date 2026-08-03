// UI：HUD / メニュー / 会話 / マップ / ショップ / 篝火

import {
  WEAPONS, SHIELDS, ARMORS, TALISMANS, SPELLS, ITEMS, UPGRADE, RECIPES, SHOP, ARTS,
  STAT_KEYS, STAT_NAMES, STAT_DESC, levelCost, scalingFactor, SCALE,
} from '../game/data.js';
import { QUESTS } from '../game/quests.js';
import { REGIONS, WORLD_RADIUS } from '../world/worldgen.js';
import { clamp, clamp01, lerp } from '../core/math.js';

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};

const MAP_SIZE = 180;
const WORLD_SPAN = WORLD_RADIUS * 2 + 200;

export class UI {
  constructor() {
    this.hud = $('#hud');
    this.overlay = $('#overlay');
    this.dialogue = $('#dialogue');
    this.deathEl = $('#death');
    this.loading = $('#loading');
    this.title = $('#title');
    this.promptEl = $('#prompt');
    this.interactBtn = $('#interactbtn');
    this.toasts = $('#toasts');
    this.dmgnums = $('#dmgnums');
    this.bossbar = $('#bossbar');
    this.bossintro = $('#bossintro');
    this.aimdot = $('#aimdot');
    this._aimW = [0, 0, 0];
    this._aimS = [0, 0];
    this.minimap = $('#minimap');
    this.mctx = this.minimap.getContext('2d');
    this.compass = $('#compass');
    this.cctx = this.compass ? this.compass.getContext('2d') : null;
    this.targetbar = $('#targetbar');
    this.stick = $('#stick');

    this.game = null;
    this.tab = 'status';
    this.overlayOpen = false;
    this.dialogueOpen = false;
    this.hpLag = 1; this.bossLag = 1;
    this.dmgPool = [];
    this.mapBaked = 0;
    this.mapCanvas = document.createElement('canvas');
    this.mapCanvas.width = MAP_SIZE; this.mapCanvas.height = MAP_SIZE;
    this.mapImg = this.mapCanvas.getContext('2d').createImageData(MAP_SIZE, MAP_SIZE);
    this.mapRow = 0;
    this.settings = {
      quality: 'medium', master: 0.8, sfx: 0.9, music: 0.5,
      sensitivity: 1.0, invertY: false, autoLock: true,
      lefty: false, buttonSize: 'normal', compass: true, damageNumbers: true,
    };
    this.loadSettings();
  }

  get modalOpen() { return this.overlayOpen || this.dialogueOpen || this.deathOpen; }

  /* ------------------------------------------------------- 起動画面 */
  progress(text, t) {
    if (t >= 1) {
      this.loading.classList.add('hidden');
      return;
    }
    $('.pbar i', this.loading).style.width = `${t * 100}%`;
    $('.ptext', this.loading).textContent = text;
  }

  showTitle(hasSave, handlers) {
    this.title.classList.remove('hidden');
    this.loading.classList.add('hidden');
    const cont = $('[data-act="continue"]', this.title);
    cont.classList.toggle('hidden', !hasSave);
    this.title.querySelectorAll('.tmenu button').forEach((b) => {
      b.onclick = () => {
        const act = b.dataset.act;
        if (act === 'settings') this.openSettingsStandalone();
        else if (act === 'howto') this.showHowTo();
        else { this.title.classList.add('hidden'); handlers[act]?.(); }
      };
    });
  }

  showHowTo() {
    this.openOverlay([{ id: 'howto', label: '操作方法' }], 'howto');
  }
  openSettingsStandalone() {
    this.openOverlay([{ id: 'settings', label: '設定' }], 'settings');
  }

  /* ---------------------------------------------------------- bind */
  bind(game) {
    this.game = game;
    this.hud.classList.remove('hidden');

    // タッチボタン
    document.querySelectorAll('[data-btn]').forEach((b) => {
      const name = b.dataset.btn;
      if (name === 'menu') {
        b.addEventListener('click', (e) => { e.preventDefault(); this.toggleMenu('status'); });
        return;
      }
      if (name === 'map') {
        b.addEventListener('click', (e) => { e.preventDefault(); this.toggleMenu('map'); });
        return;
      }
      game.input.bindButton(b, name, b.dataset.hold ? { hold: b.dataset.hold, holdMs: 250 } : {});
    });

    // 仮想スティックの表示
    game.input.onStickShow = (x, y) => {
      this.stick.classList.remove('hidden');
      this.stick.style.left = `${x}px`;
      this.stick.style.top = `${y}px`;
      $('i', this.stick).style.transform = 'translate(0,0)';
    };
    game.input.onStickMove = (dx, dy) => {
      $('i', this.stick).style.transform = `translate(${dx}px, ${dy}px)`;
    };
    game.input.onStickHide = () => this.stick.classList.add('hidden');

    this.applySettings();
  }

  /* ---------------------------------------------------------- 更新 */
  update(dt, game) {
    const p = game.player;

    // 入力によるメニュー開閉
    if (game.input.pressed('menu')) {
      if (this.dialogueOpen) this.closeDialogue();
      else this.toggleMenu('status');
    }
    if (game.input.pressed('map') && !this.dialogueOpen) this.toggleMenu('map');
    if (game.input.pressed('inventory') && !this.dialogueOpen) this.toggleMenu('inventory');

    // 体力バー
    const hpR = clamp01(p.hp / p.maxHp);
    this.hpLag = Math.max(hpR, this.hpLag - dt * 0.32);
    const hpBar = $('.bar.hp', this.hud);
    $('b', hpBar).style.width = `${hpR * 100}%`;
    $('i', hpBar).style.width = `${this.hpLag * 100}%`;
    $('span', hpBar).textContent = `${Math.ceil(p.hp)} / ${p.maxHp}`;
    // 瀕死は脈打たせる（画面の彩度低下だけでは気づきにくい）
    $('.vitals', this.hud).classList.toggle('low', hpR < 0.3 && !p.dead);
    $('.bar.fp b', this.hud).style.width = `${clamp01(p.fp / p.maxFP) * 100}%`;
    $('.bar.st b', this.hud).style.width = `${clamp01(p.stamina / p.maxStamina) * 100}%`;

    // 状態異常
    this.updateStatuses(p);

    // 残響・アイテム数
    $('.echo .val', this.hud).textContent = Math.floor(p.echo).toLocaleString('ja-JP');
    const cnt = $('.tb.sub[data-btn="item"] .cnt');
    if (cnt) cnt.textContent = p.flask.hp;

    // 世界情報
    if (game.dungeon) {
      $('.worldinfo .region', this.hud).textContent = game.dungeon.name;
      $('.worldinfo .time', this.hud).textContent = '地下';
      $('.worldinfo .weather', this.hud).textContent = `深度 ${game.dungeon.depth}`;
    } else {
      const region = game.world.regionAt(p.x, p.z);
      $('.worldinfo .region', this.hud).textContent = region.name;
      $('.worldinfo .time', this.hud).textContent = game.sky.timeLabel();
      $('.worldinfo .weather', this.hud).textContent = `${game.sky.phaseLabel()}・${game.sky.weatherLabel()}`;
    }

    // クエスト
    this.updateQuestTrack(game);

    // ボスバー
    if (this.bossRef) {
      const b = this.bossRef;
      const r = clamp01(b.hp / b.maxHp);
      this.bossLag = Math.max(r, this.bossLag - dt * 0.35);
      $('.bbar b', this.bossbar).style.width = `${r * 100}%`;
      $('.bbar i', this.bossbar).style.width = `${this.bossLag * 100}%`;
    }

    // 固定した相手の体力
    this.updateTargetBar(dt, game);

    // 方位帯
    if (this.settings.compass && this.cctx) {
      this.compass.classList.remove('hidden');
      this.compassTick = (this.compassTick || 0) + dt;
      if (this.compassTick > 0.05) { this.compassTick = 0; this.drawCompass(game); }
      // 着弾点の計算は上を向くと 340µs 掛かる。毎フレームは要らないので
      // 20 回/秒に間引く（見た目には追随して見える）
      this.aimTick = (this.aimTick || 0) + dt;
      if (this.aimTick > 0.05) { this.aimTick = 0; this.updateAim(game); }
    } else if (this.compass) {
      this.compass.classList.add('hidden');
    }

    // ミニマップ
    this.bakeMap(game, 3);
    this.mapTick = (this.mapTick || 0) + dt;
    if (this.mapTick > 0.1) { this.mapTick = 0; this.drawMinimap(game); }

    // ダメージ数字の追従
    this.updateDamageNumbers(dt, game);

    if (this.overlayOpen && this.liveRefresh) {
      this._lrT = (this._lrT || 0) + dt;
      if (this._lrT > 0.4) { this._lrT = 0; this.liveRefresh(); }
    }
  }

  updateStatuses(p) {
    const box = $('.statuses', this.hud);
    const kinds = ['bleed', 'poison', 'frost', 'fire'];
    if (!this.statusEls) {
      this.statusEls = {};
      for (const k of kinds) {
        const e = el('div', `sbar ${k}`, '<i></i>');
        e.style.display = 'none';
        box.appendChild(e);
        this.statusEls[k] = e;
      }
    }
    for (const k of kinds) {
      const v = p.status[k];
      const e = this.statusEls[k];
      e.style.display = v > 1 ? '' : 'none';
      if (v > 1) $('i', e).style.width = `${clamp01(v / 100) * 100}%`;
    }
  }

  /* ------------------------------------------------------- 方位帯 */
  /**
   * 向いている方角と、視界前方にある目印を横一列に描く。
   * ミニマップは「自分の周り」を見せるが、方位帯は「どちらへ行けばいいか」を見せる。
   */
  /** 弓や術を構えている間、着弾点に印を出す */
  updateAim(game) {
    const p = game.player;
    const el = this.aimdot;
    if (!el) return;
    const kind = (!p.dead && p.canAct !== undefined && !game.dungeonMenu)
      ? p.predictImpact(game, this._aimW) : null;
    if (!kind || p.riding) {
      if (!el.classList.contains('hidden')) el.classList.add('hidden');
      return;
    }
    if (!game.camera.project(this._aimW[0], this._aimW[1], this._aimW[2], this._aimS)) {
      if (!el.classList.contains('hidden')) el.classList.add('hidden');
      return;
    }
    el.classList.remove('hidden');
    el.classList.toggle('on', kind === 'enemy');
    el.style.left = `${this._aimS[0] * 100}%`;
    el.style.top = `${this._aimS[1] * 100}%`;
  }

  drawCompass(game) {
    const c = this.cctx;
    const W = this.compass.width, H = this.compass.height;
    c.clearRect(0, 0, W, H);
    if (game.dungeon) return;                 // 地下では方角に意味がない

    const p = game.player;
    const yaw = game.camera.yaw;
    const HALF = 1.30;                        // 表示する視野の半分（±約75°）
    const toX = (rel) => W / 2 + (rel / HALF) * (W / 2);
    const wrap = (a) => {
      let d = a;
      while (d > Math.PI) d -= Math.PI * 2;
      while (d < -Math.PI) d += Math.PI * 2;
      return d;
    };

    // 帯
    const g = c.createLinearGradient(0, 0, W, 0);
    g.addColorStop(0, 'rgba(0,0,0,0)');
    g.addColorStop(0.5, 'rgba(0,0,0,.58)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = g;
    c.fillRect(0, H * 0.38, W, H * 0.46);

    // 目盛り（15°ごと、90°ごとに方角名）
    const NAMES = { 0: '北', 90: '東', 180: '南', 270: '西' };
    c.textAlign = 'center';
    c.font = '600 15px system-ui, sans-serif';
    for (let deg = 0; deg < 360; deg += 15) {
      const rel = wrap(deg * Math.PI / 180 - yaw);
      if (Math.abs(rel) > HALF) continue;
      const x = toX(rel);
      const fade = 1 - Math.abs(rel) / HALF;
      const name = NAMES[deg];
      if (name) {
        c.fillStyle = `rgba(240,232,208,${0.42 + fade * 0.56})`;
        c.fillText(name, x, H * 0.74);
      } else {
        c.fillStyle = `rgba(232,223,200,${0.22 + fade * 0.46})`;
        c.fillRect(x - 1, H * 0.44, 2, deg % 45 === 0 ? 9 : 5);
      }
    }

    // 目印
    const marks = this._compassMarks || (this._compassMarks = []);
    marks.length = 0;
    const hintId = this.questHintPOI(game)?.id;
    for (const poi of game.pois) {
      if (!poi.discovered && poi.id !== hintId) continue;
      const d = Math.hypot(poi.x - p.x, poi.z - p.z);
      if (d > 700) continue;
      const rel = wrap(Math.atan2(poi.x - p.x, poi.z - p.z) - yaw);
      if (Math.abs(rel) > HALF) continue;
      let col = 'rgba(190,185,170,.85)', size = 3;
      if (poi.type === 'shrine') col = '#e8c86a';
      else if (poi.type === 'village') col = '#8fd0e8';
      else if (poi.type === 'boss') { col = poi.cleared ? '#6f7f6f' : '#e0464e'; size = 4; }
      else if (poi.type === 'grave' || poi.type === 'ruin') col = '#b48fe8';
      else if (poi.type === 'hermit' || poi.type === 'farm') col = '#a8c880';
      else if (poi.type === 'mine' || poi.type === 'wreck') col = '#b09070';
      else if (poi.type === 'stones' || poi.type === 'battlefield') col = '#c0a0d8';
      marks.push({ x: toX(rel), col, size, d, quest: poi.id === hintId });
    }
    // 遠いものから描いて、近いものを手前に
    marks.sort((a, b) => b.d - a.d);
    for (const m of marks) {
      const y = H * 0.26;
      if (m.quest) {
        c.fillStyle = '#ffd76a';
        c.beginPath();
        c.moveTo(m.x, y - 6); c.lineTo(m.x + 5, y); c.lineTo(m.x, y + 6); c.lineTo(m.x - 5, y);
        c.closePath(); c.fill();
      } else {
        c.globalAlpha = m.d > 400 ? 0.45 : 0.95;
        c.fillStyle = m.col;
        c.beginPath();
        c.arc(m.x, y, m.size, 0, Math.PI * 2);
        c.fill();
        c.globalAlpha = 1;
      }
    }

    // 中央の指針
    c.fillStyle = 'rgba(255,240,205,.95)';
    c.beginPath();
    c.moveTo(W / 2, H * 0.44);
    c.lineTo(W / 2 - 5, H * 0.30);
    c.lineTo(W / 2 + 5, H * 0.30);
    c.closePath();
    c.fill();
  }

  /** 追跡中のクエストが指し示す POI */
  questHintPOI(game) {
    let id = null;
    for (const qid of Object.keys(game.quests.state)) {
      if (!game.quests.isDone(qid)) { id = qid; if (qid === 'main') break; }
    }
    if (!id) return null;
    const q = QUESTS[id];
    const st = game.quests.state[id];
    const stage = q?.stages?.[st.stage];
    if (!stage?.hint) return null;
    return game.pois.find((po) => po.tag === stage.hint) || null;
  }

  /* --------------------------------------------- 固定した相手の体力 */
  updateTargetBar(dt, game) {
    const t = game.player.lockTarget;
    const bar = this.targetbar;
    if (!bar) return;
    // ボスバーが出ているときは重複させない
    if (!t || t.dead || (this.bossRef && this.bossRef === t)) {
      bar.classList.add('hidden');
      this.targetLag = 1;
      return;
    }
    bar.classList.remove('hidden');
    if (this._lastTarget !== t) { this._lastTarget = t; this.targetLag = 1; }
    const r = clamp01(t.hp / t.maxHp);
    this.targetLag = Math.max(r, (this.targetLag ?? 1) - dt * 0.4);
    $('.tname', bar).textContent = t.name || '';
    $('.tbar b', bar).style.width = `${r * 100}%`;
    $('.tbar i', bar).style.width = `${this.targetLag * 100}%`;
  }

  updateQuestTrack(game) {
    const track = $('.questtrack', this.hud);
    let id = null;
    for (const qid of Object.keys(game.quests.state)) {
      if (!game.quests.isDone(qid)) { id = qid; if (qid === 'main') break; }
    }
    if (!id) { track.classList.add('hidden'); return; }
    track.classList.remove('hidden');
    $('.qname', track).textContent = QUESTS[id]?.name || '';
    // 目的地が分かっているなら距離も出す（どれだけ歩けばいいかが分かる）
    let suffix = '';
    const hint = this.questHintPOI(game);
    if (hint && !game.dungeon) {
      const d = Math.hypot(hint.x - game.player.x, hint.z - game.player.z);
      suffix = d > 999 ? `　— ${hint.name} ${(d / 1000).toFixed(1)}km`
        : `　— ${hint.name} ${Math.round(d)}m`;
    }
    $('.qstage', track).textContent = game.quests.stageText(id, game) + suffix;
  }

  /* ------------------------------------------------------ 通知系 */
  toast(text, cls = '') {
    const t = el('div', `toast ${cls}`, text);
    this.toasts.appendChild(t);
    setTimeout(() => {
      t.style.transition = 'opacity .5s';
      t.style.opacity = '0';
      setTimeout(() => t.remove(), 520);
    }, cls.includes('big') ? 2600 : 1900);
  }
  itemGain(id, n) {
    const name = ITEMS[id]?.name || id;
    this.toast(`${name} ×${n}`);
  }
  echoGain(n) {
    this._echoAcc = (this._echoAcc || 0) + n;
    clearTimeout(this._echoTimer);
    this._echoTimer = setTimeout(() => {
      this.toast(`残響 +${this._echoAcc}`, 'gold');
      this._echoAcc = 0;
    }, 400);
    this.game?.audio.play('echo');
  }
  questUpdate(name, stage) { this.toast(`${name}：${stage}`, 'gold'); }
  questComplete(name) { this.toast(`クエスト達成 — ${name}`, 'big gold'); }
  /**
   * 発見の報せ。地図で先に知っていた場所へ着いたときは「到達」と呼び分ける。
   */
  discover(poi, echo = 0, first = true) {
    const head = first ? `${poi.name} を発見` : `${poi.name} に到達`;
    this.toast(echo ? `${head}　残響 +${echo}` : head, 'gold');
  }
  showRegion(region) {
    const s = $('#regionsplash');
    $('.rs-name', s).textContent = region.name;
    $('.rs-sub', s).textContent = region.en.toUpperCase();
    s.classList.remove('hidden');
    s.style.animation = 'none';
    void s.offsetWidth;
    s.style.animation = '';
    clearTimeout(this._regionTimer);
    this._regionTimer = setTimeout(() => s.classList.add('hidden'), 4200);
  }

  setPrompt(text) {
    if (!text) {
      this.promptEl.classList.add('hidden');
      this.interactBtn.classList.add('hidden');
      return;
    }
    this.promptEl.classList.remove('hidden');
    $('.txt', this.promptEl).textContent = text;
    this.interactBtn.classList.remove('hidden');
  }

  damageNumber(target, dmg, blocked, opts) {
    const g = this.game;
    if (!g || !this.settings.damageNumbers) return;
    const isPlayer = target === g.player;
    const n = {
      x: target.x + (Math.random() - 0.5) * 0.6,
      y: target.y + target.height * (0.7 + Math.random() * 0.25),
      z: target.z + (Math.random() - 0.5) * 0.6,
      t: 0,
      el: el('div', `dnum ${blocked ? 'blocked' : ''} ${opts?.type === 'critical' ? 'crit' : ''} ${isPlayer ? 'player' : ''}`,
        String(Math.round(dmg))),
    };
    this.dmgnums.appendChild(n.el);
    this.dmgPool.push(n);
    if (this.dmgPool.length > 26) {
      const old = this.dmgPool.shift();
      old.el.remove();
    }
  }

  updateDamageNumbers(dt, game) {
    const out = [0, 0, 0];
    for (let i = this.dmgPool.length - 1; i >= 0; i--) {
      const n = this.dmgPool[i];
      n.t += dt;
      if (n.t > 0.86) { n.el.remove(); this.dmgPool.splice(i, 1); continue; }
      game.camera.project(n.x, n.y, n.z, out);
      if (out[2] <= 0) { n.el.style.display = 'none'; continue; }
      n.el.style.display = '';
      n.el.style.left = `${out[0] * 100}%`;
      n.el.style.top = `${out[1] * 100}%`;
    }
  }

  /* -------------------------------------------------------- ボス */
  showBoss(boss) {
    this.bossRef = boss;
    this.bossLag = 1;
    this.bossbar.classList.remove('hidden');
    $('.bname', this.bossbar).textContent = boss.name;
    $('.btitle', this.bossbar).textContent = boss.title || '';
    // 形態の境目を刻む。あとどれだけで相手が変わるのかが読める
    const bar = $('.bbar', this.bossbar);
    for (const u of [...bar.querySelectorAll('u')]) u.remove();
    for (const ph of boss.bossDef?.phases || []) {
      if (!ph.hpAbove || ph.hpAbove <= 0 || ph.hpAbove >= 1) continue;
      const u = document.createElement('u');
      u.style.left = `${ph.hpAbove * 100}%`;
      bar.appendChild(u);
    }
  }
  hideBoss() {
    this.bossRef = null;
    this.bossbar.classList.add('hidden');
  }
  /**
   * ボスの名乗り。入場のときと、形態が変わったときに出す。
   * phase=true では体力バーを隠さず、上に重ねるだけにする。
   */
  bossIntro(name, title, phase = false) {
    const el0 = this.bossintro;
    if (!el0) return;
    $('.bi-name', el0).textContent = name;
    $('.bi-title', el0).textContent = title || '';
    el0.classList.remove('hidden', 'phase', 'felled');
    if (phase) el0.classList.add('phase');
    // アニメーションを掛け直すため、一度リフローを挟む
    void el0.offsetWidth;
    el0.classList.add('show');
    clearTimeout(this._introT);
    this._introT = setTimeout(() => {
      el0.classList.remove('show');
      el0.classList.add('hidden');
    }, phase ? 2200 : 3400);
  }

  /** 討伐の名乗り。曲が切れた静けさの上に置く */
  bossFelled(name) {
    const el0 = this.bossintro;
    if (!el0) { this.toast(`${name} を討伐`, 'big gold'); return; }
    $('.bi-name', el0).textContent = name;
    $('.bi-title', el0).textContent = '討 伐';
    el0.classList.remove('hidden', 'phase');
    el0.classList.add('felled');
    void el0.offsetWidth;
    el0.classList.add('show');
    clearTimeout(this._introT);
    this._introT = setTimeout(() => {
      el0.classList.remove('show');
      el0.classList.add('hidden');
    }, 3600);
  }

  bossDefeated(name) {
    this.bossFelled(name);
    setTimeout(() => this.hideBoss(), 2400);
  }

  /* -------------------------------------------------------- 死亡 */
  showDeath() {
    this.deathOpen = true;
    this.deathEl.classList.remove('hidden');
    setTimeout(() => {
      this.deathEl.classList.add('hidden');
      this.deathOpen = false;
      this.game.player.respawn(this.game);
      this.toast('篝火にて目覚める');
    }, 3400);
  }

  showEnding(game) {
    this.overlayOpen = true;
    this.overlay.classList.remove('hidden');
    this.overlay.innerHTML = '';
    const box = el('div', 'sheet');
    const t = game.player.playtime;
    const hh = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60);
    box.appendChild(el('div', 'ending', `
      <h2>残響の果て</h2>
      <p>深淵の王は沈黙した。<br>世界は記憶を取り戻し、灰は再び土になる。</p>
      <p>だが、篝火は消えない。<br>お前が灰の落人である限り——旅は続く。</p>
      <p style="opacity:.55;font-size:12px;margin-top:28px">
        討伐数 ${game.player.kills} ／ 死亡回数 ${game.player.deaths} ／ レベル ${game.player.level}<br>
        踏破時間 ${hh}時間${mm}分
      </p>
    `));
    const btn = el('button', 'btn wide', '世界に戻る');
    btn.onclick = () => this.closeOverlay();
    box.appendChild(btn);
    this.overlay.appendChild(box);
  }

  /* ======================================================= 会話 */
  openDialogue(npc) {
    const game = this.game;
    this.dialogueOpen = true;
    npc.talking = true;
    this.currentNPC = npc;
    this.dialogue.classList.remove('hidden');
    $('.dname', this.dialogue).textContent = `${npc.npcName}（${npc.title}）`;
    this.showNode(npc.dialogue(game));
    game.audio.play('ui_confirm');
  }

  showNode(node) {
    if (!node) return this.closeDialogue();
    const box = this.dialogue;
    const textEl = $('.dtext', box);
    const optsEl = $('.dopts', box);
    optsEl.innerHTML = '';
    // タイプライタ表示
    clearInterval(this._typer);
    const full = node.text || '';
    let i = 0;
    textEl.textContent = '';
    this._typer = setInterval(() => {
      i += 2;
      textEl.textContent = full.slice(0, i);
      if (i >= full.length) clearInterval(this._typer);
    }, 14);

    for (const o of node.options || []) {
      const b = el('button', '', o.label);
      b.onclick = () => {
        this.game.audio.play('ui_move');
        if (i < full.length) { clearInterval(this._typer); textEl.textContent = full; i = full.length; }
        o.action?.();
        if (o.end || (!o.next && !o.action)) return this.closeDialogue();
        if (o.next) this.showNode(o.next);
        else if (!o.keepOpen) this.closeDialogue();
      };
      optsEl.appendChild(b);
    }
    if (!node.options || !node.options.length) {
      const b = el('button', '', '閉じる');
      b.onclick = () => this.closeDialogue();
      optsEl.appendChild(b);
    }
  }

  closeDialogue() {
    clearInterval(this._typer);
    this.dialogue.classList.add('hidden');
    this.dialogueOpen = false;
    if (this.currentNPC) this.currentNPC.talking = false;
    this.currentNPC = null;
  }

  /* ==================================================== オーバーレイ */
  toggleMenu(tab) {
    if (this.overlayOpen) this.closeOverlay();
    else this.openMenu(tab);
  }

  openMenu(tab = 'status') {
    this.openOverlay([
      { id: 'status', label: 'ステータス' },
      { id: 'equip', label: '装備' },
      { id: 'inventory', label: '所持品' },
      { id: 'map', label: '地図' },
      { id: 'quests', label: '記録' },
      { id: 'settings', label: '設定' },
    ], tab);
  }

  openOverlay(tabs, tab) {
    this.overlayOpen = true;
    this.tabs = tabs;
    this.tab = tab || tabs[0].id;
    this.overlay.classList.remove('hidden');
    this.game?.audio.play('ui_on');
    this.renderOverlay();
  }

  closeOverlay() {
    this.overlayOpen = false;
    this.liveRefresh = null;
    this.overlay.classList.add('hidden');
    this.overlay.innerHTML = '';
    this.game?.audio.play('ui_off');
    this.game?.save();
  }

  renderOverlay() {
    const o = this.overlay;
    o.innerHTML = '';
    const bar = el('div', 'tabs');
    for (const t of this.tabs) {
      const b = el('button', this.tab === t.id ? 'on' : '', t.label);
      b.onclick = () => { this.tab = t.id; this.game?.audio.play('ui_move'); this.renderOverlay(); };
      bar.appendChild(b);
    }
    const close = el('button', 'close', '閉じる ✕');
    close.onclick = () => this.closeOverlay();
    bar.appendChild(close);
    o.appendChild(bar);

    const sheet = el('div', 'sheet');
    o.appendChild(sheet);
    this.liveRefresh = null;

    switch (this.tab) {
      case 'status': this.renderStatus(sheet); break;
      case 'equip': this.renderEquip(sheet); break;
      case 'inventory': this.renderInventory(sheet); break;
      case 'map': this.renderMap(sheet); break;
      case 'quests': this.renderQuests(sheet); break;
      case 'settings': this.renderSettings(sheet); break;
      case 'shop': this.renderShop(sheet); break;
      case 'smith': this.renderSmith(sheet); break;
      case 'craft': this.renderCraft(sheet); break;
      case 'shrine': this.renderShrine(sheet); break;
      case 'travel': this.renderTravel(sheet); break;
      case 'levelup': this.renderLevelUp(sheet); break;
      case 'howto': this.renderHowTo(sheet); break;
    }
  }

  /* --------------------------------------------------- ステータス */
  renderStatus(root) {
    const p = this.game.player;
    const w = WEAPONS[p.equip.weapon];
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', '', `${p.name} — レベル ${p.level}`));
    const rows = [
      ['HP', `${Math.ceil(p.hp)} / ${p.maxHp}`],
      ['FP', `${Math.ceil(p.fp)} / ${p.maxFP}`],
      ['スタミナ', `${Math.ceil(p.stamina)} / ${p.maxStamina}`],
      ['防御力', Math.round(p.def)],
      ['体勢', Math.round(p.maxPoise)],
      ['装備重量', `${p.load.toFixed(1)} / ${p.loadMax.toFixed(0)}（${ROLL_LABEL[p.rollType]}）`],
      ['攻撃力', Math.round(p.weaponAttackPower())],
      ['残響', Math.floor(p.echo).toLocaleString('ja-JP')],
    ];
    for (const [k, v] of rows) {
      const r = el('div', 'row');
      r.appendChild(el('span', '', k));
      r.appendChild(el('span', 'v', String(v)));
      panel.appendChild(r);
    }
    root.appendChild(panel);

    const sp = el('div', 'panel');
    sp.appendChild(el('h3', '', '能力値'));
    for (const k of STAT_KEYS) {
      const r = el('div', 'row');
      const left = el('span', '', `${STAT_NAMES[k]}<span class="sub">${STAT_DESC[k]}</span>`);
      r.appendChild(left);
      r.appendChild(el('span', 'v', String(p.stats[k])));
      sp.appendChild(r);
    }
    const note = el('div', 'row');
    note.appendChild(el('span', '', `<span class="sub">レベルアップは篝火で行える（次: ${levelCost(p.level).toLocaleString('ja-JP')} 残響）</span>`));
    sp.appendChild(note);
    root.appendChild(sp);

    if (w) {
      const wp = el('div', 'panel');
      const wt = UPGRADE.tier(p.upgrades[w.id] || 0);
      wp.appendChild(el('h3', '',
        `${w.name} +${p.upgrades[w.id] || 0}${wt.name ? `　${wt.name}` : ''}`));
      const scaleTxt = Object.entries(w.scale || {})
        .map(([s, g]) => `${STAT_NAMES[s]} ${g}`).join(' / ') || '—';
      const reqTxt = Object.entries(w.req || {})
        .map(([s, v]) => `${STAT_NAMES[s]} ${v}`).join(' / ') || '—';
      for (const [k, v] of [['分類', w.cls], ['基礎攻撃力', w.base], ['能力補正', scaleTxt], ['必要能力', reqTxt], ['重量', w.weight]]) {
        const r = el('div', 'row');
        r.appendChild(el('span', '', k));
        r.appendChild(el('span', 'v', String(v)));
        wp.appendChild(r);
      }
      const art = w.art ? ARTS[w.art] : null;
      if (art) {
        const r = el('div', 'row');
        r.innerHTML = `<span>戦技　${art.name}<span class="sub">${art.desc}</span></span>`
          + `<span class="v">FP ${art.fp}</span>`;
        wp.appendChild(r);
      }
      wp.appendChild(el('div', 'row', `<span class="sub">${w.desc}</span>`));
      root.appendChild(wp);
    }
  }

  /* ------------------------------------------------------- 装備 */
  /** 「今より良くなるか」を ↑↓ で示す。数値だけ並べても比較にならない */
  delta(now, next, digits = 0, lowerIsBetter = false) {
    const d = next - now;
    if (Math.abs(d) < (digits ? 0.05 : 0.5)) return '';
    const good = lowerIsBetter ? d < 0 : d > 0;
    const sign = d > 0 ? '+' : '−';
    const v = Math.abs(d).toFixed(digits);
    return `<em class="${good ? 'up' : 'down'}">${good ? '▲' : '▼'}${sign}${v}</em>`;
  }

  renderEquip(root) {
    const game = this.game;
    const p = game.player;
    const mk = (title, list, current, onPick, fmt) => {
      const panel = el('div', 'panel');
      panel.appendChild(el('h3', '', title));
      for (const id of list) {
        const r = el('div', `row clickable ${current === id ? 'sel' : ''}`);
        r.innerHTML = fmt(id);
        r.onclick = () => { onPick(id); game.audio.play('ui_confirm'); this.renderOverlay(); };
        panel.appendChild(r);
      }
      if (!list.length) panel.appendChild(el('div', 'row', '<span class="sub">まだ何も持っていない</span>'));
      root.appendChild(panel);
    };

    // 現在の積載を先頭に出す（何を持ち替えると転がりが鈍るのかを見せる）
    const ROLL = { fast: '軽やか', normal: '標準', heavy: '重い', over: '重量超過' };
    const sum = el('div', 'panel');
    sum.appendChild(el('h3', '', '装備の重さ'));
    const lr = clamp01(p.loadRatio);
    const loadRow = el('div', 'row');
    loadRow.innerHTML = `<span>積載<span class="sub">転がり：${ROLL[p.rollType]}`
      + `${p.rollType === 'over' ? '（速度低下）' : ''}</span></span>`
      + `<span class="v">${p.load.toFixed(1)} / ${p.loadMax.toFixed(1)}</span>`;
    sum.appendChild(loadRow);
    const meter = el('div', 'loadmeter', '<i></i>');
    $('i', meter).style.width = `${Math.min(100, lr * 100)}%`;
    $('i', meter).className = p.rollType;
    sum.appendChild(meter);
    sum.appendChild(el('div', 'row',
      `<span>攻撃力</span><span class="v">${Math.round(p.weaponAttackPower())}</span>`));
    sum.appendChild(el('div', 'row',
      `<span>防御力</span><span class="v">${Math.round(p.def)}</span>`));
    sum.appendChild(el('div', 'row',
      `<span>体勢（ポイズ）</span><span class="v">${Math.round(p.maxPoise)}</span>`));
    root.appendChild(sum);

    const nowAtk = p.attackPowerOf(p.equip.weapon);
    mk('武器', [...game.unlocked.weapons], p.equip.weapon,
      (id) => { p.equip.weapon = id; p.recalc(); },
      (id) => {
        const w = WEAPONS[id];
        const lv = p.upgrades[id] || 0;
        const atk = p.attackPowerOf(id);
        const cmp = id === p.equip.weapon ? '' : this.delta(nowAtk, atk);
        const unmet = p.unmetReqs(w);
        const warn = unmet.length
          ? `<span class="warn">能力不足：${unmet.map((u) => `${STAT_NAMES[u.stat]} ${u.have}/${u.req}`).join('・')}</span>`
          : '';
        const scales = Object.entries(w.scale || {})
          .map(([st, gr]) => `${STAT_NAMES[st]}${gr}`).join(' ');
        const tn = UPGRADE.tier(lv).name;
        const wa = w.art ? ARTS[w.art] : null;
        return `<span>${w.name} ${lv ? `+${lv}` : ''}${tn ? `　${tn}` : ''}`
          + `<span class="sub">${w.cls}・重量${w.weight}・補正 ${scales || '—'}`
          + `${wa ? `・戦技 ${wa.name}` : ''}</span>`
          + `<span class="sub">${w.desc}</span>${warn}</span>`
          + `<span class="v">${Math.round(atk)}${cmp}</span>`;
      });

    const nowBlock = p.equip.offhand ? SHIELDS[p.equip.offhand].block : 0;
    mk('盾 / 左手', ['none', ...game.unlocked.shields], p.equip.offhand || 'none',
      (id) => { p.equip.offhand = id === 'none' ? null : id; p.recalc(); },
      (id) => {
        if (id === 'none') {
          return `<span>なし<span class="sub">両手を空ける</span></span>`
            + `<span class="v">受け0%${this.delta(nowBlock * 100, 0)}</span>`;
        }
        const s = SHIELDS[id];
        const unmet = p.unmetReqs(s);
        const warn = unmet.length
          ? `<span class="warn">能力不足：${unmet.map((u) => `${STAT_NAMES[u.stat]} ${u.have}/${u.req}`).join('・')}</span>`
          : '';
        const cmp = id === p.equip.offhand ? '' : this.delta(nowBlock * 100, s.block * 100);
        return `<span>${s.name}<span class="sub">安定度 ${s.stability}・重量 ${s.weight}</span>`
          + `<span class="sub">${s.desc}</span>${warn}</span>`
          + `<span class="v">受け${Math.round(s.block * 100)}%${cmp}</span>`;
      });

    const heads = [...game.unlocked.armors].filter((a) => ARMORS[a].slot === 'head');
    const bodies = [...game.unlocked.armors].filter((a) => ARMORS[a].slot === 'body');
    const armorRow = (slot) => (id) => {
      const a = ARMORS[id];
      const cur = ARMORS[p.equip[slot]];
      const cmp = id === p.equip[slot] ? '' : this.delta(cur?.def || 0, a.def);
      const wd = id === p.equip[slot] ? '' : this.delta(cur?.weight || 0, a.weight, 1, true);
      return `<span>${a.name}<span class="sub">重量 ${a.weight}${wd}</span></span>`
        + `<span class="v">防御 ${a.def}${cmp}</span>`;
    };
    mk('頭防具', heads, p.equip.head,
      (id) => { p.equip.head = id; p.recalc(); }, armorRow('head'));
    mk('胴防具', bodies, p.equip.body,
      (id) => { p.equip.body = id; p.recalc(); }, armorRow('body'));

    // 護符 2 スロット
    for (let slot = 0; slot < 2; slot++) {
      mk(`護符 ${slot + 1}`, ['none', ...game.unlocked.talismans], p.equip.talisman[slot] || 'none',
        (id) => { p.equip.talisman[slot] = id === 'none' ? null : id; p.recalc(); },
        (id) => id === 'none' ? '<span>なし</span>'
          : `<span>${TALISMANS[id].name}<span class="sub">${TALISMANS[id].desc}</span></span>`);
    }

    mk('魔法', ['none', ...game.unlocked.spells], p.equip.spell || 'none',
      (id) => { p.equip.spell = id === 'none' ? null : id; },
      (id) => id === 'none' ? '<span>なし</span>'
        : `<span>${SPELLS[id].name}<span class="sub">${SPELLS[id].desc}</span></span><span class="v">FP ${SPELLS[id].fp}</span>`);
  }

  /* ------------------------------------------------------ 所持品 */
  renderInventory(root) {
    const p = this.game.player;
    const groups = { consumable: '消耗品', throw: '投擲', material: '素材', ammo: '弾', tool: '道具', valuable: '貴重品' };
    for (const [kind, label] of Object.entries(groups)) {
      const ids = Object.keys(p.inventory).filter((id) => (p.inventory[id] || 0) > 0 && ITEMS[id]?.kind === kind);
      if (!ids.length) continue;
      const panel = el('div', 'panel');
      panel.appendChild(el('h3', '', label));
      for (const id of ids) {
        const it = ITEMS[id];
        const r = el('div', 'row');
        r.innerHTML = `<span>${it.name}<span class="sub">${it.desc}</span></span><span class="v">×${p.inventory[id]}</span>`;
        if (kind === 'consumable' || kind === 'throw' || kind === 'valuable' || kind === 'tool') {
          const b = el('button', 'btn', p.equip.item === id ? '選択中' : '選択');
          b.onclick = () => { p.equip.item = id; this.game.audio.play('ui_confirm'); this.renderOverlay(); };
          r.appendChild(b);
        }
        panel.appendChild(r);
      }
      root.appendChild(panel);
    }
    const fp = el('div', 'panel');
    fp.appendChild(el('h3', '', '聖杯瓶'));
    fp.appendChild(el('div', 'row', `<span>癒しの雫<span class="sub">HPを回復。篝火で補充</span></span><span class="v">${p.flask.hp} / ${p.flask.hpMax}</span>`));
    fp.appendChild(el('div', 'row', `<span>灰の雫<span class="sub">FPを回復。篝火で補充</span></span><span class="v">${p.flask.fp} / ${p.flask.fpMax}</span>`));
    root.appendChild(fp);
  }

  /* -------------------------------------------------------- 記録 */
  renderQuests(root) {
    const game = this.game;
    const active = el('div', 'panel');
    active.appendChild(el('h3', '', '進行中'));
    let n = 0;
    for (const id of Object.keys(game.quests.state)) {
      if (game.quests.isDone(id)) continue;
      const q = QUESTS[id];
      if (!q) continue;
      n++;
      active.appendChild(el('div', 'row',
        `<span>${q.name}<span class="sub">${game.quests.stageText(id, game)}</span></span>${q.main ? '<span class="v">主線</span>' : ''}`));
    }
    if (!n) active.appendChild(el('div', 'row', '<span class="sub">進行中の依頼はない</span>'));
    root.appendChild(active);

    const done = el('div', 'panel');
    done.appendChild(el('h3', '', '達成済み'));
    let m = 0;
    for (const id of Object.keys(game.quests.state)) {
      if (!game.quests.isDone(id)) continue;
      m++;
      done.appendChild(el('div', 'row', `<span>${QUESTS[id]?.name || id}</span><span class="v">達成</span>`));
    }
    if (!m) done.appendChild(el('div', 'row', '<span class="sub">まだない</span>'));
    root.appendChild(done);

    const disc = el('div', 'panel');
    const reached = game.player.reached.size;
    const known = game.player.discovered.size;
    disc.appendChild(el('h3', '', `踏破した地 — ${reached} / ${game.pois.length}`
      + (known > reached ? `（地図で知る ${known}）` : '')));
    for (const R of REGIONS) {
      const list = game.pois.filter((p) => p.region === R.id && p.discovered);
      if (!list.length) continue;
      // 地図で知っているだけの場所は括弧でくくる
      const names = list.map((p) => (p.reached ? p.name : `（${p.name}）`)).join('・');
      const n = list.filter((p) => p.reached).length;
      disc.appendChild(el('div', 'row',
        `<span>${R.name}<span class="sub">${names}</span></span><span class="v">${n} / ${list.length}</span>`));
    }
    root.appendChild(disc);

    const st = el('div', 'panel');
    st.appendChild(el('h3', '', '記録'));
    const p = game.player;
    const t = p.playtime;
    st.appendChild(el('div', 'row', `<span>討伐数</span><span class="v">${p.kills}</span>`));
    st.appendChild(el('div', 'row', `<span>死亡回数</span><span class="v">${p.deaths}</span>`));
    st.appendChild(el('div', 'row', `<span>踏破時間</span><span class="v">${Math.floor(t / 3600)}時間${Math.floor((t % 3600) / 60)}分</span>`));
    root.appendChild(st);
  }

  /* -------------------------------------------------------- 地図 */
  renderMap(root) {
    const game = this.game;
    if (game.dungeon) {
      const d = game.dungeon;
      const p = el('div', 'panel');
      p.appendChild(el('h3', '', d.name));
      p.appendChild(el('div', 'row', '<span class="sub">地下では全図を開けない。ミニマップと松明を頼りに進め。</span>'));
      const best = game.dungeonProgress?.get(game.dungeonPOI?.id) || 0;
      p.appendChild(el('div', 'row', `<span>現在の階層</span><span>第${d.depth}層</span>`));
      p.appendChild(el('div', 'row',
        `<span>この遺跡の主</span><span>${d.bossDefeated ? '討伐済み — 下り階段が開いた' : '未討伐'}</span>`));
      p.appendChild(el('div', 'row', `<span>踏破記録</span><span>第${best}層まで</span>`));
      root.appendChild(p);
      return;
    }
    const wrap = el('div', 'panel');
    wrap.appendChild(el('h3', '', 'アエテリア全図'));
    const c = el('canvas');
    c.id = 'mapcanvas';
    c.width = 560; c.height = 560;
    wrap.appendChild(c);
    wrap.appendChild(el('div', 'maplegend',
      '<span>◈ 篝火</span><span>■ 集落</span><span>▲ 望楼</span><span>✦ ボス</span><span>● 現在地</span>'));
    root.appendChild(wrap);

    const draw = () => this.drawFullMap(c, game);
    draw();
    this.liveRefresh = draw;

    c.addEventListener('click', (ev) => {
      const rect = c.getBoundingClientRect();
      const mx = (ev.clientX - rect.left) / rect.width;
      const my = (ev.clientY - rect.top) / rect.height;
      const wx = (mx - 0.5) * WORLD_SPAN;
      const wz = (my - 0.5) * WORLD_SPAN;
      let best = null, bd = 120;
      for (const poi of game.pois) {
        if (!poi.discovered) continue;
        const d = Math.hypot(poi.x - wx, poi.z - wz);
        if (d < bd) { bd = d; best = poi; }
      }
      if (best) this.toast(`${best.name}（${REGIONS.find((r) => r.id === best.region)?.name || ''}）`);
    });
  }

  /* -------------------------------------------------------- 篝火 */
  openShrine(poi) {
    this.shrinePOI = poi;
    this.openOverlay([
      { id: 'shrine', label: '篝火' },
      { id: 'levelup', label: 'レベルアップ' },
      { id: 'travel', label: '転送' },
      { id: 'equip', label: '装備' },
      { id: 'inventory', label: '所持品' },
    ], 'shrine');
  }

  renderShrine(root) {
    const game = this.game;
    const poi = this.shrinePOI;
    const p = game.player;
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', '', poi ? poi.name : '篝火'));
    panel.appendChild(el('div', 'row', '<span class="sub">篝火で休息すると、HP・雫が回復し、倒した敵が復活する。</span>'));
    const rest = el('button', 'btn wide', '休息する');
    rest.onclick = () => {
      p.restAtShrine(poi, game);
      game.audio.play('shrine');
      this.toast('残り火に手をかざした');
      this.closeOverlay();
    };
    panel.appendChild(rest);

    const lvl = el('button', 'btn wide', `レベルアップ（必要 ${levelCost(p.level).toLocaleString('ja-JP')} 残響 / 所持 ${Math.floor(p.echo).toLocaleString('ja-JP')}）`);
    lvl.onclick = () => { this.tab = 'levelup'; this.renderOverlay(); };
    panel.appendChild(lvl);

    const tv = el('button', 'btn wide', '別の篝火へ転送する');
    tv.onclick = () => { this.tab = 'travel'; this.renderOverlay(); };
    panel.appendChild(tv);

    // 時を待つ。村人は夜になると家へ入ってしまうので、朝を待てるようにしておく
    const wait = el('div', 'row');
    wait.innerHTML = '<span>時を待つ<span class="sub">村の者は夜には家へ入る</span></span>';
    for (const [label, hour] of [['朝', 6.5], ['昼', 12], ['夜', 21]]) {
      const b = el('button', 'btn', label);
      b.onclick = () => {
        game.sky.skipToHour(hour);
        game.sky.update(0.016, game);
        this.toast(`${label}まで待った（${game.sky.timeLabel()}）`);
        this.closeOverlay();
      };
      wait.appendChild(b);
    }
    panel.appendChild(wait);
    root.appendChild(panel);
  }

  renderLevelUp(root) {
    const game = this.game;
    const p = game.player;
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', '', `レベル ${p.level} → ${p.level + 1}`));
    const cost = levelCost(p.level);
    panel.appendChild(el('div', 'row',
      `<span>必要な残響</span><span class="v">${cost.toLocaleString('ja-JP')} / 所持 ${Math.floor(p.echo).toLocaleString('ja-JP')}</span>`));
    for (const k of STAT_KEYS) {
      const r = el('div', 'row');
      const preview = this.statPreview(p, k);
      r.innerHTML = `<span>${STAT_NAMES[k]} <span class="v">${p.stats[k]} → ${p.stats[k] + 1}</span><span class="sub">${preview}</span></span>`;
      const b = el('button', 'btn', '上げる');
      b.disabled = p.echo < cost;
      b.onclick = () => {
        if (p.levelUp(k)) {
          game.audio.play('levelup');
          this.toast(`${STAT_NAMES[k]} が ${p.stats[k]} になった`, 'gold');
          this.renderOverlay();
        }
      };
      r.appendChild(b);
      panel.appendChild(r);
    }
    root.appendChild(panel);
  }

  statPreview(p, k) {
    const s = p.stats[k];
    switch (k) {
      case 'vit': return `最大HP ${p.maxHp} → ${Math.floor((280 + (s + 1) * 26 + Math.pow(s + 1, 1.5) * 1.5) * p.mods.hpMul)}`;
      case 'end': return `スタミナ ${p.maxStamina} → ${Math.floor(90 + (s + 1) * 5.2)}`;
      case 'arc': case 'fth': return `FP ${p.maxFP} → 上昇 / 魔法威力 上昇`;
      default: {
        const w = WEAPONS[p.equip.weapon];
        const g = w?.scale?.[k];
        if (!g) return '現在の武器には影響しない';
        const cur = w.base * SCALE[g] * scalingFactor(s) * 1.15;
        const nxt = w.base * SCALE[g] * scalingFactor(s + 1) * 1.15;
        return `武器攻撃力 +${(nxt - cur).toFixed(1)}`;
      }
    }
  }

  renderTravel(root) {
    const game = this.game;
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', '', '発見した篝火'));
    const shrines = game.pois.filter((p) => p.type === 'shrine' && p.discovered);
    if (!shrines.length) panel.appendChild(el('div', 'row', '<span class="sub">まだ篝火を見つけていない</span>'));
    for (const s of shrines) {
      const r = el('div', 'row clickable');
      const region = REGIONS.find((x) => x.id === s.region);
      const d = Math.hypot(s.x - game.player.x, s.z - game.player.z);
      r.innerHTML = `<span>${s.name}<span class="sub">${region?.name || ''}</span></span><span class="v">${Math.round(d)} m</span>`;
      r.onclick = () => {
        const p = game.player;
        game.placeSafely(p, s.x + 1.6, s.z + 1.6);
        p.lastShrine = s;
        p.lockTarget = null;
        game.endBoss(true);
        game.respawnWorld();
        game.audio.play('shrine');
        this.toast(`${s.name} へ転送した`);
        this.closeOverlay();
      };
      panel.appendChild(r);
    }
    root.appendChild(panel);
  }

  /* -------------------------------------------------------- 商店 */
  openShop() {
    this.openOverlay([
      { id: 'shop', label: '商品' },
      { id: 'inventory', label: '所持品' },
    ], 'shop');
  }

  renderShop(root) {
    const game = this.game;
    const p = game.player;
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', '', `品揃え — 所持 ${Math.floor(p.echo).toLocaleString('ja-JP')} 残響`));
    for (const entry of SHOP) {
      const r = el('div', 'row');
      let name = '', sub = '', owned = false;
      if (entry.item) { name = ITEMS[entry.item].name; sub = ITEMS[entry.item].desc; }
      else if (entry.weapon) { name = WEAPONS[entry.weapon].name; sub = WEAPONS[entry.weapon].desc; owned = game.unlocked.weapons.has(entry.weapon); }
      else if (entry.shield) { name = SHIELDS[entry.shield].name; sub = SHIELDS[entry.shield].desc; owned = game.unlocked.shields.has(entry.shield); }
      else if (entry.armor) { name = ARMORS[entry.armor].name; sub = `防御 ${ARMORS[entry.armor].def} / 重量 ${ARMORS[entry.armor].weight}`; owned = game.unlocked.armors.has(entry.armor); }
      else if (entry.talisman) { name = TALISMANS[entry.talisman].name; sub = TALISMANS[entry.talisman].desc; owned = game.unlocked.talismans.has(entry.talisman); }
      else if (entry.spell) { name = SPELLS[entry.spell].name; sub = SPELLS[entry.spell].desc; owned = game.unlocked.spells.has(entry.spell); }
      r.innerHTML = `<span>${name}<span class="sub">${sub}</span></span><span class="v">${entry.price.toLocaleString('ja-JP')}</span>`;
      const b = el('button', 'btn', owned ? '所持済' : '購入');
      b.disabled = owned || p.echo < entry.price;
      b.onclick = () => {
        if (p.echo < entry.price) return;
        p.echo -= entry.price;
        if (entry.item) { p.addItem(entry.item, entry.item === 'arrow' ? 20 : 1); this.itemGain(entry.item, entry.item === 'arrow' ? 20 : 1); }
        if (entry.weapon) game.unlockWeapon(entry.weapon);
        if (entry.shield) game.unlockShield(entry.shield);
        if (entry.armor) game.unlockArmor(entry.armor);
        if (entry.talisman) game.unlockTalisman(entry.talisman);
        if (entry.spell) game.unlockSpell(entry.spell);
        game.audio.play('ui_confirm');
        this.renderOverlay();
      };
      r.appendChild(b);
      panel.appendChild(r);
    }
    root.appendChild(panel);

    const sell = el('div', 'panel');
    sell.appendChild(el('h3', '', '素材を売る'));
    const mats = Object.keys(p.inventory).filter((id) => ITEMS[id]?.kind === 'material' && p.inventory[id] > 0);
    if (!mats.length) sell.appendChild(el('div', 'row', '<span class="sub">売れるものがない</span>'));
    for (const id of mats) {
      const price = id === 'shard_ancient' ? 600 : id === 'ore_silver' ? 300 : 80;
      const r = el('div', 'row');
      r.innerHTML = `<span>${ITEMS[id].name}<span class="sub">1つ ${price} 残響</span></span><span class="v">×${p.inventory[id]}</span>`;
      const b = el('button', 'btn', '1つ売る');
      b.onclick = () => {
        p.inventory[id]--;
        p.echo += price;
        game.audio.play('ui_confirm');
        this.renderOverlay();
      };
      r.appendChild(b);
      sell.appendChild(r);
    }
    root.appendChild(sell);
  }

  /* -------------------------------------------------------- 鍛冶 */
  openSmith() {
    this.openOverlay([{ id: 'smith', label: '武器強化' }, { id: 'equip', label: '装備' }], 'smith');
  }

  renderSmith(root) {
    const game = this.game;
    const p = game.player;
    const w = WEAPONS[p.equip.weapon];
    const lv = p.upgrades[p.equip.weapon] || 0;
    const panel = el('div', 'panel');
    const tier = UPGRADE.tier(lv);
    panel.appendChild(el('h3', '', `${w.name} +${lv}${tier.name ? `　${tier.name}` : ''}`));
    if (lv >= UPGRADE.maxLevel) {
      panel.appendChild(el('div', 'row',
        '<span class="sub">これ以上は強化できない。刃はもう限界まで冴えている</span>'));
    } else {
      const c = UPGRADE.cost(lv);
      panel.appendChild(el('div', 'row',
        `<span>攻撃力</span><span class="v">${Math.round(w.base * UPGRADE.mul(lv))} → ${Math.round(w.base * UPGRADE.mul(lv + 1))}</span>`));
      panel.appendChild(el('div', 'row',
        `<span>必要な残響</span><span class="v">${c.echo.toLocaleString('ja-JP')} / ${Math.floor(p.echo).toLocaleString('ja-JP')}</span>`));
      panel.appendChild(el('div', 'row',
        `<span>必要素材</span><span class="v">${ITEMS[c.mat].name} ×${c.matCount} / 所持 ${p.inventory[c.mat] || 0}</span>`));
      const b = el('button', 'btn wide', '強化する');
      b.onclick = () => { if (p.upgradeWeapon(game)) this.renderOverlay(); };
      panel.appendChild(b);

      // 次に呼び名が変わる段階と、最大までの見通し
      const next = [2, 5, 8, 10].find((n) => n > lv);
      if (next) {
        const nt = UPGRADE.tier(next);
        panel.appendChild(el('div', 'row',
          `<span>次の位<span class="sub">+${next} で「${nt.name}」</span></span>`
          + `<span class="v">あと ${next - lv}</span>`));
      }
      const tm = UPGRADE.toMax(lv);
      const mats = Object.entries(tm.mats)
        .map(([id, n]) => `${ITEMS[id].name} ×${n}`).join('・');
      panel.appendChild(el('div', 'row',
        `<span>極みまで<span class="sub">${mats}</span></span>`
        + `<span class="v">${tm.echo.toLocaleString('ja-JP')} 残響</span>`));
    }
    root.appendChild(panel);

    const list = el('div', 'panel');
    list.appendChild(el('h3', '', '所持している武器'));
    for (const id of game.unlocked.weapons) {
      const ww = WEAPONS[id];
      const r = el('div', `row clickable ${p.equip.weapon === id ? 'sel' : ''}`);
      const ulv = p.upgrades[id] || 0;
      const ut = UPGRADE.tier(ulv);
      r.innerHTML = `<span>${ww.name} +${ulv}<span class="sub">${ww.cls}`
        + `${ut.name ? `・${ut.name}` : ''}</span></span>`
        + `<span class="v">${Math.round(ww.base * UPGRADE.mul(ulv))}</span>`;
      r.onclick = () => { p.equip.weapon = id; p.recalc(); this.renderOverlay(); };
      list.appendChild(r);
    }
    root.appendChild(list);
  }

  /* -------------------------------------------------------- 調合 */
  openCraft() {
    this.openOverlay([{ id: 'craft', label: '調合' }, { id: 'inventory', label: '所持品' }], 'craft');
  }

  renderCraft(root) {
    const game = this.game;
    const p = game.player;
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', '', '調合'));
    for (const rc of RECIPES) {
      const need = Object.entries(rc.need)
        .map(([id, n]) => `${ITEMS[id].name} ×${n}（${p.inventory[id] || 0}）`).join('・');
      const ok = Object.entries(rc.need).every(([id, n]) => (p.inventory[id] || 0) >= n) && p.echo >= rc.echo;
      const r = el('div', 'row');
      r.innerHTML = `<span>${ITEMS[rc.out].name} ×${rc.count}<span class="sub">${need} ＋ ${rc.echo} 残響</span></span>`;
      const b = el('button', 'btn', '作る');
      b.disabled = !ok;
      b.onclick = () => {
        for (const [id, n] of Object.entries(rc.need)) p.inventory[id] -= n;
        p.echo -= rc.echo;
        p.addItem(rc.out, rc.count);
        game.audio.play('upgrade');
        this.itemGain(rc.out, rc.count);
        this.renderOverlay();
      };
      r.appendChild(b);
      panel.appendChild(r);
    }
    root.appendChild(panel);
  }

  /* -------------------------------------------------------- 設定 */
  renderSettings(root) {
    const s = this.settings;
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', '', 'グラフィック'));
    const qrow = el('div', 'row');
    qrow.appendChild(el('span', '', '品質<span class="sub">端末が重い場合は「軽量」を選ぶ</span>'));
    const qbox = el('span', '');
    for (const [id, label] of [['low', '軽量'], ['medium', '標準'], ['high', '高品質']]) {
      const b = el('button', 'btn', label);
      b.style.opacity = s.quality === id ? '1' : '.45';
      b.onclick = () => {
        s.quality = id;
        this.saveSettings();
        this.game?.setQuality(id);
        this.renderOverlay();
      };
      qbox.appendChild(b);
    }
    qrow.appendChild(qbox);
    panel.appendChild(qrow);
    root.appendChild(panel);

    const ap = el('div', 'panel');
    ap.appendChild(el('h3', '', '音量'));
    for (const [key, label] of [['master', '全体'], ['sfx', '効果音'], ['music', '音楽']]) {
      const r = el('div', 'row');
      r.appendChild(el('span', '', label));
      const input = el('input');
      input.type = 'range'; input.min = '0'; input.max = '1'; input.step = '0.05';
      input.value = String(s[key]);
      input.style.width = '46%';
      input.oninput = () => { s[key] = parseFloat(input.value); this.applySettings(); };
      input.onchange = () => this.saveSettings();
      r.appendChild(input);
      ap.appendChild(r);
    }
    root.appendChild(ap);

    const cp = el('div', 'panel');
    cp.appendChild(el('h3', '', '操作'));
    const sr = el('div', 'row');
    sr.appendChild(el('span', '', 'カメラ感度'));
    const si = el('input');
    si.type = 'range'; si.min = '0.4'; si.max = '2.2'; si.step = '0.1';
    si.value = String(s.sensitivity); si.style.width = '46%';
    si.oninput = () => { s.sensitivity = parseFloat(si.value); this.applySettings(); };
    si.onchange = () => this.saveSettings();
    sr.appendChild(si);
    cp.appendChild(sr);

    // 切り替え式の設定をまとめて作る
    const toggle = (label, sub, key, apply) => {
      const r = el('div', 'row clickable');
      r.innerHTML = `<span>${label}${sub ? `<span class="sub">${sub}</span>` : ''}</span>`
        + `<span class="v">${s[key] ? 'オン' : 'オフ'}</span>`;
      r.onclick = () => {
        s[key] = !s[key];
        apply?.();
        this.applySettings();
        this.saveSettings();
        this.game?.audio.play('ui_move');
        this.renderOverlay();
      };
      return r;
    };
    cp.appendChild(toggle('左利き配置', '操作ボタンと情報表示を左右反転する', 'lefty'));
    cp.appendChild(toggle('自動ロックオン', '近い敵に自動で照準を合わせる', 'autoLock'));

    // ボタンの大きさ
    const br = el('div', 'row');
    br.appendChild(el('span', '', 'ボタンの大きさ<span class="sub">指の太さに合わせる</span>'));
    const bbox = el('span', '');
    for (const [id, label] of [['small', '小'], ['normal', '中'], ['big', '大']]) {
      const b = el('button', 'btn', label);
      b.style.opacity = s.buttonSize === id ? '1' : '.45';
      b.onclick = () => {
        s.buttonSize = id;
        this.applySettings(); this.saveSettings(); this.renderOverlay();
      };
      bbox.appendChild(b);
    }
    br.appendChild(bbox);
    cp.appendChild(br);
    root.appendChild(cp);

    const hp = el('div', 'panel');
    hp.appendChild(el('h3', '', '表示'));
    hp.appendChild(toggle('方位帯', '画面上部に方角と目印を出す', 'compass'));
    hp.appendChild(toggle('ダメージ数値', '与えたダメージを数字で出す', 'damageNumbers'));
    root.appendChild(hp);

    const dp = el('div', 'panel');
    dp.appendChild(el('h3', '', 'データ'));
    const sv = el('button', 'btn wide', '手動セーブ');
    sv.onclick = () => { this.game?.save(); this.toast('セーブした'); };
    dp.appendChild(sv);
    const del = el('button', 'btn wide', 'セーブデータを削除して最初から');
    del.onclick = () => {
      if (!confirm('セーブデータを削除しますか？ この操作は取り消せません。')) return;
      localStorage.removeItem('aetheria_save');
      location.reload();
    };
    dp.appendChild(del);
    root.appendChild(dp);
  }

  applySettings() {
    const s = this.settings;
    this.game?.audio.setVolumes({ master: s.master, sfx: s.sfx, music: s.music });
    if (this.game) this.game.input.lookSensitivity = 0.0055 * s.sensitivity;
    // 画面レイアウト（左利き・ボタンの大きさ）は body のクラスで切り替える
    const b = document.body;
    b.classList.toggle('lefty', !!s.lefty);
    b.classList.toggle('tbig', s.buttonSize === 'big');
    b.classList.toggle('tsmall', s.buttonSize === 'small');
    if (this.compass) this.compass.classList.toggle('hidden', !s.compass);
  }
  saveSettings() {
    try { localStorage.setItem('aetheria_settings', JSON.stringify(this.settings)); } catch { /* noop */ }
  }
  loadSettings() {
    try {
      const d = JSON.parse(localStorage.getItem('aetheria_settings') || '{}');
      Object.assign(this.settings, d);
    } catch { /* noop */ }
  }

  renderHowTo(root) {
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', '', 'スマートフォン'));
    const rows = [
      ['左半分をドラッグ', '移動（仮想スティック）'],
      ['右半分をドラッグ', 'カメラ操作'],
      ['攻', 'タップで軽攻撃 / 長押しで強攻撃'],
      ['避', 'タップで回避（無敵あり）/ 押しっぱなしでダッシュ'],
      ['防', '押しっぱなしでガード / タップでパリィ'],
      ['跳', 'ジャンプ'],
      ['◎', '敵をロックオン'],
      ['✦', '装備した魔法を詠唱'],
      ['雫', '癒しの雫を飲む'],
      ['戦技', '武器ごとの切り札（FPとスタミナを使う）'],
      ['調べる', '篝火・宝箱・NPC・採取'],
    ];
    for (const [k, v] of rows) {
      panel.appendChild(el('div', 'row', `<span>${k}</span><span class="v">${v}</span>`));
    }
    root.appendChild(panel);

    const kb = el('div', 'panel');
    kb.appendChild(el('h3', '', 'キーボード / ゲームパッド'));
    const rows2 = [
      ['WASD', '移動'], ['マウス', 'カメラ'], ['J / 左クリック', '軽攻撃'], ['K', '強攻撃'],
      ['Space', '回避'], ['Shift', 'ダッシュ'], ['L / 右クリック', 'ガード'], ['Q', 'ロックオン'],
      ['E', '調べる'], ['R', '道具'], ['F', '魔法'], ['C', 'ジャンプ'],
      ['M', '地図'], ['Tab', '所持品'], ['Esc', 'メニュー'],
      ['ゲームパッド', '一般的な配置に対応'],
    ];
    for (const [k, v] of rows2) kb.appendChild(el('div', 'row', `<span>${k}</span><span class="v">${v}</span>`));
    root.appendChild(kb);

    const tip = el('div', 'panel');
    tip.appendChild(el('h3', '', '戦いの心得'));
    for (const t of [
      '敵の攻撃は「予備動作 → 判定 → 硬直」で構成される。予備動作を見て回避を合わせる。',
      '回避には無敵時間がある。装備が重いほど無敵は短い。',
      'ガード中に攻撃を受けるとスタミナを失う。0 になると体勢を崩される。',
      'ガードボタンをタップするとパリィ。成功すれば致命の一撃を叩き込める。',
      '敵の背後から攻撃すると背面攻撃になる。',
      '死ぬと残響を落とす。同じ場所へ戻れば回収できるが、再び死ぬと失われる。',
      '篝火で休むと回復するが、倒した敵も復活する。',
    ]) tip.appendChild(el('div', 'row', `<span class="sub">${t}</span>`));
    root.appendChild(tip);
  }

  /* ====================================================== マップ描画 */
  /** ワールドの色を少しずつオフスクリーンに焼く */
  bakeMap(game, rows) {
    if (this.mapRow >= MAP_SIZE) return;
    const img = this.mapImg.data;
    const w = game.world;
    const col = [0, 0, 0];
    for (let r = 0; r < rows && this.mapRow < MAP_SIZE; r++, this.mapRow++) {
      const j = this.mapRow;
      const wz = (j / MAP_SIZE - 0.5) * WORLD_SPAN;
      for (let i = 0; i < MAP_SIZE; i++) {
        const wx = (i / MAP_SIZE - 0.5) * WORLD_SPAN;
        const o = (j * MAP_SIZE + i) * 4;
        const h = w.heightBase(wx, wz);
        if (h < 0) {
          const d = clamp01(-h / 40);
          img[o] = lerp(30, 8, d); img[o + 1] = lerp(60, 22, d); img[o + 2] = lerp(85, 45, d);
        } else {
          w.surfaceColor(wx, wz, h, 0.2, col);
          // 北西からの陰影で起伏を読みやすくする
          const hx = w.heightBase(wx + 26, wz) - h;
          const hz = w.heightBase(wx, wz + 26) - h;
          const relief = clamp01(0.62 - (hx + hz) * 0.018);
          const shade = (1.15 + clamp01(h / 200) * 0.45) * (0.62 + relief * 0.7);
          img[o] = clamp(col[0] * 255 * shade, 0, 255);
          img[o + 1] = clamp(col[1] * 255 * shade, 0, 255);
          img[o + 2] = clamp(col[2] * 255 * shade, 0, 255);
        }
        img[o + 3] = 255;
      }
    }
    this.mapCanvas.getContext('2d').putImageData(this.mapImg, 0, 0);
  }

  drawMinimap(game) {
    if (game.dungeon) return this.drawDungeonMap(game);
    const c = this.mctx;
    const S = this.minimap.width;
    const p = game.player;
    const range = 190;   // ミニマップの表示範囲（m）
    c.clearRect(0, 0, S, S);
    c.save();
    c.beginPath();
    c.arc(S / 2, S / 2, S / 2 - 1, 0, Math.PI * 2);
    c.clip();
    c.fillStyle = '#0a0a0e';
    c.fillRect(0, 0, S, S);

    // 焼いた地図を回転して描画
    if (this.mapRow > 4) {
      const scale = (S / (range * 2)) * (WORLD_SPAN / MAP_SIZE);
      c.save();
      c.translate(S / 2, S / 2);
      c.rotate(-game.camera.yaw);
      c.imageSmoothingEnabled = true;
      const px = (p.x / WORLD_SPAN + 0.5) * MAP_SIZE;
      const pz = (p.z / WORLD_SPAN + 0.5) * MAP_SIZE;
      c.globalAlpha = 0.92;
      c.drawImage(this.mapCanvas,
        -px * scale, -pz * scale, MAP_SIZE * scale, MAP_SIZE * scale);
      c.globalAlpha = 1;
      c.restore();
    }

    // マーカー
    const toXY = (wx, wz) => {
      const dx = wx - p.x, dz = wz - p.z;
      const ca = Math.cos(-game.camera.yaw), sa = Math.sin(-game.camera.yaw);
      const rx = dx * ca - dz * sa, rz = dx * sa + dz * ca;
      return [S / 2 + (rx / range) * (S / 2), S / 2 + (rz / range) * (S / 2)];
    };
    for (const poi of game.pois) {
      if (!poi.discovered) continue;
      if (Math.abs(poi.x - p.x) > range || Math.abs(poi.z - p.z) > range) continue;
      const [x, y] = toXY(poi.x, poi.z);
      c.fillStyle = POI_COLOR[poi.type] || '#888';
      c.fillRect(x - 2.5, y - 2.5, 5, 5);
    }
    for (const e of game.enemies) {
      if (e.dead) continue;
      if (Math.abs(e.x - p.x) > range || Math.abs(e.z - p.z) > range) continue;
      const [x, y] = toXY(e.x, e.z);
      c.fillStyle = e.boss ? '#ff8a3a' : e.aggro ? '#e0464e' : 'rgba(200,90,90,.6)';
      c.beginPath(); c.arc(x, y, e.boss ? 3.4 : 2, 0, Math.PI * 2); c.fill();
    }
    // 自機
    c.fillStyle = '#e8dfc8';
    c.beginPath();
    c.moveTo(S / 2, S / 2 - 5);
    c.lineTo(S / 2 - 4, S / 2 + 4);
    c.lineTo(S / 2 + 4, S / 2 + 4);
    c.closePath();
    c.fill();
    c.restore();
  }

  /** ダンジョン内のミニマップ（探索済みのセルだけ描く） */
  drawDungeonMap(game) {
    const c = this.mctx;
    const S = this.minimap.width;
    const d = game.dungeon;
    const p = game.player;
    if (!d.seen) d.seen = new Set();
    const [pcx, pcz] = d.toCell(p.x, p.z);
    for (let j = -2; j <= 2; j++) {
      for (let i = -2; i <= 2; i++) {
        if (d.open(pcx + i, pcz + j)) d.seen.add((pcz + j) * d.gw + (pcx + i));
      }
    }
    c.clearRect(0, 0, S, S);
    c.save();
    c.beginPath();
    c.arc(S / 2, S / 2, S / 2 - 1, 0, Math.PI * 2);
    c.clip();
    c.fillStyle = '#08080b';
    c.fillRect(0, 0, S, S);
    const view = 7;                       // 表示するセル半径
    const cell = S / (view * 2 + 1);
    c.save();
    c.translate(S / 2, S / 2);
    c.rotate(-game.camera.yaw);
    for (let j = -view; j <= view; j++) {
      for (let i = -view; i <= view; i++) {
        const cx = pcx + i, cz = pcz + j;
        if (!d.seen.has(cz * d.gw + cx)) continue;
        c.fillStyle = d.grid[d.idx(cx, cz)] === 1 ? 'rgba(180,160,110,.55)' : 'rgba(120,110,86,.42)';
        c.fillRect(i * cell - cell / 2, j * cell - cell / 2, cell + 1, cell + 1);
      }
    }
    // 主の間（見えている範囲だけ赤く染める）
    if (d.bossRoom) {
      const r = d.bossRoom;
      c.fillStyle = d.bossDefeated ? 'rgba(120,180,140,.30)' : 'rgba(190,70,70,.30)';
      for (let j = r.z; j < r.z + r.h; j++) {
        for (let i = r.x; i < r.x + r.w; i++) {
          if (!d.seen.has(j * d.gw + i)) continue;
          c.fillRect((i - pcx) * cell - cell / 2, (j - pcz) * cell - cell / 2, cell + 1, cell + 1);
        }
      }
    }
    // 宝箱と出口
    for (const ch of d.chests) {
      if (ch.opened) continue;
      if (ch.boss && !d.bossDefeated) continue;
      const [bx, bz] = d.toCell(ch.x, ch.z);
      if (!d.seen.has(bz * d.gw + bx)) continue;
      c.fillStyle = '#e8c86a';
      c.fillRect((bx - pcx) * cell - 2, (bz - pcz) * cell - 2, 4, 4);
    }
    const [ex, ez] = d.toCell(d.exitPoint.x, d.exitPoint.z);
    if (d.seen.has(ez * d.gw + ex)) {
      c.fillStyle = '#8fd0e8';
      c.fillRect((ex - pcx) * cell - 2.5, (ez - pcz) * cell - 2.5, 5, 5);
    }
    // 下り階段
    if (d.bossDefeated && d.stairPoint) {
      const [sx, sz] = d.toCell(d.stairPoint.x, d.stairPoint.z);
      if (d.seen.has(sz * d.gw + sx)) {
        c.fillStyle = '#b9e8a0';
        c.beginPath();
        c.moveTo((sx - pcx) * cell, (sz - pcz) * cell + 3.5);
        c.lineTo((sx - pcx) * cell - 3.5, (sz - pcz) * cell - 2.5);
        c.lineTo((sx - pcx) * cell + 3.5, (sz - pcz) * cell - 2.5);
        c.closePath();
        c.fill();
      }
    }
    for (const e of game.enemies) {
      if (e.dead || !e.aggro) continue;
      const [bx, bz] = d.toCell(e.x, e.z);
      c.fillStyle = '#e0464e';
      c.beginPath();
      c.arc((bx - pcx) * cell, (bz - pcz) * cell, 2, 0, Math.PI * 2);
      c.fill();
    }
    c.restore();
    c.fillStyle = '#e8dfc8';
    c.beginPath();
    c.moveTo(S / 2, S / 2 - 5);
    c.lineTo(S / 2 - 4, S / 2 + 4);
    c.lineTo(S / 2 + 4, S / 2 + 4);
    c.closePath();
    c.fill();
    c.restore();
  }

  drawFullMap(canvas, game) {
    const c = canvas.getContext('2d');
    const S = canvas.width;
    const p = game.player;
    c.fillStyle = '#06060a';
    c.fillRect(0, 0, S, S);
    c.imageSmoothingEnabled = true;
    c.globalAlpha = 0.95;
    c.drawImage(this.mapCanvas, 0, 0, S, S);
    c.globalAlpha = 1;

    // 未踏エリアを暗くする
    const cell = 48;
    const px = S / WORLD_SPAN;
    c.fillStyle = 'rgba(4,4,7,.70)';
    const step = Math.ceil(cell * px) + 1;
    for (let z = -WORLD_SPAN / 2; z < WORLD_SPAN / 2; z += cell) {
      for (let x = -WORLD_SPAN / 2; x < WORLD_SPAN / 2; x += cell) {
        const key = `${Math.floor(x / cell)},${Math.floor(z / cell)}`;
        if (game.visited.has(key)) continue;
        c.fillRect(Math.floor((x + WORLD_SPAN / 2) * px), Math.floor((z + WORLD_SPAN / 2) * px), step, step);
      }
    }

    const toXY = (wx, wz) => [(wx + WORLD_SPAN / 2) * px, (wz + WORLD_SPAN / 2) * px];

    // 街道
    c.strokeStyle = 'rgba(190,160,110,.30)';
    c.lineWidth = 1.2;
    c.beginPath();
    for (const r of game.world.roads) {
      const [x0, y0] = toXY(r.x0, r.z0);
      const [x1, y1] = toXY(r.x1, r.z1);
      c.moveTo(x0, y0); c.lineTo(x1, y1);
    }
    c.stroke();

    // 地方名
    c.font = '11px sans-serif';
    c.textAlign = 'center';
    for (const R of REGIONS) {
      const [x, y] = toXY(R.cx, R.cz);
      c.fillStyle = 'rgba(232,223,200,.34)';
      c.fillText(R.name, x, y);
    }

    // POI
    for (const poi of game.pois) {
      if (!poi.discovered) continue;
      const [x, y] = toXY(poi.x, poi.z);
      c.fillStyle = POI_COLOR[poi.type] || '#999';
      const s = poi.type === 'boss' ? 5 : poi.type === 'village' ? 4.5 : 3.5;
      c.beginPath();
      if (poi.type === 'shrine') {
        c.moveTo(x, y - s); c.lineTo(x + s, y); c.lineTo(x, y + s); c.lineTo(x - s, y);
      } else if (poi.type === 'tower') {
        c.moveTo(x, y - s); c.lineTo(x + s, y + s); c.lineTo(x - s, y + s);
      } else {
        c.rect(x - s / 2, y - s / 2, s, s);
      }
      c.closePath(); c.fill();
      if (poi.type === 'village' || poi.type === 'boss') {
        c.fillStyle = 'rgba(232,223,200,.6)';
        c.font = '9px sans-serif';
        c.fillText(poi.name, x, y - s - 3);
      }
    }

    // 自機
    const [ax, ay] = toXY(p.x, p.z);
    c.fillStyle = '#fff';
    c.beginPath(); c.arc(ax, ay, 4, 0, Math.PI * 2); c.fill();
    c.strokeStyle = 'rgba(255,255,255,.6)';
    c.lineWidth = 1;
    c.beginPath();
    c.moveTo(ax, ay);
    c.lineTo(ax + Math.sin(p.yaw) * 11, ay + Math.cos(p.yaw) * 11);
    c.stroke();

    if (this.mapRow < MAP_SIZE) {
      c.fillStyle = 'rgba(232,223,200,.5)';
      c.font = '10px sans-serif';
      c.fillText(`地図を描画中… ${Math.round(this.mapRow / MAP_SIZE * 100)}%`, S / 2, S - 12);
    }
  }
}

const POI_COLOR = {
  shrine: '#e8c86a', village: '#8fd0e8', camp: '#e07050', ruin: '#b0a898',
  grave: '#a68ad0', tower: '#90d090', boss: '#ff6a4a', crystal: '#7fb0ff',
  stones: '#c0a8e0', farm: '#c8c058', battlefield: '#d08878',
  mine: '#b08850', wreck: '#78b0b8', hermit: '#a0c890',
};
const ROLL_LABEL = { fast: '軽装', normal: '中装', heavy: '重装', over: '重量超過' };

export { clamp, clamp01, lerp };
