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
 *   renderLayerPanel:      () => void,
 *   promptCanvasSize:      (opts: object) => Promise<{w, h} | null>,
 *   doFillSelection:       () => void,
 *   rotateAllLayers:       (deg: number) => void,
 *   flipAllLayers:         (axis: 'h' | 'v') => void,
 *   applyGaussianBlur:     () => void,
 *   applyZoomBlur:         () => void,
 *   addRetainedGaussianBlur: () => void,
 *   addRetainedEffect:     (type: string) => void,
 *   uiModule:              object,
 * }} deps
 *
 * @returns {{
 *   applyCanvasResize:   (newW: number, newH: number) => void,
 *   applyImageResize:    (newW: number, newH: number) => void,
 *   canvasSizePrompt:    () => Promise<void>,
 *   imageSizePrompt:     () => Promise<void>,
 * }}
 */
import { state } from './state.js';
import { resizeCanvasDocument, resizeImageDocument } from './document-geometry.js';

export function wireTopbarMenus({
  closeOtherTopbarMenus, registerDocClickAway,
  saveState, composite, fitZoom, renderLayerPanel,
  promptCanvasSize, doFillSelection,
  rotateAllLayers, flipAllLayers,
  applyGaussianBlur, applyZoomBlur, addRetainedGaussianBlur, addRetainedEffect,
  selectAll, deselectSelection, reselectSelection, invertSelection,
  transformSelection, refineSelection,
  saveNamedSelection, loadNamedSelection, deleteNamedSelection,
  uiModule,
}) {
  function finishGeometryChange(newW, newH, message) {
    const sizeLabel = document.getElementById('ge-canvas-size');
    if (sizeLabel) sizeLabel.textContent = `${newW}×${newH}`;
    fitZoom();
    composite();
    renderLayerPanel?.();
    uiModule.showToast(message);
  }

  // Canvas Size changes document bounds without resampling layer pixels.
  function applyCanvasResize(newW, newH, options = {}) {
    if (!newW || !newH || newW < 1 || newH < 1) {
      uiModule.showToast('Invalid size');
      return;
    }
    saveState('Resize canvas');
    const result = resizeCanvasDocument(state, newW, newH, options);
    finishGeometryChange(result.width, result.height, `Canvas resized to ${result.width}×${result.height}`);
  }

  function applyImageResize(newW, newH, options = {}) {
    if (!newW || !newH || newW < 1 || newH < 1) {
      uiModule.showToast('Invalid size');
      return;
    }
    saveState('Resize image');
    const result = resizeImageDocument(state, newW, newH, {
      smoothingQuality: options.interpolation || 'high',
    });
    finishGeometryChange(result.width, result.height, `Image resized to ${result.width}×${result.height}`);
  }

  async function canvasSizePrompt() {
    const result = await promptCanvasSize({
      title: 'Canvas size',
      okLabel: 'Apply',
      initialW: state.imgWidth,
      initialH: state.imgHeight,
      showUnits: true,
      showInterpolation: true,
      keepProportions: true,
      showAnchor: true,
    });
    if (!result) return;
    applyCanvasResize(result.w, result.h, result);
  }

  async function imageSizePrompt() {
    const result = await promptCanvasSize({
      title: 'Image size',
      okLabel: 'Resample',
      initialW: state.imgWidth,
      initialH: state.imgHeight,
      showUnits: true,
      showInterpolation: true,
      keepProportions: true,
      showPresets: false,
    });
    if (!result) return;
    applyImageResize(result.w, result.h, result);
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
      });
      menu.addEventListener('click', (e) => {
        const item = e.target.closest('[data-image-action]');
        if (!item || item.disabled) return;
        menu.hidden = true;
        const action = item.dataset.imageAction;
        if (action === 'canvas-size') canvasSizePrompt();
        else if (action === 'image-size') imageSizePrompt();
        else if (action === 'selection') document.getElementById('ge-edge-menu-btn')?.click();
        else if (action === 'fill') doFillSelection();
        else if (action === 'rotate-90') rotateAllLayers(90);
        else if (action === 'rotate-180') rotateAllLayers(180);
        else if (action === 'flip-h') flipAllLayers('h');
        else if (action === 'flip-v') flipAllLayers('v');
      });
      registerDocClickAway((e) => {
        if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) menu.hidden = true;
      });
    }
  }

  // ── Select menu ──
  const renderSelectionMenu = () => {
    const hasSelection = !!state.wandMask || (state.lassoPoints?.length >= 3 && !state.lassoActive);
    const menu = document.getElementById('ge-selection-menu');
    if (!menu) return;
    menu.querySelector('[data-selection-action="deselect"]')?.toggleAttribute('disabled', !hasSelection);
    menu.querySelector('[data-selection-action="invert"]')?.toggleAttribute('disabled', !hasSelection);
    menu.querySelector('[data-selection-action="transform"]')?.toggleAttribute('disabled', !hasSelection);
    menu.querySelector('[data-selection-action="refine"]')?.toggleAttribute('disabled', !hasSelection);
    menu.querySelector('[data-selection-action="save"]')?.toggleAttribute('disabled', !hasSelection);
    menu.querySelector('[data-selection-action="reselect"]')?.toggleAttribute('disabled', !state.lastSelection?.canvas);
    const list = document.getElementById('ge-saved-selection-list');
    if (!list) return;
    list.innerHTML = '';
    for (const saved of state.savedSelections || []) {
      const row = document.createElement('div');
      row.className = 'ge-saved-selection-row';
      const load = document.createElement('button');
      load.type = 'button';
      load.className = 'ge-saved-selection-load';
      load.dataset.selectionAction = 'load';
      load.dataset.selectionId = saved.id;
      load.textContent = saved.name;
      load.title = `Load ${saved.name}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'ge-saved-selection-delete';
      remove.dataset.selectionAction = 'delete';
      remove.dataset.selectionId = saved.id;
      remove.textContent = '×';
      remove.title = `Delete ${saved.name}`;
      remove.setAttribute('aria-label', `Delete ${saved.name}`);
      row.append(load, remove);
      list.appendChild(row);
    }
    if (!list.children.length) {
      const empty = document.createElement('div');
      empty.className = 'ge-saved-selection-empty';
      empty.textContent = 'None saved';
      list.appendChild(empty);
    }
  };

  {
    const btn = document.getElementById('ge-selection-menu-btn');
    const menu = document.getElementById('ge-selection-menu');
    const nameInput = document.getElementById('ge-selection-name');
    if (btn && menu) {
      btn.addEventListener('click', event => {
        event.stopPropagation();
        const willOpen = menu.hidden;
        if (willOpen) {
          closeOtherTopbarMenus('ge-selection-menu');
          renderSelectionMenu();
        }
        menu.hidden = !menu.hidden;
      });
      menu.addEventListener('click', event => {
        const item = event.target.closest('[data-selection-action]');
        if (!item || item.disabled) return;
        const action = item.dataset.selectionAction;
        let handled = false;
        if (action === 'all') handled = selectAll?.();
        else if (action === 'deselect') handled = deselectSelection?.();
        else if (action === 'reselect') handled = reselectSelection?.();
        else if (action === 'invert') handled = invertSelection?.();
        else if (action === 'transform') handled = transformSelection?.();
        else if (action === 'refine') handled = refineSelection?.();
        else if (action === 'save') {
          handled = saveNamedSelection?.(nameInput?.value);
          if (handled && nameInput) nameInput.value = '';
        } else if (action === 'load') handled = loadNamedSelection?.(item.dataset.selectionId);
        else if (action === 'delete') handled = deleteNamedSelection?.(item.dataset.selectionId);
        if (handled) renderSelectionMenu();
        if (handled && !['save', 'delete'].includes(action)) menu.hidden = true;
      });
      nameInput?.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        if (saveNamedSelection?.(nameInput.value)) {
          nameInput.value = '';
          renderSelectionMenu();
        }
      });
      registerDocClickAway(event => {
        if (!menu.hidden && !menu.contains(event.target) && event.target !== btn) menu.hidden = true;
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
      });
      menu.addEventListener('click', (e) => {
        const item = e.target.closest('[data-filter-action]');
        if (!item) return;
        menu.hidden = true;
        const action = item.dataset.filterAction;
        if (action === 'effect-blur-gaussian') addRetainedGaussianBlur?.();
        else if (action === 'effect-sharpen') addRetainedEffect?.('sharpen');
        else if (action === 'effect-color-overlay') addRetainedEffect?.('color-overlay');
        else if (action === 'effect-drop-shadow') addRetainedEffect?.('drop-shadow');
        else if (action === 'effect-stroke') addRetainedEffect?.('stroke');
        else if (action === 'effect-preset-soft-blur') addRetainedGaussianBlur?.('soft-blur');
        else if (action === 'effect-preset-crisp-detail') addRetainedEffect?.('sharpen', 'crisp-detail');
        else if (action === 'effect-preset-soft-shadow') addRetainedEffect?.('drop-shadow', 'soft-shadow');
        else if (action === 'effect-preset-white-outline') addRetainedEffect?.('stroke', 'white-outline');
        else if (action === 'blur-gaussian') applyGaussianBlur();
        else if (action === 'blur-zoom') applyZoomBlur();
      });
      registerDocClickAway((e) => {
        if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) menu.hidden = true;
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
      });
      menu.querySelectorAll('[data-resize-w]').forEach(item => {
        item.addEventListener('click', () => {
          menu.hidden = true;
          applyCanvasResize(parseInt(item.dataset.resizeW, 10), parseInt(item.dataset.resizeH, 10));
        });
      });
      menu.querySelector('[data-resize-custom]')?.addEventListener('click', () => {
        menu.hidden = true;
        canvasSizePrompt();
      });
      registerDocClickAway((e) => {
        if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) menu.hidden = true;
      });
    }
  }

  return {
    applyCanvasResize,
    applyImageResize,
    canvasSizePrompt,
    imageSizePrompt,
    renderSelectionMenu,
    // Compatibility alias consumed by the existing Ctrl+Shift+T shortcut.
    resizeCustomPrompt: canvasSizePrompt,
  };
}
