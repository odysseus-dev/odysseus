/**
 * Perspective Crop tool — overlay four draggable corner handles over the
 * canvas; on Apply the quadrilateral is straightened (and resized) by the pure
 * engine in ../perspective-crop.js. The overlay is plain DOM (fixed-position
 * handles + an SVG outline) so it does not touch the canvas draw pipeline.
 */
import { state } from '../state.js';

const HANDLE = 14;

function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

export function createPerspectiveCropTool({ getCanvas, onApply, showToast = () => {} } = {}) {
  let overlay = null;
  let corners = null; // document-space {x,y} x4 (TL,TR,BR,BL)
  let dragging = -1;

  function canvasRect() {
    const canvas = getCanvas?.();
    const r = canvas?.getBoundingClientRect?.();
    return r && r.width > 0 ? r : null;
  }

  function toDisplay(point, rect) {
    return { x: rect.left + (point.x / state.imgWidth) * rect.width, y: rect.top + (point.y / state.imgHeight) * rect.height };
  }

  function toDoc(clientX, clientY, rect) {
    return {
      x: clamp(((clientX - rect.left) / rect.width) * state.imgWidth, 0, state.imgWidth),
      y: clamp(((clientY - rect.top) / rect.height) * state.imgHeight, 0, state.imgHeight),
    };
  }

  function sizeHint() {
    const width = Math.round((Math.hypot(corners[1].x - corners[0].x, corners[1].y - corners[0].y) + Math.hypot(corners[2].x - corners[3].x, corners[2].y - corners[3].y)) / 2);
    const height = Math.round((Math.hypot(corners[3].x - corners[0].x, corners[3].y - corners[0].y) + Math.hypot(corners[2].x - corners[1].x, corners[2].y - corners[1].y)) / 2);
    return `${Math.max(1, width)} × ${Math.max(1, height)} px`;
  }

  function render() {
    if (!overlay) return;
    const rect = canvasRect();
    if (!rect) return;
    overlay.style.left = rect.left + 'px';
    overlay.style.top = rect.top + 'px';
    overlay.style.width = rect.width + 'px';
    overlay.style.height = rect.height + 'px';
    const pts = corners.map(c => toDisplay(c, rect));
    overlay.querySelector('polygon')?.setAttribute('points', pts.map(p => `${p.x - rect.left},${p.y - rect.top}`).join(' '));
    overlay.querySelectorAll('.ge-pcrop-handle').forEach((h, i) => {
      const p = pts[i];
      h.style.left = (p.x - rect.left) + 'px';
      h.style.top = (p.y - rect.top) + 'px';
    });
    const hint = overlay.querySelector('.ge-pcrop-size');
    if (hint) hint.textContent = sizeHint();
  }

  function onPointerDown(i, event) {
    event.preventDefault();
    event.stopPropagation();
    dragging = i;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    document.addEventListener('pointermove', onPointerMove, true);
    document.addEventListener('pointerup', onPointerUp, true);
  }

  function onPointerMove(event) {
    if (dragging < 0) return;
    const rect = canvasRect();
    if (!rect) return;
    corners[dragging] = toDoc(event.clientX, event.clientY, rect);
    render();
  }

  function onPointerUp() {
    dragging = -1;
    document.removeEventListener('pointermove', onPointerMove, true);
    document.removeEventListener('pointerup', onPointerUp, true);
  }

  function onKey(event) {
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    else if (event.key === 'Enter') { event.preventDefault(); apply(); }
  }

  function apply() {
    const next = corners.map(c => ({ ...c }));
    close();
    onApply?.(next);
  }

  function open() {
    close();
    const inset = 0.06;
    corners = [
      { x: state.imgWidth * inset, y: state.imgHeight * inset },
      { x: state.imgWidth * (1 - inset), y: state.imgHeight * inset },
      { x: state.imgWidth * (1 - inset), y: state.imgHeight * (1 - inset) },
      { x: state.imgWidth * inset, y: state.imgHeight * (1 - inset) },
    ];
    overlay = document.createElement('div');
    overlay.className = 'ge-pcrop-overlay';
    overlay.innerHTML = `
      <svg class="ge-pcrop-svg" preserveAspectRatio="none"><polygon points=""></polygon></svg>
      <div class="ge-pcrop-bar">
        <span class="ge-pcrop-title">Perspective Crop</span>
        <span class="ge-pcrop-size"></span>
        <span class="ge-pcrop-spacer"></span>
        <button type="button" class="ge-btn ge-btn-sm" data-pcrop="cancel">Cancel</button>
        <button type="button" class="ge-btn ge-btn-sm ge-btn-primary" data-pcrop="apply">Apply</button>
      </div>
      ${corners.map((_, i) => `<div class="ge-pcrop-handle" data-corner="${i}"></div>`).join('')}
    `;
    document.body.appendChild(overlay);
    overlay.querySelectorAll('.ge-pcrop-handle').forEach((handle, i) => {
      handle.addEventListener('pointerdown', (event) => onPointerDown(i, event));
    });
    overlay.querySelector('[data-pcrop="apply"]').addEventListener('click', apply);
    overlay.querySelector('[data-pcrop="cancel"]').addEventListener('click', close);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', render);
    render();
    showToast?.('Drag the four corners, then Apply');
  }

  function close() {
    if (!overlay) return;
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', render);
    document.removeEventListener('pointermove', onPointerMove, true);
    document.removeEventListener('pointerup', onPointerUp, true);
    overlay.remove();
    overlay = null;
    corners = null;
    dragging = -1;
  }

  return { open, close, isOpen: () => !!overlay, render };
}

export default { createPerspectiveCropTool };
