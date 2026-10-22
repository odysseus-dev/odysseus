/** Desktop and large-touch-device card reorder gestures. */
export function createCardReorder({
  lifecycle,
  _isNotesMobileMode,
  persistOrder,
  state
}) {
  function _wireCardReorder(body) {
    lifecycle.release(body);
    // Drag-reorder notes on pointer/mouse devices. Mobile uses the custom
    // placeholder sorter below `_bindLongPressDrag`; native HTML5 dragging is
    // unreliable on touch browsers and can compete with the long-press flow.
    if (!_isNotesMobileMode()) {
      body.querySelectorAll('.note-card').forEach(card => {
        card.addEventListener('dragstart', (e) => {
          if (e.target.closest('.note-checkbox, .note-card-x, .note-card-select, .note-card-pin, .note-card-action, .note-card-color-dot, .note-card-title, .note-card-edit, .note-card-edit-corner, .note-card-done, .note-card-corner-menu, .note-agent-tag, .note-card-label-chip')) {
            e.preventDefault();
            return;
          }
          card.classList.add('dragging');
          body.classList.add('drag-active');
          e.dataTransfer.effectAllowed = 'move';
          try { e.dataTransfer.setData('text/plain', card.dataset.noteId); } catch {}
        });
        card.addEventListener('dragend', async () => {
          card.classList.remove('dragging');
          body.classList.remove('drag-active');
          body.querySelectorAll('.drop-before, .drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
          const ids = [...body.querySelectorAll('.note-card')].map(c => c.dataset.noteId);
          try { await persistOrder(ids); }
          catch {}
        });
      });
    }
    // Track which card we last swapped with so a single hover-over triggers
    // one swap, not a jitter as the pointer keeps moving inside the same card.
    let _lastSwapId = null;
    function _maybeSwap(dragging, clientX, clientY) {
      const target = document.elementFromPoint(clientX, clientY)?.closest('.note-card');
      if (!target || target === dragging || !body.contains(target)) return;
      const id = target.dataset.noteId;
      if (id === _lastSwapId) return;
      // FLIP across ALL siblings. In list view only `target` moves visually, but
      // in grid view (2-col) and when the pinned-section grid-column-start rule
      // shifts, several cards reflow at once. Capture every card's pre-swap rect,
      // do the DOM swap, then animate any that actually moved. The dragging card
      // is excluded : it's already finger-tracked via translate3d.
      const cards = [...body.querySelectorAll('.note-card')].filter(c => c !== dragging);
      const prevRects = new Map(cards.map(c => [c, c.getBoundingClientRect()]));
      const draggingNext = dragging.nextSibling === target ? dragging : dragging.nextSibling;
      body.insertBefore(dragging, target);
      body.insertBefore(target, draggingNext);
      for (const c of cards) {
        const prev = prevRects.get(c);
        const next = c.getBoundingClientRect();
        const dx = prev.left - next.left;
        const dy = prev.top - next.top;
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
        c.style.transition = 'none';
        c.style.transform = `translate(${dx}px, ${dy}px)`;
        requestAnimationFrame(() => {
          c.style.transition = 'transform 0.22s cubic-bezier(0.34, 1.2, 0.64, 1)';
          c.style.transform = '';
          c.addEventListener('transitionend', () => { c.style.transition = ''; }, { once: true });
        });
      }
      _lastSwapId = id;
    }
    lifecycle.listen(body, body, 'dragover', (e) => {
      e.preventDefault();
      const dragging = body.querySelector('.note-card.dragging');
      if (!dragging) return;
      _maybeSwap(dragging, e.clientX, e.clientY);
    });
    lifecycle.listen(body, body, 'dragend', () => { _lastSwapId = null; });

    // Legacy touch drag for larger touch devices only. Phone-sized Notes uses
    // the placeholder sorter wired by `_bindLongPressDrag`; running both flows
    // makes one press start two independent drag sessions.
    if (!_isNotesMobileMode() && 'ontouchstart' in window) {
      let dragCard = null;
      let isDragging = false;
      let longPressTimer = null;
      let startX = 0, startY = 0;
      const LONG_PRESS_MS = 350;
      const MOVE_THRESHOLD_PX = 8;
      const _selectorSkip = '.note-checkbox, .note-card-x, .note-card-select, .note-card-pin, .note-card-action, .note-card-color-dot, .note-card-title, .note-card-edit, .note-card-edit-corner, .note-card-done, .note-card-corner-menu, .note-agent-tag, .note-card-label-chip, input, textarea, button, a';

      // Anchor for the finger-follow transform. Recomputed after every swap so
      // the card stays under the finger across reorderings.
      let anchorX = 0, anchorY = 0;
      const _follow = (clientX, clientY) => {
        if (!dragCard) return;
        const dx = clientX - anchorX;
        const dy = clientY - anchorY;
        // Compose with the CSS .dragging transform (scale + rotate).
        dragCard.style.transform = `translate3d(${dx}px, ${dy}px, 0) scale(1.03) rotate(-0.6deg)`;
      };
      const _reanchor = (clientX, clientY) => {
        if (!dragCard) return;
        // Clear any prior translate so the card resettles in its natural slot,
        // then anchor at the finger's current position. Subsequent _follow calls
        // translate by (finger - anchor), keeping the finger at the same relative
        // spot on the card it had when we re-anchored.
        dragCard.style.transform = '';
        anchorX = clientX;
        anchorY = clientY;
      };

      const _endDrag = (committed) => {
        clearTimeout(longPressTimer);
        longPressTimer = null;
        if (dragCard) {
          dragCard.classList.remove('dragging');
          dragCard.style.transform = '';
        }
        body.classList.remove('drag-active');
        _lastSwapId = null;
        if (isDragging) {
          document.documentElement.style.touchAction = '';
          if (committed) {
            const ids = [...body.querySelectorAll('.note-card')].map(c => c.dataset.noteId);
            persistOrder(ids).catch(() => {});
          }
        }
        dragCard = null;
        isDragging = false;
      };
      lifecycle.onDispose(body, () => _endDrag(false));

      lifecycle.listen(body, body, 'touchstart', (e) => {
        if (state.selectMode) return;
        const card = e.target.closest('.note-card');
        if (!card) return;
        if (e.target.closest(_selectorSkip)) return;
        const t = e.touches[0];
        startX = t.clientX; startY = t.clientY;
        dragCard = card;
        longPressTimer = setTimeout(() => {
          if (!dragCard) return;
          isDragging = true;
          dragCard.classList.add('dragging');
          body.classList.add('drag-active');
          document.documentElement.style.touchAction = 'none';
          _reanchor(startX, startY);
          try { if (navigator.vibrate) navigator.vibrate(15); } catch {}
        }, LONG_PRESS_MS);
      }, { passive: true });

      lifecycle.listen(body, body, 'touchmove', (e) => {
        if (!dragCard) return;
        const t = e.touches[0];
        if (!isDragging) {
          // Movement before long-press fires = user is scrolling; cancel pickup.
          if (Math.abs(t.clientX - startX) > MOVE_THRESHOLD_PX || Math.abs(t.clientY - startY) > MOVE_THRESHOLD_PX) {
            clearTimeout(longPressTimer);
            longPressTimer = null;
            dragCard = null;
          }
          return;
        }
        e.preventDefault();
        // Live-follow the finger first, then check for a swap. After a swap the
        // card's natural slot moves, so we re-anchor and re-apply the offset.
        _follow(t.clientX, t.clientY);
        const before = dragCard.parentNode && [...dragCard.parentNode.children].indexOf(dragCard);
        _maybeSwap(dragCard, t.clientX, t.clientY);
        const after = dragCard.parentNode && [...dragCard.parentNode.children].indexOf(dragCard);
        if (before !== after) {
          _reanchor(t.clientX, t.clientY);
          _follow(t.clientX, t.clientY);
        }
      }, { passive: false });

      lifecycle.listen(body, body, 'touchend', () => _endDrag(true));
      lifecycle.listen(body, body, 'touchcancel', () => _endDrag(false));
    }
  }

  return { _wireCardReorder };
}
