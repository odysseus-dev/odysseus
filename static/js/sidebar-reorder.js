// ============================================
// Sidebar / icon-rail tool reorder (SortableJS)
// ============================================

import Storage from './storage.js';

const TOOL_ORDER_STORAGE_KEY = 'ody_sidebar_tool_order_v1';

/** Default order: alphabetical by tool key (matches shipped HTML comment). */
export const TOOL_ORDER_DEFAULT = [
  'archive',
  'calendar',
  'compare',
  'cookbook',
  'email',
  'gallery',
  'memory',
  'notes',
  'research',
  'tasks',
  'theme',
];

/** Keys that appear in the sidebar tools list (email is rail-only). */
const SIDEBAR_TOOL_KEYS = new Set(
  TOOL_ORDER_DEFAULT.filter((key) => key !== 'email'),
);

const RAIL_TOOL_KEYS = new Set(TOOL_ORDER_DEFAULT);

/** Extra px past the tools list edge so Sortable registers first/last slot. */
const LIST_EDGE_SLACK_PX = 48;

const SIDEBAR_TOOLS_LIST_ID = 'sidebar-tools-list';
const RAIL_TOOLS_LIST_ID = 'rail-tools-list';

let railSortable = null;
let sidebarSortable = null;
let toolDragSuppressClickUntil = 0;
let clickSuppressionWired = false;
/** @type {{ top: number, bottom: number, left: number, width: number } | null} */
let sidebarDragBounds = null;
/** @type {DOMRect | null} */
let sidebarDragListBounds = null;
/** Y extents of visible tab rows (excludes drag padding). */
let sidebarDragContentTop = null;
let sidebarDragContentBottom = null;
/** @type {HTMLElement | null} */
let activeDragListRoot = null;
let sidebarClampRafId = null;
let sidebarDragPointerX = 0;
let sidebarDragPointerY = 0;
let sidebarDragGrabOffsetY = 0;
let sidebarPointerProxyHandler = null;
let sidebarPointerProxyUpHandler = null;
/** @type {MouseEvent | PointerEvent | TouchEvent | null} */
let lastPointerMoveEvent = null;
let dragStartIndex = null;
let pointerReorderHandled = false;

/** Keeps the in-list source row hidden after Sortable strips chosenClass on drop. */
const TOOL_REORDER_SOURCE_HOLD = 'tool-reorder-drag-source';

function isToolsList(listRoot) {
  const id = listRoot?.id;
  return id === SIDEBAR_TOOLS_LIST_ID || id === RAIL_TOOLS_LIST_ID;
}

function sortableForList(listRoot) {
  if (listRoot?.id === SIDEBAR_TOOLS_LIST_ID) return sidebarSortable;
  if (listRoot?.id === RAIL_TOOLS_LIST_ID) return railSortable;
  return null;
}

function findActiveToolFallback() {
  return document.querySelector('.tool-sortable-fallback');
}

/** Lowest Y for the drag mirror — top of #sidebar-tools-list, below the Tools header. */
function resolveSidebarToolsDragFloor(fallbackTop) {
  const toolsList = document.getElementById(SIDEBAR_TOOLS_LIST_ID);
  if (toolsList) {
    return toolsList.getBoundingClientRect().top;
  }
  const toolsSection = document.getElementById('tools-section');
  if (!toolsSection) return fallbackTop;
  const header = toolsSection.querySelector('.section-header-flex');
  if (header) return header.getBoundingClientRect().bottom;
  return fallbackTop;
}

function refreshSidebarDragLockTop() {
  if (activeDragListRoot?.id !== SIDEBAR_TOOLS_LIST_ID || !sidebarDragBounds) return;
  const container = document.getElementById('sidebar');
  const fallbackTop = container?.getBoundingClientRect().top ?? sidebarDragBounds.top;
  sidebarDragBounds.top = resolveSidebarToolsDragFloor(fallbackTop);
}

