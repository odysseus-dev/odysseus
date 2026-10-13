import { transformFrameGeometry } from './transform-frame-geometry.js';

/**
 * Snap-while-dragging: when the move tool drags a layer near another
 * layer's edge or the canvas centre/edges, gently lock the proposed
 * (nx, ny) to the nearest target within SNAP_PX.
 *
 * The implementation is pure — it takes the layer being moved + the
 * trial offset + a context describing zoom + the other layers, and
 * returns the snapped position plus any guides to draw.
 *
 * The legacy gallery editor's `_computeSnap` is a one-line wrapper
 * that builds the context from module state.
 *
 * @param {{canvas: HTMLCanvasElement, id: string}} layer
 *   The layer currently being moved.
 * @param {number} nx / ny
 *   Trial offset (top-left) in canvas pixels, before snapping.
 * @param {{
 *   zoom:        number,
 *   canvasW:     number,
 *   canvasH:     number,
 *   otherLayers: Array<{visible: boolean, id: string, canvas: HTMLCanvasElement, offset: {x:number, y:number}}>,
 *   verticalGuides?: number[], horizontalGuides?: number[],
 *   snapToGrid?: boolean, gridSize?: number,
 * }} ctx
 * @returns {{x: number, y: number, guides: Array}}
 */
export function computeSnap(layer, nx, ny, ctx) {
  if (!layer || !layer.canvas || !ctx) return { x: nx, y: ny, guides: [] };
  const zoom = Number.isFinite(Number(ctx.zoom)) ? Number(ctx.zoom) : 1;
  const SNAP_PX = 6 / Math.max(zoom, 0.0001);
  const cw = Number(ctx.canvasW) || 0, ch = Number(ctx.canvasH) || 0;
  const w = layer.canvas.width, h = layer.canvas.height;

  const vTargets = [
    { x: 0, label: 'canvas-l' },
    { x: cw, label: 'canvas-r' },
    { x: cw / 2, label: 'canvas-cx' },
  ];
  const hTargets = [
    { y: 0, label: 'canvas-t' },
    { y: ch, label: 'canvas-b' },
    { y: ch / 2, label: 'canvas-cy' },
  ];
  for (const x of Array.isArray(ctx.verticalGuides) ? ctx.verticalGuides : []) {
    if (Number.isFinite(Number(x))) vTargets.push({ x: Number(x), label: 'guide-v' });
  }
  for (const y of Array.isArray(ctx.horizontalGuides) ? ctx.horizontalGuides : []) {
    if (Number.isFinite(Number(y))) hTargets.push({ y: Number(y), label: 'guide-h' });
  }
  const otherLayers = Array.isArray(ctx.otherLayers) ? ctx.otherLayers : [];
  for (const other of otherLayers) {
    if (!other.visible || other.id === layer.id) continue;
    const o = other.offset || { x: 0, y: 0 };
    const ow = other.canvas.width, oh = other.canvas.height;
    vTargets.push({ x: o.x,            label: 'layer-l' });
    vTargets.push({ x: o.x + ow,       label: 'layer-r' });
    vTargets.push({ x: o.x + ow / 2,   label: 'layer-cx' });
    hTargets.push({ y: o.y,            label: 'layer-t' });
    hTargets.push({ y: o.y + oh,       label: 'layer-b' });
    hTargets.push({ y: o.y + oh / 2,   label: 'layer-cy' });
  }

  const myEdgesX = { l: nx, cx: nx + w / 2, r: nx + w };
  const myEdgesY = { t: ny, cy: ny + h / 2, b: ny + h };
  let bestX = null, bestDx = Infinity;
  let bestY = null, bestDy = Infinity;
  for (const [src, val] of Object.entries(myEdgesX)) {
    for (const t of vTargets) {
      const d = Math.abs(t.x - val);
      if (d < SNAP_PX && d < bestDx) {
        bestDx = d;
        bestX = { snapTo: t.x, src, target: t };
      }
    }
    if (ctx.snapToGrid && Number(ctx.gridSize) > 0) {
      const grid = Number(ctx.gridSize);
      const target = Math.round(val / grid) * grid;
      const d = Math.abs(target - val);
      if (d < SNAP_PX && d < bestDx) {
        bestDx = d;
        bestX = { snapTo: target, src, target: { x: target, label: 'grid' } };
      }
    }
  }
  for (const [src, val] of Object.entries(myEdgesY)) {
    for (const t of hTargets) {
      const d = Math.abs(t.y - val);
      if (d < SNAP_PX && d < bestDy) {
        bestDy = d;
        bestY = { snapTo: t.y, src, target: t };
      }
    }
    if (ctx.snapToGrid && Number(ctx.gridSize) > 0) {
      const grid = Number(ctx.gridSize);
      const target = Math.round(val / grid) * grid;
      const d = Math.abs(target - val);
      if (d < SNAP_PX && d < bestDy) {
        bestDy = d;
        bestY = { snapTo: target, src, target: { y: target, label: 'grid' } };
      }
    }
  }

  const guides = [];
  let snappedX = nx, snappedY = ny;
  if (bestX) {
    if (bestX.src === 'l') snappedX = bestX.snapTo;
    else if (bestX.src === 'cx') snappedX = bestX.snapTo - w / 2;
    else snappedX = bestX.snapTo - w;
    guides.push({ vertical: true, x: bestX.snapTo });
  }
  if (bestY) {
    if (bestY.src === 't') snappedY = bestY.snapTo;
    else if (bestY.src === 'cy') snappedY = bestY.snapTo - h / 2;
    else snappedY = bestY.snapTo - h;
    guides.push({ vertical: false, y: bestY.snapTo });
  }
  return { x: snappedX, y: snappedY, guides };
}

