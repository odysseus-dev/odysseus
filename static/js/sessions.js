// Session Management Functions
// This module handles all session-related operations

import Storage from './storage.js';
import uiModule, { styledPrompt } from './ui.js';
import markdownModule from './markdown.js';
import chatRenderer from './chatRenderer.js';
import { providerLogo } from './providers.js';
import { initModelPicker, updateModelPicker } from './modelPicker.js';
import themeModule from './theme.js';
import spinnerModule from './spinner.js';

const API_BASE = window.location.origin;

let sessions = [];
let currentSessionId = null;
let _sessionNavToken = 0;
let _skipAutoSelect = false;

const SIDEBAR_MAX_VISIBLE = 10;
const FOLDER_MAX_VISIBLE = 5;
let _showAllSessions = false;
let _expandedFolders = {};  // folderName -> true if "show more" clicked
let _sortMode = Storage.get('odysseus-session-sort') || 'active'; // default to last active
let _autoCreateInProgress = false; // guard against recursive auto-create
const _INCOGNITO_SESSIONS_KEY = 'ody-incognito-sessions'; // sessionStorage key for incognito session IDs
const _isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const _mod = _isMac ? '⌘' : 'Ctrl';

function _getIncognitoIds() {
  try { return JSON.parse(sessionStorage.getItem(_INCOGNITO_SESSIONS_KEY) || '[]'); } catch { return []; }
}
function _markIncognito(sid) {
  const ids = _getIncognitoIds();
  if (!ids.includes(sid)) { ids.push(sid); sessionStorage.setItem(_INCOGNITO_SESSIONS_KEY, JSON.stringify(ids)); }
}
function _isIncognitoSession(sid) { return _getIncognitoIds().includes(sid); }
async function _cleanupIncognitoSessions() {
  const ids = _getIncognitoIds();
  if (ids.length === 0) return;
  // Keep the current active incognito session alive, delete the rest
  const toDelete = ids.filter(sid => sid !== currentSessionId);
  if (toDelete.length === 0) return;
  const keep = ids.filter(sid => sid === currentSessionId);
  sessionStorage.setItem(_INCOGNITO_SESSIONS_KEY, JSON.stringify(keep));
  await Promise.all(toDelete.map(sid =>
    fetch(`${API_BASE}/api/session/${sid}`, { method: 'DELETE' }).catch(() => {})
  ));
}

// Research indicator tracking
const _researchingSessions = new Set();
const _streamingSessions = new Set();   // Background chat streams (not polled against research API)
const _completedSessions = new Set();   // Sessions with completed background streams
let _researchPollTimer = null;

// Session list keyboard navigation state
let _sessionListFocused = false;

/** Clear current session from UI (after delete/archive). */
function _deselectCurrentSession(sid) {
  if (currentSessionId !== sid) return;
  currentSessionId = null;
  uiModule.el('chat-history').innerHTML = '';
  uiModule.el('current-meta').textContent = 'Odysseus Chat';
  Storage.remove('lastSessionId');
  history.replaceState(null, '', window.location.pathname);
  if (window.chatModule && window.chatModule.showWelcomeScreen) {
    window.chatModule.showWelcomeScreen();
  }
  // Reset send button to idle state
  const submitBtn = document.querySelector('.send-btn');
  if (submitBtn) {
    submitBtn.dataset.mode = '';
    delete submitBtn.dataset.phase;
    submitBtn.classList.remove('recording');
  }
  if (window._updateSendBtnIcon) window._updateSendBtnIcon();
}

// Initialize dependencies from app.js (no-op: dependencies now imported directly)
export function initDependencies() {}

// ── Folder state persistence ──
const FOLDER_STATE_KEY = 'odysseus-folder-state';
const FOLDER_ORDER_KEY = 'odysseus-folder-order';

function loadFolderState() {
  return Storage.getJSON(FOLDER_STATE_KEY, {});
}
function saveFolderState(state) {
  Storage.setJSON(FOLDER_STATE_KEY, state);
}
function loadFolderOrder() {
  return Storage.getJSON(FOLDER_ORDER_KEY, []);
}
function saveFolderOrder(order) {
  Storage.setJSON(FOLDER_ORDER_KEY, order);
}

/** Get all unique folder names from current sessions. */
function getFolderNames() {
  const names = new Set();
  sessions.forEach(s => { if (s.folder) names.add(s.folder); });
  return Array.from(names).sort();
}

/** Move a session to a folder via the API. */
async function moveToFolder(sessionId, folderName) {
  const fd = new FormData();
  fd.append('folder', folderName || '');
  await fetch(`${API_BASE}/api/session/${sessionId}`, { method: 'PATCH', body: fd });
  // Update local data
  const s = sessions.find(x => x.id === sessionId);
  if (s) s.folder = folderName || null;
  renderSessionList();
}

/** Build the "Move to folder" submenu for a session dropdown. */
function buildFolderSubmenu(sessionId, currentFolder, dropdown) {
  const folders = getFolderNames();

  const moveItem = document.createElement('div');
  moveItem.className = 'dropdown-item-compact';
  moveItem.style.position = 'relative';
  const _folderIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
  moveItem.innerHTML = '<span class="dropdown-icon">' + _folderIcon + '</span><span>Move to folder</span>';

  const sub = document.createElement('div');
  sub.className = 'dropdown session-folder-submenu';

  // "No folder" option
  const noneOpt = document.createElement('div');
  noneOpt.className = 'dropdown-item-compact';
  if (!currentFolder) noneOpt.style.opacity = '0.5';
  noneOpt.textContent = '(No folder)';
  noneOpt.addEventListener('click', async (e) => {
    e.stopPropagation();
    await moveToFolder(sessionId, '');
    dropdown.style.display = 'none';
    sub.style.display = 'none';
  });
  sub.appendChild(noneOpt);

  // Existing folders
  folders.forEach(f => {
    const opt = document.createElement('div');
    opt.className = 'dropdown-item-compact';
    if (f === currentFolder) opt.style.opacity = '0.5';
    opt.textContent = f;
    opt.addEventListener('click', async (e) => {
      e.stopPropagation();
      await moveToFolder(sessionId, f);
      // Auto-flip to By Folder view so the user can see where the
      // chat went, same as when creating a new folder.
      setSortMode('group');
      dropdown.style.display = 'none';
      sub.style.display = 'none';
    });
    sub.appendChild(opt);
  });

  // "New folder" option
  const newOpt = document.createElement('div');
  newOpt.className = 'dropdown-item-compact';
  newOpt.style.color = 'var(--accent-primary)';
  newOpt.textContent = '+ New Folder';
  newOpt.addEventListener('click', async (e) => {
    e.stopPropagation();
    const name = await styledPrompt('Name this folder:', {
      title: 'New folder',
      placeholder: 'e.g. Work, Research, Drafts',
      confirmText: 'Create',
    });
    if (!name || !name.trim()) return;
    await moveToFolder(sessionId, name.trim());
    // Auto-flip to By Folder view so the user immediately sees the
    // folder they just created — otherwise the new folder disappears
    // into the flat list and looks like the action did nothing.
    setSortMode('group');
    dropdown.style.display = 'none';
    sub.style.display = 'none';
  });
  sub.appendChild(newOpt);

  moveItem.addEventListener('click', (e) => {
    e.stopPropagation();
    if (sub.style.display === 'block') {
      sub.style.display = 'none';
    } else {
      const rect = moveItem.getBoundingClientRect();
      const isMobile = window.innerWidth <= 768;
      sub.style.top = '-9999px';
      sub.style.display = 'block';
      const subRect = sub.getBoundingClientRect();

      if (isMobile) {
        // On mobile: position below the dropdown, centered
        const ddRect = dropdown.getBoundingClientRect();
        sub.style.left = Math.max(8, ddRect.left) + 'px';
        sub.style.width = Math.min(ddRect.width, window.innerWidth - 16) + 'px';
        const topBelow = ddRect.bottom + 4;
        if (topBelow + subRect.height > window.innerHeight) {
          sub.style.top = Math.max(8, ddRect.top - subRect.height - 4) + 'px';
        } else {
          sub.style.top = topBelow + 'px';
        }
      } else {
        // Desktop: to the right
        sub.style.left = rect.right + 2 + 'px';
        sub.style.width = '';
        if (rect.top + subRect.height > window.innerHeight) {
          sub.style.top = Math.max(2, window.innerHeight - subRect.height - 4) + 'px';
        } else {
          sub.style.top = rect.top + 'px';
        }
        // Clamp right edge
        if (rect.right + 2 + subRect.width > window.innerWidth - 8) {
          sub.style.left = Math.max(8, rect.left - subRect.width - 2) + 'px';
        }
      }
    }
  });

  sub.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => { sub.style.display = 'none'; });
  document.body.appendChild(sub);

  return moveItem;
}

