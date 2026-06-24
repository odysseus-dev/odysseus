// static/js/workspaceEditor.js
//
// Manual workspace file editor. All operations go through /api/workspace/files
// and are confined by the same server-side workspace resolver used by agent
// file tools.

import uiModule from './ui.js';
import workspaceModule from './workspace.js';
import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;
const MAX_TEXT_FILE_BYTES = 1024 * 1024;
const FILE_LIST_LIMIT = 250;

let _modal = null;
let _workspace = '';
let _cwd = '';
let _entries = [];
let _activeFile = null;
let _dirty = false;
let _filter = '';
let _entryRenderToken = 0;
let _loadController = null;
let _loadToken = 0;
let _hasLoadedDir = false;

const ICONS = {
  folder: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  file: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  save: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
  refresh: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9"/><polyline points="3 4 3 9 8 9"/></svg>',
  trash: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
  edit: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
  up: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><polyline points="5 12 12 5 19 12"/></svg>',
};

function _esc(value) {
  return uiModule.esc(String(value || ''));
}

function _qs(params) {
  const out = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => out.set(key, value == null ? '' : String(value)));
  return out.toString();
}

async function _jsonFetch(url, options = {}) {
  const res = await fetch(url, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });
  let data = {};
  try { data = await res.json(); } catch {}
  if (!res.ok) {
    throw new Error(data.detail || data.error || data.message || `HTTP ${res.status}`);
  }
  return data;
}

function _afterPaint() {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    } else {
      setTimeout(resolve, 0);
    }
  });
}

function _scheduleWork(fn) {
  if (typeof requestIdleCallback === 'function') {
    requestIdleCallback(fn, { timeout: 100 });
  } else if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(fn);
  } else {
    setTimeout(fn, 0);
  }
}

function _joinPath(base, name) {
  const b = String(base || '').replace(/[\\/]+$/, '');
  const n = String(name || '').replace(/^[\\/]+/, '');
  return b ? `${b}/${n}` : n;
}

function _dirname(path) {
  const parts = String(path || '').split(/[\\/]+/).filter(Boolean);
  parts.pop();
  return parts.join('/');
}

function _basename(path) {
  const parts = String(path || '').replace(/[\\/]+$/, '').split(/[\\/]+/);
  return parts[parts.length - 1] || path || '';
}

