/** Rulers, grid, and document-space guides. These are viewport overlays only. */
import { state } from './state.js';

export const RULER_SIZE = 18;

export function chooseRulerStep(zoom, minimumPixels = 48) {
  const safeZoom = Math.max(0.0001, Number(zoom) || 1);
  const target = minimumPixels / safeZoom;
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(1, target)));
  for (const factor of [1, 2, 5, 10]) {
    const step = magnitude * factor;
    if (step >= target) return step;
  }
  return magnitude * 10;
}

export function pointerToDocument(clientX, clientY, canvasRect, width, height) {
  const scaleX = canvasRect.width / Math.max(1, width);
  const scaleY = canvasRect.height / Math.max(1, height);
  return {
    x: (clientX - canvasRect.left) / Math.max(scaleX, 0.0001),
    y: (clientY - canvasRect.top) / Math.max(scaleY, 0.0001),
  };
}

function normalizedGuides() {
  if (!state.guides || typeof state.guides !== 'object') state.guides = { vertical: [], horizontal: [] };
  state.guides.vertical = Array.isArray(state.guides.vertical) ? state.guides.vertical : [];
  state.guides.horizontal = Array.isArray(state.guides.horizontal) ? state.guides.horizontal : [];
  return state.guides;
}

export function createPrecisionGuides({ canvasArea, composite, saveState, schedulePersist }) {
  const corner = document.createElement('div');
  corner.className = 'ge-ruler-corner';
  const horizontal = document.createElement('canvas');
  horizontal.className = 'ge-ruler ge-ruler-horizontal';
  horizontal.title = 'Drag down to add a horizontal guide';
  const vertical = document.createElement('canvas');
  vertical.className = 'ge-ruler ge-ruler-vertical';
  vertical.title = 'Drag right to add a vertical guide';
  canvasArea.append(corner, horizontal, vertical);

  let drag = null;
  let frame = null;

  function themeColors() {
    const styles = getComputedStyle(state.container || canvasArea);
    return {
      background: styles.getPropertyValue('--panel').trim() || '#111',
      foreground: styles.getPropertyValue('--fg').trim() || '#ddd',
      border: styles.getPropertyValue('--border').trim() || '#333',
    };
  }

  function prepareRuler(canvas) {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return { ctx, rect };
  }

  function drawRuler(canvas, orientation) {
    if (!state.rulersVisible || !state.mainCanvas) return;
    const { ctx, rect } = prepareRuler(canvas);
    const areaRect = canvasArea.getBoundingClientRect();
    const imageRect = state.mainCanvas.getBoundingClientRect();
    const colors = themeColors();
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.fillStyle = colors.background;
    ctx.fillRect(0, 0, rect.width, rect.height);
    ctx.strokeStyle = colors.border;
    ctx.beginPath();
    if (orientation === 'horizontal') {
      ctx.moveTo(0, rect.height - 0.5); ctx.lineTo(rect.width, rect.height - 0.5);
    } else {
      ctx.moveTo(rect.width - 0.5, 0); ctx.lineTo(rect.width - 0.5, rect.height);
    }
    ctx.stroke();

    const scale = orientation === 'horizontal'
      ? imageRect.width / Math.max(1, state.imgWidth)
      : imageRect.height / Math.max(1, state.imgHeight);
    const origin = orientation === 'horizontal'
      ? imageRect.left - areaRect.left - RULER_SIZE
      : imageRect.top - areaRect.top - RULER_SIZE;
    const limit = orientation === 'horizontal' ? state.imgWidth : state.imgHeight;
    const major = chooseRulerStep(scale);
    const minor = major / 5;
    ctx.strokeStyle = colors.foreground;
    ctx.fillStyle = colors.foreground;
    ctx.globalAlpha = 0.62;
    ctx.lineWidth = 1;
    ctx.font = '8px ui-monospace, monospace';
    ctx.textBaseline = 'top';
    for (let value = 0; value <= limit + 0.001; value += minor) {
      const pixel = origin + value * scale;
      const rulerLimit = orientation === 'horizontal' ? rect.width : rect.height;
      if (pixel < -2 || pixel > rulerLimit + 2) continue;
      const isMajor = Math.round(value / minor) % 5 === 0;
      if (orientation === 'horizontal') {
        ctx.beginPath();
        ctx.moveTo(Math.round(pixel) + 0.5, rect.height);
        ctx.lineTo(Math.round(pixel) + 0.5, rect.height - (isMajor ? 9 : 4));
        ctx.stroke();
        if (isMajor) ctx.fillText(String(Math.round(value)), pixel + 2, 1);
      } else {
        ctx.beginPath();
        ctx.moveTo(rect.width, Math.round(pixel) + 0.5);
        ctx.lineTo(rect.width - (isMajor ? 9 : 4), Math.round(pixel) + 0.5);
        ctx.stroke();
        if (isMajor) {
          ctx.save();
          ctx.translate(1, pixel - 2);
          ctx.rotate(-Math.PI / 2);
          ctx.fillText(String(Math.round(value)), 0, 0);
          ctx.restore();
        }
      }
    }
    ctx.globalAlpha = 1;
  }

  function redrawRulers() {
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = null;
      canvasArea.classList.toggle('ge-rulers-visible', !!state.rulersVisible);
      corner.hidden = !state.rulersVisible;
      horizontal.hidden = !state.rulersVisible;
      vertical.hidden = !state.rulersVisible;
      if (state.rulersVisible) {
        drawRuler(horizontal, 'horizontal');
        drawRuler(vertical, 'vertical');
      }
    });
  }

  function drawDocumentOverlay(ctx) {
    if (!ctx || !state.imgWidth || !state.imgHeight) return;
    ctx.save();
    if (state.gridVisible) {
      const base = Math.max(2, Number(state.gridSize) || 16);
      const multiplier = Math.max(1, Math.ceil(5 / Math.max(0.0001, base * state.zoom)));
      const spacing = base * multiplier;
      ctx.lineWidth = 1 / state.zoom;
      for (let x = spacing; x < state.imgWidth; x += spacing) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, state.imgHeight);
        ctx.strokeStyle = Math.round(x / spacing) % 4 === 0
          ? 'rgba(110, 180, 220, 0.42)' : 'rgba(110, 180, 220, 0.22)';
        ctx.stroke();
      }
      for (let y = spacing; y < state.imgHeight; y += spacing) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(state.imgWidth, y);
        ctx.strokeStyle = Math.round(y / spacing) % 4 === 0
          ? 'rgba(110, 180, 220, 0.42)' : 'rgba(110, 180, 220, 0.22)';
        ctx.stroke();
      }
    }
    const guides = normalizedGuides();
    ctx.strokeStyle = 'rgba(78, 190, 255, 0.92)';
    ctx.lineWidth = 1 / state.zoom;
    ctx.setLineDash([]);
    for (const x of guides.vertical) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, state.imgHeight); ctx.stroke();
    }
    for (const y of guides.horizontal) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(state.imgWidth, y); ctx.stroke();
    }
    if (drag?.moved && Number.isFinite(drag.position)) {
      ctx.strokeStyle = 'rgba(120, 215, 255, 1)';
      if (drag.orientation === 'vertical') {
        ctx.beginPath(); ctx.moveTo(drag.position, 0); ctx.lineTo(drag.position, state.imgHeight); ctx.stroke();
      } else {
        ctx.beginPath(); ctx.moveTo(0, drag.position); ctx.lineTo(state.imgWidth, drag.position); ctx.stroke();
      }
    }
    ctx.restore();
  }

  function updateDrag(event) {
    if (!drag || !state.mainCanvas) return;
    const point = pointerToDocument(
      event.clientX, event.clientY,
      state.mainCanvas.getBoundingClientRect(),
      state.imgWidth, state.imgHeight,
    );
    const delta = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    drag.moved = drag.moved || delta > 3;
    drag.position = drag.orientation === 'vertical'
      ? Math.max(0, Math.min(state.imgWidth, Math.round(point.x)))
      : Math.max(0, Math.min(state.imgHeight, Math.round(point.y)));
    if (drag.moved) composite();
  }

  function beginGuide(event, orientation) {
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    drag = { orientation, startX: event.clientX, startY: event.clientY, moved: false, position: 0 };
    const move = next => updateDrag(next);
    const cancel = () => finish(false);
    const finish = (commit, next) => {
      if (next) updateDrag(next);
      if (commit && drag?.moved) {
        const values = normalizedGuides()[drag.orientation];
        if (!values.some(value => Math.abs(value - drag.position) < 0.5)) {
          saveState?.('Add guide');
          values.push(drag.position);
          values.sort((a, b) => a - b);
          schedulePersist?.();
        }
      }
      drag = null;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', cancel);
      composite();
      redrawRulers();
    };
    const end = next => finish(true, next);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', cancel);
  }

  horizontal.addEventListener('pointerdown', event => beginGuide(event, 'horizontal'));
  vertical.addEventListener('pointerdown', event => beginGuide(event, 'vertical'));
  canvasArea.addEventListener('scroll', redrawRulers, { passive: true });
  const observer = window.ResizeObserver ? new ResizeObserver(redrawRulers) : null;
  observer?.observe(canvasArea);
  redrawRulers();

  return {
    drawDocumentOverlay,
    redrawRulers,
    syncVisibility() { redrawRulers(); composite(); },
    clearGuides() {
      const guides = normalizedGuides();
      if (!guides.vertical.length && !guides.horizontal.length) return;
      saveState?.('Clear guides');
      state.guides = { vertical: [], horizontal: [] };
      schedulePersist?.();
      composite();
      redrawRulers();
    },
  };
}
