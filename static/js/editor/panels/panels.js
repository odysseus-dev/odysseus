// Color + analysis panels for the image editor right dock (PhotoCraft parity).
// Pure DOM panels that read the shared editor `state` and call back into the
// editor for the few actions that need to mutate the document (color pick,
// layer visibility, pan, zoom). No global side effects beyond the state/hooks
// passed in by buildRightPanel/galleryEditor.

import { drawHistogram } from '../fx/histogram.js';
import { applyCanvasPan } from '../canvas-navigation.js';
import { attachColorPicker, getRecentColors, suggestColorsFor, recordRecentColor } from '../../colorPicker.js?v=20260910eyedropper1';

const SWATCHES = [
  '#000000', '#404040', '#808080', '#c0c0c0', '#ffffff', '#ff0000', '#ff8000',
  '#ffff00', '#80ff00', '#00ff00', '#00ff80', '#00ffff', '#0080ff', '#0000ff',
  '#8000ff', '#ff00ff', '#ff0080', '#7f0000', '#7f4000', '#7f7f00', '#407f00',
  '#007f00', '#007f40', '#007f7f', '#00407f', '#00007f', '#40007f', '#7f007f',
  '#7f0040', '#ffcccc', '#ffd9b3', '#ffffcc', '#ccffcc', '#cce5ff', '#e5ccff',
  '#ffcce5', '#8b4513', '#d2b48c', '#f5deb3', '#2f4f4f',
];

const GRADIENTS = [
  ['Black → White', ['#000000', '#ffffff']],
  ['White → Black', ['#ffffff', '#000000']],
  ['Red → Yellow', ['#ff0000', '#ffff00']],
  ['Yellow → Blue', ['#ffff00', '#0000ff']],
  ['Cyan → Blue', ['#00ffff', '#0000ff']],
  ['Magenta → Orange', ['#ff00ff', '#ff8000']],
  ['Green → Cyan', ['#00ff00', '#00ffff']],
  ['Spectrum', ['#ff0000', '#ffff00', '#00ff00', '#00ffff', '#0000ff', '#ff00ff']],
];

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

function activeLayer(state) {
  if (!state || !Array.isArray(state.layers)) return null;
  return state.layers.find((layer) => layer.id === state.activeLayerId) || state.layers[state.layers.length - 1] || null;
}

