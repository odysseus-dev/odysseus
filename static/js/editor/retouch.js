// Pixel-level retouch helpers for the editor. Pure/DOM-free so they can be
// unit-tested in Node. Each function takes/returns plain typed arrays so the
// caller (galleryEditor.js control ops) owns the canvas plumbing.
//
// Covers: Camera-Raw-style auto tone/colour/white-balance, frequency-separation
// skin smoothing (keeps texture), blemish removal, teeth whitening, and a
// mean-absolute-delta metric used to detect imperceptible edits.

function clamp255(v) {
  return v < 0 ? 0 : v > 255 ? 255 : v;
}

// Separable box blur of an RGB(A) buffer, returning a new Uint8ClampedArray.
export function boxBlurRGB(src, width, height, radius) {
  const r = Math.max(0, Math.round(radius));
  const out = new Uint8ClampedArray(src);
  if (!r) return out;
  const tmp = new Uint8ClampedArray(src.length);
  const win = r * 2 + 1;
  // horizontal
  for (let y = 0; y < height; y++) {
    const row = y * width * 4;
    for (let c = 0; c < 4; c++) {
      let sum = 0;
      for (let k = -r; k <= r; k++) sum += src[row + clamp255(0) * 0 + (Math.min(width - 1, Math.max(0, k)) * 4) + c];
      for (let x = 0; x < width; x++) {
        tmp[row + x * 4 + c] = sum / win;
        const add = clampX(x + r + 1, width) * 4 + c;
        const sub = clampX(x - r, width) * 4 + c;
        sum += src[row + add] - src[row + sub];
      }
    }
  }
  // vertical
  for (let x = 0; x < width; x++) {
    for (let c = 0; c < 4; c++) {
      let sum = 0;
      for (let k = -r; k <= r; k++) sum += tmp[clampY(k, height) * width * 4 + x * 4 + c];
      for (let y = 0; y < height; y++) {
        out[y * width * 4 + x * 4 + c] = sum / win;
        const add = clampY(y + r + 1, height) * width * 4 + x * 4 + c;
        const sub = clampY(y - r, height) * width * 4 + x * 4 + c;
        sum += tmp[add] - tmp[sub];
      }
    }
  }
  return out;
}

function clampX(v, w) { return v < 0 ? 0 : v >= w ? w - 1 : v; }
function clampY(v, h) { return v < 0 ? 0 : v >= h ? h - 1 : v; }

function toGray(data, i) { return 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]; }

