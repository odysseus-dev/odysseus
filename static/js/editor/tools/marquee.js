/** Rectangle and ellipse marquee interaction producing a document-space mask. */

import { state } from '../state.js';
import { selectionModeForEvent } from '../selection-modifiers.js';
import { canvasCoords } from '../canvas-coords.js';
import {
  createMarqueeMask,
  mergeSelectionMasks,
  normalizeConstrainedSelectionRect,
  selectionMaskBounds,
  selectionMaskContains,
  selectionMaskToDocument,
  translateSelectionMask,
} from '../selection-mask.js';
import { createDirectManipulationSession } from '../direct-manipulation-session.js';

export function createMarqueeTool({
  activeLayer, saveState, composite, drawOverlay, syncSelectionUi,
  ensureDocumentSelection, snapFrame,
}) {
  let pendingMode = 'replace';
  let moveHistorySaved = false;
  const gesture = createDirectManipulationSession({
    name: 'selection-boundary',
    getContext: () => ({ canvas: state.mainCanvas, tool: state.tool }),
    isContextCurrent: context => context.canvas === state.mainCanvas && state.tool === 'marquee',
    onCancel: data => {
      if (data?.mode === 'move' && state.selectionMoveOrigin) {
        state.wandMask = translateSelectionMask(
          state.selectionMoveOrigin, 0, 0, state.imgWidth, state.imgHeight,
        );
        state.wandMaskSpace = 'document';
        if (moveHistorySaved && state.undoStack?.at(-1)?._label === 'Move selection boundary') {
          state.undoStack.pop();
        }
      }
      state.selectionMoving = false;
      state.selectionMoveStart = null;
      state.selectionMoveOrigin = null;
      state.marqueeActive = false;
      state.marqueeStart = null;
      state.marqueeRect = null;
      state.activeSnapGuides = null;
      moveHistorySaved = false;
      composite();
      syncSelectionUi();
    },
  });

  return {
    begin(e) {
      const layer = activeLayer();
      if (!layer) return;
      const coords = canvasCoords(e, state.mainCanvas);
      const selection = ensureDocumentSelection?.() || null;
      if (selection && !e.shiftKey && !e.altKey && selectionMaskContains(selection, coords.x, coords.y)) {
        state.selectionMoving = true;
        state.selectionMoveStart = coords;
        state.selectionMoveOrigin = translateSelectionMask(selection, 0, 0, state.imgWidth, state.imgHeight);
        moveHistorySaved = false;
        gesture.begin(e, { mode: 'move' }, { captureTarget: e.currentTarget });
        return;
      }
      pendingMode = selectionModeForEvent(e, state.wandMode || 'replace');
      state.marqueeStart = coords;
      state.marqueeRect = normalizeConstrainedSelectionRect(
        coords,
        coords,
        state.imgWidth,
        state.imgHeight,
        {
          mode: state.marqueeConstraint,
          ratioWidth: state.marqueeRatioWidth,
          ratioHeight: state.marqueeRatioHeight,
          fixedWidth: state.marqueeFixedWidth,
          fixedHeight: state.marqueeFixedHeight,
        },
      );
      state.marqueeActive = true;
      gesture.begin(e, { mode: 'draw' }, { captureTarget: e.currentTarget });
    },

    drag(e) {
      if (!gesture.update(e)) return;
      if (state.selectionMoving && state.selectionMoveStart && state.selectionMoveOrigin) {
        e.preventDefault();
        const coords = canvasCoords(e, state.mainCanvas);
        let dx = Math.round(coords.x - state.selectionMoveStart.x);
        let dy = Math.round(coords.y - state.selectionMoveStart.y);
        if (!dx && !dy) return;
        const bounds = selectionMaskBounds(state.selectionMoveOrigin);
        const modifier = e.ctrlKey || e.metaKey;
        const snapping = state.snapEnabled ? !modifier : modifier;
        if (bounds && snapping && snapFrame) {
          const center = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
          const snapped = snapFrame({
            centerX: center.x,
            centerY: center.y,
            width: bounds.width,
            height: bounds.height,
            rotation: 0,
          }, { x: center.x + dx, y: center.y + dy });
          dx = Math.round(snapped.centerX - center.x);
          dy = Math.round(snapped.centerY - center.y);
          state.activeSnapGuides = snapped.guides || null;
        } else {
          state.activeSnapGuides = null;
        }
        if (!moveHistorySaved) {
          saveState('Move selection boundary');
          moveHistorySaved = true;
        }
        state.wandMask = translateSelectionMask(
          state.selectionMoveOrigin,
          dx,
          dy,
          state.imgWidth,
          state.imgHeight,
        );
        state.wandMaskSpace = 'document';
        state.wandLastSeed = null;
        composite();
        return;
      }
      if (!state.marqueeActive || !state.marqueeStart) return;
      e.preventDefault();
      state.marqueeRect = normalizeConstrainedSelectionRect(
        state.marqueeStart,
        canvasCoords(e, state.mainCanvas),
        state.imgWidth,
        state.imgHeight,
        {
          mode: state.marqueeConstraint,
          ratioWidth: state.marqueeRatioWidth,
          ratioHeight: state.marqueeRatioHeight,
          fixedWidth: state.marqueeFixedWidth,
          fixedHeight: state.marqueeFixedHeight,
          square: !!e.shiftKey,
        },
      );
      composite();
      drawOverlay();
    },

    end(e) {
      if (!gesture.commit(e)) return;
      state.activeSnapGuides = null;
      if (state.selectionMoving) {
        state.selectionMoving = false;
        state.selectionMoveStart = null;
        state.selectionMoveOrigin = null;
        moveHistorySaved = false;
        composite();
        syncSelectionUi();
        return;
      }
      if (!state.marqueeActive) return;
      state.marqueeActive = false;
      const rect = state.marqueeRect;
      state.marqueeStart = null;
      state.marqueeRect = null;
      if (!rect || rect.w < 1 || rect.h < 1) {
        if (pendingMode === 'replace' && state.wandMask) {
          saveState('Clear selection');
          state.wandMask = null;
          state.wandLayerId = null;
          state.wandMaskSpace = 'layer';
          state.selectionSource = null;
          state.wandLastSeed = null;
        }
        composite();
        syncSelectionUi();
        return;
      }

      saveState(`${state.marqueeShape === 'ellipse' ? 'Ellipse' : 'Rectangle'} selection`);
      const active = activeLayer();
      const selectedLayer = state.layers.find(layer => layer.id === state.wandLayerId);
      const selectedOffset = selectedLayer ? (state.layerOffsets.get(selectedLayer.id) || { x: 0, y: 0 }) : { x: 0, y: 0 };
      const current = selectionMaskToDocument(
        state.wandMask,
        state.wandMaskSpace || 'layer',
        selectedOffset,
        state.imgWidth,
        state.imgHeight,
      );
      const incoming = createMarqueeMask(
        state.imgWidth,
        state.imgHeight,
        rect,
        state.marqueeShape,
      );
      state.wandMask = mergeSelectionMasks(current, incoming, pendingMode);
      state.wandLayerId = active?.id || state.activeLayerId;
      state.wandMaskSpace = 'document';
      state.selectionSource = 'marquee';
      state.wandLastSeed = null;
      state.wandMaskVisible = true;
      state.lassoPoints = [];
      state.lassoActive = false;
      composite();
      syncSelectionUi();
    },
    cancel(reason) {
      return gesture.cancel(reason);
    },
  };
}
