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
      const innerStop = Math.max(0, 60 - state[fields.softness] * 0.55);
      softPrev.style.background = `radial-gradient(circle, var(--fg) 0%, var(--fg) ${innerStop}%, transparent 90%)`;
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

  const spacing = document.getElementById('ge-brush-spacing');
  const smoothing = document.getElementById('ge-brush-smoothing');
  const blend = document.getElementById('ge-brush-blend');
  spacing?.addEventListener('input', (e) => {
    state.brushSpacing = parseInt(e.target.value, 10);
    document.getElementById('ge-brush-spacing-label').textContent = `${state.brushSpacing}%`;
  });
  smoothing?.addEventListener('input', (e) => {
    state.brushSmoothing = parseInt(e.target.value, 10);
    document.getElementById('ge-brush-smoothing-label').textContent = `${state.brushSmoothing}%`;
  });
  blend?.addEventListener('change', (e) => { state.brushBlendMode = e.target.value; });
  document.getElementById('ge-pressure-size')?.addEventListener('change', e => { state.pressureSize = e.target.checked; });
  document.getElementById('ge-pressure-opacity')?.addEventListener('change', e => { state.pressureOpacity = e.target.checked; });
  document.getElementById('ge-pressure-flow')?.addEventListener('change', e => { state.pressureFlow = e.target.checked; });
  document.getElementById('ge-eyedropper-sample')?.addEventListener('change', e => { state.eyedropperSample = e.target.value; });

  let presets = loadBrushPresets();
  const presetSelect = document.getElementById('ge-brush-preset');
  const renderPresets = (selectedId = '') => {
    if (!presetSelect) return;
    presetSelect.innerHTML = '<option value="">Brush presets</option>' + presets.map(preset =>
      `<option value="${preset.id}">${preset.name}</option>`).join('');
    presetSelect.value = selectedId;
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
    onPresetApplied?.();
  };
  renderPresets();
  presetSelect?.addEventListener('change', () => {
    const preset = presets.find(item => item.id === presetSelect.value);
    if (!preset) return;
    applyBrushPreset(state, preset);
    syncControls();
  });
  document.getElementById('ge-brush-preset-save')?.addEventListener('click', () => {
    const name = window.prompt('Brush preset name');
    if (!name?.trim()) return;
    const preset = captureBrushPreset(state, name);
    presets.push(preset);
    saveCustomBrushPresets(presets);
    renderPresets(preset.id);
  });
  document.getElementById('ge-brush-preset-delete')?.addEventListener('click', () => {
    const preset = presets.find(item => item.id === presetSelect?.value);
    if (!preset || preset.builtIn) return;
    presets = presets.filter(item => item.id !== preset.id);
    saveCustomBrushPresets(presets);
    renderPresets();
  });
}
