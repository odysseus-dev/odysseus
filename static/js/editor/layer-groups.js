/** Parent-linked layer groups with recursively isolated compositing. */

import { drawLayerStack, drawLayerStackAsync } from './layer-clipping.js';
import { renderEffects, renderEffectsAsync } from './effects.js';

function groupMap(editorState) {
  return new Map((editorState.layerGroups || []).map(group => [group.id, group]));
}

function breakParentCycles(groups) {
  const byId = new Map(groups.map(group => [group.id, group]));
  for (const group of groups) {
    const visited = new Set([group.id]);
    let cursor = group;
    while (cursor.parentId) {
      if (visited.has(cursor.parentId)) {
        group.parentId = null;
        break;
      }
      visited.add(cursor.parentId);
      cursor = byId.get(cursor.parentId);
      if (!cursor) break;
    }
  }
}

export function normalizeLayerGroups(editorState) {
  const validLayerIds = new Set((editorState.layers || []).map(layer => layer.id));
  const claimedLayers = new Set();
  const usedGroupIds = new Set();
  const normalized = [];
  for (const source of editorState.layerGroups || []) {
    if (!source || typeof source !== 'object') continue;
    let id = String(source.id || `group-${normalized.length + 1}`);
    while (usedGroupIds.has(id)) id = `${id}-${normalized.length + 1}`;
    usedGroupIds.add(id);
    const layerIds = [];
    for (const layerId of source.layerIds || []) {
      if (validLayerIds.has(layerId) && !claimedLayers.has(layerId)) {
        claimedLayers.add(layerId);
        layerIds.push(layerId);
      }
    }
    source.id = id;
    source.name = String(source.name || `Group ${normalized.length + 1}`);
    source.layerIds = layerIds;
    source.parentId = source.parentId == null ? null : String(source.parentId);
    source.visible = source.visible !== false;
    source.opacity = Number.isFinite(Number(source.opacity))
      ? Math.max(0, Math.min(1, Number(source.opacity)))
      : 1;
    source.blendMode = source.blendMode || 'source-over';
    source.locked = !!source.locked;
    source.collapsed = !!source.collapsed;
    source.masks = Array.isArray(source.masks) ? source.masks.filter(mask => mask && typeof mask === 'object') : [];
    source.effects = Array.isArray(source.effects) ? source.effects : [];
    source.activeMaskId = source.masks.some(mask => mask.id === source.activeMaskId) ? source.activeMaskId : null;
    normalized.push(source);
  }

  const validGroupIds = new Set(normalized.map(group => group.id));
  for (const group of normalized) {
    if (group.parentId === group.id || !validGroupIds.has(group.parentId)) group.parentId = null;
  }
  breakParentCycles(normalized);

  // Parent-only groups are valid. Remove only empty leaves, then repeat because
  // their removal can make an ancestor empty as well.
  let retained = normalized;
  while (true) {
    const parentIds = new Set(retained.map(group => group.parentId).filter(Boolean));
    const next = retained.filter(group => group.layerIds.length || parentIds.has(group.id));
    if (next.length === retained.length) break;
    const nextIds = new Set(next.map(group => group.id));
    for (const group of next) {
      if (group.parentId && !nextIds.has(group.parentId)) group.parentId = null;
    }
    retained = next;
  }

  editorState.layerGroups = retained;
  if (!retained.some(group => group.id === editorState.activeGroupId)) editorState.activeGroupId = null;
  return retained;
}

export function groupForLayer(editorState, layerId) {
  return normalizeLayerGroups(editorState).find(group => group.layerIds.includes(layerId)) || null;
}

export function groupAncestors(editorState, groupOrId, { includeSelf = false } = {}) {
  const groups = normalizeLayerGroups(editorState);
  const byId = new Map(groups.map(group => [group.id, group]));
  const group = typeof groupOrId === 'string' ? byId.get(groupOrId) : groupOrId;
  const result = [];
  let cursor = includeSelf ? group : byId.get(group?.parentId);
  while (cursor && !result.some(item => item.id === cursor.id)) {
    result.push(cursor);
    cursor = byId.get(cursor.parentId);
  }
  return result;
}

export function groupDepth(editorState, groupOrId) {
  return groupAncestors(editorState, groupOrId).length;
}

export function groupsForLayer(editorState, layerId) {
  const direct = groupForLayer(editorState, layerId);
  return direct ? [direct, ...groupAncestors(editorState, direct)] : [];
}

