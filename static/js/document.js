// static/js/document.js
/**
 * Document editor module: multi-document tabbed panel alongside chat.
 * Supports multiple open documents with tab switching, per-doc state,
 * and theme-aware styling.
 */

import uiModule from './ui.js?v=20260916largetoolscroll1';

import sessionModule from './sessions.js';

import emojiPicker from './emojiPicker.js';

import { readEmailReplyResponse } from './emailReplyStream.js';

import markdownModule from './markdown.js';

import codeRunnerModule from './codeRunner.js?v=20260831richtexttools91';

import { langIcon } from './langIcons.js?v=20260831richtexttools91';

import spinnerModule from './spinner.js';

import { openLibrary, closeLibrary, isLibraryOpen, initLibrary } from './documentLibrary.js?v=20260911librarybulkdelete1';

import signatureModule from './signature.js';

import * as Modals from './modalManager.js';

import { bindMenuDismiss, dismissOrRemove, dismissTopMenu } from './escMenuStack.js';

import { topPortalZ } from './toolWindowZOrder.js';

import { getDocumentStats } from './documentStats.js?v=20260831richtexttools91';

import { parseMarkdownOutline } from './documentOutline.js?v=20260831richtexttools91';

import { attachColorPicker } from './colorPicker.js?v=20260910eyedropper1';

import {
  _openFindBar,
  applyFindMarks,
  initFindReplace,
  onFindTextareaScroll,
} from './document/ui/findReplace.js';

import {
  diffLines,
  enterDiffMode,
  exitDiffMode,
  isDiffModeActive,
  initDiffViewer,
  _diffModeActive,
} from './document/ui/diffViewer.js';

import {
  initExportDocx,
  showExportMenu,
  _importFromDevice,
} from './document/ui/exportDocx.js';

import {
  initPreviewModes,
  _setMarkdownPreviewActive,
  _isMarkdownPreviewVisible,
  toggleMarkdownPreview,
  toggleRichTextPreview,
  _setDocxPreviewActive,
  toggleDocxPreview,
  _convertDocxToRichText,
  toggleCsvPreview,
  _htmlPreviewActive,
  isHtmlPreviewActive,
  setHtmlPreviewActive,
  _themedRenderSrcdoc,
  toggleHtmlPreview,
  exitHtmlPreview,
} from './document/ui/previewModes.js';

import {
  _isFormBackedDoc,
  _downloadFilledPdf,
  _saveActiveDocBeforeExport,
  _undoPdfPaneAction,
  _setPdfDropMode,
  _renderPdfPane,
  _schedulePdfPaneSave,
  _cancelPdfPaneSave,
  _hasPendingPdfPaneSave,
  _syncPdfPaneToDoc,
  _cleanupPdfDocState,
  _releasePdfPane,
  _refreshPdfPreviewIframe,
  _setPdfViewActive,
  _pdfViewState,
  initPdfTools,
} from './document/ui/pdfTools.js';

import {
  initSuggestions,
  handleDocSuggestions,
  suspendSuggestions,
  _restoreSuggestionsFromStorage,
} from './document/ui/suggestions.js';

import {
  _richSelectionCell,
  _focusRichTableCell,
  _appendRichTableRow,
  _richTableCanMergeRight,
  _richTableCanSplitCell,
  _applyRichTableAction,
  initRichTables,
} from './document/ui/richTables.js';

import { AUTO_DETECT_DELAY, AUTO_DETECT_MIN_CHARS, AUTO_DETECT_MIN_RELEVANCE, AUTO_DETECT_SAMPLE_SIZE, HLJS_TO_DROPDOWN, _DOC_SAVE_STATE, _buildDiffSummary, _esc, _escHtml, _hasViewToggle, _isDocxLang, _isMarkdownImageFile, _isRenderLang, _isRichTextLang, _looksLikeMarkdown, _markdownImageAlt } from './document/domain/documentKinds.js';

import { _ATTACH_RE, _accountCanSend, _buildEmailContent, _buildEmailContentFromFields, _decodeBase64EmailWrapper, _emailQuoteMarkerMatch, _looksLikeWrappedEmailContent, _parseEmailHeader } from './document/domain/emailMessage.js';

import { _RICH_BLOCK_INPUT_RULES, _RICH_COLOR_PALETTE, _RICH_FONT_SIZE_PX, _RICH_SLASH_COMMANDS, _isRichInlineCodeMarker, _normalizeRichLinkUrl } from './document/domain/richTextRules.js';

import { API_BASE, isOpen, _deletedDocIds, _inFlightDeletes, _closingDocs, _documentNavigationGeneration, activeDocId, _lastSessionId, docs, _minimizedDocId, isPanelOpen, setApiBase, setActiveDocId, setLastSessionId, bumpNavigationGeneration, setPanelOpen, setMinimizedDocId, registerDocument, removeDocument, clearDocuments, markDeleted, beginDelete, endDelete, retainClosingDraft, releaseClosingDraft } from './document/application/documentState.js';

import { bindDocumentCommands, openPanel as cmdOpenPanel, closePanel as cmdClosePanel, switchToDoc as cmdSwitchToDoc, saveCurrentToMap as cmdSaveCurrentToMap, renderTabs as cmdRenderTabs, _syncDocIndicator as cmdSyncDocIndicator, _syncHeaderActions as cmdSyncHeaderActions, showEmptyState as cmdShowEmptyState, _showLoadingOverlay as cmdShowLoadingOverlay, _hideLoadingOverlay as cmdHideLoadingOverlay, _minimizeNotesForDocumentOpen as cmdMinimizeNotesForDocumentOpen, _ensureDocChipRegistered as cmdEnsureDocChipRegistered } from './document/application/documentCommands.js';

import { _autoSaveDebounce, _cancelAutoSave, _emailRichbodySaveDebounce, _flushAutoSave, _markDocumentDirty, _pendingAutoSaves, _saveClosingDraft, _scheduleAutoSave, _setDocumentSaveState, saveDocument, awaitWrites, runSerializedWrite, bindPersistenceHooks } from './document/application/persistence.js';

import { _docActiveKey, _docMinimizedKey, _docOpenKey, _forgetActiveDoc, _markDocVisibleState, addDocToTabs } from './document/application/documentSession.js';

import { _autoDetectDebounce, _autoTitleDebounce, _handleMarkdownPreviewClickHint, _hlDebounce, _lineNumberContentEl, _lineNumberObservedTextarea, _lineNumberResizeObserver, _lineNumberResizeRaf, _refreshMarkdownPreviewIfVisible, _replaceRange, attemptAutoDetect, syncGutterScroll, syncHighlighting, updateLineNumbers } from './document/ui/editor/sourceEditor.js';

import { _emailHtmlToPlainText, _emailPlainTextToHtml, _emailRichbodyActive, _richTextContentToHtml, _sanitizedRichTextHtml, _scheduleEmailRichbodySave, _syncEmailRichbody, _syncRichEmptyImport } from './document/ui/richEditor/richBodySync.js';

import { _activeRichImage, _applyRichLineSpacing, _cleanRichTextPasteHtml, _handleRichChecklistEnter, _handleRichHeadingEnter, _insertSmartRichPasteLink, _normalizeRichChecklists, _normalizeRichInlineCode, _richInlineCodeTypingArmed, _richSelectedSpacingBlockIndexes, _richSelectionChecklistItem, _richSelectionInlineCode, _richSpacingBlocks, _selectedRichImage, _setRichChecklistItemChecked, _toggleRichChecklist, _toggleRichInlineCode } from './document/ui/richEditor/richOperations.js';

import { _clearRichImageSelection, _deleteRichImage, _insertRichTextImages, _normalizeRichTextImages, _replaceRichImage, _replaceRichImageFigure, _richImageInsertRange, _selectRichImage } from './document/ui/richEditor/imageOps.js';

import { _applyRichImageAction, _editRichImageAlt, _editRichImageCaption, _removeRichLink, _restoreRichLinkRange, _richLinkAtRange, _wysiwygInsertLink } from './document/ui/richEditor/links.js';

import { _currentBlockTag, _normalizedRichMenuColor, _richMenuColorHex, _richSelectionHasFormatting, _saveFormatSelection, applyMdFormat } from './document/ui/richEditor/formatting.js';

import { _richDropdownCurrentActions, _showMdDropdown } from './document/ui/richEditor/formatMenus.js';

import { _applyRichBlockInputRule, _chooseRichSlashCommand, _hideRichSlashMenu, _renderRichSlashMenu, _richSlashActiveIndex, _richSlashContext, _richSlashMenu, _syncRichSlashMenu } from './document/ui/richEditor/slashMenu.js';

import { _showRichImageSourceMenu } from './document/ui/richEditor/imageMenus.js';

import { _followRichSelectionToolbarLayout, _hideRichSelectionToolbar, _richSelectionToolbar, _scheduleRichSelectionToolbar } from './document/ui/richEditor/selectionToolbar.js';

import { _showRichTextEditor, _wireEmailRichbody } from './document/ui/richEditor/richBodyWiring.js';
import { _clearCurrentEmailLocalDraft, _clearEmailLocalDraft, _emailFieldsWithLocalDraft, _emailLocalDraftDebounce, _persistEmailLocalDraftNow, _persistEmailLocalDraftSoon, _sanitizeOutgoingEmailBody } from './document/ui/email/localDraft.js';
import { _wireRecipientAutocomplete } from './document/ui/email/recipients.js';
import { _captureEmailBodyFocusState, _emailBodyToHtml, _emailReplyOwnText, _emailStreamAnimFrame, _emailStreamRenderedBody, _emailStreamTargetBody, _focusEmailBodyEnd, _renderStreamingEmailBody, _restoreEmailBodyFocusState, _setEmailBodyText, _splitEmailReplyQuote } from './document/ui/email/emailBody.js';
import { _showDocAiReplyChoice } from './document/ui/email/aiReply.js';
import { _discardEmail, _markUnreadAndClose, _saveDraft, _scheduleSend, _sendEmail } from './document/ui/email/sendWorkflow.js';
import { _handleAttachUpload, _renderComposeAttachments, _showComposeAttachMenu, _stageForwardedSourceAttachments, _uploadComposeFiles } from './document/ui/email/attachments.js';
import { _sendSignedReply } from './document/ui/email/signedReply.js';
import { _hideEmailFields, _maybeAutoCollapseEmailHeader, _setEmailHeaderCollapsed, _showEmailFields, _syncEmailHeaderSummary, _syncStreamingEmailFields } from './document/ui/email/composerFields.js';
import './document/ui/email/emailDocuments.js';

if (typeof globalThis !== 'undefined') {
  globalThis.markdownModule = markdownModule;
  globalThis.bindMenuDismiss = bindMenuDismiss;
  globalThis.topPortalZ = topPortalZ;
  globalThis.attachColorPicker = attachColorPicker;
  globalThis.uiModule = uiModule;

  // Rich tables bridge
  globalThis._richSelectionCell = _richSelectionCell;
  globalThis._richTableCanMergeRight = _richTableCanMergeRight;
  globalThis._richTableCanSplitCell = _richTableCanSplitCell;
  globalThis._focusRichTableCell = _focusRichTableCell;
  globalThis._applyRichTableAction = _applyRichTableAction;
  globalThis._appendRichTableRow = _appendRichTableRow;

  // Source editor bridge
  globalThis._replaceRange = _replaceRange;

  // Persistence bridge
  globalThis._markDocumentDirty = _markDocumentDirty;
  globalThis._scheduleAutoSave = _scheduleAutoSave;

  // Status & history bridge (Wave 3B pending extraction)
  globalThis._scheduleDocumentHistoryControls = _scheduleDocumentHistoryControls;
  globalThis._scheduleDocumentStats = _scheduleDocumentStats;
  globalThis._refreshDocumentOutline = _refreshDocumentOutline;

  // Selection & helper bridge
  globalThis.clearSelection = clearSelection;
  globalThis.autoTitleFromContent = autoTitleFromContent;
  globalThis.updateRichSelectionState = updateRichSelectionState;
  globalThis._uploadMarkdownImages = _uploadMarkdownImages;
  globalThis._persistEmailLocalDraftSoon = _persistEmailLocalDraftSoon;

  // Email workflow & documents bridge
  globalThis.openEmailDraft = (...args) => globalThis.openEmailDraft?.(...args);
  globalThis.replaceEmailReplyBody = (...args) => globalThis.replaceEmailReplyBody?.(...args);
  globalThis.ensureEmailDraftEnvelope = (...args) => globalThis.ensureEmailDraftEnvelope?.(...args);
  globalThis.getActiveEmailComposerContext = (...args) => globalThis.getActiveEmailComposerContext?.(...args);
  globalThis.findEmailDocId = (...args) => globalThis.findEmailDocId?.(...args);
  globalThis.focusEmailReplyBody = (...args) => globalThis.focusEmailReplyBody?.(...args);
  globalThis.generateEmailReply = (...args) => globalThis.generateEmailReply?.(...args);

  // Bridges for functions called by email modules from monolith
  globalThis.loadDocument = loadDocument;
  globalThis.injectFreshDoc = injectFreshDoc;
  globalThis.addDocToTabs = addDocToTabs;
  globalThis._autoCreateSession = _autoCreateSession;
  globalThis._closeWithoutDeleting = _closeWithoutDeleting;
  globalThis._cancelAutoSave = _cancelAutoSave;
  globalThis._visibleDocIdsForCurrentSession = _visibleDocIdsForCurrentSession;
  globalThis._emailRichbodyActive = _emailRichbodyActive;
  globalThis._emailPlainTextToHtml = _emailPlainTextToHtml;
  globalThis._emailHtmlToPlainText = _emailHtmlToPlainText;
  globalThis.syncHighlighting = syncHighlighting;
  globalThis.readEmailReplyResponse = readEmailReplyResponse;
  globalThis.dismissOrRemove = dismissOrRemove;
  globalThis._hideRichSelectionToolbar = _hideRichSelectionToolbar;
  globalThis._hideRichSlashMenu = _hideRichSlashMenu;
  globalThis._wireEmailRichbody = _wireEmailRichbody;
  Object.defineProperty(globalThis, '_selections', {
    get: () => _selections,
    set: (v) => { _selections = v; },
    configurable: true
  });
}

let _hasMountedPanel = false;

let _isEditingTabTitle = false;

let _disposePdfTools = null;

let _animationCancel = null;

{
    const s = document.createElement('style');
    s.id = 'doc-tab-menu-styles';
    s.textContent = `.doc-tab-menu-btn{background:none!important;border:none!important;outline:none!important;box-shadow:none!important;color:var(--fg);opacity:0.25;cursor:pointer;padding:2px 4px!important;height:auto!important;line-height:1;transition:opacity .15s;flex-shrink:0;-webkit-appearance:none;appearance:none}.doc-tab-menu-btn:focus,.doc-tab-menu-btn:active{outline:none!important;box-shadow:none!important;background:none!important}.doc-tab:hover .doc-tab-menu-btn{opacity:.5}.doc-tab-menu-btn:hover{opacity:1!important}.doc-tab-dropdown .dropdown-item-compact{padding:6px 8px;border-radius:6px;cursor:pointer;white-space:nowrap;border-bottom:none;display:flex;align-items:center;gap:10px;font-size:11px}.doc-tab-dropdown .dropdown-item-compact:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}.doc-tab-dropdown .dropdown-item-compact .dropdown-icon{width:14px;height:14px;display:flex;align-items:center;justify-content:center;flex-shrink:0;opacity:0.5}.doc-tab-dropdown .dropdown-divider{height:1px;margin:3px 0;background:color-mix(in srgb,var(--border) 40%,transparent)}.doc-tab-action-delete{color:var(--red,#e06c75)!important}.doc-tab-action-delete .dropdown-icon{opacity:0.7!important}`;
    document.head.appendChild(s);
  }

function _wireExportDocx() {
    initExportDocx({
      getApiBase: () => API_BASE,
      getActiveDocId: () => activeDocId,
      getActiveDoc: () => (activeDocId ? docs.get(activeDocId) : null),
      getDocs: () => docs,
      getUiModule: () => uiModule,
      getSessionModule: () => sessionModule,
      getLastSessionId: () => _lastSessionId,
      getMarkdownModule: () => markdownModule,
      openLibrary,
      addDocToTabs,
      switchToDoc,
      saveActiveDocBeforeExport: _saveActiveDocBeforeExport,
      downloadFilledPdf: _downloadFilledPdf,
      convertDocxToRichText: _convertDocxToRichText,
      dismissOrRemove,
      bindMenuDismiss,
      topPortalZ,
      richTextContentToHtml: _richTextContentToHtml,
    });
  }

_wireExportDocx();

function _wirePreviewModes() {
    initPreviewModes({
      getApiBase: () => API_BASE,
      getActiveDocId: () => activeDocId,
      getDocs: () => docs,
      getMarkdownModule: () => markdownModule,
      getUiModule: () => uiModule,
      syncHeaderActions: _syncHeaderActions,
      syncRichEmptyImport: _syncRichEmptyImport,
      saveDocument,
      switchToDoc,
      renderTabs,
      escHtml: _escHtml,
      isRichTextLang: _isRichTextLang,
      isDocxLang: _isDocxLang,
    });
  }

_wirePreviewModes();

function _wireDiffViewer() {
    initDiffViewer({
      saveDocument,
      syncHighlighting,
      updateLineNumbers,
      lineNumberContentEl: _lineNumberContentEl,
      showToast: (msg) => uiModule?.showToast?.(msg),
      onAcceptChanges: () => {
        const lang = ((docs.get(activeDocId)?.language) || document.getElementById('doc-language-select')?.value || '').toLowerCase();
        if (lang === 'markdown') {
          requestAnimationFrame(() => _setMarkdownPreviewActive(true, { remember: true }));
        }
      },
    });
  }

function _wireSuggestions() {
    initSuggestions({
      getActiveDocId: () => activeDocId,
      getDocs: () => docs,
      saveDocument,
      showError: (msg) => uiModule?.showError?.(msg),
      uiModule,
      isOpen: () => isOpen,
      openPanel,
      loadDocument,
      switchToDoc,
      saveCurrentToMap,
      isRichTextLang: _isRichTextLang,
      showRichTextEditor: _showRichTextEditor,
      showEmailFields: _showEmailFields,
      syncHighlighting,
      refreshMarkdownPreviewIfVisible: _refreshMarkdownPreviewIfVisible,
      isHtmlPreviewActive,
      isRenderLang: _isRenderLang,
      themedRenderSrcdoc: _themedRenderSrcdoc,
      updateLineNumbers,
      topPortalZ,
      isDiffModeActive: () => _diffModeActive,
      exitDiffMode,
      measurePos: _measurePos,
    });
  }

function _wirePdfTools() {
    _disposePdfTools = initPdfTools({
      getApiBase: () => API_BASE,
      getActiveDocId: () => activeDocId,
      getDoc: (id) => docs.get(id) || _closingDocs.get(id),
      saveDocument,
      markDocumentDirty: _markDocumentDirty,
      dismissDocKb: _dismissDocKb,
      getUiModule: () => uiModule,
      getSpinnerModule: () => spinnerModule,
      getSignatureModule: () => signatureModule,
    });
  }

function _wireFindReplace() {
    initFindReplace({
      isRichActive: _emailRichbodyActive,
      syncEmailRichbody: _syncEmailRichbody,
      scheduleEmailRichbodySave: _scheduleEmailRichbodySave,
      replaceRange: _replaceRange,
    });
  }

let _docStatsFrame = 0;

let _docOutlineMenu = null;

let _docOutlineClose = null;

let _docHistoryStateFrame = 0;

function _renderDocumentSaveState() {
    const status = document.getElementById('doc-footer-copy-btn');
    if (!status) return;
    const doc = activeDocId && (docs.get(activeDocId) || _closingDocs.get(activeDocId));
    const state = doc?._saveState || 'saved';
    const meta = _DOC_SAVE_STATE[state] || _DOC_SAVE_STATE.saved;
    status.dataset.saveState = state;
    status.title = `${meta.title} (Ctrl+S)`;
    status.setAttribute('aria-label', meta.label);
    const label = status.querySelector('.doc-save-button-label');
    if (label) label.textContent = meta.label;
  }

function _syncEditorPlaceholder() {
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
    const placeholders = {
      '': 'Start typing or paste text to create a document...',
      markdown: 'Write in Markdown...',
      richtext: 'Start writing...',
      email: 'Write your email...',
      csv: 'Enter CSV data...',
      json: 'Enter JSON...',
      yaml: 'Enter YAML...',
      toml: 'Enter TOML...',
      ini: 'Enter INI configuration...',
      html: 'Write HTML...',
      css: 'Write CSS...',
      xml: 'Write XML...',
      svg: 'Write SVG...',
      sql: 'Write SQL...',
    };
    textarea.placeholder = placeholders[lang] || `Write ${lang} code...`;
  }

function _setDocumentHistoryControlState(undoAvailable, redoAvailable) {
    const undo = document.getElementById('doc-undo-btn');
    const redo = document.getElementById('doc-redo-btn');
    if (undo) {
      undo.disabled = !undoAvailable;
      undo.setAttribute('aria-disabled', undoAvailable ? 'false' : 'true');
    }
    if (redo) {
      redo.disabled = !redoAvailable;
      redo.setAttribute('aria-disabled', redoAvailable ? 'false' : 'true');
    }
  }

function _syncDocumentHistoryControlsNow() {
    if (_docHistoryStateFrame) cancelAnimationFrame(_docHistoryStateFrame);
    _docHistoryStateFrame = 0;
    const pdfPane = document.getElementById('doc-pdf-view');
    if (pdfPane && pdfPane.style.display !== 'none') {
      _setDocumentHistoryControlState(true, false);
      return;
    }
    let canUndo = false;
    let canRedo = false;
    try {
      canUndo = document.queryCommandEnabled('undo');
      canRedo = document.queryCommandEnabled('redo');
    } catch (_) {}
    _setDocumentHistoryControlState(canUndo, canRedo);
  }

function _scheduleDocumentHistoryControls() {
    if (_docHistoryStateFrame) return;
    _docHistoryStateFrame = requestAnimationFrame(_syncDocumentHistoryControlsNow);
  }

function _normalizeRichStatsText(value) {
    return String(value ?? '')
      .replace(/\u00a0/gu, ' ')
      .replace(/\n{2,}/gu, '\n')
      .replace(/\n$/u, '');
  }

function _documentStatsSource() {
    const rich = document.getElementById('doc-email-richbody');
    if (rich && rich.style.display !== 'none') {
      const fullText = _normalizeRichStatsText(rich.innerText || rich.textContent || '');
      const selection = window.getSelection?.();
      if (selection && !selection.isCollapsed && selection.rangeCount) {
        const range = selection.getRangeAt(0);
        if (rich.contains(range.commonAncestorContainer)) {
          return { text: _normalizeRichStatsText(selection.toString()), selected: true };
        }
      }
      return { text: fullText, selected: false };
    }

    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return { text: '', selected: false };
    const start = Number(textarea.selectionStart) || 0;
    const end = Number(textarea.selectionEnd) || 0;
    if (end > start) return { text: textarea.value.slice(start, end), selected: true };
    return { text: textarea.value || '', selected: false };
  }

function _renderDocumentStats() {
    _docStatsFrame = 0;
    const root = document.getElementById('doc-stats');
    if (!root) return;
    const source = _documentStatsSource();
    const stats = getDocumentStats(source.text);
    const count = root.querySelector('#doc-stats-count');
    const unit = root.querySelector('#doc-stats-unit');
    const scope = root.querySelector('#doc-stats-scope');
    if (count) count.textContent = stats.words.toLocaleString();
    if (unit) unit.textContent = source.selected ? ' selected' : ` word${stats.words === 1 ? '' : 's'}`;
    if (scope) scope.textContent = source.selected ? 'Selection' : 'Document';
    const values = {
      'doc-stats-words': stats.words,
      'doc-stats-characters': stats.characters,
      'doc-stats-characters-no-spaces': stats.charactersNoSpaces,
      'doc-stats-lines': stats.lines,
      'doc-stats-reading': stats.readingMinutes ? `${stats.readingMinutes} min` : '0 min',
    };
    for (const [id, value] of Object.entries(values)) {
      const el = root.querySelector(`#${id}`);
      if (el) el.textContent = typeof value === 'number' ? value.toLocaleString() : value;
    }
    const button = root.querySelector('#doc-stats-btn');
    if (button) {
      button.classList.toggle('doc-stats-selected', source.selected);
      button.title = source.selected
        ? `${stats.words.toLocaleString()} words selected`
        : `${stats.words.toLocaleString()} words in document`;
      button.setAttribute('aria-label', button.title);
    }
  }

function _scheduleDocumentStats() {
    if (_docStatsFrame) cancelAnimationFrame(_docStatsFrame);
    _docStatsFrame = requestAnimationFrame(_renderDocumentStats);
  }

function _documentOutlineSupported() {
    const language = _activeDocLanguage();
    return language === 'markdown' || _isRichTextLang(language);
  }

function _documentOutlineEntries() {
    const doc = activeDocId && docs.get(activeDocId);
    if (!doc) return [];
    if (_isRichTextLang(doc.language)) {
      const rich = document.getElementById('doc-email-richbody');
      if (!rich || rich.style.display === 'none') return [];
      return Array.from(rich.querySelectorAll('h1, h2, h3, h4, h5, h6'))
        .map(node => ({
          level: Number(node.tagName.slice(1)) || 1,
          text: (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim(),
          node,
          mode: 'richtext',
        }))
        .filter(entry => entry.text);
    }
    if (doc.language !== 'markdown') return [];
    const textarea = document.getElementById('doc-editor-textarea');
    const entries = parseMarkdownOutline(textarea?.value || doc.content || '');
    const preview = document.getElementById('doc-md-preview');
    if (preview && preview.style.display !== 'none') {
      const nodes = Array.from(preview.querySelectorAll('h1, h2, h3, h4, h5, h6'));
      entries.forEach((entry, index) => {
        if (nodes[index]) {
          entry.node = nodes[index];
          entry.mode = 'preview';
        }
      });
    }
    return entries;
  }

function _flashOutlineTarget(element) {
    if (!element) return;
    element.classList.remove('doc-outline-target');
    void element.offsetWidth;
    element.classList.add('doc-outline-target');
    setTimeout(() => element.classList.remove('doc-outline-target'), 1100);
  }

function _jumpToDocumentOutlineEntry(entry) {
    if (entry.node?.isConnected) {
      entry.node.scrollIntoView({ behavior: 'smooth', block: 'center' });
      _flashOutlineTarget(entry.node);
      if (entry.mode === 'richtext') {
        const rich = document.getElementById('doc-email-richbody');
        const selection = window.getSelection?.();
        if (rich && selection) {
          const range = document.createRange();
          range.selectNodeContents(entry.node);
          range.collapse(true);
          selection.removeAllRanges();
          selection.addRange(range);
          try { rich.focus({ preventScroll: true }); } catch (_) { rich.focus(); }
        }
      }
      return;
    }

    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
    textarea.setSelectionRange(entry.start, entry.end);
    const lineHeight = parseFloat(getComputedStyle(textarea).lineHeight) || 18;
    const visibleLines = Math.max(3, Math.floor(textarea.clientHeight / lineHeight));
    textarea.scrollTop = Math.max(0, (entry.line - Math.floor(visibleLines / 3)) * lineHeight);
    const pre = document.getElementById('doc-editor-highlight');
    if (pre) pre.scrollTop = textarea.scrollTop;
    _flashOutlineTarget(document.getElementById('doc-editor-wrap'));
  }

function _renderDocumentOutlineMenu() {
    const menu = _docOutlineMenu;
    if (!menu) return;
    const entries = _documentOutlineEntries();
    const count = menu.querySelector('.doc-outline-count');
    const list = menu.querySelector('.doc-outline-list');
    if (count) count.textContent = entries.length ? String(entries.length) : '';
    if (!list) return;
    list.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement('div');
      empty.className = 'doc-outline-empty';
      empty.textContent = 'No headings';
      list.appendChild(empty);
      return;
    }
    entries.forEach(entry => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'doc-outline-item';
      item.style.paddingLeft = `${7 + Math.min(Math.max(0, entry.level - 1), 4) * 11}px`;
      item.title = entry.text;
      item.innerHTML = `<span class="doc-outline-level">H${entry.level}</span><span class="doc-outline-label"></span>`;
      item.querySelector('.doc-outline-label').textContent = entry.text;
      item.addEventListener('click', () => {
        _jumpToDocumentOutlineEntry(entry);
        _docOutlineClose?.();
      });
      list.appendChild(item);
    });
  }

function _refreshDocumentOutline() {
    const anchor = document.getElementById('doc-outline-toolbar-btn');
    const hasEntries = _documentOutlineSupported() && _documentOutlineEntries().length > 0;
    if (anchor) anchor.style.display = hasEntries ? '' : 'none';
    if (!hasEntries) _closeDocumentOutline();
    if (_docOutlineMenu) _renderDocumentOutlineMenu();
  }

