// Keyboard-shortcut settings: the default bindings, the capture/rebind UI and
// the persistence of customised bindings. Extracted verbatim from settings.js.

import { isAltGrEvent } from '../platform.js';
import { byId as el } from './dom.js';
import { esc } from './html.js';
import { postSettings as _postSettings } from './api.js';
import uiModule from '../ui.js?v=20260916largetoolscroll1';

const SHORTCUT_DEFAULTS = {
  search:         'ctrl+k',
  toggle_sidebar: 'ctrl+alt+b',
  new_session:    'ctrl+alt+n',
  fav_session:    'ctrl+alt+f',
  delete_session: 'ctrl+alt+d',
  cancel:         'escape',
  tts:            'alt+shift+t',
  incognito:      'ctrl+alt+i',
  settings:       'ctrl+,',
  focus_input:    'ctrl+/',
  // Open-tool shortcuts. Calendar is bound by default; the rest are
  // unbound (empty) so the user can assign their own in the panel.
  open_calendar:  'ctrl+alt+c',
  open_compare:   '',
  open_cookbook:  '',
  open_research:  '',
  open_gallery:   '',
  open_library:   '',
  open_memory:    '',
  open_skills:    '',
  open_notes:     '',
  open_tasks:     '',
  open_theme:     '',
};

const SHORTCUT_ICONS = {
  search:         '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="10" cy="10" r="7"/><path d="M21 21l-4.35-4.35"/></svg>',
  toggle_sidebar: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>',
  new_session:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  fav_session:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
  delete_session: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  cancel:         '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  tts:            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>',
  incognito:      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>',
  settings:       '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
  focus_input:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  open_calendar:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  open_compare:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="7" height="16" rx="1.5"/><rect x="14" y="4" width="7" height="16" rx="1.5"/><path d="M10 8h4"/><path d="M10 16h4"/></svg>',
  open_cookbook:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
  open_research:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>',
  open_gallery:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
  open_library:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  open_memory:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a7 7 0 0 1 7 7c0 2.4-1.2 4.5-3 5.7V17a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-2.3C6.2 13.5 5 11.4 5 9a7 7 0 0 1 7-7z"/><line x1="10" y1="22" x2="14" y2="22"/></svg>',
  open_skills:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  open_notes:     '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h10l4 4v14H5z"/><path d="M15 3v5h5"/><path d="M8 17.5 15.5 10l2.5 2.5L10.5 20H8z"/></svg>',
  open_tasks:     '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><path d="M9 16l2 2 4-4"/></svg>',
  open_theme:     '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 0 0 20 5 5 0 0 0 5-5 3 3 0 0 0-3-3h-2a3 3 0 0 1-3-3 5 5 0 0 1 5-5"/></svg>',
};

const SHORTCUT_LABELS = {
  search:         'Search conversations',
  toggle_sidebar: 'Toggle sidebar',
  new_session:    'New session',
  fav_session:    'Favorite session',
  delete_session: 'Delete session',
  cancel:         'Cancel / close',
  tts:            'Play/stop TTS',
  incognito:      'Toggle incognito',
  settings:       'Toggle Window',
  focus_input:    'Focus chat input',
  open_calendar:  'Open Calendar',
  open_compare:   'Open Compare',
  open_cookbook:  'Open Cookbook',
  open_research:  'Open Deep Research',
  open_gallery:   'Open Gallery',
  open_library:   'Open Library',
  open_memory:    'Open Memory',
  open_skills:    'Open Skills',
  open_notes:     'Open Notes',
  open_tasks:     'Open Tasks',
  open_theme:     'Open Theme',
};

const SHORTCUT_CATEGORIES = [
  { name: 'Navigation', keys: ['search', 'toggle_sidebar', 'focus_input', 'settings'] },
  { name: 'Sessions', keys: ['new_session', 'fav_session', 'delete_session'] },
  { name: 'Tools', keys: ['incognito', 'tts', 'cancel'] },
  { name: 'Open Tools', keys: ['open_calendar', 'open_compare', 'open_cookbook', 'open_research', 'open_gallery', 'open_library', 'open_memory', 'open_skills', 'open_notes', 'open_tasks', 'open_theme'] },
];

