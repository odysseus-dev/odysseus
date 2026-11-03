/** Mobile card long press, placeholder drag and reorder persistence. */
export function createMobileReorder({
  lifecycle,
  _applyMasonry,
  _enterSelectMode,
  commitMobileOrder,
  app,
}) {
  function _bindLongPressDrag(card) {
    let pressTimer = null;
    let startX = 0, startY = 0;
    let armed = false;
    const CANCEL_PX = 8;
    const HOLD_MS = 450;

    card.addEventListener('touchstart', (e) => {
      // Don't fight scroll on touchpoints over real interactive children
      // (in mobile-mode they're CSS-hidden anyway, but be defensive).
      if (e.target.closest('button, input, a, .note-form')) return;
      if (e.touches.length !== 1) return;
      armed = true;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      pressTimer = setTimeout(() => {
        if (!armed) return;
        try { navigator.vibrate?.(15); } catch {}
        app.setSuppressNextNoteTapId(card.dataset.noteId);
        _enterSelectMode(card.dataset.noteId);
      }, HOLD_MS);
    }, { passive: true });
    card.addEventListener('touchmove', (e) => {
      if (!armed) return;
      const t = e.touches[0];
      if (Math.abs(t.clientX - startX) > CANCEL_PX || Math.abs(t.clientY - startY) > CANCEL_PX) {
        armed = false;
        if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
      }
    }, { passive: true });
    const cancel = () => {
      armed = false;
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    };
    lifecycle.onDispose(card, cancel);
    card.addEventListener('touchend', cancel, { passive: true });
    card.addEventListener('touchcancel', cancel, { passive: true });
  }

  let _dragState = null;

  let _docDragHandlersBound = false;

  function _enterDragMode(initialCard, initialTouch) {
    document.body.classList.add('notes-drag-mode');
    document.querySelectorAll('.note-card').forEach(_setupDragForCard);
    if (!_docDragHandlersBound) {
      lifecycle.listen(document.getElementById('notes-pane'), document, 'touchmove', _onDocTouchMove, { passive: false });
      lifecycle.listen(document.getElementById('notes-pane'), document, 'touchend',  _onDocTouchEnd,  { passive: true });
      lifecycle.listen(document.getElementById('notes-pane'), document, 'touchcancel', _onDocTouchEnd, { passive: true });
      _docDragHandlersBound = true;
    }
    // Auto-grab the card the user long-pressed : they're already holding
    // their finger on it, so begin the drag straight away. Releasing
    // (touchend) commits the reorder AND exits drag mode in one motion.
    if (initialCard && initialTouch) {
      _beginGrab(initialCard, initialTouch);
    }
  }

  function _exitDragMode() {
    document.body.classList.remove('notes-drag-mode');
    if (_dragState) {
      // Defensive: if exit fires while a drag is in flight, snap the card back.
      _onDocTouchEnd();
    }
    // Leave _docDragHandlersBound true so re-entering drag mode reuses them.
  }

  function _setupDragForCard(card) {
    if (card.dataset.dragBound === '1') return;
    card.dataset.dragBound = '1';
    card.addEventListener('touchstart', (e) => {
      if (!document.body.classList.contains('notes-drag-mode')) return;
      if (e.touches.length !== 1) return;
      if (_dragState) return;
      e.preventDefault();
      e.stopPropagation();
      _beginGrab(card, e.touches[0]);
    }, { passive: false });
  }

  function _beginGrab(card, touch) {
    const rect = card.getBoundingClientRect();
    const prevStyle = card.getAttribute('style') || '';
    // Placeholder fills the card's old slot so the grid layout doesn't reflow.
    const placeholder = document.createElement('div');
    placeholder.className = 'note-card-placeholder';
    placeholder.style.width = rect.width + 'px';
    placeholder.style.height = rect.height + 'px';
    placeholder.style.margin = getComputedStyle(card).margin;
    if (card.style.gridRowEnd) placeholder.style.gridRowEnd = card.style.gridRowEnd;
    const grid = card.parentNode;
    grid.insertBefore(placeholder, card);

    // Detach the card visually : fixed-position, anchored to the finger.
    card.classList.add('note-card-dragging');
    card.style.position = 'fixed';
    card.style.left = rect.left + 'px';
    card.style.top  = rect.top + 'px';
    card.style.width  = rect.width + 'px';
    card.style.height = rect.height + 'px';
    card.style.margin = '0';
    card.style.zIndex = '10001';
    // pointer-events:none so elementFromPoint sees the card BENEATH the finger
    card.style.pointerEvents = 'none';

    _dragState = {
      card, placeholder, grid, prevStyle,
      grabOffsetX: touch.clientX - rect.left,
      grabOffsetY: touch.clientY - rect.top,
    };
    try { navigator.vibrate?.(8); } catch {}
  }

  function _onDocTouchMove(e) {
    if (!_dragState) return;
    if (e.touches.length !== 1) return;
    e.preventDefault();
    const touch = e.touches[0];
    const { card, placeholder, grid } = _dragState;
    card.style.left = (touch.clientX - _dragState.grabOffsetX) + 'px';
    const quickAdd = grid.querySelector('.notes-quick-add');
    const minTop = quickAdd ? quickAdd.getBoundingClientRect().bottom + 4 : grid.getBoundingClientRect().top;
    const maxTop = Math.max(minTop, window.innerHeight - card.getBoundingClientRect().height - 8);
    const nextTop = Math.max(minTop, Math.min(maxTop, touch.clientY - _dragState.grabOffsetY));
    card.style.top = nextTop + 'px';

    const hitY = Math.max(minTop + 1, Math.min(window.innerHeight - 1, touch.clientY));
    const under = document.elementFromPoint(touch.clientX, hitY);
    const target = under && under.closest
      ? under.closest('.note-card:not(.note-card-dragging)')
      : null;
    if (!target || target === card) return;
    if (target.parentNode !== grid) return;

    // Move the PLACEHOLDER (not the card) above or below the target depending
    // on which half of the target the finger is in. This is the hysteresis
    // that stops the oscillation : once the placeholder moves past a card,
    // the cursor has to cross THAT card's midpoint in the other direction
    // to swap back.
    const tRect = target.getBoundingClientRect();
    const targetMidY = tRect.top + tRect.height / 2;
    if (touch.clientY < targetMidY) {
      if (placeholder.nextElementSibling !== target) {
        grid.insertBefore(placeholder, target);
      }
    } else {
      if (target.nextElementSibling !== placeholder) {
        grid.insertBefore(placeholder, target.nextElementSibling);
      }
    }
  }

  function _onDocTouchEnd() {
    if (!_dragState) return;
    const { card, placeholder, grid, prevStyle } = _dragState;
    _dragState = null;
    // Animate the card from its current fixed position to where the
    // placeholder sits, then re-parent and clear inline styles. Drag
    // mode auto-exits once the snap finishes : release = done.
    const phRect = placeholder.getBoundingClientRect();
    card.style.transition = 'left 0.2s ease, top 0.2s ease';
    card.style.left = phRect.left + 'px';
    card.style.top  = phRect.top + 'px';
    setTimeout(() => {
      placeholder.parentNode.insertBefore(card, placeholder);
      placeholder.remove();
      card.classList.remove('note-card-dragging');
      // Restore the card's pre-drag inline styles. Mobile masonry stores
      // grid-row-end inline, and custom backgrounds use inline style too; wiping
      // cssText made dropped cards collapse into neighboring notes in grid view.
      if (prevStyle) card.setAttribute('style', prevStyle);
      else card.removeAttribute('style');
      _applyMasonry(grid);
      _commitNoteReorder();
      // One drag, one exit : release ends the rearrange session entirely.
      if (document.body.classList.contains('notes-drag-mode')) {
        document.body.classList.remove('notes-drag-mode');
      }
    }, 210);
  }

  async function _commitNoteReorder() {
    const grid = document.querySelector('#notes-pane .notes-pane-body');
    if (!grid) return;
    const ids = Array.from(grid.querySelectorAll('.note-card')).map(c => c.dataset.noteId).filter(Boolean);
    if (!ids.length) return;
    await commitMobileOrder(ids);
  }

  function dispose() {
    if (_dragState) {
      const { card, placeholder, prevStyle } = _dragState;
      _dragState = null;
      placeholder.parentNode?.insertBefore(card, placeholder);
      placeholder.remove();
      card.classList.remove('note-card-dragging');
      if (prevStyle) card.setAttribute('style', prevStyle);
      else card.removeAttribute('style');
    }
    document.body.classList.remove('notes-drag-mode');
    _docDragHandlersBound = false;
  }
  return { dispose, _bindLongPressDrag, _enterDragMode, _exitDragMode, _setupDragForCard, _beginGrab, _onDocTouchMove, _onDocTouchEnd, _commitNoteReorder };
}
