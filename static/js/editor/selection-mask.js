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
    // Snap the anchor to whole document pixels so a fixed-size marquee
    // never straddles a pixel column/row (which antialiases into a
    // 1px-taller selection on the trailing edge).
    const ax = Math.round(sx);
    const ay = Math.round(sy);
    return {
      x: Math.max(0, Math.min(width - w, ax)),
      y: Math.max(0, Math.min(height - h, ay)),
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
  // Snap the anchor origin to whole document pixels, but keep w/h derived
  // exactly from the ratio so the aspect stays exact (rounding w and h
  // independently would drift the ratio).
  return {
    x: Math.round(signX < 0 ? sx - w : sx),
    y: Math.round(signY < 0 ? sy - h : sy),
    w: Math.max(1, w),
    h: Math.max(1, h),
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

/**
 * Boundary pixels AND the selected-flag grid for a mask, computed in one
 * pass. The flags let the ants painter find each outline pixel's exposed
 * edges without re-reading the mask.
 */
export function selectionBoundary(mask, threshold = 128) {
  const fallback = { width: mask?.width || 0, height: mask?.height || 0, pixels: [], flags: null };
  if (!mask?.width || !mask?.height) return fallback;
  let data;
  try { data = mask.getContext('2d').getImageData(0, 0, mask.width, mask.height).data; }
  catch { return fallback; }
  const width = mask.width;
  const height = mask.height;
  const flags = new Uint8Array(width * height);
  // First/last selected pixel of each row, so the outline pass can skip the
  // empty margins above, below and around the selection instead of testing
  // every pixel of a large canvas.
  const firsts = new Int32Array(height).fill(-1);
  const lasts = new Int32Array(height).fill(-1);
  for (let y = 0, i = 0, alpha = 3; y < height; y += 1) {
    let first = -1, last = -1;
    for (let x = 0; x < width; x += 1, i += 1, alpha += 4) {
      if (data[alpha] < threshold) continue;
      flags[i] = 1;
      if (first < 0) first = x;
      last = x;
    }
    firsts[y] = first;
    lasts[y] = last;
  }
  // Direct index arithmetic instead of a closure call per neighbour: this runs
  // over every pixel of the image on each new selection, and the five function
  // calls per pixel were a measurable share of the pen-close cost on big docs.
  const pixels = [];
  for (let y = 0; y < height; y += 1) {
    const first = firsts[y];
    if (first < 0) continue;
    const last = lasts[y];
    const row = y * width;
    const above = row - width;
    const below = row + width;
    const top = y === 0;
    const bottom = y === height - 1;
    for (let x = first; x <= last; x += 1) {
      const index = row + x;
      if (flags[index] !== 1) continue;
      if ((x === 0 || flags[index - 1] !== 1)
        || (x === width - 1 || flags[index + 1] !== 1)
        || (top || flags[above + x] !== 1)
        || (bottom || flags[below + x] !== 1)) {
        pixels.push(index);
      }
    }
  }
  return { width, height, pixels, flags };
}

export function paintSelectionBoundary(ctx, pixels, width, height, phase = 0, zoom = 1, flags = null) {
  if (!ctx || !pixels?.length || !width || !height) return;
  if (!flags || typeof Path2D === 'undefined') {
    // Fallback (no mask context / non-browser): per-pixel fill in image space.
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
    return;
  }
  // Screen-constant marching ants. `width`/`height` are the MASK (image)
  // dimensions; the caller's overlay bitmap is rendered at SCREEN resolution
  // (image size × zoom), so we multiply the image-space outline into screen
  // pixels and stroke a flat 1px line — no bitmap resampling, so the outline
  // stays the same thickness and opacity at every zoom level.
  const scale = zoom > 0 ? zoom : 1;
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.lineWidth = 1;
  ctx.lineCap = 'butt';
  ctx.setLineDash([]);
  const white = new Path2D();
  const black = new Path2D();
  const selected = (x, y) => x >= 0 && y >= 0 && x < width && y < height && flags[y * width + x] === 1;
  for (const index of pixels) {
    const x = index % width;
    const y = Math.floor(index / width);
    const x0 = x * scale;
    const y0 = y * scale;
    const x1 = (x + 1) * scale;
    const y1 = (y + 1) * scale;
    const sx = Math.floor((x + 0.5) * scale);
    const sy = Math.floor((y + 0.5) * scale);
    const path = ((sx + sy + phase) % 8) < 4 ? white : black;
    if (!selected(x, y - 1)) { path.moveTo(x0, y0); path.lineTo(x1, y0); }
    if (!selected(x, y + 1)) { path.moveTo(x0, y1); path.lineTo(x1, y1); }
    if (!selected(x - 1, y)) { path.moveTo(x0, y0); path.lineTo(x0, y1); }
    if (!selected(x + 1, y)) { path.moveTo(x1, y0); path.lineTo(x1, y1); }
  }
  ctx.strokeStyle = '#ffffff';
  ctx.stroke(white);
  ctx.strokeStyle = '#000000';
  ctx.stroke(black);
}
