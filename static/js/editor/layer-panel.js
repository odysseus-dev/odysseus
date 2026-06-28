/**
 * Layer panel renderer — rebuilds the right-side layer list from
 * `state.layers` every time it's called. The full row tree per layer:
 *
 *   parent row
 *     [drag handle] [eye] [name] [opacity slider] [FX] [dup] [mask] [merge-down] [×]
 *   adjustment sub-rows (FX entries)
 *     [eye] [name+icon] [opacity slider] [merge] [×]
 *   mask sub-rows
 *     [eye] [name] [merge-up?] [×]
 *
 * Reads/writes shared `state` directly (layers, activeLayerId,
 * layerOffsets, imgWidth, imgHeight, lassoPoints/lassoActive,
 * wandMask, maskCanvas/maskCtx, nextLayerId). Function deps are
 * orchestration callbacks still living in galleryEditor.js.
 *
 * Returns `{ render }` so the recursive self-call works via closure
 * over `render` rather than module-state lookup.
 *
 * @param {{
 *   composite:                       () => void,
 *   saveState:                       (label?: string) => void,
 *   showLayerThumb:                  (rowEl: HTMLElement, layer: object) => void,
 *   hideLayerThumb:                  () => void,
 *   loadLayerAlphaAsSelection:       (layer: object) => void,
 *   openFxPopup:                     (layer: object, anchor: HTMLElement) => void,
 *   editAdjLayer:                    (layer: object, adj: object, anchor: HTMLElement) => void,
 *   createLayer:                     (name: string, w: number, h: number) => object,
 *   lassoToMask:                     () => void,
 *   wandToMask:                      () => void,
 *   getActiveMaskLayer:              () => object | null,
 *   syncFxPanelToActiveLayerIfPresent: () => void,
 *   dragSortModule:                  object | null,
 *   uiModule:                        object | null,
 * }} deps
 */
import { state } from './state.js';
import {
  layerHasAdjustments,
  isLayerEmpty,
  isMaskCanvasEmpty,
  adjLayerLabel,
  ADJ_ICONS,
} from './layer-helpers.js';
import { applyAdjustment } from './fx/pixel-pass.js';
import { mergeLayerDownAtIndex } from './wire-merge-buttons.js';

const EYE_OPEN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF  = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';
const EYE_OPEN_SM = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF_SM  = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';

