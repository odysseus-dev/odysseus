/**
 * Move tool — drag a layer around the canvas, with configurable snapping
 * to other layers' edges/centers and to canvas edges/center.
 *
 * Owns its own input handlers (begin/drag/end) and reads/writes the
 * shared `state` store directly. The factory takes a small dependency
 * bag for things that still live in galleryEditor.js — `activeLayer`,
 * `saveState`, `composite` — so this module doesn't have to know about
 * the orchestrator.
 *
 * @param {{
 *   activeLayer: () => {id: string, canvas: HTMLCanvasElement, locked?: boolean} | null,
 *   saveState:   (label?: string) => void,
 *   composite:   () => void,
 *   onPositionChange?: () => void,
 * }} deps
 * @returns {{ begin: (e: Event) => void, drag: (e: Event) => void, end: () => void }}
 */
import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';
import { computeSnap as computeSnapImpl } from '../snap.js';
import { selectedLayers, selectionLabel } from '../layer-selection.js';
import { isLayerPositionLocked } from '../layer-groups.js';
import { clonePlacedData, translatePlacedData } from '../placed-layer.js';

export function createMoveTool({ activeLayer, saveState, composite, onPositionChange }) {
  function computeSnap(layer, nx, ny) {
    const selectedIds = new Set(state.selectedLayerIds || []);
    return computeSnapImpl(layer, nx, ny, {
      zoom: state.zoom,
      canvasW: state.imgWidth,
      canvasH: state.imgHeight,
      otherLayers: state.layers.filter(l => !selectedIds.has(l.id)).map(l => ({
        visible: l.visible,
        id: l.id,
        canvas: l.canvas,
        offset: state.layerOffsets.get(l.id) || { x: 0, y: 0 },
      })),
      verticalGuides: state.guides?.vertical || [],
      horizontalGuides: state.guides?.horizontal || [],
      snapToGrid: !!state.snapToGrid,
      gridSize: state.gridSize || 16,
    });
  }

  return {
    begin(e) {
      const layer = activeLayer();
      if (!layer || isLayerPositionLocked(state, layer)) return;
      const activeMask = (layer.masks || []).find(mask =>
        mask.id === layer.activeMaskId && mask.mode === 'layer' && mask.linked === false);
      state.moving = true;
      state.moveHistorySaved = false;
      const coords = canvasCoords(e, state.mainCanvas);
      const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
      state.moveStartX = coords.x;
      state.moveStartY = coords.y;
      state.moveLayerOffsetX = off.x;
      state.moveLayerOffsetY = off.y;
      state.moveMaskTarget = activeMask || null;
      state.moveMaskOffsetX = Number(activeMask?.offset?.x) || 0;
      state.moveMaskOffsetY = Number(activeMask?.offset?.y) || 0;
      state.moveLayerOffsets = new Map(selectedLayers(state)
        .filter(item => !isLayerPositionLocked(state, item))
        .map(item => [
        item.id,
        { ...(state.layerOffsets.get(item.id) || { x: 0, y: 0 }) },
      ]));
      state.movePlacedSources = new Map(selectedLayers(state)
        .filter(item => item.kind === 'placed' && item.placed)
        .map(item => [item.id, clonePlacedData(item.placed)]));
      state.moveUnlinkedMaskOffsets = new Map();
      for (const item of selectedLayers(state)) {
        for (const mask of item.masks || []) {
          if (mask.mode !== 'layer' || mask.linked !== false) continue;
          state.moveUnlinkedMaskOffsets.set(mask.id, {
            mask,
            x: Number(mask.offset?.x) || 0,
            y: Number(mask.offset?.y) || 0,
          });
        }
      }
    },
    drag(e) {
      if (!state.moving) return;
      e.preventDefault();
      const layer = activeLayer();
      if (!layer) return;
      const coords = canvasCoords(e, state.mainCanvas);
      const dx = coords.x - state.moveStartX;
      const dy = coords.y - state.moveStartY;
      if (state.moveMaskTarget) {
        const mask = state.moveMaskTarget;
        const nx = Math.round(state.moveMaskOffsetX + dx);
        const ny = Math.round(state.moveMaskOffsetY + dy);
        const before = mask.offset || { x: 0, y: 0 };
        if (nx === before.x && ny === before.y) return;
        if (!state.moveHistorySaved) {
          saveState(`Move mask "${mask.name || 'Layer Mask'}"`);
          state.moveHistorySaved = true;
        }
        mask.offset = { x: nx, y: ny };
        state.activeSnapGuides = null;
        composite();
        return;
      }
      let nx = state.moveLayerOffsetX + dx;
      let ny = state.moveLayerOffsetY + dy;
      // Ctrl/Cmd temporarily inverts the persistent Snap preference.
      const modifier = e.ctrlKey || e.metaKey;
      const snapping = state.snapEnabled ? !modifier : modifier;
      if (snapping) {
        const snapped = computeSnap(layer, nx, ny);
        nx = snapped.x;
        ny = snapped.y;
        state.activeSnapGuides = snapped.guides;
      } else {
        state.activeSnapGuides = null;
      }
      const before = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
      if (nx === before.x && ny === before.y) return;
      if (!state.moveHistorySaved) {
        // Transform already owns one pre-session snapshot. A second Move
        // snapshot would make Cancel restore the staged transform instead of
        // the original layer.
        if (!state.transformActive) saveState(`Move ${selectionLabel(state, `"${layer.name || 'layer'}"`)}`);
        state.moveHistorySaved = true;
      }
      const appliedDx = nx - state.moveLayerOffsetX;
      const appliedDy = ny - state.moveLayerOffsetY;
      for (const [id, start] of state.moveLayerOffsets) {
        state.layerOffsets.set(id, { x: start.x + appliedDx, y: start.y + appliedDy });
        const placedStart = state.movePlacedSources?.get(id);
        const movedLayer = state.layers.find(item => item.id === id);
        if (placedStart && movedLayer) movedLayer.placed = translatePlacedData(placedStart, appliedDx, appliedDy);
      }
      // An unlinked mask stays in the same document position while its layer
      // moves. Its layer-relative offset therefore changes by the inverse
      // layer delta. Linked masks retain their relative offset and move along.
      for (const entry of state.moveUnlinkedMaskOffsets.values()) {
        entry.mask.offset = { x: entry.x - appliedDx, y: entry.y - appliedDy };
      }
      const next = state.layerOffsets.get(layer.id) || { x: nx, y: ny };
      composite();
      onPositionChange?.(layer, before, next);
    },
    end() {
      state.moving = false;
      state.moveHistorySaved = false;
      state.moveLayerOffsets = new Map();
      state.movePlacedSources = new Map();
      state.moveMaskTarget = null;
      state.moveUnlinkedMaskOffsets = new Map();
      state.activeSnapGuides = null;
    },
  };
}
