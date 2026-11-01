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

  function _animateReflow(prevPositions) {
    if (!prevPositions || !prevPositions.size) return;
    const body = document.querySelector('#notes-pane .notes-pane-body');
    if (!body || !body.isConnected) return;
    body.querySelectorAll('.note-card').forEach(card => {
      const id = card.dataset.noteId;
      const prev = prevPositions.get(id);
      if (!prev) return;
      const next = card.getBoundingClientRect();
      const dx = prev.left - next.left;
      const dy = prev.top - next.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      // Invert: jump back to old position
      card.style.transition = 'none';
      card.style.transform = `translate(${dx}px, ${dy}px)`;
      // Play: animate to 0
      requestAnimationFrame(() => {
        card.style.transition = 'transform 0.25s cubic-bezier(0.34, 1.2, 0.64, 1)';
        card.style.transform = '';
        card.addEventListener('transitionend', () => {
          card.style.transition = '';
        }, { once: true });
      });
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
    if (!isGrid) {
      // Clear any leftover inline spans so list view lays out normally.
      body.querySelectorAll('.note-card, .notes-label-toggle, .notes-labels-bar, .notes-quick-add, .note-form, .doclib-chip-scroll-frame:has(> .notes-labels-bar)').forEach(c => { c.style.gridRowEnd = ''; });
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
      if (quickAdd) {
        const shave = isMobileGrid ? 4 : 0;
        quickAdd.style.gridRowEnd = `span ${Math.max(1, spanForHeight(quickAdd.scrollHeight + 10) - shave)}`;
      }
      body.querySelectorAll('.note-form').forEach(form => {
        form.style.gridColumn = '1 / -1';
        const isDrawForm = !!form.querySelector('.note-form-type-seg.is-draw');
        const minSpan = isMobileGrid ? (isDrawForm ? 104 : 64) : 1;
        const renderedHeight = form.getBoundingClientRect?.().height || 0;
        const drawReserve = isDrawForm && isMobileGrid ? 12 : 12;
        const measuredHeight = Math.max(form.scrollHeight, renderedHeight) + drawReserve;
        form.style.gridRowEnd = `span ${Math.max(minSpan, spanForHeight(measuredHeight))}`;
      });
    };
    const recompute = (card) => {
      // scrollHeight returns the natural content height : card.getBoundingClientRect()
      // would return the grid cell height (collapsed to 4px until the span is set,
      // which is the value we're trying to compute).
      const h = card.scrollHeight + (isMobileGrid ? 6 : 8);
      if (h <= 0) return;
      card.style.gridRowEnd = `span ${spanForHeight(h)}`;
    };
    recomputeFullRows();
    body.querySelectorAll('.note-card').forEach(recompute);
    // Watch masonry participants : content can grow (image load, todo edits,
    // quick-add/form expansion), and stale spans are what cause visual merging.
    if ('ResizeObserver' in window) {
      _masonryObserver = new ResizeObserver(entries => {
        let fullRowsChanged = false;
        for (const e of entries) {
          if (e.target.classList.contains('note-card')) recompute(e.target);
          else fullRowsChanged = true;
        }
        if (fullRowsChanged) recomputeFullRows();
      });
      body.querySelectorAll('.note-card').forEach(c => _masonryObserver.observe(c));
    body.querySelectorAll('.notes-label-toggle, .notes-quick-add, .note-form').forEach(c => _masonryObserver.observe(c));
    const labelsBar = body.querySelector('.notes-labels-bar');
    const labelsLayout = labelsBar?.parentElement?.classList.contains('doclib-chip-scroll-frame')
      ? labelsBar.parentElement
      : labelsBar;
    if (labelsLayout) _masonryObserver.observe(labelsLayout);
    }
  }

  let _masonryObserver = null;

  function dispose() {
    _masonryObserver?.disconnect();
    _masonryObserver = null;
  }
  return { dispose, _captureCardPositions, _animateReflow, _applyMasonry };
}
