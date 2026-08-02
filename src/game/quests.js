// クエストと進行管理

export const MAIN_ID = 'main';

/**
 * ステージ定義
 *  text   : 目標表示
 *  check  : (q, game) => boolean 完了条件
 *  hint   : ワールドマップに表示する目的地（poi の tag）
 */
export const QUESTS = {
  main: {
    id: 'main', name: '王の残響', main: true,
    stages: [
      {
        text: '灰かぶりの村へ向かい、長老エルダに話を聞く',
        hint: 'hub',
        check: (q) => q.flags.talkedElder,
      },
      {
        text: '三つの標のうち二つを断つ',
        hint: 'boss_wood',
        progress: (q) => `${q.count.landmarkBosses || 0}/2`,
        check: (q) => (q.count.landmarkBosses || 0) >= 2,
      },
      {
        text: '蒼天嶺の頂で竜ヴァルドリスを討ち、竜の鍵を得る',
        hint: 'boss_sky',
        check: (q) => q.flags.dragonKey,
      },
      {
        text: '裂罅の谷の門を開き、深淵の王ノクトゥルヌスと対峙する',
        hint: 'boss_final',
        check: (q) => q.flags.bossDefeated_nocturnus,
      },
      { text: '世界の残響は静まった', check: () => false, end: true },
    ],
    reward: { echo: 0 },
  },

  herbs: {
    id: 'herbs', name: '薬師の頼み',
    giver: 'herbalist',
    stages: [
      {
        text: '血赤花を 5 つ集めて薬師に届ける',
        progress: (q, game) => `${Math.min(5, game.player.inventory.blood_flower || 0)}/5`,
        check: (q, game) => (game.player.inventory.blood_flower || 0) >= 5,
      },
      { text: '薬師に報告する', check: (q) => q.flags.herbsTurnedIn },
    ],
    reward: { echo: 900, items: [['herb', 5], ['antidote', 3]] },
  },

  wolves: {
    id: 'wolves', name: '群れを間引く',
    giver: 'hunter',
    stages: [
      {
        text: '狼を 8 体討伐する',
        progress: (q) => `${Math.min(8, q.count.kill_wolf || 0)}/8`,
        check: (q) => (q.count.kill_wolf || 0) >= 8,
      },
      { text: '狩人に報告する', check: (q) => q.flags.wolvesTurnedIn },
    ],
    reward: { echo: 1200, items: [['ore_iron', 3]], talisman: 'ring_hunter' },
  },

  smith: {
    id: 'smith', name: '鍛冶の火',
    giver: 'smith',
    stages: [
      {
        text: '銀鉱石を 2 つ鍛冶屋に持ち込む',
        progress: (q, game) => `${Math.min(2, game.player.inventory.ore_silver || 0)}/2`,
        check: (q, game) => (game.player.inventory.ore_silver || 0) >= 2,
      },
      { text: '鍛冶屋に渡す', check: (q) => q.flags.smithTurnedIn },
    ],
    reward: { echo: 600, weapon: 'longsword' },
  },

  wraiths: {
    id: 'wraiths', name: '嘆きを鎮める',
    giver: 'priest',
    stages: [
      {
        text: '嘆きの亡霊を 6 体退ける',
        progress: (q) => `${Math.min(6, q.count.kill_wraith || 0)}/6`,
        check: (q) => (q.count.kill_wraith || 0) >= 6,
      },
      { text: '司祭に報告する', check: (q) => q.flags.wraithsTurnedIn },
    ],
    reward: { echo: 2000, spell: 'heal' },
  },

  delve: {
    id: 'delve', name: '地の底を漁る',
    stages: [
      {
        text: '地下の宝箱を 4 つ開ける',
        progress: (q) => `${Math.min(4, q.count.dungeonChests || 0)}/4`,
        check: (q) => (q.count.dungeonChests || 0) >= 4,
      },
    ],
    reward: { echo: 2600, items: [['shard_ancient', 1], ['bone', 2]], talisman: 'ring_echo' },
  },

  depths: {
    id: 'depths', name: '底へ至る道',
    stages: [
      {
        text: '遺跡の主を 3 体討つ',
        progress: (q) => `${Math.min(3, q.count.dungeonBosses || 0)}/3`,
        check: (q) => (q.count.dungeonBosses || 0) >= 3,
      },
      {
        text: 'いずれかの遺跡の第 4 層まで潜る',
        progress: (q, game) => `${Math.min(4, game.deepestDepth || 0)}/4`,
        check: (q, game) => (game.deepestDepth || 0) >= 4,
      },
    ],
    reward: { echo: 6000, items: [['ore_silver', 3], ['shard_ancient', 2]], shield: 'tower_shield' },
  },

  towers: {
    id: 'towers', name: '見張り塔の灯',
    stages: [
      {
        text: '見張り塔を 3 つ調べて周辺の地図を得る',
        progress: (q) => `${Math.min(3, q.count.towers || 0)}/3`,
        check: (q) => (q.count.towers || 0) >= 3,
      },
    ],
    reward: { echo: 1500, items: [['bone', 3]] },
  },

  /* ===================================================== 連鎖する依頼 */
  smith2: {
    id: 'smith2', name: '鍛冶の火・熾き', giver: 'smith', after: 'smith',
    stages: [
      {
        text: '古き欠片をひとつ鍛冶屋に持ち込む',
        progress: (q, game) => `${Math.min(1, game.player.inventory.shard_ancient || 0)}/1`,
        check: (q, game) => (game.player.inventory.shard_ancient || 0) >= 1,
      },
      { text: '鍛冶屋に渡す', check: (q) => q.flags.smith2TurnedIn },
    ],
    reward: { echo: 3200, weapon: 'greatsword', items: [['ore_silver', 2]] },
  },

  herbs2: {
    id: 'herbs2', name: '薬師の秘薬', giver: 'herbalist', after: 'herbs',
    stages: [
      {
        text: '魔力の結晶を 5 つ薬師に届ける',
        progress: (q, game) => `${Math.min(5, game.player.inventory.crystal || 0)}/5`,
        check: (q, game) => (game.player.inventory.crystal || 0) >= 5,
      },
      { text: '薬師に渡す', check: (q) => q.flags.herbs2TurnedIn },
    ],
    reward: { echo: 2400, talisman: 'ring_vigor', items: [['herb', 8], ['antidote', 4]] },
  },

  fenwatch: {
    id: 'fenwatch', name: '霧の見張り', giver: 'priest', after: 'wraiths',
    stages: [
      {
        text: '湿原の魔女セラフィナを鎮める',
        check: (q) => q.flags.bossDefeated_witch,
      },
      { text: '司祭に報告する', check: (q) => q.flags.fenwatchTurnedIn },
    ],
    reward: { echo: 4000, spell: 'barrier', items: [['blood_flower', 4]] },
  },

  /* ===================================================== 選択のある依頼 */
  missing: {
    id: 'missing', name: '帰らぬ弟', giver: 'innkeeper',
    stages: [
      {
        text: '地下に潜り、弟の消息を探す（遺跡・墓所の宝箱を 2 つ）',
        progress: (q) => `${Math.min(2, q.count.dungeonChests || 0)}/2`,
        check: (q) => (q.count.dungeonChests || 0) >= 2,
      },
      { text: '宿の主に伝える', check: (q) => !!q.flags.missingAnswer },
    ],
    /** 真実を告げるか、優しい嘘をつくかで報いが変わる */
    reward: (q) => (q.flags.missingAnswer === 'truth'
      ? { echo: 2600, talisman: 'ring_echo', items: [['herb', 6]] }
      : { echo: 4200, items: [['echo_shard', 3]] }),
  },

  relic: {
    id: 'relic', name: '亡妻の指輪', giver: 'elder',
    stages: [
      {
        text: '遺跡の主を討ち、副葬された指輪を取り戻す',
        progress: (q) => `${Math.min(1, q.count.dungeonBosses || 0)}/1`,
        check: (q) => (q.count.dungeonBosses || 0) >= 1,
      },
      { text: '長老に指輪を届ける', check: (q) => !!q.flags.relicAnswer },
    ],
    /** 返せば信、持ち去れば力 */
    reward: (q) => (q.flags.relicAnswer === 'return'
      ? { echo: 3000, armor: 'chain', items: [['ore_silver', 2]] }
      : { echo: 1200, talisman: 'ring_moon' }),
  },

  /* ===================================================== その他 */
  courier: {
    id: 'courier', name: '遠い便り', giver: 'merchant',
    stages: [
      {
        text: 'ほかの集落まで手紙を届ける',
        check: (q) => !!q.flags.letterDelivered,
      },
    ],
    reward: { echo: 1800, items: [['bone', 2], ['ore_iron', 3]] },
  },

  packlord: {
    id: 'packlord', name: '黒い一匹', giver: 'hunter', after: 'wolves',
    stages: [
      {
        text: '群れの長を 2 体討つ',
        progress: (q) => `${Math.min(2, q.count.kill_direwolf || 0)}/2`,
        check: (q) => (q.count.kill_direwolf || 0) >= 2,
      },
      { text: '狩人に報告する', check: (q) => q.flags.packlordTurnedIn },
    ],
    reward: { echo: 3400, weapon: 'bow', items: [['arrow', 40], ['beast_bone', 6]] },
  },
};

