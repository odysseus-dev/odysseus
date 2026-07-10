// ============================================
// Keyboard Shortcuts — dynamic keybinds
// ============================================

import { adjustUiScale, setUiScale } from './theme.js';
import { registerMenuDismiss } from './escMenuStack.js';
import { _eventShortcutKey, _matchesCombo } from './keybindMatcher.js';

export { _matchesCombo } from './keybindMatcher.js';

const _defaultKeybinds = {
  search: 'ctrl+k', toggle_sidebar: 'ctrl+alt+b', new_session: 'ctrl+alt+n',
  fav_session: 'ctrl+alt+f', delete_session: 'ctrl+alt+d',
  cancel: 'escape', tts: 'alt+shift+t',
  incognito: 'ctrl+alt+i', settings: 'ctrl+,', focus_input: 'ctrl+/',
  // Open-tool shortcuts (Calendar bound by default; rest unbound).
  open_calendar: 'ctrl+alt+c', open_compare: '', open_cookbook: '',
  open_research: '', open_gallery: '', open_library: '', open_memory: '',
  open_notes: '', open_tasks: '', open_theme: '',
};

function _uiScaleShortcut(e) {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return null;
  const key = _eventShortcutKey(e);
  const code = String(e.code || '');
  if (key === '+' || key === '=' || code === 'Equal' || code === 'NumpadAdd') return 'in';
  if (key === '-' || code === 'Minus' || code === 'NumpadSubtract') return 'out';
  if (key === '0' || code === 'Digit0' || code === 'Numpad0') return 'reset';
  return null;
}

/**
 * Initialize keyboard shortcuts.
 * @param {Object} modules - References to app modules and helpers
 * @param {Function} modules.el - Element lookup helper (uiModule.el)
 * @param {Object} modules.Storage - Storage module
 * @param {Object} modules.sessionModule
 * @param {Object} modules.uiModule
 * @param {Object} modules.chatModule
 * @param {Object} modules.adminModule
 * @param {Object} modules.settingsModule
 * @param {Object} modules.searchChatModule
 * @param {Function} modules._closeCompareIfActive
 * @param {Function} modules._deactivateIncognito
 * @param {string} modules.API_BASE
 */
