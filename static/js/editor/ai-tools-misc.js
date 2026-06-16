/**
 * Misc AI-tool wiring — the three AI tools that don't share the
 * inpaint pipeline:
 *
 *   Harmonize: Reinhard color transfer on a body mask (no AI redraw)
 *              + an optional narrow inpaint on a seam mask if the
 *              "Seam fix" slider > 0.
 *   Canvas 2×/4× Upscale: in-browser bicubic resampling, no server.
 *   AI Upscale: Real-ESRGAN via /api/image/upscale-local.
 *   Style Transfer: img2img via /api/gallery/style-transfer.
 *
 * Plus the small `_addEmptyLayer` helper and its toolbar wiring,
 * since it lived next to these.
 *
 * @param {{
 *   buildLayerBodyMask:  (featherPx: number) => string | null,
 *   buildSeamMask:       (featherPx: number) => string | null,
 *   applyImageTool:      (endpoint, payload, layerName, btn, opts?) => Promise<void>,
 *   flatten:             () => HTMLCanvasElement,
 *   saveState:           (label?: string) => void,
 *   fitZoom:             () => void,
 *   composite:           () => void,
 *   createLayer:         (name, w, h) => object,
 *   renderLayerPanel:    () => void,
 *   spinnerModule:       object,
 *   uiModule:            object,
 * }} deps
 *
 * @returns {{ addEmptyLayer: () => void }}
 */
import { state } from './state.js';
import { api, apiErrorMessage } from '../axios/api.js';


