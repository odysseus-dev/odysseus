// ui/moduleWiring.js

import { addDocToTabs } from '../application/documentSession.js';

import { API_BASE, _closingDocs, _lastSessionId, activeDocId, docs, isOpen } from '../application/documentState.js';

import { _markDocumentDirty, saveDocument } from '../application/persistence.js';

import { _escHtml, _isDocxLang, _isRenderLang, _isRichTextLang } from '../domain/documentKinds.js';

import { loadDocument } from './documentFlows.js';

import { _lineNumberContentEl, _refreshMarkdownPreviewIfVisible, _replaceRange, syncHighlighting, updateLineNumbers } from './editor/sourceEditor.js';

import { _showEmailFields } from './email/composerFields.js';

import { _dismissDocKb } from './panel/paneLayout.js';

import { _emailRichbodyActive, _richTextContentToHtml, _scheduleEmailRichbodySave, _syncEmailRichbody, _syncRichEmptyImport } from './richEditor/richBodySync.js';

import { _showRichTextEditor, _showMarkdownVisualEditor, _hideMarkdownVisualEditor } from './richEditor/richBodyWiring.js';
import { hideMarkdownCompatBanner, isMarkdownVisualModeActive, isVisualMarkdownDoc, renderMarkdownCompatBanner, resolveMarkdownEditorMode } from './richEditor/markdownVisualSync.js';

import { _measurePos } from './selection/selectionHighlights.js';

import { bindMenuDismiss, dismissOrRemove, markdownModule, openLibrary, sessionModule, signatureModule, spinnerModule, topPortalZ, uiModule } from './host.js';

import { openPanel, renderTabs, saveCurrentToMap, switchToDoc, _syncHeaderActions } from '../application/documentCommands.js';

import { initExportDocx } from './exportDocx.js';
import { initImportFromDevice } from './importFromDevice.js';
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
      isVisualMarkdownDoc,
      resolveMarkdownEditorMode,
      showMarkdownVisualEditor: _showMarkdownVisualEditor,
      hideMarkdownVisualEditor: _hideMarkdownVisualEditor,
      syncEmailRichbody: _syncEmailRichbody,
      renderMarkdownCompatBanner,
      hideMarkdownCompatBanner,
    });
  }

export function _wireDiffViewer() {
    initDiffViewer({
      saveDocument,
      syncHighlighting,
      updateLineNumbers,
      lineNumberContentEl: _lineNumberContentEl,
      showToast: (msg) => uiModule?.showToast?.(msg),
      getActiveDoc: () => (activeDocId ? docs.get(activeDocId) : null),
      captureSourceToDoc: () => saveCurrentToMap(),
      isVisualMarkdownDoc,
      resolveMarkdownEditorMode,
      showMarkdownVisualEditor: _showMarkdownVisualEditor,
      hideMarkdownVisualEditor: _hideMarkdownVisualEditor,
      syncHeaderActions: _syncHeaderActions,
      onAcceptChanges: () => {
        const lang = ((docs.get(activeDocId)?.language) || document.getElementById('doc-language-select')?.value || '').toLowerCase();
        if (lang === 'markdown') {
          const docId = activeDocId;
          const pane = document.getElementById('doc-editor-pane');
          requestAnimationFrame(() => {
            if (activeDocId !== docId || !pane?.isConnected || pane.dataset.activeDocumentId !== docId) return;
            _setMarkdownPreviewActive(true, { remember: true });
          });
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
      refreshVisualMarkdownIfActive: (doc) => {
        // Only an active visual view must follow a source-level change. The
        // textarea is captured through saveCurrentToMap (with the visual view
        // detached) and the view re-renders from it; source and Preview modes
        // already read the textarea.
        if (!doc || !isVisualMarkdownDoc(doc) || !isMarkdownVisualModeActive()) return;
        _hideMarkdownVisualEditor();
        saveCurrentToMap();
        if (resolveMarkdownEditorMode(doc) !== 'visual' || !_showMarkdownVisualEditor(doc)) {
          renderMarkdownCompatBanner(doc._visualCompatReasons);
        }
        _syncHeaderActions();
      },
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
initImportFromDevice({
  getApiBase: () => API_BASE,
  getUiModule: () => uiModule,
  getSessionModule: () => sessionModule,
  getLastSessionId: () => _lastSessionId,
  openLibrary,
  addDocToTabs,
  switchToDoc,
});
_wirePreviewModes();
}

export function disposePdfTools() {
  _disposePdfTools?.();
  _disposePdfTools = null;
}
