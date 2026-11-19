// frontend/src/features/documents/application/documentCommands.js
/**
 * View command port for document operations.
 * Decouples application flows from concrete UI and panel lifecycles.
 */

let _impl = null;

const REQUIRED_COMMANDS = [
  'openPanel',
  'closePanel',
  'switchToDoc',
  'saveCurrentToMap',
  'renderTabs',
  '_syncDocIndicator',
  '_syncHeaderActions',
  'showEmptyState',
  '_showLoadingOverlay',
  '_hideLoadingOverlay',
  '_minimizeNotesForDocumentOpen',
  '_ensureDocChipRegistered'
];

function _getImpl() {
  if (!_impl) {
    throw new Error('Document commands not bound');
  }
  return _impl;
}

export function bindDocumentCommands(commands, { idempotent = false } = {}) {
  if (_impl !== null) {
    if (idempotent && _impl === commands) {
      return;
    }
    throw new Error('Document commands already bound');
  }
  if (!commands || typeof commands !== 'object') {
    throw new Error('Missing document commands object');
  }
  for (const name of REQUIRED_COMMANDS) {
    if (typeof commands[name] !== 'function') {
      throw new Error(`Missing document command implementation: ${name}`);
    }
  }
  _impl = commands;
}

export function _resetDocumentCommandsForTesting() {
  _impl = null;
}

// Fixed 12-method view command port stubs (const arrow functions)
export const openPanel = (...args) => _getImpl().openPanel(...args);
export const closePanel = (...args) => _getImpl().closePanel(...args);
export const switchToDoc = (...args) => _getImpl().switchToDoc(...args);
export const saveCurrentToMap = (...args) => _getImpl().saveCurrentToMap(...args);
export const renderTabs = (...args) => _getImpl().renderTabs(...args);
export const _syncDocIndicator = (...args) => _getImpl()._syncDocIndicator(...args);
export const _syncHeaderActions = (...args) => _getImpl()._syncHeaderActions(...args);
export const showEmptyState = (...args) => _getImpl().showEmptyState(...args);
export const _showLoadingOverlay = (...args) => _getImpl()._showLoadingOverlay(...args);
export const _hideLoadingOverlay = (...args) => _getImpl()._hideLoadingOverlay(...args);
export const _minimizeNotesForDocumentOpen = (...args) => _getImpl()._minimizeNotesForDocumentOpen(...args);
export const _ensureDocChipRegistered = (...args) => _getImpl()._ensureDocChipRegistered(...args);
