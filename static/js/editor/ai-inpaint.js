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
 *     - Dilate the mask ~padPx so the model fills a buffer zone the
 *       post-gen Feather/Edge slider can fade into.
 *     - POST flattened canvas + dilated mask to /api/image/inpaint.
 *     - Drop the result as a new layer, snapshot the AI image + hard
 *       mask on the layer for live edge tuning, hide every
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

import { api } from '../axios/api.js';

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
    const pmCtx = preMerged.getContext('2d');
    const maskData = pmCtx.getImageData(0, 0, preMerged.width, preMerged.height).data;
    let hasMask = false;
    for (let i = 3; i < maskData.length; i += 4) { if (maskData[i] > 0) { hasMask = true; break; } }
    if (!hasMask) { if (uiModule) uiModule.showToast('Draw the area you want to inpaint first'); return; }
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
        let cx = state.imgWidth / 2, cy = state.imgHeight / 2;
        try {
          const merged = buildMergedMaskCanvas();
          if (merged) {
            const d = merged.getContext('2d').getImageData(0, 0, merged.width, merged.height).data;
            let minX = merged.width, maxX = 0, minY = merged.height, maxY = 0;
            for (let y = 0; y < merged.height; y += 4) {
              for (let x = 0; x < merged.width; x += 4) {
                if (d[(y * merged.width + x) * 4 + 3] > 0) {
                  if (x < minX) minX = x; if (x > maxX) maxX = x;
                  if (y < minY) minY = y; if (y > maxY) maxY = y;
                }
              }
            }
            if (maxX >= minX) { cx = (minX + maxX) / 2; cy = (minY + maxY) / 2; }
          }
        } catch {}
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
    try {
      // Flatten current image.
      const flatCanvas = document.createElement('canvas');
      flatCanvas.width = state.imgWidth; flatCanvas.height = state.imgHeight;
      const flatCtx = flatCanvas.getContext('2d');
      for (const layer of state.layers) {
        if (!layer.visible) continue;
        flatCtx.globalAlpha = layer.opacity;
        const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
        flatCtx.drawImage(layer.canvas, off.x, off.y);
      }
      flatCtx.globalAlpha = 1;
      // Dilate the user's brush mask before sending to the model.
      // The AI fills a small buffer zone around the brush, so the
      // post-gen Edge feather slider has AI content to fade INTO
      // instead of fading straight to the original. The ORIGINAL
      // (un-dilated) mask is cached on the layer — the feather blur
      // expands outward from that boundary into the dilated AI region.
      const padPx = Math.min(80, Math.max(20, Math.round(Math.min(state.imgWidth, state.imgHeight) * 0.04)));
      // Merge every visible mask sub-layer (across all parent
      // layers) into a single union mask before sending to the AI.
      // This way, if the user built up the inpaint region across
      // multiple masks, the final generation sees the combined
      // region instead of just the currently-active mask.
      const mergedMask = buildMergedMaskCanvas() || state.maskCanvas;
      const dilatedMask = dilateMask(mergedMask, padPx);
      const imageB64 = flatCanvas.toDataURL('image/png').split(',')[1];
      const maskB64 = dilatedMask.toDataURL('image/png').split(',')[1];
      const { data } = await api.post('/api/image/inpaint', (() => {
          const sel = getSelectedAIEndpoint('inpaint');
          return { image: imageB64, mask: maskB64, prompt, width: state.imgWidth, height: state.imgHeight, strength, feather: 0, _endpoint: sel.endpoint, _model: sel.model };
        })());
      if (data.error) throw new Error(data.error);
      if (!data.image) throw new Error('No image returned from inpaint endpoint');
      // Load result as a new layer and clip with the user-drawn mask
      // so only the inpainted region is visible. Cache the
      // unfeathered (AI image + hard mask) on the layer so the live
      // Feather slider can re-derive the alpha on each input event
      // without re-running the model.
      const resultImg = new Image();
      resultImg.onload = () => {
        if (!state.editorOpen) return; // user closed mid-decode
        try {
          saveState('Inpaint result');
          // OpenAI returns at one of its allowed sizes (1024²,
          // 1024×1536, 1536×1024) which often differs from our
          // canvas. Scale to canvas size with smoothing so the
          // inpaint blends in regardless of source dims.
          const shortPrompt = (prompt || '').trim().replace(/\s+/g, ' ').slice(0, 40);
          const layerName = shortPrompt ? `Inpaint: ${shortPrompt}` : 'Inpaint Result';
          const resultLayer = createLayer(layerName, state.imgWidth, state.imgHeight);
          resultLayer.ctx.imageSmoothingEnabled = true;
          resultLayer.ctx.imageSmoothingQuality = 'high';
          resultLayer.ctx.drawImage(resultImg, 0, 0, state.imgWidth, state.imgHeight);
          // Snapshot the AI result + hard mask used for this run.
          const aiSnap = document.createElement('canvas');
          aiSnap.width = state.imgWidth; aiSnap.height = state.imgHeight;
          aiSnap.getContext('2d').drawImage(resultLayer.canvas, 0, 0);
          const maskSnap = document.createElement('canvas');
          maskSnap.width = state.maskCanvas.width;
          maskSnap.height = state.maskCanvas.height;
          maskSnap.getContext('2d').drawImage(state.maskCanvas, 0, 0);
          resultLayer.inpaintSource = { ai: aiSnap, mask: maskSnap, padPx };
          // Apply initial alpha = hard mask (no feather, no edge shift).
          applyInpaintFeather(resultLayer, 0, 0);
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
          if (fSlider) fSlider.value = '0';
          if (fLabel) fLabel.textContent = '0px';
          const eRow = document.getElementById('ge-inpaint-edgestroke-row');
          const eSlider = document.getElementById('ge-edgestroke-slider');
          const eLabel = document.getElementById('ge-edgestroke-label');
          if (eRow) eRow.style.display = '';
          if (eSlider) {
            eSlider.max = String(padPx);
            eSlider.min = String(-padPx);
            eSlider.value = '0';
          }
          if (eLabel) eLabel.textContent = '0px';
          if (uiModule) uiModule.showToast('Inpaint complete — drag Edge feather / Edge stroke to blend', 5000);
        } catch (renderErr) {
          console.error('[inpaint] render error', renderErr);
          if (uiModule) uiModule.showToast('Inpaint render failed: ' + (renderErr.message || renderErr), 6000);
        }
      };
      resultImg.onerror = (e) => {
        console.error('[inpaint] base64 decode failed', e);
        if (uiModule) uiModule.showToast('Inpaint result failed to decode', 6000);
      };
      resultImg.src = 'data:image/png;base64,' + data.image;
    } catch (e) {
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
