/**
 * Shared stroke pipeline for brush / eraser / inpaint.
 *
 * Per-sample stamping happens in `_strokeTo` (still in galleryEditor.js
 * because it touches a lot of pixel-pass internals). This module owns
 * the begin / continue / end orchestration around it:
 *
 *  - begin: capture the inpaint-erase flag for the stroke, ensure a
 *           mask sub-layer exists when inpaint runs against an empty
 *           layer, push an undo entry with a tool-specific label, then
 *           kick off the first stamp.
 *  - continue: forward the new cursor position to `_strokeTo`.
 *  - end: clear the drawing flag, composite, sync any tool indicators
 *         that reflect mask state.
 *
 * Clone has its own begin (see tools/clone.js) but reuses `continue`
 * and `end` because once a clone stroke is in progress, the pipeline
 * is identical.
 *
 * @param {{
 *   saveState:               (label: string) => void,
 *   strokeTo:                (x: number, y: number) => void,
 *   composite:               () => void,
 *   getActiveMaskLayer:      () => object | null,
 *   activeParentLayer:       () => object | null,
 *   ensureActiveMaskLayer:   () => object | null,
 *   createLayer:             (name: string, w: number, h: number) => object,
 *   renderLayerPanel:        () => void,
 *   syncToolClearIndicators: () => void,
 * }} deps
 */
import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';

const STROKE_TOOLS = new Set(['brush', 'eraser', 'inpaint', 'rembg']);

function strokeLabel(tool) {
  if (tool === 'brush') return 'Brush stroke';
  if (tool === 'eraser') return 'Eraser stroke';
  if (tool === 'inpaint') return state.inpaintEraseStroke ? 'Erase mask' : 'Paint mask';
  if (tool === 'rembg') return 'Background sample stroke';
  return 'Stroke';
}

export function createStrokeTool({
  saveState, strokeTo, composite,
  getActiveMaskLayer, activeParentLayer, ensureActiveMaskLayer, createLayer,
  renderLayerPanel, syncToolClearIndicators,
}) {
  return {
    /**
     * Begin a stroke. Returns true if the dispatcher should consider
     * the event handled (i.e. tool is one of brush/eraser/inpaint).
     */
    tryBegin(e) {
      if (!STROKE_TOOLS.has(state.tool)) return false;
      // Capture the inpaint-erase flag for this stroke. Ctrl+Alt
      // pressed at pointerdown flips the persistent toggle for one
      // stroke only.
      if (state.tool === 'inpaint') {
        const flip = e && e.ctrlKey && e.altKey;
        state.inpaintEraseStroke = flip ? !state.inpaintEraseMode : state.inpaintEraseMode;
        // Make sure we're painting onto an existing mask sub-layer. If
        // there's no parent layer at all, create one first so a totally
        // empty canvas can accept an inpaint stroke.
        if (!getActiveMaskLayer()) {
          let parent = activeParentLayer();
          if (!parent) {
            parent = createLayer('Layer 1', state.imgWidth, state.imgHeight);
            state.layers.push(parent);
            state.activeLayerId = parent.id;
          }
          if (parent.masks && parent.masks.length) {
            parent.activeMaskId = parent.masks[parent.masks.length - 1].id;
            const m = getActiveMaskLayer();
            if (m) {
              state.maskCanvas = m.canvas;
              state.maskCtx = m.ctx;
              renderLayerPanel();
            }
          } else {
            const mk = ensureActiveMaskLayer();
            if (mk) {
              state.maskCanvas = mk.canvas;
              state.maskCtx = mk.ctx;
              renderLayerPanel();
            }
          }
        }
      }
      saveState(strokeLabel(state.tool));
      state.drawing = true;
      const coords = canvasCoords(e, state.mainCanvas);
      state.lastX = coords.x;
      state.lastY = coords.y;
      strokeTo(coords.x, coords.y);
      return true;
    },

    /**
     * Forward an in-progress stroke. Returns true if a stroke is
     * actually in progress (dispatcher should short-circuit).
     */
    tryContinue(e) {
      if (!state.drawing) return false;
      e.preventDefault();
      const coords = canvasCoords(e, state.mainCanvas);
      strokeTo(coords.x, coords.y);
      return true;
    },

    /**
     * Wrap up an in-progress stroke. Returns true if there was one.
     */
    tryEnd() {
      if (!state.drawing) return false;
      const wasDrawingInpaint = state.tool === 'inpaint';
      state.drawing = false;
      composite();
      if (wasDrawingInpaint) syncToolClearIndicators();
      return true;
    },
  };
}
