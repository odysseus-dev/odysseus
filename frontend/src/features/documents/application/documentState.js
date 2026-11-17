// frontend/src/features/documents/application/documentState.js
/**
 * Authoritative document state store and transitions.
 * Baseline state declarations extracted from static/js/document.js.
 */

// Baseline state declarations extracted verbatim from static/js/document.js
export let API_BASE = '';
export let isOpen = false;
export const _deletedDocIds = new Set();
export const _inFlightDeletes = new Map();
export const _closingDocs = new Map();
export let _documentNavigationGeneration = 0;
export let activeDocId = null;
export let _lastSessionId = '';
export const docs = new Map();
export let _minimizedDocId = null;

export function isPanelOpen() {
  return isOpen;
}

// State transitions (the only permitted mutators of the state bindings)

export function setApiBase(url) {
  API_BASE = url;
}

export function setActiveDocId(id) {
  activeDocId = id;
}

export function setLastSessionId(id) {
  _lastSessionId = id;
}

export function bumpNavigationGeneration() {
  return ++_documentNavigationGeneration;
}

export function setPanelOpen(open) {
  isOpen = Boolean(open);
}

export function setMinimizedDocId(id) {
  _minimizedDocId = id;
}

export function registerDocument(doc) {
  if (!doc || !doc.id) return;
  docs.set(doc.id, doc);
}

export function removeDocument(id) {
  docs.delete(id);
}

export function clearDocuments() {
  docs.clear();
}

export function markDeleted(id) {
  if (id) {
    _deletedDocIds.add(id);
  }
}

export function beginDelete(id, task) {
  _inFlightDeletes.set(id, task);
}

export function endDelete(id) {
  _inFlightDeletes.delete(id);
}

export function retainClosingDraft(id, doc) {
  _closingDocs.set(id, doc);
}

export function releaseClosingDraft(id, doc) {
  if (doc && _closingDocs.get(id) !== doc) {
    return false;
  }
  return _closingDocs.delete(id);
}
