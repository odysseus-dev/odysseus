import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const chatSource = readFileSync(new URL('../static/js/chat.js', import.meta.url), 'utf8');
const sessionSource = readFileSync(new URL('../static/js/sessions.js', import.meta.url), 'utf8');
const notesSource = readFileSync(new URL('../static/js/features/notes/application/selection.js', import.meta.url), 'utf8');

function setup() {
  const start = chatSource.indexOf("  window.addEventListener('odysseus:notes-context-selected'");
  const end = chatSource.indexOf('  function _fmtContextNumber(n)', start);
  assert.ok(start !== -1 && end > start, 'production event handlers must exist');
  const handlers = new Map();
  const rendered = [];
  let sessionId = null;
  let pending = false;
  let clear;
  const win = { addEventListener: (name, fn) => handlers.set(name, fn) };
  const doc = {
    getElementById: () => ({ addEventListener: (name, fn) => { if (name === 'click') clear = fn; } }),
  };
  const sessionModule = {
    getCurrentSessionId: () => sessionId,
    hasPendingChat: () => pending,
  };
  vm.runInNewContext(
    `const _selectedNoteIdsBySession = new Map(); let _pendingSelectedNoteIds = [];
${chatSource.slice(start, end)}`,
    { window: win, document: doc, sessionModule, console, _renderSelectedNotesIndicator: ids => rendered.push(Array.from(ids)) },
  );
  return {
    setSession(id, isPending = false) { sessionId = id; pending = isPending; },
    emit(name, detail) { handlers.get(name)({ detail }); },
    selected(ids) { const detail = { noteIds: ids, accepted: false }; handlers.get('odysseus:notes-context-selected')({ detail }); return detail; },
    clear() { clear(); },
    lastRendered() { return rendered.at(-1); },
  };
}

test('pending selections transfer only to the materialized chat', () => {
  const ctx = setup();
  ctx.setSession(null, true);
  assert.equal(ctx.selected(['a', 'b']).accepted, true);
  assert.deepEqual(ctx.lastRendered(), ['a', 'b']);
  ctx.setSession('new-chat');
  ctx.emit('odysseus:session-changed', { sessionId: 'new-chat', materialized: true });
  assert.deepEqual(ctx.lastRendered(), ['a', 'b']);
  ctx.setSession('other-chat');
  ctx.emit('odysseus:session-changed', { sessionId: 'other-chat' });
  assert.deepEqual(ctx.lastRendered(), []);
  ctx.setSession('new-chat');
  ctx.emit('odysseus:session-changed', { sessionId: 'new-chat' });
  assert.deepEqual(ctx.lastRendered(), ['a', 'b']);
  ctx.clear();
  assert.deepEqual(ctx.lastRendered(), []);
});

test('new chat resets old indicator and invalid selection is rejected', () => {
  const ctx = setup();
  assert.equal(ctx.selected(['a']).accepted, false);
  ctx.setSession('old-chat');
  assert.equal(ctx.selected(['a']).accepted, true);
  ctx.setSession(null, true);
  ctx.emit('odysseus:session-changed', { sessionId: null });
  assert.deepEqual(ctx.lastRendered(), []);
  assert.equal(ctx.selected(['b']).accepted, true);
  ctx.emit('odysseus:session-changed', { sessionId: null });
  assert.deepEqual(ctx.lastRendered(), []);
  ctx.setSession('fresh');
  ctx.emit('odysseus:session-changed', { sessionId: 'fresh', materialized: true });
  assert.deepEqual(ctx.lastRendered(), []);
});

test('producer events and rejection acknowledgement are wired in source', () => {
  const create = sessionSource.slice(sessionSource.indexOf('export function createDirectChat'), sessionSource.indexOf('export async function materializePendingSession'));
  const materialize = sessionSource.slice(sessionSource.indexOf('export async function materializePendingSession'), sessionSource.indexOf('export function preMaterializePendingSession'));
  assert.match(create, /currentSessionId = null;[\s\S]{0,180}odysseus:session-changed/);
  assert.match(materialize, /currentSessionId = payload.id;[\s\S]{0,180}odysseus:session-changed/);
  assert.match(notesSource, /if \(!detail\.accepted\)/);
});
