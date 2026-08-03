// boss1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 760, height: 420, audio: true });

/* ---------- 1. 入場の名乗り ---------- */
const intro = await page.evaluate(async () => {
  const g = window.__game;
  const poi = g.pois.find(p => p.type === 'boss');
  g.player.x = poi.x + 4; g.player.z = poi.z + 4;
  g.player.y = g.groundHeight(g.player.x, g.player.z);
  g.startBoss(poi);
  await new Promise(r => setTimeout(r, 120));
  const el = document.querySelector('#bossintro');
  const bar = document.querySelector('#bossbar');
  const ticks = [...bar.querySelectorAll('.bbar u')].map(u => u.style.left);
  return { name: el.querySelector('.bi-name').textContent,
    title: el.querySelector('.bi-title').textContent,
    shown: el.classList.contains('show') && !el.classList.contains('hidden'),
    phaseCls: el.classList.contains('phase'),
    bossName: g.activeBoss.name, ticks,
    phases: g.activeBoss.bossDef.phases.map(p => p.hpAbove),
    music: g.audio.mode };
});
console.log('入場:', JSON.stringify(intro));
ok('入場でボスの名が出る', intro.shown && intro.name === intro.bossName, intro.name);
ok('二つ名も出る', intro.title.length > 0, intro.title);
ok('入場は形態移行の見た目ではない', !intro.phaseCls);
ok('ボス曲になる', intro.music === 'boss' || intro.music === 'final', intro.music);
ok('体力バーに形態の区切りが刻まれる', intro.ticks.length === intro.phases.filter(v => v > 0 && v < 1).length,
  `刻み ${intro.ticks.join(',')} / 形態 ${intro.phases.join(',')}`);

/* ---------- 2. 形態移行 ---------- */
const phase = await page.evaluate(async () => {
  const g = window.__game;
  const b = g.activeBoss;
  const log = { sfx: [], stop: [], shake: [], kick: [] };
  const oPlay = g.audio.play.bind(g.audio);
  g.audio.play = (n, o) => { log.sfx.push(n); return oPlay(n, o); };
  const oStop = g.time.hitstop.bind(g.time);
  g.time.hitstop = (s) => { log.stop.push(+s.toFixed(3)); return oStop(s); };
  const oShake = g.camera.shake.bind(g.camera);
  g.camera.shake = (s) => { log.shake.push(+s.toFixed(2)); return oShake(s); };
  const oKick = g.camera.kick.bind(g.camera);
  g.camera.kick = (a, c, s) => { log.kick.push(+s.toFixed(2)); return oKick(a, c, s); };
  const musicBefore = g.audio.mode;
  const before = b.phaseIndex;
  // 第2形態のしきい値を割るまで削る
  b.hp = b.maxHp * (b.bossDef.phases[1].hpAbove - 0.01);
  b.update(1/60, g);
  await new Promise(r => setTimeout(r, 120));
  const el = document.querySelector('#bossintro');
  const out = { before, after: b.phaseIndex, musicBefore, musicAfter: g.audio.mode,
    log, transitioning: +b.transitioning.toFixed(2),
    banner: el.querySelector('.bi-name').textContent,
    subtitle: el.querySelector('.bi-title').textContent,
    phaseCls: el.classList.contains('phase'),
    shown: el.classList.contains('show') };
  g.audio.play = oPlay; g.time.hitstop = oStop;
  g.camera.shake = oShake; g.camera.kick = oKick;
  return out;
});
console.log('形態移行:', JSON.stringify(phase));
ok('第2形態に移る', phase.after === phase.before + 1, `${phase.before} → ${phase.after}`);
ok('移行中は無敵の間がある', phase.transitioning > 0.5, `${phase.transitioning}s`);
ok('曲が一段上がる', phase.musicBefore === 'boss' && phase.musicAfter === 'final',
  `${phase.musicBefore} → ${phase.musicAfter}`);
ok('移行の瞬間に間が入る', phase.log.stop.length > 0 && phase.log.stop[0] > 0.15,
  JSON.stringify(phase.log.stop));
ok('画面が揺れて突かれる', phase.log.shake.length > 0 && phase.log.kick.length > 0,
  `揺れ${phase.log.shake} 突き${phase.log.kick}`);
ok('形態移行の名乗りが出る', phase.shown && phase.phaseCls && phase.banner.length > 0,
  `${phase.banner} / ${phase.subtitle}`);
ok('移行の音が鳴る', phase.log.sfx.includes('boss_phase'), phase.log.sfx.join(','));

