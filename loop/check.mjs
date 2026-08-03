// 1周を閉じる前の確認。
//
//   node loop/check.mjs
//
// 引き継ぎの器は、更新されなければ無いのと同じです。そして更新を忘れたことは、
// 忘れた本人には分かりません —— 分かるのは、記憶を持たずに次に来た側だけで、
// そのときにはもう手遅れです。だから機械に見させます。
//
// 通らなくても作業が消えるわけではありません。何が抜けているかを言うだけです。
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';

const ROOT = new URL('..', import.meta.url).pathname;
const problems = [];
const notes = [];

const git = (...args) => execFileSync('git', args, { cwd: ROOT, encoding: 'utf8' }).trim();

// --- state.json が壊れていないか -------------------------------------------
let state;
try {
  state = JSON.parse(await readFile(`${ROOT}/loop/state.json`, 'utf8'));
} catch (e) {
  console.error(`loop/state.json が読めません: ${e.message}`);
  console.error('これが壊れていると次の周は何も引き継げません。直してください。');
  process.exit(1);
}

// --- ロックが残っていないか -------------------------------------------------
const started = state.lock?.startedAt ? Date.parse(state.lock.startedAt) : null;
if (started) {
  const mins = (Date.now() - started) / 60000;
  if (mins < 45) {
    problems.push(`lock がまだ握られています（${mins.toFixed(0)}分前に取得）。` +
      '作業を終えたなら lock.startedAt を null に戻してください。' +
      '握ったまま終わると、45分間ループが止まります。');
  } else {
    notes.push(`lock が ${mins.toFixed(0)}分前のまま失効しています。null に戻しておくと綺麗です。`);
  }
}

// --- この周の記録が残っているか ---------------------------------------------
// state.json が最後に触られたコミットより後にコードのコミットが積まれていたら、
// その周の判断がどこにも書かれていない。
let stateAt = 0, codeAt = 0;
try {
  stateAt = Number(git('log', '-1', '--format=%ct', '--', 'loop/state.json')) * 1000;
  codeAt = Number(git('log', '-1', '--format=%ct', '--', 'src')) * 1000;
} catch { /* 履歴が浅いクローンなど。判定できないので黙る */ }

if (stateAt && codeAt && codeAt > stateAt) {
  const mins = (codeAt - stateAt) / 60000;
  problems.push(`src の更新が state.json より ${mins.toFixed(0)}分ぶん新しいです。` +
    'この周に何をしてなぜそうしたかが、どこにも残っていません。' +
    'handoff か tried に書いてからコミットしてください。');
}

// --- 基準が決まっているか ---------------------------------------------------
if (!state.criteria || state.criteria.length === 0) {
  notes.push('criteria が空です。「劣らないクオリティ」が何を指すかは指示に書かれて ' +
    'いないので、決めるのは回す側です。空のままだと周回ごとに解釈がぶれます。');
}

// --- 次の一手が残っているか -------------------------------------------------
if (!state.backlog || state.backlog.length === 0) {
  notes.push('backlog が空です。次に来る側は、何をするかを一から決め直すことになります。' +
    '1つでも書き残しておくと、その時間が浮きます。');
}

if (!state.handoff || !String(state.handoff).trim()) {
  problems.push('handoff が空です。次に来る側が読む最初の一行がありません。');
}

// --- 触ってよい範囲から出ていないか -----------------------------------------
try {
  const remote = git('remote', 'get-url', 'origin');
  if (!/[/:]bachikoljunior-blip\/[oO](\.git)?$/.test(remote)) {
    problems.push(`origin が ${remote} です。変更してよいのは bachikoljunior-blip/O だけです。`);
  }
} catch { /* remote が無い環境では黙る */ }

// --- 出力 -------------------------------------------------------------------
for (const n of notes) console.log(`  … ${n}`);
if (!problems.length) {
  console.log(notes.length ? '\n締められます。' : '締められます。');
  process.exit(0);
}
console.log('');
for (const p of problems) console.log(`  ✗ ${p}`);
console.log('\n直してから締めてください。');
process.exit(1);
