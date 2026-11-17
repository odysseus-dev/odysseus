/**
 * Magic-wand tool — single-click flood-fill selection on the active
 * layer's pixels. Shift/Alt modifiers override the persistent mode
 * toggle for the duration of the click (add / subtract).
 *
 * Clicking inside an existing selection with no modifier deselects.
 *
 * Wand is selection-only — it doesn't mutate the layer until the user
 * invokes an action (Erase / Copy / etc.) from the panel. That's why
 * it has just a `click` handler instead of begin/drag/end.
 *
 * @param {{
 *   activeLayer: () => object | null,
 *   saveState:   () => void,
 *   composite:   () => void,
 *   wandHits:    (cx: number, cy: number) => boolean,
 *   runMagicWand: (cx: number, cy: number, mode: 'replace'|'add'|'subtract') => void,
 * }} deps
 */
import { state } from '../state.js';
import { selectionModeForEvent } from '../selection-modifiers.js';
import { canvasCoords } from '../canvas-coords.js';

export function createWandTool({ activeLayer, saveState, composite, wandHits, runMagicWand, deselectSelection }) {
  return {
    click(e) {
      const layer = activeLayer();
      if (!layer) return;
      const coords = canvasCoords(e, state.mainCanvas);
      // Persistent toggle sets the default mode; Shift forces add, Alt
      // forces subtract regardless of the toggle (modifiers always win).
      const mode = selectionModeForEvent(e, state.wandMode || 'replace');
      // Click INSIDE the existing selection with no modifier → deselect.
      if (mode === 'replace' && wandHits(coords.x, coords.y)) {
        if (deselectSelection) {
          deselectSelection();
          return;
        }
        saveState();
        state.wandMask = null;
        state.wandLayerId = null;
        state.wandMaskSpace = 'layer';
        state.selectionSource = null;
        state.wandLastSeed = null;
        composite();
        return;
      }
      runMagicWand(coords.x, coords.y, mode);
    },
  };
}