/* ---------- 3. 討伐の余韻 ---------- */
const win = await page.evaluate(async () => {
  const g = window.__game;
  const b = g.activeBoss;
  const sfx = [];
  const oPlay = g.audio.play.bind(g.audio);
  g.audio.play = (n, o) => { sfx.push({ n, t: +g.victoryT?.toFixed?.(2) }); return oPlay(n, o); };
  b.transitioning = 0;
  b.hp = 1;
  b.takeDamage(9999, { source: g.player, poise: 999, ignoreIFrames: true }, g);
  // 演出そのものを一定の刻みで進める。
  // SwiftShader では 1 フレームが 0.5 秒近くかかり、frame() の raw は 0.1 秒に
  // 丸められるので、壁時計で測ると 5 倍遅く見えてしまう（実機では起きない）
  const trace = [];
  let acc = 0;
  for (let i = 0; i < 44; i++) {
    g.updateVictory(0.1);
    acc += 0.1;
    trace.push({ t: +acc.toFixed(2),
      scale: +g.time.scale.toFixed(3), vic: +(g.victory || 0).toFixed(3),
      music: g.audio.mode,
      banner: document.querySelector('#bossintro').classList.contains('show'),
      felled: document.querySelector('#bossintro').classList.contains('felled') });
    if (i % 6 === 0) await new Promise(r => setTimeout(r, 60));  // 表示を反映させる
  }
  g.audio.play = oPlay;
  // UI が閉じるのは実時間の setTimeout 側なので、少し待つ
  await new Promise(r => setTimeout(r, 2800));
  return { sfx, trace, dead: b.dead,
    endScale: g.time.scale, endVic: g.victory || 0,
    barHidden: document.querySelector('#bossbar').classList.contains('hidden') };
});
const t = win.trace;
const at = (sec) => t.reduce((best, r) => Math.abs(r.t - sec) < Math.abs(best.t - sec) ? r : best, t[0]);
console.log('討伐の推移:');
for (const sec of [0.2, 0.6, 1.0, 1.6, 2.2, 2.6, 3.2, 4.0]) {
  const r = at(sec);
  console.log(`  ${String(r.t).padStart(4)}s  時間×${String(r.scale).padEnd(5)} `
    + `演出${String(r.vic).padEnd(5)} 曲=${(r.music||'—').padEnd(7)} `
    + `名乗り=${r.banner ? (r.felled ? '討伐' : 'あり') : '—'}`);
}
console.log('音:', JSON.stringify(win.sfx.map(s => s.n)));
ok('倒れる', win.dead);
ok('時間が沈む', Math.min(...t.map(r => r.scale)) < 0.4,
  `最小 ×${Math.min(...t.map(r => r.scale))}`);
ok('やがて時間は戻る', win.endScale === 1, `終値 ×${win.endScale}`);
ok('画面の演出が立ち上がって消える',
  Math.max(...t.map(r => r.vic)) > 0.9 && win.endVic === 0,
  `最大 ${Math.max(...t.map(r => r.vic))} 終値 ${win.endVic}`);
ok('曲がいったん止まる', t.some(r => r.music === 'none'),
  [...new Set(t.map(r => r.music))].join(' → '));
ok('最後は探索曲に戻る', t[t.length-1].music === 'explore', t[t.length-1].music);
ok('討伐の名乗りが出る', t.some(r => r.felled && r.banner));
ok('討伐の一打が鳴る', win.sfx.some(s => s.n === 'boss_fell'), win.sfx.map(s=>s.n).join(','));
ok('静けさのあと勝利の和音が鳴る', win.sfx.some(s => s.n === 'victory'));
ok('一打より和音のほうが後', (() => {
  const a = win.sfx.findIndex(s => s.n === 'boss_fell');
  const c = win.sfx.findIndex(s => s.n === 'victory');
  return a >= 0 && c > a;
})());
ok('最後にボスUIが消える', win.barHidden);

