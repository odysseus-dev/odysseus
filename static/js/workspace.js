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
let _modal = null;
let _curPath = '';
let _defaultPath = '';
let _workspace = Storage.get(KEYS.WORKSPACE, '') || '';
let _workspaceReadyPromise = null;

export function getWorkspace() {
  return _workspace;
}

export function whenWorkspaceReady() {
  if (!_workspaceReadyPromise) _workspaceReadyPromise = _syncServerWorkspace();
  return _workspaceReadyPromise;
}

function _basename(p) {
  if (!p) return '';
  // Handle both POSIX (/) and Windows (\) separators.
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

// Workspace only applies to agent mode (it scopes the file/shell tools), so the
// pill + overflow entry are hidden in chat mode, like the bash toggle.
function _isChatMode() {
  const b = document.getElementById('mode-chat-btn');
  return !!(b && b.classList.contains('active'));
}

export function syncWorkspaceIndicator(path) {
  const chat = _isChatMode();
  const pill = document.getElementById('workspace-indicator-btn');
  const name = document.getElementById('workspace-indicator-name');
  const overflow = document.getElementById('overflow-workspace-btn');
  if (pill) {
    pill.style.display = (path && !chat) ? '' : 'none';
    pill.classList.toggle('active', !!path);
    if (path) pill.title = `Workspace: ${path}\nFile tools are confined here. Shell commands start here inside the process sandbox.\nClick to clear.`;
  }
  if (name) name.textContent = path ? _basename(path) : '';
  if (overflow) {
    overflow.style.display = chat ? 'none' : '';
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
  _workspace = path || '';
  if (path) Storage.set(KEYS.WORKSPACE, path);
  else Storage.remove(KEYS.WORKSPACE);
  syncWorkspaceIndicator(path || '');
}

async function _requestJson(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : '';
    const error = new Error(data.error || detail || `Workspace request failed (${res.status})`);
    error.status = res.status;
    error.data = data;
    throw error;
  }
  return data;
}

async function _getServerSelection() {
  const data = await _requestJson('/api/workspace/selection');
  _defaultPath = data.default_path || _defaultPath;
  return data;
}

async function _selectServerWorkspace(path, create = false) {
  const data = await _requestJson('/api/workspace/selection', {
    method: 'POST',
    body: JSON.stringify({ path, create }),
  });
  if (data.ok && data.path) setWorkspace(data.path);
  return data;
}

/**
 * Validate a manually entered path server-side, then persist the canonical
 * form. Returns {ok, path|null}. Without this, a typo / file path / deleted
 * folder / filesystem root would be stored and shown as active while the
 * backend silently refuses to bind it on every send.
 */
export async function vetAndSetWorkspace(path) {
  try {
    return await _selectServerWorkspace(path, false);
  } catch (e) {
    return {
      ok: false,
      path: null,
      error: e.message || 'Could not select folder.',
      code: e.data && e.data.code,
      can_create: !!(e.data && e.data.can_create),
      missing_path: e.data && e.data.path,
    };
  }
}

export async function clearWorkspace({ quiet = false, localOnFailure = false } = {}) {
  try {
    await _requestJson('/api/workspace/selection', { method: 'DELETE' });
    setWorkspace('');
    if (!quiet && uiModule && uiModule.showToast) uiModule.showToast('Workspace cleared');
    return true;
  } catch (e) {
    if (localOnFailure) setWorkspace('');
    if (uiModule && uiModule.showError) uiModule.showError(e.message || 'Could not clear workspace');
    return false;
  }
}

async function _load(path) {
  const url = `${API_BASE}/api/workspace/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`;
  const data = await _requestJson(url.slice(API_BASE.length));
  _defaultPath = data.default_path || _defaultPath;
  return data;
}

function _setStatus(message = '', kind = '') {
  if (!_modal) return;
  const status = _modal.querySelector('#workspace-selection-status');
  if (!status) return;
  status.textContent = message;
  status.className = `workspace-selection-status${kind ? ` workspace-status-${kind}` : ''}`;
  status.style.display = message ? '' : 'none';
}

function _setBusy(busy) {
  if (!_modal) return;
  for (const id of ['workspace-use', 'workspace-create-missing', 'workspace-new-folder']) {
    const button = _modal.querySelector(`#${id}`);
    if (button) button.disabled = !!busy || button.dataset.policyDisabled === 'true';
  }
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
    rows += `<div class="workspace-row" data-path="${encodeURIComponent(d.path)}">${_FOLDER_SVG}<span>${uiModule.esc(d.name)}</span></div>`;
  }
  if (data.truncated) {
    rows += '<div class="workspace-empty">Too many folders to list. Type or paste a path above to jump in.</div>';
  }
  if (!data.dirs.length && !data.parent) rows = '<div class="workspace-empty">No subfolders</div>';
  body.innerHTML = rows || '<div class="workspace-empty">No subfolders</div>';
  body.querySelectorAll('.workspace-row').forEach((row) => {
    row.addEventListener('click', () => _navigate(decodeURIComponent(row.dataset.path)));
  });
  // Filesystem roots (and sensitive dirs) can be browsed through but never
  // bound as the workspace; the backend rejects them too.
  const useBtn = _modal.querySelector('#workspace-use');
  if (useBtn) {
    useBtn.disabled = data.selectable === false;
    useBtn.dataset.policyDisabled = data.selectable === false ? 'true' : 'false';
    useBtn.title = data.selectable === false ? 'This folder cannot be used as a workspace' : '';
  }
  const newFolderBtn = _modal.querySelector('#workspace-new-folder');
  if (newFolderBtn) {
    newFolderBtn.disabled = data.can_create_folder !== true;
    newFolderBtn.dataset.policyDisabled = data.can_create_folder === true ? 'false' : 'true';
  }
  const createMissingBtn = _modal.querySelector('#workspace-create-missing');
  if (createMissingBtn) createMissingBtn.style.display = 'none';
  if (data.selectable === false) {
    _setStatus(data.selectable_reason || 'This folder cannot be used as a workspace.', 'error');
  } else if (data.can_create_folder !== true) {
    _setStatus(`New folders can only be created inside ${data.default_path || _defaultPath}.`, 'info');
  } else {
    _setStatus('This folder is ready to use. You can also create a folder inside it.', 'success');
  }
}

