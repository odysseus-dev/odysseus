// In-house color picker with live-feedback HSV square, hue bar,
// eyedropper, recent colors, and harmony suggestions.
// Non-invasive: wraps existing <input type="color"> elements —
// their .value stays the source of truth, and we dispatch 'input'
// events so existing listeners keep working.

import { topPortalZ } from './toolWindowZOrder.js';

const LS_RECENT = 'odysseus-recent-colors';
const MAX_RECENT = 12;

let _popover = null;
let _input = null;
let _h = 0, _s = 100, _v = 100;   // HSV
let _drag = null;                  // 'sl' | 'hue' | null
let _onOutside = null;

// ── Color math ────────────────────────────────────────────────────────
function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

function hexToRgb(hex) {
  hex = String(hex || '').replace('#', '');
  if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
  if (!/^[0-9a-f]{6}$/i.test(hex)) return { r: 0, g: 0, b: 0 };
  const n = parseInt(hex, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map(v =>
    Math.round(clamp(v, 0, 255)).toString(16).padStart(2, '0')
  ).join('');
}

function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const d = max - min;
  let h;
  const s = max === 0 ? 0 : d / max;
  const v = max;
  if (d === 0) h = 0;
  else if (max === r) h = ((g - b) / d + (g < b ? 6 : 0));
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return { h: h * 60, s: s * 100, v: v * 100 };
}

function hsvToRgb(h, s, v) {
  h = ((h % 360) + 360) % 360;
  h /= 60; s /= 100; v /= 100;
  const i = Math.floor(h);
  const f = h - i;
  const p = v * (1 - s);
  const q = v * (1 - f * s);
  const t = v * (1 - (1 - f) * s);
  let r, g, b;
  switch (i % 6) {
    case 0: r = v; g = t; b = p; break;
    case 1: r = q; g = v; b = p; break;
    case 2: r = p; g = v; b = t; break;
    case 3: r = p; g = q; b = v; break;
    case 4: r = t; g = p; b = v; break;
    case 5: r = v; g = p; b = q; break;
  }
  return { r: Math.round(r * 255), g: Math.round(g * 255), b: Math.round(b * 255) };
}

function hsvToHex(h, s, v) { const { r, g, b } = hsvToRgb(h, s, v); return rgbToHex(r, g, b); }

function hexToHsv(hex) { const { r, g, b } = hexToRgb(hex); return rgbToHsv(r, g, b); }

// ── Storage ───────────────────────────────────────────────────────────
function getRecents() {
  try { return JSON.parse(localStorage.getItem(LS_RECENT) || '[]'); }
  catch { return []; }
}

function addRecent(hex) {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return;
  let recents = getRecents().filter(c => c.toLowerCase() !== hex.toLowerCase());
  recents.unshift(hex.toLowerCase());
  recents = recents.slice(0, MAX_RECENT);
  try { localStorage.setItem(LS_RECENT, JSON.stringify(recents)); } catch {}
}

// ── Suggestions based on current color (5 harmony swatches) ──────────
function computeSuggestions() {
  // Complement, analogous ±30°, split-complement (+150), tone shift
  return [
    { hex: hsvToHex(_h + 180, _s, _v),                                   label: 'Complement' },
    { hex: hsvToHex(_h + 30, _s, _v),                                    label: 'Analogous +30°' },
    { hex: hsvToHex(_h - 30, _s, _v),                                    label: 'Analogous -30°' },
    { hex: hsvToHex(_h + 150, _s, _v),                                   label: 'Split-complement' },
    { hex: hsvToHex(_h, _s, clamp(_v > 50 ? _v - 30 : _v + 30, 10, 95)), label: 'Tone shift' },
  ];
}

// ── Popover build ─────────────────────────────────────────────────────
function buildPopover() {
  const p = document.createElement('div');
  p.className = 'cp-popover';
  p.innerHTML = `
    <div class="cp-sl" data-drag="sl">
      <div class="cp-sl-white"></div>
      <div class="cp-sl-black"></div>
      <div class="cp-sl-handle"></div>
    </div>
    <div class="cp-hue" data-drag="hue">
      <div class="cp-hue-handle"></div>
    </div>
    <div class="cp-row">
      <div class="cp-preview"></div>
      <input type="text" class="cp-hex" maxlength="7" spellcheck="false" autocomplete="off">
      <button class="cp-copy" title="Copy color" type="button" aria-label="Copy color">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
      </button>
      <button class="cp-eyedropper" title="Eyedropper" type="button">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2.5S5 10.1 5 14.5a7 7 0 0014 0C19 10.1 12 2.5 12 2.5Z"/>
          <path d="M9 16.5c.6 1.2 1.6 1.9 3 2"/>
        </svg>
      </button>
    </div>
    <div class="cp-section-label">Suggestions</div>
    <div class="cp-swatches cp-suggestions"></div>
    <div class="cp-section-label">Recent</div>
    <div class="cp-swatches cp-recent"></div>
  `;
  document.body.appendChild(p);
  wireHandlers(p);
  return p;
}

