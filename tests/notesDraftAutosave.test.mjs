import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createNotesDrafts } from '../frontend/src/features/notes/application/drafts.js';
import { createDraftEditor } from '../frontend/src/features/notes/ui/draftEditor.js';

function harness(patch = async () => ({})) {
  const data = new Map(), timers = new Map(), errors = [];
  let nextTimer = 0;
  const context = {
    localStorage: { getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,v), removeItem: k => data.delete(k) },
    setTimeout: f => { timers.set(++nextTimer, f); return nextTimer; },
    clearTimeout: id => timers.delete(id),
    _patchNote: patch, _notes: [{id:'note1'}],
    _collectItems: form => form.items,
    uiModule: {showError: message => errors.push(message)},
  };
  context.persistence = createNotesPersistence({ storage: context.localStorage });
  context.app = createNotesState();
  context.app.replaceNotes(context._notes);
  context.state = context.app.state;
  Object.assign(context, context.persistence);
  const drafts = createNotesDrafts({ app: context.app, api: { patchNote: patch }, persistence: context.persistence, scheduler: context, effects: context.uiModule });
  Object.assign(context, createDraftEditor({ ...context, ...context.persistence, bindDraft: drafts.bindDraft }));
  const fields = {'.note-form-title':{value:'Title'}, '.note-form-content':{value:'Original'}};
  const handlers = {};
  const form = { dataset:{noteType:'note'}, items:[], querySelector: s => fields[s], addEventListener:(n,f) => handlers[n]=f };
  context._wireDraftAutosave(form, 'note1');
  return {context, data, form, fields, errors,
    input: () => handlers.input(),
    drain: async () => { for (const f of timers.values()) f(); timers.clear(); await new Promise(setImmediate); },
    draft: () => JSON.parse(data.get('odysseus-note-draft-note1') || 'null'),
  };
}

test('typing is backed up synchronously, even before the autosave timer', () => {
  const h = harness();
  h.fields['.note-form-content'].value = 'Just typed'; h.input();
  assert.equal(h.draft().content, 'Just typed');
});

test('checklist type without an active pill preserves items', () => {
  const h = harness(); h.form.dataset.noteType='checklist';
  h.form.items=[{text:'Drop keys',done:false}]; h.input();
  assert.equal(h.draft().note_type,'checklist');
  assert.equal(h.draft().items[0].text,'Drop keys');
});

test('failed network save retains recoverable edits including empty text', async () => {
  const h = harness(async () => { throw new Error('offline'); });
  h.fields['.note-form-title'].value=''; h.fields['.note-form-content'].value=''; h.input();
  await h.drain();
  assert.equal(h.draft().content,'');
  assert.equal(h.context._applyDraftToNote({content:'Old'},'note1').note.content,'');
  assert.equal(h.errors.length,1);
});

test('older save cannot clear newer draft and writes are serialized', async () => {
  const releases=[], writes=[];
  const h=harness((id,payload) => { writes.push(payload.content); return new Promise(r=>releases.push(r)); });
  h.fields['.note-form-content'].value='First'; h.input(); await h.drain();
  h.fields['.note-form-content'].value='Second'; h.input(); await h.drain();
  assert.deepEqual(writes,['First']);
  releases.shift()({}); await new Promise(setImmediate);
  assert.equal(h.draft().content,'Second');
  assert.deepEqual(writes,['First','Second']);
  releases.shift()({}); await new Promise(setImmediate);
  assert.equal(h.draft(),null);
});
