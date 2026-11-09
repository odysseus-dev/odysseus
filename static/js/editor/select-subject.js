// Select Subject — one-click subject selection.
//
// This is an offline, deterministic heuristic (no network, no AI): it
// estimates the background colour from the image border, scores every cell
// by how far it is from that background (plus a small centre bias), splits
// the image with Otsu's threshold, keeps the largest connected component,
// and upsamples the result into a soft selection mask. It is a saliency
// approximation of Photoshop's on-device Select Subject, not the ML model.

function otsuThreshold(hist, total) {
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * hist[i];
  let sumB = 0;
  let wB = 0;
  let best = 0;
  let threshold = 0;
  for (let i = 0; i < 256; i++) {
    wB += hist[i];
    if (!wB) continue;
    const wF = total - wB;
    if (!wF) break;
    sumB += i * hist[i];
    const mB = sumB / wB;
    const mF = (sum - sumB) / wF;
    const between = wB * wF * (mB - mF) * (mB - mF);
    if (between > best) {
      best = between;
      threshold = i;
    }
  }
  return threshold;
}

const MAX_CHANNEL_DIST = 441.6729559300637; // sqrt(3 * 255^2)

export function computeSubjectMask(data, width, height, options = {}) {
  if (!data || width < 2 || height < 2) return null;
  const maxDim = Math.max(32, Math.min(512, Number(options.maxDim) || 200));
  const scale = Math.min(1, maxDim / Math.max(width, height));
  const sw = Math.max(2, Math.round(width * scale));
  const sh = Math.max(2, Math.round(height * scale));
  const cellW = width / sw;
  const cellH = height / sh;

  // Average source pixels into a small grid.
  const cells = new Float32Array(sw * sh * 3);
  for (let sy = 0; sy < sh; sy++) {
    const y0 = Math.floor(sy * cellH);
    const y1 = Math.max(y0 + 1, Math.min(height, Math.floor((sy + 1) * cellH)));
    for (let sx = 0; sx < sw; sx++) {
      const x0 = Math.floor(sx * cellW);
      const x1 = Math.max(x0 + 1, Math.min(width, Math.floor((sx + 1) * cellW)));
      let r = 0;
      let g = 0;
      let b = 0;
      let n = 0;
      for (let y = y0; y < y1; y++) {
        let idx = (y * width + x0) * 4;
        for (let x = x0; x < x1; x++, idx += 4) {
          r += data[idx];
          g += data[idx + 1];
          b += data[idx + 2];
          n++;
        }
      }
      const o = (sy * sw + sx) * 3;
      if (n) {
        cells[o] = r / n;
        cells[o + 1] = g / n;
        cells[o + 2] = b / n;
      }
    }
  }

  // Background colour = average of the border cells.
  let br = 0;
  let bg = 0;
  let bb = 0;
  let bn = 0;
  const addCell = (sx, sy) => {
    const o = (sy * sw + sx) * 3;
    br += cells[o];
    bg += cells[o + 1];
    bb += cells[o + 2];
    bn++;
  };
  for (let sx = 0; sx < sw; sx++) {
    addCell(sx, 0);
    addCell(sx, sh - 1);
  }
  for (let sy = 1; sy < sh - 1; sy++) {
    addCell(0, sy);
    addCell(sw - 1, sy);
  }
  br /= bn;
  bg /= bn;
  bb /= bn;

  // Saliency = distance from the background (+ a small centre bias).
  const sal = new Float32Array(sw * sh);
  let maxSal = 0;
  for (let sy = 0; sy < sh; sy++) {
    for (let sx = 0; sx < sw; sx++) {
      const o = (sy * sw + sx) * 3;
      const dr = cells[o] - br;
      const dg = cells[o + 1] - bg;
      const db = cells[o + 2] - bb;
      const dist = Math.sqrt(dr * dr + dg * dg + db * db) / MAX_CHANNEL_DIST;
      const nx = ((sx + 0.5) / sw) * 2 - 1;
      const ny = ((sy + 0.5) / sh) * 2 - 1;
      const center = Math.max(0, 1 - (nx * nx + ny * ny));
      const v = dist + center * 0.12;
      sal[sy * sw + sx] = v;
      if (v > maxSal) maxSal = v;
    }
  }

  // No clear foreground -> nothing to select.
  if (maxSal < 0.12) return null;

  const hist = new Int32Array(256);
  for (let i = 0; i < sal.length; i++) {
    hist[Math.min(255, Math.round((sal[i] / maxSal) * 255))]++;
  }
  const threshold = (otsuThreshold(hist, sal.length) / 255) * maxSal;

  const bin = new Uint8Array(sw * sh);
  for (let i = 0; i < sal.length; i++) bin[i] = sal[i] >= threshold ? 1 : 0;

  // Keep the largest connected component (4-neighbour flood fill).
  const seen = new Uint8Array(sw * sh);
  let best = null;
  for (let start = 0; start < bin.length; start++) {
    if (!bin[start] || seen[start]) continue;
    const group = [];
    const stack = [start];
    seen[start] = 1;
    while (stack.length) {
      const cell = stack.pop();
      group.push(cell);
      const cx = cell % sw;
      const cy = (cell - cx) / sw;
      if (cx > 0) { const nn = cell - 1; if (bin[nn] && !seen[nn]) { seen[nn] = 1; stack.push(nn); } }
      if (cx < sw - 1) { const nn = cell + 1; if (bin[nn] && !seen[nn]) { seen[nn] = 1; stack.push(nn); } }
      if (cy > 0) { const nn = cell - sw; if (bin[nn] && !seen[nn]) { seen[nn] = 1; stack.push(nn); } }
      if (cy < sh - 1) { const nn = cell + sw; if (bin[nn] && !seen[nn]) { seen[nn] = 1; stack.push(nn); } }
    }
    if (!best || group.length > best.length) best = group;
  }
  if (!best || best.length < 0.005 * sw * sh) return null;

  const small = document.createElement('canvas');
  small.width = sw;
  small.height = sh;
  const sctx = small.getContext('2d');
  const out = sctx.createImageData(sw, sh);
  for (const cell of best) {
    const o = cell * 4;
    out.data[o] = 255;
    out.data[o + 1] = 255;
    out.data[o + 2] = 255;
    out.data[o + 3] = 255;
  }
  sctx.putImageData(out, 0, 0);

  const mask = document.createElement('canvas');
  mask.width = width;
  mask.height = height;
  const mctx = mask.getContext('2d');
  mctx.imageSmoothingEnabled = true;
  mctx.imageSmoothingQuality = 'high';
  mctx.drawImage(small, 0, 0, width, height);
  return mask;
}
