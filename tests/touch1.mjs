// touch1 — 指で遊ぶときの操作感。
//
// 画面は撮らない。DOM の矩形（getBoundingClientRect / elementFromPoint）と
// window.__game の状態だけで測る。時間で待つ所も作らない：
//   - 状態は harness の until() で待つ
//   - 行動の取りこぼしは、描画に依らない 60fps の手回しで測る
//     （SwiftShader は 2fps しか出ないので、実ループでは 1 フレームが 0.5 秒ある）
//
// 実寸は 1 CSS px = 1/160 インチ（Android dp）で mm に直す。iPhone だと
// 1px = 0.166mm なのでこちらが厳しい側。指の当たりは 7mm（44px）を下限とする。

import { chromium } from 'playwright';
import { serve, until, ok, report } from './harness.mjs';

const CHROME = process.env.CHROME_PATH
  || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const MMPX = 25.4 / 160;
const MIN_MM = 7.0;    // 指の当たりの下限
const MIN_GAP_MM = 1.2; // 隣り合うボタンの縁と縁

// 6.1型を最後に置く。ここだけ開いたまま次の測定へ渡すので、
// 2 つのページが同時に描かれないようにする（SwiftShader は並行に耐えない）
const SCREENS = [
  { name: '5.5型', w: 740, h: 360 },
  { name: '6.7型', w: 932, h: 430 },
  { name: '6.1型', w: 844, h: 390 },
];

