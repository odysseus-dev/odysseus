/**
 * Document-wide geometry operations.
 *
 * Layer pixels live in layer coordinates, while AI/selection masks live in
 * document coordinates. Keeping those spaces explicit here prevents crop and
 * resize commands from updating the visible pixels while leaving masks and
 * selections behind.
 */

import { flipTextLayer, rotateTextLayer, scaleTextLayer } from './text-layer.js';
import { flipShapeLayer, rotateShapeLayer, scaleShapeLayer } from './shape-layer.js';
import { affineBounds, renderPlacedLayer, transformPlacedData } from './placed-layer.js';

function makeCanvas(width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(Number.isFinite(width) ? width : 1));
  canvas.height = Math.max(1, Math.round(Number.isFinite(height) ? height : 1));
  return canvas;
}

function renderWindow(source, x, y, width, height) {
  const out = makeCanvas(width, height);
  // A freshly-created / empty layer can legitimately have a 0-sized canvas;
  // drawImage() throws on those, which would abort cropping the whole document.
  if (source && source.width > 0 && source.height > 0) {
    out.getContext('2d').drawImage(source, -x, -y);
  }
  return out;
}

function renderTranslated(source, width, height, x, y) {
  const out = makeCanvas(width, height);
  out.getContext('2d').drawImage(source, x, y);
  return out;
}

function renderScaled(source, width, height, smoothingQuality = 'high') {
  const out = makeCanvas(width, height);
  const ctx = out.getContext('2d');
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = smoothingQuality;
  ctx.drawImage(source, 0, 0, out.width, out.height);
  return out;
}

function renderRotated(source, degrees) {
  const normalized = ((degrees % 360) + 360) % 360;
  const swap = normalized === 90 || normalized === 270;
  const out = makeCanvas(swap ? source.height : source.width, swap ? source.width : source.height);
  const ctx = out.getContext('2d');
  ctx.translate(out.width / 2, out.height / 2);
  ctx.rotate((normalized * Math.PI) / 180);
  ctx.drawImage(source, -source.width / 2, -source.height / 2);
  return out;
}

function renderRotatedArbitrary(source, degrees) {
  const radians = (degrees * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const width = Math.abs(source.width * cos) + Math.abs(source.height * sin);
  const height = Math.abs(source.width * sin) + Math.abs(source.height * cos);
  const out = makeCanvas(Math.ceil(width), Math.ceil(height));
  // Empty layers can have a 0-sized canvas; drawImage() throws on those.
  if (source && source.width > 0 && source.height > 0) {
    const ctx = out.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.translate(out.width / 2, out.height / 2);
    ctx.rotate(radians);
    ctx.drawImage(source, -source.width / 2, -source.height / 2);
  }
  return out;
}

function renderFlipped(source, axis) {
  const out = makeCanvas(source.width, source.height);
  const ctx = out.getContext('2d');
  ctx.save();
  if (axis === 'h') {
    ctx.translate(out.width, 0);
    ctx.scale(-1, 1);
  } else {
    ctx.translate(0, out.height);
    ctx.scale(1, -1);
  }
  ctx.drawImage(source, 0, 0);
  ctx.restore();
  return out;
}

function replaceCanvas(holder, rendered) {
  holder.canvas.width = rendered.width;
  holder.canvas.height = rendered.height;
  holder.ctx = holder.canvas.getContext('2d');
  holder.ctx.clearRect(0, 0, rendered.width, rendered.height);
  holder.ctx.drawImage(rendered, 0, 0);
}

function replaceStandaloneCanvas(canvas, rendered) {
  canvas.width = rendered.width;
  canvas.height = rendered.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, rendered.width, rendered.height);
  ctx.drawImage(rendered, 0, 0);
}

function invalidateLayerCaches(layer) {
  layer._adjCache = null;
  layer._adjCacheKey = null;
  layer._adjFinal = null;
  layer._adjFinalKey = null;
  layer._stagedAdj = null;
  layer._editingAdjId = null;
}

function syncActiveMask(editorState) {
  const activeGroup = (editorState.layerGroups || []).find(group => group.id === editorState.activeGroupId);
  const groupMask = activeGroup?.masks?.find(item => item.id === activeGroup.activeMaskId) || null;
  if (groupMask) {
    editorState.maskCanvas = groupMask.canvas;
    editorState.maskCtx = groupMask.ctx;
    return;
  }
  const active = editorState.layers.find(layer => layer.id === editorState.activeLayerId);
  const mask = active?.masks?.find(item => item.id === active.activeMaskId) || null;
  editorState.maskCanvas = mask?.canvas || null;
  editorState.maskCtx = mask?.ctx || null;
}

