// document-reference-compat: API_BASE - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: activeDocId - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: docs - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: signatureModule - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// static/js/document/pdfTools.js
/**
 * PDF Viewer UI, Form Field & Annotation Overlay Controller, and Lifecycle Facade.
 * Delegates pure geometry to ./pdf/geometry.js, markdown annotation serialization to
 * ./pdf/annotations.js, and per-doc state/autosave to ./pdf/persistence.js.
 */

import {
  clamp,
  pxToPct,
  pctToPx,
  calculateFontSizeCqh,
  calculateFieldRectPct,
  calculateAnnotationPlacement,
  calculateDragMove,
  calculateResize,
} from './pdf/geometry.js';
import {
  _isFormBackedDoc,
  _annotationRegexGlobal,
  _escapeAnnotationValue,
  _unescapeAnnotationValue,
  _parseAnnotations,
  _annotationLine,
  _writeAnnotations,
  _newAnnotationId,
  _encodeFieldName,
} from './pdf/annotations.js';
import {
  _pdfViewState,
  _pdfPaneFieldsByDoc,
  _pdfPaneAnnotationsByDoc,
  _pdfUndoStackByDoc,
  _pdfPaneSaveTimers,
  _pdfPaneSaveTimer,
  _pdfRenderGenerationByDoc,
  _pdfViewTransitionByDoc,
  _pdfLastLineHeight,
  _pdfLastFontSize,
  _sigCache,
  _pdfMountedDocId,
  setPdfMountedDocId,
  configurePdfRuntimeDeps,
  getPdfRuntimeDeps,
  _getApiBase,
  _getActiveDocId,
  _getDoc,
  _persistPdfDocument,
  _pdfMarkdownFromLive,
  _pushPdfUndoSnapshot,
  _cancelPdfPaneSave,
  _hasPendingPdfPaneSave,
  _releasePdfPane,
  _cleanupPdfDocState as _cleanupPersistedPdfDocState,
  _setPdfSaveStatus,
  _syncPdfPaneToDoc,
  _schedulePdfPaneSave,
  _flushPdfPaneSave,
  _savePdfPaneToMarkdown,
  _saveActiveDocBeforeExport,
} from './pdf/persistence.js';

let _pdfToolsUnbind = null;
let _pdfToolsDispose = null;
let _pdfAnnotationMenuDismissWired = false;
let _pdfPaneProximityWired = false;
let _pdfDropMode = null;
let _pdfAnnotationDragging = false;
let _pdfMenuDismissController = null;
let _pdfProximityController = null;
let _pdfProximityPane = null;
let _pdfProximityRaf = 0;
let _pdfMeasureMirror = null;
let _pdfActivePointerCleanup = null;