const { srv, port } = await serve();
const browser = await chromium.launch({
  executablePath: CHROME,
  args: ['--use-gl=angle', '--use-angle=swiftshader',
    '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const errs = [];

/** 指のある端末として開き、旅を始めたところまで進める */
async function openTouch(w, h) {
  const ctx = await browser.newContext({
    viewport: { width: w, height: h },
    hasTouch: true, isMobile: true, deviceScaleFactor: 3,
  });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => errs.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errs.push(`[console] ${m.text()}`); });
  await page.goto(`http://127.0.0.1:${port}/index.html`, { waitUntil: 'load' });
  if (!await until(page, () => !!document.querySelector('#title')
    && !document.querySelector('#title').classList.contains('hidden'), { timeout: 60000 })) {
    throw new Error('タイトルが出ない');
  }
  await page.click('[data-act="new"]');
  if (!await until(page, () => !!window.__game?.player
    && !document.querySelector('#hud').classList.contains('hidden'), { timeout: 60000 })) {
    throw new Error('旅が始まらない');
  }
  return { page, ctx };
}

/* ============================================================
   1. 押しやすさ・誤爆・視界の占有（5.5〜6.7型）
   ============================================================ */
let behavePage = null, behaveCtx = null;

for (const s of SCREENS) {
  const { page, ctx } = await openTouch(s.w, s.h);

  const m = await page.evaluate((mmpx) => {
    // 「調べる」は普段隠れているので、測る間だけ出す
    const ib = document.querySelector('#interactbtn');
    const wasHidden = ib.classList.contains('hidden');
    ib.classList.remove('hidden');

    const btns = [...document.querySelectorAll('[data-btn]')].map((e) => {
      const r = e.getBoundingClientRect();
      return { n: e.dataset.btn, el: e, r,
        cx: r.left + r.width / 2, cy: r.top + r.height / 2,
        mm: +(Math.min(r.width, r.height) * mmpx).toFixed(2) };
    });

    // 到達率：円の内側をなぞって、いちばん上に来るのが自分自身か
    for (const b of btns) {
      let hit = 0, tot = 0;
      for (let a = 0; a < 12; a++) {
        for (const f of [0.3, 0.6, 0.85]) {
          const ang = a / 12 * Math.PI * 2;
          const px = b.cx + Math.cos(ang) * b.r.width / 2 * f;
          const py = b.cy + Math.sin(ang) * b.r.height / 2 * f;
          if (px < 0 || py < 0 || px > innerWidth || py > innerHeight) continue;
          tot++;
          const t = document.elementFromPoint(px, py);
          if (t && (t === b.el || b.el.contains(t))) hit++;
        }
      }
      b.reach = tot ? Math.round(hit / tot * 100) : 0;
    }

    // 隣り合う組の隙間（縁から縁）
    let minGap = { p: '', mm: 99 };
    for (let i = 0; i < btns.length; i++) {
      for (let j = i + 1; j < btns.length; j++) {
        const a = btns[i], b = btns[j];
        const d = Math.hypot(a.cx - b.cx, a.cy - b.cy)
          - a.r.width / 2 - b.r.width / 2;
        if (d * mmpx < minGap.mm) minGap = { p: `${a.n}↔${b.n}`, mm: +(d * mmpx).toFixed(2) };
      }
    }

    // 触った先の内訳。ボタンでも canvas でもない所は「押しても何も起きない」
    let dead = 0, look = 0, total = 0, rTotal = 0, rLook = 0;
    for (let y = 2; y < innerHeight; y += 4) {
      for (let x = 2; x < innerWidth; x += 4) {
        const e = document.elementFromPoint(x, y);
        total++;
        const onBtn = e && e.closest('[data-btn]');
        const onCanvas = e && e.id === 'view';
        if (onCanvas) look++;
        else if (!onBtn) dead++;
        if (x > innerWidth * 0.45) { rTotal++; if (onCanvas) rLook++; }
      }
    }

    // HUD の占有と重なり
    const sel = ['.vitals', '.topright', '#compass', '.echo', '.questtrack',
      '#targetbar', '#bossbar'];
    const rects = [];
    let area = 0;
    for (const q of sel) {
      const e = document.querySelector(q);
      if (!e || e.classList.contains('hidden')) continue;
      const r = e.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      rects.push({ q, l: r.left, t: r.top, r: r.right, b: r.bottom });
      area += r.width * r.height;
    }
    const overlaps = [];
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i], b = rects[j];
        const w = Math.min(a.r, b.r) - Math.max(a.l, b.l);
        const h = Math.min(a.b, b.b) - Math.max(a.t, b.t);
        if (w > 2 && h > 2) overlaps.push(`${a.q}×${b.q} ${Math.round(w)}x${Math.round(h)}`);
      }
    }

    // 親指の付け根（下の隅）からの距離
    const reachMm = btns.map((b) => ({ n: b.n,
      mm: Math.round(Math.min(
        Math.hypot(innerWidth - b.cx, innerHeight - b.cy),
        Math.hypot(b.cx, innerHeight - b.cy)) * mmpx) }));

    if (wasHidden) ib.classList.add('hidden');
    return {
      btns: btns.map((b) => ({ n: b.n, mm: b.mm, reach: b.reach,
        w: Math.round(b.r.width), h: Math.round(b.r.height) })),
      minGap,
      deadPct: +(dead / total * 100).toFixed(1),
      lookPct: +(look / total * 100).toFixed(1),
      rightLookPct: +(rLook / rTotal * 100).toFixed(1),
      hudPct: +(area / (innerWidth * innerHeight) * 100).toFixed(1),
      overlaps, reachMm,
    };
  }, MMPX);

  console.log(`\n--- ${s.name} ${s.w}x${s.h} ---`);
  console.log('  ' + m.btns.map((b) => `${b.n}=${b.w}x${b.h}px/${b.mm}mm(${b.reach}%)`).join(' '));
  console.log(`  最小の隙間 ${m.minGap.p} ${m.minGap.mm}mm / 死に領域 ${m.deadPct}%`
    + ` / 視点に使える面積 ${m.lookPct}%（右半分 ${m.rightLookPct}%）`);
  console.log(`  HUD 占有 ${m.hudPct}% 重なり=${m.overlaps.length ? m.overlaps.join(',') : 'なし'}`);
  console.log('  下隅からの距離 ' + m.reachMm.map((r) => `${r.n}=${r.mm}mm`).join(' '));

  const small = m.btns.filter((b) => b.mm < MIN_MM);
  ok(`${s.name}：どのボタンも ${MIN_MM}mm 以上`, small.length === 0,
    small.length ? small.map((b) => `${b.n}=${b.mm}mm`).join(' ') : '最小 '
      + Math.min(...m.btns.map((b) => b.mm)) + 'mm');

  const blocked = m.btns.filter((b) => b.reach < 100);
  ok(`${s.name}：全部のボタンに指が届く`, blocked.length === 0,
    blocked.map((b) => `${b.n}=${b.reach}%`).join(' '));

  ok(`${s.name}：隣り合うボタンが ${MIN_GAP_MM}mm 以上離れている`,
    m.minGap.mm >= MIN_GAP_MM, `${m.minGap.p} ${m.minGap.mm}mm`);

  ok(`${s.name}：押しても何も起きない領域が無い`, m.deadPct <= 1.0, m.deadPct + '%');

  ok(`${s.name}：右半分の 8 割以上で視点が動かせる`, m.rightLookPct >= 80,
    m.rightLookPct + '%');

  ok(`${s.name}：HUD が画面の 25% を超えない`, m.hudPct <= 25, m.hudPct + '%');

  ok(`${s.name}：HUD 同士が重なっていない`, m.overlaps.length === 0,
    m.overlaps.join(' '));

  // 戦闘に使う 4 つは親指の届く範囲（55mm）に居ること
  const far = m.reachMm.filter((r) => ['attack', 'dodge', 'block', 'jump'].includes(r.n) && r.mm > 55);
  ok(`${s.name}：攻・避・防・跳が親指の届く範囲にある`, far.length === 0,
    far.map((r) => `${r.n}=${r.mm}mm`).join(' '));

  if (s.w === 844) { behavePage = page; behaveCtx = ctx; }
  else await ctx.close();
}

