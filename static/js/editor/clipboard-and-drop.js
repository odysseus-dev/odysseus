/**
 * Paste + drag-and-drop import handlers. Both add an image to the
 * editor as a new layer:
 *
 *   - Paste (Ctrl+V): checks `state.internalClipboard` first (set by
 *     lasso copy/cut), then falls back to the system clipboard's
 *     `image/*` items. Layer is named "Pasted Selection" or "Pasted"
 *     and becomes active; the tool snaps to Move so the user can
 *     reposition it immediately.
 *   - Drop: any `image/*` file dragged from the OS / another tab.
 *     Shows a "Drop image to add as new layer" overlay mid-drag. Each
 *     dropped image is routed through `handleImportedImage` so canvas-
 *     resize prompts + undo history work the same as the toolbar
 *     Import button.
 *
 * Both gated by `state.editorOpen` so they're inert when the editor
 * is closed (other listeners on the page get first dibs).
 *
 * @param {{
 *   container:            HTMLElement,
 *   saveState:            (label?: string) => void,
 *   createLayer:          (name: string, w: number, h: number) => object,
 *   renderLayerPanel:     () => void,
 *   composite:            () => void,
 *   handleImportedImage:  (img: HTMLImageElement, sourceName?: string) => void,
 *   uiModule:             object,
 * }} deps
 */
import { state } from './state.js';
import { createPlacedData, renderPlacedLayer } from './placed-layer.js';

let clipboardBindings;

export function wireClipboardAndDrop({
  container, saveState, createLayer, renderLayerPanel, composite,
  handleImportedImage, uiModule,
}) {
  clipboardBindings?.abort();
  clipboardBindings = new AbortController();
  const { signal } = clipboardBindings;
  // ── Paste ──
  window.addEventListener('paste', (e) => {
    if (!state.editorOpen || state.container !== container) return;
    // Editable fields keep native paste. The Clipboard button focuses the
    // canvas container before asking for a keyboard paste when read() is
    // unavailable on an insecure origin.
    if (e.target?.isContentEditable || e.target?.closest?.('input, textarea, select, [role="dialog"]')) return;

    function pasteAsLayer(imgSource, label, offset = { x: 0, y: 0 }) {
      if (!state.editorOpen) return; // user closed mid-paste
      saveState();
      const layer = createLayer(label || 'Pasted', imgSource.width, imgSource.height);
      // Selection clipboard data is already a complete layer-sized surface.
      // Keep it source-backed with an identity matrix so future transforms do
      // not repeatedly resample the pasted pixels.
      layer.kind = 'placed';
      layer.placed = createPlacedData(imgSource, [1, 0, 0, 1, offset.x, offset.y], label || 'Pasted');
      const rendered = renderPlacedLayer(layer);
      state.layerOffsets.set(layer.id, rendered.offset);
      state.layers.push(layer);
      state.activeLayerId = layer.id;
      state.selectedLayerIds = [layer.id];
      state.activeGroupId = null;
      const tb = state.container?.querySelector('.ge-toolbar');
      tb?.querySelector('[data-tool="move"]')?.click();
      renderLayerPanel();
      composite();
      uiModule.showToast('Pasted as new layer');
    }

    const imageItem = Array.from(e.clipboardData?.items || []).find(item => item.type.startsWith('image/'));
    if (imageItem) {
      e.preventDefault();
      e.stopImmediatePropagation();
      const blob = imageItem.getAsFile();
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const img = new Image();
      // External clipboard images follow the same source-backed import path
      // as files, gallery images, and drops. Internal selection clipboard
      // content is handled above as an identity placed layer.
      img.onload = () => { handleImportedImage(img, 'Pasted image'); URL.revokeObjectURL(url); };
      img.onerror = () => { URL.revokeObjectURL(url); uiModule?.showToast('Failed to load clipboard image'); };
      img.src = url;
      return;
    }
    if (state.internalClipboard && !e.defaultPrevented && !e.clipboardData?.types?.includes?.('text/plain')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      pasteAsLayer(state.internalClipboard, 'Pasted Selection', state.internalClipboardOffset || { x: 0, y: 0 });
    }
  }, { capture: true, signal });

  // ── Drag-and-drop ──
  // Visual drop-zone overlay appears mid-drag; routes via
  // handleImportedImage so the import respects canvas resizing rules
  // + saves history (same path as the toolbar Import button).
  const dropZone = container;
  if (!dropZone) return;
  let dragDepth = 0;
  const hasFileType = (dt) => dt && Array.from(dt.types || []).some(t => t === 'Files');
  const showOverlay = () => {
    if (!state.editorOpen) return;
    let ov = dropZone.querySelector('.ge-drop-overlay');
    if (!ov) {
      ov = document.createElement('div');
      ov.className = 'ge-drop-overlay';
      ov.innerHTML = '<div class="ge-drop-overlay-msg">Drop image to add as new layer</div>';
      dropZone.appendChild(ov);
    }
    ov.style.display = '';
  };
  const hideOverlay = () => {
    const ov = dropZone.querySelector('.ge-drop-overlay');
    if (ov) ov.style.display = 'none';
  };
  dropZone.addEventListener('dragenter', (e) => {
    if (!state.editorOpen || !hasFileType(e.dataTransfer)) return;
    e.preventDefault();
    dragDepth++;
    showOverlay();
  });
  dropZone.addEventListener('dragover', (e) => {
    if (!state.editorOpen || !hasFileType(e.dataTransfer)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  dropZone.addEventListener('dragleave', () => {
    if (!state.editorOpen) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) hideOverlay();
  });
  dropZone.addEventListener('drop', (e) => {
    if (!state.editorOpen) return;
    dragDepth = 0;
    hideOverlay();
    const files = Array.from(e.dataTransfer?.files || []).filter(f => f.type.startsWith('image/'));
    if (!files.length) return;
    e.preventDefault();
    e.stopPropagation();
    for (const f of files) {
      const url = URL.createObjectURL(f);
      const img = new Image();
      img.onload = () => { handleImportedImage(img); URL.revokeObjectURL(url); };
      img.onerror = () => URL.revokeObjectURL(url);
      img.src = url;
    }
  });
}
