// document-reference-compat: activeDocId - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: docs - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: markdownModule - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: renderTabs - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: API_BASE - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: switchToDoc - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: saveDocument - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: uiModule - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// static/js/document/previewModes.js
/**
 * Preview modes and live renderers for document editor.
 * Extracted from document.js for modularity, testability, and clean separation.
 *
 * Responsibilities:
 * 1. Markdown preview: _installMarkdownPreviewEditButton, _setMarkdownPreviewActive, toggleMarkdownPreview
 * 2. DOCX preview: _setDocxPreviewActive, toggleDocxPreview, _convertDocxToRichText
 * 3. CSV table view: parseCSV, csvEscapeField, syncTableToTextarea, toggleCsvPreview
 * 4. HTML sandbox preview: _themedRenderSrcdoc, toggleHtmlPreview, exitHtmlPreview
 */

let _deps = {};

export function initPreviewModes(options = {}) {
  _deps = { ...options };
}

// Accessor helpers with fallbacks for tests and standalone operation

const _getActiveDocId = () => {
  if (_deps.getActiveDocId) return _deps.getActiveDocId();
  if (typeof activeDocId !== 'undefined') return activeDocId;
  return null;
};

const _getDocs = () => {
  if (_deps.getDocs) return _deps.getDocs();
  if (typeof docs !== 'undefined') return docs;
  return null;
};

const _getTextarea = () => {
  if (_deps.getTextarea) return _deps.getTextarea();
  if (typeof document !== 'undefined') return document.getElementById('doc-editor-textarea');
  return null;
};

const _getEditorWrap = () => {
  if (_deps.getEditorWrap) return _deps.getEditorWrap();
  if (typeof document !== 'undefined') return document.getElementById('doc-editor-wrap');
  return null;
};

const _getMdPreview = () => {
  if (_deps.getMdPreview) return _deps.getMdPreview();
  if (typeof document !== 'undefined') return document.getElementById('doc-md-preview');
  return null;
};

const _getCsvPreview = () => {
  if (_deps.getCsvPreview) return _deps.getCsvPreview();
  if (typeof document !== 'undefined') return document.getElementById('doc-csv-preview');
  return null;
};

const _getHtmlPreview = () => {
  if (_deps.getHtmlPreview) return _deps.getHtmlPreview();
  if (typeof document !== 'undefined') return document.getElementById('doc-html-preview');
  return null;
};


const _getLanguage = () => {
  if (_deps.getLanguage) return _deps.getLanguage();
  if (typeof document !== 'undefined') return document.getElementById('doc-language-select')?.value || '';
  return '';
};

const _getMarkdownModule = () => {
  if (_deps.getMarkdownModule) return _deps.getMarkdownModule();
  if (typeof markdownModule !== 'undefined') return markdownModule;
  if (typeof window !== 'undefined' && window.markdownModule) return window.markdownModule;
  return null;
};


const _isRichTextLang = (l) => {
  if (_deps.isRichTextLang) return _deps.isRichTextLang(l);
  return ['richtext', 'rich-text'].includes((l || '').toLowerCase());
};

const _isDocxLang = (l) => {
  if (_deps.isDocxLang) return _deps.isDocxLang(l);
  return (l || '').toLowerCase() === 'docx';
};


const _escHtml = (s) => {
  if (_deps.escHtml) return _deps.escHtml(s);
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
};

const _syncHeaderActions = () => {
  if (_deps.syncHeaderActions) return _deps.syncHeaderActions();
};

const _syncRichEmptyImport = (el) => {
  if (_deps.syncRichEmptyImport) return _deps.syncRichEmptyImport(el);
};


const _renderTabs = () => {
  if (_deps.renderTabs) return _deps.renderTabs();
  if (typeof renderTabs === 'function') return renderTabs();
};

// ---------------------------------------------------------------------------
// 1. Markdown preview
// ---------------------------------------------------------------------------

