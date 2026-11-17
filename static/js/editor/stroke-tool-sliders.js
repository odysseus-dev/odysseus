/**
 * Per-tool stroke-modifier sliders (Opacity / Flow / Softness) for
 * Eraser, Brush, and Clone. The three sections share identical UX:
 *
 *   - Opacity slider: writes to state, updates label, fades the
 *     preview swatch opacity.
 *   - Flow slider: writes to state, updates label, fades the swatch
 *     opacity AND swaps its border style (dashed at low flow → dotted
 *     at high flow) so the user sees the "denseness" change.
 *   - Softness slider: writes to state, updates label, tweens the
 *     radial-gradient inner stop on the swatch so it visually fades
 *     from hard disk to soft falloff.
 *
 * The whole block was three near-identical 30-LOC copies before; now
 * it's one helper that takes the tool's prefix + a state-field bag.
 *
 * Usage: just call wireStrokeToolSliders() — the DOM IDs are wired
 * statically from #ge-{eraser,brush,clone}-{opacity,flow,softness}
 * + their labels + preview swatches.
 */
import { state } from './state.js';
import {
  applyBrushPreset,
  captureBrushPreset,
  loadBrushPresets,
  saveCustomBrushPresets,
} from './brush-presets.js';

/** Wire the three sliders for one stroke tool. */
function wireToolSliders(prefix, fields) {
  const opPrev   = document.getElementById(`ge-${prefix}-preview-opacity`);
  const flPrev   = document.getElementById(`ge-${prefix}-preview-flow`);
  const softPrev = document.getElementById(`ge-${prefix}-preview-softness`);

  document.getElementById(`ge-${prefix}-opacity`)?.addEventListener('input', (e) => {
    state[fields.opacity] = parseInt(e.target.value);
    document.getElementById(`ge-${prefix}-opacity-label`).textContent = state[fields.opacity] + '%';
    if (opPrev) opPrev.style.opacity = (state[fields.opacity] / 100).toFixed(2);
  });

  document.getElementById(`ge-${prefix}-flow`)?.addEventListener('input', (e) => {
    state[fields.flow] = parseInt(e.target.value);
    document.getElementById(`ge-${prefix}-flow-label`).textContent = state[fields.flow] + '%';
    // Lower flow → fewer / sparser dots. Cycle dot densities by
    // swapping the dashed/dotted border style and fading opacity.
    if (flPrev) {
      const denseness = Math.max(1, Math.round(state[fields.flow] / 20));
      flPrev.style.borderStyle = denseness <= 2 ? 'dashed' : 'dotted';
      flPrev.style.opacity = (0.3 + (state[fields.flow] / 100) * 0.6).toFixed(2);
    }
  });

  document.getElementById(`ge-${prefix}-softness`)?.addEventListener('input', (e) => {
    state[fields.softness] = parseInt(e.target.value);
    document.getElementById(`ge-${prefix}-softness-label`).textContent = state[fields.softness] + '%';
    // Preview tweens from a hard disk into a soft radial gradient as
    // softness rises (the CSS already sets the radial gradient — we
    // just tween the inner solid radius to communicate the falloff).
    if (softPrev) {
      const amount = state[fields.softness];
      const innerStop = Math.max(0, 60 - amount * 0.55);
      softPrev.style.background = amount > 300
        ? `radial-gradient(circle, var(--fg), color-mix(in srgb, var(--fg) ${Math.round(100 * 0.5 ** (amount / 300))}%, transparent) 50%, transparent 100%)`
        : `radial-gradient(circle, var(--fg) 0%, var(--fg) ${innerStop}%, transparent 90%)`;
    }
  });
}

