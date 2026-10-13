/**
 * Apply a Brightness/Contrast, Black & White, Hue/Saturation, Levels, Curves, or Color Balance
 * adjustment to a source canvas and return a fresh canvas with the
 * result. Pure pixel math — no DOM, no module state.
 *
 * Used by the editor's per-layer FX stack: each `adjLayer` calls
 * `applyAdjustment(prevCanvas, adjLayer)` and the result feeds the
 * next layer in the stack.
 *
 * Adjustment shape:
 *   { type: 'brightness-contrast', params: { brightness, contrast } }
 *   { type: 'hue-saturation',      params: { hue, saturation } }
 *   { type: 'levels',              params: { inBlack, inWhite, gamma, outBlack, outWhite } }
 *   { type: 'curves',              params: { points: {rgb, red, green, blue} } }
 *   { type: 'color-balance',       params: { shadows, midtones, highlights } }
 */
function clampByte(value) {
  return value < 0 ? 0 : value > 255 ? 255 : Math.round(value);
}

function finiteOr(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function levelLut(values = {}) {
  const inLow = Math.max(0, Math.min(254, finiteOr(values.inBlack, 0)));
  const inHigh = Math.max(inLow + 1, Math.min(255, finiteOr(values.inWhite, 255)));
  const gamma = Math.max(0.1, finiteOr(values.gamma, 1));
  const outLow = Math.max(0, Math.min(255, finiteOr(values.outBlack, 0)));
  const outHigh = Math.max(outLow, Math.min(255, finiteOr(values.outWhite, 255)));
  const lut = new Uint8ClampedArray(256);
  for (let value = 0; value < 256; value += 1) {
    const normalized = Math.max(0, Math.min(1, (value - inLow) / (inHigh - inLow)));
    lut[value] = clampByte(Math.pow(normalized, 1 / gamma) * (outHigh - outLow) + outLow);
  }
  return lut;
}

function normalizedCurvePoints(points) {
  const clean = (Array.isArray(points) ? points : [])
    .filter(point => Array.isArray(point) && point.length >= 2)
    .map(point => [clampByte(Number(point[0]) || 0), clampByte(Number(point[1]) || 0)])
    .sort((a, b) => a[0] - b[0]);
  if (!clean.length || clean[0][0] !== 0) clean.unshift([0, clean[0]?.[1] ?? 0]);
  if (clean[clean.length - 1][0] !== 255) clean.push([255, clean[clean.length - 1]?.[1] ?? 255]);
  const unique = [];
  for (const point of clean) {
    if (unique.length && unique[unique.length - 1][0] === point[0]) unique[unique.length - 1] = point;
    else unique.push(point);
  }
  return unique;
}

export function curveLut(points) {
  const clean = normalizedCurvePoints(points);
  const lut = new Uint8ClampedArray(256);
  let segment = 0;
  for (let value = 0; value < 256; value += 1) {
    while (segment < clean.length - 2 && value > clean[segment + 1][0]) segment += 1;
    const left = clean[segment];
    const right = clean[Math.min(segment + 1, clean.length - 1)];
    const span = Math.max(1, right[0] - left[0]);
    const amount = Math.max(0, Math.min(1, (value - left[0]) / span));
    lut[value] = clampByte(left[1] + (right[1] - left[1]) * amount);
  }
  return lut;
}

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const lightness = (max + min) / 2;
  if (max === min) return [0, 0, lightness];
  const delta = max - min;
  const saturation = lightness > .5 ? delta / (2 - max - min) : delta / (max + min);
  let hue;
  if (max === r) hue = ((g - b) / delta + (g < b ? 6 : 0)) / 6;
  else if (max === g) hue = ((b - r) / delta + 2) / 6;
  else hue = ((r - g) / delta + 4) / 6;
  return [hue, saturation, lightness];
}

function hueChannel(p, q, t) {
  if (t < 0) t += 1;
  if (t > 1) t -= 1;
  if (t < 1 / 6) return p + (q - p) * 6 * t;
  if (t < 1 / 2) return q;
  if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
  return p;
}

function hslToRgb(h, s, l) {
  if (!s) return [l * 255, l * 255, l * 255];
  const q = l < .5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [hueChannel(p, q, h + 1 / 3) * 255, hueChannel(p, q, h) * 255, hueChannel(p, q, h - 1 / 3) * 255];
}

function parseHexColor(value, fallback) {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(value || ''));
  const hex = match ? match[1] : fallback;
  return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
}