function _closeDocumentOutline() {
    if (_docOutlineClose) _docOutlineClose();
  }

function _openDocumentOutline() {
    const anchor = document.getElementById('doc-outline-toolbar-btn');
    if (!anchor || !_documentOutlineSupported()) return;
    if (_docOutlineMenu) {
      _closeDocumentOutline();
      return;
    }

    const menu = document.createElement('div');
    menu.id = 'doc-outline-menu';
    menu.className = 'doc-outline-menu';
    menu.setAttribute('role', 'navigation');
    menu.setAttribute('aria-label', 'Document outline');
    menu.tabIndex = -1;
    menu.innerHTML = `
      <div class="doc-outline-header"><span>Outline</span><span class="doc-outline-count"></span></div>
      <div class="doc-outline-list"></div>
    `;
    document.body.appendChild(menu);
    _docOutlineMenu = menu;
    _renderDocumentOutlineMenu();

    const rect = anchor.getBoundingClientRect();
    const width = Math.min(320, Math.max(240, window.innerWidth - 16));
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    const height = Math.min(menu.scrollHeight, Math.max(160, window.innerHeight - 24));
    const below = rect.bottom + 6;
    const top = below + height <= window.innerHeight - 8
      ? below
      : Math.max(8, rect.top - height - 6);
    menu.style.width = `${width}px`;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.style.zIndex = String(topPortalZ());
    anchor.classList.add('is-active');
    anchor.setAttribute('aria-expanded', 'true');

    const close = bindMenuDismiss(menu, () => {
      menu.remove();
      window.removeEventListener('resize', close);
      window.visualViewport?.removeEventListener('resize', close);
      if (_docOutlineMenu === menu) _docOutlineMenu = null;
      if (_docOutlineClose === close) _docOutlineClose = null;
      anchor.classList.remove('is-active');
      anchor.setAttribute('aria-expanded', 'false');
    }, event => !menu.contains(event.target) && !anchor.contains(event.target));
    _docOutlineClose = close;
    window.addEventListener('resize', close);
    window.visualViewport?.addEventListener('resize', close);
    menu.addEventListener('keydown', event => {
      const items = Array.from(menu.querySelectorAll('.doc-outline-item'));
      if (!items.length) return;
      const current = items.indexOf(document.activeElement);
      let next = current;
      if (event.key === 'ArrowDown') next = Math.min(items.length - 1, current + 1);
      else if (event.key === 'ArrowUp') next = Math.max(0, current < 0 ? 0 : current - 1);
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = items.length - 1;
      else return;
      event.preventDefault();
      items[next]?.focus();
    });
    requestAnimationFrame(() => (menu.querySelector('.doc-outline-item') || menu).focus());
  }

function _rememberActiveDoc(docId) {
    const doc = docs.get(docId);
    const sessionId = doc?.sessionId || _lastSessionId || sessionModule?.getCurrentSessionId?.();
    if (sessionId && docId) localStorage.setItem(_docActiveKey(sessionId), docId);
  }

export function prepareDocumentOpen(sessionId) {
    if (sessionId) _markDocVisibleState(sessionId, 'open');
    if (Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel')) {
      setMinimizedDocId(null);
      Modals.unregister('doc-panel');
    }
  }

export function init(apiBase) {
    setApiBase(apiBase);
    _wirePreviewModes();
    _wireSuggestions();
    _wireDiffViewer();
    initLibrary({
      apiBase,
      esc: _esc,
      getDocs: () => docs,
      isOpen: () => isOpen,
      createDocument,
      newDocument,
      loadDocument,
      prepareDocumentOpen,
      switchToDoc,
      openPanel,
      addDocToTabs,
      syncDocIndicator: _syncDocIndicator,
    });
    _wireExportDocx();
    const sidebarNewDocBtn = document.getElementById('library-new-doc-btn');
    if (sidebarNewDocBtn && !sidebarNewDocBtn.dataset.docNewWired) {
      sidebarNewDocBtn.dataset.docNewWired = '1';
      sidebarNewDocBtn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        try {
          await newDocument();
        } catch (err) {
          console.error('Failed to create document from sidebar button:', err);
          if (uiModule) uiModule.showError('Failed to create document');
        }
      });
    }
    _maybeOpenDocFromHash();
    window.addEventListener('hashchange', _maybeOpenDocFromHash);
  }

function _syncDocIndicator() {
    const btn = document.getElementById('overflow-doc-btn');
    // Has docs = at least one non-empty doc in the map
    const hasDocs = docs.size > 0;
    if (btn) btn.classList.toggle('has-docs', hasDocs);
    // Show/hide the toolbar doc indicator when docs exist
    const indicator = document.getElementById('doc-indicator-btn');
    if (indicator) indicator.classList.toggle('visible', hasDocs);
    // Hide overflow menu item when indicator is shown outside
    if (btn) btn.style.display = hasDocs ? 'none' : '';
    // Update session list icon
    const sid = sessionModule?.getCurrentSessionId();
    if (sid && sessionModule.setSessionHasDocs) {
      sessionModule.setSessionHasDocs(sid, hasDocs);
    }
  }

function updateArrowVisibility(scrollArea, leftBtn, rightBtn) {
    const atLeft = scrollArea.scrollLeft <= 0;
    const atRight = scrollArea.scrollLeft + scrollArea.clientWidth >= scrollArea.scrollWidth - 1;
    leftBtn.style.display = atLeft ? 'none' : '';
    rightBtn.style.display = atRight ? 'none' : '';
    // Toggle the edge-mask classes so the fade gradient drops to flat on the
    // side that has nothing to scroll to. Without this the left/right fade
    // reads as a permanent shadow even when no arrow is showing.
    scrollArea.classList.toggle('is-at-left', atLeft);
    scrollArea.classList.toggle('is-at-right', atRight);
  }

function _wireSwipeDismiss(el) {
    if (!el) return;
    const DISMISS_THRESHOLD = 50;    // px
    const VELOCITY_THRESHOLD = 0.3;  // px/ms — fast flick dismisses below threshold
    const RUBBER_RESISTANCE = 0.35;  // resistance when dragging up past origin
    let startY = 0, startX = 0, lastY = 0, lastT = 0, velocity = 0;
    let dragging = false, cancelled = false;
    const getPane = () => document.getElementById('doc-editor-pane');
    let pane = null;

    el.addEventListener('touchstart', (e) => {
      if (window.innerWidth > 768 || e.touches.length !== 1) return;
      pane = getPane();
      if (!pane) return;
      const t = e.touches[0];
      startY = t.clientY; startX = t.clientX; lastY = startY; lastT = e.timeStamp;
      velocity = 0; dragging = false; cancelled = false;
    }, { passive: true });

    el.addEventListener('touchmove', (e) => {
      if (cancelled || !pane || window.innerWidth > 768) return;
      const t = e.touches[0];
      const dx = Math.abs(t.clientX - startX);
      const dy = t.clientY - startY;
      if (!dragging) {
        if (dx > 40 && dx > Math.abs(dy) * 2) { cancelled = true; return; } // horizontal → tab scroll
        if (Math.abs(dy) > 8) {
          dragging = true;
          // Clear the open animation — its `both` fill-mode otherwise pins
          // transform and overrides our inline finger-following transform.
          pane.style.animation = 'none';
          pane.style.transition = 'none';
          pane.style.willChange = 'transform';
        } else return;
      }
      const dt = e.timeStamp - lastT;
      if (dt > 0) velocity = velocity * 0.6 + ((t.clientY - lastY) / dt) * 0.4;
      lastY = t.clientY; lastT = e.timeStamp;
      e.preventDefault();
      pane.style.transform = dy > 0 ? `translateY(${dy}px)` : `translateY(${dy * RUBBER_RESISTANCE}px)`;
    }, { passive: false });

    const endSwipe = () => {
      if (!dragging || !pane) { pane = null; return; }
      const p = pane; pane = null; dragging = false;
      p.style.willChange = '';
      const dy = lastY - startY;
      const shouldDismiss = dy > DISMISS_THRESHOLD || (dy > 20 && velocity > VELOCITY_THRESHOLD);
      if (shouldDismiss) {
        closePanel('down');
      } else {
        p.style.transition = 'transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1.05)';
        p.style.transform = '';
        setTimeout(() => { p.style.transition = ''; }, 260);
      }
    };
    el.addEventListener('touchend', endSwipe, { passive: true });
    el.addEventListener('touchcancel', endSwipe, { passive: true });
  }

function renderTabs() {
    if (_isEditingTabTitle) return;  // Don't rebuild while editing a title
    const tabBar = document.getElementById('doc-tab-bar');
    if (!tabBar) return;

    const focused = tabBar.contains(document.activeElement) ? document.activeElement : null;
    const focusedDocId = focused?.closest('.doc-tab')?.dataset.docId;
    const focusedControl = ['doc-tab-select', 'doc-tab-version', 'doc-tab-close'].find(name => focused?.classList.contains(name));
    // Build tab HTML with scroll arrows
    // When doc panel is on right (default), + goes on far left; on left, + goes inside scroll area
    const paneEl = document.querySelector('.doc-editor-pane');
    const isDocLeft = paneEl && paneEl.classList.contains('doc-left');
    let html = '';
    html += '<button class="doc-tab-arrow doc-tab-arrow-left" id="doc-tab-left" title="Scroll left">&#x2039;</button>';
    html += '<div class="doc-tab-scroll" id="doc-tab-scroll" role="tablist" aria-label="Open documents">';
    const curSession = sessionModule?.getCurrentSessionId() || '';
    let _anyTab = false;
    for (const [id, doc] of docs) {
      // Only show tabs for the current session
      if (doc.sessionId && curSession && doc.sessionId !== curSession) continue;
      _anyTab = true;
      const isActive = id === activeDocId;
      const title = doc.title || 'Untitled';
      const shortTitle = title.length > 24 ? title.slice(0, 22) + '...' : title;
      const menuBtn = `<button class="doc-tab-menu-btn" data-doc-id="${id}" title="Document actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2.5"/><circle cx="12" cy="12" r="2.5"/><circle cx="12" cy="19" r="2.5"/></svg></button>`;
      const ver = doc.version || doc.version_count || 1;
      const verChip = `<button type="button" class="doc-tab-version" data-doc-id="${_esc(id)}" title="Version history" aria-label="Version history for ${_esc(title)}, version ${ver}" tabindex="${isActive ? 0 : -1}">v${ver}</button>`;
      // Language icon before the title — same family as the meta-line / picker
      // icons. Hidden via :empty CSS when the doc has no useful language.
      const lic = (doc.language && doc.language !== 'text')
        ? langIcon(doc.language, 12, { style: 'opacity:0.65;flex-shrink:0;color:currentColor;margin-right:4px;' })
        : '';
      const langChip = `<span class="doc-tab-lang">${lic}</span>`;
      html += `<div class="doc-tab${isActive ? ' active' : ''}" role="presentation" draggable="true" data-doc-id="${_esc(id)}" title="${_esc(title)}">
        ${verChip}<button type="button" class="doc-tab-select" role="tab" id="doc-tab-${encodeURIComponent(id)}" data-doc-id="${_esc(id)}" aria-selected="${isActive}" aria-controls="doc-editor-surface" tabindex="${isActive ? 0 : -1}" aria-label="${_esc(title)}">${langChip}<span class="doc-tab-title">${_esc(shortTitle)}</span></button>
        <button type="button" class="doc-tab-close" data-doc-id="${_esc(id)}" tabindex="${isActive ? 0 : -1}" aria-label="Close ${_esc(title)} (kept in Library)" title="Unlink from chat (kept in the Library)">&times;</button>
      </div>`;
    }
    // Empty state (panel open, no doc yet): show a ghost "Untitled" tab so it's
    // obvious you're in a fresh document rather than staring at a blank pane.
    if (!_anyTab && isOpen && !activeDocId) {
      html += `<div class="doc-tab active doc-tab-ghost" title="New document — start typing"><span class="doc-tab-title">Untitled</span></div>`;
    }
    html += `<button class="doc-tab-new" id="doc-tab-new-btn" title="New document"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>`;
    html += '</div>';
    html += '<button class="doc-tab-arrow doc-tab-arrow-right" id="doc-tab-right" title="Scroll right">&#x203A;</button>';
    tabBar.innerHTML = html;
    const surface = document.getElementById('doc-editor-surface');
    if (surface && activeDocId) surface.setAttribute('aria-labelledby', `doc-tab-${encodeURIComponent(activeDocId)}`);
    if (focused) {
      const retained = focusedControl && tabBar.querySelector(`.doc-tab[data-doc-id="${CSS.escape(focusedDocId || '')}"] .${focusedControl}`);
      const next = retained || tabBar.querySelector('.doc-tab.active .doc-tab-select') || document.getElementById('doc-tab-new-btn');
      next?.focus({ preventScroll: true });
    }
    if (tabBar._keyboardHandler) tabBar.removeEventListener('keydown', tabBar._keyboardHandler);
    tabBar._keyboardHandler = event => {
      const selector = event.target.closest('.doc-tab-select');
      if (!selector) return;
      const selectors = Array.from(tabBar.querySelectorAll('.doc-tab-select'));
      const index = selectors.indexOf(selector);
      let nextIndex;
      if (event.key === 'ArrowRight') nextIndex = (index + 1) % selectors.length;
      else if (event.key === 'ArrowLeft') nextIndex = (index + selectors.length - 1) % selectors.length;
      else if (event.key === 'Home') nextIndex = 0;
      else if (event.key === 'End') nextIndex = selectors.length - 1;
      else if (event.key === 'Delete') {
        event.preventDefault();
        event.stopPropagation();
        closeTab(selector.dataset.docId);
        return;
      } else return;
      event.preventDefault();
      event.stopPropagation();
      const id = selectors[nextIndex].dataset.docId;
      switchToDoc(id);
      document.getElementById(`doc-tab-${encodeURIComponent(id)}`)?.focus({ preventScroll: true });
    };
    tabBar.addEventListener('keydown', tabBar._keyboardHandler);

    // Wire scroll arrows
    const scrollArea = document.getElementById('doc-tab-scroll');
    const leftBtn = document.getElementById('doc-tab-left');
    const rightBtn = document.getElementById('doc-tab-right');
    if (scrollArea && leftBtn && rightBtn) {
      leftBtn.addEventListener('click', () => scrollArea.scrollBy({ left: -120, behavior: 'smooth' }));
      rightBtn.addEventListener('click', () => scrollArea.scrollBy({ left: 120, behavior: 'smooth' }));
      updateArrowVisibility(scrollArea, leftBtn, rightBtn);
      scrollArea.addEventListener('scroll', () => updateArrowVisibility(scrollArea, leftBtn, rightBtn));
    }

    // Mobile: the tab bar doubles as a drag zone — swipe down to dismiss.
    if (!tabBar._swipeWired) { tabBar._swipeWired = true; _wireSwipeDismiss(tabBar); }

    // Bring the clicked tab fully into view — the scroll area has an 18px
    // fade-mask at each edge plus the < / > arrow buttons; without this, the
    // rightmost tab stays partially under the fade so the user can't see its
    // close button or version chip.
    const _scrollTabIntoView = (tab, behavior = 'smooth') => {
      const sa = document.getElementById('doc-tab-scroll');
      if (!sa || !tab) return;
      const EDGE_PAD = 30;
      const tabLeft = tab.offsetLeft;
      const tabRight = tabLeft + tab.offsetWidth;
      const visLeft = sa.scrollLeft + EDGE_PAD;
      const visRight = sa.scrollLeft + sa.clientWidth - EDGE_PAD;
      if (tabRight > visRight) {
        sa.scrollTo({ left: sa.scrollLeft + tabRight - visRight, behavior });
      } else if (tabLeft < visLeft) {
        sa.scrollTo({ left: Math.max(0, sa.scrollLeft + tabLeft - visLeft), behavior });
      }
    };
    tabBar._resizeObserver?.disconnect();
    if (typeof ResizeObserver !== 'undefined' && scrollArea) {
      tabBar._resizeObserver = new ResizeObserver(() => {
        if (!scrollArea.isConnected) return;
        updateArrowVisibility(scrollArea, leftBtn, rightBtn);
        _scrollTabIntoView(tabBar.querySelector('.doc-tab.active'), 'auto');
      });
      tabBar._resizeObserver.observe(scrollArea);
    }
    // Wire tab clicks (delayed to allow dblclick on title)
    let _tabClickTimer = null;
    tabBar.querySelectorAll('.doc-tab').forEach(tab => {
      tab.addEventListener('click', (e) => {
        // Check if click was on or inside the close/play button
        if (e.target.closest('.doc-tab-close') || e.target.closest('.doc-tab-play') || e.target.closest('.doc-tab-menu-btn') || e.target.closest('.doc-tab-version')) return;
        if (_isEditingTabTitle) return;
        // If clicking the title span, delay to allow dblclick
        if (e.detail > 0 && e.target.classList.contains('doc-tab-title')) {
          clearTimeout(_tabClickTimer);
          _tabClickTimer = setTimeout(() => { switchToDoc(tab.dataset.docId); _scrollTabIntoView(tab); }, 250);
        } else {
          switchToDoc(tab.dataset.docId);
          _scrollTabIntoView(tab);
        }
      });
      tab.addEventListener('dblclick', (e) => {
        clearTimeout(_tabClickTimer);
        const titleSpan = tab.querySelector('.doc-tab-title');
        if (!titleSpan) return;
        e.stopPropagation();
        const docId = tab.dataset.docId;
        const doc = docs.get(docId);
        if (!doc) return;
        startTitleEdit(titleSpan, docId, doc);
      });
    });

    // Wire close buttons — use delegation from tab bar for reliability
    // Remove previous handler to prevent accumulation across renderTabs calls
    if (tabBar._closeHandler) tabBar.removeEventListener('click', tabBar._closeHandler);
    tabBar._closeHandler = (e) => {
      const verBtn = e.target.closest('.doc-tab-version');
      if (verBtn) {
        e.stopPropagation();
        const docId = verBtn.dataset.docId;
        if (docId) { if (docId !== activeDocId) switchToDoc(docId); toggleVersionHistory({ focus: e.detail === 0 }); }
        return;
      }
      const playBtn = e.target.closest('.doc-tab-play');
      if (playBtn) {
        e.stopPropagation();
        const docId = playBtn.dataset.docId;
        if (docId) {
          if (docId !== activeDocId) switchToDoc(docId);
          toggleHtmlPreview();
        }
        return;
      }
      const menuBtnEl = e.target.closest('.doc-tab-menu-btn');
      if (menuBtnEl) {
        e.stopPropagation();
        const docId = menuBtnEl.dataset.docId;
        if (docId) showDocTabMenu(menuBtnEl, docId);
        return;
      }
      const closeBtn = e.target.closest('.doc-tab-close');
      if (!closeBtn) return;
      e.stopPropagation();
      const docId = closeBtn.dataset.docId;
      if (docId) closeTab(docId);
    };
    tabBar.addEventListener('click', tabBar._closeHandler);

    // Wire drag-to-reorder
    initTabDragReorder(tabBar);

    // Wire new doc button
    const newBtn = document.getElementById('doc-tab-new-btn');
    if (newBtn) {
      newBtn.addEventListener('click', async () => {
        let sessionId = docs.get(activeDocId)?.sessionId
          || _lastSessionId
          || (sessionModule && sessionModule.getCurrentSessionId());
        if (!sessionId) {
          try {
            sessionId = await _autoCreateSession();
          } catch (e) {
            console.error('Failed to auto-create session for document:', e);
            return;
          }
        }
        createDocument(sessionId);
      });
    }

    // Scroll active tab into view after DOM is laid out
    requestAnimationFrame(() => {
      const at = document.getElementById('doc-tab-scroll')?.querySelector('.doc-tab.active');
      _scrollTabIntoView(at, 'auto');
    });
  }

function startTitleEdit(titleSpan, docId, doc) {
    if (_isEditingTabTitle) return;
    _isEditingTabTitle = true;

    const fullTitle = doc.title || '';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'doc-tab-title-input';
    input.value = fullTitle;

    const selector = titleSpan.closest('.doc-tab-select');
    if (selector) {
      selector.hidden = true;
      selector.after(input);
    } else titleSpan.replaceWith(input);
    input.focus();
    input.select();

    function commitEdit() {
      if (!_isEditingTabTitle) return;
      const newTitle = input.value.trim();
      _isEditingTabTitle = false;
      doc.title = newTitle;
      if (docId === activeDocId) {
        const titleInput = document.getElementById('doc-title-input');
        if (titleInput) titleInput.value = newTitle;
      }
      updateTitle(docId, newTitle);
      renderTabs();
    }

    function cancelEdit() {
      _isEditingTabTitle = false;
      renderTabs();
    }

    input.addEventListener('blur', commitEdit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        input.removeEventListener('blur', commitEdit);
        commitEdit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        input.removeEventListener('blur', commitEdit);
        cancelEdit();
      }
    });
  }

function initTabDragReorder(tabBar) {
    let dragId = null;

    tabBar.querySelectorAll('.doc-tab').forEach(tab => {
      tab.addEventListener('dragstart', (e) => {
        dragId = tab.dataset.docId;
        tab.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      });

      tab.addEventListener('dragend', () => {
        tab.classList.remove('dragging');
        dragId = null;
        tabBar.querySelectorAll('.doc-tab').forEach(t => t.classList.remove('drag-over'));
      });

      tab.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (tab.dataset.docId !== dragId) {
          tab.classList.add('drag-over');
        }
      });

      tab.addEventListener('dragleave', () => {
        tab.classList.remove('drag-over');
      });

      tab.addEventListener('drop', (e) => {
        e.preventDefault();
        tab.classList.remove('drag-over');
        const targetId = tab.dataset.docId;
        if (!dragId || dragId === targetId) return;

        // Reorder the docs Map: move dragId before targetId
        const entries = [...docs.entries()];
        const fromIdx = entries.findIndex(([k]) => k === dragId);
        const toIdx = entries.findIndex(([k]) => k === targetId);
        if (fromIdx === -1 || toIdx === -1) return;

        const [moved] = entries.splice(fromIdx, 1);
        entries.splice(toIdx, 0, moved);

        clearDocuments();
        for (const [k, v] of entries) registerDocument(v);

        renderTabs();
      });
    });
  }

function showEmptyState() {
    setActiveDocId(null);
    _setDocumentHistoryControlState(false, false);
    const textarea = document.getElementById('doc-editor-textarea');
    const langSelect = document.getElementById('doc-language-select');
    const badge = document.getElementById('doc-version-badge');

    if (textarea) textarea.value = '';
    _syncEditorPlaceholder();
    if (textarea) textarea.disabled = false;
    if (langSelect) langSelect.value = '';
    if (badge) badge.textContent = '';
    _hideLoadingOverlay();
    syncHighlighting();
    renderTabs();
  }

let _loadingSpinner = null;

function _showLoadingOverlay() {
    const wrap = document.getElementById('doc-editor-wrap');
    if (!wrap) return;
    let overlay = wrap.querySelector('.doc-loading-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'doc-loading-overlay';
      wrap.appendChild(overlay);
    }
    overlay.innerHTML = '';
    overlay.style.display = '';
    _loadingSpinner = spinnerModule.create('', 'clean', 'whirlpool');
    const el = _loadingSpinner.createElement();
    overlay.appendChild(el);
    _loadingSpinner.start();
  }

function _hideLoadingOverlay() {
    if (_loadingSpinner) { _loadingSpinner.destroy(); _loadingSpinner = null; }
    const overlay = document.querySelector('.doc-loading-overlay');
    if (overlay) overlay.style.display = 'none';
  }