export class QuestLog {
  constructor() {
    this.state = {};      // id -> {stage, done}
    this.flags = {};
    this.count = {};
    this.listeners = [];
  }

  start(id) {
    if (this.state[id]) return false;
    this.state[id] = { stage: 0, done: false };
    this.emit('start', id);
    return true;
  }
  isActive(id) { return this.state[id] && !this.state[id].done; }
  isDone(id) { return this.state[id]?.done; }
  stageOf(id) { return this.state[id]?.stage ?? -1; }

  emit(kind, id) {
    for (const fn of this.listeners) fn(kind, id);
  }

  /** イベント通知 */
  onKill(archetype) {
    this.count[`kill_${archetype}`] = (this.count[`kill_${archetype}`] || 0) + 1;
  }
  onBossDefeated(id) {
    this.flags[`bossDefeated_${id}`] = true;
    if (['orgren', 'galvan', 'leonhart', 'witch'].includes(id)) {
      this.count.landmarkBosses = (this.count.landmarkBosses || 0) + 1;
    }
    if (id === 'dragon') this.flags.dragonKey = true;
  }
  onTower() { this.count.towers = (this.count.towers || 0) + 1; }

  /** 毎フレーム進行チェック */
  update(game) {
    for (const id of Object.keys(this.state)) {
      const st = this.state[id];
      if (st.done) continue;
      const q = QUESTS[id];
      if (!q) continue;
      const stage = q.stages[st.stage];
      if (!stage) { st.done = true; continue; }
      if (stage.end) continue;
      if (stage.check(this, game)) {
        st.stage++;
        if (st.stage >= q.stages.length) {
          st.done = true;
          this.grant(q, game);
          game.ui.questComplete(q.name);
        } else {
          game.ui.questUpdate(q.name, this.stageText(id, game));
        }
      }
    }
  }

