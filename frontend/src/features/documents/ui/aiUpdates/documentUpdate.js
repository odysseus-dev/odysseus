// frontend/src/features/documents/ui/aiUpdates/documentUpdate.js
/**
 * AI document update, line diff, and animated change presentation.
 * State writes use documentState setters.
 */

import { persistMarkdownDraft } from '../../application/documentSession.js';
import {
  activeDocId,
  docs,
  isOpen,
  setActiveDocId,
  bumpNavigationGeneration,
  registerDocument,
  removeDocument,
} from '../../application/documentState.js';
import {
  openPanel,
  renderTabs,
  _syncDocIndicator,
  _syncHeaderActions,
} from '../../application/documentCommands.js';
import { _isRichTextLang } from '../../domain/documentKinds.js';
import { _parseEmailHeader, _buildEmailContent } from '../../domain/emailMessage.js';
import { sessionModule } from '../host.js';
import {
  syncHighlighting,
  attemptAutoDetect,
  _refreshMarkdownPreviewIfVisible,
} from '../editor/sourceEditor.js';
import { _showEmailFields } from '../email/composerFields.js';
import { _splitEmailReplyQuote } from '../email/emailBody.js';
import { _clearEmailLocalDraft } from '../email/localDraft.js';
import { _showRichTextEditor, _showMarkdownVisualEditor, _hideMarkdownVisualEditor } from '../richEditor/richBodyWiring.js';
import { hideMarkdownCompatBanner, isMarkdownVisualModeActive, isVisualMarkdownDoc, renderMarkdownCompatBanner, resolveMarkdownEditorMode } from '../richEditor/markdownVisualSync.js';
import {
  diffLines,
  exitDiffMode,
  _diffModeActive,
} from '../diffViewer.js';
import {
  suspendSuggestions,
  _restoreSuggestionsFromStorage,
} from '../suggestions.js';
import {
  _setMarkdownPreviewActive,
  _isMarkdownPreviewVisible,
  toggleCsvPreview,
  toggleHtmlPreview,
  _themedRenderSrcdoc,
  _htmlPreviewActive,
} from '../previewModes.js';
import {
  _isFormBackedDoc,
  _pdfViewState,
  _refreshPdfPreviewIframe,
} from '../pdfTools.js';
import { streamDocFinalize } from './liveStream.js';
import { _closeVersionPanel } from '../history/versionHistory.js';
import { _syncEditorPlaceholder } from '../status/documentStatus.js';
import { autoTitleFromContent } from '../documentActions.js';

let _animationCancel = null;

function lineDiff(oldText, newText) {
    const raw = diffLines(oldText, newText, { maxCells: 500000 });
    if (!raw) return null;
    return raw.map(e => ({
      type: e.type === 'equal' ? 'same' : (e.type === 'insert' ? 'add' : 'del'),
      text: e.text,
    }));
  }

async function animateDocChange(oldText, newText) {
    if (_animationCancel) _animationCancel();

    const textarea = document.getElementById('doc-editor-textarea');
    const wrap = document.getElementById('doc-editor-wrap');
    if (!textarea) return false;
    if (oldText === newText) return true;

    const diff = lineDiff(oldText, newText);
    if (!diff) return false; // too large for diff

    // Count changes
    const delCount = diff.filter(d => d.type === 'del').length;
    const addCount = diff.filter(d => d.type === 'add').length;
    if (delCount + addCount === 0) return true;

    let cancelled = false;
    _animationCancel = () => { cancelled = true; };

    textarea.readOnly = true;
    if (wrap) wrap.classList.add('animating');

    try {
      // Build diff overlay HTML
      const overlay = document.createElement('div');
      overlay.className = 'doc-diff-overlay';

      // Stats bar
      const stats = document.createElement('div');
      stats.className = 'doc-diff-stats';
      stats.innerHTML = `<span class="diff-stat-del">\u2212${delCount}</span> <span class="diff-stat-add">+${addCount}</span>`;
      overlay.appendChild(stats);

      const content = document.createElement('div');
      content.className = 'doc-diff-content';

      // Render diff lines: show context around changes
      let inContext = false;
      let skipped = 0;
      diff.forEach((line, idx) => {
        if (line.type === 'same') {
          // Show 2 lines of context around changes
          const nearChange = diff.slice(Math.max(0, idx - 2), idx + 3).some(d => d.type !== 'same');
          if (nearChange) {
            if (skipped > 0) {
              const sep = document.createElement('div');
              sep.className = 'doc-diff-sep';
              sep.textContent = `\u22EF ${skipped} unchanged`;
              content.appendChild(sep);
              skipped = 0;
            }
            const row = document.createElement('div');
            row.className = 'doc-diff-line same';
            row.textContent = line.text || '\u00A0';
            content.appendChild(row);
          } else {
            skipped++;
          }
        } else {
          if (skipped > 0) {
            const sep = document.createElement('div');
            sep.className = 'doc-diff-sep';
            sep.textContent = `\u22EF ${skipped} unchanged`;
            content.appendChild(sep);
            skipped = 0;
          }
          const row = document.createElement('div');
          row.className = 'doc-diff-line ' + line.type;
          row.textContent = (line.type === 'del' ? '\u2212 ' : '+ ') + (line.text || '\u00A0');
          content.appendChild(row);
        }
      });

      overlay.appendChild(content);

      // Insert overlay over the textarea
      const editorArea = textarea.parentElement;
      if (editorArea) editorArea.appendChild(overlay);

      // Show diff for a moment, then fade to final content
      overlay.offsetHeight; // force reflow
      overlay.classList.add('visible');

      const DIFF_DISPLAY_MS = 2500;
      await new Promise(r => setTimeout(r, cancelled ? 0 : DIFF_DISPLAY_MS));

      if (!cancelled) {
        overlay.classList.remove('visible');
        overlay.classList.add('fading');
        textarea.value = newText;
        syncHighlighting();
        await new Promise(r => setTimeout(r, 400));
      }

      overlay.remove();

      if (!cancelled) {
        textarea.value = newText;
        syncHighlighting();
      }

      return !cancelled;
    } finally {
      textarea.readOnly = false;
      _animationCancel = null;
      if (wrap) wrap.classList.remove('animating');
    }
  }

