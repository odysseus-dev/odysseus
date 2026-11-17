/** Shared sampled renderer for brush, eraser, clone, masks, and retouch tools. */
import { state } from './state.js';
import { createBrushSampler, pressureValue } from './brush-engine.js';
import { isLayerTransparencyLocked } from './layer-groups.js';

const PAINT_TOOLS = new Set(['brush', 'pixel', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'inpaint']);

function toolSettings(tool) {
  if (tool === 'eraser') return { opacity: state.eraserOpacity, flow: state.eraserFlow, softness: state.eraserSoftness };
  if (tool === 'clone' || tool === 'heal') return { opacity: state.cloneOpacity, flow: state.cloneFlow, softness: state.cloneSoftness };
  if (tool === 'smudge') return { opacity: state.smudgeStrength, flow: state.brushFlow, softness: state.brushSoftness };
  return { opacity: state.brushOpacity, flow: state.brushFlow, softness: state.brushSoftness };
}

let _lastColor = null;
let _lastTransparentColor = null;
function transparentColor(color, alpha = 0) {
  const key = `${color}|${alpha}`;
  if (key === _lastColor && _lastTransparentColor) return _lastTransparentColor;
  _lastColor = key;
  _lastTransparentColor = colorWithAlpha(color, alpha);
  return _lastTransparentColor;
}

function colorWithAlpha(color, alpha) {
  if (color.startsWith('#')) {
    const raw = color.slice(1);
    const expanded = raw.length === 3 ? raw.split('').map(char => char + char).join('') : raw;
    const rgb = [0, 2, 4].map(index => parseInt(expanded.slice(index, index + 2), 16));
    return `rgba(${rgb.join(',')},${alpha})`;
  }
  const match = color.match(/rgba?\(([^)]+)\)/);
  if (!match) return `rgba(0,0,0,${alpha})`;
  const components = match[1].split(',');
  const originalAlpha = components.length > 3 ? Number(components[3]) : 1;
  return `rgba(${components.slice(0, 3).join(',')},${alpha * originalAlpha})`;
}

function circleStamp(ctx, x, y, radius, softness, color, alpha, operation) {
  const hard = Math.max(0, Math.min(1, 1 - softness));
  ctx.save();
  ctx.globalCompositeOperation = operation;
  ctx.globalAlpha = alpha;
  if (softness <= 0.001) {
    ctx.fillStyle = color;
  } else {
    const gradient = ctx.createRadialGradient(x, y, radius * hard, x, y, radius);
    if (softness > 1) {
      // Above the old maximum, remove the firm core with a progressively
      // gentler alpha profile while keeping the selected brush diameter.
      ctx.fillStyle = color;
      const normalized = ctx.fillStyle;
      for (let step = 0; step <= 16; step++) {
        const t = step / 16;
        gradient.addColorStop(t, transparentColor(normalized, (1 - t) ** softness));
      }
    } else {
      gradient.addColorStop(0, color);
      gradient.addColorStop(1, transparentColor(color));
    }
    ctx.fillStyle = gradient;
  }
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

// Crisp pixel brush: a hard square fillRect snapped to a grid whose cell is
// `size` px, so consecutive dabs land on the same lattice and connect cleanly.
function pixelDab(ctx, x, y, size, operation, color) {
  const s = Math.max(1, Math.round(size));
  const gx = Math.floor(x / s) * s;
  const gy = Math.floor(y / s) * s;
  ctx.save();
  ctx.globalCompositeOperation = operation;
  ctx.globalAlpha = 1;
  ctx.fillStyle = color;
  ctx.fillRect(gx, gy, s, s);
  ctx.restore();
}

// Snap a drag vector to the nearest of 8 compass directions (0/45/90...)
// so the pixel brush draws perfectly straight lines.
function snapDirection(dx, dy) {
  const step = Math.PI / 4;
  const snapped = Math.round(Math.atan2(dy, dx) / step) * step;
  return { x: Math.cos(snapped), y: Math.sin(snapped) };
}

// Connect two sampled points by walking whole grid cells (cell = `size` px)
// with a Bresenham line, so a fast drag fills every cell the line crosses and
// leaves no gaps and no half-cells.
function pixelLine(ctx, x0, y0, x1, y1, size, operation, color) {
  const s = Math.max(1, Math.round(size));
  let cx = Math.floor(Math.round(x0) / s);
  let cy = Math.floor(Math.round(y0) / s);
  const cx1 = Math.floor(Math.round(x1) / s);
  const cy1 = Math.floor(Math.round(y1) / s);
  const dx = Math.abs(cx1 - cx);
  const dy = -Math.abs(cy1 - cy);
  const sx = cx < cx1 ? 1 : -1;
  const sy = cy < cy1 ? 1 : -1;
  let err = dx + dy;
  ctx.save();
  ctx.globalCompositeOperation = operation;
  ctx.globalAlpha = 1;
  ctx.fillStyle = color;
  for (;;) {
    ctx.fillRect(cx * s, cy * s, s, s);
    if (cx === cx1 && cy === cy1) break;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; cx += sx; }
    if (e2 <= dx) { err += dx; cy += sy; }
  }
  ctx.restore();
}

