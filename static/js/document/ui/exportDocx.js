// static/js/document/exportDocx.js
/**
 * Document Export, Print, and DOCX generation controller.
 * Extracted from document.js for modularity, clean maintenance, and isolated testing.
 */

import { _escHtml, _isDocxLang, _isRichTextLang, MARKDOWN_DOCUMENT_RENDER_OPTIONS } from '../domain/documentKinds.js';
import { _normalizeRichLinkUrl } from '../domain/richTextRules.js';
import { _importFromDevice } from './importFromDevice.js';
import { _isFormBackedDoc } from './pdf/annotations.js';

const EXPORT_EXTENSION_BY_LANG = Object.freeze({
  javascript: '.js', python: '.py', html: '.html', svg: '.svg', xml: '.xml', css: '.css',
  richtext: '.html',
  markdown: '.md', json: '.json', yaml: '.yml', bash: '.sh',
  sql: '.sql', rust: '.rs', go: '.go', java: '.java', c: '.c', cpp: '.cpp', csharp: '.cs',
  typescript: '.ts', ruby: '.rb', php: '.php', text: '.txt',
  toml: '.toml', ini: '.ini', csv: '.csv',
});

let _deps = {};

export function initExportDocx(options = {}) {
  _deps = { ...options };
}

const _getApiBase = () => {
  return (_deps.getApiBase && _deps.getApiBase()) || '';
};

const _getActiveDocId = () => {
  return (_deps.getActiveDocId && _deps.getActiveDocId()) || null;
};

const _getActiveDoc = () => {
  if (_deps.getActiveDoc) return _deps.getActiveDoc();
  const id = _getActiveDocId();
  if (!id) return null;
  const docs = _deps.getDocs && _deps.getDocs();
  return docs?.get?.(id) || null;
};

const _getTextarea = () => {
  return (_deps.getTextarea && _deps.getTextarea()) || (typeof document !== 'undefined' ? document.getElementById('doc-editor-textarea') : null);
};

const _getLanguage = () => {
  if (_deps.getLanguage) return _deps.getLanguage();
  return (typeof document !== 'undefined' ? document.getElementById('doc-language-select')?.value : '') || '';
};

const _getUiModule = () => {
  return (_deps.getUiModule && _deps.getUiModule()) || (typeof window !== 'undefined' ? window.uiModule : null);
};

const _getSessionModule = () => {
  return (_deps.getSessionModule && _deps.getSessionModule()) || (typeof window !== 'undefined' ? window.sessionModule : null);
};

const _getLastSessionId = () => {
  return (_deps.getLastSessionId && _deps.getLastSessionId()) || '';
};

const _getMarkdownModule = () => {
  return (_deps.getMarkdownModule && _deps.getMarkdownModule()) || (typeof window !== 'undefined' ? window.markdownModule : null);
};

const _topPortalZ = () => {
  if (_deps.topPortalZ) return _deps.topPortalZ();
  return 10000;
};

const _dismissOrRemove = (menu) => {
  if (_deps.dismissOrRemove) {
    _deps.dismissOrRemove(menu);
  } else {
    menu?.remove?.();
  }
};

const _bindMenuDismiss = (menu, onClose, shouldDismiss) => {
  if (_deps.bindMenuDismiss) {
    return _deps.bindMenuDismiss(menu, onClose, shouldDismiss);
  }
  const onDocClick = (ev) => {
    if (shouldDismiss && !shouldDismiss(ev)) return;
    cleanup();
    onClose();
  };
  const onKeyDown = (ev) => {
    if (ev.key === 'Escape') {
      cleanup();
      onClose();
    }
  };
  const cleanup = () => {
    if (typeof document !== 'undefined') {
      document.removeEventListener('click', onDocClick, true);
      document.removeEventListener('keydown', onKeyDown, true);
    }
  };
  if (typeof document !== 'undefined') {
    setTimeout(() => {
      document.addEventListener('click', onDocClick, true);
      document.addEventListener('keydown', onKeyDown, true);
    }, 0);
  }
  return () => {
    cleanup();
    onClose();
  };
};

const _richTextToHtmlString = (html) => {
  if (_deps.richTextContentToHtml) return _deps.richTextContentToHtml(html);
  const raw = String(html || '');
  if (!raw.trim()) return '';
  const md = _getMarkdownModule();
  if (/<\/?[a-z][^>]*>/i.test(raw)) {
    if (typeof md?.sanitizeAllowedHtml !== 'function') return _escHtml(raw);
    const sanitized = md.sanitizeAllowedHtml(raw);
    return typeof sanitized === 'string' ? sanitized : '';
  }
  try {
    if (typeof md?.mdToHtml !== 'function') return _escHtml(raw);
    const rendered = md.mdToHtml(raw, { shortcodes: false });
    return typeof rendered === 'string' ? rendered : _escHtml(raw);
  } catch (_) {
    return _escHtml(raw);
  }
};

// ── Base Export Naming ──

export function _getExportBaseName(doc) {
  let title = '';
  let version = '';

  if (typeof doc === 'string') {
    title = doc;
  } else if (doc && typeof doc === 'object') {
    title = doc.title || '';
    if (doc.version) version = `_v${doc.version}`;
  } else {
    const current = _getActiveDoc();
    if (current) {
      title = current.title || '';
      if (current.version) version = `_v${current.version}`;
    }
  }

  let safeName = String(title || '')
    .replace(/[^a-zA-Z0-9_\-. ]/g, '_')
    .trim();

  if (!safeName || /^_+$/.test(safeName)) {
    safeName = 'document';
  }

  return safeName + version;
}

// ── HTML Export & Styles ──

