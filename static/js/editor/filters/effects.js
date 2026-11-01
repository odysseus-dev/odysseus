// Phase 8 filter renderers. Contract matches filters/blur.js:
//   renderer(snapCanvas, params, dstCtx)
// snapCanvas is the pre-filter snapshot, params is the slider value
// object, dstCtx is the layer 2D context to draw the result into.

function readSource(snap) {
  const ctx = snap.getContext('2d');
  return ctx.getImageData(0, 0, snap.width, snap.height);
}

function clamp255(n) {
  return n < 0 ? 0 : n > 255 ? 255 : n;
}

function clampIndex(n, max) {
  return n < 0 ? 0 : n > max ? max : n;
}

function convolve(img, kernel, divisor = 1, bias = 0, amount = 1) {
  const { width: w, height: h } = img;
  const src = img.data;
  const out = new Uint8ClampedArray(src.length);
  const side = Math.round(Math.sqrt(kernel.length));
  const half = Math.floor(side / 2);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      let r = 0; let g = 0; let b = 0;
      for (let ky = 0; ky < side; ky += 1) {
        for (let kx = 0; kx < side; kx += 1) {
          const sy = Math.min(h - 1, Math.max(0, y + ky - half));
          const sx = Math.min(w - 1, Math.max(0, x + kx - half));
          const si = (sy * w + sx) * 4;
          const k = kernel[ky * side + kx];
          r += src[si] * k;
          g += src[si + 1] * k;
          b += src[si + 2] * k;
        }
      }
      const di = (y * w + x) * 4;
      const or = r / divisor + bias;
      const og = g / divisor + bias;
      const ob = b / divisor + bias;
      out[di] = clamp255(src[di] * (1 - amount) + or * amount);
      out[di + 1] = clamp255(src[di + 1] * (1 - amount) + og * amount);
      out[di + 2] = clamp255(src[di + 2] * (1 - amount) + ob * amount);
      out[di + 3] = src[di + 3];
    }
  }
  return new ImageData(out, w, h);
}

export function addNoise(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const amount = Math.max(0, (v.amount ?? 20)) * 2.55;
  const mono = v.monochrome !== false;
  for (let i = 0; i < p.length; i += 4) {
    if (p[i + 3] === 0) continue;
    if (mono) {
      const n = (Math.random() - 0.5) * amount;
      p[i] = clamp255(p[i] + n);
      p[i + 1] = clamp255(p[i + 1] + n);
      p[i + 2] = clamp255(p[i + 2] + n);
    } else {
      p[i] = clamp255(p[i] + (Math.random() - 0.5) * amount);
      p[i + 1] = clamp255(p[i + 1] + (Math.random() - 0.5) * amount);
      p[i + 2] = clamp255(p[i + 2] + (Math.random() - 0.5) * amount);
    }
  }
  dst.putImageData(img, 0, 0);
}

export function despeckle(snap, v, dst) {
  dst.putImageData(convolve(readSource(snap), [1, 1, 1, 1, 1, 1, 1, 1, 1], 9), 0, 0);
}

export function pixelate(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const size = Math.max(2, Math.round(v.size ?? 12));
  for (let by = 0; by < h; by += size) {
    for (let bx = 0; bx < w; bx += size) {
      let r = 0; let g = 0; let b = 0; let a = 0; let n = 0;
      for (let y = by; y < Math.min(h, by + size); y += 1) {
        for (let x = bx; x < Math.min(w, bx + size); x += 1) {
          const i = (y * w + x) * 4;
          r += p[i]; g += p[i + 1]; b += p[i + 2]; a += p[i + 3]; n += 1;
        }
      }
      r /= n; g /= n; b /= n; a /= n;
      for (let y = by; y < Math.min(h, by + size); y += 1) {
        for (let x = bx; x < Math.min(w, bx + size); x += 1) {
          const i = (y * w + x) * 4;
          p[i] = r; p[i + 1] = g; p[i + 2] = b; p[i + 3] = a;
        }
      }
    }
  }
  dst.putImageData(img, 0, 0);
}

