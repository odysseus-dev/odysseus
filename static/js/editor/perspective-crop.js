/**
 * Perspective Crop — straighten an arbitrary quadrilateral drawn over the image
 * into a rectangular crop, resizing the document to the quad's edge lengths.
 *
 * Pure functions plus one thin canvas adapter. The quad-to-rectangle homography
 * (solveSquareToQuad, from perspective-warp.js) is applied FORWARD so the quad's
 * contents fill the whole output rectangle; the output size comes from the
 * quad's average edge lengths so the straightened result keeps its proportions.
 */
import { solveSquareToQuad } from './perspective-warp.js';

function dist(a, b) {
  return Math.hypot((a?.x || 0) - (b?.x || 0), (a?.y || 0) - (b?.y || 0));
}

function clampDim(value) {
  return Math.max(1, Math.round(Number(value) || 1));
}

/** Output size for a crop quad (document px): mean of the opposite edge lengths. */
export function perspectiveCropSize(corners, srcW = 0, srcH = 0) {
  const c = Array.isArray(corners) ? corners : [];
  if (c.length < 4) {
    return { width: clampDim(srcW || 1), height: clampDim(srcH || 1) };
  }
  const [tl, tr, br, bl] = c;
  const width = (dist(tl, tr) + dist(bl, br)) / 2;
  const height = (dist(tl, bl) + dist(tr, br)) / 2;
  return { width: clampDim(width), height: clampDim(height) };
}

/** Normalise document-space corners to source fractions for warpRgba. */
export function normalizeCropCorners(corners, srcW, srcH) {
  const w = clampDim(srcW), h = clampDim(srcH);
  const c = Array.isArray(corners) ? corners.slice(0, 4) : [];
  while (c.length < 4) c.push({ x: 0, y: 0 });
  return c.map(point => ({
    x: Math.max(0, Math.min(1, (Number(point?.x) || 0) / w)),
    y: Math.max(0, Math.min(1, (Number(point?.y) || 0) / h)),
  }));
}

/**
 * Straighten the quad in `sourceCanvas` to a `outW` x `outH` canvas.
 * @returns {HTMLCanvasElement}
 */
export function perspectiveCropCanvas(sourceCanvas, corners, outW, outH) {
  const w = clampDim(sourceCanvas?.width || 1);
  const h = clampDim(sourceCanvas?.height || 1);
  const width = clampDim(outW);
  const height = clampDim(outH);
  const srcData = sourceCanvas.getContext('2d').getImageData(0, 0, w, h).data;
  const quad = normalizeCropCorners(corners, w, h);
  const [a, b, c, d, e, f, g, hh] = solveSquareToQuad(quad);
  const out = new Uint8ClampedArray(width * height * 4);
  const denomX = width > 1 ? width - 1 : 1;
  const denomY = height > 1 ? height - 1 : 1;
  for (let y = 0; y < height; y += 1) {
    const v = y / denomY;
    for (let x = 0; x < width; x += 1) {
      const u = x / denomX;
      const wgt = g * u + hh * v + 1;
      if (!wgt) continue;
      const s = (a * u + b * v + c) / wgt;
      const t = (d * u + e * v + f) / wgt;
      if (s < 0 || s > 1 || t < 0 || t > 1) continue;
      const sx = Math.min(w - 1, Math.max(0, Math.round(s * (w - 1))));
      const sy = Math.min(h - 1, Math.max(0, Math.round(t * (h - 1))));
      const si = (sy * w + sx) * 4;
      const di = (y * width + x) * 4;
      out[di] = srcData[si];
      out[di + 1] = srcData[si + 1];
      out[di + 2] = srcData[si + 2];
      out[di + 3] = srcData[si + 3];
    }
  }
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(width, height);
  image.data.set(out);
  ctx.putImageData(image, 0, 0);
  return canvas;
}

/** Default corner set: inset a little from the document corners. */
export function defaultPerspectiveCorners(srcW, srcH, inset = 0.06) {
  const w = clampDim(srcW), h = clampDim(srcH);
  const dx = Math.round(w * inset), dy = Math.round(h * inset);
  return [
    { x: dx, y: dy },
    { x: w - dx, y: dy },
    { x: w - dx, y: h - dy },
    { x: dx, y: h - dy },
  ];
}

export default { perspectiveCropSize, normalizeCropCorners, perspectiveCropCanvas, defaultPerspectiveCorners };
