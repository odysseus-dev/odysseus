/**
 * Vector pen / path tool — click to drop anchors, double-click (or the
 * Finish button / Enter) to close the path. The draft is rendered live as
 * a `shape` layer of type `path`; the caller owns layer creation, geometry
 * updates and cleanup so this module stays free of editor state.
 */

export function createPenPathTool({
  saveState = () => {},
  canvasCoords = () => ({ x: 0, y: 0 }),
  mainCanvas = null,
  ensurePathLayer = () => null,
  drawPath = () => {},
  commitPath = () => {},
  cancelPath = () => {},
  composite = () => {},
  syncToolClearIndicators = () => {},
} = {}) {
  let draft = null;

  function addPoint(e) {
    const point = canvasCoords(e, mainCanvas);
    if (!Number.isFinite(point?.x) || !Number.isFinite(point?.y)) return false;
    if (!draft) {
      saveState('Add path');
      const layer = ensurePathLayer();
      if (!layer) return false;
      draft = { layer, points: [point] };
    } else {
      draft.points.push(point);
    }
    drawPath(draft.layer, draft.points);
    composite();
    return true;
  }

  function begin(e) {
    addPoint(e);
    if (e && e.detail >= 2) finish();
    return true;
  }

  function drag() {}
  function end() {}

  function finish() {
    if (!draft) return false;
    if (draft.points.length >= 2) {
      commitPath(draft.layer, draft.points);
    } else {
      cancelPath(draft.layer);
    }
    draft = null;
    composite();
    syncToolClearIndicators();
    return true;
  }

  function cancel() {
    if (!draft) return false;
    cancelPath(draft.layer);
    draft = null;
    composite();
    return true;
  }

  return { begin, drag, end, finish, cancel, hasDraft: () => !!draft };
}