// Camera-Raw-style auto analysis: per-channel clipped black/white points,
// gray-world + white-patch blended white balance gains, and an exposure/contrast
// suggestion. Returns a plain params object.
export function analyzeAutoTone(data, width, height, opts = {}) {
  const clipPct = opts.clipPct != null ? opts.clipPct : 0.006;
  const hist = [new Uint32Array(256), new Uint32Array(256), new Uint32Array(256)];
  const lumHist = new Uint32Array(256);
  let sr = 0, sg = 0, sb = 0, total = 0;
  let midR = 0, midG = 0, midB = 0, midN = 0;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 8) continue;
    const r = data[i], g = data[i + 1], b = data[i + 2];
    hist[0][r]++; hist[1][g]++; hist[2][b]++;
    const luma = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
    lumHist[luma]++;
    sr += r; sg += g; sb += b;
    // gray-world only on mid-tone, reasonably-coloured pixels (skip near-black /
    // near-white / neon) so dark or vivid images do not flip the whole cast.
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    const sat = mx <= 0 ? 0 : (mx - mn) / mx;
    if (luma > 40 && luma < 225 && sat < 0.6) { midR += r; midG += g; midB += b; midN++; }
    total++;
  }
  if (!total) return { levels: { r: [0, 255], g: [0, 255], b: [0, 255] }, gains: { r: 1, g: 1, b: 1 }, brightness: 0, contrast: 1 };
  const clip = Math.max(1, Math.floor(total * clipPct));
  const pctLo = (h) => { let acc = 0, l = 0; for (; l < 256; l++) { acc += h[l]; if (acc >= clip) break; } return l; };
  const pctHi = (h) => { let acc = 0, hh = 255; for (; hh >= 0; hh--) { acc += h[hh]; if (acc >= clip) break; } return hh; };
  const lumLo = pctLo(lumHist), lumHi = pctHi(lumHist);
  const mr0 = sr / total, mg0 = sg / total, mb0 = sb / total;
  const mean = (0.299 * mr0 + 0.587 * mg0 + 0.114 * mb0);
  const lowKey = mean < 70; // very dark photos: never stretch (amplifies noise -> false colour)
  const lo = [0, 0, 0], hi = [255, 255, 255];
  if (!lowKey) {
    for (let c = 0; c < 3; c++) {
      let cl = pctLo(hist[c]), ch = pctHi(hist[c]);
      if (ch - cl < 24) { const mid = (cl + ch) / 2; cl = Math.max(0, Math.round(mid - 32)); ch = Math.min(255, Math.round(mid + 32)); }
      // blend each channel's points toward the luminance points and bound the
      // divergence, so channels cannot be stretched independently (avoids the
      // heavy colour casts / false colour that independent per-channel levels cause).
      cl = Math.round(cl * 0.5 + lumLo * 0.5);
      ch = Math.round(ch * 0.5 + lumHi * 0.5);
      cl = Math.max(lumLo - 24, Math.min(cl, lumLo + 24));
      ch = Math.max(lumHi - 24, Math.min(ch, lumHi + 24));
      let l = Math.max(0, Math.min(cl, 250));
      let h2 = Math.min(255, Math.max(ch, l + 8));
      // bound the stretch to ~1.5x so dark/noisy regions are not amplified.
      if (h2 - l < 170) { const mid = (l + h2) / 2; l = Math.max(0, Math.round(mid - 85)); h2 = Math.min(255, Math.round(mid + 85)); }
      lo[c] = l; hi[c] = h2;
    }
  }
  // gentle gray-world white balance (mid-tones only), blended toward identity.
  let gains = { r: 1, g: 1, b: 1 };
  if (!lowKey && midN > total * 0.05) {
    const mr = midR / midN, mg = midG / midN, mb = midB / midN;
    const gray = (mr + mg + mb) / 3 || 1;
    const blendW = 0.4;
    gains = {
      r: clampRange(1 + (gray / (mr || 1) - 1) * blendW, 0.96, 1.04),
      g: clampRange(1 + (gray / (mg || 1) - 1) * blendW, 0.96, 1.04),
      b: clampRange(1 + (gray / (mb || 1) - 1) * blendW, 0.96, 1.04),
    };
  }
  let brightness = clampRange((128 - mean) / 255, -0.08, 0.08);
  let gamma = 1;
  const dyn = ((hi[0] - lo[0]) + (hi[1] - lo[1]) + (hi[2] - lo[2])) / (3 * 255);
  let contrast = (!lowKey && dyn < 0.9) ? clampRange(1 + (0.92 - dyn) * 0.35, 1.0, 1.15) : 1.0;
  if (lowKey) {
    // Dark photos: a gamma lift is visible but monotonic (255 stays 255) so it
    // barely clips highlights, unlike a levels stretch which amplifies noise.
    const target = Math.min(100, Math.max(52, mean * 1.7 + 16));
    gamma = clampRange(Math.log(target / 255) / Math.log(Math.max(6, mean) / 255), 0.7, 0.95);
    brightness = 0;
    contrast = 1.06;
  }
  return { levels: { r: [lo[0], hi[0]], g: [lo[1], hi[1]], b: [lo[2], hi[2]] }, gains, brightness, contrast, gamma };
}

function clampRange(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

export function applyAutoTone(data, width, height, params) {
  const out = new Uint8ClampedArray(data.length);
  const L = params.levels || { r: [0, 255], g: [0, 255], b: [0, 255] };
  const G = params.gains || { r: 1, g: 1, b: 1 };
  const br = (params.brightness || 0) * 255;
  const ct = params.contrast || 1;
  const gm = params.gamma || 1;
  const lut = [0, 1, 2].map((c) => {
    const key = c === 0 ? 'r' : c === 1 ? 'g' : 'b';
    const [lo, hi] = L[key], gain = G[key];
    const span = Math.max(1, hi - lo);
    const t = new Uint8ClampedArray(256);
    for (let v = 0; v < 256; v++) {
      let x = ((v - lo) / span) * 255 * gain;
      if (gm !== 1) x = 255 * Math.pow(Math.max(0, Math.min(1, x / 255)), gm);
      x = (x - 128) * ct + 128 + br;
      t[v] = clamp255(Math.round(x));
    }
    return t;
  });
  for (let i = 0; i < data.length; i += 4) {
    out[i] = lut[0][data[i]];
    out[i + 1] = lut[1][data[i + 1]];
    out[i + 2] = lut[2][data[i + 2]];
    out[i + 3] = data[i + 3];
  }
  return out;
}

// YCrCb skin detection, optionally intersected with a region mask (alpha bytes).
export function skinMaskFromData(data, width, height, regionMask = null) {
  const mask = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3];
    if (a < 8) continue;
    if (regionMask && regionMask[i + 3] < 64) continue;
    const r = data[i], g = data[i + 1], b = data[i + 2];
    let y = 0.299 * r + 0.587 * g + 0.114 * b;
    const cr = (r - y) * 0.713 + 128;
    const cb = (b - y) * 0.564 + 128;
    const skin = cr >= 133 && cr <= 176 && cb >= 77 && cb <= 130 && y > 40;
    if (skin) { mask[i] = 255; mask[i + 1] = 255; mask[i + 2] = 255; mask[i + 3] = 255; }
  }
  return mask;
}

