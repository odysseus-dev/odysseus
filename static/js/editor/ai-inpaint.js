/**
 * AI inpaint subsystem — Generate, Remove, and Outpaint variants
 * all share a single `runInpaint` core; only the prompt, strength,
 * and button-target differ. Returns a wireInpaintButtons() function
 * to attach handlers to the three buttons (#ge-inpaint-run,
 * #ge-inpaint-remove, #ge-inpaint-outpaint).
 *
 *   runInpaint:
 *     - Build a union mask from every visible mask sub-layer (across
 *       all parent layers) — the model sees the COMBINED region,
 *       not just the currently-active mask.
 *     - Crop around the mask plus context, then dilate the mask ~padPx
 *       so the model fills a buffer zone the post-gen Feather/Edge
 *       slider can fade into.
 *     - POST the bounded working image + dilated mask to /api/image/inpaint.
 *     - Drop the result as a new layer, cache the AI crop + hard mask
 *       on the layer for live edge tuning, apply an automatic expanded
 *       blend edge around the user's stroke, hide every
 *       contributing mask sub-layer, reveal the post-gen Feather +
 *       Edge Stroke sliders capped at ±padPx.
 *
 *   Remove: detects OpenAI vs SDXL backend and swaps the prompt
 *     (gpt-image-1 follows "remove …" semantically; SDXL has to be
 *     prompted with a fill description + strength 0.99).
 *
 *   Outpaint: auto-generates a mask covering empty (transparent)
 *     regions of the flattened composite, dilates it 12px inward
 *     so the model sees adjacent opaque pixels as context, runs
 *     inpaint, then restores the user's previous mask drawing.
 *
 * @param {{
 *   buildMergedMaskCanvas:  () => HTMLCanvasElement | null,
 *   dilateMask:             (src: HTMLCanvasElement, px: number) => HTMLCanvasElement,
 *   applyInpaintFeather:    (layer: object, featherPx: number, edgeShiftPx: number) => void,
 *   getSelectedAIEndpoint:  (type: string) => { endpoint?: string, model?: string },
 *   ensureActiveMaskLayer:  () => object | null,
 *   saveState:              (label?: string) => void,
 *   createLayer:            (name: string, w: number, h: number) => object,
 *   composite:              () => void,
 *   renderLayerPanel:       () => void,
 *   spinnerModule:          object,
 *   uiModule:               object | null,
 * }} deps
 */
import { state } from './state.js';

const INPAINT_MAX_WORK_PIXELS = 1024 * 1024;
const INPAINT_REQUEST_B64_BUDGET = 24 * 1024 * 1024;
const INPAINT_HISTORY_BYTE_BUDGET = 220 * 1024 * 1024;

function makeCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(w));
  c.height = Math.max(1, Math.round(h));
  return c;
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function maskBoundsFromCanvas(maskCanvas) {
  const w = maskCanvas?.width || 0;
  const h = maskCanvas?.height || 0;
  if (!w || !h) return null;
  const ctx = maskCanvas.getContext('2d', { willReadFrequently: true });
  let minX = w, minY = h, maxX = -1, maxY = -1;
  const tileH = 128;
  for (let y0 = 0; y0 < h; y0 += tileH) {
    const th = Math.min(tileH, h - y0);
    const data = ctx.getImageData(0, y0, w, th).data;
    for (let y = 0; y < th; y++) {
      const row = y * w * 4;
      const absY = y0 + y;
      for (let x = 0; x < w; x++) {
        if (data[row + x * 4 + 3] <= 0) continue;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (absY < minY) minY = absY;
        if (absY > maxY) maxY = absY;
      }
    }
  }
  if (maxX < minX || maxY < minY) return null;
  return { minX, minY, maxX, maxY };
}

