// tests/documentPersistence.test.mjs
/**
 * Dedicated persistence contract and lifecycle tests for the Odysseus Document Editor.
 * Validates save serialization, draft retention, concurrent writes, failure recovery,
 * deletion guards, navigation safety, and PDF hooks.
 */

import test, { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  API_BASE,
  setApiBase,
  activeDocId,
  setActiveDocId,
  isOpen,
  setPanelOpen,
  docs,
  _closingDocs,
  _deletedDocIds,
  _inFlightDeletes,
  registerDocument,
  removeDocument,
  clearDocuments,
  markDeleted,
  beginDelete,
  endDelete,
  retainClosingDraft,
  releaseClosingDraft,
  bumpNavigationGeneration,
  _documentNavigationGeneration
} from '../frontend/src/features/documents/application/documentState.js';

import {
  bindDocumentCommands,
  _resetDocumentCommandsForTesting
} from '../frontend/src/features/documents/application/documentCommands.js';

import {
  _autoSaveDebounce,
  _cancelAutoSave,
  _emailRichbodySaveDebounce,
  _flushAutoSave,
  _markDocumentDirty,
  _pendingAutoSaves,
  _saveClosingDraft,
  _scheduleAutoSave,
  _setDocumentSaveState,
  saveDocument,
  awaitWrites,
  runSerializedWrite,
  bindPersistenceHooks,
  _resetPersistenceHooksForTesting
} from '../frontend/src/features/documents/application/persistence.js';

