/**
 * Crop tool — drag-rect selection that lets the user cut down the
 * canvas to a smaller region. Supports Shift-lock aspect ratio and
 * click-inside-rect to reposition an existing crop without redrawing.
 *
 * Owns its own begin/drag/end handlers and reads/writes shared state.
 * The factory takes a small dependency bag for things still living in
 * galleryEditor.js — `composite` redraws the canvas, `showCropApply`
 * mounts the floating W×H + Apply panel after the user finishes
 * dragging.
 *
 * @param {{
 *   composite: () => void,
 *   showCropApply: () => void,
 * }} deps
 */
import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';
import { drawCheckerboard } from '../checkerboard.js';
import { createDirectManipulationSession } from '../direct-manipulation-session.js';

export function createCropTool({ composite, showCropApply, snapFrame }) {
  const gesture = createDirectManipulationSession({
    name: 'crop',
    getContext: () => ({ canvas: state.mainCanvas, tool: state.tool }),
    isContextCurrent: context => context.canvas === state.mainCanvas && state.tool === 'crop',
    onCancel: (data, reason) => {
      state.cropping = false;
      state.cropMoving = false;
      state.cropStart = null;
      state.cropEnd = null;
      state.cropMoveStart = null;
      state.cropAspectLock = null;
      state.activeSnapGuides = null;
      state.cropRect = data?.previousRect ? { ...data.previousRect } : null;
      composite();
      if (state.cropRect && reason !== 'tool-switch') showCropApply();
    },
  });
  return {
    begin(e) {
      const coords = canvasCoords(e, state.mainCanvas);
      const previousRect = state.cropRect ? { ...state.cropRect } : null;
      // Click inside an existing crop rect → switch to move-mode so
      // the user can reposition without redrawing.
      if (state.cropRect &&
          coords.x >= state.cropRect.x && coords.x <= state.cropRect.x + state.cropRect.w &&
          coords.y >= state.cropRect.y && coords.y <= state.cropRect.y + state.cropRect.h) {
        state.cropMoving = true;
        state.cropMoveStart = { x: coords.x, y: coords.y, rx: state.cropRect.x, ry: state.cropRect.y };
        gesture.begin(e, { mode: 'move', previousRect }, { captureTarget: e.currentTarget });
        return;
      }
      state.cropping = true;
      state.cropStart = coords;
      state.cropEnd = { ...state.cropStart };
      state.cropRect = null;
      state.cropAspectLock = null;
      // Tear down the size panel while the user is drawing a new rect.
      const old = state.container?.querySelector('.ge-crop-apply');
      if (old) old.remove();
      gesture.begin(e, { mode: 'draw', previousRect }, { captureTarget: e.currentTarget });
    },

    drag(e) {
      if (!gesture.update(e)) return;
      // Move-mode: drag the existing rect around the canvas.
      if (state.cropMoving && state.cropRect && state.cropMoveStart) {
        e.preventDefault();
        const c = canvasCoords(e, state.mainCanvas);
        const dx = c.x - state.cropMoveStart.x;
        const dy = c.y - state.cropMoveStart.y;
        let nx = state.cropMoveStart.rx + dx;
        let ny = state.cropMoveStart.ry + dy;
        const modifier = e.ctrlKey || e.metaKey;
        const snapping = state.snapEnabled ? !modifier : modifier;
        if (snapping && snapFrame) {
          const snapped = snapFrame({
            centerX: state.cropMoveStart.rx + state.cropRect.w / 2,
            centerY: state.cropMoveStart.ry + state.cropRect.h / 2,
            width: state.cropRect.w,
            height: state.cropRect.h,
            rotation: 0,
          }, { x: nx + state.cropRect.w / 2, y: ny + state.cropRect.h / 2 });
          nx = snapped.centerX - state.cropRect.w / 2;
          ny = snapped.centerY - state.cropRect.h / 2;
          state.activeSnapGuides = snapped.guides || null;
        } else {
          state.activeSnapGuides = null;
        }
        // Clamp to canvas bounds so the rect stays fully visible.
        nx = Math.max(0, Math.min(nx, state.mainCanvas.width - state.cropRect.w));
        ny = Math.max(0, Math.min(ny, state.mainCanvas.height - state.cropRect.h));
        state.cropRect = { ...state.cropRect, x: nx, y: ny };
        composite();
        return;
      }
      if (!state.cropping) return;
      e.preventDefault();
      state.cropEnd = canvasCoords(e, state.mainCanvas);
      // Shift-held = lock aspect ratio. First Shift press during the
      // drag snapshots the current aspect; subsequent moves stay locked.
      // Releasing Shift resets so the user can re-lock at a new ratio.
      if (e.shiftKey) {
        const rawDx = state.cropEnd.x - state.cropStart.x;
        const rawDy = state.cropEnd.y - state.cropStart.y;
        if (state.cropAspectLock == null) {
          const rawW = Math.abs(rawDx) || 1;
          const rawH = Math.abs(rawDy) || 1;
          state.cropAspectLock = rawW / rawH;
        }
        const absDx = Math.abs(rawDx);
        const absDy = Math.abs(rawDy);
        // Whichever axis the user moved more (relative to the lock) is
        // the driver; scale the other to preserve aspect.
        let dx, dy;
        if (absDx >= absDy * state.cropAspectLock) {
          dx = rawDx;
          dy = Math.sign(rawDy || 1) * (absDx / state.cropAspectLock);
        } else {
          dy = rawDy;
          dx = Math.sign(rawDx || 1) * (absDy * state.cropAspectLock);
        }
        state.cropEnd = { x: state.cropStart.x + dx, y: state.cropStart.y + dy };
      } else {
        state.cropAspectLock = null;
      }
      composite();
      // Draw crop overlay.
      const x = Math.min(state.cropStart.x, state.cropEnd.x);
      const y = Math.min(state.cropStart.y, state.cropEnd.y);
      const w = Math.abs(state.cropEnd.x - state.cropStart.x);
      const h = Math.abs(state.cropEnd.y - state.cropStart.y);
      state.mainCtx.fillStyle = 'rgba(0,0,0,0.4)';
      state.mainCtx.fillRect(0, 0, state.mainCanvas.width, state.mainCanvas.height);
      state.mainCtx.clearRect(x, y, w, h);
      // Redraw layers inside the crop rect (dim everything outside).
      state.mainCtx.save();
      state.mainCtx.beginPath();
      state.mainCtx.rect(x, y, w, h);
      state.mainCtx.clip();
      drawCheckerboard(state.mainCtx, state.mainCanvas.width, state.mainCanvas.height);
      for (const layer of state.layers) {
        if (!layer.visible) continue;
        state.mainCtx.globalAlpha = layer.opacity;
        const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
        state.mainCtx.drawImage(layer.canvas, off.x, off.y);
      }
      state.mainCtx.globalAlpha = 1;
      state.mainCtx.restore();
      // Dashed border around the kept region.
      state.mainCtx.strokeStyle = '#fff';
      state.mainCtx.lineWidth = 1;
      state.mainCtx.setLineDash([4, 4]);
      state.mainCtx.strokeRect(x, y, w, h);
      state.mainCtx.setLineDash([]);
      state.cropRect = { x, y, w, h };
    },

    end(e) {
      if (!gesture.commit(e)) return;
      state.activeSnapGuides = null;
      // Move-mode wrap-up: refresh the floating panel so Apply follows
      // the rect to its new spot.
      if (state.cropMoving) {
        state.cropMoving = false;
        state.cropMoveStart = null;
        if (state.cropRect) showCropApply();
        return;
      }
      state.cropping = false;
      if (state.cropRect && state.cropRect.w > 5 && state.cropRect.h > 5) {
        showCropApply();
      }
    },
    cancel(reason) {
      return gesture.cancel(reason);
    },
  };
}
