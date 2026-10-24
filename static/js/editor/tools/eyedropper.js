import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';

function hexByte(value) {
  return Math.max(0, Math.min(255, value)).toString(16).padStart(2, '0');
}

// Average an N-by-N block centred on (cx, cy), clamped to the canvas edges.
// Photoshop's "Sample Size" option: 1 = Point Sample, 3/5/11/31/51 = N-by-N.
function averageBlock(ctx, cx, cy, size, width, height) {
  const half = Math.floor(size / 2);
  const x0 = Math.max(0, cx - half);
  const y0 = Math.max(0, cy - half);
  const x1 = Math.min(width - 1, cx + half);
  const y1 = Math.min(height - 1, cy + half);
  const w = x1 - x0 + 1;
  const h = y1 - y0 + 1;
  if (w <= 0 || h <= 0) return null;
  const data = ctx.getImageData(x0, y0, w, h).data;
  let r = 0, g = 0, b = 0, a = 0;
  const n = data.length / 4;
  for (let i = 0; i < data.length; i += 4) { r += data[i]; g += data[i + 1]; b += data[i + 2]; a += data[i + 3]; }
  return { r: Math.round(r / n), g: Math.round(g / n), b: Math.round(b / n), a: a / n };
}

export function createEyedropperTool({ activeLayer, composite, syncColor, syncPreview, showToast }) {
  function sample(e, refreshComposite = false) {
    const point = canvasCoords(e, state.mainCanvas);
    let canvas = state.mainCanvas;
    let x = point.x;
    let y = point.y;
    if (state.eyedropperSample === 'layer') {
      const layer = activeLayer();
      if (!layer?.canvas) return null;
      const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
      canvas = layer.canvas;
      x -= off.x;
      y -= off.y;
    } else if (refreshComposite) {
      composite();
    }
    const ix = Math.floor(x);
    const iy = Math.floor(y);
    if (ix < 0 || iy < 0 || ix >= canvas.width || iy >= canvas.height) return null;
    const ctx = canvas.getContext('2d');
    const size = Math.max(1, Math.round(Number(state.eyedropperSampleSize) || 1));
    let r, g, b, a;
    if (size <= 1) {
      const pixel = ctx.getImageData(ix, iy, 1, 1).data;
      r = pixel[0]; g = pixel[1]; b = pixel[2]; a = pixel[3];
    } else {
      const avg = averageBlock(ctx, ix, iy, size, canvas.width, canvas.height);
      if (!avg) return null;
      r = avg.r; g = avg.g; b = avg.b; a = avg.a;
    }
    const rgb = [r, g, b];
    if (!a) return { color: null, rgb, canvas, x, y };
    return { color: `#${hexByte(r)}${hexByte(g)}${hexByte(b)}`, rgb, canvas, x, y };
  }
  return {
    preview(e) {
      const result = sample(e);
      syncPreview?.(result?.color || null, result);
      return result?.color || null;
    },
    clearPreview() {
      syncPreview?.(null, null);
    },
    pick(e) {
      const result = sample(e, true);
      const color = result?.color;
      if (!color) {
        showToast?.('That pixel is transparent');
        return;
      }
      state.color = color;
      syncColor?.(state.color);
      syncPreview?.(state.color, result);
    },
  };
}
