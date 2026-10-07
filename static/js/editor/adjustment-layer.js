/** First-class adjustment-layer rendering and retained metadata helpers. */
import { applyAdjustment } from './fx/pixel-pass.js';
import { defaultAdjParams } from './layer-helpers.js';
import { renderWithLayerMasks } from './composite-helpers.js';

const TYPES = new Set([
  'brightness-contrast',
  'exposure',
  'white-balance',
  'hue-saturation',
  'vibrance',
  'black-white',
  'shadows-highlights',
  'levels',
  'curves',
  'color-balance',
  'selective-color',
  'gradient-map',
]);

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function normalizeAdjustmentData(value) {
  const type = TYPES.has(value?.type) ? value.type : 'levels';
  const defaults = defaultAdjParams(type);
  const params = value?.params && typeof value.params === 'object'
    ? { ...clone(defaults), ...clone(value.params) }
    : clone(defaults);
  if (type === 'levels') {
    params.channels = {
      ...clone(defaults.channels),
      ...(params.channels || {}),
    };
    for (const name of Object.keys(defaults.channels)) {
      params.channels[name] = { ...clone(defaults.channels[name]), ...(params.channels[name] || {}) };
    }
  }
  if (type === 'curves') {
    params.points = {
      ...clone(defaults.points),
      ...(params.points || {}),
    };
  }
  if (type === 'selective-color') {
    params.ranges = {
      ...clone(defaults.ranges),
      ...(params.ranges || {}),
    };
    for (const name of Object.keys(defaults.ranges)) {
      params.ranges[name] = { ...clone(defaults.ranges[name]), ...(params.ranges[name] || {}) };
    }
  }
  return { type, params };
}

export function adjustmentSpec(layer) {
  if (layer?._stagedAdj) return normalizeAdjustmentData(layer._stagedAdj);
  return normalizeAdjustmentData(layer?.adjustment);
}

