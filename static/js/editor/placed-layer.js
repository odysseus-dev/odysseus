/** Non-destructive image layers backed by immutable source pixels. */

const IDENTITY = Object.freeze([1, 0, 0, 1, 0, 0]);

export function normalizeAffine(value, fallback = IDENTITY) {
  const source = Array.isArray(value) && value.length === 6 ? value : fallback;
  const matrix = source.map(Number);
  return matrix.every(Number.isFinite) ? matrix : [...fallback];
}

export function multiplyAffine(left, right) {
  const [a, b, c, d, e, f] = normalizeAffine(left);
  const [g, h, i, j, k, l] = normalizeAffine(right);
  return [
    a * g + c * h,
    b * g + d * h,
    a * i + c * j,
    b * i + d * j,
    a * k + c * l + e,
    b * k + d * l + f,
  ];
}

export function transformPoint(matrix, x, y) {
  const [a, b, c, d, e, f] = normalizeAffine(matrix);
  return { x: a * x + c * y + e, y: b * x + d * y + f };
}

export function affineBounds(matrix, width, height) {
  const points = [
    transformPoint(matrix, 0, 0),
    transformPoint(matrix, width, 0),
    transformPoint(matrix, width, height),
    transformPoint(matrix, 0, height),
  ];
  const minX = Math.floor(Math.min(...points.map(point => point.x)));
  const minY = Math.floor(Math.min(...points.map(point => point.y)));
  const maxX = Math.ceil(Math.max(...points.map(point => point.x)));
  const maxY = Math.ceil(Math.max(...points.map(point => point.y)));
  return {
    x: minX,
    y: minY,
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
    centerX: (minX + maxX) / 2,
    centerY: (minY + maxY) / 2,
  };
}

export function initialPlacedMatrix(sourceWidth, sourceHeight, frame) {
  const width = Math.max(1, Number(sourceWidth) || 1);
  const height = Math.max(1, Number(sourceHeight) || 1);
  return [
    Math.max(1, Number(frame?.width) || width) / width,
    0,
    0,
    Math.max(1, Number(frame?.height) || height) / height,
    Number(frame?.x) || 0,
    Number(frame?.y) || 0,
  ];
}

/** Matrix that maps the session's document-space frame to its target frame. */
export function frameTransformMatrix(sourceBounds, target) {
  const scaleX = Math.max(1, target.width) / Math.max(1, sourceBounds.width);
  const scaleY = Math.max(1, target.height) / Math.max(1, sourceBounds.height);
  const sx = target.flipH ? -scaleX : scaleX;
  const sy = target.flipV ? -scaleY : scaleY;
  const radians = (Number(target.rotation) || 0) * Math.PI / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  return multiplyAffine(
    [1, 0, 0, 1, target.centerX, target.centerY],
    multiplyAffine(
      [cos, sin, -sin, cos, 0, 0],
      multiplyAffine([sx, 0, 0, sy, 0, 0], [1, 0, 0, 1, -sourceBounds.centerX, -sourceBounds.centerY]),
    ),
  );
}

export function copyCanvas(source) {
  if (!source) return null;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Number(source.width) || 1);
  canvas.height = Math.max(1, Number(source.height) || 1);
  canvas.getContext('2d').drawImage(source, 0, 0);
  return canvas;
}

export function createPlacedData(sourceCanvas, matrix, sourceName = 'Placed image') {
  if (!sourceCanvas?.width || !sourceCanvas?.height) throw new Error('Placed source is empty.');
  return {
    sourceCanvas: copyCanvas(sourceCanvas),
    sourceWidth: sourceCanvas.width,
    sourceHeight: sourceCanvas.height,
    sourceName: String(sourceName || 'Placed image').slice(0, 200),
    matrix: normalizeAffine(matrix),
  };
}

export function clonePlacedData(placed, { copySource = false } = {}) {
  if (!placed?.sourceCanvas) return null;
  return {
    sourceCanvas: copySource ? copyCanvas(placed.sourceCanvas) : placed.sourceCanvas,
    sourceWidth: placed.sourceWidth || placed.sourceCanvas.width,
    sourceHeight: placed.sourceHeight || placed.sourceCanvas.height,
    sourceName: placed.sourceName || 'Placed image',
    matrix: normalizeAffine(placed.matrix),
  };
}

/** Render from the immutable source and return the layer's document offset. */
export function renderPlacedLayer(layer, placed = layer?.placed) {
  if (!layer || !placed?.sourceCanvas) return null;
  const sourceWidth = placed.sourceWidth || placed.sourceCanvas.width;
  const sourceHeight = placed.sourceHeight || placed.sourceCanvas.height;
  const bounds = affineBounds(placed.matrix, sourceWidth, sourceHeight);
  const canvas = layer.canvas || document.createElement('canvas');
  canvas.width = bounds.width;
  canvas.height = bounds.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const [a, b, c, d, e, f] = normalizeAffine(placed.matrix);
  ctx.setTransform(a, b, c, d, e - bounds.x, f - bounds.y);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(placed.sourceCanvas, 0, 0, sourceWidth, sourceHeight);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  layer.canvas = canvas;
  layer.ctx = ctx;
  layer.kind = 'placed';
  layer.placed = placed;
  layer._adjCache = null;
  layer._adjCacheKey = null;
  layer._adjFinal = null;
  layer._adjFinalKey = null;
  return { canvas, offset: { x: bounds.x, y: bounds.y }, bounds };
}

export function transformPlacedData(placed, documentTransform) {
  return {
    ...clonePlacedData(placed),
    matrix: multiplyAffine(documentTransform, placed.matrix),
  };
}

export function translatePlacedData(placed, dx, dy) {
  return transformPlacedData(placed, [1, 0, 0, 1, Number(dx) || 0, Number(dy) || 0]);
}

/** Replace pixels while preserving the exact four document-space corners. */
export function replacePlacedSource(layer, sourceCanvas, sourceName = 'Placed image') {
  if (layer?.kind !== 'placed' || !layer.placed?.sourceCanvas) return null;
  if (!sourceCanvas?.width || !sourceCanvas?.height) throw new Error('Replacement source is empty.');
  const oldWidth = layer.placed.sourceWidth || layer.placed.sourceCanvas.width;
  const oldHeight = layer.placed.sourceHeight || layer.placed.sourceCanvas.height;
  const [a, b, c, d, e, f] = normalizeAffine(layer.placed.matrix);
  layer.placed = createPlacedData(sourceCanvas, [
    a * oldWidth / sourceCanvas.width,
    b * oldWidth / sourceCanvas.width,
    c * oldHeight / sourceCanvas.height,
    d * oldHeight / sourceCanvas.height,
    e,
    f,
  ], sourceName);
  return renderPlacedLayer(layer);
}

export function rasterizePlacedLayer(layer) {
  if (layer?.kind !== 'placed') return false;
  layer.kind = 'raster';
  layer.placed = null;
  return true;
}
