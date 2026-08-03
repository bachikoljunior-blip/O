// spat1
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 640, height: 360, audio: true });

/* ---------- 1. 減衰・定位・高域の減りが距離と向きで決まる ---------- */
const curve = await page.evaluate(() => {
  const a = window.__game.audio;
  a.setListener(0, 0, 0);            // 原点で +z を向いて立つ
  const rows = [];
  for (const d of [0, 4, 8, 16, 32, 60, 89, 95]) {
    const s = a.spatial(0, d);       // 正面 d メートル
    rows.push({ d, atten: s ? +s.atten.toFixed(4) : null,
      cutoff: s ? Math.round(s.cutoff) : null, pan: s ? +s.pan.toFixed(3) : null });
  }
  const dirs = {
    front: a.spatial(0, 20), back: a.spatial(0, -20),
    right: a.spatial(20, 0), left: a.spatial(-20, 0),
    close: a.spatial(1, 0),
  };
  // 向きを変えたら定位も変わる
  a.setListener(0, 0, Math.PI / 2);  // 右（+x）を向く
  const turned = { wasRight: a.spatial(20, 0), wasFront: a.spatial(0, 20) };
  a.setListener(0, 0, 0);
  const out = {};
  for (const [k, v] of Object.entries({ ...dirs, ...turned }))
    out[k] = v ? +v.pan.toFixed(3) : null;
  return { rows, pan: out };
});
console.log('距離ごと（正面）:');
for (const r of curve.rows)
  console.log(`  ${String(r.d).padStart(3)}m  音量 ${r.atten === null ? '—（作らない）' : r.atten}  `
    + `高域 ${r.cutoff === null ? '—' : r.cutoff + 'Hz'}`);
console.log('定位:', JSON.stringify(curve.pan));
const at = (d) => curve.rows.find(r => r.d === d);
ok('近くは減衰しない', at(4).atten === 1 && at(8).atten === 1, `4m ${at(4).atten} / 8m ${at(8).atten}`);
ok('遠いほど小さくなる',
  at(8).atten > at(16).atten && at(16).atten > at(32).atten && at(32).atten > at(60).atten,
  curve.rows.slice(2,6).map(r=>r.atten).join(' > '));
ok('遠すぎる音は作らない', at(95).atten === null && at(89).atten !== null,
  `89m ${at(89).atten} / 95m ${at(95).atten}`);
ok('端で音が途切れない（0 に向かって落ちる）', at(89).atten < 0.02, String(at(89).atten));
ok('遠いほど高いほうが減る',
  at(4).cutoff > at(16).cutoff && at(16).cutoff > at(60).cutoff,
  `${at(4).cutoff} > ${at(16).cutoff} > ${at(60).cutoff}Hz`);
ok('右の音は右へ振れる', curve.pan.right > 0.7, String(curve.pan.right));
ok('左の音は左へ振れる', curve.pan.left < -0.7, String(curve.pan.left));
ok('正面と背後は振れない',
  Math.abs(curve.pan.front) < 0.05 && Math.abs(curve.pan.back) < 0.05,
  `前 ${curve.pan.front} / 後 ${curve.pan.back}`);
ok('足元の音は振り切らない', Math.abs(curve.pan.close) < 0.3, String(curve.pan.close));
ok('真横でも片耳が無音にならない', Math.abs(curve.pan.right) < 0.95, String(curve.pan.right));
ok('向きを変えると定位も変わる',
  Math.abs(curve.pan.wasRight) < 0.05 && curve.pan.wasFront < -0.7,
  `右だった音 ${curve.pan.wasRight} / 正面だった音 ${curve.pan.wasFront}`);

/* ---------- 2. 実際に鳴らすと経路が組まれる ---------- */
const graph = await page.evaluate(() => {
  const a = window.__game.audio;
  a.resume();
  a.setListener(0, 0, 0);
  const made = [];
  const oPan = a.ctx.createStereoPanner.bind(a.ctx);
  a.ctx.createStereoPanner = () => { const n = oPan(); made.push(n); return n; };
  a.play('hit_flesh', { weight: 0.5, x: 12, z: 0 });   // 右 12m
  const withPos = made.length;
  const panVal = made.length ? +made[0].pan.value.toFixed(3) : null;
  made.length = 0;
  a.play('hit_flesh', { weight: 0.5 });                // 位置なし
  const noPos = made.length;
  made.length = 0;
  a.play('hit_flesh', { weight: 0.5, x: 400, z: 0 });  // 遠すぎる
  const tooFar = made.length;
  made.length = 0;
  a.footstep('grass', 0.4, { x: -10, z: 0 });
  const footPan = made.length ? +made[0].pan.value.toFixed(3) : null;
  const footNodes = made.length;
  made.length = 0;
  a.footstep('grass', 0.4);
  const footNoPos = made.length;
  a.ctx.createStereoPanner = oPan;
  return { withPos, panVal, noPos, tooFar, footPan, footNodes, footNoPos };
});
console.log('経路:', JSON.stringify(graph));
ok('位置つきの音は定位経路を通る', graph.withPos > 0, `${graph.withPos}個`);
ok('その定位が右に寄る', graph.panVal > 0.7, String(graph.panVal));
ok('位置なしの音は今までどおり中央', graph.noPos === 0, `${graph.noPos}個`);
ok('遠すぎる音は node を作らない', graph.tooFar === 0, `${graph.tooFar}個`);
ok('足音も位置で鳴る', graph.footNodes > 0 && graph.footPan < -0.7, String(graph.footPan));
ok('自分の足音は中央のまま', graph.footNoPos === 0, `${graph.footNoPos}個`);

