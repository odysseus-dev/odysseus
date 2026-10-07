/** Shared selection-mask geometry and compositing helpers. */

import { dilateMask } from './mask-utils.js';

function makeCanvas(width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width));
  canvas.height = Math.max(1, Math.round(height));
  return canvas;
}

export function normalizeSelectionRect(start, end, width, height, square = false) {
  let dx = end.x - start.x;
  let dy = end.y - start.y;
  if (square) {
    const size = Math.min(Math.abs(dx), Math.abs(dy));
    dx = Math.sign(dx || 1) * size;
    dy = Math.sign(dy || 1) * size;
  }
  const x1 = Math.max(0, Math.min(width, start.x));
  const y1 = Math.max(0, Math.min(height, start.y));
  const x2 = Math.max(0, Math.min(width, start.x + dx));
  const y2 = Math.max(0, Math.min(height, start.y + dy));
  return {
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    w: Math.abs(x2 - x1),
    h: Math.abs(y2 - y1),
  };
}

export function normalizeConstrainedSelectionRect(start, end, width, height, options = {}) {
  const mode = ['ratio', 'size'].includes(options.mode) ? options.mode : 'free';
  if (mode === 'free') return normalizeSelectionRect(start, end, width, height, !!options.square);

  const sx = Math.max(0, Math.min(width, Number(start.x) || 0));
  const sy = Math.max(0, Math.min(height, Number(start.y) || 0));
  if (mode === 'size') {
    const w = Math.max(1, Math.min(width, Math.round(Number(options.fixedWidth) || 1)));
    const h = Math.max(1, Math.min(height, Math.round(Number(options.fixedHeight) || 1)));
    return {
      x: Math.max(0, Math.min(width - w, sx)),
      y: Math.max(0, Math.min(height - h, sy)),
      w,
      h,
    };
  }

  const ratioWidth = Math.max(0.01, Number(options.ratioWidth) || 1);
  const ratioHeight = Math.max(0.01, Number(options.ratioHeight) || 1);
  const ratio = ratioWidth / ratioHeight;
  const dx = (Number(end.x) || 0) - sx;
  const dy = (Number(end.y) || 0) - sy;
  const signX = dx < 0 ? -1 : 1;
  const signY = dy < 0 ? -1 : 1;
  let w;
  let h;
  if (Math.abs(dx) / Math.max(Math.abs(dy), 0.0001) >= ratio) {
    w = Math.abs(dx);
    h = w / ratio;
  } else {
    h = Math.abs(dy);
    w = h * ratio;
  }
  const availableW = signX < 0 ? sx : width - sx;
  const availableH = signY < 0 ? sy : height - sy;
  const scale = Math.min(1, availableW / Math.max(w, 0.0001), availableH / Math.max(h, 0.0001));
  w *= scale;
  h *= scale;
  return {
    x: signX < 0 ? sx - w : sx,
    y: signY < 0 ? sy - h : sy,
    w,
    h,
  };
}

export function createMarqueeMask(width, height, rect, shape = 'rectangle') {
  const canvas = makeCanvas(width, height);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  if (shape === 'ellipse') {
    ctx.beginPath();
    ctx.ellipse(
      rect.x + rect.w / 2,
      rect.y + rect.h / 2,
      rect.w / 2,
      rect.h / 2,
      0,
      0,
      Math.PI * 2,
    );
    ctx.fill();
  } else {
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
  }
  return canvas;
}

export function mergeSelectionMasks(current, incoming, mode = 'replace') {
  if (!current || mode === 'replace') return incoming;
  const merged = makeCanvas(incoming.width, incoming.height);
  const ctx = merged.getContext('2d');
  ctx.drawImage(current, 0, 0);
  if (mode === 'subtract') ctx.globalCompositeOperation = 'destination-out';
  else if (mode === 'intersect') ctx.globalCompositeOperation = 'destination-in';
  else ctx.globalCompositeOperation = 'source-over';
  ctx.drawImage(incoming, 0, 0);
  ctx.globalCompositeOperation = 'source-over';
  return merged;
}

function copyMask(mask) {
  if (!mask) return null;
  const canvas = makeCanvas(mask.width, mask.height);
  canvas.getContext('2d').drawImage(mask, 0, 0);
  return canvas;
}

function blurMask(mask, radius) {
  if (!mask || radius <= 0) return copyMask(mask);
  const canvas = makeCanvas(mask.width, mask.height);
  const ctx = canvas.getContext('2d');
  ctx.filter = `blur(${Math.max(0, radius)}px)`;
  ctx.drawImage(mask, 0, 0);
  ctx.filter = 'none';
  return canvas;
}

