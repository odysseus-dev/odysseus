// frontend/src/features/documents/ui/documentFlows.js
/**
 * Document creation, loading, session moves, and tab lifecycle.
 */

import { _ensureDocChipRegistered, _hideLoadingOverlay, _minimizeNotesForDocumentOpen, _showLoadingOverlay, _syncDocIndicator, closePanel, openPanel, renderTabs, saveCurrentToMap, showEmptyState, switchToDoc } from '../application/documentCommands.js';
import { _docActiveKey, _docMinimizedKey, _docOpenKey, _forgetActiveDoc, _markDocVisibleState, addDocToTabs, clearMarkdownDraft } from '../application/documentSession.js';
import { API_BASE, _closingDocs, _deletedDocIds, _documentNavigationGeneration, _inFlightDeletes, _lastSessionId, _minimizedDocId, activeDocId, beginDelete, bumpNavigationGeneration, docs, endDelete, isOpen, markDeleted, registerDocument, releaseClosingDraft, removeDocument, retainClosingDraft, setActiveDocId, setLastSessionId, setMinimizedDocId, setPanelOpen } from '../application/documentState.js';
import { _cancelAutoSave, _pendingAutoSaves, _saveClosingDraft, _scheduleAutoSave, saveDocument } from '../application/persistence.js';
import { _isRichTextLang } from '../domain/documentKinds.js';
import { _parseEmailHeader } from '../domain/emailMessage.js';
import { autoTitleFromContent } from './documentActions.js';
import { attemptAutoDetect, syncHighlighting } from './editor/sourceEditor.js';
import { _clearEmailLocalDraft } from './email/localDraft.js';
import { Modals, sessionModule, uiModule } from './host.js';
import { _closeVersionPanel } from './history/versionHistory.js';
import { _syncEditorPlaceholder } from './status/documentStatus.js';
import { _cancelPdfPaneSave, _cleanupPdfDocState, _hasPendingPdfPaneSave, _releasePdfPane, _syncPdfPaneToDoc } from './pdfTools.js';
import { _emailRichbodyActive, _richTextContentToHtml, _syncEmailRichbody } from './richEditor/richBodySync.js';
import { isVisualMarkdownDoc } from './richEditor/markdownVisualSync.js';
import { suspendSuggestions } from './suggestions.js';

export function _rememberActiveDoc(docId) {
    const doc = docs.get(docId);
    const sessionId = doc?.sessionId || _lastSessionId || sessionModule?.getCurrentSessionId?.();
    if (sessionId && docId) localStorage.setItem(_docActiveKey(sessionId), docId);
  }

export function _visibleDocIdsForCurrentSession() {
    const curSession = sessionModule?.getCurrentSessionId() || '';
    const ids = [];
    for (const [id, doc] of docs) {
      if (doc.sessionId && curSession && doc.sessionId !== curSession) continue;
      ids.push(id);
    }
    return ids;
  }

export function _closeWithoutDeleting(deleteDoc = false) {
    if (!activeDocId) return;
    const closingId = activeDocId;
    _cancelAutoSave(closingId);
    _cancelPdfPaneSave(closingId);
    if (deleteDoc) {
      clearMarkdownDraft(docs.get(closingId));
      markDeleted(closingId);
      _cleanupPdfDocState(closingId);
      fetch(`${API_BASE}/api/document/${closingId}`, { method: 'DELETE' }).catch(() => {});
    }
    // Save the current state to the doc first so it persists in the library
    saveCurrentToMap();
    if (!deleteDoc) {
      const doc = docs.get(closingId);
      if (doc) {
        _saveClosingDraft(closingId, doc);
      }
    }
    removeDocument(closingId);
    const remaining = Array.from(docs.keys());
    if (remaining.length > 0) {
      switchToDoc(remaining[0]);
    } else {
      closePanel();
    }
    renderTabs();
  }

export function _detachDocFromSession(docId, { toast = false } = {}) {
    const doc = docs.get(docId);
    _cancelAutoSave(docId);
    _cancelPdfPaneSave(docId);
    _syncPdfPaneToDoc(docId, { markDirty: false });
    const hasContent = doc && (doc._visualSyncBlocked || (doc.content || '').trim().length > 0);
    if (hasContent) {
      _saveClosingDraft(docId, doc);
      if (toast && uiModule) uiModule.showToast('Document closed');
    } else {
      clearMarkdownDraft(doc);
      markDeleted(docId);
      fetch(`${API_BASE}/api/document/${docId}`, { method: 'DELETE' }).catch(() => {});
    }
    _forgetActiveDoc(doc?.sessionId || _lastSessionId, docId);
    removeDocument(docId);
    _syncDocIndicator();
  }