/** Create a single session list-item element. */
function createSessionItem(s) {
  const div = document.createElement('div');
  div.className = 'list-item session-item';
  div.setAttribute('role', 'option');
  div.setAttribute('tabindex', '-1');
  div.setAttribute('data-session-id', s.id);
  // Special-session sentinel — true for the legacy OpenClaw row, which
  // skips the normal provider dot / name / action chrome. Was
  // previously detected here but the declaration got removed while
  // leaving the references in place, causing ReferenceError on every
  // session list re-render.
  const isOpenClaw = s.is_openclaw || s.id === 'openclaw';

  // Drag handle
  const handle = document.createElement('span');
  handle.className = 'item-drag-handle';
  handle.textContent = '\u22EE\u22EE';
  handle.title = 'Drag to reorder';
  div.appendChild(handle);

  // Provider dot indicator
  if (!isOpenClaw) {
    const star = document.createElement('span');
    const _logo = providerLogo(s.model);
    if (_logo) {
      star.className = 'session-star provider-logo';
      star.innerHTML = _logo;
      star.style.opacity = '0.4';
    } else {
      star.className = 'session-star';
    }
    div.appendChild(star);
  }

  // Session type icon
  const icon = document.createElement('span');
  const _isFork = s.name && (s.name.startsWith('Fork:') || s.name.startsWith('\u2ADD'));
  const _isGroup = s.name && s.name.startsWith('[GRP]');
  icon.className = 'session-icon' + (s.has_documents ? ' has-docs' : '');
  if (_isGroup) {
    icon.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';
  } else if (_isFork) {
    icon.textContent = '\u2ADD';
    icon.style.fontSize = '14px';
  } else if (s.has_documents) {
    icon.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
  } else if (s.has_images) {
    icon.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>';
  } else if (s.mode === 'agent') {
    icon.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
  } else if (s.mode === 'research') {
    icon.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>';
  } else {
    icon.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  }
  // Favorite bookmark replaces session-icon when important
  if (s.is_important && !isOpenClaw) {
    icon.className = 'session-icon session-fav';
    icon.title = 'Unfavorite';
    icon.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>';
    icon.addEventListener('click', async (e) => {
      e.stopPropagation();
      const fd = new FormData();
      fd.append('important', false);
      await fetch(`${API_BASE}/api/session/${s.id}/important`, { method: 'POST', body: fd });
      s.is_important = false;
      uiModule.showToast('Unfavorited');
      renderSessionList();
    });
  }
  div.appendChild(icon);

  const span = document.createElement('span');
  span.className = 'grow';
  let chatTitle = s.name || '';
  if (_isFork) chatTitle = chatTitle.replace(/^Fork:\s*/, '').replace(/^\u2ADD\s*/, '');
  if (_isGroup) chatTitle = chatTitle.replace(/^\[GRP\]\s*/, '');
  let label = chatTitle;
  if (s.model) label += ' · ' + s.model.split('/').pop();
  if (s.archived) label += ' [archived]';
  span.textContent = label;
  span.title = (s.model ? s.model.split('/').pop() + ' · ' : '') + chatTitle;
  span.classList.add('text-ellipsis');

  // Double-click to rename (only when session is already selected)
  if (!isOpenClaw) {
    span.addEventListener('dblclick', (e) => {
      if (currentSessionId !== s.id) return; // must be selected first
      e.stopPropagation();
      const input = document.createElement('input');
      input.type = 'text';
      input.value = s.name || '';
      input.className = 'session-rename-input';
      span.replaceWith(input);
      input.focus();
      input.select();
      const _stopGuard = _guardSidebarDuringRename();
      const commit = async () => {
        const newName = input.value.trim();
        if (newName && newName !== s.name) {
          const fd = new FormData();
          fd.append('name', newName);
          await fetch(`${API_BASE}/api/session/${s.id}`, { method: 'PATCH', body: fd });
          s.name = newName;
          uiModule.showToast('Renamed');
        }
        _forceSidebarOpen();
        renderSessionList();
        _stopGuard();
      };
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
        if (ev.key === 'Escape') { input.removeEventListener('blur', commit); _forceSidebarOpen(); renderSessionList(); _stopGuard(); }
      });
    });
  }

  // Clicking anywhere on the row selects the session (except drag handle and menu)
  // On mobile, suppress click if user was scrolling (touchmove detected)
  // Long press on mobile shows context menu
  let _touchMoved = false;
  let _longPressTimer = null;
  let _longPressed = false;
  div.addEventListener('touchstart', (e) => {
    _touchMoved = false;
    _longPressed = false;
    if (window.innerWidth > 768) return;
    _longPressTimer = setTimeout(() => {
      _longPressed = true;
      // Haptic feedback if available
      if (navigator.vibrate) navigator.vibrate(30);
      // Show the session dropdown directly (menu button is hidden on mobile)
      const dd = div._sessionDropdown;
      if (dd) {
        // Close any other open dropdowns
        document.querySelectorAll('.dropdown').forEach(d => { if (d !== dd) d.style.display = 'none'; });
        const rect = div.getBoundingClientRect();
        dd.style.position = 'fixed';
        dd.style.left = rect.left + 'px';
        dd.style.top = (rect.bottom + 4) + 'px';
        dd.style.right = 'auto';
        dd.style.display = 'block';
        dd.style.zIndex = '1000';
        // Clamp to viewport
        requestAnimationFrame(() => {
          const mr = dd.getBoundingClientRect();
          if (mr.bottom > window.innerHeight - 8) dd.style.top = (rect.top - mr.height - 4) + 'px';
          if (mr.right > window.innerWidth - 8) { dd.style.left = 'auto'; dd.style.right = '8px'; }
        });
        // Close on tap outside
        const close = (ev) => { if (!dd.contains(ev.target)) { dd.style.display = 'none'; document.removeEventListener('click', close, true); } };
        setTimeout(() => document.addEventListener('click', close, true), 100);
      }
    }, 500);
  }, { passive: true });
  div.addEventListener('touchmove', () => {
    _touchMoved = true;
    if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
  }, { passive: true });
  div.addEventListener('touchend', () => {
    if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
  }, { passive: true });
  div.addEventListener('click', (e) => {
    if (e.target.closest('.item-drag-handle') || e.target.closest('.session-fav') || e.target.closest('.hamburger') || e.target.closest('.session-dropdown') || e.target.closest('.session-rename-input') || e.target.closest('.session-select-cb')) return;
    if (_touchMoved || _longPressed) { _touchMoved = false; _longPressed = false; return; }
    // In select mode, toggle dot instead of navigating
    if (_selectMode) {
      const dot = div.querySelector('.session-select-cb');
      if (dot) dot.click();
      return;
    }
    selectSession(s.id);
  });

  // Create a dropdown menu button
  const menuBtn = document.createElement('button');
  menuBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
  menuBtn.title = 'Session actions';
  menuBtn.className = 'hamburger session-menu-btn';

  // Create dropdown menu
  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown session-dropdown session-dropdown-menu';

  // Create menu items
  const _icon = (svg) => `<span class="dropdown-icon">${svg}</span>`;
  const _renameIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>';
  const _archiveIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>';
  const _deleteIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
  const _copyIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

  const renameItem = document.createElement('div');
  renameItem.className = 'dropdown-item-compact';
  renameItem.innerHTML = _icon(_renameIcon) + '<span>Rename</span>';

  const archiveItem = document.createElement('div');
  archiveItem.className = 'dropdown-item-compact';
  archiveItem.innerHTML = _icon(_archiveIcon) + '<span>Archive</span>';

  const deleteItem = document.createElement('div');
  deleteItem.className = 'dropdown-item-compact dropdown-item-danger';
  deleteItem.innerHTML = _icon(_deleteIcon) + '<span>Delete</span><span class="dropdown-shortcut">' + _mod + '+Alt+D</span>';



  dropdown.appendChild(renameItem);

  // Star/Unstar item
  if (!isOpenClaw) {
    const _favIcon = s.is_important
      ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>';
    const starItem = document.createElement('div');
    starItem.className = 'dropdown-item-compact';
    starItem.innerHTML = _icon(_favIcon) + '<span>' + (s.is_important ? 'Unfavorite' : 'Favorite') + '</span><span class="dropdown-shortcut">' + _mod + '+Alt+F</span>';
    starItem.addEventListener('click', async (e) => {
      e.stopPropagation();
      const newVal = !s.is_important;
      const fd = new FormData();
      fd.append('important', newVal);
      await fetch(`${API_BASE}/api/session/${s.id}/important`, { method: 'POST', body: fd });
      s.is_important = newVal;
      dropdown.style.display = 'none';
      renderSessionList();
    });
    dropdown.appendChild(starItem);
  }

  const copyItem = document.createElement('div');
  copyItem.className = 'dropdown-item-compact';
  copyItem.innerHTML = _icon(_copyIcon) + '<span>Copy Chat</span>';
  copyItem.addEventListener('click', async (e) => {
    e.stopPropagation();
    dropdown.style.display = 'none';
    try {
      const res = await fetch(`${API_BASE}/api/history/${s.id}`);
      const data = await res.json();
      const msgs = data.history || [];
      if (!msgs.length) { uiModule.showToast('No messages to copy'); return; }
      const lines = msgs
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => {
          const label = m.role === 'user' ? 'You' : 'AI';
          const text = typeof m.content === 'string' ? m.content.trim() : JSON.stringify(m.content);
          return `${label}: ${text}`;
        });
      const text = lines.join('\n\n');
      try {
        await navigator.clipboard.writeText(text);
      } catch (_clipErr) {
        // Fallback for non-secure contexts
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      }
      uiModule.showToast('Chat copied to clipboard');
    } catch (e) {
      console.error('Copy chat failed:', e);
      uiModule.showError('Failed to copy chat');
    }
  });

  // Rename is already appended above (line 393)

  // "Select" — enter bulk select mode with this session pre-selected
  if (!isOpenClaw) {
    const selectMoreItem = document.createElement('div');
    selectMoreItem.className = 'dropdown-item-compact';
    selectMoreItem.innerHTML = _icon('<span style="font-size:16px;line-height:1;">●</span>') + '<span>Select</span>';
    selectMoreItem.addEventListener('click', (e) => {
      e.stopPropagation();
      dropdown.style.display = 'none';
      _enterSelectMode();
      const dot = div.querySelector('.session-select-cb');
      if (dot) { dot._checked = true; dot.innerHTML = '●'; dot.style.opacity = '1'; dot.style.color = 'var(--accent, var(--red))'; _selectedIds.add(s.id); _updateBulkCount(); }
    });
    // On mobile, "Select" is the primary multi-pick action — put it at the top
    // of the menu. On desktop keep its original position.
    if (window.innerWidth <= 768) {
      dropdown.insertBefore(selectMoreItem, dropdown.firstChild);
    } else {
      dropdown.appendChild(selectMoreItem);
    }
  }

  // Copy & Move to folder
  const folderItem = buildFolderSubmenu(s.id, s.folder, dropdown);
  dropdown.appendChild(copyItem);
  dropdown.appendChild(folderItem);

  // Separator before destructive actions
  const _sep = document.createElement('div');
  _sep.style.cssText = 'height:1px;margin:3px 0;background:color-mix(in srgb,var(--border) 40%,transparent)';
  dropdown.appendChild(_sep);

  dropdown.appendChild(archiveItem);
  dropdown.appendChild(deleteItem);

  // Mobile-only Cancel — explicit close for touch users. CSS hides it on
  // desktop (outside-click already dismisses cleanly there).
  const _cancelIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const cancelItem = document.createElement('div');
  cancelItem.className = 'dropdown-item-compact dropdown-cancel-mobile';
  cancelItem.innerHTML = _icon(_cancelIcon) + '<span>Cancel</span>';
  cancelItem.addEventListener('click', (e) => {
    e.stopPropagation();
    dropdown.style.display = 'none';
  });
  dropdown.appendChild(cancelItem);

  // Add event listeners
  menuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    // Close any other open dropdowns
    document.querySelectorAll('.dropdown').forEach(d => {
      if (d !== dropdown) d.style.display = 'none';
    });
    // Toggle this dropdown
    if (dropdown.style.display === 'block') {
      dropdown.style.display = 'none';
    } else {
      // Position the dropdown using viewport coords
      const rect = menuBtn.getBoundingClientRect();
      dropdown.style.left = '';
      dropdown.style.right = (window.innerWidth - rect.right) + 'px';
      // Show off-screen first to measure height
      dropdown.style.top = '-9999px';
      dropdown.style.display = 'block';
      const ddRect = dropdown.getBoundingClientRect();
      // Flip above if not enough room below
      if (rect.bottom + 2 + ddRect.height > window.innerHeight) {
        dropdown.style.top = Math.max(2, rect.top - ddRect.height - 2) + 'px';
      } else {
        dropdown.style.top = rect.bottom + 2 + 'px';
      }
    }
  });

  renameItem.addEventListener('click', () => {
    dropdown.style.display = 'none';
    _forceSidebarOpen();
    // Find the session row's name span and start inline editing
    const sessionEl = document.querySelector(`.list-item[data-session-id="${s.id}"]`);
    if (!sessionEl) return;
    const span = sessionEl.querySelector('.grow');
    if (!span || sessionEl.querySelector('.session-rename-input')) return;
    const input = document.createElement('input');
    input.type = 'text';
    input.value = s.name || '';
    input.className = 'session-rename-input';
    span.replaceWith(input);
    input.focus();
    input.select();
    const _stopGuard = _guardSidebarDuringRename();
    const commit = async () => {
      const newName = input.value.trim();
      if (newName && newName !== s.name) {
        const fd = new FormData();
        fd.append('name', newName);
        await fetch(`${API_BASE}/api/session/${s.id}`, { method: 'PATCH', body: fd });
        s.name = newName;
        uiModule.showToast('Renamed');
      }
      _forceSidebarOpen();
      renderSessionList();
      _stopGuard();
    };
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
      if (ev.key === 'Escape') { input.removeEventListener('blur', commit); _forceSidebarOpen(); renderSessionList(); _stopGuard(); }
    });
  });

  deleteItem.addEventListener('click', async () => {
    if (s.is_important) {
      uiModule.showToast('Unfavorite before deleting');
      dropdown.style.display = 'none';
      return;
    }
    dropdown.style.display = 'none';
    if (!await uiModule.styledConfirm('Delete this session?', { confirmText: 'Delete', danger: true })) {
      _forceSidebarOpen();
      return;
    }
    // Optimistic: remove from UI immediately
    const sessionEl = document.querySelector(`.list-item[data-session-id="${s.id}"]`);
    if (sessionEl) sessionEl.remove();
    const wasCurrentSession = currentSessionId === s.id;
    // If streaming, abort it before deleting
    if (wasCurrentSession && window.chatModule && window.chatModule.abortCurrentRequest) {
      window.chatModule.abortCurrentRequest();
    }
    _deselectCurrentSession(s.id);
    _skipAutoSelect = true;
    // Clean up persistent chat mapping
    try {
      const pm = await import('./presets.js');
      if (pm.removePersistentChat) pm.removePersistentChat(s.id);
    } catch (e) {}
    // On mobile, close sidebar if we deleted the active session so user sees welcome screen
    if (wasCurrentSession && window.innerWidth <= 768) {
      const sidebar = document.getElementById('sidebar');
      if (sidebar) sidebar.classList.add('hidden');
      const backdrop = document.getElementById('sidebar-backdrop');
      if (backdrop) backdrop.classList.remove('visible');
    } else {
      _forceSidebarOpen();
    }
    // Fire API and reload in background
    fetch(`${API_BASE}/api/session/${s.id}`, { method: 'DELETE' })
      .then(() => loadSessions())
      .catch(() => loadSessions());
  });

  archiveItem.addEventListener('click', async () => {
    dropdown.style.display = 'none';
    _forceSidebarOpen();
    try {
      const response = await fetch(`${API_BASE}/api/session/${s.id}/archive`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      if (response.ok) {
        _forceSidebarOpen();
        await loadSessions();
        dropdown.style.display = 'none';
        uiModule.showToast('Session archived');
      } else {
        throw new Error('Failed to archive session');
      }
    } catch (error) {
      console.error('Error archiving session:', error);
      uiModule.showError('Failed to archive session');
    }
  });

  // Dropdowns are closed by the shared global listener (_initDropdownDismiss)

  // Prevent dropdown from closing when clicking inside it
  dropdown.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  div.appendChild(span);

  // Apply processing/completed state to the star dot
  var _isProcessing = _researchingSessions.has(s.id) || _streamingSessions.has(s.id);
  var _isDone = _completedSessions.has(s.id) && !_isProcessing;
  if (!isOpenClaw) {
    var _starEl = div.querySelector('.session-star');
    if (_starEl) {
      _starEl.dataset.sessionId = s.id;
      if (_isProcessing) {
        _starEl.classList.add('processing');
        _starEl.style.opacity = '1';
      } else if (_isDone) {
        _starEl.classList.add('notify');
        _starEl.style.opacity = '1';
        div.classList.add('stream-complete');
      }
    }
  }

  div.appendChild(menuBtn);
  dropdown.addEventListener('click', (e) => e.stopPropagation());
  document.body.appendChild(dropdown);
  div._sessionDropdown = dropdown;

  return div;
}

let _renderRAF = null;
export function renderSessionList() {
  // Debounce rapid re-renders within the same frame
  if (_renderRAF) cancelAnimationFrame(_renderRAF);
  _renderRAF = requestAnimationFrame(_renderSessionListImpl);
}