export function crystallize(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const size = Math.max(3, Math.round(v.size ?? 12));
  const seeds = [];
  for (let y = 0; y < h; y += size) {
    for (let x = 0; x < w; x += size) seeds.push([x + Math.random() * size, y + Math.random() * size]);
  }
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      let best = null; let bestD = Infinity;
      for (const s of seeds) {
        const d = (s[0] - x) ** 2 + (s[1] - y) ** 2;
        if (d < bestD) { bestD = d; best = s; }
      }
      const si = (Math.min(h - 1, Math.round(best[1])) * w + Math.min(w - 1, Math.round(best[0]))) * 4;
      const di = (y * w + x) * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

export function emboss(snap, v, dst) {
  const amount = (v.amount ?? 100) / 100;
  dst.putImageData(convolve(readSource(snap), [-2, -1, 0, -1, 1, 1, 0, 1, 2], 1, 128, amount), 0, 0);
}

export function findEdges(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const out = new Uint8ClampedArray(p.length);
  const lum = (i) => 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const x0 = Math.max(0, x - 1); const x1 = Math.min(w - 1, x + 1);
      const y0 = Math.max(0, y - 1); const y1 = Math.min(h - 1, y + 1);
      const gx = lum((y * w + x1) * 4) - lum((y * w + x0) * 4);
      const gy = lum((y1 * w + x) * 4) - lum((y0 * w + x) * 4);
      const e = clamp255(255 - Math.hypot(gx, gy));
      const i = (y * w + x) * 4;
      out[i] = e; out[i + 1] = e; out[i + 2] = e; out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

export function solarize(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const threshold = v.threshold ?? 128;
  for (let i = 0; i < p.length; i += 4) {
    if (p[i] > threshold) p[i] = 255 - p[i];
    if (p[i + 1] > threshold) p[i + 1] = 255 - p[i + 1];
    if (p[i + 2] > threshold) p[i + 2] = 255 - p[i + 2];
  }
  dst.putImageData(img, 0, 0);
}

export function twirl(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const cx = w / 2; const cy = h / 2;
  const radius = Math.min(w, h) / 2;
  const angle = (v.angle ?? 90) * Math.PI / 180;
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const dx = x - cx; const dy = y - cy;
      const r = Math.hypot(dx, dy);
      let sx = x; let sy = y;
      if (r < radius) {
        const t = angle * (1 - r / radius);
        const cos = Math.cos(t); const sin = Math.sin(t);
        sx = Math.round(cx + dx * cos - dy * sin);
        sy = Math.round(cy + dx * sin + dy * cos);
      }
      sx = Math.min(w - 1, Math.max(0, sx)); sy = Math.min(h - 1, Math.max(0, sy));
      const si = (sy * w + sx) * 4; const di = (y * w + x) * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

function radialWarp(snap, dst, fn) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const cx = w / 2; const cy = h / 2;
  const radius = Math.min(w, h) / 2;
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const dx = x - cx; const dy = y - cy;
      const r = Math.hypot(dx, dy);
      let sx = x; let sy = y;
      if (r < radius && r > 0) {
        const nr = fn(r / radius) * radius;
        sx = Math.round(cx + (dx / r) * nr);
        sy = Math.round(cy + (dy / r) * nr);
      }
      sx = Math.min(w - 1, Math.max(0, sx)); sy = Math.min(h - 1, Math.max(0, sy));
      const si = (sy * w + sx) * 4; const di = (y * w + x) * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

export function pinch(snap, v, dst) {
  const amt = (v.amount ?? 50) / 100;
  radialWarp(snap, dst, (t) => t ** (1 + amt));
}

export function spherize(snap, v, dst) {
  const amt = (v.amount ?? 50) / 100;
  radialWarp(snap, dst, (t) => Math.asin(Math.min(1, t)) / (Math.PI / 2) * (1 - amt) + t * amt);
}

export function ripple(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const amount = v.amount ?? 10;
  const wavelength = Math.max(2, v.wavelength ?? 40);
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const sx = Math.min(w - 1, Math.max(0, Math.round(x + amount * Math.sin((2 * Math.PI * y) / wavelength))));
      const sy = Math.min(h - 1, Math.max(0, Math.round(y + amount * Math.cos((2 * Math.PI * x) / wavelength))));
      const si = (sy * w + sx) * 4; const di = (y * w + x) * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

function valueNoise(w, h, cell, seed, cellY = cell) {
  const gw = Math.ceil(w / cell) + 1;
  const gh = Math.ceil(h / cellY) + 1;
  const grid = new Float32Array(gw * gh);
  let s = seed;
  const rand = () => {
    s = (s * 1664525 + 1013904223) & 0xffffffff;
    return (s >>> 8) / 0xffffff;
  };
  for (let i = 0; i < grid.length; i += 1) grid[i] = rand();
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const gx = x / cell; const gy = y / cellY;
      const x0 = Math.floor(gx); const y0 = Math.floor(gy);
      const fx = gx - x0; const fy = gy - y0;
      const a = grid[y0 * gw + x0];
      const b = grid[y0 * gw + Math.min(gw - 1, x0 + 1)];
      const c = grid[Math.min(gh - 1, y0 + 1) * gw + x0];
      const d = grid[Math.min(gh - 1, y0 + 1) * gw + Math.min(gw - 1, x0 + 1)];
      const u = fx * fx * (3 - 2 * fx); const t = fy * fy * (3 - 2 * fy);
      out[y * w + x] = a * (1 - u) * (1 - t) + b * u * (1 - t) + c * (1 - u) * t + d * u * t;
    }
  }
  return out;
}

export function clouds(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const scale = Math.max(4, v.scale ?? 64);
  const seed = (v.seed ?? 1);
  const n1 = valueNoise(w, h, scale, seed);
  const n2 = valueNoise(w, h, scale / 2, seed + 7);
  const n3 = valueNoise(w, h, scale / 4, seed + 13);
  for (let i = 0, px = 0; i < p.length; i += 4, px += 1) {
    const n = Math.round(255 * (0.5 * n1[px] + 0.3 * n2[px] + 0.2 * n3[px]));
    const blend = 0.6;
    p[i] = clamp255(p[i] * (1 - blend) + n * blend);
    p[i + 1] = clamp255(p[i + 1] * (1 - blend) + n * blend);
    p[i + 2] = clamp255(p[i + 2] * (1 - blend) + n * blend);
  }
  dst.putImageData(img, 0, 0);
}

export function sharpen(snap, v, dst) {
  const amount = Math.max(0, Math.min(3, (v.amount ?? 60) / 100));
  dst.putImageData(convolve(readSource(snap), [0, -1, 0, -1, 5, -1, 0, -1, 0], 1, 0, amount), 0, 0);
}

export function sharpenEdges(snap, v, dst) {
  const amount = Math.max(0, Math.min(3, (v.amount ?? 60) / 100));
  dst.putImageData(convolve(readSource(snap), [-1, -1, -1, -1, 9, -1, -1, -1, -1], 1, 0, amount), 0, 0);
}

export function unsharpMask(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const amount = (v.amount ?? 80) / 100;
  const radius = Math.max(1, Math.round(v.radius ?? 2));
  const threshold = v.threshold ?? 0;
  const blurred = new Uint8ClampedArray(p.length);
  const { width: w, height: h } = img;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      let r = 0; let g = 0; let b = 0; let n = 0;
      for (let dy = -radius; dy <= radius; dy += 1) {
        for (let dx = -radius; dx <= radius; dx += 1) {
          const sy = Math.min(h - 1, Math.max(0, y + dy));
          const sx = Math.min(w - 1, Math.max(0, x + dx));
          const si = (sy * w + sx) * 4;
          r += p[si]; g += p[si + 1]; b += p[si + 2]; n += 1;
        }
      }
      const di = (y * w + x) * 4;
      blurred[di] = r / n; blurred[di + 1] = g / n; blurred[di + 2] = b / n;
    }
  }
  for (let i = 0; i < p.length; i += 4) {
    for (let c = 0; c < 3; c += 1) {
      const diff = p[i + c] - blurred[i + c];
      if (Math.abs(diff) >= threshold) p[i + c] = clamp255(p[i + c] + diff * amount);
    }
  }
  dst.putImageData(img, 0, 0);
}

// Custom 3x3 convolution (Photoshop Filter > Other > Custom). Reuses the
// module's `convolve` helper; divisor falls back to the kernel sum so an
// all-zero kernel that would divide by zero behaves as a no-op.
export function customKernel(snap, v, dst) {
  const k = [v.k0, v.k1, v.k2, v.k3, v.k4, v.k5, v.k6, v.k7, v.k8].map(n => Number(n) || 0);
  const sum = k.reduce((a, b) => a + b, 0);
  const divisor = Number(v.divisor) || sum || 1;
  const bias = Number(v.offset) || 0;
  dst.putImageData(convolve(readSource(snap), k, divisor, bias, 1), 0, 0);
}