function _dismissDocKb() {
    if (!(('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0)) return;
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

function _syncHeaderActions() {
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
          delete pdfPane.dataset.renderedDocId;
          pdfPane.style.display = 'none';
          pdfPane.innerHTML = '';
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

    let show = false;
    actionBtn.classList.remove('active');

    // The markdown Edit/Preview toggle is a two-icon switch; other modes use
    // the single dynamic preview button.
    const mdToggle = document.getElementById('doc-md-view-toggle');
    if (mdToggle) mdToggle.style.display = (lang === 'markdown') ? 'inline-flex' : 'none';
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
      show = false;
      if (mdToggle) {
        mdToggle.querySelector('[data-mdview="edit"]')?.classList.toggle('active', !_mdActive);
        mdToggle.querySelector('[data-mdview="preview"]')?.classList.toggle('active', _mdActive);
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
    if (_hasViewToggle(lang)) show = false;
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
      el.style.display = _isRichTextLang(lang) ? '' : 'none';
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
      el.style.display = (lang === 'email' || richMode) ? '' : 'none';
    });
    const alignButton = document.querySelector('.md-toolbar-align-control[data-dd="align"]');
    if (alignButton) alignButton.style.display = (lang === 'email' || richMode) ? '' : 'none';
    _syncDocumentToolbarSeparators(document.getElementById('md-toolbar-items'));
    const fsBtn = document.getElementById('doc-fontsize-btn');
    if (fsBtn) {
      const doc = activeDocId && docs.get(activeDocId);
      const isPdfDoc = !!(doc && _isFormBackedDoc(doc.content || ''));
      fsBtn.style.display = (isPdfDoc || richMode || (lang === 'markdown' && _mdActive)) ? 'none' : '';
    }
    const redoBtn = document.getElementById('doc-redo-btn');
    if (redoBtn) {
      const doc = activeDocId && docs.get(activeDocId);
      redoBtn.style.display = (doc && _isFormBackedDoc(doc.content || '')) ? 'none' : '';
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
    }
    const stats = document.getElementById('doc-stats');
    if (stats) stats.style.display = (_hasViewToggle(lang) && lang !== 'csv') ? 'none' : '';
    if (_mdPreview) _mdPreview.classList.toggle('md-preview-active', lang === 'markdown' && !!_mdActive);
    if (mdToolbar && mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);

    // Now that the contextual buttons' visibility is settled, collapse the bar
    // if it ended up empty (the common plain-doc-on-mobile case).
    _syncHeaderBarVisibility();
  }

function _activeDocLanguage() {
    const doc = activeDocId && docs.get(activeDocId);
    return ((doc && doc.language) || document.getElementById('doc-language-select')?.value || '').toLowerCase();
  }

function _scheduleMarkdownImageAutosave(ta) {
    updateLineNumbers(ta.value);
    const codeEl = document.getElementById('doc-editor-code');
    if (codeEl && !codeEl.dataset.hasDiff) {
      codeEl.textContent = ta.value + '\n';
      codeEl.style.minHeight = ta.scrollHeight + 'px';
    }
    clearTimeout(_hlDebounce);
    _hlDebounce = setTimeout(syncHighlighting, 80);
    clearTimeout(_autoTitleDebounce);
    _autoTitleDebounce = setTimeout(() => autoTitleFromContent(ta.value), 600);
    _scheduleAutoSave(activeDocId, 800);
  }

function _insertMarkdownImages(uploadedFiles) {
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    const files = Array.isArray(uploadedFiles) ? uploadedFiles : [];
    if (!files.length) return;

    const start = ta.selectionStart || 0;
    const end = ta.selectionEnd || start;
    const before = ta.value.slice(0, start);
    const after = ta.value.slice(end);
    const lines = files.map(file => {
      const id = encodeURIComponent(file.id || file.file_id || '');
      const alt = _markdownImageAlt(file.name || file.filename);
      return id ? `![${alt}](/api/upload/${id})` : '';
    }).filter(Boolean);
    if (!lines.length) return;

    const prefix = before && !before.endsWith('\n') ? '\n' : '';
    const suffix = after && !after.startsWith('\n') ? '\n' : '';
    const insert = `${prefix}${lines.join('\n\n')}${suffix}`;
    _replaceRange(ta, start, end, insert);
    const caret = start + insert.length;
    ta.selectionStart = caret;
    ta.selectionEnd = caret;
    ta.focus();
    _scheduleMarkdownImageAutosave(ta);
    _refreshMarkdownPreviewIfVisible(activeDocId, ta.value);
  }

async function _uploadMarkdownImages(files) {
    const images = Array.from(files || []).filter(_isMarkdownImageFile);
    if (!images.length) {
      if (uiModule) uiModule.showError('Choose an image file');
      return;
    }
    const language = _activeDocLanguage();
    const emailRichBody = language === 'email' && _emailRichbodyActive();
    if (language !== 'markdown' && !_isRichTextLang(language) && !emailRichBody) {
      if (uiModule) uiModule.showError('Switch the document to Markdown or Rich Text before inserting images');
      return;
    }

    const fd = new FormData();
    images.forEach(file => fd.append('files', file));
    try {
      const res = await fetch(`${API_BASE}/api/upload`, {
        method: 'POST',
        credentials: 'same-origin',
        body: fd,
      });
      let data = null;
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`);
      const uploaded = Array.isArray(data?.files) ? data.files : [];
      if (!uploaded.length) throw new Error('No uploaded files returned');
      if (_isRichTextLang(language) || emailRichBody) _insertRichTextImages(uploaded);
      else _insertMarkdownImages(uploaded);
      if (uiModule) uiModule.showToast(images.length === 1 ? 'Image inserted' : 'Images inserted');
    } catch (err) {
      console.error('Failed to insert markdown image:', err);
      if (uiModule) uiModule.showError('Failed to insert image');
    }
  }

async function _handleMarkdownImageUpload(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    await _uploadMarkdownImages(files);
  }

function _visibleDocIdsForCurrentSession() {
    const curSession = sessionModule?.getCurrentSessionId() || '';
    const ids = [];
    for (const [id, doc] of docs) {
      if (doc.sessionId && curSession && doc.sessionId !== curSession) continue;
      ids.push(id);
    }
    return ids;
  }

function _closeWithoutDeleting(deleteDoc = false) {
    if (!activeDocId) return;
    const closingId = activeDocId;
    _cancelAutoSave(closingId);
    _cancelPdfPaneSave(closingId);
    if (deleteDoc) {
      markDeleted(closingId);
      _cleanupPdfDocState(closingId);
      fetch(`${API_BASE}/api/document/${closingId}`, { method: 'DELETE' }).catch(() => {});
    }
    // Save the current state to the doc first so it persists in the library
    saveCurrentToMap();
    if (!deleteDoc) {
      const doc = docs.get(closingId);
      if (doc) {
        _saveClosingDraft(closingId, doc);
      }
    }
    removeDocument(closingId);
    const remaining = Array.from(docs.keys());
    if (remaining.length > 0) {
      switchToDoc(remaining[0]);
    } else {
      closePanel();
    }
    renderTabs();
  }

function switchToDoc(docId, { captureCurrent = true, preserveHistory = false } = {}) {
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
      if (prev.language !== 'email' && !(prev.content || '').trim() && !(prev.title || '').trim()) {
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
    if (_fsBtn) _fsBtn.style.display = (isPdf || _isRichTextLang(doc.language)) ? 'none' : '';
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
    const isEmail = doc.language === 'email';
    const isRichText = _isRichTextLang(doc.language);
    if (isEmail) {
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
      } else {
        const wantsMarkdownPreview = !isPdf && (doc.language || 'markdown') === 'markdown' && doc._markdownPreviewActive === true;
        _setMarkdownPreviewActive(wantsMarkdownPreview, { remember: false });
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

function _detachDocFromSession(docId, { toast = false } = {}) {
    const doc = docs.get(docId);
    _cancelAutoSave(docId);
    _cancelPdfPaneSave(docId);
    _syncPdfPaneToDoc(docId, { markDirty: false });
    const hasContent = doc && doc.content && doc.content.trim().length > 0;
    if (hasContent) {
      _saveClosingDraft(docId, doc);
      if (toast && uiModule) uiModule.showToast('Document closed');
    } else {
      markDeleted(docId);
      fetch(`${API_BASE}/api/document/${docId}`, { method: 'DELETE' }).catch(() => {});
    }
    _forgetActiveDoc(doc?.sessionId || _lastSessionId, docId);
    removeDocument(docId);
    _syncDocIndicator();
  }

async function closeTab(docId) {
    // Save current editor content to map so the check below uses fresh data
    saveCurrentToMap();
    // Closing the tab is a quiet detach action. The document-close
    // notification is reserved for closing the document surface itself;
    // showing it here makes a tab close look like a full document close.
    _detachDocFromSession(docId);
    // Find next tab in the current session
    const curSession = sessionModule?.getCurrentSessionId() || '';
    let nextId = null;
    for (const [id, d] of docs) {
      if (!d.sessionId || !curSession || d.sessionId === curSession) {
        nextId = id;
        break;
      }
    }
    if (!nextId) {
      setActiveDocId(null);
      closePanel();
      (document.getElementById('message') || document.getElementById('doc-indicator-btn'))?.focus();
      return;
    }
    if (activeDocId === docId) {
      switchToDoc(nextId);
    } else {
      renderTabs();
    }
  }

let _autoCreating = false;

let _creatingDoc = false;

async function _autoCreateFromInput(content) {
    if (_autoCreating) return;
    _autoCreating = true;
    const generation = bumpNavigationGeneration();
    try {
      let sessionId = _lastSessionId
        || (sessionModule && sessionModule.getCurrentSessionId());
      if (!sessionId) {
        sessionId = await _autoCreateSession();
      }
      const res = await fetch(`${API_BASE}/api/document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ session_id: sessionId, title: '', content, language: 'richtext' }),
      });
      if (!res.ok) throw new Error(`Document create failed: HTTP ${res.status}`);
      const doc = await res.json();
      if (!doc || !doc.id) throw new Error('Document create failed: missing id');
      if (generation !== _documentNavigationGeneration) return;
      content = document.getElementById('doc-editor-textarea')?.value ?? content;
      addDocToTabs(doc, sessionId);
      // Set the content into the map so switchToDoc preserves it
      const d = docs.get(doc.id);
      if (d) d.content = content;
      switchToDoc(doc.id);
      // Trigger auto-detect and auto-title
      setTimeout(attemptAutoDetect, 100);
      setTimeout(() => autoTitleFromContent(content, doc.id), 300);
      // Auto-save
      _scheduleAutoSave(doc.id, 2000);
    } catch (e) {
      console.error('Failed to auto-create document from input:', e);
    } finally {
      _autoCreating = false;
    }
  }

function saveCurrentToMap() {
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
        doc.content = textarea.value;
      }
    }
  }

function _minimizeNotesForDocumentOpen() {
    try {
      if (window.notesModule?.isPanelOpen?.()) {
        window.notesModule.closePanel('down');
        return;
      }
    } catch (_) {}
    if (!document.getElementById('notes-pane') && !document.getElementById('notes-pane-backdrop')) return;
    import('./notes.js?v=20260911notesselectioncancel1')
      .then(mod => {
        const close = mod.closeNotes || mod.closePanel || mod.default?.closeNotes || mod.default?.closePanel;
        if (typeof close === 'function') close('down');
      })
      .catch(() => {
        try { document.getElementById('notes-pane')?.remove(); } catch (_) {}
        try { document.getElementById('notes-pane-backdrop')?.remove(); } catch (_) {}
      });
  }

