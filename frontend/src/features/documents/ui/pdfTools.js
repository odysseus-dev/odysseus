// static/js/document/pdfTools.js
/**
 * PDF Viewer, Form-backed Markdown Docs, and Visual Annotation Engine.
 * Extracted from document.js for modularity, testability, and maintainability.
 */

// Tracks which form-backed docs the user has toggled into PDF view
// (per-doc, in-memory). Survives switches between docs in the same session.
const _pdfViewState = new Map();
const _pdfPaneFieldsByDoc = new Map(); // docId -> [{name, type, inputEl, ...}]
const _pdfPaneAnnotationsByDoc = new Map(); // docId -> [{id, page, x, y, w, h, el, wrap}]
const _pdfUndoStackByDoc = new Map(); // docId -> markdown snapshots
const _pdfPaneSaveTimers = new Map(); // docId -> timeoutId
let _pdfPaneSaveTimer = null;
let _pdfAnnotationMenuDismissWired = false;
let _pdfPaneProximityWired = false;
const _sigCache = new Map();

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

function _getApiBase() {
  if (_deps.getApiBase) {
    const val = _deps.getApiBase();
    if (val != null) return val;
  }
  if (typeof API_BASE !== 'undefined') return API_BASE;
  return '';
}

function _getActiveDocId() {
  if (_deps.getActiveDocId) {
    const id = _deps.getActiveDocId();
    if (id != null) return id;
  }
  if (typeof activeDocId !== 'undefined') return activeDocId;
  return null;
}

function _getDoc(docId) {
  if (_deps.getDoc) {
    const d = _deps.getDoc(docId);
    if (d != null) return d;
  }
  if (typeof docs !== 'undefined' && docs.get) {
    return docs.get(docId);
  }
  return null;
}

function _dismissDocKb() {
  if (_deps.dismissDocKb) {
    _deps.dismissDocKb();
    return;
  }
  if (typeof window === 'undefined') return;
  if (!('ontouchstart' in window || (navigator.maxTouchPoints || 0) > 0)) return;
  const ta = document.getElementById('doc-editor-textarea');
  const ae = document.activeElement;
  const el = (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) ? ae : ta;
  if (!el) return;
  try {
    el.setAttribute('readonly', 'readonly');
    el.blur();
    setTimeout(() => { try { el.removeAttribute('readonly'); } catch (_) {} }, 120);
  } catch (_) { try { el.blur(); } catch (_) {} }
}

function _getUiModule() {
  if (_deps.getUiModule) {
    const m = _deps.getUiModule();
    if (m) return m;
  }
  return typeof window !== 'undefined' ? window.uiModule : null;
}

function _getSpinnerModule() {
  if (_deps.getSpinnerModule) {
    const m = _deps.getSpinnerModule();
    if (m) return m;
  }
  return typeof window !== 'undefined' ? window.spinnerModule : null;
}

function _getSignatureModule() {
  if (_deps.getSignatureModule) {
    const m = _deps.getSignatureModule();
    if (m) return m;
  }
  return typeof window !== 'undefined' ? window.signatureModule : null;
}

const _escHtml = (s) => String(s || '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

// -----------------------------------------------------------------------------
// Coordinate conversion and scale calculation helpers
// -----------------------------------------------------------------------------

function clamp(val, min = 0, max = 100) {
  return Math.max(min, Math.min(max, val));
}

function pxToPct(px, total) {
  if (!total) return 0;
  return (px / total) * 100;
}

function pctToPx(pct, total) {
  if (!total) return 0;
  return (pct / 100) * total;
}

function calculateFontSizeCqh(fontSize = 11) {
  return (fontSize * 1.5) / 11;
}

function calculateFieldRectPct(rectPx = [0, 0, 0, 0], pageWidth = 1, pageHeight = 1) {
  const [x0, y0, x1, y1] = rectPx;
  const pw = pageWidth || 1;
  const ph = pageHeight || 1;
  return {
    left: pxToPct(x0, pw),
    top: pxToPct(y0, ph),
    width: pxToPct(x1 - x0, pw),
    height: pxToPct(y1 - y0, ph),
  };
}

function calculateAnnotationPlacement({
  clientX = 0,
  clientY = 0,
  pageRect = { left: 0, top: 0, width: 1, height: 1 },
  mode = 'text',
  pageNumber = 1,
  lastLineHeight = 1.3,
  lastFontSize = 11,
} = {}) {
  const rect = pageRect || { left: 0, top: 0, width: 1, height: 1 };
  const xPct = pxToPct(clientX - rect.left, rect.width);
  const yPct = pxToPct(clientY - rect.top, rect.height);
  const sizes = {
    text: { w: 8, h: 2.5 },
    check: { w: 2.5, h: 2.5 },
    signature: { w: 22, h: 6 },
  };
  const size = sizes[mode] || sizes.text;
  const centered = mode === 'check';
  const x = clamp(centered ? xPct - size.w / 2 : xPct, 0, 100 - size.w);
  const y = clamp(centered ? yPct - size.h / 2 : yPct, 0, 100 - size.h);
  return {
    id: _newAnnotationId(),
    page: pageNumber,
    x,
    y,
    w: size.w,
    h: size.h,
    value: mode === 'check' ? '[ ]' : '',
    kind: mode,
    lineHeight: mode === 'text' ? lastLineHeight : undefined,
    fontSize: mode === 'text' ? lastFontSize : undefined,
  };
}

function calculateDragMove(start, current, pageRect, widthPct, heightPct) {
  const pw = pageRect?.width || 1;
  const ph = pageRect?.height || 1;
  const dxPct = pxToPct(current.mx - start.mx, pw);
  const dyPct = pxToPct(current.my - start.my, ph);
  const w = widthPct || 0;
  const h = heightPct || 0;
  return {
    x: clamp(start.x + dxPct, 0, 100 - w),
    y: clamp(start.y + dyPct, 0, 100 - h),
  };
}

function calculateResize(start, current, pageRect, leftPct, topPct) {
  const pw = pageRect?.width || 1;
  const ph = pageRect?.height || 1;
  const dwPct = pxToPct(current.mx - start.mx, pw);
  const dhPct = pxToPct(current.my - start.my, ph);
  const x = leftPct || 0;
  const y = topPct || 0;
  return {
    w: clamp(start.w + dwPct, 1, 100 - x),
    h: clamp(start.h + dhPct, 0.8, 100 - y),
  };
}

// -----------------------------------------------------------------------------
// Form-backed document detection and export
// -----------------------------------------------------------------------------

function _isFormBackedDoc(docOrContent) {
  const c = typeof docOrContent === 'string'
    ? docOrContent
    : (docOrContent?.content || '');
  return /<!--\s*pdf_form_source\s+upload_id="[^"]+"/.test(c)
      || /<!--\s*pdf_source\s+upload_id="[^"]+"/.test(c);
}

