// Makes a modal dialog draggable by its header, Photoshop-style.
//
// On the first grab the dialog is switched to `position:fixed` anchored at
// its current on-screen box, so it follows the pointer independently of the
// host modal's centering/transform layout. Dragging is clamped to the
// viewport and ignores presses that start on interactive controls (buttons,
// inputs, selects) so clicking Cancel/Create never becomes a drag.
export function makeDraggable(dialog, handle) {
  if (!dialog || !handle || dialog.__geDraggable) return;
  dialog.__geDraggable = true;
  handle.classList.add('ge-draggable-handle');

  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;
  let dragging = false;

  const onDown = (event) => {
    if (event.button != null && event.button !== 0) return;
    if (event.target?.closest?.('button, input, select, textarea, a, [role="button"]')) return;
    const rect = dialog.getBoundingClientRect();
    dialog.style.position = 'fixed';
    dialog.style.margin = '0';
    dialog.style.left = `${rect.left}px`;
    dialog.style.top = `${rect.top}px`;
    dialog.style.right = 'auto';
    dialog.style.bottom = 'auto';
    dialog.style.transform = 'none';
    startX = event.clientX;
    startY = event.clientY;
    originLeft = rect.left;
    originTop = rect.top;
    dragging = true;
    handle.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  };

  const onMove = (event) => {
    if (!dragging) return;
    const width = dialog.offsetWidth;
    const height = dialog.offsetHeight;
    const left = Math.max(0, Math.min(window.innerWidth - width, originLeft + (event.clientX - startX)));
    const top = Math.max(0, Math.min(window.innerHeight - height, originTop + (event.clientY - startY)));
    dialog.style.left = `${left}px`;
    dialog.style.top = `${top}px`;
  };

  const onUp = (event) => {
    if (!dragging) return;
    dragging = false;
    handle.releasePointerCapture?.(event.pointerId);
  };

  handle.addEventListener('pointerdown', onDown);
  handle.addEventListener('pointermove', onMove);
  handle.addEventListener('pointerup', onUp);
  handle.addEventListener('pointercancel', onUp);
}

// Clears the inline positioning set by makeDraggable so the next open
// re-centers the dialog inside its host modal.
export function resetDraggable(dialog) {
  if (!dialog) return;
  dialog.style.position = '';
  dialog.style.margin = '';
  dialog.style.left = '';
  dialog.style.top = '';
  dialog.style.right = '';
  dialog.style.bottom = '';
  dialog.style.transform = '';
}
