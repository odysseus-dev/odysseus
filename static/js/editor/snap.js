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
 * }} ctx
 * @returns {{x: number, y: number, guides: Array}}
 */
export function computeSnap(layer, nx, ny, ctx) {
  const SNAP_PX = 6 / Math.max(ctx.zoom, 0.0001);
  const cw = ctx.canvasW, ch = ctx.canvasH;
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
  }
  for (const [src, val] of Object.entries(myEdgesY)) {
    for (const t of hTargets) {
      const d = Math.abs(t.y - val);
      if (d < SNAP_PX && d < bestDy) {
        bestDy = d;
        bestY = { snapTo: t.y, src, target: t };
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


/**
 * CSS cursor name for each transform-tool handle.
 *
 * @param {'tl'|'tr'|'bl'|'br'|'rot'|string} id
 * @returns {string}
 */
export function cursorForHandle(id) {
  switch (id) {
    case 'tl': case 'br': return 'nwse-resize';
    case 'tr': case 'bl': return 'nesw-resize';
    case 'rot': return 'grab';
    default: return 'default';
  }
}
