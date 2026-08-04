// framedata.js — the combat tuning readout.
// L5.
//
// Everything a fighting game's training mode shows, because tuning by feel
// alone is guesswork: you cannot see a 4-frame active window or tell a
// 6-tick parry from a 9-tick one by watching. Numbers first, feel second.

import { S, STATE_NAME, ACT, BUFFER_SLOTS } from '../sim/components.js';
import { attackByIndex } from '../data/attacks.js';
import { STAMINA, DODGE, PARRY, STANCE, CANCEL } from '../data/tuning.js';

const ACT_NAME = { [ACT.NONE]: '', [ACT.ATTACK]: '攻', [ACT.DODGE]: '回',
  [ACT.BLOCK]: '守', [ACT.HEAVY]: '強', [ACT.INTERACT]: '調' };

export function createFrameData(root) {
  root.innerHTML = `
    <div class="fd">
      <div class="fd-row fd-head">
        <span class="fd-state">—</span>
        <span class="fd-tick"></span>
        <span class="fd-lat"></span>
      </div>
      <div class="fd-timeline"><div class="fd-tl-track"></div><div class="fd-tl-head"></div></div>
      <div class="fd-legend"></div>
      <div class="fd-meters"></div>
      <div class="fd-buffer"></div>
      <div class="fd-enemy"></div>
    </div>`;
  return {
    root,
    el: {
      state: root.querySelector('.fd-state'),
      tick: root.querySelector('.fd-tick'),
      lat: root.querySelector('.fd-lat'),
      track: root.querySelector('.fd-tl-track'),
      head: root.querySelector('.fd-tl-head'),
      legend: root.querySelector('.fd-legend'),
      meters: root.querySelector('.fd-meters'),
      buffer: root.querySelector('.fd-buffer'),
      enemy: root.querySelector('.fd-enemy'),
    },
    latencies: [],
    lastState: -1,
  };
}

/** Record one end-to-end input->visible-reaction sample, in milliseconds. */
export function recordLatency(fd, ms) {
  if (!(ms > 0) || ms > 500) return;
  fd.latencies.push(ms);
  if (fd.latencies.length > 40) fd.latencies.shift();
}

const bar = (label, frac, cls, text) =>
  `<div class="fd-m"><b>${label}</b>` +
  `<i class="fd-track"><s class="${cls}" style="width:${Math.max(0, Math.min(1, frac)) * 100}%"></s></i>` +
  `<u>${text}</u></div>`;

/**
 * Draw the current attack as a segmented timeline. Cancel thresholds are shown
 * for BOTH the hit and whiff cases, because the 30% hit-confirm reduction is
 * invisible otherwise and it is the single most important number in the file.
 */
function timeline(fd, s, p) {
  const st = s.stateId[p];
  const active = st === S.WINDUP || st === S.ACTIVE || st === S.RECOVERY
    || st === S.DODGE || st === S.PARRY;
  if (!active) {
    fd.el.track.innerHTML = '';
    fd.el.head.style.display = 'none';
    fd.el.legend.textContent = '';
    return;
  }
  fd.el.head.style.display = 'block';

  const a = attackByIndex(s.attackId[p]);
  const hit = s.hitConfirm[p] ? 1 : 0;
  const rec = (st === S.DODGE || st === S.PARRY) ? a.recovery
    : (hit ? a.recoveryOnHit : a.recovery);
  const total = Math.max(1, a.windup + a.active + rec);

  const segs = [
    { w: a.windup, cls: 'w', name: `windup ${a.windup}` },
    { w: a.active, cls: 'a', name: `active ${a.active}` },
    { w: rec, cls: 'r', name: `recovery ${rec}${hit ? ' (hit -30%)' : ''}` },
  ];
  let html = '';
  for (const g of segs) {
    if (g.w <= 0) continue;
    html += `<span class="fd-seg fd-${g.cls}" style="width:${(g.w / total) * 100}%"></span>`;
  }

  // Cancel thresholds, measured from the start of recovery.
  const base = a.windup + a.active;
  const marks = [];
  if (a._cancel && st !== S.DODGE) {
    marks.push(['combo', base + a._cancel[hit].combo, 'c']);
    marks.push(['dodge', base + a._cancel[hit].dodge, 'd']);
  }
  if (st === S.DODGE && a.iframes) {
    marks.push(['i-start', a.iframes[0], 'i']);
    marks.push(['i-end', a.iframes[1], 'i']);
    marks.push(['actionable', a.actionableFrom, 'd']);
  }
  if (st === S.PARRY) {
    const win = PARRY.windowTicks + (fd.parryBonus || 0);
    marks.push([`parry ${win}T`, win, 'p']);
  }
  for (const [name, at, cls] of marks) {
    html += `<span class="fd-mark fd-mk-${cls}" style="left:${(at / total) * 100}%" title="${name}"></span>`;
  }
  fd.el.track.innerHTML = html;

  // Playhead. phaseT is per-phase, so accumulate the phases already passed.
  let elapsed = s.phaseT[p];
  if (st === S.ACTIVE) elapsed += a.windup;
  else if (st === S.RECOVERY) elapsed += a.windup + a.active;
  fd.el.head.style.left = `${Math.min(100, (elapsed / total) * 100)}%`;

  fd.el.legend.textContent =
    `${a.id}  ${segs.map((g) => g.name).join(' / ')}  ` +
    `phase ${s.phaseT[p].toFixed(1)}  weight ${a.weight}`;
}