export function _richTextExportCss() {
  return [
    ':root{',
    '--doc-export-bg:#ffffff;',
    '--doc-export-text:#111827;',
    '--doc-export-border:#c9ced6;',
    '--doc-export-muted:#6b7280;',
    '--doc-export-code-bg:#f3f4f6;',
    '--doc-export-pre-bg:#f5f6f8;',
    '--doc-export-th-bg:#f1f3f5;',
    '--doc-export-accent:#2563eb;',
    '}',
    'html,body{background:var(--doc-export-bg,#fff);color:var(--doc-export-text,#000)}',
    'img{max-width:100%;height:auto}',
    'img.richtext-image{display:block;max-width:100%;height:auto;margin:1em 0}',
    'figure.richtext-image{max-width:100%;margin:1em 0}',
    'figure.richtext-image img.richtext-image{margin-top:0;margin-bottom:0}',
    'figure.richtext-image .richtext-image-caption{margin-top:.45em;max-width:100%;color:var(--doc-export-muted,#6b7280);font-size:.84em;line-height:1.4;text-align:center;overflow-wrap:anywhere}',
    'figure.richtext-image:has(img.richtext-image-size-60) .richtext-image-caption{width:60%}',
    'figure.richtext-image:has(img.richtext-image-size-35) .richtext-image-caption{width:35%}',
    'figure.richtext-image:has(img.richtext-image-align-left) .richtext-image-caption{margin-left:0;margin-right:auto}',
    'figure.richtext-image:has(img.richtext-image-align-center) .richtext-image-caption{margin-left:auto;margin-right:auto}',
    'figure.richtext-image:has(img.richtext-image-align-right) .richtext-image-caption{margin-left:auto;margin-right:0}',
    'img.richtext-image-size-100{width:100%}',
    'img.richtext-image-size-60{width:60%}',
    'img.richtext-image-size-35{width:35%}',
    'img.richtext-image-align-left{margin-left:0;margin-right:auto}',
    'img.richtext-image-align-center{margin-left:auto;margin-right:auto}',
    'img.richtext-image-align-right{margin-left:auto;margin-right:0}',
    'table{width:100%;border-collapse:collapse;table-layout:fixed;margin:1em 0}',
    'th,td{border:1px solid var(--doc-export-border,#c9ced6);padding:7px 8px;vertical-align:top;overflow-wrap:anywhere}',
    'th{background:var(--doc-export-th-bg,#f1f3f5);text-align:left}',
    'blockquote{margin:1em 0;padding-left:1em;border-left:3px solid var(--doc-export-border,#c9ced6);color:#4b5563}',
    'code{padding:.12em .35em;border:1px solid #d1d5db;border-radius:3px;background:var(--doc-export-code-bg,#f3f4f6);font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:.9em;white-space:break-spaces}',
    'pre{padding:12px;overflow:auto;background:var(--doc-export-pre-bg,#f5f6f8);border-radius:4px}',
    'pre code{padding:0;border:0;background:transparent;font:inherit}',
    'hr{border:0;border-top:1px solid var(--doc-export-border,#c9ced6);margin:1.25em 0}',
    'hr.richtext-page-break{border:0;border-top:1px dashed #9ca3af;margin:1.5em 0;break-after:page;page-break-after:always}',
    '@media print{hr.richtext-page-break{border:0;margin:0}}',
    'ul.rich-checklist{list-style:none;padding-left:1.8em}',
    'ul.rich-checklist>li{position:relative;min-height:1.6em}',
    'ul.rich-checklist>li:before{content:"";position:absolute;left:-1.55em;top:.3em;width:.95em;height:.95em;box-sizing:border-box;border:1.5px solid #8b929c;border-radius:3px;background:#fff}',
    'ul.rich-checklist>li[data-checked="true"]:before{border-color:var(--doc-export-accent,#2563eb);background:var(--doc-export-accent,#2563eb)}',
    'ul.rich-checklist>li[data-checked="true"]:after{content:"";position:absolute;left:-1.27em;top:.44em;width:.28em;height:.5em;border:solid #fff;border-width:0 1.5px 1.5px 0;transform:rotate(45deg)}',
    'ul.rich-checklist>li[data-checked="true"]{color:var(--doc-export-muted,#6b7280);text-decoration:line-through}',
  ].join('');
}

export function buildHtmlExportMarkup(doc, options = {}) {
  const targetDoc = (doc && typeof doc === 'object' && !('preventDefault' in doc)) ? doc : _getActiveDoc();
  const text = (options && typeof options.content === 'string')
    ? options.content
    : (doc && typeof doc === 'string'
      ? doc
      : (targetDoc?.content ?? (_getTextarea()?.value || '')));
  const lang = (options && options.language) || (targetDoc && targetDoc.language) || _getLanguage() || '';
  const markdownMod = _getMarkdownModule();

  let body;
  if (_isRichTextLang(lang)) {
    body = _richTextToHtmlString(text);
  } else if (lang === 'markdown' && markdownMod?.mdToHtml) {
    body = markdownMod.mdToHtml(text, MARKDOWN_DOCUMENT_RENDER_OPTIONS);
  } else {
    body = '<pre style="white-space:pre-wrap;font-size:12px;font-family:monospace;">' +
      String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</pre>';
  }
  const title = (options && options.title) || (targetDoc && targetDoc.title) || 'document';
  const richStyles = _isRichTextLang(lang) ? `<style>${_richTextExportCss()}</style>` : '';
  return `<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>${_escHtml(title)}</title>${richStyles}</head><body style="max-width:800px;margin:40px auto;font-family:sans-serif;line-height:1.6;padding:0 20px;background:#fff;color:#000;">\n${body}\n</body></html>`;
}

