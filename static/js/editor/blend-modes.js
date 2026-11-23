/**
 * Layer/graphic blend modes.
 *
 * Canvas2D can only composite a subset of the Photoshop blend modes natively.
 * This module exposes that native set plus the "extra" modes PhotoCraft offers
 * (Dissolve, Linear Burn, Darker Color, Linear Dodge/Add, Lighter Color,
 * Vivid Light, Linear Light, Pin Light, Hard Mix, Subtract, Divide) and a
 * single `blendDraw` entry point that both paths go through.
 *
 * The non-native modes follow the W3C compositing spec (separable + the two
 * non-separable luminance modes) and operate on ImageData in the overlap of
 * the source and the destination so rendering stays reasonably cheap.
 */

export const NATIVE_BLEND_MODES = new Set([
  'source-over',
  'multiply',
  'screen',
  'overlay',
  'darken',
  'lighten',
  'color-dodge',
  'color-burn',
  'hard-light',
  'soft-light',
  'difference',
  'exclusion',
  'hue',
  'saturation',
  'color',
  'luminosity',
]);

const NATIVE_ALIASES = {
  'linear-dodge': 'lighter',
  add: 'lighter',
};

export const EXTRA_BLEND_MODES = [
  { value: 'linear-dodge', label: 'Linear Dodge (Add)' },
  { value: 'linear-burn', label: 'Linear Burn' },
  { value: 'darker-color', label: 'Darker Color' },
  { value: 'lighter-color', label: 'Lighter Color' },
  { value: 'vivid-light', label: 'Vivid Light' },
  { value: 'linear-light', label: 'Linear Light' },
  { value: 'pin-light', label: 'Pin Light' },
  { value: 'hard-mix', label: 'Hard Mix' },
  { value: 'subtract', label: 'Subtract' },
  { value: 'divide', label: 'Divide' },
  { value: 'dissolve', label: 'Dissolve' },
];

const NON_SEPARABLE = new Set(['darker-color', 'lighter-color']);

export function isNativeBlendMode(mode) {
  if (!mode) return true;
  return NATIVE_BLEND_MODES.has(mode) || Object.prototype.hasOwnProperty.call(NATIVE_ALIASES, mode);
}

export function isKnownBlendMode(mode) {
  if (!mode) return true;
  return isNativeBlendMode(mode) || EXTRA_BLEND_MODES.some(entry => entry.value === mode);
}

function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