async function _navigate(path) {
  try {
    _render(await _load(path));
  } catch (e) {
    _setStatus(e.message || 'Could not open folder.', 'error');
  }
}

async function _useTypedPath(create = false) {
  const input = _modal && _modal.querySelector('#workspace-cur-path');
  const path = input ? input.value.trim() : '';
  if (!path) {
    _setStatus('Enter a folder path.', 'error');
    return;
  }
  _setBusy(true);
  try {
    const data = await _selectServerWorkspace(path, create);
    if (data.browse) _render(data.browse);
    if (uiModule && uiModule.showToast) uiModule.showToast(`Workspace set: ${_basename(data.path)}`);
    closeWorkspaceBrowser();
  } catch (e) {
    _setStatus(e.message || 'Could not select folder.', 'error');
    const createBtn = _modal.querySelector('#workspace-create-missing');
    if (createBtn) {
      const canCreate = !!(e.data && e.data.code === 'folder_missing' && e.data.can_create);
      createBtn.style.display = canCreate ? '' : 'none';
      createBtn.dataset.path = canCreate ? (e.data.path || path) : '';
    }
  } finally {
    _setBusy(false);
  }
}

async function _createFolder() {
  if (!_modal) return;
  const name = await uiModule.styledPrompt('Name the folder to create here.', {
    title: 'New folder',
    placeholder: 'e.g. my-project',
    confirmText: 'Create',
    maxLength: 120,
  });
  if (!name) return;
  _setBusy(true);
  try {
    const data = await _requestJson('/api/workspace/folders', {
      method: 'POST',
      body: JSON.stringify({ parent: _curPath, name }),
    });
    if (data.browse) _render(data.browse);
    if (uiModule && uiModule.showToast) uiModule.showToast(`Folder created: ${_basename(data.path)}`);
  } catch (e) {
    _setStatus(e.message || 'Could not create folder.', 'error');
  } finally {
    _setBusy(false);
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
             placeholder="Type or paste a folder path" />
      <p class="muted workspace-input-hint">Press Enter to preview a typed path, or click <strong>Use this folder</strong> to validate and select it immediately.</p>
      <p class="muted workspace-note">File tools are <strong>confined</strong> to this folder. Shell commands start here inside a networkless process sandbox; if the sandbox is unavailable, command execution is blocked.</p>
      <p class="workspace-selection-status" id="workspace-selection-status" role="status" aria-live="polite"></p>
      <button type="button" class="confirm-btn confirm-btn-secondary workspace-create-missing" id="workspace-create-missing" style="display:none">Create and use folder</button>
      <div class="modal-body workspace-body" id="workspace-body"></div>
      <div class="modal-footer workspace-footer">
        <button type="button" class="confirm-btn confirm-btn-secondary" id="workspace-new-folder">New folder</button>
        <span class="workspace-footer-spacer"></span>
        <button type="button" class="confirm-btn confirm-btn-secondary" id="workspace-cancel">Cancel</button>
        <button type="button" class="confirm-btn confirm-btn-primary" id="workspace-use">Use this folder</button>
      </div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#workspace-close').addEventListener('click', closeWorkspaceBrowser);
  _modal.querySelector('#workspace-cancel').addEventListener('click', closeWorkspaceBrowser);
  // Editable path bar: Enter navigates to a typed/pasted folder.
  const pathInput = _modal.querySelector('#workspace-cur-path');
  pathInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const v = e.target.value.trim();
      if (v) _navigate(v);
    }
  });
  pathInput.addEventListener('input', () => {
    const useBtn = _modal.querySelector('#workspace-use');
    if (useBtn) {
      useBtn.dataset.policyDisabled = 'false';
      useBtn.disabled = !pathInput.value.trim();
      useBtn.title = '';
    }
    const createBtn = _modal.querySelector('#workspace-create-missing');
    if (createBtn) createBtn.style.display = 'none';
    _setStatus('Click Use this folder to validate the typed path.', 'info');
  });
  _modal.querySelector('#workspace-use').addEventListener('click', () => _useTypedPath(false));
  _modal.querySelector('#workspace-create-missing').addEventListener('click', () => _useTypedPath(true));
  _modal.querySelector('#workspace-new-folder').addEventListener('click', _createFolder);
  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  return _modal;
}