export function createStrokePipeline({ activeLayer, getActiveMaskLayer, composite }) {
  let strokeTool = null;
  let compositeFrame = null;
  let smudgeBuffer = null;
  let pixelLast = null;
  let pixelAnchor = null;
  let pixelClickAnchor = null;

  // Pointer events can arrive much faster than the display refresh rate.
  // Stamps are applied synchronously, so only the visible document composite
  // needs coalescing while a stroke is in progress.
  function scheduleComposite() {
    if (compositeFrame !== null) return;
    if (typeof requestAnimationFrame === 'function') {
      compositeFrame = requestAnimationFrame(() => {
        compositeFrame = null;
        composite();
      });
    } else {
      compositeFrame = setTimeout(() => {
        compositeFrame = null;
        composite();
      }, 0);
    }
  }

  function flushComposite() {
    if (compositeFrame !== null) {
      if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(compositeFrame);
      else clearTimeout(compositeFrame);
      compositeFrame = null;
    }
    composite();
  }

  function targetFor(tool, layer) {
    const activeMask = state.quickMaskActive && state.wandMask
      ? { mode: 'quick-selection', canvas: state.wandMask, ctx: state.wandMask.getContext('2d') }
      : getActiveMaskLayer();
    const paintingMask = !!activeMask && ['brush', 'eraser', 'inpaint'].includes(tool);
    const ctx = paintingMask ? activeMask.ctx : (tool === 'inpaint' ? state.maskCtx : layer.ctx);
    const layerOff = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const documentMask = paintingMask && activeMask.mode !== 'layer';
    const maskOff = paintingMask && activeMask.mode === 'layer' ? (activeMask.offset || { x: 0, y: 0 }) : { x: 0, y: 0 };
    return {
      ctx,
      paintingMask,
      xOffset: (documentMask || tool === 'inpaint') ? 0 : layerOff.x + maskOff.x,
      yOffset: (documentMask || tool === 'inpaint') ? 0 : layerOff.y + maskOff.y,
    };
  }

  function cloneStamp(sample, layer, heal = false) {
    if (!state.cloneSourceSnapshot) return;
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const diameter = pressureValue(state.brushSize, sample.pressure, state.pressureSize);
    const radius = Math.max(0.5, diameter / 2);
    const settings = toolSettings(heal ? 'heal' : 'clone');
    const alpha = pressureValue(settings.opacity / 100, sample.pressure, state.pressureOpacity)
      * pressureValue(settings.flow / 100, sample.pressure, state.pressureFlow);
    const sx = state.cloneSourceX - (state.cloneSourceOffsetX || 0) + (sample.x - state.cloneStrokeStartX);
    const sy = state.cloneSourceY - (state.cloneSourceOffsetY || 0) + (sample.y - state.cloneStrokeStartY);
    const size = Math.max(2, Math.ceil(radius * 2));
    const stamp = document.createElement('canvas');
    stamp.width = size;
    stamp.height = size;
    const stampCtx = stamp.getContext('2d');
    stampCtx.drawImage(state.cloneSourceSnapshot, sx - radius, sy - radius, size, size, 0, 0, size, size);
    stampCtx.globalCompositeOperation = 'destination-in';
    const hard = Math.max(0, Math.min(1, 1 - settings.softness / 300));
    const mask = stampCtx.createRadialGradient(radius, radius, radius * hard, radius, radius, radius);
    mask.addColorStop(0, 'rgba(0,0,0,1)');
    mask.addColorStop(1, 'rgba(0,0,0,0)');
    stampCtx.fillStyle = mask;
    stampCtx.fillRect(0, 0, size, size);
    const ctx = layer.ctx;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.globalCompositeOperation = isLayerTransparencyLocked(state, layer) ? 'source-atop' : 'source-over';
    if (heal) ctx.filter = `blur(${Math.max(0.4, radius * 0.08).toFixed(2)}px)`;
    ctx.drawImage(stamp, sample.x - off.x - radius, sample.y - off.y - radius);
    ctx.restore();
  }

  function spotHealStamp(sample, layer) {
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const diameter = pressureValue(state.brushSize, sample.pressure, state.pressureSize);
    const radius = Math.max(0.5, diameter / 2);
    const settings = toolSettings('heal');
    const alpha = pressureValue(settings.opacity / 100, sample.pressure, state.pressureOpacity)
      * pressureValue(settings.flow / 100, sample.pressure, state.pressureFlow);
    const x = Math.round(sample.x - off.x);
    const y = Math.round(sample.y - off.y);
    const ring = Math.max(2, Math.ceil(radius * 2.2));
    const left = Math.max(0, x - ring);
    const top = Math.max(0, y - ring);
    const right = Math.min(layer.canvas.width - 1, x + ring);
    const bottom = Math.min(layer.canvas.height - 1, y + ring);
    if (right < left || bottom < top) return;
    const pixels = layer.ctx.getImageData(left, top, right - left + 1, bottom - top + 1).data;
    let red = 0, green = 0, blue = 0, alphaSum = 0, weightSum = 0;
    const exclusion = Math.max(1, radius * 0.72);
    for (let py = top; py <= bottom; py += 1) {
      for (let px = left; px <= right; px += 1) {
        const distance = Math.hypot(px - x, py - y);
        if (distance < exclusion || distance > ring) continue;
        const index = ((py - top) * (right - left + 1) + (px - left)) * 4;
        const weight = 1 / Math.max(1, distance);
        red += pixels[index] * weight;
        green += pixels[index + 1] * weight;
        blue += pixels[index + 2] * weight;
        alphaSum += pixels[index + 3] * weight;
        weightSum += weight;
      }
    }
    if (!weightSum) return;
    const color = `rgba(${Math.round(red / weightSum)},${Math.round(green / weightSum)},${Math.round(blue / weightSum)},${Math.max(0, Math.min(1, alphaSum / weightSum / 255))})`;
    circleStamp(layer.ctx, x, y, radius, Math.max(0, Math.min(1, settings.softness / 300)), color, alpha, 'source-over');
  }

  function smudgeStamp(sample, layer) {
    const previousX = Number.isFinite(state.lastX) ? state.lastX : sample.x;
    const previousY = Number.isFinite(state.lastY) ? state.lastY : sample.y;
    const distance = Math.hypot(sample.x - previousX, sample.y - previousY);
    if (distance < 0.25) return;
    const diameter = pressureValue(state.brushSize, sample.pressure, state.pressureSize);
    const radius = Math.max(0.5, diameter / 2);
    const settings = toolSettings('smudge');
    const alpha = pressureValue(settings.opacity / 100, sample.pressure, state.pressureOpacity)
      * pressureValue(settings.flow / 100, sample.pressure, state.pressureFlow);
    const size = Math.max(2, Math.ceil(radius * 2));
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    if (!smudgeBuffer || smudgeBuffer.width !== size || smudgeBuffer.height !== size) {
      smudgeBuffer = document.createElement('canvas');
      smudgeBuffer.width = size;
      smudgeBuffer.height = size;
      smudgeBuffer.getContext('2d').drawImage(
        layer.canvas,
        previousX - off.x - radius, previousY - off.y - radius, size, size,
        0, 0, size, size,
      );
      return;
    }
    const stamp = document.createElement('canvas');
    stamp.width = size;
    stamp.height = size;
    const stampCtx = stamp.getContext('2d');
    stampCtx.drawImage(smudgeBuffer, 0, 0);
    stampCtx.globalCompositeOperation = 'destination-in';
    const hard = Math.max(0, Math.min(1, 1 - settings.softness / 300));
    const mask = stampCtx.createRadialGradient(radius, radius, radius * hard, radius, radius, radius);
    mask.addColorStop(0, 'rgba(0,0,0,1)');
    mask.addColorStop(1, 'rgba(0,0,0,0)');
    stampCtx.fillStyle = mask;
    stampCtx.fillRect(0, 0, size, size);
    const ctx = layer.ctx;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.globalCompositeOperation = isLayerTransparencyLocked(state, layer) ? 'source-atop' : 'source-over';
    ctx.drawImage(stamp, sample.x - off.x - radius, sample.y - off.y - radius);
    ctx.restore();
    // Capture the newly mixed patch so the next sample carries the smear
    // forward instead of repeatedly sampling the untouched canvas.
    smudgeBuffer.getContext('2d').clearRect(0, 0, size, size);
    smudgeBuffer.getContext('2d').drawImage(
      layer.canvas,
      sample.x - off.x - radius, sample.y - off.y - radius, size, size,
      0, 0, size, size,
    );
  }

  function renderSample(sample) {
    const tool = strokeTool || state.tool;
    if (!PAINT_TOOLS.has(tool)) return;
    const layer = activeLayer();
    if (!layer) return;
    // Pixel writes invalidate adjusted previews even when adjustment settings
    // themselves have not changed (otherwise retouch strokes appear late).
    layer._adjFinalKey = null;
    layer._adjCacheKey = null;
    if (tool === 'clone') return cloneStamp(sample, layer);
    if (tool === 'heal') {
      // A healing stroke without a picked source uses the convenient local
      // spot correction. With a source snapshot, blend sampled texture into
      // the destination instead of silently falling back to ring averaging.
      return state.cloneSourceSnapshot
        ? cloneStamp(sample, layer, true)
        : spotHealStamp(sample, layer);
    }
    if (tool === 'smudge') return smudgeStamp(sample, layer);
    const target = targetFor(tool, layer);
    if (!target.ctx) return;
    const settings = toolSettings(tool);
    const diameter = pressureValue(state.brushSize, sample.pressure, state.pressureSize);
    const radius = Math.max(0.5, diameter / 2);
    const opacity = pressureValue(settings.opacity / 100, sample.pressure, state.pressureOpacity);
    const flow = pressureValue(settings.flow / 100, sample.pressure, state.pressureFlow);
    let operation = 'source-over';
    let color = state.color;
    let alpha = opacity * flow;
    let softness = Math.max(0, Math.min(4, settings.softness / 300));
    if (tool === 'eraser') { operation = 'destination-out'; color = 'rgba(0,0,0,1)'; }
    else if (tool === 'inpaint') {
      operation = state.inpaintEraseStroke ? 'destination-out' : 'source-over';
      color = 'rgba(255,255,255,1)';
      alpha = 1;
      softness = 0;
    } else if (tool === 'dodge') {
      // Lighten the pixels already there rather than stamping flat white.
      // Intensity scales the per-dab strength so a stroke can be gentle or
      // punchy; the cap keeps it building up gradually like Photoshop.
      const intensity = Number.isFinite(state.dodgeBurnIntensity) ? state.dodgeBurnIntensity / 100 : 0.25;
      operation = 'screen';
      color = 'rgba(255,255,255,1)';
      alpha = Math.max(0.004, Math.min(0.6, alpha * intensity * 0.5));
    } else if (tool === 'burn') {
      // Darken the pixels already there rather than stamping flat black.
      const intensity = Number.isFinite(state.dodgeBurnIntensity) ? state.dodgeBurnIntensity / 100 : 0.25;
      operation = 'multiply';
      color = 'rgba(0,0,0,1)';
      alpha = Math.max(0.004, Math.min(0.6, alpha * intensity * 0.5));
    }
    else if (target.paintingMask) { color = 'rgba(255,255,255,1)'; alpha = 1; softness = 0; }
    else operation = isLayerTransparencyLocked(state, layer) ? 'source-atop' : state.brushBlendMode;
    // The Pixel tool always stamps hard squares; the bundle flag makes a
    // regular Brush/Eraser do the same when the (legacy) pixel preset is on.
    if ((tool === 'pixel' || (state.brushPixel && (tool === 'brush' || tool === 'eraser'))) && !target.paintingMask) {
      // Square pixel brush: hard integer-aligned dabs at the chosen size.
      // Freehand by default; hold Shift to constrain the stroke to the
      // nearest of 8 directions (a straight line anchored at the stroke
      // start, or at the previous Shift+click point).
      const pixelSize = Math.max(1, Math.round(diameter));
      const px = sample.x - target.xOffset;
      const py = sample.y - target.yOffset;
      const op = tool === 'eraser' ? 'destination-out' : 'source-over';
      if (sample.shift) {
        if (!pixelAnchor) pixelAnchor = { x: px, y: py };
        const ddx = px - pixelAnchor.x;
        const ddy = py - pixelAnchor.y;
        if (Math.hypot(ddx, ddy) < 2) {
          pixelDab(target.ctx, px, py, pixelSize, op, color);
          pixelLast = { x: px, y: py };
          return;
        }
        const dir = snapDirection(ddx, ddy);
        const len = ddx * dir.x + ddy * dir.y;
        const end = { x: pixelAnchor.x + dir.x * len, y: pixelAnchor.y + dir.y * len };
        pixelLine(target.ctx, pixelAnchor.x, pixelAnchor.y, end.x, end.y, pixelSize, op, color);
        pixelLast = end;
        return;
      }
      if (pixelLast) pixelLine(target.ctx, pixelLast.x, pixelLast.y, px, py, pixelSize, op, color);
      else pixelDab(target.ctx, px, py, pixelSize, op, color);
      pixelLast = { x: px, y: py };
      return;
    }
    circleStamp(target.ctx, sample.x - target.xOffset, sample.y - target.yOffset, radius, softness, color, alpha, operation);
  }

  const sampler = createBrushSampler({
    getDiameter: () => state.brushSize,
    getSpacing: () => state.brushSpacing / 100,
    getSmoothing: () => state.brushSmoothing / 100,
    emit: renderSample,
  });

  function beginStroke(sample, tool = state.tool) {
    strokeTool = tool;
    smudgeBuffer = null;
    pixelLast = null;
    pixelAnchor = (sample && sample.shift && pixelClickAnchor) ? { x: pixelClickAnchor.x, y: pixelClickAnchor.y } : null;
    sampler.begin(sample);
    state.lastX = sample.x;
    state.lastY = sample.y;
    composite();
  }

  function strokeTo(sample) {
    sampler.add(sample);
    state.lastX = sample.x;
    state.lastY = sample.y;
    scheduleComposite();
  }

  function endStroke(sample) {
    sampler.end(sample);
    if (pixelLast) pixelClickAnchor = { x: pixelLast.x, y: pixelLast.y };
    strokeTool = null;
    smudgeBuffer = null;
    pixelLast = null;
    pixelAnchor = null;
    flushComposite();
  }

  return { beginStroke, strokeTo, endStroke };
}
