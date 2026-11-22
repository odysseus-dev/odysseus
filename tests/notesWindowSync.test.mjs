import test from 'node:test';
import assert from 'node:assert/strict';
import { createNoteWindow } from '../frontend/src/features/notes/ui/noteWindow.js';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createNoteSession } from '../frontend/src/features/notes/application/noteSession.js';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';

function setupWindowTest({ note = { id: 'note-sync-1', title: 'Sync Note', content: '- [ ] item 1', body_revision: 'r1' }, apiOverrides = {} } = {}) {
  const listeners = {};
  const attrs = {};
  const classList = new Set();
  function makeEl(tag = 'div') {
    const elChildren = [];
    const el = {
      tagName: tag.toUpperCase(),
      id: '',
      className: '',
      dataset: {},
      value: '',
      textContent: '',
      innerHTML: '',
      children: elChildren,
      _children: elChildren,
      style: { setProperty: () => {} },
      classList: {
        add: (...c) => c.forEach(x => classList.add(x)),
        remove: (...c) => c.forEach(x => classList.delete(x)),
        toggle: (x, force) => ((force ?? !classList.has(x)) ? classList.add(x) : classList.delete(x)),
        contains: x => classList.has(x),
      },
      setAttribute: (k, v) => { attrs[k] = String(v); },
      getAttribute: k => attrs[k] ?? null,
      appendChild: c => { elChildren.push(c); return c; },
      replaceChildren: (...c) => { elChildren.length = 0; elChildren.push(...c); },
      remove: () => {},
      contains: other => {
        if (other === el) return true;
        const queue = [...elChildren];
        const seen = new Set([el]);
        while (queue.length > 0) {
          const item = queue.shift();
          if (item === other) return true;
          if (item && typeof item === 'object' && !seen.has(item)) {
            seen.add(item);
            if (Array.isArray(item._children)) queue.push(...item._children);
            if (item._subs) Object.values(item._subs).forEach(sub => queue.push(sub));
          }
        }
        return false;
      },
      addEventListener: (e, f) => { (listeners[e] = listeners[e] || []).push(f); },
      querySelector: sel => {
        if (!el._subs) el._subs = {};
        if (!el._subs[sel]) el._subs[sel] = makeEl(sel.includes('title') ? 'input' : (sel.includes('btn') ? 'button' : 'div'));
        return el._subs[sel];
      },
      querySelectorAll: () => [],
    };
    return el;
  }

  const prevDoc = globalThis.document;
  const prevNode = globalThis.Node;
  const bodyEl = makeEl('body');
  globalThis.document = {
    createElement: tag => makeEl(tag),
    body: bodyEl,
    activeElement: null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  globalThis.Node = { ELEMENT_NODE: 1 };

  const app = createNotesState();
  const currentNote = { ...note };
  app.replaceNotes([currentNote]);

  let editorMarkdown = note.content;
  let editorEdited = false;
  const editor = {
    setContent: c => { editorMarkdown = c; editorEdited = false; },
    getMarkdown: () => editorMarkdown,
    taskItems: () => [],
    flipTask: () => {},
    destroy: () => {},
    focusEnd: () => {},
    get edited() { return editorEdited; },
  };

  const richBody = { attach: () => editor };

  const storageMap = new Map();
  const persistence = createNotesPersistence({
    storage: {
      getItem: k => storageMap.get(k) ?? null,
      setItem: (k, v) => storageMap.set(String(k), String(v)),
      removeItem: k => storageMap.delete(String(k)),
      key: i => Array.from(storageMap.keys())[i] ?? null,
      get length() { return storageMap.size; },
    },
  });

  const api = {
    patchNote: async (id, body) => ({ id, ...body, body_revision: 'r-patched' }),
    saveNote: async body => ({ id: 'created-1', ...body, body_revision: 'c1' }),
    getNote: async id => currentNote,
    toggleTask: async (id, index, rev) => ({ ok: true, note: { ...currentNote, body_revision: 'r-toggled' } }),
    ...apiOverrides,
  };

  let sessionInstance = null;
  const createSessionWrapper = opts => {
    sessionInstance = createNoteSession({
      api,
      persistence,
      clock: { now: () => 1000 },
      scheduler: { setTimeout: () => 1, clearTimeout: () => {} },
      ...opts,
    });
    return sessionInstance;
  };

  let minimized = false;
  const Modals = {
    isMinimized: () => minimized,
    setMinimized: val => { minimized = val; },
    register: () => {},
    unregister: () => {},
    injectMinimizeButton: () => {},
    restore: () => { minimized = false; },
  };

  const win = createNoteWindow({
    app,
    state: app.state,
    Modals,
    makeWindowDraggable: () => {},
    bindMenuDismiss: () => {},
    dismissOrRemove: () => {},
    richBody,
    noteTags: { mount: () => ({ destroy: () => {}, set: () => {} }) },
    createSession: createSessionWrapper,
    wireReminderPicker: () => ({ refresh: () => {} }),
    drawing: { _buildDrawHtml: () => '', _wireCanvas: () => {} },
    colors: { COLORS: [], COLOR_HEX: {}, _dotBg: () => '', _dotIsActive: () => false, _isBgImage: () => false },
    esc: s => s,
    attrEsc: s => s,
    isMobile: () => false,
    isLocalVersion: () => false,
    effects: { render: () => {}, showError: () => {} },
  });

  win.open(note.id);

  const modalEl = bodyEl.children[0];
  const contentEl = modalEl?.children?.[0];
  const titleEl = contentEl?.querySelector?.('.note-window-title');

  return {
    win,
    app,
    note: currentNote,
    editor,
    modalEl,
    contentEl,
    titleEl,
    getEditorMarkdown: () => editorMarkdown,
    setEditorMarkdown: (md, markEdited = true) => {
      editorMarkdown = md;
      if (markEdited) editorEdited = true;
    },
    getSession: () => sessionInstance,
    Modals,
    cleanup: () => {
      if (prevDoc) globalThis.document = prevDoc; else delete globalThis.document;
      if (prevNode) globalThis.Node = prevNode; else delete globalThis.Node;
    },
  };
}

test('Finding B 1: successful autosave followed by an external checklist update synchronizes correctly', async () => {
  const h = setupWindowTest({
    note: { id: 'note-1', title: 'Checklist', content: '- [ ] task 1', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async (id, body) => ({ id, ...body, body_revision: 'r2' }),
    },
  });
  try {
    const session = h.getSession();
    assert.equal(h.getEditorMarkdown(), '- [ ] task 1');
    assert.equal(session.base, 'r1');

    // User edits text
    h.setEditorMarkdown('- [ ] task 1 modified');
    session.markDirty();
    assert.equal(session.dirty, true);
    assert.equal(h.editor.edited, true);

    // Autosave succeeds
    await session.flush();
    assert.equal(session.dirty, false);
    assert.equal(session.base, 'r2');
    assert.equal(session.saved.content, '- [ ] task 1 modified');
    // Historical edited flag remains true in richBody
    assert.equal(h.editor.edited, true);

    // External checklist update arrives (e.g. toggled from card)
    h.app.mergeNote(h.note, { body_revision: 'r3', content: '- [x] task 1 modified' });

    // Sync window
    h.win.sync();

    // The window must adopt the new content and revision
    assert.equal(h.getEditorMarkdown(), '- [x] task 1 modified');
    assert.equal(session.base, 'r3');
    assert.equal(session.saved.content, '- [x] task 1 modified');
  } finally {
    h.cleanup();
  }
});

test('Finding B 2: clean editor synchronization without prior edits', () => {
  const h = setupWindowTest({
    note: { id: 'note-2', title: 'Clean Note', content: 'Original content', body_revision: 'r1' },
  });
  try {
    const session = h.getSession();
    assert.equal(session.dirty, false);
    assert.equal(h.editor.edited, false);

    // External update arrives
    h.app.mergeNote(h.note, { body_revision: 'r2', content: 'Updated remotely' });
    h.win.sync();

    assert.equal(h.getEditorMarkdown(), 'Updated remotely');
    assert.equal(session.base, 'r2');
  } finally {
    h.cleanup();
  }
});

test('Finding B 3: dirty editor revision isolation protects uncommitted edits', async () => {
  let patchSentBody = null;
  const h = setupWindowTest({
    note: { id: 'note-3', title: 'Dirty Note', content: 'Base content', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async (id, body) => {
        patchSentBody = body;
        if (body.body_revision === 'r1') {
          const err = new Error('conflict');
          err.status = 409;
          throw err;
        }
        return { id, ...body, body_revision: 'r3' };
      },
    },
  });
  try {
    const session = h.getSession();

    // User types local changes
    h.setEditorMarkdown('Local dirty typing');
    session.markDirty();
    assert.equal(session.dirty, true);

    // Remote update arrives
    h.app.mergeNote(h.note, { body_revision: 'r2', content: 'Remote content' });

    // Window sync must not overwrite dirty edits or advance base revision
    h.win.sync();
    assert.equal(h.getEditorMarkdown(), 'Local dirty typing');
    assert.equal(session.base, 'r1');

    // When save runs, it sends base r1 and receives 409 conflict
    await session.flush();
    assert.equal(patchSentBody.body_revision, 'r1');
    assert.ok(session.conflict, 'session entered conflict state preserving local edits');
    assert.equal(session.conflict.local.content, 'Local dirty typing');
  } finally {
    h.cleanup();
  }
});

test('Finding B 4: synchronization during a pending save is deferred', async () => {
  let finishSave;
  const savePromise = new Promise(resolve => { finishSave = resolve; });

  const h = setupWindowTest({
    note: { id: 'note-4', title: 'In-Flight Note', content: 'Content', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async (id, body) => {
        await savePromise;
        return { id, ...body, body_revision: 'r2' };
      },
    },
  });
  try {
    const session = h.getSession();

    h.setEditorMarkdown('Typing in flight');
    session.markDirty();

    // Trigger save (now in-flight)
    const flushing = session.flush();
    assert.equal(session.saving, true);

    // External change arrives while save is in flight
    h.app.mergeNote(h.note, { body_revision: 'r-external', content: 'External update' });
    h.win.sync();

    // Must not sync during in-flight save
    assert.equal(h.getEditorMarkdown(), 'Typing in flight');
    assert.equal(session.base, 'r1');

    // Complete the in-flight save
    finishSave();
    await flushing;
    assert.equal(session.saving, false);
    assert.equal(session.base, 'r2');
  } finally {
    h.cleanup();
  }
});

test('Finding B 5: failed save followed by newer server revision preserves local edits', async () => {
  const h = setupWindowTest({
    note: { id: 'note-5', title: 'Failed Save Note', content: 'Old content', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async () => { throw new Error('network down'); },
    },
  });
  try {
    const session = h.getSession();

    h.setEditorMarkdown('Offline edits');
    session.markDirty();
    await session.flush();

    assert.equal(session.dirty, true);

    // External revision arrives
    h.app.mergeNote(h.note, { body_revision: 'r2', content: 'Server newer content' });
    h.win.sync();

    // Local edits must remain intact, base revision must not advance
    assert.equal(h.getEditorMarkdown(), 'Offline edits');
    assert.equal(session.base, 'r1');
  } finally {
    h.cleanup();
  }
});

test('Finding B 6: conflict resolution reload followed by subsequent synchronization', async () => {
  const serverVersion = { id: 'note-6', title: 'Conflict Note', content: 'Server resolution content', body_revision: 'r2' };
  const h = setupWindowTest({
    note: { id: 'note-6', title: 'Conflict Note', content: 'Initial content', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async () => {
        const err = new Error('conflict');
        err.status = 409;
        throw err;
      },
      getNote: async () => serverVersion,
    },
  });
  try {
    const session = h.getSession();

    h.setEditorMarkdown('Conflicting typing');
    session.markDirty();
    await session.flush();

    assert.ok(session.conflict);

    // While conflict is active, external sync is ignored
    h.app.mergeNote(h.note, { body_revision: 'r3', content: 'Another update' });
    h.win.sync();
    assert.equal(h.getEditorMarkdown(), 'Conflicting typing');

    // Resolve conflict with reload
    await session.resolveConflict('reload');
    assert.equal(session.conflict, null);
    assert.equal(session.base, 'r2');
    assert.equal(session.saved.content, 'Server resolution content');

    // Set editor to match reloaded server content
    h.setEditorMarkdown('Server resolution content', false);

    // Now a newer external update arrives
    h.app.mergeNote(h.note, { body_revision: 'r4', content: 'Post-conflict newer update' });
    h.win.sync();

    // Window must sync cleanly
    assert.equal(h.getEditorMarkdown(), 'Post-conflict newer update');
    assert.equal(session.base, 'r4');
  } finally {
    h.cleanup();
  }
});

test('Browser Scenario A: Focused clean editor defers synchronization while interacting and reconciles on blur', async () => {
  const h = setupWindowTest({
    note: { id: 'note-scen-a', title: 'Checklist A', content: '- [ ] item 1', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async (id, body) => ({ id, ...body, body_revision: 'r2' }),
    },
  });
  try {
    const session = h.getSession();
    // 1-4. Edit and autosave
    h.setEditorMarkdown('- [ ] item 1 edited');
    session.markDirty();
    await session.flush();
    assert.equal(session.dirty, false);
    assert.equal(session.base, 'r2');

    // 5. Keep focus inside editor
    globalThis.document.activeElement = h.titleEl;

    // 6. Checklist update from card arrives
    h.app.mergeNote(h.note, { body_revision: 'r3', content: '- [x] item 1 edited' });

    // 7. Verify sync is deferred while focused
    h.win.sync();
    assert.equal(h.getEditorMarkdown(), '- [ ] item 1 edited');
    assert.equal(session.base, 'r2');

    // 8. Blur the editor
    globalThis.document.activeElement = null;
    h.win.sync();

    // 9. Verify update is incorporated
    assert.equal(h.getEditorMarkdown(), '- [x] item 1 edited');
    assert.equal(session.base, 'r3');
  } finally {
    h.cleanup();
  }
});

test('Browser Scenario B: Minimized window synchronizes when restored', async () => {
  const h = setupWindowTest({
    note: { id: 'note-scen-b', title: 'Minimizable', content: '- [ ] task 1', body_revision: 'r1' },
  });
  try {
    const session = h.getSession();
    // 1-2. Note is clean
    assert.equal(session.dirty, false);

    // 3. Minimize window
    h.Modals.setMinimized(true);

    // 4. Toggle checklist item from card
    h.app.mergeNote(h.note, { body_revision: 'r2', content: '- [x] task 1' });

    // 5. Restore window (restore triggers sync)
    h.Modals.restore();
    h.win.sync();

    // 6. Verify updated checklist displayed
    assert.equal(h.getEditorMarkdown(), '- [x] task 1');
    assert.equal(session.base, 'r2');
  } finally {
    h.cleanup();
  }
});

test('Browser Scenario C: In-flight save is protected and completes cleanly without losing local edits', async () => {
  let resolveSave;
  const inFlightPromise = new Promise(res => { resolveSave = res; });
  const h = setupWindowTest({
    note: { id: 'note-scen-c', title: 'In-Flight Note', content: 'Base content', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async (id, body) => {
        await inFlightPromise;
        return { id, ...body, body_revision: 'r2' };
      },
    },
  });
  try {
    const session = h.getSession();
    h.setEditorMarkdown('User typed edits');
    session.markDirty();

    // Trigger save (now in-flight)
    const savePromise = session.flush();
    assert.equal(session.saving, true);

    // Remote card update arrives during in-flight save
    h.app.mergeNote(h.note, { body_revision: 'r-card', content: 'Remote card edit' });
    h.win.sync();

    // Must not overwrite local edits during in-flight save
    assert.equal(h.getEditorMarkdown(), 'User typed edits');
    assert.equal(session.base, 'r1');

    // Complete in-flight save
    resolveSave();
    await savePromise;
    assert.equal(session.saving, false);
    assert.equal(session.base, 'r2');
    assert.equal(session.saved.content, 'User typed edits');
  } finally {
    h.cleanup();
  }
});

test('Browser Scenario D: Genuine concurrent edit triggers HTTP 409 conflict and preserves local edits', async () => {
  const serverVersion = { id: 'note-scen-d', title: 'Server Note', content: 'Server edit', body_revision: 'r2' };
  let patchCalls = 0;
  let keepMineBody = null;
  const h = setupWindowTest({
    note: { id: 'note-scen-d', title: 'Conflict Note', content: 'Initial content', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async (id, body) => {
        patchCalls++;
        if (patchCalls === 1) {
          const err = new Error('conflict');
          err.status = 409;
          throw err;
        }
        keepMineBody = body;
        return { id, ...body, body_revision: 'r3' };
      },
      getNote: async () => serverVersion,
    },
  });
  try {
    const session = h.getSession();
    h.setEditorMarkdown('Local conflict edit');
    session.markDirty();
    await session.flush();

    // 409 entered conflict
    assert.ok(session.conflict);
    assert.equal(session.conflict.local.content, 'Local conflict edit');

    // Resolving conflict with Keep mine writes local text over server revision
    await session.resolveConflict('keep');
    assert.equal(session.conflict, null);
    assert.equal(session.base, 'r3');
    assert.equal(session.saved.content, 'Local conflict edit');
    assert.equal(keepMineBody?.body_revision, 'r2');
    assert.equal(keepMineBody?.content, 'Local conflict edit');
    assert.equal(keepMineBody?.replace_content, true);
  } finally {
    h.cleanup();
  }
});

test('Browser Scenario E: Failed save followed by external revision preserves local edits', async () => {
  const h = setupWindowTest({
    note: { id: 'note-scen-e', title: 'Offline Note', content: 'Initial', body_revision: 'r1' },
    apiOverrides: {
      patchNote: async () => { throw new Error('network down'); },
    },
  });
  try {
    const session = h.getSession();
    h.setEditorMarkdown('Local offline work');
    session.markDirty();
    await session.flush();

    assert.equal(session.dirty, true);

    // External revision arrives
    h.app.mergeNote(h.note, { body_revision: 'r2', content: 'Server updated content' });
    h.win.sync();

    // Local content must remain intact
    assert.equal(h.getEditorMarkdown(), 'Local offline work');
    assert.equal(session.base, 'r1');
  } finally {
    h.cleanup();
  }
});

test('Browser Scenario F: Focus on toolbar or modal elements defers sync and reconciles when focus leaves modal', () => {
  const h = setupWindowTest({
    note: { id: 'note-scen-f', title: 'Toolbar Focus', content: '- [ ] item 1', body_revision: 'r1' },
  });
  try {
    const session = h.getSession();
    // Simulate active element inside modal (toolbar button)
    const toolbarButton = h.contentEl.querySelector('.note-window-toolbar');
    globalThis.document.activeElement = toolbarButton;

    // External update arrives
    h.app.mergeNote(h.note, { body_revision: 'r2', content: '- [x] item 1' });

    // While interacting with formatting controls, sync is deferred
    h.win.sync();
    assert.equal(h.getEditorMarkdown(), '- [ ] item 1');
    assert.equal(session.base, 'r1');

    // When focus leaves modal (activeElement is outside)
    globalThis.document.activeElement = null;
    h.win.sync();

    // Reconciled cleanly
    assert.equal(h.getEditorMarkdown(), '- [x] item 1');
    assert.equal(session.base, 'r2');
  } finally {
    h.cleanup();
  }
});
