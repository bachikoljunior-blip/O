// dist1
import { readFile } from 'node:fs/promises';
import { open, ok, report } from './harness.mjs';

const { page, close, errs } = await open({ width: 700, height: 400, audio: true, start: false });

// 外部への読み込みを一切許さない。単一ファイルで完結しているかを機械で確かめる
const outside = [];
await page.route('**/*', (route) => {
  const u = route.request().url();
  if (u.startsWith('data:') || u.startsWith('blob:')) return route.continue();
  outside.push(u); return route.abort();
});
const frag = await readFile(new URL('../dist/aetheria.html', import.meta.url), 'utf8');
await page.setContent(`<!doctype html><html><head><meta charset="utf-8"></head><body>${frag}</body></html>`,
  { waitUntil: 'load' });
await page.waitForTimeout(9000);

const boot = await page.evaluate(() => ({
  hasGame: !!window.__game,
  title: document.querySelector('#title') ? !document.querySelector('#title').classList.contains('hidden') : null,
  canvas: (() => { const c = document.querySelector('#view'); return c ? `${c.width}x${c.height}` : null; })(),
  gl: (() => { const c = document.querySelector('#view'); return !!(c && (c.getContext('webgl2') || true)); })(),
}));
console.log('起動:', JSON.stringify(boot));
ok('単一ファイルだけで起動する', boot.hasGame, String(boot.hasGame));
ok('外部への読み込みが 0 件', outside.length === 0, outside.slice(0,3).join(' ') || '0件');
ok('画面が確保されている', !!boot.canvas, String(boot.canvas));

await page.click('[data-act="new"]');
await page.waitForTimeout(11000);
const run = await page.evaluate(() => {
  const g = window.__game;
  return { running: g.running, poi: g.pois.length, hp: Math.round(g.player.hp),
    frames: g.frames ?? null, enemies: g.enemies.length,
    hudShown: !document.querySelector('#hud').classList.contains('hidden') };
});
console.log('遊べる状態:', JSON.stringify(run));
ok('新しい旅が始まる', run.running, String(run.running));
ok('世界が生成されている', run.poi > 30, `${run.poi}箇所`);
ok('HUD が出る', run.hudShown);

// 描画が実際に進むか（画面が真っ暗のままでない）
const shot = await page.screenshot();
const lum = await page.evaluate(async (b64) => {
  const img = new Image();
  img.src = 'data:image/png;base64,' + b64;
  await img.decode();
  const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
  const x = c.getContext('2d'); x.drawImage(img, 0, 0);
  const d = x.getImageData(0, 0, c.width, c.height).data;
  let s = 0; for (let i = 0; i < d.length; i += 4) s += (d[i]+d[i+1]+d[i+2])/3;
  return +(s / (d.length/4) / 255).toFixed(4);
}, shot.toString('base64'));
console.log('画面の明るさ:', lum);
ok('世界が実際に描かれている', lum > 0.04 && lum < 0.95, String(lum));

/* 二重定義の直りを確かめる：背後の点で照準が消えるか */
const aim = await page.evaluate(async () => {
  const g = window.__game, p = g.player;
  const out = [0, 0, 0];
  // 目の前と、真後ろ
  const fx = g.camera.pos[0], fy = g.camera.pos[1], fz = g.camera.pos[2];
  const d = [Math.sin(g.camera.yaw), 0, Math.cos(g.camera.yaw)];
  const front = g.camera.onScreen(fx + d[0]*30, fy, fz + d[2]*30, out);
  const fo = [...out];
  const back = g.camera.onScreen(fx - d[0]*30, fy, fz - d[2]*30, out);
  const bo = [...out];
  // 実際の照準表示も見る
  g.unlocked.weapons.add('bow');
  p.equip.weapon = 'bow'; p.recalc();
  g.camera.yaw = p.yaw; g.camera.pitch = 0.05;
  await new Promise(r => setTimeout(r, 900));
  const shownFront = !document.querySelector('#aimdot').classList.contains('hidden');
  g.camera.yaw = p.yaw + Math.PI;   // 撃つ先を背中に回す
  await new Promise(r => setTimeout(r, 1200));
  const shownBack = !document.querySelector('#aimdot').classList.contains('hidden');
  p.equip.weapon = 'longsword'; p.recalc();
  return { front, back, fo, bo, shownFront, shownBack };
});
console.log('照準:', JSON.stringify(aim));
ok('前方の点は画面内と判定される', aim.front === true, String(aim.front));
ok('背後の点は画面外と判定される', aim.back === false, String(aim.back));
ok('奥行きが書き込まれる（前は正・後ろは負）', aim.fo[2] > 0 && aim.bo[2] < 0,
  `${aim.fo[2].toFixed(1)} / ${aim.bo[2].toFixed(1)}`);
ok('着弾点が画面内なら印が出る', aim.shownFront, String(aim.shownFront));
ok('着弾点が背後なら印が消える', !aim.shownBack, String(aim.shownBack));

ok('読み込みで例外が出ない', errs.length === 0, errs.length ? errs[0] : '0件');

report(errs);
await close();
