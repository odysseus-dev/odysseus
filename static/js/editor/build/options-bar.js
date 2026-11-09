/**
 * Contextual tool options bar — a Photoshop-style strip that sits
 * directly under the topbar and shows quick controls for the active
 * tool.
 *
 * This is a *mirror* of the authoritative controls in the right-hand
 * panel: it never owns state. Every value is delegated to the existing
 * input element (by selector) so the two views can never disagree —
 * editing here re-dispatches the same `input`/`change` event the real
 * control listens for, and the real control pushes updates back into
 * the bar.
 *
 * On phones (<= 820px) the bar is hidden by CSS and the right-panel
 * bottom sheet remains the single surface, exactly as before.
 */
import { TOOLS } from './toolbar.js?v=20261010brushpresets';
import { state } from '../state.js';

// Per-tool option sources. Each value is a CSS selector for an existing
// control inside the right panel; `null` means "no such control".
const BRUSH_TOOLS = {
  brush:   { size: '.ge-size-slider', opacity: '#ge-brush-opacity', flow: '#ge-brush-flow', softness: '#ge-brush-softness' },
  smudge:  { size: '.ge-size-slider', opacity: '#ge-brush-opacity', flow: '#ge-brush-flow', softness: '#ge-brush-softness' },
  dodge:   { size: '.ge-size-slider', opacity: '#ge-brush-opacity', flow: '#ge-brush-flow', softness: '#ge-brush-softness', intensity: '#ge-dodgeburn-intensity' },
  burn:    { size: '.ge-size-slider', opacity: '#ge-brush-opacity', flow: '#ge-brush-flow', softness: '#ge-brush-softness', intensity: '#ge-dodgeburn-intensity' },
  eraser:  { size: '.ge-size-slider', opacity: '#ge-eraser-opacity', flow: '#ge-eraser-flow', softness: '#ge-eraser-softness' },
  clone:   { size: '.ge-size-slider', opacity: '#ge-clone-opacity', flow: '#ge-clone-flow', softness: '#ge-clone-softness' },
  heal:    { size: '.ge-size-slider', opacity: '#ge-clone-opacity', flow: '#ge-clone-flow', softness: '#ge-clone-softness' },
};

const SELECTION_TOOLS = {
  marquee: { modes: null, feather: null },
  lasso:   { modes: null, feather: '#ge-lasso-feather' },
  wand:    { modes: null, feather: '#ge-wand-feather' },
  quickselect:  { modes: null, feather: null },
  objectselect: { modes: null, feather: null },
  sam:     { modes: null, feather: null },
};

// Liquify is not a brush: it exposes its own mode plus dab size and strength.
const LIQUIFY_TOOLS = {
  liquify: { size: '#ge-liquify-size', strength: '#ge-liquify-strength' },
};

const INTERSECT_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" style="position:relative;top:2px"><circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/></svg>';
const MODE_LABELS = [['replace', 'New'], ['add', '+'], ['subtract', '−'], ['intersect', INTERSECT_ICON]];

function resolve(selector) {
  if (!selector) return null;
  return selector.startsWith('#')
    ? document.getElementById(selector.slice(1))
    : document.querySelector(selector);
}

function makeRange(label, sourceSelector, { suffix = '' } = {}) {
  const source = resolve(sourceSelector);
  const value = source ? source.value : '0';
  const min = source?.getAttribute('min') ?? '0';
  const max = source?.getAttribute('max') ?? '100';
  const step = source?.getAttribute('step') ?? '1';
  return `
    <div class="ge-opt" data-opt="range">
      <label class="ge-opt-label">${label}<span class="ge-opt-value" data-opt-value>--</span></label>
      <input type="range" data-opt-range data-src="${sourceSelector}" min="${min}" max="${max}" step="${step}" value="${value}" />
    </div>`;
}

function makeSelect(label, sourceSelector) {
  const source = resolve(sourceSelector);
  const options = source
    ? [...source.options].map(o => `<option value="${o.value}">${o.textContent}</option>`).join('')
    : '';
  const value = source ? source.value : '';
  return `
    <div class="ge-opt" data-opt="select">
      <label class="ge-opt-label">${label}</label>
      <select data-opt-select data-src="${sourceSelector}">${options}</select>
    </div>`;
}

