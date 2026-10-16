/**
 * Canvas event wiring — mouse, touch (including pinch-zoom on two
 * fingers), and the canvas-area pan handler.
 *
 *   Mouse:
 *     mousedown on canvas    → beginDraw
 *     mousemove on window    → continueDraw (window so a drag can
 *                              continue past the canvas edge)
 *     mouseup on window      → endDraw
 *     mouseenter/mouseleave  → show/hide the brush-cursor overlay
 *     mousedown on canvas-area (NOT on the canvas itself, lasso only)
 *                            → beginDraw (lasso starts outside canvas)
 *
 *   Touch:
 *     touchstart 1 finger    → beginDraw
 *     touchmove  1 finger    → continueDraw
 *     touchend → endDraw; touchcancel → cancelDraw
 *     touchstart 2 fingers   → pinch-zoom + 2-finger pan
 *     2-finger tap           → undo (onTwoFingerTap)
 *     3-finger tap           → redo (onThreeFingerTap)
 *
 *   A multi-finger gesture reverts the accidental single-finger stroke
 *   begun by the first finger to land (cancelDraw), so a pinch or a
 *   tap never leaves a stray dab behind.
 *
 *   Pan:
 *     Hand tool / held Space / middle mouse can drag directly over the
 *     image. Empty workspace remains draggable with any tool. The same
 *     path drives one-finger Hand-tool panning on touch screens.
 *
 *   Exposes `canvasArea._resetPan()` so the zoom/fit reset can clear
 *   the pan offset.
 *
 * @param {{
 *   canvasArea:        HTMLDivElement,
 *   beginDraw:         (e: Event) => void,
 *   continueDraw:      (e: Event) => void,
 *   endDraw:           (e?: Event) => void,
 *   cancelDraw?:       () => void,
 *   updateBrushCursor: (e: Event) => void,
 *   syncZoomControls?: () => void,
 *   onTwoFingerTap?:  () => void,
 *   onThreeFingerTap?: () => void,
 * }} ctx
 */
import { state } from './state.js';
import {
  applyCanvasPan,
  isDirectPanIntent,
  nextPanOffset,
  syncPanCursor,
} from './canvas-navigation.js';
import { wireCanvasContextMenu } from './canvas-context-menu.js';

let canvasWindowBindings;

