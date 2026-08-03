// テストを順に回す。
//
// 並列にしないのは速度の都合ではない。SwiftShader を同時に何本も立てると
// 偽の失敗が出る（実際に 12 件出したことがある）。1 本ずつ回すこと。
//
//   npm test              すべて
//   npm test -- 戦        名前に「戦」を含むものだけ
//   npm test -- --list    一覧だけ出す

import { spawn } from 'node:child_process';
import { readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));

/** 何を測っているか。名前だけでは分からないので添える */
const WHAT = {
  quest1: 'クエスト・会話',
  ui1: '画面と操作',
  upg1: '強化と見た目',
  audio1: '音楽と環境音',
  spat1: '音の距離感と定位',
  boss1: 'ボス戦の演出',
  dboss: '地下のボスと階層',
  dun3: '地下の取り分',
  econ2: '採取と資源',
  expl2: '探索の報い',
  tell3: '予備動作の見切り',
  ranged2: '遠隔・着弾点の予測',
  weather1: '天候の見た目',
  wx2: '天候の手応え',
  bird1: '鳥',
  fish1: '水の生き物',
  fishing1: '釣り',
  touch1: '指の操作',
  perf1: '毎フレームの負担',
  trav1: '街道の往来',
  caravan1: '隊商',
  dray1: '荷車と積荷',
  ambush1: '街道の襲撃',
  npc1: '村人の一日',
  village1: '村の衛兵',
  poi1: 'ランドマーク',
  dist1: '単一ファイル版',
  render1: '描画が止まっていないか',
  gang2: '囲まれたとき（測定のみ）',
  balance: '難度の掃引（測定のみ）',
  bench: '地形の速度（測定のみ）',
};

/** 上から順に回す。壊れやすいものほど先に置いて、早く気づけるようにする */
const ORDER = ['render1', 'dist1', 'quest1', 'ui1', 'upg1', 'poi1', 'npc1', 'village1', 'trav1', 'caravan1', 'dray1', 'ambush1', 'bird1', 'fish1', 'fishing1', 'touch1', 'perf1',
  'audio1', 'spat1', 'weather1', 'wx2', 'ranged2', 'tell3', 'boss1', 'dboss',
  'dun3', 'econ2', 'expl2', 'gang2', 'balance', 'bench'];

const args = process.argv.slice(2);
const all = (await readdir(HERE))
  .filter((f) => f.endsWith('.mjs') && f !== 'run.mjs' && f !== 'harness.mjs')
  .map((f) => f.replace(/\.mjs$/, ''));
const ordered = [...ORDER.filter((n) => all.includes(n)), ...all.filter((n) => !ORDER.includes(n))];

if (args.includes('--list')) {
  for (const n of ordered) console.log(`  ${n.padEnd(10)} ${WHAT[n] || ''}`);
  process.exit(0);
}
const filter = args.filter((a) => !a.startsWith('--'));
const list = filter.length
  ? ordered.filter((n) => filter.some((f) => n.includes(f) || (WHAT[n] || '').includes(f)))
  : ordered;

if (!list.length) { console.error('該当するテストがありません'); process.exit(1); }

const run = (name) => new Promise((resolve) => {
  const t0 = Date.now();
  const p = spawn(process.execPath, [`${HERE}${name}.mjs`], { stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  p.stdout.on('data', (d) => { out += d; });
  p.stderr.on('data', (d) => { out += d; });
  p.on('close', (code) => {
    const okN = (out.match(/^ {2}OK {2}/gm) || []).length;
    const bad = (out.match(/^ {2}FAIL/gm) || []).length;
    resolve({ name, code, okN, bad, out, sec: ((Date.now() - t0) / 1000).toFixed(0) });
  });
});

console.log(`${list.length} 本を順に回します（並列にしない）\n`);
const results = [];
for (const name of list) {
  process.stdout.write(`▶ ${name.padEnd(10)} ${(WHAT[name] || '').padEnd(20)} `);
  const r = await run(name);
  results.push(r);
  const mark = r.code === 0 && !r.bad ? '✓' : '✗';
  console.log(`${mark} ${r.okN} OK / ${r.bad} FAIL  (${r.sec}s)`);
  if (r.bad || r.code !== 0) {
    for (const ln of r.out.split('\n').filter((l) => /^ {2}FAIL|Error|errors: (?!none)/.test(l))) {
      console.log(`    ${ln.trim()}`);
    }
  }
}

const okN = results.reduce((s, r) => s + r.okN, 0);
const bad = results.reduce((s, r) => s + r.bad, 0);
const broke = results.filter((r) => r.code !== 0 && !r.bad).map((r) => r.name);
console.log(`\n== 合計 ${okN} OK / ${bad} FAIL`);
if (broke.length) console.log(`途中で落ちた: ${broke.join(', ')}`);
if (bad || broke.length) process.exitCode = 1;