function captureDragBounds(sourceItem, listRoot) {
  const isSidebarList = listRoot.id === SIDEBAR_TOOLS_LIST_ID;
  const container = listRoot.id === RAIL_TOOLS_LIST_ID
    ? document.getElementById('icon-rail')
    : document.getElementById('sidebar');
  if (!container || !sourceItem) {
    sidebarDragBounds = null;
    sidebarDragListBounds = null;
    return;
  }
  const containerRect = container.getBoundingClientRect();
  const itemRect = sourceItem.getBoundingClientRect();
  const lockTop = isSidebarList
    ? resolveSidebarToolsDragFloor(containerRect.top)
    : containerRect.top;
  sidebarDragBounds = {
    top: lockTop,
    bottom: containerRect.bottom,
    left: itemRect.left,
    width: itemRect.width,
  };
  sidebarDragListBounds = listRoot.getBoundingClientRect();
}

function effectiveDragPointerY() {
  if (!sidebarDragBounds) return sidebarDragPointerY;
  const { top, bottom } = sidebarDragBounds;
  return Math.max(top, Math.min(bottom, sidebarDragPointerY));
}

function clampTopToSidebar(desiredTop, height) {
  if (!sidebarDragBounds) return desiredTop;
  const { top: minTop, bottom } = sidebarDragBounds;
  const maxTop = Math.max(minTop, bottom - height);
  return Math.max(minTop, Math.min(maxTop, desiredTop));
}

/** Clamp Y only when the pointer leaves the sidebar column (not past the tools list). */
function projectPointerY(clientY) {
  if (!sidebarDragBounds) return clientY;
  const { top, bottom } = sidebarDragBounds;
  return Math.max(top, Math.min(bottom, clientY));
}

function pointerNeedsProjection(clientX, clientY) {
  if (!sidebarDragBounds) return false;
  const { top, bottom, left, width } = sidebarDragBounds;
  const right = left + width;
  return clientX < left || clientX > right || clientY < top || clientY > bottom;
}

function dispatchProjectedPointerMove(sourceEvent, clientX, clientY) {
  if (!sidebarDragBounds) return;
  const projectedX = sidebarDragBounds.left + sidebarDragBounds.width / 2;
  const projectedY = projectPointerY(clientY);

  const init = {
    bubbles: true,
    cancelable: true,
    clientX: projectedX,
    clientY: projectedY,
    screenX: sourceEvent.screenX,
    screenY: sourceEvent.screenY,
    button: sourceEvent.button,
    buttons: sourceEvent.buttons,
  };

  if (typeof PointerEvent !== 'undefined' && sourceEvent instanceof PointerEvent) {
    document.dispatchEvent(new PointerEvent('pointermove', {
      ...init,
      pointerId: sourceEvent.pointerId,
      pointerType: sourceEvent.pointerType,
    }));
    return;
  }

  document.dispatchEvent(new MouseEvent('mousemove', init));
}

function dispatchSortableListEdgeMove(sourceEvent, clientY) {
  if (!sidebarDragBounds || !sourceEvent) return;
  const init = {
    bubbles: true,
    cancelable: true,
    clientX: sidebarDragBounds.left + sidebarDragBounds.width / 2,
    clientY,
    screenX: sourceEvent.screenX,
    screenY: sourceEvent.screenY,
    button: sourceEvent.button,
    buttons: sourceEvent.buttons,
  };

  if (typeof PointerEvent !== 'undefined' && sourceEvent instanceof PointerEvent) {
    document.dispatchEvent(new PointerEvent('pointermove', {
      ...init,
      pointerId: sourceEvent.pointerId,
      pointerType: sourceEvent.pointerType,
    }));
    return;
  }

  document.dispatchEvent(new MouseEvent('mousemove', init));
}

function forceSortableDragOver(listRoot) {
  const sortable = sortableForList(listRoot);
  if (!sortable || typeof sortable._emulateDragOver !== 'function') return;
  sortable._emulateDragOver();
}

function refreshSidebarDragContentExtents(listRoot) {
  if (!listRoot) return;
  sidebarDragListBounds = listRoot.getBoundingClientRect();
  const items = visibleReorderables(listRoot);
  if (!items.length) {
    sidebarDragContentTop = sidebarDragListBounds.top;
    sidebarDragContentBottom = sidebarDragListBounds.bottom;
    return;
  }
  const firstRect = items[0].getBoundingClientRect();
  const lastRect = items[items.length - 1].getBoundingClientRect();
  sidebarDragContentTop = firstRect.top;
  sidebarDragContentBottom = lastRect.bottom;
}

