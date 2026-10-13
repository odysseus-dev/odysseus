/**
 * Transform-tool handle rendering + hit-testing + overlay sync.
 *
 * Lives separately from `transform-drag.js` (which owns the drag
 * STATE MACHINE) because these three helpers are pure geometry that
 * happens to read shared state — they don't track in-progress drags,
 * they just paint and hit-test.
 *
 *  - `syncOverlay(margin)`  positions the overlay canvas + sizes its
 *                           bitmap based on the main canvas + zoom.
 *  - `drawHandles(margin)`  draws the rotated bounding outline + 8
 *                           resize handles + the rotation knob (with
 *                           hover / active visual states).
 *  - `getHandleAt(x, y)`    returns the handle id under (x, y), or
 *                           null. Geometry MUST mirror `drawHandles`
 *                           exactly or the user grabs phantom points.
 *
 * No event listeners attached here — the dispatcher in
 * editor/tools/transform-drag.js calls `getHandleAt` and routes
 * pointer events.
 */
import { state } from '../state.js';
import {
  hitTestTransformHandle,
  pointInTransformFrame,
  transformFrameGeometry,
} from '../transform-frame-geometry.js';

/**
 * Position the transform overlay canvas + size its backing bitmap.
 * Margin is screen-space slack on each side so controls remain visible
 * at every zoom. It is converted to image pixels for the backing canvas.
 */
export function syncOverlay(margin) {
  if (!state.transformOverlay || !state.mainCanvas) return;
  if (!state.transformActive) {
    state.transformOverlay.style.display = 'none';
    return;
  }
  const imageMargin = margin / Math.max(0.0001, state.zoom);
  const W = Math.ceil(state.mainCanvas.width + 2 * imageMargin);
  const H = Math.ceil(state.mainCanvas.height + 2 * imageMargin);
  if (state.transformOverlay.width !== W) state.transformOverlay.width = W;
  if (state.transformOverlay.height !== H) state.transformOverlay.height = H;
  // Overlay must scale with state.zoom so its handles render at the
  // SAME on-screen size as the main canvas content. Without this, the
  // overlay renders at full bitmap size while main canvas shrinks
  // (zoomed-out), making handles look massive.
  state.transformOverlay.style.display = '';
  state.transformOverlay.style.position = 'absolute';
  state.transformOverlay.style.width  = (W * state.zoom) + 'px';
  state.transformOverlay.style.height = (H * state.zoom) + 'px';
  state.transformOverlay.style.pointerEvents = 'none';
  state.transformOverlay.style.zIndex = '5';
  // Position the overlay at the main canvas's LAYOUT position
  // (offsetLeft/Top — unaffected by CSS transforms), shifted up-left by
  // the overlay's `margin` image-px of handle slack. Then SHARE the
  // canvas's transform (the pan handler writes the same translate3d to
  // both canvas + overlay), so pan moves them together. Reading the
  // layout offset (not getBoundingClientRect, which includes the pan
  // transform) is what avoids the double-pan "bounce".
  state.transformOverlay.style.left = Math.round(state.mainCanvas.offsetLeft - margin) + 'px';
  state.transformOverlay.style.top  = Math.round(state.mainCanvas.offsetTop  - margin) + 'px';
  state.transformOverlay.style.transform = state.mainCanvas.style.transform || 'none';
}


function rotationHandleInside(frame) {
  let inside = false;
  const outside = transformFrameGeometry(frame, { zoom: state.zoom }).rotationHandle;
  // The overlay deliberately extends beyond the image, so only fold the
  // rotation control inward when the surrounding viewport would clip it.
  try {
    const area = state.container && state.container.querySelector('.ge-canvas-area');
    if (area && !inside) {
      const aRect = area.getBoundingClientRect();
      const mRect = state.mainCanvas.getBoundingClientRect();
      const scaleX = mRect.width / state.mainCanvas.width;
      const scaleY = mRect.height / state.mainCanvas.height;
      const knobClientX = mRect.left + outside.x * scaleX;
      const knobClientY = mRect.top + outside.y * scaleY;
      if (knobClientY < aRect.top + 6) inside = true;
      if (knobClientX < aRect.left + 6 || knobClientX > aRect.right - 6) inside = true;
      const popupRect = state.transformPopup?.getBoundingClientRect?.();
      if (popupRect
        && knobClientX >= popupRect.left
        && knobClientX <= popupRect.right
        && knobClientY >= popupRect.top
        && knobClientY <= popupRect.bottom) {
        inside = true;
      }
    }
  } catch {}
  return inside;
}

function transformFrame() {
  if (state.transformTarget === 'selection') {
    if (!state.transformSelectionBounds) return null;
    return {
      centerX: state.transformCenter?.x ?? state.transformSelectionBounds.centerX,
      centerY: state.transformCenter?.y ?? state.transformSelectionBounds.centerY,
      width: state.transformPendingW || state.transformSelectionBounds.width,
      height: state.transformPendingH || state.transformSelectionBounds.height,
      rotation: state.transformPendingRot || 0,
    };
  }
  const layer = state.transformLayer;
  if (!layer) return null;
  const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
  const fallbackCenter = {
    x: off.x + layer.canvas.width / 2,
    y: off.y + layer.canvas.height / 2,
  };
  return {
    centerX: state.transformCenter?.x ?? fallbackCenter.x,
    centerY: state.transformCenter?.y ?? fallbackCenter.y,
    width: state.transformPendingW || layer.canvas.width,
    height: state.transformPendingH || layer.canvas.height,
    rotation: state.transformPendingRot || 0,
  };
}


/**
 * Draw the rotated bounding outline + 8 resize handles + the rotation
 * knob into the overlay canvas. The overlay is translated by `margin`
 * so image (0,0) maps to overlay (margin, margin).
 */
