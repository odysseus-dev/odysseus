/**
 * Lasso-tool pixel & path helpers.
 *
 * All functions take the lasso polygon `points` as an explicit
 * argument so they can be tested in isolation. The legacy gallery
 * editor calls them with its module-level `_lassoPoints` array.
 */

/**
 * Shift each polygon vertex along the outward normal by `grow` pixels.
 * Used by the lasso overlay (to draw the "feather" halo) and by
 * `buildLassoMask` (to bake the grown polygon into the mask).
 *
 * @param {{x: number, y: number}[]} points  Polygon vertices in draw order.
 * @param {number} grow                      Positive = expand outward, negative = contract.
 * @returns {{x: number, y: number}[]}       New array (same length, original is not mutated).
 */
export function lassoOffsetPoints(points, grow) {
  const n = points.length;
  if (n < 3 || !grow) return points;
  // Polygon winding (positive = CCW) — flip the normal so it points
  // away from the interior regardless of draw direction.
  let area = 0;
  for (let i = 0; i < n; i++) {
    const p = points[i], q = points[(i + 1) % n];
    area += (q.x - p.x) * (q.y + p.y);
  }
  const sign = area > 0 ? -1 : 1;
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const a = points[(i - 1 + n) % n], b = points[i], c = points[(i + 1) % n];
    const e1x = b.x - a.x, e1y = b.y - a.y;
    const e2x = c.x - b.x, e2y = c.y - b.y;
    const l1 = Math.hypot(e1x, e1y) || 1;
    const l2 = Math.hypot(e2x, e2y) || 1;
    // Perpendicular (dy, -dx); flip via `sign` for outward direction.
    const n1x = (e1y / l1) * sign, n1y = (-e1x / l1) * sign;
    const n2x = (e2y / l2) * sign, n2y = (-e2x / l2) * sign;
    const nx = (n1x + n2x) / 2;
    const ny = (n1y + n2y) / 2;
    const nl = Math.hypot(nx, ny) || 1;
    out[i] = { x: b.x + (nx / nl) * grow, y: b.y + (ny / nl) * grow };
  }
  return out;
}


/**
 * Trace the lasso polygon on the given context (move-to + line-to,
 * closed). Caller is responsible for `stroke()` / `fill()` choice.
 */
export function getLassoPath(ctx, points) {
  if (!points || points.length < 1) return;
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i++) {
    ctx.lineTo(points[i].x, points[i].y);
  }
  ctx.closePath();
}


/**
 * Build a (optionally feathered, optionally grown) selection mask
 * from a lasso polygon.
 *
 * @param {{x: number, y: number}[]} points  Polygon vertices.
 * @param {number} w / h                     Output canvas dimensions.
 * @param {number} offX / offY               Translate the polygon by (offX, offY) before rasterising.
 * @param {number} feather                   Feather width in pixels. 0 = hard edge.
 * @param {number} grow                      Positive = dilate the polygon, negative = erode.
 * @returns {HTMLCanvasElement}              A `w × h` canvas with alpha = selection strength.
 */
export function buildLassoMask(points, w, h, offX, offY, feather, grow) {
  // Step 1: draw hard mask
  const hard = document.createElement('canvas');
  hard.width = w; hard.height = h;
  const hCtx = hard.getContext('2d');
  hCtx.beginPath();
  hCtx.moveTo(points[0].x - offX, points[0].y - offY);
  for (let i = 1; i < points.length; i++) {
    hCtx.lineTo(points[i].x - offX, points[i].y - offY);
  }
  hCtx.closePath();
  hCtx.fillStyle = '#fff';
  hCtx.fill();

  // Step 1b: grow / shrink — blur the hard mask, threshold low for
  // grow and high for shrink. Same technique as the bg-remove edge
  // tuner. RGB is left alone, alpha is replaced.
  if (grow && grow !== 0) {
    const blurC = document.createElement('canvas');
    blurC.width = w; blurC.height = h;
    const bctx = blurC.getContext('2d');
    bctx.filter = `blur(${Math.abs(grow)}px)`;
    bctx.drawImage(hard, 0, 0);
    bctx.filter = 'none';
    const blurred = bctx.getImageData(0, 0, w, h).data;
    const hd = hCtx.getImageData(0, 0, w, h);
    const out = hd.data;
    const thr = grow > 0 ? 32 : 200;
    for (let i = 0; i < out.length; i += 4) {
      const a = blurred[i + 3] >= thr ? 255 : 0;
      out[i] = a; out[i + 1] = a; out[i + 2] = a; out[i + 3] = a;
    }
    hCtx.putImageData(hd, 0, 0);
  }

  if (feather <= 0) return hard;

  // Step 2: pixel data and distance-based feather.
  const hardData = hCtx.getImageData(0, 0, w, h);
  const d = hardData.data;

  // Build inside/outside map.
  const inside = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    inside[i] = d[i * 4] > 128 ? 1 : 0;
  }

  // Distance from edge (for pixels inside the selection, distance to nearest outside pixel).
  const dist = new Float32Array(w * h);
  dist.fill(feather + 1);

  // Seed: edge pixels (inside pixels adjacent to outside pixels).
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (!inside[i]) { dist[i] = 0; continue; }
      const hasOutside = (x > 0 && !inside[i-1]) || (x < w-1 && !inside[i+1]) ||
                         (y > 0 && !inside[(y-1)*w+x]) || (y < h-1 && !inside[(y+1)*w+x]);
      if (hasOutside) dist[i] = 1;
    }
  }

  // Two-pass chamfer distance transform.
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (dist[i] === 0) continue;
      if (x > 0) dist[i] = Math.min(dist[i], dist[i-1] + 1);
      if (y > 0) dist[i] = Math.min(dist[i], dist[(y-1)*w+x] + 1);
    }
  }
  for (let y = h-1; y >= 0; y--) {
    for (let x = w-1; x >= 0; x--) {
      const i = y * w + x;
      if (dist[i] === 0) continue;
      if (x < w-1) dist[i] = Math.min(dist[i], dist[i+1] + 1);
      if (y < h-1) dist[i] = Math.min(dist[i], dist[(y+1)*w+x] + 1);
    }
  }

  // Pixels near the edge get reduced alpha.
  const result = document.createElement('canvas');
  result.width = w; result.height = h;
  const rCtx = result.getContext('2d');
  const rData = rCtx.createImageData(w, h);

  for (let i = 0; i < w * h; i++) {
    if (!inside[i]) continue;
    const edgeDist = dist[i];
    const alpha = edgeDist >= feather ? 255 : Math.round((edgeDist / feather) * 255);
    rData.data[i*4] = alpha;
    rData.data[i*4+1] = alpha;
    rData.data[i*4+2] = alpha;
    rData.data[i*4+3] = 255;
  }
  rCtx.putImageData(rData, 0, 0);
  return result;
}
