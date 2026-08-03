// 釣り。水辺に留まる理由。
//
// 世界の 25.7% が水面なのに、岸に立っても「調べる」に何ひとつ出なかった
// （岸 30 点の半径 25m にある用は平均 0.2 件、うち 24 点はゼロ。陸は 0.63 件）。
// 魚（第33周）は跳ねるだけで、手を出す方法がなかった。
//
// 作りは戦闘と同じ形にしてある——待つ・読む・合わせる・こらえる。
//   待ち   浮きが揺れる。ときどき「前アタリ」が来る。ここで合わせると逃げられる
//   当たり 浮きが沈み、水音が鳴り、画面が小さく揺れる。猶予 0.55 秒
//   合わせ その窓の中で「調べる」を押す
//   取り込み 押している間だけ巻ける。魚が走っているあいだに巻くと糸が切れる。
//          走る前に 0.32 秒の予兆（穂先の震え）が出る。敵の予備動作と同じ扱い
//
// 何が掛かるかは魚影の濃さ（fish.js の群れの近さ）と時刻で決まる。
// 群れの居ない水では待つばかりで小魚しか出ない。

import { clamp, clamp01 } from '../core/math.js';
import { FISH_CATCH, ITEMS } from '../game/data.js';

/** 岸から糸が届く範囲 */
const CAST_MIN = 4.5;
const CAST_MAX = 15;
/** 投げ込む先に要る水深 */
const NEED_DEPTH = 1.0;
/** 当たりに合わせられる猶予。人の反応（0.25〜0.35 秒）に足りる幅を取る */
const BITE_WINDOW = 0.55;
/** 走る直前の予兆。敵の予備動作と同じ考え方 */
const TELL = 0.32;

export class Fishing {
  constructor() {
    this.state = 'idle';      // idle / cast / wait / bite / fight
    this.t = 0;
    this.bob = { x: 0, y: 0, z: 0 };
    this.from = { x: 0, y: 0, z: 0 };
    this.dip = 0;             // 浮きの沈み（m）
    this.strain = 0;          // 糸の張り 0〜1
    this.dist = 0;            // 取り込みまでの残り 0〜1
    this.catchId = null;
    this.rich = 0;
    this.nibbles = [];
    this.nib = 0;
    this.phase = 'calm';
    this.phaseT = 0;
    this.pulling = false;
    this.hinted = false;
    this.caught = { fish_small: 0, fish_big: 0, fish_moon: 0 };
    this.lost = 0;
    this.landed = 0;
  }

  get active() { return this.state !== 'idle'; }

  /* ------------------------------------------------------- 場所を探す */
  /**
   * 立っている岸から、糸の届く水面を探す。
   * カメラの向いているほうを優先し、見つからなければ周りを見る。
   */
  findSpot(game) {
    const p = game.player;
    if (game.dungeon || p.swimming || p.dead) return null;
    if (p.y < game.seaLevel - 0.2) return null;      // 水没している
    const yaw = game.camera ? game.camera.yaw : p.yaw;
    let best = null, bestScore = -1;
    for (let a = 0; a < 24; a++) {
      // カメラ正面から左右へ広げていく
      const off = (a % 2 ? 1 : -1) * Math.ceil(a / 2) * 0.26;
      const dir = yaw + off;
      const sx = Math.sin(dir), sz = Math.cos(dir);
      for (let r = CAST_MIN; r <= CAST_MAX; r += 1.5) {
        const x = p.x + sx * r, z = p.z + sz * r;
        const depth = game.seaLevel - game.world.height(x, z);
        if (depth < NEED_DEPTH) continue;
        // 正面ほど、また深いほど良い
        const score = (1 - Math.abs(off) / 3.2) * 2 + clamp01(depth / 3);
        if (score > bestScore) { bestScore = score; best = { x, z, depth, r }; }
        break;                                       // その方角の最初の水面でよい
      }
      if (best && Math.abs(off) < 0.01) break;       // 真正面に水があるならそれ
    }
    return best;
  }