export function allLayerIdsInGroup(editorState, groupOrId) {
  const groups = normalizeLayerGroups(editorState);
  const byId = new Map(groups.map(group => [group.id, group]));
  const root = typeof groupOrId === 'string' ? byId.get(groupOrId) : groupOrId;
  if (!root) return [];
  const children = new Map();
  for (const group of groups) {
    if (!group.parentId) continue;
    if (!children.has(group.parentId)) children.set(group.parentId, []);
    children.get(group.parentId).push(group);
  }
  const ids = new Set();
  const visit = group => {
    for (const id of group.layerIds) ids.add(id);
    for (const child of children.get(group.id) || []) visit(child);
  };
  visit(root);
  return (editorState.layers || []).filter(layer => ids.has(layer.id)).map(layer => layer.id);
}

export function isLayerEffectivelyLocked(editorState, layer) {
  if (!layer) return true;
  return !!layer.locked || groupsForLayer(editorState, layer.id).some(group => group.locked);
}

export function normalizeLayerLocks(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  return {
    pixels: !!source.pixels,
    transparency: !!source.transparency,
    position: !!source.position,
  };
}

export function layerHasAnyLock(layer) {
  const locks = normalizeLayerLocks(layer?.locks);
  return !!layer?.locked || locks.pixels || locks.transparency || locks.position;
}

export function isLayerPixelLocked(editorState, layer) {
  return isLayerEffectivelyLocked(editorState, layer) || normalizeLayerLocks(layer?.locks).pixels;
}

export function isLayerTransparencyLocked(editorState, layer) {
  return isLayerEffectivelyLocked(editorState, layer) || normalizeLayerLocks(layer?.locks).transparency;
}

export function isLayerPositionLocked(editorState, layer) {
  return isLayerEffectivelyLocked(editorState, layer) || normalizeLayerLocks(layer?.locks).position;
}

export function createGroupFromSelection(editorState, name = null) {
  const selected = new Set(editorState.selectedLayerIds || []);
  const indexed = (editorState.layers || [])
    .map((layer, index) => ({ layer, index }))
    .filter(item => selected.has(item.layer.id));
  if (indexed.length < 2) return null;

  const groups = normalizeLayerGroups(editorState);
  const descendantIds = new Map(groups.map(group => [group.id, allLayerIdsInGroup(editorState, group)]));
  const fullySelected = new Set(groups
    .filter(group => descendantIds.get(group.id).length && descendantIds.get(group.id).every(id => selected.has(id)))
    .map(group => group.id));
  const selectedGroups = groups.filter(group =>
    fullySelected.has(group.id) && !groupAncestors(editorState, group).some(parent => fullySelected.has(parent.id))
  );
  const covered = new Set(selectedGroups.flatMap(group => descendantIds.get(group.id)));
  const directLayerIds = indexed.map(item => item.layer.id).filter(id => !covered.has(id));

  const componentParents = [
    ...selectedGroups.map(group => group.parentId || null),
    ...directLayerIds.map(id => groupForLayer(editorState, id)?.id || null),
  ];
  const commonParentId = componentParents.length && componentParents.every(id => id === componentParents[0])
    ? componentParents[0]
    : null;

  const directSet = new Set(directLayerIds);
  for (const group of groups) group.layerIds = group.layerIds.filter(id => !directSet.has(id));

  const selectedIds = new Set(indexed.map(item => item.layer.id));
  const topIndex = indexed[indexed.length - 1].index;
  const remaining = editorState.layers.filter(layer => !selectedIds.has(layer.id));
  const insertion = editorState.layers
    .slice(0, topIndex + 1)
    .filter(layer => !selectedIds.has(layer.id)).length;
  const members = indexed.map(item => item.layer);
  remaining.splice(insertion, 0, ...members);
  editorState.layers = remaining;

  const id = `group-${editorState.nextLayerId++}`;
  const group = {
    id,
    name: name || `Group ${groups.length + 1}`,
    layerIds: directLayerIds,
    parentId: commonParentId,
    visible: true,
    opacity: 1,
    blendMode: 'source-over',
    locked: false,
    collapsed: false,
    masks: [],
    activeMaskId: null,
    effects: [],
  };
  for (const child of selectedGroups) child.parentId = id;
  editorState.layerGroups.push(group);
  normalizeLayerGroups(editorState);
  editorState.activeGroupId = id;
  editorState.selectedLayerIds = allLayerIdsInGroup(editorState, id);
  editorState.activeLayerId = editorState.selectedLayerIds[editorState.selectedLayerIds.length - 1];
  editorState.selectionAnchorId = editorState.activeLayerId;
  return groupMap(editorState).get(id) || group;
}

