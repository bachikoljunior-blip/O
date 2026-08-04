// loading.js — run a generator against a frame budget.
// L7.
//
// The macro field takes about a second to erode. Doing that synchronously
// freezes the tab with no feedback, which on a phone is indistinguishable from
// a crashed page. Yielding to the browser on a 22ms budget keeps the loading
// screen painting and lets the progress bar mean something.

const FRAME_BUDGET_MS = 22;

/**
 * Drive a progress-yielding generator to completion.
 * @param gen a generator yielding `{phase, t}` with t in [0,1]
 * @param onProgress called at most once per frame
 * @returns the generator's return value
 */
export function runSliced(gen, onProgress) {
  return new Promise((resolve, reject) => {
    function step() {
      const start = performance.now();
      let last = null;
      try {
        // Burn the whole budget, then hand the frame back. Checking the clock
        // every iteration would cost more than it saves for cheap steps.
        for (;;) {
          const r = gen.next();
          if (r.done) { onProgress?.({ phase: '', t: 1 }); resolve(r.value); return; }
          last = r.value;
          if (performance.now() - start >= FRAME_BUDGET_MS) break;
        }
      } catch (err) { reject(err); return; }
      if (last) onProgress?.(last);
      requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  });
}

/** Wire the boot overlay's text and bar to a progress callback. */
export function bootProgress(rootId = 'boot') {
  const root = document.getElementById(rootId);
  if (!root) return () => {};
  const sub = root.querySelector('.boot-sub');
  let bar = root.querySelector('.boot-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'boot-bar';
    bar.innerHTML = '<i></i>';
    sub.after(bar);
  }
  const fill = bar.querySelector('i');
  return ({ phase, t }) => {
    if (sub && phase) sub.textContent = `${phase}を生成しています…`;
    fill.style.width = `${Math.round(Math.max(0, Math.min(1, t)) * 100)}%`;
  };
}
