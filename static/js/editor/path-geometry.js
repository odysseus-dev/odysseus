/* Curve flattening helpers for vector paths.

   The editor stores paths as plain polylines (`pathPoints: [{x,y}, …]`), which
   the pen tool and the Shape tools already produce. To let users lay text on a
   *curved* path we flatten analytic curves (ellipse/arc, quadratic and cubic
   Bezier) into a dense polyline: the existing arc-length text renderer then
   spaces glyphs evenly and rotates each one to the local tangent, so the text
   follows the curve smoothly instead of reading as straight rotated runs.

   All functions are pure and return arrays of finite {x, y} points. Subdivision
   is chosen from arc length so a big curve gets proportionally more points. */

function stepSize(scale) {
  const s = Number(scale);
  if (!Number.isFinite(s) || s <= 0) return 6;
  return Math.max(3, Math.min(24, s / 32));
}

/* Points along the ellipse ((cx,cy), rx, ry). `start`/`end` are radians and a
   full sweep (the default) closes the ring. `rotation` rotates the whole ellipse
   about its centre. The sweep is sampled densely enough that the chord sag stays
   sub-pixel for the given radii. */
export function ellipsePoints(cx, cy, rx, ry, { start = 0, end = Math.PI * 2, rotation = 0, count = 0 } = {}) {
  const centerX = Number(cx) || 0;
  const centerY = Number(cy) || 0;
  const radiusX = Math.abs(Number(rx)) || 0;
  const radiusY = Math.abs(Number(ry)) || 0;
  const sweep = (Number(end) || 0) - (Number(start) || 0);
  const rough = Math.PI * (3 * (radiusX + radiusY) - Math.sqrt((3 * radiusX + radiusY) * (radiusX + 3 * radiusY)));
  const arcLength = (Math.abs(sweep) / (Math.PI * 2)) * rough;
  const n = Math.max(16, Math.min(2048, count || Math.ceil(arcLength / stepSize(Math.max(radiusX, radiusY)))));
  const cosR = Math.cos(Number(rotation) || 0);
  const sinR = Math.sin(Number(rotation) || 0);
  const points = [];
  for (let i = 0; i <= n; i += 1) {
    const angle = (Number(start) || 0) + sweep * (i / n);
    let dx = radiusX * Math.cos(angle);
    let dy = radiusY * Math.sin(angle);
    if (rotation) {
      const rx2 = dx * cosR - dy * sinR;
      const ry2 = dx * sinR + dy * cosR;
      dx = rx2;
      dy = ry2;
    }
    points.push({ x: centerX + dx, y: centerY + dy });
  }
  return points;
}

function controlLength(p0, p1, p2, p3) {
  const seg = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
  return seg(p0, p1) + seg(p1, p2) + seg(p2, p3);
}

/* Cubic Bezier p0..p3 flattened via de Casteljau at uniform parameter steps. */
export function cubicBezierPoints(p0, p1, p2, p3, count = 0) {
  const a = p0 || { x: 0, y: 0 };
  const b = p1 || a;
  const c = p2 || b;
  const d = p3 || c;
  const control = controlLength(a, b, c, d);
  const n = Math.max(8, Math.min(2048, count || Math.ceil(control / Math.max(3, Math.min(12, control / 48)))));
  const points = [];
  for (let i = 0; i <= n; i += 1) {
    const t = i / n;
    const u = 1 - t;
    const x = u * u * u * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t * t * t * d.x;
    const y = u * u * u * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y + t * t * t * d.y;
    points.push({ x, y });
  }
  return points;
}

/* Quadratic Bezier p0,p1,p2 flattened similarly. */
export function quadraticBezierPoints(p0, p1, p2, count = 0) {
  const a = p0 || { x: 0, y: 0 };
  const b = p1 || a;
  const c = p2 || b;
  const seg = (p, q) => Math.hypot(q.x - p.x, q.y - p.y);
  const length = seg(a, b) + seg(b, c);
  const n = Math.max(8, Math.min(2048, count || Math.ceil(length / Math.max(3, Math.min(12, length / 48)))));
  const points = [];
  for (let i = 0; i <= n; i += 1) {
    const t = i / n;
    const u = 1 - t;
    const x = u * u * a.x + 2 * u * t * b.x + t * t * c.x;
    const y = u * u * a.y + 2 * u * t * b.y + t * t * c.y;
    points.push({ x, y });
  }
  return points;
}

/* Smooth an existing polyline with a Catmull-Rom spline: useful to turn a few
   coarse hand-drawn points into a flowing curve. Returns the original points
   when there are fewer than three. */
export function smoothPathPoints(points, count = 0) {
  const pts = (Array.isArray(points) ? points : []).filter(
    (p) => p && Number.isFinite(Number(p.x)) && Number.isFinite(Number(p.y)),
  ).map((p) => ({ x: Number(p.x), y: Number(p.y) }));
  if (pts.length < 3) return pts;
  const out = [];
  const n = Math.max(4, Math.min(64, count || 12));
  const at = (i) => pts[Math.max(0, Math.min(pts.length - 1, i))];
  for (let i = 0; i < pts.length - 1; i += 1) {
    const p0 = at(i - 1);
    const p1 = at(i);
    const p2 = at(i + 1);
    const p3 = at(i + 2);
    for (let j = 0; j < n; j += 1) {
      const t = j / n;
      const t2 = t * t;
      const t3 = t2 * t;
      const x = 0.5 * ((2 * p1.x) + (-p0.x + p2.x) * t + (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 + (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3);
      const y = 0.5 * ((2 * p1.y) + (-p0.y + p2.y) * t + (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 + (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3);
      out.push({ x, y });
    }
  }
  out.push(pts[pts.length - 1]);
  return out;
}
