// tests/documentStateContract.mjs
import test, { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { parse } from 'acorn';

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

    it('ignores falsy id in markDeleted', () => {
      markDeleted(null);
      markDeleted(undefined);
      markDeleted('');
      assert.equal(_deletedDocIds.size, 0);
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

    it('preserves closing draft when releasing with non-matching document record', () => {
      const draftA = { id: 'draft-concur', content: 'draft A', _saveState: 'saving' };
      const draftB = { id: 'draft-concur', content: 'draft B', _saveState: 'saving' };

      retainClosingDraft('draft-concur', draftA);
      // Attempting to release with draftB should not remove draftA
      const released = releaseClosingDraft('draft-concur', draftB);
      assert.equal(released, false);
      assert.ok(_closingDocs.has('draft-concur'));
      assert.equal(_closingDocs.get('draft-concur'), draftA);

      // Releasing with matching draftA removes it
      const releasedMatching = releaseClosingDraft('draft-concur', draftA);
      assert.equal(releasedMatching, true);
      assert.ok(!_closingDocs.has('draft-concur'));
    });

    it('releases closing draft unconditionally when doc argument is omitted', () => {
      const draft = { id: 'draft-unconditional', content: 'text' };
      retainClosingDraft('draft-unconditional', draft);
      assert.ok(_closingDocs.has('draft-unconditional'));

      const released = releaseClosingDraft('draft-unconditional');
      assert.equal(released, true);
      assert.ok(!_closingDocs.has('draft-unconditional'));
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

  it('supports idempotent activation when idempotent option is passed with same implementations', () => {
    const impls = createValidCommandImplementations();
    bindDocumentCommands(impls);

    // Re-binding the exact same implementation with idempotent: true must not throw
    assert.doesNotThrow(() => {
      bindDocumentCommands(impls, { idempotent: true });
    });

    // Calling duplicate registration without idempotent: true must throw
    assert.throws(
      () => bindDocumentCommands(impls),
      /Document commands already bound/
    );

    // Calling with different implementation even with idempotent: true must throw
    const otherImpls = createValidCommandImplementations();
    assert.throws(
      () => bindDocumentCommands(otherImpls, { idempotent: true }),
      /Document commands already bound/
    );
  });

  it('supports nested command forwarding without unintended reentrancy issues', () => {
    const log = [];
    const impls = createValidCommandImplementations();
    // Have openPanel invoke renderTabs and saveCurrentToMap via the port stubs
    impls.openPanel = () => {
      log.push('start:openPanel');
      renderTabs();
      saveCurrentToMap();
      log.push('end:openPanel');
      return 'nested:ok';
    };
    impls.renderTabs = () => {
      log.push('called:renderTabs');
    };
    impls.saveCurrentToMap = () => {
      log.push('called:saveCurrentToMap');
    };

    bindDocumentCommands(impls);
    const res = openPanel();
    assert.equal(res, 'nested:ok');
    assert.deepEqual(log, [
      'start:openPanel',
      'called:renderTabs',
      'called:saveCurrentToMap',
      'end:openPanel'
    ]);
  });

  it('prevents rebinding during reentrant command execution', () => {
    const impls = createValidCommandImplementations();
    impls.openPanel = () => {
      bindDocumentCommands(impls);
    };
    bindDocumentCommands(impls);
    assert.throws(() => openPanel(), /Document commands already bound/);
  });
});

describe('Legacy Host Bridge Contract (ui/host.js)', () => {
  const hostPath = path.resolve('frontend/src/features/documents/ui/host.js');

  const EXPECTED_LEGACY_SPECIFIERS = [
    '/static/js/ui.js?v=20260916largetoolscroll1',
    '/static/js/sessions.js',
    '/static/js/emojiPicker.js',
    '/static/js/emailReplyStream.js',
    '/static/js/markdown.js',
    '/static/js/codeRunner.js?v=20260831richtexttools91',
    '/static/js/langIcons.js?v=20260831richtexttools91',
    '/static/js/spinner.js',
    '/static/js/documentLibrary.js?v=20260911librarybulkdelete1',
    '/static/js/signature.js',
    '/static/js/modalManager.js',
    '/static/js/escMenuStack.js',
    '/static/js/toolWindowZOrder.js',
    '/static/js/documentStats.js?v=20260831richtexttools91',
    '/static/js/documentOutline.js?v=20260831richtexttools91',
    '/static/js/colorPicker.js?v=20260910eyedropper1'
  ];

  it('re-exports exact 16 legacy module imports with identical query strings', () => {
    const hostSrc = fs.readFileSync(hostPath, 'utf8');
    const hostAst = parse(hostSrc, { ecmaVersion: 'latest', sourceType: 'module' });

    // Collect export sources from ui/host.js
    const hostExportSources = hostAst.body
      .filter(n => ['ExportNamedDeclaration', 'ExportAllDeclaration'].includes(n.type) && n.source)
      .map(n => n.source.value);

    assert.equal(EXPECTED_LEGACY_SPECIFIERS.length, 16, 'must expect exactly 16 legacy imports');

    // Verify each legacy import has a corresponding export in host.js with exact query string
    for (const spec of EXPECTED_LEGACY_SPECIFIERS) {
      assert.ok(
        hostExportSources.includes(spec),
        `ui/host.js must export legacy module with exact specifier: ${spec}`
      );
    }
  });

  it('ensures all target legacy modules exist on disk', () => {
    const hostSrc = fs.readFileSync(hostPath, 'utf8');
    const hostAst = parse(hostSrc, { ecmaVersion: 'latest', sourceType: 'module' });

    for (const n of hostAst.body) {
      if (['ExportNamedDeclaration', 'ExportAllDeclaration'].includes(n.type) && n.source) {
        const spec = n.source.value;
        const pathname = new URL(spec, 'https://odysseus.invalid').pathname;
        const fullDiskPath = path.resolve(pathname.replace(/^\//, ''));
        assert.ok(
          fs.existsSync(fullDiskPath),
          `Target legacy file on disk must exist: ${fullDiskPath}`
        );
      }
    }
  });

  it('verifies that no legacy modules create circular imports with host or document', () => {
    const hostSrc = fs.readFileSync(hostPath, 'utf8');
    const hostAst = parse(hostSrc, { ecmaVersion: 'latest', sourceType: 'module' });

    for (const n of hostAst.body) {
      if (['ExportNamedDeclaration', 'ExportAllDeclaration'].includes(n.type) && n.source) {
        const pathname = new URL(n.source.value, 'https://odysseus.invalid').pathname;
        const fullDiskPath = path.resolve(pathname.replace(/^\//, ''));
        const legacySrc = fs.readFileSync(fullDiskPath, 'utf8');
        const legacyAst = parse(legacySrc, { ecmaVersion: 'latest', sourceType: 'module' });

        for (const item of legacyAst.body) {
          if (item.type === 'ImportDeclaration' && item.source) {
            assert.ok(
              !item.source.value.includes('host') && !item.source.value.includes('document.js'),
              `Legacy module ${pathname} must not import host or document.js`
            );
          }
        }
      }
    }
  });
});
