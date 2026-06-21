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
 *     touchend / touchcancel → endDraw
 *     touchstart 2 fingers   → pinch-zoom + 2-finger pan
 *
 *   Pan (any free space around the canvas):
 *     pointerdown / pointermove / pointerup on canvas-area, skipping
 *     the canvas + transform overlay + UI elements above them. Sets
 *     canvasArea.dataset.panX/Y + CSS transform on both canvases.
 *
 *   Exposes `canvasArea._resetPan(x, y)` so the zoom/fit reset can clear
 *   or re-center the pan offset.
 *
 * @param {{
 *   canvasArea:        HTMLDivElement,
 *   beginDraw:         (e: Event) => void,
 *   continueDraw:      (e: Event) => void,
 *   endDraw:           (e?: Event) => void,
 *   updateBrushCursor: (e: Event) => void,
 *   syncZoomControls?: () => void,
 * }} ctx
 */
import { state } from './state.js';

export function wireCanvasEvents({ canvasArea, beginDraw, continueDraw, endDraw, updateBrushCursor, syncZoomControls }) {
  // Mouse — mousedown stays on the canvas; mousemove/up are bound to
  // the WINDOW so a drag can continue (and end) past the canvas edge.
  // Critical for the Resize tool where users overshoot.
  state.mainCanvas.addEventListener('mousedown', beginDraw);
  window.addEventListener('mousemove', continueDraw);
  window.addEventListener('mouseup', endDraw);
  // Lasso can start OUTSIDE the canvas — fallback mousedown on the
  // surrounding canvas-area so the user can begin a lasso path in
  // the empty space around the image. Other tools stay canvas-only.
  canvasArea.addEventListener('mousedown', (e) => {
    if (state.tool !== 'lasso') return;
    if (e.target === state.mainCanvas) return; // already handled
    beginDraw(e);
  });
  state.mainCanvas.addEventListener('mouseenter', (e) => {
    if (['brush', 'eraser', 'inpaint', 'lasso', 'clone', 'rembg'].includes(state.tool)) updateBrushCursor(e);
  });
  state.mainCanvas.addEventListener('mouseleave', () => {
    // Only hide the brush-cursor overlay on leave — DO NOT end the
    // drag, so the user can drag a resize handle past the canvas edge.
    if (state.cursorEl) state.cursorEl.style.display = 'none';
  });

  // Touch — single finger draws; two fingers pan + pinch-zoom.
  let multiActive = false;
  let multiStartDist = 0;
  let multiStartZoom = 1;
  let multiStartCenter = { x: 0, y: 0 };
  let multiStartPan = { x: 0, y: 0 };
  const touchInfo = (e) => {
    const t1 = e.touches[0], t2 = e.touches[1];
    const cx = (t1.clientX + t2.clientX) / 2;
    const cy = (t1.clientY + t2.clientY) / 2;
    const dx = t2.clientX - t1.clientX;
    const dy = t2.clientY - t1.clientY;
    return { cx, cy, dist: Math.hypot(dx, dy) };
  };
  const applyCanvasOffset = (x, y) => {
    canvasArea.dataset.panX = String(x);
    canvasArea.dataset.panY = String(y);
    const t = `translate3d(${x}px, ${y}px, 0)`;
    state.mainCanvas.style.transform = t;
    if (state.transformOverlay) state.transformOverlay.style.transform = t;
  };
  state.mainCanvas.addEventListener('touchstart', (e) => {
    e.preventDefault();
    if (e.touches.length >= 2) {
      // End any in-progress single-finger draw before switching modes.
      if (!multiActive) endDraw();
      multiActive = true;
      const info = touchInfo(e);
      multiStartDist = info.dist;
      multiStartZoom = state.zoom;
      multiStartCenter = { x: info.cx, y: info.cy };
      multiStartPan = {
        x: parseFloat(canvasArea.dataset.panX || '0') || 0,
        y: parseFloat(canvasArea.dataset.panY || '0') || 0,
      };
      return;
    }
    if (multiActive) return;
    beginDraw(e);
  }, { passive: false });
  state.mainCanvas.addEventListener('touchmove', (e) => {
    e.preventDefault();
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
      }
      const dx = info.cx - multiStartCenter.x;
      const dy = info.cy - multiStartCenter.y;
      applyCanvasOffset(multiStartPan.x + dx, multiStartPan.y + dy);
      return;
    }
    if (multiActive) return;
    continueDraw(e);
  }, { passive: false });
  state.mainCanvas.addEventListener('touchend', (e) => {
    if (multiActive) {
      if (e.touches.length < 2) multiActive = false;
      return;
    }
    endDraw(e);
  });
  state.mainCanvas.addEventListener('touchcancel', () => {
    multiActive = false;
    endDraw();
  });

  // Press-and-drag in the empty space AROUND the canvas pans the
  // canvas + overlay via CSS transform. Works even when the image
  // fits the viewport (no scroll needed). Skips presses on the canvas
  // itself (the canvas owns its own drawing input) or on UI elements
  // above it.
  let panning = false;
  let pid = null;
  let startX = 0, startY = 0;
  const getOffset = () => {
    const v = canvasArea.dataset.panX || '0';
    const u = canvasArea.dataset.panY || '0';
    return { x: parseFloat(v) || 0, y: parseFloat(u) || 0 };
  };
  const applyOffset = (x, y) => {
    canvasArea.dataset.panX = String(x);
    canvasArea.dataset.panY = String(y);
    const t = `translate3d(${x}px, ${y}px, 0)`;
    state.mainCanvas.style.transform = t;
    if (state.transformOverlay) state.transformOverlay.style.transform = t;
  };
  canvasArea.addEventListener('pointerdown', (e) => {
    if (state.tool === 'lasso') return;
    if (e.target === state.mainCanvas || e.target === state.transformOverlay) return;
    if (e.target.closest('button, input, .ge-adj-popup, .ge-transform-popup, .ge-fx-popup, .ge-inpaint-popup, .ge-controls, .ge-right-panel, .ge-fx-menu')) return;
    // During an active transform the corner/rotation handles render
    // OUTSIDE the canvas (over the surrounding area), and the overlay is
    // pointer-events:none — so a grab on an outside handle lands here.
    // Route it to the transform tool (getHandleAt works in image space,
    // even for points beyond the canvas) instead of panning the canvas.
    if (state.transformActive) {
      beginDraw(e);
      // Only swallow the event (skip pan) if a handle was grabbed OR the
      // layer-move fallback engaged; otherwise let the pan logic below
      // run so empty space still pans while the transform tool is open.
      if (state.transformHandle || state.moving) return;
    }
    const off = getOffset();
    panning = true;
    pid = e.pointerId;
    startX = e.clientX - off.x;
    startY = e.clientY - off.y;
    try { canvasArea.setPointerCapture(pid); } catch {}
    canvasArea.style.cursor = 'grabbing';
    e.preventDefault();
  });
  canvasArea.addEventListener('pointermove', (e) => {
    if (!panning || e.pointerId !== pid) return;
    applyOffset(e.clientX - startX, e.clientY - startY);
  });
  const endPan = () => {
    if (!panning) return;
    panning = false;
    try { canvasArea.releasePointerCapture(pid); } catch {}
    pid = null;
    canvasArea.style.cursor = '';
  };
  canvasArea.addEventListener('pointerup', endPan);
  canvasArea.addEventListener('pointercancel', endPan);
  // Reset offset whenever zoom/fit changes the canvas size. Fit can pass
  // a small offset so mobile bottom sheets don't cover the fitted image.
  canvasArea._resetPan = (x = 0, y = 0) => applyOffset(x, y);
}
