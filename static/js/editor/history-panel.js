/**
 * History-panel subsystem — the floating frosted list of labeled
 * undo/redo entries that hangs off the topbar History button.
 *
 * Same docking pattern as the FX adjustment popups: drag the head to
 * reposition, click the minimise button to dock into the modalManager
 * chip chain, click the chip to restore. Esc closes.
 *
 * @param {{
 *   undo: () => void,
 *   redo: () => void,
 * }} deps
 *
 * @returns {{
 *   toggleHistoryPanel:        () => void,
 *   refreshHistoryPanelIfOpen: () => void,
 *   jumpToHistory:             (offset: number) => void,
 * }}
 */
import { state } from './state.js';
import modalManager from '../modalManager.js';
import { HISTORY_ICON, relTime } from './layer-helpers.js';
import { historyPanelHTML } from './build/popups.js';

export function createHistoryPanel({ undo, redo }) {
  function jumpToHistory(offset) {
    if (offset === 0) return;
    if (offset < 0) {
      for (let i = 0; i < -offset; i++) undo();
    } else {
      for (let i = 0; i < offset; i++) redo();
    }
  }

  function closeHistoryPanel() {
    if (state.historyPanelEl) {
      if (state.historyPanelEl._escHandler) {
        document.removeEventListener('keydown', state.historyPanelEl._escHandler, true);
      }
      if (state.historyPanelEl._awayHandler) {
        document.removeEventListener('pointerdown', state.historyPanelEl._awayHandler, true);
      }
      state.historyPanelEl.remove();
      state.historyPanelEl = null;
    }
  }

  function minimiseHistoryPanel() {
    if (!state.historyPanelEl) return;
    const panel = state.historyPanelEl;
    const r = panel.getBoundingClientRect();
    panel._stashLeft = r.left;
    panel._stashTop  = r.top;
    panel.style.display = 'none';
    state.historyPanelEl = null;
    const modalId = panel._modalId || 'ge-history-panel-min';
    panel._modalId = modalId;
    modalManager.register(modalId, {
      label: window.t('History'),
      icon: HISTORY_ICON,
      restoreFn: () => {
        panel.style.left = panel._stashLeft + 'px';
        panel.style.top  = panel._stashTop  + 'px';
        panel.style.display = '';
        state.historyPanelEl = panel;
        refreshHistoryPanelIfOpen();
      },
      closeFn: () => {
        panel.remove();
        modalManager.unregister(modalId);
      },
    });
    modalManager.minimize(modalId);
  }

  function toggleHistoryPanel() {
    if (state.historyPanelEl) { closeHistoryPanel(); return; }
    const panel = document.createElement('div');
    panel.id = 'ge-history-panel';
    panel.className = 'ge-frosted';
    panel.innerHTML = historyPanelHTML(HISTORY_ICON);
    document.body.appendChild(panel);
    state.historyPanelEl = panel;
    const btn = document.getElementById('ge-history-btn');
    if (btn) {
      const r = btn.getBoundingClientRect();
      panel.style.top  = (r.bottom + 6) + 'px';
      panel.style.left = Math.max(8, r.left) + 'px';
    }
    panel.querySelector('.ge-adj-min').addEventListener('click', minimiseHistoryPanel);
    // Click anywhere outside the panel (or trigger button) closes it.
    setTimeout(() => {
      const onAway = (ev) => {
        if (!state.historyPanelEl) return;
        if (state.historyPanelEl.contains(ev.target)) return;
        if (btn && (ev.target === btn || btn.contains(ev.target))) return;
        closeHistoryPanel();
        document.removeEventListener('pointerdown', onAway, true);
      };
      document.addEventListener('pointerdown', onAway, true);
      panel._awayHandler = onAway;
    }, 0);

    const head = panel.querySelector('[data-history-drag]');
    head.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button')) return;
      e.preventDefault();
      const startX = e.clientX, startY = e.clientY;
      const r0 = panel.getBoundingClientRect();
      head.setPointerCapture(e.pointerId);
      head.style.cursor = 'grabbing';
      const onMove = (ev) => {
        const nx = Math.max(0, Math.min(window.innerWidth - 60, r0.left + (ev.clientX - startX)));
        const ny = Math.max(0, Math.min(window.innerHeight - 30, r0.top  + (ev.clientY - startY)));
        panel.style.left = nx + 'px';
        panel.style.top  = ny + 'px';
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

    const onKey = (ev) => {
      if (ev.key === 'Escape') {
        ev.preventDefault();
        ev.stopPropagation();
        closeHistoryPanel();
      }
    };
    document.addEventListener('keydown', onKey, true);
    panel._escHandler = onKey;

    refreshHistoryPanelIfOpen();
  }

  function refreshHistoryPanelIfOpen() {
    if (!state.historyPanelEl) return;
    const list = state.historyPanelEl.querySelector('#ge-history-list');
    if (!list) return;
    // Chronological order — oldest at top, latest at bottom. Past
    // (undo) states first, then Current, then future (redo) states.
    const rows = [];
    for (let i = 0; i < state.undoStack.length; i++) {
      const s = state.undoStack[i];
      rows.push({ offset: -(state.undoStack.length - i), label: s._label || window.t('Edit'), ts: s._ts });
    }
    rows.push({ offset: 0, label: window.t('Current'), ts: Date.now(), current: true });
    for (let i = state.redoStack.length - 1; i >= 0; i--) {
      const s = state.redoStack[i];
      rows.push({ offset: (state.redoStack.length - i), label: s._label || window.t('Edit'), ts: s._ts, future: true });
    }
    list.innerHTML = rows.map(r => `
    <button class="ge-history-row${r.current ? ' current' : ''}${r.future ? ' future' : ''}" data-offset="${r.offset}">
      <span class="ge-history-row-dot"></span>
      <span class="ge-history-row-label">${(r.label || '').replace(/[<>&]/g,'')}</span>
      <span class="ge-history-row-time">${relTime(r.ts)}</span>
    </button>
  `).join('');
    list.querySelectorAll('.ge-history-row').forEach(btn => {
      btn.addEventListener('click', () => {
        const off = parseInt(btn.dataset.offset, 10);
        jumpToHistory(off);
      });
    });
    // Scroll the current marker into view.
    const cur = list.querySelector('.current');
    if (cur) cur.scrollIntoView({ block: 'center' });
  }

  return { toggleHistoryPanel, refreshHistoryPanelIfOpen, jumpToHistory };
}
