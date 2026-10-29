// Auto-Select hit-testing (Move tool). Walks the layer stack front-to-back
// and returns the topmost layer whose rendered pixels are opaque at the
// requested document point. Kept dependency-free so it can run under node
// for unit tests; the caller injects layer-geometry and pixel sampling.

export const AUTO_SELECT_ALPHA_THRESHOLD = 8;

export function pickTopmostLayerAt({
  layers,
  point,
  isHidden = () => false,
  isLocked = () => false,
  getOffset = () => ({ x: 0, y: 0 }),
  sampleAlpha,
} = {}) {
  if (!Array.isArray(layers) || !point || typeof sampleAlpha !== 'function') return null;
  // Front-to-back: the last layer in the array paints on top.
  for (let index = layers.length - 1; index >= 0; index -= 1) {
    const layer = layers[index];
    if (!layer?.visible || layer.kind === 'adjustment' || !layer.canvas) continue;
    if (isHidden(layer)) continue;
    if (isLocked(layer)) continue;
    const offset = getOffset(layer) || { x: 0, y: 0 };
    const x = Math.floor(point.x - offset.x);
    const y = Math.floor(point.y - offset.y);
    if (x < 0 || y < 0 || x >= layer.canvas.width || y >= layer.canvas.height) continue;
    const alpha = sampleAlpha(layer, x, y);
    if (alpha != null && alpha > AUTO_SELECT_ALPHA_THRESHOLD) return layer;
  }
  return null;
}
