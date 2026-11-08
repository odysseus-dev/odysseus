/**
 * Paint Bucket (flood fill) tool.
 *
 * `floodFillImageData` and `hexToRgb` are pure helpers (exported for tests);
 * `createBucketTool` fills the active layer's contiguous matching pixels —
 * confined to the active selection mask when there is one — like Photoshop's
 * contiguous Paint Bucket.
 */
import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';

export function hexToRgb(color) {
  const hex = String(color || '').replace('#', '');
  const full = hex.length === 3 ? hex.split('').map((c) => c + c).join('') : hex;
  if (full.length !== 6 || !/^[0-9a-fA-F]{6}$/.test(full)) return [0, 0, 0];
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function matches(data, i, target, tolerance) {
  return Math.abs(data[i] - target[0]) <= tolerance
    && Math.abs(data[i + 1] - target[1]) <= tolerance
    && Math.abs(data[i + 2] - target[2]) <= tolerance
    && Math.abs(data[i + 3] - target[3]) <= tolerance;
}

/**
 * Scanline flood fill over an RGBA buffer, in place.
 *
 * @param {Uint8ClampedArray} data RGBA pixels (width*height*4).
 * @param {number} width
 * @param {number} height
 * @param {number} startX
 * @param {number} startY
 * @param {[number,number,number]} rgb fill colour (alpha forced opaque).
 * @param {{tolerance?: number, mask?: Uint8ClampedArray|null}} options
 *   `mask` is an optional RGBA alpha buffer; only pixels whose mask alpha
 *   is >= 128 are eligible (confines the fill to a selection).
 * @returns {number} number of pixels filled.
 */
export function floodFillImageData(data, width, height, startX, startY, rgb, options = {}) {
  const tolerance = Math.max(0, Math.min(255, options.tolerance == null ? 32 : options.tolerance));
  const mask = options.mask || null;
  const x0 = Math.floor(startX);
  const y0 = Math.floor(startY);
  if (x0 < 0 || y0 < 0 || x0 >= width || y0 >= height) return 0;
  const startIdx = (y0 * width + x0) * 4;
  if (mask && mask[startIdx + 3] < 128) return 0;
  const target = [data[startIdx], data[startIdx + 1], data[startIdx + 2], data[startIdx + 3]];
  const fill = [rgb[0], rgb[1], rgb[2], 255];
  if (target[3] === 255
    && Math.abs(target[0] - fill[0]) <= tolerance
    && Math.abs(target[1] - fill[1]) <= tolerance
    && Math.abs(target[2] - fill[2]) <= tolerance) return 0;

  const visited = new Uint8Array(width * height);
  const eligible = (px, py) => {
    const vi = py * width + px;
    if (visited[vi]) return false;
    const i = vi * 4;
    if (mask && mask[i + 3] < 128) return false;
    return matches(data, i, target, tolerance);
  };

  const stack = [[x0, y0]];
  let filled = 0;
  while (stack.length) {
    const [sx, sy] = stack.pop();
    let x = sx;
    while (x >= 0 && eligible(x, sy)) x--;
    x++;
    let spanUp = false;
    let spanDown = false;
    while (x < width && eligible(x, sy)) {
      visited[sy * width + x] = 1;
      const i = (sy * width + x) * 4;
      data[i] = fill[0];
      data[i + 1] = fill[1];
      data[i + 2] = fill[2];
      data[i + 3] = 255;
      filled++;
      if (sy > 0) {
        const up = eligible(x, sy - 1);
        if (up && !spanUp) { stack.push([x, sy - 1]); spanUp = true; }
        else if (!up) spanUp = false;
      }
      if (sy < height - 1) {
        const down = eligible(x, sy + 1);
        if (down && !spanDown) { stack.push([x, sy + 1]); spanDown = true; }
        else if (!down) spanDown = false;
      }
      x++;
    }
  }
  return filled;
}

export function createBucketTool({ activeLayer, saveState, composite, renderLayerPanel, invalidateLayerCaches, showToast }) {
  function maskFor(layer) {
    if (state.wandMask
      && state.wandMask.width === layer.canvas.width
      && state.wandMask.height === layer.canvas.height) {
      return state.wandMask.getContext('2d')
        .getImageData(0, 0, layer.canvas.width, layer.canvas.height).data;
    }
    return null;
  }

  // Flood-fill `layer` at layer-local pixel (x, y). Returns pixels filled.
  function fillAtPoint(layer, x, y, { color, tolerance } = {}) {
    if (!layer?.canvas || !layer.ctx) return 0;
    const lx = Math.floor(x);
    const ly = Math.floor(y);
    if (lx < 0 || ly < 0 || lx >= layer.canvas.width || ly >= layer.canvas.height) return 0;
    const tol = Math.max(0, Math.min(255, tolerance == null ? (Number(state.bucketTolerance) || 0) : Number(tolerance)));
    const img = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    const count = floodFillImageData(
      img.data, layer.canvas.width, layer.canvas.height, lx, ly,
      hexToRgb(color || state.color), { tolerance: tol, mask: maskFor(layer) },
    );
    if (!count) return 0;
    saveState?.();
    layer.ctx.putImageData(img, 0, 0);
    invalidateLayerCaches?.(layer);
    composite?.();
    renderLayerPanel?.();
    return count;
  }

  // The active layer can be an adjustment layer (no raster canvas) or a
  // hidden/locked one; in that case fall back to the topmost visible raster
  // layer so a click still fills something instead of silently doing nothing.
  function paintableLayer() {
    const direct = activeLayer();
    if (direct?.canvas && direct.ctx && direct.kind !== 'adjustment') return direct;
    for (let i = state.layers.length - 1; i >= 0; i--) {
      const l = state.layers[i];
      if (l?.canvas && l.ctx && l.kind !== 'adjustment' && l.visible !== false) return l;
    }
    return null;
  }

  function fill(e) {
    const layer = paintableLayer();
    if (!layer) { showToast?.('Select a pixel layer to fill'); return 0; }
    if (state.activeLayerId !== layer.id) {
      state.activeLayerId = layer.id;
      state.selectedLayerIds = [layer.id];
      renderLayerPanel?.();
    }
    const point = canvasCoords(e, state.mainCanvas);
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const count = fillAtPoint(layer, point.x - off.x, point.y - off.y);
    if (!count) showToast?.('Nothing to fill there');
    return count;
  }

  return { fill, fillAtPoint };
}
