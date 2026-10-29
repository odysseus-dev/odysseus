/**
 * Lasso tool — freehand polygon selection. Mouse-down starts a fresh
 * polygon; every move appends a point and redraws the dashed outline;
 * mouse-up keeps the selection visible (the panel's action buttons
 * read `state.lassoPoints` to act on it).
 *
 * Magnetic sub-mode: when `state.lassoMagnetic` is on, each sampled
 * point is snapped to the strongest local image gradient within
 * `state.lassoMagneticWidth` px, so the outline hugs edges. Edge
 * strength below `state.lassoMagneticContrast` (0-100) is ignored.
 *
 * Owns its own begin/drag/end handlers and reads/writes shared state.
 *
 * @param {{
 *   activeLayer:               () => object | null,
 *   saveState:                 (label?: string) => void,
 *   commitSelectionMask:       (mask, layer, mode, source, offset) => void,
 *   composite:                 () => void,
 *   drawLassoOverlay:          () => void,
 *   syncToolClearIndicators:   () => void,
 * }} deps
 */
import { state } from '../state.js';
import { selectionModeForEvent } from '../selection-modifiers.js';
import { canvasCoords } from '../canvas-coords.js';
import { buildLassoMask } from './lasso-mask.js';

// Luminance-gradient magnitude field for the active layer, used by the
// magnetic sub-mode. Built once per stroke; null when unavailable.
function buildEdgeField(layer, width, height) {
  if (!layer || !layer.ctx || !width || !height) return null;
  try {
    const data = layer.ctx.getImageData(0, 0, width, height).data;
    const lum = new Float32Array(width * height);
    for (let i = 0; i < width * height; i++) {
      lum[i] = 0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2];
    }
    const mag = new Float32Array(width * height);
    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        const i = y * width + x;
        const gx = lum[i + 1] - lum[i - 1];
        const gy = lum[i + width] - lum[i - width];
        mag[i] = Math.abs(gx) + Math.abs(gy);
      }
    }
    return { mag, width, height };
  } catch {
    return null;
  }
}

function snapToEdge(field, x, y, radius, contrast) {
  const r = Math.max(1, Math.round(radius));
  const threshold = contrast * 2.55;
  let best = null;
  let bestScore = -Infinity;
  const cx = Math.round(x);
  const cy = Math.round(y);
  for (let dy = -r; dy <= r; dy++) {
    for (let dx = -r; dx <= r; dx++) {
      const px = cx + dx;
      const py = cy + dy;
      if (px < 1 || py < 1 || px >= field.width - 1 || py >= field.height - 1) continue;
      const m = field.mag[py * field.width + px];
      if (m < threshold) continue;
      const score = m - Math.hypot(dx, dy) * 2;
      if (score > bestScore) {
        bestScore = score;
        best = { x: px, y: py };
      }
    }
  }
  return best;
}

export function createLassoTool({
  activeLayer, saveState, commitSelectionMask,
  composite, drawLassoOverlay, syncToolClearIndicators,
}) {
  let pendingMode = 'replace';
  let edgeField = null;
  return {
    begin(e) {
      pendingMode = selectionModeForEvent(e, state.wandMode || 'replace');
      state.lassoPoints = [];
      state.lassoActive = true;
      edgeField = state.lassoMagnetic
        ? buildEdgeField(activeLayer?.(), state.imgWidth, state.imgHeight)
        : null;
      const coords = canvasCoords(e, state.mainCanvas);
      state.lassoPoints.push(coords);
    },

    drag(e) {
      if (!state.lassoActive) return;
      e.preventDefault();
      let coords = canvasCoords(e, state.mainCanvas);
      if (state.lassoMagnetic && edgeField) {
        const snapped = snapToEdge(
          edgeField,
          coords.x,
          coords.y,
          state.lassoMagneticWidth || 12,
          state.lassoMagneticContrast ?? 20,
        );
        if (snapped) coords = snapped;
      }
      const last = state.lassoPoints[state.lassoPoints.length - 1];
      if (last && Math.hypot(coords.x - last.x, coords.y - last.y) < 1) return;
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
      edgeField = null;
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
      edgeField = null;
      state.lassoPoints = [];
      composite();
      syncToolClearIndicators();
      return true;
    },
  };
}
