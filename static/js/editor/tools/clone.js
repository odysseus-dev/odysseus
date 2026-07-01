/**
 * Clone tool — Alt-click (desktop) or double-tap (mobile) sets the
 * sample source; a regular click+drag stamps from that source onto the
 * active layer. The source point moves WITH the brush so the offset
 * stays constant across the stroke.
 *
 * begin() handles the source-pick and stroke-start branches; the
 * actual per-sample stamping continues through the shared stroke
 * pipeline (`_strokeTo`) which knows about clone-mode internally.
 *
 * @param {{
 *   activeLayer: () => object | null,
 *   saveState:   (label?: string) => void,
 *   strokeTo:    (x: number, y: number) => void,
 *   showToast:   (msg: string) => void,
 * }} deps
 */
import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';

export function createCloneTool({ activeLayer, saveState, strokeTo, showToast }) {
  return {
    begin(e) {
      const layer = activeLayer();
      const coords = canvasCoords(e, state.mainCanvas);
      // Mobile equivalent of Alt-click: double-tap in screen pixels.
      // Wider tolerances (500 ms, 40 px) than desktop because finger
      // taps drift more than mouse clicks.
      const isTouchEvt = e.type && e.type.startsWith('touch');
      let isDoubleTap = false;
      if (isTouchEvt) {
        const t = e.touches ? e.touches[0] : null;
        const cx = t ? t.clientX : 0;
        const cy = t ? t.clientY : 0;
        const now = Date.now();
        const dt = now - state.cloneLastTapTime;
        const dx = cx - state.cloneLastTapX;
        const dy = cy - state.cloneLastTapY;
        if (dt < 500 && Math.hypot(dx, dy) < 40) {
          isDoubleTap = true;
          state.cloneLastTapTime = 0; // consume the pair
        } else {
          state.cloneLastTapTime = now;
          state.cloneLastTapX = cx;
          state.cloneLastTapY = cy;
        }
      }
      if (e.altKey || isDoubleTap) {
        state.cloneSourceX = coords.x;
        state.cloneSourceY = coords.y;
        state.cloneSourceLayerId = (layer && layer.id) || state.activeLayerId;
        state.cloneSourceSnapshot = null; // captured at first stroke
        showToast(window.t('Clone source set'));
        return;
      }
      if (state.cloneSourceX === null || state.cloneSourceY === null) {
        showToast(isTouchEvt
          ? window.t('Double-tap first to set a clone source')
          : window.t('Alt-click first to set a clone source'));
        return;
      }
      if (!layer || layer.locked) return;
      saveState('Clone stroke');
      // Snapshot the source layer's pixels at stroke-start so the
      // brush samples clean source pixels even after it has painted
      // over them. Otherwise we'd cascade-clone the same ring.
      const srcLayer = state.layers.find(l => l.id === state.cloneSourceLayerId) || layer;
      const snap = document.createElement('canvas');
      snap.width = srcLayer.canvas.width;
      snap.height = srcLayer.canvas.height;
      snap.getContext('2d').drawImage(srcLayer.canvas, 0, 0);
      state.cloneSourceSnapshot = snap;
      state.cloneStrokeStartX = coords.x;
      state.cloneStrokeStartY = coords.y;
      state.drawing = true;
      state.lastX = coords.x;
      state.lastY = coords.y;
      strokeTo(coords.x, coords.y);
    },
  };
}