async function _persistPdfDocument(docId, md, opts = {}) {
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

async function _saveActiveDocBeforeExport() {
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

async function _downloadFilledPdf() {
  const activeId = _getActiveDocId();
  if (!activeId) return;
  _dismissDocKb();
  await _saveActiveDocBeforeExport();
  try {
    const apiBase = _getApiBase();
    const r = await fetch(`${apiBase}/api/document/${activeId}/export-pdf`);
    if (!r.ok) {
      const t = await r.text();
      throw new Error(t || r.statusText);
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const cd = r.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^"';]+)/i);
    const _slug = (s) => (s || 'form').replace(/\.pdf$/i, '').replace(/\s+/g, '_').replace(/[^A-Za-z0-9._-]/g, '').replace(/_+/g, '_').replace(/^_|_$/g, '') || 'form';
    const doc = _getDoc(activeId);
    a.download = (m && decodeURIComponent(m[1])) || (_slug(doc?.title) + '_annotated.pdf');
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    const ui = _getUiModule();
    if (ui?.showError) ui.showError('Export failed: ' + e.message);
    else if (typeof alert === 'function') alert('Export failed: ' + e.message);
  }
}

// -----------------------------------------------------------------------------
// Annotation model serialization & parsing
// -----------------------------------------------------------------------------

function _annotationRegexGlobal() {
  return /^[ \t]*-\s+(.*?)\s*<!--\s*annotation\s+id=([\w-]+)\s+page=(\d+)\s+x=([\d.]+)\s+y=([\d.]+)\s+w=([\d.]+)\s+h=([\d.]+)(?:\s+kind=(\w+))?(?:\s+lh=([\d.]+))?(?:\s+fs=([\d.]+))?\s*-->[ \t]*$/gm;
}

function _escapeAnnotationValue(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/\n/g, '\\n');
}

function _unescapeAnnotationValue(s) {
  return String(s || '').replace(/\\(.)/g, (m, c) => c === 'n' ? '\n' : c === '\\' ? '\\' : m);
}

function _parseAnnotations(md) {
  const out = [];
  const re = _annotationRegexGlobal();
  let m;
  while ((m = re.exec(md || '')) !== null) {
    const rawVal = m[1] === '_(empty)_' ? '' : _unescapeAnnotationValue(m[1]);
    out.push({
      value: rawVal,
      id: m[2],
      page: parseInt(m[3], 10),
      x: parseFloat(m[4]),
      y: parseFloat(m[5]),
      w: parseFloat(m[6]),
      h: parseFloat(m[7]),
      kind: m[8] || 'text',
      lineHeight: m[9] ? parseFloat(m[9]) : 1.3,
      fontSize: m[10] ? parseFloat(m[10]) : 11,
    });
  }
  return out;
}

function _annotationLine(a) {
  const kind = a.kind || 'text';
  const lh = (a.lineHeight && Number.isFinite(a.lineHeight)) ? a.lineHeight : 1.3;
  const fs = (a.fontSize && Number.isFinite(a.fontSize)) ? a.fontSize : 11;
  const escaped = a.value === '' || a.value == null ? '_(empty)_' : _escapeAnnotationValue(a.value);
  return `- ${escaped} <!-- annotation id=${a.id} page=${a.page} x=${a.x.toFixed(2)} y=${a.y.toFixed(2)} w=${a.w.toFixed(2)} h=${a.h.toFixed(2)} kind=${kind} lh=${lh.toFixed(2)} fs=${fs.toFixed(1)} -->`;
}

function _writeAnnotations(md, annotations) {
  let out = (md || '').replace(_annotationRegexGlobal(), '');
  out = out.replace(/\n##\s+Annotations\s*\r?\n+/g, '\n');
  out = out.replace(/\n{3,}/g, '\n\n');
  if (!annotations || !annotations.length) return out;
  if (!out.endsWith('\n')) out += '\n';
  out += '\n## Annotations\n\n';
  for (const a of annotations) out += _annotationLine(a) + '\n';
  return out;
}

function _newAnnotationId() {
  return 'ann-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
}

// -----------------------------------------------------------------------------
// Gesture, placement, and undo history
// -----------------------------------------------------------------------------

function _encodeFieldName(name) {
  let out = '';
  for (const ch of name || '') {
    if (/[A-Za-z0-9_.\-]/.test(ch)) {
      out += ch;
    } else {
      const enc = new TextEncoder().encode(ch);
      for (const b of enc) out += '%' + b.toString(16).toUpperCase().padStart(2, '0');
    }
  }
  return out;
}

function _pdfMarkdownFromLive(docId = _getActiveDocId()) {
  const doc = _getDoc(docId);
  if (!doc) return null;
  const annotations = _pdfPaneAnnotationsByDoc.get(docId) || [];
  return _writeAnnotations(doc.content || '', annotations.map(a => {
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

function _pushPdfUndoSnapshot(docId = _getActiveDocId()) {
  const md = _pdfMarkdownFromLive(docId);
  if (md == null) return;
  const stack = _pdfUndoStackByDoc.get(docId) || [];
  if (stack[stack.length - 1] === md) return;
  stack.push(md);
  if (stack.length > 50) stack.shift();
  _pdfUndoStackByDoc.set(docId, stack);
}

async function _undoPdfPaneAction(docId = _getActiveDocId()) {
  if (!docId) return false;
  const stack = _pdfUndoStackByDoc.get(docId) || [];
  const prev = stack.pop();
  if (!prev) return false;
  _pdfUndoStackByDoc.set(docId, stack);
  _cancelPdfPaneSave(docId);
  const doc = _getDoc(docId);
  if (!doc) return false;
  _setPdfSaveStatus('saving', undefined, docId);
  try {
    const ok = await _persistPdfDocument(docId, prev);
    if (!ok) {
      _setPdfSaveStatus('error', 'Undo failed', docId);
      return true;
    }
    _setPdfSaveStatus('saved', undefined, docId);
    if (docId === _getActiveDocId()) {
      _renderPdfPane(docId);
    }
    return true;
  } catch (e) {
    _setPdfSaveStatus('error', e.message || 'Undo failed', docId);
    return true;
  }
}

let _pdfDropMode = null;
let _pdfAnnotationDragging = false;
function _setPdfAnnotationDragging(dragging) {
  _pdfAnnotationDragging = dragging;
  const currentDocId = typeof _getActiveDocId === 'function' ? _getActiveDocId() : (typeof activeDocId !== 'undefined' ? activeDocId : null);
  for (const ref of _pdfPaneAnnotationsByDoc.get(currentDocId) || []) {
    ref._refreshHandles?.();
    if (dragging) {
      const menu = ref.wrap?.querySelector('.pdf-annotation-text-menu');
      if (menu) menu.style.display = 'none';
    }
  }
}
// Per-doc last-used line spacing for text annotations.
const _pdfLastLineHeight = new Map(); // docId -> number
const _pdfLastFontSize = new Map(); // docId -> number

function _wirePdfAnnotationMenuDismiss() {
  if (_pdfAnnotationMenuDismissWired || typeof document === 'undefined') return;
  _pdfAnnotationMenuDismissWired = true;
  document.addEventListener('pointerdown', (ev) => {
    if (ev.target.closest('.pdf-annotation-text-menu, .pdf-annotation-menu-btn')) return;
    document.querySelectorAll('.pdf-annotation-text-menu').forEach(menu => {
      menu.style.display = 'none';
      menu.dataset.lhUndoCaptured = '0';
    });
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    document.querySelectorAll('.pdf-annotation-text-menu').forEach(menu => {
      menu.style.display = 'none';
      menu.dataset.lhUndoCaptured = '0';
    });
  });
}

function _setPdfDropMode(mode) {
  _pdfDropMode = mode;
  if (typeof document === 'undefined') return;
  const pane = document.getElementById('doc-pdf-view');
  if (pane) pane.style.cursor = mode ? 'crosshair' : '';
  for (const id of ['doc-pdf-add-text-btn', 'doc-pdf-add-check-btn', 'doc-pdf-add-sign-btn']) {
    const b = document.getElementById(id);
    if (!b) continue;
    const want = (mode === 'text' && id === 'doc-pdf-add-text-btn')
      || (mode === 'check' && id === 'doc-pdf-add-check-btn')
      || (mode === 'signature' && id === 'doc-pdf-add-sign-btn');
    b.style.outline = want ? '2px solid var(--accent-primary, var(--red))' : '';
  }
}

function _wirePdfPaneProximity(pane) {
  if (_pdfPaneProximityWired || !pane) return;
  _pdfPaneProximityWired = true;
  let raf = 0;
  const buffer = 44;
  pane.addEventListener('mousemove', (ev) => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const currentDocId = _getActiveDocId();
      const refs = _pdfPaneAnnotationsByDoc.get(currentDocId) || [];
      for (const ref of refs) {
        if (!ref || !ref.wrap || !ref._setHandlesVisible) continue;
        const r = ref.wrap.getBoundingClientRect();
        const dx = Math.max(r.left - ev.clientX, 0, ev.clientX - r.right);
        const dy = Math.max(r.top - ev.clientY, 0, ev.clientY - r.bottom);
        ref._setHandlesVisible(Math.hypot(dx, dy) <= buffer);
      }
    });
  });
  pane.addEventListener('mouseleave', () => {
    const currentDocId = _getActiveDocId();
    const refs = _pdfPaneAnnotationsByDoc.get(currentDocId) || [];
    for (const ref of refs) ref._setHandlesVisible && ref._setHandlesVisible(false);
  });
}

async function _pdfResponseErrorMessage(res) {
  const text = await res.text().catch(() => '');
  try {
    const data = JSON.parse(text);
    if (typeof data?.detail === 'string') return data.detail;
    if (data?.detail) return JSON.stringify(data.detail);
  } catch (_) {}
  return text || res.statusText || `HTTP ${res.status}`;
}

// -----------------------------------------------------------------------------
// PDF Pane rendering and live sync
// -----------------------------------------------------------------------------

async function _renderPdfPane(docId = _getActiveDocId(), options = {}) {
  if (typeof document === 'undefined') return;
  const pane = document.getElementById('doc-pdf-view');
  if (!pane || !docId) return;
  delete pane.dataset.renderedDocId;
  _wirePdfPaneProximity(pane);
  const savedPill = document.getElementById('doc-pdf-save-pill');
  pane.innerHTML = '';
  const spinner = _getSpinnerModule();
  const pdfLoading = spinner?.createLoadingRow ? spinner.createLoadingRow('Loading PDF…', 26) : document.createElement('div');
  pdfLoading.classList.add('pdf-loading-state');
  pane.appendChild(pdfLoading);
  if (savedPill) pane.appendChild(savedPill);
  let data;
  try {
    const apiBase = _getApiBase();
    const res = await fetch(`${apiBase}/api/document/${docId}/render-pages`);
    if (!res.ok) throw new Error(await _pdfResponseErrorMessage(res));
    data = await res.json();
  } catch (e) {
    pane.innerHTML = `<div style="color:#fbb;padding:40px;text-align:center;">Failed to load PDF view: ${_escHtml(e.message || String(e))}</div>`;
    if (savedPill) pane.appendChild(savedPill);
    return;
  }
  if (docId !== _getActiveDocId()) return;

  pane.dataset.renderedDocId = docId;
  pane.innerHTML = '';
  if (savedPill) pane.appendChild(savedPill);
  const fieldRefs = [];
  const annotationRefs = [];
  _pdfPaneAnnotationsByDoc.set(docId, annotationRefs);
  const doc = _getDoc(docId);
  const liveMd = (doc && doc.content) || '';
  const allAnnotations = _parseAnnotations(liveMd);

  if (!_pdfLastLineHeight.has(docId)) {
    for (let i = allAnnotations.length - 1; i >= 0; i--) {
      const a = allAnnotations[i];
      if (a.kind === 'text' && a.lineHeight) {
        _pdfLastLineHeight.set(docId, a.lineHeight);
        break;
      }
    }
  }
  if (!_pdfLastFontSize.has(docId)) {
    for (let i = allAnnotations.length - 1; i >= 0; i--) {
      const a = allAnnotations[i];
      if (a.kind === 'text' && Number.isFinite(a.fontSize)) {
        _pdfLastFontSize.set(docId, a.fontSize);
        break;
      }
    }
  }
  for (const page of (data.pages || [])) {
    const pageWrap = document.createElement('div');
    pageWrap.dataset.docId = docId;
    pageWrap.style.cssText = `position:relative;margin:0 auto 16px auto;width:${page.width}px;max-width:calc(100% - 24px);aspect-ratio:${page.width} / ${page.height};background:#fff;box-shadow:0 4px 16px rgba(0,0,0,0.4);container-type:size;`;
    const img = document.createElement('img');
    const apiBase = _getApiBase();
    img.src = `${apiBase}/api/document/${docId}/page/${page.page}.png`;
    img.style.cssText = 'display:block;width:100%;height:100%;user-select:none;-webkit-user-drag:none;pointer-events:none;';
    img.draggable = false;
    pageWrap.appendChild(img);

    for (const f of (page.fields || [])) {
      const [x0, y0, x1, y1] = f.rect_px || [0, 0, 0, 0];
      const wPct = ((x1 - x0) / page.width) * 100;
      const hPct = ((y1 - y0) / page.height) * 100;
      const lPct = (x0 / page.width) * 100;
      const tPct = (y0 / page.height) * 100;
      const isSig = f.type === 'signature' || /sign(?:ed|ature)/i.test((f.name || '') + ' ' + (f.label || ''));
      let el;
      const baseStyle = `position:absolute;left:${lPct}%;top:${tPct}%;width:${wPct}%;height:${hPct}%;box-sizing:border-box;font-family:inherit;`;
      if (isSig) {
        el = document.createElement('div');
        el.style.cssText = baseStyle + 'cursor:pointer;display:flex;align-items:center;justify-content:center;overflow:hidden;';
        el.dataset.fieldName = f.name;
        el.dataset.fieldType = 'signature';

        const initialSigId = (typeof f.value === 'string' && f.value.startsWith('signature:'))
          ? f.value.slice('signature:'.length).trim() : '';
        const renderSigUI = async (sigId) => {
          el.innerHTML = '';
          if (sigId) {
            el.dataset.signatureId = sigId;
            const imgEl = document.createElement('img');
            imgEl.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;pointer-events:none;';
            try {
              if (!_sigCache.has(sigId)) {
                const apiBase = typeof _getApiBase === "function" ? _getApiBase() : (typeof API_BASE !== "undefined" ? API_BASE : "");
          const r = await fetch(`${apiBase}/api/signatures`);
                const sdata = await r.json();
                for (const s of sdata.signatures || []) _sigCache.set(s.id, s.data_url);
              }
              const dataUrl = _sigCache.get(sigId);
              if (dataUrl) imgEl.src = dataUrl;
              else throw new Error('not found');
              el.appendChild(imgEl);
              el.style.border = '1px solid color-mix(in srgb, var(--accent, var(--red)) 45%, transparent)';
              el.style.background = 'transparent';
            } catch {
              el.removeAttribute('data-signature-id');
              renderSigUI('');
            }
          } else {
            delete el.dataset.signatureId;
            el.style.border = '1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent)';
            el.style.background = 'color-mix(in srgb, var(--accent, var(--red)) 10%, transparent)';
            const span = document.createElement('span');
            span.style.cssText = 'color:var(--accent, var(--red));font-size:11px;';
            span.textContent = 'Sign here';
            el.appendChild(span);
          }
        };
        el.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          const sigModule = typeof _getSignatureModule === "function" ? _getSignatureModule() : (typeof signatureModule !== "undefined" ? signatureModule : null);
          const sig = sigModule?.pick ? await sigModule.pick() : null;
          if (sig) {
            _sigCache.set(sig.id, sig.dataUrl);
            await renderSigUI(sig.id);
            _schedulePdfPaneSave(docId);
          }
        });
        renderSigUI(initialSigId);
      } else if (f.type === 'checkbox') {
        el = document.createElement('input');
        el.type = 'checkbox';
        el.checked = !!f.value;
        el.style.cssText = baseStyle + 'cursor:pointer;';
      } else if (f.type === 'choice' && (f.options || []).length) {
        el = document.createElement('select');
        const blank = document.createElement('option');
        blank.value = ''; blank.textContent = '-';
        el.appendChild(blank);
        for (const opt of f.options) {
          const o = document.createElement('option');
          o.value = opt; o.textContent = opt;
          if (opt === f.value) o.selected = true;
          el.appendChild(o);
        }
        el.style.cssText = baseStyle + 'border:1px solid color-mix(in srgb, var(--accent, var(--red)) 45%, transparent);background:rgba(255,255,255,0.85);font-size:11px;padding:0 2px;';
      } else {
        el = document.createElement('input');
        el.type = 'text';
        el.value = f.value == null ? '' : String(f.value);
        const fontPx = Math.max(8, Math.min(14, Math.round((y1 - y0) * 0.4)));
        el.style.cssText = baseStyle + `border:1px solid color-mix(in srgb, var(--accent, var(--red)) 45%, transparent);background:rgba(255,255,255,0.85);font-size:${fontPx}px;padding:0 2px;`;
      }
      if (!isSig) {
        el.dataset.fieldName = f.name;
        el.dataset.fieldType = f.type;
        el.addEventListener('input', () => _schedulePdfPaneSave(docId));
        el.addEventListener('change', () => _schedulePdfPaneSave(docId));
      }
      pageWrap.appendChild(el);
      fieldRefs.push({ name: f.name, type: isSig ? 'signature' : f.type, el });

      const isDate = f.type === 'text' && /\b(date|dated)\b/i.test(`${f.name} ${f.label}`);
      if (isDate) {
        const today = document.createElement('button');
        today.type = 'button';
        today.textContent = 'Today';
        today.title = "Set to today's date";
        today.style.cssText = `position:absolute;left:calc(${lPct}% + ${wPct}%);top:${tPct}%;height:${hPct}%;margin-left:4px;padding:0 6px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 55%, transparent);background:rgba(255,255,255,0.95);color:var(--accent, var(--red));border-radius:3px;cursor:pointer;font-size:10px;line-height:1;white-space:nowrap;`;
        today.addEventListener('click', () => {
          const d = new Date();
          const dd = String(d.getDate()).padStart(2, '0');
          const mm = String(d.getMonth() + 1).padStart(2, '0');
          const yyyy = d.getFullYear();
          el.value = `${dd}/${mm}/${yyyy}`;
          _schedulePdfPaneSave(docId);
        });
        pageWrap.appendChild(today);
      }
    }

    for (const ann of allAnnotations) {
      if (ann.page !== page.page) continue;
      const built = _buildAnnotation(pageWrap, ann);
      annotationRefs.push(built.ref);
    }

    const isBlankPageTarget = target => target === pageWrap
      || (target?.tagName === 'IMG' && target.parentElement === pageWrap);
    let placementStartedOnPage = false;
    pageWrap.addEventListener('pointerdown', ev => {
      placementStartedOnPage = isBlankPageTarget(ev.target);
    }, true);
    pageWrap.addEventListener('click', (ev) => {
      const canPlace = placementStartedOnPage;
      placementStartedOnPage = false;
      if (!canPlace || !isBlankPageTarget(ev.target) || _pdfAnnotationDragging) return;
      if (!_pdfDropMode) return;
      const rect = pageWrap.getBoundingClientRect();
      const xPct = ((ev.clientX - rect.left) / rect.width) * 100;
      const yPct = ((ev.clientY - rect.top) / rect.height) * 100;
      const sizes = {
        text: { w: 8, h: 2.5 },
        check: { w: 2.5, h: 2.5 },
        signature: { w: 22, h: 6 },
      };
      const size = sizes[_pdfDropMode] || sizes.text;
      const centered = _pdfDropMode === 'check';
      const x = Math.max(0, Math.min(100 - size.w, centered ? xPct - size.w / 2 : xPct));
      const y = Math.max(0, Math.min(100 - size.h, centered ? yPct - size.h / 2 : yPct));
      const ann = {
        id: _newAnnotationId(),
        docId,
        page: page.page,
        x, y, w: size.w, h: size.h,
        value: _pdfDropMode === 'check' ? '[ ]' : '',
        kind: _pdfDropMode,
        lineHeight: _pdfDropMode === 'text' ? (_pdfLastLineHeight.get(docId) || 1.3) : undefined,
        fontSize: _pdfDropMode === 'text' ? (_pdfLastFontSize.get(docId) || 11) : undefined,
      };
      _pushPdfUndoSnapshot(docId);
      const built = _buildAnnotation(pageWrap, ann);
      annotationRefs.push(built.ref);
      if (_pdfDropMode === 'text') {
        built.ref.el.focus();
      } else if (_pdfDropMode === 'signature') {
        built.ref.el.click();
      }
      _schedulePdfPaneSave(docId);
    });

    pane.appendChild(pageWrap);
  }
  _pdfPaneFieldsByDoc.set(docId, fieldRefs);
}

function _buildAnnotation(arg1, arg2, scale) {
  let pageWrap;
  let ann;
  if (arg1 && (arg1.nodeType || typeof arg1.appendChild === 'function')) {
    pageWrap = arg1;
    ann = arg2 || {};
  } else {
    ann = arg1 || {};
    pageWrap = arg2;
  }
  const capturedDocId = ann.docId || pageWrap?.dataset?.docId || null;
  const _annotationDocId = () => capturedDocId || (typeof _getActiveDocId === 'function' ? _getActiveDocId() : (typeof activeDocId !== 'undefined' ? activeDocId : null));
  const kind = ann.kind || 'text';
  const wrap = document.createElement('div');
  wrap.className = 'pdf-annotation-wrap';
  wrap.style.cssText = `position:absolute;left:${ann.x}%;top:${ann.y}%;width:${ann.w}%;height:${ann.h}%;box-sizing:border-box;z-index:2;`;
  wrap.dataset.annId = ann.id;
  wrap.dataset.annKind = kind;

  let input;
  if (kind === 'check') {
    input = document.createElement('div');
    input.style.cssText = `width:100%;height:100%;display:flex;align-items:center;justify-content:center;user-select:none;pointer-events:none;`;
    input.innerHTML = `<svg viewBox="0 0 24 24" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%;display:block;"><path d="M4 12 L10 18 L20 6" fill="none" stroke="#111" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  } else if (kind === 'signature') {
    input = document.createElement('div');
    input.style.cssText = `width:100%;height:100%;box-sizing:border-box;border:1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 10%, transparent);display:flex;align-items:center;justify-content:center;cursor:pointer;overflow:hidden;font-size:10px;color:var(--accent, var(--red));`;
    input.textContent = (ann.value && ann.value.startsWith('signature:')) ? '' : 'Sign here';
    input.dataset.signatureId = (ann.value && ann.value.startsWith('signature:')) ? ann.value.slice(10) : '';
  } else {
    input = document.createElement('textarea');
    input.value = ann.value || '';
    input.placeholder = 'Type…';
    input.rows = 1;
    input.spellcheck = false;
    const lh = ann.lineHeight || 1.3;
    const fs = ann.fontSize || 11;
    input.style.cssText = `width:100%;height:100%;box-sizing:border-box;border:1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 10%, transparent);font-family:inherit;font-size:${(fs * 1.5 / 11).toFixed(3)}cqh;line-height:${lh};padding:1px 4px;color:#111;resize:none;overflow:auto;white-space:pre-wrap;`;
  }

  const _isTouch = typeof matchMedia === 'function' && matchMedia('(hover: none)').matches;
  const HS = _isTouch ? 28 : 20;
  const OFF = -HS;
  const HIDE = _isTouch ? '' : 'none';

  const del = document.createElement('button');
  del.type = 'button';
  del.textContent = '×';
  del.title = 'Delete annotation';
  del.style.cssText = `position:absolute;top:${OFF}px;right:${OFF}px;width:${HS}px;height:${HS}px;padding:0;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:transparent;color:var(--accent, var(--red));border-radius:50%;cursor:pointer;font-size:20px;line-height:1;display:${HIDE};font-weight:400;touch-action:none;box-sizing:border-box;`;

  const grip = document.createElement('div');
  grip.title = 'Drag to move';
  grip.textContent = '☰';
  grip.style.cssText = `position:absolute;top:${OFF}px;left:${OFF}px;width:${HS}px;height:${HS}px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:transparent;color:var(--accent, var(--red));border-radius:3px;cursor:move;font-size:11px;line-height:${HS}px;text-align:center;display:${HIDE};touch-action:none;box-sizing:border-box;`;

  const resize = document.createElement('div');
  resize.title = 'Drag to resize';
  resize.style.cssText = `position:absolute;bottom:${OFF}px;right:${OFF}px;width:${HS}px;height:${HS}px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:transparent;color:var(--accent, var(--red));border-radius:3px;cursor:nwse-resize;display:${HIDE};touch-action:none;box-sizing:border-box;`;
  resize.innerHTML = '<svg width="14" height="14" viewBox="0 0 10 10" style="display:block;margin:auto;height:100%;"><path d="M2 8 L8 2 M5 8 L8 5" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg>';

  let menuBtn = null;
  if (kind === 'text') {
    menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'pdf-annotation-menu-btn';
    menuBtn.textContent = '…';
    menuBtn.title = 'Text annotation options';
    menuBtn.style.cssText = `position:absolute;bottom:${OFF}px;left:${OFF}px;width:${HS}px;height:${HS}px;padding:0;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:transparent;color:var(--accent, var(--red));border-radius:50%;cursor:pointer;font-size:15px;line-height:0.8;display:${HIDE};font-weight:bold;touch-action:none;box-sizing:border-box;`;
  }

  let handlesRequested = _isTouch;
  const _setHandlesVisible = (show) => {
    if (!_pdfAnnotationDragging) handlesRequested = show;
    const dismissed = wrap.dataset.controlsDismissed === '1';
    const v = (show && !dismissed && !_pdfAnnotationDragging) ? '' : 'none';
    del.style.display = v;
    grip.style.display = v;
    resize.style.display = v;
    if (menuBtn) menuBtn.style.display = v;
  };
  if (!_isTouch) {
    wrap.addEventListener('mouseenter', () => _setHandlesVisible(true));
    for (const h of [del, grip, resize, menuBtn].filter(Boolean)) {
      h.addEventListener('mouseenter', () => _setHandlesVisible(true));
    }
  }
  wrap.addEventListener('pointerdown', (ev) => {
    if (ev.target === del || ev.target === grip || ev.target === resize || ev.target === menuBtn) return;
    wrap.dataset.controlsDismissed = '0';
    _setHandlesVisible(true);
  });

  const ref = {
    id: ann.id,
    page: ann.page,
    x: ann.x,
    y: ann.y,
    w: ann.w,
    h: ann.h,
    el: input,
    wrap,
    kind,
    _setHandlesVisible,
    _refreshHandles: () => _setHandlesVisible(handlesRequested),
  };

  if (kind === 'check') {
    ref.value = '✓';
  } else if (kind === 'signature') {
    const _renderSig = async (sigId) => {
      input.innerHTML = '';
      if (!sigId) {
        input.dataset.signatureId = '';
        input.style.background = 'color-mix(in srgb, var(--accent, var(--red)) 10%, transparent)';
        input.style.border = '1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent)';
        const span = document.createElement('span');
        span.textContent = 'Sign here';
        input.appendChild(span);
        return;
      }
      input.dataset.signatureId = sigId;
      try {
        if (!_sigCache.has(sigId)) {
          const apiBase = typeof _getApiBase === "function" ? _getApiBase() : (typeof API_BASE !== "undefined" ? API_BASE : "");
          const r = await fetch(`${apiBase}/api/signatures`);
          const data = await r.json();
          for (const s of data.signatures || []) _sigCache.set(s.id, s.data_url);
        }
        const dataUrl = _sigCache.get(sigId);
        if (!dataUrl) throw new Error('not found');
        const img = document.createElement('img');
        img.src = dataUrl;
        img.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;pointer-events:none;';
        input.appendChild(img);
        input.style.background = 'transparent';
        input.style.border = '1px solid color-mix(in srgb, var(--accent, var(--red)) 45%, transparent)';
      } catch {
        _renderSig('');
      }
    };
    input.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const sigModule = typeof _getSignatureModule === "function" ? _getSignatureModule() : (typeof signatureModule !== "undefined" ? signatureModule : null);
      const sig = sigModule?.pick ? await sigModule.pick() : null;
      if (sig) {
        const targetId = _annotationDocId();
        _pushPdfUndoSnapshot(targetId);
        _sigCache.set(sig.id, sig.dataUrl);
        await _renderSig(sig.id);
        ref.value = `signature:${sig.id}`;
        _schedulePdfPaneSave(targetId);
      }
    });
    _renderSig(input.dataset.signatureId);
  } else {
    let _mirror = null;
    const _autoGrow = () => {
      if (!pageWrap) return;
      const pageRect = pageWrap.getBoundingClientRect();
      if (!pageRect.height || !pageRect.width) return;

      if (!_mirror) {
        _mirror = document.createElement('div');
        _mirror.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-family:inherit;padding:1px 4px;left:-9999px;top:-9999px;';
        document.body.appendChild(_mirror);
      }
      const cs = window.getComputedStyle(input);
      _mirror.style.fontSize = cs.fontSize;
      _mirror.style.fontWeight = cs.fontWeight;
      _mirror.style.fontFamily = cs.fontFamily;
      _mirror.style.letterSpacing = cs.letterSpacing;
      let widestPx = 0;
      const lines = (input.value || input.placeholder || '').split('\n');
      for (const line of lines) {
        _mirror.textContent = line || ' ';
        if (_mirror.offsetWidth > widestPx) widestPx = _mirror.offsetWidth;
      }
      const neededWPct = ((widestPx + 12) / pageRect.width) * 100;
      if (neededWPct > ref.w) {
        ref.w = Math.min(100 - ref.x, neededWPct);
        wrap.style.width = ref.w + '%';
      }

      const prev = input.style.height;
      input.style.height = 'auto';
      const neededHpx = input.scrollHeight + 4;
      input.style.height = prev || '100%';
      const neededHpct = (neededHpx / pageRect.height) * 100;
      if (neededHpct > ref.h) {
        ref.h = Math.min(100 - ref.y, neededHpct);
        wrap.style.height = ref.h + '%';
      }
    };
    input.addEventListener('input', () => {
      const targetId = _annotationDocId();
      if (wrap.dataset.textUndoCaptured !== '1') {
        _pushPdfUndoSnapshot(targetId);
        wrap.dataset.textUndoCaptured = '1';
      }
      ref.value = input.value;
      _autoGrow();
      _schedulePdfPaneSave(targetId);
    });
    input.addEventListener('change', () => {
      const targetId = _annotationDocId();
      ref.value = input.value;
      _autoGrow();
      _schedulePdfPaneSave(targetId);
    });
    input.addEventListener('focus', () => {
      _pushPdfUndoSnapshot(_annotationDocId());
      wrap.dataset.textUndoCaptured = '1';
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') input.blur();
    });
    requestAnimationFrame(_autoGrow);
    ref._autoGrow = _autoGrow;
  }

  del.addEventListener('pointerdown', ev => {
    ev.preventDefault();
    ev.stopPropagation();
  });
  del.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const targetId = _annotationDocId();
    _pushPdfUndoSnapshot(targetId);
    _removeAnnotation(ref, targetId);
  });

  grip.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const targetId = _annotationDocId();
    _pushPdfUndoSnapshot(targetId);
    try { grip.setPointerCapture(ev.pointerId); } catch (_) {}
    _setPdfAnnotationDragging(true);
    del.style.display = 'none';
    resize.style.display = 'none';
    if (menuBtn) menuBtn.style.display = 'none';
    const start = { mx: ev.clientX, my: ev.clientY, x: ref.x, y: ref.y };
    const rect = pageWrap ? pageWrap.getBoundingClientRect() : { width: 1, height: 1 };
    const onMove = (e) => {
      const dxPct = ((e.clientX - start.mx) / rect.width) * 100;
      const dyPct = ((e.clientY - start.my) / rect.height) * 100;
      ref.x = Math.max(0, Math.min(100 - ref.w, start.x + dxPct));
      ref.y = Math.max(0, Math.min(100 - ref.h, start.y + dyPct));
      wrap.style.left = ref.x + '%';
      wrap.style.top = ref.y + '%';
    };
    const onUp = () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
      _setPdfAnnotationDragging(false);
      _setHandlesVisible(true);
      _schedulePdfPaneSave(targetId);
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
  });

  resize.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const targetId = _annotationDocId();
    _pushPdfUndoSnapshot(targetId);
    try { resize.setPointerCapture(ev.pointerId); } catch (_) {}
    _setPdfAnnotationDragging(true);
    del.style.display = 'none';
    grip.style.display = 'none';
    if (menuBtn) menuBtn.style.display = 'none';
    const start = { mx: ev.clientX, my: ev.clientY, w: ref.w, h: ref.h };
    const rect = pageWrap ? pageWrap.getBoundingClientRect() : { width: 1, height: 1 };
    const onMove = (e) => {
      const dwPct = ((e.clientX - start.mx) / rect.width) * 100;
      const dhPct = ((e.clientY - start.my) / rect.height) * 100;
      ref.w = Math.max(1, Math.min(100 - ref.x, start.w + dwPct));
      ref.h = Math.max(0.8, Math.min(100 - ref.y, start.h + dhPct));
      wrap.style.width = ref.w + '%';
      wrap.style.height = ref.h + '%';
    };
    const onUp = () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
      _setPdfAnnotationDragging(false);
      _setHandlesVisible(true);
      _schedulePdfPaneSave(targetId);
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
  });

  if (kind === 'text') {
    const popover = document.createElement('div');
    popover.className = 'pdf-annotation-text-menu';
    popover.style.cssText = 'position:absolute;bottom:calc(100% + 24px);left:0;display:none;background:#fff;border:1px solid var(--accent, var(--red));border-radius:4px;padding:6px 8px;box-shadow:0 2px 8px rgba(0,0,0,0.2);z-index:10;flex-direction:column;align-items:stretch;gap:6px;font-size:10px;color:#222;white-space:nowrap;';
    popover.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;">
        <span style="min-width:58px;">Text size</span>
        <input type="range" class="fs-slider" min="6" max="36" step="1" value="${ann.fontSize || 11}" style="width:90px;accent-color:var(--accent, var(--red));" />
        <input type="number" class="fs-val pdf-annotation-line-value" min="6" max="72" step="1" value="${ann.fontSize || 11}" />
      </div>
      <div style="display:flex;align-items:center;gap:6px;">
        <span style="min-width:58px;">Line spacing</span>
        <input type="range" class="lh-slider" min="1" max="3" step="0.05" value="${ann.lineHeight || 1.3}" style="width:90px;accent-color:var(--accent, var(--red));" />
        <input type="number" class="lh-val pdf-annotation-line-value" min="0.5" max="5" step="0.01" value="${(ann.lineHeight || 1.3).toFixed(2)}" />
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;">
        <button type="button" class="pdf-ann-today" style="height:22px;padding:0 7px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 55%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 10%, transparent);color:var(--accent, var(--red));border-radius:4px;cursor:pointer;font-size:10px;font-family:inherit;text-align:left;display:inline-flex;align-items:center;gap:4px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="17" rx="2"></rect><line x1="8" y1="2" x2="8" y2="6"></line><line x1="16" y1="2" x2="16" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line><path d="m8 15 2 2 5-5"></path></svg><span>Today</span></button>
        <button type="button" class="pdf-ann-done" style="height:22px;padding:0 8px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 55%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 14%, transparent);color:var(--accent, var(--red));border-radius:4px;cursor:pointer;font-size:10px;font-family:inherit;display:inline-flex;align-items:center;gap:4px;"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg><span>Save</span></button>
      </div>
    `;
    const slider = popover.querySelector('.lh-slider');
    const valInput = popover.querySelector('.lh-val');
    const fsSlider = popover.querySelector('.fs-slider');
    const fsInput = popover.querySelector('.fs-val');
    const todayBtn = popover.querySelector('.pdf-ann-today');
    const doneBtn = popover.querySelector('.pdf-ann-done');
    const _applyLh = (v, fromSlider) => {
      if (!Number.isFinite(v)) return;
      const targetId = _annotationDocId();
      if (popover.dataset.lhUndoCaptured !== '1') {
        _pushPdfUndoSnapshot(targetId);
        popover.dataset.lhUndoCaptured = '1';
      }
      v = Math.max(0.5, Math.min(5, v));
      const allRefs = _pdfPaneAnnotationsByDoc.get(targetId) || [];
      for (const r of allRefs) {
        if (r.kind !== 'text') continue;
        r.lineHeight = v;
        if (r.el && r.el.style) r.el.style.lineHeight = String(v);
        if (typeof r._autoGrow === 'function') r._autoGrow();
      }
      ref.lineHeight = v;
      input.style.lineHeight = String(v);
      if (fromSlider) valInput.value = v.toFixed(2);
      else slider.value = String(Math.max(parseFloat(slider.min), Math.min(parseFloat(slider.max), v)));
      _pdfLastLineHeight.set(targetId, v);
      _schedulePdfPaneSave(targetId);
    };
    slider.addEventListener('input', () => _applyLh(parseFloat(slider.value), true));
    valInput.addEventListener('input', () => _applyLh(parseFloat(valInput.value), false));
    const _applyFs = (v, fromSlider) => {
      if (!Number.isFinite(v)) return;
      const targetId = _annotationDocId();
      if (popover.dataset.fsUndoCaptured !== '1') {
        _pushPdfUndoSnapshot(targetId);
        popover.dataset.fsUndoCaptured = '1';
      }
      v = Math.max(6, Math.min(72, v));
      ref.fontSize = v;
      _pdfLastFontSize.set(targetId, v);
      input.style.fontSize = `${(v * 1.5 / 11).toFixed(3)}cqh`;
      if (fromSlider) fsInput.value = String(Math.round(v));
      else fsSlider.value = String(Math.max(6, Math.min(36, v)));
      if (typeof ref._autoGrow === 'function') ref._autoGrow();
      _schedulePdfPaneSave(targetId);
    };
    fsSlider.addEventListener('input', () => _applyFs(parseFloat(fsSlider.value), true));
    fsInput.addEventListener('input', () => _applyFs(parseFloat(fsInput.value), false));
    fsInput.addEventListener('blur', () => {
      const v = parseFloat(fsInput.value);
      if (!Number.isFinite(v)) fsInput.value = String(Math.round(ref.fontSize || 11));
      popover.dataset.fsUndoCaptured = '0';
    });
    valInput.addEventListener('blur', () => {
      const v = parseFloat(valInput.value);
      if (!Number.isFinite(v)) valInput.value = (ref.lineHeight || 1.3).toFixed(2);
      popover.dataset.lhUndoCaptured = '0';
    });
    todayBtn.addEventListener('click', () => {
      const targetId = _annotationDocId();
      _pushPdfUndoSnapshot(targetId);
      const d = new Date();
      const dd = String(d.getDate()).padStart(2, '0');
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const yyyy = d.getFullYear();
      const text = `${dd}/${mm}/${yyyy}`;
      const start = input.selectionStart ?? input.value.length;
      const end = input.selectionEnd ?? start;
      input.value = input.value.slice(0, start) + text + input.value.slice(end);
      const next = start + text.length;
      try { input.setSelectionRange(next, next); } catch (_) {}
      ref.value = input.value;
      if (typeof ref._autoGrow === 'function') ref._autoGrow();
      _schedulePdfPaneSave(targetId);
      input.focus({ preventScroll: true });
    });
    doneBtn.addEventListener('click', () => {
      popover.style.display = 'none';
      popover.dataset.lhUndoCaptured = '0';
      popover.dataset.fsUndoCaptured = '0';
      input.focus({ preventScroll: true });
    });
    popover.addEventListener('mousedown', (e) => e.stopPropagation());
    popover.addEventListener('click', (e) => e.stopPropagation());
    menuBtn?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const opening = popover.style.display !== 'flex';
      document.querySelectorAll('.pdf-annotation-text-menu').forEach(menu => {
        if (menu !== popover) menu.style.display = 'none';
      });
      const targetId = _annotationDocId();
      if (opening) {
        _pdfLastLineHeight.set(targetId, ref.lineHeight || 1.3);
        _pdfLastFontSize.set(targetId, ref.fontSize || 11);
      }
      popover.style.display = opening ? 'flex' : 'none';
      if (!opening) popover.dataset.lhUndoCaptured = '0';
    });
    _wirePdfAnnotationMenuDismiss();
    wrap.appendChild(popover);
    ref.lineHeight = ann.lineHeight || 1.3;
    ref.fontSize = ann.fontSize || 11;
  }

  wrap.appendChild(input);
  wrap.appendChild(del);
  wrap.appendChild(grip);
  wrap.appendChild(resize);
  if (menuBtn) wrap.appendChild(menuBtn);
  if (pageWrap) pageWrap.appendChild(wrap);
  return { wrap, ref, ...ref };
}

function _removeAnnotation(refOrId, explicitDocId = null) {
  if (!refOrId) return;
  const docId = explicitDocId || (typeof _getActiveDocId === "function" ? _getActiveDocId() : (typeof activeDocId !== "undefined" ? activeDocId : null));
  const refs = _pdfPaneAnnotationsByDoc.get(docId) || [];
  let targetRef = null;
  let idx = -1;
  if (typeof refOrId === 'string') {
    idx = refs.findIndex(r => r.id === refOrId);
    if (idx >= 0) targetRef = refs[idx];
  } else {
    idx = refs.indexOf(refOrId);
    targetRef = refOrId;
  }
  if (idx >= 0) refs.splice(idx, 1);
  if (targetRef && targetRef.wrap) {
    targetRef.wrap.remove();
  } else if (typeof refOrId === 'string' && typeof document !== 'undefined') {
    const wrap = document.querySelector(`.pdf-annotation-wrap[data-ann-id="${refOrId}"]`);
    wrap?.remove();
  }
  _schedulePdfPaneSave(docId);
}
// Prompt user for an instruction and ask the backend's VL pipeline to propose annotations

async function _aiFillAnnotations(docId = _getActiveDocId()) {
  if (!docId) return;
  const doc = _getDoc(docId);
  if (!doc) return;

  const instruction = typeof window !== 'undefined' && window.prompt
    ? window.prompt('What should the AI fill in?\n(e.g. "My name is Jane Doe, address 123 Main St, dob 1990-01-15")')
    : null;
  if (!instruction || !instruction.trim()) return;

  _setPdfSaveStatus('saving', undefined, docId);
  const btn = typeof document !== 'undefined' ? document.getElementById('doc-pdf-ai-fill-btn') : null;
  if (btn) { btn.disabled = true; btn.textContent = 'Thinking…'; }
  try {
    const apiBase = _getApiBase();
    const res = await fetch(`${apiBase}/api/document/${docId}/ai-fill-annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction: instruction.trim() }),
    });
    if (!res.ok) {
      const t = await res.text().catch(() => res.statusText);
      throw new Error(t || res.statusText);
    }
    const data = await res.json();
    const proposed = (data && data.annotations) || [];
    if (!proposed.length) {
      _setPdfSaveStatus('idle', undefined, docId);
      const ui = _getUiModule();
      if (ui?.showToast) ui.showToast('AI found nothing to fill');
      return;
    }
    const existing = _parseAnnotations(doc.content || '');
    const combined = existing.slice();
    for (const a of proposed) {
      combined.push({
        id: _newAnnotationId(),
        page: parseInt(a.page, 10) || 1,
        x: Math.max(0, Math.min(100, parseFloat(a.x) || 0)),
        y: Math.max(0, Math.min(100, parseFloat(a.y) || 0)),
        w: Math.max(0.5, Math.min(100, parseFloat(a.w) || 22)),
        h: Math.max(0.3, Math.min(100, parseFloat(a.h) || 3.5)),
        value: String(a.value || ''),
      });
    }
    const newMd = _writeAnnotations(doc.content || '', combined);
    _cancelPdfPaneSave(docId);
    const ok = await _persistPdfDocument(docId, newMd);
    if (!ok) {
      throw new Error('Save failed');
    }
    _setPdfSaveStatus('saved', undefined, docId);
    const ui = _getUiModule();
    if (ui?.showToast) ui.showToast(`AI added ${proposed.length} annotations`);
    if (docId === _getActiveDocId()) {
      _renderPdfPane(docId);
    }
  } catch (e) {
    console.error('AI fill failed:', e);
    _setPdfSaveStatus('error', `AI fill failed: ${e.message || e}`, docId);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'AI fill'; }
  }
}

