/**
 * Topbar overflow handler — keeps lightweight labels updated and hides
 * only low-priority AI model controls when the editor window gets narrow.
 *
 * Plus the small canvas-size display label updater (since it sits in
 * the topbar too).
 *
 * Import and Canvas stay as real topbar buttons; there is intentionally
 * no "More" overflow menu here.
 *
 * @param {{
 *   container:            HTMLElement,
 *   registerDocClickAway: (handler: (e: Event) => void) => void,
 * }} deps
 */
import { state } from './state.js';

let disposeTopbar;

export function wireTopbarOverflow({ container }) {
  disposeTopbar?.();
  const cleanups = [];
  disposeTopbar = () => cleanups.forEach(cleanup => cleanup());
  // Canvas-size badge updater (kept simple — it lives in the topbar).
  const sizeLabel = document.getElementById('ge-canvas-size');
  function updateSizeLabel() {
    if (sizeLabel) sizeLabel.textContent = `${state.imgWidth}×${state.imgHeight}`;
  }
  updateSizeLabel();

  const topbar = container.querySelector('.ge-topbar');
  // The Gen control + its "Gen" label span — collapse as a group when
  // narrow. The Inpaint model selector moved into the side panel.
  const aiGroup = [
    container.querySelector('#ge-ai-model'),
    ...container.querySelectorAll('.ge-topbar span[style*="font-size:9px"]'),
  ].filter(Boolean);

  // Native popovers keep the existing DOM/event ownership while escaping
  // the toolbar's horizontal scroll clip and the editor's stacking context.
  topbar?.querySelectorAll('.dropdown[hidden]').forEach(menu => {
    if (!menu.showPopover) return;
    const anchor = menu.parentElement.querySelector('button');
    if (!anchor) return;
    menu.setAttribute('popover', 'manual');
    const position = () => {
      const rect = anchor.getBoundingClientRect();
      menu.style.top = `${rect.bottom + 4}px`;
      menu.style.right = `${Math.max(8, window.innerWidth - rect.right)}px`;
      menu.style.left = 'auto';
    };
    const sync = () => {
      if (menu.hidden) {
        if (menu.matches(':popover-open')) menu.hidePopover();
      } else if (menu.isConnected) {
        position();
        if (!menu.matches(':popover-open')) menu.showPopover();
      }
    };
    const observer = new MutationObserver(sync);
    observer.observe(menu, { attributes: true, attributeFilter: ['hidden'] });
    topbar.addEventListener('scroll', position, { passive: true });
    window.addEventListener('resize', position);
    cleanups.push(() => {
      observer.disconnect();
      topbar.removeEventListener('scroll', position);
      window.removeEventListener('resize', position);
      if (menu.matches(':popover-open')) menu.hidePopover();
    });
  });

  function syncOverflow() {
    if (!topbar) return;
    aiGroup.forEach(el => { el.style.display = ''; });
    if (topbar.scrollWidth > topbar.clientWidth) {
      // Hide AI group first — bulky and least essential at narrow widths.
      aiGroup.forEach(el => { el.style.display = 'none'; });
    }
  }

  if (topbar && window.ResizeObserver) {
    const ro = new ResizeObserver(() => syncOverflow());
    ro.observe(topbar);
    cleanups.push(() => ro.disconnect());
  }
  // Initial pass after layout settles.
  requestAnimationFrame(syncOverflow);
}
