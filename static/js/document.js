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

import { _handleMarkdownPreviewClickHint, _lineNumberContentEl, _refreshMarkdownPreviewIfVisible, _replaceRange, attemptAutoDetect, syncGutterScroll, syncHighlighting, updateLineNumbers } from './document/ui/editor/sourceEditor.js';

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
import { _showDocAiReplyChoice, generateEmailReply as _runEmailAiReply } from './document/ui/email/aiReply.js';
import { _discardEmail, _markUnreadAndClose, _saveDraft, _scheduleSend, _sendEmail } from './document/ui/email/sendWorkflow.js';
import { _handleAttachUpload, _renderComposeAttachments, _showComposeAttachMenu, _stageForwardedSourceAttachments, _uploadComposeFiles } from './document/ui/email/attachments.js';
import { _sendSignedReply } from './document/ui/email/signedReply.js';
import { _hideEmailFields, _maybeAutoCollapseEmailHeader, _setEmailHeaderCollapsed, _showEmailFields, _syncEmailHeaderSummary, _syncStreamingEmailFields } from './document/ui/email/composerFields.js';
import './document/ui/email/emailDocuments.js';
import { autoTitleFromContent, copyDocument, runDocument, updateLanguage, updateTitle } from './document/ui/documentActions.js';
import './document/ui/header/overflow.js';
import { _handleMarkdownImageUpload, _uploadMarkdownImages } from './document/ui/editor/markdownImages.js';
import { _closeWithoutDeleting, _ensureDocPaneMounted, closeTab, createDocument, deleteActiveDocument, getChatDocumentId, injectFreshDoc, loadDocument, moveActiveDocumentToCurrentChat, moveActiveDocumentToNewChat } from './document/ui/documentFlows.js';
import { _measurePos, _scheduleSelRerender, _selections, clearSelection, syncSelectionOverlay, updateRichSelectionState, updateSelectionState } from './document/ui/selection/selectionHighlights.js';
import { _syncDocumentToolbarSeparators, initMdToolbar } from './document/ui/toolbar/toolbar.js';
import { _closeDocTabMenu, _docTabMenu, _syncDocIndicator, renderTabs, showEmptyState } from './document/ui/tabs/tabBar.js';

import "./document/ui/aiUpdates/documentUpdate.js";
const _handleDocUpdateFromLiveStream = (...args) => globalThis.handleDocUpdate?.(...args);
import { _closeVersionPanel, _historyOwnsView, _versionHistory, toggleVersionHistory } from "./document/ui/history/versionHistory.js";
import { streamDocFinalize } from "./document/ui/aiUpdates/liveStream.js";
const _streamDocOpenFromLiveStream = (...args) => globalThis.streamDocOpen?.(...args);
const _streamDocDeltaFromLiveStream = (...args) => globalThis.streamDocDelta?.(...args);

export {
  _handleDocUpdateFromLiveStream as handleDocUpdate,
  _streamDocOpenFromLiveStream as streamDocOpen,
  _streamDocDeltaFromLiveStream as streamDocDelta,
  streamDocFinalize,
};

import { _activeDocLanguage, _closeDocumentOutline, _documentOutlineEntries, _documentOutlineSupported, _openDocumentOutline, _refreshDocumentOutline, _renderDocumentSaveState, _renderDocumentStats, _scheduleDocumentHistoryControls, _scheduleDocumentStats, _setDocumentHistoryControlState, _syncDocumentHistoryControlsNow, _syncEditorPlaceholder } from "./document/ui/status/documentStatus.js";
import { _ensureDocChipRegistered, _hideLoadingOverlay, _minimizeNotesForDocumentOpen, _showLoadingOverlay, closePanel, newDocument, openPanel, prepareDocumentOpen, swapSide } from './document/ui/panel/panelLifecycle.js';
import { saveCurrentToMap, switchToDoc } from './document/ui/panel/documentPresenter.js';
import { _wireSwipeDismiss, toggleFullscreen } from './document/ui/panel/paneLayout.js';
import { _syncHeaderActions } from './document/ui/header/headerActions.js';
import { activateModuleWiring, _wireDiffViewer, _wireExportDocx, _wireFindReplace, _wirePdfTools, _wirePreviewModes, _wireSuggestions } from './document/ui/moduleWiring.js';
import { installEscapeLayering } from './document/ui/panel/panelLifecycle.js';


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
  globalThis._activeDocLanguage = _activeDocLanguage;
  globalThis._closeVersionPanel = _closeVersionPanel;
  globalThis._setDocumentHistoryControlState = _setDocumentHistoryControlState;
  globalThis._syncEditorPlaceholder = _syncEditorPlaceholder;
  globalThis._wireSwipeDismiss = _wireSwipeDismiss;
  globalThis.toggleFullscreen = toggleFullscreen;
  globalThis.toggleVersionHistory = toggleVersionHistory;

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











activateModuleWiring();

















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









































































































function _maybeOpenDocFromHash() {
    const m = (window.location.hash || '').match(/^#document-(.+)$/);
    if (m) loadDocument(m[1]);
  }

























































installEscapeLayering();



















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
const _ensureDocPanelFacade = (...args) => globalThis.ensureDocPanel?.(...args);
const _loadSessionDocsFacade = (...args) => globalThis.loadSessionDocs?.(...args);
const _getSelectionContextFacade = (...args) => globalThis.getSelectionContext?.(...args);
const _restoreSelectionReferenceFacade = (...args) => globalThis.restoreSelectionReference?.(...args);

export {
  _openEmailDraft as openEmailDraft,
  _replaceEmailReplyBody as replaceEmailReplyBody,
  _ensureEmailDraftEnvelope as ensureEmailDraftEnvelope,
  _getActiveEmailComposerContext as getActiveEmailComposerContext,
  _findEmailDocId as findEmailDocId,
  _focusEmailReplyBody as focusEmailReplyBody,
  _runEmailAiReply as generateEmailReply,
  _ensureDocPanelFacade as ensureDocPanel,
  _loadSessionDocsFacade as loadSessionDocs,
  _getSelectionContextFacade as getSelectionContext,
  _restoreSelectionReferenceFacade as restoreSelectionReference,
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
  generateEmailReply: _runEmailAiReply,
  ensurePaneMounted: _ensureDocPaneMounted,
  loadSessionDocs: _loadSessionDocsFacade,
  ensureDocPanel: _ensureDocPanelFacade,
  saveDocument,
  handleDocUpdate: _handleDocUpdateFromLiveStream,
  handleDocSuggestions,
  streamDocOpen: _streamDocOpenFromLiveStream,
  streamDocDelta: _streamDocDeltaFromLiveStream,
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
  getSelectionContext: _getSelectionContextFacade,
  restoreSelectionReference: _restoreSelectionReferenceFacade,
  clearSelection,
  clearAll,
  openLibrary,
  closeLibrary,
  isLibraryOpen,
};

export { createDocument, injectFreshDoc, loadDocument, getChatDocumentId };
export { closePanel, newDocument, openPanel, prepareDocumentOpen, swapSide };
export { handleDocSuggestions, isPanelOpen, saveDocument };

export default documentModule;

window.documentModule = documentModule;