export function updateFrameData(fd, world, p, extra = {}) {
  const s = world.store;
  fd.parryBonus = world.inputProfile.parryWindowBonus;

  const st = s.stateId[p];
  fd.el.state.textContent = STATE_NAME[st];
  fd.el.state.className = `fd-state fd-s-${STATE_NAME[st].toLowerCase()}`;
  fd.el.tick.textContent =
    `t${world.tick}  hitstop ${world.hitstopT > 0 ? world.hitstopT.toFixed(0) : '—'}` +
    `  motion ${world.motionScale}` +
    (extra.paused ? '  ⏸ PAUSED' : extra.timeScale !== 1 ? `  ×${extra.timeScale}` : '');

  // Latency: median is the honest figure. A mean is dragged by GC spikes, and
  // the whole point is what the hand consistently feels.
  if (fd.latencies.length) {
    const sorted = fd.latencies.slice().sort((x, y) => x - y);
    const med = sorted[sorted.length >> 1];
    const worst = sorted[sorted.length - 1];
    const frames = med / 16.67;
    const slowRenderer = (extra.fps || 60) < 30;
    fd.el.lat.textContent =
      `入力遅延 中央${med.toFixed(0)}ms (${frames.toFixed(1)}F) 最悪${worst.toFixed(0)}ms`
      + (slowRenderer ? '  ※描画が律速。実機で測ること' : '');
    fd.el.lat.className = `fd-lat ${slowRenderer ? 'warn' : frames <= 4 ? 'ok' : 'bad'}`;
  }

  timeline(fd, s, p);

  let m0 = bar('HP', s.hp[p] / Math.max(1, s.hpMax[p]), 'hp',
    `${s.hp[p].toFixed(0)}/${s.hpMax[p].toFixed(0)}`);
  const staFrac = s.stamina[p] / Math.max(1, s.staminaMax[p]);
  const locked = s.staRegenDelay[p] > 0;
  let m = m0;
  m += bar('STA', staFrac, locked ? 'sta locked' : 'sta',
    `${s.stamina[p].toFixed(0)}/${s.staminaMax[p]}` +
    (locked ? `  遅延${s.staRegenDelay[p].toFixed(0)}T` : ''));
  m += bar('POISE', s.poise[p] / Math.max(1, s.poiseMax[p]), 'poise',
    `${s.poise[p].toFixed(0)}/${s.poiseMax[p]}`);
  m += bar('STANCE', s.stance[p] / Math.max(1, s.stanceMax[p]), 'stance',
    `${s.stance[p].toFixed(0)}/${s.stanceMax[p]}` +
    (s.stanceIdleT[p] < STANCE.idleBeforeRegen
      ? `  回復まで${(STANCE.idleBeforeRegen - s.stanceIdleT[p]).toFixed(0)}T` : '  回復中'));
  m += bar('IFRAME', s.iframeT[p] / 26, s.iframeT[p] > 0 ? 'iframe on' : 'iframe',
    s.iframeT[p] > 0 ? `無敵 ${s.iframeT[p].toFixed(0)}T` : '—');
  if (s.riposteT[p] > 0) {
    m += bar('RIPOSTE', s.riposteT[p] / PARRY.riposteWindowTicks, 'ripo',
      `致命可 ${s.riposteT[p].toFixed(0)}T`);
  }
  if (s.vulnerableT[p] > 0) {
    m += bar('BROKEN', s.vulnerableT[p] / STANCE.brokenTicks, 'broken',
      `被ダメ×${STANCE.brokenDamageMul}  ${s.vulnerableT[p].toFixed(0)}T`);
  }
  fd.el.meters.innerHTML = m;

  // Buffer contents. Seeing an input sit here explains "why didn't it come out".
  let b = '<b>BUF</b>';
  const base = p * BUFFER_SLOTS;
  let any = false;
  for (let k = 0; k < BUFFER_SLOTS; k++) {
    const act = s.bufAct[base + k];
    if (act === ACT.NONE) continue;
    any = true;
    b += `<span class="fd-buf">${ACT_NAME[act]}<i>${s.bufTtl[base + k]}T</i></span>`;
  }
  if (!any) b += '<span class="fd-buf empty">空</span>';
  fd.el.buffer.innerHTML = b;

  // Enemy telegraph. The delayed variant is the mechanic that turns
  // memorisation into reading, so it gets its own colour.
  const e = extra.enemyIndex;
  if (e !== undefined && e !== -1) {
    const est = s.stateId[e];
    const ea = attackByIndex(s.attackId[e]);
    const delayed = s.phaseT[e] < 0;
    let txt = `敵 ${STATE_NAME[est]}`;
    if (est === S.WINDUP) {
      const left = Math.max(0, ea.windup - s.phaseT[e]);
      txt += ` — ${ea.id} 予備動作 残り${left.toFixed(0)}T / 全${ea.windup}T`;
      if (delayed) txt += '  ★ディレイ派生';
    }
    txt += `  HP ${s.hp[e].toFixed(0)}  体勢 ${s.stance[e].toFixed(0)}/${s.stanceMax[e]}`;
    fd.el.enemy.textContent = txt;
    fd.el.enemy.className = `fd-enemy${delayed ? ' delayed' : est === S.WINDUP ? ' windup' : ''}`;
  }
}
