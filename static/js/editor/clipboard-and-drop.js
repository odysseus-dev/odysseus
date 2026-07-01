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
 *   handleImportedImage:  (img: HTMLImageElement) => void,
 *   uiModule:             object,
 * }} deps
 */
import { state } from './state.js';

export function wireClipboardAndDrop({
  container, saveState, createLayer, renderLayerPanel, composite,
  handleImportedImage, uiModule,
}) {
  // ── Paste ──
  window.addEventListener('paste', (e) => {
    if (!state.editorOpen) return;

    function pasteAsLayer(imgSource, label) {
      if (!state.editorOpen) return; // user closed mid-paste
      saveState();
      const layer = createLayer(label || window.t('Pasted'), imgSource.width, imgSource.height);
      layer.ctx.drawImage(imgSource, 0, 0);
      state.layers.push(layer);
      state.activeLayerId = layer.id;
      state.tool = 'move';
      const tb = state.container?.querySelector('.ge-toolbar');
      if (tb) tb.querySelectorAll('.ge-tool-btn').forEach(b => b.classList.toggle('active', b.dataset.tool === 'move'));
      renderLayerPanel();
      composite();
      uiModule.showToast(window.t('Pasted as new layer'));
    }

    // Check internal clipboard first (from Ctrl+C lasso/wand).
    if (state.internalClipboard) {
      e.preventDefault();
      e.stopImmediatePropagation();
      pasteAsLayer(state.internalClipboard, window.t('Pasted Selection'));
      return;
    }

    // Fall back to system clipboard.
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (!item.type.startsWith('image/')) continue;
      e.preventDefault();
      e.stopImmediatePropagation();
      const blob = item.getAsFile();
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => { pasteAsLayer(img, window.t('Pasted')); URL.revokeObjectURL(url); };
      img.src = url;
      break;
    }
  }, true);  // capture phase so we beat chat input

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
      ov.innerHTML = `<div class="ge-drop-overlay-msg">${window.t('Drop image to add as new layer')}</div>`;
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