// Morphological dilate (max) / erode (min) over RGB, computed with two
// separable passes (horizontal then vertical) so the cost is O(r) rather
// than O(r^2). Alpha is carried through unchanged. Deterministic.
function morphology(snap, radius, mode) {
  const img = readSource(snap);
  const { width: w, height: h } = img;
  const src = img.data;
  const r = Math.max(0, Math.round(Number(radius) || 0));
  if (r === 0) return img;
  const isMax = mode === 'max';
  const pass = (input, horizontal) => {
    const res = new Uint8ClampedArray(input.length);
    for (let y = 0; y < h; y += 1) {
      for (let x = 0; x < w; x += 1) {
        let r0 = isMax ? 0 : 255;
        let g0 = isMax ? 0 : 255;
        let b0 = isMax ? 0 : 255;
        for (let k = -r; k <= r; k += 1) {
          const sx = horizontal ? clampIndex(x + k, w - 1) : x;
          const sy = horizontal ? y : clampIndex(y + k, h - 1);
          const i = (sy * w + sx) * 4;
          const rr = input[i]; const gg = input[i + 1]; const bb = input[i + 2];
          if (isMax) {
            if (rr > r0) r0 = rr;
            if (gg > g0) g0 = gg;
            if (bb > b0) b0 = bb;
          } else {
            if (rr < r0) r0 = rr;
            if (gg < g0) g0 = gg;
            if (bb < b0) b0 = bb;
          }
        }
        const di = (y * w + x) * 4;
        res[di] = r0; res[di + 1] = g0; res[di + 2] = b0; res[di + 3] = input[di + 3];
      }
    }
    return res;
  };
  const out = pass(pass(src, true), false);
  for (let i = 0; i < src.length; i += 4) out[i + 3] = src[i + 3];
  return new ImageData(out, w, h);
}

export function maximum(snap, v, dst) {
  dst.putImageData(morphology(snap, v.radius ?? 4, 'max'), 0, 0);
}

export function minimum(snap, v, dst) {
  dst.putImageData(morphology(snap, v.radius ?? 4, 'min'), 0, 0);
}

