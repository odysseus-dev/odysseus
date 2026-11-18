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
  if (existing && (existing._visualSyncBlocked || ['dirty', 'saving', 'error'].includes(existing._saveState))) {
    registerDocument(existing);
    releaseClosingDraft(doc.id);
    return;
  }
  const adopted = Object.assign(existing || {}, {
    id: doc.id,
    title: doc.title || '',
    language: doc.language || '',
    content: doc.current_content ?? doc.content ?? '',
    _persistedContent: doc.current_content ?? doc.content ?? '',
    version: doc.version_count || 1,
    sessionId: sessionId || doc.session_id,
    userSetLanguage: !!doc.language,
    _composeAtts: existing?._composeAtts,
    _skipLocalDraftOnce: !!doc._skipLocalDraftOnce,
    sourceEmailUid: doc.source_email_uid || null,
    sourceEmailFolder: doc.source_email_folder || null,
    sourceEmailAccountId: doc.source_email_account_id || null,
    sourceEmailMessageId: doc.source_email_message_id || null
  });
  recoverMarkdownDraft(adopted);
  registerDocument(adopted);
}

// Tab-scoped recovery follows the existing server-authorized document/session
// identity. Email composition retains its own, separate local draft contract.
const markdownDraftKey = doc => doc?.id && doc?.sessionId
  ? `odysseus.document.markdownDraft.v1:${encodeURIComponent(doc.sessionId)}:${encodeURIComponent(doc.id)}` : '';

export function clearMarkdownDraft(doc) {
  const key = markdownDraftKey(doc);
  if (!key) return;
  try { sessionStorage.removeItem(key); } catch (_) {}
}

export function persistMarkdownDraft(doc) {
  if (doc?.language !== 'markdown') return true;
  const key = markdownDraftKey(doc);
  if (!key) return false;
  try {
    if (!doc._visualSyncBlocked && doc.content === doc._persistedContent) {
      sessionStorage.removeItem(key);
    } else {
      sessionStorage.setItem(key, JSON.stringify({
        id: doc.id, sessionId: doc.sessionId, content: doc.content,
        baseContent: doc._persistedContent ?? doc._visualBaselineMarkdown ?? '',
        visualBase: doc._visualBaselineMarkdown ?? doc.content,
        visualHtml: doc._visualSyncBlocked ? doc._visualDraftHtml : null,
        reasons: doc._visualCompatReasons,
      }));
    }
    doc._markdownRecoveryStored = true;
    return true;
  } catch (_) {
    doc._markdownRecoveryStored = false;
    return false;
  }
}

function recoverMarkdownDraft(doc) {
  if (doc.language !== 'markdown') return;
  const key = markdownDraftKey(doc);
  if (!key) return;
  try {
    const draft = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (!draft || draft.id !== doc.id || draft.sessionId !== doc.sessionId
        || typeof draft.content !== 'string' || typeof draft.baseContent !== 'string') return;
    if (draft.visualHtml == null && draft.content === doc.content) {
      clearMarkdownDraft(doc);
      return;
    }
    const conflict = doc.content !== draft.baseContent && doc.content !== draft.content;
    if (!conflict) doc.content = draft.content;
    doc._saveState = 'dirty';
    doc._editRevision = (doc._editRevision || 0) + 1;
    doc._markdownRecoveryStored = true;
    if (typeof draft.visualHtml === 'string' || conflict) {
      doc._saveState = 'blocked';
      doc._visualSyncBlocked = true;
      doc._visualDraftHtml = typeof draft.visualHtml === 'string' ? draft.visualHtml : null;
      doc._visualBaselineMarkdown = typeof draft.visualBase === 'string' ? draft.visualBase : draft.content;
      doc._visualRecoveryMarkdown = draft.content;
      doc._visualCompatReasons = conflict
        ? ['the server changed while this local draft was pending; copy your draft before discarding it']
        : Array.isArray(draft.reasons) ? draft.reasons.filter(reason => typeof reason === 'string') : ['recovered visual formatting cannot be saved'];
    }
  } catch (_) {
    // Unreadable recovery never replaces a server-authorized document.
  }
}

export function guardMarkdownDraftUnload(event) {
  let hasUnsavedChanges = false;
  for (const doc of [...docs.values(), ..._closingDocs.values()]) {
    if (doc.language !== 'markdown' || (!doc._visualSyncBlocked && !['dirty', 'saving', 'error'].includes(doc._saveState))) continue;
    persistMarkdownDraft(doc);
    // Tab storage survives reload but is removed when the browser tab closes.
    // beforeunload cannot distinguish those operations, so retain the warning
    // until the current content and formatting have actually been saved.
    if (doc._visualSyncBlocked || doc.content !== doc._persistedContent) hasUnsavedChanges = true;
  }
  if (hasUnsavedChanges) {
    event.preventDefault();
    event.returnValue = true;
  }
}
