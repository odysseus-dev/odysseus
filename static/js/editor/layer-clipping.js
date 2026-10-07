/** Alpha clipping for flat layer stacks and isolated groups. */

function scopeByLayer(editorState) {
  const scopes = new Map();
  for (const group of editorState.layerGroups || []) {
    for (const id of group.layerIds || []) scopes.set(id, group.id);
  }
  return scopes;
}

export function clippingBaseForLayer(editorState, layerId) {
  const layers = editorState.layers || [];
  const index = layers.findIndex(layer => layer.id === layerId);
  if (index < 0 || !layers[index]?.clipped || index === 0) return null;
  const scopes = scopeByLayer(editorState);
  const scope = scopes.get(layerId) || null;
  if ((scopes.get(layers[index - 1].id) || null) !== scope) return null;
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const candidate = layers[cursor];
    if ((scopes.get(candidate.id) || null) !== scope) return null;
    if (candidate.kind === 'adjustment') return null;
    if (!candidate.clipped) return candidate;
  }
  return null;
}

export function canToggleLayerClipping(editorState, layerId) {
  const layer = (editorState.layers || []).find(item => item.id === layerId);
  if (!layer) return false;
  if (layer.clipped) return true;
  layer.clipped = true;
  const canClip = !!clippingBaseForLayer(editorState, layerId);
  layer.clipped = false;
  return canClip;
}

/** Clear clipping flags made invalid by delete, grouping, or reorder. */
export function normalizeLayerClipping(editorState) {
  const cleared = [];
  for (const layer of editorState.layers || []) {
    layer.clipped = !!layer.clipped;
    if (layer.clipped && !clippingBaseForLayer(editorState, layer.id)) {
      layer.clipped = false;
      cleared.push(layer.id);
    }
  }
  return cleared;
}

function ensureScratchCanvas(editorState) {
  const canvas = editorState.clippingCompositeCanvas || document.createElement('canvas');
  editorState.clippingCompositeCanvas = canvas;
  if (canvas.width !== editorState.imgWidth) canvas.width = editorState.imgWidth;
  if (canvas.height !== editorState.imgHeight) canvas.height = editorState.imgHeight;
  return canvas;
}

/** Draw one contiguous stack scope, bottom to top. */
export function drawLayerStack(target, editorState, layers, renderLayer, renderAdjustment = null) {
  let base = null;
  const drawNormal = layer => {
    if (!layer.visible) return;
    const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    target.globalAlpha = layer.opacity;
    target.globalCompositeOperation = layer.blendMode || 'source-over';
    target.drawImage(renderLayer(layer), offset.x, offset.y);
  };
  for (const layer of layers) {
    if (layer.kind === 'adjustment' && renderAdjustment) {
      renderAdjustment(target, layer, layer.clipped ? base : null);
      if (!layer.clipped) base = null;
      continue;
    }
    if (!layer.clipped || !base) {
      base = layer;
      drawNormal(layer);
      continue;
    }
    if (!layer.visible || !base.visible) continue;
    const scratch = ensureScratchCanvas(editorState);
    const scratchCtx = scratch.getContext('2d');
    scratchCtx.globalAlpha = 1;
    scratchCtx.globalCompositeOperation = 'source-over';
    scratchCtx.clearRect(0, 0, scratch.width, scratch.height);
    const layerOffset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    scratchCtx.drawImage(renderLayer(layer), layerOffset.x, layerOffset.y);
    scratchCtx.globalCompositeOperation = 'destination-in';
    const baseOffset = editorState.layerOffsets.get(base.id) || { x: 0, y: 0 };
    scratchCtx.drawImage(renderLayer(base), baseOffset.x, baseOffset.y);
    scratchCtx.globalCompositeOperation = 'source-over';
    target.globalAlpha = layer.opacity;
    target.globalCompositeOperation = layer.blendMode || 'source-over';
    target.drawImage(scratch, 0, 0);
  }
  target.globalAlpha = 1;
  target.globalCompositeOperation = 'source-over';
}

/** Async counterpart used by the worker-backed live effect compositor. */
export async function drawLayerStackAsync(target, editorState, layers, renderLayer, renderAdjustment = null, shouldContinue = () => true) {
  let base = null;
  const drawNormal = async layer => {
    if (!layer.visible || !shouldContinue()) return;
    const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    target.globalAlpha = layer.opacity;
    target.globalCompositeOperation = layer.blendMode || 'source-over';
    const rendered = await renderLayer(layer);
    if (!shouldContinue() || !rendered) return;
    target.drawImage(rendered, offset.x, offset.y);
  };
  for (const layer of layers) {
    if (!shouldContinue()) return;
    if (layer.kind === 'adjustment' && renderAdjustment) {
      await renderAdjustment(target, layer, layer.clipped ? base : null);
      if (!layer.clipped) base = null;
      continue;
    }
    if (!layer.clipped || !base) {
      base = layer;
      await drawNormal(layer);
      continue;
    }
    if (!layer.visible || !base.visible) continue;
    const scratch = ensureScratchCanvas(editorState);
    const scratchCtx = scratch.getContext('2d');
    scratchCtx.globalAlpha = 1;
    scratchCtx.globalCompositeOperation = 'source-over';
    scratchCtx.clearRect(0, 0, scratch.width, scratch.height);
    const layerOffset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const renderedLayer = await renderLayer(layer);
    const renderedBase = await renderLayer(base);
    if (!shouldContinue() || !renderedLayer || !renderedBase) return;
    scratchCtx.drawImage(renderedLayer, layerOffset.x, layerOffset.y);
    scratchCtx.globalCompositeOperation = 'destination-in';
    const baseOffset = editorState.layerOffsets.get(base.id) || { x: 0, y: 0 };
    scratchCtx.drawImage(renderedBase, baseOffset.x, baseOffset.y);
    scratchCtx.globalCompositeOperation = 'source-over';
    target.globalAlpha = layer.opacity;
    target.globalCompositeOperation = layer.blendMode || 'source-over';
    target.drawImage(scratch, 0, 0);
  }
  target.globalAlpha = 1;
  target.globalCompositeOperation = 'source-over';
}
