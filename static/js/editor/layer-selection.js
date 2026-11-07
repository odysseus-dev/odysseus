/** Shared multi-layer selection semantics for panel and canvas tools. */
import { isLayerEffectivelyLocked } from './layer-groups.js';

function existingIds(editorState) {
  return new Set((editorState.layers || []).map(layer => layer.id));
}

export function normalizeLayerSelection(editorState) {
  const valid = existingIds(editorState);
  const selected = [];
  for (const id of editorState.selectedLayerIds || []) {
    if (valid.has(id) && !selected.includes(id)) selected.push(id);
  }
  if (valid.has(editorState.activeLayerId) && !selected.includes(editorState.activeLayerId)) {
    selected.length = 0;
    selected.push(editorState.activeLayerId);
    editorState.activeGroupId = null;
  }
  if (!selected.length && editorState.layers?.length) {
    const fallback = editorState.layers[editorState.layers.length - 1];
    editorState.activeLayerId = fallback.id;
    selected.push(fallback.id);
    editorState.activeGroupId = null;
  }
  editorState.selectedLayerIds = selected;
  if (!valid.has(editorState.selectionAnchorId)) {
    editorState.selectionAnchorId = editorState.activeLayerId || null;
  }
  return selected;
}

export function selectedLayers(editorState, { unlockedOnly = false } = {}) {
  const ids = new Set(normalizeLayerSelection(editorState));
  return (editorState.layers || []).filter(layer =>
    ids.has(layer.id) && (!unlockedOnly || !isLayerEffectivelyLocked(editorState, layer))
  );
}

export function selectOnlyLayer(editorState, layerId) {
  if (!(editorState.layers || []).some(layer => layer.id === layerId)) return [];
  editorState.activeLayerId = layerId;
  editorState.selectionAnchorId = layerId;
  editorState.selectedLayerIds = [layerId];
  editorState.activeGroupId = null;
  return editorState.selectedLayerIds;
}

export function toggleLayerSelection(editorState, layerId) {
  const valid = (editorState.layers || []).some(layer => layer.id === layerId);
  if (!valid) return normalizeLayerSelection(editorState);
  const selected = normalizeLayerSelection(editorState);
  const index = selected.indexOf(layerId);
  if (index >= 0 && selected.length > 1) {
    selected.splice(index, 1);
    if (editorState.activeLayerId === layerId) {
      editorState.activeLayerId = selected[selected.length - 1];
    }
  } else if (index < 0) {
    selected.push(layerId);
    editorState.activeLayerId = layerId;
  }
  editorState.selectionAnchorId = layerId;
  editorState.selectedLayerIds = selected;
  editorState.activeGroupId = null;
  return selected;
}

export function selectLayerRange(editorState, layerId) {
  const layers = editorState.layers || [];
  const target = layers.findIndex(layer => layer.id === layerId);
  if (target < 0) return normalizeLayerSelection(editorState);
  const anchorId = editorState.selectionAnchorId || editorState.activeLayerId || layerId;
  const anchor = layers.findIndex(layer => layer.id === anchorId);
  if (anchor < 0) return selectOnlyLayer(editorState, layerId);
  const start = Math.min(anchor, target);
  const end = Math.max(anchor, target);
  editorState.selectedLayerIds = layers.slice(start, end + 1).map(layer => layer.id);
  editorState.activeLayerId = layerId;
  editorState.activeGroupId = null;
  return editorState.selectedLayerIds;
}

export function selectAllLayers(editorState) {
  editorState.selectedLayerIds = (editorState.layers || []).map(layer => layer.id);
  if (editorState.selectedLayerIds.length && !editorState.selectedLayerIds.includes(editorState.activeLayerId)) {
    editorState.activeLayerId = editorState.selectedLayerIds[editorState.selectedLayerIds.length - 1];
  }
  editorState.selectionAnchorId = editorState.activeLayerId || null;
  editorState.activeGroupId = null;
  return editorState.selectedLayerIds;
}

export function selectionLabel(editorState, singular = 'layer') {
  const count = normalizeLayerSelection(editorState).length;
  return count === 1 ? singular : `${count} layers`;
}
