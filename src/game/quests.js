// quests.js — main storyline chapters plus procedurally generated side quests.

import { makeRNG, clamp, dist } from '../core/util.js';
import { ITEMS, rollEquip } from './items.js';
import { ENEMY_TYPES } from './entities.js';
import { TILE } from '../world/worldgen.js';

export const MAIN_CHAPTERS = [
  {
    id: 'mq0', title: '第一章・目覚め',
    desc: '王都のギルド長に話を聞こう。',
    hint: 'ギルド長を訪ねる',
    type: 'talk', targetRole: 'guildmaster',
    reward: { xp: 40, gold: 80, items: [{ id: 'potion_s', n: 3 }] },
  },
  {
    id: 'mq1', title: '第二章・野盗の影',
    desc: '街道を荒らす野盗の野営地を制圧せよ。',
    hint: '野盗の野営地を制圧する',
    type: 'clearCamp', count: 1,
    reward: { xp: 140, gold: 220, equipLevel: 4 },
  },
  {
    id: 'mq2', title: '第三章・古き祠',
    desc: '大陸に散る祠を3つ見つけ、加護を受けよ。',
    hint: '祠を3つ発見する',
    type: 'shrines', count: 3,
    reward: { xp: 260, gold: 320, items: [{ id: 'ether_m', n: 3 }] },
  },
  {
    id: 'mq3', title: '第四章・地の底の囁き',
    desc: '地下迷宮の最深部にひそむ主を討て。',
    hint: 'ダンジョンのボスを倒す',
    type: 'dungeonBoss', count: 1,
    reward: { xp: 520, gold: 600, equipLevel: 12 },
  },
  {
    id: 'mq4', title: '第五章・曙光の欠片',
    desc: '封印の要となる欠片を4つ集めよ。迷宮の主が持っている。',
    hint: '曙光の欠片を4つ集める',
    type: 'collect', item: 'relic', count: 4,
    reward: { xp: 900, gold: 1200, items: [{ id: 'elixir', n: 2 }], equipLevel: 18 },
  },
  {
    id: 'mq5', title: '終章・灰燼竜ヴォルガ',
    desc: '火口へ向かい、大陸を焼く竜を討て。',
    hint: '竜の火口へ向かう',
    type: 'killBoss', boss: 'dragon',
    reward: { xp: 4000, gold: 5000, equipLevel: 24 },
  },
];

const SIDE_TEMPLATES = [
  {
    kind: 'hunt', make(rng, level, world, from) {
      const pool = ['slime', 'wolf', 'boar', 'spider', 'bandit', 'skeleton', 'ghost', 'imp', 'golem'];
      const tiers = { slime: 1, wolf: 2, boar: 3, spider: 3, bandit: 4, skeleton: 6, ghost: 7, imp: 9, golem: 11 };
      const ok = pool.filter((k) => tiers[k] <= level + 3);
      const kind = rng.pick(ok.length ? ok : ['slime']);
      const n = rng.irange(3, 8);
      return {
        type: 'kill', kind, count: n, progress: 0,
        title: `${ENEMY_TYPES[kind].name}の討伐`,
        desc: `${ENEMY_TYPES[kind].name}を${n}体倒してほしい。`,
      };
    },
  },
  {
    kind: 'gather', make(rng, level, world, from) {
      const mats = ['herb', 'wood', 'hide', 'ore_iron', 'fang', 'bone', 'jelly', 'silk'];
      const id = rng.pick(mats);
      const n = rng.irange(3, 8);
      return {
        type: 'collect', item: id, count: n, progress: 0,
        title: `${ITEMS[id].name}の採集`,
        desc: `${ITEMS[id].name}を${n}個 集めてきてくれ。`,
      };
    },
  },
  {
    kind: 'deliver', make(rng, level, world, from) {
      const others = world.settlements.filter((s) => s !== from);
      if (!others.length) return null;
      const to = rng.pick(others);
      return {
        type: 'deliver', to: to.id, count: 1, progress: 0,
        marker: { x: (to.x + 0.5) * TILE, y: (to.y + 0.5) * TILE },
        title: `${to.name}への配達`,
        desc: `この荷を ${to.name}${to.label} まで届けてほしい。`,
      };
    },
  },
  {
    kind: 'explore', make(rng, level, world, from) {
      const undiscovered = world.pois.filter((p) => !p.discovered && (p.kind === 'ruin' || p.kind === 'dungeon' || p.kind === 'shrine'));
      if (!undiscovered.length) return null;
      undiscovered.sort((a, b) => dist(a.tx, a.ty, from.x, from.y) - dist(b.tx, b.ty, from.x, from.y));
      const poi = undiscovered[rng.int(Math.min(4, undiscovered.length))];
      return {
        type: 'discover', poi: poi.id, count: 1, progress: 0,
        marker: { x: poi.x, y: poi.y },
        title: '未踏の地の調査',
        desc: 'この先にある古い場所を調べてきてくれ。地図に印をつけておいた。',
      };
    },
  },
];

