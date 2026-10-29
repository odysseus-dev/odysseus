// Perspective (keystone) warp via a projective homography.
//
// Pure array maths so it can run in the browser and in Node tests. The
// destination is described by four normalized corners (TL, TR, BR, BL) as
// fractions of the output size; source pixels are sampled by inverse mapping.

export function solveSquareToQuad(corners) {
  const [p0, p1, p2, p3] = corners; // TL, TR, BR, BL
  const dx1 = p1.x - p2.x;
  const dx2 = p3.x - p2.x;
  const dx3 = p0.x - p1.x + p2.x - p3.x;
  const dy1 = p1.y - p2.y;
  const dy2 = p3.y - p2.y;
  const dy3 = p0.y - p1.y + p2.y - p3.y;
  if (dx3 === 0 && dy3 === 0) {
    // Affine.
    return [p1.x - p0.x, p2.x - p1.x, p0.x, p1.y - p0.y, p2.y - p1.y, p0.y, 0, 0, 1];
  }
  const den = dx1 * dy2 - dx2 * dy1;
  const g = (dx3 * dy2 - dx2 * dy3) / den;
  const h = (dx1 * dy3 - dx3 * dy1) / den;
  const a = p1.x - p0.x + g * p1.x;
  const b = p3.x - p0.x + h * p3.x;
  const c = p0.x;
  const d = p1.y - p0.y + g * p1.y;
  const e = p3.y - p0.y + h * p3.y;
  const f = p0.y;
  return [a, b, c, d, e, f, g, h, 1];
}

function invert3x3(m) {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h;
  const B = -(d * i - f * g);
  const C = d * h - e * g;
  const D = -(b * i - c * h);
  const E = a * i - c * g;
  const F = -(a * h - b * g);
  const G = b * f - c * e;
  const H = -(a * f - c * d);
  const I = a * e - b * d;
  const det = a * A + b * B + c * C;
  if (!det) return null;
  return [A / det, D / det, G / det, B / det, E / det, H / det, C / det, F / det, I / det];
}

/** Convert a `{ perspectiveX, perspectiveY }` amount pair (each −100..100) into
 *  destination corners for a unit square, keystoned about the centre. */
export function keystoneCorners(perspectiveX = 0, perspectiveY = 0) {
  const topScale = Math.max(0.05, 1 - perspectiveX / 200); // >0 narrows the top
  const sideScale = Math.max(0.05, 1 - perspectiveY / 200); // >0 narrows the sides
  const insetX = (1 - topScale) / 2;
  const insetY = (1 - sideScale) / 2;
  return [
    { x: insetX, y: insetY }, // TL
    { x: 1 - insetX, y: insetY }, // TR
    { x: 1, y: 1 - insetY }, // BR
    { x: 0, y: 1 - insetY }, // BL
  ];
}

/** Warp an RGBA buffer (`Uint8ClampedArray`, `Uint8Array` or plain array) with
 *  an inverse-mapped homography. Returns a fresh `Uint8ClampedArray`. */
export function warpRgba(src, srcW, srcH, corners, outW, outH) {
  const out = new Uint8ClampedArray(outW * outH * 4);
  const H = solveSquareToQuad(corners);
  const Hinv = invert3x3(H);
  if (!Hinv) return out;
  const [a, b, c, d, e, f, g, h] = Hinv;
  for (let y = 0; y < outH; y++) {
    const v = outH > 1 ? y / (outH - 1) : 0;
    for (let x = 0; x < outW; x++) {
      const u = outW > 1 ? x / (outW - 1) : 0;
      const w = g * u + h * v + 1;
      if (!w) continue;
      const s = (a * u + b * v + c) / w;
      const t = (d * u + e * v + f) / w;
      if (s < 0 || s > 1 || t < 0 || t > 1) continue;
      const sx = Math.min(srcW - 1, Math.max(0, Math.round(s * (srcW - 1))));
      const sy = Math.min(srcH - 1, Math.max(0, Math.round(t * (srcH - 1))));
      const si = (sy * srcW + sx) * 4;
      const di = (y * outW + x) * 4;
      out[di] = src[si];
      out[di + 1] = src[si + 1];
      out[di + 2] = src[si + 2];
      out[di + 3] = src[si + 3];
    }
  }
  return out;
}

/** Browser helper: return a new canvas holding the warped source canvas. */
export function perspectiveWarpCanvas(sourceCanvas, corners, outW, outH) {
  const srcW = sourceCanvas.width;
  const srcH = sourceCanvas.height;
  const sctx = sourceCanvas.getContext('2d');
  const src = sctx.getImageData(0, 0, srcW, srcH).data;
  const warped = warpRgba(src, srcW, srcH, corners, outW, outH);
  const canvas = document.createElement('canvas');
  canvas.width = outW;
  canvas.height = outH;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(outW, outH);
  image.data.set(warped);
  ctx.putImageData(image, 0, 0);
  return canvas;
}

export function isPerspectiveActive(perspectiveX = 0, perspectiveY = 0) {
  return Math.abs(perspectiveX) > 0.01 || Math.abs(perspectiveY) > 0.01;
}
