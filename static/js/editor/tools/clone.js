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
 *   onSourceChanged?: () => void,
 * }} deps
 */
import { state } from '../state.js';
import { isLayerPixelLocked } from '../layer-groups.js';
import { canvasCoords } from '../canvas-coords.js';

export function createCloneTool({ activeLayer, saveState, beginStroke, showToast, onSourceChanged, getSampleCanvas }) {
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
        const sampledCanvas = getSampleCanvas?.({ layer, mode: state.cloneSampleMode });
        const sourceLayer = layer && (
          state.cloneSampleMode === 'active-layer' || !sampledCanvas
        ) ? layer : null;
        const sourceOffset = sourceLayer ? (state.layerOffsets.get(sourceLayer.id) || { x: 0, y: 0 }) : { x: 0, y: 0 };
        state.cloneSourceOffsetX = sourceOffset.x;
        state.cloneSourceOffsetY = sourceOffset.y;
        state.cloneSourceSnapshot = null; // captured at first stroke
        onSourceChanged?.();
        showToast('Clone source set');
        return;
      }
      // Healing defaults to source-free spot correction. Once a source is
      // chosen, the same tool switches to sampled healing in the pipeline.
      if (state.tool === 'heal' && (state.cloneSourceX === null || state.cloneSourceY === null)) {
        if (!layer || isLayerPixelLocked(state, layer)) {
          showToast('Unlock image pixels before healing');
          return;
        }
        if (layer.kind === 'placed') {
          showToast('Rasterize the placed layer before healing its pixels');
          return;
        }
        saveState('Healing stroke');
        state.drawing = true;
        state.lastX = coords.x;
        state.lastY = coords.y;
        beginStroke({ x: coords.x, y: coords.y, pressure: Number(e.pressure) > 0 ? e.pressure : 1 }, state.tool);
        return;
      }
      if (state.cloneSourceX === null || state.cloneSourceY === null) {
        showToast(isTouchEvt
          ? 'Double-tap first to set a clone source'
          : 'Alt-click first to set a clone source');
        return;
      }
      if (!layer || isLayerPixelLocked(state, layer)) {
        showToast('Unlock image pixels before cloning');
        return;
      }
      if (layer.kind === 'placed') {
        showToast('Rasterize the placed layer before cloning into it');
        return;
      }
      const toolName = state.tool === 'heal' ? 'Healing stroke' : 'Clone stroke';
      saveState(toolName);
      // Snapshot the source layer's pixels at stroke-start so the
      // brush samples clean source pixels even after it has painted
      // over them. Otherwise we'd cascade-clone the same ring.
      const srcLayer = state.layers.find(l => l.id === state.cloneSourceLayerId) || layer;
      const sourceCanvas = getSampleCanvas?.({ layer: srcLayer, mode: state.cloneSampleMode }) || srcLayer.canvas;
      const snap = document.createElement('canvas');
      snap.width = sourceCanvas.width;
      snap.height = sourceCanvas.height;
      snap.getContext('2d').drawImage(sourceCanvas, 0, 0);
      state.cloneSourceSnapshot = snap;
      state.cloneStrokeStartX = coords.x;
      state.cloneStrokeStartY = coords.y;
      state.drawing = true;
      state.lastX = coords.x;
      state.lastY = coords.y;
      beginStroke({ x: coords.x, y: coords.y, pressure: Number(e.pressure) > 0 ? e.pressure : 1 }, state.tool);
    },
  };
}
