export const TOOL_WINDOW_SELECTOR = 'body > .modal, body > .research-overlay, body > .notes-pane-backdrop';

export function topToolWindowZ(options = {}) {
  const {
    exclude = null,
    root = globalThis.document,
    getStyle = globalThis.getComputedStyle,
    floor = 250,
  } = options;
  let top = floor;
  if (!root || typeof root.querySelectorAll !== 'function' || typeof getStyle !== 'function') return top;
  root.querySelectorAll(TOOL_WINDOW_SELECTOR).forEach(el => {
    if (!el || el === exclude) return;
    if (el.classList?.contains('hidden') || el.classList?.contains('modal-minimized')) return;
    const cs = getStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const z = parseInt(cs.zIndex, 10);
    if (Number.isFinite(z)) top = Math.max(top, z);
  });
  return top;
}

export function nextToolWindowZ(options = {}) {
  const { current = null } = options;
  const top = topToolWindowZ(options);
  const currentZ = parseInt(current, 10);
  if (Number.isFinite(currentZ) && currentZ > top) return currentZ;
  return top + 1;
}
