/**
 * Stroke pipeline — paints one segment (last-position → current
 * position) onto the active layer (or its active mask sub-layer).
 *
 * `strokeTo` dispatches by tool:
 *   - clone  → cloneStrokeTo (custom stamp-based paint loop)
 *   - brush  → source-over with opacity × flow + softness blur
 *   - eraser → destination-out with opacity × flow + softness blur
 *   - inpaint → source-over (paint) or destination-out (erase) with
 *               full alpha on the mask canvas
 *
 * If the active parent has an active mask sub-layer, brush / eraser /
 * inpaint target the mask canvas instead of the layer's pixel canvas.
 *
 * @param {{
 *   activeLayer:          () => object | null,
 *   getActiveMaskLayer:   () => object | null,
 *   composite:            () => void,
 * }} deps
 */
import { state } from './state.js';
import { ensureRembgSampleCanvas } from './rembg-sample-mask.js';

export function createStrokePipeline({ activeLayer, getActiveMaskLayer, composite }) {
  function cloneStrokeTo(x, y, layer) {
    if (!state.cloneSourceSnapshot) return;
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const dx = x - state.cloneStrokeStartX;
    const dy = y - state.cloneStrokeStartY;
    const srcX = state.cloneSourceX + dx;
    const srcY = state.cloneSourceY + dy;
    const ctx = layer.ctx;
    const radius = Math.max(1, state.brushSize / 2);
    // Walk last → current in roughly half-brush steps so stamps
    // overlap into a continuous brush trail.
    const lastSrcX = state.cloneSourceX + (state.lastX - state.cloneStrokeStartX);
    const lastSrcY = state.cloneSourceY + (state.lastY - state.cloneStrokeStartY);
    const dist = Math.hypot(x - state.lastX, y - state.lastY);
    const step = Math.max(1, radius * 0.5);
    const steps = Math.max(1, Math.ceil(dist / step));
    const stampSize = Math.max(2, Math.ceil(radius * 2));
    const stampRadius = stampSize / 2;
    const stamp = document.createElement('canvas');
    stamp.width = stampSize;
    stamp.height = stampSize;
    const stampCtx = stamp.getContext('2d');
    const softness = Math.max(0, Math.min(1, state.cloneSoftness / 300));
    const hardStop = stampRadius * (1 - softness);
    ctx.save();
    ctx.globalAlpha = (state.cloneOpacity / 100) * (state.cloneFlow / 100);
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      const px = state.lastX + (x - state.lastX) * t - off.x;
      const py = state.lastY + (y - state.lastY) * t - off.y;
      const sx = lastSrcX + (srcX - lastSrcX) * t;
      const sy = lastSrcY + (srcY - lastSrcY) * t;
      stampCtx.clearRect(0, 0, stampSize, stampSize);
      stampCtx.globalCompositeOperation = 'source-over';
      stampCtx.drawImage(
        state.cloneSourceSnapshot,
        sx - stampRadius, sy - stampRadius, stampSize, stampSize,
        0, 0, stampSize, stampSize,
      );
      stampCtx.globalCompositeOperation = 'destination-in';
      const mask = stampCtx.createRadialGradient(stampRadius, stampRadius, hardStop, stampRadius, stampRadius, stampRadius);
      mask.addColorStop(0, 'rgba(0,0,0,1)');
      mask.addColorStop(1, 'rgba(0,0,0,0)');
      stampCtx.fillStyle = mask;
      stampCtx.fillRect(0, 0, stampSize, stampSize);
      ctx.drawImage(stamp, px - stampRadius, py - stampRadius);
    }
    ctx.restore();
    state.lastX = x;
    state.lastY = y;
    composite();
  }

  function rembgSampleStrokeTo(x, y) {
    const c = ensureRembgSampleCanvas();
    const ctx = state.rembgSampleCtx || c.getContext('2d');
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.lineWidth = state.brushSize;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = 'rgba(255,255,255,1)';
    ctx.beginPath();
    ctx.moveTo(state.lastX, state.lastY);
    ctx.lineTo(x, y);
    ctx.stroke();
    ctx.restore();
    state.lastX = x;
    state.lastY = y;
    composite();
  }

  function strokeTo(x, y) {
    if (state.tool === 'rembg') return rembgSampleStrokeTo(x, y);
    const layer = activeLayer();
    if (!layer) return;
    // Clone uses a stamp-based paint loop, not the line-stroke
    // pipeline below.
    if (state.tool === 'clone') return cloneStrokeTo(x, y, layer);

    // If the active parent has an active mask sub-layer, brush /
    // eraser / inpaint paint the mask canvas instead of the layer's
    // pixel canvas. Brush adds to the mask, Eraser carves it away,
    // Inpaint still works (its mask plumbing was already pointed at
    // the same canvas).
    const activeMask = getActiveMaskLayer();
    const paintingMask = !!activeMask &&
      (state.tool === 'brush' || state.tool === 'eraser' || state.tool === 'inpaint');
    const ctx = paintingMask
      ? activeMask.ctx
      : (state.tool === 'inpaint' ? state.maskCtx : layer.ctx);
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };

    ctx.save();
    ctx.lineWidth = state.brushSize;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    if (state.tool === 'eraser') {
      ctx.globalCompositeOperation = 'destination-out';
      // Effective alpha = opacity × flow. Opacity = max strength a
      // stroke can reach; flow = how much erases per pass.
      ctx.globalAlpha = (state.eraserOpacity / 100) * (state.eraserFlow / 100);
      ctx.strokeStyle = 'rgba(0,0,0,1)';
      if (state.eraserSoftness > 0) {
        const blurPx = (state.eraserSoftness / 100) * (state.brushSize / 2);
        ctx.filter = `blur(${blurPx.toFixed(2)}px)`;
      }
    } else if (state.tool === 'brush') {
      // Brush — state.color onto the layer (or white onto an active
      // mask sub-layer). Mask painting forces full alpha so masks
      // stay a clean binary by default (a sub-100% brush would
      // silently paint partial-strength mask pixels).
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = paintingMask ? 'rgba(255,255,255,1)' : state.color;
      if (paintingMask) {
        ctx.globalAlpha = 1;
      } else {
        ctx.globalAlpha = (state.brushOpacity / 100) * (state.brushFlow / 100);
        if (state.brushSoftness > 0) {
          const blurPx = (state.brushSoftness / 100) * (state.brushSize / 2);
          ctx.filter = `blur(${blurPx.toFixed(2)}px)`;
        }
      }
    } else if (state.tool === 'inpaint') {
      if (state.inpaintEraseStroke) {
        ctx.globalCompositeOperation = 'destination-out';
        ctx.strokeStyle = 'rgba(0,0,0,1)';
      } else {
        ctx.globalCompositeOperation = 'source-over';
        // Diffusion server expects white = inpaint area. The red
        // overlay is rendered separately in composite() for the user.
        ctx.strokeStyle = 'rgba(255,255,255,1)';
      }
    } else {
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = state.color;
    }

    // Mask canvases are always full-image (no per-layer offset), so
    // painting onto a mask uses canvas-coord origin too — same as
    // inpaint.
    const onMaskOrInpaint = paintingMask || state.tool === 'inpaint';
    const drawX = onMaskOrInpaint ? 0 : off.x;
    const drawY = onMaskOrInpaint ? 0 : off.y;

    ctx.beginPath();
    ctx.moveTo(state.lastX - drawX, state.lastY - drawY);
    ctx.lineTo(x - drawX, y - drawY);
    ctx.stroke();
    ctx.restore();

    state.lastX = x;
    state.lastY = y;
    composite();
  }

  return { strokeTo, cloneStrokeTo };
}
