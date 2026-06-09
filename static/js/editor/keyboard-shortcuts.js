/**
 * Editor keyboard shortcuts — bound to `document` so shortcuts work
 * without first clicking into the canvas. Gated by `state.editorOpen`
 * so they don't leak into chat input when the editor is closed.
 *
 * Covers:
 *   ?              toggle the shortcuts cheatsheet
 *   Enter          confirm in-progress transform
 *   Esc            cancel transform / lasso / crop (in priority order)
 *   Ctrl+Z         undo (Shift adds redo)
 *   Ctrl+Shift+D   deselect (clears wand + lasso)
 *   Ctrl+S         save (Shift = save as / export to gallery)
 *   Ctrl+Shift+T   open resize popup
 *   Ctrl+Alt+T     start free transform
 *   Ctrl+Alt+I     invert wand / lasso selection
 *   Ctrl+Alt+J     new empty layer
 *   Ctrl+Alt+A     select all canvas (lasso polygon = full bounds)
 *   Ctrl+C/X       copy / cut wand or lasso selection (image clipboard
 *                  + internal clipboard)
 *   Ctrl+V         (handled by the paste event listener)
 *   Tool keys (V, B, E, L, …) → toolbar click
 *   [ / ]          shrink / grow brush size proportionally
 *   D, C, M (when lasso has 3+ points) → delete / copy / convert mask
 *   Delete / Backspace (wand or lasso) → delete pixels
 *
 * @param {{
 *   toolbar:                HTMLDivElement,
 *   toolKeyMap:             Record<string, string>,
 *   composite:              () => void,
 *   saveState:              (label?: string) => void,
 *   undo:                   () => void,
 *   redo:                   () => void,
 *   toggleShortcuts:        (show?: boolean) => void,
 *   confirmTransform:       () => void,
 *   cancelTransform:        () => void,
 *   startTransform:         () => void,
 *   resizeCustomPrompt:     () => void,
 *   addEmptyLayer:          () => void,
 *   brushSizeSync:          (source: HTMLInputElement | null) => void,
 *   invertSelection:        () => boolean,
 *   wandDeleteSelection:    () => void,
 *   wandCopyToNewLayer:     () => void,
 *   lassoDeleteSelection:   () => void,
 *   lassoCopyToLayer:       () => void,
 *   lassoToMask:            () => void,
 *   buildLassoMask:         (w: number, h: number, offX: number, offY: number, feather: number, grow: number) => HTMLCanvasElement,
 *   drawLassoOverlay:       () => void,
 *   activeLayer:            () => object | null,
 *   uiModule:               object,
 * }} deps
 */
import { state } from './state.js';
import { isAltGrEvent } from '../platform.js';

