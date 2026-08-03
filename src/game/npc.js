// NPC：村人・商人・鍛冶屋・クエスト提供者と会話ツリー

import { Actor, TEAM } from './actor.js';
import { makeRng } from '../core/noise.js';
import { TAU } from '../core/math.js';
import { makeContext, pickLine } from './dialogue.js';

const FIRST = ['エルダ', 'ミラ', 'ヨナス', 'カイ', 'セシル', 'ロウ', 'ハンナ', 'ギルド', 'テオ', 'ノラ',
  'ヴィム', 'サラ', 'オズ', 'リーヴ', 'マレン', 'ドラン'];
const TITLES = {
  elder: '長老', merchant: '行商人', smith: '鍛冶屋', herbalist: '薬師',
  hunter: '狩人', priest: '司祭', villager: '村人', innkeeper: '宿の主',
};

const ROLE_TINT = {
  elder: [0.55, 0.50, 0.42], merchant: [0.45, 0.32, 0.52], smith: [0.42, 0.30, 0.24],
  herbalist: [0.32, 0.46, 0.32], hunter: [0.36, 0.32, 0.22], priest: [0.78, 0.76, 0.70],
  villager: [0.48, 0.44, 0.38], innkeeper: [0.52, 0.38, 0.30],
};

/**
 * 一日の行動表。[開始時刻, 持ち場, 動作]。
 * その時刻を過ぎた「最後の」項目が有効になる。先頭は必ず 0 時から始める。
 *
 * 宿の主だけは夜通し起きていて、明け方に寝る。
 * 誰も彼もが同時に寝てしまうと、夜に村へ着いた者が詰むためでもある。
 */
const SCHEDULES = {
  elder: [[0, 'home', 'sleep'], [7.0, 'well', 'idle'], [8.5, 'square', 'talk'],
    [12.5, 'fire', 'eat'], [14.0, 'square', 'talk'], [18.5, 'fire', 'talk'], [22.0, 'home', 'sleep']],
  merchant: [[0, 'home', 'sleep'], [6.5, 'well', 'idle'], [8.0, 'work', 'work'],
    [12.5, 'fire', 'eat'], [13.5, 'work', 'work'], [19.0, 'fire', 'talk'], [22.0, 'home', 'sleep']],
  smith: [[0, 'home', 'sleep'], [6.0, 'well', 'idle'], [7.0, 'work', 'work'],
    [12.5, 'fire', 'eat'], [13.5, 'work', 'work'], [19.5, 'fire', 'talk'], [22.5, 'home', 'sleep']],
  herbalist: [[0, 'home', 'sleep'], [6.5, 'field', 'work'], [12.5, 'fire', 'eat'],
    [13.5, 'field', 'work'], [17.5, 'work', 'work'], [21.0, 'home', 'sleep']],
  hunter: [[0, 'home', 'sleep'], [5.0, 'gate', 'idle'], [6.0, 'field', 'work'],
    [16.0, 'gate', 'work'], [19.0, 'fire', 'talk'], [22.0, 'home', 'sleep']],
  priest: [[0, 'home', 'sleep'], [5.5, 'work', 'pray'], [12.5, 'fire', 'eat'],
    [13.5, 'work', 'pray'], [19.0, 'work', 'pray'], [22.0, 'home', 'sleep']],
  innkeeper: [[0, 'work', 'work'], [3.5, 'home', 'sleep'], [8.5, 'work', 'work']],
  villager: [[0, 'home', 'sleep'], [6.5, 'well', 'work'], [9.0, 'field', 'work'],
    [12.5, 'fire', 'eat'], [13.5, 'field', 'work'], [18.0, 'fire', 'talk'], [21.5, 'home', 'sleep']],
};

/** 持ち場に着いてからの散らばり方（同じ点に重ならないように） */
const SPREAD = { home: 0, work: 1.5, well: 1.7, fire: 2.3, square: 3.4, field: 3.0, gate: 2.0 };

export const ACTIVITY_LABEL = {
  sleep: '眠っている', work: '働いている', pray: '祈っている',
  eat: '食べている', talk: '話し込んでいる', idle: '',
};

