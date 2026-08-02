// dialogue.js — role-flavoured, context-aware NPC conversations.

import { makeRNG } from '../core/util.js';
import { MAIN_CHAPTERS } from './quests.js';

const SMALLTALK = {
  villager: [
    '畑の実りは悪くない。魔物さえ出なければ、な。',
    '街道はもう安全かい？　行商人が来なくて困ってるんだ。',
    '夜になると森から嫌な音が聞こえる。近づかん方がいい。',
    'あんた、旅の人かい。ゆっくりしていきな。',
    '西の丘に古い祠があるって話だ。触れると力が湧くとか。',
    'うちの子が「空に赤い光が見えた」なんて言うんだ。気味が悪いよ。',
  ],
  guard: [
    'この街は俺が守る。悪さはするなよ。',
    '野盗どもが街道に居座っている。腕に覚えがあるなら頼みたい。',
    '日が落ちたら門の外へ出るな。骨の兵が起き上がる。',
    '灰の荒野……あそこだけは行くな。竜がいる。',
  ],
  merchant: [
    'いらっしゃい！　旅の必需品ならうちで揃うよ。',
    '仕入れが滞っていてね。街道の安全があってこその商売さ。',
    '掘り出し物があるかもしれないよ、見ていきな。',
  ],
  smith: [
    '鉄は正直だ。打てば打つだけ応えてくれる。',
    '武具の強化なら任せろ。鉱石を持ってきな。',
    'いい得物を持ってるじゃないか。手入れは怠るなよ。',
  ],
  alchemist: [
    '薬草と月光花……素材さえあれば奇跡も作れる。',
    '月光花は夜の暗い森にしか咲かない。気をつけて摘むんだよ。',
    '調合してほしいものがあるなら言っておくれ。',
  ],
  innkeeper: [
    '一晩ゆっくり休んでいきな。傷も疲れも取れるよ。',
    '旅の話を聞かせておくれ。退屈しのぎにね。',
    '暖炉は焚いてある。冷えた体を温めていきな。',
  ],
  guildmaster: [
    '冒険者ギルドへようこそ。依頼はいつでも受け付けている。',
    '実績を積めば、より難しい依頼も回せる。',
    '無理はするな。生きて帰ってこそ冒険者だ。',
  ],
  elder: [
    '古い言い伝えでは、四つの欠片が竜を封じたという。',
    'この地には昔、光の柱が立っていたそうな。',
    '若いの、焦ることはない。道は必ず開ける。',
  ],
  priest: [
    '女神の加護があらんことを。',
    '祠に祈れば、遠き地へも導かれるでしょう。',
    '死者が起き上がるのは、封印が緩んでいる証。',
  ],
  child: [
    'ねえ、その剣かっこいい！　ぼくも冒険者になるんだ！',
    'あっちの木の下に光るものが埋まってるんだって！',
    'かくれんぼしよ！　……あ、忙しいのか。',
  ],
};

const NIGHT_LINES = [
  'こんな時間に出歩くなんて、命知らずだね。',
  '夜の魔物は昼より強い。気をつけな。',
  'もう休む時間だ。あんたも早く宿へ。',
];
const RAIN_LINES = [
  'ひどい雨だ。屋根の下に入りな。',
  '雨の日は魔物も大人しい……といいんだがね。',
];

export function greeting(npc, g) {
  const rng = makeRNG(npc.dialogueSeed + Math.floor(g.time / 120));
  const hour = g.hour();
  if (g.weather.rain > 0.4 && rng.chance(0.35)) return rng.pick(RAIN_LINES);
  if ((hour < 6 || hour > 21) && rng.chance(0.45)) return rng.pick(NIGHT_LINES);
  const pool = SMALLTALK[npc.role] || SMALLTALK.villager;
  // storyline flavour
  const ch = g.quests.chapter;
  if (rng.chance(0.3) && ch < MAIN_CHAPTERS.length) {
    const story = [
      '……近ごろ、空気が変わった。何かが目覚めようとしている。',
      '灰の荒野の空が赤い。昔語りの竜が動いたのかもしれん。',
      'あんたのような者が現れるのを、皆待っていたのかもしれんな。',
    ];
    return rng.pick(story);
  }
  return rng.pick(pool);
}

/** Build the option list for talking to an NPC. */
export function options(npc, g) {
  const out = [];
  const q = g.quests;
  if (npc.role === 'guildmaster') {
    out.push({ label: '依頼を見る', act: 'board' });
  }
  if (npc.role === 'merchant') out.push({ label: '買い物をする', act: 'shop', shop: 'shop' });
  if (npc.role === 'smith') {
    out.push({ label: '武具を買う', act: 'shop', shop: 'smith' });
    out.push({ label: '武具を強化する', act: 'upgrade' });
  }
  if (npc.role === 'alchemist') {
    out.push({ label: '薬を買う', act: 'shop', shop: 'alchemist' });
    out.push({ label: '調合する', act: 'craft', station: 'alchemist' });
  }
  if (npc.role === 'innkeeper') {
    out.push({ label: `宿に泊まる (${g.innCost()}G)`, act: 'rest' });
    out.push({ label: '食料を買う', act: 'shop', shop: 'inn' });
  }
  if (npc.role === 'priest') out.push({ label: '祈る', act: 'pray' });
  // quest turn-ins
  for (const quest of q.active) {
    if (quest.state === 'complete' && !quest.main) {
      if (quest.type === 'deliver') {
        if (npc.settlement && npc.settlement.id === quest.to) out.push({ label: `「${quest.title}」を報告`, act: 'turnin', quest });
      } else if (npc.role === 'guildmaster') {
        out.push({ label: `「${quest.title}」を報告`, act: 'turnin', quest });
      }
    }
  }
  out.push({ label: '噂を聞く', act: 'rumor' });
  out.push({ label: '別れる', act: 'close' });
  return out;
}

const RUMORS = [
  '北の山には石の巨人が眠っているらしい。',
  '砂漠の遺跡には、まだ誰も開けていない宝箱があるとか。',
  '暗い森の奥、月光花の群生地を見た者がいるそうだ。',
  '祠の光は、遠く離れた祠と繋がっていると聞く。',
  '深い迷宮の主は、光る欠片を抱えているという。',
  '灰の荒野の中心、火口の底に竜の巣がある。',
  '夜に骨の兵が湧くのは、封印が緩んでいるからだ。',
  '腕利きの鍛冶師は、霊銀鉱があれば伝説級の武具を打てるという。',
  '祠に祈れば、そこへいつでも戻れるようになる。',
  '盾を構えた直後に受け止めれば、相手の体勢を崩せる。',
];

export function rumor(g) {
  const rng = makeRNG(Math.floor(g.time * 7) ^ 0x5f3a);
  return rng.pick(RUMORS);
}