function syncDocumentCanvas(editorState, width, height) {
  editorState.imgWidth = width;
  editorState.imgHeight = height;
  if (editorState.mainCanvas) {
    editorState.mainCanvas.width = width;
    editorState.mainCanvas.height = height;
    editorState.mainCtx = editorState.mainCanvas.getContext('2d');
  }
  editorState.documentCompositeCanvas = null;
  editorState.compositeMaskUnion = null;
  editorState.wandSrcCache = null;
  syncActiveMask(editorState);
}

function normalizedGuideValues(values, limit, mapper = value => value) {
  const output = [];
  for (const raw of Array.isArray(values) ? values : []) {
    const value = mapper(Number(raw));
    if (!Number.isFinite(value) || value < 0 || value > limit) continue;
    if (!output.some(existing => Math.abs(existing - value) < 0.0001)) output.push(value);
  }
  return output.sort((a, b) => a - b);
}

function transformGuides(editorState, vertical, horizontal) {
  editorState.guides = {
    vertical: normalizedGuideValues(vertical.values, vertical.limit, vertical.map),
    horizontal: normalizedGuideValues(horizontal.values, horizontal.limit, horizontal.map),
  };
}

function clipPolygonAxis(points, inside, intersect) {
  if (!points.length) return [];
  const output = [];
  let previous = points[points.length - 1];
  let previousInside = inside(previous);
  for (const current of points) {
    const currentInside = inside(current);
    if (currentInside !== previousInside) output.push(intersect(previous, current));
    if (currentInside) output.push(current);
    previous = current;
    previousInside = currentInside;
  }
  return output;
}

/** Clip a polygon to a document rectangle using Sutherland-Hodgman. */
export function clipPolygonToRect(points, width, height) {
  let out = (points || []).map(point => ({ x: point.x, y: point.y }));
  const xAt = (value) => (a, b) => {
    const dx = b.x - a.x;
    const t = dx === 0 ? 0 : (value - a.x) / dx;
    return { x: value, y: a.y + (b.y - a.y) * t };
  };
  const yAt = (value) => (a, b) => {
    const dy = b.y - a.y;
    const t = dy === 0 ? 0 : (value - a.y) / dy;
    return { x: a.x + (b.x - a.x) * t, y: value };
  };
  out = clipPolygonAxis(out, p => p.x >= 0, xAt(0));
  out = clipPolygonAxis(out, p => p.x <= width, xAt(width));
  out = clipPolygonAxis(out, p => p.y >= 0, yAt(0));
  out = clipPolygonAxis(out, p => p.y <= height, yAt(height));
  return out.length >= 3 ? out : [];
}

function normalizeCropRect(editorState, rect) {
  // Accept either {w,h} (internal) or {width,height} (control-op params).
  const rawW = Number.isFinite(rect.w) ? rect.w : rect.width;
  const rawH = Number.isFinite(rect.h) ? rect.h : rect.height;
  const x = Math.max(0, Math.min(editorState.imgWidth - 1, Math.round(rect.x)));
  const y = Math.max(0, Math.min(editorState.imgHeight - 1, Math.round(rect.y)));
  const width = Math.max(1, Math.min(editorState.imgWidth - x, Math.round(Number.isFinite(rawW) ? rawW : editorState.imgWidth)));
  const height = Math.max(1, Math.min(editorState.imgHeight - y, Math.round(Number.isFinite(rawH) ? rawH : editorState.imgHeight)));
  return { x, y, width, height };
}

function forEachStoredSelection(editorState, callback) {
  for (const selection of editorState.savedSelections || []) {
    if (selection?.canvas) callback(selection.canvas);
  }
  if (editorState.lastSelection?.canvas) callback(editorState.lastSelection.canvas);
}

