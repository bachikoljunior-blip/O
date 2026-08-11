// achievements.js — durable adventure milestones and progress calculations.

const count = (items, predicate) => (items || []).reduce((n, item) => n + (predicate(item) ? 1 : 0), 0);

export const ACHIEVEMENTS = [
  {
    id: 'journey', name: '旅立ち', icon: '✦', target: 1,
    desc: '冒険の手ほどきを終える。',
    value: (g) => g.onboarding ? 0 : 1,
  },
  {
    id: 'first_blood', name: '初陣', icon: '剣', target: 1,
    desc: '初めて魔物を倒す。',
    value: (g) => g.player?.kills || 0,
  },
  {
    id: 'flow_master', name: '流転の剣', icon: '連', target: 15,
    desc: '攻撃を15回途切れずにつなぐ。',
    value: (g) => g.player?.highestChain || 0,
  },
  {
    id: 'untouchable', name: '刹那の見切り', icon: '瞬', target: 5,
    desc: '完璧回避を5回成功させる。',
    value: (g) => g.player?.perfectDodges || 0,
  },
  {
    id: 'parry_master', name: '鉄壁の返し', icon: '盾', target: 5,
    desc: 'パリィを5回成功させる。',
    value: (g) => g.player?.parries || 0,
  },
  {
    id: 'hunter', name: '百戦錬磨', icon: '討', target: 100,
    desc: '魔物を100体倒す。',
    value: (g) => g.player?.kills || 0,
  },
  {
    id: 'wayfarer', name: '街道の旅人', icon: '街', target: 9,
    desc: '9つの集落を見つける。',
    value: (g) => count(g.world?.settlements, (s) => s.discovered),
  },
  {
    id: 'explorer', name: '地図にない場所', icon: '図', target: 30,
    desc: '見どころを30か所発見する。',
    value: (g) => count(g.world?.pois, (p) => p.discovered),
  },
  {
    id: 'pilgrim', name: '星巡り', icon: '祠', target: 5,
    desc: '祠を5つ起動する。',
    value: (g) => count(g.world?.pois, (p) => p.kind === 'shrine' && p.activated),
  },
  {
    id: 'delver', name: '深淵を越えて', icon: '穴', target: 1,
    desc: '地下迷宮の主を倒す。',
    value: (g) => g.quests?.counters?.dungeonBoss || 0,
  },
  {
    id: 'artisan', name: '名工の相棒', icon: '鍛', target: 5,
    desc: '武具を+5まで強化する。',
    value: (g) => Math.max(0, ...(g.player?.inv || []).map((e) => e.item?.plus || 0)),
  },
  {
    id: 'legend', name: 'アエテリアの伝説', icon: '竜', target: 1,
    desc: '灰燼竜ヴォルガを討ち、物語を完結させる。',
    value: (g) => (g.quests?.chapter || 0) >= 6 ? 1 : 0,
  },
];

export function achievementProgress(def, game) {
  const value = Math.max(0, Number(def.value(game)) || 0);
  return {
    value,
    target: def.target,
    ratio: Math.min(1, value / Math.max(1, def.target)),
    complete: value >= def.target,
  };
}
