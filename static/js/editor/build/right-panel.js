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
 *   controlsHTML:     (ctx: {color, brushSize, wandTolerance}) => string,
 *   layerPanelHTML:   () => string,
 * }} build
 *
 * @returns {{
 *   rightPanel:  HTMLDivElement,
 *   controls:    HTMLDivElement,
 *   layerPanel:  HTMLDivElement,
 *   panelResize: HTMLDivElement,
 * }}
 */
import { state } from '../state.js';

function isTouchLandscape() {
  try {
    return window.matchMedia('(orientation: landscape) and (hover: none)').matches ||
      window.matchMedia('(orientation: landscape) and (pointer: coarse)').matches;
  } catch {
    return false;
  }
}

function isCompactEditorViewport() {
  const shell = state.container?.closest?.('.gallery-modal-content, .modal-content');
  const shellWidth = shell?.getBoundingClientRect?.().width || window.innerWidth || 0;
  if (isTouchLandscape()) return false;
  return window.innerWidth <= 700 ||
    shellWidth <= 700;
}

export function buildRightPanel({ controlsHTML, layerPanelHTML }) {
  const rightPanel = document.createElement('div');
  rightPanel.className = 'ge-right-panel';
  const hideSliderBubble = () => {
    try { document.dispatchEvent(new CustomEvent('ge:hide-slider-bubble')); } catch {}
    try { window.__geHideSliderBubble?.(); } catch {}
  };
  function revealLayerPeek() {
    rightPanel.classList.remove('expanded', 'minimized');
  }

  // Controls section.
  const controls = document.createElement('div');
  controls.className = 'ge-controls';
  // Swipe-down to dismiss on mobile. Tap the same tool again to bring
  // the sheet back. Only the top ~40 px (grab handle area) initiates
  // the gesture so taps on inputs/sliders inside the panel still work.
  {
    let sy = 0, dragging = false;
    controls.addEventListener('touchstart', (e) => {
      if (!isCompactEditorViewport()) return;
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
      if (dy > 60) {
        controls.classList.add('dismissed');
        revealLayerPeek();
        hideSliderBubble();
      }
    });
  }
  controls.innerHTML = controlsHTML({
    color: state.color,
    brushSize: state.brushSize,
    wandTolerance: state.wandTolerance,
  });
  rightPanel.appendChild(controls);

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
  rightPanel.appendChild(layerPanel);

  function restoreInpaintPanelToControls() {
    const inpaintPanel = document.getElementById('ge-inpaint-section');
    if (!inpaintPanel || inpaintPanel.parentElement === controls) return;
    const anchor = controls.querySelector('#ge-clone-section')
      || controls.querySelector('#ge-brush-section')
      || null;
    controls.insertBefore(inpaintPanel, anchor);
    inpaintPanel.classList.remove('ge-inpaint-popover', 'ge-inpaint-popover-dragging');
    delete inpaintPanel.dataset.portaled;
    inpaintPanel.style.left = '';
    inpaintPanel.style.top = '';
    inpaintPanel.style.maxHeight = '';
  }

  function clearSheetDragState() {
    controls.style.transition = '';
    controls.style.transform = '';
  }

  function syncControlsPlacement() {
    if (!state.container || !rightPanel.isConnected || !controls.isConnected) return;
    const compact = isCompactEditorViewport();
    const touchLandscape = isTouchLandscape();
    const editorBody = state.container.querySelector('.ge-editor-body');

    if (compact) {
      // Portrait/narrow: the right panel is itself transformed into a
      // layers bottom sheet. position:fixed descendants are trapped by
      // transformed ancestors on mobile WebViews, so controls must live
      // directly under the editor root to scroll as a real bottom sheet.
      if (controls.parentElement !== state.container) {
        state.container.insertBefore(controls, editorBody);
      } else if (editorBody && controls.nextElementSibling !== editorBody) {
        state.container.insertBefore(controls, editorBody);
      }
    } else {
      // Landscape/wide: controls belong back inside the side panel so
      // the panel owns one coherent vertical scroll area.
      if (controls.parentElement !== rightPanel) {
        rightPanel.insertBefore(controls, layerPanel);
      }
      clearSheetDragState();
      controls.classList.remove('dismissed');
      restoreInpaintPanelToControls();
    }

    if (touchLandscape) {
      rightPanel.classList.remove('expanded', 'minimized');
      controls.classList.remove('dismissed', 'ge-inpaint-popover-host');
      clearSheetDragState();
      restoreInpaintPanelToControls();
    }
  }

  let placementRaf = 0;
  let placementCleaned = false;
  function cleanupPlacementSync() {
    if (placementCleaned) return;
    placementCleaned = true;
    if (placementRaf) cancelAnimationFrame(placementRaf);
    window.removeEventListener('resize', scheduleControlsPlacementSync);
    window.removeEventListener('orientationchange', scheduleControlsPlacementSync);
    try { window.visualViewport?.removeEventListener('resize', scheduleControlsPlacementSync); } catch {}
    try { window.visualViewport?.removeEventListener('scroll', scheduleControlsPlacementSync); } catch {}
    try { screen.orientation?.removeEventListener?.('change', scheduleControlsPlacementSync); } catch {}
  }

  function scheduleControlsPlacementSync() {
    if (placementCleaned) return;
    if (!rightPanel.isConnected && !controls.isConnected) {
      cleanupPlacementSync();
      return;
    }
    if (placementRaf) return;
    placementRaf = requestAnimationFrame(() => {
      placementRaf = 0;
      syncControlsPlacement();
    });
  }

  syncControlsPlacement();
  window.addEventListener('resize', scheduleControlsPlacementSync, { passive: true });
  window.addEventListener('orientationchange', scheduleControlsPlacementSync, { passive: true });
  try { window.visualViewport?.addEventListener('resize', scheduleControlsPlacementSync, { passive: true }); } catch {}
  try { window.visualViewport?.addEventListener('scroll', scheduleControlsPlacementSync, { passive: true }); } catch {}
  try { screen.orientation?.addEventListener?.('change', scheduleControlsPlacementSync); } catch {}
  state.editorCleanupHandlers.push(cleanupPlacementSync);

  // Mobile: tap the header grab handle or swipe up/down to toggle
  // the layers sheet between peek and expanded. The peek state
  // always shows the active layer so users never lose access to it.
  {
    const header = layerPanel.querySelector('.ge-layers-header');
    if (header) {
      let sy = 0, sx = 0, dragging = false, didSwipe = false;
      header.addEventListener('touchstart', (e) => {
        if (!isCompactEditorViewport()) return;
        if (e.target.closest('button')) return;
        hideSliderBubble();
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
          const isExpanded = rightPanel.classList.contains('expanded');
          const isMinimized = rightPanel.classList.contains('minimized');
          hideSliderBubble();
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
          // Expanded layers => hide tool controls so layers gets the
          // full screen. Peek/minimized leave controls state as-is
          // (user dismissed them by swiping, only re-tapping a tool
          // brings them back).
          if (rightPanel.classList.contains('expanded')) {
            controls.classList.add('dismissed');
          }
          e.preventDefault();
        }
      });
      header.addEventListener('click', (e) => {
        if (!isCompactEditorViewport()) return;
        if (e.target.closest('button')) return;
        if (didSwipe) { didSwipe = false; return; }
        hideSliderBubble();
        // Click cycles between peek and expanded; minimized comes
        // back to peek (so a tap on the handle always reveals at
        // least the active layer row).
        if (rightPanel.classList.contains('minimized')) {
          rightPanel.classList.remove('minimized');
        } else {
          rightPanel.classList.toggle('expanded');
        }
        // Expanded layers => hide tool controls; peek/minimized leave
        // controls state as-is (only re-tapping a tool brings them back).
        if (rightPanel.classList.contains('expanded')) {
          controls.classList.add('dismissed');
        }
      });
    }
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

  return { rightPanel, controls, layerPanel, panelResize };
}
