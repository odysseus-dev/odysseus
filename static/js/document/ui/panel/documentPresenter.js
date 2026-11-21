// ui/panel/documentPresenter.js

import { API_BASE, activeDocId, bumpNavigationGeneration, docs, markDeleted, removeDocument, setActiveDocId } from '../../application/documentState.js';
import { _hideLoadingOverlay, _syncDocIndicator, _syncHeaderActions, renderTabs } from '../../application/documentCommands.js';
import { _diffModeActive, exitDiffMode } from '../diffViewer.js';
import { _setDocxPreviewActive, _setMarkdownPreviewActive, exitHtmlPreview, setHtmlPreviewActive, toggleCsvPreview } from '../previewModes.js';
import { _isFormBackedDoc, _releasePdfPane, _syncPdfPaneToDoc } from '../pdfTools.js';
import { _restoreSuggestionsFromStorage } from '../suggestions.js';

import { clearMarkdownDraft, persistMarkdownDraft } from '../../application/documentSession.js';
import { _cancelAutoSave, _flushAutoSave } from '../../application/persistence.js';

import { _isDocxLang, _isRichTextLang } from '../../domain/documentKinds.js';

import { _buildEmailContent } from '../../domain/emailMessage.js';

import { _rememberActiveDoc } from '../documentFlows.js';

import { attemptAutoDetect, syncHighlighting } from '../editor/sourceEditor.js';

import { _hideEmailFields, _showEmailFields } from '../email/composerFields.js';

import { _persistEmailLocalDraftSoon } from '../email/localDraft.js';

import { _closeVersionPanel, _historyOwnsView, _versionHistory } from '../history/versionHistory.js';

import { _sanitizedRichTextHtml, _syncRichEmptyImport } from '../richEditor/richBodySync.js';

import { _showRichTextEditor, _showMarkdownVisualEditor, _hideMarkdownVisualEditor } from '../richEditor/richBodyWiring.js';
import {
  hideMarkdownCompatBanner,
  hideMarkdownSyncWarningBanner,
  isVisualMarkdownDoc,
  renderMarkdownCompatBanner,
  resolveMarkdownEditorMode,
  syncVisualMarkdownToDoc,
} from '../richEditor/markdownVisualSync.js';

import { _hideRichSelectionToolbar } from '../richEditor/selectionToolbar.js';

import { _hideRichSlashMenu } from '../richEditor/slashMenu.js';

import { clearSelection } from '../selection/selectionHighlights.js';

import { _closeDocumentOutline, _renderDocumentSaveState, _scheduleDocumentStats, _setDocumentHistoryControlState } from '../status/documentStatus.js';