  /** 竿を持っていて、水辺に立っているか */
  canStart(game) {
    if (this.active) return null;
    const p = game.player;
    if (!p.canAct || !p.canAct()) return null;
    return this.findSpot(game);
  }

  hasRod(game) { return (game.player.inventory.fishing_rod || 0) > 0; }

  /* --------------------------------------------------------- 投げる */
  start(game, spot) {
    const p = game.player;
    spot = spot || this.findSpot(game);
    if (!spot || !this.hasRod(game)) return false;
    this.from.x = p.x; this.from.y = p.y + 1.35; this.from.z = p.z;
    this.bob.x = spot.x; this.bob.z = spot.z; this.bob.y = game.seaLevel;
    this.rich = game.fish ? game.fish.richnessAt(game, spot.x, spot.z) : 0;
    p.yaw = Math.atan2(spot.x - p.x, spot.z - p.z);
    this.state = 'cast';
    this.t = 0;
    this.dip = 0;
    this.strain = 0;
    this.dist = 1;
    this.catchId = null;
    this.nib = 0;
    game.audio.play('throw');
    return true;
  }

  /** 待ちの長さと前アタリを決める。魚影が濃く、朝夕であるほど早く来る */
  planBite(game) {
    const act = game.fish ? game.fish.activity(game) : 0.5;
    const base = 14 - this.rich * 8;
    this.waitDur = (base / (0.45 + act * 0.85)) * (0.7 + Math.random() * 0.6);
    // 前アタリ。魚影が薄いほど、ためらいが多い
    const n = Math.random() < 0.35 + (1 - this.rich) * 0.4 ? (Math.random() < 0.4 ? 2 : 1) : 0;
    this.nibbles = [];
    for (let i = 0; i < n; i++) {
      this.nibbles.push(0.9 + Math.random() * Math.max(0.5, this.waitDur - 1.8));
    }
    this.nibbles.sort((a, b) => a - b);
  }

  /* ----------------------------------------------------------- 更新 */
  update(dt, game) {
    if (this.state === 'idle') return;
    const p = game.player;
    if (game.dungeon || p.dead) { this.stop(game, 'quit'); return; }

    this.from.x = p.x; this.from.y = p.y + 1.35; this.from.z = p.z;
    this.t += dt;
    const press = game.input.pressed('interact');

    switch (this.state) {
      case 'cast': {
        // 糸が飛んでいるあいだ。着水で小さく水が跳ねる
        if (this.t >= 0.62) {
          this.state = 'wait';
          this.t = 0;
          this.planBite(game);
          game.fx.splash(this.bob.x, game.seaLevel, this.bob.z, 7, 0.35);
          game.audio.play('splash', { pitch: 1.6 + Math.random() * 0.2, x: this.bob.x, z: this.bob.z });
        }
        break;
      }

      case 'wait': {
        // 前アタリ。浮きが小さく揺れるだけで、まだ食っていない
        while (this.nibbles.length && this.t >= this.nibbles[0]) {
          this.nibbles.shift();
          this.nib = 0.34;
          game.audio.play('ui_move', { pitch: 0.7, x: this.bob.x, z: this.bob.z });
        }
        if (this.nib > 0) this.nib = Math.max(0, this.nib - dt);
        this.dip = this.nib > 0 ? 0.07 : 0;

        if (press) {
          // 前アタリで合わせると持っていかれる。何も無いのに引けば、ただ糸が戻る
          if (this.nib > 0) this.miss(game, '早い —— 逃げられた');
          else this.stop(game, 'quit');
          return;
        }
        if (this.t >= this.waitDur) {
          this.state = 'bite';
          this.t = 0;
          this.catchId = this.pickCatch(game);
          this.dip = 0.26;
          game.audio.play('splash', { pitch: 0.75, x: this.bob.x, z: this.bob.z });
          game.fx.splash(this.bob.x, game.seaLevel, this.bob.z, 9, 0.4);
          game.camera.kick(this.bob.x - p.x, this.bob.z - p.z, 0.10);
        }
        break;
      }

      case 'bite': {
        this.dip = 0.26 + Math.sin(this.t * 26) * 0.06;
        if (press) { this.hook(game); return; }
        if (this.t >= BITE_WINDOW) { this.miss(game, '遅い —— 持っていかれた'); return; }
        break;
      }

      case 'fight': {
        this.stepFight(dt, game);
        break;
      }
    }
  }