function _formatKeyCaps(combo) {
  return combo.split('+').map(p => {
    let label;
    if (p === 'ctrl') label = 'Ctrl';
    else if (p === 'alt') label = 'Alt';
    else if (p === 'shift') label = 'Shift';
    else if (p === 'meta') label = 'Cmd';
    else if (p === 'escape') label = 'Esc';
    else if (p === ',') label = ',';
    else if (p === '/') label = '/';
    else if (p === 'space') label = 'Space';
    else label = p.charAt(0).toUpperCase() + p.slice(1);
    return `<kbd>${label}</kbd>`;
  }).join('');
}

function _comboFromEvent(e) {
  // Drop a stray AltGr keystroke (e.g. AltGr+E to type €) so it isn't recorded
  // as a bogus ctrl+alt+<char> binding — onKey ignores empty combos. See
  // platform.js for the macOS carve-out and Windows trade-off.
  if (isAltGrEvent(e)) return '';
  const parts = [];
  if (e.ctrlKey || e.metaKey) parts.push('ctrl');
  if (e.altKey) parts.push('alt');
  if (e.shiftKey) parts.push('shift');
  const key = e.key.toLowerCase();
  if (!['control', 'alt', 'shift', 'meta'].includes(key)) {
    parts.push(key === ' ' ? 'space' : key);
  }
  return parts.join('+');
}

