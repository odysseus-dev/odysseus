/**
 * Haze removal (dehaze) — a compact, offline dark-channel-prior implementation
 * with a guided-filter refined transmission map.
 *
 * The dark channel of a hazy photo is dominated by the atmospheric light, so the
 * haze can be estimated and removed from a single image:
 *   1. dark channel  D(x) = min over {R,G,B} of a small local window (min of the
 *      channels, then a separable minimum filter). The window is kept small
 *      (patch of ~7-15px) so it does not smear a rectangular halo around hard
 *      edges.
 *   2. atmospheric light  A  = the mean colour of the brightest 0.1% of the
 *      dark channel (a robust estimate; a single brightest cell is noisy).
 *   3. transmission  t(x) = 1 - omega * D(x) / max(A), clamped to >= t0 (0.1).
 *   4. refine t with a guided filter (He et al. 2010) using the luminance as the
 *      guide, so the map follows real edges instead of the min-filter blocks.
 *   5. recover  J = (I - A) / max(t, t0) + A.
 *
 * The dark channel, atmospheric light and guided filter run on a bounded working
 * copy (<= maxDim) and the refined transmission is up-sampled smoothly, so the
 * pass stays interactive on large documents. No network or AI; deterministic.
 */

function clamp(value, lo, hi) {
  return value < lo ? lo : value > hi ? hi : value;
}

/* Separable minimum filter (horizontal then vertical) over a Float array. */
function minFilter2D(values, width, height, radius) {
  if (radius < 1) return values.slice();
  const tmp = new Float32Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let min = Infinity;
      for (let d = -radius; d <= radius; d += 1) {
        const xx = x + d;
        if (xx < 0 || xx >= width) continue;
        const v = values[y * width + xx];
        if (v < min) min = v;
      }
      tmp[y * width + x] = min;
    }
  }
  const out = new Float32Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let min = Infinity;
      for (let d = -radius; d <= radius; d += 1) {
        const yy = y + d;
        if (yy < 0 || yy >= height) continue;
        const v = tmp[yy * width + x];
        if (v < min) min = v;
      }
      out[y * width + x] = min;
    }
  }
  return out;
}

/* O(N) mean (box) filter via a summed-area table. */
function boxFilter(src, width, height, radius) {
  const stride = width + 1;
  const integral = new Float64Array(stride * (height + 1));
  for (let y = 0; y < height; y += 1) {
    let rowSum = 0;
    for (let x = 0; x < width; x += 1) {
      rowSum += src[y * width + x];
      integral[(y + 1) * stride + (x + 1)] = integral[y * stride + (x + 1)] + rowSum;
    }
  }
  const out = new Float32Array(width * height);
  for (let y = 0; y < height; y += 1) {
    const y0 = Math.max(0, y - radius);
    const y1 = Math.min(height - 1, y + radius);
    for (let x = 0; x < width; x += 1) {
      const x0 = Math.max(0, x - radius);
      const x1 = Math.min(width - 1, x + radius);
      const area = (x1 - x0 + 1) * (y1 - y0 + 1);
      const sum = integral[(y1 + 1) * stride + (x1 + 1)]
        - integral[y0 * stride + (x1 + 1)]
        - integral[(y1 + 1) * stride + x0]
        + integral[y0 * stride + x0];
      out[y * width + x] = sum / area;
    }
  }
  return out;
}

/*
 * Guided filter (He, Sun, Tang 2010) — edge-preserving smoothing of `p` guided
 * by `guide`. Both arrays are 0..1; `eps` is the regularisation (1e-3 typical).
 */
function guidedFilter(guide, p, width, height, radius, eps) {
  const n = width * height;
  const meanGuide = boxFilter(guide, width, height, radius);
  const meanP = boxFilter(p, width, height, radius);
  const gg = new Float32Array(n);
  const gp = new Float32Array(n);
  for (let i = 0; i < n; i += 1) {
    gg[i] = guide[i] * guide[i];
    gp[i] = guide[i] * p[i];
  }
  const corrGG = boxFilter(gg, width, height, radius);
  const corrGP = boxFilter(gp, width, height, radius);
  const a = new Float32Array(n);
  const b = new Float32Array(n);
  for (let i = 0; i < n; i += 1) {
    const varG = corrGG[i] - meanGuide[i] * meanGuide[i];
    const covGP = corrGP[i] - meanGuide[i] * meanP[i];
    a[i] = covGP / (varG + eps);
    b[i] = meanP[i] - a[i] * meanGuide[i];
  }
  const meanA = boxFilter(a, width, height, radius);
  const meanB = boxFilter(b, width, height, radius);
  const q = new Float32Array(n);
  for (let i = 0; i < n; i += 1) q[i] = meanA[i] * guide[i] + meanB[i];
  return q;
}

