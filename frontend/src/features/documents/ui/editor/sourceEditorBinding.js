// ui/editor/sourceEditorBinding.js

import { activeDocId, docs, isPanelOpen } from '../../application/documentState.js';

import { _autoSaveDebounce, _cancelAutoSave, _emailRichbodySaveDebounce, _markDocumentDirty, _scheduleAutoSave, saveDocument } from '../../application/persistence.js';

import { _isMarkdownImageFile } from '../../domain/documentKinds.js';

import { _autoCreateFromInput, _creatingDoc, deleteActiveDocument } from '../documentFlows.js';

import { _uploadMarkdownImages } from './markdownImages.js';

import { _replaceRange, scheduleSourceEditorWork, syncGutterScroll, updateLineNumbers } from './sourceEditor.js';

import { _uploadComposeFiles } from '../email/attachments.js';

import { _persistEmailLocalDraftSoon } from '../email/localDraft.js';

import { _wireDiffViewer, _wireFindReplace, _wirePdfTools, _wireSuggestions } from '../moduleWiring.js';

import { applyMdFormat } from '../richEditor/formatting.js';

import { _selections, clearSelection, syncSelectionOverlay, updateSelectionState } from '../selection/selectionHighlights.js';

import { _activeDocLanguage, _openDocumentOutline, _refreshDocumentOutline, _scheduleDocumentHistoryControls, _scheduleDocumentStats } from '../status/documentStatus.js';

import { uiModule } from '../host.js';

import { closePanel, saveCurrentToMap, _syncHeaderActions } from '../../application/documentCommands.js';
import { _diffModeActive, exitDiffMode } from '../diffViewer.js';
import { _openFindBar, onFindTextareaScroll } from '../findReplace.js';
import { initRichTables } from '../richTables.js';
import { _hideMarkdownVisualEditor, _showMarkdownVisualEditor } from '../richEditor/richBodyWiring.js';
import {
  isMarkdownVisualModeActive,
  isSyncingVisualToTextarea,
  isVisualMarkdownDoc,
  renderMarkdownCompatBanner,
  resolveMarkdownEditorMode,
} from '../richEditor/markdownVisualSync.js';

const sourceTemplates = {
  reference: id => ({ text: `[Link text][${id}]\n\n[${id}]: https://example.org/`, block: true }),
  footnote: id => ({ text: `Text with a note.[^${id}]\n\n[^${id}]: Footnote text.`, block: true }),
  math: () => ({ text: '$x^2 + y^2 = r^2$', block: false }),
  equation: () => ({ text: '$$\nE = mc^2\n$$', block: true }),
  mermaid: () => ({ text: '```mermaid\nflowchart LR\n  Draft --> Review --> Publish\n```', block: true }),
  details: () => ({ text: '<details>\n<summary>Details</summary>\n<p>More information.</p>\n</details>', block: true }),
};

function insertSourceTemplate(ta, kind) {
  if (!Object.hasOwn(sourceTemplates, kind)) return;
  const source = ta.value;
  let id = kind;
  if (kind === 'reference' || kind === 'footnote') {
    for (let index = 2; source.toLowerCase().includes(`[${kind === 'footnote' ? '^' : ''}${id}]`); index++) {
      id = `${kind}-${index}`;
    }
  }
  const { text, block } = sourceTemplates[kind](id);
  let offset = ta.selectionEnd;
  if (!block) {
    _replaceRange(ta, offset, offset, text);
    return;
  }
  // Insert after the current line so an existing line stays intact.
  const newline = source.indexOf('\n', offset);
  offset = newline === -1 ? source.length : newline + 1;
  const before = source.slice(0, offset);
  const prefix = !before || before.endsWith('\n\n') ? '' : before.endsWith('\n') ? '\n' : '\n\n';
  const suffix = source[offset] === '\n' || offset === source.length ? '\n' : '\n\n';
  _replaceRange(ta, offset, offset, prefix + text + suffix);
}