function pointerPastDragContentEdge(pointerY) {
  if (sidebarDragContentTop == null || sidebarDragContentBottom == null) return null;
  if (pointerY < sidebarDragContentTop - 4) return 'top';
  if (pointerY > sidebarDragContentBottom + 4) return 'bottom';
  return null;
}

function feedSortableEdgeSlot(listRoot) {
  if (!isToolsList(listRoot) || !lastPointerMoveEvent) return;

  const edge = pointerPastDragContentEdge(sidebarDragPointerY);
  if (!edge) return;

  const minEdgeY = sidebarDragBounds?.top ?? sidebarDragContentTop;
  const clientY = edge === 'bottom'
    ? sidebarDragContentBottom + LIST_EDGE_SLACK_PX
    : Math.max(minEdgeY, sidebarDragContentTop - LIST_EDGE_SLACK_PX);
  dispatchSortableListEdgeMove(lastPointerMoveEvent, clientY);
  forceSortableDragOver(listRoot);
}

function onSidebarPointerProxy(event) {
  if (!sidebarDragBounds) return;

  let clientX = event.clientX;
  let clientY = event.clientY;
  if (event.type === 'touchmove' && event.touches?.length) {
    clientX = event.touches[0].clientX;
    clientY = event.touches[0].clientY;
  }

  sidebarDragPointerX = clientX;
  sidebarDragPointerY = clientY;
  lastPointerMoveEvent = event;

  if (
    isToolsList(activeDragListRoot)
    && !pointerNeedsProjection(clientX, clientY)
    && pointerPastDragContentEdge(clientY)
  ) {
    feedSortableEdgeSlot(activeDragListRoot);
  }

  if (!pointerNeedsProjection(clientX, clientY)) return;

  event.stopImmediatePropagation();
  dispatchProjectedPointerMove(event, clientX, clientY);
}

function onSidebarPointerProxyUp(event) {
  if (!sidebarDragBounds) return;

  let clientX = event.clientX;
  let clientY = event.clientY;
  if (event.type === 'touchend' && event.changedTouches?.length) {
    clientX = event.changedTouches[0].clientX;
    clientY = event.changedTouches[0].clientY;
  }

  sidebarDragPointerX = clientX;
  sidebarDragPointerY = clientY;
  lastPointerMoveEvent = event;

  if (
    isToolsList(activeDragListRoot)
    && pointerPastDragContentEdge(clientY)
  ) {
    feedSortableEdgeSlot(activeDragListRoot);
  }

  if (!pointerNeedsProjection(clientX, clientY)) return;

  dispatchProjectedPointerMove(event, clientX, clientY);
}

function startSidebarPointerProxy() {
  stopSidebarPointerProxy();
  sidebarPointerProxyHandler = onSidebarPointerProxy;
  sidebarPointerProxyUpHandler = onSidebarPointerProxyUp;
  document.addEventListener('mousemove', sidebarPointerProxyHandler, true);
  document.addEventListener('pointermove', sidebarPointerProxyHandler, true);
  document.addEventListener('touchmove', sidebarPointerProxyHandler, { capture: true, passive: false });
  document.addEventListener('mouseup', sidebarPointerProxyUpHandler, true);
  document.addEventListener('pointerup', sidebarPointerProxyUpHandler, true);
  document.addEventListener('touchend', sidebarPointerProxyUpHandler, { capture: true, passive: true });
}

function stopSidebarPointerProxy() {
  if (!sidebarPointerProxyHandler && !sidebarPointerProxyUpHandler) return;
  if (sidebarPointerProxyHandler) {
    document.removeEventListener('mousemove', sidebarPointerProxyHandler, true);
    document.removeEventListener('pointermove', sidebarPointerProxyHandler, true);
    document.removeEventListener('touchmove', sidebarPointerProxyHandler, true);
    sidebarPointerProxyHandler = null;
  }
  if (sidebarPointerProxyUpHandler) {
    document.removeEventListener('mouseup', sidebarPointerProxyUpHandler, true);
    document.removeEventListener('pointerup', sidebarPointerProxyUpHandler, true);
    document.removeEventListener('touchend', sidebarPointerProxyUpHandler, true);
    sidebarPointerProxyUpHandler = null;
  }
  sidebarDragPointerX = 0;
  sidebarDragPointerY = 0;
  sidebarDragGrabOffsetY = 0;
  lastPointerMoveEvent = null;
}