export function wireCanvasEvents({ canvasArea, beginDraw, continueDraw, endDraw, cancelDraw, updateBrushCursor, updateEyedropperPreview, syncZoomControls, onViewportChange, onTwoFingerTap, onThreeFingerTap }) {
  canvasWindowBindings?.abort();
  canvasWindowBindings = new AbortController();
  const { signal } = canvasWindowBindings;
  let suppressMouseUntil = 0;
  // Tool-aware right-click menu on the image and the surrounding area.
  wireCanvasContextMenu(canvasArea, { signal });
  // Window-level drag handlers exist so a gesture can continue past the
  // canvas edge. But the direct-manipulation selection/crop rects are
  // built from raw pointer coords, so pointer motion over editor chrome
  // (topbar, toolbars, panels) must not feed the rect. Brush/transform/
  // move/resize gestures DO keep tracking over chrome — overshooting the
  // canvas edge is normal for those and was the reason for the window
  // binding.
  const UI_CHROME_SELECTOR = '.ge-topbar, .ge-toolbar, .ge-controls, .ge-right-panel, '
    + '.ge-dock, .ge-options-bar, .ge-canvas-toolbar, button, input, select, textarea';
  const isUiChrome = e => !!e.target?.closest?.(UI_CHROME_SELECTOR);
  // Only in-progress selection/crop drags are affected by the chrome
  // guard. A committed cropRect/selection is a resting state — clicking
  // its Apply button must not be treated as "strayed onto chrome".
  const directGestureActive = () => !!(
    state.selectionMoving || state.marqueeActive ||
    state.lassoActive || (state.lassoPoints && state.lassoPoints.length) ||
    state.cropping || state.cropMoving
  );
  // Mouse — mousedown stays on the canvas; mousemove/up are bound to
  // the WINDOW so a drag can continue (and end) past the canvas edge.
  // Critical for the Resize tool where users overshoot.
  state.mainCanvas.addEventListener('mousedown', (e) => {
    if (Date.now() < suppressMouseUntil) return;
    if (isDirectPanIntent(state.tool, state.spacePanActive, e.button)) return;
    beginDraw(e);
  });
  window.addEventListener('mousemove', (e) => {
    if (Date.now() < suppressMouseUntil) return;
    // Only shield the raw-coordinate selection/crop rects from chrome.
    if (isUiChrome(e) && directGestureActive()) return;
    continueDraw(e);
  }, { signal });
  window.addEventListener('mouseup', (e) => {
    if (Date.now() < suppressMouseUntil) return;
    // Releasing over chrome commits the gesture; the marquee/crop end
    // paths clamp the rect to the document, so an overshoot onto a panel
    // is kept (Photoshop behaviour), not discarded.
    endDraw(e);
  }, { signal });
  // A tool switch happens on the tool button, before the release. Cancel
  // any in-progress direct gesture first so its move is reverted rather
  // than committed by the subsequent chrome mouseup. Explicit tool
  // switches are the only place we abandon a selection/crop gesture.
  window.addEventListener('mousedown', (e) => {
    if (!directGestureActive()) return;
    if (e.target?.closest?.('.ge-tool-btn')) cancelDraw?.();
  }, { capture: true, signal });
  // Preserve pressure and browser-coalesced samples for pen input.
  // Compatibility mouse events are briefly suppressed to avoid a
  // duplicate stroke after pointerup.
  let activePenId = null;
  state.mainCanvas.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'pen') return;
    suppressMouseUntil = Date.now() + 500;
    activePenId = e.pointerId;
    try { state.mainCanvas.setPointerCapture(activePenId); } catch {}
    beginDraw(e);
    e.preventDefault();
  });
  state.mainCanvas.addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'pen' || e.pointerId !== activePenId) return;
    continueDraw(e);
    e.preventDefault();
  });
  const endPen = (e) => {
    if (e.pointerType !== 'pen' || e.pointerId !== activePenId) return;
    if (e.type === 'pointercancel') cancelDraw?.();
    else endDraw(e);
    try { state.mainCanvas.releasePointerCapture(activePenId); } catch {}
    activePenId = null;
    suppressMouseUntil = Date.now() + 500;
    e.preventDefault();
  };
  state.mainCanvas.addEventListener('pointerup', endPen);
  state.mainCanvas.addEventListener('pointercancel', endPen);
  // Lasso can start OUTSIDE the canvas — fallback mousedown on the
  // surrounding canvas-area so the user can begin a lasso path in
  // the empty space around the image. Other tools stay canvas-only.
  canvasArea.addEventListener('mousedown', (e) => {
    if (state.tool !== 'lasso') return;
    if (e.target === state.mainCanvas) return; // already handled
    beginDraw(e);
  });
  state.mainCanvas.addEventListener('mouseenter', (e) => {
    if (state.tool === 'eyedropper') updateEyedropperPreview?.(e);
    if (['brush', 'eraser', 'inpaint', 'lasso', 'clone', 'heal', 'smudge', 'dodge', 'burn'].includes(state.tool)) updateBrushCursor(e);
  });
  state.mainCanvas.addEventListener('mouseleave', () => {
    // Only hide the brush-cursor overlay on leave — DO NOT end the
    // drag, so the user can drag a resize handle past the canvas edge.
    if (state.cursorEl) state.cursorEl.style.display = 'none';
    if (state.tool === 'eyedropper') updateEyedropperPreview?.(null, true);
    if (state.tool === 'transform' && state.hoveredHandle) {
      state.hoveredHandle = null;
      state.mainCanvas.style.cursor = 'default';
      // The frame is drawn on a separate overlay, so clear its hover state
      // explicitly when the pointer leaves the source canvas.
      continueDraw?.({ clientX: -1, clientY: -1, target: null });
    }
  });

  // Touch — single finger draws; two fingers pan + pinch-zoom.
  // A quick two-finger tap = undo, a quick three-finger tap = redo.
  let multiActive = false;
  let multiStartDist = 0;
  let multiStartZoom = 1;
  let multiStartCenter = { x: 0, y: 0 };
  let multiStartPan = { x: 0, y: 0 };
  // Tap-gesture tracking (mobile undo/redo). A gesture starts when the
  // first finger lands and is decided when the last finger lifts: at
  // most TAP_MAX_MS elapsed, the centre never moved more than
  // TAP_MAX_MOVE px, and the peak finger count was 2 (undo) or 3 (redo).
  const TAP_MAX_MS = 300;
  const TAP_MAX_MOVE = 12;
  let gesturePeakTouches = 0;
  let gestureStartAt = 0;
  let gestureStartCenter = null;
  let gestureMoved = false;
  const touchInfo = (e) => {
    const t1 = e.touches[0], t2 = e.touches[1];
    const cx = (t1.clientX + t2.clientX) / 2;
    const cy = (t1.clientY + t2.clientY) / 2;
    const dx = t2.clientX - t1.clientX;
    const dy = t2.clientY - t1.clientY;
    return { cx, cy, dist: Math.hypot(dx, dy) };
  };
  const touchCenter = (e) => {
    if (!e.touches || !e.touches.length) return null;
    let x = 0, y = 0;
    for (const t of e.touches) { x += t.clientX; y += t.clientY; }
    return { x: x / e.touches.length, y: y / e.touches.length };
  };
  const applyCanvasOffset = (x, y) => applyCanvasPan(state, canvasArea, x, y);
  state.mainCanvas.addEventListener('touchstart', (e) => {
    e.preventDefault();
    if (gesturePeakTouches === 0) {
      // First contact of a fresh gesture (finger counts are read from
      // e.touches so simultaneous touches are handled too).
      gesturePeakTouches = Math.max(1, e.touches.length);
      gestureStartAt = Date.now();
      gestureStartCenter = touchCenter(e);
      gestureMoved = false;
    } else if (e.touches.length > gesturePeakTouches) {
      gesturePeakTouches = e.touches.length;
    }
    if (e.touches.length >= 2) {
      // Revert the accidental single-finger stroke the first finger
      // began, then switch to pinch/pan so multi-finger input never
      // leaves a stray dab behind.
      if (!multiActive) cancelDraw?.();
      multiActive = true;
      const info = touchInfo(e);
      multiStartDist = info.dist;
      multiStartZoom = state.zoom;
      multiStartCenter = { x: info.cx, y: info.cy };
      multiStartPan = { x: state.panX || 0, y: state.panY || 0 };
      return;
    }
    if (multiActive) return;
    if (state.tool === 'hand') return;
    beginDraw(e);
  }, { passive: false });
  state.mainCanvas.addEventListener('touchmove', (e) => {
    e.preventDefault();
    if (!gestureMoved && gestureStartCenter) {
      const c = touchCenter(e);
      if (c && Math.hypot(c.x - gestureStartCenter.x, c.y - gestureStartCenter.y) > TAP_MAX_MOVE) {
        gestureMoved = true;
      }
    }
    if (multiActive && e.touches.length >= 2) {
      const info = touchInfo(e);
      const ratio = info.dist / Math.max(1, multiStartDist);
      const newZoom = Math.max(0.1, Math.min(5, multiStartZoom * ratio));
      if (Math.abs(newZoom - state.zoom) > 0.001) {
        state.zoom = newZoom;
        state.mainCanvas.style.width = (state.imgWidth * state.zoom) + 'px';
        state.mainCanvas.style.height = (state.imgHeight * state.zoom) + 'px';
        const label = state.container.querySelector('.ge-zoom-label');
        if (label) label.textContent = Math.round(state.zoom * 100) + '%';
        syncZoomControls?.();
        onViewportChange?.();
      }
      const dx = info.cx - multiStartCenter.x;
      const dy = info.cy - multiStartCenter.y;
      applyCanvasOffset(multiStartPan.x + dx, multiStartPan.y + dy);
      onViewportChange?.();
      return;
    }
    if (multiActive) return;
    continueDraw(e);
  }, { passive: false });
  state.mainCanvas.addEventListener('touchend', (e) => {
    // Decide the gesture only once every finger has lifted.
    if (e.touches.length === 0 && gesturePeakTouches >= 2) {
      const quick = !gestureMoved && (Date.now() - gestureStartAt) <= TAP_MAX_MS;
      const peak = gesturePeakTouches;
      gesturePeakTouches = 0;
      gestureStartCenter = null;
      gestureMoved = false;
      multiActive = false;
      if (quick) {
        if (peak === 2) onTwoFingerTap?.();
        else if (peak >= 3) onThreeFingerTap?.();
      }
      return;
    }
    if (multiActive) {
      if (e.touches.length < 2) multiActive = false;
      return;
    }
    endDraw(e);
    if (e.touches.length === 0) {
      gesturePeakTouches = 0;
      gestureStartCenter = null;
      gestureMoved = false;
    }
  });
  state.mainCanvas.addEventListener('touchcancel', () => {
    multiActive = false;
    gesturePeakTouches = 0;
    gestureStartCenter = null;
    gestureMoved = false;
    cancelDraw?.();
  });

  // Direct pan gestures own the image as well as the surrounding area.
  // With other tools only empty workspace pans, preserving normal edit input.
  let panning = false;
  let pid = null;
  let transformPointerId = null;
  let startX = 0, startY = 0;
  let startPanX = 0, startPanY = 0;
  const getOffset = () => ({ x: state.panX || 0, y: state.panY || 0 });
  const applyOffset = (x, y) => {
    const result = applyCanvasPan(state, canvasArea, x, y);
    onViewportChange?.();
    return result;
  };
  canvasArea.addEventListener('pointerdown', (e) => {
    const directPan = isDirectPanIntent(state.tool, state.spacePanActive, e.button);
    if (state.tool === 'lasso' && !directPan) return;
    if (e.target.closest('button, input, .ge-adj-popup, .ge-transform-popup, .ge-fx-popup, .ge-inpaint-popup, .ge-controls, .ge-right-panel, .ge-fx-menu')) return;
    const onImage = e.target === state.mainCanvas || e.target === state.transformOverlay;
    if (onImage && !directPan) return;
    // During an active transform the corner/rotation handles render
    // OUTSIDE the canvas (over the surrounding area), and the overlay is
    // pointer-events:none — so a grab on an outside handle lands here.
    // Route it to the transform tool (getHandleAt works in image space,
    // even for points beyond the canvas) instead of panning the canvas.
    if (state.transformActive && !directPan) {
      beginDraw(e);
      // Only swallow the event (skip pan) if a handle was grabbed OR the
      // layer-move fallback engaged; otherwise let the pan logic below
      // run so empty space still pans while the transform tool is open.
      if (state.transformHandle || state.moving) {
        // Mouse drags already continue on window mousemove. Pen/touch drags
        // that start on an outside-canvas handle need pointer capture so the
        // session survives leaving the editor surface.
        if (e.pointerType && e.pointerType !== 'mouse') {
          transformPointerId = e.pointerId;
          try { canvasArea.setPointerCapture(transformPointerId); } catch {}
          e.preventDefault();
        }
        return;
      }
    }
    const off = getOffset();
    panning = true;
    pid = e.pointerId;
    startX = e.clientX;
    startY = e.clientY;
    startPanX = off.x;
    startPanY = off.y;
    try { canvasArea.setPointerCapture(pid); } catch {}
    state.navigationPanning = true;
    syncPanCursor(state, canvasArea, true);
    e.preventDefault();
  });
  canvasArea.addEventListener('pointermove', (e) => {
    if (transformPointerId !== null && e.pointerId === transformPointerId) {
      continueDraw(e);
      return;
    }
    if (!panning || e.pointerId !== pid) return;
    const next = nextPanOffset(
      { x: startX, y: startY },
      { x: startPanX, y: startPanY },
      { x: e.clientX, y: e.clientY },
    );
    applyOffset(next.x, next.y);
  });
  const endPan = (e) => {
    if (transformPointerId !== null && (!e || e.pointerId === transformPointerId)) {
      const capturedId = transformPointerId;
      transformPointerId = null;
      if (e?.type === 'pointercancel') cancelDraw?.();
      else endDraw(e);
      try { canvasArea.releasePointerCapture(capturedId); } catch {}
    }
    if (!panning) return;
    panning = false;
    try { canvasArea.releasePointerCapture(pid); } catch {}
    pid = null;
    state.navigationPanning = false;
    syncPanCursor(state, canvasArea, false);
  };
  canvasArea.addEventListener('pointerup', endPan);
  canvasArea.addEventListener('pointercancel', endPan);
  // Treat focus loss as release: preserve the work already drawn, but never
  // resume the gesture when the user returns without a fresh pointer press.
  window.addEventListener('blur', () => {
    if (!state.editorOpen || !canvasArea.contains(state.mainCanvas)) return;
    endDraw();
    if (activePenId !== null) {
      try { state.mainCanvas.releasePointerCapture(activePenId); } catch {}
      activePenId = null;
    }
    multiActive = false;
    endPan();
    state.spacePanActive = false;
    syncPanCursor(state, canvasArea, false);
    if (state.cursorEl) state.cursorEl.style.display = 'none';
  }, { signal });
  // Reset offset whenever zoom/fit changes the canvas size.
  canvasArea._resetPan = () => applyOffset(0, 0);
  const navigation = {
    resetPan: canvasArea._resetPan,
    setTemporaryPan(active) {
      state.spacePanActive = !!active;
      syncPanCursor(state, canvasArea, panning);
    },
    updateCursor() {
      syncPanCursor(state, canvasArea, panning);
    },
  };
  navigation.updateCursor();
  return navigation;
}
