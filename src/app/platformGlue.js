// platformGlue.js — the browser hostility layer.
// L7.
//
// Every line here exists because a mobile browser does something unhelpful by
// default: zooms on double-tap, rubber-bands the page, sleeps the screen
// mid-fight, or refuses to start audio.

export function installPlatformGlue({ canvas, onResize, onAudioUnlock, onHidden }) {
  // Audio cannot start without a gesture on any mobile browser.
  const unlock = () => { onAudioUnlock?.(); };
  for (const ev of ['pointerdown', 'keydown', 'touchstart']) {
    window.addEventListener(ev, unlock, { once: true, passive: true });
  }

  // Keep the screen awake. Wake Lock works on iOS 16.4+, and inside installed
  // PWAs only from 18.4 — so failure here is expected, not exceptional.
  let wakeLock = null;
  const requestWake = async () => {
    try {
      if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen');
    } catch { /* denied or unsupported; the game still runs */ }
  };
  requestWake();

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') requestWake();
    else onHidden?.();
  });
  window.addEventListener('pagehide', () => onHidden?.());

  // Suppress iOS zoom and rubber-band scrolling.
  document.addEventListener('gesturestart', (e) => e.preventDefault(), { passive: false });
  document.addEventListener('dblclick', (e) => e.preventDefault(), { passive: false });
  document.addEventListener('touchmove', (e) => {
    if (e.touches.length > 1) e.preventDefault();
  }, { passive: false });
  document.addEventListener('contextmenu', (e) => e.preventDefault());

  const resize = () => onResize?.();
  window.addEventListener('resize', resize);
  window.visualViewport?.addEventListener('resize', resize);
  // Orientation change needs a settle delay or the reported size is stale.
  window.addEventListener('orientationchange', () => setTimeout(resize, 250));

  return { get wakeLock() { return wakeLock; } };
}

/**
 * Haptics. iOS Safari has NEVER supported the Vibration API, on any version,
 * so this is a no-op for roughly half the audience. Nothing in the combat
 * design may depend on it — the flash, the hitstop and the audio transient
 * carry the feedback on their own.
 */
export function createHaptics() {
  const supported = typeof navigator !== 'undefined' && !!navigator.vibrate;
  return (ms) => { if (supported && ms > 0) navigator.vibrate(ms); };
}

/**
 * Orientation lock is likewise unsupported in iOS Safari. The manifest asks
 * politely for installed PWAs; otherwise both orientations must simply work.
 */
export function tryLockOrientation() {
  try { screen.orientation?.lock?.('landscape').catch(() => {}); } catch { /* expected */ }
}