function clampSidebarFallbackPosition(fallbackEl) {
  if (!sidebarDragBounds || !fallbackEl) return;

  const { left, width } = sidebarDragBounds;
  const height = fallbackEl.offsetHeight;
  const nextTop = clampTopToSidebar(
    sidebarDragPointerY - sidebarDragGrabOffsetY,
    height,
  );

  fallbackEl.style.position = 'fixed';
  fallbackEl.style.transform = 'none';
  fallbackEl.style.webkitTransform = 'none';
  fallbackEl.style.margin = '0';
  fallbackEl.style.top = `${nextTop}px`;
  fallbackEl.style.left = `${left}px`;
  fallbackEl.style.width = `${width}px`;
}

function sidebarClampLoop() {
  refreshSidebarDragLockTop();
  const fallback = findActiveToolFallback();
  if (fallback) clampSidebarFallbackPosition(fallback);
  sidebarClampRafId = requestAnimationFrame(sidebarClampLoop);
}

function startSidebarFallbackClamp(sourceItem, sortableEvt, listRoot) {
  stopSidebarFallbackClamp();
  captureDragBounds(sourceItem, listRoot);
  refreshSidebarDragContentExtents(listRoot);
  const original = sortableEvt?.originalEvent;
  const itemRect = sourceItem?.getBoundingClientRect();
  if (original && typeof original.clientY === 'number' && itemRect) {
    sidebarDragPointerX = original.clientX;
    sidebarDragPointerY = original.clientY;
    sidebarDragGrabOffsetY = original.clientY - itemRect.top;
  } else {
    sidebarDragPointerX = sidebarDragBounds?.left ?? 0;
    sidebarDragPointerY = itemRect?.top ?? sidebarDragBounds?.top ?? 0;
    sidebarDragGrabOffsetY = 0;
  }
  document.body.classList.add('sidebar-tool-reorder-dragging');
  startSidebarPointerProxy();
  sidebarClampRafId = requestAnimationFrame(sidebarClampLoop);
}

function stopSidebarFallbackClamp() {
  document.body.classList.remove('sidebar-tool-reorder-dragging');
  stopSidebarPointerProxy();
  if (sidebarClampRafId != null) {
    cancelAnimationFrame(sidebarClampRafId);
    sidebarClampRafId = null;
  }
  sidebarDragBounds = null;
  sidebarDragListBounds = null;
  sidebarDragContentTop = null;
  sidebarDragContentBottom = null;
  activeDragListRoot = null;
}

function motionReduced() {
  if (typeof window.matchMedia === 'function') {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return true;
  }
  const scale = document.documentElement.style.getPropertyValue('--motion-scale');
  if (scale === '0') return true;
  return document.documentElement.getAttribute('data-motion') === 'reduced';
}

function coarsePointer() {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(hover: none) and (pointer: coarse)').matches;
}

export function readToolOrder() {
  try {
    const raw = Storage.get(TOOL_ORDER_STORAGE_KEY);
    if (!raw) return [...TOOL_ORDER_DEFAULT];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [...TOOL_ORDER_DEFAULT];
    const kept = parsed.filter((key) => TOOL_ORDER_DEFAULT.includes(key));
    const missing = TOOL_ORDER_DEFAULT.filter((key) => !kept.includes(key));
    return [...kept, ...missing];
  } catch {
    return [...TOOL_ORDER_DEFAULT];
  }
}

function saveToolOrder(order) {
  Storage.set(TOOL_ORDER_STORAGE_KEY, JSON.stringify(order));
}

function readOrderFromList(listRoot) {
  if (!listRoot) return [];
  return Array.from(listRoot.querySelectorAll('[data-tool-key]'))
    .map((el) => el.getAttribute('data-tool-key'))
    .filter((key) => key && TOOL_ORDER_DEFAULT.includes(key));
}