export function wireStrokeToolSliders({ onPresetApplied } = {}) {
  wireToolSliders('eraser', { opacity: 'eraserOpacity', flow: 'eraserFlow', softness: 'eraserSoftness' });
  wireToolSliders('brush',  { opacity: 'brushOpacity',  flow: 'brushFlow',  softness: 'brushSoftness'  });
  wireToolSliders('clone',  { opacity: 'cloneOpacity',  flow: 'cloneFlow',  softness: 'cloneSoftness'  });

  const smudgeStrength = document.getElementById('ge-smudge-strength');
  smudgeStrength?.addEventListener('input', (e) => {
    state.smudgeStrength = parseInt(e.target.value, 10);
    document.getElementById('ge-smudge-strength-label').textContent = `${state.smudgeStrength}%`;
    const preview = document.getElementById('ge-smudge-preview-strength');
    if (preview) preview.style.opacity = (state.smudgeStrength / 100).toFixed(2);
  });

  const dodgeBurnIntensity = document.getElementById('ge-dodgeburn-intensity');
  dodgeBurnIntensity?.addEventListener('input', (e) => {
    state.dodgeBurnIntensity = parseInt(e.target.value, 10);
    document.getElementById('ge-dodgeburn-intensity-label').textContent = `${state.dodgeBurnIntensity}%`;
    const preview = document.getElementById('ge-dodgeburn-preview');
    if (preview) preview.style.opacity = (0.2 + state.dodgeBurnIntensity / 125).toFixed(2);
  });

  const spacing = document.getElementById('ge-brush-spacing');
  const smoothing = document.getElementById('ge-brush-smoothing');
  const blend = document.getElementById('ge-brush-blend');
  const spacingPrev = document.getElementById('ge-brush-preview-spacing');
  const smoothingPrev = document.getElementById('ge-brush-preview-smoothing');
  // Spacing preview: split the single dot into two dots whose gap grows with
  // the spacing value, so the swatch reads as "dots spaced apart".
  const paintSpacingPreview = () => {
    if (!spacingPrev) return;
    const gap = Math.min(9, 1 + state.brushSpacing * 0.08);
    const c1 = (7 - gap / 2).toFixed(2);
    const c2 = (7 + gap / 2).toFixed(2);
    spacingPrev.style.opacity = '';
    spacingPrev.style.background = `radial-gradient(circle 3.4px at ${c1}px 50%, var(--fg) 98%, transparent 100%), radial-gradient(circle 3.4px at ${c2}px 50%, var(--fg) 98%, transparent 100%)`;
  };
  spacing?.addEventListener('input', (e) => {
    state.brushSpacing = parseInt(e.target.value, 10);
    document.getElementById('ge-brush-spacing-label').textContent = `${state.brushSpacing}%`;
    paintSpacingPreview();
  });
  smoothing?.addEventListener('input', (e) => {
    state.brushSmoothing = parseInt(e.target.value, 10);
    document.getElementById('ge-brush-smoothing-label').textContent = `${state.brushSmoothing}%`;
    // More smoothing → softer edge; widen the radial falloff.
    if (smoothingPrev) {
      const innerStop = Math.max(0, 70 - state.brushSmoothing * 0.7);
      smoothingPrev.style.background = `radial-gradient(circle, var(--fg) 0%, var(--fg) ${innerStop}%, transparent 90%)`;
    }
  });
  blend?.addEventListener('change', (e) => { state.brushBlendMode = e.target.value; });
  document.getElementById('ge-pressure-size')?.addEventListener('change', e => { state.pressureSize = e.target.checked; });
  document.getElementById('ge-pressure-opacity')?.addEventListener('change', e => { state.pressureOpacity = e.target.checked; });
  document.getElementById('ge-pressure-flow')?.addEventListener('change', e => { state.pressureFlow = e.target.checked; });
  document.getElementById('ge-eyedropper-sample')?.addEventListener('change', e => { state.eyedropperSample = e.target.value; });
  document.getElementById('ge-eyedropper-sample-size')?.addEventListener('change', e => {
    const size = Math.max(1, Math.round(Number(e.target.value) || 1));
    state.eyedropperSampleSize = size;
  });

  let presets = loadBrushPresets();
  const presetSelect = document.getElementById('ge-brush-preset');
  const presetSaveBtn = document.getElementById('ge-brush-preset-save');
  const presetDeleteBtn = document.getElementById('ge-brush-preset-delete');
  // Save and Delete only apply to custom brushes: they are hidden while a
  // fixed built-in preset is selected. Save is available on the unsaved
  // "Custom" slot (to create one) and on a saved custom (to save over it);
  // Delete is available only for a saved (non built-in) custom preset.
  const syncPresetButtons = () => {
    const id = presetSelect?.value || '';
    const preset = presets.find(item => item.id === id);
    const isBuiltIn = !!preset && !!preset.builtIn;
    const isUserCustom = !!preset && !preset.builtIn;
    if (presetSaveBtn) presetSaveBtn.hidden = isBuiltIn;
    if (presetDeleteBtn) presetDeleteBtn.hidden = !isUserCustom;
  };
  const renderPresets = (selectedId = '') => {
    if (!presetSelect) return;
    // Pixel is its own tool, so it is not offered as a brush preset here.
    const list = presets.filter(preset => !preset.pixel);
    presetSelect.innerHTML = '<option value="">Custom</option>' + list.map(preset =>
      `<option value="${preset.id}">${preset.name}</option>`).join('');
    presetSelect.value = selectedId;
    syncPresetButtons();
  };
  const syncControls = () => {
    if (spacing) spacing.value = String(state.brushSpacing);
    if (smoothing) smoothing.value = String(state.brushSmoothing);
    if (blend) blend.value = state.brushBlendMode;
    if (smudgeStrength) smudgeStrength.value = String(state.smudgeStrength);
    const smudgeLabel = document.getElementById('ge-smudge-strength-label');
    if (smudgeLabel) smudgeLabel.textContent = `${state.smudgeStrength}%`;
    document.getElementById('ge-brush-spacing-label').textContent = `${state.brushSpacing}%`;
    document.getElementById('ge-brush-smoothing-label').textContent = `${state.brushSmoothing}%`;
    paintSpacingPreview();
    onPresetApplied?.();
  };
  // Restore the last preset the user picked instead of snapping back to the
  // unsaved "Custom" slot on every editor open.
  let savedPresetId = '';
  try { savedPresetId = localStorage.getItem('ge-brush-preset') || ''; } catch (error) { /* storage unavailable */ }
  renderPresets(presets.some(item => item.id === savedPresetId && !item.pixel) ? savedPresetId : '');
  presetSelect?.addEventListener('change', () => {
    try { localStorage.setItem('ge-brush-preset', presetSelect.value || ''); } catch (error) { /* ignore */ }
    const preset = presets.find(item => item.id === presetSelect.value);
    if (!preset) {
      // "Custom" (no preset) — leave the current brush alone but drop pixel
      // mode so the user can get back out of the 1px Pixel preset.
      state.brushPixel = false;
      onPresetApplied?.();
      syncPresetButtons();
      return;
    }
    applyBrushPreset(state, preset);
    syncControls();
    syncPresetButtons();
  });
  document.getElementById('ge-brush-preset-save')?.addEventListener('click', () => {
    const name = window.prompt('Brush preset name');
    if (!name?.trim()) return;
    const preset = captureBrushPreset(state, name);
    presets.push(preset);
    saveCustomBrushPresets(presets);
    try { localStorage.setItem('ge-brush-preset', preset.id); } catch (error) { /* ignore */ }
    renderPresets(preset.id);
  });
  document.getElementById('ge-brush-preset-delete')?.addEventListener('click', () => {
    const preset = presets.find(item => item.id === presetSelect?.value);
    if (!preset || preset.builtIn) return;
    presets = presets.filter(item => item.id !== preset.id);
    saveCustomBrushPresets(presets);
    try { localStorage.setItem('ge-brush-preset', ''); } catch (error) { /* ignore */ }
  renderPresets();
  paintSpacingPreview();
  });

  // The caret ("v") next to the brush row drops down the less-used stroke
  // controls (Spacing / Smoothing / Blend / pen pressure) so the options strip
  // stays a single tidy line. Same pattern as the wand's caret menu.
  const moreBtn = document.getElementById('ge-brush-more');
  const moreMenu = document.getElementById('ge-brush-more-menu');
  const closeBrushMore = () => {
    if (!moreMenu || moreMenu.hidden) return;
    moreMenu.hidden = true;
    moreBtn?.setAttribute('aria-expanded', 'false');
    moreBtn?.classList.remove('active');
  };
  moreBtn?.addEventListener('click', (event) => {
    event.stopPropagation();
    const open = moreMenu.hidden;
    moreMenu.hidden = !open;
    moreBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    moreBtn.classList.toggle('active', open);
  });
  document.addEventListener('click', (event) => {
    if (!moreMenu || moreMenu.hidden) return;
    if (moreMenu.contains(event.target) || moreBtn?.contains(event.target)) return;
    closeBrushMore();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeBrushMore();
  });
}