export function ungroupLayers(editorState, groupId) {
  const group = normalizeLayerGroups(editorState).find(item => item.id === groupId);
  if (!group || group.masks?.length) return null;
  const selectedIds = allLayerIdsInGroup(editorState, group);
  const parent = group.parentId ? groupMap(editorState).get(group.parentId) : null;
  if (parent) {
    const direct = new Set([...parent.layerIds, ...group.layerIds]);
    parent.layerIds = (editorState.layers || []).filter(layer => direct.has(layer.id)).map(layer => layer.id);
  }
  for (const child of editorState.layerGroups) {
    if (child.parentId === group.id) child.parentId = group.parentId || null;
  }
  editorState.layerGroups = editorState.layerGroups.filter(item => item.id !== groupId);
  if (editorState.activeGroupId === groupId) editorState.activeGroupId = group.parentId || null;
  editorState.selectedLayerIds = selectedIds;
  normalizeLayerGroups(editorState);
  return group;
}

export function groupSiblingUnits(editorState, parentId = null) {
  const groups = normalizeLayerGroups(editorState);
  const parent = parentId ? groups.find(group => group.id === parentId) : null;
  if (parentId && !parent) return [];
  const layerOrder = new Map((editorState.layers || []).map((layer, index) => [layer.id, index]));
  const units = [];
  const directIds = parent
    ? parent.layerIds
    : (editorState.layers || [])
      .filter(layer => !groups.some(group => group.layerIds.includes(layer.id)))
      .map(layer => layer.id);
  for (const layerId of directIds) {
    if (layerOrder.has(layerId)) units.push({ type: 'layer', id: layerId, layerIds: [layerId] });
  }
  for (const group of groups.filter(item => (item.parentId || null) === (parentId || null))) {
    const layerIds = allLayerIdsInGroup(editorState, group);
    if (layerIds.length) units.push({ type: 'group', id: group.id, layerIds });
  }
  return units.sort((a, b) => {
    const aIndex = Math.min(...a.layerIds.map(id => layerOrder.get(id)).filter(Number.isInteger));
    const bIndex = Math.min(...b.layerIds.map(id => layerOrder.get(id)).filter(Number.isInteger));
    return aIndex - bIndex;
  });
}

/** Move a complete group subtree among siblings without changing its parent. */
export function reorderGroupAmongSiblings(editorState, groupId, targetIndex) {
  const group = normalizeLayerGroups(editorState).find(item => item.id === groupId);
  if (!group) return null;
  const parentId = group.parentId || null;
  const units = groupSiblingUnits(editorState, parentId);
  const currentIndex = units.findIndex(unit => unit.type === 'group' && unit.id === groupId);
  if (currentIndex < 0) return null;
  const nextIndex = Math.max(0, Math.min(units.length - 1, Math.round(Number(targetIndex))));
  if (nextIndex === currentIndex) return { currentIndex, targetIndex: nextIndex, changed: false };
  const [moving] = units.splice(currentIndex, 1);
  units.splice(nextIndex, 0, moving);

  const scopeIds = new Set(units.flatMap(unit => unit.layerIds));
  const byId = new Map((editorState.layers || []).map(layer => [layer.id, layer]));
  const reordered = units.flatMap(unit => unit.layerIds).map(id => byId.get(id)).filter(Boolean);
  let cursor = 0;
  editorState.layers = (editorState.layers || []).map(layer =>
    scopeIds.has(layer.id) ? reordered[cursor++] : layer
  );
  return { currentIndex, targetIndex: nextIndex, changed: true, parentId };
}

function groupCanvas(editorState, groupId) {
  if (!(editorState.groupCompositeCanvases instanceof Map)) editorState.groupCompositeCanvases = new Map();
  let canvas = editorState.groupCompositeCanvases.get(groupId);
  if (!canvas) {
    canvas = document.createElement('canvas');
    editorState.groupCompositeCanvases.set(groupId, canvas);
  }
  if (canvas.width !== editorState.imgWidth) canvas.width = editorState.imgWidth;
  if (canvas.height !== editorState.imgHeight) canvas.height = editorState.imgHeight;
  return canvas;
}

