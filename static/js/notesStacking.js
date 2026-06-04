export const NOTES_PANE_MIN_Z = 1000;


function _computedStyle(doc, el) {
  const view = doc?.defaultView || globalThis;
  if (view && typeof view.getComputedStyle === 'function') {
    return view.getComputedStyle(el);
  }
  if (typeof globalThis.getComputedStyle === 'function') {
    return globalThis.getComputedStyle(el);
  }
  return el?.style || {};
}


function _isHiddenWindow(doc, el) {
  if (!el) return true;
  if (el.classList?.contains('hidden') || el.classList?.contains('modal-minimized')) return true;
  const style = _computedStyle(doc, el);
  return style?.display === 'none' || style?.visibility === 'hidden';
}


export function nextNotesPaneZIndex(doc = document) {
  let top = 0;
  const nodes = doc?.querySelectorAll?.('.modal, #notes-pane-backdrop') || [];
  for (const node of nodes) {
    if (_isHiddenWindow(doc, node)) continue;
    const style = _computedStyle(doc, node);
    const z = parseInt(style?.zIndex ?? node?.style?.zIndex, 10);
    if (Number.isFinite(z)) top = Math.max(top, z);
  }
  return Math.max(NOTES_PANE_MIN_Z, top + 1);
}