/**
 * Merge a partial reorder (sidebar or rail) into the full saved order.
 * Keys that only exist on the other list (e.g. email) keep their relative slot.
 */
export function mergeToolOrder(prevOrder, reorderedSubset, sourceKeys) {
  const result = [];
  let subsetIndex = 0;

  for (const key of prevOrder) {
    if (!TOOL_ORDER_DEFAULT.includes(key)) continue;
    if (sourceKeys.has(key)) {
      if (subsetIndex < reorderedSubset.length) {
        result.push(reorderedSubset[subsetIndex++]);
      }
    } else {
      result.push(key);
    }
  }

  for (; subsetIndex < reorderedSubset.length; subsetIndex++) {
    const key = reorderedSubset[subsetIndex];
    if (!result.includes(key)) result.push(key);
  }

  for (const key of TOOL_ORDER_DEFAULT) {
    if (!result.includes(key)) result.push(key);
  }

  return result;
}

function setListDragging(listRoot, active) {
  if (!listRoot) return;
  listRoot.classList.toggle('tool-reorder-is-dragging', Boolean(active));
}

const SORTABLE_DRAG_CLASSES = [
  'tool-sortable-ghost',
  'tool-sortable-chosen',
  'tool-sortable-drag',
  'tool-sortable-fallback',
  TOOL_REORDER_SOURCE_HOLD,
];

const DRAG_INLINE_STYLE_PROPS = [
  'opacity',
  'visibility',
  'transform',
  'webkitTransform',
  'transition',
  'height',
  'minHeight',
  'paddingTop',
  'paddingBottom',
  'margin',
  'overflow',
];

function clearSortableDragClasses(listRoot) {
  if (!listRoot) return;
  listRoot.querySelectorAll('.tool-reorderable').forEach((el) => {
    SORTABLE_DRAG_CLASSES.forEach((cls) => el.classList.remove(cls));
    DRAG_INLINE_STYLE_PROPS.forEach((prop) => el.style.removeProperty(prop));
  });
}

function listMatchesKeyOrder(listRoot, keys) {
  const current = readOrderFromList(listRoot);
  return current.length === keys.length && current.every((key, index) => key === keys[index]);
}

function reorderListToKeys(listRoot, keys) {
  if (!listRoot || listMatchesKeyOrder(listRoot, keys)) return;
  keys.forEach((key) => {
    const el = listRoot.querySelector(`[data-tool-key="${key}"]`);
    if (el) listRoot.appendChild(el);
  });
}

export function applyToolOrder(orderIds) {
  const railList = document.getElementById('rail-tools-list');
  const sidebarList = document.getElementById('sidebar-tools-list');
  const seq = orderIds.filter((id) => TOOL_ORDER_DEFAULT.includes(id));
  const trailing = TOOL_ORDER_DEFAULT.filter((id) => !seq.includes(id));
  const finalOrder = [...seq, ...trailing];

  if (railList) {
    reorderListToKeys(railList, finalOrder);
  }

  if (sidebarList) {
    const sidebarOrder = finalOrder.filter((key) => SIDEBAR_TOOL_KEYS.has(key));
    reorderListToKeys(sidebarList, sidebarOrder);
  }
}

function visibleReorderables(listRoot) {
  return [...listRoot.querySelectorAll(':scope > .tool-reorderable')]
    .filter((el) => el.getBoundingClientRect().height > 0);
}

function resolveDropIndexFromPointer(items, pointerY) {
  for (let index = 0; index < items.length; index++) {
    const rect = items[index].getBoundingClientRect();
    const midpoint = rect.top + rect.height / 2;
    if (pointerY < midpoint) return index;
  }
  return items.length;
}

