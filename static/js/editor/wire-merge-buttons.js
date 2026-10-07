/**
 * Layer merge / flatten buttons in the layer-panel footer:
 *
 *   #ge-flatten     Flatten Copy — merge every visible layer into a
 *                   new "Flattened" layer, keep originals.
 *   #ge-merge-all   Merge All — flatten every VISIBLE layer into the
 *                   lowest visible one. Hidden layers dropped. Base
 *                   = lowest visible (not bottom of stack) so a
 *                   hidden base can't absorb the visible stack into
 *                   an invisible result.
 *   #ge-merge-down  Merge active layer into the one beneath it.
 *
 * @param {{
 *   saveState:        (label?: string) => void,
 *   createLayer:      (name, w, h) => object,
 *   renderLayerPanel: () => void,
 *   composite:        () => void,
 *   renderLayer?:     (layer) => HTMLCanvasElement,
 *   flatten?:         () => HTMLCanvasElement,
 *   uiModule:         object,
 * }} deps
 */
import { state } from './state.js';
import { rasterizeTextLayer } from './text-layer.js';
import { rasterizeShapeLayer } from './shape-layer.js';
import { drawLayerStack, normalizeLayerClipping } from './layer-clipping.js';
import { groupForLayer, normalizeLayerGroups } from './layer-groups.js';

function _renderSource(layer, renderLayer) {
  if (!layer) return null;
  try {
    return typeof renderLayer === 'function' ? (renderLayer(layer) || layer.canvas) : layer.canvas;
  } catch {
    return layer.canvas;
  }
}

function _markBakedRaster(layer) {
  if (!layer) return;
  rasterizeTextLayer(layer);
  rasterizeShapeLayer(layer);
  layer.adjLayers = [];
  layer._adjFinal = null;
  layer._adjFinalKey = '';
  layer._adjCache = null;
  layer._adjCacheKey = '';
  layer.masks = [];
  layer.activeMaskId = null;
  layer.clipped = false;
}

export function mergeLayerDownAtIndex(idx, renderLayer = null) {
  if (idx < 1 || idx >= state.layers.length) return null;
  const upper = state.layers[idx];
  const lower = state.layers[idx - 1];
  if (lower.clipped || groupForLayer(state, upper.id)?.id !== groupForLayer(state, lower.id)?.id) return null;
  const merged = document.createElement('canvas');
  merged.width = state.imgWidth;
  merged.height = state.imgHeight;
  const mctx = merged.getContext('2d');
  drawLayerStack(mctx, state, [lower, upper], layer => _renderSource(layer, renderLayer));
  lower.canvas = merged;
  lower.ctx = lower.canvas.getContext('2d');
  lower.opacity = 1;
  lower.visible = true;
  lower.blendMode = 'source-over';
  state.layerOffsets.set(lower.id, { x: 0, y: 0 });
  _markBakedRaster(lower);
  state.layers.splice(idx, 1);
  state.layerOffsets.delete(upper.id);
  for (const group of state.layerGroups || []) group.layerIds = group.layerIds.filter(id => id !== upper.id);
  normalizeLayerGroups(state);
  normalizeLayerClipping(state);
  state.activeLayerId = lower.id;
  return lower;
}

export function wireMergeButtons({ saveState, createLayer, renderLayerPanel, composite, renderLayer, flatten, uiModule }) {
  // Flatten Copy.
  document.getElementById('ge-flatten')?.addEventListener('click', () => {
    if (state.layers.length < 2) return;
    saveState('Flatten copy');
    const merged = createLayer('Flattened', state.imgWidth, state.imgHeight);
    if (typeof flatten === 'function') merged.ctx.drawImage(flatten(), 0, 0);
    else drawLayerStack(merged.ctx, state, state.layers.filter(layer => layer.visible), layer => _renderSource(layer, renderLayer));
    _markBakedRaster(merged);
    state.layers.push(merged);
    state.activeLayerId = merged.id;
    renderLayerPanel();
    composite();
    uiModule.showToast('Flattened copy created');
  });

  // Merge All — drop hidden layers; base = lowest visible.
  document.getElementById('ge-merge-all')?.addEventListener('click', () => {
    const visibleLayers = state.layers.filter(l => l.visible);
    if (visibleLayers.length < 2) {
      if (uiModule) uiModule.showToast('Need at least two visible layers to merge');
      return;
    }
    saveState('Merge all');
    const base = visibleLayers[0];
    const merged = typeof flatten === 'function' ? flatten() : document.createElement('canvas');
    if (typeof flatten !== 'function') {
      merged.width = state.imgWidth;
      merged.height = state.imgHeight;
      drawLayerStack(merged.getContext('2d'), state, visibleLayers, layer => _renderSource(layer, renderLayer));
    }
    base.canvas = merged;
    base.ctx = base.canvas.getContext('2d');
    base.opacity = 1;
    base.visible = true;
    base.blendMode = 'source-over';
    state.layerOffsets.set(base.id, { x: 0, y: 0 });
    _markBakedRaster(base);
    // Free offset entries for the discarded layers; keep base.
    for (const l of state.layers) {
      if (l === base) continue;
      state.layerOffsets.delete(l.id);
    }
    state.layers = [base];
    state.layerGroups = [];
    state.activeLayerId = base.id;
    renderLayerPanel();
    composite();
    uiModule.showToast('Visible layers merged');
  });

  // Merge Down.
  document.getElementById('ge-merge-down')?.addEventListener('click', () => {
    const idx = state.layers.findIndex(l => l.id === state.activeLayerId);
    if (idx < 1) return; // can't merge the bottom layer
    const upper = state.layers[idx];
    const lower = state.layers[idx - 1];
    if (lower.clipped || groupForLayer(state, upper.id)?.id !== groupForLayer(state, lower.id)?.id) {
      uiModule?.showToast?.('Release the lower clipping mask or keep both layers in the same group before merging');
      return;
    }
    saveState('Merge down');
    const merged = mergeLayerDownAtIndex(idx, renderLayer);
    if (!merged) return;
    renderLayerPanel();
    composite();
    uiModule.showToast('Layer merged down');
  });
}
