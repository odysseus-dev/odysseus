import { closePanel, saveCurrentToMap } from '../../application/documentCommands.js';
import { enterDiffMode } from '../diffViewer.js';
// ui/header/headerActions.js

import { API_BASE, _documentNavigationGeneration, activeDocId, docs, isOpen } from '../../application/documentState.js';

import { saveDocument } from '../../application/persistence.js';

import { _hasViewToggle, _isDocxLang, _isRenderLang, _isRichTextLang } from '../../domain/documentKinds.js';

import { copyDocument, runDocument } from '../documentActions.js';

import { closeTab } from '../documentFlows.js';

import { _handleMarkdownPreviewClickHint, syncHighlighting, updateLineNumbers } from '../editor/sourceEditor.js';

import { _sendSignedReply } from '../email/signedReply.js';

import { _closeVersionPanel, toggleVersionHistory } from '../history/versionHistory.js';

import { _dismissDocKb, _wireSwipeDismiss } from '../panel/paneLayout.js';

import { _emailRichbodyActive, _scheduleEmailRichbodySave, _syncEmailRichbody } from '../richEditor/richBodySync.js';
import { _hideMarkdownVisualEditor, _showMarkdownVisualEditor } from '../richEditor/richBodyWiring.js';
import { alignText } from '../../domain/sourceFidelity.js';
import { markdownToVisualHtml } from '../../domain/markdownVisualCodec.js';
import { segmentMarkdownBlocks } from '../../domain/sourceSegmentation.js';
import {
  assessMarkdownVisualSupport,
  discardVisualDraft,
  getVisualCaretBlockInfo,
  hideMarkdownCompatBanner,
  isMarkdownVisualModeActive,
  isVisualMarkdownDoc,
  renderMarkdownCompatBanner,
  setMarkdownModePreference,
} from '../richEditor/markdownVisualSync.js';

import { _scheduleSelRerender } from '../selection/selectionHighlights.js';

import { _closeDocumentOutline, _documentOutlineEntries, _documentOutlineSupported, _scheduleDocumentHistoryControls, _syncDocumentHistoryControlsNow } from '../status/documentStatus.js';

import { _syncDocumentToolbarSeparators } from '../toolbar/toolbar.js';

import { _hidePdfViewPane, _isFormBackedDoc, _pdfViewState, _renderPdfPane } from '../pdfTools.js';
import { _undoPdfPaneAction } from '../pdfTools.js';
import { _diffModeActive, exitDiffMode } from '../diffViewer.js';
import { _setMarkdownPreviewActive, toggleCsvPreview, toggleDocxPreview, toggleHtmlPreview, toggleMarkdownPreview, toggleRichTextPreview } from '../previewModes.js';
import { showExportMenu } from '../exportDocx.js';
import { _importFromDevice } from '../importFromDevice.js';

import { openLibrary, uiModule } from '../host.js';

function _syncHeaderBarVisibility() {
    const hdr = document.getElementById('doc-editor-actions');
    if (!hdr) return;
    // Email docs hide the whole header (they use their own send footer) — never
    // resurrect it here.
    if (docs.get(activeDocId)?.language === 'email') { hdr.style.display = 'none'; return; }
    const vis = (id) => {
      const e = document.getElementById(id);
      if (!e || !e.parentElement) return false;
      // Only count items still LIVING in the header itself — the runtime
      // rearrangement (~line 3217) moves several buttons into the footer, and
      // we don't want a button parked elsewhere to keep this top row alive.
      if (!hdr.contains(e)) return false;
      return e.style.display !== 'none';
    };
    // Hide the whole header when nothing visible lives here anymore. Without
    // this every desktop view rendered an empty doc-editor-header above the
    // real action footer — a duplicate row.
    const visible = vis('doc-stream-indicator')
      || vis('doc-version-badge')
      || vis('doc-export-pdf-btn')
      || vis('doc-pdf-view-btn');
    hdr.style.display = visible ? '' : 'none';
  }