/** Toggle markdown preview */
export function _installMarkdownPreviewEditButton(preview) {
  if (!preview || preview.querySelector('.doc-preview-hover-edit')) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'doc-preview-hover-edit';
  button.title = 'Edit document';
  button.setAttribute('aria-label', 'Edit document');
  button.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg><span>Edit</span>';
  button.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    _setMarkdownPreviewActive(false, { remember: true });
    requestAnimationFrame(() => {
      const rich = document.getElementById('doc-email-richbody');
      if (rich && rich.style.display !== 'none') rich.focus();
      else document.getElementById('doc-editor-textarea')?.focus();
    });
  });
  preview.prepend(button);
}

export function _setMarkdownPreviewActive(active, { remember = true } = {}) {
  const preview = _getMdPreview();
  const wrap = _getEditorWrap();
  const textarea = _getTextarea();
  const emptyImport = typeof document !== 'undefined' ? document.getElementById('doc-rich-empty-import') : null;
  if (!preview || !wrap || !textarea) return;

  const mdMod = _getMarkdownModule();
  const docMap = _getDocs();
  const docId = _getActiveDocId();
  const currentLang = _getLanguage();
  const richMode = _isRichTextLang(currentLang);

  if (active) {
    // The import action belongs to the empty editor, not the preview. Keep
    // the preview surface to a single Edit action.
    if (emptyImport) emptyImport.style.display = 'none';
    const rich = typeof document !== 'undefined' ? document.getElementById('doc-email-richbody') : null;
    if (!richMode && rich && rich.style.display !== 'none' && rich.classList.contains('markdown-visual-mode')) {
      _deps.syncEmailRichbody?.(rich);
      if (docMap?.get(docId)?._visualSyncBlocked) return;
      rich.style.display = 'none';
    }
    _deps.hideMarkdownCompatBanner?.();
    const md = textarea.value || '';
    if (richMode) {
      const sanitized = typeof mdMod?.sanitizeAllowedHtml === 'function'
        ? mdMod.sanitizeAllowedHtml(md)
        : _escHtml(md).replace(/\n/g, '<br>');
      const safe = typeof sanitized === 'string' ? sanitized : '';
      preview.classList.add('doc-rich-preview');
      preview.innerHTML = safe || '<p class="doc-rich-preview-empty">No preview yet.</p>';
    } else if (mdMod && mdMod.mdToHtml) {
      preview.innerHTML = mdMod.mdToHtml(md, { shortcodes: false, commonmark: true }); // doc preview: shortcodes stay literal, CommonMark escapes apply
    } else {
      preview.innerHTML = md.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g, '<br>');
    }
    if (typeof window !== 'undefined' && window.hljs) {
      preview.querySelectorAll('pre code').forEach(b => window.hljs.highlightElement(b));
    }
    if (mdMod && mdMod.renderMermaid) {
      mdMod.renderMermaid(preview);
    }
    _installMarkdownPreviewEditButton(preview);
    preview.style.display = '';
    wrap.style.display = 'none';
    if (richMode && rich) rich.style.display = 'none';
  } else {
    preview.style.display = 'none';
    preview.innerHTML = '';
    preview.classList.remove('doc-rich-preview');
    const activeDoc = docMap && docId ? docMap.get(docId) : null;
    const isEmailDoc = activeDoc?.language === 'email';
    const richEmailBody = typeof document !== 'undefined' ? document.getElementById('doc-email-richbody') : null;
    if (richMode && richEmailBody) {
      richEmailBody.style.display = '';
      wrap.style.display = 'none';
    } else if (!isEmailDoc && activeDoc && _deps.isVisualMarkdownDoc?.(activeDoc) && _deps.resolveMarkdownEditorMode?.(activeDoc) === 'visual') {
      _deps.showMarkdownVisualEditor?.(activeDoc);
    } else {
      if (!isEmailDoc && richEmailBody) {
        richEmailBody.classList.remove('markdown-visual-mode');
        richEmailBody.style.display = 'none';
      }
      if (!(isEmailDoc && richEmailBody && richEmailBody.style.display !== 'none')) {
        wrap.style.display = '';
      }
      if (!isEmailDoc && activeDoc?._visualCompatReasons?.length) {
        _deps.renderMarkdownCompatBanner?.(activeDoc._visualCompatReasons);
      }
    }
    _syncRichEmptyImport(richEmailBody);
  }
  if (remember && docId && docMap && docMap.has(docId)) {
    if (richMode) docMap.get(docId)._richPreviewActive = !!active;
    else docMap.get(docId)._markdownPreviewActive = !!active;
  }
  _syncHeaderActions();
}

