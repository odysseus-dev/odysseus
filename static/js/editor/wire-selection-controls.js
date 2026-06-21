/**
 * Lasso + Magic Wand panel controls — sliders, mode toggles, and the
 * panel action buttons (Invert / Clear / Delete / Copy / To Mask /
 * Bg Remove). The actual selection algorithms live in their tool
 * modules (editor/tools/lasso.js, editor/tools/wand.js); this file
 * just wires the side-panel UI to them.
 *
 *   Lasso section:
 *     #ge-lasso-feather       slider, updates label + preview, recomposites
 *     #ge-lasso-grow          slider, updates label + recomposites
 *     #ge-lasso-invert        → invertSelection
 *     #ge-lasso-delete        → lassoDeleteSelection
 *     #ge-lasso-copy          → lassoCopyToLayer
 *     #ge-lasso-mask          → lassoToMask
 *
 *   Wand section:
 *     #ge-wand-feather        slider, updates label + recomposites
 *     #ge-wand-grow           slider, updates label + recomposites
 *     #ge-wand-tolerance      slider, updates future wand-click tolerance
 *     #ge-wand-live           opt-in rAF-coalesced live retune while dragging
 *     .ge-wand-mode-btn       segmented toggle (New / Add / Subtract)
 *     #ge-wand-vis            toggle the translucent red overlay
 *     #ge-wand-clear / -invert / -delete / -copy / -mask / -rembg
 *
 * @param {{
 *   composite:               () => void,
 *   invertSelection:         () => boolean,
 *   lassoDeleteSelection:    () => void,
 *   lassoCopyToLayer:        () => void,
 *   lassoToMask:             () => void,
 *   runMagicWand:            (x: number, y: number, mode: string, opts?: object) => void,
 *   wandClear:               () => void,
 *   wandDeleteSelection:     () => void,
 *   wandCopyToNewLayer:      () => void,
 *   wandToMask:              () => void,
 *   buildSelectionHintMask:  () => string | null,
 *   applyImageTool:          (endpoint, payload, name, btn, opts?) => Promise<void>,
 *   uiModule:                object,
 * }} deps
 */
import { state } from './state.js';

const EYE_OPEN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF  = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';