export function _syncHeaderActions() {
    const actionBtn = document.getElementById('doc-header-preview-btn');
    const exportBtn = document.getElementById('doc-export-pdf-btn');
    const pdfViewBtn = document.getElementById('doc-pdf-view-btn');
    const pdfPane = document.getElementById('doc-pdf-view');
    const langSelect = document.getElementById('doc-language-select');
    const live = document.getElementById('doc-editor-textarea')?.value
      || docs.get(activeDocId)?.content
      || '';
    const isForm = _isFormBackedDoc(live);
    // Footer main button: for a doc opened from an email attachment, morph the
    // Save button into "Attach" (send the filled file back to the sender via
    // the signed-reply flow). Otherwise it forces a new saved version.
    const _copyBtn = document.getElementById('doc-footer-copy-btn');
    if (_copyBtn) {
      const _ad = docs.get(activeDocId);
      const _replyable = !!(_ad && _ad.sourceEmailUid && _ad.sourceEmailFolder);
      if (_replyable && _copyBtn.dataset.mode !== 'reply') {
        _copyBtn.dataset.mode = 'reply';
        _copyBtn.title = 'Reply to the sender with this filled file attached';
        _copyBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="color:var(--fg);"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>Attach';
      } else if (!_replyable && _copyBtn.dataset.mode !== 'save') {
        _copyBtn.dataset.mode = 'save';
        _copyBtn.title = 'Save new version';
        _copyBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save';
      }
    }
    // Standalone Export PDF / PDF-toggle icon buttons are retired — for a
    // form-backed doc the language selector itself toggles between
    // "pdf" (rendered view) and "markdown" (source view).
    if (exportBtn) exportBtn.style.display = 'none';
    if (pdfViewBtn) pdfViewBtn.style.display = 'none';
    if (true) {
      const explicit = _pdfViewState.get(activeDocId);
      const active = isForm && explicit !== false;
      // Sync the language select's displayed value to the current view.
      if (isForm && langSelect) {
        const want = active ? 'pdf' : 'markdown';
        if (langSelect.value !== want) langSelect.value = want;
      }
      if (pdfPane) {
        if (active) {
          if (pdfPane.style.display === 'none' || pdfPane.dataset.renderedDocId !== activeDocId) {
            const wrap = document.getElementById('doc-editor-wrap');
            if (wrap) wrap.style.display = 'none';
            pdfPane.style.display = '';
            _renderPdfPane();
          }
          _syncDocumentHistoryControlsNow();
        } else if (pdfPane.style.display !== 'none') {
          _hidePdfViewPane(pdfPane);
          const wrap = document.getElementById('doc-editor-wrap');
          if (wrap) wrap.style.display = '';
          _scheduleDocumentHistoryControls();
        }
      }
    }
    if (!actionBtn) return;

    const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
    const canPreview = ['markdown', 'csv'].includes(lang) || _isRenderLang(lang);
    const canRun = ['javascript', 'js', 'python', 'py', 'bash', 'sh', 'shell', 'zsh'].includes(lang);

    const _eyeIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    const _penIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>';
    const _playIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    const _codeIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';

    // Check active states
    const _mdPreview = document.getElementById('doc-md-preview');
    const _csvPreview = document.getElementById('doc-csv-preview');
    const _htmlPreview = document.getElementById('doc-html-preview');
    const _docxPreview = document.getElementById('doc-docx-preview');
    const _outputPanel = document.getElementById('doc-run-output');
    const _mdActive = _mdPreview && _mdPreview.style.display !== 'none';
    const _csvActive = _csvPreview && _csvPreview.style.display !== 'none';
    const _htmlActive = _htmlPreview && _htmlPreview.style.display !== 'none';
    const _docxActive = _docxPreview && _docxPreview.style.display !== 'none';
    const _richPreviewActive = lang === 'richtext' || lang === 'rich-text'
      ? (_mdPreview && _mdPreview.style.display !== 'none')
      : false;
    const _outputActive = _outputPanel && _outputPanel.style.display !== 'none';

    const doc = activeDocId && docs.get(activeDocId);
    const isPdfDoc = !!(doc && _isFormBackedDoc(doc.content || ''));
    const visualMdActive = lang === 'markdown' && !isPdfDoc && !_mdActive && isMarkdownVisualModeActive();

    let show = false;
    actionBtn.classList.remove('active');

    // The markdown Visual/Markdown toggle is a two-option segmented switch;
    // hidden for form-backed PDFs.
    const mdToggle = document.getElementById('doc-md-view-toggle');
    if (mdToggle) mdToggle.style.display = (lang === 'markdown' && !isPdfDoc) ? 'inline-flex' : 'none';
    const renderToggle = document.getElementById('doc-render-view-toggle');
    if (renderToggle) {
      renderToggle.style.display = _hasViewToggle(lang) ? 'inline-flex' : 'none';
      // Swap the "run" side's icon to match what the language actually does:
      //   CSV → 4-quadrant grid (table view)
      //   HTML / SVG / XML → eye (rendered preview)
      //   Python / JS / TS / bash → play triangle (run code)
      const runBtn = renderToggle.querySelector('[data-renderview="run"]');
      if (runBtn) {
        let icon, title;
        if (lang === 'csv') {
          icon = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>';
          title = 'Table view';
        } else if (_isRenderLang(lang) || _isDocxLang(lang) || _isRichTextLang(lang)) {
          icon = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
          title = 'Preview';
        } else {
          icon = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
          title = 'Run';
        }
        if (runBtn.dataset.lastIcon !== lang) {
          const label = lang === 'csv' ? 'Table' : ((_isRenderLang(lang) || _isDocxLang(lang) || _isRichTextLang(lang)) ? 'Preview' : 'Run');
          runBtn.innerHTML = `${icon}<span class="md-view-label">${label}</span>`;
          runBtn.title = title;
          runBtn.dataset.lastIcon = lang;
        }
      }
      // Swap the "code" side's icon too — CSV's "code" really means "edit
      // the underlying spreadsheet text", so a pencil reads better than the
      // </> brackets used for actual code.
      const codeBtn = renderToggle.querySelector('[data-renderview="code"]');
      if (codeBtn) {
        const codeIco = (lang === 'csv')
          ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>'
          : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
        const codeTitle = (lang === 'csv') ? 'Edit' : 'Edit code';
        if (codeBtn.dataset.lastIcon !== lang) {
          codeBtn.innerHTML = `${codeIco}<span class="md-view-label">Edit</span>`;
          codeBtn.title = codeTitle;
          codeBtn.dataset.lastIcon = lang;
        }
      }
      // Reflect which side is currently active so the toggle shows the same
      // visual feedback markdown's Edit/Preview switch does (background tint
      // + the "punch" pop animation from .md-view-toggle .md-view-opt.active).
      // For CSV the run side = table view; for HTML/SVG/XML = iframe preview;
      // for runnable langs = output panel open.
      let _viewActive = false;
      if (lang === 'csv') _viewActive = _csvActive;
      else if (_isRenderLang(lang)) _viewActive = _htmlActive;
      else if (_isDocxLang(lang)) _viewActive = _docxActive;
      else if (_isRichTextLang(lang)) _viewActive = _richPreviewActive;
      else _viewActive = _outputActive;
      const _codeBtn2 = renderToggle.querySelector('[data-renderview="code"]');
      const _runBtn2 = renderToggle.querySelector('[data-renderview="run"]');
      _codeBtn2?.classList.toggle('active', !_viewActive);
      _runBtn2?.classList.toggle('active', _viewActive);
    }

    if (lang === 'markdown') {
      show = Boolean(doc);
      actionBtn.textContent = _mdActive ? 'Edit' : 'Preview';
      actionBtn.title = _mdActive ? 'Return to editor' : 'Preview Markdown';
      actionBtn.setAttribute('aria-pressed', String(Boolean(_mdActive)));
      if (mdToggle) {
        const visualBtn = mdToggle.querySelector('[data-mdview="visual"]');
        const editBtn = mdToggle.querySelector('[data-mdview="edit"]');
        const compatBlocked = !isPdfDoc && isVisualMarkdownDoc(doc)
          && Array.isArray(doc?._visualCompatReasons) && doc._visualCompatReasons.length > 0;
        const visualOn = !_mdActive && visualMdActive;
        const markdownOn = !_mdActive && !visualMdActive;
        visualBtn?.classList.toggle('active', visualOn);
        visualBtn?.classList.toggle('is-compat-blocked', compatBlocked);
        visualBtn?.setAttribute('aria-pressed', visualOn ? 'true' : 'false');
        editBtn?.classList.toggle('active', markdownOn);
        editBtn?.setAttribute('aria-pressed', markdownOn ? 'true' : 'false');
        mdToggle.classList.toggle('is-preview-active', !!_mdActive);
      }
    } else if (lang === 'csv') {
      show = true;
      actionBtn.innerHTML = _csvActive ? _penIco : '<span style="font-size:12px;font-weight:600;">⊞</span>';
      actionBtn.title = _csvActive ? 'Edit' : 'Table View';
      if (_csvActive) actionBtn.classList.add('active');
    } else if (_isRenderLang(lang)) {
      // SVG/HTML/XML use the segmented Code </> | Run ▶ light-switch toggle
      // (like markdown's edit/preview switch) instead of the single button.
      show = false;
      if (renderToggle) {
        renderToggle.querySelector('[data-renderview="code"]')?.classList.toggle('active', !_htmlActive);
        renderToggle.querySelector('[data-renderview="run"]')?.classList.toggle('active', _htmlActive);
      }
    } else if (_isDocxLang(lang)) {
      show = false;
      if (renderToggle) {
        renderToggle.querySelector('[data-renderview="code"]')?.classList.toggle('active', !_docxActive);
        renderToggle.querySelector('[data-renderview="run"]')?.classList.toggle('active', _docxActive);
      }
    } else if (_isRichTextLang(lang)) {
      show = false;
      if (renderToggle) {
        renderToggle.querySelector('[data-renderview="code"]')?.classList.toggle('active', !_richPreviewActive);
        renderToggle.querySelector('[data-renderview="run"]')?.classList.toggle('active', _richPreviewActive);
      }
    } else if (canRun) {
      show = true;
      actionBtn.innerHTML = _outputActive ? _codeIco : _playIco;
      actionBtn.title = _outputActive ? 'Hide output' : 'Run';
      if (_outputActive) actionBtn.classList.add('active');
    }

    // The unified segmented Code/Run-or-View toggle (`#doc-render-view-toggle`)
    // covers CSV / Python / JS / bash / HTML / SVG / XML. When it's shown,
    // suppress the single morph button to avoid two redundant controls.
    if (_hasViewToggle(lang) && lang !== 'markdown') show = false;
    if (lang !== 'markdown') actionBtn.removeAttribute('aria-pressed');
    actionBtn.style.display = show ? '' : 'none';
    document.querySelectorAll('.md-toolbar-edit-only').forEach(el => {
      el.style.display = (lang === 'markdown' && _mdActive) ? 'none' : '';
    });
    const aiWritingButton = document.getElementById('doc-ai-writing-btn');
    if (aiWritingButton) {
      const writingMode = lang === 'email' || lang === 'markdown' || _isRichTextLang(lang);
      aiWritingButton.style.display = writingMode && !(lang === 'markdown' && _mdActive) ? '' : 'none';
    }
    document.querySelectorAll('.md-toolbar-rich-only').forEach(el => {
      const action = el.dataset.md || el.dataset.dd;
      const isMdOnlyRichAction = action === 'alert';
      const allowInMdVisual = visualMdActive && (action === 'quote' || action === 'table' || action === 'alert' || action === 'code' || action === 'removeformat');
      const allowInRich = _isRichTextLang(lang) && !isMdOnlyRichAction;
      el.style.display = (allowInRich || allowInMdVisual) ? '' : 'none';
    });
    const richMode = _isRichTextLang(lang);
    const attachButton = document.getElementById('md-toolbar-attach-btn');
    if (attachButton) {
      const paperclip = attachButton.querySelector('.md-attach-paperclip-icon');
      const imageIcon = attachButton.querySelector('.md-attach-image-icon');
      const isEmail = lang === 'email';
      if (paperclip) paperclip.style.display = isEmail ? '' : 'none';
      if (imageIcon) imageIcon.style.display = isEmail ? 'none' : '';
      attachButton.title = isEmail ? 'Attach file' : 'Insert image';
      attachButton.setAttribute('aria-label', attachButton.title);
    }
    const inlineImageButton = document.getElementById('md-toolbar-inline-image-btn');
    if (inlineImageButton) inlineImageButton.style.display = (lang === 'email' || richMode) ? '' : 'none';
    document.querySelectorAll('.md-toolbar-inline-format').forEach(el => {
      // Visual Markdown reuses the separator control; it offers only the plain
      // divider, which is the one style Markdown can represent (`---`).
      const allowInMdVisual = visualMdActive && el.dataset.dd === 'separator';
      el.style.display = (lang === 'email' || richMode || allowInMdVisual) ? '' : 'none';
    });
    const alignButton = document.querySelector('.md-toolbar-align-control[data-dd="align"]');
    if (alignButton) alignButton.style.display = (lang === 'email' || richMode) ? '' : 'none';
    _syncDocumentToolbarSeparators(document.getElementById('md-toolbar-items'));
    const fsBtn = document.getElementById('doc-fontsize-btn');
    if (fsBtn) {
      fsBtn.style.display = (isPdfDoc || richMode || visualMdActive || (lang === 'markdown' && _mdActive)) ? 'none' : '';
    }
    const redoBtn = document.getElementById('doc-redo-btn');
    if (redoBtn) {
      redoBtn.style.display = isPdfDoc ? 'none' : '';
    }
    const outlineBtn = document.getElementById('doc-outline-toolbar-btn');
    if (outlineBtn) {
      const hasOutline = _documentOutlineSupported() && _documentOutlineEntries().length > 0;
      outlineBtn.style.display = hasOutline ? '' : 'none';
      if (!hasOutline) _closeDocumentOutline();
    }
    const mdToolbar = document.getElementById('doc-md-toolbar');
    if (mdToolbar) {
      mdToolbar.classList.toggle('md-preview-active', lang === 'markdown' && !!_mdActive);
      mdToolbar.classList.toggle('md-write-active', lang === 'markdown' && !_mdActive);
      mdToolbar.classList.toggle('md-visual-active', visualMdActive);
    }
    const stats = document.getElementById('doc-stats');
    if (stats) stats.style.display = (_hasViewToggle(lang) && lang !== 'csv') ? 'none' : '';
    if (_mdPreview) _mdPreview.classList.toggle('md-preview-active', lang === 'markdown' && !!_mdActive);
    if (mdToolbar && mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);

    // Now that the contextual buttons' visibility is settled, collapse the bar
    // if it ended up empty (the common plain-doc-on-mobile case).
    _syncHeaderBarVisibility();
  }