function _dismissDocKb() {
  const deps = getPdfRuntimeDeps();
  if (deps.dismissDocKb) {
    deps.dismissDocKb();
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
  const deps = getPdfRuntimeDeps();
  if (deps.getUiModule) {
    const m = deps.getUiModule();
    if (m) return m;
  }
  return typeof window !== 'undefined' ? window.uiModule : null;
}

function _getSpinnerModule() {
  const deps = getPdfRuntimeDeps();
  if (deps.getSpinnerModule) {
    const m = deps.getSpinnerModule();
    if (m) return m;
  }
  return typeof window !== 'undefined' ? window.spinnerModule : null;
}

function _getSignatureModule() {
  const deps = getPdfRuntimeDeps();
  if (deps.getSignatureModule) {
    const m = deps.getSignatureModule();
    if (m) return m;
  }
  return typeof window !== 'undefined' ? window.signatureModule : null;
}

const _escHtml = (s) => String(s || '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

function _getPdfMeasureMirror() {
  if (typeof document === 'undefined') return null;
  if (_pdfMeasureMirror && _pdfMeasureMirror.isConnected) return _pdfMeasureMirror;
  _pdfMeasureMirror?.remove?.();
  _pdfMeasureMirror = document.createElement('div');
  _pdfMeasureMirror.className = 'pdf-annotation-measure';
  _pdfMeasureMirror.setAttribute('aria-hidden', 'true');
  _pdfMeasureMirror.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-family:inherit;padding:1px 4px;left:-9999px;top:-9999px;pointer-events:none;';
  document.body.appendChild(_pdfMeasureMirror);
  return _pdfMeasureMirror;
}

function _clearPdfMeasureMirror() {
  if (_pdfMeasureMirror) {
    _pdfMeasureMirror.remove();
    _pdfMeasureMirror = null;
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

async function _undoPdfPaneAction(docId = _getActiveDocId()) {
  if (!docId) return false;
  const stack = _pdfUndoStackByDoc.get(docId) || [];
  const prev = stack.pop();
  if (!prev) return false;
  _pdfUndoStackByDoc.set(docId, stack);
  _cancelPdfPaneSave(docId);
  const doc = _getDoc(docId);
  if (!doc) return false;
  const draftBeforeUndo = doc.content;
  _setPdfSaveStatus('saving', undefined, docId);
  try {
    const write = _persistPdfDocument(docId, prev);
    const undoRevision = doc._editRevision;
    const ok = await write;
    if (!ok) {
      if (doc.content === prev) doc.content = draftBeforeUndo;
      if (docId === _getActiveDocId() && typeof document !== 'undefined') {
        const ta = document.getElementById('doc-editor-textarea');
        if (ta && ta.value === prev) ta.value = draftBeforeUndo;
      }
      _setPdfSaveStatus('error', 'Undo failed', docId);
      stack.push(prev);
      return true;
    }
    const ownsDraft = doc._editRevision === undoRevision && doc.content === prev;
    _setPdfSaveStatus(ownsDraft ? 'saved' : 'dirty', undefined, docId);
    if (ownsDraft && docId === _getActiveDocId()) {
      _renderPdfPane(docId);
    }
    return true;
  } catch (e) {
    if (doc.content === prev) doc.content = draftBeforeUndo;
    if (docId === _getActiveDocId() && typeof document !== 'undefined') {
      const ta = document.getElementById('doc-editor-textarea');
      if (ta && ta.value === prev) ta.value = draftBeforeUndo;
    }
    stack.push(prev);
    _setPdfSaveStatus('error', e.message || 'Undo failed', docId);
    return true;
  }
}

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

function _wirePdfAnnotationMenuDismiss() {
  if ((_pdfAnnotationMenuDismissWired && _pdfMenuDismissController) || typeof document === 'undefined') return;
  _pdfMenuDismissController?.abort();
  const controller = new AbortController();
  _pdfMenuDismissController = controller;
  _pdfAnnotationMenuDismissWired = true;
  document.addEventListener('pointerdown', (ev) => {
    if (ev.target.closest('.pdf-annotation-text-menu, .pdf-annotation-menu-btn')) return;
    document.querySelectorAll('.pdf-annotation-text-menu').forEach(menu => {
      menu.style.display = 'none';
      menu.dataset.lhUndoCaptured = '0';
    });
  }, { signal: controller.signal });
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    document.querySelectorAll('.pdf-annotation-text-menu').forEach(menu => {
      menu.style.display = 'none';
      menu.dataset.lhUndoCaptured = '0';
    });
  }, { signal: controller.signal });
}

function _unwirePdfAnnotationMenuDismiss() {
  if (_pdfMenuDismissController) {
    _pdfMenuDismissController.abort();
    _pdfMenuDismissController = null;
  }
  _pdfAnnotationMenuDismissWired = false;
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

function _unwirePdfPaneProximity() {
  if (_pdfProximityRaf && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(_pdfProximityRaf);
  }
  _pdfProximityRaf = 0;
  if (_pdfProximityController) {
    _pdfProximityController.abort();
    _pdfProximityController = null;
  }
  _pdfProximityPane = null;
  _pdfPaneProximityWired = false;
}

function _wirePdfPaneProximity(pane) {
  if (!pane) return;
  if (_pdfPaneProximityWired && _pdfProximityPane === pane && _pdfProximityController) return;
  _unwirePdfPaneProximity();
  const controller = new AbortController();
  _pdfProximityController = controller;
  _pdfProximityPane = pane;
  _pdfPaneProximityWired = true;
  const buffer = 44;
  pane.addEventListener('mousemove', (ev) => {
    if (_pdfProximityRaf) return;
    _pdfProximityRaf = requestAnimationFrame(() => {
      _pdfProximityRaf = 0;
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
  }, { signal: controller.signal });
  pane.addEventListener('mouseleave', () => {
    const currentDocId = _getActiveDocId();
    const refs = _pdfPaneAnnotationsByDoc.get(currentDocId) || [];
    for (const ref of refs) ref._setHandlesVisible && ref._setHandlesVisible(false);
  }, { signal: controller.signal });
}

async function _pdfResponseErrorMessage(res) {
  const status = res?.status ? `HTTP ${res.status}` : 'PDF error';
  const raw = await res?.text?.().catch(() => '');
  if (!raw) return status;
  try {
    const parsed = JSON.parse(raw);
    return parsed?.detail || parsed?.message || status;
  } catch (_) {
    return raw.trim() || status;
  }
}

async function _renderPdfPane(docId = _getActiveDocId(), options = {}) {
  if (typeof document === 'undefined') return;
  const pane = document.getElementById('doc-pdf-view');
  if (!pane || !docId) return;
  if (_pdfMountedDocId && _pdfMountedDocId !== docId) {
    _pdfPaneFieldsByDoc.delete(_pdfMountedDocId);
    _pdfPaneAnnotationsByDoc.delete(_pdfMountedDocId);
    _pdfRenderGenerationByDoc.set(_pdfMountedDocId, (_pdfRenderGenerationByDoc.get(_pdfMountedDocId) || 0) + 1);
  }
  setPdfMountedDocId(docId);
  const generation = (_pdfRenderGenerationByDoc.get(docId) || 0) + 1;
  _pdfRenderGenerationByDoc.set(docId, generation);
  const renderPane = pane;
  const renderDoc = _getDoc(docId);
  const renderContent = renderDoc?.content;
  const renderRevision = renderDoc?._editRevision;
  const isCurrentView = () => _pdfRenderGenerationByDoc.get(docId) === generation
    && docId === _getActiveDocId() && _pdfViewState.get(docId) !== false
    && renderPane.isConnected && document.getElementById('doc-pdf-view') === renderPane
    && _getDoc(docId) === renderDoc && _getDoc(docId)?.content === renderContent
    && _getDoc(docId)?._editRevision === renderRevision;
  delete pane.dataset.renderedDocId;
  _wirePdfPaneProximity(pane);
  _pdfPaneFieldsByDoc.delete(docId);
  _pdfPaneAnnotationsByDoc.delete(docId);
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
    if (!Array.isArray(data?.pages) || data.pages.length === 0) {
      throw new Error('PDF renderer returned no pages');
    }
    const renderedPages = new Set(data.pages.map(page => Number(page.page)));
    if (_parseAnnotations(renderContent || '').some(annotation => !renderedPages.has(Number(annotation.page)))) {
      throw new Error('PDF renderer returned an incomplete annotation page set');
    }
  } catch (e) {
    if (!isCurrentView()) return;
    pane.innerHTML = `<div style="color:#fbb;padding:40px;text-align:center;">Failed to load PDF view: ${_escHtml(e.message || String(e))}</div>`;
    if (savedPill) pane.appendChild(savedPill);
    return;
  }
  if (!isCurrentView()) return;

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
      const rect = calculateFieldRectPct(f.rect_px || [0, 0, 0, 0], page.width, page.height);
      const { left: lPct, top: tPct, width: wPct, height: hPct } = rect;
      const [x0, y0, x1, y1] = f.rect_px || [0, 0, 0, 0];
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
                const sigApiBase = typeof _getApiBase === 'function' ? _getApiBase() : (typeof API_BASE !== 'undefined' ? API_BASE : '');
                const r = await fetch(`${sigApiBase}/api/signatures`);
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
          const sigModule = typeof _getSignatureModule === 'function' ? _getSignatureModule() : (typeof signatureModule !== 'undefined' ? signatureModule : null);
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
      const ann = {
        ...calculateAnnotationPlacement({
          clientX: ev.clientX,
          clientY: ev.clientY,
          pageRect: pageWrap.getBoundingClientRect(),
          mode: _pdfDropMode,
          pageNumber: page.page,
          lastLineHeight: _pdfLastLineHeight.get(docId) || 1.3,
          lastFontSize: _pdfLastFontSize.get(docId) || 11,
        }),
        docId,
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
    input.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;user-select:none;pointer-events:none;';
    input.innerHTML = '<svg viewBox="0 0 24 24" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%;display:block;"><path d="M4 12 L10 18 L20 6" fill="none" stroke="#111" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  } else if (kind === 'signature') {
    input = document.createElement('div');
    input.style.cssText = 'width:100%;height:100%;box-sizing:border-box;border:1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 10%, transparent);display:flex;align-items:center;justify-content:center;cursor:pointer;overflow:hidden;font-size:10px;color:var(--accent, var(--red));';
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
    input.style.cssText = `width:100%;height:100%;box-sizing:border-box;border:1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 10%, transparent);font-family:inherit;font-size:${calculateFontSizeCqh(fs).toFixed(3)}cqh;line-height:${lh};padding:1px 4px;color:#111;resize:none;overflow:auto;white-space:pre-wrap;`;
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
          const apiBase = typeof _getApiBase === 'function' ? _getApiBase() : (typeof API_BASE !== 'undefined' ? API_BASE : '');
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
      const sigModule = typeof _getSignatureModule === 'function' ? _getSignatureModule() : (typeof signatureModule !== 'undefined' ? signatureModule : null);
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
    const _autoGrow = () => {
      if (!pageWrap || !wrap.isConnected) return;
      const pageRect = pageWrap.getBoundingClientRect();
      if (!pageRect.height || !pageRect.width) return;

      const mirror = _getPdfMeasureMirror();
      if (!mirror) return;
      const cs = window.getComputedStyle(input);
      mirror.style.fontSize = cs.fontSize;
      mirror.style.fontWeight = cs.fontWeight;
      mirror.style.fontFamily = cs.fontFamily;
      mirror.style.letterSpacing = cs.letterSpacing;
      let widestPx = 0;
      const lines = (input.value || input.placeholder || '').split('\n');
      for (const line of lines) {
        mirror.textContent = line || ' ';
        if (mirror.offsetWidth > widestPx) widestPx = mirror.offsetWidth;
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

  const _bindPointerGesture = (handle, hiddenHandles, startState, onDragStep) => {
    handle.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const targetId = _annotationDocId();
      _pushPdfUndoSnapshot(targetId);
      try { handle.setPointerCapture(ev.pointerId); } catch (_) {}
      _setPdfAnnotationDragging(true);
      for (const h of hiddenHandles) if (h) h.style.display = 'none';
      const rect = pageWrap ? pageWrap.getBoundingClientRect() : { width: 1, height: 1 };
      const start = { mx: ev.clientX, my: ev.clientY, ...startState() };
      const onMove = (e) => onDragStep(start, { mx: e.clientX, my: e.clientY }, rect);
      const cleanupGesture = () => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        document.removeEventListener('pointercancel', onUp);
        if (_pdfActivePointerCleanup === cleanupGesture) _pdfActivePointerCleanup = null;
      };
      const onUp = () => {
        cleanupGesture();
        _setPdfAnnotationDragging(false);
        _setHandlesVisible(true);
        _schedulePdfPaneSave(targetId);
      };
      _pdfActivePointerCleanup?.();
      _pdfActivePointerCleanup = cleanupGesture;
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
      document.addEventListener('pointercancel', onUp);
    });
  };

  _bindPointerGesture(grip, [del, resize, menuBtn], () => ({ x: ref.x, y: ref.y }), (start, current, rect) => {
    const moved = calculateDragMove(start, current, rect, ref.w, ref.h);
    ref.x = moved.x;
    ref.y = moved.y;
    wrap.style.left = ref.x + '%';
    wrap.style.top = ref.y + '%';
  });

  _bindPointerGesture(resize, [del, grip, menuBtn], () => ({ w: ref.w, h: ref.h }), (start, current, rect) => {
    const resized = calculateResize(start, current, rect, ref.x, ref.y);
    ref.w = resized.w;
    ref.h = resized.h;
    wrap.style.width = ref.w + '%';
    wrap.style.height = ref.h + '%';
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
      input.style.fontSize = `${calculateFontSizeCqh(v).toFixed(3)}cqh`;
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
  const docId = explicitDocId || (typeof _getActiveDocId === 'function' ? _getActiveDocId() : (typeof activeDocId !== 'undefined' ? activeDocId : null));
  const refs = _pdfPaneAnnotationsByDoc.get(docId) || [];
  let targetRef = null;
  let idx = -1;
  if (typeof refOrId === 'string') {
    idx = refs.findIndex(r => r && r.id === refOrId);
    targetRef = idx >= 0 ? refs[idx] : null;
  } else {
    targetRef = refOrId;
    idx = refs.indexOf(refOrId);
    if (idx < 0 && refOrId.id) idx = refs.findIndex(r => r && r.id === refOrId.id);
  }
  if (idx >= 0) refs.splice(idx, 1);
  if (targetRef && targetRef.wrap && targetRef.wrap.remove) {
    targetRef.wrap.remove();
  } else if (typeof refOrId === 'string' && typeof document !== 'undefined') {
    const wrap = document.querySelector(`.pdf-annotation-wrap[data-ann-id="${refOrId}"]`);
    wrap?.remove();
  }
  if (!refs.length) _clearPdfMeasureMirror();
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

function _cleanupPdfDocState(docId) {
  if (!docId) return;
  const wasMounted = _pdfMountedDocId === docId;
  _cleanupPersistedPdfDocState(docId);
  if (wasMounted) {
    _clearPdfMeasureMirror();
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

function _hidePdfViewPane(pane = typeof document !== 'undefined' ? document.getElementById('doc-pdf-view') : null) {
  _setPdfDropMode(null);
  _unwirePdfPaneProximity();
  _unwirePdfAnnotationMenuDismiss();
  _clearPdfMeasureMirror();
  _pdfActivePointerCleanup?.();
  if (!pane) return;
  delete pane.dataset.renderedDocId;
  pane.style.display = 'none';
  const savedPill = document.getElementById('doc-pdf-save-pill');
  pane.innerHTML = '';
  if (savedPill) pane.appendChild(savedPill);
}

async function _setPdfViewActive(active) {
  if (typeof document === 'undefined') return;
  const pane = document.getElementById('doc-pdf-view');
  const wrap = document.getElementById('doc-editor-wrap');
  const btn = document.getElementById('doc-pdf-view-btn');
  if (!pane || !wrap) return;
  const activeId = _getActiveDocId();
  const transition = (_pdfViewTransitionByDoc.get(activeId) || 0) + 1;
  _pdfViewTransitionByDoc.set(activeId, transition);
  if (active) {
    _pdfViewState.set(activeId, true);
    wrap.style.display = 'none';
    pane.style.display = '';
    _renderPdfPane(activeId);
    btn?.classList.add('active');
  } else {
    await _flushPdfPaneSave(activeId);
    if (_pdfViewTransitionByDoc.get(activeId) !== transition || _getActiveDocId() !== activeId) return;
    _pdfViewState.set(activeId, false);
    _hidePdfViewPane(pane);
    _pdfPaneFieldsByDoc.delete(activeId);
    _pdfPaneAnnotationsByDoc.delete(activeId);
    _pdfRenderGenerationByDoc.set(activeId, (_pdfRenderGenerationByDoc.get(activeId) || 0) + 1);
    wrap.style.display = '';
    btn?.classList.remove('active');
  }
}

function initPdfTools(options = {}) {
  configurePdfRuntimeDeps(options);
  if (typeof document === 'undefined') return () => {};
  _pdfToolsUnbind?.();
  const bindings = [];
  const bind = (id, handler) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('click', handler);
    bindings.push(() => el.removeEventListener('click', handler));
  };
  bind('doc-export-pdf-btn', _downloadFilledPdf);
  bind('doc-pdf-view-btn', () => {
    const pane = document.getElementById('doc-pdf-view');
    const visible = pane && pane.style.display !== 'none';
    _setPdfViewActive(!visible);
  });
  bind('doc-pdf-add-text-btn', () => _setPdfDropMode(_pdfDropMode === 'text' ? null : 'text'));
  bind('doc-pdf-add-check-btn', () => _setPdfDropMode(_pdfDropMode === 'check' ? null : 'check'));
  bind('doc-pdf-add-sign-btn', () => _setPdfDropMode(_pdfDropMode === 'signature' ? null : 'signature'));
  bind('doc-pdf-refresh-btn', async () => {
    const id = _getActiveDocId();
    await _flushPdfPaneSave(id);
    if (id === _getActiveDocId()) _renderPdfPane(id);
  });
  bind('doc-pdf-ai-fill-btn', () => _aiFillAnnotations());
  const beforeUnload = () => {
    for (const docId of Array.from(_pdfPaneSaveTimers.keys())) {
      _cancelPdfPaneSave(docId);
      _savePdfPaneToMarkdown(docId, { keepalive: true });
    }
  };
  window.addEventListener('beforeunload', beforeUnload);
  bindings.push(() => window.removeEventListener('beforeunload', beforeUnload));
  const unbind = () => {
    for (const unbindFn of bindings.splice(0)) unbindFn();
  };
  _pdfToolsUnbind = unbind;
  const dispose = () => {
    unbind();
    if (_pdfToolsUnbind !== unbind) return;
    _pdfToolsUnbind = null;
    _pdfToolsDispose = null;
    _hidePdfViewPane();
    const docId = _pdfMountedDocId;
    if (docId) {
      _releasePdfPane(docId);
    } else {
      setPdfMountedDocId(null);
    }
  };
  _pdfToolsDispose = dispose;
  return dispose;
}

function disposePdfTools() {
  _pdfToolsDispose?.();
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
  _releasePdfPane,
  _setPdfSaveStatus,
  _savePdfPaneToMarkdown,
  _refreshPdfPreviewIframe,
  _hidePdfViewPane,
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
  disposePdfTools,
};