export function openPanel(options = {}) {
    _minimizeNotesForDocumentOpen();
    if (isOpen) return;
    // Clear any pane/divider still sliding out from a just-fired close so we
    // don't end up with two #doc-editor-pane nodes (and a stale close stripping
    // doc-view). Paired with the isOpen guard in _finishClose above.
    document.getElementById('doc-editor-pane')?._dispose?.();
    document.getElementById('doc-editor-pane')?.remove();
    document.getElementById('doc-divider')?.remove();
    // If the doc was minimized as a chip and the user opened the panel via
    // a different path (toolbar button, indicator), clear that chip — the
    // doc is becoming visible again.
    if (Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel')) {
      setMinimizedDocId(null);
      Modals.unregister('doc-panel');
    }
    const container = document.getElementById('chat-container');
    if (!container) return;

    setPanelOpen(true);
    // Doc was opened last → it goes in front of the email windows (clears the
    // email-front flag; the doc/email z-index alternation lives in CSS).
    document.body.classList.remove('email-front');
    _markDocVisibleState(_lastSessionId, 'open');

    document.body.classList.add('doc-view');

    // Sync toggle button state
    const toggleBtn = document.getElementById('overflow-doc-btn');
    if (toggleBtn) toggleBtn.classList.add('active');
    const docInd = document.getElementById('doc-indicator-btn');
    if (docInd) docInd.classList.add('active');

    // Create divider — grip in the middle (drag-to-resize), swapped for a
    // clickable collapse chevron on hover.
    const divider = document.createElement('div');
    divider.className = 'doc-divider';
    divider.id = 'doc-divider';
    // Single chevron that swaps direction based on cursor position:
    //   - cursor INSIDE the doc pane  →  › (collapse / close panel)
    //   - cursor OUTSIDE the doc pane →  ‹ (fullscreen — grow leftward)
    // The arrow rotates via CSS so the swap feels clean. The action follows
    // the glyph, so clicking always does what the arrow promises.
    // The secondary X button below it is only shown in fullscreen mode and
    // hides the pane outright (so fullscreen has an escape that isn't just
    // "exit fullscreen").
    divider.innerHTML = '<button type="button" class="doc-divider-collapse" title="Collapse panel" data-mode="collapse"><span>›</span></button>' +
      '<button type="button" class="doc-divider-hide" title="Hide panel" aria-label="Hide panel"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>';
    const _divHide = divider.querySelector('.doc-divider-hide');
    if (_divHide) {
      _divHide.addEventListener('mousedown', (e) => e.stopPropagation());
      _divHide.addEventListener('click', (e) => { e.stopPropagation(); closePanel('down'); });
    }

    // Create the editor pane
    const pane = document.createElement('div');
    pane.id = 'doc-editor-pane';
    pane.className = 'doc-editor-pane';
    const paneLifetime = new AbortController();
    pane._lifetime = paneLifetime;
    pane._dispose = () => {
      paneLifetime.abort();
      divider._disposeResize?.();
      pane.querySelector('#doc-md-toolbar')?._resizeObserver?.disconnect();
      pane.querySelector('#doc-tab-bar')?._resizeObserver?.disconnect();
      if (pane.contains(_lineNumberObservedTextarea)) {
        _lineNumberResizeObserver?.disconnect();
        _lineNumberObservedTextarea = null;
      }
      _selResizeObserver?.disconnect();
      _selResizeObserver = null;
      if (_lineNumberResizeRaf) cancelAnimationFrame(_lineNumberResizeRaf);
      _lineNumberResizeRaf = null;
      clearTimeout(_hlDebounce);
      clearTimeout(_autoDetectDebounce);
      clearTimeout(_autoTitleDebounce);
      clearTimeout(_emailLocalDraftDebounce);
    };
    if (options.restore) pane.classList.add('doc-pane-restoring');
    // ── Mobile: make toolbar/footer buttons work on the FIRST tap with the
    // keyboard up ──
    // Normally a tap while the keyboard is open is eaten by the OS keyboard
    // dismissal and the button's click never fires ("nothing triggers"). Keep
    // the field focused through the press so the tap isn't consumed, then
    // re-dispatch the click on release so the action fires on the first tap.
    // The action handler itself decides whether to then drop the keyboard
    // (Undo/Export/Close do; Format/Copy keep it). Touch only — desktop is
    // untouched.
    {
      let _kbBtn = null;
      pane.addEventListener('pointerdown', (e) => {
        _kbBtn = null;
        if (e.pointerType !== 'touch') return;
        const btn = e.target.closest && e.target.closest('button');
        if (!btn) return;
        const ae = document.activeElement;
        if (!(ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA'))) return;
        e.preventDefault();   // keep focus; this cancels the native touch click
        _kbBtn = btn;
      }, true);
      pane.addEventListener('pointerup', (e) => {
        const btn = _kbBtn; _kbBtn = null;
        if (!btn) return;
        if (e.target.closest && e.target.closest('button') === btn) {
          btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        }
      }, true);
      pane.addEventListener('pointercancel', () => { _kbBtn = null; }, true);
    }
    pane.innerHTML = `
      <input type="hidden" id="doc-title-input" value="" />
      <div class="doc-mobile-grabber" id="doc-mobile-grabber" aria-hidden="true"></div>
      <div class="doc-editor-header" id="doc-editor-actions">
        <button id="doc-undo-btn" class="doc-action-icon-btn" title="Undo (Ctrl+Z)" aria-label="Undo" aria-disabled="true" disabled style="gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg><span style="font-size:11px;">Undo</span></button>
        <button id="doc-redo-btn" class="doc-action-icon-btn" title="Redo (Ctrl+Shift+Z)" aria-label="Redo" aria-disabled="true" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.13-9.36L23 10"/></svg></button>
        <button id="doc-header-preview-btn" class="doc-action-icon-btn" title="Run / Preview" style="display:none;opacity:0.85;gap:4px;"></button>
        <span id="doc-stream-indicator" class="doc-stream-indicator" style="display:none"><span class="doc-stream-dot"></span> editing</span>
        <span id="doc-version-badge" class="doc-version-badge" title="Version history" style="display:none">v1</span>
        <span style="flex:1"></span>
        <button id="doc-export-pdf-btn" class="doc-action-icon-btn" title="Export PDF" style="display:none;opacity:0.7;gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 18 15 15"/></svg> <span style="font-size:11px;">Export PDF</span></button>
        <button id="doc-pdf-view-btn" class="doc-action-icon-btn" title="Toggle PDF view" style="display:none;opacity:0.7;gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> <span style="font-size:11px;">PDF</span></button>
        <select id="doc-language-select" class="doc-language-select">
          <option value="python">python</option>
          <option value="javascript">javascript</option>
          <option value="typescript">typescript</option>
          <option value="html">html</option>
          <option value="css">css</option>
          <option value="richtext">Rich Text</option>
          <option value="markdown">markdown</option>
          <option value="docx">Word / DOCX</option>
          <option value="json">json</option>
          <option value="yaml">yaml</option>
          <option value="bash">bash</option>
          <option value="sql">sql</option>
          <option value="rust">rust</option>
          <option value="go">go</option>
          <option value="java">java</option>
          <option value="c">c</option>
          <option value="cpp">c++</option>
          <option value="csharp">c#</option>
          <option value="xml">xml</option>
          <option value="svg">svg</option>
          <option value="toml">toml</option>
          <option value="ini">ini</option>
          <option value="ruby">ruby</option>
          <option value="php">php</option>
          <option value="csv">csv</option>
          <option value="email">email</option>
          <option value="pdf">pdf</option>
        </select>
        <!-- Close + Copy/Export moved to the bottom action footer (#doc-actions-footer)
             so regular docs match the email footer layout. -->
      </div>
      <div class="doc-tab-bar" id="doc-tab-bar"></div>
      <div id="doc-email-header" class="doc-email-header" style="display:none">
        <button type="button" id="doc-email-collapse-btn" class="doc-email-collapse-btn" title="Hide email fields" aria-expanded="true">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 15 12 9 18 15"/></svg>
          <span id="doc-email-collapse-summary" class="doc-email-collapse-summary">No recipient · No subject</span>
        </button>
        <div id="doc-email-fields" class="doc-email-fields">
          <div class="email-field" style="position:relative">
            <span class="email-field-prefix">To</span>
            <input type="text" id="doc-email-to" placeholder="recipient@example.com" autocomplete="off" />
            <div id="doc-email-to-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" id="doc-email-show-cc" class="email-cc-toggle" title="Show Cc/Bcc">Cc</button>
          </div>
          <div class="email-field" id="doc-email-cc-row" style="display:none;position:relative">
            <span class="email-field-prefix">Cc</span>
            <input type="text" id="doc-email-cc" placeholder="cc@example.com, example2" autocomplete="off" />
            <div id="doc-email-cc-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" class="email-cc-close" data-cc-close title="Hide Cc/Bcc" aria-label="Hide Cc/Bcc"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div class="email-field" id="doc-email-bcc-row" style="display:none;position:relative">
            <span class="email-field-prefix">Bcc</span>
            <input type="text" id="doc-email-bcc" placeholder="bcc@example.com" autocomplete="off" />
            <div id="doc-email-bcc-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" class="email-cc-close" data-cc-close title="Hide Cc/Bcc" aria-label="Hide Cc/Bcc"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div class="email-field" style="position:relative"><span class="email-field-prefix">Subject</span><input type="text" id="doc-email-subject" placeholder="" /></div>
          <div id="doc-email-attachments" class="email-attachments" style="display:none"></div>
          <div id="doc-email-compose-atts" class="email-compose-atts" style="display:none"></div>
        </div>
        <input type="hidden" id="doc-email-in-reply-to" />
        <input type="hidden" id="doc-email-references" />
        <input type="hidden" id="doc-email-source-uid" />
        <input type="hidden" id="doc-email-source-folder" />
        <input type="file" id="doc-email-file-input" multiple style="display:none" />
      </div>
      <input type="file" id="doc-md-image-input" accept="image/*" multiple style="display:none" />
      <div class="doc-md-toolbar" id="doc-md-toolbar" style="display:none">
        <div class="md-toolbar-leading-controls">
          <span class="md-view-toggle" id="doc-md-view-toggle" style="display:none" role="group" aria-label="Edit or preview">
            <button type="button" class="md-view-opt" data-mdview="edit" title="Edit source (Ctrl+Alt+M to toggle)"><span class="md-view-label">Write</span><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
            <button type="button" class="md-view-opt" data-mdview="preview" title="Preview (Ctrl+Alt+M to toggle)"><span class="md-view-label">Preview</span><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
          </span>
          <span class="md-view-toggle" id="doc-render-view-toggle" style="display:none" role="group" aria-label="Code or run">
            <button type="button" class="md-view-opt" data-renderview="code" title="Edit code"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg></button>
            <button type="button" class="md-view-opt" data-renderview="run" title="Run / Preview"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>
          </span>
        </div>
        <button type="button" class="md-scroll-arrow md-scroll-left" id="md-scroll-left" title="Scroll left" style="display:none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
        <div class="md-toolbar-items" id="md-toolbar-items">
          <button id="doc-email-ai-reply-btn" class="doc-action-icon-btn md-toolbar-email-only" type="button" title="Draft a reply with AI (fast + optional context)" style="display:none;align-items:center;gap:4px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="color:var(--accent, var(--red));flex-shrink:0;position:relative;top:-1px;"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg><span style="font-size:11px;">Reply</span></button>
          <span id="md-toolbar-sep-after-ai-reply" class="md-toolbar-sep md-toolbar-manual-sep md-toolbar-email-only" aria-hidden="true"></span>
          <button type="button" id="doc-ai-writing-btn" class="doc-ai-writing-btn md-toolbar-edit-only" title="Writing tools" aria-label="Writing tools" aria-haspopup="menu" aria-expanded="false"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 1.8 14.45 9.55 22.2 12l-7.75 2.45L12 22.2l-2.45-7.75L1.8 12l7.75-2.45L12 1.8Z"/></svg><svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button id="doc-fontsize-btn" class="doc-action-icon-btn" title="Editor display size" aria-label="Editor display size" style="position:relative;width:28px;height:26px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.7;"><path d="M4 7V4h16v3"/><path d="M12 4v16"/><path d="M8 20h8"/></svg><span class="doc-fontsize-levels"><i data-sz="s">S</i><i data-sz="m">M</i><i data-sz="l">L</i></span></button>
          <button id="doc-diff-toggle-btn" class="doc-action-icon-btn" title="Compare changes" style="opacity:0.7;display:none;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="M5 12H2l5-5 5 5H9"/><path d="M19 12h3l-5 5-5-5h3"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" class="md-toolbar-edit-only" data-md="bold" title="Bold (Ctrl+B)"><b>B</b></button>
          <button type="button" class="md-toolbar-edit-only" data-md="italic" title="Italic (Ctrl+I)"><i>I</i></button>
          <button type="button" class="md-toolbar-edit-only" data-md="strike" title="Strikethrough (Ctrl+Shift+X)"><s>S</s></button>
          <button type="button" class="md-toolbar-edit-only md-toolbar-rich-only" data-md="underline" title="Underline (Ctrl+U)" style="display:none"><u>U</u></button>
          <button type="button" class="md-toolbar-rich-only" data-md="superscript" title="Superscript" style="display:none"><span class="rich-script-icon">x<sup>2</sup></span></button>
          <button type="button" class="md-toolbar-rich-only" data-md="subscript" title="Subscript" style="display:none"><span class="rich-script-icon">x<sub>2</sub></span></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" class="md-dd-toggle md-toolbar-edit-only" data-dd="heading" title="Heading (Ctrl+Alt+1-6)"><b>H</b><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="font" title="Font family" aria-label="Font family" style="display:none"><span>Aa</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span id="md-toolbar-sep-after-heading" class="md-toolbar-sep md-toolbar-manual-sep md-toolbar-edit-only" aria-hidden="true"></span>
          <button type="button" class="md-dd-toggle md-toolbar-edit-only" data-dd="list" title="Bulleted list"><svg class="rich-list-bullet-icon" width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="4" cy="6" r="1.5"/><circle cx="4" cy="12" r="1.5"/><circle cx="4" cy="18" r="1.5"/><path d="M9 5h12v2H9zM9 11h12v2H9zM9 17h12v2H9z"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" id="md-toolbar-attach-btn" class="md-toolbar-attach-btn md-toolbar-edit-only" title="Attach file"><svg class="md-attach-paperclip-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg><svg class="md-attach-image-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/><line x1="18" y1="4" x2="18" y2="10"/><line x1="15" y1="7" x2="21" y2="7"/></svg></button>
          <button type="button" id="md-toolbar-inline-image-btn" class="md-toolbar-inline-image-btn md-toolbar-edit-only" title="Insert image" aria-label="Insert image" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/><line x1="18" y1="4" x2="18" y2="10"/><line x1="15" y1="7" x2="21" y2="7"/></svg></button>
          <button type="button" class="md-toolbar-edit-only" data-md="link" title="Link (Ctrl+K)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="image" title="Image options" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-email-hide md-toolbar-edit-only" data-dd="code" title="Code">\`<svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-rich-only" style="display:none"></span>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="textsize" title="Font size" aria-label="Font size" style="display:none"><span class="rich-font-size-icon">16</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-align-control" data-dd="align" title="Text alignment (Ctrl+Shift+L/E/J)" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="10" x2="16" y2="10"/><line x1="4" y1="14" x2="20" y2="14"/><line x1="4" y1="18" x2="14" y2="18"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="spacing" title="Line spacing" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M4 12h16M4 17h16"/><path d="m1 5 2-2 2 2M3 3v16m-2-2 2 2 2-2"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-toolbar-rich-only" data-md="outdent" title="Decrease indent (Ctrl+[)" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="11" y1="6" x2="21" y2="6"/><line x1="11" y1="12" x2="21" y2="12"/><line x1="11" y1="18" x2="21" y2="18"/><polyline points="7 8 3 12 7 16"/></svg></button>
          <button type="button" class="md-toolbar-rich-only" data-md="indent" title="Increase indent (Ctrl+])" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="11" y1="6" x2="21" y2="6"/><line x1="11" y1="12" x2="21" y2="12"/><line x1="11" y1="18" x2="21" y2="18"/><polyline points="3 8 7 12 3 16"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-inline-format" data-dd="color" title="Text color" style="display:none"><span class="rich-color-letter">A</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-inline-format" data-dd="highlight" title="Highlight color" style="display:none"><svg class="rich-highlight-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 11-6 6v3h3l6-6"/><path d="m22 12-7-7-8.5 8.5 7 7Z"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span id="md-toolbar-sep-after-highlight" class="md-toolbar-sep md-toolbar-manual-sep md-toolbar-edit-only" aria-hidden="true"></span>
          <button type="button" class="md-toolbar-rich-only" data-md="quote" title="Block quote" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M7 17H3l3-10h5zm10 0h-4l3-10h5z"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="table" title="Table" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="1"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-inline-format" data-dd="separator" title="Insert separator" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="12" x2="21" y2="12"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-toolbar-rich-only md-toolbar-email-hide" data-md="pagebreak" title="Page break" aria-label="Page break" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 5h18M3 19h18"/><path d="M12 8v7m0 0-3-3m3 3 3-3"/></svg></button>
          <button type="button" class="md-toolbar-rich-only" data-md="unlink" title="Remove link" aria-label="Remove link" disabled style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 15l-2 2a4 4 0 0 1-6-6l3-3a4 4 0 0 1 5-1"/><path d="M15 9l2-2a4 4 0 0 1 6 6l-3 3a4 4 0 0 1-5 1"/><line x1="8" y1="2" x2="16" y2="22"/></svg></button>
          <button type="button" class="md-toolbar-rich-only" data-md="removeformat" title="Clear formatting" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M12 5v14M8 19h8"/><path d="m4 4 16 16"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" id="doc-find-toolbar-btn" class="md-toolbar-edit-only" title="Find (Ctrl+F)" aria-label="Find"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg></button>
          <button type="button" id="doc-outline-toolbar-btn" title="Document outline" aria-label="Document outline" aria-haspopup="true" aria-expanded="false"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3" cy="6" r="1" fill="currentColor" stroke="none"/><circle cx="3" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="3" cy="18" r="1" fill="currentColor" stroke="none"/></svg></button>
          <span id="md-toolbar-emoji-slot" class="md-toolbar-edit-only"></span>
          <span class="md-toolbar-sep md-toolbar-pdf-only" style="display:none"></span>
          <button type="button" id="doc-pdf-add-sign-btn" class="md-toolbar-pdf-only" title="Add signature (then click on PDF)" style="display:none"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3l6 6-9 9-3-3z"/><path d="M9 15l-3 1 1-3"/><path d="M4 18l3-3"/><path d="M3 20l3-3"/><path d="M5 22l3-3"/></svg><span class="doc-pdf-sign-label">sign</span></button>
          <span class="md-toolbar-sep md-toolbar-pdf-only" style="display:none"></span>
          <button type="button" id="doc-pdf-add-text-btn" class="md-toolbar-pdf-only" title="Add text box (then click on PDF)" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg></button>
          <button type="button" id="doc-pdf-add-check-btn" class="md-toolbar-pdf-only" title="Add checkmark (then click on PDF)" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></button>
          <button type="button" id="doc-pdf-refresh-btn" class="md-toolbar-pdf-only" title="Reload PDF view" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>
        </div>
        <button type="button" class="md-scroll-arrow md-scroll-right" id="md-scroll-right" title="Scroll right" style="display:none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
        <div class="md-toolbar-overflow-wrapper" id="md-toolbar-overflow-wrapper" style="display:none">
          <button class="md-toolbar-overflow-toggle" id="md-toolbar-overflow-toggle" title="More formatting"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>
          <div class="md-toolbar-overflow-menu" id="md-toolbar-overflow-menu"></div>
        </div>
      </div>
      <div id="doc-find-bar" class="doc-find-bar" style="display:none">
        <div class="doc-find-row">
          <input id="doc-find-input" class="doc-find-input" type="text" placeholder="Find..." autocomplete="off" />
          <span id="doc-find-count" class="doc-find-count" aria-live="polite"></span>
          <button type="button" id="doc-find-prev" class="doc-find-nav" title="Previous match" aria-label="Previous match">&uarr;</button>
          <button type="button" id="doc-find-next" class="doc-find-nav" title="Next match" aria-label="Next match">&darr;</button>
          <button type="button" id="doc-find-replace-toggle" class="doc-find-nav" title="Show replace" aria-label="Show replace" aria-expanded="false"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m17 3 4 4-4 4"/><path d="M3 7h18"/><path d="m7 21-4-4 4-4"/><path d="M21 17H3"/></svg></button>
          <button type="button" id="doc-find-close" class="doc-find-close" title="Close" aria-label="Close find">&times;</button>
        </div>
        <div id="doc-replace-row" class="doc-replace-row" hidden>
          <input id="doc-replace-input" class="doc-find-input" type="text" placeholder="Replace with..." autocomplete="off" />
          <button type="button" id="doc-replace-current" class="doc-find-action">Replace</button>
          <button type="button" id="doc-replace-all" class="doc-find-action">Replace all</button>
        </div>
      </div>
      <div id="doc-editor-surface" role="tabpanel" class="doc-editor-surface">
      <div id="doc-editor-wrap" class="doc-editor-wrap">
        <div id="doc-line-numbers" class="doc-line-numbers">1</div>
        <pre id="doc-editor-highlight" class="doc-editor-highlight"><code id="doc-editor-code"></code></pre>
        <textarea id="doc-editor-textarea" class="doc-editor-textarea" placeholder="Document content..." spellcheck="false"></textarea>
      </div>
      <!-- WYSIWYG email body. In email mode this replaces the source editor:
           B/I/S act on the live text (execCommand), and on send its HTML becomes
           the email's HTML part. Its plain text is mirrored into the textarea so
           the existing send/draft/change-detection paths keep working. -->
      <div id="doc-email-richbody" class="doc-email-richbody" contenteditable="true" spellcheck="false" style="display:none" data-no-swipe-dismiss></div>
      <div id="doc-rich-empty-import" class="doc-rich-empty-import" style="display:none" aria-live="polite">
        <button type="button" class="doc-preview-hover-edit doc-rich-empty-import-btn" title="Import a document" aria-label="Import a document">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M12 12v6"></path><path d="m9 15 3 3 3-3"></path></svg>
          <span>Import document</span>
        </button>
      </div>
      <div id="doc-email-actions" class="doc-email-actions" style="display:none">
        <button id="doc-email-discard-btn" class="email-discard-btn" title="Close email" style="display:inline-flex;align-items:center;gap:5px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg><span>Close</span></button>
        <span style="flex:1"></span>
        <div class="email-send-split">
          <button type="button" id="doc-email-send-btn" class="email-send-btn email-send-main" title="Send email (Ctrl+Enter)" onpointerdown="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)" onmousedown="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)" onclick="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>Send</button>
          <button type="button" id="doc-email-send-caret" class="email-send-btn email-send-caret" title="More send options" aria-haspopup="true" aria-expanded="false" onpointerdown="window.odysseusEmailCaretIntent&&window.odysseusEmailCaretIntent(event)" onmousedown="window.odysseusEmailCaretIntent&&window.odysseusEmailCaretIntent(event)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button>
          <div id="doc-email-more-menu" class="email-more-menu" style="display:none">
            <div class="dropdown-item-compact" id="doc-email-draft-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg></span>Save Draft</div>
            <div class="dropdown-item-compact" id="doc-email-schedule-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></span>Schedule Send...</div>
            <div class="dropdown-item-compact" id="doc-email-unread-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg></span>Mark Unread</div>
          </div>
        </div>
      </div>
      <div id="doc-md-preview" class="doc-md-preview" style="display:none"></div>
      <div id="doc-docx-preview" class="doc-docx-preview" style="display:none"></div>
      <div id="doc-csv-preview" class="doc-csv-preview" style="display:none"></div>
      <iframe id="doc-html-preview" class="doc-html-preview" sandbox="allow-scripts allow-modals" style="display:none"></iframe>
      <div id="doc-pdf-view" style="display:none;width:100%;flex:1;min-height:0;overflow:auto;background:#525659;padding:20px 0;position:relative;">
        <div id="doc-pdf-save-pill" style="display:none;position:absolute;top:8px;right:14px;padding:4px 10px;border-radius:12px;font-size:11px;z-index:5;pointer-events:none;background:transparent;color:transparent;"></div>
      </div>
      </div>
      <!-- Action footer sits AFTER all the content/preview panes so it stays
           pinned to the bottom no matter which pane (editor / md-preview /
           csv / html / pdf) is the one growing to fill. -->
      <div id="doc-actions-footer" class="doc-email-actions">
        <span id="doc-stats" class="doc-stats-wrap">
          <button type="button" id="doc-stats-btn" class="doc-stats-btn" aria-haspopup="true" aria-expanded="false" title="0 words in document">
            <span id="doc-stats-count">0</span><span id="doc-stats-unit" class="doc-stats-unit"> words</span>
          </button>
          <div id="doc-stats-popover" class="doc-stats-popover" hidden>
            <div id="doc-stats-scope" class="doc-stats-scope">Document</div>
            <div class="doc-stats-row"><span>Words</span><strong id="doc-stats-words">0</strong></div>
            <div class="doc-stats-row"><span>Characters</span><strong id="doc-stats-characters">0</strong></div>
            <div class="doc-stats-row"><span>Without spaces</span><strong id="doc-stats-characters-no-spaces">0</strong></div>
            <div class="doc-stats-row"><span>Lines</span><strong id="doc-stats-lines">0</strong></div>
            <div class="doc-stats-row"><span>Reading time</span><strong id="doc-stats-reading">0 min</strong></div>
          </div>
        </span>
        <span class="email-send-split" id="doc-copy-export-split">
          <button type="button" id="doc-footer-copy-btn" class="email-send-btn email-send-main doc-save-button" title="All changes saved (Ctrl+S)" data-mode="save" data-save-state="saved" aria-label="Saved" aria-live="polite">
            <svg class="doc-save-state-icon doc-save-state-dirty" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 3h12l4 4v14H4z"/><path d="M8 3v6h8V3"/><path d="M8 21v-6h8v6"/><path d="m16 3 6 6m0-6-6 6" stroke="var(--fg)" stroke-width="3"/></svg>
            <svg class="doc-save-state-icon doc-save-state-saving" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.2-8.56"/></svg>
            <svg class="doc-save-state-icon doc-save-state-saved" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
            <svg class="doc-save-state-icon doc-save-state-error" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><line x1="12" y1="7" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            <span class="doc-save-button-label">Saved</span>
          </button>
          <button type="button" id="doc-footer-export-btn" class="email-send-btn email-send-caret" title="Export as…" aria-label="Export options"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 15 12 9 18 15"/></svg></button>
        </span>
      </div>
      <div id="doc-version-panel" class="doc-version-panel hidden">
        <div class="doc-version-header">
          <span>Version History</span>
          <button id="doc-version-close" class="doc-action-icon-btn" title="Close" aria-label="Close version history"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div id="doc-version-list" class="doc-version-list"></div>
      </div>
      <div id="doc-mobile-footer" class="doc-mobile-footer">
        <button id="doc-mobile-close" class="doc-mobile-footer-btn" type="button">Unlink</button>
        <span style="flex:1"></span>
        <button id="doc-mobile-copy" class="doc-mobile-footer-btn" type="button">Copy</button>
      </div>
    `;

    // Consolidate into a SINGLE action bar: move Undo + the type picker out of
    // the top header into the bottom footer (left side, next to Close) so a
    // regular doc shows one bar, not two. The rest of the header (run/preview,
    // fullscreen, version, PDF) stays put; the header hides itself when nothing
    // in it is visible — see _syncHeaderBarVisibility().
    // Note: `#doc-render-view-toggle` (code↔run for SVG/HTML) intentionally
    // stays in the top header so it matches `#doc-md-view-toggle` (markdown
    // edit↔preview) — both view toggles live in the same place.
    {
      const _footer = pane.querySelector('#doc-actions-footer');
      const _split = _footer && _footer.querySelector('#doc-copy-export-split');
      const _undo = pane.querySelector('#doc-undo-btn');
      const _redo = pane.querySelector('#doc-redo-btn');
      const _lang = pane.querySelector('#doc-language-select');
      const _preview = pane.querySelector('#doc-header-preview-btn');  // single Run ▶ for python/bash/js/csv
      const _exportPdf = pane.querySelector('#doc-export-pdf-btn');
      const _pdfView = pane.querySelector('#doc-pdf-view-btn');
      if (_footer && _split) {
        // Footer order (left → right): Undo, Run/Preview, Lang, …, Copy/Export.
        // The X close was here too but is now redundant with the per-tab close
        // button in the title strip — removed.
        if (_undo) _footer.insertBefore(_undo, _footer.firstChild);
        const _anchor = _undo;
        if (_redo && _anchor) _anchor.after(_redo);
        const _historyAnchor = _redo || _anchor;
        if (_preview && _historyAnchor) _historyAnchor.after(_preview);
        if (_lang) _split.before(_lang);
        // Pull every remaining header-only control into the footer so we
        // only ever render ONE bottom action row. The standalone top header
        // was leaving a duplicate row above (with fullscreen + version badge
        // + stream indicator). Each item keeps its own display: toggling.
        const _streamInd = pane.querySelector('#doc-stream-indicator');
        const _versionBadge = pane.querySelector('#doc-version-badge');
        if (_split) {
          if (_pdfView)      _split.before(_pdfView);
          if (_exportPdf)    _split.before(_exportPdf);
          if (_versionBadge) _split.before(_versionBadge);
          if (_streamInd)    _split.before(_streamInd);
        }
      }
      // iOS keeps the soft keyboard up when you tap a <button> (it doesn't blur
      // the focused textarea), so it lingers after you've typed. Dismiss it on
      // any footer control tap.
      if (_footer) _footer.addEventListener('pointerdown', (e) => {
        if (!e.target.closest('button, select')) return;
        const _ta = document.getElementById('doc-editor-textarea');
        if (_ta && document.activeElement === _ta) _ta.blur();
      });
    }

    {
      const statsRoot = pane.querySelector('#doc-stats');
      const statsButton = pane.querySelector('#doc-stats-btn');
      const statsPopover = pane.querySelector('#doc-stats-popover');
      if (statsRoot && statsButton && statsPopover) {
        statsButton.addEventListener('mousedown', (event) => event.preventDefault());
        statsButton.addEventListener('click', () => {
          if (!statsPopover.hidden) {
            dismissOrRemove(statsPopover);
            return;
          }
          _renderDocumentStats();
          statsPopover.hidden = false;
          statsPopover.style.zIndex = String(topPortalZ());
          // The editor pane clips overflow, so a footer-anchored absolute
          // popover can disappear underneath the document. Float it against
          // the viewport and place it above the stats button.
          const buttonRect = statsButton.getBoundingClientRect();
          const popoverHeight = statsPopover.offsetHeight;
          const popoverWidth = statsPopover.offsetWidth;
          const left = Math.max(8, Math.min(
            window.innerWidth - popoverWidth - 8,
            buttonRect.right - popoverWidth,
          ));
          const above = buttonRect.top - popoverHeight - 7;
          statsPopover.style.left = `${Math.round(left)}px`;
          statsPopover.style.top = `${Math.round(above >= 8 ? above : buttonRect.bottom + 7)}px`;
          statsButton.setAttribute('aria-expanded', 'true');
          bindMenuDismiss(
            statsPopover,
            () => {
              statsPopover.hidden = true;
              statsButton.setAttribute('aria-expanded', 'false');
            },
            event => !statsRoot.contains(event.target),
          );
        });
      }
    }

    // The pane must claim its final flex space before its entrance starts.
    // Animating width from zero and then returning ownership to flex caused a
    // second reflow, which looked like the open document rubber-banding when
    // a chat response settled.
    const desktopEntrance = window.innerWidth > 768;

    // Insert after chat-container (appears on right by default)
    // If sidebar is on the right, insert before chat-container instead
    const sidebar = document.getElementById('sidebar');
    const isRight = sidebar && sidebar.classList.contains('right-side');
    if (isRight) {
      pane.classList.add('doc-left');
      container.parentNode.insertBefore(pane, container);
      container.parentNode.insertBefore(divider, container);
    } else {
      pane.classList.remove('doc-left');
      container.after(divider);
      divider.after(pane);
    }

    // Desktop: reveal from the right without translating the flex item. A
    // transform makes the sibling layout appear to fly out and snap back.
    // Mobile uses the sheet animation from CSS.
    const isStartupMount = !!document.getElementById('app-loader');
    if (desktopEntrance && (_hasMountedPanel || isStartupMount)) {
      const isFirstMount = !_hasMountedPanel;
      _hasMountedPanel = true;
      // Hide the surface, not the flex item. This keeps the chat/document
      // split stable for the entire animation.
      pane.style.clipPath = 'inset(0 0 0 100%)';
      pane.style.opacity = '0';
      pane.style.borderColor = 'transparent';
      pane.style.boxShadow = 'none';
      divider.style.opacity = '0';
      const reveal = () => {
        if (!pane.isConnected || !isOpen) return;
        pane.style.transition = 'clip-path 0.35s cubic-bezier(0.22,1,0.36,1), opacity 0.28s ease-out';
        pane.style.clipPath = 'inset(0 0 0 0)';
        pane.style.opacity = '1';
        pane.style.borderColor = '';
        pane.style.boxShadow = '';
        divider.style.transition = 'opacity 0.2s ease-out';
        divider.style.opacity = '1';
        setTimeout(() => {
          pane.style.transition = '';
          pane.style.clipPath = '';
          pane.style.opacity = '';
          pane.style.borderColor = '';
          pane.style.boxShadow = '';
          divider.style.transition = '';
          divider.style.opacity = '';
        }, 380);
      };
      // Commit the new flex layout first, then pause briefly before revealing.
      // The pause plus the reveal is roughly one second overall.
      requestAnimationFrame(() => {
        const scheduleReveal = () => setTimeout(reveal, 650);
        if (isFirstMount) requestAnimationFrame(scheduleReveal);
        else scheduleReveal();
      });
    } else if (desktopEntrance) {
      _hasMountedPanel = true;
    }

    // Wire up divider drag to resize
    initDividerDrag(divider, pane, isRight);
    // Divider chevron — single button with three modes (the glyph is the
     // same `›` in markup; CSS rotates 180° for the left-pointing variant).
     //   • cursor INSIDE the doc pane  →  collapse  (›, slide back, closes panel)
     //   • cursor OUTSIDE the doc pane →  fullscreen (‹, slide outward, expands)
     //   • already fullscreen          →  unfullscreen (›, points back in)
     // The user can also drag the chevron vertically along the divider to
     // reposition it.
    const _divCollapse = divider.querySelector('.doc-divider-collapse');
    if (_divCollapse) {
      let _handleIdleTimer = null;
      const _scheduleHandleIdle = () => {
        if (_handleIdleTimer) clearTimeout(_handleIdleTimer);
        _handleIdleTimer = setTimeout(() => {
          if (!divider.matches(':hover') && !divider.contains(document.activeElement)) {
            divider.classList.add('doc-divider-handle-idle');
          }
        }, 2000);
      };
      const _showDividerHandle = () => {
        divider.classList.remove('doc-divider-handle-idle');
        _scheduleHandleIdle();
      };
      divider.addEventListener('pointerenter', _showDividerHandle);
      divider.addEventListener('pointermove', _showDividerHandle);
      divider.addEventListener('pointerleave', _scheduleHandleIdle);
      divider.addEventListener('focusin', _showDividerHandle);
      divider.addEventListener('focusout', _scheduleHandleIdle);
      _scheduleHandleIdle();

      _divCollapse.addEventListener('mousedown', (e) => e.stopPropagation());
      let _dragging = false;
      _divCollapse.addEventListener('click', (e) => {
        e.stopPropagation();
        _showDividerHandle();
        if (_dragging) { _dragging = false; return; }  // suppress click after drag
        const mode = _divCollapse.dataset.mode;
        if (mode === 'fullscreen' || mode === 'unfullscreen') toggleFullscreen();
        else closePanel('down');
      });
      const HYSTERESIS = 24;
      const _applyMode = (ev) => {
        // Fullscreen state takes precedence — once the pane is fullscreen the
        // chevron always offers the "exit fullscreen" affordance regardless
        // of cursor position.
        const isFull = pane.classList.contains('doc-fullscreen');
        if (isFull) {
          if (_divCollapse.dataset.mode !== 'unfullscreen') {
            _divCollapse.dataset.mode = 'unfullscreen';
            _divCollapse.title = 'Exit fullscreen';
          }
          return;
        }
        if (!ev) return;
        const rect = divider.getBoundingClientRect();
        const midX = (rect.left + rect.right) / 2;
        const cur = _divCollapse.dataset.mode;
        if (ev.clientX > midX + HYSTERESIS && cur !== 'collapse') {
          _divCollapse.dataset.mode = 'collapse';
          _divCollapse.title = 'Collapse panel';
        } else if (ev.clientX < midX - HYSTERESIS && cur !== 'fullscreen') {
          _divCollapse.dataset.mode = 'fullscreen';
          _divCollapse.title = 'Fullscreen';
        }
      };
      const _onMove = (ev) => _applyMode(ev);
      document.addEventListener('pointermove', _onMove, { passive: true, signal: paneLifetime.signal });
      paneLifetime.signal.addEventListener('abort', () => clearTimeout(_handleIdleTimer), { once: true });
      // Reflect the fullscreen state immediately on toggle (no cursor move).
      const _classObs = new MutationObserver(() => _applyMode());
      _classObs.observe(pane, { attributes: true, attributeFilter: ['class'] });

      // Drag-to-reposition: hold + drag vertically moves the chevron along
      // the divider. Stored as a percent so resizing the pane keeps it
      // proportional. Only kicks in after a small movement so a normal tap
      // still registers as a click.
      const DRAG_THRESHOLD = 4;
      let _startY = 0, _moved = false, _pid = null;
      _divCollapse.addEventListener('pointerdown', (ev) => {
        if (ev.button !== 0 && ev.pointerType === 'mouse') return;
        _startY = ev.clientY;
        _moved = false;
        _pid = ev.pointerId;
        _divCollapse.setPointerCapture?.(_pid);
        ev.preventDefault();
      });
      _divCollapse.addEventListener('pointermove', (ev) => {
        if (_pid === null) return;
        const dy = ev.clientY - _startY;
        if (!_moved && Math.abs(dy) < DRAG_THRESHOLD) return;
        _moved = true;
        _dragging = true;
        const rect = divider.getBoundingClientRect();
        if (!rect.height) return;
        const pct = Math.max(6, Math.min(94, ((ev.clientY - rect.top) / rect.height) * 100));
        _divCollapse.style.top = pct + '%';
      });
      const _endDrag = () => {
        if (_pid !== null) {
          try { _divCollapse.releasePointerCapture?.(_pid); } catch {}
          _pid = null;
        }
      };
      _divCollapse.addEventListener('pointerup', _endDrag);
      _divCollapse.addEventListener('pointercancel', _endDrag);

      const _obs = new MutationObserver(() => {
        if (!document.body.contains(divider)) {
          document.removeEventListener('pointermove', _onMove);
          _classObs.disconnect();
          _obs.disconnect();
        }
      });
      _obs.observe(document.body, { childList: true, subtree: true });
    }

    // Mobile grab handle — swipe down to dismiss (like the other sheet windows).
    _wireSwipeDismiss(document.getElementById('doc-mobile-grabber'));
    document.getElementById('doc-mobile-grabber')?.addEventListener('click', () => closePanel('down'));

    // Wire up events
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
    // Mobile footer: Close the current doc + Copy its content (replaces the
    // per-tab × on small screens, mirroring the email reader's Close footer).
    document.getElementById('doc-mobile-close')?.addEventListener('click', () => { if (activeDocId) closeTab(activeDocId); });
    document.getElementById('doc-mobile-copy')?.addEventListener('click', () => copyDocument());
    // Save, copy, run, export, delete, preview toggles are now in per-tab context menu
    document.getElementById('doc-version-badge').addEventListener('click', toggleVersionHistory);
    document.getElementById('doc-version-close').addEventListener('click', () => _closeVersionPanel({ restoreFocus: true }));
    // Reflect the current language as a small icon left of the type select.
    const _syncLangIcon = () => {
      const iconEl = document.getElementById('doc-language-icon');
      const v = document.getElementById('doc-language-select')?.value || '';
      if (iconEl) iconEl.innerHTML = v ? langIcon(v, 14, { style: 'opacity:0.75;' }) : '';
      _syncEditorPlaceholder();
    };
    // Intercept programmatic `langSelect.value = …` so the icon updates without
    // having to instrument every set-site in this file.
    (function _interceptLangSelectValue() {
      const ls = document.getElementById('doc-language-select');
      if (!ls) return;
      const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
      if (!desc || !desc.set) return;
      Object.defineProperty(ls, 'value', {
        configurable: true,
        get() { return desc.get.call(this); },
        set(v) { desc.set.call(this, v); _syncLangIcon(); _syncLangPicker(); },
      });
      _syncLangIcon();  // initial paint
    })();

    // ── Custom language picker ────────────────────────────────────────────
    // Native <option> can't render SVG. So we build a custom dropdown that
    // shows each language's icon + label, while keeping the underlying
    // <select> in place as the source of truth (all existing code that
    // reads/writes langSelect.value keeps working). The native select is
    // visually-hidden but still focusable for accessibility / keyboard.
    let _syncLangPicker = () => {};
    (function _initLangPicker() {
      const ls = document.getElementById('doc-language-select');
      if (!ls || ls.dataset.pickerWired === '1') return;
      ls.dataset.pickerWired = '1';

      const trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.id = 'doc-langpicker-trigger';
      trigger.className = 'doc-langpicker-trigger';
      trigger.setAttribute('aria-haspopup', 'listbox');
      trigger.setAttribute('aria-expanded', 'false');

      const menu = document.createElement('div');
      menu.id = 'doc-langpicker-menu';
      menu.className = 'doc-langpicker-menu';
      menu.setAttribute('role', 'listbox');
      menu.style.display = 'none';

      // Build the menu rows from the <select>'s real <option>s — single
      // source of truth, future additions to the select auto-propagate.
      const _buildMenu = () => {
        menu.innerHTML = '';
        const options = Array.from(ls.options);
        const byValue = new Map(options.map(opt => [opt.value, opt]));
        const groups = [
          { label: 'Documents', values: ['richtext', 'markdown', 'pdf'] },
          { label: 'Email & data', values: ['email', 'csv'] },
          {
            label: 'Code',
            values: options
              .map(opt => opt.value)
              .filter(value => !['richtext', 'markdown', 'pdf', 'email', 'csv'].includes(value))
              .sort((a, b) => a.localeCompare(b)),
          },
        ];
        groups.forEach((group, groupIndex) => {
          const groupOptions = group.values.map(value => byValue.get(value)).filter(Boolean);
          if (!groupOptions.length) return;
          if (groupIndex) {
            const divider = document.createElement('div');
            divider.className = 'doc-langpicker-divider';
            divider.setAttribute('role', 'separator');
            menu.appendChild(divider);
          }
          const heading = document.createElement('div');
          heading.className = 'doc-langpicker-group';
          heading.textContent = group.label;
          heading.setAttribute('aria-hidden', 'true');
          menu.appendChild(heading);
          groupOptions.forEach(opt => {
          const row = document.createElement('button');
          row.type = 'button';
          row.className = 'doc-langpicker-item';
          row.dataset.value = opt.value;
          row.setAttribute('role', 'option');
          const ic = opt.value
            ? langIcon(opt.value, 14, { style: 'opacity:0.85;' })
            // Empty value = the "type" placeholder option — small dot so the
            // row still aligns with the others (and the picker shows _some_
            // mark when no type is set yet).
            : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="opacity:0.5;"><circle cx="12" cy="12" r="3"/></svg>';
          row.innerHTML = ic +
                          `<span class="doc-langpicker-label">${uiModule.esc(opt.textContent || opt.value)}</span>`;
          row.addEventListener('click', (e) => {
            e.stopPropagation();
            if (ls.value !== opt.value) {
              ls.value = opt.value;
              ls.dispatchEvent(new Event('change', { bubbles: true }));
            }
            _close();
          });
          menu.appendChild(row);
          });
        });
      };
      _buildMenu();

      _syncLangPicker = () => {
        const v = ls.value || '';
        const sel = Array.from(ls.options).find(o => o.value === v) || ls.options[0];
        const ic = v
          ? langIcon(v, 14, { style: 'opacity:0.85;flex-shrink:0;' })
          // No language picked yet → small dot mark so the trigger isn't bare.
          : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="opacity:0.5;flex-shrink:0;"><circle cx="12" cy="12" r="3"/></svg>';
        trigger.innerHTML = ic +
          `<span class="doc-langpicker-label">${uiModule.esc(sel?.textContent || 'type')}</span>` +
          '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:4px;opacity:0.6;"><polyline points="6 9 12 15 18 9"/></svg>';
        // Highlight the current row in the open menu.
        menu.querySelectorAll('.doc-langpicker-item').forEach(r => {
          r.classList.toggle('is-selected', r.dataset.value === v);
        });
      };

      const _close = () => {
        menu.style.display = 'none';
        trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('click', _outsideClick, true);
        document.removeEventListener('keydown', _escKey, true);
      };
      const _outsideClick = (e) => {
        if (!menu.contains(e.target) && !trigger.contains(e.target)) _close();
      };
      const _escKey = (e) => {
        if (e.key !== 'Escape' || menu.style.display === 'none') return;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _close();
      };

      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = menu.style.display !== 'none';
        if (open) { _close(); return; }
        // Position the menu under the trigger. It is body-mounted + fixed so
        // it escapes the document/footer stacking context, then promoted above
        // whatever tool window is currently on top.
        const r = trigger.getBoundingClientRect();
        menu.style.display = 'block';
        menu.style.visibility = 'hidden';
        menu.style.position = 'fixed';
        menu.style.zIndex = String(topPortalZ());
        menu.style.minWidth = r.width + 'px';
        menu.style.maxWidth = 'calc(100vw - 16px)';
        menu.style.maxHeight = 'min(60vh, calc(100dvh - 16px))';
        const left = Math.min(Math.max(8, r.left), Math.max(8, window.innerWidth - 168));
        menu.style.left = left + 'px';
        menu.style.top = Math.min(window.innerHeight - 8, r.bottom + 4) + 'px';
        // If it would overflow the bottom of the viewport, flip above.
        requestAnimationFrame(() => {
          const mr = menu.getBoundingClientRect();
          const maxLeft = Math.max(8, window.innerWidth - mr.width - 8);
          menu.style.left = Math.min(Math.max(8, r.left), maxLeft) + 'px';
          if (mr.bottom > window.innerHeight - 8) {
            menu.style.top = Math.max(8, r.top - mr.height - 4) + 'px';
          }
          menu.style.visibility = 'visible';
        });
        trigger.setAttribute('aria-expanded', 'true');
        document.addEventListener('click', _outsideClick, true);
        document.addEventListener('keydown', _escKey, true);
      });

      // Hide the native select but keep it in the layout for screen readers
      // / programmatic value sets / focus management. The icon span next to
      // it is removed since the trigger now carries the current icon.
      ls.classList.add('doc-langpicker-native-hidden');
      const iconSpan = document.getElementById('doc-language-icon');
      if (iconSpan) iconSpan.remove();
      ls.parentNode.insertBefore(trigger, ls);
      // Menu is body-mounted so position:fixed coords work cleanly.
      document.body.appendChild(menu);

      _syncLangPicker();
    })();
    document.getElementById('doc-language-select').addEventListener('change', () => {
      // Run output belongs to the previous language. Clear it immediately so
      // an old empty/error result cannot remain attached to the new format.
      const staleRunOutput = document.getElementById('doc-run-output');
      if (staleRunOutput) {
        staleRunOutput.style.display = 'none';
        staleRunOutput.innerHTML = '';
      }
      const changingDoc = activeDocId && docs.get(activeDocId);
      const previousLanguage = changingDoc?.language || '';
      if (_isRichTextLang(previousLanguage)) {
        const rich = _emailRichbodyActive();
        if (rich) _syncEmailRichbody(rich);
      } else if (changingDoc && previousLanguage === 'email') {
        const rich = _emailRichbodyActive();
        const fields = _parseEmailHeader(changingDoc.content || '');
        changingDoc.content = rich ? rich.innerHTML : (fields.body || '');
      } else if (changingDoc && previousLanguage !== 'email') {
        changingDoc.content = document.getElementById('doc-editor-textarea')?.value || changingDoc.content || '';
      }
      _syncLangIcon();
      _syncLangPicker();
      const val = document.getElementById('doc-language-select').value;
      // For form-backed docs, the select toggles between PDF view and the
      // markdown source instead of changing the underlying language.
      const live = document.getElementById('doc-editor-textarea')?.value
        || docs.get(activeDocId)?.content || '';
      if (_isFormBackedDoc(live) && (val === 'pdf' || val === 'markdown')) {
        _setPdfViewActive(val === 'pdf');
        return;
      }
      // Mark user explicitly chose a language — stop auto-detection
      if (activeDocId && docs.has(activeDocId)) {
        docs.get(activeDocId).userSetLanguage = (val !== '');
      }
      updateLanguage();
      syncHighlighting();
      // Show/hide markdown toolbar
      const lang = document.getElementById('doc-language-select').value;
      const mdToolbar = document.getElementById('doc-md-toolbar');
      if (mdToolbar) {
        // Toolbar stays visible for every type now; only the items inside
        // gate themselves on language.
        mdToolbar.style.display = '';
        if (mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
      }
      // If switching away from markdown, exit preview
      if (lang !== 'markdown') {
        _setMarkdownPreviewActive(false);
      }
      if (_isDocxLang(lang)) {
        _setDocxPreviewActive(true);
      } else {
        _setDocxPreviewActive(false);
      }
      // If switching away from CSV, exit table preview
      if (lang !== 'csv') {
        const csvPreview = document.getElementById('doc-csv-preview');
        const wrap2 = document.getElementById('doc-editor-wrap');
        if (csvPreview) csvPreview.style.display = 'none';
        if (wrap2) wrap2.style.display = '';
      }
      // If switching away from html, exit HTML preview
      if (!_isRenderLang(lang)) exitHtmlPreview();
      // Show/hide email fields
      if (lang === 'email') {
        const doc = activeDocId && docs.get(activeDocId);
        if (doc) _showEmailFields(doc);
      } else if (_isRichTextLang(lang)) {
        _hideEmailFields();
        const doc = activeDocId && docs.get(activeDocId);
        if (doc) {
          doc.language = lang;
          _showRichTextEditor(doc);
          _scheduleAutoSave(activeDocId, 300);
        }
      } else {
        _hideEmailFields();
      }
      _syncRichEmptyImport();
      // Sync header action buttons for new language
      _syncHeaderActions();
    });

    // Email send/draft buttons
    // Inject emoji picker button into markdown toolbar
    const emojiSlot = document.getElementById('md-toolbar-emoji-slot');
    if (emojiSlot && !emojiSlot.querySelector('.emoji-picker-btn')) {
      // Resolve the live target on click: the WYSIWYG email contenteditable
      // when active, otherwise the plain markdown textarea.
      emojiSlot.appendChild(emojiPicker.createEmojiButton(
        () => _emailRichbodyActive() || document.getElementById('doc-editor-textarea')
      ));
    }

    const handleSendIntent = (e) => {
      if (e && e.__odysseusEmailSendHandled) return;
      const rawTarget = e && e.target;
      const target = rawTarget && rawTarget.nodeType === Node.TEXT_NODE ? rawTarget.parentElement : rawTarget;
      const targetBtn = target && target.closest ? target.closest('#doc-email-send-btn') : null;
      const btn = targetBtn || null;
      if (!btn || btn.disabled) return;
      if (e) {
        e.preventDefault();
        e.stopPropagation();
        e.__odysseusEmailSendHandled = true;
      }
      const _m = document.getElementById('doc-email-more-menu');
      if (_m) _m.style.display = 'none';
      document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
      _sendEmail();
    };
    window.odysseusEmailSendIntent = handleSendIntent;
    if (!window._emailSendDelegatedBoundV3) {
      window._emailSendDelegatedBoundV3 = true;
      ['pointerdown', 'mousedown', 'pointerup', 'click'].forEach((type) => {
        window.addEventListener(type, handleSendIntent, true);
        document.addEventListener(type, handleSendIntent, true);
      });
    }

    let lastCaretToggleAt = 0;
    const toggleSendMenu = (caret) => {
      const menu = document.getElementById('doc-email-more-menu');
      if (!menu) return;
      const opening = menu.style.display === 'none';
      menu.style.display = opening ? '' : 'none';
      if (caret) caret.setAttribute('aria-expanded', String(opening));
    };
    const handleCaretIntent = (e) => {
      if (e && e.__odysseusEmailCaretHandled) return;
      const now = Date.now();
      if (e && e.type === 'click' && now - lastCaretToggleAt < 350) {
        e.preventDefault();
        e.stopPropagation();
        e.__odysseusEmailCaretHandled = true;
        return;
      }
      const rawTarget = e && e.target;
      const target = rawTarget && rawTarget.nodeType === Node.TEXT_NODE ? rawTarget.parentElement : rawTarget;
      const targetCaret = target && target.closest ? target.closest('#doc-email-send-caret') : null;
      const caret = targetCaret || null;
      if (!caret) return;
      if (e) {
        e.preventDefault();
        e.stopPropagation();
        e.__odysseusEmailCaretHandled = true;
      }
      lastCaretToggleAt = now;
      toggleSendMenu(caret);
    };
    window.odysseusEmailCaretIntent = handleCaretIntent;
    if (!window._emailCaretDelegatedBoundV1) {
      window._emailCaretDelegatedBoundV1 = true;
      ['pointerdown', 'mousedown', 'click'].forEach((type) => {
        window.addEventListener(type, handleCaretIntent, true);
        document.addEventListener(type, handleCaretIntent, true);
      });
    }

    // Ctrl+Enter / Cmd+Enter sends the email when an email doc is active
    // Bind once at module level via a guard to avoid duplicate listeners on re-open
    if (!window._emailCtrlEnterBound) {
      window._emailCtrlEnterBound = true;
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          const doc = activeDocId && docs.get(activeDocId);
          if (doc && doc.language === 'email' && isOpen) {
            e.preventDefault();
            _sendEmail();
          }
        }
      });
    }
    // Ctrl+Alt+M (and Cmd+Opt+M on mac) flips Edit ↔ Preview on a markdown
    // doc. Bound once globally; gated on the doc panel being open and the
    // active doc being markdown so it doesn't fire while the user is typing
    // in a non-markdown context.
    if (!window._docMdToggleBound) {
      window._docMdToggleBound = true;
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.altKey && !e.shiftKey && (e.key === 'm' || e.key === 'M' || e.code === 'KeyM')) {
          if (!isOpen) return;
          const doc = activeDocId && docs.get(activeDocId);
          const lang = (doc?.language || 'markdown').toLowerCase();
          if (lang !== 'markdown') return;
          e.preventDefault();
          toggleMarkdownPreview();
          _syncHeaderActions();
        }
      });
    }
    // Keep document typing attached to the active document when the user
    // clicks a non-editable part of another window (for example an email
    // header or panel background). Real form fields and contenteditable areas
    // remain independent so email composition and controls are not hijacked.
    if (!window._docStickyTypingBound) {
      window._docStickyTypingBound = true;
      window.addEventListener('keydown', (e) => {
        if (!isOpen || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
        const textarea = document.getElementById('doc-editor-textarea');
        if (!textarea || textarea.disabled || textarea.readOnly || textarea.getClientRects().length === 0) return;

        const target = e.target instanceof Element ? e.target : null;
        if (
          target?.matches('input, textarea, select, button, a, [contenteditable="true"]')
          || target?.closest('input, textarea, select, button, a, [contenteditable="true"]')
          || target?.isContentEditable
        ) return;

        let replacement = null;
        if (e.key.length === 1) replacement = e.key;
        else if (e.key === 'Enter') replacement = '\n';
        else if (e.key === 'Backspace' || e.key === 'Delete') {
          const start = textarea.selectionStart;
          const end = textarea.selectionEnd;
          if (start === end) {
            if (e.key === 'Backspace' && start > 0) textarea.setSelectionRange(start - 1, start);
            else if (e.key === 'Delete' && end < textarea.value.length) textarea.setSelectionRange(end, end + 1);
          }
          replacement = '';
        }
        if (replacement === null) return;

        textarea.focus({ preventScroll: true });
        textarea.setRangeText(replacement, textarea.selectionStart, textarea.selectionEnd, 'end');
        textarea.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: replacement ? 'insertText' : 'deleteContentBackward', data: replacement || null }));
        e.preventDefault();
        e.stopPropagation();
      }, true);
    }
    document.getElementById('doc-email-draft-btn')?.addEventListener('click', () => {
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _saveDraft();
    });
    document.getElementById('doc-email-discard-btn')?.addEventListener('click', _discardEmail);
    document.getElementById('doc-email-unread-btn')?.addEventListener('click', () => {
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _markUnreadAndClose();
    });
    document.getElementById('doc-email-schedule-btn')?.addEventListener('click', (e) => {
      const anchor = document.getElementById('doc-email-send-caret') || e.currentTarget;
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _scheduleSend(anchor);
    });
    document.getElementById('doc-email-ai-reply-btn')?.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      _showDocAiReplyChoice(ev.currentTarget);
    });

    const collapseBtn = document.getElementById('doc-email-collapse-btn');
    if (collapseBtn && !collapseBtn._emailCollapseWired) {
      collapseBtn._emailCollapseWired = true;
      collapseBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const focusState = _captureEmailBodyFocusState();
        const header = document.getElementById('doc-email-header');
        const nextCollapsed = !header?.classList.contains('doc-email-header-collapsed');
        _setEmailHeaderCollapsed(nextCollapsed);
        if (!nextCollapsed) _restoreEmailBodyFocusState(focusState);
      });
      collapseBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
    }
    ['doc-email-to', 'doc-email-cc', 'doc-email-bcc', 'doc-email-subject'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', () => {
        _syncEmailHeaderSummary();
        saveCurrentToMap();
        _persistEmailLocalDraftSoon();
        _scheduleAutoSave(activeDocId, 800);
      });
      document.getElementById(id)?.addEventListener('focus', () => _setEmailHeaderCollapsed(false, { manual: false }));
    });
    document.getElementById('doc-email-richbody')?.addEventListener('focus', _maybeAutoCollapseEmailHeader);
    if (window.visualViewport && !window._docEmailViewportCollapseBound) {
      window._docEmailViewportCollapseBound = true;
      window.visualViewport.addEventListener('resize', _maybeAutoCollapseEmailHeader);
    }

    // Split-button caret toggles the send-options menu.
    document.getElementById('doc-email-send-caret')?.addEventListener('click', handleCaretIntent);
    document.addEventListener('click', (e) => {
      const menu = document.getElementById('doc-email-more-menu');
      // Keep the menu open ONLY while interacting with the caret itself or the
      // menu. Any other click — including the Send button (which sits in the
      // same .email-send-split) — closes it, so the popup is tied to the arrow.
      if (menu && !e.target.closest('#doc-email-send-caret, #doc-email-more-menu')) {
        menu.style.display = 'none';
        document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
      }
    }, { signal: paneLifetime.signal });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      const menu = document.getElementById('doc-email-more-menu');
      if (!menu || menu.style.display === 'none') return;
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      menu.style.display = 'none';
      document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
    }, { capture: true, signal: paneLifetime.signal });

    // Attachments
    document.getElementById('doc-email-attach-btn')?.addEventListener('click', (e) => {
      _showComposeAttachMenu(e.currentTarget);
    });
    document.getElementById('md-toolbar-attach-btn')?.addEventListener('click', (e) => {
      if (_activeDocLanguage() === 'email') {
        _showComposeAttachMenu(e.currentTarget);
      } else if (_isRichTextLang(_activeDocLanguage())) {
        _showRichImageSourceMenu(e.currentTarget);
      } else {
        document.getElementById('doc-md-image-input')?.click();
      }
    });
    document.getElementById('md-toolbar-inline-image-btn')?.addEventListener('click', (e) => {
      if (_activeDocLanguage() === 'email' || _isRichTextLang(_activeDocLanguage())) {
        _showRichImageSourceMenu(e.currentTarget);
      }
    });
    document.getElementById('doc-email-file-input')?.addEventListener('change', _handleAttachUpload);
    document.getElementById('doc-md-image-input')?.addEventListener('change', _handleMarkdownImageUpload);

    // Cc/Bcc toggle
    document.getElementById('doc-email-show-cc')?.addEventListener('click', () => {
      _setEmailHeaderCollapsed(false, { manual: false });
      const ccRow = document.getElementById('doc-email-cc-row');
      const bccRow = document.getElementById('doc-email-bcc-row');
      if (ccRow) ccRow.style.display = '';
      if (bccRow) bccRow.style.display = '';
      document.getElementById('doc-email-show-cc').style.display = 'none';
      _syncEmailHeaderSummary();
    });

    // Cc/Bcc close — X buttons inside the Cc and Bcc fields hide both
    // rows + clear their inputs + restore the Cc opener on the To row.
    document.querySelectorAll('[data-cc-close]').forEach(closeBtn => {
      closeBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const ccRow = document.getElementById('doc-email-cc-row');
        const bccRow = document.getElementById('doc-email-bcc-row');
        const ccInput = document.getElementById('doc-email-cc');
        const bccInput = document.getElementById('doc-email-bcc');
        if (ccRow) ccRow.style.display = 'none';
        if (bccRow) bccRow.style.display = 'none';
        if (ccInput) ccInput.value = '';
        if (bccInput) bccInput.value = '';
        const ccToggle = document.getElementById('doc-email-show-cc');
        if (ccToggle) ccToggle.style.display = '';
        _syncEmailHeaderSummary();
        saveCurrentToMap();
        _scheduleAutoSave(activeDocId, 800);
      });
    });

    // Autocomplete for To / Cc / Bcc — typed fragment after the last
    // comma triggers contact search; Enter / Tab / click on a suggestion
    // appends "<email>, " so the user can keep typing more recipients.
    _wireRecipientAutocomplete('doc-email-to',  'doc-email-to-suggestions');
    _wireRecipientAutocomplete('doc-email-cc',  'doc-email-cc-suggestions');
    _wireRecipientAutocomplete('doc-email-bcc', 'doc-email-bcc-suggestions');

    // Header unified action button (preview or run depending on language)
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

    // Markdown Edit/Preview two-icon switch — click a side to go to that view.
    document.getElementById('doc-md-view-toggle')?.addEventListener('click', (e) => {
      const opt = e.target.closest('.md-view-opt');
      if (!opt) return;
      const wantPreview = opt.dataset.mdview === 'preview';
      const mdPrev = document.getElementById('doc-md-preview');
      const isPreview = mdPrev && mdPrev.style.display !== 'none';
      if (wantPreview !== isPreview) toggleMarkdownPreview();
      _syncHeaderActions();
    });
    document.getElementById('doc-md-preview')?.addEventListener('click', _handleMarkdownPreviewClickHint);

    // Unified Code / Run-or-View two-icon switch — language-aware: CSV flips
    // between code and the table view, Python/JS/etc. between code and run
    // output, HTML/SVG/XML between code and the iframe preview.
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

    // Font size toggle (S → M → L)
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
    // Click cycles through the sizes (S → M → L → S).
    if (fontBtn) fontBtn.addEventListener('click', () => {
      _fontIdx = (_fontIdx + 1) % 3;
      _applyDocFont();
      syncHighlighting();
      _scheduleSelRerender();
    });

    // Undo button in header
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

    // Diff toggle button — compare current content against previous version
    const diffToggleBtn = document.getElementById('doc-diff-toggle-btn');
    if (diffToggleBtn) diffToggleBtn.addEventListener('click', async () => {
      if (_diffModeActive) {
        exitDiffMode(true);
        return;
      }
      if (!activeDocId) return;
      const ta = document.getElementById('doc-editor-textarea');
      if (!ta) return;
      const current = ta.value;

      // Fetch version history and compare against previous version
      try {
        const res = await fetch(`${API_BASE}/api/document/${activeDocId}/versions`);
        if (!res.ok) throw new Error('Failed');
        const versions = await res.json();
        if (versions.length < 2) {
          if (uiModule) uiModule.showToast('No previous version to compare');
          return;
        }
        // versions are sorted desc — [0] is latest, [1] is previous
        const prevContent = versions[1].content || '';
        if (prevContent === current) {
          if (uiModule) uiModule.showToast('No changes from previous version');
          return;
        }
        enterDiffMode(prevContent, current);
      } catch {
        if (uiModule) uiModule.showError('Failed to load version history');
      }
    });

    // Markdown formatting toolbar
    initMdToolbar();

    // Wire highlighting sync
    const ta = document.getElementById('doc-editor-textarea');
    const pre = document.getElementById('doc-editor-highlight');
    if (ta && pre) {
      ta.addEventListener('input', () => {
        // Typing invalidates any pinned selection highlight
        if (_selections.length) clearSelection({ preserveCaret: true });
        // Auto-create a document if user types/pastes with no active doc.
        // Skip while a createDocument POST is in flight — otherwise typing
        // during the round-trip spawns a duplicate untitled doc.
        if (!activeDocId && !_creatingDoc && ta.value.trim()) {
          _autoCreateFromInput(ta.value);
          return;
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
        clearTimeout(_hlDebounce);
        _hlDebounce = setTimeout(syncHighlighting, 80);
        clearTimeout(_autoDetectDebounce);
        _autoDetectDebounce = setTimeout(attemptAutoDetect, AUTO_DETECT_DELAY);
        clearTimeout(_autoTitleDebounce);
        _autoTitleDebounce = setTimeout(() => autoTitleFromContent(ta.value), 600);
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
          if (e.key === 'b') { e.preventDefault(); applyMdFormat('bold'); }
          else if (e.key === 'i') { e.preventDefault(); applyMdFormat('italic'); }
          else if (e.key === 'k') { e.preventDefault(); applyMdFormat('link'); }
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

    renderTabs();

    // If no docs loaded, show empty state with helpful placeholder
    if (docs.size === 0 || !activeDocId) {
      showEmptyState();
    }
  }

const _DOCUMENT_TOOLBAR_GROUPS = [
    {
      name: 'display-size',
      selectors: ['#doc-fontsize-btn'],
    },
    {
      name: 'type',
      selectors: [
        '#doc-email-ai-reply-btn',
        '#md-toolbar-sep-after-ai-reply',
        '#doc-ai-writing-btn',
        '[data-dd="heading"]',
        '[data-dd="font"]',
        '#md-toolbar-sep-after-heading',
        '[data-dd="textsize"]',
      ],
    },
    {
      name: 'inline-basic',
      selectors: [
        '[data-md="bold"]',
        '[data-md="italic"]',
        '[data-md="underline"]',
        '[data-md="strike"]',
      ],
    },
    {
      name: 'inline-color',
      selectors: [
        '[data-dd="color"]',
        '[data-dd="highlight"]',
        '#md-toolbar-sep-after-highlight',
        '[data-md="removeformat"]',
        '#md-toolbar-attach-btn',
        '[data-md="link"]',
        '#md-toolbar-inline-image-btn',
      ],
    },
    {
      name: 'alignment',
      selectors: ['[data-dd="align"]'],
    },
    {
      name: 'spacing',
      selectors: ['[data-dd="spacing"]'],
    },
    {
      name: 'paragraph',
      selectors: [
        '[data-dd="list"]',
        '[data-dd="separator"]',
        '[data-md="outdent"]',
        '[data-md="indent"]',
        '[data-md="quote"]',
        '[data-dd="code"]',
      ],
    },
    {
      name: 'insert',
      selectors: [
        '[data-dd="image"]',
        '[data-dd="table"]',
        '[data-md="pagebreak"]',
      ],
    },
    {
      name: 'inline-rich',
      selectors: ['[data-md="superscript"]', '[data-md="subscript"]'],
    },
    {
      name: 'document',
      selectors: [
        '#doc-find-toolbar-btn',
        '#doc-outline-toolbar-btn',
        '#md-toolbar-emoji-slot',
      ],
    },
    {
      name: 'view',
      selectors: ['#doc-diff-toggle-btn'],
    },
  ];

const _AI_WRITING_ACTIONS = Object.freeze({
    proofread: 'Fix spelling and grammar in the open document. Apply corrections directly using targeted edits. Preserve the meaning, voice, and formatting; do not rewrite for style. Keep already-correct sentences unchanged. Use each affected paragraph as an exact unique FIND anchor and change only the spelling or grammar errors in its replacement. Check every paragraph, including repeated errors. Do not just list corrections in chat.',
    improve: 'Review the open document and create inline suggestions for clarity, wording, structure, and readability. Do not apply changes.',
    concise: 'Make the open document more concise by proposing one concrete, shorter replacement per affected paragraph. Each REPLACE must actually shorten the prose, not merely correct spelling or grammar. Each FIND must quote the entire original paragraph exactly, including any HTML tags. Keep distinct paragraphs separate and preserve their facts and meaning. Create inline suggestions only; do not apply changes.',
    style: 'Rewrite the open document to match my configured Writing Style setting. Preserve the meaning and create inline suggestions only; do not apply changes.',
    sources: 'Check factual claims in the open document using web research. For each claim, verify it, suggest a source link/citation when valid, or clearly mark it as unverified when you cannot find reliable evidence. Create inline suggestions only; do not apply changes.',
  });

const _aiWritingButtonMarkup = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 1.8 14.45 9.55 22.2 12l-7.75 2.45L12 22.2l-2.45-7.75L1.8 12l7.75-2.45L12 1.8Z"/></svg><svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';

function _aiWritingSelectionText() {
    const rich = _emailRichbodyActive();
    const browserSelection = window.getSelection?.();
    if (rich && browserSelection?.rangeCount && rich.contains(browserSelection.anchorNode)) {
      const text = browserSelection.toString().trim();
      if (text) return text;
    }
    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea && textarea.selectionStart !== textarea.selectionEnd) {
      const text = textarea.value.slice(textarea.selectionStart, textarea.selectionEnd).trim();
      if (text) return text;
    }
    return '';
  }

function _setAiWritingLoading(loading) {
    const button = document.getElementById('doc-ai-writing-btn');
    if (!button) return;
    if (button._writingSpinner) {
      button._writingSpinner.destroy?.();
      button._writingSpinner = null;
    }
    button.disabled = !!loading;
    button.classList.toggle('is-loading', !!loading);
    button.innerHTML = _aiWritingButtonMarkup;
    if (!loading) return;
    const whirlpool = spinnerModule.createWhirlpool(13);
    whirlpool.element.classList.add('doc-ai-writing-whirlpool');
    whirlpool.element.style.cssText = 'width:13px;height:13px;margin:0;display:inline-flex;align-items:center;justify-content:center;';
    button.replaceChildren(whirlpool.element, button.lastElementChild);
    button._writingSpinner = whirlpool;
  }

function _closeAiWritingMenu() {
    const menu = document.getElementById('doc-ai-writing-menu');
    if (menu) {
      menu._cleanup?.();
      menu.remove();
    }
    document.getElementById('doc-ai-writing-btn')?.setAttribute('aria-expanded', 'false');
  }

function _showAiWritingMenu(button) {
    _closeAiWritingMenu();
    // Capture before the menu can take focus or alter the browser selection.
    // This matters for both textarea ranges and contenteditable selections.
    const selectedTextAtOpen = _aiWritingSelectionText();
    const menu = document.createElement('div');
    menu.id = 'doc-ai-writing-menu';
    menu.className = 'doc-overflow-menu doc-ai-writing-menu open';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `
      <div class="doc-ai-writing-menu-title"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 1.8 14.45 9.55 22.2 12l-7.75 2.45L12 22.2l-2.45-7.75L1.8 12l7.75-2.45L12 1.8Z"/></svg><span>Writing tools</span></div>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="proofread" role="menuitem"><span class="doc-ai-writing-item-icon">Aa</span><span>Spelling &amp; grammar</span></button>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="improve" role="menuitem"><span class="doc-ai-writing-item-icon">✦</span><span>Suggest improvements</span></button>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="concise" role="menuitem"><span class="doc-ai-writing-item-icon">≡</span><span>Make more concise</span></button>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="style" role="menuitem"><span class="doc-ai-writing-item-icon">✎</span><span>Match my writing style</span></button>
      <button type="button" class="doc-overflow-item" data-ai-writing-action="sources" role="menuitem"><span class="doc-ai-writing-item-icon">↗</span><span>Check sources</span></button>`;
    document.body.appendChild(menu);
    const rect = button.getBoundingClientRect();
    const menuWidth = menu.offsetWidth || 210;
    const menuHeight = menu.offsetHeight || 132;
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.left))}px`;
    menu.style.top = `${Math.min(window.innerHeight - menuHeight - 8, rect.bottom + 5)}px`;
    button.setAttribute('aria-expanded', 'true');

    const dismiss = (event) => {
      if (event && (menu.contains(event.target) || event.target === button)) return;
      _closeAiWritingMenu();
      document.removeEventListener('pointerdown', dismiss, true);
      document.removeEventListener('keydown', onKeydown, true);
    };
    const onKeydown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        _closeAiWritingMenu();
        document.removeEventListener('pointerdown', dismiss, true);
        document.removeEventListener('keydown', onKeydown, true);
        button.focus({ preventScroll: true });
      }
    };
    menu._cleanup = () => {
      document.removeEventListener('pointerdown', dismiss, true);
      document.removeEventListener('keydown', onKeydown, true);
    };
    menu.querySelectorAll('[data-ai-writing-action]').forEach(item => {
      item.addEventListener('mousedown', event => event.preventDefault());
      item.addEventListener('click', async () => {
        const action = item.dataset.aiWritingAction;
        _closeAiWritingMenu();
        const prompt = _AI_WRITING_ACTIONS[action];
        if (!prompt) return;
        let configuredStyle = '';
        if (action === 'style') {
          const activeDocument = activeDocId ? docs.get(activeDocId) : null;
          const isEmailDocument = activeDocument?.language === 'email';
          try {
            const accountId = String(window.__odysseusActiveEmailAccount || '').trim();
            const suffix = accountId ? `?account_id=${encodeURIComponent(accountId)}` : '';
            const generalResponse = await fetch('/api/auth/settings', { credentials: 'same-origin' });
            const generalData = await generalResponse.json().catch(() => ({}));
            const generalStyle = String(generalData.document_writing_style || '').trim();
            let emailStyle = '';
            if (isEmailDocument) {
              const emailResponse = await fetch(`/api/email/style${suffix}`, { credentials: 'same-origin' });
              const emailData = await emailResponse.json().catch(() => ({}));
              if (emailResponse.ok) emailStyle = String(emailData.style || '').trim();
            }
            configuredStyle = isEmailDocument
              ? [
                  generalStyle && `GENERAL WRITING STYLE:\n${generalStyle}`,
                  emailStyle && `EMAIL CONVENTIONS:\n${emailStyle}`,
                ].filter(Boolean).join('\n\n')
              : generalStyle;
            if (!generalResponse.ok || !configuredStyle) {
              uiModule?.showToast?.('You haven\'t set up a writing style yet.', {
                duration: 7000,
                action: 'Set up in Settings',
                actionHint: isEmailDocument ? 'Email → Writing Style' : 'AI Defaults → Writing Style',
                onAction: () => window.adminModule?.open?.(isEmailDocument ? 'email' : 'ai'),
              });
              return;
            }
          } catch (_) {
            uiModule?.showToast?.('You haven\'t set up a writing style yet.', {
              duration: 7000,
              action: 'Set up in Settings',
              actionHint: isEmailDocument ? 'Email → Writing Style' : 'AI Defaults → Writing Style',
              onAction: () => window.adminModule?.open?.(isEmailDocument ? 'email' : 'ai'),
            });
            return;
          }
        }
        const selectedText = selectedTextAtOpen || _aiWritingSelectionText();
        const styleContext = configuredStyle
          ? `\n\nUse this configured writing style as the source of truth:\n---\n${configuredStyle.slice(0, 8000)}\n---`
          : '';
        const scopedPrompt = selectedText
          ? `${prompt}${styleContext}\n\nImportant scope: work only on this selected passage and do not change or suggest changes elsewhere in the document. Use the exact matching text from the active document as the FIND target even if the editor stores formatting markup around it. Selected passage:\n---\n${selectedText.slice(0, 12000)}\n---`
          : `${prompt}${styleContext}\n\nThere is no text selection, so work on the whole open document.`;
        // Desktop reveals the chat beside the document before dispatching the
        // writing request. On mobile the document is already a fixed sheet;
        // toggling its fullscreen class here reflows the sheet as the prompt is
        // sent and makes the whole screen visibly bounce.
        if (window.innerWidth > 768 && document.querySelector('.doc-editor-pane.doc-fullscreen')) {
          toggleFullscreen();
        }
        try { await saveDocument({ silent: true }); } catch (_) {}
        const input = document.getElementById('message');
        const send = document.querySelector('.send-btn');
        if (!input || !send || !window.chatModule?.handleChatSubmit) {
          uiModule?.showError?.('Chat is not ready for writing tools.');
          return;
        }
        input.value = scopedPrompt;
        window.chatModule.setHideUserBubble?.();
        _setAiWritingLoading(true);
        send.click();
        const streamSessionId = sessionModule.getCurrentSessionId?.();
        const loadingStartedAt = Date.now();
        const waitForWritingStream = () => {
          const active = !!window.chatModule?.hasActiveStream?.(streamSessionId);
          const tooLong = Date.now() - loadingStartedAt > 10 * 60 * 1000;
          if ((!active && Date.now() - loadingStartedAt > 500) || tooLong) _setAiWritingLoading(false);
          else setTimeout(waitForWritingStream, 250);
        };
        setTimeout(waitForWritingStream, 250);
      });
    });
    setTimeout(() => {
      document.addEventListener('pointerdown', dismiss, true);
      document.addEventListener('keydown', onKeydown, true);
    }, 0);
  }

function _orderDocumentToolbar(itemsWrap) {
    if (!itemsWrap) return;
    const pdfItems = Array.from(itemsWrap.querySelectorAll(':scope > .md-toolbar-pdf-only'));
    itemsWrap.querySelectorAll(':scope > .md-toolbar-sep:not(.md-toolbar-pdf-only):not(.md-toolbar-manual-sep)').forEach(separator => separator.remove());

    _DOCUMENT_TOOLBAR_GROUPS.forEach((group, index) => {
      group.selectors.forEach(selector => {
        const item = itemsWrap.querySelector(`:scope > ${selector}`);
        if (!item) return;
        item.dataset.toolbarGroup = group.name;
        itemsWrap.appendChild(item);
      });
      if (index === _DOCUMENT_TOOLBAR_GROUPS.length - 1) return;
      const separator = document.createElement('span');
      separator.className = 'md-toolbar-sep md-toolbar-edit-only';
      separator.dataset.toolbarSeparator = `${group.name}-${_DOCUMENT_TOOLBAR_GROUPS[index + 1].name}`;
      separator.setAttribute('aria-hidden', 'true');
      itemsWrap.appendChild(separator);
    });

    pdfItems.forEach(item => itemsWrap.appendChild(item));
  }

function _syncDocumentToolbarSeparators(itemsWrap) {
    if (!itemsWrap) return;
    const isVisible = (el) => el && !el.hidden && el.style.display !== 'none';
    itemsWrap.querySelectorAll(':scope > .md-toolbar-sep:not(.md-toolbar-pdf-only)').forEach((separator) => {
      if (separator.classList.contains('md-toolbar-manual-sep')) {
        let following = separator.nextElementSibling;
        while (following && !isVisible(following)) following = following.nextElementSibling;
        separator.style.display = isVisible(separator.previousElementSibling) && following ? '' : 'none';
        return;
      }
      separator.style.display = isVisible(separator.previousElementSibling) &&
        isVisible(separator.nextElementSibling) ? '' : 'none';
    });
  }

function initMdToolbar() {
    const toolbar = document.getElementById('doc-md-toolbar');
    if (!toolbar) return;

    let _mdDdActivationSerial = 0;

    const itemsWrap = document.getElementById('md-toolbar-items');
    const overflowWrapper = document.getElementById('md-toolbar-overflow-wrapper');
    const overflowToggle = document.getElementById('md-toolbar-overflow-toggle');
    const overflowMenu = document.getElementById('md-toolbar-overflow-menu');
    const undoBtn = document.getElementById('md-toolbar-undo');

    _orderDocumentToolbar(itemsWrap);

    toolbar.querySelectorAll('.md-dd-toggle').forEach(button => {
      button.setAttribute('aria-haspopup', 'menu');
      button.setAttribute('aria-expanded', 'false');
    });

    toolbar.addEventListener('pointerdown', (e) => {
      const dd = e.target.closest('.md-dd-toggle');
      if (dd) dd._mdDdActivationToken = ++_mdDdActivationSerial;
      _saveFormatSelection(e);
    });

    // Click handler for format buttons + the grouped dropdown toggles. The menu
    // is appended to <body> (not nested in the toolbar) so the draggable panel's
    // CSS transform doesn't reparent its fixed positioning or clip it.
    // Keep the editor's focus + selection when a format button / dropdown
    // toggle is pressed. Without this the button steals focus on press, which
    // collapses the textarea selection (so B/I/S apply to nothing) and, on
    // mobile, drops the keyboard — whose viewport resize then instantly closes
    // any dropdown that just opened. Preventing the default mousedown keeps the
    // textarea focused, so formatting hits the live selection and menus stay up.
    toolbar.addEventListener('mousedown', (e) => {
      if (_saveFormatSelection(e)) e.preventDefault();
    });

    toolbar.addEventListener('click', (e) => {
      const aiWriting = e.target.closest('.doc-ai-writing-btn');
      if (aiWriting) { e.preventDefault(); _showAiWritingMenu(aiWriting); return; }
      const dd = e.target.closest('.md-dd-toggle');
      if (dd) {
        e.preventDefault();
        // The contextual selection toolbar is useful until the main toolbar
        // is used. Hide it here so the dropdown opened by the user's latest
        // click cannot render underneath that older floating toolbar.
        _hideRichSelectionToolbar();
        _showMdDropdown(dd, null, dd._mdDdActivationToken);
        return;
      }
      const btn = e.target.closest('[data-md]');
      if (!btn) return;
      e.preventDefault();
      applyMdFormat(btn.dataset.md);
    });
    toolbar.addEventListener('keydown', (e) => {
      const dd = e.target.closest?.('.md-dd-toggle');
      if (!dd) return;
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      e.stopPropagation();
      _showMdDropdown(dd, e.key === 'ArrowUp' ? -1 : 0, ++_mdDdActivationSerial);
    });

    // Undo button
    if (undoBtn) {
      undoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        const ta = document.getElementById('doc-editor-textarea');
        if (ta) { ta.focus(); document.execCommand('undo'); }
      });
    }

    // Overflow collapse logic
    let _mdMenuOpen = false;
    // Horizontal-scroll affordance: the toolbar scrolls its icons; edge arrows
    // appear when there's more off either side and smoothly scroll to that edge.
    const scrollLeftBtn = document.getElementById('md-scroll-left');
    const scrollRightBtn = document.getElementById('md-scroll-right');
    function updateScrollArrows() {
      if (!itemsWrap || !scrollLeftBtn || !scrollRightBtn) return;
      const maxScroll = itemsWrap.scrollWidth - itemsWrap.clientWidth;
      const overflowing = maxScroll > 2;
      const showLeft = overflowing && itemsWrap.scrollLeft > 1;
      const showRight = overflowing && itemsWrap.scrollLeft < maxScroll - 1;
      toolbar.classList.toggle('has-left-scroll-arrow', showLeft);
      toolbar.classList.toggle('has-right-scroll-arrow', showRight);
      scrollLeftBtn.style.display = showLeft ? 'flex' : 'none';
      scrollRightBtn.style.display = showRight ? 'flex' : 'none';
    }
    scrollLeftBtn?.addEventListener('click', () => itemsWrap.scrollTo({ left: 0, behavior: 'smooth' }));
    scrollRightBtn?.addEventListener('click', () => itemsWrap.scrollTo({ left: itemsWrap.scrollWidth, behavior: 'smooth' }));
    itemsWrap?.addEventListener('scroll', updateScrollArrows, { passive: true });
    if (window.ResizeObserver && itemsWrap) {
      toolbar._resizeObserver = new ResizeObserver(updateScrollArrows);
      toolbar._resizeObserver.observe(itemsWrap);
    }

    function syncMdOverflow() {
      if (overflowWrapper) overflowWrapper.style.display = 'none';
      updateScrollArrows();
    }

    function closeMdMenu() {
      _mdMenuOpen = false;
      if (overflowMenu) overflowMenu.classList.remove('open');
    }

    if (overflowToggle) {
      overflowToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        _mdMenuOpen = !_mdMenuOpen;
        if (_mdMenuOpen) {
          document.body.appendChild(overflowMenu);
          const rect = overflowToggle.getBoundingClientRect();
          overflowMenu.style.position = 'fixed';
          overflowMenu.style.top = (rect.bottom + 2) + 'px';
          overflowMenu.style.right = (window.innerWidth - rect.right) + 'px';
          overflowMenu.style.left = 'auto';
        } else {
          overflowWrapper.appendChild(overflowMenu);
        }
        overflowMenu.classList.toggle('open', _mdMenuOpen);
      });
    }
    const signal = toolbar.closest('#doc-editor-pane')?._lifetime?.signal;
    document.addEventListener('click', () => {
      if (_mdMenuOpen) { closeMdMenu(); overflowWrapper.appendChild(overflowMenu); }
    }, { signal });

    // Re-check overflow on resize
    let _mdResizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(_mdResizeTimer);
      _mdResizeTimer = setTimeout(syncMdOverflow, 100);
    }, { signal });
    signal?.addEventListener('abort', () => {
      clearTimeout(_mdResizeTimer);
      if (overflowMenu && !toolbar.contains(overflowMenu)) overflowMenu.remove();
    }, { once: true });

    // Show toolbar if language is already markdown
    const lang = document.getElementById('doc-language-select')?.value;
    if (lang === 'markdown') toolbar.style.display = '';

    // Initial sync after layout
    requestAnimationFrame(syncMdOverflow);
    // Expose for external calls (e.g. after fullscreen toggle)
    toolbar._syncOverflow = syncMdOverflow;
  }

const _DOC_RECENTS_KEY = 'odysseus-doc-actions-recent';

const _DOC_MAX_VISIBLE = 2;

function _getDocRecent() {
    try { return JSON.parse(localStorage.getItem(_DOC_RECENTS_KEY) || '[]'); } catch { return []; }
  }

function _trackDocAction(id) {
    let recent = _getDocRecent().filter(x => x !== id);
    recent.unshift(id);
    if (recent.length > 10) recent.length = 10;
    localStorage.setItem(_DOC_RECENTS_KEY, JSON.stringify(recent));
  }

function initActionOverflow() {
    const actionsEl = document.getElementById('doc-editor-actions');
    const wrapper = document.getElementById('doc-overflow-wrapper');
    const toggle = document.getElementById('doc-overflow-toggle');
    const menu = document.getElementById('doc-overflow-menu');
    if (!actionsEl || !wrapper || !toggle || !menu) return;

    const allBtns = Array.from(actionsEl.querySelectorAll('.doc-collapsible-btn'));
    let _menuOpen = false;

    function syncOverflow() {
      allBtns.forEach(b => { b.classList.remove('doc-collapsed'); });
      menu.innerHTML = '';

      // Filter to currently visible buttons
      const available = allBtns.filter(b => b.style.display !== 'none');

      // Sort by recent usage, defaults: copy, export, save
      const recent = _getDocRecent();
      const defaults = ['doc-copy-btn', 'doc-export-btn', 'doc-save-btn'];
      const order = recent.length > 0 ? recent : defaults;

      // Auto-pin: md preview when language is markdown
      const lang = document.getElementById('doc-language-select')?.value;
      const pinned = [];
      if (lang === 'markdown') {
        const mdBtn = available.find(b => b.id === 'doc-md-btn');
        if (mdBtn) pinned.push(mdBtn);
      }

      const sorted = [...available].sort((a, b) => {
        const ai = order.indexOf(a.id), bi = order.indexOf(b.id);
        if (ai >= 0 && bi >= 0) return ai - bi;
        if (ai >= 0) return -1;
        if (bi >= 0) return 1;
        return 0;
      });

      // Pinned + top N (deduplicated) — pinned count against the max
      const visible = [...pinned];
      for (const btn of sorted) {
        if (visible.length >= _DOC_MAX_VISIBLE) break;
        if (!visible.includes(btn)) visible.push(btn);
      }
      // Ensure we never exceed MAX_VISIBLE
      while (visible.length > _DOC_MAX_VISIBLE) visible.pop();
      const overflow = sorted.filter(b => !visible.includes(b));

      // Show visible, hide overflow
      overflow.forEach(b => b.classList.add('doc-collapsed'));

      // Reorder DOM: visible buttons before wrapper
      for (const btn of visible) {
        actionsEl.insertBefore(btn, wrapper);
      }

      if (overflow.length > 0) {
        wrapper.style.display = '';
        overflow.forEach(btn => {
          const item = document.createElement('button');
          item.className = 'doc-overflow-item';
          item.innerHTML = btn.innerHTML + '<span>' + (btn.title || '') + '</span>';
          item.addEventListener('click', (e) => {
            _trackDocAction(btn.id);
            // Export button has its own submenu
            if (btn.id === 'doc-export-btn') {
              e.stopPropagation();
              const savedRect = item.getBoundingClientRect();
              closeMenu();
              setTimeout(() => showExportMenu(null, savedRect), 50);
              return;
            }
            closeMenu();
            btn.click();
            syncOverflow(); // re-sort with new recency
          });
          menu.appendChild(item);
        });
      } else {
        wrapper.style.display = 'none';
      }
    }

    function closeMenu() {
      _menuOpen = false;
      menu.classList.remove('open');
    }

    toggle.addEventListener('click', (e) => {
      e.stopPropagation();
      _menuOpen = !_menuOpen;
      if (_menuOpen) {
        // Move to body to escape overflow:hidden on doc-editor-pane
        document.body.appendChild(menu);
        const rect = toggle.getBoundingClientRect();
        menu.style.position = 'fixed';
        menu.style.top = (rect.bottom + 2) + 'px';
        menu.style.right = (window.innerWidth - rect.right) + 'px';
        menu.style.left = 'auto';
      } else {
        wrapper.appendChild(menu);
      }
      menu.classList.toggle('open', _menuOpen);
    });
    document.addEventListener('click', () => {
      if (_menuOpen) { closeMenu(); wrapper.appendChild(menu); }
    });

    // Also track when visible buttons are clicked directly
    allBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        _trackDocAction(btn.id);
        // Defer re-sort so the click handler fires first
        setTimeout(syncOverflow, 100);
      });
    });

    requestAnimationFrame(syncOverflow);
    _syncOverflow = syncOverflow;
  }

function initDividerDrag(divider, pane, isRight) {
    divider._disposeResize?.();
    const controller = new AbortController();
    const options = { signal: controller.signal };
    let dragging = false;
    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      syncHighlighting();
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) updateLineNumbers(textarea.value);
    };
    divider.addEventListener('mousedown', event => {
      dragging = true;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      event.preventDefault();
    }, options);
    document.addEventListener('mousemove', event => {
      if (!dragging) return;
      const width = isRight ? event.clientX : window.innerWidth - event.clientX;
      pane.style.width = Math.max(250, Math.min(width, window.innerWidth * 0.7)) + 'px';
      pane.style.flex = 'none';
    }, options);
    document.addEventListener('mouseup', endDrag, options);
    divider._disposeResize = () => {
      endDrag();
      controller.abort();
    };
  }

function _ensureDocChipRegistered() {
    if (Modals.isRegistered('doc-panel')) return;
    Modals.register('doc-panel', {
      // The ✕ / drag-to-trash on the minimized chip is a real close — detach
      // the doc from the chat session so it doesn't reappear in that chat.
      closeFn: () => {
        // Content was already saved to the map when the panel was minimized,
        // so just detach (don't re-read the now-removed editor).
        const id = _minimizedDocId;
        setMinimizedDocId(null);
        if (id) _detachDocFromSession(id);
      },
      restoreFn: () => {
        const id = _minimizedDocId;
        setMinimizedDocId(null);
        // openPanel builds the pane shell; switchToDoc re-renders the
        // saved doc content into it (including PDF render-pages, syntax
        // highlighting, etc.). Without switchToDoc, the pane is empty.
        openPanel();
        if (id && docs.has(id)) {
          try { switchToDoc(id); } catch (e) { console.error('Restore doc failed:', e); }
        }
      },
    });
  }

export function closePanel(direction) {
    if (!isOpen) {
      if (direction !== 'down' && Modals.isRegistered('doc-panel')) {
        setMinimizedDocId(null);
        _markDocVisibleState(_lastSessionId, 'closed');
        Modals.unregister('doc-panel');
      }
      return;
    }
    bumpNavigationGeneration();
    _closeVersionPanel();
    document.getElementById('doc-version-panel')?.remove();
    setPanelOpen(false);
    _closeDocumentOutline();
    _hideRichSelectionToolbar();
    _hideRichSlashMenu();
    if (_diffModeActive) exitDiffMode(true);
    try {
      CSS.highlights?.delete('doc-find-results');
      CSS.highlights?.delete('doc-find-current');
      CSS.highlights?.delete('doc-ai-selections');
    } catch (_) {}
    // On touch, closing the doc should leave the keyboard DOWN. The tap blurs
    // the textarea (keyboard starts down), but a stray refocus during teardown
    // (the view behind regaining focus, etc.) was bouncing it back up. Blur any
    // focused field now and again after the close settles to keep it down.
    if (direction !== 'down' && (('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0)) {
      const _dropKb = () => {
        const ae = document.activeElement;
        if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) { try { ae.blur(); } catch (_) {} }
      };
      _dropKb();
      requestAnimationFrame(_dropKb);
      setTimeout(_dropKb, 80);
    }
    // Snapshot before disposing the pane. PDF timers need a captured draft,
    // rather than DOM references that disappear during the close animation.
    saveCurrentToMap();
    _persistEmailLocalDraftNow();
    if (_hasPendingPdfPaneSave(activeDocId)) _flushAutoSave(activeDocId);
    _disposePdfTools?.();
    _disposePdfTools = null;
    suspendSuggestions();

    // A "down" close means minimize, not close. Register the chip and flip
    // the dock state to minimized so a chip appears at the bottom. Any
    // other direction is a real close — make sure any leftover chip from a
    // prior minimize cycle is cleared too.
    if (direction === 'down') {
      setMinimizedDocId(activeDocId);
      _markDocVisibleState(_lastSessionId, 'minimized');
      _ensureDocChipRegistered();
      Modals.minimize('doc-panel');
    } else if (Modals.isRegistered('doc-panel')) {
      setMinimizedDocId(null);
      _markDocVisibleState(_lastSessionId, 'closed');
      Modals.unregister('doc-panel');
    } else {
      _markDocVisibleState(_lastSessionId, 'closed');
    }

    const pane = document.getElementById('doc-editor-pane');
    const divider = document.getElementById('doc-divider');
    pane?._dispose?.();

    const _finishClose = () => {
      // If the panel was reopened during the slide-out animation (close →
      // reopen fast, e.g. close a draft then immediately compose a new one),
      // bail — otherwise this stale close strips doc-view after the new open
      // re-added it, and the fresh pane drops into the desktop split layout
      // (renders as a narrow "sidebar" on mobile).
      if (isOpen) { if (pane) pane.remove(); if (divider) divider.remove(); return; }
      document.body.classList.remove('doc-view');
      const container = document.getElementById('chat-container');
      if (container) container.style.display = '';
      if (pane) pane.remove();
      if (divider) divider.remove();
      setActiveDocId(null);
      const btn = document.getElementById('overflow-doc-btn');
      if (btn) btn.classList.remove('active');
      const docInd = document.getElementById('doc-indicator-btn');
      if (docInd) docInd.classList.remove('active');
    };

    if (pane) {
      // Determine slide direction
      let transform;
      if (direction === 'down') {
        // Full slide off-screen on mobile (sheet dismiss); small nudge on desktop.
        transform = window.innerWidth <= 768 ? 'translateY(100%)' : 'translateY(30px)';
      } else {
        const fromLeft = pane.classList.contains('doc-left');
        transform = fromLeft ? 'translateX(-40px)' : 'translateX(40px)';
      }
      pane.style.transition = 'transform 0.15s ease-in, opacity 0.1s ease-in';
      pane.style.transform = transform;
      pane.style.opacity = '0';
      if (divider) { divider.style.transition = 'opacity 0.1s ease-in'; divider.style.opacity = '0'; }
      pane.addEventListener('transitionend', _finishClose, { once: true });
      // Safety fallback
      setTimeout(_finishClose, 200);
    } else {
      _finishClose();
    }
  }

export function swapSide() {
    if (!isOpen) return;
    const pane = document.getElementById('doc-editor-pane');
    const divider = document.getElementById('doc-divider');
    const container = document.getElementById('chat-container');
    if (!pane || !divider || !container) return;

    const sidebar = document.getElementById('sidebar');
    const isRight = sidebar && sidebar.classList.contains('right-side');

    if (isRight) {
      // Sidebar moved right → doc goes left (before chat)
      pane.classList.add('doc-left');
      container.parentNode.insertBefore(pane, container);
      container.parentNode.insertBefore(divider, container);
    } else {
      // Sidebar moved left → doc goes right (after chat)
      pane.classList.remove('doc-left');
      container.after(divider);
      divider.after(pane);
    }

    // Re-init divider drag for the new side
    initDividerDrag(divider, pane, isRight);
  }

export async function newDocument() {
    let sessionId = docs.get(activeDocId)?.sessionId
      || _lastSessionId
      || (sessionModule && sessionModule.getCurrentSessionId());
    if (!sessionId) {
      try { sessionId = await _autoCreateSession(); }
      catch (e) { console.error('Failed to auto-create session for document:', e); return; }
    }
    await createDocument(sessionId);
  }

export async function createDocument(sessionId) {
    if (_creatingDoc) return;
    _creatingDoc = true;
    const generation = bumpNavigationGeneration();
    // If the panel was in empty-state, the user may type into the editor
    // during the create round-trip — preserve that text into the new doc
    // instead of letting switchToDoc blank it.
    const wasEmpty = !activeDocId;
    try {
      const res = await fetch(`${API_BASE}/api/document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          session_id: sessionId,
          title: '',
          content: '',
          language: 'richtext',
        }),
      });
      if (!res.ok) throw new Error(`Document create failed: HTTP ${res.status}`);
      const doc = await res.json();
      if (!doc || !doc.id) throw new Error('Document create failed: missing id');
      if (generation !== _documentNavigationGeneration) return;
      addDocToTabs(doc, sessionId);
      if (!isOpen) openPanel();
      // Re-enable editor if it was in empty state
      let textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        textarea.disabled = false;
        _syncEditorPlaceholder();
      }
      // Capture text typed during the round-trip (only when starting from the
      // empty editor — don't steal another doc's content).
      const typed = (wasEmpty && textarea && textarea.value.trim()) ? textarea.value : '';
      switchToDoc(doc.id);
      if (typed) {
        textarea = document.getElementById('doc-editor-textarea');
        if (textarea) textarea.value = typed;
        const d = docs.get(doc.id);
        if (d) d.content = typed;
        const rich = _emailRichbodyActive();
        if (rich && d && _isRichTextLang(d.language)) {
          rich.innerHTML = _richTextContentToHtml(typed);
          _syncEmailRichbody(rich);
        }
        syncHighlighting();
        _scheduleAutoSave(doc.id, 800);
      }
      textarea = document.getElementById('doc-editor-textarea');
      const focusTarget = _emailRichbodyActive() || textarea;
      if (focusTarget) focusTarget.focus();
    } catch (e) {
      if (generation !== _documentNavigationGeneration) return;
      console.error('Failed to create document:', e);
      if (uiModule) uiModule.showError('Failed to create document');
    } finally {
      _creatingDoc = false;
    }
  }

