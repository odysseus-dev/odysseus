/** Mobile checklist grip dragging and document listener ownership. */
export function createChecklistReorder({
  lifecycle
}) {
  let _clDrag = null;

  let _clDocBound = false;
  let _clOwner = null;

  function _wireChecklistTouchReorder(form) {
    const container = form.querySelector('.note-checklist-inputs');
    if (!container) return;
    container.querySelectorAll('.note-cl-grip').forEach(grip => {
      if (grip.dataset.touchBound === '1') return;
      grip.dataset.touchBound = '1';
      grip.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1) return;
        e.preventDefault();
        const row = grip.closest('.note-cl-row');
        if (!row) return;
        _beginChecklistGrab(row, container, e.touches[0]);
      }, { passive: false });
    });
    if (!_clDocBound || _clOwner !== form) {
      if (_clOwner) lifecycle.release(_clOwner);
      _clOwner = form;
      lifecycle.listen(form, document, 'touchmove', _onClTouchMove, { passive: false });
      lifecycle.listen(form, document, 'touchend',  _onClTouchEnd,  { passive: true });
      lifecycle.listen(form, document, 'touchcancel', _onClTouchEnd, { passive: true });
      _clDocBound = true;
    }
  }

  function _beginChecklistGrab(row, container, touch) {
    if (_clDrag) return;
    const rect = row.getBoundingClientRect();
    const placeholder = document.createElement('div');
    placeholder.className = 'note-cl-row-placeholder';
    placeholder.style.height = rect.height + 'px';
    container.insertBefore(placeholder, row);

    row.classList.add('note-cl-row-dragging');
    row.style.position = 'fixed';
    row.style.left = rect.left + 'px';
    row.style.top  = rect.top + 'px';
    row.style.width = rect.width + 'px';
    row.style.zIndex = '10002';
    row.style.pointerEvents = 'none';

    _clDrag = {
      row, placeholder, container,
      grabOffsetX: touch.clientX - rect.left,
      grabOffsetY: touch.clientY - rect.top,
    };
    try { navigator.vibrate?.(8); } catch {}
  }

  function _onClTouchMove(e) {
    if (!_clDrag) return;
    if (e.touches.length !== 1) return;
    e.preventDefault();
    const t = e.touches[0];
    const { row, placeholder, container } = _clDrag;
    row.style.left = (t.clientX - _clDrag.grabOffsetX) + 'px';
    row.style.top  = (t.clientY - _clDrag.grabOffsetY) + 'px';

    const under = document.elementFromPoint(t.clientX, t.clientY);
    const target = under && under.closest
      ? under.closest('.note-cl-row:not(.note-cl-row-dragging)')
      : null;
    if (!target || target === row) return;
    if (target.parentNode !== container) return;

    const tRect = target.getBoundingClientRect();
    const targetMidY = tRect.top + tRect.height / 2;
    if (t.clientY < targetMidY) {
      if (placeholder.nextElementSibling !== target) {
        container.insertBefore(placeholder, target);
      }
    } else {
      if (target.nextElementSibling !== placeholder) {
        container.insertBefore(placeholder, target.nextElementSibling);
      }
    }
  }

  function _onClTouchEnd() {
    if (!_clDrag) return;
    const { row, placeholder } = _clDrag;
    _clDrag = null;
    const phRect = placeholder.getBoundingClientRect();
    row.style.transition = 'left 0.18s ease, top 0.18s ease';
    row.style.left = phRect.left + 'px';
    row.style.top  = phRect.top + 'px';
    setTimeout(() => {
      placeholder.parentNode.insertBefore(row, placeholder);
      placeholder.remove();
      row.classList.remove('note-cl-row-dragging');
      row.style.cssText = '';
      // Order is persisted as part of the form's normal save (rows are
      // re-serialized in DOM order via _collectItems).
    }, 200);
  }

  function dispose() {
    if (_clDrag) _onClTouchEnd();
    _clDocBound = false;
    _clOwner = null;
  }
  return { dispose, _wireChecklistTouchReorder, _beginChecklistGrab, _onClTouchMove, _onClTouchEnd };
}
