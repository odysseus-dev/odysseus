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
import { _showDocAiReplyChoice } from './document/ui/email/aiReply.js';
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



let _disposePdfTools = null;



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
      globalThis._disposeLineNumberResizeObserver?.(pane);
      globalThis._disposeSelectionResizeObserver?.();
      globalThis._disposeLineNumberResizeObserver?.(pane);
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
        globalThis._scheduleSourceEditorInput?.(ta, autoTitleFromContent);
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









function _maybeOpenDocFromHash() {
    const m = (window.location.hash || '').match(/^#document-(.+)$/);
    if (m) loadDocument(m[1]);
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
const _generateEmailReply = (...args) => globalThis.generateEmailReply?.(...args);
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
  _generateEmailReply as generateEmailReply,
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
  generateEmailReply: _generateEmailReply,
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
export { handleDocSuggestions, isPanelOpen, saveDocument };

export default documentModule;

window.documentModule = documentModule;