export function switchToDoc(docId, { captureCurrent = true, preserveHistory = false } = {}) {
    if (!docs.has(docId)) return;
    bumpNavigationGeneration();
    if (!preserveHistory) _closeVersionPanel();
    _closeDocumentOutline();
    _hideRichSelectionToolbar();
    _hideRichSlashMenu();
    _hideLoadingOverlay();
    if (_diffModeActive) exitDiffMode(true);

    // These panes are shared by all tabs; never carry a previous document's
    // run or preview state into the document being opened.
    const staleRunOutput = document.getElementById('doc-run-output');
    if (staleRunOutput) {
      staleRunOutput.style.display = 'none';
      staleRunOutput.innerHTML = '';
    }
    const staleHtmlPreview = document.getElementById('doc-html-preview');
    if (staleHtmlPreview) {
      staleHtmlPreview.style.display = 'none';
      staleHtmlPreview.srcdoc = '';
    }
    const staleCsvPreview = document.getElementById('doc-csv-preview');
    if (staleCsvPreview) staleCsvPreview.style.display = 'none';
    setHtmlPreviewActive(false);

    // Save current doc state before switching only when this mounted pane was
    // actually populated from that document. A freshly reopened pane has an
    // empty textarea while activeDocId still points at the prior tab; saving
    // that shell would erase the cached document before we can render it.
    const mountedPane = document.getElementById('doc-editor-pane');
    if (captureCurrent && mountedPane?.dataset.activeDocumentId === activeDocId) saveCurrentToMap();

    // Auto-delete the doc we're leaving if it's completely empty, or flush pending saves
    const prevId = activeDocId;
    if (prevId && prevId !== docId && docs.has(prevId)) {
      const prev = docs.get(prevId);
      if (!prev._visualSyncBlocked && prev.language !== 'email' && !(prev.content || '').trim() && !(prev.title || '').trim()) {
        clearMarkdownDraft(prev);
        markDeleted(prevId);
        _cancelAutoSave(prevId);
        fetch(`${API_BASE}/api/document/${prevId}`, { method: 'DELETE' }).catch(() => {});
        removeDocument(prevId);
        _syncDocIndicator();
      } else {
        _flushAutoSave(prevId);
      }
    }

    if (prevId && prevId !== docId) _releasePdfPane(prevId);
    setActiveDocId(docId);
    if (mountedPane) mountedPane.dataset.activeDocumentId = docId;
    _rememberActiveDoc(docId);
    clearSelection();
    const doc = docs.get(docId);
    _renderDocumentSaveState();
    _setDocumentHistoryControlState(false, false);

    // Populate editor
    const titleInput = document.getElementById('doc-title-input');
    const textarea = document.getElementById('doc-editor-textarea');
    const langSelect = document.getElementById('doc-language-select');
    const badge = document.getElementById('doc-version-badge');

    if (titleInput) titleInput.value = doc.title || '';
    // For email docs, _showEmailFields will set textarea to body only (not raw header)
    if (textarea && doc.language !== 'email') textarea.value = doc.content || '';
    if (langSelect) langSelect.value = doc.language || 'markdown';
    if (badge) { const _v = doc.version || 1; badge.textContent = `v${_v}`; badge.style.display = _v > 1 ? '' : 'none'; }
    { const _v = doc.version || 1; const _dbtn = document.getElementById('doc-diff-toggle-btn'); if (_dbtn) _dbtn.style.display = _v > 1 ? '' : 'none'; }
    syncHighlighting();
    // Deferred re-sync: ensure minHeight is correct after browser layout
    requestAnimationFrame(() => {
      const ta2 = document.getElementById('doc-editor-textarea');
      const code2 = document.getElementById('doc-editor-code');
      const pre2 = document.getElementById('doc-editor-highlight');
      if (ta2 && code2 && pre2) {
        code2.style.minHeight = ta2.scrollHeight + 'px';
        pre2.scrollTop = ta2.scrollTop;
      }
    });

    // Auto-detect language for docs with no language set
    if (!doc.userSetLanguage && !doc.language) {
      setTimeout(attemptAutoDetect, 100);
    }

    // Show/hide markdown toolbar based on language. PDF-backed docs are
    // markdown under the hood, so the toolbar shows up for them too — and
    // gets the PDF-specific buttons (Text/Check/Sign/AI) revealed below.
    const isMd = (doc.language || 'markdown') === 'markdown';
    const isPdf = _isFormBackedDoc(doc.content || '');

    // For PDF-backed docs, re-run text extraction on the backend so the AI
    // can see the contents on the very next message. Idempotent + skipped
    // once per session per doc to avoid hammering the VL model on every
    // switch — track via a sentinel on the doc object.
    if (isPdf && !doc._ocrTriggered) {
      doc._ocrTriggered = true;
      (async () => {
        try {
          const r = await fetch(`${API_BASE}/api/document/${docId}/extract-pdf-text`, { method: 'POST', credentials: 'same-origin' });
          if (!r.ok) return;
          const j = await r.json().catch(() => ({}));
          if (j && j.extracted) {
            // Pull the fresh content into the local cache so subsequent AI
            // turns and the source view both reflect the extraction.
            const dr = await fetch(`${API_BASE}/api/document/${docId}`, { credentials: 'same-origin' });
            if (dr.ok) {
              const full = await dr.json();
              const cached = docs.get(docId);
              if (cached && full && full.current_content) {
                cached.content = full.current_content;
              }
            }
          }
        } catch (_) {}
      })();
    }
    const mdToolbar = document.getElementById('doc-md-toolbar');
    if (mdToolbar) {
      // Show for every doc type so users always have access to font-size /
      // diff toggle / language-specific controls. Items inside the toolbar
      // gate their own visibility on language (md edit/preview toggle, etc).
      mdToolbar.style.display = '';
      if (mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
    }
    // Toggle PDF-only toolbar group
    document.querySelectorAll('.md-toolbar-pdf-only').forEach(el => {
      el.style.display = isPdf ? '' : 'none';
    });
    // Font size does nothing for a PDF (annotations are placed, not styled) —
    // hide it on PDFs so the toolbar only shows what actually works.
    const _fsBtn = document.getElementById('doc-fontsize-btn');
    const isEmail = doc.language === 'email';
    const isRichText = _isRichTextLang(doc.language);
    const visualMdDoc = !isEmail && !isRichText && isVisualMarkdownDoc(doc);
    if (_fsBtn) _fsBtn.style.display = (isPdf || isRichText || visualMdDoc) ? 'none' : '';
    // Exit CSV preview when switching docs, or auto-show for CSV
    const isCsv = doc.language === 'csv';
    const csvPreview = document.getElementById('doc-csv-preview');
    if (!isCsv) {
      if (csvPreview) csvPreview.style.display = 'none';
    } else {
      // Auto-show table view for CSV documents
      requestAnimationFrame(() => toggleCsvPreview());
    }

    // Exit HTML preview on switch
    exitHtmlPreview();
    const docxPreview = document.getElementById('doc-docx-preview');
    if (docxPreview) { docxPreview.style.display = 'none'; docxPreview.replaceChildren(); }

    // Show/hide email fields. Markdown preview uses the same editor wrapper
    // as email source mode, so clear it before showing the rich email body;
    // otherwise the source wrapper can reappear over the composer.
    if (isEmail) {
      hideMarkdownCompatBanner();
      document.getElementById('doc-email-richbody')?.classList.remove('markdown-visual-mode');
      _setMarkdownPreviewActive(false, { remember: false });
      const forceHeaderFields = !!doc._skipLocalDraftOnce;
      const applyLocalDraft = forceHeaderFields ? false : true;
      doc._skipLocalDraftOnce = false;
      _showEmailFields(doc, { applyLocalDraft, forceHeaderFields });
    } else {
      _hideEmailFields();
      if (isRichText) {
        _setMarkdownPreviewActive(false, { remember: false });
        _showRichTextEditor(doc);
      } else if (visualMdDoc) {
        const wantsMarkdownPreview = doc._markdownPreviewActive === true;
        const resolvedMode = resolveMarkdownEditorMode(doc);
        if (wantsMarkdownPreview) {
          _hideMarkdownVisualEditor();
          hideMarkdownCompatBanner();
          hideMarkdownSyncWarningBanner(true);
          _setMarkdownPreviewActive(true, { remember: false });
        } else if (resolvedMode === 'visual') {
          _setMarkdownPreviewActive(false, { remember: false });
          _showMarkdownVisualEditor(doc);
        } else {
          _hideMarkdownVisualEditor();
          _setMarkdownPreviewActive(false, { remember: false });
          hideMarkdownSyncWarningBanner(true);
          if (doc._visualCompatReasons?.length) renderMarkdownCompatBanner(doc._visualCompatReasons);
          else hideMarkdownCompatBanner();
        }
      } else {
        _hideMarkdownVisualEditor();
        hideMarkdownCompatBanner();
        _setMarkdownPreviewActive(false, { remember: false });
        if (_isDocxLang(doc.language)) {
          requestAnimationFrame(() => _setDocxPreviewActive(doc._docxPreviewActive !== false, { remember: false }));
        }
      }
    }

    _syncRichEmptyImport();

    // Hide version panel on switch
    const vp = document.getElementById('doc-version-panel');
    if (vp && !preserveHistory) vp.classList.add('hidden');

    renderTabs();
    _syncHeaderActions();
    _scheduleDocumentStats();

    // Restore any persisted suggestions for this doc
    _restoreSuggestionsFromStorage(docId);

  }

function _visibleVisualMarkdownSurface() {
    const rich = document.getElementById('doc-email-richbody');
    return rich && rich.style.display !== 'none' && rich.classList.contains('markdown-visual-mode') ? rich : null;
  }

export function saveCurrentToMap() {
    // The active ID can still identify the previous document while a new pane
    // is mounting. Only its populated owner may supply a draft snapshot.
    const pane = document.getElementById('doc-editor-pane');
    if (!pane || pane.dataset.activeDocumentId !== activeDocId) return;
    if (_historyOwnsView(_versionHistory) && _versionHistory.previewing) return;
    if (!activeDocId || !docs.has(activeDocId)) return;
    const doc = docs.get(activeDocId);
    const textarea = document.getElementById('doc-editor-textarea');
    const titleInput = document.getElementById('doc-title-input');
    const langSelect = document.getElementById('doc-language-select');
    if (titleInput) doc.title = titleInput.value;
    if (langSelect) doc.language = langSelect.value;
    // For email docs, reconstruct full content with header
    if (_isRichTextLang(doc.language)) {
      const rich = document.getElementById('doc-email-richbody');
      if (rich && rich.style.display !== 'none') {
        doc.content = _sanitizedRichTextHtml(rich);
        if (textarea) textarea.value = doc.content;
      }
    } else if (isVisualMarkdownDoc(doc) && _visibleVisualMarkdownSurface()) {
      // Visual Markdown owns the content while shown; the source editor and
      // Preview fall through to the textarea branch below.
      syncVisualMarkdownToDoc(_visibleVisualMarkdownSurface(), { doc });
      if (doc._visualSyncBlocked) {
        doc._visualDraftHtml = _visibleVisualMarkdownSurface().innerHTML;
      }
    } else if (doc.language === 'email' && textarea) {
      const to = document.getElementById('doc-email-to')?.value || '';
      const cc = document.getElementById('doc-email-cc')?.value || '';
      const bcc = document.getElementById('doc-email-bcc')?.value || '';
      const subject = document.getElementById('doc-email-subject')?.value || '';
      const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value || '';
      const references = document.getElementById('doc-email-references')?.value || '';
      const sourceUid = document.getElementById('doc-email-source-uid')?.value || '';
      const sourceFolder = document.getElementById('doc-email-source-folder')?.value || '';
      // Persist the WYSIWYG body as HTML so reopening the draft keeps its
      // formatting (the textarea mirror is plain text). _emailBodyToHtml detects
      // the leading '<' on reload and restores it verbatim.
      const _rich = document.getElementById('doc-email-richbody');
      const _emailBody = (_rich && _rich.style.display !== 'none') ? _rich.innerHTML : textarea.value;
      doc.content = _buildEmailContent(to, subject, inReplyTo, references, _emailBody, sourceUid, sourceFolder, cc, bcc);
      _persistEmailLocalDraftSoon();
    } else if (textarea) {
      const pdfPane = document.getElementById('doc-pdf-view');
      const pdfVisibleForActive = Boolean(
        pdfPane && pdfPane.style.display !== 'none' && pdfPane.dataset.renderedDocId === activeDocId
      );
      if (pdfVisibleForActive) {
        _syncPdfPaneToDoc(activeDocId, { markDirty: false });
      } else if (!(textarea.value === '' && _isFormBackedDoc(doc.content))) {
        // Don't clobber a PDF/form-backed doc's source when the textarea is empty
        // (it's hidden behind the rendered PDF view, so its value isn't the source
        // of truth). Overwriting here dropped the pdf_form_source marker, so after
        // minimize->restore the doc came back blank.
        // Textareas normalize line endings. Keep the original bytes on a no-op
        // and maintain a CRLF Markdown document's convention on a Source edit.
        const previous = String(doc.content || '');
        if (textarea.value !== previous.replace(/\r\n?/g, '\n')) {
          const useCrlf = isVisualMarkdownDoc(doc) && previous.includes('\r\n') && !/(?<!\r)\n|\r(?!\n)/.test(previous);
          doc.content = useCrlf ? textarea.value.replace(/\n/g, '\r\n') : textarea.value;
        }
        persistMarkdownDraft(doc);
      }
    }
  }