  /** 何が掛かるか。魚影の濃さと時刻で決まる */
  pickCatch(game) {
    const h = ((game.sky.time || 0) * 24) % 24;
    const night = h < 4.8 || h > 19.2;
    let total = 0;
    const ws = [];
    for (const c of FISH_CATCH) {
      if (c.night && !night) { ws.push(0); continue; }
      if (this.rich < c.rich) { ws.push(0); continue; }
      // 良いものほど、魚影が濃いところでしか出ない
      const span = Math.max(0.05, 1 - c.rich);
      const w = c.rich > 0 ? c.weight * clamp01((this.rich - c.rich) / span) : c.weight;
      ws.push(w);
      total += w;
    }
    if (total <= 0) return 'fish_small';
    let r = Math.random() * total;
    for (let i = 0; i < FISH_CATCH.length; i++) {
      r -= ws[i];
      if (r <= 0) return FISH_CATCH[i].id;
    }
    return 'fish_small';
  }

  spec() {
    return FISH_CATCH.find((c) => c.id === this.catchId) || FISH_CATCH[0];
  }

  /* --------------------------------------------------------- 合わせ */
  hook(game) {
    const p = game.player;
    this.state = 'fight';
    this.t = 0;
    this.dist = 1;
    this.strain = 0.18;
    this.phase = 'calm';
    this.phaseT = this.spec().calmDur * (0.6 + Math.random() * 0.6);
    this.pulling = false;
    game.audio.play('keen', { pitch: 1.1 });
    game.audio.play('splash', { pitch: 0.9, x: this.bob.x, z: this.bob.z });
    game.fx.splash(this.bob.x, game.seaLevel, this.bob.z, 14, 0.6);
    game.camera.kick(this.bob.x - p.x, this.bob.z - p.z, 0.2);
    game.time.hitstop(0.05);
  }

  /* ------------------------------------------------------- 取り込み */
  stepFight(dt, game) {
    const p = game.player;
    const c = this.spec();
    this.phaseT -= dt;
    if (this.phaseT <= 0) {
      if (this.phase === 'calm') {
        // 走る前に穂先が震える。ここが読みどころ
        this.phase = 'tell';
        this.phaseT = TELL;
        game.audio.play('ui_move', { pitch: 1.5, x: this.bob.x, z: this.bob.z });
        game.camera.kick(this.bob.x - p.x, this.bob.z - p.z, 0.06);
      } else if (this.phase === 'tell') {
        this.phase = 'run';
        this.phaseT = c.runDur * (0.75 + Math.random() * 0.5);
        game.audio.play('splash', { pitch: 0.62, x: this.bob.x, z: this.bob.z });
        game.fx.splash(this.bob.x, game.seaLevel, this.bob.z, 8, 0.45);
      } else {
        this.phase = 'calm';
        this.phaseT = c.calmDur * (0.7 + Math.random() * 0.6);
      }
    }

    // 押しているあいだだけ巻ける。スタミナも食う
    const want = game.input.down('interact');
    this.pulling = want && p.stamina > 1;
    if (this.pulling) {
      p.stamina = Math.max(0, p.stamina - 6 * dt);
      p.staminaDelay = Math.max(p.staminaDelay, 0.3);
      this.dist -= c.reel * dt;
      this.strain += (this.phase === 'run' ? c.runPull : c.calmPull) * dt;
    } else {
      this.strain -= c.relax * dt;
      if (this.phase === 'run') this.dist += 0.09 * dt;   // 走られると糸を出す
    }
    this.strain = clamp(this.strain, 0, 1.2);
    this.dist = clamp(this.dist, 0, 1.15);

    // 浮きは魚に引かれて沈み、走られるとさらに沈む
    this.dip = 0.20 + this.strain * 0.22 + (this.phase === 'run' ? 0.12 : 0);
    // 浮きを手元へ寄せる
    const k = 1 - Math.exp(-2.2 * dt);
    const tx = p.x + (this.bob.x - p.x) * clamp01(0.25 + this.dist * 0.75);
    const tz = p.z + (this.bob.z - p.z) * clamp01(0.25 + this.dist * 0.75);
    this.bob.x += (tx - this.bob.x) * k;
    this.bob.z += (tz - this.bob.z) * k;

    if (this.strain >= 1) { this.miss(game, '糸が切れた'); return; }
    if (this.dist <= 0) this.land(game);
  }

