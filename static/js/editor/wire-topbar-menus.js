/**
 * Topbar dropdown menus — Image, Filter, and Resize.
 *
 *   Image menu (#ge-image-menu-btn → #ge-image-menu):
 *     resize, selection (edge feather/delete), fill, rotate 90/180,
 *     flip horizontal/vertical.
 *
 *   Filter menu (#ge-filter-menu-btn → #ge-filter-menu):
 *     Blur sub-menu — Gaussian, Zoom.
 *
 *   Resize menu (#ge-resize-menu-btn → #ge-resize-menu):
 *     preset W×H items (data-resize-w/-h) apply immediately;
 *     [data-resize-custom] opens a themed prompt for arbitrary sizes.
 *
 * Returns the resize helpers so the keyboard-shortcuts module can
 * call them too (Ctrl+Shift+T opens the custom prompt).
 *
 * @param {{
 *   closeOtherTopbarMenus: (keepId: string) => void,
 *   registerDocClickAway:  (handler: (e: Event) => void) => void,
 *   saveState:             (label?: string) => void,
 *   composite:             () => void,
 *   fitZoom:               () => void,
 *   promptCanvasSize:      (opts: object) => Promise<{w, h} | null>,
 *   doFillSelection:       () => void,
 *   rotateAllLayers:       (deg: number) => void,
 *   flipAllLayers:         (axis: 'h' | 'v') => void,
 *   applyGaussianBlur:     () => void,
 *   applyZoomBlur:         () => void,
 *   uiModule:              object,
 * }} deps
 *
 * @returns {{
 *   applyResize:         (newW: number, newH: number) => void,
 *   resizeCustomPrompt:  () => Promise<void>,
 * }}
 */
import { state } from './state.js';

function isTouchLandscape() {
  try {
    return window.matchMedia('(orientation: landscape) and (hover: none)').matches ||
      window.matchMedia('(orientation: landscape) and (pointer: coarse)').matches;
  } catch {
    return false;
  }
}

function isTouchLandscapeGalleryEditor() {
  const modal = state.container?.closest?.('#gallery-modal');
  return !!modal && isTouchLandscape();
}

function clearLandscapeMenuPosition(menu) {
  if (!menu || menu.dataset.geLandscapePositioned !== '1') return;
  delete menu.dataset.geLandscapePositioned;
  for (const prop of ['position', 'top', 'right', 'bottom', 'left', 'maxWidth', 'maxHeight', 'overflowY', 'zIndex']) {
    menu.style[prop] = '';
  }
}

function editorMenuBounds(pad = 8) {
  const viewport = {
    left: pad,
    top: pad,
    right: Math.max(pad, (window.innerWidth || 0) - pad),
    bottom: Math.max(pad, (window.innerHeight || 0) - pad),
  };
  const surface = state.container?.closest?.('.gallery-modal-content, .modal-content')
    || state.container?.closest?.('.gallery-editor')
    || state.container;
  const rect = surface?.getBoundingClientRect?.();
  if (!rect || rect.width < 120 || rect.height < 120) return viewport;
  const bounds = {
    left: Math.max(viewport.left, rect.left + pad),
    top: Math.max(viewport.top, rect.top + pad),
    right: Math.min(viewport.right, rect.right - pad),
    bottom: Math.min(viewport.bottom, rect.bottom - pad),
  };
  if (bounds.right - bounds.left < 140 || bounds.bottom - bounds.top < 120) return viewport;
  return bounds;
}

function positionLandscapeMenu(btn, menu, minWidth = 180) {
  if (!btn || !menu) return;
  if (!isTouchLandscapeGalleryEditor()) {
    clearLandscapeMenuPosition(menu);
    return;
  }
  const rect = btn.getBoundingClientRect();
  const bounds = editorMenuBounds(8);
  const width = Math.min(Math.max(minWidth, rect.width), Math.max(140, bounds.right - bounds.left));
  const left = Math.max(bounds.left, Math.min(rect.left, Math.max(bounds.left, bounds.right - width)));
  const top = Math.max(bounds.top, rect.bottom + 4);
  const maxHeight = Math.max(96, Math.min(420, bounds.bottom - top));
  menu.dataset.geLandscapePositioned = '1';
  menu.style.position = 'fixed';
  menu.style.top = `${Math.round(top)}px`;
  menu.style.left = `${Math.round(left)}px`;
  menu.style.right = 'auto';
  menu.style.bottom = 'auto';
  menu.style.maxWidth = `${Math.round(bounds.right - left)}px`;
  menu.style.maxHeight = `${Math.round(maxHeight)}px`;
  menu.style.overflowY = 'auto';
  menu.style.zIndex = '10020';
}

