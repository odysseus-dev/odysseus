import { LAYER_STYLES } from './layer-styles.js';

const items = [
  ['bevel-emboss', 'Bevel & Emboss'], ['stroke', 'Stroke'],
  ['inner-shadow', 'Inner Shadow'], ['inner-glow', 'Inner Glow'], ['satin', 'Satin'],
  ['color-overlay', 'Color Overlay'], ['gradient-overlay', 'Gradient Overlay'],
  ['pattern-overlay', 'Pattern Overlay'], ['outer-glow', 'Outer Glow'], ['drop-shadow', 'Drop Shadow'],
];
let menu = null, activeAnchor = null;
export function openLayerStyleMenu(anchor, onSelect) {
  const wasOpen = activeAnchor === anchor;
  menu?.remove(); menu = null;
  activeAnchor?.setAttribute('aria-expanded', 'false'); activeAnchor = null;
  if (wasOpen) return;
  const popup = document.createElement('div');
  popup.className = 'dropdown session-dropdown-menu ge-layer-style-menu';
  popup.setAttribute('popover', 'auto'); popup.setAttribute('role', 'menu');
  popup.setAttribute('aria-label', 'Layer styles');
  for (const [type, label] of items) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'dropdown-item-compact';
    button.setAttribute('role', 'menuitem'); button.textContent = LAYER_STYLES[type]?.label || label;
    button.addEventListener('click', event => {
      event.stopPropagation(); popup.hidePopover(); onSelect(type);
    });
    popup.append(button);
  }
  popup.addEventListener('toggle', () => {
    if (!popup.matches(':popover-open')) {
      if (menu === popup) { menu = null; activeAnchor = null; }
      anchor.setAttribute('aria-expanded', 'false'); popup.remove();
    }
  });
  document.body.append(popup); menu = popup; activeAnchor = anchor;
  popup.showPopover(); anchor.setAttribute('aria-expanded', 'true');
  const rect = anchor.getBoundingClientRect();
  popup.style.maxHeight = `${Math.max(40, Math.max(rect.top - 10, innerHeight - rect.bottom - 10))}px`;
  const bounds = popup.getBoundingClientRect();
  popup.style.left = `${Math.max(6, Math.min(innerWidth - bounds.width - 6, rect.right - bounds.width))}px`;
  const top = rect.bottom + bounds.height + 10 <= innerHeight ? rect.bottom + 4 : rect.top - bounds.height - 4;
  popup.style.top = `${Math.max(6, Math.min(innerHeight - bounds.height - 6, top))}px`;
  popup.querySelector('button')?.focus();
}