export function drawGroupedLayers(ctx, editorState, renderLayer, renderAdjustment = null, renderGroup = null, shouldContinue = () => true) {
  ctx.clearRect(0, 0, editorState.imgWidth, editorState.imgHeight);
  if (!(editorState.groupCompositeCanvases instanceof Map)) editorState.groupCompositeCanvases = new Map();
  // A hidden or removed group must not keep advertising its previous pixels to
  // the layer panel while the next document composite is being built.
  editorState.groupCompositeCanvases.clear();
  const groups = normalizeLayerGroups(editorState);
  const byId = new Map(groups.map(group => [group.id, group]));
  const directGroupByLayer = new Map();
  const children = new Map();
  for (const group of groups) {
    for (const id of group.layerIds) directGroupByLayer.set(id, group.id);
    const parentId = group.parentId || null;
    if (!children.has(parentId)) children.set(parentId, []);
    children.get(parentId).push(group);
  }
  const indexes = new Map((editorState.layers || []).map((layer, index) => [layer.id, index]));
  const boundsMemo = new Map();
  const groupBounds = group => {
    if (boundsMemo.has(group.id)) return boundsMemo.get(group.id);
    const values = [
      ...group.layerIds.map(id => indexes.get(id)).filter(Number.isInteger),
      ...(children.get(group.id) || []).flatMap(child => groupBounds(child)),
    ];
    const result = values.length ? [Math.min(...values), Math.max(...values)] : [];
    boundsMemo.set(group.id, result);
    return result;
  };

  const renderScope = (target, parentId = null) => {
    const nodes = [];
    for (const layer of editorState.layers || []) {
      if ((directGroupByLayer.get(layer.id) || null) === parentId) {
        nodes.push({ type: 'layer', index: indexes.get(layer.id), layer });
      }
    }
    for (const group of children.get(parentId) || []) {
      const bounds = groupBounds(group);
      if (bounds.length) nodes.push({ type: 'group', index: bounds[0], group });
    }
    nodes.sort((a, b) => a.index - b.index);
    let layerRun = [];
    const flushLayers = () => {
      if (!layerRun.length) return;
      drawLayerStack(target, editorState, layerRun, renderLayer, renderAdjustment);
      layerRun = [];
    };
    for (const node of nodes) {
      if (!shouldContinue()) return;
      if (node.type === 'layer') {
        layerRun.push(node.layer);
        continue;
      }
      flushLayers();
      const group = byId.get(node.group.id);
      if (!group?.visible) continue;
      const canvas = groupCanvas(editorState, group.id);
      const groupCtx = canvas.getContext('2d');
      groupCtx.clearRect(0, 0, canvas.width, canvas.height);
      renderScope(groupCtx, group.id);
      const masks = (group.masks || []).filter(mask => mask.visible !== false && mask.canvas);
      if (masks.length) {
        groupCtx.globalAlpha = 1;
        groupCtx.globalCompositeOperation = 'destination-in';
        for (const mask of masks) {
          groupCtx.globalAlpha = Number.isFinite(Number(mask.density))
            ? Math.max(0, Math.min(1, Number(mask.density)))
            : 1;
          const feather = Number.isFinite(Number(mask.feather))
            ? Math.max(0, Math.min(200, Number(mask.feather)))
            : 0;
          groupCtx.filter = feather > 0 ? `blur(${feather}px)` : 'none';
          groupCtx.drawImage(mask.canvas, 0, 0, canvas.width, canvas.height);
        }
      }
      groupCtx.globalAlpha = 1;
      groupCtx.filter = 'none';
      groupCtx.globalCompositeOperation = 'source-over';
      const groupOutput = renderGroup
        ? renderGroup(canvas, group, shouldContinue)
        : (group.effects?.length ? renderEffects(canvas, group.effects, shouldContinue) : canvas);
      if (!shouldContinue()) return;
      if (groupOutput?.width && groupOutput?.height) {
        // Keep the exact final group output for the layer-panel thumbnail. A
        // raw member fallback loses masks, blending, and group effects.
        editorState.groupCompositeCanvases.set(group.id, groupOutput);
      }
      target.globalAlpha = group.opacity;
      target.globalCompositeOperation = group.blendMode || 'source-over';
      target.drawImage(groupOutput, 0, 0);
    }
    flushLayers();
    target.globalAlpha = 1;
    target.globalCompositeOperation = 'source-over';
  };

  renderScope(ctx);
  const validIds = new Set(groups.map(group => group.id));
  for (const id of editorState.groupCompositeCanvases?.keys?.() || []) {
    if (!validIds.has(id)) editorState.groupCompositeCanvases.delete(id);
  }
}