// Helper for deterministic deferred promises
function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('Document Persistence Engine', () => {
  let commandsLog;
  let fetchCalls;
  let originalFetch;
  let originalDocument;
  let originalWindow;
  let mockUiModule;

  beforeEach(() => {
    // Reset state store
    setApiBase('http://localhost:8000');
    setActiveDocId(null);
    setPanelOpen(false);
    clearDocuments();
    _deletedDocIds.clear();
    _inFlightDeletes.clear();
    _closingDocs.clear();

    // Reset persistence debounce & queue
    _pendingAutoSaves.clear();
    _resetPersistenceHooksForTesting();

    // Track command invocations
    commandsLog = {
      saveCurrentToMap: 0,
      renderTabs: 0,
      _syncDocIndicator: 0
    };

    _resetDocumentCommandsForTesting();
    bindDocumentCommands({
      openPanel: () => {},
      closePanel: () => {},
      switchToDoc: () => {},
      saveCurrentToMap: () => { commandsLog.saveCurrentToMap++; },
      renderTabs: () => { commandsLog.renderTabs++; },
      _syncDocIndicator: () => { commandsLog._syncDocIndicator++; },
      _syncHeaderActions: () => {},
      showEmptyState: () => {},
      _showLoadingOverlay: () => {},
      _hideLoadingOverlay: () => {},
      _minimizeNotesForDocumentOpen: () => {},
      _ensureDocChipRegistered: () => {}
    }, { idempotent: true });

    // Mock UI module
    mockUiModule = {
      toasts: [],
      errors: [],
      showToast(msg) { this.toasts.push(msg); },
      showError(msg) { this.errors.push(msg); }
    };
    globalThis.uiModule = mockUiModule;

    // Mock DOM elements
    const mockElements = new Map();
    originalDocument = globalThis.document;
    globalThis.document = {
      getElementById(id) {
        if (!mockElements.has(id)) {
          mockElements.set(id, {
            id,
            textContent: '',
            style: { display: '' },
            dataset: {},
            title: '',
            setAttribute(k, v) { this[k] = v; },
            querySelector() { return { textContent: '' }; }
          });
        }
        return mockElements.get(id);
      }
    };

    // Mock fetch tracking
    fetchCalls = [];
    originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      fetchCalls.push({ url, options, body: options?.body ? JSON.parse(options.body) : null });
      return {
        ok: true,
        status: 200,
        json: async () => ({ id: 'doc-1', version_count: 2 })
      };
    };
  });

  // -------------------------------------------------------------------------
  // 1. Successful manual and automated saves
  // -------------------------------------------------------------------------
  describe('Successful Save Operations', () => {
    it('saves the active document and increments its version', async () => {
      const doc = { id: 'doc-1', title: 'Test Document', content: 'hello world', version: 1, _editRevision: 1 };
      registerDocument(doc);
      setActiveDocId('doc-1');

      const ok = await saveDocument({ silent: false, docId: 'doc-1' });

      assert.equal(ok, true, 'saveDocument should succeed');
      assert.equal(fetchCalls.length, 1);
      assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/document/doc-1');
      assert.equal(fetchCalls[0].options.method, 'PUT');
      assert.equal(fetchCalls[0].body.content, 'hello world');
      assert.equal(doc.version, 2, 'document version should be updated to 2');
      assert.equal(doc._saveState, 'saved');
      assert.equal(commandsLog.renderTabs, 1, 'renderTabs should be called on version change');
      assert.equal(commandsLog._syncDocIndicator, 1);
      assert.equal(mockUiModule.toasts.length, 1);
      assert.equal(mockUiModule.toasts[0], 'Document saved');
    });

    it('suppresses toast notifications when silent is true', async () => {
      const doc = { id: 'doc-1', title: 'Test Document', content: 'hello world', version: 1 };
      registerDocument(doc);

      const ok = await saveDocument({ silent: true, docId: 'doc-1' });

      assert.equal(ok, true);
      assert.equal(mockUiModule.toasts.length, 0, 'silent save must not display toast');
    });

    it('displays new version saved toast when forceVersion is true', async () => {
      const doc = { id: 'doc-1', title: 'Test Document', content: 'hello world', version: 1 };
      registerDocument(doc);

      const ok = await saveDocument({ silent: false, forceVersion: true, docId: 'doc-1' });

      assert.equal(ok, true);
      assert.equal(fetchCalls[0].body.force_version, true);
      assert.equal(fetchCalls[0].body.summary, 'Saved version');
      assert.equal(mockUiModule.toasts[0], 'New version saved');
    });

    it('updates doc-version-badge element when present in DOM', async () => {
      const doc = { id: 'doc-1', title: 'Test Document', content: 'hello world', version: 1 };
      registerDocument(doc);
      setActiveDocId('doc-1');

      const badge = globalThis.document.getElementById('doc-version-badge');
      await saveDocument({ silent: true, docId: 'doc-1' });

      assert.equal(badge.textContent, 'v2');
      assert.equal(badge.style.display, '');
    });

    it('routes version badge updates through bound _renderVersionBadge port without direct DOM access', async () => {
      const badgeCalls = [];
      bindPersistenceHooks({
        _renderVersionBadge(docId, version) {
          badgeCalls.push({ docId, version });
        }
      }, { idempotent: true });

      const badge = globalThis.document.getElementById('doc-version-badge');
      badge.textContent = 'untouched';
      badge.style.display = 'none';

      const doc = { id: 'doc-1', title: 'Test Document', content: 'hello world', version: 1 };
      registerDocument(doc);
      setActiveDocId('doc-1');

      await saveDocument({ silent: true, docId: 'doc-1' });

      assert.deepEqual(badgeCalls, [{ docId: 'doc-1', version: 2 }]);
      assert.equal(badge.textContent, 'untouched', 'bound port must replace direct DOM mutation inside saveDocument');
      assert.equal(badge.style.display, 'none');
    });
  });

  // -------------------------------------------------------------------------
  // 2. Failed-save recovery and draft preservation
  // -------------------------------------------------------------------------
  describe('Failed Save Recovery and Draft Integrity', () => {
    it('sets saveState to error and preserves local draft on HTTP 500', async () => {
      globalThis.fetch = async () => ({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error'
      });

      const doc = { id: 'doc-1', title: 'Important Notes', content: 'unpersisted data', version: 1, _editRevision: 1 };
      registerDocument(doc);

      const ok = await saveDocument({ silent: false, docId: 'doc-1' });

      assert.equal(ok, false, 'saveDocument must return false on HTTP error');
      assert.equal(doc._saveState, 'error');
      assert.equal(doc.content, 'unpersisted data', 'local draft must never be cleared or discarded');
      assert.equal(docs.get('doc-1'), doc, 'document record identity must be preserved');
      assert.equal(mockUiModule.errors.length, 1);
      assert.equal(mockUiModule.errors[0], 'Failed to save document');
    });

    it('preserves draft and transitions to error on network failure', async () => {
      globalThis.fetch = async () => {
        throw new Error('Network offline');
      };

      const doc = { id: 'doc-1', title: 'Draft', content: 'offline content', version: 1 };
      registerDocument(doc);

      const ok = await saveDocument({ silent: false, docId: 'doc-1' });

      assert.equal(ok, false);
      assert.equal(doc._saveState, 'error');
      assert.equal(doc.content, 'offline content');
      assert.equal(mockUiModule.errors.length, 1);
      assert.equal(mockUiModule.errors[0], 'Failed to save document');
    });

    it('sets error and returns false on silent email 404', async () => {
      globalThis.fetch = async () => ({
        ok: false,
        status: 404
      });

      const doc = { id: 'doc-email', title: 'Reply', content: 'reply body', language: 'email', version: 1 };
      registerDocument(doc);

      const ok = await saveDocument({ silent: true, docId: 'doc-email' });

      assert.equal(ok, false);
      assert.equal(doc._saveState, 'error');
      assert.equal(doc.content, 'reply body');
    });
  });

  // -------------------------------------------------------------------------
  // 3. Write serialization for single and multiple documents
  // -------------------------------------------------------------------------
  describe('Per-Document Write Serialization', () => {
    it('serializes concurrent saves for the same document in order', async () => {
      const doc = { id: 'doc-1', content: 'initial', version: 1, _editRevision: 1 };
      registerDocument(doc);

      const deferred1 = createDeferred();
      const deferred2 = createDeferred();
      let putCount = 0;

      globalThis.fetch = async (url, opts) => {
        putCount++;
        const currentCount = putCount;
        const body = JSON.parse(opts.body);
        fetchCalls.push({ url, options: opts, body });
        if (currentCount === 1) {
          await deferred1.promise;
          return { ok: true, json: async () => ({ version_count: 2 }) };
        } else {
          await deferred2.promise;
          return { ok: true, json: async () => ({ version_count: 3 }) };
        }
      };

      // Initiate first save
      const save1Promise = saveDocument({ silent: true, docId: 'doc-1' });
      await Promise.resolve();
      await Promise.resolve();

      // While save1 is in flight, mutate content and start save2
      doc.content = 'intermediate change';
      doc._editRevision = 2;
      const save2Promise = saveDocument({ silent: true, docId: 'doc-1' });
      await Promise.resolve();
      await Promise.resolve();

      // Verify save2 is queued and waiting for save1
      assert.equal(fetchCalls.length, 1, 'second PUT request must wait for first PUT to settle');
      assert.equal(fetchCalls[0].body.content, 'initial');

      // Release save1
      deferred1.resolve();
      const res1 = await save1Promise;
      assert.equal(res1, true);
      await Promise.resolve();
      await Promise.resolve();

      // Now save2 must have started with the authoritative draft
      assert.equal(fetchCalls.length, 2, 'second PUT must start after first resolves');
      assert.equal(fetchCalls[1].body.content, 'intermediate change');

      deferred2.resolve();
      const res2 = await save2Promise;
      assert.equal(res2, true);
      assert.equal(doc.version, 3);
      assert.equal(doc._saveState, 'saved');
    });

    it('captures authoritative draft at queue-turn time, not admission time', async () => {
      const doc = { id: 'doc-1', content: 'admission-time content', version: 1, _editRevision: 1 };
      registerDocument(doc);

      const deferred = createDeferred();
      let first = true;
      globalThis.fetch = async (url, opts) => {
        fetchCalls.push({ url, options: opts, body: JSON.parse(opts.body) });
        if (first) {
          first = false;
          await deferred.promise;
          return { ok: true, json: async () => ({ version_count: 2 }) };
        }
        return { ok: true, json: async () => ({ version_count: 3 }) };
      };

      // Start save 1
      const p1 = saveDocument({ silent: true, docId: 'doc-1' });

      // Queue save 2
      const p2 = saveDocument({ silent: true, docId: 'doc-1' });

      // Before save 1 releases turn, update document content (simulating restore or late input)
      doc.content = 'queue-turn-time content';
      doc._editRevision = 3;

      deferred.resolve();
      await Promise.all([p1, p2]);

      assert.equal(fetchCalls[1].body.content, 'queue-turn-time content');
    });

    it('executes concurrent saves for separate documents independently', async () => {
      const docA = { id: 'doc-A', content: 'AAA', version: 1 };
      const docB = { id: 'doc-B', content: 'BBB', version: 1 };
      registerDocument(docA);
      registerDocument(docB);

      const defA = createDeferred();
      const defB = createDeferred();

      globalThis.fetch = async (url, opts) => {
        fetchCalls.push({ url, options: opts, body: JSON.parse(opts.body) });
        if (url.includes('doc-A')) {
          await defA.promise;
          return { ok: true, json: async () => ({ version_count: 2 }) };
        }
        if (url.includes('doc-B')) {
          await defB.promise;
          return { ok: true, json: async () => ({ version_count: 5 }) };
        }
      };

      const pA = saveDocument({ silent: true, docId: 'doc-A' });
      const pB = saveDocument({ silent: true, docId: 'doc-B' });
      await Promise.resolve();
      await Promise.resolve();

      // Both should have dispatched their PUT immediately without waiting for the other
      assert.equal(fetchCalls.length, 2, 'independent documents must not serialize behind each other');

      defB.resolve();
      await pB;
      assert.equal(docB.version, 5);
      assert.equal(docA.version, 1, 'doc-A must still be awaiting resolution');

      defA.resolve();
      await pA;
      assert.equal(docA.version, 2);
    });
  });

  // -------------------------------------------------------------------------
  // 4. Edits occurring during in-flight save (revision tracking)
  // -------------------------------------------------------------------------
  describe('In-Flight Edits and Revision Check', () => {
    it('leaves saveState as dirty if user edits document while save is in flight', async () => {
      const doc = { id: 'doc-1', content: 'rev1 content', version: 1, _editRevision: 1 };
      registerDocument(doc);

      const deferred = createDeferred();
      globalThis.fetch = async (url, opts) => {
        fetchCalls.push({ url, options: opts, body: JSON.parse(opts.body) });
        // User types while HTTP request is in flight
        _markDocumentDirty('doc-1');
        doc.content = 'rev2 content';
        await deferred.promise;
        return { ok: true, json: async () => ({ version_count: 2 }) };
      };

      const savePromise = saveDocument({ silent: true, docId: 'doc-1' });

      // Server acknowledges rev1 snapshot
      deferred.resolve();
      const ok = await savePromise;

      assert.equal(ok, true);
      assert.equal(doc.version, 2);
      // Because _editRevision (2) !== saveRevision (1), doc must remain dirty!
      assert.equal(doc._saveState, 'dirty', 'saveState must remain dirty when edit revision changed during save');
      assert.equal(doc.content, 'rev2 content', 'user edit must not be overwritten');
    });
  });

  // -------------------------------------------------------------------------
  // 5. Closing draft retention and concurrent draft release
  // -------------------------------------------------------------------------
  describe('Closing Draft Retention and Recovery', () => {
    it('retains closing document draft in _closingDocs until save succeeds', async () => {
      const doc = { id: 'doc-1', title: 'Closing Doc', content: 'draft content', version: 1, _saveState: 'dirty' };
      registerDocument(doc);

      const deferred = createDeferred();
      globalThis.fetch = async () => {
        await deferred.promise;
        return { ok: true, json: async () => ({ version_count: 2 }) };
      };

      // Tab closed: retain closing draft and start background save
      const closePromise = _saveClosingDraft('doc-1', doc);
      assert.equal(_closingDocs.get('doc-1'), doc, 'draft must be retained in _closingDocs');

      // Simulate tab removal from active docs Map
      removeDocument('doc-1');
      assert.equal(docs.has('doc-1'), false);
      assert.equal(_closingDocs.has('doc-1'), true);

      deferred.resolve();
      const ok = await closePromise;

      assert.equal(ok, true);
      assert.equal(_closingDocs.has('doc-1'), false, 'closing draft must be released once saved');
    });

    it('preserves closing draft in _closingDocs if background save fails', async () => {
      const doc = { id: 'doc-1', title: 'Closing Doc', content: 'unrecoverable unless saved', version: 1 };
      registerDocument(doc);

      globalThis.fetch = async () => ({
        ok: false,
        status: 500
      });

      const closePromise = _saveClosingDraft('doc-1', doc);
      removeDocument('doc-1');

      const ok = await closePromise;

      assert.equal(ok, false);
      assert.equal(_closingDocs.get('doc-1'), doc, 'closing draft must remain retained when save fails');
      assert.equal(doc._saveState, 'error');

      // Reopening recovers the exact draft record
      registerDocument(_closingDocs.get('doc-1'));
      assert.equal(docs.get('doc-1').content, 'unrecoverable unless saved');
    });

    it('preserves newer detached record if an earlier save completion settles late', async () => {
      const oldDoc = { id: 'doc-1', content: 'old draft', version: 1, _saveState: 'saving' };
      const newDoc = { id: 'doc-1', content: 'new draft', version: 2, _saveState: 'saving' };

      retainClosingDraft('doc-1', oldDoc);
      // Simulate concurrent tab reopen and re-close with newer record
      retainClosingDraft('doc-1', newDoc);
      assert.equal(_closingDocs.get('doc-1'), newDoc);

      // Old save finishes late: releaseClosingDraft with oldDoc identity must NOT delete newDoc!
      const released = releaseClosingDraft('doc-1', oldDoc);
      assert.equal(released, false, 'must reject release when record identity does not match');
      assert.equal(_closingDocs.get('doc-1'), newDoc, 'newer closing draft must remain intact');

      // New save finishes: matches identity and releases
      const releasedNew = releaseClosingDraft('doc-1', newDoc);
      assert.equal(releasedNew, true);
      assert.equal(_closingDocs.has('doc-1'), false);
    });
  });

  // -------------------------------------------------------------------------
  // 6. Deletion safety and late acknowledgement protection
  // -------------------------------------------------------------------------
  describe('Document Deletion and Tombstone Invariants', () => {
    it('aborts save immediately if document is marked deleted before write', async () => {
      markDeleted('doc-1');

      const ok = await saveDocument({ silent: true, docId: 'doc-1' });

      assert.equal(ok, false);
      assert.equal(fetchCalls.length, 0, 'must not send HTTP request for tombstoned document');
    });

    it('awaits in-flight deletion before evaluating save', async () => {
      const doc = { id: 'doc-1', content: 'dying draft', version: 1 };
      registerDocument(doc);

      const deleteDeferred = createDeferred();
      beginDelete('doc-1', deleteDeferred.promise);

      let saveResolved = false;
      const savePromise = saveDocument({ silent: true, docId: 'doc-1' }).then(r => {
        saveResolved = true;
        return r;
      });

      // While delete is in flight, save must await
      await new Promise(r => setTimeout(r, 10));
      assert.equal(saveResolved, false);

      // Complete delete
      markDeleted('doc-1');
      endDelete('doc-1');
      deleteDeferred.resolve();

      const ok = await savePromise;
      assert.equal(ok, false, 'save must abort once deletion confirms');
      assert.equal(fetchCalls.length, 0);
    });

    it('ignores late HTTP response if document was deleted while save was in flight', async () => {
      const doc = { id: 'doc-1', content: 'content', version: 1 };
      registerDocument(doc);

      const deferred = createDeferred();
      globalThis.fetch = async () => {
        await deferred.promise;
        return { ok: true, json: async () => ({ version_count: 2 }) };
      };

      const savePromise = saveDocument({ silent: true, docId: 'doc-1' });

      // User deletes document while PUT is in flight
      markDeleted('doc-1');
      removeDocument('doc-1');

      // Late response arrives
      deferred.resolve();
      const ok = await savePromise;

      assert.equal(ok, false, 'late acknowledgement must return false');
      assert.equal(docs.has('doc-1'), false, 'deleted document must not be resurrected');
      assert.equal(commandsLog.renderTabs, 0, 'tabs must not be re-rendered for deleted doc');
    });
  });

  // -------------------------------------------------------------------------
  // 7. Navigation generation and panel state
  // -------------------------------------------------------------------------
  describe('Navigation Generation Protection', () => {
    it('captures DOM content via saveCurrentToMap only when panel is open and activeDoc matches', async () => {
      const doc = { id: 'doc-1', content: 'cached', version: 1 };
      registerDocument(doc);
      setActiveDocId('doc-1');
      setPanelOpen(true);

      await saveDocument({ silent: true, docId: 'doc-1' });
      assert.ok(commandsLog.saveCurrentToMap >= 1, 'saveCurrentToMap must be called when active and open');

      // When saving inactive document while another is open, do not read DOM
      commandsLog.saveCurrentToMap = 0;
      const doc2 = { id: 'doc-2', content: 'background', version: 1 };
      registerDocument(doc2);

      await saveDocument({ silent: true, docId: 'doc-2' });
      assert.equal(commandsLog.saveCurrentToMap, 0, 'saveCurrentToMap must not be called for background doc');
    });

    it('preserves navigation generation consistency across async yields', async () => {
      const doc = { id: 'doc-1', content: 'content', version: 1 };
      registerDocument(doc);

      const genBefore = _documentNavigationGeneration;
      bumpNavigationGeneration();
      const genAfter = _documentNavigationGeneration;

      assert.equal(genAfter, genBefore + 1);
    });
  });

  // -------------------------------------------------------------------------
  // 8. Autosave debounce, timers, and flushing
  // -------------------------------------------------------------------------
  describe('Autosave Scheduling and Debounce Timers', () => {
    it('schedules autosave with debounce and executes after delay', async () => {
      const doc = { id: 'doc-1', content: 'autosave draft', version: 1 };
      registerDocument(doc);

      const deferred = createDeferred();
      globalThis.fetch = async (url, opts) => {
        fetchCalls.push({ url, options: opts, body: JSON.parse(opts.body) });
        deferred.resolve();
        return { ok: true, json: async () => ({ version_count: 2 }) };
      };

      _scheduleAutoSave('doc-1', 20);
      assert.equal(_pendingAutoSaves.has('doc-1'), true);

      await deferred.promise;
      assert.equal(_pendingAutoSaves.has('doc-1'), false);
      assert.equal(fetchCalls.length, 1);
    });

    it('cancels pending autosave on _cancelAutoSave', async () => {
      const doc = { id: 'doc-1', content: 'cancelled draft', version: 1 };
      registerDocument(doc);

      _scheduleAutoSave('doc-1', 50);
      assert.equal(_pendingAutoSaves.has('doc-1'), true);

      _cancelAutoSave('doc-1');
      assert.equal(_pendingAutoSaves.has('doc-1'), false);

      await new Promise(r => setTimeout(r, 70));
      assert.equal(fetchCalls.length, 0, 'cancelled autosave must not trigger fetch');
    });

    it('immediately flushes pending autosave via _flushAutoSave', async () => {
      const doc = { id: 'doc-1', content: 'flushed draft', version: 1 };
      registerDocument(doc);

      _scheduleAutoSave('doc-1', 2000);
      assert.equal(_pendingAutoSaves.has('doc-1'), true);

      const flushed = await _flushAutoSave('doc-1');

      assert.equal(flushed, true);
      assert.equal(_pendingAutoSaves.has('doc-1'), false);
      assert.equal(fetchCalls.length, 1);
    });

    it('flushes dirty document even if debounce timer was not queued', async () => {
      const doc = { id: 'doc-1', content: 'dirty without timer', version: 1, _saveState: 'dirty' };
      registerDocument(doc);

      const flushed = await _flushAutoSave('doc-1');

      assert.equal(flushed, true);
      assert.equal(fetchCalls.length, 1);
    });
  });

  // -------------------------------------------------------------------------
  // 9. Version restoration and serialized write integration
  // -------------------------------------------------------------------------
  describe('Version Restoration Serialization', () => {
    it('executes serialized writes through saveDocument.runSerializedWrite', async () => {
      const doc = { id: 'doc-1', content: 'current', version: 2 };
      registerDocument(doc);

      const defSave = createDeferred();
      globalThis.fetch = async () => {
        await defSave.promise;
        return { ok: true, json: async () => ({ version_count: 3 }) };
      };

      // In-flight save
      const savePromise = saveDocument({ silent: true, docId: 'doc-1' });

      // Concurrent version restore queued
      let restoreExecuted = false;
      const restorePromise = runSerializedWrite('doc-1', async () => {
        restoreExecuted = true;
        doc.content = 'restored version 1';
        doc.version = 1;
        return 'restored';
      });

      // Verify restore waits for in-flight save
      await new Promise(r => setTimeout(r, 10));
      assert.equal(restoreExecuted, false, 'restore must wait for in-flight write to complete');

      defSave.resolve();
      await savePromise;

      const restoreResult = await restorePromise;
      assert.equal(restoreResult, 'restored');
      assert.equal(restoreExecuted, true);
      assert.equal(doc.content, 'restored version 1');
    });

    it('awaits queue tail settlement with awaitWrites', async () => {
      const doc = { id: 'doc-1', content: 'pending', version: 1 };
      registerDocument(doc);

      const deferred = createDeferred();
      globalThis.fetch = async () => {
        await deferred.promise;
        return { ok: true, json: async () => ({ version_count: 2 }) };
      };

      saveDocument({ silent: true, docId: 'doc-1' });

      let settled = false;
      const waitPromise = awaitWrites('doc-1').then(() => {
        settled = true;
      });

      await new Promise(r => setTimeout(r, 10));
      assert.equal(settled, false);

      deferred.resolve();
      await waitPromise;
      assert.equal(settled, true);
    });
  });

  // -------------------------------------------------------------------------
  // 10. PDF and view hook boundaries
  // -------------------------------------------------------------------------
  describe('PDF and View Integration Hooks', () => {
    it('invokes PDF cancellation and sync hooks prior to network PUT', async () => {
      const doc = { id: 'doc-pdf', content: 'pdf annotations', version: 1 };
      registerDocument(doc);

      let pdfCancelCalled = false;
      let pdfSyncCalled = false;

      bindPersistenceHooks({
        _cancelPdfPaneSave: (id) => { if (id === 'doc-pdf') pdfCancelCalled = true; },
        _syncPdfPaneToDoc: (id) => { if (id === 'doc-pdf') pdfSyncCalled = true; }
      });

      await saveDocument({ silent: true, docId: 'doc-pdf' });

      assert.equal(pdfCancelCalled, true, '_cancelPdfPaneSave must be called');
      assert.equal(pdfSyncCalled, true, '_syncPdfPaneToDoc must be called');
    });

    it('skips PDF sync and current DOM capture when skipPdfSync is true', async () => {
      const doc = { id: 'doc-pdf', content: 'pdf', version: 1 };
      registerDocument(doc);
      setActiveDocId('doc-pdf');
      setPanelOpen(true);

      let pdfSyncCalled = false;
      bindPersistenceHooks({
        _syncPdfPaneToDoc: () => { pdfSyncCalled = true; }
      });

      await saveDocument({ silent: true, docId: 'doc-pdf', skipPdfSync: true });

      assert.equal(pdfSyncCalled, false, '_syncPdfPaneToDoc must be skipped when skipPdfSync is true');
      assert.equal(commandsLog.saveCurrentToMap, 0, 'saveCurrentToMap must be skipped when skipPdfSync is true');
    });

    it('flushes autosave when _hasPendingPdfPaneSave hook reports true', async () => {
      const doc = { id: 'doc-pdf', content: 'clean in memory', version: 1, _saveState: 'saved' };
      registerDocument(doc);

      bindPersistenceHooks({
        _hasPendingPdfPaneSave: (id) => id === 'doc-pdf'
      });

      const flushed = await _flushAutoSave('doc-pdf');

      assert.equal(flushed, true, 'must flush when PDF pane has pending unsaved work');
      assert.equal(fetchCalls.length, 1);
    });

    it('resolves document id from getChatDocumentId hook when docId is omitted', async () => {
      const doc = { id: 'doc-chat', content: 'chat doc', version: 1 };
      registerDocument(doc);

      bindPersistenceHooks({
        getChatDocumentId: () => 'doc-chat'
      });

      const ok = await saveDocument({ silent: true });

      assert.equal(ok, true);
      assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/document/doc-chat');
    });
  });
});
