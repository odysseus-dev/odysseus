// static/js/bgEffectClock.js
//
// One clock for the canvas background effects in theme.js (#4911).
//
// Each effect was written as "move this much per display refresh" and redrew a
// full-screen canvas on every refresh: 60 times a second on most screens and
// 120 on ProMotion displays, where it also ran twice as fast. They now share
// this clock, which draws at most BG_EFFECT_FPS times a second and passes `k`,
// the number of 60 Hz frames that have elapsed, so every step keeps the speed
// it was designed at on any display. With the system's "reduce motion" setting
// on, an effect settles into a still frame instead of animating.
//
// No DOM access beyond what is passed in, so it can be unit-tested under node.

export const BG_EFFECT_FPS = 30;
const FRAME_MS = 1000 / BG_EFFECT_FPS;
const DESIGN_FRAME_MS = 1000 / 60;
// After a long gap (a hidden tab, a busy main thread) catch up at most this
// many 60 Hz frames, so nothing jumps across the screen.
const MAX_K = 4;
// Frames run before holding still under "reduce motion", so rain, petals and
// embers are spread across the screen rather than just starting.
const SETTLE_FRAMES = 90;

// True with the chance that an event with probability `p` per 60 Hz frame
// happens at least once in `k` frames.
export function bgChance(p, k, random = Math.random) {
  return random() < 1 - Math.pow(1 - p, k);
}

// How many of something to spawn over `k` frames when the effect spawned one
// with probability `p` per 60 Hz frame. Keeps the average rate exact, which a
// single chance per draw cannot once `p * k` approaches 1 (rain spawns 0.6).
export function bgCount(p, k, random = Math.random) {
  const n = p * k;
  const whole = Math.floor(n);
  return whole + (random() < n - whole ? 1 : 0);
}

// The alpha that fades as much over `k` frames as `a` fades over one.
export function bgFade(a, k) {
  return 1 - Math.pow(1 - a, k);
}

// Drive `draw(k)` until `canvas` leaves the document. The effect removes its
// own canvas when its pattern is switched off, which stops the clock.
export function runBgEffect(canvas, draw, {
  raf = (cb) => requestAnimationFrame(cb),
  reduceMotion = typeof matchMedia === 'function'
    ? matchMedia('(prefers-reduced-motion: reduce)') : null,
} = {}) {
  const reduced = () => !!(reduceMotion && reduceMotion.matches);
  const restart = () => runBgEffect(canvas, draw, { raf, reduceMotion });

  if (reduced()) {
    for (let i = 0; i < SETTLE_FRAMES && canvas.isConnected; i++) draw(1);
    if (reduceMotion.addEventListener) {
      reduceMotion.addEventListener('change', () => {
        if (canvas.isConnected) restart();
      }, { once: true });
    }
    return;
  }

  let last = 0;
  function frame(now) {
    if (!canvas.isConnected) return;
    if (reduced()) { restart(); return; }
    // The 2 ms of slack keeps a 60 Hz display on every second refresh even
    // when its timestamps jitter slightly below 33.3 ms apart.
    if (last && now - last < FRAME_MS - 2) { raf(frame); return; }
    const k = last ? Math.min((now - last) / DESIGN_FRAME_MS, MAX_K) : 1;
    last = now;
    draw(k);
    if (canvas.isConnected) raf(frame);
  }
  raf(frame);
}
