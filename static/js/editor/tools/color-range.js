// Color Range selection — pure helpers.

export function hexToRgb(hex) {
  const value = String(hex || '').trim().replace(/^#/, '');
  const full = value.length === 3
    ? value.split('').map(ch => ch + ch).join('')
    : value.padEnd(6, '0').slice(0, 6);
  const int = parseInt(full, 16);
  if (Number.isNaN(int)) return { r: 0, g: 0, b: 0 };
  return { r: (int >> 16) & 255, g: (int >> 8) & 255, b: int & 255 };
}

// Global (non-connected) color match. Returns a canvas whose alpha marks
// every pixel within `fuzziness` of `target` (RGBA squared-distance, the
// same scaling the magic wand uses for tolerance). Fully transparent
// source pixels are never selected.
export function colorRangeMask(srcData, width, height, target, fuzziness) {
  const out = document.createElement('canvas');
  out.width = width;
  out.height = height;
  const ctx = out.getContext('2d');
  const image = ctx.createImageData(width, height);
  const threshold = Math.max(0, Number(fuzziness) || 0) * 4.42;
  const thresholdSq = threshold * threshold;
  const tr = target?.r ?? 0;
  const tg = target?.g ?? 0;
  const tb = target?.b ?? 0;
  for (let i = 0; i < srcData.length; i += 4) {
    if (srcData[i + 3] === 0) continue;
    const dr = srcData[i] - tr;
    const dg = srcData[i + 1] - tg;
    const db = srcData[i + 2] - tb;
    if (dr * dr + dg * dg + db * db <= thresholdSq) {
      image.data[i] = 255;
      image.data[i + 1] = 255;
      image.data[i + 2] = 255;
      image.data[i + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  return out;
}