/** Crop layer pixels, masks, and transient selections as one document. */
export function cropDocument(editorState, rect) {
  if (!editorState.imgWidth || !editorState.imgHeight) return null;
  const oldWidth = editorState.imgWidth;
  const oldHeight = editorState.imgHeight;
  const { x, y, width, height } = normalizeCropRect(editorState, rect);
  const oldOffsets = new Map(editorState.layerOffsets);

  for (const layer of editorState.layers) {
    const offset = oldOffsets.get(layer.id) || { x: 0, y: 0 };
    const retainedText = layer.kind === 'text' && layer.text;
    const retainedShape = layer.kind === 'shape' && layer.shape;
    let placedOffset = null;
    if (layer.kind === 'placed' && layer.placed) {
      layer.placed = transformPlacedData(layer.placed, [1, 0, 0, 1, -x, -y]);
      placedOffset = renderPlacedLayer(layer)?.offset || null;
    } else if (!retainedText && !retainedShape) {
      replaceCanvas(layer, renderWindow(layer.canvas, x - offset.x, y - offset.y, width, height));
    }
    for (const mask of layer.masks || []) {
      const documentSpace = (mask.space || (mask.mode === 'layer' ? 'layer' : 'document')) === 'document';
      if ((retainedText || retainedShape || layer.kind === 'placed') && !documentSpace) continue;
      const maskOffset = mask.offset || { x: 0, y: 0 };
      const sourceX = documentSpace ? x : x - offset.x - (Number(maskOffset.x) || 0);
      const sourceY = documentSpace ? y : y - offset.y - (Number(maskOffset.y) || 0);
      replaceCanvas(mask, renderWindow(mask.canvas, sourceX, sourceY, width, height));
      mask.space = documentSpace ? 'document' : 'layer';
      if (!documentSpace) mask.offset = { x: 0, y: 0 };
    }
    editorState.layerOffsets.set(layer.id, placedOffset || ((retainedText || retainedShape)
      ? { x: offset.x - x, y: offset.y - y }
      : { x: 0, y: 0 }));
    invalidateLayerCaches(layer);
  }
  for (const group of editorState.layerGroups || []) {
    for (const mask of group.masks || []) replaceCanvas(mask, renderWindow(mask.canvas, x, y, width, height));
  }

  if (editorState.wandMask) {
    const sourceLayerOffset = oldOffsets.get(editorState.wandLayerId) || { x: 0, y: 0 };
    const documentSpace = editorState.wandMaskSpace === 'document' || (
      !editorState.wandMaskSpace && editorState.wandMask.width === oldWidth && editorState.wandMask.height === oldHeight
    );
    const sourceX = documentSpace ? x : x - sourceLayerOffset.x;
    const sourceY = documentSpace ? y : y - sourceLayerOffset.y;
    replaceStandaloneCanvas(
      editorState.wandMask,
      renderWindow(editorState.wandMask, sourceX, sourceY, width, height),
    );
  }
  forEachStoredSelection(editorState, canvas => {
    replaceStandaloneCanvas(canvas, renderWindow(canvas, x, y, width, height));
  });
  if (editorState.wandLastSeed) {
    const seed = { ...editorState.wandLastSeed, x: editorState.wandLastSeed.x - x, y: editorState.wandLastSeed.y - y };
    editorState.wandLastSeed = seed.x >= 0 && seed.y >= 0 && seed.x < width && seed.y < height ? seed : null;
  }
  editorState.lassoPoints = clipPolygonToRect(
    (editorState.lassoPoints || []).map(point => ({ x: point.x - x, y: point.y - y })),
    width,
    height,
  );
  const guides = editorState.guides || {};
  transformGuides(
    editorState,
    { values: guides.vertical, limit: width, map: value => value - x },
    { values: guides.horizontal, limit: height, map: value => value - y },
  );
  editorState.cropRect = null;
  editorState.cropStart = null;
  editorState.cropEnd = null;
  syncDocumentCanvas(editorState, width, height);
  return { x, y, width, height };
}

/** Resize the actual image content, including every layer-space/document-space mask. */
export function resizeImageDocument(editorState, width, height, options = {}) {
  const newWidth = Math.max(1, Math.round(width));
  const newHeight = Math.max(1, Math.round(height));
  const oldWidth = editorState.imgWidth;
  const oldHeight = editorState.imgHeight;
  if (!oldWidth || !oldHeight) return null;
  const scaleX = newWidth / oldWidth;
  const scaleY = newHeight / oldHeight;
  const quality = options.smoothingQuality || 'high';

  for (const layer of editorState.layers) {
    const targetLayerWidth = Math.max(1, Math.round(layer.canvas.width * scaleX));
    const targetLayerHeight = Math.max(1, Math.round(layer.canvas.height * scaleY));
    let placedOffset = null;
    if (layer.kind === 'text' && layer.text) scaleTextLayer(layer, scaleX, scaleY);
    else if (layer.kind === 'shape' && layer.shape) scaleShapeLayer(layer, scaleX, scaleY);
    else if (layer.kind === 'placed' && layer.placed) {
      layer.placed = transformPlacedData(layer.placed, [scaleX, 0, 0, scaleY, 0, 0]);
      placedOffset = renderPlacedLayer(layer)?.offset || null;
    } else replaceCanvas(layer, renderScaled(layer.canvas, targetLayerWidth, targetLayerHeight, quality));
    const layerWidth = layer.canvas.width;
    const layerHeight = layer.canvas.height;
    for (const mask of layer.masks || []) {
      const documentSpace = (mask.space || (mask.mode === 'layer' ? 'layer' : 'document')) === 'document';
      const targetMaskWidth = documentSpace
        ? newWidth
        : Math.max(1, Math.round(mask.canvas.width * scaleX));
      const targetMaskHeight = documentSpace
        ? newHeight
        : Math.max(1, Math.round(mask.canvas.height * scaleY));
      replaceCanvas(
        mask,
        renderScaled(mask.canvas, targetMaskWidth, targetMaskHeight, quality),
      );
      mask.space = documentSpace ? 'document' : 'layer';
      if (!documentSpace) {
        mask.offset = {
          x: Math.round((Number(mask.offset?.x) || 0) * scaleX),
          y: Math.round((Number(mask.offset?.y) || 0) * scaleY),
        };
      }
    }
    const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    editorState.layerOffsets.set(layer.id, placedOffset || {
      x: Math.round(offset.x * scaleX),
      y: Math.round(offset.y * scaleY),
    });
    invalidateLayerCaches(layer);
  }
  for (const group of editorState.layerGroups || []) {
    for (const mask of group.masks || []) replaceCanvas(mask, renderScaled(mask.canvas, newWidth, newHeight, quality));
  }

  if (editorState.wandMask) {
    replaceStandaloneCanvas(
      editorState.wandMask,
      renderScaled(
        editorState.wandMask,
        editorState.wandMaskSpace === 'document' ? newWidth : Math.max(1, Math.round(editorState.wandMask.width * scaleX)),
        editorState.wandMaskSpace === 'document' ? newHeight : Math.max(1, Math.round(editorState.wandMask.height * scaleY)),
        quality,
      ),
    );
  }
  forEachStoredSelection(editorState, canvas => {
    replaceStandaloneCanvas(canvas, renderScaled(canvas, newWidth, newHeight, quality));
  });
  if (editorState.wandLastSeed) {
    editorState.wandLastSeed = {
      ...editorState.wandLastSeed,
      x: editorState.wandLastSeed.x * scaleX,
      y: editorState.wandLastSeed.y * scaleY,
    };
  }
  editorState.lassoPoints = (editorState.lassoPoints || []).map(point => ({
    x: point.x * scaleX,
    y: point.y * scaleY,
  }));
  const guides = editorState.guides || {};
  transformGuides(
    editorState,
    { values: guides.vertical, limit: newWidth, map: value => value * scaleX },
    { values: guides.horizontal, limit: newHeight, map: value => value * scaleY },
  );
  syncDocumentCanvas(editorState, newWidth, newHeight);
  return { width: newWidth, height: newHeight, scaleX, scaleY };
}

