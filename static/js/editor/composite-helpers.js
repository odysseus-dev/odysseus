/**
 * Pure composite helpers — flatten a layer list into a single canvas
 * for thumbnails / merged-mask use.
 *
 * Both helpers are stateless: the caller passes everything they need
 * (layer list, canvas dimensions, an offsets lookup). The legacy
 * gallery editor's module-level functions wrap these with their own
 * state.
 */

/**
 * Cheap downscaled preview composited from all visible layers.
 * Returns a JPEG dataURL, or null when there's nothing to draw.
 *
 * @param {Array<{visible: boolean, opacity: number, id: string, canvas: HTMLCanvasElement}>} layers
 * @param {number} imgW            Document width in canvas pixels.
 * @param {number} imgH            Document height in canvas pixels.
 * @param {Map<string,{x:number,y:number}>} offsets  Layer offsets, keyed by id.
 * @param {number} maxDim          Longest-edge target in CSS pixels.
 * @param {number} quality         JPEG quality 0..1.
 * @returns {string|null}
 */
export function buildThumbnail(layers, imgW, imgH, offsets, maxDim, quality = 0.6) {
  if (!imgW || !imgH) return null;
  try {
    const scale = Math.min(1, maxDim / Math.max(imgW, imgH));
    const tw = Math.max(1, Math.round(imgW * scale));
    const th = Math.max(1, Math.round(imgH * scale));
    const c = document.createElement('canvas');
    c.width = tw; c.height = th;
    const ctx = c.getContext('2d');
    for (const layer of layers) {
      if (!layer.visible) continue;
      ctx.globalAlpha = layer.opacity;
      const off = offsets.get(layer.id) || { x: 0, y: 0 };
      ctx.drawImage(
        layer.canvas,
        off.x * scale, off.y * scale,
        layer.canvas.width * scale, layer.canvas.height * scale,
      );
    }
    ctx.globalAlpha = 1;
    return c.toDataURL('image/jpeg', quality);
  } catch (_) {
    return null;
  }
}


/**
 * Union of every visible mask sub-layer across `layers`, rendered as a
 * binary white canvas the size of the document.
 *
 * `lighter` composite = additive — overlapping pixels stay clamped at
 * 255, so wherever any mask painted, the result is solid white.
 * Returns null when no mask layer contributed any pixels (so the caller
 * can early-out cleanly).
 *
 * @param {Array<{masks?: Array<{visible: boolean, canvas: HTMLCanvasElement}>}>} layers
 * @param {number} imgW
 * @param {number} imgH
 * @returns {HTMLCanvasElement|null}
 */
export function buildMergedMaskCanvas(layers, imgW, imgH) {
  if (!imgW || !imgH) return null;
  const out = document.createElement('canvas');
  out.width = imgW;
  out.height = imgH;
  const ctx = out.getContext('2d');
  ctx.globalCompositeOperation = 'lighter';
  let anyMask = false;
  for (const ly of layers) {
    if (!ly.masks || !ly.masks.length) continue;
    for (const mk of ly.masks) {
      if (mk.mode === 'layer') continue;
      if (!mk.visible) continue;
      if (!mk.canvas || !mk.canvas.width || !mk.canvas.height) continue;
      ctx.drawImage(mk.canvas, 0, 0);
      anyMask = true;
    }
  }
  ctx.globalCompositeOperation = 'source-over';
  return anyMask ? out : null;
}


/** Apply visible true layer masks to an already-adjusted layer canvas. */
export function renderWithLayerMasks(source, layer, layerOffset = { x: 0, y: 0 }) {
  const masks = (layer?.masks || []).filter(mask => mask.mode === 'layer' && mask.visible !== false);
  if (!source || masks.length === 0) return source;
  const out = document.createElement('canvas');
  out.width = source.width;
  out.height = source.height;
  const ctx = out.getContext('2d');
  ctx.drawImage(source, 0, 0);
  ctx.globalCompositeOperation = 'destination-in';
  for (const mask of masks) {
    ctx.globalAlpha = Number.isFinite(Number(mask.density))
      ? Math.max(0, Math.min(1, Number(mask.density)))
      : 1;
    const feather = Number.isFinite(Number(mask.feather))
      ? Math.max(0, Math.min(200, Number(mask.feather)))
      : 0;
    ctx.filter = feather > 0 ? `blur(${feather}px)` : 'none';
    if (mask.space === 'document') {
      ctx.drawImage(mask.canvas, -(layerOffset.x || 0), -(layerOffset.y || 0));
    } else {
      const offset = mask.offset && typeof mask.offset === 'object' ? mask.offset : { x: 0, y: 0 };
      ctx.drawImage(mask.canvas, Number(offset.x) || 0, Number(offset.y) || 0);
    }
  }
  ctx.globalAlpha = 1;
  ctx.filter = 'none';
  ctx.globalCompositeOperation = 'source-over';
  return out;
}