export class QuestLog {
  constructor(game) {
    this.g = game;
    this.active = [];
    this.done = [];
    this.chapter = 0;
    this.mainQuest = null;
    this.counters = { camps: 0, shrines: 0, dungeonBoss: 0 };
    this.startChapter(0);
  }

  startChapter(i) {
    this.chapter = i;
    if (i >= MAIN_CHAPTERS.length) { this.mainQuest = null; return; }
    const c = MAIN_CHAPTERS[i];
    this.mainQuest = {
      ...c, main: true, progress: 0, count: c.count || 1, state: 'active', id: c.id,
    };
    this.active.unshift(this.mainQuest);
  }

  addSide(quest) {
    quest.state = 'active';
    this.active.push(quest);
    this.g.toast('クエストを受注: ' + quest.title, '#8fd0ff');
  }

  /** Build the quest board for a settlement. */
  generateBoard(settlement, level, seed) {
    const rng = makeRNG(seed);
    const out = [];
    for (let i = 0; i < 3; i++) {
      const tpl = rng.pick(SIDE_TEMPLATES);
      const q = tpl.make(rng, level, this.g.world, settlement);
      if (!q) continue;
      const lv = clamp(level + rng.irange(-1, 2), 1, 30);
      q.id = 'sq_' + settlement.id + '_' + seed + '_' + i;
      q.level = lv;
      q.giver = settlement.name;
      q.reward = {
        gold: Math.round((40 + lv * 26) * (0.8 + rng() * 0.6)),
        xp: Math.round((36 + lv * 30) * (0.8 + rng() * 0.5)),
      };
      if (rng.chance(0.4)) q.reward.equipLevel = lv;
      else if (rng.chance(0.5)) q.reward.items = [{ id: rng.pick(['potion_m', 'ether_m', 'tonic', 'ward']), n: rng.irange(1, 2) }];
      out.push(q);
    }
    return out;
  }

  isTaken(id) {
    return this.active.some((q) => q.id === id) || this.done.some((q) => q.id === id);
  }

  onKill(kind, x, y) {
    for (const q of this.active) {
      if (q.type === 'kill' && q.kind === kind && q.progress < q.count) {
        q.progress++;
        this.g.toast(`${q.title} ${q.progress}/${q.count}`, '#cfd6dd');
        this.check(q);
      }
    }
  }
  onCollect(id, n) {
    for (const q of this.active) {
      if (q.type === 'collect' && q.item === id) {
        q.progress = this.g.countItem(id);
        this.check(q);
      }
    }
  }
  onCampCleared() {
    this.counters.camps++;
    for (const q of this.active) if (q.type === 'clearCamp') { q.progress++; this.check(q); }
  }
  onShrine() {
    this.counters.shrines++;
    for (const q of this.active) if (q.type === 'shrines') { q.progress = this.counters.shrines; this.check(q); }
  }
  onDungeonBoss() {
    this.counters.dungeonBoss++;
    for (const q of this.active) if (q.type === 'dungeonBoss') { q.progress++; this.check(q); }
  }
  onBossKill(kind) {
    for (const q of this.active) if (q.type === 'killBoss' && q.boss === kind) { q.progress = 1; this.check(q); }
  }
  onDiscover(poi) {
    for (const q of this.active) if (q.type === 'discover' && q.poi === poi.id) { q.progress = 1; this.check(q); }
  }
  onTalk(npc) {
    for (const q of this.active) {
      if (q.type === 'talk' && npc.role === q.targetRole) { q.progress = 1; this.check(q); }
      if (q.type === 'deliver' && npc.settlement && npc.settlement.id === q.to) { q.progress = 1; this.check(q); }
    }
  }

