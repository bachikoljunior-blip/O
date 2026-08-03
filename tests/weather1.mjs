// weather1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360 });

/* ---------- 1. 濡れの蓄積と乾き ---------- */
const wet = await page.evaluate(async () => {
  const g = window.__game, s = g.sky;
  const step = (secs, dt = 1/10) => { for (let i = 0; i < secs / dt; i++) s.update(dt, null); };
  s.paused = true;                      // 太陽を止めて条件を揃える
  s.time = 0.5;                          // 正午（乾きが最も速い）
  s.wetness = 0; s.snowCover = 0;
  s.setWeather('rain', true);
  step(10);  const at10 = s.wetness;
  step(50);  const at60 = s.wetness;
  step(120); const soaked = s.wetness;
  s.setWeather('clear', true);
  step(30);  const dry30 = s.wetness;
  step(90);  const dry120 = s.wetness;
  step(400); const dry520 = s.wetness;
  // 同じ 120 秒でも夜は乾きが遅いこと
  s.setWeather('rain', true); step(180);
  s.time = 0.0; s.update(0, null);
  s.setWeather('clear', true); step(120);
  const nightWet = s.wetness;
  s.paused = false;
  return { at10, at60, soaked, dry30, dry120, dry520, nightWet };
});
console.log('濡れ:', JSON.stringify(Object.fromEntries(
  Object.entries(wet).map(([k,v]) => [k, +v.toFixed(3)]))));
ok('雨で徐々に濡れる（一気に最大にならない）', wet.at10 > 0.02 && wet.at10 < 0.45,
  '10秒で ' + wet.at10.toFixed(3));
ok('降り続けると飽和する', wet.soaked > 0.85, wet.soaked.toFixed(3));
ok('止んですぐには乾かない', wet.dry30 > wet.soaked * 0.75, wet.dry30.toFixed(3));
ok('やがて乾く', wet.dry520 < 0.02, wet.dry520.toFixed(3));
ok('乾きは単調に進む', wet.dry30 > wet.dry120 && wet.dry120 > wet.dry520);
ok('同じ時間でも夜は乾きが遅い', wet.nightWet > wet.dry120 * 1.3,
  `昼120秒後 ${wet.dry120.toFixed(3)} / 夜120秒後 ${wet.nightWet.toFixed(3)}`);

/* ---------- 2. 積雪 ---------- */
const snow = await page.evaluate(async () => {
  const g = window.__game, s = g.sky;
  const step = (secs, dt = 1/10) => { for (let i = 0; i < secs / dt; i++) s.update(dt, null); };
  s.paused = true; s.time = 0.5;
  s.wetness = 0; s.snowCover = 0;
  s.setWeather('snow', true);
  step(60);  const at60 = s.snowCover;
  step(300); const piled = s.snowCover;
  const wetWhilePiled = s.wetness;
  s.setWeather('clear', true);
  step(120); const melt120 = s.snowCover;
  step(900); const melt1020 = s.snowCover;
  s.paused = false;
  return { at60, piled, wetWhilePiled, melt120, melt1020 };
});
console.log('積雪:', JSON.stringify(Object.fromEntries(
  Object.entries(snow).map(([k,v]) => [k, +v.toFixed(3)]))));
ok('雪が積もる', snow.piled > 0.7, snow.piled.toFixed(3));
ok('積もるのは濡れより遅い', snow.at60 < wet.at60, `雪${snow.at60.toFixed(3)} 雨${wet.at60.toFixed(3)}`);
ok('積雪面は濡れとして扱われない', snow.wetWhilePiled < 0.3, snow.wetWhilePiled.toFixed(3));
ok('雪は溶ける', snow.melt1020 < 0.02, snow.melt1020.toFixed(3));
ok('同じ 120 秒で、雪は水たまりより残る', snow.melt120 / snow.piled > wet.dry120 / wet.soaked,
  `雪 ${(snow.melt120/snow.piled).toFixed(3)} / 濡れ ${(wet.dry120/wet.soaked).toFixed(3)}`);

/* ---------- 3. 視程 ---------- */
const vis = await page.evaluate(() => {
  const s = window.__game.sky;
  const out = {};
  for (const w of ['clear','fair','cloudy','rain','storm','fog','snow']) {
    s.setWeather(w, true); s.update(0.016, null);
    out[w] = +s.visibility.toFixed(3);
  }
  s.setWeather('fair', true); s.update(0.016, null);
  return out;
});
console.log('視程:', JSON.stringify(vis));
ok('快晴の視程が最大', vis.clear >= 0.99, String(vis.clear));
ok('濃霧が最も視程が悪い', vis.fog === Math.min(...Object.values(vis)), String(vis.fog));
ok('雨・嵐・雪でも視程が落ちる',
  vis.rain < 0.9 && vis.storm < vis.rain && vis.snow < 0.9,
  `雨${vis.rain} 嵐${vis.storm} 雪${vis.snow}`);
