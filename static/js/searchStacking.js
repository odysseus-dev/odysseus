export const SEARCH_OVERLAY_MIN_Z = 10050;


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


function _isHiddenStackTarget(doc, el) {
  if (!el) return true;
  if (el.classList?.contains('hidden') || el.classList?.contains('modal-minimized')) return true;
  const style = _computedStyle(doc, el);
  return style?.display === 'none' || style?.visibility === 'hidden';
}


export function nextSearchOverlayZIndex(doc = document) {
  let top = 0;
  const nodes = doc?.querySelectorAll?.('.modal, .doc-editor-pane') || [];
  for (const node of nodes) {
    if (_isHiddenStackTarget(doc, node)) continue;
    const style = _computedStyle(doc, node);
    const z = parseInt(style?.zIndex ?? node?.style?.zIndex, 10);
    if (Number.isFinite(z)) top = Math.max(top, z);
  }
  return Math.max(SEARCH_OVERLAY_MIN_Z, top + 1);
}