export function wireAIToolsMisc({
 buildLayerBodyMask, buildSeamMask, applyImageTool,
 flatten, saveState, fitZoom, composite, createLayer, renderLayerPanel,
 spinnerModule, uiModule,
}) {
 // ── Harmonize sliders — Color match + Seam fix ──
 const harmColorPrev = document.getElementById('ge-harmonize-color-preview');
 const harmSeamPrev = document.getElementById('ge-harmonize-seam-preview');
 document.getElementById('ge-harmonize-color')?.addEventListener('input', (e) => {
   document.getElementById('ge-harmonize-color-label').textContent = (e.target.value / 100).toFixed(2);
   if (harmColorPrev) harmColorPrev.style.opacity = (parseInt(e.target.value, 10) / 100).toFixed(2);
 });
 document.getElementById('ge-harmonize-seam')?.addEventListener('input', (e) => {
   document.getElementById('ge-harmonize-seam-label').textContent = (e.target.value / 100).toFixed(2);
   if (harmSeamPrev) harmSeamPrev.style.opacity = (parseInt(e.target.value, 10) / 100).toFixed(2);
 });

 // Harmonize button — two-stage:
 //   1) Reinhard color transfer on body mask (no AI redraw)
 //   2) Optional narrow inpaint on seam mask (if seam_fix > 0)
 document.getElementById('ge-harmonize-run')?.addEventListener('click', () => {
   const prompt = document.getElementById('ge-harmonize-prompt')?.value?.trim() || 'photorealistic, natural lighting, seamless blend';
   const color_match = (parseInt(document.getElementById('ge-harmonize-color')?.value || '65')) / 100;
   const seam_fix = (parseInt(document.getElementById('ge-harmonize-seam')?.value || '0')) / 100;
   const bodyFeather = Math.max(6, Math.round(Math.min(state.imgWidth, state.imgHeight) * 0.012));
   const seamFeather = Math.max(8, Math.round(Math.min(state.imgWidth, state.imgHeight) * 0.015));
   const body_mask = buildLayerBodyMask(bodyFeather);
   const seam_mask = seam_fix > 0.01 ? buildSeamMask(seamFeather) : null;
   // Harmonize needs a non-base layer to color-match against the
   // background. Without one, the server would fall back to legacy
   // whole-image img2img — i.e. regenerate the whole photo. Block
   // that and tell the user what's missing.
   if (!body_mask) {
     if (uiModule) uiModule.showToast('Harmonize needs a second layer pasted/imported over the base photo — nothing to color-match against.', 6000);
     return;
   }
   const payload = { prompt, color_match, seam_fix, body_mask };
   if (seam_mask) payload.seam_mask = seam_mask;
   applyImageTool('/api/image/harmonize', payload, 'Harmonized', document.getElementById('ge-harmonize-run'));
 });

 // ── Canvas upscale (bicubic) ──
 function canvasUpscale(factor) {
   saveState(`Upscale ${factor}×`);
   const newW = state.imgWidth * factor;
   const newH = state.imgHeight * factor;
   state.layers.forEach(l => {
     const tmp = document.createElement('canvas');
     tmp.width = newW; tmp.height = newH;
     const tCtx = tmp.getContext('2d');
     tCtx.imageSmoothingEnabled = true;
     tCtx.imageSmoothingQuality = 'high';
     tCtx.drawImage(l.canvas, 0, 0, newW, newH);
     l.canvas.width = newW; l.canvas.height = newH;
     l.ctx.drawImage(tmp, 0, 0);
   });
   if (state.maskCanvas) { state.maskCanvas.width = newW; state.maskCanvas.height = newH; }
   state.imgWidth = newW; state.imgHeight = newH;
   state.mainCanvas.width = newW; state.mainCanvas.height = newH;
   const sizeLabel = document.getElementById('ge-canvas-size');
   if (sizeLabel) sizeLabel.textContent = `${newW}×${newH}`;
   fitZoom();
   composite();
   uiModule.showToast(`Upscaled ${factor}× to ${newW}×${newH}`);
 }
 document.getElementById('ge-upscale-2x')?.addEventListener('click', () => canvasUpscale(2));
 document.getElementById('ge-upscale-4x')?.addEventListener('click', () => canvasUpscale(4));

 // ── AI upscale (Real-ESRGAN, no diffusion server required) ──
 document.getElementById('ge-upscale-ai')?.addEventListener('click', async () => {
   const btn = document.getElementById('ge-upscale-ai');
   const origHTML = btn.innerHTML;
   btn.disabled = true;
   let upWp = null;
   try {
     upWp = spinnerModule.createWhirlpool(14);
     upWp.element.style.cssText = 'display:inline-block;vertical-align:middle;position:relative;top:1px;margin-right:6px;width:14px;height:14px;';
     btn.innerHTML = '';
     btn.appendChild(upWp.element);
     const lbl = document.createElement('span');
     lbl.textContent = 'Upscaling…';
     btn.appendChild(lbl);
   } catch (_) { btn.textContent = 'Upscaling…'; }
   try {
     const flat = flatten();
     const imageB64 = flat.toDataURL('image/png').split(',')[1];
     const { data } = await api.post('/api/image/upscale-local', { image: imageB64, scale: 2 });
     if (data.image) {
       const img = new Image();
       img.onload = () => {
         if (!state.editorOpen) return;
         saveState();
         const newW = img.width, newH = img.height;
         const layer = createLayer('AI Upscaled', newW, newH);
         layer.ctx.drawImage(img, 0, 0);
         state.layers.push(layer);
         state.activeLayerId = layer.id;
         state.imgWidth = newW; state.imgHeight = newH;
         state.mainCanvas.width = newW; state.mainCanvas.height = newH;
         if (state.maskCanvas) { state.maskCanvas.width = newW; state.maskCanvas.height = newH; }
         const sizeLabel = document.getElementById('ge-canvas-size');
         if (sizeLabel) sizeLabel.textContent = `${newW}×${newH}`;
         fitZoom();
         composite();
         renderLayerPanel();
         uiModule.showToast(`AI upscaled to ${newW}×${newH}`);
       };
       img.src = 'data:image/png;base64,' + data.image;
     } else {
       throw new Error(data.error || 'No image returned');
     }
   } catch (e) {
     uiModule.showToast('AI upscale failed: ' + apiErrorMessage(e));
   }
   try { upWp?.destroy(); } catch (_) {}
   btn.disabled = false;
   btn.innerHTML = origHTML;
 });

 // ── Style transfer ──
 document.getElementById('ge-style-strength')?.addEventListener('input', (e) => {
   document.getElementById('ge-style-strength-label').textContent = (parseInt(e.target.value) / 100).toFixed(2);
 });
 document.getElementById('ge-style-run')?.addEventListener('click', async () => {
   const btn = document.getElementById('ge-style-run');
   const prompt = document.getElementById('ge-style-prompt').value.trim();
   if (!prompt) { uiModule.showToast('Enter a style prompt'); return; }
   const strength = parseInt(document.getElementById('ge-style-strength').value) / 100;
   btn.disabled = true; btn.textContent = 'Applying...';
   try {
     const flat = flatten();
     const blob = await new Promise(r => flat.toBlob(r, 'image/png'));
     const fd = new FormData();
     fd.append('image', blob, 'style.png');
     fd.append('prompt', prompt);
     fd.append('strength', String(strength));
     const { data } = await api.post('/api/gallery/style-transfer', fd);
     if (data.image) {
       const img = new Image();
       img.onload = () => {
         if (!state.editorOpen) return;
         saveState();
         const layer = createLayer('Styled: ' + prompt.substring(0, 20), state.imgWidth, state.imgHeight);
         layer.ctx.drawImage(img, 0, 0, state.imgWidth, state.imgHeight);
         state.layers.push(layer);
         state.activeLayerId = layer.id;
         composite();
         renderLayerPanel();
         uiModule.showToast('Style applied');
       };
       img.src = 'data:image/png;base64,' + data.image;
     } else {
       throw new Error(data.error || 'No image returned');
     }
   } catch (e) {
     uiModule.showToast('Style transfer failed: ' + apiErrorMessage(e));
   }
   btn.disabled = false; btn.textContent = 'Apply Style';
 });

 // ── Add empty layer (used by the layer-panel header button + the
 // Ctrl+Alt+J keyboard shortcut). Returned so keyboard-shortcuts.js
 // can call it through the same path. ──
 function addEmptyLayer() {
   saveState('Add layer');
   const layer = createLayer('Layer ' + state.layers.length, state.imgWidth, state.imgHeight);
   state.layers.push(layer);
   state.activeLayerId = layer.id;
   renderLayerPanel();
   composite();
 }
 document.getElementById('ge-add-layer')?.addEventListener('click', addEmptyLayer);

 return { addEmptyLayer };
}
