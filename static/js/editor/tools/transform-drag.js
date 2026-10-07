/**
 * Transform-drag tool — handle drag interactions for the Transform
 * tool (resize via corner/edge handles, rotation via the rot grip).
 *
 * The transform UI runs in TWO modes: the floating popup (W/H/rot
 * numeric inputs, lives elsewhere) AND direct drag on the canvas
 * handles. Both ultimately mutate `state.transformPendingW/H/Rot` and
 * call `reapplyTransform()` to redraw. This module owns the drag
 * branch.
 *
 * The dispatcher in galleryEditor.js calls `tryBegin/tryContinue/
 * tryEnd` which return `true` when the event was for the transform
 * tool and was handled (so the dispatcher can short-circuit).
 *
 * @param {{
 *   composite:              () => void,
 *   drawTransformHandles:   () => void,
 *   reapplyTransform:       () => void,
 *   getTransformHandle:     (x: number, y: number) => string | null,
 *   cursorForHandle:        (id: string | null) => string,
 *   pointInTransformFrame:  (x: number, y: number) => boolean,
 *   snapTransformFrame?:    (frame: object, center: {x:number,y:number}) => object,
 * }} deps
 */
import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';
import { resizeTransformFrame } from '../transform-frame-geometry.js';
import { createDirectManipulationSession } from '../direct-manipulation-session.js';

