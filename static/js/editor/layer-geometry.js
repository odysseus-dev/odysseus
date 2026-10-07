/** Exact active-layer geometry used by controls, dragging, and key nudging. */
import { state } from './state.js';
import { selectedLayers, selectionLabel } from './layer-selection.js';
import { isLayerPositionLocked } from './layer-groups.js';
import { translatePlacedData } from './placed-layer.js';

const MAX_COORDINATE = 1000000;

export function normalizeLayerCoordinate(value, fallback = 0) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return Math.round(Number(fallback) || 0);
  return Math.max(-MAX_COORDINATE, Math.min(MAX_COORDINATE, Math.round(parsed)));
}

export function readLayerGeometry(editorState, layer) {
  if (!layer?.canvas) return null;
  const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  return {
    x: normalizeLayerCoordinate(offset.x),
    y: normalizeLayerCoordinate(offset.y),
    width: Math.max(1, Math.round(layer.canvas.width || 1)),
    height: Math.max(1, Math.round(layer.canvas.height || 1)),
  };
}

export function setLayerPosition(editorState, layer, x, y) {
  if (!layer) return null;
  const current = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const next = {
    x: normalizeLayerCoordinate(x, current.x),
    y: normalizeLayerCoordinate(y, current.y),
  };
  editorState.layerOffsets.set(layer.id, next);
  return next;
}

export function nudgeLayerPosition(editorState, layer, dx, dy) {
  const current = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  return setLayerPosition(
    editorState,
    layer,
    current.x + normalizeLayerCoordinate(dx),
    current.y + normalizeLayerCoordinate(dy),
  );
}

function moveLayerBy(editorState, layer, dx, dy) {
  if (!layer || isLayerPositionLocked(editorState, layer)) return false;
  const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const next = setLayerPosition(editorState, layer, offset.x + dx, offset.y + dy);
  if (layer.kind === 'placed' && layer.placed) {
    layer.placed = translatePlacedData(layer.placed, next.x - offset.x, next.y - offset.y);
  }
  for (const mask of layer.masks || []) {
    if (mask.mode !== 'layer' || mask.linked !== false) continue;
    mask.offset = {
      x: (Number(mask.offset?.x) || 0) - (next.x - offset.x),
      y: (Number(mask.offset?.y) || 0) - (next.y - offset.y),
    };
  }
  return next.x !== offset.x || next.y !== offset.y;
}

/** Move selected layers to a document edge/center without rasterizing them. */
export function alignSelectedLayers(editorState, layers, alignment) {
  const targets = (layers || []).filter(layer => readLayerGeometry(editorState, layer));
  if (targets.length < 2) return false;
  let changed = false;
  for (const layer of targets) {
    const geometry = readLayerGeometry(editorState, layer);
    let x = geometry.x;
    let y = geometry.y;
    if (alignment === 'left') x = 0;
    else if (alignment === 'center') x = (editorState.imgWidth - geometry.width) / 2;
    else if (alignment === 'right') x = editorState.imgWidth - geometry.width;
    else if (alignment === 'top') y = 0;
    else if (alignment === 'middle') y = (editorState.imgHeight - geometry.height) / 2;
    else if (alignment === 'bottom') y = editorState.imgHeight - geometry.height;
    changed = moveLayerBy(editorState, layer, x - geometry.x, y - geometry.y) || changed;
  }
  return changed;
}

/** Evenly distribute selected layer centers along one document axis. */
export function distributeSelectedLayers(editorState, layers, axis) {
  const entries = (layers || []).map(layer => ({ layer, geometry: readLayerGeometry(editorState, layer) }))
    .filter(entry => entry.geometry)
    .sort((a, b) => a.geometry[axis === 'vertical' ? 'y' : 'x'] - b.geometry[axis === 'vertical' ? 'y' : 'x']);
  if (entries.length < 3) return false;
  const coordinate = axis === 'vertical' ? 'y' : 'x';
  const size = axis === 'vertical' ? 'height' : 'width';
  const first = entries[0].geometry[coordinate] + entries[0].geometry[size] / 2;
  const lastEntry = entries[entries.length - 1];
  const last = lastEntry.geometry[coordinate] + lastEntry.geometry[size] / 2;
  const step = (last - first) / (entries.length - 1);
  let changed = false;
  entries.forEach((entry, index) => {
    const target = first + step * index - entry.geometry[size] / 2;
    const delta = target - entry.geometry[coordinate];
    changed = moveLayerBy(editorState, entry.layer, axis === 'vertical' ? 0 : delta, axis === 'vertical' ? delta : 0) || changed;
  });
  return changed;
}