export function dehazeImageData(src, width, height, options = {}) {
  const strength = clamp(Number(options.strength ?? 0.8), 0, 1);
  if (strength <= 0 || width < 2 || height < 2) return src;
  const omega = clamp(Number(options.omega ?? 0.95), 0, 1) * strength;
  // Small dark-channel patch (Photoshop-style 7-15px) keeps the halo tight.
  const patch = Math.max(3, Math.min(31, Math.round(Number(options.patch) || 15)));
  const guideRadius = Math.max(1, Math.round(Number(options.guideRadius) || 40));
  const eps = Math.max(1e-6, Number(options.eps) || 1e-3);
  const t0 = clamp(Number(options.t0 ?? 0.1), 0.02, 1);
  const maxDim = Math.max(16, Number(options.maxDim) || 1024);
  const scale = Math.min(1, maxDim / Math.max(width, height));
  const sw = Math.max(2, Math.round(width * scale));
  const sh = Math.max(2, Math.round(height * scale));
  // Radius that yields a ~patch-pixel window at the working resolution.
  const radius = Math.max(1, Math.round((patch - 1) / 2 * scale));

  // Block down-sample: the darkest per-pixel min(R,G,B) in each cell, its colour
  // (for the atmospheric light) and the mean luminance (the guided-filter guide).
  const dark = new Float32Array(sw * sh);
  const darkColor = new Float32Array(sw * sh * 3);
  const lum = new Float32Array(sw * sh);
  const bw = width / sw;
  const bh = height / sh;
  for (let sy = 0; sy < sh; sy += 1) {
    const y0 = Math.floor(sy * bh);
    const y1 = Math.min(height, Math.max(y0 + 1, Math.ceil((sy + 1) * bh)));
    for (let sx = 0; sx < sw; sx += 1) {
      const x0 = Math.floor(sx * bw);
      const x1 = Math.min(width, Math.max(x0 + 1, Math.ceil((sx + 1) * bw)));
      let best = 256;
      let br = 0;
      let bg = 0;
      let bb = 0;
      let lumSum = 0;
      let count = 0;
      for (let y = y0; y < y1; y += 1) {
        for (let x = x0; x < x1; x += 1) {
          const i = (y * width + x) * 4;
          const r = src[i];
          const g = src[i + 1];
          const b = src[i + 2];
          const mn = Math.min(r, g, b);
          if (mn < best) { best = mn; br = r; bg = g; bb = b; }
          lumSum += (r * 0.299 + g * 0.587 + b * 0.114) / 255;
          count += 1;
        }
      }
      const k = sy * sw + sx;
      dark[k] = best;
      darkColor[k * 3] = br;
      darkColor[k * 3 + 1] = bg;
      darkColor[k * 3 + 2] = bb;
      lum[k] = count ? lumSum / count : 0;
    }
  }

  const filtered = minFilter2D(dark, sw, sh, radius);

  // Atmospheric light: mean colour of the brightest 0.1% of the dark channel.
  const total = sw * sh;
  const topCount = Math.max(1, Math.round(total * 0.001));
  const order = new Int32Array(total);
  for (let k = 0; k < total; k += 1) order[k] = k;
  const ordered = Array.from(order).sort((a, b) => filtered[b] - filtered[a]);
  let ar = 0;
  let ag = 0;
  let ab = 0;
  for (let n = 0; n < topCount; n += 1) {
    const idx = ordered[n];
    ar += darkColor[idx * 3];
    ag += darkColor[idx * 3 + 1];
    ab += darkColor[idx * 3 + 2];
  }
  ar /= topCount;
  ag /= topCount;
  ab /= topCount;
  const aLum = Math.max(1, Math.max(ar, ag, ab));

  // Raw per-cell transmission (clamped to >= t0), then guided-filter refined.
  const rawT = new Float32Array(total);
  for (let k = 0; k < total; k += 1) {
    rawT[k] = clamp(1 - omega * (filtered[k] / aLum), t0, 1);
  }
  const refined = guidedFilter(lum, rawT, sw, sh, guideRadius, eps);

  const small = new Uint8ClampedArray(sw * sh * 4);
  for (let k = 0; k < sw * sh; k += 1) {
    const v = Math.round(clamp(refined[k], t0, 1) * 255);
    small[k * 4] = v;
    small[k * 4 + 1] = v;
    small[k * 4 + 2] = v;
    small[k * 4 + 3] = 255;
  }

  let tFull = null;
  if (typeof document !== 'undefined') {
    const sm = document.createElement('canvas');
    sm.width = sw;
    sm.height = sh;
    sm.getContext('2d').putImageData(new ImageData(small, sw, sh), 0, 0);
    const big = document.createElement('canvas');
    big.width = width;
    big.height = height;
    const bctx = big.getContext('2d');
    bctx.imageSmoothingEnabled = true;
    bctx.imageSmoothingQuality = 'high';
    bctx.drawImage(sm, 0, 0, width, height);
    tFull = bctx.getImageData(0, 0, width, height).data;
  }

  const out = new Uint8ClampedArray(src.length);
  for (let i = 0, p = 0; i < src.length; i += 4, p += 1) {
    const t = tFull ? Math.max(t0, tFull[p * 4] / 255) : 1;
    out[i] = clamp((src[i] - ar) / t + ar, 0, 255);
    out[i + 1] = clamp((src[i + 1] - ag) / t + ag, 0, 255);
    out[i + 2] = clamp((src[i + 2] - ab) / t + ab, 0, 255);
    out[i + 3] = src[i + 3];
  }
  return out;
}