export function handleDocUpdate(data) {
    suspendSuggestions();
    bumpNavigationGeneration();
    _closeVersionPanel();
    const streamingId = streamDocFinalize();
    // Discard any pending AI-edit diff before this update changes the active
    // document. The diff state (_diffModeActive/_diffOldContent/...) is a
    // module-global singleton bound to whatever doc was active when the diff
    // opened; if we switch documents without clearing it, a later tab switch or
    // Accept/Reject-All flushes the stale diff's content into the now-active
    // doc and silently overwrites it (issue #2467). activeDocId still points at
    // the previously-active doc here, so exitDiffMode(true) restores and saves
    // THAT doc before we reassign activeDocId below — mirroring switchToDoc()
    // and enterDiffMode().
    if (_diffModeActive) exitDiffMode(true, { persist: data.doc_id !== activeDocId });
    let docId = data.doc_id;
    let newContent = data.content || '';

    // Migrate streaming temp doc to real ID
    if (streamingId && streamingId.startsWith('_streaming_') && docs.has(streamingId)) {
      const tempDoc = docs.get(streamingId);
      removeDocument(streamingId);
      tempDoc.id = docId;
      tempDoc.version = data.version || 1;
      if (data.title) tempDoc.title = data.title;
      if (data.language) tempDoc.language = data.language;
      tempDoc.content = newContent;
      registerDocument(tempDoc);
      // Fix activeDocId reference
      if (activeDocId === streamingId) setActiveDocId(docId);
    }

    // Deduplicate: if a new doc has same title as existing doc in this session, update it instead
    if (!docs.has(docId)) {
      const curSession = sessionModule?.getCurrentSessionId() || '';
      let reuseId = null;
      const incomingFields = _parseEmailHeader(newContent || '');

      // Email subjects repeat constantly ("test", "Re: ..."). Match open
      // compose docs by source email identity first; never let a same-title
      // draft steal an update meant for a different open email.
      if (incomingFields.sourceUid) {
        const wantFolder = (incomingFields.sourceFolder || 'INBOX').trim();
        for (const [existingId, existingDoc] of docs) {
          const existingFields = _parseEmailHeader(existingDoc.content || '');
          if (
            String(existingFields.sourceUid || '') === String(incomingFields.sourceUid)
            && ((existingFields.sourceFolder || 'INBOX').trim() === wantFolder)
          ) {
            reuseId = existingId;
            break;
          }
        }
      }

      // First: match by title
      if (!reuseId && data.title) {
        for (const [existingId, existingDoc] of docs) {
          if (
            existingDoc.title === data.title
            && existingDoc.sessionId === curSession
            && (existingDoc.language || '').toLowerCase() !== 'email'
          ) {
            reuseId = existingId;
            break;
          }
        }
      }

      // Second: if no title match, reuse an empty untitled doc in this session
      if (!reuseId) {
        for (const [existingId, existingDoc] of docs) {
          if (existingDoc.sessionId === curSession &&
              (!existingDoc.title || existingDoc.title === 'Untitled') &&
              !existingDoc._visualSyncBlocked && (!existingDoc.content || existingDoc.content.trim() === '')) {
            reuseId = existingId;
            break;
          }
        }
      }

      if (reuseId) docId = reuseId;
    }

    // Capture old content before updating the map
    let textarea = document.getElementById('doc-editor-textarea');
    const oldContent = (docId === activeDocId && textarea) ? textarea.value : '';
    const isExistingDoc = docs.has(docId);
    if (isExistingDoc) {
      const existingDoc = docs.get(docId);
      const existingLang = ((existingDoc?.language || data.language || '') + '').toLowerCase();
      const oldFields = _parseEmailHeader(existingDoc?.content || '');
      const newFields = _parseEmailHeader(newContent || '');
      if (
        existingLang === 'email'
        && oldFields.body
        && newFields.body
        && (oldFields.inReplyTo || oldFields.sourceUid || newFields.inReplyTo || newFields.sourceUid)
      ) {
        const oldSplit = _splitEmailReplyQuote(oldFields.body);
        if (oldSplit.quote) {
          const newSplit = _splitEmailReplyQuote(newFields.body);
          const nextBody = `${(newSplit.body || newFields.body || '').trim()}\n\n${oldSplit.quote}`.trim();
          newContent = _buildEmailContent(
            newFields.to || oldFields.to,
            newFields.subject || oldFields.subject,
            newFields.inReplyTo || oldFields.inReplyTo,
            newFields.references || oldFields.references,
            nextBody,
            newFields.sourceUid || oldFields.sourceUid,
            newFields.sourceFolder || oldFields.sourceFolder,
            newFields.cc || oldFields.cc,
            newFields.bcc || oldFields.bcc,
          );
        }
      }
    }

    // Add or update in docs map
    if (isExistingDoc) {
      const doc = docs.get(docId);
      doc.content = newContent;
      doc._persistedContent = newContent;
      if (doc._visualSyncBlocked) doc._visualCompatReasons = ['document source changed while visual edits were pending'];
      persistMarkdownDraft(doc);
      doc.version = data.version || doc.version;
      if (data.title) doc.title = data.title;
      if (data.language) doc.language = data.language;
    } else {
      registerDocument({
        id: docId,
        title: data.title || '',
        language: data.language || '',
        content: newContent,
        _persistedContent: newContent,
        version: data.version || 1,
        sessionId: sessionModule?.getCurrentSessionId() || '',
      });
    }

    _syncDocIndicator();

    if (!isOpen) openPanel();
    textarea = document.getElementById('doc-editor-textarea');

    // Force doc button visible (overrides appearance settings & toolbar collapse)
    const toggleBtn = document.getElementById('overflow-doc-btn');
    if (toggleBtn) {
      toggleBtn.style.display = '';
      toggleBtn.classList.remove('toolbar-collapsed');
      toggleBtn.classList.add('has-docs');
    }
    const docInd = document.getElementById('doc-indicator-btn');
    if (docInd) docInd.classList.add('visible');

    // Switch to this doc's tab
    setActiveDocId(docId);
    const pane = document.getElementById('doc-editor-pane');
    if (pane) pane.dataset.activeDocumentId = docId;

    const badge = document.getElementById('doc-version-badge');
    const titleInput = document.getElementById('doc-title-input');
    const langSelect = document.getElementById('doc-language-select');

    // Re-enable editor if it was in empty state
    if (textarea) {
      textarea.disabled = false;
      _syncEditorPlaceholder();
    }
    if (badge) badge.textContent = `v${data.version || 1}`;
    if (titleInput) titleInput.value = docs.get(docId)?.title || '';
    // Auto-title from content after activating the tab and title input so UI stays synchronized
    if (!data.title) autoTitleFromContent(newContent, docId);
    // Set language from data, or fall back to what the doc already has (e.g. from streaming)
    const docLang = data.language || (docs.has(docId) && docs.get(docId).language) || '';
    if (langSelect) langSelect.value = docLang || 'markdown';
    const isEmailUpdate = (docLang || '').toLowerCase() === 'email';
    const isRichTextUpdate = _isRichTextLang(docLang);
    const markdownPreviewWasVisible = _isMarkdownPreviewVisible();
    const targetDoc = docs.get(docId);

    // The server has already saved doc_update. Show its current content now;
    // a temporary diff or typing animation makes a completed edit look pending.
    const isEdit = !isEmailUpdate && !isRichTextUpdate && isExistingDoc && oldContent && oldContent !== newContent && !streamingId;
    const updatedDocForRichText = isRichTextUpdate ? targetDoc : null;
    if (isRichTextUpdate && updatedDocForRichText) {
      _showRichTextEditor(updatedDocForRichText);
    } else if (isEdit && textarea) {
      // This event reports an already-saved edit. Do not turn it into a
      // pending accept/reject diff that can later restore the old content.
      if (markdownPreviewWasVisible && _refreshMarkdownPreviewIfVisible(docId, newContent)) {
        // Keep the visible preview synchronized with the saved document.
      } else {
        textarea.value = newContent;
        syncHighlighting();
      }
    } else {
      if (isEmailUpdate) {
        const updatedDocForEmail = docs.get(docId);
        if (updatedDocForEmail) {
          const updatedFields = _parseEmailHeader(updatedDocForEmail.content || '');
          _clearEmailLocalDraft(updatedFields.sourceUid, updatedFields.sourceFolder, updatedFields.inReplyTo);
          _setMarkdownPreviewActive(false, { remember: false });
          _showEmailFields(updatedDocForEmail, { applyLocalDraft: false });
        }
      } else {
        if (textarea) textarea.value = newContent;
        syncHighlighting();
        _refreshMarkdownPreviewIfVisible(docId, newContent);
      }
    }

    if (!isRichTextUpdate && !isEmailUpdate && targetDoc && isVisualMarkdownDoc(targetDoc) && !markdownPreviewWasVisible) {
      // Re-render from the saved content (including a finished stream); this
      // also resets the no-op baseline.
      if (resolveMarkdownEditorMode(targetDoc) === 'visual') {
        _showMarkdownVisualEditor(targetDoc);
      } else {
        _hideMarkdownVisualEditor();
        if (targetDoc._visualCompatReasons?.length) renderMarkdownCompatBanner(targetDoc._visualCompatReasons);
        else hideMarkdownCompatBanner();
      }
    }

    const ownsPane = () => pane?.isConnected && pane === document.getElementById('doc-editor-pane') && pane.dataset.activeDocumentId === docId && activeDocId === docId;

    // Flash the editor wrap to indicate content was updated
    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap) {
      wrap.classList.remove('doc-updated-flash');
      void wrap.offsetWidth; // force reflow
      wrap.classList.add('doc-updated-flash');
      wrap.addEventListener('animationend', () => wrap.classList.remove('doc-updated-flash'), { once: true });
    }

    // Auto-detect language for docs with no language set
    const updatedDoc = docs.get(docId);
    if (isEmailUpdate && updatedDoc) {
      updatedDoc.language = 'email';
      if (langSelect) langSelect.value = 'email';
      const updatedFields = _parseEmailHeader(updatedDoc.content || '');
      _clearEmailLocalDraft(updatedFields.sourceUid, updatedFields.sourceFolder, updatedFields.inReplyTo);
      _showEmailFields(updatedDoc, { applyLocalDraft: false });
    }
    if (updatedDoc && !updatedDoc.userSetLanguage && !updatedDoc.language) {
      setTimeout(() => { if (ownsPane()) attemptAutoDetect(); }, 100);
    }

    // Show/hide format-specific buttons and auto-toggle previews
    const finalLang = docLang || (updatedDoc && updatedDoc.language) || '';
    const mdToolbar = document.getElementById('doc-md-toolbar');
    // Toolbar shown for every doc type — items inside self-gate on language.
    if (mdToolbar) mdToolbar.style.display = '';
    // Auto-show table view for CSV after streaming
    const finalLangLower = (finalLang || '').toLowerCase();
    if (finalLangLower === 'svg') {
      requestAnimationFrame(() => {
        if (!ownsPane() || !isOpen) return;
        // Idempotent: doc_update and the tool-output fallback may both arrive.
        // SVG uses the existing sandboxed preview, never the server code runner.
        if (!_htmlPreviewActive) toggleHtmlPreview();
        else {
          const iframe = document.getElementById('doc-html-preview');
          if (iframe) iframe.srcdoc = _themedRenderSrcdoc(docs.get(docId)?.content || '', 'svg');
        }
      });
    } else if (finalLangLower === 'csv') {
      requestAnimationFrame(() => {
        if (!ownsPane()) return;
        const csvPreview = document.getElementById('doc-csv-preview');
        if (csvPreview && csvPreview.style.display === 'none') toggleCsvPreview();
      });
    } else if (streamingId && finalLangLower === 'markdown') {
      // A finished stream is shown rendered. The visual editor is already the
      // rendered (and editable) view; Preview remains for documents that stay
      // in source mode, where it renders diagrams and math.
      requestAnimationFrame(() => {
        if (!ownsPane() || isMarkdownVisualModeActive()) return;
        _setMarkdownPreviewActive(true, { remember: true });
      });
    }

    _restoreSuggestionsFromStorage(docId);
    renderTabs();

    // Refresh the header buttons (Run/Preview ▶, edit toggles) for the active
    // doc after ANY update — otherwise an AI-created html/svg/code doc wouldn't
    // show its ▶ Run button until the page was refreshed.
    if (docId === activeDocId) {
      _syncHeaderActions();
      // Form-backed (PDF) docs: re-fetch the rendered preview if it's showing.
      if (_isFormBackedDoc(newContent)) {
        const explicit = _pdfViewState.get(docId);
        if (explicit !== false) _refreshPdfPreviewIframe();
      }
    }
  }