export class NPC extends Actor {
  constructor(opts) {
    super({
      rig: 'humanoid', team: TEAM.NEUTRAL, radius: 0.42, height: 1.8,
      tint: ROLE_TINT[opts.role] || ROLE_TINT.villager,
      x: opts.x, y: opts.y, z: opts.z, yaw: opts.yaw || 0,
      hp: 99999, poise: 99999,
    });
    this.role = opts.role;
    this.npcName = opts.name;
    this.title = TITLES[opts.role] || '村人';
    this.poi = opts.poi;
    this.homeX = opts.x; this.homeZ = opts.z;
    this.wanderT = Math.random() * 4;
    this.wanderAngle = Math.random() * TAU;
    this.moving = false;
    this.talking = false;
    this.interactLabel = '話す';
    this.weaponModel = opts.role === 'smith' ? 'w_axe' : null;

    // ---- 一日の行動 ----
    this.spots = opts.spots || null;
    // 全員が同時刻に動き出すと行列に見えるので、各自 ±20 分ずらす
    this.shift = (opts.shift ?? 0);
    this.postAngle = opts.postAngle ?? Math.random() * TAU;
    this.entryIdx = -1;
    this.activity = 'idle';
    this.spotKey = 'home';
    this.indoors = false;        // 家の中にいる間は描画も会話もしない
    this.arrived = false;
    this.target = null;
    this.driftT = 0;
    this.drift = [0, 0];
    this.stuck = 0;
    this.detour = 0;
  }

  /**
   * 進む向きを決める。経路探索は持たないので、一歩先を扇状に試して
   * 「進めて、かつ目的地に近づく」向きを選ぶ。
   * 角で左右に振れないよう、一度選んだ側は変えにくくしてある。
   */
  steer(game, dx, dz, probe) {
    const base = Math.atan2(dx, dz);
    const sx = this.x, sz = this.z;
    const tx = this.target.x, tz = this.target.z;
    const test = (off) => {
      this.x = sx; this.z = sz;
      const a = base + off;
      this.tryMove(game, sx + Math.sin(a) * probe, sz + Math.cos(a) * probe);
      return { moved: Math.hypot(this.x - sx, this.z - sz),
        d: Math.hypot(this.x - tx, this.z - tz) };
    };
    // まっすぐ行けるならそれでいい
    if (test(0).moved > probe * 0.80) { this.x = sx; this.z = sz; this.detour = 0; return 0; }
    let best = null, bestScore = Infinity;
    for (const off of [0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 2.0, -2.0, 2.6, -2.6]) {
      const r = test(off);
      if (r.moved < probe * 0.55) continue;              // その向きは塞がれている
      let score = r.d + Math.abs(off) * 0.25;
      // 迂回する側を毎フレーム変えると、壁の前で左右に揺れて進まない
      if (this.detour !== 0 && Math.sign(off) !== Math.sign(this.detour)) score += 1.2;
      if (score < bestScore) { bestScore = score; best = off; }
    }
    this.x = sx; this.z = sz;
    this.detour = best ?? 0;
    return this.detour;
  }

  get table() { return SCHEDULES[this.role] || SCHEDULES.villager; }

  /** いま有効な行動表の項目 */
  scheduleIndex(hour) {
    const tab = this.table;
    let idx = 0;
    for (let i = 0; i < tab.length; i++) {
      if (hour >= tab[i][0] + (i === 0 ? 0 : this.shift)) idx = i;
    }
    return idx;
  }

  /** 持ち場を決める。遠くにいる間は歩かせず、直接そこへ置く */
  setEntry(i, game) {
    const [, key, act] = this.table[i];
    this.entryIdx = i;
    this.activity = act;
    this.spotKey = key;
    const s = (this.spots && this.spots[key]) || (this.spots && this.spots.home)
      || { x: this.homeX, z: this.homeZ };
    const r = SPREAD[key] ?? 1.5;
    const bk = game?.blockers;
    let tx = s.x + Math.cos(this.postAngle) * r;
    let tz = s.z + Math.sin(this.postAngle) * r;
    if (key === 'home') {
      // 戸口は村の中心を向いた側に取る。家は中心を向いて建っているので、
      // 裏手に回られると隣家との隙間に挟まって帰れなくなる
      const c = this.spots?.center || s;
      const ang = Math.atan2(c.x - s.x, c.z - s.z);
      const box = bk && bk.at(s.x, s.z);
      const reach = box ? Math.max(box.hx, box.hz) + this.radius + 0.7 : 1.0;
      tx = s.x + Math.sin(ang) * reach;
      tz = s.z + Math.cos(ang) * reach;
    }
    // 持ち場が建物の中や、建物どうしの隙間に来てしまったら外へ出す。
    // 立てない場所を目指すと、その周りを永久に回り続けることになる
    if (bk) {
      const o = [tx, tz];
      for (let k = 0; k < 4; k++) if (!bk.resolve(o[0], o[1], this.radius + 0.45, o)) break;
      tx = o[0]; tz = o[1];
    }
    this.target = { x: tx, z: tz, fx: s.x, fz: s.z };
    this.drift = [0, 0];
    this.stuck = 0;
    // プレイヤーから遠ければ、村に着いたとき既にその場に居るようにする
    const p = game?.player;
    if (p && Math.hypot(this.x - p.x, this.z - p.z) > 55) {
      this.x = this.target.x; this.z = this.target.z;
      this.y = game.groundHeight(this.x, this.z);
      this.arrived = true;
    } else this.arrived = false;
  }