/**
 * Async group compositor. It deliberately uses fresh group surfaces so an
 * older generation cannot overwrite the cached surface used by a newer one.
 */
export async function drawGroupedLayersAsync(ctx, editorState, renderLayer, renderAdjustment = null, renderGroup = null, shouldContinue = () => true) {
  ctx.clearRect(0, 0, editorState.imgWidth, editorState.imgHeight);
  if (!(editorState.groupCompositeCanvases instanceof Map)) editorState.groupCompositeCanvases = new Map();
  editorState.groupCompositeCanvases.clear();
  const groups = normalizeLayerGroups(editorState);
  const byId = new Map(groups.map(group => [group.id, group]));
  const directGroupByLayer = new Map();
  const children = new Map();
  for (const group of groups) {
    for (const id of group.layerIds) directGroupByLayer.set(id, group.id);
    const parentId = group.parentId || null;
    if (!children.has(parentId)) children.set(parentId, []);
    children.get(parentId).push(group);
  }
  const indexes = new Map((editorState.layers || []).map((layer, index) => [layer.id, index]));
  const boundsMemo = new Map();
  const groupBounds = group => {
    if (boundsMemo.has(group.id)) return boundsMemo.get(group.id);
    const values = [
      ...group.layerIds.map(id => indexes.get(id)).filter(Number.isInteger),
      ...(children.get(group.id) || []).flatMap(child => groupBounds(child)),
    ];
    const result = values.length ? [Math.min(...values), Math.max(...values)] : [];
    boundsMemo.set(group.id, result);
    return result;
  };

  const renderScope = async (target, parentId = null) => {
    const nodes = [];
    for (const layer of editorState.layers || []) {
      if ((directGroupByLayer.get(layer.id) || null) === parentId) {
        nodes.push({ type: 'layer', index: indexes.get(layer.id), layer });
      }
    }
    for (const group of children.get(parentId) || []) {
      const bounds = groupBounds(group);
      if (bounds.length) nodes.push({ type: 'group', index: bounds[0], group });
    }
    nodes.sort((a, b) => a.index - b.index);
    let layerRun = [];
    const flushLayers = async () => {
      if (!layerRun.length || !shouldContinue()) return;
      const run = layerRun;
      layerRun = [];
      await drawLayerStackAsync(target, editorState, run, renderLayer, renderAdjustment, shouldContinue);
    };
    for (const node of nodes) {
      if (!shouldContinue()) return;
      if (node.type === 'layer') {
        layerRun.push(node.layer);
        continue;
      }
      await flushLayers();
      if (!shouldContinue()) return;
      const group = byId.get(node.group.id);
      if (!group?.visible) continue;
      const canvas = document.createElement('canvas');
      canvas.width = editorState.imgWidth;
      canvas.height = editorState.imgHeight;
      const groupCtx = canvas.getContext('2d');
      await renderScope(groupCtx, group.id);
      if (!shouldContinue()) return;
      const masks = (group.masks || []).filter(mask => mask.visible !== false && mask.canvas);
      if (masks.length) {
        groupCtx.globalAlpha = 1;
        groupCtx.globalCompositeOperation = 'destination-in';
        for (const mask of masks) {
          groupCtx.globalAlpha = Number.isFinite(Number(mask.density))
            ? Math.max(0, Math.min(1, Number(mask.density)))
            : 1;
          const feather = Number.isFinite(Number(mask.feather))
            ? Math.max(0, Math.min(200, Number(mask.feather)))
            : 0;
          groupCtx.filter = feather > 0 ? `blur(${feather}px)` : 'none';
          groupCtx.drawImage(mask.canvas, 0, 0, canvas.width, canvas.height);
        }
      }
      groupCtx.globalAlpha = 1;
      groupCtx.filter = 'none';
      groupCtx.globalCompositeOperation = 'source-over';
      const groupOutput = renderGroup
        ? await renderGroup(canvas, group, shouldContinue)
        : (group.effects?.length ? await renderEffectsAsync(canvas, group.effects, shouldContinue) : canvas);
      if (!shouldContinue() || !groupOutput) return;
      if (groupOutput.width && groupOutput.height) {
        editorState.groupCompositeCanvases.set(group.id, groupOutput);
      }
      target.globalAlpha = group.opacity;
      target.globalCompositeOperation = group.blendMode || 'source-over';
      target.drawImage(groupOutput, 0, 0);
    }
    await flushLayers();
    target.globalAlpha = 1;
    target.globalCompositeOperation = 'source-over';
  };

  await renderScope(ctx);
}