// Frequency separation: blur the LOW frequencies further and add back the HIGH
// frequencies (pores/texture), while explicitly preserving the mean luminance
// and colour of the masked area so skin is never brightened or washed out.
// Skin smoothing that keeps texture: blend the original with a lightly blurred
// copy inside the mask. The blur is locally mean-preserving and clamped so a
// pixel can only ever be DARKENED (never brightened), which prevents the
// ghost-white / washed-out artifacts a reconstruction-based split produced.
export function frequencySeparationSmooth(src, width, height, mask, opts = {}) {
  const r = Math.max(2, Math.min(24, Math.round(opts.smoothRadius || 8)));
  const opacity = opts.opacity != null ? opts.opacity : 0.35;  // gentle default (Photoshop Skin Smoothing is subtle)
  const maxDown = opts.maxDown != null ? opts.maxDown : 18;
  const maxUp = opts.maxUp != null ? opts.maxUp : 0;
  const blur = boxBlurRGB(src, width, height, r);
  const out = new Uint8ClampedArray(src);
  for (let i = 0; i < src.length; i += 4) {
    const m = mask ? mask[i + 3] / 255 : 1;
    if (m <= 0) continue;
    const w = m * opacity;
    for (let c = 0; c < 3; c++) {
      let d = (blur[i + c] - src[i + c]) * w;
      if (d > maxUp) d = maxUp;               // tiny lift to soften dark pores, never a whitening blow
      else if (d < -maxDown) d = -maxDown;    // may soften bright blotches a little
      out[i + c] = clamp255(src[i + c] + d);
    }
  }
  return out;
}

// Build an exclusion mask for facial features that must never be smoothed:
// dark features (eyes/brows/lashes/nostrils) and red lips, relative to the
// region's mean luminance. Callers dilate this and subtract it from the skin mask.
export function featureExclusionMask(data, width, height, regionMask = null) {
  const mask = new Uint8ClampedArray(width * height * 4);
  let sum = 0, n = 0;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 8) continue;
    if (regionMask && regionMask[i + 3] < 64) continue;
    sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    n++;
  }
  const meanY = n ? sum / n : 128;
  const darkT = Math.max(30, meanY * 0.66);
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 8) continue;
    if (regionMask && regionMask[i + 3] < 64) continue;
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const y = 0.299 * r + 0.587 * g + 0.114 * b;
    const dark = y < darkT;
    const red = (r - g) > 26 && (r - b) > 22 && r > 90;
    if (dark || red) { mask[i] = 255; mask[i + 1] = 255; mask[i + 2] = 255; mask[i + 3] = 255; }
  }
  return mask;
}

// Separable dilation of a mask's alpha channel (grows the feature exclusion).
export function dilateAlpha(mask, width, height, radius) {
  const r = Math.max(0, Math.round(radius));
  const out = new Uint8ClampedArray(mask);
  if (!r) return out;
  const a = new Uint8ClampedArray(width * height);
  for (let p = 0; p < width * height; p++) a[p] = mask[p * 4 + 3];
  const tmp = new Uint8ClampedArray(width * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let m = 0;
      for (let k = -r; k <= r; k++) { const xx = x + k; if (xx >= 0 && xx < width) { const v = a[y * width + xx]; if (v > m) m = v; } }
      tmp[y * width + x] = m;
    }
  }
  for (let x = 0; x < width; x++) {
    for (let y = 0; y < height; y++) {
      let m = 0;
      for (let k = -r; k <= r; k++) { const yy = y + k; if (yy >= 0 && yy < height) { const v = tmp[yy * width + x]; if (v > m) m = v; } }
      const p = y * width + x;
      out[p * 4] = 255; out[p * 4 + 1] = 255; out[p * 4 + 2] = 255; out[p * 4 + 3] = m;
    }
  }
  return out;
}

