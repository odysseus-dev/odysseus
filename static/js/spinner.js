// static/js/spinner.js

/**
 * ASCII Spinner Module for AI thinking/processing status
 */

import { t } from './i18n.js';

class Spinner {
  constructor(message, style = "right", animation = "spinner") {
    // Different animation frames
    this.animations = {
      spinner: ['|', '/', '-', '\\'],
      wave: ['▁▂▃', '▂▃▄', '▃▄▅', '▄▅▆', '▅▆▅', '▆▅▄', '▅▄▃', '▄▃▂', '▃▂▁']
    };

    this.animation = animation;
    this.frames = this.animations[animation] || this.animations.spinner;
    this.message = message === undefined ? t('AI is processing') : message;
    this.style = style; // "left", "right", or "clean"
    this.isRunning = false;
    this.currentFrame = 0;
    this.intervalId = null;
    this.rafId = null;
    this.element = null;
  }

  /**
   * Create and return the spinner HTML element
   */
  createElement() {
    if (this.animation === 'sinewave') {
      return this._createSineWaveElement();
    }
    if (this.animation === 'whirlpool') {
      return this._createWhirlpoolElement();
    }
    const span = document.createElement('span');
    span.className = 'ai-spinner';
    span.style.cssText = 'font-family: monospace; white-space: pre;';
    this.element = span;
    this.updateDisplay();
    return span;
  }

  _createSineWaveElement() {
    const wrapper = document.createElement('span');
    wrapper.className = 'ai-spinner ai-spinner-sinewave';
    wrapper.style.cssText = 'font-family: monospace; white-space: pre; display: inline-flex; align-items: center; gap: 6px;';

    const canvas = document.createElement('canvas');
    canvas.width = 50;
    canvas.height = 18;
    canvas.style.cssText = 'display: inline-block; vertical-align: middle;';

    const msgSpan = document.createElement('span');
    msgSpan.textContent = this.message;
    this._msgSpan = msgSpan;

    if (this.style === 'left') {
      wrapper.appendChild(canvas);
      wrapper.appendChild(msgSpan);
    } else if (this.style === 'right') {
      wrapper.appendChild(msgSpan);
      wrapper.appendChild(canvas);
    } else {
      wrapper.appendChild(msgSpan);
    }

    this._canvas = canvas;
    this._ctx = canvas.getContext('2d');
    this._waveT = 0;
    this._wavePrev = performance.now();
    this.element = wrapper;
    return wrapper;
  }

