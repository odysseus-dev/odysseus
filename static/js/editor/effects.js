/** Retained, non-destructive layer effects. */
import { LAYER_STYLES, styleParams, renderLayerStyle, renderDropShadow } from './layer-styles.js';

export function effectsWithPreview(owner) {
  const effects = owner.effects || [];
  const preview = owner._effectPreview;
  if (!preview) return effects;
  const index = preview.id ? effects.findIndex(effect => effect.id === preview.id) : -1;
  return index < 0 ? [...effects, preview] : effects.map((effect, i) => i === index ? preview : effect);
}

const EFFECT_TYPES = new Set(['gaussian-blur', 'sharpen', 'color-overlay', 'drop-shadow', 'stroke', 'linear-gradient', 'radial-gradient']);
for (const type of Object.keys(LAYER_STYLES)) EFFECT_TYPES.add(type);

export const EFFECT_PRESETS = {
  'soft-blur': { type: 'gaussian-blur', params: { radius: 4 } },
  'crisp-detail': { type: 'sharpen', params: { amount: 0.35 } },
  'soft-shadow': { type: 'drop-shadow', params: { color: '#000000', opacity: 0.3, blur: 8, x: 2, y: 3 } },
  'white-outline': { type: 'stroke', params: { color: '#ffffff', opacity: 0.85, width: 2 } },
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function normalizeEffectMask(mask) {
  if (!mask || typeof mask !== 'object' || Array.isArray(mask)) return null;
  return {
    id: String(mask.id || `effect-mask-${Math.random().toString(36).slice(2, 9)}`),
    name: String(mask.name || 'Effect Mask'),
    visible: mask.visible !== false,
    canvas: mask.canvas || null,
    ctx: mask.ctx || null,
    canvasW: Number(mask.canvasW) || mask.canvas?.width || 0,
    canvasH: Number(mask.canvasH) || mask.canvas?.height || 0,
    dataUrl: typeof mask.dataUrl === 'string' ? mask.dataUrl : null,
  };
}

export function defaultEffectParams(type) {
  if (LAYER_STYLES[type]) return styleParams(type);
  if (type === 'gaussian-blur') return { radius: 6 };
  if (type === 'sharpen') return { amount: 0.5 };
  if (type === 'color-overlay') return { color: '#ffffff', opacity: 0.2, blendMode: 'source-atop' };
  if (type === 'drop-shadow') return { color: '#000000', opacity: 0.45, blur: 12, x: 4, y: 6 };
  if (type === 'stroke') return { color: '#ffffff', opacity: 1, width: 3 };
  if (type === 'linear-gradient' || type === 'radial-gradient') return {
    x1: 0, y1: 0, x2: 1, y2: 0,
    stops: [{ position: 0, color: '#e06c75', alpha: 1 }, { position: 100, color: '#ffffff', alpha: 1 }],
    opacity: 1,
  };
  return {};
}

export function effectPreset(name) {
  const preset = EFFECT_PRESETS[name];
  return preset ? { type: preset.type, params: clone(preset.params) } : null;
}

export function normalizeEffect(effect) {
  const type = EFFECT_TYPES.has(effect?.type) ? effect.type : 'gaussian-blur';
  const defaults = defaultEffectParams(type);
  const params = effect?.params && typeof effect.params === 'object'
    ? { ...defaults, ...clone(effect.params) }
    : defaults;
  if (LAYER_STYLES[type]) Object.assign(params, styleParams(type, params));
  if (type === 'gaussian-blur') params.radius = Math.max(0, Math.min(200, Number(params.radius) || 0));
  if (type === 'sharpen') params.amount = Math.max(0, Math.min(1, Number(params.amount) || 0));
  if (type === 'color-overlay') {
    params.color = /^#[0-9a-f]{6}$/i.test(params.color) ? params.color : defaults.color;
    params.opacity = Math.max(0, Math.min(1, Number(params.opacity) || 0));
    params.blendMode = typeof params.blendMode === 'string' ? params.blendMode : defaults.blendMode;
  }
  if (type === 'drop-shadow') {
    params.color = /^#[0-9a-f]{6}$/i.test(params.color) ? params.color : defaults.color;
    for (const key of ['opacity', 'blur', 'x', 'y']) params[key] = Number(params[key]) || 0;
    params.opacity = Math.max(0, Math.min(1, params.opacity));
    params.blur = Math.max(0, Math.min(200, params.blur));
    params.x = Math.max(-200, Math.min(200, params.x));
    params.y = Math.max(-200, Math.min(200, params.y));
  }
  if (type === 'stroke') {
    params.color = /^#[0-9a-f]{6}$/i.test(params.color) ? params.color : defaults.color;
    params.opacity = Math.max(0, Math.min(1, Number(params.opacity) || 0));
    params.width = Math.max(0, Math.min(100, Number(params.width) || 0));
  }
  if (type === 'linear-gradient' || type === 'radial-gradient') {
    for (const key of ['x1', 'y1', 'x2', 'y2']) {
      const value = Number(params[key]);
      params[key] = Number.isFinite(value) ? Math.max(-2_000_000, Math.min(2_000_000, value)) : defaults[key];
    }
    params.opacity = Math.max(0, Math.min(1, Number(params.opacity) || 0));
    const stops = Array.isArray(params.stops) ? params.stops : defaults.stops;
    params.stops = stops.map(stop => ({
      position: Math.max(0, Math.min(100, Number(stop?.position) || 0)),
      color: /^#[0-9a-f]{6}$/i.test(stop?.color) ? stop.color : '#000000',
      alpha: Math.max(0, Math.min(1, Number(stop?.alpha ?? 1) || 0)),
    })).sort((a, b) => a.position - b.position).slice(0, 12);
    if (params.stops.length < 2) params.stops = clone(defaults.stops);
  }
  return {
    id: String(effect?.id || `effect-${Math.random().toString(36).slice(2, 9)}`),
    type,
    name: String(effect?.name || effectLabel(type)),
    visible: effect?.visible !== false,
    opacity: Math.max(0, Math.min(1, Number(effect?.opacity ?? 1) || 0)),
    mask: normalizeEffectMask(effect?.mask),
    params,
  };
}

export function effectLabel(type) {
  if (LAYER_STYLES[type]) return LAYER_STYLES[type].label;
  return {
    'gaussian-blur': 'Gaussian Blur',
    sharpen: 'Sharpen',
    'color-overlay': 'Color Overlay',
    'drop-shadow': 'Drop Shadow',
    'stroke': 'Stroke',
    'linear-gradient': 'Gradient',
    'radial-gradient': 'Radial Gradient',
  }[type] || type;
}

function copyCanvas(source) {
  const canvas = document.createElement('canvas');
  canvas.width = source.width;
  canvas.height = source.height;
  canvas.getContext('2d').drawImage(source, 0, 0);
  return canvas;
}

function gradientColor(value, alpha = 1) {
  const raw = String(value || '').replace(/^#/, '');
  if (!/^[0-9a-f]{6}$/i.test(raw)) return `rgba(0,0,0,${alpha})`;
  const channels = [0, 2, 4].map(offset => parseInt(raw.slice(offset, offset + 2), 16));
  return `rgba(${channels.join(',')},${alpha})`;
}

function renderGradient(ctx, effect, width, height) {
  const p = effect.params;
  const gradient = effect.type === 'radial-gradient'
    ? ctx.createRadialGradient(p.x1, p.y1, 0, p.x1, p.y1, Math.max(0.5, Math.hypot(p.x2 - p.x1, p.y2 - p.y1)))
    : ctx.createLinearGradient(p.x1, p.y1, p.x2, p.y2);
  for (const stop of p.stops) gradient.addColorStop(stop.position / 100, gradientColor(stop.color, stop.alpha));
  ctx.globalAlpha = p.opacity;
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);
  ctx.globalAlpha = 1;
}

function applyEffectMask(source, effected, mask) {
  if (!mask?.canvas || !source || !effected) return effected;
  const masked = document.createElement('canvas');
  masked.width = effected.width;
  masked.height = effected.height;
  const mctx = masked.getContext('2d');
  mctx.drawImage(effected, 0, 0);
  mctx.globalCompositeOperation = 'destination-in';
  mctx.drawImage(mask.canvas, 0, 0, masked.width, masked.height);
  mctx.globalCompositeOperation = 'source-over';
  return masked;
}

function sharpenCanvas(source, amount, shouldContinue = () => true) {
  if (!amount) return copyCanvas(source);
  const out = document.createElement('canvas');
  out.width = source.width;
  out.height = source.height;
  const src = source.getContext('2d').getImageData(0, 0, source.width, source.height);
  const dst = out.getContext('2d').createImageData(source.width, source.height);
  const a = Math.max(0, Math.min(1, Number(amount) || 0));
  const stride = source.width * 4;
  for (let y = 0; y < source.height; y += 1) {
    if ((y & 7) === 0 && !shouldContinue()) return null;
    for (let x = 0; x < source.width; x += 1) {
      const at = (y * source.width + x) * 4;
      for (let channel = 0; channel < 3; channel += 1) {
        const center = src.data[at + channel] * (1 + 4 * a);
        const left = src.data[y * stride + Math.max(0, x - 1) * 4 + channel];
        const right = src.data[y * stride + Math.min(source.width - 1, x + 1) * 4 + channel];
        const above = src.data[Math.max(0, y - 1) * stride + x * 4 + channel];
        const below = src.data[Math.min(source.height - 1, y + 1) * stride + x * 4 + channel];
        dst.data[at + channel] = Math.max(0, Math.min(255, center - a * (left + right + above + below)));
      }
      dst.data[at + 3] = src.data[at + 3];
    }
  }
  out.getContext('2d').putImageData(dst, 0, 0);
  return out;
}

/** Render effects in list order without mutating the source layer. */
export function renderEffects(source, effects = [], shouldContinue = () => true) {
  if (!source || !Array.isArray(effects) || effects.length === 0) return source;
  let current = copyCanvas(source);
  for (const raw of effects) {
    if (!shouldContinue()) return current;
    const effect = normalizeEffect(raw);
    if (!effect.visible || effect.opacity <= 0) continue;
    const next = document.createElement('canvas');
    next.width = current.width;
    next.height = current.height;
    const ctx = next.getContext('2d');
    if (LAYER_STYLES[effect.type]) {
      ctx.drawImage(renderLayerStyle(current, effect.type, effect.params), 0, 0);
    } else if (effect.type === 'gaussian-blur') {
      ctx.filter = `blur(${effect.params.radius}px)`;
      ctx.drawImage(current, 0, 0);
      ctx.filter = 'none';
    } else if (effect.type === 'sharpen') {
      const sharpened = sharpenCanvas(current, effect.params.amount, shouldContinue);
      if (!sharpened) return current;
      ctx.drawImage(sharpened, 0, 0);
    } else if (effect.type === 'color-overlay') {
      ctx.drawImage(current, 0, 0);
      ctx.globalAlpha = effect.params.opacity;
      ctx.globalCompositeOperation = effect.params.blendMode;
      ctx.fillStyle = effect.params.color;
      ctx.fillRect(0, 0, next.width, next.height);
      ctx.globalAlpha = 1;
    } else if (effect.type === 'drop-shadow') {
      ctx.drawImage(renderDropShadow(current, effect.params), 0, 0);
    } else if (effect.type === 'stroke') {
      ctx.globalAlpha = effect.params.opacity;
      ctx.shadowColor = effect.params.color;
      ctx.shadowBlur = effect.params.width;
      ctx.shadowOffsetX = 0;
      ctx.shadowOffsetY = 0;
      ctx.drawImage(current, 0, 0);
      ctx.shadowColor = 'transparent';
      ctx.shadowBlur = 0;
      ctx.drawImage(current, 0, 0);
      ctx.globalAlpha = 1;
    } else if (effect.type === 'linear-gradient' || effect.type === 'radial-gradient') {
      renderGradient(ctx, effect, next.width, next.height);
    }
    ctx.globalCompositeOperation = 'source-over';
    const contribution = effect.mask?.canvas && effect.mask.visible !== false
      ? applyEffectMask(current, next, effect.mask)
      : next;
    const blended = copyCanvas(current);
    const blendCtx = blended.getContext('2d');
    if (effect.type === 'drop-shadow') blendCtx.globalCompositeOperation = 'destination-over';
    blendCtx.globalAlpha = effect.opacity;
    blendCtx.drawImage(contribution, 0, 0);
    blendCtx.globalAlpha = 1;
    current = blended;
  }
  return current;
}

/**
 * Rasterize retained effects off the main thread when browser canvas workers
 * are available. The synchronous renderer remains the compatibility fallback.
 */
export async function renderEffectsAsync(source, effects = [], shouldContinue = () => true) {
  if (!source || !Array.isArray(effects) || effects.length === 0) return source;
  if (typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined' || typeof createImageBitmap !== 'function') {
    return renderEffects(source, effects, shouldContinue);
  }
  if (!shouldContinue()) return source;
  let sourceBitmap;
  const maskBitmaps = [];
  try {
    sourceBitmap = await createImageBitmap(source);
    for (const effect of effects) {
      if (!shouldContinue()) {
        sourceBitmap.close?.();
        for (const bitmap of maskBitmaps) bitmap?.close?.();
        return null;
      }
      maskBitmaps.push(effect?.mask?.canvas && effect.mask.visible !== false
        ? await createImageBitmap(effect.mask.canvas)
        : null);
    }
  } catch {
    sourceBitmap?.close?.();
    for (const bitmap of maskBitmaps) bitmap?.close?.();
    return shouldContinue() ? renderEffects(source, effects, shouldContinue) : null;
  }
  if (!shouldContinue()) {
    sourceBitmap.close?.();
    for (const bitmap of maskBitmaps) bitmap?.close?.();
    return source;
  }
  let worker;
  try {
    worker = new Worker(new URL('./effects-worker.js', import.meta.url), { type: 'module' });
  } catch {
    sourceBitmap.close?.();
    for (const bitmap of maskBitmaps) bitmap?.close?.();
    return shouldContinue() ? renderEffects(source, effects, shouldContinue) : null;
  }
  const payloadEffects = effects.map(raw => {
    const effect = normalizeEffect(raw);
    return { ...effect, mask: null };
  });
  return new Promise(resolve => {
    let settled = false;
    const finish = result => {
      if (settled) return;
      settled = true;
      worker.terminate();
      sourceBitmap.close?.();
      for (const bitmap of maskBitmaps) bitmap?.close?.();
      resolve(result);
    };
    worker.onmessage = event => {
      const { bitmap, error } = event.data || {};
      if (error || !bitmap || !shouldContinue()) {
        bitmap?.close?.();
        finish(shouldContinue() ? renderEffects(source, effects, shouldContinue) : source);
        return;
      }
      const output = document.createElement('canvas');
      output.width = source.width;
      output.height = source.height;
      output.getContext('2d').drawImage(bitmap, 0, 0);
      bitmap.close?.();
      finish(output);
    };
    // A stale worker must not trigger a full-resolution fallback while a newer
    // preview is already queued.
    worker.onerror = () => finish(shouldContinue() ? renderEffects(source, effects, shouldContinue) : null);
    worker.postMessage({ source: sourceBitmap, effects: payloadEffects, masks: maskBitmaps }, [
      sourceBitmap,
      ...maskBitmaps.filter(Boolean),
    ]);
  });
}

export const EFFECT_TYPES_LIST = [...EFFECT_TYPES];