export function injectFreshDoc(doc) {
    if (!doc || !doc.id) return;
    const generation = bumpNavigationGeneration();
    const sessionId = doc.session_id || _lastSessionId || null;
    if (doc.language === 'email') {
      doc._skipLocalDraftOnce = true;
      try {
        const fields = _parseEmailHeader(doc.current_content || doc.content || '');
        _clearEmailLocalDraft(fields.sourceUid, fields.sourceFolder, fields.inReplyTo);
      } catch (_) {}
    }
    addDocToTabs(doc, sessionId);
    // Use _ensureDocPaneMounted (not `if (!isOpen) openPanel()`): when a draft
    // is composed from the email modal, `isOpen` can be stale-true while the
    // actual pane was torn down — a bare openPanel() early-returns and the doc
    // mounts into a wrong/half-built pane (rendered as a narrow sidebar on
    // mobile instead of its own full-screen window). This remounts it cleanly.
    _ensureDocPaneMounted();
    // Defer to the next frame so the panel DOM exists before switchToDoc
    // populates it. Do not call switchToDoc synchronously here: it saves the
    // previously active doc and can re-enter the email draft path while a reply
    // document is still being injected.
    return new Promise(resolve => requestAnimationFrame(() => {
      if (generation === _documentNavigationGeneration && docs.has(doc.id)) switchToDoc(doc.id);
      resolve();
    }));
  }