/* ============================================================
   2. 取りこぼし・誤爆・同時押し（6.1型でまとめて）
   ============================================================ */
const page = behavePage;
console.log('\n--- 取りこぼしと同時押し ---');

// 60fps を手で回して測る。描画にも実時間にも依らない
const sim = await page.evaluate(() => {
  const g = window.__game, p = g.player, inp = g.input;
  const acts = [];
  const oA = p.doAttack.bind(p), oD = p.doDodge.bind(p);
  p.doAttack = (k, gm) => { acts.push('attack:' + k); return oA(k, gm); };
  p.doDodge = (...a) => { acts.push('dodge'); return oD(...a); };
  const step = (n) => { for (let i = 0; i < n; i++) { inp.update(); p.update(1 / 60, g); inp.endFrame(); } };
  const reset = () => {
    p.state = 'idle'; p.comboIndex = 0; p.comboTimer = 0;
    p.stamina = p.maxStamina; p.hp = p.maxHp; acts.length = 0;
  };

  // 弱攻撃 1 回で再び動けるまで何フレーム掛かるか
  reset();
  inp.press('attack'); step(1); inp.release('attack');
  let recover = -1;
  for (let i = 0; i < 240; i++) { step(1); if (p.canAct()) { recover = i + 1; break; } }

  // その硬直の途中で 2 撃目を押したとき、出るか消えるか
  const chain = {};
  for (const d of [2, 6, 12, 20, 30, 40]) {
    reset();
    inp.press('attack'); step(1); inp.release('attack');
    step(d);
    inp.press('attack'); step(1); inp.release('attack');
    step(90);
    chain[d] = acts.filter((a) => a.startsWith('attack')).length;
  }

  // 転がりの硬直も測っておく
  reset();
  inp.press('dodge'); step(1); inp.release('dodge');
  let rollRecover = -1;
  for (let i = 0; i < 240; i++) { step(1); if (p.canAct()) { rollRecover = i + 1; break; } }

  // 転がりが明ける少し前に攻撃を押す（指だとここを狙って押すことになる）
  reset();
  inp.press('dodge'); step(1); inp.release('dodge');
  step(Math.max(1, rollRecover - 6));
  inp.press('attack'); step(1); inp.release('attack');
  step(90);
  const rollThenAttack = acts.slice();

  // 押しっぱなしのゲームパッドで暴発しないこと（press が毎フレーム来る形）
  reset();
  for (let i = 0; i < 40; i++) { inp.press('attack'); inp.update(); p.update(1 / 60, g); inp.endFrame(); }
  inp.release('attack');
  const heldFrames = acts.filter((a) => a.startsWith('attack')).length;

  p.doAttack = oA; p.doDodge = oD;
  return { recover, rollRecover, chain, rollThenAttack, heldFrames };
});
console.log('  弱攻撃の硬直:', sim.recover, 'フレーム =', (sim.recover / 60).toFixed(2), '秒');
console.log('  転がりの硬直:', sim.rollRecover, 'フレーム =', (sim.rollRecover / 60).toFixed(2), '秒');
console.log('  硬直の途中で 2 撃目 → 出た攻撃の数:', JSON.stringify(sim.chain));
console.log('  転がりが明ける 6 フレーム前に攻撃:', JSON.stringify(sim.rollThenAttack));
console.log('  押しっぱなしで出た攻撃:', sim.heldFrames);

