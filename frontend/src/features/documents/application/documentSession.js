// frontend/src/features/documents/application/documentSession.js
/**
 * Document session persistence and tab adoption.
 * Handles per-session localStorage keys, visibility state, and tab registration.
 */

import { docs, _closingDocs, registerDocument, releaseClosingDraft } from './documentState.js';

export const _docOpenKey = (sessionId) => 'odysseus-doc-open-' + sessionId;
export const _docMinimizedKey = (sessionId) => 'odysseus-doc-minimized-' + sessionId;
export const _docActiveKey = (sessionId) => 'odysseus-doc-active-' + sessionId;

export function _forgetActiveDoc(sessionId, docId) {
  if (!sessionId || localStorage.getItem(_docActiveKey(sessionId)) !== docId) return;
  localStorage.removeItem(_docActiveKey(sessionId));
}

export function _markDocVisibleState(sessionId, state) {
  if (!sessionId) return;
  if (state === 'open') {
    localStorage.setItem(_docOpenKey(sessionId), '1');
    localStorage.removeItem(_docMinimizedKey(sessionId));
  } else if (state === 'minimized') {
    localStorage.removeItem(_docOpenKey(sessionId));
    localStorage.setItem(_docMinimizedKey(sessionId), '1');
  } else {
    localStorage.removeItem(_docOpenKey(sessionId));
    localStorage.removeItem(_docMinimizedKey(sessionId));
  }
}

export function addDocToTabs(doc, sessionId) {
  const existing = docs.get(doc.id) || _closingDocs.get(doc.id);
  if (existing && ['dirty', 'saving', 'error'].includes(existing._saveState)) {
    registerDocument(existing);
    releaseClosingDraft(doc.id);
    return;
  }
  registerDocument(Object.assign(existing || {}, {
    id: doc.id,
    title: doc.title || '',
    language: doc.language || '',
    content: doc.current_content || doc.content || '',
    version: doc.version_count || 1,
    sessionId: sessionId || doc.session_id,
    userSetLanguage: !!doc.language,
    _composeAtts: existing?._composeAtts,
    _skipLocalDraftOnce: !!doc._skipLocalDraftOnce,
    sourceEmailUid: doc.source_email_uid || null,
    sourceEmailFolder: doc.source_email_folder || null,
    sourceEmailAccountId: doc.source_email_account_id || null,
    sourceEmailMessageId: doc.source_email_message_id || null
  }));
}
