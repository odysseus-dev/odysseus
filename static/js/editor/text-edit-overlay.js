/** Direct on-canvas editor for retained text layers. */

function textFont(text) {
  const family = text.fontFamily.includes(',') ? text.fontFamily : `"${text.fontFamily.replace(/["\\]/g, '')}", Arial, sans-serif`;
  return `${text.fontStyle} ${text.fontWeight} ${text.fontSize}px/${text.lineHeight} ${family}`;
}

export function createTextEditOverlay({ state, onInput, onCommit, onCancel }) {
  let input = null;
  let layer = null;
  let resizeObserver = null;
  let frame = 0;

  function position() {
    if (!input || !layer || !state.mainCanvas?.isConnected) return;
    const canvasRect = state.mainCanvas.getBoundingClientRect();
    const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    const scaleX = canvasRect.width / Math.max(1, state.mainCanvas.width);
    const scaleY = canvasRect.height / Math.max(1, state.mainCanvas.height);
    const text = layer.text;
    input.style.left = `${canvasRect.left + offset.x * scaleX}px`;
    input.style.top = `${canvasRect.top + offset.y * scaleY}px`;
    input.style.minWidth = `${Math.max(80, text.frameWidth * scaleX)}px`;
    input.style.width = `${Math.max(80, text.frameWidth * scaleX)}px`;
    input.style.minHeight = `${Math.max(36, layer.canvas.height * scaleY)}px`;
    input.style.font = textFont({ ...text, fontSize: text.fontSize * scaleY });
    input.style.letterSpacing = `${text.letterSpacing * scaleX}px`;
    input.style.textAlign = text.align;
    input.style.color = text.color;
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
    input.remove();
    input = null;
    const target = layer;
    layer = null;
    if (commit) onCommit?.(target, value); else onCancel?.(target);
  }

  function open(target, { selectAll = false } = {}) {
    close(true);
    layer = target;
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
      if (ready && input && document.activeElement !== input) close(true);
    }, 0));
    document.body.appendChild(input);
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
