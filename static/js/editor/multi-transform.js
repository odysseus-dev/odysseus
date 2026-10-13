/** Shared-bounds geometry for transforming one or many layers. */

export function selectionBounds(editorState, layers) {
  if (!layers?.length) return null;
  let left = Infinity;
  let top = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  for (const layer of layers) {
    const offset = editorState.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    left = Math.min(left, offset.x);
    top = Math.min(top, offset.y);
    right = Math.max(right, offset.x + Math.max(1, layer.canvas?.width || 1));
    bottom = Math.max(bottom, offset.y + Math.max(1, layer.canvas?.height || 1));
  }
  return {
    x: left,
    y: top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
    centerX: (left + right) / 2,
    centerY: (top + bottom) / 2,
  };
}

export function transformedLayerGeometry(snapshot, sourceBounds, target) {
  const scaleX = Math.max(1, target.width) / Math.max(1, sourceBounds.width);
  const scaleY = Math.max(1, target.height) / Math.max(1, sourceBounds.height);
  const signedScaleX = target.flipH ? -scaleX : scaleX;
  const signedScaleY = target.flipV ? -scaleY : scaleY;
  const radians = (target.rotation * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const absCos = Math.abs(cos);
  const absSin = Math.abs(sin);
  const sourceCenterX = snapshot.offset.x + snapshot.width / 2;
  const sourceCenterY = snapshot.offset.y + snapshot.height / 2;
  const relativeX = (sourceCenterX - sourceBounds.centerX) * signedScaleX;
  const relativeY = (sourceCenterY - sourceBounds.centerY) * signedScaleY;
  const centerX = target.centerX + relativeX * cos - relativeY * sin;
  const centerY = target.centerY + relativeX * sin + relativeY * cos;
  const scaledWidth = snapshot.width * scaleX;
  const scaledHeight = snapshot.height * scaleY;
  const width = Math.max(1, Math.round(scaledWidth * absCos + scaledHeight * absSin));
  const height = Math.max(1, Math.round(scaledWidth * absSin + scaledHeight * absCos));
  return {
    scaleX,
    scaleY,
    signedScaleX,
    signedScaleY,
    rotation: target.rotation,
    radians,
    width,
    height,
    centerX,
    centerY,
    offset: {
      x: Math.round(centerX - width / 2),
      y: Math.round(centerY - height / 2),
    },
  };
}

export function transformedSelectionBounds(sourceBounds, target) {
  const radians = (target.rotation * Math.PI) / 180;
  const cos = Math.abs(Math.cos(radians));
  const sin = Math.abs(Math.sin(radians));
  const width = Math.max(1, target.width * cos + target.height * sin);
  const height = Math.max(1, target.width * sin + target.height * cos);
  return {
    x: target.centerX - width / 2,
    y: target.centerY - height / 2,
    width,
    height,
    centerX: target.centerX,
    centerY: target.centerY,
  };
}

function canvasPixels(canvas) {
  const width = Number(canvas?.width) || 0;
  const height = Number(canvas?.height) || 0;
  return Math.max(0, width) * Math.max(0, height);
}

/**
 * Build a transform plan without allocating output canvases. The project
 * decoder and the live editor share the same limits so a transform cannot
 * create a document that the editor would later refuse to reopen.
 */
export function planLayerTransform(editorState, snapshots, sourceBounds, target, limits) {
  const maxDimension = Number(limits?.maxDimension) || 32768;
  const maxSurfacePixels = Number(limits?.maxSurfacePixels) || 300_000_000;
  const numericTarget = [
    target?.width, target?.height, target?.rotation,
    target?.centerX, target?.centerY,
  ].every(Number.isFinite);
  if (!numericTarget || target.width < 1 || target.height < 1) {
    return { ok: false, reason: 'Transform values must be finite positive numbers.' };
  }

  const planned = [];
  const byLayerId = new Map();
  for (const snapshot of snapshots || []) {
    const geometry = transformedLayerGeometry(snapshot, sourceBounds, target);
    if (![geometry.width, geometry.height, geometry.offset.x, geometry.offset.y].every(Number.isFinite)) {
      return { ok: false, reason: 'Transform geometry is outside the supported numeric range.' };
    }
    if (geometry.width > maxDimension || geometry.height > maxDimension) {
      return {
        ok: false,
        reason: `A transformed layer would exceed the ${maxDimension.toLocaleString()} px dimension limit.`,
      };
    }
    const item = { snapshot, geometry };
    planned.push(item);
    byLayerId.set(snapshot.layer.id, item);
  }

  let surfacePixels = 0;
  const addPixels = pixels => {
    surfacePixels += pixels;
    return surfacePixels <= maxSurfacePixels;
  };
  for (const layer of editorState.layers || []) {
    const item = byLayerId.get(layer.id);
    if (!addPixels(item ? item.geometry.width * item.geometry.height : canvasPixels(layer.canvas))) {
      return { ok: false, reason: 'Transform would exceed the 300 megapixel surface budget.' };
    }
    if (layer.kind === 'placed' && layer.placed?.sourceCanvas && !addPixels(canvasPixels(layer.placed.sourceCanvas))) {
      return { ok: false, reason: 'Transform would exceed the 300 megapixel surface budget.' };
    }
    for (const mask of layer.masks || []) {
      const maskSnapshot = item?.snapshot.masks?.find(candidate => candidate.mask === mask);
      const pixels = maskSnapshot?.linked
        ? item.geometry.width * item.geometry.height
        : canvasPixels(mask.canvas);
      if (!addPixels(pixels)) {
        return { ok: false, reason: 'Transform would exceed the 300 megapixel surface budget.' };
      }
    }
  }
  for (const group of editorState.layerGroups || []) {
    for (const mask of group.masks || []) {
      if (!addPixels(canvasPixels(mask.canvas))) {
        return { ok: false, reason: 'Transform would exceed the 300 megapixel surface budget.' };
      }
    }
  }
  for (const selection of editorState.savedSelections || []) {
    if (!addPixels(canvasPixels(selection.canvas))) {
      return { ok: false, reason: 'Transform would exceed the 300 megapixel surface budget.' };
    }
  }
  if (editorState.wandMask && !addPixels(canvasPixels(editorState.wandMask))) {
    return { ok: false, reason: 'Transform would exceed the 300 megapixel surface budget.' };
  }
  return { ok: true, surfacePixels, items: planned };
}