ok('視程は下限で止まる', Math.min(...Object.values(vis)) >= 0.30);

/* ---------- 4. 索敵距離が天候で変わる ---------- */
const aggro = await page.evaluate(async () => {
  const g = window.__game;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const measure = (weather) => {
    g.sky.setWeather(weather, true); g.sky.update(0.016, g);
    g.sky.night = 0;
    // 敵を 1 体だけ置いて、気づかれる距離を二分探索する
    let lo = 0, hi = 80;
    for (let k = 0; k < 14; k++) {
      const mid = (lo + hi) / 2;
      g.enemies.length = 0;
      const e = spawnEnemy('wolf', {
        x: g.player.x + mid, y: g.player.y, z: g.player.z, yaw: 0, leash: 400,
      });
      g.enemies.push(e);
      e.aggro = false;
      for (let i = 0; i < 4; i++) e.update(1/30, g);
      if (e.aggro) lo = mid; else hi = mid;
    }
    return +lo.toFixed(2);
  };
  const clear = measure('clear');
  const fog = measure('fog');
  const storm = measure('storm');
  g.enemies.length = 0;
  g.sky.setWeather('fair', true);
  return { clear, fog, storm };
});
console.log('索敵距離(m):', JSON.stringify(aggro));
ok('快晴では遠くから気づかれる', aggro.clear > 15, String(aggro.clear));
ok('濃霧では気づかれにくい', aggro.fog < aggro.clear * 0.8,
  `快晴${aggro.clear} 霧${aggro.fog}`);
ok('嵐でも索敵が落ちる', aggro.storm < aggro.clear, `嵐${aggro.storm}`);
ok('霧でも完全には消えない', aggro.fog > aggro.clear * 0.3, String(aggro.fog));

/* ---------- 5. 風がうねる ---------- */
const wind = await page.evaluate(() => {
  const s = window.__game.sky;
  s.setWeather('fair', true);
  const w = [], dirs = [];
  for (let i = 0; i < 60 * 30; i++) {
    s.update(1/30, null);
    if (i % 90 === 0) { w.push(+s.wind.toFixed(3)); dirs.push(+s.windDir[0].toFixed(3)); }
  }
  return { min: Math.min(...w), max: Math.max(...w), n: new Set(w).size,
    dirSpread: Math.max(...dirs) - Math.min(...dirs) };
});
console.log('風:', JSON.stringify(wind));
ok('風は一定ではない', wind.max > wind.min * 1.25, `${wind.min}〜${wind.max}`);
ok('風は正のまま', wind.min > 0, String(wind.min));
ok('風向きが回る', wind.dirSpread > 0.05, String(wind.dirSpread));

/* ---------- 6. 地下には天候が届かない ---------- */
const dg = await page.evaluate(() => {
  const g = window.__game;
  g.sky.setWeather('storm', true); g.sky.wetness = 1; g.sky.snowCover = 0.5;
  const poi = g.pois.find(p => p.dungeonTheme) || g.pois.find(p => p.type === 'grave');
  g.enterDungeon(poi);
  g.update(1/30);
  const inside = { rain: g.sky.rainAmount, snow: g.sky.snowAmount,
    vis: g.sky.visibility, wind: g.sky.wind };
  g.exitDungeon();
  g.sky.setWeather('fair', true);
  return inside;
});
console.log('地下:', JSON.stringify(dg));
ok('地下では雨雪が止む', dg.rain === 0 && dg.snow === 0);
ok('地下では視程が落ちない', dg.vis === 1);