export async function closeTab(docId) {
    // Save current editor content to map so the check below uses fresh data
    saveCurrentToMap();
    // Closing the tab is a quiet detach action. The document-close
    // notification is reserved for closing the document surface itself;
    // showing it here makes a tab close look like a full document close.
    _detachDocFromSession(docId);
    // Find next tab in the current session
    const curSession = sessionModule?.getCurrentSessionId() || '';
    let nextId = null;
    for (const [id, d] of docs) {
      if (!d.sessionId || !curSession || d.sessionId === curSession) {
        nextId = id;
        break;
      }
    }
    if (!nextId) {
      setActiveDocId(null);
      closePanel();
      (document.getElementById('message') || document.getElementById('doc-indicator-btn'))?.focus();
      return;
    }
    if (activeDocId === docId) {
      switchToDoc(nextId);
    } else {
      renderTabs();
    }
  }

let _autoCreating = false;

export let _creatingDoc = false;

export async function _autoCreateFromInput(content) {
    if (_autoCreating) return;
    _autoCreating = true;
    const generation = bumpNavigationGeneration();
    try {
      let sessionId = _lastSessionId
        || (sessionModule && sessionModule.getCurrentSessionId());
      if (!sessionId) {
        sessionId = await _autoCreateSession();
      }
      const res = await fetch(`${API_BASE}/api/document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ session_id: sessionId, title: '', content, language: 'markdown' }),
      });
      if (!res.ok) throw new Error(`Document create failed: HTTP ${res.status}`);
      const doc = await res.json();
      if (!doc || !doc.id) throw new Error('Document create failed: missing id');

      addDocToTabs(doc, sessionId);
      if (generation !== _documentNavigationGeneration) {
        renderTabs();
        return;
      }

      const initialContent = document.getElementById('doc-editor-textarea')?.value ?? content;
      const d = docs.get(doc.id);
      if (d) d.content = initialContent;
      switchToDoc(doc.id);
      // Trigger auto-detect and auto-title
      setTimeout(attemptAutoDetect, 100);
      setTimeout(() => autoTitleFromContent(initialContent, doc.id), 300);
      // Auto-save
      _scheduleAutoSave(doc.id, 2000);
    } catch (e) {
      console.error('Failed to auto-create document from input:', e);
    } finally {
      _autoCreating = false;
    }
  }

export async function createDocument(sessionId) {
    if (_creatingDoc) return;
    _creatingDoc = true;
    const generation = bumpNavigationGeneration();
    // If the panel was in empty-state, the user may type into the editor
    // during the create round-trip - preserve that text into the new doc
    // instead of letting switchToDoc blank it.
    const wasEmpty = !activeDocId;
    try {
      const res = await fetch(`${API_BASE}/api/document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          session_id: sessionId,
          title: '',
          content: '',
          language: 'markdown',
        }),
      });
      if (!res.ok) throw new Error(`Document create failed: HTTP ${res.status}`);
      const doc = await res.json();
      if (!doc || !doc.id) throw new Error('Document create failed: missing id');

      addDocToTabs(doc, sessionId);

      if (generation !== _documentNavigationGeneration) {
        renderTabs();
        return;
      }

      if (!isOpen) openPanel();
      // Re-enable editor if it was in empty state
      let textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        textarea.disabled = false;
        _syncEditorPlaceholder();
      }
      // Capture text typed during the round-trip (only when starting from the
      // empty editor - don't steal another doc's content).
      const typed = (wasEmpty && textarea && textarea.value.trim()) ? textarea.value : '';
      switchToDoc(doc.id);
      if (typed) {
        textarea = document.getElementById('doc-editor-textarea');
        if (textarea) textarea.value = typed;
        const d = docs.get(doc.id);
        if (d) d.content = typed;
        const rich = _emailRichbodyActive();
        if (rich && d && _isRichTextLang(d.language)) {
          rich.innerHTML = _richTextContentToHtml(typed);
          _syncEmailRichbody(rich);
        } else if (d && isVisualMarkdownDoc(d)) {
          switchToDoc(doc.id, { captureCurrent: false });
        }
        syncHighlighting();
        _scheduleAutoSave(doc.id, 800);
      }
      textarea = document.getElementById('doc-editor-textarea');
      const focusTarget = _emailRichbodyActive() || textarea;
      if (focusTarget) focusTarget.focus();
    } catch (e) {
      if (generation !== _documentNavigationGeneration) return;
      console.error('Failed to create document:', e);
      if (uiModule) uiModule.showError('Failed to create document');
    } finally {
      _creatingDoc = false;
    }
  }