function _renderSessionListImpl() {
  _renderRAF = null;
  const list = uiModule.el('session-list');
  if (!list) return;

  // Get saved order from localStorage
  const savedOrder = Storage.get('session-order');
  let orderedSessions = sessions.filter(s => !s.archived && s.folder !== 'Assistant' && !_isIncognitoSession(s.id) && (s.name || '').trim() !== 'Nobody' && (s.name || '').trim() !== 'Incognito');

  if (savedOrder) {
    try {
      const orderIds = JSON.parse(savedOrder);
      const sessionMap = new Map(orderedSessions.map(s => [s.id, s]));
      const ordered = [];
      orderIds.forEach(id => {
        if (sessionMap.has(id)) {
          ordered.push(sessionMap.get(id));
          sessionMap.delete(id);
        }
      });
      // Append any new sessions not in saved order
      sessionMap.forEach(s => ordered.push(s));
      orderedSessions = ordered;
    } catch (e) {
      console.warn('Failed to restore session order:', e);
    }
  }

  // Clean up any previous session dropdowns and folder submenus from body
  document.querySelectorAll('.session-dropdown, .folder-submenu').forEach(d => d.remove());

  const _frag = document.createDocumentFragment();

  // ── Flat sort modes: ignore folders, show one ordered list. ──
  // Folders are only shown when _sortMode === 'group' (or null/empty
  // for manual mode). This keeps the picker simple: a folder-grouped
  // view is one of the sort choices, alongside Last Active / Newest.
  if (_sortMode && _sortMode !== 'group') {
    orderedSessions.sort((a, b) => {
      if (_sortMode === 'newest') return (b.created_at || '').localeCompare(a.created_at || '');
      // "Last active" sorts by the last actual MESSAGE, not updated_at —
      // updated_at is bumped by renames / model swaps / folder moves, which
      // made the order feel random. Fall back to updated_at/created_at for
      // older rows that predate the last_message_at backfill.
      if (_sortMode === 'active') {
        const av = a.last_message_at || a.updated_at || a.created_at || '';
        const bv = b.last_message_at || b.updated_at || b.created_at || '';
        return bv.localeCompare(av);
      }
      return 0;
    });
    // Starred still float to top
    const starred = orderedSessions.filter(s => s.is_important);
    const rest = orderedSessions.filter(s => !s.is_important);
    const allFlat = [...starred, ...rest];

    const limit = _showAllSessions ? allFlat.length : SIDEBAR_MAX_VISIBLE;
    const visible = allFlat.slice(0, limit);
    const activeIdx = allFlat.findIndex(s => s.id === currentSessionId);
    if (!_showAllSessions && activeIdx >= limit) visible.push(allFlat[activeIdx]);

    visible.forEach(s => _frag.appendChild(createSessionItem(s)));

    if (allFlat.length > SIDEBAR_MAX_VISIBLE) {
      const remaining = allFlat.length - SIDEBAR_MAX_VISIBLE;
      const toggleBtn = document.createElement('button');
      toggleBtn.className = 'session-show-more-btn';
      toggleBtn.textContent = _showAllSessions ? 'Show less' : `Show ${remaining} more`;
      toggleBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        _showAllSessions = !_showAllSessions;
        renderSessionList();
      });
      _frag.appendChild(toggleBtn);
    }

    list.innerHTML = '';
    list.appendChild(_frag);
    _postRenderSessionList(list);
    return;
  }

  // ── Group / manual mode: render folders, then unfiled sessions. ──
  const folderState = loadFolderState();
  const folders = {}; // folderName -> [sessions]
  const unfiled = [];

  orderedSessions.forEach(s => {
    if (s.folder) {
      if (!folders[s.folder]) folders[s.folder] = [];
      folders[s.folder].push(s);
    } else {
      unfiled.push(s);
    }
  });

  // Move starred sessions to top of each group, preserving relative order
  const starPartition = (arr) => {
    const starred = arr.filter(s => s.is_important);
    const rest = arr.filter(s => !s.is_important);
    arr.length = 0;
    arr.push(...starred, ...rest);
  };
  starPartition(unfiled);
  Object.values(folders).forEach(arr => starPartition(arr));

  // Render folders first (above unfiled sessions)
  const savedFolderOrder = loadFolderOrder();
  const allFolderNames = Object.keys(folders);
  const orderedFolderNames = [];
  savedFolderOrder.forEach(name => {
    if (allFolderNames.includes(name)) orderedFolderNames.push(name);
  });
  allFolderNames.forEach(name => {
    if (!orderedFolderNames.includes(name)) orderedFolderNames.push(name);
  });

  orderedFolderNames.forEach(folderName => {
    const folderDiv = document.createElement('div');
    folderDiv.className = 'session-folder';
    folderDiv.dataset.folderName = folderName;

    const header = document.createElement('div');
    header.className = 'session-folder-header';
    header.dataset.folderName = folderName;
    const collapsed = folderState[folderName] === false;

    // Drag handle for folder reordering
    const dragHandle = document.createElement('span');
    dragHandle.className = 'folder-drag-handle';
    dragHandle.textContent = '\u2630';
    dragHandle.title = 'Drag to reorder folder';
    header.appendChild(dragHandle);

    const toggle = document.createElement('span');
    toggle.className = 'folder-toggle';
    toggle.textContent = collapsed ? '\u25B6' : '\u25BC';
    header.appendChild(toggle);

    const nameSpan = document.createElement('span');
    nameSpan.className = 'folder-name';
    nameSpan.textContent = folderName;
    header.appendChild(nameSpan);

    const countSpan = document.createElement('span');
    countSpan.className = 'folder-count';
    countSpan.textContent = `(${folders[folderName].length})`;
    header.appendChild(countSpan);

    // Delete folder button
    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'folder-delete-btn';
    deleteBtn.textContent = '\u00d7';
    deleteBtn.title = 'Delete folder and all sessions';
    deleteBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const count = folders[folderName].length;
      if (!await uiModule.styledConfirm(`Delete folder "${folderName}" and all ${count} session(s) inside it?`, { confirmText: 'Delete', danger: true })) return;
      for (const s of folders[folderName]) {
        try {
          await fetch(`${API_BASE}/api/session/${s.id}`, { method: 'DELETE' });
          _deselectCurrentSession(s.id);
        } catch (err) {
          console.error('Failed to delete session:', s.id, err);
        }
      }
      await loadSessions();
    });
    header.appendChild(deleteBtn);

    let _folderTouchMoved = false;
    header.addEventListener('touchstart', () => { _folderTouchMoved = false; }, { passive: true });
    header.addEventListener('touchmove', () => { _folderTouchMoved = true; }, { passive: true });
    header.addEventListener('click', (e) => {
      e.stopPropagation();
      if (e.target.closest('.folder-drag-handle') || e.target.closest('.folder-delete-btn')) return;
      if (_folderTouchMoved) { _folderTouchMoved = false; return; }
      const state = loadFolderState();
      const isCollapsed = state[folderName] === false;
      state[folderName] = isCollapsed ? true : false;
      saveFolderState(state);
      renderSessionList();
    });

    // Allow renaming folder via double-click
    header.addEventListener('dblclick', async (e) => {
      e.stopPropagation();
      if (e.target.closest('.folder-delete-btn')) return;
      const newName = await styledPrompt('Rename folder:', {
        title: 'Rename folder',
        defaultValue: folderName,
        confirmText: 'Rename',
      });
      if (!newName || !newName.trim() || newName.trim() === folderName) return;
      const promises = folders[folderName].map(s => moveToFolder(s.id, newName.trim()));
      Promise.all(promises).then(() => loadSessions());
    });

    folderDiv.appendChild(header);

    if (!collapsed) {
      const content = document.createElement('div');
      content.className = 'session-folder-content';
      const folderSessions = folders[folderName];
      const folderExpanded = _expandedFolders[folderName];
      const folderLimit = folderExpanded ? folderSessions.length : FOLDER_MAX_VISIBLE;
      const visibleFolder = folderSessions.slice(0, folderLimit);

      // Always include active session even if beyond limit
      const activeInFolder = folderSessions.findIndex(s => s.id === currentSessionId);
      if (!folderExpanded && activeInFolder >= folderLimit) {
        visibleFolder.push(folderSessions[activeInFolder]);
      }

      visibleFolder.forEach(s => {
        content.appendChild(createSessionItem(s));
      });

      if (folderSessions.length > FOLDER_MAX_VISIBLE) {
        const rem = folderSessions.length - FOLDER_MAX_VISIBLE;
        const moreBtn = document.createElement('button');
        moreBtn.className = 'session-show-more-btn';
        moreBtn.textContent = folderExpanded ? 'Show less' : `Show ${rem} more`;
        moreBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          _expandedFolders[folderName] = !folderExpanded;
          renderSessionList();
        });
        content.appendChild(moreBtn);
      }

      folderDiv.appendChild(content);
    }

    _frag.appendChild(folderDiv);
  });

  // Render unfiled sessions below folders (capped unless expanded)
  const hasFolders = orderedFolderNames.length > 0;
  const activeInUnfiled = unfiled.findIndex(s => s.id === currentSessionId);
  const limit = _showAllSessions ? unfiled.length : SIDEBAR_MAX_VISIBLE;
  const visibleUnfiled = unfiled.slice(0, limit);

  // If active session is beyond the limit, include it
  if (!_showAllSessions && activeInUnfiled >= limit) {
    visibleUnfiled.push(unfiled[activeInUnfiled]);
  }

  // Wrap in "Unsorted" folder if real folders exist
  let unfiledTarget = _frag;
  if (hasFolders && unfiled.length > 0) {
    const unsortedDiv = document.createElement('div');
    unsortedDiv.className = 'session-folder unsorted-folder';
    const unsortedHeader = document.createElement('div');
    unsortedHeader.className = 'session-folder-header';
    const unsortedCollapsed = loadFolderState()['__unsorted__'] === false;

    const dragHandle = document.createElement('span');
    dragHandle.className = 'folder-drag-handle';
    dragHandle.textContent = '\u2630';
    dragHandle.title = 'Drag to reorder folder';
    unsortedHeader.appendChild(dragHandle);

    const toggle = document.createElement('span');
    toggle.className = 'folder-toggle';
    toggle.textContent = unsortedCollapsed ? '\u25B6' : '\u25BC';
    unsortedHeader.appendChild(toggle);
    const nameSpan = document.createElement('span');
    nameSpan.className = 'folder-name';
    nameSpan.textContent = 'Unsorted';
    unsortedHeader.appendChild(nameSpan);
    const countSpan = document.createElement('span');
    countSpan.className = 'folder-count';
    countSpan.textContent = `(${unfiled.length})`;
    unsortedHeader.appendChild(countSpan);

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'folder-delete-btn';
    deleteBtn.textContent = '\u00d7';
    deleteBtn.title = 'Delete all unsorted sessions';
    deleteBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!await uiModule.styledConfirm(`Delete all ${unfiled.length} unsorted session(s)?`, { confirmText: 'Delete', danger: true })) return;
      for (const s of unfiled) {
        try {
          await fetch(`${API_BASE}/api/session/${s.id}`, { method: 'DELETE' });
          _deselectCurrentSession(s.id);
        } catch (err) {
          console.error('Failed to delete session:', s.id, err);
        }
      }
      await loadSessions();
    });
    unsortedHeader.appendChild(deleteBtn);

    unsortedHeader.addEventListener('click', (e) => {
      e.stopPropagation();
      const state = loadFolderState();
      state['__unsorted__'] = state['__unsorted__'] === false ? true : false;
      saveFolderState(state);
      renderSessionList();
    });
    unsortedDiv.appendChild(unsortedHeader);
    if (!unsortedCollapsed) {
      const content = document.createElement('div');
      content.className = 'session-folder-content';
      unfiledTarget = content;
      unsortedDiv.appendChild(content);
    }
    _frag.appendChild(unsortedDiv);
    if (unsortedCollapsed) {
      unfiledTarget = null;
    }
  }

  if (unfiledTarget) {
    visibleUnfiled.forEach(s => {
      unfiledTarget.appendChild(createSessionItem(s));
    });
  }

  // "Show more" / "Show less" toggle
  if (unfiledTarget && unfiled.length > SIDEBAR_MAX_VISIBLE) {
    const remaining = unfiled.length - SIDEBAR_MAX_VISIBLE;
    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'session-show-more-btn';
    toggleBtn.textContent = _showAllSessions ? 'Show less' : `Show ${remaining} more`;
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _showAllSessions = !_showAllSessions;
      renderSessionList();
    });
    unfiledTarget.appendChild(toggleBtn);
  }

  // Flush all built elements into the list in one operation
  list.innerHTML = '';
  list.appendChild(_frag);

  _postRenderSessionList(list);
}

/** Shared post-render: highlight, keyboard nav, swipe hint, drag sort */
function _postRenderSessionList(list) {
  if (currentSessionId) {
    const activeEl = document.querySelector(`.list-item[data-session-id="${currentSessionId}"]`);
    if (activeEl) {
      activeEl.classList.add('active-session');
      if (_sessionListFocused) activeEl.focus();
    }
  }

  _initKeyboardNav(list);
  _initSwipeToDelete(list);
  initDragSort();
  _showSwipeHint(list);
}

function _initKeyboardNav(list) {
  if (!list._kbInit) {
    list._kbInit = true;
    list.addEventListener('keydown', _onSessionListKeydown);
    list.addEventListener('focusin', () => { _sessionListFocused = true; });
    list.addEventListener('focusout', (e) => {
      if (!list.contains(e.relatedTarget)) _sessionListFocused = false;
    });
  }
}

function _initSwipeToDelete(list) {
  // handled by existing swipe code — placeholder for consistency
}

function _showSwipeHint(list) {
  if ('ontouchstart' in window && !localStorage.getItem('ody-swipe-hint-shown')) {
    const firstItem = list.querySelector('.session-item');
    if (firstItem) {
      localStorage.setItem('ody-swipe-hint-shown', '1');
      const hint = document.createElement('div');
      hint.className = 'swipe-hint';
      hint.innerHTML = '<span class="swipe-hint-arrow">\u2190</span> swipe to delete';
      firstItem.style.position = 'relative';
      firstItem.appendChild(hint);
      setTimeout(() => { hint.style.opacity = '0'; }, 3000);
      setTimeout(() => { hint.remove(); }, 3500);
    }
  }
}

// ── Force sidebar open on mobile (after dropdown actions) ──
function _forceSidebarOpen() {
  if (window.innerWidth > 768) return;
  // Suppress backdrop close
  if (window._suppressSidebarClose !== undefined) {
    window._suppressSidebarClose = true;
    setTimeout(() => { window._suppressSidebarClose = false; }, 2000);
  }
  // Force sidebar visible
  requestAnimationFrame(() => {
    const sb = document.getElementById('sidebar');
    if (sb && sb.classList.contains('hidden')) {
      sb.classList.remove('hidden');
      if (window.syncRailSide) window.syncRailSide();
    }
  });
}

// While an inline rename is in progress on mobile, several paths can hide the
// sidebar (backdrop tap, soft-keyboard viewport resize, dropdown dismiss). Watch
// the sidebar directly and re-open it if anything hides it — bulletproof against
// whichever path fires. Returns a stopper to call once the rename is committed.
function _guardSidebarDuringRename() {
  if (window.innerWidth > 768 || !window.MutationObserver) return () => {};
  const sb = document.getElementById('sidebar');
  if (!sb) return () => {};
  const obs = new MutationObserver(() => {
    if (sb.classList.contains('hidden')) {
      sb.classList.remove('hidden');
      const bd = document.getElementById('sidebar-backdrop');
      if (bd) bd.classList.add('visible');
    }
  });
  obs.observe(sb, { attributes: true, attributeFilter: ['class'] });
  // Keep guarding briefly after the caller stops, to catch the keyboard-dismiss
  // resize that fires just after blur/commit.
  return () => setTimeout(() => obs.disconnect(), 400);
}

// ── Bulk select mode ──
let _selectMode = false;
let _selectedIds = new Set();

function _enterSelectMode() {
  _selectMode = true;
  _selectedIds.clear();
  const bulkBar = document.getElementById('session-bulk-bar');
  if (bulkBar) bulkBar.classList.remove('hidden');
  const selectBtn = document.getElementById('session-select-btn');
  if (selectBtn) selectBtn.style.opacity = '1';
  // Add select dots to all session items
  document.querySelectorAll('.list-item[data-session-id]').forEach(item => {
    if (item.querySelector('.session-select-cb')) return;
    const dot = document.createElement('span');
    dot.className = 'session-select-cb';
    dot.innerHTML = '○';
    dot.style.cssText = 'cursor:pointer;font-size:16px;flex-shrink:0;opacity:0.4;transition:opacity 0.1s;user-select:none;';
    dot._checked = false;
    dot.addEventListener('click', (e) => {
      e.stopPropagation();
      dot._checked = !dot._checked;
      dot.innerHTML = dot._checked ? '●' : '○';
      dot.style.opacity = dot._checked ? '1' : '0.4';
      dot.style.color = dot._checked ? 'var(--accent, var(--red))' : '';
      const sid = item.dataset.sessionId;
      if (dot._checked) _selectedIds.add(sid);
      else _selectedIds.delete(sid);
      _updateBulkCount();
    });
    item.insertBefore(dot, item.firstChild);
  });
  _updateBulkCount();
}

function _exitSelectMode() {
  _selectMode = false;
  _selectedIds.clear();
  const bulkBar = document.getElementById('session-bulk-bar');
  if (bulkBar) bulkBar.classList.add('hidden');
  const selectBtn = document.getElementById('session-select-btn');
  if (selectBtn) selectBtn.style.opacity = '0.5';
  const selectAll = document.getElementById('session-select-all');
  if (selectAll) selectAll.checked = false;
  // Remove checkboxes
  document.querySelectorAll('.session-select-cb').forEach(cb => cb.remove());
}