export function bindHeaderActionsPrimary({ pane, divider, paneLifetime, isRight }) {
_wireSwipeDismiss(document.getElementById('doc-mobile-grabber'));
document.getElementById('doc-mobile-grabber')?.addEventListener('click', () => closePanel('down'));
document.getElementById('doc-close-btn')?.addEventListener('click', () => closePanel('down'));
document.getElementById('doc-footer-close-btn')?.addEventListener('click', () => { if (activeDocId) closeTab(activeDocId); });
document.getElementById('doc-import-btn')?.addEventListener('click', () => openLibrary());
document.getElementById('doc-rich-empty-import')?.querySelector('button')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      _importFromDevice();
    });
document.getElementById('doc-footer-copy-btn')?.addEventListener('click', (e) => {
      if (e.currentTarget.dataset.mode === 'reply') { if (activeDocId) _sendSignedReply(activeDocId); }
      else saveDocument({ silent: false, forceVersion: true });
    });
document.getElementById('doc-footer-export-btn')?.addEventListener('click', (e) => showExportMenu(e, e.currentTarget.getBoundingClientRect()));
document.getElementById('doc-mobile-close')?.addEventListener('click', () => { if (activeDocId) closeTab(activeDocId); });
document.getElementById('doc-mobile-copy')?.addEventListener('click', () => copyDocument());
document.getElementById('doc-version-badge').addEventListener('click', toggleVersionHistory);
document.getElementById('doc-version-close').addEventListener('click', () => _closeVersionPanel({ restoreFocus: true }));
}