function hueDistance(a, b) {
  const distance = Math.abs(a - b);
  return Math.min(distance, 1 - distance);
}

function selectiveRangeWeight(name, hue, saturation, lightness) {
  if (name === 'blacks') return Math.max(0, Math.min(1, (0.55 - lightness) / 0.45));
  if (name === 'neutrals') return Math.max(0, Math.min(1, (1 - saturation) * (1 - Math.abs(lightness - .5) * 1.5)));
  const centers = { reds: 0, yellows: 1 / 6, greens: 1 / 3, cyans: .5, blues: 2 / 3, magentas: 5 / 6 };
  const distance = hueDistance(hue, centers[name] ?? 0);
  return Math.max(0, 1 - distance * 6) * saturation;
}

export function applyAdjustment(srcCanvas, adj) {
  const w = srcCanvas.width, h = srcCanvas.height;
  const out = document.createElement('canvas');
  out.width = w; out.height = h;
  const octx = out.getContext('2d');

  // B/C and H/S can use the fast browser-native CSS filter pipeline.
  if (adj.type === 'brightness-contrast') {
    const p = adj.params;
    octx.filter = `brightness(${p.brightness}) contrast(${p.contrast})`;
    octx.drawImage(srcCanvas, 0, 0);
    octx.filter = 'none';
    return out;
  }
  // The remaining adjustments need deterministic per-pixel math so preview,
  // flattening, and reopened documents produce the same result.
  octx.drawImage(srcCanvas, 0, 0);
  const img = octx.getImageData(0, 0, w, h);
  const d = img.data;

  if (adj.type === 'exposure') {
    const p = adj.params || {};
    const multiplier = Math.pow(2, finiteOr(p.exposure, 0));
    const offset = finiteOr(p.offset, 0);
    const gamma = Math.max(.1, finiteOr(p.gamma, 1));
    for (let i = 0; i < d.length; i += 4) {
      d[i] = clampByte(Math.pow(Math.max(0, Math.min(1, d[i] / 255 * multiplier + offset)), 1 / gamma) * 255);
      d[i + 1] = clampByte(Math.pow(Math.max(0, Math.min(1, d[i + 1] / 255 * multiplier + offset)), 1 / gamma) * 255);
      d[i + 2] = clampByte(Math.pow(Math.max(0, Math.min(1, d[i + 2] / 255 * multiplier + offset)), 1 / gamma) * 255);
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'white-balance') {
    const p = adj.params || {};
    const temperature = finiteOr(p.temperature, 0) / 100;
    const tint = finiteOr(p.tint, 0) / 100;
    for (let i = 0; i < d.length; i += 4) {
      d[i] = clampByte(d[i] * (1 + temperature * .28 + tint * .09));
      d[i + 1] = clampByte(d[i + 1] * (1 - tint * .18));
      d[i + 2] = clampByte(d[i + 2] * (1 - temperature * .28 + tint * .09));
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'hue-saturation') {
    const p = adj.params || {};
    const hueShift = finiteOr(p.hue, 0) / 360;
    const saturationScale = Math.max(0, finiteOr(p.saturation, 1));
    const lightnessShift = finiteOr(p.lightness, 0) / 100;
    for (let i = 0; i < d.length; i += 4) {
      let [hue, saturation, lightness] = rgbToHsl(d[i], d[i + 1], d[i + 2]);
      hue = (hue + hueShift + 1) % 1;
      saturation = Math.max(0, Math.min(1, saturation * saturationScale));
      lightness = Math.max(0, Math.min(1, lightness + lightnessShift));
      const rgb = hslToRgb(hue, saturation, lightness);
      d[i] = clampByte(rgb[0]); d[i + 1] = clampByte(rgb[1]); d[i + 2] = clampByte(rgb[2]);
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'vibrance') {
    const amount = Math.max(-1, Math.min(1, finiteOr(adj.params?.vibrance, 0) / 100));
    for (let i = 0; i < d.length; i += 4) {
      const [hue, saturation, lightness] = rgbToHsl(d[i], d[i + 1], d[i + 2]);
      const adjustedSaturation = amount >= 0
        ? saturation + (1 - saturation) * amount
        : saturation * (1 + amount);
      const rgb = hslToRgb(hue, Math.max(0, Math.min(1, adjustedSaturation)), lightness);
      d[i] = clampByte(rgb[0]); d[i + 1] = clampByte(rgb[1]); d[i + 2] = clampByte(rgb[2]);
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'black-white') {
    const p = adj.params || {};
    const red = finiteOr(p.red, 30) / 100;
    const green = finiteOr(p.green, 59) / 100;
    const blue = finiteOr(p.blue, 11) / 100;
    const constant = finiteOr(p.constant, 0) * 2.55;
    for (let i = 0; i < d.length; i += 4) {
      const gray = d[i] * red + d[i + 1] * green + d[i + 2] * blue + constant;
      d[i] = clampByte(gray);
      d[i + 1] = clampByte(gray);
      d[i + 2] = clampByte(gray);
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'shadows-highlights') {
    const shadows = Math.max(-1, Math.min(1, finiteOr(adj.params?.shadows, 0) / 100));
    const highlights = Math.max(-1, Math.min(1, finiteOr(adj.params?.highlights, 0) / 100));
    const toneAdjust = (value, amount, weight) => amount >= 0
      ? value + (255 - value) * amount * weight
      : value + value * amount * weight;
    for (let i = 0; i < d.length; i += 4) {
      const luminance = (0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]) / 255;
      const shadowWeight = (1 - luminance) ** 2;
      const highlightWeight = luminance ** 2;
      d[i] = clampByte(toneAdjust(toneAdjust(d[i], shadows, shadowWeight), highlights, highlightWeight));
      d[i + 1] = clampByte(toneAdjust(toneAdjust(d[i + 1], shadows, shadowWeight), highlights, highlightWeight));
      d[i + 2] = clampByte(toneAdjust(toneAdjust(d[i + 2], shadows, shadowWeight), highlights, highlightWeight));
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'levels') {
    const l = adj.params;
    const master = levelLut(l);
    const red = levelLut(l.channels?.red);
    const green = levelLut(l.channels?.green);
    const blue = levelLut(l.channels?.blue);
    for (let i = 0; i < d.length; i += 4) {
      d[i] = red[master[d[i]]];
      d[i + 1] = green[master[d[i + 1]]];
      d[i + 2] = blue[master[d[i + 2]]];
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'curves') {
    const points = adj.params?.points || {};
    const master = curveLut(points.rgb);
    const red = curveLut(points.red);
    const green = curveLut(points.green);
    const blue = curveLut(points.blue);
    for (let i = 0; i < d.length; i += 4) {
      d[i] = red[master[d[i]]];
      d[i + 1] = green[master[d[i + 1]]];
      d[i + 2] = blue[master[d[i + 2]]];
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'color-balance') {
    const cb = adj.params;
    const scale = 0.6;
    const s = cb.shadows, m = cb.midtones, hi = cb.highlights;
    const sR = s.r*scale, sG = s.g*scale, sB = s.b*scale;
    const mR = m.r*scale, mG = m.g*scale, mB = m.b*scale;
    const hR = hi.r*scale, hG = hi.g*scale, hB = hi.b*scale;
    // Bell-curve tone weights so each pixel's shift is proportional to
    // how "shadow", "midtone", or "highlight" its luminance is.
    const wS = new Float32Array(256), wM = new Float32Array(256), wH = new Float32Array(256);
    const sig = 0.25;
    for (let v = 0; v < 256; v++) {
      const t = v / 255;
      wS[v] = Math.exp(-(t*t) / (2*sig*sig));
      wM[v] = Math.exp(-((t-0.5)*(t-0.5)) / (2*sig*sig));
      wH[v] = Math.exp(-((1-t)*(1-t)) / (2*sig*sig));
    }
    for (let i = 0; i < d.length; i += 4) {
      let r = d[i], g = d[i+1], b = d[i+2];
      const Y = (0.2126*r + 0.7152*g + 0.0722*b) | 0;
      const ws = wS[Y], wm = wM[Y], wh = wH[Y];
      r += sR*ws + mR*wm + hR*wh;
      g += sG*ws + mG*wm + hG*wh;
      b += sB*ws + mB*wm + hB*wh;
      d[i]   = r < 0 ? 0 : r > 255 ? 255 : r;
      d[i+1] = g < 0 ? 0 : g > 255 ? 255 : g;
      d[i+2] = b < 0 ? 0 : b > 255 ? 255 : b;
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'selective-color') {
    const ranges = adj.params?.ranges || {};
    for (let i = 0; i < d.length; i += 4) {
      let r = d[i], g = d[i + 1], b = d[i + 2];
      const [hue, saturation, lightness] = rgbToHsl(r, g, b);
      for (const [name, values] of Object.entries(ranges)) {
        const weight = selectiveRangeWeight(name, hue, saturation, lightness) * .65;
        if (weight <= 0) continue;
        const black = finiteOr(values.black, 0) / 100;
        r += (-finiteOr(values.cyan, 0) / 100 * 255 - black * r) * weight;
        g += (-finiteOr(values.magenta, 0) / 100 * 255 - black * g) * weight;
        b += (-finiteOr(values.yellow, 0) / 100 * 255 - black * b) * weight;
      }
      d[i] = clampByte(r); d[i + 1] = clampByte(g); d[i + 2] = clampByte(b);
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  if (adj.type === 'gradient-map') {
    const p = adj.params || {};
    let shadows = parseHexColor(p.shadows, '000000');
    let highlights = parseHexColor(p.highlights, 'ffffff');
    if (p.reverse) [shadows, highlights] = [highlights, shadows];
    const midpoint = Math.max(.01, Math.min(.99, finiteOr(p.midpoint, 50) / 100));
    const exponent = Math.log(.5) / Math.log(midpoint);
    for (let i = 0; i < d.length; i += 4) {
      const luminance = (0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]) / 255;
      const amount = Math.pow(luminance, exponent);
      d[i] = clampByte(shadows[0] + (highlights[0] - shadows[0]) * amount);
      d[i + 1] = clampByte(shadows[1] + (highlights[1] - shadows[1]) * amount);
      d[i + 2] = clampByte(shadows[2] + (highlights[2] - shadows[2]) * amount);
    }
    octx.putImageData(img, 0, 0);
    return out;
  }

  return out;
}


/**
 * Apply a combined Levels + Color Balance pass to a layer in-place via
 * its `layer.adjustments` field. Cached on `layer._adjCache` keyed by
 * `cacheKey` so repeated composite passes don't re-run the math.
 *
 * Returns the cached output canvas.
 *
 * @param {{
 *   canvas: HTMLCanvasElement,
 *   adjustments: object,
 *   _adjCache?: HTMLCanvasElement,
 *   _adjCacheKey?: string,
 * }} layer
 * @param {string} cacheKey  Stable signature of `layer.adjustments`.
 */
export function renderLayerPixelAdjustments(layer, cacheKey) {
  const adj = layer.adjustments;
  if (layer._adjCache && layer._adjCacheKey === cacheKey) return layer._adjCache;
  if (!layer._adjCache) {
    layer._adjCache = document.createElement('canvas');
  }
  const out = layer._adjCache;
  out.width = layer.canvas.width;
  out.height = layer.canvas.height;
  const octx = out.getContext('2d');
  octx.clearRect(0, 0, out.width, out.height);
  octx.drawImage(layer.canvas, 0, 0);
  const img = octx.getImageData(0, 0, out.width, out.height);
  const d = img.data;

  // Single 256-entry LUT for the Levels portion (applied per R/G/B
  // channel identically — luma-style isn't right when colour balance
  // follows, per-channel is fine here).
  const l = adj.levels || { inBlack: 0, inWhite: 255, gamma: 1, outBlack: 0, outWhite: 255 };
  const inLow  = Math.max(0, Math.min(254, l.inBlack));
  const inHigh = Math.max(inLow + 1, Math.min(255, l.inWhite));
  const gamma  = Math.max(0.1, l.gamma || 1);
  const outLow  = Math.max(0, Math.min(255, l.outBlack));
  const outHigh = Math.max(outLow, Math.min(255, l.outWhite));
  const inv = 1.0 / gamma;
  const span = (outHigh - outLow);
  const lut = new Uint8ClampedArray(256);
  for (let v = 0; v < 256; v++) {
    let t = (v - inLow) / (inHigh - inLow);
    if (t < 0) t = 0; else if (t > 1) t = 1;
    t = Math.pow(t, inv);
    lut[v] = Math.round(t * span + outLow);
  }

  // Color Balance bell-curve weights (see applyAdjustment).
  const cb = adj.colorBalance || { shadows: {r:0,g:0,b:0}, midtones: {r:0,g:0,b:0}, highlights: {r:0,g:0,b:0} };
  const s = cb.shadows || {r:0,g:0,b:0};
  const m = cb.midtones || {r:0,g:0,b:0};
  const h = cb.highlights || {r:0,g:0,b:0};
  const scale = 0.6;
  const sR = s.r * scale, sG = s.g * scale, sB = s.b * scale;
  const mR = m.r * scale, mG = m.g * scale, mB = m.b * scale;
  const hR = h.r * scale, hG = h.g * scale, hB = h.b * scale;

  const wS = new Float32Array(256);
  const wM = new Float32Array(256);
  const wH = new Float32Array(256);
  for (let v = 0; v < 256; v++) {
    const t = v / 255;
    const dS = t, wsig = 0.25;
    const dM = t - 0.5;
    const dH = 1 - t;
    wS[v] = Math.exp(-(dS * dS) / (2 * wsig * wsig));
    wM[v] = Math.exp(-(dM * dM) / (2 * wsig * wsig));
    wH[v] = Math.exp(-(dH * dH) / (2 * wsig * wsig));
  }

  for (let i = 0; i < d.length; i += 4) {
    let r = lut[d[i]];
    let g = lut[d[i + 1]];
    let b = lut[d[i + 2]];
    const Y = (0.2126 * r + 0.7152 * g + 0.0722 * b) | 0;
    const ws = wS[Y], wm = wM[Y], wh = wH[Y];
    r += sR * ws + mR * wm + hR * wh;
    g += sG * ws + mG * wm + hG * wh;
    b += sB * ws + mB * wm + hB * wh;
    d[i]     = r < 0 ? 0 : r > 255 ? 255 : r;
    d[i + 1] = g < 0 ? 0 : g > 255 ? 255 : g;
    d[i + 2] = b < 0 ? 0 : b > 255 ? 255 : b;
  }
  octx.putImageData(img, 0, 0);
  layer._adjCacheKey = cacheKey;
  return out;
}


/**
 * Walk the layer's `adjLayers` stack (skipping the one currently being
 * edited, if any) plus an optional staged preview adjustment, producing
 * a final canvas the composite step can paint. The result is memoised
 * on `layer._adjFinal` keyed by a signature of all adjLayer params +
 * staged + editing id, so repeated composite passes are O(1) when
 * nothing has changed.
 *
 * If the stack is empty AND nothing is staged, returns the layer's own
 * canvas unchanged (no allocation).
 *
 * @param {{
 *   canvas: HTMLCanvasElement,
 *   adjLayers?: Array<{id: string, type: string, params: object, visible: boolean, opacity: number}>,
 *   _stagedAdj?: {type: string, params: object} | null,
 *   _editingAdjId?: string | null,
 *   _adjFinal?: HTMLCanvasElement,
 *   _adjFinalKey?: string,
 * }} layer
 * @returns {HTMLCanvasElement}
 */
export function renderLayerWithAdjLayers(layer) {
  const editingId = layer._editingAdjId || null;
  const stack = (layer.adjLayers || []).filter(a => a.visible && a.id !== editingId);
  const staged = layer._stagedAdj;
  if (stack.length === 0 && (!staged || layer._adjCompare)) {
    layer._adjFinalKey = '';
    return layer.canvas;
  }
  const sig = stack.map(a => `${a.id}:${a.visible?1:0}:${a.opacity}:${a.type}:${JSON.stringify(a.params)}`).join('|') +
    (staged ? `|S:${staged.type}:${JSON.stringify(staged.params)}` : '') +
    (editingId ? `|E:${editingId}` : '');
  const compare = !!layer._adjCompare;
  const fullSig = `${sig}|C:${compare ? 1 : 0}`;
  if (layer._adjFinal && layer._adjFinalKey === fullSig) return layer._adjFinal;
  let cur = layer.canvas;
  const w = layer.canvas.width, h = layer.canvas.height;
  for (const adj of stack) {
    const adjOut = applyAdjustment(cur, adj);
    if (adj.opacity >= 0.999) {
      cur = adjOut;
    } else {
      const blend = document.createElement('canvas');
      blend.width = w; blend.height = h;
      const bctx = blend.getContext('2d');
      bctx.drawImage(cur, 0, 0);
      bctx.globalAlpha = adj.opacity;
      bctx.drawImage(adjOut, 0, 0);
      bctx.globalAlpha = 1;
      cur = blend;
    }
  }
  if (staged && !compare) {
    cur = applyAdjustment(cur, staged);
  }
  layer._adjFinal = cur;
  layer._adjFinalKey = fullSig;
  return cur;
}