function computeTargetKeyOrder(listRoot, dragged, pointerY) {
  const visibleItems = visibleReorderables(listRoot);
  if (!visibleItems.length) return null;

  const visibleOldIndex = visibleItems.indexOf(dragged);
  if (visibleOldIndex === -1) return null;

  const visibleTargetIndex = resolveDropIndexFromPointer(visibleItems, pointerY);
  if (visibleTargetIndex === visibleOldIndex) return null;

  const visibleKeys = visibleItems.map((el) => el.getAttribute('data-tool-key'));
  const draggedKey = dragged.getAttribute('data-tool-key');
  visibleKeys.splice(visibleOldIndex, 1);
  visibleKeys.splice(visibleTargetIndex, 0, draggedKey);

  const visibleSet = new Set(visibleItems);
  const allItems = [...listRoot.querySelectorAll(':scope > .tool-reorderable')];
  const targetKeys = [];
  let visibleKeyIndex = 0;
  for (const el of allItems) {
    if (visibleSet.has(el)) {
      targetKeys.push(visibleKeys[visibleKeyIndex++]);
    } else {
      targetKeys.push(el.getAttribute('data-tool-key'));
    }
  }
  return targetKeys;
}

function pointerReorderTarget(listRoot, dragged, pointerY) {
  if (!isToolsList(listRoot)) return null;
  const targetKeys = computeTargetKeyOrder(listRoot, dragged, pointerY);
  if (!targetKeys || listMatchesKeyOrder(listRoot, targetKeys)) return null;
  return targetKeys;
}

/** Sortable missed the index on a fast fling — use its built-in animated sort(). */
function reorderToPointer(listRoot, dragged, pointerY, animationMs, targetKeys = null) {
  const sortable = sortableForList(listRoot);
  if (!sortable) return false;

  const keys = targetKeys ?? pointerReorderTarget(listRoot, dragged, pointerY);
  if (!keys) return false;

  sortable.sort(keys, animationMs > 0);
  return true;
}

function persistToolOrderFromDom(sourceList, deferApplyMs = 0) {
  const subsetOrder = readOrderFromList(sourceList);
  if (!subsetOrder.length) return;

  const sourceKeys = sourceList.id === 'rail-tools-list' ? RAIL_TOOL_KEYS : SIDEBAR_TOOL_KEYS;
  const merged = mergeToolOrder(readToolOrder(), subsetOrder, sourceKeys);
  saveToolOrder(merged);

  const apply = () => applyToolOrder(merged);
  if (deferApplyMs > 0) {
    window.setTimeout(apply, deferApplyMs);
  } else {
    apply();
  }
}

