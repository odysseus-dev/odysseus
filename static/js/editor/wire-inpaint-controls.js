/**
 * Inpaint panel controls — the non-AI side-panel UI for the inpaint
 * tool (the AI Generate/Remove/Outpaint buttons live in
 * editor/ai-inpaint.js).
 *
 *   Pre-gen sliders (Feather + Strength swatch previews):
 *     #ge-strength-slider     just-updates-the-label-and-swatch
 *
 *   Post-gen live edge tuners — alpha-blur + dilate/erode on the most
 *   recent Inpaint Result layer, rAF-throttled so dragging stays
 *   smooth on big canvases:
 *     #ge-feather-slider       calls applyInpaintFeather + composite
 *     #ge-edgestroke-slider    same
 *
 *   Mask controls:
 *     #ge-mask-vis             toggle red-overlay visibility
 *     #ge-inpaint-invert       invert the active mask sub-layer
 *     #ge-inpaint-clear        wipe the active mask
 *     #ge-inpaint-mode-paint   set persistent paint mode
 *     #ge-inpaint-mode-erase   set persistent erase mode
 *
 *   Mask tint pickers (wired to keep both visually in sync):
 *     .ge-inpaint-mask-color   (inpaint section)
 *     #ge-topbar-mask-color    (topbar swatch — HSV picker attached)
 *
 * @param {{
 *   composite:                () => void,
 *   applyInpaintFeather:      (layer: object, featherPx: number, edgeShiftPx: number) => void,
 *   syncToolClearIndicators:  () => void,
 *   attachColorPicker:        (el: HTMLInputElement) => void,
 *   uiModule:                 object,
 * }} deps
 */
import { state } from './state.js';

const EYE_OPEN_SM = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF_SM  = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';