function lum(r, g, b) {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

// Separable per-channel blend functions on normalized [0,1] values.
function blendChannel(mode, cb, cs) {
  switch (mode) {
    case 'normal':
      return cs;
    case 'multiply':
      return cb * cs;
    case 'screen':
      return cb + cs - cb * cs;
    case 'overlay':
      return cb <= 0.5 ? 2 * cb * cs : 1 - 2 * (1 - cb) * (1 - cs);
    case 'darken':
      return Math.min(cb, cs);
    case 'lighten':
      return Math.max(cb, cs);
    case 'color-dodge':
      if (cb === 0) return 0;
      if (cs === 1) return 1;
      return Math.min(1, cb / (1 - cs));
    case 'color-burn':
      if (cb === 1) return 1;
      if (cs === 0) return 0;
      return 1 - Math.min(1, (1 - cb) / cs);
    case 'hard-light':
      return cs <= 0.5 ? 2 * cs * cb : 1 - 2 * (1 - cs) * (1 - cb);
    case 'soft-light': {
      if (cs <= 0.5) return cb - (1 - 2 * cs) * cb * (1 - cb);
      const d = cb <= 0.25 ? ((16 * cb - 12) * cb + 4) * cb : Math.sqrt(cb);
      return cb + (2 * cs - 1) * (d - cb);
    }
    case 'difference':
      return Math.abs(cb - cs);
    case 'exclusion':
      return cb + cs - 2 * cb * cs;
    case 'linear-burn':
      return clamp01(cb + cs - 1);
    case 'linear-dodge':
      return clamp01(cb + cs);
    case 'vivid-light':
      return cs <= 0.5
        ? (cs === 0 ? 0 : 1 - Math.min(1, (1 - cb) / (2 * cs)))
        : (cs === 1 ? 1 : Math.min(1, cb / (2 * (1 - cs))));
    case 'linear-light':
      return clamp01(cb + 2 * cs - 1);
    case 'pin-light':
      return cs <= 0.5 ? Math.min(cb, 2 * cs) : Math.max(cb, 2 * cs - 1);
    case 'hard-mix': {
      const vivid = cs <= 0.5
        ? (cs === 0 ? 0 : 1 - Math.min(1, (1 - cb) / (2 * cs)))
        : (cs === 1 ? 1 : Math.min(1, cb / (2 * (1 - cs))));
      return vivid < 0.5 ? 0 : 1;
    }
    case 'subtract':
      return clamp01(cb - cs);
    case 'divide':
      return cs === 0 ? 1 : clamp01(cb / cs);
    default:
      return cs;
  }
}

function dissolveRandom(i) {
  let seed = (i * 2654435761) >>> 0;
  seed ^= seed >>> 13;
  seed = (seed * 1274126177) >>> 0;
  return seed / 4294967296;
}

function ensureSourceData(source) {
  const sw = source.width || source.naturalWidth || 0;
  const sh = source.height || source.naturalHeight || 0;
  if (!sw || !sh) return null;
  const scratch = document.createElement('canvas');
  scratch.width = sw;
  scratch.height = sh;
  const sctx = scratch.getContext('2d');
  sctx.drawImage(source, 0, 0, sw, sh);
  return { data: sctx.getImageData(0, 0, sw, sh).data, sw, sh };
}

function blendCustom(ctx, source, mode, alpha, dx, dy) {
  if (alpha <= 0) return;
  const src = ensureSourceData(source);
  if (!src) return;
  const tw = ctx.canvas.width;
  const th = ctx.canvas.height;
  const sw = src.sw;
  const sh = src.sh;
  const sx0 = Math.max(0, -dx);
  const sy0 = Math.max(0, -dy);
  const dx0 = Math.max(0, dx);
  const dy0 = Math.max(0, dy);
  const w = Math.min(sw - sx0, tw - dx0);
  const h = Math.min(sh - sy0, th - dy0);
  if (w <= 0 || h <= 0) return;

  const dst = ctx.getImageData(dx0, dy0, w, h);
  const dd = dst.data;
  const sd = src.data;
  const nonSeparable = NON_SEPARABLE.has(mode);
  const dissolve = mode === 'dissolve';

  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const si = ((y + sy0) * sw + (x + sx0)) * 4;
      const di = (y * w + x) * 4;
      const sa = (sd[si + 3] / 255) * alpha;
      const ba = dd[di + 3] / 255;

      if (dissolve) {
        const seed = (y + dy0) * 73856093 ^ (x + dx0) * 19349663;
        if (dissolveRandom(seed) >= sa) continue;
        dd[di] = sd[si];
        dd[di + 1] = sd[si + 1];
        dd[di + 2] = sd[si + 2];
        dd[di + 3] = 255;
        continue;
      }

      if (sa === 0) continue;

      const sr = sd[si] / 255;
      const sg = sd[si + 1] / 255;
      const sb = sd[si + 2] / 255;
      const br = dd[di] / 255;
      const bg = dd[di + 1] / 255;
      const bb = dd[di + 2] / 255;

      let mergedR;
      let mergedG;
      let mergedB;
      if (nonSeparable) {
        const backdropLum = lum(br, bg, bb);
        const sourceLum = lum(sr, sg, sb);
        const useSource = mode === 'darker-color' ? sourceLum < backdropLum : sourceLum > backdropLum;
        mergedR = useSource ? sr : br;
        mergedG = useSource ? sg : bg;
        mergedB = useSource ? sb : bb;
      } else {
        mergedR = blendChannel(mode, br, sr);
        mergedG = blendChannel(mode, bg, sg);
        mergedB = blendChannel(mode, bb, sb);
      }

      const srEff = (1 - ba) * sr + ba * mergedR;
      const sgEff = (1 - ba) * sg + ba * mergedG;
      const sbEff = (1 - ba) * sb + ba * mergedB;

      const ao = sa + ba * (1 - sa);
      if (ao <= 0) continue;
      const cr = (sa * srEff + ba * br * (1 - sa)) / ao;
      const cg = (sa * sgEff + ba * bg * (1 - sa)) / ao;
      const cbOut = (sa * sbEff + ba * bb * (1 - sa)) / ao;

      dd[di] = Math.round(clamp01(cr) * 255);
      dd[di + 1] = Math.round(clamp01(cg) * 255);
      dd[di + 2] = Math.round(clamp01(cbOut) * 255);
      dd[di + 3] = Math.round(ao * 255);
    }
  }
  ctx.putImageData(dst, dx0, dy0);
}

/**
 * Composite `source` onto a 2D context (or canvas) using `mode` at `alpha`.
 * Native modes use globalCompositeOperation; the rest fall back to pixel math.
 */
export function blendDraw(target, source, mode, alpha = 1, dx = 0, dy = 0) {
  if (!target || !source) return;
  const ctx = typeof target.getContext === 'function' ? target.getContext('2d') : target;
  const a = Number.isFinite(alpha) ? clamp01(alpha) : 1;
  const normalized = mode || 'source-over';
  if (isNativeBlendMode(normalized)) {
    const prevAlpha = ctx.globalAlpha;
    const prevOp = ctx.globalCompositeOperation;
    ctx.globalAlpha = a;
    ctx.globalCompositeOperation = NATIVE_ALIASES[normalized] || normalized;
    ctx.drawImage(source, dx, dy);
    ctx.globalAlpha = prevAlpha;
    ctx.globalCompositeOperation = prevOp;
    return;
  }
  blendCustom(ctx, source, normalized, a, dx, dy);
}