  update(dt, game) {
    if (this.talking) {
      this.indoors = false;
      this.faceTowards(game.player.x, game.player.z, dt, 6);
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      return;
    }

    const idx = this.scheduleIndex(game.sky.hour);
    if (idx !== this.entryIdx) this.setEntry(idx, game);

    const t = this.target;
    const tx = t.x + this.drift[0], tz = t.z + this.drift[1];
    const dx = tx - this.x, dz = tz - this.z;
    const dist = Math.hypot(dx, dz);
    // 着いた後は少し広めに見る（持ち場での小さな動きで着離を繰り返さないように）
    const base = this.activity === 'sleep' ? 0.6 : 0.9;
    const arriveR = this.arrived ? base + 1.4 : base;
    let speed = 0;

    if (dist > arriveR) {
      // 持ち場が遠いほど急ぐ。寝に帰るときも足が速い
      const walk = dist > 14 || this.activity === 'sleep' ? 2.0 : 1.4;
      const x0 = this.x, z0 = this.z;
      // 探査は実際の歩幅で行う。長い距離で試すと、壁沿いに滑った分を
      // 「進めた」と誤判定して、隅に挟まったまま前を向き続けることになる
      const off = this.steer(game, dx, dz, walk * dt);
      const a = Math.atan2(dx, dz) + off;
      const mx = Math.sin(a), mz = Math.cos(a);
      this.faceTowards(this.x + mx, this.z + mz, dt, 4);
      this.moveOnGround(dt, game, mx, mz, walk);
      speed = walk;
      this.arrived = false;
      const moved = Math.hypot(this.x - x0, this.z - z0);
      if (moved < walk * dt * 0.45) this.stuck += dt;
      else this.stuck = Math.max(0, this.stuck - dt);
    } else {
      if (!this.arrived) { this.arrived = true; this.driftT = 0; }
      // 持ち場では中心（かがり火・井戸・仕事場）の方を向いて立つ
      if (this.activity !== 'sleep') this.faceTowards(t.fx, t.fz, dt, 2);
      // 立ち尽くさないよう、持ち場のまわりで小さく動く
      this.driftT -= dt;
      if (this.driftT <= 0) {
        this.driftT = 5 + Math.random() * 8;
        const a = Math.random() * TAU, r = Math.random() * 1.3;
        this.drift = this.activity === 'sleep' ? [0, 0] : [Math.cos(a) * r, Math.sin(a) * r];
      }
    }

    // 眠っている間は家の中。壁の中に立たせたままにせず、描画も会話も止める。
    // 途中で立ち往生しただけの者を消してしまわないよう、戸口に着いたことを距離で見る
    this.indoors = this.activity === 'sleep' && dist <= arriveR + 0.6;
    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);
  }

  /** いま何をしているか（会話やプロンプトに使う） */
  activityLabel() { return ACTIVITY_LABEL[this.activity] || ''; }

  /** 会話ツリーのルートノードを返す */
  dialogue(game) {
    const p = game.player;
    const q = game.quests;
    switch (this.role) {
      case 'elder': {
        const opts = [];
        if (!q.flags.talkedElder) {
          return {
            text: `……よくぞ生きて辿り着いた、灰の落人よ。\nこの地は「残響」に蝕まれている。深淵の王ノクトゥルヌスが世界の記憶を喰らい始めたのだ。`,
            options: [
              {
                label: '残響とは何だ？',
                next: {
                  text: '死者の想いの残り火だ。お前が敵を斃して集めるあの光——あれが残響。篝火で己の器に注げば、力になる。',
                  options: [{
                    label: '私は何をすべきだ',
                    action: () => { q.flags.talkedElder = true; q.start('main'); },
                    next: {
                      text: 'まずは三つの標——森、灰燼、黄金の地を統べる者どもを断て。\nその二つを断てば、蒼天嶺の竜が目を覚ます。竜の鍵なくして王の門は開かぬ。',
                      options: [{ label: '承知した', end: true }],
                    },
                  }],
                },
              },
              {
                label: 'なぜ私が',
                action: () => { q.flags.talkedElder = true; q.start('main'); },
                next: {
                  text: '死してなお灰にならぬ者——それがお前だ。何度斃れても戻ってくる。それは呪いであり、唯一の希望でもある。',
                  options: [{ label: '……行こう', end: true }],
                },
              },
            ],
          };
        }
        if (q.isActive('main')) {
          opts.push({ label: '現状を確認する', next: { text: q.stageText('main', game), options: [{ label: '分かった', end: true }] } });
        }
        // 亡妻の指輪（選択のある依頼）
        if (!q.state.relic && q.flags.talkedElder) {
          opts.push({
            label: '頼みたいことは無いか',
            action: () => q.start('relic'),
            next: {
              text: '……ひとつだけ。妻を地下墓所に葬った。指輪を嵌めたままだ。\n主が居座って以来、誰も近づけぬ。取り戻してくれるなら、礼はする。',
              options: [{ label: '探してみよう', end: true }],
            },
          });
        } else if (q.isActive('relic') && q.stageOf('relic') === 1) {
          opts.push({
            label: '指輪を差し出す',
            next: {
              text: 'これは……ああ、間違いない。\n……いや、待て。お前が持っていくこともできる。魔よけとして、悪くない品だ。どうする。',
              options: [
                {
                  label: '返す（当然だ）',
                  action: () => { q.flags.relicAnswer = 'return'; },
                  next: { text: '……ありがとう。この鎖帷子は、妻が私に遺したものだ。持っていけ。もう寒くはないそうだ。', options: [{ label: '受け取る', end: true }] },
                },
                {
                  label: '貰っておく',
                  action: () => { q.flags.relicAnswer = 'keep'; },
                  next: { text: '……そうか。いや、責めはせん。生きている者が使う方がいい。\n約束の礼だ。少ないがな。', options: [{ label: '……', end: true }] },
                },
              ],
            },
          });
        }
        opts.push({ label: 'この地について', next: { text: this.lore(game), options: [{ label: 'なるほど', end: true }] } });
        opts.push({ label: '去る', end: true });
        return { text: this.greeting(game), options: opts };
      }

      case 'merchant': {
        const opts = [{ label: '商品を見る', action: () => game.ui.openShop(this) }];
        const here = this.poi?.id;
        if (!q.state.courier) {
          opts.push({
            label: '運ぶものはあるか',
            action: () => { q.start('courier'); q.flags.courierFrom = here; },
            next: {
              text: 'この手紙を、別の集落まで。誰でもいい、そこの商人か宿の主に渡してくれ。\n街道は物騒でね、私はもう歩きたくない。',
              options: [{ label: '預かった', end: true }],
            },
          });
        } else if (q.isActive('courier') && q.flags.courierFrom && q.flags.courierFrom !== here) {
          opts.push({
            label: '手紙を渡す',
            action: () => { q.flags.letterDelivered = true; },
            next: { text: 'これは……あいつの字だ。まだ生きていたのか。\n礼だ、受け取ってくれ。街道を歩ける人間は貴重だからな。', options: [{ label: '受け取る', end: true }] },
          });
        }
        opts.push({ label: '噂を聞く', next: { text: this.rumor(game), options: [{ label: '礼を言う', end: true }] } });
        opts.push({ label: '去る', end: true });
        return { text: this.greeting(game), options: opts };
      }

      case 'smith': {
        const opts = [{ label: '武器を強化する', action: () => game.ui.openSmith(this) }];
        if (!q.state.smith) {
          opts.push({
            label: '仕事はあるか',
            action: () => q.start('smith'),
            next: { text: '銀鉱石が要る。二つ持ってきてくれれば、俺の打った剣をやろう。', options: [{ label: '引き受けた', end: true }] },
          });
        } else if (q.isActive('smith') && q.stageOf('smith') === 1) {
          opts.push({
            label: '銀鉱石を渡す',
            action: () => {
              p.inventory.ore_silver -= 2;
              q.flags.smithTurnedIn = true;
            },
            next: { text: 'よくやった。これが俺の打った長剣だ。手入れは怠るなよ。', options: [{ label: '感謝する', end: true }] },
          });
        }
        if (q.isDone('smith') && !q.state.smith2) {
          opts.push({
            label: 'もっと良い鉄はあるか',
            action: () => q.start('smith2'),
            next: {
              text: '……ある。だが銀じゃ届かん。「古き欠片」だ。\nひとつでいい。持ってこられたら、俺が生涯で一本しか打てん大剣をやる。',
              options: [{ label: '探してくる', end: true }],
            },
          });
        } else if (q.isActive('smith2') && q.stageOf('smith2') === 1) {
          opts.push({
            label: '古き欠片を渡す',
            action: () => { p.inventory.shard_ancient -= 1; q.flags.smith2TurnedIn = true; },
            next: { text: '……本当に持ってきやがった。\n三日かかる。いや、今のあんたなら待てんだろう。持っていけ、灰鉄の大剣だ。', options: [{ label: '受け取る', end: true }] },
          });
        }
        opts.push({ label: '去る', end: true });
        return { text: this.greeting(game), options: opts };
      }

      case 'herbalist': {
        const opts = [];
        if (!q.state.herbs) {
          opts.push({
            label: '手伝えることは？',
            action: () => q.start('herbs'),
            next: { text: '湿原に咲く血赤花を五つ。あれがなければ薬が作れないの。', options: [{ label: '探してみよう', end: true }] },
          });
        } else if (q.isActive('herbs') && q.stageOf('herbs') === 1) {
          opts.push({
            label: '血赤花を渡す',
            action: () => { p.inventory.blood_flower -= 5; q.flags.herbsTurnedIn = true; },
            next: { text: 'ありがとう！ これで村のみんなを助けられる。お礼を受け取って。', options: [{ label: 'どういたしまして', end: true }] },
          });
        }
        if (q.isDone('herbs') && !q.state.herbs2) {
          opts.push({
            label: 'まだ何か要るか',
            action: () => q.start('herbs2'),
            next: {
              text: '……ひとつ試したい薬があるの。魔力の結晶が五つ要る。\n死なない体をつくる薬じゃない。痛みを忘れない薬。あなたには要るでしょう。',
              options: [{ label: '集めてくる', end: true }],
            },
          });
        } else if (q.isActive('herbs2') && q.stageOf('herbs2') === 1) {
          opts.push({
            label: '結晶を渡す',
            action: () => { p.inventory.crystal -= 5; q.flags.herbs2TurnedIn = true; },
            next: { text: 'できた。……これを飲めば、器が少しだけ広がる。\n生きて帰る理由が増えるってことよ。', options: [{ label: '受け取る', end: true }] },
          });
        }
        opts.push({ label: '調合を頼む', action: () => game.ui.openCraft(this) });
        opts.push({ label: '去る', end: true });
        return { text: this.greeting(game), options: opts };
      }

      case 'hunter': {
        const opts = [];
        if (!q.state.wolves) {
          opts.push({
            label: '困りごとは？',
            action: () => q.start('wolves'),
            next: { text: '狼が増えすぎた。八匹も間引けば群れは散る。頼めるか。', options: [{ label: '任せろ', end: true }] },
          });
        } else if (q.isActive('wolves') && q.stageOf('wolves') === 1) {
          opts.push({
            label: '狼を仕留めた',
            action: () => { q.flags.wolvesTurnedIn = true; },
            next: { text: '見事だ。この指輪を持っていけ、狩人の証だ。', options: [{ label: '受け取る', end: true }] },
          });
        }
        if (q.isDone('wolves') && !q.state.packlord) {
          opts.push({
            label: '群れはまだ散らないのか',
            action: () => q.start('packlord'),
            next: {
              text: '散らん。頭がいるからだ。黒いのが二匹。\nあれが吠えると群れが変わる。速くなる、怖がらなくなる。先に頭を潰せ。',
              options: [{ label: '狩ってくる', end: true }],
            },
          });
        } else if (q.isActive('packlord') && q.stageOf('packlord') === 1) {
          opts.push({
            label: '黒い二匹を仕留めた',
            action: () => { q.flags.packlordTurnedIn = true; },
            next: { text: 'ようやく森が静かになる。……この弓は親父のだ。俺より当たる。持っていけ。', options: [{ label: '受け取る', end: true }] },
          });
        }
        opts.push({
          label: '狩りの助言',
          next: {
            text: this.huntTip(game),
            options: [{ label: '覚えておく', end: true }],
          },
        });
        opts.push({ label: '去る', end: true });
        return { text: this.greeting(game), options: opts };
      }

      case 'priest': {
        const opts = [];
        if (!q.state.wraiths) {
          opts.push({
            label: '祈りの内容は？',
            action: () => q.start('wraiths'),
            next: { text: '嘆きの亡霊が六体、彷徨っている。鎮めてくれるなら、癒しの奇跡を授けよう。', options: [{ label: '引き受ける', end: true }] },
          });
        } else if (q.isActive('wraiths') && q.stageOf('wraiths') === 1) {
          opts.push({
            label: '亡霊を鎮めた',
            action: () => { q.flags.wraithsTurnedIn = true; },
            next: { text: '安らかに……。約束の奇跡だ。己の傷を癒すがよい。', options: [{ label: '感謝する', end: true }] },
          });
        }
        if (q.isDone('wraiths') && !q.state.fenwatch) {
          opts.push({
            label: '亡霊はなぜ湧く',
            action: () => q.start('fenwatch'),
            next: {
              text: '湧いているのではない。呼ばれているのだ。\n霧の奥に、かつて癒し手だった女がいる。あれを鎮めぬ限り、嘆きは尽きぬ。',
              options: [{ label: '霧へ向かう', end: true }],
            },
          });
        } else if (q.isActive('fenwatch') && q.stageOf('fenwatch') === 1) {
          opts.push({
            label: '魔女を鎮めた',
            action: () => { q.flags.fenwatchTurnedIn = true; },
            next: { text: '……霧が薄い。何年ぶりだろうな。\nこの守りの奇跡を授ける。お前の背を守るものが、もう誰もいないのだから。', options: [{ label: '感謝する', end: true }] },
          });
        }
        opts.push({ label: '教義を聞く', next: { text: this.lore(game), options: [{ label: '……', end: true }] } });
        opts.push({ label: '去る', end: true });
        return { text: this.greeting(game), options: opts };
      }

      case 'innkeeper': {
        const opts = [{
          label: '休む（HP・雫を回復）',
          action: () => {
            p.hp = p.maxHp; p.stamina = p.maxStamina; p.fp = p.maxFP;
            p.flask.hp = p.flask.hpMax; p.flask.fp = p.flask.fpMax;
            game.sky.skipToMorning();
            game.ui.toast('よく眠った');
          },
        }];
        if (!q.state.missing) {
          opts.push({
            label: '浮かない顔だな',
            action: () => q.start('missing'),
            next: {
              text: '弟が地下へ潜ったきり戻らん。宝を探すと言って、もう半年だ。\n……生きているとは思っていない。ただ、どこで終わったのかだけ知りたい。',
              options: [{ label: '探してみよう', end: true }],
            },
          });
        } else if (q.isActive('missing') && q.stageOf('missing') === 1) {
          opts.push({
            label: '弟のことだが',
            next: {
              text: '……見つけたのか。',
              options: [
                {
                  label: '骨と、開けられぬままの宝箱があった',
                  action: () => { q.flags.missingAnswer = 'truth'; },
                  next: {
                    text: '……そうか。最後まで欲張りな奴だ。\nありがとう。嘘をつかれなかったことに、礼を言う。これを持っていけ。',
                    options: [{ label: '受け取る', end: true }],
                  },
                },
                {
                  label: '別の街道で生きているらしい',
                  action: () => { q.flags.missingAnswer = 'lie'; },
                  next: {
                    text: 'そうか……そうか！ あいつらしい。\n礼だ、多めに包んだ。……いつか帰ってくるかもしれんからな。',
                    options: [{ label: '……受け取る', end: true }],
                  },
                },
              ],
            },
          });
        }
        if (q.isActive('courier') && q.flags.courierFrom && q.flags.courierFrom !== this.poi?.id) {
          opts.push({
            label: '手紙を預かっている',
            action: () => { q.flags.letterDelivered = true; },
            next: { text: 'おお、あの男からか。生きているならそう言え、まったく。\n少ないが、駄賃だ。', options: [{ label: '受け取る', end: true }] },
          });
        }
        opts.push({ label: '噂を聞く', next: { text: this.rumor(game), options: [{ label: '礼を言う', end: true }] } });
        opts.push({ label: '去る', end: true });
        return { text: this.greeting(game), options: opts };
      }

      default: {
        // 一般村人：世間話に加え、噂も持っている
        return {
          text: this.smalltalk(game),
          options: [
            { label: '噂を聞く', next: { text: this.rumor(game), options: [{ label: '礼を言う', end: true }] } },
            { label: '去る', end: true },
          ],
        };
      }
    }
  }

  /**
   * この NPC が今どんな用件を持っているか。
   * 'new'    : 受けられる依頼がある
   * 'turnin' : 報告できる依頼がある
   * null     : 特に用は無い
   */
  questMark(game) {
    const q = game.quests;
    const ready = (id) => q.isActive(id) && q.stageOf(id) === 1;
    const fresh = (id, cond = true) => !q.state[id] && cond;
    switch (this.role) {
      case 'elder':
        if (!q.flags.talkedElder) return 'new';
        if (ready('relic')) return 'turnin';
        if (fresh('relic')) return 'new';
        return null;
      case 'merchant':
        if (q.isActive('courier') && q.flags.courierFrom
          && q.flags.courierFrom !== this.poi?.id) return 'turnin';
        if (fresh('courier')) return 'new';
        return null;
      case 'smith':
        if (ready('smith') || ready('smith2')) return 'turnin';
        if (fresh('smith') || fresh('smith2', q.isDone('smith'))) return 'new';
        return null;
      case 'herbalist':
        if (ready('herbs') || ready('herbs2')) return 'turnin';
        if (fresh('herbs') || fresh('herbs2', q.isDone('herbs'))) return 'new';
        return null;
      case 'hunter':
        if (ready('wolves') || ready('packlord')) return 'turnin';
        if (fresh('wolves') || fresh('packlord', q.isDone('wolves'))) return 'new';
        return null;
      case 'priest':
        if (ready('wraiths') || ready('fenwatch')) return 'turnin';
        if (fresh('wraiths') || fresh('fenwatch', q.isDone('wraiths'))) return 'new';
        return null;
      case 'innkeeper':
        if (ready('missing')) return 'turnin';
        if (q.isActive('courier') && q.flags.courierFrom
          && q.flags.courierFrom !== this.poi?.id) return 'turnin';
        if (fresh('missing')) return 'new';
        return null;
      default:
        return null;
    }
  }

  /** 状況（時刻・天候・進行・こちらの様子）に反応した台詞を引く */
  say(pool, game, drift = 0) {
    const ctx = makeContext(this, game);
    const salt = (this.id | 0) + Math.floor(game.time.now * 0.08) + drift;
    return pickLine(pool, ctx, salt) || '……。';
  }
  greeting(game) {
    return this.say(`greet_${this.role}`, game) || this.say('smalltalk', game);
  }
  huntTip(game) {
    const tips = [
      '獣は正面から来る。焦らず、引きつけて転がれ。背後を取れれば一撃で終わる。',
      '囲まれたら、殴りかかってくるのは二匹か三匹までだ。残りは回っている。そいつらの背中を狙え。',
      '振りかぶって止める奴がいる。あれは誘いだ。転がるのは刃が来てからでいい。',
      '同じ技でも溜めが毎回違う。数えるな、見ろ。',
      '大盾は正面を通さん。回り込むか、体当たりで態勢を崩せ。',
      '術者を先に黙らせろ。死人を起こす奴は、放っておくと切りがない。',
      '長柄は間合いの外から刺す。踏み込むか、下がりきるかだ。中途半端が一番死ぬ。',
    ];
    return tips[(this.id + Math.floor(game.player.kills / 7)) % tips.length];
  }
  lore(game) { return this.say('lore', game, 3); }
  rumor(game) { return this.say('rumor', game, 7); }
  smalltalk(game) { return this.say('smalltalk', game, 11); }
}

