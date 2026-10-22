/** Canvas drawing tools, local drawing undo and PNG uploads. */
export function createDrawing({
  lifecycle,
  _safeImgSrc,
  api,
  attachColorPicker
}) {
  function _buildDrawHtml() {
    return `
    <div class="note-form-draw-wrap">
      <canvas class="note-form-canvas" width="600" height="320"></canvas>
      <div class="note-form-draw-toolbar">
        <input type="color" class="note-form-draw-color" title="Stroke color" value="#222222" />
        <label class="note-form-draw-tool note-form-draw-size-wrap" title="Stroke size">
          <input type="range" class="note-form-draw-size" min="1" max="24" value="3" />
        </label>
        <div class="note-form-draw-be" role="group">
          <button type="button" class="note-form-draw-be-btn note-form-draw-brush active" data-mode="pen" title="Brush">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04 0-1.67-1.34-3.02-3-3.02z"/></svg>
          </button>
          <button type="button" class="note-form-draw-be-btn note-form-draw-eraser" data-mode="eraser" title="Eraser">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/></svg>
          </button>
        </div>
        <button type="button" class="note-form-draw-undo" title="Undo" aria-label="Undo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"></polyline><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"></path></svg>
        </button>
        <button type="button" class="note-form-draw-text" title="Add text — click to cycle size" aria-label="Add text">T<span class="note-form-draw-text-badge"></span></button>
        <button type="button" class="note-form-draw-line" title="Line — click to cycle size">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="20" x2="20" y2="4"/></svg>
          <span class="note-form-draw-shape-badge"></span>
        </button>
        <button type="button" class="note-form-draw-circle" title="Circle — click to cycle size">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/></svg>
          <span class="note-form-draw-shape-badge"></span>
        </button>
      </div>
    </div>
  `;
  }

  function _wireCanvas(container, initialImageUrl) {
    const canvas = container.querySelector('.note-form-canvas');
    if (!canvas) return;
    const resolveThemeColor = (expression, fallback) => {
      const probe = document.createElement('span');
      probe.style.color = expression;
      probe.style.position = 'fixed';
      probe.style.visibility = 'hidden';
      document.body.appendChild(probe);
      const resolved = getComputedStyle(probe).color || fallback;
      probe.remove();
      return resolved;
    };
    const colorToHex = (color, fallback) => {
      try {
        const probe = document.createElement('canvas');
        probe.width = probe.height = 1;
        const probeCtx = probe.getContext('2d');
        probeCtx.fillStyle = color;
        probeCtx.fillRect(0, 0, 1, 1);
        const [r, g, b] = probeCtx.getImageData(0, 0, 1, 1).data;
        return `#${[r, g, b].map(v => v.toString(16).padStart(2, '0')).join('')}`;
      } catch { return fallback; }
    };
    const surfaceColor = resolveThemeColor('var(--panel, var(--bg))', '#282c34');
    const surfaceColorHex = colorToHex(surfaceColor, '#282c34');
    const highlightColor = colorToHex(
      resolveThemeColor('var(--accent, var(--red))', '#e06c75'),
      '#e06c75',
    );
    const colorInput = container.querySelector('.note-form-draw-color');
    if (colorInput) colorInput.value = highlightColor;
    // Bump the backing-store resolution for retina displays so strokes stay
    // crisp. Set only style.width : leaving style.height to auto so the canvas
    // scales uniformly via its intrinsic aspect ratio. If both CSS dimensions
    // are pinned, max-width:100% shrinks the width only, leaving rasterized
    // glyphs visibly stretched relative to their on-screen input.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cssW = canvas.width;
    const cssH = canvas.height;
    // Fill the container up to the logical width (don't pin a hard 600px,
    // which on a narrow phone forces the card wider than the viewport and
    // pushes the drawing outside the note). _pos() scales pointer coords by
    // the actual displayed width, so accuracy is preserved at any size.
    canvas.style.width = '100%';
    canvas.style.maxWidth = cssW + 'px';
    canvas.style.height = 'auto';
    canvas.style.aspectRatio = cssW + ' / ' + cssH;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.fillStyle = surfaceColor;
    ctx.fillRect(0, 0, cssW, cssH);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    // Load prior drawing as starting point so consecutive edits compose.
    const safeInitialImageUrl = _safeImgSrc(initialImageUrl);
    if (safeInitialImageUrl) {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => { try { ctx.drawImage(img, 0, 0, cssW, cssH); } catch {} };
      img.src = safeInitialImageUrl;
      // Float an X over the canvas so the user can blank it out and go back to
      // a clean draw surface. Removes itself once clicked.
      const wrap = container.querySelector('.note-form-draw-wrap');
      if (wrap && !wrap.querySelector('.note-form-draw-bg-rm')) {
        const rm = document.createElement('button');
        rm.type = 'button';
        rm.className = 'note-form-draw-bg-rm';
        rm.title = 'Clear photo (regular draw)';
        rm.innerHTML = '&times;';
        rm.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          ctx.fillStyle = surfaceColor;
          ctx.fillRect(0, 0, cssW, cssH);
          rm.remove();
        });
        wrap.appendChild(rm);
      }
    }

    // Swap the native browser color dialog for the in-house HSV picker
    // (same one used by Themes + the gallery editor). Existing `input` event
    // listeners + .value reads keep working : see colorPicker.js.
    if (colorInput) attachColorPicker(colorInput);
    const sizeInput = container.querySelector('.note-form-draw-size');
    const beSeg = container.querySelector('.note-form-draw-be');
    const brushBtn = container.querySelector('.note-form-draw-brush');
    const eraserBtn = container.querySelector('.note-form-draw-eraser');
    const textBtn = container.querySelector('.note-form-draw-text');
    const lineBtn = container.querySelector('.note-form-draw-line');
    const circleBtn = container.querySelector('.note-form-draw-circle');
    const undoBtn = container.querySelector('.note-form-draw-undo');
    // Single source of truth for what clicks/drags do. Other booleans are
    // derived from this so we never end up with conflicting "eraser AND text"
    // states (the bug that made T appear broken after using the eraser).
    // Modes: 'pen' | 'eraser' | 'text-s' | 'text-m' | 'text-l' | 'line' | 'circle'
    let mode = 'pen';
    let drawing = false;
    let last = null;
    // The text tool has three preset sizes (CSS px font-size).
    const TEXT_SIZES = { 's': 16, 'm': 26, 'l': 40 };
    // Line / circle stroke widths in logical pixels : three crisp options.
    const SHAPE_WIDTHS = { 's': 2, 'm': 5, 'l': 10 };
    // Snapshot taken at the start of a shape drag so the preview can repaint
    // cleanly on each move without accumulating intermediate strokes.
    let _shapeSnapshot = null;

    // Per-canvas undo stack. We snapshot the bitmap (as ImageData) BEFORE each
    // operation : stroke, text commit, or future operations : and pop+restore
    // on Undo. Cap to 30 to keep memory bounded.
    const _undoStack = [];
    const UNDO_LIMIT = 30;
    const _syncUndoButton = () => {
      const available = _undoStack.length > 0;
      undoBtn?.classList.toggle('has-undo', available);
      undoBtn?.toggleAttribute('disabled', !available);
      undoBtn?.setAttribute('aria-disabled', available ? 'false' : 'true');
    };
    const _markCanvasDirty = () => container.dispatchEvent(new Event('input', { bubbles: true }));
    const _snapshot = () => {
      try {
        const w = canvas.width, h = canvas.height;
        _undoStack.push(ctx.getImageData(0, 0, w, h));
        if (_undoStack.length > UNDO_LIMIT) _undoStack.shift();
        _syncUndoButton();
      } catch {}
    };
    const _undo = () => {
      const prev = _undoStack.pop();
      if (!prev) { _syncUndoButton(); return; }
      // Restore against the raw backing store: temporarily reset the active
      // ctx scale, paint the snapshot 1:1, then reapply our standard transform.
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.putImageData(prev, 0, 0);
      ctx.restore();
      _markCanvasDirty();
      _syncUndoButton();
    };

    const _pos = (e) => {
      // CSS can shrink the canvas (max-width:100%) without changing its logical
      // size, so compute the displayed-to-logical scale per axis. Pointer coords
      // are in CSS pixels; the dpr-scaled ctx expects logical (cssW × cssH).
      const r = canvas.getBoundingClientRect();
      const sx = cssW / r.width;
      const sy = cssH / r.height;
      const t = e.touches ? e.touches[0] : e;
      return { x: (t.clientX - r.left) * sx, y: (t.clientY - r.top) * sy };
    };
    const _begin = (e) => {
      if (mode.startsWith('text-')) {
        // Stop the event so the browser doesn't synthesize a follow-up click
        // that would blur the input we're about to create.
        e.preventDefault?.();
        e.stopPropagation?.();
        _openTextInput(e);
        return;
      }
      _snapshot();
      last = _pos(e);
      drawing = true;
      if (mode.startsWith('line-') || mode.startsWith('circle-')) {
        // Capture the backing pixels so the preview can replay each move from
        // the same starting state (otherwise live shapes accumulate).
        try { _shapeSnapshot = ctx.getImageData(0, 0, canvas.width, canvas.height); } catch {}
        return;
      }
      ctx.beginPath();
      ctx.moveTo(last.x, last.y);
    };

    // Drop an HTML input at the click position so the user can type, then
    // rasterize the text onto the canvas at blur/Enter. Mirrors how PDF form
    // annotations work in our doc editor.
    let _activeTextInput = null;
    const _openTextInput = (e) => {
      // Commit any prior pending input before starting a new one : otherwise
      // the first click leaves an orphaned input the user thinks "didn't work".
      if (_activeTextInput) { try { _activeTextInput.blur(); } catch {} }
      const r = canvas.getBoundingClientRect();
      const t = e.touches ? e.touches[0] : e;
      // Position is anchored to the wrap, not the canvas; since the canvas is
      // the first child of the wrap and the wrap has no padding, they share an
      // origin, so we offset from the canvas rect directly.
      const px = t.clientX - r.left;
      const py = t.clientY - r.top;
      const logical = _pos(e);
      // Size is decided by which T variant is active (S/M/L), not the stroke
      // slider : those are independent dials.
      const sizeKey = mode.startsWith('text-') ? mode.slice(-1) : 'm';
      const sizeCss = TEXT_SIZES[sizeKey] || TEXT_SIZES.m;
      const wrap = container.querySelector('.note-form-draw-wrap');
      if (!wrap) return;
      if (getComputedStyle(wrap).position === 'static') wrap.style.position = 'relative';
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'note-form-draw-textinput';
      input.placeholder = 'type then Enter';
      const color = colorInput?.value || '#222';
      const maxW = Math.max(120, Math.floor(r.width - px - 4));
      input.style.cssText = [
        'position:absolute',
        `left:${px}px`,
        `top:${Math.max(0, py - sizeCss * 0.7)}px`,
        `font:${sizeCss}px Arial, sans-serif`,
        `color:${color}`,
        'background:#ffffff',
        'border:2px solid var(--accent)',
        'border-radius:4px',
        'outline:none',
        'padding:2px 6px',
        'min-width:120px',
        `max-width:${maxW}px`,
        'z-index:1000',
        'box-shadow:0 2px 8px rgba(0,0,0,0.25)',
        'pointer-events:auto',
      ].join(';');
      wrap.appendChild(input);
      _activeTextInput = input;
      // Focus synchronously so the call still counts as inside the user gesture
      // on iOS / Android, then re-focus on the next frame in case a competing
      // synthetic event (touch → click) moved focus away.
      input.focus();
      requestAnimationFrame(() => { if (document.activeElement !== input) input.focus(); });
      let committed = false;
      const commit = () => {
        if (committed) return;
        committed = true;
        const text = input.value;
        if (_activeTextInput === input) _activeTextInput = null;
        input.remove();
        if (!text) return;
        // Snapshot BEFORE rasterizing so Undo removes the text in one step.
        _snapshot();
        ctx.save();
        ctx.globalCompositeOperation = 'source-over';
        ctx.fillStyle = color;
        // Canvas now scales uniformly (style.height: auto), so a single ratio
        // suffices for picking the logical font size that matches what the
        // user just saw in the HTML input.
        const sx = cssW / r.width;
        const logicalSize = sizeCss * sx;
        ctx.font = `${logicalSize}px sans-serif`;
        ctx.textBaseline = 'top';
        ctx.fillText(text, logical.x, logical.y - logicalSize * 0.7);
        ctx.restore();
        _markCanvasDirty();
      };
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
        else if (ev.key === 'Escape') { input.value = ''; input.blur(); }
      });
    };
    const _move = (e) => {
      if (!drawing) return;
      e.preventDefault?.();
      const p = _pos(e);
      if (mode.startsWith('line-') || mode.startsWith('circle-')) {
        // Restore the pre-shape bitmap, then redraw the preview shape from
        // anchor → current pointer.
        if (_shapeSnapshot) {
          ctx.save();
          ctx.setTransform(1, 0, 0, 1, 0, 0);
          ctx.putImageData(_shapeSnapshot, 0, 0);
          ctx.restore();
        }
        ctx.globalCompositeOperation = 'source-over';
        ctx.strokeStyle = colorInput?.value || '#222';
        const sizeKey = mode.slice(-1);
        ctx.lineWidth = SHAPE_WIDTHS[sizeKey] || SHAPE_WIDTHS.m;
        ctx.beginPath();
        if (mode.startsWith('line-')) {
          ctx.moveTo(last.x, last.y);
          ctx.lineTo(p.x, p.y);
        } else {
          const dx = p.x - last.x;
          const dy = p.y - last.y;
          const radius = Math.hypot(dx, dy);
          ctx.arc(last.x, last.y, radius, 0, Math.PI * 2);
        }
        ctx.stroke();
        return;
      }
      const erasing = mode === 'eraser';
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = erasing ? surfaceColor : (colorInput?.value || highlightColor);
      ctx.lineWidth = Number(sizeInput?.value || 3) * (erasing ? 2.5 : 1);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
      last = p;
    };
    const _end = () => {
      if (drawing) _markCanvasDirty();
      drawing = false; last = null; _shapeSnapshot = null;
    };

    canvas.addEventListener('mousedown', _begin);
    canvas.addEventListener('mousemove', _move);
    lifecycle.listen(canvas, window, 'mouseup', _end);
    // Non-passive so text mode can preventDefault : otherwise the synthetic
    // mousedown/click that follows a touch can blur the freshly-created text
    // input on iOS Safari, making T look like a no-op.
    canvas.addEventListener('touchstart', (e) => { if (mode.startsWith('text-')) e.preventDefault(); _begin(e); }, { passive: false });
    canvas.addEventListener('touchmove', _move, { passive: false });
    canvas.addEventListener('touchend', _end);
    canvas.addEventListener('touchcancel', _end);

    // Single mode setter : keeps the toolbar, swatch, and cursor in sync, and
    // makes sure exiting the eraser restores the user's chosen color whether
    // they exit by clicking another tool or by toggling eraser off.
    let _preEraseColor = null;
    const _setMode = (next) => {
      const wasEraser = mode === 'eraser';
      mode = next;
      const isEraser = next === 'eraser';
      const isPen = next === 'pen';
      const isText = next.startsWith('text-');
      // Swatch: theme surface while erasing, restored when leaving that mode.
      if (isEraser && !wasEraser && colorInput) {
        _preEraseColor = colorInput.value;
        colorInput.value = surfaceColorHex;
      } else if (!isEraser && wasEraser && colorInput && _preEraseColor) {
        colorInput.value = _preEraseColor;
        _preEraseColor = null;
      }
      // Brush/Eraser segmented pill : slides to the active side. When a non-
      // pen/eraser tool (T / line / circle) is active, neither half is
      // highlighted, but the pill still indicates which side the user was on
      // last so they can return with one click.
      const isLine = next.startsWith('line-');
      const isCircle = next.startsWith('circle-');
      beSeg?.classList.toggle('is-eraser', isEraser);
      brushBtn?.classList.toggle('active', isPen);
      eraserBtn?.classList.toggle('active', isEraser);
      textBtn?.classList.toggle('active', isText);
      lineBtn?.classList.toggle('active', isLine);
      circleBtn?.classList.toggle('active', isCircle);
      // Per-button size badges (S/M/L), driven off the mode suffix.
      const tBadge = textBtn?.querySelector('.note-form-draw-text-badge');
      if (tBadge) tBadge.textContent = isText ? next.slice(-1).toUpperCase() : '';
      const lBadge = lineBtn?.querySelector('.note-form-draw-shape-badge');
      if (lBadge) lBadge.textContent = isLine ? next.slice(-1).toUpperCase() : '';
      const cBadge = circleBtn?.querySelector('.note-form-draw-shape-badge');
      if (cBadge) cBadge.textContent = isCircle ? next.slice(-1).toUpperCase() : '';
      // Reflect the chosen size in the icon itself : thicker line/circle stroke
      // for M+L, larger T glyph for M+L. CSS rules read `.size-s/.size-m/.size-l`.
      const _sz = next.slice(-1);
      [textBtn, lineBtn, circleBtn].forEach(b => b?.classList.remove('size-s', 'size-m', 'size-l'));
      if (isText && /[sml]/.test(_sz)) textBtn?.classList.add('size-' + _sz);
      if (isLine && /[sml]/.test(_sz)) lineBtn?.classList.add('size-' + _sz);
      if (isCircle && /[sml]/.test(_sz)) circleBtn?.classList.add('size-' + _sz);
      canvas.style.cursor = isText ? 'text' : 'crosshair';
    };
    brushBtn?.addEventListener('click', () => _setMode('pen'));
    eraserBtn?.addEventListener('click', () => _setMode('eraser'));
    // T / Line / Circle: each cycles its own three sizes, then back to pen.
    const _cycle = (prefix) => {
      const seq = ['s', 'm', 'l'];
      if (!mode.startsWith(prefix)) return prefix + 's';
      const cur = mode.slice(-1);
      const next = seq[seq.indexOf(cur) + 1];
      return next ? prefix + next : 'pen';
    };
    textBtn?.addEventListener('click', () => _setMode(_cycle('text-')));
    lineBtn?.addEventListener('click', () => _setMode(_cycle('line-')));
    circleBtn?.addEventListener('click', () => _setMode(_cycle('circle-')));
    undoBtn?.addEventListener('click', () => _undo());
    _syncUndoButton();

    // Stash so the save handler can read it later without re-resolving DOM.
    canvas._cssW = cssW;
    canvas._cssH = cssH;
    return canvas;
  }

  async function _uploadCanvasAsPng(canvas, galleryId = null) {
    if (!canvas) return null;
    const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
    if (!blob) return null;
    const fd = new FormData();
    fd.append('files', blob, 'drawing.png');
    if (galleryId) fd.append('gallery_id', galleryId);
    try {
      const res = await api.upload(fd);
      const data = await res.json();
      const id = data.files?.[0]?.id;
      return id ? {
        url: api.uploadUrl(id),
        galleryId: data.files?.[0]?.gallery_id || null,
      } : null;
    } catch { return null; }
  }

  return { _buildDrawHtml, _wireCanvas, _uploadCanvasAsPng };
}
