// Small, dependency-light mask helpers used by the editor-control mask
// operations (add-mask-from-selection, apply/invert/feather) and per-layer
// vision ops (get bounds). Pure data helpers are exported so they can be unit
// tested in Node without a real canvas.

// Bounding box of non-transparent pixels. `data` is RGBA (Uint8ClampedArray or
// plain array), row-major, length width*height*4. Returns {x,y,w,h} or null.
export function alphaBounds(data, width, height, threshold = 8) {
  if (!data || !(width > 0) || !(height > 0)) return null;
  let minX = width, minY = height, maxX = -1, maxY = -1;
  for (let y = 0; y < height; y++) {
    const row = y * width;
    for (let x = 0; x < width; x++) {
      const a = data[(row + x) * 4 + 3];
      if (a > threshold) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return null;
  return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
}

// Invert a mask stored as white RGB + alpha (selected = alpha 255). Returns a
// NEW array; the source is not mutated.
export function invertMaskData(data) {
  const out = new Uint8ClampedArray(data.length);
  for (let i = 0; i < data.length; i += 4) {
    out[i] = 255;
    out[i + 1] = 255;
    out[i + 2] = 255;
    out[i + 3] = 255 - data[i + 3];
  }
  return out;
}

// Blur (feather) a mask canvas into a fresh canvas. `createCanvas(w,h)` must
// return a canvas-like object with getContext('2d'). Returns the new canvas.
export function featherMaskCanvas(createCanvas, srcCanvas, radius) {
  const w = srcCanvas.width, h = srcCanvas.height;
  const out = createCanvas(w, h);
  const ctx = out.getContext('2d');
  const r = Math.max(0, Number(radius) || 0);
  if (r > 0 && 'filter' in ctx) ctx.filter = `blur(${r}px)`;
  ctx.drawImage(srcCanvas, 0, 0);
  if (r > 0 && 'filter' in ctx) ctx.filter = 'none';
  return out;
}

// Bake a mask into a layer by multiplying the layer alpha (destination-in).
export function bakeMaskIntoLayer(layerCtx, maskCanvas, offsetX = 0, offsetY = 0) {
  layerCtx.save();
  layerCtx.globalCompositeOperation = 'destination-in';
  layerCtx.drawImage(maskCanvas, offsetX, offsetY);
  layerCtx.restore();
  return true;
}

export default { alphaBounds, invertMaskData, featherMaskCanvas, bakeMaskIntoLayer };
