/** Card reflow animation and the owned masonry resize observer. */
export function createLayout({ isOpen = () => true } = {}) {
  function _captureCardPositions() {
    const body = document.querySelector('#notes-pane .notes-pane-body');
    if (!body) return null;
    const positions = new Map();
    body.querySelectorAll('.note-card').forEach(card => {
      const id = card.dataset.noteId;
      if (id) positions.set(id, card.getBoundingClientRect());
    });
    return positions;
  }

  // FLIP every card that moved. All rects are read first and all transforms
  // written afterwards: reading a rect right after writing the previous
  // card's transform forces one full layout per card, which froze the tab
  // for minutes on a few hundred notes.
  function _animateReflow(prevPositions, cards = null) {
    if (!prevPositions || !prevPositions.size) return;
    const body = document.querySelector('#notes-pane .notes-pane-body');
    if (!body || !body.isConnected) return;
    const moves = [];
    for (const card of cards || body.querySelectorAll('.note-card')) {
      const prev = prevPositions.get(card.dataset.noteId);
      if (!prev) continue;
      const next = card.getBoundingClientRect();
      const dx = prev.left - next.left;
      const dy = prev.top - next.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      moves.push([card, dx, dy]);
    }
    for (const [card, dx, dy] of moves) {
      // Invert: jump back to old position
      card.style.transition = 'none';
      card.style.transform = `translate(${dx}px, ${dy}px)`;
    }
    if (!moves.length) return;
    // Play: animate to 0
    requestAnimationFrame(() => {
      for (const [card] of moves) {
        card.style.transition = 'transform 0.25s cubic-bezier(0.34, 1.2, 0.64, 1)';
        card.style.transform = '';
        card.addEventListener('transitionend', () => {
          card.style.transition = '';
        }, { once: true });
      }
    });
  }

  function _applyMasonry(body) {
    if (!body || !body.isConnected) return;
    const pane = body.closest('.notes-pane');
    const isGrid = pane?.classList.contains('notes-view-grid');
    const isMobileGrid = isGrid && window.matchMedia('(max-width: 768px)').matches;
    // Tear down any prior observer (defensive : _renderNotes wipes body.innerHTML).
    if (_masonryObserver) { try { _masonryObserver.disconnect(); } catch {} _masonryObserver = null; }
    // A save that resolves after close re-renders the leaving pane; don't
    // re-create the observer that panel teardown already disposed.
    if (!isOpen()) return;
    _recomputeCard = null;
    if (!isGrid) {
      // Clear any leftover inline spans so list view lays out normally.
      body.querySelectorAll('.note-card, .notes-label-toggle, .notes-labels-bar, .notes-quick-add, .doclib-chip-scroll-frame:has(> .notes-labels-bar)').forEach(c => { c.style.gridRowEnd = ''; });
      return;
    }
    const ROW_PX = 4;
    const spanForHeight = (h) => Math.max(1, Math.ceil(h / ROW_PX));
    const recomputeFullRows = () => {
      const quickAdd = body.querySelector('.notes-quick-add');
      const labelsToggle = body.querySelector('.notes-label-toggle');
      const labelsBar = body.querySelector('.notes-labels-bar');
      const labelsLayout = labelsBar?.parentElement?.classList.contains('doclib-chip-scroll-frame')
        ? labelsBar.parentElement
        : labelsBar;
      if (labelsToggle && getComputedStyle(labelsToggle).display !== 'none') {
        labelsToggle.style.gridRowEnd = `span ${Math.max(1, spanForHeight(labelsToggle.scrollHeight))}`;
      }
      if (labelsLayout && getComputedStyle(labelsLayout).display !== 'none') {
        const shave = isMobileGrid ? 4 : 0;
        labelsLayout.style.gridRowEnd = `span ${Math.max(1, spanForHeight(labelsLayout.scrollHeight) - shave)}`;
      }
      body.querySelectorAll('.notes-shared-header').forEach(header => {
        header.style.gridRowEnd = `span ${Math.max(1, spanForHeight(header.scrollHeight + 16))}`;
      });
      if (quickAdd) {
        const shave = isMobileGrid ? 4 : 0;
        quickAdd.style.gridRowEnd = `span ${Math.max(1, spanForHeight(quickAdd.scrollHeight + 10) - shave)}`;
      }
    };
    // Read every card's height, then write every span: interleaving the two
    // forces a layout per card.
    const recompute = (cards) => {
      // scrollHeight returns the natural content height : card.getBoundingClientRect()
      // would return the grid cell height (collapsed to 4px until the span is set,
      // which is the value we're trying to compute).
      const heights = Array.from(cards, card => card.scrollHeight + (isMobileGrid ? 6 : 8));
      Array.from(cards).forEach((card, i) => {
        if (heights[i] > 0) card.style.gridRowEnd = `span ${spanForHeight(heights[i])}`;
      });
    };
    recomputeFullRows();
    recompute(body.querySelectorAll('.note-card'));
    _recomputeCard = card => recompute([card]);
    // Watch masonry participants : content can grow (image load, todo edits,
    // quick-add expansion), and stale spans are what cause visual merging.
    if ('ResizeObserver' in window) {
      _masonryObserver = new ResizeObserver(entries => {
        const cards = entries.map(e => e.target).filter(t => t.classList.contains('note-card'));
        if (cards.length) recompute(cards);
        if (cards.length < entries.length) recomputeFullRows();
      });
      body.querySelectorAll('.note-card').forEach(c => _masonryObserver.observe(c));
    body.querySelectorAll('.notes-label-toggle, .notes-quick-add').forEach(c => _masonryObserver.observe(c));
    const labelsBar = body.querySelector('.notes-labels-bar');
    const labelsLayout = labelsBar?.parentElement?.classList.contains('doclib-chip-scroll-frame')
      ? labelsBar.parentElement
      : labelsBar;
    if (labelsLayout) _masonryObserver.observe(labelsLayout);
    }
  }

  let _masonryObserver = null;
  let _recomputeCard = null;

  /** A card replaced in place: size its grid span and watch it like the others. */
  function _layoutCard(oldCard, card) {
    if (_masonryObserver) {
      if (oldCard) _masonryObserver.unobserve(oldCard);
      _masonryObserver.observe(card);
    }
    if (_recomputeCard && card.closest('.notes-pane')?.classList.contains('notes-view-grid')) _recomputeCard(card);
  }

  function dispose() {
    _masonryObserver?.disconnect();
    _masonryObserver = null;
    _recomputeCard = null;
  }
  return { dispose, _captureCardPositions, _animateReflow, _applyMasonry, _layoutCard };
}