function expandBounds(bounds, pad, width, height) {
  const x1 = clamp(Math.floor(bounds.minX - pad), 0, width - 1);
  const y1 = clamp(Math.floor(bounds.minY - pad), 0, height - 1);
  const x2 = clamp(Math.ceil(bounds.maxX + pad) + 1, x1 + 1, width);
  const y2 = clamp(Math.ceil(bounds.maxY + pad) + 1, y1 + 1, height);
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

function chooseInitialInpaintBlend(maskBounds, padPx) {
  const maskW = Math.max(1, maskBounds.maxX - maskBounds.minX + 1);
  const maskH = Math.max(1, maskBounds.maxY - maskBounds.minY + 1);
  const maskSpan = Math.max(maskW, maskH);
  const imageMin = Math.max(1, Math.min(state.imgWidth || maskW, state.imgHeight || maskH));
  const spanRatio = maskSpan / imageMin;
  const edgeRatio = spanRatio < 0.14 ? 0.85 : (spanRatio < 0.35 ? 0.70 : 0.55);
  const edgePx = clamp(Math.round(padPx * edgeRatio), 0, padPx);
  const featherPx = clamp(Math.round(Math.max(6, edgePx * 0.45)), 0, padPx);
  return { edgePx, featherPx };
}

function estimateUndoSnapshotBytes() {
  let pixels = 0;
  for (const layer of state.layers || []) {
    if (layer?.canvas) pixels += layer.canvas.width * layer.canvas.height;
    for (const mask of layer?.masks || []) {
      if (mask?.canvas) pixels += mask.canvas.width * mask.canvas.height;
    }
  }
  if (state.wandMask) pixels += state.wandMask.width * state.wandMask.height;
  if (state.rembgSampleCanvas) pixels += state.rembgSampleCanvas.width * state.rembgSampleCanvas.height;
  return pixels * 4;
}

function drawVisibleLayersToWorkCanvas(workCanvas, crop, scale) {
  const ctx = workCanvas.getContext('2d');
  ctx.save();
  ctx.clearRect(0, 0, workCanvas.width, workCanvas.height);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.scale(scale, scale);
  ctx.translate(-crop.x, -crop.y);
  for (const layer of state.layers) {
    if (!layer.visible) continue;
    ctx.globalAlpha = layer.opacity;
    const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
    ctx.drawImage(layer.canvas, off.x, off.y);
  }
  ctx.restore();
  ctx.globalAlpha = 1;
}

function thresholdMaskCanvas(maskCanvas) {
  const ctx = maskCanvas.getContext('2d');
  const img = ctx.getImageData(0, 0, maskCanvas.width, maskCanvas.height);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = img.data[i + 3] > 16 || img.data[i] > 16 || img.data[i + 1] > 16 || img.data[i + 2] > 16 ? 255 : 0;
    img.data[i] = v;
    img.data[i + 1] = v;
    img.data[i + 2] = v;
    img.data[i + 3] = v;
  }
  ctx.putImageData(img, 0, 0);
}

function prepareInpaintWork({ mergedMask, maskBounds, padPx, dilateMask }) {
  const minDim = Math.min(state.imgWidth, state.imgHeight);
  const contextPx = Math.min(512, Math.max(128, padPx * 3, Math.round(minDim * 0.08)));
  const crop = expandBounds(maskBounds, contextPx + padPx, state.imgWidth, state.imgHeight);
  const cropPixels = crop.w * crop.h;
  const scale = Math.min(1, Math.sqrt(INPAINT_MAX_WORK_PIXELS / Math.max(1, cropPixels)));
  const workW = Math.max(1, Math.round(crop.w * scale));
  const workH = Math.max(1, Math.round(crop.h * scale));

  const imageCanvas = makeCanvas(workW, workH);
  drawVisibleLayersToWorkCanvas(imageCanvas, crop, scale);

  const hardMaskCanvas = makeCanvas(crop.w, crop.h);
  hardMaskCanvas.getContext('2d').drawImage(
    mergedMask,
    crop.x, crop.y, crop.w, crop.h,
    0, 0, crop.w, crop.h,
  );

  const dilatedMaskCrop = dilateMask(hardMaskCanvas, padPx);
  const maskCanvas = makeCanvas(workW, workH);
  const maskCtx = maskCanvas.getContext('2d');
  maskCtx.imageSmoothingEnabled = true;
  maskCtx.imageSmoothingQuality = 'high';
  maskCtx.drawImage(dilatedMaskCrop, 0, 0, workW, workH);
  thresholdMaskCanvas(maskCanvas);
  try { dilatedMaskCrop.width = dilatedMaskCrop.height = 1; } catch (_) {}

  return {
    crop,
    scale,
    imageCanvas,
    maskCanvas,
    hardMaskCanvas,
    downscaled: scale < 0.999,
  };
}