// Subtract one mask's alpha (exclude) from another (base).
export function subtractMask(base, exclude) {
  const out = new Uint8ClampedArray(base);
  if (!exclude) return out;
  for (let i = 0; i < base.length; i += 4) {
    const ex = (exclude[i + 3] || 0) / 255;
    if (ex > 0) out[i + 3] = clamp255(base[i + 3] * (1 - ex));
  }
  return out;
}

// Replace small high-contrast spots (blemishes) with the local neighbourhood
// value, masked.
export function blemishRemoval(src, width, height, mask, opts = {}) {
  const radius = Math.max(2, Math.round(opts.radius || 4));
  const threshold = opts.threshold != null ? opts.threshold : 16;
  const smooth = boxBlurRGB(src, width, height, radius);
  const out = new Uint8ClampedArray(src);
  for (let i = 0; i < src.length; i += 4) {
    if (data_alpha(src, i) < 8) continue;
    const m = mask ? mask[i + 3] : 255;
    if (m < 64) continue;
    const dev = (Math.abs(src[i] - smooth[i]) + Math.abs(src[i + 1] - smooth[i + 1]) + Math.abs(src[i + 2] - smooth[i + 2])) / 3;
    if (dev >= threshold) {
      out[i] = smooth[i]; out[i + 1] = smooth[i + 1]; out[i + 2] = smooth[i + 2];
    }
  }
  return out;
}

function data_alpha(d, i) { return d[i + 3]; }

// Reduce yellow cast + lift brightness inside a teeth mask.
export function teethWhitening(src, width, height, mask, opts = {}) {
  const desat = opts.desat != null ? opts.desat : 0.7;
  const bright = opts.bright != null ? opts.bright : 22;
  // Yellow-only whitening: only pixels that actually look yellowed (teeth) are
  // desaturated and brightened. Neutral brightening is deliberately NOT applied,
  // because without a precise teeth mask it whitens cheeks/nose as a block.
  const out = new Uint8ClampedArray(src);
  for (let i = 0; i < src.length; i += 4) {
    const m = mask ? mask[i + 3] / 255 : 1;
    if (m <= 0) continue;
    const r = src[i], g = src[i + 1], b = src[i + 2];
    const y = 0.299 * r + 0.587 * g + 0.114 * b;
    const yellow = Math.max(0, Math.min(1, (Math.min(r, g) - b) / 70));
    const w = m * yellow;
    if (w <= 0) continue;
    for (let c = 0; c < 3; c++) {
      const desatV = src[i + c] * (1 - desat * w) + y * (desat * w);
      out[i + c] = clamp255(desatV + bright * w);
    }
  }
  return out;
}

// Separable box blur of a single-channel Float32 buffer (edge-clamped).
function boxBlurFloat(src, width, height, radius) {
  const r = Math.max(0, Math.round(radius));
  const out = new Float32Array(src.length);
  if (!r) { out.set(src); return out; }
  const tmp = new Float32Array(src.length);
  const win = r * 2 + 1;
  for (let y = 0; y < height; y++) {
    const row = y * width;
    let sum = 0;
    for (let k = -r; k <= r; k++) sum += src[row + Math.min(width - 1, Math.max(0, k))];
    for (let x = 0; x < width; x++) {
      tmp[row + x] = sum / win;
      sum += src[row + clampX(x + r + 1, width)] - src[row + clampX(x - r, width)];
    }
  }
  for (let x = 0; x < width; x++) {
    let sum = 0;
    for (let k = -r; k <= r; k++) sum += tmp[clampY(k, height) * width + x];
    for (let y = 0; y < height; y++) {
      out[y * width + x] = sum / win;
      sum += tmp[clampY(y + r + 1, height) * width + x] - tmp[clampY(y - r, height) * width + x];
    }
  }
  return out;
}

