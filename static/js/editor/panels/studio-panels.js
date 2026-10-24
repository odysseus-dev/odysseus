/**
 * Extra right-dock studio panels (PhotoCraft parity): Properties,
 * Character/Paragraph, Paths, Patterns, Adjustments and History. Every
 * panel is decoupled — live edits are delegated back through the
 * callbacks passed in, so this module owns no editor state.
 */
import { WARP_STYLES, EDITOR_TEXT_FONTS, availableEditorFonts } from '../text-layer.js';

const FONTS = availableEditorFonts().length ? availableEditorFonts() : EDITOR_TEXT_FONTS.slice();

export const ADJUSTMENT_TYPES = Object.freeze([
  ['brightness-contrast', 'Brightness/Contrast'],
  ['exposure', 'Exposure'],
  ['develop', 'Develop'],
  ['white-balance', 'White Balance'],
  ['hue-saturation', 'Hue/Saturation'],
  ['vibrance', 'Vibrance'],
  ['black-white', 'Black & White'],
  ['shadows-highlights', 'Shadows/Highlights'],
  ['levels', 'Levels'],
  ['curves', 'Curves'],
  ['color-balance', 'Color Balance'],
  ['selective-color', 'Selective Color'],
  ['gradient-map', 'Gradient Map'],
  ['invert', 'Invert'],
  ['posterize', 'Posterize'],
  ['threshold', 'Threshold'],
  ['photo-filter', 'Photo Filter'],
  ['channel-mixer', 'Channel Mixer'],
  ['color-lookup', 'Color Lookup'],
]);

// Inline SVG glyphs (16x16, currentColor) for the Adjustments panel buttons.
// Kept as path strings so each button is a single self-contained SVG.
const ADJUSTMENT_ICONS = Object.freeze({
  'brightness-contrast': '<circle cx="8" cy="8" r="6"/><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor" stroke="none"/>',
  exposure: '<circle cx="8" cy="8" r="3.2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.2 3.2l1.4 1.4M11.4 11.4l1.4 1.4M12.8 3.2l-1.4 1.4M4.6 11.4l-1.4 1.4"/>',
  develop: '<path d="M2 4h12M2 8h12M2 12h12"/><circle cx="5" cy="4" r="1.6" fill="currentColor" stroke="none"/><circle cx="10" cy="8" r="1.6" fill="currentColor" stroke="none"/><circle cx="6" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
  'white-balance': '<circle cx="8" cy="8" r="6"/><path d="M8 2a6 6 0 0 0 0 12z" fill="currentColor" stroke="none"/>',
  'hue-saturation': '<circle cx="8" cy="8" r="6"/><path d="M8 2v12M2 8h12" opacity=".7"/>',
  vibrance: '<circle cx="8" cy="8" r="5.5"/><path d="M8 4.4l1 2.4 2.4 1-2.4 1-1 2.4-1-2.4-2.4-1 2.4-1z" fill="currentColor" stroke="none"/>',
  'black-white': '<rect x="2" y="2" width="12" height="12" rx="2"/><path d="M8 2h4a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8z" fill="currentColor" stroke="none"/>',
  'shadows-highlights': '<circle cx="6" cy="8" r="4"/><circle cx="10" cy="8" r="4"/>',
  levels: '<path d="M3 13V7M6.3 13V4M9.7 13V9M13 13V6"/>',
  curves: '<path d="M2 13C6 13 6 3 14 3"/>',
  'color-balance': '<circle cx="6" cy="6" r="3.6"/><circle cx="10" cy="6" r="3.6"/><circle cx="8" cy="10" r="3.6"/>',
  'selective-color': '<circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="2.6" fill="currentColor" stroke="none"/>',
  'gradient-map': '<rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M2 7h12M2 9h12M2 11h12" opacity=".55"/>',
  invert: '<circle cx="8" cy="8" r="6"/><path d="M8 2a6 6 0 0 0 0 12z" fill="currentColor" stroke="none"/>',
  posterize: '<rect x="2" y="2" width="6" height="6"/><rect x="8" y="8" width="6" height="6"/><rect x="8" y="2" width="6" height="6" fill="currentColor" stroke="none"/>',
  threshold: '<rect x="2" y="2" width="12" height="12" rx="1.5"/><path d="M8 2h6v12H8z" fill="currentColor" stroke="none"/>',
  'photo-filter': '<circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="2.6"/>',
  'channel-mixer': '<path d="M3 4h10M3 8h10M3 12h10"/><circle cx="6" cy="4" r="1.6" fill="currentColor" stroke="none"/><circle cx="10" cy="8" r="1.6" fill="currentColor" stroke="none"/><circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
  'color-lookup': '<rect x="2" y="2" width="5.5" height="5.5"/><rect x="8.5" y="2" width="5.5" height="5.5"/><rect x="2" y="8.5" width="5.5" height="5.5"/><rect x="8.5" y="8.5" width="5.5" height="5.5"/>',
});

