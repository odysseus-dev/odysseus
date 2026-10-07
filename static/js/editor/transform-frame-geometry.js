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
  const rotateLocal = (localX, localY) => ({
    x: centerX + localX * cos - localY * sin,
    y: centerY + localX * sin + localY * cos,
  });
  const resizeHandles = HANDLE_LAYOUT.map(([id, xFactor, yFactor]) => ({
    id,
    ...rotateLocal(width * xFactor, height * yFactor),
  }));
  const byId = Object.fromEntries(resizeHandles.map(handle => [handle.id, handle]));
  const zoom = Math.max(0.0001, finite(options.zoom, 1));
  const rotationOffset = Math.max(0, finite(options.rotationOffsetCss, 24)) / zoom;
  const rotationLocalY = options.rotationInside
    ? -Math.max(0, height / 2 - rotationOffset)
    : -(height / 2 + rotationOffset);
  const rotationHandle = {
    id: 'rot',
    ...rotateLocal(0, rotationLocalY),
  };

  return {
    centerX,
    centerY,
    width,
    height,
    rotation,
    radians,
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