function _cancelPdfPaneSave(docId = _getActiveDocId()) {
  if (!docId) return;
  const timer = _pdfPaneSaveTimers.get(docId);
  if (timer) {
    clearTimeout(timer);
    _pdfPaneSaveTimers.delete(docId);
    _syncPdfPaneTimerExport();
  }
}

function _hasPendingPdfPaneSave(docId = _getActiveDocId()) {
  return Boolean(docId && _pdfPaneSaveTimers.has(docId));
}

function _cleanupPdfDocState(docId) {
  if (!docId) return;
  _cancelPdfPaneSave(docId);
  _pdfPaneFieldsByDoc.delete(docId);
  _pdfPaneAnnotationsByDoc.delete(docId);
  _pdfUndoStackByDoc.delete(docId);
  _pdfLastLineHeight.delete(docId);
  _pdfLastFontSize.delete(docId);
  _pdfViewState.delete(docId);
}

function _schedulePdfPaneSave(docId = _getActiveDocId()) {
  if (!docId) return;
  const doc = _getDoc(docId);
  if (!doc) return;
  if (typeof _deps.markDocumentDirty === 'function') {
    _deps.markDocumentDirty(docId);
  }
  _setPdfSaveStatus('dirty', undefined, docId);
  _cancelPdfPaneSave(docId);
  const timer = setTimeout(() => {
    _pdfPaneSaveTimers.delete(docId);
    _syncPdfPaneTimerExport();
    _savePdfPaneToMarkdown(docId);
  }, 600);
  _pdfPaneSaveTimers.set(docId, timer);
  _syncPdfPaneTimerExport();
}