/** Change document bounds without resampling layer pixels. Origin stays top-left. */
export function resizeCanvasDocument(editorState, width, height, options = {}) {
  const newWidth = Math.max(1, Math.round(width));
  const newHeight = Math.max(1, Math.round(height));
  const anchorX = Math.max(0, Math.min(1, Number(options.anchorX) || 0));
  const anchorY = Math.max(0, Math.min(1, Number(options.anchorY) || 0));
  const shiftX = Math.round((newWidth - editorState.imgWidth) * anchorX);
  const shiftY = Math.round((newHeight - editorState.imgHeight) * anchorY);
  for (const layer of editorState.layers) {
    for (const mask of layer.masks || []) {
      const documentSpace = (mask.space || (mask.mode === 'layer' ? 'layer' : 'document')) === 'document';
      if (!documentSpace) continue;
      replaceCanvas(mask, renderTranslated(mask.canvas, newWidth, newHeight, shiftX, shiftY));
      mask.space = 'document';
    }
    const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    editorState.layerOffsets.set(layer.id, {
      x: (Number(offset.x) || 0) + shiftX,
      y: (Number(offset.y) || 0) + shiftY,
    });
  }
  for (const group of editorState.layerGroups || []) {
    for (const mask of group.masks || []) replaceCanvas(mask, renderTranslated(mask.canvas, newWidth, newHeight, shiftX, shiftY));
  }
  if (editorState.wandMask && editorState.wandMaskSpace === 'document') {
    replaceStandaloneCanvas(editorState.wandMask, renderTranslated(editorState.wandMask, newWidth, newHeight, shiftX, shiftY));
  }
  forEachStoredSelection(editorState, canvas => {
    replaceStandaloneCanvas(canvas, renderTranslated(canvas, newWidth, newHeight, shiftX, shiftY));
  });
  editorState.lassoPoints = clipPolygonToRect(
    (editorState.lassoPoints || []).map(point => ({ x: point.x + shiftX, y: point.y + shiftY })),
    newWidth,
    newHeight,
  );
  if (editorState.wandLastSeed) {
    const seed = { ...editorState.wandLastSeed, x: editorState.wandLastSeed.x + shiftX, y: editorState.wandLastSeed.y + shiftY };
    editorState.wandLastSeed = seed.x >= 0 && seed.y >= 0 && seed.x < newWidth && seed.y < newHeight ? seed : null;
  }
  const guides = editorState.guides || {};
  transformGuides(
    editorState,
    { values: guides.vertical, limit: newWidth, map: value => value + shiftX },
    { values: guides.horizontal, limit: newHeight, map: value => value + shiftY },
  );
  syncDocumentCanvas(editorState, newWidth, newHeight);
  return { width: newWidth, height: newHeight };
}

function rotatedPoint(point, degrees, oldWidth, oldHeight) {
  const normalized = ((degrees % 360) + 360) % 360;
  if (normalized === 90) return { x: oldHeight - point.y, y: point.x };
  if (normalized === 180) return { x: oldWidth - point.x, y: oldHeight - point.y };
  if (normalized === 270) return { x: point.y, y: oldWidth - point.x };
  return { x: point.x, y: point.y };
}