export function injectFreshDoc(doc) {
    if (!doc || !doc.id) return;
    const generation = bumpNavigationGeneration();
    const sessionId = doc.session_id || _lastSessionId || null;
    if (doc.language === 'email') {
      doc._skipLocalDraftOnce = true;
      try {
        const fields = _parseEmailHeader(doc.current_content || doc.content || '');
        _clearEmailLocalDraft(fields.sourceUid, fields.sourceFolder, fields.inReplyTo);
      } catch (_) {}
    }
    addDocToTabs(doc, sessionId);
    // Use _ensureDocPaneMounted (not `if (!isOpen) openPanel()`): when a draft
    // is composed from the email modal, `isOpen` can be stale-true while the
    // actual pane was torn down — a bare openPanel() early-returns and the doc
    // mounts into a wrong/half-built pane (rendered as a narrow sidebar on
    // mobile instead of its own full-screen window). This remounts it cleanly.
    _ensureDocPaneMounted();
    // Defer to the next frame so the panel DOM exists before switchToDoc
    // populates it. Do not call switchToDoc synchronously here: it saves the
    // previously active doc and can re-enter the email draft path while a reply
    // document is still being injected.
    return new Promise(resolve => requestAnimationFrame(() => {
      if (generation === _documentNavigationGeneration && docs.has(doc.id)) switchToDoc(doc.id);
      resolve();
    }));
  }

export function _ensureDocPaneMounted() {
    // Loading a specific document is an explicit open action. A minimized
    // registration can survive session switching on mobile, leaving the newly
    // selected document represented only by its bottom dock tab even though
    // the editor has mounted. Clear that stale dock state before deciding
    // whether the existing pane can be reused.
    if (Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel')) {
      setMinimizedDocId(null);
      Modals.unregister('doc-panel');
    }
    if (!isOpen || !document.getElementById('doc-editor-pane')) {
      setPanelOpen(false);
      openPanel();
    } else {
      _markDocVisibleState(_lastSessionId, 'open');
    }
  }

export async function loadDocument(docId) {
    const generation = bumpNavigationGeneration();
    _minimizeNotesForDocumentOpen();
    if (_closingDocs.has(docId)) {
      registerDocument(_closingDocs.get(docId));
      releaseClosingDraft(docId);
    }
    // If already in tabs, just switch
    if (docs.has(docId)) {
      _ensureDocPaneMounted();
      switchToDoc(docId);
      return true;
    }
    // Mount the editor before the network request so mobile gets immediate
    // feedback instead of appearing unchanged while the document loads.
    _ensureDocPaneMounted();
    _showLoadingOverlay();
    try {
      const res = await fetch(`${API_BASE}/api/document/${docId}`);
      if (!res.ok) throw new Error(res.status === 404 ? 'Not found' : `HTTP ${res.status}`);
      const doc = await res.json();
      if (generation !== _documentNavigationGeneration) return false;
      addDocToTabs(doc, doc.session_id);
      switchToDoc(doc.id);
      return true;
    } catch (e) {
      if (generation !== _documentNavigationGeneration) return false;
      _hideLoadingOverlay();
      console.error('Failed to load document:', e);
      if (uiModule) {
        const msg = e.message === 'Not found'
          ? 'Document not found — try opening it from the Library.'
          : 'Could not open document.';
        uiModule.showError(msg);
      }
      return false;
    }
  }

export async function ensureDocPanel() {
    let sessionId = _lastSessionId
      || (sessionModule && sessionModule.getCurrentSessionId());
    if (!sessionId) {
      try {
        sessionId = await _autoCreateSession();
      } catch (e) {
        console.error('Failed to auto-create session for document:', e);
        openPanel();
        return;
      }
    }
    await loadSessionDocs(sessionId);
  }

