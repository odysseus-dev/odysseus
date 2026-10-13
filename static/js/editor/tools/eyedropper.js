import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';

function hexByte(value) {
  return Math.max(0, Math.min(255, value)).toString(16).padStart(2, '0');
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
    if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) return null;
    const pixel = canvas.getContext('2d').getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
    const rgb = [pixel[0], pixel[1], pixel[2]];
    if (!pixel[3]) return { color: null, rgb, canvas, x, y };
    return { color: `#${hexByte(pixel[0])}${hexByte(pixel[1])}${hexByte(pixel[2])}`, rgb, canvas, x, y };
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
