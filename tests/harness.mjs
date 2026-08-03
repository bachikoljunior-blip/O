// 計測ハーネス。
//
// 各周で「直す前に測る」ための土台。個々の測定はここではなく tests/*.mjs にある。
//
// SwiftShader 越しだと 2fps 程度しか出ないので、
//   - 数値は window.__game から取る（rAF を待つ計測は壊れる）
//   - 同時に何本も走らせない（並列で 12 件の偽の失敗を出したことがある）
// この二つは守ること。

import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = fileURLToPath(new URL('..', import.meta.url));
const CHROME = process.env.CHROME_PATH
  || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json', '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml', '.png': 'image/png',
};

// 一度読んだものは持っておく。32 個のモジュールを毎回ディスクから読むと、
// 起動が延びて、時間待ちで書かれた測定が落ちる
const cache = new Map();

/** ゲームを配る静的サーバ。テストごとに空きポートで立てる */
export function serve(root = ROOT) {
  return new Promise((resolve) => {
    const srv = createServer(async (req, res) => {
      const rel = normalize(decodeURIComponent(req.url.split('?')[0]))
        .replace(/^(\.\.[/\\])+/, '');
      try {
        let hit = cache.get(rel);
        if (!hit) {
          let file = join(root, rel);
          if ((await stat(file)).isDirectory()) file = join(file, 'index.html');
          hit = { type: MIME[extname(file)] || 'application/octet-stream',
            body: await readFile(file) };
          cache.set(rel, hit);
        }
        res.writeHead(200, { 'content-type': hit.type, 'cache-control': 'no-cache' });
        res.end(hit.body);
      } catch {
        res.writeHead(404).end('not found');
      }
    });
    srv.keepAliveTimeout = 5000;
    srv.listen(0, '127.0.0.1', () => resolve({ srv, port: srv.address().port }));
  });
}

/**
 * 条件が満たされるまで待つ。
 *
 * SwiftShader だと 2fps しか出ないので、「900ms 待てば描画が 1 周した
 * はず」という前提の測定は落ちる。時間ではなく状態を待つこと。
 */
export async function until(page, fn, { timeout = 8000, step = 250 } = {}) {
  const t0 = Date.now();
  for (;;) {
    if (await page.evaluate(fn)) return true;
    if (Date.now() - t0 > timeout) return false;
    await page.waitForTimeout(step);
  }
}

/**
 * ブラウザを開き、新しい旅を始めたところまで進める。
 * @returns page / close / errs（ページ内で出た例外と console.error）
 */
export async function open(opts = {}) {
  const { width = 640, height = 360, audio = false, start = true } = opts;
  const { srv, port } = await serve();
  const args = ['--use-gl=angle', '--use-angle=swiftshader',
    '--enable-unsafe-swiftshader', '--no-sandbox'];
  if (audio) args.push('--autoplay-policy=no-user-gesture-required');
  const browser = await chromium.launch({ executablePath: CHROME, args });
  const page = await browser.newPage({ viewport: { width, height } });
  const errs = [];
  page.on('pageerror', (e) => errs.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errs.push(`[console] ${m.text()}`); });
  await page.goto(`http://127.0.0.1:${port}/index.html`, { waitUntil: 'load' });
  await page.waitForTimeout(6000);
  if (start) {
    await page.click('[data-act="new"]');
    await page.waitForTimeout(9000);
  }
  const close = async () => {
    await browser.close();
    await new Promise((r) => srv.close(r));
  };
  return { page, close, errs, port, browser };
}

/* ------------------------------------------------------------ 判定 */
let passed = 0, failed = 0;
const fails = [];

export function ok(label, cond, extra = '') {
  if (cond) passed++; else { failed++; fails.push(label); }
  console.log(`${cond ? '  OK  ' : '  FAIL'} ${label}${extra ? `  ${extra}` : ''}`);
  return !!cond;
}

/** 最後に呼ぶ。落ちていれば終了コードを 1 にする */
export function report(errs = []) {
  if (errs.length) console.log('errors:', errs.slice(0, 6).join('\n'));
  else console.log('errors: none');
  console.log(`== ${passed} OK / ${failed} FAIL`);
  if (failed) process.exitCode = 1;
  return { passed, failed, fails };
}

/* ------------------------------------------------------ よく使う下ごしらえ */

/**
 * 平らな地面へプレイヤーを移す。
 * 「平地」と思って測ったら坂だった、という取り違えを何度もやったので共通化した。
 */
export async function flatGround(page, { flat = 1.2, span = 40, clear = 2.5 } = {}) {
  return page.evaluate(({ flat, span, clear }) => {
    const g = window.__game, p = g.player;
    for (let i = 0; i < 8000; i++) {
      const a = i * 2.39996, r = 30 + i * 0.25;
      const x = p.x + Math.cos(a) * r, z = p.z + Math.sin(a) * r;
      if (g.blockers.at(x, z)) continue;
      const out = [0, 0];
      if (g.collide(x, z, clear, out)) continue;
      const h0 = g.groundHeight(x, z);
      let level = true;
      for (let d = 1; d <= span; d += 2) {
        if (Math.abs(g.groundHeight(x + d, z) - h0) > flat) { level = false; break; }
      }
      if (!level || h0 < 1) continue;
      p.x = x; p.z = z; p.y = h0;
      return { x: Math.round(x), z: Math.round(z), h: Math.round(h0) };
    }
    return null;
  }, { flat, span, clear });
}

/** 中央値 */
export const median = (l) => {
  const s = [...l].sort((a, b) => a - b);
  return s.length ? s[Math.floor(s.length / 2)] : null;
};
