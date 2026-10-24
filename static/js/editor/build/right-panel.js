/**
 * Build the right-hand panel (controls + layers) — DOM creation,
 * controls innerHTML population, mobile bottom-sheet swipe behavior,
 * controls-panel re-parenting on mobile, slider value-chip layout
 * normalization, layer-panel header + mobile peek/expand swipe, and
 * the panel-width drag-resize handle.
 *
 * Owns its own event listeners (touch swipe gestures, mouse resize
 * drag). Returns the `rightPanel` element + the `panelResize` handle
 * + the inner `controls` element so the caller can wire any post-
 * mount tweaks. Reads state.container (for mobile re-parenting) and
 * state.color / state.brushSize / state.wandTolerance (initial slider
 * values).
 *
 * @param {{
 *   controlsHTML:     (ctx: {color, brushSize, wandTolerance, bucketTolerance, cropAspectPreset}) => string,
 *   layerPanelHTML:   () => string,
 * }} build
 *
 * @returns {{
 *   rightPanel:  HTMLDivElement,
 *   controls:    HTMLDivElement,
 *   layerPanel:  HTMLDivElement,
 *   panelResize: HTMLDivElement,
 *   dock:        {dock: HTMLDivElement, show: (id: string) => void}|null,
 * }}
 */
import { state } from '../state.js';
import { buildDock } from './dock.js';

