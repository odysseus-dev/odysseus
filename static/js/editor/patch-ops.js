// Patch / Content-Aware Move pixel engine.
//
// Deterministic and fully offline (no AI/network). Two operations:
//   * patchRegion  — Photoshop-style Patch: replace what is inside the
//     selection with the same-shaped source region shifted by (dx,dy),
//     blended at a feathered edge so the seam disappears.
//   * contentAwareMove — cut the selection, paste it at +(dx,dy), then
//     fill the vacated hole by diffusion (repeatedly averaging the four
//     neighbouring colours inward from the hole boundary).
//
// All geometry is passed as canvases + a layer offset so the helpers stay
// dependency-light and can be unit-tested with a fake `createCanvas`.
// `createCanvas(w,h)` must return a canvas-like object with getContext('2d').

// Draw `source` into a fresh canvas, keeping only the pixels where `mask`
// is opaque (destination-in). Returns the new canvas.
function restrictToMask(createCanvas, source, mask, width, height) {
  const out = createCanvas(width, height);
  const ctx = out.getContext('2d');
  ctx.drawImage(source, 0, 0);
  ctx.globalCompositeOperation = 'destination-in';
  ctx.drawImage(mask, 0, 0);
  return out;
}

// Feathered copy of a mask (soft edge for blending a patch). Returns a NEW
// canvas; the source is not mutated.
function featherMask(createCanvas, mask, width, height, radius) {
  const out = createCanvas(width, height);
  const ctx = out.getContext('2d');
  const r = Math.max(0, Number(radius) || 0);
  if (r > 0 && 'filter' in ctx) ctx.filter = `blur(${r}px)`;
  ctx.drawImage(mask, 0, 0);
  if (r > 0 && 'filter' in ctx) ctx.filter = 'none';
  return out;
}

// Translate a document-space mask into layer space (shift by -offset).
function maskInLayerSpace(createCanvas, mask, offset, width, height) {
  const out = createCanvas(width, height);
  const ctx = out.getContext('2d');
  ctx.drawImage(mask, -(offset.x || 0), -(offset.y || 0));
  return out;
}

/**
 * Patch: fill the selection with content sampled from (dx,dy) away.
 * @returns {*} a NEW canvas the size of `layerCanvas` with the patch applied.
 */
export function patchRegion(createCanvas, layerCanvas, maskCanvas, offset, dx, dy, feather = 0) {
  const w = layerCanvas.width;
  const h = layerCanvas.height;
  const out = createCanvas(w, h);
  const octx = out.getContext('2d');
  octx.drawImage(layerCanvas, 0, 0);

  let localMask = maskInLayerSpace(createCanvas, maskCanvas, offset, w, h);
  if (feather > 0) localMask = featherMask(createCanvas, localMask, w, h, feather);

  // Source pixels sampled at (x+dx, y+dy) == the layer drawn shifted by
  // (-dx, -dy), then clipped to the selection.
  const shifted = createCanvas(w, h);
  shifted.getContext('2d').drawImage(layerCanvas, -dx, -dy);
  const sourceInMask = restrictToMask(createCanvas, shifted, localMask, w, h);

  octx.drawImage(sourceInMask, 0, 0);
  return out;
}

/**
 * Content-Aware Move: move the selection content by (dx,dy) and fill the
 * hole it leaves behind with a diffusion fill grown from the boundary.
 */
export function contentAwareMove(createCanvas, layerCanvas, maskCanvas, offset, dx, dy, iterations = 16) {
  const w = layerCanvas.width;
  const h = layerCanvas.height;
  const localMask = maskInLayerSpace(createCanvas, maskCanvas, offset, w, h);

  // Content inside the selection, cut out on its own.
  const cut = restrictToMask(createCanvas, layerCanvas, localMask, w, h);

  // Fill the hole. Start with the surrounding content (hole transparent),
  // then grow the edge colour inwards one pixel per pass in all four
  // directions (nearest-boundary fill, "destination-over" only writes where
  // the buffer is still transparent). This reliably covers even a large hole,
  // unlike a plain diffusion which needs O(radius^2) iterations to converge.
  const fill = createCanvas(w, h);
  const fctx = fill.getContext('2d');
  fctx.drawImage(layerCanvas, 0, 0);
  fctx.globalCompositeOperation = 'destination-out';
  fctx.drawImage(localMask, 0, 0);
  fctx.globalCompositeOperation = 'source-over';

  let bw = 0;
  let bh = 0;
  try {
    const md = localMask.getContext('2d').getImageData(0, 0, w, h).data;
    let minX = w, minY = h, maxX = -1, maxY = -1;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (md[(y * w + x) * 4 + 3] > 128) {
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }
    if (maxX >= 0) { bw = maxX - minX + 1; bh = maxY - minY + 1; }
  } catch (_) { /* no getImageData — use the requested pass count */ }
  const radius = Math.ceil(Math.max(bw, bh) / 2);
  const passes = Math.max(0, Math.min(256, Math.max(Math.round(iterations) || 0, radius + 2)));
  fctx.globalCompositeOperation = 'destination-over';
  for (let i = 0; i < passes; i++) {
    fctx.drawImage(fill, 1, 0);
    fctx.drawImage(fill, -1, 0);
    fctx.drawImage(fill, 0, 1);
    fctx.drawImage(fill, 0, -1);
  }
  fctx.globalCompositeOperation = 'source-over';

  // Light smoothing pass so the grown fill is not blocky.
  for (let i = 0; i < 3; i++) {
    const avg = createCanvas(w, h);
    const actx = avg.getContext('2d');
    actx.globalAlpha = 0.25;
    actx.drawImage(fill, 1, 0);
    actx.drawImage(fill, -1, 0);
    actx.drawImage(fill, 0, 1);
    actx.drawImage(fill, 0, -1);
    actx.globalAlpha = 1;
    const maskedAvg = restrictToMask(createCanvas, avg, localMask, w, h);
    fctx.drawImage(maskedAvg, 0, 0);
  }
  const filledHole = restrictToMask(createCanvas, fill, localMask, w, h);

  const out = createCanvas(w, h);
  const octx = out.getContext('2d');
  octx.drawImage(layerCanvas, 0, 0);
  octx.drawImage(filledHole, 0, 0);
  octx.drawImage(cut, dx, dy);
  return out;
}

export default { patchRegion, contentAwareMove };
