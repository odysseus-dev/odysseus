// tests/documentStateContract.mjs
import test, { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  API_BASE,
  isOpen,
  _deletedDocIds,
  _inFlightDeletes,
  _closingDocs,
  _documentNavigationGeneration,
  activeDocId,
  _lastSessionId,
  docs,
  _minimizedDocId,
  isPanelOpen,
  setApiBase,
  setActiveDocId,
  setLastSessionId,
  bumpNavigationGeneration,
  setPanelOpen,
  setMinimizedDocId,
  registerDocument,
  removeDocument,
  clearDocuments,
  markDeleted,
  beginDelete,
  endDelete,
  retainClosingDraft,
  releaseClosingDraft
} from '../frontend/src/features/documents/application/documentState.js';

import {
  bindDocumentCommands,
  _resetDocumentCommandsForTesting,
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
  _ensureDocChipRegistered
} from '../frontend/src/features/documents/application/documentCommands.js';

describe('Document State Contract', () => {
  beforeEach(() => {
    // Reset state to baseline values before each test
    setApiBase('');
    setActiveDocId(null);
    setLastSessionId('');
    setPanelOpen(false);
    setMinimizedDocId(null);
    clearDocuments();
    _deletedDocIds.clear();
    _inFlightDeletes.clear();
    _closingDocs.clear();
  });

  describe('Baseline State Declarations and Map Identity', () => {
    it('preserves docs Map reference identity across operations', () => {
      const initialDocsRef = docs;
      assert.ok(docs instanceof Map, 'docs must be an instance of Map');

      registerDocument({ id: 'doc-1', title: 'First Doc', content: 'alpha' });
      assert.equal(docs, initialDocsRef, 'docs identity must remain unchanged after registerDocument');
      assert.equal(docs.size, 1);
      assert.equal(docs.get('doc-1').title, 'First Doc');

      removeDocument('doc-1');
      assert.equal(docs, initialDocsRef, 'docs identity must remain unchanged after removeDocument');
      assert.equal(docs.size, 0);

      registerDocument({ id: 'doc-2', title: 'Second Doc' });
      clearDocuments();
      assert.equal(docs, initialDocsRef, 'docs identity must remain unchanged after clearDocuments');
      assert.equal(docs.size, 0);
    });

    it('ignores documents without an id on registration', () => {
      registerDocument(null);
      registerDocument(undefined);
      registerDocument({});
      registerDocument({ title: 'No Id' });
      assert.equal(docs.size, 0);
    });

    it('exposes collections with expected types', () => {
      assert.ok(_deletedDocIds instanceof Set, '_deletedDocIds must be a Set');
      assert.ok(_inFlightDeletes instanceof Map, '_inFlightDeletes must be a Map');
      assert.ok(_closingDocs instanceof Map, '_closingDocs must be a Map');
      assert.ok(docs instanceof Map, 'docs must be a Map');
    });
  });

  describe('Live Binding Exports and State Transitions', () => {
    it('updates API_BASE live binding via setApiBase', () => {
      assert.equal(API_BASE, '');
      setApiBase('/api/v2');
      assert.equal(API_BASE, '/api/v2');
      setApiBase('');
      assert.equal(API_BASE, '');
    });

    it('updates activeDocId live binding via setActiveDocId', () => {
      assert.equal(activeDocId, null);
      setActiveDocId('target-doc-id');
      assert.equal(activeDocId, 'target-doc-id');
      setActiveDocId(null);
      assert.equal(activeDocId, null);
    });

    it('updates _lastSessionId live binding via setLastSessionId', () => {
      assert.equal(_lastSessionId, '');
      setLastSessionId('session-xyz');
      assert.equal(_lastSessionId, 'session-xyz');
      setLastSessionId('');
      assert.equal(_lastSessionId, '');
    });

    it('updates isOpen and isPanelOpen via setPanelOpen with boolean coercion', () => {
      assert.equal(isOpen, false);
      assert.equal(isPanelOpen(), false);

      setPanelOpen(true);
      assert.equal(isOpen, true);
      assert.equal(isPanelOpen(), true);

      setPanelOpen(0);
      assert.equal(isOpen, false);
      assert.equal(isPanelOpen(), false);

      setPanelOpen('non-empty');
      assert.equal(isOpen, true);
      assert.equal(isPanelOpen(), true);

      setPanelOpen(false);
      assert.equal(isOpen, false);
      assert.equal(isPanelOpen(), false);
    });

    it('updates _minimizedDocId live binding via setMinimizedDocId', () => {
      assert.equal(_minimizedDocId, null);
      setMinimizedDocId('minimized-doc-1');
      assert.equal(_minimizedDocId, 'minimized-doc-1');
      setMinimizedDocId(null);
      assert.equal(_minimizedDocId, null);
    });

    it('increments _documentNavigationGeneration monotonically via bumpNavigationGeneration', () => {
      const initialGen = _documentNavigationGeneration;
      const gen1 = bumpNavigationGeneration();
      assert.equal(gen1, initialGen + 1);
      assert.equal(_documentNavigationGeneration, gen1);

      const gen2 = bumpNavigationGeneration();
      assert.equal(gen2, gen1 + 1);
      assert.equal(_documentNavigationGeneration, gen2);
    });
  });

  describe('Delete Tracking and In-Flight Delete Transitions', () => {
    it('tracks deleted ids without coupling to document map removal', () => {
      const doc = { id: 'doc-del-1', title: 'To Delete' };
      registerDocument(doc);

      // markDeleted does not remove from docs
      markDeleted('doc-del-1');
      assert.ok(_deletedDocIds.has('doc-del-1'));
      assert.ok(docs.has('doc-del-1'), 'markDeleted must not remove document from docs');

      // removeDocument does not mark as deleted
      registerDocument({ id: 'doc-del-2', title: 'Session Switch' });
      removeDocument('doc-del-2');
      assert.ok(!docs.has('doc-del-2'));
      assert.ok(!_deletedDocIds.has('doc-del-2'), 'removeDocument must not mark document deleted');
    });

    it('tracks in-flight delete lifecycle via beginDelete and endDelete', () => {
      const dummyTask = { promise: Promise.resolve() };
      beginDelete('doc-flight-1', dummyTask);

      assert.ok(_inFlightDeletes.has('doc-flight-1'));
      assert.equal(_inFlightDeletes.get('doc-flight-1'), dummyTask);

      endDelete('doc-flight-1');
      assert.ok(!_inFlightDeletes.has('doc-flight-1'));
    });
  });

  describe('Draft Retention Transitions', () => {
    it('retains draft object identity in _closingDocs until releaseClosingDraft', () => {
      const draftObj = { id: 'draft-1', content: 'unsaved text', _saveState: 'saving' };

      retainClosingDraft('draft-1', draftObj);
      assert.ok(_closingDocs.has('draft-1'));
      assert.equal(_closingDocs.get('draft-1'), draftObj, 'retained draft must preserve exact object identity');

      releaseClosingDraft('draft-1', draftObj);
      assert.ok(!_closingDocs.has('draft-1'));
    });
  });
});