/* ---------- 3. 誰の音に位置がつくか ---------- */
const who = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const { spawnEnemy } = await import('/src/game/enemies.js');
  const calls = [];
  const oPlay = g.audio.play.bind(g.audio);
  g.audio.play = (n, o = {}) => { calls.push({ n, pos: o.x !== undefined }); return oPlay(n, o); };
  const oFoot = g.audio.footstep.bind(g.audio);
  const foots = [];
  g.audio.footstep = (s, v, at) => { foots.push(!!at); return oFoot(s, v, at); };

  // 自分の攻撃
  calls.length = 0;
  p.doAttack('light', g);
  const mine = calls.filter(c => c.n === 'swing');

  // 敵の攻撃
  g.enemies.length = 0;
  const e = spawnEnemy('bandit', { x: p.x + 14, y: p.y, z: p.z, yaw: 0, leash: 400 });
  g.enemies.push(e);
  calls.length = 0;
  e.beginAttack(e.arch.attacks[0], g, false);
  const theirs = calls.filter(c => c.n === 'swing');

  // 敵に当てた音と、自分が受けた音
  calls.length = 0;
  g.impact(e, 10, { source: p }, false);
  const onThem = calls.filter(c => c.n.startsWith('hit_'));
  calls.length = 0;
  g.impact(p, 10, { source: g.enemies[0] }, false);
  const onMe = calls.filter(c => c.n.startsWith('hit_'));

  // 足音
  foots.length = 0;
  e.onLand(6, g);
  const eFoot = foots.slice();
  foots.length = 0;
  p.onLand(6, g);
  const pFoot = foots.slice();

  g.audio.play = oPlay; g.audio.footstep = oFoot;
  g.enemies.length = 0;
  return { mine: mine.map(c=>c.pos), theirs: theirs.map(c=>c.pos),
    onThem: onThem.map(c=>c.pos), onMe: onMe.map(c=>c.pos), eFoot, pFoot };
});
console.log('位置がつくか:', JSON.stringify(who));
ok('自分の剣の音は中央', who.mine.length > 0 && who.mine.every(v => !v), JSON.stringify(who.mine));
ok('敵の剣の音には位置がつく', who.theirs.length > 0 && who.theirs.every(v => v), JSON.stringify(who.theirs));
ok('敵に当てた音には位置がつく', who.onThem.length > 0 && who.onThem.every(v => v), JSON.stringify(who.onThem));
ok('自分が受けた音は中央', who.onMe.length > 0 && who.onMe.every(v => !v), JSON.stringify(who.onMe));
ok('敵の足音には位置がつく', who.eFoot.length > 0 && who.eFoot.every(v => v), JSON.stringify(who.eFoot));
ok('自分の足音は中央', who.pFoot.length > 0 && who.pFoot.every(v => !v), JSON.stringify(who.pFoot));

/* ---------- 4. 聴き手が毎フレーム追いつく ---------- */
const listener = await page.evaluate(async () => {
  const g = window.__game;
  const before = { x: g.audio.lx, z: g.audio.lz, yaw: g.audio.lyaw };
  g.player.x += 40; g.player.z += 25;
  g.player.y = g.groundHeight(g.player.x, g.player.z);
  for (let i = 0; i < 40; i++) g.update(1/60);
  const after = { x: g.audio.lx, z: g.audio.lz, yaw: g.audio.lyaw };
  return { before, after, cam: { x: g.camera.pos[0], z: g.camera.pos[2], yaw: g.camera.yaw } };
});
console.log('聴き手:', JSON.stringify(listener));
ok('聴き手がカメラに追いつく',
  Math.abs(listener.after.x - listener.cam.x) < 0.01
  && Math.abs(listener.after.z - listener.cam.z) < 0.01,
  `聴き手 ${listener.after.x.toFixed(1)},${listener.after.z.toFixed(1)} / カメラ ${listener.cam.x.toFixed(1)},${listener.cam.z.toFixed(1)}`);
ok('動けば聴き手も動く',
  Math.hypot(listener.after.x - listener.before.x, listener.after.z - listener.before.z) > 5,
  `${Math.round(Math.hypot(listener.after.x-listener.before.x, listener.after.z-listener.before.z))}m`);

report(errs);
await close();
