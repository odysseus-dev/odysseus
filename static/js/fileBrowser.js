// static/js/fileBrowser.js
// File Browser Modal — browse, read, edit, upload, download server files.
import uiModule from './ui.js';
import markdownModule from './markdown.js';
import { makeWindowDraggable } from './windowDrag.js';
import * as Modals from './modalManager.js';

const API_BASE = window.location.origin;
const _t = (k, v) => (window.__t || (kk => kk))(k, v);

// State
let _open = false;
let _currentPath = '/';
let _entries = [];
let _selectedFile = null;
let _editMode = false;
let _editContent = '';
let _searchQuery = '';
let _sortField = 'name';
let _sortAsc = true;
let _previewContent = null;
let _contextMenuEl = null;
let _dragCounter = 0;
let _escHandler = null;

// DOM references
let _modal = null;
let _listEl = null;
let _previewEl = null;
let _breadcrumbEl = null;
let _searchInput = null;

// ---- Helpers ----

function _esc(s) {
  return uiModule.esc ? uiModule.esc(s || '') : (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _attrEsc(s) {
  return String(s || '').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _formatSize(bytes) {
  if (bytes === null || bytes === undefined) return '';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function _formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d)) return '';
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return _t('files.justNow', 'just now');
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
}

function _getFileIcon(entry) {
  const svgOpen = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px">';
  const svgClose = '</svg>';
  if (entry.is_dir) return `${svgOpen}<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
  const ext = (entry.name || '').split('.').pop().toLowerCase();
  const codeExts = ['js', 'ts', 'jsx', 'tsx', 'py', 'rb', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'css', 'scss', 'less', 'html', 'xml', 'json', 'yaml', 'yml', 'toml', 'sh', 'bash', 'zsh'];
  const imgExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'];
  const docExts = ['md', 'txt', 'rtf', 'doc', 'docx', 'pdf'];
  if (codeExts.includes(ext)) return `${svgOpen}<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`;
  if (imgExts.includes(ext)) return `${svgOpen}<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>`;
  if (docExts.includes(ext)) return `${svgOpen}<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`;
  return `${svgOpen}<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
}

function _isTextFile(name) {
  const textExts = ['txt', 'md', 'json', 'js', 'ts', 'jsx', 'tsx', 'py', 'rb', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'css', 'scss', 'less', 'html', 'xml', 'yaml', 'yml', 'toml', 'sh', 'bash', 'zsh', 'sql', 'csv', 'log', 'ini', 'cfg', 'conf', 'env', 'gitignore', 'dockerignore', 'dockerfile', 'makefile', 'readme', 'license'];
  const ext = (name || '').split('.').pop().toLowerCase();
  return textExts.includes(ext) || !ext;
}

function _sortBy(field, asc, entries) {
  const dirs = entries.filter(e => e.is_dir);
  const files = entries.filter(e => !e.is_dir);
  const comparator = (a, b) => {
    let va, vb;
    if (field === 'name') {
      va = (a.name || '').toLowerCase();
      vb = (b.name || '').toLowerCase();
      return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    }
    if (field === 'size') {
      va = a.size || 0;
      vb = b.size || 0;
      return asc ? va - vb : vb - va;
    }
    if (field === 'mtime') {
      va = new Date(a.mtime || 0).getTime();
      vb = new Date(b.mtime || 0).getTime();
      return asc ? va - vb : vb - va;
    }
    return 0;
  };
  dirs.sort(comparator);
  files.sort(comparator);
  return [...dirs, ...files];
}

function _getFilteredEntries() {
  let entries = _entries;
  if (_searchQuery) {
    const q = _searchQuery.toLowerCase();
    entries = entries.filter(e => (e.name || '').toLowerCase().includes(q));
  }
  return _sortBy(_sortField, _sortAsc, entries);
}

// ---- API ----

async function _fetchEntries(path) {
  try {
    const res = await fetch(`${API_BASE}/api/files/browse?path=${encodeURIComponent(path)}`, { credentials: 'same-origin' });
    if (!res.ok) {
      const err = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${err ? ': ' + err.slice(0, 100) : ''}`);
    }
    const data = await res.json();
    _currentPath = data.path || path;
    _entries = (data.items || data.entries || []).map(e => {
      const isDir = e.is_dir !== undefined ? e.is_dir : (e.type === 'dir' || e.type === 'directory');
      return {
        ...e,
        is_dir: isDir,
        path: e.path || (_currentPath === '/' ? '/' + e.name : _currentPath + '/' + e.name),
      };
    });
  } catch (e) {
    console.error('Failed to fetch entries:', e);
    uiModule.showError(`Failed to load: ${e.message}`);
    _entries = [];
  }
}

async function _readFile(path) {
  try {
    const res = await fetch(`${API_BASE}/api/files/read?path=${encodeURIComponent(path)}`, { credentials: 'same-origin' });
    if (!res.ok) {
      const err = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${err ? ': ' + err.slice(0, 100) : ''}`);
    }
    const data = await res.json();
    return data.content || '';
  } catch (e) {
    console.error('Failed to read file:', e);
    uiModule.showError(`Failed to read: ${e.message}`);
    return null;
  }
}