function releaseRequestCanvases(work) {
  try {
    if (work?.imageCanvas) work.imageCanvas.width = work.imageCanvas.height = 1;
    if (work?.maskCanvas) work.maskCanvas.width = work.maskCanvas.height = 1;
  } catch (_) {}
}

function canvasToPngBase64(canvas) {
  return new Promise((resolve, reject) => {
    try {
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error('Could not encode inpaint canvas'));
          return;
        }
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('Could not read inpaint canvas'));
        reader.onload = () => {
          const value = String(reader.result || '');
          const comma = value.indexOf(',');
          resolve(comma >= 0 ? value.slice(comma + 1) : value);
        };
        reader.readAsDataURL(blob);
      }, 'image/png');
    } catch (err) {
      reject(err);
    }
  });
}

function makeInpaintProgressId() {
  try {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID().replace(/-/g, '');
    }
  } catch (_) {}
  return `inp_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 14)}`;
}

function formatInpaintElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes ? `${minutes}:${String(seconds).padStart(2, '0')}` : `${seconds}s`;
}

function phaseTitle(value) {
  return String(value || 'working')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

function appendInpaintProgressRow(listEl, label, detail, tone = '') {
  if (!listEl) return;
  const wasNearBottom = (listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight) < 18;
  const row = document.createElement('div');
  row.className = 'ge-inpaint-progress-row';
  if (tone) row.classList.add(`is-${tone}`);
  const rowLabel = document.createElement('span');
  rowLabel.className = 'ge-inpaint-progress-row-label';
  rowLabel.textContent = label || 'Working';
  row.appendChild(rowLabel);
  if (detail) {
    const rowDetail = document.createElement('span');
    rowDetail.className = 'ge-inpaint-progress-row-detail';
    rowDetail.textContent = detail;
    row.appendChild(rowDetail);
  }
  listEl.appendChild(row);
  while (listEl.children.length > 7) listEl.removeChild(listEl.firstElementChild);
  if (wasNearBottom) listEl.scrollTop = listEl.scrollHeight;
}

function createInpaintProgress({ title = 'Inpaint' } = {}) {
  const id = makeInpaintProgressId();
  const startedAt = Date.now();
  let destroyed = false;
  let waiting = false;
  let waitDetail = '';
  let closeTimer = null;
  let source = null;

  const root = document.createElement('div');
  root.className = 'ge-inpaint-progress';
  root.setAttribute('role', 'status');
  root.setAttribute('aria-live', 'polite');

  const head = document.createElement('div');
  head.className = 'ge-inpaint-progress-head';
  const dot = document.createElement('span');
  dot.className = 'ge-inpaint-progress-dot';
  dot.setAttribute('aria-hidden', 'true');
  const titleEl = document.createElement('span');
  titleEl.className = 'ge-inpaint-progress-title';
  titleEl.textContent = title;
  const elapsedEl = document.createElement('span');
  elapsedEl.className = 'ge-inpaint-progress-elapsed';
  head.append(dot, titleEl, elapsedEl);

  const statusEl = document.createElement('div');
  statusEl.className = 'ge-inpaint-progress-status';
  const detailEl = document.createElement('div');
  detailEl.className = 'ge-inpaint-progress-detail';

  const bar = document.createElement('div');
  bar.className = 'ge-inpaint-progress-bar';
  const fill = document.createElement('div');
  fill.className = 'ge-inpaint-progress-fill';
  bar.appendChild(fill);

  const listEl = document.createElement('div');
  listEl.className = 'ge-inpaint-progress-list';

  root.append(head, statusEl, detailEl, bar, listEl);
  document.body.appendChild(root);

  function tick() {
    const elapsed = formatInpaintElapsed(Date.now() - startedAt);
    elapsedEl.textContent = elapsed;
    if (waiting && waitDetail) {
      detailEl.textContent = `${waitDetail} · ${elapsed}`;
    }
  }

  const interval = window.setInterval(tick, 1000);
  tick();

  function setPercent(percent) {
    if (typeof percent !== 'number' || !Number.isFinite(percent)) return;
    fill.style.width = `${clamp(percent, 0, 100)}%`;
  }

  function update(label, detail = '', percent = null, tone = '') {
    if (destroyed) return;
    window.clearTimeout(closeTimer);
    root.classList.toggle('is-error', tone === 'error');
    root.classList.toggle('is-complete', tone === 'complete');
    statusEl.textContent = label || 'Working';
    waitDetail = detail || '';
    detailEl.textContent = waitDetail;
    setPercent(percent);
    appendInpaintProgressRow(listEl, label, detail, tone);
    tick();
  }

  function closeSoon(ms = 6500) {
    if (destroyed) return;
    window.clearTimeout(closeTimer);
    closeTimer = window.setTimeout(() => api.destroy(), ms);
  }

  const api = {
    id,
    step(label, detail = '', percent = null) {
      waiting = false;
      update(label, detail, percent);
    },
    wait(label, detail = '', percent = null) {
      waiting = true;
      update(label, detail, percent);
    },
    backend(event) {
      if (!event || destroyed) return;
      const label = phaseTitle(event.phase);
      const detail = String(event.message || '');
      if (event.error) {
        waiting = false;
        update(label || 'Backend failed', detail, event.percent ?? 100, 'error');
        closeSoon(9000);
        return;
      }
      waiting = event.phase === 'model_wait';
      update(label, detail, event.percent ?? null, event.done ? 'complete' : '');
    },
    attachBackend() {
      if (typeof window.EventSource !== 'function') return;
      try {
        source = new EventSource(`/api/image/inpaint/progress/${encodeURIComponent(id)}`);
        source.onmessage = (evt) => {
          try { api.backend(JSON.parse(evt.data || '{}')); } catch (_) {}
        };
        source.onerror = () => {
          try { source.close(); } catch (_) {}
          source = null;
        };
      } catch (_) {}
    },
    done(label = 'Inpaint complete', detail = '') {
      waiting = false;
      update(label, detail, 100, 'complete');
      closeSoon();
    },
    fail(label = 'Inpaint failed', detail = '') {
      waiting = false;
      update(label, detail, 100, 'error');
      closeSoon(9000);
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      window.clearInterval(interval);
      window.clearTimeout(closeTimer);
      if (source) {
        try { source.close(); } catch (_) {}
        source = null;
      }
      try { root.remove(); } catch (_) {}
    },
  };

  api.step('Preparing mask', 'Combining visible mask layers.', 8);
  return api;
}

export function wireInpaintButtons({
  buildMergedMaskCanvas, dilateMask, applyInpaintFeather,
  getSelectedAIEndpoint, ensureActiveMaskLayer,
  saveState, createLayer, composite, renderLayerPanel,
  spinnerModule, uiModule,
}) {
  // Shared inpaint runner — used by Generate, Remove, and Outpaint.
  async function runInpaint({ prompt, strength, btnId, labelId, idleLabel, busyLabel }) {
    // Pre-check: build the union mask the AI will receive and verify
    // at least one pixel is painted.
    const preMerged = buildMergedMaskCanvas();
    if (!preMerged) { if (uiModule) uiModule.showToast('Draw the area you want to inpaint first'); return; }
    const maskBounds = maskBoundsFromCanvas(preMerged);
    if (!maskBounds) { if (uiModule) uiModule.showToast('Draw the area you want to inpaint first'); return; }
    const btn = document.getElementById(btnId);
    const btnLabel = labelId ? document.getElementById(labelId) : null;
    btn.disabled = true;
    if (btnLabel) btnLabel.textContent = busyLabel;
    let runWp = null;
    try {
      runWp = spinnerModule.createWhirlpool(14);
      runWp.element.style.cssText = 'margin:0;flex-shrink:0;';
      btn.appendChild(runWp.element);
    } catch (_) { /* spinner is optional */ }
    // Canvas-overlay whirlpool — visual feedback right where the
    // user's working, since the run button lives in the side panel
    // and may be out of view at high zoom. Positioned over the
    // mask's centroid in viewport coords.
    let canvasWp = null;
    let canvasWpEl = null;
    try {
      const area = state.container && state.container.querySelector('.ge-canvas-area');
      const mainRect = state.mainCanvas.getBoundingClientRect();
      if (area && mainRect.width && mainRect.height) {
        // Find the mask's bbox so we can centre the whirlpool over it.
        const cx = (maskBounds.minX + maskBounds.maxX) / 2;
        const cy = (maskBounds.minY + maskBounds.maxY) / 2;
        const scaleX = mainRect.width / state.mainCanvas.width;
        const scaleY = mainRect.height / state.mainCanvas.height;
        const vpX = mainRect.left + cx * scaleX;
        const vpY = mainRect.top  + cy * scaleY;
        canvasWp = spinnerModule.create('', 'clean', 'whirlpool');
        canvasWpEl = canvasWp.createElement();
        canvasWpEl.style.cssText = `position:fixed;left:${vpX}px;top:${vpY}px;transform:translate(-50%,-50%);z-index:12;pointer-events:none;`;
        document.body.appendChild(canvasWpEl);
        canvasWp.start();
      }
    } catch (_) { /* overlay is decorative */ }
    let work = null;
    let progress = null;
    try {
      progress = createInpaintProgress({ title: idleLabel || 'Inpaint' });
      progress.attachBackend();
    } catch (_) { /* progress UI is optional */ }
    try {
      // Dilate the user's brush mask before sending to the model.
      // The AI fills a buffer zone around the brush. The initial
      // result now shows a dynamic part of that buffer by default so
      // seams can resolve around the drawn area instead of stopping at
      // the exact brush shape. The ORIGINAL (un-dilated) mask is still
      // cached on the layer so the post-gen sliders can shrink back to
      // the precise old edge or expand farther into the AI buffer.
      const padPx = Math.min(80, Math.max(20, Math.round(Math.min(state.imgWidth, state.imgHeight) * 0.04)));
      const initialBlend = chooseInitialInpaintBlend(maskBounds, padPx);
      // Build a bounded AI work area around the mask instead of
      // serialising the whole photo. Full-resolution phone images can
      // make Electron's renderer restart when inpaint keeps several
      // huge canvases and base64 strings alive at once.
      work = prepareInpaintWork({
        mergedMask: preMerged,
        maskBounds,
        padPx,
        dilateMask,
      });
      progress?.step(
        'Preparing crop',
        `${work.imageCanvas.width}x${work.imageCanvas.height} work area${work.downscaled ? ' (downscaled)' : ''}.`,
        25,
      );
      try { preMerged.width = preMerged.height = 1; } catch (_) {}
      progress?.step('Encoding request', 'Compressing image and mask PNGs.', 38);
      let imageB64 = await canvasToPngBase64(work.imageCanvas);
      let maskB64 = await canvasToPngBase64(work.maskCanvas);
      if ((imageB64.length + maskB64.length) > INPAINT_REQUEST_B64_BUDGET) {
        throw new Error('Inpaint request is too large. Try a smaller mask area or resize the canvas.');
      }
      const sel = getSelectedAIEndpoint('inpaint');
      const endpointDetail = [sel.model, sel.display || sel.endpoint].filter(Boolean).join(' · ') || 'Default image endpoint.';
      progress?.step('Selecting endpoint', endpointDetail, 48);
      const payload = {
        image: imageB64,
        mask: maskB64,
        prompt,
        width: work.imageCanvas.width,
        height: work.imageCanvas.height,
        strength,
        feather: initialBlend.featherPx,
        _endpoint: sel.endpoint,
        _endpoint_id: sel.endpointId,
        _model: sel.model,
        _progress_id: progress?.id || '',
      };
      imageB64 = null;
      maskB64 = null;
      let requestBody = JSON.stringify(payload);
      payload.image = '';
      payload.mask = '';
      progress?.wait('Model running', 'Waiting for inpaint endpoint response.', 62);
      const res = await fetch('/api/image/inpaint', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: requestBody,
      });
      requestBody = null;
      progress?.step('Reading response', `HTTP ${res.status}`, res.ok ? 78 : 100);
      if (!res.ok) {
        let errDetail = res.statusText;
        try { const errBody = await res.json(); errDetail = errBody.detail || errBody.error || errDetail; } catch {}
        throw new Error(errDetail);
      }
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      if (!data.image) throw new Error('No image returned from inpaint endpoint');
      progress?.step(
        'Receiving result',
        data.elapsed ? `Backend completed in ${data.elapsed}s.` : 'Decoding model response.',
        84,
      );
      releaseRequestCanvases(work);
      // Load result as a new layer and clip with an automatically
      // expanded, feathered version of the user-drawn mask so the
      // model can repair/blend a context band around the stroke. Cache
      // the unfeathered (AI image + hard mask) on the layer so the
      // live Feather/Edge Stroke sliders can re-derive the alpha on
      // each input event without re-running the model.
      const resultImg = new Image();
      resultImg.onload = () => {
        if (!state.editorOpen) {
          progress?.done('Inpaint response received', 'Editor was closed before rendering.');
          return;
        } // user closed mid-decode
        try {
          progress?.step('Rendering result', 'Compositing the returned image as a new layer.', 92);
          const saveUndo = estimateUndoSnapshotBytes() <= INPAINT_HISTORY_BYTE_BUDGET;
          if (saveUndo) {
            saveState('Inpaint result');
          } else {
            console.warn('[inpaint] skipped undo snapshot for large canvas to avoid renderer memory pressure');
          }
          // OpenAI returns at one of its allowed sizes (1024²,
          // 1024×1536, 1536×1024) which often differs from our
          // canvas. Scale to canvas size with smoothing so the
          // inpaint blends in regardless of source dims.
          const shortPrompt = (prompt || '').trim().replace(/\s+/g, ' ').slice(0, 40);
          const layerName = shortPrompt ? `Inpaint: ${shortPrompt}` : 'Inpaint Result';
          const resultLayer = createLayer(layerName, state.imgWidth, state.imgHeight);
          const aiCrop = makeCanvas(work.crop.w, work.crop.h);
          const aiCtx = aiCrop.getContext('2d');
          aiCtx.imageSmoothingEnabled = true;
          aiCtx.imageSmoothingQuality = 'high';
          aiCtx.drawImage(resultImg, 0, 0, work.crop.w, work.crop.h);
          resultLayer.inpaintSource = {
            ai: aiCrop,
            mask: work.hardMaskCanvas,
            x: work.crop.x,
            y: work.crop.y,
            w: work.crop.w,
            h: work.crop.h,
            padPx,
            autoBlendEdgePx: initialBlend.edgePx,
            autoBlendFeatherPx: initialBlend.featherPx,
          };
          applyInpaintFeather(resultLayer, initialBlend.featherPx, initialBlend.edgePx);
          state.layers.push(resultLayer);
          state.activeLayerId = resultLayer.id;
          state.lastInpaintLayerId = resultLayer.id;
          // Hide every mask sub-layer that contributed to the
          // generation so the red overlay doesn't cover the result —
          // but KEEP the mask pixels intact, and reflect "hidden"
          // on each sub-row's eye icon.
          for (const ly of state.layers) {
            if (!ly.masks || !ly.masks.length) continue;
            for (const mk of ly.masks) mk.visible = false;
          }
          composite();
          renderLayerPanel();
          // Reveal post-generation Feather + Edge Stroke sliders.
          // Cap Edge Stroke at ±padPx so the slider can't ask for
          // more AI buffer than we generated.
          const fRow = document.getElementById('ge-inpaint-postfeather-row');
          const fSlider = document.getElementById('ge-feather-slider');
          const fLabel = document.getElementById('ge-feather-label');
          // Divider + heading are always visible; once Generate
          // succeeds we hide the "Available after Generate" hint.
          const divEl = document.getElementById('ge-inpaint-postedge-divider');
          const titleEl = document.getElementById('ge-inpaint-postedge-title');
          const hintEl = document.getElementById('ge-inpaint-postedge-hint');
          if (divEl) divEl.style.display = '';
          if (titleEl) titleEl.style.display = '';
          if (hintEl) hintEl.style.display = 'none';
          if (fRow) fRow.style.display = '';
          if (fSlider) fSlider.value = String(initialBlend.featherPx);
          if (fLabel) fLabel.textContent = `${initialBlend.featherPx}px`;
          const fPrev = document.getElementById('ge-feather-preview');
          if (fPrev) {
            const inner = Math.max(0, 50 - initialBlend.featherPx * 1.25);
            fPrev.style.background = `radial-gradient(circle, var(--fg) 0%, var(--fg) ${inner}%, transparent 75%)`;
          }
          const eRow = document.getElementById('ge-inpaint-edgestroke-row');
          const eSlider = document.getElementById('ge-edgestroke-slider');
          const eLabel = document.getElementById('ge-edgestroke-label');
          if (eRow) eRow.style.display = '';
          if (eSlider) {
            eSlider.max = String(padPx);
            eSlider.min = String(-padPx);
            eSlider.value = String(initialBlend.edgePx);
          }
          if (eLabel) eLabel.textContent = `+${initialBlend.edgePx}px`;
          const ePrev = document.getElementById('ge-edgestroke-preview');
          if (ePrev) {
            ePrev.style.background = 'rgba(120,200,120,0.5)';
            ePrev.style.opacity = Math.min(1, Math.abs(initialBlend.edgePx) / 80).toFixed(2);
          }
          if (uiModule) {
            uiModule.showToast(
              saveUndo
                ? 'Inpaint complete — auto-expanded around the mask for blending'
                : 'Inpaint complete. Large-image undo was skipped; delete the result layer to revert.',
              5000,
            );
          }
          progress?.done(
            'Inpaint complete',
            saveUndo ? 'Layer added with auto-expanded blend.' : 'Layer added; large-image undo was skipped.',
          );
        } catch (renderErr) {
          console.error('[inpaint] render error', renderErr);
          progress?.fail('Render failed', renderErr.message || String(renderErr));
          if (uiModule) uiModule.showToast('Inpaint render failed: ' + (renderErr.message || renderErr), 6000);
        }
      };
      resultImg.onerror = (e) => {
        console.error('[inpaint] base64 decode failed', e);
        progress?.fail('Decode failed', 'The endpoint returned an unreadable image.');
        if (uiModule) uiModule.showToast('Inpaint result failed to decode', 6000);
      };
      resultImg.src = 'data:image/png;base64,' + data.image;
    } catch (e) {
      releaseRequestCanvases(work);
      progress?.fail('Inpaint failed', e.message || String(e));
      if (uiModule) uiModule.showToast('Inpaint failed: ' + e.message, 6000);
    } finally {
      btn.disabled = false;
      if (btnLabel) btnLabel.textContent = idleLabel;
      if (runWp) { try { runWp.destroy(); } catch (_) {} }
      if (canvasWp) { try { canvasWp.destroy(); } catch (_) {} }
      if (canvasWpEl) { try { canvasWpEl.remove(); } catch (_) {} }
      window.dispatchEvent(new CustomEvent('ge:inpaint-done'));
    }
  }

  // Generate.
  document.getElementById('ge-inpaint-run').addEventListener('click', async () => {
    const prompt = document.getElementById('ge-inpaint-prompt')?.value?.trim();
    if (!prompt) { if (uiModule) uiModule.showToast('Enter a prompt for inpainting'); return; }
    const strength = (parseInt(document.getElementById('ge-strength-slider')?.value || '75')) / 100;
    await runInpaint({
      prompt, strength,
      btnId: 'ge-inpaint-run',
      labelId: 'ge-inpaint-run-label',
      idleLabel: 'Generate', busyLabel: 'Generating',
    });
  });

  // Remove — detects backend type and substitutes a content-aware
  // fill prompt. gpt-image-1 understands "remove …" semantically;
  // SDXL inpaint pipelines literally try to draw the prompt, so we
  // send a generic surroundings-matching prompt and crank strength.
  document.getElementById('ge-inpaint-remove').addEventListener('click', async () => {
    const sel = getSelectedAIEndpoint('inpaint');
    const ep = (sel.endpoint || '').toLowerCase();
    const isOpenAI = ep.includes('api.openai.com');
    let prompt, strength;
    if (isOpenAI) {
      const userP = document.getElementById('ge-inpaint-prompt')?.value?.trim();
      prompt = userP
        ? `Remove ${userP}. Fill seamlessly with the surrounding background, photorealistic, no objects, no people.`
        : 'Remove the masked area. Fill seamlessly with the surrounding background, photorealistic, no objects, no people.';
      strength = (parseInt(document.getElementById('ge-strength-slider')?.value || '75')) / 100;
    } else {
      // SDXL inpaint: describe the surroundings, not what's there.
      // Crank strength to ensure the model fully overwrites the
      // masked region — at low strength it would denoise toward
      // what was there.
      prompt = 'seamless natural background, photorealistic, continuation of surrounding scene, empty area, no objects, no people, no text, clean';
      strength = 0.99;
    }
    await runInpaint({
      prompt, strength,
      btnId: 'ge-inpaint-remove',
      labelId: 'ge-inpaint-remove-label',
      idleLabel: 'Remove', busyLabel: 'Removing',
    });
  });

  // Outpaint — auto-generate a mask covering empty (transparent)
  // areas of the flattened composite, then run inpaint to fill them
  // seamlessly. Mask is dilated ~12px so the AI sees adjacent
  // opaque pixels as context. Ignores the user's drawn mask.
  document.getElementById('ge-inpaint-outpaint').addEventListener('click', async () => {
    // 1) Flatten visible layers to detect alpha=0 (empty) regions.
    const flat = document.createElement('canvas');
    flat.width = state.imgWidth; flat.height = state.imgHeight;
    const fctx = flat.getContext('2d');
    for (const layer of state.layers) {
      if (!layer.visible) continue;
      fctx.globalAlpha = layer.opacity;
      const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
      fctx.drawImage(layer.canvas, off.x, off.y);
    }
    fctx.globalAlpha = 1;
    const flatData = fctx.getImageData(0, 0, state.imgWidth, state.imgHeight).data;
    // 2) White wherever the composite is transparent.
    const maskRaw = document.createElement('canvas');
    maskRaw.width = state.imgWidth; maskRaw.height = state.imgHeight;
    const mrCtx = maskRaw.getContext('2d');
    const mrImg = mrCtx.createImageData(state.imgWidth, state.imgHeight);
    let emptyCount = 0;
    for (let i = 0; i < flatData.length; i += 4) {
      if (flatData[i + 3] === 0) {
        mrImg.data[i] = 255;
        mrImg.data[i + 1] = 255;
        mrImg.data[i + 2] = 255;
        mrImg.data[i + 3] = 255;
        emptyCount++;
      }
    }
    if (emptyCount === 0) {
      if (uiModule) uiModule.showToast('No empty areas to outpaint — canvas is fully covered.');
      return;
    }
    mrCtx.putImageData(mrImg, 0, 0);
    // 3) Dilate the mask outward 12px so it overlaps a band of
    //    opaque pixels — context for the model to blend cleanly.
    const expanded = document.createElement('canvas');
    expanded.width = state.imgWidth; expanded.height = state.imgHeight;
    const ectx = expanded.getContext('2d');
    ectx.filter = 'blur(12px)';
    ectx.drawImage(maskRaw, 0, 0);
    ectx.filter = 'none';
    const expData = ectx.getImageData(0, 0, state.imgWidth, state.imgHeight);
    for (let i = 0; i < expData.data.length; i += 4) {
      const a = expData.data[i + 3];
      const v = a > 6 ? 255 : 0;
      expData.data[i] = v;
      expData.data[i + 1] = v;
      expData.data[i + 2] = v;
      expData.data[i + 3] = v;
    }
    ectx.putImageData(expData, 0, 0);
    // 4) Temporarily replace the active mask sub-layer with the
    //    outpaint mask. Snapshot the previous so we can restore.
    const mask = ensureActiveMaskLayer();
    if (!mask) { if (uiModule) uiModule.showToast('No active layer for outpaint'); return; }
    const savedMask = mask.ctx.getImageData(0, 0, mask.canvas.width, mask.canvas.height);
    mask.ctx.clearRect(0, 0, mask.canvas.width, mask.canvas.height);
    mask.ctx.drawImage(expanded, 0, 0);
    // 5) Prompt: prefer user input, else a generic fill.
    const userP = document.getElementById('ge-inpaint-prompt')?.value?.trim();
    const prompt = userP || 'seamless natural continuation of the surrounding image, photorealistic, matching style, no objects, no people, no text';
    const strength = 0.99;
    try {
      await runInpaint({
        prompt, strength,
        btnId: 'ge-inpaint-outpaint',
        labelId: 'ge-inpaint-outpaint-label',
        idleLabel: 'Outpaint', busyLabel: 'Outpainting',
      });
    } finally {
      // Restore the user's previous mask drawing so subsequent
      // Generate/Remove operates on what they actually drew.
      mask.ctx.clearRect(0, 0, mask.canvas.width, mask.canvas.height);
      mask.ctx.putImageData(savedMask, 0, 0);
      composite();
    }
  });
}
