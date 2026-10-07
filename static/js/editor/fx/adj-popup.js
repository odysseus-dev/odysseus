/**
 * FX / adjustment-popup machinery — the per-layer Brightness/Contrast,
 * Hue/Saturation, Levels, and Color-Balance editor.
 *
 * Self-contained subsystem with three external touchpoints:
 *
 *  - `composite()`         redraw the canvas after every staged change
 *  - `saveState(label)`    push an undo entry on Apply
 *  - `renderLayerPanel()`  refresh the layer panel after add/edit
 *
 * Lifecycle:
 *
 *   FX button on layer row → openFxPopup(layer, anchor)
 *     → small chooser menu (B/C, H/S, Levels, Color Balance)
 *     → openAdjPopup(layer, type, anchor[, existingAdj])
 *       → buildAdjBody renders the type-specific sliders + histogram
 *       → sliders / histogram handles mutate `layer._stagedAdj.params`
 *       → composite() previews live via the adjLayers stack
 *       → Apply commits to layer.adjLayers + saveState() + renderLayerPanel()
 *       → Cancel / Esc drops the staged state
 *
 * Popups can be minimised → modalManager dock chip → click chip to
 * restore. Re-opening a committed sub-layer (from the layer panel's
 * adj-row click) calls `editAdjLayer` which re-opens openAdjPopup
 * with the existing sub-layer's params staged for editing.
 *
 * @param {{
 *   composite:        () => void,
 *   saveState:        (label?: string) => void,
 *   renderLayerPanel: () => void,
 * }} deps
 *
 * @returns {{
 *   openFxPopup, openAdjPopup, editAdjLayer,
 *   closeFxPopup, closeFxMenu, closeAdjPopup,
 *   ensureFxDock, ensureAdjustments,
 *   syncFxPanelToActiveLayerIfPresent,
 *   minimiseAdjPopup,
 * }}
 */
import { state } from '../state.js';
import modalManager from '../../modalManager.js';
import {
  ADJ_ICONS,
  adjLayerLabel,
  adjustmentPresetOptions,
  adjustmentPresetParams,
  defaultAdjParams,
} from '../layer-helpers.js';
import { drawHistogram } from './histogram.js';

