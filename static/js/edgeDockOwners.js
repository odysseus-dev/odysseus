import { TOOL_WINDOW_SELECTOR } from './toolWindowZOrder.js';

const SIDE_OWNER_SELECTORS = Object.freeze({
  left: '.modal-left-docked, .email-snap-left',
  right: '.modal-right-docked',
});
const NOTES_BACKDROP_SELECTOR = 'body > .notes-pane-backdrop';

export function dockOwnerSelectorForSide(side) {
  return SIDE_OWNER_SELECTORS[side] || '';
}

export function isApplicationDockOwner(owner) {
  if (!owner || typeof owner.matches !== 'function') return false;
  if (owner.matches('.notes-pane')) {
    return !!owner.parentElement?.matches?.(NOTES_BACKDROP_SELECTOR);
  }
  return owner.matches(TOOL_WINDOW_SELECTOR);
}

export function isUsableDockOwner(owner, options = {}) {
  const {
    resolveContent = (candidate) => candidate?.querySelector?.('.modal-content') || candidate,
    getStyle = globalThis.getComputedStyle,
  } = options;
  if (!isApplicationDockOwner(owner) || !owner.isConnected) return false;
  if (owner.classList?.contains('hidden') || owner.classList?.contains('modal-minimized')) return false;
  const ownerStyle = typeof getStyle === 'function' ? getStyle(owner) : owner.style;
  if (ownerStyle?.display === 'none' || ownerStyle?.visibility === 'hidden') return false;

  const content = resolveContent(owner);
  if (!content || !content.isConnected) return false;
  if (content.classList?.contains('hidden') || content.classList?.contains('modal-minimized')) return false;
  const contentStyle = typeof getStyle === 'function' ? getStyle(content) : content.style;
  if (contentStyle?.display === 'none' || contentStyle?.visibility === 'hidden') return false;
  const rect = content.getBoundingClientRect?.();
  return !!rect && rect.width > 0 && rect.height > 0;
}

export function dockOwnersForSide(side, options = {}) {
  const {
    root = globalThis.document,
    resolveContent,
    getStyle,
  } = options;
  const selector = dockOwnerSelectorForSide(side);
  if (!selector || !root || typeof root.querySelectorAll !== 'function') return [];
  return Array.from(root.querySelectorAll(selector)).filter((owner) => (
    isUsableDockOwner(owner, { resolveContent, getStyle })
  ));
}