/**
 * その集落の「場所」を、実際に建っている構造物から拾う。
 * 小さな集落には市も納屋も無いので、順に代わりを探す。
 */
function villageSpots(poi, rng) {
  const st = poi.structures || [];
  const of = (...models) => st.filter((s) => models.includes(s.model));
  const one = (arr, fb) => (arr.length ? arr[(rng() * arr.length) | 0] : fb);
  const center = { x: poi.x, z: poi.z };
  const xz = (s) => (s ? { x: s.x, z: s.z } : center);
  return {
    houses: of('house_a', 'house_b', 'hut', 'tent'),
    center,
    well: xz(one(of('well'), null)),
    fire: xz(one(of('campfire'), null)),
    square: center,
    stall: xz(one(of('stall'), one(of('cart'), null))),
    statue: xz(one(of('statue'), one(of('banner'), one(of('signpost'), null)))),
    barn: xz(one(of('barn'), one(of('cart'), null))),
    R: poi.plateauR || 22,
  };
}

/** 役割ごとの仕事場 */
function workSpot(role, sp, home) {
  switch (role) {
    case 'merchant': return sp.stall;
    case 'smith': return sp.barn;
    case 'priest': return sp.statue;
    case 'innkeeper': return home || sp.center;
    case 'herbalist': return sp.well;
    case 'hunter': return sp.center;
    case 'elder': return sp.square;
    default: return sp.well;
  }
}