export function createAdjPopupSystem({ composite, saveState, renderLayerPanel, getAdjustmentSource = null }) {
  function suppressLayerGhostTap() {
    window.__geSuppressLayerTapUntil = Date.now() + 650;
  }

  function closeFxPopup() {
    if (state.fxPopupEl) {
      state.fxPopupEl.remove();
      state.fxPopupEl = null;
      state.fxPopupLayerId = null;
    }
  }

  function ensureAdjustments(layer) {
    // Older layers (loaded from saved projects) may be missing the
    // adjustments structure entirely. Pad with identity values.
    if (!layer.adjustments) layer.adjustments = {};
    const a = layer.adjustments;
    if (a.brightness === undefined) a.brightness = 1;
    if (a.contrast === undefined) a.contrast = 1;
    if (a.saturation === undefined) a.saturation = 1;
    if (a.hue === undefined) a.hue = 0;
    if (!a.levels) a.levels = { inBlack: 0, inWhite: 255, gamma: 1.0, outBlack: 0, outWhite: 255 };
    if (!a.colorBalance) a.colorBalance = {
      shadows: { r: 0, g: 0, b: 0 },
      midtones: { r: 0, g: 0, b: 0 },
      highlights: { r: 0, g: 0, b: 0 },
    };
    return a;
  }

  // Floating dock for minimised FX popups — lives at bottom-right.
  function ensureFxDock() {
    let dock = document.getElementById('ge-fx-dock');
    if (!dock) {
      dock = document.createElement('div');
      dock.id = 'ge-fx-dock';
      document.body.appendChild(dock);
    }
    return dock;
  }

  function closeFxMenu() {
    if (state.fxMenuEl) {
      if (state.fxMenuEl._escHandler) {
        document.removeEventListener('keydown', state.fxMenuEl._escHandler, true);
      }
      if (state.fxMenuEl._awayHandler) {
        document.removeEventListener('pointerdown', state.fxMenuEl._awayHandler, true);
      }
      state.fxMenuEl.remove();
      state.fxMenuEl = null;
    }
    document.getElementById('ge-fx-menu-backdrop')?.remove();
  }

  function openFxPopup(layer, anchorEl) {
    if (layer?.kind === 'adjustment') {
      openAdjPopup(layer, layer.adjustment?.type || 'levels', anchorEl, layer.adjustment);
      return;
    }
    // Toggle off ONLY if a menu for this layer is genuinely on-screen.
    // `state` is a shared singleton that survives editor close/reopen,
    // so a stale `fxMenuEl` from a previous session (whose detached
    // element still carries a now-recycled `_layerId`) used to make
    // this guard fire and silently swallow the first click. Verify the
    // element is still in the document before treating it as "open".
    if (state.fxMenuEl && document.body.contains(state.fxMenuEl) &&
        state.fxMenuEl._layerId === layer.id) { closeFxMenu(); return; }
    closeFxMenu();
    if (!layer.adjLayers) layer.adjLayers = [];
    const backdrop = document.createElement('div');
    backdrop.id = 'ge-fx-menu-backdrop';
    backdrop.style.cssText = 'position:fixed;inset:0;z-index:10001;background:transparent;pointer-events:auto;touch-action:none;';
    document.body.appendChild(backdrop);
    backdrop.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      closeFxMenu();
    }, true);
    backdrop.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
    }, true);
    const menu = document.createElement('div');
    menu.className = 'ge-fx-menu ge-frosted';
    menu._layerId = layer.id;
    menu._ignoreActivationUntil = Date.now() + 350;
    menu.style.zIndex = '10002';
    menu.style.pointerEvents = 'auto';
    const items = [
      { type: 'brightness-contrast', label: 'Brightness / Contrast' },
      { type: 'exposure',            label: 'Exposure' },
      { type: 'white-balance',       label: 'White Balance' },
      { type: 'hue-saturation',      label: 'Hue / Saturation' },
      { type: 'vibrance',            label: 'Vibrance' },
      { type: 'black-white',         label: 'Black & White' },
      { type: 'shadows-highlights',  label: 'Shadows / Highlights' },
      { type: 'levels',              label: 'Levels' },
      { type: 'curves',              label: 'Curves' },
      { type: 'color-balance',       label: 'Color Balance' },
      { type: 'selective-color',     label: 'Selective Color' },
      { type: 'gradient-map',        label: 'Gradient Map' },
    ];
    menu.innerHTML = items.map(i =>
      `<button class="ge-fx-menu-item" data-fx-type="${i.type}"><span class="ge-fx-menu-icon">${ADJ_ICONS[i.type] || ''}</span><span>${i.label}</span></button>`
    ).join('');
    document.body.appendChild(menu);
    state.fxMenuEl = menu;
    const activateMenuItem = (btn, ev) => {
      ev?.preventDefault?.();
      ev?.stopPropagation?.();
      if (Date.now() < (menu._ignoreActivationUntil || 0)) return;
      if (!btn || btn.dataset.opening === '1') return;
      btn.dataset.opening = '1';
      const type = btn.dataset.fxType;
      closeFxMenu();
      openAdjPopup(layer, type, anchorEl);
    };
    menu.addEventListener('pointerdown', (ev) => {
      ev.stopPropagation();
    }, true);
    menu.addEventListener('pointerup', (ev) => {
      const btn = ev.target.closest('.ge-fx-menu-item');
      if (btn) activateMenuItem(btn, ev);
      else ev.stopPropagation();
    }, true);
    menu.addEventListener('click', (ev) => {
      const btn = ev.target.closest('.ge-fx-menu-item');
      if (btn) activateMenuItem(btn, ev);
      else ev.stopPropagation();
    }, true);

    const isMobile = window.matchMedia('(max-width: 820px)').matches;
    const r = isMobile ? null : anchorEl?.getBoundingClientRect?.();
    if (isMobile) {
      menu.style.left = '';
      menu.style.top = '';
      menu.style.right = '';
      menu.style.bottom = '';
    } else if (r) {
      const menuW = 220;
      const menuH = menu.offsetHeight || 200;
      const rightX = r.right + 4;
      const leftX  = r.left - menuW - 4;
      const fitsRight = rightX + menuW <= window.innerWidth - 8;
      let left = fitsRight ? rightX : Math.max(8, leftX);
      left = Math.min(window.innerWidth - menuW - 8, Math.max(8, left));
      menu.style.left = left + 'px';
      let top = r.top;
      if (top + menuH > window.innerHeight - 8) top = r.bottom - menuH;
      top = Math.min(window.innerHeight - menuH - 8, Math.max(8, top));
      menu.style.top = top + 'px';
    }
    menu.querySelectorAll('.ge-fx-menu-item').forEach(btn => {
      const activate = (ev) => {
        activateMenuItem(btn, ev);
      };
      btn.addEventListener('pointerup', activate);
      btn.addEventListener('click', activate);
    });
    // Esc closes the menu, capture-phase + stopPropagation so the
    // gallery modal's own Esc handler doesn't fire too.
    const onKey = (ev) => {
      if (ev.key === 'Escape') {
        ev.preventDefault();
        ev.stopPropagation();
        closeFxMenu();
        document.removeEventListener('keydown', onKey, true);
      }
    };
    document.addEventListener('keydown', onKey, true);
    menu._escHandler = onKey;
  }

  // Hide an adj popup and drop a chip into the FX dock. Click the chip
  // to restore the popup in its previous position with staged state
  // intact (we do NOT clear staged on minimise).
  function minimiseAdjPopup(pop) {
    if (!pop) return;
    const type = pop._type;
    const r = pop.getBoundingClientRect();
    pop._stashLeft = r.left;
    pop._stashTop  = r.top;
    pop.style.display = 'none';
    if (state.adjPopupEl === pop) state.adjPopupEl = null;
    const popupId = pop._modalId || `ge-fx-popup-${Math.random().toString(36).slice(2, 8)}`;
    pop._modalId = popupId;
    modalManager.register(popupId, {
      label: adjLayerLabel(type),
      icon: ADJ_ICONS[type] || '',
      restoreFn: () => {
        pop.style.left = pop._stashLeft + 'px';
        pop.style.top  = pop._stashTop  + 'px';
        pop.style.display = '';
        if (state.adjPopupEl && state.adjPopupEl !== pop) {
          const other = state.adjPopupEl;
          state.adjPopupEl = other;
          closeAdjPopup();
        }
        state.adjPopupEl = pop;
      },
      closeFn: () => {
        state.adjPopupEl = pop;
        closeAdjPopup();
        modalManager.unregister(popupId);
      },
    });
    modalManager.minimize(popupId);
  }

  // Re-open an existing committed adjustment sub-layer for editing.
  // Pre-loads its params as the staged state; Apply updates in place.
  function editAdjLayer(layer, adj, anchorEl) {
    openAdjPopup(layer, adj.type, anchorEl, adj);
  }

  function closeAdjPopup() {
    if (state.adjPopupEl) {
      suppressLayerGhostTap();
      const layer = state.adjPopupEl._layer;
      if (layer) {
        if (state.adjPreviewLayer === layer) state.adjPreviewLayer = null;
        layer._adjPreview = false;
        layer._adjCompare = false;
        if (layer._stagedAdj) layer._stagedAdj = null;
        if (layer._newAdjustmentLayer) {
          const index = state.layers.findIndex(item => item.id === layer.id);
          if (index >= 0) state.layers.splice(index, 1);
          state.layerOffsets.delete(layer.id);
          if (Number.isInteger(layer._newAdjustmentUndoLength) && state.undoStack.length > layer._newAdjustmentUndoLength) {
            state.undoStack.length = layer._newAdjustmentUndoLength;
          }
          state.activeLayerId = state.layers[state.layers.length - 1]?.id || null;
          layer._newAdjustmentLayer = false;
          renderLayerPanel();
        }
        if (layer._editingAdjId) layer._editingAdjId = null;
        layer._adjFinalKey = null;
        composite();
      }
      if (state.adjPopupEl._escHandler) {
        document.removeEventListener('keydown', state.adjPopupEl._escHandler, true);
      }
      if (state.adjPopupEl._modalId) {
        try { modalManager.unregister(state.adjPopupEl._modalId); } catch {}
      }
      state.adjPopupEl.remove();
      state.adjPopupEl = null;
    }
  }

  function openAdjPopup(layer, type, anchorEl, existingAdj) {
    closeAdjPopup();
    // Editing an existing sub-layer? Pre-load its params as the staged
    // preview and mark the popup so Apply updates instead of appending.
    const firstClass = layer?.kind === 'adjustment';
    const editing = !!existingAdj;
    const presetOptions = adjustmentPresetOptions(type);
    const startParams = editing
      ? JSON.parse(JSON.stringify(existingAdj.params))
      : defaultAdjParams(type);
    layer._stagedAdj = { type, params: startParams };
    layer._adjPreview = true;
    state.adjPreviewLayer = layer;
    if (editing) {
      // Hide the existing sub-layer from the render stack so the
      // staged preview shows correctly without doubling the effect.
      layer._editingAdjId = existingAdj.id;
      layer._adjFinalKey = null;
    }
    const pop = document.createElement('div');
    pop.className = 'ge-adj-popup ge-frosted';
    pop.style.zIndex = '10003';
    pop._layer = layer;
    pop._type = type;
    pop._anchorEl = anchorEl;
    pop._existingAdj = existingAdj || null;
    pop.innerHTML = `
    <div class="ge-adj-head" data-adj-drag>
      <span class="ge-adj-icon">${ADJ_ICONS[type] || ''}</span>
      <span class="ge-adj-title">${adjLayerLabel(type)}</span>
      <span class="ge-head-btns">
        <button class="ge-adj-min" type="button" title="Minimise">&minus;</button>
      </span>
    </div>
    <label class="ge-adj-preset-field">Preset
      <select class="ge-adj-preset-select">
        <option value="custom"${editing ? ' selected' : ''}>Custom</option>
        ${presetOptions.map(name => `<option value="${name}"${!editing && name === 'Default' ? ' selected' : ''}>${name}</option>`).join('')}
      </select>
    </label>
    <div class="ge-adj-body" data-adj-body></div>
    <div class="ge-adj-foot">
      <button class="ge-btn ge-btn-sm ge-adj-reset-btn" data-adj-action="reset">Reset</button>
      <span class="ge-adj-foot-spacer"></span>
      <button class="ge-btn ge-btn-sm ge-adj-compare-btn" data-adj-action="compare" aria-pressed="false">Compare</button>
      <button class="ge-btn ge-btn-sm ge-adj-cancel-btn" data-adj-action="cancel">Cancel</button>
      <button class="ge-btn ge-btn-sm ge-btn-primary ge-adj-apply-btn" data-adj-action="ok">Apply</button>
    </div>
    `;
    document.body.appendChild(pop);
    state.adjPopupEl = pop;

    const r = anchorEl?.getBoundingClientRect?.();
    const pw = type === 'color-balance' ? 340 : 320;
    // Prefer right of anchor; fall back to left if no room.
    let left;
    if (r) {
      const rightX = r.right + 8;
      const leftX  = r.left - pw - 8;
      const fitsRight = rightX + pw <= window.innerWidth - 8;
      left = fitsRight ? rightX : Math.max(8, leftX);
    } else {
      left = (window.innerWidth - pw) / 2;
    }
    const top = r ? Math.max(8, r.top - 20) : 60;
    pop.style.left = left + 'px';
    pop.style.top  = top  + 'px';

    const body = pop.querySelector('[data-adj-body]');
    buildAdjBody(layer, type, body, pop);
    body.addEventListener('input', () => {
      const presetSelect = pop.querySelector('.ge-adj-preset-select');
      if (presetSelect) presetSelect.value = 'custom';
    });
    const popupRect = pop.getBoundingClientRect();
    if (!window.matchMedia('(max-width: 820px)').matches) {
      const clampedTop = Math.max(8, Math.min(Number.parseFloat(pop.style.top) || 8, window.innerHeight - popupRect.height - 8));
      pop.style.top = `${clampedTop}px`;
    }

    pop.querySelector('.ge-adj-close')?.addEventListener('click', closeAdjPopup);
    pop.querySelector('.ge-adj-min')?.addEventListener('click', () => minimiseAdjPopup(pop));
    // Drag by head — anywhere except buttons. Mobile pins via !important
    // rules; setProperty with 'important' lets inline styles win during drag.
    const head = pop.querySelector('[data-adj-drag]');
    if (head) {
      const isMobile = window.matchMedia('(max-width: 820px)').matches;
      const setPos = (x, y) => {
        if (isMobile) {
          pop.style.setProperty('left', x + 'px', 'important');
          pop.style.setProperty('top', y + 'px', 'important');
          pop.style.setProperty('right', 'auto', 'important');
          pop.style.setProperty('bottom', 'auto', 'important');
          pop.style.setProperty('width', 'auto', 'important');
          pop.style.setProperty('max-width', 'calc(100vw - 16px)', 'important');
        } else {
          pop.style.left = x + 'px';
          pop.style.top = y + 'px';
        }
      };
      head.style.touchAction = 'none';
      head.addEventListener('pointerdown', (e) => {
        if (e.target.closest('button')) return;
        e.preventDefault();
        const startX = e.clientX, startY = e.clientY;
        const r0 = pop.getBoundingClientRect();
        head.setPointerCapture(e.pointerId);
        head.style.cursor = 'grabbing';
        const onMove = (ev) => {
          const nx = Math.max(0, Math.min(window.innerWidth - 60, r0.left + (ev.clientX - startX)));
          const ny = Math.max(0, Math.min(window.innerHeight - 30, r0.top  + (ev.clientY - startY)));
          setPos(nx, ny);
        };
        const onUp = () => {
          head.releasePointerCapture(e.pointerId);
          head.style.cursor = '';
          head.removeEventListener('pointermove', onMove);
          head.removeEventListener('pointerup', onUp);
        };
        head.addEventListener('pointermove', onMove);
        head.addEventListener('pointerup', onUp);
      });
    }
    // Esc closes; capture-phase + stopPropagation so the gallery modal's
    // own Esc handler doesn't fire too.
    const onKey = (ev) => {
      if (ev.key === 'Escape') {
        ev.preventDefault();
        ev.stopPropagation();
        closeAdjPopup();
        document.removeEventListener('keydown', onKey, true);
      }
    };
    document.addEventListener('keydown', onKey, true);
    pop._escHandler = onKey;
    pop.querySelector('[data-adj-action="cancel"]')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeAdjPopup();
    });
    pop.querySelector('[data-adj-action="reset"]')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      layer._stagedAdj.params = defaultAdjParams(type);
      const presetSelect = pop.querySelector('.ge-adj-preset-select');
      if (presetSelect) presetSelect.value = 'Default';
      body.innerHTML = '';
      buildAdjBody(layer, type, body, pop);
      scheduleAdjRefresh(layer);
    });
    pop.querySelector('.ge-adj-preset-select')?.addEventListener('change', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const preset = e.currentTarget.value;
      layer._stagedAdj.params = adjustmentPresetParams(type, preset);
      layer._adjFinalKey = null;
      body.innerHTML = '';
      buildAdjBody(layer, type, body, pop);
      scheduleAdjRefresh(layer);
    });
    pop.querySelector('[data-adj-action="compare"]')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      layer._adjCompare = !layer._adjCompare;
      e.currentTarget.setAttribute('aria-pressed', layer._adjCompare ? 'true' : 'false');
      e.currentTarget.classList.toggle('is-active', layer._adjCompare);
      layer._adjFinalKey = null;
      composite();
    });
    pop.querySelector('[data-adj-action="ok"]')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      suppressLayerGhostTap();
      if (!layer._newAdjustmentLayer) {
        saveState(editing ? `Edit ${adjLayerLabel(type)}` : `Add ${adjLayerLabel(type)}`);
      }
      const params = layer._stagedAdj.params;
      layer._stagedAdj = null;
      layer._adjPreview = false;
      layer._adjCompare = false;
      if (firstClass) {
        layer.adjustment = { type, params };
        layer.name = layer.name || adjLayerLabel(type);
        layer._newAdjustmentLayer = false;
        layer._newAdjustmentUndoLength = null;
      } else if (editing) {
        const existing = (layer.adjLayers || []).find(a => a.id === existingAdj.id);
        if (existing) existing.params = params;
        layer._editingAdjId = null;
      } else {
        if (!layer.adjLayers) layer.adjLayers = [];
        layer.adjLayers.push({
          id: 'adj-' + Math.random().toString(36).slice(2, 9),
          type,
          name: adjLayerLabel(type),
          visible: true,
          opacity: 1,
          params,
        });
      }
      layer._adjFinalKey = null;
      composite();
      renderLayerPanel();
      closeAdjPopup();
    });
  }

  // rAF-throttled live preview while sliders are dragged.
  function scheduleAdjRefresh(layer) {
    if (state.adjRafPending) return;
    state.adjRafPending = true;
    requestAnimationFrame(() => {
      state.adjRafPending = false;
      if (state.adjPreviewLayer !== layer || !layer._stagedAdj) return;
      layer._adjFinalKey = null;
      composite();
    });
  }

  function buildAdjBody(layer, type, body, popEl) {
    const p = layer._stagedAdj.params;
    const levelValues = () => {
      const channel = ['red', 'green', 'blue'].includes(p.channel) ? p.channel : 'rgb';
      if (channel === 'rgb') return p;
      if (!p.channels) p.channels = {};
      if (!p.channels[channel]) p.channels[channel] = defaultAdjParams('levels').channels[channel];
      return p.channels[channel];
    };
    const revertIcon = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>';
    const sliderRow = (key, label, min, max, value, suffix) => `
      <div class="ge-adj-row" data-adj-key="${key}">
        <label>${label}</label>
        <input type="range" min="${min}" max="${max}" value="${value}" data-key="${key}" />
        <span class="ge-adj-value">${value}${suffix || ''}</span>
        <button class="ge-adj-revert" type="button" title="Reset this slider" data-revert-key="${key}">${revertIcon}</button>
      </div>
    `;
    if (type === 'brightness-contrast') {
      const bSlider = Math.round((p.brightness - 1) * 100);
      const cSlider = Math.round((p.contrast - 1) * 100);
      body.innerHTML = `
      ${sliderRow('brightness', 'Brightness', -100, 100, bSlider, '')}
      ${sliderRow('contrast',   'Contrast',   -100, 100, cSlider, '')}
    `;
    } else if (type === 'exposure') {
      body.innerHTML = `
      ${sliderRow('exposure', 'Exposure', -500, 500, Math.round(p.exposure * 100), ' EV')}
      ${sliderRow('offset', 'Offset', -50, 50, Math.round(p.offset * 100), '')}
      ${sliderRow('gamma', 'Gamma', 10, 300, Math.round(p.gamma * 100), 'γ')}
    `;
    } else if (type === 'white-balance') {
      body.innerHTML = `
      ${sliderRow('temperature', 'Temperature', -100, 100, Math.round(p.temperature), '')}
      ${sliderRow('tint', 'Tint', -100, 100, Math.round(p.tint), '')}
    `;
    } else if (type === 'hue-saturation') {
      const hSlider = Math.round(p.hue);
      const sSlider = Math.round((p.saturation - 1) * 100);
      body.innerHTML = `
      ${sliderRow('hue',        'Hue',        -180, 180, hSlider, ' °')}
      ${sliderRow('saturation', 'Saturation', -100, 100, sSlider, '')}
      ${sliderRow('lightness',  'Lightness',  -100, 100, Math.round(p.lightness || 0), '')}
    `;
    } else if (type === 'vibrance') {
      body.innerHTML = `
      ${sliderRow('vibrance', 'Vibrance', -100, 100, Math.round(p.vibrance || 0), '')}
      <p class="ge-adj-help">Boosts muted colors more than already-saturated colors.</p>
    `;
    } else if (type === 'black-white') {
      body.innerHTML = `
      ${sliderRow('red', 'Red', 0, 100, Math.round(p.red ?? 30), '%')}
      ${sliderRow('green', 'Green', 0, 100, Math.round(p.green ?? 59), '%')}
      ${sliderRow('blue', 'Blue', 0, 100, Math.round(p.blue ?? 11), '%')}
      ${sliderRow('constant', 'Constant', -100, 100, Math.round(p.constant || 0), '')}
      <p class="ge-adj-help">Mixes the source channels into a neutral grayscale result.</p>
    `;
    } else if (type === 'shadows-highlights') {
      body.innerHTML = `
      ${sliderRow('shadows', 'Shadows', -100, 100, Math.round(p.shadows || 0), '')}
      ${sliderRow('highlights', 'Highlights', -100, 100, Math.round(p.highlights || 0), '')}
      <p class="ge-adj-help">Recover detail in dark and bright tones without shifting midtones.</p>
    `;
    } else if (type === 'levels') {
      const values = levelValues();
      // Histogram canvas + sliders. Histogram is computed from the
      // layer's pixel data (after any adjLayers below this one) so
      // the user is matching levels against what they're really seeing.
      // <details> wrapper is collapsed by default on mobile to save
      // vertical space; open by default on desktop.
      const isMobile = window.matchMedia('(max-width: 820px)').matches;
      body.innerHTML = `
      <label class="ge-adj-channel-field">Channel
        <select class="ge-adj-channel-select">
          <option value="rgb"${(p.channel || 'rgb') === 'rgb' ? ' selected' : ''}>RGB</option>
          <option value="red"${p.channel === 'red' ? ' selected' : ''}>Red</option>
          <option value="green"${p.channel === 'green' ? ' selected' : ''}>Green</option>
          <option value="blue"${p.channel === 'blue' ? ' selected' : ''}>Blue</option>
        </select>
      </label>
      <details class="ge-adj-hist-details"${isMobile ? '' : ' open'}>
        <summary>Histogram</summary>
        <div class="ge-adj-hist-wrap">
          <canvas class="ge-adj-histogram" width="280" height="80"></canvas>
          <div class="ge-adj-hist-handles">
            <div class="ge-adj-hist-handle hist-h-black"  data-handle="inBlack"  title="Input black — drag"></div>
            <div class="ge-adj-hist-handle hist-h-gamma"  data-handle="gamma"    title="Gamma — drag"></div>
            <div class="ge-adj-hist-handle hist-h-white"  data-handle="inWhite"  title="Input white — drag"></div>
          </div>
        </div>
        <div class="ge-adj-clip-status" aria-live="polite"></div>
      </details>
      ${sliderRow('inBlack',  'Input black',  0, 254, values.inBlack, '')}
      ${sliderRow('inWhite',  'Input white',  1, 255, values.inWhite, '')}
      ${sliderRow('gamma',    'Gamma',        10, 990, Math.round((values.gamma || 1) * 100), 'γ')}
      ${sliderRow('outBlack', 'Output black', 0, 255, values.outBlack, '')}
      ${sliderRow('outWhite', 'Output white', 0, 255, values.outWhite, '')}
    `;
      const hist = body.querySelector('.ge-adj-histogram');
      const histogramLayer = layer.kind === 'adjustment' && getAdjustmentSource
        ? { canvas: getAdjustmentSource(layer), _stagedAdj: { params: values } }
        : layer;
      const clipStatus = body.querySelector('.ge-adj-clip-status');
      const renderHistogram = () => {
        const channel = ['red', 'green', 'blue'].includes(p.channel) ? p.channel : 'rgb';
        const stats = drawHistogram(hist, histogramLayer, channel);
        if (clipStatus) {
          const channelLabel = channel === 'rgb' ? 'RGB' : channel[0].toUpperCase() + channel.slice(1);
          clipStatus.textContent = `${channelLabel} clipping: black ${stats.black.toFixed(1)}% · white ${stats.white.toFixed(1)}%`;
        }
      };
      renderHistogram();
      wireHistogramHandles(body, layer, type);
      // Redraw histogram when the user opens the disclosure (canvas
      // dimensions are layout-dependent).
      body.querySelector('.ge-adj-hist-details')?.addEventListener('toggle', (e) => {
        if (e.target.open) renderHistogram();
      });
      body.querySelector('.ge-adj-channel-select')?.addEventListener('change', event => {
        p.channel = event.target.value;
        buildAdjBody(layer, type, body, popEl);
      });
    } else if (type === 'curves') {
      if (!p.points) p.points = defaultAdjParams('curves').points;
      if (!p.channel) p.channel = 'rgb';
      body.innerHTML = `
        <label class="ge-adj-channel-field">Channel
          <select class="ge-adj-channel-select">
            <option value="rgb"${p.channel === 'rgb' ? ' selected' : ''}>RGB</option>
            <option value="red"${p.channel === 'red' ? ' selected' : ''}>Red</option>
            <option value="green"${p.channel === 'green' ? ' selected' : ''}>Green</option>
            <option value="blue"${p.channel === 'blue' ? ' selected' : ''}>Blue</option>
          </select>
        </label>
        <div class="ge-curves-wrap">
          <canvas class="ge-curves-canvas" width="280" height="180" aria-label="Editable tone curve"></canvas>
        </div>
        <p class="ge-adj-help">Click the curve to add a point. Drag to adjust. Double-click a point to remove it.</p>
      `;
      const canvas = body.querySelector('.ge-curves-canvas');
      wireCurveEditor(canvas, layer);
      body.querySelector('.ge-adj-channel-select')?.addEventListener('change', event => {
        p.channel = event.target.value;
        buildAdjBody(layer, type, body, popEl);
      });
    } else if (type === 'color-balance') {
      // Color-tinted slider ends so the user sees what direction does what.
      const cbRow = (key, leftCol, rightCol, label, value) => `
      <div class="ge-adj-row ge-adj-cb-row" data-adj-key="${key}">
        <span class="ge-adj-cb-dot" style="background:${leftCol}"></span>
        <input type="range" min="-100" max="100" value="${value}" data-key="${key}" />
        <span class="ge-adj-cb-dot" style="background:${rightCol}"></span>
        <span class="ge-adj-value">${value}</span>
        <button class="ge-adj-revert" type="button" title="Reset this slider" data-revert-key="${key}">${revertIcon}</button>
      </div>
    `;
      // Tone picker: one tone group visible at a time. Remember the
      // last picked tone on the popup so re-renders (revert button
      // etc.) keep it.
      const tone = popEl._cbTone || 'shadows';
      popEl._cbTone = tone;
      const toneSliders = (t) => `
      ${cbRow(`${t}-r`, '#00d2d2', '#ff5555', 'Cyan ↔ Red',      p[t].r)}
      ${cbRow(`${t}-g`, '#d855d8', '#55d855', 'Magenta ↔ Green', p[t].g)}
      ${cbRow(`${t}-b`, '#e6e64a', '#4a78ff', 'Yellow ↔ Blue',   p[t].b)}
    `;
      body.innerHTML = `
      <div class="ge-adj-cb-tone-picker">
        <select class="ge-adj-cb-tone-select">
          <option value="shadows"${tone === 'shadows' ? ' selected' : ''}>Shadows</option>
          <option value="midtones"${tone === 'midtones' ? ' selected' : ''}>Midtones</option>
          <option value="highlights"${tone === 'highlights' ? ' selected' : ''}>Highlights</option>
        </select>
      </div>
      <div class="ge-adj-cb-sliders" data-cb-tone="${tone}">
        ${toneSliders(tone)}
      </div>
    `;
      body.querySelector('.ge-adj-cb-tone-select')?.addEventListener('change', (e) => {
        popEl._cbTone = e.target.value;
        body.innerHTML = '';
        buildAdjBody(layer, type, body, popEl);
      });
    } else if (type === 'selective-color') {
      if (!p.range) p.range = 'reds';
      if (!p.ranges?.[p.range]) p.ranges = defaultAdjParams(type).ranges;
      const values = p.ranges[p.range];
      const ranges = ['reds', 'yellows', 'greens', 'cyans', 'blues', 'magentas', 'neutrals', 'blacks'];
      body.innerHTML = `
        <label class="ge-adj-channel-field">Colors
          <select class="ge-adj-selective-range">
            ${ranges.map(name => `<option value="${name}"${p.range === name ? ' selected' : ''}>${name[0].toUpperCase() + name.slice(1)}</option>`).join('')}
          </select>
        </label>
        ${sliderRow('cyan', 'Cyan', -100, 100, values.cyan, '')}
        ${sliderRow('magenta', 'Magenta', -100, 100, values.magenta, '')}
        ${sliderRow('yellow', 'Yellow', -100, 100, values.yellow, '')}
        ${sliderRow('black', 'Black', -100, 100, values.black, '')}
      `;
      body.querySelector('.ge-adj-selective-range')?.addEventListener('change', event => {
        p.range = event.target.value;
        buildAdjBody(layer, type, body, popEl);
      });
    } else if (type === 'gradient-map') {
      body.innerHTML = `
        <div class="ge-gradient-map-preview" style="--ge-gradient-start:${p.shadows};--ge-gradient-end:${p.highlights}"></div>
        <div class="ge-gradient-color-row">
          <label>Shadows <input type="color" data-gradient-key="shadows" value="${p.shadows}"></label>
          <label>Highlights <input type="color" data-gradient-key="highlights" value="${p.highlights}"></label>
        </div>
        ${sliderRow('midpoint', 'Midpoint', 1, 99, Math.round(p.midpoint), '%')}
        <label class="ge-adj-toggle-row"><span>Reverse</span><input type="checkbox" data-gradient-reverse${p.reverse ? ' checked' : ''}><span class="ge-toggle-track"></span></label>
      `;
      body.querySelectorAll('[data-gradient-key]').forEach(input => {
        input.addEventListener('input', () => {
          p[input.dataset.gradientKey] = input.value;
          const preview = body.querySelector('.ge-gradient-map-preview');
          preview?.style.setProperty('--ge-gradient-start', p.shadows);
          preview?.style.setProperty('--ge-gradient-end', p.highlights);
          scheduleAdjRefresh(layer);
        });
      });
      body.querySelector('[data-gradient-reverse]')?.addEventListener('change', event => {
        p.reverse = event.target.checked;
        scheduleAdjRefresh(layer);
      });
    }
    // Wire all sliders.
    body.querySelectorAll('input[type="range"]').forEach(sl => {
      sl.addEventListener('input', () => onAdjSliderInput(layer, type, sl));
    });
    // Per-slider revert buttons.
    body.querySelectorAll('.ge-adj-revert').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = btn.dataset.revertKey;
        revertAdjKey(layer, type, key);
        // Rebuild body so values + histogram refresh.
        body.innerHTML = '';
        buildAdjBody(layer, type, body, popEl);
      });
    });
  }

  // Reset a single slider key back to identity. Updates staged params
  // and triggers a composite refresh.
  function revertAdjKey(layer, type, key) {
    const defaults = defaultAdjParams(type);
    const p = layer._stagedAdj.params;
    if (['brightness-contrast', 'exposure', 'white-balance', 'hue-saturation', 'vibrance', 'black-white', 'shadows-highlights', 'gradient-map'].includes(type)) {
      p[key] = defaults[key];
    } else if (type === 'levels') {
      const channel = ['red', 'green', 'blue'].includes(p.channel) ? p.channel : 'rgb';
      const values = channel === 'rgb' ? p : p.channels[channel];
      const defaultValues = channel === 'rgb' ? defaults : defaults.channels[channel];
      values[key] = defaultValues[key];
    } else if (type === 'color-balance') {
      const [tone, ch] = key.split('-');
      p[tone][ch] = defaults[tone][ch];
    } else if (type === 'selective-color') {
      p.ranges[p.range][key] = defaults.ranges[p.range][key];
    }
    layer._adjFinalKey = null;
    composite();
  }

  function onAdjSliderInput(layer, type, sl) {
    const key = sl.dataset.key;
    const raw = parseInt(sl.value, 10);
    const valEl = sl.parentElement.querySelector('.ge-adj-value');
    const p = layer._stagedAdj.params;
    let display = String(raw);
    if (type === 'brightness-contrast' || type === 'hue-saturation') {
      if (key === 'brightness' || key === 'contrast' || key === 'saturation') {
        p[key] = 1 + raw / 100;
      } else if (key === 'hue') {
        p.hue = raw; display = raw + ' °';
      } else if (key === 'lightness') {
        p.lightness = raw;
      }
    } else if (type === 'exposure') {
      if (key === 'exposure') { p.exposure = raw / 100; display = (raw / 100).toFixed(2) + ' EV'; }
      else if (key === 'offset') { p.offset = raw / 100; display = (raw / 100).toFixed(2); }
      else if (key === 'gamma') { p.gamma = raw / 100; display = (raw / 100).toFixed(2) + 'γ'; }
    } else if (type === 'white-balance') {
      p[key] = raw;
    } else if (type === 'vibrance') {
      p[key] = raw;
    } else if (type === 'black-white') {
      p[key] = raw;
    } else if (type === 'shadows-highlights') {
      p[key] = raw;
    } else if (type === 'levels') {
      const channel = ['red', 'green', 'blue'].includes(p.channel) ? p.channel : 'rgb';
      const values = channel === 'rgb' ? p : p.channels[channel];
      if (key === 'gamma') {
        values.gamma = raw / 100; display = (raw / 100).toFixed(2) + 'γ';
      } else {
        values[key] = raw;
      }
    } else if (type === 'color-balance') {
      const [tone, ch] = key.split('-');
      p[tone][ch] = raw;
    } else if (type === 'selective-color') {
      p.ranges[p.range][key] = raw;
    } else if (type === 'gradient-map') {
      p.midpoint = raw;
      display = raw + '%';
    }
    if (valEl) valEl.textContent = display;
    scheduleAdjRefresh(layer);
  }

  function wireCurveEditor(canvas, layer) {
    if (!canvas) return;
    const params = layer._stagedAdj.params;
    const channelColor = { rgb: '#f1f5f9', red: '#ff6b78', green: '#57d68d', blue: '#69a7ff' };
    const points = () => params.points[params.channel] || params.points.rgb;
    const canvasPoint = event => {
      const rect = canvas.getBoundingClientRect();
      return {
        x: Math.max(0, Math.min(255, ((event.clientX - rect.left) / rect.width) * 255)),
        y: Math.max(0, Math.min(255, (1 - (event.clientY - rect.top) / rect.height) * 255)),
      };
    };
    const draw = () => {
      const source = layer.kind === 'adjustment' && getAdjustmentSource
        ? getAdjustmentSource(layer)
        : layer.canvas;
      drawHistogram(canvas, { canvas: source });
      const ctx = canvas.getContext('2d');
      const width = canvas.width;
      const height = canvas.height;
      ctx.strokeStyle = 'rgba(255,255,255,.12)';
      ctx.lineWidth = 1;
      for (let index = 1; index < 4; index += 1) {
        const x = Math.round(width * index / 4) + .5;
        const y = Math.round(height * index / 4) + .5;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
      }
      const list = points();
      ctx.strokeStyle = channelColor[params.channel] || channelColor.rgb;
      ctx.lineWidth = 2;
      ctx.beginPath();
      list.forEach((point, index) => {
        const x = point[0] / 255 * width;
        const y = height - point[1] / 255 * height;
        if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      });
      ctx.stroke();
      for (const point of list) {
        const x = point[0] / 255 * width;
        const y = height - point[1] / 255 * height;
        ctx.fillStyle = '#0b0d10';
        ctx.strokeStyle = channelColor[params.channel] || channelColor.rgb;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }
    };
    const nearestIndex = event => {
      const point = canvasPoint(event);
      let nearest = -1;
      let distance = 14;
      points().forEach((candidate, index) => {
        const next = Math.hypot(candidate[0] - point.x, candidate[1] - point.y);
        if (next < distance) { distance = next; nearest = index; }
      });
      return nearest;
    };
    canvas.addEventListener('pointerdown', event => {
      event.preventDefault();
      const list = points();
      let index = nearestIndex(event);
      if (index < 0) {
        const point = canvasPoint(event);
        list.push([Math.round(point.x), Math.round(point.y)]);
        list.sort((a, b) => a[0] - b[0]);
        index = list.findIndex(item => item[0] === Math.round(point.x) && item[1] === Math.round(point.y));
      }
      canvas.setPointerCapture(event.pointerId);
      const move = moveEvent => {
        const next = canvasPoint(moveEvent);
        const current = list[index];
        const endpoint = index === 0 || index === list.length - 1;
        current[0] = endpoint
          ? (index === 0 ? 0 : 255)
          : Math.round(Math.max(list[index - 1][0] + 1, Math.min(list[index + 1][0] - 1, next.x)));
        current[1] = Math.round(next.y);
        draw();
        scheduleAdjRefresh(layer);
      };
      const up = () => {
        canvas.releasePointerCapture(event.pointerId);
        canvas.removeEventListener('pointermove', move);
        canvas.removeEventListener('pointerup', up);
      };
      canvas.addEventListener('pointermove', move);
      canvas.addEventListener('pointerup', up);
      draw();
      scheduleAdjRefresh(layer);
    });
    canvas.addEventListener('dblclick', event => {
      const index = nearestIndex(event);
      const list = points();
      if (index <= 0 || index >= list.length - 1) return;
      list.splice(index, 1);
      draw();
      scheduleAdjRefresh(layer);
    });
    draw();
  }

  // Position the three histogram triangle handles by current staged
  // values + wire pointer drags.
  function wireHistogramHandles(bodyEl, layer, type) {
    const wrap = bodyEl.querySelector('.ge-adj-hist-wrap');
    const canvas = bodyEl.querySelector('.ge-adj-histogram');
    if (!wrap || !canvas) return;
    const handles = bodyEl.querySelectorAll('.ge-adj-hist-handle');
    const levelParams = () => {
      const root = layer._stagedAdj.params;
      const channel = ['red', 'green', 'blue'].includes(root.channel) ? root.channel : 'rgb';
      return channel === 'rgb' ? root : root.channels[channel];
    };
    const placeHandles = () => {
      const w = canvas.getBoundingClientRect().width;
      const p = levelParams();
      const xB = (p.inBlack  / 255) * w;
      const xW = (p.inWhite  / 255) * w;
      // Gamma handle sits at a fraction of the (xB..xW) span, mapped
      // from gamma's log scale (1 = midpoint, 0.1 = far right, 10 = far left).
      const gammaT = 1 - (Math.log(p.gamma || 1) / Math.log(10) * 0.5 + 0.5);
      const xG = xB + (xW - xB) * gammaT;
      const set = (sel, x) => {
        const el = bodyEl.querySelector(sel);
        if (el) el.style.left = (x - 6) + 'px';
      };
      set('.hist-h-black', xB);
      set('.hist-h-gamma', xG);
      set('.hist-h-white', xW);
    };
    placeHandles();
    handles.forEach(h => {
      h.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        e.stopPropagation();
        h.setPointerCapture(e.pointerId);
        const which = h.dataset.handle;
        const rect = canvas.getBoundingClientRect();
        const onMove = (ev) => {
          const x = Math.max(0, Math.min(rect.width, ev.clientX - rect.left));
          const v = Math.round((x / rect.width) * 255);
          const p = levelParams();
          if (which === 'inBlack') {
            p.inBlack = Math.min(p.inWhite - 1, v);
          } else if (which === 'inWhite') {
            p.inWhite = Math.max(p.inBlack + 1, v);
          } else if (which === 'gamma') {
            const xB = (p.inBlack / 255) * rect.width;
            const xW = (p.inWhite / 255) * rect.width;
            const span = Math.max(1, xW - xB);
            let t = (x - xB) / span;
            t = Math.max(0.01, Math.min(0.99, t));
            // Invert the placeHandles mapping: t = 1 - (log10(g)*0.5+0.5).
            const log10g = -((t - 0.5) * 2);
            p.gamma = Math.pow(10, log10g);
          }
          placeHandles();
          // Update visible slider rows + value labels.
          const updateRow = (key, displayVal) => {
            const sl = bodyEl.querySelector(`input[type="range"][data-key="${key}"]`);
            const values = levelParams();
            if (sl) sl.value = String(key === 'gamma' ? Math.round(values.gamma * 100) : values[key]);
            const val = sl?.parentElement.querySelector('.ge-adj-value');
            if (val) val.textContent = displayVal;
          };
          const values = levelParams();
          if (which === 'inBlack') updateRow('inBlack', String(values.inBlack));
          if (which === 'inWhite') updateRow('inWhite', String(values.inWhite));
          if (which === 'gamma')   updateRow('gamma',   values.gamma.toFixed(2) + 'γ');
          const histogramLayer = layer.kind === 'adjustment' && getAdjustmentSource
            ? { canvas: getAdjustmentSource(layer), _stagedAdj: { params: values } }
            : layer;
          const channel = ['red', 'green', 'blue'].includes(layer._stagedAdj.params.channel)
            ? layer._stagedAdj.params.channel : 'rgb';
          const stats = drawHistogram(canvas, histogramLayer, channel);
          const clipStatus = bodyEl.querySelector('.ge-adj-clip-status');
          if (clipStatus) {
            const channelLabel = channel === 'rgb' ? 'RGB' : channel[0].toUpperCase() + channel.slice(1);
            clipStatus.textContent = `${channelLabel} clipping: black ${stats.black.toFixed(1)}% · white ${stats.white.toFixed(1)}%`;
          }
          scheduleAdjRefresh(layer);
        };
        const onUp = () => {
          h.releasePointerCapture(e.pointerId);
          h.removeEventListener('pointermove', onMove);
          h.removeEventListener('pointerup', onUp);
        };
        h.addEventListener('pointermove', onMove);
        h.addEventListener('pointerup', onUp);
      });
    });
  }

  // Legacy sidebar-FX panel sync — FX now lives in a per-layer popup;
  // stubbed so any stale callers don't error.
  function syncFxPanelToActiveLayerIfPresent() { /* no-op */ }

  return {
    openFxPopup, openAdjPopup, editAdjLayer,
    closeFxPopup, closeFxMenu, closeAdjPopup,
    ensureFxDock, ensureAdjustments,
    syncFxPanelToActiveLayerIfPresent,
    minimiseAdjPopup,
  };
}