// ── UI sync ───────────────────────────────────────────────────────────
function syncUI() {
  if (!_popover) return;
  const sl = _popover.querySelector('.cp-sl');
  const slH = _popover.querySelector('.cp-sl-handle');
  const hue = _popover.querySelector('.cp-hue');
  const hueH = _popover.querySelector('.cp-hue-handle');
  const hex = _popover.querySelector('.cp-hex');
  const preview = _popover.querySelector('.cp-preview');

  const pureHue = hsvToHex(_h, 100, 100);
  sl.style.background = pureHue;   // base hue — white/black layers stacked on top via CSS

  slH.style.left = (_s) + '%';
  slH.style.top = (100 - _v) + '%';

  hueH.style.left = (_h / 360 * 100) + '%';

  const current = hsvToHex(_h, _s, _v);
  preview.style.background = current;
  if (document.activeElement !== hex) hex.value = current;

  // Suggestions
  const sContainer = _popover.querySelector('.cp-suggestions');
  const sugs = computeSuggestions();
  sContainer.innerHTML = sugs.map(s =>
    `<button class="cp-swatch" title="${s.label}: ${s.hex}" data-hex="${s.hex}" style="background:${s.hex}"></button>`
  ).join('');

  // Recents
  const rContainer = _popover.querySelector('.cp-recent');
  const recs = getRecents();
  rContainer.innerHTML = recs.length
    ? recs.map(h => `<button class="cp-swatch" title="${h}" data-hex="${h}" style="background:${h}"></button>`).join('')
    : '<div class="cp-recent-empty">(none yet)</div>';
}

function applyToInput(pushChange) {
  if (!_input) return;
  const hex = hsvToHex(_h, _s, _v);
  _input.value = hex;  // setter also updates style.background
  if (pushChange) _input.dispatchEvent(new Event('input', { bubbles: true }));
  syncUI();
}

function setFromHex(hex) {
  const v = hexToHsv(hex);
  _h = v.h; _s = v.s; _v = v.v;
}

function cssColorToHex(value) {
  const match = String(value || '').match(/rgba?\(\s*([\d.]+)[, ]+\s*([\d.]+)[, ]+\s*([\d.]+)(?:[, /]+\s*([\d.]+%?))?\s*\)/i);
  if (!match) return null;
  const alpha = match[4] == null ? 1 : (match[4].endsWith('%') ? parseFloat(match[4]) / 100 : parseFloat(match[4]));
  if (!Number.isFinite(alpha) || alpha <= 0) return null;
  return rgbToHex(parseFloat(match[1]), parseFloat(match[2]), parseFloat(match[3]));
}

function colorAtPoint(x, y) {
  const candidates = document.elementsFromPoint(x, y)
    .filter(el => el !== _popover && !_popover?.contains(el));
  for (const el of candidates) {
    const style = getComputedStyle(el);
    for (const value of [style.backgroundColor, style.borderTopColor, style.color]) {
      const hex = cssColorToHex(value);
      if (hex) return hex;
    }
  }
  return null;
}

function openFallbackEyedropper() {
  return new Promise((resolve) => {
    const hint = document.createElement('div');
    hint.textContent = 'Click a visible color to sample · Esc to cancel';
    hint.style.cssText = 'position:fixed;left:50%;top:12px;transform:translateX(-50%);z-index:2147483647;padding:5px 9px;border:1px solid var(--border,#555);border-radius:5px;background:var(--bg,#222);color:var(--fg,#fff);font:12px sans-serif;pointer-events:none;box-shadow:0 2px 10px rgba(0,0,0,.35)';
    document.body.appendChild(hint);
    const finish = (value) => {
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKey, true);
      hint.remove();
      resolve(value);
    };
    const onClick = (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      finish(colorAtPoint(event.clientX, event.clientY));
    };
    const onKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        finish(null);
      }
    };
    document.addEventListener('click', onClick, true);
    document.addEventListener('keydown', onKey, true);
  });
}

// ── Handlers ──────────────────────────────────────────────────────────
// Window-level pointer listeners — installed ONCE, not per-popover, so they
// don't leak when the popover is rebuilt on every open.
let _windowPointerInstalled = false;
function _installWindowPointer() {
  if (_windowPointerInstalled) return;
  _windowPointerInstalled = true;
  window.addEventListener('pointermove', (e) => { if (_drag) handleDrag(e); });
  window.addEventListener('pointerup', () => {
    if (_drag) {
      _drag = null;
      commitCurrent();
    }
  });
}