function _updateBulkCount() {
  const count = _selectedIds.size;
  const archiveBtn = document.getElementById('session-bulk-archive');
  const deleteBtn = document.getElementById('session-bulk-delete');
  if (archiveBtn) { archiveBtn.disabled = count === 0; archiveBtn.style.opacity = count === 0 ? '0.2' : ''; }
  if (deleteBtn) { deleteBtn.disabled = count === 0; deleteBtn.style.opacity = count === 0 ? '0.2' : ''; }
}

function _initBulkSelect() {
  const selectBtn = document.getElementById('session-select-btn');
  if (selectBtn) {
    selectBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (_selectMode) _exitSelectMode();
      else _enterSelectMode();
    });
  }
  const cancelBtn = document.getElementById('session-bulk-cancel');
  if (cancelBtn) cancelBtn.addEventListener('click', () => _exitSelectMode());

  // Select from funnel dropdown
  const selectFromDropdown = document.getElementById('session-select-from-dropdown');
  if (selectFromDropdown) {
    selectFromDropdown.addEventListener('click', () => {
      const dd = document.getElementById('session-sort-dropdown');
      if (dd) dd.style.display = 'none';
      _enterSelectMode();
    });
  }

  // Escape exits select mode
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _selectMode) {
      _exitSelectMode();
    }
  });

  const selectAll = document.getElementById('session-select-all');
  const selectAllDot = document.getElementById('session-select-all-dot');
  const selectAllLabel = document.getElementById('session-select-all-label');
  if (selectAll && selectAllDot) {
    const _toggleAll = () => {
      selectAll.checked = !selectAll.checked;
      selectAllDot.innerHTML = selectAll.checked ? '●' : '○';
      selectAllDot.style.opacity = selectAll.checked ? '1' : '0.4';
      selectAllDot.style.color = selectAll.checked ? 'var(--accent, var(--red))' : '';
      document.querySelectorAll('.session-select-cb').forEach(dot => {
        dot._checked = selectAll.checked;
        dot.innerHTML = selectAll.checked ? '●' : '○';
        dot.style.opacity = selectAll.checked ? '1' : '0.4';
        dot.style.color = selectAll.checked ? 'var(--accent, var(--red))' : '';
        const sid = dot.closest('[data-session-id]')?.dataset.sessionId;
        if (sid) {
          if (selectAll.checked) _selectedIds.add(sid);
          else _selectedIds.delete(sid);
        }
      });
      _updateBulkCount();
    };
    selectAllDot.addEventListener('click', _toggleAll);
    if (selectAllLabel) selectAllLabel.addEventListener('click', _toggleAll);
  }

  const archiveBtn = document.getElementById('session-bulk-archive');
  if (archiveBtn) {
    archiveBtn.addEventListener('click', async () => {
      if (_selectedIds.size === 0) return;
      const count = _selectedIds.size;
      if (!await uiModule.styledConfirm(`Archive ${count} session(s)?`, { confirmText: 'Archive' })) return;
      for (const sid of _selectedIds) {
        try {
          await fetch(`${API_BASE}/api/session/${sid}/archive`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        } catch (_) {}
      }
      _exitSelectMode();
      if (window._suppressSidebarClose !== undefined) { window._suppressSidebarClose = true; setTimeout(() => { window._suppressSidebarClose = false; }, 1500); }
      await loadSessions();
      uiModule.showToast(`${count} session(s) archived`);
    });
  }

  const deleteBtn = document.getElementById('session-bulk-delete');
  if (deleteBtn) {
    deleteBtn.addEventListener('click', async () => {
      if (_selectedIds.size === 0) return;
      const count = _selectedIds.size;
      if (!await uiModule.styledConfirm(`Delete ${count} session(s)? This cannot be undone.`, { confirmText: 'Delete', danger: true })) return;
      const deletedIds = [];
      for (const sid of _selectedIds) {
        try {
          const res = await fetch(`${API_BASE}/api/session/${sid}`, { method: 'DELETE' });
          if (res.ok) deletedIds.push(sid);
        } catch (_) {}
      }
      await _animateSessionRowsRemoving(deletedIds, '#session-list .list-item[data-session-id]');
      _exitSelectMode();
      if (window._suppressSidebarClose !== undefined) { window._suppressSidebarClose = true; setTimeout(() => { window._suppressSidebarClose = false; }, 1500); }
      await loadSessions();
      uiModule.showToast(`${deletedIds.length} session(s) deleted`);
    });
  }
}

function _animateSessionRowsRemoving(ids, selector) {
  const idSet = new Set((ids || []).map(id => String(id)));
  if (!idSet.size) return Promise.resolve();
  const rows = Array.from(document.querySelectorAll(selector || '.list-item[data-session-id]'))
    .filter(row => idSet.has(String(row.dataset.sessionId || row.dataset.sid)));
  if (!rows.length) return Promise.resolve();
  for (const row of rows) {
    row.style.maxHeight = `${Math.max(row.getBoundingClientRect().height, row.scrollHeight)}px`;
    row.classList.add('memory-tidy-removing');
  }
  return new Promise(resolve => setTimeout(resolve, 520));
}

export async function loadSessions() {
  try {
    // Delete incognito sessions left over from a previous page load
    await _cleanupIncognitoSessions();

    // Use prefetched data from login page if available (first load only)
    const prefetched = sessionStorage.getItem('ody-prefetch-sessions');
    let fetched;
    if (prefetched) {
      sessionStorage.removeItem('ody-prefetch-sessions');
      fetched = JSON.parse(prefetched);
    } else {
      const res = await fetch(`${API_BASE}/api/sessions`);
      fetched = await res.json();
    }
    sessions = fetched;
    renderSessionList();

    const sessionsSection = uiModule.el('sessions-section');
    if (sessions.length === 0) {
      sessionsSection.classList.add('hidden');
    } else {
      sessionsSection.classList.remove('hidden');
    }

    const activeSessions = sessions.filter(s => !s.archived);
    // "Transient" sessions = the singleton Assistant chat + any task-output
    // session. Treat them as not-restorable so coming back to the app lands
    // on the user's last actual conversation, not whichever check-in task
    // most recently appended a message.
    const _isTransient = (s) => !!s && (s.folder === 'Assistant' || s.folder === 'Tasks');
    const _realSessions = activeSessions.filter(s => !_isTransient(s));
    const hashId = window.location.hash.replace('#', '');
    let savedId = Storage.get('lastSessionId');
    // If the persisted lastSessionId points to a transient session (legacy
    // state from before the persistence-guard was added), drop it.
    if (savedId) {
      const _saved = activeSessions.find(s => s.id === savedId);
      if (_saved && _isTransient(_saved)) {
        Storage.remove('lastSessionId');
        savedId = null;
      }
    }
    const hasPendingChat = !!_pendingChat;
    let targetId = null;
    if (hasPendingChat) {
      // A model was picked and the UI is showing a fresh New Chat, but the
      // session is not created until the first message. Background stream
      // completions call loadSessions() later; without this guard that reload
      // sees no current session and auto-selects the previous chat.
      targetId = null;
    } else if (hashId && activeSessions.some(s => s.id === hashId)) {
      targetId = hashId;
    } else if (currentSessionId && activeSessions.some(s => s.id === currentSessionId)) {
      targetId = currentSessionId;
    } else if (currentSessionId) {
      // Session was just created but may not be in the list yet — keep it
      targetId = currentSessionId;
    } else if (savedId && activeSessions.some(s => s.id === savedId)) {
      targetId = savedId;
    } else if (!_skipAutoSelect && _realSessions.length > 0) {
      // Most-recent NON-transient session — skip Assistant / Tasks so the
      // auto-firing assistant doesn't become the apparent default chat.
      targetId = _realSessions[0].id;
    } else if (!_skipAutoSelect && activeSessions.length > 0) {
      // Only transient sessions exist (brand-new account) — fall through to
      // the original behaviour so we don't leave the user with nothing.
      targetId = activeSessions[0].id;
    }
    _skipAutoSelect = false;

    // Fresh login: prefer a default-model session so a brand-new user lands
    // ready to chat. CRITICAL: only do this when there's NO session to return
    // to (no hash / lastSessionId / existing chat resolved into targetId).
    // Otherwise a fresh page load — which a server restart triggers — would
    // spin up a new empty default-model chat and shadow the user's last
    // conversation, making it look like the chat "lost its context" (and the
    // picker would still show the old model's name from cached state). See
    // the targetId resolution above (hash → currentSession → lastSessionId →
    // most-recent).
    const _isFirstLoad = !sessionStorage.getItem('ody-session-active');
    if (_isFirstLoad) {
      sessionStorage.setItem('ody-session-active', '1');
      if (!targetId) {
        try {
          const dcRes = await fetch(`${API_BASE}/api/default-chat`);
          const dc = await dcRes.json();
          if (dc.endpoint_url && dc.model) {
            // Check if there's already an empty session with this model we can reuse
            const emptyDefault = activeSessions.find(s =>
              s.model === dc.model && s.message_count === 0
            );
            if (emptyDefault) {
              targetId = emptyDefault.id;
            } else {
              await createDirectChat(dc.endpoint_url, dc.model, dc.endpoint_id);
              // On mobile, hide sidebar so user lands directly in chat
              if (window.innerWidth < 768) {
                const sb = document.getElementById('sidebar');
                if (sb) sb.classList.add('hidden');
              }
              return; // createDirectChat handles selectSession internally
            }
          }
        } catch (_) { /* no default model configured */ }
      }
    }

    if (targetId && targetId !== currentSessionId) {
      await selectSession(targetId, { keepSidebar: true });
    } else if (targetId && targetId === currentSessionId) {
      // Same session — just refresh the header name in case it was auto-generated
      const s = sessions.find(x => x.id === targetId);
      const metaEl = document.getElementById('current-meta');
      if (metaEl && s) metaEl.textContent = s.name;
    }

    // No session selected — still enable input so slash commands (e.g. /setup) work
    if (!targetId && !hasPendingChat) {
      const msgInput = document.getElementById('message');
      if (msgInput) {
        msgInput.disabled = false;
        if (window.innerWidth > 768) msgInput.focus();
      }
      if (window.chatModule && window.chatModule.showWelcomeScreen) {
        window.chatModule.showWelcomeScreen();
      }
      updateModelPicker();
      // Only auto-create if there are truly zero sessions (not just unselected)
      if (activeSessions.length === 0 && !_autoCreateInProgress) {
        _autoCreateInProgress = true;
        try {
          const dcRes = await fetch(`${API_BASE}/api/default-chat`);
          const dc = await dcRes.json();
          if (dc.endpoint_url && dc.model) {
            await createDirectChat(dc.endpoint_url, dc.model, dc.endpoint_id);
          }
        } catch (_) { /* no default model — that's fine, user can /setup */ }
        _autoCreateInProgress = false;
      }
    }
  } catch (error) {
    console.error('Error in loadSessions:', error);
    uiModule.showError('Failed to load sessions: ' + error.message);
  }
}