// Map text boundaries rather than counting Markdown delimiters as visible text.
function mapCaretTextOffset(from, to, offset) {
  const at = Math.min(Math.max(0, offset), from.length);
  const alignment = alignText(from, to);
  if (!alignment) return Math.min(at, to.length);
  for (let index = at; index < alignment.length; index++) {
    if (alignment[index] >= 0) return alignment[index];
  }
  for (let index = at - 1; index >= 0; index--) {
    if (alignment[index] >= 0) return alignment[index] + 1;
  }
  return 0;
}

/**
 * Switch the active Markdown document between the Visual editor and the Markdown
 * source editor. The current content is captured through `saveCurrentToMap`, which
 * owns the pane-identity and history-preview guards, so a switch never writes
 * stale or previewed content into the document.
 * @param {'visual'|'markdown'} requestedMode
 * @returns {boolean} true when the requested mode is shown
 */
export function _setMarkdownEditorMode(requestedMode, { focus = true, persistPreference = true, notifyBlocked = true, discardVisualDraft: forceDiscard = false } = {}) {
    const doc = activeDocId ? docs.get(activeDocId) : null;
    if (!doc || !isVisualMarkdownDoc(doc)) return false;

    const rich = document.getElementById('doc-email-richbody');
    const ta = document.getElementById('doc-editor-textarea');
    const mdPrev = document.getElementById('doc-md-preview');
    const wasPreview = !!(mdPrev && mdPrev.style.display !== 'none');

    if (requestedMode === 'visual' && !wasPreview && isMarkdownVisualModeActive()) {
      doc._markdownEditorMode = 'visual';
      if (persistPreference) setMarkdownModePreference('visual');
      return true;
    }

    let visualCaretInfo = null;
    if (requestedMode === 'markdown' && rich && rich.classList.contains('markdown-visual-mode')) {
      visualCaretInfo = getVisualCaretBlockInfo(rich);
    }
    let targetBlockIndex = null;
    let targetOffset = 0;
    if (requestedMode === 'visual' && ta && (document.activeElement === ta || focus)) {
      const sourceCaretPos = ta.selectionStart || 0;
      const segments = segmentMarkdownBlocks(ta.value);
      for (let i = 0; i < segments.length; i++) {
        const seg = segments[i];
        if (sourceCaretPos >= seg.startOffset && sourceCaretPos <= seg.endOffset) {
          targetBlockIndex = i;
          const rendered = new DOMParser().parseFromString(markdownToVisualHtml(seg.raw), 'text/html');
          targetOffset = mapCaretTextOffset(seg.raw, rendered.body.textContent || '', sourceCaretPos - seg.startOffset);
          break;
        }
      }
    }

    saveCurrentToMap();

    if (requestedMode === 'markdown' && doc._visualSyncBlocked) {
      if (!forceDiscard) {
        _syncHeaderActions();
        _scheduleDocumentHistoryControls();
        return false;
      }
      discardVisualDraft(doc);
    }
    const currentContent = doc.content || '';
    const showSource = (caretInfo = null) => {
      if (!ta) return;
      if (ta.value !== currentContent) ta.value = currentContent;
      syncHighlighting(ta.value, 'markdown');
      updateLineNumbers(ta.value);
      if (caretInfo) {
        const segments = segmentMarkdownBlocks(ta.value);
        const seg = segments[Math.min(caretInfo.blockIndex, segments.length - 1)];
        if (seg) {
          const localOffset = mapCaretTextOffset(caretInfo.blockText ?? seg.raw, seg.raw, caretInfo.offsetInBlock || 0);
          const pos = Math.min(seg.endOffset, seg.startOffset + localOffset);
          try {
            ta.setSelectionRange(pos, pos);
          } catch (_) {}
        }
      }
      if (focus) {
        try { ta.focus(); } catch (_) {}
      }
    };

    if (requestedMode === 'visual') {
      if (wasPreview) _setMarkdownPreviewActive(false, { remember: true });
      const compat = assessMarkdownVisualSupport(currentContent);
      doc._visualCompatReasons = compat.supported ? [] : compat.reasons;
      const shown = compat.supported && _showMarkdownVisualEditor(doc, { focus, targetBlockIndex, targetOffset });
      if (!shown) {
        _hideMarkdownVisualEditor();
        renderMarkdownCompatBanner(doc._visualCompatReasons);
        showSource();
        if (notifyBlocked) {
          uiModule?.showToast?.(`Markdown mode preserved: ${doc._visualCompatReasons[0] || 'unsupported syntax'}`);
        }
        _syncHeaderActions();
        _scheduleDocumentHistoryControls();
        return false;
      }
      doc._markdownEditorMode = 'visual';
      if (persistPreference) setMarkdownModePreference('visual');
      _syncHeaderActions();
      _scheduleDocumentHistoryControls();
      return true;
    }

    // Record the choice first so leaving Preview does not re-mount the visual view.
    doc._markdownEditorMode = 'markdown';
    if (persistPreference) setMarkdownModePreference('markdown');
    if (wasPreview) _setMarkdownPreviewActive(false, { remember: true });
    _hideMarkdownVisualEditor();

    const compat = assessMarkdownVisualSupport(currentContent);
    doc._visualCompatReasons = compat.supported ? [] : compat.reasons;
    if (!compat.supported) renderMarkdownCompatBanner(compat.reasons);
    else hideMarkdownCompatBanner();
    showSource(visualCaretInfo);
    _syncHeaderActions();
    _scheduleDocumentHistoryControls();
    return true;
  }