function rotatedOffset(offset, width, height, degrees, oldWidth, oldHeight) {
  const normalized = ((degrees % 360) + 360) % 360;
  if (normalized === 90) return { x: oldHeight - offset.y - height, y: offset.x };
  if (normalized === 180) return { x: oldWidth - offset.x - width, y: oldHeight - offset.y - height };
  if (normalized === 270) return { x: offset.y, y: oldWidth - offset.x - width };
  return { x: offset.x, y: offset.y };
}

/** Rotate every layer, mask, offset, and active selection by a right angle. */
export function rotateDocument(editorState, degrees) {
  const normalized = ((degrees % 360) + 360) % 360;
  if (![90, 180, 270].includes(normalized)) return null;
  const oldWidth = editorState.imgWidth;
  const oldHeight = editorState.imgHeight;
  const newWidth = normalized === 180 ? oldWidth : oldHeight;
  const newHeight = normalized === 180 ? oldHeight : oldWidth;

  for (const layer of editorState.layers) {
    const oldLayerWidth = layer.canvas.width;
    const oldLayerHeight = layer.canvas.height;
    const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    let placedOffset = null;
    if (layer.kind === 'text' && layer.text) rotateTextLayer(layer, normalized);
    else if (layer.kind === 'shape' && layer.shape) rotateShapeLayer(layer, normalized);
    else if (layer.kind === 'placed' && layer.placed) {
      const matrix = normalized === 90
        ? [0, 1, -1, 0, oldHeight, 0]
        : normalized === 180
          ? [-1, 0, 0, -1, oldWidth, oldHeight]
          : [0, -1, 1, 0, 0, oldWidth];
      layer.placed = transformPlacedData(layer.placed, matrix);
      placedOffset = renderPlacedLayer(layer)?.offset || null;
    } else replaceCanvas(layer, renderRotated(layer.canvas, normalized));
    for (const mask of layer.masks || []) {
      const oldMaskWidth = mask.canvas.width;
      const oldMaskHeight = mask.canvas.height;
      const maskOffset = mask.offset || { x: 0, y: 0 };
      replaceCanvas(mask, renderRotated(mask.canvas, normalized));
      if ((mask.space || (mask.mode === 'layer' ? 'layer' : 'document')) === 'layer') {
        const ox = Number(maskOffset.x) || 0;
        const oy = Number(maskOffset.y) || 0;
        if (normalized === 90) {
          mask.offset = { x: oldLayerHeight - oy - oldMaskHeight, y: ox };
        } else if (normalized === 180) {
          mask.offset = { x: oldLayerWidth - ox - oldMaskWidth, y: oldLayerHeight - oy - oldMaskHeight };
        } else {
          mask.offset = { x: oy, y: oldLayerWidth - ox - oldMaskWidth };
        }
      }
    }
    editorState.layerOffsets.set(layer.id, placedOffset ||
      rotatedOffset(offset, oldLayerWidth, oldLayerHeight, normalized, oldWidth, oldHeight));
    invalidateLayerCaches(layer);
  }
  for (const group of editorState.layerGroups || []) {
    for (const mask of group.masks || []) replaceCanvas(mask, renderRotated(mask.canvas, normalized));
  }

  if (editorState.wandMask) replaceStandaloneCanvas(editorState.wandMask, renderRotated(editorState.wandMask, normalized));
  forEachStoredSelection(editorState, canvas => {
    replaceStandaloneCanvas(canvas, renderRotated(canvas, normalized));
  });
  if (editorState.wandLastSeed) {
    editorState.wandLastSeed = {
      ...editorState.wandLastSeed,
      ...rotatedPoint(editorState.wandLastSeed, normalized, oldWidth, oldHeight),
    };
  }
  editorState.lassoPoints = (editorState.lassoPoints || []).map(point =>
    rotatedPoint(point, normalized, oldWidth, oldHeight));
  const guides = editorState.guides || {};
  if (normalized === 90) {
    transformGuides(
      editorState,
      { values: guides.horizontal, limit: newWidth, map: value => oldHeight - value },
      { values: guides.vertical, limit: newHeight },
    );
  } else if (normalized === 180) {
    transformGuides(
      editorState,
      { values: guides.vertical, limit: newWidth, map: value => oldWidth - value },
      { values: guides.horizontal, limit: newHeight, map: value => oldHeight - value },
    );
  } else {
    transformGuides(
      editorState,
      { values: guides.horizontal, limit: newWidth },
      { values: guides.vertical, limit: newHeight, map: value => oldWidth - value },
    );
  }
  syncDocumentCanvas(editorState, newWidth, newHeight);
  return { width: newWidth, height: newHeight, degrees: normalized };
}

