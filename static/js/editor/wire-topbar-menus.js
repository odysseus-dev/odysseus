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
 *   rotateActiveLayer:     (deg: number) => void,
 *   flipActiveLayer:       (axis: 'h' | 'v') => void,
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
import { wireSubmenuToggles } from './submenu-toggles.js';

// Ask for a single number (Photoshop-style modify radius/width). Mirrors the
// canvas context menu's helper so Select > Modify matches the right-click flow.
function promptNumber(message, fallback, run) {
  const raw = window.prompt(message, String(fallback));
  if (raw == null) return;
  const value = Number(raw);
  if (Number.isFinite(value)) run(value);
}

export function wireTopbarMenus({
  closeOtherTopbarMenus, registerDocClickAway,
  saveState, composite, fitZoom, renderLayerPanel,
  promptCanvasSize, doFillSelection,
  rotateAllLayers, rotateAllLayersArbitrary, openPerspectiveCrop, flipAllLayers,
  rotateActiveLayer, flipActiveLayer,
  applyGaussianBlur, applyZoomBlur, applyMotionBlur, applyNoise, applyDespeckle, applyDehaze,
  applyPixelate, applyCrystallize, applyEmboss, applyFindEdges, applyGlowingEdges, applySolarize,
  applyOffset, applyBoxBlur, applySurfaceBlur, applyCustom,
  applyMaximum, applyMinimum, applyMedian, applyPolarCoordinates, applyDifferenceClouds,
  applyLensFlare, applyFibers, applyOilPaint, applyLensCorrection, applyReplaceColor,
  applyDiffuse, applyOceanRipple, applyGlass, applyMosaicTiles, applyWind,
  applyTraceContour, applyHalftonePattern, applyReticulation, applyShear, applyNotePaper,
  applyCharcoal, applyChalkCharcoal, applyGraphicPen, applyPhotocopy, applyWaterPaper,
  applySharpenMore, applyPosterEdges, applyCutout, applyInkOutlines, applyStainedGlass,
  applyGrain, applyTexturizer, applyPatchwork, applyCraquelure, applyCrosshatch,
  applyTwirl, applyPinch, applySpherize, applyRipple, applyClouds,
  applySharpen, applySharpenEdges, applyUnsharpMask,
  addRetainedGaussianBlur, addRetainedEffect,
  selectAll, deselectSelection, reselectSelection, invertSelection,
  transformSelection, refineSelection, colorRange, selectAndMask,
  saveNamedSelection, loadNamedSelection, deleteNamedSelection,
  setColorMode, uiModule,
}) {
  function finishGeometryChange(newW, newH, message) {
    const sizeLabel = document.getElementById('ge-canvas-size');
    if (sizeLabel) sizeLabel.textContent = `${newW}×${newH}`;
    fitZoom();
    composite();
    renderLayerPanel?.();
    uiModule.showToast(message);
  }

  // Photoshop-style nested flyouts are wired by the shared submenu-toggles
  // helper (also used by the Window > Workspace submenu).

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
      showPreview: true,
      showResolution: true,
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
      wireSubmenuToggles(menu);
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
        else if (action === 'mode-rgb') setColorMode?.('rgb');
        else if (action === 'mode-grayscale') setColorMode?.('grayscale');
        else if (action === 'desaturate') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'hue-saturation', params: { saturation: 0 } });
        else if (action === 'auto-tone') window.geEditorControl?.invoke?.('photo.autoTone', {});
        else if (action === 'auto-contrast') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'levels', params: { inBlack: 12, inWhite: 243 } });
        else if (action === 'auto-color') window.geEditorControl?.invoke?.('photo.autoTone', {});
        else if (action === 'equalize') window.geEditorControl?.invoke?.('adjustments.equalize', {});
        else if (action === 'trim') window.geEditorControl?.invoke?.('document.trim', {});
        else if (action === 'replace-color') applyReplaceColor?.();
        else if (action === 'rotate-ccw') rotateAllLayers(270);
        else if (action === 'rotate-arbitrary') promptNumber('Rotation angle (degrees, clockwise; negative for counter-clockwise)', 15, (v) => rotateAllLayersArbitrary?.(v));
      else if (action === 'perspective-crop') openPerspectiveCrop?.();
        else if (action === 'adj-levels') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'levels' });
        else if (action === 'adj-curves') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'curves' });
        else if (action === 'adj-exposure') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'exposure' });
        else if (action === 'adj-develop') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'develop' });
        else if (action === 'adj-vibrance') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'vibrance' });
        else if (action === 'adj-color-balance') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'color-balance' });
        else if (action === 'adj-black-white') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'black-white' });
        else if (action === 'adj-photo-filter') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'photo-filter' });
        else if (action === 'adj-channel-mixer') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'channel-mixer' });
        else if (action === 'adj-invert') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'invert' });
        else if (action === 'adj-posterize') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'posterize' });
        else if (action === 'adj-threshold') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'threshold' });
        else if (action === 'adj-gradient-map') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'gradient-map' });
        else if (action === 'adj-shadows-highlights') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'shadows-highlights' });
        else if (action === 'adj-brightness-contrast') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'brightness-contrast' });
        else if (action === 'adj-hue-saturation') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'hue-saturation' });
        else if (action === 'adj-selective-color') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'selective-color' });
        else if (action === 'adj-levels') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'levels' });
        else if (action === 'adj-curves') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'curves' });
        else if (action === 'adj-exposure') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'exposure' });
        else if (action === 'adj-develop') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'develop' });
        else if (action === 'adj-vibrance') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'vibrance' });
        else if (action === 'adj-color-balance') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'color-balance' });
        else if (action === 'adj-black-white') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'black-white' });
        else if (action === 'adj-photo-filter') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'photo-filter' });
        else if (action === 'adj-channel-mixer') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'channel-mixer' });
        else if (action === 'adj-invert') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'invert' });
        else if (action === 'adj-posterize') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'posterize' });
        else if (action === 'adj-threshold') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'threshold' });
        else if (action === 'adj-gradient-map') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'gradient-map' });
        else if (action === 'adj-shadows-highlights') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'shadows-highlights' });
        else if (action === 'adj-brightness-contrast') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'brightness-contrast' });
        else if (action === 'adj-hue-saturation') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'hue-saturation' });
        else if (action === 'adj-selective-color') window.geEditorControl?.invoke?.('adjustments.apply', { type: 'selective-color' });
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
    for (const id of ['modifyBorder', 'modifySmooth', 'modifyExpand', 'modifyContract', 'modifyFeather']) {
      menu.querySelector(`[data-selection-action="${id}"]`)?.toggleAttribute('disabled', !hasSelection);
    }
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
      wireSubmenuToggles(menu);
      menu.addEventListener('click', event => {
        const item = event.target.closest('[data-selection-action]');
        if (!item || item.disabled) return;
        const action = item.dataset.selectionAction;
        let handled = false;
        if (action === 'all') handled = selectAll?.();
        else if (action === 'deselect') handled = deselectSelection?.();
        else if (action === 'reselect') handled = reselectSelection?.();
        else if (action === 'invert') handled = invertSelection?.();
        else if (action === 'grow') handled = !!window.geEditorControl?.invoke?.('selection.grow', { amount: 1 });
        else if (action === 'modifyFeather') { promptNumber('Feather radius (px)', 8, v => window.geEditorControl?.invoke?.('selection.feather', { radius: v })); handled = true; }
        else if (action === 'modifyExpand') { promptNumber('Expand by (px)', 4, v => window.geEditorControl?.invoke?.('selection.expand', { amount: v })); handled = true; }
        else if (action === 'modifyContract') { promptNumber('Contract by (px)', 4, v => window.geEditorControl?.invoke?.('selection.contract', { amount: v })); handled = true; }
        else if (action === 'modifyBorder') { promptNumber('Border width (px)', 4, v => window.geEditorControl?.invoke?.('selection.border', { width: v })); handled = true; }
        else if (action === 'modifySmooth') { promptNumber('Smooth radius (px)', 4, v => window.geEditorControl?.invoke?.('selection.smooth', { radius: v })); handled = true; }
        else if (action === 'similar') handled = !!window.geEditorControl?.invoke?.('selection.similar', {});
        else if (action === 'subject') { window.geEditorControl?.invoke?.('selection.subject', {}); handled = true; }
        else if (action === 'transform') handled = transformSelection?.();
        else if (action === 'refine') handled = refineSelection?.();
        else if (action === 'colorRange') handled = !!colorRange?.();
        else if (action === 'selectAndMask') handled = !!selectAndMask?.();
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
      wireSubmenuToggles(menu);
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
        else if (action === 'effect-motion-blur') addRetainedEffect?.('motion-blur');
        else if (action === 'effect-zoom-blur') addRetainedEffect?.('zoom-blur');
        else if (action === 'effect-pixelate') addRetainedEffect?.('pixelate');
        else if (action === 'effect-preset-soft-blur') addRetainedGaussianBlur?.('soft-blur');
        else if (action === 'effect-preset-crisp-detail') addRetainedEffect?.('sharpen', 'crisp-detail');
        else if (action === 'effect-preset-soft-shadow') addRetainedEffect?.('drop-shadow', 'soft-shadow');
        else if (action === 'effect-preset-white-outline') addRetainedEffect?.('stroke', 'white-outline');
        else if (action === 'blur-gaussian') applyGaussianBlur?.();
        else if (action === 'dehaze') applyDehaze?.();
        else if (action === 'blur-zoom') applyZoomBlur?.();
        else if (action === 'blur-motion') applyMotionBlur?.();
        else if (action === 'radial-blur') applyZoomBlur?.();
        else if (action === 'blur-box') applyBoxBlur?.();
        else if (action === 'blur-surface') applySurfaceBlur?.();
        else if (action === 'sharpen') applySharpen?.();
        else if (action === 'sharpen-edges') applySharpenEdges?.();
        else if (action === 'unsharp-mask') applyUnsharpMask?.();
        else if (action === 'smart-sharpen') applyUnsharpMask?.();
        else if (action === 'smart-sharpen') applyUnsharpMask?.();
        else if (action === 'noise-add') applyNoise?.();
        else if (action === 'noise-despeckle') applyDespeckle?.();
        else if (action === 'reduce-noise') applyDespeckle?.();
        else if (action === 'dust-scratches') applyDespeckle?.();
        else if (action === 'pixelate-mosaic') applyPixelate?.();
        else if (action === 'pixelate-crystallize') applyCrystallize?.();
        else if (action === 'stylize-emboss') applyEmboss?.();
        else if (action === 'stylize-find-edges') applyFindEdges?.();
        else if (action === 'stylize-glowing-edges') applyGlowingEdges?.();
        else if (action === 'stylize-solarize') applySolarize?.();
        else if (action === 'distort-twirl') applyTwirl?.();
        else if (action === 'distort-pinch') applyPinch?.();
        else if (action === 'distort-spherize') applySpherize?.();
        else if (action === 'distort-ripple') applyRipple?.();
        else if (action === 'render-clouds') applyClouds?.();
        else if (action === 'other-offset') applyOffset?.();
        else if (action === 'other-custom') applyCustom?.();
        else if (action === 'other-maximum') applyMaximum?.();
        else if (action === 'other-minimum') applyMinimum?.();
        else if (action === 'noise-median') applyMedian?.();
        else if (action === 'distort-polar') applyPolarCoordinates?.();
        else if (action === 'render-difference-clouds') applyDifferenceClouds?.();
        else if (action === 'render-lens-flare') applyLensFlare?.();
        else if (action === 'render-fibers') applyFibers?.();
        else if (action === 'stylize-oil-paint') applyOilPaint?.();
        else if (action === 'distort-lens-correction') applyLensCorrection?.();
        else if (action === 'stylize-diffuse') applyDiffuse?.();
        else if (action === 'distort-ocean-ripple') applyOceanRipple?.();
        else if (action === 'distort-glass') applyGlass?.();
        else if (action === 'pixelate-mosaic-tiles') applyMosaicTiles?.();
        else if (action === 'stylize-wind') applyWind?.();
        else if (action === 'stylize-trace-contour') applyTraceContour?.();
        else if (action === 'sketch-halftone-pattern') applyHalftonePattern?.();
        else if (action === 'sketch-reticulation') applyReticulation?.();
        else if (action === 'distort-shear') applyShear?.();
        else if (action === 'sketch-note-paper') applyNotePaper?.();
        else if (action === 'sketch-charcoal') applyCharcoal?.();
        else if (action === 'sketch-chalk-charcoal') applyChalkCharcoal?.();
        else if (action === 'sketch-graphic-pen') applyGraphicPen?.();
        else if (action === 'sketch-photocopy') applyPhotocopy?.();
        else if (action === 'sketch-water-paper') applyWaterPaper?.();
        else if (action === 'sharpen-more') applySharpenMore?.();
        else if (action === 'artistic-poster-edges') applyPosterEdges?.();
        else if (action === 'artistic-cutout') applyCutout?.();
        else if (action === 'brush-ink-outlines') applyInkOutlines?.();
        else if (action === 'texture-stained-glass') applyStainedGlass?.();
        else if (action === 'texture-grain') applyGrain?.();
        else if (action === 'texture-texturizer') applyTexturizer?.();
        else if (action === 'texture-patchwork') applyPatchwork?.();
        else if (action === 'texture-craquelure') applyCraquelure?.();
        else if (action === 'brush-crosshatch') applyCrosshatch?.();
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