export async function selectSession(id, { keepSidebar = false } = {}) {
  // Exit compare mode cleanly if active
  if (window.compareModule && window.compareModule.isActive()) {
    window.compareModule.deactivate(true);
    return; // deactivate does a page reload
  }
  try {
    const navToken = ++_sessionNavToken;
    const prevSessionId = currentSessionId;
    // Re-archive peeked session when navigating away
    _checkPeekCleanup(id);
    // Clear any leftover document text selection so it doesn't bleed into the new chat
    if (prevSessionId !== id && window.documentModule?.clearSelection) {
      try { window.documentModule.clearSelection(); } catch {}
    }
    currentSessionId = id;
    // Identify Assistant / task-output sessions so we don't "trap" the user
    // there on return. Skipped from both `lastSessionId` persistence and the
    // URL hash — the user complained that coming back to Odysseus kept
    // landing them on the auto-firing task-log chat instead of their last
    // real conversation.
    const _meta = sessions.find(s => s.id === id);
    const _isTransientChat = !!_meta && (_meta.folder === 'Assistant' || _meta.folder === 'Tasks');
    if (!_isTransientChat) {
      Storage.set('lastSessionId', id);
      // Update URL hash without triggering hashchange handler
      if (window.location.hash !== '#' + id) {
        history.replaceState(null, '', '#' + id);
      }
    }
    // Restore character preset for persistent chats
    try {
      const presetsModule = window.presetsModule || (await import('./presets.js')).default;
      if (presetsModule && presetsModule.onSessionSwitch) presetsModule.onSessionSwitch(id);
    } catch (e) {}
    const meta = sessions.find(s => s.id === id);

    // Detach any in-flight stream to background instead of aborting
    try {
      if (window.chatModule) {
        if (window.chatModule.detachCurrentStream) {
          window.chatModule.detachCurrentStream(prevSessionId);
        } else if (window.chatModule.abortCurrentRequest) {
          window.chatModule.abortCurrentRequest();
        }
      }
    } catch (e) {
      console.warn('detachCurrentStream error:', e);
      if (window.chatModule && window.chatModule.abortCurrentRequest) {
        window.chatModule.abortCurrentRequest();
      }
    }
    // Reset send button to idle state
    if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    const sendBtn = document.querySelector('.send-btn');
    if (sendBtn && sendBtn.dataset.mode === 'streaming') {
      sendBtn.dataset.mode = '';
      sendBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
      sendBtn.title = 'Send message';
    }
    // Deactivate compare mode on session switch
    if (window.compareModule) {
      if (window.compareModule.isActive()) window.compareModule.deactivate(true);
      else if (window.compareModule.hasVisibleResults()) window.compareModule.cleanupResults();
    }
    const msgInput = document.getElementById('message');
    if (msgInput) {
      msgInput.disabled = false;
      msgInput.value = '';
    }
    const sendBtn2 = document.querySelector('.send-btn');
    if (sendBtn2) {
      sendBtn2.style.color = '';
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    }

    // On mobile, keep sidebar open — user dismisses it by tapping chat area or swiping

    // Highlight active session in sidebar
    document.querySelectorAll('.list-item.active-session').forEach(el => el.classList.remove('active-session'));
    const activeEl = document.querySelector(`.list-item[data-session-id="${id}"]`);
    if (activeEl) activeEl.classList.add('active-session');

    const currentMetaEl = uiModule.el('current-meta');
    if (currentMetaEl) {
      currentMetaEl.textContent = meta ? meta.name : 'Odysseus Chat';
    }
    // Update model picker visibility
    updateModelPicker();

    // Refresh session cost badge for the newly selected session
    if (chatRenderer.updateSessionCostUI) chatRenderer.updateSessionCostUI();

    const chatHistory = uiModule.el('chat-history');
    // Prefetch history before fading so we can swap instantly. `isOC`
    // is the OpenClaw special-session sentinel — used by the wouldWipe
    // guard below and the welcome-screen branch further down. (Its
    // declaration had been removed while leaving the references in
    // place, producing a ReferenceError every selectSession.)
    const isOC = meta && (meta.is_openclaw || id === 'openclaw');
    let msgHistory = [], modelName = null;
    if (!isOC) {
      const res = await fetch(`${API_BASE}/api/history/${id}`);
      const data = await res.json();
      if (navToken !== _sessionNavToken || currentSessionId !== id) return;
      msgHistory = data.history || [];
      modelName = data.model || null;
      // The model returned by /api/history is the authoritative one the
      // backend will use for this session. Write it back into the cached
      // session meta and refresh the picker so the displayed model can
      // never diverge from what's actually sent (the "picker says Minimax
      // but it used the default" bug after a restart / stale cache).
      if (modelName) {
        const sMeta = sessions.find(s => s.id === id);
        if (sMeta && sMeta.model !== modelName) {
          sMeta.model = modelName;
          updateModelPicker();
        }
      }
    }

    // Guard: if the fetched history is empty but the DOM already has message
    // bubbles for the same session (incognito doesn't persist, so /api/history
    // returns []), preserve the DOM instead of wiping it. This fixes the
    // "reply flashes for 0.1s then empty" bug when selectSession is called
    // after a streaming completion in an incognito chat.
    const isSameSession = (prevSessionId === id);
    const hasExistingBubbles = chatHistory && chatHistory.querySelectorAll('.msg').length > 0;
    const wouldWipe = !isOC && !msgHistory.length && isSameSession && hasExistingBubbles;
    if (wouldWipe) {
      // Skip the fade/reload; we're already showing the right content.
      if (chatHistory) chatHistory.classList.remove('no-animate');
      return;
    }

    // Fade out old content, swap, fade in
    if (chatHistory) {
      chatHistory.style.transition = 'opacity 0.12s ease-out';
      chatHistory.style.opacity = '0';
      await new Promise(r => setTimeout(r, 120));
      if (navToken !== _sessionNavToken || currentSessionId !== id) return;
      chatHistory.innerHTML = '';
    }

    // Suppress per-message entrance animations during bulk history render
    if (chatHistory) chatHistory.classList.add('no-animate');

    // Populate new content while invisible
    if (isOC) {
      if (window.chatModule && window.chatModule.showWelcomeScreen) window.chatModule.showWelcomeScreen();
      window.chatModule.addMessage('assistant',
        `<p>\uD83E\uDD9E <strong>OpenClaw Agent Connected</strong></p>
         <p>Messages will be routed through your OpenClaw agent. The agent has access to tools, memory, and skills configured in your OpenClaw workspace.</p>`,
        'OpenClaw');
    } else if (msgHistory.length) {
      for (const msg of msgHistory) {
        const meta = msg.metadata ? { ...msg.metadata, _fromHistory: true } : null;
        let displayContent = typeof msg.content === 'string' ? msg.content : (msg.content ? String(msg.content) : '');
        // Clean up doc selection context for display
        if (msg.role === 'user') {
          // Hide "Continue where you left off" bubbles
          if (displayContent.trim() === 'Continue where you left off' || displayContent.trim().startsWith('Your message was cut off.') || displayContent.trim().startsWith('Your previous response was interrupted.') || displayContent.includes('[Instruction: Rewrite') || displayContent.includes('[Instruction: Explain')) continue;
          const docEditMatch = displayContent.match(/^In the document, edit this specific text \((lines? [\d-]+)\):\n```\n([\s\S]*?)\n```\n\nInstruction: ([\s\S]*)$/);
          if (docEditMatch) {
            displayContent = `[Doc edit: ${docEditMatch[1]}] ${docEditMatch[3]}`;
          }
        }
        window.chatModule.addMessage(msg.role, markdownModule.renderContent(displayContent), modelName, meta);
      }
    } else {
      if (window.chatModule && window.chatModule.showWelcomeScreen) window.chatModule.showWelcomeScreen();
      // Don't highlight empty sessions — feels like nothing is selected
      document.querySelectorAll('.list-item.active-session').forEach(el => el.classList.remove('active-session'));
    }
    uiModule.scrollHistoryInstant();

    // Fade in and re-enable message animations
    if (chatHistory) {
      chatHistory.style.transition = 'opacity 0.15s ease-in';
      chatHistory.style.opacity = '1';
      chatHistory.classList.remove('no-animate');
    }
    if (window.hljs) {
      document.querySelectorAll('pre code:not(.hljs)').forEach(block => {
        window.hljs.highlightElement(block);
      });
    }
    // Hide research button on session switch — it's only for the session that started it
    var _rBtn = document.getElementById('research-toggle-btn');
    var _rChk = document.getElementById('research-toggle');
    if (_rBtn) _rBtn.style.display = 'none';
    if (_rChk) _rChk.checked = false;

    // Check for pending/completed research that survived a page refresh
    if (window.chatModule && window.chatModule.checkPendingResearch) {
      window.chatModule.checkPendingResearch(id);
    }
    // Restore group chat state if this is a group session
    if (window.groupModule && window.groupModule.restoreState && window.groupModule.restoreState(id)) {
      if (window._syncGroupIndicator) window._syncGroupIndicator(true);
      // Hide model picker for group sessions
      const _mpw = document.getElementById('model-picker-wrap');
      if (_mpw) _mpw.style.display = 'none';
    } else if (window.groupModule && window.groupModule.isActive()) {
      // Switching away from group session — deactivate
      window.groupModule.stopGroup();
      if (window._syncGroupIndicator) window._syncGroupIndicator(false);
    }

    // Stop pulsing notification — user is now viewing this session
    clearStreamComplete(id);

    // Re-attach any background stream
    try {
      if (window.chatModule && window.chatModule.checkBackgroundStream) {
        window.chatModule.checkBackgroundStream(id);
      }
    } catch (e) {
      console.warn('checkBackgroundStream error:', e);
    }
    // Check server for active stream (survives page refresh)
    _checkServerStream(id);
    // Document panel: keep open if next session also wants it, otherwise close
    if (window.documentModule) {
      const docBtn = document.getElementById('overflow-doc-btn');
      const meta = sessions.find(s => s.id === id);
      const shouldOpen = localStorage.getItem('odysseus-doc-open-' + id) === '1';
      const hasDocs = !!(meta && meta.has_documents);
      if (docBtn) {
        docBtn.classList.remove('active');
        docBtn.classList.toggle('has-docs', hasDocs);
      }
      const docInd = document.getElementById('doc-indicator-btn');
      if (docInd) docInd.classList.toggle('visible', hasDocs);
      if (hasDocs) {
        // Wait for session UI to settle, then slide in documents
        setTimeout(() => window.documentModule.loadSessionDocs(id, { restoreMode: true }), 300);
      } else if (!shouldOpen) {
        window.documentModule.closePanel();
      }
    }

  } catch (error) {
    console.error('Error in selectSession:', error);
    uiModule.showError('Failed to load session: ' + error.message);
  } finally {
    // Ensure memories are loaded after session selection
    if (window.memoryModule && window.memoryModule.loadMemories) {
      await window.memoryModule.loadMemories();
    }
    // Auto-focus message input (unless session list has keyboard focus).
    // Skip on mobile — focusing the textarea pops up the on-screen keyboard,
    // which is intrusive when the user is just navigating between chats
    // (e.g. picking a chat from the Library). They can tap the input to
    // bring up the keyboard when they actually want to type.
    if (!_sessionListFocused && window.innerWidth > 768) {
      const msgInput = document.getElementById('message');
      if (msgInput) msgInput.focus();
    }
  }
}

// Pending session — stored locally until the first message is sent
let _pendingChat = null; // { url, modelId, endpointId }

export function createDirectChat(url, modelId, endpointId) {
  _sessionNavToken++;
  // Detach any active stream so it doesn't interfere with the new chat
  if (window.chatModule && window.chatModule.detachCurrentStream) {
    window.chatModule.detachCurrentStream(currentSessionId);
  }
  // Stop an active GROUP chat too — otherwise its in-flight parallel/round-robin
  // streams keep rendering into the brand-new chat (abort the group's fetches).
  if (window.groupModule && window.groupModule.isActive && window.groupModule.isActive()) {
    try { window.groupModule.stopGroup(); } catch {}
    if (window._syncGroupIndicator) window._syncGroupIndicator(false);
  }

  // Don't hit the API — just store the model info and prepare the UI
  _pendingChat = { url, modelId, endpointId };
  _skipAutoSelect = true;
  currentSessionId = null;
  Storage.remove('lastSessionId');
  history.replaceState(null, '', window.location.pathname);
  document.querySelectorAll('.list-item.active-session, .session-item.active').forEach(el => {
    el.classList.remove('active-session', 'active');
  });

  // Close document panel — new chat has no docs
  if (window.documentModule && window.documentModule.isPanelOpen()) {
    window.documentModule.closePanel();
  }
  const docBtn = document.getElementById('overflow-doc-btn');
  if (docBtn) {
    docBtn.classList.remove('active', 'has-docs');
    docBtn.style.display = ''; // show in overflow menu again
  }
  const docInd = document.getElementById('doc-indicator-btn');
  if (docInd) docInd.classList.remove('visible', 'active');

  // Clear chat area and show welcome
  const box = document.getElementById('chat-history');
  if (box) box.innerHTML = '';
  if (window.chatModule && window.chatModule.showWelcomeScreen) {
    window.chatModule.showWelcomeScreen();
  }

  // Update model picker to show the pending model
  updateModelPicker();

  // Update current-meta header
  const metaEl = document.getElementById('current-meta');
  if (metaEl) {
    metaEl.textContent = 'New Chat';
  }

  // Enable input
  const msgInput = document.getElementById('message');
  if (msgInput) { msgInput.disabled = false; msgInput.value = ''; msgInput.focus(); }
}

/** Actually create the session in the DB. Called on first message send. */
export async function materializePendingSession() {
  const pending = _pendingChat;
  if (!pending) return false;
  _pendingChat = null;

  const incognitoChk = document.getElementById('incognito-toggle');
  const isIncognito = incognitoChk && incognitoChk.checked;
  const base = (pending.modelId || 'model').split('/').pop();
  const name = isIncognito ? 'Nobody' : `${base} ${new Date().toLocaleTimeString()}`;

  const fd = new FormData();
  fd.append('name', name);
  fd.append('endpoint_url', pending.url || '');
  fd.append('model', pending.modelId || '');
  if (pending.url && pending.modelId) {
    fd.append('skip_validation', 'true');
  }
  if (pending.endpointId) {
    fd.append('endpoint_id', pending.endpointId);
  }

  let res;
  try {
    res = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: fd });
  } catch (e) {
    uiModule.showError('Failed to reach backend: ' + e);
    return false;
  }

  let payload;
  try {
    payload = await res.json();
  } catch {
    payload = { detail: await res.text() };
  }

  if (!res.ok) {
    uiModule.showError(`Session create failed (${res.status}) ${payload.detail || JSON.stringify(payload)}`);
    return false;
  }

  if (isIncognito && payload.id) {
    _markIncognito(payload.id);
  }

  // Clear any leftover document text selection from the previous session
  if (window.documentModule?.clearSelection) {
    try { window.documentModule.clearSelection(); } catch {}
  }
  currentSessionId = payload.id;
  Storage.set('lastSessionId', payload.id);
  history.replaceState(null, '', '#' + payload.id);

  // Reload sidebar to show the new session — await it so the session
  // is fully registered before the caller proceeds (prevents race conditions)
  await loadSessions().catch(() => {});
  return true;
}

export function hasPendingChat() { return !!_pendingChat; }
export function getPendingChat() { return _pendingChat; }
// Getters for external access
export function getCurrentSessionId() {
  return currentSessionId;
}

export function getSessions() {
  return sessions;
}

export function getCurrentModel() {
  const sess = sessions.find(x => x.id === currentSessionId);
  if (sess && sess.model) return sess.model;
  // Pending session not yet materialized — read from model picker label
  const label = document.getElementById('model-picker-label');
  return label ? label.textContent.trim() : null;
}

/** Endpoint URL serving the current (or pending) session's model. Used to
 *  decide whether a model is local (free) vs a billable cloud provider. */
export function getCurrentEndpointUrl() {
  const sess = sessions.find(x => x.id === currentSessionId);
  if (sess && sess.endpoint_url) return sess.endpoint_url;
  if (_pendingChat && _pendingChat.url) return _pendingChat.url;
  return null;
}

export function setCurrentSessionId(id) {
  _sessionNavToken++;
  currentSessionId = id;
  if (!id) {
    Storage.remove('lastSessionId');
    history.replaceState(null, '', window.location.pathname);
    document.querySelectorAll('.list-item.active-session, .session-item.active').forEach(el => {
      el.classList.remove('active-session', 'active');
    });
  }
}

// Session list keyboard navigation: arrows to move, Delete to delete
async function _onSessionListKeydown(e) {
  const item = e.target.closest('.list-item[data-session-id]');
  if (!item) return;

  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    // Get all visible session items across all containers
    const allItems = Array.from(document.querySelectorAll('#session-list .list-item[data-session-id]'));
    const idx = allItems.indexOf(item);
    if (idx < 0) return;
    const next = e.key === 'ArrowDown' ? allItems[idx + 1] : allItems[idx - 1];
    if (next) {
      next.focus();
      const sid = next.dataset.sessionId;
      if (sid) selectSession(sid);
    }
    return;
  }

  if (e.key === 'Delete' || e.key === 'Backspace') {
    e.preventDefault();
    const sid = item.dataset.sessionId;
    const s = sessions.find(x => x.id === sid);
    if (!s) return;
    if (s.is_important) {
      uiModule.showToast('Unfavorite before deleting');
      return;
    }
    const ok = await uiModule.styledConfirm('Delete this session?', { confirmText: 'Delete', danger: true });
    if (!ok) return;
    _sessionListFocused = true;
    (async () => {
      await fetch(`${API_BASE}/api/session/${s.id}`, { method: 'DELETE' });
      _deselectCurrentSession(s.id);
      await loadSessions();
    })();
    return;
  }

  if (e.key === 'Enter') {
    e.preventDefault();
    const sid = item.dataset.sessionId;
    if (sid) selectSession(sid);
    return;
  }
}

// Initialize drag sorting for sessions — uses the same dragSortModule as models
export function initDragSort() {
  if (!window.dragSortModule) return;
  const list = uiModule.el('session-list');
  if (!list) return;

  // Unfiled sessions (exclude items nested inside folders)
  window.dragSortModule.enable('session-list', '.list-item', {
    instanceKey: 'session-items',
    handleSelector: '.item-drag-handle',
    excludeSelector: '.session-folder-content .list-item',
    storageKey: 'session-order',
  });

  // Folder reordering
  window.dragSortModule.enable('session-list', '.session-folder', {
    instanceKey: 'session-folders',
    handleSelector: '.folder-drag-handle',
    onReorder: (items) => {
      const order = items.map(f => f.dataset.folderName).filter(Boolean);
      saveFolderOrder(order);
    },
  });

  // Sessions within each folder
  list.querySelectorAll('.session-folder-content').forEach((content, i) => {
    const id = 'session-folder-content-' + i;
    content.id = id;
    window.dragSortModule.enable(id, '.list-item', {
      handleSelector: '.item-drag-handle',
    });
  });
}