export function createLayerGeometryController({ activeLayer, saveState, composite }) {
  let root = null;
  let fieldHistorySaved = false;
  let nudgeHistorySaved = false;

  function shiftTransformFrame(dx, dy) {
    if (!state.transformActive) return;
    if (state.transformCenter) {
      state.transformCenter = { x: state.transformCenter.x + dx, y: state.transformCenter.y + dy };
    }
    if (state.transformBounds) {
      state.transformBounds = { ...state.transformBounds, x: state.transformBounds.x + dx, y: state.transformBounds.y + dy };
    }
    if (state.transformOrigOffset) {
      state.transformOrigOffset.x += dx;
      state.transformOrigOffset.y += dy;
    }
  }

  const inputs = () => ({
    x: root?.querySelector('#ge-layer-x') || null,
    y: root?.querySelector('#ge-layer-y') || null,
    width: root?.querySelector('#ge-layer-width') || null,
    height: root?.querySelector('#ge-layer-height') || null,
    name: root?.querySelector('#ge-layer-geometry-name') || null,
  });

  function sync() {
    const layer = activeLayer();
    const fields = inputs();
    const geometry = readLayerGeometry(state, layer);
    if (fields.name) fields.name.textContent = layer?.name || 'No layer selected';
    for (const field of [fields.x, fields.y]) field && (field.disabled = !layer || isLayerPositionLocked(state, layer));
    for (const field of [fields.width, fields.height]) field && (field.disabled = !layer);
    if (!geometry) {
      for (const field of [fields.x, fields.y, fields.width, fields.height]) {
        if (field) field.value = '';
      }
      return null;
    }
    if (fields.x && document.activeElement !== fields.x) fields.x.value = String(geometry.x);
    if (fields.y && document.activeElement !== fields.y) fields.y.value = String(geometry.y);
    if (fields.width) fields.width.value = String(geometry.width);
    if (fields.height) fields.height.value = String(geometry.height);
    return geometry;
  }

  function moveTo(x, y, label = null) {
    const layer = activeLayer();
    if (!layer || isLayerPositionLocked(state, layer)) return false;
    const before = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const nextX = normalizeLayerCoordinate(x, before.x);
    const nextY = normalizeLayerCoordinate(y, before.y);
    if (before.x === nextX && before.y === nextY) {
      sync();
      return false;
    }
    if (label) saveState(label);
    const dx = nextX - before.x;
    const dy = nextY - before.y;
    for (const selected of selectedLayers(state).filter(item => !isLayerPositionLocked(state, item))) {
      const selectedOffset = state.layerOffsets.get(selected.id) || { x: 0, y: 0 };
      setLayerPosition(state, selected, selectedOffset.x + dx, selectedOffset.y + dy);
      if (selected.kind === 'placed' && selected.placed) {
        selected.placed = translatePlacedData(selected.placed, dx, dy);
      }
      for (const mask of selected.masks || []) {
        if (mask.mode !== 'layer' || mask.linked !== false) continue;
        mask.offset = {
          x: (Number(mask.offset?.x) || 0) - dx,
          y: (Number(mask.offset?.y) || 0) - dy,
        };
      }
    }
    if (state.transformActive && state.transformLayer?.id === layer.id) shiftTransformFrame(dx, dy);
    composite();
    sync();
    return true;
  }

  function nudge(dx, dy) {
    const layer = activeLayer();
    if (!layer || isLayerPositionLocked(state, layer)) return false;
    const activeMask = (layer.masks || []).find(mask =>
      mask.id === layer.activeMaskId && mask.mode === 'layer' && mask.linked === false);
    if (activeMask) {
      if (!nudgeHistorySaved) saveState(`Nudge mask "${activeMask.name || 'Layer Mask'}"`);
      activeMask.offset = {
        x: (Number(activeMask.offset?.x) || 0) + dx,
        y: (Number(activeMask.offset?.y) || 0) + dy,
      };
      nudgeHistorySaved = true;
      composite();
      return true;
    }
    const current = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const label = !state.transformActive && !nudgeHistorySaved
      ? `Nudge ${selectionLabel(state, `"${layer.name || 'layer'}"`)}`
      : null;
    const moved = moveTo(current.x + dx, current.y + dy, label);
    if (moved) nudgeHistorySaved = true;
    return moved;
  }

  function endNudge() {
    nudgeHistorySaved = false;
  }

  function trackExternalMove(layer, before, next) {
    if (state.transformActive && state.transformLayer?.id === layer?.id) {
      shiftTransformFrame(next.x - before.x, next.y - before.y);
    }
    sync();
  }

  function wire(nextRoot) {
    root = nextRoot;
    const fields = inputs();
    const applyFields = () => {
      const layer = activeLayer();
      const geometry = readLayerGeometry(state, layer);
      if (!geometry || isLayerPositionLocked(state, layer)) return sync();
      const nextX = normalizeLayerCoordinate(fields.x?.value, geometry.x);
      const nextY = normalizeLayerCoordinate(fields.y?.value, geometry.y);
      if (nextX === geometry.x && nextY === geometry.y) return;
      const label = !state.transformActive && !fieldHistorySaved
        ? `Position "${layer.name || 'layer'}"`
        : null;
      if (moveTo(nextX, nextY, label)) fieldHistorySaved = true;
    };
    for (const field of [fields.x, fields.y]) {
      if (!field) continue;
      field.addEventListener('input', applyFields);
      field.addEventListener('change', () => { applyFields(); fieldHistorySaved = false; });
      field.addEventListener('blur', () => { fieldHistorySaved = false; sync(); });
      field.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') field.blur();
        if (event.key === 'Escape') { event.preventDefault(); field.blur(); sync(); }
      });
    }
    sync();
  }

  return { wire, sync, moveTo, nudge, endNudge, trackExternalMove };
}