export function bindSourceEditor({ pane, paneLifetime }) {
const ta = document.getElementById('doc-editor-textarea');
const pre = document.getElementById('doc-editor-highlight');
if (ta && pre) {
      const sourceInsert = document.getElementById('doc-source-insert');
      sourceInsert?.addEventListener('change', () => {
        const kind = sourceInsert.value;
        sourceInsert.value = '';
        const doc = docs.get(activeDocId);
        if (isVisualMarkdownDoc(doc) && !doc._visualSyncBlocked && ta.offsetParent && !ta.readOnly) {
          insertSourceTemplate(ta, kind);
        }
      }, { signal: paneLifetime.signal });

      ta.addEventListener('input', () => {
        if (isSyncingVisualToTextarea()) return;
        // Typing invalidates any pinned selection highlight
        if (_selections.length) clearSelection({ preserveCaret: true });
        // Auto-create a document if user types/pastes with no active doc.
        // Skip while a createDocument POST is in flight — otherwise typing
        // during the round-trip spawns a duplicate untitled doc.
        if (!activeDocId && !_creatingDoc && ta.value.trim()) {
          _autoCreateFromInput(ta.value);
          return;
        }
        // A programmatic source write (tools, insertions) while the visual editor
        // is shown: adopt the source as the document, re-render the visual view
        // from it, then run the normal source pipeline below.
        const activeDoc = activeDocId && docs.get(activeDocId);
        if (activeDoc && isVisualMarkdownDoc(activeDoc) && isMarkdownVisualModeActive()) {
          _hideMarkdownVisualEditor();
          saveCurrentToMap();
          if (resolveMarkdownEditorMode(activeDoc) !== 'visual' || !_showMarkdownVisualEditor(activeDoc)) {
            renderMarkdownCompatBanner(activeDoc._visualCompatReasons);
          }
          _syncHeaderActions();
        }
        _markDocumentDirty(activeDocId);
        saveCurrentToMap();
        // Sync text content immediately (prevents visual duplication from scroll desync)
        const codeEl = document.getElementById('doc-editor-code');
        if (codeEl && !codeEl.dataset.hasDiff) {
          codeEl.textContent = ta.value + '\n';
          codeEl.style.minHeight = ta.scrollHeight + 'px';
        }
        if (pre) {
          pre.scrollTop = ta.scrollTop;
          pre.scrollLeft = ta.scrollLeft;
        }
        updateLineNumbers(ta.value);
        // Debounce expensive operations (syntax highlighting, auto-detect, auto-save)
        scheduleSourceEditorWork(ta.value);
        _scheduleAutoSave(activeDocId, 2000);
        const doc = activeDocId && docs.get(activeDocId);
        if (doc && doc.language === 'email') _persistEmailLocalDraftSoon();
        _scheduleDocumentStats();
        _refreshDocumentOutline();
        _scheduleDocumentHistoryControls();
      });
      for (const eventName of ['select', 'keyup', 'mouseup']) {
        ta.addEventListener(eventName, _scheduleDocumentStats);
      }
      ta.addEventListener('focus', _scheduleDocumentHistoryControls);
      ta.addEventListener('paste', (e) => {
        if (_activeDocLanguage() !== 'markdown') return;
        const files = Array.from(e.clipboardData?.files || []).filter(_isMarkdownImageFile);
        if (!files.length) return;
        e.preventDefault();
        e.stopPropagation();
        _uploadMarkdownImages(files);
      });
      ta.addEventListener('dragover', (e) => {
        if (_activeDocLanguage() !== 'markdown') return;
        const items = Array.from(e.dataTransfer?.items || []);
        if (!items.some(item => item.kind === 'file' && /^image\//i.test(item.type || ''))) return;
        e.preventDefault();
      });
      ta.addEventListener('drop', (e) => {
        if (_activeDocLanguage() !== 'markdown') return;
        const files = Array.from(e.dataTransfer?.files || []).filter(_isMarkdownImageFile);
        if (!files.length) return;
        e.preventDefault();
        e.stopPropagation();
        _uploadMarkdownImages(files);
      });
      ta.addEventListener('scroll', () => {
        const code = document.getElementById('doc-editor-code');
        if (code) code.style.minHeight = ta.scrollHeight + 'px';
        pre.scrollTop = ta.scrollTop;
        pre.scrollLeft = ta.scrollLeft;
        syncGutterScroll();
        syncSelectionOverlay();
        // Re-position find rects so they track the textarea on scroll.
        onFindTextareaScroll();
      });
      ta.addEventListener('cut', () => {
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        if (start === end) return;
        setTimeout(() => {
          ta.focus();
          ta.selectionStart = ta.selectionEnd = Math.min(start, ta.value.length);
        }, 0);
      });
      // Tab key inserts a real tab; Escape clears selection
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          if (_diffModeActive) { exitDiffMode(true); return; }
          // First Esc clears any pinned selection without closing the
          // panel. Second Esc (no selection left) minimises the panel
          // to a dock chip. The previous all-in-one path made one Esc
          // press both clear and close, which was annoying because the
          // user lost their working doc by hitting Esc to dismiss a
          // mistaken highlight.
          if (_selections.length > 0) {
            clearSelection();
            e.preventDefault();
            e.stopPropagation();
            return;
          }
          // No pinned selection — Esc MINIMIZES the panel (tabs it
          // down to a dock chip) — same as the chevron button.
          e.preventDefault();
          e.stopPropagation();
          closePanel('down');
          return;
        }
        if (e.key === 'Tab') {
          e.preventDefault();
          document.execCommand('insertText', false, '\t');
        }
        // Markdown shortcuts (only when language is markdown)
        const lang = document.getElementById('doc-language-select')?.value;
        if (lang === 'markdown' && (e.ctrlKey || e.metaKey)) {
          const formatOptions = { bypassDuplicateGuard: !e.repeat };
          if (e.key === 'b') { e.preventDefault(); applyMdFormat('bold', formatOptions); }
          else if (e.key === 'i') { e.preventDefault(); applyMdFormat('italic', formatOptions); }
          else if (e.key === 'k') { e.preventDefault(); applyMdFormat('link', formatOptions); }
        }
      });

      // Find & Replace, Diff & Review, PDF tools, and Inline suggestions controllers
      _wireFindReplace();
      _wireDiffViewer();
      _wirePdfTools();
      _wireSuggestions();

      // Rich Text Tables controller
      initRichTables({
        showToast: (msg) => uiModule?.showToast?.(msg),
      });
      document.getElementById('doc-outline-toolbar-btn')?.addEventListener('click', _openDocumentOutline);

      // Intercept Ctrl+F on the editor pane
      pane.addEventListener('keydown', (e) => {
        const mod = e.ctrlKey || e.metaKey;
        const key = e.key.toLowerCase();
        if (mod && key === 's') {
          e.preventDefault();
          e.stopPropagation();
          clearTimeout(_autoSaveDebounce);
          _cancelAutoSave(activeDocId);
          clearTimeout(_emailRichbodySaveDebounce);
          const doc = activeDocId && docs.get(activeDocId);
          saveDocument({ silent: false, forceVersion: doc?.language !== 'email', docId: activeDocId });
          return;
        }
        if (mod && (key === 'f' || key === 'h')) {
          e.preventDefault();
          e.stopPropagation();
          _openFindBar(key === 'h');
        }
      });

      // Delete (or Backspace) over the doc PANEL itself (not while typing in
      // a field) deletes the active document. Matches the email-reader Delete
      // behavior so the keyboard shortcut is consistent across surfaces.
      document.addEventListener('keydown', (e) => {
        if (e.key !== 'Delete' && e.key !== 'Backspace') return;
        if (!isPanelOpen()) return;
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
        e.preventDefault();
        deleteActiveDocument();
      }, { signal: paneLifetime.signal });

      // Drag-and-drop file attachment for email compose docs. The whole pane
      // is the drop target; visual highlight while a drag is hovering. Files
      // dropped get uploaded via the same compose-upload endpoint as the
      // file picker.
      let _dragDepth = 0;
      const _isEmailDrag = (e) => {
        const doc = docs.get(activeDocId);
        if (!doc || doc.language !== 'email') return false;
        const dt = e.dataTransfer;
        if (!dt) return false;
        // Files-only — don't trigger on text drags etc.
        return dt.types && Array.from(dt.types).includes('Files');
      };
      pane.addEventListener('dragenter', (e) => {
        if (!_isEmailDrag(e)) return;
        e.preventDefault();
        _dragDepth++;
        pane.classList.add('email-dragover');
      });
      pane.addEventListener('dragover', (e) => {
        if (!_isEmailDrag(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
      });
      pane.addEventListener('dragleave', (e) => {
        if (!_isEmailDrag(e)) return;
        _dragDepth = Math.max(0, _dragDepth - 1);
        if (_dragDepth === 0) pane.classList.remove('email-dragover');
      });
      pane.addEventListener('drop', async (e) => {
        if (!_isEmailDrag(e)) return;
        e.preventDefault();
        _dragDepth = 0;
        pane.classList.remove('email-dragover');
        const files = e.dataTransfer.files;
        if (files && files.length) await _uploadComposeFiles(files);
      });

      // Track selection for AI-assisted editing
      ta.addEventListener('mouseup', () => {
        setTimeout(updateSelectionState, 50);
      });
      ta.addEventListener('keyup', (e) => {
        if (e.shiftKey) updateSelectionState();
      });
      // ESC clears any pinned selections — matches the badge's clear
      // button so users have a keyboard shortcut for the same action.
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && _selections.length > 0) {
          e.preventDefault();
          e.stopPropagation();
          clearSelection();
        }
      });
    }
}
