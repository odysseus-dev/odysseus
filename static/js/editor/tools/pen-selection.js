import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';
import { selectionModeForEvent } from '../selection-modifiers.js';

export function createPenSelectionTool({ activeLayer, saveState, commitSelectionMask, composite, syncSelectionUi }) {
  let points = [], dragging = false, owner = null, canvas = null, mode = 'replace';
  const current = () => canvas === state.mainCanvas && state.tool === 'pen' && owner === activeLayer();
  function cancel() {
    const hadPath = points.length > 0;
    points = []; dragging = false; owner = canvas = null;
    if (hadPath) composite();
    return hadPath;
  }
  function trace(ctx, closed) {
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length + (closed ? 1 : 0); i++) {
      const a = points[i - 1], b = points[i % points.length];
      ctx.bezierCurveTo(a.x + a.dx, a.y + a.dy, b.x - b.dx, b.y - b.dy, b.x, b.y);
    }
    if (closed) ctx.closePath();
  }
  function commit() {
    if (!current() || points.length < 3) return false;
    const mask = document.createElement('canvas');
    mask.width = state.imgWidth; mask.height = state.imgHeight;
    const ctx = mask.getContext('2d');
    trace(ctx, true);
    ctx.fillStyle = '#fff'; ctx.fill();
    saveState('Pen selection');
    commitSelectionMask(mask, owner, mode, 'pen', { x: 0, y: 0 });
    state.wandMaskVisible = true;
    state.wandLastSeed = null;
    state.lassoPoints = []; state.lassoActive = false;
    cancel(); syncSelectionUi();
    return true;
  }
  return {
    cancel, commit,
    begin(event) {
      if (points.length && !current()) cancel();
      if (!activeLayer()) return;
      event.preventDefault();
      const p = canvasCoords(event, state.mainCanvas);
      if (points.length >= 3 && Math.hypot(p.x - points[0].x, p.y - points[0].y) * state.zoom < 9) {
        commit(); return;
      }
      if (!points.length) {
        owner = activeLayer(); canvas = state.mainCanvas;
        mode = selectionModeForEvent(event, state.wandMode || 'replace');
      }
      points.push({ ...p, dx: 0, dy: 0 }); dragging = true;
      composite();
    },
    drag(event) {
      if (!dragging || !current()) return;
      event.preventDefault();
      const p = canvasCoords(event, state.mainCanvas), anchor = points.at(-1);
      anchor.dx = p.x - anchor.x; anchor.dy = p.y - anchor.y;
      composite();
    },
    end() { dragging = false; },
    key(event) {
      if (!points.length || !current()) return false;
      if (event.key === 'Escape') cancel();
      else if (event.key === 'Enter') commit();
      else if (event.key === 'Backspace' || event.key === 'Delete' || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z')) {
        points.pop(); dragging = false; composite();
      } else return false;
      event.preventDefault(); event.stopImmediatePropagation(); return true;
    },
    draw() {
      if (!points.length || !current()) return;
      const ctx = state.mainCtx, unit = 1 / state.zoom;
      ctx.save();
      trace(ctx, false);
      ctx.strokeStyle = '#000'; ctx.lineWidth = 3 * unit; ctx.stroke();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = unit; ctx.stroke();
      for (const p of points) {
        if (p.dx || p.dy) {
          ctx.beginPath(); ctx.moveTo(p.x - p.dx, p.y - p.dy); ctx.lineTo(p.x + p.dx, p.y + p.dy); ctx.stroke();
          for (const sign of [-1, 1]) {
            ctx.beginPath(); ctx.arc(p.x + sign * p.dx, p.y + sign * p.dy, 2.5 * unit, 0, Math.PI * 2);
            ctx.fillStyle = '#fff'; ctx.fill();
          }
        }
        ctx.fillStyle = '#fff'; ctx.fillRect(p.x - 3 * unit, p.y - 3 * unit, 6 * unit, 6 * unit);
        ctx.strokeStyle = '#000'; ctx.strokeRect(p.x - 3 * unit, p.y - 3 * unit, 6 * unit, 6 * unit);
        ctx.strokeStyle = '#fff';
      }
      ctx.restore();
    },
  };
}
