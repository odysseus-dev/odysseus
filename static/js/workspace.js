// static/js/workspace.js
//
// Workspace picker: browse server directories in a draggable modal, choose a
// folder, and show it as a removable pill in the chat input bar. While set, the
// chat request sends `workspace` so the agent's file/shell tools are confined
// to that folder (see routes/chat_routes.py + src/tool_execution.py).

import Storage, { KEYS } from './storage.js';
import uiModule from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;
// Same folder glyph as the overflow menu item + pill (not an emoji).
const _FOLDER_SVG = '<svg class="workspace-row-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
// Star glyph for the "saved workspaces" rows.
const _STAR_SVG = '<svg class="workspace-row-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
// Per-user prefs key holding the saved-workspaces array (server-side, persists
// across devices). Each entry is { name, path }.
const _SAVED_PREF = 'saved-workspaces';
let _modal = null;
let _curPath = '';
let _saved = [];

export function getWorkspace() {
  return Storage.get(KEYS.WORKSPACE, '') || '';
}

function _basename(p) {
  if (!p) return '';
  // Handle both POSIX (/) and Windows (\) separators.
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

// ── Saved workspaces (per-user prefs) ──────────────────────────────
// Persisted server-side via /api/prefs so bookmarks follow the user across
// devices and survive a localStorage clear, unlike the active-workspace pill.

async function _loadSaved() {
  try {
    const res = await fetch(`${API_BASE}/api/prefs/${_SAVED_PREF}`, { credentials: 'same-origin' });
    if (!res.ok) return [];
    const data = await res.json();
    const v = data && data.value;
    // Tolerate a never-set pref (null) or any legacy/corrupt shape.
    return Array.isArray(v) ? v.filter((e) => e && typeof e.path === 'string' && e.path) : [];
  } catch (_) {
    return [];
  }
}

async function _persistSaved(list) {
  const res = await fetch(`${API_BASE}/api/prefs/${_SAVED_PREF}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ value: list }),
  });
  if (!res.ok) throw new Error(`save failed: ${res.status}`);
}

function _isSaved(path) {
  return _saved.some((e) => e.path === path);
}

// Star/un-star a folder. Persists to prefs and returns the new saved state so
// callers can update the clicked folder's star icon in place.
async function _toggleFav(path, name) {
  if (!path) return _isSaved(path);
  const wasSaved = _isSaved(path);
  const next = wasSaved
    ? _saved.filter((e) => e.path !== path)
    : _saved.concat([{ name: name || _basename(path), path }]);
  try {
    await _persistSaved(next);
    _saved = next;
    if (uiModule && uiModule.showToast) {
      uiModule.showToast(wasSaved ? `Unsaved: ${_basename(path)}` : `Saved: ${_basename(path)}`);
    }
  } catch (e) {
    if (uiModule && uiModule.showError) uiModule.showError('Could not update saved workspaces');
    return wasSaved; // unchanged
  }
  return !wasSaved;
}

export function syncWorkspaceIndicator(path) {
  const pill = document.getElementById('workspace-indicator-btn');
  const name = document.getElementById('workspace-indicator-name');
  const overflow = document.getElementById('overflow-workspace-btn');
  if (pill) {
    pill.style.display = path ? '' : 'none';
    pill.classList.toggle('active', !!path);
    if (path) pill.title = `Workspace: ${path} — click to clear`;
  }
  if (name) name.textContent = path ? _basename(path) : '';
  if (overflow) overflow.classList.toggle('active', !!path);
  // Recompute the "+" overflow dot (app.js owns updatePlusDot via this event).
  try { document.dispatchEvent(new CustomEvent('overflow-state-change')); } catch (_) {}
}

export function setWorkspace(path) {
  if (path) Storage.set(KEYS.WORKSPACE, path);
  else Storage.remove(KEYS.WORKSPACE);
  syncWorkspaceIndicator(path || '');
}

export function clearWorkspace() {
  setWorkspace('');
  if (uiModule && uiModule.showToast) uiModule.showToast('Workspace cleared');
}

async function _load(path) {
  const url = `${API_BASE}/api/workspace/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`;
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`browse failed: ${res.status}`);
  return res.json();
}

function _render(data) {
  _curPath = data.path;
  const body = _modal.querySelector('#workspace-body');
  const pathEl = _modal.querySelector('#workspace-cur-path');
  if (pathEl) {
    // Reflect the resolved (realpath) location back into the editable field.
    pathEl.value = data.path;
    pathEl.title = data.path;
  }
  let rows = '';
  if (data.parent) {
    rows += `<div class="workspace-row workspace-up" data-path="${encodeURIComponent(data.parent)}">↑ ..</div>`;
  }
  for (const d of data.dirs) {
    // Backend supplies the full child path (os.path.join → cross-platform).
    // Each folder carries a star toggle to favorite it as a saved workspace.
    const star = _isSaved(d.path) ? ' saved' : '';
    rows += `<div class="workspace-row" data-path="${encodeURIComponent(d.path)}">`
      + _FOLDER_SVG
      + `<span>${uiModule.esc(d.name)}</span>`
      + `<button type="button" class="workspace-fav-btn${star}" data-fav="${encodeURIComponent(d.path)}" data-name="${uiModule.esc(d.name)}" `
      + `title="${star ? 'Saved — click to remove' : 'Save this workspace'}" aria-label="Toggle saved workspace">${_STAR_SVG}</button>`
      + `</div>`;
  }
  if (!data.dirs.length && !data.parent) rows = '<div class="workspace-empty">No subfolders</div>';
  body.innerHTML = rows || '<div class="workspace-empty">No subfolders</div>';
  body.querySelectorAll('.workspace-row').forEach((row) => {
    row.addEventListener('click', (ev) => {
      if (ev.target.closest('.workspace-fav-btn')) return; // star handled below
      _navigate(decodeURIComponent(row.dataset.path));
    });
  });
  body.querySelectorAll('.workspace-fav-btn').forEach((btn) => {
    btn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const path = decodeURIComponent(btn.dataset.fav);
      const nowSaved = await _toggleFav(path, btn.dataset.name);
      btn.classList.toggle('saved', nowSaved);
      btn.title = nowSaved ? 'Saved — click to remove' : 'Save this workspace';
    });
  });
}

async function _navigate(path) {
  try {
    _render(await _load(path));
  } catch (e) {
    if (uiModule && uiModule.showError) uiModule.showError('Could not open folder');
  }
}

function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'workspace-modal';
  _modal.className = 'modal';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>Select workspace</h4>
        <button class="close-btn" id="workspace-close" aria-label="Close">✖</button>
      </div>
      <input type="text" class="styled-prompt-input workspace-cur" id="workspace-cur-path"
             spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off"
             placeholder="Type or paste a folder path, then press Enter" />
      <div class="modal-body workspace-body" id="workspace-body"></div>
      <div class="modal-footer workspace-footer">
        <span class="workspace-hint">★ a folder to save it as a workspace</span>
        <span class="workspace-footer-spacer"></span>
        <button type="button" class="confirm-btn confirm-btn-secondary" id="workspace-cancel">Cancel</button>
        <button type="button" class="confirm-btn confirm-btn-primary" id="workspace-use">Use this folder</button>
      </div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#workspace-close').addEventListener('click', closeWorkspaceBrowser);
  _modal.querySelector('#workspace-cancel').addEventListener('click', closeWorkspaceBrowser);
  // Editable path bar: Enter navigates to a typed/pasted folder.
  _modal.querySelector('#workspace-cur-path').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const v = e.target.value.trim();
      if (v) _navigate(v);
    }
  });
  _modal.querySelector('#workspace-use').addEventListener('click', () => {
    setWorkspace(_curPath);
    if (uiModule && uiModule.showToast) uiModule.showToast(`Workspace set: ${_basename(_curPath)}`);
    closeWorkspaceBrowser();
  });
  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  return _modal;
}

export async function openWorkspaceBrowser() {
  const modal = _getModal();
  modal.style.display = 'flex';
  // Load saved bookmarks first so folder rows render their star state correctly,
  // then browse. (Both hit the network; saved is small and resolves quickly.)
  try {
    _saved = await _loadSaved();
  } catch (_) { _saved = []; }
  try {
    _render(await _load(getWorkspace() || ''));
  } catch (e) {
    if (uiModule && uiModule.showError) uiModule.showError('Could not browse folders');
  }
}

export function closeWorkspaceBrowser() {
  if (_modal) _modal.style.display = 'none';
}

// ── Saved-workspaces list modal (opened from the + overflow menu) ───
let _savedModal = null;

function _getSavedModal() {
  if (_savedModal) return _savedModal;
  _savedModal = document.createElement('div');
  _savedModal.id = 'saved-workspace-modal';
  _savedModal.className = 'modal';
  _savedModal.style.display = 'none';
  _savedModal.innerHTML = `
    <div class="modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>Saved workspaces</h4>
        <button class="close-btn" id="saved-workspace-close" aria-label="Close">✖</button>
      </div>
      <div class="modal-body workspace-body" id="saved-workspace-body"></div>
      <div class="modal-footer workspace-footer">
        <button type="button" class="confirm-btn confirm-btn-secondary" id="saved-workspace-browse">Browse folders…</button>
        <span class="workspace-footer-spacer"></span>
        <button type="button" class="confirm-btn confirm-btn-secondary" id="saved-workspace-cancel">Close</button>
      </div>
    </div>`;
  document.body.appendChild(_savedModal);
  _savedModal.querySelector('#saved-workspace-close').addEventListener('click', closeSavedWorkspaces);
  _savedModal.querySelector('#saved-workspace-cancel').addEventListener('click', closeSavedWorkspaces);
  _savedModal.querySelector('#saved-workspace-browse').addEventListener('click', () => {
    closeSavedWorkspaces();
    openWorkspaceBrowser();
  });
  const content = _savedModal.querySelector('.modal-content');
  const header = _savedModal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_savedModal, { content, header });
  return _savedModal;
}

function _renderSavedList() {
  const body = _savedModal.querySelector('#saved-workspace-body');
  if (!_saved.length) {
    body.innerHTML = '<div class="workspace-empty">No saved workspaces yet — open “Browse folders…”, then ★ a folder to save it.</div>';
    return;
  }
  const active = getWorkspace();
  let rows = '';
  for (let i = 0; i < _saved.length; i++) {
    const e = _saved[i];
    const isActive = active === e.path ? ' active' : '';
    rows += `<div class="workspace-row workspace-saved-row${isActive}" data-idx="${i}" title="${uiModule.esc(e.path)}">`
      + _FOLDER_SVG
      + `<span>${uiModule.esc(e.name || _basename(e.path))}</span>`
      + `<button type="button" class="workspace-saved-del" data-del="${i}" title="Remove" aria-label="Remove saved workspace">✖</button>`
      + `</div>`;
  }
  body.innerHTML = rows;
  body.querySelectorAll('.workspace-saved-row').forEach((row) => {
    row.addEventListener('click', (ev) => {
      if (ev.target.closest('.workspace-saved-del')) return;
      const entry = _saved[Number(row.dataset.idx)];
      if (!entry) return;
      setWorkspace(entry.path);
      if (uiModule && uiModule.showToast) uiModule.showToast(`Workspace set: ${_basename(entry.path)}`);
      closeSavedWorkspaces();
    });
  });
  body.querySelectorAll('.workspace-saved-del').forEach((btn) => {
    btn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const entry = _saved[Number(btn.dataset.del)];
      if (!entry) return;
      const next = _saved.filter((e) => e.path !== entry.path);
      try {
        await _persistSaved(next);
        _saved = next;
        _renderSavedList();
      } catch (e) {
        if (uiModule && uiModule.showError) uiModule.showError('Could not remove saved workspace');
      }
    });
  });
}

export async function openSavedWorkspaces() {
  const modal = _getSavedModal();
  modal.style.display = 'flex';
  const body = modal.querySelector('#saved-workspace-body');
  body.innerHTML = '<div class="workspace-empty">Loading…</div>';
  try {
    _saved = await _loadSaved();
  } catch (_) { _saved = []; }
  _renderSavedList();
}

export function closeSavedWorkspaces() {
  if (_savedModal) _savedModal.style.display = 'none';
}

export function initWorkspace() {
  // Restore persisted workspace into the pill on load.
  syncWorkspaceIndicator(getWorkspace());
  const overflow = document.getElementById('overflow-workspace-btn');
  if (overflow) overflow.addEventListener('click', openWorkspaceBrowser);
  const savedBtn = document.getElementById('overflow-saved-workspaces-btn');
  if (savedBtn) savedBtn.addEventListener('click', openSavedWorkspaces);
  const pill = document.getElementById('workspace-indicator-btn');
  if (pill) pill.addEventListener('click', clearWorkspace);
}

export default { initWorkspace, openWorkspaceBrowser, openSavedWorkspaces, getWorkspace, setWorkspace, clearWorkspace, syncWorkspaceIndicator };
