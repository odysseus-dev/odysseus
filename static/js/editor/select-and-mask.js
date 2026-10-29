// Select and Mask workspace.
//
// A full-area overlay that lets the user refine the current selection with
// the same feather/expand/smooth/border controls as Refine Selection, plus a
// quick brush to add to or subtract from the selection directly, and an
// output-to choice (selection, layer mask, new layer, new layer with mask).
//
// The module owns only the overlay + a working document-space mask canvas; it
// never mutates editor state itself. The caller supplies `refine` (the shared
// refineSelectionMask) and receives the finished mask through `onCommit`.

import { refineSelectionMask } from './selection-mask.js';

const REFINE_KEYS = [
  { key: 'feather', label: 'Feather', min: 0, max: 100, value: 0, suffix: 'px' },
  { key: 'expand', label: 'Expand / Contract', min: -100, max: 100, value: 0, suffix: 'px' },
  { key: 'smooth', label: 'Smooth', min: 0, max: 50, value: 0, suffix: 'px' },
  { key: 'border', label: 'Border', min: 0, max: 100, value: 0, suffix: 'px' },
];

export function openSelectAndMask({
  mask = null,
  width = 0,
  height = 0,
  refine = refineSelectionMask,
  onCommit,
  onCancel,
} = {}) {
  const docW = width || mask?.width || 512;
  const docH = height || mask?.height || 512;

  const work = document.createElement('canvas');
  work.width = docW;
  work.height = docH;
  const workCtx = work.getContext('2d');
  if (mask) workCtx.drawImage(mask, 0, 0, docW, docH);

  const params = { feather: 0, expand: 0, smooth: 0, border: 0 };
  let brushSize = Math.max(8, Math.round(Math.min(docW, docH) / 8));
  let brushMode = 'add';
  let painting = false;

  const overlay = document.createElement('div');
  overlay.className = 'ge-sam-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Select and Mask');

  const refineRows = REFINE_KEYS.map((k) => `
    <div class="ge-sam-row" data-sam-key="${k.key}">
      <label>${k.label} <span class="ge-sam-value" data-sam-value="${k.key}">${k.value}${k.suffix}</span></label>
      <input type="range" data-sam-range="${k.key}" min="${k.min}" max="${k.max}" value="${k.value}" step="1" />
    </div>`).join('');

  const outputOptions = [
    ['selection', 'Selection'],
    ['mask', 'Layer Mask'],
    ['newLayer', 'New Layer'],
    ['newLayerWithMask', 'New Layer with Mask'],
  ].map(([v, l]) => `<option value="${v}">${l}</option>`).join('');

  overlay.innerHTML = `
    <div class="ge-sam-panel">
      <div class="ge-sam-head">
        <span class="ge-sam-title">Select and Mask</span>
        <label class="ge-sam-output-label">Output to
          <select class="ge-sam-output">${outputOptions}</select>
        </label>
      </div>
      <div class="ge-sam-body">
        <canvas class="ge-sam-preview"></canvas>
        <div class="ge-sam-controls">
          ${refineRows}
          <div class="ge-sam-row ge-sam-brush-row">
            <label>Refine brush</label>
            <div class="ge-sam-brush-modes">
              <button type="button" class="ge-btn ge-btn-sm ge-sam-brush-add active" data-sam-brush="add">Add</button>
              <button type="button" class="ge-btn ge-btn-sm ge-sam-brush-subtract" data-sam-brush="subtract">Subtract</button>
            </div>
          </div>
          <div class="ge-sam-row" data-sam-key="brushSize">
            <label>Brush size <span class="ge-sam-value" data-sam-value="brushSize">${brushSize}px</span></label>
            <input type="range" data-sam-brush-size min="4" max="400" value="${brushSize}" step="1" />
          </div>
        </div>
      </div>
      <div class="ge-sam-actions">
        <button type="button" class="ge-btn ge-btn-sm ge-sam-cancel">Cancel</button>
        <button type="button" class="ge-btn ge-btn-sm ge-btn-primary ge-sam-apply">Apply</button>
      </div>
    </div>`;

  document.body.appendChild(overlay);

  const preview = overlay.querySelector('.ge-sam-preview');
  const previewCtx = preview.getContext('2d');
  const fit = Math.min(1, 320 / docW, 260 / docH);
  preview.width = Math.max(1, Math.round(docW * fit));
  preview.height = Math.max(1, Math.round(docH * fit));

  const refined = () => (typeof refine === 'function' ? refine(work, { ...params }) : work);

  const render = () => {
    const out = refined();
    previewCtx.clearRect(0, 0, preview.width, preview.height);
    previewCtx.fillStyle = '#11151b';
    previewCtx.fillRect(0, 0, preview.width, preview.height);
    previewCtx.drawImage(out, 0, 0, preview.width, preview.height);
  };

  const setValueLabel = (key, value, suffix = '') => {
    const el = overlay.querySelector(`[data-sam-value="${key}"]`);
    if (el) el.textContent = `${value}${suffix}`;
  };

  for (const k of REFINE_KEYS) {
    const input = overlay.querySelector(`[data-sam-range="${k.key}"]`);
    input?.addEventListener('input', () => {
      params[k.key] = parseInt(input.value, 10);
      setValueLabel(k.key, params[k.key], k.suffix);
      render();
    });
  }

  const brushSizeInput = overlay.querySelector('[data-sam-brush-size]');
  brushSizeInput?.addEventListener('input', () => {
    brushSize = parseInt(brushSizeInput.value, 10);
    setValueLabel('brushSize', brushSize, 'px');
  });

  overlay.querySelectorAll('[data-sam-brush]').forEach((btn) => {
    btn.addEventListener('click', () => {
      brushMode = btn.dataset.samBrush;
      overlay.querySelectorAll('[data-sam-brush]').forEach((b) => b.classList.toggle('active', b === btn));
    });
  });

  const paintAt = (event) => {
    const rect = preview.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width * docW;
    const y = (event.clientY - rect.top) / rect.height * docH;
    const radius = brushSize / 2;
    workCtx.save();
    workCtx.globalCompositeOperation = brushMode === 'subtract' ? 'destination-out' : 'source-over';
    workCtx.fillStyle = '#ffffff';
    workCtx.beginPath();
    workCtx.arc(x, y, radius, 0, Math.PI * 2);
    workCtx.fill();
    workCtx.restore();
    render();
  };

  preview.addEventListener('pointerdown', (e) => {
    painting = true;
    preview.setPointerCapture?.(e.pointerId);
    paintAt(e);
  });
  preview.addEventListener('pointermove', (e) => {
    if (painting) paintAt(e);
  });
  const endPaint = () => { painting = false; };
  preview.addEventListener('pointerup', endPaint);
  preview.addEventListener('pointercancel', endPaint);
  preview.addEventListener('pointerleave', endPaint);

  const close = () => {
    document.removeEventListener('keydown', onKeydown);
    overlay.remove();
  };

  const outputSel = overlay.querySelector('.ge-sam-output');
  overlay.querySelector('.ge-sam-cancel')?.addEventListener('click', () => {
    onCancel?.();
    close();
  });
  overlay.querySelector('.ge-sam-apply')?.addEventListener('click', () => {
    const finalMask = refined();
    onCommit?.({ mask: finalMask, output: outputSel?.value || 'selection' });
    close();
  });

  function onKeydown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      onCancel?.();
      close();
    } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      overlay.querySelector('.ge-sam-apply')?.click();
    }
  }
  document.addEventListener('keydown', onKeydown);

  render();
  return { close, getMask: () => refined() };
}