function _ensureDocPaneMounted() {
    // Loading a specific document is an explicit open action. A minimized
    // registration can survive session switching on mobile, leaving the newly
    // selected document represented only by its bottom dock tab even though
    // the editor has mounted. Clear that stale dock state before deciding
    // whether the existing pane can be reused.
    if (Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel')) {
      setMinimizedDocId(null);
      Modals.unregister('doc-panel');
    }
    if (!isOpen || !document.getElementById('doc-editor-pane')) {
      setPanelOpen(false);
      openPanel();
    } else {
      _markDocVisibleState(_lastSessionId, 'open');
    }
  }

export async function loadDocument(docId) {
    const generation = bumpNavigationGeneration();
    _minimizeNotesForDocumentOpen();
    if (_closingDocs.has(docId)) {
      registerDocument(_closingDocs.get(docId));
      releaseClosingDraft(docId);
    }
    // If already in tabs, just switch
    if (docs.has(docId)) {
      _ensureDocPaneMounted();
      switchToDoc(docId);
      return true;
    }
    // Mount the editor before the network request so mobile gets immediate
    // feedback instead of appearing unchanged while the document loads.
    _ensureDocPaneMounted();
    _showLoadingOverlay();
    try {
      const res = await fetch(`${API_BASE}/api/document/${docId}`);
      if (!res.ok) throw new Error(res.status === 404 ? 'Not found' : `HTTP ${res.status}`);
      const doc = await res.json();
      if (generation !== _documentNavigationGeneration) return false;
      addDocToTabs(doc, doc.session_id);
      switchToDoc(doc.id);
      return true;
    } catch (e) {
      if (generation !== _documentNavigationGeneration) return false;
      _hideLoadingOverlay();
      console.error('Failed to load document:', e);
      if (uiModule) {
        const msg = e.message === 'Not found'
          ? 'Document not found — try opening it from the Library.'
          : 'Could not open document.';
        uiModule.showError(msg);
      }
      return false;
    }
  }

