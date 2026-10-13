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
 *   renderLayerPanel:    () => void,
 * }} deps
 */
import { state } from './state.js';
import { flipDocument, rotateDocument } from './document-geometry.js';

export function createCanvasTransforms({
  saveState, composite, fitZoom, showCanvasLoading, hideCanvasLoading, renderLayerPanel,
}) {
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
      saveState(`Rotate ${deg}°`);
      showCanvasLoading('Rotating…');
      requestAnimationFrame(() => {
        try {
          const result = rotateDocument(state, deg);
          const sizeLabel = document.getElementById('ge-canvas-size');
          if (sizeLabel && result) sizeLabel.textContent = `${result.width}×${result.height}`;
          fitZoom();
          composite();
          renderLayerPanel?.();
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
      saveState(axis === 'h' ? 'Flip horizontal' : 'Flip vertical');
      flipDocument(state, axis);
      composite();
      renderLayerPanel?.();
    },
  };
}
