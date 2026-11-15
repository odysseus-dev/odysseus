// ui/moduleWiring.js

import { _docActiveKey, _docMinimizedKey, _docOpenKey, _forgetActiveDoc, _markDocVisibleState, addDocToTabs } from '../application/documentSession.js';

import { API_BASE, _closingDocs, _deletedDocIds, _documentNavigationGeneration, _inFlightDeletes, _lastSessionId, _minimizedDocId, activeDocId, beginDelete, bumpNavigationGeneration, clearDocuments, docs, endDelete, isOpen, isPanelOpen, markDeleted, registerDocument, releaseClosingDraft, removeDocument, retainClosingDraft, setActiveDocId, setApiBase, setLastSessionId, setMinimizedDocId, setPanelOpen } from '../application/documentState.js';

import { _autoSaveDebounce, _cancelAutoSave, _emailRichbodySaveDebounce, _flushAutoSave, _markDocumentDirty, _pendingAutoSaves, _resetPersistenceHooksForTesting, _saveClosingDraft, _scheduleAutoSave, _setDocumentSaveState, awaitWrites, bindPersistenceHooks, runSerializedWrite, saveDocument } from '../application/persistence.js';

import { AUTO_DETECT_DELAY, AUTO_DETECT_MIN_CHARS, AUTO_DETECT_MIN_RELEVANCE, AUTO_DETECT_SAMPLE_SIZE, HLJS_TO_DROPDOWN, _DOC_SAVE_STATE, _buildDiffSummary, _esc, _escHtml, _hasViewToggle, _isDocxLang, _isMarkdownImageFile, _isRenderLang, _isRichTextLang, _looksLikeMarkdown, _markdownImageAlt } from '../domain/documentKinds.js';

import { _autoCreateFromInput, _autoCreateSession, _closeWithoutDeleting, _creatingDoc, _detachDocFromSession, _ensureDocPaneMounted, _rememberActiveDoc, _visibleDocIdsForCurrentSession, closeTab, createDocument, deleteActiveDocument, getChatDocumentId, injectFreshDoc, loadDocument, moveActiveDocumentToCurrentChat, moveActiveDocumentToNewChat } from './documentFlows.js';

import { _autoDetectDebounce, _autoTitleDebounce, _handleMarkdownPreviewClickHint, _hlDebounce, _lineNumberContentEl, _lineNumberObservedTextarea, _lineNumberResizeObserver, _lineNumberResizeRaf, _refreshMarkdownPreviewIfVisible, _replaceRange, attemptAutoDetect, syncGutterScroll, syncHighlighting, updateLineNumbers } from './editor/sourceEditor.js';

import { _hideEmailFields, _maybeAutoCollapseEmailHeader, _setEmailHeaderCollapsed, _showEmailFields, _syncEmailHeaderSummary, _syncStreamingEmailFields } from './email/composerFields.js';

import { _dismissDocKb, _wireSwipeDismiss, initDividerDrag, toggleFullscreen } from './panel/paneLayout.js';

import { _emailHtmlToPlainText, _emailPlainTextToHtml, _emailRichbodyActive, _richTextContentToHtml, _sanitizedRichTextHtml, _scheduleEmailRichbodySave, _syncEmailRichbody, _syncRichEmptyImport } from './richEditor/richBodySync.js';

import { _showRichTextEditor, _wireEmailRichbody } from './richEditor/richBodyWiring.js';

import { _measurePos, _scheduleSelRerender, _selResizeObserver, _selections, clearSelection, syncSelectionOverlay, updateRichSelectionState, updateSelectionState } from './selection/selectionHighlights.js';

import { Modals, attachColorPicker, bindMenuDismiss, closeLibrary, codeRunnerModule, dismissOrRemove, dismissTopMenu, documentLibrary, emojiPicker, getDocumentStats, initLibrary, isLibraryOpen, langIcon, markdownModule, openLibrary, parseMarkdownOutline, readEmailReplyResponse, sessionModule, signatureModule, spinnerModule, topPortalZ, uiModule } from './host.js';

import { closePanel, openPanel, renderTabs, saveCurrentToMap, switchToDoc, _syncHeaderActions } from '../application/documentCommands.js';

import { initExportDocx } from './exportDocx.js';
import { _downloadFilledPdf, _saveActiveDocBeforeExport, initPdfTools } from './pdfTools.js';
import { initPreviewModes, _convertDocxToRichText, _setMarkdownPreviewActive, _themedRenderSrcdoc, isHtmlPreviewActive } from './previewModes.js';
import { _diffModeActive, exitDiffMode, initDiffViewer } from './diffViewer.js';
import { initSuggestions } from './suggestions.js';
import { initFindReplace } from './findReplace.js';



export let _disposePdfTools = null;

export function _wireExportDocx() {
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

export function _wirePreviewModes() {
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

export function _wireDiffViewer() {
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

export function _wireSuggestions() {
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

export function _wirePdfTools() {
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

export function _wireFindReplace() {
    initFindReplace({
      isRichActive: _emailRichbodyActive,
      syncEmailRichbody: _syncEmailRichbody,
      scheduleEmailRichbodySave: _scheduleEmailRichbodySave,
      replaceRange: _replaceRange,
    });
  }

export function activateModuleWiring() {
_wireExportDocx();
_wirePreviewModes();
}

export function disposePdfTools() {
  _disposePdfTools?.();
  _disposePdfTools = null;
}
