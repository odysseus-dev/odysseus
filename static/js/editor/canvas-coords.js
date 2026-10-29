/**
 * Convert a pointer event's client coordinates into the canvas's
 * internal pixel coordinates, accounting for current display scale.
 *
 * Handles both mouse and the first finger of a touch event.
 *
 * @param {MouseEvent|TouchEvent} e
 * @param {HTMLCanvasElement} canvas
 * @returns {{x: number, y: number}}
 */
import { state } from './state.js';

let _cachedCanvas = null;
let _cachedRect = null;
let _cachedRectTime = 0;

export function invalidateCanvasCoordsCache() {
  _cachedCanvas = null;
  _cachedRect = null;
  _cachedRectTime = 0;
}

export function canvasCoords(e, canvas) {
  const now = performance.now();
  let rect;
  if (_cachedCanvas === canvas && _cachedRect && (state.drawing || (now - _cachedRectTime < 64))) {
    rect = _cachedRect;
  } else {
    rect = canvas.getBoundingClientRect();
    _cachedCanvas = canvas;
    _cachedRect = rect;
    _cachedRectTime = now;
  }
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const clientX = e.touches && e.touches.length ? e.touches[0].clientX : e.clientX;
  const clientY = e.touches && e.touches.length ? e.touches[0].clientY : e.clientY;
  return {
    x: (clientX - rect.left) * scaleX,
    y: (clientY - rect.top) * scaleY,
  };
}
