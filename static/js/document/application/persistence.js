// frontend/src/features/documents/application/persistence.js
/**
 * Document Editor Persistence Engine.
 * Handles autosave scheduling, debounce timers, serialized writes,
 * detached draft retention, and save state synchronization.
 */

import {
  API_BASE,
  activeDocId,
  isOpen,
  docs,
  _closingDocs,
  _deletedDocIds,
  _inFlightDeletes,
  retainClosingDraft,
  releaseClosingDraft
} from './documentState.js';

import {
  saveCurrentToMap,
  renderTabs,
  _syncDocIndicator
} from './documentCommands.js';

import { persistMarkdownDraft } from './documentSession.js';

import { _DOC_SAVE_STATE } from '../domain/documentKinds.js';

export let _autoSaveDebounce = null;
let _lastAutoSaveErrorAt = 0;
export let _emailRichbodySaveDebounce = null;
export const _pendingAutoSaves = new Map();

let _boundHooks = null;

const _ports = {
  _hasPendingPdfPaneSave(docId) {
    if (_boundHooks && typeof _boundHooks._hasPendingPdfPaneSave === 'function') {
      return _boundHooks._hasPendingPdfPaneSave(docId);
    }
    if (typeof window !== 'undefined' && typeof window._hasPendingPdfPaneSave === 'function') {
      return window._hasPendingPdfPaneSave(docId);
    }
    return false;
  },
  _renderDocumentSaveState() {
    if (_boundHooks && typeof _boundHooks._renderDocumentSaveState === 'function') {
      return _boundHooks._renderDocumentSaveState();
    }
    if (typeof window !== 'undefined' && typeof window._renderDocumentSaveState === 'function') {
      return window._renderDocumentSaveState();
    }
    const status = typeof document !== 'undefined' ? document.getElementById('doc-footer-copy-btn') : null;
    if (!status) return;
    const doc = activeDocId && (docs.get(activeDocId) || _closingDocs.get(activeDocId));
    const state = doc?._saveState || 'saved';
    const meta = _DOC_SAVE_STATE[state] || _DOC_SAVE_STATE.saved;
    status.dataset.saveState = state;
    status.title = `${meta.title} (Ctrl+S)`;
    status.setAttribute('aria-label', meta.label);
    const label = status.querySelector('.doc-save-button-label');
    if (label) label.textContent = meta.label;
  },
  getChatDocumentId() {
    if (_boundHooks && typeof _boundHooks.getChatDocumentId === 'function') {
      return _boundHooks.getChatDocumentId();
    }
    if (typeof window !== 'undefined' && typeof window.getChatDocumentId === 'function') {
      return window.getChatDocumentId();
    }
    return null;
  },
  _cancelPdfPaneSave(docId) {
    if (_boundHooks && typeof _boundHooks._cancelPdfPaneSave === 'function') {
      return _boundHooks._cancelPdfPaneSave(docId);
    }
    if (typeof window !== 'undefined' && typeof window._cancelPdfPaneSave === 'function') {
      return window._cancelPdfPaneSave(docId);
    }
  },
  _syncPdfPaneToDoc(docId) {
    if (_boundHooks && typeof _boundHooks._syncPdfPaneToDoc === 'function') {
      return _boundHooks._syncPdfPaneToDoc(docId);
    }
    if (typeof window !== 'undefined' && typeof window._syncPdfPaneToDoc === 'function') {
      return window._syncPdfPaneToDoc(docId);
    }
  },
  _renderVersionBadge(docId, version) {
    if (_boundHooks && typeof _boundHooks._renderVersionBadge === 'function') {
      return _boundHooks._renderVersionBadge(docId, version);
    }
    if (typeof window !== 'undefined' && typeof window._renderVersionBadge === 'function') {
      return window._renderVersionBadge(docId, version);
    }
    const badge = typeof document !== 'undefined' ? document.getElementById('doc-version-badge') : null;
    if (badge && activeDocId === docId) {
      const _v = version || 1;
      badge.textContent = `v${_v}`;
      badge.style.display = _v > 1 ? '' : 'none';
    }
  }
};

export function bindPersistenceHooks(suppliedHooks, { idempotent = false } = {}) {
  if (_boundHooks !== null) {
    if (idempotent) {
      return;
    }
    throw new Error('Persistence hooks already bound');
  }
  if (!suppliedHooks || typeof suppliedHooks !== 'object') {
    throw new Error('Missing persistence hooks object');
  }
  _boundHooks = Object.assign({}, suppliedHooks);
}

export function _resetPersistenceHooksForTesting() {
  _boundHooks = null;
}

const uiModule = {
  showToast(...args) {
    const m = (typeof window !== 'undefined' ? window.uiModule : null) || (typeof globalThis !== 'undefined' ? globalThis.uiModule : null);
    if (m && typeof m.showToast === 'function') {
      return m.showToast(...args);
    }
  },
  showError(...args) {
    const m = (typeof window !== 'undefined' ? window.uiModule : null) || (typeof globalThis !== 'undefined' ? globalThis.uiModule : null);
    if (m && typeof m.showError === 'function') {
      return m.showError(...args);
    }
  }
};