// Median noise reduction: per-channel median over a (2r+1) window, so a
// radius of 1 is the classic 3x3 median. Deterministic.
export function median(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const r = Math.max(1, Math.min(20, Math.round(Number(v.radius) || 1)));
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const rr = []; const gg = []; const bb = [];
      for (let dy = -r; dy <= r; dy += 1) {
        const sy = clampIndex(y + dy, h - 1);
        for (let dx = -r; dx <= r; dx += 1) {
          const sx = clampIndex(x + dx, w - 1);
          const i = (sy * w + sx) * 4;
          rr.push(p[i]); gg.push(p[i + 1]); bb.push(p[i + 2]);
        }
      }
      rr.sort((a, b) => a - b); gg.sort((a, b) => a - b); bb.sort((a, b) => a - b);
      const m = rr.length >> 1;
      const di = (y * w + x) * 4;
      out[di] = rr[m]; out[di + 1] = gg[m]; out[di + 2] = bb[m]; out[di + 3] = p[di + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Polar Coordinates (Filter > Distort). mode 0 / undefined maps the
// rectangular image into a disc (angle -> x, radius -> y); any other value
// maps a disc back to a rectangle. Pure nearest-neighbour remap.
export function polarCoordinates(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const cx = w / 2; const cy = h / 2;
  const toPolar = Math.round(Number(v.mode) || 0) === 0;
  const maxR = Math.min(w, h) / 2;
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      let sx; let sy;
      if (toPolar) {
        const dx = x - cx; const dy = y - cy;
        const r = Math.hypot(dx, dy);
        const ang = Math.atan2(dy, dx);
        sx = Math.round(((ang + Math.PI) / (2 * Math.PI)) * (w - 1));
        sy = Math.round(Math.min(1, r / maxR) * (h - 1));
      } else {
        const ang = (x / w) * 2 * Math.PI - Math.PI;
        const r = (y / h) * maxR;
        sx = Math.round(cx + r * Math.cos(ang));
        sy = Math.round(cy + r * Math.sin(ang));
      }
      sx = clampIndex(sx, w - 1); sy = clampIndex(sy, h - 1);
      const si = (sy * w + sx) * 4; const di = (y * w + x) * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Difference Clouds (Filter > Render): the layered value noise from
// `clouds`, combined with the source using a per-pixel absolute difference.
// Reuses the same deterministic `valueNoise` grid so a fixed seed is stable.
export function differenceClouds(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const scale = Math.max(4, v.scale ?? 64);
  const seed = (v.seed ?? 1);
  const n1 = valueNoise(w, h, scale, seed);
  const n2 = valueNoise(w, h, scale / 2, seed + 7);
  const n3 = valueNoise(w, h, scale / 4, seed + 13);
  for (let i = 0, px = 0; i < p.length; i += 4, px += 1) {
    const n = Math.round(255 * (0.5 * n1[px] + 0.3 * n2[px] + 0.2 * n3[px]));
    p[i] = clamp255(Math.abs(p[i] - n));
    p[i + 1] = clamp255(Math.abs(p[i + 1] - n));
    p[i + 2] = clamp255(Math.abs(p[i + 2] - n));
  }
  dst.putImageData(img, 0, 0);
}

// Parse #rgb / #rrggbb into an [r, g, b] triple. Tolerates short hex and a
// missing '#', defaulting malformed input to black so a render never NaN's.
function hexToRgb(hex) {
  let s = String(hex ?? '').replace('#', '').trim();
  if (s.length === 3) s = s.split('').map(c => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(s)) s = '000000';
  const n = parseInt(s, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// Replace Color (Image > Adjustments). Recolours every pixel within
// `fuzziness` of the `from` colour toward `to`, preserving alpha and leaving
// distant pixels untouched. Pure per-pixel Euclidean RGB distance; no
// randomness, so the result is stable across preview and commit.
export function replaceColor(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const from = hexToRgb(v.from ?? '#000000');
  const to = hexToRgb(v.to ?? '#ffffff');
  const fuzz = Math.max(0, Math.min(100, Number(v.fuzziness ?? 30))) / 100;
  const strength = Math.max(0, Math.min(100, Number(v.strength ?? 100))) / 100;
  const tolerance = Math.max(1, fuzz * 441.67);
  for (let i = 0; i < p.length; i += 4) {
    if (p[i + 3] === 0) continue;
    const dr = p[i] - from[0];
    const dg = p[i + 1] - from[1];
    const db = p[i + 2] - from[2];
    const dist = Math.sqrt(dr * dr + dg * dg + db * db);
    if (dist > tolerance) continue;
    const weight = (1 - dist / tolerance) * strength;
    p[i] = clamp255(p[i] + (to[0] - p[i]) * weight);
    p[i + 1] = clamp255(p[i + 1] + (to[1] - p[i + 1]) * weight);
    p[i + 2] = clamp255(p[i + 2] + (to[2] - p[i + 2]) * weight);
  }
  dst.putImageData(img, 0, 0);
}

// Lens Flare (Filter > Render). Deterministic additive glow: a bright core
// plus a halo and three tinted ghosts laid along the axis through the image
// centre. Position/size/brightness are the only knobs; no randomness.
export function lensFlare(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const cx = (Math.max(0, Math.min(100, Number(v.x ?? 50))) / 100) * w;
  const cy = (Math.max(0, Math.min(100, Number(v.y ?? 50))) / 100) * h;
  const brightness = Math.max(0, Math.min(200, Number(v.brightness ?? 100))) / 100;
  const size = Math.max(0.05, Math.min(2, Number(v.size ?? 50) / 100));
  const base = Math.min(w, h) * size;
  const axisX = cx - w / 2;
  const axisY = cy - h / 2;
  const glows = [
    { x: cx, y: cy, r: base * 0.10, gain: 2.2 * brightness, color: [255, 250, 230] },
    { x: cx, y: cy, r: base * 0.55, gain: 0.5 * brightness, color: [120, 170, 255] },
    { x: w / 2 - axisX * 0.6, y: h / 2 - axisY * 0.6, r: base * 0.18, gain: 0.45 * brightness, color: [255, 180, 120] },
    { x: w / 2 - axisX * 1.3, y: h / 2 - axisY * 1.3, r: base * 0.12, gain: 0.4 * brightness, color: [140, 255, 180] },
    { x: w / 2 - axisX * 2.1, y: h / 2 - axisY * 2.1, r: base * 0.22, gain: 0.3 * brightness, color: [200, 140, 255] },
  ];
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      let addR = 0; let addG = 0; let addB = 0;
      for (const g of glows) {
        const dx = x - g.x; const dy = y - g.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > g.r * g.r * 9) continue;
        const fall = Math.exp(-d2 / (2 * g.r * g.r));
        addR += g.color[0] * fall * g.gain;
        addG += g.color[1] * fall * g.gain;
        addB += g.color[2] * fall * g.gain;
      }
      const i = (y * w + x) * 4;
      p[i] = clamp255(p[i] + addR);
      p[i + 1] = clamp255(p[i + 1] + addG);
      p[i + 2] = clamp255(p[i + 2] + addB);
    }
  }
  dst.putImageData(img, 0, 0);
}

// Fibers (Filter > Render). Vertically stretched value noise blended over the
// source. Reuses the module `valueNoise` grid with a non-square cell so the
// streaks run up and down. Seed-stable and deterministic.
export function fibers(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const variance = Math.max(1, Math.min(64, Math.round(Number(v.variance ?? 16))));
  const strength = Math.max(0, Math.min(100, Number(v.strength ?? 60))) / 100;
  const seed = Math.round(Number(v.seed ?? 1));
  const n = valueNoise(w, h, variance, seed, variance * 12);
  for (let i = 0, px = 0; i < p.length; i += 4, px += 1) {
    const streak = Math.round(255 * n[px]);
    p[i] = clamp255(p[i] * (1 - strength) + streak * strength);
    p[i + 1] = clamp255(p[i + 1] * (1 - strength) + streak * strength);
    p[i + 2] = clamp255(p[i + 2] * (1 - strength) + streak * strength);
  }
  dst.putImageData(img, 0, 0);
}

// Oil Paint (Filter > Stylize). For each pixel, pick the most common quantised
// luminance among its neighbours, then average the RGB of pixels in that
// intensity bin — the classic bristle-radius oil-paint pass. Deterministic.
export function oilPaint(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const radius = Math.max(1, Math.min(8, Math.round(Number(v.radius ?? 3))));
  const levels = Math.max(2, Math.min(32, Math.round(Number(v.levels ?? 8))));
  const out = new Uint8ClampedArray(p.length);
  const hist = new Int32Array(levels);
  const sumR = new Float32Array(levels);
  const sumG = new Float32Array(levels);
  const sumB = new Float32Array(levels);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      hist.fill(0); sumR.fill(0); sumG.fill(0); sumB.fill(0);
      for (let dy = -radius; dy <= radius; dy += 1) {
        const sy = clampIndex(y + dy, h - 1);
        for (let dx = -radius; dx <= radius; dx += 1) {
          const sx = clampIndex(x + dx, w - 1);
          const i = (sy * w + sx) * 4;
          const lum = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
          const bin = Math.min(levels - 1, Math.floor((lum / 255) * levels));
          hist[bin] += 1; sumR[bin] += p[i]; sumG[bin] += p[i + 1]; sumB[bin] += p[i + 2];
        }
      }
      let best = 0; let bestCount = -1;
      for (let b = 0; b < levels; b += 1) {
        if (hist[b] > bestCount) { bestCount = hist[b]; best = b; }
      }
      const count = Math.max(1, hist[best]);
      const di = (y * w + x) * 4;
      out[di] = sumR[best] / count;
      out[di + 1] = sumG[best] / count;
      out[di + 2] = sumB[best] / count;
      out[di + 3] = p[di + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Lens Correction (Filter > Distort). Parameter-driven radial barrel /
// pincushion remap (the same nearest-neighbour warp used by the Distort
// filters) followed by a vignette. Fully deterministic.
export function lensCorrection(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const cx = w / 2; const cy = h / 2;
  const radius = Math.min(w, h) / 2;
  const k = Math.max(-0.9, Math.min(0.9, Number(v.distortion ?? 0) / 100));
  const vig = Math.max(-1, Math.min(1, Number(v.vignette ?? 0) / 100));
  const maxR = Math.hypot(cx, cy) || 1;
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const dx = x - cx; const dy = y - cy;
      const r = Math.hypot(dx, dy);
      let sx = x; let sy = y;
      if (r > 0 && k !== 0) {
        const t = r / radius;
        const nr = t * (1 + k * t * t) * radius;
        sx = Math.round(cx + (dx / r) * nr);
        sy = Math.round(cy + (dy / r) * nr);
      }
      sx = clampIndex(sx, w - 1); sy = clampIndex(sy, h - 1);
      const si = (sy * w + sx) * 4; const di = (y * w + x) * 4;
      let scale = 1;
      if (vig) { const t = Math.min(1, r / maxR); scale = 1 + vig * t * t; }
      out[di] = clamp255(p[si] * scale);
      out[di + 1] = clamp255(p[si + 1] * scale);
      out[di + 2] = clamp255(p[si + 2] * scale);
      out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Diffuse (Filter > Stylize). Each pixel is displaced by a small random offset
// within `radius`, driven by a deterministic LCG so the preview and the
// committed result are identical for a given seed.
export function diffuse(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const radius = Math.max(1, Math.min(50, Math.round(Number(v.radius ?? 4))));
  const seed = Math.round(Number(v.seed ?? 1)) || 1;
  const out = new Uint8ClampedArray(p.length);
  let s = seed >>> 0;
  const rand = () => {
    s = (s * 1664525 + 1013904223) & 0xffffffff;
    return (s >>> 8) / 0xffffff;
  };
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const dx = Math.round((rand() - 0.5) * 2 * radius);
      const dy = Math.round((rand() - 0.5) * 2 * radius);
      const sx = clampIndex(x + dx, w - 1);
      const sy = clampIndex(y + dy, h - 1);
      const si = (sy * w + sx) * 4; const di = (y * w + x) * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Ocean Ripple (Filter > Distort). Two overlapping sine displacement waves
// offset the sample position along x and y, giving the rippled-water look.
// `size` is the wavelength and `magnitude` the displacement. Deterministic.
export function oceanRipple(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const magnitude = Math.max(0, Math.min(50, Number(v.magnitude ?? 8)));
  const size = Math.max(4, Number(v.size ?? 24));
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const waveA = Math.sin((2 * Math.PI * (x + y)) / size);
      const waveB = Math.cos((2 * Math.PI * (x - y)) / (size * 1.5));
      const sx = clampIndex(Math.round(x + magnitude * waveA), w - 1);
      const sy = clampIndex(Math.round(y + magnitude * waveB), h - 1);
      const si = (sy * w + sx) * 4; const di = (y * w + x) * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Glass (Filter > Distort). A tiled, voronoi-ish displacement: two seeded
// value-noise fields (reusing the module `valueNoise` grid) smoothly nudge each
// sample within `tile`-sized cells, refracting the image like frosted glass.
// Deterministic for a fixed seed.
export function glass(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const tile = Math.max(4, Math.min(200, Math.round(Number(v.tile ?? 16))));
  const amount = Math.max(0, Math.min(60, Number(v.amount ?? 10)));
  const seed = Math.round(Number(v.seed ?? 1));
  const nx = valueNoise(w, h, tile, seed);
  const ny = valueNoise(w, h, tile, seed + 41);
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const px = y * w + x;
      const sx = clampIndex(Math.round(x + (nx[px] - 0.5) * 2 * amount), w - 1);
      const sy = clampIndex(Math.round(y + (ny[px] - 0.5) * 2 * amount), h - 1);
      const si = (sy * w + sx) * 4; const di = px * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Mosaic Tiles (Filter > Pixelate). The classic mosaic pass (average each cell)
// plus darker grout lines on the cell borders. Fully deterministic.
export function mosaicTiles(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const size = Math.max(3, Math.min(200, Math.round(Number(v.size ?? 12))));
  const grout = Math.max(0, Math.min(10, Math.round(Number(v.grout ?? 1))));
  for (let by = 0; by < h; by += size) {
    for (let bx = 0; bx < w; bx += size) {
      let r = 0; let g = 0; let b = 0; let a = 0; let n = 0;
      for (let y = by; y < Math.min(h, by + size); y += 1) {
        for (let x = bx; x < Math.min(w, bx + size); x += 1) {
          const i = (y * w + x) * 4;
          r += p[i]; g += p[i + 1]; b += p[i + 2]; a += p[i + 3]; n += 1;
        }
      }
      r /= n; g /= n; b /= n; a /= n;
      for (let y = by; y < Math.min(h, by + size); y += 1) {
        for (let x = bx; x < Math.min(w, bx + size); x += 1) {
          const i = (y * w + x) * 4;
          const edge = (x - bx) < grout || (y - by) < grout;
          const k = edge ? 0.55 : 1;
          p[i] = r * k; p[i + 1] = g * k; p[i + 2] = b * k; p[i + 3] = a;
        }
      }
    }
  }
  dst.putImageData(img, 0, 0);
}

// Wind (Filter > Stylize). A directional streak: samples are smeared along
// `angle` with a linearly decaying weight, so highlights trail across the
// image. Deterministic; angle 0 smears to the right.
export function wind(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const strength = Math.max(1, Math.min(64, Math.round(Number(v.strength ?? 20))));
  const angle = (Number(v.angle ?? 0) * Math.PI) / 180;
  const dx = Math.cos(angle);
  const dy = Math.sin(angle);
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      let r = 0; let g = 0; let b = 0; let a = 0; let weight = 0;
      for (let k = 0; k < strength; k += 1) {
        const sx = clampIndex(Math.round(x + dx * k), w - 1);
        const sy = clampIndex(Math.round(y + dy * k), h - 1);
        const si = (sy * w + sx) * 4;
        const wgt = (strength - k) / strength;
        r += p[si] * wgt; g += p[si + 1] * wgt; b += p[si + 2] * wgt; a += p[si + 3] * wgt;
        weight += wgt;
      }
      const di = (y * w + x) * 4;
      out[di] = r / weight; out[di + 1] = g / weight; out[di + 2] = b / weight; out[di + 3] = a / weight;
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Trace Contour (Filter > Stylize). Marks pixels whose luminance crosses
// `level` against the right/bottom neighbour, leaving a thin dark outline on a
// white field (alpha preserved). Fully deterministic.
export function traceContour(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const level = Math.max(0, Math.min(255, Math.round(Number(v.level ?? 128))));
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const ri = (y * w + Math.min(w - 1, x + 1)) * 4;
      const bi = (Math.min(h - 1, y + 1) * w + x) * 4;
      const luma = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
      const lumaR = 0.299 * p[ri] + 0.587 * p[ri + 1] + 0.114 * p[ri + 2];
      const lumaB = 0.299 * p[bi] + 0.587 * p[bi + 1] + 0.114 * p[bi + 2];
      const edge = (luma > level) !== (lumaR > level) || (luma > level) !== (lumaB > level);
      const tone = edge ? 0 : 255;
      out[i] = tone; out[i + 1] = tone; out[i + 2] = tone; out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Halftone Pattern (Filter > Sketch). A deterministic dot screen: each
// `size`-pixel cell renders a centred dot whose radius grows with the cell's
// darkness, on a white field. Alpha is preserved.
export function halftonePattern(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const size = Math.max(2, Math.min(64, Math.round(Number(v.size ?? 6))));
  const out = new Uint8ClampedArray(p.length);
  for (let by = 0; by < h; by += size) {
    for (let bx = 0; bx < w; bx += size) {
      const ex = Math.min(w, bx + size);
      const ey = Math.min(h, by + size);
      let sum = 0; let n = 0;
      for (let y = by; y < ey; y += 1) {
        for (let x = bx; x < ex; x += 1) {
          const i = (y * w + x) * 4;
          sum += 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
          n += 1;
        }
      }
      const dark = 1 - (sum / n) / 255;
      const radius = (size / 2) * Math.sqrt(dark);
      const cx = bx + size / 2;
      const cy = by + size / 2;
      for (let y = by; y < ey; y += 1) {
        for (let x = bx; x < ex; x += 1) {
          const i = (y * w + x) * 4;
          const d = Math.hypot(x + 0.5 - cx, y + 0.5 - cy);
          const tone = d <= radius ? 0 : 255;
          out[i] = tone; out[i + 1] = tone; out[i + 2] = tone; out[i + 3] = p[i + 3];
        }
      }
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Reticulation (Filter > Sketch). A clumpy film-grain pass: the luminance is
// pushed through a seeded value-noise field and re-quantised, leaving a grainy
// reticulated monochrome. Deterministic for a fixed seed.
export function reticulation(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const density = Math.max(1, Math.min(100, Math.round(Number(v.density ?? 30))));
  const seed = Math.round(Number(v.seed ?? 1)) || 1;
  const grain = valueNoise(w, h, 3, seed);
  const blobs = valueNoise(w, h, 14, seed + 29);
  for (let i = 0, px = 0; i < p.length; i += 4, px += 1) {
    const gray = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
    const n = (grain[px] - 0.5) * density + (blobs[px] - 0.5) * density * 1.6;
    const q = Math.round(clamp255(gray + n) / 32) * 32;
    p[i] = q; p[i + 1] = q; p[i + 2] = q;
  }
  dst.putImageData(img, 0, 0);
}

// Shear (Filter > Distort). Each row is displaced horizontally along a fixed
// S-curve, so the image leans like a sheared card. Deterministic; `amount` is
// the peak shift in pixels.
export function shear(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const amount = Math.max(-200, Math.min(200, Math.round(Number(v.amount ?? 40))));
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    const t = h > 1 ? y / (h - 1) : 0;
    const shift = Math.round((amount * Math.sin(2 * Math.PI * (t - 0.5))) / 2);
    for (let x = 0; x < w; x += 1) {
      const sx = clampIndex(x - shift, w - 1);
      const si = (y * w + sx) * 4; const di = (y * w + x) * 4;
      out[di] = p[si]; out[di + 1] = p[si + 1]; out[di + 2] = p[si + 2]; out[di + 3] = p[si + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Note Paper (Filter > Sketch). A grayscale emboss plus a seeded grain, giving
// the raised, fibrous look of notepaper. Deterministic for a fixed seed.
export function notePaper(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const relief = Math.max(1, Math.min(10, Number(v.relief ?? 3)));
  const seed = Math.round(Number(v.seed ?? 1)) || 1;
  const grain = valueNoise(w, h, 4, seed + 7);
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const iUp = (Math.max(0, y - 1) * w + x) * 4;
      const iDn = (Math.min(h - 1, y + 1) * w + x) * 4;
      const luma = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
      const slope = (0.299 * p[iDn] + 0.587 * p[iDn + 1] + 0.114 * p[iDn + 2])
        - (0.299 * p[iUp] + 0.587 * p[iUp + 1] + 0.114 * p[iUp + 2]);
      const n = (grain[y * w + x] - 0.5) * 40;
      const tone = clamp255(190 + (luma - 128) * 0.35 + slope * relief + n);
      out[i] = tone; out[i + 1] = tone; out[i + 2] = tone; out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Charcoal (Filter > Sketch). Smudged inverse-luminance strokes on a light
// paper field: `thickness` widens the strokes, `detail` deepens them and
// `balance` shifts the whole sheet lighter or darker, over a seeded grain.
// Deterministic for a fixed seed; alpha is preserved.
export function charcoal(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const thickness = Math.max(1, Math.min(7, Math.round(Number(v.thickness ?? 2))));
  const detail = Math.max(0, Math.min(5, Math.round(Number(v.detail ?? 3))));
  const balance = Math.max(0, Math.min(100, Number(v.balance ?? 50)));
  const seed = Math.round(Number(v.seed ?? 1)) || 1;
  const grain = valueNoise(w, h, 3, seed);
  const lift = ((balance - 50) / 50) * 70;
  const out = new Uint8ClampedArray(p.length);
  for (let i = 0, px = 0; i < p.length; i += 4, px += 1) {
    const luma = (0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2]) / 255;
    const ink = Math.pow(1 - luma, 1 + detail * 0.4);
    const tone = clamp255(232 - ink * 255 * (1 + thickness * 0.14) + lift + (grain[px] - 0.5) * 34);
    out[i] = tone; out[i + 1] = tone; out[i + 2] = tone; out[i + 3] = p[i + 3];
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Chalk & Charcoal (Filter > Sketch). Presses charcoal into the darks and chalk
// into the lights, so shadows get soft black strokes and highlights get white
// ones; `charcoal` / `chalk` scale each area and `pressure` the grain.
// Deterministic for a fixed seed; alpha is preserved.
export function chalkCharcoal(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const charcoalArea = Math.max(0, Math.min(20, Math.round(Number(v.charcoal ?? 6))));
  const chalkArea = Math.max(0, Math.min(20, Math.round(Number(v.chalk ?? 6))));
  const pressure = Math.max(0, Math.min(5, Number(v.pressure ?? 1)));
  const seed = Math.round(Number(v.seed ?? 1)) || 1;
  const grain = valueNoise(w, h, 2, seed + 5);
  const out = new Uint8ClampedArray(p.length);
  for (let i = 0, px = 0; i < p.length; i += 4, px += 1) {
    const luma = (0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2]) / 255;
    const dark = Math.pow(1 - luma, 1 + charcoalArea * 0.15);
    const light = Math.pow(luma, 1 + chalkArea * 0.15);
    const n = (grain[px] - 0.5) * 40 * (0.5 + pressure);
    const tone = clamp255(200 + light * 55 * (1 + pressure * 0.2) - dark * 180 * (1 + pressure * 0.2) + n);
    out[i] = tone; out[i + 1] = tone; out[i + 2] = tone; out[i + 3] = p[i + 3];
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Graphic Pen (Filter > Sketch). Cross-hatches the darks with fine directional
// lines of `length` px running along `angle`, gated by the light/dark
// `balance`. Deterministic; alpha is preserved.
export function graphicPen(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const length = Math.max(1, Math.min(15, Math.round(Number(v.length ?? 6))));
  const angle = (Number(v.angle ?? 45) * Math.PI) / 180;
  const balance = Math.max(0, Math.min(100, Number(v.balance ?? 41))) / 100;
  const out = new Uint8ClampedArray(p.length);
  const ax = Math.cos(angle);
  const ay = Math.sin(angle);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const luma = (0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2]) / 255;
      const dark = 1 - luma;
      const proj = x * -ay + y * ax;
      const slot = ((proj % length) + length) % length;
      const width = dark * length * 0.9;
      const inked = dark > (1 - balance) && slot < width;
      const tone = inked ? 18 : 245;
      out[i] = tone; out[i + 1] = tone; out[i + 2] = tone; out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Photocopy (Filter > Sketch). A high-contrast two-tone threshold whose cutoff
// is set by `darkness`, with local edge contrast weighed by `detail`.
// Deterministic; alpha is preserved.
export function photocopy(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const detail = Math.max(1, Math.min(24, Math.round(Number(v.detail ?? 12))));
  const darkness = Math.max(1, Math.min(24, Math.round(Number(v.darkness ?? 12))));
  const cutoff = 255 * (1 - darkness / 32);
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const iL = (y * w + Math.max(0, x - 1)) * 4;
      const iR = (y * w + Math.min(w - 1, x + 1)) * 4;
      const luma = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
      const edge = Math.abs(
        (0.299 * p[iR] + 0.587 * p[iR + 1] + 0.114 * p[iR + 2])
        - (0.299 * p[iL] + 0.587 * p[iL + 1] + 0.114 * p[iL + 2]),
      );
      const tone = luma - edge * (detail / 12) < cutoff ? 12 : 243;
      out[i] = tone; out[i + 1] = tone; out[i + 2] = tone; out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Water Paper (Filter > Sketch). Smears the image along a seeded fibrous noise
// field (the wet-paper bleed) and re-levels it with `brightness` / `contrast`.
// Deterministic for a fixed seed; alpha is preserved.
export function waterPaper(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const fiber = Math.max(3, Math.min(50, Math.round(Number(v.fiber ?? 15))));
  const brightness = Math.max(0, Math.min(100, Number(v.brightness ?? 60)));
  const contrast = Math.max(0, Math.min(100, Number(v.contrast ?? 50)));
  const seed = Math.round(Number(v.seed ?? 1)) || 1;
  const cell = Math.max(1, Math.round(fiber / 5));
  const nx = valueNoise(w, h, cell, seed);
  const ny = valueNoise(w, h, cell, seed + 17);
  const gain = 0.5 + contrast / 100;
  const bias = (brightness - 50) * 1.6;
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0, px = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1, px += 1) {
      const sx = clampIndex(x + Math.round((nx[px] - 0.5) * fiber), w - 1);
      const sy = clampIndex(y + Math.round((ny[px] - 0.5) * fiber), h - 1);
      const si = (sy * w + sx) * 4;
      const luma = 0.299 * p[si] + 0.587 * p[si + 1] + 0.114 * p[si + 2];
      const tone = clamp255(128 + (luma - 128) * gain + bias);
      out[px * 4] = tone; out[px * 4 + 1] = tone; out[px * 4 + 2] = tone; out[px * 4 + 3] = p[px * 4 + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Sharpen More (Filter > Sharpen). A stronger fixed sharpen than Sharpen: the
// 4-neighbour Laplacian is weighted twice as heavily (centre 9), so edges gain
// roughly double the contrast. Deterministic; alpha is preserved.
export function sharpenMore(snap, v, dst) {
  const amount = Math.max(0, Math.min(3, (v.amount ?? 60) / 100));
  dst.putImageData(convolve(readSource(snap), [0, -2, 0, -2, 9, -2, 0, -2, 0], 1, 0, amount), 0, 0);
}

// Poster Edges (Filter > Artistic). Posterises each channel into `levels` flat
// bands and then subtracts the luminance edge gradient, giving flat colour
// areas ringed with dark outlines. Deterministic; alpha is preserved.
export function posterEdges(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const edge = Math.max(0, Math.min(10, Number(v.edge ?? 2)));
  const levels = Math.max(2, Math.min(16, Math.round(Number(v.levels ?? 5))));
  const step = 255 / (levels - 1);
  const luma = (i) => 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const xm = Math.max(0, x - 1); const xp = Math.min(w - 1, x + 1);
      const ym = Math.max(0, y - 1); const yp = Math.min(h - 1, y + 1);
      const gx = luma((y * w + xp) * 4) - luma((y * w + xm) * 4);
      const gy = luma((yp * w + x) * 4) - luma((ym * w + x) * 4);
      const ink = Math.hypot(gx, gy) * (edge / 2);
      for (let c = 0; c < 3; c += 1) {
        out[i + c] = clamp255(Math.round(p[i + c] / step) * step - ink);
      }
      out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Cutout (Filter > Artistic). Reduces the image to `levels` flat colour bands
// by posterising every channel, for a screen-printed decal look. Deterministic;
// alpha is preserved.
export function cutout(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const levels = Math.max(2, Math.min(16, Math.round(Number(v.levels ?? 5))));
  const step = 255 / (levels - 1);
  for (let i = 0; i < p.length; i += 4) {
    p[i] = Math.round(Math.round(p[i] / step) * step);
    p[i + 1] = Math.round(Math.round(p[i + 1] / step) * step);
    p[i + 2] = Math.round(Math.round(p[i + 2] / step) * step);
  }
  dst.putImageData(img, 0, 0);
}

// Ink Outlines (Filter > Brush Strokes). Darkens a Laplacian edge response into
// inky outlines over the original colours. Deterministic; alpha is preserved.
export function inkOutlines(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const dark = Math.max(0, Math.min(50, Number(v.dark ?? 20)));
  const luma = (x, y) => {
    const i = (y * w + x) * 4;
    return 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
  };
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const lap = 4 * luma(x, y) - luma(Math.max(0, x - 1), y) - luma(Math.min(w - 1, x + 1), y)
        - luma(x, Math.max(0, y - 1)) - luma(x, Math.min(h - 1, y + 1));
      const ink = clamp255(lap) * (dark / 25);
      out[i] = clamp255(p[i] - ink);
      out[i + 1] = clamp255(p[i + 1] - ink);
      out[i + 2] = clamp255(p[i + 2] - ink);
      out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Stained Glass (Filter > Texture). Partitions the image into jittered Voronoi
// cells from a seeded grid, fills each with its average colour and edges the
// cell boundaries with dark lead lines. Deterministic for a fixed seed; alpha
// is preserved.
export function stainedGlass(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const cell = Math.max(4, Math.min(64, Math.round(Number(v.cell ?? 12))));
  const border = Math.max(0, Math.min(8, Math.round(Number(v.border ?? 1))));
  const seed = Math.round(Number(v.seed ?? 1)) || 1;
  const cellW = Math.max(1, Math.ceil(w / cell));
  const cellH = Math.max(1, Math.ceil(h / cell));
  const n = cellW * cellH;
  const jx = new Float32Array(n);
  const jy = new Float32Array(n);
  let s = seed;
  const rand = () => { s = (s * 1664525 + 1013904223) & 0xffffffff; return (s >>> 8) / 0xffffff; };
  for (let i = 0; i < n; i += 1) { jx[i] = rand(); jy[i] = rand(); }
  const owner = new Int32Array(w * h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      let best = 0; let bd = Infinity;
      const cx = Math.floor(x / cell); const cy = Math.floor(y / cell);
      for (let oy = -1; oy <= 1; oy += 1) {
        for (let ox = -1; ox <= 1; ox += 1) {
          const gx = cx + ox; const gy = cy + oy;
          if (gx < 0 || gy < 0 || gx >= cellW || gy >= cellH) continue;
          const gi = gy * cellW + gx;
          const px = (gx + 0.2 + jx[gi] * 0.6) * cell;
          const py = (gy + 0.2 + jy[gi] * 0.6) * cell;
          const d = (px - x) * (px - x) + (py - y) * (py - y);
          if (d < bd) { bd = d; best = gi; }
        }
      }
      owner[y * w + x] = best;
    }
  }
  const sum = new Float32Array(n * 3);
  const cnt = new Uint32Array(n);
  for (let i = 0, px = 0; i < p.length; i += 4, px += 1) {
    const o = owner[px] * 3;
    sum[o] += p[i]; sum[o + 1] += p[i + 1]; sum[o + 2] += p[i + 2];
    cnt[owner[px]] += 1;
  }
  const out = new Uint8ClampedArray(p.length);
  for (let i = 0, px = 0; i < p.length; i += 4, px += 1) {
    const o = owner[px]; const c = cnt[o] || 1;
    const x = px % w; const y = (px / w) | 0;
    let isBorder = false;
    if (border > 0) {
      isBorder = (x > 0 && owner[px - 1] !== o) || (x < w - 1 && owner[px + 1] !== o)
        || (y > 0 && owner[px - w] !== o) || (y < h - 1 && owner[px + w] !== o);
    }
    if (isBorder) {
      out[i] = 12; out[i + 1] = 12; out[i + 2] = 12;
    } else {
      out[i] = sum[o] / c; out[i + 1] = sum[o + 1] / c; out[i + 2] = sum[o + 2] / c;
    }
    out[i + 3] = p[i + 3];
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Grain (Filter > Texture). Adds a deterministic monochrome grain: one value
// from a seeded LCG is added to all three channels, so the result is neutral
// and the preview matches the commit. Alpha is preserved.
export function grain(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const intensity = Math.max(0, Math.min(100, Number(v.intensity ?? 20)));
  const seed = Math.round(Number(v.seed ?? 1)) || 1;
  let s = seed >>> 0;
  const rand = () => { s = (s * 1664525 + 1013904223) & 0xffffffff; return (s >>> 8) / 0xffffff; };
  const amp = intensity * 1.28;
  for (let i = 0; i < p.length; i += 4) {
    const n = (rand() - 0.5) * 2 * amp;
    p[i] = clamp255(p[i] + n);
    p[i + 1] = clamp255(p[i + 1] + n);
    p[i + 2] = clamp255(p[i + 2] + n);
  }
  dst.putImageData(img, 0, 0);
}

// Texturizer (Filter > Texture). Lays a seeded value-noise relief over the
// image and shades it with a fixed top-left light, so the noise reads as an
// embossed canvas. Deterministic for a fixed seed; alpha is preserved.
export function texturizer(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const scale = Math.max(2, Math.min(64, Math.round(Number(v.scale ?? 8))));
  const relief = Math.max(0, Math.min(50, Number(v.relief ?? 4)));
  const seed = Math.round(Number(v.seed ?? 1));
  const n = valueNoise(w, h, scale, seed);
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const px = y * w + x;
      const xm = clampIndex(x - 1, w - 1); const xp = clampIndex(x + 1, w - 1);
      const ym = clampIndex(y - 1, h - 1); const yp = clampIndex(y + 1, h - 1);
      const d = (n[y * w + xp] - n[y * w + xm]) + (n[yp * w + x] - n[ym * w + x]);
      const shade = d * relief * 32;
      const i = px * 4;
      out[i] = clamp255(p[i] + shade);
      out[i + 1] = clamp255(p[i + 1] + shade);
      out[i + 2] = clamp255(p[i + 2] + shade);
      out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Patchwork (Filter > Texture). Averages every `size`-square tile and gives it
// a light top/left and dark bottom/right bevel, so the tiles look raised.
// Deterministic; alpha is preserved.
export function patchwork(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const size = Math.max(2, Math.min(200, Math.round(Number(v.size ?? 12))));
  const out = new Uint8ClampedArray(p.length);
  for (let ty = 0; ty < h; ty += size) {
    for (let tx = 0; tx < w; tx += size) {
      const xe = Math.min(w, tx + size); const ye = Math.min(h, ty + size);
      let r = 0; let g = 0; let b = 0; let n = 0;
      for (let y = ty; y < ye; y += 1) {
        for (let x = tx; x < xe; x += 1) {
          const i = (y * w + x) * 4;
          r += p[i]; g += p[i + 1]; b += p[i + 2]; n += 1;
        }
      }
      const ar = n ? r / n : 0; const ag = n ? g / n : 0; const ab = n ? b / n : 0;
      for (let y = ty; y < ye; y += 1) {
        for (let x = tx; x < xe; x += 1) {
          const i = (y * w + x) * 4;
          const bevel = (x === tx || y === ty) ? 20 : ((x === xe - 1 || y === ye - 1) ? -20 : 0);
          out[i] = clamp255(ar + bevel);
          out[i + 1] = clamp255(ag + bevel);
          out[i + 2] = clamp255(ab + bevel);
          out[i + 3] = p[i + 3];
        }
      }
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Craquelure (Filter > Texture). Traces the level-set contours of a seeded
// value-noise field and darkens where the surface crosses the midpoint, giving
// a network of hairline cracks. Deterministic; alpha is preserved.
export function craquelure(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const crack = Math.max(3, Math.min(64, Math.round(Number(v.crack ?? 10))));
  const depth = Math.max(0, Math.min(10, Number(v.depth ?? 3)));
  const seed = Math.round(Number(v.seed ?? 1));
  const n = valueNoise(w, h, crack, seed);
  const level = 0.5;
  const out = new Uint8ClampedArray(p.length);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const px = y * w + x;
      const right = y * w + Math.min(w - 1, x + 1);
      const down = Math.min(h - 1, y + 1) * w + x;
      const edge = (n[px] - level) * (n[right] - level) < 0
        || (n[px] - level) * (n[down] - level) < 0;
      const i = px * 4;
      const d = edge ? -depth * 12 : 0;
      out[i] = clamp255(p[i] + d);
      out[i + 1] = clamp255(p[i + 1] + d);
      out[i + 2] = clamp255(p[i + 2] + d);
      out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}

// Crosshatch (Filter > Brush Strokes). Draws two families of diagonal strokes
// (x+y and x-y) whose density follows the local darkness, so shadows fill in
// with crossed ink lines. Deterministic; alpha is preserved.
export function crosshatch(snap, v, dst) {
  const img = readSource(snap);
  const p = img.data;
  const { width: w, height: h } = img;
  const length = Math.max(3, Math.min(50, Math.round(Number(v.length ?? 8))));
  const sharpness = Math.max(1, Math.min(20, Number(v.sharpness ?? 6)));
  const strength = Math.max(0, Math.min(100, Number(v.strength ?? 50)));
  const out = new Uint8ClampedArray(p.length);
  const period = length * 2;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const luma = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
      const dark = 1 - luma / 255;
      const a = ((x + y) % period + period) % period;
      const b = ((x - y) % period + period) % period;
      const line = (a < length || b < length) ? 1 : 0;
      const ink = line * Math.pow(dark, sharpness / 2) * (strength / 100) * 200;
      out[i] = clamp255(p[i] - ink);
      out[i + 1] = clamp255(p[i + 1] - ink);
      out[i + 2] = clamp255(p[i + 2] - ink);
      out[i + 3] = p[i + 3];
    }
  }
  dst.putImageData(new ImageData(out, w, h), 0, 0);
}
