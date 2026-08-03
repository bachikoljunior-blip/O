// audio1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 800, height: 400, audio: true });

/* ---------- 音楽理論の中身を直接検証する ---------- */
await page.addScriptTag({ type: 'module', content: `
  import { REGION_TONE } from '/src/core/audio.js';
  window.__TONE = REGION_TONE;
`});
await page.waitForTimeout(400);

const theory = await page.evaluate(() => {
  const g = window.__game, a = g.audio;
  // 発音をすべて記録するフックを付ける
  const notes = [];
  const origTone = a.tone.bind(a);
  a.tone = (o) => { notes.push({ freq: o.freq, gain: o.gain || 0, dest: o.dest === a.musicGain ? 'music' : 'sfx' }); return origTone(o); };
  const origNoise = a.noise.bind(a);
  a.noise = (o) => { notes.push({ noise: true, freq: o.freq, gain: o.gain || 0 }); return origNoise(o); };
  window.__notes = notes;
  window.__restoreAudio = () => { a.tone = origTone; a.noise = origNoise; };
  return { ready: a.ready, muted: a.muted, mode: a.mode };
});
console.log('audio:', JSON.stringify(theory));
ok('オーディオが起動している', theory.ready && !theory.muted);

/** モードを固定して n 秒ぶん進め、鳴った音を集める */
async function capture(mode, seconds, setup) {
  return page.evaluate(async ({ mode, seconds, setupSrc }) => {
    const g = window.__game, a = g.audio;
    if (setupSrc) (new Function('g', setupSrc))(g);
    a.setMode(mode);
    a.beat = 0; a.nextNote = 0;
    window.__notes.length = 0;
    const step = 1 / 30;
    for (let i = 0; i < seconds * 30; i++) {
      a.update(step, g);
      if (i % 300 === 0) await new Promise(r => setTimeout(r, 0));
    }
    const music = window.__notes.filter(n => n.dest === 'music');
    const freqs = music.map(n => +n.freq.toFixed(2));
    return { total: window.__notes.length, music: music.length,
      distinct: new Set(freqs).size, freqs: freqs.slice(0, 400),
      percussion: window.__notes.filter(n => n.noise).length };
  }, { mode, seconds, setupSrc: setup || null });
}

/* ---------- 1. モードごとに鳴りが違う ---------- */
const modes = {};
for (const m of ['explore', 'combat', 'boss', 'final', 'dungeon']) {
  modes[m] = await capture(m, 24);
  console.log(`   ${m.padEnd(8)} 音数${String(modes[m].music).padStart(4)} 打楽器${String(modes[m].percussion).padStart(4)} 異なる音高${modes[m].distinct}`);
}
ok('全モードが音を出す', Object.values(modes).every(m => m.music > 5),
  Object.entries(modes).filter(([,v])=>v.music<=5).map(([k])=>k).join(','));
ok('ボス戦は探索より音数が多い', modes.boss.music > modes.explore.music,
  `boss=${modes.boss.music} explore=${modes.explore.music}`);
ok('ボス戦だけ打楽器が入る', modes.boss.percussion > 0 && modes.explore.percussion === 0,
  `boss=${modes.boss.percussion} explore=${modes.explore.percussion}`);
ok('地下は最も静か', modes.dungeon.music < modes.explore.music,
  `dungeon=${modes.dungeon.music} explore=${modes.explore.music}`);

/* ---------- 2. 和音が進行している ---------- */
const prog = await page.evaluate(async () => {
  const g = window.__game, a = g.audio;
  a.setMode('explore'); a.beat = 0; a.nextNote = 0;
  const bars = [];
  const step = 1 / 30;
  for (let bar = 0; bar < 8; bar++) {
    window.__notes.length = 0;
    // 4 拍 = 1 小節ぶん進める
    const spb = 60 / 54;
    for (let i = 0; i < spb * 4 * 30 + 2; i++) { a.update(step, g); }
    const music = window.__notes.filter(n => n.dest === 'music').map(n => +n.freq.toFixed(1));
    bars.push(music.length ? Math.min(...music) : 0);   // その小節の最低音＝根音
  }
  return bars;
});
console.log('小節ごとの最低音(Hz):', prog.map(v => v.toFixed(0)).join(' '));
ok('和音が小節ごとに動く', new Set(prog.filter(Boolean)).size >= 3,
  '異なる根音 ' + new Set(prog.filter(Boolean)).size + '種');

/* ---------- 3. 地方で調が変わる ---------- */
const byRegion = await page.evaluate(async () => {
  const g = window.__game, a = g.audio;
  const out = {};
  const regions = Object.keys(window.__TONE);
  for (const id of regions) {
    // その地方の中心へ飛ぶ
    const R = g.world.constructor && null; void R;
    const reg = (await import('/src/world/worldgen.js')).REGIONS.find(r => r.id === id);
    g.player.x = reg.cx; g.player.z = reg.cz;
    const tone = a.toneOf(g);
    a.setMode('explore'); a.beat = 0; a.nextNote = 0;
    window.__notes.length = 0;
    for (let i = 0; i < 30 * 20; i++) a.update(1/30, g);
    const music = window.__notes.filter(n => n.dest === 'music').map(n => +n.freq.toFixed(1));
    out[id] = { root: tone.root, mode: tone.mode, bright: tone.bright,
      lowest: music.length ? +Math.min(...music).toFixed(1) : 0 };
  }
  return out;
});
console.log('地方ごとの調:');
for (const [id, v] of Object.entries(byRegion)) {
  console.log(`   ${id.padEnd(10)} 移調${String(v.root).padStart(3)} ${v.mode.padEnd(9)} 明るさ${v.bright} 最低音${v.lowest}Hz`);
}
const lows = Object.values(byRegion).map(v => v.lowest);
ok('地方ごとに旋法が割り当てられている',
  new Set(Object.values(byRegion).map(v => v.mode)).size >= 4,
  [...new Set(Object.values(byRegion).map(v => v.mode))].join(','));
