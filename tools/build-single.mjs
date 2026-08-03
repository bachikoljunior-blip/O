// 単一ファイル版をつくる。
//
// アエテリア戦記は 29 個の ES モジュールと 1 枚の CSS でできている。
// そのままでは URL ひとつで配れないので、esbuild でひとまとめにし、
// 外部への参照が一切ない HTML の断片として書き出す。
// 出力は <!doctype> も <html>/<head>/<body> も含まない（公開側が包む）。
//
//   node tools/build-single.mjs [出力先]

import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = process.argv[2] || resolve(ROOT, 'dist/aetheria.html');

/** index.html の <body> の中身だけを取り出す（読み込みタグは落とす） */
async function bodyOf() {
  const html = await readFile(resolve(ROOT, 'index.html'), 'utf8');
  const m = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  if (!m) throw new Error('index.html に <body> が見つからない');
  return m[1].replace(/<script[^>]*src=[^>]*><\/script>/gi, '').trim();
}

/** src/main.js を 1 本の古典スクリプトに畳む */
async function script() {
  const r = await build({
    entryPoints: [resolve(ROOT, 'src/main.js')],
    bundle: true, format: 'iife', target: 'es2022',
    minify: true, legalComments: 'none', write: false,
    // 配布版に Service Worker は無い。登録の呼び出しごと消す
    define: { 'navigator.serviceWorker': 'undefined' },
  });
  return r.outputFiles[0].text;
}

const [body, css, js] = await Promise.all([
  bodyOf(),
  readFile(resolve(ROOT, 'styles/main.css'), 'utf8'),
  script(),
]);

// 公開側の余白・背景・配色をこの世界のものに戻す。
// 明暗の切り替えには従わない。灰の世界は暗いままでいい。
const frame = `
:root, :root[data-theme="dark"], :root[data-theme="light"] { color-scheme: dark; }
html, body { margin: 0; padding: 0; height: 100%; overflow: hidden;
  background: #08080b; color: #e8dfc8; }
`;

const out = `<style>${frame}${css}</style>\n${body}\n<script>${js}</script>\n`;
await writeFile(OUT, out, 'utf8');
console.log(`${OUT}  ${(out.length / 1024).toFixed(0)} KB`
  + `（本体 ${(js.length / 1024).toFixed(0)} KB / 意匠 ${(css.length / 1024).toFixed(0)} KB）`);
