/** Shape boolean operations via canvas compositing. */

export const SHAPE_BOOLEAN_OPS = Object.freeze(['unite', 'subtract', 'intersect', 'exclude']);

const OP_COMPOSITE = Object.freeze({
  unite: 'source-over',
  subtract: 'destination-out',
  intersect: 'destination-in',
  exclude: 'xor',
});

/**
 * Combine layer canvases into a single document-sized canvas.
 * @param {Array<{canvas: HTMLCanvasElement}>} layers bottom-to-top order
 * @param {string} op one of SHAPE_BOOLEAN_OPS
 * @param {{width:number, height:number, offsets?: Array<{x:number,y:number}>}} options
 * @returns {HTMLCanvasElement|null}
 */
export function combineLayers(layers, op, { width = 0, height = 0, offsets = [] } = {}) {
  const list = Array.isArray(layers) ? layers.filter(layer => layer && layer.canvas) : [];
  if (!list.length) return null;
  const mode = SHAPE_BOOLEAN_OPS.includes(op) ? op : 'unite';
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width) || list[0].canvas.width);
  canvas.height = Math.max(1, Math.round(height) || list[0].canvas.height);
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  const base = list[0];
  const baseOffset = offsets[0] || { x: 0, y: 0 };
  if (mode === 'unite') {
    ctx.globalCompositeOperation = 'source-over';
    list.forEach((layer, index) => {
      const offset = offsets[index] || { x: 0, y: 0 };
      ctx.drawImage(layer.canvas, offset.x, offset.y);
    });
  } else {
    ctx.globalCompositeOperation = 'source-over';
    ctx.drawImage(base.canvas, baseOffset.x, baseOffset.y);
    ctx.globalCompositeOperation = OP_COMPOSITE[mode];
    for (let index = 1; index < list.length; index += 1) {
      const layer = list[index];
      const offset = offsets[index] || { x: 0, y: 0 };
      ctx.drawImage(layer.canvas, offset.x, offset.y);
    }
  }
  ctx.globalCompositeOperation = 'source-over';
  return canvas;
}