/** 村 POI に NPC を配置 */
export function populateVillage(poi, world) {
  const rng = makeRng((poi.x * 7919 + poi.z * 104729 + 13) | 0);
  const sp = villageSpots(poi, rng);

  /** その NPC 用の持ち場一式を組む */
  const spotsFor = (role, home, bearing) => {
    const R = sp.R;
    const out = {
      home: home || sp.center,
      well: sp.well, fire: sp.fire, square: sp.square, center: sp.center,
      work: workSpot(role, sp, home),
      // 畑仕事・狩りは集落の外。各自ちがう方角へ出る
      field: { x: poi.x + Math.cos(bearing) * (R + 9), z: poi.z + Math.sin(bearing) * (R + 9) },
      gate: { x: poi.x + Math.cos(bearing) * (R * 0.85), z: poi.z + Math.sin(bearing) * (R * 0.85) },
    };
    return out;
  };

  // 隠者の庵には一人だけ住んでいる
  if (poi.type === 'hermit') {
    const solo = ['herbalist', 'hunter', 'merchant'][(rng() * 3) | 0];
    const a = rng() * TAU, r = 3.5 + rng() * 2;
    const x = poi.x + Math.cos(a) * r, z = poi.z + Math.sin(a) * r;
    const home = sp.houses.length ? { x: sp.houses[0].x, z: sp.houses[0].z } : sp.center;
    return [new NPC({
      role: solo, name: FIRST[(rng() * FIRST.length) | 0],
      x, y: world.height(x, z), z, yaw: -a + Math.PI, poi,
      spots: spotsFor(solo, home, a), shift: (rng() - 0.5) * 0.6, postAngle: rng() * TAU,
    })];
  }

  const roles = ['elder', 'merchant', 'smith', 'herbalist', 'hunter', 'priest', 'innkeeper'];
  const services = poi.services || ['merchant'];
  const chosen = ['merchant'];
  if (services.includes('smith')) chosen.push('smith');
  if (services.includes('inn')) chosen.push('innkeeper');
  if (poi.tag === 'hub') chosen.push('elder', 'herbalist', 'hunter', 'priest');
  else {
    for (const r of roles) {
      if (chosen.length >= 4) break;
      if (!chosen.includes(r) && r !== 'elder' && rng() < 0.5) chosen.push(r);
    }
  }
  const npcs = [];
  // 家は一軒ずつ割り当てる。同じ家で寝る二人が出ないように
  let houseAt = 0;
  const nextHome = () => {
    if (!sp.houses.length) return sp.center;
    const h = sp.houses[houseAt % sp.houses.length];
    houseAt++;
    return { x: h.x, z: h.z };
  };

  const all = [...chosen];
  const extra = poi.size >= 9 ? 3 : 1;
  for (let i = 0; i < extra; i++) all.push('villager');

  all.forEach((role, i) => {
    const a = (i / all.length) * TAU + rng() * 0.6;
    const r = 6 + rng() * 7;
    const x = poi.x + Math.cos(a) * r;
    const z = poi.z + Math.sin(a) * r;
    const home = nextHome();
    npcs.push(new NPC({
      role,
      name: role === 'elder' ? 'エルダ'
        : role === 'villager' ? FIRST[(rng() * FIRST.length) | 0]
          : FIRST[1 + ((rng() * (FIRST.length - 1)) | 0)],
      x, y: world.height(x, z), z, yaw: -a + Math.PI, poi,
      spots: spotsFor(role, home, a),
      shift: (rng() - 0.5) * 0.66,     // ±20 分
      postAngle: rng() * TAU,
    }));
  });
  return npcs;
}