export function createTransformDragTool({
  composite, drawTransformHandles, reapplyTransform,
  getTransformHandle, cursorForHandle, pointInTransformFrame,
  snapTransformFrame,
}) {
  const gesture = createDirectManipulationSession({
    name: 'transform-frame',
    getContext: () => ({ canvas: state.mainCanvas, items: state.transformItems }),
    isContextCurrent: context => state.transformActive &&
      context.canvas === state.mainCanvas && context.items === state.transformItems,
    onCancel: () => {
      state.transformHandle = null;
      state.activeSnapGuides = null;
      composite();
      drawTransformHandles();
    },
  });
  return {
    /**
     * Called on pointerdown. Returns true if the transform tool handled
     * the event (the dispatcher should NOT fall through to other tools).
     */
    tryBegin(e) {
      if (!state.transformActive) return false;
      const coords = canvasCoords(e, state.mainCanvas);
      const pointerType = e.pointerType || (e.touches ? 'touch' : 'mouse');
      state.transformHandle = getTransformHandle(coords.x, coords.y, { pointerType });
      if (state.transformHandle) {
        state.transformStartX = coords.x;
        state.transformStartY = coords.y;
        // Snapshot offset + size at drag-start so each frame computes
        // "start + dx" (correct delta) rather than accumulating off the
        // running offset, which was making top/left grabs drift.
        const center = state.transformCenter || { x: 0, y: 0 };
        state.transformStartCenter = { ...center };
        state.transformOrigW = state.transformPendingW;
        state.transformOrigH = state.transformPendingH;
        state.transformStartRotation = state.transformPendingRot;
        state.transformStartFlipH = state.transformPendingFlipH;
        state.transformStartFlipV = state.transformPendingFlipV;
        state.transformStartOffX = center.x - state.transformOrigW / 2;
        state.transformStartOffY = center.y - state.transformOrigH / 2;
        gesture.begin(e, { handle: state.transformHandle }, { captureTarget: e.currentTarget });
        return true;
      }
      // No handle hit: dragging inside the staged frame moves the transform
      // itself. Keeping this in the transform session is essential for
      // selection transforms, which must never fall through to layer Move.
      if (pointInTransformFrame?.(coords.x, coords.y)) {
        state.transformHandle = 'move';
        state.transformStartX = coords.x;
        state.transformStartY = coords.y;
        state.transformStartCenter = { ...(state.transformCenter || { x: 0, y: 0 }) };
        gesture.begin(e, { handle: 'move' }, { captureTarget: e.currentTarget });
        return true;
      }
      return false;
    },

    /**
     * Called on pointermove. Returns true if handled.
     *
     * When transformActive but no handle is grabbed, updates the
     * hover cursor + pulse. When a handle is grabbed, drives the
     * resize / rotation pipeline.
     */
    tryContinue(e) {
      if (!state.transformActive) return false;
      // No drag in progress — just hover-cursor + pulse.
      if (!state.transformHandle && state.mainCanvas) {
        const coords = canvasCoords(e, state.mainCanvas);
        const pointerType = e.pointerType || (e.touches ? 'touch' : 'mouse');
        const hovered = getTransformHandle(coords.x, coords.y, { pointerType });
        const bodyHovered = !hovered && pointInTransformFrame?.(coords.x, coords.y);
        state.mainCanvas.style.cursor = hovered
          ? cursorForHandle(hovered, state.transformPendingRot)
          : bodyHovered ? 'move' : 'default';
        if (hovered !== state.hoveredHandle) {
          state.hoveredHandle = hovered;
          composite();
        }
        return false; // didn't fully consume the event
      }
      if (!state.transformHandle) return false;
      if (!gesture.update(e)) return false;
      e.preventDefault();
      const coords = canvasCoords(e, state.mainCanvas);
      if (state.transformHandle === 'move') {
        let center = {
          x: state.transformStartCenter.x + coords.x - state.transformStartX,
          y: state.transformStartCenter.y + coords.y - state.transformStartY,
        };
        const modifier = e.ctrlKey || e.metaKey;
        const snapping = state.snapEnabled ? !modifier : modifier;
        if (snapping && snapTransformFrame) {
          const snapped = snapTransformFrame({
            centerX: state.transformStartCenter.x,
            centerY: state.transformStartCenter.y,
            width: state.transformPendingW,
            height: state.transformPendingH,
            rotation: state.transformPendingRot,
          }, center);
          center = { x: snapped.centerX, y: snapped.centerY };
          state.activeSnapGuides = snapped.guides || null;
        } else {
          state.activeSnapGuides = null;
        }
        state.transformCenter = center;
        reapplyTransform();
        return true;
      }
      // Rotation grip — angle measured from the layer's geometric
      // centre to the cursor. Mirror into the popup if it's open.
      if (state.transformHandle === 'rot') {
        const cx = state.transformCenter?.x || 0;
        const cy = state.transformCenter?.y || 0;
        const rad = Math.atan2(coords.y - cy, coords.x - cx) + Math.PI / 2;
        let deg = Math.round((rad * 180) / Math.PI);
        if (e.shiftKey) deg = Math.round(deg / 15) * 15; // 15° snap
        while (deg > 180) deg -= 360;
        while (deg <= -180) deg += 360;
        state.transformPendingRot = deg;
        reapplyTransform();
        if (state.transformPopup) {
          const rotIn = state.transformPopup.querySelector('#ge-transform-rot');
          if (rotIn) rotIn.value = String(deg);
        }
        return true;
      }
      // Resize in the frame's LOCAL axes. This keeps rotated side handles
      // attached to the pointer and fixes the opposite edge in document space.
      const resized = resizeTransformFrame({
        centerX: state.transformStartCenter.x,
        centerY: state.transformStartCenter.y,
        width: state.transformOrigW,
        height: state.transformOrigH,
        rotation: state.transformStartRotation,
      }, state.transformHandle, {
        x: state.transformStartX,
        y: state.transformStartY,
      }, coords, {
        centered: !!e.altKey,
        lockAspect: !!e.shiftKey,
        flipH: state.transformStartFlipH,
        flipV: state.transformStartFlipV,
      });
      state.transformPendingW = resized.width;
      state.transformPendingH = resized.height;
      state.transformPendingFlipH = resized.flipH;
      state.transformPendingFlipV = resized.flipV;
      state.transformCenter = { x: resized.centerX, y: resized.centerY };
      reapplyTransform();
      // Mirror the new W/H into the popup if it's open.
      if (state.transformPopup) {
        const wIn = state.transformPopup.querySelector('#ge-transform-w');
        const hIn = state.transformPopup.querySelector('#ge-transform-h');
        if (wIn) wIn.value = String(state.transformPendingFlipH ? -resized.width : resized.width);
        if (hIn) hIn.value = String(state.transformPendingFlipV ? -resized.height : resized.height);
      }
      return true;
    },

    /**
     * Called on pointerup. Returns true if handled.
     */
    tryEnd(e) {
      if (!(state.transformActive && state.transformHandle)) return false;
      if (!gesture.commit(e)) return false;
      state.transformHandle = null;
      state.transformOrigW = state.transformPendingW;
      state.transformOrigH = state.transformPendingH;
      state.activeSnapGuides = null;
      composite();
      drawTransformHandles();
      return true;
    },
    cancel(reason) {
      return gesture.cancel(reason);
    },
  };
}
