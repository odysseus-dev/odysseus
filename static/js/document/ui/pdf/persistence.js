// document-reference-compat: API_BASE - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: activeDocId - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: docs - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// frontend/src/features/documents/ui/pdf/persistence.js
/**
 * Per-document PDF pane state, undo snapshot stack, serialization, and autosave lifecycle.
 */

import { _encodeFieldName, _writeAnnotations } from './annotations.js';

// Tracks which form-backed docs the user has toggled into PDF view
// (per-doc, in-memory). Survives switches between docs in the same session.
export const _pdfViewState = new Map();
export const _pdfPaneFieldsByDoc = new Map(); // docId -> [{name, type, inputEl, ...}]
export const _pdfPaneAnnotationsByDoc = new Map(); // docId -> [{id, page, x, y, w, h, el, wrap}]
export const _pdfUndoStackByDoc = new Map(); // docId -> markdown snapshots
export const _pdfPaneSaveTimers = new Map(); // docId -> timeoutId
export const _pdfRenderGenerationByDoc = new Map();
export const _pdfViewTransitionByDoc = new Map();
export const _pdfLastLineHeight = new Map(); // docId -> number
export const _pdfLastFontSize = new Map(); // docId -> number
export const _sigCache = new Map();

export let _pdfMountedDocId = null;
export let _pdfPaneSaveTimer = null;

export function setPdfMountedDocId(docId) {
  _pdfMountedDocId = docId || null;
}

function _syncPdfPaneTimerExport() {
  _pdfPaneSaveTimer = _pdfPaneSaveTimers.values().next().value || null;
}

let _deps = {
  getApiBase: () => '',
  getActiveDocId: () => null,
  getDoc: () => null,
  getDocs: () => new Map(),
  saveDocument: null,
  markDocumentDirty: null,
  dismissDocKb: () => {},
  getUiModule: () => (typeof window !== 'undefined' ? window.uiModule : null),
  getSpinnerModule: () => (typeof window !== 'undefined' ? window.spinnerModule : null),
  getSignatureModule: () => (typeof window !== 'undefined' ? window.signatureModule : null),
};

export function configurePdfRuntimeDeps(options = {}) {
  _deps = { ..._deps, ...options };
}

export function getPdfRuntimeDeps() {
  return _deps;
}

export function _getApiBase() {
  if (_deps.getApiBase) {
    const val = _deps.getApiBase();
    if (val != null) return val;
  }
  if (typeof API_BASE !== 'undefined') return API_BASE;
  return '';
}

export function _getActiveDocId() {
  if (_deps.getActiveDocId) {
    const id = _deps.getActiveDocId();
    if (id != null) return id;
  }
  if (typeof activeDocId !== 'undefined') return activeDocId;
  return null;
}

export function _getDoc(docId) {
  if (_deps.getDoc) {
    const d = _deps.getDoc(docId);
    if (d != null) return d;
  }
  if (typeof docs !== 'undefined' && docs.get) {
    return docs.get(docId);
  }
  return null;
}