function documentCanvas(width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

/**
 * Replace the already-rendered content under an adjustment layer with its
 * adjusted equivalent. Opacity, blend mode, masks, clipping, and group scope
 * are applied by drawing back into the current stack target.
 */
export function drawAdjustmentLayer(target, editorState, layer, clippingBase, renderLayer) {
  if (!layer?.visible || layer.kind !== 'adjustment' || !target?.canvas) return false;
  // During comparison the underlying stack is already on the target. Leaving
  // it untouched shows the true before state without creating a history entry.
  if (layer._adjCompare) return true;
  const width = editorState.imgWidth;
  const height = editorState.imgHeight;
  // Large live previews use a bounded working surface. The staged layer is
  // marked only while its popup is open; committed rendering stays full-size
  // so export and reopen remain pixel-accurate.
  const maxPreviewEdge = 1400;
  const previewScale = layer._adjPreview
    ? Math.min(1, maxPreviewEdge / Math.max(width, height))
    : 1;
  const sourceWidth = Math.max(1, Math.round(width * previewScale));
  const sourceHeight = Math.max(1, Math.round(height * previewScale));
  const source = documentCanvas(sourceWidth, sourceHeight);
  source.getContext('2d').drawImage(target.canvas, 0, 0, sourceWidth, sourceHeight);
  let adjusted = applyAdjustment(source, adjustmentSpec(layer));
  if (previewScale !== 1) {
    const expanded = documentCanvas(width, height);
    expanded.getContext('2d').drawImage(adjusted, 0, 0, width, height);
    adjusted = expanded;
  }
  adjusted = renderWithLayerMasks(adjusted, layer, { x: 0, y: 0 });

  if (layer.clipped && clippingBase) {
    const clipped = documentCanvas(width, height);
    const clippedCtx = clipped.getContext('2d');
    clippedCtx.drawImage(adjusted, 0, 0);
    clippedCtx.globalCompositeOperation = 'destination-in';
    const offset = editorState.layerOffsets.get(clippingBase.id) || { x: 0, y: 0 };
    clippedCtx.drawImage(renderLayer(clippingBase), offset.x, offset.y);
    clippedCtx.globalCompositeOperation = 'source-over';
    adjusted = clipped;
  }

  target.globalAlpha = Number.isFinite(layer.opacity) ? layer.opacity : 1;
  target.globalCompositeOperation = layer.blendMode || 'source-over';
  target.drawImage(adjusted, 0, 0);
  target.globalAlpha = 1;
  target.globalCompositeOperation = 'source-over';
  return true;
}

/** Async adjustment rendering for live previews and large documents. */
export async function drawAdjustmentLayerAsync(target, editorState, layer, clippingBase, renderLayer, shouldContinue = () => true) {
  if (!layer?.visible || layer.kind !== 'adjustment' || !target?.canvas || !shouldContinue()) return false;
  if (layer._adjCompare) return true;
  const width = editorState.imgWidth;
  const height = editorState.imgHeight;
  const maxPreviewEdge = 1400;
  const previewScale = layer._adjPreview
    ? Math.min(1, maxPreviewEdge / Math.max(width, height))
    : 1;
  const sourceWidth = Math.max(1, Math.round(width * previewScale));
  const sourceHeight = Math.max(1, Math.round(height * previewScale));
  const source = documentCanvas(sourceWidth, sourceHeight);
  source.getContext('2d').drawImage(target.canvas, 0, 0, sourceWidth, sourceHeight);
  let adjusted = await renderAdjustmentAsync(source, adjustmentSpec(layer), shouldContinue);
  if (!shouldContinue() || !adjusted) return false;
  if (previewScale !== 1) {
    if (!shouldContinue()) return false;
    const expanded = documentCanvas(width, height);
    expanded.getContext('2d').drawImage(adjusted, 0, 0, width, height);
    adjusted = expanded;
  }
  if (!shouldContinue()) return false;
  adjusted = renderWithLayerMasks(adjusted, layer, { x: 0, y: 0 });
  if (!shouldContinue()) return false;
  if (layer.clipped && clippingBase) {
    const clipped = documentCanvas(width, height);
    const clippedCtx = clipped.getContext('2d');
    clippedCtx.drawImage(adjusted, 0, 0);
    clippedCtx.globalCompositeOperation = 'destination-in';
    const offset = editorState.layerOffsets.get(clippingBase.id) || { x: 0, y: 0 };
    const baseOutput = await renderLayer(clippingBase);
    if (!shouldContinue() || !baseOutput) return false;
    clippedCtx.drawImage(baseOutput, offset.x, offset.y);
    clippedCtx.globalCompositeOperation = 'source-over';
    adjusted = clipped;
  }
  target.globalAlpha = Number.isFinite(layer.opacity) ? layer.opacity : 1;
  target.globalCompositeOperation = layer.blendMode || 'source-over';
  target.drawImage(adjusted, 0, 0);
  target.globalAlpha = 1;
  target.globalCompositeOperation = 'source-over';
  return true;
}

async function renderAdjustmentAsync(source, adjustment, shouldContinue) {
  if (typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined' || typeof createImageBitmap !== 'function') {
    return applyAdjustment(source, adjustment);
  }
  let sourceBitmap;
  try {
    sourceBitmap = await createImageBitmap(source);
  } catch {
    return applyAdjustment(source, adjustment);
  }
  if (!shouldContinue()) {
    sourceBitmap.close?.();
    return null;
  }
  let worker;
  try {
    worker = new Worker(new URL('./adjustments-worker.js', import.meta.url), { type: 'module' });
  } catch {
    return shouldContinue() ? applyAdjustment(source, adjustment) : null;
  }
  return new Promise(resolve => {
    let settled = false;
    const finish = result => {
      if (settled) return;
      settled = true;
      worker.terminate();
      sourceBitmap.close?.();
      resolve(result);
    };
    worker.onmessage = event => {
      const { bitmap, error } = event.data || {};
      if (error || !bitmap || !shouldContinue()) {
        bitmap?.close?.();
        finish(shouldContinue() ? applyAdjustment(source, adjustment) : null);
        return;
      }
      const output = documentCanvas(source.width, source.height);
      output.getContext('2d').drawImage(bitmap, 0, 0);
      bitmap.close?.();
      finish(output);
    };
    // A stale worker must not trigger a full-resolution fallback. The caller
    // will discard it, and doing the work here makes slider scrubbing worse.
    worker.onerror = () => finish(shouldContinue() ? applyAdjustment(source, adjustment) : null);
    worker.postMessage({ source: sourceBitmap, adjustment }, [sourceBitmap]);
  });
}