function wireHandlers(p) {
  const sl = p.querySelector('.cp-sl');
  const hue = p.querySelector('.cp-hue');
  const hex = p.querySelector('.cp-hex');
  const copy = p.querySelector('.cp-copy');
  const eye = p.querySelector('.cp-eyedropper');

  const onDown = (type) => (e) => {
    _drag = type;
    handleDrag(e);
    e.preventDefault();
  };
  sl.addEventListener('pointerdown', onDown('sl'));
  hue.addEventListener('pointerdown', onDown('hue'));
  _installWindowPointer();

  hex.addEventListener('input', () => {
    let v = hex.value.trim();
    if (!v.startsWith('#')) v = '#' + v;
    if (/^#[0-9a-f]{6}$/i.test(v)) {
      setFromHex(v);
      applyToInput(true);
    }
  });
  hex.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { commitCurrent(); close(); }
    if (e.key === 'Escape') { close(); }
  });

  copy.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const value = hsvToHex(_h, _s, _v);
    try {
      await navigator.clipboard.writeText(value);
    } catch (_) {
      hex.focus();
      hex.select();
      document.execCommand('copy');
      hex.setSelectionRange(hex.value.length, hex.value.length);
    }
    copy.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>';
    copy.title = 'Copied';
    copy.setAttribute('aria-label', 'Copied');
    setTimeout(() => {
      if (!copy.isConnected) return;
      copy.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
      copy.title = 'Copy color';
      copy.setAttribute('aria-label', 'Copy color');
    }, 900);
  });

  p.addEventListener('click', (e) => {
    const sw = e.target.closest('.cp-swatch');
    if (sw && sw.dataset.hex) {
      setFromHex(sw.dataset.hex);
      applyToInput(true);
      commitCurrent();
    }
  });

  eye.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      // Suppress the outside-click close while the OS eyedropper is open.
      // Without this, the user's pixel-pick fires a window click that
      // hits our document-capture listener and closes the popover.
      const wasOnOutside = _onOutside;
      _detachOutsideHandlers();
      try {
        const r = window.EyeDropper
          ? await new window.EyeDropper().open()
          : { sRGBHex: await openFallbackEyedropper() };
        if (r && r.sRGBHex) {
          setFromHex(r.sRGBHex);
          applyToInput(true);
          commitCurrent();
        }
      } catch (_) { /* user cancelled */ }
      // Re-arm outside-click handler after a frame so the eyedropper's
      // own pick-click doesn't immediately re-close us.
      if (wasOnOutside && _popover) {
        requestAnimationFrame(() => {
          if (!_popover) return;
          _onOutside = wasOnOutside;
          _onEsc = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
          document.addEventListener('click', _onOutside, true);
          document.addEventListener('keydown', _onEsc, true);
        });
      }
    });
  if (!window.EyeDropper) {
    eye.title = 'Click a visible UI color to sample it';
  }
}

function handleDrag(e) {
  if (_drag === 'sl') {
    const sl = _popover.querySelector('.cp-sl');
    const r = sl.getBoundingClientRect();
    const x = clamp((e.clientX - r.left) / r.width, 0, 1);
    const y = clamp((e.clientY - r.top) / r.height, 0, 1);
    _s = x * 100;
    _v = (1 - y) * 100;
    applyToInput(true);
  } else if (_drag === 'hue') {
    const hue = _popover.querySelector('.cp-hue');
    const r = hue.getBoundingClientRect();
    const x = clamp((e.clientX - r.left) / r.width, 0, 1);
    _h = x * 360;
    applyToInput(true);
  }
}

function commitCurrent() {
  if (!_input) return;
  addRecent(_input.value);
  syncUI();
}

// ── Open / close ──────────────────────────────────────────────────────
function position(p, anchor) {
  p.style.zIndex = String(topPortalZ());
  if (window.matchMedia && window.matchMedia('(max-width: 768px)').matches) {
    p.style.left = '50%';
    p.style.top = '50%';
    p.style.transform = 'translate(-50%, -50%)';
    return;
  }
  p.style.transform = '';
  const rect = anchor.getBoundingClientRect();
  const pRect = p.getBoundingClientRect();
  let left = rect.left;
  let top = rect.bottom + 6;
  if (left + pRect.width > window.innerWidth - 8) left = window.innerWidth - pRect.width - 8;
  if (top + pRect.height > window.innerHeight - 8) top = rect.top - pRect.height - 6;
  if (left < 8) left = 8;
  if (top < 8) top = 8;
  p.style.left = left + 'px';
  p.style.top = top + 'px';
}

let _onEsc = null;