/* ---------- 3.5 名乗りが実際に見えるところまで出る ---------- */
const vis = await page.evaluate(async () => {
  const g = window.__game;
  g.ui.bossFelled('試 験');
  const trace = [];
  // SwiftShader はフレーム間隔が 0.5 秒近いので、CSS の 0.5 秒フェードが
  // 反映されるまでに実時間で 1 秒近くかかる。クラスではなく実効不透明度で見る
  for (let i = 0; i < 14; i++) {
    await new Promise(r => setTimeout(r, 250));
    const el = document.querySelector('#bossintro');
    trace.push(+getComputedStyle(el).opacity);
  }
  const el = document.querySelector('#bossintro');
  const rule = getComputedStyle(document.querySelector('#bossintro .bi-rule'));
  return { trace, peak: Math.max(...trace),
    nameOpacity: +getComputedStyle(document.querySelector('#bossintro .bi-name')).opacity,
    ruleWidth: parseFloat(rule.width),
    color: getComputedStyle(document.querySelector('#bossintro .bi-title')).color,
    size: parseFloat(getComputedStyle(document.querySelector('#bossintro .bi-name')).fontSize),
    display: getComputedStyle(el).display };
});
console.log('名乗りの不透明度:', JSON.stringify(vis.trace));
ok('名乗りは実際に不透明になる（クラスだけでなく）', vis.peak >= 0.99, `最大 ${vis.peak}`);
ok('文字も不透明になる', vis.nameOpacity >= 0.99, String(vis.nameOpacity));
ok('討伐の名乗りは大きい', vis.size >= 30, `${vis.size}px`);
ok('下の罫線が伸びる', vis.ruleWidth > 100, `${vis.ruleWidth}px`);

/* ---------- 4. 演出中も操作が死なない ---------- */
const live = await page.evaluate(async () => {
  const g = window.__game;
  // もう一度ボスを立て、討伐直後に動けるか見る
  const poi = g.pois.find(p => p.type === 'boss' && !p.cleared) || g.pois.find(p => p.type === 'boss');
  g.player.x = poi.x + 4; g.player.z = poi.z + 4;
  g.player.y = g.groundHeight(g.player.x, g.player.z);
  g.startBoss(poi);
  const b = g.activeBoss;
  b.hp = 1;
  b.takeDamage(9999, { source: g.player, poise: 999, ignoreIFrames: true }, g);
  await new Promise(r => setTimeout(r, 700));   // スロー中
  const x0 = g.player.x, z0 = g.player.z;
  const scaleDuring = g.time.scale;
  for (let i = 0; i < 40; i++) {
    g.player.tryMove(g, g.player.x + 0.05, g.player.z);
    await new Promise(r => requestAnimationFrame(r));
  }
  const moved = Math.hypot(g.player.x - x0, g.player.z - z0);
  await new Promise(r => setTimeout(r, 3600));
  return { moved: +moved.toFixed(2), scaleDuring: +scaleDuring.toFixed(2),
    scaleAfter: g.time.scale, vic: g.victory || 0 };
});
console.log('演出中:', JSON.stringify(live));
ok('スロー中でも動ける', live.moved > 0.5, `${live.moved}m`);
ok('スローが掛かっている最中だった', live.scaleDuring < 0.6, `×${live.scaleDuring}`);
ok('演出が終われば元に戻る', live.scaleAfter === 1 && live.vic === 0, JSON.stringify(live));

/* ---------- 5. 画面が実際に変わる ---------- */
const pix = await page.evaluate(async () => {
  const g = window.__game;
  const grab = async (v) => {
    g.victory = v;
    for (let i = 0; i < 8; i++) await new Promise(r => requestAnimationFrame(r));
    const cv = g.renderer.gl.canvas;
    const t = document.createElement('canvas');
    t.width = 160; t.height = 90;
    const cx = t.getContext('2d');
    cx.drawImage(cv, 0, 0, 160, 90);
    const d = cx.getImageData(0, 0, 160, 90).data;
    // 彩度（最大成分 - 最小成分）の平均と、四隅の暗さ
    let sat = 0, n = 0, corner = 0, cn = 0;
    for (let y = 0; y < 90; y++) for (let x = 0; x < 160; x++) {
      const i = (y * 160 + x) * 4;
      const mx = Math.max(d[i], d[i+1], d[i+2]), mn = Math.min(d[i], d[i+1], d[i+2]);
      sat += (mx - mn) / 255; n++;
      if ((x < 16 || x > 143) && (y < 9 || y > 80)) {
        corner += (d[i]*0.299 + d[i+1]*0.587 + d[i+2]*0.114) / 255; cn++;
      }
    }
    return { sat: +(sat/n).toFixed(4), corner: +(corner/cn).toFixed(4) };
  };
  const off = await grab(0);
  const on = await grab(1);
  g.victory = 0;
  return { off, on };
});
console.log('画面:', JSON.stringify(pix));
ok('討伐中は彩度が落ちる', pix.on.sat < pix.off.sat * 0.75,
  `通常 ${pix.off.sat} → 討伐 ${pix.on.sat}`);
ok('討伐中は周辺が締まる', pix.on.corner < pix.off.corner,
  `通常 ${pix.off.corner} → 討伐 ${pix.on.corner}`);

report(errs);
await close();