function _setPdfSaveStatus(status, msg, docId = _getActiveDocId()) {
  if (typeof document === 'undefined') return;
  if (docId && docId !== _getActiveDocId()) return;
  const pill = document.getElementById('doc-pdf-save-pill');
  if (!pill) return;
  const palette = {
    idle:   { txt: '',           bg: 'transparent',           fg: 'transparent' },
    dirty:  { txt: 'Editing…',   bg: 'var(--panel)',          fg: 'var(--fg)' },
    saving: { txt: 'Saving…',    bg: 'var(--panel)',          fg: 'var(--fg)' },
    saved:  { txt: 'Saved',      bg: 'rgba(34,197,94,0.85)',  fg: '#fff' },
    error:  { txt: msg || 'Save failed', bg: 'var(--red)',    fg: 'var(--bg)' },
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
      if (pill.textContent === 'Saved') _setPdfSaveStatus('idle', undefined, expectedDocId);
    }, 1200);
  }
}

function _composePdfPaneMarkdown(docId) {
  const fields = _pdfPaneFieldsByDoc.get(docId) || [];
  const annotations = _pdfPaneAnnotationsByDoc.get(docId) || [];
  if (!docId) return null;
  const doc = _getDoc(docId);
  if (!doc) return null;
  const hadAnnotations = /<!--\s*annotation\s+/.test(doc.content || '');
  if (!fields.length && !annotations.length && !hadAnnotations) return null;

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
  return { doc, md, changed: md !== doc.content };
}