export function _isMarkdownPreviewVisible() {
  const preview = _getMdPreview();
  return !!(preview && preview.style.display !== 'none');
}

export function toggleMarkdownPreview() {
  const preview = _getMdPreview();
  _setMarkdownPreviewActive(!(preview && preview.style.display !== 'none'));
}

export function toggleRichTextPreview() {
  const preview = _getMdPreview();
  _setMarkdownPreviewActive(!(preview && preview.style.display !== 'none'));
}

// ---------------------------------------------------------------------------
// 2. DOCX preview
// ---------------------------------------------------------------------------

  let _docxPreviewRequest = 0;
  export async function _setDocxPreviewActive(active, { remember = true } = {}) {
    const requestId = ++_docxPreviewRequest;
    const docId = (typeof _deps !== 'undefined' && _deps.getActiveDocId) ? _deps.getActiveDocId() : (typeof activeDocId !== 'undefined' ? activeDocId : null);
    const isCurrent = () => requestId === _docxPreviewRequest && ((typeof _deps !== 'undefined' && _deps.getActiveDocId) ? _deps.getActiveDocId() : (typeof activeDocId !== 'undefined' ? activeDocId : null)) === docId;
    const preview = (typeof _deps !== 'undefined' && _deps.getDocxPreview ? _deps.getDocxPreview() : null) || (typeof document !== 'undefined' ? document.getElementById('doc-docx-preview') : null);
    const wrap = (typeof _deps !== 'undefined' && _deps.getEditorWrap ? _deps.getEditorWrap() : null) || (typeof document !== 'undefined' ? document.getElementById('doc-editor-wrap') : null);
    const docMap = (typeof _deps !== 'undefined' && _deps.getDocs) ? _deps.getDocs() : (typeof docs !== 'undefined' ? docs : null);
    const syncHeaders = (typeof _deps !== 'undefined' && _deps.syncHeaderActions) ? _deps.syncHeaderActions : (typeof _syncHeaderActions !== 'undefined' ? _syncHeaderActions : () => {});
    const mdMod = (typeof _deps !== 'undefined' && _deps.getMarkdownModule) ? _deps.getMarkdownModule() : (typeof markdownModule !== 'undefined' ? markdownModule : (typeof window !== 'undefined' ? window.markdownModule : null));
    const esc = (typeof _deps !== 'undefined' && _deps.escHtml) ? _deps.escHtml : (typeof _escHtml !== 'undefined' ? _escHtml : (s => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')));
    const apiBase = (typeof _deps !== 'undefined' && _deps.getApiBase) ? _deps.getApiBase() : (typeof API_BASE !== 'undefined' ? API_BASE : '');

    if (!preview || !wrap) return;
    if (!active) {
      preview.style.display = 'none';
      if (typeof preview.replaceChildren === 'function') {
        preview.replaceChildren();
      } else {
        preview.innerHTML = '';
      }
      wrap.style.display = '';
      if (remember && docId && docMap && docMap.has(docId)) docMap.get(docId)._docxPreviewActive = false;
      syncHeaders();
      return;
    }
    preview.style.display = '';
    wrap.style.display = 'none';
    preview.innerHTML = '<div class="doc-docx-preview-loading">Loading Word preview...</div>';
    try {
      const response = await fetch(`${apiBase}/api/document/${encodeURIComponent(docId)}/render-docx`, { credentials: 'same-origin' });
      const payload = await response.json().catch(() => ({}));
      if (!isCurrent()) return;
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      const raw = String(payload.html || '');
      const safe = mdMod && mdMod.sanitizeAllowedHtml
        ? mdMod.sanitizeAllowedHtml(raw)
        : esc(raw);
      preview.innerHTML = `<article class="doc-docx-paper">${safe || '<p>No preview content.</p>'}</article>`;
      if (remember && docId && docMap && docMap.has(docId)) docMap.get(docId)._docxPreviewActive = true;
    } catch (error) {
      if (!isCurrent()) return;
      preview.innerHTML = `<div class="doc-docx-preview-error">Could not render Word preview: ${esc(error.message || error)}</div>`;
    }
    syncHeaders();
  }

  export function toggleDocxPreview() {
    const preview = (typeof _deps !== 'undefined' && _deps.getDocxPreview ? _deps.getDocxPreview() : null) || (typeof document !== 'undefined' ? document.getElementById('doc-docx-preview') : null);
    _setDocxPreviewActive(!(preview && preview.style.display !== 'none'));
  }

  export async function _convertDocxToRichText(arrayBuffer) {
    const docId = (typeof _deps !== 'undefined' && _deps.getActiveDocId) ? _deps.getActiveDocId() : (typeof activeDocId !== 'undefined' ? activeDocId : null);
    const docMap = (typeof _deps !== 'undefined' && _deps.getDocs) ? _deps.getDocs() : (typeof docs !== 'undefined' ? docs : null);
    const doc = docMap ? docMap.get(docId) : null;
    const checkDocx = (typeof _deps !== 'undefined' && _deps.isDocxLang) ? _deps.isDocxLang : (typeof _isDocxLang !== 'undefined' ? _isDocxLang : ((l) => (l || '').toLowerCase() === 'docx'));
    if (!doc || !checkDocx(doc.language)) return;
    const originalContent = doc.content;
    const apiBase = (typeof _deps !== 'undefined' && _deps.getApiBase) ? _deps.getApiBase() : (typeof API_BASE !== 'undefined' ? API_BASE : '');
    const switchDoc = (typeof _deps !== 'undefined' && _deps.switchToDoc) ? _deps.switchToDoc : (typeof switchToDoc !== 'undefined' ? switchToDoc : () => {});
    const saveDoc = (typeof _deps !== 'undefined' && _deps.saveDocument) ? _deps.saveDocument : (typeof saveDocument !== 'undefined' ? saveDocument : () => {});
    const ui = (typeof _deps !== 'undefined' && _deps.getUiModule) ? _deps.getUiModule() : (typeof uiModule !== 'undefined' ? uiModule : (typeof window !== 'undefined' ? window.uiModule : null));

    try {
      let html = '';
      if (arrayBuffer && typeof window !== 'undefined' && window.mammoth) {
        const result = await window.mammoth.convertToHtml({ arrayBuffer });
        html = String(result?.value || '');
      } else {
        const response = await fetch(`${apiBase}/api/document/${encodeURIComponent(docId)}/render-docx`, { credentials: 'same-origin' });
        const payload = await response.json().catch(() => ({}));
        // A conversion must not replace another tab, concurrent edits, or a
        // document that was closed while the server was rendering it.
        const currentActiveId = (typeof _deps !== 'undefined' && _deps.getActiveDocId) ? _deps.getActiveDocId() : (typeof activeDocId !== 'undefined' ? activeDocId : null);
        if (currentActiveId !== docId || docMap.get(docId) !== doc
            || doc.content !== originalContent || !checkDocx(doc.language)) return;
        if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
        html = String(payload.html || '');
      }
      const currentActiveId = (typeof _deps !== 'undefined' && _deps.getActiveDocId) ? _deps.getActiveDocId() : (typeof activeDocId !== 'undefined' ? activeDocId : null);
      if (currentActiveId !== docId || docMap.get(docId) !== doc
          || doc.content !== originalContent || !checkDocx(doc.language)) return;
      if (!html.trim()) throw new Error('The DOCX contained no readable content');
      doc.content = html;
      doc.language = 'richtext';
      doc._docxPreviewActive = false;
      const textarea = (typeof _deps !== 'undefined' && _deps.getTextarea ? _deps.getTextarea() : null) || (typeof document !== 'undefined' ? document.getElementById('doc-editor-textarea') : null);
      if (textarea) textarea.value = html;
      switchDoc(currentActiveId);
      await saveDoc({ silent: true, forceVersion: true });
      if (ui?.showToast) ui.showToast('Converted DOCX to Rich Text');
    } catch (error) {
      if (ui?.showError) ui.showError(`DOCX conversion failed: ${error.message || error}`);
    }
  }

// ---------------------------------------------------------------------------
// 3. CSV table view
// ---------------------------------------------------------------------------

  /** Parse CSV text into a 2D array (handles quoted fields) */
export function parseCSV(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') { inQuotes = false; }
      else { field += ch; }
    } else {
      if (ch === '"') { inQuotes = true; }
      else if (ch === ',') { row.push(field); field = ''; }
      else if (ch === '\n' || (ch === '\r' && text[i + 1] === '\n')) {
        if (ch === '\r') i++;
        row.push(field); field = '';
        if (row.some(c => c.trim())) rows.push(row);
        row = [];
      } else { field += ch; }
    }
  }
  row.push(field);
  if (row.some(c => c.trim())) rows.push(row);
  return rows;
}