function makeAutoSelect() {
  return `
    <label class="ge-opt ge-auto-select-option" data-opt="check">
      <input type="checkbox" id="ge-auto-select-layer" class="ge-ctl-check" />
      <span class="ge-opt-label">Auto-Select</span>
    </label>
    <div class="ge-opt" data-opt="select">
      <label class="ge-opt-label" for="ge-auto-select-target">Target</label>
      <select id="ge-auto-select-target">
        <option value="layer">Layer</option>
        <option value="group">Group</option>
      </select>
    </div>`;
}

function makeModes(sectionSelector) {
  const buttons = MODE_LABELS.map(([mode, text]) =>
    `<button type="button" class="ge-opt-mode" data-opt-mode="${mode}" data-section="${sectionSelector}">${text}</button>`
  ).join('');
  return `<div class="ge-opt" data-opt="modes"><span class="ge-opt-label">Mode</span><span class="ge-opt-modes">${buttons}</span></div>`;
}

function buildToolControls(toolId) {
  const parts = [];

  if (BRUSH_TOOLS[toolId]) {
    const src = BRUSH_TOOLS[toolId];
    parts.push(makeRange('Size', src.size, { suffix: 'px' }));
    parts.push(makeRange('Opacity', src.opacity));
    parts.push(makeRange('Flow', src.flow));
    parts.push(makeRange('Softness', src.softness));
    if (src.intensity) parts.push(makeRange('Intensity', src.intensity));
  } else if (SELECTION_TOOLS[toolId]) {
    const src = SELECTION_TOOLS[toolId];
    if (src.modes) parts.push(makeModes(src.modes));
    if (src.feather) parts.push(makeRange('Feather', src.feather, { suffix: 'px' }));
  } else if (LIQUIFY_TOOLS[toolId]) {
    const src = LIQUIFY_TOOLS[toolId];
    parts.push(makeSelect('Mode', '#ge-liquify-mode'));
    parts.push(makeRange('Size', src.size, { suffix: 'px' }));
    parts.push(makeRange('Strength', src.strength));
  } else if (toolId === 'move') {
    parts.push(makeAutoSelect());
  }

  if (!parts.length) {
    return '';
  }
  return parts.join('');
}

function valueLabelOf(source, value) {
  // Prefer the sibling label the right panel already maintains so the
  // two surfaces always show the same formatted number.
  const row = source?.closest?.('.ge-eraser-row, .ge-control-row');
  const label = row?.querySelector('span[id$="-label"], span[id$="-opacity-label"], span[id$="-flow-label"], span[id$="-softness-label"]');
  const text = label?.textContent?.trim();
  return text || (typeof value === 'string' ? value : String(value));
}

/** Refresh every mirrored control inside `bar` from its source. */
export function syncOptionsBar(bar) {
  if (!bar) return;
  bar.querySelectorAll('[data-src]').forEach(el => {
    const source = resolve(el.dataset.src);
    if (!source) return;
    if (el.tagName === 'SELECT') { el.value = source.value; return; }
    el.value = source.value;
    const field = el.closest('.ge-opt');
    const out = field?.querySelector('[data-opt-value]');
    if (out) out.textContent = valueLabelOf(source, source.value);
  });
  // Selection mode buttons.
  bar.querySelectorAll('[data-opt-mode]').forEach(btn => {
    const source = resolve(`${btn.dataset.section} [data-wand-mode="${btn.dataset.optMode}"]`);
    btn.classList.toggle('active', !!source && source.classList.contains('active'));
  });
  // Move-tool auto-select (owned here rather than in the right panel).
  const autoSelect = bar.querySelector('#ge-auto-select-layer');
  if (autoSelect) autoSelect.checked = !!state.autoSelectLayer;
  const target = bar.querySelector('#ge-auto-select-target');
  if (target) target.value = state.autoSelectTarget === 'group' ? 'group' : 'layer';
}

