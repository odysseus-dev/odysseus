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
const WORKSPACE_REQUEST_TIMEOUT_MS = 12000;
// Same folder glyph as the overflow menu item + pill (not an emoji).
const _FOLDER_SVG = '<svg class="workspace-row-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
let _modal = null;
let _curPath = '';
let _roots = null;

export function getWorkspace() {
  return Storage.get(KEYS.WORKSPACE, '') || '';
}

function _basename(p) {
  if (!p) return '';
  // Handle both POSIX (/) and Windows (\) separators.
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

function _isAndroidStandalone() {
  try {
    return new URLSearchParams(window.location.search || '').get('mobile') === 'standalone';
  } catch (_) {
    return false;
  }
}

function _workspaceNoteHtml() {
  if (_isAndroidStandalone()) {
    return 'This folder is local to this Android device. Public roots use Android Documents/Downloads when available; App Workspace stays inside Odysseus storage. File tools are <strong>confined</strong> here; shell commands are unavailable in standalone mode.';
  }
  return 'This folder is on the computer running Odysseus. Android in Connect to PC mode edits that PC folder through the backend. File tools are <strong>confined</strong> here; shell commands start here but are <strong>not sandboxed</strong>.';
}

export function syncWorkspaceIndicator(path) {
  const pill = document.getElementById('workspace-indicator-btn');
  const name = document.getElementById('workspace-indicator-name');
  const overflow = document.getElementById('overflow-workspace-btn');
  if (pill) {
    pill.style.display = path ? '' : 'none';
    pill.classList.toggle('active', !!path);
    if (path) pill.title = `Workspace: ${path}\nFile and folder requests use Agent mode and are confined here; shell commands start here but are not sandboxed.\nClick to clear.`;
  }
  if (name) name.textContent = path ? _basename(path) : '';
  if (overflow) {
    overflow.style.display = '';
    overflow.classList.toggle('active', !!path);
  }
  // Recompute the "+" overflow dot (app.js owns updatePlusDot via this event).
  try { document.dispatchEvent(new CustomEvent('overflow-state-change')); } catch (_) {}
}

// Called by the agent/chat mode toggle so the pill + overflow entry follow mode.
export function applyMode(_mode) {
  syncWorkspaceIndicator(getWorkspace());
}

export function setWorkspace(path) {
  const nextPath = path || '';
  if (nextPath) Storage.set(KEYS.WORKSPACE, nextPath);
  else Storage.remove(KEYS.WORKSPACE);
  syncWorkspaceIndicator(nextPath);
  try {
    document.dispatchEvent(new CustomEvent('workspace-change', { detail: { path: nextPath } }));
  } catch (_) {}
}

function _isDeprecatedWorkspaceError(err) {
  return Number(err && err.status ? err.status : 0) === 410;
}

/**
 * Validate a manually entered path server-side, then persist the canonical
 * form. Returns {ok, path|null}. Without this, a typo / file path / deleted
 * folder / filesystem root would be stored and shown as active while the
 * backend silently refuses to bind it on every send.
 */
export async function vetAndSetWorkspace(path) {
  try {
    const data = await _fetchJson(`${API_BASE}/api/workspace/vet?path=${encodeURIComponent(path)}`);
    if (data.ok && data.path) {
      setWorkspace(data.path);
      return { ok: true, path: data.path };
    }
    return { ok: false, path: null };
  } catch (e) {
    return { ok: false, path: null };
  }
}

export function clearWorkspace() {
  setWorkspace('');
  if (uiModule && uiModule.showToast) uiModule.showToast('Workspace cleared');
}

async function _readErrorDetail(res) {
  try {
    const data = await res.clone().json();
    return data.detail || data.error || data.message || '';
  } catch (_) {
    try { return await res.text(); } catch (_) { return ''; }
  }
}

async function _fetchJson(url, options = {}) {
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timeout = controller
    ? setTimeout(() => controller.abort(), WORKSPACE_REQUEST_TIMEOUT_MS)
    : null;
  try {
    const res = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      signal: controller ? controller.signal : options.signal,
    });
    if (!res.ok) {
      const err = new Error(`workspace request failed: ${res.status}`);
      err.status = res.status;
      err.detail = await _readErrorDetail(res);
      throw err;
    }
    return res.json();
  } catch (e) {
    if (e && e.name === 'AbortError') e.timeout = true;
    throw e;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

async function _load(path) {
  const url = `${API_BASE}/api/workspace/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`;
  return _fetchJson(url);
}

async function _loadRoots() {
  if (_roots) return _roots;
  try {
    _roots = await _fetchJson(`${API_BASE}/api/workspace/roots`);
  } catch (e) {
    // Older PC backends did not have shortcut roots. Keep browsing compatible,
    // but do not hide auth, connection, or timeout failures from the sheet.
    if (e && (e.status === 401 || e.status === 403 || e.timeout || !e.status)) {
      throw e;
    }
    _roots = { default_path: '', roots: [] };
  }
  return _roots;
}

function _workspaceErrorMessage(err, fallback) {
  const status = Number(err && err.status ? err.status : 0);
  const detail = String((err && err.detail) || (err && err.message) || '').trim();
  if (status === 401) return 'Sign in again to browse workspace folders.';
  if (status === 403) return detail || 'Workspace selection is admin-only on this backend.';
  if (status === 404 || status === 501 || /mobile standalone route not implemented/i.test(detail)) {
    return detail || 'Workspace browsing is not available on this backend.';
  }
  if ((err && err.timeout) || (err && err.name === 'AbortError')) {
    return 'Workspace browsing timed out. Check the backend connection, then retry.';
  }
  if (!status && /failed to fetch|network|load failed|connection/i.test(detail)) {
    return 'Could not reach the backend for workspace browsing.';
  }
  return detail ? `${fallback}: ${detail}` : fallback;
}

function _showWorkspaceError(message) {
  if (uiModule && uiModule.showError) uiModule.showError(message);
}

function _updateActionButtons(selectable, reason) {
  const useBtn = _modal && _modal.querySelector('#workspace-use');
  const openFilesBtn = _modal && _modal.querySelector('#workspace-open-files');
  const disabled = !selectable;
  const disabledTitle = reason || 'Choose a usable workspace folder first';
  if (useBtn) {
    useBtn.disabled = disabled;
    useBtn.title = disabled ? disabledTitle : '';
  }
  if (openFilesBtn) {
    openFilesBtn.disabled = disabled;
    openFilesBtn.title = disabled ? disabledTitle : 'Open this workspace in the file editor';
  }
}

function _samePath(a, b) {
  const left = String(a || '').replace(/[\\/]+$/, '').toLowerCase();
  const right = String(b || '').replace(/[\\/]+$/, '').toLowerCase();
  return !!left && left === right;
}

function _renderShortcuts() {
  const wrap = _modal && _modal.querySelector('#workspace-shortcuts');
  if (!wrap) return;
  const roots = (_roots && Array.isArray(_roots.roots)) ? _roots.roots : [];
  wrap.innerHTML = '';
  if (!roots.length) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = '';
  for (const root of roots) {
    if (!root || !root.path) continue;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'workspace-shortcut-btn';
    btn.classList.toggle('active', _samePath(root.path, _curPath));
    btn.disabled = root.selectable === false && !root.path;
    btn.title = root.path;
    btn.innerHTML = `${_FOLDER_SVG}<span>${uiModule.esc(root.label || _basename(root.path))}</span>`;
    btn.addEventListener('click', () => _navigate(root.path));
    wrap.appendChild(btn);
  }
}

function _render(data) {
  _curPath = data.path || '';
  _renderShortcuts();
  const body = _modal.querySelector('#workspace-body');
  const pathEl = _modal.querySelector('#workspace-cur-path');
  if (pathEl) {
    // Reflect the resolved (realpath) location back into the editable field.
    pathEl.value = _curPath;
    pathEl.title = _curPath;
  }
  let rows = '';
  if (data.parent) {
    rows += `<div class="workspace-row workspace-up" data-path="${encodeURIComponent(data.parent)}">↑ ..</div>`;
  }
  const dirs = Array.isArray(data.dirs) ? data.dirs : [];
  for (const d of dirs) {
    // Backend supplies the full child path (os.path.join → cross-platform).
    rows += `<div class="workspace-row" data-path="${encodeURIComponent(d.path)}">${_FOLDER_SVG}<span>${uiModule.esc(d.name)}</span></div>`;
  }
  if (data.truncated) {
    rows += '<div class="workspace-empty">Too many folders to list. Type or paste a path above to jump in.</div>';
  }
  if (!dirs.length && !data.parent) rows = '<div class="workspace-empty">No subfolders</div>';
  body.innerHTML = rows || '<div class="workspace-empty">No subfolders</div>';
  body.querySelectorAll('.workspace-row').forEach((row) => {
    row.addEventListener('click', () => _navigate(decodeURIComponent(row.dataset.path)));
  });
  // Filesystem roots (and sensitive dirs) can be browsed through but never
  // bound as the workspace; the backend rejects them too.
  _updateActionButtons(data.selectable !== false && !!_curPath, 'This folder cannot be used as a workspace');
}

function _renderLoading(message) {
  _curPath = '';
  _updateActionButtons(false, message || 'Loading folders...');
  const body = _modal && _modal.querySelector('#workspace-body');
  if (body) body.innerHTML = `<div class="workspace-status">${uiModule.esc(message || 'Loading folders...')}</div>`;
}

function _renderError(message, options = {}) {
  _curPath = '';
  _updateActionButtons(false, message);
  _renderShortcuts();
  const body = _modal && _modal.querySelector('#workspace-body');
  if (!body) return;
  const canRetry = options.retry !== false;
  body.innerHTML = `
    <div class="workspace-status workspace-status-error">
      <div class="workspace-status-message">${uiModule.esc(message)}</div>
      ${canRetry ? '<button type="button" class="workspace-retry-btn">Retry</button>' : ''}
    </div>`;
  const retry = body.querySelector('.workspace-retry-btn');
  if (retry) {
    retry.addEventListener('click', () => {
      if (options.retryOpen) openWorkspaceBrowser();
      else _navigate(options.retryPath || '');
    });
  }
}

async function _navigate(path) {
  const targetPath = path || '';
  try {
    _renderLoading('Loading folders...');
    _render(await _load(targetPath));
  } catch (e) {
    const message = _workspaceErrorMessage(e, 'Could not open folder');
    _renderError(message, { retryPath: targetPath });
    _showWorkspaceError(message);
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
      <div class="workspace-shortcuts" id="workspace-shortcuts"></div>
      <p class="muted workspace-note">${_workspaceNoteHtml()}</p>
      <div class="modal-body workspace-body" id="workspace-body"></div>
      <div class="modal-footer workspace-footer">
        <button type="button" class="confirm-btn confirm-btn-secondary" id="workspace-cancel">Cancel</button>
        <button type="button" class="confirm-btn confirm-btn-secondary" id="workspace-open-files">Open files</button>
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
  _modal.querySelector('#workspace-cur-path').addEventListener('change', (e) => {
    const v = e.target.value.trim();
    if (v && v !== _curPath) _navigate(v);
  });
  _modal.querySelector('#workspace-use').addEventListener('click', () => {
    if (!_curPath) {
      _showWorkspaceError('Choose a workspace folder first');
      return;
    }
    setWorkspace(_curPath);
    if (uiModule && uiModule.showToast) uiModule.showToast(`Workspace set: ${_basename(_curPath)}`);
    closeWorkspaceBrowser();
  });
  _modal.querySelector('#workspace-open-files').addEventListener('click', async () => {
    if (!_curPath) return;
    setWorkspace(_curPath);
    closeWorkspaceBrowser();
    try {
      const mod = await import('./workspaceEditor.js');
      const editor = mod.default || mod;
      await editor.openWorkspaceEditor({ workspace: _curPath, deferLoad: true });
    } catch (e) {
      if (uiModule && uiModule.showError) uiModule.showError('Could not open workspace files');
    }
  });
  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  return _modal;
}

export async function openWorkspaceBrowser() {
  const modal = _getModal();
  modal.style.display = 'flex';
  _renderLoading('Loading folders...');
  let roots = { default_path: '', roots: [] };
  try {
    roots = await _loadRoots();
    _renderShortcuts();
  } catch (e) {
    const message = _workspaceErrorMessage(e, 'Could not load workspace shortcuts');
    _renderError(message, { retryOpen: true });
    _showWorkspaceError(message);
    return;
  }
  try {
    _render(await _load(getWorkspace() || roots.default_path || ''));
  } catch (e) {
    if (_isDeprecatedWorkspaceError(e) && getWorkspace()) {
      setWorkspace('');
      try {
        _render(await _load(roots.default_path || ''));
        if (uiModule && uiModule.showToast) uiModule.showToast('Old private workspace cleared');
        return;
      } catch (retryErr) {
        e = retryErr;
      }
    }
    const message = _workspaceErrorMessage(e, 'Could not browse folders');
    _renderError(message, { retryOpen: true });
    _showWorkspaceError(message);
  }
}

export function closeWorkspaceBrowser() {
  if (_modal) _modal.style.display = 'none';
}

export function initWorkspace() {
  // Restore persisted workspace into the pill on load.
  syncWorkspaceIndicator(getWorkspace());
  const overflow = document.getElementById('overflow-workspace-btn');
  if (overflow) overflow.addEventListener('click', openWorkspaceBrowser);
  const pill = document.getElementById('workspace-indicator-btn');
  if (pill) pill.addEventListener('click', clearWorkspace);
}

export default { initWorkspace, openWorkspaceBrowser, getWorkspace, setWorkspace, vetAndSetWorkspace, clearWorkspace, syncWorkspaceIndicator, applyMode };