function _maybeOpenDocFromHash() {
    const m = (window.location.hash || '').match(/^#document-(.+)$/);
    if (m) loadDocument(m[1]);
  }

export async function ensureDocPanel() {
    let sessionId = _lastSessionId
      || (sessionModule && sessionModule.getCurrentSessionId());
    if (!sessionId) {
      try {
        sessionId = await _autoCreateSession();
      } catch (e) {
        console.error('Failed to auto-create session for document:', e);
        openPanel();
        return;
      }
    }
    await loadSessionDocs(sessionId);
  }

async function _autoCreateSession({ adopt = true, forceNew = false } = {}) {
    // Materialize pending chat first if one exists
    if (!forceNew && sessionModule && sessionModule.hasPendingChat && sessionModule.hasPendingChat()) {
      await sessionModule.materializePendingSession();
      const id = sessionModule.getCurrentSessionId();
      if (id) { setLastSessionId(id); return id; }
    }
    // Preserve the current model when creating a doc session
    const curModel = sessionModule?.getCurrentModel ? sessionModule.getCurrentModel() : null;
    const sessionsRaw = sessionModule?.getSessions?.();
    const sessions = Array.isArray(sessionsRaw) ? sessionsRaw : [];
    const match = curModel && sessions.find(s => s.model === curModel && s.endpoint_url);
    const fd = new FormData();
    fd.append('name', `Notes ${new Date().toLocaleTimeString()}`);
    fd.append('skip_validation', 'true');
    if (match) {
      fd.append('endpoint_url', match.endpoint_url);
      fd.append('model', match.model);
      if (match.endpoint_id) fd.append('endpoint_id', match.endpoint_id);
    }
    const res = await fetch(`${API_BASE}/api/session`, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.detail || `Session create failed: HTTP ${res.status}`);
    const sessionId = payload.id;
    if (!sessionId) throw new Error('Session create returned no session ID');
    setLastSessionId(sessionId);
    // Tell sessions module so chat uses the same session
    if (adopt && sessionModule && sessionModule.setCurrentSessionId) {
      sessionModule.setCurrentSessionId(sessionId);
    }
    if (sessionModule && sessionModule.loadSessions) sessionModule.loadSessions();
    return sessionId;
  }

export async function loadSessionDocs(sessionId, opts = {}) {
    suspendSuggestions();
    const generation = bumpNavigationGeneration();
    _closeVersionPanel();
    setLastSessionId(sessionId);
    const restoreMode = !!opts.restoreMode;
    const shouldRestoreOpen = localStorage.getItem(_docOpenKey(sessionId)) === '1';
    const shouldRestoreMinimized = localStorage.getItem(_docMinimizedKey(sessionId)) === '1';

    // Save current editor content to map before tearing down
    if (activeDocId && docs.has(activeDocId)) {
      saveCurrentToMap();
    }

    // Flush any pending saves or dirty documents belonging to previous sessions
    for (const [id, doc] of docs) {
      if (doc.sessionId && doc.sessionId !== sessionId) {
        if (_pendingAutoSaves.has(id) || ['dirty', 'error'].includes(doc._saveState) || _hasPendingPdfPaneSave(id)) {
          _cancelAutoSave(id);
          _cancelPdfPaneSave(id);
          _saveClosingDraft(id, doc);
        } else if (doc._saveState === 'saving') {
          retainClosingDraft(id, doc);
        } else {
          _cancelAutoSave(id);
          _cancelPdfPaneSave(id);
        }
      }
    }

    // Clear docs from other sessions so tabs are per-session,
    // but keep session-less docs (e.g. email compose) - they are independent
    for (const [id, doc] of [...docs]) {
      if (doc.sessionId && doc.sessionId !== sessionId) removeDocument(id);
    }
    _releasePdfPane(activeDocId);
    setActiveDocId(null);

    // Show loading state while fetching
    if (isOpen) _showLoadingOverlay();

    try {
      const res = await fetch(`${API_BASE}/api/documents/${sessionId}`, {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!res.ok) throw new Error(`Session documents failed: HTTP ${res.status}`);
      const allDocs = await res.json();
      if (generation !== _documentNavigationGeneration) return;
      _hideLoadingOverlay();
      // Only load active docs
      const activeDocs = allDocs.filter(d => d.is_active);
      if (activeDocs.length === 0) {
        // No docs yet — show empty editor, doc will be created when user types
        if (!restoreMode || shouldRestoreOpen) {
          if (!isOpen) openPanel();
          showEmptyState();
          renderTabs();
        }
        return;
      }
      for (const doc of activeDocs) {
        // Always rehydrate from the canonical DB row. A cached tab shell can
        // survive panel/session restoration with an empty or stale `content`
        // field; skipping existing IDs then shows the document but not its
        // body after refresh.
        addDocToTabs(doc, sessionId);
      }
      _syncDocIndicator();
      // Restore the exact document that was active in this conversation.
      // Keeping only the panel-open bit made a hard refresh reopen the editor
      // on activeDocs[0], silently severing follow-up edits from the document
      // the user had actually been discussing.
      const rememberedDocId = localStorage.getItem(_docActiveKey(sessionId));
      const target = activeDocs.find(doc => doc.id === rememberedDocId) || activeDocs[0];
      if (rememberedDocId && target.id !== rememberedDocId) {
        localStorage.removeItem(_docActiveKey(sessionId));
      }
      if (restoreMode && !shouldRestoreOpen) {
        // Coming back to a chat with documents should advertise the doc without
        // stealing half the screen. Default to a docked chip; only reopen the
        // full editor when this session explicitly persisted an open state.
        setActiveDocId(target.id);
        setMinimizedDocId(target.id);
        _markDocVisibleState(sessionId, 'minimized');
        _ensureDocChipRegistered();
        if (isOpen) {
          try { switchToDoc(target.id); } catch (e) { console.error('Minimize restored doc failed:', e); }
          closePanel('down');
        } else {
          Modals.minimize('doc-panel');
        }
        return;
      }
      _markDocVisibleState(sessionId, 'open');
      if (!isOpen) openPanel({ restore: restoreMode && shouldRestoreOpen });
      switchToDoc(target.id);
    } catch (e) {
      if (generation !== _documentNavigationGeneration) return;
      _hideLoadingOverlay();
      console.error('Failed to load session documents:', e);
      // Open empty panel on error too
      if (!isOpen) openPanel();
      showEmptyState();
    }
  }

let _selections = [];

const _richSelectionHighlightName = 'doc-ai-selections';

function _richRootText(rich) {
    if (!rich) return '';
    const range = document.createRange();
    range.selectNodeContents(rich);
    return range.toString();
  }

function _richRangeFromOffsets(rich, start, end) {
    if (!rich || start < 0 || end <= start) return null;
    const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
    let offset = 0;
    let startNode = null, startOffset = 0, endNode = null, endOffset = 0;
    let node;
    while ((node = walker.nextNode())) {
      const length = (node.nodeValue || '').length;
      if (!startNode && start >= offset && start < offset + length) {
        startNode = node;
        startOffset = start - offset;
      }
      if (end > offset && end <= offset + length) {
        endNode = node;
        endOffset = end - offset;
        break;
      }
      offset += length;
    }
    if (!startNode || !endNode) return null;
    const range = document.createRange();
    range.setStart(startNode, startOffset);
    range.setEnd(endNode, endOffset);
    return range;
  }

function updateRichSelectionState(rich) {
    if (!rich || !rich.isConnected || rich.style.display === 'none') return;
    const selection = window.getSelection();
    if (!selection?.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    if (!rich.contains(range.commonAncestorContainer)) return;

    const selectedText = range.toString();
    if (!selectedText.trim()) return;
    const prefix = document.createRange();
    prefix.selectNodeContents(rich);
    prefix.setEnd(range.startContainer, range.startOffset);
    const start = prefix.toString().length;
    const end = start + selectedText.length;
    const fullText = _richRootText(rich);
    const startLine = fullText.slice(0, start).split('\n').length;
    const endLine = fullText.slice(0, end).split('\n').length;
    const entry = { kind: 'rich', text: selectedText, startLine, endLine, start, end };
    const overlapIdx = _selections.findIndex(s => s.kind === 'rich' && (
      (start >= s.start && start <= s.end) || (end >= s.start && end <= s.end)
      || (start <= s.start && end >= s.end)
    ));
    if (overlapIdx >= 0) _selections[overlapIdx] = entry;
    else _selections.push(entry);
    showSelectionBadge();
    renderAllSelectionHighlights();
  }

let _selResizeScheduled = false;

function _scheduleSelRerender() {
    if (_selResizeScheduled || _selections.length === 0) return;
    _selResizeScheduled = true;
    requestAnimationFrame(() => {
      _selResizeScheduled = false;
      try { renderAllSelectionHighlights(); } catch (_) {}
    });
  }

if (typeof window !== 'undefined') {
    window.addEventListener('resize', _scheduleSelRerender);
  }

let _selResizeObserver = null;

function _ensureSelResizeObserver() {
    if (_selResizeObserver || typeof ResizeObserver === 'undefined') return;
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    _selResizeObserver = new ResizeObserver(_scheduleSelRerender);
    _selResizeObserver.observe(ta);
  }

function updateSelectionState() {
    // The mirror measurement below uses the textarea's live computed metrics,
    // so pinned selections remain valid with wrapped lines, larger font sizes,
    // mobile widths, and non-fullscreen panes. Older code disabled selection
    // whenever wrapping was detected; increasing the document font made that
    // path fire constantly, so selecting text appeared to stop working.
    _ensureSelResizeObserver();
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;

    if (start === end) {
      // Simple click — don't clear, user might be clicking into chat
      return;
    }

    const text = textarea.value;
    const selectedText = text.substring(start, end);
    const startLine = text.substring(0, start).split('\n').length;
    const endLine = text.substring(0, end).split('\n').length;

    // Check for overlap with existing selection — replace if overlapping
    const overlapIdx = _selections.findIndex(s =>
      (start >= s.start && start <= s.end) || (end >= s.start && end <= s.end) ||
      (start <= s.start && end >= s.end)
    );
    const entry = { text: selectedText, startLine, endLine, start, end };
    if (overlapIdx >= 0) {
      _selections[overlapIdx] = entry;
    } else {
      _selections.push(entry);
    }

    showSelectionBadge();
    renderAllSelectionHighlights();
  }

function showSelectionBadge() {
    let badge = document.getElementById('doc-selection-badge');
    if (!badge) {
      badge = document.createElement('span');
      badge.id = 'doc-selection-badge';
      badge.className = 'doc-selection-badge';
      badge.title = 'Selected regions — type in chat to edit';
      // Sits directly under the formatting toolbar so it reads as part
      // of the toolbar row, not buried in the page header. Falls back
      // to the editor header if the toolbar isn't on screen.
      const toolbar = document.getElementById('doc-md-toolbar');
      if (toolbar && toolbar.parentNode) {
        toolbar.insertAdjacentElement('afterend', badge);
      } else {
        const header = document.querySelector('.doc-editor-header');
        if (header) header.insertBefore(badge, header.firstChild);
      }
    }
    if (_selections.length === 0) {
      badge.style.display = 'none';
      return;
    }
    const labels = _selections.map(s => s.kind === 'rich'
      ? 'Text'
      : (s.startLine === s.endLine ? `L${s.startLine}` : `L${s.startLine}-${s.endLine}`));
    badge.replaceChildren();
    if (_selections.length === 1) {
      badge.append(document.createTextNode(`${labels[0]} selected`));
    } else {
      badge.append(document.createTextNode(`${_selections.length} selections`));
      labels.forEach((selectionLabel, index) => {
        const chip = document.createElement('span');
        chip.className = 'doc-selection-chip';
        chip.textContent = selectionLabel;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'doc-selection-chip-clear';
        remove.title = `Remove ${selectionLabel} selection`;
        remove.setAttribute('aria-label', `Remove ${selectionLabel} selection`);
        remove.textContent = '×';
        remove.addEventListener('click', (e) => {
          e.stopPropagation();
          clearSelectionAt(index);
        });
        chip.appendChild(remove);
        badge.appendChild(chip);
      });
    }
    const clearAll = document.createElement('button');
    clearAll.className = 'doc-selection-clear doc-selection-chip-clear';
    clearAll.title = 'Clear Selection';
    clearAll.setAttribute('aria-label', 'Clear Selection');
    clearAll.textContent = 'Clear Selection';
    badge.appendChild(clearAll);
    badge.style.display = '';
    clearAll.addEventListener('click', (e) => {
      e.stopPropagation();
      clearSelection();
    });
  }

function _isCodeDoc() {
    const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
    if (!lang) return false;
    // Prose / preview types that should get character-precise highlights.
    const prose = new Set(['markdown', 'md', 'text', 'txt', 'email', 'html', 'csv']);
    return !prose.has(lang);
  }

function _measurePos(mirror, text, pos) {
    mirror.innerHTML = '';
    if (pos > 0) mirror.appendChild(document.createTextNode(text.substring(0, pos)));
    const marker = document.createElement('span');
    marker.textContent = '​';
    mirror.appendChild(marker);
    const r = marker.getBoundingClientRect();
    const m = mirror.getBoundingClientRect();
    return { x: r.left - m.left, y: r.top - m.top };
  }

function _validateSelections(text) {
    if (_selections.length === 0) return;
    const rich = _emailRichbodyActive();
    const richText = rich ? _richRootText(rich) : '';
    const survivors = [];
    for (const s of _selections) {
      const captured = s.text || '';
      if (!captured) continue;
      const source = s.kind === 'rich' ? richText : text;
      if (s.kind === 'rich' && !rich) continue;
      // Fast path: still at the same offsets.
      if (source.substring(s.start, s.end) === captured) {
        survivors.push(s);
        continue;
      }
      // Re-anchor: find the captured text and pick the occurrence
      // nearest to the old start so multi-match docs don't snap to the
      // wrong one. indexOf scans are cheap for typical doc sizes.
      let best = -1, bestDist = Infinity;
      let from = 0;
      while (true) {
        const idx = source.indexOf(captured, from);
        if (idx === -1) break;
        const dist = Math.abs(idx - s.start);
        if (dist < bestDist) { best = idx; bestDist = dist; }
        from = idx + 1;
      }
      if (best === -1) continue;  // text gone entirely → drop
      const newStart = best;
      const newEnd = best + captured.length;
      survivors.push({
        ...s,
        start: newStart,
        end: newEnd,
        startLine: source.substring(0, newStart).split('\n').length,
        endLine: source.substring(0, newEnd).split('\n').length,
      });
    }
    _selections = survivors;
  }

function renderAllSelectionHighlights() {
    document.querySelectorAll('.doc-selection-rich-clear').forEach(el => el.remove());
    // Delete the persistent CSS highlight before checking whether any
    // selections remain; otherwise clearing the last selection leaves the
    // painted range visible until the next render.
    try { CSS.highlights?.delete(_richSelectionHighlightName); } catch (_) {}
    const wrap = document.getElementById('doc-editor-wrap');
    if (!wrap) return;
    // Remove old overlays
    wrap.querySelectorAll('.doc-selection-overlay').forEach(el => el.remove());

    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea || _selections.length === 0) return;

    const text = textarea.value;
    // Pre-render guard: re-anchor or drop selections whose text has
    // shifted (undo, programmatic edits, etc.) so the overlays never
    // draw on the wrong region.
    _validateSelections(text);
    const rich = _emailRichbodyActive();
    const richRanges = rich
      ? _selections.filter(s => s.kind === 'rich').map(s => _richRangeFromOffsets(rich, s.start, s.end)).filter(Boolean)
      : [];
    if (richRanges.length) {
      try {
        if (CSS.highlights && typeof Highlight === 'function') {
          CSS.highlights.set(_richSelectionHighlightName, new Highlight(...richRanges));
        }
      } catch (_) {}
      const richSelections = _selections.filter(s => s.kind === 'rich');
      richRanges.forEach((range, rangeIndex) => {
        const rects = Array.from(range.getClientRects());
        const rect = rects[rects.length - 1];
        if (!rect || (!rect.width && !rect.height)) return;
        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.className = 'doc-selection-rich-clear';
        clearBtn.title = 'Remove this selection';
        clearBtn.setAttribute('aria-label', 'Remove this selection');
        clearBtn.textContent = '×';
        clearBtn.style.left = `${Math.max(4, rect.right - 10)}px`;
        clearBtn.style.top = `${Math.max(4, rect.top - 6)}px`;
        const selectionIndex = _selections.indexOf(richSelections[rangeIndex]);
        clearBtn.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          clearSelectionAt(selectionIndex);
        });
        document.body.appendChild(clearBtn);
      });
    }
    const sourceSelections = _selections.filter(s => s.kind !== 'rich');
    if (_selections.length === 0) { showSelectionBadge(); return; }
    if (sourceSelections.length === 0) return;
    const style = getComputedStyle(textarea);
    const paddingTop = parseFloat(style.paddingTop) || 10;
    const paddingLeft = parseFloat(style.paddingLeft) || 48;
    const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);

    // Shared mirror for measurement — same box model as the textarea
    // so any measurement we take lines up 1:1 with the rendered text.
    let mirror = document.getElementById('doc-selection-mirror');
    if (!mirror) {
      mirror = document.createElement('div');
      mirror.id = 'doc-selection-mirror';
      // box-sizing:border-box is critical — without it the mirror's
      // actual box width = (width prop) + horizontal padding, which is
      // wider than the textarea's text-render area. Text wraps at a
      // different column inside the mirror, so every measured y-offset
      // drifts from where the real text sits. border-box makes
      // mirror.box = textarea.clientWidth exactly.
      mirror.style.cssText = 'position:absolute;top:0;left:0;right:0;visibility:hidden;pointer-events:none;' +
        'white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word;overflow:hidden;box-sizing:border-box;';
      wrap.appendChild(mirror);
    }
    mirror.style.font = style.font;
    mirror.style.padding = style.padding;
    mirror.style.borderWidth = style.borderWidth;
    mirror.style.borderStyle = 'solid';
    mirror.style.borderColor = 'transparent';
    mirror.style.width = textarea.clientWidth + 'px';
    mirror.style.tabSize = style.tabSize;
    mirror.style.letterSpacing = style.letterSpacing;
    mirror.style.wordSpacing = style.wordSpacing;
    mirror.style.textIndent = style.textIndent;

    const codeDoc = _isCodeDoc();
    const scrollTop = textarea.scrollTop;

    for (const sel of sourceSelections) {
      const selectionIndex = _selections.indexOf(sel);
      if (codeDoc) {
        // Line-based: span every line that contains any selected char.
        const beforeStart = text.substring(0, sel.start);
        const lastNewline = beforeStart.lastIndexOf('\n');
        const startLineBegin = lastNewline + 1;
        mirror.textContent = text.substring(0, startLineBegin);
        const startTop = mirror.scrollHeight - paddingTop;

        const afterEnd = text.indexOf('\n', sel.end);
        const endLineEnd = afterEnd === -1 ? text.length : afterEnd;
        mirror.textContent = text.substring(0, endLineEnd);
        const endBottom = mirror.scrollHeight - paddingTop;

        mirror.textContent = '';

        const top = paddingTop + startTop - scrollTop;
        const height = endBottom - startTop || lineHeight;
        const overlay = document.createElement('div');
        overlay.className = 'doc-selection-overlay';
        overlay.style.top = top + 'px';
        overlay.style.left = paddingLeft + 'px';
        overlay.style.right = '0';
        overlay.style.height = height + 'px';
        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.className = 'doc-selection-overlay-clear';
        clearBtn.title = 'Remove this selection';
        clearBtn.setAttribute('aria-label', 'Remove this selection');
        clearBtn.textContent = '×';
        clearBtn.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          clearSelectionAt(selectionIndex);
        });
        overlay.appendChild(clearBtn);
        wrap.appendChild(overlay);
      } else {
        // Character-precise: measure the actual selection start/end via
        // a marker span. Render one rect for single-line selections, or
        // three rects (first partial, middle full, last partial) for
        // multi-line selections.
        const startPos = _measurePos(mirror, text, sel.start);
        const endPos = _measurePos(mirror, text, sel.end);
        mirror.innerHTML = '';

        const addRect = (top, left, width, height, withClear = false) => {
          const overlay = document.createElement('div');
          overlay.className = 'doc-selection-overlay';
          overlay.style.top = (paddingTop + top - scrollTop) + 'px';
          overlay.style.left = (paddingLeft + left) + 'px';
          if (width != null) overlay.style.width = width + 'px';
          else overlay.style.right = '0';
          overlay.style.height = height + 'px';
          if (withClear) {
            const clearBtn = document.createElement('button');
            clearBtn.type = 'button';
            clearBtn.className = 'doc-selection-overlay-clear';
            clearBtn.title = 'Remove this selection';
            clearBtn.setAttribute('aria-label', 'Remove this selection');
            clearBtn.textContent = '×';
            clearBtn.addEventListener('click', (event) => {
              event.preventDefault();
              event.stopPropagation();
              clearSelectionAt(selectionIndex);
            });
            overlay.appendChild(clearBtn);
          }
          wrap.appendChild(overlay);
        };

        if (Math.abs(endPos.y - startPos.y) < 1) {
          // Single visual line.
          addRect(startPos.y, startPos.x, endPos.x - startPos.x, lineHeight, true);
        } else {
          // First line: from selection start to right edge.
          addRect(startPos.y, startPos.x, null, lineHeight, true);
          // Middle lines (if any): full-width band between the two.
          const middleTop = startPos.y + lineHeight;
          const middleHeight = endPos.y - middleTop;
          if (middleHeight > 0) addRect(middleTop, 0, null, middleHeight);
          // Last line: from left edge to selection end.
          addRect(endPos.y, 0, endPos.x, lineHeight);
        }
      }
    }
  }

function syncSelectionOverlay() {
    if (_selections.length === 0) return;
    renderAllSelectionHighlights();
  }

function clearSelection({ preserveCaret = false } = {}) {
    _selections = [];
    try { CSS.highlights?.delete(_richSelectionHighlightName); } catch (_) {}
    document.querySelectorAll('.doc-selection-rich-clear').forEach(el => el.remove());
    // A restored rich-text reference also creates a native browser range so
    // the referenced text is visibly selected. Clear that range with the
    // pinned selection; otherwise document stats keep reporting "selected"
    // after the badge's X has cleared the actual AI-edit context.
    const rich = _emailRichbodyActive();
    const browserSelection = window.getSelection?.();
    // Input already placed the caret after the edit. Clearing pinned AI
    // context must not discard that live insertion point.
    if (!preserveCaret && rich && browserSelection?.rangeCount
        && (rich.contains(browserSelection.anchorNode) || rich.contains(browserSelection.focusNode))) {
      browserSelection.removeAllRanges();
    }
    const badge = document.getElementById('doc-selection-badge');
    if (badge) badge.style.display = 'none';
    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap) wrap.querySelectorAll('.doc-selection-overlay').forEach(el => el.remove());
    _scheduleDocumentStats();
  }

function clearSelectionAt(index) {
    if (index < 0 || index >= _selections.length) return;
    const removed = _selections[index];
    _selections.splice(index, 1);
    if (removed?.kind === 'rich') {
      const rich = _emailRichbodyActive();
      const browserSelection = window.getSelection?.();
      if (rich && browserSelection?.rangeCount
          && (rich.contains(browserSelection.anchorNode) || rich.contains(browserSelection.focusNode))) {
        browserSelection.removeAllRanges();
      }
    }
    renderAllSelectionHighlights();
    showSelectionBadge();
    _scheduleDocumentStats();
  }

export function getSelectionContext() {
    if (_selections.length === 0) return null;
    // Re-anchor / drop stale selections before handing them to chat —
    // shipping text from a stale offset would mean the AI sees content
    // from a different region than what the user thinks they highlighted.
    const _ta = document.getElementById('doc-editor-textarea');
    if (_ta) _validateSelections(_ta.value);
    if (_selections.length === 0) return null;
    if (_selections.length === 1) {
      const ctx = _selections[0];
      clearSelection();
      return ctx;
    }
    // Multiple selections — return array
    const ctx = [..._selections];
    clearSelection();
    return ctx;
  }

export async function restoreSelectionReference(reference, options = {}) {
    const requestedDocId = String(options.documentId || '').trim();
    if (requestedDocId) await loadDocument(requestedDocId);
    if (!activeDocId || !docs.has(activeDocId)) return false;

    _ensureDocPaneMounted();
    const doc = docs.get(activeDocId);
    const rich = _isRichTextLang(doc.language) ? _emailRichbodyActive() : null;
    const textarea = document.getElementById('doc-editor-textarea');
    const source = rich ? _richRootText(rich) : (textarea?.value || doc.content || '');
    const supplied = Array.isArray(options.selections)
      ? options.selections
      : (options.selections ? [options.selections] : []);
    const restored = [];

    for (const selection of supplied) {
      const selectedText = String(selection?.text || '');
      if (!selectedText) continue;
      const start = source.indexOf(selectedText);
      if (start < 0) continue;
      const end = start + selectedText.length;
      restored.push({
        kind: rich ? 'rich' : undefined,
        text: selectedText,
        start,
        end,
        startLine: source.slice(0, start).split('\n').length,
        endLine: source.slice(0, end).split('\n').length,
      });
    }

    if (!restored.length) {
      const lineMatches = Array.from(String(reference || '').matchAll(/(?:L|lines?)\s*(\d+)(?:\s*[-–]\s*(\d+))?/gi));
      const lines = source.split('\n');
      const lineStart = lineNumber => {
        let offset = 0;
        for (let index = 1; index < lineNumber && index <= lines.length; index++) {
          offset += lines[index - 1].length + 1;
        }
        return offset;
      };
      for (const match of lineMatches) {
        const startLine = Math.max(1, Math.min(lines.length, Number(match[1]) || 1));
        const endLine = Math.max(startLine, Math.min(lines.length, Number(match[2]) || startLine));
        const start = lineStart(startLine);
        const end = lineStart(endLine) + (lines[endLine - 1] || '').length;
        if (end <= start) continue;
        restored.push({
          kind: rich ? 'rich' : undefined,
          text: source.slice(start, end),
          start,
          end,
          startLine,
          endLine,
        });
      }
    }
    if (!restored.length) return false;

    _selections = restored;
    showSelectionBadge();
    renderAllSelectionHighlights();
    const first = restored[0];
    if (rich) {
      const range = _richRangeFromOffsets(rich, first.start, first.end);
      const browserSelection = window.getSelection?.();
      if (range && browserSelection) {
        browserSelection.removeAllRanges();
        browserSelection.addRange(range);
        range.startContainer.parentElement?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      }
      rich.focus({ preventScroll: true });
    } else if (textarea) {
      textarea.focus({ preventScroll: true });
      textarea.setSelectionRange(first.start, first.end, 'forward');
      const lineHeight = parseFloat(getComputedStyle(textarea).lineHeight) || 18;
      textarea.scrollTop = Math.max(0, (first.startLine - 2) * lineHeight);
      syncSelectionOverlay();
    }
    return true;
  }

function runDocument() {
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;

    const code = textarea.value;
    const langSelect = document.getElementById('doc-language-select');
    const lang = (langSelect ? langSelect.value : '').toLowerCase();

    // Get or create the output panel below the editor
    let outputPanel = document.getElementById('doc-run-output');
    if (!outputPanel) {
      outputPanel = document.createElement('div');
      outputPanel.id = 'doc-run-output';
      outputPanel.className = 'doc-run-output';
      const editorWrap = document.getElementById('doc-editor-wrap');
      if (editorWrap) editorWrap.after(outputPanel);
    }
    outputPanel.style.display = 'block';
    outputPanel.innerHTML = '';

    if (!code.trim()) {
      outputPanel.innerHTML = '<pre class="doc-run-error">Nothing to run. Add some code first.</pre>';
      return;
    }

    if (_isRenderLang(lang)) {
      // HTML / SVG / XML — render inline in the sandboxed preview iframe.
      outputPanel.style.display = 'none';
      toggleHtmlPreview();
      return;
    }

    if (!codeRunnerModule) {
      outputPanel.innerHTML = '<pre class="doc-run-error">Code runner not loaded</pre>';
      setTimeout(() => { if (outputPanel) outputPanel.style.display = 'none'; }, 5000);
      return;
    }

    if (lang === 'bash' || lang === 'sh' || lang === 'shell' || lang === 'zsh') {
      codeRunnerModule.runServer(code, outputPanel, 'bash');
      return;
    }

    if (lang === 'python' || lang === 'py') {
      codeRunnerModule.runServer(code, outputPanel, 'python');
      return;
    }

    if (lang === 'javascript' || lang === 'js') {
      codeRunnerModule.runJavaScript(code, outputPanel);
      return;
    }

    outputPanel.innerHTML = '<pre class="doc-run-error">Unsupported language. Supported: bash, python, javascript, html</pre>';
    setTimeout(() => { if (outputPanel) outputPanel.style.display = 'none'; }, 5000);
  }

async function copyDocument() {
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea || !textarea.value) return;
    if (uiModule && uiModule.copyToClipboard) {
      await uiModule.copyToClipboard(textarea.value);
    } else {
      try {
        await navigator.clipboard.writeText(textarea.value);
      } catch (e) { /* ignore */ }
    }
    if (uiModule) uiModule.showToast('Copied to clipboard');
  }

let _docTabMenu = null;

function _closeDocTabMenu() {
    if (_docTabMenu) { _docTabMenu.style.display = 'none'; }
  }

if (!window._documentInnerEscapeBound) {
    window._documentInnerEscapeBound = true;
    window.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || !isOpen) return;

      // If email is the visible foreground window, its own inner Escape
      // handler owns the event. A document selection behind it should not
      // intercept the email reader's Escape key.
      const emailModal = document.getElementById('email-lib-modal');
      const target = e.target;
      if (emailModal && !emailModal.classList.contains('hidden')
          && !target?.closest?.('.doc-editor-pane, #doc-rich-selection-toolbar, #doc-selection-badge, #doc-md-dd-menu')) {
        return;
      }

      // A pinned text selection is the user's most local Escape target. Clear
      // it before closing any menu, toolbar, or the document itself.
      if (_selections.length > 0) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        clearSelection();
        return;
      }

      const versionPanel = document.getElementById('doc-version-panel');
      if (versionPanel && !versionPanel.classList.contains('hidden')) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _closeVersionPanel({ restoreFocus: true });
        return;
      }

      // Menus are transient and must close before selection state. The stack
      // also covers color pickers and other document dropdowns.
      if (dismissTopMenu()) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        return;
      }
      const menu = document.getElementById('doc-md-dd-menu');
      const menuVisible = menu && !menu.hidden && getComputedStyle(menu).display !== 'none';
      if (menuVisible) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        menu._dismiss?.(true);
        return;
      }
      if (_docTabMenu && _docTabMenu.style.display === 'block') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _closeDocTabMenu();
        return;
      }
      if (_richSlashMenu) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _hideRichSlashMenu();
        return;
      }
      if (_richSelectionToolbar) {
        const rich = _emailRichbodyActive();
        const selection = window.getSelection?.();
        if (rich && selection && !selection.isCollapsed
            && rich.contains(selection.anchorNode) && rich.contains(selection.focusNode)) {
          selection.collapseToEnd();
        }
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _hideRichSelectionToolbar();
        return;
      }
    }, true);
  }

