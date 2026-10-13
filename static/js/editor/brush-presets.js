const STORAGE_KEY = 'odysseus.editor.brush-presets.v1';

const BUILT_INS = [
  { id: 'soft-round', name: 'Soft Round', size: 80, opacity: 100, flow: 35, softness: 100, spacing: 12, smoothing: 25, blendMode: 'source-over' },
  { id: 'hard-round', name: 'Hard Round', size: 24, opacity: 100, flow: 100, softness: 0, spacing: 18, smoothing: 15, blendMode: 'source-over' },
  { id: 'detail', name: 'Detail', size: 6, opacity: 100, flow: 70, softness: 20, spacing: 10, smoothing: 55, blendMode: 'source-over' },
];

export function loadBrushPresets(storage = globalThis.localStorage) {
  let custom = [];
  try {
    const parsed = JSON.parse(storage?.getItem(STORAGE_KEY) || '[]');
    if (Array.isArray(parsed)) custom = parsed.filter(item => item && typeof item.name === 'string');
  } catch {}
  return [...BUILT_INS.map(item => ({ ...item, builtIn: true })), ...custom];
}

export function saveCustomBrushPresets(presets, storage = globalThis.localStorage) {
  const custom = (presets || []).filter(item => item && !item.builtIn);
  storage?.setItem(STORAGE_KEY, JSON.stringify(custom));
}

export function captureBrushPreset(state, name) {
  return {
    id: `custom-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    name: String(name || 'Brush').trim() || 'Brush',
    size: state.brushSize,
    opacity: state.brushOpacity,
    flow: state.brushFlow,
    softness: state.brushSoftness,
    spacing: state.brushSpacing,
    smoothing: state.brushSmoothing,
    blendMode: state.brushBlendMode,
  };
}

export function applyBrushPreset(state, preset) {
  if (!preset) return;
  state.brushSize = preset.size;
  state.brushOpacity = preset.opacity;
  state.brushFlow = preset.flow;
  state.brushSoftness = preset.softness;
  state.brushSpacing = preset.spacing;
  state.brushSmoothing = preset.smoothing;
  state.brushBlendMode = preset.blendMode || 'source-over';
}