export async function openWorkspaceBrowser() {
  const modal = _getModal();
  modal.style.display = 'flex';
  try {
    const state = await _getServerSelection();
    if (state.path) setWorkspace(state.path);
    else if (state.warning) await clearWorkspace({ quiet: true, localOnFailure: true });
    else setWorkspace('');
    _render(await _load(state.path || state.default_path || _defaultPath));
    if (state.warning) _setStatus(state.warning, 'error');
  } catch (e) {
    _setStatus(e.message || 'Could not browse folders.', 'error');
  }
}

export function closeWorkspaceBrowser() {
  if (_modal) _modal.style.display = 'none';
}

async function _syncServerWorkspace() {
  try {
    const cached = getWorkspace();
    const state = await _getServerSelection();
    if (state.path) {
      setWorkspace(state.path);
    } else if (state.warning) {
      await clearWorkspace({ quiet: true, localOnFailure: true });
    } else if (cached && state.migration_allowed && !state.warning) {
      // One-time migration from the old browser-only setting.
      const migrated = await vetAndSetWorkspace(cached);
      if (!migrated.ok) setWorkspace('');
    } else {
      setWorkspace('');
    }
    if (state.warning && uiModule && uiModule.showError) uiModule.showError(state.warning);
  } catch (e) {
    // Retain the local cache during a transient server failure; selection is
    // still revalidated by the chat route before any tools receive it.
    console.warn('[workspace] Failed to load server selection:', e.message);
  }
}

export function initWorkspace() {
  // Show the browser cache immediately, then replace it with the server-owned
  // per-user selection so another browser or PC sees the same workspace.
  syncWorkspaceIndicator(getWorkspace());
  const overflow = document.getElementById('overflow-workspace-btn');
  if (overflow) overflow.addEventListener('click', openWorkspaceBrowser);
  const pill = document.getElementById('workspace-indicator-btn');
  if (pill) pill.addEventListener('click', clearWorkspace);
  void whenWorkspaceReady();
}

export default { initWorkspace, openWorkspaceBrowser, getWorkspace, whenWorkspaceReady, setWorkspace, vetAndSetWorkspace, clearWorkspace, syncWorkspaceIndicator, applyMode };
