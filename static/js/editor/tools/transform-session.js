/**
 * Transform-tool session lifecycle + floating popup wiring.
 *
 *   _startTransform        snapshot the active layer + open popup
 *   _openTransformPopup    build the W/H/rotation popup, wire inputs
 *   _wireTransformDrag     header drag, mobile + desktop position handling
 *   _reapplyTransform      live preview re-render from the snapshot
 *   _confirmTransform      commit + clear session state
 *   _cancelTransform       restore via undo() + clear session state
 *
 * Handle-drag interactions on the CANVAS (corner / rotation grip) live
 * in `editor/tools/transform-drag.js` — those mutate the same staged
 * `state.transformPending*` fields that the popup inputs do, so both
 * surfaces stay in sync via `_reapplyTransform()`.
 *
 * @param {{
 *   activeLayer:           () => object | null,
 *   saveState:             (label?: string) => void,
 *   composite:             () => void,
 *   fitZoom:               () => void,
 *   drawTransformHandles:  () => void,
 *   showCanvasLoading:     (label: string) => void,
 *   hideCanvasLoading:     () => void,
 *   undo:                  () => void,
 *   uiModule:              object | null,
 *   syncGeometryControls?: () => void,
 *   getDocumentSelection?: () => HTMLCanvasElement | null,
 *   syncSelectionUi?:      () => void,
 *   schedulePersist?:      () => void,
 * }} deps
 *
 * @returns {{
 *   startTransform, openTransformPopup, closeTransformPopup,
 *   reapplyTransform, nudgeTransform, confirmTransform, cancelTransform,
 * }}
 */
import { state } from '../state.js';
import { normalizeTextData, renderTextLayer } from '../text-layer.js';
import { normalizeShapeData, renderShapeLayer } from '../shape-layer.js';
import { isLayerPositionLocked } from '../layer-groups.js';
import { selectedLayers, selectionLabel } from '../layer-selection.js';
import {
  planLayerTransform,
  selectionBounds,
  transformedSelectionBounds,
} from '../multi-transform.js';
import {
  EDITOR_MAX_DIMENSION,
  EDITOR_MAX_SURFACE_PIXELS,
} from '../document-codec.js';
import {
  transformPopupHTML,
  attachSpinRepeat,
} from '../build/transform-popup.js';
import {
  selectionMaskBounds,
  transformSelectionMask,
} from '../selection-mask.js';
import {
  clonePlacedData,
  createPlacedData,
  frameTransformMatrix,
  renderPlacedLayer,
  transformPlacedData,
} from '../placed-layer.js';