function adjIcon(type) {
  const body = ADJUSTMENT_ICONS[type];
  if (!body) return '';
  return `<span class="ge-adj-icon" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">${body}</svg></span>`;
}

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}

function panel(id, title, body) {
  const wrap = el('div', 'ge-panel');
  wrap.id = `ge-panel-${id}`;
  if (title) wrap.appendChild(el('div', 'ge-panel-title', title));
  wrap.appendChild(body);
  return wrap;
}

function row(label, control) {
  const wrap = el('label', 'ge-text-field');
  wrap.appendChild(el('span', null, label));
  wrap.appendChild(control);
  return wrap;
}

function activeLayerOf(state) {
  return (state.layers || []).find(layer => layer.id === state.activeLayerId) || null;
}

function patternDataUrl(kind) {
  const canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#20242c';
  ctx.fillRect(0, 0, 32, 32);
  ctx.fillStyle = '#c8cdd6';
  if (kind === 'stripes') {
    for (let x = 0; x < 32; x += 8) ctx.fillRect(x, 0, 4, 32);
  } else if (kind === 'checker') {
    for (let y = 0; y < 32; y += 8) {
      for (let x = 0; x < 32; x += 8) if (((x + y) / 8) % 2 === 0) ctx.fillRect(x, y, 8, 8);
    }
  } else if (kind === 'dots') {
    for (let y = 4; y < 32; y += 8) for (let x = 4; x < 32; x += 8) { ctx.beginPath(); ctx.arc(x, y, 2, 0, Math.PI * 2); ctx.fill(); }
  } else if (kind === 'grid') {
    ctx.strokeStyle = '#c8cdd6';
    for (let i = 0; i <= 32; i += 8) { ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, 32); ctx.moveTo(0, i); ctx.lineTo(32, i); ctx.stroke(); }
  } else if (kind === 'diagonal') {
    for (let i = -32; i < 32; i += 8) { ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i + 32, 32); ctx.lineWidth = 3; ctx.strokeStyle = '#c8cdd6'; ctx.stroke(); }
  } else if (kind === 'cross') {
    ctx.fillRect(12, 0, 4, 32);
    ctx.fillRect(0, 12, 32, 4);
  }
  return canvas.toDataURL('image/png');
}

const PATTERN_KINDS = ['stripes', 'checker', 'dots', 'grid', 'diagonal', 'cross'];

