// NPC：村人・商人・鍛冶屋・クエスト提供者と会話ツリー

import { Actor, TEAM } from './actor.js';
import { makeRng } from '../core/noise.js';
import { TAU } from '../core/math.js';

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
        opts.push({ label: 'この地について', next: { text: this.lore(game), options: [{ label: 'なるほど', end: true }] } });
        opts.push({ label: '去る', end: true });
        return { text: '灰の落人よ。まだ火は消えておらぬな。', options: opts };
      }

      case 'merchant':
        return {
          text: '旅の御方、掘り出し物があるよ。残響さえあれば何でも揃う。',
          options: [
            { label: '商品を見る', action: () => game.ui.openShop(this) },
            { label: '噂を聞く', next: { text: this.rumor(game), options: [{ label: '礼を言う', end: true }] } },
            { label: '去る', end: true },
          ],
        };

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
        opts.push({ label: '去る', end: true });
        return { text: '火は正直だ。鉄も、人もな。', options: opts };
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
        opts.push({ label: '調合を頼む', action: () => game.ui.openCraft(this) });
        opts.push({ label: '去る', end: true });
        return { text: '薬草の匂いは嫌い？ 私は好きよ。生きている匂いだもの。', options: opts };
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
        opts.push({ label: '狩りの助言', next: { text: '獣は正面から来る。焦らず、引きつけて転がれ。背後を取れれば一撃で終わる。', options: [{ label: '覚えておく', end: true }] } });
        opts.push({ label: '去る', end: true });
        return { text: '足音を殺せ。獣はお前より耳がいい。', options: opts };
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
        opts.push({ label: '教義を聞く', next: { text: '死は終わりではない。残響が続く限り、我らは何度でも立ち上がる。それを呪いと呼ぶ者もいるがな。', options: [{ label: '……', end: true }] } });
        opts.push({ label: '去る', end: true });
        return { text: '灰の落人よ。祈りは届いているか。', options: opts };
      }

      case 'innkeeper':
        return {
          text: '休んでいくかい。屋根があるだけでも贅沢さ。',
          options: [
            {
              label: '休む（HP・雫を回復）',
              action: () => {
                p.hp = p.maxHp; p.stamina = p.maxStamina; p.fp = p.maxFP;
                p.flask.hp = p.flask.hpMax; p.flask.fp = p.flask.fpMax;
                game.sky.skipToMorning();
                game.ui.toast('よく眠った');
              },
            },
            { label: '噂を聞く', next: { text: this.rumor(game), options: [{ label: '礼を言う', end: true }] } },
            { label: '去る', end: true },
          ],
        };

      default:
        return {
          text: this.smalltalk(game),
          options: [{ label: '去る', end: true }],
        };
    }
  }

  lore(game) {
    const lines = [
      'かつてこの地には王がいた。名を捨て、簒奪王と呼ばれた男だ。彼が深淵に触れた日、世界は記憶を失い始めた。',
      '八つの地方には、それぞれ守り手がいた。今はもう、化物になり果てているがな。',
      '篝火は王の剣の欠片だ。あれに触れれば、死んでも戻ってこられる。',
    ];
    return lines[(game.player.kills + this.id) % lines.length];
  }
  rumor(game) {
    const lines = [
      '灰燼の荒野には、燃え尽きぬ騎士がいるという。近づく者は皆、灰になる。',
      '常闇の森の奥、木々が避ける場所に「森の主」がいる。あれは狼ではない。',
      '蒼天嶺の頂には竜が眠る。竜の鍵がなければ、王の門は開かぬそうだ。',
      '湿原の魔女は、かつて人を癒す者だった。霧に呑まれてから、誰も戻らない。',
      '見張り塔に登れば、その地方の地図が手に入る。登る価値はあるさ。',
      '宝箱は罠かもしれん。だが開けずに死ぬのも間抜けだろう？',
    ];
    return lines[(Math.floor(game.time.now * 0.2) + this.id) % lines.length];
  }
  smalltalk(game) {
    const lines = [
      'あんた、生きてるのか？ ……いや、聞くだけ野暮か。',
      '外は危ない。だが、ここも安全とは言えないな。',
      '村の外れの篝火は使えるはずだ。あそこで休むといい。',
      '子供が森に近づかないよう見ていてくれ。あそこは変わってしまった。',
      '残響が集まると、頭の中で誰かの声がする。慣れるさ。',
    ];
    return lines[(this.id + Math.floor(game.time.now * 0.1)) % lines.length];
  }
}

/** 村 POI に NPC を配置 */
export function populateVillage(poi, world) {
  const rng = makeRng((poi.x * 7919 + poi.z * 104729 + 13) | 0);
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