async function _writeFile(path, content) {
  try {
    const res = await fetch(`${API_BASE}/api/files/write`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, content }),
    });
    if (!res.ok) {
      const err = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${err ? ': ' + err.slice(0, 100) : ''}`);
    }
    return true;
  } catch (e) {
    console.error('Failed to write file:', e);
    uiModule.showError(`Failed to save: ${e.message}`);
    return false;
  }
}

async function _createDir(path) {
  try {
    const res = await fetch(`${API_BASE}/api/files/mkdir`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      const err = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${err ? ': ' + err.slice(0, 100) : ''}`);
    }
    return true;
  } catch (e) {
    console.error('Failed to create directory:', e);
    uiModule.showError(`Failed to create folder: ${e.message}`);
    return false;
  }
}

async function _deleteEntry(path) {
  try {
    const res = await fetch(`${API_BASE}/api/files/delete`, {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      const err = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${err ? ': ' + err.slice(0, 100) : ''}`);
    }
    return true;
  } catch (e) {
    console.error('Failed to delete:', e);
    uiModule.showError(`Failed to delete: ${e.message}`);
    return false;
  }
}

async function _uploadFile(dirPath, file) {
  try {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('path', dirPath);
    const res = await fetch(`${API_BASE}/api/files/upload`, {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (!res.ok) {
      const err = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${err ? ': ' + err.slice(0, 100) : ''}`);
    }
    return true;
  } catch (e) {
    console.error('Failed to upload:', e);
    uiModule.showError(`Upload failed: ${e.message}`);
    return false;
  }
}

function _downloadFile(path) {
  const a = document.createElement('a');
  a.href = `${API_BASE}/api/files/download?path=${encodeURIComponent(path)}`;
  a.download = '';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 100);
}

// ---- UI Rendering ----

function _renderBreadcrumb() {
  if (!_breadcrumbEl) return;
  const parts = _currentPath.split('/').filter(Boolean);
  let html = `<button class="fb-breadcrumb-item fb-breadcrumb-home" data-path="/" title="Home"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg></button>`;
  let accumulated = '';
  for (let i = 0; i < parts.length; i++) {
    accumulated += '/' + parts[i];
    const p = accumulated;
    html += `<span class="fb-breadcrumb-sep">/</span><button class="fb-breadcrumb-item" data-path="${_attrEsc(p)}">${_esc(parts[i])}</button>`;
  }
  _breadcrumbEl.innerHTML = html;
}

