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
  }

  update(dt, game) {
    if (this.talking) {
      this.faceTowards(game.player.x, game.player.z, dt, 6);
      this.updateAnim(dt, 0);
      this.updatePhysics(dt, game);
      return;
    }
    this.wanderT -= dt;
    let speed = 0;
    if (this.wanderT <= 0) {
      this.wanderT = 3 + Math.random() * 6;
      this.moving = Math.random() < 0.45;
      this.wanderAngle = Math.random() * TAU;
    }
    if (this.moving) {
      const tx = this.homeX + Math.sin(this.wanderAngle) * 5;
      const tz = this.homeZ + Math.cos(this.wanderAngle) * 5;
      const dx = tx - this.x, dz = tz - this.z;
      if (Math.hypot(dx, dz) > 0.8) {
        this.faceTowards(tx, tz, dt, 3);
        this.moveOnGround(dt, game, dx, dz, 1.5);
        speed = 1.5;
      } else this.moving = false;
    }
    this.updatePhysics(dt, game);
    this.updateAnim(dt, speed);
  }

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

/** 村 POI に NPC を配置 */
export function populateVillage(poi, world) {
  const rng = makeRng((poi.x * 7919 + poi.z * 104729 + 13) | 0);

  // 隠者の庵には一人だけ住んでいる
  if (poi.type === 'hermit') {
    const solo = ['herbalist', 'hunter', 'merchant'][(rng() * 3) | 0];
    const a = rng() * TAU, r = 3.5 + rng() * 2;
    const x = poi.x + Math.cos(a) * r, z = poi.z + Math.sin(a) * r;
    return [new NPC({
      role: solo, name: FIRST[(rng() * FIRST.length) | 0],
      x, y: world.height(x, z), z, yaw: -a + Math.PI, poi,
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
  chosen.forEach((role, i) => {
    const a = (i / chosen.length) * TAU + rng() * 0.6;
    const r = 6 + rng() * 7;
    const x = poi.x + Math.cos(a) * r;
    const z = poi.z + Math.sin(a) * r;
    npcs.push(new NPC({
      role,
      name: role === 'elder' ? 'エルダ' : FIRST[1 + ((rng() * (FIRST.length - 1)) | 0)],
      x, y: world.height(x, z), z, yaw: -a + Math.PI, poi,
    }));
  });
  // 一般村人
  const extra = poi.size >= 9 ? 3 : 1;
  for (let i = 0; i < extra; i++) {
    const a = rng() * TAU, r = 4 + rng() * 12;
    const x = poi.x + Math.cos(a) * r, z = poi.z + Math.sin(a) * r;
    npcs.push(new NPC({
      role: 'villager', name: FIRST[(rng() * FIRST.length) | 0],
      x, y: world.height(x, z), z, yaw: rng() * TAU, poi,
    }));
  }
  return npcs;
}
