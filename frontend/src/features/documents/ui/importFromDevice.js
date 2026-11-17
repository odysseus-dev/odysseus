// frontend/src/features/documents/ui/importFromDevice.js
/**
 * Device file import flow (PDF, DOCX, spreadsheet deferral, and source/text files).
 */

const EXT_TO_LANG = Object.freeze({
  '.py': 'python', '.js': 'javascript', '.ts': 'typescript', '.html': 'html', '.htm': 'html',
  '.css': 'css', '.md': 'markdown', '.json': 'json', '.yml': 'yaml', '.yaml': 'yaml',
  '.sh': 'bash', '.bash': 'bash', '.sql': 'sql', '.rs': 'rust', '.go': 'go',
  '.java': 'java', '.c': 'c', '.cpp': 'cpp', '.h': 'c', '.hpp': 'cpp',
  '.rb': 'ruby', '.php': 'php', '.xml': 'xml', '.toml': 'toml', '.ini': 'ini',
  '.txt': '', '.log': '', '.csv': 'csv', '.tsv': 'csv', '.jsx': 'javascript', '.tsx': 'typescript',
});

let _deps = {};

export function initImportFromDevice(options = {}) {
  _deps = { ...options };
}

function _getUi() {
  return (_deps.getUiModule && _deps.getUiModule()) || (typeof window !== 'undefined' ? window.uiModule : null);
}

function _getSessionId() {
  const sessionMod = (_deps.getSessionModule && _deps.getSessionModule())
    || (typeof window !== 'undefined' ? window.sessionModule : null);
  const lastSid = (_deps.getLastSessionId && _deps.getLastSessionId()) || '';
  return sessionMod?.getCurrentSessionId?.() || lastSid || '';
}

async function _uploadDocumentForm(apiBase, endpoint, file, sid) {
  const fd = new FormData();
  fd.append('file', file);
  if (sid) fd.append('session_id', sid);
  const r = await fetch(`${apiBase}${endpoint}`, { method: 'POST', body: fd, credentials: 'same-origin' });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, data: j };
}

export function _importFromDevice() {
  const fi = document.createElement('input');
  fi.type = 'file';
  fi.style.display = 'none';
  fi.addEventListener('change', async () => {
    const file = fi.files?.[0];
    if (!file) return;
    const name = file.name;
    const dotIdx = name.lastIndexOf('.');
    const ext = dotIdx >= 0 ? name.slice(dotIdx).toLowerCase() : '';
    const baseTitle = dotIdx > 0 ? name.slice(0, dotIdx) : name;
    const isSpreadsheet = ['.xlsx', '.xls', '.ods'].includes(ext);
    const isPdf = ext === '.pdf';
    const isDocx = ext === '.docx';
    const ui = _getUi();
    const apiBase = (_deps.getApiBase && _deps.getApiBase()) || '';
    const lastSid = (_deps.getLastSessionId && _deps.getLastSessionId()) || '';
    // Spreadsheets need the library per-sheet split, defer to it.
    if (isSpreadsheet) {
      if (_deps.openLibrary) _deps.openLibrary();
      requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById('doclib-import-file-btn')?.click()));
      return;
    }
    try {
      let docId = null;
      const sid = _getSessionId();
      if (isPdf) {
        const res = await _uploadDocumentForm(apiBase, '/api/documents/import-pdf', file, sid);
        if (!res.ok) throw new Error('PDF import failed');
        docId = res.data.doc_id || res.data.id;
      } else if (isDocx) {
        const res = await _uploadDocumentForm(apiBase, '/api/documents/import-docx', file, sid);
        if (!res.ok) throw new Error(res.data.detail || 'DOCX import failed');
        docId = res.data.id || res.data.doc_id;
      } else {
        const content = await new Promise((res, rej) => {
          const reader = new FileReader();
          reader.onload = () => res(reader.result || '');
          reader.onerror = () => rej(reader.error);
          reader.readAsText(file);
        });
        const lang = EXT_TO_LANG[ext] !== undefined ? EXT_TO_LANG[ext] : null;
        const body = { title: baseTitle, language: lang, content };
        if (sid) body.session_id = sid;
        const r = await fetch(`${apiBase}/api/document`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error('Import failed');
        const j = await r.json();
        docId = j.id || j.doc_id;
      }
      if (docId) {
        try {
          const dr = await fetch(`${apiBase}/api/document/${docId}`, { credentials: 'same-origin' });
          const full = dr.ok ? await dr.json() : { id: docId, title: baseTitle };
          const currentSid = _getSessionId();
          if (_deps.addDocToTabs) _deps.addDocToTabs(full, full.session_id || currentSid);
          if (_deps.switchToDoc) _deps.switchToDoc(full.id || docId);
        } catch (_) {
          if (_deps.addDocToTabs) _deps.addDocToTabs({ id: docId, title: baseTitle }, lastSid || '');
          if (_deps.switchToDoc) _deps.switchToDoc(docId);
        }
      }
    } catch (err) {
      if (ui?.showError) ui.showError('Import failed: ' + (err.message || err));
    } finally {
      fi.value = '';
      fi.remove();
    }
  });
  document.body.appendChild(fi);
  fi.click();
}
