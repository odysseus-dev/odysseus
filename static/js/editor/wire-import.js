/**
 * Image-import wiring — covers all four entry points that drop an
 * image as a new layer:
 *
 *   #ge-import-topbar    topbar "+ Import" button
 *   #ge-import-file      File button in the Import section
 *   #ge-import-paste     Clipboard button (uses async clipboard API)
 *   #ge-import-gallery   Gallery picker — fetches /api/gallery/library
 *                        and shows a thumbnail grid overlay
 *
 * Plus the shared `handleImportedImage(img)` sink — scales to canvas,
 * centres, creates a new layer, switches to Move tool, hides the
 * import section, refreshes the panel. Returned so the drag-and-drop
 * + paste paths (wired in editor/clipboard-and-drop.js) can use the
 * same sink.
 *
 * @param {{
 *   container:        HTMLElement,
 *   saveState:        (label?: string) => void,
 *   createLayer:      (name, w, h) => object,
 *   composite:        () => void,
 *   renderLayerPanel: () => void,
 *   uiModule:         object,
 * }} deps
 *
 * @returns {{ handleImportedImage: (img: HTMLImageElement) => void }}
 */
import { state } from './state.js';
import {
  createPlacedData,
  initialPlacedMatrix,
  renderPlacedLayer,
} from './placed-layer.js';
import { parsePsd, decodeLayeredPsd } from './psd.js';

const PSD_TO_EDITOR_BLEND = {
  norm: 'source-over', mul: 'multiply', scrn: 'screen', over: 'overlay',
  dark: 'darken', lite: 'lighten', div: 'color-dodge', idiv: 'color-burn',
  hLit: 'hard-light', sLit: 'soft-light', diff: 'difference', smud: 'exclusion',
  hue: 'hue', sat: 'saturation', colr: 'color', lum: 'luminosity',
};

function editorBlendForPsd(key) {
  return PSD_TO_EDITOR_BLEND[(key || '').trim()] || null;
}