/** Render the controls for `toolId` and wire delegation + mirroring. */
export function renderOptionsBar(bar, toolId) {
  if (!bar) return;
  bar.dataset.optionsTool = toolId;
  const hasControls = !!(BRUSH_TOOLS[toolId] || SELECTION_TOOLS[toolId] || LIQUIFY_TOOLS[toolId] || toolId === 'gradient' || toolId === 'move');
  // Keep the strip mounted at a stable height for every tool. Hiding it
  // for tools without options reflows the centered canvas-area and makes
  // the canvas jump vertically when switching tools; an empty strip is
  // visually harmless.
  bar.classList.toggle('ge-options-empty', !hasControls);

  // Brush-family tools now expose their secondary rows (spacing / smoothing /
  // blend / pen pressure) through their own caret dropdown in the strip, so
  // the generic "..." overflow button is retired for every tool.
  const moreBtn = bar.querySelector('#ge-options-more');
  if (moreBtn) {
    const showMore = false;
    moreBtn.hidden = !showMore;
    if (!showMore) {
      bar.classList.remove('ge-more-open');
      moreBtn.setAttribute('aria-expanded', 'false');
    }
  }

  const icon = bar.querySelector('.ge-options-tool-icon');
  const name = bar.querySelector('.ge-options-tool-name');
  const meta = TOOLS[toolId];
  if (name) name.textContent = meta?.label || toolId || '';
  if (icon) icon.innerHTML = meta?.icon || '';

  const body = bar.querySelector('.ge-options-body');
  if (body) body.innerHTML = buildToolControls(toolId);

  // Mirror right-panel changes back into the bar.
  bar.querySelectorAll('[data-src]').forEach(el => {
    const source = resolve(el.dataset.src);
    if (!source || source.dataset.geOptMirror) return;
    source.dataset.geOptMirror = '1';
    source.addEventListener('input', () => syncOptionsBar(bar));
    source.addEventListener('change', () => syncOptionsBar(bar));
  });

  syncOptionsBar(bar);
}

/**
 * Build the (empty) options-bar shell. Call `renderOptionsBar` after
 * the right-panel controls exist so delegation can resolve them.
 */
export function buildOptionsBar() {
  const bar = document.createElement('div');
  bar.className = 'ge-options-bar';
  bar.id = 'ge-options-bar';
  bar.innerHTML = `
    <div class="ge-options-tool">
      <span class="ge-options-tool-icon" aria-hidden="true"></span>
      <span class="ge-options-tool-name"></span>
    </div>
    <div class="ge-options-sep" aria-hidden="true"></div>
    <div class="ge-options-body"></div>
    <button type="button" class="ge-options-more" id="ge-options-more" title="More options" aria-label="More options" aria-haspopup="true" aria-expanded="false" hidden>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>
    </button>`;

  const more = bar.querySelector('#ge-options-more');
  more.addEventListener('click', (event) => {
    event.stopPropagation();
    const open = bar.classList.toggle('ge-more-open');
    more.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  // Delegation: ranges + selects write through to their real control.
  bar.addEventListener('input', event => {
    const el = event.target.closest('[data-opt-range]');
    if (!el) return;
    const source = resolve(el.dataset.src);
    if (!source) return;
    source.value = el.value;
    source.dispatchEvent(new Event('input', { bubbles: true }));
    const out = el.closest('.ge-opt')?.querySelector('[data-opt-value]');
    if (out) out.textContent = valueLabelOf(source, source.value);
  });
  bar.addEventListener('change', event => {
    if (event.target.id === 'ge-auto-select-layer') {
      state.autoSelectLayer = event.target.checked;
      bar.dispatchEvent(new CustomEvent('ge-editor-prefs-change', { bubbles: true }));
      return;
    }
    if (event.target.id === 'ge-auto-select-target') {
      state.autoSelectTarget = event.target.value === 'group' ? 'group' : 'layer';
      bar.dispatchEvent(new CustomEvent('ge-editor-prefs-change', { bubbles: true }));
      return;
    }
    const el = event.target.closest('[data-opt-select]');
    if (!el) return;
    const source = resolve(el.dataset.src);
    if (!source) return;
    source.value = el.value;
    source.dispatchEvent(new Event('change', { bubbles: true }));
  });
  bar.addEventListener('click', event => {
    const btn = event.target.closest('[data-opt-mode]');
    if (!btn) return;
    const source = resolve(`${btn.dataset.section} [data-wand-mode="${btn.dataset.optMode}"]`);
    source?.click();
    syncOptionsBar(bar);
  });

  return bar;
}