export function wireKeyboardShortcuts(deps) {
  const {
    toolbar, toolKeyMap,
    composite, saveState, undo, redo,
    toggleShortcuts, confirmTransform, cancelTransform, startTransform,
    resizeCustomPrompt, addEmptyLayer, brushSizeSync,
    invertSelection,
    wandDeleteSelection, wandCopyToNewLayer,
    lassoDeleteSelection, lassoCopyToLayer, lassoToMask,
    buildLassoMask, drawLassoOverlay,
    activeLayer, uiModule,
  } = deps;

  document.addEventListener('keydown', (e) => {
    if (!state.editorOpen) return;
    // `?` toggles the cheatsheet. Don't fire while typing in a text
    // field — the user might be typing a prompt with a `?`.
    if (e.key === '?' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
      e.preventDefault();
      toggleShortcuts();
      return;
    }
    if (e.key === 'Enter' && state.transformActive) {
      e.preventDefault();
      confirmTransform();
      return;
    }
    if (e.key === 'Escape') return;
    // Skip the Ctrl+Alt editor chords for an AltGr keystroke (see platform.js);
    // only the chord block is skipped, so the layout-character handlers below
    // still act — AltGr+5 / AltGr+8 stay as the [ ] brush-size shortcut on
    // AZERTY / QWERTZ.
    if ((e.ctrlKey || e.metaKey) && !isAltGrEvent(e)) {
      if (e.key === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
      // Ctrl+Shift+D = Deselect: clears the wand selection (and
      // lasso if active) without affecting layers.
      if (e.shiftKey && (e.key === 'D' || e.key === 'd')) {
        if (state.wandMask || state.lassoPoints.length) {
          e.preventDefault();
          if (state.wandMask) {
            saveState();
            state.wandMask = null;
            state.wandLayerId = null;
            state.wandLastSeed = null;
          }
          if (state.lassoPoints.length) {
            state.lassoPoints = [];
            state.lassoActive = false;
          }
          composite();
        }
      }
      // Save shortcuts — match the hints shown in the Save dropdown.
      if ((e.key === 's' || e.key === 'S') && !e.altKey) {
        e.preventDefault();
        document.getElementById(e.shiftKey ? 'ge-export-gallery' : 'ge-save')?.click();
      }
      if (e.shiftKey && e.key === 'T') { e.preventDefault(); resizeCustomPrompt(); }
      if (e.altKey && e.key === 't') { e.preventDefault(); startTransform(); }
      // Ctrl+Alt+I — invert current selection. Uses e.code so
      // Alt-modified key values (e.g. `ˆ` on Mac with Option+I)
      // don't break the match.
      if (e.altKey && e.code === 'KeyI') {
        if (invertSelection()) {
          e.preventDefault();
          e.stopPropagation();
        }
      }
      // Ctrl+Alt+J — new empty layer.
      if (e.altKey && e.code === 'KeyJ') {
        e.preventDefault();
        e.stopPropagation();
        addEmptyLayer();
      }
      // Wand selection: Delete = erase pixels. Ctrl+X = cut to
      // clipboard + new layer + erase. Ctrl+C = copy.
      // (Legacy `&& !_wandActive` clause referenced an undeclared
      // variable — removed; the wand is selection-only and has no
      // "active drag" state.)
      if (state.wandMask) {
        if (e.key === 'Delete' || e.key === 'Backspace') {
          e.preventDefault();
          wandDeleteSelection();
          return;
        }
        if ((e.ctrlKey || e.metaKey) && (e.key === 'x' || e.key === 'c')) {
          e.preventDefault();
          const isCut = e.key === 'x';
          const src = state.layers.find(l => l.id === state.wandLayerId);
          if (!src) return;
          // Clip source by wand mask into a temp canvas.
          const w = src.canvas.width, h = src.canvas.height;
          const tmp = document.createElement('canvas');
          tmp.width = w; tmp.height = h;
          const tCtx = tmp.getContext('2d');
          tCtx.drawImage(src.canvas, 0, 0);
          tCtx.globalCompositeOperation = 'destination-in';
          tCtx.drawImage(state.wandMask, 0, 0);
          state.internalClipboard = tmp;
          tmp.toBlob(blob => {
            if (blob && navigator.clipboard?.write) {
              navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]).then(() => {
                uiModule.showToast(isCut ? 'Cut to clipboard' : 'Copied to clipboard');
              }).catch(() => uiModule.showToast(isCut ? 'Cut (editor only)' : 'Copied (editor only)'));
            }
          }, 'image/png');
          if (isCut) {
            // Cut also moves the selection to a new layer + erases source.
            wandCopyToNewLayer();
            wandDeleteSelection();
          }
          return;
        }
      }
      if ((e.key === 'x' || e.key === 'c') && state.lassoPoints.length >= 3) {
        e.preventDefault();
        const layer = activeLayer();
        if (!layer) return;
        const off = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
        const feather = parseInt(document.getElementById('ge-lasso-feather')?.value || '0');
        const grow = parseInt(document.getElementById('ge-lasso-grow')?.value || '0');
        const w = layer.canvas.width, h = layer.canvas.height;
        const mask = buildLassoMask(w, h, off.x, off.y, feather, grow);
        const srcData = layer.ctx.getImageData(0, 0, w, h);
        const maskData = mask.getContext('2d').getImageData(0, 0, w, h);
        // Build clipped image.
        const tmp = document.createElement('canvas');
        tmp.width = w; tmp.height = h;
        const tCtx = tmp.getContext('2d');
        const outData = tCtx.createImageData(w, h);
        for (let i = 0; i < w * h; i++) {
          const mv = maskData.data[i * 4] / 255;
          if (mv > 0) {
            outData.data[i*4] = srcData.data[i*4];
            outData.data[i*4+1] = srcData.data[i*4+1];
            outData.data[i*4+2] = srcData.data[i*4+2];
            outData.data[i*4+3] = Math.round(srcData.data[i*4+3] * mv);
          }
        }
        tCtx.putImageData(outData, 0, 0);
        state.internalClipboard = tmp;
        const isCut = e.key === 'x';
        tmp.toBlob(blob => {
          if (blob && navigator.clipboard?.write) {
            navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]).then(() => {
              uiModule.showToast(isCut ? 'Cut to clipboard' : 'Copied to clipboard');
            }).catch(() => uiModule.showToast(isCut ? 'Cut (editor only)' : 'Copied (editor only)'));
          }
        }, 'image/png');
        if (e.key === 'x') {
          const savedPts = [...state.lassoPoints];
          state.lassoPoints = savedPts;
          lassoDeleteSelection();
        } else {
          state.lassoPoints = [];
          composite();
        }
      }
      // Ctrl+C with no active selection → copy the entire active layer
      // to the system clipboard as a PNG. Gives a "just copy this image"
      // shortcut without having to lasso-select-all first. The
      // selection-aware Ctrl+C paths above run first (wand + lasso),
      // so this only fires when neither is active.
      if (e.key === 'c' && !e.shiftKey && !state.wandMask && state.lassoPoints.length < 3) {
        const layer = activeLayer();
        if (layer && layer.canvas && layer.canvas.width > 0) {
          e.preventDefault();
          layer.canvas.toBlob(blob => {
            if (blob && navigator.clipboard?.write) {
              navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
                .then(() => uiModule.showToast('Layer copied to clipboard'))
                .catch(() => uiModule.showToast('Copy failed (clipboard permission denied?)'));
            }
          }, 'image/png');
          return;
        }
      }
      // Ctrl+Alt+A = select all canvas.
      if (e.altKey && e.key === 'a' && state.imgWidth > 0 && state.imgHeight > 0) {
        e.preventDefault();
        state.lassoPoints = [
          { x: 0, y: 0 }, { x: state.imgWidth, y: 0 },
          { x: state.imgWidth, y: state.imgHeight }, { x: 0, y: state.imgHeight },
        ];
        state.lassoActive = false;
        composite();
        drawLassoOverlay();
        uiModule.showToast('All selected — Ctrl+C to copy, Del to delete');
      }
      // Ctrl+V handled by the paste event listener.
      if (e.key === 'v') { /* no-op here */ }
      return;
    }
    // Tool shortcuts (only when not typing in an input).
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const toolId = toolKeyMap[e.key.toLowerCase()];
    if (toolId) {
      const toolBtn = toolbar.querySelector(`[data-tool="${toolId}"]`);
      if (toolBtn) toolBtn.click();
    }
    // Bracket keys for brush size — ±10% multiplier mirrors the
    // exponential slider curve so each press feels the same at any
    // size.
    if (e.key === '[' || e.key === ']') {
      const factor = e.key === '[' ? 0.9 : 1.1;
      state.brushSize = Math.max(1, Math.min(800, Math.round(state.brushSize * factor)));
      try { brushSizeSync(null); } catch {}
    }
    // Lasso shortcuts (when selection exists).
    if (state.lassoPoints.length >= 3) {
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); lassoDeleteSelection(); }
      if (e.key === 'd') { e.preventDefault(); lassoDeleteSelection(); }
      if (e.key === 'c') { e.preventDefault(); lassoCopyToLayer(); }
      if (e.key === 'm') { e.preventDefault(); lassoToMask(); }
    }
  });
}