async function _moveDocToSession(docId, sessionId, { switchChat = false } = {}) {
    const doc = docs.get(docId);
    if (!doc || !sessionId) return false;
    if (docId === activeDocId) {
      try { await saveDocument({ silent: true }); } catch (_) {}
    }
    const res = await fetch(`${API_BASE}/api/document/${encodeURIComponent(docId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!res.ok) throw new Error(`Move failed: HTTP ${res.status}`);
    const updated = await res.json();
    doc.sessionId = updated.session_id || sessionId;
    doc.title = updated.title ?? doc.title;
    doc.language = updated.language ?? doc.language;
    doc.content = updated.current_content ?? doc.content;
    registerDocument(doc);
    setLastSessionId(sessionId);
    try { sessionModule?.setSessionHasDocs?.(sessionId, true); } catch (_) {}
    if (switchChat && sessionModule?.selectSession) {
      await sessionModule.selectSession(sessionId);
      addDocToTabs(updated, sessionId);
      switchToDoc(docId);
      if (!isOpen) openPanel();
    } else {
      renderTabs();
    }
    _syncDocIndicator();
    return true;
  }

async function moveActiveDocumentToCurrentChat({ quiet = false } = {}) {
    if (!activeDocId) return;
    let sessionId = sessionModule?.getCurrentSessionId?.() || '';
    if (!sessionId) sessionId = await _autoCreateSession();
    try {
      await _moveDocToSession(activeDocId, sessionId);
      if (!quiet) uiModule?.showToast?.('Document moved to current chat');
    } catch (e) {
      console.error('Failed to move document to current chat:', e);
      uiModule?.showError?.('Could not move document to current chat');
    }
  }

async function moveActiveDocumentToNewChat() {
    if (!activeDocId) return;
    try {
      // Do not adopt the empty destination before the active document has
      // saved and moved. Switching currentSessionId early made saveDocument
      // operate against the wrong chat and aborted the move client-side.
      const sessionId = await _autoCreateSession({ adopt: false, forceNew: true });
      await _moveDocToSession(activeDocId, sessionId, { switchChat: true });
      uiModule?.showToast?.('Document moved to new chat');
    } catch (e) {
      console.error('Failed to move document to new chat:', e);
      uiModule?.showError?.('Could not move document to new chat');
    }
  }

function showDocTabMenu(btnEl, docId) {
    // Toggle off if already open for this doc
    if (_docTabMenu && _docTabMenu.style.display === 'block' && _docTabMenu._docId === docId) {
      _closeDocTabMenu();
      return;
    }

    // Capture button position before any DOM changes
    const _menuAnchorRect = btnEl.getBoundingClientRect();

    // Switch to this doc if not already active
    if (docId !== activeDocId) switchToDoc(docId);

    const doc = docs.get(docId);
    if (!doc) return;

    // Create singleton menu container once
    if (!_docTabMenu) {
      _docTabMenu = document.createElement('div');
      _docTabMenu.className = 'doc-tab-dropdown';
      _docTabMenu.style.cssText = 'position:fixed;z-index:1000;min-width:0;width:max-content;padding:4px;background:var(--panel);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);backdrop-filter:blur(12px);font-size:12px;display:none;';
      document.body.appendChild(_docTabMenu);
      // Close on outside click
      document.addEventListener('click', (e) => {
        if (_docTabMenu && !_docTabMenu.contains(e.target) && !e.target.closest('.doc-tab-menu-btn')) {
          _closeDocTabMenu();
        }
      });
      document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || !_docTabMenu || _docTabMenu.style.display !== 'block') return;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _closeDocTabMenu();
      }, true);
    }

    const lang = (doc.language || '').toLowerCase();
    const canRun = _isRenderLang(lang) || ['javascript', 'js', 'python', 'py', 'bash', 'sh', 'shell', 'zsh'].includes(lang);

    let previewIcon = '', previewLabel = '';
    const _mdPreview = document.getElementById('doc-md-preview');
    const _csvPreview = document.getElementById('doc-csv-preview');
    const _htmlPreview = document.getElementById('doc-html-preview');
    const _mdActive = _mdPreview && _mdPreview.style.display !== 'none';
    const _csvActive = _csvPreview && _csvPreview.style.display !== 'none';
    const _htmlActive = _htmlPreview && _htmlPreview.style.display !== 'none';
    if (lang === 'markdown') { previewIcon = 'MD'; previewLabel = _mdActive ? 'Edit' : 'Preview'; }
    else if (lang === 'csv') { previewIcon = '⊞'; previewLabel = _csvActive ? 'Edit' : 'Table View'; }
    else if (_isRenderLang(lang)) { previewIcon = '▶'; previewLabel = _htmlActive ? 'Edit' : 'Run / Preview'; }
    else if (_isDocxLang(lang)) { previewIcon = 'W'; previewLabel = 'Word Preview'; }

    const _di = (svg) => `<span class="dropdown-icon">${svg}</span>`;
    const _saveIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>';
    const _copyIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    const _runIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    const _previewIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    const _deleteIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
    const _moveIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v3"/><path d="M13 17h8"/><path d="m17 13 4 4-4 4"/><path d="M3 9v8a2 2 0 0 0 2 2h6"/></svg>';
    const _newChatIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h9"/><path d="M19 3v6"/><path d="M16 6h6"/></svg>';

    let items = '';
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="save">${_di(_saveIco)}<span>Save</span></div>`;
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="copy">${_di(_copyIco)}<span>Copy</span></div>`;
    if (canRun) {
      items += `<div class="dropdown-item-compact doc-tab-action" data-action="run">${_di(_runIco)}<span>Run</span></div>`;
    }
    if (previewLabel) {
      items += `<div class="dropdown-item-compact doc-tab-action" data-action="preview"><span class="dropdown-icon">${previewIcon}</span><span>${previewLabel}</span></div>`;
    }
    const _downloadIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="download">${_di(_downloadIco)}<span>Download</span></div>`;
    items += `<div class="dropdown-divider"></div>`;
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="move-current">${_di(_moveIco)}<span>Move to current chat</span></div>`;
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="move-new">${_di(_newChatIco)}<span>Move to new chat</span></div>`;
    // "Send signed reply" — only if this doc was opened from an email attachment
    if (doc.sourceEmailUid && doc.sourceEmailFolder) {
      const _sendBackIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg>';
      items += `<div class="dropdown-item-compact doc-tab-action" data-action="signed-reply">${_di(_sendBackIco)}<span>Send signed reply</span></div>`;
    }
    const _closeIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="close">${_di(_closeIco)}<span>Close</span></div>`;
    items += `<div class="dropdown-divider"></div>`;
    items += `<div class="dropdown-item-compact doc-tab-action doc-tab-action-delete" data-action="delete">${_di(_deleteIco)}<span>Delete</span></div>`;

    _docTabMenu.innerHTML = items;
    _docTabMenu.style.display = 'block';
    _docTabMenu._docId = docId;

    // Position: anchor to the tab bar bottom, aligned to button horizontally
    const rect = _menuAnchorRect;
    const tabBar = document.getElementById('doc-tab-bar');
    const barBottom = tabBar ? tabBar.getBoundingClientRect().bottom : rect.bottom;
    _docTabMenu.style.position = 'fixed';
    _docTabMenu.style.zIndex = '1000';
    _docTabMenu.style.left = rect.left + 'px';
    _docTabMenu.style.top = (barBottom + 2) + 'px';

    // Clamp to viewport edges
    requestAnimationFrame(() => {
      const menuRect = _docTabMenu.getBoundingClientRect();
      if (menuRect.right > window.innerWidth - 8) {
        _docTabMenu.style.left = (window.innerWidth - menuRect.width - 8) + 'px';
      }
      if (menuRect.left < 8) {
        _docTabMenu.style.left = '8px';
      }
      if (menuRect.bottom > window.innerHeight - 8) {
        _docTabMenu.style.top = (barBottom - menuRect.height - 4) + 'px';
      }
    });

    // Wire action clicks
    _docTabMenu.querySelectorAll('.doc-tab-action').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const action = item.dataset.action;
        _closeDocTabMenu();
        switch (action) {
          case 'save': saveDocument({ docId }); break;
          case 'copy': copyDocument(); break;
          case 'run': runDocument(); break;
          case 'preview':
            if (lang === 'markdown') toggleMarkdownPreview();
            else if (lang === 'csv') toggleCsvPreview();
            else if (_isRenderLang(lang)) toggleHtmlPreview();
            else if (_isDocxLang(lang)) toggleDocxPreview();
            break;
          case 'download': {
            const btn = document.getElementById('doc-fontsize-btn') || document.getElementById('doc-language-select');
            showExportMenu(null, btn?.getBoundingClientRect());
            break;
          }
          case 'move-current': moveActiveDocumentToCurrentChat(); break;
          case 'move-new': moveActiveDocumentToNewChat(); break;
          case 'signed-reply': _sendSignedReply(docId); break;
          case 'close': closeTab(docId); break;
          case 'delete': deleteActiveDocument(); break;
        }
      });
    });
  }

async function deleteActiveDocument() {
    if (!activeDocId) return;
    const idToDelete = activeDocId;
    if (_inFlightDeletes.has(idToDelete)) return _inFlightDeletes.get(idToDelete);

    const deleteTask = (async () => {
      let hadPendingAutoSave = false;
      let pendingAutoSaveEntry = null;
      let hadPendingPdfSave = false;
      let wasDirty = false;
      let confirmed = false;
      try {
        const doc = docs.get(idToDelete);
        const name = doc ? doc.title : 'this document';
        const ok = uiModule && uiModule.styledConfirm
          ? await uiModule.styledConfirm(`Delete "${name}"?`, { confirmText: 'Delete', danger: true })
          : confirm(`Delete "${name}"?`);
        if (!ok) return;
        confirmed = true;

        pendingAutoSaveEntry = _pendingAutoSaves.get(idToDelete) || null;
        hadPendingAutoSave = Boolean(pendingAutoSaveEntry);
        hadPendingPdfSave = _hasPendingPdfPaneSave(idToDelete);
        wasDirty = (docs.get(idToDelete)?._saveState === 'dirty');
        _cancelAutoSave(idToDelete);
        _cancelPdfPaneSave(idToDelete);

        const targetDoc = docs.get(idToDelete) || _closingDocs.get(idToDelete);
        if (targetDoc?._saveQueueTail) {
          await targetDoc._saveQueueTail.catch(() => {});
        }
        const res = await fetch(`${API_BASE}/api/document/${idToDelete}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`Delete failed: HTTP ${res.status}`);

        markDeleted(idToDelete);
        _cleanupPdfDocState(idToDelete);
        // Remove tab
        const tab = document.querySelector(`.doc-tab[data-doc-id="${idToDelete}"]`);
        if (tab) tab.remove();
        _forgetActiveDoc(targetDoc?.sessionId || _lastSessionId, idToDelete);
        removeDocument(idToDelete);
        releaseClosingDraft(idToDelete);
        // Switch to another doc or close panel when deleting the currently viewed doc
        if (activeDocId === idToDelete) {
          const remaining = Array.from(docs.keys());
          if (remaining.length > 0) {
            switchToDoc(remaining[0]);
          } else {
            setActiveDocId(null);
            closePanel();
          }
        } else {
          renderTabs();
          _syncDocIndicator();
        }
        if (uiModule) uiModule.showToast('Document deleted');
      } catch (e) {
        console.error('Failed to delete document:', e);
        if (confirmed && docs.has(idToDelete) && !_deletedDocIds.has(idToDelete)) {
          if (hadPendingPdfSave) {
            _schedulePdfPaneSave(idToDelete);
          } else if (hadPendingAutoSave || wasDirty) {
            _scheduleAutoSave(idToDelete, 800, pendingAutoSaveEntry?.options || {});
          }
        }
        if (uiModule) uiModule.showError('Failed to delete document');
      } finally {
        endDelete(idToDelete);
      }
    })();

    beginDelete(idToDelete, deleteTask);
    return deleteTask;
  }

function toggleFullscreen() {
    const pane = document.getElementById('doc-editor-pane');
    const container = document.getElementById('chat-container');
    if (!pane) return;
    // Note: the divider stays in the DOM during fullscreen so its chevron can
    // act as the exit-fullscreen affordance (the CSS rule
    // `body:has(.doc-editor-pane.doc-fullscreen) .doc-divider-collapse` slides
    // it into a forced-inside position). Hiding the divider here would hide
    // the chevron with it.

    // Hide the tab bar during the layout shift so any in-flight smooth
    // scroll / reflow doesn't visibly "fly" the active tab across the
    // pane as it expands. Restored after the layout settles.
    const tabBar = document.getElementById('doc-tab-bar');
    if (tabBar) {
      tabBar.style.visibility = 'hidden';
      clearTimeout(tabBar._fsHideTimer);
      tabBar._fsHideTimer = setTimeout(() => {
        tabBar.style.visibility = '';
      }, 100);
    }

    if (pane.classList.contains('doc-fullscreen')) {
      pane.classList.remove('doc-fullscreen');
      if (container) container.style.display = '';
    } else {
      pane.classList.add('doc-fullscreen');
      if (container) container.style.display = 'none';
    }
    // Re-check md toolbar overflow after layout change
    const mdToolbar = document.getElementById('doc-md-toolbar');
    if (mdToolbar?._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
  }

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
              (!existingDoc.content || existingDoc.content.trim() === '')) {
            reuseId = existingId;
            break;
          }
        }
      }

      if (reuseId) docId = reuseId;
    }

    // Capture old content before updating the map
    const textarea = document.getElementById('doc-editor-textarea');
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
      doc.version = data.version || doc.version;
      if (data.title) doc.title = data.title;
      if (data.language) doc.language = data.language;
    } else {
      registerDocument({
        id: docId,
        title: data.title || '',
        language: data.language || '',
        content: newContent,
        version: data.version || 1,
        sessionId: sessionModule?.getCurrentSessionId() || '',
      });
    }

    _syncDocIndicator();

    // Auto-title from content if still "Untitled" and AI didn't provide a title
    if (!data.title) autoTitleFromContent(newContent, docId);

    if (!isOpen) openPanel();

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

    const badge = document.getElementById('doc-version-badge');
    const titleInput = document.getElementById('doc-title-input');
    const langSelect = document.getElementById('doc-language-select');

    // Re-enable editor if it was in empty state
    if (textarea) {
      textarea.disabled = false;
      _syncEditorPlaceholder();
    }
    if (badge) badge.textContent = `v${data.version || 1}`;
    if (data.title && titleInput) titleInput.value = data.title;
    // Set language from data, or fall back to what the doc already has (e.g. from streaming)
    const docLang = data.language || (docs.has(docId) && docs.get(docId).language) || '';
    if (docLang && langSelect) langSelect.value = docLang;
    if (!docLang) attemptAutoDetect();
    const isEmailUpdate = (docLang || '').toLowerCase() === 'email';
    const isRichTextUpdate = _isRichTextLang(docLang);
    const markdownPreviewWasVisible = _isMarkdownPreviewVisible();

    // The server has already saved doc_update. Show its current content now;
    // a temporary diff or typing animation makes a completed edit look pending.
    const isEdit = !isEmailUpdate && !isRichTextUpdate && isExistingDoc && oldContent && oldContent !== newContent && !streamingId;
    const updatedDocForRichText = isRichTextUpdate ? docs.get(docId) : null;
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
      setTimeout(attemptAutoDetect, 100);
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
        if (activeDocId !== docId || !isOpen) return;
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
        const csvPreview = document.getElementById('doc-csv-preview');
        if (csvPreview && csvPreview.style.display === 'none') toggleCsvPreview();
      });
    } else if (streamingId && finalLangLower === 'markdown') {
      requestAnimationFrame(() => _setMarkdownPreviewActive(true, { remember: true }));
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

let _versionClickOutside = null;

let _versionHistory = null;

let _versionHistoryGeneration = 0;

function _historyOwnsView(state) {
    return state && _versionHistory === state
      && state.generation === _versionHistoryGeneration
      && state.docId === activeDocId && isOpen
      && state.pane === document.getElementById('doc-editor-pane');
  }

async function toggleVersionHistory({ focus = false } = {}) {
    const panel = document.getElementById('doc-version-panel');
    if (!panel || !activeDocId) return;
    if (!panel.classList.contains('hidden')) {
      _closeVersionPanel();
      return;
    }
    if (_diffModeActive) exitDiffMode(true);
    saveCurrentToMap();
    const textarea = document.getElementById('doc-editor-textarea');
    const state = {
      docId: activeDocId, doc: docs.get(activeDocId),
      pane: document.getElementById('doc-editor-pane'),
      generation: ++_versionHistoryGeneration, operation: 0,
      previewing: false, readOnly: textarea?.readOnly || false,
    };
    _versionHistory = state;
    const sidebar = document.getElementById('sidebar');
    if (window.innerWidth > 768 && sidebar) {
      const atLeft = sidebar.classList.contains('right-side') || document.body.classList.contains('sidebar-collapsed');
      panel.style.left = atLeft ? '0' : sidebar.offsetWidth + 'px';
      panel.style.right = 'auto';
    } else {
      panel.style.left = '';
      panel.style.right = '';
      panel.style.top = '';
    }
    if (panel.parentElement !== document.body) document.body.appendChild(panel);
    panel.classList.remove('hidden');
    _versionClickOutside = event => {
      if (!panel.contains(event.target) && !event.target.closest('.doc-tab-version')) _closeVersionPanel();
    };
    document.addEventListener('click', _versionClickOutside, true);
    await loadVersionHistory(state);
    if (focus && _historyOwnsView(state)) (panel.querySelector('.doc-version-preview') || panel.querySelector('#doc-version-close'))?.focus({ preventScroll: true });
  }

function _closeVersionPanel({ restoreFocus = false } = {}) {
    const state = _versionHistory;
    const ownsView = _historyOwnsView(state);
    _versionHistory = null;
    ++_versionHistoryGeneration;
    document.getElementById('doc-version-panel')?.classList.add('hidden');
    if (ownsView) {
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        if (state.previewing) textarea.value = state.doc.content || '';
        textarea.readOnly = state.readOnly;
      }
      if (state.previewing) syncHighlighting();
    }
    if (_versionClickOutside) document.removeEventListener('click', _versionClickOutside, true);
    _versionClickOutside = null;
    if (restoreFocus && state) document.querySelector(`.doc-tab[data-doc-id="${CSS.escape(state.docId)}"] .doc-tab-version`)?.focus({ preventScroll: true });
  }

async function loadVersionHistory(state = _versionHistory) {
    if (!_historyOwnsView(state)) return;
    const list = document.getElementById('doc-version-list');
    if (!list) return;

    try {
      const res = await fetch(`${API_BASE}/api/document/${state.docId}/versions`);
      if (!res.ok) throw new Error(`History failed: HTTP ${res.status}`);
      const versions = await res.json();
      if (!_historyOwnsView(state)) return;

      // Build diff summaries between consecutive versions
      const diffs = [];
      for (let i = 0; i < versions.length; i++) {
        if (i < versions.length - 1) {
          diffs.push(_buildDiffSummary(versions[i + 1].content, versions[i].content));
        } else {
          diffs.push('');
        }
      }

      list.innerHTML = versions.map((v, i) => `
        <div class="doc-version-item" data-version="${v.version_number}">
          <div class="doc-version-info">
            <button type="button" class="doc-version-preview" aria-label="Preview version ${v.version_number}" aria-pressed="false"><span class="doc-version-num">v${v.version_number}</span></button>
            ${i === 0 ? '<span class="doc-version-latest">latest</span>' : `<span class="doc-version-source">${_escHtml(v.source)}</span><span class="doc-version-time">${v.created_at ? new Date(v.created_at).toLocaleString() : ''}</span>`}
          </div>
          ${v.summary ? `<div class="doc-version-summary">${_escHtml(v.summary)}</div>` : ''}
          ${diffs[i] ? `<div class="doc-version-diff">${diffs[i]}</div>` : ''}
          ${i > 0 ? `<button type="button" class="doc-version-restore" data-version="${v.version_number}" aria-label="Restore version ${v.version_number}">Restore</button>` : ''}
        </div>
      `).join('');

      // Wire restore buttons
      list.querySelectorAll('.doc-version-restore').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          restoreVersion(parseInt(btn.dataset.version));
        });
      });

      // Wire click to preview version + active state
      list.querySelectorAll('.doc-version-item').forEach(item => {
        item.addEventListener('click', (e) => {
          if (e.target.classList.contains('doc-version-restore')) return;
          // Toggle active state
          list.querySelectorAll('.doc-version-item.active').forEach(el => el.classList.remove('active'));
          item.classList.add('active');
          list.querySelectorAll('.doc-version-preview').forEach(button => button.setAttribute('aria-pressed', button.closest('.doc-version-item') === item ? 'true' : 'false'));
          previewVersion(parseInt(item.dataset.version));
        });
      });
    } catch (e) {
      if (!_historyOwnsView(state)) return;
      list.innerHTML = '<div style="padding:8px;opacity:0.5;">Failed to load versions</div>';
    }
  }

async function previewVersion(num) {
    const state = _versionHistory;
    if (!_historyOwnsView(state) || state.restoring) return;
    const operation = ++state.operation;
    const revision = state.doc._editRevision || 0;
    try {
      const res = await fetch(`${API_BASE}/api/document/${state.docId}/version/${num}`);
      if (!res.ok) throw new Error(`Preview failed: HTTP ${res.status}`);
      const ver = await res.json();
      if (!_historyOwnsView(state) || state.operation !== operation
          || (state.doc._editRevision || 0) !== revision) return;
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        state.previewing = true;
        textarea.readOnly = true;
        textarea.value = ver.content || '';
      }
      syncHighlighting();
    } catch (e) {
      if (_historyOwnsView(state) && state.operation === operation) uiModule?.showError('Failed to preview version');
    }
  }

async function restoreVersion(num) {
    const state = _versionHistory;
    if (!_historyOwnsView(state) || state.restoring) return;
    state.restoring = true;
    const operation = ++state.operation;
    const doc = state.doc;
    const revision = doc._editRevision || 0;
    const content = doc.content;
    _cancelAutoSave(state.docId);
    const previousSave = doc._saveQueueTail || Promise.resolve();
    let release;
    const turn = new Promise(resolve => { release = resolve; });
    doc._saveQueueTail = previousSave.catch(() => {}).then(() => turn);
    await previousSave.catch(() => {});
    try {
      if (!_historyOwnsView(state) || state.operation !== operation) return;
      const res = await fetch(`${API_BASE}/api/document/${state.docId}/restore/${num}`, { method: 'POST' });
      if (!res.ok) throw new Error(`Restore failed: HTTP ${res.status}`);
      const restored = await res.json();
      const unchanged = (doc._editRevision || 0) === revision && doc.content === content;
      doc.version = Math.max(doc.version || 1, restored.version_count || 1);
      if (unchanged) {
        doc.content = restored.current_content ?? restored.content ?? '';
        _setDocumentSaveState('saved', state.docId);
      } else {
        _setDocumentSaveState('dirty', state.docId);
        _scheduleAutoSave(state.docId, 0);
      }
      if (_historyOwnsView(state) && state.operation === operation) {
        state.previewing = false;
        const textarea = document.getElementById('doc-editor-textarea');
        if (textarea) textarea.readOnly = state.readOnly;
        if (unchanged) {
          if (doc.language === 'email') doc._skipLocalDraftOnce = true;
          _releasePdfPane(state.docId);
          switchToDoc(state.docId, { captureCurrent: false, preserveHistory: true });
        }
        await loadVersionHistory(state);
        uiModule?.showToast(`Restored to v${num}`);
      }
    } catch (e) {
      if (_historyOwnsView(state)) uiModule?.showError('Failed to restore version');
    } finally {
      state.restoring = false;
      release();
      if (['dirty', 'error'].includes(doc._saveState)) _scheduleAutoSave(state.docId, 800);
    }
  }

async function updateTitle(overrideDocId, overrideTitle) {
    const docId = overrideDocId || activeDocId;
    if (!docId) return;
    const title = overrideTitle || document.getElementById('doc-title-input')?.value;
    if (!title) return;
    try {
      await fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      if (docs.has(docId)) {
        docs.get(docId).title = title;
        renderTabs();
      }
    } catch (e) {
      console.error('Failed to update title:', e);
    }
  }

function autoTitleFromContent(content, docId) {
    const id = docId || activeDocId;
    if (!id) return;
    const doc = docs.get(id);
    if (!doc || (doc.title && doc.title !== '' && doc.title !== 'Untitled')) return;

    const text = (content || '').trimStart();
    if (!text) return;

    let title = null;

    // Markdown header: # Title
    const mdMatch = text.match(/^#{1,3}\s+(.+)/m);
    if (mdMatch) {
      title = mdMatch[1].trim();
    }

    // HTML heading: <h1>Title</h1>
    if (!title) {
      const htmlMatch = text.match(/<h[1-3][^>]*>([^<]+)<\/h[1-3]>/i);
      if (htmlMatch) title = htmlMatch[1].trim();
    }

    // First non-empty line as fallback (only if short enough to be a title)
    if (!title) {
      const firstLine = text.split('\n').find(l => l.trim().length > 0);
      if (firstLine) {
        const cleaned = firstLine.trim();
        if (cleaned.length <= 60 && cleaned.length >= 2) {
          title = cleaned;
        }
      }
    }

    if (!title) return;

    // Clean up: strip trailing punctuation like : or ...
    title = title.replace(/[:#*`]+$/g, '').trim();
    if (title.length > 50) title = title.slice(0, 48) + '...';
    if (!title) return;

    updateTitle(id, title);
    const titleInput = document.getElementById('doc-title-input');
    if (titleInput && id === activeDocId) titleInput.value = title;
  }

async function updateLanguage() {
    if (!activeDocId) return;
    const select = document.getElementById('doc-language-select');
    if (!select) return;
    try {
      await fetch(`${API_BASE}/api/document/${activeDocId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: select.value }),
      });
      if (docs.has(activeDocId)) {
        docs.get(activeDocId).language = select.value;
        renderTabs();
      }
    } catch (e) {
      console.error('Failed to update language:', e);
    }
  }

export function clearAll() {
    suspendSuggestions();
    bumpNavigationGeneration();
    _closeVersionPanel();
    clearDocuments();
    setActiveDocId(null);
    setLastSessionId('');
    if (isOpen) closePanel();
    _syncDocIndicator();
  }

export function getCurrentDocId() {
    return activeDocId;
  }

export function getChatDocumentId() {
    // A minimized document remains the document linked to this conversation.
    // On mobile, minimizing the sheet is the only way to reach the composer;
    // treating that layout action as unlinking erased the document exactly
    // when the user tried to say "edit this". A real tab close clears
    // `_minimizedDocId` and removes the document from `docs`.
    const pane = document.getElementById('doc-editor-pane');
    const style = pane ? window.getComputedStyle(pane) : null;
    const visiblyOpen = !!(
      activeDocId
      && pane?.isConnected
      && !(Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel'))
      && style?.display !== 'none'
      && style?.visibility !== 'hidden'
      && style?.opacity !== '0'
    );
    const minimizedId = (
      Modals.isRegistered('doc-panel')
      && Modals.isMinimized('doc-panel')
      && _minimizedDocId
    ) ? _minimizedDocId : null;
    const id = visiblyOpen ? activeDocId : minimizedId;
    return id && docs.has(id) ? id : null;
  }

try {
  bindDocumentCommands({
    openPanel,
    closePanel,
    switchToDoc,
    saveCurrentToMap,
    renderTabs,
    _syncDocIndicator,
    _syncHeaderActions,
    showEmptyState,
    _showLoadingOverlay,
    _hideLoadingOverlay,
    _minimizeNotesForDocumentOpen,
    _ensureDocChipRegistered,
  }, { idempotent: true });
} catch (e) {
  if (!String(e?.message).includes('already bound')) {
    throw e;
  }
}

try {
  bindPersistenceHooks({
    _hasPendingPdfPaneSave,
    _renderDocumentSaveState,
    getChatDocumentId,
    _cancelPdfPaneSave,
    _syncPdfPaneToDoc,
    _renderVersionBadge(docId, version) {
      const badge = document.getElementById('doc-version-badge');
      if (badge && activeDocId === docId) {
        const _v = version || 1;
        badge.textContent = `v${_v}`;
        badge.style.display = _v > 1 ? '' : 'none';
      }
    },
  }, { idempotent: true });
} catch (e) {
  if (!String(e?.message).includes('already bound')) {
    throw e;
  }
}


const _openEmailDraft = (...args) => globalThis.openEmailDraft?.(...args);
const _replaceEmailReplyBody = (...args) => globalThis.replaceEmailReplyBody?.(...args);
const _ensureEmailDraftEnvelope = (...args) => globalThis.ensureEmailDraftEnvelope?.(...args);
const _getActiveEmailComposerContext = (...args) => globalThis.getActiveEmailComposerContext?.(...args);
const _findEmailDocId = (...args) => globalThis.findEmailDocId?.(...args);
const _focusEmailReplyBody = (...args) => globalThis.focusEmailReplyBody?.(...args);
const _generateEmailReply = (...args) => globalThis.generateEmailReply?.(...args);

export {
  _openEmailDraft as openEmailDraft,
  _replaceEmailReplyBody as replaceEmailReplyBody,
  _ensureEmailDraftEnvelope as ensureEmailDraftEnvelope,
  _getActiveEmailComposerContext as getActiveEmailComposerContext,
  _findEmailDocId as findEmailDocId,
  _focusEmailReplyBody as focusEmailReplyBody,
  _generateEmailReply as generateEmailReply,
};

const documentModule = {
  init,
  openPanel,
  closePanel,
  swapSide,
  createDocument,
  newDocument,
  loadDocument,
  injectFreshDoc,
  replaceEmailReplyBody: _replaceEmailReplyBody,
  ensureEmailDraftEnvelope: _ensureEmailDraftEnvelope,
  openEmailDraft: _openEmailDraft,
  generateEmailReply: _generateEmailReply,
  ensurePaneMounted: _ensureDocPaneMounted,
  loadSessionDocs,
  ensureDocPanel,
  saveDocument,
  handleDocUpdate,
  handleDocSuggestions,
  streamDocOpen,
  streamDocDelta,
  streamDocFinalize,
  isPanelOpen,
  enterDiffMode,
  exitDiffMode,
  isDiffModeActive,
  getCurrentDocId,
  getChatDocumentId,
  getActiveEmailComposerContext: _getActiveEmailComposerContext,
  focusEmailReplyBody: _focusEmailReplyBody,
  moveActiveDocumentToCurrentChat,
  moveActiveDocumentToNewChat,
  findEmailDocId: _findEmailDocId,
  getSelectionContext,
  restoreSelectionReference,
  clearSelection,
  clearAll,
  openLibrary,
  closeLibrary,
  isLibraryOpen,
};

export { handleDocSuggestions, isPanelOpen, saveDocument };

export default documentModule;

window.documentModule = documentModule;