/** Escape a CSV field (quote if it contains comma, quote, or newline) */
export function csvEscapeField(val) {
  const str = String(val ?? '');
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return '"' + str.replace(/"/g, '""') + '"';
  }
  return str;
}

/** Rebuild CSV text from the live table DOM */
export function syncTableToTextarea(preview, textarea) {
  const table = preview?.querySelector?.('.csv-table');
  if (!table || !textarea) return;
  const lines = [];
  // Header
  const ths = table.querySelectorAll('thead th');
  if (ths.length) lines.push([...ths].map(th => csvEscapeField(th.textContent)).join(','));
  // Body
  table.querySelectorAll('tbody tr').forEach(tr => {
    const cells = [...tr.querySelectorAll('td')].map(td => csvEscapeField(td.textContent));
    lines.push(cells.join(','));
  });
  textarea.value = lines.join('\n') + '\n';
  if (typeof textarea.dispatchEvent === 'function') {
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

/** Toggle CSV table preview */
export function toggleCsvPreview() {
  const preview = _getCsvPreview();
  const wrap = _getEditorWrap();
  const textarea = _getTextarea();
  if (!preview || !wrap || !textarea) return;

  if (preview.style.display === 'none') {
    const rows = parseCSV(textarea.value || '');
    if (rows.length === 0) {
      // Re-route the "no data" message to the shared run-output block so
      // every doc type surfaces errors/empty-state in the same place
      // (instead of stamping it inside the table view itself).
      let outputPanel = typeof document !== 'undefined' ? document.getElementById('doc-run-output') : null;
      if (!outputPanel && typeof document !== 'undefined') {
        outputPanel = document.createElement('div');
        outputPanel.id = 'doc-run-output';
        outputPanel.className = 'doc-run-output';
        const editorWrap = _getEditorWrap();
        if (editorWrap) editorWrap.after(outputPanel);
      }
      if (outputPanel) {
        outputPanel.style.display = 'block';
        outputPanel.innerHTML = '<pre class="doc-run-error">No data: CSV is empty or unparseable.</pre>';
      }
      return;
    } else {
      const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const colCount = Math.max(...rows.map(r => r.length));
      let html = '<div class="csv-table-wrap"><table class="csv-table"><thead><tr>';
      for (let j = 0; j < colCount; j++) {
        html += `<th contenteditable="true">${esc(rows[0][j] || '')}</th>`;
      }
      html += '</tr></thead><tbody>';
      for (let i = 1; i < rows.length; i++) {
        html += '<tr>';
        for (let j = 0; j < colCount; j++) {
          html += `<td contenteditable="true">${esc(rows[i][j] || '')}</td>`;
        }
        html += '</tr>';
      }
      html += '</tbody></table>';
      html += '</div>';
      preview.innerHTML = html;

      // Sync edits back to textarea
      const table = preview.querySelector('.csv-table');
      if (table) {
        table.addEventListener('input', () => syncTableToTextarea(preview, textarea));
        // Prevent Enter from creating <br> inside cells
        table.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            // Move to next row, same column
            const cell = e.target.closest('td,th');
            if (!cell) return;
            const colIdx = [...cell.parentElement.children].indexOf(cell);
            const nextRow = cell.parentElement.nextElementSibling;
            if (nextRow && nextRow.children[colIdx]) {
              nextRow.children[colIdx].focus();
            }
          } else if (e.key === 'Tab') {
            e.preventDefault();
            const cell = e.target.closest('td,th');
            if (!cell) return;
            const next = e.shiftKey ? cell.previousElementSibling : cell.nextElementSibling;
            if (next) next.focus();
          }
        });
      }

      // Add row button
      const addBtn = preview.querySelector('.csv-add-row-btn');
      if (addBtn && table) {
        addBtn.addEventListener('click', () => {
          const tbody = table.querySelector('tbody');
          const tr = document.createElement('tr');
          for (let j = 0; j < colCount; j++) {
            const td = document.createElement('td');
            td.contentEditable = 'true';
            tr.appendChild(td);
          }
          tbody.appendChild(tr);
          tr.children[0].focus();
          syncTableToTextarea(preview, textarea);
        });
      }
    }
    preview.style.display = '';
    wrap.style.display = 'none';
  } else {
    preview.style.display = 'none';
    wrap.style.display = '';
  }
  // Update the segmented Code/Run toggle's active class so the icon
  // highlights match the new state: without this, opening a CSV that
  // auto-shows the table view left the Edit (code) side wrongly marked
  // active and the user had to flip the toggle to resync.
  _syncHeaderActions();
}