function _detachOutsideHandlers() {
  if (_onOutside) {
    document.removeEventListener('click', _onOutside, true);
    document.removeEventListener('mousedown', _onOutside, true);
    document.removeEventListener('pointerdown', _onOutside, true);
    _onOutside = null;
  }
  if (_onEsc) {
    document.removeEventListener('keydown', _onEsc, true);
    _onEsc = null;
  }
}

function _destroyPopover() {
  _detachOutsideHandlers();
  if (_popover && _popover.parentNode) {
    _popover.parentNode.removeChild(_popover);
  }
  _popover = null;
  _input = null;
  _drag = null;
}

function open(inputEl) {
  // Always tear down any previous popover so we never inherit stale state
  // (orphaned listeners, hidden-but-mispositioned div, etc.).
  _destroyPopover();
  _popover = buildPopover();
  _input = inputEl;
  setFromHex(inputEl.value || '#000000');
  _popover.style.display = 'block';
  _popover.style.visibility = 'visible';
  _popover.style.opacity = '1';
  _popover.style.pointerEvents = 'auto';
  // Let it render with its natural size, then position
  requestAnimationFrame(() => {
    if (_popover && _input) position(_popover, _input);
  });
  syncUI();

  _onOutside = (e) => {
    if (_drag) return;                        // ignore during drag
    if (!_popover) return;
    if (_popover.contains(e.target)) return;
    if (e.target === _input) return;
    // If the click landed on a modal close button (X), swallow it so the
    // popover-close doesn't also dismiss the enclosing modal. The user
    // wants their first click to just close the color picker.
    const closeBtn = e.target.closest && e.target.closest('.close-btn, [aria-label*="lose" i]');
    if (closeBtn) {
      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
    }
    close();
  };
  _onEsc = (e) => {
    if (e.key === 'Escape') {
      // Same idea for the keyboard: Escape closes the picker first; the
      // modal's own Esc handler only fires on the next press.
      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
      close();
    }
  };
  // Defer install so the click that opened us doesn't immediately close us.
  // Use requestAnimationFrame instead of setTimeout(0) to be sure the current
  // click event has fully bubbled before we register the listener.
  requestAnimationFrame(() => {
    document.addEventListener('click', _onOutside, true);
    // pointerdown fires before click on touch devices, and reliably even
    // when the tap target swallows the click. Catching it ensures
    // outside-touches close the picker on mobile.
    document.addEventListener('pointerdown', _onOutside, true);
    document.addEventListener('keydown', _onEsc, true);
  });
}

function close() {
  _destroyPopover();
}

// ── Attach to inputs ──────────────────────────────────────────────────
// Standard setter we need to call after wrapping .value with a custom setter.
const _NATIVE_VALUE_DESC = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');

function _syncSwatch(el) {
  const v = _NATIVE_VALUE_DESC.get.call(el);
  if (/^#[0-9a-f]{6}$/i.test(v || '')) el.style.background = v;
}

export function attachColorPicker(inputEl) {
  if (!inputEl || inputEl.dataset.cpAttached === '1') return;
  inputEl.dataset.cpAttached = '1';

  // Neutralize the native color dialog by changing the element's type.
  // Existing `.value` reads + `input` event listeners continue to work.
  const initialAttr = inputEl.getAttribute('value');
  const initial = inputEl.value || initialAttr || '#000000';
  inputEl.setAttribute('data-cp-original-type', inputEl.type || 'color');
  inputEl.type = 'text';
  inputEl.readOnly = true;
  inputEl.classList.add('cp-swatch-input');

  // Wrap .value so ANY assignment (from theme.js applyColors etc.) auto-updates the swatch bg.
  Object.defineProperty(inputEl, 'value', {
    configurable: true,
    get() { return _NATIVE_VALUE_DESC.get.call(this); },
    set(v) {
      _NATIVE_VALUE_DESC.set.call(this, v);
      _syncSwatch(this);
    },
  });

  // Apply initial value so swatch shows color even before any programmatic set.
  inputEl.value = initial;

  // Use mousedown so we fire BEFORE any document-level click handler
  // (e.g. our own _onOutside listener) can decide to close.
  inputEl.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    // If the same input already has the picker open, close it (toggle).
    // Otherwise always (re)open — never get stuck in a "won't reopen" state.
    if (_input === inputEl && _popover) {
      close();
    } else {
      open(inputEl);
    }
  });
  // Suppress the trailing click so it can't bubble to overlays/listeners.
  inputEl.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
}

export function initColorPickers(root = document) {
  root.querySelectorAll('input[type="color"]').forEach(attachColorPicker);
}

// Re-run on new inputs that may mount after init
export function refreshColorPickers(root = document) {
  initColorPickers(root);
}