// Detached drafts retain the same object until persistence succeeds. Reopening
// adopts that object rather than replacing a failed draft with a server row.
export function _saveClosingDraft(docId, doc) {
  retainClosingDraft(docId, doc);
  return saveDocument({ silent: true, docId }).then(ok => {
    if (ok && doc._saveState === 'saved' && _closingDocs.get(docId) === doc) {
      releaseClosingDraft(docId, doc);
    }
    return ok;
  });
}

export function _scheduleAutoSave(docId = activeDocId, delay = 800, options = {}) {
  if (!docId || _deletedDocIds.has(docId)) return;
  if (_pendingAutoSaves.has(docId)) {
    clearTimeout(_pendingAutoSaves.get(docId).timer);
    _pendingAutoSaves.delete(docId);
  }
  if (docs.get(docId)?._visualSyncBlocked) return;
  const timer = setTimeout(async () => {
    _pendingAutoSaves.delete(docId);
    if (_deletedDocIds.has(docId)) return;
    await saveDocument({ silent: true, docId, ...options });
  }, delay);
  _pendingAutoSaves.set(docId, { timer, options });
  _autoSaveDebounce = timer;
  if (options?.isEmailRichbody) _emailRichbodySaveDebounce = timer;
}

export function _cancelAutoSave(docId = activeDocId) {
  if (!docId) return;
  if (_pendingAutoSaves.has(docId)) {
    const entry = _pendingAutoSaves.get(docId);
    clearTimeout(entry.timer);
    if (_autoSaveDebounce === entry.timer) _autoSaveDebounce = null;
    if (_emailRichbodySaveDebounce === entry.timer) _emailRichbodySaveDebounce = null;
    _pendingAutoSaves.delete(docId);
  }
}

export function _flushAutoSave(docId = activeDocId) {
  if (!docId) return Promise.resolve(false);
  const targetDoc = docs.get(docId) || _closingDocs.get(docId);
  if (_pendingAutoSaves.has(docId)) {
    const entry = _pendingAutoSaves.get(docId);
    clearTimeout(entry.timer);
    if (_autoSaveDebounce === entry.timer) _autoSaveDebounce = null;
    if (_emailRichbodySaveDebounce === entry.timer) _emailRichbodySaveDebounce = null;
    _pendingAutoSaves.delete(docId);
    if (!_deletedDocIds.has(docId)) {
      return saveDocument({ silent: true, docId, ...entry.options });
    }
    return Promise.resolve(false);
  }
  if ((targetDoc && targetDoc._saveState === 'dirty' || _ports._hasPendingPdfPaneSave(docId)) && !_deletedDocIds.has(docId)) {
    return saveDocument({ silent: true, docId });
  }
  return Promise.resolve(false);
}

export function _setDocumentSaveState(state, docId = activeDocId) {
  const doc = docId && (docs.get(docId) || _closingDocs.get(docId));
  if (!doc || !_DOC_SAVE_STATE[state]) return;
  if (doc._saveState === state) return;
  doc._saveState = state;
  persistMarkdownDraft(doc);
  if (docId === activeDocId) _ports._renderDocumentSaveState();
}

export function _markDocumentDirty(docId = activeDocId) {
  const doc = docId && (docs.get(docId) || _closingDocs.get(docId));
  if (!doc) return;
  doc._editRevision = (doc._editRevision || 0) + 1;
  _setDocumentSaveState(doc._visualSyncBlocked ? 'blocked' : 'dirty', docId);
  persistMarkdownDraft(doc);
}

