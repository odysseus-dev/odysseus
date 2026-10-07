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
 *   undo:                    () => void,
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
import { isLayerPixelLocked, isLayerTransparencyLocked } from '../layer-groups.js';

const STROKE_TOOLS = new Set(['brush', 'eraser', 'smudge', 'dodge', 'burn', 'inpaint']);

function strokeLabel(tool) {
  if (tool === 'brush') return 'Brush stroke';
  if (tool === 'eraser') return 'Eraser stroke';
  if (tool === 'inpaint') return state.inpaintEraseStroke ? 'Erase mask' : 'Paint mask';
  if (tool === 'smudge') return 'Smudge stroke';
  if (tool === 'dodge') return 'Dodge stroke';
  if (tool === 'burn') return 'Burn stroke';
  return 'Stroke';
}

function pointerPressure(e) {
  if (e?.touches?.[0] && Number.isFinite(e.touches[0].force) && e.touches[0].force > 0) return e.touches[0].force;
  return Number.isFinite(e?.pressure) && e.pressure > 0 ? e.pressure : 1;
}

function eventSample(e) {
  const coords = canvasCoords(e, state.mainCanvas);
  return { x: coords.x, y: coords.y, pressure: pointerPressure(e) };
}

export function createStrokeTool({
  saveState, undo, beginStroke, strokeTo, endStroke, composite,
  getActiveMaskLayer, activeParentLayer, ensureActiveMaskLayer, createLayer,
  renderLayerPanel, syncToolClearIndicators, showToast,
}) {
  return {
    /**
     * Begin a stroke. Returns true if the dispatcher should consider
     * the event handled (i.e. tool is one of brush/eraser/inpaint).
     */
    tryBegin(e) {
      if (!STROKE_TOOLS.has(state.tool)) return false;
      const parent = activeParentLayer();
      const activeMask = state.quickMaskActive && state.wandMask
        ? { mode: 'quick-selection', canvas: state.wandMask, ctx: state.wandMask.getContext('2d') }
        : getActiveMaskLayer();
      if (['brush', 'eraser', 'smudge', 'dodge', 'burn'].includes(state.tool) && !activeMask && parent?.kind === 'placed') {
        showToast?.('Rasterize the placed layer before painting its pixels');
        return true;
      }
      if (['brush', 'eraser', 'smudge', 'dodge', 'burn'].includes(state.tool) && !activeMask && isLayerPixelLocked(state, parent)) {
        showToast?.('Unlock image pixels before painting');
        return true;
      }
      if (state.tool === 'eraser' && !activeMask && isLayerTransparencyLocked(state, parent)) {
        showToast?.('Unlock transparent pixels before erasing');
        return true;
      }
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
      beginStroke(eventSample(e), state.tool);
      return true;
    },

    /**
     * Forward an in-progress stroke. Returns true if a stroke is
     * actually in progress (dispatcher should short-circuit).
     */
    tryContinue(e) {
      if (!state.drawing) return false;
      e.preventDefault();
      const events = typeof e.getCoalescedEvents === 'function' ? e.getCoalescedEvents() : [];
      const samples = events.length ? events : [e];
      for (const sampleEvent of samples) strokeTo(eventSample(sampleEvent));
      return true;
    },

    /**
     * Wrap up an in-progress stroke. Returns true if there was one.
     */
    tryEnd(e) {
      if (!state.drawing) return false;
      const wasDrawingInpaint = state.tool === 'inpaint';
      state.drawing = false;
      endStroke(e ? eventSample(e) : null);
      composite();
      // Strokes composite every frame while drawing, but the layer panel is
      // intentionally not rebuilt at that frequency. Refresh once at the end
      // so inline layer/group thumbnails reflect the completed edit.
      renderLayerPanel();
      if (wasDrawingInpaint) syncToolClearIndicators();
      return true;
    },

    cancel() {
      if (!state.drawing) return false;
      state.drawing = false;
      state.cloneSourceSnapshot = null;
      undo?.();
      state.redoStack = [];
      composite();
      renderLayerPanel();
      syncToolClearIndicators();
      return true;
    },
  };
}
