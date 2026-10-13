/** Canvas viewport navigation shared by mouse, pen, and touch input. */
export function isDirectPanIntent(tool, temporaryPan, button = 0) {
  return tool === 'hand' || temporaryPan === true || button === 1;
}

export function nextPanOffset(startPointer, startPan, currentPointer) {
  return {
    x: startPan.x + currentPointer.x - startPointer.x,
    y: startPan.y + currentPointer.y - startPointer.y,
  };
}

export function applyCanvasPan(state, canvasArea, x, y) {
  const panX = Number.isFinite(Number(x)) ? Number(x) : 0;
  const panY = Number.isFinite(Number(y)) ? Number(y) : 0;
  state.panX = panX;
  state.panY = panY;
  canvasArea.dataset.panX = String(panX);
  canvasArea.dataset.panY = String(panY);
  const transform = `translate3d(${panX}px, ${panY}px, 0)`;
  if (state.mainCanvas) state.mainCanvas.style.transform = transform;
  if (state.selectionOverlay) state.selectionOverlay.style.transform = transform;
  if (state.transformOverlay) state.transformOverlay.style.transform = transform;
  return { x: panX, y: panY };
}

export function syncPanCursor(state, canvasArea, panning = false) {
  const ready = state.tool === 'hand' || state.spacePanActive === true;
  canvasArea.classList.toggle('ge-pan-ready', ready && !panning);
  canvasArea.classList.toggle('ge-panning', panning);
}