// Hash-based routing: navigate between sessions with browser back/forward
window.addEventListener('hashchange', () => {
  const hashId = window.location.hash.replace('#', '');
  if (hashId && hashId !== currentSessionId) {
    const target = sessions.find(s => s.id === hashId && !s.archived);
    if (target) selectSession(hashId);
  }
});

// ── Research indicator management ──
function _updateResearchDots() {
  document.querySelectorAll('.session-star[data-session-id]').forEach(function(star) {
    var sid = star.dataset.sessionId;
    var isRunning = _researchingSessions.has(sid) || _streamingSessions.has(sid);
    var isCompleted = _completedSessions.has(sid) && !isRunning;
    var listItem = star.closest('.list-item');
    star.classList.toggle('processing', isRunning);
    star.classList.toggle('notify', isCompleted);
    if (listItem) listItem.classList.toggle('stream-complete', isCompleted);

    if (isRunning || isCompleted) {
      star.style.opacity = '1';
    } else {
      star.style.opacity = '';
    }
  });
}

function _startResearchPolling() {
  if (_researchPollTimer) return;
  _researchPollTimer = setInterval(async function() {
    if (_researchingSessions.size === 0) {
      clearInterval(_researchPollTimer);
      _researchPollTimer = null;
      return;
    }
    for (var sid of _researchingSessions) {
      try {
        var res = await fetch(`${API_BASE}/api/research/status/${sid}`);
        if (!res.ok) { _researchingSessions.delete(sid); continue; }
        var data = await res.json();
        if (data.status !== 'running') {
          _researchingSessions.delete(sid);
        }
      } catch (e) {
        _researchingSessions.delete(sid);
      }
    }
    _updateResearchDots();
    if (_researchingSessions.size === 0 && _researchPollTimer) {
      clearInterval(_researchPollTimer);
      _researchPollTimer = null;
    }
  }, 5000);
}

export function markResearching(sessionId) {
  _researchingSessions.add(sessionId);
  _updateResearchDots();
  _updateRailNotifs();
  _startResearchPolling();
}

export function clearResearching(sessionId) {
  _researchingSessions.delete(sessionId);
  _updateResearchDots();
  _updateRailNotifs();
}

export function markStreaming(sessionId) {
  _streamingSessions.add(sessionId);
  _updateResearchDots();
  _updateRailNotifs();
}

export function clearStreaming(sessionId) {
  _streamingSessions.delete(sessionId);
  _updateResearchDots();
  _updateRailNotifs();
}

export function markStreamComplete(sessionId) {
  _researchingSessions.delete(sessionId);
  _streamingSessions.delete(sessionId);
  // Don't pulse if user is already viewing this session — they can see the response
  if (currentSessionId === sessionId) {
    _updateResearchDots();
    _updateRailNotifs();
    return;
  }
  _completedSessions.add(sessionId);
  _updateResearchDots();
  _updateRailNotifs();
  // Show notification dot on Chats section if collapsed
  const sessSection = document.getElementById('sessions-section');
  if (sessSection && sessSection.classList.contains('collapsed')) {
    const dot = document.getElementById('chats-notif-dot');
    if (dot) dot.style.display = 'inline-block';
  }
  // Safety net: re-apply after a tick in case a concurrent renderSessionList overwrites the DOM
  setTimeout(function() {
    if (_completedSessions.has(sessionId)) {
      _updateResearchDots();
    }
  }, 300);
}

// ── Rail notification dots ──
// Keep rail buttons lit when background work is happening / finished
function _updateRailNotifs() {
  // Research rail — pulsing while any session is researching
  const railResearch = document.getElementById('rail-research');
  if (railResearch) {
    // OR in the Deep Research panel's job state (set by panel.js)
    // so inline-research and panel-research both keep the rail lit.
    const researching = _researchingSessions.size > 0 || !!window._researchJobsActive;
    railResearch.classList.toggle('rail-notify', researching);
  }
  // Chats rail — show when a background stream completed
  const railChats = document.getElementById('rail-chats');
  if (railChats) {
    const sidebar = document.getElementById('sidebar');
    const sidebarHidden = sidebar && sidebar.classList.contains('hidden');
    const hasCompleted = _completedSessions.size > 0;
    railChats.classList.toggle('rail-notify', hasCompleted && sidebarHidden);
    railChats.classList.toggle('rail-notify-success', hasCompleted && sidebarHidden);
    // Store first completed session for click-to-open
    if (hasCompleted) {
      railChats.dataset.targetSession = [..._completedSessions][0];
    } else {
      delete railChats.dataset.targetSession;
    }
  }
  // Trigger rail sync so buttons become visible
  if (window._syncRailDynamic) window._syncRailDynamic();
}

/**
 * Check server for an active stream (survives page refresh).
 * If the server is still streaming for this session, show a spinner
 * and poll until done, then reload the session.
 */
async function _checkServerStream(sessionId) {
  try {
    // Skip if research is running — it has its own progress UI
    if (_researchingSessions.has(sessionId)) return;

    // Skip if the SSE reader is still actively connected — it handles rendering
    if (window.chatModule && window.chatModule.hasActiveStream && window.chatModule.hasActiveStream(sessionId)) return;

    const res = await fetch(`${API_BASE}/api/chat/stream_status/${sessionId}`);
    if (!res.ok) return; // 404 = no active stream
    const info = await res.json();
    if (info.status !== 'streaming') return;

    // Skip if this is a research stream — research has its own progress UI
    if (info.mode === 'research' || info.is_research) return;

    // Server is still streaming — show spinner and poll
    const box = document.getElementById('chat-history');
    if (!box) return;

    const holder = document.createElement('div');
    holder.className = 'msg msg-ai';
    holder.innerHTML = '<div class="body"></div>';
    const bodyDiv = holder.querySelector('.body');

    const spinnerMod = await import('./spinner.js');
    const spinner = spinnerMod.default.create('Generating response...', 'right');
    bodyDiv.appendChild(spinner.createElement());
    spinner.start();
    box.appendChild(holder);
    uiModule.scrollHistory();

    const pollId = setInterval(async () => {
      if (getCurrentSessionId() !== sessionId) {
        clearInterval(pollId);
        spinner.destroy();
        if (holder.parentNode) holder.remove();
        return;
      }
      try {
        const r = await fetch(`${API_BASE}/api/chat/stream_status/${sessionId}`);
        if (!r.ok || (await r.json()).status !== 'streaming') {
          clearInterval(pollId);
          spinner.destroy();
          if (holder.parentNode) holder.remove();
          // Reload session to show the completed response + docs
          selectSession(sessionId);
        }
      } catch (_) {
        clearInterval(pollId);
        spinner.destroy();
        if (holder.parentNode) holder.remove();
        selectSession(sessionId);
      }
    }, 1500);
  } catch (_) {
    // No stream active — nothing to do
  }
}

export function clearStreamComplete(sessionId) {
  _completedSessions.delete(sessionId);
  // Direct DOM cleanup in case _updateResearchDots misses it
  var item = document.querySelector(`.list-item[data-session-id="${sessionId}"]`);
  if (item) item.classList.remove('stream-complete');
  var star = document.querySelector(`.session-star[data-session-id="${sessionId}"]`);
  if (star) { star.classList.remove('notify', 'processing'); star.style.opacity = ''; }
  _updateResearchDots();
  _updateRailNotifs();
}

// Initialize dropdowns once DOM is ready
function _initAllDropdowns() {
  initModelPicker({
    getCurrentSessionId: () => currentSessionId,
    getSessions: () => sessions,
    getPendingChat: () => _pendingChat,
    setPendingChat: (v) => { _pendingChat = v; },
    createDirectChat,
  });
  _initDropdownDismiss();
  _initBulkSelect();
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initAllDropdowns);
} else {
  _initAllDropdowns();
}

// Shared global listener to close all session dropdowns on click-away or Escape
function _initDropdownDismiss() {
  document.addEventListener('click', (e) => {
    if (e.target.closest('.session-dropdown-menu')) return;
    document.querySelectorAll('.session-dropdown-menu').forEach(d => d.style.display = 'none');
  });
  // Watch the sidebar — when it's hidden (any path: hamburger, swipe, mobile
  // collapse), close any open session dropdowns so they don't orphan over
  // the page.
  const _sb = document.getElementById('sidebar');
  if (_sb) {
    new MutationObserver(() => {
      if (_sb.classList.contains('hidden')) {
        document.querySelectorAll('.session-dropdown-menu, .folder-submenu').forEach(d => d.style.display = 'none');
      }
    }).observe(_sb, { attributes: true, attributeFilter: ['class'] });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.session-dropdown-menu').forEach(d => d.style.display = 'none');
    }
  });
}

// ──────────────────────────────────────────────
// Shared: positioned dropdown menu
// ──────────────────────────────────────────────

/**
 * Show a dropdown menu anchored to a button, using the existing
 * .dropdown / .dropdown-item-compact / .session-dropdown-menu CSS.
 * Items: [{ label, action, danger? }]
 * Returns a close() function.
 */
function _showDropdown(anchorEl, items) {
  // Close any open archive dropdown
  document.querySelectorAll('.session-dropdown-menu.archive-dd').forEach(d => d.remove());

  const dd = document.createElement('div');
  dd.className = 'dropdown session-dropdown-menu archive-dd';
  for (const item of items) {
    const row = document.createElement('div');
    row.className = 'dropdown-item-compact' + (item.danger ? ' dropdown-item-danger' : '');
    row.innerHTML = '<span>' + item.label + '</span>';
    row.addEventListener('click', (e) => {
      e.stopPropagation();
      close();
      item.action();
    });
    dd.appendChild(row);
  }
  document.body.appendChild(dd);

  // Position using viewport coords (same pattern as session menus)
  const rect = anchorEl.getBoundingClientRect();
  dd.style.right = (window.innerWidth - rect.right) + 'px';
  dd.style.top = '-9999px';
  dd.style.display = 'block';
  const ddRect = dd.getBoundingClientRect();
  if (rect.bottom + 2 + ddRect.height > window.innerHeight) {
    dd.style.top = Math.max(2, rect.top - ddRect.height - 2) + 'px';
  } else {
    dd.style.top = (rect.bottom + 2) + 'px';
  }

  function close() { dd.remove(); }
  // Existing _initDropdownDismiss handles click-away + Escape for .session-dropdown-menu
  return close;
}


// ──────────────────────────────────────────────
// Archive Browser
// ──────────────────────────────────────────────

// All mutable archive state lives here; reset on each openArchive().
const _arc = { data: [], total: 0, search: '', offset: 0, sort: 'recent', model: '', debounce: null, selectMode: false, selected: new Set(), allModelCounts: null };