// Edge-aware local contrast ("clarity"). A guided filter (self-guided, per
// channel) produces the base layer: at high-contrast edges the guided filter
// follows the edge (variance >> eps => a -> 1 => q -> I), so the detail layer
// is ~0 there. On top of that a gradient-based edge mask suppresses any
// remaining detail right at strong edges (the guided base can still overshoot a
// little at a step transition), so there is NO overshoot/halo (unlike a
// large-radius unsharp). The detail delta is clamped as a final safety net.
// Alpha is preserved.
export function guidedClarity(src, width, height, opts = {}) {
  const out = new Uint8ClampedArray(src);
  const amount = Number.isFinite(Number(opts.amount)) ? Number(opts.amount) : 20;
  const strength = Math.max(-1, Math.min(2, amount / 100));
  const radius = Math.max(1, Math.round(Number(opts.radius) || 30));
  const clamp = Math.max(0, Number.isFinite(Number(opts.clamp)) ? Number(opts.clamp) : 10);
  const eps = Number.isFinite(Number(opts.eps)) ? Number(opts.eps) : 625;
  const n = width * height;
  if (strength === 0 || n <= 0) return out;

  const guidedBase = (get) => {
    const I = new Float32Array(n);
    const II = new Float32Array(n);
    for (let p = 0; p < n; p++) { const v = get(p); I[p] = v; II[p] = v * v; }
    const meanI = boxBlurFloat(I, width, height, radius);
    const meanII = boxBlurFloat(II, width, height, radius);
    const a = new Float32Array(n);
    const b = new Float32Array(n);
    for (let p = 0; p < n; p++) {
      const varI = Math.max(0, meanII[p] - meanI[p] * meanI[p]);
      const ai = varI / (varI + eps);
      a[p] = ai;
      b[p] = meanI[p] * (1 - ai);
    }
    const meanA = boxBlurFloat(a, width, height, radius);
    const meanB = boxBlurFloat(b, width, height, radius);
    const res = new Float32Array(n);
    for (let p = 0; p < n; p++) res[p] = meanA[p] * I[p] + meanB[p];
    return res;
  };

  // Edge weight derived from the guided filter's own variance term: where the
  // local luminance variance is high (a strong edge) the guided base is close
  // to the source and we suppress the detail entirely, so there is no overshoot
  // / halo at edges. The variance map is blurred over the same radius so the
  // suppression covers the edge flank.
  const gray = new Float32Array(n);
  const grayII = new Float32Array(n);
  for (let p = 0, i = 0; p < n; p++, i += 4) { const v = src[i] * 0.299 + src[i + 1] * 0.587 + src[i + 2] * 0.114; gray[p] = v; grayII[p] = v * v; }
  const meanG = boxBlurFloat(gray, width, height, radius);
  const meanGII = boxBlurFloat(grayII, width, height, radius);
  const edgeVar = Number.isFinite(Number(opts.edgeVariance)) ? Number(opts.edgeVariance) : 220;
  const weight = new Float32Array(n);
  for (let p = 0; p < n; p++) {
    const varG = Math.max(0, meanGII[p] - meanG[p] * meanG[p]);
    weight[p] = edgeVar / (varG + edgeVar);
  }

  const qR = guidedBase(p => src[p * 4]);
  const qG = guidedBase(p => src[p * 4 + 1]);
  const qB = guidedBase(p => src[p * 4 + 2]);
  for (let p = 0, i = 0; p < n; p++, i += 4) {
    const w = weight[p];
    let dr = (src[i] - qR[p]) * strength * w;
    let dg = (src[i + 1] - qG[p]) * strength * w;
    let db = (src[i + 2] - qB[p]) * strength * w;
    dr = dr > clamp ? clamp : dr < -clamp ? -clamp : dr;
    dg = dg > clamp ? clamp : dg < -clamp ? -clamp : dg;
    db = db > clamp ? clamp : db < -clamp ? -clamp : db;
    out[i] = src[i] + dr;
    out[i + 1] = src[i + 1] + dg;
    out[i + 2] = src[i + 2] + db;
    out[i + 3] = src[i + 3];
  }
  return out;
}

// Mean absolute RGB delta in 0..1, used to flag imperceptible edits.
export function meanAbsDelta(a, b, width, height) {
  const len = Math.min(a.length, b.length);
  let sum = 0, n = 0;
  for (let i = 0; i < len; i += 4) {
    sum += Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]);
    n += 3;
  }
  return n ? sum / (n * 255) : 0;
}

export default {
  boxBlurRGB, analyzeAutoTone, applyAutoTone, skinMaskFromData,
  frequencySeparationSmooth, blemishRemoval, teethWhitening, meanAbsDelta,
  guidedClarity,
};
