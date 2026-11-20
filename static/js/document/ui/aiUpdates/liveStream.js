// frontend/src/features/documents/ui/aiUpdates/liveStream.js
/**
 * Live AI document streaming.
 * State writes use documentState setters.
 */

import {
  activeDocId,
  docs,
  isOpen,
  setActiveDocId,
  bumpNavigationGeneration,
  registerDocument,
} from '../../application/documentState.js';
import {
  openPanel,
  renderTabs,
  saveCurrentToMap,
  _syncDocIndicator,
  _syncHeaderActions,
} from '../../application/documentCommands.js';
import { _flushAutoSave } from '../../application/persistence.js';
import { _parseEmailHeader } from '../../domain/emailMessage.js';
import { sessionModule } from '../host.js';
import {
  syncHighlighting,
  attemptAutoDetect,
  updateLineNumbers,
} from '../editor/sourceEditor.js';
import {
  _showEmailFields,
  _hideEmailFields,
  _syncStreamingEmailFields,
} from '../email/composerFields.js';
import { _renderStreamingEmailBody } from '../email/emailBody.js';
import { _closeVersionPanel } from '../history/versionHistory.js';
import { _hideMarkdownVisualEditor } from '../richEditor/richBodyWiring.js';
import { _syncEditorPlaceholder } from '../status/documentStatus.js';
import { exitDiffMode, _diffModeActive } from '../diffViewer.js';
import {
  suspendSuggestions,
  _restoreSuggestionsFromStorage,
} from '../suggestions.js';
import { exitHtmlPreview } from '../previewModes.js';
import { _isFormBackedDoc, _releasePdfPane } from '../pdfTools.js';

let _streamDocId = null;

function _syncStreamDocChrome(doc) {
    if (!doc) return;
    const lang = (doc.language || 'markdown').toLowerCase();
    const isMd = lang === 'markdown';
    const isPdf = _isFormBackedDoc(doc.content || '');
    // Show the toolbar for any doc type that has a view toggle of its own
    // (markdown edit↔preview, or code↔run for renderable code types). The
    // `data-mode` attribute lets CSS hide markdown-only buttons (bold,
    // italic, headings, etc.) when we're in a code-mode doc.
    const renderable = ['svg', 'html', 'css', 'csv', 'python', 'javascript', 'typescript',
                        'json', 'xml', 'bash', 'sh', 'yaml', 'toml', 'sql'];
    const isCodeRenderable = renderable.includes(lang);
    const mt = document.getElementById('doc-md-toolbar');
    if (mt) {
      const showToolbar = isMd || isPdf || isCodeRenderable;
      mt.style.display = showToolbar ? '' : 'none';
      mt.dataset.mode = isMd ? 'md' : (isPdf ? 'pdf' : (isCodeRenderable ? 'code' : ''));
      if (showToolbar && mt._syncOverflow) requestAnimationFrame(mt._syncOverflow);
    }
    _syncHeaderActions();
  }