export async function _autoCreateSession({ adopt = true, forceNew = false } = {}) {
    // Materialize pending chat first if one exists
    if (!forceNew && sessionModule && sessionModule.hasPendingChat && sessionModule.hasPendingChat()) {
      await sessionModule.materializePendingSession();
      const id = sessionModule.getCurrentSessionId();
      if (id) { setLastSessionId(id); return id; }
    }
    // Preserve the current model when creating a doc session
    const curModel = sessionModule?.getCurrentModel ? sessionModule.getCurrentModel() : null;
    const sessionsRaw = sessionModule?.getSessions?.();
    const sessions = Array.isArray(sessionsRaw) ? sessionsRaw : [];
    const match = curModel && sessions.find(s => s.model === curModel && s.endpoint_url);
    const fd = new FormData();
    fd.append('name', `Notes ${new Date().toLocaleTimeString()}`);
    fd.append('skip_validation', 'true');
    if (match) {
      fd.append('endpoint_url', match.endpoint_url);
      fd.append('model', match.model);
      if (match.endpoint_id) fd.append('endpoint_id', match.endpoint_id);
    }
    const res = await fetch(`${API_BASE}/api/session`, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.detail || `Session create failed: HTTP ${res.status}`);
    const sessionId = payload.id;
    if (!sessionId) throw new Error('Session create returned no session ID');
    setLastSessionId(sessionId);
    // Tell sessions module so chat uses the same session
    if (adopt && sessionModule && sessionModule.setCurrentSessionId) {
      sessionModule.setCurrentSessionId(sessionId);
    }
    if (sessionModule && sessionModule.loadSessions) sessionModule.loadSessions();
    return sessionId;
  }