export async function initShortcuts() {
  const listEl = el('shortcuts-list');
  const resetBtn = el('shortcuts-reset-btn');
  if (!listEl) return;

  // Load saved keybinds
  let keybinds = { ...SHORTCUT_DEFAULTS };
  try {
    const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    const settings = await res.json();
    if (settings.keybinds) keybinds = { ...keybinds, ...settings.keybinds };
    if (keybinds.toggle_sidebar === 'ctrl+b') {
      keybinds.toggle_sidebar = SHORTCUT_DEFAULTS.toggle_sidebar;
    }
  } catch (e) {}

  function _findConflicts() {
    const comboMap = {};
    for (const [action, combo] of Object.entries(keybinds)) {
      if (!comboMap[combo]) comboMap[combo] = [];
      comboMap[combo].push(action);
    }
    const conflicts = new Set();
    for (const actions of Object.values(comboMap)) {
      if (actions.length > 1) actions.forEach(a => conflicts.add(a));
    }
    return conflicts;
  }

  function render() {
    listEl.innerHTML = '';
    const conflicts = _findConflicts();

    for (const cat of SHORTCUT_CATEGORIES) {
      const catHeader = document.createElement('div');
      catHeader.className = 'shortcut-category';
      catHeader.textContent = cat.name;
      listEl.appendChild(catHeader);

      for (const action of cat.keys) {
        if (!(action in keybinds)) continue;
        const combo = keybinds[action];
        // Unbound shortcuts (empty combo) still render so the user can
        // assign one \u2014 they show a "Set" affordance instead of keycaps.
        const label = SHORTCUT_LABELS[action] || action;
        const icon = SHORTCUT_ICONS[action] || '';
        const isCustom = combo !== (SHORTCUT_DEFAULTS[action] || '');
        const hasConflict = combo && conflicts.has(action);
        const row = document.createElement('div');
        row.className = 'shortcut-row' + (hasConflict ? ' shortcut-conflict' : '');
        row.dataset.action = action;
        const keyContent = combo ? _formatKeyCaps(combo) : '<span class="shortcut-unset">Set</span>';
        row.innerHTML = `
          <span class="shortcut-label"><span class="shortcut-icon">${icon}</span>${esc(label)}${hasConflict ? '<span class="shortcut-warn" title="Duplicate shortcut">!</span>' : ''}</span>
          <div class="shortcut-controls">
            <span class="shortcut-hint" hidden></span>
            <button class="shortcut-key${combo ? '' : ' shortcut-key-unset'}" data-action="${action}" title="Click to rebind">${keyContent}</button>
            <button class="shortcut-action-btn ${isCustom ? 'is-reset' : ''}" data-action="${action}" title="${isCustom ? 'Reset to default' : 'Confirm'}" style="${isCustom ? '' : 'visibility:hidden'}">
              ${isCustom
                ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>'
                : '\u2713'}
            </button>
          </div>
        `;
        listEl.appendChild(row);
      }
    }

    listEl.querySelectorAll('.shortcut-key').forEach(btn => {
      btn.addEventListener('click', () => startRebind(btn));
    });

    listEl.querySelectorAll('.shortcut-action-btn.is-reset').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.dataset.action;
        keybinds[action] = SHORTCUT_DEFAULTS[action];
        saveKeybinds();
        render();
      });
    });
  }

  function startRebind(btn) {
    const action = btn.dataset.action;
    const row = btn.closest('.shortcut-row');
    const actionBtn = row.querySelector('.shortcut-action-btn');
    const hintEl = row.querySelector('.shortcut-hint');

    // Remove any other active rebind
    listEl.querySelectorAll('.shortcut-key.listening').forEach(b => {
      b.classList.remove('listening');
      b.innerHTML = _formatKeyCaps(keybinds[b.dataset.action]);
      const otherRow = b.closest('.shortcut-row');
      const otherAction = otherRow.querySelector('.shortcut-action-btn');
      if (otherAction && !otherAction.classList.contains('is-reset')) otherAction.style.visibility = 'hidden';
    });

    btn.classList.add('listening');
    btn.textContent = 'Press keys...';
    // Show confirm button
    actionBtn.textContent = '\u2713';
    actionBtn.classList.remove('is-reset');
    actionBtn.style.visibility = 'visible';
    actionBtn.title = 'Confirm';
    // Hint: tell the user how to commit / cancel the rebind.
    if (hintEl) {
      hintEl.hidden = false;
      hintEl.textContent = 'press a key';
    }

    let pendingCombo = null;

    // Wire confirm button
    const confirmHandler = () => {
      if (pendingCombo) {
        keybinds[action] = pendingCombo;
        saveKeybinds();
      }
      cleanup();
      render();
    };
    actionBtn.addEventListener('click', confirmHandler, { once: true });

    function onKey(e) {
      e.preventDefault();
      e.stopPropagation();

      if (e.key === 'Escape') {
        cleanup();
        btn.innerHTML = _formatKeyCaps(keybinds[action]);
        const isCustom = keybinds[action] !== SHORTCUT_DEFAULTS[action];
        if (isCustom) {
          actionBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>';
          actionBtn.classList.add('is-reset');
          actionBtn.title = 'Reset to default';
        } else {
          actionBtn.style.visibility = 'hidden';
        }
        return;
      }

      // Enter commits the previewed combo (same as clicking \u2713). Only acts
      // as commit once a combo has been captured \u2014 otherwise it would just
      // try to bind Enter itself.
      if (e.key === 'Enter' && pendingCombo) {
        confirmHandler();
        return;
      }

      const combo = _comboFromEvent(e);
      if (!combo || combo === 'ctrl' || combo === 'alt' || combo === 'shift' || combo === 'ctrl+alt' || combo === 'ctrl+shift' || combo === 'alt+shift' || combo === 'ctrl+alt+shift') return;

      // Preview the combo, wait for confirm
      pendingCombo = combo;
      btn.innerHTML = _formatKeyCaps(combo);
      // Now that a combo is captured, prompt to commit with Enter.
      if (hintEl) hintEl.textContent = '\u21B5 Enter to save';
    }

    function cleanup() {
      btn.classList.remove('listening');
      if (hintEl) { hintEl.hidden = true; hintEl.textContent = ''; }
      document.removeEventListener('keydown', onKey, true);
      actionBtn.removeEventListener('click', confirmHandler);
    }

    document.addEventListener('keydown', onKey, true);
  }

  async function saveKeybinds() {
    try {
      await _postSettings({ keybinds });
      // Update global keybinds so they take effect immediately
      window._odysseusKeybinds = keybinds;
      if (uiModule && uiModule.showToast) uiModule.showToast('Shortcut saved');
    } catch (e) {
      console.error('Failed to save keybinds:', e);
      if (uiModule && uiModule.showToast) uiModule.showToast('Failed to save shortcut');
    }
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', async () => {
      keybinds = { ...SHORTCUT_DEFAULTS };
      render();
      await saveKeybinds();
      if (uiModule && uiModule.showToast) uiModule.showToast('Shortcuts reset to defaults');
    });
  }

  render();
}