/* ---------- 7. 雨天でも描画が壊れない・重くならない ---------- */
const perf = await page.evaluate(async () => {
  const g = window.__game;
  const run = async (weather, wetness, secs) => {
    g.sky.setWeather(weather, true);
    g.sky.wetness = wetness; g.sky.snowCover = 0;
    g.sky.update(0.016, g);
    let frames = 0;
    const t0 = performance.now();
    await new Promise(res => {
      const tick = () => {
        frames++;
        if (performance.now() - t0 > secs * 1000) res(); else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    return +(frames / ((performance.now() - t0) / 1000)).toFixed(1);
  };
  await run('clear', 0, 5);            // 地形の再構築が落ち着くまで捨てる
  const dryFps = await run('clear', 0, 8);
  const wetFps = await run('storm', 1, 8);
  const dryFps2 = await run('clear', 0, 8);
  return { dryFps, wetFps, dryFps2 };
});
console.log('fps:', JSON.stringify(perf));
const dryAvg = (perf.dryFps + perf.dryFps2) / 2;
// SwiftShader のソフトウェア描画では素の fps が 2 前後しか出ない。
// 絶対値ではなく「描画ループが回り続けているか」と「乾いた時との比」で見る
ok('雨天でも描画が続く', perf.wetFps > 1, `${perf.wetFps}fps`);
ok('濡れ表現のコストが許容範囲', perf.wetFps > dryAvg * 0.8,
  `乾${dryAvg.toFixed(1)} 濡${perf.wetFps}`);
ok('測定が安定している', Math.abs(perf.dryFps - perf.dryFps2) < dryAvg * 0.25,
  `乾1回目${perf.dryFps} 2回目${perf.dryFps2}`);

/* ---------- 8. 濡れると画面が実際に変わる ---------- */
const shot = await page.evaluate(async () => {
  const g = window.__game;
  const grab = async (wetness, snowCover) => {
    g.sky.paused = true; g.sky.time = 0.42;
    g.sky.setWeather('clear', true);
    g.sky.update(0.016, g);
    g.sky.wetness = wetness; g.sky.snowCover = snowCover;
    for (let i = 0; i < 6; i++) await new Promise(r => requestAnimationFrame(r));
    const cv = g.renderer.gl.canvas;
    const tmp = document.createElement('canvas');
    tmp.width = 160; tmp.height = 90;
    tmp.getContext('2d').drawImage(cv, 0, 0, 160, 90);
    const d = tmp.getContext('2d').getImageData(0, 0, 160, 90).data;
    // 画面下半分＝地面。平均輝度と、最も明るい画素の数を見る
    let sum = 0, n = 0, bright = 0;
    for (let y = 50; y < 90; y++) for (let x = 0; x < 160; x++) {
      const i = (y * 160 + x) * 4;
      const l = (d[i] * 0.299 + d[i+1] * 0.587 + d[i+2] * 0.114) / 255;
      sum += l; n++;
      if (l > 0.72) bright++;
    }
    return { lum: +(sum / n).toFixed(4), bright, raw: [...d.slice(0, 12)] };
  };
  const dry = await grab(0, 0);
  const wetOnly = await grab(1, 0);
  const snowy = await grab(0, 1);
  g.sky.wetness = 0; g.sky.snowCover = 0; g.sky.paused = false;
  return { dry, wetOnly, snowy };
});
console.log('地面の明るさ 乾:', shot.dry.lum, '濡:', shot.wetOnly.lum, '雪:', shot.snowy.lum);
ok('濡れると地面の見た目が変わる', Math.abs(shot.wetOnly.lum - shot.dry.lum) > 0.004,
  `差 ${(shot.wetOnly.lum - shot.dry.lum).toFixed(4)}`);
ok('積雪で地面が明るくなる', shot.snowy.lum > shot.dry.lum + 0.02,
  `乾${shot.dry.lum} 雪${shot.snowy.lum}`);
ok('雪面は白く飛ぶ画素が増える', shot.snowy.bright > shot.dry.bright,
  `乾${shot.dry.bright} 雪${shot.snowy.bright}`);

/* ---------- 9. 雨で地面に飛沫が出る ---------- */
const spray = await page.evaluate(async () => {
  const g = window.__game;
  const count = (weather) => {
    g.sky.setWeather(weather, true); g.sky.update(0.016, g);
    g.fx.active.length = 0;
    for (let i = 0; i < 60; i++) g.updateAmbient(1/30);
    return g.fx.active.length;
  };
  const clear = count('clear');
  const rain = count('rain');
  const storm = count('storm');
  g.sky.setWeather('fair', true);
  return { clear, rain, storm };
});
console.log('粒子数:', JSON.stringify(spray));
ok('雨で粒子が湧く', spray.rain > spray.clear + 20, JSON.stringify(spray));
ok('嵐は雨より激しい', spray.storm > spray.rain, `雨${spray.rain} 嵐${spray.storm}`);

/* ---------- 10. セーブに天候の痕跡が残る ---------- */
const save = await page.evaluate(() => {
  const g = window.__game;
  g.sky.wetness = 0.63; g.sky.snowCover = 0.21;
  g.save();
  const raw = JSON.parse(localStorage.getItem('aetheria_save'));
  g.sky.wetness = 0; g.sky.snowCover = 0;
  g.sky.deserialize(raw.sky);
  return { stored: raw.sky, wetness: g.sky.wetness, snowCover: g.sky.snowCover };
});
console.log('save:', JSON.stringify(save));
ok('濡れ・積雪がセーブされる', Math.abs(save.wetness - 0.63) < 0.01 &&
  Math.abs(save.snowCover - 0.21) < 0.01, JSON.stringify(save.stored));

report(errs);
await close();