export async function _persistPdfDocument(docId, md, opts = {}) {
  const doc = _getDoc(docId);
  if (!docId || !doc) return false;
  doc.content = md;
  if (typeof document !== 'undefined' && docId === _getActiveDocId()) {
    const ta = document.getElementById('doc-editor-textarea');
    if (ta) ta.value = md;
  }
  if (typeof _deps.markDocumentDirty === 'function') {
    _deps.markDocumentDirty(docId);
  }
  if (typeof _deps.saveDocument === 'function') {
    return await _deps.saveDocument({
      silent: true,
      docId,
      keepalive: !!opts.keepalive,
      skipPdfSync: true,
    });
  }
  const apiBase = _getApiBase();
  const res = await fetch(`${apiBase}/api/document/${docId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: md }),
    keepalive: !!opts.keepalive,
  });
  if (!res.ok) {
    const t = await res.text().catch(() => res.statusText);
    const err = new Error(t || res.statusText || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return true;
}

export function _pdfMarkdownFromLive(docId = _getActiveDocId()) {
  const doc = _getDoc(docId);
  if (!doc) return null;
  return _composePdfPaneMarkdown(docId)?.md ?? doc.content ?? '';
}

export function _pushPdfUndoSnapshot(docId = _getActiveDocId()) {
  const md = _pdfMarkdownFromLive(docId);
  if (md == null) return;
  const stack = _pdfUndoStackByDoc.get(docId) || [];
  if (stack[stack.length - 1] === md) return;
  stack.push(md);
  if (stack.length > 50) stack.shift();
  _pdfUndoStackByDoc.set(docId, stack);
}

export function _cancelPdfPaneSave(docId = _getActiveDocId()) {
  if (!docId) return;
  const timer = _pdfPaneSaveTimers.get(docId);
  if (timer) {
    clearTimeout(timer);
    _pdfPaneSaveTimers.delete(docId);
    _syncPdfPaneTimerExport();
  }
}

export function _hasPendingPdfPaneSave(docId = _getActiveDocId()) {
  return Boolean(docId && _pdfPaneSaveTimers.has(docId));
}

// Call after capturing pending edits when this shared pane changes owner.
export function _releasePdfPane(docId) {
  if (!docId) return;
  _pdfPaneFieldsByDoc.delete(docId);
  _pdfPaneAnnotationsByDoc.delete(docId);
  _pdfRenderGenerationByDoc.set(docId, (_pdfRenderGenerationByDoc.get(docId) || 0) + 1);
  if (_pdfMountedDocId === docId) _pdfMountedDocId = null;
  if (typeof document !== 'undefined') {
    const pane = document.getElementById('doc-pdf-view');
    if (pane?.dataset.renderedDocId === docId) delete pane.dataset.renderedDocId;
  }
}

export function _cleanupPdfDocState(docId) {
  if (!docId) return;
  _cancelPdfPaneSave(docId);
  _releasePdfPane(docId);
  _pdfRenderGenerationByDoc.delete(docId);
  _pdfViewTransitionByDoc.delete(docId);
  _pdfUndoStackByDoc.delete(docId);
  _pdfLastLineHeight.delete(docId);
  _pdfLastFontSize.delete(docId);
  _pdfViewState.delete(docId);
}

export function _setPdfSaveStatus(status, msg, docId = _getActiveDocId()) {
  if (typeof document === 'undefined') return;
  if (docId && docId !== _getActiveDocId()) return;
  const pill = document.getElementById('doc-pdf-save-pill');
  if (!pill) return;
  const palette = {
    idle: { txt: '', bg: 'transparent', fg: 'transparent' },
    dirty: { txt: 'Editing…', bg: 'var(--panel)', fg: 'var(--fg)' },
    saving: { txt: 'Saving…', bg: 'var(--panel)', fg: 'var(--fg)' },
    saved: { txt: 'Saved', bg: 'rgba(34,197,94,0.85)', fg: '#fff' },
    error: { txt: msg || 'Save failed', bg: 'var(--red)', fg: 'var(--bg)' },
  };
  const p = palette[status] || palette.idle;
  pill.textContent = p.txt;
  pill.style.background = p.bg;
  pill.style.color = p.fg;
  pill.style.display = p.txt ? '' : 'none';
  if (status === 'saved') {
    const expectedDocId = docId;
    setTimeout(() => {
      if (expectedDocId && expectedDocId !== _getActiveDocId()) return;
      if (pill.isConnected && pill.textContent === 'Saved') _setPdfSaveStatus('idle', undefined, expectedDocId);
    }, 1200);
  }
}

export function _composePdfPaneMarkdown(docId) {
  const hasFields = _pdfPaneFieldsByDoc.has(docId);
  const hasAnnotations = _pdfPaneAnnotationsByDoc.has(docId);
  const fields = _pdfPaneFieldsByDoc.get(docId) || [];
  const annotations = _pdfPaneAnnotationsByDoc.get(docId) || [];
  if (!docId) return null;
  const doc = _getDoc(docId);
  if (!doc) return null;
  if (!hasFields && !hasAnnotations) return null;

  let md = doc.content || '';
  for (const ref of fields) {
    const encName = _encodeFieldName(ref.name);
    const re = new RegExp(
      `^(\\s*-\\s+)(.*?)(\\s*<!--\\s*field=${encName.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}\\s+type=\\w+\\s*-->\\s*)$`,
      'm'
    );
    const m = md.match(re);
    if (!m) continue;
    const body = m[2];
    let newBody = body;
    if (ref.type === 'checkbox') {
      const mark = ref.el.checked ? '[x]' : '[ ]';
      newBody = body.replace(/^\s*\[[ xX]\]/, mark);
    } else if (ref.type === 'choice') {
      const v = ref.el.value || '_(not selected)_';
      newBody = body.replace(/(\][\s]*:[ ]*).*$/, `$1${v}`);
    } else if (ref.type === 'signature') {
      const sid = ref.el.dataset.signatureId || '';
      const v = sid ? `signature:${sid}` : '_(unsigned)_';
      newBody = body.replace(/(:\*\*[ ]*).*$/, `$1${v}`);
    } else {
      const v = ref.el.value === '' ? '_(empty)_' : ref.el.value;
      newBody = body.replace(/(:\*\*[ ]*).*$/, `$1${v}`);
    }
    if (newBody !== body) {
      md = md.replace(re, `${m[1]}${newBody}${m[3]}`);
    }
  }
  if (hasAnnotations) {
    md = _writeAnnotations(md, annotations.map(a => {
      let value = '';
      if (a.kind === 'check') {
        value = '✓';
      } else if (a.kind === 'signature') {
        const sid = a.el && a.el.dataset && a.el.dataset.signatureId;
        value = sid ? `signature:${sid}` : '';
      } else {
        value = (a.el && typeof a.el.value === 'string') ? a.el.value : '';
      }
      return {
        id: a.id,
        page: a.page,
        x: a.x,
        y: a.y,
        w: a.w,
        h: a.h,
        kind: a.kind || 'text',
        lineHeight: a.lineHeight || 1.3,
        fontSize: a.fontSize || 11,
        value,
      };
    }));
  }
  return { doc, md, changed: md !== doc.content };
}

export function _syncPdfPaneToDoc(docId = _getActiveDocId(), { markDirty = true } = {}) {
  if (!docId) return false;
  const composed = _composePdfPaneMarkdown(docId);
  if (!composed || !composed.changed) return false;
  composed.doc.content = composed.md;
  if (typeof document !== 'undefined' && docId === _getActiveDocId()) {
    const ta = document.getElementById('doc-editor-textarea');
    if (ta) ta.value = composed.md;
  }
  if (markDirty && typeof _deps.markDocumentDirty === 'function') {
    _deps.markDocumentDirty(docId);
  }
  return true;
}

export function _schedulePdfPaneSave(docId = _getActiveDocId()) {
  if (!docId) return;
  // Removing a focused control can fire change while its old pane is being
  // replaced. A disposed view cannot revise or schedule the current draft.
  if (!_pdfPaneFieldsByDoc.has(docId) && !_pdfPaneAnnotationsByDoc.has(docId)) return;
  const doc = _getDoc(docId);
  if (!doc) return;
  _syncPdfPaneToDoc(docId, { markDirty: false });
  if (typeof _deps.markDocumentDirty === 'function') {
    _deps.markDocumentDirty(docId);
  }
  _setPdfSaveStatus('dirty', undefined, docId);
  _cancelPdfPaneSave(docId);
  const timer = setTimeout(() => {
    _pdfPaneSaveTimers.delete(docId);
    _syncPdfPaneTimerExport();
    _savePdfPaneToMarkdown(docId, { persistDraft: true });
  }, 600);
  _pdfPaneSaveTimers.set(docId, timer);
  _syncPdfPaneTimerExport();
}

export async function _flushPdfPaneSave(docId = _getActiveDocId(), opts = {}) {
  if (!docId) return false;
  const hadTimer = _hasPendingPdfPaneSave(docId);
  _cancelPdfPaneSave(docId);
  if (!hadTimer) {
    const composed = _composePdfPaneMarkdown(docId);
    if (!composed || !composed.changed) return false;
  }
  return await _savePdfPaneToMarkdown(docId, { ...opts, persistDraft: hadTimer });
}

export async function _savePdfPaneToMarkdown(targetDocId = _getActiveDocId(), opts = {}) {
  if (targetDocId && typeof targetDocId === 'object') {
    opts = targetDocId;
    targetDocId = opts.docId || _getActiveDocId();
  }
  const docId = targetDocId || opts.docId || _getActiveDocId();
  if (!docId) return false;
  _cancelPdfPaneSave(docId);
  const doc = _getDoc(docId);
  const composed = _composePdfPaneMarkdown(docId)
    || (opts.persistDraft && doc ? { doc, md: doc.content, changed: false } : null);
  if (!composed) return false;
  if (!composed.changed && !opts.persistDraft && !['dirty', 'error'].includes(doc?._saveState)) {
    _setPdfSaveStatus('idle', undefined, docId);
    return true;
  }
  _setPdfSaveStatus('saving', undefined, docId);
  try {
    const ok = await _persistPdfDocument(docId, composed.md, opts);
    if (!ok) {
      if (_getDoc(docId)) {
        _setPdfSaveStatus('error', 'Save failed', docId);
      } else {
        _setPdfSaveStatus('idle', undefined, docId);
      }
      return false;
    }
    _setPdfSaveStatus('saved', undefined, docId);
    return true;
  } catch (e) {
    const msg = e?.status ? `Save failed: ${e.status}` : (e.message || 'Save failed');
    _setPdfSaveStatus('error', msg, docId);
    console.warn('PDF-pane save failed:', e);
    return false;
  }
}

export async function _saveActiveDocBeforeExport() {
  const activeId = _getActiveDocId();
  if (!activeId) return;
  await _flushPdfPaneSave(activeId);
  if (typeof document === 'undefined') return;
  const ta = document.getElementById('doc-editor-textarea');
  const doc = _getDoc(activeId);
  if (!ta || !doc) return;
  const live = ta.value;
  if (live === doc.content) return;
  try {
    await _persistPdfDocument(activeId, live);
  } catch (e) {
    console.warn('Pre-export save failed:', e);
  }
}
