/**
 * Local two-color gradient tool.
 *
 * The drag becomes a retained effect on completion, so previews never mutate
 * source pixels or compound across pointer-move events.
 */
import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';
import { isLayerPixelLocked } from '../layer-groups.js';

export function createGradientTool({ activeLayer, saveState, composite, schedulePersist, renderLayerPanel, getSettings }) {
  let start = null;
  let lastPoint = null;
  let activeLayerId = null;
  let historySaved = false;

  function settings() {
    const values = getSettings?.() || {};
    return {
      start: values.start || state.gradientStart || state.color,
      mid: values.mid || state.gradientMid || '#808080',
      midPosition: values.midPosition ?? state.gradientMidPosition ?? 50,
      midEnabled: values.midEnabled ?? state.gradientMidEnabled ?? false,
      extraStops: Array.isArray(values.extraStops) ? values.extraStops : (state.gradientStops || []),
      end: values.end || state.gradientEnd || '#ffffff',
      endAlpha: values.endAlpha ?? state.gradientEndAlpha ?? 100,
      opacity: values.opacity ?? state.gradientOpacity ?? 100,
      type: values.type || state.gradientType || 'linear-gradient',
    };
  }

  function preview(point) {
    const layer = activeLayer();
    if (!start || !layer || layer.id !== activeLayerId) return false;
    const dx = point.x - start.x;
    const dy = point.y - start.y;
    if (Math.hypot(dx, dy) < 0.5) return false;
    const values = settings();
    const stops = [
      { position: 0, color: values.start, alpha: 1 },
      ...(values.midEnabled ? [{ position: Number(values.midPosition), color: values.mid, alpha: 1 }] : []),
      ...values.extraStops.slice(0, 10).map(stop => ({
        position: Math.max(1, Math.min(99, Number(stop.position) || 50)),
        color: stop.color || '#808080',
        alpha: 1,
      })),
      { position: 100, color: values.end, alpha: Math.max(0, Math.min(100, Number(values.endAlpha))) / 100 },
    ].sort((a, b) => a.position - b.position);
    layer._effectPreview = {
      id: 'gradient-preview', type: values.type, name: values.type === 'radial-gradient' ? 'Radial Gradient' : 'Gradient', visible: true, opacity: 1,
      params: { x1: start.x, y1: start.y, x2: point.x, y2: point.y, stops, opacity: Math.max(0, Math.min(100, Number(values.opacity))) / 100 },
    };
    composite();
    return true;
  }

  return {
    begin(e) {
      const layer = activeLayer();
      if (!layer) return false;
      if (isLayerPixelLocked(state, layer)) return false;
      if (layer.kind === 'placed') return false;
      activeLayerId = layer.id;
      start = canvasCoords(e, state.mainCanvas);
      lastPoint = { ...start };
      state.gradientActive = true;
      historySaved = false;
      return true;
    },

    drag(e) {
      if (!state.gradientActive) return false;
      lastPoint = canvasCoords(e, state.mainCanvas);
      preview(lastPoint);
      return true;
    },

    end(e) {
      if (!state.gradientActive) return false;
      const point = e && (Number.isFinite(e.clientX) || (e.touches && e.touches.length))
        ? canvasCoords(e, state.mainCanvas)
        : lastPoint;
      const changed = point ? preview(point) : false;
      const layer = activeLayer();
      state.gradientActive = false;
      if (layer?._effectPreview && changed) {
        if (!historySaved) {
          saveState('Gradient');
          historySaved = true;
        }
        layer.effects = Array.isArray(layer.effects) ? layer.effects : [];
        layer.effects.push(layer._effectPreview);
      }
      if (layer) layer._effectPreview = null;
      start = null;
      lastPoint = null;
      activeLayerId = null;
      historySaved = false;
      if (changed) {
        renderLayerPanel?.();
        schedulePersist?.();
      }
      return true;
    },

    cancel() {
      if (!state.gradientActive) return false;
      const layer = activeLayer();
      if (layer) layer._effectPreview = null;
      state.gradientActive = false;
      start = null;
      lastPoint = null;
      activeLayerId = null;
      historySaved = false;
      composite();
      return true;
    },
  };
}
