// static/js/rag.js

/**
 * RAG (Retrieval Augmented Generation) management
 */

import uiModule from './ui.js';
import spinnerModule from './spinner.js';

let API_BASE = '';

export function init(apiBase) {
  API_BASE = apiBase;
  _setupUploadZone();
}

function _humanSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * Load and display RAG documents with delete buttons
 */
export async function loadPersonalDocs() {
  const box = document.getElementById('docs-view');
  if (!box) return;

  box.innerHTML = '';
  const { element: wpEl } = spinnerModule.createWhirlpool(24);
  wpEl.title = 'Loading…';
  box.appendChild(wpEl);

  try {
    const res = await fetch(`${API_BASE}/api/personal`, { credentials: 'same-origin' });
    const data = await res.json();
    const files = data.files || [];

    box.innerHTML = '';

    if (files.length === 0) {
      const placeholder = document.createElement('div');
      placeholder.textContent = 'Drop files above to add to RAG';
      placeholder.style.cssText = 'color:var(--color-muted);font-size:12px;padding:4px 0;';
      box.appendChild(placeholder);
      return;
    }

    files.forEach(f => {
      const row = document.createElement('div');
      row.className = 'list-item';
      row.style.cssText = 'display:flex;align-items:center;gap:4px;';

      const name = document.createElement('span');
      name.className = 'grow';
      name.textContent = f.name.split('/').pop();
      name.title = f.path || f.name;
      name.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      row.appendChild(name);

      const size = document.createElement('span');
      size.style.cssText = 'color:var(--color-muted);font-size:11px;flex-shrink:0;';
      size.textContent = _humanSize(f.size);
      row.appendChild(size);

      const del = document.createElement('button');
      del.className = 'rag-file-delete';
      del.textContent = 'x';
      del.title = 'Remove from RAG';
      del.style.cssText = 'background:none;border:none;color:var(--color-error);cursor:pointer;padding:2px 4px;font-size:12px;flex-shrink:0;';
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        _deleteFile(f.path || f.name, f.name.split('/').pop());
      });
      row.appendChild(del);

      box.appendChild(row);
    });
  } catch (e) {
    console.error(e);
    box.innerHTML = '';
    const error = document.createElement('div');
    error.textContent = 'Failed to load files';
    error.style.color = 'var(--color-error)';
    box.appendChild(error);
  }
}

async function _deleteFile(filepath, displayName) {
  if (!await uiModule.styledConfirm(`Remove "${displayName}" from RAG?`, { confirmText: 'Remove', danger: true })) return;
  try {
    const res = await fetch(`${API_BASE}/api/personal/file?filepath=${encodeURIComponent(filepath)}`, {
      method: 'DELETE',
      credentials: 'same-origin'
    });
    if (!res.ok) throw new Error(await res.text());
    await loadPersonalDocs();
  } catch (e) {
    console.error('Delete failed:', e);
    alert('Failed to delete file: ' + e.message);
  }
}

/**
 * Upload files to RAG
 */
export async function uploadRagFiles(fileList) {
  if (!fileList || !fileList.length) return;

  const zone = document.getElementById('rag-upload-zone');
  if (zone) zone.textContent = 'Uploading…';

  const fd = new FormData();
  for (const file of fileList) {
    fd.append('files', file);
  }

  try {
    const res = await fetch(`${API_BASE}/api/personal/upload`, {
      method: 'POST',
      credentials: 'same-origin',
      body: fd
    });

    const data = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    if (!res.ok || !data.success) {
      throw new Error(data.detail || data.error || data.message || 'Upload failed');
    }

    if (zone) zone.textContent = 'Drop files here or click to upload';
    await loadPersonalDocs();
    return data;
  } catch (e) {
    console.error('Upload failed:', e);
    if (zone) zone.textContent = 'Drop files here or click to upload';
    alert('Upload failed: ' + e.message);
  }
}

function _setupUploadZone() {
  const zone = document.getElementById('rag-upload-zone');
  const input = document.getElementById('rag-file-input');
  if (!zone || !input) return;

  zone.addEventListener('click', () => input.click());

  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('dragover');
  });

  zone.addEventListener('dragleave', () => {
    zone.classList.remove('dragover');
  });

  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    if (e.dataTransfer.files.length) {
      uploadRagFiles(e.dataTransfer.files);
    }
  });

  input.addEventListener('change', () => {
    if (input.files.length) {
      uploadRagFiles(input.files);
      input.value = '';
    }
  });
}

const ragModule = {
  init,
  loadPersonalDocs,
  uploadRagFiles
};

export default ragModule;
