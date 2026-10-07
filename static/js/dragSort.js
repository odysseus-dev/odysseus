// static/js/dragSort.js

/**
 * Vertical drag-and-drop sorting with magnetic snap behavior
 */

import Storage from './storage.js';

const instances = new Map();

/**
 * Make a container's children sortable via vertical drag
 */
export function enable(containerId, itemSelector, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) {
    console.warn('[DragSort] container not found:', containerId);
    return;
  }

  // Allow multiple instances per container via instanceKey
  const key = options.instanceKey || containerId;

  // Clean up previous instance
  if (instances.has(key)) {
    instances.get(key).cleanup();
    instances.delete(key);
  }

  const config = {
    onReorder: options.onReorder || null,
    handleSelector: options.handleSelector || null,
    excludeSelector: options.excludeSelector || null,
    storageKey: options.storageKey || null,
  };

  let draggedEl = null;
  let placeholder = null;
  let offsetY = 0;
  let items = [];

  function getItems() {
    let all = Array.from(container.querySelectorAll(itemSelector));
    if (config.excludeSelector) {
      all = all.filter(el => !el.matches(config.excludeSelector));
    }
    return all;
  }

  function createPlaceholder(height) {
    const ph = document.createElement('div');
    ph.className = 'drag-placeholder';
    ph.style.height = height + 'px';
    ph.style.margin = '4px 0';
    return ph;
  }

  // --- Shared drag logic ---

  function startDrag(clientY, item) {
    const rect = item.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const relativeTop = rect.top - containerRect.top + container.scrollTop;
    const relativeLeft = rect.left - containerRect.left;

    offsetY = clientY - rect.top;
    items = getItems();
    draggedEl = item;

    if (getComputedStyle(container).position === 'static') {
      container.style.position = 'relative';
    }

    placeholder = createPlaceholder(rect.height);
    item.parentNode.insertBefore(placeholder, item);

    item.classList.add('dragging');
    Object.assign(item.style, {
      position: 'absolute',
      width: rect.width + 'px',
      left: relativeLeft + 'px',
      top: relativeTop + 'px',
      zIndex: '9999',
      pointerEvents: 'none',
      margin: '0',
      boxSizing: 'border-box',
      transition: 'none'
    });
  }

  function moveDrag(clientY) {
    if (!draggedEl) return;
    const containerRect = container.getBoundingClientRect();
    const newTop = clientY - offsetY - containerRect.top + container.scrollTop;
    draggedEl.style.top = newTop + 'px';

    const otherItems = items.filter(i => i !== draggedEl);
    const dragRect = draggedEl.getBoundingClientRect();
    const dragCenter = dragRect.top + dragRect.height / 2;
    let insertBefore = null;

    for (const item of otherItems) {
      const rect = item.getBoundingClientRect();
      const itemCenter = rect.top + rect.height / 2;
      if (dragCenter < itemCenter) {
        insertBefore = item;
        break;
      }
    }

    if (insertBefore) {
      if (placeholder.nextElementSibling !== insertBefore) {
        container.insertBefore(placeholder, insertBefore);
      }
    } else if (otherItems.length > 0) {
      const lastItem = otherItems[otherItems.length - 1];
      if (placeholder !== lastItem.nextElementSibling) {
        container.insertBefore(placeholder, lastItem.nextElementSibling);
      }
    }
  }

  function endDrag() {
    if (!draggedEl) return;

    const phRect = placeholder.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const snapTop = phRect.top - containerRect.top + container.scrollTop;
    draggedEl.style.transition = 'top 0.08s ease-out';
    draggedEl.style.top = snapTop + 'px';

    setTimeout(() => {
      placeholder.parentNode.insertBefore(draggedEl, placeholder);
      placeholder.remove();
      draggedEl.classList.remove('dragging');
      draggedEl.style.cssText = '';

      if (config.storageKey) {
        const ids = getItems().map(el =>
          el.dataset.sessionId || el.dataset.modelId || el.dataset.filePath
        ).filter(Boolean);
        if (ids.length) {
          Storage.setJSON(config.storageKey, ids);
        }
      }
      if (config.onReorder) {
        config.onReorder(getItems());
      }

      draggedEl = null;
      placeholder = null;
      items = [];
    }, 80);
  }

  // --- Mouse events ---

  function onMouseDown(e) {
    if (e.button !== 0) return;
    if (config.handleSelector && !e.target.closest(config.handleSelector)) return;
    const item = e.target.closest(itemSelector);
    if (!item || !container.contains(item)) return;
    if (config.excludeSelector && item.matches(config.excludeSelector)) return;

    e.preventDefault();
    e.stopPropagation();
    startDrag(e.clientY, item);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }

  function onMouseMove(e) { moveDrag(e.clientY); }

  function onMouseUp() {
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    endDrag();
  }

  // --- Touch events (long-press to start) ---

  let _touchTimer = null;
  let _touchStartY = 0;
  let _touchItem = null;

  function onTouchStart(e) {
    if (_pointerId !== null) return;
    // Don't start on buttons/inputs.
    if (e.target.closest('button, input, select, a')) return;
    // Respect handleSelector on touch too — long-press anywhere was
    // unintentionally letting users start a reorder from the whole row.
    if (config.handleSelector && !e.target.closest(config.handleSelector)) return;
    const item = e.target.closest(itemSelector);
    if (!item || !container.contains(item)) return;
    if (config.excludeSelector && item.matches(config.excludeSelector)) return;

    _touchItem = item;
    const startY = e.touches[0].clientY;
    _touchStartY = startY;

    // Long-press: 400ms hold to initiate drag
    _touchTimer = setTimeout(() => {
      _touchTimer = null;
      if (!_touchItem) return;
      // Haptic feedback if available
      if (navigator.vibrate) navigator.vibrate(30);
      startDrag(startY, _touchItem);
      _touchItem.classList.add('touch-dragging');
    }, 400);
  }

  function onTouchMove(e) {
    // If long-press hasn't fired yet, cancel if finger moved too much
    if (_touchTimer) {
      const dy = Math.abs(e.touches[0].clientY - _touchStartY);
      if (dy > 10) {
        clearTimeout(_touchTimer);
        _touchTimer = null;
        _touchItem = null;
      }
      return;
    }

    // If dragging, prevent scroll and move
    if (draggedEl) {
      e.preventDefault();
      moveDrag(e.touches[0].clientY);
    }
  }

  function onTouchEnd() {
    if (_touchTimer) {
      clearTimeout(_touchTimer);
      _touchTimer = null;
      _touchItem = null;
      return;
    }
    if (draggedEl) {
      draggedEl.classList.remove('touch-dragging');
      endDrag();
    }
  }

  // Modern mobile browsers expose touch as pointer events. Use pointer
  // capture for pen input so a reorder continues even when the row becomes
  // absolutely positioned or the pen leaves its original handle. Finger
  // gestures stay on the touch path below because browsers may cancel a
  // synthetic pointer stream while preserving touch events.
  let _pointerId = null;
  function onPointerDown(e) {
    if (e.pointerType !== 'pen') return;
    if (_touchTimer || draggedEl) return;
    if (e.target.closest('button, input, select, a')) return;
    if (config.handleSelector && !e.target.closest(config.handleSelector)) return;
    const item = e.target.closest(itemSelector);
    if (!item || !container.contains(item)) return;
    if (config.excludeSelector && item.matches(config.excludeSelector)) return;
    _pointerId = e.pointerId;
    _touchItem = item;
    _touchStartY = e.clientY;
    try { container.setPointerCapture(_pointerId); } catch {}
    _touchTimer = setTimeout(() => {
      _touchTimer = null;
      if (!_touchItem) return;
      if (navigator.vibrate) navigator.vibrate(30);
      startDrag(_touchStartY, _touchItem);
      _touchItem.classList.add('touch-dragging');
    }, 400);
  }

  function onPointerMove(e) {
    if (e.pointerId !== _pointerId) return;
    if (_touchTimer) {
      if (Math.abs(e.clientY - _touchStartY) > 10) {
        clearTimeout(_touchTimer);
        _touchTimer = null;
        _touchItem = null;
      }
      return;
    }
    if (draggedEl) {
      e.preventDefault();
      moveDrag(e.clientY);
    }
  }

  function onPointerEnd(e) {
    if (e.pointerId !== _pointerId) return;
    if (_touchTimer) {
      clearTimeout(_touchTimer);
      _touchTimer = null;
      _touchItem = null;
    } else if (draggedEl) {
      draggedEl.classList.remove('touch-dragging');
      endDrag();
    }
    try { container.releasePointerCapture(_pointerId); } catch {}
    _pointerId = null;
  }

  container.addEventListener('mousedown', onMouseDown);
  container.addEventListener('pointerdown', onPointerDown);
  container.addEventListener('pointermove', onPointerMove, { passive: false });
  container.addEventListener('pointerup', onPointerEnd);
  container.addEventListener('pointercancel', onPointerEnd);
  container.addEventListener('touchstart', onTouchStart, { passive: true });
  container.addEventListener('touchmove', onTouchMove, { passive: false });
  container.addEventListener('touchend', onTouchEnd);
  container.addEventListener('touchcancel', onTouchEnd);

  const instance = {
    cleanup: () => {
      container.removeEventListener('mousedown', onMouseDown);
      container.removeEventListener('pointerdown', onPointerDown);
      container.removeEventListener('pointermove', onPointerMove);
      container.removeEventListener('pointerup', onPointerEnd);
      container.removeEventListener('pointercancel', onPointerEnd);
      container.removeEventListener('touchstart', onTouchStart);
      container.removeEventListener('touchmove', onTouchMove);
      container.removeEventListener('touchend', onTouchEnd);
      container.removeEventListener('touchcancel', onTouchEnd);
    },
    refresh: () => { items = getItems(); }
  };
  instances.set(key, instance);
  return instance;
}

const dragSortModule = { enable };
export default dragSortModule;
window.dragSortModule = dragSortModule;
