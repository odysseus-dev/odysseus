// Quick Selection brush — drag to grow a wand-style selection.
// Reuses the existing magic-wand flood fill by re-sampling along the
// stroke; intermediate samples skip the undo snapshot (retune mode) so a
// whole drag collapses to one history entry (taken on begin).

import { selectionModeForEvent } from '../selection-modifiers.js';

export function createQuickSelectTool({
  activeLayer,
  saveState,
  composite,
  runMagicWand,
  canvasCoords,
  brushSize,
  syncToolClearIndicators,
}) {
  let painting = false;
  let last = null;

  const modeFor = (event) => selectionModeForEvent(event, 'add');

  const sample = (event) => {
    const coords = canvasCoords(event);
    if (!coords) return;
    runMagicWand(coords.x, coords.y, modeFor(event), { retune: true });
  };

  const begin = (event) => {
    const layer = activeLayer();
    if (!layer) return;
    saveState();
    painting = true;
    last = null;
    sample(event);
    const coords = canvasCoords(event);
    if (coords) last = coords;
  };

  const drag = (event) => {
    if (!painting) return;
    const coords = canvasCoords(event);
    if (!coords) return;
    const gap = Math.max(3, (brushSize?.() || 24) / 4);
    if (last && Math.hypot(coords.x - last.x, coords.y - last.y) < gap) return;
    sample(event);
    last = coords;
  };

  const end = () => {
    if (!painting) return;
    painting = false;
    last = null;
    composite();
    syncToolClearIndicators?.();
  };

  const cancel = () => {
    painting = false;
    last = null;
  };

  return { begin, drag, end, cancel };
}