function createSortableOptions(listRoot) {
  const reduceMotionUi = motionReduced();
  const touchUi = coarsePointer();
  const isSidebarList = listRoot.id === SIDEBAR_TOOLS_LIST_ID;
  const isRailList = listRoot.id === RAIL_TOOLS_LIST_ID;
  const animationMs = reduceMotionUi ? 0 : 240;
  // Sidebar rows are <div class="list-item"> with touch-action: pan-y for scroll.
  // Native HTML5 drag also leaves the row in-place with drag styling. forceFallback
  // appends a body-level clone that actually follows the pointer.
  const useFallback = true;
  return {
    animation: animationMs,
    easing: 'cubic-bezier(0.25, 1, 0.32, 1)',
    draggable: '.tool-reorderable',
    dataIdAttr: 'data-tool-key',
    filter: '.list-item-plus-btn, .list-item-plus-icon, .list-item-plus-label',
    preventOnFilter: true,
    ghostClass: 'tool-sortable-ghost',
    chosenClass: 'tool-sortable-chosen',
    dragClass: 'tool-sortable-drag',
    fallbackClass: 'tool-sortable-fallback',
    direction: 'vertical',
    swapThreshold: isSidebarList ? 0.5 : 0.65,
    invertSwap: false,
    emptyInsertThreshold: (isSidebarList || isRailList) ? LIST_EDGE_SLACK_PX : 5,
    delay: touchUi ? 120 : 0,
    delayOnTouchOnly: true,
    forceFallback: useFallback,
    fallbackOnBody: useFallback,
    fallbackTolerance: useFallback ? 3 : 0,
    onStart(evt) {
      activeDragListRoot = listRoot;
      dragStartIndex = evt.oldIndex ?? null;
      pointerReorderHandled = false;
      setListDragging(listRoot, true);
      startSidebarFallbackClamp(evt.item, evt, listRoot);
    },
    onChange() {
      if (isToolsList(listRoot)) {
        refreshSidebarDragContentExtents(listRoot);
      }
    },
    onUnchoose(evt) {
      if (!isToolsList(listRoot) || dragStartIndex == null) return;
      const currentIndex = [...listRoot.children].indexOf(evt.item);
      if (currentIndex !== dragStartIndex) return;
      const targetKeys = pointerReorderTarget(
        listRoot,
        evt.item,
        effectiveDragPointerY(),
      );
      if (!targetKeys) return;
      pointerReorderHandled = true;
      evt.item.classList.add(TOOL_REORDER_SOURCE_HOLD);
      reorderToPointer(
        listRoot,
        evt.item,
        sidebarDragPointerY,
        animationMs,
        targetKeys,
      );
      evt.item.classList.remove(TOOL_REORDER_SOURCE_HOLD);
    },
    onEnd(evt) {
      const pointerY = effectiveDragPointerY();
      const sortableMoved = evt.oldIndex != null
        && evt.newIndex != null
        && evt.oldIndex !== evt.newIndex;
      if (!sortableMoved && !pointerReorderHandled && isToolsList(listRoot)) {
        evt.item.classList.add(TOOL_REORDER_SOURCE_HOLD);
        pointerReorderHandled = reorderToPointer(
          listRoot,
          evt.item,
          pointerY,
          animationMs,
        );
        evt.item.classList.remove(TOOL_REORDER_SOURCE_HOLD);
      }
      const pointerReorder = pointerReorderHandled;
      const moved = sortableMoved || pointerReorder;

      stopSidebarFallbackClamp();
      setListDragging(listRoot, false);
      dragStartIndex = null;
      pointerReorderHandled = false;

      const animatingDrop = moved && animationMs > 0;
      if (!animatingDrop) {
        clearSortableDragClasses(listRoot);
      }

      if (moved) {
        toolDragSuppressClickUntil = performance.now() + 500;
        const deferPersist = isToolsList(listRoot) && animationMs > 0
          ? animationMs
          : 0;
        persistToolOrderFromDom(listRoot, deferPersist);
      }
      window.setTimeout(
        () => clearSortableDragClasses(listRoot),
        animatingDrop ? animationMs + 50 : 0,
      );
    },
  };
}

function wireClickSuppression() {
  if (clickSuppressionWired) return;
  clickSuppressionWired = true;

  const suppressIfNeeded = (e) => {
    if (performance.now() >= toolDragSuppressClickUntil) return;
    const target = e.target instanceof Element ? e.target : null;
    if (!target) return;
    if (!target.closest('#rail-tools-list, #sidebar-tools-list')) return;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
  };

  document.addEventListener('click', suppressIfNeeded, true);
  document.addEventListener('pointerup', suppressIfNeeded, true);
}

export function toggleSidebarReorder(enabled) {
  const body = document.body;
  body.classList.toggle('sidebar-reorder-enabled', enabled);

  const railList = document.getElementById('rail-tools-list');
  const sidebarList = document.getElementById('sidebar-tools-list');

  if (enabled) {
    if (typeof window.Sortable !== 'function') {
      console.warn(
        'SortableJS did not load. Sidebar tool reorder is disabled until it succeeds.',
      );
      return;
    }

    wireClickSuppression();

    if (railList && !railSortable) {
      railSortable = window.Sortable.create(railList, createSortableOptions(railList));
    }
    if (sidebarList && !sidebarSortable) {
      sidebarSortable = window.Sortable.create(sidebarList, createSortableOptions(sidebarList));
    }
  } else {
    if (railSortable) {
      railSortable.destroy();
      railSortable = null;
    }
    if (sidebarSortable) {
      sidebarSortable.destroy();
      sidebarSortable = null;
    }
  }
}

if (typeof window !== 'undefined') {
  window.toggleSidebarReorder = toggleSidebarReorder;
}

export function initSidebarReorder() {
  applyToolOrder(readToolOrder());

  let enabled = false;
  if (typeof window !== 'undefined' && window.loadUIVis) {
    const state = window.loadUIVis();
    enabled = state['sidebar-tool-reorder'] === true;
  }
  toggleSidebarReorder(enabled);
}
