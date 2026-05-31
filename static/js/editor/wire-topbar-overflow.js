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

export function wireTopbarOverflow({ container }) {
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
    ...container.querySelectorAll('.ge-topbar span[style*="font-size:0.55rem"]'),
  ].filter(Boolean);

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
  }
  // Initial pass after layout settles.
  requestAnimationFrame(syncOverflow);
}