export function streamDocOpen(title, language) {
    bumpNavigationGeneration();
    _closeVersionPanel();
    // Discard any pending AI-edit diff before this stream changes the active
    // document. When the AI streams a NEW document while an unapproved diff is
    // open on the current one, streamDocOpen reassigns activeDocId below; if the
    // stale diff isn't cleared first, a later exitDiffMode applies the old doc's
    // content to the new one and overwrites it (issue #2467). activeDocId still
    // points at the previously-active doc here, so exitDiffMode(true) restores
    // and saves THAT doc — same guard handleDocUpdate/switchToDoc use.
    if (_diffModeActive) exitDiffMode(true);
    // If already streaming a doc, reuse it (don't create a second temp doc)
    if (_streamDocId && docs.has(_streamDocId)) {
      const existing = docs.get(_streamDocId);
      if (title) existing.title = title;
      if (language) existing.language = language;
      if (_streamDocId !== activeDocId) { renderTabs(); return; }
      // Update UI fields
      const titleInput = document.getElementById('doc-title-input');
      const langSelect = document.getElementById('doc-language-select');
      if (title && titleInput) titleInput.value = title;
      if (langSelect) langSelect.value = existing.language || 'markdown';
      if (language === 'email') {
        _showEmailFields(existing);
      }
      _syncStreamDocChrome(existing);
      renderTabs();
      return;
    }

    suspendSuggestions();
    const sessionId = sessionModule?.getCurrentSessionId() || '';
    // Reuse existing doc with same title in this session, or create a temp one
    let docId = null;
    if (title) {
      for (const [existingId, existingDoc] of docs) {
        if (existingDoc.title === title && existingDoc.sessionId === sessionId) {
          docId = existingId;
          break;
        }
      }
    }
    if (!docId) {
      docId = '_streaming_' + Date.now();
      registerDocument({
        id: docId,
        title: title || '',
        language: language || '',
        content: '',
        version: 1,
        sessionId,
      });
    }
    if (activeDocId && activeDocId !== docId) {
      saveCurrentToMap();
      _flushAutoSave(activeDocId);
      _releasePdfPane(activeDocId);
    }
    // Streamed content is shown live in the source view. Detach the visual
    // surface so it cannot keep showing, or be synchronized from, the previous
    // content; the finalizing doc_update re-renders the visual view.
    _hideMarkdownVisualEditor();
    _streamDocId = docId;
    setActiveDocId(docId);
    _syncDocIndicator();

    if (!isOpen) openPanel();
    // A previous SVG preview must not hide the next document's live code.
    exitHtmlPreview();

    // Force doc button visible
    const toggleBtn = document.getElementById('overflow-doc-btn');
    if (toggleBtn) {
      toggleBtn.style.display = '';
      toggleBtn.classList.remove('toolbar-collapsed');
      toggleBtn.classList.add('has-docs');
    }
    const docInd2 = document.getElementById('doc-indicator-btn');
    if (docInd2) docInd2.classList.add('visible');

    const titleInput = document.getElementById('doc-title-input');
    const langSelect = document.getElementById('doc-language-select');
    const badge = document.getElementById('doc-version-badge');
    if (titleInput) titleInput.value = title || '';
    if (langSelect) langSelect.value = language || 'markdown';
    if (badge) badge.textContent = 'v1';

    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea) {
      textarea.disabled = false;
      _syncEditorPlaceholder();
      textarea.value = '';
    }
    const pane = document.getElementById('doc-editor-pane');
    if (pane) pane.dataset.activeDocumentId = docId;
    // Show streaming indicator
    const indicator = document.getElementById('doc-stream-indicator');
    if (indicator) indicator.style.display = '';

    // Show email fields immediately when streaming an email doc so the user
    // doesn't have to refresh for the editor to flip into email mode.
    if (language === 'email') {
      const streamDoc = docs.get(_streamDocId);
      if (streamDoc) _showEmailFields(streamDoc);
    } else {
      _hideEmailFields();
    }

    syncHighlighting();
    _syncStreamDocChrome(docs.get(_streamDocId));
    _restoreSuggestionsFromStorage(_streamDocId);
    renderTabs();
  }

let _editAnimFrame = null;