function _renderFileList() {
  if (!_listEl) return;
  const filtered = _getFilteredEntries();
  if (!filtered.length) {
    _listEl.innerHTML = `<div class="fb-empty">${_searchQuery ? _t('files.noMatch', 'No matching files') : _t('files.emptyFolder', 'Empty folder')}</div>`;
    return;
  }
  const rows = filtered.map(e => {
    const icon = _getFileIcon(e);
    const size = e.is_dir ? '' : _formatSize(e.size);
    const mtime = _formatDate(e.mtime);
    const selected = _selectedFile === e.path ? ' fb-selected' : '';
    return `<tr class="fb-row${selected}" data-path="${_attrEsc(e.path)}" data-is-dir="${e.is_dir ? '1' : '0'}">
      <td class="fb-cell-icon">${icon}</td>
      <td class="fb-cell-name">${_esc(e.name)}</td>
      <td class="fb-cell-size">${size}</td>
      <td class="fb-cell-mtime">${mtime}</td>
    </tr>`;
  }).join('');

  const sortIndicator = (field) => {
    if (_sortField !== field) return '';
    return _sortAsc ? ' ▲' : ' ▼';
  };

  _listEl.innerHTML = `<table class="fb-table">
    <thead><tr>
      <th class="fb-th-icon"></th>
      <th class="fb-th-name" data-sort="name">${_t('files.name', 'Name')}${sortIndicator('name')}</th>
      <th class="fb-th-size" data-sort="size">${_t('files.size', 'Size')}${sortIndicator('size')}</th>
      <th class="fb-th-mtime" data-sort="mtime">${_t('files.modified', 'Modified')}${sortIndicator('mtime')}</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function _renderPreview(path, isDir) {
  if (!_previewEl) return;
  if (isDir) {
    _previewEl.innerHTML = `<div class="fb-preview-empty">${_t('files.selectFile', 'Select a file to preview')}</div>`;
    return;
  }
  if (!_isTextFile(path)) {
    _previewEl.innerHTML = `<div class="fb-preview-empty">${_t('files.noPreview', 'Preview not available for this file type')}</div>`;
    return;
  }
  _previewEl.innerHTML = `<div class="fb-preview-loading">${_t('files.loading', 'Loading...')}</div>`;
  const content = await _readFile(path);
  if (content === null) {
    _previewEl.innerHTML = `<div class="fb-preview-empty">${_t('files.loadFailed', 'Failed to load preview')}</div>`;
    return;
  }
  _previewContent = content;
  if (path.endsWith('.md')) {
    _previewEl.innerHTML = `<div class="fb-preview-content fb-preview-markdown">${markdownModule.mdToHtml(content)}</div>`;
  } else {
    _previewEl.innerHTML = `<pre class="fb-preview-content fb-preview-code">${_esc(content)}</pre>`;
  }
}

function _renderEditor() {
  if (!_previewEl) return;
  _previewEl.innerHTML = `<div class="fb-editor">
    <textarea class="fb-editor-textarea">${_esc(_editContent)}</textarea>
    <div class="fb-editor-actions">
      <button class="fb-btn fb-btn-primary" id="fb-save-btn">${_t('files.save', 'Save')}</button>
      <button class="fb-btn fb-btn-secondary" id="fb-cancel-btn">${_t('files.cancel', 'Cancel')}</button>
    </div>
  </div>`;
  const textarea = _previewEl.querySelector('.fb-editor-textarea');
  if (textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = textarea.scrollHeight + 'px';
    textarea.addEventListener('input', () => {
      textarea.style.height = 'auto';
      textarea.style.height = textarea.scrollHeight + 'px';
    });
  }
  document.getElementById('fb-save-btn')?.addEventListener('click', _handleSave);
  document.getElementById('fb-cancel-btn')?.addEventListener('click', _handleCancelEdit);
}

function _render() {
  _renderBreadcrumb();
  _renderFileList();
  if (_editMode) {
    _renderEditor();
  } else if (_selectedFile) {
    _renderPreview(_selectedFile, false);
  } else {
    _renderPreview(_currentPath, true);
  }
}

// ---- Event Handlers ----

function _handleRowClick(e) {
  const row = e.target.closest('.fb-row');
  if (!row) return;
  const path = row.dataset.path;
  const isDir = row.dataset.isDir === '1';
  if (isDir) {
    _selectedFile = null;
    _currentPath = path;
    _editMode = false;
    _fetchEntries(path).then(_render);
  } else {
    _selectedFile = path;
    _editMode = false;
    _renderFileList();
    _renderPreview(path, false);
  }
}

function _handleRowDblClick(e) {
  const row = e.target.closest('.fb-row');
  if (!row) return;
  const isDir = row.dataset.isDir === '1';
  if (isDir) return;
  const path = row.dataset.path;
  _selectedFile = path;
  _startEdit();
}

function _handleContextMenu(e) {
  e.preventDefault();
  const row = e.target.closest('.fb-row');
  if (!row) return;
  const path = row.dataset.path;
  const isDir = row.dataset.isDir === '1';
  _selectedFile = path;
  _renderFileList();
  _showContextMenu(e.clientX, e.clientY, path, isDir);
}

function _showContextMenu(x, y, path, isDir) {
  _dismissContextMenu();
  const menu = document.createElement('div');
  menu.className = 'fb-context-menu';
  let html = `<button class="fb-ctx-item" data-action="open">${_t('files.open', 'Open')}</button>`;
  if (!isDir) {
    html += `<button class="fb-ctx-item" data-action="edit">${_t('files.edit', 'Edit')}</button>`;
    html += `<button class="fb-ctx-item" data-action="download">${_t('files.download', 'Download')}</button>`;
  }
  html += `<button class="fb-ctx-item fb-ctx-danger" data-action="delete">${_t('files.delete', 'Delete')}</button>`;
  menu.innerHTML = html;
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  document.body.appendChild(menu);
  _contextMenuEl = menu;

  const adjustPos = () => {
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) menu.style.left = `${x - rect.width}px`;
    if (rect.bottom > window.innerHeight) menu.style.top = `${y - rect.height}px`;
  };
  requestAnimationFrame(adjustPos);

  menu.addEventListener('click', async (ev) => {
    const item = ev.target.closest('.fb-ctx-item');
    if (!item) return;
    const action = item.dataset.action;
    _dismissContextMenu();
    if (action === 'open') {
      if (isDir) {
        _currentPath = path;
        _selectedFile = null;
        await _fetchEntries(path);
        _render();
      } else {
        _selectedFile = path;
        _renderFileList();
        _renderPreview(path, false);
      }
    } else if (action === 'edit') {
      _selectedFile = path;
      await _startEdit();
    } else if (action === 'download') {
      _downloadFile(path);
    } else if (action === 'delete') {
      await _handleDelete(path);
    }
  });

  const dismiss = (ev) => {
    if (!menu.contains(ev.target)) {
      _dismissContextMenu();
      document.removeEventListener('click', dismiss, true);
    }
  };
  setTimeout(() => document.addEventListener('click', dismiss, true), 0);
}

function _dismissContextMenu() {
  if (_contextMenuEl) {
    _contextMenuEl.remove();
    _contextMenuEl = null;
  }
}

async function _startEdit() {
  if (!_selectedFile) return;
  const content = await _readFile(_selectedFile);
  if (content === null) return;
  _editMode = true;
  _editContent = content;
  _render();
}

async function _handleSave() {
  const textarea = _previewEl?.querySelector('.fb-editor-textarea');
  if (!textarea || !_selectedFile) return;
  const content = textarea.value;
  const ok = await _writeFile(_selectedFile, content);
  if (ok) {
    uiModule.showToast(_t('files.saved', 'File saved'));
    _editMode = false;
    _editContent = '';
    _renderPreview(_selectedFile, false);
  }
}

function _handleCancelEdit() {
  _editMode = false;
  _editContent = '';
  _render();
}

async function _handleDelete(path) {
  const name = path.split('/').pop();
  const confirmed = await uiModule.styledConfirm(_t('files.confirmDelete', 'Delete "{name}"?').replace('{name}', name), { danger: true, confirmText: _t('files.delete', 'Delete') });
  if (!confirmed) return;
  const ok = await _deleteEntry(path);
  if (ok) {
    uiModule.showToast(_t('files.deleted', 'Deleted'));
    if (_selectedFile === path) {
      _selectedFile = null;
      _editMode = false;
    }
    await _fetchEntries(_currentPath);
    _render();
  }
}

async function _handleNewFolder() {
  const name = await uiModule.styledPrompt(_t('files.folderName', 'Folder name:'), { title: _t('files.newFolder', 'New Folder'), placeholder: 'folder-name' });
  if (!name) return;
  const path = _currentPath === '/' ? `/${name}` : `${_currentPath}/${name}`;
  const ok = await _createDir(path);
  if (ok) {
    uiModule.showToast(_t('files.folderCreated', 'Folder created'));
    await _fetchEntries(_currentPath);
    _render();
  }
}

async function _handleNewFile() {
  const name = await uiModule.styledPrompt(_t('files.fileName', 'File name:'), { title: _t('files.newFile', 'New File'), placeholder: 'file.txt' });
  if (!name) return;
  const path = _currentPath === '/' ? `/${name}` : `${_currentPath}/${name}`;
  const ok = await _writeFile(path, '');
  if (ok) {
    uiModule.showToast(_t('files.fileCreated', 'File created'));
    _selectedFile = path;
    await _fetchEntries(_currentPath);
    await _startEdit();
    _render();
  }
}

function _handleUploadClick() {
  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = true;
  input.style.cssText = 'position:fixed;left:-9999px;top:-9999px;';
  document.body.appendChild(input);
  input.addEventListener('change', async () => {
    for (const file of input.files || []) {
      await _uploadFile(_currentPath, file);
    }
    input.remove();
    if (input.files?.length) {
      uiModule.showToast(_t('files.uploaded', 'Uploaded {n} file(s)').replace('{n}', input.files.length));
      await _fetchEntries(_currentPath);
      _render();
    }
  });
  input.click();
}

function _handleRefresh() {
  _fetchEntries(_currentPath).then(_render);
}

function _handleSearchInput(e) {
  _searchQuery = e.target.value.trim();
  _renderFileList();
}

function _handleSort(e) {
  const th = e.target.closest('[data-sort]');
  if (!th) return;
  const field = th.dataset.sort;
  if (_sortField === field) {
    _sortAsc = !_sortAsc;
  } else {
    _sortField = field;
    _sortAsc = true;
  }
  _renderFileList();
}

// ---- Drag & Drop ----

function _handleDragEnter(e) {
  e.preventDefault();
  e.stopPropagation();
  _dragCounter++;
  if (_modal) _modal.querySelector('.modal-content')?.classList.add('fb-drag-over');
}

function _handleDragLeave(e) {
  e.preventDefault();
  e.stopPropagation();
  _dragCounter--;
  if (_dragCounter <= 0) {
    _dragCounter = 0;
    if (_modal) _modal.querySelector('.modal-content')?.classList.remove('fb-drag-over');
  }
}

function _handleDragOver(e) {
  e.preventDefault();
  e.stopPropagation();
}

async function _handleDrop(e) {
  e.preventDefault();
  e.stopPropagation();
  _dragCounter = 0;
  if (_modal) _modal.querySelector('.modal-content')?.classList.remove('fb-drag-over');
  const files = e.dataTransfer?.files;
  if (!files?.length) return;
  for (const file of files) {
    await _uploadFile(_currentPath, file);
  }
  uiModule.showToast(_t('files.uploaded', 'Uploaded {n} file(s)').replace('{n}', files.length));
  await _fetchEntries(_currentPath);
  _render();
}

// ---- Modal Management ----

function _wireEvents() {
  if (!_modal) return;
  const content = _modal.querySelector('.modal-content');
  _listEl = content?.querySelector('.fb-list');
  _previewEl = content?.querySelector('.fb-preview');
  _breadcrumbEl = content?.querySelector('.fb-breadcrumb');
  _searchInput = content?.querySelector('.fb-search-input');

  _listEl?.addEventListener('click', _handleRowClick);
  _listEl?.addEventListener('dblclick', _handleRowDblClick);
  _listEl?.addEventListener('contextmenu', _handleContextMenu);
  _listEl?.addEventListener('click', _handleSort);

  _searchInput?.addEventListener('input', _handleSearchInput);

  content?.querySelector('.fb-upload-btn')?.addEventListener('click', _handleUploadClick);
  content?.querySelector('.fb-new-folder-btn')?.addEventListener('click', _handleNewFolder);
  content?.querySelector('.fb-new-file-btn')?.addEventListener('click', _handleNewFile);
  content?.querySelector('.fb-refresh-btn')?.addEventListener('click', _handleRefresh);
  document.getElementById('fb-close-btn')?.addEventListener('click', closePanel);

  _breadcrumbEl?.addEventListener('click', async (e) => {
    const item = e.target.closest('.fb-breadcrumb-item');
    if (!item) return;
    const path = item.dataset.path;
    _currentPath = path;
    _selectedFile = null;
    _editMode = false;
    await _fetchEntries(path);
    _render();
  });

  // Drag & drop
  content?.addEventListener('dragenter', _handleDragEnter);
  content?.addEventListener('dragleave', _handleDragLeave);
  content?.addEventListener('dragover', _handleDragOver);
  content?.addEventListener('drop', _handleDrop);

  // Keyboard
  _escHandler = (e) => {
    if (e.key === 'Escape') {
      if (_editMode) {
        _handleCancelEdit();
      } else {
        closePanel();
      }
    }
  };
  document.addEventListener('keydown', _escHandler);
}

function _createModal() {
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'filebrowser-modal';
  modal.innerHTML = `
    <div class="modal-content fb-modal-content">
      <div class="modal-header">
        <h4>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          ${_t('nav.files', 'Files')}
        </h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="fb-close-btn" title="${_t('common.close', 'Close')}">&times;</button>
      </div>
      <div class="modal-body">
        <div class="fb-breadcrumb"></div>
        <div class="fb-toolbar">
          <button class="fb-btn fb-upload-btn" title="${_t('files.uploadFiles', 'Upload files')}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>${_t('files.upload', 'Upload')}</button>
          <button class="fb-btn fb-new-folder-btn" title="${_t('files.newFolder', 'New folder')}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>${_t('files.newFolder', 'New Folder')}</button>
          <button class="fb-btn fb-new-file-btn" title="${_t('files.newFile', 'New file')}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/></svg>${_t('files.newFile', 'New File')}</button>
          <button class="fb-btn fb-refresh-btn" title="${_t('files.refresh', 'Refresh')}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></button>
          <div class="fb-search">
            <input type="text" class="fb-search-input" placeholder="${_t('files.search', 'Search files...')}" autocomplete="off" />
          </div>
        </div>
        <div class="fb-body">
          <div class="fb-list"></div>
          <div class="fb-preview"></div>
        </div>
      </div>
    </div>`;

  document.body.appendChild(modal);

  // Make draggable
  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  if (content && header) {
    makeWindowDraggable(modal, { content, header });
  }

  // Close on backdrop click
  modal.addEventListener('click', (e) => {
    if (uiModule.isTouchInsideModal && uiModule.isTouchInsideModal()) return;
    if (e.target === modal) closePanel();
  });

  return modal;
}

export function openPanel() {
  if (_open) return;
  _open = true;
  _editMode = false;
  _editContent = '';
  _selectedFile = null;
  _searchQuery = '';
  _sortField = 'name';
  _sortAsc = true;

  _modal = _createModal();
  _wireEvents();
  _fetchEntries('').then(_render);
}

export function closePanel() {
  if (!_open) return;
  _open = false;
  _editMode = false;
  _editContent = '';
  _dismissContextMenu();
  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler);
    _escHandler = null;
  }
  if (_modal) {
    const content = _modal.querySelector('.modal-content');
    const modal = _modal;
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => modal.remove(), { once: true });
      setTimeout(() => { if (modal.parentElement) modal.remove(); }, 250);
    } else {
      modal.remove();
    }
  }
  _modal = null;
  _listEl = null;
  _previewEl = null;
  _breadcrumbEl = null;
  _searchInput = null;
}

export function togglePanel() {
  if (_open) closePanel();
  else openPanel();
}

export function isPanelOpen() {
  return _open;
}

export default { openPanel, closePanel, togglePanel, isPanelOpen };