export function exportAsHtml(doc) {
  const targetDoc = (doc && typeof doc === 'object' && !('preventDefault' in doc)) ? doc : _getActiveDoc();
  if (!targetDoc && !_getActiveDocId()) return;
  const html = buildHtmlExportMarkup(targetDoc);
  if (typeof Blob !== 'undefined' && typeof URL !== 'undefined' && URL.createObjectURL) {
    const blob = new Blob([html], { type: 'text/html' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = _getExportBaseName(targetDoc) + '.html';
    a.click();
    URL.revokeObjectURL(a.href);
  }
  const ui = _getUiModule();
  if (ui?.showToast) ui.showToast('Exported as HTML');
  return html;
}

// ── Visual Report Export & Preview ──

export async function exportAsVisualReport(doc) {
  const targetDoc = (doc && typeof doc === 'object' && !('preventDefault' in doc)) ? doc : _getActiveDoc();
  const docId = targetDoc?.id || _getActiveDocId();
  if (!docId) return;
  const ui = _getUiModule();
  try {
    if (_deps.saveActiveDocBeforeExport) await _deps.saveActiveDocBeforeExport();
    const response = await fetch(`${_getApiBase()}/api/document/${encodeURIComponent(docId)}/visual-report`, {
      credentials: 'same-origin',
    });
    if (!response.ok) {
      throw new Error((await response.text()) || response.statusText || 'Request failed');
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = _getExportBaseName(targetDoc) + '-visual-report.html';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    if (ui?.showToast) ui.showToast('Exported visual report');
  } catch (error) {
    if (ui?.showError) ui.showError('Visual report export failed: ' + (error.message || error));
  }
}

export async function previewVisualReport(doc) {
  const targetDoc = (doc && typeof doc === 'object' && !('preventDefault' in doc)) ? doc : _getActiveDoc();
  const docId = targetDoc?.id || _getActiveDocId();
  if (!docId) return;
  const ui = _getUiModule();
  const preview = window.open('', '_blank');
  if (!preview) {
    if (ui?.showError) ui.showError('Preview blocked: please allow popups for this site.');
    return;
  }
  preview.document.write('<!DOCTYPE html><title>Loading visual report...</title><body style="font-family:sans-serif;padding:2rem;">Loading visual report...</body>');
  preview.document.close();
  try {
    if (_deps.saveActiveDocBeforeExport) await _deps.saveActiveDocBeforeExport();
    const response = await fetch(`${_getApiBase()}/api/document/${encodeURIComponent(docId)}/visual-report`, {
      credentials: 'same-origin',
    });
    if (!response.ok) {
      throw new Error((await response.text()) || response.statusText || 'Request failed');
    }
    const html = await response.text();
    preview.document.open();
    preview.document.write(html);
    preview.document.close();
  } catch (error) {
    preview.document.open();
    preview.document.write(`<title>Visual report preview failed</title><body style="font-family:sans-serif;padding:2rem;color:#b00;">${String(error.message || error).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}</body>`);
    preview.document.close();
    if (ui?.showError) ui.showError('Visual report preview failed: ' + (error.message || error));
  }
}

// ── PDF Print Export ──

export async function exportAsPdf(doc) {
  const targetDoc = (doc && typeof doc === 'object' && !('preventDefault' in doc)) ? doc : _getActiveDoc();
  const docId = targetDoc?.id || _getActiveDocId();
  if (!docId) return;
  const textarea = _getTextarea();
  if (!textarea && !targetDoc) return;
  const ui = _getUiModule();
  if (typeof window.print !== 'function') {
    if (ui) ui.showError('Browser printing is unavailable.');
    return;
  }
  const lang = (targetDoc && targetDoc.language) || _getLanguage();
  const text = (textarea ? textarea.value : targetDoc?.content) || '';
  const markdownMod = _getMarkdownModule();
  // Render content as HTML for the browser print dialog.
  let html;
  if (_isRichTextLang(lang)) {
    html = _richTextToHtmlString(text);
  } else if (_isDocxLang(lang)) {
    const previewHtml = document.querySelector('#doc-docx-preview .doc-docx-paper')?.innerHTML;
    html = previewHtml
      ? _richTextToHtmlString(previewHtml)
      : '<pre style="white-space:pre-wrap">' + text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
  } else if (lang === 'markdown' && markdownMod?.mdToHtml) {
    html = markdownMod.mdToHtml(text, MARKDOWN_DOCUMENT_RENDER_OPTIONS);
  } else {
    html = '<pre style="white-space:pre-wrap;font-size:11px;font-family:monospace;color:#000;background:#fff;">' +
      text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
  }
  document.getElementById('doc-browser-print-frame')?.remove();
  const frame = document.createElement('iframe');
  frame.id = 'doc-browser-print-frame';
  frame.title = 'Document print view';
  frame.setAttribute('sandbox', 'allow-same-origin allow-modals');
  frame.style.cssText = 'position:fixed;left:-10000px;top:0;width:800px;height:600px;border:0;';
  document.body.appendChild(frame);
  try {
    const printDoc = frame.contentDocument;
    printDoc.open();
    printDoc.write('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body></body></html>');
    printDoc.close();
    printDoc.title = _getExportBaseName(targetDoc);
    const container = printDoc.createElement('div');
    container.style.cssText = 'padding:20px;font-family:sans-serif;font-size:12px;color:#000;background:#fff;line-height:1.6;';
    const parsedDoc = new DOMParser().parseFromString(html, 'text/html');
    for (const node of Array.from(parsedDoc.body?.childNodes || [])) {
      container.appendChild(printDoc.importNode(node, true));
    }
    if (_isRichTextLang(lang)) {
      const style = printDoc.createElement('style');
      style.textContent = _richTextExportCss();
      container.prepend(style);
    }
    if (markdownMod?.renderMath) {
      await markdownMod.renderMath(container);
    }
    const styles = [];
    document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
      if (!link.href.includes('/katex/')) return;
      const copy = printDoc.createElement('link');
      copy.rel = 'stylesheet';
      copy.href = link.href;
      styles.push(new Promise((resolve, reject) => {
        copy.onload = resolve;
        copy.onerror = () => reject(new Error('Math styles could not be loaded'));
      }));
      printDoc.head.appendChild(copy);
    });
    const printStyle = printDoc.createElement('style');
    printStyle.textContent = '@page { margin: 10mm; } body { margin: 0; } img { max-width: 100%; } pre { white-space: pre-wrap; overflow-wrap: anywhere; }';
    printDoc.head.appendChild(printStyle);
    printDoc.body.appendChild(printDoc.importNode(container, true));
    await Promise.all(styles);
    if (printDoc.fonts?.ready) {
      await printDoc.fonts.ready;
    }
    await Promise.all(Array.from(printDoc.images, img => img.decode?.().catch(() => {})));
    frame.contentWindow.addEventListener('afterprint', () => frame.remove(), { once: true });
    frame.contentWindow.focus();
    frame.contentWindow.print();
  } catch (error) {
    frame.remove();
    if (ui) ui.showError('Could not open browser print: ' + (error.message || error));
  }
}

// ── Original Document Conversion ──

export async function convertOriginalDocument(target) {
  const activeId = _getActiveDocId();
  if (!activeId) return;
  const ui = _getUiModule();
  const apiBase = _getApiBase();
  try {
    const response = await fetch(
      `${apiBase}/api/document/${encodeURIComponent(activeId)}/convert-original/${target}`,
      { credentials: 'same-origin' },
    );
    if (!response.ok) {
      let message = `Conversion failed (HTTP ${response.status})`;
      try {
        const data = await response.json();
        if (data?.detail) message = data.detail;
      } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match?.[1] || `${_getExportBaseName()}.${target}`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    if (ui) ui.showToast(`Converted original file to ${target.toUpperCase()}`);
  } catch (error) {
    if (ui) ui.showError(error.message || String(error));
  }
}

export async function convertOriginalToPdfForSigning() {
  const activeId = _getActiveDocId();
  if (!activeId) return;
  const ui = _getUiModule();
  const apiBase = _getApiBase();
  try {
    const response = await fetch(
      `${apiBase}/api/document/${encodeURIComponent(activeId)}/convert-original/pdf`,
      { credentials: 'same-origin' },
    );
    if (!response.ok) {
      let message = `Conversion failed (HTTP ${response.status})`;
      try {
        const data = await response.json();
        if (data?.detail) message = data.detail;
      } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const source = _getActiveDoc();
    const name = `${source?.title || 'document'}.pdf`;
    const file = new File([blob], name, { type: 'application/pdf' });
    const form = new FormData();
    form.append('file', file);
    const sessionMod = _getSessionModule();
    const sessionId = (sessionMod?.getCurrentSessionId?.()) || _getLastSessionId() || '';
    if (sessionId) form.append('session_id', sessionId);
    const imported = await fetch(`${apiBase}/api/documents/import-pdf`, {
      method: 'POST',
      body: form,
      credentials: 'same-origin',
    });
    const payload = await imported.json().catch(() => ({}));
    if (!imported.ok) throw new Error(payload.detail || 'Could not open converted PDF');
    const docId = payload.doc_id || payload.id;
    if (!docId) throw new Error('Converted PDF did not return a document');
    const fullResponse = await fetch(`${apiBase}/api/document/${encodeURIComponent(docId)}`, { credentials: 'same-origin' });
    const full = fullResponse.ok ? await fullResponse.json() : payload;
    if (_deps.addDocToTabs) _deps.addDocToTabs(full, full.session_id || sessionId);
    if (_deps.switchToDoc) _deps.switchToDoc(full.id || docId);
    if (ui) ui.showToast('PDF opened: signature and annotation tools are ready');
  } catch (error) {
    if (ui) ui.showError(error.message || String(error));
  }
}

// ── Generic Document Export ──

export function exportDocument(format, options = {}) {
  const formatStr = typeof format === 'string' ? format.toLowerCase() : null;
  const targetDoc = (options && options.doc) || _getActiveDoc();
  if (!targetDoc && !_getActiveDocId()) return;

  if (formatStr === 'docx') {
    return exportAsDocx(targetDoc);
  }
  if (formatStr === 'pdf') {
    return exportAsPdf(targetDoc);
  }
  if (formatStr === 'visual-report') {
    return exportAsVisualReport(targetDoc);
  }

  const textarea = _getTextarea();
  const content = (options && typeof options.content === 'string')
    ? options.content
    : (textarea ? textarea.value : (targetDoc?.content || ''));

  const lang = formatStr || (options && options.language) || _getLanguage() || (targetDoc && targetDoc.language) || '';

  const ext = (options && options.extension) || EXPORT_EXTENSION_BY_LANG[lang] || '.txt';
  const baseName = (options && options.filename) || _getExportBaseName(targetDoc);
  const mime = lang === 'csv' ? 'text/csv' : lang === 'json' ? 'application/json' : _isRichTextLang(lang) ? 'text/html' : 'text/plain';

  const blob = new Blob([content], { type: mime });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (baseName.endsWith(ext) ? baseName : baseName + ext);
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Export Menu ──

export function showExportMenu(anchor, anchorRect) {
  let e = null;
  let rect = null;

  if (anchor && typeof anchor.preventDefault === 'function') {
    e = anchor;
    rect = anchorRect || e.target?.closest('button')?.getBoundingClientRect();
  } else if (typeof Element !== 'undefined' && anchor instanceof Element) {
    rect = anchorRect || anchor.getBoundingClientRect();
  } else {
    rect = anchorRect;
  }

  if (e && typeof e.stopPropagation === 'function') e.stopPropagation();

  const existing = document.getElementById('doc-export-menu');
  if (existing) { _dismissOrRemove(existing); return; }

  if (!rect) {
    rect = document.getElementById('doc-language-select')?.getBoundingClientRect();
  }
  if (!rect) return;

  const lang = _getLanguage();
  const ext = EXPORT_EXTENSION_BY_LANG[lang] || '.txt';
  const codeLanguages = new Set([
    'python', 'javascript', 'typescript', 'bash', 'sh', 'shell', 'php',
    'ruby', 'sql', 'java', 'go', 'rust', 'c', 'cpp', 'c++', 'csharp', 'c#',
    'css', 'json', 'yaml', 'ini', 'toml', 'html', 'svg', 'xml',
  ]);
  const isSourceCode = codeLanguages.has(String(lang).toLowerCase());
  const isCsv = String(lang).toLowerCase() === 'csv';

  const menu = document.createElement('div');
  menu.id = 'doc-export-menu';
  menu.className = 'doc-overflow-menu open';
  menu.style.position = 'fixed';
  menu.style.top = (rect.bottom + 2) + 'px';
  menu.style.right = (window.innerWidth - rect.right) + 'px';
  menu.style.left = 'auto';
  const editorPane = document.querySelector('.doc-editor-pane');
  const paneZ = (editorPane && parseInt(getComputedStyle(editorPane).zIndex, 10)) || 0;
  menu.style.zIndex = String(Math.max(_topPortalZ(), paneZ + 1));

  const activeDoc = _getActiveDoc();
  const liveContent = _getTextarea()?.value || activeDoc?.content || '';
  const isForm = _isFormBackedDoc(liveContent);
  const options = [];

  options.push({ label: 'Import from library', fn: () => (_deps.openLibrary ? _deps.openLibrary() : undefined) });
  options.push({ label: 'Import from device', fn: () => _importFromDevice(), _divider: true });
  if (isForm && _deps.downloadFilledPdf) options.push({ label: 'Filled PDF (.pdf)', fn: _deps.downloadFilledPdf });
  if (isSourceCode) {
    options.push({ label: `Export source (${ext})`, fn: exportDocument });
  } else if (isCsv) {
    options.push({ label: 'Export CSV (.csv)', fn: exportDocument });
  } else {
    options.push({ label: _isRichTextLang(lang) ? 'Export HTML' : 'Export Markdown', fn: exportDocument });
  }
  if (lang === 'markdown') {
    options.push({ label: 'Preview Visual Report', fn: previewVisualReport });
    options.push({ label: 'Export as Visual Report', fn: exportAsVisualReport });
  }
  if (_isDocxLang(lang)) {
    if (_deps.convertDocxToRichText) options.push({ label: 'Convert to Rich Text', fn: _deps.convertDocxToRichText });
    options.push({ label: 'Sign / annotate (PDF)', fn: convertOriginalToPdfForSigning });
  }
  // PDF-backed documents already have a lossless filled-PDF export above.
  // Running the generic browser print path would rebuild the extracted text and
  // destroy the original page layout, images, and form structure.
  if (!isForm && (_isDocxLang(lang) || typeof window.print === 'function')) {
    options.push({
      label: _isDocxLang(lang) ? 'Convert to PDF' : 'Print / save PDF',
      fn: _isDocxLang(lang) ? () => convertOriginalDocument('pdf') : exportAsPdf,
    });
  }
  if (lang === 'pdf') {
    options.push({ label: 'Convert to Word (.docx)', fn: () => convertOriginalDocument('docx') });
  }
  if (!isSourceCode && !isCsv && !_isRichTextLang(lang) && lang !== 'markdown') {
    if (lang !== 'pdf') options.push({ label: 'Export as Word', fn: exportAsDocx });
  } else if (_isRichTextLang(lang) || lang === 'markdown') {
    options.push({ label: 'Export as Word', fn: exportAsDocx });
  }

  options.forEach(opt => {
    const item = document.createElement('button');
    item.className = 'doc-overflow-item';
    item.textContent = opt.label;
    item.addEventListener('click', (ev) => { ev.stopPropagation(); close(); opt.fn(); });
    menu.appendChild(item);
    if (opt._divider) {
      const sep = document.createElement('div');
      sep.className = 'doc-overflow-divider';
      sep.style.cssText = 'height:1px;margin:3px 6px;background:color-mix(in srgb,var(--border) 60%,transparent);';
      menu.appendChild(sep);
    }
  });

  document.body.appendChild(menu);
  const mh = menu.offsetHeight;
  if (rect.bottom + mh > window.innerHeight - 8) {
    menu.style.top = 'auto';
    menu.style.bottom = (window.innerHeight - rect.top + 2) + 'px';
  }
  const exportBtn = document.getElementById('doc-footer-export-btn');
  const close = _bindMenuDismiss(menu, () => { menu.remove(); }, (ev) => !menu.contains(ev.target) && !exportBtn?.contains(ev.target));
}

// ── DOCX Engine & AST Generation ──

let _docxReady = null;

export function ensureDocx() {
  if (typeof window !== 'undefined' && window.docx) {
    return Promise.resolve(window.docx);
  }
  if (_docxReady) return _docxReady;

  _docxReady = new Promise((resolve, reject) => {
    if (typeof document === 'undefined') {
      _docxReady = null;
      return reject(new Error('Document is undefined'));
    }
    const s = document.createElement('script');
    s.src = '/static/lib/docx.umd.min.js';
    s.onload = () => {
      resolve(window.docx);
    };
    s.onerror = () => {
      _docxReady = null; // Error recovery: allow retry on subsequent attempts
      s.remove?.();
      reject(new Error('Failed to load DOCX library'));
    };
    (document.head || document.body || document.documentElement).appendChild(s);
  }).catch((err) => {
    _docxReady = null; // Error recovery
    throw err;
  });

  return _docxReady;
}

export function _docxHexColor(value) {
  const raw = String(value || '').trim().toLowerCase();
  const named = {
    black: '000000', white: 'FFFFFF', red: 'FF0000', orange: 'FFA500', yellow: 'FFFF00',
    green: '008000', blue: '0000FF', purple: '800080', gray: '808080', grey: '808080',
  };
  if (named[raw]) return named[raw];
  const short = raw.match(/^#([0-9a-f]{3})$/i);
  if (short) return short[1].split('').map(part => part + part).join('').toUpperCase();
  const hex = raw.match(/^#([0-9a-f]{6})(?:[0-9a-f]{2})?$/i);
  if (hex) return hex[1].toUpperCase();
  const rgb = raw.match(/^rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)/i);
  if (!rgb) return '';
  return rgb.slice(1, 4)
    .map(part => Math.max(0, Math.min(255, Number(part))).toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase();
}

export function _docxInlineStyle(element, inherited = {}) {
  const next = { ...inherited };
  const tag = element?.tagName || '';
  if (tag === 'B' || tag === 'STRONG') next.bold = true;
  if (tag === 'I' || tag === 'EM') next.italics = true;
  if (tag === 'U') next.underline = true;
  if (tag === 'S' || tag === 'STRIKE' || tag === 'DEL') next.strike = true;
  if (tag === 'SUP') { next.superScript = true; delete next.subScript; }
  if (tag === 'SUB') { next.subScript = true; delete next.superScript; }
  if (tag === 'CODE') {
    next.font = 'Consolas';
    next.shading = 'F3F4F6';
  }
  const style = element?.style;
  if (style) {
    const weight = String(style.fontWeight || '').toLowerCase();
    if (weight === 'bold' || Number(weight) >= 600) next.bold = true;
    if (style.fontStyle === 'italic') next.italics = true;
    const decoration = String(style.textDecoration || style.textDecorationLine || '').toLowerCase();
    if (decoration.includes('underline')) next.underline = true;
    if (decoration.includes('line-through')) next.strike = true;
    const color = _docxHexColor(style.color);
    if (color) next.color = color;
    const shading = _docxHexColor(style.backgroundColor);
    if (shading) next.shading = shading;
    if (style.fontFamily) next.font = style.fontFamily.split(',')[0].replace(/["']/g, '').trim();
    const size = String(style.fontSize || '').match(/^([\d.]+)(px|pt)$/i);
    if (size) next.size = Math.max(12, Math.round(Number(size[1]) * (size[2].toLowerCase() === 'px' ? 1.5 : 2)));
  }
  if (tag === 'FONT') {
    const color = _docxHexColor(element.getAttribute('color'));
    if (color) next.color = color;
    if (element.getAttribute('face')) next.font = element.getAttribute('face').split(',')[0].trim();
    const sizes = { '1': 16, '2': 20, '3': 24, '4': 28, '5': 36, '6': 48, '7': 56 };
    if (sizes[element.getAttribute('size')]) next.size = sizes[element.getAttribute('size')];
  }
  return next;
}

export function _docxTextRuns(text, style, docx) {
  const value = String(text || '').replace(/\u200b/g, '');
  if (!value) return [];
  const parts = value.split('\n');
  return parts.map((part, index) => {
    const options = { text: part };
    if (index) options.break = 1;
    if (style.bold) options.bold = true;
    if (style.italics) options.italics = true;
    if (style.underline) options.underline = { type: docx.UnderlineType.SINGLE };
    if (style.strike) options.strike = true;
    if (style.superScript) options.superScript = true;
    if (style.subScript) options.subScript = true;
    if (style.font) options.font = style.font;
    if (style.size) options.size = style.size;
    if (style.color) options.color = style.color;
    if (style.shading) options.shading = { type: docx.ShadingType.CLEAR, fill: style.shading };
    return new docx.TextRun(options);
  });
}

export function _docxImageFallbackRun(image, docx) {
  const label = (image?.getAttribute?.('alt') || '').trim();
  return new docx.TextRun({
    text: label ? `[Image: ${label}]` : '[Image]',
    italics: true,
    color: '6B7280',
  });
}

export function _docxImageWidth(image, naturalWidth) {
  const pageWidth = 624;
  const classWidths = {
    'richtext-image-size-100': pageWidth,
    'richtext-image-size-60': Math.round(pageWidth * 0.6),
    'richtext-image-size-35': Math.round(pageWidth * 0.35),
  };
  for (const [className, width] of Object.entries(classWidths)) {
    if (image.classList.contains(className)) return width;
  }
  const styleWidth = String(image.style?.width || '').trim().match(/^([\d.]+)(px|%)$/i);
  if (styleWidth) {
    const value = Number(styleWidth[1]);
    if (Number.isFinite(value) && value > 0) {
      return Math.max(1, Math.round(styleWidth[2] === '%' ? pageWidth * value / 100 : Math.min(pageWidth, value)));
    }
  }
  return Math.max(1, Math.min(pageWidth, Math.round(naturalWidth || pageWidth)));
}

export function _docxImageAlignment(image, docx) {
  if (image?.classList?.contains('richtext-image-align-center')) return docx.AlignmentType.CENTER;
  if (image?.classList?.contains('richtext-image-align-right')) return docx.AlignmentType.RIGHT;
  return docx.AlignmentType.LEFT;
}

export async function _docxImagePngData(image) {
  const src = image?.getAttribute?.('src') || '';
  if (!src) throw new Error('Image has no source');
  const response = await fetch(src, { credentials: 'same-origin' });
  if (!response.ok) throw new Error(`Image request failed (${response.status})`);
  const blob = await response.blob();
  let drawable;
  let cleanup = () => {};
  if (typeof createImageBitmap === 'function') {
    drawable = await createImageBitmap(blob);
    cleanup = () => drawable.close?.();
  } else {
    const objectUrl = URL.createObjectURL(blob);
    cleanup = () => URL.revokeObjectURL(objectUrl);
    try {
      drawable = await new Promise((resolve, reject) => {
        const loaded = new Image();
        loaded.onload = () => resolve(loaded);
        loaded.onerror = () => reject(new Error('Image could not be decoded'));
        loaded.src = objectUrl;
      });
    } catch (error) {
      cleanup();
      throw error;
    }
  }
  try {
    const naturalWidth = drawable.width || drawable.naturalWidth || 1;
    const naturalHeight = drawable.height || drawable.naturalHeight || 1;
    const rasterScale = Math.min(1, 2400 / Math.max(naturalWidth, naturalHeight));
    const rasterWidth = Math.max(1, Math.round(naturalWidth * rasterScale));
    const rasterHeight = Math.max(1, Math.round(naturalHeight * rasterScale));
    const canvas = document.createElement('canvas');
    canvas.width = rasterWidth;
    canvas.height = rasterHeight;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas is unavailable');
    context.drawImage(drawable, 0, 0, rasterWidth, rasterHeight);
    const png = await new Promise((resolve, reject) => {
      canvas.toBlob(result => result ? resolve(result) : reject(new Error('PNG conversion failed')), 'image/png');
    });
    return { data: new Uint8Array(await png.arrayBuffer()), naturalWidth, naturalHeight };
  } finally {
    cleanup();
  }
}

export async function _docxPrepareImages(root, docx) {
  const prepared = new Map();
  const images = Array.from(root.querySelectorAll('img'));
  await Promise.all(images.map(async (image, index) => {
    try {
      const source = await _docxImagePngData(image);
      const width = _docxImageWidth(image, source.naturalWidth);
      const height = Math.max(1, Math.round(width * source.naturalHeight / source.naturalWidth));
      const description = (image.getAttribute('alt') || '').trim();
      prepared.set(image, {
        data: source.data,
        transformation: { width, height },
        altText: {
          name: `Document image ${index + 1}`,
          title: description || `Image ${index + 1}`,
          description,
        },
      });
    } catch (error) {
      console.warn('Could not include image in Word export:', image.getAttribute('src') || '', error);
    }
  }));
  return prepared;
}

export function _docxInlineChildren(nodes, docx, inherited = {}, preparedImages = new Map()) {
  const children = [];
  Array.from(nodes || []).forEach(node => {
    if (node.nodeType === Node.TEXT_NODE) {
      children.push(..._docxTextRuns(node.nodeValue, inherited, docx));
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.tagName === 'BR') {
      children.push(new docx.TextRun({ text: '', break: 1 }));
      return;
    }
    if (node.tagName === 'IMG') {
      const imageOptions = preparedImages.get(node);
      children.push(imageOptions ? new docx.ImageRun(imageOptions) : _docxImageFallbackRun(node, docx));
      return;
    }
    const style = _docxInlineStyle(node, inherited);
    const nested = _docxInlineChildren(node.childNodes, docx, style, preparedImages);
    if (node.tagName === 'A' && nested.length) {
      const safeHref = _normalizeRichLinkUrl(node.getAttribute('href'));
      if (safeHref) {
        children.push(new docx.ExternalHyperlink({ link: safeHref, children: nested }));
      } else {
        children.push(...nested);
      }
    } else {
      children.push(...nested);
    }
  });
  return children;
}

export function _docxParagraphOptions(element, docx) {
  const options = {};
  const heading = {
    H1: docx.HeadingLevel.HEADING_1, H2: docx.HeadingLevel.HEADING_2,
    H3: docx.HeadingLevel.HEADING_3, H4: docx.HeadingLevel.HEADING_4,
    H5: docx.HeadingLevel.HEADING_5, H6: docx.HeadingLevel.HEADING_6,
  }[element?.tagName];
  if (heading) options.heading = heading;
  const alignment = String(element?.style?.textAlign || element?.getAttribute?.('align') || '').toLowerCase();
  const alignments = {
    left: docx.AlignmentType.LEFT, center: docx.AlignmentType.CENTER,
    right: docx.AlignmentType.RIGHT, justify: docx.AlignmentType.JUSTIFIED,
  };
  if (alignments[alignment]) options.alignment = alignments[alignment];
  const lineHeight = Number.parseFloat(element?.style?.lineHeight || '');
  options.spacing = { after: 120 };
  if (Number.isFinite(lineHeight)) options.spacing.line = Math.round(240 * lineHeight);
  if (element?.tagName === 'BLOCKQUOTE') options.indent = { left: 720 };
  if (element?.tagName === 'PRE') {
    options.shading = { type: docx.ShadingType.CLEAR, fill: 'F5F6F8' };
    options.spacing.before = 120;
  }
  const image = element?.tagName === 'IMG'
    ? element
    : (!(element?.textContent || '').trim() ? element?.querySelector?.('img.richtext-image, img') : null);
  if (image) options.alignment = _docxImageAlignment(image, docx);
  return options;
}

export function _docxParagraphFromElement(element, docx, extra = {}, preparedImages = new Map()) {
  let children;
  if (element.tagName === 'PRE') {
    children = _docxTextRuns(element.textContent || '', { font: 'Consolas', size: 20 }, docx);
  } else if (element.tagName === 'IMG') {
    children = _docxInlineChildren([element], docx, {}, preparedImages);
  } else {
    const inlineNodes = Array.from(element.childNodes).filter(node => {
      return node.nodeType !== Node.ELEMENT_NODE || !['UL', 'OL', 'TABLE'].includes(node.tagName);
    });
    children = _docxInlineChildren(inlineNodes, docx, {}, preparedImages);
  }
  return new docx.Paragraph({ ..._docxParagraphOptions(element, docx), ...extra, children });
}

export function _docxListBlocks(list, docx, level = 0, preparedImages = new Map()) {
  const blocks = [];
  const checklist = list.classList.contains('rich-checklist');
  Array.from(list.children).filter(item => item.tagName === 'LI').forEach(item => {
    const inlineNodes = Array.from(item.childNodes).filter(node => {
      return node.nodeType !== Node.ELEMENT_NODE || (node.tagName !== 'UL' && node.tagName !== 'OL');
    });
    const children = _docxInlineChildren(inlineNodes, docx, {}, preparedImages);
    const extra = checklist
      ? { indent: { left: 720 + level * 360, hanging: 300 } }
      : list.tagName === 'OL'
        ? { numbering: { reference: 'rich-numbered-list', level: Math.min(5, level) } }
        : { bullet: { level: Math.min(5, level) } };
    if (checklist) {
      children.unshift(new docx.TextRun({ text: item.dataset.checked === 'true' ? '[x] ' : '[ ] ', bold: true }));
    }
    blocks.push(new docx.Paragraph({ ...extra, spacing: { after: 60 }, children }));
    Array.from(item.children)
      .filter(child => child.tagName === 'UL' || child.tagName === 'OL')
      .forEach(child => blocks.push(..._docxListBlocks(child, docx, level + 1, preparedImages)));
  });
  return blocks;
}

export function _docxTableBlock(table, docx, preparedImages = new Map()) {
  const rows = Array.from(table.querySelectorAll('tr')).map(tr => {
    const tableHeader = tr.parentElement?.tagName === 'THEAD' || Array.from(tr.children).every(cell => cell.tagName === 'TH');
    const cells = Array.from(tr.children).filter(cell => cell.tagName === 'TD' || cell.tagName === 'TH').map(cell => {
      const inlineNodes = Array.from(cell.childNodes).filter(node => {
        return node.nodeType !== Node.ELEMENT_NODE || !['UL', 'OL', 'TABLE'].includes(node.tagName);
      });
      const paragraphs = [new docx.Paragraph({
        ..._docxParagraphOptions(cell, docx),
        spacing: { after: 0 },
        children: _docxInlineChildren(inlineNodes, docx, cell.tagName === 'TH' ? { bold: true } : {}, preparedImages),
      })];
      Array.from(cell.children).filter(child => child.tagName === 'UL' || child.tagName === 'OL').forEach(list => {
        paragraphs.push(..._docxListBlocks(list, docx, 0, preparedImages));
      });
      const options = {
        children: paragraphs.length ? paragraphs : [new docx.Paragraph({ children: [] })],
      };
      const colSpan = Number(cell.getAttribute('colspan') || '1');
      const rowSpan = Number(cell.getAttribute('rowspan') || '1');
      if (colSpan > 1) options.columnSpan = colSpan;
      if (rowSpan > 1) options.rowSpan = rowSpan;
      const verticalAlignment = String(cell.style?.verticalAlign || cell.getAttribute?.('valign') || '').toLowerCase();
      const docxVerticalAlignment = {
        top: docx.VerticalAlign?.TOP,
        middle: docx.VerticalAlign?.CENTER,
        bottom: docx.VerticalAlign?.BOTTOM,
      }[verticalAlignment];
      if (docxVerticalAlignment) options.verticalAlign = docxVerticalAlignment;
      if (cell.tagName === 'TH') {
        options.shading = { type: docx.ShadingType.CLEAR, fill: 'F1F3F5' };
      }
      return new docx.TableCell(options);
    });
    return new docx.TableRow({ children: cells, tableHeader });
  });
  return new docx.Table({
    rows,
    width: { size: 100, type: docx.WidthType.PERCENTAGE },
    margins: { top: 80, bottom: 80, left: 100, right: 100 },
  });
}

export function _docxFigureBlocks(figure, docx, preparedImages = new Map()) {
  const image = figure.querySelector(':scope > img, :scope > picture img');
  if (!image) return [];
  const blocks = [new docx.Paragraph({
    alignment: _docxImageAlignment(image, docx),
    children: _docxInlineChildren([image], docx, {}, preparedImages),
  })];
  const caption = figure.querySelector(':scope > .richtext-image-caption, :scope > figcaption');
  if (caption?.textContent?.trim()) {
    blocks.push(new docx.Paragraph({
      alignment: _docxImageAlignment(image, docx),
      spacing: { before: 60, after: 120 },
      children: _docxInlineChildren(caption.childNodes, docx, {
        italics: true,
        color: '6B7280',
        size: 18,
      }, preparedImages),
    }));
  }
  return blocks;
}

export function _docxBlocksFromNodes(nodes, docx, preparedImages = new Map()) {
  const blocks = [];
  Array.from(nodes || []).forEach(node => {
    if (node.nodeType === Node.TEXT_NODE) {
      if (node.nodeValue.trim()) {
        blocks.push(new docx.Paragraph({ children: _docxTextRuns(node.nodeValue, {}, docx) }));
      }
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.tagName === 'UL' || node.tagName === 'OL') {
      blocks.push(..._docxListBlocks(node, docx, 0, preparedImages));
    } else if (node.tagName === 'FIGURE' && node.classList.contains('richtext-image')) {
      blocks.push(..._docxFigureBlocks(node, docx, preparedImages));
    } else if (node.tagName === 'TABLE') {
      blocks.push(_docxTableBlock(node, docx, preparedImages));
    } else if (node.tagName === 'HR') {
      blocks.push(node.classList.contains('richtext-page-break')
        ? new docx.Paragraph({ children: [new docx.PageBreak()] })
        : new docx.Paragraph({ thematicBreak: true }));
    } else if (node.tagName === 'DIV' && Array.from(node.children).some(child => {
      return /^(P|DIV|H[1-6]|UL|OL|BLOCKQUOTE|PRE|TABLE|HR)$/.test(child.tagName);
    })) {
      blocks.push(..._docxBlocksFromNodes(node.childNodes, docx, preparedImages));
    } else {
      blocks.push(_docxParagraphFromElement(node, docx, {}, preparedImages));
    }
  });
  return blocks;
}

export async function _richTextToDocxChildren(html, docx) {
  const safeHtml = _richTextToHtmlString(html);
  const parsed = typeof DOMParser !== 'undefined'
    ? new DOMParser().parseFromString(safeHtml, 'text/html').body
    : null;
  if (!parsed) return [];
  const preparedImages = await _docxPrepareImages(parsed, docx);
  return _docxBlocksFromNodes(parsed.childNodes, docx, preparedImages);
}

export function _markdownToDocxChildren(text, docx) {
  return String(text || '').split('\n').map(line => {
    const heading = line.match(/^(#{1,6})\s+(.+)/);
    if (heading) {
      return new docx.Paragraph({
        text: heading[2],
        heading: docx.HeadingLevel[`HEADING_${heading[1].length}`],
      });
    }
    const runs = [];
    const parts = line.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/);
    for (const part of parts) {
      if (part.startsWith('**') && part.endsWith('**')) {
        runs.push(new docx.TextRun({ text: part.slice(2, -2), bold: true }));
      } else if (part.startsWith('*') && part.endsWith('*')) {
        runs.push(new docx.TextRun({ text: part.slice(1, -1), italics: true }));
      } else {
        runs.push(new docx.TextRun(part));
      }
    }
    return new docx.Paragraph({ children: runs });
  });
}

export function _richDocxNumbering(docx) {
  return {
    config: [{
      reference: 'rich-numbered-list',
      levels: Array.from({ length: 6 }, (_, level) => ({
        level,
        format: docx.NumberFormat.DECIMAL,
        text: `%${level + 1}.`,
        alignment: docx.AlignmentType.START,
        style: { paragraph: { indent: { left: 720 + level * 360, hanging: 300 } } },
      })),
    }],
  };
}

export async function exportAsDocx(doc) {
  const targetDoc = (doc && typeof doc === 'object' && !('preventDefault' in doc)) ? doc : _getActiveDoc();
  const docId = targetDoc?.id || _getActiveDocId();
  if (!docId) return;
  const textarea = _getTextarea();
  if (!textarea && !targetDoc) return;
  const ui = _getUiModule();
  try {
    await ensureDocx();
  } catch (e) {
    if (ui) ui.showError('Failed to load DOCX library');
    return;
  }
  // PDF/DOCX documents carry an internal upload pointer for their native
  // preview. It is useful to the app, but should never appear in a
  // converted editable Word file.
  const rawText = (textarea ? textarea.value : targetDoc?.content) || '';
  const text = rawText
    .replace(/^\s*<!--\s*(?:pdf|pdf_form|docx)_source\s+upload_id="[^"]+"\s*-->\s*/i, '')
    .replace(/^\s*<!--\s*docx_source\s+upload_id="[^"]+"\s*-->\s*/i, '');
  const lang = (targetDoc && targetDoc.language) || _getLanguage();
  const children = _isRichTextLang(lang)
    ? await _richTextToDocxChildren(text, window.docx)
    : _markdownToDocxChildren(text, window.docx);
  const wordDoc = new window.docx.Document({
    numbering: _richDocxNumbering(window.docx),
    sections: [{ children }],
  });
  const blob = await window.docx.Packer.toBlob(wordDoc);
  const baseName = _getExportBaseName(targetDoc);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = baseName + '.docx';
  a.click();
  URL.revokeObjectURL(a.href);
  if (ui) ui.showToast('Exported as DOCX');
}