// ---------------------------------------------------------------------------
// 4. HTML sandbox preview
// ---------------------------------------------------------------------------

export let _htmlPreviewActive = false;

export function isHtmlPreviewActive() {
  return _htmlPreviewActive;
}

export function setHtmlPreviewActive(active) {
  _htmlPreviewActive = !!active;
}

/** Toggle inline HTML preview (iframe) */
export function _themedRenderSrcdoc(code, lang) {
  const rootStyle = typeof getComputedStyle === 'function' && typeof document !== 'undefined' && document.documentElement
    ? getComputedStyle(document.documentElement)
    : null;
  const bg = (rootStyle && rootStyle.getPropertyValue('--bg')?.trim()) || '#111';
  const fg = (rootStyle && rootStyle.getPropertyValue('--fg')?.trim()) || '#f5f5f5';
  const theme = `html,body{margin:0;min-height:100%;background:${bg};color:${fg}}body{box-sizing:border-box;padding:20px}`;
  const source = String(code || '');
  if ((lang || '').toLowerCase() === 'svg' || (lang || '').toLowerCase() === 'xml') {
    return `<!doctype html><html><head><style>${theme}</style></head><body>${source}</body></html>`;
  }
  if (/<head\b[^>]*>/i.test(source)) {
    return source.replace(/(<head\b[^>]*>)/i, `$1<style>${theme}</style>`);
  }
  return `<style>${theme}</style>${source}`;
}

export function toggleHtmlPreview() {
  const iframe = _getHtmlPreview();
  const wrap = _getEditorWrap();
  const textarea = _getTextarea();
  if (!iframe || !wrap || !textarea) return;

  if (!_htmlPreviewActive) {
    // Show preview: hide markdown preview if active
    const mdPreview = _getMdPreview();
    if (mdPreview) mdPreview.style.display = 'none';
    const code = textarea.value || '';
    const lang = _getLanguage();
    iframe.srcdoc = _themedRenderSrcdoc(code, lang);
    iframe.style.display = '';
    wrap.style.display = 'none';
    _htmlPreviewActive = true;
    _renderTabs();
  } else {
    exitHtmlPreview();
  }
}

/** Exit HTML preview back to code view */
export function exitHtmlPreview() {
  const iframe = _getHtmlPreview();
  const wrap = _getEditorWrap();
  if (!_htmlPreviewActive) return;
  _htmlPreviewActive = false;
  if (iframe) { iframe.style.display = 'none'; iframe.srcdoc = ''; }
  if (wrap) wrap.style.display = '';
  _renderTabs();
}
