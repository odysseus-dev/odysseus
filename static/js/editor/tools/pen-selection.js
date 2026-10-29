import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';
import { selectionModeForEvent } from '../selection-modifiers.js';

export function createPenSelectionTool({ activeLayer, saveState, commitSelectionMask, composite, syncSelectionUi, redraw, refreshSelection }) {
  let points = [], dragging = false, owner = null, canvas = null, mode = 'replace';
  const current = () => canvas === state.mainCanvas && state.tool === 'pen' && owner === activeLayer();
  // The in-progress path lives on its own overlay canvas, so repainting it must
  // not re-composite the whole document (that is what made every pen click flash
  // the entire canvas). Fall back to a composite only if no overlay hook exists.
  const repaint = () => { if (typeof redraw === 'function') redraw(); else composite(); };
  function cancel() {
    const hadPath = points.length > 0;
    points = []; dragging = false; owner = canvas = null;
    if (hadPath) repaint();
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
    saveState('Pen selection', { selectionOnly: true });
    commitSelectionMask(mask, owner, mode, 'pen', { x: 0, y: 0 });
    state.wandMaskVisible = true;
    state.wandLastSeed = null;
    state.lassoPoints = []; state.lassoActive = false;
    // Only the selection changed: repainting the marching ants is enough, so
    // skip the full document composite (which is the most expensive part of
    // closing a path on a large canvas).
    cancel();
    if (typeof refreshSelection === 'function') refreshSelection();
    else composite();
    syncSelectionUi();
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
      repaint();
    },
    drag(event) {
      if (!dragging || !current()) return;
      event.preventDefault();
      const p = canvasCoords(event, state.mainCanvas), anchor = points.at(-1);
      anchor.dx = p.x - anchor.x; anchor.dy = p.y - anchor.y;
      repaint();
    },
    end() { dragging = false; },
    key(event) {
      if (!points.length || !current()) return false;
      if (event.key === 'Escape') cancel();
      else if (event.key === 'Enter') commit();
      else if (event.key === 'Backspace' || event.key === 'Delete' || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z')) {
        points.pop(); dragging = false; repaint();
      } else return false;
      event.preventDefault(); event.stopImmediatePropagation(); return true;
    },
    draw() {
      const overlay = state.penOverlay, ctx = state.penOverlayCtx;
      if (!overlay || !ctx) return;
      if (!points.length || !current()) {
        if (overlay.style.display !== 'none') {
          overlay.style.display = 'none';
          ctx.setTransform(1, 0, 0, 1, 0, 0);
          ctx.clearRect(0, 0, overlay.width, overlay.height);
        }
        return;
      }
      const main = state.mainCanvas;
      const w = Math.max(1, Math.round(state.imgWidth * state.zoom));
      const h = Math.max(1, Math.round(state.imgHeight * state.zoom));
      if (overlay.width !== w || overlay.height !== h) { overlay.width = w; overlay.height = h; }
      overlay.style.width = w + 'px';
      overlay.style.height = h + 'px';
      overlay.style.left = main.offsetLeft + 'px';
      overlay.style.top = main.offsetTop + 'px';
      overlay.style.transform = main.style.transform || '';
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, w, h);
      // The stylesheet hides this overlay by default, so clearing the inline
      // value would leave it at display:none; show it explicitly.
      overlay.style.display = 'block';
      const unit = 1 / state.zoom;
      ctx.save();
      ctx.scale(state.zoom, state.zoom);
      trace(ctx, false);
      ctx.strokeStyle = '#000'; ctx.lineWidth = 3 * unit; ctx.stroke();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = unit; ctx.stroke();
      // Photoshop only shows the bezier handles of the anchor being shaped.
      // Drawing them for every point turned the preview into a mesh of long
      // straight lines across the shape.
      const shaped = dragging ? points.length - 1 : -1;
      for (let i = 0; i < points.length; i += 1) {
        const p = points[i];
        if (i === shaped && (p.dx || p.dy)) {
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
      // Ring the starting anchor once the path can be closed, so it reads as
      // "click here to close" instead of just another point.
      if (points.length >= 3) {
        const first = points[0];
        ctx.beginPath(); ctx.arc(first.x, first.y, 6 * unit, 0, Math.PI * 2);
        ctx.strokeStyle = '#000'; ctx.lineWidth = 3 * unit; ctx.stroke();
        ctx.strokeStyle = '#fff'; ctx.lineWidth = unit; ctx.stroke();
        ctx.strokeStyle = '#fff';
      }
      ctx.restore();
    },
  };
}