// Minimal HSV <-> hex conversion for the inline Swatches picker. Kept local so
// the panel does not depend on colorPicker.js internals (which only export the
// harmony/recent helpers) and to avoid pulling the shared popover singleton
// into the dock.
function hexToHsv(hex) {
  const m = String(hex || '').trim().match(/^#?([0-9a-f]{6})$/i);
  const int = m ? parseInt(m[1], 16) : 0;
  const r = ((int >> 16) & 255) / 255;
  const g = ((int >> 8) & 255) / 255;
  const b = (int & 255) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const s = max ? (d / max) * 100 : 0;
  return { h, s, v: max * 100 };
}

function hsvToHex(h, s, v) {
  h = ((h % 360) + 360) % 360;
  s = Math.max(0, Math.min(100, s)) / 100;
  v = Math.max(0, Math.min(100, v)) / 100;
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  let r = 0, g = 0, b = 0;
  if (h < 60) { r = c; g = x; }
  else if (h < 120) { r = x; g = c; }
  else if (h < 180) { g = c; b = x; }
  else if (h < 240) { g = x; b = c; }
  else if (h < 300) { r = x; b = c; }
  else { r = c; b = x; }
  const to = (n) => Math.round((n + m) * 255).toString(16).padStart(2, '0');
  return `#${to(r)}${to(g)}${to(b)}`;
}

export function createColorPanels({
  state,
  canvasArea = null,
  setColor = (hex) => { if (state) state.color = hex; },
  requestRender = () => {},
  zoomIn = null,
  zoomOut = null,
  zoomFit = null,
} = {}) {
  if (!state) throw new Error('createColorPanels requires editor state');

  // ---- Swatches ---------------------------------------------------------
  // Reuse the app's shared color picker (colorPicker.js → `.cp-popover`: SL +
  // hue, hex, suggestions, recent swatches, eyedropper) instead of a static
  // preset grid.
  const swatchBody = el('div', 'ge-swatch-body');
  const colorInput = document.createElement('input');
  colorInput.type = 'color';
  colorInput.className = 'ge-color-input';
  colorInput.value = (state.color || '#000000');
  colorInput.title = 'Pick a color';
  // colorInput is kept as an internal colour holder only (it is not appended);
  // the panel shows the inline picker, a hex field, Suggestions and Recent.
  // Suggestions + recent colours sit right next to the picker (reusing the
  // app's colorPicker harmony/recent logic), so common picks are one click.
  const swatchCol = el('div', 'ge-swatch-col');
  const suggestRow = el('div', 'ge-swatch-row ge-swatch-suggest');
  const recentRow = el('div', 'ge-swatch-row ge-swatch-recent');
  const sugLabel = el('div', 'ge-swatch-row-label'); sugLabel.textContent = 'Suggestions';
  const recLabel = el('div', 'ge-swatch-row-label'); recLabel.textContent = 'Recent';
  swatchCol.append(sugLabel, suggestRow, recLabel, recentRow);
  swatchBody.appendChild(swatchCol);
  const _syncForeground = (hex) => {
    setColor(hex);
    // Mirror the pick into the rail's foreground swatch so the whole editor
    // (brush/bucket colour, swap, footer) follows a click on a suggestion.
    const fg = document.getElementById('ge-fg-color');
    if (fg) { fg.value = hex; try { fg.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {} }
  };
  const renderInlineSwatches = () => {
    const cur = state.color || colorInput.value || '#000000';
    const sug = suggestColorsFor(cur);
    suggestRow.innerHTML = sug.map(s =>
      `<button type="button" class="ge-mini-swatch" title="${s.label}: ${s.hex}" data-hex="${s.hex}" style="background:${s.hex}"></button>`
    ).join('');
    // Same number of Recent swatches as Suggestions, on a single row.
    const recs = getRecentColors().slice(0, sug.length);
    recentRow.innerHTML = recs.length
      ? recs.map(h => `<button type="button" class="ge-mini-swatch" title="${h}" data-hex="${h}" style="background:${h}"></button>`).join('')
      : '<span class="ge-mini-empty">none yet</span>';
  };

  // Inline picker: an always-visible SV square + hue strip so the panel reads
  // as a colour picker at a glance (the shared popover only appears after a
  // click, so a static dock shot never shows it). Reuses the app's .cp-sl /
  // .cp-hue styling so it matches the popover exactly.
  const picker = el('div', 'ge-inline-picker');
  const sl = el('div', 'cp-sl');
  sl.innerHTML = '<div class="cp-sl-white"></div><div class="cp-sl-black"></div><div class="cp-sl-handle"></div>';
  const hue = el('div', 'cp-hue');
  hue.innerHTML = '<div class="cp-hue-handle"></div>';
  picker.append(sl, hue);
  swatchBody.appendChild(picker);
  // Hex code field (e.g. #e06c75) — type a value to set the colour.
  const hexRow = el('div', 'ge-hex-row');
  const hexHash = el('span', 'ge-hex-hash'); hexHash.textContent = '#';
  const hexInput = el('input', 'ge-hex-input');
  hexInput.type = 'text';
  hexInput.spellcheck = false;
  hexInput.autocomplete = 'off';
  hexInput.setAttribute('aria-label', 'Hex colour code');
  hexInput.value = String(state.color || '#000000').replace(/^#/, '');
  hexRow.append(hexHash, hexInput);
  swatchBody.appendChild(hexRow);
  const _setHex = (hex) => {
    if (hexInput && document.activeElement !== hexInput) hexInput.value = String(hex || '').replace(/^#/, '');
  };
  hexInput.addEventListener('input', () => {
    let v = String(hexInput.value || '').trim();
    if (v && v[0] !== '#') v = `#${v}`;
    if (v.length === 4) v = `#${v[1]}${v[1]}${v[2]}${v[2]}${v[3]}${v[3]}`;
    if (!/^#[0-9a-f]{6}$/i.test(v)) return;
    _syncForeground(v);
    colorInput.value = v;
    setPickerFromColor(v);
    renderInlineSwatches();
  });
  const slHandle = sl.querySelector('.cp-sl-handle');
  const hueHandle = hue.querySelector('.cp-hue-handle');
  let ipH = 0, ipS = 0, ipV = 0, ipDrag = null;
  const paintPicker = () => {
    sl.style.backgroundColor = hsvToHex(ipH, 100, 100);
    slHandle.style.left = `${ipS}%`;
    slHandle.style.top = `${100 - ipV}%`;
    hueHandle.style.left = `${(ipH / 360) * 100}%`;
  };
  const setPickerFromColor = (hex) => {
    const { h, s, v } = hexToHsv(hex);
    ipH = h; ipS = s; ipV = v;
    paintPicker();
  };
  const commitPicker = () => {
    const hex = hsvToHex(ipH, ipS, ipV);
    _syncForeground(hex);
    colorInput.value = hex;
    _setHex(hex);
    renderInlineSwatches();
  };
  const pickFromEvent = (event) => {
    if (ipDrag === 'sl') {
      const r = sl.getBoundingClientRect();
      ipS = Math.max(0, Math.min(100, ((event.clientX - r.left) / r.width) * 100));
      ipV = Math.max(0, Math.min(100, (1 - (event.clientY - r.top) / r.height) * 100));
      paintPicker();
      commitPicker();
    } else if (ipDrag === 'hue') {
      const r = hue.getBoundingClientRect();
      ipH = Math.max(0, Math.min(360, ((event.clientX - r.left) / r.width) * 360));
      paintPicker();
      commitPicker();
    }
  };
  const startDrag = (kind, captureEl) => (event) => {
    event.preventDefault();
    ipDrag = kind;
    try { captureEl.setPointerCapture(event.pointerId); } catch (_) {}
    pickFromEvent(event);
  };
  sl.addEventListener('pointerdown', startDrag('sl', sl));
  hue.addEventListener('pointerdown', startDrag('hue', hue));
  picker.addEventListener('pointermove', (event) => { if (ipDrag) pickFromEvent(event); });
  picker.addEventListener('pointerup', () => { ipDrag = null; });
  picker.addEventListener('pointercancel', () => { ipDrag = null; });
  setPickerFromColor(state.color || colorInput.value || '#000000');

  swatchBody.addEventListener('click', (e) => {
    const btn = e.target.closest('.ge-mini-swatch');
    if (!btn) return;
    const hex = btn.dataset.hex;
    recordRecentColor(hex);
    _syncForeground(hex);
    colorInput.value = hex;
    _setHex(hex);
    setPickerFromColor(hex);
    renderInlineSwatches();
  });
  colorInput.addEventListener('input', () => {
    _syncForeground(colorInput.value);
    _setHex(colorInput.value);
    setPickerFromColor(colorInput.value);
    renderInlineSwatches();
  });
  const swatches = panel('swatches', '', swatchBody);
  renderInlineSwatches();
  const refreshSwatches = () => {
    const color = (state.color || '#000000').toLowerCase();
    if (String(colorInput.value).toLowerCase() !== color) colorInput.value = color;
    _setHex(color);
    setPickerFromColor(color);
    renderInlineSwatches();
  };

  // ---- Gradients --------------------------------------------------------
  const gradientGrid = el('div', 'ge-gradient-grid');
  GRADIENTS.forEach(([name, stops]) => {
    const css = `linear-gradient(90deg, ${stops.join(', ')})`;
    const btn = el('button', 'ge-gradient-swatch');
    btn.type = 'button';
    btn.title = name;
    btn.style.background = css;
    btn.addEventListener('click', () => {
      const start = document.getElementById('ge-gradient-start');
      const end = document.getElementById('ge-gradient-end');
      const type = document.getElementById('ge-gradient-type');
      if (start && end) {
        start.value = stops[0];
        end.value = stops[stops.length - 1];
        start.dispatchEvent(new Event('input', { bubbles: true }));
        end.dispatchEvent(new Event('input', { bubbles: true }));
        if (type) { type.value = 'linear'; type.dispatchEvent(new Event('change', { bubbles: true })); }
        requestRender();
      }
    });
    gradientGrid.appendChild(btn);
  });
  const gradients = panel('gradients', '', gradientGrid);

  // ---- Channels (layer visibility) -------------------------------------
  const channelList = el('div', 'ge-channel-list');
  const channels = panel('channels', 'CHANNELS', channelList);
  const renderChannels = () => {
    channelList.innerHTML = '';
    const layers = Array.isArray(state.layers) ? state.layers : [];
    if (!layers.length) {
      channelList.appendChild(el('p', 'ge-panel-empty', 'No layers yet.'));
      return;
    }
    layers.slice().reverse().forEach((layer) => {
      const row = el('div', 'ge-channel-row');
      row.classList.toggle('active', layer.id === state.activeLayerId);
      const eye = el('button', 'ge-channel-eye');
      eye.type = 'button';
      eye.setAttribute('aria-pressed', layer.visible === false ? 'false' : 'true');
      eye.title = 'Toggle visibility';
      eye.textContent = layer.visible === false ? '◌' : '◉';
      eye.addEventListener('click', () => {
        layer.visible = layer.visible === false;
        requestRender();
        renderChannels();
      });
      const name = el('span', 'ge-channel-name', layer.name || 'Layer');
      row.appendChild(eye);
      row.appendChild(name);
      row.addEventListener('click', (event) => {
        if (event.target === eye) return;
        state.activeLayerId = layer.id;
        requestRender();
        renderChannels();
      });
      channelList.appendChild(row);
    });
  };

  // ---- Histogram --------------------------------------------------------
  const histCanvas = el('canvas', 'ge-histogram-canvas');
  histCanvas.width = 260;
  histCanvas.height = 90;
  const histBody = el('div', 'ge-histogram-body');
  histBody.appendChild(histCanvas);
  const histogram = panel('histogram', '', histBody);
  const redrawHistogram = () => {
    try { drawHistogram(histCanvas, activeLayer(state)); } catch { /* not ready */ }
  };

  // ---- Info -------------------------------------------------------------
  const infoBody = el('div', 'ge-info-body');
  const infoColor = el('span', 'ge-info-color');
  const infoHex = el('span', 'ge-info-hex', '—');
  const infoSize = el('span', 'ge-info-size', '—');
  const infoZoom = el('span', 'ge-info-zoom', '—');
  const infoLayer = el('span', 'ge-info-layer', '—');
  [
    ['Color', [infoColor, infoHex]],
    ['Size', infoSize],
    ['Zoom', infoZoom],
    ['Layer', infoLayer],
  ].forEach(([label, nodes]) => {
    const row = el('div', 'ge-info-row');
    row.appendChild(el('span', 'ge-info-label', label));
    const cell = el('span', 'ge-info-value');
    (Array.isArray(nodes) ? nodes : [nodes]).forEach((n) => cell.appendChild(n));
    row.appendChild(cell);
    infoBody.appendChild(row);
  });
  const info = panel('info', 'INFO', infoBody);
  const refreshInfo = () => {
    infoSize.textContent = `${state.imgWidth || 0} × ${state.imgHeight || 0} px`;
    infoZoom.textContent = `${Math.round((state.zoom || 1) * 100)}%`;
    const layer = activeLayer(state);
    infoLayer.textContent = layer ? (layer.name || 'Layer') : '—';
  };
  const sampleAt = (clientX, clientY) => {
    const canvas = state.mainCanvas;
    const ctx = state.mainCtx;
    if (!canvas || !ctx) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = Math.round((clientX - rect.left) / rect.width * (canvas.width || 1));
    const y = Math.round((clientY - rect.top) / rect.height * (canvas.height || 1));
    if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) return;
    try {
      const data = ctx.getImageData(x, y, 1, 1).data;
      const hex = `#${[data[0], data[1], data[2]].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
      infoColor.style.background = hex;
      infoHex.textContent = hex.toUpperCase();
    } catch { /* tainted/empty */ }
  };
  if (canvasArea) {
    canvasArea.addEventListener('pointermove', (event) => sampleAt(event.clientX, event.clientY));
  }

  // ---- Navigator --------------------------------------------------------
  const navCanvas = el('canvas', 'ge-navigator-canvas');
  navCanvas.width = 220;
  navCanvas.height = 150;
  const navBody = el('div', 'ge-navigator-body');
  navBody.appendChild(navCanvas);
  const navigator = panel('navigator', 'NAVIGATOR', navBody);
  const navScale = () => {
    const docW = state.imgWidth || state.mainCanvas?.width || 1;
    const docH = state.imgHeight || state.mainCanvas?.height || 1;
    return Math.min(navCanvas.width / docW, navCanvas.height / docH);
  };
  const drawNavigator = () => {
    const ctx = navCanvas.getContext('2d');
    if (!ctx) return;
    ctx.clearRect(0, 0, navCanvas.width, navCanvas.height);
    ctx.fillStyle = '#0b0d10';
    ctx.fillRect(0, 0, navCanvas.width, navCanvas.height);
    const source = state.mainCanvas;
    if (source) {
      try {
        const s = navScale();
        const w = (state.imgWidth || source.width) * s;
        const h = (state.imgHeight || source.height) * s;
        ctx.drawImage(source, 0, 0, source.width, source.height, 0, 0, w, h);
        const zoom = state.zoom || 1;
        const area = canvasArea?.getBoundingClientRect();
        if (area && area.width && area.height) {
          const viewX = (-(state.panX || 0)) / zoom * s;
          const viewY = (-(state.panY || 0)) / zoom * s;
          const viewW = area.width / zoom * s;
          const viewH = area.height / zoom * s;
          ctx.strokeStyle = '#ff5a5f';
          ctx.lineWidth = 1.5;
          ctx.strokeRect(viewX, viewY, viewW, viewH);
        }
      } catch { /* not ready */ }
    }
  };
  const panFromNavigator = (event) => {
    const rect = navCanvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const mx = (event.clientX - rect.left) / rect.width * navCanvas.width;
    const my = (event.clientY - rect.top) / rect.height * navCanvas.height;
    const s = navScale();
    const docX = mx / s;
    const docY = my / s;
    const zoom = state.zoom || 1;
    const area = canvasArea;
    if (!area) return;
    const panX = -docX * zoom + area.clientWidth / 2;
    const panY = -docY * zoom + area.clientHeight / 2;
    applyCanvasPan(state, area, panX, panY);
    drawNavigator();
  };
  let navDragging = false;
  navCanvas.addEventListener('pointerdown', (event) => { navDragging = true; navCanvas.setPointerCapture?.(event.pointerId); panFromNavigator(event); });
  navCanvas.addEventListener('pointermove', (event) => { if (navDragging) panFromNavigator(event); });
  navCanvas.addEventListener('pointerup', (event) => { navDragging = false; navCanvas.releasePointerCapture?.(event.pointerId); });
  navCanvas.addEventListener('pointerleave', () => { navDragging = false; });

  const refresh = () => {
    refreshSwatches();
    renderChannels();
    redrawHistogram();
    refreshInfo();
    drawNavigator();
  };
  refresh();

  return { swatches, gradients, channels, histogram, info, navigator, refresh };
}
