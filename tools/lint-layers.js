// lint-layers.js — enforce the dependency direction and the file-size cap.
//
// Three of the five god-object rules are machine-checked, and this is where.
// The previous build's Game class violated all five at once; a rule that is
// only written in a document does not survive contact with a deadline.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'src');

const LAYER = {
  core: 0,
  data: 1,
  sim: 2,
  present: 3,
  gfx: 4,
  ui: 5,
  platform: 6,
  app: 7,
};

const MAX_LINES = 400;

/** Layers a given layer is allowed to import from, beyond lower-or-equal ones. */
const EXTRA_ALLOWED = {
  present: ['platform'],
  gfx: ['platform'],
  ui: ['platform'],
  app: Object.keys(LAYER),
};

const errors = [];

function walk(dir, out = []) {
  for (const name of readdirSync(dir).sort()) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (name.endsWith('.js')) out.push(p);
  }
  return out;
}

const layerOf = (file) => relative(SRC, file).split('/')[0];

const IMPORT_RE = /(?:^|\n)\s*(?:import|export)[^'"\n]*from\s*['"]([^'"]+)['"]/g;

for (const file of walk(SRC)) {
  const rel = relative(ROOT, file);
  const src = readFileSync(file, 'utf8');
  const lines = src.split('\n').length;

  if (lines > MAX_LINES) {
    errors.push(`${rel}: ${lines} lines exceeds the ${MAX_LINES}-line cap — split it`);
  }

  const from = layerOf(file);
  const fromRank = LAYER[from];
  if (fromRank === undefined) continue;

  let m;
  IMPORT_RE.lastIndex = 0;
  while ((m = IMPORT_RE.exec(src)) !== null) {
    const spec = m[1];
    if (!spec.startsWith('.')) continue;                 // vendored engine, bare specifiers
    const target = resolve(dirname(file), spec);
    if (!target.startsWith(SRC)) continue;
    const to = layerOf(target);
    const toRank = LAYER[to];
    if (toRank === undefined) continue;

    const allowed = toRank <= fromRank || (EXTRA_ALLOWED[from] || []).includes(to);
    if (!allowed) {
      errors.push(`${rel}: L${fromRank} ${from}/ may not import L${toRank} ${to}/ (${spec})`);
    }

    // Systems must not know about each other. Communication goes through
    // component columns or the event queue, never a direct call.
    if (rel.includes('sim/systems/') && target.includes('sim/systems/')) {
      errors.push(`${rel}: systems may not import other systems (${spec})`);
    }
  }
}

// app/boot.js must contain wiring only. Control flow there is how logic
// creeps back into the entry point and the god object reassembles.
try {
  const boot = readFileSync(join(SRC, 'app/boot.js'), 'utf8')
    .split('\n')
    .filter((l) => !l.trim().startsWith('//'))
    .join('\n');
  const branches = (boot.match(/\b(if|for|while|switch)\s*\(/g) || []).length;
  if (branches > 6) {
    errors.push(`src/app/boot.js: ${branches} control-flow statements — boot should be wiring only`);
  }
} catch { /* boot.js not written yet */ }

if (errors.length) {
  console.error('Layer lint FAILED:\n');
  for (const e of errors) console.error('  ' + e);
  process.exit(1);
}
console.log('Layer lint passed.');