const late = Object.entries(sim.chain).filter(([d]) => +d >= 20);
ok('硬直の終わり際に押した 2 撃目が出る（先行入力）',
  late.every(([, n]) => n >= 2), JSON.stringify(sim.chain));
ok('硬直の入り口で押した分は流れる（押しっぱなし扱いにならない）',
  sim.chain[2] === 1, '+2f → ' + sim.chain[2] + ' 回');
ok('転がりが明ける前に押した攻撃が繋がる',
  sim.rollThenAttack.filter((a) => a.startsWith('attack')).length >= 1,
  JSON.stringify(sim.rollThenAttack));
ok('押しっぱなしで攻撃が連射されない', sim.heldFrames === 1, sim.heldFrames + ' 回');

/* --- 実際のタッチ：短いタップ / 指の滑り --- */
const tap = await page.evaluate(() => {
  const inp = window.__game.input;
  const el = document.querySelector('[data-btn="attack"]');
  const r = el.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const fire = (type, x, y, id = 3) => {
    const t = new Touch({ identifier: id, target: el, clientX: x, clientY: y });
    const empty = type === 'touchend' || type === 'touchcancel';
    el.dispatchEvent(new TouchEvent(type, {
      touches: empty ? [] : [t], changedTouches: [t],
      targetTouches: empty ? [] : [t], bubbles: true, cancelable: true }));
  };

  // 同じフレームのうちに押して離す（速い連打の 1 回ぶん）
  inp.btn.clear(); inp.btnPressed.clear(); inp._buf.clear();
  fire('touchstart', cx, cy); fire('touchend', cx, cy);
  const sameFrame = inp.pressed('attack');

  // 押したまま指がボタンの外へ滑って、そこで離す
  inp.btn.clear(); inp.btnPressed.clear(); inp._buf.clear();
  fire('touchstart', cx, cy);
  const held = inp.down('attack');
  fire('touchend', cx + 220, cy - 160);
  const released = !inp.down('attack');

  // 指が離れずに中断された（電話が掛かってきた等）
  inp.btn.clear(); inp.btnPressed.clear(); inp._buf.clear();
  fire('touchstart', cx, cy);
  fire('touchcancel', cx, cy);
  const cancelled = !inp.down('attack');

  inp.btn.clear(); inp.btnPressed.clear(); inp._buf.clear();
  return { sameFrame, held, released, cancelled };
});
console.log('  同一フレームの押して離す:', tap.sameFrame, '/ 滑って離す:', tap.released,
  '/ 中断:', tap.cancelled);
ok('1 フレームより短いタップを取りこぼさない', tap.sameFrame);
ok('押している間は下がったまま', tap.held);
ok('ボタンの外で離しても解除される', tap.released);
ok('タッチが中断されても押しっぱなしにならない', tap.cancelled);

/* --- 長押しで強攻撃が出るところまで見る --- */
await page.evaluate(() => {
  const el = document.querySelector('[data-btn="attack"]');
  const r = el.getBoundingClientRect();
  const t = new Touch({ identifier: 9, target: el,
    clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
  const g = window.__game, p = g.player;
  p.state = 'idle'; p.comboIndex = 0; p.comboTimer = 0; p.stamina = p.maxStamina;
  window.__kinds = [];
  window.__origAttack = p.doAttack.bind(p);
  p.doAttack = (k, gm) => { window.__kinds.push(k); return window.__origAttack(k, gm); };
  el.dispatchEvent(new TouchEvent('touchstart', { touches: [t], changedTouches: [t],
    targetTouches: [t], bubbles: true, cancelable: true }));
});
// 長押しの成立は時間ではなく「hold が付いたか」で待つ
const armed = await until(page, () => document.querySelector('[data-btn="attack"]')
  .classList.contains('hold'), { timeout: 6000, step: 60 });
const holdRes = await page.evaluate(() => {
  const g = window.__game, p = g.player, inp = g.input;
  // 60fps で回して、硬直が明けたときに強攻撃が出るか
  for (let i = 0; i < 120; i++) { inp.update(); p.update(1 / 60, g); inp.endFrame(); }
  const el = document.querySelector('[data-btn="attack"]');
  const t = new Touch({ identifier: 9, target: el, clientX: 0, clientY: 0 });
  el.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [t],
    targetTouches: [], bubbles: true, cancelable: true }));
  for (let i = 0; i < 60; i++) { inp.update(); p.update(1 / 60, g); inp.endFrame(); }
  const kinds = window.__kinds.slice();
  p.doAttack = window.__origAttack;
  return kinds;
});
console.log('  攻撃ボタンを押しっぱなし → 出た攻撃:', JSON.stringify(holdRes), '(hold 付与', armed, ')');
ok('長押しで強攻撃が出る', armed && holdRes.includes('heavy'), JSON.stringify(holdRes));
ok('長押しでも強攻撃は一度だけ',
  holdRes.filter((k) => k === 'heavy').length === 1,
  holdRes.filter((k) => k === 'heavy').length + ' 回');