/** Save manual edits */
export async function saveDocument({ silent = false, forceVersion = false, docId = null, keepalive = false, skipPdfSync = false } = {}) {
  const savingDocId = docId || _ports.getChatDocumentId();
  if (!savingDocId) return false;
  if (_deletedDocIds.has(savingDocId)) return false;
  const inFlightDelete = _inFlightDeletes.get(savingDocId);
  if (inFlightDelete) {
    await inFlightDelete.catch(() => {});
  }
  if (_deletedDocIds.has(savingDocId)) return false;
  _cancelAutoSave(savingDocId);
  _ports._cancelPdfPaneSave(savingDocId);
  if (!skipPdfSync) _ports._syncPdfPaneToDoc(savingDocId);
  // Minimizing removes the editor DOM, but closePanel already captured its
  // content in docs. Persist that cache without reading an absent/new pane.
  if (isOpen && activeDocId === savingDocId && !skipPdfSync) saveCurrentToMap();
  const localDoc = docs.get(savingDocId) || _closingDocs.get(savingDocId);
  if (!localDoc || _deletedDocIds.has(savingDocId)) return false;
  const saveRequest = (localDoc?._saveRequest || 0) + 1;
  if (localDoc) localDoc._saveRequest = saveRequest;
  if (!localDoc._visualSyncBlocked) _setDocumentSaveState('saving', savingDocId);
  const previousSave = localDoc?._saveQueueTail || Promise.resolve();
  let releaseSaveTurn = () => {};
  const saveTurn = new Promise(resolve => { releaseSaveTurn = resolve; });
  if (localDoc) {
    localDoc._saveQueueTail = previousSave.catch(() => {}).then(() => saveTurn);
  }
  await previousSave.catch(() => {});

  if (_deletedDocIds.has(savingDocId)) {
    releaseSaveTurn();
    return false;
  }

  // Choose the authoritative draft when this write gets its turn. A save
  // queued behind a restore must not replay the pre-restore document.
  if (isOpen && activeDocId === savingDocId && !skipPdfSync) saveCurrentToMap();
  if (localDoc._visualSyncBlocked) {
    _setDocumentSaveState('blocked', savingDocId);
    releaseSaveTurn();
    return false;
  }
  const contentToSave = localDoc.content ?? '';
  const saveRevision = localDoc._editRevision || 0;
  if (localDoc._saveRequest === saveRequest) _setDocumentSaveState('saving', savingDocId);

  try {
    const res = await fetch(`${API_BASE}/api/document/${savingDocId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      keepalive: !!keepalive,
      body: JSON.stringify({
        content: contentToSave,
        force_version: !!forceVersion,
        summary: forceVersion ? 'Saved version' : undefined,
      }),
    });
    if (_deletedDocIds.has(savingDocId)) {
      return false;
    }
    if (res.status === 404 && silent && localDoc.language === 'email') {
      _setDocumentSaveState('error', savingDocId);
      return false;
    }
    // A failed request, including a missing server row, never discards the
    // local draft. Only a confirmed explicit deletion owns that transition.
    if (!res.ok) throw new Error(`Document save failed: HTTP ${res.status}`);
    const doc = await res.json();
    if (_deletedDocIds.has(savingDocId)) {
      return false;
    }
    _ports._renderVersionBadge(savingDocId, doc.version_count || 1);
    // Update map
    let versionChanged = false;
    const targetDoc = docs.get(savingDocId) || _closingDocs.get(savingDocId);
    if (targetDoc) {
      const previousVersion = targetDoc.version || 1;
      targetDoc.version = Math.max(previousVersion, doc.version_count || 1);
      versionChanged = targetDoc.version !== previousVersion;
      targetDoc._persistedContent = contentToSave;
      persistMarkdownDraft(targetDoc);
      if (targetDoc._saveRequest === saveRequest) {
        // A response acknowledges a snapshot; it never owns the current draft.
        // In particular, the editor DOM may already have been torn down.
        _setDocumentSaveState(
          targetDoc._visualSyncBlocked ? 'blocked' : (targetDoc._editRevision || 0) === saveRevision && targetDoc.content === contentToSave ? 'saved' : 'dirty',
          savingDocId,
        );
      }
    }
    if (versionChanged) renderTabs();
    _syncDocIndicator();
    if (!silent && uiModule && targetDoc?._saveState === 'saved') uiModule.showToast(forceVersion ? 'New version saved' : 'Document saved');
    return true;
  } catch (e) {
    if (_deletedDocIds.has(savingDocId)) {
      return false;
    }
    console.error('Failed to save document:', e);
    const failedDoc = docs.get(savingDocId) || _closingDocs.get(savingDocId);
    if (failedDoc?._saveRequest === saveRequest) {
      _setDocumentSaveState(failedDoc._visualSyncBlocked ? 'blocked' : 'error', savingDocId);
    }
    const now = Date.now();
    if (uiModule && (!silent || now - _lastAutoSaveErrorAt > 10000)) {
      uiModule.showError(silent ? 'Autosave failed' : 'Failed to save document');
      _lastAutoSaveErrorAt = now;
    }
    return false;
  } finally {
    releaseSaveTurn();
    if (!docs.has(savingDocId) && localDoc._saveState === 'saved'
        && _closingDocs.get(savingDocId) === localDoc) releaseClosingDraft(savingDocId, localDoc);
  }
}

export async function awaitWrites(docId) {
  const doc = docId && (docs.get(docId) || _closingDocs.get(docId));
  if (doc?._saveQueueTail) {
    await doc._saveQueueTail.catch(() => {});
  }
}

export async function runSerializedWrite(docId, task) {
  const doc = docId && (docs.get(docId) || _closingDocs.get(docId));
  if (!doc) {
    return task ? task() : Promise.resolve();
  }
  const previousSave = doc._saveQueueTail || Promise.resolve();
  let release;
  const turn = new Promise(resolve => { release = resolve; });
  doc._saveQueueTail = previousSave.catch(() => {}).then(() => turn);
  await previousSave.catch(() => {});
  try {
    return await (task ? task() : Promise.resolve());
  } finally {
    release();
  }
}