export function createTransformSession({
  activeLayer, saveState, composite, fitZoom, drawTransformHandles,
  showCanvasLoading, hideCanvasLoading, undo, uiModule,
  syncGeometryControls, getDocumentSelection, syncSelectionUi, schedulePersist,
}) {
  let lastValidLayerTarget = null;
  let lastTransformLimitMessage = '';

  const formatTransformValue = value => {
    const number = Number(value) || 0;
    return Number.isInteger(number) ? String(number) : String(Number(number.toFixed(2)));
  };

  function syncTransformPopupValues(force = false) {
    const pop = state.transformPopup;
    if (!pop || !state.transformCenter) return;
    const values = {
      '#ge-transform-x': state.transformCenter.x,
      '#ge-transform-y': state.transformCenter.y,
      '#ge-transform-w': state.transformPendingFlipH ? -state.transformPendingW : state.transformPendingW,
      '#ge-transform-h': state.transformPendingFlipV ? -state.transformPendingH : state.transformPendingH,
      '#ge-transform-rot': state.transformPendingRot,
    };
    for (const [selector, value] of Object.entries(values)) {
      const input = pop.querySelector(selector);
      if (input && (force || document.activeElement !== input)) input.value = formatTransformValue(value);
    }
  }

  function copyCanvas(source) {
    const canvas = document.createElement('canvas');
    canvas.width = source.width;
    canvas.height = source.height;
    canvas.getContext('2d').drawImage(source, 0, 0);
    return canvas;
  }

  function startTransform() {
    const layer = activeLayer();
    const layers = selectedLayers(state);
    if (!layer || !layers.length) { uiModule.showToast('Select an unlocked layer'); return; }
    if (layers.some(item => isLayerPositionLocked(state, item))) {
      uiModule.showToast(layers.length > 1 ? 'Unlock the position of every selected layer before transforming' : 'Unlock the layer position before transforming');
      return;
    }
    if (state.transformActive) { cancelTransform(); return; } // toggle off
    const bounds = selectionBounds(state, layers);
    state.transformActive = true;
    state.transformTarget = 'layers';
    state.transformLayer = layer;
    state.transformLayers = [...layers];
    state.transformItems = layers.map(item => ({
      layer: item,
      kind: item.kind || 'raster',
      canvas: copyCanvas(item.canvas),
      width: item.canvas.width,
      height: item.canvas.height,
      offset: { ...(state.layerOffsets.get(item.id) || { x: 0, y: 0 }) },
      wasPlaced: item.kind === 'placed',
      placed: item.kind === 'placed'
        ? clonePlacedData(item.placed)
        : (item.kind === 'raster' ? createPlacedData(item.canvas, [1, 0, 0, 1, 0, 0], item.name || 'Raster layer') : null),
      masks: (item.masks || [])
        .filter(mask => (mask.space || 'layer') === 'layer' && mask.canvas)
        .map(mask => {
          const maskOffset = {
            x: Number(mask.offset?.x) || 0,
            y: Number(mask.offset?.y) || 0,
          };
          const layerOffset = state.layerOffsets.get(item.id) || { x: 0, y: 0 };
          return {
            mask,
            canvas: copyCanvas(mask.canvas),
            linked: mask.linked !== false,
            offset: maskOffset,
            documentOffset: {
              x: layerOffset.x + maskOffset.x,
              y: layerOffset.y + maskOffset.y,
            },
          };
        }),
    }));
    const activeSnapshot = state.transformItems.find(item => item.layer.id === layer.id);
    state.transformOrigW = bounds.width;
    state.transformOrigH = bounds.height;
    state.transformSessionStartW = bounds.width;
    state.transformSessionStartH = bounds.height;
    state.transformPendingW = state.transformOrigW;
    state.transformPendingH = state.transformOrigH;
    state.transformPendingRot = 0;
    state.transformPendingFlipH = false;
    state.transformPendingFlipV = false;
    // Snapshot the layer so live preview can re-derive from the
    // original pixels on every keystroke instead of stacking
    // destructive edits.
    state.transformOrigCanvas = activeSnapshot?.canvas || null;
    state.transformOrigMasks = activeSnapshot?.masks || [];
    state.transformOrigOffset = { ...(activeSnapshot?.offset || { x: 0, y: 0 }) };
    state.transformSelectionBounds = { ...bounds };
    state.transformBounds = { ...bounds };
    state.transformCenter = { x: bounds.centerX, y: bounds.centerY };
    state.transformStartCenter = { ...state.transformCenter };
    lastValidLayerTarget = currentTransformTarget();
    lastTransformLimitMessage = '';
    saveState(`Transform ${selectionLabel(state, `"${layer.name || 'layer'}"`)}`);
    // Fit canvas to viewport so the corner handles are visible —
    // without this, a layer larger than the viewport leaves the grab
    // markers off-screen.
    try { fitZoom(); } catch {}
    composite();
    drawTransformHandles();
    openTransformPopup();
    syncGeometryControls?.();
  }

  function startSelectionTransform() {
    if (state.transformActive) cancelTransform();
    const selection = getDocumentSelection?.();
    const bounds = selectionMaskBounds(selection);
    if (!selection || !bounds) {
      uiModule?.showToast('Make a selection before transforming it');
      return false;
    }
    const frame = {
      ...bounds,
      centerX: bounds.x + bounds.width / 2,
      centerY: bounds.y + bounds.height / 2,
    };
    state.transformActive = true;
    state.transformTarget = 'selection';
    state.transformLayer = null;
    state.transformLayers = [];
    state.transformItems = [];
    state.transformSelectionCanvas = copyCanvas(selection);
    state.transformSelectionBounds = { ...frame };
    state.transformBounds = { ...frame };
    state.transformCenter = { x: frame.centerX, y: frame.centerY };
    state.transformStartCenter = { ...state.transformCenter };
    state.transformOrigW = frame.width;
    state.transformOrigH = frame.height;
    state.transformSessionStartW = frame.width;
    state.transformSessionStartH = frame.height;
    state.transformPendingW = frame.width;
    state.transformPendingH = frame.height;
    state.transformPendingRot = 0;
    state.transformPendingFlipH = false;
    state.transformPendingFlipV = false;
    saveState('Transform selection');
    try { fitZoom(); } catch {}
    composite();
    drawTransformHandles();
    openTransformPopup();
    syncSelectionUi?.();
    return true;
  }

  function closeTransformPopup() {
    if (state.transformPopup) {
      try { state.transformPopup.remove(); } catch {}
      state.transformPopup = null;
    }
  }

  // Floating Transform popup — horizontal layout, draggable via its
  // header, anchored over the right panel (layers area) by default
  // so it doesn't cover the canvas. Lets the user type exact W/H/Rot
  // and flip via negative values.
  function openTransformPopup() {
    closeTransformPopup();
    if (!state.container) return;
    // Global tours and toasts sit above tool popups and can cover the compact
    // mobile header or Apply/Cancel controls. Starting a new edit is a stronger
    // signal than those transient messages, so clear them first.
    document.querySelector('.tour-hint .tour-hint-dismiss')?.click();
    document.querySelector('#toast.show .toast-close-btn')?.click();
    const pop = document.createElement('div');
    pop.className = 'ge-transform-popup';
    pop.innerHTML = transformPopupHTML();
    const title = pop.querySelector('.ge-adj-title');
    if (title && state.transformTarget === 'selection') title.textContent = 'Transform Selection';
    else if (title && state.transformLayers.length > 1) title.textContent = `Transform ${state.transformLayers.length} layers`;
    state.container.appendChild(pop);
    state.transformPopup = pop;
    wireTransformDrag(pop);
    const wInput = pop.querySelector('#ge-transform-w');
    const hInput = pop.querySelector('#ge-transform-h');
    const rotInput = pop.querySelector('#ge-transform-rot');
    const xInput = pop.querySelector('#ge-transform-x');
    const yInput = pop.querySelector('#ge-transform-y');
    const aspectBtn = pop.querySelector('#ge-transform-aspect');
    const preserveSource = pop.querySelector('#ge-transform-preserve-source');
    const preserveField = pop.querySelector('.ge-transform-preserve-field');
    const canPreserveSource = state.transformTarget !== 'selection'
      && state.transformItems.some(item => item.layer?.kind === 'raster' || item.wasPlaced);
    if (preserveField) preserveField.hidden = !canPreserveSource;
    if (preserveSource) {
      preserveSource.checked = state.transformPreserveSource !== false;
      preserveSource.addEventListener('change', () => {
        state.transformPreserveSource = preserveSource.checked;
        reapplyTransform();
      });
    }
    syncTransformPopupValues();
    aspectBtn.classList.toggle('active', state.transformAspectLock);
    aspectBtn.setAttribute('aria-pressed', state.transformAspectLock ? 'true' : 'false');

    // Aspect-lock follower model: while the lock is engaged, ONE
    // field is the "driver" and the other is read-only + dimmed.
    // Driver = whichever field the user last typed in. Toggling the
    // chain releases the follower.
    let driver = null;
    const applyAspectVisuals = () => {
      if (!state.transformAspectLock || !driver) {
        wInput.readOnly = false;
        hInput.readOnly = false;
        wInput.classList.remove('ge-transform-input-locked');
        hInput.classList.remove('ge-transform-input-locked');
        return;
      }
      const followerW = driver === 'h';
      const followerH = driver === 'w';
      wInput.readOnly = followerW;
      hInput.readOnly = followerH;
      wInput.classList.toggle('ge-transform-input-locked', followerW);
      hInput.classList.toggle('ge-transform-input-locked', followerH);
    };
    const refresh = () => {
      let w = parseInt(wInput.value, 10);
      let h = parseInt(hInput.value, 10);
      const rot = parseInt(rotInput.value, 10) || 0;
      const x = Number.parseFloat(xInput.value);
      const y = Number.parseFloat(yInput.value);
      state.transformPendingFlipH = w < 0;
      state.transformPendingFlipV = h < 0;
      w = Math.abs(w || state.transformOrigW);
      h = Math.abs(h || state.transformOrigH);
      state.transformPendingW = Math.max(1, w);
      state.transformPendingH = Math.max(1, h);
      state.transformPendingRot = rot;
      if (Number.isFinite(x) && Number.isFinite(y)) state.transformCenter = { x, y };
      reapplyTransform();
    };
    xInput.addEventListener('input', refresh);
    yInput.addEventListener('input', refresh);
    wInput.addEventListener('input', () => {
      if (state.transformAspectLock) {
        driver = 'w';
        const w = parseInt(wInput.value, 10);
        if (!Number.isNaN(w) && state.transformOrigW > 0) {
          const sign = (parseInt(hInput.value, 10) || 1) < 0 ? -1 : 1;
          const newH = Math.round((Math.abs(w) / state.transformOrigW) * state.transformOrigH) * sign;
          hInput.value = String(newH);
        }
        applyAspectVisuals();
      }
      refresh();
    });
    hInput.addEventListener('input', () => {
      if (state.transformAspectLock) {
        driver = 'h';
        const h = parseInt(hInput.value, 10);
        if (!Number.isNaN(h) && state.transformOrigH > 0) {
          const sign = (parseInt(wInput.value, 10) || 1) < 0 ? -1 : 1;
          const newW = Math.round((Math.abs(h) / state.transformOrigH) * state.transformOrigW) * sign;
          wInput.value = String(newW);
        }
        applyAspectVisuals();
      }
      refresh();
    });
    rotInput.addEventListener('input', refresh);
    aspectBtn.addEventListener('click', () => {
      state.transformAspectLock = !state.transformAspectLock;
      aspectBtn.classList.toggle('active', state.transformAspectLock);
      aspectBtn.setAttribute('aria-pressed', state.transformAspectLock ? 'true' : 'false');
      // Reset follower the moment the user breaks the lock so both
      // fields go editable; re-engaging means "next type sets the driver".
      driver = null;
      applyAspectVisuals();
    });
    pop.querySelector('#ge-transform-apply').addEventListener('click', () => confirmTransform());
    pop.querySelector('#ge-transform-cancel').addEventListener('click', () => cancelTransform());
    pop.querySelector('#ge-transform-cancel-btn')?.addEventListener('click', () => cancelTransform());
    // Minimise — collapses the body so only the header is visible.
    pop.querySelector('#ge-transform-min')?.addEventListener('click', (e) => {
      e.stopPropagation();
      pop.classList.toggle('ge-transform-popup-minimised');
    });
    // Quick actions: flip W/H via sign so the reapply pipeline picks
    // up the new orientation. Rotate-90 nudges rotation ±90°.
    pop.querySelector('#ge-transform-flip-h')?.addEventListener('click', () => {
      const wIn = pop.querySelector('#ge-transform-w');
      const cur = parseInt(wIn.value, 10) || state.transformOrigW;
      wIn.value = String(-cur);
      wIn.dispatchEvent(new Event('input', { bubbles: true }));
    });
    pop.querySelector('#ge-transform-flip-v')?.addEventListener('click', () => {
      const hIn = pop.querySelector('#ge-transform-h');
      const cur = parseInt(hIn.value, 10) || state.transformOrigH;
      hIn.value = String(-cur);
      hIn.dispatchEvent(new Event('input', { bubbles: true }));
    });
    pop.querySelector('#ge-transform-rot-90')?.addEventListener('click', (e) => {
      const rIn = pop.querySelector('#ge-transform-rot');
      const cur = parseInt(rIn.value, 10) || 0;
      const delta = e.shiftKey ? -90 : 90;
      let next = cur + delta;
      while (next > 180) next -= 360;
      while (next <= -180) next += 360;
      rIn.value = String(next);
      // Big images: rotation pass blocks UI ~0.5–2 s. Show a spinner
      // so the user sees something happen. rAF defers the heavy work
      // past the current frame so the overlay paints first.
      showCanvasLoading('Rotating…');
      requestAnimationFrame(() => {
        try { rIn.dispatchEvent(new Event('input', { bubbles: true })); }
        finally { hideCanvasLoading(); }
      });
    });
    attachSpinRepeat(pop);
  }

  // Header-drag for the Transform popup. Default position: over the
  // right panel (layers area). Mobile pins via stylesheet so we use
  // setProperty 'important' to override during drag.
  function wireTransformDrag(pop) {
    const isMobile = window.matchMedia('(max-width: 820px)').matches;
    const defaultRight = 20;
    const defaultTop = 60;
    if (isMobile) {
      pop.style.setProperty('position', 'fixed', 'important');
    } else {
      pop.style.position = 'absolute';
      pop.style.right = defaultRight + 'px';
      pop.style.top = defaultTop + 'px';
      pop.style.left = 'auto';
    }
    const dragSource = pop.querySelector('[data-transform-drag]') || pop;
    let dragging = false;
    let startX = 0, startY = 0, originLeft = 0, originTop = 0;
    const NON_DRAG = 'input,button,select,textarea,a,[contenteditable]';

    const setPos = (x, y) => {
      if (isMobile) {
        pop.style.setProperty('left', x + 'px', 'important');
        pop.style.setProperty('top', y + 'px', 'important');
        pop.style.setProperty('right', 'auto', 'important');
        pop.style.setProperty('bottom', 'auto', 'important');
        pop.style.setProperty('width', 'auto', 'important');
        pop.style.setProperty('max-width', 'calc(100vw - 16px)', 'important');
      } else {
        pop.style.left = x + 'px';
        pop.style.top = y + 'px';
        pop.style.right = 'auto';
      }
    };

    const beginDrag = (clientX, clientY) => {
      dragging = true;
      const rect = pop.getBoundingClientRect();
      if (isMobile) {
        originLeft = rect.left;
        originTop = rect.top;
      } else {
        const parentRect = state.container.getBoundingClientRect();
        originLeft = rect.left - parentRect.left;
        originTop = rect.top - parentRect.top;
      }
      startX = clientX;
      startY = clientY;
      setPos(originLeft, originTop);
      pop.classList.add('ge-transform-popup-dragging');
      document.body.style.userSelect = 'none';
    };

    const moveDrag = (clientX, clientY) => {
      if (!dragging) return;
      const dx = clientX - startX;
      const dy = clientY - startY;
      let nx = originLeft + dx;
      let ny = originTop + dy;
      if (isMobile) {
        const rect = pop.getBoundingClientRect();
        nx = Math.max(0, Math.min(window.innerWidth - rect.width, nx));
        ny = Math.max(0, Math.min(window.innerHeight - rect.height, ny));
      }
      setPos(nx, ny);
    };

    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      document.body.style.userSelect = '';
      pop.classList.remove('ge-transform-popup-dragging');
    };

    dragSource.addEventListener('mousedown', (e) => {
      if (e.target.closest(NON_DRAG)) return;
      e.preventDefault();
      beginDrag(e.clientX, e.clientY);
    });
    document.addEventListener('mousemove', (e) => moveDrag(e.clientX, e.clientY));
    document.addEventListener('mouseup', endDrag);

    dragSource.addEventListener('touchstart', (e) => {
      if (e.target.closest(NON_DRAG)) return;
      if (!e.touches || e.touches.length !== 1) return;
      e.preventDefault();
      beginDrag(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: false });
    document.addEventListener('touchmove', (e) => {
      if (!dragging) return;
      if (!e.touches || e.touches.length !== 1) return;
      e.preventDefault();
      moveDrag(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: false });
    document.addEventListener('touchend', endDrag);
    document.addEventListener('touchcancel', endDrag);
  }

  function renderSnapshot(source, snapshot, geometry) {
    const canvas = document.createElement('canvas');
    canvas.width = geometry.width;
    canvas.height = geometry.height;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.save();
    ctx.translate(canvas.width / 2, canvas.height / 2);
    if (geometry.rotation) ctx.rotate(geometry.radians);
    ctx.scale(geometry.signedScaleX, geometry.signedScaleY);
    ctx.drawImage(source, -snapshot.width / 2, -snapshot.height / 2, snapshot.width, snapshot.height);
    ctx.restore();
    return canvas;
  }

  function currentTransformTarget() {
    return {
      width: state.transformPendingW,
      height: state.transformPendingH,
      rotation: state.transformPendingRot,
      flipH: state.transformPendingFlipH,
      flipV: state.transformPendingFlipV,
      centerX: state.transformCenter?.x,
      centerY: state.transformCenter?.y,
    };
  }

  function restoreTransformTarget(target) {
    if (!target) return;
    state.transformPendingW = target.width;
    state.transformPendingH = target.height;
    state.transformPendingRot = target.rotation;
    state.transformPendingFlipH = target.flipH;
    state.transformPendingFlipV = target.flipV;
    state.transformCenter = { x: target.centerX, y: target.centerY };
  }

  // Re-derive every selected layer from immutable per-session snapshots.
  function reapplyTransform() {
    if (state.transformTarget === 'selection') {
      if (!state.transformSelectionCanvas || !state.transformSelectionBounds || !state.transformCenter) return;
      const target = {
        width: state.transformPendingW,
        height: state.transformPendingH,
        rotation: state.transformPendingRot,
        flipH: state.transformPendingFlipH,
        flipV: state.transformPendingFlipV,
        centerX: state.transformCenter.x,
        centerY: state.transformCenter.y,
      };
      state.wandMask = transformSelectionMask(
        state.transformSelectionCanvas,
        state.transformSelectionBounds,
        target,
        state.imgWidth,
        state.imgHeight,
      );
      state.wandLayerId = state.activeLayerId;
      state.wandMaskSpace = 'document';
      state.selectionSource = 'transform';
      state.wandLastSeed = null;
      state.lassoPoints = [];
      state.lassoActive = false;
      state.transformBounds = transformedSelectionBounds(state.transformSelectionBounds, target);
      composite();
      syncTransformPopupValues();
      syncSelectionUi?.();
      drawTransformHandles();
      return;
    }
    if (!state.transformItems.length || !state.transformSelectionBounds || !state.transformCenter) return;
    const target = currentTransformTarget();
    const plan = planLayerTransform(
      state,
      state.transformItems,
      state.transformSelectionBounds,
      target,
      { maxDimension: EDITOR_MAX_DIMENSION, maxSurfacePixels: EDITOR_MAX_SURFACE_PIXELS },
    );
    if (!plan.ok) {
      restoreTransformTarget(lastValidLayerTarget);
      syncTransformPopupValues(true);
      if (lastTransformLimitMessage !== plan.reason) {
        uiModule?.showToast(plan.reason, 5000);
        lastTransformLimitMessage = plan.reason;
      }
      composite();
      drawTransformHandles();
      return false;
    }
    lastValidLayerTarget = { ...target };
    lastTransformLimitMessage = '';
    const documentTransform = frameTransformMatrix(state.transformSelectionBounds, target);
    for (const { snapshot, geometry: plannedGeometry } of plan.items) {
      const layer = snapshot.layer;
      let geometry = plannedGeometry;
      if (snapshot.placed && (snapshot.wasPlaced || state.transformPreserveSource !== false)) {
        layer.placed = transformPlacedData(snapshot.placed, documentTransform);
        const rendered = renderPlacedLayer(layer);
        geometry = {
          ...plannedGeometry,
          width: rendered.canvas.width,
          height: rendered.canvas.height,
          offset: rendered.offset,
        };
      } else {
        if (snapshot.kind === 'raster') {
          layer.kind = 'raster';
          layer.placed = null;
        }
        const output = renderSnapshot(snapshot.canvas, snapshot, geometry);
        layer.canvas.width = geometry.width;
        layer.canvas.height = geometry.height;
        layer.ctx = layer.canvas.getContext('2d');
        layer.ctx.drawImage(output, 0, 0);
      }
      layer._adjFinal = null;
      layer._adjFinalKey = null;
      layer._adjCache = null;
      layer._adjCacheKey = null;
      state.layerOffsets.set(layer.id, geometry.offset);
      for (const maskSnapshot of snapshot.masks) {
        if (maskSnapshot.linked) {
          const positioned = document.createElement('canvas');
          positioned.width = snapshot.width;
          positioned.height = snapshot.height;
          positioned.getContext('2d').drawImage(
            maskSnapshot.canvas,
            maskSnapshot.offset.x,
            maskSnapshot.offset.y,
          );
          const maskOutput = renderSnapshot(positioned, snapshot, geometry);
          maskSnapshot.mask.canvas.width = geometry.width;
          maskSnapshot.mask.canvas.height = geometry.height;
          maskSnapshot.mask.ctx = maskSnapshot.mask.canvas.getContext('2d');
          maskSnapshot.mask.ctx.drawImage(maskOutput, 0, 0);
          maskSnapshot.mask.offset = { x: 0, y: 0 };
        } else {
          // Rebuild from the immutable snapshot so every preview remains
          // cancel-safe, then compensate for the layer's new document offset.
          maskSnapshot.mask.canvas.width = maskSnapshot.canvas.width;
          maskSnapshot.mask.canvas.height = maskSnapshot.canvas.height;
          maskSnapshot.mask.ctx = maskSnapshot.mask.canvas.getContext('2d');
          maskSnapshot.mask.ctx.drawImage(maskSnapshot.canvas, 0, 0);
          maskSnapshot.mask.offset = {
            x: maskSnapshot.documentOffset.x - geometry.offset.x,
            y: maskSnapshot.documentOffset.y - geometry.offset.y,
          };
        }
      }
    }
    state.transformBounds = transformedSelectionBounds(state.transformSelectionBounds, target);
    composite();
    syncTransformPopupValues();
    syncGeometryControls?.();
    drawTransformHandles();
    return true;
  }

  function confirmTransform() {
    closeTransformPopup();
    const target = state.transformTarget;
    if (target === 'selection') {
      clearTransformState();
      composite();
      syncSelectionUi?.();
      schedulePersist?.();
      uiModule?.showToast('Selection transformed');
      return;
    }
    for (const snapshot of state.transformItems) {
      const layer = snapshot.layer;
      if (!layer || !['text', 'shape'].includes(layer.kind)) continue;
      const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
      const center = { x: offset.x + layer.canvas.width / 2, y: offset.y + layer.canvas.height / 2 };
      const retained = layer.kind === 'text' ? normalizeTextData(layer.text) : normalizeShapeData(layer.shape);
      retained.transform.scaleX *= state.transformPendingW / Math.max(1, state.transformSessionStartW);
      retained.transform.scaleY *= state.transformPendingH / Math.max(1, state.transformSessionStartH);
      retained.transform.rotation += state.transformPendingRot;
      if (state.transformPendingFlipH) retained.transform.flipH = !retained.transform.flipH;
      if (state.transformPendingFlipV) retained.transform.flipV = !retained.transform.flipV;
      if (layer.kind === 'text') {
        layer.text = retained;
        renderTextLayer(layer);
      } else {
        layer.shape = retained;
        renderShapeLayer(layer);
      }
      state.layerOffsets.set(layer.id, {
        x: Math.round(center.x - layer.canvas.width / 2),
        y: Math.round(center.y - layer.canvas.height / 2),
      });
    }
    const count = state.transformItems.length;
    clearTransformState();
    composite();
    syncGeometryControls?.();
    schedulePersist?.();
    uiModule.showToast(count > 1 ? `${count} layers transformed` : 'Transform applied');
  }

  function nudgeTransform(dx, dy) {
    if (!state.transformActive || !state.transformCenter) return false;
    state.transformCenter = {
      x: state.transformCenter.x + Number(dx || 0),
      y: state.transformCenter.y + Number(dy || 0),
    };
    reapplyTransform();
    return true;
  }

  function clearTransformState() {
    state.transformOrigCanvas = null;
    state.transformOrigMasks = [];
    state.transformOrigOffset = null;
    state.transformSelectionCanvas = null;
    state.transformLayers = [];
    state.transformItems = [];
    state.transformSelectionBounds = null;
    state.transformBounds = null;
    state.transformCenter = null;
    state.transformStartCenter = null;
    state.transformOrigW = 0;
    state.transformOrigH = 0;
    state.transformPendingW = 0;
    state.transformPendingH = 0;
    state.transformPendingRot = 0;
    state.transformPendingFlipH = false;
    state.transformPendingFlipV = false;
    state.transformActive = false;
    state.transformTarget = null;
    state.transformLayer = null;
    state.transformHandle = null;
    state.hoveredHandle = null;
    state.transformSessionStartW = 0;
    state.transformSessionStartH = 0;
    state.transformStartRotation = 0;
    state.transformStartFlipH = false;
    state.transformStartFlipV = false;
    lastValidLayerTarget = null;
    lastTransformLimitMessage = '';
  }

  function cancelTransform() {
    closeTransformPopup();
    const hadTransform = state.transformActive;
    if (hadTransform) {
      undo(); // restore the one pre-session snapshot
      state.redoStack = []; // Cancel must not expose the discarded preview via Redo.
    }
    clearTransformState();
    composite();
    syncGeometryControls?.();
    syncSelectionUi?.();
  }

  return {
    startTransform, startSelectionTransform, openTransformPopup, closeTransformPopup,
    reapplyTransform, nudgeTransform, confirmTransform, cancelTransform,
  };
}