  _drawSineWave() {
    const ctx = this._ctx;
    const W = this._canvas.width;
    const H = this._canvas.height;
    const midY = H / 2;
    const AMP = 7;
    const CYCLES = 2.5;
    const PAD = 3;
    const trackW = W - 2 * PAD;
    const BASE_SPEED = 0.44;
    const MIN_SPEED = 0.4;
    const MAX_SPEED = 2.5;

    const now = performance.now();
    const dt = (now - this._wavePrev) / 1000;
    this._wavePrev = now;

    const dotPhase = 0.5 * CYCLES * 2 * Math.PI + this._waveT;
    const norm = (1 + Math.sin(dotPhase)) / 2;
    const speedMul = MIN_SPEED + (MAX_SPEED - MIN_SPEED) * Math.pow(norm, 1.3);
    this._waveT += dt * BASE_SPEED * speedMul * CYCLES * 2 * Math.PI;

    ctx.clearRect(0, 0, W, H);

    // wave line
    ctx.beginPath();
    for (let i = 0; i <= 80; i++) {
      const frac = i / 80;
      const x = PAD + frac * trackW;
      const phase = frac * CYCLES * 2 * Math.PI + this._waveT;
      const y = midY + Math.sin(phase) * AMP;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = 'rgba(156, 222, 242, 0.5)';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // dot
    const cx = W / 2;
    const cPhase = 0.5 * CYCLES * 2 * Math.PI + this._waveT;
    const cy = midY + Math.sin(cPhase) * AMP;
    ctx.beginPath();
    ctx.arc(cx, cy, 1.5, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(156, 222, 242, 0.9)';
    ctx.fill();

    if (this.isRunning) {
      this.rafId = requestAnimationFrame(() => this._drawSineWave());
    }
  }

  _createWhirlpoolElement() {
    const wrapper = document.createElement('span');
    wrapper.className = 'ai-spinner ai-spinner-whirlpool';
    wrapper.style.cssText = 'font-family: monospace; white-space: pre; display: inline-flex; align-items: center; gap: 6px;';

    const size = this._wpSize || 18;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    canvas.style.cssText = 'display: inline-block; vertical-align: middle;';

    const msgSpan = document.createElement('span');
    msgSpan.textContent = this.message;
    this._msgSpan = msgSpan;

    if (this.style === 'left') {
      wrapper.appendChild(canvas);
      wrapper.appendChild(msgSpan);
    } else if (this.style === 'right') {
      wrapper.appendChild(msgSpan);
      wrapper.appendChild(canvas);
    } else {
      wrapper.appendChild(canvas);
    }

    this._wpCanvas = canvas;
    this._wpCtx = canvas.getContext('2d');
    this._wpFrame = 60;
    this.element = wrapper;
    return wrapper;
  }

  _drawWhirlpool() {
    const ctx = this._wpCtx;
    const W = this._wpCanvas.width;
    const H = this._wpCanvas.height;
    const cx = W / 2, cy = H / 2;
    const maxR = Math.min(W, H) / 2 - 1;
    const lw = W > 30 ? 3 : W > 20 ? 2 : 1.5;
    const TOTAL_TURNS = 4;
    const TAIL_LEN = 0.45;
    const SPIN_SPEED = 0.08;
    const LAYERS = 12;
    const STEPS = 50;
    const t = this._wpFrame;

    // Colors from CSS vars — read ONCE and cache. Calling getComputedStyle every
    // frame forces a full style recalc per frame, which janks/freezes the canvas
    // animation badly when it's painting over a heavy photo. (Theme changes are
    // rare; the spinner is short-lived, so a stale cache is fine.)
    if (!this._wpColors) {
      const s = getComputedStyle(document.documentElement);
      this._wpColors = {
        fg: s.getPropertyValue('--red').trim() || s.getPropertyValue('--fg').trim() || '#9cdef2',
        track: s.getPropertyValue('--border').trim() || '#355a66',
      };
    }
    const fg = this._wpColors.fg;
    const track = this._wpColors.track;

    function spiralPoint(frac, rot) {
      const r = maxR * (1 - frac);
      const angle = frac * TOTAL_TURNS * Math.PI * 2 + rot;
      return { x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r };
    }

    ctx.clearRect(0, 0, W, H);

    // track ring
    ctx.beginPath();
    ctx.arc(cx, cy, maxR - lw / 2, 0, Math.PI * 2);
    ctx.strokeStyle = track;
    ctx.lineWidth = lw;
    ctx.globalAlpha = 0.35;
    ctx.stroke();
    ctx.globalAlpha = 1;

    const headPos = (t * 0.008) % 1;

    // overlapping sub-paths for smooth fade
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (let layer = LAYERS - 1; layer >= 0; layer--) {
      const endFrac = (layer + 1) / LAYERS;
      const stepsForLayer = Math.ceil(STEPS * endFrac);
      const alpha = Math.pow(1 - endFrac, 2) * 0.7;

      ctx.beginPath();
      let started = false;
      let prevPos = -1;
      for (let i = 0; i <= stepsForLayer; i++) {
        const frac = i / STEPS;
        let pos = headPos - frac * TAIL_LEN;
        if (pos < 0) pos += 1;
        if (started && prevPos < 0.3 && pos > 0.7) {
          ctx.stroke();
          ctx.beginPath();
          started = false;
        }
        const pt = spiralPoint(pos, t * SPIN_SPEED);
        if (!started) { ctx.moveTo(pt.x, pt.y); started = true; }
        else ctx.lineTo(pt.x, pt.y);
        prevPos = pos;
      }
      ctx.strokeStyle = fg;
      ctx.lineWidth = lw * 0.8;
      ctx.globalAlpha = alpha;
      ctx.stroke();
    }

    // bright dot at head
    const head = spiralPoint(headPos, t * SPIN_SPEED);
    ctx.beginPath();
    ctx.arc(head.x, head.y, Math.max(1, lw * 0.45), 0, Math.PI * 2);
    ctx.fillStyle = fg;
    ctx.globalAlpha = 0.9;
    ctx.fill();
    ctx.globalAlpha = 1;

    this._wpFrame++;
    if (!this.isRunning) return;
    // Leak-safe self-terminate: stop once our element WAS in the DOM and then
    // got removed (e.g. a loading row replaced by results). But keep spinning
    // before it's first appended — start() runs synchronously, before the
    // caller inserts the element, so it isn't connected on frame 1.
    const connected = !!(this.element && this.element.isConnected);
    if (connected) this._wpWasConnected = true;
    if (connected || !this._wpWasConnected) {
      this.rafId = requestAnimationFrame(() => this._drawWhirlpool());
    } else {
      this.isRunning = false;
    }
  }

  /**
   * Update the spinner display
   */
  updateDisplay() {
    if (!this.element) return;

    const frame = this.frames[this.currentFrame % this.frames.length];

    let display = '';
    if (this.style === "left") {
      display = `${frame} ${this.message}`;
    } else if (this.style === "right") {
      display = `${this.message} ${frame}`;
    } else { // clean
      display = this.message;
    }

    this.element.innerHTML = display;
  }

  /**
   * Start the spinner animation
   */
  start(speed = 150) {
    if (this.isRunning) return;
    this.isRunning = true;

    if (this.animation === 'sinewave') {
      this._wavePrev = performance.now();
      this._drawSineWave();
      return;
    }

    if (this.animation === 'whirlpool') {
      this._wpFrame = 60;
      this._drawWhirlpool();
      return;
    }

    this.currentFrame = 0;
    this.intervalId = setInterval(() => {
      this.currentFrame++;
      this.updateDisplay();
    }, speed);
  }

  /**
   * Stop the spinner
   */
  stop() {
    this.isRunning = false;
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }

  /**
   * Update the message while spinner is running
   */
  updateMessage(newMessage) {
    this.message = newMessage;
    if ((this.animation === 'sinewave' || this.animation === 'whirlpool') && this._msgSpan) {
      this._msgSpan.textContent = newMessage;
    } else {
      this.updateDisplay();
    }
  }

  /**
   * Update the spinner label text
   */
  updateLabel(newMessage) {
    this.message = newMessage;
    if (this._msgSpan) {
      this._msgSpan.textContent = newMessage;
    } else {
      this.updateDisplay();
    }
  }

  /**
   * Destroy the spinner and clean up
   */
  destroy() {
    this.stop();
    if (this.element && this.element.parentNode) {
      this.element.parentNode.removeChild(this.element);
    }
    this.element = null;
  }
}

/**
 * Create a new spinner instance
 */
export function create(message, style = "right", animation = "wave") {
  return new Spinner(message, style, animation);
}

/**
 * Create a standalone whirlpool circle spinner (replaces CSS .spinner)
 * Returns { element, start(), stop(), destroy() }
 */
export function createWhirlpool(size = 24) {
  const sp = new Spinner('', 'clean', 'whirlpool');
  sp._wpSize = size;
  const el = sp.createElement();
  // wrap in a div matching .spinner layout
  const wrap = document.createElement('div');
  wrap.className = 'spinner-whirlpool';
  wrap.style.cssText = `width:${size}px;height:${size}px;margin:8px auto;`;
  wrap.appendChild(el);
  sp.start();
  return { element: wrap, stop: () => sp.stop(), destroy: () => sp.destroy() };
}

/**
 * A consistent inline loading row for list/library empty-states: a label plus
 * the whirlpool spinner. Returns a detached element; the spinner self-stops
 * once the element leaves the DOM (see _drawWhirlpool), so callers can just
 * replace it with results — no manual cleanup needed.
 */
export function createLoadingRow(text, size = 16) {
  const resolvedText = text === undefined ? t('Loading…') : text;
  const sp = new Spinner('', 'clean', 'whirlpool');
  sp._wpSize = size;
  const canvas = sp.createElement();
  const row = document.createElement('div');
  row.className = 'lib-loading-row';
  const label = document.createElement('span');
  label.textContent = resolvedText;
  row.appendChild(label);
  row.appendChild(canvas);
  sp.start();
  return row;
}

export { Spinner };

const spinnerModule = { create, createWhirlpool, createLoadingRow, Spinner };
export default spinnerModule;