  stageText(id, game) {
    const q = QUESTS[id];
    const st = this.state[id];
    if (!q || !st) return '';
    const stage = q.stages[Math.min(st.stage, q.stages.length - 1)];
    if (!stage) return '';
    let t = stage.text;
    if (stage.progress) t = t.replace('{n}', '') + `  [${stage.progress(this, game)}]`;
    return t;
  }

  grant(q, game) {
    const p = game.player;
    // 報いは選択で変わることがあるので、関数なら今の状態から求める
    const r = (typeof q.reward === 'function' ? q.reward(this, game) : q.reward) || {};
    if (r.echo) { p.echo += r.echo; game.ui.toast(`残響 +${r.echo}`); }
    for (const [item, n] of r.items || []) { p.addItem(item, n); game.ui.itemGain(item, n); }
    if (r.weapon) game.unlockWeapon(r.weapon);
    if (r.armor) game.unlockArmor(r.armor);
    if (r.shield) game.unlockShield(r.shield);
    if (r.talisman) game.unlockTalisman(r.talisman);
    if (r.spell) game.unlockSpell(r.spell);
  }

  serialize() { return { state: this.state, flags: this.flags, count: this.count }; }
  deserialize(d) {
    if (!d) return;
    this.state = d.state || {};
    this.flags = d.flags || {};
    this.count = d.count || {};
  }
}
