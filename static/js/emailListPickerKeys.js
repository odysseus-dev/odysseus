/**
 * Email command palette / move-picker keyboard routing.
 * Mirrors search-chat.js: bubble keydown on input, .search-result-item + .selected.
 */

function _listEl(overlay) {
  return overlay.querySelector('.search-results');
}

function _inputEl(overlay) {
  return overlay.querySelector('.search-palette-input');
}

function _rows(overlay) {
  const list = _listEl(overlay);
  return list ? [...list.querySelectorAll('.search-result-item')] : [];
}

function _highlightIdx(overlay) {
  const raw = overlay.dataset.highlightIdx;
  if (raw === undefined || raw === '') return -1;
  return parseInt(raw, 10);
}

function _setHighlightIdx(overlay, idx) {
  overlay.dataset.highlightIdx = String(idx);
}

/** Repaint row highlight — same .selected class as search-chat.js */
export function paintEmailListPicker(overlay) {
  if (!overlay?.isConnected) return;
  const idx = _highlightIdx(overlay);
  const rows = _rows(overlay);
  rows.forEach((row, i) => {
    row.classList.toggle('selected', i === idx);
  });
  if (idx >= 0) rows[idx]?.scrollIntoView({ block: 'nearest' });
}

export function resetEmailListPickerHighlight(overlay) {
  if (!overlay) return;
  _setHighlightIdx(overlay, -1);
  paintEmailListPicker(overlay);
}

function _handlePickerKeydown(e, overlay) {
  if (!overlay?.isConnected || e.defaultPrevented) return false;
  const rows = _rows(overlay);
  let idx = _highlightIdx(overlay);

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (rows.length) {
      idx = idx < 0 ? 0 : Math.min(idx + 1, rows.length - 1);
      _setHighlightIdx(overlay, idx);
      paintEmailListPicker(overlay);
    }
    return true;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (rows.length) {
      idx = Math.max(idx - 1, 0);
      _setHighlightIdx(overlay, idx);
      paintEmailListPicker(overlay);
    }
    return true;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    if (idx >= 0 && rows[idx]) rows[idx].click();
    return true;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    overlay._emailPickerOnEscape?.();
    return true;
  }
  return false;
}

/** @returns {() => void} detach */
export function bindEmailListPickerKeys(overlay, input, { onEscape } = {}) {
  overlay._emailPickerOnEscape = onEscape;
  _setHighlightIdx(overlay, -1);

  const onKey = (e) => { _handlePickerKeydown(e, overlay); };
  (input || _inputEl(overlay))?.addEventListener('keydown', onKey);

  // Capture on document so Esc closes the palette before the global bulk-select
  // handler (keyboard-shortcuts.js) clears email multi-select.
  const onDocEsc = (e) => {
    if (e.key !== 'Escape' || !overlay.isConnected) return;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation?.();
    onEscape?.();
  };
  document.addEventListener('keydown', onDocEsc, true);

  return () => {
    const inp = input || _inputEl(overlay);
    inp?.removeEventListener('keydown', onKey);
    document.removeEventListener('keydown', onDocEsc, true);
    delete overlay._emailPickerOnEscape;
    delete overlay.dataset.highlightIdx;
  };
}

export function readEmailListPickerDebug() {
  const overlay = document.getElementById('email-cmd-palette') || document.getElementById('email-move-picker');
  if (!overlay) {
    return { overlayId: null, apiBound: false, listConnected: false, rowCount: 0, highlightIdx: null };
  }
  const list = _listEl(overlay);
  return {
    overlayId: overlay.id,
    apiBound: typeof overlay._emailPickerOnEscape === 'function',
    listConnected: !!list?.isConnected,
    rowCount: list?.querySelectorAll('.search-result-item').length ?? 0,
    highlightIdx: overlay.dataset.highlightIdx ?? null,
  };
}
