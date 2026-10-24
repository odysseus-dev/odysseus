// Object Selection brush — drag a rough box over the subject and let the
// server-side segmentation model (SAM, via /api/image/mask) pick it out.
// The stroke is sampled into positive/negative points plus a bounding
// box, both already supported by the backend endpoint.

import { selectionModeForEvent } from '../selection-modifiers.js';

export function createObjectSelectTool({
  activeLayer,
  saveState,
  requestSam,
  canvasCoords,
  layerOffset,
  showToast,
  syncToolClearIndicators,
}) {
  let active = false;
  let layer = null;
  let points = [];
  let box = null;
  let mode = 'replace';

  const addPoint = (coords, event) => {
    if (!layer) return;
    const off = layerOffset(layer) || { x: 0, y: 0 };
    const lx = Math.floor(coords.x - off.x);
    const ly = Math.floor(coords.y - off.y);
    if (lx < 0 || ly < 0 || lx >= layer.canvas.width || ly >= layer.canvas.height) return;
    const last = points[points.length - 1];
    if (last && Math.hypot(lx - last.x, ly - last.y) < 8) return;
    points.push({ x: lx, y: ly, label: event.altKey ? 0 : 1 });
    if (!box) box = [lx, ly, lx, ly];
    else {
      box[0] = Math.min(box[0], lx);
      box[1] = Math.min(box[1], ly);
      box[2] = Math.max(box[2], lx);
      box[3] = Math.max(box[3], ly);
    }
  };

  const begin = (event) => {
    layer = activeLayer();
    if (!layer) {
      showToast?.('Select a layer');
      return;
    }
    active = true;
    points = [];
    box = null;
    mode = selectionModeForEvent(event, 'replace');
    const coords = canvasCoords(event);
    if (coords) addPoint(coords, event);
  };

  const drag = (event) => {
    if (!active) return;
    const coords = canvasCoords(event);
    if (coords) addPoint(coords, event);
  };

  const end = async () => {
    if (!active) return;
    active = false;
    const target = layer;
    const pts = points;
    const bx = points.length ? box : null;
    points = [];
    box = null;
    layer = null;
    if (!target || !pts.length) return;
    saveState();
    try {
      await requestSam(target, { points: pts, box: bx }, mode);
    } catch (err) {
      if (err?.name !== 'AbortError') showToast?.('Object selection failed');
    }
    syncToolClearIndicators?.();
  };

  const cancel = () => {
    active = false;
    points = [];
    box = null;
    layer = null;
  };

  return { begin, drag, end, cancel };
}