export function buildRightPanel({ controlsHTML, layerPanelHTML, extraPanels = [] }) {
  const rightPanel = document.createElement('div');
  rightPanel.className = 'ge-right-panel';
  // Desktop: present Tool Options + Layers as tabs in a dock. Mobile
  // (≤ 700 px) keeps the existing bottom-sheet model, where the two
  // panels are independent sheets, so the dock is bypassed entirely.
  const useDock = window.innerWidth > 700;

  // Controls section.
  const controls = document.createElement('div');
  controls.className = 'ge-controls';
  // Swipe-down to dismiss on mobile. Tap the same tool again to bring
  // the sheet back. Only the top ~40 px (grab handle area) initiates
  // the gesture so taps on inputs/sliders inside the panel still work.
  {
    let sy = 0, dragging = false;
    controls.addEventListener('touchstart', (e) => {
      if (window.innerWidth > 700) return;
      const rect = controls.getBoundingClientRect();
      const t = e.touches[0];
      // Only engage if touch starts in the top grab zone.
      if (t.clientY - rect.top > 40) return;
      sy = t.clientY;
      dragging = true;
      controls.style.transition = 'none';
    }, { passive: true });
    controls.addEventListener('touchmove', (e) => {
      if (!dragging) return;
      const dy = e.touches[0].clientY - sy;
      if (dy > 0) controls.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    controls.addEventListener('touchend', (e) => {
      if (!dragging) return;
      dragging = false;
      const dy = e.changedTouches[0].clientY - sy;
      controls.style.transition = '';
      controls.style.transform = '';
      if (dy > 60) controls.classList.add('dismissed');
    });
  }
  controls.innerHTML = controlsHTML({
    color: state.color,
    brushSize: state.brushSize,
    wandTolerance: state.wandTolerance,
    bucketTolerance: state.bucketTolerance,
    cropAspectPreset: state.cropAspectPreset,
    eyedropperSampleSize: state.eyedropperSampleSize,
  });
  if (!useDock) rightPanel.appendChild(controls);
  // Mobile only (≤ 700 px — matches the .ge-editor-body column-stack
  // breakpoint): the right panel becomes a transformed bottom-sheet,
  // so any position:fixed descendant gets trapped by the transform
  // and rides along with the panel. Re-parent the controls panel to
  // the editor root so it can truly fix to the viewport bottom
  // regardless of the layers-sheet state. On desktop, controls stay
  // docked inside the right panel above the layers list.
  if (window.innerWidth <= 700 && state.container) {
    state.container.appendChild(controls);
    // Start canvas-first on phones. The active tool button brings this
    // sheet back and minimizes Layers, so the two bottom sheets never
    // compete for the same pointer events on initial open.
    controls.classList.add('dismissed');
  }

  // Move every slider-row's value chip out of its <label> and place
  // it AFTER the slider, so the value sits on the right edge of the
  // row instead of being smashed against the slider track on the left.
  controls.querySelectorAll('.ge-eraser-row').forEach(row => {
    const valueSpan = row.querySelector('label > span[id$="-label"]');
    const slider = row.querySelector('input[type="range"]');
    if (valueSpan && slider) {
      valueSpan.classList.add('ge-slider-value');
      slider.after(valueSpan);
    }
  });

  // Layer panel.
  const layerPanel = document.createElement('div');
  layerPanel.className = 'ge-layers';
  layerPanel.innerHTML = layerPanelHTML();
  if (!useDock) rightPanel.appendChild(layerPanel);
  // Mobile: tap the header grab handle or swipe up/down to toggle
  // the layers sheet between peek and expanded. The peek state
  // always shows the active layer so users never lose access to it.
  {
    const header = layerPanel.querySelector('.ge-layers-header');
    if (header) {
      const claimLayerSheet = () => {
        if (window.innerWidth <= 700) controls.classList.add('dismissed');
      };
      let sy = 0, sx = 0, dragging = false, didSwipe = false;
      header.addEventListener('touchstart', (e) => {
        if (window.innerWidth > 700) return;
        if (e.target.closest('button')) return;
        sy = e.touches[0].clientY;
        sx = e.touches[0].clientX;
        dragging = true;
        didSwipe = false;
      }, { passive: true });
      header.addEventListener('touchend', (e) => {
        if (!dragging) return;
        dragging = false;
        const dy = e.changedTouches[0].clientY - sy;
        const dx = Math.abs(e.changedTouches[0].clientX - sx);
        // Real swipe — three states cycle by direction:
        //   minimized → peek → expanded   (swipe up)
        //   expanded → peek → minimized   (swipe down)
        if (Math.abs(dy) > 20 && Math.abs(dy) > dx) {
          didSwipe = true;
          claimLayerSheet();
          const isExpanded = rightPanel.classList.contains('expanded');
          const isMinimized = rightPanel.classList.contains('minimized');
          if (dy < 0) {
            if (isMinimized) {
              rightPanel.classList.remove('minimized');
            } else if (!isExpanded) {
              rightPanel.classList.add('expanded');
            }
          } else {
            if (isExpanded) {
              rightPanel.classList.remove('expanded');
            } else if (!isMinimized) {
              rightPanel.classList.add('minimized');
            }
          }
          e.preventDefault();
        }
      });
      header.addEventListener('click', (e) => {
        if (window.innerWidth > 700) return;
        if (e.target.closest('button')) return;
        if (didSwipe) { didSwipe = false; return; }
        claimLayerSheet();
        // Click cycles between peek and expanded; minimized comes
        // back to peek (so a tap on the handle always reveals at
        // least the active layer row).
        if (rightPanel.classList.contains('minimized')) {
          rightPanel.classList.remove('minimized');
        } else {
          rightPanel.classList.toggle('expanded');
        }
      });
    }
  }

  // Mobile panel access (≤ 700 px). The desktop dock is bypassed on
  // phones, which left the whole studio panel set (Swatches, Gradients,
  // Patterns, Properties, Adjustments, Histogram, Info, Channels, Paths,
  // Character, Navigator, History, Actions) unreachable — there was no way
  // to add an adjustment or pick a colour from the sheet. Mount the same
  // entries in a compact `<select>` overlay reachable from a "Panels"
  // button in the layers header, without touching the peek/expand model.
  let mobilePanels = null;
  if (!useDock && Array.isArray(extraPanels) && extraPanels.length) {
    const mDock = buildDock({
      entries: extraPanels,
      persistKey: 'ge-dock-active',
      defaultId: null,
      compact: true,
    });
    const overlay = document.createElement('div');
    overlay.className = 'ge-mobile-panels';
    overlay.hidden = true;
    const head = document.createElement('div');
    head.className = 'ge-mobile-panels-head';
    const title = document.createElement('span');
    title.className = 'ge-mobile-panels-title';
    title.textContent = 'Panels';
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'ge-mobile-panels-close';
    closeBtn.setAttribute('aria-label', 'Close panels');
    closeBtn.textContent = '✕';
    head.append(title, closeBtn);
    overlay.append(head, mDock.dock);
    rightPanel.appendChild(overlay);
    closeBtn.addEventListener('click', () => { overlay.hidden = true; });

    const hdr = layerPanel.querySelector('.ge-layers-header');
    if (hdr) {
      const panelsBtn = document.createElement('button');
      panelsBtn.type = 'button';
      panelsBtn.className = 'ge-layers-panels-btn';
      panelsBtn.textContent = 'Panels';
      panelsBtn.title = 'Show panels';
      panelsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        overlay.hidden = !overlay.hidden;
      });
      hdr.appendChild(panelsBtn);
    }
    mobilePanels = overlay;
  }

  // Desktop dock: Tool Options + Layers as tabs (PhotoCraft-parity).
  // The panels are only re-parented, so every existing selector and
  // listener keeps working; switching tabs just toggles `hidden`.
  let dock = null;
  if (useDock) {    rightPanel.classList.add('ge-right-panel--dock');
    dock = buildDock({
      entries: [
        ...(Array.isArray(extraPanels) ? extraPanels : []),
      ],
      defaultId: (Array.isArray(extraPanels) && extraPanels[0]?.id) || null,
      persistKey: 'ge-dock-active',
      // Narrow desktop columns get the compact selector fallback; the
      // multi-group stack needs a bit more room to be usable.
      compact: window.innerWidth <= 820,
    });
    rightPanel.appendChild(dock.dock);
    // Layers stays pinned below the dock so the layer list is always
    // visible while editing (Photoshop-style), regardless of the active
    // dock tab. Only the tool options + studio panels live in tabs.
    rightPanel.appendChild(layerPanel);
  }

  // Horizontal drag handle on the LEFT edge of the right panel — drag
  // left to widen, right to narrow. Persists chosen width in
  // localStorage so it survives reopens. (Earlier version was a
  // vertical-drag for height; horizontal feels more natural since
  // cramped LAYER ROWS are about width, not height.)
  const panelResize = document.createElement('div');
  panelResize.className = 'ge-panel-resize';
  panelResize.title = 'Drag to resize panel';
  rightPanel.appendChild(panelResize);
  try {
    const savedW = parseInt(localStorage.getItem('ge-right-panel-width') || '', 10);
    if (savedW && savedW > 160 && savedW < 800) rightPanel.style.flex = `0 0 ${savedW}px`;
  } catch {}
  let panelResizing = false;
  let panelStartX = 0;
  let panelStartW = 0;
  panelResize.addEventListener('mousedown', (e) => {
    panelResizing = true;
    panelStartX = e.clientX;
    panelStartW = rightPanel.getBoundingClientRect().width;
    e.preventDefault();
    document.body.style.cursor = 'ew-resize';
  });
  document.addEventListener('mousemove', (e) => {
    if (!panelResizing) return;
    // Dragging left → wider panel (the panel sits on the right of
    // the editor, so a leftward drag pulls its left edge left).
    const delta = panelStartX - e.clientX;
    const next = Math.max(160, Math.min(window.innerWidth - 200, panelStartW + delta));
    rightPanel.style.flex = `0 0 ${next}px`;
  });
  document.addEventListener('mouseup', () => {
    if (!panelResizing) return;
    panelResizing = false;
    document.body.style.cursor = '';
    try {
      const w = Math.round(rightPanel.getBoundingClientRect().width);
      localStorage.setItem('ge-right-panel-width', String(w));
    } catch {}
  });

  return { rightPanel, controls, layerPanel, panelResize, dock };
}