export function createStudioPanels({
  state,
  onSelectLayer = () => {},
  onAddAdjustment = () => {},
  onPattern = () => {},
  updateText = () => {},
  getTextLayer = () => null,
  setLayerOpacity = () => {},
  requestRender = () => {},
} = {}) {
  if (!state) throw new Error('createStudioPanels requires editor state');

  // ---- Properties -------------------------------------------------------
  const propBody = el('div', 'ge-panel-body');
  const propName = el('div', 'ge-info-row', '<span class="ge-info-label">Layer</span><span class="ge-info-value" data-prop="name">—</span>');
  const propKind = el('div', 'ge-info-row', '<span class="ge-info-label">Type</span><span class="ge-info-value" data-prop="kind">—</span>');
  const propOpacity = document.createElement('input');
  propOpacity.type = 'range';
  propOpacity.min = '0';
  propOpacity.max = '100';
  propOpacity.value = '100';
  propOpacity.setAttribute('data-prop', 'opacity');
  propOpacity.addEventListener('input', () => setLayerOpacity(Number(propOpacity.value) / 100));
  const propBlend = document.createElement('select');
  propBlend.setAttribute('data-prop', 'blend');
  const liveBlend = document.getElementById('ge-layer-blend');
  if (liveBlend) {
    propBlend.innerHTML = liveBlend.innerHTML;
    propBlend.addEventListener('change', () => {
      const target = document.getElementById('ge-layer-blend');
      if (target) { target.value = propBlend.value; target.dispatchEvent(new Event('change', { bubbles: true })); }
    });
  }
  propBody.append(propName, propKind, row('Opacity', propOpacity), row('Blend', propBlend));
  const properties = panel('properties', '', propBody);

  // ---- Character / Paragraph -------------------------------------------
  const charBody = el('div', 'ge-panel-body');
  const charFont = document.createElement('select');
  FONTS.forEach(font => charFont.appendChild(new Option(font, font)));
  const charSize = document.createElement('input'); charSize.type = 'number'; charSize.min = '1'; charSize.max = '2000'; charSize.value = '48';
  const charLine = document.createElement('input'); charLine.type = 'number'; charLine.step = '0.05'; charLine.min = '0.5'; charLine.max = '5'; charLine.value = '1.2';
  const charTracking = document.createElement('input'); charTracking.type = 'number'; charTracking.min = '-100'; charTracking.max = '500'; charTracking.value = '0';
  const charColor = document.createElement('input'); charColor.type = 'color'; charColor.className = 'ge-color-picker'; charColor.value = '#ffffff';
  const charVertical = document.createElement('input'); charVertical.type = 'checkbox';
  const charSmallCaps = document.createElement('input'); charSmallCaps.type = 'checkbox';
  const charLigatures = document.createElement('input'); charLigatures.type = 'checkbox'; charLigatures.checked = true;
  const charWarp = document.createElement('select');
  WARP_STYLES.forEach(style => charWarp.appendChild(new Option(style, style)));
  const alignButtons = el('div', 'ge-text-align-row');
  const alignGroup = ['left', 'center', 'right'].map(value => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ge-text-toggle';
    button.dataset.charAlign = value;
    button.textContent = value === 'left' ? 'L' : value === 'center' ? 'C' : 'R';
    alignButtons.appendChild(button);
    return button;
  });
  const emitText = patch => { updateText(patch); };
  charFont.addEventListener('change', () => emitText({ fontFamily: charFont.value }));
  [['fontSize', charSize], ['lineHeight', charLine], ['letterSpacing', charTracking]].forEach(([key, input]) => {
    input.addEventListener('input', () => emitText({ [key]: Number(input.value) }));
  });
  charColor.addEventListener('input', () => emitText({ color: charColor.value }));
  charVertical.addEventListener('change', () => emitText({ vertical: charVertical.checked }));
  charSmallCaps.addEventListener('change', () => emitText({ smallCaps: charSmallCaps.checked }));
  charLigatures.addEventListener('change', () => emitText({ ligatures: charLigatures.checked }));
  charWarp.addEventListener('change', () => emitText({ warp: charWarp.value }));
  alignGroup.forEach(button => button.addEventListener('click', () => {
    emitText({ align: button.dataset.charAlign });
    refreshCharacter();
  }));
  charBody.append(
    row('Font', charFont), row('Size', charSize), row('Line height', charLine),
    row('Tracking', charTracking), row('Color', charColor), alignButtons,
    row('Vertical', charVertical), row('Small caps', charSmallCaps),
    row('Ligatures', charLigatures), row('Warp', charWarp),
  );
  const character = panel('character', 'CHARACTER / PARAGRAPH', charBody);

  // ---- Paths ------------------------------------------------------------
  const pathBody = el('div', 'ge-panel-body ge-path-list');
  const paths = panel('paths', 'PATHS', pathBody);

  // ---- Patterns ---------------------------------------------------------
  const patternGrid = el('div', 'ge-swatch-grid');
  PATTERN_KINDS.forEach(kind => {
    const url = patternDataUrl(kind);
    const swatch = el('button', 'ge-swatch');
    swatch.type = 'button';
    swatch.title = kind;
    swatch.style.backgroundImage = `url(${url})`;
    swatch.style.background = `url(${url})`;
    swatch.addEventListener('click', () => {
      renderPatterns(kind);
      onPattern(url, kind);
    });
    patternGrid.appendChild(swatch);
  });
  const patterns = panel('patterns', '', patternGrid);

  // ---- Adjustments ------------------------------------------------------
  const adjGrid = el('div', 'ge-adjustment-grid');
  ADJUSTMENT_TYPES.forEach(([type, label]) => {
    const button = el('button', 'ge-btn ge-btn-sm ge-adj-btn', `${adjIcon(type)}<span class="ge-adj-label">${label}</span>`);
    button.type = 'button';
    button.dataset.adjustmentType = type;
    button.addEventListener('click', () => onAddAdjustment(type, button));
    adjGrid.appendChild(button);
  });
  const adjustments = panel('adjustments', '', adjGrid);

  // ---- History ----------------------------------------------------------
  // Photoshop-style: the panel *is* the timeline — a scrollable list of
  // states you can click to jump to. No "open in window" button: the timeline
  // is already right here. The floating window is still reachable from the
  // topbar clock button (#ge-history-btn) if you want it undocked.
  const historyBody = el('div', 'ge-panel-body ge-history-body');
  const historyList = el('div', 'ge-history-list');
  historyList.id = 'ge-dock-history-list';
  historyBody.appendChild(historyList);
  const history = panel('history', 'HISTORY', historyBody);

  function refreshProperties() {
    const layer = activeLayerOf(state);
    propName.querySelector('[data-prop="name"]').textContent = layer ? (layer.name || 'Layer') : '—';
    propKind.querySelector('[data-prop="kind"]').textContent = layer ? (layer.kind || 'raster') : '—';
    const opacity = layer && Number.isFinite(layer.opacity) ? layer.opacity : 1;
    propOpacity.value = String(Math.round(opacity * 100));
    const target = document.getElementById('ge-layer-blend');
    if (target) propBlend.value = target.value;
  }

  function refreshCharacter() {
    const layer = getTextLayer();
    if (!layer || !layer.text) return;
    const text = layer.text;
    charFont.value = text.fontFamily || 'Arial';
    charSize.value = String(text.fontSize ?? 48);
    charLine.value = String(text.lineHeight ?? 1.2);
    charTracking.value = String(text.letterSpacing ?? 0);
    charColor.value = text.color || '#ffffff';
    charVertical.checked = !!text.vertical;
    charSmallCaps.checked = !!text.smallCaps;
    charLigatures.checked = text.ligatures !== false;
    charWarp.value = text.warp || 'none';
    alignGroup.forEach(button => button.classList.toggle('active', button.dataset.charAlign === (text.align || 'left')));
  }

  function renderPaths() {
    pathBody.innerHTML = '';
    const shapeLayers = (state.layers || []).filter(layer => layer.kind === 'shape' && layer.shape);
    if (!shapeLayers.length) {
      pathBody.appendChild(el('p', 'ge-panel-empty', 'No vector shapes yet.'));
      return;
    }
    shapeLayers.slice().reverse().forEach(layer => {
      const button = el('button', 'ge-channel-row', `<span class="ge-channel-name">${layer.shape.type === 'path' ? 'Path' : layer.shape.type} · ${layer.name || layer.id.slice(0, 6)}</span>`);
      button.type = 'button';
      button.addEventListener('click', () => { onSelectLayer(layer.id); renderPaths(); });
      pathBody.appendChild(button);
    });
  }

  function renderPatterns(activeKind = null) {
    patternGrid.querySelectorAll('.ge-swatch').forEach((swatch, index) => {
      swatch.classList.toggle('active', PATTERN_KINDS[index] === activeKind);
    });
  }

  function refresh() {
    refreshProperties();
    refreshCharacter();
    renderPaths();
  }

  refresh();
  return { properties, character, paths, patterns, adjustments, history, refresh };
}
