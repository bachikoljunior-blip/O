// いまのコードの見取り図を出す。
//
//   node loop/map.mjs
//
// 新しいセッションは 16,000 行を読むところから始まる。そこに毎回20分かけていると、
// 1周ぶんが下読みで終わる。かといって手書きの見取り図はすぐ古くなり、古い地図は
// 無いより悪い —— 読んだ側は正しいと思って動くので。
//
// なのでコードから起こす。中身が変われば出力も変わるので、ずれようがない。
import { readdir, readFile, stat } from 'node:fs/promises';
import { join, relative } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const SRC = join(ROOT, 'src');

async function walk(dir) {
  const out = [];
  for (const name of await readdir(dir)) {
    const p = join(dir, name);
    if ((await stat(p)).isDirectory()) out.push(...await walk(p));
    else if (name.endsWith('.js')) out.push(p);
  }
  return out;
}

const files = (await walk(SRC)).sort();
const rows = [];

for (const f of files) {
  const src = await readFile(f, 'utf8');
  const lines = src.split('\n');

  // The first block of leading comments, if the file opens with one. A module
  // that says what it is in its own words beats anything inferred from names.
  const lead = [];
  for (const l of lines) {
    const t = l.trim();
    if (t.startsWith('//')) lead.push(t.replace(/^\/\/\s?/, ''));
    else if (t.startsWith('/*') || t.startsWith('*') || t === '') { if (lead.length) break; }
    else break;
  }

  const exports = [...src.matchAll(/^export\s+(?:async\s+)?(?:function\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)/gm)]
    .map(m => m[1]);
  const imports = [...src.matchAll(/^import\s.*?from\s+['"]([^'"]+)['"]/gm)]
    .map(m => m[1]).filter(p => p.startsWith('.'));

  rows.push({
    path: relative(ROOT, f),
    lines: lines.length,
    summary: lead[0] || '',
    exports,
    imports,
  });
}

const total = rows.reduce((a, r) => a + r.lines, 0);
console.log(`# 見取り図 — ${rows.length} モジュール / ${total.toLocaleString()} 行`);
console.log(`# node loop/map.mjs で再生成。手で書き足さないこと（次に走らせたら消えます）\n`);

let dir = null;
for (const r of rows) {
  const d = r.path.split('/').slice(0, -1).join('/');
  if (d !== dir) { dir = d; console.log(`\n## ${d}`); }
  console.log(`\n  ${r.path.split('/').pop()}  (${r.lines}行)`);
  if (r.summary) console.log(`    ${r.summary}`);
  if (r.exports.length) {
    const shown = r.exports.slice(0, 12).join(', ');
    console.log(`    export: ${shown}${r.exports.length > 12 ? ` …他${r.exports.length - 12}` : ''}`);
  }
}

// Which modules everything leans on. Touching one of these is not a local change,
// and that is worth knowing before touching it rather than after.
const depended = {};
for (const r of rows) for (const i of r.imports) {
  const key = i.replace(/^\.+\//, '').replace(/\.js$/, '');
  depended[key] = (depended[key] || 0) + 1;
}
const hot = Object.entries(depended).sort((a, b) => b[1] - a[1]).slice(0, 8);
if (hot.length) {
  console.log('\n\n## 依存が集まっているところ（触ると広く効く）\n');
  for (const [k, n] of hot) console.log(`  ${k}  ← ${n} モジュールが読んでいる`);
}
