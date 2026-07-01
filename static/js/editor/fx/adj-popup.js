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
  defaultAdjParams,
} from '../layer-helpers.js';
import { drawHistogram } from './histogram.js';

export function createAdjPopupSystem({ composite, saveState, renderLayerPanel }) {
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
      { type: 'brightness-contrast', label: window.t('Brightness / Contrast') },
      { type: 'hue-saturation',      label: window.t('Hue / Saturation') },
      { type: 'levels',              label: window.t('Levels') },
      { type: 'color-balance',       label: window.t('Color Balance') },
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
        if (layer._stagedAdj) layer._stagedAdj = null;
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
    const editing = !!existingAdj;
    const startParams = editing
      ? JSON.parse(JSON.stringify(existingAdj.params))
      : defaultAdjParams(type);
    layer._stagedAdj = { type, params: startParams };
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
        <button class="ge-adj-min" type="button" title="${window.t('Minimise')}">&minus;</button>
      </span>
    </div>
    <div class="ge-adj-body" data-adj-body></div>
    <div class="ge-adj-foot">
      <button class="ge-btn ge-btn-sm ge-adj-cancel-btn" data-adj-action="cancel">${window.t('Cancel')}</button>
      <button class="ge-btn ge-btn-sm ge-btn-primary ge-adj-apply-btn" data-adj-action="ok">${window.t('Apply')}</button>
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
    pop.querySelector('[data-adj-action="ok"]')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      suppressLayerGhostTap();
      saveState(editing ? window.t('Edit {label}', { label: adjLayerLabel(type) }) : window.t('Add {label}', { label: adjLayerLabel(type) }));
      const params = layer._stagedAdj.params;
      layer._stagedAdj = null;
      if (editing) {
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
      layer._adjFinalKey = null;
      composite();
    });
  }

  function buildAdjBody(layer, type, body, popEl) {
    const p = layer._stagedAdj.params;
    const revertIcon = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>';
    const sliderRow = (key, label, min, max, value, suffix) => `
      <div class="ge-adj-row" data-adj-key="${key}">
        <label>${label}</label>
        <input type="range" min="${min}" max="${max}" value="${value}" data-key="${key}" />
        <span class="ge-adj-value">${value}${suffix || ''}</span>
        <button class="ge-adj-revert" type="button" title="${window.t('Reset this slider')}" data-revert-key="${key}">${revertIcon}</button>
      </div>
    `;
    if (type === 'brightness-contrast') {
      const bSlider = Math.round((p.brightness - 1) * 100);
      const cSlider = Math.round((p.contrast - 1) * 100);
      body.innerHTML = `
      ${sliderRow('brightness', window.t('Brightness'), -100, 100, bSlider, '')}
      ${sliderRow('contrast',   window.t('Contrast'),   -100, 100, cSlider, '')}
    `;
    } else if (type === 'hue-saturation') {
      const hSlider = Math.round(p.hue);
      const sSlider = Math.round((p.saturation - 1) * 100);
      body.innerHTML = `
      ${sliderRow('hue',        window.t('Hue'),        -180, 180, hSlider, ' °')}
      ${sliderRow('saturation', window.t('Saturation'), -100, 100, sSlider, '')}
    `;
    } else if (type === 'levels') {
      // Histogram canvas + sliders. Histogram is computed from the
      // layer's pixel data (after any adjLayers below this one) so
      // the user is matching levels against what they're really seeing.
      // <details> wrapper is collapsed by default on mobile to save
      // vertical space; open by default on desktop.
      const isMobile = window.matchMedia('(max-width: 820px)').matches;
      body.innerHTML = `
      <details class="ge-adj-hist-details"${isMobile ? '' : ' open'}>
        <summary>${window.t('Histogram')}</summary>
        <div class="ge-adj-hist-wrap">
          <canvas class="ge-adj-histogram" width="280" height="80"></canvas>
          <div class="ge-adj-hist-handles">
            <div class="ge-adj-hist-handle hist-h-black"  data-handle="inBlack"  title="${window.t('Input black — drag')}"></div>
            <div class="ge-adj-hist-handle hist-h-gamma"  data-handle="gamma"    title="${window.t('Gamma — drag')}"></div>
            <div class="ge-adj-hist-handle hist-h-white"  data-handle="inWhite"  title="${window.t('Input white — drag')}"></div>
          </div>
        </div>
      </details>
      ${sliderRow('inBlack',  window.t('Input black'),  0, 254, p.inBlack, '')}
      ${sliderRow('inWhite',  window.t('Input white'),  1, 255, p.inWhite, '')}
      ${sliderRow('gamma',    window.t('Gamma'),        10, 990, Math.round((p.gamma || 1) * 100), 'γ')}
      ${sliderRow('outBlack', window.t('Output black'), 0, 255, p.outBlack, '')}
      ${sliderRow('outWhite', window.t('Output white'), 0, 255, p.outWhite, '')}
    `;
      const hist = body.querySelector('.ge-adj-histogram');
      drawHistogram(hist, layer);
      wireHistogramHandles(body, layer, type);
      // Redraw histogram when the user opens the disclosure (canvas
      // dimensions are layout-dependent).
      body.querySelector('.ge-adj-hist-details')?.addEventListener('toggle', (e) => {
        if (e.target.open) drawHistogram(hist, layer);
      });
    } else if (type === 'color-balance') {
      // Color-tinted slider ends so the user sees what direction does what.
      const cbRow = (key, leftCol, rightCol, label, value) => `
      <div class="ge-adj-row ge-adj-cb-row" data-adj-key="${key}">
        <span class="ge-adj-cb-dot" style="background:${leftCol}"></span>
        <input type="range" min="-100" max="100" value="${value}" data-key="${key}" />
        <span class="ge-adj-cb-dot" style="background:${rightCol}"></span>
        <span class="ge-adj-value">${value}</span>
        <button class="ge-adj-revert" type="button" title="${window.t('Reset this slider')}" data-revert-key="${key}">${revertIcon}</button>
      </div>
    `;
      // Tone picker: one tone group visible at a time. Remember the
      // last picked tone on the popup so re-renders (revert button
      // etc.) keep it.
      const tone = popEl._cbTone || 'shadows';
      popEl._cbTone = tone;
      const toneSliders = (t) => `
      ${cbRow(`${t}-r`, '#00d2d2', '#ff5555', window.t('Cyan ↔ Red'),      p[t].r)}
      ${cbRow(`${t}-g`, '#d855d8', '#55d855', window.t('Magenta ↔ Green'), p[t].g)}
      ${cbRow(`${t}-b`, '#e6e64a', '#4a78ff', window.t('Yellow ↔ Blue'),   p[t].b)}
    `;
      body.innerHTML = `
      <div class="ge-adj-cb-tone-picker">
        <select class="ge-adj-cb-tone-select">
          <option value="shadows"${tone === 'shadows' ? ' selected' : ''}>${window.t('Shadows')}</option>
          <option value="midtones"${tone === 'midtones' ? ' selected' : ''}>${window.t('Midtones')}</option>
          <option value="highlights"${tone === 'highlights' ? ' selected' : ''}>${window.t('Highlights')}</option>
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
    if (type === 'brightness-contrast' || type === 'hue-saturation') {
      p[key] = defaults[key];
    } else if (type === 'levels') {
      p[key] = defaults[key];
    } else if (type === 'color-balance') {
      const [tone, ch] = key.split('-');
      p[tone][ch] = defaults[tone][ch];
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
      }
    } else if (type === 'levels') {
      if (key === 'gamma') {
        p.gamma = raw / 100; display = (raw / 100).toFixed(2) + 'γ';
      } else {
        p[key] = raw;
      }
    } else if (type === 'color-balance') {
      const [tone, ch] = key.split('-');
      p[tone][ch] = raw;
    }
    if (valEl) valEl.textContent = display;
    scheduleAdjRefresh(layer);
  }

  // Position the three histogram triangle handles by current staged
  // values + wire pointer drags.
  function wireHistogramHandles(bodyEl, layer, type) {
    const wrap = bodyEl.querySelector('.ge-adj-hist-wrap');
    const canvas = bodyEl.querySelector('.ge-adj-histogram');
    if (!wrap || !canvas) return;
    const handles = bodyEl.querySelectorAll('.ge-adj-hist-handle');
    const placeHandles = () => {
      const w = canvas.getBoundingClientRect().width;
      const p = layer._stagedAdj.params;
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
          const p = layer._stagedAdj.params;
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
            if (sl) sl.value = String(key === 'gamma' ? Math.round(layer._stagedAdj.params.gamma * 100) : layer._stagedAdj.params[key]);
            const val = sl?.parentElement.querySelector('.ge-adj-value');
            if (val) val.textContent = displayVal;
          };
          if (which === 'inBlack') updateRow('inBlack', String(layer._stagedAdj.params.inBlack));
          if (which === 'inWhite') updateRow('inWhite', String(layer._stagedAdj.params.inWhite));
          if (which === 'gamma')   updateRow('gamma',   layer._stagedAdj.params.gamma.toFixed(2) + 'γ');
          drawHistogram(canvas, layer);
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
