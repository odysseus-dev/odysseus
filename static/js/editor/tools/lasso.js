/**
 * Lasso tool — freehand polygon selection. Mouse-down starts a fresh
 * polygon; every move appends a point and redraws the dashed outline;
 * mouse-up keeps the selection visible (the panel's action buttons
 * read `state.lassoPoints` to act on it).
 *
 * Owns its own begin/drag/end handlers and reads/writes shared state.
 *
 * @param {{
 *   composite:                 () => void,
 *   drawLassoOverlay:          () => void,
 *   syncToolClearIndicators:   () => void,
 * }} deps
 */
import { state } from '../state.js';
import { selectionModeForEvent } from '../selection-modifiers.js';
import { canvasCoords } from '../canvas-coords.js';
import { buildLassoMask } from './lasso-mask.js';

export function createLassoTool({
  activeLayer, saveState, commitSelectionMask,
  composite, drawLassoOverlay, syncToolClearIndicators,
}) {
  let pendingMode = 'replace';
  return {
    begin(e) {
      pendingMode = selectionModeForEvent(e, state.wandMode || 'replace');
      state.lassoPoints = [];
      state.lassoActive = true;
      const coords = canvasCoords(e, state.mainCanvas);
      state.lassoPoints.push(coords);
    },

    drag(e) {
      if (!state.lassoActive) return;
      e.preventDefault();
      const coords = canvasCoords(e, state.mainCanvas);
      state.lassoPoints.push(coords);
      // Live overlay: dashed white outline + translucent red fill.
      composite();
      if (state.lassoPoints.length > 1) {
        state.mainCtx.beginPath();
        state.mainCtx.moveTo(state.lassoPoints[0].x, state.lassoPoints[0].y);
        for (let i = 1; i < state.lassoPoints.length; i++) {
          state.mainCtx.lineTo(state.lassoPoints[i].x, state.lassoPoints[i].y);
        }
        state.mainCtx.closePath();
        state.mainCtx.strokeStyle = '#fff';
        state.mainCtx.lineWidth = 1 / state.zoom;
        state.mainCtx.setLineDash([4 / state.zoom, 4 / state.zoom]);
        state.mainCtx.stroke();
        state.mainCtx.setLineDash([]);
        state.mainCtx.fillStyle = 'rgba(255, 80, 80, 0.15)';
        state.mainCtx.fill();
      }
    },

    end() {
      state.lassoActive = false;
      if (state.lassoPoints.length < 3) {
        state.lassoPoints = [];
        composite();
        syncToolClearIndicators();
        return;
      }
      const layer = activeLayer?.();
      if (!layer) return;
      saveState?.('Lasso selection');
      const mask = buildLassoMask(
        state.lassoPoints,
        state.imgWidth,
        state.imgHeight,
        0,
        0,
        0,
        0,
      );
      commitSelectionMask?.(mask, layer, pendingMode, 'lasso', { x: 0, y: 0 });
      state.wandMaskVisible = true;
      state.wandLastSeed = null;
      state.lassoPoints = [];
      composite();
      syncToolClearIndicators();
    },

    cancel() {
      if (!state.lassoActive && !state.lassoPoints.length) return false;
      state.lassoActive = false;
      state.lassoPoints = [];
      composite();
      syncToolClearIndicators();
      return true;
    },
  };
}