/* --- 同時押し：移動しながら回避 / 構えながら攻撃 --- */
const multi = await page.evaluate(() => {
  const g = window.__game, p = g.player, inp = g.input;
  const cv = document.querySelector('#view');
  const canvasTouch = (type, x, y, id) => {
    const t = new Touch({ identifier: id, target: cv, clientX: x, clientY: y });
    const empty = type === 'touchend';
    cv.dispatchEvent(new TouchEvent(type, { touches: empty ? [] : [t], changedTouches: [t],
      targetTouches: empty ? [] : [t], bubbles: true, cancelable: true }));
  };
  const btnTouch = (name, type, id) => {
    const el = document.querySelector(`[data-btn="${name}"]`);
    const r = el.getBoundingClientRect();
    const t = new Touch({ identifier: id, target: el,
      clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
    const empty = type === 'touchend';
    el.dispatchEvent(new TouchEvent(type, { touches: empty ? [] : [t], changedTouches: [t],
      targetTouches: empty ? [] : [t], bubbles: true, cancelable: true }));
  };
  const step = () => { inp.update(); p.update(1 / 60, g); inp.endFrame(); };

  // 左でスティックを倒したまま、右で回避
  p.state = 'idle'; p.stamina = p.maxStamina; p.hp = p.maxHp;
  canvasTouch('touchstart', 90, 300, 31);
  canvasTouch('touchmove', 90, 240, 31);
  inp.update();
  const move = { x: +inp.move.x.toFixed(2), y: +inp.move.y.toFixed(2) };
  inp.endFrame();
  btnTouch('dodge', 'touchstart', 32);
  step();
  const dodging = p.state;
  const rolled = p.rollDir ? Math.hypot(p.rollDir[0], p.rollDir[1]) : 0;
  const stickAlive = inp.stick.active;
  const moveAfter = { x: +inp.move.x.toFixed(2), y: +inp.move.y.toFixed(2) };
  btnTouch('dodge', 'touchend', 32);

  // 構え（防）を押したまま攻撃。防はタップでパリィなので、
  // パリィが明けるまで押し続けてから攻撃を重ねる
  p.state = 'idle'; p.stamina = p.maxStamina;
  btnTouch('block', 'touchstart', 33);
  let parryFrames = 0;
  for (let i = 0; i < 120; i++) { step(); parryFrames++; if (p.canAct()) break; }
  const blocking = p.blocking;
  btnTouch('attack', 'touchstart', 34);
  step();
  const bothDown = inp.down('block') && p.state === 'attack';
  btnTouch('attack', 'touchend', 34);
  btnTouch('block', 'touchend', 33);
  canvasTouch('touchend', 90, 240, 31);
  return { move, moveAfter, dodging, rolled, stickAlive, blocking, bothDown, parryFrames };
});
console.log('  移動しながら回避:', JSON.stringify(multi));
ok('スティックを倒したまま回避が出る', multi.dodging === 'roll', multi.dodging);
ok('回避しても移動入力が生きている', multi.stickAlive && Math.hypot(multi.moveAfter.x, multi.moveAfter.y) > 0.5,
  JSON.stringify(multi.moveAfter));
ok('回避が向きを持つ（棒立ちの後退にならない）', multi.rolled > 0.5, String(multi.rolled));
ok('構えたまま攻撃が出る（防を離さずに攻）', multi.bothDown,
  `パリィ ${multi.parryFrames}f のあと 構え=${multi.blocking}`);

/* --- スティックの台座が指を追う --- */
const stick = await page.evaluate(() => {
  const inp = window.__game.input;
  const cv = document.querySelector('#view');
  const ev = (type, x, y) => {
    const t = new Touch({ identifier: 41, target: cv, clientX: x, clientY: y });
    const empty = type === 'touchend';
    cv.dispatchEvent(new TouchEvent(type, { touches: empty ? [] : [t], changedTouches: [t],
      targetTouches: empty ? [] : [t], bubbles: true, cancelable: true }));
  };
  ev('touchstart', 120, 300);
  ev('touchmove', 320, 300); inp.update();
  const far = +inp.move.x.toFixed(2); inp.endFrame();
  ev('touchmove', 250, 300); inp.update();
  const back = +inp.move.x.toFixed(2); inp.endFrame();
  ev('touchend', 250, 300);
  return { far, back, radius: inp.stick.radius };
});
console.log('  スティック 200px 倒す→', stick.far, ' そこから 70px 戻す→', stick.back);
ok('倒しきってもスティックが振り切れる', stick.far >= 0.99, String(stick.far));
ok('倒しきった後に少し戻すと向きが返る', stick.back < 0,
  `70px 戻して ${stick.back}（追わないと +1 のまま）`);

/* --- 左利き配置：ボタンとスティックが同じ親指に載らないこと --- */
const lefty = await page.evaluate(() => {
  const g = window.__game, ui = g.ui, inp = g.input;
  const cv = document.querySelector('#view');
  const probe = (x) => {
    inp.stick.active = false; inp.lookTouch.active = false;
    const t = new Touch({ identifier: 51, target: cv, clientX: x, clientY: innerHeight - 60 });
    cv.dispatchEvent(new TouchEvent('touchstart', { touches: [t], changedTouches: [t],
      targetTouches: [t], bubbles: true, cancelable: true }));
    const s = inp.stick.active;
    cv.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [t],
      targetTouches: [], bubbles: true, cancelable: true }));
    inp.stick.active = false; inp.lookTouch.active = false;
    return s;
  };
  const read = () => {
    const cl = document.querySelector('.cluster').getBoundingClientRect();
    return { clusterOnLeft: cl.left + cl.width / 2 < innerWidth / 2,
      stickLeft: probe(60), stickRight: probe(innerWidth - 60) };
  };
  const was = ui.settings.lefty;
  ui.settings.lefty = false; ui.applySettings();
  const normal = read();
  ui.settings.lefty = true; ui.applySettings();
  const flipped = read();
  ui.settings.lefty = was; ui.applySettings();
  return { normal, flipped };
});
console.log('  右利き:', JSON.stringify(lefty.normal));
console.log('  左利き:', JSON.stringify(lefty.flipped));
ok('右利き配置：ボタンは右、スティックは左',
  !lefty.normal.clusterOnLeft && lefty.normal.stickLeft && !lefty.normal.stickRight,
  JSON.stringify(lefty.normal));