/**
 * Rotate the whole document by an arbitrary angle (degrees, clockwise),
 * expanding the canvas so nothing is clipped. Right angles delegate to the
 * exact integer path; other angles resample every layer, mask, and selection
 * about the old image centre and re-place each layer's centre in the new
 * frame. Guides are dropped because arbitrary rotation leaves no
 * axis-aligned reference.
 */
export function rotateDocumentArbitrary(editorState, degrees) {
  const raw = Number(degrees);
  if (!Number.isFinite(raw)) return null;
  const normalized = ((raw % 360) + 360) % 360;
  if (normalized === 0) {
    return { width: editorState.imgWidth, height: editorState.imgHeight, degrees: 0 };
  }
  if ([90, 180, 270].includes(normalized)) return rotateDocument(editorState, normalized);

  const radians = (normalized * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const oldWidth = editorState.imgWidth;
  const oldHeight = editorState.imgHeight;
  const newWidth = Math.max(1, Math.ceil(Math.abs(oldWidth * cos) + Math.abs(oldHeight * sin)));
  const newHeight = Math.max(1, Math.ceil(Math.abs(oldWidth * sin) + Math.abs(oldHeight * cos)));
  const mapPoint = (x, y) => ({
    x: cos * (x - oldWidth / 2) - sin * (y - oldHeight / 2) + newWidth / 2,
    y: sin * (x - oldWidth / 2) + cos * (y - oldHeight / 2) + newHeight / 2,
  });

  for (const layer of editorState.layers) {
    const before = layerBounds(editorState, layer);
    const mapped = mapPoint(before.centerX, before.centerY);
    let placedOffset = null;
    if (layer.kind === 'text' && layer.text) {
      rotateTextLayer(layer, normalized);
    } else if (layer.kind === 'shape' && layer.shape) {
      rotateShapeLayer(layer, normalized);
    } else if (layer.kind === 'placed' && layer.placed) {
      // Rotate the placed matrix about its centre, then translate that centre
      // to where the document rotation maps it.
      const matrix = [
        cos, sin, -sin, cos,
        before.centerX - cos * before.centerX + sin * before.centerY + (mapped.x - before.centerX),
        before.centerY - sin * before.centerX - cos * before.centerY + (mapped.y - before.centerY),
      ];
      layer.placed = transformPlacedData(layer.placed, matrix);
      placedOffset = renderPlacedLayer(layer)?.offset || null;
    } else {
      replaceCanvas(layer, renderRotatedArbitrary(layer.canvas, normalized));
    }
    const layerWidth = layer.canvas.width;
    const layerHeight = layer.canvas.height;
    for (const mask of layer.masks || []) {
      const oldMaskWidth = mask.canvas.width;
      const oldMaskHeight = mask.canvas.height;
      const maskOffset = mask.offset || { x: 0, y: 0 };
      replaceCanvas(mask, renderRotatedArbitrary(mask.canvas, normalized));
      const cx = (Number(maskOffset.x) || 0) + oldMaskWidth / 2;
      const cy = (Number(maskOffset.y) || 0) + oldMaskHeight / 2;
      if ((mask.space || (mask.mode === 'layer' ? 'layer' : 'document')) === 'layer') {
        // Layer-space masks rotate with their layer's canvas.
        const nx = cos * (cx - before.width / 2) - sin * (cy - before.height / 2) + layerWidth / 2;
        const ny = sin * (cx - before.width / 2) + cos * (cy - before.height / 2) + layerHeight / 2;
        mask.offset = {
          x: Math.round(nx - mask.canvas.width / 2),
          y: Math.round(ny - mask.canvas.height / 2),
        };
      } else {
        const mappedCenter = mapPoint(cx, cy);
        mask.offset = {
          x: Math.round(mappedCenter.x - mask.canvas.width / 2),
          y: Math.round(mappedCenter.y - mask.canvas.height / 2),
        };
      }
    }
    editorState.layerOffsets.set(layer.id, placedOffset || {
      x: Math.round(mapped.x - layerWidth / 2),
      y: Math.round(mapped.y - layerHeight / 2),
    });
    invalidateLayerCaches(layer);
  }
  for (const group of editorState.layerGroups || []) {
    for (const mask of group.masks || []) {
      const oldMaskWidth = mask.canvas.width;
      const oldMaskHeight = mask.canvas.height;
      const maskOffset = mask.offset || { x: 0, y: 0 };
      replaceCanvas(mask, renderRotatedArbitrary(mask.canvas, normalized));
      const mappedCenter = mapPoint(
        (Number(maskOffset.x) || 0) + oldMaskWidth / 2,
        (Number(maskOffset.y) || 0) + oldMaskHeight / 2,
      );
      mask.offset = {
        x: Math.round(mappedCenter.x - mask.canvas.width / 2),
        y: Math.round(mappedCenter.y - mask.canvas.height / 2),
      };
    }
  }

  if (editorState.wandMask) {
    replaceStandaloneCanvas(editorState.wandMask, renderRotatedArbitrary(editorState.wandMask, normalized));
  }
  forEachStoredSelection(editorState, canvas => {
    replaceStandaloneCanvas(canvas, renderRotatedArbitrary(canvas, normalized));
  });
  if (editorState.wandLastSeed) {
    const mappedSeed = mapPoint(editorState.wandLastSeed.x, editorState.wandLastSeed.y);
    editorState.wandLastSeed = { ...editorState.wandLastSeed, x: mappedSeed.x, y: mappedSeed.y };
  }
  editorState.lassoPoints = (editorState.lassoPoints || []).map(point => mapPoint(point.x, point.y));
  // Arbitrary rotation leaves no axis-aligned reference, so guides are dropped.
  editorState.guides = { vertical: [], horizontal: [] };
  syncDocumentCanvas(editorState, newWidth, newHeight);
  return { width: newWidth, height: newHeight, degrees: normalized };
}

/** Flip every layer, mask, offset, and active selection across the document. */
export function flipDocument(editorState, axis) {
  if (axis !== 'h' && axis !== 'v') return null;
  const width = editorState.imgWidth;
  const height = editorState.imgHeight;
  for (const layer of editorState.layers) {
    const layerWidth = layer.canvas.width;
    const layerHeight = layer.canvas.height;
    let placedOffset = null;
    if (layer.kind === 'text' && layer.text) flipTextLayer(layer, axis);
    else if (layer.kind === 'shape' && layer.shape) flipShapeLayer(layer, axis);
    else if (layer.kind === 'placed' && layer.placed) {
      const matrix = axis === 'h'
        ? [-1, 0, 0, 1, width, 0]
        : [1, 0, 0, -1, 0, height];
      layer.placed = transformPlacedData(layer.placed, matrix);
      placedOffset = renderPlacedLayer(layer)?.offset || null;
    } else replaceCanvas(layer, renderFlipped(layer.canvas, axis));
    for (const mask of layer.masks || []) {
      const maskWidth = mask.canvas.width;
      const maskHeight = mask.canvas.height;
      const maskOffset = mask.offset || { x: 0, y: 0 };
      replaceCanvas(mask, renderFlipped(mask.canvas, axis));
      if ((mask.space || (mask.mode === 'layer' ? 'layer' : 'document')) === 'layer') {
        const ox = Number(maskOffset.x) || 0;
        const oy = Number(maskOffset.y) || 0;
        mask.offset = axis === 'h'
          ? { x: layerWidth - ox - maskWidth, y: oy }
          : { x: ox, y: layerHeight - oy - maskHeight };
      }
    }
    const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    editorState.layerOffsets.set(layer.id, placedOffset || (axis === 'h'
      ? { x: width - offset.x - layerWidth, y: offset.y }
      : { x: offset.x, y: height - offset.y - layerHeight }));
    invalidateLayerCaches(layer);
  }
  for (const group of editorState.layerGroups || []) {
    for (const mask of group.masks || []) replaceCanvas(mask, renderFlipped(mask.canvas, axis));
  }
  if (editorState.wandMask) replaceStandaloneCanvas(editorState.wandMask, renderFlipped(editorState.wandMask, axis));
  forEachStoredSelection(editorState, canvas => {
    replaceStandaloneCanvas(canvas, renderFlipped(canvas, axis));
  });
  if (editorState.wandLastSeed) {
    editorState.wandLastSeed = {
      ...editorState.wandLastSeed,
      x: axis === 'h' ? width - editorState.wandLastSeed.x : editorState.wandLastSeed.x,
      y: axis === 'v' ? height - editorState.wandLastSeed.y : editorState.wandLastSeed.y,
    };
  }
  editorState.lassoPoints = (editorState.lassoPoints || []).map(point => ({
    x: axis === 'h' ? width - point.x : point.x,
    y: axis === 'v' ? height - point.y : point.y,
  }));
  const guides = editorState.guides || {};
  transformGuides(
    editorState,
    {
      values: guides.vertical,
      limit: width,
      map: axis === 'h' ? value => width - value : value => value,
    },
    {
      values: guides.horizontal,
      limit: height,
      map: axis === 'v' ? value => height - value : value => value,
    },
  );
  syncDocumentCanvas(editorState, width, height);
  return { width, height, axis };
}

// ── Single-layer transforms (Edit ▸ Transform semantics) ──
// Unlike the document-wide helpers above, these mutate ONE layer in place:
// the document bounds, other layers, and the active selection are untouched.
// The layer's visual centre is preserved so a rotation does not fling it
// across the canvas.

function layerBounds(editorState, layer) {
  if (layer.kind === 'placed' && layer.placed?.sourceCanvas) {
    const sw = layer.placed.sourceWidth || layer.placed.sourceCanvas.width;
    const sh = layer.placed.sourceHeight || layer.placed.sourceCanvas.height;
    return affineBounds(layer.placed.matrix, sw, sh);
  }
  const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const width = layer.canvas.width;
  const height = layer.canvas.height;
  return {
    x: offset.x,
    y: offset.y,
    width,
    height,
    centerX: offset.x + width / 2,
    centerY: offset.y + height / 2,
  };
}

function recenterLayer(editorState, layer, before) {
  const width = layer.canvas.width;
  const height = layer.canvas.height;
  editorState.layerOffsets.set(layer.id, {
    x: Math.round(before.centerX - width / 2),
    y: Math.round(before.centerY - height / 2),
  });
}

function flipMatrixAbout(centerX, centerY, axis) {
  return axis === 'h'
    ? [-1, 0, 0, 1, 2 * centerX, 0]
    : [1, 0, 0, -1, 0, 2 * centerY];
}

function rotateMatrixAbout(centerX, centerY, degrees) {
  const radians = (degrees * Math.PI) / 180;
  const cos = Math.round(Math.cos(radians));
  const sin = Math.round(Math.sin(radians));
  // T(center) · R · T(-center)
  return [
    cos, sin, -sin, cos,
    centerX - cos * centerX + sin * centerY,
    centerY - sin * centerX - cos * centerY,
  ];
}

function transformLayerMasks(layer, transform) {
  for (const mask of layer.masks || []) {
    transform(mask);
  }
}

/** Flip a single layer horizontally ('h') or vertically ('v'), in place. */
export function flipLayer(editorState, layer, axis) {
  if (!layer || (axis !== 'h' && axis !== 'v')) return null;
  const before = layerBounds(editorState, layer);
  if (layer.kind === 'text' && layer.text) flipTextLayer(layer, axis);
  else if (layer.kind === 'shape' && layer.shape) flipShapeLayer(layer, axis);
  else if (layer.kind === 'placed' && layer.placed) {
    layer.placed = transformPlacedData(layer.placed, flipMatrixAbout(before.centerX, before.centerY, axis));
    const rendered = renderPlacedLayer(layer);
    if (rendered) editorState.layerOffsets.set(layer.id, rendered.offset);
  } else {
    replaceCanvas(layer, renderFlipped(layer.canvas, axis));
    recenterLayer(editorState, layer, before);
  }
  transformLayerMasks(layer, (mask) => {
    const maskWidth = mask.canvas.width;
    const maskHeight = mask.canvas.height;
    const maskOffset = mask.offset || { x: 0, y: 0 };
    replaceCanvas(mask, renderFlipped(mask.canvas, axis));
    if ((mask.space || (mask.mode === 'layer' ? 'layer' : 'document')) === 'layer') {
      const ox = Number(maskOffset.x) || 0;
      const oy = Number(maskOffset.y) || 0;
      mask.offset = axis === 'h'
        ? { x: before.width - ox - maskWidth, y: oy }
        : { x: ox, y: before.height - oy - maskHeight };
    }
  });
  invalidateLayerCaches(layer);
  return { axis };
}

/** Rotate a single layer by 90/180/270°, keeping its centre fixed. */
export function rotateLayer(editorState, layer, degrees) {
  const normalized = ((degrees % 360) + 360) % 360;
  if (!layer || ![90, 180, 270].includes(normalized)) return null;
  const before = layerBounds(editorState, layer);
  if (layer.kind === 'text' && layer.text) rotateTextLayer(layer, normalized);
  else if (layer.kind === 'shape' && layer.shape) rotateShapeLayer(layer, normalized);
  else if (layer.kind === 'placed' && layer.placed) {
    layer.placed = transformPlacedData(layer.placed, rotateMatrixAbout(before.centerX, before.centerY, normalized));
    const rendered = renderPlacedLayer(layer);
    if (rendered) editorState.layerOffsets.set(layer.id, rendered.offset);
  } else {
    replaceCanvas(layer, renderRotated(layer.canvas, normalized));
    recenterLayer(editorState, layer, before);
  }
  transformLayerMasks(layer, (mask) => {
    const oldMaskWidth = mask.canvas.width;
    const oldMaskHeight = mask.canvas.height;
    const maskOffset = mask.offset || { x: 0, y: 0 };
    replaceCanvas(mask, renderRotated(mask.canvas, normalized));
    if ((mask.space || (mask.mode === 'layer' ? 'layer' : 'document')) === 'layer') {
      const ox = Number(maskOffset.x) || 0;
      const oy = Number(maskOffset.y) || 0;
      if (normalized === 90) {
        mask.offset = { x: before.height - oy - oldMaskHeight, y: ox };
      } else if (normalized === 180) {
        mask.offset = { x: before.width - ox - oldMaskWidth, y: before.height - oy - oldMaskHeight };
      } else {
        mask.offset = { x: oy, y: before.width - ox - oldMaskWidth };
      }
    }
  });
  invalidateLayerCaches(layer);
  return { degrees: normalized };
}