export function bindHeaderActionsSecondary({ pane, divider, paneLifetime, isRight }) {
document.getElementById('doc-header-preview-btn').addEventListener('click', () => {
      const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
      if (lang === 'markdown') toggleMarkdownPreview();
      else if (lang === 'csv') toggleCsvPreview();
      else if (_isRenderLang(lang)) toggleHtmlPreview();
      else {
        // Runnable language — toggle output
        const outputPanel = document.getElementById('doc-run-output');
        if (outputPanel && outputPanel.style.display !== 'none') {
          outputPanel.style.display = 'none';
        } else {
          runDocument();
        }
      }
      _syncHeaderActions();
    });
document.getElementById('doc-md-view-toggle')?.addEventListener('click', (e) => {
      const opt = e.target.closest('.md-view-opt');
      if (!opt) return;
      const targetMode = opt.dataset.mdview === 'visual' ? 'visual' : 'markdown';
      _setMarkdownEditorMode(targetMode, { focus: true, persistPreference: true, notifyBlocked: true });
    });
document.getElementById('doc-md-preview')?.addEventListener('click', _handleMarkdownPreviewClickHint);
document.getElementById('doc-render-view-toggle')?.addEventListener('click', (e) => {
      const opt = e.target.closest('.md-view-opt');
      if (!opt) return;
      const wantRun = opt.dataset.renderview === 'run';
      const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
      if (lang === 'csv') {
        const csv = document.getElementById('doc-csv-preview');
        const isOn = csv && csv.style.display !== 'none';
        if (wantRun !== isOn) toggleCsvPreview();
      } else if (_isRenderLang(lang)) {
        const htmlPrev = document.getElementById('doc-html-preview');
        const isOn = htmlPrev && htmlPrev.style.display !== 'none';
        if (wantRun !== isOn) toggleHtmlPreview();
      } else if (_isDocxLang(lang)) {
        const docxPrev = document.getElementById('doc-docx-preview');
        const isOn = docxPrev && docxPrev.style.display !== 'none';
        if (wantRun !== isOn) toggleDocxPreview();
      } else if (_isRichTextLang(lang)) {
        const richPrev = document.getElementById('doc-md-preview');
        const isOn = richPrev && richPrev.style.display !== 'none';
        if (wantRun !== isOn) toggleRichTextPreview();
      } else {
        // Runnable language (python / js / ts / bash …) — clicking Run is
        // a one-shot execute; clicking Code dismisses the output pane.
        if (wantRun) {
          document.getElementById('doc-header-preview-btn')?.click();
        } else {
          const out = document.getElementById('doc-run-output');
          if (out) out.style.display = 'none';
        }
      }
      _syncHeaderActions();
    });
const fontBtn = document.getElementById('doc-fontsize-btn');
const editorWrap = document.getElementById('doc-editor-wrap');
const _fontSizes = ['s', 'm', 'l'];
const _iconSizes = [12, 14, 16];
let _fontIdx = parseInt(localStorage.getItem('odysseus-doc-fontsize') || '0', 10);
if (!(_fontIdx >= 0 && _fontIdx < 3)) _fontIdx = 0;
function _applyDocFont() {
      const richEmailBody = document.getElementById('doc-email-richbody');
      [editorWrap, richEmailBody].filter(Boolean).forEach(el => {
        el.classList.remove('doc-font-s', 'doc-font-m', 'doc-font-l');
        if (_fontSizes[_fontIdx] !== 's') el.classList.add('doc-font-' + _fontSizes[_fontIdx]);
      });
      if (fontBtn) {
        fontBtn.dataset.size = _fontSizes[_fontIdx];
        // Keep the original behaviour: the icon itself grows with the size.
        const svg = fontBtn.querySelector('svg');
        if (svg) { const sz = _iconSizes[_fontIdx]; svg.setAttribute('width', sz); svg.setAttribute('height', sz); }
        // Show only the active size letter (just S, or just M, or just L).
        fontBtn.querySelectorAll('.doc-fontsize-levels [data-sz]').forEach(el => {
          const active = el.dataset.sz === _fontSizes[_fontIdx];
          el.classList.toggle('active', active);
          el.style.display = active ? '' : 'none';
        });
      }
      localStorage.setItem('odysseus-doc-fontsize', _fontIdx);
    }
_applyDocFont();
if (fontBtn) fontBtn.addEventListener('click', () => {
      _fontIdx = (_fontIdx + 1) % 3;
      _applyDocFont();
      syncHighlighting();
      _scheduleSelRerender();
    });
const docUndoBtn = document.getElementById('doc-undo-btn');
if (docUndoBtn) docUndoBtn.addEventListener('click', async () => {
      const pdfPane = document.getElementById('doc-pdf-view');
      const pdfVisible = pdfPane && pdfPane.style.display !== 'none';
      if (pdfVisible && await _undoPdfPaneAction()) return;
      const rich = _emailRichbodyActive();
      if (rich) {
        rich.focus();
        document.execCommand('undo');
        _syncEmailRichbody(rich);
        _scheduleEmailRichbodySave();
        _syncDocumentHistoryControlsNow();
        _dismissDocKb();
        return;
      }
      const ta = document.getElementById('doc-editor-textarea');
      if (ta) {
        ta.focus();   // execCommand('undo') needs the textarea focused
        document.execCommand('undo');
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        _syncDocumentHistoryControlsNow();
        _dismissDocKb();   // then force the keyboard back down on touch
      }
    });
const docRedoBtn = document.getElementById('doc-redo-btn');
if (docRedoBtn) docRedoBtn.addEventListener('click', () => {
      const rich = _emailRichbodyActive();
      if (rich) {
        rich.focus();
        document.execCommand('redo');
        _syncEmailRichbody(rich);
        _scheduleEmailRichbodySave();
        _syncDocumentHistoryControlsNow();
        _dismissDocKb();
        return;
      }
      const ta = document.getElementById('doc-editor-textarea');
      if (!ta) return;
      ta.focus();
      document.execCommand('redo');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      _syncDocumentHistoryControlsNow();
      _dismissDocKb();
    });
const diffToggleBtn = document.getElementById('doc-diff-toggle-btn');
if (diffToggleBtn) {
      let _compareRequestSeq = 0;
      diffToggleBtn.addEventListener('click', async () => {
      const requestSeq = ++_compareRequestSeq;
      if (_diffModeActive) {
        exitDiffMode(true);
        return;
      }
      const docId = activeDocId;
      if (!docId) return;
      const doc = docs.get(docId);
      const ownerPane = pane || document.getElementById('doc-editor-pane');
      const ta = document.getElementById('doc-editor-textarea');
      if (!doc || !ownerPane || !ta) return;
      const generation = _documentNavigationGeneration;
      const revision = doc._editRevision || 0;
      const current = ta.value;
      const isCompareOwnerValid = () => (
        requestSeq === _compareRequestSeq &&
        isOpen &&
        _documentNavigationGeneration === generation &&
        activeDocId === docId &&
        docs.get(docId) === doc &&
        (doc._editRevision || 0) === revision &&
        ta.isConnected &&
        ta === document.getElementById('doc-editor-textarea') &&
        ta.value === current &&
        ownerPane.isConnected &&
        ownerPane === document.getElementById('doc-editor-pane') &&
        ownerPane.dataset.activeDocumentId === docId
      );

      // Fetch version history and compare against previous version
      let versions;
      try {
        const res = await fetch(`${API_BASE}/api/document/${docId}/versions`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        versions = await res.json();
      } catch (err) {
        if (!isCompareOwnerValid()) return;
        console.error('Failed to load version history:', err);
        if (uiModule) uiModule.showError('Failed to load version history');
        return;
      }

      if (!isCompareOwnerValid()) return;
      if (!Array.isArray(versions) || versions.length < 2) {
        if (uiModule) uiModule.showToast('No previous version to compare');
        return;
      }
      // versions are sorted desc — [0] is latest, [1] is previous
      const prevContent = versions[1].content || '';
      if (prevContent === current) {
        if (uiModule) uiModule.showToast('No changes from previous version');
        return;
      }
      try {
        enterDiffMode(prevContent, current);
      } catch (err) {
        console.error('Failed to initialize diff mode:', err);
        if (uiModule) uiModule.showError('Failed to compare versions');
        throw err;
      }
    });
    }
}
