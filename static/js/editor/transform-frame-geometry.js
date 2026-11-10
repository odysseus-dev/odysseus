/** Pure geometry shared by transform-frame drawing and hit testing. */

const HANDLE_LAYOUT = [
  ['tl', -0.5, -0.5],
  ['t', 0, -0.5],
  ['tr', 0.5, -0.5],
  ['r', 0.5, 0],
  ['br', 0.5, 0.5],
  ['b', 0, 0.5],
  ['bl', -0.5, 0.5],
  ['l', -0.5, 0],
];

function finite(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function transformFrameGeometry(frame, options = {}) {
  const centerX = finite(frame?.centerX);
  const centerY = finite(frame?.centerY);
  const width = Math.max(1, Math.abs(finite(frame?.width, 1)));
  const height = Math.max(1, Math.abs(finite(frame?.height, 1)));
  const rotation = finite(frame?.rotation);
  const radians = rotation * Math.PI / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  // Skew (degrees) shears the frame in its LOCAL axes — the same order the
  // canvas render pipeline uses (scale → skew → rotate). Zero skew leaves
  // the geometry byte-for-byte identical to the un-skewed frame.
  const skewX = finite(frame?.skewX);
  const skewY = finite(frame?.skewY);
  const shearX = Math.tan(skewX * Math.PI / 180);
  const shearY = Math.tan(skewY * Math.PI / 180);
  const localToWorld = (localX, localY) => {
    const sx = localX + shearX * localY;
    const sy = localY + shearY * localX;
    return {
      x: centerX + sx * cos - sy * sin,
      y: centerY + sx * sin + sy * cos,
    };
  };
  // Free distort (Ctrl/Cmd+drag): per-corner offsets in frame-normal units.
  // When present, the corner/edge handles follow the quad so only the dragged
  // point moves; every other point stays where it was.
  const distort = frame?.distort || null;
  const localOf = (() => {
    if (!distort) {
      return Object.fromEntries(HANDLE_LAYOUT.map(([id, xFactor, yFactor]) => [id, { x: width * xFactor, y: height * yFactor }]));
    }
    const delta = key => distort[key] || { x: 0, y: 0 };
    const corner = (key, sx, sy) => ({
      x: (sx + delta(key).x) * width,
      y: (sy + delta(key).y) * height,
    });
    const tl = corner('tl', -0.5, -0.5);
    const tr = corner('tr', 0.5, -0.5);
    const br = corner('br', 0.5, 0.5);
    const bl = corner('bl', -0.5, 0.5);
    const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
    return { tl, t: mid(tl, tr), tr, r: mid(tr, br), br, b: mid(br, bl), bl, l: mid(bl, tl) };
  })();
  const resizeHandles = HANDLE_LAYOUT.map(([id]) => ({
    id,
    ...localToWorld(localOf[id].x, localOf[id].y),
  }));
  const byId = Object.fromEntries(resizeHandles.map(handle => [handle.id, handle]));
  const zoom = Math.max(0.0001, finite(options.zoom, 1));
  const rotationOffset = Math.max(0, finite(options.rotationOffsetCss, 24)) / zoom;
  const topMidX = localOf.t ? localOf.t.x : 0;
  const topMidY = localOf.t ? localOf.t.y : -height / 2;
  const rotationLocalY = options.rotationInside
    ? topMidY + Math.max(0, rotationOffset)
    : topMidY - rotationOffset;
  const rotationHandle = {
    id: 'rot',
    ...localToWorld(topMidX, rotationLocalY),
  };

  return {
    centerX,
    centerY,
    width,
    height,
    rotation,
    radians,
    skewX,
    skewY,
    distort,
    resizeHandles,
    handles: [...resizeHandles, rotationHandle],
    rotationHandle,
    corners: {
      tl: { x: byId.tl.x, y: byId.tl.y },
      tr: { x: byId.tr.x, y: byId.tr.y },
      br: { x: byId.br.x, y: byId.br.y },
      bl: { x: byId.bl.x, y: byId.bl.y },
    },
    pivot: { x: centerX, y: centerY },
  };
}

export function hitTestTransformHandle(geometry, x, y, options = {}) {
  if (!geometry) return null;
  const zoom = Math.max(0.0001, finite(options.zoom, 1));
  const pointerType = options.pointerType || 'mouse';
  const radiusCss = pointerType === 'touch' || pointerType === 'pen' ? 20 : 8;
  const radius = radiusCss / zoom;
  const radiusSquared = radius * radius;
  for (const handle of geometry.handles || []) {
    const dx = finite(x) - handle.x;
    const dy = finite(y) - handle.y;
    if (dx * dx + dy * dy <= radiusSquared) return handle.id;
  }
  return null;
}

export function pointInTransformFrame(geometry, x, y) {
  if (!geometry) return false;
  const dx = finite(x) - geometry.centerX;
  const dy = finite(y) - geometry.centerY;
  const cos = Math.cos(-geometry.radians);
  const sin = Math.sin(-geometry.radians);
  const localX = dx * cos - dy * sin;
  const localY = dx * sin + dy * cos;
  const epsilon = 0.0001;
  return Math.abs(localX) <= geometry.width / 2 + epsilon
    && Math.abs(localY) <= geometry.height / 2 + epsilon;
}

/** Resize a frame from a screen-space pointer delta on its rotated axes. */
export function resizeTransformFrame(frame, handle, startPoint, currentPoint, options = {}) {
  const geometry = transformFrameGeometry(frame);
  const xSign = handle?.includes('l') ? -1 : handle?.includes('r') ? 1 : 0;
  const ySign = handle?.includes('t') ? -1 : handle?.includes('b') ? 1 : 0;
  if (!xSign && !ySign) return {
    width: geometry.width,
    height: geometry.height,
    centerX: geometry.centerX,
    centerY: geometry.centerY,
    flipH: !!options.flipH,
    flipV: !!options.flipV,
  };

  const worldDx = finite(currentPoint?.x) - finite(startPoint?.x);
  const worldDy = finite(currentPoint?.y) - finite(startPoint?.y);
  const cos = Math.cos(geometry.radians);
  const sin = Math.sin(geometry.radians);
  const localDx = worldDx * cos + worldDy * sin;
  const localDy = -worldDx * sin + worldDy * cos;
  const multiplier = options.centered ? 2 : 1;
  let rawWidth = geometry.width + xSign * localDx * multiplier;
  let rawHeight = geometry.height + ySign * localDy * multiplier;

  if (options.lockAspect) {
    const aspect = geometry.width / geometry.height;
    const widthChange = xSign ? Math.abs(Math.abs(rawWidth) - geometry.width) / geometry.width : -1;
    const heightChange = ySign ? Math.abs(Math.abs(rawHeight) - geometry.height) / geometry.height : -1;
    if (xSign && (!ySign || widthChange >= heightChange)) {
      const sign = rawHeight < 0 ? -1 : 1;
      rawHeight = sign * Math.abs(rawWidth) / aspect;
    } else if (ySign) {
      const sign = rawWidth < 0 ? -1 : 1;
      rawWidth = sign * Math.abs(rawHeight) * aspect;
    }
  }

  const width = Math.max(1, Math.round(Math.abs(rawWidth)));
  const height = Math.max(1, Math.round(Math.abs(rawHeight)));
  const signedWidth = (rawWidth < 0 ? -1 : 1) * width;
  const signedHeight = (rawHeight < 0 ? -1 : 1) * height;
  const localCenterX = options.centered || !xSign
    ? 0
    : xSign * (signedWidth - geometry.width) / 2;
  const localCenterY = options.centered || !ySign
    ? 0
    : ySign * (signedHeight - geometry.height) / 2;

  return {
    width,
    height,
    centerX: geometry.centerX + localCenterX * cos - localCenterY * sin,
    centerY: geometry.centerY + localCenterX * sin + localCenterY * cos,
    flipH: !!options.flipH !== (rawWidth < 0),
    flipV: !!options.flipV !== (rawHeight < 0),
  };
}

/**
 * Skew a frame from a screen-space pointer delta on its rotated axes,
 * mirroring Photoshop's Ctrl+drag on a handle.
 *
 *   top / bottom handles   → horizontal drag shears vertically-offset
 *                            rows (skewX)
 *   left / right handles   → vertical drag shears horizontally-offset
 *                            columns (skewY)
 *   corner handles         → both
 *
 * Returns { skewX, skewY } in degrees relative to `options.startSkew*`,
 * clamped to ±85° so the tangent never blows up. Size, centre and
 * rotation are untouched — skew is a separate slot in the transform
 * target, exactly like the popup's Skew X / Skew Y fields.
 */
export function skewTransformFrame(frame, handle, startPoint, currentPoint, options = {}) {
  const geometry = transformFrameGeometry(frame);
  const xSign = handle?.includes('l') ? -1 : handle?.includes('r') ? 1 : 0;
  const ySign = handle?.includes('t') ? -1 : handle?.includes('b') ? 1 : 0;
  const worldDx = finite(currentPoint?.x) - finite(startPoint?.x);
  const worldDy = finite(currentPoint?.y) - finite(startPoint?.y);
  const cos = Math.cos(geometry.radians);
  const sin = Math.sin(geometry.radians);
  const localDx = worldDx * cos + worldDy * sin;
  const localDy = -worldDx * sin + worldDy * cos;
  const toDegrees = 180 / Math.PI;
  const LIMIT = 85;
  const clamp = value => Math.max(-LIMIT, Math.min(LIMIT, value));
  const startSkewX = finite(options.startSkewX, 0);
  const startSkewY = finite(options.startSkewY, 0);

  let skewX = startSkewX;
  let skewY = startSkewY;
  // Top/bottom edge: the dragged edge follows the horizontal delta.
  if (ySign) skewX = clamp(startSkewX + Math.atan2(2 * localDx / ySign, geometry.height) * toDegrees);
  // Left/right edge: the dragged edge follows the vertical delta.
  if (xSign) skewY = clamp(startSkewY + Math.atan2(2 * localDy / xSign, geometry.width) * toDegrees);

  return { skewX, skewY };
}

/**
 * Free distort from a screen-space pointer delta on a handle, mirroring
 * Photoshop's Ctrl+drag: ONLY the dragged point moves.
 *
 *   corner handle → that corner alone follows the pointer (the frame
 *                   becomes a quad)
 *   edge handle   → both corners of that edge move together, so the edge
 *                   slides/keeps its length but nothing else moves
 *
 * Corner offsets are returned in FRAME-NORMAL units (a fraction of the
 * frame's width/height; ±0.5 is the frame half-size) relative to
 * `options.startDistort`, so they compose with any existing distort.
 * Size, centre, rotation and skew are untouched.
 */
export function distortTransformFrame(frame, handle, startPoint, currentPoint, options = {}) {
  const geometry = transformFrameGeometry(frame);
  const corners = ['tl', 'tr', 'br', 'bl'];
  const owners = {
    tl: ['tl'], tr: ['tr'], br: ['br'], bl: ['bl'],
    t: ['tl', 'tr'], b: ['bl', 'br'], l: ['tl', 'bl'], r: ['tr', 'br'],
  }[handle];
  if (!owners) return null;

  const worldDx = finite(currentPoint?.x) - finite(startPoint?.x);
  const worldDy = finite(currentPoint?.y) - finite(startPoint?.y);
  const cos = Math.cos(geometry.radians);
  const sin = Math.sin(geometry.radians);
  const localDx = worldDx * cos + worldDy * sin;
  const localDy = -worldDx * sin + worldDy * cos;
  const ndx = localDx / geometry.width;
  const ndy = localDy / geometry.height;

  const start = options.startDistort || null;
  const base = key => (start && start[key] ? { x: finite(start[key].x), y: finite(start[key].y) } : { x: 0, y: 0 });
  const LIMIT = 4;
  const clamp = value => Math.max(-LIMIT, Math.min(LIMIT, value));
  const next = {};
  for (const key of corners) {
    if (owners.includes(key)) next[key] = { x: clamp(base(key).x + ndx), y: clamp(base(key).y + ndy) };
    else next[key] = base(key);
  }
  return next;
}