function _arcRelativeTime(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

// ── Actions (pure side-effects, no DOM creation) ──

// Peek at an archived session — load its history without unarchiving
let _peekingSessionId = null;

async function _arcPeekOpen(sid) {
  try {
    _peekingSessionId = sid;
    closeArchive();
    // Load history directly without unarchiving
    const res = await fetch(`${API_BASE}/api/history/${sid}`);
    const data = await res.json();
    const history = data.history || [];

    // Set as current session so chat renders
    currentSessionId = sid;

    // Find the archived session metadata
    const meta = _arc.data.find(s => s.id === sid);
    const metaEl = document.getElementById('current-meta');
    if (metaEl) metaEl.textContent = (meta?.name || 'Archived') + ' (archived)';

    // Render the chat history
    const chatBox = document.getElementById('chat-history');
    if (chatBox) chatBox.innerHTML = '';
    if (window.chatModule && window.chatModule.hideWelcomeScreen) window.chatModule.hideWelcomeScreen();

    const addMsg = window.chatModule && window.chatModule.addMessage;
    if (addMsg) {
      for (const msg of history) {
        if (msg.role === 'user' || msg.role === 'assistant') {
          const model = String((msg.metadata && msg.metadata.model) || '');
          const content = typeof msg.content === 'string' ? msg.content : (Array.isArray(msg.content) ? msg.content : String(msg.content || ''));
          try { addMsg(msg.role, content, model, msg.metadata || null); } catch (e) { console.warn('Failed to render message:', e); }
        }
      }
    }
    if (window.uiModule) window.uiModule.scrollHistory();
  } catch (e) {
    console.error('Peek open failed:', e);
    uiModule.showError('Failed to open archived session');
  }
}

// When navigating away from a peeked session, just clear the state
function _checkPeekCleanup(newSessionId) {
  if (_peekingSessionId && _peekingSessionId !== newSessionId) {
    _peekingSessionId = null;
  }
}

async function _arcRestore(sid) {
  try {
    const res = await fetch(`${API_BASE}/api/session/${sid}/unarchive`, { method: 'POST' });
    if (!res.ok) throw new Error('Failed');
    _arcRemove(sid);
    _arcRefreshUI();
    uiModule.showToast('Session restored');
    loadSessions();
  } catch { uiModule.showError('Failed to restore session'); }
}

async function _arcDelete(sid) {
  if (!await window.styledConfirm('Delete this session permanently?', { confirmText: 'Delete', danger: true })) return;
  try {
    const res = await fetch(`${API_BASE}/api/session/${sid}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed');
    await _animateSessionRowsRemoving([sid], '#archive-grid .archive-row[data-session-id]');
    _arcRemove(sid);
    _arcRefreshUI();
    uiModule.showToast('Session deleted');
  } catch { uiModule.showError('Failed to delete session'); }
}

function _arcRemove(sid) {
  _arc.data = _arc.data.filter(x => x.id !== sid);
  _arc.total--;
  _arc.selected.delete(sid);
}

async function _arcBulkRestore() {
  const ids = [..._arc.selected];
  if (!ids.length) return;
  for (const sid of ids) {
    try {
      await fetch(`${API_BASE}/api/session/${sid}/unarchive`, { method: 'POST' });
      _arcRemove(sid);
    } catch {}
  }
  _arc.selected.clear();
  _arcRefreshUI();
  uiModule.showToast(`${ids.length} session${ids.length > 1 ? 's' : ''} restored`);
  loadSessions();
}

async function _arcBulkDelete() {
  const ids = [..._arc.selected];
  if (!ids.length) return;
  const ok = await uiModule.styledConfirm(`Delete ${ids.length} session${ids.length > 1 ? 's' : ''} permanently?`, { confirmText: 'Delete', danger: true });
  if (!ok) return;
  const deletedIds = [];
  for (const sid of ids) {
    try {
      const res = await fetch(`${API_BASE}/api/session/${sid}`, { method: 'DELETE' });
      if (res.ok) {
        deletedIds.push(sid);
        _arcRemove(sid);
      }
    } catch {}
  }
  await _animateSessionRowsRemoving(deletedIds, '#archive-grid .archive-row[data-session-id]');
  _arc.selected.clear();
  _arcRefreshUI();
  uiModule.showToast(`${deletedIds.length} session${deletedIds.length > 1 ? 's' : ''} deleted`);
}

function _arcToggleSelectMode() {
  _arc.selectMode = !_arc.selectMode;
  _arc.selected.clear();
  _arcRefreshUI();
}

function _arcUpdateBulkBar() {
  const bar = document.getElementById('archive-bulk-bar');
  const count = document.getElementById('archive-selected-count');
  const selectBtn = document.getElementById('archive-select-btn');
  if (bar) bar.classList.toggle('hidden', !_arc.selectMode);
  if (count) count.textContent = `${_arc.selected.size} selected`;
  if (selectBtn) {
    selectBtn.textContent = _arc.selectMode ? 'Cancel' : 'Select';
    selectBtn.classList.toggle('active', _arc.selectMode);
  }
}

// ── Data fetching ──

async function _arcFetch(append) {
  if (!append) _arc.offset = 0;
  const params = new URLSearchParams({ offset: String(_arc.offset), limit: '20', sort: _arc.sort });
  if (_arc.search) params.set('search', _arc.search);
  if (_arc.model) params.set('model', _arc.model);
  try {
    const res = await fetch(`${API_BASE}/api/sessions/archived?${params}`);
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    _arc.data = append ? _arc.data.concat(data.sessions) : data.sessions;
    _arc.total = data.total;
    // Cache model counts from unfiltered first fetch
    if (!_arc.allModelCounts && !_arc.model && !_arc.search) {
      const counts = {};
      _arc.data.forEach(s => {
        const m = (s.model || '').split('/').pop();
        if (m) counts[m] = (counts[m] || 0) + 1;
      });
      _arc.allModelCounts = { counts, total: _arc.total };
    }
    _arcRefreshUI();
  } catch (e) {
    console.error('Archive fetch failed:', e);
  }
}

// ── Rendering (dumb — reads _arc, writes DOM) ──

function _arcRefreshUI() {
  _arcRenderStats();
  _arcRenderChips();
  _arcRenderGrid();
  _arcRenderLoadMore();
  _arcUpdateBulkBar();
}

function _arcRenderStats() {
  const el = document.getElementById('archive-stats');
  if (el) el.textContent = _arc.total ? `${_arc.total}` : '';
}

function _arcRenderChips() {
  const el = document.getElementById('archive-chips');
  if (!el) return;
  // Use cached counts so chips don't disappear when filtering
  const cached = _arc.allModelCounts;
  if (!cached) return;
  const modelCounts = cached.counts;
  const models = Object.keys(modelCounts).sort();
  if (models.length < 2) { el.innerHTML = ''; return; }
  el.innerHTML = '';
  const mkChip = (label, value, count) => {
    const chip = document.createElement('button');
    chip.className = 'doclib-chip' + (_arc.model === value ? ' active' : '');
    chip.textContent = `${label} (${count})`;
    chip.addEventListener('click', () => { _arc.model = (_arc.model === value ? '' : value); _arcFetch(false); });
    el.appendChild(chip);
  };
  mkChip('All', '', cached.total);
  models.forEach(m => mkChip(m, m, modelCounts[m]));
}

function _arcRenderCard(s) {
  const card = document.createElement('div');
  card.className = 'memory-item archive-row' + (_arc.selected.has(s.id) ? ' selected' : '');
  card.dataset.sessionId = s.id;
  const modelShort = uiModule.esc((s.model || '').split('/').pop());
  const msgCount = s.message_count || 0;
  const checkboxHtml = _arc.selectMode
    ? `<input type="checkbox" class="memory-select-cb archive-checkbox" data-sid="${s.id}" ${_arc.selected.has(s.id) ? 'checked' : ''}>`
    : '';

  card.innerHTML = `
    ${checkboxHtml}
    <div style="flex:1;min-width:0;">
      <div class="memory-item-title">${uiModule.esc(s.name || 'Untitled')}</div>
      <div class="memory-item-meta" style="font-size:10px;opacity:0.4;margin-top:2px;">
        <span>${modelShort || 'no model'}</span>
        <span>\u00b7</span>
        <span>${msgCount} msg${msgCount !== 1 ? 's' : ''}</span>
        <span>\u00b7</span>
        <span>${_arcRelativeTime(s.updated_at)}</span>
      </div>
    </div>
    <div class="memory-item-actions">
      <button class="memory-item-btn archive-menu-btn" title="Actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>
    </div>
  `;

  const checkbox = card.querySelector('.archive-checkbox');
  if (checkbox) {
    checkbox.addEventListener('change', (e) => {
      e.stopPropagation();
      if (e.target.checked) _arc.selected.add(s.id);
      else _arc.selected.delete(s.id);
      card.classList.toggle('selected', e.target.checked);
      _arcUpdateBulkBar();
    });
  }
  card.querySelector('.archive-menu-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    _showDropdown(e.currentTarget, [
      { label: 'Open', action: () => _arcPeekOpen(s.id) },
      { label: 'Restore', action: () => _arcRestore(s.id) },
      { label: 'Delete', action: () => _arcDelete(s.id), danger: true },
    ]);
  });
  card.addEventListener('click', () => {
    if (_arc.selectMode) {
      if (_arc.selected.has(s.id)) _arc.selected.delete(s.id);
      else _arc.selected.add(s.id);
      const cb = card.querySelector('.archive-checkbox');
      if (cb) cb.checked = _arc.selected.has(s.id);
      card.classList.toggle('selected', _arc.selected.has(s.id));
      _arcUpdateBulkBar();
    } else {
      _arcPeekOpen(s.id);
    }
  });
  return card;
}

function _arcRenderGrid() {
  const grid = document.getElementById('archive-grid');
  if (!grid) return;
  if (_arc.data.length === 0) {
    grid.innerHTML = '<div class="doclib-empty">No archived sessions</div>';
    return;
  }
  grid.innerHTML = '';
  for (const s of _arc.data) grid.appendChild(_arcRenderCard(s));
}

function _arcRenderLoadMore() {
  const btn = document.getElementById('archive-load-more');
  if (!btn) return;
  btn.style.display = _arc.data.length < _arc.total ? '' : 'none';
}


// ── Unified Library Modal (Chats / Documents / Archive) ──

const _lib = { tab: 'chats', search: '', sort: 'recent', debounce: null, selectMode: false, selected: new Set() };

export function openLibrary(defaultTab) {
  // Delegate everything to the document module's library (has tabs for Chats/Documents/Archive)
  if (window.documentModule && window.documentModule.openLibrary) {
    window.documentModule.openLibrary({ tab: defaultTab || 'documents' });
    return;
  }
  if (document.getElementById('library-modal')) return;
  Object.assign(_lib, { tab: defaultTab || 'chats', search: '', sort: 'recent', debounce: null, selectMode: false, selected: new Set() });

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'library-modal';
  modal.innerHTML = `
    <div class="modal-content doclib-modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>Library <span id="lib-stats" style="font-size:0.8em;opacity:0.5;font-weight:normal;margin-left:4px"></span></h4>
        <button class="close-btn" id="lib-close">✖</button>
      </div>
      <div class="modal-body">
        <div class="lib-tabs" id="lib-tabs">
          <button class="lib-tab${_lib.tab === 'chats' ? ' active' : ''}" data-lib-tab="chats">Chats</button>
          <button class="lib-tab${_lib.tab === 'documents' ? ' active' : ''}" data-lib-tab="documents">Documents</button>
          <button class="lib-tab${_lib.tab === 'archive' ? ' active' : ''}" data-lib-tab="archive">Archive</button>
          <button class="lib-tab${_lib.tab === 'research' ? ' active' : ''}" data-lib-tab="research">Research</button>
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
          <select class="memory-sort-select" id="lib-sort">
            <option value="recent">Recent</option>
            <option value="oldest">Oldest</option>
            <option value="most-messages">Most messages</option>
            <option value="alpha">A\u2013Z</option>
          </select>
          <input type="text" class="memory-search-input" id="lib-search" placeholder="Filter\u2026" style="flex:1;" />
          <button class="memory-toolbar-btn" id="lib-select-btn" title="Select">Select</button>
        </div>
        <div class="memory-bulk-bar hidden" id="lib-bulk-bar">
          <label class="memory-bulk-check-all"><input type="checkbox" id="lib-select-all"> All</label>
          <span id="lib-selected-count" style="color:color-mix(in srgb, var(--fg) 50%, transparent);font-size:10px;flex:1;">0 selected</span>
          <button class="memory-toolbar-btn" id="lib-bulk-action1"></button>
          <button class="memory-toolbar-btn danger" id="lib-bulk-delete">Delete</button>
        </div>
        <div class="doclib-grid archive-list" id="lib-grid"></div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  // Draggable
  const _clContent = modal.querySelector('.modal-content');
  const _clHeader = modal.querySelector('.modal-header');
  if (themeModule && themeModule.makeDraggable && _clContent && _clHeader) {
    themeModule.makeDraggable(_clContent, _clHeader);
  }

  document.getElementById('lib-close').addEventListener('click', closeLibrary);

  // Tab switching
  modal.querySelectorAll('.lib-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      // Documents tab — open the document module's library (has expand/preview)
      if (tab.dataset.libTab === 'documents' && window.documentModule && window.documentModule.openLibrary) {
        closeLibrary();
        window.documentModule.openLibrary();
        return;
      }
      _lib.tab = tab.dataset.libTab;
      _lib.search = '';
      _lib.selectMode = false;
      _lib.selected.clear();
      modal.querySelectorAll('.lib-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('lib-search').value = '';
      document.getElementById('lib-bulk-bar').classList.add('hidden');
      // Update bulk action button label based on tab
      const action1 = document.getElementById('lib-bulk-action1');
      if (_lib.tab === 'archive') { action1.textContent = 'Restore'; }
      else if (_lib.tab === 'chats') { action1.textContent = 'Archive'; }
      else if (_lib.tab === 'research') { action1.textContent = 'Open Report'; }
      else { action1.textContent = 'Export'; }
      _renderLibGrid();
    });
  });

  // Set initial bulk action label
  const _initAction = document.getElementById('lib-bulk-action1');
  if (_initAction) _initAction.textContent = _lib.tab === 'archive' ? 'Restore' : _lib.tab === 'documents' ? 'Export' : 'Archive';

  document.getElementById('lib-sort').addEventListener('change', () => { _lib.sort = document.getElementById('lib-sort').value; _renderLibGrid(); });
  document.getElementById('lib-search').addEventListener('input', (e) => {
    clearTimeout(_lib.debounce);
    _lib.debounce = setTimeout(() => { _lib.search = e.target.value.trim().toLowerCase(); _renderLibGrid(); }, 200);
  });

  // Select mode
  document.getElementById('lib-select-btn').addEventListener('click', () => {
    _lib.selectMode = !_lib.selectMode;
    _lib.selected.clear();
    document.getElementById('lib-bulk-bar').classList.toggle('hidden', !_lib.selectMode);
    _renderLibGrid();
  });
  document.getElementById('lib-select-all').addEventListener('change', (e) => {
    const checked = e.target.checked;
    document.querySelectorAll('#lib-grid .memory-select-cb').forEach(cb => { cb.checked = checked; });
    document.querySelectorAll('#lib-grid .doclib-card').forEach(card => {
      const id = card.dataset.sessionId || card.dataset.docId;
      if (id) { if (checked) _lib.selected.add(id); else _lib.selected.delete(id); }
    });
    _updateLibCount();
  });

  // Bulk action 1 (Archive/Restore/Export)
  document.getElementById('lib-bulk-action1').addEventListener('click', async () => {
    if (_lib.tab === 'chats') {
      for (const sid of _lib.selected) await fetch(`${API_BASE}/api/session/${sid}/archive`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
      uiModule.showToast(`Archived ${_lib.selected.size} sessions`);
    } else if (_lib.tab === 'archive') {
      for (const sid of _lib.selected) await fetch(`${API_BASE}/api/session/${sid}/restore`, { method: 'POST' });
      uiModule.showToast(`Restored ${_lib.selected.size} sessions`);
    }
    _lib.selected.clear();
    _lib.selectMode = false;
    document.getElementById('lib-bulk-bar').classList.add('hidden');
    await loadSessions();
    _renderLibGrid();
  });

  // Bulk delete
  document.getElementById('lib-bulk-delete').addEventListener('click', async () => {
    if (!await uiModule.styledConfirm(`Delete ${_lib.selected.size} items?`, { confirmText: 'Delete', danger: true })) return;
    if (_lib.tab === 'chats' || _lib.tab === 'archive') {
      for (const sid of _lib.selected) await fetch(`${API_BASE}/api/session/${sid}`, { method: 'DELETE' });
    } else if (_lib.tab === 'documents') {
      for (const did of _lib.selected) await fetch(`${API_BASE}/api/document/${did}`, { method: 'DELETE' });
    } else if (_lib.tab === 'research') {
      for (const rid of _lib.selected) await fetch(`${API_BASE}/api/research/${rid}`, { method: 'DELETE' });
    }
    _lib.selected.clear();
    _lib.selectMode = false;
    document.getElementById('lib-bulk-bar').classList.add('hidden');
    await loadSessions();
    _renderLibGrid();
  });

  _renderLibGrid();
}

function _updateLibCount() {
  const el = document.getElementById('lib-selected-count');
  if (el) el.textContent = `${_lib.selected.size} selected`;
}

function _renderLibGrid() {
  const grid = document.getElementById('lib-grid');
  if (!grid) return;

  if (_lib.tab === 'chats') _renderLibChats(grid);
  else if (_lib.tab === 'archive') _renderLibArchive(grid);
  else if (_lib.tab === 'documents') _renderLibDocuments(grid);
  else if (_lib.tab === 'research') _renderLibResearch(grid);
}

function _renderLibChats(grid) {
  if (!sessions || !sessions.length) {
    grid.innerHTML = '<div class="doclib-empty">No sessions loaded</div>';
    return;
  }
  let filtered = sessions.filter(s => !s.archived);
  if (_lib.search) {
    const q = _lib.search;
    filtered = filtered.filter(s => (s.name || '').toLowerCase().includes(q) || (s.model || '').toLowerCase().includes(q));
  }
  if (_lib.sort === 'oldest') filtered.sort((a, b) => (a.created_at || '') > (b.created_at || '') ? 1 : -1);
  else if (_lib.sort === 'most-messages') filtered.sort((a, b) => (b.message_count || 0) - (a.message_count || 0));
  else if (_lib.sort === 'alpha') filtered.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  else filtered.sort((a, b) => (b.updated_at || '') > (a.updated_at || '') ? 1 : -1);

  const stats = document.getElementById('lib-stats');
  if (stats) stats.textContent = `(${filtered.length})`;

  if (!filtered.length) { grid.innerHTML = '<div class="doclib-empty">No chats found</div>'; return; }
  grid.innerHTML = '';
  for (const s of filtered) {
    const card = _buildLibCard(s.id, s.name || 'Untitled', s.message_count || 0, (s.model || '').split('/').pop(), s.updated_at, s.id === currentSessionId);
    card.addEventListener('click', (e) => {
      if (e.target.closest('.archive-menu-btn,.memory-select-cb')) return;
      if (_lib.selectMode) { _toggleLibSelect(card, s.id); return; }
      closeLibrary(); selectSession(s.id);
    });
    card.querySelector('.archive-menu-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      _showDropdown(e.currentTarget, [
        { label: 'Open', action: () => { closeLibrary(); selectSession(s.id); } },
        { label: 'Archive', action: async () => { await fetch(`${API_BASE}/api/session/${s.id}/archive`, { method: 'POST', headers: { 'Content-Type': 'application/json' } }); await loadSessions(); _renderLibGrid(); } },
        { label: 'Delete', action: async () => { if (!await uiModule.styledConfirm('Delete?', { confirmText: 'Delete', danger: true })) return; await fetch(`${API_BASE}/api/session/${s.id}`, { method: 'DELETE' }); await loadSessions(); _renderLibGrid(); }, danger: true },
      ]);
    });
    grid.appendChild(card);
  }
}