function thresholdMask(mask, threshold = 128) {
  const canvas = copyMask(mask);
  const ctx = canvas.getContext('2d');
  const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
  for (let i = 0; i < image.data.length; i += 4) {
    const alpha = image.data[i + 3] >= threshold ? 255 : 0;
    image.data[i] = alpha;
    image.data[i + 1] = alpha;
    image.data[i + 2] = alpha;
    image.data[i + 3] = alpha;
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

/** Build a non-destructive preview for the Select > Refine workflow. */
export function refineSelectionMask(mask, options = {}) {
  if (!mask) return null;
  const expand = Math.max(-100, Math.min(100, Math.round(Number(options.expand) || 0)));
  const smooth = Math.max(0, Math.min(100, Math.round(Number(options.smooth) || 0)));
  const border = Math.max(0, Math.min(200, Math.round(Number(options.border) || 0)));
  const feather = Math.max(0, Math.min(200, Number(options.feather) || 0));
  let result = expand ? dilateMask(mask, expand) : copyMask(mask);
  if (smooth) result = thresholdMask(blurMask(result, smooth), 128);
  if (border) {
    const outer = dilateMask(result, Math.ceil(border / 2));
    const inner = dilateMask(result, -Math.floor(border / 2));
    const ctx = outer.getContext('2d');
    ctx.globalCompositeOperation = 'destination-out';
    ctx.drawImage(inner, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
    result = outer;
  }
  return feather ? blurMask(result, feather) : result;
}

export function selectionMaskToDocument(mask, space, layerOffset, width, height) {
  if (!mask) return null;
  if (space === 'document' && mask.width === width && mask.height === height) return mask;
  const canvas = makeCanvas(width, height);
  canvas.getContext('2d').drawImage(
    mask,
    space === 'document' ? 0 : (layerOffset?.x || 0),
    space === 'document' ? 0 : (layerOffset?.y || 0),
  );
  return canvas;
}

export function selectionMaskForLayer(mask, space, layerOffset, width, height) {
  if (!mask) return null;
  if (space !== 'document' && mask.width === width && mask.height === height) return mask;
  const canvas = makeCanvas(width, height);
  canvas.getContext('2d').drawImage(
    mask,
    space === 'document' ? -(layerOffset?.x || 0) : 0,
    space === 'document' ? -(layerOffset?.y || 0) : 0,
  );
  return canvas;
}

export function selectionMaskContains(mask, x, y, threshold = 1) {
  if (!mask || x < 0 || y < 0 || x >= mask.width || y >= mask.height) return false;
  try {
    return mask.getContext('2d').getImageData(Math.floor(x), Math.floor(y), 1, 1).data[3] >= threshold;
  } catch {
    return false;
  }
}

export function selectionMaskBounds(mask, threshold = 1) {
  if (!mask?.width || !mask?.height) return null;
  let data;
  try { data = mask.getContext('2d').getImageData(0, 0, mask.width, mask.height).data; }
  catch { return null; }
  let left = mask.width;
  let top = mask.height;
  let right = -1;
  let bottom = -1;
  for (let y = 0; y < mask.height; y += 1) {
    for (let x = 0; x < mask.width; x += 1) {
      if (data[(y * mask.width + x) * 4 + 3] < threshold) continue;
      left = Math.min(left, x);
      top = Math.min(top, y);
      right = Math.max(right, x);
      bottom = Math.max(bottom, y);
    }
  }
  return right < left ? null : { x: left, y: top, width: right - left + 1, height: bottom - top + 1 };
}

export function translateSelectionMask(mask, dx, dy, width = mask?.width, height = mask?.height) {
  if (!mask) return null;
  const canvas = makeCanvas(width, height);
  canvas.getContext('2d').drawImage(mask, Math.round(dx), Math.round(dy));
  return canvas;
}

/**
 * Render a document-space selection through one affine transform. The source
 * mask is never mutated, so callers can rebuild every live preview from the
 * same pixels instead of accumulating interpolation damage.
 */
export function transformSelectionMask(mask, sourceBounds, target, width = mask?.width, height = mask?.height) {
  if (!mask || !sourceBounds || !target) return null;
  const sourceWidth = Math.max(1, Number(sourceBounds.width) || 1);
  const sourceHeight = Math.max(1, Number(sourceBounds.height) || 1);
  const targetWidth = Math.max(1, Number(target.width) || 1);
  const targetHeight = Math.max(1, Number(target.height) || 1);
  const canvas = makeCanvas(width, height);
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.save();
  ctx.translate(Number(target.centerX) || 0, Number(target.centerY) || 0);
  ctx.rotate(((Number(target.rotation) || 0) * Math.PI) / 180);
  ctx.scale(target.flipH ? -1 : 1, target.flipV ? -1 : 1);
  ctx.drawImage(
    mask,
    sourceBounds.x,
    sourceBounds.y,
    sourceWidth,
    sourceHeight,
    -targetWidth / 2,
    -targetHeight / 2,
    targetWidth,
    targetHeight,
  );
  ctx.restore();
  return canvas;
}

export function selectionBoundaryPixels(mask, threshold = 128) {
  if (!mask?.width || !mask?.height) return [];
  let data;
  try { data = mask.getContext('2d').getImageData(0, 0, mask.width, mask.height).data; }
  catch { return []; }
  const selected = (x, y) => x >= 0 && y >= 0 && x < mask.width && y < mask.height
    && data[(y * mask.width + x) * 4 + 3] >= threshold;
  const pixels = [];
  for (let y = 0; y < mask.height; y += 1) {
    for (let x = 0; x < mask.width; x += 1) {
      if (!selected(x, y)) continue;
      if (!selected(x - 1, y) || !selected(x + 1, y) || !selected(x, y - 1) || !selected(x, y + 1)) {
        pixels.push(y * mask.width + x);
      }
    }
  }
  return pixels;
}

export function paintSelectionBoundary(ctx, pixels, width, height, phase = 0) {
  if (!ctx || !pixels?.length || !width || !height) return;
  const image = ctx.createImageData(width, height);
  for (const index of pixels) {
    const x = index % width;
    const y = Math.floor(index / width);
    const light = ((x + y + phase) % 8) < 4;
    const offset = index * 4;
    image.data[offset] = light ? 255 : 0;
    image.data[offset + 1] = light ? 255 : 0;
    image.data[offset + 2] = light ? 255 : 0;
    image.data[offset + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
}