export function createLayerPanelRenderer(deps) {
  const {
    composite, saveState, showLayerThumb, hideLayerThumb,
    loadLayerAlphaAsSelection, openFxPopup, editAdjLayer,
    createLayer, lassoToMask, wandToMask, getActiveMaskLayer,
    syncFxPanelToActiveLayerIfPresent,
    dragSortModule, uiModule,
  } = deps;

  function shouldIgnoreLayerTap() {
    return Date.now() < (window.__geSuppressLayerTapUntil || 0);
  }

  function render() {
    // FX panel mirrors the active layer's adjustments — re-sync on
    // every layer event (activation, add, delete, etc).
    try { syncFxPanelToActiveLayerIfPresent(); } catch {}
    const list = document.getElementById('ge-layers-list');
    if (!list) return;
    // Mobile bottom-sheet peek height — header + N rows, capped so a
    // 20-layer document doesn't get a peek that eats the canvas.
    const panel = document.querySelector('.ge-right-panel');
    if (panel) {
      requestAnimationFrame(() => {
        const header = panel.querySelector('.ge-layers-header');
        const firstRow = list.querySelector('.ge-layer-item');
        const headerH = header ? header.offsetHeight : 52;
        const rowH = firstRow ? firstRow.offsetHeight : 36;
        const allRows = list.querySelectorAll('.ge-layer-item').length;
        const MAX_ROWS = 2;
        const rows = Math.min(allRows, MAX_ROWS);
        panel.style.setProperty('--peek-height', `${headerH + rows * rowH + 6}px`);
      });
    }
    list.innerHTML = '';

    // Render in reverse order (top layer first).
    for (let i = state.layers.length - 1; i >= 0; i--) {
      const layer = state.layers[i];
      const item = document.createElement('div');
      // Parent row is highlighted ONLY when it's actually the paint
      // target — activated AND no mask sub-layer is currently active.
      const parentIsPaintTarget = layer.id === state.activeLayerId &&
        !(layer.masks && layer.activeMaskId && layer.masks.some(m => m.id === layer.activeMaskId));
      item.className = 'ge-layer-item' +
        (parentIsPaintTarget ? ' active' : '') +
        (layer.id === state.activeLayerId && !parentIsPaintTarget ? ' active-parent' : '');
      item.dataset.layerId = layer.id;
      // Hover thumbnail.
      item.addEventListener('mouseenter', () => showLayerThumb(item, layer));
      item.addEventListener('mouseleave', () => hideLayerThumb());
      item.addEventListener('click', (e) => {
        if (shouldIgnoreLayerTap()) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        // Shift+click → load layer transparency as wand selection.
        if (e.shiftKey) {
          e.preventDefault();
          loadLayerAlphaAsSelection(layer);
          return;
        }
        if (state.activeLayerId === layer.id) return;
        state.activeLayerId = layer.id;
        // Toggle the active class inline (avoid full re-render so the
        // dblclick listener on the name element stays alive between
        // clicks — a re-render destroys the element after the first
        // click and the second lands on a different node).
        document.querySelectorAll('.ge-layers-list .ge-layer-item').forEach(el => {
          el.classList.toggle('active', el.dataset.layerId === state.activeLayerId);
        });
      });

      // Drag handle — grip dots; dragSortModule.enable() below scopes
      // drag-init to this handle so row body clicks still activate.
      const handle = document.createElement('span');
      handle.className = 'ge-layer-drag';
      handle.title = 'Drag to reorder';
      handle.innerHTML = '<svg width="8" height="14" viewBox="0 0 8 14" fill="currentColor"><circle cx="2" cy="2" r="1"/><circle cx="6" cy="2" r="1"/><circle cx="2" cy="7" r="1"/><circle cx="6" cy="7" r="1"/><circle cx="2" cy="12" r="1"/><circle cx="6" cy="12" r="1"/></svg>';
      item.appendChild(handle);

      const visBtn = document.createElement('button');
      visBtn.className = 'ge-layer-vis' + (layer.visible ? ' visible' : '');
      visBtn.innerHTML = layer.visible ? EYE_OPEN : EYE_OFF;
      visBtn.title = layer.visible ? 'Hide layer' : 'Show layer';
      visBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        layer.visible = !layer.visible;
        composite();
        render();
      });

      const nameEl = document.createElement('span');
      nameEl.className = 'ge-layer-name';
      nameEl.textContent = layer.name + (isLayerEmpty(layer) ? ' (empty)' : '');
      nameEl.addEventListener('dblclick', () => {
        const input = document.createElement('input');
        input.type = 'text';
        input.value = layer.name;
        input.className = 'ge-layer-name-input';
        nameEl.replaceWith(input);
        input.focus();
        const save = () => { layer.name = input.value || layer.name; render(); };
        input.addEventListener('blur', save);
        input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') save(); });
      });

      const opSlider = document.createElement('input');
      opSlider.type = 'range';
      opSlider.min = '0';
      opSlider.max = '100';
      opSlider.value = String(Math.round(layer.opacity * 100));
      opSlider.className = 'ge-layer-opacity';
      opSlider.title = 'Opacity';
      opSlider.addEventListener('input', (e) => {
        e.stopPropagation();
        layer.opacity = parseInt(e.target.value) / 100;
        composite();
      });
      // Browser :active drops the moment the cursor leaves the slider
      // hit-area in some browsers; a JS-managed `dragging` class
      // survives the OS pointer-capture so the slider stays expanded
      // for the whole drag.
      opSlider.addEventListener('pointerdown', () => {
        opSlider.classList.add('dragging');
        const onUp = () => {
          opSlider.classList.remove('dragging');
          window.removeEventListener('pointerup', onUp);
        };
        window.addEventListener('pointerup', onUp);
      });

      const controls = document.createElement('div');
      controls.className = 'ge-layer-controls';

      // FX (adjustments) — opens a floating popup bound to this layer.
      const fxBtn = document.createElement('button');
      fxBtn.className = 'ge-layer-btn ge-layer-fx-btn' + (layerHasAdjustments(layer) ? ' active' : '');
      fxBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor"/></svg>';
      fxBtn.title = 'Adjust layer (Brightness, Contrast, Saturation, Hue, Levels, Color Balance)';
      fxBtn.style.touchAction = 'manipulation';
      let lastFxPointerOpenAt = 0;
      let fxOpenTimer = null;
      const openLayerFx = (e, delay = 0) => {
        e.preventDefault?.();
        e.stopPropagation();
        window.__geSuppressLayerTapUntil = 0;
        if (fxOpenTimer) clearTimeout(fxOpenTimer);
        fxOpenTimer = setTimeout(() => {
          fxOpenTimer = null;
          openFxPopup(layer, fxBtn);
        }, delay);
      };
      fxBtn.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
      });
      fxBtn.addEventListener('pointerup', (e) => {
        lastFxPointerOpenAt = Date.now();
        const delay = e.pointerType === 'touch' || e.pointerType === 'pen' ? 120 : 0;
        openLayerFx(e, delay);
      });
      fxBtn.addEventListener('click', (e) => {
        if (Date.now() - lastFxPointerOpenAt < 500) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        openLayerFx(e);
      });
      controls.appendChild(fxBtn);

      // Duplicate — clones pixels + offset + opacity + masks + adjLayers
      // + visibility; inserts above the original; new copy becomes
      // active.
      const dupBtn = document.createElement('button');
      dupBtn.className = 'ge-layer-btn';
      dupBtn.title = 'Duplicate layer';
      dupBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
      dupBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        saveState(`Duplicate "${layer.name}"`);
        const copy = createLayer(layer.name + ' copy', layer.canvas.width, layer.canvas.height);
        copy.ctx.drawImage(layer.canvas, 0, 0);
        copy.opacity = layer.opacity;
        copy.visible = layer.visible;
        const srcOff = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
        state.layerOffsets.set(copy.id, { x: srcOff.x, y: srcOff.y });
        if (Array.isArray(layer.masks) && layer.masks.length) {
          copy.masks = layer.masks.map(m => {
            const c = document.createElement('canvas');
            c.width = m.canvas.width; c.height = m.canvas.height;
            c.getContext('2d').drawImage(m.canvas, 0, 0);
            return {
              id: 'mask-' + (state.nextLayerId++),
              name: m.name,
              canvas: c,
              ctx: c.getContext('2d'),
              visible: m.visible !== false,
            };
          });
        }
        if (Array.isArray(layer.adjLayers) && layer.adjLayers.length) {
          copy.adjLayers = layer.adjLayers.map(a => ({
            id: 'adj-' + Math.random().toString(36).slice(2, 9),
            type: a.type,
            name: a.name,
            visible: a.visible !== false,
            opacity: a.opacity != null ? a.opacity : 1,
            params: JSON.parse(JSON.stringify(a.params || {})),
          }));
        }
        const idx = state.layers.findIndex(l => l.id === layer.id);
        if (idx >= 0) state.layers.splice(idx + 1, 0, copy);
        else state.layers.push(copy);
        state.activeLayerId = copy.id;
        composite();
        render();
        if (uiModule) uiModule.showToast('Layer duplicated');
      });
      controls.appendChild(dupBtn);

      // Add-mask — if a lasso/wand selection is active, bake it into a
      // mask sub-layer on this layer; otherwise create an empty mask
      // for the user to paint with the Brush tool.
      const hasLassoSelInitial = state.lassoPoints.length >= 3 && !state.lassoActive;
      const hasWandSelInitial = !!state.wandMask;
      const maskBtn = document.createElement('button');
      maskBtn.className = 'ge-layer-btn ge-layer-mask-btn' +
        ((hasLassoSelInitial || hasWandSelInitial) ? ' from-selection' : '');
      maskBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 12c4 0 4-4 8-4s4 4 8 4-4 4-8 4-4-4-8-4z" fill="currentColor"/></svg>';
      maskBtn.title = (hasLassoSelInitial || hasWandSelInitial)
        ? 'Make mask from current selection'
        : 'Add empty mask (paint with Brush)';
      maskBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        // Activate this layer first so the new mask attaches here.
        state.activeLayerId = layer.id;
        // Re-check selection state AT CLICK TIME — captured vars may
        // be stale if a selection was drawn after the panel paint.
        const hasLassoSel = state.lassoPoints.length >= 3 && !state.lassoActive;
        const hasWandSel = !!state.wandMask;
        if (hasLassoSel) {
          saveState(`Mask from lasso on "${layer.name}"`);
          // Force a fresh mask sub-layer for this conversion so each
          // selection becomes its own mask instead of merging into the
          // previously active one.
          layer.activeMaskId = null;
          lassoToMask();
        } else if (hasWandSel) {
          saveState(`Mask from wand on "${layer.name}"`);
          layer.activeMaskId = null;
          wandToMask();
        } else {
          saveState(`Add mask to "${layer.name}"`);
          const c = document.createElement('canvas');
          c.width = state.imgWidth;
          c.height = state.imgHeight;
          if (!layer.masks) layer.masks = [];
          const mask = {
            id: 'mask-' + (state.nextLayerId++),
            name: 'Mask ' + (layer.masks.length + 1),
            canvas: c,
            ctx: c.getContext('2d'),
            visible: true,
          };
          layer.masks.push(mask);
          layer.activeMaskId = mask.id;
          state.maskCanvas = mask.canvas;
          state.maskCtx = mask.ctx;
          composite();
          render();
        }
      });
      controls.appendChild(maskBtn);

      // Per-row Merge Down — bakes this layer into the one beneath.
      // Hidden on the bottom layer in the visual stack (idx 0 forward).
      if (i > 0) {
        const mergeDownBtn = document.createElement('button');
        mergeDownBtn.className = 'ge-layer-btn';
        mergeDownBtn.title = 'Merge down into layer below';
        mergeDownBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="6 13 12 19 18 13"/></svg>';
        mergeDownBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          saveState(`Merge "${layer.name}" down`);
          mergeLayerDownAtIndex(i);
          composite();
          render();
          uiModule.showToast('Layer merged down');
        });
        controls.appendChild(mergeDownBtn);
      }

      // Delete — shown for every layer except when this is the last
      // remaining one. Base photo is deletable too; Ctrl+Z brings it
      // back from history. Extra confirm for the base layer.
      if (state.layers.length > 1) {
        const delBtn = document.createElement('button');
        delBtn.className = 'ge-layer-btn danger';
        delBtn.textContent = '×';
        delBtn.title = layer.isBase ? 'Delete original layer (Ctrl+Z to undo)' : 'Delete layer';
        delBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          if (layer.isBase && uiModule?.styledConfirm) {
            const ok = await uiModule.styledConfirm(
              'Delete the original photo layer? Ctrl+Z brings it back.',
              { confirmText: (window.__t || (k=>k))('common.delete'), cancelText: (window.__t || (k=>k))('common.cancel'), danger: true }
            );
            if (!ok) return;
          }
          // Snapshot BEFORE removing so Ctrl+Z can bring it back.
          saveState(`Delete layer "${layer.name}"`);
          state.layers.splice(i, 1);
          state.layerOffsets.delete(layer.id);
          if (state.activeLayerId === layer.id) {
            state.activeLayerId = state.layers[Math.min(i, state.layers.length - 1)].id;
          }
          composite();
          render();
        });
        controls.appendChild(delBtn);
      }

      item.appendChild(visBtn);
      item.appendChild(nameEl);
      item.appendChild(opSlider);
      item.appendChild(controls);

      item.addEventListener('click', () => {
        if (shouldIgnoreLayerTap()) return;
        state.activeLayerId = layer.id;
        // Clicking the PARENT row makes layer pixels the paint target
        // (mask is no longer the target). Mask sub-rows stay in the
        // panel; clicking one re-targets it.
        layer.activeMaskId = null;
        state.maskCanvas = null;
        state.maskCtx = null;
        render();
        composite();
      });

      list.appendChild(item);

      // Adjustment sub-layer rows, indented under the parent.
      if (layer.adjLayers && layer.adjLayers.length) {
        for (const adj of layer.adjLayers) {
          const sub = document.createElement('div');
          sub.className = 'ge-layer-item ge-adj-sub-item';
          sub.dataset.adjId = adj.id;
          const sVis = document.createElement('button');
          sVis.className = 'ge-layer-vis' + (adj.visible ? ' visible' : '');
          sVis.innerHTML = adj.visible ? EYE_OPEN_SM : EYE_OFF_SM;
          sVis.title = adj.visible ? 'Hide adjustment' : 'Show adjustment';
          sVis.addEventListener('click', (e) => {
            e.stopPropagation();
            adj.visible = !adj.visible;
            layer._adjFinalKey = null;
            composite();
            render();
          });
          const sName = document.createElement('span');
          sName.className = 'ge-layer-name ge-adj-sub-name';
          sName.innerHTML = `<span class="ge-adj-sub-icon">${ADJ_ICONS[adj.type] || ''}</span><span>${(adj.name || adjLayerLabel(adj.type)).replace(/[<>&]/g,'')}</span>`;
          const sOp = document.createElement('input');
          sOp.type = 'range';
          sOp.min = '0'; sOp.max = '100';
          sOp.value = Math.round(adj.opacity * 100);
          sOp.className = 'ge-layer-opacity';
          sOp.title = 'Adjustment opacity';
          sOp.addEventListener('input', () => {
            adj.opacity = parseInt(sOp.value, 10) / 100;
            layer._adjFinalKey = null;
            composite();
          });
          const sControls = document.createElement('div');
          sControls.className = 'ge-layer-controls';
          const mergeBtn = document.createElement('button');
          mergeBtn.className = 'ge-layer-btn';
          mergeBtn.title = 'Merge into layer (bake)';
          mergeBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
          mergeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            // Bake just this adjustment into layer.canvas, then drop it.
            saveState(`Merge ${adjLayerLabel(adj.type)}`);
            const baked = applyAdjustment(layer.canvas, adj);
            layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
            layer.ctx.drawImage(baked, 0, 0);
            layer.adjLayers = layer.adjLayers.filter(x => x.id !== adj.id);
            layer._adjFinalKey = null;
            composite();
            render();
          });
          sControls.appendChild(mergeBtn);
          const delBtn = document.createElement('button');
          delBtn.className = 'ge-layer-btn danger';
          delBtn.textContent = '×';
          delBtn.title = 'Delete adjustment';
          delBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            saveState(`Delete ${adjLayerLabel(adj.type)}`);
            layer.adjLayers = layer.adjLayers.filter(x => x.id !== adj.id);
            layer._adjFinalKey = null;
            composite();
            render();
          });
          sControls.appendChild(delBtn);

          sub.appendChild(sVis);
          sub.appendChild(sName);
          sub.appendChild(sOp);
          sub.appendChild(sControls);
          // Single-click on the sub-row (outside the inline controls)
          // reopens the adj popup with this sub-layer's params staged.
          sub.addEventListener('click', (e) => {
            if (shouldIgnoreLayerTap()) {
              e.preventDefault();
              e.stopPropagation();
              return;
            }
            if (e.target.closest('.ge-layer-vis, .ge-layer-opacity, .ge-layer-btn')) return;
            if (!e.target.closest('.ge-adj-sub-name')) return;
            e.stopPropagation();
            editAdjLayer(layer, adj, sub);
          });
          list.appendChild(sub);
        }
      }

      // Mask sub-layer rows.
      if (layer.masks && layer.masks.length) {
        for (let mi = 0; mi < layer.masks.length; mi++) {
          const mk = layer.masks[mi];
          const sub = document.createElement('div');
          sub.className = 'ge-layer-item ge-adj-sub-item ge-mask-sub-item' +
            (layer.activeMaskId === mk.id ? ' active' : '');
          sub.dataset.maskId = mk.id;
          const sVis = document.createElement('button');
          sVis.className = 'ge-layer-vis' + (mk.visible ? ' visible' : '');
          sVis.innerHTML = mk.visible ? EYE_OPEN_SM : EYE_OFF_SM;
          sVis.title = mk.visible ? 'Hide mask' : 'Show mask';
          sVis.addEventListener('click', (e) => {
            e.stopPropagation();
            mk.visible = !mk.visible;
            composite();
            render();
          });
          const sName = document.createElement('span');
          sName.className = 'ge-layer-name ge-adj-sub-name';
          const maskIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 12c4 0 4-4 8-4s4 4 8 4-4 4-8 4-4-4-8-4z" fill="currentColor"/></svg>';
          const mkName = String(mk.name || 'Mask').replace(/[<>&]/g, '');
          const mkEmpty = isMaskCanvasEmpty(mk.canvas) ? ' <span style="opacity:0.55;">(empty)</span>' : '';
          sName.innerHTML = `<span class="ge-adj-sub-icon">${maskIcon}</span><span>${mkName}${mkEmpty}</span>`;
          const sControls = document.createElement('div');
          sControls.className = 'ge-layer-controls';
          // Merge-up — combine this mask into the one above (lower mi).
          if (mi > 0) {
            const mergeBtn = document.createElement('button');
            mergeBtn.className = 'ge-layer-btn';
            mergeBtn.title = 'Merge into mask above';
            mergeBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="6 11 12 5 18 11"/></svg>';
            mergeBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              const above = layer.masks[mi - 1];
              if (!above) return;
              saveState(`Merge mask "${mk.name}" into "${above.name}"`);
              // Union of alpha — `source-over` already does max for
              // fully opaque white masks; this also handles partial alpha.
              above.ctx.save();
              above.ctx.globalCompositeOperation = 'source-over';
              above.ctx.drawImage(mk.canvas, 0, 0);
              above.ctx.restore();
              layer.masks = layer.masks.filter(x => x.id !== mk.id);
              if (layer.activeMaskId === mk.id) layer.activeMaskId = above.id;
              const a = getActiveMaskLayer();
              if (a) { state.maskCanvas = a.canvas; state.maskCtx = a.ctx; }
              else   { state.maskCanvas = null;     state.maskCtx = null; }
              composite();
              render();
            });
            sControls.appendChild(mergeBtn);
          }
          const delBtn = document.createElement('button');
          delBtn.className = 'ge-layer-btn danger';
          delBtn.textContent = '×';
          delBtn.title = 'Delete mask';
          delBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            saveState(`Delete mask "${mk.name}"`);
            layer.masks = layer.masks.filter(x => x.id !== mk.id);
            if (layer.activeMaskId === mk.id) {
              layer.activeMaskId = layer.masks[layer.masks.length - 1]?.id || null;
            }
            // Sync global mask plumbing.
            const a = getActiveMaskLayer();
            if (a) { state.maskCanvas = a.canvas; state.maskCtx = a.ctx; }
            else   { state.maskCanvas = null;     state.maskCtx = null; }
            composite();
            render();
          });
          sControls.appendChild(delBtn);
          sub.appendChild(sVis);
          sub.appendChild(sName);
          sub.appendChild(sControls);
          sub.addEventListener('click', (e) => {
            if (e.target.closest('.ge-layer-vis, .ge-layer-btn')) return;
            e.stopPropagation();
            // Activate this mask: paint/inpaint/generate target.
            layer.activeMaskId = mk.id;
            state.activeLayerId = layer.id;
            state.maskCanvas = mk.canvas;
            state.maskCtx = mk.ctx;
            render();
            composite();
          });
          list.appendChild(sub);
        }
      }
    }

    // Wire the shared dragSort module — limit drag-init to the grip
    // handle so row body clicks still activate. Called every render
    // because `enable()` cleans up the previous instance keyed on
    // instanceKey.
    if (dragSortModule) {
      dragSortModule.enable('ge-layers-list', '.ge-layer-item', {
        instanceKey: 'ge-layers',
        handleSelector: '.ge-layer-drag',
        onReorder: (orderedItems) => {
          // DOM is top→bottom = reverse of array order, so the new
          // array is the reverse of the DOM order.
          const byId = new Map(state.layers.map(l => [l.id, l]));
          const newLayers = orderedItems
            .map(el => byId.get(el.dataset.layerId))
            .filter(Boolean)
            .reverse();
          if (newLayers.length === state.layers.length) {
            state.layers = newLayers;
            saveState();
            composite();
          }
        },
      });
    }
  }

  return { render };
}