async function _renderLibArchive(grid) {
  grid.innerHTML = '';
  grid.appendChild(spinnerModule.createLoadingRow('Loading…'));
  try {
    const params = new URLSearchParams({ limit: '50', sort: _lib.sort === 'most-messages' ? 'messages' : _lib.sort });
    if (_lib.search) params.set('search', _lib.search);
    const res = await fetch(`${API_BASE}/api/sessions/archived?${params}`);
    const data = await res.json();
    const items = data.sessions || [];
    const stats = document.getElementById('lib-stats');
    if (stats) stats.textContent = `(${data.total || items.length})`;
    if (!items.length) { grid.innerHTML = '<div class="doclib-empty">No archived sessions</div>'; return; }
    grid.innerHTML = '';
    for (const s of items) {
      const card = _buildLibCard(s.id, s.name || 'Untitled', s.message_count || 0, (s.model || '').split('/').pop(), s.updated_at);
      card.addEventListener('click', (e) => {
        if (e.target.closest('.archive-menu-btn,.memory-select-cb')) return;
        if (_lib.selectMode) { _toggleLibSelect(card, s.id); return; }
      });
      card.querySelector('.archive-menu-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        _showDropdown(e.currentTarget, [
          { label: 'Restore', action: async () => { await fetch(`${API_BASE}/api/session/${s.id}/restore`, { method: 'POST' }); await loadSessions(); _renderLibGrid(); } },
          { label: 'Delete', action: async () => { if (!await uiModule.styledConfirm('Delete?', { confirmText: 'Delete', danger: true })) return; await fetch(`${API_BASE}/api/session/${s.id}`, { method: 'DELETE' }); _renderLibGrid(); }, danger: true },
        ]);
      });
      grid.appendChild(card);
    }
  } catch (e) { console.error('Library archive error:', e); grid.innerHTML = '<div class="doclib-empty">Failed to load archive</div>'; }
}

async function _renderLibDocuments(grid) {
  grid.innerHTML = '';
  grid.appendChild(spinnerModule.createLoadingRow('Loading…'));
  try {
    const params = new URLSearchParams({ limit: '50', sort: _lib.sort });
    if (_lib.search) params.set('search', _lib.search);
    const res = await fetch(`${API_BASE}/api/documents/library?${params}`);
    const data = await res.json();
    const docs = data.documents || [];
    const stats = document.getElementById('lib-stats');
    if (stats) stats.textContent = `(${data.total || docs.length})`;
    if (!docs.length) { grid.innerHTML = '<div class="doclib-empty">No documents found</div>'; return; }
    grid.innerHTML = '';
    for (const d of docs) {
      const card = _buildLibCard(d.id, d.title || 'Untitled', d.version_count || 1, d.language || 'text', d.updated_at, false, true);
      card.dataset.docId = d.id;
      card.addEventListener('click', (e) => {
        if (e.target.closest('.archive-menu-btn,.memory-select-cb')) return;
        if (_lib.selectMode) { _toggleLibSelect(card, d.id); return; }
        // Open document in its session
        if (d.session_id && window.documentModule) {
          closeLibrary();
          selectSession(d.session_id);
          setTimeout(() => { if (window.documentModule.loadSessionDocs) window.documentModule.loadSessionDocs(d.session_id); }, 300);
        }
      });
      card.querySelector('.archive-menu-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        _showDropdown(e.currentTarget, [
          { label: 'Open', action: () => { if (d.session_id) { closeLibrary(); selectSession(d.session_id); } } },
          { label: 'Delete', action: async () => { if (!await uiModule.styledConfirm('Delete?', { confirmText: 'Delete', danger: true })) return; await fetch(`${API_BASE}/api/document/${d.id}`, { method: 'DELETE' }); _renderLibGrid(); }, danger: true },
        ]);
      });
      grid.appendChild(card);
    }
  } catch (e) { console.error('Library documents error:', e); grid.innerHTML = '<div class="doclib-empty">Failed to load documents</div>'; }
}

async function _renderLibResearch(grid) {
  grid.innerHTML = '';
  grid.appendChild(spinnerModule.createLoadingRow('Loading research…'));
  try {
    const params = new URLSearchParams({ limit: '50', sort: _lib.sort });
    if (_lib.search) params.set('search', _lib.search);
    const res = await fetch(`${API_BASE}/api/research/library?${params}`);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    const items = data.research || [];
    const statsEl = document.getElementById('lib-stats');
    if (statsEl) statsEl.textContent = `${data.total || 0} research`;
    grid.innerHTML = '';
    if (!items.length) {
      grid.innerHTML = '<div class="doclib-empty">No research found</div>';
      return;
    }
    for (const item of items) {
      const meta = [
        item.duration || '',
        item.rounds ? item.rounds + ' rounds' : '',
      ].filter(Boolean).join(' \u00b7 ');
      const card = _buildLibCard(
        item.id, item.query || '(untitled)', item.source_count || 0,
        meta, item.completed_at ? new Date(item.completed_at * 1000).toISOString() : '',
        false, false,
      );
      const metaEl = card.querySelector('.memory-item-meta');
      if (metaEl) metaEl.textContent = metaEl.textContent.replace(/\d+ msgs?/, (item.source_count || 0) + ' sources');
      card.addEventListener('click', (e) => {
        if (e.target.closest('.archive-menu-btn') || e.target.closest('.memory-select-cb')) return;
        window.open(`${API_BASE}/api/research/report/${item.id}`, '_blank');
      });
      const menuBtn = card.querySelector('.archive-menu-btn');
      if (menuBtn) {
        menuBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          _showDropdown(e.currentTarget, [
            { label: 'Open Report', action: () => window.open(`${API_BASE}/api/research/report/${item.id}`, '_blank') },
            { label: 'Re-run', action: () => {
              const modal = document.getElementById('library-modal');
              if (modal) modal.style.display = 'none';
              const msgInput = document.getElementById('message');
              if (msgInput) { msgInput.value = item.query; msgInput.focus(); }
              uiModule.showToast('Toggle Research and send to re-run');
            }},
            { label: 'Delete', danger: true, action: async () => {
              if (!await window.styledConfirm('Delete this research?', { confirmText: 'Delete', danger: true })) return;
              await fetch(`${API_BASE}/api/research/${item.id}`, { method: 'DELETE' });
              _renderLibGrid();
            }},
          ]);
        });
      }
      grid.appendChild(card);
    }
  } catch (e) { console.error('Library research error:', e); grid.innerHTML = '<div class="doclib-empty">Failed to load research</div>'; }
}

function _buildLibCard(id, title, count, meta, time, isActive, isDoc) {
  const card = document.createElement('div');
  card.className = 'memory-item';
  card.dataset.sessionId = id;
  if (isDoc) card.dataset.docId = id;
  const cbHtml = _lib.selectMode ? `<input type="checkbox" class="memory-select-cb"${_lib.selected.has(id) ? ' checked' : ''}>` : '';
  const metaParts = [];
  if (meta) metaParts.push(uiModule.esc(meta));
  metaParts.push(isDoc ? 'v' + count : count + ' msg' + (count !== 1 ? 's' : ''));
  if (time) metaParts.push(_arcRelativeTime(time));
  card.innerHTML = `
    ${cbHtml}
    <div style="flex:1;min-width:0;">
      <div class="memory-item-title"${isActive ? ' style="color:var(--accent);"' : ''}>${uiModule.esc(title)}</div>
      <div class="memory-item-meta" style="font-size:10px;opacity:0.4;margin-top:2px;">${metaParts.join(' \u00b7 ')}</div>
    </div>
    <div class="memory-item-actions">
      <button class="memory-item-btn archive-menu-btn" title="Actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>
    </div>
  `;
  const cb = card.querySelector('.memory-select-cb');
  if (cb) {
    cb.addEventListener('click', (e) => e.stopPropagation());
    cb.addEventListener('change', () => { if (cb.checked) _lib.selected.add(id); else _lib.selected.delete(id); _updateLibCount(); });
  }
  return card;
}

function _toggleLibSelect(card, id) {
  const cb = card.querySelector('.memory-select-cb');
  if (cb) { cb.checked = !cb.checked; if (cb.checked) _lib.selected.add(id); else _lib.selected.delete(id); _updateLibCount(); }
}

export function closeLibrary() {
  const modal = document.getElementById('library-modal');
  if (modal) {
    const content = modal.querySelector('.modal-content');
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => modal.remove(), { once: true });
      setTimeout(() => { if (modal.parentElement) modal.remove(); }, 250);
    } else {
      modal.remove();
    }
  }
}

export function openArchive() {
  if (document.getElementById('archive-modal')) return;
  Object.assign(_arc, { data: [], total: 0, search: '', offset: 0, sort: 'recent', model: '', debounce: null, selectMode: false, selected: new Set(), allModelCounts: null });

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'archive-modal';
  modal.innerHTML = `
    <div class="modal-content doclib-modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>Archive <span id="archive-stats" style="font-size:0.8em;opacity:0.5;font-weight:normal;margin-left:4px"></span></h4>
        <button class="close-btn" id="archive-close">✖</button>
      </div>
      <div class="modal-body">
        <div class="doclib-chips" id="archive-chips"></div>
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
          <select class="memory-sort-select" id="archive-sort">
            <option value="recent">Recent</option>
            <option value="oldest">Oldest</option>
            <option value="most-messages">Most messages</option>
            <option value="alpha">A\u2013Z</option>
          </select>
          <input type="text" class="memory-search-input" id="archive-search" placeholder="Filter\u2026" style="flex:1;" />
          <button class="memory-toolbar-btn" id="archive-select-btn" title="Select sessions">Select</button>
        </div>
        <div class="memory-bulk-bar hidden" id="archive-bulk-bar">
          <label class="memory-bulk-check-all"><input type="checkbox" id="archive-select-all"> All</label>
          <span id="archive-selected-count" style="color:color-mix(in srgb, var(--fg) 50%, transparent);font-size:10px;flex:1;">0 selected</span>
          <button class="memory-toolbar-btn" id="archive-bulk-restore">Restore</button>
          <button class="memory-toolbar-btn danger" id="archive-bulk-delete">Delete</button>
        </div>
        <div class="doclib-grid archive-list" id="archive-grid"></div>
        <button class="doclib-load-more" id="archive-load-more" style="display:none">Load more</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  // Make draggable via header
  const _arcContent = modal.querySelector('.modal-content');
  const _arcHeader = modal.querySelector('.modal-header');
  if (themeModule && themeModule.makeDraggable && _arcContent && _arcHeader) {
    themeModule.makeDraggable(_arcContent, _arcHeader);
  }

  document.getElementById('archive-close').addEventListener('click', closeArchive);
  document.getElementById('archive-sort').addEventListener('change', (e) => { _arc.sort = e.target.value; _arcFetch(false); });
  document.getElementById('archive-search').addEventListener('input', (e) => {
    clearTimeout(_arc.debounce);
    _arc.debounce = setTimeout(() => { _arc.search = e.target.value.trim(); _arcFetch(false); }, 300);
  });
  document.getElementById('archive-load-more').addEventListener('click', () => { _arc.offset = _arc.data.length; _arcFetch(true); });
  document.getElementById('archive-select-btn').addEventListener('click', _arcToggleSelectMode);
  document.getElementById('archive-bulk-restore').addEventListener('click', _arcBulkRestore);
  document.getElementById('archive-bulk-delete').addEventListener('click', _arcBulkDelete);
  document.getElementById('archive-select-all').addEventListener('change', (e) => {
    if (e.target.checked) _arc.data.forEach(s => _arc.selected.add(s.id));
    else _arc.selected.clear();
    _arcRefreshUI();
  });
  modal.addEventListener('click', (e) => { if (uiModule.isTouchInsideModal()) return; if (e.target === modal) closeArchive(); });

  _arcFetch(false);
}

export function closeArchive() {
  const modal = document.getElementById('archive-modal');
  if (modal) {
    const content = modal.querySelector('.modal-content');
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => modal.remove(), { once: true });
      setTimeout(() => { if (modal.parentElement) modal.remove(); }, 250);
    } else {
      modal.remove();
    }
  }
}

/** Update has_documents flag for a session and re-render the sidebar icon */
export function getSortMode() { return _sortMode; }
export function setSortMode(mode) {
  _sortMode = mode || null;
  if (mode) Storage.set('odysseus-session-sort', mode);
  else Storage.remove('odysseus-session-sort');
  renderSessionList();
}

export function setSessionHasDocs(sessionId, hasDocs) {
  const s = sessions.find(s => s.id === sessionId);
  if (s && s.has_documents !== hasDocs) {
    s.has_documents = hasDocs;
    renderSessionList();
  }
}

// Export all functions to window for use in main app
const sessionModule = {
  initDependencies,
  renderSessionList,
  loadSessions,
  selectSession,
  createDirectChat,
  materializePendingSession,
  hasPendingChat,
  getPendingChat,
  getCurrentSessionId,
  getSessions,
  getCurrentModel,
  getCurrentEndpointUrl,
  setCurrentSessionId,
  initDragSort,
  updateModelPicker,
  markResearching,
  clearResearching,
  markStreaming,
  clearStreaming,
  markStreamComplete,
  clearStreamComplete,
  openLibrary,
  closeLibrary,
  openArchive,
  closeArchive,
  setSessionHasDocs,
  getSortMode,
  setSortMode
};

export { updateModelPicker };

export default sessionModule;