ok('地方ごとに調（基音）が変わる', new Set(lows).size >= 4, [...new Set(lows)].join(','));

/* ---------- 4. 地下に入ると曲が変わる ---------- */
const dungeonSwitch = await page.evaluate(async () => {
  const g = window.__game;
  const poi = g.pois.find(p => p.dungeonTheme) || g.pois.find(p => p.type === 'grave');
  g.enemies.length = 0;
  const before = (g.updateMusic(), g.audio.mode);
  g.enterDungeon(poi);
  g.enemies.length = 0;
  g.updateMusic();
  const inside = g.audio.mode;
  const tone = g.audio.toneOf(g);
  g.exitDungeon();
  g.updateMusic();
  const after = g.audio.mode;
  return { before, inside, after, tone };
});
console.log('dungeon music:', JSON.stringify(dungeonSwitch));
ok('地下では専用の曲になる', dungeonSwitch.inside === 'dungeon', dungeonSwitch.inside);
ok('地上に戻ると探索曲に戻る', dungeonSwitch.after === 'explore', dungeonSwitch.after);

/* ---------- 5. 地下の環境音（水滴・地鳴り）と、地上の環境音 ---------- */
const amb = await page.evaluate(async () => {
  const g = window.__game;
  const poi = g.pois.find(p => p.dungeonTheme) || g.pois.find(p => p.type === 'grave');
  const count = async (seconds) => {
    window.__notes.length = 0;
    for (let i = 0; i < seconds * 30; i++) {
      g.audio.updateAmbience(1/30, g);
      if (i % 300 === 0) await new Promise(r => setTimeout(r, 0));
    }
    return window.__notes.length;
  };
  g.enterDungeon(poi);
  const inDungeon = await count(40);
  g.exitDungeon();
  // 昼の草原
  const downs = (await import('/src/world/worldgen.js')).REGIONS.find(r => r.id === 'downs');
  g.player.x = downs.cx; g.player.z = downs.cz;
  g.player.y = g.world.height(g.player.x, g.player.z);
  g.sky.time = 0.4;
  const outside = await count(40);
  return { inDungeon, outside };
});
console.log('ambience:', JSON.stringify(amb));
ok('地下でも環境音が鳴る（水滴・地鳴り）', amb.inDungeon > 0, 'n=' + amb.inDungeon);
ok('地上でも環境音が鳴る', amb.outside > 0, 'n=' + amb.outside);

/* ---------- 6. 集落の気配 ---------- */
const town = await page.evaluate(async () => {
  const g = window.__game;
  const v = g.pois.find(p => p.type === 'village' && (p.size||0) >= 9) || g.pois.find(p => p.type === 'village');
  const run = async (x, z, timeOfDay, seconds) => {
    g.player.x = x; g.player.z = z;
    g.player.y = g.world.height(x, z);
    g.sky.time = timeOfDay;
    // isNight は sky.night から導かれる。update を通さないと更新されないので直接指定する
    g.sky.night = timeOfDay > 0.8 || timeOfDay < 0.2 ? 1 : 0;
    g.audio.ambience.town = 0;
    window.__notes.length = 0;
    let events = 0, lastLen = 0;
    for (let i = 0; i < seconds * 30; i++) {
      g.audio.updateSettlement(1/30, g);
      if (window.__notes.length > lastLen) { events++; lastLen = window.__notes.length; }
      if (i % 300 === 0) await new Promise(r => setTimeout(r, 0));
    }
    // 「静かさ」は鳴った回数ではなく音量の総和で見る
    const energy = window.__notes.reduce((s, n) => s + (n.gain || 0), 0);
    return { notes: window.__notes.length, energy: +energy.toFixed(4), events };
  };
  const near = await run(v.x + 8, v.z + 8, 0.42, 60);
  const far = await run(v.x + 400, v.z + 400, 0.42, 60);
  const night = await run(v.x + 8, v.z + 8, 0.95, 60);
  return { village: v.name, size: v.size, near, far, night };
});
console.log('settlement:', JSON.stringify(town));
ok('集落のそばで生活音が鳴る', town.near.notes > 0, 'n=' + town.near.notes);
ok('離れると鳴らない', town.far.notes === 0, 'n=' + town.far.notes);
ok('夜は音量が下がる', town.night.energy < town.near.energy * 0.75,
  `昼${town.near.energy} 夜${town.night.energy}`);
ok('夜は鳴る回数が減る', town.night.events < town.near.events,
  `昼${town.near.events}回 夜${town.night.events}回`);

await page.evaluate(() => window.__restoreAudio());
report(errs);
await close();
