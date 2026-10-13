/**
 * Gallery Editor — canvas-based image editor with layers, brush, eraser, text, crop, inpaint mask.
 */

import uiModule from './ui.js?v=20260916largetoolscroll1';
import dragSortModule from './dragSort.js';
import spinnerModule from './spinner.js';
import { attachColorPicker } from './colorPicker.js?v=20260910eyedropper1';
import modalManager from './modalManager.js';
import { canvasCoords as _canvasCoords } from './editor/canvas-coords.js';
import { drawCheckerboard as _drawCheckerboard } from './editor/checkerboard.js';
import { dilateMask as _dilateMask, applyInpaintFeather as _applyInpaintFeather } from './editor/mask-utils.js';
import {
  lassoOffsetPoints as _lassoOffsetPointsImpl,
  getLassoPath as _getLassoPathImpl,
  buildLassoMask as _buildLassoMaskImpl,
} from './editor/tools/lasso-mask.js';
import { floodFillMask as _floodFillMask } from './editor/tools/flood-fill.js';
import { drawHistogram as _drawHistogram } from './editor/fx/histogram.js';
import {
  applyAdjustment as _applyAdjToCanvas,
  renderLayerPixelAdjustments as _renderLayerPixelAdjustmentsImpl,
  renderLayerWithAdjLayers as _renderLayerWithAdjLayers,
} from './editor/fx/pixel-pass.js';
import {
  layerFilterString as _layerFilterString,
  fxFilterToSlider as _fxFilterToSlider,
} from './editor/fx/filter-string.js';
import {
  layerHasAdjustments as _layerHasAdjustments,
  layerNeedsPixelPass as _layerNeedsPixelPass,
  adjustmentsKey as _adjustmentsKey,
  defaultAdjParams as _defaultAdjParams,
  adjLayerLabel as _adjLayerLabel,
  ADJ_ICONS as _ADJ_ICONS,
  HISTORY_ICON as _HISTORY_ICON,
  isMaskCanvasEmpty as _isMaskCanvasEmpty,
  isLayerEmpty as _isLayerEmpty,
  relTime as _relTime,
} from './editor/layer-helpers.js';
import {
  renderEffects as _renderEffects,
  effectsWithPreview as _effectsWithPreview,
  renderEffectsAsync as _renderEffectsAsync,
  normalizeEffect as _normalizeEffect,
  effectLabel as _effectLabel,
  effectPreset as _effectPreset,
} from './editor/effects.js';
import {
  computeSnap as _computeSnapImpl,
  computeTransformSnap as _computeTransformSnapImpl,
  cursorForHandle as _cursorForHandle,
} from './editor/snap.js';
import {
  layerUnionAlpha as _layerUnionAlphaImpl,
  seamMask as _seamMaskImpl,
  layerBodyMask as _layerBodyMaskImpl,
} from './editor/harmonize-masks.js';
import {
  gaussianBlur as _gaussianBlur,
  zoomBlur as _zoomBlur,
  motionBlur as _motionBlur,
} from './editor/filters/blur.js';
import { edgeFeather as _edgeFeather } from './editor/filters/edge-feather.js';
import { createRenderGeneration as _createRenderGeneration } from './editor/render-cancellation.js';
import {
  buildMergedMaskCanvas as _buildMergedMaskCanvasImpl,
  renderWithLayerMasks as _renderWithLayerMasks,
} from './editor/composite-helpers.js';
import {
  drawAdjustmentLayer as _drawAdjustmentLayer,
  drawAdjustmentLayerAsync as _drawAdjustmentLayerAsync,
  normalizeAdjustmentData as _normalizeAdjustmentData,
} from './editor/adjustment-layer.js';
import { buildToolbar as _buildToolbar } from './editor/build/toolbar.js?v=20260830editor2';
import { buildTopbar as _buildTopbar, buildZoomFooter as _buildZoomFooter } from './editor/build/topbar.js';
import {
  controlsHTML as _controlsHTML,
  layerPanelHTML as _layerPanelHTML,
} from './editor/build/controls.js?v=20260830editor2';
import {
  transformPopupHTML as _transformPopupHTML,
  attachSpinRepeat as _attachSpinRepeat,
} from './editor/build/transform-popup.js';
import {
  shortcutsPopupHTML as _shortcutsPopupHTML,
  historyPanelHTML as _historyPanelHTMLImpl,
  canvasSizePromptHTML as _canvasSizePromptHTML,
} from './editor/build/popups.js';
import { state } from './editor/state.js';
import {
  cloneDocumentValue as _cloneDocumentValue,
  EDITOR_PROJECT_MAX_BYTES as _EDITOR_PROJECT_MAX_BYTES,
  nextLayerIdFromDocument as _nextLayerIdFromDocument,
  normalizeEditorView as _normalizeEditorView,
  prepareEditorDocument as _prepareEditorDocument,
  serializeEditorDocument as _serializeEditorDocument,
} from './editor/document-codec.js';
import {
  snapshotByteSize as _snapshotByteSize,
  trimHistoryStack as _trimHistoryStack,
  shareSnapshotPixels as _shareSnapshotPixels,
} from './editor/history-budget.js';
import { cropDocument as _cropDocument } from './editor/document-geometry.js';
import { encodeExportCanvas as _encodeExportCanvas, openExportDialog as _openExportDialog } from './editor/export-dialog.js';
import {
  normalizeTextData as _normalizeTextData,
  rasterizeTextLayer as _rasterizeTextLayer,
  renderTextLayer as _renderTextLayer,
} from './editor/text-layer.js';
import { createTextEditOverlay } from './editor/text-edit-overlay.js';
import {
  normalizeGradientStops as _normalizeGradientStops,
  normalizeShapeData as _normalizeShapeData,
  rasterizeShapeLayer as _rasterizeShapeLayer,
  renderShapeLayer as _renderShapeLayer,
} from './editor/shape-layer.js';
import {
  createPlacedData as _createPlacedData,
  rasterizePlacedLayer as _rasterizePlacedLayer,
  renderPlacedLayer as _renderPlacedLayer,
  replacePlacedSource as _replacePlacedSource,
} from './editor/placed-layer.js';
import { createMoveTool } from './editor/tools/move.js';
import { createLayerGeometryController } from './editor/layer-geometry.js';
import {
  drawGroupedLayers as _drawGroupedLayers,
  drawGroupedLayersAsync as _drawGroupedLayersAsync,
  groupAncestors as _groupAncestors,
  isLayerEffectivelyLocked as _isLayerEffectivelyLocked,
  isLayerPixelLocked as _isLayerPixelLocked,
  isLayerTransparencyLocked as _isLayerTransparencyLocked,
  normalizeLayerLocks as _normalizeLayerLocks,
  normalizeLayerGroups as _normalizeLayerGroups,
} from './editor/layer-groups.js';
import { normalizeLayerClipping as _normalizeLayerClipping } from './editor/layer-clipping.js';
import { createCropTool } from './editor/tools/crop.js';
import { createLassoTool } from './editor/tools/lasso.js';
import { createPenSelectionTool } from './editor/tools/pen-selection.js';
import { LAYER_STYLES, styleParams } from './editor/layer-styles.js';
import { openLayerStyleMenu } from './editor/layer-style-menu.js';
import { createMarqueeTool } from './editor/tools/marquee.js';
import { createWandTool } from './editor/tools/wand.js';
import {
  createMarqueeMask as _createMarqueeMask,
  mergeSelectionMasks as _mergeSelectionMasks,
  paintSelectionBoundary as _paintSelectionBoundary,
  refineSelectionMask as _refineSelectionMask,
  selectionBoundaryPixels as _selectionBoundaryPixels,
  selectionMaskForLayer as _selectionMaskForLayer,
  selectionMaskToDocument as _selectionMaskToDocument,
  translateSelectionMask as _translateSelectionMask,
} from './editor/selection-mask.js';
import { createCloneTool } from './editor/tools/clone.js';
import { createEyedropperTool } from './editor/tools/eyedropper.js';
import { createGradientTool } from './editor/tools/gradient.js';
import { createTransformDragTool } from './editor/tools/transform-drag.js';
import { createStrokeTool } from './editor/tools/stroke.js';
import { createLayerPanelRenderer } from './editor/layer-panel.js';
import {
  syncOverlay as _syncTransformOverlayImpl,
  drawHandles as _drawTransformHandlesImpl,
  getHandleAt as _getTransformHandleImpl,
  containsFramePoint as _containsTransformFramePoint,
} from './editor/tools/transform-handles.js';
import { createCanvasTransforms } from './editor/canvas-transforms.js';
import { createApplyImageTool } from './editor/ai-tool-runner.js';
import { createStrokePipeline } from './editor/stroke-pipeline.js';
import { createAdjPopupSystem } from './editor/fx/adj-popup.js';
import { createHistoryPanel } from './editor/history-panel.js';
import { createTransformSession } from './editor/tools/transform-session.js';
import { wireCanvasEvents } from './editor/canvas-events.js';
import { buildRightPanel } from './editor/build/right-panel.js';
import { wireSliderUx } from './editor/slider-ux.js';
import { createShortcutsPopover } from './editor/shortcuts-popover.js';
import { wireKeyboardShortcuts } from './editor/keyboard-shortcuts.js';
import { wireClipboardAndDrop } from './editor/clipboard-and-drop.js';
import { wireAIModelSelectors, resolveInpaintModel } from './editor/ai-models.js';
import { wireInpaintButtons } from './editor/ai-inpaint.js?v=20260708match1';
import { wireAIToolsMisc } from './editor/ai-tools-misc.js';
import { wireRembgAndSharpen } from './editor/ai-rembg.js';
import { wireStrokeToolSliders } from './editor/stroke-tool-sliders.js';
import { wireImport } from './editor/wire-import.js';
import { wireMergeButtons } from './editor/wire-merge-buttons.js';
import { wireSelectionControls } from './editor/wire-selection-controls.js';
import { wireInpaintControls } from './editor/wire-inpaint-controls.js?v=20260708match1';
import { wireTopbar, closeOtherTopbarMenus as _closeOtherTopbarMenus } from './editor/wire-topbar.js';
import { wireTopbarOverflow } from './editor/wire-topbar-overflow.js';
import { wireTopbarMenus } from './editor/wire-topbar-menus.js';
import { createPrecisionGuides } from './editor/precision-guides.js';
import { wireViewMenu } from './editor/wire-view-menu.js';

const API_BASE = window.location.origin;
// ── State ──
// Transform-overlay canvas — sits over the main canvas with extra margin
// so resize / rotation handles render OUTSIDE the image edges. Pointer
// events disabled; the main canvas still handles all input.
const _TRANSFORM_OVERLAY_MARGIN = 60; // screen-space px of slack on each side
// Thin wrappers around the transform-handles impls — the refactor
// imported them under *Impl aliases but several call sites still use
// the bare names. Without these, _startTransform threw a ReferenceError
// before opening the popup, so Transform showed no handles / no popup.
function _drawTransformHandles() { _drawTransformHandlesImpl(_TRANSFORM_OVERLAY_MARGIN); }
function _getTransformHandle(x, y, options) { return _getTransformHandleImpl(x, y, options); }
function _syncTransformOverlay() { _syncTransformOverlayImpl(_TRANSFORM_OVERLAY_MARGIN); }
function _computeTransformSnap(frame, center) {
  const selectedIds = new Set(state.selectedLayerIds || []);
  return _computeTransformSnapImpl(frame, center, {
    zoom: state.zoom,
    canvasW: state.imgWidth,
    canvasH: state.imgHeight,
    otherLayers: state.layers.filter(layer => !selectedIds.has(layer.id)).map(layer => ({
      visible: layer.visible,
      id: layer.id,
      canvas: layer.canvas,
      offset: state.layerOffsets.get(layer.id) || { x: 0, y: 0 },
    })),
    verticalGuides: state.guides?.vertical || [],
    horizontalGuides: state.guides?.horizontal || [],
    snapToGrid: !!state.snapToGrid,
    gridSize: state.gridSize || 16,
  });
}
// Inpaint uses a much bigger default brush — when the user enters
// the inpaint tool for the first time in this editor session we bump
// the slider to this value (without touching other tools).
const _INPAINT_DEFAULT_BRUSH = 100;
let _samAbortController = null;
let _precisionGuides = null;
let _selectionAnimationFrame = null;
let _selectionAnimationLast = 0;
let _selectionAnimationPhase = 0;
let _selectionBoundaryCache = null;
let _selectionNudgeTimer = null;
let _selectionNudgeHistorySaved = false;
let _renderSavedSelectionsMenu = null;

function _cancelSamQuery(showToast = true) {
  if (!_samAbortController) return false;
  try { _samAbortController.abort(); } catch {}
  if (showToast && uiModule) uiModule.showToast('SAM query cancelled');
  return true;
}

function _galleryEditMounted() {
  return !!document.querySelector('#gallery-editor-container .gallery-editor');
}

if (!window.__galleryEditEscHardGuardInstalled) {
  window.__galleryEditEscHardGuardInstalled = true;
  window.addEventListener('keydown', (e) => {
    if (_activeFilterPrompt) {
      _activeFilterPrompt.handleKey(e);
      return;
    }
    if (_rasterizePromptPending && (e.key === 'Escape' || e.key === 'Enter')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      const target = e.key === 'Escape' ? 'styled-confirm-cancel'
        : (e.target?.id === 'styled-confirm-cancel' ? 'styled-confirm-cancel' : 'styled-confirm-ok');
      document.getElementById(target)?.click();
      return;
    }
    if (e.target?.closest?.('#styled-confirm-overlay')) return;
    if (state.editorOpen && state.tool === 'pen' && !e.target?.closest?.('input, textarea, select, [contenteditable="true"]') && _penTool.key(e)) return;
    const isSamCancel = !!_samAbortController
      && (e.key === 'Escape' || ((e.ctrlKey || e.metaKey) && String(e.key || '').toLowerCase() === 'c'));
    if (isSamCancel) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _cancelSamQuery();
      return;
    }
    if (e.key !== 'Escape') return;
    // Quick Edit owns Escape while expanded. Dispatch a local close event
    // before the gallery-level guard can consume it for the whole editor.
    const quickEdit = e.target?.closest?.('#ge-ai-command');
    if (quickEdit && !quickEdit.classList.contains('ge-ai-command-collapsed')) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      quickEdit.dispatchEvent(new CustomEvent('ge-ai-command-close'));
      return;
    }
    const renameInput = e.target?.closest?.('.ge-layer-name-input');
    if (renameInput) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      renameInput.dataset.cancelRename = 'true';
      renameInput.blur();
      return;
    }
    // Resolve the New Project prompt before any outer modal handler can hide
    // it without completing the pending openEditor() promise.
    const sizePrompt = e.target?.closest?.('#ge-canvas-size-overlay');
    if (sizePrompt) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      sizePrompt._cancelCanvasSize?.();
      return;
    }
    // Window capture runs before Gallery's document-level Escape guard. Handle
    // the transform here so no surrounding modal can swallow the cancellation.
    if (state.transformActive) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _cancelTransform();
      return;
    }
    if (state.cropRect || state.cropping || state.cropMoving) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _cancelCrop('escape');
      return;
    }
    if (state.marqueeActive || state.selectionMoving) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _marqueeTool.cancel('escape');
      return;
    }
    if (state.lassoActive || state.lassoPoints.length) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _lassoTool.cancel();
      return;
    }
    if (state.gradientActive) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _gradientTool.cancel();
      return;
    }
    if (state.drawing) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _strokeTool.cancel();
      return;
    }
    if (_shapeDraft) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _cancelShapeDraft();
      return;
    }
    if (window.__galleryEditLive || _galleryEditMounted()) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
    }
  }, true);
}

// Document-level click-away handlers for topbar dropdowns. Each
// openEditor invocation adds 6 of these (save / edge / image / filter /
// resize / more), and without removal they accumulated across reopens.
// Tracked here and removed wholesale in closeEditor.
function _registerDocClickAway(handler) {
  document.addEventListener('click', handler);
  state.editorDocClickHandlers.push(handler);
}

// Drawing state

// Move tool state
// Crop state
// Persistent mode toggle for wand clicks. 'replace' = a new click
// replaces the selection (default); 'add' = always union; 'subtract' =
// always remove from the existing selection. Shift / Alt held during a
// click still override this transiently.
// Last seed click so the tolerance slider can re-run the wand live.
// Stored in canvas coords (same units `_runMagicWand` accepts).
// Lasso state

// Transform state
// Snapshot of the layer's pixels at the moment the transform started.
// Lets the popup live-preview by re-applying from the original on every
// input change instead of stacking destructive edits.
// Current popup-driven values (re-applied on every change).

// Inpaint mask (separate canvas, same dimensions as image)
// Cached canvas reused each composite() to merge every visible mask
// sub-layer into a single tinted overlay (avoids re-allocating on each
// frame). Recreated lazily if dimensions change.
// Softer default than the original full-saturation red — the user
// found the previous tint distracting. Tweakable via the color picker
// under the Paint/Erase row.
// Persistent paint/erase toggle for the Inpaint brush. False = paint
// (default), true = erase. Ctrl+Alt held during a stroke flips this
// transiently for the duration of that one stroke.
// Resolved per-stroke at pointerdown: state.inpaintEraseMode XOR (Ctrl+Alt).
// Most-recent inpaint result layer id — the post-generation Feather
// slider edits this layer's alpha edge live.

// _dilateMask + _applyInpaintFeather live in editor/mask-utils.js
// — see import at top of file.

// Eraser settings
// Edge softness, 0..100. 0 = hard pixel edge; higher values blur the
// stroke's alpha so the eraser fades out at the brush perimeter.
// Brush settings (same shape as eraser).
// Clone Stamp brush modifiers — independent from the Brush tool's
// settings so users can dial in cloning without losing their brush
// preset (and vice-versa).
// `state.cloneSourceX/Y` is the sample anchor set by Alt-click. While
// painting, the source point moves in lockstep with the brush so the
// sampled offset stays constant (Photoshop "aligned" mode).
// First brush coord of the current stroke — used to compute the
// running offset (`sample = source + (current - strokeStart)`).
// Snapshot of the source layer's pixels at stroke-start so we can keep
// sampling clean pixels even after the brush has painted over them
// (avoids feedback / smearing).
// Double-tap detection for the Clone tool on touch devices — sets the
// sample anchor without a keyboard Alt modifier.

// Undo/Redo snapshots are raw RGBA data, so entry count alone is not a safe
// bound for large layered documents. history-budget.js applies both limits.

/** Get the selected AI endpoint+model. Returns { endpoint, model }.
 * Dropdown values are encoded as "<base_url>::<model_id>" so users can pick
 * a specific model on a multi-model endpoint (e.g. dall-e-2 vs gpt-image-1). */
function _getSelectedAIEndpoint(type) {
  let raw = '';
  if (type === 'inpaint') {
    const option = resolveInpaintModel(document.getElementById('ge-ai-inpaint'));
    if (!option) throw new Error('No available inpaint model. Select or connect an image-editing endpoint.');
    raw = option.value;
  } else if (type) {
    // Per-tool dropdowns (harmonize/upscale/style). Each lives in its
    // own section's panel and is marked with data-ge-tool-model="<name>".
    const sel = document.querySelector(`select[data-ge-tool-model="${type}"]`);
    raw = sel?.value || '';
  }
  if (!raw) raw = document.getElementById('ge-ai-model')?.value || '';
  if (!raw) return { endpoint: '', model: '' };
  const idx = raw.indexOf('::');
  if (idx < 0) return { endpoint: raw, model: '' };
  return { endpoint: raw.slice(0, idx), model: raw.slice(idx + 2) };
}

/** Shared helper: flatten layers → POST to API → add result as new layer. */
// Maps a layer-name (the past-participle returned from each AI tool —
// "BG Removed", "Sharpened", etc.) into a present-progressive label for
// the busy button state ("Removing…", "Sharpening…"). Falls back to a
// neutral "Processing…" when the layer name doesn't match a known verb.
const _BUSY_LABELS = {
  'bg removed': 'Removing…',
  'sharpened': 'Sharpening…',
  'enhanced': 'Enhancing…',
  'harmonized': 'Harmonizing…',
  'upscaled': 'Upscaling…',
  'styled': 'Styling…',
};
function _deriveBusyLabel(layerName) {
  if (!layerName) return 'Processing…';
  return _BUSY_LABELS[String(layerName).toLowerCase()] || 'Processing…';
}

// AI-tool runner — sharpen / harmonize / upscale / style / bg-remove
// all flatten the doc, POST a PNG to a server endpoint, and drop the
// result back as a new layer. Full implementation in editor/ai-tool-
// runner.js; instantiated lazily (so it can reference function decls
// that haven't hoisted at module load time? — actually all named
// function decls hoist, so we instantiate at module top).
const _applyImageTool = createApplyImageTool({
  flatten: () => flatten(),
  saveState: _saveState,
  createLayer,
  composite,
  renderLayerPanel: () => _renderLayerPanel(),
  deriveBusyLabel: (name) => _deriveBusyLabel(name),
  getSelectedAIEndpoint: (type) => _getSelectedAIEndpoint(type),
  openCookbookForDependency: (pkg) => _openCookbookForDependency(pkg),
  openCookbookForImg2img: () => _openCookbookForImg2img(),
  spinnerModule,
  uiModule,
});

function _setAiCommandStatus(text, kind = '') {
  const el = document.getElementById('ge-ai-command-status');
  if (!el) return;
  el.textContent = text || '';
  el.dataset.kind = kind || '';
}

function _escapeAiCommandText(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function _clickToolButton(toolId) {
  const btn = state.container?.querySelector(`.ge-tool-btn[data-tool="${toolId}"]`);
  if (btn) btn.click();
}

function _runExistingButton(id, status) {
  const btn = document.getElementById(id);
  if (!btn) {
    _setAiCommandStatus('That edit is not available in this editor state.', 'error');
    return null;
  }
  if (btn.disabled) {
    _setAiCommandStatus('That edit is already running.', 'error');
    return null;
  }
  if (status) _setAiCommandStatus(status, 'running');
  btn.click();
  return btn;
}

function _waitForExistingButton(btn) {
  if (!btn) return Promise.resolve();
  return new Promise(resolve => {
    let sawBusy = !!(btn.disabled || btn.classList.contains('ge-btn-processing'));
    let frames = 0;
    const check = () => {
      const busy = !!(btn.disabled || btn.classList.contains('ge-btn-processing'));
      sawBusy ||= busy;
      // Tool handlers set their busy state synchronously on click. The
      // short fallback also prevents a stale/missing handler from locking
      // Quick Edit forever.
      if ((sawBusy && !busy) || (!sawBusy && frames > 2) || frames > 36000) {
        resolve();
        return;
      }
      frames += 1;
      requestAnimationFrame(check);
    };
    requestAnimationFrame(check);
  });
}

function _openSamPrompt() {
  if (state.tool !== 'sam') {
    _clickToolButton('sam');
  } else {
    const controls = document.getElementById('ge-controls') || document.querySelector('.ge-controls');
    controls?.classList.remove('dismissed');
    document.getElementById('ge-sam-section')?.style.removeProperty('display');
  }
  requestAnimationFrame(() => {
    const input = document.getElementById('ge-sam-query');
    input?.focus();
    input?.select?.();
  });
}

function _buildAiCommandBox() {
  const wrap = document.createElement('div');
  wrap.className = 'ge-ai-command ge-ai-command-collapsed';
  wrap.id = 'ge-ai-command';
  wrap.innerHTML = `
    <button type="button" class="ge-ai-command-toggle" id="ge-ai-command-toggle" aria-expanded="false">
      <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
      <span>Quick Edit</span>
      <svg class="ge-ai-command-toggle-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="6 15 12 9 18 15"></polyline>
      </svg>
    </button>
    <div class="ge-ai-command-head">
      <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
      <span class="ge-ai-command-title">Quick Edit</span>
      <span class="ge-ai-command-scope">Document</span>
      <button type="button" class="ge-ai-command-close" id="ge-ai-command-close" title="Collapse Quick Edit" aria-label="Collapse Quick Edit">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>
    </div>
    <form class="ge-ai-command-form" id="ge-ai-command-form">
      <input type="text" class="ge-ai-command-input" id="ge-ai-command-input" autocomplete="off" aria-label="Describe the image edit" placeholder="Describe an edit, or choose an action" />
      <button type="button" class="ge-ai-command-clear" id="ge-ai-command-clear" title="Clear command" aria-label="Clear command" hidden>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
      <button type="submit" class="ge-ai-command-run" id="ge-ai-command-run" title="Run AI edit" aria-label="Run AI edit">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <line x1="12" y1="19" x2="12" y2="5"></line>
          <polyline points="5 12 12 5 19 12"></polyline>
        </svg>
      </button>
    </form>
    <div class="ge-ai-command-suggestions" id="ge-ai-command-suggestions" hidden></div>
    <div class="ge-ai-command-status" id="ge-ai-command-status" aria-live="polite"></div>
  `;
  return wrap;
}

const _AI_COMMAND_SUGGESTIONS = [
  { label: 'Rotate 90', insert: 'rotate 90', hint: 'Turn the image clockwise', kind: 'Local', aliases: ['ro', 'rotate', 'right', 'clockwise', 'turn'] },
  { label: 'Rotate left', insert: 'rotate left', hint: 'Turn the image counter-clockwise', kind: 'Local', aliases: ['rotate left', 'left', 'counter clockwise', 'ccw'] },
  { label: 'Rotate 180', insert: 'rotate 180', hint: 'Flip the canvas upside down', kind: 'Local', aliases: ['rotate 180', 'upside down'] },
  { label: 'Flip horizontal', insert: 'flip horizontal', hint: 'Mirror left to right', kind: 'Local', aliases: ['flip', 'mirror', 'horizontal'] },
  { label: 'Flip vertical', insert: 'flip vertical', hint: 'Mirror top to bottom', kind: 'Local', aliases: ['flip vertical', 'vertical'] },
  { label: 'Remove background', insert: 'remove background', hint: 'Make the background transparent', kind: 'AI', aliases: ['remove bg', 'background', 'transparent', 'cut out'] },
  { label: 'Upscale', insert: 'upscale 2x', hint: 'Increase image resolution', kind: 'AI', aliases: ['upscale', 'bigger', 'larger', '2x', '4x'] },
  { label: 'Denoise', insert: 'denoise', hint: 'Reduce grain and noise', kind: 'AI', aliases: ['denoise', 'noise', 'grain', 'clean up'] },
  { label: 'Sharpen', insert: 'sharpen', hint: 'Make details crisper', kind: 'AI', aliases: ['sharpen', 'sharp', 'clearer', 'crisp', 'enhance'] },
  { label: 'Enhance face', insert: 'enhance face', hint: 'Restore portrait and skin detail', kind: 'AI', aliases: ['face', 'portrait', 'skin', 'selfie', 'restore'] },
  { label: 'Style edit', insert: 'style: ', hint: 'Run a full-image prompt edit', kind: 'AI', aliases: ['style', 'paint', 'anime', 'photo', 'prompt'] },
];

function _matchAiCommandSuggestions(query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return [];
  return _AI_COMMAND_SUGGESTIONS
    .map((item) => {
      const hay = [item.label, item.insert, ...(item.aliases || [])].map(v => String(v || '').toLowerCase());
      const starts = hay.some(v => v.startsWith(q));
      const contains = hay.some(v => v.includes(q));
      if (!starts && !contains) return null;
      return { item, score: starts ? 0 : 1 };
    })
    .filter(Boolean)
    .sort((a, b) => a.score - b.score || a.item.label.localeCompare(b.item.label))
    .slice(0, 3)
    .map(hit => hit.item);
}

function _wireAiCommandBox() {
  const wrap = document.getElementById('ge-ai-command');
  const toggle = document.getElementById('ge-ai-command-toggle');
  const closeBtn = document.getElementById('ge-ai-command-close');
  const form = document.getElementById('ge-ai-command-form');
  const input = document.getElementById('ge-ai-command-input');
  const clearBtn = document.getElementById('ge-ai-command-clear');
  const runBtn = document.getElementById('ge-ai-command-run');
  const suggestions = document.getElementById('ge-ai-command-suggestions');
  if (!wrap || !form || !input || !runBtn) return;
  let suggestionItems = [];
  let suggestionIndex = 0;
  let busy = false;
  const syncClearButton = () => {
    if (clearBtn) clearBtn.hidden = !input.value;
  };
  const setBusy = next => {
    busy = !!next;
    wrap.classList.toggle('ge-ai-command-busy', busy);
    input.disabled = busy;
    runBtn.disabled = busy;
    if (clearBtn) clearBtn.disabled = busy;
  };
  const hideSuggestions = () => {
    suggestionItems = [];
    suggestionIndex = 0;
    if (suggestions) {
      suggestions.hidden = true;
      suggestions.innerHTML = '';
    }
  };
  const renderSuggestions = () => {
    if (!suggestions || wrap.classList.contains('ge-ai-command-collapsed')) return;
    suggestionItems = _matchAiCommandSuggestions(input.value);
    suggestionIndex = Math.min(suggestionIndex, Math.max(0, suggestionItems.length - 1));
    if (!suggestionItems.length) {
      hideSuggestions();
      return;
    }
    suggestions.hidden = false;
    suggestions.innerHTML = suggestionItems.map((item, idx) => `
      <button type="button" class="ge-ai-command-suggestion${idx === suggestionIndex ? ' active' : ''}" data-ai-command-suggestion="${idx}">
        <span class="ge-ai-command-suggestion-kind">${_escapeAiCommandText(item.kind || 'Edit')}</span>
        <span class="ge-ai-command-suggestion-main">${_escapeAiCommandText(item.label)}</span>
        <span class="ge-ai-command-suggestion-hint">${_escapeAiCommandText(item.hint || item.insert)}</span>
      </button>
    `).join('');
  };
  const pickSuggestion = (idx, run = false) => {
    const item = suggestionItems[idx];
    if (!item) return false;
    input.value = item.insert;
    hideSuggestions();
    input.focus();
    if (run) form.requestSubmit();
    return true;
  };
  wrap.addEventListener('pointerdown', (e) => e.stopPropagation());
  wrap.addEventListener('click', (e) => e.stopPropagation());
  suggestions?.addEventListener('pointerdown', (e) => e.preventDefault());
  suggestions?.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-ai-command-suggestion]');
    if (!btn) return;
    pickSuggestion(Number(btn.dataset.aiCommandSuggestion), false);
  });
  const setOpen = (open) => {
    wrap.classList.toggle('ge-ai-command-collapsed', !open);
    toggle?.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) requestAnimationFrame(() => {
      input.focus();
      renderSuggestions();
    });
    else hideSuggestions();
  };
  wrap.addEventListener('ge-ai-command-close', () => setOpen(false));
  toggle?.addEventListener('click', () => setOpen(wrap.classList.contains('ge-ai-command-collapsed')));
  closeBtn?.addEventListener('click', () => setOpen(false));
  clearBtn?.addEventListener('click', () => {
    input.value = '';
    syncClearButton();
    _setAiCommandStatus('', '');
    hideSuggestions();
    input.focus();
    renderSuggestions();
  });
  input.addEventListener('input', () => {
    syncClearButton();
    renderSuggestions();
  });
  input.addEventListener('keydown', (e) => {
    const open = suggestions && !suggestions.hidden && suggestionItems.length;
    if (open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      suggestionIndex = e.key === 'ArrowDown'
        ? (suggestionIndex + 1) % suggestionItems.length
        : (suggestionIndex - 1 + suggestionItems.length) % suggestionItems.length;
      renderSuggestions();
      return;
    }
    if (open && e.key === 'Enter') {
      e.preventDefault();
      pickSuggestion(suggestionIndex, true);
      return;
    }
    if (open && e.key === 'Tab') {
      e.preventDefault();
      pickSuggestion(suggestionIndex, false);
      return;
    }
    if (open && e.key === 'Escape') {
      e.preventDefault();
      hideSuggestions();
    }
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (busy) return;
    hideSuggestions();
    const prompt = input.value.trim();
    if (!prompt) {
      _setAiCommandStatus('Type what you want changed.', 'error');
      input.focus();
      return;
    }
    const p = prompt.toLowerCase();
    setBusy(true);
    try {
      if (/\brotate\b.*\b180\b|\bupside\s*down\b/.test(p)) {
        _rotateAllLayers(180);
        requestAnimationFrame(() => _setAiCommandStatus('Rotated 180.', 'done'));
        return;
      }
      if (/\brotate\b.*\b(left|ccw|counter)\b|\bturn\s+left\b/.test(p)) {
        _rotateAllLayers(270);
        requestAnimationFrame(() => _setAiCommandStatus('Rotated left.', 'done'));
        return;
      }
      if (/\brotate\b|\bturn\s+right\b|\bclockwise\b/.test(p)) {
        _rotateAllLayers(90);
        requestAnimationFrame(() => _setAiCommandStatus('Rotated 90.', 'done'));
        return;
      }
      if (/\bflip\b.*\b(vertical|v)\b|\bmirror\b.*\b(vertical|v)\b/.test(p)) {
        _flipAllLayers('v');
        _setAiCommandStatus('Flipped vertical.', 'done');
        return;
      }
      if (/\bflip\b|\bmirror\b/.test(p)) {
        _flipAllLayers('h');
        _setAiCommandStatus('Flipped horizontal.', 'done');
        return;
      }
      if (/\b(remove|erase|cut\s*out|transparent)\b.*\b(bg|background)\b|\b(bg|background)\b.*\b(remove|erase|transparent)\b/.test(p)) {
        _clickToolButton('rembg');
        const target = _runExistingButton('ge-rembg-run', 'Removing background...');
        if (!target) return;
        await _waitForExistingButton(target);
        _setAiCommandStatus('Background removal finished.', 'done');
        return;
      }
      if (/\b(upscale|higher\s*res|increase\s*resolution|bigger|2x|4x)\b/.test(p)) {
        _clickToolButton('upscale');
        const target = _runExistingButton('ge-upscale-ai', 'Upscaling image...');
        if (!target) return;
        await _waitForExistingButton(target);
        _setAiCommandStatus('Upscale finished.', 'done');
        return;
      }
      if (/\b(denoise|noise|grain|grainy|clean\s*up)\b/.test(p)) {
        _setAiCommandStatus('Denoising image...', 'running');
        await _applyImageTool('/api/image/denoise', { strength: 0.55 }, 'Denoised', runBtn, { busyLabel: 'Denoising...' });
        _setAiCommandStatus('Added denoised layer.', 'done');
        return;
      }
      if (/\b(face|portrait|skin|selfie|restore)\b/.test(p)) {
        _setAiCommandStatus('Enhancing face/portrait...', 'running');
        await _applyImageTool('/api/image/enhance-face', {}, 'Enhanced Face', runBtn, { busyLabel: 'Enhancing...' });
        _setAiCommandStatus('Added enhanced layer.', 'done');
        return;
      }
      if (/\b(sharpen|sharp|crisp|clearer|make it look better|enhance|improve|better)\b/.test(p)) {
        const amount = document.getElementById('ge-sharpen-amount');
        if (amount) {
          amount.value = '65';
          amount.dispatchEvent(new Event('input', { bubbles: true }));
        }
        _clickToolButton('sharpen');
        const target = _runExistingButton('ge-sharpen-run', 'Sharpening image...');
        if (!target) return;
        await _waitForExistingButton(target);
        _setAiCommandStatus('Sharpen finished.', 'done');
        return;
      }

      const stylePrompt = document.getElementById('ge-style-prompt');
      const styleStrength = document.getElementById('ge-style-strength');
      if (stylePrompt) stylePrompt.value = prompt;
      if (styleStrength) {
        styleStrength.value = /\b(subtle|slight|small)\b/.test(p) ? '35' : '55';
        styleStrength.dispatchEvent(new Event('input', { bubbles: true }));
      }
      _clickToolButton('style');
      const target = _runExistingButton('ge-style-run', 'Running full-image AI edit...');
      if (!target) return;
      await _waitForExistingButton(target);
      _setAiCommandStatus('Style edit finished.', 'done');
    } catch (err) {
      console.error('[ge-ai-command] failed', err);
      _setAiCommandStatus(err?.message || 'AI edit failed', 'error');
    } finally {
      setBusy(false);
    }
  });
  syncClearButton();
}

// Layer offsets for move tool

// ── Layer class ──


function createLayer(name, width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const layer = {
    id: 'layer-' + (state.nextLayerId++),
    name,
    canvas,
    ctx: canvas.getContext('2d'),
    visible: true,
    opacity: 1,
    locked: false,
    locks: { pixels: false, transparency: false, position: false },
    clipped: false,
    blendMode: 'source-over',
    kind: 'raster',
    text: null,
    shape: null,
    adjustment: null,
    effects: [],
    placed: null,
    // Mask sub-layers — same shape as adjLayers, parallel concept.
    // Each entry: {id, name, canvas, visible}. The "active" mask is the
    // one that paint / lasso / inpaint operations target; rendered as a
    // red overlay in composite().
    masks: [],
    activeMaskId: null,
    // Non-destructive adjustments. B/C/S/H are applied at composite()
    // via ctx.filter (fast, CSS). Levels + Color Balance need per-pixel
    // math and are baked into a cached canvas (layer._adjCache) that
    // gets re-rendered only when those values change.
    adjustments: {
      brightness: 1, // 0..2 (1 = neutral)
      contrast: 1,   // 0..2 (1 = neutral)
      saturation: 1, // 0..2 (0 = grayscale, 1 = neutral)
      hue: 0,        // degrees, -180..180
      // Levels — Photoshop-style three-stop adjust applied per channel.
      // input 0..255, gamma 0.1..9.9. Default is identity.
      levels: { inBlack: 0, inWhite: 255, gamma: 1.0, outBlack: 0, outWhite: 255 },
      // Color Balance — additive per-channel shifts weighted by tone.
      // Each value is -100..+100 mapping to roughly ±60 in 0..255 space.
      colorBalance: {
        shadows:    { r: 0, g: 0, b: 0 },
        midtones:   { r: 0, g: 0, b: 0 },
        highlights: { r: 0, g: 0, b: 0 },
      },
    },
  };
  state.layerOffsets.set(layer.id, { x: 0, y: 0 });
  return layer;
}

// _layerFilterString + _fxFilterToSlider live in editor/fx/filter-string.js
// — see import at top.

// _layerHasAdjustments lives in editor/layer-helpers.js — see import at top.

// ── Mask sub-layers ──
// Resolves to the parent layer that should own masks for the current
// edit. The "active" parent is whichever layer the user has selected,
// excluding mask-sublayer entries themselves.
function _activeParentLayer() {
  return state.layers.find(l => l.id === state.activeLayerId) || state.layers[state.layers.length - 1] || null;
}

// Find the active mask sub-layer (the one paint/lasso/inpaint ops
// target). Returns null if the parent has no masks OR if no mask is
// currently activated (i.e. the user explicitly selected the parent
// pixels as the paint target). Earlier code fell back to "last mask
// in the list" which meant clicking the parent row couldn't escape
// mask-paint mode — that surprised the user, so the fallback is
// gone.
function _getActiveMaskLayer() {
  const activeGroup = (state.layerGroups || []).find(group => group.id === state.activeGroupId);
  if (activeGroup?.activeMaskId) {
    const groupMask = (activeGroup.masks || []).find(mask => mask.id === activeGroup.activeMaskId);
    if (groupMask) return groupMask;
  }
  const parent = _activeParentLayer();
  if (!parent || !parent.masks || !parent.masks.length) return null;
  if (!parent.activeMaskId) return null;
  return parent.masks.find(m => m.id === parent.activeMaskId) || null;
}

function _getStrokeTargetMask() {
  const mask = _getActiveMaskLayer();
  return state.tool === 'inpaint' && mask?.mode !== 'selection' ? null : mask;
}

// Get-or-create a mask sub-layer on the active parent. Used by tools
// that need a mask to write into (Brush on mask, Inpaint stroke,
// lasso→mask, wand→mask).
function _ensureActiveMaskLayer() {
  const parent = _activeParentLayer();
  if (!parent) return null;
  if (!parent.masks) parent.masks = [];
  let mask = _getActiveMaskLayer();
  if (mask?.mode === 'layer') mask = null;
  if (!mask) {
    mask = parent.masks.find(m => m.mode !== 'layer') || null;
    if (mask) parent.activeMaskId = mask.id;
  }
  if (mask) return mask;
  const c = document.createElement('canvas');
  c.width = state.imgWidth;
  c.height = state.imgHeight;
  mask = {
    id: 'mask-' + (state.nextLayerId++),
    name: 'Mask ' + (parent.masks.length + 1),
    canvas: c,
    ctx: c.getContext('2d'),
    visible: true,
    mode: 'selection',
  };
  parent.masks.push(mask);
  parent.activeMaskId = mask.id;
  return mask;
}

// True if any visible layer in the doc carries a mask sub-layer; drives
// the "red overlay" pass in composite().
function _hasAnyMasks() {
  for (const l of state.layers) {
    if (l.masks && l.masks.length) return true;
  }
  return false;
}

// Union of every VISIBLE mask sub-layer across the whole document,
// returned as a fresh image-sized canvas with white = masked area.
// Used by inpaint Generate/Remove so the AI sees the combined region
// instead of just the active mask. Returns null when no masks exist
// (caller should fall back to the active mask plumbing in that case).
function _buildMergedMaskCanvas() {
  return _buildMergedMaskCanvasImpl(state.layers, state.imgWidth, state.imgHeight);
}

// True if the layer needs the (slower) per-pixel LUT pass — i.e. Levels
// or Color Balance are non-identity. Brightness/Contrast/Saturation/Hue
// alone can stay on the fast CSS-filter path.
// _layerNeedsPixelPass + _adjustmentsKey live in editor/layer-helpers.js.

// Per-pixel Levels + Color Balance. Renders the layer.canvas into a
// cached canvas (layer._adjCache) with the LUT-style transforms applied.
// CSS-filter adjustments (B/C/S/H) are still applied at composite() on
// top of this cache.
// Pixel-pass adjustment math lives in editor/fx/pixel-pass.js. This
// wrapper forwards the layer + a fresh adjustments-cache key so
// existing callers stay unchanged.
function _renderLayerPixelAdjustments(layer) {
  return _renderLayerPixelAdjustmentsImpl(layer, _adjustmentsKey(layer.adjustments));
}
// Layer FX popup — floating window bound to a specific layer. Sliders
// edit that layer's adjustments and live-update composite(). The popup
// stays open across clicks elsewhere unless dismissed via its × button.

// FX / adjustment-popup machinery — full implementation in
// editor/fx/adj-popup.js. Wrappers preserve the legacy names that
// every layer-row FX button, panel-row click, and undo/redo path
// already references.
const _adjPopupSystem = createAdjPopupSystem({
  composite,
  saveState: _saveState,
  renderLayerPanel: () => _renderLayerPanel(),
  getAdjustmentSource: layer => _adjustmentSourceCanvas(layer),
});
const _closeFxPopup                     = _adjPopupSystem.closeFxPopup;
const _ensureAdjustments                = _adjPopupSystem.ensureAdjustments;
const _ensureFxDock                     = _adjPopupSystem.ensureFxDock;
const _closeFxMenu                      = _adjPopupSystem.closeFxMenu;
const _openFxPopup                      = _adjPopupSystem.openFxPopup;
const _openAdjPopup                     = _adjPopupSystem.openAdjPopup;
const _editAdjLayer                     = _adjPopupSystem.editAdjLayer;
const _closeAdjPopup                    = _adjPopupSystem.closeAdjPopup;
const _minimiseAdjPopup                 = _adjPopupSystem.minimiseAdjPopup;
const _syncFxPanelToActiveLayerIfPresent = _adjPopupSystem.syncFxPanelToActiveLayerIfPresent;

function _pickLayerAtEvent(e) {
  const point = _canvasCoords(e, state.mainCanvas);
  if (!point) return null;
  // Walk front-to-back and inspect rendered alpha, so retained text/shapes,
  // masks, and ordinary raster layers share the same hit-test semantics.
  for (let index = state.layers.length - 1; index >= 0; index -= 1) {
    const layer = state.layers[index];
    if (!layer?.visible || layer.kind === 'adjustment' || !layer.canvas) continue;
    const parentGroup = (state.layerGroups || []).find(group => (group.layerIds || []).includes(layer.id));
    if (parentGroup && _groupAncestors(state, parentGroup, { includeSelf: true }).some(group => group.visible === false)) continue;
    const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const x = Math.floor(point.x - offset.x);
    const y = Math.floor(point.y - offset.y);
    if (x < 0 || y < 0 || x >= layer.canvas.width || y >= layer.canvas.height) continue;
    const rendered = _renderLayerOutput(layer);
    if (!rendered || x >= rendered.width || y >= rendered.height) continue;
    const alpha = rendered.getContext('2d', { willReadFrequently: true })
      .getImageData(x, y, 1, 1).data[3];
    if (alpha > 8) return layer;
  }
  return null;
}

function activeLayer() {
  return state.layers.find(l => l.id === state.activeLayerId) || null;
}

function _addAdjustmentLayer(type, anchorEl) {
  const undoLength = state.undoStack.length;
  _saveState(`Add ${_adjLayerLabel(type)} adjustment layer`);
  const layer = createLayer(_adjLayerLabel(type), state.imgWidth, state.imgHeight);
  layer.kind = 'adjustment';
  layer.adjustment = { type, params: _defaultAdjParams(type) };
  layer.locks = { pixels: true, transparency: true, position: true };
  layer._newAdjustmentLayer = true;
  layer._newAdjustmentUndoLength = undoLength;
  const activeIndex = state.layers.findIndex(item => item.id === state.activeLayerId);
  const insertIndex = activeIndex >= 0 ? activeIndex + 1 : state.layers.length;
  state.layers.splice(insertIndex, 0, layer);
  const activeGroup = (state.layerGroups || []).find(group => (group.layerIds || []).includes(state.activeLayerId));
  if (activeGroup) activeGroup.layerIds.push(layer.id);
  state.activeLayerId = layer.id;
  state.selectedLayerIds = [layer.id];
  _renderLayerPanel();
  composite();
  const row = document.querySelector(`.ge-layer-item[data-layer-id="${CSS.escape(layer.id)}"]`);
  _openAdjPopup(layer, type, row || anchorEl, layer.adjustment);
}

function _adjustmentSourceCanvas(layer) {
  const canvas = document.createElement('canvas');
  canvas.width = state.imgWidth;
  canvas.height = state.imgHeight;
  const index = state.layers.findIndex(item => item.id === layer?.id);
  if (index <= 0) return canvas;
  const layers = state.layers.slice(0, index);
  const validIds = new Set(layers.map(item => item.id));
  const view = {
    ...state,
    layers,
    layerGroups: (state.layerGroups || []).map(group => ({
      ...group,
      layerIds: (group.layerIds || []).filter(id => validIds.has(id)),
      masks: [...(group.masks || [])],
    })),
    clippingCompositeCanvas: null,
    groupCompositeCanvases: new Map(),
  };
  const renderLayer = item => _renderLayerOutput(item);
  _drawGroupedLayers(
    canvas.getContext('2d'),
    view,
    renderLayer,
    (target, item, base) => _drawAdjustmentLayer(target, view, item, base, renderLayer),
  );
  return canvas;
}

// Flood-fill enclosed regions of the inpaint mask. After the user
// draws a closed shape (circle, lasso, whatever), the interior is
// alpha=0 surrounded by white mask. We mark all alpha=0 pixels
// reachable from the canvas edges as "outside"; anything still alpha=0
// after that pass is enclosed and gets filled with white.
function _fillEnclosedMaskRegions() {
  if (!state.maskCanvas || !state.maskCtx) return;
  const w = state.maskCanvas.width, h = state.maskCanvas.height;
  if (w * h > 4096 * 4096) return; // safety cap
  const img = state.maskCtx.getImageData(0, 0, w, h);
  const d = img.data;
  // visited bitmap — 0 = unvisited, 1 = reached from edge (outside),
  // 2 = mask (alpha>0). After BFS, alpha=0 pixels with visited[i]=0
  // are enclosed.
  const visited = new Uint8Array(w * h);
  const stack = [];
  // Pre-mark all mask pixels as visited=2 so we don't cross them.
  for (let i = 0, j = 0; i < d.length; i += 4, j++) {
    if (d[i + 3] > 0) visited[j] = 2;
  }
  // Seed flood from every edge pixel that's empty.
  const seed = (x, y) => {
    const k = y * w + x;
    if (visited[k] === 0) { visited[k] = 1; stack.push(k); }
  };
  for (let x = 0; x < w; x++) { seed(x, 0); seed(x, h - 1); }
  for (let y = 0; y < h; y++) { seed(0, y); seed(w - 1, y); }
  // BFS — 4-connected.
  while (stack.length) {
    const k = stack.pop();
    const x = k % w, y = (k - x) / w;
    if (x > 0)     { const n = k - 1; if (visited[n] === 0) { visited[n] = 1; stack.push(n); } }
    if (x < w - 1) { const n = k + 1; if (visited[n] === 0) { visited[n] = 1; stack.push(n); } }
    if (y > 0)     { const n = k - w; if (visited[n] === 0) { visited[n] = 1; stack.push(n); } }
    if (y < h - 1) { const n = k + w; if (visited[n] === 0) { visited[n] = 1; stack.push(n); } }
  }
  // Anything still visited=0 → enclosed empty region. Fill white.
  let filled = false;
  for (let j = 0, i = 0; j < visited.length; j++, i += 4) {
    if (visited[j] === 0) {
      d[i] = 255; d[i + 1] = 255; d[i + 2] = 255; d[i + 3] = 255;
      filled = true;
    }
  }
  if (filled) state.maskCtx.putImageData(img, 0, 0);
}

// True if a layer has no opaque pixels — used to tag the row in the
// layer panel as "(empty)" so the user can tell at a glance which
// layers carry actual content.
// Lightweight loading overlay anchored to the canvas area. Used for
// blocking operations (rotation on big images, etc.) so the user gets
// feedback while the main thread is busy. The actual heavy call should
// be deferred with rAF so the overlay paints before the block.
function _showCanvasLoading(message) {
  if (!state.container) return;
  let overlay = state.container.querySelector('.ge-canvas-loading');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'ge-canvas-loading ge-frosted';
    overlay.innerHTML = `
      <div class="ge-canvas-loading-spinner"></div>
      <div class="ge-canvas-loading-msg"></div>
    `;
    state.container.appendChild(overlay);
  }
  overlay.querySelector('.ge-canvas-loading-msg').textContent = message || 'Working…';
  overlay.style.display = '';
}
function _hideCanvasLoading() {
  const overlay = state.container && state.container.querySelector('.ge-canvas-loading');
  if (overlay) overlay.style.display = 'none';
}

// Cheap "is this mask canvas blank?" check. Used to suffix "(empty)" on
// mask sub-layer rows in the panel so the user can tell at a glance
// which masks have actually been painted on.
// _isMaskCanvasEmpty lives in editor/layer-helpers.js.

// Document-wide rotate / flip — implementations in
// editor/canvas-transforms.js. Wrappers preserve the legacy names that
// the topbar Image menu and shortcuts already wire to.
const _canvasTransforms = createCanvasTransforms({
  saveState: _saveState,
  composite,
  fitZoom: () => _fitZoom(),
  showCanvasLoading: (label) => _showCanvasLoading(label),
  hideCanvasLoading: () => _hideCanvasLoading(),
  renderLayerPanel: () => _renderLayerPanel(),
});
function _rotateAllLayers(deg) { return _canvasTransforms.rotateAll(deg); }
function _flipAllLayers(axis)  { return _canvasTransforms.flipAll(axis); }

// _isLayerEmpty lives in editor/layer-helpers.js.

// ── Composite ──

function _renderLayerOutput(layer, shouldContinue = () => true) {
  return _renderWithLayerMasks(
    _renderEffects(_renderLayerWithAdjLayers(layer), _effectsWithPreview(layer), shouldContinue),
    layer,
    state.layerOffsets.get(layer.id) || { x: 0, y: 0 },
  );
}

const _renderGeneration = _createRenderGeneration();
let _asyncCompositeInFlight = null;
let _asyncCompositeQueued = false;

function _drawDocumentLayers(ctx, shouldContinue = () => true) {
  const renderLayer = layer => _renderLayerOutput(layer, shouldContinue);
  _drawGroupedLayers(
    ctx,
    state,
    renderLayer,
    (target, layer, base) => _drawAdjustmentLayer(target, state, layer, base, renderLayer),
    (base, group, renderGroupContinue) => _renderEffects(base, _effectsWithPreview(group), renderGroupContinue),
    shouldContinue,
  );
}

async function _renderLayerOutputAsync(layer, shouldContinue = () => true) {
  const rendered = await _renderEffectsAsync(_renderLayerWithAdjLayers(layer), _effectsWithPreview(layer), shouldContinue);
  return _renderWithLayerMasks(rendered, layer, state.layerOffsets.get(layer.id) || { x: 0, y: 0 });
}

async function _drawDocumentLayersAsync(ctx, shouldContinue = () => true) {
  const renderLayer = layer => _renderLayerOutputAsync(layer, shouldContinue);
  await _drawGroupedLayersAsync(
    ctx,
    state,
    renderLayer,
    (target, layer, base) => _drawAdjustmentLayerAsync(
      target,
      state,
      layer,
      base,
      item => _renderLayerOutputAsync(item, shouldContinue),
      shouldContinue,
    ),
    (base, group, renderGroupContinue) => _renderEffectsAsync(base, _effectsWithPreview(group), renderGroupContinue),
    shouldContinue,
  );
}

function _hasRetainedEffects() {
  return state.layers.some(layer => (layer.effects && layer.effects.length) || layer._effectPreview)
    || (state.layerGroups || []).some(group => (group.effects && group.effects.length) || group._effectPreview);
}

function _selectionMaskAsDocument({ materializeLasso = false } = {}) {
  if (state.wandMask) {
    const layer = state.layers.find(item => item.id === state.wandLayerId);
    const offset = layer ? (state.layerOffsets.get(layer.id) || { x: 0, y: 0 }) : { x: 0, y: 0 };
    const documentMask = _selectionMaskToDocument(
      state.wandMask,
      state.wandMaskSpace || 'layer',
      offset,
      state.imgWidth,
      state.imgHeight,
    );
    if (materializeLasso && documentMask !== state.wandMask) {
      state.wandMask = documentMask;
      state.wandMaskSpace = 'document';
    }
    return documentMask;
  }
  if (!state.lassoActive && state.lassoPoints.length >= 3) {
    const feather = parseInt(document.getElementById('ge-lasso-feather')?.value || '0', 10);
    const grow = parseInt(document.getElementById('ge-lasso-grow')?.value || '0', 10);
    const documentMask = _buildLassoMask(state.imgWidth, state.imgHeight, 0, 0, feather, grow);
    if (materializeLasso) {
      state.wandMask = documentMask;
      state.wandLayerId = state.activeLayerId;
      state.wandMaskSpace = 'document';
      state.selectionSource = 'lasso';
      state.wandLastSeed = null;
      state.lassoPoints = [];
      state.lassoActive = false;
    }
    return documentMask;
  }
  return null;
}

function _cloneSelectionCanvas(source) {
  if (!source) return null;
  const canvas = document.createElement('canvas');
  canvas.width = source.width;
  canvas.height = source.height;
  canvas.getContext('2d').drawImage(source, 0, 0);
  return canvas;
}

function _activateSelectionCanvas(source, selectionSource = 'saved') {
  if (!source) return false;
  state.wandMask = _cloneSelectionCanvas(source);
  state.wandLayerId = state.activeLayerId;
  state.wandMaskSpace = 'document';
  state.selectionSource = selectionSource;
  state.wandLastSeed = null;
  state.wandMaskVisible = true;
  state.lassoPoints = [];
  state.lassoActive = false;
  composite();
  _syncToolClearIndicators();
  return true;
}

function _deselectSelection({ saveHistory = true, remember = true } = {}) {
  const selection = _selectionMaskAsDocument({ materializeLasso: true });
  if (!selection) return false;
  if (saveHistory) _saveState('Deselect');
  if (remember) state.lastSelection = { canvas: _cloneSelectionCanvas(selection) };
  state.wandMask = null;
  state.wandLayerId = null;
  state.wandMaskSpace = 'layer';
  state.selectionSource = null;
  state.wandLastSeed = null;
  state.lassoPoints = [];
  state.lassoActive = false;
  _invalidateWandCache();
  composite();
  _syncToolClearIndicators();
  return true;
}

function _reselectSelection() {
  if (!state.lastSelection?.canvas) return false;
  _saveState('Reselect');
  return _activateSelectionCanvas(state.lastSelection.canvas, 'reselect');
}

function _selectAllSelection() {
  if (!state.imgWidth || !state.imgHeight) return false;
  _saveState('Select all');
  return _activateSelectionCanvas(_createMarqueeMask(
    state.imgWidth,
    state.imgHeight,
    { x: 0, y: 0, w: state.imgWidth, h: state.imgHeight },
    'rectangle',
  ), 'marquee');
}

function _saveNamedSelection(name) {
  const mask = _selectionMaskAsDocument({ materializeLasso: true });
  if (!mask) return false;
  const cleanName = String(name || '').trim().slice(0, 100) || `Selection ${(state.savedSelections || []).length + 1}`;
  _saveState(`Save selection "${cleanName}"`);
  const existing = (state.savedSelections || []).find(item => item.name.toLowerCase() === cleanName.toLowerCase());
  if (existing) {
    existing.name = cleanName;
    existing.canvas = _cloneSelectionCanvas(mask);
  } else {
    state.savedSelections.push({
      id: `selection-${state.nextSavedSelectionId++}`,
      name: cleanName,
      canvas: _cloneSelectionCanvas(mask),
    });
  }
  _schedulePersist();
  _renderSavedSelectionsMenu?.();
  return true;
}

function _loadNamedSelection(id) {
  const saved = (state.savedSelections || []).find(item => item.id === id);
  if (!saved) return false;
  _saveState(`Load selection "${saved.name}"`);
  return _activateSelectionCanvas(saved.canvas, 'saved');
}

function _deleteNamedSelection(id) {
  const index = (state.savedSelections || []).findIndex(item => item.id === id);
  if (index < 0) return false;
  const saved = state.savedSelections[index];
  _saveState(`Delete selection "${saved.name}"`);
  state.savedSelections.splice(index, 1);
  _schedulePersist();
  _renderSavedSelectionsMenu?.();
  return true;
}

async function _refineSelection() {
  const current = _selectionMaskAsDocument({ materializeLasso: true });
  if (!current) return false;
  const original = _cloneSelectionCanvas(current);
  let preview = original;
  const renderPreview = values => {
    preview = _refineSelectionMask(original, values);
    state.wandMask = preview;
    state.wandMaskSpace = 'document';
    state.wandMaskVisible = true;
    composite();
  };
  const values = await _filterSliderPrompt('Refine Selection', [
    { key: 'feather', label: 'Feather', min: 0, max: 100, step: 1, value: 0, suffix: 'px' },
    { key: 'expand', label: 'Expand / Contract', min: -100, max: 100, step: 1, value: 0, suffix: 'px' },
    { key: 'smooth', label: 'Smooth', min: 0, max: 50, step: 1, value: 0, suffix: 'px' },
    { key: 'border', label: 'Border', min: 0, max: 100, step: 1, value: 0, suffix: 'px' },
  ], renderPreview);
  state.wandMask = original;
  state.wandMaskSpace = 'document';
  if (values === null) {
    composite();
    return false;
  }
  _saveState('Refine selection');
  state.wandMask = _refineSelectionMask(original, values);
  state.wandMaskSpace = 'document';
  state.wandLastSeed = null;
  composite();
  _syncToolClearIndicators();
  return true;
}

function _syncSelectionOverlay() {
  const overlay = state.selectionOverlay;
  if (!overlay || !state.mainCanvas) return;
  if (overlay.width !== state.imgWidth) overlay.width = state.imgWidth;
  if (overlay.height !== state.imgHeight) overlay.height = state.imgHeight;
  overlay.style.width = `${state.imgWidth * state.zoom}px`;
  overlay.style.height = `${state.imgHeight * state.zoom}px`;
  overlay.style.left = `${state.mainCanvas.offsetLeft}px`;
  overlay.style.top = `${state.mainCanvas.offsetTop}px`;
  overlay.style.transform = state.mainCanvas.style.transform || 'none';
}

function _stopSelectionAnimation() {
  if (_selectionAnimationFrame) cancelAnimationFrame(_selectionAnimationFrame);
  _selectionAnimationFrame = null;
  _selectionAnimationLast = 0;
}

function _paintSelectionOverlay() {
  const overlay = state.selectionOverlay;
  const ctx = state.selectionOverlayCtx;
  if (!overlay || !ctx) return;
  _syncSelectionOverlay();
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  const mask = _selectionMaskAsDocument();
  if (!mask || !state.wandMaskVisible) {
    overlay.style.display = 'none';
    return;
  }
  overlay.style.display = '';
  if (state.quickMaskActive) {
    ctx.save();
    ctx.fillStyle = 'rgba(224, 58, 72, 0.48)';
    ctx.fillRect(0, 0, overlay.width, overlay.height);
    ctx.globalCompositeOperation = 'destination-out';
    ctx.drawImage(mask, 0, 0, overlay.width, overlay.height);
    ctx.restore();
    return;
  }
  const cache = _selectionBoundaryCache;
  if (!cache || cache.mask !== mask || cache.width !== mask.width || cache.height !== mask.height) return;
  _paintSelectionBoundary(ctx, cache.pixels, overlay.width, overlay.height, _selectionAnimationPhase);
}

function _ensureSelectionAnimation() {
  if (_selectionAnimationFrame || state.quickMaskActive || !state.wandMaskVisible || !_selectionMaskAsDocument()) return;
  const tick = timestamp => {
    _selectionAnimationFrame = null;
    if (!state.editorOpen || state.quickMaskActive || !state.wandMaskVisible || !_selectionMaskAsDocument()) {
      _stopSelectionAnimation();
      return;
    }
    if (!_selectionAnimationLast || timestamp - _selectionAnimationLast >= 110) {
      _selectionAnimationLast = timestamp;
      _selectionAnimationPhase = (_selectionAnimationPhase + 1) % 8;
      _paintSelectionOverlay();
    }
    _selectionAnimationFrame = requestAnimationFrame(tick);
  };
  _selectionAnimationFrame = requestAnimationFrame(tick);
}

function _refreshSelectionOverlay() {
  const mask = _selectionMaskAsDocument();
  if (!mask || !state.wandMaskVisible) {
    _selectionBoundaryCache = null;
    _stopSelectionAnimation();
    if (state.selectionOverlayCtx && state.selectionOverlay) {
      state.selectionOverlayCtx.clearRect(0, 0, state.selectionOverlay.width, state.selectionOverlay.height);
      state.selectionOverlay.style.display = 'none';
    }
    return;
  }
  _selectionBoundaryCache = {
    mask,
    width: mask.width,
    height: mask.height,
    pixels: _selectionBoundaryPixels(mask),
  };
  _paintSelectionOverlay();
  if (state.quickMaskActive) _stopSelectionAnimation();
  else _ensureSelectionAnimation();
}

let _layerPreviewTimer;
function _finishComposite(render, documentCanvas) {
  if (!render.isCurrent()) return;
  clearTimeout(_layerPreviewTimer);
  _layerPreviewTimer = setTimeout(() => {
    if (render.isCurrent() && !state.drawing) _layerPanelRenderer.refreshPreviews();
  }, 100);
  state.documentRenderReady = true;
  if (!state.compareBaselineCanvas && !state.compareActive && documentCanvas.width && documentCanvas.height) {
    state.compareBaselineCanvas = document.createElement('canvas');
    state.compareBaselineCanvas.width = documentCanvas.width;
    state.compareBaselineCanvas.height = documentCanvas.height;
    state.compareBaselineCanvas.getContext('2d').drawImage(documentCanvas, 0, 0);
  }
  const displayCanvas = state.compareActive && state.compareBaselineCanvas
    ? state.compareBaselineCanvas
    : documentCanvas;
  state.mainCtx.drawImage(displayCanvas, 0, 0);
  if (state.compareActive && state.compareBaselineCanvas) return;
  const inspectedMask = state.maskInspectMode ? _getActiveMaskLayer() : null;
  if (inspectedMask?.canvas) {
    // Render the selected mask in isolation without changing document data.
    // Layer masks use the same document-space placement as the compositor.
    const ctx = state.mainCtx;
    const parent = _activeParentLayer();
    const layerOffset = parent ? (state.layerOffsets.get(parent.id) || { x: 0, y: 0 }) : { x: 0, y: 0 };
    const maskOffset = inspectedMask.mode === 'layer' && inspectedMask.space !== 'document'
      ? {
          x: (Number(layerOffset.x) || 0) + (Number(inspectedMask.offset?.x) || 0),
          y: (Number(layerOffset.y) || 0) + (Number(inspectedMask.offset?.y) || 0),
        }
      : { x: 0, y: 0 };
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#111';
    ctx.fillRect(0, 0, state.mainCanvas.width, state.mainCanvas.height);
    ctx.drawImage(inspectedMask.canvas, maskOffset.x, maskOffset.y);
    ctx.restore();
  }
  // Show mask overlay as red tint whenever a mask sub-layer is present
  // on the active parent (was previously gated on inpaint-tool only; now
  // masks are first-class layer entities, so users see them in any tool).
  // Mask canvas has white pixels — we tint them red for visibility.
  if (state.maskVisible && !inspectedMask) {
    // Build a SINGLE merged mask canvas from every visible mask
    // sub-layer (union of alpha — `lighter` keeps max alpha per pixel,
    // so overlapping strokes don't visually stack). Then tint it red
    // once and composite at the configured opacity. Result: the user
    // sees a flat, consistent translucent red over the masked area
    // regardless of how many strokes / masks contributed to it. Mask
    // visibility is INDEPENDENT of parent visibility — hiding the
    // parent layer doesn't hide its masks.
    let _haveAny = false;
    if (!state.compositeMaskUnion) state.compositeMaskUnion = document.createElement('canvas');
    const union = state.compositeMaskUnion;
    union.width = state.mainCanvas.width;
    union.height = state.mainCanvas.height;
    const uctx = union.getContext('2d');
    uctx.clearRect(0, 0, union.width, union.height);
    uctx.globalCompositeOperation = 'lighter';
    for (const ly of state.layers) {
      if (!ly.masks || !ly.masks.length) continue;
      for (const mk of ly.masks) {
        if (mk.mode === 'layer') continue;
        if (!mk.visible) continue;
        if (!mk.canvas || !mk.canvas.width || !mk.canvas.height) continue;
        uctx.drawImage(mk.canvas, 0, 0);
        _haveAny = true;
      }
    }
    uctx.globalCompositeOperation = 'source-over';
    if (_haveAny) {
      uctx.globalCompositeOperation = 'source-in';
      uctx.fillStyle = state.maskTintColor || 'rgba(255, 50, 50, 1)';
      uctx.fillRect(0, 0, union.width, union.height);
      uctx.globalCompositeOperation = 'source-over';
      state.mainCtx.globalAlpha = state.maskTintOpacity;
      state.mainCtx.drawImage(union, 0, 0);
      state.mainCtx.globalAlpha = 1;
    }
  }
  if (state.transformActive) _drawTransformHandles();
  else if (state.transformOverlay) state.transformOverlay.style.display = 'none';
  if (state.marqueeActive && state.marqueeRect) _drawMarqueeOverlay();
  _penTool.draw();
  if (state.cropRect && !state.cropping) _drawCropOverlay();
  if (state.activeSnapGuides && state.activeSnapGuides.length) _drawSnapGuides();
  _precisionGuides?.drawDocumentOverlay(state.mainCtx);
  _refreshSelectionOverlay();
  _syncToolClearIndicators();
}

function composite() {
  if (!state.mainCtx) return;
  const asyncComposite = _hasRetainedEffects()
    && typeof Worker !== 'undefined'
    && typeof OffscreenCanvas !== 'undefined';
  if (asyncComposite && _asyncCompositeInFlight) {
    // A slider or drag can invalidate many frames before the worker finishes.
    // Keep one latest-generation rerender queued instead of spawning another
    // full-canvas worker for every input event.
    _renderGeneration.invalidate();
    _asyncCompositeQueued = true;
    state.documentRenderReady = false;
    return;
  }
  const render = _renderGeneration.begin();
  state.documentRenderReady = false;
  state.mainCtx.clearRect(0, 0, state.mainCanvas.width, state.mainCanvas.height);
  // Checkerboard background
  _drawCheckerboard(state.mainCtx, state.mainCanvas.width, state.mainCanvas.height);
  if (!state.documentCompositeCanvas) state.documentCompositeCanvas = document.createElement('canvas');
  const documentCanvas = state.documentCompositeCanvas;
  if (documentCanvas.width !== state.imgWidth || documentCanvas.height !== state.imgHeight) {
    documentCanvas.width = state.imgWidth;
    documentCanvas.height = state.imgHeight;
  }
  // Keep the last completed document visible while an async generation is
  // rendering. The new generation replaces it atomically when ready.
  if (asyncComposite && documentCanvas.width && documentCanvas.height) {
    state.mainCtx.drawImage(documentCanvas, 0, 0);
  }
  if (asyncComposite) {
    const renderPromise = _drawDocumentLayersAsync(documentCanvas.getContext('2d'), render.isCurrent)
      .then(() => _finishComposite(render, documentCanvas))
      .catch(() => {
        if (!render.isCurrent()) return;
        _drawDocumentLayers(documentCanvas.getContext('2d'), render.isCurrent);
        _finishComposite(render, documentCanvas);
      });
    const job = {};
    job.promise = renderPromise.then(() => {
      if (_asyncCompositeInFlight !== job) return;
      _asyncCompositeInFlight = null;
      if (_asyncCompositeQueued) {
        _asyncCompositeQueued = false;
        composite();
      }
    }, () => {
      if (_asyncCompositeInFlight !== job) return;
      _asyncCompositeInFlight = null;
      if (_asyncCompositeQueued) {
        _asyncCompositeQueued = false;
        composite();
      }
    });
    _asyncCompositeInFlight = job;
    return;
  }
  _drawDocumentLayers(documentCanvas.getContext('2d'), render.isCurrent);
  _finishComposite(render, documentCanvas);
}

function _toggleCompare() {
  if (!state.compareBaselineCanvas) {
    uiModule?.showToast?.('Compare is available after the document finishes loading');
    return;
  }
  state.compareActive = !state.compareActive;
  const button = document.getElementById('ge-compare-btn');
  if (button) {
    button.setAttribute('aria-pressed', state.compareActive ? 'true' : 'false');
    button.classList.toggle('active', state.compareActive);
    button.title = state.compareActive ? 'Show the current document' : 'Show the document before editing';
  }
  composite();
}

function _drawSnapGuides() {
  const ctx = state.mainCtx;
  ctx.save();
  ctx.strokeStyle = 'rgba(224, 108, 117, 0.85)';
  ctx.lineWidth = 1 / state.zoom;
  ctx.setLineDash([4 / state.zoom, 3 / state.zoom]);
  for (const g of state.activeSnapGuides) {
    ctx.beginPath();
    if (g.vertical) {
      ctx.moveTo(g.x, 0);
      ctx.lineTo(g.x, state.imgHeight);
    } else {
      ctx.moveTo(0, g.y);
      ctx.lineTo(state.imgWidth, g.y);
    }
    ctx.stroke();
  }
  ctx.restore();
}

// Draw the dim-everything-else + cleared-crop-window overlay for the
// current `state.cropRect`. Shared by _continueCrop (live preview during drag)
// and composite() (re-draw after canvas redraws while the crop is held).
function _drawCropOverlay() {
  if (!state.cropRect || !state.mainCtx || !state.mainCanvas) return;
  const { x, y, w, h } = state.cropRect;
  state.mainCtx.fillStyle = 'rgba(0,0,0,0.4)';
  state.mainCtx.fillRect(0, 0, state.mainCanvas.width, state.mainCanvas.height);
  state.mainCtx.clearRect(x, y, w, h);
  state.mainCtx.save();
  state.mainCtx.beginPath();
  state.mainCtx.rect(x, y, w, h);
  state.mainCtx.clip();
  _drawCheckerboard(state.mainCtx, state.mainCanvas.width, state.mainCanvas.height);
  if (state.documentCompositeCanvas) state.mainCtx.drawImage(state.documentCompositeCanvas, 0, 0);
  state.mainCtx.globalAlpha = 1;
  state.mainCtx.restore();
  state.mainCtx.strokeStyle = '#fff';
  state.mainCtx.lineWidth = 1;
  state.mainCtx.setLineDash([4, 4]);
  state.mainCtx.strokeRect(x, y, w, h);
  state.mainCtx.setLineDash([]);
}

// _drawCheckerboard lives in editor/checkerboard.js — see import at top.

// ── History ──

function _snapshotState() {
  let wand = null;
  if (state.wandMask) {
    try {
      const wctx = state.wandMask.getContext('2d');
      wand = {
        layerId: state.wandLayerId,
        space: state.wandMaskSpace || 'layer',
        source: state.selectionSource || null,
        w: state.wandMask.width,
        h: state.wandMask.height,
        imageData: wctx.getImageData(0, 0, state.wandMask.width, state.wandMask.height),
        seed: state.wandLastSeed ? { ...state.wandLastSeed } : null,
      };
    } catch {}
  }
  const snapshot = {
    imgWidth: state.imgWidth,
    imgHeight: state.imgHeight,
    activeLayerId: state.activeLayerId,
    selectedLayerIds: [...(state.selectedLayerIds || [])],
    selectionAnchorId: state.selectionAnchorId || null,
    layerGroups: (state.layerGroups || []).map(group => ({
      id: group.id,
      name: group.name,
      layerIds: [...(group.layerIds || [])],
      parentId: group.parentId || null,
      visible: group.visible !== false,
      opacity: group.opacity,
      blendMode: group.blendMode,
      locked: !!group.locked,
      collapsed: !!group.collapsed,
      activeMaskId: group.activeMaskId || null,
      effects: (group.effects || []).map(effect => _cloneDocumentValue(_normalizeEffect(effect), null)),
      masks: (group.masks || []).map(mask => {
        let imageData = null;
        try { imageData = mask.ctx.getImageData(0, 0, mask.canvas.width, mask.canvas.height); } catch {}
        return {
          id: mask.id,
          name: mask.name,
          visible: mask.visible !== false,
          mode: 'group',
          space: 'document',
          canvasW: mask.canvas.width,
          canvasH: mask.canvas.height,
          imageData,
        };
      }),
    })),
    activeGroupId: state.activeGroupId || null,
    nextLayerId: state.nextLayerId,
    wand,
    savedSelections: (state.savedSelections || []).map(selection => {
      let imageData = null;
      try { imageData = selection.canvas.getContext('2d').getImageData(0, 0, selection.canvas.width, selection.canvas.height); } catch {}
      return { id: selection.id, name: selection.name, w: selection.canvas.width, h: selection.canvas.height, imageData };
    }),
    lastSelection: (() => {
      const canvas = state.lastSelection?.canvas;
      if (!canvas) return null;
      let imageData = null;
      try { imageData = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height); } catch {}
      return { w: canvas.width, h: canvas.height, imageData };
    })(),
    nextSavedSelectionId: state.nextSavedSelectionId,
    lassoPoints: (state.lassoPoints || []).map(point => ({ ...point })),
    lassoActive: !!state.lassoActive,
    guides: {
      vertical: [...(state.guides?.vertical || [])],
      horizontal: [...(state.guides?.horizontal || [])],
    },
    layers: state.layers.map(l => {
      // getImageData throws on a 0-sized canvas — guard so a single
      // broken layer/mask can't take down the whole snapshot (which
      // would silently break undo/redo for brush strokes etc.).
      let imageData = null;
      try {
        if (l.canvas.width > 0 && l.canvas.height > 0) {
          imageData = l.ctx.getImageData(0, 0, l.canvas.width, l.canvas.height);
        }
      } catch (_) { /* keep imageData=null, restore will skip */ }
      let placed = null;
      if (l.kind === 'placed' && l.placed?.sourceCanvas) {
        let sourceImageData = null;
        try {
          sourceImageData = l.placed.sourceCanvas.getContext('2d').getImageData(
            0, 0, l.placed.sourceCanvas.width, l.placed.sourceCanvas.height,
          );
        } catch {}
        placed = {
          sourceW: l.placed.sourceCanvas.width,
          sourceH: l.placed.sourceCanvas.height,
          sourceImageData,
          sourceName: l.placed.sourceName || 'Placed image',
          matrix: Array.isArray(l.placed.matrix) ? [...l.placed.matrix] : [1, 0, 0, 1, 0, 0],
        };
      }
      return {
        id: l.id, name: l.name, visible: l.visible, opacity: l.opacity, locked: l.locked,
        locks: _normalizeLayerLocks(l.locks),
        clipped: !!l.clipped,
        blendMode: l.blendMode || 'source-over',
        kind: l.kind || 'raster',
        text: _cloneDocumentValue(l.text, null),
        shape: _cloneDocumentValue(l.shape, null),
        adjustment: _cloneDocumentValue(l.adjustment, null),
        effects: (l.effects || []).map(effect => {
          const normalized = _normalizeEffect(effect);
          const mask = effect?.mask;
          if (!mask?.canvas) return normalized;
          let imageData = null;
          try { imageData = mask.ctx.getImageData(0, 0, mask.canvas.width, mask.canvas.height); } catch {}
          return {
            ...normalized,
            mask: {
              id: mask.id,
              name: mask.name || 'Effect Mask',
              visible: mask.visible !== false,
              canvasW: mask.canvas.width,
              canvasH: mask.canvas.height,
              imageData,
            },
          };
        }),
        placed,
        canvasW: l.canvas.width,
        canvasH: l.canvas.height,
        imageData,
        offset: { ...(state.layerOffsets.get(l.id) || { x: 0, y: 0 }) },
        adjustments: (() => {
          try { return JSON.parse(JSON.stringify(l.adjustments || {})); }
          catch (e) { console.error('[gallery] adjustments not serializable, dropping from snapshot:', e); return {}; }
        })(),
        // Deep-clone defensively — a non-serializable / circular value here
        // would throw out of the whole snapshot (and historically aborted
        // every mutating op). Fall back to [] rather than blow up.
        adjLayers: (() => {
          try { return l.adjLayers ? JSON.parse(JSON.stringify(l.adjLayers)) : []; }
          catch (e) { console.error('[gallery] adjLayers not serializable, dropping from snapshot:', e); return []; }
        })(),
        masks: (l.masks || []).map(m => {
          let mImageData = null;
          try {
            if (m.canvas.width > 0 && m.canvas.height > 0) {
              mImageData = m.ctx.getImageData(0, 0, m.canvas.width, m.canvas.height);
            }
          } catch (_) {}
          return {
            id: m.id,
            name: m.name,
            visible: m.visible !== false,
            mode: m.mode || 'selection',
            space: m.space || (m.mode === 'layer' ? 'layer' : 'document'),
            linked: m.mode === 'layer' ? m.linked !== false : true,
            offset: { x: Number(m.offset?.x) || 0, y: Number(m.offset?.y) || 0 },
            canvasW: m.canvas.width,
            canvasH: m.canvas.height,
            imageData: mImageData,
          };
        }),
        activeMaskId: l.activeMaskId || null,
        isBase: !!l.isBase,
      };
    }),
  };
  snapshot._bytes = _snapshotByteSize(snapshot);
  return snapshot;
}

function _saveState(label) {
  // saveState() runs FIRST in every mutating op (import, paste, copy,
  // merge, mask, delete, brush, …). If anything here throws, the whole
  // operation aborts before its real work runs — which silently breaks
  // import ("no layer created") and every layer button. So each step is
  // isolated: a history-snapshot failure must degrade (lose one undo
  // step) rather than kill the user's action.
  try {
    const snap = _shareSnapshotPixels(_snapshotState(), state.undoStack.at(-1));
    snap._label = label || 'Edit';
    snap._ts = Date.now();
    state.undoStack.push(snap);
    _trimHistoryStack(state.undoStack);
    state.redoStack = [];
  } catch (e) {
    console.error('[gallery] saveState snapshot failed (continuing without this undo step):', e);
  }
  try { _invalidateWandCache(); } catch (e) { console.error('[gallery] invalidateWandCache:', e); }
  try { _schedulePersist(); } catch (e) { console.error('[gallery] schedulePersist:', e); }
  try { _refreshHistoryPanelIfOpen(); } catch (e) { console.error('[gallery] refreshHistoryPanel:', e); }
}

// ────────── Persistent edit drafts (server-backed) ──────────
// The previous implementation keyed drafts by gallery image-id in
// localStorage; that meant blank-canvas sessions silently lost work and
// drafts couldn't roam between devices. We now hold a server-side draft
// row identified by a uuid (`state.draftId`) and PUT updates to it on a
// debounced timer. `state.imageId` (gallery id) is still tracked separately
// for "save back to the original photo" behaviour.
const PERSIST_DEBOUNCE_MS = 800;
const THUMB_MAX = 160;
const ACTIVE_EDITOR_SESSION_KEY = 'odysseus-gallery-active-editor-v1';

// Keep just enough session metadata to reopen the editor after a browser
// refresh. The actual layered document remains in the server-backed draft.
function _writeActiveEditorSession() {
  if (!state.editorOpen) return;
  try {
    sessionStorage.setItem(ACTIVE_EDITOR_SESSION_KEY, JSON.stringify({
      imageUrl: state.imageUrl || null,
      imageId: state.imageId || null,
      draftId: state.draftId || null,
      draftName: state.draftName || 'Untitled',
      width: Number(state.imgWidth) || 0,
      height: Number(state.imgHeight) || 0,
    }));
  } catch (_) {}
}

function _setDraftStatus(label, stateName = '') {
  const el = document.getElementById('ge-draft-status');
  if (!el) return;
  const normalizedState = stateName === 'saved' ? 'saved' : 'dirty';
  const normalizedLabel = normalizedState === 'saved' ? 'Saved' : 'Unsaved';
  el.textContent = normalizedLabel;
  el.dataset.state = stateName;
  const iconHost = el.closest('#ge-save-menu-btn');
  iconHost?.querySelectorAll('.ge-save-state-icon').forEach(icon => {
    icon.hidden = !icon.classList.contains(`ge-save-state-${normalizedState}`);
  });
  const saveButton = document.getElementById('ge-save-menu-btn');
  if (saveButton) {
    saveButton.title = normalizedState === 'error' ? 'Draft autosave needs attention' : `Draft status: ${normalizedLabel}`;
    saveButton.setAttribute('aria-label', normalizedLabel);
  }
}

function _clearActiveEditorSession() {
  try { sessionStorage.removeItem(ACTIVE_EDITOR_SESSION_KEY); } catch (_) {}
}

function _schedulePersist() {
  if (!state.editorOpen || !state.layers.length) return;
  if (state.persistTimer) clearTimeout(state.persistTimer);
  _setDraftStatus('Saving soon…', 'pending');
  // The first save establishes the recovery anchor. Start it immediately
  // when no server draft exists; subsequent edits can stay debounced.
  const delay = state.draftId ? PERSIST_DEBOUNCE_MS : 0;
  state.persistTimer = setTimeout(() => { state.persistTimer = null; _persistDraft(); }, delay);
}

function _buildDraftPayload() {
  return _serializeEditorDocument(state);
}

function _serializationSnapshot(source, canvasProxy) {
  const groups = (source.layerGroups || []).map(group => ({
    ...group,
    masks: (group.masks || []).map(mask => ({ ...mask, canvas: canvasProxy(mask.canvas) })),
    effects: (group.effects || []).map(effect => ({
      ...effect,
      mask: effect.mask ? { ...effect.mask, canvas: canvasProxy(effect.mask.canvas) } : effect.mask,
    })),
  }));
  const layers = (source.layers || []).map(layer => ({
    ...layer,
    canvas: canvasProxy(layer.canvas),
    masks: (layer.masks || []).map(mask => ({ ...mask, canvas: canvasProxy(mask.canvas) })),
    effects: (layer.effects || []).map(effect => ({
      ...effect,
      mask: effect.mask ? { ...effect.mask, canvas: canvasProxy(effect.mask.canvas) } : effect.mask,
    })),
    placed: layer.placed ? { ...layer.placed, sourceCanvas: canvasProxy(layer.placed.sourceCanvas) } : layer.placed,
  }));
  return {
    ...source,
    savedSelections: (source.savedSelections || []).map(selection => ({
      ...selection,
      canvas: canvasProxy(selection.canvas),
    })),
    layerGroups: groups,
    layers,
  };
}

function _replaceSerializationTokens(value, replacements) {
  if (typeof value === 'string') return replacements.get(value) || value;
  if (Array.isArray(value)) return value.map(item => _replaceSerializationTokens(item, replacements));
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    _replaceSerializationTokens(item, replacements),
  ]));
}

function _readBlobAsDataUrl(blob) {
  return new Promise(resolve => {
    if (!blob) { resolve(null); return; }
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : null);
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(blob);
  });
}

async function _encodeSerializedCanvases(canvases) {
  const fallback = () => canvases.map(canvas => {
    try { return canvas?.toDataURL?.('image/png') || null; } catch { return null; }
  });
  if (!canvases.length || typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined'
      || typeof createImageBitmap !== 'function') return fallback();
  const bitmaps = [];
  try {
    for (const canvas of canvases) bitmaps.push(canvas ? await createImageBitmap(canvas) : null);
  } catch {
    for (const bitmap of bitmaps) bitmap?.close?.();
    return fallback();
  }
  return new Promise(resolve => {
    const worker = new Worker(new URL('./editor/serialization-worker.js', import.meta.url), { type: 'module' });
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      worker.terminate();
      resolve(value);
    };
    worker.onmessage = async event => {
      const { blobs, error } = event.data || {};
      if (error || !Array.isArray(blobs)) { finish(fallback()); return; }
      finish(await Promise.all(blobs.map(_readBlobAsDataUrl)));
    };
    worker.onerror = () => finish(fallback());
    worker.postMessage({ bitmaps }, bitmaps.filter(Boolean));
  });
}

async function _buildDraftPayloadAsync() {
  const canvases = [];
  const tokens = new Map();
  const canvasProxy = canvas => {
    if (!canvas || typeof canvas.toDataURL !== 'function') return canvas;
    let index = canvases.indexOf(canvas);
    if (index < 0) {
      index = canvases.length;
      canvases.push(canvas);
    }
    const token = `data:image/png;base64,ODYSSEUS_CANVAS_TOKEN_${index}`;
    tokens.set(canvas, token);
    return { width: canvas.width, height: canvas.height, toDataURL: () => token };
  };
  const snapshot = _serializationSnapshot(state, canvasProxy);
  const payload = _serializeEditorDocument(snapshot);
  const encoded = await _encodeSerializedCanvases(canvases);
  const replacements = new Map(encoded.map((value, index) => [
    tokens.get(canvases[index]), value || tokens.get(canvases[index]),
  ]));
  return _replaceSerializationTokens(payload, replacements);
}

function _buildThumbnail() {
  return _buildThumbnailFromSource(
    state.documentRenderReady && state.documentCompositeCanvas
      ? state.documentCompositeCanvas
      : flatten(),
  );
}

function _buildThumbnailFromSource(source) {
  try {
    const scale = Math.min(1, THUMB_MAX / Math.max(source.width, source.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(source.width * scale));
    canvas.height = Math.max(1, Math.round(source.height * scale));
    canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.6);
  } catch {
    return null;
  }
}

async function _buildThumbnailAsync() {
  let source;
  try {
    source = state.documentRenderReady && state.documentCompositeCanvas
      ? state.documentCompositeCanvas
      : flatten();
  } catch {
    return null;
  }
  if (!source || typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined'
      || typeof createImageBitmap !== 'function') {
    return _buildThumbnailFromSource(source);
  }
  let bitmap;
  try {
    bitmap = await createImageBitmap(source);
  } catch {
    return _buildThumbnailFromSource(source);
  }
  return new Promise(resolve => {
    const worker = new Worker(new URL('./editor/thumbnail-worker.js', import.meta.url), { type: 'module' });
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      worker.terminate();
      resolve(value);
    };
    worker.onmessage = event => {
      const { blob, error } = event.data || {};
      if (error || !blob) {
        finish(_buildThumbnailFromSource(source));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => finish(typeof reader.result === 'string' ? reader.result : null);
      reader.onerror = () => finish(_buildThumbnailFromSource(source));
      reader.readAsDataURL(blob);
    };
    worker.onerror = () => finish(_buildThumbnailFromSource(source));
    worker.postMessage({ bitmap, maxDim: THUMB_MAX, quality: 0.6 }, [bitmap]);
  });
}

async function _persistDraft() {
  if (!state.editorOpen || !state.layers.length) return;
  // Coalesce concurrent saves — if one's already in-flight, mark dirty
  // and let the running call kick off another when it returns.
  if (state.persistInFlight) { state.persistDirty = true; _setDraftStatus('Saving…', 'saving'); return; }
  const sessionToken = state.editorSessionToken;
  const draftIdAtStart = state.draftId || null;
  const draftNameAtStart = state.draftName || 'Untitled';
  const sourceImageIdAtStart = state.imageId || null;
  const widthAtStart = state.imgWidth;
  const heightAtStart = state.imgHeight;
  const isCurrentSession = () => state.editorOpen && state.editorSessionToken === sessionToken;
  _setDraftStatus('Saving…', 'saving');
  // Reserve the in-flight slot before thumbnail encoding yields. This keeps
  // a second edit from starting a competing save while the worker is busy.
  const doSave = async () => {
    // Start both captures before either one yields. Close-time cleanup can
    // clear editor state while workers are active, so each operation must
    // retain its own source snapshot rather than reading state sequentially.
    const payloadPromise = _buildDraftPayloadAsync();
    const thumbnailPromise = _buildThumbnailAsync();
    const [payload, thumbnail] = await Promise.all([payloadPromise, thumbnailPromise]);
    const body = {
      name: draftNameAtStart,
      source_image_id: sourceImageIdAtStart,
      width: widthAtStart,
      height: heightAtStart,
      payload,
      thumbnail,
    };
    const serializedBody = JSON.stringify(body);
    if (serializedBody.length > _EDITOR_PROJECT_MAX_BYTES) {
      const message = 'Draft autosave paused: this layered document exceeds the 256 MB safety limit. Export or flatten a copy before closing.';
      if (isCurrentSession()) {
        if (state.persistErrorMessage !== message && uiModule) uiModule.showToast(message, 9000);
        state.persistErrorMessage = message;
        _setDraftStatus('Save paused', 'error');
      }
      return;
    }
    const responseError = async (res, method) => {
      let detail = '';
      try { detail = (await res.json())?.detail || ''; } catch {}
      const error = new Error(detail || `${method} failed: ${res.status}`);
      error.status = res.status;
      return error;
    };
    const doRequest = async () => {
      if (draftIdAtStart) {
        const res = await fetch(`/api/editor-drafts/${encodeURIComponent(draftIdAtStart)}`, {
          method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: serializedBody,
        });
        // 404 means our row was deleted while editing — fall through to
        // create a fresh one so the user doesn't lose work.
        if (res.status === 404) {
          return fetch('/api/editor-drafts', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: serializedBody,
          }).then(async created => {
            if (!created.ok) throw await responseError(created, 'POST');
            return created.json();
          });
        }
        if (!res.ok) throw await responseError(res, 'PUT');
        return res.json();
      }
      const res = await fetch('/api/editor-drafts', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: serializedBody,
      });
      if (!res.ok) throw await responseError(res, 'POST');
      return res.json();
    };
    return doRequest();
  };
  const savePromise = doSave();
  state.persistInFlight = savePromise
    .catch((e) => {
      console.warn('[ge] draft save failed', e);
      if (!isCurrentSession()) return;
      const message = e?.status === 413
        ? 'Draft autosave paused: the document exceeds the configured storage limit.'
        : `Draft autosave failed: ${e?.message || 'server error'}`;
      if (state.persistErrorMessage !== message && uiModule) uiModule.showToast(message, 7000);
      state.persistErrorMessage = message;
      _setDraftStatus('Save failed', 'error');
    })
    .then((out) => {
      if (isCurrentSession()) state.persistInFlight = null;
      if (!isCurrentSession()) return;
      if (out && out.id) {
        state.draftId = out.id;
        _writeActiveEditorSession();
      }
      state.persistErrorMessage = null;
      if (!state.persistErrorMessage) _setDraftStatus('Saved', 'saved');
      if (state.persistDirty) {
        state.persistDirty = false;
        _schedulePersist();
      }
    });
  return state.persistInFlight;
}

async function _loadDraftById(draftId) {
  if (!draftId) return null;
  try {
    const res = await fetch(`/api/editor-drafts/${encodeURIComponent(draftId)}`, {
      credentials: 'same-origin',
    });
    if (!res.ok) return null;
    const out = await res.json();
    if (!out || !out.payload || !Array.isArray(out.payload.layers)) return null;
    return out;
  } catch (_) {
    return null;
  }
}

async function _findDraftForImage(imageId) {
  if (!imageId) return null;
  try {
    const res = await fetch('/api/editor-drafts', { credentials: 'same-origin' });
    if (!res.ok) return null;
    const out = await res.json();
    const match = (out.drafts || []).find(d => d.source_image_id === imageId);
    if (!match) return null;
    return _loadDraftById(match.id);
  } catch (_) {
    return null;
  }
}

async function _clearDraftServer(draftId) {
  if (!draftId) return;
  try {
    await fetch(`/api/editor-drafts/${encodeURIComponent(draftId)}`, {
      method: 'DELETE', credentials: 'same-origin',
    });
  } catch (_) { /* best-effort */ }
}

// Hydrate state.layers from a previously-persisted draft. Accepts either the
// raw payload (v1 localStorage shape) or a server response with
// {payload: {...}}. Returns a promise that resolves once every layer's
// dataURL has decoded into its canvas.
async function _restoreDraft(draft) {
  // Server response: {id, name, payload:{...}, ...}. Unwrap.
  const rawData = draft.payload && typeof draft.payload === 'object' ? draft.payload : draft;
  const prepared = _prepareEditorDocument(rawData);
  const data = prepared.document;
  const restoreWarnings = [...prepared.warnings];
  const records = Array.isArray(data.layers) ? data.layers : [];
  _initCanvasFromDims(data.imgWidth, data.imgHeight);
  state.layers = [];
  state.layerOffsets.clear();
  Object.assign(state, _normalizeEditorView(data.view, {
    rulersVisible: state.rulersVisible,
    gridVisible: state.gridVisible,
    gridSize: state.gridSize,
    snapEnabled: state.snapEnabled,
    snapToGrid: state.snapToGrid,
    guides: { vertical: [], horizontal: [] },
  }));

  const decodeInto = (dataUrl, canvas, ctx, failure) => new Promise(resolve => {
    if (!dataUrl) { resolve(null); return; }
    const img = new Image();
    img.onload = () => {
      try {
        if (!state.editorOpen) { resolve(null); return; }
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0);
        resolve(null);
      } catch {
        resolve(failure);
      }
    };
    img.onerror = () => resolve(failure);
    img.src = dataUrl;
  });

  const decodes = [];
  const missingPlacedPreviewIds = new Set();
  for (const s of records) {
    const layer = createLayer(s.name || 'Layer', s.canvasW || state.imgWidth, s.canvasH || state.imgHeight);
    const generatedId = layer.id;
    layer.id = s.id || generatedId;
    if (generatedId !== layer.id) state.layerOffsets.delete(generatedId);
    layer.visible = s.visible !== false;
    layer.opacity = typeof s.opacity === 'number' ? s.opacity : 1;
    layer.locked = !!s.locked;
    layer.locks = _normalizeLayerLocks(s.locks);
    layer.clipped = !!s.clipped;
    layer.isBase = !!s.isBase;
    layer.blendMode = s.blendMode || 'source-over';
    layer.kind = s.kind || 'raster';
    layer.text = layer.kind === 'text' ? _normalizeTextData(s.text) : null;
    layer.shape = layer.kind === 'shape' ? _normalizeShapeData(s.shape) : null;
    layer.adjustment = layer.kind === 'adjustment' ? _normalizeAdjustmentData(s.adjustment) : null;
    layer.effects = Array.isArray(s.effects) ? s.effects.map(effect => {
      const normalized = _normalizeEffect(effect);
      const savedMask = effect?.mask;
      if (!savedMask?.imageData) return normalized;
      const canvas = document.createElement('canvas');
      canvas.width = savedMask.canvasW || layer.canvas.width;
      canvas.height = savedMask.canvasH || layer.canvas.height;
      const ctx = canvas.getContext('2d');
      try { ctx.putImageData(savedMask.imageData, 0, 0); } catch {}
      normalized.mask = {
        id: savedMask.id || `effect-mask-${state.nextLayerId++}`,
        name: savedMask.name || 'Effect Mask',
        visible: savedMask.visible !== false,
        canvas,
        ctx,
        canvasW: canvas.width,
        canvasH: canvas.height,
      };
      return normalized;
    }) : [];
    for (const effect of layer.effects) {
      const savedMask = effect.mask;
      if (!savedMask?.dataUrl || savedMask.canvas) continue;
      const canvas = document.createElement('canvas');
      canvas.width = savedMask.canvasW || layer.canvas.width;
      canvas.height = savedMask.canvasH || layer.canvas.height;
      const ctx = canvas.getContext('2d');
      effect.mask = {
        id: savedMask.id || `effect-mask-${state.nextLayerId++}`,
        name: savedMask.name || 'Effect Mask',
        visible: savedMask.visible !== false,
        canvas,
        ctx,
        canvasW: canvas.width,
        canvasH: canvas.height,
      };
      decodes.push(decodeInto(savedMask.dataUrl, canvas, ctx, {
        type: 'effect-mask', layerId: layer.id, id: effect.mask.id,
        message: `${layer.name} / ${effect.name || 'Effect'} mask was skipped because its image could not be decoded.`,
      }));
    }
    layer.placed = null;
    if (layer.kind === 'placed' && s.placed) {
      const sourceCanvas = document.createElement('canvas');
      sourceCanvas.width = s.placed.sourceWidth;
      sourceCanvas.height = s.placed.sourceHeight;
      layer.placed = {
        sourceCanvas,
        sourceWidth: s.placed.sourceWidth,
        sourceHeight: s.placed.sourceHeight,
        sourceName: s.placed.sourceName || 'Placed image',
        matrix: [...s.placed.matrix],
      };
      decodes.push(decodeInto(
        s.placed.sourceDataUrl,
        sourceCanvas,
        sourceCanvas.getContext('2d'),
        {
          type: 'placed-source', id: layer.id,
          message: `${layer.name} had a corrupt placed source and was recovered from its preview.`,
        },
      ));
    }
    layer.adjustments = _cloneDocumentValue(s.adjustments, layer.adjustments);
    layer.adjLayers = _cloneDocumentValue(s.adjLayers, []);
    layer.masks = [];
    for (const ms of Array.isArray(s.masks) ? s.masks : []) {
      const canvas = document.createElement('canvas');
      canvas.width = ms.canvasW || state.imgWidth;
      canvas.height = ms.canvasH || state.imgHeight;
      const ctx = canvas.getContext('2d');
      const mask = {
        id: ms.id || `mask-${state.nextLayerId++}`,
        name: ms.name || 'Mask',
        visible: ms.visible !== false,
        mode: ms.mode || 'selection',
        space: ms.space || (ms.mode === 'layer' ? 'layer' : 'document'),
        linked: ms.mode === 'layer' ? ms.linked !== false : true,
        offset: { x: Number(ms.offset?.x) || 0, y: Number(ms.offset?.y) || 0 },
        canvas,
        ctx,
      };
      layer.masks.push(mask);
      decodes.push(decodeInto(ms.dataUrl, canvas, ctx, {
        type: 'mask', layerId: layer.id, id: mask.id,
        message: `${layer.name} / ${mask.name} was skipped because its image could not be decoded.`,
      }));
    }
    layer.activeMaskId = s.activeMaskId && layer.masks.some(m => m.id === s.activeMaskId)
      ? s.activeMaskId
      : null;
    state.layers.push(layer);
    state.layerOffsets.set(layer.id, { ...(s.offset || { x: 0, y: 0 }) });
    if (layer.kind === 'placed' && !s.dataUrl) missingPlacedPreviewIds.add(layer.id);
    decodes.push(decodeInto(s.dataUrl, layer.canvas, layer.ctx, {
      type: ['text', 'shape'].includes(layer.kind) ? `${layer.kind}-preview` : (layer.kind === 'placed' ? 'placed-preview' : 'layer'), id: layer.id,
      message: ['text', 'shape'].includes(layer.kind)
        ? `${layer.name} preview was rebuilt from its editable ${layer.kind} data.`
        : `${layer.name} was skipped because its image could not be decoded.`,
    }));
  }

  const restoredGroups = (data.groups || []).map(source => {
    const group = {
      ...source,
      effects: Array.isArray(source.effects) ? source.effects.map(effect => _normalizeEffect(effect)) : [],
      masks: [],
    };
    for (const savedMask of source.masks || []) {
      const canvas = document.createElement('canvas');
      canvas.width = savedMask.canvasW || state.imgWidth;
      canvas.height = savedMask.canvasH || state.imgHeight;
      const ctx = canvas.getContext('2d');
      const mask = {
        id: savedMask.id || `mask-${state.nextLayerId++}`,
        name: savedMask.name || 'Group Mask',
        visible: savedMask.visible !== false,
        mode: 'group',
        space: 'document',
        canvas,
        ctx,
      };
      group.masks.push(mask);
      decodes.push(decodeInto(savedMask.dataUrl, canvas, ctx, {
        type: 'group-mask', groupId: group.id, id: mask.id,
        message: `${group.name} / ${mask.name} was skipped because its image could not be decoded.`,
      }));
    }
    group.activeMaskId = group.masks.some(mask => mask.id === source.activeMaskId) ? source.activeMaskId : null;
    return group;
  });

  state.savedSelections = (data.savedSelections || []).map(source => {
    const canvas = document.createElement('canvas');
    canvas.width = source.canvasW || state.imgWidth;
    canvas.height = source.canvasH || state.imgHeight;
    const selection = { id: source.id, name: source.name || 'Selection', canvas };
    decodes.push(decodeInto(source.dataUrl, canvas, canvas.getContext('2d'), {
      type: 'saved-selection', id: source.id,
      message: `${selection.name} was skipped because its saved selection could not be decoded.`,
    }));
    return selection;
  });
  state.lastSelection = null;
  state.nextSavedSelectionId = Math.max(1, ...state.savedSelections.map(selection => {
    const match = String(selection.id || '').match(/(\d+)$/);
    return match ? Number(match[1]) + 1 : 1;
  }));

  const decodeFailures = (await Promise.all(decodes)).filter(Boolean);
  const failedLayerIds = new Set(decodeFailures.filter(item => item.type === 'layer').map(item => item.id));
  const failedPlacedSourceIds = new Set(decodeFailures.filter(item => item.type === 'placed-source').map(item => item.id));
  const failedPlacedPreviewIds = new Set(decodeFailures.filter(item => item.type === 'placed-preview').map(item => item.id));
  for (const layer of state.layers) {
    if (layer.kind !== 'placed') continue;
    if (failedPlacedSourceIds.has(layer.id)) {
      layer.kind = 'raster';
      layer.placed = null;
      if (failedPlacedPreviewIds.has(layer.id) || missingPlacedPreviewIds.has(layer.id)) failedLayerIds.add(layer.id);
      continue;
    }
    // The serialized layer canvas is the exact visible result at save time.
    // Do not redraw it from the immutable source during restore: another
    // interpolation pass can change pixels across browsers. Rebuild only
    // when an older/incomplete project has no rendered preview.
    if (!missingPlacedPreviewIds.has(layer.id)) continue;
    const rendered = _renderPlacedLayer(layer);
    if (rendered) state.layerOffsets.set(layer.id, rendered.offset);
  }
  const failedMaskIds = new Set(decodeFailures.filter(item => item.type === 'mask').map(item => item.id));
  if (failedLayerIds.size) {
    state.layers = state.layers.filter(layer => {
      if (!failedLayerIds.has(layer.id)) return true;
      state.layerOffsets.delete(layer.id);
      return false;
    });
  }
  for (const layer of state.layers) {
    if (failedMaskIds.size) layer.masks = (layer.masks || []).filter(mask => !failedMaskIds.has(mask.id));
    if (!layer.masks.some(mask => mask.id === layer.activeMaskId)) layer.activeMaskId = null;
  }
  const failedGroupMaskIds = new Set(decodeFailures.filter(item => item.type === 'group-mask').map(item => item.id));
  for (const group of restoredGroups) {
    if (failedGroupMaskIds.size) group.masks = group.masks.filter(mask => !failedGroupMaskIds.has(mask.id));
    if (!group.masks.some(mask => mask.id === group.activeMaskId)) group.activeMaskId = null;
  }
  const failedSelectionIds = new Set(decodeFailures.filter(item => item.type === 'saved-selection').map(item => item.id));
  if (failedSelectionIds.size) {
    state.savedSelections = state.savedSelections.filter(selection => !failedSelectionIds.has(selection.id));
  }
  restoreWarnings.push(...decodeFailures.map(item => item.message));
  if (!state.layers.length) throw new Error('No recoverable layers could be decoded from this project.');

  state.nextLayerId = _nextLayerIdFromDocument(data);
  state.activeLayerId = data.activeLayerId && state.layers.some(l => l.id === data.activeLayerId)
    ? data.activeLayerId
    : state.layers[state.layers.length - 1].id;
  state.layerGroups = restoredGroups;
  state.activeGroupId = null;
  state.selectedLayerIds = [state.activeLayerId];
  state.selectionAnchorId = state.activeLayerId;
  _normalizeLayerGroups(state);
  _normalizeLayerClipping(state);
  const activeMask = _getActiveMaskLayer();
  state.maskCanvas = activeMask?.canvas || null;
  state.maskCtx = activeMask?.ctx || null;
  for (const layer of state.layers) {
    if (layer.kind === 'text' && layer.text) _renderTextLayer(layer);
    if (layer.kind === 'shape' && layer.shape) _renderShapeLayer(layer);
  }
  _precisionGuides?.syncVisibility();
  _renderSavedSelectionsMenu?.();
  return { warnings: restoreWarnings, migratedFrom: prepared.migratedFrom };
}

function _showDocumentRestoreReport(report) {
  const warnings = Array.isArray(report?.warnings) ? report.warnings.filter(Boolean) : [];
  if (!warnings.length || !uiModule) return;
  const preview = warnings.slice(0, 2).join(' ');
  const remaining = warnings.length > 2 ? ` (+${warnings.length - 2} more)` : '';
  uiModule.showToast(`Opened with ${warnings.length} recovery warning${warnings.length === 1 ? '' : 's'}: ${preview}${remaining}`, 9000);
}

// Used both by the fresh openEditor path and by _restoreDraft. The full
// _initCanvas in openEditor is closure-scoped, so factored out here.
function _initCanvasFromDims(w, h) {
  state.imgWidth = w;
  state.imgHeight = h;
  if (state.mainCanvas) {
    state.mainCanvas.width = w;
    state.mainCanvas.height = h;
  }
  state.maskCanvas = document.createElement('canvas');
  state.maskCanvas.width = w;
  state.maskCanvas.height = h;
  state.maskCtx = state.maskCanvas.getContext('2d');
}

function _restoreState(snap) {
  // Restore canvas dimensions first so layer imageData fits cleanly. This
  // is what makes Ctrl+Z work for crops (which change the main canvas
  // size) in addition to paint strokes.
  const dimsChanged = snap.imgWidth && snap.imgHeight &&
    (snap.imgWidth !== state.imgWidth || snap.imgHeight !== state.imgHeight);
  if (snap.imgWidth && snap.imgHeight) {
    state.imgWidth = snap.imgWidth;
    state.imgHeight = snap.imgHeight;
    if (state.mainCanvas) {
      state.mainCanvas.width = snap.imgWidth;
      state.mainCanvas.height = snap.imgHeight;
    }
    if (state.maskCanvas) {
      state.maskCanvas.width = snap.imgWidth;
      state.maskCanvas.height = snap.imgHeight;
    }
  }
  const layerStates = snap.layers || snap;
  // Rebuild the state.layers array from the snapshot order. This lets Ctrl+Z
  // restore deleted layers (previously the loop only updated existing
  // ones and silently dropped any layer the snapshot still knew about).
  // Layers absent from the snapshot are dropped — that's the desired
  // behavior for undoing an "+Add" or a paste.
  const _existingById = new Map(state.layers.map(l => [l.id, l]));
  const _rebuilt = [];
  for (const s of layerStates) {
    let layer = _existingById.get(s.id);
    if (!layer) {
      // Layer was deleted (or merged away). Recreate it from the
      // snapshot's ImageData so Ctrl+Z brings it back.
      const c = document.createElement('canvas');
      c.width = s.canvasW || state.imgWidth;
      c.height = s.canvasH || state.imgHeight;
      layer = { id: s.id, name: s.name, canvas: c, ctx: c.getContext('2d'),
                visible: true, opacity: 1, locked: false,
                locks: { pixels: false, transparency: false, position: false } };
    } else {
      _existingById.delete(s.id);
    }
    layer.name = s.name;
    layer.visible = s.visible;
    layer.opacity = s.opacity;
    layer.locked = s.locked;
    layer.locks = _normalizeLayerLocks(s.locks);
    layer.clipped = !!s.clipped;
    layer.blendMode = s.blendMode || 'source-over';
    layer.kind = s.kind || 'raster';
    layer.text = layer.kind === 'text' ? _normalizeTextData(s.text) : null;
    layer.shape = layer.kind === 'shape' ? _normalizeShapeData(s.shape) : null;
    layer.adjustment = layer.kind === 'adjustment' ? _normalizeAdjustmentData(s.adjustment) : null;
    layer.placed = null;
    layer.adjustments = s.adjustments
      ? JSON.parse(JSON.stringify(s.adjustments))
      : layer.adjustments;
    if (s.canvasW && s.canvasH) {
      layer.canvas.width = s.canvasW;
      layer.canvas.height = s.canvasH;
    }
    try { if (s.imageData) layer.ctx.putImageData(s.imageData, 0, 0); } catch (_) {}
    state.layerOffsets.set(layer.id, { ...s.offset });
    if (layer.kind === 'placed' && s.placed?.sourceImageData) {
      const sourceCanvas = document.createElement('canvas');
      sourceCanvas.width = s.placed.sourceW;
      sourceCanvas.height = s.placed.sourceH;
      sourceCanvas.getContext('2d').putImageData(s.placed.sourceImageData, 0, 0);
      layer.placed = _createPlacedData(sourceCanvas, s.placed.matrix, s.placed.sourceName);
      const rendered = _renderPlacedLayer(layer);
      if (rendered) state.layerOffsets.set(layer.id, rendered.offset);
    } else if (layer.kind === 'placed') {
      // Old history entries have only a preview. Keep their visible pixels
      // rather than leaving a broken placed-layer shell.
      layer.kind = 'raster';
    }
    // Restore adjustment sub-layers + invalidate the composite cache
    // so the live render reflects the rolled-back FX state.
    layer.adjLayers = s.adjLayers ? JSON.parse(JSON.stringify(s.adjLayers)) : [];
    if (s.isBase !== undefined) layer.isBase = s.isBase;
    // Restore mask sub-layers — rebuild each mask's canvas from the
    // snapshot's imageData. We don't reuse old mask canvases (snapshot
    // dims might differ after a transform) so a fresh canvas is safer.
    layer.masks = (s.masks || []).map(ms => {
      const mc = document.createElement('canvas');
      mc.width = ms.canvasW || state.imgWidth;
      mc.height = ms.canvasH || state.imgHeight;
      const mctx = mc.getContext('2d');
      try { if (ms.imageData) mctx.putImageData(ms.imageData, 0, 0); } catch {}
      return {
        id: ms.id, name: ms.name, canvas: mc, ctx: mctx,
        visible: ms.visible !== false,
        mode: ms.mode || 'selection',
        space: ms.space || (ms.mode === 'layer' ? 'layer' : 'document'),
        linked: ms.mode === 'layer' ? ms.linked !== false : true,
        offset: { x: Number(ms.offset?.x) || 0, y: Number(ms.offset?.y) || 0 },
      };
    });
    layer.activeMaskId = s.activeMaskId || (layer.masks[0]?.id ?? null);
    layer._adjFinal = null;
    layer._adjFinalKey = null;
    layer._stagedAdj = null;
    layer._editingAdjId = null;
    _rebuilt.push(layer);
  }
  // Drop any layer that's no longer in the snapshot.
  for (const lost of _existingById.values()) state.layerOffsets.delete(lost.id);
  state.layers = _rebuilt;
  state.nextLayerId = Number.isFinite(snap.nextLayerId) ? snap.nextLayerId : state.nextLayerId;
  state.activeLayerId = snap.activeLayerId || state.activeLayerId;
  state.selectedLayerIds = (snap.selectedLayerIds || [state.activeLayerId]).filter(id =>
    state.layers.some(layer => layer.id === id)
  );
  state.selectionAnchorId = snap.selectionAnchorId || state.activeLayerId;
  state.layerGroups = (snap.layerGroups || []).map(group => ({
    ...group,
    effects: Array.isArray(group.effects) ? group.effects.map(effect => _normalizeEffect(effect)) : [],
    masks: (group.masks || []).map(mask => {
      const canvas = document.createElement('canvas');
      canvas.width = mask.canvasW || state.imgWidth;
      canvas.height = mask.canvasH || state.imgHeight;
      const ctx = canvas.getContext('2d');
      try { if (mask.imageData) ctx.putImageData(mask.imageData, 0, 0); } catch {}
      return { ...mask, mode: 'group', space: 'document', canvas, ctx };
    }),
  }));
  state.activeGroupId = snap.activeGroupId || null;
  _normalizeLayerGroups(state);
  _normalizeLayerClipping(state);
  state.lassoPoints = (snap.lassoPoints || []).map(point => ({ ...point }));
  state.lassoActive = !!snap.lassoActive;
  state.guides = {
    vertical: [...(snap.guides?.vertical || [])],
    horizontal: [...(snap.guides?.horizontal || [])],
  };
  if (!state.layers.find(l => l.id === state.activeLayerId) && state.layers.length) {
    state.activeLayerId = state.layers[state.layers.length - 1].id;
  }
  // Repoint the global mask plumbing at the active parent's active
  // mask sub-layer (if any) — undo can swap the actual canvas object.
  {
    const m = _getActiveMaskLayer();
    if (m) { state.maskCanvas = m.canvas; state.maskCtx = m.ctx; }
    else { state.maskCanvas = null; state.maskCtx = null; }
  }
  // Restore wand selection (or clear it if the snapshot had none).
  if (snap.wand && snap.wand.imageData) {
    const mc = document.createElement('canvas');
    mc.width = snap.wand.w;
    mc.height = snap.wand.h;
    mc.getContext('2d').putImageData(snap.wand.imageData, 0, 0);
    state.wandMask = mc;
    state.wandLayerId = snap.wand.layerId;
    state.wandMaskSpace = snap.wand.space || 'layer';
    state.selectionSource = snap.wand.source || null;
    state.wandLastSeed = snap.wand.seed ? { ...snap.wand.seed } : null;
  } else {
    state.wandMask = null;
    state.wandLayerId = null;
    state.wandMaskSpace = 'layer';
    state.selectionSource = null;
    state.wandLastSeed = null;
  }
  state.savedSelections = (snap.savedSelections || []).map(saved => {
    const canvas = document.createElement('canvas');
    canvas.width = saved.w || state.imgWidth;
    canvas.height = saved.h || state.imgHeight;
    try { if (saved.imageData) canvas.getContext('2d').putImageData(saved.imageData, 0, 0); } catch {}
    return { id: saved.id, name: saved.name, canvas };
  });
  if (snap.lastSelection?.imageData) {
    const canvas = document.createElement('canvas');
    canvas.width = snap.lastSelection.w || state.imgWidth;
    canvas.height = snap.lastSelection.h || state.imgHeight;
    canvas.getContext('2d').putImageData(snap.lastSelection.imageData, 0, 0);
    state.lastSelection = { canvas };
  } else state.lastSelection = null;
  state.nextSavedSelectionId = Number.isFinite(snap.nextSavedSelectionId) ? snap.nextSavedSelectionId : 1;
  _renderSavedSelectionsMenu?.();
  composite();
  _precisionGuides?.redrawRulers();
  _renderLayerPanel();
  _syncToolClearIndicators();
  // Refit the viewport when canvas size changed (crop undo/redo) so the
  // user sees the full restored image, not the zoomed-in upper-left
  // corner left over from the previous fit.
  if (dimsChanged) _fitZoom();
  // Update the topbar canvas-size badge directly (the helper is scoped
  // inside _buildEditor, so we touch the DOM here).
  const sizeLabel = document.getElementById('ge-canvas-size');
  if (sizeLabel) sizeLabel.textContent = `${state.imgWidth}×${state.imgHeight}`;
}

function undo() {
  if (state.undoStack.length === 0) return;
  const target = state.undoStack.pop();
  const cur = _snapshotState();
  _shareSnapshotPixels(cur, target);
  cur._label = target._label || 'Edit';
  cur._ts = Date.now();
  state.redoStack.push(cur);
  _trimHistoryStack(state.redoStack);
  _restoreState(target);
  _schedulePersist();
  _refreshHistoryPanelIfOpen();
}

function redo() {
  if (state.redoStack.length === 0) return;
  const target = state.redoStack.pop();
  const cur = _snapshotState();
  _shareSnapshotPixels(cur, target);
  cur._label = target._label || 'Edit';
  cur._ts = Date.now();
  state.undoStack.push(cur);
  _trimHistoryStack(state.undoStack);
  _restoreState(target);
  _schedulePersist();
  _refreshHistoryPanelIfOpen();
}

// Jump to any state in the labeled history. Negative offsets go back
// (into state.undoStack), positive go forward (into state.redoStack). 0 = current.
// Used by the history panel.
// History panel — full implementation in editor/history-panel.js.
// Wrappers preserve the legacy names that the topbar History button
// + undo/redo paths already reference.
const _historyPanel = createHistoryPanel({ undo, redo });
const _jumpToHistory             = _historyPanel.jumpToHistory;
const _toggleHistoryPanel        = _historyPanel.toggleHistoryPanel;
const _refreshHistoryPanelIfOpen = _historyPanel.refreshHistoryPanelIfOpen;

// _relTime lives in editor/layer-helpers.js.

// ── Canvas event helpers ──

// _canvasCoords lives in editor/canvas-coords.js — see import at top.

// ── Drawing ──

let _rasterizePromptPending = false;
const _pixelPaintTools = new Set(['brush', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'gradient']);

async function _offerRasterizeForTool(tool = state.tool) {
  const layer = activeLayer() || _activeParentLayer();
  if (!_pixelPaintTools.has(tool) || _getActiveMaskLayer() || state.quickMaskActive ||
      !['text', 'shape', 'placed'].includes(layer?.kind)) return false;
  return _confirmRasterizeLayer(layer);
}

async function _confirmRasterizeLayer(layer) {
  if (_rasterizePromptPending || _isLayerPixelLocked(state, layer)) return false;
  _rasterizePromptPending = true;
  try {
    const accepted = await uiModule.styledConfirm(
      `Rasterize "${layer.name || layer.kind}" to edit its pixels? You can undo this change.`,
      { title: 'Rasterize layer', confirmText: 'Rasterize', cancelText: 'Cancel' },
    );
    // The document or active target may have changed while the dialog was open.
    if (!accepted || !state.editorOpen || !state.layers.includes(layer) ||
        (activeLayer() || _activeParentLayer()) !== layer || _getActiveMaskLayer() ||
        _isLayerPixelLocked(state, layer)) return false;
    _saveState(`Rasterize "${layer.name}"`);
    _rasterizeTextLayer(layer);
    _rasterizeShapeLayer(layer);
    _rasterizePlacedLayer(layer);
    composite();
    _renderLayerPanel();
    _schedulePersist();
  } finally {
    _rasterizePromptPending = false;
  }
  return true;
}

function _beginDraw(e) {
  // Move always follows the object under the pointer. Selecting it before the
  // drag starts keeps the active layer, layer panel, and dragged pixels aligned.
  if (state.tool === 'move') {
    const picked = _pickLayerAtEvent(e);
    if (picked && picked.id !== state.activeLayerId) {
      state.activeLayerId = picked.id;
      state.selectedLayerIds = [picked.id];
      state.activeGroupId = null;
      _renderLayerPanel();
      _syncToolClearIndicators();
    }
  }
  // Fall back to the parent resolver so a stale activeLayerId doesn't
  // block strokes when there ARE layers present.
  const layer = activeLayer() || _activeParentLayer();
  if (_pixelPaintTools.has(state.tool) && !_getActiveMaskLayer() && !state.quickMaskActive &&
      ['text', 'shape', 'placed'].includes(layer?.kind)) {
    void _offerRasterizeForTool();
    return;
  }
  // Transform-tool drag (handle grab or move-fallback) — handler in
  // editor/tools/transform-drag.js.
  if (_transformDragTool.tryBegin(e)) return;
  // Magic wand is selection-only — works even on locked layers because
  // it doesn't mutate the layer until an action (Erase/Copy) is taken.
  // Full implementation in editor/tools/wand.js.
  if (state.tool === 'wand') return _wandTool.click(e);
  if (state.tool === 'sam') return _runSamSelection(e);
  if (state.tool === 'text') return _placeText(e);
  if (state.tool === 'shape') return _beginShape(e);
  if (state.tool === 'eyedropper') return _eyedropperTool.pick(e);
  if (state.tool === 'gradient') return _gradientTool.begin(e);
  if (state.tool === 'marquee') return _marqueeTool.begin(e);
  if (state.tool === 'pen') return _penTool.begin(e);
  // Inpaint can create its own layer + mask on the fly, so skip the
  // "no active layer → bail" gate for it specifically.
  const activeMask = _getActiveMaskLayer();
  const activeGroup = (state.layerGroups || []).find(group => group.id === state.activeGroupId);
  const groupMaskLocked = activeMask?.mode === 'group' && activeGroup
    ? activeGroup.locked || _groupAncestors(state, activeGroup).some(group => group.locked)
    : null;
  if (state.tool !== 'inpaint' && (!layer || (groupMaskLocked ?? _isLayerEffectivelyLocked(state, layer)))) return;
  // Keep activeLayerId in sync so downstream lookups resolve.
  if (layer && state.activeLayerId !== layer.id) state.activeLayerId = layer.id;
  if (state.tool === 'move') return _beginMove(e);
  if (state.tool === 'crop') return _beginCrop(e);
  if (state.tool === 'lasso') return _beginLasso(e);
  // Clone-stamp — source pick + stroke start. Full implementation in
  // editor/tools/clone.js; per-sample stamping continues through the
  // shared `_strokeTo` pipeline below.
  if (state.tool === 'clone' || state.tool === 'heal') return _cloneTool.begin(e);
  // Brush / Eraser / Inpaint share a stroke pipeline — handler in
  // editor/tools/stroke.js. Returns true for those tools, false
  // otherwise (any other tool that reached here is a no-op).
  _strokeTool.tryBegin(e);
}

function _continueDraw(e) {
  if (state.tool === 'pen') return _penTool.drag(e);
  // _continueDraw is now bound to the window so drags can extend past
  // the canvas. The brush-cursor overlay should only follow the cursor
  // when it's actually over the canvas, otherwise hide it.
  const overCanvas = state.mainCanvas && e.target === state.mainCanvas;
  if (state.tool === 'eyedropper' && overCanvas) _eyedropperTool.preview(e);
  if (['eraser', 'inpaint', 'lasso', 'brush', 'clone', 'heal', 'smudge', 'dodge', 'burn'].includes(state.tool) && state.mainCanvas) {
    if (overCanvas) _updateBrushCursor(e);
    else if (state.cursorEl) state.cursorEl.style.display = 'none';
  }
  // Transform-tool hover-cursor + handle drag — handler in
  // editor/tools/transform-drag.js. Returns true when the drag is
  // consuming the event (rotation / resize); the hover-cursor pass
  // returns false so the dispatcher can still fall through to other
  // tools that share the canvas hover (none currently, but kept for
  // future-proofing).
  if (_transformDragTool.tryContinue(e)) return;
  if (_shapeDraft) return _continueShape(e);
  if (state.gradientActive) return _gradientTool.drag(e);
  if (state.marqueeActive || state.selectionMoving) return _marqueeTool.drag(e);
  if (state.lassoActive) return _continueLasso(e);
  if (!state.drawing) {
    if (state.moving) return _continueMove(e);
    if (state.cropping || state.cropMoving) return _continueCrop(e);
    return;
  }
  // In-progress stroke (brush / eraser / inpaint / clone) — handler in
  // editor/tools/stroke.js.
  _strokeTool.tryContinue(e);
}

function _endDraw(e) {
  if (state.tool === 'pen') return _penTool.end();
  // Transform-tool drag end — handler in editor/tools/transform-drag.js.
  if (_transformDragTool.tryEnd(e)) return;
  if (_shapeDraft) return _endShape(e);
  if (state.gradientActive) return _gradientTool.end(e);
  if (state.marqueeActive || state.selectionMoving) return _marqueeTool.end(e);
  if (state.lassoActive) return _endLasso();
  if (state.moving) return _endMove();
  if (state.cropping || state.cropMoving) return _endCrop(e);
  // Stroke end (brush / eraser / inpaint / clone) — handler in
  // editor/tools/stroke.js.
  _strokeTool.tryEnd(e);
}

// Floating popup that appears after an inpaint stroke so the user can
// type a prompt and Generate without diverting to the side panel. The
// popup re-uses the existing #ge-inpaint-prompt and #ge-inpaint-run
// elements by reparenting them into a positioned wrapper, so all the
// existing handlers (Enter to submit, generate-button click) still fire.
// Inpaint-stroke prompt popup feature was removed — the user types in
// the side panel and hits Generate there. Helpers _showInpaintPrompt /
// _dismissInpaintPrompt and their dismiss-handlers were dead code and
// have been deleted.

// Clone Stamp painter — stamps circular samples from the source
// snapshot at every interpolated point between the last brush position
// and the current one, so a drag produces a continuous clone. The
// sample offset is fixed at stroke-start (Photoshop "aligned" mode):
// `sample = source + (cursor − strokeStart)`.
// Stroke pipeline — paints one segment last→current onto the active
// layer (or active mask sub-layer). Full implementation in
// editor/stroke-pipeline.js.
const _strokePipeline = createStrokePipeline({
  // Use the fallback-capable parent resolver so the stroke pipeline
  // and _getActiveMaskLayer() agree on which layer is active. Plain
  // activeLayer() returns null when activeLayerId is stale, which made
  // strokeTo bail even though a mask had been created on the fallback
  // parent — the "inpaint draws nothing" bug.
  activeLayer: () => activeLayer() || _activeParentLayer(),
  getActiveMaskLayer: () => _getStrokeTargetMask(),
  composite,
});
const _beginStroke = _strokePipeline.beginStroke;
const _strokeTo = _strokePipeline.strokeTo;
const _endStroke = _strokePipeline.endStroke;

// ── Brush cursor overlay ──


function _updateBrushCursor(e) {
  if (!state.mainCanvas) return;
  const rect = state.mainCanvas.getBoundingClientRect();
  const clientX = e.touches ? e.touches[0].clientX : e.clientX;
  const clientY = e.touches ? e.touches[0].clientY : e.clientY;

  if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;

  if (!state.cursorEl) {
    state.cursorEl = document.createElement('div');
    state.cursorEl.className = 'ge-brush-cursor';
    document.body.appendChild(state.cursorEl);
  }

  // Lasso uses the feather radius (or a sensible default) so the circle
  // shows the area that will be selected. Other tools use the brush size.
  let basePx;
  if (state.tool === 'lasso') {
    const f = parseInt(document.getElementById('ge-lasso-feather')?.value || '0');
    basePx = Math.max(10, f * 2);
  } else {
    basePx = state.brushSize;
  }
  // Use the canvas's real rendered scale. This stays accurate when fit-to-view
  // or responsive layout changes the canvas independently of nominal zoom.
  const diameterX = basePx * (rect.width / Math.max(1, state.mainCanvas.width));
  const diameterY = basePx * (rect.height / Math.max(1, state.mainCanvas.height));
  state.cursorEl.style.width = diameterX + 'px';
  state.cursorEl.style.height = diameterY + 'px';
  state.cursorEl.style.left = (clientX - diameterX / 2) + 'px';
  state.cursorEl.style.top = (clientY - diameterY / 2) + 'px';
  state.cursorEl.style.display = 'block';
  state.cursorEl.dataset.clientX = String(clientX);
  state.cursorEl.dataset.clientY = String(clientY);
  if (state.tool === 'inpaint') {
    // Visual cue for paint vs erase mode. Ctrl+Alt held mid-hover also
    // flips the cursor so the user sees the effective mode before they
    // click. Red = paint mask, white-dashed = erase mask.
    const flip = e && e.ctrlKey && e.altKey;
    const eraseEffective = flip ? !state.inpaintEraseMode : state.inpaintEraseMode;
    if (eraseEffective) {
      state.cursorEl.style.borderColor = 'rgba(255,255,255,0.9)';
      state.cursorEl.style.background = 'rgba(255,255,255,0.10)';
      state.cursorEl.style.borderStyle = 'dashed';
    } else {
      state.cursorEl.style.borderColor = 'rgba(255,80,80,0.8)';
      state.cursorEl.style.background = 'rgba(255,50,50,0.25)';
      state.cursorEl.style.borderStyle = 'solid';
    }
  } else if (state.tool === 'lasso') {
    state.cursorEl.style.borderColor = 'rgba(255,255,255,0.85)';
    state.cursorEl.style.background = 'rgba(0,0,0,0.15)';
    state.cursorEl.style.borderStyle = 'solid';
  } else {
    state.cursorEl.style.borderColor = state.tool === 'eraser' ? 'rgba(255,255,255,0.6)' : state.color;
    state.cursorEl.style.background = 'transparent';
    state.cursorEl.style.borderStyle = 'solid';
  }
}

// ── Move tool ──

// Move tool — full implementation lives in editor/tools/move.js. Wrap
// `_beginMove` / `_continueMove` / `_endMove` to the factory output so
// the existing dispatcher (_beginDraw / _continueDraw / _endDraw) keeps
// working without changes.
const _layerGeometry = createLayerGeometryController({ activeLayer, saveState: _saveState, composite });
const _moveTool = createMoveTool({
  activeLayer,
  saveState: _saveState,
  composite,
  onPositionChange: (layer, before, next) => {
    _layerGeometry.trackExternalMove(layer, before, next);
    if (state.transformActive) _drawTransformHandles();
  },
});
const _beginMove    = _moveTool.begin;
const _continueMove = _moveTool.drag;
const _endMove      = _moveTool.end;

// ── Crop tool ──

// Crop tool — full implementation in editor/tools/crop.js. Wire
// `_beginCrop` / `_continueCrop` / `_endCrop` to the factory output so
// the existing dispatcher keeps working without changes.
const _cropTool = createCropTool({
  composite,
  showCropApply: () => _showCropApply(),
  snapFrame: (frame, center) => _computeTransformSnap(frame, center),
});
const _beginCrop    = _cropTool.begin;
const _continueCrop = _cropTool.drag;
const _endCrop      = _cropTool.end;
function _cancelCrop(reason = 'cancel') {
  if (!state.cropRect && !state.cropping && !state.cropMoving) return false;
  _cropTool.cancel(reason);
  const panel = state.container?.querySelector('.ge-crop-apply');
  if (panel) panel.remove();
  return true;
}

function _showCropApply() {
  let pop = state.container.querySelector('.ge-crop-apply');
  if (pop) pop.remove();
  // A small floating panel: W × H inputs and the Apply button.
  pop = document.createElement('div');
  pop.className = 'ge-crop-apply';
  pop.innerHTML = `
    <input type="number" class="ge-crop-w" min="1" max="20000" value="${Math.round(state.cropRect.w)}" title="Width">
    <span class="ge-crop-x">×</span>
    <input type="number" class="ge-crop-h" min="1" max="20000" value="${Math.round(state.cropRect.h)}" title="Height">
    <button class="ge-crop-apply-btn">Apply</button>
  `;
  const area = state.container.querySelector('.ge-canvas-area');
  if (!area || !state.cropRect || !state.mainCanvas) return;
  area.appendChild(pop);

  pop.querySelector('.ge-crop-apply-btn').addEventListener('click', () => _applyCrop());
  // Editing W/H updates the crop rect anchored at its top-left so the
  // user sees the dimensions live in the overlay.
  const wInput = pop.querySelector('.ge-crop-w');
  const hInput = pop.querySelector('.ge-crop-h');
  const onSize = () => {
    if (!state.cropRect) return;
    const w = Math.max(1, parseInt(wInput.value, 10) || state.cropRect.w);
    const h = Math.max(1, parseInt(hInput.value, 10) || state.cropRect.h);
    state.cropRect = { ...state.cropRect, w, h };
    composite();
  };
  wInput.addEventListener('input', onSize);
  hInput.addEventListener('input', onSize);
  // Enter in either field triggers apply.
  [wInput, hInput].forEach(inp => {
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') _applyCrop(); });
  });

  // Position the panel just outside the bottom-right corner of the
  // crop rectangle (where the user finished dragging), in area coords.
  const canvasRect = state.mainCanvas.getBoundingClientRect();
  const areaRect = area.getBoundingClientRect();
  const scaleX = canvasRect.width / state.mainCanvas.width;
  const scaleY = canvasRect.height / state.mainCanvas.height;
  const localX = (canvasRect.left - areaRect.left) + (state.cropRect.x + state.cropRect.w) * scaleX;
  const localY = (canvasRect.top - areaRect.top) + (state.cropRect.y + state.cropRect.h) * scaleY;
  pop.style.position = 'absolute';
  pop.style.left = (localX + 6) + 'px';
  pop.style.top = (localY + 6) + 'px';
  // Clamp inside the CANVAS image bounds (not just the canvas-area) so
  // the panel doesn't sit on the dark padding around the canvas — it
  // stays anchored over the actual image.
  requestAnimationFrame(() => {
    const bRect = pop.getBoundingClientRect();
    const canvasLeft = canvasRect.left - areaRect.left;
    const canvasTop = canvasRect.top - areaRect.top;
    const canvasRight = canvasLeft + canvasRect.width;
    const canvasBottom = canvasTop + canvasRect.height;
    let nx = parseFloat(pop.style.left) || 0;
    let ny = parseFloat(pop.style.top) || 0;
    if (nx + bRect.width > canvasRight - 4) nx = canvasRight - bRect.width - 4;
    if (ny + bRect.height > canvasBottom - 4) ny = canvasBottom - bRect.height - 4;
    nx = Math.max(canvasLeft + 4, nx);
    ny = Math.max(canvasTop + 4, ny);
    pop.style.left = nx + 'px';
    pop.style.top = ny + 'px';
  });
}

function _applyCrop() {
  if (!state.cropRect) return;
  _saveState('Crop');
  _cropDocument(state, state.cropRect);
  const btn = state.container.querySelector('.ge-crop-apply');
  if (btn) btn.remove();
  composite();
  _renderLayerPanel();
  _schedulePersist();
  _fitZoom();
}

function _activeTextLayer() {
  const layer = activeLayer();
  return layer?.kind === 'text' && layer.text ? layer : null;
}

function _activeShapeLayer() {
  const layer = activeLayer();
  return layer?.kind === 'shape' && layer.shape ? layer : null;
}

let _textEditor = null;
let _shapeDraft = null;

function _syncTextControls() {
  const section = document.getElementById('ge-text-section');
  if (!section) return;
  const layer = _activeTextLayer();
  section.classList.toggle('has-text-layer', !!layer);
  if (!layer) return;
  const text = _normalizeTextData(layer.text);
  const setValue = (id, value) => {
    const input = document.getElementById(id);
    if (input && document.activeElement !== input) input.value = String(value);
  };
  setValue('ge-text-content', text.content);
  setValue('ge-text-font', text.fontFamily);
  setValue('ge-text-size', text.fontSize);
  setValue('ge-text-color', text.color);
  setValue('ge-text-line-height', text.lineHeight);
  setValue('ge-text-letter-spacing', text.letterSpacing);
  setValue('ge-text-frame-width', text.frameWidth);
  setValue('ge-text-frame-height', text.frameHeight);
  setValue('ge-text-vertical-align', text.verticalAlign);
  setValue('ge-text-stroke-width', text.strokeWidth);
  setValue('ge-text-stroke-color', text.strokeColor);
  const autoWidth = document.getElementById('ge-text-auto-width');
  if (autoWidth) autoWidth.checked = !!text.autoWidth;
  const frameWidth = document.getElementById('ge-text-frame-width');
  if (frameWidth) frameWidth.disabled = !!text.autoWidth;
  const bold = document.getElementById('ge-text-bold');
  const italic = document.getElementById('ge-text-italic');
  bold?.classList.toggle('active', text.fontWeight === '700');
  bold?.setAttribute('aria-pressed', text.fontWeight === '700' ? 'true' : 'false');
  italic?.classList.toggle('active', text.fontStyle === 'italic');
  italic?.setAttribute('aria-pressed', text.fontStyle === 'italic' ? 'true' : 'false');
  section.querySelectorAll('[data-text-align]').forEach(button => {
    const active = button.dataset.textAlign === text.align;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function _textDataFromControls(base = {}) {
  const section = document.getElementById('ge-text-section');
  const activeAlign = section?.querySelector('[data-text-align].active')?.dataset.textAlign || base.align;
  const contentInput = document.getElementById('ge-text-content');
  return _normalizeTextData({
    ...base,
    content: contentInput ? contentInput.value : (base.content ?? 'Text'),
    fontFamily: document.getElementById('ge-text-font')?.value || base.fontFamily,
    fontSize: document.getElementById('ge-text-size')?.value || base.fontSize,
    fontWeight: document.getElementById('ge-text-bold')?.classList.contains('active') ? '700' : '400',
    fontStyle: document.getElementById('ge-text-italic')?.classList.contains('active') ? 'italic' : 'normal',
    align: activeAlign,
    color: document.getElementById('ge-text-color')?.value || base.color,
    lineHeight: document.getElementById('ge-text-line-height')?.value || base.lineHeight,
    letterSpacing: document.getElementById('ge-text-letter-spacing')?.value ?? base.letterSpacing,
    frameWidth: document.getElementById('ge-text-frame-width')?.value || base.frameWidth,
    frameHeight: document.getElementById('ge-text-frame-height')?.value ?? base.frameHeight,
    autoWidth: !!document.getElementById('ge-text-auto-width')?.checked,
    verticalAlign: document.getElementById('ge-text-vertical-align')?.value || base.verticalAlign,
    strokeWidth: document.getElementById('ge-text-stroke-width')?.value ?? base.strokeWidth,
    strokeColor: document.getElementById('ge-text-stroke-color')?.value || base.strokeColor,
  });
}

function _rerenderTextLayer(layer, nextText, preserveCenter = true) {
  const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const center = { x: offset.x + layer.canvas.width / 2, y: offset.y + layer.canvas.height / 2 };
  layer.text = _normalizeTextData(nextText);
  _renderTextLayer(layer);
  if (preserveCenter) {
    state.layerOffsets.set(layer.id, {
      x: Math.round(center.x - layer.canvas.width / 2),
      y: Math.round(center.y - layer.canvas.height / 2),
    });
  }
  layer.name = layer.text.content.split('\n')[0].trim().slice(0, 36) || 'Text';
  composite();
}

function _openTextEditor(layer, { selectAll = false, saveHistory = true } = {}) {
  if (!layer?.text || !_textEditor) return;
  if (saveHistory) _saveState('Edit text');
  _textEditor.open(layer, { selectAll });
}

function _syncShapeControls() {
  const section = document.getElementById('ge-shape-section');
  if (!section) return;
  const layer = _activeShapeLayer();
  const shape = _normalizeShapeData(layer?.shape || {
    type: section.querySelector('[data-shape-type].active')?.dataset.shapeType,
    fillColor: state.color,
  });
  section.classList.toggle('has-shape-layer', !!layer);
  const setValue = (id, value) => {
    const input = document.getElementById(id);
    if (input && document.activeElement !== input) input.value = String(value);
  };
  setValue('ge-shape-fill', shape.fillColor);
  setValue('ge-shape-fill-type', shape.fillType);
  setValue('ge-shape-gradient-start', shape.gradientStart);
  setValue('ge-shape-gradient-mid', shape.gradientMid);
  setValue('ge-shape-gradient-mid-position', shape.gradientMidPosition);
  setValue('ge-shape-gradient-end', shape.gradientEnd);
  setValue('ge-shape-gradient-angle', shape.gradientAngle);
  const extraStops = section.querySelector('#ge-shape-gradient-extra-stops');
  if (extraStops) {
    const legacyPositions = new Set([0, 100]);
    if (shape.gradientMidEnabled) legacyPositions.add(Number(shape.gradientMidPosition));
    extraStops.innerHTML = shape.gradientStops
      .filter(stop => !legacyPositions.has(Number(stop.position)))
      .map((stop, index) => `
        <div class="ge-gradient-extra-stop" data-gradient-extra-stop>
          <label class="ge-text-field"><span>Stop ${index + 1}</span><input type="color" class="ge-color-picker" data-gradient-stop-color value="${String(stop.color).replace(/"/g, '&quot;')}" /></label>
          <label class="ge-text-field"><span>Position</span><input type="number" min="1" max="99" step="1" data-gradient-stop-position value="${Number(stop.position)}" /></label>
          <button type="button" class="ge-btn ge-btn-sm ge-gradient-stop-remove" data-gradient-stop-remove title="Remove gradient stop" aria-label="Remove gradient stop">×</button>
        </div>`).join('');
  }
  setValue('ge-shape-stroke', shape.strokeColor);
  setValue('ge-shape-stroke-width', shape.strokeWidth);
  setValue('ge-shape-radius', shape.cornerRadius);
  setValue('ge-shape-sides', shape.sides);
  section.querySelectorAll('[data-shape-type]').forEach(button => {
    const selected = button.dataset.shapeType === shape.type;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
  const sides = section.querySelector('.ge-shape-sides-field');
  if (sides) sides.hidden = shape.type !== 'polygon';
  const gradientVisible = shape.fillType === 'linear-gradient';
  const gradientFields = section.querySelector('.ge-shape-gradient-fields');
  const gradientAngle = section.querySelector('.ge-shape-gradient-angle-field');
  if (gradientFields) gradientFields.hidden = !gradientVisible;
  if (gradientAngle) gradientAngle.hidden = !gradientVisible;
  const gradientMidEnabled = section.querySelector('#ge-shape-gradient-mid-enabled');
  const gradientMid = section.querySelector('#ge-shape-gradient-mid');
  const gradientMidPosition = section.querySelector('#ge-shape-gradient-mid-position');
  if (gradientMidEnabled) gradientMidEnabled.checked = !!shape.gradientMidEnabled;
  if (gradientMid) gradientMid.disabled = !shape.gradientMidEnabled;
  if (gradientMidPosition) gradientMidPosition.disabled = !shape.gradientMidEnabled;
  const addGradientStop = section.querySelector('#ge-shape-gradient-add-stop');
  if (addGradientStop) addGradientStop.hidden = !gradientVisible;
}

function _shapeDataFromControls(base = {}) {
  const section = document.getElementById('ge-shape-section');
  const gradientStart = document.getElementById('ge-shape-gradient-start')?.value || base.gradientStart || '#ffffff';
  const gradientMid = document.getElementById('ge-shape-gradient-mid')?.value || base.gradientMid || '#808080';
  const gradientMidEnabled = !!document.getElementById('ge-shape-gradient-mid-enabled')?.checked;
  const gradientMidPosition = document.getElementById('ge-shape-gradient-mid-position')?.value ?? base.gradientMidPosition;
  const gradientEnd = document.getElementById('ge-shape-gradient-end')?.value || base.gradientEnd || '#000000';
  const extraStops = [...(section?.querySelectorAll('[data-gradient-extra-stop]') || [])].map(row => ({
    color: row.querySelector('[data-gradient-stop-color]')?.value,
    position: row.querySelector('[data-gradient-stop-position]')?.value,
  }));
  const gradientStops = _normalizeGradientStops([
    { position: 0, color: gradientStart },
    ...(gradientMidEnabled ? [{ position: gradientMidPosition, color: gradientMid }] : []),
    ...extraStops,
    { position: 100, color: gradientEnd },
  ]);
  return _normalizeShapeData({
    ...base,
    type: section?.querySelector('[data-shape-type].active')?.dataset.shapeType || base.type,
    fillColor: document.getElementById('ge-shape-fill')?.value || base.fillColor || state.color,
    fillType: document.getElementById('ge-shape-fill-type')?.value || base.fillType,
    gradientStart,
    gradientMid,
    gradientMidEnabled,
    gradientMidPosition,
    gradientEnd,
    gradientStops,
    gradientAngle: document.getElementById('ge-shape-gradient-angle')?.value ?? base.gradientAngle,
    strokeColor: document.getElementById('ge-shape-stroke')?.value || base.strokeColor,
    strokeWidth: document.getElementById('ge-shape-stroke-width')?.value ?? base.strokeWidth,
    cornerRadius: document.getElementById('ge-shape-radius')?.value ?? base.cornerRadius,
    sides: document.getElementById('ge-shape-sides')?.value ?? base.sides,
  });
}

function _rerenderShapeLayer(layer, nextShape, preserveCenter = true) {
  const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const center = { x: offset.x + layer.canvas.width / 2, y: offset.y + layer.canvas.height / 2 };
  layer.shape = _normalizeShapeData(nextShape);
  _renderShapeLayer(layer);
  if (preserveCenter) {
    state.layerOffsets.set(layer.id, {
      x: Math.round(center.x - layer.canvas.width / 2),
      y: Math.round(center.y - layer.canvas.height / 2),
    });
  }
  layer.name = `${layer.shape.type[0].toUpperCase()}${layer.shape.type.slice(1)}`;
  composite();
}

function _wireShapeControls(controls) {
  const section = controls.querySelector('#ge-shape-section');
  if (!section || section.dataset.wired) return;
  section.dataset.wired = 'true';
  section.querySelectorAll('[data-shape-type]').forEach(button => button.addEventListener('click', () => {
    section.querySelectorAll('[data-shape-type]').forEach(item => item.classList.toggle('active', item === button));
    const layer = _activeShapeLayer();
    if (layer) {
      _saveState('Change shape');
      _rerenderShapeLayer(layer, _shapeDataFromControls(layer.shape));
      _renderLayerPanel();
    }
    _syncShapeControls();
  }));
  section.querySelectorAll('input').forEach(input => {
    input.addEventListener('focus', () => { input.dataset.geHistorySaved = 'false'; });
    input.addEventListener('input', () => {
      const layer = _activeShapeLayer();
      if (!layer) return;
      if (input.dataset.geHistorySaved !== 'true') {
        _saveState('Edit shape');
        input.dataset.geHistorySaved = 'true';
      }
      _rerenderShapeLayer(layer, _shapeDataFromControls(layer.shape));
    });
    input.addEventListener('change', () => {
      input.dataset.geHistorySaved = 'false';
      _renderLayerPanel();
    });
  });
  section.querySelector('#ge-shape-fill-type')?.addEventListener('change', event => {
    const layer = _activeShapeLayer();
    if (!layer) return;
    _saveState('Change shape fill');
    _rerenderShapeLayer(layer, _shapeDataFromControls(layer.shape));
    _syncShapeControls();
  });
  section.querySelector('#ge-shape-gradient-add-stop')?.addEventListener('click', () => {
    const layer = _activeShapeLayer();
    if (!layer) return;
    const shape = _shapeDataFromControls(layer.shape);
    const stops = shape.gradientStops;
    if (stops.length >= 12) return;
    let largestGap = 0;
    let position = 50;
    for (let index = 1; index < stops.length; index += 1) {
      const gap = stops[index].position - stops[index - 1].position;
      if (gap > largestGap) {
        largestGap = gap;
        position = Math.round(stops[index - 1].position + gap / 2);
      }
    }
    _saveState('Add gradient stop');
    shape.gradientStops.push({ position, color: '#808080' });
    _rerenderShapeLayer(layer, shape);
    _renderLayerPanel();
    _syncShapeControls();
  });
  section.addEventListener('click', event => {
    const remove = event.target.closest('[data-gradient-stop-remove]');
    if (!remove) return;
    const row = remove.closest('[data-gradient-extra-stop]');
    const layer = _activeShapeLayer();
    if (!row || !layer) return;
    const shape = _shapeDataFromControls(layer.shape);
    const rows = [...section.querySelectorAll('[data-gradient-extra-stop]')];
    const index = rows.indexOf(row);
    if (index < 0) return;
    const interior = shape.gradientStops.filter(stop => stop.position > 0 && stop.position < 100 && !(shape.gradientMidEnabled && stop.position === Number(shape.gradientMidPosition)));
    if (!interior[index]) return;
    _saveState('Remove gradient stop');
    interior.splice(index, 1);
    shape.gradientStops = _normalizeGradientStops([
      { position: 0, color: shape.gradientStart },
      ...(shape.gradientMidEnabled ? [{ position: shape.gradientMidPosition, color: shape.gradientMid }] : []),
      ...interior,
      { position: 100, color: shape.gradientEnd },
    ]);
    _rerenderShapeLayer(layer, shape);
    _renderLayerPanel();
    _syncShapeControls();
  });
  section.addEventListener('focusin', event => {
    if (event.target.matches('[data-gradient-stop-color], [data-gradient-stop-position]')) {
      event.target.dataset.geHistorySaved = 'false';
    }
  });
  section.addEventListener('input', event => {
    if (!event.target.matches('[data-gradient-stop-color], [data-gradient-stop-position]')) return;
    const layer = _activeShapeLayer();
    if (!layer) return;
    if (event.target.dataset.geHistorySaved !== 'true') {
      _saveState('Edit shape');
      event.target.dataset.geHistorySaved = 'true';
    }
    _rerenderShapeLayer(layer, _shapeDataFromControls(layer.shape));
  });
  section.querySelector('#ge-shape-rasterize')?.addEventListener('click', () => {
    const layer = _activeShapeLayer();
    if (!layer) return;
    _saveState(`Rasterize "${layer.name}"`);
    _rasterizeShapeLayer(layer);
    composite();
    _renderLayerPanel();
    _syncShapeControls();
    uiModule.showToast('Shape layer rasterized');
  });
  _syncShapeControls();
}

function _wireTextControls(controls) {
  const section = controls.querySelector('#ge-text-section');
  if (!section || section.dataset.wired) return;
  section.dataset.wired = 'true';
  const editable = section.querySelectorAll('textarea, select, input[type="number"], .ge-color-picker');
  editable.forEach(input => {
    input.addEventListener('focus', () => { input.dataset.geHistorySaved = 'false'; });
    input.addEventListener('mousedown', () => { input.dataset.geHistorySaved = 'false'; });
    input.addEventListener('input', () => {
      const layer = _activeTextLayer();
      if (!layer) return;
      if (input.dataset.geHistorySaved !== 'true') {
        _saveState('Edit text');
        input.dataset.geHistorySaved = 'true';
      }
      _rerenderTextLayer(layer, _textDataFromControls(layer.text));
    });
    input.addEventListener('change', () => {
      input.dataset.geHistorySaved = 'false';
      _renderLayerPanel();
    });
  });
  section.querySelector('#ge-text-auto-width')?.addEventListener('change', () => {
    const layer = _activeTextLayer();
    if (!layer) return;
    _saveState('Toggle text auto width');
    _rerenderTextLayer(layer, _textDataFromControls(layer.text));
    _syncTextControls();
    _renderLayerPanel();
  });
  const toggle = (button, mutator) => button?.addEventListener('click', () => {
    const layer = _activeTextLayer();
    if (!layer) return;
    _saveState('Format text');
    mutator(button, layer);
    _rerenderTextLayer(layer, _textDataFromControls(layer.text));
    _renderLayerPanel();
    _syncTextControls();
  });
  toggle(section.querySelector('#ge-text-bold'), button => button.classList.toggle('active'));
  toggle(section.querySelector('#ge-text-italic'), button => button.classList.toggle('active'));
  section.querySelectorAll('[data-text-align]').forEach(button => toggle(button, selected => {
    section.querySelectorAll('[data-text-align]').forEach(item => item.classList.toggle('active', item === selected));
  }));
  section.querySelector('#ge-text-rasterize')?.addEventListener('click', () => {
    const layer = _activeTextLayer();
    if (!layer) return;
    _saveState(`Rasterize "${layer.name}"`);
    layer.kind = 'raster';
    layer.text = null;
    composite();
    _renderLayerPanel();
    _syncTextControls();
    uiModule.showToast('Text layer rasterized');
  });
  _syncTextControls();
}

function _placeText(e) {
  const point = _canvasCoords(e, state.mainCanvas);
  for (let index = state.layers.length - 1; index >= 0; index--) {
    const layer = state.layers[index];
    if (layer.kind !== 'text' || !layer.visible) continue;
    const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    if (point.x < offset.x || point.y < offset.y ||
        point.x > offset.x + layer.canvas.width || point.y > offset.y + layer.canvas.height) continue;
    state.activeLayerId = layer.id;
    layer.activeMaskId = null;
    state.maskCanvas = null;
    state.maskCtx = null;
    _renderLayerPanel();
    _syncTextControls();
    _openTextEditor(layer);
    return;
  }
  _saveState('Add text');
  const layer = createLayer('Text', 1, 1);
  layer.kind = 'text';
  layer.text = _textDataFromControls({ color: state.color, frameWidth: 320, autoWidth: false });
  if (!layer.text.content) layer.text.content = 'Text';
  _renderTextLayer(layer);
  state.layerOffsets.set(layer.id, { x: Math.round(point.x), y: Math.round(point.y) });
  state.layers.push(layer);
  state.activeLayerId = layer.id;
  _renderLayerPanel();
  composite();
  _syncTextControls();
  _openTextEditor(layer, { selectAll: true, saveHistory: false });
}

function _beginShape(e) {
  const point = _canvasCoords(e, state.mainCanvas);
  _saveState('Add shape');
  const layer = createLayer('Shape', 1, 1);
  layer.kind = 'shape';
  layer.shape = _shapeDataFromControls({ width: 1, height: 1, fillColor: state.color });
  _renderShapeLayer(layer);
  state.layerOffsets.set(layer.id, { x: Math.round(point.x), y: Math.round(point.y) });
  state.layers.push(layer);
  state.activeLayerId = layer.id;
  _shapeDraft = { layer, start: point };
  _renderLayerPanel();
  _syncShapeControls();
  composite();
}

function _continueShape(e) {
  if (!_shapeDraft) return;
  const point = _canvasCoords(e, state.mainCanvas);
  const x = Math.min(_shapeDraft.start.x, point.x);
  const y = Math.min(_shapeDraft.start.y, point.y);
  const shape = {
    ..._shapeDraft.layer.shape,
    width: Math.max(1, Math.abs(point.x - _shapeDraft.start.x)),
    height: Math.max(1, Math.abs(point.y - _shapeDraft.start.y)),
  };
  _shapeDraft.layer.shape = _normalizeShapeData(shape);
  _renderShapeLayer(_shapeDraft.layer);
  state.layerOffsets.set(_shapeDraft.layer.id, { x: Math.round(x), y: Math.round(y) });
  composite();
}

function _endShape(e) {
  if (!_shapeDraft) return;
  if (e) _continueShape(e);
  if (_shapeDraft.layer.shape.width < 3 && _shapeDraft.layer.shape.height < 3) {
    _shapeDraft.layer.shape.width = 180;
    _shapeDraft.layer.shape.height = 120;
    _renderShapeLayer(_shapeDraft.layer);
  }
  _shapeDraft = null;
  _renderLayerPanel();
  _syncShapeControls();
  _schedulePersist();
  composite();
}

function _cancelShapeDraft() {
  if (!_shapeDraft) return false;
  const draftLayer = _shapeDraft.layer;
  state.layers = state.layers.filter(layer => layer !== draftLayer);
  state.layerOffsets.delete(draftLayer.id);
  state.selectedLayerIds = (state.selectedLayerIds || []).filter(id => id !== draftLayer.id);
  state.activeLayerId = state.selectedLayerIds.at(-1) || state.layers.at(-1)?.id || null;
  _shapeDraft = null;
  _renderLayerPanel();
  _syncShapeControls();
  composite();
  return true;
}

// ── Free Transform (Ctrl+Alt+T) ──

// Transform session — full implementation in
// editor/tools/transform-session.js. Wrappers preserve the legacy
// names that the dispatcher / toolbar / shortcuts already reference.
const _transformSession = createTransformSession({
  activeLayer,
  saveState: _saveState,
  composite,
  fitZoom: () => _fitZoom(),
  drawTransformHandles: () => _drawTransformHandles(),
  showCanvasLoading: (label) => _showCanvasLoading(label),
  hideCanvasLoading: () => _hideCanvasLoading(),
  undo,
  uiModule,
  syncGeometryControls: () => _layerGeometry.sync(),
  getDocumentSelection: () => _selectionMaskAsDocument({ materializeLasso: true }),
  syncSelectionUi: () => _syncToolClearIndicators(),
  schedulePersist: () => _schedulePersist(),
});
const _startTransform      = _transformSession.startTransform;
const _startSelectionTransform = _transformSession.startSelectionTransform;
const _openTransformPopup  = _transformSession.openTransformPopup;
const _closeTransformPopup = _transformSession.closeTransformPopup;
const _reapplyTransform    = _transformSession.reapplyTransform;
const _nudgeTransform      = _transformSession.nudgeTransform;
const _confirmTransform    = _transformSession.confirmTransform;
const _cancelTransform     = _transformSession.cancelTransform;
window.__galleryEditorHandleEscape = () => {
  const sizePrompt = document.getElementById('ge-canvas-size-overlay');
  if (sizePrompt && getComputedStyle(sizePrompt).display !== 'none') {
    sizePrompt._cancelCanvasSize?.();
    return true;
  }
  if (state.transformActive) {
    _cancelTransform();
    return true;
  }
  if (state.cropRect || state.cropping || state.cropMoving) {
    _cancelCrop('escape');
    return true;
  }
  if (state.marqueeActive || state.selectionMoving) {
    _marqueeTool.cancel('escape');
    return true;
  }
  if (state.lassoActive || state.lassoPoints.length) {
    _cancelLasso();
    return true;
  }
  if (state.gradientActive) {
    _gradientTool.cancel();
    return true;
  }
  if (_shapeDraft) {
    _cancelShapeDraft();
    return true;
  }
  return false;
};

// ── Lasso tool ──

// Lasso tool — full implementation in editor/tools/lasso.js.
const _lassoTool = createLassoTool({
  activeLayer,
  saveState: _saveState,
  commitSelectionMask: (mask, layer, mode, source, offset) => _commitSelectionMask(mask, layer, mode, source, offset),
  composite,
  drawLassoOverlay: () => _drawLassoOverlay(),
  syncToolClearIndicators: () => _syncToolClearIndicators(),
});
const _beginLasso    = _lassoTool.begin;
const _continueLasso = _lassoTool.drag;
const _endLasso      = _lassoTool.end;
const _cancelLasso   = _lassoTool.cancel;
const _penTool = createPenSelectionTool({
  activeLayer, saveState: _saveState, composite,
  commitSelectionMask: _commitSelectionMask,
  syncSelectionUi: _syncToolClearIndicators,
});

function _drawMarqueeOverlay() {
  const rect = state.marqueeRect;
  if (!rect || !state.mainCtx) return;
  const ctx = state.mainCtx;
  ctx.save();
  ctx.beginPath();
  if (state.marqueeShape === 'ellipse') {
    ctx.ellipse(rect.x + rect.w / 2, rect.y + rect.h / 2, rect.w / 2, rect.h / 2, 0, 0, Math.PI * 2);
  } else {
    ctx.rect(rect.x, rect.y, rect.w, rect.h);
  }
  ctx.fillStyle = 'rgba(255, 80, 80, 0.12)';
  ctx.fill();
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 1 / state.zoom;
  ctx.setLineDash([4 / state.zoom, 4 / state.zoom]);
  ctx.stroke();
  ctx.restore();
}

const _marqueeTool = createMarqueeTool({
  activeLayer,
  saveState: _saveState,
  composite,
  drawOverlay: _drawMarqueeOverlay,
  syncSelectionUi: () => _syncToolClearIndicators(),
  ensureDocumentSelection: () => _selectionMaskAsDocument({ materializeLasso: true }),
  snapFrame: (frame, center) => _computeTransformSnap(frame, center),
});

// Magic wand — selection-only click handler in editor/tools/wand.js.
const _wandTool = createWandTool({
  activeLayer,
  saveState: _saveState,
  composite,
  wandHits: (x, y) => _wandHits(x, y),
  runMagicWand: (x, y, mode) => _runMagicWand(x, y, mode),
  deselectSelection: () => _deselectSelection(),
});

function _syncCloneSourceUi() {
  const label = document.getElementById('ge-clone-source-label');
  const clear = document.getElementById('ge-clone-source-clear');
  const hasSource = state.cloneSourceX !== null && state.cloneSourceY !== null;
  if (label) label.textContent = hasSource
    ? (state.cloneSampleMode === 'composite' ? 'All visible layers sampled' : 'Active layer sampled')
    : 'No source selected';
  if (clear) clear.hidden = !hasSource;
}

// Clone-stamp tool — source-pick + stroke-start handler in
// editor/tools/clone.js. Per-sample stamping still runs through the
// shared stroke pipeline (`_strokeTo`) since clone-mode is detected
// there from state.cloneSourceSnapshot.
const _cloneTool = createCloneTool({
  activeLayer,
  saveState: _saveState,
  beginStroke: (sample, tool) => _beginStroke(sample, tool),
  showToast: (msg) => { if (uiModule) uiModule.showToast(msg); },
  onSourceChanged: _syncCloneSourceUi,
  getSampleCanvas: ({ layer, mode }) => mode === 'composite'
    ? state.documentCompositeCanvas
    : layer?.canvas,
});

const _eyedropperTool = createEyedropperTool({
  activeLayer,
  composite,
  syncColor: (color) => {
    state.container?.querySelectorAll('.ge-color-picker').forEach(input => {
      input.value = color;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  },
  syncPreview: (color, sample) => {
    const value = state.container?.querySelector('#ge-eyedropper-live-value');
    const rgbValue = state.container?.querySelector('#ge-eyedropper-live-rgb');
    const hslValue = state.container?.querySelector('#ge-eyedropper-live-hsl');
    const swatch = state.container?.querySelector('#ge-eyedropper-live-swatch');
    const loupe = state.container?.querySelector('#ge-eyedropper-loupe');
    if (value) value.textContent = color || 'No visible pixel';
    const rgb = sample?.rgb;
    if (rgbValue) rgbValue.textContent = rgb ? `RGB ${rgb.join(' ')}` : 'RGB --';
    if (hslValue) {
      if (!rgb) hslValue.textContent = 'HSL --';
      else {
        const [r, g, b] = rgb.map(channel => channel / 255);
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        const delta = max - min;
        const lightness = (max + min) / 2;
        let hue = 0;
        let saturation = 0;
        if (delta) {
          saturation = delta / (1 - Math.abs(2 * lightness - 1));
          if (max === r) hue = ((g - b) / delta) % 6;
          else if (max === g) hue = (b - r) / delta + 2;
          else hue = (r - g) / delta + 4;
          hue = Math.round(hue * 60);
          if (hue < 0) hue += 360;
        }
        hslValue.textContent = `HSL ${hue} ${Math.round(saturation * 100)}% ${Math.round(lightness * 100)}%`;
      }
    }
    if (swatch) {
      swatch.style.background = color || 'transparent';
      swatch.classList.toggle('empty', !color);
    }
    if (loupe) {
      const ctx = loupe.getContext('2d');
      ctx.clearRect(0, 0, loupe.width, loupe.height);
      if (sample?.canvas && Number.isFinite(sample.x) && Number.isFinite(sample.y)) {
        const radius = 5;
        const size = radius * 2 + 1;
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(sample.canvas, Math.floor(sample.x) - radius, Math.floor(sample.y) - radius, size, size, 0, 0, loupe.width, loupe.height);
        ctx.strokeStyle = 'rgba(224, 108, 117, 0.92)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(loupe.width / 2, 0);
        ctx.lineTo(loupe.width / 2, loupe.height);
        ctx.moveTo(0, loupe.height / 2);
        ctx.lineTo(loupe.width, loupe.height / 2);
        ctx.stroke();
      }
    }
  },
  showToast: (msg) => uiModule?.showToast(msg),
});

const _gradientTool = createGradientTool({
  activeLayer,
  saveState: _saveState,
  composite,
  schedulePersist: _schedulePersist,
  renderLayerPanel: () => _renderLayerPanel(),
  getSettings: () => ({
    start: document.getElementById('ge-gradient-start')?.value || state.gradientStart,
    mid: document.getElementById('ge-gradient-mid')?.value || state.gradientMid,
    midPosition: document.getElementById('ge-gradient-mid-position')?.value ?? state.gradientMidPosition,
    midEnabled: document.getElementById('ge-gradient-mid-enabled')?.checked ?? state.gradientMidEnabled,
    extraStops: [...(document.querySelectorAll('#ge-gradient-extra-stops [data-gradient-extra-stop]') || [])].map(row => ({
      color: row.querySelector('[data-gradient-stop-color]')?.value,
      position: row.querySelector('[data-gradient-stop-position]')?.value,
    })),
    end: document.getElementById('ge-gradient-end')?.value || state.gradientEnd,
    endAlpha: document.getElementById('ge-gradient-end-alpha')?.value ?? state.gradientEndAlpha,
    opacity: document.getElementById('ge-gradient-opacity')?.value ?? state.gradientOpacity,
    type: document.getElementById('ge-gradient-type')?.value || state.gradientType,
  }),
});

// Transform-tool drag interactions (handle picking, rotation, resize)
// in editor/tools/transform-drag.js. The dispatcher calls
// `tryBegin/tryContinue/tryEnd` and short-circuits when they return true.
const _transformDragTool = createTransformDragTool({
  composite,
  drawTransformHandles: () => _drawTransformHandles(),
  reapplyTransform: () => _reapplyTransform(),
  getTransformHandle: (x, y, options) => _getTransformHandle(x, y, options),
  cursorForHandle: _cursorForHandle,
  pointInTransformFrame: (x, y) => _containsTransformFramePoint(x, y),
  snapTransformFrame: (frame, center) => _computeTransformSnap(frame, center),
});

// Shared stroke pipeline (brush / eraser / inpaint) in
// editor/tools/stroke.js. Clone reuses tryContinue / tryEnd via the
// shared drawing flag; clone's own begin is in editor/tools/clone.js.
const _strokeTool = createStrokeTool({
  saveState: _saveState,
  undo,
  beginStroke: (sample, tool) => _beginStroke(sample, tool),
  strokeTo: (sample) => _strokeTo(sample),
  endStroke: (sample) => _endStroke(sample),
  composite,
  getActiveMaskLayer: () => _getStrokeTargetMask(),
  activeParentLayer: () => _activeParentLayer(),
  ensureActiveMaskLayer: () => _ensureActiveMaskLayer(),
  createLayer,
  renderLayerPanel: () => _renderLayerPanel(),
  syncToolClearIndicators: () => _syncToolClearIndicators(),
  showToast: (message) => uiModule?.showToast(message),
});

// Compute the outward-normal offset of the lasso polygon by `grow`
// pixels at each vertex. Lets the Edge stroke slider visually move
// the dashed outline in/out without re-running the mask raster.
// Thin wrapper around the pure helper in editor/tools/lasso-mask.js
// so existing callers using module state stay unchanged.
function _lassoOffsetPoints(grow) {
  return _lassoOffsetPointsImpl(state.lassoPoints, grow);
}

function _drawLassoOverlay() {
  if (state.lassoPoints.length < 3) return;
  // Read live slider values so the overlay shows the actual edge that
  // will be committed: Edge stroke shifts the polygon outline in/out;
  // Feather draws a soft red halo to suggest the alpha fade.
  const featherEl = document.getElementById('ge-lasso-feather');
  const growEl = document.getElementById('ge-lasso-grow');
  const feather = featherEl ? parseInt(featherEl.value || '0', 10) : 0;
  const grow = growEl ? parseInt(growEl.value || '0', 10) : 0;
  const ringPts = grow ? _lassoOffsetPoints(grow) : state.lassoPoints;
  const tracePath = (pts) => {
    state.mainCtx.beginPath();
    state.mainCtx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) state.mainCtx.lineTo(pts[i].x, pts[i].y);
    state.mainCtx.closePath();
  };
  if (feather > 0) {
    // Concentric outer outlines that fade out, suggesting the feather
    // fade band that will be applied to the mask alpha at commit.
    const rings = 4;
    for (let r = 1; r <= rings; r++) {
      const offset = (feather * r) / rings;
      tracePath(_lassoOffsetPoints(grow + offset));
      state.mainCtx.strokeStyle = `rgba(255, 80, 80, ${0.4 * (1 - r / rings)})`;
      state.mainCtx.lineWidth = 1 / state.zoom;
      state.mainCtx.setLineDash([]);
      state.mainCtx.stroke();
    }
  }
  tracePath(ringPts);
  state.mainCtx.strokeStyle = '#fff';
  state.mainCtx.lineWidth = 1 / state.zoom;
  state.mainCtx.setLineDash([4 / state.zoom, 4 / state.zoom]);
  state.mainCtx.stroke();
  state.mainCtx.setLineDash([]);
  state.mainCtx.fillStyle = 'rgba(255, 80, 80, 0.1)';
  state.mainCtx.fill();
}

function _getLassoPath(ctx) {
  _getLassoPathImpl(ctx, state.lassoPoints);
}

/**
 * Build a feathered selection mask from the current lasso polygon.
 * Implementation lives in editor/tools/lasso-mask.js — this wrapper
 * forwards the current `state.lassoPoints` so existing callers keep
 * working unchanged.
 */
function _buildLassoMask(w, h, offX, offY, feather, grow) {
  return _buildLassoMaskImpl(state.lassoPoints, w, h, offX, offY, feather, grow);
}

// ── Magic Wand ──

// Click-fill from (cx, cy) on the active layer. Builds a binary mask of
// all pixels reachable from the seed whose RGB distance is within
// state.wandTolerance × 4.42 (4.42 ≈ scale factor so tolerance=100 ≈ max).
//
// `mode`:
//   'replace'  (default) — replaces any previous selection
//   'add'      — unions the new region with the existing selection
//   'subtract' — removes the new region from the existing selection
// Cached layer pixel data + dimensions for the wand. `getImageData` is
// the dominant cost when live-retuning tolerance (millions of pixels →
// 50–200 ms per call on a 4K canvas). Invalidated by _invalidateWandCache
// whenever the active layer changes or the editor closes.
// Pristine snapshot of the last Bg-Removed cutout so the Edge cleanup
// sliders can live-rebuild the alpha without re-running the model.
function _invalidateWandCache() { state.wandSrcCache = null; }

function _commitSelectionMask(mask, layer, mode = 'replace', source = 'wand', sourceOffset = null) {
  if (!mask || !layer) return;
  const previousLayer = state.layers.find(candidate => candidate.id === state.wandLayerId);
  const previousOffset = previousLayer
    ? (state.layerOffsets.get(previousLayer.id) || { x: 0, y: 0 })
    : { x: 0, y: 0 };
  const current = _selectionMaskToDocument(
    state.wandMask,
    state.wandMaskSpace || 'layer',
    previousOffset,
    state.imgWidth,
    state.imgHeight,
  );
  const incomingOffset = sourceOffset || state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const incoming = _selectionMaskToDocument(
    mask,
    'layer',
    incomingOffset,
    state.imgWidth,
    state.imgHeight,
  );
  state.wandMask = _mergeSelectionMasks(current, incoming, mode);
  state.wandLayerId = layer.id;
  state.wandMaskSpace = 'document';
  state.selectionSource = source;
}

function _getWandSource(layer) {
  if (state.wandSrcCache && state.wandSrcCache.layerId === layer.id
      && state.wandSrcCache.w === layer.canvas.width
      && state.wandSrcCache.h === layer.canvas.height) {
    return state.wandSrcCache;
  }
  const w = layer.canvas.width, h = layer.canvas.height;
  state.wandSrcCache = {
    layerId: layer.id, w, h,
    data: layer.ctx.getImageData(0, 0, w, h).data,
  };
  return state.wandSrcCache;
}

// Click-deselect helper: returns true if (cx, cy) lands inside the
// existing wand selection on the same layer. Used by the mousedown
// handler to make a second click "in the selection" toggle it off.
function _wandHits(cx, cy) {
  if (!state.wandMask || !state.wandLayerId) return false;
  const layer = state.layers.find(l => l.id === state.wandLayerId);
  if (!layer) return false;
  const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const documentSpace = state.wandMaskSpace === 'document';
  const lx = Math.floor(documentSpace ? cx : cx - off.x);
  const ly = Math.floor(documentSpace ? cy : cy - off.y);
  if (lx < 0 || ly < 0 || lx >= state.wandMask.width || ly >= state.wandMask.height) return false;
  try {
    const px = state.wandMask.getContext('2d').getImageData(lx, ly, 1, 1).data;
    return px[3] > 128;
  } catch { return false; }
}

function _runMagicWand(cx, cy, mode = 'replace', opts = {}) {
  if (!opts.retune && !opts.deferred) {
    const cleanup = _showWandLoading();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        try {
          _runMagicWand(cx, cy, mode, { ...opts, deferred: true });
        } finally {
          cleanup();
        }
      });
    });
    return;
  }
  const layer = activeLayer();
  if (!layer) return;
  // If an active mask sub-layer is selected, the wand operates on the
  // MASK pixels rather than the parent layer's pixels — lets the user
  // click inside / outside an existing mask to select that region for
  // further editing. Document-space masks use the document origin; true
  // layer masks use the parent layer origin plus their relative offset.
  const activeMask = _getActiveMaskLayer();
  const sourceCanvas = activeMask ? activeMask.canvas : layer.canvas;
  const sourceCtx = activeMask ? activeMask.ctx : layer.ctx;
  // Snapshot current state to undo BEFORE mutating the selection, but
  // skip when called via the tolerance slider (`opts.retune`) so dragging
  // the slider doesn't fill the undo stack with intermediate states.
  if (!opts.retune) _saveState();
  // Remember the seed so the tolerance slider can re-run the wand live.
  state.wandLastSeed = { x: cx, y: cy, mode };
  const layerOffset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const off = !activeMask || activeMask.mode === 'layer'
    ? {
        x: layerOffset.x + (activeMask?.offset?.x || 0),
        y: layerOffset.y + (activeMask?.offset?.y || 0),
      }
    : { x: 0, y: 0 };
  const lx = Math.floor(cx - off.x);
  const ly = Math.floor(cy - off.y);
  const w = sourceCanvas.width, h = sourceCanvas.height;
  if (lx < 0 || ly < 0 || lx >= w || ly >= h) return;
  // Read pixels from the chosen source. Bypass the cache when sourcing
  // from a mask — masks change frequently and the cache is keyed by
  // parent layer id, not by mask id.
  const src = activeMask
    ? sourceCtx.getImageData(0, 0, w, h).data
    : _getWandSource(layer).data;
  // Pixel-level flood fill lives in editor/tools/flood-fill.js.
  // Returns a mask canvas at (w × h) with white where the fill landed.
  const mask = _floodFillMask(src, w, h, lx, ly, state.wandTolerance);
  if (!mask) return;
  // Merge with existing selection per `mode`. If the existing mask is
  // for a different layer or has different dimensions, treat as replace
  // since merging doesn't make sense across canvases.
  _commitSelectionMask(mask, layer, mode, 'wand', off);
  composite();
  _syncToolClearIndicators();
}

async function _runSamSelection(e) {
  const layer = activeLayer();
  if (!layer) {
    if (uiModule) uiModule.showToast('Select a layer');
    return;
  }
  const coords = _canvasCoords(e, state.mainCanvas);
  const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const lx = Math.floor(coords.x - off.x);
  const ly = Math.floor(coords.y - off.y);
  if (lx < 0 || ly < 0 || lx >= layer.canvas.width || ly >= layer.canvas.height) return;

  let mode = state.wandMode || 'replace';
  if (e.shiftKey) mode = 'add';
  else if (e.altKey) mode = 'subtract';

  _cancelSamQuery(false);
  const controller = new AbortController();
  _samAbortController = controller;
  const cleanup = _showWandLoading();
  try {
    await _requestAndApplySamMask(layer, {
      points: [{ x: lx, y: ly, label: 1 }],
    }, mode, { x: coords.x, y: coords.y }, { signal: controller.signal });
  } catch (err) {
    if (err?.name !== 'AbortError' && uiModule) {
      uiModule.showToast(err.message || String(err), 7000);
    }
  } finally {
    cleanup();
    if (_samAbortController === controller) _samAbortController = null;
  }
}

async function _runSamTextSelection() {
  const layer = activeLayer();
  if (!layer) {
    if (uiModule) uiModule.showToast('Select a layer');
    return;
  }
  const input = document.getElementById('ge-sam-query');
  const text = (input?.value || '').trim();
  if (!text) {
    if (uiModule) uiModule.showToast('Type an object to find');
    input?.focus();
    return;
  }
  const btn = document.getElementById('ge-sam-find');
  const old = btn?.innerHTML;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="ge-btn-ai-mark" aria-hidden="true">✦</span>Finding…';
  }
  _cancelSamQuery(false);
  const controller = new AbortController();
  _samAbortController = controller;
  const cleanup = _showWandLoading();
  try {
    await _requestAndApplySamMask(layer, { text }, state.wandMode || 'replace', null, { signal: controller.signal });
  } catch (err) {
    if (err?.name !== 'AbortError' && uiModule) {
      uiModule.showToast(err.message || String(err), 7000);
    }
  } finally {
    cleanup();
    if (_samAbortController === controller) _samAbortController = null;
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = old || '<span class="ge-btn-ai-mark" aria-hidden="true">✦</span>Find';
    }
  }
}

async function _requestAndApplySamMask(layer, payload, mode, seedPoint, opts = {}) {
  const res = await fetch('/api/image/mask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: opts.signal,
      body: JSON.stringify({
        image: layer.canvas.toDataURL('image/png').split(',')[1],
        ...payload,
      }),
    });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.mask) {
    throw new Error(data.detail || data.error || `Mask failed (${res.status})`);
  }
  if (!data.bbox) {
    throw new Error(data.grounding ? `Found ${data.grounding.label || 'object'}, but SAM returned an empty mask` : 'SAM returned an empty mask');
  }

  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = () => reject(new Error('Failed to decode mask'));
    img.src = 'data:image/png;base64,' + data.mask;
  });
  const mask = document.createElement('canvas');
  mask.width = layer.canvas.width;
  mask.height = layer.canvas.height;
  const mctx = mask.getContext('2d');
  mctx.drawImage(img, 0, 0, mask.width, mask.height);
  const maskData = mctx.getImageData(0, 0, mask.width, mask.height);
  const md = maskData.data;
  for (let i = 0; i < md.length; i += 4) {
    const alpha = md[i]; // server mask is white selected / black unselected
    md[i] = 255;
    md[i + 1] = 255;
    md[i + 2] = 255;
    md[i + 3] = alpha;
  }
  mctx.putImageData(maskData, 0, 0);

  _saveState();
  _commitSelectionMask(mask, layer, mode, 'sam');
  state.wandLastSeed = seedPoint
    ? { x: seedPoint.x, y: seedPoint.y, mode, source: 'sam' }
    : { x: 0, y: 0, mode, source: 'sam-text' };
  state.wandMaskVisible = true;
  composite();
  _syncToolClearIndicators();
  if (data.grounding && uiModule) {
    const pct = Math.round((data.grounding.score || 0) * 100);
    uiModule.showToast(`Selected ${data.grounding.label || 'object'}${pct ? ` (${pct}%)` : ''}`, 2500);
  }
}

function _showWandLoading() {
  const area = state.container?.querySelector('.ge-canvas-area');
  if (!area) return () => {};
  const overlay = document.createElement('div');
  overlay.className = 'ge-wand-loading';
  let spinner = null;
  try {
    spinner = spinnerModule.createWhirlpool(30);
    spinner.element.style.cssText = 'width:30px;height:30px;margin:0;';
    overlay.appendChild(spinner.element);
  } catch (_) {
    overlay.textContent = 'Selecting...';
  }
  area.appendChild(overlay);
  return () => {
    try { spinner?.destroy?.(); } catch {}
    overlay.remove();
  };
}

// Draw the wand selection as a translucent red overlay, mirroring the
// inpaint-mask visual so users know what's selected.
function _drawWandOverlay() {
  if (!state.wandMask || !state.mainCtx) return;
  const layer = state.layers.find(l => l.id === state.wandLayerId);
  if (!layer) return;
  const off = state.wandMaskSpace === 'document'
    ? { x: 0, y: 0 }
    : (state.layerOffsets.get(layer.id) || { x: 0, y: 0 });
  // Tint the white mask red, draw at the layer's offset on the main canvas.
  const tint = document.createElement('canvas');
  tint.width = state.wandMask.width;
  tint.height = state.wandMask.height;
  const tc = tint.getContext('2d');
  tc.drawImage(state.wandMask, 0, 0);
  tc.globalCompositeOperation = 'source-in';
  tc.fillStyle = 'rgba(255, 60, 60, 1)';
  tc.fillRect(0, 0, tint.width, tint.height);
  state.mainCtx.save();
  state.mainCtx.globalAlpha = 0.4;
  state.mainCtx.drawImage(tint, off.x, off.y);
  state.mainCtx.globalAlpha = 1;
  state.mainCtx.restore();
}

function _wandClear() { _deselectSelection(); }

// Hover thumbnail — generated by downscaling the layer's canvas into a
// small floating panel. Lives in document.body so panel `overflow:
// hidden` can't clip it. One singleton element, repositioned per hover.
function _showLayerThumb(rowEl, layer) {
  if (!layer || !layer.canvas) return;
  if (!state.layerThumbEl) {
    state.layerThumbEl = document.createElement('div');
    state.layerThumbEl.className = 'ge-layer-thumb';
    document.body.appendChild(state.layerThumbEl);
  }
  const SIZE = 120;
  // Keep the hover preview consistent with the inline thumbnail: retained
  // effects and layer masks belong to the visible layer output, not the raw
  // source canvas.
  const preview = _renderLayerOutput(layer) || layer.canvas;
  // Downscale layer onto a small canvas, preserving aspect.
  const lw = preview.width, lh = preview.height;
  const scale = Math.min(SIZE / lw, SIZE / lh);
  const tw = Math.max(1, Math.round(lw * scale));
  const th = Math.max(1, Math.round(lh * scale));
  const c = document.createElement('canvas');
  c.width = tw; c.height = th;
  c.setAttribute('role', 'img');
  c.setAttribute('aria-label', `${layer.name || 'Layer'} preview`);
  // Checker bg so transparency reads
  const ctx = c.getContext('2d');
  const tile = 8;
  for (let y = 0; y < th; y += tile) for (let x = 0; x < tw; x += tile) {
    ctx.fillStyle = ((x / tile + y / tile) & 1) ? '#444' : '#333';
    ctx.fillRect(x, y, tile, tile);
  }
  ctx.drawImage(preview, 0, 0, tw, th);
  state.layerThumbEl.innerHTML = '';
  state.layerThumbEl.appendChild(c);
  // Position to the LEFT of the row so it doesn't cover other layers.
  const r = rowEl.getBoundingClientRect();
  state.layerThumbEl.style.top = Math.max(8, r.top - 4) + 'px';
  state.layerThumbEl.style.right = (window.innerWidth - r.left + 8) + 'px';
  state.layerThumbEl.style.left = '';
  state.layerThumbEl.style.display = 'block';
}
function _hideLayerThumb() {
  if (state.layerThumbEl) state.layerThumbEl.style.display = 'none';
}

// Ctrl-click on a layer thumbnail uses that layer's opaque pixels as a
// wand-style selection. Lifts pixel alpha > 0 into the wand mask so the
// user can immediately Bg-Remove / Erase / Copy through the layer.
function _loadLayerAlphaAsSelection(layer) {
  if (!layer || !layer.canvas) return;
  const rendered = _renderLayerOutput(layer) || layer.canvas;
  const w = rendered.width, h = rendered.height;
  const src = rendered.getContext('2d').getImageData(0, 0, w, h).data;
  const mask = document.createElement('canvas');
  mask.width = w; mask.height = h;
  const mctx = mask.getContext('2d');
  const mdata = mctx.createImageData(w, h);
  for (let i = 0; i < w * h; i++) {
    if (src[i * 4 + 3] > 0) {
      mdata.data[i * 4]     = 255;
      mdata.data[i * 4 + 1] = 255;
      mdata.data[i * 4 + 2] = 255;
      mdata.data[i * 4 + 3] = 255;
    }
  }
  mctx.putImageData(mdata, 0, 0);
  _saveState();
  _commitSelectionMask(mask, layer, 'replace', 'wand');
  _activateSelectionCanvas(state.wandMask, 'wand');
  if (uiModule) uiModule.showToast('Layer pixels selected');
}

function _loadMaskAsSelection(layer, mask) {
  if (!layer || !mask?.canvas) return;
  const layerOffset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const maskOffset = mask.offset || { x: 0, y: 0 };
  const offset = mask.mode === 'layer'
    ? {
        x: layerOffset.x + (Number(maskOffset.x) || 0),
        y: layerOffset.y + (Number(maskOffset.y) || 0),
      }
    : (mask.space === 'document' ? { x: 0, y: 0 } : layerOffset);
  const documentMask = _selectionMaskToDocument(
    mask.canvas,
    mask.space === 'document' ? 'document' : 'layer',
    offset,
    state.imgWidth,
    state.imgHeight,
  );
  _saveState(`Load mask "${mask.name || 'Mask'}" as selection`);
  _activateSelectionCanvas(documentMask, 'mask');
  uiModule?.showToast('Mask loaded as selection');
}

// Invert the active selection: lasso (point list — turn into a polygon
// covering the canvas with the lasso polygon as a hole) or wand (flip
// the mask alpha). Wired to Ctrl+Alt+I.
function _invertSelection() {
  if (state.wandMask && state.wandLayerId) {
    _saveState();
    const w = state.wandMask.width, h = state.wandMask.height;
    const ctx = state.wandMask.getContext('2d');
    const data = ctx.getImageData(0, 0, w, h);
    const d = data.data;
    for (let i = 0; i < d.length; i += 4) {
      const a = d[i + 3] > 128 ? 0 : 255;
      d[i] = 255; d[i + 1] = 255; d[i + 2] = 255; d[i + 3] = a;
    }
    ctx.putImageData(data, 0, 0);
    composite();
    if (uiModule) uiModule.showToast('Selection inverted');
    return true;
  }
  if (state.lassoPoints.length >= 3 && !state.lassoActive) {
    // Build polygon covering the whole canvas, with the lasso as a hole.
    // Easiest: convert lasso to wand mask, then invert.
    _saveState();
    const w = state.imgWidth, h = state.imgHeight;
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const cctx = c.getContext('2d');
    cctx.fillStyle = '#fff';
    cctx.fillRect(0, 0, w, h);
    cctx.globalCompositeOperation = 'destination-out';
    cctx.beginPath();
    cctx.moveTo(state.lassoPoints[0].x, state.lassoPoints[0].y);
    for (let i = 1; i < state.lassoPoints.length; i++) cctx.lineTo(state.lassoPoints[i].x, state.lassoPoints[i].y);
    cctx.closePath();
    cctx.fill();
    state.wandMask = c;
    state.wandLayerId = state.activeLayerId;
    state.wandMaskSpace = 'document';
    state.selectionSource = 'lasso';
    state.wandLastSeed = null;
    state.lassoPoints = [];
    state.lassoActive = false;
    composite();
    if (uiModule) uiModule.showToast('Selection inverted (converted to wand)');
    return true;
  }
  return false;
}

// Convert the wand selection into the inpaint mask, mirroring _lassoToMask.
// Switches to the inpaint tool so the user sees the result right away.
function _wandToMask() {
  if (!state.wandMask || !state.wandLayerId) return;
  const selectionLayer = state.layers.find(l => l.id === state.wandLayerId);
  const layer = activeLayer() || selectionLayer;
  if (!layer) return;
  const selectionOffset = selectionLayer
    ? (state.layerOffsets.get(selectionLayer.id) || { x: 0, y: 0 })
    : { x: 0, y: 0 };
  // Make the wand's parent active so the mask is attached to it, then
  // get-or-create a mask sub-layer on it. Repoint the global mask
  // plumbing at the new sub-layer's canvas/ctx.
  state.activeLayerId = layer.id;
  const mask = _ensureActiveMaskLayer();
  if (!mask) return;
  state.maskCanvas = mask.canvas;
  state.maskCtx = mask.ctx;
  // Refine the wand mask with the panel's Feather + Edge stroke values
  // before merging into the inpaint mask. Grow/shrink uses the same
  // blur+threshold dilate/erode as the lasso path; feather blurs the
  // result's alpha for a soft edge.
  const wFeather = parseInt(document.getElementById('ge-wand-feather')?.value || '0', 10);
  const wGrow = parseInt(document.getElementById('ge-wand-grow')?.value || '0', 10);
  let refinedWand = _selectionMaskToDocument(
    state.wandMask,
    state.wandMaskSpace || 'layer',
    selectionOffset,
    state.imgWidth,
    state.imgHeight,
  );
  if (wGrow !== 0) {
    const c = document.createElement('canvas');
    c.width = state.wandMask.width; c.height = state.wandMask.height;
    const bctx = c.getContext('2d');
    bctx.filter = `blur(${Math.abs(wGrow)}px)`;
    bctx.drawImage(state.wandMask, 0, 0);
    bctx.filter = 'none';
    const blurred = bctx.getImageData(0, 0, c.width, c.height).data;
    const out = bctx.createImageData(c.width, c.height);
    const od = out.data;
    const thr = wGrow > 0 ? 32 : 200;
    for (let i = 0; i < od.length; i += 4) {
      const a = blurred[i + 3] >= thr ? 255 : 0;
      od[i] = a; od[i + 1] = a; od[i + 2] = a; od[i + 3] = a;
    }
    bctx.putImageData(out, 0, 0);
    refinedWand = c;
  }
  if (wFeather > 0) {
    const c = document.createElement('canvas');
    c.width = refinedWand.width; c.height = refinedWand.height;
    const fctx = c.getContext('2d');
    fctx.filter = `blur(${wFeather}px)`;
    fctx.drawImage(refinedWand, 0, 0);
    fctx.filter = 'none';
    refinedWand = c;
  }
  // Draw the refined wand mask into the inpaint mask canvas at the
  // layer's offset. OR-like merge: any painted pixel in the wand mask
  // is added to the inpaint mask (max alpha wins). Matches the lasso
  // path's semantics.
  const tmp = document.createElement('canvas');
  tmp.width = state.maskCanvas.width;
  tmp.height = state.maskCanvas.height;
  const tctx = tmp.getContext('2d');
  tctx.drawImage(refinedWand, 0, 0);
  const incoming = tctx.getImageData(0, 0, tmp.width, tmp.height);
  const cur = state.maskCtx.getImageData(0, 0, state.maskCanvas.width, state.maskCanvas.height);
  for (let i = 0; i < incoming.data.length; i += 4) {
    if (incoming.data[i + 3] > cur.data[i + 3]) {
      cur.data[i]     = 255;
      cur.data[i + 1] = 255;
      cur.data[i + 2] = 255;
      cur.data[i + 3] = incoming.data[i + 3];
    }
  }
  state.maskCtx.putImageData(cur, 0, 0);
  // Stay on the Wand tool — just bake the selection into the mask.
  // Clear the wand selection so a re-click starts fresh (and the red
  // overlay doesn't double up over the inpaint-mask red tint).
  state.wandMask = null;
  state.wandLayerId = null;
  state.wandMaskSpace = 'layer';
  state.selectionSource = null;
  state.wandLastSeed = null;
  mask.visible = true;
  layer.activeMaskId = mask.id;
  state.maskVisible = true;
  composite();
  _renderLayerPanel();
  if (uiModule) uiModule.showToast('Selection added to mask');
}

function _autoMatchLastInpaintLayer() {
  const layer = state.layers.find(l => l.id === state.lastInpaintLayerId);
  const src = layer?.inpaintSource;
  if (!layer || !src?.base || !src?.mask) {
    if (uiModule) uiModule.showToast('Run inpaint first');
    return;
  }
  const w = state.imgWidth;
  const h = state.imgHeight;
  let baseData, resultData, maskData;
  try {
    baseData = src.base.getContext('2d').getImageData(0, 0, w, h).data;
    resultData = layer.canvas.getContext('2d').getImageData(0, 0, w, h).data;
    maskData = src.mask.getContext('2d').getImageData(0, 0, w, h).data;
  } catch (err) {
    if (uiModule) uiModule.showToast('Auto match failed: cannot read pixels');
    return;
  }

  const inside = { r: 0, g: 0, b: 0, y: 0, n: 0 };
  const outside = { r: 0, g: 0, b: 0, y: 0, n: 0 };
  const step = Math.max(1, Math.round(Math.max(w, h) / 900));
  const radius = Math.max(2, Math.round(Math.min(w, h) * 0.006));
  const sample = (bucket, data, idx) => {
    const r = data[idx], g = data[idx + 1], b = data[idx + 2];
    bucket.r += r; bucket.g += g; bucket.b += b;
    bucket.y += 0.2126 * r + 0.7152 * g + 0.0722 * b;
    bucket.n++;
  };
  const isMasked = (x, y) => {
    if (x < 0 || y < 0 || x >= w || y >= h) return false;
    return maskData[(y * w + x) * 4 + 3] > 24;
  };
  for (let y = radius; y < h - radius; y += step) {
    for (let x = radius; x < w - radius; x += step) {
      const idx = (y * w + x) * 4;
      const m = maskData[idx + 3] > 24;
      let touchesOther = false;
      for (let dy = -radius; dy <= radius && !touchesOther; dy += radius) {
        for (let dx = -radius; dx <= radius; dx += radius) {
          if (!dx && !dy) continue;
          if (isMasked(x + dx, y + dy) !== m) {
            touchesOther = true;
            break;
          }
        }
      }
      if (!touchesOther) continue;
      if (m && resultData[idx + 3] > 24) sample(inside, resultData, idx);
      else if (!m && baseData[idx + 3] > 24) sample(outside, baseData, idx);
    }
  }
  if (inside.n < 20 || outside.n < 20) {
    if (uiModule) uiModule.showToast('Auto match needs a larger mask edge');
    return;
  }
  for (const b of [inside, outside]) {
    b.r /= b.n; b.g /= b.n; b.b /= b.n; b.y /= b.n;
  }
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const dr = clamp((outside.r - inside.r) * 0.55, -55, 55);
  const dg = clamp((outside.g - inside.g) * 0.55, -55, 55);
  const db = clamp((outside.b - inside.b) * 0.55, -55, 55);
  const dy = clamp((outside.y - inside.y) * 0.35, -35, 35);
  if (!layer.adjLayers) layer.adjLayers = [];
  layer.adjLayers = layer.adjLayers.filter(a => a.id !== 'auto-match-color' && a.id !== 'auto-match-light');
  layer.adjLayers.push({
    id: 'auto-match-light',
    type: 'brightness-contrast',
    params: {
      brightness: clamp(1 + (dy / 255), 0.75, 1.25),
      contrast: 1,
    },
    opacity: 0.7,
    visible: true,
  });
  layer.adjLayers.push({
    id: 'auto-match-color',
    type: 'color-balance',
    params: {
      shadows: { r: dr * 0.45, g: dg * 0.45, b: db * 0.45 },
      midtones: { r: dr, g: dg, b: db },
      highlights: { r: dr * 0.35, g: dg * 0.35, b: db * 0.35 },
    },
    opacity: 0.75,
    visible: true,
  });
  _saveState('Auto match inpaint color');
  composite();
  _renderLayerPanel();
  if (uiModule) uiModule.showToast('Auto matched color');
}

// Reveal/hide the small "X" badge on the Lasso, Wand, and SAM tool buttons
// based on whether each tool currently holds a selection. Called from
// anywhere selection state mutates (wand click, lasso close, undo, etc.).
function _syncToolClearIndicators() {
  // Selection state drives:
  //   (1) the "from-selection" highlight on each layer's Add-mask btn
  //   (2) the visibility of the post-selection refine rows (Feather +
  //       Edge stroke) on the lasso / wand panels.
  //   (3) the topbar Fill button — visible whenever lasso/wand/active
  //       mask gives us a region to fill.
  const lassoHasSel = (state.lassoPoints.length >= 3 && !state.lassoActive)
    || (!!state.wandMask && state.selectionSource === 'lasso');
  const wandHasSel = !!state.wandMask;
  const hasMaskTarget = !!_getActiveMaskLayer();
  const hasSel = lassoHasSel || wandHasSel;
  _renderSavedSelectionsMenu?.();
  document.querySelectorAll('.ge-layer-mask-btn').forEach(b => {
    b.classList.toggle('from-selection', hasSel);
  });
  // Fill action now lives in the Image menu — enable when there's
  // something fillable (selection or active mask).
  const fillItem = document.getElementById('ge-image-action-fill');
  if (fillItem) {
    fillItem.disabled = !(hasSel || hasMaskTarget);
    fillItem.title = fillItem.disabled
      ? 'Make a selection or pick a mask first'
      : 'Fill the active selection / mask with the current color';
  }
  // Topbar Selection button only makes sense with an active selection.
  const edgeWrap = document.getElementById('ge-edge-wrap');
  if (edgeWrap) edgeWrap.hidden = !hasSel;
  const lFeather = document.getElementById('ge-lasso-refine-feather');
  const lGrow = document.getElementById('ge-lasso-refine-grow');
  if (lFeather) lFeather.style.display = lassoHasSel ? '' : 'none';
  if (lGrow) lGrow.style.display = lassoHasSel ? '' : 'none';
  const wFeather = document.getElementById('ge-wand-refine-feather');
  const wGrow = document.getElementById('ge-wand-refine-grow');
  if (wFeather) wFeather.style.display = wandHasSel ? '' : 'none';
  if (wGrow) wGrow.style.display = wandHasSel ? '' : 'none';
  if (!state.container) return;
  const marqueeBtn = state.container.querySelector('.ge-tool-btn[data-tool="marquee"]');
  const lassoBtn = state.container.querySelector('.ge-tool-btn[data-tool="lasso"]');
  const wandBtn  = state.container.querySelector('.ge-tool-btn[data-tool="wand"]');
  const samBtn   = state.container.querySelector('.ge-tool-btn[data-tool="sam"]');
  const inpaintBtn = state.container.querySelector('.ge-tool-btn[data-tool="inpaint"]');
  if (marqueeBtn) marqueeBtn.classList.toggle('has-selection', !!state.wandMask && state.selectionSource === 'marquee');
  if (lassoBtn) lassoBtn.classList.toggle('has-selection', lassoHasSel);
  if (wandBtn)  wandBtn.classList.toggle('has-selection', !!state.wandMask && state.selectionSource === 'wand');
  if (samBtn)   samBtn.classList.toggle('has-selection', !!state.wandMask && state.selectionSource === 'sam');
  // Inpaint no longer carries a clear-X badge; masks live as sub-layers
  // in the layer panel and are deleted from there.
  if (inpaintBtn) inpaintBtn.classList.remove('has-selection');
}

function _hasMaskPixels() {
  if (!state.maskCanvas || !state.maskCtx) return false;
  try {
    const d = state.maskCtx.getImageData(0, 0, state.maskCanvas.width, state.maskCanvas.height).data;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 0) return true;
  } catch (_) {}
  return false;
}

async function _canMutateLayerPixels(layer, action = 'editing pixels') {
  if (!layer) return false;
  if (layer.kind === 'adjustment') {
    uiModule?.showToast(`Select a pixel layer or mask before ${action}`);
    return false;
  }
  if (!['placed', 'text', 'shape'].includes(layer.kind)) return true;
  return _confirmRasterizeLayer(layer);
}

function _readPixelTarget() {
  const parent = activeLayer();
  const mask = _getActiveMaskLayer();
  const surface = mask || parent;
  if (!surface?.canvas) return null;
  const parentOffset = state.layerOffsets.get(parent?.id) || { x: 0, y: 0 };
  const offset = mask
    ? (mask.mode === 'layer' && mask.space !== 'document'
      ? { x: parentOffset.x + (mask.offset?.x || 0), y: parentOffset.y + (mask.offset?.y || 0) }
      : { x: 0, y: 0 })
    : parentOffset;
  return { parent, mask, canvas: surface.canvas, ctx: surface.ctx || surface.canvas.getContext('2d'), offset };
}

async function _preparePixelTarget(action, { erase = false } = {}) {
  const parent = activeLayer();
  const mask = _getActiveMaskLayer();
  const group = (state.layerGroups || []).find(item => item.id === state.activeGroupId);
  const ownerLocked = group && mask?.mode === 'group'
    ? group.locked || _groupAncestors(state, group).some(item => item.locked)
    : _isLayerEffectivelyLocked(state, parent);
  if (!parent && !mask) { uiModule?.showToast('Select a layer or mask first'); return null; }
  if (ownerLocked || mask?.locked || (!mask && _isLayerPixelLocked(state, parent))) {
    uiModule?.showToast('Unlock the selected layer or mask first'); return null;
  }
  if (!mask && erase && _isLayerTransparencyLocked(state, parent)) {
    uiModule?.showToast('Unlock transparent pixels before erasing'); return null;
  }
  if (!mask && !await _canMutateLayerPixels(parent, action)) return null;
  if (activeLayer() !== parent || _getActiveMaskLayer() !== mask) return null;
  return _readPixelTarget();
}

function _captureSelectedPixels(target) {
  const selection = _selectionMaskAsDocument({ materializeLasso: true });
  const canvas = document.createElement('canvas');
  canvas.width = target.canvas.width;
  canvas.height = target.canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(target.canvas, 0, 0);
  if (selection) {
    ctx.globalCompositeOperation = 'destination-in';
    ctx.drawImage(_selectionMaskForLayer(selection, 'document', target.offset, canvas.width, canvas.height), 0, 0);
  }
  return canvas;
}

async function _copyPixelsToClipboard({ cut = false } = {}) {
  const target = cut ? await _preparePixelTarget('cutting pixels', { erase: true }) : _readPixelTarget();
  if (!target) return;
  const canvas = _captureSelectedPixels(target);
  state.internalClipboard = canvas;
  state.internalClipboardOffset = { ...target.offset };
  if (cut) {
    if (state.wandMask) await _wandDeleteSelection({ message: 'Selection cut' });
    else {
      _saveState('Cut pixels');
      target.ctx.clearRect(0, 0, target.canvas.width, target.canvas.height);
      composite();
      _renderLayerPanel();
    }
  }
  canvas.toBlob(blob => {
    if (blob && navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
      navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
        .then(() => uiModule?.showToast(cut ? 'Cut to clipboard' : 'Copied to clipboard'))
        .catch(() => uiModule?.showToast(cut ? 'Cut (editor only)' : 'Copied (editor only)'));
    } else uiModule?.showToast(cut ? 'Cut (editor only)' : 'Copied (editor only)');
  }, 'image/png');
}

async function _wandDeleteSelection({ saveHistory = true, message = 'Selection deleted' } = {}) {
  if (!state.wandMask) return;
  const selection = _selectionMaskAsDocument({ materializeLasso: true });
  const layer = await _preparePixelTarget('erasing pixels', { erase: true });
  if (!layer || !selection) return;
  if (saveHistory) _saveState();
  const layerMask = _selectionMaskForLayer(
    selection,
    'document',
    layer.offset,
    layer.canvas.width,
    layer.canvas.height,
  );
  // Use destination-out with the mask to erase the selected pixels.
  layer.ctx.save();
  layer.ctx.globalCompositeOperation = 'destination-out';
  layer.ctx.drawImage(layerMask, 0, 0);
  layer.ctx.restore();
  _deselectSelection({ saveHistory: false, remember: false });
  _renderLayerPanel();
  uiModule?.showToast(message);
}

function _wandCopyToNewLayer({ saveHistory = true, activate = true, announce = true } = {}) {
  if (!_selectionMaskAsDocument({ materializeLasso: true })) return;
  const src = _readPixelTarget();
  if (!src) return;
  if (saveHistory) _saveState();
  const tmp = _captureSelectedPixels(src);
  const newLayer = createLayer('Selection', src.canvas.width, src.canvas.height);
  newLayer.ctx.drawImage(tmp, 0, 0);
  state.layerOffsets.set(newLayer.id, { ...src.offset });
  const idx = state.layers.findIndex(l => l.id === src.parent?.id);
  state.layers.splice(idx + 1, 0, newLayer);
  if (activate) {
    state.activeLayerId = newLayer.id;
    state.selectedLayerIds = [newLayer.id];
    state.activeGroupId = null;
  }
  composite();
  _renderLayerPanel();
  _revealLayerPanel();
  if (announce && uiModule) uiModule.showToast('Copied to new layer');
  return newLayer;
}

async function _lassoDeleteSelection() {
  if (!_selectionMaskAsDocument({ materializeLasso: true })) return;
  return _wandDeleteSelection();
}

function _lassoCopyToLayer() {
  return _wandCopyToNewLayer();
}

function _lassoToMask() {
  if (state.lassoPoints.length < 3) return;
  // Get-or-create a mask sub-layer on the active parent layer and
  // repoint the global mask plumbing at it.
  const mask = _ensureActiveMaskLayer();
  if (!mask) return;
  state.maskCanvas = mask.canvas;
  state.maskCtx = mask.ctx;

  // Fill selection into the mask with feather + grow/shrink applied.
  const feather = parseInt(document.getElementById('ge-lasso-feather')?.value || '0');
  const grow = parseInt(document.getElementById('ge-lasso-grow')?.value || '0');
  const lassoFill = _buildLassoMask(state.maskCanvas.width, state.maskCanvas.height, 0, 0, feather, grow);
  const maskData = lassoFill.getContext('2d').getImageData(0, 0, state.maskCanvas.width, state.maskCanvas.height);
  const curData = state.maskCtx.getImageData(0, 0, state.maskCanvas.width, state.maskCanvas.height);
  // Merge: add the new selection to existing mask
  for (let i = 0; i < maskData.data.length; i += 4) {
    const val = maskData.data[i];
    if (val > curData.data[i]) {
      curData.data[i] = val;
      curData.data[i + 1] = val;
      curData.data[i + 2] = val;
      curData.data[i + 3] = val;
    }
  }
  state.maskCtx.putImageData(curData, 0, 0);

  // Stay on the Lasso tool — just bake the selection into the mask.
  // Keep the lasso points so the user can keep tweaking; clear the
  // active-shape state so the next click starts fresh if they want.
  state.lassoPoints = [];
  composite();
  _renderLayerPanel();
  uiModule.showToast('Selection added to mask');
}

// ── Edge feather ──

// Themed slider modal for filter parameters. Builds a single in-line
// overlay anchored to the canvas-area's centre. `params` is an array
// of `{ key, label, min, max, step, value, suffix }` or a color control.
// As the user
// drags any slider the `onPreview(values)` callback fires for live
// rendering; clicking Apply commits and resolves the returned Promise
// with the final values; Cancel / Esc resolves with null. The caller
// is responsible for snapshotting state BEFORE opening (so Cancel can
// restore the layer's pixels).
let _activeFilterPrompt = null;

function _filterSliderPrompt(title, params, onPreview, onCancel) {
  _activeFilterPrompt?.cancel();
  return new Promise((resolve) => {
    if (!state.container) { resolve(null); return; }
    const session = state.editorSessionToken;
    const parent = _activeParentLayer();
    const mask = _getActiveMaskLayer();
    const groupId = state.activeGroupId;
    const previousFocus = document.activeElement;
    const isCurrent = () => state.editorOpen && state.editorSessionToken === session
      && _activeParentLayer() === parent && _getActiveMaskLayer() === mask
      && state.activeGroupId === groupId;
    const overlay = document.createElement('div');
    overlay.className = 'ge-filter-overlay';
    let rows = '';
    for (const p of params) {
      // Reuse the editor's eraser-row class so the slider picks up the
      // standard slim red-thumb styling instead of the bare browser
      // default. Value chip on the same line as the label.
      const isColor = p.type === 'color';
      rows += `
        <div class="ge-filter-row ge-eraser-row">
          <label>${p.label}${isColor ? '' : ` <span class="ge-filter-row-value" data-val-for="${p.key}">${p.value}${p.suffix || ''}</span>`}</label>
          ${isColor
            ? `<input type="color" data-key="${p.key}" value="${p.value}" aria-label="${p.label}" />`
            : `<input type="range" data-key="${p.key}" min="${p.min}" max="${p.max}" step="${p.step || 1}" value="${p.value}" />`}
        </div>
      `;
    }
    overlay.innerHTML = `
      <div class="ge-filter-modal" role="dialog" aria-modal="true" aria-label="${title}">
        <div class="ge-filter-modal-head">${title}</div>
        ${rows}
        <div class="ge-filter-modal-actions">
          <button type="button" class="ge-btn ge-btn-sm" data-action="cancel">Cancel</button>
          <button type="button" class="ge-btn ge-btn-sm ge-btn-primary" data-action="apply">Apply</button>
        </div>
      </div>
    `;
    state.container.appendChild(overlay);
    const values = {};
    for (const p of params) values[p.key] = p.value;
    // Initial preview render.
    try { onPreview(values); } catch {}
    overlay.querySelectorAll('input[data-key]').forEach(inp => {
      inp.addEventListener('input', (e) => {
        if (!isCurrent()) { cleanup(null); return; }
        const k = e.target.dataset.key;
        const param = params.find(p => p.key === k);
        const v = param?.type === 'color' ? e.target.value : parseFloat(e.target.value);
        values[k] = v;
        const lbl = overlay.querySelector(`[data-val-for="${k}"]`);
        if (lbl) lbl.textContent = v + (param && param.suffix ? param.suffix : '');
        try { onPreview(values); } catch {}
      });
    });
    let settled = false;
    const cleanup = (result) => {
      if (settled) return;
      settled = true;
      const current = isCurrent();
      if (result === null || !current) onCancel?.();
      observer.disconnect();
      try { overlay.remove(); } catch {}
      if (_activeFilterPrompt === prompt) _activeFilterPrompt = null;
      if (current && previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
      resolve(current ? result : null);
    };
    const onKey = (e) => {
      e.stopImmediatePropagation();
      if (e.key === 'Escape') { e.preventDefault(); cleanup(null); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        cleanup(e.target?.dataset.action === 'cancel' ? null : values);
      } else if (e.key === 'Tab') {
        const fields = [...overlay.querySelectorAll('input, button')];
        const index = fields.indexOf(document.activeElement);
        e.preventDefault();
        fields[(index + (e.shiftKey ? -1 : 1) + fields.length) % fields.length]?.focus();
      }
    };
    const prompt = { handleKey: onKey, cancel: () => cleanup(null) };
    _activeFilterPrompt = prompt;
    const observer = new MutationObserver(() => {
      if (!overlay.isConnected || !isCurrent()) cleanup(null);
    });
    observer.observe(state.container, { childList: true, subtree: true });
    overlay.querySelector('input, button')?.focus({ preventScroll: true });
    overlay.querySelector('[data-action="apply"]').addEventListener('click', () => cleanup(values));
    overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => cleanup(null));
    // Click outside the modal (on the dim backdrop) = cancel.
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(null); });
  });
}

// Preview from a fixed source; only acceptance creates a history entry.
async function _applyLiveBlur({ title, params, label, renderer }) {
  const layer = await _preparePixelTarget('applying a pixel filter');
  if (!layer) return;
  const w = layer.canvas.width, h = layer.canvas.height;
  const snap = document.createElement('canvas');
  snap.width = w; snap.height = h;
  snap.getContext('2d').drawImage(layer.canvas, 0, 0);
  const session = state.editorSessionToken;
  const isCurrent = () => state.editorOpen && state.editorSessionToken === session
    && _readPixelTarget()?.canvas === layer.canvas;
  const draw = (values) => {
    if (!isCurrent()) return;
    layer.ctx.clearRect(0, 0, w, h);
    try { renderer(snap, values, layer.ctx); } catch (_) { layer.ctx.drawImage(snap, 0, 0); }
    composite();
  };
  const restore = () => {
    layer.ctx.clearRect(0, 0, w, h);
    layer.ctx.drawImage(snap, 0, 0);
  };
  const result = await _filterSliderPrompt(title, params, draw, restore);
  restore();
  if (result === null || !isCurrent()) {
    if (state.editorSessionToken === session) composite();
    return;
  }
  _saveState(label);
  // Final render from snapshot for a clean commit.
  layer.ctx.clearRect(0, 0, w, h);
  renderer(snap, result, layer.ctx);
  composite();
  _renderLayerPanel();
  if (uiModule) uiModule.showToast(label + ' applied');
}

function _applyGaussianBlur() {
  _applyLiveBlur({
    title: 'Gaussian Blur',
    label: 'Gaussian Blur',
    params: [{ key: 'radius', label: 'Radius', min: 0, max: 100, step: 1, value: 6, suffix: 'px' }],
    renderer: _gaussianBlur,
  });
}

async function _addRetainedGaussianBlur(presetName = null) {
  const layer = state.activeGroupId
    ? (state.layerGroups || []).find(group => group.id === state.activeGroupId)
    : activeLayer();
  if (!layer || layer.kind === 'adjustment') {
    uiModule?.showToast?.('Select an image layer or group first');
    return;
  }
  const preset = _effectPreset(presetName);
  const initial = [{ key: 'radius', label: 'Radius', min: 0, max: 100, step: 1, value: preset?.type === 'gaussian-blur' ? preset.params.radius : 6, suffix: 'px' }];
  const result = await _filterSliderPrompt(
    'Retained Gaussian Blur',
    initial,
    values => {
      layer._effectPreview = { type: 'gaussian-blur', params: values, opacity: 1, visible: true };
      composite();
    },
  );
  layer._effectPreview = null;
  composite();
  if (!result) return;
  _saveState('Add retained Gaussian Blur');
  if (!Array.isArray(layer.effects)) layer.effects = [];
  layer.effects.push(_normalizeEffect({ type: 'gaussian-blur', params: result }));
  _schedulePersist();
  _renderLayerPanel();
  composite();
  uiModule?.showToast?.('Retained Gaussian Blur added');
}

async function _addRetainedEffect(type, presetName = null) {
  if (type === 'gaussian-blur') return _addRetainedGaussianBlur(presetName);
  const layer = state.activeGroupId
    ? (state.layerGroups || []).find(group => group.id === state.activeGroupId)
    : activeLayer();
  if (!layer || layer.kind === 'adjustment') {
    uiModule?.showToast?.('Select an image layer or group first');
    return;
  }
  if (LAYER_STYLES[type]) return _configureLayerStyle(layer, type);
  const initial = type === 'color-overlay'
    ? [
      { key: 'color', label: 'Color', type: 'color', value: '#ffffff' },
      { key: 'opacity', label: 'Opacity', min: 0, max: 100, step: 1, value: 20, suffix: '%' },
    ]
    : type === 'sharpen'
      ? [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 50, suffix: '%' }]
    : type === 'stroke'
      ? [
        { key: 'color', label: 'Color', type: 'color', value: '#ffffff' },
        { key: 'opacity', label: 'Opacity', min: 0, max: 100, step: 1, value: 100, suffix: '%' },
        { key: 'width', label: 'Width', min: 0, max: 100, step: 1, value: 3, suffix: 'px' },
      ]
    : [
      { key: 'color', label: 'Color', type: 'color', value: '#000000' },
      { key: 'opacity', label: 'Opacity', min: 0, max: 100, step: 1, value: 45, suffix: '%' },
      { key: 'blur', label: 'Blur', min: 0, max: 100, step: 1, value: 12, suffix: 'px' },
      { key: 'x', label: 'Horizontal', min: -100, max: 100, step: 1, value: 4, suffix: 'px' },
      { key: 'y', label: 'Vertical', min: -100, max: 100, step: 1, value: 6, suffix: 'px' },
    ];
  const preset = _effectPreset(presetName);
  if (preset?.type === type) {
    for (const control of initial) {
      const value = preset.params[control.key];
      if (value == null) continue;
      control.value = control.type === 'color' ? value : Number(value) * (control.suffix === '%' ? 100 : 1);
    }
  }
  const result = await _filterSliderPrompt(
    type === 'color-overlay' ? 'Retained Color Overlay' : type === 'sharpen' ? 'Retained Sharpen' : type === 'stroke' ? 'Retained Stroke' : 'Retained Drop Shadow',
    initial,
    values => {
      layer._effectPreview = {
        type,
        opacity: 1,
        visible: true,
        params: type === 'sharpen'
          ? { amount: values.amount / 100 }
          : type === 'color-overlay'
          ? { color: values.color, opacity: values.opacity / 100, blendMode: 'source-atop' }
          : type === 'stroke'
            ? { color: values.color, opacity: values.opacity / 100, width: values.width }
            : { color: values.color, opacity: values.opacity / 100, blur: values.blur, x: values.x, y: values.y },
      };
      composite();
    },
  );
  layer._effectPreview = null;
  composite();
  if (!result) return;
  _saveState(`Add retained ${type === 'color-overlay' ? 'Color Overlay' : 'Drop Shadow'}`);
  if (!Array.isArray(layer.effects)) layer.effects = [];
  layer.effects.push(_normalizeEffect({
    type,
    params: type === 'sharpen'
      ? { amount: result.amount / 100 }
      : type === 'color-overlay'
      ? { color: result.color, opacity: result.opacity / 100, blendMode: 'source-atop' }
      : type === 'stroke'
        ? { color: result.color, opacity: result.opacity / 100, width: result.width }
        : { color: result.color, opacity: result.opacity / 100, blur: result.blur, x: result.x, y: result.y },
  }));
  _schedulePersist();
  _renderLayerPanel();
  composite();
  uiModule?.showToast?.(`${type === 'color-overlay' ? 'Color Overlay' : 'Drop Shadow'} added`);
}

async function _configureLayerStyle(layer, type, existing = null) {
  if (_isLayerEffectivelyLocked(state, layer)) { uiModule?.showToast('Unlock the layer first'); return; }
  const session = state.editorSessionToken;
  const initial = styleParams(type, existing?.params);
  const controls = LAYER_STYLES[type].controls.map(control => ({ ...control, value: initial[control.key] }));
  const result = await _filterSliderPrompt(LAYER_STYLES[type].label, controls, values => {
    layer._effectPreview = { ...existing, type, visible: true, opacity: existing?.opacity ?? 1, params: styleParams(type, values) };
    composite();
  });
  layer._effectPreview = null;
  composite();
  if (!result || session !== state.editorSessionToken || !state.editorOpen ||
      !(state.layers.includes(layer) || state.layerGroups?.includes(layer))) return;
  _saveState(`${existing ? 'Edit' : 'Add'} ${LAYER_STYLES[type].label}`);
  if (existing) existing.params = styleParams(type, result);
  else {
    layer.effects ||= [];
    layer.effects.push(_normalizeEffect({ type, params: styleParams(type, result) }));
  }
  _schedulePersist(); _renderLayerPanel(); composite();
}

async function _editRetainedGradient(layer, effect) {
  const params = effect.params || {};
  const stops = Array.isArray(params.stops) && params.stops.length >= 2
    ? params.stops
    : [{ position: 0, color: '#e06c75', alpha: 1 }, { position: 100, color: '#ffffff', alpha: 1 }];
  const start = stops[0];
  const end = stops[stops.length - 1];
  const initial = [
    { key: 'start', label: 'Start', type: 'color', value: start.color },
    { key: 'end', label: 'End', type: 'color', value: end.color },
    { key: 'opacity', label: 'Opacity', min: 0, max: 100, step: 1, value: Math.round((Number(params.opacity) || 0) * 100), suffix: '%' },
    { key: 'x1', label: 'Start X', min: -2000000, max: 2000000, step: 1, value: Number(params.x1) || 0, suffix: 'px' },
    { key: 'y1', label: 'Start Y', min: -2000000, max: 2000000, step: 1, value: Number(params.y1) || 0, suffix: 'px' },
    { key: 'x2', label: 'End X', min: -2000000, max: 2000000, step: 1, value: Number(params.x2) || 0, suffix: 'px' },
    { key: 'y2', label: 'End Y', min: -2000000, max: 2000000, step: 1, value: Number(params.y2) || 0, suffix: 'px' },
  ];
  stops.slice(1, -1).forEach((stop, index) => {
    initial.push(
      { key: `stopColor${index}`, label: `Stop ${index + 2}`, type: 'color', value: stop.color },
      { key: `stopPosition${index}`, label: `Stop ${index + 2} position`, min: 1, max: 99, step: 1, value: Number(stop.position) || 50, suffix: '%' },
    );
  });
  const buildParams = values => ({
    x1: values.x1, y1: values.y1, x2: values.x2, y2: values.y2,
    opacity: values.opacity / 100,
    stops: stops.map((stop, index) => ({
      ...stop,
      color: index === 0 ? values.start
        : index === stops.length - 1 ? values.end
          : values[`stopColor${index - 1}`] || stop.color,
      position: index === 0 ? 0
        : index === stops.length - 1 ? 100
          : Number(values[`stopPosition${index - 1}`]) || stop.position,
    })),
  });
  const result = await _filterSliderPrompt('Edit Gradient', initial, values => {
    layer._effectPreview = { ...effect, params: buildParams(values) };
    composite();
  });
  layer._effectPreview = null;
  composite();
  if (!result) return;
  _saveState('Edit Gradient');
  effect.params = buildParams(result);
  composite();
  _schedulePersist();
  _renderLayerPanel();
  uiModule?.showToast?.('Gradient updated');
}

async function _editRetainedEffect(layer, effect) {
  if (!layer || !effect) return;
  const type = effect.type;
  if (LAYER_STYLES[type]) return _configureLayerStyle(layer, type, effect);
  if (type === 'linear-gradient' || type === 'radial-gradient') return _editRetainedGradient(layer, effect);
  const p = effect.params || {};
  const initial = type === 'gaussian-blur'
    ? [{ key: 'radius', label: 'Radius', min: 0, max: 100, step: 1, value: Number(p.radius) || 0, suffix: 'px' }]
    : type === 'sharpen'
      ? [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: Math.round((Number(p.amount) || 0) * 100), suffix: '%' }]
    : type === 'color-overlay'
      ? [
        { key: 'color', label: 'Color', type: 'color', value: p.color || '#ffffff' },
        { key: 'opacity', label: 'Opacity', min: 0, max: 100, step: 1, value: Math.round((Number(p.opacity) || 0) * 100), suffix: '%' },
      ]
      : type === 'stroke'
        ? [
          { key: 'color', label: 'Color', type: 'color', value: p.color || '#ffffff' },
          { key: 'opacity', label: 'Opacity', min: 0, max: 100, step: 1, value: Math.round((Number(p.opacity) || 0) * 100), suffix: '%' },
          { key: 'width', label: 'Width', min: 0, max: 100, step: 1, value: Number(p.width) || 0, suffix: 'px' },
        ]
      : [
        { key: 'color', label: 'Color', type: 'color', value: p.color || '#000000' },
        { key: 'opacity', label: 'Opacity', min: 0, max: 100, step: 1, value: Math.round((Number(p.opacity) || 0) * 100), suffix: '%' },
        { key: 'blur', label: 'Blur', min: 0, max: 100, step: 1, value: Number(p.blur) || 0, suffix: 'px' },
        { key: 'x', label: 'Horizontal', min: -100, max: 100, step: 1, value: Number(p.x) || 0, suffix: 'px' },
        { key: 'y', label: 'Vertical', min: -100, max: 100, step: 1, value: Number(p.y) || 0, suffix: 'px' },
      ];
  const preview = values => {
    const params = type === 'gaussian-blur'
      ? { radius: values.radius }
      : type === 'sharpen'
        ? { amount: values.amount / 100 }
      : type === 'color-overlay'
        ? { color: values.color, opacity: values.opacity / 100, blendMode: p.blendMode || 'source-atop' }
        : type === 'stroke'
          ? { color: values.color, opacity: values.opacity / 100, width: values.width }
        : { color: values.color, opacity: values.opacity / 100, blur: values.blur, x: values.x, y: values.y };
    layer._effectPreview = { ...effect, params };
    composite();
  };
  const result = await _filterSliderPrompt(`Edit ${_effectLabel(type)}`, initial, preview);
  layer._effectPreview = null;
  composite();
  if (!result) return;
  _saveState(`Edit ${_effectLabel(type)}`);
  effect.params = type === 'gaussian-blur'
    ? { radius: result.radius }
    : type === 'sharpen'
      ? { amount: result.amount / 100 }
    : type === 'color-overlay'
      ? { color: result.color, opacity: result.opacity / 100, blendMode: p.blendMode || 'source-atop' }
      : type === 'stroke'
        ? { color: result.color, opacity: result.opacity / 100, width: result.width }
      : { color: result.color, opacity: result.opacity / 100, blur: result.blur, x: result.x, y: result.y };
  composite();
  _schedulePersist();
  _renderLayerPanel();
  uiModule?.showToast?.(`${_effectLabel(type)} updated`);
}

function _addEffectMask(layer, effect) {
  if (!layer || !effect) return;
  const selection = _selectionMaskAsDocument({ materializeLasso: true });
  if (!selection) {
    uiModule?.showToast?.('Create a selection before adding an effect mask');
    return;
  }
  const layerOffset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const canvas = document.createElement('canvas');
  canvas.width = layer.canvas.width;
  canvas.height = layer.canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(selection, -layerOffset.x, -layerOffset.y, state.imgWidth, state.imgHeight);
  _saveState(`Add mask to ${_effectLabel(effect.type)}`);
  effect.mask = {
    id: `effect-mask-${state.nextLayerId++}`,
    name: 'Effect Mask',
    visible: true,
    canvas,
    ctx,
    canvasW: canvas.width,
    canvasH: canvas.height,
  };
  _schedulePersist();
  composite();
  _renderLayerPanel();
  uiModule?.showToast?.(`${_effectLabel(effect.type)} mask added from selection`);
}

async function _rasterizeEffects(layer) {
  if (!layer || layer.kind === 'adjustment' || !Array.isArray(layer.effects) || !layer.effects.length) return;
  const base = _renderLayerWithAdjLayers(layer);
  _showCanvasLoading('Rasterizing effects…');
  let baked;
  try {
    // Rasterization must use the same renderer as export/composite. The
    // worker path can apply browser color/shadow operations slightly
    // differently, which makes a visually identical bake fail fidelity
    // checks and surprises users when they commit an effect.
    baked = _renderEffects(base, layer.effects);
  } catch (error) {
    _hideCanvasLoading();
    uiModule?.showToast?.(`Could not rasterize effects: ${error.message || error}`);
    return;
  }
  _hideCanvasLoading();
  _saveState(`Rasterize effects on "${layer.name}"`);
  layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
  layer.ctx.drawImage(baked, 0, 0, layer.canvas.width, layer.canvas.height);
  layer.kind = 'raster';
  layer.text = null;
  layer.shape = null;
  layer.placed = null;
  layer.effects = [];
  layer._effectPreview = null;
  layer._adjFinalKey = null;
  composite();
  _schedulePersist();
  _renderLayerPanel();
  uiModule?.showToast?.('Effects rasterized');
}

function _applyZoomBlur() {
  _applyLiveBlur({
    title: 'Zoom Blur',
    label: 'Zoom Blur',
    params: [{ key: 'strength', label: 'Strength', min: 1, max: 50, step: 1, value: 15 }],
    renderer: _zoomBlur,
  });
}

function _applyMotionBlur() {
  _applyLiveBlur({
    title: 'Motion Blur',
    label: 'Motion Blur',
    params: [
      { key: 'length', label: 'Length', min: 1, max: 200, step: 1, value: 20, suffix: 'px' },
      { key: 'angle', label: 'Angle', min: -180, max: 180, step: 1, value: 0, suffix: '°' },
    ],
    renderer: _motionBlur,
  });
}

async function _applyEdgeFeather(layer, width, hardDelete) {
  if (!await _canMutateLayerPixels(layer, 'feathering pixels')) return false;
  _saveState(hardDelete ? 'Delete edges' : 'Feather edges');
  const w = layer.canvas.width;
  const h = layer.canvas.height;
  const imgData = layer.ctx.getImageData(0, 0, w, h);
  _edgeFeather(imgData, width, hardDelete);
  layer.ctx.putImageData(imgData, 0, 0);
  return true;
}

// ── Zoom ──

function _fitZoom() {
  const fit = _getFitZoom();
  if (!fit) return;
  state.zoom = fit;
  state.zoomViewMode = 'fit';
  _applyZoom({ resetPan: true });
}

function _getFitZoom() {
  const area = state.container.querySelector('.ge-canvas-area');
  if (!area || !state.imgWidth) return null;
  const pad = 20;
  const rulerPad = state.rulersVisible ? 18 : 0;
  const maxW = area.clientWidth - pad * 2 - rulerPad;
  const maxH = area.clientHeight - pad * 2 - rulerPad;
  return Math.min(1, maxW / state.imgWidth, maxH / state.imgHeight);
}

function _applyZoom({ resetPan = false } = {}) {
  if (!state.mainCanvas) return;
  state.mainCanvas.style.width = (state.imgWidth * state.zoom) + 'px';
  state.mainCanvas.style.height = (state.imgHeight * state.zoom) + 'px';
  const label = state.container.querySelector('.ge-zoom-label');
  if (label) label.textContent = Math.round(state.zoom * 100) + '%';
  _syncZoomControls();
  const area = state.container && state.container.querySelector('.ge-canvas-area');
  if (resetPan && area && area._resetPan) area._resetPan();
  _syncSelectionOverlay();
  _precisionGuides?.redrawRulers();
}

function _buildQuickMaskBar() {
  const bar = document.createElement('div');
  bar.id = 'ge-quick-mask-bar';
  bar.className = 'ge-quick-mask-bar';
  bar.hidden = true;
  bar.innerHTML = `
    <span class="ge-quick-mask-label">Quick Mask</span>
    <div class="ge-quick-mask-tools" role="group" aria-label="Quick Mask brush mode">
      <button type="button" class="ge-quick-mask-tool active" data-tool="brush">Brush</button>
      <button type="button" class="ge-quick-mask-tool" data-tool="eraser">Erase</button>
    </div>
    <button type="button" class="ge-quick-mask-done">Done</button>`;
  bar.querySelectorAll('.ge-quick-mask-tool').forEach(button => {
    button.addEventListener('click', () => {
      bar.querySelectorAll('.ge-quick-mask-tool').forEach(item => item.classList.toggle('active', item === button));
      _clickToolButton(button.dataset.tool);
    });
  });
  bar.querySelector('.ge-quick-mask-done')?.addEventListener('click', () => _setQuickMaskActive(false));
  return bar;
}

function _setQuickMaskActive(active) {
  const next = !!active;
  if (next && !_selectionMaskAsDocument({ materializeLasso: true })) {
    uiModule?.showToast('Make a selection before using Quick Mask');
    return false;
  }
  state.quickMaskActive = next;
  state.wandMaskVisible = true;
  const bar = document.getElementById('ge-quick-mask-bar');
  if (bar) bar.hidden = !next;
  document.querySelectorAll('.ge-quick-mask-toggle').forEach(button => {
    button.classList.toggle('active', next);
    button.setAttribute('aria-pressed', next ? 'true' : 'false');
  });
  if (next) {
    bar?.querySelectorAll('.ge-quick-mask-tool').forEach(item => item.classList.toggle('active', item.dataset.tool === 'brush'));
    _clickToolButton('brush');
  }
  composite();
  return true;
}

function _toggleQuickMask() {
  return _setQuickMaskActive(!state.quickMaskActive);
}

function _nudgeSelectionBoundary(dx, dy) {
  const mask = _selectionMaskAsDocument({ materializeLasso: true });
  if (!mask) return false;
  if (!_selectionNudgeHistorySaved) {
    _saveState('Nudge selection boundary');
    _selectionNudgeHistorySaved = true;
  }
  state.wandMask = _translateSelectionMask(mask, dx, dy, state.imgWidth, state.imgHeight);
  state.wandMaskSpace = 'document';
  state.wandLastSeed = null;
  state.savedSelections = [];
  state.lastSelection = null;
  state.nextSavedSelectionId = 1;
  clearTimeout(_selectionNudgeTimer);
  _selectionNudgeTimer = setTimeout(() => { _selectionNudgeHistorySaved = false; }, 300);
  composite();
  return true;
}

function _syncZoomControls() {
  const actualBtn = document.getElementById('ge-zoom-100');
  const fit = _getFitZoom();
  const isFit = fit !== null && Math.abs(state.zoom - fit) < 0.001 &&
    (fit < 0.999 || state.zoomViewMode !== 'scale');
  if (actualBtn) {
    const isActual = !isFit && Math.abs(state.zoom - 1) < 0.001;
    actualBtn.classList.toggle('active', isFit || isActual);
    actualBtn.setAttribute('aria-pressed', isActual ? 'true' : 'false');
    actualBtn.title = isActual ? 'Switch to fit view' : 'Switch to actual size';
    actualBtn.setAttribute('aria-label', isActual ? 'Actual size; switch to fit view' : 'Fit view; switch to actual size');
    const label = actualBtn.querySelector('.ge-stacked-label');
    if (label) label.textContent = isActual ? 'SCALE' : 'FIT';
    const glyph = actualBtn.querySelector('.ge-stacked-glyph');
    if (glyph) glyph.innerHTML = isActual ? '1:1' : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 14 4 20 10 20"/><polyline points="20 10 20 4 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
  }
}

let _inpaintPanelPosition = null;

function _applyInpaintPanelPosition(panel, position) {
  const left = Math.max(8, Math.min(window.innerWidth - panel.offsetWidth - 8, position.left));
  const top = Math.max(8, Math.min(window.innerHeight - panel.offsetHeight - 8, position.top));
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
  return { left, top };
}

function _positionInpaintPanel(anchorBtn) {
  const panel = document.getElementById('ge-inpaint-section');
  if (!panel || window.innerWidth <= 820) return;
  if (_inpaintPanelPosition) {
    panel.classList.add('ge-inpaint-popover');
    panel.dataset.userMoved = '1';
    _inpaintPanelPosition = _applyInpaintPanelPosition(panel, _inpaintPanelPosition);
    return;
  }
  panel.classList.add('ge-inpaint-popover');
  // Anchor to the Layers header on the right panel so the popover
  // appears to slide out from there. The toolbar button on the left
  // shifts around as controls reflow, which was causing the popover
  // to land in different spots on each open and look "jumpy" when the
  // user grabbed it to move. Anchoring to the right panel — which has
  // a stable position — keeps the docked appearance steady.
  const layersHeader = document.querySelector('.ge-layers-header');
  const rightPanel = document.querySelector('.ge-right-panel');
  const ref = layersHeader || rightPanel;
  if (!ref) {
    // Fallback to the old toolbar-button anchor if the layers panel
    // isn't on screen yet.
    const r = anchorBtn?.getBoundingClientRect?.();
    if (!r) return;
    requestAnimationFrame(() => {
      if (!panel.isConnected || _inpaintPanelPosition || panel.dataset.userMoved === '1') return;
      const panelW = panel.offsetWidth || 320;
      const panelH = panel.offsetHeight || 520;
      const left = Math.min(window.innerWidth - panelW - 12, Math.max(12, r.right + 10));
      const top = Math.min(window.innerHeight - panelH - 12, Math.max(12, r.top));
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
    });
    return;
  }
  requestAnimationFrame(() => {
    if (!panel.isConnected || _inpaintPanelPosition || panel.dataset.userMoved === '1') return;
    const refRect = ref.getBoundingClientRect();
    const panelW = panel.offsetWidth || 320;
    const panelH = panel.offsetHeight || 520;
    // Sit immediately to the left of the right panel, top-aligned with
    // the Layers header. 10px gap so it's clearly a separate window
    // and not visually fused with the panel.
    let left = refRect.left - panelW - 10;
    let top = refRect.top;
    // Clamp into the viewport so the popover never leaves the screen.
    left = Math.max(12, Math.min(window.innerWidth - panelW - 12, left));
    top = Math.max(12, Math.min(window.innerHeight - panelH - 12, top));
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  });
}

function _wireInpaintPopoverWindow() {
  const panel = document.getElementById('ge-inpaint-section');
  if (!panel || panel.dataset.windowWired === '1') return;
  panel.dataset.windowWired = '1';
  const closeBtn = document.getElementById('ge-inpaint-popover-close');
  closeBtn?.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    panel.classList.add('dismissed');
    panel.style.display = 'none';
    document.getElementById('ge-controls')?.classList.remove('ge-inpaint-popover-host');
  });
  const head = panel.querySelector('[data-inpaint-drag]');
  if (!head) return;
  head.addEventListener('pointerdown', (e) => {
    if (window.innerWidth <= 820 || e.button !== 0 || e.target.closest('button')) return;
    e.preventDefault();
    e.stopPropagation();
    panel.classList.add('ge-inpaint-popover');
    const startX = e.clientX;
    const startY = e.clientY;
    const r0 = panel.getBoundingClientRect();
    // Claim the position immediately so a pending auto-placement cannot
    // overwrite this drag, and retain it when the editor rebuilds its DOM.
    panel.dataset.userMoved = '1';
    _inpaintPanelPosition = { left: r0.left, top: r0.top };
    head.setPointerCapture(e.pointerId);
    head.style.cursor = 'grabbing';
    const onMove = (ev) => {
      const w = panel.offsetWidth || r0.width;
      const h = panel.offsetHeight || r0.height;
      const nx = Math.max(8, Math.min(window.innerWidth - w - 8, r0.left + ev.clientX - startX));
      const ny = Math.max(8, Math.min(window.innerHeight - h - 8, r0.top + ev.clientY - startY));
      panel.dataset.userMoved = '1';
      panel.style.left = `${nx}px`;
      panel.style.top = `${ny}px`;
      _inpaintPanelPosition = { left: nx, top: ny };
    };
    const onUp = () => {
      try { head.releasePointerCapture(e.pointerId); } catch {}
      head.style.cursor = '';
      head.removeEventListener('pointermove', onMove);
      head.removeEventListener('pointerup', onUp);
      head.removeEventListener('pointercancel', onUp);
      head.removeEventListener('lostpointercapture', onUp);
    };
    head.addEventListener('pointermove', onMove);
    head.addEventListener('pointerup', onUp);
    head.addEventListener('pointercancel', onUp);
    head.addEventListener('lostpointercapture', onUp);
  });
}

// ── Build DOM ──

function _buildEditor(container) {
  document.querySelectorAll('body > #ge-save-menu').forEach(el => el.remove());
  container.innerHTML = '';
  container.className = 'gallery-editor';

  // Toolbar (left) — DOM construction lives in editor/build/toolbar.js;
  // the big tool-switch handler stays here so it can touch module state.
  let canvasNavigation = null;
  const { toolbar, toolKeyMap: _toolKeyMap } = _buildToolbar({
    currentTool: state.tool,
    onClearSelection: (which) => {
      if (which === 'lasso') {
        state.lassoPoints = [];
        state.lassoActive = false;
        composite();
      } else if (which === 'marquee' || which === 'wand') {
        _wandClear();
      } else if (which === 'sam') {
        _openSamPrompt();
        return;
      }
      _syncToolClearIndicators();
    },
    onSelectTool: (toolId, _btn, toolbarEl) => {
      if (state.tool !== toolId) {
        _penTool.cancel();
        // Finish a paint gesture before its tool identity changes.
        if (state.drawing) _strokeTool.tryEnd();
        _cropTool.cancel('tool-switch');
        _marqueeTool.cancel('tool-switch');
        if (state.gradientActive) _gradientTool.cancel();
        _transformDragTool.tryEnd();
      }
      // Leaving transform mode without confirm? Treat tool change as confirm.
      if (state.transformActive && toolId !== 'transform') _confirmTransform();
      if (toolId !== 'text' && _textEditor?.isOpen()) _textEditor.close(true);
      // Re-clicking the active tool toggles the mobile control sheet —
      // lets the user swipe-down to dismiss, then tap the tool again to
      // bring it back. On desktop this is a no-op visually since the
      // controls live in the right panel.
      const reactivated = state.tool === toolId;
      state.tool = toolId;
      void _offerRasterizeForTool(toolId);
      state.hoveredHandle = null;
      const controls = document.getElementById('ge-controls') || document.querySelector('.ge-controls');
      if (controls) {
        if (reactivated && window.innerWidth <= 820) controls.classList.toggle('dismissed');
        else controls.classList.remove('dismissed');
      }
      // On mobile, picking a tool that's about to SHOW its controls
      // panel auto-minimises the layers sheet so the controls aren't
      // covered. Swiping the layers handle back up restores it.
      const isMobile = window.innerWidth <= 820;
      const hasToolControls = ['move', 'transform', 'brush', 'gradient', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'eyedropper', 'text', 'shape', 'marquee', 'lasso', 'pen', 'wand', 'inpaint', 'sam'].includes(toolId);
      const controlsVisible = controls && !controls.classList.contains('dismissed');
      if (isMobile && hasToolControls && controlsVisible) {
        const rp = document.querySelector('.ge-right-panel');
        if (rp) {
          rp.classList.remove('expanded');
          rp.classList.add('minimized');
        }
      }
      toolbarEl.querySelectorAll('.ge-tool-btn').forEach(b => b.classList.toggle('active', b.dataset.tool === state.tool));
      // Activate drag-resize handles when picking the Resize tool
      if (toolId === 'transform' && !state.transformActive) _startTransform();
      // Show/hide brush controls. Brush, Eraser AND Clone use the
      // shared size+color row; Inpaint has its OWN size slider.
      const brushControls = document.getElementById('ge-brush-controls');
      const needsBrush = ['brush', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn'].includes(toolId);
      if (brushControls) brushControls.style.display = needsBrush ? '' : 'none';
      const textSection = document.getElementById('ge-text-section');
      const penSection = document.getElementById('ge-pen-section');
      if (penSection) penSection.style.display = toolId === 'pen' ? '' : 'none';
      if (textSection) textSection.style.display = toolId === 'text' ? '' : 'none';
      if (toolId === 'text') _syncTextControls();
      const shapeSection = document.getElementById('ge-shape-section');
      if (shapeSection) shapeSection.style.display = toolId === 'shape' ? '' : 'none';
      if (toolId === 'shape') _syncShapeControls();
      const gradientSection = document.getElementById('ge-gradient-section');
      if (gradientSection) gradientSection.style.display = toolId === 'gradient' ? '' : 'none';
      const geometrySection = document.getElementById('ge-layer-geometry-section');
      if (geometrySection) geometrySection.style.display = ['move', 'transform'].includes(toolId) ? '' : 'none';
      if (['move', 'transform'].includes(toolId)) _layerGeometry.sync();
      // Eraser and Clone don't care about color — hide the color row.
      const colorRow = document.getElementById('ge-color-row');
      if (colorRow) colorRow.style.display = ['eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn'].includes(toolId) ? 'none' : '';
      const colorLabel = colorRow?.querySelector('label');
      if (colorLabel) colorLabel.textContent = 'Color';
      const sizeLabelEl = brushControls?.querySelector('.ge-size-slider')?.parentElement?.querySelector('label');
      if (sizeLabelEl && sizeLabelEl.firstChild && sizeLabelEl.firstChild.nodeType === Node.TEXT_NODE) {
        sizeLabelEl.firstChild.nodeValue = (toolId === 'eraser') ? 'Brush Size ' : 'Size ';
      }
      // Per-tool stroke-modifier sections (opacity / flow / softness).
      const brushSection = document.getElementById('ge-brush-section');
      if (brushSection) brushSection.style.display = ['brush', 'smudge', 'dodge', 'burn'].includes(toolId) ? '' : 'none';
      const brushTitle = brushSection?.querySelector('.ge-section-title > span');
      if (brushTitle) brushTitle.textContent = toolId === 'smudge' ? 'Smudge' : 'Brush';
      const smudgeStrengthRow = document.getElementById('ge-smudge-strength-row');
      if (smudgeStrengthRow) smudgeStrengthRow.style.display = toolId === 'smudge' ? '' : 'none';
      const cloneSection = document.getElementById('ge-clone-section');
      if (cloneSection) cloneSection.style.display = ['clone', 'heal'].includes(toolId) ? '' : 'none';
      const cloneTitle = cloneSection?.querySelector('.ge-section-title > span');
      if (cloneTitle) cloneTitle.textContent = toolId === 'heal' ? 'Healing' : 'Clone';
      const cloneHelp = cloneSection?.querySelector('.ge-section-help');
      if (cloneHelp) {
        cloneHelp.setAttribute('aria-label', toolId === 'heal' ? 'How healing works' : 'How clone works');
        cloneHelp.title = toolId === 'heal'
          ? 'Paint over a small blemish to blend it with nearby pixels, or Alt-click (desktop) / double-tap (mobile) to sample a source first. Size / Opacity / Flow / Softness come from the Brush panel.'
          : 'Alt-click (desktop) or double-tap (mobile) somewhere on the canvas to set the sample source. Then drag elsewhere to clone those pixels onto the active layer. The source point moves with your brush so the offset stays constant. Size / Opacity / Flow / Softness come from the Brush panel.';
      }
      const cloneHintDesktop = cloneSection?.querySelector('.ge-clone-hint-desktop');
      const cloneHintMobile = cloneSection?.querySelector('.ge-clone-hint-mobile');
      if (toolId === 'heal') {
        if (cloneHintDesktop) cloneHintDesktop.textContent = 'Paint over, or Alt-click to sample';
        if (cloneHintMobile) cloneHintMobile.textContent = 'Paint over, or double-tap to sample';
      } else {
        if (cloneHintDesktop) cloneHintDesktop.textContent = 'Alt-click';
        if (cloneHintMobile) cloneHintMobile.textContent = 'Double-tap';
      }
      const eyedropperSection = document.getElementById('ge-eyedropper-section');
      if (eyedropperSection) eyedropperSection.style.display = toolId === 'eyedropper' ? '' : 'none';
      const lassoSection = document.getElementById('ge-lasso-section');
      if (lassoSection) lassoSection.style.display = state.tool === 'lasso' ? '' : 'none';
      const marqueeSection = document.getElementById('ge-marquee-section');
      if (marqueeSection) marqueeSection.style.display = state.tool === 'marquee' ? '' : 'none';
      const wandSection = document.getElementById('ge-wand-section');
      if (wandSection) wandSection.style.display = state.tool === 'wand' ? '' : 'none';
      const samSection = document.getElementById('ge-sam-section');
      if (samSection) samSection.style.display = state.tool === 'sam' ? '' : 'none';
      const inpaintSection = document.getElementById('ge-inpaint-section');
      if (inpaintSection) {
        if (state.tool === 'inpaint') {
          if (reactivated) inpaintSection.classList.toggle('dismissed');
          else inpaintSection.classList.remove('dismissed');
          inpaintSection.style.display = inpaintSection.classList.contains('dismissed') ? 'none' : '';
          const inpaintOpen = !inpaintSection.classList.contains('dismissed');
          controls?.classList.toggle('ge-inpaint-popover-host', inpaintOpen && window.innerWidth > 820);
          if (inpaintOpen) _positionInpaintPanel(_btn);
        } else {
          controls?.classList.remove('ge-inpaint-popover-host');
          inpaintSection.classList.remove('dismissed');
          inpaintSection.style.display = 'none';
        }
      }
      // Entering inpaint mode: make sure the active parent layer has a
      // mask sub-layer, and point the global mask plumbing at it. Also
      // force the global mask-visibility flag back on — a previous
      // Generate cleared it, but on re-entry the user expects to see
      // their mask again.
      if (state.tool === 'inpaint') {
        // If the user just made a SAM/Wand selection and then moves to
        // Inpaint, do the obvious thing: bake that selection into the
        // inpaint mask. Otherwise Generate says "draw the area first"
        // even though a red selection is visible on screen.
        if (state.wandMask && state.wandLayerId) {
          _wandToMask();
        }
        // First inpaint entry per session: bump the brush size to the
        // mask-friendly default (other tools keep their own size).
        if (!state.inpaintBrushInitialised) {
          state.brushSize = _INPAINT_DEFAULT_BRUSH;
          state.inpaintBrushInitialised = true;
          const inp = document.getElementById('ge-inpaint-brush-slider');
          if (inp) {
            const pos = Math.round(Math.log(Math.max(1, state.brushSize)) / Math.log(800) * 1000);
            inp.value = String(pos);
            const lbl = document.getElementById('ge-inpaint-brush-label');
            if (lbl) lbl.textContent = `${state.brushSize}px`;
          }
        }
        // If the active parent already carries one or more masks, reuse
        // the most-recent one instead of creating a new "Mask 2" /
        // "Mask 3" every time the user re-enters inpaint.
        const parent = _activeParentLayer();
        const selectionMasks = (parent?.masks || []).filter(m => m.mode !== 'layer');
        if (selectionMasks.length) {
          const current = _getActiveMaskLayer();
          if (!current || current.mode === 'layer') {
            parent.activeMaskId = selectionMasks[selectionMasks.length - 1].id;
          }
          const m = _getActiveMaskLayer();
          if (m) { state.maskCanvas = m.canvas; state.maskCtx = m.ctx; }
        } else {
          const mask = _ensureActiveMaskLayer();
          if (mask) {
            state.maskCanvas = mask.canvas;
            state.maskCtx = mask.ctx;
            // Reflect the freshly-created mask sub-row in the panel.
            _renderLayerPanel();
          }
        }
        if (!state.maskVisible) {
          state.maskVisible = true;
          const maskBtn = document.getElementById('ge-mask-vis');
          if (maskBtn) {
            maskBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
            maskBtn.title = 'Hide mask';
            maskBtn.classList.add('visible');
          }
        }
      }
      const eraserSection = document.getElementById('ge-eraser-section');
      if (eraserSection) eraserSection.style.display = state.tool === 'eraser' ? '' : 'none';
      const sharpenSection = document.getElementById('ge-sharpen-section');
      if (sharpenSection) sharpenSection.style.display = state.tool === 'sharpen' ? '' : 'none';
      const rembgSection = document.getElementById('ge-rembg-section');
      if (rembgSection) {
        const show = state.tool === 'rembg';
        rembgSection.style.display = show ? '' : 'none';
        if (show) _checkRembgInstalled();
      }
      const importSection = document.getElementById('ge-import-section');
      if (importSection) importSection.style.display = state.tool === 'import' ? '' : 'none';
      const harmonizeSection = document.getElementById('ge-harmonize-section');
      if (harmonizeSection) harmonizeSection.style.display = state.tool === 'harmonize' ? '' : 'none';
      const upscaleSection = document.getElementById('ge-upscale-section');
      if (upscaleSection) upscaleSection.style.display = state.tool === 'upscale' ? '' : 'none';
      const styleSection = document.getElementById('ge-style-section');
      if (styleSection) styleSection.style.display = state.tool === 'style' ? '' : 'none';
      // Toggle cursor — hide native cursor for tools that draw via our
      // own circle overlay (brush/eraser/inpaint/lasso); for other tools
      // pick a cursor that matches the tool's affordance.
      const useCircle = ['brush', 'eraser', 'inpaint', 'lasso', 'clone', 'heal', 'smudge', 'dodge', 'burn'].includes(state.tool);
      if (state.mainCanvas) {
        // Custom SVG cursor for the Move tool — white fill with black
        // stroke so it reads on both light and dark canvases.
        const moveCursorSvg = `data:image/svg+xml;utf8,${encodeURIComponent(
          '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="white" stroke="black" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 L9 5 H11 V11 H5 V9 L2 12 L5 15 V13 H11 V19 H9 L12 22 L15 19 H13 V13 H19 V15 L22 12 L19 9 V11 H13 V5 H15 Z"/></svg>'
        )}`;
        let cursor = 'crosshair';
        if (state.tool === 'move') cursor = `url("${moveCursorSvg}") 12 12, move`;
        else if (state.tool === 'hand') cursor = 'grab';
        else if (state.tool === 'transform') cursor = 'default';
        else if (useCircle) cursor = 'none';
        state.mainCanvas.style.cursor = cursor;
      }
      if (state.cursorEl) state.cursorEl.style.display = useCircle ? '' : 'none';
      canvasNavigation?.updateCursor();
      composite();
    },
  });
  // Top bar — static DOM lives in editor/build/topbar.js; all click
  // handlers below wire to the IDs baked into the markup.
  const topBar = _buildTopbar();
  container.appendChild(topBar);

  // Editor body (toolbar + canvas + panel)
  const editorBody = document.createElement('div');
  editorBody.className = 'ge-editor-body';
  editorBody.appendChild(toolbar);

  // Canvas area (center)
  const canvasArea = document.createElement('div');
  canvasArea.className = 'ge-canvas-area';
  state.mainCanvas = document.createElement('canvas');
  state.mainCanvas.className = 'ge-main-canvas';
  state.mainCtx = state.mainCanvas.getContext('2d');
  // Initial cursor matches the default tool (move) so the user sees the
  // four-arrow icon as soon as the editor opens. Uses a filled white
  // arrow with black stroke for readability on light AND dark canvases.
  if (state.tool === 'move') {
    const svg = `data:image/svg+xml;utf8,${encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="white" stroke="black" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 L9 5 H11 V11 H5 V9 L2 12 L5 15 V13 H11 V19 H9 L12 22 L15 19 H13 V13 H19 V15 L22 12 L19 9 V11 H13 V5 H15 Z"/></svg>'
    )}`;
    state.mainCanvas.style.cursor = `url("${svg}") 12 12, move`;
  } else {
    state.mainCanvas.style.cursor = 'crosshair';
  }
  canvasArea.appendChild(state.mainCanvas);
  _textEditor = createTextEditOverlay({
    state,
    onInput: (layer, value, input, frameWidth) => {
      if (!layer?.text) return;
      _rerenderTextLayer(layer, {
        ...layer.text,
        content: value,
        frameWidth: frameWidth || layer.text.frameWidth,
        autoWidth: false,
      }, false);
      _syncTextControls();
    },
    onCommit: layer => {
      if (!layer) return;
      _renderLayerPanel();
      _syncTextControls();
      _schedulePersist();
    },
    onCancel: () => undo(),
  });

  state.selectionOverlay = document.createElement('canvas');
  state.selectionOverlay.className = 'ge-selection-overlay';
  state.selectionOverlayCtx = state.selectionOverlay.getContext('2d');
  canvasArea.appendChild(state.selectionOverlay);

  // Transform overlay — separate canvas positioned over the main canvas
  // with extra margin so the resize/rotation handles can render OUTSIDE
  // the image bounds. Sized + zoomed in sync with the main canvas via
  // _syncTransformOverlay(). The overlay is pointer-events:none so it
  // doesn't intercept clicks; hit-testing still happens in image coords.
  state.transformOverlay = document.createElement('canvas');
  state.transformOverlay.className = 'ge-transform-overlay';
  state.transformOverlayCtx = state.transformOverlay.getContext('2d');
  canvasArea.appendChild(state.transformOverlay);
  canvasArea.appendChild(_buildQuickMaskBar());
  canvasArea.appendChild(_buildAiCommandBox());
  _precisionGuides = createPrecisionGuides({
    canvasArea,
    composite,
    saveState: _saveState,
    schedulePersist: _schedulePersist,
  });
  // Keep the transform handles glued to the photo while the canvas-area
  // scrolls (the overlay is anchored to the canvas's live rect, so a
  // re-draw on scroll re-reads its position).
  canvasArea.addEventListener('scroll', () => {
    _syncSelectionOverlay();
    if (state.transformActive) _drawTransformHandles();
  }, { passive: true });

  // Canvas events (mouse + touch + pinch-zoom + pan) — full
  // implementation in editor/canvas-events.js.
  canvasNavigation = wireCanvasEvents({
    canvasArea,
    beginDraw: _beginDraw,
    continueDraw: _continueDraw,
    endDraw: _endDraw,
    cancelDraw: () => {
      if (state.tool === 'pen') { _penTool.cancel(); return; }
      if (state.transformActive) _cancelTransform();
      else if (state.drawing) _strokeTool.cancel();
      else if (state.gradientActive) _gradientTool.cancel();
      else if (state.marqueeActive || state.selectionMoving) _marqueeTool.cancel('pointercancel');
      else if (state.lassoActive || state.lassoPoints.length) _cancelLasso();
      else if (state.cropRect || state.cropping || state.cropMoving) _cancelCrop('pointercancel');
      else if (_shapeDraft) _cancelShapeDraft();
    },
    updateBrushCursor: (e) => _updateBrushCursor(e),
    updateEyedropperPreview: (e, leaving = false) => {
      if (leaving) return _eyedropperTool.clearPreview();
      if (e) _eyedropperTool.preview(e);
    },
    syncZoomControls: () => _syncZoomControls(),
    onViewportChange: () => _precisionGuides?.redrawRulers(),
  });

  editorBody.appendChild(canvasArea);

  // Right panel (controls + layers + resize handle) — full
  // implementation in editor/build/right-panel.js.
  const { rightPanel, controls, layerPanel } = buildRightPanel({
    controlsHTML: _controlsHTML,
    layerPanelHTML: _layerPanelHTML,
  });
  editorBody.appendChild(rightPanel);
  container.appendChild(editorBody);
  container.appendChild(_buildZoomFooter());
  _wireInpaintPopoverWindow();

  // Slider UX (expand-while-using, floating bubble, click-to-type) —
  // full implementation in editor/slider-ux.js.
  wireSliderUx({ registerDocClickAway: _registerDocClickAway });

  // Shortcuts cheatsheet popover — full implementation in
  // editor/shortcuts-popover.js. (Dead `_makeShortcutsDraggable`
  // helper for the old centered-modal version was dropped.)
  const _shortcutsPopover = createShortcutsPopover();
  const _toggleShortcuts = _shortcutsPopover.toggleShortcuts;
  document.getElementById('ge-shortcuts-btn')?.addEventListener('click', () => _toggleShortcuts());

  // Dismiss-listeners for the inpaint popup are attached lazily by
  // _showInpaintPrompt() and removed by _dismissInpaintPrompt(), so the
  // active-edit path doesn't pay for them on every event. (Listening on
  // mousedown/wheel/touchstart/input/change at capture phase, even with
  // a fast `closest()` check, added up to noticeable lag during heavy
  // brush use.)

  // Wire up controls
  controls.querySelector('.ge-color-picker').addEventListener('input', (e) => { state.color = e.target.value; });
  // Swap the editor's native color inputs for the in-house HSV picker
  // we built in the theme system — eyedropper, suggestions, recents,
  // no native OS dialog. Each picker keeps its existing `input` event
  // wiring so callers just keep reading `e.target.value`.
  controls.querySelectorAll('.ge-color-picker').forEach(attachColorPicker);
  const gradientStart = controls.querySelector('#ge-gradient-start');
  const gradientEnd = controls.querySelector('#ge-gradient-end');
  const gradientMid = controls.querySelector('#ge-gradient-mid');
  const gradientMidEnabled = controls.querySelector('#ge-gradient-mid-enabled');
  const gradientMidPosition = controls.querySelector('#ge-gradient-mid-position');
  const gradientExtraStops = controls.querySelector('#ge-gradient-extra-stops');
  const gradientEndAlpha = controls.querySelector('#ge-gradient-end-alpha');
  const gradientOpacity = controls.querySelector('#ge-gradient-opacity');
  const gradientType = controls.querySelector('#ge-gradient-type');
  gradientType?.addEventListener('change', e => { state.gradientType = e.target.value; });
  if (gradientType) gradientType.value = state.gradientType || 'linear-gradient';
  gradientStart?.addEventListener('input', e => { state.gradientStart = e.target.value; });
  gradientEnd?.addEventListener('input', e => { state.gradientEnd = e.target.value; });
  gradientMid?.addEventListener('input', e => { state.gradientMid = e.target.value; });
  gradientMidEnabled?.addEventListener('change', e => {
    state.gradientMidEnabled = e.target.checked;
    if (gradientMid) gradientMid.disabled = !state.gradientMidEnabled;
    if (gradientMidPosition) gradientMidPosition.disabled = !state.gradientMidEnabled;
  });
  gradientMidPosition?.addEventListener('input', e => {
    state.gradientMidPosition = Number(e.target.value);
    const label = controls.querySelector('#ge-gradient-mid-position-label');
    if (label) label.textContent = `${state.gradientMidPosition}%`;
  });
  if (gradientMid) gradientMid.value = state.gradientMid;
  if (gradientMidEnabled) gradientMidEnabled.checked = state.gradientMidEnabled;
  if (gradientMidPosition) gradientMidPosition.value = String(state.gradientMidPosition);
  if (gradientMid) gradientMid.disabled = !state.gradientMidEnabled;
  if (gradientMidPosition) gradientMidPosition.disabled = !state.gradientMidEnabled;
  const gradientMidPositionLabel = controls.querySelector('#ge-gradient-mid-position-label');
  if (gradientMidPositionLabel) gradientMidPositionLabel.textContent = `${state.gradientMidPosition}%`;
  const readGradientExtraStops = () => [...(gradientExtraStops?.querySelectorAll('[data-gradient-extra-stop]') || [])].map(row => ({
    color: row.querySelector('[data-gradient-stop-color]')?.value || '#808080',
    position: Number(row.querySelector('[data-gradient-stop-position]')?.value) || 50,
  }));
  const renderGradientExtraStops = () => {
    if (!gradientExtraStops) return;
    const stops = state.gradientStops?.length ? state.gradientStops : readGradientExtraStops();
    gradientExtraStops.innerHTML = stops.map((stop, index) => `
      <div class="ge-gradient-extra-stop" data-gradient-extra-stop>
        <label class="ge-text-field"><span>Stop ${index + 1}</span><input type="color" class="ge-color-picker" data-gradient-stop-color value="${String(stop.color).replace(/"/g, '&quot;')}" /></label>
        <label class="ge-text-field"><span>Position</span><input type="number" min="1" max="99" step="1" data-gradient-stop-position value="${Math.max(1, Math.min(99, stop.position))}" /></label>
        <button type="button" class="ge-btn ge-btn-sm ge-gradient-stop-remove" data-gradient-stop-remove title="Remove gradient stop" aria-label="Remove gradient stop">×</button>
      </div>`).join('');
    gradientExtraStops.querySelectorAll('.ge-color-picker').forEach(attachColorPicker);
  };
  if (gradientExtraStops && state.gradientStops?.length) renderGradientExtraStops();
  controls.querySelector('#ge-gradient-add-stop')?.addEventListener('click', () => {
    const stops = [
      { position: 0 },
      ...(gradientMidEnabled?.checked ? [{ position: Number(gradientMidPosition?.value) || 50 }] : []),
      ...readGradientExtraStops(),
      { position: 100 },
    ].sort((a, b) => a.position - b.position);
    if (stops.length >= 12) return;
    let largestGap = 0;
    let position = 50;
    for (let index = 1; index < stops.length; index += 1) {
      const gap = stops[index].position - stops[index - 1].position;
      if (gap > largestGap) {
        largestGap = gap;
        position = Math.round(stops[index - 1].position + gap / 2);
      }
    }
    _saveState('Add gradient stop');
    state.gradientStops = [...readGradientExtraStops(), { position, color: '#808080' }];
    renderGradientExtraStops();
  });
  gradientExtraStops?.addEventListener('click', event => {
    const remove = event.target.closest('[data-gradient-stop-remove]');
    if (!remove) return;
    const row = remove.closest('[data-gradient-extra-stop]');
    const rows = [...gradientExtraStops.querySelectorAll('[data-gradient-extra-stop]')];
    const index = rows.indexOf(row);
    if (index < 0) return;
    const next = readGradientExtraStops();
    _saveState('Remove gradient stop');
    next.splice(index, 1);
    state.gradientStops = next;
    renderGradientExtraStops();
  });
  gradientExtraStops?.addEventListener('focusin', event => {
    if (event.target.matches('[data-gradient-stop-color], [data-gradient-stop-position]')) event.target.dataset.geHistorySaved = 'false';
  });
  gradientExtraStops?.addEventListener('input', event => {
    if (!event.target.matches('[data-gradient-stop-color], [data-gradient-stop-position]')) return;
    if (event.target.dataset.geHistorySaved !== 'true') {
      _saveState('Edit gradient stop');
      event.target.dataset.geHistorySaved = 'true';
    }
    state.gradientStops = readGradientExtraStops();
  });
  gradientEndAlpha?.addEventListener('input', e => {
    state.gradientEndAlpha = Number(e.target.value);
    const label = controls.querySelector('#ge-gradient-end-alpha-label');
    if (label) label.textContent = `${state.gradientEndAlpha}%`;
  });
  gradientOpacity?.addEventListener('input', e => {
    state.gradientOpacity = Number(e.target.value);
    const label = controls.querySelector('#ge-gradient-opacity-label');
    if (label) label.textContent = `${state.gradientOpacity}%`;
  });
  controls.querySelector('#ge-clone-source-clear')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    state.cloneSourceX = null;
    state.cloneSourceY = null;
    state.cloneSourceLayerId = null;
    state.cloneSourceSnapshot = null;
    state.cloneSourceOffsetX = 0;
    state.cloneSourceOffsetY = 0;
    _syncCloneSourceUi();
    uiModule?.showToast?.('Sampled source cleared');
  });
  const cloneSampleMode = controls.querySelector('#ge-clone-sample-mode');
  if (cloneSampleMode) {
    cloneSampleMode.value = state.cloneSampleMode || 'active-layer';
    cloneSampleMode.addEventListener('change', event => {
      state.cloneSampleMode = event.target.value === 'composite' ? 'composite' : 'active-layer';
      state.cloneSourceX = null;
      state.cloneSourceY = null;
      state.cloneSourceLayerId = null;
      state.cloneSourceSnapshot = null;
      state.cloneSourceOffsetX = 0;
      state.cloneSourceOffsetY = 0;
      _syncCloneSourceUi();
      uiModule?.showToast?.('Sample source cleared');
    });
  }
  _syncCloneSourceUi();
  _wireTextControls(controls);
  _wireShapeControls(controls);
  _layerGeometry.wire(controls);
  controls.querySelectorAll('.ge-color-picker').forEach(el => {
    // Set the initial swatch background so it reflects the starting value.
    el.value = el.value;
  });
  // Hide brush controls initially (default tool is Move)
  const initBrushCtrl = document.getElementById('ge-brush-controls');
  if (initBrushCtrl) initBrushCtrl.style.display = 'none';
  const initGradientCtrl = document.getElementById('ge-gradient-section');
  if (initGradientCtrl) initGradientCtrl.style.display = 'none';
  // Brush-size slider is exponential — slider position 0..1000 maps to
  // brush size 1..800 via Math.pow(800, pos/1000). This gives fine
  // control at small sizes (where precision matters most) and bigger
  // jumps at the high end (where +/-50 px is barely visible anyway).
  // We expose two sliders (global brush-controls + inpaint section) and
  // keep them in sync via _brushSizeSync.
  function _brushSizeSync(source) {
    const globalLabel = controls.querySelector('.ge-size-label');
    const globalInput = controls.querySelector('.ge-size-slider');
    const inpaintLabel = document.getElementById('ge-inpaint-brush-label');
    const inpaintInput = document.getElementById('ge-inpaint-brush-slider');
    const pos = Math.round(Math.log(Math.max(1, state.brushSize)) / Math.log(800) * 1000);
    if (globalLabel) globalLabel.textContent = state.brushSize + 'px';
    if (inpaintLabel) inpaintLabel.textContent = state.brushSize + 'px';
    if (globalInput && source !== globalInput) globalInput.value = String(pos);
    if (inpaintInput && source !== inpaintInput) inpaintInput.value = String(pos);
    const cursorX = Number(state.cursorEl?.dataset.clientX);
    const cursorY = Number(state.cursorEl?.dataset.clientY);
    if (state.cursorEl && state.cursorEl.style.display !== 'none'
      && Number.isFinite(cursorX) && Number.isFinite(cursorY)) {
      _updateBrushCursor({ clientX: cursorX, clientY: cursorY });
    }
  }
  function _wireBrushSlider(el) {
    if (!el) return;
    el.addEventListener('input', (e) => {
      const pos = parseInt(e.target.value, 10);
      state.brushSize = Math.max(1, Math.round(Math.pow(800, pos / 1000)));
      _brushSizeSync(e.target);
    });
  }
  _wireBrushSlider(controls.querySelector('.ge-size-slider'));
  _wireBrushSlider(document.getElementById('ge-inpaint-brush-slider'));
  // Topbar wiring (undo/redo/history, Save dropdown, zoom buttons,
  // Export/Download/Project, Edge popup, cross-dropdown coordination) —
  // full implementation in editor/wire-topbar.js.
  wireTopbar({
    undo, redo,
    toggleHistoryPanel: _toggleHistoryPanel,
    toggleCompare: _toggleCompare,
    fitZoom: () => _fitZoom(),
    applyZoom: () => _applyZoom(),
    exportToGallery, downloadPNG,
    saveProject: () => _saveProject(),
    loadProjectPrompt: () => _loadProjectPrompt(),
    activeLayer,
    saveState: _saveState,
    applyEdgeFeather: _applyEdgeFeather,
    composite,
    registerDocClickAway: _registerDocClickAway,
    uiModule,
  });
  wireViewMenu({
    closeOtherTopbarMenus: _closeOtherTopbarMenus,
    registerDocClickAway: _registerDocClickAway,
    precisionGuides: _precisionGuides,
    composite,
    schedulePersist: _schedulePersist,
  });
  // Fill — visible only when a selection or active mask exists. Pours
  // the current colour into whichever target is live:
  //   - active mask sub-layer → fills the layer's pixels clipped by
  //     the mask (uses mask alpha as a stencil).
  //   - lasso closed → fills the polygon area on the active layer.
  //   - wand selection → fills the wand mask area on the active layer.
  // Fill — invoked from the Image menu's "Fill selection / mask" item.
  // Pours the current colour into whichever target is live:
  //   - active mask sub-layer → fills the layer's pixels clipped by
  //     the mask (uses mask alpha as a stencil).
  //   - lasso closed → fills the polygon area on the active layer.
  //   - wand selection → fills the wand mask area on the active layer.
  async function _doFillSelection() {
    const selection = _selectionMaskAsDocument({ materializeLasso: true });
    const layer = await _preparePixelTarget('filling pixels');
    if (!layer) return;
    const off = layer.offset;
    const w = layer.canvas.width;
    const h = layer.canvas.height;
    const stencil = document.createElement('canvas');
    stencil.width = w; stencil.height = h;
    const sctx = stencil.getContext('2d');
    if (selection) {
      sctx.drawImage(_selectionMaskForLayer(
        selection,
        'document',
        off,
        w,
        h,
      ), 0, 0);
    } else {
      sctx.fillStyle = '#fff';
      sctx.fillRect(0, 0, w, h);
    }
    _saveState(selection ? 'Fill selection' : 'Fill layer');
    sctx.globalCompositeOperation = 'source-in';
    sctx.fillStyle = state.color;
    sctx.fillRect(0, 0, w, h);
    sctx.globalCompositeOperation = 'source-over';
    layer.ctx.save();
    if (!layer.mask && _isLayerTransparencyLocked(state, layer.parent)) layer.ctx.globalCompositeOperation = 'source-atop';
    layer.ctx.drawImage(stencil, 0, 0);
    layer.ctx.restore();
    composite();
    _renderLayerPanel();
    if (uiModule) uiModule.showToast('Filled');
    _schedulePersist();
  }
  document.getElementById('ge-layer-fill')?.addEventListener('click', () => _doFillSelection());
  document.getElementById('ge-pen-commit')?.addEventListener('click', () => _penTool.commit());
  document.getElementById('ge-pen-cancel')?.addEventListener('click', () => _penTool.cancel());

  // AI model selectors (Gen, Inpaint, per-tool) — full
  // implementation in editor/ai-models.js.
  wireAIModelSelectors({
    container,
    apiBase: API_BASE,
    openCookbookForImg2img: () => _openCookbookForImg2img(),
  });

  document.getElementById('ge-save').addEventListener('click', async () => {
    if (!state.imageId) {
      await exportToGallery();
      return;
    }
    const endBusy = _saveButtonBusy('Saving…');
    let blob = null;
    let savedOk = false;
    const t0 = performance.now();
    try {
      // Encode directly from the flattened canvas via toBlob() to avoid
      // the dataURL round-trip (which doubles peak memory). Pick JPEG for
      // photo sources so 24MP uploads don't balloon to 200MB+ PNG —
      // critical when the editor is accessed over Tailscale Funnel etc.
      const flat = flatten();
      const ext = (state.originalExt || 'png').toLowerCase();
      const isJpeg = ext === 'jpg' || ext === 'jpeg';
      const mime = isJpeg ? 'image/jpeg' : 'image/png';
      const quality = isJpeg ? 0.92 : undefined;
      blob = await new Promise((resolve, reject) => {
        flat.toBlob(b => b ? resolve(b) : reject(new Error('Canvas encode failed')), mime, quality);
      });
      const fd = new FormData();
      fd.append('image', blob, `edited.${isJpeg ? 'jpg' : 'png'}`);
      const resp = await fetch(`${API_BASE}/api/gallery/${state.imageId}/replace`, {
        method: 'POST',
        credentials: 'same-origin',
        body: fd,
      });
      if (!resp.ok) {
        let detail = '';
        try { const j = await resp.json(); detail = j.detail || j.error || ''; } catch {}
        throw new Error(`HTTP ${resp.status}${detail ? `: ${detail}` : ''}`);
      }
      const totalMs = Math.round(performance.now() - t0);
      if (uiModule) uiModule.showToast(`Saved over original (${(blob.size / 1024 / 1024).toFixed(1)}MB · ${(totalMs / 1000).toFixed(1)}s)`, 4000);
      window.dispatchEvent(new CustomEvent('gallery-refresh'));
      savedOk = true;
    } catch (e) {
      console.error('[save] error:', e);
      const sizeMB = blob ? ` (${(blob.size / 1024 / 1024).toFixed(1)}MB)` : '';
      let msg = e?.message || 'unknown';
      if (e?.name === 'TypeError' || /fetch|network|load failed/i.test(msg)) {
        msg = `network dropped${sizeMB} — try "Save as copy" or check connection`;
      } else {
        msg += sizeMB;
      }
      if (uiModule) uiModule.showToast('Failed to save: ' + msg, 6000);
    } finally {
      endBusy();
      if (savedOk) {
        _setDraftStatus('Saved', 'saved');
        _flashSaveButtonOk();
      } else {
        _setDraftStatus('Save failed', 'error');
      }
    }
  });

  // Topbar overflow + canvas-size badge — full implementation in
  // editor/wire-topbar-overflow.js.
  wireTopbarOverflow({ container, registerDocClickAway: _registerDocClickAway });

  // Topbar dropdown menus (Image, Filter, Resize) + the resize-canvas
  // helpers — full implementation in editor/wire-topbar-menus.js. The
  // returned `_resizeCustomPrompt` is consumed by the keyboard
  // shortcuts module (Ctrl+Shift+T).
  const { resizeCustomPrompt: _resizeCustomPrompt, renderSelectionMenu } = wireTopbarMenus({
    closeOtherTopbarMenus: _closeOtherTopbarMenus,
    registerDocClickAway: _registerDocClickAway,
    saveState: _saveState,
    composite,
    fitZoom: () => _fitZoom(),
    renderLayerPanel: () => _renderLayerPanel(),
    promptCanvasSize: (opts) => _promptCanvasSize(opts),
    doFillSelection: () => _doFillSelection(),
    rotateAllLayers: (deg) => _rotateAllLayers(deg),
    flipAllLayers: (axis) => _flipAllLayers(axis),
    applyGaussianBlur: () => _applyGaussianBlur(),
    applyZoomBlur: () => _applyZoomBlur(),
    addRetainedGaussianBlur: (presetName) => _addRetainedGaussianBlur(presetName),
    addRetainedEffect: (type, presetName) => _addRetainedEffect(type, presetName),
    selectAll: _selectAllSelection,
    deselectSelection: () => _deselectSelection(),
    reselectSelection: _reselectSelection,
    invertSelection: _invertSelection,
    transformSelection: _startSelectionTransform,
    refineSelection: _refineSelection,
    saveNamedSelection: _saveNamedSelection,
    loadNamedSelection: _loadNamedSelection,
    deleteNamedSelection: _deleteNamedSelection,
    uiModule,
  });
  _renderSavedSelectionsMenu = renderSelectionMenu;

  // Inpaint side-panel controls (Feather/Strength previews, post-gen
  // edge tuner, mask vis/invert/clear, paint-erase toggle, mask tint
  // pickers) — full implementation in editor/wire-inpaint-controls.js.
  wireInpaintControls({
    composite,
    applyInpaintFeather: _applyInpaintFeather,
    autoMatchInpaint: _autoMatchLastInpaintLayer,
    syncToolClearIndicators: () => _syncToolClearIndicators(),
    attachColorPicker,
    uiModule,
  });

  // AI inpaint (Generate / Remove / Outpaint) — full implementation
  // in editor/ai-inpaint.js.
  wireInpaintButtons({
    renderLayer: (layer) => _renderLayerOutput(layer),
    buildMergedMaskCanvas: () => _buildMergedMaskCanvas(),
    dilateMask: _dilateMask,
    applyInpaintFeather: _applyInpaintFeather,
    getSelectedAIEndpoint: (type) => _getSelectedAIEndpoint(type),
    ensureActiveMaskLayer: () => _ensureActiveMaskLayer(),
    saveState: _saveState,
    createLayer,
    composite,
    flatten,
    renderLayerPanel: () => _renderLayerPanel(),
    spinnerModule,
    uiModule,
  });

  // Per-tool Opacity / Flow / Softness sliders (Eraser / Brush /
  // Clone) — full implementation in editor/stroke-tool-sliders.js.
  wireStrokeToolSliders({
    onPresetApplied: () => {
      _brushSizeSync(null);
      const values = {
        'ge-brush-opacity': state.brushOpacity,
        'ge-brush-flow': state.brushFlow,
        'ge-brush-softness': state.brushSoftness,
      };
      Object.entries(values).forEach(([id, value]) => {
        const input = document.getElementById(id);
        if (!input) return;
        input.value = String(value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
    },
  });

  // Sharpen + Bg Remove + edge cleanup — full implementation in
  // editor/ai-rembg.js. Returns the selection-hint-mask builder so
  // the wand-rembg button (in the wand controls section) can reuse it.
  const { buildSelectionHintMask: _buildSelectionHintMask } = wireRembgAndSharpen({
    applyImageTool: _applyImageTool,
    openCookbookForDependency: (pkg) => _openCookbookForDependency(pkg),
    composite,
    renderLayerPanel: () => _renderLayerPanel(),
    uiModule,
  });

  // Image import (topbar / panel File / Clipboard / Gallery picker) —
  // full implementation in editor/wire-import.js. Returns the shared
  // handleImportedImage sink so drag-drop wires through the same path.
  const { handleImportedImage: _handleImportedImage } = wireImport({
    container,
    saveState: _saveState,
    createLayer,
    composite,
    renderLayerPanel: () => _renderLayerPanel(),
    uiModule,
  });

  // Harmonize / Canvas Upscale / AI Upscale / Style Transfer +
  // Add-Empty-Layer — full implementation in editor/ai-tools-misc.js.
  const { addEmptyLayer: _addEmptyLayer } = wireAIToolsMisc({
    apiBase: API_BASE,
    buildLayerBodyMask: _buildLayerBodyMask,
    buildSeamMask: _buildSeamMask,
    applyImageTool: _applyImageTool,
    flatten,
    saveState: _saveState,
    fitZoom: () => _fitZoom(),
    composite,
    createLayer,
    renderLayerPanel: () => _renderLayerPanel(),
    spinnerModule,
    uiModule,
    openAdjustmentLayer: (type, anchor) => _addAdjustmentLayer(type, anchor),
  });
  // (Merge dropdown removed — Merge Down / Merge All / Flatten Copy
  // are now three inline icon buttons in the layers header next to
  // + Add. Their individual click handlers below already bind by id.)

  // Lasso + Magic Wand panel controls — full implementation in
  // editor/wire-selection-controls.js.
  wireSelectionControls({
    composite,
    invertSelection: _invertSelection,
    lassoDeleteSelection: _lassoDeleteSelection,
    lassoCopyToLayer: _lassoCopyToLayer,
    lassoToMask: _lassoToMask,
    runMagicWand: (x, y, mode, opts) => _runMagicWand(x, y, mode, opts),
    wandClear: _wandClear,
    wandDeleteSelection: _wandDeleteSelection,
    wandCopyToNewLayer: _wandCopyToNewLayer,
    wandToMask: _wandToMask,
    buildSelectionHintMask: _buildSelectionHintMask,
    applyImageTool: _applyImageTool,
    uiModule,
    toggleQuickMask: _toggleQuickMask,
  });
  document.getElementById('ge-sam-find')?.addEventListener('click', () => _runSamTextSelection());
  document.getElementById('ge-sam-query')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      _runSamTextSelection();
    }
  });
  document.getElementById('ge-sam-clear')?.addEventListener('click', () => _wandClear());
  document.getElementById('ge-sam-mask')?.addEventListener('click', () => _wandToMask());
  document.getElementById('ge-sam-vis')?.addEventListener('click', () => {
    state.wandMaskVisible = !state.wandMaskVisible;
    const btn = document.getElementById('ge-sam-vis');
    if (btn) {
      btn.innerHTML = state.wandMaskVisible
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
        : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20C5 20 1 12 1 12a20.29 20.29 0 0 1 5.06-5.94"/><path d="M9.9 4.24A10.45 10.45 0 0 1 12 4c7 0 11 8 11 8a20.65 20.65 0 0 1-2.16 3.19"/><path d="M14.12 14.12A3 3 0 0 1 9.88 9.88"/><path d="M1 1l22 22"/></svg>';
      btn.title = state.wandMaskVisible ? 'Hide selection overlay' : 'Show selection overlay';
      btn.classList.toggle('visible', state.wandMaskVisible);
    }
    composite();
  });
  _wireAiCommandBox();

  // Merge / Flatten buttons (layer-panel footer) — full
  // implementation in editor/wire-merge-buttons.js.
  wireMergeButtons({
    saveState: _saveState,
    createLayer,
    renderLayerPanel: () => _renderLayerPanel(),
    composite,
    renderLayer: (layer) => _renderLayerOutput(layer),
    flatten,
    uiModule,
  });

  // Capture-phase Escape interceptor — runs BEFORE any bubble-phase
  // handler (gallery, keyboard-shortcuts module, etc.) so cancelling a
  // crop / lasso / transform inside the editor can't ever bubble up and
  // accidentally close the gallery modal.
  document.addEventListener('keydown', (e) => {
    if (!state.editorOpen) return;
    if (e.target?.closest?.('#styled-confirm-overlay')) return;
    // Inline layer renaming owns Escape so it can restore the original name
    // without the editor-wide guard swallowing the event first.
    const renameInput = e.key === 'Escape' && e.target?.closest?.('.ge-layer-name-input');
    if (renameInput) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      renameInput.dataset.cancelRename = 'true';
      renameInput.blur();
      return;
    }
    const sizePrompt = e.key === 'Escape' && e.target?.closest?.('#ge-canvas-size-overlay');
    if (sizePrompt) {
      sizePrompt._cancelCanvasSize?.();
      return;
    }
    // Esc on the shortcuts overlay closes it; takes priority over the
    // other modal cancels so the cheatsheet feels responsive AND so the
    // gallery's own Esc handler doesn't fire and close gallery instead.
    if (e.key === 'Escape' && _shortcutsPopover.isOpen()) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _toggleShortcuts(false);
      return;
    }
    // Enter accepts an active crop (same as the Apply button). Skip when
    // typing in a field — the crop W/H inputs handle their own Enter, and
    // we don't want to hijack Enter elsewhere.
    if (e.key === 'Enter' && state.cropRect && !state.cropping && !state.cropMoving) {
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      e.preventDefault();
      e.stopPropagation();
      _applyCrop();
      return;
    }
    if (e.key === 'Escape' && state.transformActive) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _cancelTransform();
      return;
    }
    if (e.key === 'Escape' && (state.cropRect || state.cropping || state.cropMoving)) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _cancelCrop('escape');
      return;
    }
    if (e.key !== 'Escape') return;
    // Escape is disabled inside Gallery Edit. It must not close the
    // editor, close Gallery, or cancel active editor state.
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
  }, true);

  // Keyboard shortcuts — full implementation in
  // editor/keyboard-shortcuts.js.
  wireKeyboardShortcuts({
    fillSelection: _doFillSelection,
    copyPixelsToClipboard: _copyPixelsToClipboard,
    toolbar, toolKeyMap: _toolKeyMap,
    composite, saveState: _saveState, undo, redo,
    toggleShortcuts: _toggleShortcuts,
    confirmTransform: _confirmTransform,
    cancelTransform: _cancelTransform,
    startTransform: _startTransform,
    nudgeTransform: _nudgeTransform,
    resizeCustomPrompt: _resizeCustomPrompt,
    addEmptyLayer: _addEmptyLayer,
    brushSizeSync: _brushSizeSync,
    invertSelection: _invertSelection,
    wandDeleteSelection: _wandDeleteSelection,
    wandCopyToNewLayer: _wandCopyToNewLayer,
    lassoDeleteSelection: _lassoDeleteSelection,
    lassoCopyToLayer: _lassoCopyToLayer,
    lassoToMask: _lassoToMask,
    buildLassoMask: _buildLassoMask,
    drawLassoOverlay: _drawLassoOverlay,
    activeLayer,
    deleteSelectedLayers: () => _layerPanelRenderer.deleteSelectedLayers?.(),
    duplicateActiveLayer: () => _layerPanelRenderer.duplicateActiveLayer?.(),
    uiModule,
    setTemporaryPan: (active) => canvasNavigation?.setTemporaryPan(active),
    nudgeActiveLayer: (dx, dy) => _layerGeometry.nudge(dx, dy),
    endLayerNudge: () => _layerGeometry.endNudge(),
    toggleQuickMask: _toggleQuickMask,
    nudgeSelection: _nudgeSelectionBoundary,
    deselectSelection: () => _deselectSelection(),
  });
  container.setAttribute('tabindex', '0');

  // Paste + drag-and-drop image import — full implementation in
  // editor/clipboard-and-drop.js.
  wireClipboardAndDrop({
    container,
    saveState: _saveState,
    createLayer,
    renderLayerPanel: () => _renderLayerPanel(),
    composite,
    handleImportedImage: (img, sourceName) => _handleImportedImage(img, sourceName),
    uiModule,
  });
}

// ── Layer panel rendering ──

function _replacePlacedLayerFromFile(layer) {
  if (layer?.kind !== 'placed') return;
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.addEventListener('change', () => {
    const file = input.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      try {
        const source = document.createElement('canvas');
        source.width = image.naturalWidth || image.width;
        source.height = image.naturalHeight || image.height;
        source.getContext('2d').drawImage(image, 0, 0);
        _saveState(`Replace source for "${layer.name}"`);
        const rendered = _replacePlacedSource(layer, source, file.name || 'Placed image');
        if (!rendered) throw new Error('The selected layer is no longer a placed image.');
        state.layerOffsets.set(layer.id, rendered.offset);
        composite();
        _renderLayerPanel();
        _schedulePersist();
        uiModule?.showToast('Placed image replaced');
      } catch (error) {
        uiModule?.showToast(error?.message || 'Could not replace placed image');
      } finally {
        URL.revokeObjectURL(url);
      }
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      uiModule?.showToast('Could not read replacement image');
    };
    image.src = url;
  }, { once: true });
  input.click();
}

function _rasterizePlacedLayerCommand(layer) {
  if (layer?.kind !== 'placed') return;
  _saveState(`Rasterize "${layer.name}"`);
  if (!_rasterizePlacedLayer(layer)) return;
  composite();
  _renderLayerPanel();
  _schedulePersist();
  uiModule?.showToast('Placed layer rasterized');
}

// Layer-panel renderer — implementation in editor/layer-panel.js.
// Wrap to the legacy name so the dozens of `_renderLayerPanel()` call
// sites scattered across the file keep working unchanged.
const _layerPanelRenderer = createLayerPanelRenderer({
  openLayerStyles: (layer, anchor) => openLayerStyleMenu(anchor, type => {
    state.activeGroupId = null;
    state.activeLayerId = layer.id;
    void _addRetainedEffect(type);
  }),
  composite,
  saveState: _saveState,
  showLayerThumb: (row, layer) => _showLayerThumb(row, layer),
  hideLayerThumb: () => _hideLayerThumb(),
  loadLayerAlphaAsSelection: (layer) => _loadLayerAlphaAsSelection(layer),
  loadMaskAsSelection: (layer, mask) => _loadMaskAsSelection(layer, mask),
  getDocumentSelection: () => _selectionMaskAsDocument({ materializeLasso: true }),
  openFxPopup: (layer, anchor) => _openFxPopup(layer, anchor),
  editAdjLayer: (layer, adj, anchor) => _editAdjLayer(layer, adj, anchor),
  editRetainedEffect: (layer, effect, anchor) => _editRetainedEffect(layer, effect, anchor),
  addEffectMask: (layer, effect) => _addEffectMask(layer, effect),
  rasterizeEffects: (layer) => _rasterizeEffects(layer),
  createLayer,
  renderLayer: (layer) => _renderLayerOutput(layer),
  replacePlacedLayer: (layer) => _replacePlacedLayerFromFile(layer),
  rasterizePlacedLayer: (layer) => _rasterizePlacedLayerCommand(layer),
  lassoToMask: () => _lassoToMask(),
  wandToMask: () => _wandToMask(),
  getActiveMaskLayer: () => _getActiveMaskLayer(),
  onSelectLayer: () => { _syncTextControls(); _syncShapeControls(); _layerGeometry.sync(); },
  syncFxPanelToActiveLayerIfPresent: () => _syncFxPanelToActiveLayerIfPresent(),
  dragSortModule,
  uiModule,
});
function _renderLayerPanel() {
  const result = _layerPanelRenderer.render();
  _layerPanelRenderer.refreshPreviews();
  _syncTextControls();
  _syncShapeControls();
  _layerGeometry.sync();
  return result;
}

function _revealLayerPanel() {
  requestAnimationFrame(() => {
    const panel = state.container?.querySelector?.('.ge-right-panel') ||
      document.querySelector('.ge-right-panel');
    if (!panel) return;
    panel.classList.remove('minimized');
    panel.classList.add('expanded');
  });
}

// ── Flatten / Export ──

function flatten() {
  const out = document.createElement('canvas');
  out.width = state.imgWidth;
  out.height = state.imgHeight;
  const ctx = out.getContext('2d');
  _drawDocumentLayers(ctx);
  return out;
}

// Build the union of all "foreground" visible-layer alphas (binary).
// "Background" = the BOTTOMMOST visible layer (Harmonize's colour-match
// reference). Everything visible ABOVE it = foreground that goes into
// the body mask. Independent of the `isBase` flag, so reordering layers
// (or hiding the original photo after a bg-remove) doesn't break the
// semantics.
// Harmonize-pipeline mask builders live in editor/harmonize-masks.js.
// Thin wrappers translate module state into the pure helpers.
function _harmonizeLayerList() {
  return state.layers.map(l => ({
    visible: l.visible,
    id: l.id,
    canvas: l.canvas,
    offset: state.layerOffsets.get(l.id) || { x: 0, y: 0 },
  }));
}
function _buildLayerUnionAlpha() { return _layerUnionAlphaImpl(state.imgWidth, state.imgHeight, _harmonizeLayerList()); }
function _buildSeamMask(featherPx = 12) { return _seamMaskImpl(state.imgWidth, state.imgHeight, _harmonizeLayerList(), featherPx); }
function _buildLayerBodyMask(featherPx = 12) { return _layerBodyMaskImpl(state.imgWidth, state.imgHeight, _harmonizeLayerList(), featherPx); }

export function exportPNG() {
  return flatten().toDataURL('image/png');
}

// Briefly turn the Save button green with a checkmark so the user can't
// miss a successful save (the toast alone is easy to miss on remote
// connections where focus drifts during the upload).
function _flashSaveButtonOk() {
  const btn = document.getElementById('ge-save-menu-btn');
  if (!btn) return;
  const origHTML = btn.innerHTML;
  const origBg = btn.style.background;
  btn.style.background = '#3aa75a';
  btn.style.color = '#fff';
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-3px;margin-right:4px;"><polyline points="20 6 9 17 4 12"/></svg>Saved';
  setTimeout(() => {
    btn.style.background = origBg;
    btn.style.color = '';
    btn.innerHTML = origHTML;
  }, 1800);
}

// Show whirlpool + label on the visible "Save ▾" topbar button while a
// save operation runs. Returns a function to call when done (or in finally).
function _saveButtonBusy(label) {
  const btn = document.getElementById('ge-save-menu-btn');
  if (!btn) return () => {};
  const origHTML = btn.innerHTML;
  const origWidth = btn.offsetWidth;
  btn.disabled = true;
  btn.style.minWidth = origWidth + 'px';
  btn.innerHTML = '';
  let sp = null;
  try {
    sp = spinnerModule.create('', 'clean', 'whirlpool');
    btn.appendChild(sp.createElement());
    const txt = document.createElement('span');
    txt.className = 'ge-btn-busy-label';
    txt.textContent = label || 'Saving…';
    btn.appendChild(txt);
    sp.start();
  } catch { btn.textContent = label || 'Saving…'; }
  return () => {
    try { sp && sp.stop && sp.stop(); } catch {}
    btn.disabled = false;
    btn.innerHTML = origHTML;
    btn.style.minWidth = '';
  };
}

function _defaultExportName() {
  return String(state.draftName || 'edited-image')
    .replace(/×/g, 'x')
    .replace(/\.[^.]+$/, '')
    .replace(/[^a-z0-9 _-]+/gi, '')
    .replace(/\s+/g, '-')
    .trim() || 'edited-image';
}

export async function exportToGallery() {
  let blob = null;
  let savedOk = false;
  const t0 = performance.now();
  const flat = flatten();
  const settings = await _openExportDialog({
    sourceCanvas: flat,
    defaultName: _defaultExportName(),
    title: 'Save Copy',
    submitLabel: 'Save Copy',
    attachColorPicker,
    returnFocus: document.getElementById('ge-save-menu-btn'),
  });
  if (!settings) return;
  const endBusy = _saveButtonBusy('Saving copy…');
  try {
    const encoded = await _encodeExportCanvas(flat, settings);
    blob = encoded.blob;
    const formData = new FormData();
    formData.append('file', blob, `${settings.filename}.${encoded.extension}`);

    const saveRes = await fetch(`${API_BASE}/api/gallery/upload`, {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });
    if (!saveRes.ok) {
      const errBody = await saveRes.text().catch(() => '');
      throw new Error(`HTTP ${saveRes.status}: ${errBody.substring(0, 120)}`);
    }
    const totalMs = Math.round(performance.now() - t0);
    window.dispatchEvent(new CustomEvent('gallery-refresh'));
    if (uiModule) uiModule.showToast(`Saved copy to gallery (${(blob.size / 1024 / 1024).toFixed(1)}MB · ${(totalMs / 1000).toFixed(1)}s)`, 4000);
    savedOk = true;
    if (state.draftId) {
      _clearDraftServer(state.draftId);
      state.draftId = null;
    }
  } catch (e) {
    console.error('[save-as-copy] error:', e);
    const sizeMB = blob ? ` (${(blob.size / 1024 / 1024).toFixed(1)}MB)` : '';
    let msg = e?.message || 'unknown';
    if (e?.name === 'TypeError' || /fetch|network|load failed/i.test(msg)) {
      msg = `network dropped${sizeMB} — check connection`;
    } else {
      msg += sizeMB;
    }
    if (uiModule) uiModule.showToast('Save failed: ' + msg, 6000);
  } finally {
    endBusy();
    if (savedOk) _flashSaveButtonOk();
  }
}

// Open the Cookbook modal scoped to img2img-capable models so the user
// can serve one in a few clicks. Falls back to plain Cookbook if the
// filter hook isn't available.
// Open Cookbook on its Dependencies tab and highlight a specific
// package row. Used for "rembg not installed" → install path.
function _openCookbookForDependency(pkgName) {
  // Use cookbookModule.open({ tab: 'Dependencies' }) so the intent is
  // honored after Cookbook's async render. The old path clicked the
  // sidebar button + polled for the modal, but Cookbook's _renderRecipes
  // runs AFTER an awaited _syncFromServer, so depsTab.click() often
  // raced and the user landed on Download.
  const cookbook = window.cookbookModule;
  if (!cookbook || typeof cookbook.open !== 'function') {
    // Fall back to the old click-then-poll path if the module isn't
    // on window for some reason.
    const btn = document.getElementById('tool-cookbook-btn');
    if (btn) btn.click();
    else if (uiModule) uiModule.showToast(`Open Cookbook to install ${pkgName}`, 6000);
    return;
  }
  cookbook.open({ tab: 'Dependencies' });
  // Now wait for the Dependencies group to render, switch the server
  // selector to Local, and highlight the package row.
  const cb = document.getElementById('cookbook-modal');
  if (cb) cb.style.zIndex = 260;
  const tryServer = (attempt = 0) => {
    const serverSel = document.getElementById('hwfit-deps-server');
    if (!serverSel) {
      if (attempt < 25) return setTimeout(() => tryServer(attempt + 1), 80);
      return;
    }
    if (serverSel.value !== 'local') {
      serverSel.value = 'local';
      serverSel.dispatchEvent(new Event('change', { bubbles: true }));
    }
  };
  tryServer();
  const tryHighlight = (a2 = 0) => {
    const rows = document.querySelectorAll('[data-pkg-name]');
    if (!rows.length) {
      if (a2 < 40) return setTimeout(() => tryHighlight(a2 + 1), 100);
      return;
    }
    const row = Array.from(rows).find(r => (r.dataset.pkgName || '').toLowerCase() === pkgName.toLowerCase());
    if (row) {
      row.scrollIntoView({ block: 'center' });
      row.classList.add('cookbook-pkg-flash');
      setTimeout(() => row.classList.remove('cookbook-pkg-flash'), 2000);
    }
  };
  tryHighlight();
}

// Async check whether `rembg` is installed on the Odysseus server.
// Toggles the "install rembg" notice + the Bg Remove run button. The
// `/api/cookbook/packages` endpoint is cheap (importlib calls only).
async function _checkRembgInstalled() {
  const noticeEl = document.getElementById('ge-rembg-dep-missing');
  const runRow = document.getElementById('ge-rembg-run-row');
  if (!noticeEl || !runRow) return;
  // Use cached result if we already checked this editor session.
  if (state.rembgInstalledCache !== null) {
    noticeEl.style.display = state.rembgInstalledCache ? 'none' : '';
    runRow.style.display = state.rembgInstalledCache ? '' : 'none';
    return;
  }
  try {
    const r = await fetch('/api/cookbook/packages', { credentials: 'same-origin' });
    if (!r.ok) throw new Error('packages query failed');
    const data = await r.json();
    const pkg = (data.packages || []).find(p => (p.name || '').toLowerCase() === 'rembg');
    state.rembgInstalledCache = pkg ? !!pkg.installed : null;
  } catch (e) {
    state.rembgInstalledCache = null; // unknown — fall back to silent
  }
  if (state.rembgInstalledCache === false) {
    noticeEl.style.display = '';
    runRow.style.display = 'none';
  } else {
    noticeEl.style.display = 'none';
    runRow.style.display = '';
  }
}

function _openCookbookForImg2img() {
  // Try multiple openers in order — the sidebar button may be hidden on
  // mobile so we fall back to the rail button, then to modalManager.
  let opened = false;
  const btn = document.getElementById('tool-cookbook-btn');
  const railBtn = document.getElementById('rail-cookbook');
  if (btn && btn.offsetParent !== null) { btn.click(); opened = true; }
  else if (railBtn) { railBtn.click(); opened = true; }
  else { try { modalManager.restore('cookbook-modal'); opened = true; } catch {} }
  if (opened) {
    // Two-stage navigation: 1) wait for modal mount, 2) click Serve tab,
    // 3) after the serve tag chips render, click the "image" one.
    const tryServe = (attempt = 0) => {
      const cb = document.getElementById('cookbook-modal');
      const serveTab = cb ? cb.querySelector('.cookbook-tab[data-backend="Serve"]') : null;
      // Retry until BOTH the modal mounts AND its tab bar has rendered.
      // Cookbook builds its body html after the modal opens, so we need
      // to wait a bit longer than just "modal exists".
      if (!cb || !serveTab) {
        if (attempt < 40) return setTimeout(() => tryServe(attempt + 1), 80);
        return;
      }
      cb.style.zIndex = 260;
      serveTab.click();
      // Now wait for the serve-tags container to populate (it lazy-loads
      // after the cached-models fetch resolves) and click the image chip.
      const tryImageFilter = (a2 = 0) => {
        const tags = document.getElementById('serve-tags');
        if (!tags || !tags.querySelector('.memory-cat-chip')) {
          if (a2 < 20) return setTimeout(() => tryImageFilter(a2 + 1), 100);
          return;
        }
        const imgChip = Array.from(tags.querySelectorAll('.memory-cat-chip'))
          .find(c => /^image$/i.test(c.dataset.serveTag || '') || /image/i.test(c.textContent || ''));
        if (imgChip) imgChip.click();
      };
      tryImageFilter();
    };
    tryServe();
    return;
  }
  if (uiModule) uiModule.showToast('Open Cookbook from the sidebar to serve an img2img model', 6000);
}

export async function downloadPNG() {
  const source = flatten();
  const settings = await _openExportDialog({
    sourceCanvas: source,
    defaultName: _defaultExportName(),
    attachColorPicker,
    returnFocus: document.getElementById('ge-save-menu-btn'),
  });
  if (!settings) return;
  try {
    const { blob, extension } = await _encodeExportCanvas(source, settings);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${settings.filename}.${extension}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    if (uiModule) uiModule.showToast(`Exported ${(blob.size / 1024 / 1024).toFixed(1)} MB`);
  } catch (error) {
    console.error('[image-export] error:', error);
    if (uiModule) uiModule.showToast('Export failed: ' + (error?.message || 'unknown error'), 5000);
  }
}

// Save the entire layered editor state as a JSON project file. Each
// layer is encoded as a base64 PNG so transparency / partial alpha
// survives the round-trip. Use Load Project to restore.
function _saveProject() {
  if (!state.layers.length) {
    if (uiModule) uiModule.showToast('Nothing to save');
    return;
  }
  const project = _serializeEditorDocument(state, {
    type: 'odysseus-gallery-editor-project',
  });
  const json = JSON.stringify(project);
  if (json.length > _EDITOR_PROJECT_MAX_BYTES) {
    if (uiModule) uiModule.showToast('Project exceeds the 256 MB export safety limit. Flatten or remove layers first.', 8000);
    return;
  }
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'project.geproj.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (uiModule) uiModule.showToast('Project saved', 3000);
}

// Open-file picker for Load Project. Restores layers + canvas size.
function _loadProjectPrompt() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = 'application/json,.json';
  inp.addEventListener('change', async () => {
    const file = inp.files && inp.files[0];
    if (!file) return;
    let previousSnapshot = null;
    let previousView = null;
    try {
      if (file.size > _EDITOR_PROJECT_MAX_BYTES) {
        throw new Error('Project file exceeds the 256 MB safety limit.');
      }
      const text = await file.text();
      const proj = JSON.parse(text);
      if (proj.type !== 'odysseus-gallery-editor-project') {
        if (uiModule) uiModule.showToast('Not a project file', 5000);
        return;
      }
      previousSnapshot = _snapshotState();
      previousView = _normalizeEditorView(state);
      const report = await _restoreDraft(proj);
      previousSnapshot._label = 'Before load project';
      previousSnapshot._ts = Date.now();
      state.undoStack = [previousSnapshot];
      state.redoStack = [];
      composite();
      _renderLayerPanel();
      _fitZoom();
      if (uiModule) uiModule.showToast('Project loaded', 3000);
      _showDocumentRestoreReport(report);
    } catch (e) {
      if (previousSnapshot) {
        Object.assign(state, previousView);
        _restoreState(previousSnapshot);
      }
      if (uiModule) uiModule.showToast('Load failed: ' + (e.message || e), 6000);
    }
  });
  inp.click();
}

// ── Public API ──

// Styled in-app prompt for canvas size — replaces the browser's
// native prompt() which doesn't follow the app theme. Returns a Promise
// resolving to {w, h} on submit, or null on cancel. Optional opts:
//   title, okLabel, initialW, initialH.
function _promptCanvasSize(opts) {
  opts = opts || {};
  const title    = opts.title    || 'New project';
  const okLabel  = opts.okLabel  || 'Create';
  const initialW = opts.initialW || 1024;
  const initialH = opts.initialH || 1024;
  return new Promise(resolve => {
    let overlay = document.getElementById('ge-canvas-size-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'ge-canvas-size-overlay';
      overlay.className = 'modal';
      overlay.innerHTML = _canvasSizePromptHTML();
      document.body.appendChild(overlay);
    }
    overlay.style.display = '';
    overlay.classList.remove('hidden');
    const wInput = document.getElementById('ge-canvas-prompt-w');
    const hInput = document.getElementById('ge-canvas-prompt-h');
    const okBtn = document.getElementById('ge-canvas-prompt-ok');
    const cancelBtn = document.getElementById('ge-canvas-prompt-cancel');
    const titleEl = document.getElementById('ge-canvas-prompt-title');
    const presetButtons = overlay.querySelectorAll('[data-canvas-preset]');
    const presetGrid = overlay.querySelector('.ge-canvas-preset-grid');
    const customLabel = overlay.querySelector('.ge-canvas-prompt-custom-label');
    const anchorButtons = overlay.querySelectorAll('[data-canvas-anchor]');
    const anchorOptions = overlay.querySelector('.ge-canvas-anchor-options');
    const resizeOptions = overlay.querySelector('.ge-canvas-resize-options');
    const lockInput = document.getElementById('ge-canvas-prompt-lock');
    const interpolationField = overlay.querySelector('.ge-canvas-interpolation-field');
    const interpolationInput = document.getElementById('ge-canvas-prompt-interpolation');
    const unitsField = overlay.querySelector('.ge-canvas-units-field');
    const unitsInput = document.getElementById('ge-canvas-prompt-units');
    if (titleEl) titleEl.textContent = title;
    if (okBtn) okBtn.textContent = okLabel;
    const showUnits = !!opts.showUnits;
    const initialUnit = showUnits && opts.units === 'percent' ? 'percent' : 'px';
    if (unitsField) unitsField.hidden = !showUnits;
    if (unitsInput) unitsInput.value = initialUnit;
    if (presetGrid) presetGrid.hidden = opts.showPresets === false;
    if (customLabel) customLabel.hidden = opts.showPresets === false;
    wInput.value = initialUnit === 'percent' ? '100' : String(initialW);
    hInput.value = initialUnit === 'percent' ? '100' : String(initialH);
    if (anchorOptions) anchorOptions.hidden = !opts.showAnchor;
    if (resizeOptions) resizeOptions.hidden = !opts.showInterpolation && !opts.keepProportions;
    if (interpolationField) interpolationField.hidden = !opts.showInterpolation;
    if (lockInput) {
      lockInput.checked = !!opts.keepProportions;
      lockInput.hidden = !opts.keepProportions;
    }
    presetButtons.forEach(button => button.classList.remove('active'));
    anchorButtons.forEach((button, index) => button.classList.toggle('active', index === 0));
    setTimeout(() => { wInput.focus(); wInput.select(); }, 0);
    function cleanup(result) {
      overlay.style.display = 'none';
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onBackdrop);
      document.removeEventListener('keydown', onKey);
      overlay.removeEventListener('keydown', onKey, true);
      if (overlay._cancelCanvasSize === onCancel) delete overlay._cancelCanvasSize;
      presetButtons.forEach(button => button.removeEventListener('click', onPreset));
      anchorButtons.forEach(button => button.removeEventListener('click', onAnchor));
      wInput.removeEventListener('input', onWidthInput);
      hInput.removeEventListener('input', onHeightInput);
      unitsInput?.removeEventListener('change', onUnitsChange);
      resolve(result);
    }
    function onOk() {
      const unit = unitsInput?.value === 'percent' ? 'percent' : 'px';
      const dims = unit === 'percent'
        ? _parsePercentSizePrompt(wInput.value, hInput.value, initialW, initialH)
        : _parseCanvasSizePrompt(wInput.value, hInput.value, initialW, initialH);
      if (!dims) { uiModule.showToast('Invalid size'); return; }
      const anchor = overlay.querySelector('[data-canvas-anchor].active')?.dataset.canvasAnchor || '0,0';
      const [anchorX, anchorY] = anchor.split(',').map(Number);
      cleanup({
        ...dims,
        anchorX,
        anchorY,
        interpolation: interpolationInput?.value || 'high',
        keepProportions: !!lockInput?.checked,
        units: unit,
      });
    }
    function onCancel() { cleanup(null); }
    overlay._cancelCanvasSize = onCancel;
    function onPreset(e) {
      const match = String(e.currentTarget.dataset.canvasPreset || '').match(/^(\d+)x(\d+)$/);
      if (!match) return;
      wInput.value = match[1];
      hInput.value = match[2];
      presetButtons.forEach(button => button.classList.toggle('active', button === e.currentTarget));
    }
    function onAnchor(e) {
      anchorButtons.forEach(button => button.classList.toggle('active', button === e.currentTarget));
    }
    const ratio = Number(initialW) / Math.max(1, Number(initialH));
    function onWidthInput() {
      if (!lockInput?.checked || !Number.isFinite(ratio) || ratio <= 0) return;
      const width = Number(wInput.value);
      if (Number.isFinite(width) && width > 0) hInput.value = unitsInput?.value === 'percent'
        ? String(Math.max(1, Math.min(1000, Math.round(width))))
        : String(Math.max(1, Math.round(width / ratio)));
    }
    function onHeightInput() {
      if (!lockInput?.checked || !Number.isFinite(ratio) || ratio <= 0) return;
      const height = Number(hInput.value);
      if (Number.isFinite(height) && height > 0) wInput.value = unitsInput?.value === 'percent'
        ? String(Math.max(1, Math.min(1000, Math.round(height))))
        : String(Math.max(1, Math.round(height * ratio)));
    }
    function onUnitsChange() {
      const percent = unitsInput?.value === 'percent';
      const width = Number(wInput.value);
      const height = Number(hInput.value);
      if (percent) {
        wInput.value = String(Math.max(1, Math.min(100, Math.round(width / initialW * 100)))) || '100';
        hInput.value = String(Math.max(1, Math.min(100, Math.round(height / initialH * 100)))) || '100';
      } else {
        wInput.value = String(Math.max(1, Math.round(initialW * width / 100)));
        hInput.value = String(Math.max(1, Math.round(initialH * height / 100)));
      }
    }
    function onBackdrop(e) { if (e.target === overlay) cleanup(null); }
    function onKey(e) {
      if (e.key === 'Enter') { e.preventDefault(); onOk(); }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cleanup(null); }
    }
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    presetButtons.forEach(button => button.addEventListener('click', onPreset));
    anchorButtons.forEach(button => button.addEventListener('click', onAnchor));
    wInput.addEventListener('input', onWidthInput);
    hInput.addEventListener('input', onHeightInput);
    unitsInput?.addEventListener('change', onUnitsChange);
    overlay.addEventListener('click', onBackdrop);
    document.addEventListener('keydown', onKey);
    // Capture Escape on the dialog itself so global modal/shortcut handlers
    // cannot hide the prompt without resolving its pending Promise.
    overlay.addEventListener('keydown', onKey, true);
  });
}

function _parseCanvasSizePrompt(widthText, heightText, initialW = 1024, initialH = 1024) {
  const parseWhole = (value) => {
    const text = String(value || '').trim();
    if (!/^\d+$/.test(text)) return null;
    const n = Number(text);
    return Number.isSafeInteger(n) && n >= 1 && n <= 8192 ? n : null;
  };
  const parseRatio = (value) => {
    const m = String(value || '').trim().match(/^(\d+(?:\.\d+)?)\s*(?:x|×|:|\/)\s*(\d+(?:\.\d+)?)$/i);
    if (!m) return null;
    const rw = Number(m[1]);
    const rh = Number(m[2]);
    if (!Number.isFinite(rw) || !Number.isFinite(rh) || rw <= 0 || rh <= 0) return null;
    const w = Math.max(1, Math.min(8192, Math.round(initialW)));
    const h = Math.max(1, Math.min(8192, Math.round(w * rh / rw)));
    return { w, h };
  };
  const ratioDims = parseRatio(widthText) || parseRatio(heightText);
  if (ratioDims) return ratioDims;
  const w = parseWhole(widthText);
  const h = parseWhole(heightText);
  if (!w || !h) return null;
  return { w, h };
}

function _parsePercentSizePrompt(widthText, heightText, initialW, initialH) {
  const parsePercent = (value) => {
    const n = Number(String(value || '').trim());
    return Number.isFinite(n) && n >= 1 && n <= 1000 ? n : null;
  };
  const width = parsePercent(widthText);
  const height = parsePercent(heightText);
  if (width == null || height == null) return null;
  return {
    w: Math.max(1, Math.min(8192, Math.round(initialW * width / 100))),
    h: Math.max(1, Math.min(8192, Math.round(initialH * height / 100))),
  };
}

// imageUrl=null + presetSize={w,h} → skips the size prompt and creates a
// blank canvas at the given dimensions (used by template tiles in the
// gallery's Edit-tab landing). `displayName` is optional — when provided,
// the Edit tab in the gallery is renamed to "Edit: <name>".
// Shared loading-overlay mount/unmount — used by the image-load path AND
// the draft-restore paths so every "we're waiting on something" moment
// in the editor surfaces the same whirlpool + label instead of a blank
// canvas that looks broken.
function _mountEditorLoading(label, dims) {
  if (!state.container) return;
  const area = state.container.querySelector('.ge-canvas-area');
  _unmountEditorLoading();
  // Cover the WHOLE editor (toolbar + canvas + panel), not just the canvas area
  // — otherwise the toolbar/old content shows above the overlay at the top while
  // a past project loads, which looks half-rendered.
  const el = document.createElement('div');
  el.className = 'ge-loading-overlay ge-loading-overlay-full';
  // Aspect-ratio placeholder so the user sees the shape of the canvas they're
  // about to land in. Sized to the canvas area but centered in the overlay.
  let placeholder = null;
  if (dims && dims.w > 0 && dims.h > 0 && area) {
    placeholder = document.createElement('div');
    placeholder.className = 'ge-canvas-placeholder';
    const areaRect = area.getBoundingClientRect();
    const maxW = Math.max(0, areaRect.width - 32);
    const maxH = Math.max(0, areaRect.height - 32);
    const ratio = dims.w / dims.h;
    let w = maxW;
    let h = w / ratio;
    if (h > maxH) { h = maxH; w = h * ratio; }
    placeholder.style.width = w + 'px';
    placeholder.style.height = h + 'px';
    el.appendChild(placeholder);
  }
  const inner = document.createElement('div');
  inner.className = 'ge-loading-inner';
  inner.innerHTML = `<span class="ge-loading-text">${label || 'Loading…'}</span>`;
  el.appendChild(inner);
  // Mount on the editor BODY (toolbar + canvas + panel) — it sits below the
  // gallery's search/select bar, so the cover doesn't bleed up over those.
  const _mountTarget = state.container.querySelector('.ge-editor-body') || state.container;
  _mountTarget.appendChild(el);
  try {
    const sp = spinnerModule.create('', 'clean', 'whirlpool');
    inner.insertBefore(sp.createElement(), inner.firstChild);
    sp.start();
    el._spinner = sp;
  } catch {}
  el._placeholder = placeholder;
  state.editorLoadingEl = el;
}
function _unmountEditorLoading() {
  if (!state.editorLoadingEl) return;
  try { state.editorLoadingEl._spinner?.destroy(); } catch {}
  try { state.editorLoadingEl._placeholder?.remove(); } catch {}
  try { state.editorLoadingEl.remove(); } catch {}
  state.editorLoadingEl = null;
}

export function openEditor(imageUrl, imageId, presetSize, displayName, draftId) {
  _activeFilterPrompt?.cancel();
  _setEditTabLabel(displayName || (presetSize ? 'New canvas' : 'Untitled'));
  state.imageId = imageId || null;
  // Track original file extension so save-over-original can re-encode in the
  // same format. JPEG re-encoding cuts upload size 5-10x for camera photos,
  // which matters over remote tunnels (Tailscale Funnel etc.).
  try {
    const m = (imageUrl || '').match(/\.([a-z0-9]{2,5})(?:\?|$)/i);
    state.originalExt = m ? m[1].toLowerCase() : 'png';
  } catch { state.originalExt = 'png'; }
  state.draftId = draftId || null;
  state.imageUrl = imageUrl || null;
  state.draftName = displayName || (presetSize ? `New ${presetSize.w}×${presetSize.h}` : 'Untitled');
  state.editorSessionToken += 1;
  state.editorOpen = true;
  _writeActiveEditorSession();
  state.layers = [];
  state.selectedLayerIds = [];
  state.selectionAnchorId = null;
  state.layerGroups = [];
  state.activeGroupId = null;
  state.undoStack = [];
  state.redoStack = [];
  state.layerOffsets.clear();
  state.nextLayerId = 1;
  state.tool = 'move';
  state.gradientActive = false;
  state.gradientStops = [];
  state.compareBaselineCanvas = null;
  state.compareActive = false;
  state.transformActive = false;
  state.transformTarget = null;
  state.transformLayer = null;
  state.transformSelectionCanvas = null;
  state.transformLayers = [];
  state.transformItems = [];
  state.transformSelectionBounds = null;
  state.transformBounds = null;
  state.transformCenter = null;
  state.panX = 0;
  state.panY = 0;
  state.spacePanActive = false;
  state.navigationPanning = false;
  state.guides = { vertical: [], horizontal: [] };
  state.cropRect = null;
  state.lassoPoints = [];
  state.lassoActive = false;
  state.marqueeActive = false;
  state.marqueeStart = null;
  state.marqueeRect = null;
  state.wandMask = null;
  state.wandLayerId = null;
  state.wandMaskSpace = 'layer';
  state.selectionSource = null;
  state.wandLastSeed = null;
  state.quickMaskActive = false;
  state.savedSelections = [];
  state.lastSelection = null;
  state.selectionMoving = false;
  state.selectionMoveStart = null;
  state.selectionMoveOrigin = null;
  _selectionBoundaryCache = null;
  _stopSelectionAnimation();
  window.__galleryEditLive = true;
  if (state.persistTimer) { clearTimeout(state.persistTimer); state.persistTimer = null; }
  state.persistDirty = false;
  state.persistErrorMessage = null;
  state.persistInFlight = null;

  state.container = document.getElementById('gallery-editor-container');
  if (!state.container) {
    console.error('[openEditor] #gallery-editor-container not found in DOM — editor cannot open');
    if (uiModule) uiModule.showError('Editor container missing');
    return;
  }
  state.container.style.display = 'flex';

  try {
    _buildEditor(state.container);
  } catch (e) {
    console.error('[openEditor] _buildEditor threw:', e);
    if (uiModule) uiModule.showError('Editor failed to build: ' + (e?.message || 'unknown'));
    return;
  }

  function _initCanvas(w, h) {
    state.imgWidth = w;
    state.imgHeight = h;
    state.mainCanvas.width = w;
    state.mainCanvas.height = h;
    state.maskCanvas = document.createElement('canvas');
    state.maskCanvas.width = w;
    state.maskCanvas.height = h;
    state.maskCtx = state.maskCanvas.getContext('2d');
  }

  if (!imageUrl && draftId) {
    // Re-open a saved draft by its server-side id — covers the
    // "Resume" buttons on the Edit-tab landing.
    _mountEditorLoading('Loading draft…', presetSize || null);
    // Bail if the user closes the editor while the async load is in
    // flight — without this guard, the .then() callbacks fire after
    // closeEditor and re-mount the spinner / draw into a dead canvas,
    // leaving "stuck" preview artefacts on the next open.
    return _loadDraftById(draftId)
      .then(d => {
        if (!state.editorOpen) return;
        if (!d) {
          _unmountEditorLoading();
          if (uiModule) uiModule.showToast('Draft not found');
          closeEditor();
          return;
        }
        state.draftId = d.id;
        state.draftName = d.name || 'Untitled';
        _writeActiveEditorSession();
        _setEditTabLabel(state.draftName);
        state.imageId = d.source_image_id || null;
        return _restoreDraft(d).then(report => {
          if (!state.editorOpen) return;
          composite();
          _renderLayerPanel();
          _fitZoom();
          _setDraftStatus('Saved', 'saved');
          const sizeLabel = document.getElementById('ge-canvas-size');
          if (sizeLabel) sizeLabel.textContent = `${state.imgWidth}×${state.imgHeight}`;
          _unmountEditorLoading();
          if (uiModule) uiModule.showToast('Resumed draft');
          _showDocumentRestoreReport(report);
        });
      })
      .catch(err => {
        if (!state.editorOpen) return;
        _unmountEditorLoading();
        console.warn('[ge] draft load failed', err);
        if (uiModule) uiModule.showToast(`Failed to load draft: ${err?.message || 'invalid project data'}`, 7000);
        closeEditor();
      });
  }

  if (!imageUrl) {
    // Empty canvas — use preset size if supplied, otherwise show the
    // styled prompt. Asynchronous: we promise-chain so callers can await
    // openEditor() and still rely on isEditorOpen() afterwards.
    const _finishBlank = (w, h) => {
      _initCanvas(w, h);
      // White-filled Background so the canvas is visible, then a separate
      // transparent Edit layer on top — keeps user's work isolated from
      // the underlying canvas, the standard editor pattern.
      const bgLayer = createLayer('Background', w, h);
      bgLayer.ctx.fillStyle = '#ffffff';
      bgLayer.ctx.fillRect(0, 0, w, h);
      const editLayer = createLayer('Edit', w, h);
      state.layers.push(bgLayer);
      state.layers.push(editLayer);
      state.activeLayerId = editLayer.id;
      // Refresh recovery needs the chosen canvas dimensions even before the
      // first debounced server draft has been created.
      _writeActiveEditorSession();
      composite();
      _renderLayerPanel();
      _fitZoom();
      // First persist creates the server-side row (blank-canvas drafts).
      _schedulePersist();
    };
    if (presetSize && presetSize.w > 0 && presetSize.h > 0) {
      _finishBlank(presetSize.w, presetSize.h);
      return;
    }
    return _promptCanvasSize().then(size => {
      if (!size) {
        // The editor shell is mounted before the prompt opens, so the normal
        // user-facing close guard would mistake this cancellation for an
        // attempt to close an active edit. Treat the cancelled setup as an
        // internal teardown instead.
        closeEditor({ force: true });
        window.dispatchEvent(new CustomEvent('gallery-editor-setup-cancelled'));
        return;
      }
      _finishBlank(size.w, size.h);
    });
  }

  // Try to restore a previously-persisted draft for this image — that
  // way closing the gallery / editor mid-edit doesn't lose progress.
  // (Server-backed: look up by source_image_id.)
  _mountEditorLoading('Looking up draft…');
  _findDraftForImage(imageId).then(_draft => {
    if (!state.editorOpen) return;
    if (!_draft) return null;
    state.draftId = _draft.id;
    state.draftName = _draft.name || displayName || 'Untitled';
    _writeActiveEditorSession();
    const innerLabel = state.editorLoadingEl?.querySelector('.ge-loading-text');
    if (innerLabel) innerLabel.textContent = 'Resuming draft…';
    return _restoreDraft(_draft).then(report => {
      if (!state.editorOpen) return null;
      // If the draft was broken/empty (0 layers reconstructed), fall
      // through to loading the source image as a normal edit. Without
      // this guard the editor would sit empty and the user would be
      // stuck with no way to recover.
      if (state.layers.length === 0) {
        console.warn('[openEditor] draft restored but produced 0 layers — falling back to source image');
        return null;
      }
      composite();
      _renderLayerPanel();
      _fitZoom();
      _setDraftStatus('Saved', 'saved');
      const sizeLabel = document.getElementById('ge-canvas-size');
      if (sizeLabel) sizeLabel.textContent = `${state.imgWidth}×${state.imgHeight}`;
      _unmountEditorLoading();
      if (uiModule) uiModule.showToast('Resumed previous edit');
      _showDocumentRestoreReport(report);
      return 'restored';
    });
  }).then(restored => {
    if (!state.editorOpen) return;
    if (restored) return;
    _loadSourceImage();
  }).catch(err => {
    if (!state.editorOpen) return;
    _unmountEditorLoading();
    console.warn('[openEditor] draft lookup failed', err);
    _loadSourceImage();
  });
  function _loadSourceImage() {

  // Loading overlay — whirlpool + "Loading" label while the source image
  // downloads / decodes. Especially important for multi-MB photos where
  // the canvas would otherwise sit blank for several seconds with no
  // feedback. If a draft-lookup overlay is already mounted, reuse it.
  if (!state.editorLoadingEl) _mountEditorLoading('Loading…');
  else {
    const inner = state.editorLoadingEl.querySelector('.ge-loading-text');
    if (inner) inner.textContent = 'Loading…';
  }
  const _removeLoading = () => _unmountEditorLoading();

  // Load image — single layer named "Photo" (no extra Edit layer; the
  // user can add one manually if they want isolated edits).
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    if (!state.editorOpen) return;
    _initCanvas(img.naturalWidth, img.naturalHeight);
    const photoLayer = createLayer('Photo', state.imgWidth, state.imgHeight);
    photoLayer.ctx.drawImage(img, 0, 0);
    photoLayer.isBase = true;
    state.layers.push(photoLayer);
    state.activeLayerId = photoLayer.id;
    composite();
    _renderLayerPanel();
    _fitZoom();
    _removeLoading();
    _schedulePersist();
  };
  img.onerror = (e) => {
    console.error('[_loadSourceImage] onerror — failed to load', imageUrl, e);
    _removeLoading();
    if (uiModule) uiModule.showToast('Failed to load image');
    closeEditor();
  };
  img.src = imageUrl;
  }
}

// Update the gallery's Edit tab label to reflect what's currently open.
// Pass null to reset to plain "Edit". Only mutates the inner label span
// so the SVG icon next to it survives the update.
function _setEditTabLabel(name) {
  const tab = document.getElementById('gallery-editor-tab');
  if (!tab) return;
  const labelEl = tab.querySelector('.gallery-tab-label') || tab;
  if (!name) {
    labelEl.textContent = 'Edit';
    tab.classList.remove('has-edit');
    return;
  }
  const trimmed = name.length > 24 ? name.slice(0, 22) + '…' : name;
  labelEl.textContent = `Edit: ${trimmed}`;
  tab.classList.add('has-edit');
}

export function closeEditor(options = {}) {
  _penTool.cancel();
  const force = options === true || options.force === true;
  const editorMounted = _galleryEditMounted();
  if ((state.editorOpen || editorMounted) && !force && !window.__galleryAllowCloseEditor) {
    try { uiModule.showToast('Close the edit tab first'); } catch {}
    return false;
  }
  _activeFilterPrompt?.cancel();
  if (_textEditor?.isOpen()) _textEditor.close(true);
  // Flush any pending debounced persist + fire one final save so closing
  // the editor mid-stroke doesn't lose work. The call is fire-and-forget;
  // the server commit lands shortly after the modal hides.
  if (state.persistTimer) { clearTimeout(state.persistTimer); state.persistTimer = null; }
  if (state.layers.length) {
    try { _persistDraft(); } catch {}
  }
  // Invalidate asynchronous encoding/network callbacks before tearing down
  // the document, so a late save cannot mutate the next editor session.
  state.editorSessionToken += 1;
  _setEditTabLabel(null);
  _clearActiveEditorSession();
  _unmountEditorLoading();
  state.editorOpen = false;
  _renderSavedSelectionsMenu = null;
  _stopSelectionAnimation();
  if (_selectionNudgeTimer) clearTimeout(_selectionNudgeTimer);
  _selectionNudgeTimer = null;
  _selectionNudgeHistorySaved = false;
  _precisionGuides = null;
  // Drop every document-level click-away handler registered by this
  // openEditor invocation. Without this, dropdown closers accumulated
  // across reopens (six handlers × N opens).
  while (state.editorDocClickHandlers.length) {
    const h = state.editorDocClickHandlers.pop();
    try { document.removeEventListener('click', h); } catch {}
  }
  if (state.cursorEl) { state.cursorEl.remove(); state.cursorEl = null; }
  // Tear down all floating popups + the dock so closing the editor
  // doesn't leave stale chips/panels behind on top of the gallery.
  try { _closeFxMenu(); } catch {}
  try { _closeAdjPopup(); } catch {}
  try { _closeHistoryPanel(); } catch {}
  try {
    const dock = document.getElementById('ge-fx-dock');
    if (dock) dock.remove();
  } catch {}
  try {
    document.querySelectorAll('.ge-inpaint-popup, .ge-fx-popup, .ge-adj-popup').forEach(el => {
      if (el._escHandler) {
        document.removeEventListener('keydown', el._escHandler, true);
      }
      // v2 review HIGH-2/3: unregister any modalManager entry left over
      // from FX-popup / History-panel minimise so _state and _LABELS
      // don't grow unboundedly across editor opens.
      if (el._modalId) {
        try { modalManager.unregister(el._modalId); } catch {}
      }
      el.remove();
    });
  } catch {}
  try {
    document.querySelectorAll('body > #ge-save-menu').forEach(el => el.remove());
  } catch {}
  // Belt-and-suspenders: scrub any minimized-dock chip + modalManager
  // entry whose id matches our ephemeral popups (in case the DOM node
  // was already removed when the user dragged the chip to trash).
  try {
    const dock = document.getElementById('minimized-dock');
    if (dock) {
      dock.querySelectorAll('[data-modal-id^="ge-fx-popup-"], [data-modal-id="ge-history-panel-min"]').forEach(c => {
        const mid = c.dataset.modalId;
        try { modalManager.unregister(mid); } catch {}
        c.remove();
      });
    }
  } catch {}
  if (state.container) {
    state.container.style.display = 'none';
    state.container.innerHTML = '';
  }
  state.layers = [];
  state.selectedLayerIds = [];
  state.selectionAnchorId = null;
  state.layerGroups = [];
  state.activeGroupId = null;
  state.undoStack = [];
  state.redoStack = [];
  state.layerOffsets.clear();
  state.mainCanvas = null;
  state.mainCtx = null;
  state.selectionOverlay = null;
  state.selectionOverlayCtx = null;
  state.quickMaskActive = false;
  state.documentCompositeCanvas = null;
  state.documentRenderReady = false;
  _renderGeneration.invalidate();
  _asyncCompositeQueued = false;
  _asyncCompositeInFlight = null;
  state.groupCompositeCanvas = null;
  state.groupCompositeCanvases = new Map();
  state.clippingCompositeCanvas = null;
  state.transformActive = false;
  state.transformTarget = null;
  state.transformLayer = null;
  state.transformSelectionCanvas = null;
  state.transformLayers = [];
  state.transformItems = [];
  state.transformSelectionBounds = null;
  state.transformBounds = null;
  state.transformCenter = null;
  state.maskCanvas = null;
  state.maskCtx = null;
  state.imageId = null;
  state.imageUrl = null;
  state.gradientActive = false;
  state.gradientStops = [];
  state.compareBaselineCanvas = null;
  state.compareActive = false;
  state.container = null;
  window.__galleryEditLive = false;
  return true;
}

export function isEditorOpen() {
  return state.editorOpen;
}

const galleryEditorModule = {
  openEditor,
  closeEditor,
  isEditorOpen,
  exportPNG,
  exportToGallery,
  downloadPNG,
};

export default galleryEditorModule;