export function initKeyboardShortcuts(modules) {
  const {
    el, Storage, sessionModule, uiModule, chatModule,
    adminModule, settingsModule, searchChatModule,
    _closeCompareIfActive, _deactivateIncognito, API_BASE
  } = modules;

  window._odysseusKeybinds = { ..._defaultKeybinds };

  // Load saved keybinds
  fetch('/api/auth/settings', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(s => { if (s.keybinds) window._odysseusKeybinds = { ..._defaultKeybinds, ...s.keybinds }; })
    .catch(() => {});

  // ── Esc cancels select mode (capture phase, before modal-close) ──
  // Every tool's bulk-select bar has a `*-bulk-cancel` button whose click
  // already runs the correct teardown (clears selection, hides the bar,
  // re-renders). So a single global handler that clicks whichever cancel
  // button is currently visible covers all of them — notes, skills,
  // memory, gallery, sessions, doc library (chats/archive/research/docs),
  // email, cookbook serve — without each module wiring its own listener.
  // Capture phase + stopPropagation so Esc cancels select instead of
  // closing the surrounding modal.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const cancels = document.querySelectorAll('[id$="-bulk-cancel"]');
    for (const btn of cancels) {
      // Do not rely on offsetParent: visible fixed-position or modal-contained
      // controls can report null. Check the rendered box and hidden ancestors.
      const visible = (() => {
        if (btn.disabled || btn.closest('.hidden,[hidden]')) return false;
        const cs = getComputedStyle(btn);
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
        return btn.offsetWidth > 0 || btn.offsetHeight > 0 || btn.getClientRects().length > 0;
      })();
      if (visible) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        btn.click();
        return;
      }
    }
  }, true);

  // ── "Toggle Window" — close whatever tool window is open, or reopen the
  // last one. Maps each window's modal element to the button/title that
  // opens it (mirrors modalManager's _AUTO_WIRE, plus email's section title).
  const _WINDOW_TRIGGERS = {
    'settings-modal':         'user-bar-settings',
    'theme-modal':            'tool-theme-btn',
    'tasks-modal':            'tool-tasks-btn',
    'notes-panel':            'tool-notes-btn',
    'memory-modal':           'tool-memory-btn',
    'doclib-modal':           'tool-library-btn',
    'gallery-modal':          'tool-gallery-btn',
    'research-overlay':       'tool-research-btn',
    'cookbook-modal':         'tool-cookbook-btn',
    'compare-model-overlay':  'tool-compare-btn',
    'calendar-modal':         'tool-calendar-btn',
    'email-lib-modal':        'email-section-title',
  };
  let _lastWindow = 'settings-modal';

  const _windowVisible = (id) => {
    const m = document.getElementById(id);
    if (!m || m.classList.contains('hidden')) return false;
    const cs = getComputedStyle(m);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    return m.offsetWidth > 0 || m.offsetHeight > 0 || m.getClientRects().length > 0;
  };

  const _toggleActiveWindow = () => {
    // Close the first open window (remembering it), else reopen the last one.
    let openId = null;
    for (const id in _WINDOW_TRIGGERS) {
      if (_windowVisible(id)) { openId = id; break; }
    }
    if (openId) {
      _lastWindow = openId;
      const m = document.getElementById(openId);
      const closeBtn = m && m.querySelector('.close-btn, .modal-close, [data-close]');
      if (closeBtn) closeBtn.click();
      else if (openId === 'settings-modal' && settingsModule) settingsModule.close();
      else { const t = el(_WINDOW_TRIGGERS[openId]); if (t) t.click(); }
    } else if (_lastWindow === 'settings-modal') {
      if (settingsModule) settingsModule.open();
    } else {
      const t = el(_WINDOW_TRIGGERS[_lastWindow]);
      if (t) t.click();
      else if (settingsModule) settingsModule.open();
    }
  };

  document.addEventListener('keydown', (e) => {
    const kb = window._odysseusKeybinds;
    const uiScaleAction = _uiScaleShortcut(e);
    if (uiScaleAction) {
      e.preventDefault();
      e.stopImmediatePropagation();
      const scale = uiScaleAction === 'reset'
        ? setUiScale('100')
        : adjustUiScale(uiScaleAction === 'in' ? 1 : -1);
      uiModule?.showToast?.(`UI size ${scale}%`);
      return;
    }

    if (_matchesCombo(e, kb.search)) {
      e.preventDefault();
      if (searchChatModule) {
        searchChatModule.isOpen() ? searchChatModule.closeSearch() : searchChatModule.openSearch();
      }
      return;
    }
    if (_matchesCombo(e, kb.toggle_sidebar)) {
      e.preventDefault();
      var sb = document.getElementById('sidebar');
      var ir = document.getElementById('icon-rail');
      if (sb && !sb.classList.contains('hidden')) {
        sb.classList.add('hidden');
      } else {
        if (ir) ir.classList.remove('rail-hidden');
        if (sb) sb.classList.remove('hidden');
      }
      if (typeof syncRailSide === 'function') syncRailSide();
      return;
    }
    if (_matchesCombo(e, kb.tts)) {
      e.preventDefault();
      var mgr = window.aiTTSManager;
      if (!mgr || !mgr.available) return;
      if (mgr.isPlaying || mgr._processing) { mgr.stop(); return; }
      var allAI = document.querySelectorAll('#chat-history .msg-ai');
      for (var i = allAI.length - 1; i >= 0; i--) {
        var ttsBtn = allAI[i].querySelector('.ai-tts-button');
        if (ttsBtn) { ttsBtn.click(); return; }
      }
      return;
    }
    if (_matchesCombo(e, kb.fav_session)) {
      e.preventDefault();
      const sid = sessionModule && sessionModule.getCurrentSessionId();
      if (!sid) return;
      const s = sessionModule.getSessions().find(x => x.id === sid);
      if (!s) return;
      const newVal = !s.is_important;
      const fd = new FormData();
      fd.append('important', newVal);
      fetch(`${API_BASE}/api/session/${sid}/important`, { method: 'POST', body: fd });
      s.is_important = newVal;
      sessionModule.renderSessionList();
      uiModule.showToast(newVal ? 'Session favorited' : 'Session unfavorited');
      return;
    }
    if (_matchesCombo(e, kb.delete_session)) {
      e.preventDefault();
      const sid = sessionModule && sessionModule.getCurrentSessionId();
      if (!sid) return;
      const s = sessionModule.getSessions().find(x => x.id === sid);
      if (!s) return;
      if (s.is_important) { uiModule.showToast('Unstar before deleting'); return; }
      uiModule.styledConfirm('Delete this session?', { confirmText: 'Delete', danger: true }).then(ok => {
        if (!ok) return;
        const allSessions = sessionModule.getSessions();
        const idx = allSessions.findIndex(x => x.id === sid);
        const nextSession = allSessions.filter(x => !x.archived && x.id !== sid)[Math.max(0, idx)] ||
                            allSessions.find(x => !x.archived && x.id !== sid);
        fetch(`${API_BASE}/api/session/${sid}`, { method: 'DELETE' }).then(async () => {
          await sessionModule.loadSessions();
          if (nextSession) {
            await sessionModule.selectSession(nextSession.id);
          } else {
            sessionModule.setCurrentSessionId(null);
            el('chat-history').innerHTML = '';
            el('current-meta').textContent = 'Odysseus Chat';
            Storage.remove('lastSessionId');
            if (chatModule && chatModule.showWelcomeScreen) chatModule.showWelcomeScreen();
          }
        });
      });
      return;
    }
    if (_matchesCombo(e, kb.new_session)) {
      e.preventDefault();
      if (_closeCompareIfActive()) return;
      _deactivateIncognito();
      const sid = sessionModule && sessionModule.getCurrentSessionId();
      const sessions = sessionModule ? sessionModule.getSessions() : [];
      const cur = sessions.find(s => s.id === sid);
      const name = new Date().toLocaleTimeString();
      const fd = new FormData();
      fd.append('name', name);
      fd.append('endpoint_url', cur ? cur.endpoint_url || '' : '');
      fd.append('model', cur ? cur.model || '' : '');
      if (cur && cur.endpoint_id) fd.append('endpoint_id', cur.endpoint_id);
      fd.append('skip_validation', 'true');
      fetch(`${API_BASE}/api/session`, { method: 'POST', body: fd, credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : null)
        .then(async data => {
          if (data) {
            await sessionModule.loadSessions();
            await sessionModule.selectSession(data.id);
          }
        });
      return;
    }
    if (_matchesCombo(e, kb.cancel)) {
      if (chatModule) chatModule.abortCurrentRequest();
    }
    if (_matchesCombo(e, kb.incognito)) {
      e.preventDefault();
      // Drive the visible button so the real toggle logic runs (visual
      // state, welcome-screen guard, checkbox sync) — flipping the hidden
      // checkbox alone did nothing.
      const btn = el('incognito-btn');
      if (btn) btn.click();
      return;
    }
    if (_matchesCombo(e, kb.settings)) {
      e.preventDefault();
      _toggleActiveWindow();
      return;
    }
    // Open-tool shortcuts — click the sidebar tool button so each tool's
    // own open/toggle logic runs. Unbound (empty) combos never match.
    const _toolBtns = {
      open_calendar: 'tool-calendar-btn',
      open_compare:  'tool-compare-btn',
      open_cookbook: 'tool-cookbook-btn',
      open_research: 'tool-research-btn',
      open_gallery:  'tool-gallery-btn',
      open_library:  'tool-library-btn',
      open_memory:   'tool-memory-btn',
      open_notes:    'tool-notes-btn',
      open_tasks:    'tool-tasks-btn',
      open_theme:    'tool-theme-btn',
    };
    for (const action in _toolBtns) {
      if (_matchesCombo(e, kb[action])) {
        e.preventDefault();
        const b = el(_toolBtns[action]);
        if (b) b.click();
        return;
      }
    }
    if (_matchesCombo(e, kb.focus_input)) {
      e.preventDefault();
      const inp = el('message');
      if (inp) inp.focus();
      return;
    }
    // ── Keyboard shortcut cheatsheet (press ? when no input is focused) ──
    if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const tag = document.activeElement?.tagName?.toLowerCase();
      const editable = document.activeElement?.isContentEditable || document.activeElement?.getAttribute?.('contenteditable') === 'true';
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || editable) return; // don't steal from text fields
      e.preventDefault();
      _toggleCheatsheet();
      return;
    }
  });

  // ── Cheatsheet overlay ──
  let _cheatsheetUnregister = null;
  let _cheatsheetPreviousFocus = null;

  function _buildCheatsheetModal() {
    // Rebuild on every open so changes made in Keyboard Shortcuts settings are
    // reflected immediately instead of leaving a stale cached cheatsheet.
    document.getElementById('kb-cheatsheet-overlay')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'kb-cheatsheet-overlay';
    overlay.className = 'kb-cheatsheet-overlay hidden';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Keyboard shortcuts');

    const kb = window._odysseusKeybinds || _defaultKeybinds;
    const groups = [
      { title: 'Navigation', items: [
        ['Search conversations', kb.search],
        ['Toggle sidebar', kb.toggle_sidebar],
        ['Focus chat input', kb.focus_input],
        ['Toggle tool window', kb.settings],
      ]},
      { title: 'Sessions', items: [
        ['New session', kb.new_session],
        ['Favorite session', kb.fav_session],
        ['Delete session', kb.delete_session],
        ['Toggle incognito', kb.incognito],
      ]},
      { title: 'Tools', items: [
        ['Calendar', kb.open_calendar],
        ['Compare models', kb.open_compare],
        ['Cookbook', kb.open_cookbook],
        ['Research', kb.open_research],
        ['Gallery', kb.open_gallery],
        ['Library', kb.open_library],
        ['Memory', kb.open_memory],
        ['Notes', kb.open_notes],
        ['Tasks', kb.open_tasks],
        ['Theme', kb.open_theme],
      ]},
      { title: 'Other', items: [
        ['Cancel / abort', kb.cancel],
        ['Text-to-speech', kb.tts],
        ['UI zoom in', 'Ctrl++'],
        ['UI zoom out', 'Ctrl+−'],
        ['UI zoom reset', 'Ctrl+0'],
      ]},
    ];

    const formatCombo = (combo) => {
      if (!combo) return '—';
      return String(combo).replace(/ctrl\+alt\+/gi, 'Ctrl+Alt+')
        .replace(/ctrl\+/gi, 'Ctrl+')
        .replace(/alt\+/gi, 'Alt+')
        .replace(/shift\+/gi, 'Shift+')
        .replace(/escape/gi, 'Esc')
        .replace(/\b(\w)/g, (_, c) => c.toUpperCase());
    };
    const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);

    let html = '<div class="kb-cheatsheet-backdrop"></div>';
    html += '<div class="kb-cheatsheet-modal">';
    html += '<div class="kb-cheatsheet-header">';
    html += '<h2>Keyboard Shortcuts</h2>';
    html += '<button type="button" class="kb-cheatsheet-close" aria-label="Close">×</button>';
    html += '</div>';
    html += '<div class="kb-cheatsheet-body">';

    for (const grp of groups) {
      const visible = grp.items.filter(([, combo]) => combo);
      if (!visible.length) continue;
      html += `<div class="kb-cheatsheet-group"><h3>${escapeHtml(grp.title)}</h3>`;
      for (const [label, combo] of visible) {
        html += `<div class="kb-cheatsheet-row"><span class="kb-cheatsheet-label">${escapeHtml(label)}</span><kbd>${escapeHtml(formatCombo(combo))}</kbd></div>`;
      }
      html += '</div>';
    }

    html += '</div>';
    html += '<div class="kb-cheatsheet-footer">Press <kbd>?</kbd> or <kbd>Esc</kbd> to close</div>';
    html += '</div>';
    overlay.innerHTML = html;

    overlay.querySelector('.kb-cheatsheet-backdrop').addEventListener('click', _closeCheatsheet);
    overlay.querySelector('.kb-cheatsheet-close').addEventListener('click', _closeCheatsheet);
    overlay.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') return;
      const focusable = Array.from(overlay.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    document.body.appendChild(overlay);
    return overlay;
  }

  function _closeCheatsheet() {
    const overlay = document.getElementById('kb-cheatsheet-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;
    const unregister = _cheatsheetUnregister;
    _cheatsheetUnregister = null;
    unregister?.();
    overlay.classList.add('hidden');
    if (_cheatsheetPreviousFocus?.isConnected) {
      _cheatsheetPreviousFocus.focus();
    }
    _cheatsheetPreviousFocus = null;
  }

  function _toggleCheatsheet() {
    const existing = document.getElementById('kb-cheatsheet-overlay');
    if (existing && !existing.classList.contains('hidden')) {
      _closeCheatsheet();
      return;
    }
    const overlay = _buildCheatsheetModal();
    _cheatsheetPreviousFocus = document.activeElement;
    overlay.classList.remove('hidden');
    _cheatsheetUnregister = registerMenuDismiss(_closeCheatsheet);
    overlay.querySelector('.kb-cheatsheet-close')?.focus();
  }
}