  check(q) {
    if (q.progress >= q.count && q.state === 'active') {
      q.state = 'complete';
      this.g.toast('クエスト達成: ' + q.title, '#ffd76a');
      this.g.audio.sfx('quest');
      if (q.main) this.turnIn(q);        // main chapters auto-complete
    }
  }

  turnIn(q) {
    const i = this.active.indexOf(q);
    if (i >= 0) this.active.splice(i, 1);
    q.state = 'done';
    this.done.push(q);
    const g = this.g;
    const r = q.reward || {};
    if (r.gold) { g.player.gold += r.gold; g.toast(`${r.gold} ゴールドを得た`, '#ffd76a'); }
    if (r.xp) g.player.addXp(r.xp, g);
    if (r.items) for (const it of r.items) g.giveItem(it.id, it.n);
    if (r.equipLevel) {
      const eq = rollEquip(makeRNG((Date.now() ^ (q.id.length * 7919)) >>> 0), r.equipLevel, 2);
      g.giveEquip(eq);
    }
    g.audio.sfx('quest');
    if (q.main) {
      const next = this.chapter + 1;
      if (next < MAIN_CHAPTERS.length) {
        // Advance and persist immediately; only the presentation is delayed.
        this.startChapter(next);
        g.save(true);
        setTimeout(() => {
          g.toast('新章: ' + MAIN_CHAPTERS[next].title, '#c58cff');
          g.showBanner(MAIN_CHAPTERS[next].title, MAIN_CHAPTERS[next].desc);
        }, 1400);
      } else {
        this.chapter = MAIN_CHAPTERS.length;
        g.showBanner('伝説の終わり', '大陸に朝が還った。旅は続く。');
        g.save(true);
      }
    } else {
      g.save(true);
    }
  }

  markerFor(q) {
    if (q.marker) return { ...q.marker, main: !!q.main, title: q.title };
    const g = this.g;
    if (!g || !g.player || !g.world) return null;
    let candidates = [];
    if (q.type === 'talk') {
      candidates = (g.npcs || []).filter((n) => n.role === q.targetRole);
    } else if (q.type === 'clearCamp') {
      candidates = g.world.pois.filter((p) => p.kind === 'camp' &&
        !p.cleared && !g.campState?.get(p.id)?.cleared);
    } else if (q.type === 'shrines') {
      candidates = g.world.pois.filter((p) => p.kind === 'shrine' && !p.activated);
    } else if (q.type === 'dungeonBoss' || (q.type === 'collect' && q.item === 'relic')) {
      candidates = g.world.pois.filter((p) => p.kind === 'dungeon' && !p.cleared);
    } else if (q.type === 'killBoss' && q.boss === 'dragon') {
      candidates = g.world.pois.filter((p) => p.kind === 'lair' && !p.cleared);
    }
    let best = null, bestD = Infinity;
    for (const c of candidates) {
      const d = dist(g.player.x, g.player.y, c.x, c.y);
      if (d < bestD) { best = c; bestD = d; }
    }
    return best ? { x: best.x, y: best.y, main: !!q.main, title: q.title } : null;
  }

  /** Resolved active objectives used by the HUD, minimap and full map. */
  markers() {
    const out = [];
    for (const q of this.active) {
      if (q.state === 'complete') continue;
      const marker = this.markerFor(q);
      if (marker) out.push(marker);
    }
    return out;
  }

  save() {
    return {
      active: this.active, done: this.done, chapter: this.chapter, counters: this.counters,
    };
  }
  load(d) {
    if (!d) return;
    this.active = d.active || [];
    this.done = (d.done || []).map((q) => typeof q === 'string' ? { id: q, state: 'done' } : q);
    this.chapter = d.chapter || 0;
    this.counters = d.counters || { camps: 0, shrines: 0, dungeonBoss: 0 };
    this.mainQuest = this.active.find((q) => q.main) || null;
    if (!this.mainQuest && this.chapter < MAIN_CHAPTERS.length) {
      let next = this.chapter;
      while (next < MAIN_CHAPTERS.length && this.done.some((q) => q.id === MAIN_CHAPTERS[next].id)) next++;
      this.startChapter(next);
    }
  }
}