function _formatBytes(size) {
  const n = Number(size || 0);
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function _formatTime(epochSeconds) {
  if (!epochSeconds) return '';
  try {
    return new Date(epochSeconds * 1000).toLocaleString();
  } catch {
    return '';
  }
}

function _setStatus(message, isError = false) {
  const el = _modal && _modal.querySelector('#workspace-editor-status');
  if (!el) return;
  el.textContent = message || '';
  el.classList.toggle('error', !!isError);
}

function _setDirty(next) {
  _dirty = !!next;
  const saveBtn = _modal && _modal.querySelector('#workspace-editor-save');
  const dirtyEl = _modal && _modal.querySelector('#workspace-editor-dirty');
  if (saveBtn) saveBtn.disabled = !_activeFile || !_dirty;
  if (dirtyEl) dirtyEl.style.display = _dirty ? '' : 'none';
}

async function _confirmDiscard() {
  if (!_dirty) return true;
  return uiModule.styledConfirm('Discard unsaved file changes?', {
    confirmText: 'Discard',
    danger: true,
  });
}

function _ensureModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'workspace-editor-modal';
  _modal.className = 'modal';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content workspace-editor-content">
      <div class="modal-header">
        <h4>${ICONS.folder}<span>Workspace Files</span></h4>
        <button class="close-btn" id="workspace-editor-close" aria-label="Close">x</button>
      </div>
      <div class="workspace-editor-toolbar">
        <div class="workspace-editor-root" title="">
          <span>Workspace</span>
          <code id="workspace-editor-root-path"></code>
        </div>
        <button type="button" class="memory-toolbar-btn" id="workspace-editor-choose">${ICONS.folder}<span>Choose</span></button>
        <button type="button" class="memory-toolbar-btn" id="workspace-editor-reload">${ICONS.refresh}<span>Reload</span></button>
      </div>
      <div class="workspace-editor-body">
        <aside class="workspace-editor-tree">
          <div class="workspace-editor-pathbar">
            <button type="button" class="workspace-editor-icon-btn" id="workspace-editor-up" title="Parent folder" aria-label="Parent folder">${ICONS.up}</button>
            <input id="workspace-editor-path-input" spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off" />
          </div>
          <div class="workspace-editor-actions">
            <button type="button" class="memory-toolbar-btn" id="workspace-editor-new-file">${ICONS.file}<span>File</span></button>
            <button type="button" class="memory-toolbar-btn" id="workspace-editor-new-folder">${ICONS.folder}<span>Folder</span></button>
          </div>
          <input id="workspace-editor-filter" class="memory-search-input" placeholder="Filter files..." />
          <div class="workspace-editor-list" id="workspace-editor-list"></div>
        </aside>
        <section class="workspace-editor-panel">
          <div class="workspace-editor-filebar">
            <div class="workspace-editor-filetitle">
              <strong id="workspace-editor-file-name">No file open</strong>
              <span id="workspace-editor-file-meta"></span>
              <span id="workspace-editor-dirty" style="display:none;">Unsaved</span>
            </div>
            <div class="workspace-editor-file-actions">
              <button type="button" class="memory-toolbar-btn" id="workspace-editor-rename" disabled>${ICONS.edit}<span>Rename</span></button>
              <button type="button" class="memory-toolbar-btn danger" id="workspace-editor-delete" disabled>${ICONS.trash}<span>Delete</span></button>
              <button type="button" class="memory-toolbar-btn active" id="workspace-editor-save" disabled>${ICONS.save}<span>Save</span></button>
            </div>
          </div>
          <textarea id="workspace-editor-textarea" class="workspace-editor-textarea" spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off" disabled placeholder="Select an editable text file from the workspace."></textarea>
        </section>
      </div>
      <div class="workspace-editor-footer">
        <span id="workspace-editor-status"></span>
      </div>
    </div>`;
  document.body.appendChild(_modal);

  const close = () => closeWorkspaceEditor();
  _modal.querySelector('#workspace-editor-close')?.addEventListener('click', close);
  _modal.querySelector('#workspace-editor-choose')?.addEventListener('click', async () => {
    if (await _confirmDiscard()) workspaceModule.openWorkspaceBrowser();
  });
  _modal.querySelector('#workspace-editor-reload')?.addEventListener('click', () => _loadDir(_cwd));
  _modal.querySelector('#workspace-editor-up')?.addEventListener('click', () => _loadDir(_dirname(_cwd)));
  _modal.querySelector('#workspace-editor-new-file')?.addEventListener('click', _newFile);
  _modal.querySelector('#workspace-editor-new-folder')?.addEventListener('click', _newFolder);
  _modal.querySelector('#workspace-editor-save')?.addEventListener('click', _saveActiveFile);
  _modal.querySelector('#workspace-editor-rename')?.addEventListener('click', () => {
    if (_activeFile) _renamePath(_activeFile.path, 'file');
  });
  _modal.querySelector('#workspace-editor-delete')?.addEventListener('click', () => {
    if (_activeFile) _deletePath(_activeFile.path, 'file');
  });
  _modal.querySelector('#workspace-editor-path-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      _loadDir(event.currentTarget.value.trim());
    }
  });
  _modal.querySelector('#workspace-editor-filter')?.addEventListener('input', (event) => {
    _filter = event.currentTarget.value.trim().toLowerCase();
    _renderEntries();
  });
  _modal.querySelector('#workspace-editor-textarea')?.addEventListener('input', () => _setDirty(true));
  document.addEventListener('workspace-change', async (event) => {
    const nextWorkspace = event?.detail?.path || '';
    _workspace = nextWorkspace;
    _cwd = '';
    _entries = [];
    _activeFile = null;
    _renderRoot();
    _renderActiveFile();
    if (!_modal || _modal.style.display === 'none') return;
    if (!nextWorkspace) {
      _renderEntries();
      _setStatus('Choose a workspace to browse files.');
      return;
    }
    await _loadDir('');
  });

  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  return _modal;
}

function _renderRoot() {
  const rootPath = _modal.querySelector('#workspace-editor-root-path');
  const rootWrap = _modal.querySelector('.workspace-editor-root');
  if (rootPath) rootPath.textContent = _workspace || 'No workspace selected';
  if (rootWrap) rootWrap.title = _workspace || '';
}

function _renderDir(data) {
  _cwd = data.path || '';
  _entries = Array.isArray(data.entries) ? data.entries : [];
  _hasLoadedDir = true;
  const pathInput = _modal.querySelector('#workspace-editor-path-input');
  if (pathInput) pathInput.value = _cwd;
  _renderRoot();
  _renderEntries();
  const storageWarning = String(data.storage_warning || '').trim();
  if (storageWarning) {
    _setStatus(storageWarning, true);
  } else if (data.truncated) {
    _setStatus(`Showing first ${data.max_entries || _entries.length} entries. Use the filter or path box to narrow.`);
  } else {
    _setStatus(`${_entries.length} item${_entries.length === 1 ? '' : 's'}`);
  }
}

function _createEntryRow(entry) {
  const isDir = entry.type === 'directory';
  const row = document.createElement('div');
  row.className = 'workspace-file-row' + (isDir ? ' is-dir' : '') + (_activeFile && _activeFile.path === entry.path ? ' active' : '');
  row.title = entry.path || entry.name || '';
  row.innerHTML = `
    <span class="workspace-file-icon">${isDir ? ICONS.folder : ICONS.file}</span>
    <span class="workspace-file-name">${_esc(entry.name)}</span>
    <span class="workspace-file-meta">${isDir ? '' : _formatBytes(entry.size)}</span>
    <span class="workspace-file-row-actions">
      <button type="button" class="workspace-editor-icon-btn" data-act="rename" title="Rename" aria-label="Rename">${ICONS.edit}</button>
      <button type="button" class="workspace-editor-icon-btn danger" data-act="delete" title="Delete" aria-label="Delete">${ICONS.trash}</button>
    </span>`;
  row.addEventListener('click', () => {
    if (isDir) _loadDir(entry.path);
    else _openFile(entry);
  });
  row.querySelector('[data-act="rename"]')?.addEventListener('click', (event) => {
    event.stopPropagation();
    _renamePath(entry.path, isDir ? 'folder' : 'file');
  });
  row.querySelector('[data-act="delete"]')?.addEventListener('click', (event) => {
    event.stopPropagation();
    _deletePath(entry.path, isDir ? 'folder' : 'file');
  });
  return row;
}

function _renderEntries() {
  const list = _modal.querySelector('#workspace-editor-list');
  if (!list) return;
  const token = ++_entryRenderToken;
  const visible = !_filter
    ? _entries
    : _entries.filter((entry) => String(entry.name || '').toLowerCase().includes(_filter));
  if (!visible.length) {
    const message = !_hasLoadedDir
      ? 'Press Reload to load files.'
      : _filter
        ? 'No matching files or folders.'
        : 'No files or folders found.';
    list.innerHTML = `<div class="workspace-editor-empty">${_esc(message)}</div>`;
    return;
  }
  list.innerHTML = '';
  let index = 0;
  const batchSize = 100;
  const renderBatch = () => {
    if (token !== _entryRenderToken) return;
    const fragment = document.createDocumentFragment();
    const end = Math.min(index + batchSize, visible.length);
    for (; index < end; index += 1) {
      fragment.appendChild(_createEntryRow(visible[index]));
    }
    list.appendChild(fragment);
    if (index < visible.length) _scheduleWork(renderBatch);
  };
  renderBatch();
}

function _renderActiveFile() {
  const name = _modal.querySelector('#workspace-editor-file-name');
  const meta = _modal.querySelector('#workspace-editor-file-meta');
  const textarea = _modal.querySelector('#workspace-editor-textarea');
  const renameBtn = _modal.querySelector('#workspace-editor-rename');
  const deleteBtn = _modal.querySelector('#workspace-editor-delete');
  if (!_activeFile) {
    if (name) name.textContent = 'No file open';
    if (meta) meta.textContent = '';
    if (textarea) {
      textarea.value = '';
      textarea.disabled = true;
      textarea.placeholder = 'Select an editable text file from the workspace.';
    }
    if (renameBtn) renameBtn.disabled = true;
    if (deleteBtn) deleteBtn.disabled = true;
    _setDirty(false);
    return;
  }
  if (name) name.textContent = _activeFile.name || _basename(_activeFile.path);
  if (meta) meta.textContent = `${_formatBytes(_activeFile.size)} - ${_formatTime(_activeFile.modified)}`;
  if (textarea) {
    textarea.disabled = false;
    textarea.value = _activeFile.content || '';
    textarea.placeholder = '';
    textarea.focus();
  }
  if (renameBtn) renameBtn.disabled = false;
  if (deleteBtn) deleteBtn.disabled = false;
  _setDirty(false);
  _renderEntries();
}

async function _loadDir(path = '') {
  if (!await _confirmDiscard()) return;
  if (!_workspace) {
    uiModule.showToast('Choose a workspace first');
    workspaceModule.openWorkspaceBrowser();
    return;
  }
  if (_loadController) _loadController.abort();
  _loadController = new AbortController();
  const token = ++_loadToken;
  try {
    _setStatus('Loading folder...');
    const data = await _jsonFetch(`${API_BASE}/api/workspace/files/list?${_qs({ workspace: _workspace, path, limit: FILE_LIST_LIMIT })}`, {
      signal: _loadController.signal,
    });
    if (token !== _loadToken) return;
    _renderDir(data);
  } catch (err) {
    if (err?.name === 'AbortError') return;
    _setStatus(err.message || 'Could not load folder', true);
    uiModule.showError(err.message || 'Could not load folder');
  } finally {
    if (token === _loadToken) _loadController = null;
  }
}

async function _openFile(entry) {
  if (Number(entry.size || 0) > MAX_TEXT_FILE_BYTES) {
    uiModule.showToast('This file is too large for the quick editor.');
    return;
  }
  if (!await _confirmDiscard()) return;
  try {
    _setStatus('Opening file...');
    const data = await _jsonFetch(`${API_BASE}/api/workspace/files/read?${_qs({ workspace: _workspace, path: entry.path })}`);
    _activeFile = data;
    _renderActiveFile();
    _setStatus(`Opened ${data.path}`);
  } catch (err) {
    _setStatus(err.message || 'Could not open file', true);
    if ((err.message || '').includes('editable text')) {
      uiModule.showToast('This file is not editable text.');
    } else {
      uiModule.showError(err.message || 'Could not open file');
    }
  }
}

async function _saveActiveFile() {
  if (!_activeFile) return;
  const textarea = _modal.querySelector('#workspace-editor-textarea');
  try {
    _setStatus('Saving...');
    const data = await _jsonFetch(`${API_BASE}/api/workspace/files/write`, {
      method: 'POST',
      body: JSON.stringify({
        workspace: _workspace,
        path: _activeFile.path,
        content: textarea ? textarea.value : '',
        previous_mtime: _activeFile.modified,
      }),
    });
    _activeFile = {
      ..._activeFile,
      ...data,
      content: textarea ? textarea.value : '',
    };
    _renderActiveFile();
    await _loadDir(_cwd);
    _setStatus(`Saved ${data.path}`);
  } catch (err) {
    _setStatus(err.message || 'Save failed', true);
    uiModule.showError(err.message || 'Save failed');
  }
}

async function _newFile() {
  const name = await uiModule.styledPrompt('Create a file inside the current folder.', {
    title: 'New file',
    placeholder: 'notes.md',
    confirmText: 'Create',
    maxLength: 180,
  });
  if (!name) return;
  const path = _joinPath(_cwd, name);
  try {
    const data = await _jsonFetch(`${API_BASE}/api/workspace/files/write`, {
      method: 'POST',
      body: JSON.stringify({ workspace: _workspace, path, content: '', create_parents: false }),
    });
    await _loadDir(_cwd);
    await _openFile({ ...data, editable: true });
  } catch (err) {
    _setStatus(err.message || 'Could not create file', true);
    uiModule.showError(err.message || 'Could not create file');
  }
}

async function _newFolder() {
  const name = await uiModule.styledPrompt('Create a folder inside the current folder.', {
    title: 'New folder',
    placeholder: 'src',
    confirmText: 'Create',
    maxLength: 180,
  });
  if (!name) return;
  const path = _joinPath(_cwd, name);
  try {
    await _jsonFetch(`${API_BASE}/api/workspace/files/mkdir`, {
      method: 'POST',
      body: JSON.stringify({ workspace: _workspace, path }),
    });
    await _loadDir(_cwd);
    _setStatus(`Created ${path}`);
  } catch (err) {
    _setStatus(err.message || 'Could not create folder', true);
    uiModule.showError(err.message || 'Could not create folder');
  }
}

async function _renamePath(path, kind) {
  const currentName = _basename(path);
  const nextName = await uiModule.styledPrompt(`Rename this ${kind}.`, {
    title: `Rename ${kind}`,
    defaultValue: currentName,
    confirmText: 'Rename',
    maxLength: 180,
  });
  if (!nextName || nextName === currentName) return;
  const nextPath = _joinPath(_dirname(path), nextName);
  try {
    const data = await _jsonFetch(`${API_BASE}/api/workspace/files/rename`, {
      method: 'POST',
      body: JSON.stringify({ workspace: _workspace, path, new_path: nextPath }),
    });
    if (_activeFile && _activeFile.path === path) {
      _activeFile = { ..._activeFile, path: data.path, name: _basename(data.path) };
      _renderActiveFile();
    } else if (_activeFile && kind === 'folder' && _activeFile.path.startsWith(`${path.replace(/[\\/]+$/, '')}/`)) {
      const suffix = _activeFile.path.slice(path.replace(/[\\/]+$/, '').length);
      _activeFile = { ..._activeFile, path: `${data.path}${suffix}` };
      _renderActiveFile();
    }
    await _loadDir(_cwd);
    _setStatus(`Renamed to ${data.path}`);
  } catch (err) {
    _setStatus(err.message || 'Rename failed', true);
    uiModule.showError(err.message || 'Rename failed');
  }
}

async function _deletePath(path, kind) {
  const label = path || kind;
  const ok = await uiModule.styledConfirm(
    kind === 'folder'
      ? `Delete folder "${label}" and everything inside it?`
      : `Delete file "${label}"?`,
    { confirmText: 'Delete', danger: true },
  );
  if (!ok) return;
  try {
    await _jsonFetch(`${API_BASE}/api/workspace/files/delete?${_qs({
      workspace: _workspace,
      path,
      recursive: kind === 'folder' ? 'true' : 'false',
    })}`, { method: 'DELETE' });
    const normalized = String(path || '').replace(/[\\/]+$/, '');
    if (_activeFile && (_activeFile.path === path || (kind === 'folder' && _activeFile.path.startsWith(`${normalized}/`)))) {
      _activeFile = null;
      _renderActiveFile();
    }
    await _loadDir(_cwd);
    _setStatus(`Deleted ${label}`);
  } catch (err) {
    _setStatus(err.message || 'Delete failed', true);
    uiModule.showError(err.message || 'Delete failed');
  }
}

export async function openWorkspaceEditor(options = {}) {
  _ensureModal();
  const selectedWorkspace = options.workspace || workspaceModule.getWorkspace();
  if (!selectedWorkspace) {
    uiModule.showToast('Choose a workspace first');
    _workspace = '';
    _cwd = '';
    _entries = [];
    _activeFile = null;
    _modal.style.display = 'flex';
    _modal.classList.remove('hidden');
    _renderRoot();
    _renderEntries();
    _renderActiveFile();
    _setStatus('Choose a workspace to browse files.');
    workspaceModule.openWorkspaceBrowser();
    return;
  }
  _workspace = selectedWorkspace;
  _cwd = options.path || '';
  _modal.style.display = 'flex';
  _modal.classList.remove('hidden');
  _renderRoot();
  _entries = [];
  _hasLoadedDir = false;
  const pathInput = _modal.querySelector('#workspace-editor-path-input');
  if (pathInput) pathInput.value = _cwd;
  _renderEntries();
  _renderActiveFile();
  if (options.deferLoad) {
    _setStatus('Workspace ready. Press Reload to load files, or type a subfolder path and press Enter.');
    return;
  }
  _setStatus('Opening workspace...');
  await _afterPaint();
  await _loadDir(_cwd || '');
}

export async function closeWorkspaceEditor() {
  if (!await _confirmDiscard()) return;
  if (_modal) {
    _modal.style.display = 'none';
    _modal.classList.add('hidden');
  }
}

export default { openWorkspaceEditor, closeWorkspaceEditor };
