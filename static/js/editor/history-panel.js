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
 *   takeSnapshot?:        (label?: string) => ({ id: string, label: string } | void),
 *   restoreSnapshot?:     (id: string) => void,
 *   deleteSnapshot?:      (id: string) => void,
 *   listSnapshots?:       () => ({ id: string, label: string, ts: number }[]),
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

export function createHistoryPanel({ undo, redo, takeSnapshot, restoreSnapshot, deleteSnapshot, listSnapshots }) {
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
      label: 'History',
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
    // Fill in the snapshot + timeline content FIRST so the panel has its real
    // height before we measure it for on-screen clamping below.
    refreshHistoryPanelIfOpen();
    const btn = document.getElementById('ge-history-btn');
    if (btn) {
      const r = btn.getBoundingClientRect();
      panel.style.left = Math.max(8, r.left) + 'px';
      panel.style.top  = (r.bottom + 6) + 'px';
      // Anchor under the button, but if the panel would run off the bottom of
      // the viewport (the History button lives in the footer) flip it above the
      // button and clamp both edges so it is always fully visible.
      const ph = panel.offsetHeight || 200;
      const pw = panel.offsetWidth || 240;
      let top  = r.bottom + 6;
      let left = Math.max(8, Math.min(r.left, window.innerWidth - pw - 8));
      if (top + ph > window.innerHeight - 8) {
        top = Math.max(8, r.top - ph - 6);
        if (top < 8) top = Math.max(8, window.innerHeight - ph - 8);
      }
      panel.style.top  = top + 'px';
      panel.style.left = left + 'px';
    }
    panel.querySelector('.ge-adj-min').addEventListener('click', minimiseHistoryPanel);
    // Click anywhere outside the panel (or trigger button) closes it.
    setTimeout(() => {
      const onAway = (ev) => {
        if (!state.historyPanelEl) return;
        if (state.historyPanelEl.contains(ev.target)) return;
        if (btn && (ev.target === btn || btn.contains(ev.target))) return;
        // A click inside a topbar dropdown (Window/Edit > History) is an
        // explicit toggle, not an "away" click — don't close on its
        // pointerdown or the row's click would immediately reopen it.
        if (ev.target.closest && ev.target.closest('.dropdown')) return;
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
  }

  // Render the chronological timeline (oldest/past -> Current -> future) into
  // any list element. Shared by the floating panel and the docked History
  // panel, matching Photoshop's docked History timeline.
  function renderHistoryList(list) {
    if (!list) return;
    const rows = [];
    for (let i = 0; i < state.undoStack.length; i++) {
      const s = state.undoStack[i];
      rows.push({ offset: -(state.undoStack.length - i), label: s._label || 'Edit', ts: s._ts });
    }
    rows.push({ offset: 0, label: 'Current', ts: Date.now(), current: true });
    for (let i = state.redoStack.length - 1; i >= 0; i--) {
      const s = state.redoStack[i];
      rows.push({ offset: (state.redoStack.length - i), label: s._label || 'Edit', ts: s._ts, future: true });
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
    // Reveal the current marker without disturbing the surrounding chrome.
    // Element.scrollIntoView() bubbles up and scrolls EVERY scrollable
    // ancestor, which yanked the whole right-hand dock down to the History
    // group on load (hiding the Color/Properties tab strips). Instead scroll
    // only the list itself, or the dock group's own panel when the list is
    // not the scroller — so the docked History auto-reveals the current step
    // while the tab strips above stay put.
    const cur = list.querySelector('.current');
    if (cur) {
      const scroller = list.scrollHeight > list.clientHeight + 1
        ? list
        : list.closest('.ge-dock-panels');
      if (scroller) {
        const c = cur.getBoundingClientRect();
        const s = scroller.getBoundingClientRect();
        if (c.bottom > s.bottom) scroller.scrollTop += (c.bottom - s.bottom) + 8;
        else if (c.top < s.top) scroller.scrollTop -= (s.top - c.top) + 8;
      }
    }
  }

  // Named snapshots (Photoshop-style restore points) section — a camera
  // button plus one row per snapshot. Rendered above the timeline in both
  // the floating panel and the docked History panel.
  const SNAPSHOT_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>';
  function renderSnapshots(container) {
    if (!container) return;
    const snaps = (typeof listSnapshots === 'function') ? (listSnapshots() || []) : [];
    const rows = snaps.map(s => `
    <div class="ge-history-snap-row" data-snap-id="${s.id}">
      <button class="ge-history-snap-restore" type="button" data-snap-id="${s.id}" title="Restore this snapshot">
        <span class="ge-history-snap-dot"></span>
        <span class="ge-history-row-label">${(s.label || 'Snapshot').replace(/[<>&]/g, '')}</span>
      </button>
      <span class="ge-history-row-time">${relTime(s.ts)}</span>
      <button class="ge-history-snap-del" type="button" data-snap-id="${s.id}" title="Delete snapshot" aria-label="Delete snapshot">&times;</button>
    </div>`).join('');
    container.innerHTML = `
    <div class="ge-history-snap-head">
      <span class="ge-history-snap-title">Snapshots</span>
      <button class="ge-history-snap-new" type="button" title="Create a new snapshot">${SNAPSHOT_ICON}</button>
    </div>
    <div class="ge-history-snap-rows">${rows || '<div class="ge-history-snap-empty">No snapshots yet</div>'}</div>
  `;
    container.querySelector('.ge-history-snap-new')?.addEventListener('click', () => {
      if (typeof takeSnapshot !== 'function') return;
      const n = ((typeof listSnapshots === 'function' && listSnapshots()) || []).length + 1;
      const label = window.prompt('Snapshot name', 'Snapshot ' + n);
      if (label == null) return;
      takeSnapshot(label.trim() || ('Snapshot ' + n));
    });
    container.querySelectorAll('.ge-history-snap-restore').forEach(btn => {
      btn.addEventListener('click', () => { if (typeof restoreSnapshot === 'function') restoreSnapshot(btn.dataset.snapId); });
    });
    container.querySelectorAll('.ge-history-snap-del').forEach(btn => {
      btn.addEventListener('click', () => { if (typeof deleteSnapshot === 'function') deleteSnapshot(btn.dataset.snapId); });
    });
  }

  // Refresh the floating panel (if open) and the docked History panel (if
  // present) — every undo/redo/edit already calls this.
  function refreshHistoryPanelIfOpen() {
    if (state.historyPanelEl) {
      renderSnapshots(state.historyPanelEl.querySelector('#ge-history-snapshots'));
      renderHistoryList(state.historyPanelEl.querySelector('#ge-history-list'));
    }
    const dockList = document.getElementById('ge-dock-history-list');
    if (dockList) {
      let dockSnaps = document.getElementById('ge-dock-history-snapshots');
      if (!dockSnaps) {
        dockSnaps = document.createElement('div');
        dockSnaps.id = 'ge-dock-history-snapshots';
        dockSnaps.className = 'ge-history-snapshots';
        dockList.parentNode?.insertBefore(dockSnaps, dockList);
      }
      renderSnapshots(dockSnaps);
      renderHistoryList(dockList);
    }
  }

  return { toggleHistoryPanel, refreshHistoryPanelIfOpen, renderHistoryList, renderSnapshots, jumpToHistory };
}