ok('左利き配置：ボタンが左へ移ればスティックも右へ移る',
  lefty.flipped.clusterOnLeft && lefty.flipped.stickRight && !lefty.flipped.stickLeft,
  JSON.stringify(lefty.flipped));

/* --- 反応：指が触れてから状態が変わるまでのフレーム数 --- */
await page.evaluate(() => {
  window.__probe = null;
  const tick = () => {
    const pr = window.__probe;
    if (pr && !pr.done) {
      pr.frames++;
      if (window.__game.player.state === 'attack') pr.done = true;
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});
await page.evaluate(() => {
  const g = window.__game, p = g.player;
  p.state = 'idle'; p.comboIndex = 0; p.comboTimer = 0; p.stamina = p.maxStamina;
  window.__probe = { frames: 0, done: false };
  const el = document.querySelector('[data-btn="attack"]');
  const r = el.getBoundingClientRect();
  const t = new Touch({ identifier: 61, target: el,
    clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
  el.dispatchEvent(new TouchEvent('touchstart', { touches: [t], changedTouches: [t],
    targetTouches: [t], bubbles: true, cancelable: true }));
  el.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [t],
    targetTouches: [], bubbles: true, cancelable: true }));
});
const reacted = await until(page, () => window.__probe?.done, { timeout: 20000, step: 120 });
const frames = await page.evaluate(() => window.__probe.frames);
console.log('  触れてから構えに入るまで:', frames, 'フレーム');
ok('触れた次のフレームで攻撃に入る', reacted && frames <= 2, frames + ' フレーム');

report(errs);
await behaveCtx.close();
await browser.close();
await new Promise((r) => srv.close(r));
