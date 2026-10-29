// Patch tool — with a selection active, drag from inside it to a clean
// source region; on release the selected content is replaced by that
// source region (feathered at the seam). The pixel work lives in
// ../patch-ops.js; this module only tracks the drag offset.
//
// begin()/drag()/end() are dispatched from the canvas event handler the
// same way as the object-selection brush.

import { state } from '../state.js';

export function createPatchTool({
  activeLayer,
  canvasCoords,
  saveState,
  showToast,
  onApply,
}) {
  let active = false;
  let start = null;

  const begin = (event) => {
    const layer = activeLayer();
    if (!layer) {
      showToast?.('Select a layer to patch');
      return;
    }
    if (!state.wandMask) {
      showToast?.('Make a selection, then drag the Patch tool to a source area');
      return;
    }
    const coords = canvasCoords(event);
    if (!coords) return;
    start = coords;
    state.patchDx = 0;
    state.patchDy = 0;
    active = true;
    if (state.mainCanvas) state.mainCanvas.style.cursor = 'copy';
  };

  const drag = (event) => {
    if (!active || !start) return;
    const coords = canvasCoords(event);
    if (!coords) return;
    state.patchDx = Math.round(coords.x - start.x);
    state.patchDy = Math.round(coords.y - start.y);
  };

  const end = () => {
    if (!active) return;
    active = false;
    start = null;
    if (state.mainCanvas) state.mainCanvas.style.cursor = '';
    const dx = Number(state.patchDx) || 0;
    const dy = Number(state.patchDy) || 0;
    state.patchDx = 0;
    state.patchDy = 0;
    if (dx === 0 && dy === 0) return;
    saveState?.('Patch');
    onApply?.(dx, dy);
  };

  const cancel = () => {
    active = false;
    start = null;
    if (state.mainCanvas) state.mainCanvas.style.cursor = '';
  };

  return { begin, drag, end, cancel };
}