/** Snap a rotated transform frame by its visible bounds and center. */
export function computeTransformSnap(frame, proposedCenter, ctx = {}) {
  const zoom = Number.isFinite(Number(ctx.zoom)) ? Number(ctx.zoom) : 1;
  const threshold = 6 / Math.max(zoom, 0.0001);
  const geometry = transformFrameGeometry({
    ...frame,
    centerX: proposedCenter.x,
    centerY: proposedCenter.y,
  });
  const points = Object.values(geometry.corners);
  const minX = Math.min(...points.map(point => point.x));
  const maxX = Math.max(...points.map(point => point.x));
  const minY = Math.min(...points.map(point => point.y));
  const maxY = Math.max(...points.map(point => point.y));
  const sourcesX = [minX, geometry.centerX, maxX];
  const sourcesY = [minY, geometry.centerY, maxY];
  const vertical = [0, Number(ctx.canvasW) || 0, (Number(ctx.canvasW) || 0) / 2];
  const horizontal = [0, Number(ctx.canvasH) || 0, (Number(ctx.canvasH) || 0) / 2];
  for (const value of Array.isArray(ctx.verticalGuides) ? ctx.verticalGuides : []) {
    if (Number.isFinite(Number(value))) vertical.push(Number(value));
  }
  for (const value of Array.isArray(ctx.horizontalGuides) ? ctx.horizontalGuides : []) {
    if (Number.isFinite(Number(value))) horizontal.push(Number(value));
  }
  for (const layer of Array.isArray(ctx.otherLayers) ? ctx.otherLayers : []) {
    if (!layer?.visible || !layer.canvas) continue;
    const offset = layer.offset || { x: 0, y: 0 };
    const left = Number(offset.x) || 0;
    const top = Number(offset.y) || 0;
    const right = left + (Number(layer.canvas.width) || 0);
    const bottom = top + (Number(layer.canvas.height) || 0);
    vertical.push(left, (left + right) / 2, right);
    horizontal.push(top, (top + bottom) / 2, bottom);
  }

  let bestX = null;
  let bestY = null;
  const consider = (source, target, current) => {
    const delta = target - source;
    if (Math.abs(delta) >= threshold) return current;
    return !current || Math.abs(delta) < Math.abs(current.delta) ? { delta, target } : current;
  };
  for (const source of sourcesX) for (const target of vertical) bestX = consider(source, target, bestX);
  for (const source of sourcesY) for (const target of horizontal) bestY = consider(source, target, bestY);
  if (ctx.snapToGrid && Number(ctx.gridSize) > 0) {
    const size = Number(ctx.gridSize);
    for (const source of sourcesX) bestX = consider(source, Math.round(source / size) * size, bestX);
    for (const source of sourcesY) bestY = consider(source, Math.round(source / size) * size, bestY);
  }
  return {
    centerX: proposedCenter.x + (bestX?.delta || 0),
    centerY: proposedCenter.y + (bestY?.delta || 0),
    guides: [
      ...(bestX ? [{ vertical: true, x: bestX.target }] : []),
      ...(bestY ? [{ vertical: false, y: bestY.target }] : []),
    ],
  };
}


/**
 * CSS cursor name for each transform-tool handle.
 *
 * @param {'tl'|'t'|'tr'|'r'|'br'|'b'|'bl'|'l'|'rot'|string} id
 * @param {number} rotation Current transform-frame rotation in degrees.
 * @returns {string}
 */
export function cursorForHandle(id, rotation = 0) {
  if (id === 'rot') return 'grab';
  const localAngles = {
    r: 0, br: 45, b: 90, bl: 135,
    l: 180, tl: 225, t: 270, tr: 315,
  };
  if (!(id in localAngles)) return 'default';
  const angle = ((localAngles[id] + Number(rotation || 0)) % 180 + 180) % 180;
  const direction = Math.round(angle / 45) % 4;
  if (direction === 0) return 'ew-resize';
  if (direction === 1) return 'nwse-resize';
  if (direction === 2) return 'ns-resize';
  return 'nesw-resize';
}