  /* ----------------------------------------------------------- 結末 */
  land(game) {
    const p = game.player;
    const id = this.catchId || 'fish_small';
    const n = id === 'fish_small' && Math.random() < 0.28 ? 2 : 1;
    p.addItem(id, n);
    this.caught[id] = (this.caught[id] || 0) + n;
    this.landed++;
    game.ui.itemGain(id, n);
    game.ui.toast(`${ITEMS[id].name}を釣り上げた`, id === 'fish_small' ? '' : 'gold');
    game.audio.play('splash', { pitch: 0.7, x: this.bob.x, z: this.bob.z });
    game.audio.play(id === 'fish_small' ? 'gather_plant' : 'chest');
    game.fx.splash(this.bob.x, game.seaLevel, this.bob.z, 20, 0.8);
    game.camera.kick(this.bob.x - p.x, this.bob.z - p.z, 0.24);
    game.time.hitstop(0.07);
    game.fish?.startle(this.bob.x, this.bob.z, 16);
    this.stop(game, 'landed');
  }

  /** 逃げられた */
  miss(game, why) {
    this.lost++;
    game.audio.play('fail');
    game.audio.play('splash', { pitch: 1.2, x: this.bob.x, z: this.bob.z });
    game.fx.splash(this.bob.x, game.seaLevel, this.bob.z, 10, 0.5);
    game.ui.toast(why);
    game.fish?.startle(this.bob.x, this.bob.z, 14);
    this.stop(game, 'miss');
  }

  stop(game, reason = 'quit') {
    if (this.state === 'idle') return;
    this.state = 'idle';
    this.t = 0;
    this.dip = 0;
    this.strain = 0;
    this.pulling = false;
    this.nibbles.length = 0;
    this.nib = 0;
    if (reason === 'quit' || reason === 'cancel') game.audio?.play('ui_off');
    game.ui?.setFishing(null);
  }

  /** HUD に渡す。釣っていないあいだは null */
  hud() {
    if (this.state === 'idle') return null;
    return {
      state: this.state,
      text: this.state === 'cast' ? '投げた'
        : this.state === 'wait' ? (this.nib > 0 ? '…触った' : '待つ')
          : this.state === 'bite' ? '来た！' : this.phase === 'run' ? '走っている' : '寄せる',
      dist: this.dist, strain: this.strain,
      warn: this.state === 'bite' || (this.state === 'fight' && this.phase !== 'calm'),
    };
  }

  /** 「調べる」に出す文言 */
  prompt() {
    switch (this.state) {
      case 'cast': return null;
      case 'wait': return '糸を上げる';
      case 'bite': return '合わせる';
      case 'fight': return '押して寄せる';
      default: return null;
    }
  }
}
