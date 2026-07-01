/**
 * Whole-document transforms: rotate by 90/180/270° or flip horizontal/
 * vertical. These mutate every layer's canvas + the offset map + the
 * document's overall width/height so the result feels like the whole
 * image rotated as one piece.
 *
 * Pure-ish — reads/writes shared state directly; the factory takes a
 * small dep bag for the orchestration plumbing (undo snapshot, canvas
 * loading overlay, fit-zoom-to-viewport, composite redraw).
 *
 * @param {{
 *   saveState:           (label?: string) => void,
 *   composite:           () => void,
 *   fitZoom:             () => void,
 *   showCanvasLoading:   (label: string) => void,
 *   hideCanvasLoading:   () => void,
 * }} deps
 */
import { state } from './state.js';

export function createCanvasTransforms({ saveState, composite, fitZoom, showCanvasLoading, hideCanvasLoading }) {
  return {
    /**
     * Rotate the entire document by `deg` (90 / 180 / 270). 90 and 270
     * swap canvas dimensions. Each layer is rotated around its own
     * centre, then its centre is rotated around the old image centre
     * and translated into the new image's frame.
     *
     * Wrapped in requestAnimationFrame because the rotation pass can
     * block the UI for 0.5–2 s on big images — the spinner overlay
     * paints before we block.
     */
    rotateAll(deg) {
      if (!state.layers.length) return;
      saveState(window.t('Rotate {deg}°', { deg }));
      showCanvasLoading(window.t('Rotating…'));
      const oldW = state.imgWidth, oldH = state.imgHeight;
      const swap = (deg === 90 || deg === 270);
      const newW = swap ? oldH : oldW;
      const newH = swap ? oldW : oldH;
      const rad = (deg * Math.PI) / 180;
      const cos = Math.cos(rad), sin = Math.sin(rad);
      requestAnimationFrame(() => {
        try {
          for (const layer of state.layers) {
            const lw = layer.canvas.width, lh = layer.canvas.height;
            const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
            // Layer centre in old image coords.
            const cx = off.x + lw / 2;
            const cy = off.y + lh / 2;
            // Rotate the centre around the old image centre and
            // translate so the new image centre lands at (newW/2, newH/2).
            const dx = cx - oldW / 2;
            const dy = cy - oldH / 2;
            const nx = dx * cos - dy * sin + newW / 2;
            const ny = dx * sin + dy * cos + newH / 2;
            // New per-layer dims: swap when 90/270.
            const newLw = swap ? lh : lw;
            const newLh = swap ? lw : lh;
            const tmp = document.createElement('canvas');
            tmp.width = newLw; tmp.height = newLh;
            const tctx = tmp.getContext('2d');
            tctx.translate(newLw / 2, newLh / 2);
            tctx.rotate(rad);
            tctx.drawImage(layer.canvas, -lw / 2, -lh / 2);
            layer.canvas.width = newLw;
            layer.canvas.height = newLh;
            layer.ctx.drawImage(tmp, 0, 0);
            // The adjustment-render caches are keyed only by the adjustment
            // signature, which rotation doesn't change — so composite would draw
            // the STALE pre-rotation cache (the "had to click twice" bug). Drop
            // them so the next composite re-renders from the rotated canvas.
            layer._adjCacheKey = null;
            layer._adjFinalKey = null;
            state.layerOffsets.set(layer.id, {
              x: Math.round(nx - newLw / 2),
              y: Math.round(ny - newLh / 2),
            });
          }
          state.imgWidth = newW;
          state.imgHeight = newH;
          state.mainCanvas.width = newW;
          state.mainCanvas.height = newH;
          if (state.maskCanvas) {
            state.maskCanvas.width = newW;
            state.maskCanvas.height = newH;
          }
          const sizeLabel = document.getElementById('ge-canvas-size');
          if (sizeLabel) sizeLabel.textContent = `${newW}×${newH}`;
          fitZoom();
          composite();
        } finally {
          hideCanvasLoading();
        }
      });
    },

    /**
     * Mirror every layer horizontally ('h') or vertically ('v').
     * Canvas dimensions don't change. Each layer offset is reflected
     * around the image centre.
     */
    flipAll(axis) {
      if (!state.layers.length) return;
      saveState(axis === 'h' ? window.t('Flip horizontal') : window.t('Flip vertical'));
      for (const layer of state.layers) {
        const lw = layer.canvas.width, lh = layer.canvas.height;
        const tmp = document.createElement('canvas');
        tmp.width = lw; tmp.height = lh;
        const tctx = tmp.getContext('2d');
        tctx.save();
        if (axis === 'h') { tctx.translate(lw, 0); tctx.scale(-1, 1); }
        else              { tctx.translate(0, lh); tctx.scale(1, -1); }
        tctx.drawImage(layer.canvas, 0, 0);
        tctx.restore();
        layer.ctx.clearRect(0, 0, lw, lh);
        layer.ctx.drawImage(tmp, 0, 0);
        // Invalidate the adjustment-render caches (keyed by adjustment sig only)
        // so composite redraws from the flipped canvas, not a stale cache.
        layer._adjCacheKey = null;
        layer._adjFinalKey = null;
        const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
        if (axis === 'h') {
          state.layerOffsets.set(layer.id, { x: state.imgWidth - off.x - lw, y: off.y });
        } else {
          state.layerOffsets.set(layer.id, { x: off.x, y: state.imgHeight - off.y - lh });
        }
      }
      composite();
    },
  };
}
