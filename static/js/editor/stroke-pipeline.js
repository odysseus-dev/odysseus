/** Shared sampled renderer for brush, eraser, clone, masks, and retouch tools. */
import { state } from './state.js';
import { createBrushSampler, pressureValue } from './brush-engine.js';
import { isLayerTransparencyLocked } from './layer-groups.js';

const PAINT_TOOLS = new Set(['brush', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'inpaint']);

function toolSettings(tool) {
  if (tool === 'eraser') return { opacity: state.eraserOpacity, flow: state.eraserFlow, softness: state.eraserSoftness };
  if (tool === 'clone' || tool === 'heal') return { opacity: state.cloneOpacity, flow: state.cloneFlow, softness: state.cloneSoftness };
  if (tool === 'smudge') return { opacity: state.smudgeStrength, flow: state.brushFlow, softness: state.brushSoftness };
  return { opacity: state.brushOpacity, flow: state.brushFlow, softness: state.brushSoftness };
}

function transparentColor(color) {
  if (color.startsWith('#')) {
    const raw = color.slice(1);
    const expanded = raw.length === 3 ? raw.split('').map(char => char + char).join('') : raw;
    const rgb = [0, 2, 4].map(index => parseInt(expanded.slice(index, index + 2), 16));
    return `rgba(${rgb.join(',')},0)`;
  }
  const match = color.match(/rgba?\(([^)]+)\)/);
  return match ? `rgba(${match[1].split(',').slice(0, 3).join(',')},0)` : 'rgba(0,0,0,0)';
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
    gradient.addColorStop(0, color);
    gradient.addColorStop(1, transparentColor(color));
    ctx.fillStyle = gradient;
  }
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

export function createStrokePipeline({ activeLayer, getActiveMaskLayer, composite }) {
  let strokeTool = null;
  let compositeFrame = null;
  let smudgeBuffer = null;

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
    let softness = Math.max(0, Math.min(1, settings.softness / 300));
    if (tool === 'eraser') { operation = 'destination-out'; color = 'rgba(0,0,0,1)'; }
    else if (tool === 'inpaint') {
      operation = state.inpaintEraseStroke ? 'destination-out' : 'source-over';
      color = 'rgba(255,255,255,1)';
      alpha = 1;
      softness = 0;
    } else if (tool === 'dodge') { operation = 'screen'; color = 'rgba(255,255,255,1)'; }
    else if (tool === 'burn') { operation = 'multiply'; color = 'rgba(0,0,0,1)'; }
    else if (target.paintingMask) { color = 'rgba(255,255,255,1)'; alpha = 1; softness = 0; }
    else operation = isLayerTransparencyLocked(state, layer) ? 'source-atop' : state.brushBlendMode;
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
    strokeTool = null;
    smudgeBuffer = null;
    flushComposite();
  }

  return { beginStroke, strokeTo, endStroke };
}
