// tests/documentSession.test.mjs
import test, { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// Set up mock localStorage globally before importing application modules
const storage = new Map();
globalThis.localStorage = {
  getItem(key) {
    const k = String(key);
    return storage.has(k) ? storage.get(k) : null;
  },
  setItem(key, value) {
    storage.set(String(key), String(value));
  },
  removeItem(key) {
    storage.delete(String(key));
  },
  clear() {
    storage.clear();
  },
  get length() {
    return storage.size;
  },
  key(index) {
    return Array.from(storage.keys())[index] ?? null;
  }
};

import {
  docs,
  _closingDocs,
  clearDocuments,
  registerDocument,
  retainClosingDraft
} from '../frontend/src/features/documents/application/documentState.js';

import {
  _docOpenKey,
  _docMinimizedKey,
  _docActiveKey,
  _forgetActiveDoc,
  _markDocVisibleState,
  addDocToTabs
} from '../frontend/src/features/documents/application/documentSession.js';

describe('Document Session Module', () => {
  beforeEach(() => {
    globalThis.localStorage.clear();
    clearDocuments();
    _closingDocs.clear();
  });

  describe('Storage Keys', () => {
    it('generates expected storage key strings', () => {
      assert.equal(_docOpenKey('s1'), 'odysseus-doc-open-s1');
      assert.equal(_docMinimizedKey('s1'), 'odysseus-doc-minimized-s1');
      assert.equal(_docActiveKey('s1'), 'odysseus-doc-active-s1');
    });

    it('formats storage keys for various session IDs', () => {
      const sessionIds = ['session-123', 'uuid-abc-def', '12345', 'default'];
      for (const sid of sessionIds) {
        assert.equal(_docOpenKey(sid), `odysseus-doc-open-${sid}`);
        assert.equal(_docMinimizedKey(sid), `odysseus-doc-minimized-${sid}`);
        assert.equal(_docActiveKey(sid), `odysseus-doc-active-${sid}`);
      }
    });
  });

  describe('Storage Visibility Lifecycle (_markDocVisibleState)', () => {
    it('marks session as open by setting open key to 1 and removing minimized key', () => {
      const sessionId = 'session-open-test';
      globalThis.localStorage.setItem(_docMinimizedKey(sessionId), '1');

      _markDocVisibleState(sessionId, 'open');

      assert.equal(globalThis.localStorage.getItem(_docOpenKey(sessionId)), '1');
      assert.equal(globalThis.localStorage.getItem(_docMinimizedKey(sessionId)), null);
    });

    it('marks session as minimized by setting minimized key to 1 and removing open key', () => {
      const sessionId = 'session-min-test';
      globalThis.localStorage.setItem(_docOpenKey(sessionId), '1');

      _markDocVisibleState(sessionId, 'minimized');

      assert.equal(globalThis.localStorage.getItem(_docMinimizedKey(sessionId)), '1');
      assert.equal(globalThis.localStorage.getItem(_docOpenKey(sessionId)), null);
    });

    it('marks session as closed by removing both open and minimized keys', () => {
      const sessionId = 'session-close-test';
      globalThis.localStorage.setItem(_docOpenKey(sessionId), '1');
      globalThis.localStorage.setItem(_docMinimizedKey(sessionId), '1');

      _markDocVisibleState(sessionId, 'closed');

      assert.equal(globalThis.localStorage.getItem(_docOpenKey(sessionId)), null);
      assert.equal(globalThis.localStorage.getItem(_docMinimizedKey(sessionId)), null);
    });

    it('removes both keys when state is any other non-open non-minimized value', () => {
      const sessionId = 'session-other-test';
      globalThis.localStorage.setItem(_docOpenKey(sessionId), '1');
      globalThis.localStorage.setItem(_docMinimizedKey(sessionId), '1');

      _markDocVisibleState(sessionId, 'hidden');

      assert.equal(globalThis.localStorage.getItem(_docOpenKey(sessionId)), null);
      assert.equal(globalThis.localStorage.getItem(_docMinimizedKey(sessionId)), null);
    });

    it('safely ignores missing or empty sessionId without throwing or mutating storage', () => {
      const initialLength = globalThis.localStorage.length;

      _markDocVisibleState(null, 'open');
      _markDocVisibleState(undefined, 'open');
      _markDocVisibleState('', 'open');
      _markDocVisibleState(null, 'minimized');
      _markDocVisibleState('', 'closed');

      assert.equal(globalThis.localStorage.length, initialLength);
    });
  });

  describe('Active Document Forgetting (_forgetActiveDoc)', () => {
    it('removes active doc key if stored value matches docId', () => {
      const sessionId = 'sess-forget-1';
      const docId = 'doc-to-forget';
      globalThis.localStorage.setItem(_docActiveKey(sessionId), docId);

      _forgetActiveDoc(sessionId, docId);

      assert.equal(globalThis.localStorage.getItem(_docActiveKey(sessionId)), null);
    });

    it('does nothing if stored value differs from docId', () => {
      const sessionId = 'sess-forget-2';
      const storedDocId = 'doc-active';
      const differentDocId = 'doc-other';
      globalThis.localStorage.setItem(_docActiveKey(sessionId), storedDocId);

      _forgetActiveDoc(sessionId, differentDocId);

      assert.equal(globalThis.localStorage.getItem(_docActiveKey(sessionId)), storedDocId);
    });

    it('does nothing if key does not exist in storage', () => {
      const sessionId = 'sess-forget-3';
      _forgetActiveDoc(sessionId, 'non-existent-doc');
      assert.equal(globalThis.localStorage.getItem(_docActiveKey(sessionId)), null);
    });

    it('safely ignores missing or empty sessionId', () => {
      globalThis.localStorage.setItem('odysseus-doc-active-', 'doc-dummy');

      _forgetActiveDoc('', 'doc-dummy');
      _forgetActiveDoc(null, 'doc-dummy');
      _forgetActiveDoc(undefined, 'doc-dummy');

      assert.equal(globalThis.localStorage.getItem('odysseus-doc-active-'), 'doc-dummy');
    });
  });

  describe('Document Tab Adoption (addDocToTabs)', () => {
    describe('Draft Adoption (dirty, saving, error)', () => {
      for (const saveState of ['dirty', 'saving', 'error']) {
        it(`preserves existing object identity and content when _saveState is "${saveState}" in docs`, () => {
          const docId = `draft-${saveState}-doc`;
          const existingDraft = {
            id: docId,
            title: 'Local Modified Draft',
            content: 'Unsaved draft text',
            version: 1,
            sessionId: 'sess-draft',
            _saveState: saveState,
            _composeAtts: [{ name: 'file.txt' }]
          };
          registerDocument(existingDraft);

          const serverRow = {
            id: docId,
            title: 'Stale Server Title',
            current_content: 'Stale server content',
            version_count: 5,
            session_id: 'sess-server'
          };

          addDocToTabs(serverRow, 'sess-server');

          const registered = docs.get(docId);
          assert.equal(registered, existingDraft, 'Must retain exact object identity');
          assert.equal(registered.title, 'Local Modified Draft', 'Must not overwrite title with server row');
          assert.equal(registered.content, 'Unsaved draft text', 'Must not overwrite content with server row');
          assert.equal(registered.version, 1, 'Must not overwrite version with server row');
          assert.deepEqual(registered._composeAtts, [{ name: 'file.txt' }]);
        });

        it(`re-registers and releases closing draft when _saveState is "${saveState}" in _closingDocs`, () => {
          const docId = `closing-${saveState}-doc`;
          const closingDraft = {
            id: docId,
            title: 'Closing Modified Draft',
            content: 'Draft being closed',
            version: 2,
            sessionId: 'sess-closing',
            _saveState: saveState,
            _composeAtts: [{ name: 'att.pdf' }]
          };
          retainClosingDraft(docId, closingDraft);
          assert.equal(_closingDocs.has(docId), true);

          const serverRow = {
            id: docId,
            title: 'Server Overwrite Attempt',
            content: 'Server content',
            version_count: 9
          };

          addDocToTabs(serverRow, 'sess-new');

          assert.equal(_closingDocs.has(docId), false, 'Must be released from _closingDocs');
          const registered = docs.get(docId);
          assert.equal(registered, closingDraft, 'Must retain exact object identity in docs');
          assert.equal(registered.title, 'Closing Modified Draft');
          assert.equal(registered.content, 'Draft being closed');
          assert.equal(registered.version, 2);
          assert.deepEqual(registered._composeAtts, [{ name: 'att.pdf' }]);
        });
      }
    });

    describe('Clean or New Document Adoption', () => {
      it('registers new document into docs map with all expected fields', () => {
        const serverDoc = {
          id: 'new-doc-1',
          title: 'Clean New Doc',
          language: 'markdown',
          current_content: 'Brand new markdown content',
          version_count: 3,
          source_email_uid: 'uid-100',
          source_email_folder: 'INBOX',
          source_email_account_id: 'acc-200',
          source_email_message_id: 'msg-300',
          _skipLocalDraftOnce: true
        };

        addDocToTabs(serverDoc, 'session-tab-1');

        assert.equal(docs.has('new-doc-1'), true);
        const adopted = docs.get('new-doc-1');
        assert.equal(adopted.id, 'new-doc-1');
        assert.equal(adopted.title, 'Clean New Doc');
        assert.equal(adopted.language, 'markdown');
        assert.equal(adopted.content, 'Brand new markdown content');
        assert.equal(adopted.version, 3);
        assert.equal(adopted.sessionId, 'session-tab-1');
        assert.equal(adopted.userSetLanguage, true);
        assert.equal(adopted._skipLocalDraftOnce, true);
        assert.equal(adopted._composeAtts, undefined);
        assert.equal(adopted.sourceEmailUid, 'uid-100');
        assert.equal(adopted.sourceEmailFolder, 'INBOX');
        assert.equal(adopted.sourceEmailAccountId, 'acc-200');
        assert.equal(adopted.sourceEmailMessageId, 'msg-300');
      });

      it('updates existing clean document retaining object identity and preserving _composeAtts', () => {
        const docId = 'clean-existing-doc';
        const existingObj = {
          id: docId,
          title: 'Old Title',
          content: 'Old Content',
          _saveState: 'saved',
          _composeAtts: [{ name: 'attachment.png', size: 1024 }]
        };
        registerDocument(existingObj);

        const serverDoc = {
          id: docId,
          title: 'Updated Server Title',
          language: 'python',
          current_content: 'print("hello world")',
          version_count: 4,
          session_id: 'sess-fallback'
        };

        addDocToTabs(serverDoc, 'sess-primary');

        const adopted = docs.get(docId);
        assert.equal(adopted, existingObj, 'Must mutate and retain existing object identity');
        assert.equal(adopted.title, 'Updated Server Title');
        assert.equal(adopted.language, 'python');
        assert.equal(adopted.content, 'print("hello world")');
        assert.equal(adopted.version, 4);
        assert.equal(adopted.sessionId, 'sess-primary');
        assert.equal(adopted.userSetLanguage, true);
        assert.deepEqual(adopted._composeAtts, [{ name: 'attachment.png', size: 1024 }], 'Preserves _composeAtts');
      });

      it('defaults missing or empty attributes gracefully', () => {
        // Fallback: content uses doc.content when current_content is absent
        const docWithContent = {
          id: 'content-fallback-1',
          content: 'fallback content text'
        };
        addDocToTabs(docWithContent);
        const adopted1 = docs.get('content-fallback-1');
        assert.equal(adopted1.content, 'fallback content text');
        assert.equal(adopted1.title, '');
        assert.equal(adopted1.language, '');
        assert.equal(adopted1.version, 1);
        assert.equal(adopted1.sessionId, undefined);
        assert.equal(adopted1.userSetLanguage, false);
        assert.equal(adopted1._skipLocalDraftOnce, false);
        assert.equal(adopted1.sourceEmailUid, null);
        assert.equal(adopted1.sourceEmailFolder, null);
        assert.equal(adopted1.sourceEmailAccountId, null);
        assert.equal(adopted1.sourceEmailMessageId, null);

        // Fallback: content uses '' when both current_content and content are absent
        const docEmpty = {
          id: 'content-empty-doc',
          session_id: 'session-from-row'
        };
        addDocToTabs(docEmpty);
        const adopted2 = docs.get('content-empty-doc');
        assert.equal(adopted2.content, '');
        assert.equal(adopted2.sessionId, 'session-from-row');
        assert.equal(adopted2.version, 1);
      });

      it('prioritizes current_content over content', () => {
        const docBoth = {
          id: 'doc-both-content',
          current_content: 'primary content',
          content: 'secondary content'
        };
        addDocToTabs(docBoth, 's-both');
        assert.equal(docs.get('doc-both-content').content, 'primary content');
      });

      it('prioritizes passed sessionId parameter over doc.session_id', () => {
        const doc = {
          id: 'doc-session-precedence',
          session_id: 'server-session'
        };
        addDocToTabs(doc, 'explicit-session');
        assert.equal(docs.get('doc-session-precedence').sessionId, 'explicit-session');
      });

      it('sets userSetLanguage to false when language is empty or undefined', () => {
        addDocToTabs({ id: 'lang-1', language: '' });
        assert.equal(docs.get('lang-1').userSetLanguage, false);

        addDocToTabs({ id: 'lang-2' });
        assert.equal(docs.get('lang-2').userSetLanguage, false);

        addDocToTabs({ id: 'lang-3', language: 'javascript' });
        assert.equal(docs.get('lang-3').userSetLanguage, true);
      });
    });
  });

  describe('State Ownership and Isolation', () => {
    it('uses the shared docs Map instance from documentState.js', () => {
      const initialSize = docs.size;
      addDocToTabs({ id: 'isolation-test-1', title: 'Isolation Test' }, 'sess-iso');
      assert.equal(docs.size, initialSize + 1);
      assert.ok(docs.has('isolation-test-1'));
    });
  });
});

describe('Markdown draft recovery', () => {
  let draftStorage;
  beforeEach(() => {
    draftStorage = new Map();
    globalThis.sessionStorage = {
      getItem: key => draftStorage.get(key) ?? null,
      setItem: (key, value) => draftStorage.set(key, value),
      removeItem: key => draftStorage.delete(key),
    };
    clearDocuments();
    _closingDocs.clear();
  });

  it('recovers unsaved source after server-authorized adoption and rejects a different session', async () => {
    const { persistMarkdownDraft } = await import('../frontend/src/features/documents/application/documentSession.js');
    addDocToTabs({ id: 'recovery', current_content: 'Server', language: 'markdown' }, 's1');
    const local = docs.get('recovery');
    local.content = 'Local pending';
    assert.equal(persistMarkdownDraft(local), true);
    clearDocuments();
    addDocToTabs({ id: 'recovery', current_content: 'Server', language: 'markdown' }, 's2');
    assert.equal(docs.get('recovery').content, 'Server');
    clearDocuments();
    addDocToTabs({ id: 'recovery', current_content: 'Server', language: 'markdown' }, 's1');
    assert.equal(docs.get('recovery').content, 'Local pending');
    assert.equal(docs.get('recovery')._saveState, 'dirty');
  });

  it('retains diverged source as a blocked conflict instead of overwriting newer server content', async () => {
    const { persistMarkdownDraft } = await import('../frontend/src/features/documents/application/documentSession.js');
    addDocToTabs({ id: 'conflict', current_content: 'Server', language: 'markdown' }, 's1');
    const local = docs.get('conflict');
    local.content = 'Local edit';
    persistMarkdownDraft(local);
    clearDocuments();
    addDocToTabs({ id: 'conflict', current_content: 'Server changed', language: 'markdown' }, 's1');
    const recovered = docs.get('conflict');
    assert.equal(recovered.content, 'Server changed');
    assert.equal(recovered._visualSyncBlocked, true);
    assert.equal(recovered._visualRecoveryMarkdown, 'Local edit');
  });

  it('clears only acknowledged source and retains blocked formatting even at the acknowledged revision', async () => {
    const { persistMarkdownDraft } = await import('../frontend/src/features/documents/application/documentSession.js');
    addDocToTabs({ id: 'ack', current_content: 'Original', language: 'markdown' }, 's1');
    const local = docs.get('ack');
    local.content = 'Newer';
    persistMarkdownDraft(local);
    assert.equal(draftStorage.size, 1);
    local._persistedContent = 'Older write';
    persistMarkdownDraft(local);
    assert.equal(draftStorage.size, 1);
    local._persistedContent = 'Newer';
    local._visualSyncBlocked = true;
    local._visualDraftHtml = '<p>Unrepresentable draft</p>';
    persistMarkdownDraft(local);
    assert.equal(draftStorage.size, 1);
    local._visualSyncBlocked = false;
    persistMarkdownDraft(local);
    assert.equal(draftStorage.size, 0);
  });

  it('warns on unload when draft storage is unavailable without losing the live draft', async () => {
    const { guardMarkdownDraftUnload } = await import('../frontend/src/features/documents/application/documentSession.js');
    addDocToTabs({ id: 'quota', current_content: 'Original', language: 'markdown' }, 's1');
    const local = docs.get('quota');
    local.content = 'Unsaved';
    local._saveState = 'dirty';
    globalThis.sessionStorage.setItem = () => { throw Error('quota'); };
    let warned = false;
    const event = { preventDefault() { warned = true; } };
    guardMarkdownDraftUnload(event);
    assert.equal(warned, true);
    assert.equal(local.content, 'Unsaved');
    assert.equal(local._markdownRecoveryStored, false);
  });
});

it('warns before leaving an unsaved draft even when tab-scoped recovery was stored', async () => {
  const { guardMarkdownDraftUnload } = await import('../frontend/src/features/documents/application/documentSession.js');
  const previousStorage = globalThis.sessionStorage;
  const recovery = new Map();
  clearDocuments();
  _closingDocs.clear();
  try {
    globalThis.sessionStorage = {
      getItem: key => recovery.get(key) ?? null,
      setItem: (key, value) => recovery.set(key, value),
      removeItem: key => recovery.delete(key),
    };
    registerDocument({ id: 'tab-close', sessionId: 's1', language: 'markdown', content: 'Unsaved', _persistedContent: 'Original', _saveState: 'dirty' });
    let warned = false;
    guardMarkdownDraftUnload({ preventDefault() { warned = true; } });
    assert.equal(recovery.size, 1);
    assert.equal(warned, true, 'sessionStorage disappears when the browser tab closes');
  } finally {
    globalThis.sessionStorage = previousStorage;
    clearDocuments();
  }
});

describe('Logout draft boundary', () => {
  let sessionCase = 0;
  async function withSessionStorage(run) {
    const session = await import(`../frontend/src/features/documents/application/documentSession.js?logout-case=${++sessionCase}`);
    const previousStorage = globalThis.sessionStorage;
    const recovery = new Map();
    clearDocuments();
    _closingDocs.clear();
    globalThis.sessionStorage = {
      getItem: key => recovery.get(key) ?? null,
      setItem: (key, value) => recovery.set(key, value),
      removeItem: key => recovery.delete(key),
      clear: () => recovery.clear(),
    };
    try {
      await run(session, recovery);
    } finally {
      globalThis.sessionStorage = previousStorage;
      clearDocuments();
    }
  }
  const dirtyDoc = () => ({ id: 'd1', sessionId: 's1', language: 'markdown', content: 'Private', _persistedContent: 'Original', _saveState: 'dirty' });

  it('keeps ordinary refresh recovery until the account session ends', async () => {
    await withSessionStorage(({ guardMarkdownDraftUnload }, recovery) => {
      registerDocument(dirtyDoc());
      let warned = false;
      guardMarkdownDraftUnload({ preventDefault() { warned = true; } });
      assert.equal(recovery.size, 1);
      assert.equal(warned, true);
    });
  });

  it('does not recreate the outgoing draft from the unload guard, timers or status changes', async () => {
    await withSessionStorage(({ guardMarkdownDraftUnload, persistMarkdownDraft, endMarkdownDraftPersistence }, recovery) => {
      const doc = dirtyDoc();
      registerDocument(doc);
      persistMarkdownDraft(doc);
      assert.equal(recovery.size, 1);

      endMarkdownDraftPersistence();
      recovery.clear(); // what logout does to tab storage
      let warned = false;
      guardMarkdownDraftUnload({ preventDefault() { warned = true; } });
      doc.content = 'More private text';
      assert.equal(persistMarkdownDraft(doc), false);
      assert.equal(recovery.size, 0, 'no stale writer may refill tab storage');
      assert.equal(warned, false, 'logout is not a Leave site? prompt');
    });
  });

  it('does not recover a stored draft into a document adopted after the boundary', async () => {
    await withSessionStorage(({ addDocToTabs: adopt, endMarkdownDraftPersistence }, recovery) => {
      recovery.set('odysseus.document.markdownDraft.v1:s1:d1', JSON.stringify({
        id: 'd1', sessionId: 's1', content: 'Private', baseContent: 'Original', visualBase: 'Original', visualHtml: null,
      }));
      endMarkdownDraftPersistence();
      adopt({ id: 'd1', title: 't', language: 'markdown', current_content: 'Original', version_count: 1 }, 's1');
      assert.equal(docs.get('d1').content, 'Original');
    });
  });

  it('keeps blocked visual drafts recoverable until logout succeeds', async () => {
    await withSessionStorage(({ persistMarkdownDraft, endMarkdownDraftPersistence }, recovery) => {
      const doc = { ...dirtyDoc(), _visualSyncBlocked: true, _visualDraftHtml: '<p>Private visual draft</p>', _visualBaselineMarkdown: 'Original', _saveState: 'blocked' };
      registerDocument(doc);
      assert.equal(persistMarkdownDraft(doc), true);
      assert.equal(recovery.size, 1);
      assert.match([...recovery.values()][0], /Private visual draft/);
      endMarkdownDraftPersistence();
      recovery.clear();
      assert.equal(persistMarkdownDraft(doc), false);
      assert.equal(recovery.size, 0);
    });
  });
});