export async function loadSessionDocs(sessionId, opts = {}) {
    suspendSuggestions();
    const generation = bumpNavigationGeneration();
    _closeVersionPanel();
    setLastSessionId(sessionId);
    const restoreMode = !!opts.restoreMode;
    const shouldRestoreOpen = localStorage.getItem(_docOpenKey(sessionId)) === '1';
    const shouldRestoreMinimized = localStorage.getItem(_docMinimizedKey(sessionId)) === '1';

    // Save current editor content to map before tearing down
    if (activeDocId && docs.has(activeDocId)) {
      saveCurrentToMap();
    }

    // Flush any pending saves or dirty documents belonging to previous sessions
    for (const [id, doc] of docs) {
      if (doc.sessionId && doc.sessionId !== sessionId) {
        if (_pendingAutoSaves.has(id) || ['dirty', 'error'].includes(doc._saveState) || _hasPendingPdfPaneSave(id)) {
          _cancelAutoSave(id);
          _cancelPdfPaneSave(id);
          _saveClosingDraft(id, doc);
        } else if (doc._saveState === 'saving') {
          retainClosingDraft(id, doc);
        } else {
          _cancelAutoSave(id);
          _cancelPdfPaneSave(id);
        }
      }
    }

    // Clear docs from other sessions so tabs are per-session,
    // but keep session-less docs (e.g. email compose) - they are independent
    for (const [id, doc] of [...docs]) {
      if (doc.sessionId && doc.sessionId !== sessionId) removeDocument(id);
    }
    _releasePdfPane(activeDocId);
    setActiveDocId(null);

    // Show loading state while fetching
    if (isOpen) _showLoadingOverlay();

    try {
      const res = await fetch(`${API_BASE}/api/documents/${sessionId}`, {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!res.ok) throw new Error(`Session documents failed: HTTP ${res.status}`);
      const allDocs = await res.json();
      if (generation !== _documentNavigationGeneration) return;
      _hideLoadingOverlay();
      // Only load active docs
      const activeDocs = allDocs.filter(d => d.is_active);
      if (activeDocs.length === 0) {
        // No docs yet — show empty editor, doc will be created when user types
        if (!restoreMode || shouldRestoreOpen) {
          if (!isOpen) openPanel();
          showEmptyState();
          renderTabs();
        }
        return;
      }
      for (const doc of activeDocs) {
        // Always rehydrate from the canonical DB row. A cached tab shell can
        // survive panel/session restoration with an empty or stale `content`
        // field; skipping existing IDs then shows the document but not its
        // body after refresh.
        addDocToTabs(doc, sessionId);
      }
      _syncDocIndicator();
      // Restore the exact document that was active in this conversation.
      // Keeping only the panel-open bit made a hard refresh reopen the editor
      // on activeDocs[0], silently severing follow-up edits from the document
      // the user had actually been discussing.
      const rememberedDocId = localStorage.getItem(_docActiveKey(sessionId));
      const target = activeDocs.find(doc => doc.id === rememberedDocId) || activeDocs[0];
      if (rememberedDocId && target.id !== rememberedDocId) {
        localStorage.removeItem(_docActiveKey(sessionId));
      }
      if (restoreMode && !shouldRestoreOpen) {
        // Coming back to a chat with documents should advertise the doc without
        // stealing half the screen. Default to a docked chip; only reopen the
        // full editor when this session explicitly persisted an open state.
        setActiveDocId(target.id);
        setMinimizedDocId(target.id);
        _markDocVisibleState(sessionId, 'minimized');
        _ensureDocChipRegistered();
        if (isOpen) {
          try { switchToDoc(target.id); } catch (e) { console.error('Minimize restored doc failed:', e); }
          closePanel('down');
        } else {
          Modals.minimize('doc-panel');
        }
        return;
      }
      _markDocVisibleState(sessionId, 'open');
      if (!isOpen) openPanel({ restore: restoreMode && shouldRestoreOpen });
      switchToDoc(target.id);
    } catch (e) {
      if (generation !== _documentNavigationGeneration) return;
      _hideLoadingOverlay();
      console.error('Failed to load session documents:', e);
      // Open empty panel on error too
      if (!isOpen) openPanel();
      showEmptyState();
    }
  }

async function _moveDocToSession(docId, sessionId, { switchChat = false } = {}) {
    const doc = docs.get(docId);
    if (!doc || !sessionId) return false;
    if (docId === activeDocId) {
      try { await saveDocument({ silent: true }); } catch (_) {}
    }
    const res = await fetch(`${API_BASE}/api/document/${encodeURIComponent(docId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!res.ok) throw new Error(`Move failed: HTTP ${res.status}`);
    const updated = await res.json();
    doc.sessionId = updated.session_id || sessionId;
    doc.title = updated.title ?? doc.title;
    doc.language = updated.language ?? doc.language;
    doc.content = updated.current_content ?? doc.content;
    registerDocument(doc);
    setLastSessionId(sessionId);
    try { sessionModule?.setSessionHasDocs?.(sessionId, true); } catch (_) {}
    if (switchChat && sessionModule?.selectSession) {
      await sessionModule.selectSession(sessionId);
      addDocToTabs(updated, sessionId);
      switchToDoc(docId);
      if (!isOpen) openPanel();
    } else {
      renderTabs();
    }
    _syncDocIndicator();
    return true;
  }

export async function moveActiveDocumentToCurrentChat({ quiet = false } = {}) {
    if (!activeDocId) return;
    let sessionId = sessionModule?.getCurrentSessionId?.() || '';
    if (!sessionId) sessionId = await _autoCreateSession();
    try {
      await _moveDocToSession(activeDocId, sessionId);
      if (!quiet) uiModule?.showToast?.('Document moved to current chat');
    } catch (e) {
      console.error('Failed to move document to current chat:', e);
      uiModule?.showError?.('Could not move document to current chat');
    }
  }

export async function moveActiveDocumentToNewChat() {
    if (!activeDocId) return;
    try {
      // Do not adopt the empty destination before the active document has
      // saved and moved. Switching currentSessionId early made saveDocument
      // operate against the wrong chat and aborted the move client-side.
      const sessionId = await _autoCreateSession({ adopt: false, forceNew: true });
      await _moveDocToSession(activeDocId, sessionId, { switchChat: true });
      uiModule?.showToast?.('Document moved to new chat');
    } catch (e) {
      console.error('Failed to move document to new chat:', e);
      uiModule?.showError?.('Could not move document to new chat');
    }
  }

export async function deleteActiveDocument() {
    if (!activeDocId) return;
    const idToDelete = activeDocId;
    if (_inFlightDeletes.has(idToDelete)) return _inFlightDeletes.get(idToDelete);

    const deleteTask = (async () => {
      let hadPendingAutoSave = false;
      let pendingAutoSaveEntry = null;
      let hadPendingPdfSave = false;
      let wasDirty = false;
      let confirmed = false;
      try {
        const doc = docs.get(idToDelete);
        const name = doc ? doc.title : 'this document';
        const ok = uiModule && uiModule.styledConfirm
          ? await uiModule.styledConfirm(`Delete "${name}"?`, { confirmText: 'Delete', danger: true })
          : confirm(`Delete "${name}"?`);
        if (!ok) return;
        confirmed = true;

        pendingAutoSaveEntry = _pendingAutoSaves.get(idToDelete) || null;
        hadPendingAutoSave = Boolean(pendingAutoSaveEntry);
        hadPendingPdfSave = _hasPendingPdfPaneSave(idToDelete);
        // Preserve the draft before stopping view timers. Rollback must also
        // work after navigation or disposal removes every PDF control.
        _syncPdfPaneToDoc(idToDelete, { markDirty: false });
        if (activeDocId === idToDelete) saveCurrentToMap();
        wasDirty = ((docs.get(idToDelete) || _closingDocs.get(idToDelete))?._saveState === 'dirty');
        _cancelAutoSave(idToDelete);
        _cancelPdfPaneSave(idToDelete);

        const targetDoc = docs.get(idToDelete) || _closingDocs.get(idToDelete);
        if (targetDoc?._saveQueueTail) {
          await targetDoc._saveQueueTail.catch(() => {});
        }
        const res = await fetch(`${API_BASE}/api/document/${idToDelete}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`Delete failed: HTTP ${res.status}`);

        clearMarkdownDraft(docs.get(idToDelete) || _closingDocs.get(idToDelete));
        markDeleted(idToDelete);
        _cleanupPdfDocState(idToDelete);
        // Remove tab
        const tab = document.querySelector(`.doc-tab[data-doc-id="${idToDelete}"]`);
        if (tab) tab.remove();
        _forgetActiveDoc(targetDoc?.sessionId || _lastSessionId, idToDelete);
        removeDocument(idToDelete);
        releaseClosingDraft(idToDelete);
        // Switch to another doc or close panel when deleting the currently viewed doc
        if (activeDocId === idToDelete) {
          const remaining = Array.from(docs.keys());
          if (remaining.length > 0) {
            switchToDoc(remaining[0]);
          } else {
            setActiveDocId(null);
            closePanel();
          }
        } else {
          renderTabs();
          _syncDocIndicator();
        }
        if (uiModule) uiModule.showToast('Document deleted');
      } catch (e) {
        console.error('Failed to delete document:', e);
        const draft = docs.get(idToDelete) || _closingDocs.get(idToDelete);
        if (confirmed && draft && !_deletedDocIds.has(idToDelete)) {
          if (hadPendingPdfSave || hadPendingAutoSave || wasDirty || draft._saveState === 'dirty' || draft._saveState === 'error') {
            _scheduleAutoSave(idToDelete, 800, pendingAutoSaveEntry?.options || {});
          }
        }
        if (uiModule) uiModule.showError('Failed to delete document');
      } finally {
        endDelete(idToDelete);
      }
    })();

    beginDelete(idToDelete, deleteTask);
    return deleteTask;
  }

export function getChatDocumentId() {
    // A minimized document remains the document linked to this conversation.
    // On mobile, minimizing the sheet is the only way to reach the composer;
    // treating that layout action as unlinking erased the document exactly
    // when the user tried to say "edit this". A real tab close clears
    // `_minimizedDocId` and removes the document from `docs`.
    const pane = document.getElementById('doc-editor-pane');
    const style = pane ? window.getComputedStyle(pane) : null;
    const visiblyOpen = !!(
      activeDocId
      && pane?.isConnected
      && !(Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel'))
      && style?.display !== 'none'
      && style?.visibility !== 'hidden'
      && style?.opacity !== '0'
    );
    const minimizedId = (
      Modals.isRegistered('doc-panel')
      && Modals.isMinimized('doc-panel')
      && _minimizedDocId
    ) ? _minimizedDocId : null;
    const id = visiblyOpen ? activeDocId : minimizedId;
    return id && docs.has(id) ? id : null;
  }