export function wireInpaintControls({
  composite, applyInpaintFeather, syncToolClearIndicators,
  attachColorPicker, uiModule,
}) {
  // ── Feather + Strength preview swatches ──
  const featherPrev = document.getElementById('ge-feather-preview');
  const strengthPrev = document.getElementById('ge-strength-preview');
  function syncFeatherPreview(v) {
    if (!featherPrev) return;
    const inner = Math.max(0, 50 - v * 1.25);
    featherPrev.style.background = `radial-gradient(circle, var(--fg) 0%, var(--fg) ${inner}%, transparent 75%)`;
  }
  function syncStrengthPreview(v) {
    if (!strengthPrev) return;
    strengthPrev.style.opacity = (v / 100).toFixed(2);
  }

  // ── Post-inpaint live edge tuner ──
  // Alpha-blur (Feather) + dilate/erode (Edge Stroke) on the last
  // Inpaint Result layer. rAF-throttled so dragging stays smooth.
  let featherRafPending = false;
  function scheduleInpaintEdgeRefresh() {
    if (featherRafPending) return;
    featherRafPending = true;
    requestAnimationFrame(() => {
      featherRafPending = false;
      const layer = state.layers.find(l => l.id === state.lastInpaintLayerId);
      if (!layer || !layer.inpaintSource) return;
      const feather = parseInt(document.getElementById('ge-feather-slider')?.value || '0', 10);
      const edge = parseInt(document.getElementById('ge-edgestroke-slider')?.value || '0', 10);
      applyInpaintFeather(layer, feather, edge);
      composite();
    });
  }
  document.getElementById('ge-feather-slider')?.addEventListener('input', (e) => {
    const v = parseInt(e.target.value, 10);
    document.getElementById('ge-feather-label').textContent = v + 'px';
    syncFeatherPreview(v);
    scheduleInpaintEdgeRefresh();
  });
  document.getElementById('ge-edgestroke-slider')?.addEventListener('input', (e) => {
    const v = parseInt(e.target.value, 10);
    const label = document.getElementById('ge-edgestroke-label');
    if (label) label.textContent = (v > 0 ? '+' : '') + v + 'px';
    const prev = document.getElementById('ge-edgestroke-preview');
    if (prev) {
      // Visualise direction: dilate (+) → green, erode (−) → red.
      const dir = v === 0 ? 'transparent' : (v > 0 ? 'rgba(120,200,120,0.5)' : 'rgba(200,120,120,0.5)');
      prev.style.background = dir;
      prev.style.opacity = Math.min(1, Math.abs(v) / 80).toFixed(2);
    }
    scheduleInpaintEdgeRefresh();
  });
  document.getElementById('ge-strength-slider')?.addEventListener('input', (e) => {
    document.getElementById('ge-strength-label').textContent = (e.target.value / 100).toFixed(2);
    syncStrengthPreview(parseInt(e.target.value, 10));
  });
  syncFeatherPreview(0);
  syncStrengthPreview(75);

  // ── Mask vis / invert / clear ──
  document.getElementById('ge-mask-vis')?.addEventListener('click', () => {
    state.maskVisible = !state.maskVisible;
    const btn = document.getElementById('ge-mask-vis');
    if (!btn) { composite(); return; }
    btn.innerHTML = `${state.maskVisible ? EYE_OPEN_SM : EYE_OFF_SM}<span id="ge-mask-vis-label">${state.maskVisible ? window.t('Hide') : window.t('Show')}</span>`;
    btn.title = state.maskVisible ? window.t('Hide mask') : window.t('Show mask');
    btn.classList.toggle('visible', state.maskVisible);
    composite();
  });
  document.getElementById('ge-inpaint-invert')?.addEventListener('click', () => {
    if (!state.maskCtx || !state.maskCanvas) return;
    const imgData = state.maskCtx.getImageData(0, 0, state.maskCanvas.width, state.maskCanvas.height);
    const d = imgData.data;
    for (let i = 0; i < d.length; i += 4) {
      const alpha = d[i + 3];
      if (alpha > 0) {
        d[i] = 0; d[i+1] = 0; d[i+2] = 0; d[i+3] = 0;
      } else {
        d[i] = 255; d[i+1] = 255; d[i+2] = 255; d[i+3] = 255;
      }
    }
    state.maskCtx.putImageData(imgData, 0, 0);
    composite();
    syncToolClearIndicators();
    uiModule.showToast(window.t('Mask inverted'));
  });
  document.getElementById('ge-inpaint-clear')?.addEventListener('click', () => {
    if (state.maskCtx) { state.maskCtx.clearRect(0, 0, state.maskCanvas.width, state.maskCanvas.height); composite(); }
    syncToolClearIndicators();
  });

  // ── Paint / Erase segmented toggle ──
  function setInpaintMode(eraseMode) {
    state.inpaintEraseMode = !!eraseMode;
    const paintBtn = document.getElementById('ge-inpaint-mode-paint');
    const eraseBtn = document.getElementById('ge-inpaint-mode-erase');
    if (paintBtn) paintBtn.classList.toggle('active', !state.inpaintEraseMode);
    if (eraseBtn) eraseBtn.classList.toggle('active', state.inpaintEraseMode);
  }
  document.getElementById('ge-inpaint-mode-paint')?.addEventListener('click', () => setInpaintMode(false));
  document.getElementById('ge-inpaint-mode-erase')?.addEventListener('click', () => setInpaintMode(true));

  // ── Mask color picker ──
  // Updates state.maskTintColor live so the user can pick a colour
  // that contrasts with their photo. Wire both the topbar picker AND
  // the inpaint-section picker so changing one syncs the other.
  function applyMaskTintFromHex(hex) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    state.maskTintColor = `rgba(${r}, ${g}, ${b}, 1)`;
    const inpaintPicker = document.querySelector('.ge-inpaint-mask-color');
    const topbarPicker = document.getElementById('ge-topbar-mask-color');
    if (inpaintPicker && inpaintPicker.value !== hex) inpaintPicker.value = hex;
    if (topbarPicker && topbarPicker.value !== hex) topbarPicker.value = hex;
    composite();
  }
  document.querySelector('.ge-inpaint-mask-color')?.addEventListener('input', (e) => applyMaskTintFromHex(e.target.value));
  document.getElementById('ge-topbar-mask-color')?.addEventListener('input', (e) => applyMaskTintFromHex(e.target.value));
  // Use the in-house HSV picker for the topbar swatch.
  const topbarMaskColor = document.getElementById('ge-topbar-mask-color');
  if (topbarMaskColor) {
    try { attachColorPicker(topbarMaskColor); topbarMaskColor.value = topbarMaskColor.value; } catch {}
  }
}
