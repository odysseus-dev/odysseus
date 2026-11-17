import { state } from '../state.js';
import { canvasCoords } from '../canvas-coords.js';

/**
 * Liquify — Photoshop-style destructive warp brush for the active layer.
 *
 * Dragging samples the layer's pixels through a displacement field: Push
 * drags pixels with the pointer, Twirl rotates them around the dab, Pucker
 * pulls inward, Bloat pushes outward and Smooth softens the area. Each
 * stroke is one undo step (the snapshot is taken on press) and the layer is
 * re-composited at most once per animation frame, so the canvas shows the
 * warp live without re-rendering the document on every mouse event.
 */

const MODES = new Set(['push', 'twirl', 'pucker', 'bloat', 'smooth']);

export function createLiquifyTool({ activeLayer, saveState, composite }) {
  let layer = null, owner = null, last = null, live = false, raf = 0;

  const radius = () => Math.max(4, Math.min(1000, Number(state.liquifySize) || 120));
  const strength = () => Math.max(1, Math.min(100, Number(state.liquifyStrength) || 50)) / 100;
  const mode = () => (MODES.has(state.liquifyMode) ? state.liquifyMode : 'push');

  const requestComposite = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; composite(); });
  };

  // Pointer position in the active layer's own pixel space (layers can be
  // offset from the document origin).
  function layerPoint(event) {
    const p = canvasCoords(event, state.mainCanvas);
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    return { x: p.x - off.x, y: p.y - off.y };
  }

  function sampleBilinear(src, w, h, fx, fy, dst, di) {
    const x = Math.max(0, Math.min(w - 1, fx - 0.5));
    const y = Math.max(0, Math.min(h - 1, fy - 0.5));
    const x1 = Math.floor(x), y1 = Math.floor(y);
    const x2 = Math.min(w - 1, x1 + 1), y2 = Math.min(h - 1, y1 + 1);
    const tx = x - x1, ty = y - y1;
    const i11 = (y1 * w + x1) * 4, i21 = (y1 * w + x2) * 4;
    const i12 = (y2 * w + x1) * 4, i22 = (y2 * w + x2) * 4;
    const wx1 = 1 - tx, wy1 = 1 - ty, wx2 = tx, wy2 = ty;
    for (let c = 0; c < 4; c++) {
      dst[di + c] = src[i11 + c] * wx1 * wy1 + src[i21 + c] * wx2 * wy1
        + src[i12 + c] * wx1 * wy2 + src[i22 + c] * wx2 * wy2;
    }
  }

  function average3x3(src, w, h, px, py) {
    let r = 0, g = 0, b = 0, a = 0, n = 0;
    for (let oy = -1; oy <= 1; oy++) {
      for (let ox = -1; ox <= 1; ox++) {
        const x = Math.max(0, Math.min(w - 1, Math.round(px) + ox));
        const y = Math.max(0, Math.min(h - 1, Math.round(py) + oy));
        const i = (y * w + x) * 4;
        r += src[i]; g += src[i + 1]; b += src[i + 2]; a += src[i + 3]; n++;
      }
    }
    return { r: r / n, g: g / n, b: b / n, a: a / n };
  }

  function dab(cx, cy, dx, dy, spin) {
    const canvas = layer.canvas, ctx = layer.ctx;
    const w = canvas.width, h = canvas.height;
    const r = radius();
    const x0 = Math.max(0, Math.floor(cx - r - 1));
    const y0 = Math.max(0, Math.floor(cy - r - 1));
    const x1 = Math.min(w, Math.ceil(cx + r + 1));
    const y1 = Math.min(h, Math.ceil(cy + r + 1));
    const bw = x1 - x0, bh = y1 - y0;
    if (bw <= 0 || bh <= 0) return;

    const img = ctx.getImageData(x0, y0, bw, bh);
    const out = ctx.createImageData(bw, bh);
    const src = img.data, dst = out.data;
    const m = mode(), s = strength();

    for (let y = 0; y < bh; y++) {
      for (let x = 0; x < bw; x++) {
        const di = (y * bw + x) * 4;
        const px = x0 + x + 0.5, py = y0 + y + 0.5;
        const ox = px - cx, oy = py - cy;
        const dist = Math.hypot(ox, oy);
        if (dist >= r) {
          dst[di] = src[di]; dst[di + 1] = src[di + 1]; dst[di + 2] = src[di + 2]; dst[di + 3] = src[di + 3];
          continue;
        }
        const t = 1 - dist / r;
        const fall = t * t * (3 - 2 * t);
        let sx = px, sy = py;
        if (m === 'push') {
          sx = px - dx * fall * s;
          sy = py - dy * fall * s;
        } else if (m === 'twirl') {
          const ang = spin * s * 2.6 * fall * fall;
          const ca = Math.cos(ang), sa = Math.sin(ang);
          sx = cx + ox * ca - oy * sa;
          sy = cy + ox * sa + oy * ca;
        } else if (m === 'pucker') {
          const k = 1 - 0.55 * s * fall;
          sx = cx + ox * k;
          sy = cy + oy * k;
        } else if (m === 'bloat') {
          const k = 1 + 0.55 * s * fall;
          sx = cx + ox * k;
          sy = cy + oy * k;
        } else if (m === 'smooth') {
          const avg = average3x3(src, bw, bh, px - x0, py - y0);
          const k = fall * s * 0.9;
          dst[di] = src[di] * (1 - k) + avg.r * k;
          dst[di + 1] = src[di + 1] * (1 - k) + avg.g * k;
          dst[di + 2] = src[di + 2] * (1 - k) + avg.b * k;
          dst[di + 3] = src[di + 3] * (1 - k) + avg.a * k;
          continue;
        }
        sampleBilinear(src, bw, bh, sx - x0, sy - y0, dst, di);
      }
    }
    ctx.putImageData(out, x0, y0);
  }

  function strokeTo(point, previous) {
    const dx = point.x - previous.x, dy = point.y - previous.y;
    const distance = Math.hypot(dx, dy);
    // Twirl direction: the pointer's motion around the dab decides clockwise
    // vs counter-clockwise, matching how Photoshop's twirl brush feels.
    const ox = previous.x - point.x, oy = previous.y - point.y;
    const spin = (ox * dy - oy * dx) >= 0 ? 1 : -1;
    const step = Math.max(1, radius() * 0.25);
    const steps = Math.max(1, Math.ceil(distance / step));
    for (let i = 1; i <= steps; i++) {
      dab(previous.x + (dx * i) / steps, previous.y + (dy * i) / steps, dx / steps, dy / steps, spin);
    }
    requestComposite();
  }

  return {
    begin(event) {
      const target = activeLayer();
      if (!target || !target.canvas || !target.ctx) return;
      if (event.button != null && event.button !== 0) return;
      event.preventDefault();
      owner = target;
      layer = target;
      saveState('Liquify');
      last = layerPoint(event);
      live = true;
      // A single click still warps (twirl/pucker/bloat do something useful
      // on press); Push needs movement, so it is a no-op until the drag.
      if (mode() !== 'push') { dab(last.x, last.y, 0, 0, 1); requestComposite(); }
      state.drawing = true;
    },
    drag(event) {
      if (!live || !layer || owner !== activeLayer() || state.tool !== 'liquify') return;
      event.preventDefault();
      const point = layerPoint(event);
      strokeTo(point, last);
      last = point;
    },
    end() {
      if (!live) return;
      live = false;
      last = null;
      owner = null;
      state.drawing = false;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      composite();
    },
    cancel() {
      live = false; last = null; owner = null; state.drawing = false;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
    },
  };
}