function _syncPdfPaneToDoc(docId = _getActiveDocId(), { markDirty = true } = {}) {
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

async function _flushPdfPaneSave(docId = _getActiveDocId(), opts = {}) {
  if (!docId) return false;
  const hadTimer = _hasPendingPdfPaneSave(docId);
  _cancelPdfPaneSave(docId);
  if (!hadTimer) {
    const composed = _composePdfPaneMarkdown(docId);
    if (!composed || !composed.changed) return false;
  }
  return await _savePdfPaneToMarkdown(docId, opts);
}

async function _savePdfPaneToMarkdown(targetDocId = _getActiveDocId(), opts = {}) {
  if (targetDocId && typeof targetDocId === 'object') {
    opts = targetDocId;
    targetDocId = opts.docId || _getActiveDocId();
  }
  const docId = targetDocId || opts.docId || _getActiveDocId();
  if (!docId) return false;
  _cancelPdfPaneSave(docId);
  const composed = _composePdfPaneMarkdown(docId);
  if (!composed) return false;
  if (!composed.changed) {
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

async function _refreshPdfPreviewIframe() {
  if (typeof document === 'undefined') return;
  const pane = document.getElementById('doc-pdf-view');
  const activeId = _getActiveDocId();
  if (!pane || !activeId) return;
  if (pane.style.display === 'none') return;
  await _flushPdfPaneSave(activeId);
  _renderPdfPane(activeId);
}

async function _setPdfViewActive(active) {
  if (typeof document === 'undefined') return;
  const pane = document.getElementById('doc-pdf-view');
  const wrap = document.getElementById('doc-editor-wrap');
  const btn = document.getElementById('doc-pdf-view-btn');
  if (!pane || !wrap) return;
  const activeId = _getActiveDocId();
  if (active) {
    _pdfViewState.set(activeId, true);
    wrap.style.display = 'none';
    pane.style.display = '';
    _renderPdfPane(activeId);
    btn?.classList.add('active');
  } else {
    await _flushPdfPaneSave(activeId);
    _pdfViewState.set(activeId, false);
    delete pane.dataset.renderedDocId;
    pane.style.display = 'none';
    const savedPill = document.getElementById('doc-pdf-save-pill');
    pane.innerHTML = '';
    if (savedPill) pane.appendChild(savedPill);
    _pdfPaneFieldsByDoc.delete(activeId);
    _pdfPaneAnnotationsByDoc.delete(activeId);
    wrap.style.display = '';
    btn?.classList.remove('active');
  }
}

function initPdfTools(options = {}) {
  _deps = { ..._deps, ...options };
  if (typeof document === 'undefined') return;

  document.getElementById('doc-export-pdf-btn')?.addEventListener('click', _downloadFilledPdf);
  document.getElementById('doc-pdf-view-btn')?.addEventListener('click', () => {
    const pane = document.getElementById('doc-pdf-view');
    const visible = pane && pane.style.display !== 'none';
    _setPdfViewActive(!visible);
  });
  document.getElementById('doc-pdf-add-text-btn')?.addEventListener('click', () => _setPdfDropMode(_pdfDropMode === 'text' ? null : 'text'));
  document.getElementById('doc-pdf-add-check-btn')?.addEventListener('click', () => _setPdfDropMode(_pdfDropMode === 'check' ? null : 'check'));
  document.getElementById('doc-pdf-add-sign-btn')?.addEventListener('click', () => _setPdfDropMode(_pdfDropMode === 'signature' ? null : 'signature'));
  document.getElementById('doc-pdf-refresh-btn')?.addEventListener('click', () => _renderPdfPane());
  document.getElementById('doc-pdf-ai-fill-btn')?.addEventListener('click', () => _aiFillAnnotations());
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => {
    for (const docId of Array.from(_pdfPaneSaveTimers.keys())) {
      _cancelPdfPaneSave(docId);
      _savePdfPaneToMarkdown(docId, { keepalive: true });
    }
  });
}

export {
  _pdfViewState,
  _pdfPaneFieldsByDoc,
  _pdfPaneAnnotationsByDoc,
  _pdfUndoStackByDoc,
  _pdfPaneSaveTimers,
  _pdfPaneSaveTimer,
  _pdfDropMode,
  _pdfAnnotationDragging,
  _pdfLastLineHeight,
  _pdfLastFontSize,
  _pdfAnnotationMenuDismissWired,
  _pdfPaneProximityWired,
  _sigCache,
  _isFormBackedDoc,
  _downloadFilledPdf,
  _saveActiveDocBeforeExport,
  _annotationRegexGlobal,
  _escapeAnnotationValue,
  _unescapeAnnotationValue,
  _parseAnnotations,
  _annotationLine,
  _writeAnnotations,
  _newAnnotationId,
  _encodeFieldName,
  _pdfMarkdownFromLive,
  _pushPdfUndoSnapshot,
  _undoPdfPaneAction,
  _setPdfAnnotationDragging,
  _wirePdfAnnotationMenuDismiss,
  _setPdfDropMode,
  _wirePdfPaneProximity,
  _pdfResponseErrorMessage,
  _renderPdfPane,
  _buildAnnotation,
  _removeAnnotation,
  _aiFillAnnotations,
  _schedulePdfPaneSave,
  _cancelPdfPaneSave,
  _hasPendingPdfPaneSave,
  _flushPdfPaneSave,
  _syncPdfPaneToDoc,
  _cleanupPdfDocState,
  _setPdfSaveStatus,
  _savePdfPaneToMarkdown,
  _refreshPdfPreviewIframe,
  _setPdfViewActive,
  clamp,
  pxToPct,
  pctToPx,
  calculateFontSizeCqh,
  calculateFieldRectPct,
  calculateAnnotationPlacement,
  calculateDragMove,
  calculateResize,
  initPdfTools,
};
