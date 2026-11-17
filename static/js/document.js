import { openEmailDraft as _openEmailDraft, replaceEmailReplyBody as _replaceEmailReplyBody, ensureEmailDraftEnvelope as _ensureEmailDraftEnvelope, getActiveEmailComposerContext as _getActiveEmailComposerContext, findEmailDocId as _findEmailDocId } from './document/ui/email/emailDocuments.js';
import { focusEmailReplyBody as _focusEmailReplyBody } from './document/ui/email/emailBody.js';
import { ensureDocPanel as _ensureDocPanelFacade, loadSessionDocs as _loadSessionDocsFacade } from './document/ui/documentFlows.js';
import { getSelectionContext as _getSelectionContextFacade, restoreSelectionReference as _restoreSelectionReferenceFacade } from './document/ui/selection/selectionHighlights.js';
import { handleDocUpdate as _handleDocUpdateFromLiveStream } from './document/ui/aiUpdates/documentUpdate.js';
import { streamDocOpen as _streamDocOpenFromLiveStream, streamDocDelta as _streamDocDeltaFromLiveStream } from './document/ui/aiUpdates/liveStream.js';
// static/js/document.js
/**
 * Document editor module: multi-document tabbed panel alongside chat.
 * Supports multiple open documents with tab switching, per-doc state,
 * and theme-aware styling.
 */

import uiModule from './ui.js?v=20260916largetoolscroll1';

import './sessions.js';

import markdownModule from './markdown.js';

import { openLibrary, closeLibrary, isLibraryOpen, initLibrary } from './documentLibrary.js?v=20260911librarybulkdelete1';

import './modalManager.js';

import { enterDiffMode, exitDiffMode, isDiffModeActive } from './document/ui/diffViewer.js';

import { _cancelPdfPaneSave, _hasPendingPdfPaneSave, _syncPdfPaneToDoc } from './document/ui/pdfTools.js';

import { handleDocSuggestions, suspendSuggestions } from './document/ui/suggestions.js';

import { _esc } from './document/domain/documentKinds.js';

import { isOpen, activeDocId, docs, isPanelOpen, setApiBase, setActiveDocId, setLastSessionId, bumpNavigationGeneration, clearDocuments } from './document/application/documentState.js';

import { bindDocumentCommands } from './document/application/documentCommands.js';

import { saveDocument, bindPersistenceHooks } from './document/application/persistence.js';

import { addDocToTabs } from './document/application/documentSession.js';

import './document/ui/editor/sourceEditor.js';

import './document/ui/richEditor/imageMenus.js';

import { generateEmailReply as _runEmailAiReply } from './document/ui/email/aiReply.js';

import './document/ui/header/overflow.js';

import { _ensureDocPaneMounted, createDocument, getChatDocumentId, injectFreshDoc, loadDocument, moveActiveDocumentToCurrentChat, moveActiveDocumentToNewChat } from './document/ui/documentFlows.js';
import { replaceSelections, _selections, clearSelection } from './document/ui/selection/selectionHighlights.js';

import { _syncDocIndicator, renderTabs, showEmptyState } from './document/ui/tabs/tabBar.js';

import { _closeVersionPanel } from './document/ui/history/versionHistory.js';
import { streamDocFinalize } from "./document/ui/aiUpdates/liveStream.js";

export {
  _handleDocUpdateFromLiveStream as handleDocUpdate,
  _streamDocOpenFromLiveStream as streamDocOpen,
  _streamDocDeltaFromLiveStream as streamDocDelta,
  streamDocFinalize,
};

import { _renderDocumentSaveState } from './document/ui/status/documentStatus.js';
import { _ensureDocChipRegistered, _hideLoadingOverlay, _minimizeNotesForDocumentOpen, _showLoadingOverlay, closePanel, newDocument, openPanel, prepareDocumentOpen, swapSide } from './document/ui/panel/panelLifecycle.js';
import { saveCurrentToMap, switchToDoc } from './document/ui/panel/documentPresenter.js';

import { _syncHeaderActions } from './document/ui/header/headerActions.js';
import { activateModuleWiring, _wireDiffViewer, _wireExportDocx, _wirePreviewModes, _wireSuggestions } from './document/ui/moduleWiring.js';
import { installEscapeLayering } from './document/ui/panel/panelLifecycle.js';

// Existing external integrations read these legacy singletons.
globalThis.uiModule = uiModule;
globalThis.markdownModule = markdownModule;
Object.defineProperty(globalThis, '_selections', {
  get: () => _selections,
  set: replaceSelections,
  configurable: true,
});

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