export function wireTopbarMenus({
  closeOtherTopbarMenus, registerDocClickAway,
  saveState, composite, fitZoom,
  promptCanvasSize, doFillSelection,
  rotateAllLayers, flipAllLayers,
  applyGaussianBlur, applyZoomBlur,
  uiModule,
}) {
  // ── Resize canvas ──
  // Extracted so both the popup presets and the Ctrl+Shift+T shortcut
  // can call it.
  function applyResize(newW, newH) {
    if (!newW || !newH || newW < 1 || newH < 1) {
      uiModule.showToast('Invalid size');
      return;
    }
    saveState('Resize canvas');
    // Only resize the main canvas — layers keep their original size.
    // Content outside the new bounds is clipped during composite, not
    // destroyed.
    if (state.maskCanvas) {
      const tmpMask = document.createElement('canvas');
      tmpMask.width = state.maskCanvas.width;
      tmpMask.height = state.maskCanvas.height;
      tmpMask.getContext('2d').drawImage(state.maskCanvas, 0, 0);
      state.maskCanvas.width = newW;
      state.maskCanvas.height = newH;
      state.maskCtx.drawImage(tmpMask, 0, 0);
    }
    state.imgWidth = newW;
    state.imgHeight = newH;
    state.mainCanvas.width = newW;
    state.mainCanvas.height = newH;
    const sizeLabel = document.getElementById('ge-canvas-size');
    if (sizeLabel) sizeLabel.textContent = `${newW}×${newH}`;
    fitZoom();
    composite();
    uiModule.showToast(`Canvas resized to ${newW}×${newH}`);
  }

  async function resizeCustomPrompt() {
    const result = await promptCanvasSize({
      title: 'Canvas size',
      okLabel: 'Apply',
      initialW: state.imgWidth,
      initialH: state.imgHeight,
    });
    if (!result) return;
    applyResize(result.w, result.h);
  }

  // ── Image menu ──
  {
    const btn = document.getElementById('ge-image-menu-btn');
    const menu = document.getElementById('ge-image-menu');
    if (btn && menu) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = menu.hidden;
        if (willOpen) closeOtherTopbarMenus('ge-image-menu');
        menu.hidden = !menu.hidden;
        if (!menu.hidden) positionLandscapeMenu(btn, menu, 180);
        else clearLandscapeMenuPosition(menu);
      });
      menu.addEventListener('click', (e) => {
        const item = e.target.closest('[data-image-action]');
        if (!item || item.disabled) return;
        menu.hidden = true;
        clearLandscapeMenuPosition(menu);
        const action = item.dataset.imageAction;
        if (action === 'resize') resizeCustomPrompt();
        else if (action === 'selection') document.getElementById('ge-edge-menu-btn')?.click();
        else if (action === 'fill') doFillSelection();
        else if (action === 'rotate-90') rotateAllLayers(90);
        else if (action === 'rotate-180') rotateAllLayers(180);
        else if (action === 'flip-h') flipAllLayers('h');
        else if (action === 'flip-v') flipAllLayers('v');
      });
      registerDocClickAway((e) => {
        if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) {
          menu.hidden = true;
          clearLandscapeMenuPosition(menu);
        }
      });
    }
  }

  // ── Filter menu (Blur sub-menu — Gaussian / Zoom) ──
  {
    const btn = document.getElementById('ge-filter-menu-btn');
    const menu = document.getElementById('ge-filter-menu');
    if (btn && menu) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = menu.hidden;
        if (willOpen) closeOtherTopbarMenus('ge-filter-menu');
        menu.hidden = !menu.hidden;
        if (!menu.hidden) positionLandscapeMenu(btn, menu, 180);
        else clearLandscapeMenuPosition(menu);
      });
      menu.addEventListener('click', (e) => {
        const item = e.target.closest('[data-filter-action]');
        if (!item) return;
        menu.hidden = true;
        clearLandscapeMenuPosition(menu);
        const action = item.dataset.filterAction;
        if (action === 'blur-gaussian') applyGaussianBlur();
        else if (action === 'blur-zoom') applyZoomBlur();
      });
      registerDocClickAway((e) => {
        if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) {
          menu.hidden = true;
          clearLandscapeMenuPosition(menu);
        }
      });
    }
  }

  // ── Resize popup (preset items + Custom… → resizeCustomPrompt) ──
  {
    const btn = document.getElementById('ge-resize-menu-btn');
    const menu = document.getElementById('ge-resize-menu');
    if (btn && menu) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = menu.hidden;
        if (willOpen) closeOtherTopbarMenus('ge-resize-menu');
        menu.hidden = !menu.hidden;
        if (!menu.hidden) positionLandscapeMenu(btn, menu, 200);
        else clearLandscapeMenuPosition(menu);
      });
      menu.querySelectorAll('[data-resize-w]').forEach(item => {
        item.addEventListener('click', () => {
          menu.hidden = true;
          clearLandscapeMenuPosition(menu);
          applyResize(parseInt(item.dataset.resizeW, 10), parseInt(item.dataset.resizeH, 10));
        });
      });
      menu.querySelector('[data-resize-custom]')?.addEventListener('click', () => {
        menu.hidden = true;
        clearLandscapeMenuPosition(menu);
        resizeCustomPrompt();
      });
      registerDocClickAway((e) => {
        if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) {
          menu.hidden = true;
          clearLandscapeMenuPosition(menu);
        }
      });
    }
  }

  return { applyResize, resizeCustomPrompt };
}
