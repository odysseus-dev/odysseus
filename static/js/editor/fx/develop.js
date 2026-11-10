/**
 * Camera-Raw-style "Develop" tonal pipeline — a single deterministic pass that
 * mirrors the Basic slider set (white balance, exposure, highlights, shadows,
 * whites, blacks, contrast, clarity, vibrance, saturation).
 *
 * Pure pixel math: no DOM, no network, no module state, so it runs identically
 * on the main thread and inside the adjustment worker. Work is done in
 * [0,1] floats and hue is preserved by scaling the RGB triplet by the ratio
 * between the shaped luma and the original luma, so a tonal move never drifts
 * the colour.
 */

function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

function finiteOr(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export const DEVELOP_DEFAULTS = Object.freeze({
  exposure: 0,
  contrast: 0,
  highlights: 0,
  shadows: 0,
  whites: 0,
  blacks: 0,
  temperature: 0,
  tint: 0,
  clarity: 0,
  vibrance: 0,
  saturation: 0,
});

/** Separable box blur of a scalar field — used for the clarity local contrast. */
function boxBlurField(field, width, height, radius) {
  if (radius < 1) return field;
  const tmp = new Float32Array(field.length);
  const out = new Float32Array(field.length);
  const window = radius * 2 + 1;
  for (let y = 0; y < height; y += 1) {
    const row = y * width;
    let sum = 0;
    for (let x = -radius; x <= radius; x += 1) sum += field[row + Math.max(0, Math.min(width - 1, x))];
    for (let x = 0; x < width; x += 1) {
      tmp[row + x] = sum / window;
      const add = field[row + Math.min(width - 1, x + radius + 1)];
      const sub = field[row + Math.max(0, x - radius)];
      sum += add - sub;
    }
  }
  for (let x = 0; x < width; x += 1) {
    let sum = 0;
    for (let y = -radius; y <= radius; y += 1) sum += tmp[Math.max(0, Math.min(height - 1, y)) * width + x];
    for (let y = 0; y < height; y += 1) {
      out[y * width + x] = sum / window;
      const add = tmp[Math.min(height - 1, y + radius + 1) * width + x];
      const sub = tmp[Math.max(0, y - radius) * width + x];
      sum += add - sub;
    }
  }
  return out;
}

/**
 * Apply the Develop pipeline in place on an RGBA byte array.
 * @returns {Uint8ClampedArray} the same array (mutated) for chaining.
 */
export function developImageData(data, width, height, params = {}) {
  const p = {
    exposure: finiteOr(params.exposure, 0),
    contrast: finiteOr(params.contrast, 0),
    highlights: finiteOr(params.highlights, 0),
    shadows: finiteOr(params.shadows, 0),
    whites: finiteOr(params.whites, 0),
    blacks: finiteOr(params.blacks, 0),
    temperature: finiteOr(params.temperature, 0),
    tint: finiteOr(params.tint, 0),
    clarity: finiteOr(params.clarity, 0),
    vibrance: finiteOr(params.vibrance, 0),
    saturation: finiteOr(params.saturation, 0),
  };
  const active = Object.keys(p).some(key => Math.abs(p[key]) > 1e-6);
  if (!active || width <= 0 || height <= 0) return data;

  const n = width * height;
  const exp = Math.pow(2, p.exposure);
  const t = p.temperature / 100;
  const ti = p.tint / 100;
  // Temperature: +warms (more red, less blue). Tint: +magenta (less green).
  const rMul = exp * (1 + 0.30 * t) * (1 + 0.10 * ti);
  const gMul = exp * (1 - 0.20 * ti);
  const bMul = exp * (1 - 0.30 * t) * (1 + 0.10 * ti);
  const hi = p.highlights / 100;
  const sh = p.shadows / 100;
  const wh = p.whites / 100;
  const bl = p.blacks / 100;
  const con = p.contrast / 100;
  const vib = p.vibrance / 100;
  const sat = p.saturation / 100;
  const clarity = p.clarity / 100;

  const lum = new Float32Array(n);
  for (let i = 0; i < n; i += 1) {
    const o = i * 4;
    let r = clamp01((data[o] / 255) * rMul);
    let g = clamp01((data[o + 1] / 255) * gMul);
    let b = clamp01((data[o + 2] / 255) * bMul);

    const base = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    let y = base;
    if (sh) y += sh * 0.5 * (1 - y) * (1 - y);   // + brightens shadows
    if (hi) y += hi * 0.5 * y * y;               // + brightens / - recovers highlights
    if (wh) y += wh * 0.30 * y * y;              // whites: endpoint at the top
    if (bl) y -= bl * 0.30 * (1 - y) * (1 - y);  // blacks: endpoint at the bottom
    y = clamp01(y);
    if (con) y = clamp01(0.5 + (y - 0.5) * (1 + con * 0.9)); // S-curve about mid-grey

    if (base > 1e-5) {
      const k = y / base;
      r = clamp01(r * k);
      g = clamp01(g * k);
      b = clamp01(b * k);
    } else {
      r = g = b = y;
    }
    lum[i] = 0.2126 * r + 0.7152 * g + 0.0722 * b;

    if (vib || sat) {
      const avg = (r + g + b) / 3;
      const mx = Math.max(r, g, b);
      const mn = Math.min(r, g, b);
      const chroma = mx > 0 ? (mx - mn) / mx : 0;
      const amount = sat + vib * (1 - chroma);
      if (amount) {
        r = clamp01(r + (r - avg) * amount);
        g = clamp01(g + (g - avg) * amount);
        b = clamp01(b + (b - avg) * amount);
      }
    }
    data[o] = Math.round(r * 255);
    data[o + 1] = Math.round(g * 255);
    data[o + 2] = Math.round(b * 255);
  }

  if (clarity) {
    const radius = Math.max(2, Math.round(Math.min(width, height) * 0.01));
    const blurred = boxBlurField(lum, width, height, radius);
    for (let i = 0; i < n; i += 1) {
      const detail = (lum[i] - blurred[i]) * clarity * 1.2;
      const o = i * 4;
      data[o] = Math.max(0, Math.min(255, data[o] + detail * 255));
      data[o + 1] = Math.max(0, Math.min(255, data[o + 1] + detail * 255));
      data[o + 2] = Math.max(0, Math.min(255, data[o + 2] + detail * 255));
    }
  }
  return data;
}

export default { developImageData, DEVELOP_DEFAULTS };