export function drawHandles(margin) {
  if (!state.transformActive || (state.transformTarget !== 'selection' && !state.transformLayer)) return;
  syncOverlay(margin);
  if (!state.transformOverlayCtx) return;
  const frame = transformFrame();
  if (!frame) return;
  const rotInside = rotationHandleInside(frame);
  const geometry = transformFrameGeometry(frame, {
    zoom: state.zoom,
    rotationInside: rotInside,
  });
  const ctx = state.transformOverlayCtx;
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#e06c75';
  const imageMargin = margin / Math.max(0.0001, state.zoom);
  // Clear + shift drawing by margin so image (0,0) maps to overlay (M,M).
  ctx.clearRect(0, 0, state.transformOverlay.width, state.transformOverlay.height);
  ctx.save();
  ctx.translate(imageMargin, imageMargin);
  // Zoom-corrected handle size + stroke so they stay readable at any zoom.
  const sz = 10 / state.zoom;
  const stroke = 1.5 / state.zoom;

  const { tl, tr, br, bl } = geometry.corners;

  // Use the editor accent instead of white so the frame remains distinct
  // from both the canvas and the ruler/UI chrome.
  const drawRectOutline = () => {
    ctx.beginPath();
    ctx.moveTo(tl.x, tl.y);
    ctx.lineTo(tr.x, tr.y);
    ctx.lineTo(br.x, br.y);
    ctx.lineTo(bl.x, bl.y);
    ctx.closePath();
    ctx.stroke();
  };
  ctx.lineWidth = 3 / state.zoom;
  ctx.strokeStyle = 'rgba(0, 0, 0, 0.82)';
  ctx.setLineDash([6 / state.zoom, 4 / state.zoom]);
  ctx.lineDashOffset = 1 / state.zoom;
  drawRectOutline();
  ctx.lineWidth = 1.5 / state.zoom;
  ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#e06c75';
  ctx.lineDashOffset = 0;
  drawRectOutline();
  ctx.setLineDash([]);

  // Resize handles + rotation knob anchored to the rotated layer's
  // top-center (not bbox top), so the knob stays attached to the
  // visible content as it spins.
  const topHandle = geometry.resizeHandles.find(handle => handle.id === 't');
  if (!rotInside) {
    ctx.beginPath();
    ctx.moveTo(topHandle.x, topHandle.y);
    ctx.lineTo(geometry.rotationHandle.x, geometry.rotationHandle.y);
    ctx.strokeStyle = accent;
    ctx.lineWidth = 1 / state.zoom;
    ctx.stroke();
  }
  for (const c of geometry.handles) {
    const active = c.id === state.transformHandle;
    const hovered = !active && c.id === state.hoveredHandle;
    const edge = c.id.length === 1 && c.id !== 'rot';
    const baseRadius = edge ? sz * 0.42 : sz / 2;
    const radius = active ? sz * 0.75 : hovered ? sz * 0.6 : baseRadius;
    ctx.beginPath();
    ctx.arc(c.x, c.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = active ? '#fff' : hovered ? '#ffd' : accent;
    ctx.fill();
    ctx.lineWidth = stroke;
    ctx.strokeStyle = active ? accent : 'rgba(0, 0, 0, 0.72)';
    ctx.stroke();
    if (hovered) {
      // Subtle red ring around the hovered handle for visual feedback.
      ctx.beginPath();
      ctx.arc(c.x, c.y, radius + 2 / state.zoom, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(224, 108, 117, 0.7)';
      ctx.lineWidth = stroke;
      ctx.stroke();
    }
  }
  // The center marker communicates the rotation pivot without becoming an
  // interactive handle until pivot dragging lands in the transform workflow.
  const pivotRadius = 3 / state.zoom;
  ctx.beginPath();
  ctx.arc(geometry.pivot.x, geometry.pivot.y, pivotRadius, 0, Math.PI * 2);
  ctx.strokeStyle = accent;
  ctx.lineWidth = 1 / state.zoom;
  ctx.stroke();
  ctx.restore();
}


/**
 * Hit-test (x, y) against the transform handles. Returns the handle
 * id ('tl' | 't' | 'tr' | 'r' | 'br' | 'b' | 'bl' | 'l' | 'rot') or null.
 *
 * Geometry MUST mirror `drawHandles` exactly, otherwise the user
 * grabs phantom points.
 */
export function getHandleAt(x, y, options = {}) {
  if (state.transformTarget !== 'selection' && !state.transformLayer) return null;
  const frame = transformFrame();
  if (!frame) return null;
  const rotationInside = rotationHandleInside(frame);
  const geometry = transformFrameGeometry(frame, {
    zoom: state.zoom,
    rotationInside,
  });
  const hit = hitTestTransformHandle(geometry, x, y, {
    zoom: state.zoom,
    pointerType: options.pointerType,
  });
  if (hit || !rotationInside) return hit;

  // Keep the clipped grip reachable during viewport-edge transitions. The
  // frame is drawn with the inward position, but a touch can begin from the
  // previously rendered outward position for one frame after a resize/pan.
  const unclippedGeometry = transformFrameGeometry(frame, {
    zoom: state.zoom,
    rotationInside: false,
  });
  return hitTestTransformHandle(unclippedGeometry, x, y, {
    zoom: state.zoom,
    pointerType: options.pointerType,
  });
}

/** True only for the visible rotated frame, not its empty AABB corners. */
export function containsFramePoint(x, y) {
  const frame = transformFrame();
  if (!frame) return false;
  return pointInTransformFrame(transformFrameGeometry(frame, { zoom: state.zoom }), x, y);
}