function _animateDocEdit(textarea, newContent) {
    if (_editAnimFrame) cancelAnimationFrame(_editAnimFrame);
    const indicator = document.getElementById('doc-stream-indicator');
    if (indicator) indicator.style.display = '';
    const codeEl = document.getElementById('doc-editor-code');
    let cursor = document.getElementById('doc-stream-cursor');
    if (!cursor) {
      cursor = document.createElement('span');
      cursor.id = 'doc-stream-cursor';
      cursor.className = 'doc-stream-cursor';
      cursor.textContent = '\u258F';
    }

    const oldContent = textarea.value;

    // Find common prefix and suffix to isolate the changed region
    let prefixLen = 0;
    while (prefixLen < oldContent.length && prefixLen < newContent.length &&
           oldContent[prefixLen] === newContent[prefixLen]) prefixLen++;
    let suffixLen = 0;
    while (suffixLen < (oldContent.length - prefixLen) &&
           suffixLen < (newContent.length - prefixLen) &&
           oldContent[oldContent.length - 1 - suffixLen] === newContent[newContent.length - 1 - suffixLen]) suffixLen++;

    const deletedText = oldContent.slice(prefixLen, oldContent.length - suffixLen);
    const insertedText = newContent.slice(prefixLen, newContent.length - suffixLen);
    const suffix = oldContent.slice(oldContent.length - suffixLen);

    // Phase 1: delete characters one by one, then Phase 2: insert
    const deleteChunk = Math.max(2, Math.ceil(deletedText.length / 30));
    const insertChunk = Math.max(2, Math.ceil(insertedText.length / 30));
    let deletePos = deletedText.length;
    let insertPos = 0;
    let phase = deletedText.length > 0 ? 'delete' : 'insert';

    // Scroll to the edit region
    const linesBefore = oldContent.slice(0, prefixLen).split('\n').length;
    const lineH = parseFloat(getComputedStyle(textarea).lineHeight) || 20;
    textarea.scrollTop = Math.max(0, (linesBefore - 3) * lineH);

    function tick() {
      if (phase === 'delete') {
        deletePos = Math.max(0, deletePos - deleteChunk);
        const current = oldContent.slice(0, prefixLen) + deletedText.slice(0, deletePos) + suffix;
        textarea.value = current;
        if (codeEl) codeEl.textContent = current + '\n';
        if (codeEl && codeEl.parentElement) codeEl.parentElement.appendChild(cursor);
        updateLineNumbers(current);
        if (deletePos > 0) {
          _editAnimFrame = requestAnimationFrame(tick);
        } else {
          phase = 'insert';
          _editAnimFrame = requestAnimationFrame(tick);
        }
      } else {
        insertPos = Math.min(insertPos + insertChunk, insertedText.length);
        const current = newContent.slice(0, prefixLen + insertPos) + suffix;
        textarea.value = current;
        if (codeEl) codeEl.textContent = current + '\n';
        if (codeEl && codeEl.parentElement) codeEl.parentElement.appendChild(cursor);
        updateLineNumbers(current);
        if (insertPos < insertedText.length) {
          _editAnimFrame = requestAnimationFrame(tick);
        } else {
          // Done — set final content
          textarea.value = newContent;
          _editAnimFrame = null;
          if (indicator) indicator.style.display = 'none';
          if (cursor) cursor.remove();
          syncHighlighting();
        }
      }
    }
    _editAnimFrame = requestAnimationFrame(tick);
  }

let _streamHlDebounce = null;

export function streamDocDelta(content) {
    if (!_streamDocId) return;
    const doc = docs.get(_streamDocId);
    if (doc) doc.content = content;

    if (_streamDocId === activeDocId) {
      if ((doc?.language || '').toLowerCase() === 'email') {
        _syncStreamingEmailFields(doc);
        return;
      }
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        textarea.value = content;
        // Auto-scroll to bottom as content streams in
        textarea.scrollTop = textarea.scrollHeight;
      }
      // Update text and line numbers immediately, debounce expensive highlighting
      const codeEl = document.getElementById('doc-editor-code');
      if (codeEl) codeEl.textContent = content + '\n';
      updateLineNumbers(content);
      // Show blinking cursor at end of content
      let cursor = document.getElementById('doc-stream-cursor');
      if (!cursor) {
        cursor = document.createElement('span');
        cursor.id = 'doc-stream-cursor';
        cursor.className = 'doc-stream-cursor';
        cursor.textContent = '\u258F';
      }
      if (codeEl && codeEl.parentElement) codeEl.parentElement.appendChild(cursor);
      clearTimeout(_streamHlDebounce);
      _streamHlDebounce = setTimeout(syncHighlighting, 150);
    }
  }

export function streamDocFinalize() {
    const oldId = _streamDocId;
    if (!oldId) return null;
    const finishingDoc = oldId ? docs.get(oldId) : null;
    if (oldId === activeDocId && (finishingDoc?.language || '').toLowerCase() === 'email') {
      const fields = _parseEmailHeader(finishingDoc.content || '');
      _renderStreamingEmailBody(fields.body || '', { immediate: true });
    }
    _streamDocId = null;
    // Hide streaming indicator + cursor
    const indicator = document.getElementById('doc-stream-indicator');
    if (indicator) indicator.style.display = 'none';
    const cursor = document.getElementById('doc-stream-cursor');
    if (cursor) cursor.remove();
    // Final highlighting pass + auto-detect language
    clearTimeout(_streamHlDebounce);
    syncHighlighting();
    attemptAutoDetect();
    return oldId;
  }
