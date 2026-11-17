/** Direct on-canvas editor for retained text layers. */

function textFont(text) {
  const family = text.fontFamily.includes(',') ? text.fontFamily : `"${text.fontFamily.replace(/["\\]/g, '')}", Arial, sans-serif`;
  return `${text.fontStyle} ${text.fontWeight} ${text.fontSize}px/${text.lineHeight} ${family}`;
}

export function createTextEditOverlay({ state, onInput, onCommit, onCancel }) {
  let input = null;
  let layer = null;
  let resizeObserver = null;
  let handle = null;
  let frame = 0;
  let appliedWidth = 0;
  let resizing = false;

  function position() {
    if (!input || !layer || !state.mainCanvas?.isConnected) return;
    const canvasRect = state.mainCanvas.getBoundingClientRect();
    const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const scaleX = canvasRect.width / Math.max(1, state.mainCanvas.width);
    const scaleY = canvasRect.height / Math.max(1, state.mainCanvas.height);
    const text = layer.text;
    input.style.left = `${canvasRect.left + offset.x * scaleX}px`;
    input.style.top = `${canvasRect.top + offset.y * scaleY}px`;
    // Only rewrite the width when the model's frame width (or the zoom) changed.
    // Writing it every animation frame overwrote the native resize handle's
    // width before the ResizeObserver could report it, so the box could not be
    // resized at all.
    const targetW = Math.max(80, text.frameWidth * scaleX);
    if (!resizing && Math.abs(targetW - appliedWidth) > 0.5) {
      input.style.width = `${targetW}px`;
      appliedWidth = targetW;
    }
    input.style.minWidth = '40px';
    input.style.minHeight = `${Math.max(36, layer.canvas.height * scaleY)}px`;
    input.style.font = textFont({ ...text, fontSize: text.fontSize * scaleY });
    input.style.letterSpacing = `${text.letterSpacing * scaleX}px`;
    input.style.textAlign = text.align;
    input.style.color = text.color;
    if (handle) {
      const rect = input.getBoundingClientRect();
      handle.style.left = `${rect.right - 8}px`;
      handle.style.top = `${rect.bottom - 8}px`;
      handle.style.display = '';
    }
  }

  function loop() {
    position();
    if (input) frame = requestAnimationFrame(loop);
  }

  function close(commit = true) {
    if (!input) return;
    const value = input.value;
    resizeObserver?.disconnect();
    cancelAnimationFrame(frame);
    handle?.remove();
    handle = null;
    resizing = false;
    input.remove();
    input = null;
    const target = layer;
    layer = null;
    if (commit) onCommit?.(target, value); else onCancel?.(target);
  }

  function open(target, { selectAll = false } = {}) {
    close(true);
    layer = target;
    appliedWidth = 0;
    input = document.createElement('textarea');
    input.className = 'ge-direct-text-editor';
    input.value = target.text.content;
    input.setAttribute('aria-label', 'Edit text on canvas');
    input.spellcheck = true;
    input.addEventListener('input', () => onInput?.(target, input.value, input));
    input.addEventListener('keydown', event => {
      event.stopPropagation();
      if (event.key === 'Escape') {
        event.preventDefault();
        close(false);
      } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        close(true);
      }
    });
    let ready = false;
    input.addEventListener('blur', () => setTimeout(() => {
      if (ready && input && !resizing && document.activeElement !== input) close(true);
    }, 0));
    document.body.appendChild(input);
    // A visible grab square in the corner: the native textarea resize grip is
    // invisible in this theme and only a few pixels wide, so most drags missed
    // it. This handle also survives the animation-frame positioning pass.
    handle = document.createElement('div');
    handle.className = 'ge-direct-text-handle';
    handle.title = 'Drag to resize';
    handle.setAttribute('aria-hidden', 'true');
    handle.style.display = 'none';
    // Same dot-grip language as the layer-row drag handles, arranged as a
    // corner wedge so it reads as "resize from this corner".
    handle.innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true"><circle cx="4" cy="9" r="1.1"/><circle cx="7" cy="9" r="1.1"/><circle cx="10" cy="9" r="1.1"/><circle cx="7" cy="6" r="1.1"/><circle cx="10" cy="6" r="1.1"/><circle cx="10" cy="3" r="1.1"/></svg>';
    let dragStart = null;
    handle.addEventListener('pointerdown', (event) => {
      if (!input || !layer || event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      const canvasRect = state.mainCanvas.getBoundingClientRect();
      const scale = canvasRect.width / Math.max(1, state.mainCanvas.width);
      const rect = input.getBoundingClientRect();
      dragStart = { x: event.clientX, y: event.clientY, w: rect.width, h: rect.height, scale };
      resizing = true;
      handle.setPointerCapture?.(event.pointerId);
    });
    handle.addEventListener('pointermove', (event) => {
      if (!dragStart || !input || !layer) return;
      const w = Math.max(80, Math.round(dragStart.w + (event.clientX - dragStart.x)));
      const h = Math.max(36, Math.round(dragStart.h + (event.clientY - dragStart.y)));
      input.style.width = `${w}px`;
      input.style.height = `${h}px`;
      appliedWidth = w;
      onInput?.(layer, input.value, input, w / Math.max(0.01, dragStart.scale));
    });
    const endDrag = () => {
      if (!dragStart) return;
      dragStart = null;
      resizing = false;
      if (input) appliedWidth = input.getBoundingClientRect().width;
      input?.focus({ preventScroll: true });
    };
    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);
    document.body.appendChild(handle);
    resizeObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => {
      if (!input || !layer) return;
      const canvasRect = state.mainCanvas.getBoundingClientRect();
      const scale = canvasRect.width / Math.max(1, state.mainCanvas.width);
      const width = Math.max(1, input.getBoundingClientRect().width / Math.max(0.01, scale));
      onInput?.(layer, input.value, input, width);
    }) : null;
    resizeObserver?.observe(input);
    position();
    frame = requestAnimationFrame(() => {
      if (!input) return;
      input.focus({ preventScroll: true });
      if (selectAll) input.select();
      ready = true;
      frame = requestAnimationFrame(loop);
    });
    return input;
  }

  return { open, close, position, isOpen: () => !!input, activeLayer: () => layer };
}