export function wireImport({ container, saveState, createLayer, composite, renderLayerPanel, uiModule }) {
  // Hidden <input type="file"> the topbar + File buttons both click.
  const importFileInput = document.createElement('input');
  importFileInput.type = 'file';
  importFileInput.accept = 'image/*,.psd,.psb';
  importFileInput.style.display = 'none';
  container.appendChild(importFileInput);

  function handleImportedImage(img, sourceName = 'Imported image') {
    if (!state.editorOpen) return;
    saveState('Import image');
    // Scale down if larger than canvas.
    let w = img.naturalWidth || img.width;
    let h = img.naturalHeight || img.height;
    if (w > state.imgWidth || h > state.imgHeight) {
      const scale = Math.min(state.imgWidth / w, state.imgHeight / h);
      w = Math.round(w * scale);
      h = Math.round(h * scale);
    }
    const source = document.createElement('canvas');
    source.width = img.naturalWidth || img.width;
    source.height = img.naturalHeight || img.height;
    source.getContext('2d').drawImage(img, 0, 0);
    const layer = createLayer('Imported', w, h);
    // Centre on the canvas.
    const ox = Math.round((state.imgWidth - w) / 2);
    const oy = Math.round((state.imgHeight - h) / 2);
    layer.kind = 'placed';
    layer.placed = createPlacedData(
      source,
      initialPlacedMatrix(source.width, source.height, { x: ox, y: oy, width: w, height: h }),
      sourceName,
    );
    const rendered = renderPlacedLayer(layer);
    state.layerOffsets.set(layer.id, rendered.offset);
    state.layers.push(layer);
    state.activeLayerId = layer.id;
    // Switch to move tool so the imported layer is immediately
    // repositionable.
    state.tool = 'move';
    const tb = container.querySelector('.ge-toolbar');
    if (tb) tb.querySelectorAll('.ge-tool-btn').forEach(b => b.classList.toggle('active', b.dataset.tool === 'move'));
    // Hide the import section now that the import is done.
    const importSec = document.getElementById('ge-import-section');
    if (importSec) importSec.style.display = 'none';
    composite();
    renderLayerPanel();
    if (uiModule) uiModule.showToast('Image imported — drag to position');
  }

  const PSD_BLEND = {
    norm: 'source-over', 'mul ': 'multiply', scrn: 'screen', over: 'overlay',
    dark: 'darken', lite: 'lighten', 'div ': 'color-dodge', idiv: 'color-burn',
    hLit: 'hard-light', sLit: 'soft-light', diff: 'difference', smud: 'exclusion',
    'hue ': 'hue', 'sat ': 'saturation', colr: 'color', 'lum ': 'luminosity',
  };

  function handleImportedLayers(decoded, sourceName = 'Imported PSD') {
    if (!state.editorOpen) return;
    saveState('Import PSD layers');
    const created = [];
    for (const layer of decoded.layers) {
      if (!layer.width || !layer.height) continue;
      const canvas = document.createElement('canvas');
      canvas.width = layer.width;
      canvas.height = layer.height;
      const ctx = canvas.getContext('2d');
      const image = ctx.createImageData(layer.width, layer.height);
      image.data.set(layer.data);
      ctx.putImageData(image, 0, 0);
      const createdLayer = createLayer(layer.name || 'Layer', layer.width, layer.height);
      createdLayer.canvas.width = layer.width;
      createdLayer.canvas.height = layer.height;
      createdLayer.ctx = createdLayer.canvas.getContext('2d');
      createdLayer.ctx.clearRect(0, 0, layer.width, layer.height);
      createdLayer.ctx.drawImage(canvas, 0, 0);
      createdLayer.kind = 'raster';
      if (Number.isFinite(layer.opacity)) createdLayer.opacity = layer.opacity;
      if (layer.visible === false) createdLayer.visible = false;
      if (PSD_BLEND[layer.blendKey]) createdLayer.blendMode = PSD_BLEND[layer.blendKey];
      state.layerOffsets.set(createdLayer.id, { x: layer.x || 0, y: layer.y || 0 });
      state.layers.push(createdLayer);
      created.push(createdLayer);
    }
    if (created.length) state.activeLayerId = created[created.length - 1].id;
    const importSec = document.getElementById('ge-import-section');
    if (importSec) importSec.style.display = 'none';
    composite();
    renderLayerPanel();
    if (uiModule) uiModule.showToast(`Imported ${created.length} PSD layer${created.length === 1 ? '' : 's'} from ${sourceName}`);
  }

  importFileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const isPsd = /\.(psd|psb)$/i.test(file.name || '');
    const reader = new FileReader();
    reader.onload = (ev) => {
      if (isPsd) {
        try {
          const decoded = decodeLayeredPsd(ev.target.result);
          if (decoded.layers && decoded.layers.length) handleImportedLayers(decoded);
          else handleImportedImage(parsePsd(ev.target.result).canvas, file.name || 'Imported PSD');
        } catch (err) {
          if (uiModule) uiModule.showToast('Failed to import PSD: ' + err.message);
        }
        return;
      }
      const img = new Image();
      img.onload = () => handleImportedImage(img, file.name || 'Imported image');
      img.src = ev.target.result;
    };
    if (isPsd) reader.readAsArrayBuffer(file);
    else reader.readAsDataURL(file);
    importFileInput.value = '';
  });

  document.getElementById('ge-import-topbar')?.addEventListener('click', () => importFileInput.click());
  document.getElementById('ge-import-file')?.addEventListener('click', () => importFileInput.click());

  document.getElementById('ge-import-paste')?.addEventListener('click', async () => {
    const pasteFromKeyboard = () => {
      document.getElementById('gallery-editor-container')?.focus();
      uiModule?.showToast('Press Ctrl+V to paste the image');
    };
    if (!navigator.clipboard?.read) {
      pasteFromKeyboard();
      return;
    }
    try {
      const clipItems = await navigator.clipboard.read();
      let blob = null;
      for (const item of clipItems) {
        const imgType = item.types.find(t => t.startsWith('image/'));
        if (imgType) { blob = await item.getType(imgType); break; }
      }
      if (!blob) { if (uiModule) uiModule.showToast('No image found in clipboard'); return; }
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => { handleImportedImage(img); URL.revokeObjectURL(url); };
      img.onerror = () => { URL.revokeObjectURL(url); if (uiModule) uiModule.showToast('Failed to load clipboard image'); };
      img.src = url;
    } catch (e) {
      pasteFromKeyboard();
    }
  });

  // Import from Gallery — fetch /api/gallery/library and show a
  // thumbnail-grid picker overlay.
  document.getElementById('ge-import-gallery')?.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/gallery/library?limit=50', { credentials: 'same-origin' });
      const data = await res.json();
      const items = data.items || [];
      if (!items.length) { if (uiModule) uiModule.showToast('No images in gallery'); return; }

      // Picker overlay.
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;';
      const panel = document.createElement('div');
      panel.style.cssText = 'background:var(--panel,#1e1e1e);border-radius:12px;padding:16px;max-width:500px;max-height:70vh;overflow-y:auto;width:90%;';
      panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;"><span style="font-size:13px;font-weight:600;">Pick from Gallery</span><button id="ge-gallery-close" style="background:none;border:none;color:var(--fg);cursor:pointer;font-size:18px;">✕</button></div>';
      const grid = document.createElement('div');
      grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:6px;';
      for (const item of items) {
        const thumb = document.createElement('img');
        thumb.src = item.url;
        thumb.style.cssText = 'width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;cursor:pointer;border:2px solid transparent;transition:border-color 0.15s;';
        thumb.addEventListener('mouseenter', () => { thumb.style.borderColor = 'var(--accent,#61afef)'; });
        thumb.addEventListener('mouseleave', () => { thumb.style.borderColor = 'transparent'; });
        thumb.addEventListener('click', () => {
          overlay.remove();
          const img = new Image();
          img.crossOrigin = 'anonymous';
          img.onload = () => handleImportedImage(img, item.name || item.filename || 'Gallery image');
          img.onerror = () => { if (uiModule) uiModule.showToast('Failed to load gallery image'); };
          img.src = item.url;
        });
        grid.appendChild(thumb);
      }
      panel.appendChild(grid);
      overlay.appendChild(panel);
      document.body.appendChild(overlay);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
      panel.querySelector('#ge-gallery-close').addEventListener('click', () => overlay.remove());
    } catch (e) {
      if (uiModule) uiModule.showToast('Failed to load gallery: ' + e.message);
    }
  });

  return { handleImportedImage };
}