export function wireSelectionControls({
  composite,
  invertSelection,
  lassoDeleteSelection, lassoCopyToLayer, lassoToMask,
  runMagicWand,
  wandClear, wandDeleteSelection, wandCopyToNewLayer, wandToMask,
  buildSelectionHintMask, applyImageTool,
  uiModule,
}) {
  // ── Lasso section ──
  const lassoFPrev = document.getElementById('ge-lasso-feather-preview');
  function syncLassoFeather(v) {
    if (!lassoFPrev) return;
    const inner = Math.max(0, 50 - v * 1.0);
    lassoFPrev.style.background = `radial-gradient(circle, var(--fg) 0%, var(--fg) ${inner}%, transparent 75%)`;
  }
  syncLassoFeather(0);
  document.getElementById('ge-lasso-feather')?.addEventListener('input', (e) => {
    document.getElementById('ge-lasso-feather-label').textContent = e.target.value + 'px';
    syncLassoFeather(parseInt(e.target.value, 10));
    composite();
  });
  document.getElementById('ge-lasso-grow')?.addEventListener('input', (e) => {
    document.getElementById('ge-lasso-grow-label').textContent = e.target.value + 'px';
    composite();
  });
  document.getElementById('ge-lasso-delete')?.addEventListener('click', () => {
    if (state.lassoPoints.length >= 3 && !state.lassoActive) lassoDeleteSelection();
    else if (state.wandMask) wandDeleteSelection();
  });
  document.getElementById('ge-lasso-copy')?.addEventListener('click', () => {
    if (state.lassoPoints.length >= 3 && !state.lassoActive) lassoCopyToLayer();
    else if (state.wandMask) wandCopyToNewLayer();
  });
  document.getElementById('ge-lasso-mask')?.addEventListener('click', () => {
    if (state.lassoPoints.length >= 3 && !state.lassoActive) lassoToMask();
    else if (state.wandMask) wandToMask();
  });
  document.getElementById('ge-lasso-invert')?.addEventListener('click', invertSelection);

  // ── Wand section ──
  document.getElementById('ge-wand-feather')?.addEventListener('input', (e) => {
    const v = parseInt(e.target.value, 10) || 0;
    document.getElementById('ge-wand-feather-label').textContent = v + 'px';
    const prev = document.getElementById('ge-wand-feather-preview');
    if (prev) prev.style.setProperty('--feather-blur', Math.min(v / 14, 8) + 'px');
    composite();
  });
  document.getElementById('ge-wand-grow')?.addEventListener('input', (e) => {
    document.getElementById('ge-wand-grow-label').textContent = e.target.value + 'px';
    composite();
  });

  // Tolerance slider fires `input` rapidly — coalesce to one wand run
  // per frame with rAF. Label updates synchronously so the number
  // tracks the cursor even when the flood-fill runs at ~60fps.
  let wandRetuneRaf = null;
  const retuneWand = () => {
    if (!state.wandLastSeed || !state.wandMask) return;
    if (wandRetuneRaf) return;
    wandRetuneRaf = requestAnimationFrame(() => {
      wandRetuneRaf = null;
      runMagicWand(state.wandLastSeed.x, state.wandLastSeed.y, 'replace', { retune: true });
    });
  };
  const liveBtn = document.getElementById('ge-wand-live');
  liveBtn?.addEventListener('click', () => {
    state.wandLiveRetune = !state.wandLiveRetune;
    liveBtn.classList.toggle('active', state.wandLiveRetune);
    liveBtn.setAttribute('aria-pressed', state.wandLiveRetune ? 'true' : 'false');
    if (state.wandLiveRetune) retuneWand();
  });
  document.getElementById('ge-wand-tolerance')?.addEventListener('input', (e) => {
    state.wandTolerance = parseInt(e.target.value, 10);
    const lbl = document.getElementById('ge-wand-tol-label');
    if (lbl) lbl.textContent = state.wandTolerance;
    const wp = document.getElementById('ge-wand-tol-preview');
    if (wp) wp.style.opacity = (state.wandTolerance / 100).toFixed(2);
    if (state.wandLiveRetune) retuneWand();
  });

  // Wand mode segmented toggle (New / Add / Subtract).
  document.querySelectorAll('.ge-wand-mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.wandMode;
      if (!mode) return;
      state.wandMode = mode;
      document.querySelectorAll('.ge-wand-mode-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.wandMode === mode);
      });
    });
  });

  // Toggle the translucent red overlay for the wand selection.
  document.getElementById('ge-wand-vis')?.addEventListener('click', () => {
    state.wandMaskVisible = !state.wandMaskVisible;
    const btn = document.getElementById('ge-wand-vis');
    if (btn) {
      btn.innerHTML = state.wandMaskVisible ? EYE_OPEN : EYE_OFF;
      btn.title = state.wandMaskVisible ? 'Hide selection overlay' : 'Show selection overlay';
      btn.classList.toggle('visible', state.wandMaskVisible);
    }
    composite();
  });

  document.getElementById('ge-wand-clear')?.addEventListener('click', wandClear);
  document.getElementById('ge-wand-invert')?.addEventListener('click', invertSelection);
  document.getElementById('ge-wand-delete')?.addEventListener('click', wandDeleteSelection);
  document.getElementById('ge-wand-copy')?.addEventListener('click', wandCopyToNewLayer);
  document.getElementById('ge-wand-mask')?.addEventListener('click', wandToMask);
  // Selection-constrained Bg Remove — reuses the same path the toolbar
  // Bg Remove button does. buildSelectionHintMask picks the active
  // wand/lasso selection, so this just kicks off the existing flow.
  document.getElementById('ge-wand-rembg')?.addEventListener('click', async () => {
    const btn = document.getElementById('ge-wand-rembg');
    const hint = buildSelectionHintMask();
    if (!hint) { if (uiModule) uiModule.showToast('Click to make a wand selection first'); return; }
    const rawStrength = parseInt(document.getElementById('ge-rembg-strength')?.value || '70', 10);
    const pct = Number.isFinite(rawStrength) ? Math.max(10, Math.min(100, rawStrength)) : 70;
    await applyImageTool('/api/image/remove-bg', { hint_mask: hint, strength: +(pct / 100).toFixed(2) }, 'BG Removed', btn);
    wandClear();
  });

  // Live tolerance preview (just opacity-tracking like sharpen).
  const wandTolPrev = document.getElementById('ge-wand-tol-preview');
  if (wandTolPrev) wandTolPrev.style.opacity = (state.wandTolerance / 100).toFixed(2);
}
