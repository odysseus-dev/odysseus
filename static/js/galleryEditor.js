/**
 * Gallery Editor — canvas-based image editor with layers, brush, eraser, text, crop, inpaint mask.
 */

import uiModule from './ui.js?v=20260916largetoolscroll1';
import dragSortModule from './dragSort.js';
import spinnerModule from './spinner.js';
import { attachColorPicker } from './colorPicker.js?v=20260910eyedropper1';
import modalManager from './modalManager.js';
import { canvasCoords as _canvasCoords, invalidateCanvasCoordsCache } from './editor/canvas-coords.js';
import { pickTopmostLayerAt as _pickTopmostLayerAt } from './editor/layer-hit-test.js';
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
  defaultEffectParams as _defaultEffectParams,
  EFFECT_TYPES_LIST as _effectTypesList,
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
import { dehazeImageData as _dehazeImageData } from './editor/filters/dehaze.js';
import {
  addNoise as _addNoise,
  despeckle as _despeckle,
  pixelate as _pixelate,
  crystallize as _crystallize,
  emboss as _emboss,
  findEdges as _findEdges,
  solarize as _solarize,
  twirl as _twirl,
  pinch as _pinch,
  spherize as _spherize,
  ripple as _ripple,
  clouds as _clouds,
  sharpen as _sharpen,
  sharpenEdges as _sharpenEdges,
  unsharpMask as _unsharpMask,
  customKernel as _customKernel,
  maximum as _maximum,
  minimum as _minimum,
  median as _median,
  polarCoordinates as _polarCoordinates,
  differenceClouds as _differenceClouds,
  replaceColor as _replaceColor,
  lensFlare as _lensFlare,
  fibers as _fibers,
  oilPaint as _oilPaint,
  lensCorrection as _lensCorrection,
  diffuse as _diffuse,
  oceanRipple as _oceanRipple,
  glass as _glass,
  mosaicTiles as _mosaicTiles,
  wind as _wind,
  traceContour as _traceContour,
  halftonePattern as _halftonePattern,
  reticulation as _reticulation,
  shear as _shear,
  notePaper as _notePaper,
  charcoal as _charcoal,
  chalkCharcoal as _chalkCharcoal,
  graphicPen as _graphicPen,
  photocopy as _photocopy,
  waterPaper as _waterPaper,
  sharpenMore as _sharpenMore,
  posterEdges as _posterEdges,
  cutout as _cutout,
  inkOutlines as _inkOutlines,
  stainedGlass as _stainedGlass,
  grain as _grain,
  texturizer as _texturizer,
  patchwork as _patchwork,
  craquelure as _craquelure,
  crosshatch as _crosshatch,
} from './editor/filters/effects.js';
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
import { buildToolbar as _buildToolbar, TOOLS as _TOOLS } from './editor/build/toolbar.js?v=20261010brushpresets';
import { buildOptionsBar as _buildOptionsBar, renderOptionsBar as _renderOptionsBar } from './editor/build/options-bar.js?v=20261009autoselect';
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
import { makeDraggable as _makeDraggable, resetDraggable as _resetDraggable } from './editor/draggable-dialog.js';
import { startEditorBridge as _startEditorBridge } from './editor/editor-bridge.js';
import { state } from './editor/state.js';
import {
  cloneDocumentValue as _cloneDocumentValue,
  EDITOR_PROJECT_MAX_BYTES as _EDITOR_PROJECT_MAX_BYTES,
  nextLayerIdFromDocument as _nextLayerIdFromDocument,
  normalizeEditorView as _normalizeEditorView,
  prepareEditorDocument as _prepareEditorDocument,
  serializeEditorDocument as _serializeEditorDocument,
} from './editor/document-codec.js';
import { parsePsd as _parsePsd, decodeLayeredPsd as _decodeLayeredPsd } from './editor/psd.js';

// Lightweight in-memory registry of documents open in this editor session
// (the editor mounts one document at a time; this lets the agent list,
//  duplicate, create and switch between them).
const _openDocuments = [];
let _activeDocumentSeq = 0;
import {
  snapshotByteSize as _snapshotByteSize,
  trimHistoryStack as _trimHistoryStack,
  shareSnapshotPixels as _shareSnapshotPixels,
} from './editor/history-budget.js';
import { cropDocument as _cropDocument, resizeCanvasDocument as _resizeCanvasDocument, resizeImageDocument as _resizeImageDocument } from './editor/document-geometry.js';
import { aspectFitCropRect as _aspectFitCropRect } from './editor/aspect-fit.js';
import { decodeAIImage as _decodeAIImage } from './editor/ai-operation.js';
import { encodeExportCanvas as _encodeExportCanvas, openExportDialog as _openExportDialog } from './editor/export-dialog.js';
import {
  normalizeTextData as _normalizeTextData,
  rasterizeTextLayer as _rasterizeTextLayer,
  renderTextLayer as _renderTextLayer,
  loadEditorFonts as _loadEditorFonts,
  availableEditorFonts as _availableEditorFonts,
  pathTextPadding as _pathTextPadding,
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
import { createLayerGeometryController, alignSelectedLayers as _alignSelectedLayers, distributeSelectedLayers as _distributeSelectedLayers } from './editor/layer-geometry.js';
import {
  drawGroupedLayers as _drawGroupedLayers,
  drawGroupedLayersAsync as _drawGroupedLayersAsync,
  groupAncestors as _groupAncestors,
  groupForLayer as _groupForLayer,
  allLayerIdsInGroup as _allLayerIdsInGroup,
  isLayerEffectivelyLocked as _isLayerEffectivelyLocked,
  isLayerPositionLocked as _isLayerPositionLocked,
  isLayerPixelLocked as _isLayerPixelLocked,
  isLayerTransparencyLocked as _isLayerTransparencyLocked,
  normalizeLayerLocks as _normalizeLayerLocks,
  normalizeLayerGroups as _normalizeLayerGroups,
} from './editor/layer-groups.js';
import { normalizeLayerClipping as _normalizeLayerClipping } from './editor/layer-clipping.js';
import { createCropTool } from './editor/tools/crop.js';
import { createLassoTool } from './editor/tools/lasso.js';
import { createPenSelectionTool } from './editor/tools/pen-selection.js';
import { createLiquifyTool } from './editor/tools/liquify.js';
import { createPenPathTool } from './editor/tools/pen-path.js';
import { LAYER_STYLES, styleParams } from './editor/layer-styles.js';
import { openLayerStyleMenu } from './editor/layer-style-menu.js';
import { createMarqueeTool } from './editor/tools/marquee.js';
import { createWandTool } from './editor/tools/wand.js';
import { createQuickSelectTool } from './editor/tools/quick-select.js';
import { createObjectSelectTool } from './editor/tools/object-select.js';
import { createPatchTool } from './editor/tools/patch.js';
import { createPerspectiveCropTool } from './editor/tools/perspective-crop.js';
import { perspectiveCropSize as _perspectiveCropSize, perspectiveCropCanvas as _perspectiveCropCanvas } from './editor/perspective-crop.js';
import { patchRegion as _patchRegion, contentAwareMove as _contentAwareMove } from './editor/patch-ops.js';
import { colorRangeMask as _colorRangeMask, hexToRgb as _hexToRgb } from './editor/tools/color-range.js';
import { computeSubjectMask as _computeSubjectMask } from './editor/select-subject.js';
import { ellipsePoints as _ellipsePoints, cubicBezierPoints as _cubicBezierPoints, quadraticBezierPoints as _quadraticBezierPoints, smoothPathPoints as _smoothPathPoints } from './editor/path-geometry.js';
import { openSelectAndMask } from './editor/select-and-mask.js';
import {
  createMarqueeMask as _createMarqueeMask,
  mergeSelectionMasks as _mergeSelectionMasks,
  paintSelectionBoundary as _paintSelectionBoundary,
  refineSelectionMask as _refineSelectionMask,
  selectionBoundary as _selectionBoundary,
  selectionMaskForLayer as _selectionMaskForLayer,
  selectionMaskToDocument as _selectionMaskToDocument,
  translateSelectionMask as _translateSelectionMask,
} from './editor/selection-mask.js';
import { createCloneTool } from './editor/tools/clone.js';
import { createEyedropperTool, ALT_EYEDROPPER_TOOLS as _altEyedropperTools } from './editor/tools/eyedropper.js';
import { createBucketTool } from './editor/tools/bucket.js';
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
import { parseCubeLut as _parseCubeLut, generateCubeLut as _generateCubeLut } from './editor/fx/cube-lut.js';
import { createHistoryPanel } from './editor/history-panel.js';
import { createTransformSession } from './editor/tools/transform-session.js';
import { wireCanvasEvents } from './editor/canvas-events.js';
import { buildRightPanel } from './editor/build/right-panel.js';
import * as panelVisibility from './editor/panel-visibility.js';
import { createColorPanels } from './editor/panels/panels.js';
import { createStudioPanels } from './editor/panels/studio-panels.js';
import { createActionsPanel, ACTION_COMMANDS } from './editor/actions-panel.js';
import { runBatchOverDocuments } from './editor/batch-runner.js';
import { encodeLayeredPsd as _encodeLayeredPsd } from './editor/psd.js';
import { wireSliderUx } from './editor/slider-ux.js';
import { createShortcutsPopover } from './editor/shortcuts-popover.js';
import { wireKeyboardShortcuts } from './editor/keyboard-shortcuts.js';
import { createEditorControl as _createEditorControl } from './editor/editor-control.js';
import { installControlKit as _installControlKit, initControlKit as _initControlKit } from './editor/control-kit.js';
import { createNaturalLanguageEditor as _createNaturalLanguageEditor, colorForName as _colorForName, formatCropRect as _formatCropRect } from './editor/nl-edit.js';
import { createEditorAgent as _createEditorAgent } from './editor/editor-agent.js';
import { alphaBounds as _maskAlphaBounds, featherMaskCanvas as _featherMaskCanvas, bakeMaskIntoLayer as _bakeMaskIntoLayer } from './editor/mask-ops.js';
import { analyzeAutoTone as _analyzeAutoTone, applyAutoTone as _applyAutoTone, boxBlurRGB as _boxBlurRGB, skinMaskFromData as _skinMaskFromData, frequencySeparationSmooth as _frequencySeparationSmooth, blemishRemoval as _blemishRemoval, teethWhitening as _teethWhitening, featureExclusionMask as _featureExclusionMask, dilateAlpha as _dilateAlpha, subtractMask as _subtractMask, guidedClarity as _guidedClarity } from './editor/retouch.js';
import { wireClipboardAndDrop } from './editor/clipboard-and-drop.js';
import { wireAIModelSelectors, resolveInpaintModel, resolveQuickEditModel, resolveQuickEditAgentModel } from './editor/ai-models.js';
import { createPreflight } from './editor/preflight.js';
import { createAiSettings } from './editor/ai-settings.js';
import { lookRecipeSteps } from './editor/look-recipes.js';
import { wireInpaintButtons } from './editor/ai-inpaint.js?v=20260708match1';
import { wireAIToolsMisc } from './editor/ai-tools-misc.js';
import { wireRembgAndSharpen } from './editor/ai-rembg.js';
import { wireStrokeToolSliders } from './editor/stroke-tool-sliders.js';
import { wireImport } from './editor/wire-import.js';
import { wireMergeButtons, mergeLayersByIds as _mergeLayersByIds } from './editor/wire-merge-buttons.js';
import { wireSelectionControls } from './editor/wire-selection-controls.js';
import { wireInpaintControls } from './editor/wire-inpaint-controls.js?v=20260708match1';
import { wireTopbar, closeOtherTopbarMenus as _closeOtherTopbarMenus } from './editor/wire-topbar.js';
import { wireTopbarOverflow } from './editor/wire-topbar-overflow.js';
import { wireTopbarMenus } from './editor/wire-topbar-menus.js';
import { wireExtraMenus } from './editor/wire-extra-menus.js';
import { combineLayers as _combineLayers } from './editor/shape-boolean.js';
import { attachScrollAffordance } from './editor/scroll-affordance.js';
import { applyColorMode as _applyColorMode } from './editor/color-mode.js';
import { createCommandPalette } from './editor/build/command-palette.js';
import { applyWorkspace as _applyWorkspace } from './editor/workspaces.js';
import { createPrecisionGuides } from './editor/precision-guides.js';
import { wireViewMenu } from './editor/wire-view-menu.js';

const API_BASE = window.location.origin;

// Plain-language image-generation failures — shared by the Quick Edit card and
// the low-level AI ops so a generation failure is NEVER silent, never spins
// forever, and never buries the reason in a generic message:
//   offline   - the image server could not be reached (or timed out)
//   disabled  - the image server is up but generation is switched off (HTTP 503)
//   otherwise - the server's own short reason, shown as-is
const _GEN_OFFLINE_MSG = 'Image server is offline right now, so I cannot generate. Try again later.';
const _GEN_DISABLED_MSG = 'Image generation is switched off on the server at the moment.';
const _isGenOfflineMsg = (m) => m === _GEN_OFFLINE_MSG || m === _GEN_DISABLED_MSG || /^Could not generate/.test(String(m || ''));
const _generationErrorMessage = ({ status = 0, detail = '', network = false, timedOut = false } = {}) => {
  const reason = String(detail || '').trim();
  const low = reason.toLowerCase();
  const unreachable = /unreachable|offline|timed out|timeout|connection|connect |refused|no route|network|failed to fetch/.test(low);
  if (network || timedOut || unreachable || status === 0) return _GEN_OFFLINE_MSG;
  if (status === 503) return _GEN_DISABLED_MSG;
  return reason ? `Could not generate: ${reason}` : 'Could not generate an image right now.';
};

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
let _preflight = null;
let _aiSettings = null;
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
    if (e.key === 'Escape') {
      // Floating dialogs/popups that live outside the editor modal must close on
      // Escape reliably (their document-capture handlers can miss when focus is
      // outside them). Close the top-most one and stop here.
      const exportOverlay = document.querySelector('.ge-export-overlay');
      if (exportOverlay) {
        e.preventDefault(); e.stopImmediatePropagation();
        (exportOverlay.querySelector('.ge-export-cancel') || exportOverlay.querySelector('.ge-export-close'))?.click();
        return;
      }
      const adjPopup = document.querySelector('.ge-adj-popup');
      if (adjPopup) {
        e.preventDefault(); e.stopImmediatePropagation();
        adjPopup.querySelector('[data-adj-action="cancel"]')?.click();
        return;
      }
      const filterOverlay = document.querySelector('.ge-filter-overlay');
      if (filterOverlay) {
        e.preventDefault(); e.stopImmediatePropagation();
        filterOverlay.querySelector('[data-action="cancel"]')?.click();
        return;
      }
    }
    if (state.editorOpen && state.tool === 'pen' && !e.target?.closest?.('input, textarea, select, [contenteditable="true"]') && _penTool.key(e)) return;
    if (state.editorOpen && state.tool === 'path' && e.key === 'Enter' && !e.target?.closest?.('input, textarea, select, [contenteditable="true"]')) {
      e.preventDefault();
      if (_pathTool.hasDraft()) _pathTool.finish();
      return;
    }
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
    if (state.tool === 'path' && _pathTool.hasDraft()) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      _pathTool.cancel();
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

/** The model the Quick Edit box will use (explicit picker, else inpaint/Auto). */
function _getSelectedQuickEditModel() {
  try { return resolveQuickEditModel(); } catch (_) { return { endpoint: '', model: '', label: '' }; }
}

/** The AGENT (planner) model the Quick Edit box will use. */
function _getSelectedQuickEditAgentModel() {
  try { return resolveQuickEditAgentModel(); } catch (_) { return { agent: '', endpoint: '', label: '' }; }
}

/**
 * Quick Edit routing mode (from the box's mode selector):
 *   'auto' – deterministic local ops first, else send to the image model
 *   'gen'  – generate a brand-new image (text-to-image) on a new layer
 *   'edit' – always the image edit model, skipping the local fast path
 * Older builds stored 'quick' / 'agent' / 'image'; those map onto the
 * three-way selector so an existing preference keeps working.
 */
function _getQuickEditMode() {
  const sel = document.getElementById('ge-ai-command-mode');
  const v = sel && sel.value;
  if (v === 'image') return 'gen';
  if (v === 'quick') return 'auto';
  if (v === 'agent') return 'edit';
  return (v === 'gen' || v === 'edit') ? v : 'auto';
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

let _quickEditActiveJob = null;
// The in-flight image-model request (if any) so Cancel can abort the
// network call itself, not just drop the row from the job list.
let _aiEditAbort = null;

// Screen-space rect (relative to .ge-canvas-area) of the region the busy glow
// covers while an AI edit runs. We cover the WHOLE canvas area edge-to-edge
// (not just the image, which is letterboxed inside the workspace) so the
// skeleton always reaches the sides instead of reading as a small square.
function _aiBusyTargetRect() {
  const area = state.container?.querySelector('.ge-canvas-area');
  if (!area) return null;
  return {
    left: 0,
    top: 0,
    width: Math.max(1, area.clientWidth),
    height: Math.max(1, area.clientHeight),
  };
}

// ASCII fireflies overlay shown over the edited region while an AI edit runs.
// Mirrors the "yoyo"/terminal theme's ascii-fireflies background: a firefly
// field is simulated on an offscreen canvas, then every 2x2 block is mapped to
// a glyph of a monospace ramp and painted as text — scoped to the busy rect so
// it reads as "the editor is thinking", in ASCII.
let _aiFireflies = null;

function _aiFirefliesStart() {
  const area = state.container?.querySelector('.ge-canvas-area');
  const r = _aiBusyTargetRect();
  if (!area || !r) return;
  // Remove any stray overlays (incl. legacy vector canvases) so no orphan
  // skeleton is left behind in a corner.
  area.querySelectorAll('.ge-ai-ascii-fireflies, .ge-ai-fireflies').forEach((node) => {
    if (node !== _aiFireflies?.el) { try { node.remove(); } catch (_) {} }
  });
  let el = area.querySelector('.ge-ai-ascii-fireflies');
  if (!el) {
    el = document.createElement('pre');
    el.className = 'ge-ai-ascii-fireflies';
    el.setAttribute('aria-hidden', 'true');
    area.appendChild(el);
  }
  const w = Math.max(1, Math.round(r.width));
  const h = Math.max(1, Math.round(r.height));
  el.style.left = Math.round(r.left) + 'px';
  el.style.top = Math.round(r.top) + 'px';
  el.style.width = w + 'px';
  el.style.height = h + 'px';
  const fontSize = h < 150 ? 8 : 11;
  el.style.fontSize = fontSize + 'px';
  const source = document.createElement('canvas');
  const sctx = source.getContext('2d', { willReadFrequently: true });
  const ramp = ' .,:;irsXA253hMHGS#9B&@';
  sctx.font = `${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  const charWidth = Math.max(4, sctx.measureText('M').width || 7);
  // Keep the raw (fractional) advance — rounding it up under-counts columns and
  // leaves a ~5% gap on the right. Add a spare column/row beyond the exact fit
  // too, because the browser rounds the monospace advance per line; the
  // overflow is clipped by the pre's `overflow: hidden`.
  const columns = Math.max(8, Math.ceil(w / charWidth) + 1);
  const rows = Math.max(4, Math.ceil(h / fontSize) + 1);
  source.width = columns * 2;
  source.height = rows * 2;
  if (_aiFireflies && _aiFireflies.raf) { cancelAnimationFrame(_aiFireflies.raf); }
  const st = { el, sctx, source, raf: 0, columns, rows, ramp, t: 0, sec: 0, last: 0, lastSample: 0, lum: null };
  _aiFireflies = st;
  // Sample the AFFECTED region of the live canvas into the small offscreen
  // buffer so the skeleton is the actual picture turned into ASCII.
  const sample = () => {
    const main = state.mainCanvas;
    if (!main || !main.width || !main.height) return false;
    sctx.clearRect(0, 0, source.width, source.height);
    // The skeleton covers the whole canvas area now, so stretch the entire
    // photo across the offscreen buffer — every glyph gets fed and there are no
    // bare strips at the sides/bottom where the image didn't reach.
    try { sctx.drawImage(main, 0, 0, main.width, main.height, 0, 0, source.width, source.height); }
    catch (_) { return false; }
    const W = source.width;
    const pixels = sctx.getImageData(0, 0, W, source.height).data;
    const lum = new Float32Array(columns * rows);
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < columns; x++) {
        let s = 0;
        for (let sy2 = 0; sy2 < 2; sy2++) for (let sx2 = 0; sx2 < 2; sx2++) s += pixels[((y * 2 + sy2) * W + x * 2 + sx2) * 4];
        lum[y * columns + x] = s / 4;
      }
    }
    st.lum = lum;
    return true;
  };
  // Map the sampled luminance to glyphs with a flowing diagonal wave so the
  // skeleton shimmers and drifts over the affected area (a "flowy skeleton
  // load"), rather than a frozen snapshot.
  const render = (t) => {
    const lum = st.lum;
    if (!lum) return;
    const lines = [];
    for (let y = 0; y < rows; y++) {
      let line = '';
      for (let x = 0; x < columns; x++) {
        const base = lum[y * columns + x] / 255;
        const wave = 0.5 + 0.5 * Math.sin(x * 0.42 + y * 0.6 - t * 2.6);
        // Normalise both terms to 0..1 before mixing. A raw additive blend of
        // a 0..255 base plus the wave saturates bright regions to the densest
        // glyph, so the wave vanishes there ("full" corner); mixing keeps the
        // full glyph swing in both dark and bright areas.
        const v = (base * 0.5 + wave * 0.5) * 255;
        line += ramp[Math.min(ramp.length - 1, Math.max(0, Math.floor(v / 256 * ramp.length)))];
      }
      lines.push(line);
    }
    st.el.textContent = lines.join('\n');
  };
  const step = (now) => {
    const host = state.container?.querySelector('.ge-canvas-area');
    if (!host || !host.classList.contains('ge-canvas-ai-busy') || st.el !== _aiFireflies?.el) { st.raf = 0; return; }
    st.raf = requestAnimationFrame(step);
    st.sec = now / 1000;
    if (now - st.lastSample > 420) { st.lastSample = now; sample(); }
    if (now - st.last < 80) return;
    st.last = now;
    render(st.sec);
  };
  st.lastSample = performance.now() - 1000;
  sample();
  render(0);
  st.raf = requestAnimationFrame(step);
}

function _aiFirefliesStop() {
  const st = _aiFireflies;
  if (st) {
    if (st.raf) { cancelAnimationFrame(st.raf); st.raf = 0; }
    try { st.el.remove(); } catch (_) { /* already gone */ }
  }
  // Sweep any orphaned overlays (e.g. from a previous editor session).
  try {
    state.container?.querySelectorAll('.ge-ai-ascii-fireflies, .ge-ai-fireflies')
      .forEach((node) => { try { node.remove(); } catch (_) {} });
  } catch (_) { /* noop */ }
  _aiFireflies = null;
}

function _setCanvasAiBusy(busy) {
  const area = state.container?.querySelector('.ge-canvas-area');
  if (!area) { if (!busy) _aiFirefliesStop(); return; }
  area.classList.toggle('ge-canvas-ai-busy', !!busy);
  if (!busy) { _aiFirefliesStop(); return; }
  const r = _aiBusyTargetRect();
  if (!r) return;
  area.style.setProperty('--ge-ai-busy-x', `${Math.round(r.left)}px`);
  area.style.setProperty('--ge-ai-busy-y', `${Math.round(r.top)}px`);
  area.style.setProperty('--ge-ai-busy-w', `${Math.round(r.width)}px`);
  area.style.setProperty('--ge-ai-busy-h', `${Math.round(r.height)}px`);
  _aiFirefliesStart();
}

let _aiProgressTimer = null;
function _setAiProgress(frac, label) {
  const wrap = document.getElementById('ge-ai-command-progress');
  if (!wrap) return;
  if (frac == null) {
    wrap.hidden = true;
    const lbl0 = document.getElementById('ge-ai-command-progress-label');
    if (lbl0) lbl0.textContent = '';
    return;
  }
  wrap.hidden = false;
  const fill = document.getElementById('ge-ai-command-progress-fill');
  if (fill) fill.style.width = `${Math.max(0, Math.min(1, frac)) * 100}%`;
  const lbl = document.getElementById('ge-ai-command-progress-label');
  if (lbl) lbl.textContent = label || '';
}
function _startAiProgress(requestId) {
  const endpointId = arguments[1] || '';
  _stopAiProgress();
  if (!requestId) return;
  const tick = async () => {
    try {
      const epQuery = endpointId ? `&endpoint_id=${encodeURIComponent(endpointId)}` : '';
      const r = await fetch(`/api/image/edit-progress?request_id=${encodeURIComponent(requestId)}` + epQuery, { credentials: 'same-origin' });
      if (!r.ok) return;
      const p = await r.json();
      const st = p.stage || '';
      if (st === 'done' || st === 'error') { _setAiProgress(1, st === 'done' ? 'Done' : 'Failed'); return; }
      const frac = typeof p.progress === 'number' ? p.progress : 0;
      const stepTxt = p.steps ? ` ${p.step || 0}/${p.steps}` : '';
      const eta = (typeof p.eta_s === 'number' && p.eta_s > 0) ? ` · ETA ${p.eta_s}s` : '';
      const verb = st === 'editing' ? 'Generating' : (st ? st[0].toUpperCase() + st.slice(1) : 'Working');
      _setAiProgress(frac, `${verb}…${stepTxt}${eta}`);
      _setAiCommandStatus(`${verb}…${stepTxt}${eta}`, 'running');
    } catch (_) {}
  };
  _aiProgressTimer = setInterval(tick, 1000);
  tick();
}
function _stopAiProgress() {
  if (_aiProgressTimer) { clearInterval(_aiProgressTimer); _aiProgressTimer = null; }
  _setAiProgress(null);
}

function _setAiCommandStatus(text, kind = '') {
  const el = document.getElementById('ge-ai-command-status');
  if (el) {
    el.textContent = text || '';
    el.dataset.kind = kind || '';
  }
  // While AI work is in flight, glow the image canvas so it's obvious the
  // editor is busy even when the status text is off-screen.
  _setCanvasAiBusy(kind === 'running');
  // While a stacked prompt is executing, mirror its progress into the job row
  // so the queue view shows queued / running / done / error for every prompt.
  const job = _quickEditActiveJob;
  if (job) {
    if (text) job.detail = text;
    if (kind === 'done') job.status = 'done';
    else if (kind === 'error') job.status = 'error';
    else if (kind === 'running' && job.status !== 'done' && job.status !== 'error') job.status = 'running';
    if (typeof job.onChange === 'function') job.onChange();
  }
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
  const btn = state.container?.querySelector(`.ge-tool-btn[data-tool="${toolId}"]`)
    || state.container?.querySelector(`[data-tool-option="${toolId}"]`);
  if (btn) btn.click();
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

// Quick Edit "Harmonize" action: hand the image-edit model a purpose-built
// prompt that re-blends every composited layer into one coherent image. Kept
// in one place so the suggestion, the router and the tests agree.
const _HARMONIZE_PROMPT = 'Blend all the composited elements into one coherent, photorealistic image. '
  + 'Unify the global lighting direction, colour temperature and white balance across the whole frame; '
  + 'add matching shadows, contact shadows and reflections beneath each subject; unify perspective, scale and depth of field; '
  + 'clean up seams, halos and hard cutout edges between pasted layers. '
  + 'Keep every subject, its position and the overall composition unchanged.';

// Detect an explicit "harmonize / blend the layers" instruction so it routes to
// the image model with the crafted prompt above, in every routing mode.
const _isHarmonizeIntent = (text) => {
  const t = String(text || '').toLowerCase();
  if (/\bharmoni[sz]e\b/.test(t)) return true;
  if (/\bseamless\s+blend\b/.test(t)) return true;
  if (/\bunify\s+(?:the\s+)?layers?\b/.test(t)) return true;
  if (/\bmake\s+it\s+(?:all\s+)?(?:cohesive|blend|seamless)\b/.test(t)) return true;
  return /\bblend\b[^.]*\b(?:layers?|elements?|everything|all|together|into one|cohesive)\b/.test(t);
};

// Recent Quick Edit prompts (most-recent-first, de-duplicated, capped). Photoshop
// remembers your recent generative prompts; the Quick Edit box surfaces these as
// suggestions the moment it is opened with an empty field, and ArrowUp/Down picks
// one. Persisted per browser.
const _AI_RECENT_KEY = 'ge-ai-command-recent';
const _AI_RECENT_MAX = 8;
const _getRecentPrompts = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(_AI_RECENT_KEY) || '[]');
    if (!Array.isArray(raw)) return [];
    return raw.filter((v) => typeof v === 'string' && v.trim()).slice(0, _AI_RECENT_MAX);
  } catch (_) { return []; }
};
const _pushRecentPrompt = (prompt) => {
  const p = String(prompt || '').trim();
  if (!p) return;
  try {
    const list = _getRecentPrompts().filter((v) => v.toLowerCase() !== p.toLowerCase());
    list.unshift(p);
    localStorage.setItem(_AI_RECENT_KEY, JSON.stringify(list.slice(0, _AI_RECENT_MAX)));
  } catch (_) {}
};

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
      <select class="ge-ai-command-mode ge-ctl-select" id="ge-ai-command-mode" title="How your prompt is handled" aria-label="Quick Edit routing">
        <option value="auto">Auto</option>
        <option value="gen">Img Gen</option>
        <option value="edit">Img Edit</option>
      </select>
      <select class="ge-ai-command-scope ge-ctl-select" id="ge-ai-command-scope" title="What the edit model sees" aria-label="Edit input">
        <option value="all">All layers</option>
        <option value="layer">This layer</option>
      </select>
      <button type="button" class="ge-ai-command-close" id="ge-ai-command-close" title="Collapse Quick Edit" aria-label="Collapse Quick Edit">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>
    </div>
    <form class="ge-ai-command-form" id="ge-ai-command-form">
      <input type="text" class="ge-ai-command-input" id="ge-ai-command-input" autocomplete="off" aria-label="Describe the image edit" placeholder="Describe an edit, or choose an action" title="Tip: press Tab to switch routing mode" />
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
    <div class="ge-ai-command-progress" id="ge-ai-command-progress" hidden>
      <div class="ge-ai-command-progress-fill" id="ge-ai-command-progress-fill"></div>
      <span class="ge-ai-command-progress-label" id="ge-ai-command-progress-label"></span>
    </div>
    <div class="ge-ai-command-status" id="ge-ai-command-status" aria-live="polite"></div>
    <div class="ge-ai-command-stack" id="ge-ai-command-stack" aria-label="Edit queue" hidden></div>
  `;
  return wrap;
}

const _AI_COMMAND_SUGGESTIONS = [
  { label: 'Undo', insert: 'undo', hint: 'Revert the last change', kind: 'Local', aliases: ['undo', 'revert', 'go back', 'step back'] },
  { label: 'Redo', insert: 'redo', hint: 'Re-apply the last undone change', kind: 'Local', aliases: ['redo'] },
  { label: 'Rotate 90', insert: 'rotate 90', hint: 'Turn the image clockwise', kind: 'Local', aliases: ['ro', 'rotate', 'right', 'clockwise', 'turn', 'flip 90'] },
  { label: 'Rotate left', insert: 'rotate left', hint: 'Turn the image counter-clockwise', kind: 'Local', aliases: ['rotate left', 'left', 'counter clockwise', 'ccw'] },
  { label: 'Rotate 180', insert: 'rotate 180', hint: 'Flip the canvas upside down', kind: 'Local', aliases: ['rotate 180', 'upside down', 'flip 180'] },
  { label: 'Flip horizontal', insert: 'flip horizontal', hint: 'Mirror left to right', kind: 'Local', aliases: ['flip', 'mirror', 'horizontal'] },
  { label: 'Flip vertical', insert: 'flip vertical', hint: 'Mirror top to bottom', kind: 'Local', aliases: ['flip vertical', 'vertical'] },
  { label: 'New layer', insert: 'new layer', hint: 'Add an empty layer', kind: 'Local', aliases: ['new layer', 'add layer', 'create layer', 'blank layer'] },
  { label: 'Duplicate layer', insert: 'duplicate layer', hint: 'Copy the active layer', kind: 'Local', aliases: ['duplicate layer', 'copy layer', 'clone layer'] },
  { label: 'Delete layer', insert: 'delete layer', hint: 'Remove the selected layer', kind: 'Local', aliases: ['delete layer', 'remove layer', 'trash layer'] },
  { label: 'Hide layer', insert: 'hide layer', hint: 'Toggle layer visibility', kind: 'Local', aliases: ['hide layer', 'show layer', 'toggle layer'] },
  { label: 'Merge down', insert: 'merge down', hint: 'Merge into the layer below', kind: 'Local', aliases: ['merge down', 'merge layer', 'flatten', 'merge all'] },
  { label: 'Fill canvas', insert: 'fill the canvas with ', hint: 'Fill the whole canvas with a colour', kind: 'Local', aliases: ['fill canvas', 'fill layer', 'fill everything', 'fill with', 'bucket', 'fill'] },
  { label: 'AI fill', insert: 'ai fill', hint: 'Fill the empty areas with generated content', kind: 'AI', aliases: ['ai fill', 'fill empty', 'fill the gaps', 'complete the canvas', 'outpaint'] },
  { label: 'Harmonize', insert: 'harmonize the layers', hint: 'Blend every layer into one coherent image with the AI model', kind: 'AI', aliases: ['harmonize', 'harmonise', 'blend layers', 'blend into one', 'unify layers', 'seamless blend', 'blend everything', 'make it cohesive'] },
  { label: 'Remove background', insert: 'remove background', hint: 'Make the background transparent', kind: 'AI', aliases: ['remove bg', 'background', 'transparent', 'cut out'] },
  { label: 'Upscale', insert: 'upscale 2x', hint: 'Increase image resolution', kind: 'AI', aliases: ['upscale', 'bigger', 'larger', '2x', '4x'] },
  { label: 'Denoise', insert: 'denoise', hint: 'Reduce grain and noise', kind: 'AI', aliases: ['denoise', 'noise', 'grain', 'clean up'] },
  { label: 'Sharpen', insert: 'sharpen', hint: 'Make details crisper', kind: 'AI', aliases: ['sharpen', 'sharp', 'clearer', 'crisp', 'enhance'] },
  { label: 'Enhance face', insert: 'enhance face', hint: 'Restore portrait and skin detail', kind: 'AI', aliases: ['face', 'portrait', 'skin', 'selfie', 'restore'] },
  { label: 'Style edit', insert: 'style: ', hint: 'Run a full-image prompt edit', kind: 'AI', aliases: ['style', 'paint', 'anime', 'photo', 'prompt'] },
];

// Curated starter prompts shown the moment Quick Edit opens with an empty field
// and no history yet — Photoshop's generative box likewise offers example
// prompts so an empty box is never a dead end. Each maps to a real route.
const _AI_EXAMPLE_PROMPTS = [
  { label: 'Remove the background', insert: 'remove background', hint: 'Cut out the subject' },
  { label: 'Make it cinematic', insert: 'make it cinematic', hint: 'Teal-and-orange film grade' },
  { label: 'Replace the sky with a sunset', insert: 'replace the sky with a sunset', hint: 'Generative sky swap' },
  { label: 'Warm golden-hour light', insert: 'warm golden-hour light', hint: 'Sunlit amber tone' },
  { label: 'Enhance face', insert: 'enhance face', hint: 'Restore portrait detail' },
  { label: 'Turn it black and white', insert: 'turn it black and white', hint: 'Classic monochrome' },
];

// Every toolbar tool is available from Quick Edit too, carrying its SVG icon so
// the command list matches the toolbar. Selecting one switches the active tool.
const _QUICK_EDIT_TOOL_SUGGESTIONS = Object.entries(_TOOLS).map(([id, t]) => ({
  label: t.label || id,
  insert: (t.label || id).toLowerCase(),
  hint: 'Tool',
  kind: 'Tool',
  icon: t.icon || '',
  toolId: id,
  aliases: [id],
}));
const _QUICK_EDIT_CATALOG = [..._QUICK_EDIT_TOOL_SUGGESTIONS, ..._AI_COMMAND_SUGGESTIONS];

// Resolve a prompt like "use the eraser tool" / "switch to marquee" / a bare
// tool name to a toolbar tool id. Only tool-verb phrasing or an exact name
// matches, so ordinary edit prompts never trip it.
function _matchTool(text) {
  const q = String(text || '').toLowerCase().trim();
  if (!q) return null;
  const names = _QUICK_EDIT_TOOL_SUGGESTIONS.map((item) => [item.toolId, item.label.toLowerCase(), item.toolId.toLowerCase()]);
  for (const [id, label, tid] of names) {
    if (q === label || q === tid) return id;
  }
  const m = q.match(/\b(?:use|select|switch\s+to|open|pick|choose|activate|apply)\b\s+(?:the\s+)?(.+?)\s*(?:tool|brush)?\b/);
  if (m) {
    const want = m[1].replace(/\s+tool$/, '').trim();
    for (const [id, label, tid] of names) {
      if (want === label || want === tid) return id;
    }
  }
  return null;
}

function _matchAiCommandSuggestions(query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return [];
  return _QUICK_EDIT_CATALOG
    .map((item) => {
      const hay = [item.label, item.insert, ...(item.aliases || [])].map(v => String(v || '').toLowerCase());
      const starts = hay.some(v => v.startsWith(q));
      const contains = hay.some(v => v.includes(q));
      if (!starts && !contains) return null;
      return { item, score: starts ? 0 : 1 };
    })
    .filter(Boolean)
    .sort((a, b) => a.score - b.score || a.item.label.localeCompare(b.item.label))
    .slice(0, 4)
    .map(hit => hit.item);
}

// On phones the layers panel is a bottom sheet that can cover the editor
// footer (zoom / setup). Float the footer just above the sheet's top edge so
// its controls stay tappable; restore it to normal flow when the sheet is
// collapsed or on desktop.
let _lastFooterDelta = 0;
const _placeMobileFooter = () => {
  const footer = document.querySelector('.ge-editor-footer');
  if (!footer) return;
  const area = document.querySelector('.ge-canvas-area');
  const _clear = () => {
    footer.classList.remove('ge-footer-floating');
    footer.style.position = ''; footer.style.left = ''; footer.style.right = '';
    footer.style.bottom = ''; footer.style.zIndex = '';
    document.body.classList.remove('ge-footer-docked');
    state._footerDocked = false;
    if (area && (area.style.marginBottom || area.style.height || area.style.flex || area.style.minHeight)) {
      area.style.marginBottom = ''; area.style.height = ''; area.style.flex = ''; area.style.minHeight = '';
      _lastFooterDelta = 0;
      // The sheet may have squeezed fit view to a few pixels. Restore fit
      // after releasing that constraint, while retaining an explicit zoom.
      if (state.zoomViewMode === 'fit') {
        try { _fitZoom(); } catch (_) {}
      }
    }
  };
  if (window.innerWidth > 700) { _clear(); return; }
  const sheet = document.querySelector('.ge-right-panel');
  if (!sheet || sheet.classList.contains('minimized') || sheet.classList.contains('expanded')) { _clear(); return; }
  const r = sheet.getBoundingClientRect();
  const vh = window.innerHeight;
  if (r.height <= 0 || r.top >= vh || r.top <= 140) { _clear(); return; }
  footer.classList.add('ge-footer-floating');
  footer.style.position = 'fixed';
  footer.style.left = '0';
  footer.style.right = '0';
  footer.style.zIndex = '60';
  // Dock the footer flush to the sheet top: no seam / dead space between them.
  footer.style.bottom = Math.max(0, Math.round(vh - r.top)) + 'px';
  document.body.classList.add('ge-footer-docked');
  // Dock the canvas image flush too: with the dock padding off, the image
  // fills the region so its bottom meets the footer top (no dead space).
  state._footerDocked = true;
  // Size the canvas region so it ends exactly where the footer begins:
  // canvas bottom == footer top == sheet top. (Its mobile min-height:40vh
  // would otherwise keep it running under the footer and the sheet.) The
  // ge-footer-docked class also hides the sheet grab handle.
  if (area) {
    const footerH = footer.getBoundingClientRect().height || 0;
    const desiredBottom = Math.round(r.top - footerH);
    const want = Math.max(0, Math.round(desiredBottom - area.getBoundingClientRect().top));
    if (want !== _lastFooterDelta) {
      area.style.flex = want ? '0 0 auto' : '';
      area.style.minHeight = want ? '0px' : '';
      area.style.height = want ? want + 'px' : '';
      _lastFooterDelta = want;
      if (state.zoomViewMode === 'fit') {
        try { _fitZoom(); } catch (_) {}
      }
    }
  }
};

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
  // Opt-in "Double-check AI edits": off by default (the VL review added no
  // measurable quality gain for ~15% more latency). Persisted per browser.
  const selfCheckToggle = document.getElementById('ge-ai-command-selfcheck');
  try {
    window.geQuickEditSelfCheck = localStorage.getItem('ge-ai-self-check') === '1';
  } catch (_) { window.geQuickEditSelfCheck = false; }
  if (selfCheckToggle) {
    selfCheckToggle.checked = !!window.geQuickEditSelfCheck;
    selfCheckToggle.addEventListener('change', () => {
      window.geQuickEditSelfCheck = !!selfCheckToggle.checked;
      try { localStorage.setItem('ge-ai-self-check', selfCheckToggle.checked ? '1' : '0'); } catch (_) {}
    });
  }
  // Routing mode: Auto (deterministic → image model), Gen, Edit.
  const modeSelect = document.getElementById('ge-ai-command-mode');
  if (modeSelect) {
    try {
      const saved = localStorage.getItem('ge-ai-command-mode');
      const migrated = saved === 'image' ? 'gen'
        : saved === 'agent' ? 'edit'
          : saved === 'quick' ? 'auto'
            : saved;
      if (migrated === 'auto' || migrated === 'gen' || migrated === 'edit') {
        modeSelect.value = migrated;
        if (migrated !== saved) localStorage.setItem('ge-ai-command-mode', migrated);
      }
    } catch (_) {}
    modeSelect.addEventListener('change', () => {
      try { localStorage.setItem('ge-ai-command-mode', modeSelect.value); } catch (_) {}
      syncEditScope();
    });
  }
  // Edit scope: send the flattened document (all visible layers) or only the
  // selected layer to the image edit model. Gen always uses the flattened
  // canvas, so the scope control is hidden in that mode.
  const scopeSelect = document.getElementById('ge-ai-command-scope');
  const syncEditScope = () => {
    if (!scopeSelect) return;
    if (modeSelect) scopeSelect.hidden = modeSelect.value === 'gen';
  };
  if (scopeSelect) {
    try {
      const savedScope = localStorage.getItem('ge-ai-command-edit-scope');
      if (savedScope === 'layer' || savedScope === 'all') scopeSelect.value = savedScope;
    } catch (_) {}
    scopeSelect.addEventListener('change', () => {
      try { localStorage.setItem('ge-ai-command-edit-scope', scopeSelect.value); } catch (_) {}
    });
    syncEditScope();
  }
  let suggestionItems = [];
  let suggestionIndex = 0;
  const syncClearButton = () => {
    if (clearBtn) clearBtn.hidden = !input.value;
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
    if (!input.value.trim()) {
      const recents = _getRecentPrompts();
      suggestionItems = recents.length
        ? recents.map((p) => ({ label: p, insert: p, hint: 'Recent prompt', kind: 'Recent' }))
        : _AI_EXAMPLE_PROMPTS.map((e) => ({ label: e.label, insert: e.insert, hint: e.hint, kind: 'Example' }));
    } else {
      suggestionItems = _matchAiCommandSuggestions(input.value);
    }
    suggestionIndex = Math.min(suggestionIndex, Math.max(0, suggestionItems.length - 1));
    if (!suggestionItems.length) {
      hideSuggestions();
      return;
    }
    suggestions.hidden = false;
    suggestions.innerHTML = suggestionItems.map((item, idx) => `
      <button type="button" class="ge-ai-command-suggestion${idx === suggestionIndex ? ' active' : ''}" data-ai-command-suggestion="${idx}">
        <span class="ge-ai-command-suggestion-icon" aria-hidden="true">${item.icon || ''}</span>
        <span class="ge-ai-command-suggestion-kind">${_escapeAiCommandText(item.kind || 'Edit')}</span>
        <span class="ge-ai-command-suggestion-main">${_escapeAiCommandText(item.label)}</span>
        <span class="ge-ai-command-suggestion-hint">${_escapeAiCommandText(item.hint || item.insert)}</span>
      </button>
    `).join('');
  };
  const pickSuggestion = (idx, run = false) => {
    const item = suggestionItems[idx];
    if (!item) return false;
    if (item.toolId) {
      _clickToolButton(item.toolId);
      _setAiCommandStatus(`${item.label} tool`, 'done');
      hideSuggestions();
      input.focus();
      return true;
    }
    input.value = item.insert;
    hideSuggestions();
    input.focus();
    if (run) form.requestSubmit();
    return true;
  };
  wrap.addEventListener('pointerdown', (e) => e.stopPropagation());
  wrap.addEventListener('click', (e) => e.stopPropagation());
  // Clicking anywhere outside the Quick Edit box dismisses the suggestions
  // popup. Use capture so it still fires when another handler stops
  // propagation first; clicks inside `wrap` (input, clear/run, the popup
  // itself) are skipped so picking a suggestion is never lost.
  document.addEventListener('pointerdown', (e) => {
    if (!suggestions || suggestions.hidden) return;
    if (!wrap.contains(e.target)) hideSuggestions();
  }, true);
  suggestions?.addEventListener('pointerdown', (e) => e.preventDefault());
  suggestions?.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-ai-command-suggestion]');
    if (!btn) return;
    pickSuggestion(Number(btn.dataset.aiCommandSuggestion), false);
  });
  const setOpen = (open) => {
    wrap.classList.toggle('ge-ai-command-collapsed', !open);
    toggle?.setAttribute('aria-expanded', open ? 'true' : 'false');
    document.body.classList.toggle('ge-qe-open', !!open);
    if (open) {
      // On phones the layers sheet and the Quick Edit bar both live at the
      // bottom; if the sheet is expanded there is no room for the bar (it would
      // have to cover either the tool rail or the sheet), so drop the sheet back
      // to its peek height and let the bar sit above it.
      if (window.innerWidth <= 700) {
        const sheet = document.querySelector('.ge-right-panel');
        if (sheet) { sheet.classList.remove('expanded'); sheet.classList.remove('minimized'); }
      }
      requestAnimationFrame(() => {
        input.focus();
        renderSuggestions();
      });
    }
    else hideSuggestions();
  };
  wrap.addEventListener('ge-ai-command-close', () => setOpen(false));
  wrap.addEventListener('ge-ai-command-open', () => setOpen(true));
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
    if (e.key === 'Tab' && !e.shiftKey) {
      // Tab cycles the routing mode (Auto → Gen → Edit) so you can
      // re-route the prompt without submitting it. It no longer sends.
      const ms = document.getElementById('ge-ai-command-mode');
      if (!ms) return;
      e.preventDefault();
      hideSuggestions();
      const opts = Array.from(ms.options).map((o) => o.value);
      const cur = Math.max(0, opts.indexOf(ms.value));
      ms.value = opts[(cur + 1) % opts.length];
      try { localStorage.setItem('ge-ai-command-mode', ms.value); } catch (_) {}
      ms.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    if (open && e.key === 'Escape') {
      e.preventDefault();
      hideSuggestions();
    }
  });
  // Window/keyboard aware: keep the docked Quick Edit bar sitting just above
  // the on-screen keyboard (chat-box style) by measuring how far its host
  // extends past the *visible* (visualViewport) area and lifting the bar by
  // that overlap. While the prompt is focused we also poll for a moment, since
  // iOS fires visualViewport resize/scroll late as the keyboard slides up.
  const _liftQuickEdit = () => {
    if (!wrap.isConnected) { _stopLift(); return; }
    const vv = window.visualViewport;
    const visibleBottom = vv ? (vv.offsetTop + vv.height) : window.innerHeight;
    const host = wrap.parentElement || wrap;
    const hostBottom = host.getBoundingClientRect().bottom || window.innerHeight;
    let lift = Math.max(0, Math.round(Math.min(hostBottom, window.innerHeight) - visibleBottom));
    // On phones the layers panel is a bottom sheet that can sit over the bar;
    // keep the bar clear of the sheet's top edge so its input stays tappable.
    if (window.innerWidth <= 700) {
      const hostTop = Math.min(hostBottom, window.innerHeight);
      const sheet = document.querySelector('.ge-right-panel');
      if (sheet) {
        const r = sheet.getBoundingClientRect();
        if (r.height > 0) lift = Math.max(lift, Math.round(hostTop - r.top - 8));
      }
      // …but never lift so far that the bar rides up over the tool rail.
      const rail = document.querySelector('.ge-toolbar');
      if (rail && !wrap.classList.contains('ge-ai-command-collapsed')) {
        const barH = wrap.offsetHeight || 0;
        const maxLift = Math.round(hostTop - 14 - barH - rail.getBoundingClientRect().bottom - 8);
        lift = Math.min(lift, Math.max(0, maxLift));
      }
    }
    wrap.style.setProperty('--ge-ai-lift', lift + 'px');
    wrap.classList.toggle('ge-ai-command-lifted', lift > 0);
  };
  let _liftPoller = null;
  const _stopLift = () => {
    if (_liftPoller) { clearInterval(_liftPoller); _liftPoller = null; }
    window.visualViewport?.removeEventListener('resize', _liftQuickEdit);
    window.visualViewport?.removeEventListener('scroll', _liftQuickEdit);
    window.removeEventListener('resize', _liftQuickEdit);
  };
  const _trackLift = () => {
    _liftQuickEdit();
    if (_liftPoller) clearInterval(_liftPoller);
    let n = 0;
    _liftPoller = setInterval(() => { _liftQuickEdit(); if (++n >= 30) { clearInterval(_liftPoller); _liftPoller = null; } }, 100);
  };
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', _trackLift);
    window.visualViewport.addEventListener('scroll', _liftQuickEdit);
  }
  window.addEventListener('resize', _liftQuickEdit);
  document.addEventListener('focusin', (e) => { if (wrap.contains(e.target)) _trackLift(); });
  input.addEventListener('focus', _trackLift);
  input.addEventListener('click', _trackLift);
  wrap.addEventListener('focusin', _trackLift);
  requestAnimationFrame(() => requestAnimationFrame(_liftQuickEdit));
  // The layers sheet expands/collapses (and its peek height grows as layers
  // render) independently of the keyboard; watch it so the bar and the footer
  // keep clear of the sheet's top edge as it moves.
  const _syncMobileChrome = () => { _liftQuickEdit(); _placeMobileFooter(); };
  // The sheet animates (transform .22s), so a single sample lands mid-flight;
  // re-sample for a moment after it changes.
  const _settleMobileChrome = () => {
    let n = 0;
    const t = setInterval(() => { _syncMobileChrome(); if (++n >= 14) clearInterval(t); }, 60);
  };
  const _watchSheetForLift = () => {
    const sheet = document.querySelector('.ge-right-panel');
    if (!sheet || sheet.__geQeLiftWatched) return;
    sheet.__geQeLiftWatched = true;
    try { new MutationObserver(_settleMobileChrome).observe(sheet, { attributes: true, attributeFilter: ['class', 'style'] }); } catch (_) {}
    if (typeof ResizeObserver !== 'undefined') {
      try {
        const ro = new ResizeObserver(_settleMobileChrome);
        ro.observe(sheet);
        const layers = sheet.querySelector('.ge-layers');
        if (layers) ro.observe(layers);
      } catch (_) {}
    }
  };
  _watchSheetForLift();
  window.addEventListener('resize', _syncMobileChrome);
  requestAnimationFrame(() => { _watchSheetForLift(); _syncMobileChrome(); });
  let _chromeSettle = 0;
  const _chromeSettleId = setInterval(() => { _watchSheetForLift(); _syncMobileChrome(); if (++_chromeSettle >= 12) clearInterval(_chromeSettleId); }, 150);
  // Client-side fallback planner. Quick Edit normally delegates the routing
  // decision to the server planner (`window.geEditorAgent`, HARNESS-owned) —
  // that planner decides deterministic-ops-first and always sends generative
  // intents (remove/replace/add, background/scene change, extend, generate) to
  // the AI model. This local path only runs when the server planner is
  // unreachable, so it must never *fake* a generative request with a local
  // adjustment; a generative prompt that lands here falls through to the AI
  // model (window.geEditorAI) just like the server would route it.
  const _executeQuickEditLocal = async (prompt) => {
    const p = prompt.toLowerCase();
    // A targeted recolour ("make only the red car blue", "recolour the rock to
    // teal") needs the model to repaint a specific object — never fake it with a
    // global colour tweak from the deterministic fast path.
    if (_looksRecolor(p)) return false;
    // ── Deterministic document / effect / grade commands ──
    // These MUST never reach the image model: a local control op expresses them
    // exactly. Keep this BEFORE the creative-repaint branch so a plain tone word
    // ("cinematic", "moody") grades rather than repaints.
    // Resize: "resize to 1920 wide", "resize to 1080 tall", "resize to 1200x800".
    const resizeM = p.match(/\bresize\b[^.]*?\b(\d{2,5})\s*(?:px|pixels?)?\s*(wide|width|tall|height|high)?\b/)
      || p.match(/\bresize\b[^.]*?\b(\d{2,5})\s*[x×]\s*(\d{2,5})\b/);
    if (/\bresize\b/.test(p) && resizeM) {
      const n = Number(resizeM[1]);
      const second = Number(resizeM[2]);
      const ar = state.imgWidth / state.imgHeight;
      let w, h;
      if (Number.isFinite(second) && /^\d{2,5}$/.test(String(resizeM[2]))) { w = n; h = second; }
      else if (/tall|height|high/i.test(String(resizeM[2] || ''))) { h = n; w = Math.round(h * ar); }
      else { w = n; h = Math.round(w / ar); }
      await _runQuickControlOp('document.resizeCanvas', { width: w, height: h });
      _setAiCommandStatus(`Resized to ${w}×${h}.`, 'done');
      return;
    }
    // Export: "save as PSD", "export to photoshop".
    if (/\bpsd\b|\bphotoshop\s+document\b/.test(p) || (/\b(?:save|export)\b/.test(p) && /\b(?:psd|photoshop|layered)\b/.test(p))) {
      await _runQuickControlOp('document.savePsd', {});
      _setAiCommandStatus('Exported a layered PSD.', 'done');
      return;
    }
    // Photoshop one-click Image ▸ Adjustments commands. These are exact,
    // deterministic edits, so they must never reach the image model:
    //   Desaturate (Cmd/Ctrl+Shift+U) → Hue/Saturation with the saturation
    //   multiplier at 0 (the internal scale is 1 = neutral, 0 = grayscale —
    //   see fx/pixel-pass.js). Guard the phrase so "remove the colour cast"
    //   (a white-balance fix) is not mistaken for a desaturate.
    if (/\bde-?satur\w*/.test(p)
      || /\b(?:remove|strip|drop|take\s+out)\s+(?:all\s+|the\s+)?colou?rs?\b(?!\s*cast)/.test(p)) {
      await _runQuickControlOp('adjustments.apply', { type: 'hue-saturation', params: { saturation: 0 } });
      _setAiCommandStatus('Desaturated the photo.', 'done');
      return;
    }
    // Auto Tone / Auto Levels / Auto Color → the Camera-Raw-style autoTone op
    // (per-channel levels + auto white balance + exposure/contrast).
    if (/\bauto[-\s]?tone\b|\bauto[-\s]?levels?\b|\bauto[-\s]?colou?rs?\b/.test(p)) {
      await _runQuickControlOp('photo.autoTone', {});
      _setAiCommandStatus('Auto-toned the photo.', 'done');
      return;
    }
    // Auto Contrast → the Levels auto-contrast endpoint stretch (in 12 / 243).
    if (/\bauto[-\s]?contrast\b/.test(p)) {
      await _runQuickControlOp('adjustments.apply', { type: 'levels', params: { inBlack: 12, inWhite: 243 } });
      _setAiCommandStatus('Added auto contrast.', 'done');
      return;
    }
    // Motion / zoom blur → the real Filter-menu blur variants (deterministic).
    // Placed before the generic blur branch so a specific kind wins over the
    // default Gaussian. The ids resolve via the filters.apply alias map.
    if (/\b(?:motion|directional|panning)\s*blur\b/.test(p)) {
      await _runQuickControlOp('filters.apply', { id: 'motion-blur' });
      _setAiCommandStatus('Applied a motion blur.', 'done');
      return;
    }
    if (/\b(?:zoom|radial)\s*blur\b/.test(p)) {
      await _runQuickControlOp('filters.apply', { id: 'zoom-blur' });
      _setAiCommandStatus('Applied a zoom blur.', 'done');
      return;
    }
    // Blur / bokeh / depth-of-field → a real Gaussian blur (masked to the
    // background when the prompt says so). This must run before the generative
    // gate: "blur the background" mentions the background but is NOT a scene
    // replacement, so it must not be routed to ai.replaceBackground.
    if (/\bblur\b|\bbokeh\b|\bdepth\s*of\s*field\b|\bdof\b/.test(p)) {
      const _strong = /\b(?:strong|heavy|lots|very|much|blurrier)\b/.test(p);
      const _soft = /\b(?:subtle|slight|light|soft|gentle|little)\b/.test(p);
      const radius = _strong ? 26 : _soft ? 8 : 16;
      const _bg = /\b(?:background|backdrop|\bbg\b|behind)\b/.test(p);
      if (_bg) {
        await _runQuickControlOp('selection.background', {});
        await _runQuickControlOp('filters.applyMasked', { id: 'blur-gaussian', radius, invert: true });
        _setAiCommandStatus('Blurred the background.', 'done');
      } else {
        await _runQuickControlOp('filters.applyMasked', { id: 'blur-gaussian', radius, invert: true, full: true });
        _setAiCommandStatus('Blurred the image.', 'done');
      }
      return;
    }
    // Vignette / darken-edges.
    if (/\bvignette\b|\bdarken\s+(?:the\s+)?edges?\b/.test(p)) {
      await _runQuickControlOp('filters.apply', { id: 'vignette' });
      _setAiCommandStatus('Added a vignette.', 'done');
      return;
    }
    // --- Filter menu: specialty filters (deterministic, no model call) ---
    // Real Photoshop Filter-menu effects that are unambiguous and do not
    // overlap the blur / sharpen / vignette branches above. A null matcher
    // uses the shared _specNoise test (an "add noise / film grain" phrase).
    {
      const _specNoise = /\b(?:add|apply|give|more)\b[^.]*\b(?:film\s+)?(?:noise|grain)\b/.test(p)
        || /\bfilm\s+grain\b/.test(p);
      const SPECIALTY_FILTERS = [
        [/\bunsharp(?:\s+mask)?\b/, 'unsharp-mask', 'Unsharp Mask'],
        [/\bpixelate\b|\bmosaic\b/, 'pixelate-mosaic', 'Pixelate'],
        [/\bcrystalli[sz]e\b/, 'pixelate-crystallize', 'Crystallize'],
        [/\bemboss\b/, 'stylize-emboss', 'Emboss'],
        [/\bfind\s*edges?\b|\bedge\s*detect(?:ion)?\b/, 'stylize-find-edges', 'Find Edges'],
        [/\bsolari[sz]e\b/, 'stylize-solarize', 'Solarize'],
        [/\btwirl\b/, 'distort-twirl', 'Twirl'],
        [/\bpinch\b/, 'distort-pinch', 'Pinch'],
        [/\bspheri[sz]e\b/, 'distort-spherize', 'Spherize'],
        [/\bripple\b/, 'distort-ripple', 'Ripple'],
        [/\bdespeckle\b/, 'noise-despeckle', 'Despeckle'],
        [null, 'noise-add', 'Noise'],
      ];
      const hit = SPECIALTY_FILTERS.find(([re]) => (re ? re.test(p) : _specNoise));
      if (hit && !_looksGenerative(p)) {
        await _runQuickControlOp('filters.apply', { id: hit[1] });
        _setAiCommandStatus(`Applied the ${hit[2]} filter.`, 'done');
        return;
      }
    }
    // "punchy / pop" → vibrance + a little contrast (a grade, never a repaint).
    if (/\bpunch(?:y|ier)?\b|\bmake\s+it\s+pop\b|\bmore\s+pop\b/.test(p)) {
      await _runQuickControlOp('adjustments.apply', { type: 'vibrance', params: { vibrance: 40 } });
      await _runQuickControlOp('adjustments.apply', { type: 'brightness-contrast', params: { contrast: 14 } });
      _setAiCommandStatus('Added punch.', 'done');
      return;
    }
    // Cinematic / moody / dramatic / dreamy → a deterministic film grade.
    if (/\bcinematic\b|\bmoody\b|\bdramatic\b|\bdreamy\b|\bethereal\b|\batmospheric\b|\bfilmic\b/.test(p)) {
      await _runQuickControlOp('adjustments.apply', { type: 'white-balance', params: { temperature: -16, tint: 8 } });
      await _runQuickControlOp('adjustments.apply', { type: 'brightness-contrast', params: { brightness: -10, contrast: 20 } });
      await _runQuickControlOp('adjustments.apply', { type: 'vibrance', params: { vibrance: -10 } });
      _setAiCommandStatus('Applied a cinematic grade.', 'done');
      return;
    }
    // History: "undo" / "redo" are simple deterministic actions, never a model call.
    if (/\bredo\b/.test(p)) {
      await _runQuickControlOp('history.redo', {});
      _setAiCommandStatus('Redone.', 'done');
      return;
    }
    if (/\b(undo|revert|go\s*back|step\s*back)\b/.test(p)) {
      await _runQuickControlOp('history.undo', {});
      _setAiCommandStatus('Undone.', 'done');
      return;
    }
    // Every toolbar tool is reachable from Quick Edit: "use the eraser tool",
    // "switch to marquee", or just the tool name switches the active tool.
    const toolId = _matchTool(p);
    if (toolId) {
      _clickToolButton(toolId);
      _setAiCommandStatus(`${_TOOLS[toolId]?.label || toolId} tool`, 'done');
      return;
    }
    // Named looks (film / photographer / era / colour style) render as an
    // explicit deterministic grade instead of a random tweak or a full repaint.
    // Content-change looks (neon, Blade Runner, Ghibli, painting, ...) return
    // null here and fall through to the creative repaint just below.
    const _lookRec = lookRecipeSteps(prompt, { strength: _lookStrength() });
    if (_lookRec) {
      _setAiCommandStatus(`Applying ${_lookRec.name} look…`, 'running');
      try {
        for (const step of _lookRec.steps) {
          try { await _runQuickControlOp(step.op, step.params || {}); }
          catch (err) { console.warn('[ge-ai-command] look step failed', step.op, err); }
        }
      } finally {
        _setAiCommandStatus('', '');
      }
      return;
    }
    // Creative / artistic intent ("turn it into a painting", "make it
    // cinematic", "anime style", "make it dramatic") must REPAINT the image
    // with the model. Doing it here (select-all + generative fill) is
    // deterministic and skips the planner's generic vibrance/brightness tweak,
    // which users perceive as "nothing creative".
    if (_looksCreative(p) && !/\b(?:remove|erase|delete|replace)\b/.test(p)) {
      try {
        _setAiCommandStatus('Creating…', 'running');
        await _runQuickControlOp('selection.selectAll', {});
        await _runQuickControlOp('ai.generativeFill', { prompt });
        await _runQuickControlOp('selection.deselect', {});
        _setAiCommandStatus('', '');
        return;
      } catch (err) {
        console.warn('[ge-ai-command] creative repaint failed', err);
        try { await _runQuickControlOp('selection.deselect', {}); } catch (_) {}
        _setAiCommandStatus('', '');
        return false;
      }
    }
    // "rotate/flip the whole image/canvas/photo" targets the whole document
    // (all layers + canvas, like Image > Image Rotation); everything else
    // targets the active layer (like Edit > Transform).
    const wholeImage = /\b(?:whole|entire|full)\s+(?:image|canvas|photo|picture|document)\b/.test(p)
      || /\b(?:rotate|flip|mirror|turn)\s+the\s+(?:whole\s+)?(?:image|canvas|photo|picture|document)\b/.test(p)
      || (/\b(?:canvas|document)\b/.test(p) && /\b(?:rotate|flip|mirror|turn)\b/.test(p));
    const doRotate = async (deg) => {
      await _runQuickControlOp(wholeImage ? 'document.rotate' : 'layer.rotate', { degrees: deg });
    };
    const doFlip = async (axis) => {
      await _runQuickControlOp(wholeImage ? 'document.flip' : 'layer.flip', { axis });
    };
    try {
      const degMatch = p.match(/\b(?:flip|rotate|turn)\b[^a-z0-9]*(90|180|270)\b/);
      if (degMatch) {
        const deg = Number(degMatch[1]);
        await doRotate(deg);
        requestAnimationFrame(() => _setAiCommandStatus(`Rotated ${deg}\u00b0.`, 'done'));
        return;
      }
      if (/\brotate\b.*\b180\b|\bupside\s*down\b/.test(p)) {
        await doRotate(180);
        requestAnimationFrame(() => _setAiCommandStatus('Rotated 180.', 'done'));
        return;
      }
      if (/\brotate\b.*\b(left|ccw|counter)\b|\bturn\s+left\b/.test(p)) {
        await doRotate(270);
        requestAnimationFrame(() => _setAiCommandStatus('Rotated left.', 'done'));
        return;
      }
      if (/\brotate\b|\bturn\s+right\b|\bclockwise\b/.test(p)) {
        await doRotate(90);
        requestAnimationFrame(() => _setAiCommandStatus('Rotated 90.', 'done'));
        return;
      }
      if (/\bflip\b.*\b(vertical|v)\b|\bmirror\b.*\b(vertical|v)\b/.test(p)) {
        await doFlip('v');
        _setAiCommandStatus('Flipped vertical.', 'done');
        return;
      }
      if (/\bflip\b|\bmirror\b/.test(p)) {
        await doFlip('h');
        _setAiCommandStatus('Flipped horizontal.', 'done');
        return;
      }
      // --- Edit ▸ Free Transform (deterministic) ---
      // Photoshop Edit ▸ Free Transform (Ctrl/Cmd+T). "free transform" acts on
      // the active layer; "transform the selection" acts on the marquee's pixel
      // contents. Requires an explicit free-transform / selection phrase so a
      // generative "transform the photo into a painting" still reaches the model.
      if (/\bfree[-\s]*transform\b/.test(p)
        || (/\btransform\b/.test(p) && /\b(?:selection|selected|marquee)\b/.test(p))) {
        if (/\b(?:selection|selected|marquee)\b/.test(p) && state.wandMask && typeof _startSelectionTransform === 'function') {
          _startSelectionTransform();
          _setAiCommandStatus('Transforming the selection. Drag the handles, then press Enter.', 'done');
        } else {
          await _runQuickControlOp('transform.start', {});
          _setAiCommandStatus('Started a free transform. Drag the handles, then press Enter.', 'done');
        }
        return;
      }
      // --- Layer ▸ Combine (unite / subtract / intersect / exclude) ---
      // Photoshop Layer ▸ Combine Shapes boolean. Requires an explicit combine
      // verb AND a shape noun so it never collides with "merge down" / "flatten"
      // (handled by the layer-visibility block below) or plain "subtract".
      if (/\b(?:unite|combine|merge|intersect|exclude|subtract)\b[^.]*\b(?:shapes?|rectangles?|ellipses?|circles?)\b/.test(p)) {
        const _combineShapes = (state.layers || []).filter((l) => l && (l.kind === 'shape' || l.shape));
        if (_combineShapes.length < 2) {
          _setAiCommandStatus('Add at least two shapes to combine.', '');
          return;
        }
        const _combineOp = /\bintersect\b/.test(p) ? 'intersect'
          : /\bexclude\b/.test(p) ? 'exclude'
          : (/\bsubtract\b/.test(p) || /\bminus\b/.test(p)) ? 'subtract'
          : 'unite';
        const _cOrder = new Map(state.layers.map((l, i) => [l.id, i]));
        _combineShapes.sort((a, b) => (_cOrder.get(a.id) ?? 0) - (_cOrder.get(b.id) ?? 0));
        const _cOffsets = _combineShapes.map((l) => state.layerOffsets.get(l.id) || { x: 0, y: 0 });
        const _cResult = _combineLayers(_combineShapes, _combineOp, { width: state.imgWidth, height: state.imgHeight, offsets: _cOffsets });
        if (_cResult) {
          const _cBase = _combineShapes[0];
          _saveState('Combine shapes');
          _cBase.kind = 'raster';
          _cBase.text = null;
          _cBase.shape = null;
          _cBase.canvas.width = state.imgWidth;
          _cBase.canvas.height = state.imgHeight;
          _cBase.ctx = _cBase.canvas.getContext('2d');
          _cBase.ctx.clearRect(0, 0, _cBase.canvas.width, _cBase.canvas.height);
          _cBase.ctx.drawImage(_cResult, 0, 0);
          state.layerOffsets.set(_cBase.id, { x: 0, y: 0 });
          const _cRemove = new Set(_combineShapes.slice(1).map((l) => l.id));
          state.layers = state.layers.filter((l) => !_cRemove.has(l.id));
          if (state.layers[0]?.id !== _cBase.id) state.layers = [_cBase, ...state.layers.filter((l) => l.id !== _cBase.id)];
          state.selectedLayerIds = [_cBase.id];
          state.activeLayerId = _cBase.id;
          composite();
          _renderLayerPanel();
          _setAiCommandStatus(`Combined the shapes (${_combineOp}).`, 'done');
        } else {
          _setAiCommandStatus('Could not combine those shapes.', '');
        }
        return;
      }
      // --- Rasterize (Layer ▸ Rasterize) ---
      // "rasterize the text/shape/logo" quotes a live text/shape/placed layer;
      // bake it to pixels. Routed here (independent of the layer-word guard
      // below) so it never falls through to the image model.
      if (/\brasteri[sz]e\b/.test(p)) {
        await _runQuickControlOp('layers.rasterize', {});
        _setAiCommandStatus('Rasterized the layer.', 'done');
        return;
      }
      // --- Layer ▸ Layer Mask (deterministic) ---
      // "add a layer mask" / "mask the selection" — Photoshop's Layer ▸
      // Layer Mask ▸ Reveal/Hide Selection. Requires an explicit mask word so
      // it never collides with the Filter "unsharp mask" (matched earlier) or
      // the layer-visibility branch below.
      if (/\bmask\b/.test(p)) {
        if (/\b(?:remove|delete|discard|drop)\b/.test(p)) {
          await _runQuickControlOp('layers.removeMask', {});
          _setAiCommandStatus('Removed the layer mask.', 'done');
          return;
        }
        if (/\bapply\b|\bbake\b/.test(p)) {
          await _runQuickControlOp('layers.applyMask', {});
          _setAiCommandStatus('Applied the layer mask.', 'done');
          return;
        }
        if (/\binvert\b|\binverse\b/.test(p)) {
          await _runQuickControlOp('mask.invert', {});
          _setAiCommandStatus('Inverted the layer mask.', 'done');
          return;
        }
        if (/\bfeather\b|\bsoften\b/.test(p)) {
          const _mAmt = p.match(/\b(?:by\s+)?(\d{1,3})\s*(?:px|pixels?)?\b/);
          const _mRadius = _mAmt ? Math.max(0, Math.round(Number(_mAmt[1]))) : 8;
          await _runQuickControlOp('mask.feather', { radius: Math.min(100, _mRadius) });
          _setAiCommandStatus('Feathered the layer mask.', 'done');
          return;
        }
        if (/\b(?:add|create|make|new|reveal|show|hide|mask|conceal)\b/.test(p)) {
          const _mHide = /\b(?:hide|conceal|mask\s*out|mask\s*away|invert)\b/.test(p) && !/\breveal\b|\bshow\b/.test(p);
          await _runQuickControlOp('layers.addMaskFromSelection', _mHide ? { invert: true } : {});
          _setAiCommandStatus(_mHide ? 'Added a layer mask (hiding the selection).' : 'Added a layer mask (revealing the selection).', 'done');
          return;
        }
      }
      // --- Layer ▸ Opacity (Photoshop Layers-panel Opacity field, deterministic) ---
      // "set the layer opacity to 50%" / "make it 30% opaque" / "make the layer
      // 60% transparent". Requires an opacity word AND a number, so a bare
      // "50%" or a tone request ("make it darker") is unaffected.
      {
        const _opWord = /\b(?:opacity|opaque|transparen(?:t|cy))\b/.test(p);
        const _opNum = p.match(/(\d{1,3})\s*(?:%|percent)?/);
        if (_opWord && _opNum && !_looksGenerative(p)) {
          const _pct = Math.max(0, Math.min(100, Number(_opNum[1])));
          const _val = /\btransparen(?:t|cy)\b/.test(p) ? (100 - _pct) / 100 : _pct / 100;
          await _runQuickControlOp('layers.setOpacity', { opacity: _val });
          _setAiCommandStatus(`Set the layer opacity to ${Math.round(_val * 100)}%.`, 'done');
          return;
        }
      }
      // --- Layer ▸ Blend Mode (Photoshop Layers-panel blend dropdown, deterministic) ---
      // "set the blend mode to multiply" / "change the blending to screen".
      // Needs the word "blend" AND a known mode name, so an Image ▸ Mode request
      // ("make it grayscale") or a tone prompt ("make it darker") is unaffected.
      if (/\bblend(?:ing)?\b/.test(p) && !_looksGenerative(p)) {
        const _BLEND = [
          ['linear dodge', 'linear-dodge'], ['linear burn', 'linear-burn'],
          ['darker color', 'darker-color'], ['darker colour', 'darker-color'],
          ['lighter color', 'lighter-color'], ['lighter colour', 'lighter-color'],
          ['vivid light', 'vivid-light'], ['linear light', 'linear-light'],
          ['pin light', 'pin-light'], ['hard mix', 'hard-mix'],
          ['color dodge', 'color-dodge'], ['colour dodge', 'color-dodge'],
          ['color burn', 'color-burn'], ['colour burn', 'color-burn'],
          ['hard light', 'hard-light'], ['soft light', 'soft-light'],
          ['source over', 'source-over'],
          ['multiply', 'multiply'], ['screen', 'screen'], ['overlay', 'overlay'],
          ['darken', 'darken'], ['lighten', 'lighten'],
          ['difference', 'difference'], ['exclusion', 'exclusion'],
          ['luminosity', 'luminosity'], ['saturation', 'saturation'],
          ['color', 'color'], ['colour', 'color'],
          ['subtract', 'subtract'], ['divide', 'divide'], ['dissolve', 'dissolve'],
          ['add', 'linear-dodge'], ['normal', 'source-over'],
        ];
        const _hit = _BLEND.find(([word]) => new RegExp(`\\b${word.replace(/ /g, '\\s+')}\\b`).test(p));
        if (_hit) {
          await _runQuickControlOp('layers.setBlend', { blendMode: _hit[1] });
          _setAiCommandStatus(`Set the layer blend mode to ${_hit[1].replace(/-/g, ' ')}.`, 'done');
          return;
        }
      }
      // --- Layer management commands ---
      // A layer-style / effect request ("add a stroke to the layer") mentions
      // "layer" but is not a management command — let it fall through to the
      // keyword planner that emits layers.addStyle.
      const _styleOrEffect = /\b(stroke|outline|drop\s*shadow|shadow|overlay|glow|bevel|emboss|color\s*overlay|colour\s*overlay|gradient\s*map|blur|sharpen|vignette|effect|filter|align)\b/.test(p);
      if (!_styleOrEffect && (/\blayers?\b/.test(p) || /\bflatten\b|\bmerge\s+down\b|\bun\s*group\b/.test(p))) {
        const rm = p.match(/\b(?:rename|call)\b[^]*?\blayers?\b\s*(?:to|as)?\s*(.+)$/);
        if (rm) {
          const nm = rm[1].trim().replace(/^["']|["']$/g, '');
          if (nm) {
            await _runQuickControlOp('layers.rename', { name: nm });
            _setAiCommandStatus(`Renamed layer to “${nm}”.`, 'done');
            return;
          }
        }
        if (/\bun\s*group\b/.test(p)) {
          await _runQuickControlOp('layers.ungroup', {});
          _setAiCommandStatus('Ungrouped.', 'done');
          return;
        }
        if (/\bgroup\b/.test(p) && !/\bun\s*group\b/.test(p)) {
          await _runQuickControlOp('layers.group', {});
          _setAiCommandStatus('Grouped the selected layers.', 'done');
          return;
        }
        if (/\bflatten\b|\bmerge\s+all\b|\bmerge\s+everything\b/.test(p)) {
          await _runQuickControlOp('layers.flatten', {});
          _setAiCommandStatus('Flattened the image.', 'done');
          return;
        }
        if (/\bmerge\b/.test(p)) {
          await _runQuickControlOp('layers.mergeDown', {});
          _setAiCommandStatus('Merged the layer down.', 'done');
          return;
        }
        if (/\b(hide|hidden|invisible)\b/.test(p)) {
          await _runQuickControlOp('layers.setVisibility', { visible: false });
          _setAiCommandStatus('Hid the layer.', 'done');
          return;
        }
        if (/\b(show|unhide|visible|reveal)\b/.test(p)) {
          await _runQuickControlOp('layers.setVisibility', { visible: true });
          _setAiCommandStatus('Showed the layer.', 'done');
          return;
        }
        if (/\btoggle\b/.test(p)) {
          await _runQuickControlOp('layers.setVisibility', {});
          _setAiCommandStatus('Toggled layer visibility.', 'done');
          return;
        }
        if (/\b(duplicate|copy|clone|replicate)\b/.test(p)) {
          await _runQuickControlOp('layers.duplicate', {});
          _setAiCommandStatus('Duplicated the layer.', 'done');
          return;
        }
        if (/\b(delete|remove|drop|trash|discard)\b/.test(p) && !/\b(bg|background)\b/.test(p)) {
          await _runQuickControlOp('layers.delete', {});
          _setAiCommandStatus('Deleted the layer.', 'done');
          return;
        }
        const reorder = (/\b(new|add|create|make|blank|empty)\b/.test(p)) ? null : _layerReorderDirection(p);
        if (reorder) {
          await _runQuickControlOp('layers.reorder', { direction: reorder });
          _setAiCommandStatus(`Moved the layer ${reorder}.`, 'done');
          return;
        }
        const adj = _detectAdjustmentType(p);
        if (adj) {
          await _runQuickControlOp('layers.addAdjustment', { type: adj });
          _setAiCommandStatus(`Added a ${adj.replace(/-/g, ' ')} layer.`, 'done');
          return;
        }
        if (/\b(new|add|create|make|blank|empty)\b/.test(p)) {
          await _runQuickControlOp('layers.addRaster', {});
          _setAiCommandStatus('Added a new layer.', 'done');
          return;
        }
      }
      // Background-removal intent (shared with the routing guard so the regex
      // can never drift between the two call sites).
      if (_looksBackgroundRemoval(p)) {
        await _runQuickControlOp('ai.removeBackground', {});
        // Chain a tone step when the prompt also asks for one
        // ("remove the background and make it brighter", "...and warm it up").
        if (/\b(bright(?:er|en)?|lighten|exposure|well[-\s]*lit)\b/.test(p)) {
          await _runQuickControlOp('adjustments.apply', { type: 'brightness-contrast', params: { brightness: 16, contrast: 4 } });
        } else if (/\b(dark(?:er|en)?|dimmer)\b/.test(p)) {
          await _runQuickControlOp('adjustments.apply', { type: 'brightness-contrast', params: { brightness: -14, contrast: 4 } });
        } else if (/\b(warm(?:er)?)\b/.test(p)) {
          await _runQuickControlOp('adjustments.apply', { type: 'white-balance', params: { temperature: 18 } });
        } else if (/\b(cool(?:er)?)\b/.test(p)) {
          await _runQuickControlOp('adjustments.apply', { type: 'white-balance', params: { temperature: -18 } });
        }
        _setAiCommandStatus('Background removal finished.', 'done');
        return;
      }
      if (!/\b(text|title|caption|font|heading|subtitle)\b/.test(p)
        && /\b(upscale|higher\s*res|increase\s*resolution|bigger|2x|4x)\b/.test(p)) {
        // Use the real upscale op (Real-ESRGAN, basic-upscale fallback). The
        // old code clicked a #ge-upscale-ai button that no longer exists, so
        // every upscale prompt failed with an availability error.
        const scale = /\b4x\b|\b4\s*x\b|\bquadruple\b/.test(p) ? 4 : 2;
        _setAiCommandStatus('Upscaling image...', 'running');
        await _runQuickControlOp('ai.generativeUpscale', { scale });
        _setAiCommandStatus('Upscale finished.', 'done');
        return;
      }
      if (/\b(denoise|noise|grain|grainy|clean\s*up)\b/.test(p)) {
        _setAiCommandStatus('Denoising image...', 'running');
        await _applyImageTool('/api/image/denoise', { strength: 0.55 }, 'Denoised', runBtn, { busyLabel: 'Denoising...' });
        _setAiCommandStatus('Added denoised layer.', 'done');
        return;
      }
      // --- Portrait looks (deterministic, region-aware) ---
      // Specific portrait attributes must reach their handler rather than the
      // blanket "enhance face" AI pass further down.
      if (/\bteeth\b/.test(p) && /\b(whiten|white|whiter|bright|brighten|yellow|smile)\b/.test(p)) {
        await _runQuickControlOp('retouch.teeth', {});
        _setAiCommandStatus('Whitened the teeth.', 'done');
        return;
      }
      if (/\beyes?\b/.test(p) && /\b(bright|brighten|whiten|white|clear|sparkle|pop|catch\s*light)\b/.test(p)) {
        try { await _runQuickControlOp('selection.samText', { text: 'eyes' }); }
        catch (_) { try { await _runQuickControlOp('selection.face', {}); } catch (_) {} }
        await _runQuickControlOp('adjustments.applyMasked', { brightness: 18, contrast: 8 });
        _setAiCommandStatus('Brightened the eyes.', 'done');
        return;
      }
      if (/\b(shine|shiny|shinier|oily|oil|glossy|greasy|dewy|sweaty|glare|reflection)\b/.test(p)) {
        try { await _runQuickControlOp('selection.face', {}); } catch (_) {}
        await _runQuickControlOp('adjustments.apply', { type: 'shadows-highlights', params: { highlights: -40, shadows: 8 } });
        _setAiCommandStatus('Reduced the shine.', 'done');
        return;
      }
      if (/\b(warm|warmer|warmth)\b/.test(p) && !_looksGenerative(p)) {
        await _runQuickControlOp('adjustments.apply', { type: 'white-balance', params: { temperature: 22 } });
        _setAiCommandStatus('Warmed the photo.', 'done');
        return;
      }
      if (/\b(cool|cooler|colder|moody|teal)\b/.test(p) && !_looksGenerative(p)) {
        await _runQuickControlOp('adjustments.apply', { type: 'white-balance', params: { temperature: -22 } });
        _setAiCommandStatus('Cooled the photo.', 'done');
        return;
      }
      if (/\b(brighten|brighter|lighten|exposure|too\s+dark|backlit|well[-\s]*lit)\b/.test(p) && !_looksGenerative(p)) {
        await _runQuickControlOp('adjustments.apply', { type: 'brightness-contrast', params: { brightness: 14, contrast: 4 } });
        _setAiCommandStatus('Brightened the photo.', 'done');
        return;
      }
      if (/\b(darken|darker|dimmer|reduce\s+exposure|too\s+bright)\b/.test(p) && !_looksGenerative(p)) {
        await _runQuickControlOp('adjustments.apply', { type: 'brightness-contrast', params: { brightness: -12, contrast: 4 } });
        _setAiCommandStatus('Darkened the photo.', 'done');
        return;
      }
      const _portraitAttr = /\b(warm|cool|golden|bright|dark|exposure|shine|shiny|oily|glossy|teeth|eyes?|skin|smooth|blemish|acne|pores|sharpen|contrast|vivid|hdr|vintage|glow|blur|soft)\b/.test(p);
      if (/\b(face|portrait|selfie)\b/.test(p) && !_portraitAttr) {
        _setAiCommandStatus('Enhancing face/portrait...', 'running');
        await _applyImageTool('/api/image/enhance-face', {}, 'Enhanced Face', runBtn, { busyLabel: 'Enhancing...' });
        _setAiCommandStatus('Added enhanced layer.', 'done');
        return;
      }
      if (/\b(skin|complexion|retouch|blemishes?|acne|pores|blackheads?)\b/.test(p)) {
        await _runQuickControlOp('retouch.skin', {
          smooth: 24,
          opacity: 0.85,
          blemish: /\bblemi\w*|acne|pores|blackheads?\b/.test(p),
        });
        _setAiCommandStatus('Smoothed the skin.', 'done');
        return;
      }
      if (/\b(sharpen|sharp|crisp|clearer)\b/.test(p)) {
        await _runQuickControlOp('filters.apply', { id: 'sharpen' });
        _setAiCommandStatus('Sharpened.', 'done');
        return;
      }

      // --- AI fill: fill the empty/transparent parts of the canvas with
      //     generated content (deterministic empty-region mask + inpaint). ---
      const _extendAsk = /\b(?:extend|expand|outpaint|wider|taller|aspect|ratio)\b|\d{1,2}\s*[:\-x]\s*\d{1,2}\b/i.test(p);
      const _fillRegion = /\b(?:sky|clouds?|background|foreground|subject|water|mountains?|trees?|grass|field|meadow|person|face|portrait)\b/i.test(p);
      if ((/\b(?:ai|generative)\s*fill\b|\boutpaint\b/i.test(p) && !_extendAsk && !_fillRegion)
        || (/\bfill\b[^.]*\b(?:empty|gap|gaps|blank|missing|transparent|rest|remainder)\b/i.test(p) && !_fillRegion)
        || /\b(?:complete|finish)\s+the\s+(?:canvas|image|photo|picture)\b/i.test(p)) {
        _setAiCommandStatus('Filling with AI…', 'running');
        try {
          await _runQuickControlOp('ai.fillEmpty', { prompt: 'continue the surrounding scene and lighting naturally into the empty area' });
          _setAiCommandStatus('', '');
        } catch (err) {
          _setAiCommandStatus(`Could not fill — ${err && err.message ? err.message : 'failed'}`, 'error');
        }
        return;
      }

      // --- Fill / paint bucket (deterministic, no model call) ---
      const FILL_WORDS = 'red|blue|green|yellow|orange|purple|pink|black|white|grey|gray|cyan|magenta|brown|teal|#[0-9a-fA-F]{3,6}';
      const fillWith = p.match(new RegExp(`\\bfill\\b[^.]*?\\bwith\\s+(${FILL_WORDS})\\b`, 'i'))
        || p.match(new RegExp(`\\b(?:paint|flood\\s*fill|flood-fill|bucket)\\b[^.]*?\\b(${FILL_WORDS})\\b`, 'i'))
        || p.match(new RegExp(`\\bfill\\b[^.]*?\\b(${FILL_WORDS})\\b`, 'i'));
      // Photoshop Edit ▸ Fill ▸ Contents: "fill with the foreground/background
      // colour". Requires an explicit "with/using the …" so it never collides
      // with a background *region* phrase ("fill the background with blue").
      const _fillFgBg = /\b(?:with|using)\s+(?:the\s+)?foreground(?:\s*colou?r)?\b/.test(p) ? (state.color || '#000000')
        : /\b(?:with|using)\s+(?:the\s+)?background(?:\s*colou?r)?\b/.test(p) ? (state.bgColor || '#ffffff') : null;
      // A "fill the canvas" request must NOT swallow a generative extend/expand
      // ("extend the canvas to 16:9 with generative fill") — that needs the model.
      const _fillIsGenerative = /\b(?:extend|expand|generative|genfill|outpaint|aspect|ratio)\b|\d+\s*:\s*\d+/.test(p);
      if (!_fillIsGenerative && (/\bfill\b|\bflood\s*fill\b|\bpaint\s*bucket\b/.test(p)) && /\b(?:canvas|whole|entire|everything|all|layer)\b/.test(p)) {
        // "fill the canvas/layer" — fill everything, defaulting to the current
        // foreground colour when the prompt names no colour (e.g. the
        // "Fill canvas" suggestion inserts "fill the canvas with ").
        const fillAllColor = fillWith ? _colorForName(fillWith[1]) : (_fillFgBg || state.color || '#000000');
        await _runQuickControlOp('selection.selectAll', {});
        await _runQuickControlOp('selection.fill', { color: fillAllColor });
        await _runQuickControlOp('selection.deselect', {});
        _setAiCommandStatus('Filled the canvas.', 'done');
        return;
      }
      if (/\bfill\b|\bflood\s*fill\b|\bpaint\s*bucket\b/.test(p) && (fillWith || _fillFgBg)) {
        const color = fillWith ? _colorForName(fillWith[1]) : _fillFgBg;
        if (/\bselection\b|\bselected\b/.test(p) && state.wandMask) {
          // Photoshop Edit ▸ Fill: fill only the active selection.
          await _runQuickControlOp('selection.fill', { color });
          _setAiCommandStatus('Filled the selection.', 'done');
          return;
        }
        if (/\bsky\b/.test(p)) {
          await _runQuickControlOp('selection.sky', {});
          await _runQuickControlOp('selection.fill', { color });
          _setAiCommandStatus('Filled the sky.', 'done');
          return;
        }
        if (/\bbackground\b/.test(p)) {
          await _runQuickControlOp('selection.background', {});
          await _runQuickControlOp('selection.fill', { color });
          _setAiCommandStatus('Filled the background.', 'done');
          return;
        }
        if (/\bsubject\b|\bperson\b|\bpeople\b/.test(p)) {
          await _runQuickControlOp('selection.subject', { label: 'person' });
          await _runQuickControlOp('selection.fill', { color });
          _setAiCommandStatus('Filled the subject.', 'done');
          return;
        }
        const regionColor = p.match(new RegExp(`\\b(?:the\\s+)?(${FILL_WORDS})\\s+(?:area|region|part|background|section|block)\\b`, 'i'));
        const layer = activeLayer();
        if (regionColor && layer) {
          const pt = _findColorPoint(layer, _namedRgb(regionColor[1]));
          if (pt) {
            await _runQuickControlOp('fill.flood', { x: pt.x, y: pt.y, color, tolerance: 60 });
            _setAiCommandStatus('Filled the area.', 'done');
            return;
          }
        }
        if (layer) {
          if (_fillFgBg) {
            // Edit ▸ Fill with an explicit foreground/background colour and no
            // selection: fill the whole layer so the status can name the exact
            // colour that was used.
            await _runQuickControlOp('selection.selectAll', {});
            await _runQuickControlOp('selection.fill', { color });
            await _runQuickControlOp('selection.deselect', {});
            const _fgBgLabel = /\bbackground\b/.test(p) ? 'background' : 'foreground';
            _setAiCommandStatus(`Filled the layer with the ${_fgBgLabel} colour (${color}).`, 'done');
            return;
          }
          await _runQuickControlOp('fill.flood', { x: Math.round(layer.canvas.width / 2), y: Math.round(layer.canvas.height / 2), color, tolerance: 40 });
          _setAiCommandStatus('Filled from the centre.', 'done');
          return;
        }
      }

      // --- Layer ▸ Select ▸ All Layers (deterministic) ---
      // Photoshop Layer ▸ Select ▸ All Layers. Requires an explicit layer noun
      // so a plain "select all" still means the pixel marquee (handled by the
      // selection block just below); clicking the real button runs the same
      // multi-select + re-render as the Layer menu item.
      if (/\bselect\b[^.]*\b(?:all|every|everything)\b[^.]*\blayers?\b/.test(p)) {
        document.getElementById('ge-select-all-layers')?.click();
        _setAiCommandStatus('Selected all layers.', 'done');
        return;
      }

      // --- Image ▸ Adjustments ▸ Invert (Cmd/Ctrl+I, deterministic) ---
      // Colour-inverts the active layer. Requires an explicit colour/image noun
      // so "invert the selection" still routes to Select ▸ Inverse (handled by
      // the selection block just below). Sits before it so the plain "invert"
      // keyword cannot hijack an image invert.
      if (/\binvert\b[^.]*\b(?:colou?rs?|tones?|image|photo|picture)\b/.test(p)
        || /\b(?:colou?rs?|image|photo|picture)\b[^.]*\binvert\b/.test(p)) {
        await _runQuickControlOp('adjustments.apply', { type: 'invert' });
        _setAiCommandStatus('Inverted the colours.', 'done');
        return;
      }

      // --- Layer ▸ Layer Style effects (deterministic, no model call) ---
      // Photoshop's one-click Layer ▸ Layer Style ▸ Drop Shadow / Stroke / Outer
      // Glow / Color Overlay. The layer-management block above deliberately lets
      // these fall through to the keyword planner, so route the unambiguous ones
      // straight to the non-destructive style op. Each needs a specific noun (and
      // an add-ish verb for the shadow/glow/stroke cases) so a plain "shadow" /
      // "stroke" filter word cannot hijack an unrelated edit.
      {
        const _STYLE_COLOURS = 'red|blue|green|yellow|orange|purple|pink|black|white|grey|gray|cyan|magenta|brown|teal|#[0-9a-fA-F]{3,6}';
        const _addVerb = /\b(?:add|apply|give|put|create|cast|with)\b/.test(p);
        let styleType = null;
        if (/\bcolou?r\s*overlay\b/.test(p)) styleType = 'color-overlay';
        else if (/\bgradient\s*overlay\b/.test(p)) styleType = 'gradient-overlay';
        else if (/\bdrop\s*shadow\b/.test(p)
          || (_addVerb && /\bshadow\b(?!\s*(?:detail|recovery|highlight|highlights))/i.test(p))) styleType = 'drop-shadow';
        else if (_addVerb && /\b(?:outer\s*)?glow\b/.test(p)) styleType = 'outer-glow';
        else if (_addVerb && /\b(?:stroke|outline)\b/.test(p)) styleType = 'stroke';
        if (styleType && !_looksGenerative(p)) {
          let params = {};
          if (styleType !== 'drop-shadow') {
            const cm = p.match(new RegExp(`\\b(${_STYLE_COLOURS})\\b`));
            const col = cm ? _colorForName(cm[1]) : null;
            if (col) params = { color: col };
          }
          await _runQuickControlOp('layers.addStyle', { type: styleType, params });
          _setAiCommandStatus(`Added a ${styleType.replace(/-/g, ' ')}.`, 'done');
          return;
        }
      }

      // --- Selection commands (deterministic, no model call) ---
      if (/\bselect\b|\bselection\b|\bdeselect\b|\binvert\b|\binverse\b|\breselect\b/.test(p)) {
        // Photoshop Select ▸ Reselect (Cmd/Ctrl+Shift+D): bring back the most
        // recently dismissed selection. Checked first, because "reselect" is the
        // only place the standalone word appears and it must not fall through.
        if (/\breselect\b|\bbring\s+back\s+(?:the\s+)?selection\b|\brestore\s+(?:the\s+)?selection\b/.test(p)) {
          if (_reselectSelection()) _setAiCommandStatus('Reselected the last selection.', 'done');
          else _setAiCommandStatus('There is nothing to reselect yet.', '');
          return;
        }
        // Photoshop Select ▸ Save Selection: remember the current marquee under a
        // name ("…as sky") so Select ▸ Load Selection can bring it back later.
        // Falls back to an auto-numbered name; reports a hint when nothing is
        // selected rather than escalating to the model.
        if (/\b(?:save|store)\b/.test(p)) {
          const nm = p.match(/\b(?:as|named?|called)\s+([^.,;]+?)\s*$/i);
          const name = nm ? nm[1].trim() : '';
          if (_saveNamedSelection(name)) {
            const count = (state.savedSelections || []).length;
            _setAiCommandStatus(`Saved the selection as “${name || `Selection ${count}`}”.`, 'done');
          } else {
            _setAiCommandStatus('There is no active selection to save.', '');
          }
          return;
        }
        // Photoshop Select ▸ Load Selection: bring back a previously saved
        // marquee ("load the selection as sky"). Resolve by name ("…as sky" or
        // "load the sky selection"); with no name, fall back to the only saved
        // selection, else the most recent one.
        if (/\bload\b/.test(p)) {
          const saved = state.savedSelections || [];
          if (!saved.length) {
            _setAiCommandStatus('There are no saved selections to load yet.', '');
            return;
          }
          let name = '';
          const nm = p.match(/\b(?:as|named?|called)\s+([^.,;]+?)\s*$/i);
          if (nm) name = nm[1].trim();
          if (!name) {
            const m2 = p.match(/\bload\s+(?:the\s+|my\s+)?([a-z0-9 _-]+?)\s+selection\b/i);
            if (m2) name = m2[1].trim();
          }
          const lc = name.toLowerCase();
          let target = name
            ? saved.find(s => s.name.toLowerCase() === lc) || saved.find(s => lc.includes(s.name.toLowerCase()))
            : null;
          if (!target && saved.length === 1) target = saved[0];
          if (!target) target = saved[saved.length - 1];
          if (_loadNamedSelection(target.id)) {
            _setAiCommandStatus(`Loaded the “${target.name}” selection.`, 'done');
          } else {
            _setAiCommandStatus('Could not load that selection.', '');
          }
          return;
        }
        if (/\bselect\s+(?:all|everything)\b|\bselect\s+the\s+(?:whole|entire)\b|\ball\b/.test(p) && /\bselect\b/.test(p)) {
          await _runQuickControlOp('selection.selectAll', {});
          _setAiCommandStatus('Selected all.', 'done');
          return;
        }
        if (/\bdeselect\b|\bclear\s+(?:the\s+)?selection\b|\bremove\s+(?:the\s+)?selection\b/.test(p)) {
          await _runQuickControlOp('selection.deselect', {});
          _setAiCommandStatus('Deselected.', 'done');
          return;
        }
        if (/\binvert\b|\binverse\b/.test(p)) {
          await _runQuickControlOp('selection.invert', {});
          _setAiCommandStatus('Inverted the selection.', 'done');
          return;
        }
        // Photoshop Select ▸ Modify: Feather / Expand / Contract. Parse an
        // optional pixel amount ("by 20px", "20 pixels"); fall back to the
        // Photoshop-style defaults (feather 8, expand/contract 4).
        const _selAmount = (def) => {
          const m = p.match(/\b(?:by\s+)?(\d{1,3})\s*(?:px|pixels?)?\b/);
          return m ? Math.max(0, Math.round(Number(m[1]))) : def;
        };
        if (/\bfeather\b|\bsoften\b/.test(p)) {
          const radius = Math.min(100, _selAmount(8));
          await _runQuickControlOp('selection.feather', { radius });
          _setAiCommandStatus(`Feathered the selection by ${radius}px.`, 'done');
          return;
        }
        if (/\bexpand\b|\bgrow\b|\benlarge\b|\bbigger\b/.test(p)) {
          const amount = Math.min(64, _selAmount(4));
          await _runQuickControlOp('selection.expand', { amount });
          _setAiCommandStatus(`Expanded the selection by ${amount}px.`, 'done');
          return;
        }
        if (/\bcontract\b|\bshrink\b|\btighten\b|\breduce\b/.test(p)) {
          const amount = Math.min(64, _selAmount(4));
          await _runQuickControlOp('selection.contract', { amount });
          _setAiCommandStatus(`Contracted the selection by ${amount}px.`, 'done');
          return;
        }
        // Select ▸ Modify ▸ Border: a ring of the selection edge (half inside,
        // half outside), and Smooth: rounds the selection corners. Both are
        // deterministic mask operations from the shared refine pipeline.
        if (/\bborder\b/.test(p)) {
          const base = _selectionMaskAsDocument({ materializeLasso: true });
          if (!base) { _setAiCommandStatus('There is no active selection to border.', ''); return; }
          const width = Math.min(100, Math.max(1, _selAmount(5)));
          _saveState('Border selection');
          state.wandMask = _refineSelectionMask(base, { border: width });
          state.wandMaskSpace = 'document';
          state.wandMaskVisible = true;
          composite();
          _setAiCommandStatus(`Added a ${width}px border to the selection.`, 'done');
          return;
        }
        if (/\bsmooth\b/.test(p)) {
          const base = _selectionMaskAsDocument({ materializeLasso: true });
          if (!base) { _setAiCommandStatus('There is no active selection to smooth.', ''); return; }
          const amount = Math.min(50, Math.max(1, _selAmount(4)));
          _saveState('Smooth selection');
          state.wandMask = _refineSelectionMask(base, { smooth: amount });
          state.wandMaskSpace = 'document';
          state.wandMaskVisible = true;
          composite();
          _setAiCommandStatus(`Smoothed the selection by ${amount}px.`, 'done');
          return;
        }
        // Edit ▸ Cut (Cmd/Ctrl+X) and Edit ▸ Clear (Delete): cut copies then
        // removes the selection pixels; clear just removes them.
        if (/\bcut\b/.test(p)) {
          await _runQuickControlOp('selection.copy', { cut: true });
          _setAiCommandStatus('Cut the selection.', 'done');
          return;
        }
        if (/\bdelete\b/.test(p)) {
          await _runQuickControlOp('selection.delete', {});
          _setAiCommandStatus('Deleted the selection.', 'done');
          return;
        }
        if (/\bsubject\b|\bperson\b|\bpeople\b/.test(p)) {
          await _runQuickControlOp('selection.subject', { label: 'person' });
          _setAiCommandStatus('Selected the subject.', 'done');
          return;
        }
        if (/\bsky\b/.test(p)) {
          await _runQuickControlOp('selection.sky', {});
          _setAiCommandStatus('Selected the sky.', 'done');
          return;
        }
        if (/\bbackground\b/.test(p)) {
          await _runQuickControlOp('selection.background', {});
          _setAiCommandStatus('Selected the background.', 'done');
          return;
        }
      }

      // --- Align (deterministic) ---
      // Skip when the prompt also asks to add text (e.g. "add a subtitle and
      // align it") so the keyword planner adds the text before aligning.
      const alignWantsText = /\b(?:add|create|write|put|insert)\b[^.]*\b(?:title|caption|label|text|word|subtitle)\b/.test(p)
        || /\bwith\s+the\s+(?:text|title|word|caption|label)\b/.test(p);
      const alignM = p.match(/\balign\b[^.]*?\b(centre|center|left|right|top|middle|bottom)\b/);
      if (/\balign\b/.test(p) && alignM && !alignWantsText) {
        const raw = alignM[1].toLowerCase();
        const mode = raw === 'centre' ? 'center' : raw === 'middle' ? 'center' : raw;
        await _runQuickControlOp('layers.align', { mode });
        _setAiCommandStatus(`Aligned ${raw}.`, 'done');
        return;
      }

      // --- Type ▸ Warp (deterministic) ---
      // Photoshop's Warp Text. A style word ("arch/wave/bulge/…") only counts
      // when the prompt also names text, so "wave" alone (e.g. a photo request)
      // isn't mistaken for a warp.
      const _warpWord = /\bwarp(?:ed)?\b/.test(p);
      const _warpStyleHit = /\b(?:arc|arch|arched|bulge|flag|wave|rise|fisheye|inflate|squeeze|twist)\b/.test(p);
      const _warpTextWord = /\b(?:text|title|caption|heading|word|lettering|type)\b/.test(p);
      if ((_warpWord || (_warpStyleHit && _warpTextWord)) && !/\b(?:add|create|write|put|insert)\b/.test(p)) {
        let style = 'arc';
        if (/\bremove\b|\bclear\b|\bundo\b|\bnone\b|\breset\b|\bstraighten\b|\bun-?warp\b/.test(p)) style = 'none';
        else if (/\barch/.test(p)) style = 'arch';
        else if (/\bbulge\b|\bballoon\b/.test(p)) style = 'bulge';
        else if (/\binflate\b/.test(p)) style = 'inflate';
        else if (/\bsqueeze\b/.test(p)) style = 'squeeze';
        else if (/\bfisheye\b/.test(p)) style = 'fisheye';
        else if (/\bwave\b/.test(p)) style = 'wave';
        else if (/\brise\b/.test(p)) style = 'rise';
        else if (/\btwist\b/.test(p)) style = 'twist';
        else if (/\bflag\b/.test(p)) style = 'flag';
        else if (/\barc\b/.test(p)) style = 'arc';
        const _wAm = p.match(/\b(?:by|amount|to)\s*(-?\d{1,3})\b/);
        const _wAmount = style === 'none' ? 0 : (_wAm ? Math.max(-100, Math.min(100, Math.round(Number(_wAm[1])))) : 50);
        const _wLayer = (state.layers || []).slice().reverse().find((l) => l && l.kind === 'text' && l.text);
        if (!_wLayer) {
          _setAiCommandStatus('There is no text layer to warp.', '');
          return;
        }
        _saveState('Warp text');
        _rerenderTextLayer(_wLayer, { ..._wLayer.text, warp: style, warpAmount: _wAmount });
        _renderLayerPanel();
        _setAiCommandStatus(style === 'none' ? 'Removed the text warp.' : `Warped the text (${style}).`, 'done');
        return;
      }

      // --- Text edit (deterministic) ---
      if (/\btext\b|\btitle\b|\bcaption\b|\bfont\b|\bheading\b/.test(p) && !/\b(add|create|write|put|insert)\b/.test(p)) {
        const tp = {};
        if (/\b(bigger|larger|increase|grow|enlarge)\b/.test(p)) tp.size = 96;
        else if (/\b(smaller|reduce|decrease|shrink|tiny)\b/.test(p)) tp.size = 28;
        const cm = p.match(new RegExp(`\\b(${FILL_WORDS})\\b`, 'i'));
        if (cm) tp.color = _colorForName(cm[1]);
        if (Object.keys(tp).length) {
          await _runQuickControlOp('text.edit', tp);
          _setAiCommandStatus('Updated the text.', 'done');
          return;
        }
      }

      // --- Crop to the active selection (Image ▸ Crop) ---
      if (/\bcrop\b/.test(p) && /\bselection\b|\bselected\b/.test(p) && state.wandMask) {
        const b = _selectionBounds(state.wandMask, state.imgWidth, state.imgHeight);
        if (b) {
          await _runQuickControlOp('document.crop', { x: b.x, y: b.y, width: b.width, height: b.height });
          _setAiCommandStatus(`Cropped to the selection (${b.width}×${b.height}).`, 'done');
          return;
        }
      }

      // --- Crop to a named aspect ratio (deterministic) ---
      // "extend/expand … to 16:9" grows the canvas (generative), not crops it.
      const cropRect = (/\bextend\b|\bexpand\b/.test(p)) ? null : _formatCropRect(prompt, state.imgWidth, state.imgHeight);
      if (cropRect) {
        await _runQuickControlOp('document.crop', { x: cropRect.x, y: cropRect.y, width: cropRect.width, height: cropRect.height });
        _setAiCommandStatus(`Cropped to ${cropRect.width}×${cropRect.height}.`, 'done');
        return;
      }

      // --- Clipboard (deterministic) ---
      if (/\bcopy\b/.test(p) && !/\bcopy\s+the\s+(?:image|layer)\b/.test(p)) {
        await _runQuickControlOp('selection.copy', {});
        _setAiCommandStatus('Copied the selection.', 'done');
        return;
      }
      if (/\bpaste\b/.test(p)) {
        await _runQuickControlOp('selection.paste', {});
        _setAiCommandStatus('Pasted.', 'done');
        return;
      }

      // --- Colour swatches (Photoshop: X swaps, D resets) ---
      if (/\bswap\b[^.]*\b(?:colou?rs?|foreground|background)\b|\b(?:colou?rs?)\b[^.]*\bswap\b/.test(p)) {
        window.geSwapColors?.();
        _setAiCommandStatus('Swapped the foreground and background colours.', 'done');
        return;
      }
      if (/\breset\b[^.]*\bcolou?rs?\b|\bdefault\b[^.]*\bcolou?rs?\b/.test(p)) {
        window.geResetColors?.();
        _setAiCommandStatus('Reset to the default colours.', 'done');
        return;
      }
      // --- View menu (Photoshop View ▸ Rulers / Grid / Snap / Clear guides) ---
      // Deterministic viewport toggles. Click the real View-menu item so the
      // exact same state flip + overlay refresh + persistence runs as a manual
      // click; require an explicit view verb (or the "snap to grid" phrase) so
      // a generative prompt ("add a grid of windows") is not mistaken for one.
      if (/\b(rulers?|grid|guides?|snap(?:ping)?)\b/.test(p)) {
        const viewItem = (action) => document.querySelector(`#ge-view-menu [data-view-action="${action}"]`);
        const viewIsOn = { rulers: !!state.rulersVisible, grid: !!state.gridVisible, snap: !!state.snapEnabled, 'snap-grid': !!state.snapToGrid };
        const viewOff = /\b(hide|turn\s+off|switch\s+off|disable|remove|clear)\b/.test(p);
        const viewToggle = /\btoggle\b/.test(p);
        const viewOn = /\b(show|display|reveal|turn\s+on|switch\s+on|enable)\b/.test(p);
        if (/\bguides?\b/.test(p) && viewOff) {
          viewItem('clear-guides')?.click();
          _setAiCommandStatus('Cleared the guides.', 'done');
          return;
        }
        if (/\bsnap\s*to\s*grid\b/.test(p) && !viewOff) {
          const want = viewToggle ? !viewIsOn['snap-grid'] : true;
          const item = viewItem('snap-grid');
          if (item && viewIsOn['snap-grid'] !== want) item.click();
          _setAiCommandStatus(`Snapping to the grid ${want ? 'is on' : 'is off'}.`, 'done');
          return;
        }
        if (viewOn || viewOff || viewToggle) {
          for (const [re, action, label] of [[/\bsnap(?:ping)?\b/, 'snap', 'Snapping'], [/\bgrid\b/, 'grid', 'Grid'], [/\brulers?\b/, 'rulers', 'Rulers']]) {
            if (!re.test(p)) continue;
            const want = viewOff ? false : viewToggle ? !viewIsOn[action] : true;
            const item = viewItem(action);
            if (item && viewIsOn[action] !== want) item.click();
            _setAiCommandStatus(action === 'snap' ? `Snapping ${want ? 'is on' : 'is off'}.` : `${label} ${want ? 'shown' : 'hidden'}.`, 'done');
            return;
          }
        }
      }

      // No deterministic local op matched. Signal the caller so it can
      // escalate to the server planner / AI model instead of guessing.
      return false;
    } catch (err) {
      console.error('[ge-ai-command] failed', err);
      _setAiCommandStatus(err?.message || 'AI edit failed', 'error');
      return true;
    }
  };

  // Prompts that must go to the generative model — never approximated with a
  // local adjustment/filter. Deterministic requests (colour, light, rotation,
  // layers, text, selection, …) are handled locally first.
  // Nouns/subjects that mean "add a real thing to the picture" -> generative.
  const GEN_ADD_NOUNS = 'person|man|woman|men|women|people|boy|girl|child|kid|dog|cat|animal|horse|bird|birds|car|truck|boat|ship|plane|balloon|tree|trees|forest|flower|flowers|wildflowers|plant|bush|moon|sun|stars?|clouds?|rainbow|building|house|castle|mountain|mountains|lake|river|waterfall|fog|mist|hat|sunglasses|glasses|beard|mustache|smile|tattoo|balloon|logo|object|snow|snowfall';
  const GEN_COLORS = 'red|blue|green|yellow|orange|purple|violet|pink|teal|cyan|magenta|brown|gold|golden|grey|gray|black|white|turquoise|azure';
  const GEN_RECOLOR = 'red|blue|green|yellow|orange|purple|violet|pink|teal|cyan|magenta|brown|gold|golden|turquoise|azure';
  const _looksGenerative = (text) => {
    const t = String(text || '');
    if (/\b(?:remove|erase|get\s+rid\s+of|replace|swap|change\s+(?:the\s+)?background|background|scene|sky|beach|mountain|forest|ocean|sea|desert|city|night|sunset|sunrise|stars?s|snowy?|rain(?:y)?|generate|generative\s*fill|inpaint|extend|expand\s+the|insert)\b/i.test(t)) return true;
    // "add <up to 3 words> <generative noun>" (covers adjectives: dramatic clouds, wildflowers, ...).
    if (new RegExp(`\\badd\\s+(?:a\\s+|an\\s+|the\\s+)?(?:\\w+\\s+){0,3}?(?:${GEN_ADD_NOUNS})\\b`, 'i').test(t)) return true;
    // "put <something> on <something>" (e.g. put sunglasses on her).
    if (/\bput\s+\w+(?:\s+\w+){0,2}\s+on\b/i.test(t)) return true;
    // "make it look/feel like X" or a scene/season change.
    if (/\bmake\s+(?:it|the\s+\w+)\s+(?:look|feel)\s+like\b/i.test(t)) return true;
    if (/\bmake\s+it\s+(?:look\s+)?(?:snowy|snow|autumn|fall|winter|spring|summer|rainy|rain|foggy|fog|misty|stormy|storm|sunny|tropical|desert|underwater)\b/i.test(t)) return true;
    if (/\bmake\s+it\s+(?:autumn|winter|spring|summer|snowy|rainy|stormy)\b/i.test(t)) return true;
    // Targeted recolour: only the model can repaint a specific object.
    if (/\brecolou?r\b/i.test(t)) return true;
    if (/\bmake\s+only\b/i.test(t)) return true;
    if (new RegExp(`\\b(?:change|turn|convert)\\b[^.]*\\b(?:to|into)\\s+(?:a\\s+|an\\s+|the\\s+)?(?:${GEN_COLORS})\\b`, 'i').test(t)) return true;
    // "make/turn <object> <colour>" repaints a specific object (exclude tone looks
    // like "make it black and white" and text-colour edits "make the title red").
    if (new RegExp(`\\b(?:make|turn)\\s+(?:the\\s+|only\\s+)?(?:\\w+\\s+){0,2}(?:${GEN_RECOLOR})\\b`, 'i').test(t)
      && !/\b(text|title|caption|font|heading|subtitle|label|black\s+and\s+white|b&w|grayscale|monochrome)\b/i.test(t)) return true;
    return false;
  };

  // A subset of the generative gate: prompts that repaint a specific object a
  // named colour. These must never be satisfied by a global local adjustment.
  const _looksRecolor = (text) => {
    const t = String(text || '');
    if (/\brecolou?r\b/i.test(t)) return true;
    if (/\bmake\s+only\b/i.test(t)) return true;
    if (new RegExp(`\\b(?:change|turn|convert|paint|colou?r)\\b[^.]*\\b(?:to|into)\\s+(?:a\\s+|an\\s+|the\\s+)?(?:${GEN_COLORS})\\b`, 'i').test(t)) return true;
    if (new RegExp(`\\b(?:make|turn)\\s+(?:the\\s+|only\\s+)?(?:\\w+\\s+){0,2}(?:${GEN_RECOLOR})\\b`, 'i').test(t)
      && !/\b(text|title|caption|font|heading|subtitle|label|black\s+and\s+white|b&w|grayscale|monochrome)\b/i.test(t)) return true;
    return false;
  };

  // Explicit background-removal intent. Broad on purpose: the old regex only
  // matched the exact word "remove", so "removal", "bg remov", "cutout",
  // "no background", "isolate the subject" etc. missed it. Shared by the
  // deterministic fast path and the routing guard that keeps these prompts
  // away from the generative model.
  const _looksBackgroundRemoval = (text) => {
    const p = String(text || '').toLowerCase();
    return /\b(?:remov\w*|erase|delete|drop|get\s*rid\s*of|cut\s*out|cutout|knock\s*out|isolate|strip|clear|transparent|no|without|rm)\b[^.]*\b(?:bg|background|backdrop|back\s*ground)\b/.test(p)
      || /\b(?:bg|background|backdrop|back\s*ground)\b[^.]*\b(?:remov\w*|erase|delete|drop|cut\s*out|cutout|transparent|gone|less|out)\b/.test(p)
      || /\b(?:cutout|cut\s*out|knock\s*out|isolate)\b[^.]*\b(?:subject|foreground|person|them|him|her|object|it)\b/.test(p);
  };

  // Style/"look" edits: the whole feel changes (often generative or a heavy
  // grade). Used to decide when the VL self-check should run.
  const _looksLook = (text) => /\b(?:look|style|vintage|retro|cinematic|dramatic|moody|film|polaroid|glamou?r|glossy|editorial|headshot|poster|noir|neon|sepia|faded|washed|instagram|wes\s+anderson|ghibli|blade\s+runner|national\s+geographic)\b/i.test(String(text || ''));

  // The server has deterministic named-look recipes keyed on specific words
  // (cyberpunk, "vintage film", wes anderson, ghibli, film noir). Map common
  // phrasings that miss those keywords onto them so e.g. "Blade Runner neon
  // night" hits the cyberpunk instruction edit instead of a generic tweak.
  const _LOOK_ALIASES = [
    [/\b(blade\s*runner|neon\s*(?:night|city|glow|soaked)|synthwave|vaporwave|futuristic\s+(?:city|neon))\b/i, 'cyberpunk'],
    [/\b(1970s|70s|seventies|analog\s+film|film\s+photograph|polaroid|instant\s+camera|instant\s+photo|kodak)\b/i, 'vintage film'],
  ];
  const _enrichLookPrompt = (text) => {
    const t = String(text || '');
    for (const [re, canonical] of _LOOK_ALIASES) {
      const already = new RegExp(canonical.replace(/\s+/g, '\\s+'), 'i');
      if (re.test(t) && !already.test(t)) return `${t} (${canonical} look)`;
    }
    return t;
  };

  // Creative / artistic requests (art mediums and strong moods). These should
  // actually REPAINT the image with the model, never a quiet slider tweak
  // (vibrance + brightness), which is what users notice as "nothing creative".
  const _CREATIVE_STYLES = 'painting|watercolo?ur|oil\\s+paint|acrylic|gouache|pastel|sketch|pencil|charcoal|ink\\s+drawing|line\\s*art|comic(?:\\s*book)?|cartoon|anime|manga|illustration|illustrated|3d\\s*render|cgi|claymation|clay|pixel\\s*art|voxel|low\\s*poly|mosaic|stained\\s*glass|pop\\s*art|art\\s+nouveau|ukiyo|surreal|fantasy\\s+(?:art|landscape)|steampunk|cyberpunk|vaporwave|synthwave|graffiti|woodcut|linocut|etching|lithograph|impressionis\\w*|cubis\\w*|abstract|doodle|vector\\s*art|flat\\s+design|posteriz\\w*|neon\\s+sign|neon|blade\\s*runner|bloom|holograph\\w*|epic|painterly|artistic|psychedelic|trippy|gothic|retro\\s+future';
  const _looksCreative = (text) => new RegExp(`\\b(?:${_CREATIVE_STYLES})\\b`, 'i').test(String(text || ''));
  // Look-grade strength (localStorage, set by the AI settings Strength slider);
  // default strong enough that a grade reads at a glance.
  const _lookStrength = () => {
    try {
      const raw = parseFloat(localStorage.getItem('ge-look-strength'));
      if (Number.isFinite(raw) && raw > 0) return raw;
    } catch (_) {}
    return 1.4;
  };

  // Clearly vague / meaningless requests. The planner must not answer these
  // with a silent default brightness/vibrance tweak; ask what was meant and
  // offer a couple of concrete options instead.
  const _VAGUE_PATTERNS = /\b(?:make it better|make it nice|make it good|make it pretty|make it amazing|fix it|fix this|do the thing|do something|improve it|clean it up|edit this|edit the photo)\b/i;
  // Concrete named looks / styles we can actually honour. If a prompt says
  // "look ..." but names none of these, we do not know what to do.
  const _NAMED_LOOK = /\b(?:vintage|retro|cinematic|dramatic|moody|film|noir|polaroid|kodachrome|kodak|sepia|faded|glamou?r|glossy|editorial|headshot|poster|neon|instagram|wes\s*anderson|ghibli|blade\s*runner|national\s*geographic|natgeo|technicolor|kurosawa|wong\s*kar|vhs|ansel\s*adams|cyberpunk|synthwave|vaporwave|futuristic|dreamy|ethereal|golden\s*hour|teal\s*(?:and|&)\s*orange|western|documentary|disposable|camera|attenborough|super\s*8|8mm|16mm)\b/i;
  const _isVague = (text) => {
    const s = String(text || '').trim();
    if (!s || _VAGUE_PATTERNS.test(s)) return true;
    // A prompt a deterministic control op can handle is never "vague" — never
    // let the image-model fallback swallow a local command.
    if (/\b(?:psd|png|jpe?g|webp|export|save|resize|scale|vignette|punchy|cinematic|moody|dramatic|dreamy|ethereal|atmospheric|warmer|cooler|brighter|darker|brighten|darken|warm|cool|rotate|flip|mirror|crop|trim|blur|sharpen|upscale|denoise|grayscale|greyscale|monochrome|sepia|vibrance|saturation|contrast|auto[-\s]?tone|black\s*and\s*white)\b/.test(s)) return false;
    // "look <something we don't know>" — e.g. "make it look proffesional".
    if (_looksLook(s) && !_NAMED_LOOK.test(s) && !_looksGenerative(s)) return true;
    // Very short prompts the planner could not make sense of.
    const words = s.split(/\s+/).filter(Boolean);
    if (words.length <= 3 && !_looksGenerative(s) && !_looksCreative(s) && !_looksRecolor(s) && !_NAMED_LOOK.test(s)) return true;
    return false;
  };

  // Capture the current canvas as a data URL (before/after comparison).
  const _captureShot = async () => {
    try {
      const r = await window.geEditorControl?.invoke('screenshot');
      const res = (r && r.result) ? r.result : r;
      return (res && (res.dataUrl || res)) || null;
    } catch (_) { return null; }
  };

  // VL self-check: ask the agent VL model to compare BEFORE/AFTER and decide
  // done vs retry. The caller enforces a single retry (max 1).
  const _vlJudgeAfter = async (prompt, before, after) => {
    if (!before || !after) return { done: true };
    let ep = '';
    let model = 'Qwen3.5-9B';
    try {
      const info = _getSelectedQuickEditAgentModel();
      if (info && info.endpoint) ep = info.endpoint;
      if (info && info.agent) model = info.agent;
    } catch (_) {}
    const judgePrompt = `You are a strict photo-edit QA judge. You are given two images: the BEFORE image first, then the AFTER image. The user asked: "${prompt}". Reply with EXACTLY ONE word: "FAIL" only if the AFTER image is a clear failure — the requested change is missing, applied to the wrong region, or shows obvious artefacts — otherwise reply "OK". No other words.`;
    const parseJudge = (txt) => {
      const s = String(txt || '');
      if (/\bFAIL\b/i.test(s)) return { done: false, verdict: 'FAIL', retryPrompt: prompt };
      return { done: true, verdict: s.trim().slice(0, 160) || 'OK' };
    };
    const logJudge = (res) => {
      try {
        (window.__selfCheckLog = window.__selfCheckLog || []).push({
          prompt, verdict: res.verdict || (res.done ? 'DONE' : ''), retry: res.retryPrompt || null,
        });
      } catch (_) {}
      return res;
    };
    // Prefer a same-origin proxy (the VL server sends no CORS headers, so a
    // direct cross-origin call from the page fails). HARNESS provides
    // POST /api/editor/self-check {model,prompt,before,after} -> {text}.
    try {
      const r = await fetch('/api/editor/self-check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: judgePrompt, before, after }),
      });
      if (r.ok) {
        const j = await r.json();
        return logJudge(parseJudge((j && (j.text || j.content || j.reply)) || ''));
      }
      if (r.status !== 404 && r.status !== 405) return logJudge({ done: true, verdict: 'proxy-error' });
    } catch (_) { /* proxy not available -> try direct */ }
    const body = {
      model,
      temperature: 0,
      max_tokens: 120,
      messages: [{ role: 'user', content: [
        { type: 'text', text: judgePrompt },
        { type: 'image_url', image_url: { url: before } },
        { type: 'image_url', image_url: { url: after } },
      ] }],
    };
    if (!ep) return { done: true };
    try {
      const resp = await fetch(ep.replace(/\/$/, '') + '/chat/completions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!resp.ok) return { done: true };
      const json = await resp.json();
      const txt = (json && json.choices && json.choices[0] && json.choices[0].message && json.choices[0].message.content) || '';
      return logJudge(parseJudge(txt));
    } catch (_) { return { done: true }; }
  };

  // Run the client's deterministic keyword planner (no server, no model) and
  // apply the resulting ops directly. Returns true when it applied something.
  const _runLocalPlan = async (prompt) => {
    const ai = window.geEditorAI;
    if (!ai || typeof ai.localPlan !== 'function') return false;
    let steps = [];
    try {
      // localPlan returns { reply, steps, planner } (or, in older builds, a
      // bare array) — accept both shapes.
      const planned = ai.localPlan(prompt, { width: state.imgWidth, height: state.imgHeight });
      // A generic fallback plan (localPlan could not match anything) must not
      // be applied — it would mask the request with a meaningless tweak. Let
      // the prompt escalate to the planner/AI instead.
      if (planned && planned.fallback) return false;
      steps = Array.isArray(planned) ? planned : (planned && Array.isArray(planned.steps) ? planned.steps : []);
    } catch (_) {
      steps = [];
    }
    steps = steps.filter((s) => s && s.op);
    if (!steps.length) return false;
    _setAiCommandStatus('Applying edit…', 'running');
    let ok = 0;
    for (const s of steps) {
      try {
        await window.geEditorControl?.invoke(s.op, s.params || {});
        ok += 1;
      } catch (err) {
        console.warn('[ge-ai-command] local step failed', s.op, err);
      }
    }
    _setAiCommandStatus(ok ? '' : 'Nothing to apply.', ok ? '' : 'error');
    return ok > 0;
  };

  // Quick Edit entry point — LOCAL-FIRST. A deterministic request (colour,
  // light, rotate/flip, layers, text, selection, adjust/filter, crop) is done
  // on the client with no round-trip. Only generative or unrecognised prompts
  // go to the server planner / AI model.
  // Send a prompt to the image edit model (generative instruction edit).
  // This is the generative fallback: the model understands free language, so
  // prompts that no deterministic op can express ("add a man", "make the sky
  // purple") go here directly — never to a filter/adjustment planner.
  const _regionSelectionOp = (text) => {
    const t = String(text || '').toLowerCase();
    if (/\b(?:sky|clouds?|sun(?:set|rise)?|moon|stars?|horizon|sunbeams?|night\s*sky|rainbow)\b/.test(t)) return 'selection.sky';
    // Background/scene/location replacement OR "put <subject> on/in <place>":
    // keep the subject and repaint only the background (subject-keeping pipeline).
    if (/\b(?:background|backdrop)\b/.test(t)) return 'selection.background';
    if (/\b(?:change|replace|swap|put|set)\b[^.]*\b(?:scene|setting|location|surroundings|beach|forest|mountains?|ocean|sea|desert|cityscape|street|field|meadow)\b/.test(t)) return 'selection.background';
    if (/\bput\s+(?:her|him|them|it|me|us|the\s+\w+)\s+(?:in|on|into|at)\b/.test(t)) return 'selection.background';
    return null;
  };

  // Plain-language image-generation failures live at module scope
  // (_generationErrorMessage / _GEN_OFFLINE_MSG / _GEN_DISABLED_MSG).

  const _runImageModelEdit = async (prompt, { signal = null } = {}) => {
    if (signal && signal.aborted) return { ok: false, cancelled: true };
    _setAiCommandStatus('Editing the image…', 'running');
    try {
      // One undo group for the whole generative edit (select → fill → deselect).
      let mark = null;
      try { mark = await _runQuickControlOp('history.mark', {}); } catch (_) {}
      // Extend/outpaint to a ratio → grow the canvas and generative-fill the
      // new border (the image model alone cannot change the canvas size).
      const ratioAsk = prompt.match(/\b(\d{1,2})\s*[:\-x]\s*(\d{1,2})\b/);
      if (ratioAsk && /\bextend|\bexpand|outpaint|wider|taller|more\s+(?:sky|room|space)\b/.test(prompt)) {
        const ar = Number(ratioAsk[1]) / Number(ratioAsk[2]);
        let tw = state.imgWidth, th = Math.round(tw / ar);
        if (th < state.imgHeight) { th = state.imgHeight; tw = Math.round(th * ar); }
        await _runQuickControlOp('ai.outpaint', { prompt, ratio: `${ratioAsk[1]}:${ratioAsk[2]}`, size: `${tw}x${th}`, steps: 4 });
        try { if (typeof mark === 'number') await _runQuickControlOp('history.collapse', { mark }); } catch (_) {}
        _setAiCommandStatus('', '');
        return { ok: true };
      }
      // Background/scene replacement has a dedicated server pipeline
      // (subject mask -> repaint the inverse -> harmonise + contact shadow ->
      // paste the subject back), which is better than a masked generative fill.
      const regionOp = _regionSelectionOp(prompt);
      if (regionOp === 'selection.background') {
        await _runQuickControlOp('ai.replaceBackground', { prompt, steps: 4 });
        try { if (typeof mark === 'number') await _runQuickControlOp('history.collapse', { mark }); } catch (_) {}
        _setAiCommandStatus('', '');
        return { ok: true };
      }
      // Other named regions (sky) scope the generative edit so the model paints
      // only that area and the rest of the photo is preserved.
      let scoped = false;
      if (regionOp) {
        try { await _runQuickControlOp(regionOp, {}); } catch (_) {}
        scoped = !!(state.wandMask && state.wandMask.width && state.wandMask.height);
      }
      if (!scoped) await _runQuickControlOp('selection.selectAll', {});
      await _runQuickControlOp('ai.generativeFill', { prompt });
      await _runQuickControlOp('selection.deselect', {});
      try {
        if (typeof mark === 'number') await _runQuickControlOp('history.collapse', { mark });
      } catch (_) {}
      _setAiCommandStatus('', '');
      return { ok: true };
    } catch (err) {
      if (signal && signal.aborted) {
        _setAiCommandStatus('', '');
        return { ok: false, cancelled: true };
      }
      try { await _runQuickControlOp('selection.deselect', {}); } catch (_) {}
      const _genErr = err && err.message ? err.message : String(err);
      _setAiCommandStatus(_isGenOfflineMsg(_genErr) ? _genErr : `Could not generate that edit: ${_genErr}`, 'error');
      return { ok: false, error: _genErr };
    }
  };

  const executeQuickEdit = async (prompt, { forceAi = false, _noSelfCheck = false, _retried = false, signal = null } = {}) => {
    if (signal && signal.aborted) return { ok: false, cancelled: true };
    // Routing mode (chosen in the Quick Edit box):
    //   auto  – deterministic ops first, else the IMAGE MODEL (never a filter planner)
    //   gen   – generate a brand-new image on a new layer
    //   edit  – always the image edit model (skips the deterministic fast path)
    // forceAi (Tab) skips the deterministic fast path and goes to the model.
    const mode = _getQuickEditMode();
    const forceAgent = !forceAi && mode === 'agent';
    const quickOnly = !forceAi && mode === 'quick';
    // Only Auto tries the deterministic client ops first; Gen/Edit and Tab skip them.
    const tryLocal = !forceAi && mode === 'auto';
    // Deterministic crop: a bare "crop"/"trim" with no target ratio means
    // "remove the empty/uniform borders", which we can do exactly on the
    // client — never send it to the AI model.
    const cropText = String(prompt || '');
    const hasRatio = /\b(?:1:1|16:9|9:16|4:5|5:4|4:3|3:2|2:3|21:9|square|portrait|landscape|story|widescreen|banner|profile|instagram|facebook|twitter|youtube)\b/i.test(cropText)
      || /\b\d{2,4}\s*[x×]\s*\d{2,4}\b/i.test(cropText);
    if (tryLocal && /\b(?:crop|trim)\b/i.test(cropText) && !hasRatio && !/\bselection\b|\bselected\b/i.test(cropText)) {
      const b = _contentBounds();
      const full = !b || (b.x <= 0 && b.y <= 0 && b.w >= state.imgWidth && b.h >= state.imgHeight);
      if (full) {
        _setAiCommandStatus('Nothing to trim — the image already fills the canvas.', 'error');
        return { ok: false, blocked: true, message: 'Nothing to trim' };
      }
      _setAiCommandStatus('Trimming empty borders…', 'running');
      try {
        await window.geEditorControl?.invoke('document.crop', { x: b.x, y: b.y, width: b.w, height: b.h });
        _setAiCommandStatus(`Trimmed to ${b.w}×${b.h}.`, 'done');
        return { ok: true };
      } catch (err) {
        _setAiCommandStatus(`Crop failed: ${err && err.message ? err.message : err}`, 'error');
        return { ok: false, error: err && err.message ? err.message : String(err) };
      }
    }
    // Explicit background removal is a deterministic rembg op — never let it
    // reach the generative model, whatever the routing mode. Previously an
    // Image/AI-mode prompt ("remove background") went to the model, so a rembg
    // outage turned into an AI repaint (often a blank canvas) instead of a
    // clear "rembg is unavailable" error.
    if (_looksBackgroundRemoval(prompt)) {
      _setAiCommandStatus('Removing the background…', 'running');
      try {
        const done = await _executeQuickEditLocal(prompt);
        if (done !== false) return { ok: true };
      } catch (err) {
        if (signal && signal.aborted) { _setAiCommandStatus('', ''); return { ok: false, cancelled: true }; }
        const msg = err && err.message ? err.message : String(err);
        _setAiCommandStatus(`Could not remove the background: ${msg}`, 'error');
        return { ok: false, error: msg };
      }
    }
    // Explicit "harmonize / blend the layers" → global re-blend with the image
    // model using a purpose-built prompt. It's a named action, so it runs in
    // every routing mode (unlike a free-form prompt, which Quick mode blocks).
    if (_isHarmonizeIntent(prompt)) {
      _setAiCommandStatus('Harmonizing layers…', 'running');
      return await _runImageModelEdit(_HARMONIZE_PROMPT, { signal });
    }
    // "Gen" routing mode: the user explicitly chose to send this prompt to the
    // image model to GENERATE a brand-new picture (text-to-image), placed as a
    // new layer — not an in-place edit. Run it before any deterministic/local
    // routing so "Gen" always means "generate".
    if (mode === 'gen') {
      _setAiCommandStatus('Generating an image…', 'running');
      try {
        const res = await _runQuickControlOp('ai.generateImage', { prompt });
        _setAiCommandStatus('Generated an image.', 'done');
        return { ok: true, image: res };
      } catch (err) {
        const msg = err && err.message ? err.message : String(err);
        _setAiCommandStatus(_isGenOfflineMsg(msg) ? msg : `Could not generate an image: ${msg}`, 'error');
        return { ok: false, error: msg };
      }
    }
    // "Edit" routing mode: always the image edit model, skipping the
    // deterministic fast path (same as Tab, but sticky).
    if (mode === 'edit' && !forceAi) {
      return await _runImageModelEdit(prompt, { signal });
    }
    // 1) Deterministic client ops (rotate/flip/layers/rembg/upscale/selection/
    //    fill/align/text/…) — fast path, no model call. The helper's contract is
    //    "return false only when nothing matched"; any other return means it
    //    handled the prompt.
    if (tryLocal) {
      const done = await _executeQuickEditLocal(prompt);
      if (done !== false) return { ok: true };
    }
    // 2) Keyword planner (still deterministic, no model) for the long tail:
    //    adjustments, filters, styles, extend-by-format, …  Generative prompts
    //    skip this so a content change ("add a full moon in the sky",
    //    "add wildflowers in the foreground") reaches the model, not a local
    //    region toning.
    if (tryLocal && !_looksGenerative(prompt)) {
      const planned = await _runLocalPlan(prompt);
      if (planned) return { ok: true };
    }
    // Legacy "quick" mode (no longer in the selector): deterministic only.
    if (quickOnly) {
      _setAiCommandStatus('No built-in quick action matched — switch to Auto, Gen or Edit.', 'error');
      return { ok: false, blocked: true, message: 'No quick action matched' };
    }
    // Unrecognised / short prompts are handed to the image model rather than
    // dead-ending with a clarification, so a rough instruction still does
    // something ("just fall back to image edit").
    if (mode === 'auto' && !forceAi && _isVague(prompt)) {
      _setAiCommandStatus('Interpreting with the image model…', 'running');
      return await _runImageModelEdit(prompt, { signal });
    }
    // 3) Agent mode → the planner. Generative/look edits get a VL self-check
    //    (opt-in): after the edit the VL compares before/after and may request
    //    one retry (max 1).
    if (forceAgent) {
      const selfCheckEligible = !_noSelfCheck
        && window.geQuickEditSelfCheck === true
        && (_looksGenerative(prompt) || _looksLook(prompt));
      const beforeShot = selfCheckEligible ? await _captureShot() : null;
      const agent = window.geEditorAgent;
      if (agent) {
        _setAiCommandStatus('Asking the editor agent…', 'running');
        let out = null;
        try {
          out = await agent.run(_enrichLookPrompt(prompt), {
            onStep: (entry) => _setAiCommandStatus(`Step: ${entry.op}${entry.ok ? '' : ' (failed)'}`, 'running'),
            signal,
          });
        } catch (err) {
          // A user cancel (AbortError) must stop here — never fall through to the
          // client planner and silently re-run the edit the user just cancelled.
          if (signal && signal.aborted) {
            _setAiCommandStatus('', '');
            return { ok: false, cancelled: true };
          }
          console.warn('[ge-ai-command] server planner unavailable', err);
        }
        if (out && !out.unavailable) {
          if (out.blocked) {
            const rolled = out.rolledBack ? ` (rolled back ${out.rolledBack} partial edit${out.rolledBack === 1 ? '' : 's'})` : '';
            _setAiCommandStatus(`${out.message || 'Could not complete that edit.'}${rolled}`, 'error');
          } else if (out.ok) {
            _setAiCommandStatus('', '');
            if (selfCheckEligible && !_retried) {
              const afterShot = await _captureShot();
              const judge = await _vlJudgeAfter(prompt, beforeShot, afterShot);
              if (!judge.done) {
                _setAiCommandStatus('Retrying…', 'running');
                const retryOut = await executeQuickEdit(judge.retryPrompt || prompt, { forceAi: true, _noSelfCheck: true, _retried: true, signal });
                if (retryOut && retryOut.ok) return retryOut;
                _setAiCommandStatus('', '');
                return out;
              }
            }
          } else {
            _setAiCommandStatus(out.error || 'Could not complete that edit.', 'error');
          }
          return out;
        }
      }
      // Server planner unreachable — client planner (may hit the model).
      if (signal && signal.aborted) return { ok: false, cancelled: true };
      const ai = window.geEditorAI;
      if (!ai) {
        _setAiCommandStatus('Editor agent unavailable.', 'error');
        return { ok: false, blocked: true, message: 'Editor agent unavailable.' };
      }
      _setAiCommandStatus('Planning edits…', 'running');
      const out = await ai.execute(prompt, {
        onStep: (entry, i, n) => _setAiCommandStatus(`Step ${i}/${n}: ${entry.op}`, 'running'),
      });
      _setAiCommandStatus(
        out.ok ? ''
               : `Stopped at ${out.failed?.op || 'step'}: ${out.failed?.error || 'failed'}`,
        out.ok ? '' : 'error',
      );
      return out;
    }
    // 4) Auto / Image / Tab → the image model (the generative fallback).
    return await _runImageModelEdit(prompt, { signal });
  };

  // Expose the real Quick Edit entry point (the local-first hybrid path) so the
  // benchmark/coverage runners and other callers exercise exactly what the
  // command box does, not the raw server agent.
  try { window.geQuickEditRun = (prompt, opts) => executeQuickEdit(prompt, opts || {}); } catch (_) {}

  // Stacked prompt queue. Prompts run one at a time (the document is a single
  // state), and the stack shows which are queued, running, done or failed —
  // jelly-style notifications so work can be lined up without blocking input.
  const jobs = [];
  let jobSeq = 0;
  let pumping = false;
  const stackEl = document.getElementById('ge-ai-command-stack');

  const jobIcon = (status) => (
    status === 'done' ? '\u2713'
      : status === 'error' ? '!'
        : status === 'running' ? ''
          : '<span class="ge-ai-job-dot" aria-hidden="true"></span>'
  );

  // Transient "jelly" notifications: a prompt shows a row while it is queued and
  // running, then fades out on completion and removes itself — no persistent
  // history to dismiss. Multiple prompts stack under each other.
  const renderJobStack = () => {
    if (!stackEl) return;
    if (!jobs.length) {
      stackEl.hidden = true;
      stackEl.innerHTML = '';
      wrap.classList.remove('ge-ai-command-busy');
      return;
    }
    stackEl.hidden = false;
    jobs.forEach((job, i) => {
      let el = job.el;
      if (!el) {
        el = document.createElement('div');
        el.dataset.aiJob = job.id;
        el.setAttribute('role', 'status');
        el.innerHTML = '<span class="ge-ai-job-icon" aria-hidden="true"></span>'
          + '<span class="ge-ai-job-body"><span class="ge-ai-job-prompt"></span>'
          + '<span class="ge-ai-job-detail"></span></span>'
          + '<button type="button" class="ge-ai-job-cancel" title="Cancel this request" aria-label="Cancel this request" hidden>Cancel</button>';
        job.el = el;
      }
      el.className = `ge-ai-job ge-ai-job-${job.status}${job.leaving ? ' ge-ai-job-leaving' : ''}`;
      const iconEl = el.querySelector('.ge-ai-job-icon');
      iconEl.innerHTML = jobIcon(job.status);
      if (job.status === 'running') {
        if (!job.spinner) {
          job.spinner = spinnerModule.createWhirlpool(16);
          try { job.spinner.start(); } catch (_) {}
        }
        if (iconEl.firstChild !== job.spinner.element) iconEl.appendChild(job.spinner.element);
      } else if (job.spinner) {
        try { job.spinner.destroy(); } catch (_) {}
        job.spinner = null;
      }
      el.querySelector('.ge-ai-job-prompt').textContent = job.prompt;
      const detail = el.querySelector('.ge-ai-job-detail');
      const showDetail = Boolean(job.detail) && job.detail !== job.prompt;
      detail.textContent = showDetail ? job.detail : '';
      detail.hidden = !showDetail;
      const cancelBtn = el.querySelector('.ge-ai-job-cancel');
      if (cancelBtn) cancelBtn.hidden = !(job.status === 'queued' || job.status === 'running');
      if (stackEl.children[i] !== el) stackEl.insertBefore(el, stackEl.children[i] || null);
    });
    Array.from(stackEl.children).forEach((child) => {
      if (!jobs.some(j => j.id === child.dataset.aiJob)) child.remove();
    });
    const running = jobs.some(j => j.status === 'running');
    wrap.classList.toggle('ge-ai-command-busy', running);
    if (runBtn) runBtn.title = running ? 'Editing — new prompts are queued' : 'Run AI edit';
  };

  // Fade a finished notification out, then drop it from the stack.
  const dismissJob = (job) => {
    if (!job || job.leaving) return;
    job.leaving = true;
    renderJobStack();
    setTimeout(() => {
      const idx = jobs.indexOf(job);
      if (job.spinner) { try { job.spinner.destroy(); } catch (_) {} job.spinner = null; }
      if (idx >= 0) jobs.splice(idx, 1);
      renderJobStack();
    }, 360);
  };

  const updateJob = (id, patch) => {
    const job = jobs.find(j => j.id === id);
    if (!job) return;
    Object.assign(job, patch);
    renderJobStack();
  };

  // Cancel a queued or running request: abort the in-flight fetch, drop the row
  // immediately and clear the busy state so the box is free for the next prompt.
  const cancelJob = (job) => {
    if (!job || job.status === 'done' || job.status === 'error') return;
    job.cancelled = true;
    try { job.controller?.abort(); } catch (_) {}
    try { _aiEditAbort?.abort(); } catch (_) {}
    if (_quickEditActiveJob === job) _quickEditActiveJob = null;
    if (job.spinner) { try { job.spinner.destroy(); } catch (_) {} job.spinner = null; }
    const idx = jobs.indexOf(job);
    if (idx >= 0) jobs.splice(idx, 1);
    renderJobStack();
    _setAiCommandStatus('', '');
  };
  if (stackEl) {
    stackEl.addEventListener('click', (e) => {
      const btn = e.target.closest && e.target.closest('.ge-ai-job-cancel');
      if (!btn) return;
      const row = btn.closest('.ge-ai-job');
      const job = jobs.find(j => j.id === (row && row.dataset.aiJob));
      if (job) cancelJob(job);
    });
  }

  const pumpQuickEdit = async () => {
    if (pumping) return;
    pumping = true;
    try {
      for (;;) {
        const job = jobs.find(j => j.status === 'queued');
        if (!job) break;
        job.status = 'running';
        job.detail = 'Working\u2026';
        job.onChange = renderJobStack;
        const controller = (typeof AbortController === 'function') ? new AbortController() : null;
        job.controller = controller;
        renderJobStack();
        _quickEditActiveJob = job;
        try {
          await executeQuickEdit(job.prompt, { forceAi: !!job.forceAi, signal: controller ? controller.signal : null });
        } catch (err) {
          if (!job.cancelled) {
            job.status = 'error';
            job.detail = err?.message || 'AI edit failed';
          }
        } finally {
          if (_quickEditActiveJob === job) _quickEditActiveJob = null;
          job.onChange = null;
          job.controller = null;
          if (job.cancelled) continue;
          if (job.status === 'running') job.status = 'done';
          renderJobStack();
          setTimeout(() => dismissJob(job), job.status === 'error' ? 3200 : 1600);
        }
      }
    } finally {
      pumping = false;
    }
  };

  const enqueueQuickEditJob = (prompt, forceAi = false) => {
    const trimmed = String(prompt || '').trim();
    if (!trimmed) {
      _setAiCommandStatus('Type what you want changed.', 'error');
      input.focus();
      return;
    }
    input.value = '';
    syncClearButton();
    const job = {
      id: `job-${++jobSeq}`, prompt: trimmed, status: 'queued', detail: 'Queued', onChange: null, forceAi,
    };
    jobs.push(job);
    while (jobs.length > 12) jobs.shift();
    renderJobStack();
    // Hand focus back to the document once a prompt is sent: single-key tool
    // shortcuts (V for Move, B for Brush, …) should switch tools instead of
    // typing into the still-focused prompt box. The bar stays open for the
    // next click.
    input.blur();
    pumpQuickEdit();
  };
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    hideSuggestions();
    _pushRecentPrompt(input.value);
    enqueueQuickEditJob(input.value, false);
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
  schedulePersist: () => _schedulePersist(),
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
  return _pickTopmostLayerAt({
    layers: state.layers,
    point,
    isHidden: layer => {
      const parentGroup = (state.layerGroups || []).find(group => (group.layerIds || []).includes(layer.id));
      return !!(parentGroup && _groupAncestors(state, parentGroup, { includeSelf: true }).some(group => group.visible === false));
    },
    // Auto-select only picks position-unlocked layers (Photoshop behaviour).
    isLocked: layer => _isLayerPositionLocked(state, layer),
    getOffset: layer => state.layerOffsets.get(layer.id) || { x: 0, y: 0 },
    sampleAlpha: (layer, x, y) => {
      const rendered = _renderLayerOutput(layer);
      if (!rendered || x >= rendered.width || y >= rendered.height) return null;
      return rendered.getContext('2d', { willReadFrequently: true })
        .getImageData(x, y, 1, 1).data[3];
    },
  });
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
  _openAdjPopup(layer, type, anchorEl || row, layer.adjustment);
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
function _rotateAllLayersArbitrary(deg) { return _canvasTransforms.rotateAllArbitrary(deg); }
function _flipAllLayers(axis)  { return _canvasTransforms.flipAll(axis); }
function _rotateActiveLayer(deg) { return _canvasTransforms.rotateLayer(deg); }
function _flipActiveLayer(axis)  { return _canvasTransforms.flipLayer(axis); }

async function _runQuickControlOp(op, params) {
  const control = window.geEditorControl;
  if (!control || typeof control.invoke !== 'function') throw new Error('editor control unavailable');
  const r = await control.invoke(op, params || {});
  if (!r || !r.ok) throw new Error((r && r.error) || `${op} failed`);
  return r.result;
}

const _QUICK_ADJ_TYPES = [
  [/\bbrightness\b|\bcontrast\b/, 'brightness-contrast'],
  [/\bexposure\b/, 'exposure'],
  [/\bwhite\s*balance\b/, 'white-balance'],
  [/\bhue\b|\bsaturation\b|\bcolorize\b|\bcolourize\b/, 'hue-saturation'],
  [/\bvibrance\b/, 'vibrance'],
  [/\bblack\s*(?:and|&)?\s*white\b|\bgrayscale\b|\bgrey\s*scale\b|\bmonochrome\b/, 'black-white'],
  [/\bshadows?\b|\bhighlights?\b/, 'shadows-highlights'],
  [/\blevels\b/, 'levels'],
  [/\bcurves\b/, 'curves'],
  [/\bcolou?r\s*balance\b/, 'color-balance'],
  [/\bselective\s*colou?r\b/, 'selective-color'],
  [/\bgradient\s*map\b/, 'gradient-map'],
  [/\binvert\b/, 'invert'],
  [/\bposterize\b/, 'posterize'],
  [/\bthreshold\b/, 'threshold'],
  [/\bphoto\s*filter\b/, 'photo-filter'],
  [/\bchannel\s*mixer\b/, 'channel-mixer'],
  [/\bcolou?r\s*lookup\b|\blut\b/, 'color-lookup'],
];
function _detectAdjustmentType(p) {
  for (const [re, type] of _QUICK_ADJ_TYPES) if (re.test(p)) return type;
  if (/\badjustment\b/.test(p)) return 'brightness-contrast';
  return null;
}
function _layerReorderDirection(p) {
  if (/\b(bring|move)\b.*\bto\s*(the\s*)?(top|front)\b|\braise\b|\bto\s*front\b/.test(p)) return 'top';
  if (/\b(send|move)\b.*\bto\s*(the\s*)?(bottom|back)\b|\bto\s*back\b/.test(p)) return 'bottom';
  if (/\bup\b|\bforward\b|\babove\b|\bhigher\b/.test(p)) return 'up';
  if (/\bdown\b|\bbackward\b|\bbelow\b|\blower\b/.test(p)) return 'down';
  return null;
}

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
// Async generations render into this scratch canvas and are copied into
// state.documentCompositeCanvas only when they complete, so an invalidated
// (aborted) render can never leave a half-drawn document on screen.
let _documentBackCanvas = null;

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

function _selectionBounds(mask, docW = 0, docH = 0) {
  if (!mask || !mask.width || !mask.height) return null;
  let ctx;
  try { ctx = mask.getContext('2d'); } catch (_) { return null; }
  let data;
  try { data = ctx.getImageData(0, 0, mask.width, mask.height).data; } catch (_) { return null; }
  let minX = mask.width, minY = mask.height, maxX = -1, maxY = -1;
  for (let y = 0; y < mask.height; y++) {
    const row = y * mask.width;
    for (let x = 0; x < mask.width; x++) {
      if (data[(row + x) * 4 + 3] > 8) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < minX || maxY < minY) return null;
  const sx = docW > 0 ? docW / mask.width : 1;
  const sy = docH > 0 ? docH / mask.height : 1;
  return {
    x: Math.round(minX * sx),
    y: Math.round(minY * sy),
    width: Math.max(1, Math.round((maxX - minX + 1) * sx)),
    height: Math.max(1, Math.round((maxY - minY + 1) * sy)),
  };
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
  const dispW = Math.max(1, Math.round(state.imgWidth * state.zoom));
  const dispH = Math.max(1, Math.round(state.imgHeight * state.zoom));
  if (overlay.width !== dispW) overlay.width = dispW;
  if (overlay.height !== dispH) overlay.height = dispH;
  overlay.style.width = `${dispW}px`;
  overlay.style.height = `${dispH}px`;
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
  _paintSelectionBoundary(ctx, cache.pixels, cache.width, cache.height, _selectionAnimationPhase, state.zoom, cache.flags);
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
    ..._selectionBoundary(mask),
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
  if (state.tool === 'pen' || state.penPoints?.length) _penTool.draw();
  if (state.cropRect && !state.cropping) _drawCropOverlay();
  if (state.cropRect) _syncCropFrame();
  if (state.activeSnapGuides && state.activeSnapGuides.length) _drawSnapGuides();
  if (!state.drawing) _precisionGuides?.drawDocumentOverlay(state.mainCtx);
  _refreshSelectionOverlay();
  if (!state.drawing) _syncToolClearIndicators();
}

function composite() {
  if (!state.mainCtx) return;
  // Interactive direct updates (brush/liquify strokes, layer dragging) render
  // synchronously: every pointer move invalidates the previous worker frame, so
  // an async composite would only ever paint once the gesture ends.
  const asyncComposite = _hasRetainedEffects()
    && !state.drawing
    && !state.moving
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
    if (!_documentBackCanvas) _documentBackCanvas = document.createElement('canvas');
    const back = _documentBackCanvas;
    if (back.width !== documentCanvas.width || back.height !== documentCanvas.height) {
      back.width = documentCanvas.width;
      back.height = documentCanvas.height;
    }
    const copyBack = () => {
      const dctx = documentCanvas.getContext('2d');
      dctx.setTransform(1, 0, 0, 1, 0, 0);
      dctx.clearRect(0, 0, documentCanvas.width, documentCanvas.height);
      dctx.drawImage(back, 0, 0);
      _finishComposite(render, documentCanvas);
    };
    const renderPromise = _drawDocumentLayersAsync(back.getContext('2d'), render.isCurrent)
      .then(() => {
        if (!render.isCurrent()) return;
        copyBack();
      })
      .catch(() => {
        if (!render.isCurrent()) return;
        _drawDocumentLayers(back.getContext('2d'), render.isCurrent);
        copyBack();
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
  state.mainCtx.lineWidth = 1 / state.zoom;
  state.mainCtx.setLineDash([4 / state.zoom, 4 / state.zoom]);
  state.mainCtx.strokeRect(x, y, w, h);
  state.mainCtx.setLineDash([]);
}

// Screen-space dashed frame for the current crop rect. The crop overlay
// above is painted on the image-sized main canvas, so a crop that reaches
// past the image edge would be clipped there. This DOM frame draws the FULL
// rect (including the part over the canvas-area padding) so the user can see
// the region that will be ADDED when the crop expands the canvas.
function _syncCropFrame() {
  const area = state.container?.querySelector('.ge-canvas-area');
  if (!area) return;
  const existing = area.querySelector('.ge-crop-frame');
  if (!state.cropRect || !state.mainCanvas) { if (existing) existing.remove(); return; }
  let el = existing;
  if (!el) { el = document.createElement('div'); el.className = 'ge-crop-frame'; area.appendChild(el); }
  const mRect = state.mainCanvas.getBoundingClientRect();
  const aRect = area.getBoundingClientRect();
  const scaleX = state.mainCanvas.width ? (mRect.width / state.mainCanvas.width) : 1;
  const scaleY = state.mainCanvas.height ? (mRect.height / state.mainCanvas.height) : 1;
  const { x, y, w, h } = state.cropRect;
  el.style.left = (mRect.left - aRect.left + x * scaleX) + 'px';
  el.style.top = (mRect.top - aRect.top + y * scaleY) + 'px';
  el.style.width = Math.max(0, w * scaleX) + 'px';
  el.style.height = Math.max(0, h * scaleY) + 'px';
}

// _drawCheckerboard lives in editor/checkerboard.js — see import at top.

// ── History ──

function _snapshotState(options = {}) {
  // Selection-only changes (pen/marquee/wand) do not touch layer pixels, so
  // reuse the previous snapshot's ImageData instead of re-reading every layer
  // — getImageData is the dominant cost of a pen close on a large canvas.
  const reuse = options.selectionOnly ? state.undoStack.at(-1) : null;
  const prevLayers = new Map((reuse?.layers || []).map(layer => [layer.id, layer]));
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
      const prevLayer = prevLayers.get(l.id);
      let imageData = null;
      if (prevLayer && prevLayer.canvasW === l.canvas.width && prevLayer.canvasH === l.canvas.height && prevLayer.imageData) {
        imageData = prevLayer.imageData;
      } else {
        try {
          if (l.canvas.width > 0 && l.canvas.height > 0) {
            imageData = l.ctx.getImageData(0, 0, l.canvas.width, l.canvas.height);
          }
        } catch (_) { /* keep imageData=null, restore will skip */ }
      }
      let placed = null;
      if (l.kind === 'placed' && l.placed?.sourceCanvas) {
        let sourceImageData = null;
        const prevPlaced = prevLayer?.placed;
        if (prevPlaced && prevPlaced.sourceW === l.placed.sourceCanvas.width
            && prevPlaced.sourceH === l.placed.sourceCanvas.height && prevPlaced.sourceImageData) {
          sourceImageData = prevPlaced.sourceImageData;
        } else {
          try {
            sourceImageData = l.placed.sourceCanvas.getContext('2d').getImageData(
              0, 0, l.placed.sourceCanvas.width, l.placed.sourceCanvas.height,
            );
          } catch {}
        }
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
          const prevMask = prevLayer?.masks?.find(mask => mask.id === m.id);
          if (prevMask && prevMask.canvasW === m.canvas.width && prevMask.canvasH === m.canvas.height && prevMask.imageData) {
            mImageData = prevMask.imageData;
          } else {
            try {
              if (m.canvas.width > 0 && m.canvas.height > 0) {
                mImageData = m.ctx.getImageData(0, 0, m.canvas.width, m.canvas.height);
              }
            } catch (_) {}
          }
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

function _saveState(label, options = {}) {
  // saveState() runs FIRST in every mutating op (import, paste, copy,
  // merge, mask, delete, brush, …). If anything here throws, the whole
  // operation aborts before its real work runs — which silently breaks
  // import ("no layer created") and every layer button. So each step is
  // isolated: a history-snapshot failure must degrade (lose one undo
  // step) rather than kill the user's action.
  try {
    const snap = _shareSnapshotPixels(_snapshotState(options), state.undoStack.at(-1));
    snap._label = label || 'Edit';
    snap._ts = Date.now();
    state.undoStack.push(snap);
    _trimHistoryStack(state.undoStack);
    state.redoStack = [];
  } catch (e) {
    console.error('[gallery] saveState snapshot failed (continuing without this undo step):', e);
  }
  try { _invalidateWandCache(); } catch (e) { console.error('[gallery] invalidateWandCache:', e); }
  try {
    state.documentRevision = (state.documentRevision || 0) + 1;
    state.persistDirty = true;
    _setDraftStatus('Unsaved', 'dirty');
    if (!_draftSavingDisabled()) {
      _schedulePersist();
    }
  } catch (e) { console.error('[gallery] schedulePersist:', e); }
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
  const validStates = ['saved', 'dirty', 'pending', 'saving', 'error', 'paused'];
  let normalizedState = stateName;
  if (!validStates.includes(normalizedState)) {
    if (stateName === 'saved' || label === 'Saved') normalizedState = 'saved';
    else if (stateName === 'error' || /fail|error/i.test(label || '')) normalizedState = 'error';
    else if (stateName === 'paused' || /paused/i.test(label || '')) normalizedState = 'paused';
    else if (stateName === 'saving' || label === 'Saving…' || label === 'Saving...') normalizedState = 'saving';
    else if (stateName === 'pending' || /saving soon/i.test(label || '')) normalizedState = 'pending';
    else normalizedState = 'dirty';
  }
  const defaultLabels = {
    saved: 'Saved',
    dirty: 'Unsaved',
    pending: 'Saving soon…',
    saving: 'Saving…',
    error: 'Save failed',
    paused: 'Save paused',
  };
  const normalizedLabel = label || defaultLabels[normalizedState] || 'Unsaved';
  el.textContent = normalizedLabel;
  el.dataset.state = normalizedState;
  const iconHost = el.closest('#ge-save-menu-btn');
  const iconKind = (normalizedState === 'error' || normalizedState === 'paused')
    ? 'error'
    : (normalizedState === 'saved' ? 'saved' : 'dirty');
  // The icons are <svg>; SVGElement has no `hidden` property, so set the attribute.
  iconHost?.querySelectorAll('.ge-save-state-icon').forEach(icon => {
    icon.toggleAttribute('hidden', !icon.classList.contains(`ge-save-state-${iconKind}`));
  });
  const saveButton = document.getElementById('ge-save-menu-btn');
  if (saveButton) {
    if (normalizedState === 'error' || normalizedState === 'paused') {
      saveButton.title = state.persistErrorMessage || `Draft autosave failed: ${normalizedLabel}`;
      saveButton.setAttribute('aria-label', `Draft autosave failed: ${normalizedLabel}`);
    } else {
      saveButton.title = `Draft status: ${normalizedLabel}`;
      saveButton.setAttribute('aria-label', `Draft status: ${normalizedLabel}`);
    }
  }
}
try { window.__geSetDraftStatus = _setDraftStatus; } catch (_) {}

function _clearActiveEditorSession() {
  try { sessionStorage.removeItem(ACTIVE_EDITOR_SESSION_KEY); } catch (_) {}
}

// Test harnesses must not accumulate recovery drafts in the user's Gallery.
// Set window.__geNoDraft = true or open the page with ?nodraft=1 to disable
// all draft persistence for the session.
function _draftSavingDisabled() {
  try {
    if (typeof window !== 'undefined') {
      if (window.__geNoDraft) return true;
      const search = (window.location && window.location.search) || '';
      if (/(?:^|[?&])nodraft=1(?:&|$)/.test(search)) return true;
    }
  } catch (_) {}
  return false;
}

function _schedulePersist() {
  if (_draftSavingDisabled()) return;
  if (!state.editorOpen || !state.layers.length) return;
  if (state.persistTimer) { clearTimeout(state.persistTimer); state.persistTimer = null; }
  if (state.persistRetryTimer) { clearTimeout(state.persistRetryTimer); state.persistRetryTimer = null; }
  if (state.pendingStatusTimer) { clearTimeout(state.pendingStatusTimer); state.pendingStatusTimer = null; }
  // The first save establishes the recovery anchor. Start it immediately
  // when no server draft exists; subsequent edits can stay debounced.
  const delay = state.draftId ? PERSIST_DEBOUNCE_MS : 0;
  if (delay > 0) {
    state.pendingStatusTimer = setTimeout(() => {
      state.pendingStatusTimer = null;
      if (state.editorOpen) _setDraftStatus('Saving soon…', 'pending');
    }, Math.min(200, Math.floor(delay / 2)));
  } else {
    _setDraftStatus('Saving soon…', 'pending');
  }
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
  if (_draftSavingDisabled()) return;
  if (!state.editorOpen || !state.layers.length) return;
  // Coalesce concurrent saves — if one's already in-flight, mark dirty
  // and let the running call kick off another when it returns.
  if (state.persistInFlight) { state.persistDirty = true; _setDraftStatus('Saving…', 'saving'); return; }
  const currentRevision = state.documentRevision || 0;
  state.persistingRevision = currentRevision;
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
      _setDraftStatus('Save paused', 'error');
      const err = new Error('Draft autosave paused: this layered document exceeds the 256 MB safety limit. Export or flatten a copy before closing.');
      err.status = 413;
      throw err;
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
    .then((out) => {
      if (!isCurrentSession()) return;
      state.savedRevision = Math.max(state.savedRevision || 0, currentRevision);
      state.persistRetryCount = 0;
      if (out && out.id) {
        state.draftId = out.id;
        _writeActiveEditorSession();
      }
      state.persistErrorMessage = null;
      if ((state.documentRevision || 0) > state.savedRevision || state.persistDirty) {
        state.persistDirty = true;
        _setDraftStatus('Unsaved', 'dirty');
      } else {
        _setDraftStatus('Saved', 'saved');
      }
      if (state.persistDirty) {
        state.persistDirty = false;
        _schedulePersist();
      }
    })
    .catch((e) => {
      console.warn('[ge] draft save failed', e);
      if (!isCurrentSession()) return;
      state.persistDirty = true;
      const isPaused = e?.status === 413;
      const message = isPaused
        ? (e?.message || 'Draft autosave paused: the document exceeds the configured storage limit.')
        : `Draft autosave failed: ${e?.message || 'server error'}`;
      if (state.persistErrorMessage !== message && uiModule) uiModule.showToast(message, isPaused ? 9000 : 7000);
      state.persistErrorMessage = message;
      _setDraftStatus(isPaused ? 'Save paused' : 'Save failed', isPaused ? 'paused' : 'error');
      if (!isPaused && state.editorOpen) {
        state.persistRetryCount = Math.min((state.persistRetryCount || 0) + 1, 5);
        const retryDelay = Math.min(1000 * Math.pow(2, state.persistRetryCount), 15000);
        if (state.persistRetryTimer) clearTimeout(state.persistRetryTimer);
        state.persistRetryTimer = setTimeout(() => {
          state.persistRetryTimer = null;
          if (state.editorOpen && (state.persistDirty || (state.documentRevision || 0) > (state.savedRevision || 0))) {
            _persistDraft();
          }
        }, retryDelay);
      }
    })
    .finally(() => {
      if (isCurrentSession()) state.persistInFlight = null;
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
  state.colorMode = data.colorMode === 'grayscale' ? 'grayscale' : 'rgb';
  state.bitDepth = 8;
  state.layers = [];
  state.layerOffsets.clear();
  Object.assign(state, _normalizeEditorView(data.view, {
    rulersVisible: state.rulersVisible,
    gridVisible: state.gridVisible,
    gridSize: state.gridSize,
    snapEnabled: state.snapEnabled,
    snapToGrid: state.snapToGrid,
    autoSelectLayer: state.autoSelectLayer,
    autoSelectTarget: state.autoSelectTarget,
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
    // Restore the retained FX list (and drop any live preview) so undoing an
    // "add effect" step actually removes it. The rebuild path used to leave
    // layer.effects untouched, so a freshly added gradient survived Ctrl+Z.
    layer.effects = Array.isArray(s.effects)
      ? s.effects.map(effect => _normalizeEffect(effect))
      : [];
    layer._effectPreview = null;
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
  if (sizeLabel) sizeLabel.textContent = `${state.imgWidth}×${state.imgHeight} px`;
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
let _historySnapshotSeq = 0;

// Take a named snapshot: an independent labelled copy of the document that
// survives later edits (and undo-stack trimming) and can be restored later,
// mirroring Photoshop's History-panel snapshots.
function _takeHistorySnapshot(label) {
  const snap = _shareSnapshotPixels(_snapshotState(), state.undoStack.at(-1) || null);
  snap._label = label || ('Snapshot ' + (state.historySnapshots.length + 1));
  snap._ts = Date.now();
  snap._snapshotId = 'snap-' + (++_historySnapshotSeq);
  if (!Array.isArray(state.historySnapshots)) state.historySnapshots = [];
  state.historySnapshots.push(snap);
  while (state.historySnapshots.length > 20) state.historySnapshots.shift();
  _refreshHistoryPanelIfOpen();
  return { id: snap._snapshotId, label: snap._label, ts: snap._ts };
}

// Restore a named snapshot. The pre-restore state is pushed onto the undo
// stack first, so a restore is itself undoable.
function _restoreHistorySnapshot(id) {
  const snap = (state.historySnapshots || []).find(s => s._snapshotId === id);
  if (!snap) throw new Error('snapshot not found');
  try {
    const cur = _shareSnapshotPixels(_snapshotState(), state.undoStack.at(-1) || null);
    cur._label = 'Before snapshot: ' + (snap._label || 'Snapshot');
    cur._ts = Date.now();
    state.undoStack.push(cur);
    _trimHistoryStack(state.undoStack);
    state.redoStack = [];
  } catch (e) { console.error('[gallery] snapshot restore push:', e); }
  _restoreState(snap);
  _schedulePersist();
  _refreshHistoryPanelIfOpen();
  return true;
}

function _deleteHistorySnapshot(id) {
  state.historySnapshots = (state.historySnapshots || []).filter(s => s._snapshotId !== id);
  _refreshHistoryPanelIfOpen();
  return true;
}

function _listHistorySnapshots() {
  return (state.historySnapshots || []).map(s => ({ id: s._snapshotId, label: s._label, ts: s._ts }));
}

const _historyPanel = createHistoryPanel({
  undo,
  redo,
  takeSnapshot: (label) => _takeHistorySnapshot(label),
  restoreSnapshot: (id) => _restoreHistorySnapshot(id),
  deleteSnapshot: (id) => _deleteHistorySnapshot(id),
  listSnapshots: () => _listHistorySnapshots(),
});
const _jumpToHistory             = _historyPanel.jumpToHistory;
const _toggleHistoryPanel        = _historyPanel.toggleHistoryPanel;
const _refreshHistoryPanelIfOpen = _historyPanel.refreshHistoryPanelIfOpen;

// _relTime lives in editor/layer-helpers.js.

// ── Canvas event helpers ──

// _canvasCoords lives in editor/canvas-coords.js — see import at top.

// ── Drawing ──

let _rasterizePromptPending = false;
const _pixelPaintTools = new Set(['brush', 'pixel', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'gradient', 'bucket', 'liquify']);

async function _offerRasterizeForTool(tool = state.tool) {
  const layer = activeLayer() || _activeParentLayer();
  if (!_pixelPaintTools.has(tool) || _getActiveMaskLayer() || state.quickMaskActive ||
      !['text', 'shape', 'placed'].includes(layer?.kind)) return false;
  return _confirmRasterizeLayer(layer);
}

// Rasterize a text/shape/placed layer in place without prompting. The change is
// recorded in history, so it stays undoable. Used by tools (the bucket) that
// should paint in a single click instead of bouncing the user through a dialog.
function _rasterizeLayerNow(layer) {
  if (!layer || _isLayerPixelLocked(state, layer)) return false;
  if (!state.layers.includes(layer) || (activeLayer() || _activeParentLayer()) !== layer) return false;
  _saveState(`Rasterize "${layer.name}"`);
  _rasterizeTextLayer(layer);
  _rasterizeShapeLayer(layer);
  _rasterizePlacedLayer(layer);
  composite();
  _renderLayerPanel();
  _schedulePersist();
  return true;
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

let _lastCursorRect = null;
let _lastCursorRectTime = 0;

function _invalidateCoordsCaches() {
  invalidateCanvasCoordsCache();
  _lastCursorRect = null;
  _lastCursorRectTime = 0;
}

function _beginDraw(e) {
  if (state.mainCanvas) {
    // Prime canvas rect and cursor rect once before stroke begins
    // to avoid synchronous layout reads during pointermove.
    _canvasCoords(e, state.mainCanvas);
    const now = performance.now();
    _lastCursorRect = state.mainCanvas.getBoundingClientRect();
    _lastCursorRectTime = now;
  }
  // Move-tool auto-select (Photoshop parity): when the Auto-Select option is
  // on — or Ctrl/Cmd is held for a one-off — pick the topmost visible,
  // position-unlocked layer under the pointer before the drag. If the picked
  // layer is already in the current selection we leave the selection alone so
  // dragging a multi-layer / group selection moves every selected layer.
  if (state.tool === 'move') {
    const autoOn = !!state.autoSelectLayer || e.ctrlKey || e.metaKey;
    if (autoOn) {
      const picked = _pickLayerAtEvent(e);
      if (picked) {
        if (state.autoSelectTarget === 'group') {
          const direct = _groupForLayer(state, picked.id);
          const ancestors = direct ? _groupAncestors(state, direct, { includeSelf: true }) : [];
          const topGroup = ancestors.length ? ancestors[ancestors.length - 1] : null;
          if (topGroup) {
            state.activeGroupId = topGroup.id;
            state.activeLayerId = picked.id;
            state.selectedLayerIds = _allLayerIdsInGroup(state, topGroup);
            _renderLayerPanel();
            _syncToolClearIndicators();
          }
        } else {
          const alreadySelected = (state.selectedLayerIds || []).includes(picked.id);
          if (!alreadySelected && picked.id !== state.activeLayerId) {
            state.activeLayerId = picked.id;
            state.selectedLayerIds = [picked.id];
            state.activeGroupId = null;
            _renderLayerPanel();
            _syncToolClearIndicators();
          }
        }
      }
    }
  }
  // Fall back to the parent resolver so a stale activeLayerId doesn't
  // block strokes when there ARE layers present.
  const layer = activeLayer() || _activeParentLayer();
  // Photoshop parity: hold Alt with a paint tool to borrow the eyedropper — the
  // press samples a colour instead of painting.
  if (e.altKey && _altEyedropperTools.has(state.tool)) {
    _setCursorLoupe(true, e);
    _eyedropperTool.pick(e);
    return;
  }
  if (_pixelPaintTools.has(state.tool) && !_getActiveMaskLayer() && !state.quickMaskActive &&
      ['text', 'shape', 'placed'].includes(layer?.kind)) {
    // Paint tools need pixels: prompt for confirmation before destructive rasterization.
    // If user cancels, editable text/shape/placed layer is preserved intact.
    // If user accepts, layer is converted with an undo boundary so it can be reverted.
    void _confirmRasterizeLayer(layer);
    return;
  }
  // Transform-tool drag (handle grab or move-fallback) — handler in
  // editor/tools/transform-drag.js.
  if (_transformDragTool.tryBegin(e)) return;
  // Magic wand is selection-only — works even on locked layers because
  // it doesn't mutate the layer until an action (Erase/Copy) is taken.
  // Full implementation in editor/tools/wand.js.
  if (state.tool === 'zoom') return _zoomToolClick(e);
  if (state.tool === 'wand') return _wandTool.click(e);
  if (state.tool === 'sam') return _runSamSelection(e);
  if (state.tool === 'text') return _placeText(e);
  if (state.tool === 'shape') return _beginShape(e);
  if (state.tool === 'eyedropper') { _setCursorLoupe(true, e); return _eyedropperTool.pick(e); }
  if (state.tool === 'gradient') return _gradientTool.begin(e);
  if (state.tool === 'marquee') return _marqueeTool.begin(e);
  if (state.tool === 'pen') return _penTool.begin(e);
  if (state.tool === 'path') return _pathTool.begin(e);
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
  if (state.tool === 'bucket') return _bucketTool.fill(e);
  if (state.tool === 'lasso') return _beginLasso(e);
  if (state.tool === 'quickselect') return _quickSelectTool.begin(e);
  if (state.tool === 'objectselect') return _objectSelectTool.begin(e);
  if (state.tool === 'patch') return _patchTool.begin(e);
  // Clone-stamp — source pick + stroke start. Full implementation in
  // editor/tools/clone.js; per-sample stamping continues through the
  // shared `_strokeTo` pipeline below.
  if (state.tool === 'clone' || state.tool === 'heal') return _cloneTool.begin(e);
  if (state.tool === 'liquify') return _liquifyTool.begin(e);
  // Brush / Eraser / Inpaint share a stroke pipeline — handler in
  // editor/tools/stroke.js. Returns true for those tools, false
  // otherwise (any other tool that reached here is a no-op).
  _strokeTool.tryBegin(e);
}

let _cursorRaf = null;
let _pendingCursorEvent = null;
const _cursorOverlayTools = new Set([
  'eraser', 'pixel', 'inpaint', 'lasso', 'brush', 'clone', 'heal',
  'smudge', 'dodge', 'burn', 'liquify', 'quickselect', 'objectselect'
]);

function _continueDraw(e) {
  if (state.tool === 'pen') return _penTool.drag(e);
  if (state.tool === 'path') return _pathTool.drag(e);
  if (state.tool === 'liquify') {
    // Liquify hides the native cursor, so keep the size circle following the
    // pointer here; the early return below would otherwise skip the hover pass.
    const overCanvas = state.mainCanvas && e.target === state.mainCanvas;
    if (overCanvas) _updateBrushCursor(e);
    else if (state.cursorEl) state.cursorEl.style.display = 'none';
    return _liquifyTool.drag(e);
  }
  // _continueDraw is now bound to the window so drags can extend past
  // the canvas. The brush-cursor overlay should only follow the cursor
  // when it's actually over the canvas, otherwise hide it.
  const overCanvas = state.mainCanvas && e.target === state.mainCanvas;
  if (state.tool === 'eyedropper' && overCanvas) _eyedropperTool.preview(e);
  if (_cursorOverlayTools.has(state.tool) && state.mainCanvas) {
    const syncCursor = () => {
      if (!overCanvas) {
        if (state.cursorEl) state.cursorEl.style.display = 'none';
        return;
      }
      if (state.drawing) {
        _pendingCursorEvent = e;
        if (!_cursorRaf) {
          _cursorRaf = requestAnimationFrame(() => {
            _cursorRaf = null;
            if (_pendingCursorEvent) {
              _updateBrushCursor(_pendingCursorEvent);
              _pendingCursorEvent = null;
            }
          });
        }
      } else {
        _updateBrushCursor(e);
      }
    };
    // Mid-stroke, move the cursor only after the stroke has sampled the canvas
    // rect: a style write first forces a synchronous style + layout pass of the
    // whole editor on every pointer move, which made long strokes lag.
    if (state.drawing) {
      try { return _continueDrawTools(e); } finally { syncCursor(); }
    }
    syncCursor();
  }
  return _continueDrawTools(e);
}

function _continueDrawTools(e) {
  if (state.drawing) return _strokeTool.tryContinue(e);
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
  if (state.tool === 'quickselect') return _quickSelectTool.drag(e);
  if (state.tool === 'objectselect') return _objectSelectTool.drag(e);
  if (state.tool === 'patch') return _patchTool.drag(e);
  if (state.moving) return _continueMove(e);
  if (state.cropping || state.cropMoving) return _continueCrop(e);
}

function _endDraw(e) {
  if (_cursorRaf) { cancelAnimationFrame(_cursorRaf); _cursorRaf = null; }
  if (_pendingCursorEvent) { _updateBrushCursor(_pendingCursorEvent); _pendingCursorEvent = null; }
  if (_cursorLoupeVisible) _setCursorLoupe(false);
  if (state.tool === 'pen') return _penTool.end();
  if (state.tool === 'path') return _pathTool.end(e);
  if (state.tool === 'liquify') return _liquifyTool.end(e);
  // Transform-tool drag end — handler in editor/tools/transform-drag.js.
  if (_transformDragTool.tryEnd(e)) return;
  if (_shapeDraft) return _endShape(e);
  if (state.gradientActive) return _gradientTool.end(e);
  if (state.marqueeActive || state.selectionMoving) return _marqueeTool.end(e);
  if (state.lassoActive) return _endLasso();
  if (state.tool === 'quickselect') return _quickSelectTool.end(e);
  if (state.tool === 'objectselect') return _objectSelectTool.end(e);
  if (state.tool === 'patch') return _patchTool.end(e);
  if (state.moving) return _endMove();
  if (state.cropping || state.cropMoving) return _endCrop(e);
  // Stroke end (brush / eraser / inpaint / clone) — handler in
  // editor/tools/stroke.js.
  _strokeTool.tryEnd(e);
  _invalidateCoordsCaches();
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
  const now = performance.now();
  let rect;
  if (_lastCursorRect && (state.drawing || (now - _lastCursorRectTime < 32))) {
    rect = _lastCursorRect;
  } else {
    rect = state.mainCanvas.getBoundingClientRect();
    _lastCursorRect = rect;
    _lastCursorRectTime = now;
  }
  const clientX = e.touches ? e.touches[0].clientX : e.clientX;
  const clientY = e.touches ? e.touches[0].clientY : e.clientY;

  if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;

  if (!state.cursorEl) {
    let layer = document.getElementById('ge-cursor-layer');
    if (!layer) {
      layer = document.createElement('div');
      layer.id = 'ge-cursor-layer';
      layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;contain:strict;z-index:2147483647;';
      document.body.appendChild(layer);
    }
    state.cursorEl = document.createElement('div');
    state.cursorEl.className = 'ge-brush-cursor';
    layer.appendChild(state.cursorEl);
  }

  // Lasso uses the feather radius (or a sensible default) so the circle
  // shows the area that will be selected. Other tools use the brush size.
  let basePx;
  if (state.tool === 'lasso') {
    const f = parseInt(document.getElementById('ge-lasso-feather')?.value || '0');
    basePx = Math.max(10, f * 2);
  } else if (state.tool === 'liquify') {
    basePx = Number(state.liquifySize) || 120;
  } else {
    basePx = state.brushSize;
  }
  // The pixel brush draws hard integer squares, so show a square cursor and
  // snap the preview to whole pixels.
  const pixelBrush = state.tool === 'pixel' || (!!state.brushPixel && (state.tool === 'brush' || state.tool === 'eraser'));
  if (pixelBrush) basePx = Math.max(1, Math.round(basePx));
  // Use the canvas's real rendered scale. This stays accurate when fit-to-view
  // or responsive layout changes the canvas independently of nominal zoom.
  const diameterX = basePx * (rect.width / Math.max(1, state.mainCanvas.width));
  const diameterY = basePx * (rect.height / Math.max(1, state.mainCanvas.height));
  if (state._lastCursorDiameterX !== diameterX || state._lastCursorDiameterY !== diameterY) {
    state._lastCursorDiameterX = diameterX;
    state._lastCursorDiameterY = diameterY;
    state.cursorEl.style.width = diameterX + 'px';
    state.cursorEl.style.height = diameterY + 'px';
  }
  state.cursorEl.classList.toggle('ge-brush-cursor--pixel', pixelBrush);
  // Updating an inline style on every move invalidates document-wide :has()
  // selectors. A paused animation keeps the same transform in the rendering
  // pipeline without mutating the cursor's style attribute on every frame.
  const cursorTransform = `translate3d(${clientX - diameterX / 2}px, ${clientY - diameterY / 2}px, 0)`;
  if (!state.cursorEl._positionAnimation || state.cursorEl._positionAnimation.playState === 'idle') {
    state.cursorEl._positionAnimation = state.cursorEl.animate(
      [{ transform: cursorTransform }, { transform: cursorTransform }],
      { duration: 1, fill: 'both' },
    );
    state.cursorEl._positionAnimation.pause();
    state.cursorEl._positionAnimation.currentTime = 0;
  } else {
    state.cursorEl._positionAnimation.effect.setKeyframes([{ transform: cursorTransform }, { transform: cursorTransform }]);
  }
  if (state.cursorEl.style.display !== 'block') {
    state.cursorEl.style.display = 'block';
  }
  state.cursorClientX = clientX;
  state.cursorClientY = clientY;
  if (!state.drawing) {
    state.cursorEl.dataset.clientX = String(clientX);
    state.cursorEl.dataset.clientY = String(clientY);
  }
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
    const borderColor = state.tool === 'eraser' ? 'rgba(255,255,255,0.6)' : state.color;
    if (state._lastCursorBorderColor !== borderColor) {
      state._lastCursorBorderColor = borderColor;
      state.cursorEl.style.borderColor = borderColor;
      state.cursorEl.style.background = 'transparent';
      state.cursorEl.style.borderStyle = 'solid';
    }
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
  syncCropFrame: () => _syncCropFrame(),
});
const _beginCrop    = _cropTool.begin;
const _continueCrop = _cropTool.drag;
const _endCrop      = _cropTool.end;
function _cancelCrop(reason = 'cancel') {
  if (!state.cropRect && !state.cropping && !state.cropMoving) return false;
  // Remove the crop entirely (same as the Apply-panel ×). cancel() only aborts
  // an in-flight gesture and restores the previous rect, so it is a no-op once
  // the draw gesture has committed — reset() always clears the rect.
  _cropTool.reset();
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
    <button type="button" class="ge-crop-close" title="Cancel crop" aria-label="Cancel crop"><span class="ge-crop-close-x" aria-hidden="true">&times;</span></button>
    <select class="ge-crop-size-preset" title="Standard crop size" aria-label="Standard crop size">
      <option value="custom">Custom</option>
      <option value="1080x1080">1080 × 1080</option>
      <option value="1080x1350">1080 × 1350</option>
      <option value="1080x1920">1080 × 1920</option>
      <option value="1200x630">1200 × 630</option>
      <option value="1280x720">1280 × 720</option>
      <option value="1920x1080">1920 × 1080</option>
      <option value="2048x1536">2048 × 1536</option>
      <option value="3000x2400">3000 × 2400</option>
      <option value="4000x3000">4000 × 3000</option>
      <option value="original">Original</option>
    </select>
    <input type="number" class="ge-crop-w" min="1" max="20000" value="${Math.round(state.cropRect.w)}" title="Width">
    <span class="ge-crop-x">×</span>
    <input type="number" class="ge-crop-h" min="1" max="20000" value="${Math.round(state.cropRect.h)}" title="Height">
    <button class="ge-crop-apply-btn">Apply</button>
  `;
  const area = state.container.querySelector('.ge-canvas-area');
  if (!area || !state.cropRect || !state.mainCanvas) return;
  area.appendChild(pop);

  // Full teardown, not cancel(): the draw gesture already committed in
  // end(), so gesture.cancel() is a no-op and would leave the rect behind.
  pop.querySelector('.ge-crop-close').addEventListener('click', () => _cropTool.reset());
  pop.querySelector('.ge-crop-apply-btn').addEventListener('click', () => _applyCrop());
  // Editing W/H updates the crop rect anchored at its top-left so the
  // user sees the dimensions live in the overlay.
  const wInput = pop.querySelector('.ge-crop-w');
  const hInput = pop.querySelector('.ge-crop-h');
  const sizeSel = pop.querySelector('.ge-crop-size-preset');
  const onSize = () => {
    if (!state.cropRect) return;
    const w = Math.max(1, parseInt(wInput.value, 10) || state.cropRect.w);
    const h = Math.max(1, parseInt(hInput.value, 10) || state.cropRect.h);
    state.cropRect = { ...state.cropRect, w, h };
    if (sizeSel) sizeSel.value = 'custom';
    composite();
  };
  wInput.addEventListener('input', onSize);
  hInput.addEventListener('input', onSize);
  // Standard-size dropdown: sets the crop rect to a fixed pixel size (or the
  // original image size), anchored at the rect's top-left. Choosing a size
  // that exceeds the image is fine — Apply expands the canvas there.
  sizeSel?.addEventListener('change', () => {
    if (!state.cropRect) return;
    const v = sizeSel.value;
    let w;
    let h;
    if (v === 'original') { w = state.imgWidth; h = state.imgHeight; }
    else {
      const m = /^(\d+)x(\d+)$/.exec(v);
      if (!m) return;
      w = Number(m[1]); h = Number(m[2]);
    }
    state.cropRect = { ...state.cropRect, w, h };
    state.cropStart = { x: state.cropRect.x, y: state.cropRect.y };
    state.cropEnd = { x: state.cropRect.x + w, y: state.cropRect.y + h };
    wInput.value = Math.round(w);
    hInput.value = Math.round(h);
    composite();
  });
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
  setValue('ge-text-direction', text.vertical ? 'vertical' : 'horizontal');
  setValue('ge-text-warp', text.warp);
  setValue('ge-text-warp-amount', text.warpAmount);
  const smallCapsInput = document.getElementById('ge-text-small-caps');
  if (smallCapsInput) smallCapsInput.checked = !!text.smallCaps;
  const kerningInput = document.getElementById('ge-text-kerning');
  if (kerningInput) kerningInput.checked = text.kerning !== false;
  const ligaturesInput = document.getElementById('ge-text-ligatures');
  if (ligaturesInput) ligaturesInput.checked = text.ligatures !== false;
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
    vertical: document.getElementById('ge-text-direction')?.value === 'vertical',
    warp: document.getElementById('ge-text-warp')?.value || base.warp,
    warpAmount: document.getElementById('ge-text-warp-amount')?.value ?? base.warpAmount,
    smallCaps: document.getElementById('ge-text-small-caps')?.checked === true,
    kerning: document.getElementById('ge-text-kerning')?.checked !== false,
    ligatures: document.getElementById('ge-text-ligatures')?.checked !== false,
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

/* Text on a path — copy a shape layer's path points into the text layer (in the
   text canvas's own local frame), offset the layer so the glyphs sit exactly
   where the path was drawn, then re-render. */
function _attachTextToPath(textLayer, pathLayer) {
  if (!textLayer || textLayer.kind !== 'text' || !textLayer.text) return null;
  const shape = pathLayer && pathLayer.kind === 'shape' ? pathLayer.shape : null;
  const raw = shape && shape.type === 'path' && Array.isArray(shape.pathPoints) ? shape.pathPoints : null;
  if (!raw || raw.length < 2) return null;
  const pad = _pathTextPadding(textLayer.text);
  const xs = raw.map((point) => Number(point.x) || 0);
  const ys = raw.map((point) => Number(point.y) || 0);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const local = raw.map((point) => ({ x: (Number(point.x) || 0) - minX + pad, y: (Number(point.y) || 0) - minY + pad }));
  textLayer.text = _normalizeTextData({ ...textLayer.text, path: local });
  const pathOffset = state.layerOffsets.get(pathLayer.id) || { x: 0, y: 0 };
  state.layerOffsets.set(textLayer.id, { x: Math.round(pathOffset.x + minX - pad), y: Math.round(pathOffset.y + minY - pad) });
  _renderTextLayer(textLayer);
  return { id: textLayer.id, pathLayerId: pathLayer.id, points: local.length };
}

function _detachTextFromPath(textLayer) {
  if (!textLayer || textLayer.kind !== 'text' || !textLayer.text) return false;
  if (!textLayer.text.path) return false;
  const offset = state.layerOffsets.get(textLayer.id) || { x: 0, y: 0 };
  const center = { x: offset.x + textLayer.canvas.width / 2, y: offset.y + textLayer.canvas.height / 2 };
  textLayer.text = _normalizeTextData({ ...textLayer.text, path: null });
  _renderTextLayer(textLayer);
  state.layerOffsets.set(textLayer.id, { x: Math.round(center.x - textLayer.canvas.width / 2), y: Math.round(center.y - textLayer.canvas.height / 2) });
  return true;
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
  // Text fonts are self-hosted web fonts; drop any option the browser cannot
  // actually render (e.g. optional system extras on Android) and re-rasterize
  // existing text layers once the webfonts finish loading so the selected face
  // is applied on every device instead of a shared fallback.
  const _fontSel = section.querySelector('#ge-text-font');
  if (_fontSel) {
    _loadEditorFonts().then(() => {
      const allow = new Set(_availableEditorFonts());
      Array.from(_fontSel.options).forEach((opt) => { if (!allow.has(opt.value)) opt.remove(); });
      if (!_fontSel.options.length) _availableEditorFonts().forEach((f) => _fontSel.appendChild(new Option(f, f)));
      let redrew = false;
      (state.layers || []).forEach((layer) => { if (layer && layer.kind === 'text') { _renderTextLayer(layer); redrew = true; } });
      if (redrew) { try { composite(); } catch (_) {} }
      _syncTextControls();
    }).catch(() => {});
  }
  const editable = section.querySelectorAll('textarea, select, input[type="number"], input[type="range"], .ge-color-picker, .ge-text-opentype-row input[type="checkbox"]');
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
  // A tool's "More" popover sits above the strip; Escape closes it first.
  const openMore = document.querySelector('.ge-opt-more:not([hidden])');
  if (openMore) {
    openMore.hidden = true;
    document.querySelectorAll('.ge-more-toggle').forEach((btn) => {
      btn.setAttribute('aria-expanded', 'false');
      btn.classList.remove('active');
    });
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
  syncSelectionUi: () => _syncToolClearIndicators(),
  // Repaint just the pen overlay while a path is being drawn instead of
  // re-compositing the whole document on every click/drag.
  redraw: () => _penTool.draw(),
  // Closing a path only changes the selection mask; refreshing the ants is
  // enough and avoids a full document composite on big canvases.
  refreshSelection: () => _refreshSelectionOverlay(),
});

// Liquify warp brush — warps the active layer's pixels in place. One undo
// snapshot per stroke; the whole doc is composited live while dragging.
const _liquifyTool = createLiquifyTool({
  activeLayer,
  saveState: _saveState,
  composite,
});

// Vector pen / path tool — creates a shape layer of type 'path' via a
// click-polyline. Distinct from the pen-SELECTION tool above.
function _pathStyle() {
  const fill = document.getElementById('ge-path-fill');
  const stroke = document.getElementById('ge-path-stroke');
  const width = document.getElementById('ge-path-stroke-width');
  const closed = document.getElementById('ge-path-closed');
  const strokeWidth = Number(width?.value);
  return {
    fillColor: fill?.value || state.color || '#ffffff',
    strokeColor: stroke?.value || '#111111',
    strokeWidth: Number.isFinite(strokeWidth) ? strokeWidth : 2,
    closed: closed ? closed.checked : true,
  };
}

function _ensurePathLayer() {
  const layer = createLayer('Path', 1, 1);
  const style = _pathStyle();
  layer.kind = 'shape';
  layer.shape = _normalizeShapeData({
    type: 'path',
    pathPoints: [{ x: 0, y: 0 }],
    closed: style.closed,
    fillColor: style.fillColor,
    strokeColor: style.strokeColor,
    strokeWidth: style.strokeWidth,
  });
  state.layers.push(layer);
  state.layerOffsets.set(layer.id, { x: 0, y: 0 });
  state.activeLayerId = layer.id;
  state.selectedLayerIds = [layer.id];
  _renderLayerPanel();
  return layer;
}

function _drawPathDraft(layer, points) {
  if (!layer || !Array.isArray(points) || !points.length) return;
  const xs = points.map(point => point.x);
  const ys = points.map(point => point.y);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const style = _pathStyle();
  layer.shape = _normalizeShapeData({
    ...layer.shape,
    type: 'path',
    pathPoints: points.map(point => ({ x: point.x - minX, y: point.y - minY })),
    width: Math.max(1, Math.max(...xs) - minX),
    height: Math.max(1, Math.max(...ys) - minY),
    closed: style.closed,
    fillColor: style.fillColor,
    strokeColor: style.strokeColor,
    strokeWidth: style.strokeWidth,
  });
  _renderShapeLayer(layer);
  state.layerOffsets.set(layer.id, { x: minX, y: minY });
  _renderLayerPanel();
}

function _commitPathDraft() {
  _renderLayerPanel();
  _schedulePersist();
  if (uiModule?.showToast) uiModule.showToast('Path created');
}

function _cancelPathDraft(layer) {
  if (!layer) return;
  const index = state.layers.indexOf(layer);
  if (index >= 0) state.layers.splice(index, 1);
  state.layerOffsets.delete(layer.id);
  state.selectedLayerIds = (state.selectedLayerIds || []).filter(id => id !== layer.id);
  if (state.activeLayerId === layer.id) {
    state.activeLayerId = state.layers.length ? state.layers[state.layers.length - 1].id : null;
  }
  _renderLayerPanel();
}

function _blendKeyFor(blend) {
  const map = {
    'source-over': 'norm', multiply: 'mul ', screen: 'scrn', overlay: 'over',
    darken: 'dark', lighten: 'lite', 'color-dodge': 'div ', 'color-burn': 'idiv',
    'hard-light': 'hLit', 'soft-light': 'sLit', difference: 'diff', exclusion: 'smud',
    hue: 'hue ', saturation: 'sat ', color: 'colr', luminosity: 'lum ',
  };
  return map[blend] || 'norm';
}

function _layeredPsdPayload() {
  if (!state.layers?.length) return null;
  const descriptors = [];
  for (const layer of state.layers) {
    if (!layer.canvas) continue;
    if (layer.kind === 'text') _renderTextLayer(layer);
    else if (layer.kind === 'shape') _renderShapeLayer(layer);
    const ctx = layer.canvas.getContext('2d');
    const data = ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height).data;
    const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    descriptors.push({
      name: layer.name || 'Layer',
      x: Math.round(offset.x),
      y: Math.round(offset.y),
      width: layer.canvas.width,
      height: layer.canvas.height,
      data,
      opacity: Number.isFinite(layer.opacity) ? layer.opacity : 1,
      visible: layer.visible !== false,
      blendKey: _blendKeyFor(layer.blendMode),
    });
  }
  const psb = state.imgWidth > 30000 || state.imgHeight > 30000;
  const bytes = _encodeLayeredPsd({ width: state.imgWidth, height: state.imgHeight, layers: descriptors, psb });
  const blob = new Blob([bytes], { type: 'image/vnd.adobe.photoshop' });
  const filename = psb ? 'edited-image.psb' : 'edited-image.psd';
  // Base64 (for callers that want the bytes back, e.g. the editor agent API).
  let dataUrl = null;
  try {
    if (bytes.length <= 16 * 1024 * 1024) {
      let bin = '';
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
      }
      dataUrl = 'data:image/vnd.adobe.photoshop;base64,' + btoa(bin);
    }
  } catch (_) { dataUrl = null; }
  return { bytes, blob, filename, psb, dataUrl, layerNames: descriptors.map((d) => d.name) };
}

function _exportLayeredPsd() {
  const payload = _layeredPsdPayload();
  if (!payload) return null;
  const url = URL.createObjectURL(payload.blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = payload.filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (uiModule?.showToast) uiModule.showToast(payload.psb ? 'Exported layered PSB' : 'Exported layered PSD');
  return payload;
}

let _pathControlsWired = false;
function _wirePathControls() {
  if (_pathControlsWired) return;
  _pathControlsWired = true;
  document.getElementById('ge-path-finish')?.addEventListener('click', () => _pathTool.finish());
  document.getElementById('ge-path-cancel')?.addEventListener('click', () => _pathTool.cancel());
}

const _pathTool = createPenPathTool({
  saveState: _saveState,
  canvasCoords: (e) => _canvasCoords(e, state.mainCanvas),
  mainCanvas: () => state.mainCanvas,
  ensurePathLayer: () => _ensurePathLayer(),
  drawPath: (layer, points) => _drawPathDraft(layer, points),
  commitPath: (layer) => _commitPathDraft(layer),
  cancelPath: (layer) => _cancelPathDraft(layer),
  composite,
  syncToolClearIndicators: () => _syncToolClearIndicators(),
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

// Quick Selection brush — grows the selection at each sampled point.
const _quickSelectTool = createQuickSelectTool({
  activeLayer,
  saveState: _saveState,
  composite,
  runMagicWand: (x, y, mode, opts) => _runMagicWand(x, y, mode, opts),
  canvasCoords: (e) => _canvasCoords(e, state.mainCanvas),
  brushSize: () => state.quickSelectBrushSize || 24,
  syncToolClearIndicators: () => _syncToolClearIndicators(),
});

// Object Selection brush — SAM-driven stroke → object mask.
const _objectSelectTool = createObjectSelectTool({
  activeLayer,
  saveState: _saveState,
  requestSam: (layer, payload, mode) => _requestAndApplySamMask(layer, payload, mode, null, {}),
  canvasCoords: (e) => _canvasCoords(e, state.mainCanvas),
  layerOffset: (layer) => state.layerOffsets.get(layer.id) || { x: 0, y: 0 },
  showToast: (msg) => uiModule?.showToast?.(msg),
  syncToolClearIndicators: () => _syncToolClearIndicators(),
});

// Patch tool — drag from inside a selection to a clean source region on
// release the selected content is replaced by that region (feathered seam).
// Pixel work lives in editor/patch-ops.js. Content-Aware Move (below) is the
// Edit-menu cousin that moves the selection and diffusion-fills the hole.
const _patchTool = createPatchTool({
  activeLayer,
  canvasCoords: (e) => _canvasCoords(e, state.mainCanvas),
  saveState: _saveState,
  showToast: (msg) => uiModule?.showToast?.(msg),
  onApply: (dx, dy) => _applyPatchOffset(dx, dy, { history: false }),
});

const _perspectiveCropTool = createPerspectiveCropTool({
  getCanvas: () => state.mainCanvas,
  showToast: (msg) => uiModule?.showToast?.(msg),
  onApply: (corners) => _applyPerspectiveCrop(corners),
});

async function _applyPerspectiveCrop(corners) {
  if (!Array.isArray(corners) || corners.length < 4 || !state.mainCanvas) return null;
  const src = document.createElement('canvas');
  src.width = state.imgWidth;
  src.height = state.imgHeight;
  src.getContext('2d').drawImage(state.mainCanvas, 0, 0);
  const size = _perspectiveCropSize(corners, state.imgWidth, state.imgHeight);
  const warped = _perspectiveCropCanvas(src, corners, size.width, size.height);
  _saveState('Perspective crop');
  const id = 'layer-pc-' + Date.now().toString(36);
  await _restoreDraft({
    imgWidth: size.width,
    imgHeight: size.height,
    colorMode: state.colorMode || 'rgb',
    bitDepth: 8,
    activeLayerId: id,
    layers: [{ id, name: 'Perspective Crop', canvasW: size.width, canvasH: size.height, offset: { x: 0, y: 0 }, dataUrl: warped.toDataURL('image/png'), visible: true, opacity: 1 }],
    groups: [],
    savedSelections: [],
  });
  try { _fitZoom(); } catch (_) {}
  composite();
  _renderLayerPanel();
  return { width: size.width, height: size.height };
}

function _openPerspectiveCrop() {
  _perspectiveCropTool.open();
}

function _patchCreateCanvas(w, h) {
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(w));
  canvas.height = Math.max(1, Math.round(h));
  return canvas;
}

function _patchTargetLayer() {
  const layer = activeLayer();
  if (!layer) throw new Error('select a layer');
  if (layer.kind === 'placed') throw new Error('rasterize the placed layer before patching');
  if (layer.kind && layer.kind !== 'raster') throw new Error('rasterize the layer before patching');
  if (_isLayerPixelLocked(state, layer)) throw new Error('unlock image pixels before patching');
  return layer;
}

function _patchWriteLayer(layer, canvas) {
  if (layer.canvas.width !== canvas.width || layer.canvas.height !== canvas.height) {
    layer.canvas.width = canvas.width;
    layer.canvas.height = canvas.height;
  }
  layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
  layer.ctx.drawImage(canvas, 0, 0);
  layer._adjFinal = null;
  layer._adjFinalKey = null;
  layer._stagedAdj = null;
  layer._editingAdjId = null;
  composite();
}

function _applyPatchOffset(dx, dy, { history = true } = {}) {
  const layer = _patchTargetLayer();
  if (!state.wandMask) throw new Error('no selection');
  if (history) _saveState('Patch');
  const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const result = _patchRegion(_patchCreateCanvas, layer.canvas, state.wandMask, offset, dx, dy, 4);
  _patchWriteLayer(layer, result);
  return { dx, dy };
}

function _applyContentAwareMove(dx, dy, { history = true } = {}) {
  const layer = _patchTargetLayer();
  if (!state.wandMask) throw new Error('no selection');
  if (history) _saveState('Content-Aware Move');
  const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const result = _contentAwareMove(_patchCreateCanvas, layer.canvas, state.wandMask, offset, dx, dy);
  _patchWriteLayer(layer, result);
  return { dx, dy };
}

function _syncCloneSourceUi() {
  const label = document.getElementById('ge-clone-source-label');
  const clear = document.getElementById('ge-clone-source-clear');
  const hasSource = state.cloneSourceX !== null && state.cloneSourceY !== null;
  if (label) label.textContent = hasSource
    ? (state.cloneSampleMode === 'composite' ? 'All visible layers sampled' : 'Active layer sampled')
    : 'No source selected';
  // Match the wand/marquee Clear ✕: always visible, lit up (accent) while a
  // sample source is set, so it is obvious the button has something to clear.
  if (clear) {
    clear.hidden = false;
    clear.classList.toggle('has-selection', hasSource);
  }
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

// Floating eyedropper "loupe": a circle that mirrors the panel's live pixel
// preview next to the pointer while the eyedropper is pressed, so you can see
// exactly which pixel you are sampling. The panel copy (#ge-eyedropper-loupe)
// stays put; this one follows the cursor.
let _cursorLoupeEl = null;
let _cursorLoupeCanvas = null;
let _cursorLoupeVisible = false;

function _ensureCursorLoupe() {
  if (_cursorLoupeCanvas) return _cursorLoupeCanvas;
  const el = document.createElement('div');
  el.className = 'ge-eyedropper-cursor-loupe';
  el.setAttribute('aria-hidden', 'true');
  const canvas = document.createElement('canvas');
  canvas.width = 96;
  canvas.height = 96;
  el.appendChild(canvas);
  document.body.appendChild(el);
  _cursorLoupeEl = el;
  _cursorLoupeCanvas = canvas;
  return canvas;
}

function _positionCursorLoupe(e) {
  if (!_cursorLoupeEl || !e || !Number.isFinite(e.clientX) || !Number.isFinite(e.clientY)) return;
  const size = _cursorLoupeEl.offsetWidth || 108;
  const gap = 18;
  let left = e.clientX + gap;
  let top = e.clientY + gap;
  if (left + size > window.innerWidth - 8) left = e.clientX - gap - size;
  if (top + size > window.innerHeight - 8) top = e.clientY - gap - size;
  _cursorLoupeEl.style.left = `${Math.round(Math.max(8, left))}px`;
  _cursorLoupeEl.style.top = `${Math.round(Math.max(8, top))}px`;
}

function _setCursorLoupe(visible, e) {
  _ensureCursorLoupe();
  _cursorLoupeVisible = !!visible;
  if (!_cursorLoupeEl) return;
  _cursorLoupeEl.classList.toggle('is-visible', _cursorLoupeVisible);
  if (_cursorLoupeVisible) _positionCursorLoupe(e);
}

// Draw the zoomed pixel block + centre crosshair. Shared by the panel readout
// and the floating cursor loupe so both show the same preview.
function _drawEyedropperLoupe(canvas, sample) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!sample?.canvas || !Number.isFinite(sample.x) || !Number.isFinite(sample.y)) return;
  const radius = 5;
  const size = radius * 2 + 1;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(sample.canvas, Math.floor(sample.x) - radius, Math.floor(sample.y) - radius, size, size, 0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = 'rgba(224, 108, 117, 0.92)';
  ctx.lineWidth = canvas.width >= 90 ? 3 : 2;
  ctx.beginPath();
  ctx.moveTo(canvas.width / 2, 0);
  ctx.lineTo(canvas.width / 2, canvas.height);
  ctx.moveTo(0, canvas.height / 2);
  ctx.lineTo(canvas.width, canvas.height / 2);
  ctx.stroke();
}

const _eyedropperTool = createEyedropperTool({
  activeLayer,
  composite,
  syncColor: (color) => {
    state.container?.querySelectorAll('.ge-color-picker').forEach(input => {
      input.value = color;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  },
  syncPreview: (color, sample, e) => {
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
    if (loupe) _drawEyedropperLoupe(loupe, sample);
    if (_cursorLoupeVisible) {
      if (sample) {
        _drawEyedropperLoupe(_ensureCursorLoupe(), sample);
        _positionCursorLoupe(e);
      } else {
        _setCursorLoupe(false);
      }
    }
  },
  showToast: (msg) => uiModule?.showToast(msg),
});

const _bucketTool = createBucketTool({
  activeLayer: () => activeLayer() || _activeParentLayer(),
  saveState: _saveState,
  composite,
  renderLayerPanel: () => _renderLayerPanel(),
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

// One-click gradient fill used by the Gradients panel: paints the tool's
// current settings across the whole layer (left → right) so clicking a preset
// applies the gradient immediately instead of only staging its colors.
function _applyFullGradient() {
  const canvas = state.mainCanvas;
  if (!canvas) return false;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return false;
  const midY = rect.top + rect.height / 2;
  const from = { clientX: rect.left + 1, clientY: midY };
  const to = { clientX: rect.right - 1, clientY: midY };
  if (!_gradientTool.begin(from)) return false;
  _gradientTool.drag(to);
  _gradientTool.end(to);
  return true;
}

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
  const incomingOffset = sourceOffset || state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const documentSized = mask.width === state.imgWidth && mask.height === state.imgHeight;
  // A document-sized mask already sitting at offset 0 (the pen builds exactly
  // that) needs no copy at all; only layer-space masks with an offset do.
  const incoming = documentSized && incomingOffset.x === 0 && incomingOffset.y === 0
    ? mask
    : _selectionMaskToDocument(mask, 'layer', incomingOffset, state.imgWidth, state.imgHeight);
  let result = incoming;
  // Replace mode throws the previous selection away, so converting its mask
  // into a full-image canvas first was pure waste on every new selection.
  if (mode !== 'replace' && state.wandMask) {
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
    result = _mergeSelectionMasks(current, incoming, mode);
  }
  state.wandMask = result;
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

// Color Range: select every pixel within a fuzziness of a sample color,
// across the whole layer (not just the connected region). Committed to
// the canonical selection so marching ants / combine modes / masks work.
async function _colorRangeSelection() {
  const layer = activeLayer();
  if (!layer) return;
  const values = await _filterSliderPrompt('Color Range', [
    { key: 'color', type: 'color', value: state.color || '#000000', label: 'Sample color' },
    { key: 'fuzziness', label: 'Fuzziness', min: 0, max: 200, value: state.colorRangeFuzz ?? 40 },
  ]);
  if (!values) return;
  state.colorRangeFuzz = values.fuzziness;
  const src = _getWandSource(layer);
  const target = _hexToRgb(values.color);
  const mask = _colorRangeMask(src.data, src.w, src.h, target, values.fuzziness);
  _saveState('Color range selection');
  _commitSelectionMask(mask, layer, state.wandMode || 'replace', 'color-range');
  composite();
  _syncToolClearIndicators();
}

// Offline one-click subject selection: estimate the background from the
// layer border, threshold saliency (Otsu), keep the largest blob and commit
// it as the selection. Returns the mask size, or null when no distinct
// subject is found (caller falls back to SAM or an approximate region).
function _applyOfflineSubjectSelection(layer, mode = 'replace') {
  if (!layer || !layer.canvas || !layer.ctx) return null;
  const w = layer.canvas.width;
  const h = layer.canvas.height;
  let mask = null;
  try {
    const data = layer.ctx.getImageData(0, 0, w, h).data;
    mask = _computeSubjectMask(data, w, h, {});
  } catch (_) {
    mask = null;
  }
  if (!mask) return null;
  _saveState('Select subject');
  _commitSelectionMask(mask, layer, mode, 'subject');
  state.wandMaskVisible = true;
  composite();
  _syncToolClearIndicators();
  return { width: w, height: h };
}

// Full-area Select-and-Mask workspace: preview the active selection, refine
// it with edge sliders, paint with an add/subtract brush, then output to a
// selection, a layer mask, a new layer, or a new layer with a mask.
function _selectAndMask() {
  const layer = activeLayer();
  if (!layer) return;
  const initial = _selectionMaskAsDocument({ materializeLasso: true });
  openSelectAndMask({
    mask: initial,
    width: state.imgWidth,
    height: state.imgHeight,
    refine: _refineSelectionMask,
    onCommit: ({ mask, output } = {}) => {
      if (!mask) return;
      state.wandMask = mask;
      state.wandMaskSpace = 'document';
      state.wandLayerId = layer.id;
      state.selectionSource = 'select-and-mask';
      if (output === 'mask') {
        _wandToMask();
      } else if (output === 'newLayer') {
        _wandCopyToNewLayer();
      } else if (output === 'newLayerWithMask') {
        _wandCopyToNewLayer();
        _wandToMask();
      } else {
        _saveState('Select and mask');
        composite();
      }
      _syncToolClearIndicators();
    },
  });
}

// Convert the document working mode. Only RGB <-> Grayscale are
// destructive; CMYK/Lab are metadata-only (menu entries are disabled).
function _setColorMode(mode) {
  if (mode === 'rgb' || mode === 'grayscale') {
    if (state.colorMode === mode) {
      uiModule?.showToast?.(`Already in ${mode === 'rgb' ? 'RGB' : 'Grayscale'} mode`);
      return;
    }
    const converted = _applyColorMode(state.layers, mode);
    state.colorMode = mode;
    _saveState(`Convert to ${mode === 'rgb' ? 'RGB' : 'Grayscale'}`);
    composite();
    _renderLayerPanel();
    uiModule?.showToast?.(`Converted ${converted} layer${converted === 1 ? '' : 's'} to ${mode === 'rgb' ? 'RGB' : 'Grayscale'}`);
  }
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

// Text-grounded SAM is intermittently flaky (the grounding model can miss an
// object on a given call); retry once before letting callers fall back.
async function _samWithRetry(layer, payload, mode, seedPoint = null) {
  let lastError = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      return await _requestAndApplySamMask(layer, payload, mode, seedPoint, {});
    } catch (e) {
      lastError = e;
    }
  }
  throw lastError || new Error('SAM selection failed');
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
  // Flyout-only tools (lasso / sam / quickselect / objectselect) carry
  // data-tool-option instead of data-tool, so match either attribute.
  const toolBtn = (id) => state.container.querySelector(
    `.ge-tool-btn[data-tool="${id}"], .ge-tool-btn[data-tool-option="${id}"]`);
  const marqueeBtn = toolBtn('marquee');
  const lassoBtn = toolBtn('lasso');
  const wandBtn  = toolBtn('wand');
  const samBtn   = toolBtn('sam');
  const quickSelectBtn = toolBtn('quickselect');
  const objectSelectBtn = toolBtn('objectselect');
  const inpaintBtn = toolBtn('inpaint');
  if (marqueeBtn) marqueeBtn.classList.toggle('has-selection', !!state.wandMask && state.selectionSource === 'marquee');
  if (lassoBtn) lassoBtn.classList.toggle('has-selection', lassoHasSel);
  if (wandBtn)  wandBtn.classList.toggle('has-selection', !!state.wandMask && state.selectionSource === 'wand');
  if (samBtn)   samBtn.classList.toggle('has-selection', !!state.wandMask && state.selectionSource === 'sam');
  if (quickSelectBtn) quickSelectBtn.classList.toggle('has-selection', !!state.wandMask && state.selectionSource === 'wand');
  if (objectSelectBtn) objectSelectBtn.classList.toggle('has-selection', !!state.wandMask && state.selectionSource === 'sam');
  // The Clear ✕ in the marquee / wand action rows lights up (accent) while a
  // selection is active, matching the tool-button badges above.
    state.container.querySelectorAll('#ge-marquee-clear, #ge-wand-clear, #ge-lasso-clear, #ge-sam-clear, #ge-quickselect-clear, #ge-objectselect-clear').forEach((btn) => {
    btn.classList.toggle('has-selection', !!state.wandMask);
  });
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

function _wandCopyToNewLayer({ saveHistory = true, activate = true, announce = true, name = 'Selection' } = {}) {
  if (!_selectionMaskAsDocument({ materializeLasso: true })) return;
  const src = _readPixelTarget();
  if (!src) return;
  if (saveHistory) _saveState();
  const tmp = _captureSelectedPixels(src);
  const newLayer = createLayer(name, src.canvas.width, src.canvas.height);
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
  return _wandCopyToNewLayer({ name: 'Wand copy' });
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
// The heavy-filter commit render is synchronous: it blocks the main thread, so
// the veil's class is set but no observer (or repaint) can run until the render
// returns. Keep the veil on for at least FILTER_BUSY_MIN_MS from the moment it
// appears, and always leave a short idle tail (FILTER_BUSY_TAIL_MS) after the
// render finishes so the spinner is actually repainted, visible and screen-
// capturable even when the render itself exceeds the minimum.
const FILTER_BUSY_MIN_MS = 600;
const FILTER_BUSY_TAIL_MS = 200;
let _filterBusyShownAt = 0;

function _showFilterBusy(label) {
  const host = state.container || document.body;
  if (!host) return;
  let veil = host.querySelector('.ge-busy-veil');
  if (!veil) {
    veil = document.createElement('div');
    veil.className = 'ge-busy-veil';
    veil.innerHTML = '<div class="ge-busy-card"><span class="ge-busy-spin"></span><span class="ge-busy-label"></span></div>';
    host.appendChild(veil);
  }
  const l = veil.querySelector('.ge-busy-label');
  if (l) l.textContent = (label || 'Working') + '\u2026';
  _filterBusyShownAt = (typeof performance !== 'undefined' ? performance.now() : Date.now());
  veil.classList.add('is-on');
}

async function _hideFilterBusy() {
  const host = state.container || document.body;
  const veil = host && host.querySelector('.ge-busy-veil');
  if (!veil) return;
  const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
  const remaining = Math.max(FILTER_BUSY_MIN_MS - (now - _filterBusyShownAt), FILTER_BUSY_TAIL_MS);
  await new Promise((resolve) => setTimeout(resolve, remaining));
  veil.classList.remove('is-on');
}

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
  const _heavyFilter = (w * h) > 1500000;
  if (_heavyFilter) {
    _showFilterBusy(label);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }
  try {
    layer.ctx.clearRect(0, 0, w, h);
    renderer(snap, result, layer.ctx);
    composite();
  } finally {
    if (_heavyFilter) await _hideFilterBusy();
  }
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

/* Haze removal renderer: reads the snapshot pixels, runs the dark-channel-prior
   dehaze and writes the recovered pixels into the target context. Amount is the
   slider value (0..100). */
function _dehazeRenderer(snap, values, ctx) {
  const w = snap.width;
  const h = snap.height;
  const img = snap.getContext('2d').getImageData(0, 0, w, h);
  const amount = Math.max(0, Math.min(100, Number(values && values.strength) || 0)) / 100;
  const out = _dehazeImageData(img.data, w, h, { strength: amount });
  ctx.putImageData(new ImageData(out, w, h), 0, 0);
}

function _applyDehaze() {
  _applyLiveBlur({
    title: 'Haze Removal',
    label: 'Haze Removal',
    params: [{ key: 'strength', label: 'Amount', min: 0, max: 100, step: 1, value: 70, suffix: '%' }],
    renderer: _dehazeRenderer,
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

// Drop shadows are stored as x/y offsets, but people describe them with a
// direction and a distance; convert at the dialog boundary so the renderer
// keeps its simple offsets.
function _shadowAngleDistance(params = {}) {
  const x = Number(params.x) || 0;
  const y = Number(params.y) || 0;
  return {
    distance: Math.round(Math.hypot(x, y)),
    angle: Math.round((Math.atan2(-y, x) * 180) / Math.PI),
  };
}
function _shadowOffsets(values = {}) {
  const radians = ((Number(values.angle) || 0) * Math.PI) / 180;
  const distance = Number(values.distance) || 0;
  return {
    x: Math.round(Math.cos(radians) * distance),
    y: Math.round(-Math.sin(radians) * distance),
  };
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
  if (type === 'motion-blur' || type === 'zoom-blur' || type === 'pixelate') {
    const controls = type === 'motion-blur'
      ? [
        { key: 'distance', label: 'Distance', min: 0, max: 200, step: 1, value: 20, suffix: 'px' },
        { key: 'angle', label: 'Angle', min: -180, max: 180, step: 1, value: 0, suffix: '\u00b0' },
      ]
      : type === 'zoom-blur'
        ? [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 40, suffix: '%' }]
        : [{ key: 'size', label: 'Cell size', min: 1, max: 200, step: 1, value: 8, suffix: 'px' }];
    const toParams = (values) => type === 'motion-blur'
      ? { distance: values.distance, angle: values.angle }
      : type === 'zoom-blur'
        ? { amount: values.amount }
        : { size: values.size };
    const label = type === 'motion-blur' ? 'Motion Blur' : type === 'zoom-blur' ? 'Zoom Blur' : 'Pixelate';
    const result = await _filterSliderPrompt('Retained ' + label, controls, (values) => {
      layer._effectPreview = { type, opacity: 1, visible: true, params: toParams(values) };
      composite();
    });
    layer._effectPreview = null;
    composite();
    if (!result) return;
    _saveState('Add retained ' + label);
    if (!Array.isArray(layer.effects)) layer.effects = [];
    layer.effects.push(_normalizeEffect({ type, params: toParams(result) }));
    _schedulePersist();
    _renderLayerPanel();
    composite();
    uiModule?.showToast?.(`${label} added`);
    return;
  }
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
      { key: 'distance', label: 'Distance', min: 0, max: 200, step: 1, value: _shadowAngleDistance({ x: 4, y: 6 }).distance, suffix: 'px' },
      { key: 'angle', label: 'Direction', min: -180, max: 180, step: 1, value: _shadowAngleDistance({ x: 4, y: 6 }).angle, suffix: '\u00b0' },
    ];
  const preset = _effectPreset(presetName);
  if (preset?.type === type) {
    const presetValues = type === 'drop-shadow'
      ? { ...preset.params, ..._shadowAngleDistance(preset.params) }
      : preset.params;
    for (const control of initial) {
      const value = presetValues[control.key];
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
            : { color: values.color, opacity: values.opacity / 100, blur: values.blur, ..._shadowOffsets(values) },
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
        : { color: result.color, opacity: result.opacity / 100, blur: result.blur, ..._shadowOffsets(result) },
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

// Non-interactive layer-style application for the agent control API: applies a
// LAYER_STYLES entry (drop-shadow, stroke, outer-glow, gradient-overlay, ...)
// with the supplied params merged over the type defaults.
function _applyLayerStyleNonInteractive(layer, type, params = {}) {
  const isLayerStyle = !!LAYER_STYLES[type];
  const isEffect = !isLayerStyle && _effectTypesList.includes(type);
  if (!isLayerStyle && !isEffect) throw new Error(`unknown layer style: ${type}`);
  if (!layer || layer.kind === 'adjustment') throw new Error('select an image layer or group');
  if (!Array.isArray(layer.effects)) layer.effects = [];
  const merged = isLayerStyle ? styleParams(type, params) : _mergeEffectParams(type, params);
  const normalized = _normalizeEffect({ type, params: merged });
  layer.effects.push(normalized);
  return type;
}

// Merge agent-supplied params over the effect-type defaults, tolerating common
// model aliases (size/radius -> blur, offsetX/dx -> x, offsetY/dy -> y) and an
// opacity given either 0..1 or 0..100.
function _mergeEffectParams(type, params) {
  const defaults = _defaultEffectParams(type) || {};
  const out = { ...defaults };
  if (params && typeof params === 'object') {
    for (const [key, value] of Object.entries(params)) {
      if (value == null) continue;
      if (key in defaults) out[key] = value;
      else if (key === 'size' || key === 'radius') out.blur = value;
      else if (key === 'offsetX' || key === 'dx') out.x = value;
      else if (key === 'offsetY' || key === 'dy') out.y = value;
    }
  }
  if (typeof out.opacity === 'number' && out.opacity > 1) out.opacity = out.opacity / 100;
  return out;
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
  if (type === 'motion-blur' || type === 'zoom-blur' || type === 'pixelate') {
    const controls = type === 'motion-blur'
      ? [
        { key: 'distance', label: 'Distance', min: 0, max: 200, step: 1, value: Number(p.distance) || 0, suffix: 'px' },
        { key: 'angle', label: 'Angle', min: -180, max: 180, step: 1, value: Number(p.angle) || 0, suffix: '\u00b0' },
      ]
      : type === 'zoom-blur'
        ? [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: Number(p.amount) || 0, suffix: '%' }]
        : [{ key: 'size', label: 'Cell size', min: 1, max: 200, step: 1, value: Number(p.size) || 8, suffix: 'px' }];
    const toParams = (values) => type === 'motion-blur'
      ? { distance: values.distance, angle: values.angle }
      : type === 'zoom-blur'
        ? { amount: values.amount }
        : { size: values.size };
    const label = type === 'motion-blur' ? 'Motion Blur' : type === 'zoom-blur' ? 'Zoom Blur' : 'Pixelate';
    const result = await _filterSliderPrompt('Edit ' + label, controls, (values) => {
      layer._effectPreview = { ...effect, params: toParams(values) };
      composite();
    });
    layer._effectPreview = null;
    composite();
    if (!result) return;
    _saveState('Edit ' + label);
    effect.params = toParams(result);
    composite();
    _schedulePersist();
    return;
  }
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
        { key: 'distance', label: 'Distance', min: 0, max: 200, step: 1, value: _shadowAngleDistance(p).distance, suffix: 'px' },
        { key: 'angle', label: 'Direction', min: -180, max: 180, step: 1, value: _shadowAngleDistance(p).angle, suffix: '\u00b0' },
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
        : { color: values.color, opacity: values.opacity / 100, blur: values.blur, ..._shadowOffsets(values) };
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
      : { color: result.color, opacity: result.opacity / 100, blur: result.blur, ..._shadowOffsets(result) };
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

function _applyNoise() {
  _applyLiveBlur({
    title: 'Add Noise',
    label: 'Add Noise',
    params: [
      { key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 20, suffix: '%' },
      { key: 'monochrome', label: 'Monochromatic', min: 0, max: 1, step: 1, value: 1 },
    ],
    renderer: _addNoise,
  });
}

function _applyDespeckle() {
  _applyLiveBlur({
    title: 'Despeckle',
    label: 'Despeckle',
    params: [{ key: 'passes', label: 'Passes', min: 1, max: 3, step: 1, value: 1 }],
    renderer: _despeckle,
  });
}

function _applyPixelate() {
  _applyLiveBlur({
    title: 'Pixelate',
    label: 'Pixelate',
    params: [{ key: 'size', label: 'Cell size', min: 2, max: 64, step: 1, value: 12, suffix: 'px' }],
    renderer: _pixelate,
  });
}

function _applyCrystallize() {
  _applyLiveBlur({
    title: 'Crystallize',
    label: 'Crystallize',
    params: [{ key: 'size', label: 'Cell size', min: 2, max: 64, step: 1, value: 16, suffix: 'px' }],
    renderer: _crystallize,
  });
}

function _applyEmboss() {
  _applyLiveBlur({
    title: 'Emboss',
    label: 'Emboss',
    params: [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 100, suffix: '%' }],
    renderer: _emboss,
  });
}

function _applyFindEdges() {
  _applyLiveBlur({
    title: 'Find Edges',
    label: 'Find Edges',
    params: [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 100, suffix: '%' }],
    renderer: _findEdges,
  });
}

function _offset(snap, v, dst) {
  const w = snap.width, h = snap.height;
  const ox = ((Math.round(Number(v.dx) || 0) % w) + w) % w;
  const oy = ((Math.round(Number(v.dy) || 0) % h) + h) % h;
  dst.clearRect(0, 0, w, h);
  dst.drawImage(snap, ox, oy);
  dst.drawImage(snap, ox - w, oy);
  dst.drawImage(snap, ox, oy - h);
  dst.drawImage(snap, ox - w, oy - h);
}

function _applyOffset() {
  _applyLiveBlur({
    title: 'Offset',
    label: 'Offset',
    params: [
      { key: 'dx', label: 'Horizontal', min: -1000, max: 1000, step: 1, value: 100, suffix: 'px' },
      { key: 'dy', label: 'Vertical', min: -1000, max: 1000, step: 1, value: 100, suffix: 'px' },
    ],
    renderer: _offset,
  });
}

// Box blur — a straight averaged blur. Reuses the pure separable
// `boxBlurRGB` helper from the retouch module instead of a canvas
// filter so edges are clamped (no transparent halo).
function _boxBlur(snap, v, dst) {
  const w = snap.width, h = snap.height;
  const src = snap.getContext('2d').getImageData(0, 0, w, h);
  const radius = Math.max(0, Math.round(Number(v.radius) || 0));
  const out = _boxBlurRGB(src.data, w, h, radius);
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

function _applyBoxBlur() {
  _applyLiveBlur({
    title: 'Box Blur',
    label: 'Box Blur',
    params: [{ key: 'radius', label: 'Radius', min: 0, max: 100, step: 1, value: 6, suffix: 'px' }],
    renderer: _boxBlur,
  });
}

// Surface blur — edge-preserving smoothing: blur everywhere, then blend
// the original back in wherever the local change exceeds the threshold.
// Reuses the same box-blur helper for the smoothing pass.
function _surfaceBlur(snap, v, dst) {
  const w = snap.width, h = snap.height;
  const src = snap.getContext('2d').getImageData(0, 0, w, h);
  const radius = Math.max(1, Math.round(Number(v.radius) || 5));
  const threshold = Math.max(1, Number(v.threshold) || 15) * 2.55;
  const blurred = _boxBlurRGB(src.data, w, h, radius);
  const out = new Uint8ClampedArray(src.data.length);
  for (let i = 0; i < src.data.length; i += 4) {
    const diff = (Math.abs(src.data[i] - blurred[i])
      + Math.abs(src.data[i + 1] - blurred[i + 1])
      + Math.abs(src.data[i + 2] - blurred[i + 2])) / 3;
    const keep = Math.min(1, diff / threshold);
    out[i] = src.data[i] * keep + blurred[i] * (1 - keep);
    out[i + 1] = src.data[i + 1] * keep + blurred[i + 1] * (1 - keep);
    out[i + 2] = src.data[i + 2] * keep + blurred[i + 2] * (1 - keep);
    out[i + 3] = src.data[i + 3];
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

function _applySurfaceBlur() {
  _applyLiveBlur({
    title: 'Surface Blur',
    label: 'Surface Blur',
    params: [
      { key: 'radius', label: 'Radius', min: 1, max: 50, step: 1, value: 5, suffix: 'px' },
      { key: 'threshold', label: 'Threshold', min: 1, max: 100, step: 1, value: 15, suffix: '%' },
    ],
    renderer: _surfaceBlur,
  });
}

// Custom — user-editable 3x3 convolution kernel (Filter > Other > Custom).
// The kernel renderer lives in filters/effects.js and reuses `convolve`.
function _applyCustom() {
  _applyLiveBlur({
    title: 'Custom',
    label: 'Custom',
    params: [
      { key: 'k0', label: 'Top left', min: -9, max: 9, step: 1, value: 0 },
      { key: 'k1', label: 'Top middle', min: -9, max: 9, step: 1, value: 0 },
      { key: 'k2', label: 'Top right', min: -9, max: 9, step: 1, value: 0 },
      { key: 'k3', label: 'Middle left', min: -9, max: 9, step: 1, value: 0 },
      { key: 'k4', label: 'Center', min: -9, max: 9, step: 1, value: 1 },
      { key: 'k5', label: 'Middle right', min: -9, max: 9, step: 1, value: 0 },
      { key: 'k6', label: 'Bottom left', min: -9, max: 9, step: 1, value: 0 },
      { key: 'k7', label: 'Bottom middle', min: -9, max: 9, step: 1, value: 0 },
      { key: 'k8', label: 'Bottom right', min: -9, max: 9, step: 1, value: 0 },
      { key: 'divisor', label: 'Scale', min: 1, max: 20, step: 1, value: 1 },
      { key: 'offset', label: 'Offset', min: -128, max: 128, step: 1, value: 0 },
    ],
    renderer: _customKernel,
  });
}

// Maximum — morphological dilation (Filter > Other > Maximum). Reuses the
// live-filter dialog and a pure separable max renderer in effects.js.
function _applyMaximum() {
  _applyLiveBlur({
    title: 'Maximum',
    label: 'Maximum',
    params: [{ key: 'radius', label: 'Radius', min: 1, max: 50, step: 1, value: 4, suffix: 'px' }],
    renderer: _maximum,
  });
}

// Minimum — morphological erosion (Filter > Other > Minimum).
function _applyMinimum() {
  _applyLiveBlur({
    title: 'Minimum',
    label: 'Minimum',
    params: [{ key: 'radius', label: 'Radius', min: 1, max: 50, step: 1, value: 4, suffix: 'px' }],
    renderer: _minimum,
  });
}

// Median — per-channel median noise reduction (Filter > Noise > Median).
function _applyMedian() {
  _applyLiveBlur({
    title: 'Median',
    label: 'Median',
    params: [{ key: 'radius', label: 'Radius', min: 1, max: 20, step: 1, value: 1, suffix: 'px' }],
    renderer: _median,
  });
}

// Polar Coordinates — rectangular/polar disc remap (Filter > Distort).
function _applyPolarCoordinates() {
  _applyLiveBlur({
    title: 'Polar Coordinates',
    label: 'Polar Coordinates',
    params: [{ key: 'mode', label: 'Direction (0 rect, 1 polar)', min: 0, max: 1, step: 1, value: 0 }],
    renderer: _polarCoordinates,
  });
}

// Difference Clouds — deterministic seeded clouds blended by difference
// (Filter > Render).
function _applyDifferenceClouds() {
  _applyLiveBlur({
    title: 'Difference Clouds',
    label: 'Difference Clouds',
    params: [
      { key: 'scale', label: 'Scale', min: 4, max: 200, step: 1, value: 64, suffix: 'px' },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _differenceClouds,
  });
}

// Lens Flare — deterministic additive render flare (Filter > Render).
function _applyLensFlare() {
  _applyLiveBlur({
    title: 'Lens Flare',
    label: 'Lens Flare',
    params: [
      { key: 'x', label: 'Flare X', min: 0, max: 100, step: 1, value: 50, suffix: '%' },
      { key: 'y', label: 'Flare Y', min: 0, max: 100, step: 1, value: 35, suffix: '%' },
      { key: 'brightness', label: 'Brightness', min: 0, max: 200, step: 1, value: 100, suffix: '%' },
      { key: 'size', label: 'Size', min: 5, max: 200, step: 1, value: 50, suffix: '%' },
    ],
    renderer: _lensFlare,
  });
}

// Fibers — vertically stretched seeded noise (Filter > Render).
function _applyFibers() {
  _applyLiveBlur({
    title: 'Fibers',
    label: 'Fibers',
    params: [
      { key: 'variance', label: 'Variance', min: 1, max: 64, step: 1, value: 16 },
      { key: 'strength', label: 'Strength', min: 0, max: 100, step: 1, value: 60, suffix: '%' },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _fibers,
  });
}

// Oil Paint — most-common-intensity neighbourhood pass (Filter > Stylize).
function _applyOilPaint() {
  _applyLiveBlur({
    title: 'Oil Paint',
    label: 'Oil Paint',
    params: [
      { key: 'radius', label: 'Bristle radius', min: 1, max: 8, step: 1, value: 3, suffix: 'px' },
      { key: 'levels', label: 'Intensity levels', min: 2, max: 32, step: 1, value: 8 },
    ],
    renderer: _oilPaint,
  });
}

// Lens Correction — barrel/pincushion remap + vignette (Filter > Distort).
function _applyLensCorrection() {
  _applyLiveBlur({
    title: 'Lens Correction',
    label: 'Lens Correction',
    params: [
      { key: 'distortion', label: 'Distortion', min: -100, max: 100, step: 1, value: 0 },
      { key: 'vignette', label: 'Vignette', min: -100, max: 100, step: 1, value: 0, suffix: '%' },
    ],
    renderer: _lensCorrection,
  });
}

// Diffuse — seeded random per-pixel displacement (Filter > Stylize).
function _applyDiffuse() {
  _applyLiveBlur({
    title: 'Diffuse',
    label: 'Diffuse',
    params: [
      { key: 'radius', label: 'Radius', min: 1, max: 50, step: 1, value: 4, suffix: 'px' },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _diffuse,
  });
}

// Ocean Ripple — two overlapping sine displacement waves (Filter > Distort).
function _applyOceanRipple() {
  _applyLiveBlur({
    title: 'Ocean Ripple',
    label: 'Ocean Ripple',
    params: [
      { key: 'magnitude', label: 'Ripple magnitude', min: 1, max: 50, step: 1, value: 8, suffix: 'px' },
      { key: 'size', label: 'Ripple size', min: 4, max: 100, step: 1, value: 24, suffix: 'px' },
    ],
    renderer: _oceanRipple,
  });
}

// Glass — tiled value-noise displacement (Filter > Distort).
function _applyGlass() {
  _applyLiveBlur({
    title: 'Glass',
    label: 'Glass',
    params: [
      { key: 'tile', label: 'Tile size', min: 4, max: 200, step: 1, value: 16, suffix: 'px' },
      { key: 'amount', label: 'Distortion', min: 1, max: 60, step: 1, value: 10, suffix: 'px' },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _glass,
  });
}

// Mosaic Tiles — mosaic cells plus darker grout lines (Filter > Pixelate).
function _applyMosaicTiles() {
  _applyLiveBlur({
    title: 'Mosaic Tiles',
    label: 'Mosaic Tiles',
    params: [
      { key: 'size', label: 'Tile size', min: 3, max: 200, step: 1, value: 12, suffix: 'px' },
      { key: 'grout', label: 'Grout width', min: 0, max: 10, step: 1, value: 1, suffix: 'px' },
    ],
    renderer: _mosaicTiles,
  });
}

// Wind — directional decaying streak blur (Filter > Stylize).
function _applyWind() {
  _applyLiveBlur({
    title: 'Wind',
    label: 'Wind',
    params: [
      { key: 'strength', label: 'Strength', min: 1, max: 64, step: 1, value: 20, suffix: 'px' },
      { key: 'angle', label: 'Direction', min: -180, max: 180, step: 1, value: 0, suffix: '°' },
    ],
    renderer: _wind,
  });
}

// Trace Contour — thresholded edge outline (Filter > Stylize).
function _applyTraceContour() {
  _applyLiveBlur({
    title: 'Trace Contour',
    label: 'Trace Contour',
    params: [{ key: 'level', label: 'Level', min: 0, max: 255, step: 1, value: 128 }],
    renderer: _traceContour,
  });
}

// Halftone Pattern — deterministic dot screen (Filter > Sketch).
function _applyHalftonePattern() {
  _applyLiveBlur({
    title: 'Halftone Pattern',
    label: 'Halftone Pattern',
    params: [{ key: 'size', label: 'Pattern size', min: 2, max: 64, step: 1, value: 6, suffix: 'px' }],
    renderer: _halftonePattern,
  });
}

// Reticulation — seeded film-grain re-quantisation (Filter > Sketch).
function _applyReticulation() {
  _applyLiveBlur({
    title: 'Reticulation',
    label: 'Reticulation',
    params: [
      { key: 'density', label: 'Density', min: 1, max: 100, step: 1, value: 30 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _reticulation,
  });
}

// Shear — fixed S-curve row displacement (Filter > Distort).
function _applyShear() {
  _applyLiveBlur({
    title: 'Shear',
    label: 'Shear',
    params: [{ key: 'amount', label: 'Shear amount', min: -200, max: 200, step: 1, value: 40, suffix: 'px' }],
    renderer: _shear,
  });
}

// Note Paper — embossed, grainy stock (Filter > Sketch).
function _applyNotePaper() {
  _applyLiveBlur({
    title: 'Note Paper',
    label: 'Note Paper',
    params: [
      { key: 'relief', label: 'Relief', min: 1, max: 10, step: 1, value: 3 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _notePaper,
  });
}

// Charcoal — smudged inverse-luminance strokes (Filter > Sketch).
function _applyCharcoal() {
  _applyLiveBlur({
    title: 'Charcoal',
    label: 'Charcoal',
    params: [
      { key: 'thickness', label: 'Thickness', min: 1, max: 7, step: 1, value: 2 },
      { key: 'detail', label: 'Detail', min: 0, max: 5, step: 1, value: 3 },
      { key: 'balance', label: 'Light/Dark Balance', min: 0, max: 100, step: 1, value: 50 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _charcoal,
  });
}

// Chalk & Charcoal — chalk in the lights, charcoal in the darks (Filter > Sketch).
function _applyChalkCharcoal() {
  _applyLiveBlur({
    title: 'Chalk & Charcoal',
    label: 'Chalk & Charcoal',
    params: [
      { key: 'charcoal', label: 'Charcoal Area', min: 0, max: 20, step: 1, value: 6 },
      { key: 'chalk', label: 'Chalk Area', min: 0, max: 20, step: 1, value: 6 },
      { key: 'pressure', label: 'Stroke Pressure', min: 0, max: 5, step: 1, value: 1 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _chalkCharcoal,
  });
}

// Graphic Pen — fine directional hatching (Filter > Sketch).
function _applyGraphicPen() {
  _applyLiveBlur({
    title: 'Graphic Pen',
    label: 'Graphic Pen',
    params: [
      { key: 'length', label: 'Stroke Length', min: 1, max: 15, step: 1, value: 6, suffix: 'px' },
      { key: 'angle', label: 'Direction', min: -180, max: 180, step: 1, value: 45, suffix: '°' },
      { key: 'balance', label: 'Light/Dark Balance', min: 0, max: 100, step: 1, value: 41 },
    ],
    renderer: _graphicPen,
  });
}

// Photocopy — two-tone threshold with edge detail (Filter > Sketch).
function _applyPhotocopy() {
  _applyLiveBlur({
    title: 'Photocopy',
    label: 'Photocopy',
    params: [
      { key: 'detail', label: 'Detail', min: 1, max: 24, step: 1, value: 12 },
      { key: 'darkness', label: 'Darkness', min: 1, max: 24, step: 1, value: 12 },
    ],
    renderer: _photocopy,
  });
}

// Water Paper — fibrous wet-paper bleed (Filter > Sketch).
function _applyWaterPaper() {
  _applyLiveBlur({
    title: 'Water Paper',
    label: 'Water Paper',
    params: [
      { key: 'fiber', label: 'Fiber Length', min: 3, max: 50, step: 1, value: 15, suffix: 'px' },
      { key: 'brightness', label: 'Brightness', min: 0, max: 100, step: 1, value: 60 },
      { key: 'contrast', label: 'Contrast', min: 0, max: 100, step: 1, value: 50 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _waterPaper,
  });
}

// Sharpen More — stronger fixed sharpen (Filter > Sharpen).
function _applySharpenMore() {
  _applyLiveBlur({
    title: 'Sharpen More',
    label: 'Sharpen More',
    params: [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 60, suffix: '%' }],
    renderer: _sharpenMore,
  });
}

// Poster Edges — flat colour bands with inked outlines (Filter > Artistic).
function _applyPosterEdges() {
  _applyLiveBlur({
    title: 'Poster Edges',
    label: 'Poster Edges',
    params: [
      { key: 'edge', label: 'Edge Intensity', min: 0, max: 10, step: 1, value: 2 },
      { key: 'levels', label: 'Posterization', min: 2, max: 16, step: 1, value: 5 },
    ],
    renderer: _posterEdges,
  });
}

// Cutout — flat posterised colour bands (Filter > Artistic).
function _applyCutout() {
  _applyLiveBlur({
    title: 'Cutout',
    label: 'Cutout',
    params: [
      { key: 'levels', label: 'No. of Levels', min: 2, max: 16, step: 1, value: 5 },
    ],
    renderer: _cutout,
  });
}

// Ink Outlines — dark Laplacian outlines (Filter > Brush Strokes).
function _applyInkOutlines() {
  _applyLiveBlur({
    title: 'Ink Outlines',
    label: 'Ink Outlines',
    params: [
      { key: 'dark', label: 'Dark Intensity', min: 0, max: 50, step: 1, value: 20 },
    ],
    renderer: _inkOutlines,
  });
}

// Stained Glass — seeded Voronoi cells with lead lines (Filter > Texture).
function _applyStainedGlass() {
  _applyLiveBlur({
    title: 'Stained Glass',
    label: 'Stained Glass',
    params: [
      { key: 'cell', label: 'Cell Size', min: 4, max: 64, step: 1, value: 12, suffix: 'px' },
      { key: 'border', label: 'Border Thickness', min: 0, max: 8, step: 1, value: 1, suffix: 'px' },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _stainedGlass,
  });
}

// Grain — deterministic monochrome film grain (Filter > Texture).
function _applyGrain() {
  _applyLiveBlur({
    title: 'Grain',
    label: 'Grain',
    params: [
      { key: 'intensity', label: 'Intensity', min: 0, max: 100, step: 1, value: 20 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _grain,
  });
}

// Texturizer — seeded canvas relief (Filter > Texture).
function _applyTexturizer() {
  _applyLiveBlur({
    title: 'Texturizer',
    label: 'Texturizer',
    params: [
      { key: 'scale', label: 'Scaling', min: 2, max: 64, step: 1, value: 8, suffix: 'px' },
      { key: 'relief', label: 'Relief', min: 0, max: 50, step: 1, value: 4 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _texturizer,
  });
}

// Patchwork — averaged tiles with a light bevel (Filter > Texture).
function _applyPatchwork() {
  _applyLiveBlur({
    title: 'Patchwork',
    label: 'Patchwork',
    params: [
      { key: 'size', label: 'Square Size', min: 2, max: 200, step: 1, value: 12, suffix: 'px' },
    ],
    renderer: _patchwork,
  });
}

// Craquelure — contour-line cracks over the image (Filter > Texture).
function _applyCraquelure() {
  _applyLiveBlur({
    title: 'Craquelure',
    label: 'Craquelure',
    params: [
      { key: 'crack', label: 'Crack Spacing', min: 3, max: 64, step: 1, value: 10, suffix: 'px' },
      { key: 'depth', label: 'Crack Depth', min: 0, max: 10, step: 1, value: 3 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 1 },
    ],
    renderer: _craquelure,
  });
}

// Crosshatch — crossed diagonal ink strokes (Filter > Brush Strokes).
function _applyCrosshatch() {
  _applyLiveBlur({
    title: 'Crosshatch',
    label: 'Crosshatch',
    params: [
      { key: 'length', label: 'Stroke Length', min: 3, max: 50, step: 1, value: 8, suffix: 'px' },
      { key: 'sharpness', label: 'Sharpness', min: 1, max: 20, step: 1, value: 6 },
      { key: 'strength', label: 'Strength', min: 0, max: 100, step: 1, value: 50 },
    ],
    renderer: _crosshatch,
  });
}

// Replace Color — proximity recolour (Image > Adjustments).
function _applyReplaceColor() {
  _applyLiveBlur({
    title: 'Replace Color',
    label: 'Replace Color',
    params: [
      { key: 'from', label: 'Target colour', type: 'color', value: '#000000' },
      { key: 'to', label: 'Replacement', type: 'color', value: '#ffffff' },
      { key: 'fuzziness', label: 'Fuzziness', min: 0, max: 100, step: 1, value: 30, suffix: '%' },
      { key: 'strength', label: 'Strength', min: 0, max: 100, step: 1, value: 100, suffix: '%' },
    ],
    renderer: _replaceColor,
  });
}

function _glowingEdges(snap, v, dst) {
  const w = snap.width, h = snap.height;
  const edges = document.createElement('canvas');
  edges.width = w; edges.height = h;
  const ectx = edges.getContext('2d');
  _findEdges(snap, v, ectx);
  const img = ectx.getImageData(0, 0, w, h);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    d[i] = 255 - d[i];
    d[i + 1] = 255 - d[i + 1];
    d[i + 2] = 255 - d[i + 2];
  }
  dst.putImageData(img, 0, 0);
}

function _applyGlowingEdges() {
  _applyLiveBlur({
    title: 'Glowing Edges',
    label: 'Glowing Edges',
    params: [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 100, suffix: '%' }],
    renderer: _glowingEdges,
  });
}

function _applySolarize() {
  _applyLiveBlur({
    title: 'Solarize',
    label: 'Solarize',
    params: [{ key: 'threshold', label: 'Threshold', min: 0, max: 255, step: 1, value: 128 }],
    renderer: _solarize,
  });
}

function _applyTwirl() {
  _applyLiveBlur({
    title: 'Twirl',
    label: 'Twirl',
    params: [{ key: 'angle', label: 'Angle', min: -360, max: 360, step: 1, value: 90, suffix: '°' }],
    renderer: _twirl,
  });
}

function _applyPinch() {
  _applyLiveBlur({
    title: 'Pinch',
    label: 'Pinch',
    params: [{ key: 'amount', label: 'Amount', min: -100, max: 100, step: 1, value: 50 }],
    renderer: _pinch,
  });
}

function _applySpherize() {
  _applyLiveBlur({
    title: 'Spherize',
    label: 'Spherize',
    params: [{ key: 'amount', label: 'Amount', min: -100, max: 100, step: 1, value: 50 }],
    renderer: _spherize,
  });
}

function _applyRipple() {
  _applyLiveBlur({
    title: 'Ripple',
    label: 'Ripple',
    params: [
      { key: 'amount', label: 'Amount', min: -100, max: 100, step: 1, value: 20, suffix: 'px' },
      { key: 'wavelength', label: 'Wavelength', min: 4, max: 100, step: 1, value: 20, suffix: 'px' },
    ],
    renderer: _ripple,
  });
}

function _applyClouds() {
  _applyLiveBlur({
    title: 'Clouds',
    label: 'Clouds',
    params: [
      { key: 'scale', label: 'Scale', min: 4, max: 200, step: 1, value: 48 },
      { key: 'seed', label: 'Seed', min: 1, max: 999, step: 1, value: 7 },
    ],
    renderer: _clouds,
  });
}

function _applySharpen() {
  _applyLiveBlur({
    title: 'Sharpen',
    label: 'Sharpen',
    params: [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 50, suffix: '%' }],
    renderer: _sharpen,
  });
}

function _applySharpenEdges() {
  _applyLiveBlur({
    title: 'Sharpen Edges',
    label: 'Sharpen Edges',
    params: [{ key: 'amount', label: 'Amount', min: 0, max: 100, step: 1, value: 50, suffix: '%' }],
    renderer: _sharpenEdges,
  });
}

function _applyUnsharpMask() {
  _applyLiveBlur({
    title: 'Unsharp Mask',
    label: 'Unsharp Mask',
    params: [
      { key: 'amount', label: 'Amount', min: 0, max: 200, step: 1, value: 80, suffix: '%' },
      { key: 'radius', label: 'Radius', min: 1, max: 50, step: 1, value: 2, suffix: 'px' },
      { key: 'threshold', label: 'Threshold', min: 0, max: 128, step: 1, value: 0 },
    ],
    renderer: _unsharpMask,
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
  const pad = state._footerDocked ? 0 : 20;
  const rulerPad = state.rulersVisible ? 18 : 0;
  const maxW = area.clientWidth - pad * 2 - rulerPad;
  const maxH = area.clientHeight - pad * 2 - rulerPad;
  return Math.min(1, maxW / state.imgWidth, maxH / state.imgHeight);
}

function _applyZoom({ resetPan = false } = {}) {
  _invalidateCoordsCaches();
  if (!state.mainCanvas) return;
  state.mainCanvas.style.width = (state.imgWidth * state.zoom) + 'px';
  state.mainCanvas.style.height = (state.imgHeight * state.zoom) + 'px';
  const label = state.container.querySelector('.ge-zoom-label');
  if (label) label.textContent = Math.round(state.zoom * 100) + '%';
  _syncZoomControls();
  const area = state.container && state.container.querySelector('.ge-canvas-area');
  if (resetPan && area && area._resetPan) area._resetPan();
  _syncSelectionOverlay();
  // The transform overlay is sized and placed for the zoom it was drawn at;
  // a refit (e.g. the phone Layers sheet collapsing) must redraw it.
  if (state.transformActive) _drawTransformHandles();
  _precisionGuides?.redrawRulers();
}

// Zoom tool (Z): click to zoom in one step, Alt/Option-click to zoom out.
// Photoshop-style — keeps the current pan and only changes the scale.
function _zoomToolClick(e) {
  const zoomOut = !!(e && (e.altKey || e.metaKey));
  const factor = zoomOut ? (1 / 1.5) : 1.5;
  const next = Math.max(0.02, Math.min(64, (state.zoom || 1) * factor));
  if (Math.abs(next - state.zoom) < 0.0001) return;
  state.zoom = next;
  state.zoomViewMode = 'scale';
  _applyZoom();
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
  // Mark the page while the editor is open so shared overlays (e.g. toasts)
  // can avoid the editor's own top rows on small screens.
  try { document.body.classList.add('ge-editor-open'); } catch (_) {}

  // Toolbar (left) — DOM construction lives in editor/build/toolbar.js;
  // the big tool-switch handler stays here so it can touch module state.
  let canvasNavigation = null;
  const { toolbar, toolKeyMap: _toolKeyMap, syncActive: _syncToolActive } = _buildToolbar({
    currentTool: state.tool,
    onClearSelection: (which) => {
      if (which === 'marquee' || which === 'wand' || which === 'lasso'
        || which === 'quickselect' || which === 'objectselect') {
        // _wandClear()/_deselectSelection() materializes and clears the lasso
        // too, so it fully clears the selection for every one of these tools.
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
        if (state.tool === 'crop') _cropTool.reset();
        else _cropTool.cancel('tool-switch');
        _marqueeTool.cancel('tool-switch');
        _quickSelectTool.cancel();
        _objectSelectTool.cancel();
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
      // Pixel is its own tool: it always stamps hard 1px squares, while a
      // regular Brush/Eraser drops the pixel flag so they paint normally.
      if (toolId === 'pixel') state.brushPixel = true;
      else if (toolId === 'brush' || toolId === 'eraser') state.brushPixel = false;
      // Paint tools rasterize silently on the canvas press (undoable), so a
      // single click always paints — never a dialog at tool select.
      if (!_pixelPaintTools.has(toolId)) void _offerRasterizeForTool(toolId);
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
      const hasToolControls = ['move', 'transform', 'brush', 'pixel', 'gradient', 'bucket', 'crop', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'liquify', 'eyedropper', 'text', 'shape', 'path', 'marquee', 'lasso', 'pen', 'quickselect', 'objectselect', 'wand', 'inpaint', 'sam'].includes(toolId);
      const controlsVisible = controls && !controls.classList.contains('dismissed');
      if (isMobile && hasToolControls && controlsVisible) {
        const rp = document.querySelector('.ge-right-panel');
        if (rp) {
          rp.classList.remove('expanded');
          rp.classList.add('minimized');
        }
      }
      toolbarEl.querySelectorAll('.ge-tool-btn').forEach(b => b.classList.toggle('active', b.dataset.tool === state.tool));
      _syncToolActive(state.tool);
      const statusTool = document.getElementById('ge-status-tool');
      if (statusTool) statusTool.textContent = _TOOLS[toolId]?.label || toolId || '';
      _renderOptionsBar(optionsBar, state.tool);
      // Desktop dock: Move/Transform drive the Position X/Y/W/H fields that
      // now live in the Properties panel, so reveal them. (Tool Options is
      // no longer a dock panel — it renders in the options bar.)
      if (dock && (toolId === 'move' || toolId === 'transform')) dock.show('properties');
      // Activate drag-resize handles when picking the Resize tool
      if (toolId === 'transform' && !state.transformActive) {
        _startTransform();
        _maybeShowTransformHint();
      }
      // Show/hide brush controls. Brush, Eraser AND Clone use the
      // shared size+color row; Inpaint has its OWN size slider.
      const brushControls = document.getElementById('ge-brush-controls');
      const needsBrush = ['brush', 'pixel', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn'].includes(toolId);
      if (brushControls) brushControls.style.display = needsBrush ? '' : 'none';
      // Surface the brush-preset picker inline in the options bar for
      // painting tools instead of burying it behind the "..." overflow.
      if (brushControls) brushControls.classList.toggle('ge-has-brush-presets', needsBrush);
      const textSection = document.getElementById('ge-text-section');
      const penSection = document.getElementById('ge-pen-section');
      if (penSection) penSection.style.display = toolId === 'pen' ? '' : 'none';
      const liquifySection = document.getElementById('ge-liquify-section');
      if (liquifySection) liquifySection.style.display = toolId === 'liquify' ? '' : 'none';
      if (textSection) textSection.style.display = toolId === 'text' ? '' : 'none';
      // The Text option set is wider than the thin strip: let the bar grow
      // to a second row instead of clipping the trailing fields.
      document.querySelector('.ge-options-bar')?.classList.toggle('ge-options-tall', toolId === 'text');
      if (toolId === 'text') _syncTextControls();
      const shapeSection = document.getElementById('ge-shape-section');
      if (shapeSection) shapeSection.style.display = toolId === 'shape' ? '' : 'none';
      if (toolId === 'shape') _syncShapeControls();
      const pathSection = document.getElementById('ge-path-section');
      if (pathSection) pathSection.style.display = toolId === 'path' ? '' : 'none';
      if (toolId === 'path') _wirePathControls();
      const gradientSection = document.getElementById('ge-gradient-section');
      if (gradientSection) gradientSection.style.display = toolId === 'gradient' ? '' : 'none';
      const geometrySection = document.getElementById('ge-layer-geometry-section');
      if (geometrySection) geometrySection.style.display = '';
      if (['move', 'transform'].includes(toolId)) _layerGeometry.sync();
      // Eraser and Clone don't care about color — hide the color row.
      const colorRow = document.getElementById('ge-color-row');
      if (colorRow) colorRow.style.display = ['eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'liquify'].includes(toolId) ? 'none' : '';
      const colorLabel = colorRow?.querySelector('label');
      if (colorLabel) colorLabel.textContent = 'Color';
      // Per-tool stroke modifiers all live in the shared brush "More" menu so
      // every brush-family tool has the same compact strip as the Brush. Only
      // the group for the active tool is shown; the rest stay hidden.
      const strokeTool = (toolId === 'brush' || toolId === 'pixel' || toolId === 'dodge' || toolId === 'burn') ? 'brush'
        : toolId === 'eraser' ? 'eraser'
          : (toolId === 'clone' || toolId === 'heal') ? 'clone'
            : toolId === 'smudge' ? 'smudge'
              : '';
      document.querySelectorAll('#ge-brush-more-menu [data-stroke-tool]').forEach((row) => {
        row.style.display = row.dataset.strokeTool === strokeTool ? '' : 'none';
      });
      const dodgeBurnRow = document.getElementById('ge-dodgeburn-intensity-row');
      // Intensity sits in the strip next to Size, so it must show for Dodge and
      // Burn only (and never for the other brush-family tools).
      if (dodgeBurnRow) dodgeBurnRow.style.display = (toolId === 'dodge' || toolId === 'burn') ? '' : 'none';
      const dodgeBurnSlider = document.getElementById('ge-dodgeburn-intensity');
      if (dodgeBurnSlider) {
        const intensity = Number.isFinite(state.dodgeBurnIntensity) ? state.dodgeBurnIntensity : 25;
        dodgeBurnSlider.value = String(intensity);
        const label = document.getElementById('ge-dodgeburn-intensity-label');
        if (label) label.textContent = `${Math.round(intensity)}%`;
      }
      // Every stroke group reads the same: a titled card of rows. The shared
      // brush card is shown for Brush / Pixel / Dodge / Burn, so relabel its
      // heading to the active tool (and the clone card to Healing for heal).
      const brushStrokeTitle = document.getElementById('ge-stroke-title-brush');
      if (brushStrokeTitle) brushStrokeTitle.textContent = _TOOLS[toolId]?.label || 'Brush';
      const cloneStrokeTitle = document.getElementById('ge-stroke-title-clone');
      if (cloneStrokeTitle) cloneStrokeTitle.textContent = toolId === 'heal' ? 'Healing' : 'Clone';
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
      const bucketSection = document.getElementById('ge-bucket-section');
      if (bucketSection) bucketSection.style.display = toolId === 'bucket' ? '' : 'none';
      const cropSection = document.getElementById('ge-crop-section');
      if (cropSection) cropSection.style.display = toolId === 'crop' ? '' : 'none';
      const lassoSection = document.getElementById('ge-lasso-section');
      if (lassoSection) lassoSection.style.display = state.tool === 'lasso' ? '' : 'none';
      const marqueeSection = document.getElementById('ge-marquee-section');
      if (marqueeSection) marqueeSection.style.display = state.tool === 'marquee' ? '' : 'none';
      const wandSection = document.getElementById('ge-wand-section');
      if (wandSection) wandSection.style.display = state.tool === 'wand' ? '' : 'none';
      const samSection = document.getElementById('ge-sam-section');
      if (samSection) samSection.style.display = state.tool === 'sam' ? '' : 'none';
      const quickSelectSection = document.getElementById('ge-quickselect-section');
      if (quickSelectSection) quickSelectSection.style.display = state.tool === 'quickselect' ? '' : 'none';
      const objectSelectSection = document.getElementById('ge-objectselect-section');
      if (objectSelectSection) objectSelectSection.style.display = state.tool === 'objectselect' ? '' : 'none';
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
      const useCircle = ['brush', 'pixel', 'eraser', 'inpaint', 'lasso', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'liquify'].includes(state.tool);
      if (state.mainCanvas) {
        // Custom SVG cursor for the Move tool — white fill with black
        // stroke so it reads on both light and dark canvases.
        const moveCursorSvg = `data:image/svg+xml;utf8,${encodeURIComponent(
          '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="white" stroke="black" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 L9 5 H11 V11 H5 V9 L2 12 L5 15 V13 H11 V19 H9 L12 22 L15 19 H13 V13 H19 V15 L22 12 L19 9 V11 H13 V5 H15 Z"/></svg>'
        )}`;
        // Custom pipette cursor for the Eyedropper tool — a white pipette with
        // a dark outline, its tip at the hotspot, so the sampling tool reads as
        // a live dropper rather than a plain crosshair.
        const eyedropperCursorSvg = `data:image/svg+xml;utf8,${encodeURIComponent(
          '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="black" stroke-width="4" opacity=".85" stroke-linecap="round" stroke-linejoin="round"><path d="m2 22 1-1h3l9-9"/><path d="M3 21v-3l9-9"/><path d="m15 6 3.4-3.4a2.1 2.1 0 1 1 3 3L18 9l.9.9a1 1 0 0 1 0 1.4l-1.6 1.6a1 1 0 0 1-1.4 0L12 8.4a1 1 0 0 1 0-1.4l1.6-1.6a1 1 0 0 1 1.4 0l.9.9Z"/></g><g stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m2 22 1-1h3l9-9"/><path d="M3 21v-3l9-9"/><path d="m15 6 3.4-3.4a2.1 2.1 0 1 1 3 3L18 9l.9.9a1 1 0 0 1 0 1.4l-1.6 1.6a1 1 0 0 1-1.4 0L12 8.4a1 1 0 0 1 0-1.4l1.6-1.6a1 1 0 0 1 1.4 0l.9.9Z"/></g></svg>'
        )}`;
        let cursor = 'crosshair';
        if (state.tool === 'move') cursor = `url("${moveCursorSvg}") 12 12, move`;
        else if (state.tool === 'eyedropper') cursor = `url("${eyedropperCursorSvg}") 2 22, crosshair`;
        else if (state.tool === 'hand') cursor = 'grab';
        else if (state.tool === 'transform') cursor = 'default';
        else if (state.tool === 'zoom') cursor = 'zoom-in';
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

  // Custom scroll indicator for the editor dropdown menus: macOS and headless
  // browsers hide native overlay scrollbars, so draw a thin 3px thumb that
  // tracks scrollTop inside any menu that overflows.
  {
    const MENU_SELECTORS = '.ge-image-menu, .ge-layer-menu, .ge-filter-menu, .ge-edit-menu, .ge-selection-menu, .ge-type-menu, .ge-3d-menu, .ge-view-menu, .ge-window-menu, .ge-plugins-menu, .ge-help-menu, .ge-submenu';
    const wireMenuThumb = (menu) => {
      if (!menu || menu.__geThumb) return;
      menu.__geThumb = true;
      const thumb = document.createElement('span');
      thumb.className = 'ge-menu-thumb';
      menu.appendChild(thumb);
      const sync = () => {
        if (menu.hidden) { thumb.style.display = 'none'; return; }
        const sh = menu.scrollHeight;
        const ch = menu.clientHeight;
        if (sh - ch <= 1) { thumb.style.display = 'none'; return; }
        const h = Math.max(18, Math.round((ch / sh) * ch));
        const maxTop = Math.max(0, ch - h);
        const top = maxTop <= 0 ? 0 : Math.round((menu.scrollTop / (sh - ch)) * maxTop);
        thumb.style.display = 'block';
        thumb.style.height = h + 'px';
        thumb.style.transform = 'translateY(' + top + 'px)';
      };
      menu.addEventListener('scroll', sync, { passive: true });
      if (typeof MutationObserver !== 'undefined') {
        new MutationObserver(sync).observe(menu, { attributes: true, attributeFilter: ['hidden', 'style', 'class'] });
      }
      sync();
    };
    topBar.querySelectorAll(MENU_SELECTORS).forEach(wireMenuThumb);
    window.addEventListener('resize', () => topBar.querySelectorAll(MENU_SELECTORS).forEach((m) => m.dispatchEvent(new Event('scroll'))));
  }

  // Custom scroll indicator for the editor dropdown menus. macOS and headless
  // browsers hide overlay scrollbars, so draw a thin 3px thumb driven by the
  // menu's own scrollTop; it appears only when the menu actually overflows.
  {
    const _menuSel = '.ge-image-menu, .ge-layer-menu, .ge-filter-menu, .ge-edit-menu, .ge-selection-menu, .ge-type-menu, .ge-3d-menu, .ge-view-menu, .ge-window-menu, .ge-plugins-menu, .ge-help-menu';
    const _wireMenuThumb = (menu) => {
      if (!menu || menu.__geThumbWired) return;
      menu.__geThumbWired = true;
      let thumb = menu.querySelector(':scope > .ge-menu-thumb');
      if (!thumb) {
        thumb = document.createElement('span');
        thumb.className = 'ge-menu-thumb';
        menu.appendChild(thumb);
      }
      const sync = () => {
        const sh = menu.scrollHeight;
        const ch = menu.clientHeight;
        if (sh - ch <= 1) { thumb.style.display = 'none'; return; }
        const h = Math.max(18, Math.round((ch / sh) * ch));
        const top = (menu.scrollTop / (sh - ch)) * (ch - h);
        thumb.style.display = 'block';
        thumb.style.height = h + 'px';
        thumb.style.transform = `translateY(${Math.round(top)}px)`;
      };
      menu.addEventListener('scroll', sync);
      if (typeof MutationObserver !== 'undefined') {
        new MutationObserver(sync).observe(menu, { attributes: true, attributeFilter: ['hidden', 'style', 'class'] });
      }
      sync();
    };
    const _wireMenuThumbs = () => topBar.querySelectorAll(_menuSel).forEach(_wireMenuThumb);
    _wireMenuThumbs();
  }

  // Guarantee all twelve Photoshop menus stay visible: if the flat menu bar
  // overflows its track, collapse the right group to icon-only Import/Save
  // before Window/Help scroll out of view.
  {
    const syncTopbarCompact = () => {
      const left = topBar.querySelector('.ge-topbar-left');
      if (!left) return;
      topBar.classList.toggle('ge-topbar-compact', left.scrollWidth - left.clientWidth > 1);
    };
    syncTopbarCompact();
    window.addEventListener('resize', syncTopbarCompact);
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(syncTopbarCompact).observe(topBar);
    }
  }

  // Maximize/restore the editor frame. The editor opens full-viewport by
  // default (Photoshop-style); the toggle remembers a user's windowed
  // preference in localStorage.
  let setEditorWindowed = null;
  {
    const MAX_KEY = 'ge-editor-windowed';
    let windowed = false;
    try { windowed = localStorage.getItem(MAX_KEY) === '1'; } catch (_) {}
    const _MAX_ICON_EXPAND = '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>';
    const _MAX_ICON_RESTORE = '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/>';
    // While the editor is in fullscreen, tuck the app's left nav away (collapse
    // it to its icon rail) so the canvas gets the width; restore it when the
    // editor returns to a window.
    let _sidebarPrevCollapsed = null;
    const _setAppSidebar = (collapsed) => {
      const html = document.documentElement;
      const sb = document.getElementById('sidebar');
      const rail = document.getElementById('icon-rail');
      html.classList.remove('ody-sidebar-off');
      html.classList.toggle('ody-sidebar-mini', collapsed);
      if (sb) sb.classList.toggle('hidden', collapsed);
      if (rail) rail.classList.toggle('rail-hidden', !collapsed);
      try { window.syncRailSide?.(); } catch (_) {}
    };
    const applyMaxState = (isWindowed) => {
      container.classList.toggle('ge-editor-windowed', isWindowed);
      if (!isWindowed) {
        if (_sidebarPrevCollapsed === null) {
          const sb = document.getElementById('sidebar');
          _sidebarPrevCollapsed = sb ? sb.classList.contains('hidden') : false;
        }
        _setAppSidebar(true);
      } else if (_sidebarPrevCollapsed !== null) {
        _setAppSidebar(_sidebarPrevCollapsed);
        _sidebarPrevCollapsed = null;
      }
      const btn = document.getElementById('ge-maximize-toggle');
      if (!btn) return;
      btn.classList.toggle('is-windowed', isWindowed);
      btn.setAttribute('aria-pressed', String(isWindowed));
      btn.title = isWindowed ? 'Maximize editor' : 'Restore editor window';
      // One SVG, swapped between the expand and restore shapes.
      const icon = btn.querySelector('.ge-max-icon');
      if (icon) icon.innerHTML = isWindowed ? _MAX_ICON_RESTORE : _MAX_ICON_EXPAND;
    };
    applyMaxState(windowed);
    document.getElementById('ge-maximize-toggle')?.addEventListener('click', () => {
      windowed = !windowed;
      try { localStorage.setItem(MAX_KEY, windowed ? '1' : '0'); } catch (_) {}
      applyMaxState(windowed);
    });
    setEditorWindowed = (isWindowed) => {
      windowed = !!isWindowed;
      try { localStorage.setItem(MAX_KEY, windowed ? '1' : '0'); } catch (_) {}
      applyMaxState(windowed);
    };
  }

  // Contextual tool options bar — mirrors the right-panel controls for
  // the active tool (Photoshop-style). Its body is populated once the
  // right-panel controls exist, since it delegates to them.
  const optionsBar = _buildOptionsBar();
  optionsBar.addEventListener('ge-editor-prefs-change', () => { _schedulePersist(); });
  container.appendChild(optionsBar);

  // The open documents are surfaced as tabs in the GALLERY tab bar (see
  // _renderEditorDocTabs) — the editor has no separate title-bar strip. The
  // primary actions (Import, Save) live in the topbar row with File/Edit/…,
  // and the maximize control moves up into the modal header.
  {
    const topbarRight = topBar.querySelector('.ge-topbar-right') || topBar;
    const importBtn = topBar.querySelector('#ge-import-topbar');
    const saveWrap = topBar.querySelector('.ge-save-wrap');
    const maxBtn = topBar.querySelector('#ge-maximize-toggle');
    if (importBtn) topbarRight.appendChild(importBtn);
    if (saveWrap) topbarRight.appendChild(saveWrap);
    // The editor's maximize control moves up into the modal header, sitting
    // next to the window's own minimize (_) and close (×) buttons. Adopt their
    // class so it reads as one matched set (no bespoke title-bar sizing).
    if (maxBtn) {
      maxBtn.classList.remove('ge-btn', 'ge-btn-sm', 'ge-win-btn');
      maxBtn.classList.add('modal-minimize-btn', 'ge-header-max-btn');
      const header = state.container?.closest('.modal')?.querySelector('.modal-header')
        || document.querySelector('#gallery-modal .modal-header');
      if (header) {
        // The button is adopted out of the editor container, so rebuilding the
        // editor (File > New, opening another document) used to leave the
        // previous copy behind in the modal header and stack up duplicates.
        header.querySelectorAll('.ge-header-max-btn').forEach((stale) => stale.remove());
        const anchor = header.querySelector('.modal-minimize-btn:not(.ge-header-max-btn)')
          || header.querySelector('.modal-close, .close-btn');
        if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(maxBtn, anchor);
        else header.appendChild(maxBtn);
      }
    }
  }

  // Editor body (toolbar + canvas + panel)
  const editorBody = document.createElement('div');
  editorBody.className = 'ge-editor-body';
  // Wrap the tool rail so a "more tools" chevron can overlay its right edge on
  // phones (the rail scrolls sideways; the fade alone read as a hard clip).
  {
    const toolbarWrap = document.createElement('div');
    toolbarWrap.className = 'ge-toolbar-wrap';
    toolbarWrap.appendChild(toolbar);
    const toolbarMore = document.createElement('button');
    toolbarMore.type = 'button';
    toolbarMore.className = 'ge-tool-more';
    toolbarMore.title = 'More tools';
    toolbarMore.setAttribute('aria-label', 'More tools');
    toolbarMore.textContent = '\u203A';
    toolbarMore.addEventListener('click', () => {
      toolbar.scrollBy({ left: Math.max(160, Math.round(toolbar.clientWidth * 0.6)), behavior: 'smooth' });
    });
    toolbarWrap.appendChild(toolbarMore);
    const syncMore = () => {
      const max = toolbar.scrollWidth - toolbar.clientWidth;
      toolbarMore.classList.toggle('is-visible', max - toolbar.scrollLeft > 8);
    };
    toolbar.addEventListener('scroll', syncMore, { passive: true });
    window.addEventListener('resize', syncMore);
    requestAnimationFrame(syncMore);
    editorBody.appendChild(toolbarWrap);
  }

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

  // Dedicated overlay for the in-progress pen path. Drawing it on its own
  // canvas means adding/moving a point no longer re-composites the whole
  // document (which made the image visibly refresh on every click).
  state.penOverlay = document.createElement('canvas');
  state.penOverlay.className = 'ge-pen-overlay';
  state.penOverlayCtx = state.penOverlay.getContext('2d');
  canvasArea.appendChild(state.penOverlay);

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
  _preflight = createPreflight({ canvasArea });
  _aiSettings = createAiSettings({ container });
  try { window.geEditorAiSettings = _aiSettings; } catch (_) {}
  // On phones the rulers eat scarce canvas width; default them off.
  // The View > Rulers toggle still re-enables them.
  try {
    if (window.matchMedia && window.matchMedia('(max-width: 700px)').matches) {
      state.rulersVisible = false;
    }
  } catch (_) {}
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
      if (_cursorLoupeVisible) _setCursorLoupe(false);
      if (state.tool === 'pen') { _penTool.cancel(); return; }
      if (state.tool === 'path') { _pathTool.cancel(); return; }
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
    onViewportChange: () => {
      _precisionGuides?.redrawRulers();
      if (state.transformActive) _drawTransformHandles();
    },
    onTwoFingerTap: () => undo(),
    onThreeFingerTap: () => redo(),
  });

  editorBody.appendChild(canvasArea);

  // Right panel (controls + layers + resize handle) — full
  // implementation in editor/build/right-panel.js.
  const colorPanels = createColorPanels({
    state,
    canvasArea: state.container?.querySelector('.ge-canvas-area'),
    setColor: (hex) => {
      state.color = hex;
      const pick = document.querySelector('#ge-color-row .ge-color-picker');
      if (pick) { pick.value = hex; pick.dispatchEvent(new Event('input', { bubbles: true })); }
    },
    requestRender: () => composite(),
    zoomIn: () => document.getElementById('ge-zoom-in')?.click(),
    zoomOut: () => document.getElementById('ge-zoom-out')?.click(),
    zoomFit: () => document.getElementById('ge-zoom-100')?.click(),
    applyGradient: () => _applyFullGradient(),
  });
  // Let the module-level _renderLayerPanel keep these panels fresh (histogram et al).
  _refreshSidePanels = () => colorPanels.refresh();
  const studioPanels = createStudioPanels({
    state,
    onSelectLayer: (id) => {
      state.activeLayerId = id;
      state.selectedLayerIds = [id];
      _renderLayerPanel();
      composite();
    },
    onAddAdjustment: (type, anchor) => {
      _addAdjustmentLayer(type, anchor || document.getElementById('ge-add-fx'));
    },
    onPattern: (url, kind) => {
      state.pattern = url;
      if (uiModule?.showToast) uiModule.showToast(`Pattern "${kind}" selected`);
    },
    getTextLayer: () => _activeTextLayer(),
    updateText: (patch) => {
      const layer = _activeTextLayer();
      if (!layer) return;
      _saveState('Edit text');
      _rerenderTextLayer(layer, { ...layer.text, ...patch });
      _renderLayerPanel();
    },
    setLayerOpacity: (value) => {
      const layer = activeLayer();
      if (!layer) return;
      _saveState('Layer opacity');
      layer.opacity = value;
      composite();
      _renderLayerPanel();
    },
    requestRender: () => composite(),
  });
  const actionsPanel = createActionsPanel({
    showToast: (message) => uiModule?.showToast?.(message),
    getLayers: () => state.layers.slice(),
    selectLayer: (id) => {
      state.activeLayerId = id;
      state.selectedLayerIds = [id];
      _renderLayerPanel();
      composite();
    },
  });
  const { rightPanel, controls, mobilePanels, layerPanel, dock } = buildRightPanel({
    controlsHTML: _controlsHTML,
    layerPanelHTML: _layerPanelHTML,
    extraPanels: [
      { id: 'swatches', label: 'Swatches', group: 'Color', groupSize: 268, element: colorPanels.swatches },
      { id: 'gradients', label: 'Gradients', group: 'Color', element: colorPanels.gradients },
      { id: 'patterns', label: 'Patterns', group: 'Color', element: studioPanels.patterns },
      { id: 'properties', label: 'Properties', group: 'Adjustments', groupSize: 232, element: studioPanels.properties },
      { id: 'adjustments', label: 'Adjustments', group: 'Adjustments', element: studioPanels.adjustments },
      { id: 'histogram', label: 'Histogram', group: 'Adjustments', element: colorPanels.histogram },
      { id: 'info', label: 'Info', group: 'Adjustments', element: colorPanels.info },
      { id: 'channels', label: 'Channels', group: 'Panels', groupCollapsed: true, element: colorPanels.channels },
      { id: 'paths', label: 'Paths', group: 'Panels', element: studioPanels.paths },
      { id: 'character', label: 'Character', group: 'Panels', element: studioPanels.character },
      { id: 'navigator', label: 'Navigator', group: 'Panels', element: colorPanels.navigator },
      { id: 'history', label: 'History', group: 'History', groupSize: 240, element: studioPanels.history },
      { id: 'actions', label: 'Actions', group: 'History', element: actionsPanel.element },
    ],
    // "Close <section>" from a dock section's ... menu hides every panel in
    // that group (it re-collapses out of the rail); each one comes back via
    // Window > <panel>, which re-reveals the group.
    onCloseGroup: (groupName) => {
      for (const p of panelVisibility.PANELS) {
        if (p.group === groupName) panelVisibility.setVisible(p.id, false);
      }
    },
  });
  editorBody.appendChild(rightPanel);
  panelVisibility.apply();
  panelVisibility.subscribe(_invalidateCoordsCaches);
  window.gePanelVisibility = panelVisibility;
  // Remember the workspace/window layout the user last chose and restore it on
  // open, so the editor comes back the way they left it. A fresh session has no
  // stored key, so the default arrangement is left untouched. Deferred a frame
  // so the dock panels exist and the visibility tags stick. The right-panel
  // width only applies on desktop (the phone layout is a full-width sheet).
  try {
    const savedWorkspace = localStorage.getItem('ge-active-workspace');
    if (savedWorkspace) {
      const restoreWorkspace = () => _applyWorkspace(savedWorkspace, {
        dock,
        rightPanel: window.innerWidth > 700 ? rightPanel : null,
        setPanelVisible: (id, visible) => panelVisibility.setVisible(id, visible),
      });
      requestAnimationFrame(() => { try { restoreWorkspace(); panelVisibility.apply(); } catch (_) {} });
    }
  } catch (_) {}
  container.appendChild(editorBody);
  container.appendChild(_buildZoomFooter());
  // Mobile: the footer's undo/redo/history pill is lifted above the bottom
  // sheets so it stays reachable, but that made it float over the Layers
  // sheet's Blend row. Move it into the layer-sheet header (which has room on
  // the right) so it never covers a control.
  if (window.innerWidth <= 700) {
    try {
      const fh = container.querySelector('.ge-footer-history');
      const hdr = container.querySelector('.ge-layers-header');
      if (fh && hdr) hdr.appendChild(fh);
    } catch (_) {}
  }
  // Populate the docked History timeline now that the panel exists (it then
  // refreshes on every undo/redo/edit via refreshHistoryPanelIfOpen).
  try { _refreshHistoryPanelIfOpen(); } catch (_) {}
  // Fade the overflowing edge of the menu row + tool rail so the off-screen
  // File…Help menus and tools are discoverable on phones (audit #5).
  try {
    attachScrollAffordance(container.querySelector('.ge-topbar-left'));
    attachScrollAffordance(container.querySelector('.ge-toolbar-scroll'));
  } catch (_) {}
  {
    const initialStatusTool = document.getElementById('ge-status-tool');
    if (initialStatusTool) initialStatusTool.textContent = _TOOLS[state.tool]?.label || state.tool || '';
  }
  // Preflight checklist: greet the user on open (unless dismissed) and
  // expose a footer button to re-open it.
  try {
    const footer = container.querySelector('.ge-editor-footer');
    if (footer) {
      const setupBtn = document.createElement('button');
      setupBtn.type = 'button';
      setupBtn.id = 'ge-setup-btn';
      setupBtn.className = 'ge-setup-btn';
      setupBtn.title = 'Editor setup checklist';
      setupBtn.textContent = 'Setup';
      setupBtn.addEventListener('click', () => _preflight?.open());
      footer.appendChild(setupBtn);
      const aiBtn = document.createElement('button');
      aiBtn.type = 'button';
      aiBtn.id = 'ge-ai-settings-btn';
      aiBtn.className = 'ge-setup-btn';
      aiBtn.title = 'AI settings — image edit and agent models';
      aiBtn.textContent = 'AI settings';
      aiBtn.addEventListener('click', () => _aiSettings?.open());
      footer.appendChild(aiBtn);
    }
    _preflight?.maybeOpen();
  } catch (_) {}
  try { _initControlKit(container); } catch (_) {}
  let _lastLayoutDesktop = null;
  const _syncResponsiveLayout = (force = false) => {
    _invalidateCoordsCaches();
    const isDesktop = window.innerWidth > 700;
    if (!force && isDesktop === _lastLayoutDesktop) return;
    _lastLayoutDesktop = isDesktop;

    const footer = container.querySelector('.ge-editor-footer');
    const hdr = container.querySelector('.ge-layers-header');
    const fh = container.querySelector('.ge-footer-history');
    const fz = container.querySelector('.ge-footer-zoom');
    const setupBtn = document.getElementById('ge-setup-btn');
    const aiBtn = document.getElementById('ge-ai-settings-btn');
    const optionsBar = container.querySelector('.ge-options-bar');

    if (isDesktop) {
      // Reparent controls into optionsBar
      if (controls && optionsBar && controls.parentElement !== optionsBar) {
        optionsBar.appendChild(controls);
      }
      controls?.classList.add('ge-controls--in-options');
      controls?.classList.remove('dismissed');
      optionsBar?.classList.add('ge-options-bar--has-controls');

      // Right panel dock mode
      rightPanel?.classList.add('ge-right-panel--dock');
      if (dock?.dock) dock.dock.style.display = '';
      if (mobilePanels) mobilePanels.style.display = 'none';
      const panelsBtn = layerPanel?.querySelector('.ge-layers-panels-btn');
      if (panelsBtn) panelsBtn.style.display = 'none';

      // Clear mobile footer docking on desktop
      if (fh && footer && fh.parentElement !== footer) {
        footer.insertBefore(fh, footer.firstChild);
      }
      _placeMobileFooter();
    } else {
      // Reparent controls into editor container (mobile sheet)
      if (controls && controls.parentElement !== container) {
        container.appendChild(controls);
      }
      controls?.classList.remove('ge-controls--in-options');
      optionsBar?.classList.remove('ge-options-bar--has-controls');

      // Right panel mobile sheet mode
      rightPanel?.classList.remove('ge-right-panel--dock');
      if (dock?.dock) dock.dock.style.display = 'none';
      if (mobilePanels) mobilePanels.style.display = '';
      const panelsBtn = layerPanel?.querySelector('.ge-layers-panels-btn');
      if (panelsBtn) panelsBtn.style.display = '';

      if (fh && hdr && fh.parentElement !== hdr) {
        hdr.appendChild(fh);
      }
      // Dock footer flush to sheet on mobile
      _placeMobileFooter();
    }
  };

  _syncResponsiveLayout(true);
  try {
    const _mobileMql = window.matchMedia('(max-width: 700px)');
    _mobileMql.addEventListener('change', () => _syncResponsiveLayout(true));
  } catch (_) {}
  window.addEventListener('resize', () => _syncResponsiveLayout(false), { passive: true });

  // Crop options: aspect-ratio presets (Free / Original / 1:1 / 4:5 / 16:9).
  // Choosing one locks new crop drags to the ratio and refits an existing
  // crop rect (centred, clamped to the canvas). Wired after the controls
  // panel has been relocated into the options bar so the row is mounted.
  try {
    const presetRow = document.getElementById('ge-crop-presets');
    if (presetRow) {
      const ratioFor = (preset) => {
        if (preset === 'original') {
          return state.imgWidth && state.imgHeight ? state.imgWidth / state.imgHeight : null;
        }
        const m = /^(\d+):(\d+)$/.exec(preset || '');
        return m ? Number(m[1]) / Number(m[2]) : null;
      };
      const applyCropPreset = (preset) => {
        state.cropAspectPreset = preset;
        presetRow.querySelectorAll('.ge-crop-preset').forEach((b) => {
          b.classList.toggle('active', b.dataset.cropPreset === preset);
        });
        const ratio = ratioFor(preset);
        if (ratio && state.mainCanvas) {
          const cw = state.mainCanvas.width;
          const ch = state.mainCanvas.height;
          // No crop rect yet (the user picked a ratio before dragging): start
          // from a centred rect that already matches the requested ratio.
          let rect = state.cropRect;
          if (!(rect && rect.w > 5 && rect.h > 5)) {
            let w = cw;
            let h = w / ratio;
            if (h > ch) { h = ch; w = h * ratio; }
            rect = { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
          }
          const cx = rect.x + rect.w / 2;
          const cy = rect.y + rect.h / 2;
          let w = rect.w;
          let h = w / ratio;
          if (h > ch) { h = ch; w = h * ratio; }
          if (w > cw) { w = cw; h = w / ratio; }
          state.cropRect = {
            x: Math.max(0, Math.min(cx - w / 2, cw - w)),
            y: Math.max(0, Math.min(cy - h / 2, ch - h)),
            w,
            h,
          };
          state.cropStart = { x: state.cropRect.x, y: state.cropRect.y };
          state.cropEnd = { x: state.cropRect.x + w, y: state.cropRect.y + h };
          composite();
          _showCropApply();
        }
      };
      presetRow.addEventListener('click', (e) => {
        const btn = e.target.closest('.ge-crop-preset');
        if (btn) {
          const customRatioInput = document.getElementById('ge-crop-custom-ratio');
          if (customRatioInput) customRatioInput.value = '';
          applyCropPreset(btn.dataset.cropPreset);
        }
      });
      // Free-form ratio: type e.g. "3:2", "21:9" or "5x4" and press Enter.
      const customRatioInput = document.getElementById('ge-crop-custom-ratio');
      if (customRatioInput) {
        const applyCustomRatio = () => {
          const m = /^(\d+)\s*[:xX/]\s*(\d+)$/.exec((customRatioInput.value || '').trim());
          if (!m) return;
          const w = Number(m[1]);
          const h = Number(m[2]);
          if (!(w > 0) || !(h > 0)) return;
          applyCropPreset(`${w}:${h}`);
        };
        customRatioInput.addEventListener('change', applyCustomRatio);
        customRatioInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') { e.preventDefault(); applyCustomRatio(); }
        });
      }
    }
  } catch (_) {}
  // Photoshop keeps the layer transform (X/Y/W/H) in the Properties panel,
  // not the tool options bar. Move the geometry block there so the options
  // bar stays a single thin row.
  {
    const geometrySection = controls.querySelector('#ge-layer-geometry-section');
    const propsPanel = studioPanels?.properties;
    if (geometrySection && propsPanel?.appendChild) {
      propsPanel.insertBefore(geometrySection, propsPanel.firstChild);
      geometrySection.style.display = '';
    }
  }
  _renderOptionsBar(optionsBar, state.tool);
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
  {
    const fgSwatch = document.getElementById('ge-fg-color');
    const bgSwatch = document.getElementById('ge-bg-color');
    const mainPicker = controls.querySelector('.ge-color-picker');
    const _syncMainPicker = (hex) => { if (mainPicker) mainPicker.value = hex; };
    const _syncFgSwatch = (hex) => { if (fgSwatch) fgSwatch.value = hex; };
    // The strip's Color swatch and the sidebar foreground swatch are the
    // same "current colour" — keep them mirrored in both directions.
    mainPicker?.addEventListener('input', (e) => {
      state.color = e.target.value;
      _syncFgSwatch(e.target.value);
    });
    // Left-rail foreground/background color swatches (Photoshop-style).
    // The foreground swatch mirrors `state.color`; the swap button flips
    // the two swatches and keeps the main color picker in sync.
    if (fgSwatch) {
      fgSwatch.value = state.color || '#000000';
      _syncMainPicker(fgSwatch.value);
      fgSwatch.addEventListener('input', (e) => {
        state.color = e.target.value;
        _syncMainPicker(e.target.value);
      });
      try { attachColorPicker(fgSwatch); } catch (_) {}
    }
    if (bgSwatch) { try { attachColorPicker(bgSwatch); } catch (_) {} }
    const _swapColors = () => {
      if (!fgSwatch || !bgSwatch) return;
      const tmp = fgSwatch.value;
      fgSwatch.value = bgSwatch.value;
      bgSwatch.value = tmp;
      state.color = fgSwatch.value;
      _syncMainPicker(fgSwatch.value);
    };
    document.getElementById('ge-swap-colors')?.addEventListener('click', _swapColors);
    window.geSwapColors = _swapColors;
    // Photoshop 'D': reset to the default black foreground / white background.
    window.geResetColors = () => {
      if (fgSwatch) fgSwatch.value = '#000000';
      if (bgSwatch) bgSwatch.value = '#ffffff';
      state.color = '#000000';
      _syncMainPicker('#000000');
    };
  }
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
  _layerGeometry.wire(document);
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
      composite();
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
    exportToGallery, downloadPNG, exportPNGFile,
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

  // Text/Shape "More" menus: the strip only shows the primary fields, so the
  // rare rows live in a popover that never changes the bar height.
  [
    ['ge-text-more-toggle', 'ge-text-more'],
    ['ge-shape-more-toggle', 'ge-shape-more'],
  ].forEach(([btnId, menuId]) => {
    const btn = document.getElementById(btnId);
    const menu = document.getElementById(menuId);
    if (!btn || !menu) return;
    const close = () => {
      menu.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      btn.classList.remove('active');
    };
    btn.addEventListener('click', (event) => {
      event.stopPropagation();
      const open = menu.hidden;
      menu.hidden = !open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      btn.classList.toggle('active', open);
    });
    document.addEventListener('pointerdown', (event) => {
      if (menu.hidden || !menu.isConnected) return;
      if (menu.contains(event.target) || btn.contains(event.target)) return;
      close();
    });
    // Escape is owned by the Gallery's earliest capture listener, which
    // delegates to window.__galleryEditorHandleEscape; that handler closes an
    // open options "More" panel (see below) before anything else.
  });

  // Liquify: mode + dab size/strength live in state; the sliders in the tool
  // section mirror it (the options bar copies the same elements).
  const _liquifyMode = document.getElementById('ge-liquify-mode');
  const _liquifySize = document.getElementById('ge-liquify-size');
  const _liquifyStrength = document.getElementById('ge-liquify-strength');
  if (_liquifyMode) {
    _liquifyMode.value = state.liquifyMode || 'push';
    _liquifyMode.addEventListener('change', () => { state.liquifyMode = _liquifyMode.value; });
  }
  if (_liquifySize) {
    const sync = () => {
      state.liquifySize = Math.max(4, Number(_liquifySize.value) || 120);
      const out = document.getElementById('ge-liquify-size-label');
      if (out) out.textContent = Math.round(state.liquifySize) + 'px';
    };
    _liquifySize.value = String(state.liquifySize || 120);
    _liquifySize.addEventListener('input', sync);
    sync();
  }
  if (_liquifyStrength) {
    const sync = () => {
      state.liquifyStrength = Math.max(1, Math.min(100, Number(_liquifyStrength.value) || 50));
      const out = document.getElementById('ge-liquify-strength-label');
      if (out) out.textContent = Math.round(state.liquifyStrength) + '%';
    };
    _liquifyStrength.value = String(state.liquifyStrength || 50);
    _liquifyStrength.addEventListener('input', sync);
    sync();
  }

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
    rotateAllLayersArbitrary: (deg) => _rotateAllLayersArbitrary(deg),
    openPerspectiveCrop: () => _openPerspectiveCrop(),
    flipAllLayers: (axis) => _flipAllLayers(axis),
    rotateActiveLayer: (deg) => _rotateActiveLayer(deg),
    flipActiveLayer: (axis) => _flipActiveLayer(axis),
    applyGaussianBlur: () => _applyGaussianBlur(),
    applyDehaze: () => _applyDehaze(),
    applyZoomBlur: () => _applyZoomBlur(),
    applyMotionBlur: () => _applyMotionBlur(),
    applyNoise: () => _applyNoise(),
    applyDespeckle: () => _applyDespeckle(),
    applyPixelate: () => _applyPixelate(),
    applyCrystallize: () => _applyCrystallize(),
    applyEmboss: () => _applyEmboss(),
    applyFindEdges: () => _applyFindEdges(),
    applyGlowingEdges: () => _applyGlowingEdges(),
    applyOffset: () => _applyOffset(),
    applyBoxBlur: () => _applyBoxBlur(),
    applySurfaceBlur: () => _applySurfaceBlur(),
    applyCustom: () => _applyCustom(),
    applyMaximum: () => _applyMaximum(),
    applyMinimum: () => _applyMinimum(),
    applyMedian: () => _applyMedian(),
    applyPolarCoordinates: () => _applyPolarCoordinates(),
    applyDifferenceClouds: () => _applyDifferenceClouds(),
    applyLensFlare: () => _applyLensFlare(),
    applyFibers: () => _applyFibers(),
    applyOilPaint: () => _applyOilPaint(),
    applyLensCorrection: () => _applyLensCorrection(),
    applyReplaceColor: () => _applyReplaceColor(),
    applyDiffuse: () => _applyDiffuse(),
    applyOceanRipple: () => _applyOceanRipple(),
    applyGlass: () => _applyGlass(),
    applyMosaicTiles: () => _applyMosaicTiles(),
    applyWind: () => _applyWind(),
    applyTraceContour: () => _applyTraceContour(),
    applyHalftonePattern: () => _applyHalftonePattern(),
    applyReticulation: () => _applyReticulation(),
    applyShear: () => _applyShear(),
    applyNotePaper: () => _applyNotePaper(),
    applyCharcoal: () => _applyCharcoal(),
    applyChalkCharcoal: () => _applyChalkCharcoal(),
    applyGraphicPen: () => _applyGraphicPen(),
    applyPhotocopy: () => _applyPhotocopy(),
    applyWaterPaper: () => _applyWaterPaper(),
    applySharpenMore: () => _applySharpenMore(),
    applyPosterEdges: () => _applyPosterEdges(),
    applyCutout: () => _applyCutout(),
    applyInkOutlines: () => _applyInkOutlines(),
    applyStainedGlass: () => _applyStainedGlass(),
    applyGrain: () => _applyGrain(),
    applyTexturizer: () => _applyTexturizer(),
    applyPatchwork: () => _applyPatchwork(),
    applyCraquelure: () => _applyCraquelure(),
    applyCrosshatch: () => _applyCrosshatch(),
    applySolarize: () => _applySolarize(),
    applyTwirl: () => _applyTwirl(),
    applyPinch: () => _applyPinch(),
    applySpherize: () => _applySpherize(),
    applyRipple: () => _applyRipple(),
    applyClouds: () => _applyClouds(),
    applySharpen: () => _applySharpen(),
    applySharpenEdges: () => _applySharpenEdges(),
    applyUnsharpMask: () => _applyUnsharpMask(),
    addRetainedGaussianBlur: (presetName) => _addRetainedGaussianBlur(presetName),
    addRetainedEffect: (type, presetName) => _addRetainedEffect(type, presetName),
    selectAll: _selectAllSelection,
    deselectSelection: () => _deselectSelection(),
    reselectSelection: _reselectSelection,
    invertSelection: _invertSelection,
    transformSelection: _startSelectionTransform,
    refineSelection: _refineSelection,
    colorRange: _colorRangeSelection,
    selectAndMask: _selectAndMask,
    setColorMode: (mode) => _setColorMode(mode),
    saveNamedSelection: _saveNamedSelection,
    loadNamedSelection: _loadNamedSelection,
    deleteNamedSelection: _deleteNamedSelection,
    uiModule,
  });
  _renderSavedSelectionsMenu = renderSelectionMenu;

  // Combine the selected shape layers with a boolean op (unite /
  // subtract / intersect / exclude), rasterizing the result into the
  // bottom-most selected layer.
  function _combineSelectedShapes(op) {
    const ids = Array.isArray(state.selectedLayerIds) ? state.selectedLayerIds : [];
    const layers = ids.map((id) => state.layers.find((layer) => layer.id === id)).filter(Boolean);
    if (layers.length < 2) {
      uiModule?.showToast?.('Select two or more layers to combine.');
      return;
    }
    const order = new Map(state.layers.map((layer, index) => [layer.id, index]));
    layers.sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0));
    const offsets = layers.map((layer) => state.layerOffsets.get(layer.id) || { x: 0, y: 0 });
    const result = _combineLayers(layers, op, { width: state.imgWidth, height: state.imgHeight, offsets });
    if (!result) return;
    const base = layers[0];
    base.kind = 'raster';
    base.text = null;
    base.shape = null;
    base.canvas.width = state.imgWidth;
    base.canvas.height = state.imgHeight;
    base.ctx = base.canvas.getContext('2d');
    base.ctx.clearRect(0, 0, base.canvas.width, base.canvas.height);
    base.ctx.drawImage(result, 0, 0);
    state.layerOffsets.set(base.id, { x: 0, y: 0 });
    const remove = new Set(layers.slice(1).map((layer) => layer.id));
    state.layers = state.layers.filter((layer) => !remove.has(layer.id));
    if (state.layers[0]?.id !== base.id) {
      state.layers = [base, ...state.layers.filter((layer) => layer.id !== base.id)];
    }
    state.selectedLayerIds = [base.id];
    state.activeLayerId = base.id;
    _saveState('Combine shapes');
    composite();
    _renderLayerPanel();
    uiModule?.showToast?.(`Shapes combined (${op}).`);
  }

  // Reflect "shown" state as a checkmark on the Window dropdown rows, using the
  // same `.active` + `ge-view-check` glyph the View menu uses. Kept here (not in
  // wire-extra-menus) so that file stays a state-free delegator.
  function _syncWindowMenuChecks(menu) {
    if (!menu) return;
    let activeWorkspace = 'essentials';
    try { activeWorkspace = localStorage.getItem('ge-active-workspace') || 'essentials'; } catch {}
    menu.querySelectorAll('[data-window-action]').forEach((item) => {
      const action = item.dataset.windowAction;
      let on = false;
      if (action.startsWith('workspace-')) on = action.slice('workspace-'.length) === activeWorkspace;
      else if (action === 'history') on = !!state.historyPanelEl;
      else if (action === 'shortcuts') on = !!_shortcutsPopover?.isOpen?.();
      else if (action === 'quick-edit') {
        const wrap = document.getElementById('ge-ai-command');
        on = !!wrap && !wrap.classList.contains('ge-ai-command-collapsed');
      }
      else if (panelVisibility.PANEL_IDS.includes(action)) on = panelVisibility.isVisible(action);
      item.classList.toggle('active', on);
      item.setAttribute('aria-checked', on ? 'true' : 'false');
    });
  }

  // "Save Current" workspace: snapshot the current panel visibility, active
  // dock tab and right-panel width into a custom slot the Workspace flyout can
  // re-apply (the restore-on-open path reads it too).
  const _saveCurrentWorkspace = () => {
    const panels = panelVisibility.PANEL_IDS.filter((id) => panelVisibility.isVisible(id));
    const activeTab = state.container?.querySelector?.('.ge-dock-tab.active')?.dataset?.panel || 'layers';
    let width = 300;
    const w = rightPanel ? parseFloat(rightPanel.style.width) : NaN;
    if (Number.isFinite(w)) width = Math.round(w);
    try {
      localStorage.setItem('ge-workspace-custom', JSON.stringify({ label: 'Custom', tab: activeTab, width, panels }));
      localStorage.setItem('ge-active-workspace', 'custom');
    } catch { /* storage unavailable */ }
    uiModule?.showToast?.('Workspace saved as Custom');
  };

  wireExtraMenus({
    closeOtherTopbarMenus: _closeOtherTopbarMenus,
    registerDocClickAway: _registerDocClickAway,
    dock,
    combineShapes: (op) => _combineSelectedShapes(op),
    exportLayeredPsd: () => _exportLayeredPsd(),
    uiModule,
    workspace: (name) => _applyWorkspace(name, { dock, rightPanel, setPanelVisible: (id, visible) => panelVisibility.setVisible(id, visible) }),
    saveWorkspace: _saveCurrentWorkspace,
    syncWindowChecks: _syncWindowMenuChecks,
    togglePanel: (id) => panelVisibility.toggle(id),
    // Window > Quick Edit mirrors the bar's own collapse control: unchecking
    // hides the prompt, checking it brings the input back.
    toggleQuickEdit: () => {
      const wrap = document.getElementById('ge-ai-command');
      if (!wrap) return;
      const collapsed = wrap.classList.contains('ge-ai-command-collapsed');
      wrap.dispatchEvent(new CustomEvent(collapsed ? 'ge-ai-command-open' : 'ge-ai-command-close'));
    },
    // File > New: flush the current draft, then build a blank document.
    newDocument: (w, h) => {
      if (!(w > 0) || !(h > 0)) return;
      if (state.layers.length) { try { _persistDraft(); } catch (_) {} }
      openEditor(null, null, { w: Math.round(w), h: Math.round(h) }, 'Untitled');
    },
    promptNewDocument: () => _promptCanvasSize({
      title: 'New document',
      okLabel: 'Create',
      initialW: 1920,
      initialH: 1080,
      showUnits: true,
      showPresets: true,
    }),
    newFromClipboard: async () => {
      try {
        const items = await navigator.clipboard.read();
        for (const item of items) {
          const type = item.types.find((t) => t.startsWith('image/'));
          if (!type) continue;
          const blob = await item.getType(type);
          const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
          });
          const img = new Image();
          await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = dataUrl; });
          if (state.layers.length) { try { _persistDraft(); } catch (_) {} }
          openEditor(dataUrl, null, { w: img.naturalWidth, h: img.naturalHeight }, 'Clipboard');
          return;
        }
        uiModule?.showError?.('No image found on the clipboard');
      } catch (_) {
        uiModule?.showError?.('Could not read the clipboard');
      }
    },
  });

  // Command palette (Ctrl/Cmd+K) — full implementation in
  // editor/build/command-palette.js.
  const commandPalette = createCommandPalette({
    commands: [
      ...Array.from(state.container.querySelectorAll('.ge-tool-btn[data-tool]')).map((btn) => ({
        label: `Tool: ${btn.dataset.tool}`,
        run: () => btn.click(),
      })),
      ...(dock ? [
        { label: 'Panel: Tool Options', run: () => dock.show('options') },
        { label: 'Panel: Layers', run: () => dock.show('layers') },
        { label: 'Panel: Swatches', run: () => dock.show('swatches') },
        { label: 'Panel: Gradients', run: () => dock.show('gradients') },
        { label: 'Panel: Channels', run: () => dock.show('channels') },
        { label: 'Panel: Histogram', run: () => dock.show('histogram') },
        { label: 'Panel: Info', run: () => dock.show('info') },
        { label: 'Panel: Navigator', run: () => dock.show('navigator') },
      ] : []),
      { label: 'Save over original', run: () => document.getElementById('ge-save')?.click() },
      { label: 'Export image', run: () => document.getElementById('ge-download')?.click() },
      { label: 'Undo', run: () => document.getElementById('ge-undo')?.click() },
      { label: 'Redo', run: () => document.getElementById('ge-redo')?.click() },
      { label: 'History', run: () => document.getElementById('ge-history-btn')?.click() },
      { label: 'Keyboard shortcuts', run: () => document.getElementById('ge-shortcuts-btn')?.click() },
      { label: 'Zoom to fit / 100%', run: () => document.getElementById('ge-zoom-100')?.click() },
    ],
  });
  const onCommandPaletteKey = (event) => {
    if ((event.ctrlKey || event.metaKey) && (event.key === 'k' || event.key === 'K')) {
      if (!state.editorOpen) return;
      event.preventDefault();
      commandPalette.toggle();
    }
  };
  document.addEventListener('keydown', onCommandPaletteKey);

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
      composite();
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
  // The SAM action row (visibility / clear / mask) now shares the wand's
  // unified action-row wiring inside wireSelectionControls — nothing extra to
  // bind here.
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

  // Free Transform entered from a shortcut must swap the options strip the
  // same way picking the Transform tool does — otherwise the previous tool's
  // settings (e.g. Text) stay on screen and overlap the transform controls.
  const _enterTransform = () => {
    if (state.transformActive) return;
    const btn = toolbar.querySelector('.ge-tool-flyout-item[data-tool-option="transform"]')
      || toolbar.querySelector('[data-tool="transform"]');
    if (btn) btn.click();
    else _startTransform();
  };
  // One-time-per-session hint so the shortcut is discoverable (Ctrl+T is
  // reserved by the browser, so Ctrl+Alt+T is the chord that works).
  const _maybeShowTransformHint = () => {
    try {
      if (sessionStorage.getItem('ge-transform-hint')) return;
      sessionStorage.setItem('ge-transform-hint', '1');
    } catch (_) { /* storage unavailable */ }
    // `toast-transform-hint` pins this one message to a single spot under the
    // canvas; without it the generic panel rules moved it between the
    // top-right corner and the bottom banner.
    uiModule?.showToast?.('Free Transform — Enter applies, Esc cancels. Shortcut: Ctrl+Alt+T', { toastClass: 'toast-transform-hint' });
  };

  // Holding Alt with a paint tool borrows the eyedropper, so switch to the
  // sampling cursor (and hide the brush ring) while Alt is held.
  const _syncAltEyedropper = (on) => {
    document.body.classList.toggle('ge-alt-eyedropper', !!on && _altEyedropperTools.has(state.tool));
  };
  if (!state._altEyedropperWired) {
    state._altEyedropperWired = true;
    document.addEventListener('keydown', (ev) => { if (state.editorOpen) _syncAltEyedropper(ev.altKey); });
    document.addEventListener('keyup', (ev) => { if (state.editorOpen) _syncAltEyedropper(ev.altKey); });
    window.addEventListener('blur', () => _syncAltEyedropper(false));
  }

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
    startTransform: _enterTransform,
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

  // Programmatic control surface (Goal 4). A single dispatcher every UI
  // action and every agent/MCP command can go through. Handlers wrap the
  // in-scope editor operations and return JSON-friendly results.
  // Masked pixel helpers: apply a filter/adjustment only within the
  // document-space selection (state.wandMask), so "blur the background"
  // and "dramatic sky" edit regionally instead of the whole layer.
  const _maskCanvasForLayer = (layer, invert, featherPx = 0) => {
    const w = layer.canvas.width, h = layer.canvas.height;
    const mask = document.createElement('canvas');
    mask.width = w; mask.height = h;
    const mctx = mask.getContext('2d');
    const docMask = state.wandMask;
    if (docMask) {
      const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
      if (featherPx > 0) mctx.filter = `blur(${featherPx}px)`;
      mctx.drawImage(docMask, -off.x, -off.y);
      mctx.filter = 'none';
    } else {
      mctx.fillStyle = '#ffffff';
      mctx.fillRect(0, 0, w, h);
    }
    if (invert) {
      const id = mctx.getImageData(0, 0, w, h);
      const d = id.data;
      for (let i = 3; i < d.length; i += 4) d[i] = 255 - d[i];
      mctx.putImageData(id, 0, 0);
    }
    return mask;
  };
  const _snapshotLayer = (layer) => {
    const snap = document.createElement('canvas');
    snap.width = layer.canvas.width; snap.height = layer.canvas.height;
    snap.getContext('2d').drawImage(layer.canvas, 0, 0);
    return snap;
  };
  const _commitMaskedEffect = (layer, snap, effectCanvas, invert, featherPx = 0) => {
    const w = layer.canvas.width, h = layer.canvas.height;
    if (featherPx <= 0 && state.wandMask) featherPx = 2;
    const mask = _maskCanvasForLayer(layer, invert, featherPx);
    const ectx = effectCanvas.getContext('2d');
    ectx.globalCompositeOperation = 'destination-in';
    ectx.drawImage(mask, 0, 0);
    ectx.globalCompositeOperation = 'source-over';
    layer.ctx.clearRect(0, 0, w, h);
    layer.ctx.drawImage(snap, 0, 0);
    layer.ctx.drawImage(effectCanvas, 0, 0);
  };
  // Halo-free background blur via normalized masked blur (edge extension).
  // Mask the subject OUT (alpha 0) so it contributes nothing, then blur the
  // background-only image. Because canvas pixels are premultiplied, the
  // browser's blur already performs the normalized convolution
  // blur(I*W)/blur(W): every output colour is the weighted average of the
  // neighbouring BACKGROUND pixels, which propagates real background colours
  // inward across the subject edge (no flat fill colour, so no bright halo).
  // The sharp subject is redrawn on top by _commitMaskedEffect.
  const _backgroundBlurPlate = (layer, snap, radius) => {
    const w = layer.canvas.width, h = layer.canvas.height;
    const bgW = _maskCanvasForLayer(layer, true, 0);
    const iw = document.createElement('canvas');
    iw.width = w; iw.height = h;
    const ictx = iw.getContext('2d');
    ictx.drawImage(snap, 0, 0);
    ictx.globalCompositeOperation = 'destination-in';
    ictx.drawImage(bgW, 0, 0);
    const plate = document.createElement('canvas');
    plate.width = w; plate.height = h;
    const pctx = plate.getContext('2d');
    _gaussianBlur(iw, { radius }, pctx);
    // Opaque plate; the subject region is masked out by _commitMaskedEffect.
    const img = pctx.getImageData(0, 0, w, h);
    const d = img.data;
    for (let i = 3; i < d.length; i += 4) d[i] = 255;
    pctx.putImageData(img, 0, 0);
    return plate;
  };
  const _applyMaskedAdjust = (layer, opts = {}) => {
    const w = layer.canvas.width, h = layer.canvas.height;
    const snap = _snapshotLayer(layer);
    const out = document.createElement('canvas');
    out.width = w; out.height = h;
    const octx = out.getContext('2d');
    octx.drawImage(snap, 0, 0);
    const img = octx.getImageData(0, 0, w, h);
    const d = img.data;
    const brightness = Number(opts.brightness) || 0;
    const contrast = Number(opts.contrast) || 0;
    let saturation = opts.saturation == null ? 1 : Number(opts.saturation);
    if (!Number.isFinite(saturation)) saturation = 1;
    // Photoshop-style "hue-saturation" adjustments pass saturation as a DELTA
    // percentage (0 = leave saturation alone, -100 = grey, +100 = double). The
    // legacy masked-adjust callers instead pass a FACTOR (>1 treated as a
    // percentage). Disambiguate with the mode so a hue-only recolour (hue delta
    // + saturation:0) keeps its colour instead of flattening to grey.
    if (opts.mode === 'hue-saturation') saturation = 1 + saturation / 100;
    else if (saturation > 3) saturation = 1 + saturation / 100;
    saturation = Math.max(0, Math.min(3, saturation));
    const warmth = Number(opts.warmth) || 0;
    const hue = Number(opts.hue) || 0;
    // Source-hue-limited rotation: only rotate pixels whose hue is near the
    // object's source hue (with a soft falloff and a saturation gate), so a
    // recolour keeps windows, tyres and chrome their own colour.
    let maskAlpha = null;
    if (hue) {
      try {
        const mc = _maskCanvasForLayer(layer, state.wandMask ? !!opts.invert : false, 2);
        if (mc && mc.width === w && mc.height === h) {
          maskAlpha = mc.getContext('2d').getImageData(0, 0, w, h).data;
        }
      } catch (_) { maskAlpha = null; }
    }
    let hueCenter = null; // 0..1
    if (hue) {
      if (opts.sourceHue != null && Number.isFinite(Number(opts.sourceHue))) {
        hueCenter = (((Number(opts.sourceHue) % 360) + 360) % 360) / 360;
      } else if (maskAlpha) {
        const bins = new Array(36).fill(0);
        for (let i = 0; i < d.length; i += 4) {
          if (maskAlpha[i + 3] < 128) continue;
          const rr0 = d[i] / 255, gg0 = d[i + 1] / 255, bb0 = d[i + 2] / 255;
          const max0 = Math.max(rr0, gg0, bb0), min0 = Math.min(rr0, gg0, bb0);
          if (max0 === min0) continue;
          const dd0 = max0 - min0;
          const l0 = (max0 + min0) / 2;
          const s0 = l0 > 0.5 ? dd0 / (2 - max0 - min0) : dd0 / (max0 + min0);
          if (s0 < 0.18) continue;
          let hh0;
          if (max0 === rr0) hh0 = (gg0 - bb0) / dd0 + (gg0 < bb0 ? 6 : 0);
          else if (max0 === gg0) hh0 = (bb0 - rr0) / dd0 + 2;
          else hh0 = (rr0 - gg0) / dd0 + 4;
          bins[Math.min(35, Math.floor((hh0 / 6) * 36))] += 1;
        }
        let best = -1, bestN = 0;
        for (let k = 0; k < 36; k++) if (bins[k] > bestN) { bestN = bins[k]; best = k; }
        if (best >= 0) hueCenter = (best + 0.5) / 36;
      }
    }
    const cFactor = (259 * (contrast + 255)) / (255 * (259 - contrast));
    const hue2rgb = (p2, q2, t2) => {
      if (t2 < 0) t2 += 1; if (t2 > 1) t2 -= 1;
      if (t2 < 1 / 6) return p2 + (q2 - p2) * 6 * t2;
      if (t2 < 1 / 2) return q2;
      if (t2 < 2 / 3) return p2 + (q2 - p2) * (2 / 3 - t2) * 6;
      return p2;
    };
    for (let i = 0; i < d.length; i += 4) {
      let r = d[i], g = d[i + 1], b = d[i + 2];
      if (brightness) { r += brightness; g += brightness; b += brightness; }
      if (contrast) { r = cFactor * (r - 128) + 128; g = cFactor * (g - 128) + 128; b = cFactor * (b - 128) + 128; }
      if (warmth) { r += warmth; b -= warmth; }
      if (saturation !== 1) {
        const gray = 0.299 * r + 0.587 * g + 0.114 * b;
        r = gray + (r - gray) * saturation;
        g = gray + (g - gray) * saturation;
        b = gray + (b - gray) * saturation;
      }
      if (hue) {
        // Rotate hue in HSL, preserving lightness and saturation — this is
        // what turns a shaded red object blue without flattening it to a fill.
        const rr = Math.max(0, Math.min(255, r)) / 255;
        const gg = Math.max(0, Math.min(255, g)) / 255;
        const bb = Math.max(0, Math.min(255, b)) / 255;
        const max = Math.max(rr, gg, bb), min = Math.min(rr, gg, bb);
        const l = (max + min) / 2;
        let hh = 0;
        let s = 0;
        if (max !== min) {
          const dd = max - min;
          s = l > 0.5 ? dd / (2 - max - min) : dd / (max + min);
          if (max === rr) hh = (gg - bb) / dd + (gg < bb ? 6 : 0);
          else if (max === gg) hh = (bb - rr) / dd + 2;
          else hh = (rr - gg) / dd + 4;
          hh /= 6;
        }
        // Weight the rotation by closeness to the source hue and by
        // saturation, so low-sat pixels (chrome/tyres) and off-hue pixels
        // (light-blue windows) are left alone.
        let rotWeight = 1;
        if (hueCenter != null) {
          const satW = Math.max(0, Math.min(1, (s - 0.12) / 0.18));
          let dh = Math.abs(hh - hueCenter);
          if (dh > 0.5) dh = 1 - dh;
          const inner = 0.06, outer = 0.17;
          const hueW = dh <= inner ? 1
            : (dh >= outer ? 0 : 0.5 + 0.5 * Math.cos(Math.PI * (dh - inner) / (outer - inner)));
          rotWeight = satW * hueW;
        }
        hh = (hh + (hue * rotWeight) / 360) % 1;
        if (hh < 0) hh += 1;
        let ro, go, bo;
        if (s === 0) { ro = go = bo = l; }
        else {
          const q2 = l < 0.5 ? l * (1 + s) : l + s - l * s;
          const p2 = 2 * l - q2;
          ro = hue2rgb(p2, q2, hh + 1 / 3);
          go = hue2rgb(p2, q2, hh);
          bo = hue2rgb(p2, q2, hh - 1 / 3);
        }
        r = ro * 255; g = go * 255; b = bo * 255;
      }
      d[i] = r; d[i + 1] = g; d[i + 2] = b;
    }
    octx.putImageData(img, 0, 0);
    _commitMaskedEffect(layer, snap, out, state.wandMask ? !!opts.invert : false);
  };
  const _ADJUSTMENT_TYPE_ALIASES = {
    blackandwhite: 'black-white', black_white: 'black-white', grayscale: 'black-white', greyscale: 'black-white', monochrome: 'black-white',
    brightnesscontrast: 'brightness-contrast', brightness_contrast: 'brightness-contrast',
    huesaturation: 'hue-saturation', hue_saturation: 'hue-saturation',
    colorbalance: 'color-balance', color_balance: 'color-balance',
    photofilter: 'photo-filter', photo_filter: 'photo-filter',
    whitebalance: 'white-balance', white_balance: 'white-balance',
    shadowshighlights: 'shadows-highlights', shadows_highlights: 'shadows-highlights',
    selectivecolor: 'selective-color', selective_color: 'selective-color',
    gradientmap: 'gradient-map', gradient_map: 'gradient-map',
    channelmixer: 'channel-mixer', channel_mixer: 'channel-mixer',
    colorlookup: 'color-lookup', color_lookup: 'color-lookup',
  };
  // Synonyms the model often emits for a single adjustment (with a generic
  // {value} amount): map each to its adjustment type + the target param key.
  const _ADJ_SHORTHAND = {
    exposure: { type: 'exposure', key: 'exposure' },
    brightness: { type: 'brightness-contrast', key: 'brightness' },
    contrast: { type: 'brightness-contrast', key: 'contrast' },
    saturation: { type: 'hue-saturation', key: 'saturation' },
    hue: { type: 'hue-saturation', key: 'hue' },
    shadows: { type: 'shadows-highlights', key: 'shadows' },
    highlights: { type: 'shadows-highlights', key: 'highlights' },
    temperature: { type: 'white-balance', key: 'temperature' },
    warmth: { type: 'white-balance', key: 'temperature' },
    warm: { type: 'white-balance', key: 'temperature' },
    tint: { type: 'white-balance', key: 'tint' },
    vibrance: { type: 'vibrance', key: 'vibrance' },
    levels: { type: 'levels', key: 'level' },
  };
  // Preferred keys used to pick an adjustment type when the model sends a flat
  // bag of numeric fields with no type (e.g. {temperature:12, vibrance:18, contrast:14}).
  const _ADJ_BAG_PRIORITY = [
    'brightness', 'contrast', 'vibrance', 'saturation', 'hue',
    'exposure', 'temperature', 'warmth', 'warm', 'tint', 'shadows', 'highlights',
  ];
  // Accept the model's camelCase / snake_case adjustment names (the live route's
  // catalog advertises camelCase) and map them to the editor's hyphenated types.
  const _normalizeAdjustmentType = (type) => {
    if (!type) return type;
    const key = String(type).toLowerCase().replace(/[^a-z]/g, '');
    return _ADJUSTMENT_TYPE_ALIASES[key] || type;
  };
  const _normalizeAdjustmentParams = (type, params = {}) => {
    const out = { ...(params || {}) };
    // brightness-contrast adjustment layers use CSS multipliers (1 = neutral),
    // but the planner contract is -100..100 percentages (0 = neutral).
    if (type === 'brightness-contrast') {
      if (typeof out.brightness === 'number') out.brightness = 1 + out.brightness / 100;
      if (typeof out.contrast === 'number') out.contrast = 1 + out.contrast / 100;
    }
    return out;
  };
  // Accept both the canonical {type, params} and the shorthand the model often
  // emits ({adjustment|type, value} / flat numeric fields) so a semantic request
  // is not silently lost to a schema mismatch. Canonical form is unchanged.
  const _normalizeAdjustmentArgs = (p = {}) => {
    const rawType = p.type || p.adjustment || p.adjustmentType || p.adjust_type || p.name || null;
    const key = rawType ? String(rawType).toLowerCase().replace(/[^a-z]/g, '') : '';
    const sh = _ADJ_SHORTHAND[key] || null;
    let adjType = sh ? sh.type : _normalizeAdjustmentType(rawType);
    let params;
    if (p.params && typeof p.params === 'object') {
      params = { ...p.params };
    } else {
      const nested = (p.adjustments && typeof p.adjustments === 'object') ? p.adjustments
        : ((p.adjust && typeof p.adjust === 'object') ? p.adjust : null);
      params = nested ? { ...nested } : {};
      for (const k of ['brightness', 'contrast', 'exposure', 'shadows', 'highlights', 'saturation', 'vibrance', 'hue', 'temperature', 'warmth', 'warm', 'tint', 'clarity', 'strength', 'amount', 'level', 'radius', 'balance']) {
        if (p[k] != null && p[k] !== '' && Number.isFinite(Number(p[k]))) params[k] = Number(p[k]);
      }
      if (p.value != null && Number.isFinite(Number(p.value))) {
        const def = _defaultAdjParams(adjType) || {};
        const primary = (sh && sh.key) || Object.keys(def).find((k) => typeof def[k] === 'number') || 'value';
        params[primary] = Number(p.value);
      }
    }
    // Flat numeric bag with no type key: derive the best-matching adjustment type
    // from the present fields so the intent is not silently lost.
    if (!adjType) {
      const found = _ADJ_BAG_PRIORITY.find((k) => params[k] != null && Number.isFinite(Number(params[k])));
      if (found) adjType = _ADJ_SHORTHAND[found].type;
    }
    // Last resort: a flat bag whose keys match no priority synonym — pick the
    // adjustment type whose default params overlap the bag the most, so the edit
    // still lands instead of being dropped with 'type required'.
    if (!adjType && Object.keys(params).length) {
      let best = null, bestScore = 0;
      for (const t of ['brightness-contrast', 'exposure', 'white-balance', 'hue-saturation', 'vibrance', 'black-white', 'shadows-highlights', 'photo-filter', 'levels', 'curves', 'color-balance', 'selective-color', 'gradient-map', 'posterize', 'threshold', 'channel-mixer', 'color-lookup']) {
        const def = _defaultAdjParams(t) || {};
        let score = 0;
        for (const k of Object.keys(params)) if (def[k] != null) score++;
        if (score > bestScore) { bestScore = score; best = t; }
      }
      if (best) adjType = best;
    }
    if (adjType) {
      const def = _defaultAdjParams(adjType) || null;
      if (def) {
        if (adjType === 'white-balance') {
          if (params.temperature == null && params.warmth != null) params.temperature = params.warmth;
          if (params.temperature == null && params.warm != null) params.temperature = params.warm;
        }
        const allowed = new Set(Object.keys(def));
        const filtered = {};
        for (const k of Object.keys(params)) if (allowed.has(k)) filtered[k] = params[k];
        if (Object.keys(filtered).length) params = filtered;
      }
    }
    return { adjType, params };
  };
  const _aiSignal = (typeof AbortController !== 'undefined') ? new AbortController().signal : undefined;
  // The image the edit model sees: the flattened document by default, or just
  // the selected layer (drawn at its offset on a document-size canvas) when
  // the Quick Edit scope control is set to "This layer".
  const _aiEditSourceCanvas = () => {
    let scope = 'all';
    try { scope = localStorage.getItem('ge-ai-command-edit-scope') || 'all'; } catch (_) {}
    if (scope !== 'layer') return flatten();
    const layer = activeLayer() || _activeParentLayer();
    if (!layer || !layer.canvas) return flatten();
    const out = document.createElement('canvas');
    out.width = state.imgWidth;
    out.height = state.imgHeight;
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    out.getContext('2d').drawImage(layer.canvas, off.x, off.y);
    return out;
  };
  const _aiEditCanvas = async (endpoint, payload = {}, { keepNaturalSize = false } = {}) => {
    const flat = _aiEditSourceCanvas();
    const image = flat.toDataURL('image/png').split(',')[1];
    const requestId = 'qe-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
    const qeModel = _getSelectedQuickEditModel();
    const endpointId = payload._endpoint || qeModel.endpoint || '';
    if (endpointId) {
      _startAiProgress(requestId, endpointId);
    } else {
      _startAiProgress(requestId);
    }
    const body = JSON.stringify({ image, request_id: requestId, ...payload });
    // The image servers occasionally reject a request transiently (503 while a
    // model swaps, 502/504 gateways, dropped connections). Retry those a couple
    // of times with backoff so a hiccup doesn't silently leave the canvas
    // unchanged; surface a clear error only if they keep failing.
    const _retryable = new Set([429, 500, 502, 503, 504]);
    const maxAttempts = 3;
    // Never leave the card spinning: give up after 3 minutes and report "offline".
    const _ctl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const _timer = _ctl ? setTimeout(() => { try { _ctl.abort(); } catch (_) {} }, 180000) : null;
    _aiEditAbort = _ctl;
    try {
      let res = null;
      let lastErr = 'Image edit failed';
      let lastStatus = 0;
      let netFail = false;
      let aborted = false;
      for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        if (aborted) break;
        try {
          res = await fetch(endpoint, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body, signal: _ctl ? _ctl.signal : undefined });
        } catch (e) {
          res = null;
          if (e && e.name === 'AbortError') { aborted = true; lastErr = 'timed out'; }
          else { lastErr = (e && e.message) || 'network error'; }
          netFail = true;
          lastStatus = 0;
          if (aborted) break;
        }
        if (res && res.ok) break;
        if (res) {
          netFail = false;
          lastStatus = res.status;
          let detail = res.statusText || '';
          try { const e = await res.json(); detail = e.detail || e.error || detail; } catch (_) {}
          lastErr = detail || ('HTTP ' + res.status);
          if (!_retryable.has(res.status)) break;
        }
        if (attempt < maxAttempts) await new Promise((r) => setTimeout(r, 700 * attempt));
      }
      if (!res || !res.ok) {
        throw new Error(_generationErrorMessage({ status: lastStatus, detail: lastErr, network: netFail, timedOut: /timed out/i.test(lastErr) }));
      }
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      if (!data.image) throw new Error('No image returned');
      const img = await _decodeAIImage(data.image, _ctl ? _ctl.signal : _aiSignal);
      const c = document.createElement('canvas');
      if (keepNaturalSize) {
        // Outpaint/extend returns a LARGER frame; keep its real dimensions so the
        // caller can grow the document instead of squashing the result.
        c.width = img.width;
        c.height = img.height;
      } else {
        c.width = state.imgWidth;
        c.height = state.imgHeight;
      }
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      return { canvas: c, dataUrl: c.toDataURL('image/png') };
    } finally {
      if (_timer) clearTimeout(_timer);
      if (_aiEditAbort === _ctl) _aiEditAbort = null;
      _stopAiProgress();
    }
  };
  const _placeResultLayer = (canvas, name, maskCanvas) => {
    _saveState(`AI: ${name}`);
    const layer = createLayer(name, state.imgWidth, state.imgHeight);
    layer.ctx.drawImage(canvas, 0, 0);
    if (maskCanvas) {
      layer.ctx.save();
      layer.ctx.globalCompositeOperation = 'destination-in';
      layer.ctx.drawImage(maskCanvas, 0, 0);
      layer.ctx.restore();
    }
    state.layers.push(layer);
    state.activeLayerId = layer.id;
    state.selectedLayerIds = [layer.id];
    composite();
    _renderLayerPanel();
    return layer.id;
  };
  const _aiInpaintSelection = async (prompt) => {
    if (!state.wandMask) throw new Error('make a selection first');
    const layer = activeLayer() || _activeParentLayer();
    if (!layer) throw new Error('no layer');
    const mask = state.wandMask.toDataURL('image/png').split(',')[1];
    const { canvas } = await _aiEditCanvas('/api/image/inpaint', { mask, prompt: prompt || '', steps: 4 });
    return _placeResultLayer(canvas, 'Generative fill', state.wandMask);
  };
  // Replace the background/scene while keeping the subject: the server derives
  // the subject mask, repaints the inverse (new background), harmonises colour
  // and adds a contact shadow, then pastes the subject back. Full-frame result.
  const _aiReplaceBackground = async (prompt) => {
    const { canvas } = await _aiEditCanvas('/api/image/replace-background', {
      prompt: prompt || '',
      steps: 4,
      harmonise: true,
      shadow: true,
    });
    return _placeResultLayer(canvas, 'Background replaced');
  };
  // Extend / outpaint the canvas to a ratio (16:9, 9:16) or size via the
  // dedicated server pipeline (pad + strip-by-strip inpaint with tone match,
  // original pixels preserved). Server endpoint: /api/image/outpaint.
  const _aiOutpaint = async (prompt, opts = {}) => {
    const payload = { prompt: prompt || '', steps: Math.max(1, Math.round(Number(opts.steps) || 4)) };
    if (opts.ratio) payload.ratio = opts.ratio;
    if (opts.size) payload.size = opts.size;
    if (opts.context != null) payload.context = opts.context;
    const { canvas } = await _aiEditCanvas('/api/image/outpaint', payload, { keepNaturalSize: true });
    const tw = canvas.width;
    const th = canvas.height;
    if (tw === state.imgWidth && th === state.imgHeight) {
      return _placeResultLayer(canvas, 'Canvas extended');
    }
    // The server returns the whole extended frame; grow the document to match
    // and lay the result down full-size (one undo step with the resize).
    _saveState('Canvas extended');
    _resizeCanvasDocument(state, tw, th, { anchorX: 0, anchorY: 0 });
    const layer = createLayer('Canvas extended', tw, th);
    layer.ctx.imageSmoothingEnabled = true;
    layer.ctx.imageSmoothingQuality = 'high';
    layer.ctx.drawImage(canvas, 0, 0);
    state.layers.push(layer);
    state.activeLayerId = layer.id;
    state.selectedLayerIds = [layer.id];
    composite();
    _renderLayerPanel();
    return { width: tw, height: th, layerId: layer.id };
  };
  // AI-fill the EMPTY (transparent) parts of the canvas: build a selection from
  // the transparent pixels, then run the generative inpaint inside it so the
  // model continues the surrounding content into the gaps.
  const _aiFillEmpty = async (prompt) => {
    const doc = flatten();
    const w = doc.width, h = doc.height;
    const data = doc.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, w, h).data;
    const out = document.createElement('canvas'); out.width = w; out.height = h;
    const octx = out.getContext('2d'); const id = octx.createImageData(w, h);
    let empty = 0;
    for (let i = 0; i < data.length; i += 4) {
      const on = data[i + 3] < 16 ? 255 : 0;
      if (on) empty++;
      id.data[i] = 255; id.data[i + 1] = 255; id.data[i + 2] = 255; id.data[i + 3] = on;
    }
    if (empty < Math.max(64, 0.003 * w * h)) throw new Error('the canvas has no empty areas to fill');
    let mask = out;
    try { mask = _growMaskCanvas(out, 6, true); } catch (_) {}
    state.wandMask = mask;
    const filled = await _aiInpaintSelection(prompt || 'continue the surrounding scene and lighting naturally into the empty area');
    return { empty, layerId: filled && filled.id };
  };
  // Grow (expand) or shrink (contract) a document-space selection mask by a
  // pixel radius using a separable max filter. grow=true expands, false shrinks.
  const _growMaskCanvas = (canvas, radius, grow) => {
    const w = canvas.width, h = canvas.height;
    const src = canvas.getContext('2d').getImageData(0, 0, w, h);
    const on = new Uint8ClampedArray(w * h);
    for (let i = 0, p = 0; i < src.data.length; i += 4, p++) on[p] = src.data[i + 3] >= 128 ? 1 : 0;
    const r = Math.max(0, Math.min(64, Math.round(radius) || 0));
    // Separable box dilation (grow) / erosion (contract). Off-canvas neighbours
    // count as unselected so a selection touching the canvas edge also shrinks
    // inward when contracted.
    const horiz = new Uint8ClampedArray(w * h);
    for (let y = 0; y < h; y++) { for (let x = 0; x < w; x++) { let v = grow ? 0 : 1; for (let xx = x - r; xx <= x + r; xx++) { const nn = (xx >= 0 && xx < w) ? on[y * w + xx] : 0; if (grow ? nn : !nn) { v = grow ? 1 : 0; break; } } horiz[y * w + x] = v; } }
    const grown = new Uint8ClampedArray(w * h);
    for (let y = 0; y < h; y++) { for (let x = 0; x < w; x++) { let v = grow ? 0 : 1; for (let yy = y - r; yy <= y + r; yy++) { const nn = (yy >= 0 && yy < h) ? horiz[yy * w + x] : 0; if (grow ? nn : !nn) { v = grow ? 1 : 0; break; } } grown[y * w + x] = v; } }
    const out = document.createElement('canvas'); out.width = w; out.height = h;
    const octx = out.getContext('2d'); const id = octx.createImageData(w, h);
    for (let p = 0; p < grown.length; p++) { const a = grown[p]; id.data[p * 4] = 255; id.data[p * 4 + 1] = 255; id.data[p * 4 + 2] = 255; id.data[p * 4 + 3] = a * 255; }
    octx.putImageData(id, 0, 0);
    return out;
  };
  // Build a soft alpha mask that is opaque only where `after` differs from
  // `before`. Used for INSTRUCTION-mode edits (whole-image) so untouched areas
  // stay pixel-identical to the original. Returns {mask, changedFraction}.
  const _differenceMaskCanvas = (before, after, { threshold = 14, grow = 3, feather = 4, edgeExcludePx = 0 } = {}) => {
    const w = before.width, h = before.height;
    const bd = before.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, w, h).data;
    const ad = after.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, w, h).data;
    const bin = document.createElement('canvas'); bin.width = w; bin.height = h;
    const bctx = bin.getContext('2d'); const out = bctx.createImageData(w, h);
    let changed = 0;
    const thr = Math.max(0, Number(threshold) || 0);
    for (let i = 0; i < bd.length; i += 4) {
      const d = Math.max(Math.abs(bd[i] - ad[i]), Math.abs(bd[i + 1] - ad[i + 1]), Math.abs(bd[i + 2] - ad[i + 2]));
      const on = d > thr ? 255 : 0;
      if (on) changed++;
      out.data[i] = 255; out.data[i + 1] = 255; out.data[i + 2] = 255; out.data[i + 3] = on;
    }
    bctx.putImageData(out, 0, 0);
    let mask = bin;
    const g = Math.max(0, Number(grow) || 0);
    const f = Math.max(0, Number(feather) || 0);
    if (g > 0) mask = _growMaskCanvas(mask, g, true);
    if (f > 0) mask = _featherMaskCanvas((cw, ch) => { const c = document.createElement('canvas'); c.width = cw; c.height = ch; return c; }, mask, f);
    // Drop a thin band along the canvas edge. The provider's resampling often
    // shifts the outermost pixels, so a 2-3px border otherwise reads as a
    // spurious "change" and leaves a visible frame around the placed result.
    const edge = Math.max(0, Math.round(Number(edgeExcludePx) || 0));
    if (edge > 0) {
      const ectx = mask.getContext('2d');
      ectx.save();
      ectx.globalCompositeOperation = 'destination-out';
      ectx.fillStyle = '#000';
      ectx.fillRect(0, 0, w, edge);
      ectx.fillRect(0, h - edge, w, edge);
      ectx.fillRect(0, 0, edge, h);
      ectx.fillRect(w - edge, 0, edge, h);
      ectx.restore();
    }
    return { mask, changedFraction: changed / (w * h) };
  };
  // Clear a thin band along all four canvas edges from a mask (destination-out).
  const _clearMaskEdgeBand = (mask, edgePx) => {
    const e = Math.max(0, Math.round(Number(edgePx) || 0));
    if (!(e > 0)) return;
    const w = mask.width, h = mask.height;
    const ectx = mask.getContext('2d');
    ectx.save();
    ectx.globalCompositeOperation = 'destination-out';
    ectx.fillStyle = '#000';
    ectx.fillRect(0, 0, w, e);
    ectx.fillRect(0, h - e, w, e);
    ectx.fillRect(0, 0, e, h);
    ectx.fillRect(w - e, 0, e, h);
    ectx.restore();
  };
  // Extend a layer's interior pixels out to the canvas edges (nearest-pixel
  // stretch) so the outermost band is generated content, never the original.
  const _edgeFillFromInterior = (ctx, w, h, bandPx) => {
    const b = Math.max(1, Math.round(Number(bandPx) || 1));
    if (w <= b + 1 || h <= b + 1) return;
    const snap = document.createElement('canvas');
    snap.width = w; snap.height = h;
    snap.getContext('2d').drawImage(ctx.canvas, 0, 0);
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.drawImage(snap, b, 0, 1, h, 0, 0, b, h);
    ctx.drawImage(snap, w - b - 1, 0, 1, h, w - b, 0, b, h);
    ctx.drawImage(snap, 0, b, w, 1, 0, 0, w, b);
    ctx.drawImage(snap, 0, h - b - 1, w, 1, 0, h - b, w, b);
    ctx.restore();
  };
  const _retouchTargetLayer = () => {
    const cur = activeLayer();
    if (cur && cur.canvas && cur.kind !== 'adjustment') return cur;
    return state.layers.slice().reverse().find((l) => l && l.canvas && l.kind !== 'adjustment') || null;
  };
  const _selectionMaskDataForLayer = (layer) => {
    if (!state.wandMask) return null;
    const mc = _maskCanvasForLayer(layer, false, 0);
    if (!mc) return null;
    return mc.getContext('2d').getImageData(0, 0, mc.width, mc.height).data;
  };
  const _writeLayerData = (layer, data) => {
    layer.ctx.putImageData(new ImageData(data, layer.canvas.width, layer.canvas.height), 0, 0);
  };
  const _controlFindLayer = (params = {}) => {
    if (!params) return activeLayer();
    if (params.id) return state.layers.find((l) => l.id === params.id) || null;
    if (typeof params.index === 'number') {
      const idx = Math.max(0, Math.min(state.layers.length - 1, params.index));
      return state.layers[state.layers.length - 1 - idx] || null;
    }
    if (params.name) return state.layers.find((l) => l.name === params.name) || null;
    return activeLayer();
  };
  const _layerDocBounds = (layer) => {
    if (!layer) return null;
    const c = _renderLayerOutput(layer) || layer.canvas;
    if (!c || !c.getContext) return null;
    const data = c.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, c.width, c.height).data;
    const b = _maskAlphaBounds(data, c.width, c.height, 8);
    if (!b) return null;
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    return { x: b.x + (off.x || 0), y: b.y + (off.y || 0), w: b.w, h: b.h };
  };
  // The placed photo/content layer bounds (top-most non-adjustment, non-text
  // raster layer with real content). Used to place titles inside the photo and
  // to centre a crop-to-aspect on the actual image rather than the canvas.
  const _imageLayerBounds = () => {
    for (let i = state.layers.length - 1; i >= 0; i--) {
      const l = state.layers[i];
      if (!l || l.kind === 'adjustment' || l.kind === 'text' || !l.canvas) continue;
      const b = _layerDocBounds(l);
      if (b && b.w > 16 && b.h > 16) return b;
    }
    return null;
  };
  // Focus centre for a crop-to-aspect: the face/subject selection if one is
  // present, else the image layer centre, else the canvas centre.
  const _contentFocusCenter = () => {
    if (state.wandMask) {
      try {
        const d = state.wandMask.getContext('2d').getImageData(0, 0, state.imgWidth, state.imgHeight).data;
        const b = _maskAlphaBounds(d, state.imgWidth, state.imgHeight, 8);
        if (b) return { x: b.x + b.w / 2, y: b.y + b.h / 2 };
      } catch (_) {}
    }
    const ib = _imageLayerBounds();
    if (ib) return { x: ib.x + ib.w / 2, y: ib.y + ib.h / 2 };
    return { x: state.imgWidth / 2, y: state.imgHeight / 2 };
  };
  const _controlSelect = (layer) => {
    if (!layer) return null;
    state.activeLayerId = layer.id;
    state.activeGroupId = null;
    state.selectedLayerIds = [layer.id];
    _renderLayerPanel();
    _syncToolClearIndicators();
    return layer.id;
  };
  const _controlLayerSummary = (l) => {
    let coverage = null;
    try {
      if (l && l.canvas && l.canvas.width) {
        const c = document.createElement('canvas'); c.width = 48; c.height = 48;
        const cx = c.getContext('2d'); cx.drawImage(l.canvas, 0, 0, 48, 48);
        const d = cx.getImageData(0, 0, 48, 48).data;
        let on = 0;
        for (let i = 3; i < d.length; i += 4) if (d[i] > 8) on++;
        coverage = on / (48 * 48);
      }
    } catch (_) {}
    return {
      id: l.id, name: l.name, kind: l.kind || 'raster', coverage,
      maskCount: (l.masks || []).length,
      visible: l.visible !== false, opacity: typeof l.opacity === 'number' ? l.opacity : 1,
      blendMode: l.blendMode || 'source-over', locked: !!l.locked, clipped: !!l.clipped,
      adjustment: l.adjustment ? l.adjustment.type : null,
    };
  };
  const _activeDocEntry = () => {
    if (!state.documentId) state.documentId = 'doc-' + (++_activeDocumentSeq);
    let entry = _openDocuments.find((d) => d.id === state.documentId);
    if (!entry) {
      entry = { id: state.documentId, name: state.draftName || 'Untitled', width: state.imgWidth, height: state.imgHeight, doc: null };
      _openDocuments.push(entry);
    }
    return entry;
  };
  // Set while closing the active tab: the switch op's snapshot step would
  // otherwise re-add the just-closed id (and copy its pixels onto the
  // survivor), so it is skipped for that one transition.
  let _suppressDocSync = false;
  const _syncActiveDocument = () => {
    try {
      if (_suppressDocSync || !state.editorOpen) return null;
      const entry = _activeDocEntry();
      entry.doc = _serializeEditorDocument(state);
      entry.name = state.draftName || entry.name;
      entry.width = state.imgWidth; entry.height = state.imgHeight;
      return entry;
    } catch (_) { return null; }
  };
  const _controlState = {
    installEditorControl() {
      const control = _createEditorControl({
        getState: () => ({
          editorOpen: !!state.editorOpen, width: state.imgWidth, height: state.imgHeight,
          layerCount: state.layers.length, activeLayerId: state.activeLayerId,
          selectedLayerIds: state.selectedLayerIds.slice(), tool: state.tool, zoom: state.zoom,
        }),
        handlers: {
          'document.info': { description: 'Current document + selection summary.', run: () => ({
            width: state.imgWidth, height: state.imgHeight, layerCount: state.layers.length,
            activeLayerId: state.activeLayerId, selectedLayerIds: state.selectedLayerIds.slice(),
            tool: state.tool, zoom: state.zoom, draftName: state.draftName || null,
          }) },
          'document.importImage': { description: 'Import an image from a data URL as a new (rasterized) layer {dataUrl,name,raster}.', run: (p) => new Promise((resolve, reject) => { if (!p || !p.dataUrl) { reject(new Error('dataUrl required')); return; } const img = new Image(); img.onload = () => { try { _handleImportedImage(img, p.name || 'Imported image'); if (p.raster !== false) { const l = activeLayer(); if (l && l.kind === 'placed') _rasterizePlacedLayer(l); } resolve(state.activeLayerId); } catch (e) { reject(e); } }; img.onerror = () => reject(new Error('image load failed')); img.src = p.dataUrl; }) },
          'layers.list': { description: 'List layers, top-most first.', run: () => state.layers.slice().reverse().map(_controlLayerSummary) },
          'layers.select': { description: 'Activate a layer by id/index/name.', run: (p) => _controlSelect(_controlFindLayer(p)) },
          'layers.addRaster': { description: 'Add an empty raster layer.', run: () => { _addEmptyLayer(); return state.activeLayerId; } },
          'layers.addAdjustment': { description: 'Add an adjustment layer by type.', run: (p) => { const rawType = p && (p.type || p.adjustment); if (!rawType) throw new Error('type required'); _addAdjustmentLayer(_normalizeAdjustmentArgs({ type: rawType }).adjType); return state.activeLayerId; } },
          'layers.duplicate': { description: 'Duplicate a layer.', run: (p) => { _controlSelect(_controlFindLayer(p)); _layerPanelRenderer.duplicateActiveLayer?.(); return state.activeLayerId; } },
          'layers.delete': { description: 'Delete layers (defaults to selection).', run: (p) => { if (p && (p.id || p.index || p.name)) _controlSelect(_controlFindLayer(p)); _layerPanelRenderer.deleteSelectedLayers?.(); return true; } },
          'layers.setOpacity': { description: 'Set a layer opacity (0-1).', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); _saveState(`Change opacity of ${l.name}`); l.opacity = Math.max(0, Math.min(1, Number(p.opacity))); composite(); _renderLayerPanel(); return l.id; } },
          'layers.setBlend': { description: 'Set a layer blend mode.', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); _saveState(`Change blend mode of ${l.name}`); l.blendMode = p.blendMode || p.mode || 'source-over'; composite(); _renderLayerPanel(); return l.id; } },
          'layers.placeImage': { description: 'Place an image (data URL) as a NEW layer, masked to the current selection when one exists; never overwrites the original. Instruction mode (mode:"instruction" or differenceMask:true) keeps untouched areas pixel-identical via a difference map. {dataUrl,name,masked,blend,opacity,x,y,w,h,mode,differenceMask,diffThreshold,diffGrow,diffFeather}.', run: (p) => new Promise((resolve, reject) => { if (!p || !p.dataUrl) { reject(new Error('dataUrl required')); return; } const img = new Image(); img.onload = () => { try { _saveState(`Place ${p.name || 'image'}`); const dw = state.imgWidth, dh = state.imgHeight; const useDiff = (p.differenceMask === true) || String(p.mode || '').toLowerCase() === 'instruction'; const beforeSnap = useDiff ? flatten() : null; const layer = createLayer(p.name || 'Placed image', dw, dh); let tw = Number(p.w), th = Number(p.h); if (!(tw > 0) || !(th > 0)) { if (useDiff) { tw = dw; th = dh; } else { const scale = Math.min(dw / img.naturalWidth, dh / img.naturalHeight); tw = Math.max(1, Math.round(img.naturalWidth * scale)); th = Math.max(1, Math.round(img.naturalHeight * scale)); } } let tx = Number(p.x), ty = Number(p.y); if (!Number.isFinite(tx)) tx = useDiff ? 0 : Math.round((dw - tw) / 2); if (!Number.isFinite(ty)) ty = useDiff ? 0 : Math.round((dh - th) / 2); layer.ctx.imageSmoothingEnabled = true; layer.ctx.imageSmoothingQuality = 'high'; layer.ctx.drawImage(img, tx, ty, tw, th); if (p.masked !== false && state.wandMask) { layer.ctx.save(); layer.ctx.globalCompositeOperation = 'destination-in'; layer.ctx.drawImage(state.wandMask, 0, 0); layer.ctx.restore(); } else if (useDiff && beforeSnap) { const diff = _differenceMaskCanvas(beforeSnap, layer.canvas, { threshold: p.diffThreshold, grow: p.diffGrow, feather: p.diffFeather, edgeExcludePx: 0 }); const explicitEdge = p.edgeExcludePx != null ? Number(p.edgeExcludePx) : null; const sceneReplace = diff.changedFraction > 0.5; const edgeExclude = sceneReplace ? 0 : (explicitEdge != null ? explicitEdge : 3); if (edgeExclude > 0) _clearMaskEdgeBand(diff.mask, edgeExclude); layer.ctx.save(); layer.ctx.globalCompositeOperation = 'destination-in'; layer.ctx.drawImage(diff.mask, 0, 0); layer.ctx.restore(); if (sceneReplace) _edgeFillFromInterior(layer.ctx, dw, dh, 3); } if (p.blend) layer.blendMode = p.blend; if (p.opacity != null) layer.opacity = Math.max(0, Math.min(1, Number(p.opacity))); state.layers.push(layer); state.activeLayerId = layer.id; state.selectedLayerIds = [layer.id]; _renderLayerPanel(); composite(); resolve(layer.id); } catch (e) { reject(e); } }; img.onerror = () => reject(new Error('image load failed')); img.src = p.dataUrl; }) },
          'layers.addMaskFromSelection': { description: 'Add a layer mask from the current selection (reveals the selection). {invert}.', run: (p) => { const parent = _activeParentLayer(); if (!parent) throw new Error('no layer'); _saveState('Add layer mask'); const mask = _ensureActiveMaskLayer(); if (!mask) throw new Error('could not create mask'); const w = mask.canvas.width, h = mask.canvas.height; const mctx = mask.ctx; const off = state.layerOffsets.get(parent.id) || { x: 0, y: 0 }; const ox = -(off.x || 0), oy = -(off.y || 0); mctx.globalCompositeOperation = 'source-over'; mctx.clearRect(0, 0, w, h); if (state.wandMask) { if (p && p.invert) { mctx.fillStyle = '#fff'; mctx.fillRect(0, 0, w, h); mctx.globalCompositeOperation = 'destination-out'; mctx.drawImage(state.wandMask, ox, oy); mctx.globalCompositeOperation = 'source-over'; } else { mctx.drawImage(state.wandMask, ox, oy); } } else { mctx.fillStyle = (p && p.invert) ? '#000' : '#fff'; mctx.fillRect(0, 0, w, h); } composite(); _renderLayerPanel(); return mask.id; } },
          'layers.removeMask': { description: 'Remove the active layer mask (does not affect pixels).', run: () => { const parent = _activeParentLayer(); if (!parent || !parent.masks || !parent.masks.length) throw new Error('no mask'); _saveState('Remove layer mask'); parent.masks = parent.masks.filter((m) => m.id !== parent.activeMaskId); parent.activeMaskId = parent.masks[0]?.id || null; composite(); _renderLayerPanel(); return true; } },
          'layers.applyMask': { description: 'Bake the active layer mask into the layer alpha and remove the mask.', run: () => { const parent = _activeParentLayer(); const mask = _getActiveMaskLayer(); if (!parent || !parent.ctx || !mask) throw new Error('no mask to apply'); _saveState('Apply layer mask'); _bakeMaskIntoLayer(parent.ctx, mask.canvas, 0, 0); parent.masks = parent.masks.filter((m) => m.id !== mask.id); parent.activeMaskId = parent.masks[0]?.id || null; composite(); _renderLayerPanel(); return true; } },
          'mask.invert': { description: 'Invert the active layer mask.', run: () => { const mask = _getActiveMaskLayer(); if (!mask) throw new Error('no mask'); _saveState('Invert mask'); const w = mask.canvas.width, h = mask.canvas.height; const tmp = document.createElement('canvas'); tmp.width = w; tmp.height = h; tmp.getContext('2d').drawImage(mask.canvas, 0, 0); const mctx = mask.ctx; mctx.globalCompositeOperation = 'source-over'; mctx.clearRect(0, 0, w, h); mctx.fillStyle = '#fff'; mctx.fillRect(0, 0, w, h); mctx.globalCompositeOperation = 'destination-out'; mctx.drawImage(tmp, 0, 0); mctx.globalCompositeOperation = 'source-over'; composite(); _renderLayerPanel(); return true; } },
          'mask.feather': { description: 'Feather the active layer mask edge {radius}.', run: (p) => { const mask = _getActiveMaskLayer(); if (!mask) throw new Error('no mask'); const radius = Math.max(0, Number(p && p.radius) || 4); _saveState('Feather mask'); const out = _featherMaskCanvas((w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; }, mask.canvas, radius); mask.ctx.globalCompositeOperation = 'source-over'; mask.ctx.clearRect(0, 0, mask.canvas.width, mask.canvas.height); mask.ctx.drawImage(out, 0, 0); composite(); _renderLayerPanel(); return { radius }; } },
          'layers.getImage': { description: 'Return a layer as a PNG data URL (id/index/name).', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); const c = _renderLayerOutput(l) || l.canvas; if (!c || !c.toDataURL) throw new Error('layer has no raster content'); return { id: l.id, name: l.name, kind: l.kind || 'raster', width: c.width, height: c.height, dataUrl: c.toDataURL('image/png') }; } },
          'layers.getBounds': { description: 'Return the non-transparent bounds of a layer in document space (id/index/name).', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l || !l.canvas) throw new Error('layer not found'); const c = _renderLayerOutput(l) || l.canvas; const data = c.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, c.width, c.height).data; const b = _maskAlphaBounds(data, c.width, c.height, 8); const off = state.layerOffsets.get(l.id) || { x: 0, y: 0 }; return { id: l.id, name: l.name, bounds: b, x: (b ? b.x : 0) + (off.x || 0), y: (b ? b.y : 0) + (off.y || 0), width: b ? b.w : 0, height: b ? b.h : 0 }; } },
          'ai.removeBackground': { description: 'Remove a layer background and store it as a NON-destructive layer mask {id/index/name}.', run: async (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l || !l.canvas) throw new Error('layer not found'); const image = l.canvas.toDataURL('image/png').split(',')[1]; const res = await fetch('/api/image/remove-bg', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image }) }); if (!res.ok) { let err = res.statusText; try { const e = await res.json(); err = e.detail || e.error || err; } catch (_) {} throw new Error(err); } const data = await res.json(); if (data.error) throw new Error(data.error); if (!data.image) throw new Error('No image returned'); const img = await _decodeAIImage(data.image, _aiSignal); const cut = document.createElement('canvas'); cut.width = l.canvas.width; cut.height = l.canvas.height; const cctx = cut.getContext('2d'); cctx.drawImage(img, 0, 0, cut.width, cut.height); cctx.globalCompositeOperation = 'source-in'; cctx.fillStyle = '#ffffff'; cctx.fillRect(0, 0, cut.width, cut.height); _saveState('Remove background (mask)'); if (!l.masks) l.masks = []; const mask = { id: 'mask-' + (state.nextLayerId++), name: 'Background removed', canvas: cut, ctx: cctx, visible: true, mode: 'layer', space: 'layer', linked: true, offset: { x: 0, y: 0 } }; l.masks.push(mask); l.activeMaskId = mask.id; composite(); _renderLayerPanel(); return { layerId: l.id, maskId: mask.id }; } },
          'ai.generativeFill': { description: 'Generative fill: inpaint the current selection with a prompt; result is a NEW layer masked to the selection. {prompt}.', run: (p) => _aiInpaintSelection((p && p.prompt) || '') },
          'ai.replaceBackground': { description: 'Replace the background/scene with new content while keeping the subject (server-side subject mask + harmonise + contact shadow). {prompt}.', run: (p) => _aiReplaceBackground((p && p.prompt) || '') },
          'ai.outpaint': { description: 'Extend/outpaint the canvas to a ratio (16:9, 9:16) or size WxH via the server outpaint pipeline (pad + strip inpaint with tone match, original pixels preserved). {prompt,ratio,size,context,steps}.', run: (p) => _aiOutpaint((p && p.prompt) || '', { ratio: p && p.ratio, size: p && p.size, context: p && p.context, steps: p && p.steps }) },
          'ai.fillEmpty': { description: 'AI-fill the empty/transparent parts of the canvas with generated content that continues the scene. {prompt}.', run: (p) => _aiFillEmpty((p && p.prompt) || '') },
          'ai.removeObject': { description: 'Remove the selected object and fill naturally. {prompt}.', run: (p) => _aiInpaintSelection((p && p.prompt) || 'remove the object, fill the area naturally') },
          'ai.harmonize': { description: 'Harmonize the layer/selection lighting with the rest of the image; result is a NEW layer. {strength}.', run: async (p) => { const { canvas } = await _aiEditCanvas('/api/image/harmonize', { strength: Number(p && p.strength) || 0.4 }); return _placeResultLayer(canvas, 'Harmonized', state.wandMask || null); } },
          'ai.generativeUpscale': { description: 'Upscale the image 2x/4x; result is a NEW layer. Falls back to a high-quality canvas upscale labelled "basic upscale" when no Real-ESRGAN model is served. {scale}.', run: async (p) => { const scale = Number(p && p.scale) === 4 ? 4 : 2; try { const { canvas } = await _aiEditCanvas('/api/image/upscale-local', { scale }); return _placeResultLayer(canvas, `Upscaled ${scale}x`, null); } catch (e) { const src = flatten(); const c = document.createElement('canvas'); c.width = Math.max(1, Math.round(src.width * scale)); c.height = Math.max(1, Math.round(src.height * scale)); const cx = c.getContext('2d'); cx.imageSmoothingEnabled = true; cx.imageSmoothingQuality = 'high'; cx.drawImage(src, 0, 0, c.width, c.height); _saveState('Upscale (basic)'); _resizeCanvasDocument(state, c.width, c.height, { anchorX: 0, anchorY: 0 }); const layerId = _placeResultLayer(c, `Upscaled ${scale}x (basic upscale)`, null); return { layerId, scale, method: 'basic', reason: String(e && e.message || e) }; } } },
          'ai.generateImage': { description: 'Generate an image from a prompt (current canvas as reference) and place it as a NEW layer. {prompt,width,height}.', run: async (p) => { const endpoint = (typeof window !== 'undefined' && window.geGenerateImageEndpoint) || '/api/image/generate'; const reference = flatten().toDataURL('image/png').split(',')[1]; const _pickedModel = (() => { try { const v = localStorage.getItem('ge-ai-command-model') || ''; return v.includes('::') ? v.split('::').pop() : ''; } catch (_) { return ''; } })(); const body = { prompt: (p && p.prompt) || '', width: Math.max(1, Math.round(Number(p && p.width) || state.imgWidth)), height: Math.max(1, Math.round(Number(p && p.height) || state.imgHeight)), reference, ...(_pickedModel ? { model: _pickedModel } : {}) }; let _gres = null; try { _gres = await fetch(endpoint, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); } catch (_e) { throw new Error(_generationErrorMessage({ network: true, detail: (_e && _e.message) || 'network error' })); } const res = _gres; if (!res.ok) { let _gd = res.statusText; try { const e = await res.json(); _gd = e.detail || e.error || _gd; } catch (_) {} throw new Error(_generationErrorMessage({ status: res.status, detail: _gd })); } const data = await res.json(); if (data.error) throw new Error(_generationErrorMessage({ detail: data.error })); const b64 = data.image || (Array.isArray(data.images) && data.images[0]); if (!b64) throw new Error('No image returned'); try { window.__geGenerationDisabled = 0; } catch (_) {} const img = await _decodeAIImage(b64, _aiSignal); const c = document.createElement('canvas'); c.width = state.imgWidth; c.height = state.imgHeight; c.getContext('2d').drawImage(img, 0, 0, state.imgWidth, state.imgHeight); return _placeResultLayer(c, 'Generated', state.wandMask || null); } },
          'ai.generativeExpand': { description: 'Generatively expand the canvas to width×height; only the new border is inpainted, the original pixels are kept pixel-identical, and the seam is feathered. {width,height,anchor,prompt}.', run: async (p) => { const tw = Math.max(1, Math.round(Number(p && p.width) || state.imgWidth)); const th = Math.max(1, Math.round(Number(p && p.height) || state.imgHeight)); if (tw <= state.imgWidth && th <= state.imgHeight) throw new Error('target must be larger than the current canvas'); const anchor = (p && p.anchor) || 'center'; const anchorX = anchor === 'left' ? 0 : anchor === 'right' ? 1 : 0.5; const anchorY = anchor === 'top' ? 0 : anchor === 'bottom' ? 1 : 0.5; _saveState('Generative expand'); const steps = Math.max(1, Math.round(Number(p && p.steps) || 4)); const sw = state.imgWidth; const sh = state.imgHeight; _resizeCanvasDocument(state, tw, th, { anchorX, anchorY }); const flat = flatten(); const b = _contentBounds(); const mask = document.createElement('canvas'); mask.width = tw; mask.height = th; const mctx = mask.getContext('2d'); mctx.fillStyle = '#ffffff'; mctx.fillRect(0, 0, tw, th); const prefill = document.createElement('canvas'); prefill.width = tw; prefill.height = th; const pctx = prefill.getContext('2d'); pctx.imageSmoothingEnabled = true; pctx.drawImage(flat, 0, 0, tw, th); if (b) { mctx.clearRect(b.x, b.y, b.w, b.h); const bx = b.x, by = b.y, bw = b.w, bh = b.h, rx = bx + bw, by2 = by + bh; if (bx > 0) pctx.drawImage(flat, bx, by, 1, bh, 0, by, bx, bh); if (rx < tw) pctx.drawImage(flat, rx - 1, by, 1, bh, rx, by, tw - rx, bh); if (by > 0) pctx.drawImage(flat, bx, by, bw, 1, bx, 0, bw, by); if (by2 < th) pctx.drawImage(flat, bx, by2 - 1, bw, 1, bx, by2, bw, th - by2); if (bx > 0 && by > 0) pctx.drawImage(flat, bx, by, 1, 1, 0, 0, bx, by); if (rx < tw && by > 0) pctx.drawImage(flat, rx - 1, by, 1, 1, rx, 0, tw - rx, by); if (bx > 0 && by2 < th) pctx.drawImage(flat, bx, by2 - 1, 1, 1, 0, by2, bx, th - by2); if (rx < tw && by2 < th) pctx.drawImage(flat, rx - 1, by2 - 1, 1, 1, rx, by2, tw - rx, th - by2); } else { const tx = Math.round((tw - sw) * anchorX); const ty = Math.round((th - sh) * anchorY); mctx.clearRect(tx, ty, sw, sh); } const image = prefill.toDataURL('image/png').split(',')[1]; const maskB64 = mask.toDataURL('image/png').split(',')[1]; const res = await fetch('/api/image/inpaint', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image, mask: maskB64, prompt: (p && p.prompt) || 'extend the scene seamlessly, continuing the surrounding content and lighting naturally', steps }) }); if (!res.ok) { let err = res.statusText; try { const e = await res.json(); err = e.detail || e.error || err; } catch (_) {} throw new Error(err); } const data = await res.json(); if (data.error) throw new Error(data.error); if (!data.image) throw new Error('No image returned'); const img = await _decodeAIImage(data.image, _aiSignal); const seam = Math.max(12, Math.round(Math.min(tw, th) * 0.02)); let blendMask = _growMaskCanvas(mask, seam, true); blendMask = _featherMaskCanvas((cw, ch) => { const c = document.createElement('canvas'); c.width = cw; c.height = ch; return c; }, blendMask, Math.round(seam * 1.4)); const layer = createLayer('Generative expand', tw, th); layer.ctx.imageSmoothingEnabled = true; layer.ctx.imageSmoothingQuality = 'high'; layer.ctx.drawImage(img, 0, 0, tw, th); if (b) { const edge = _edgeBandAverage(flat, b); if (edge) { layer.ctx.save(); layer.ctx.globalAlpha = 0.14; layer.ctx.fillStyle = `rgb(${edge[0]}, ${edge[1]}, ${edge[2]})`; layer.ctx.fillRect(0, 0, tw, th); layer.ctx.restore(); } } _bakeMaskIntoLayer(layer.ctx, blendMask); state.layers.push(layer); state.activeLayerId = layer.id; state.selectedLayerIds = [layer.id]; composite(); _renderLayerPanel(); return { width: tw, height: th, layerId: layer.id, seam, steps }; } },
          'layers.rename': { description: 'Rename a layer {id|index|name, name|newName}.', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); const next = String(p.name || p.newName || '').trim(); if (!next) throw new Error('name required'); _saveState('Rename layer'); l.name = next; _renderLayerPanel(); return l.id; } },
          'layers.setVisibility': { description: 'Show/hide a layer {id|index|name, visible}.', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); _saveState('Toggle layer visibility'); l.visible = (p.visible === undefined) ? !l.visible : !!p.visible; composite(); _renderLayerPanel(); return l.visible; } },
          'layers.reorder': { description: 'Move a layer in z-order {id|index|name, direction:up|down|top|bottom}.', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); const i = state.layers.indexOf(l); if (i < 0) throw new Error('layer not in stack'); const dir = p.direction || 'up'; let j = i; if (dir === 'up') j = Math.min(state.layers.length - 1, i + 1); else if (dir === 'down') j = Math.max(0, i - 1); else if (dir === 'top') j = state.layers.length - 1; else if (dir === 'bottom') j = 0; if (j === i) return l.id; _saveState('Reorder layer'); state.layers.splice(i, 1); state.layers.splice(j, 0, l); composite(); _renderLayerPanel(); return l.id; } },
          'layers.rasterize': { description: 'Rasterize a placed or text layer into pixels {id|index|name}.', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); _saveState('Rasterize layer'); if (l.kind === 'placed' && _rasterizePlacedLayer) _rasterizePlacedLayer(l); else if (l.kind === 'text' && _rasterizeTextLayer) _rasterizeTextLayer(l); composite(); _renderLayerPanel(); return l.id; } },
          'layers.mergeDown': { description: 'Merge the active layer down into the layer below.', run: () => { const b = document.getElementById('ge-merge-down'); if (!b) throw new Error('merge down unavailable'); b.click(); return true; } },
          'layers.mergeLayers': { description: 'Merge all selected layers into the lowest one (Photoshop Layer > Merge Layers).', run: () => { let ids = (Array.isArray(state.selectedLayerIds) ? state.selectedLayerIds : []).filter((id) => state.layers.some((l) => l.id === id)); if (ids.length < 2) { const i = state.layers.findIndex((l) => l.id === state.activeLayerId); if (i > 0) ids = [state.layers[i - 1].id, state.layers[i].id]; } if (ids.length < 2) throw new Error('select at least two layers'); _saveState('Merge layers'); const merged = _mergeLayersByIds(ids, _renderLayerOutput); if (!merged) throw new Error('these layers cannot be merged'); composite(); _renderLayerPanel(); return merged.id; } },
          'layers.flatten': { description: 'Flatten all layers into a single layer.', run: () => { const layers = state.layers || []; if (layers.length < 2) return state.activeLayerId || null; const visible = layers.filter(l => l.visible); if (visible.length >= 2) { document.getElementById('ge-merge-all')?.click(); return state.activeLayerId; } const base = visible[0] || layers[0]; _saveState('Flatten image'); for (const l of layers) if (l !== base) state.layerOffsets.delete(l.id); base.visible = true; state.layers = [base]; state.layerGroups = []; state.activeLayerId = base.id; state.selectedLayerIds = [base.id]; _renderLayerPanel(); composite(); return base.id; } },
          'layers.align': { description: 'Align/distribute the selected layers {mode:left|center|right|top|middle|bottom|distribute-h|distribute-v}.', run: (p) => { const sel = state.layers.filter((l) => state.selectedLayerIds.includes(l.id)); if (sel.length < 2) throw new Error('select at least two layers'); const mode = p.mode || 'left'; _saveState('Align layers'); if (mode === 'distribute-h' || mode === 'distribute-v') _distributeSelectedLayers(state, sel, mode === 'distribute-h' ? 'horizontal' : 'vertical'); else _alignSelectedLayers(state, sel, mode); composite(); _renderLayerPanel(); return mode; } },
          'layer.flip': { description: 'Flip one layer in place {axis:h|v, id|index|name}.', run: (p = {}) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); state.activeLayerId = l.id; _flipActiveLayer(p.axis === 'v' ? 'v' : 'h'); return l.id; } },
          'layer.rotate': { description: 'Rotate one layer by 90/180/270 degrees {degrees, id|index|name}.', run: (p = {}) => { const l = _controlFindLayer(p) || activeLayer(); if (!l) throw new Error('layer not found'); const deg = Number(p.degrees) || 90; if (![90, 180, 270].includes(((deg % 360) + 360) % 360)) throw new Error('degrees must be 90, 180 or 270'); state.activeLayerId = l.id; _rotateActiveLayer(deg); return l.id; } },
          'document.rotate': { description: 'Rotate the whole document (all layers + canvas) by 90/180/270 {degrees}.', run: (p = {}) => { const deg = Number(p.degrees) || 90; if (![90, 180, 270].includes(((deg % 360) + 360) % 360)) throw new Error('degrees must be 90, 180 or 270'); _rotateAllLayers(deg); return { degrees: deg }; } },
          'document.rotateArbitrary': { description: 'Rotate the whole image by an arbitrary angle {degrees} (clockwise; negative = counter-clockwise), expanding the canvas to fit.', run: (p = {}) => { const deg = Number(p.degrees); if (!Number.isFinite(deg)) throw new Error('degrees required'); _rotateAllLayersArbitrary(deg); return { degrees: deg }; } },
          'document.flip': { description: 'Flip the whole document (all layers + canvas) {axis:h|v}.', run: (p = {}) => { const axis = p.axis === 'v' ? 'v' : 'h'; _flipAllLayers(axis); return { axis }; } },
          'document.perspectiveCrop': { description: 'Straighten an arbitrary quadrilateral {corners:[{x,y},{x,y},{x,y},{x,y}]} (TL,TR,BR,BL in document coords) to a resized rectangle (Perspective Crop); flattens to a single raster layer.', run: (p = {}) => { const corners = Array.isArray(p.corners) ? p.corners : null; if (!corners || corners.length < 4) throw new Error('corners required (4 points)'); return _applyPerspectiveCrop(corners); } },
          'selection.polygon': { description: 'Polygon selection {points:[[x,y],...]} (>=3).', run: (p) => { const pts = Array.isArray(p.points) ? p.points : null; if (!pts || pts.length < 3) throw new Error('points[] required (>=3)'); const w = state.imgWidth, h = state.imgHeight; const c = document.createElement('canvas'); c.width = w; c.height = h; const cx = c.getContext('2d'); cx.fillStyle = '#fff'; cx.beginPath(); pts.forEach((pt, i) => { const x = Number(Array.isArray(pt) ? pt[0] : pt.x) || 0; const y = Number(Array.isArray(pt) ? pt[1] : pt.y) || 0; if (i === 0) cx.moveTo(x, y); else cx.lineTo(x, y); }); cx.closePath(); cx.fill(); _saveState('Polygon selection'); _commitSelectionMask(c, activeLayer(), p.mode || 'replace', 'polygon'); composite(); _syncToolClearIndicators(); return { points: pts.length }; } },
          'selection.fill': { description: 'Fill the current selection with a color on the active pixel layer {color}.', run: (p) => { const layer = activeLayer(); if (!layer || !layer.canvas || layer.kind === 'adjustment') throw new Error('select a pixel layer'); if (!state.wandMask) throw new Error('no selection'); _saveState('Fill selection'); const tmp = document.createElement('canvas'); tmp.width = layer.canvas.width; tmp.height = layer.canvas.height; const tctx = tmp.getContext('2d'); tctx.fillStyle = p.color || state.color || '#000000'; tctx.fillRect(0, 0, tmp.width, tmp.height); const mask = _maskCanvasForLayer(layer, false, 0); const eff = document.createElement('canvas'); eff.width = tmp.width; eff.height = tmp.height; const ectx = eff.getContext('2d'); ectx.drawImage(tmp, 0, 0); ectx.globalCompositeOperation = 'destination-in'; ectx.drawImage(mask, 0, 0); layer.ctx.drawImage(eff, 0, 0); composite(); _renderLayerPanel(); return true; } },
          'fill.flood': { description: 'Paint-bucket flood-fill a pixel layer {x, y, color, tolerance, id|index|name}.', run: (p = {}) => { const l = _controlFindLayer(p) || activeLayer(); if (!l || !l.canvas) throw new Error('layer not found'); const off = state.layerOffsets.get(l.id) || { x: 0, y: 0 }; const count = _bucketTool.fillAtPoint(l, Number(p.x) - off.x, Number(p.y) - off.y, { color: p.color, tolerance: p.tolerance }); if (!count) throw new Error('nothing to fill at that point'); return { layerId: l.id, count }; } },
          'selection.delete': { description: 'Delete the pixels inside the current selection.', run: async () => { if (!state.wandMask) throw new Error('no selection'); await _wandDeleteSelection({ saveHistory: true, message: 'Selection deleted' }); return true; } },
          'selection.copy': { description: 'Copy (or cut with cut:true) the selection pixels to the internal clipboard.', run: async (p) => { if (!state.wandMask) throw new Error('no selection'); await _copyPixelsToClipboard({ cut: !!(p && p.cut) }); return true; } },
          'selection.paste': { description: 'Paste the internal clipboard as a new layer.', run: (p = {}) => { if (!state.internalClipboard) throw new Error('clipboard empty'); return _placeResultLayer(state.internalClipboard, p.name || 'Pasted', null); } },
          'edit.copyMerged': { description: 'Copy the flattened composite of all visible layers to the clipboard (Photoshop Edit > Copy Merged).', run: () => { const flat = flatten(); state.internalClipboard = flat; state.internalClipboardOffset = { x: 0, y: 0 }; flat.toBlob((blob) => { if (blob && navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') { navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]).then(() => uiModule?.showToast?.('Copied merged')).catch(() => uiModule?.showToast?.('Copied merged (editor only)')); } else uiModule?.showToast?.('Copied merged (editor only)'); }, 'image/png'); return { width: flat.width, height: flat.height }; } },
          'selection.feather': { description: 'Feather (soften) the current selection edge {radius}.', run: (p) => { if (!state.wandMask) throw new Error('no selection'); const r = Math.max(0, Math.min(100, Number(p.radius) || 8)); _saveState('Feather selection'); state.wandMask = _featherMaskCanvas(() => document.createElement('canvas'), state.wandMask, r); composite(); return { radius: r }; } },
          'selection.expand': { description: 'Expand the current selection by {amount} pixels.', run: (p) => { if (!state.wandMask) throw new Error('no selection'); const amt = Math.max(0, Math.min(64, Number(p.amount) || 4)); _saveState('Expand selection'); state.wandMask = _growMaskCanvas(state.wandMask, amt, true); composite(); return { amount: amt }; } },
          'selection.contract': { description: 'Contract the current selection by {amount} pixels.', run: (p) => { if (!state.wandMask) throw new Error('no selection'); const amt = Math.max(0, Math.min(64, Number(p.amount) || 4)); _saveState('Contract selection'); state.wandMask = _growMaskCanvas(state.wandMask, amt, false); composite(); return { amount: amt }; } },
          'selection.border': { description: 'Replace the selection with a {width}-pixel band hugging its edge, growing outward and inward from it (Photoshop Select > Modify > Border).', run: (p) => { if (!state.wandMask) throw new Error('no selection'); const wdt = Math.max(1, Math.min(64, Number(p.width) || 4)); const outer = _growMaskCanvas(state.wandMask, wdt, true); const inner = _growMaskCanvas(state.wandMask, wdt, false); const out = document.createElement('canvas'); out.width = outer.width; out.height = outer.height; const octx = out.getContext('2d'); octx.drawImage(outer, 0, 0); octx.globalCompositeOperation = 'destination-out'; octx.drawImage(inner, 0, 0); _saveState('Border selection'); state.wandMask = out; composite(); _syncToolClearIndicators(); return { width: wdt }; } },
          'selection.smooth': { description: 'Round the corners and soften jagged edges of the current selection by {radius} pixels (Photoshop Select > Modify > Smooth).', run: (p) => { if (!state.wandMask) throw new Error('no selection'); const r = Math.max(1, Math.min(64, Number(p.radius) || 4)); const sm = _featherMaskCanvas((cw, ch) => { const c = document.createElement('canvas'); c.width = cw || state.imgWidth; c.height = ch || state.imgHeight; return c; }, state.wandMask, r); const sctx = sm.getContext('2d'); const id = sctx.getImageData(0, 0, sm.width, sm.height); for (let i = 3; i < id.data.length; i += 4) id.data[i] = id.data[i] >= 128 ? 255 : 0; sctx.putImageData(id, 0, 0); _saveState('Smooth selection'); state.wandMask = sm; composite(); _syncToolClearIndicators(); return { radius: r }; } },
          'selection.grow': { description: 'Grow the current selection by {amount} pixels, extending it into nearby pixels (Photoshop Select > Grow).', run: (p) => { if (!state.wandMask) throw new Error('no selection'); const amt = Math.max(1, Math.min(64, Number(p.amount) || 1)); _saveState('Grow selection'); state.wandMask = _growMaskCanvas(state.wandMask, amt, true); composite(); _syncToolClearIndicators(); return { amount: amt }; } },
          'selection.similar': { description: 'Extend the selection to every similar-coloured pixel across the layer (Photoshop Select > Similar) {fuzziness,mode}.', run: (p = {}) => { const layer = activeLayer(); if (!layer || !layer.canvas) throw new Error('no pixel layer'); const src = _getWandSource(layer); const fuzz = Number.isFinite(Number(p.fuzziness)) ? Number(p.fuzziness) : (state.colorRangeFuzz ?? 40); let target = null; if (state.wandMask) { try { const md = _maskCanvasForLayer(layer, false, 0); const dd = md.getContext('2d').getImageData(0, 0, src.w, src.h).data; let r = 0, g = 0, b = 0, n = 0; for (let i = 0; i < src.data.length; i += 4) { if (dd[i + 3] < 128) continue; r += src.data[i]; g += src.data[i + 1]; b += src.data[i + 2]; n += 1; } if (n) target = { r: Math.round(r / n), g: Math.round(g / n), b: Math.round(b / n) }; } catch (_) { target = null; } } if (!target) target = _hexToRgb(state.color || '#000000'); const mask = _colorRangeMask(src.data, src.w, src.h, target, fuzz); const mode = state.wandMask ? (p.mode || 'add') : (p.mode || 'replace'); _saveState('Select similar'); _commitSelectionMask(mask, layer, mode, 'color-range'); composite(); _syncToolClearIndicators(); return { color: target, fuzziness: fuzz, mode }; } },
          'text.edit': { description: 'Edit an existing text layer {id|index|name, content, font, size, color, align, tracking, lineHeight}.', run: (p) => { const l = _controlFindLayer(p) || activeLayer(); if (!l || l.kind !== 'text' || !l.text) throw new Error('text layer not found'); _saveState('Edit text'); if (p.content != null) l.text.content = String(p.content); if (p.font) l.text.font = p.font; if (p.size != null) l.text.size = Number(p.size); if (p.color) l.text.color = p.color; if (p.align) l.text.align = p.align; if (p.tracking != null) l.text.tracking = Number(p.tracking); if (p.lineHeight != null) l.text.lineHeight = Number(p.lineHeight); _rerenderTextLayer(l, l.text.content); composite(); _renderLayerPanel(); return l.id; } },
          'document.crop': { description: 'Crop the document {x,y,width,height} (or use the current selection bounds when omitted).', run: (p) => { let rect = null; if (p && (p.width || p.w) && (p.height || p.h)) rect = { x: Number(p.x) || 0, y: Number(p.y) || 0, width: Number(p.width || p.w), height: Number(p.height || p.h) }; else if (state.wandMask) { const b = _maskAlphaBounds(state.wandMask.getContext('2d').getImageData(0, 0, state.imgWidth, state.imgHeight).data, state.imgWidth, state.imgHeight, 8); if (b) rect = { x: b.x, y: b.y, width: b.w, height: b.h }; } if (!rect) throw new Error('crop rect or selection required'); _saveState('Crop'); _cropDocument(state, rect); composite(); _renderLayerPanel(); _schedulePersist(); try { _fitZoom(); } catch (_) {} return rect; } },
          'document.trim': { description: 'Trim the document to the non-transparent bounds of the active pixel layer (Photo > Trim).', run: () => { const cur = activeLayer(); const target = (cur && cur.kind !== 'adjustment' && cur.canvas) ? cur : state.layers.slice().reverse().find((l) => l && l.canvas && l.kind !== 'adjustment'); if (!target) throw new Error('no pixel layer to trim'); const w = target.canvas.width, h = target.canvas.height; const b = _maskAlphaBounds(target.ctx.getImageData(0, 0, w, h).data, w, h, 8); if (!b) throw new Error('nothing to trim'); const off = state.layerOffsets.get(target.id) || { x: 0, y: 0 }; const rect = { x: Math.round(off.x + b.x), y: Math.round(off.y + b.y), width: b.w, height: b.h }; if (rect.x <= 0 && rect.y <= 0 && rect.width >= state.imgWidth && rect.height >= state.imgHeight) return { trimmed: false, rect }; _saveState('Trim'); _cropDocument(state, rect); composite(); _renderLayerPanel(); _schedulePersist(); try { _fitZoom(); } catch (_) {} return { trimmed: true, rect }; } },
          'document.savePsd': { description: 'Export the layered document as a PSD (also triggers a browser download). Returns {filename,size,dataUrl,layerNames}.', run: () => { const payload = _exportLayeredPsd(); if (!payload) throw new Error('no layers to export'); return { filename: payload.filename, size: payload.bytes.length, dataUrl: payload.dataUrl, layerNames: payload.layerNames }; } },
          'document.extend': { description: 'Extend the canvas to a new size without cropping the image {width,height,anchor:top-left|top|top-right|left|center|right|bottom-left|bottom|bottom-right}.', run: (p) => { const nw = Math.max(1, Math.round(Number(p && (p.width || p.w)) || 0)); const nh = Math.max(1, Math.round(Number(p && (p.height || p.h)) || 0)); if (!nw || !nh) throw new Error('width and height required'); const a = String((p && p.anchor) || 'center').toLowerCase(); const ax = /left/.test(a) ? 0 : /right/.test(a) ? 1 : 0.5; const ay = /top/.test(a) ? 0 : /bottom/.test(a) ? 1 : 0.5; _saveState('Extend canvas'); _resizeCanvasDocument(state, nw, nh, { anchorX: ax, anchorY: ay }); composite(); _renderLayerPanel(); _schedulePersist(); try { _fitZoom(); } catch (_) {} return { width: nw, height: nh, anchor: a }; } },
          'document.resizeCanvas': { description: 'Resample the whole image to a new pixel size {width,height}. Aspect-preserving by default: if the target aspect differs from the current image it first crops to that aspect (centred on the subject/selection when present) and then scales UNIFORMLY, so content is never stretched. Pass {allowDistort:true} for an intentional non-uniform resize.', run: (p = {}) => { const nw = Math.max(1, Math.round(Number(p && (p.width || p.w)) || 0)); const nh = Math.max(1, Math.round(Number(p && (p.height || p.h)) || 0)); if (!nw || !nh) throw new Error('width and height required'); const tgtAspect = nw / nh; if (!p.allowDistort) { const focus = _contentFocusCenter(); const rect = _aspectFitCropRect({ width: state.imgWidth, height: state.imgHeight, focusX: focus.x, focusY: focus.y, targetAspect: tgtAspect }); if (rect && (Math.abs(rect.width - state.imgWidth) > 1 || Math.abs(rect.height - state.imgHeight) > 1)) { _saveState('Crop to aspect'); _cropDocument(state, { x: rect.x, y: rect.y, width: rect.width, height: rect.height }); } } _saveState('Resize image'); _resizeImageDocument(state, nw, nh, {}); composite(); _renderLayerPanel(); _schedulePersist(); try { _fitZoom(); } catch (_) {} return { width: nw, height: nh }; } },
          'document.exportLayers': { description: 'Return each visible layer as a PNG data URL.', run: () => state.layers.filter((l) => l.visible !== false).map((l) => { try { const c = _renderLayerOutput(l); return { id: l.id, name: l.name, dataUrl: c.toDataURL('image/png') }; } catch (_) { return { id: l.id, name: l.name, dataUrl: null }; } }) },
          'layers.addStyle': { description: 'Add a non-destructive layer style (drop-shadow, stroke, outer-glow, gradient-overlay, ...) to the active layer/group. {type,params}.', run: (p) => { const type = (p && p.type) || 'drop-shadow'; const layer = state.activeGroupId ? (state.layerGroups || []).find(g => g.id === state.activeGroupId) : activeLayer(); if (!layer) throw new Error('no layer'); _saveState(`Add ${type}`); _applyLayerStyleNonInteractive(layer, type, (p && p.params) || {}); composite(); _renderLayerPanel(); return { type }; } },
          'photo.autoTone': { description: 'Camera-Raw-style auto tone/colour: per-channel levels, auto white balance, exposure + contrast. Applies to the active pixel layer.', run: () => { const layer = _retouchTargetLayer(); if (!layer) throw new Error('no pixel layer'); const img = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height); const params = _analyzeAutoTone(img.data, img.width, img.height); const out = _applyAutoTone(img.data, img.width, img.height, params); _saveState('Auto tone'); _writeLayerData(layer, out); composite(); _renderLayerPanel(); return { brightness: params.brightness, contrast: params.contrast, layerId: layer.id }; } },
          'adjustments.equalize': { description: 'Histogram-equalise the active pixel layer for a balanced tonal range (Photoshop Image > Adjustments > Equalize).', run: () => { const layer = _retouchTargetLayer(); if (!layer) throw new Error('no pixel layer'); const w = layer.canvas.width, h = layer.canvas.height; const img = layer.ctx.getImageData(0, 0, w, h); const d = img.data; if (!d.length) return { changed: false }; const lut = new Uint8ClampedArray(256 * 3); let any = false; for (let c = 0; c < 3; c += 1) { const hist = new Uint32Array(256); let count = 0; for (let i = c; i < d.length; i += 4) { if (d[i + 3] === 0) continue; hist[d[i]] += 1; count += 1; } if (!count) continue; let cdfMin = 0; for (let v = 0; v < 256 && !cdfMin; v += 1) cdfMin = hist[v]; const denom = Math.max(1, count - cdfMin); let cum = 0; for (let v = 0; v < 256; v += 1) { cum += hist[v]; lut[c * 256 + v] = Math.round(((cum - cdfMin) / denom) * 255); } any = true; } if (!any) return { changed: false }; const out = new Uint8ClampedArray(d.length); for (let i = 0; i < d.length; i += 4) { out[i] = lut[d[i]]; out[i + 1] = lut[256 + d[i + 1]]; out[i + 2] = lut[512 + d[i + 2]]; out[i + 3] = d[i + 3]; } _saveState('Equalize'); _writeLayerData(layer, out); composite(); _renderLayerPanel(); return { changed: true, layerId: layer.id }; } },
          'retouch.skin': { description: 'Skin smoothing (frequency separation, keeps texture, preserves luminance) over skin tones, excluding eyes/brows/lips. {smooth,opacity,blemish,radius,threshold,dilate,feather}.', run: (p = {}) => { const layer = _retouchTargetLayer(); if (!layer) throw new Error('no pixel layer'); const w = layer.canvas.width, h = layer.canvas.height; const smoothR = Math.min(14, Math.max(2, Number(p.smooth) || 8)); const opac = Math.min(0.45, Math.max(0.2, Number.isFinite(Number(p.opacity)) ? Number(p.opacity) : 0.35)); const img = layer.ctx.getImageData(0, 0, w, h); const sel = _selectionMaskDataForLayer(layer); const skin = _skinMaskFromData(img.data, w, h, null); const feat = _featureExclusionMask(img.data, w, h, null); const featD = _dilateAlpha(feat, w, h, Number.isFinite(Number(p.dilate)) ? Number(p.dilate) : 3); let mask = _subtractMask(skin, featD); const featherPx = Number.isFinite(Number(p.feather)) ? Math.max(0, Number(p.feather)) : 4; if (featherPx > 0) { const mc = document.createElement('canvas'); mc.width = w; mc.height = h; mc.getContext('2d').putImageData(new ImageData(mask, w, h), 0, 0); const fc = _featherMaskCanvas((cw, ch) => { const c = document.createElement('canvas'); c.width = cw; c.height = ch; return c; }, mc, featherPx); mask = fc.getContext('2d').getImageData(0, 0, w, h).data; } const out = _frequencySeparationSmooth(img.data, w, h, mask, { lowRadius: 8, smoothRadius: smoothR, opacity: opac }); const final = (p.blemish === false) ? out : _blemishRemoval(out, w, h, mask, { radius: Math.min(6, Math.max(1, Number(p.radius) || 4)), threshold: Math.max(12, Number(p.threshold) || 16) }); _saveState('Skin retouch'); _writeLayerData(layer, final); composite(); _renderLayerPanel(); return { masked: !!sel, layerId: layer.id, opacity: opac }; } },
          'retouch.blemish': { description: 'Remove small blemishes within the selection or skin region. {radius,threshold}.', run: (p = {}) => { const layer = _retouchTargetLayer(); if (!layer) throw new Error('no pixel layer'); const img = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height); const sel = _selectionMaskDataForLayer(layer); const mask = sel || _skinMaskFromData(img.data, img.width, img.height, null); const out = _blemishRemoval(img.data, img.width, img.height, mask, { radius: Number(p.radius) || 4, threshold: Number(p.threshold) || 16 }); _saveState('Blemish removal'); _writeLayerData(layer, out); composite(); _renderLayerPanel(); return true; } },
          'retouch.teeth': { description: 'Whiten teeth (SAM text "teeth", falling back to the current selection); desaturates yellow and lifts brightness with a small feather. {desat,bright}.', run: async (p = {}) => { const layer = _retouchTargetLayer(); if (!layer) throw new Error('no pixel layer'); try { await _samWithRetry(layer, { text: 'teeth' }, 'replace', null); } catch (_) {} const img = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height); const sel = _selectionMaskDataForLayer(layer); const mask = sel || _skinMaskFromData(img.data, img.width, img.height, null); const out = _teethWhitening(img.data, img.width, img.height, mask, { desat: Number.isFinite(Number(p.desat)) ? Number(p.desat) : 0.6, bright: Number.isFinite(Number(p.bright)) ? Number(p.bright) : 16 }); _saveState('Whiten teeth'); _writeLayerData(layer, out); composite(); _renderLayerPanel(); return { masked: !!sel, layerId: layer.id }; } },
          'layers.group': { description: 'Group the current selection.', run: () => { document.getElementById('ge-group-selected')?.click(); return true; } },
          'layers.ungroup': { description: 'Ungroup the active group.', run: () => { const b = document.querySelector('.ge-layer-group-row [title="Ungroup layers"]'); if (!b) throw new Error('no group selected'); b.click(); return true; } },
          'selection.selectAll': { description: 'Select the whole document.', run: () => { _selectAllSelection(); return true; } },
          'selection.deselect': { description: 'Clear the selection.', run: () => { _deselectSelection(); return true; } },
          'selection.invert': { description: 'Invert the selection.', run: () => { _invertSelection(); return true; } },
          'selection.rectangle': { description: 'Rectangular selection {x,y,w,h}.', run: (p) => { const rect = { x: Number(p.x) || 0, y: Number(p.y) || 0, w: Number(p.w) || 0, h: Number(p.h) || 0 }; const mask = _createMarqueeMask(state.imgWidth, state.imgHeight, rect, p.shape === 'ellipse' ? 'ellipse' : 'rectangle'); _commitSelectionMask(mask, activeLayer(), 'replace', 'marquee', null); return rect; } },
          'selection.ellipse': { description: 'Elliptical selection {x,y,w,h}.', run: (p) => { const rect = { x: Number(p.x) || 0, y: Number(p.y) || 0, w: Number(p.w) || 0, h: Number(p.h) || 0 }; const mask = _createMarqueeMask(state.imgWidth, state.imgHeight, rect, 'ellipse'); _commitSelectionMask(mask, activeLayer(), 'replace', 'marquee', null); return rect; } },
          'selection.hasMask': { description: 'Whether a document selection exists.', run: () => ({ active: !!state.wandMask, space: state.wandMaskSpace }) },
          'selection.maskBounds': { description: 'Alpha bounds of the current selection mask in document coordinates.', run: () => { if (!state.wandMask) return null; const d = state.wandMask.getContext('2d').getImageData(0, 0, state.imgWidth, state.imgHeight).data; return _maskAlphaBounds(d, state.imgWidth, state.imgHeight, 8); } },
          'selection.exportMask': { description: 'Return the current document selection as a PNG data URL {mask,width,height}, or null when nothing is selected.', run: () => { const doc = _selectionMaskAsDocument({ materializeLasso: true }); if (!doc) return null; return { mask: doc.toDataURL('image/png'), width: doc.width, height: doc.height }; } },
          'selection.regionLuminance': { description: 'Mean luminance (0-255) of the active pixel layer, restricted to the current selection when one exists.', run: () => { const layer = activeLayer(); if (!layer || !layer.canvas) return { luminance: null, samples: 0, masked: false }; const w = layer.canvas.width, h = layer.canvas.height; const d = layer.ctx.getImageData(0, 0, w, h).data; const mask = state.wandMask ? _maskCanvasForLayer(layer, false, 0).getContext('2d').getImageData(0, 0, w, h).data : null; let sum = 0, n = 0; for (let i = 0; i < d.length; i += 4) { if (mask && mask[i + 3] < 64) continue; sum += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]; n++; } return { luminance: n ? Math.round((sum / n) * 10) / 10 : null, samples: n, masked: !!state.wandMask }; } },
          'selection.subject': { description: 'Select the main subject in one click. An offline saliency pass runs first (background estimated from the layer border, Otsu threshold, largest connected blob); if it finds no distinct subject it falls back to a server SAM text prompt {label}, then to a centred region. {label,mode}.', run: async (p = {}) => { const layer = activeLayer(); if (!layer || !layer.canvas || layer.kind === 'adjustment') throw new Error('select a pixel layer'); const mode = p.mode || state.wandMode || 'replace'; const offline = _applyOfflineSubjectSelection(layer, mode); if (offline) return { mode, method: 'saliency', width: offline.width, height: offline.height }; const text = String(p.label || p.text || 'person').trim(); try { await _samWithRetry(layer, { text }, mode, null); return { text, mode, method: 'sam' }; } catch (e) { const w = state.imgWidth, h = state.imgHeight; const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 }; const lw = (layer.canvas && layer.canvas.width) || w, lh = (layer.canvas && layer.canvas.height) || h; const rect = { x: Math.round(off.x + lw * 0.18), y: Math.round(off.y + lh * 0.04), w: Math.round(lw * 0.64), h: Math.round(lh * 0.92) }; const mask = _createMarqueeMask(w, h, rect, 'rectangle'); _saveState('Select subject (approx)'); _commitSelectionMask(mask, layer, mode, 'subject-approx'); composite(); _syncToolClearIndicators(); return { text, mode, method: 'approx', reason: String(e && e.message || e) }; } } },
          'selection.samText': { description: 'Select an object by text with SAM {text,mode,fallbackColor,fuzziness}. When the SAM mask comes back empty and fallbackColor is given, fall back to a colour-range selection (keeps the caller\'s intent when SAM cannot isolate the object).', run: async (p) => { const layer = activeLayer(); if (!layer) throw new Error('no layer'); const text = String(p.text || '').trim(); if (!text) throw new Error('text required'); const mode = p.mode || state.wandMode || 'replace'; await _samWithRetry(layer, { text }, mode, null); let empty = true; if (state.wandMask && state.wandMask.width > 0) { try { const md = state.wandMask.getContext('2d').getImageData(0, 0, state.wandMask.width, state.wandMask.height).data; empty = !_maskAlphaBounds(md, state.wandMask.width, state.wandMask.height, 8); } catch (_) { empty = false; } } if (empty && p.fallbackColor) { const src = _getWandSource(layer); const target = _hexToRgb(typeof p.fallbackColor === 'string' ? p.fallbackColor : (state.color || '#000000')); const fuzz = Number.isFinite(Number(p.fuzziness)) ? Number(p.fuzziness) : (state.colorRangeFuzz ?? 40); const mask = _colorRangeMask(src.data, src.w, src.h, target, fuzz); _saveState('Color range selection (SAM fallback)'); _commitSelectionMask(mask, layer, mode, 'color-range'); composite(); _syncToolClearIndicators(); return { text, mode, fallback: 'colorRange', color: p.fallbackColor, fuzziness: fuzz }; } return { text, mode, empty }; } },
          'selection.face': { description: 'Select a face (SAM text "face", falling back to an upper-centre ellipse) {mode}.', run: async (p) => { const layer = activeLayer(); if (!layer) throw new Error('no layer'); const mode = p.mode || state.wandMode || 'replace'; try { await _samWithRetry(layer, { text: 'face' }, mode, null); return { text: 'face', method: 'sam' }; } catch (e) { const w = state.imgWidth, h = state.imgHeight; const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 }; const lw = (layer.canvas && layer.canvas.width) || w, lh = (layer.canvas && layer.canvas.height) || h; const rect = { x: Math.round(off.x + lw * 0.3), y: Math.round(off.y + lh * 0.1), w: Math.round(lw * 0.4), h: Math.round(lh * 0.34) }; const mask = _createMarqueeMask(w, h, rect, 'ellipse'); _saveState('Select face (approx)'); _commitSelectionMask(mask, layer, mode, 'face-approx'); composite(); _syncToolClearIndicators(); return { text: 'face', method: 'approx', reason: String(e && e.message || e) }; } } },
          'selection.sky': { description: 'Select the sky (SAM text, falling back to a top-region color range).', run: async (p) => { const layer = activeLayer(); if (!layer) throw new Error('no layer'); const mode = p.mode || state.wandMode || 'replace'; try { await _samWithRetry(layer, { text: 'sky' }, mode, null); return { text: 'sky', method: 'sam' }; } catch (e) { const w = state.imgWidth, h = state.imgHeight; const src = _getWandSource(layer); const band = Math.max(1, Math.round(src.h * 0.06)); let r = 0, g = 0, b = 0, n = 0; for (let y = 0; y < band; y++) { for (let x = 0; x < src.w; x += 4) { const i = (y * src.w + x) * 4; r += src.data[i]; g += src.data[i + 1]; b += src.data[i + 2]; n++; } } r = Math.round(r / n); g = Math.round(g / n); b = Math.round(b / n); const mask = _colorRangeMask(src.data, src.w, src.h, { r, g, b }, Number(p.fuzziness) || 55); _saveState('Select sky (approx)'); _commitSelectionMask(mask, layer, mode, 'sky-approx'); composite(); _syncToolClearIndicators(); return { method: 'approx', color: `rgb(${r},${g},${b})`, reason: String(e && e.message || e) }; } } },
          'selection.background': { description: 'Select the background: SAM subject inverted when possible, otherwise union the four corner colours {fuzziness,mode,subject}.', run: async (p) => { const layer = activeLayer(); if (!layer) throw new Error('no layer'); const mode = p.mode || state.wandMode || 'replace'; const subjText = String(p.subject || 'person').trim(); try { await _samWithRetry(layer, { text: subjText }, 'replace', null); _invertSelection(); return { method: 'sam-invert', subject: subjText }; } catch (eSam) { const src = _getWandSource(layer); const fuzz = Number.isFinite(Number(p.fuzziness)) ? Number(p.fuzziness) : 30; const pts = [[2, 2], [src.w - 3, 2], [2, src.h - 3], [src.w - 3, src.h - 3], [Math.floor(src.w / 2), 2], [2, Math.floor(src.h / 2)], [src.w - 3, Math.floor(src.h / 2)]]; const out = document.createElement('canvas'); out.width = src.w; out.height = src.h; const octx = out.getContext('2d'); for (const [x, y] of pts) { const i = (Math.max(0, Math.min(src.h - 1, y)) * src.w + Math.max(0, Math.min(src.w - 1, x))) * 4; const c = { r: src.data[i], g: src.data[i + 1], b: src.data[i + 2] }; octx.drawImage(_colorRangeMask(src.data, src.w, src.h, c, fuzz), 0, 0); } _saveState('Select background'); _commitSelectionMask(out, layer, mode, 'background'); composite(); _syncToolClearIndicators(); return { fuzziness: fuzz, method: 'corner-colors', reason: String(eSam && eSam.message || eSam) }; } } },
          'selection.colorRange': { description: 'Select a color range {color,fuzziness,mode}.', run: (p) => { const layer = activeLayer(); if (!layer) throw new Error('no layer'); const src = _getWandSource(layer); const target = _hexToRgb(p.color || state.color || '#000000'); const fuzz = Number.isFinite(Number(p.fuzziness)) ? Number(p.fuzziness) : (state.colorRangeFuzz ?? 40); const mask = _colorRangeMask(src.data, src.w, src.h, target, fuzz); _saveState('Color range selection'); _commitSelectionMask(mask, layer, p.mode || state.wandMode || 'replace', 'color-range'); composite(); _syncToolClearIndicators(); return { color: p.color || state.color, fuzziness: fuzz }; } },
          'colorLookup.loadCube': { description: 'Import an Adobe .cube 3D LUT from its text and apply it to the active layer as a Color Lookup adjustment ({text, strength}).', run: (p = {}) => { const text = String(p.text || ''); if (!text.trim()) throw new Error('text required'); const lut = _parseCubeLut(text); const cube = { size: lut.size, domainMin: Array.from(lut.domainMin), domainMax: Array.from(lut.domainMax), data: Array.from(lut.data) }; const strength = p.strength == null ? 100 : Number(p.strength); _addAdjustmentLayer('color-lookup', document.getElementById('ge-add-layer')); const l = activeLayer(); if (!l || !l._stagedAdj) throw new Error('adjustment not staged'); l.clipped = true; l._stagedAdj.params = { preset: 'cube', cube, strength }; const ok = document.querySelector('.ge-adj-popup [data-adj-action="ok"]'); if (!ok) throw new Error('adjustment popup missing'); ok.click(); return { layerId: l.id, size: cube.size, strength }; } },
          'colorLookup.exportCube': { description: 'Return the .cube text for the active Color Lookup adjustment (or for a supplied {cube}).', run: (p = {}) => { let cube = p && p.cube && p.cube.size ? p.cube : null; if (!cube) { const l = activeLayer(); const adj = l && l.adjustment; if (adj && adj.type === 'color-lookup' && adj.params && adj.params.cube && adj.params.cube.size) cube = adj.params.cube; } if (!cube) throw new Error('no cube loaded'); return { text: _generateCubeLut({ size: cube.size, domainMin: cube.domainMin || [0, 0, 0], domainMax: cube.domainMax || [1, 1, 1], data: cube.data }) }; } },
          'adjustments.apply': { description: 'Add an adjustment layer and commit it with params ({type,params}; also accepts the shorthand {adjustment,value} / flat numeric fields). Clipped to the target pixel layer so it only affects that layer and respects its alpha.', run: (p) => { if (!p) throw new Error('params required'); const { adjType, params } = _normalizeAdjustmentArgs(p); if (!adjType) throw new Error('type required'); _addAdjustmentLayer(adjType, document.getElementById('ge-add-layer')); const l = activeLayer(); if (!l || !l._stagedAdj) throw new Error('adjustment not staged'); l.clipped = true; if (params && Object.keys(params).length) Object.assign(l._stagedAdj.params, _normalizeAdjustmentParams(adjType, params)); const ok = document.querySelector('.ge-adj-popup [data-adj-action="ok"]'); if (!ok) throw new Error('adjustment popup missing'); ok.click(); return { layerId: l.id, type: adjType, clipped: !!l.clipped }; } },
          'filters.apply': { description: 'Apply a filter by its menu id (e.g. blur-gaussian).', run: async (p = {}) => { const _FILTER_ALIAS = { dehaze: 'dehaze', 'haze-removal': 'dehaze', haze: 'dehaze', blur: 'blur-gaussian', 'gaussian-blur': 'blur-gaussian', 'motion-blur': 'blur-motion', 'zoom-blur': 'blur-zoom', 'radial-blur': 'blur-zoom', sharpen: 'sharpen', 'sharpen-edges': 'sharpen-edges', unsharp: 'unsharp-mask', 'unsharp-mask': 'unsharp-mask', 'smart-sharpen': 'unsharp-mask', noise: 'noise-add', 'add-noise': 'noise-add', despeckle: 'noise-despeckle', 'reduce-noise': 'noise-despeckle', 'dust-scratches': 'noise-despeckle', 'box-blur': 'blur-box', 'surface-blur': 'blur-surface', custom: 'other-custom', 'custom-kernel': 'other-custom', maximum: 'other-maximum', minimum: 'other-minimum', median: 'noise-median', 'polar-coordinates': 'distort-polar', polar: 'distort-polar', 'difference-clouds': 'render-difference-clouds', 'diff-clouds': 'render-difference-clouds', 'lens-flare': 'render-lens-flare', flare: 'render-lens-flare', fibers: 'render-fibers', fiber: 'render-fibers', 'oil-paint': 'stylize-oil-paint', oil: 'stylize-oil-paint', 'lens-correction': 'distort-lens-correction', 'lens-correct': 'distort-lens-correction', diffuse: 'stylize-diffuse', 'ocean-ripple': 'distort-ocean-ripple', ocean: 'distort-ocean-ripple', glass: 'distort-glass', 'mosaic-tiles': 'pixelate-mosaic-tiles', 'mosaic-tile': 'pixelate-mosaic-tiles', wind: 'stylize-wind', 'trace-contour': 'stylize-trace-contour', 'halftone-pattern': 'sketch-halftone-pattern', halftone: 'sketch-halftone-pattern', reticulation: 'sketch-reticulation', shear: 'distort-shear', 'note-paper': 'sketch-note-paper', notepaper: 'sketch-note-paper', glow: 'blur-gaussian', 'soft-glow': 'blur-gaussian', 'soft-focus': 'blur-gaussian', pixelate: 'pixelate-mosaic', mosaic: 'pixelate-mosaic', crystallize: 'pixelate-crystallize', emboss: 'stylize-emboss', 'find-edges': 'stylize-find-edges', solarize: 'stylize-solarize', twirl: 'distort-twirl', pinch: 'distort-pinch', spherize: 'distort-spherize', ripple: 'distort-ripple', clouds: 'render-clouds', vignette: 'vignette', 'post-crop-vignette': 'vignette', 'darken-edges': 'vignette', grain: 'noise-add', 'film-grain': 'noise-add', clarity: 'clarity', 'local-contrast': 'clarity', 'micro-contrast': 'clarity', structure: 'clarity', 'high-pass': 'high-pass', highpass: 'high-pass', 'high-pass-filter': 'high-pass', 'lens-blur': 'lens-blur', 'depth-blur': 'lens-blur', 'depth-of-field': 'lens-blur', 'dof': 'lens-blur', 'tilt-shift': 'lens-blur', 'bokeh-blur': 'lens-blur', charcoal: 'sketch-charcoal', 'chalk-charcoal': 'sketch-chalk-charcoal', chalkcharcoal: 'sketch-chalk-charcoal', 'graphic-pen': 'sketch-graphic-pen', graphicpen: 'sketch-graphic-pen', photocopy: 'sketch-photocopy', 'water-paper': 'sketch-water-paper', waterpaper: 'sketch-water-paper', 'sharpen-more': 'sharpen-more', 'poster-edges': 'artistic-poster-edges', posteredges: 'artistic-poster-edges', cutout: 'artistic-cutout', 'ink-outlines': 'brush-ink-outlines', inkoutlines: 'brush-ink-outlines', 'stained-glass': 'texture-stained-glass', stainedglass: 'texture-stained-glass', 'texture-grain': 'texture-grain', grain: 'texture-grain', 'texture-texturizer': 'texture-texturizer', texturizer: 'texture-texturizer', 'texture-patchwork': 'texture-patchwork', patchwork: 'texture-patchwork', 'texture-craquelure': 'texture-craquelure', craquelure: 'texture-craquelure', 'brush-crosshatch': 'brush-crosshatch', crosshatch: 'brush-crosshatch' }; const rawId = p.id || p.filter || p.type || p.name || p.effect; if (!rawId) throw new Error('id required'); const id = _FILTER_ALIAS[String(rawId).toLowerCase()] || rawId; if (id === 'dehaze') { const cur = activeLayer(); const layer = (cur && cur.kind !== 'adjustment' && cur.canvas) ? cur : state.layers.slice().reverse().find((l) => l && l.canvas && l.kind !== 'adjustment'); if (!layer) throw new Error('no pixel layer for dehaze'); const w = layer.canvas.width, h = layer.canvas.height; let amt = Number(p.amount); if (!Number.isFinite(amt)) amt = 70; else if (amt <= 1) amt *= 100; const img = layer.ctx.getImageData(0, 0, w, h); const out = _dehazeImageData(img.data, w, h, { strength: Math.max(0, Math.min(100, amt)) / 100 }); _saveState('Haze Removal'); layer.ctx.clearRect(0, 0, w, h); layer.ctx.putImageData(new ImageData(out, w, h), 0, 0); composite(); _renderLayerPanel(); return { id: 'dehaze', direct: true, amount: amt }; } if (id === 'clarity') { const cur = activeLayer(); const layer = (cur && cur.kind !== 'adjustment' && cur.canvas) ? cur : state.layers.slice().reverse().find((l) => l && l.canvas && l.kind !== 'adjustment'); if (!layer) throw new Error('no pixel layer for clarity'); let amt = Number(p.amount); if (!Number.isFinite(amt)) amt = 20; else if (amt <= 1) amt *= 100; const rad = Math.max(1, Math.min(80, Number(p.radius) || 30)); const clampAmt = Number.isFinite(Number(p.clamp)) ? Math.max(0, Number(p.clamp)) : 10; const w = layer.canvas.width, h = layer.canvas.height; const img = layer.ctx.getImageData(0, 0, w, h); let out; try { out = _guidedClarity(img.data, w, h, { amount: amt, radius: rad, clamp: clampAmt }); } catch (_) { out = img.data; } _saveState('Clarity'); layer.ctx.putImageData(new ImageData(out, w, h), 0, 0); composite(); _renderLayerPanel(); return { id: 'clarity', direct: true, edgeAware: true }; } if (id === 'vignette') { const cur = activeLayer(); const layer = (cur && cur.kind !== 'adjustment' && cur.canvas) ? cur : state.layers.slice().reverse().find((l) => l && l.canvas && l.kind !== 'adjustment'); if (!layer) throw new Error('no pixel layer for vignette'); const w = layer.canvas.width, h = layer.canvas.height; let araw = Number(p.amount); if (!Number.isFinite(araw)) araw = 30; else if (araw <= 1) araw *= 100; const amt = Math.max(0, Math.min(100, araw)) / 100; let fraw = Number(p.feather); if (!Number.isFinite(fraw)) fraw = 0.55; else if (fraw > 1) fraw /= 100; const feather = Math.max(0.1, Math.min(1, fraw)); _saveState('Vignette'); const g = layer.ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * (1 - feather) * 0.5, w / 2, h / 2, Math.max(w, h) * 0.72); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, `rgba(0,0,0,${amt})`); layer.ctx.save(); layer.ctx.fillStyle = g; layer.ctx.fillRect(0, 0, w, h); layer.ctx.restore(); composite(); _renderLayerPanel(); return { id: 'vignette', direct: true }; } if (id === 'high-pass') { const cur = activeLayer(); const layer = (cur && cur.kind !== 'adjustment' && cur.canvas) ? cur : state.layers.slice().reverse().find((l) => l && l.canvas && l.kind !== 'adjustment'); if (!layer) throw new Error('no pixel layer for high-pass'); const w = layer.canvas.width, h = layer.canvas.height; let rad = Number(p.radius); if (!Number.isFinite(rad)) rad = 10; rad = Math.max(0.5, Math.min(60, rad)); const snap = _snapshotLayer(layer); const tmp = document.createElement('canvas'); tmp.width = w; tmp.height = h; try { _gaussianBlur(snap, { radius: rad }, tmp.getContext('2d')); } catch (_) { tmp.getContext('2d').drawImage(snap, 0, 0); } const base = snap.getContext('2d').getImageData(0, 0, w, h); const blurd = tmp.getContext('2d').getImageData(0, 0, w, h); const out = layer.ctx.createImageData(w, h); for (let i = 0; i < base.data.length; i += 4) { out.data[i] = Math.max(0, Math.min(255, 128 + (base.data[i] - blurd.data[i]))); out.data[i + 1] = Math.max(0, Math.min(255, 128 + (base.data[i + 1] - blurd.data[i + 1]))); out.data[i + 2] = Math.max(0, Math.min(255, 128 + (base.data[i + 2] - blurd.data[i + 2]))); out.data[i + 3] = base.data[i + 3]; } _saveState('High pass'); layer.ctx.clearRect(0, 0, w, h); layer.ctx.putImageData(out, 0, 0); composite(); _renderLayerPanel(); return { id: 'high-pass', direct: true, radius: rad }; } if (id === 'lens-blur') { const cur = activeLayer(); const layer = (cur && cur.kind !== 'adjustment' && cur.canvas) ? cur : state.layers.slice().reverse().find((l) => l && l.canvas && l.kind !== 'adjustment'); if (!layer) throw new Error('no pixel layer for lens-blur'); const w = layer.canvas.width, h = layer.canvas.height; let rad = Number(p.radius); if (!Number.isFinite(rad)) rad = 14; rad = Math.max(1, Math.min(80, rad)); let focus = Number(p.focus); if (!Number.isFinite(focus)) focus = 0.5; focus = Math.max(0, Math.min(1, focus)); let band = Number(p.band); if (!Number.isFinite(band)) band = 0.28; band = Math.max(0.02, Math.min(0.9, band)); let feather = Number(p.feather); if (!Number.isFinite(feather)) feather = 0.16; feather = Math.max(0.01, Math.min(0.45, feather)); const snap = _snapshotLayer(layer); const blur = document.createElement('canvas'); blur.width = w; blur.height = h; const bctx = blur.getContext('2d'); try { _gaussianBlur(snap, { radius: rad }, bctx); } catch (_) { bctx.drawImage(snap, 0, 0); } const bandMask = document.createElement('canvas'); bandMask.width = w; bandMask.height = h; const mctx2 = bandMask.getContext('2d'); const grad = mctx2.createLinearGradient(0, 0, 0, h); const bandTop = Math.max(0, focus - band), bandBot = Math.min(1, focus + band), topEdge = Math.max(0, focus - band - feather), botEdge = Math.min(1, focus + band + feather); grad.addColorStop(0, 'rgba(255,255,255,0)'); grad.addColorStop(Math.max(0.001, topEdge), 'rgba(255,255,255,0)'); grad.addColorStop(Math.max(0.002, bandTop), 'rgba(255,255,255,1)'); grad.addColorStop(Math.min(0.998, bandBot), 'rgba(255,255,255,1)'); grad.addColorStop(Math.min(0.999, botEdge), 'rgba(255,255,255,0)'); grad.addColorStop(1, 'rgba(255,255,255,0)'); mctx2.fillStyle = grad; mctx2.fillRect(0, 0, w, h); bctx.save(); bctx.globalCompositeOperation = 'destination-out'; bctx.drawImage(bandMask, 0, 0); bctx.restore(); const sharp = document.createElement('canvas'); sharp.width = w; sharp.height = h; const sctx = sharp.getContext('2d'); sctx.drawImage(snap, 0, 0); sctx.globalCompositeOperation = 'destination-in'; sctx.drawImage(bandMask, 0, 0); _saveState('Lens blur'); layer.ctx.clearRect(0, 0, w, h); layer.ctx.drawImage(blur, 0, 0); layer.ctx.drawImage(sharp, 0, 0); composite(); _renderLayerPanel(); return { id: 'lens-blur', direct: true, focus, band }; } const item = document.querySelector(`[data-filter-action="${id}"]`); if (!item) throw new Error(`unknown filter: ${id}`); const current = activeLayer(); if (!current || current.kind === 'adjustment' || !current.canvas) { const idx = state.layers.findIndex((l) => l.id === state.activeLayerId); let pick = null; for (let i = (idx < 0 ? state.layers.length - 1 : idx - 1); i >= 0; i--) { const l = state.layers[i]; if (l && l.kind !== 'adjustment' && l.canvas) { pick = l; break; } } if (!pick) { for (let i = 0; i < state.layers.length; i++) { const l = state.layers[i]; if (l && l.kind !== 'adjustment' && l.canvas) { pick = l; break; } } } if (!pick) throw new Error('no pixel layer for filter'); state.activeLayerId = pick.id; state.selectedLayerIds = [pick.id]; try { _renderLayerPanel(); } catch (_) {} } item.click(); const apply = await new Promise((resolve) => { let tries = 0; const t = setInterval(() => { const b = document.querySelector('.ge-filter-overlay .ge-filter-modal-actions [data-action="apply"]'); if (b || ++tries > 60) { clearInterval(t); resolve(b || null); } }, 25); }); if (!apply) throw new Error(`filter dialog did not open for: ${id}`); apply.click(); return { id }; } },
          'filters.applyMasked': { description: 'Apply a filter only within the selection {id,radius/amount,invert}.', run: (p) => { const layer = activeLayer(); if (!layer || layer.kind === 'adjustment') throw new Error('select a pixel layer'); const hasMask = !!state.wandMask; const snap = _snapshotLayer(layer); const out = document.createElement('canvas'); out.width = layer.canvas.width; out.height = layer.canvas.height; const octx = out.getContext('2d'); const id = p.id || 'blur-gaussian'; const haloFreeBgBlur = id === 'blur-gaussian' && hasMask && !!p.invert; if (haloFreeBgBlur) { octx.drawImage(_backgroundBlurPlate(layer, snap, Number(p.radius) || 10), 0, 0); } else if (id === 'blur-gaussian') _gaussianBlur(snap, { radius: Number(p.radius) || 10 }, octx); else if (id === 'sharpen') _sharpen(snap, { amount: Number(p.amount) || 60 }, octx); else if (id === 'unsharp-mask') _unsharpMask(snap, { amount: Number(p.amount) || 80 }, octx); else throw new Error(`unsupported masked filter: ${id}`); _saveState(`Masked ${id}`); _commitMaskedEffect(layer, snap, out, hasMask ? !!p.invert : false); composite(); _renderLayerPanel(); return { id, invert: hasMask ? !!p.invert : false, masked: hasMask, haloFree: haloFreeBgBlur }; } },
          'adjustments.applyMasked': { description: 'Apply masked pixel adjustments {brightness,contrast,saturation,warmth,hue,invert,sourceHue} within the selection. hue is a hue rotation in degrees (0-360) that preserves lightness and saturation and is limited to pixels near sourceHue (or the dominant masked hue) with a soft falloff, so windows/chrome keep their colour.', run: (p) => { const layer = activeLayer(); if (!layer || layer.kind === 'adjustment') throw new Error('select a pixel layer'); _saveState('Masked adjustment'); _applyMaskedAdjust(layer, p || {}); composite(); _renderLayerPanel(); return { masked: !!state.wandMask }; } },
          'transform.start': { description: 'Begin a transform.', run: () => { _startTransform(); return true; } },
          'transform.apply': { description: 'Commit the active transform.', run: () => { _confirmTransform(); return true; } },
          'transform.cancel': { description: 'Cancel the active transform.', run: () => { _cancelTransform(); return true; } },
          'text.add': { description: 'Add a text layer {text,x,y,color,size,font,align}.', run: (p) => { _saveState('Add text'); const layer = createLayer('Text', 1, 1); layer.kind = 'text'; layer.text = _textDataFromControls({ color: p.color || state.color, frameWidth: 320, autoWidth: false }); layer.text.content = p.text || 'Text'; if (p.color) layer.text.color = p.color; if (p.size) layer.text.fontSize = Number(p.size); if (p.font) layer.text.fontFamily = p.font; if (p.align) layer.text.align = p.align; _renderTextLayer(layer); state.layerOffsets.set(layer.id, { x: Math.round(Number(p.x) || 0), y: Math.round(Number(p.y) || 0) }); state.layers.push(layer); state.activeLayerId = layer.id; _renderLayerPanel(); composite(); return layer.id; } },
          'path.add': { description: 'Create a vector path layer from document-space points [{x,y},…] {points,closed?}.', run: (p = {}) => { const pts = Array.isArray(p.points) ? p.points.filter((q) => Number.isFinite(Number(q && q.x)) && Number.isFinite(Number(q && q.y))).map((q) => ({ x: Number(q.x), y: Number(q.y) })) : []; if (pts.length < 2) throw new Error('need at least two points'); _saveState('Add path'); const layer = _ensurePathLayer(); _drawPathDraft(layer, pts); _commitPathDraft(); _renderLayerPanel(); composite(); return { id: layer.id, points: pts.length }; } },
          'path.addCurve': { description: 'Create a curved vector path layer from an analytic curve: {type:"ellipse",cx,cy,rx,ry,startDeg?,endDeg?,rotationDeg?} | {type:"bezier"|"cubic",points:[p0,c1,c2,p3]} | {type:"quadratic",points:[p0,c1,p2]} | {type:"smooth",points:[…]}. The curve is flattened to a dense polyline.', run: (p = {}) => { const type = String(p.type || 'ellipse').toLowerCase(); let pts = []; if (type === 'ellipse' || type === 'arc') { const cx = Number(p.cx); const cy = Number(p.cy); const rx = Math.abs(Number(p.rx)); const ry = Math.abs(Number.isFinite(Number(p.ry)) ? Number(p.ry) : Number(p.rx)); if (![cx, cy, rx, ry].every(Number.isFinite)) throw new Error('cx, cy, rx, ry required'); const start = (Number(p.startDeg) || 0) * Math.PI / 180; const end = p.endDeg == null ? start + Math.PI * 2 : (Number(p.endDeg) || 0) * Math.PI / 180; pts = _ellipsePoints(cx, cy, rx, ry, { start, end, rotation: (Number(p.rotationDeg) || 0) * Math.PI / 180 }); } else if (type === 'bezier' || type === 'cubic') { const c = Array.isArray(p.points) ? p.points : []; if (c.length < 4) throw new Error('bezier needs 4 points'); pts = _cubicBezierPoints(c[0], c[1], c[2], c[3]); } else if (type === 'quadratic') { const c = Array.isArray(p.points) ? p.points : []; if (c.length < 3) throw new Error('quadratic needs 3 points'); pts = _quadraticBezierPoints(c[0], c[1], c[2]); } else if (type === 'smooth') { pts = _smoothPathPoints(Array.isArray(p.points) ? p.points : []); } else { throw new Error('unknown curve type: ' + type); } if (pts.length < 2) throw new Error('curve needs at least two points'); _saveState('Add curved path'); const layer = _ensurePathLayer(); _drawPathDraft(layer, pts); layer.shape = _normalizeShapeData({ ...layer.shape, fillColor: p.fill || 'rgba(0,0,0,0)', strokeColor: p.stroke || '#2b8cff' }); _renderShapeLayer(layer); _commitPathDraft(); _renderLayerPanel(); composite(); return { id: layer.id, points: pts.length, type }; } },
          'text.onCircle': { description: 'Lay a text layer around a circle. {layerId?, cx?, cy?, radius?} — defaults to the text layer centre and a radius of ~30% of the shorter canvas side.', run: (p = {}) => { const textLayer = (p.layerId ? _controlFindLayer(p) : null) || (activeLayer() && activeLayer().kind === 'text' ? activeLayer() : null) || state.layers.slice().reverse().find((l) => l && l.kind === 'text'); if (!textLayer) throw new Error('no text layer'); const w = textLayer.canvas ? textLayer.canvas.width : 0; const h = textLayer.canvas ? textLayer.canvas.height : 0; const off = state.layerOffsets.get(textLayer.id) || { x: 0, y: 0 }; const cx = Number.isFinite(Number(p.cx)) ? Number(p.cx) : Math.round(off.x + w / 2); const cy = Number.isFinite(Number(p.cy)) ? Number(p.cy) : Math.round(off.y + h / 2); const radius = Number.isFinite(Number(p.radius)) ? Math.abs(Number(p.radius)) : Math.max(40, Math.round(Math.min(state.imgWidth, state.imgHeight) * 0.3)); _saveState('Text on a circle'); const pts = _ellipsePoints(cx, cy, radius, radius, { start: Math.PI / 2, end: Math.PI / 2 + Math.PI * 2 }); const layer = _ensurePathLayer(); _drawPathDraft(layer, pts); layer.shape = _normalizeShapeData({ ...layer.shape, fillColor: 'rgba(0,0,0,0)', strokeColor: '#2b8cff' }); _renderShapeLayer(layer); const result = _attachTextToPath(textLayer, layer); if (!result) throw new Error('could not attach text'); state.activeLayerId = textLayer.id; state.selectedLayerIds = [textLayer.id]; _renderLayerPanel(); composite(); return { ...result, radius, cx, cy }; } },
          'text.attachToPath': { description: 'Lay a text layer along a path layer (text on a path). {layerId?, pathLayerId?} — defaults to the active/newest text layer and the selected/newest path layer.', run: (p = {}) => { const textLayer = (p.layerId ? _controlFindLayer(p) : null) || (activeLayer() && activeLayer().kind === 'text' ? activeLayer() : null) || state.layers.slice().reverse().find((l) => l && l.kind === 'text'); if (!textLayer) throw new Error('no text layer'); const active = activeLayer(); const pathLayer = (p.pathLayerId ? state.layers.find((l) => l.id === p.pathLayerId) : null) || (active && active.kind === 'shape' && active.shape && active.shape.type === 'path' ? active : null) || state.layers.slice().reverse().find((l) => l && l.kind === 'shape' && l.shape && l.shape.type === 'path'); if (!pathLayer) throw new Error('no path layer'); _saveState('Text on a path'); const result = _attachTextToPath(textLayer, pathLayer); if (!result) throw new Error('path needs at least two points'); state.activeLayerId = textLayer.id; state.selectedLayerIds = [textLayer.id]; _renderLayerPanel(); composite(); return result; } },
          'text.detachFromPath': { description: 'Return a text layer that sits on a path to a normal straight baseline. {layerId?}', run: (p = {}) => { const textLayer = (p.layerId ? _controlFindLayer(p) : null) || (activeLayer() && activeLayer().kind === 'text' ? activeLayer() : null) || state.layers.slice().reverse().find((l) => l && l.kind === 'text'); if (!textLayer) throw new Error('no text layer'); if (!textLayer.text || !textLayer.text.path) throw new Error('text is not on a path'); _saveState('Detach text from path'); _detachTextFromPath(textLayer); _renderLayerPanel(); composite(); return { id: textLayer.id }; } },
          'text.layout': { description: 'Position/resize a title text layer RELATIVE to the image layer bounds (so it stays inside the photo). {id?,anchor:"top-center"|"center"|"bottom-center"|"custom",sizePct,marginPct,size,x,y}.', run: (p = {}) => { const layer = (p && p.id ? _controlFindLayer(p) : null) || state.layers.slice().reverse().find((l) => l && l.kind === 'text'); if (!layer) throw new Error('no text layer'); const ib = _imageLayerBounds(); const targetW = ib ? ib.w : state.imgWidth; const targetH = ib ? ib.h : state.imgHeight; const baseX = ib ? ib.x : 0; const baseY = ib ? ib.y : 0; _saveState('Place title'); if (p.sizePct) { const s = Math.max(8, Math.round(targetH * Math.min(0.2, Math.max(0.08, Number(p.sizePct))))); layer.text.size = s; _renderTextLayer(layer); } else if (Number.isFinite(Number(p.size)) && Number(p.size) > 0) { layer.text.size = Number(p.size); _renderTextLayer(layer); } const tb = _layerDocBounds(layer) || { x: 0, y: 0, w: 0, h: 0 }; let anchor = p.anchor || 'top-center'; const margin = Math.round(targetH * (Number.isFinite(Number(p.marginPct)) ? Number(p.marginPct) : 0.05)); if ((anchor === 'top-center') && state.wandMask && state.wandMask.width > 0) { try { const mw = state.wandMask.width; const mh = state.wandMask.height; const md = state.wandMask.getContext('2d').getImageData(0, 0, mw, mh).data; const subj = _maskAlphaBounds(md, mw, mh, 8); const titleTop = baseY + margin; if (subj && subj.h > 0 && titleTop + tb.h > subj.y) anchor = 'bottom-center'; } catch (_) {} } let nx; let ny; if (anchor === 'center') { nx = Math.round(baseX + (targetW - tb.w) / 2); ny = Math.round(baseY + (targetH - tb.h) / 2); } else if (anchor === 'bottom-center') { nx = Math.round(baseX + (targetW - tb.w) / 2); ny = Math.round(baseY + targetH - tb.h - margin); } else if (anchor === 'custom') { nx = Math.round(Number(p.x) || 0); ny = Math.round(Number(p.y) || 0); } else { nx = Math.round(baseX + (targetW - tb.w) / 2); ny = Math.round(baseY + margin); } state.layerOffsets.set(layer.id, { x: nx, y: ny }); state.activeLayerId = layer.id; state.selectedLayerIds = [layer.id]; _renderLayerPanel(); composite(); return { id: layer.id, x: nx, y: ny, size: layer.text ? layer.text.size : null }; } },
          'history.undo': { description: 'Undo one step.', run: () => { undo(); return true; } },
          'history.redo': { description: 'Redo one step.', run: () => { redo(); return true; } },
          'history.mark': { description: 'Return the current undo-stack length (use before a batch).', run: () => (state.undoStack ? state.undoStack.length : 0) },
          'history.collapse': { description: 'Collapse all undo entries added since a mark into one labelled step.', run: (p) => { const stack = state.undoStack || []; const mark = Math.max(0, Math.min(stack.length, Number(p.mark) || 0)); const added = stack.length - mark; if (added <= 0) return 0; const last = stack[stack.length - 1]; if (last && p.label) last._label = p.label; if (added > 1) stack.splice(mark, added - 1); try { _refreshHistoryPanelIfOpen(); } catch (_) {} return 1; } },
          'history.snapshot': { description: 'Create a named snapshot {label} of the current document state.', run: (p = {}) => _takeHistorySnapshot(p && p.label) },
          'history.snapshots': { description: 'List the named snapshots as {id,label,ts}.', run: () => _listHistorySnapshots() },
          'history.restoreSnapshot': { description: 'Restore a named snapshot {id}.', run: (p = {}) => { if (!p || !p.id) throw new Error('id required'); return _restoreHistorySnapshot(p.id); } },
          'history.deleteSnapshot': { description: 'Delete a named snapshot {id}.', run: (p = {}) => { if (!p || !p.id) throw new Error('id required'); return _deleteHistorySnapshot(p.id); } },
          'panels.list': { description: 'List every sidebar panel with its visibility.', run: () => panelVisibility.list() },
          'panels.setVisible': { description: 'Show or hide a sidebar panel {id,visible}.', run: (p = {}) => { if (!p.id) throw new Error('id required'); panelVisibility.setVisible(p.id, p.visible !== false); return panelVisibility.list(); } },
          'panels.toggle': { description: 'Toggle a sidebar panel {id}.', run: (p = {}) => { if (!p.id) throw new Error('id required'); panelVisibility.toggle(p.id); return panelVisibility.list(); } },
          'panels.showAll': { description: 'Show every sidebar panel.', run: () => { panelVisibility.showAll(); return panelVisibility.list(); } },
          'panels.hideAll': { description: 'Hide every sidebar panel.', run: () => { panelVisibility.hideAll(); return panelVisibility.list(); } },
          'panels.reset': { description: 'Reset panel visibility to the default layout.', run: () => { panelVisibility.reset(); return panelVisibility.list(); } },
          'patch.apply': { description: 'Patch the current selection: replace its content with the same-shaped source region shifted by {dx,dy} pixels, feathered at the seam (Photoshop Patch tool).', run: (p = {}) => _applyPatchOffset(Math.round(Number(p.dx) || 0), Math.round(Number(p.dy) || 0)) },
          'edit.contentAwareMove': { description: 'Content-Aware Move: move the current selection content by {dx,dy} pixels and diffusion-fill the hole it leaves behind.', run: (p = {}) => _applyContentAwareMove(Math.round(Number(p.dx) || 0), Math.round(Number(p.dy) || 0)) },
          'run_editor_script': { description: 'Run a validated batch of control ops in order, stopping at the first failure. {steps:[{op,params}]}. Each op is validated against the control catalog.', run: async (p) => { const steps = (p && Array.isArray(p.steps)) ? p.steps : []; if (!steps.length) throw new Error('steps required'); const results = []; for (const step of steps) { const op = step && step.op; if (!op || !control.describe(op)) throw new Error(`unknown editor op: ${op}`); const r = await control.invoke(op, step.params || {}); results.push({ op, ok: r.ok, result: r.result, error: r.error }); if (!r.ok) throw new Error(`${op}: ${r.error}`); } return results; } },
          'document.list': { description: 'List the documents open in this editor session (each with id/name/width/height/active).', run: () => { const entry = _activeDocEntry(); return { active: entry.id, documents: _openDocuments.map((d) => ({ id: d.id, name: d.name, width: d.width, height: d.height, active: d.id === entry.id })) }; } },
          'actions.batchDocuments': { description: 'Run a saved Action ({name}, or explicit {steps}) over every open document, optionally exporting each ({export:true}). Returns one result per document. Each document is isolated: a failure on one does not stop the rest.', run: async (p = {}) => { const name = String(p.name || '').trim(); let steps = Array.isArray(p.steps) ? p.steps.slice() : null; if (!steps) { try { steps = (JSON.parse(localStorage.getItem('ge-actions') || '{}')[name]) || null; } catch (_) { steps = null; } } if (!steps || !steps.length) throw new Error('unknown or empty action: ' + (name || '(none)')); const selectors = Object.fromEntries(ACTION_COMMANDS.map(([id, , sel]) => [id, sel])); const invoke = (op, params) => window.geEditorControl.invoke(op, params || {}); try { _syncActiveDocument(); } catch (_) {} return runBatchOverDocuments({ documents: _openDocuments.map((d) => ({ id: d.id, name: d.name })), runOne: async (doc) => { await invoke('document.switch', { id: doc.id }); for (const step of steps) { const sel = selectors[step]; if (!sel) continue; document.querySelector(sel)?.click(); await new Promise((r) => setTimeout(r, 60)); } if (p.export) { const r = await invoke('export.png', {}); return (r && r.result) ? r.result.dataUrl : null; } return null; } }); } },
          'document.duplicate': { description: 'Duplicate the current document and switch to the copy {name}.', run: async (p = {}) => { const cur = _syncActiveDocument(); if (!cur || !cur.doc) throw new Error('no document'); const copy = JSON.parse(JSON.stringify(cur.doc)); const id = 'doc-' + (++_activeDocumentSeq); const name = String(p.name || ((cur.name || 'Untitled') + ' copy')); _openDocuments.push({ id, name, width: cur.width, height: cur.height, doc: copy }); await _restoreDraft(copy); state.documentId = id; state.draftName = name; return { id, name }; } },
          'document.create': { description: 'Create and switch to a new blank document {width,height,name}.', run: async (p = {}) => { const w = Math.max(1, Math.round(Number(p.width) || 1024)); const h = Math.max(1, Math.round(Number(p.height) || 1024)); const name = String(p.name || 'Untitled'); _syncActiveDocument(); const id = 'doc-' + (++_activeDocumentSeq); _openDocuments.push({ id, name, width: w, height: h, doc: null }); await openEditor(null, null, { w, h }, name); state.documentId = id; return { id, name, width: w, height: h }; } },
          'document.switch': { description: 'Switch to an open document by id {id}.', run: async (p = {}) => { const id = String(p.id || ''); const entry = _openDocuments.find((d) => d.id === id); if (!entry) throw new Error('unknown document: ' + id); if (!entry.doc) throw new Error('document has no saved content'); _syncActiveDocument(); await _restoreDraft(entry.doc); state.documentId = id; state.draftName = entry.name; return { id: entry.id, name: entry.name }; } },
          'document.openPsd': { description: 'Open a PSD (base64 data URL) as a new document {dataUrl|base64,name}; restores PSD layers when present, else the flattened composite.', run: async (p = {}) => { const b64 = String(p.dataUrl || p.base64 || '').replace(/^data:[^,]*,/, ''); if (!b64) throw new Error('dataUrl required'); const bin = atob(b64); const bytes = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i); const name = String(p.name || 'Imported PSD'); let decoded = null; try { decoded = _decodeLayeredPsd(bytes.buffer); } catch (_) { decoded = null; } if (decoded && Array.isArray(decoded.layers) && decoded.layers.length) { const w = decoded.width, h = decoded.height; const records = decoded.layers.map((L, i) => { const c = document.createElement('canvas'); c.width = Math.max(1, L.width | 0); c.height = Math.max(1, L.height | 0); c.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(L.data), c.width, c.height), 0, 0); return { id: 'layer-' + (i + 1), name: String(L.name || ('Layer ' + (i + 1))), canvasW: c.width, canvasH: c.height, offset: { x: L.x | 0, y: L.y | 0 }, dataUrl: c.toDataURL('image/png'), visible: L.visible !== false, opacity: typeof L.opacity === 'number' ? L.opacity : 1 }; }); _syncActiveDocument(); const id = 'doc-' + (++_activeDocumentSeq); _openDocuments.push({ id, name, width: w, height: h, doc: null }); await _restoreDraft({ imgWidth: w, imgHeight: h, colorMode: 'rgb', bitDepth: 8, activeLayerId: records[records.length - 1].id, layers: records, groups: [], savedSelections: [], view: {} }); state.documentId = id; state.draftName = name; _syncActiveDocument(); return { id, name, width: w, height: h, layerCount: records.length, layerNames: records.map((r) => r.name) }; } const parsed = _parsePsd(bytes.buffer); if (!parsed || !parsed.canvas) throw new Error('could not parse PSD'); const w = parsed.canvas.width, h = parsed.canvas.height; _syncActiveDocument(); const id = 'doc-' + (++_activeDocumentSeq); _openDocuments.push({ id, name, width: w, height: h, doc: null }); await openEditor(parsed.canvas.toDataURL('image/png'), null, { w, h }, name); state.documentId = id; return { id, name, width: w, height: h }; } },
          'export.png': { description: 'Return the flattened document as a PNG data URL.', run: () => ({ dataUrl: exportPNG() }) },
          'export.gallery': { description: 'Save a copy of the document to the gallery.', run: async () => { await exportToGallery(); return true; } },
          'screenshot': { description: 'Return the current canvas as a PNG data URL (waits for any in-flight async composite so the frame is painted).', run: async () => {
            const deadline = Date.now() + 1500;
            while (!state.documentRenderReady && Date.now() < deadline) {
              await new Promise((resolve) => setTimeout(resolve, 16));
            }
            await new Promise((resolve) => (typeof requestAnimationFrame === 'function' ? requestAnimationFrame(() => resolve()) : resolve()));
            return { dataUrl: state.mainCanvas ? state.mainCanvas.toDataURL('image/png') : null, width: state.imgWidth, height: state.imgHeight };
          } },
        },
      });
      if (typeof window !== 'undefined') {
        window.geEditorControl = control;
        try {
          window.geEditorAI = _createNaturalLanguageEditor({ control });
        } catch (e) { console.error('[ge-editor] NL editor init failed', e); }
      }
      return control;
    },
  };
  _controlState.installEditorControl();
  // ── Multi-document tabs, rendered in the GALLERY tab bar ──────────────────
  // The active document is the primary #gallery-editor-tab; every other open
  // document renders as an extra "Edit: <name>" tab beside it (plus a "+" to
  // spawn a new one). Switching/closing drives the document.* control ops.
  const _renderEditorDocTabs = () => {
    try {
      if (!state.editorOpen) return;
      _syncActiveDocument();
      const activeId = state.documentId;
      const activeDoc = _openDocuments.find((d) => d.id === activeId);
      const primary = document.getElementById('gallery-editor-tab');
      if (primary) {
        const lbl = primary.querySelector('.gallery-tab-label');
        const nm = (activeDoc && activeDoc.name) || state.draftName || 'Untitled';
        if (lbl) lbl.textContent = 'Edit: ' + (nm.length > 24 ? nm.slice(0, 22) + '…' : nm);
        primary.dataset.docId = activeId || '';
      }
      const extra = document.getElementById('gallery-editor-tabs-extra');
      if (!extra) return;
      extra.innerHTML = '';
      const makeTab = (doc) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'gallery-tab';
        b.dataset.tab = 'editor';
        b.dataset.docId = doc.id;
        b.innerHTML = '<span class="gallery-tab-label"></span><span class="gallery-tab-close" title="Close tab" aria-label="Close tab">×</span>';
        b.querySelector('.gallery-tab-label').textContent = 'Edit: ' + (String(doc.name || 'Untitled').slice(0, 24));
        b.addEventListener('click', (e) => {
          if (e.target.closest('.gallery-tab-close')) return;
          window.geEditorControl?.invoke('document.switch', { id: doc.id });
        });
        b.querySelector('.gallery-tab-close').addEventListener('click', (e) => {
          e.stopPropagation();
          _closeEditorDoc(doc.id);
        });
        return b;
      };
      for (const d of _openDocuments) {
        if (d.id === activeId) continue;
        extra.appendChild(makeTab(d));
      }
      const plus = document.createElement('button');
      plus.type = 'button';
      plus.className = 'gallery-tab gallery-tab-new';
      plus.title = 'New edit tab';
      plus.setAttribute('aria-label', 'New edit tab');
      plus.textContent = '+';
      // Ask for the canvas size with the same dialog as File ▸ New Image, so a
      // new tab never silently replaces the current document's resolution.
      plus.addEventListener('click', async () => {
        const size = await _promptCanvasSize({
          title: 'New edit',
          okLabel: 'Create',
          initialW: 1920,
          initialH: 1080,
          showUnits: true,
          showPresets: true,
        });
        if (!size || !(size.w > 0) || !(size.h > 0)) return;
        window.geEditorControl?.invoke('document.create', { width: size.w, height: size.h, name: 'Untitled' });
      });
      extra.appendChild(plus);
    } catch (_) {}
  };
  const _closeEditorDoc = (id) => {
    try {
      const idx = _openDocuments.findIndex((d) => d.id === id);
      if (idx < 0) return;
      _openDocuments.splice(idx, 1);
      if (!_openDocuments.length) {
        window.__galleryAllowCloseEditor = true;
        try { window.geGalleryCloseEditor?.(); } catch (_) {}
        window.__galleryAllowCloseEditor = false;
        return;
      }
      if (state.documentId === id) {
        const next = _openDocuments[Math.max(0, idx - 1)];
        if (next) {
          state.documentId = next.id;
          _suppressDocSync = true;
          Promise.resolve(window.geEditorControl?.invoke('document.switch', { id: next.id }))
            .finally(() => { _suppressDocSync = false; });
        }
      } else {
        _renderEditorDocTabs();
      }
    } catch (_) {}
  };
  if (typeof window !== 'undefined') {
    window.geEditorDocTabs = _renderEditorDocTabs;
    // The primary Edit tab's × first tries to close just the active document
    // when several are open; the gallery keeps its close-the-editor behaviour
    // for the last one.
    window.geEditorCloseActiveDocument = () => {
      if (_openDocuments.length <= 1) return false;
      _closeEditorDoc(state.documentId);
      return true;
    };
    if (window.geEditorControl && !window.geEditorControl.__docTabsPatched) {
      const _origInvoke = window.geEditorControl.invoke.bind(window.geEditorControl);
      window.geEditorControl.invoke = async (op, params) => {
        const r = await _origInvoke(op, params);
        if (typeof op === 'string' && op.indexOf('document.') === 0) { try { _renderEditorDocTabs(); } catch (_) {} }
        return r;
      };
      window.geEditorControl.__docTabsPatched = true;
    }
  }
  // Subscribe to the agent command bridge so in-app agents can drive this open
  // editor (GET bridge/events -> run op -> POST bridge/result).
  if (typeof window !== 'undefined') {
    try { window.__geEditorBridge && window.__geEditorBridge.stop(); } catch (_) {}
    const agentBridgeSession = window.geEditorBridgeSession || String(state.editorSessionToken || 'default');
    try {
      // Scoped photo_editor agent path: the Quick Edit popup posts
      // {prompt,session,state,screenshot} to the HARNESS endpoint; its
      // run_editor_command / ai_edit_image / generate_image tools execute here
      // through the bridge below, streaming each step back into the popup.
      window.geEditorAgent = _createEditorAgent({
        control: window.geEditorControl,
        endpoint: window.geEditorAgentEndpoint || '/api/editor/agent',
        getSession: () => agentBridgeSession,
        getModel: () => _getSelectedQuickEditModel(),
        getAgentModel: () => _getSelectedQuickEditAgentModel(),
      });
    } catch (e) { console.error('[ge-editor] agent init failed', e); }
    try {
      window.__geEditorBridge = _startEditorBridge({
        control: window.geEditorControl,
        session: agentBridgeSession,
        onCommand: (payload, cmd) => {
          try { window.geEditorAgent && window.geEditorAgent.reportStep(payload, cmd); } catch (_) {}
        },
      });
    } catch (e) { console.error('[ge-editor] bridge init failed', e); }
  }
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

// Side-panel refresh hook. Set inside openEditor (after createColorPanels);
// _renderLayerPanel is module-level but the panels live per-editor-instance,
// so we stash the refresh callback here. Refreshes are coalesced to one per
// frame and skipped while the rail is hidden so drags stay smooth.
let _refreshSidePanels = null;
let _sidePanelsRaf = 0;
function _scheduleSidePanelRefresh() {
  if (!_refreshSidePanels || _sidePanelsRaf) return;
  const rail = state.container?.querySelector?.('.ge-right-panel') || document.querySelector('.ge-right-panel');
  if (rail && rail.offsetWidth === 0) return;
  _sidePanelsRaf = requestAnimationFrame(() => {
    _sidePanelsRaf = 0;
    try { _refreshSidePanels?.(); } catch (_) { /* panel not ready */ }
  });
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
  // Keep the dock side panels (histogram/info/navigator/channels) in sync with
  // the active layer after every edit — they are otherwise only drawn once at
  // build time and would stay blank.
  _scheduleSidePanelRefresh();
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

// True when a layer's canvas is a single flat colour (e.g. the white blank-canvas
// Background fill or an empty transparent layer) — i.e. padding, not content.
function _layerIsUniform(canvas) {
  const w = canvas.width;
  const h = canvas.height;
  if (!w || !h) return true;
  const d = canvas.getContext('2d').getImageData(0, 0, w, h).data;
  const r0 = d[0], g0 = d[1], b0 = d[2], a0 = d[3];
  const step = Math.max(1, Math.floor(Math.min(w, h) / 32));
  for (let y = 0; y < h; y += step) {
    for (let x = 0; x < w; x += step) {
      const i = (y * w + x) * 4;
      if (d[i] !== r0 || d[i + 1] !== g0 || d[i + 2] !== b0 || d[i + 3] !== a0) return false;
    }
  }
  return true;
}

// RGB lookup for the deterministic fill fast path.
const _NAMED_RGB = {
  red: [255, 0, 0], blue: [0, 0, 255], green: [0, 160, 0], yellow: [255, 255, 0],
  orange: [255, 128, 0], purple: [128, 0, 255], pink: [255, 105, 180], black: [0, 0, 0],
  white: [255, 255, 255], grey: [128, 128, 128], gray: [128, 128, 128], cyan: [0, 255, 255],
  magenta: [255, 0, 255], brown: [139, 69, 19], teal: [0, 128, 128],
};
function _namedRgb(name) {
  const k = String(name || '').toLowerCase();
  return _NAMED_RGB[k] || null;
}
// First sampled pixel of `layer` closest to `rgb`, on a coarse grid so a fill
// can anchor inside a colour region (e.g. the white area) without a full scan.
function _findColorPoint(layer, rgb, tol = 60) {
  try {
    const w = layer.canvas.width, h = layer.canvas.height;
    const data = layer.ctx.getImageData(0, 0, w, h).data;
    const step = Math.max(1, Math.round(Math.min(w, h) / 120));
    let best = null, bestD = Infinity;
    for (let y = (step >> 1); y < h; y += step) {
      for (let x = (step >> 1); x < w; x += step) {
        const i = (y * w + x) * 4;
        if (data[i + 3] < 32) continue;
        const d = Math.abs(data[i] - rgb[0]) + Math.abs(data[i + 1] - rgb[1]) + Math.abs(data[i + 2] - rgb[2]);
        if (d < bestD) { bestD = d; best = { x, y }; if (d <= tol) return best; }
      }
    }
    return bestD <= tol * 3 ? best : null;
  } catch (_) {
    return null;
  }
}

// Bounding box (document space) of the real image content — the union of all
// visible, non-uniform layers. Uniform (white/empty) padding is ignored so
// Generative Expand extends the photo, not the blank-canvas margins.
function _contentBounds() {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, found = false;
  for (const l of state.layers) {
    if (!l.visible || !l.canvas) continue;
    if (_layerIsUniform(l.canvas)) continue;
    const off = state.layerOffsets.get(l.id) || { x: 0, y: 0 };
    x0 = Math.min(x0, off.x);
    y0 = Math.min(y0, off.y);
    x1 = Math.max(x1, off.x + l.canvas.width);
    y1 = Math.max(y1, off.y + l.canvas.height);
    found = true;
  }
  if (!found) return null;
  x0 = Math.max(0, Math.floor(x0));
  y0 = Math.max(0, Math.floor(y0));
  x1 = Math.min(state.imgWidth, Math.ceil(x1));
  y1 = Math.min(state.imgHeight, Math.ceil(y1));
  if (x1 - x0 < 2 || y1 - y0 < 2) return null;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

// Average colour of a thin band just INSIDE the content bbox, used to tint a
// generated expand border toward the original edge so the seam is less visible.
function _edgeBandAverage(canvas, b) {
  try {
    const ctx = canvas.getContext('2d');
    const t = 2;
    const x = Math.max(0, b.x);
    const y = Math.max(0, b.y);
    const w = Math.max(1, Math.min(canvas.width - x, b.w));
    const h = Math.max(1, Math.min(canvas.height - y, b.h));
    const rects = [
      [x, y, w, Math.min(t, h)],
      [x, y + h - Math.min(t, h), w, Math.min(t, h)],
      [x, y, Math.min(t, w), h],
      [x + w - Math.min(t, w), y, Math.min(t, w), h],
    ];
    let r = 0, g = 0, bl = 0, n = 0;
    for (const [rx, ry, rw, rh] of rects) {
      if (rw < 1 || rh < 1) continue;
      const d = ctx.getImageData(rx, ry, rw, rh).data;
      for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; bl += d[i + 2]; n++; }
    }
    if (!n) return null;
    return [Math.round(r / n), Math.round(g / n), Math.round(bl / n)];
  } catch (_) {
    return null;
  }
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

// One-click PNG export (no format dialog) — the explicit "Save as PNG" option.
export async function exportPNGFile() {
  const source = flatten();
  const name = _defaultExportName();
  try {
    const { blob, extension } = await _encodeExportCanvas(source, { filename: name, format: 'png', transparency: true });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name}.${extension}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    if (uiModule) uiModule.showToast(`Saved PNG (${(blob.size / 1024 / 1024).toFixed(1)} MB)`);
  } catch (error) {
    console.error('[image-export] png error:', error);
    if (uiModule) uiModule.showToast('PNG export failed: ' + (error?.message || 'unknown error'), 5000);
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
    // Photoshop-style floating dialog: drag by the header. Re-centered each
    // open (reset clears any position from a previous drag).
    {
      const dialogContent = overlay.querySelector('.modal-content');
      const dialogHeader = overlay.querySelector('.modal-header');
      if (dialogContent && dialogHeader) {
        _resetDraggable(dialogContent);
        _makeDraggable(dialogContent, dialogHeader);
      }
    }
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
    const previewWrap = overlay.querySelector('.ge-canvas-preview');
    const previewImg = document.getElementById('ge-canvas-prompt-preview');
    const resolutionField = overlay.querySelector('.ge-canvas-resolution-field');
    const resolutionInput = document.getElementById('ge-canvas-prompt-resolution');
    if (previewWrap) previewWrap.hidden = !opts.showPreview;
    if (opts.showPreview && previewImg && state.mainCanvas) {
      try { previewImg.src = state.mainCanvas.toDataURL('image/png'); } catch (_) { previewWrap.hidden = true; }
    }
    if (resolutionField) resolutionField.hidden = !opts.showResolution;
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
        resolution: Number(resolutionInput?.value) || 72,
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
  state.documentRevision = 0;
  state.persistingRevision = 0;
  state.savedRevision = 0;
  state.persistRetryCount = 0;
  if (state.persistRetryTimer) { clearTimeout(state.persistRetryTimer); state.persistRetryTimer = null; }
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
  if (state.persistRetryTimer) { clearTimeout(state.persistRetryTimer); state.persistRetryTimer = null; }
  if (state.pendingStatusTimer) { clearTimeout(state.pendingStatusTimer); state.pendingStatusTimer = null; }
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
    // Keep the status-bar document size in sync the moment the canvas is
    // sized (the initial label pass runs before dimensions are known).
    const sizeLabel = document.getElementById('ge-canvas-size');
    if (sizeLabel) sizeLabel.textContent = `${w}×${h} px`;
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
          if (sizeLabel) sizeLabel.textContent = `${state.imgWidth}×${state.imgHeight} px`;
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
      if (sizeLabel) sizeLabel.textContent = `${state.imgWidth}×${state.imgHeight} px`;
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
  // Keep the in-editor document tab in sync with the document name.
  const docLabel = document.querySelector('.ge-doc-tab-label');
  if (docLabel) docLabel.textContent = name || state.draftName || 'Untitled';
  const tab = document.getElementById('gallery-editor-tab');
  if (tab) {
    const labelEl = tab.querySelector('.gallery-tab-label') || tab;
    if (!name) {
      labelEl.textContent = 'Edit';
      tab.classList.remove('has-edit');
    } else {
      const trimmed = name.length > 24 ? name.slice(0, 22) + '…' : name;
      labelEl.textContent = `Edit: ${trimmed}`;
      tab.classList.add('has-edit');
    }
  }
  // Re-render the multi-document tab strip (extra tabs + the "+" spawner).
  try { window.geEditorDocTabs?.(); } catch (_) {}
}

export function closeEditor(options = {}) {
  try { document.body.classList.remove('ge-editor-open'); } catch (_) {}
  _penTool.cancel();
  try { window.__geEditorBridge && window.__geEditorBridge.stop(); } catch (_) {}
  window.__geEditorBridge = null;
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
  if (state.persistRetryTimer) { clearTimeout(state.persistRetryTimer); state.persistRetryTimer = null; }
  if (state.pendingStatusTimer) { clearTimeout(state.pendingStatusTimer); state.pendingStatusTimer = null; }
  if (state.layers.length) {
    try { _persistDraft(); } catch {}
  }
  // Invalidate asynchronous encoding/network callbacks before tearing down
  // the document, so a late save cannot mutate the next editor session.
  state.editorSessionToken += 1;
  _setEditTabLabel(null);
  _clearActiveEditorSession();
  _unmountEditorLoading();
  // The maximize control lives in the modal header while the editor is open;
  // drop it on close so the header keeps only the window's own buttons.
  document.querySelectorAll('.ge-header-max-btn').forEach((btn) => btn.remove());
  state.editorOpen = false;
  // Tear down the gallery multi-document tab strip.
  try {
    const _extraTabs = document.getElementById('gallery-editor-tabs-extra');
    if (_extraTabs) _extraTabs.innerHTML = '';
    _openDocuments.length = 0;
  } catch (_) {}
  _renderSavedSelectionsMenu = null;
  _stopSelectionAnimation();
  if (_selectionNudgeTimer) clearTimeout(_selectionNudgeTimer);
  _selectionNudgeTimer = null;
  _selectionNudgeHistorySaved = false;
  _precisionGuides = null;
  try { _preflight?.destroy(); } catch {}
  _preflight = null;
  try { _aiSettings?.destroy(); } catch {}
  _aiSettings = null;
  try { delete window.geEditorAiSettings; } catch (_) { window.geEditorAiSettings = null; }
  // Drop every document-level click-away handler registered by this
  // openEditor invocation. Without this, dropdown closers accumulated
  // across reopens (six handlers × N opens).
  while (state.editorDocClickHandlers.length) {
    const h = state.editorDocClickHandlers.pop();
    try { document.removeEventListener('click', h); } catch {}
  }
  if (_cursorRaf) { cancelAnimationFrame(_cursorRaf); _cursorRaf = null; }
  _pendingCursorEvent = null;
  _lastCursorRect = null;
  state._lastCursorDiameterX = null;
  state._lastCursorDiameterY = null;
  state._lastCursorBorderColor = null;
  if (state.cursorEl) {
    if (state.cursorEl._positionAnimation) {
      try { state.cursorEl._positionAnimation.cancel(); } catch {}
      state.cursorEl._positionAnimation = null;
    }
    state.cursorEl.remove();
    state.cursorEl = null;
  }
  try { document.getElementById('ge-cursor-layer')?.remove(); } catch {}
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
  state.penOverlay = null;
  state.penOverlayCtx = null;
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
  _refreshSidePanels = null;
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

// Module-level handle so the gallery/MCP layer can open and close the
// editor without importing the ES module. Once a document is open, the
// richer per-document command dispatcher is available at
// window.geEditorControl.
// Chat/MCP entrypoint: open the editor for a photo and immediately run a
// natural-language edit through the scoped photo_editor agent (bridge-driven,
// one undo group). Used by the chat "Open in editor & apply" button, which
// passes the attached image plus the agent's suggested prompt.
async function gePhotoEditorApply(opts = {}) {
  const {
    imageUrl = null, imageId = null, prompt = '', displayName = null,
    draftId = null, presetSize = null, onStep = null, timeout = 25000,
  } = opts || {};
  if (!prompt || !String(prompt).trim()) throw new Error('prompt required');
  const waitFor = (pred, ms, label) => new Promise((resolve, reject) => {
    const start = Date.now();
    (function tick() {
      let ok = false; try { ok = !!pred(); } catch (_) {}
      if (ok) return resolve(true);
      if (Date.now() - start > ms) return reject(new Error('timed out waiting for ' + label));
      setTimeout(tick, 60);
    })();
  });
  // Ensure the gallery modal (which hosts #gallery-editor-container) exists and
  // is open, then mount the editor into it.
  if (!document.getElementById('gallery-editor-container')) {
    try { document.getElementById('tool-gallery-btn')?.click(); } catch (_) {}
    try { await waitFor(() => document.getElementById('gallery-editor-container'), 6000, 'gallery modal'); } catch (_) {}
  }
  openEditor(imageUrl, imageId,
    presetSize || undefined, displayName || undefined, draftId || undefined);
  await waitFor(() => window.geEditorControl && window.geEditorAgent && window.__geEditorBridge, timeout, 'editor bridge');
  if (imageUrl || imageId) {
    await waitFor(() => Array.isArray(state.layers)
      && state.layers.some((l) => l && l.kind !== 'adjustment' && l.canvas && l.canvas.width > 0), timeout, 'image load');
  }
  await new Promise((resolve) => setTimeout(resolve, 200)); // let the SSE bridge connect
  const out = await window.geEditorAgent.run(prompt, { onStep });
  return out;
}

if (typeof window !== 'undefined') {
  window.gePhotoEditorApply = gePhotoEditorApply;
  _installControlKit();
  window.geGalleryEditor = {
    openEditor,
    closeEditor,
    isEditorOpen,
    exportPNG,
    exportToGallery,
    downloadPNG,
    openInEditorAndApply: gePhotoEditorApply,
  };
}