describe('Document Command Port Contract', () => {
  beforeEach(() => {
    _resetDocumentCommandsForTesting();
  });

  const ALL_12_COMMANDS = [
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

  function createValidCommandImplementations(recordedCalls = []) {
    const impls = {};
    for (const name of ALL_12_COMMANDS) {
      impls[name] = (...args) => {
        recordedCalls.push({ name, args });
        return `${name}:result`;
      };
    }
    return impls;
  }

  it('throws when commands are called before binding', () => {
    assert.throws(() => openPanel(), /Document commands not bound/);
    assert.throws(() => closePanel(), /Document commands not bound/);
    assert.throws(() => switchToDoc('d1'), /Document commands not bound/);
    assert.throws(() => saveCurrentToMap(), /Document commands not bound/);
    assert.throws(() => renderTabs(), /Document commands not bound/);
    assert.throws(() => _syncDocIndicator(), /Document commands not bound/);
    assert.throws(() => _syncHeaderActions(), /Document commands not bound/);
    assert.throws(() => showEmptyState(), /Document commands not bound/);
    assert.throws(() => _showLoadingOverlay(), /Document commands not bound/);
    assert.throws(() => _hideLoadingOverlay(), /Document commands not bound/);
    assert.throws(() => _minimizeNotesForDocumentOpen(), /Document commands not bound/);
    assert.throws(() => _ensureDocChipRegistered(), /Document commands not bound/);
  });

  it('rejects binding when command object is invalid or missing methods', () => {
    assert.throws(() => bindDocumentCommands(null), /Missing document commands object/);
    assert.throws(() => bindDocumentCommands(undefined), /Missing document commands object/);
    assert.throws(() => bindDocumentCommands('not-an-object'), /Missing document commands object/);

    // Missing some methods
    assert.throws(() => bindDocumentCommands({}), /Missing document command implementation: openPanel/);
    assert.throws(
      () => bindDocumentCommands({ openPanel: () => {} }),
      /Missing document command implementation: closePanel/
    );

    // Test missing every single one individually
    for (const missingName of ALL_12_COMMANDS) {
      _resetDocumentCommandsForTesting();
      const partial = createValidCommandImplementations();
      delete partial[missingName];
      assert.throws(
        () => bindDocumentCommands(partial),
        new RegExp(`Missing document command implementation: ${missingName}`)
      );
    }
  });

  it('binds successfully with all 12 methods and rejects re-binding', () => {
    const impls = createValidCommandImplementations();
    bindDocumentCommands(impls);

    assert.throws(
      () => bindDocumentCommands(impls),
      /Document commands already bound/
    );
  });

  it('forwards all 12 command calls with arguments and returns their results', () => {
    const recordedCalls = [];
    const impls = createValidCommandImplementations(recordedCalls);
    bindDocumentCommands(impls);

    assert.equal(openPanel({ force: true }), 'openPanel:result');
    assert.equal(closePanel(), 'closePanel:result');
    assert.equal(switchToDoc('doc-10', { line: 5 }), 'switchToDoc:result');
    assert.equal(saveCurrentToMap(), 'saveCurrentToMap:result');
    assert.equal(renderTabs(), 'renderTabs:result');
    assert.equal(_syncDocIndicator(), '_syncDocIndicator:result');
    assert.equal(_syncHeaderActions(), '_syncHeaderActions:result');
    assert.equal(showEmptyState(), 'showEmptyState:result');
    assert.equal(_showLoadingOverlay('Loading...'), '_showLoadingOverlay:result');
    assert.equal(_hideLoadingOverlay(), '_hideLoadingOverlay:result');
    assert.equal(_minimizeNotesForDocumentOpen(), '_minimizeNotesForDocumentOpen:result');
    assert.equal(_ensureDocChipRegistered('chip-id'), '_ensureDocChipRegistered:result');

    assert.equal(recordedCalls.length, 12);
    assert.deepEqual(recordedCalls[0], { name: 'openPanel', args: [{ force: true }] });
    assert.deepEqual(recordedCalls[1], { name: 'closePanel', args: [] });
    assert.deepEqual(recordedCalls[2], { name: 'switchToDoc', args: ['doc-10', { line: 5 }] });
    assert.deepEqual(recordedCalls[3], { name: 'saveCurrentToMap', args: [] });
    assert.deepEqual(recordedCalls[4], { name: 'renderTabs', args: [] });
    assert.deepEqual(recordedCalls[5], { name: '_syncDocIndicator', args: [] });
    assert.deepEqual(recordedCalls[6], { name: '_syncHeaderActions', args: [] });
    assert.deepEqual(recordedCalls[7], { name: 'showEmptyState', args: [] });
    assert.deepEqual(recordedCalls[8], { name: '_showLoadingOverlay', args: ['Loading...'] });
    assert.deepEqual(recordedCalls[9], { name: '_hideLoadingOverlay', args: [] });
    assert.deepEqual(recordedCalls[10], { name: '_minimizeNotesForDocumentOpen', args: [] });
    assert.deepEqual(recordedCalls[11], { name: '_ensureDocChipRegistered', args: ['chip-id'] });
  });
});
