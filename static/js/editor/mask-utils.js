/**
 * Mask-canvas helpers used by the inpaint pipeline.
 *
 * Pure utility functions — they take a canvas (or layer-shape) as
 * input and return a fresh canvas, with no module-level state.
 */

/**
 * Dilate (positive `px`) or erode (negative `px`) a binary alpha mask.
 *
 * Strategy: blur the source by `|px|`, then re-threshold the result.
 * - Dilation keeps anything with non-trivial blurred alpha (low cutoff).
 * - Erosion keeps only pixels that retained near-full alpha after blur.
 *
 * @param {HTMLCanvasElement} src   Source mask canvas.
 * @param {number}            px    Pixels to dilate (>0) or erode (<0). 0 = copy.
 * @returns {HTMLCanvasElement}     Fresh canvas with the same dimensions.
 */
export function dilateMask(src, px) {
  const w = src.width, h = src.height;
  const tmp = document.createElement('canvas');
  tmp.width = w; tmp.height = h;
  const ctx = tmp.getContext('2d');
  if (px === 0) {
    ctx.drawImage(src, 0, 0);
    return tmp;
  }
  const dilate = px > 0;
  const radius = Math.abs(px);
  ctx.filter = `blur(${radius}px)`;
  ctx.drawImage(src, 0, 0);
  ctx.filter = 'none';
  const img = ctx.getImageData(0, 0, w, h);
  const threshold = dilate ? 8 : 247;
  for (let i = 0; i < img.data.length; i += 4) {
    const a = img.data[i + 3];
    const keep = dilate ? a > threshold : a >= threshold;
    if (keep) {
      img.data[i] = img.data[i + 1] = img.data[i + 2] = 255;
      img.data[i + 3] = 255;
    } else {
      img.data[i + 3] = 0;
    }
  }
  ctx.putImageData(img, 0, 0);
  return tmp;
}


/**
 * Re-derive an inpaint-result layer's alpha from its cached AI image +
 * the hard mask, applying a feather + optional dilate/erode of the
 * boundary. Mutates `layer.canvas` in place via `layer.ctx`.
 *
 * The layer must carry an `inpaintSource = { ai, mask }` cache from the
 * original inpaint call so we can re-shape the alpha cheaply (no
 * second model call required). Large-image inpaint may cache only the
 * edited crop as `{ ai, mask, x, y, w, h }`; this avoids keeping a
 * second full-size AI canvas alive just for edge tuning.
 *
 * @param {{canvas: HTMLCanvasElement, ctx: CanvasRenderingContext2D,
 *          inpaintSource?: {ai: CanvasImageSource, mask: HTMLCanvasElement,
 *            x?: number, y?: number, w?: number, h?: number}}} layer
 * @param {number} featherPx     Gaussian blur radius applied to the mask alpha.
 * @param {number} [edgeShiftPx] Dilate (+) or erode (-) the mask before blurring.
 */
export function applyInpaintFeather(layer, featherPx, edgeShiftPx = 0) {
  if (!layer || !layer.inpaintSource) return;
  const { ai, mask } = layer.inpaintSource;
  const w = layer.canvas.width;
  const h = layer.canvas.height;
  const hasCrop = Number.isFinite(layer.inpaintSource.x)
    && Number.isFinite(layer.inpaintSource.y)
    && Number.isFinite(layer.inpaintSource.w)
    && Number.isFinite(layer.inpaintSource.h);
  const dx = hasCrop ? layer.inpaintSource.x : 0;
  const dy = hasCrop ? layer.inpaintSource.y : 0;
  const dw = hasCrop ? layer.inpaintSource.w : w;
  const dh = hasCrop ? layer.inpaintSource.h : h;
  // 1) Optional dilate/erode, then optional blur, into a fresh mask.
  let shaped = mask;
  if (edgeShiftPx !== 0) shaped = dilateMask(mask, edgeShiftPx);
  const softMask = document.createElement('canvas');
  softMask.width = dw; softMask.height = dh;
  const smCtx = softMask.getContext('2d');
  if (featherPx > 0) {
    smCtx.filter = `blur(${featherPx}px)`;
    smCtx.drawImage(shaped, 0, 0, dw, dh);
    smCtx.filter = 'none';
  } else {
    smCtx.drawImage(shaped, 0, 0, dw, dh);
  }
  // 2) Draw the AI image fresh, then multiply alpha by the soft mask.
  const ctx = layer.ctx;
  ctx.save();
  ctx.globalCompositeOperation = 'source-over';
  ctx.clearRect(0, 0, w, h);
  if (hasCrop) ctx.drawImage(ai, dx, dy, dw, dh);
  else ctx.drawImage(ai, 0, 0);
  ctx.globalCompositeOperation = 'destination-in';
  if (hasCrop) ctx.drawImage(softMask, dx, dy, dw, dh);
  else ctx.drawImage(softMask, 0, 0);
  ctx.restore();
}
