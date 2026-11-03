import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { parse } from 'acorn';
import { createNotesApi } from '../frontend/src/features/notes/infrastructure/http.js';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createNotesWorkflows } from '../frontend/src/features/notes/application/workflows.js';

const source = fs.readFileSync(new URL('../static/js/notes.js', import.meta.url), 'utf8');
const ast = parse(source, { ecmaVersion: 'latest', sourceType: 'module' });
const implementation = fs.readFileSync(new URL('../frontend/src/features/notes/index.js', import.meta.url), 'utf8');
const implementationAST = parse(implementation, { ecmaVersion: 'latest', sourceType: 'module' });
function functions(names, ports = {}) {
  const datesSource = fs.readFileSync(new URL('../frontend/src/features/notes/domain/recurrence.js', import.meta.url), 'utf8').replaceAll('export ', '');
  const context = vm.createContext(ports);
  const datesContext = vm.createContext({ ...ports, clock: { now: () => (ports.Date || Date).now() } });
  vm.runInContext(datesSource, datesContext);
  context.dates = datesContext;
  context.clock = { now: () => (ports.Date || Date).now() };
  context.api = createNotesApi({ origin: ports.API_BASE, request: ports.fetch });
  context._saveNote = context.api.saveNote;
  context._patchNote = context.api.patchNote;
  context._deleteNoteApi = context.api.deleteNote;
  context.workflows = createNotesWorkflows({ app: createNotesState(), api: context.api, effects: {} });
  for (const statement of implementationAST.body) {
    const node = statement.declaration || statement;
    if (node.type === 'FunctionDeclaration' && names.includes(node.id.name)) {
      vm.runInContext(implementation.slice(node.start, node.end), context);
    }
  }
  return context;
}

test('legacy public namespace and default API are intentional', () => {
  const names = [];
  for (const node of ast.body) {
    if (node.type !== 'ExportNamedDeclaration') continue;
    if (node.declaration) names.push(node.declaration.id.name);
    for (const spec of node.specifiers) if (spec.exported.name !== 'default') names.push(spec.exported.name);
  }
  assert.deepEqual(names.sort(), ['closeNotes', 'closePanel', 'dismissFiredReminderDot', 'isNotesOpen', 'isPanelOpen', 'newNote', 'openNote', 'openNotes', 'openPanel', 'refreshDueBadge', 'togglePanel']);
  assert.match(source, /window.notesModule = notesModule/);
});

test('CRUD preserves same-origin credentials, payloads and error contracts', async () => {
  const requests = [];
  let ok = true;
  const api = functions(['_saveNote', '_patchNote', '_deleteNoteApi'], {
    API_BASE: 'https://notes.invalid',
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      return { ok, status: 403, json: async () => ({ id: 'saved' }) };
    },
  });
  await api._saveNote({ title: 'New' });
  await api._saveNote({ id: 'a', title: '' });
  await api._patchNote('a', { due_date: null });
  await api._deleteNoteApi('a');
  assert.deepEqual(requests.map(r => [r.url, r.method, r.credentials]), [
    ['https://notes.invalid/api/notes', 'POST', 'same-origin'],
    ['https://notes.invalid/api/notes/a', 'PUT', 'same-origin'],
    ['https://notes.invalid/api/notes/a', 'PUT', 'same-origin'],
    ['https://notes.invalid/api/notes/a', 'DELETE', 'same-origin'],
  ]);
  assert.equal(requests[2].body, '{"due_date":null}');
  ok = false;
  await assert.rejects(api._saveNote({}), /Failed to save note/);
  await assert.rejects(api._patchNote('a', {}), /Failed to update note/);
  await assert.rejects(api._deleteNoteApi('a'), /HTTP 403/);
});

test('recurrence clock is read after first advancement and never for none', () => {
  const calls = [];
  class ClockDate extends Date {
    static now() { calls.push('clock'); return Date.parse('2024-02-01T12:00'); }
    setDate(value) { calls.push('step'); return super.setDate(value); }
  }
  const domain = functions(['_normalizeRepeat', '_advanceRecurring', '_toLocalDatetimeStr', '_nthWeekdayOfMonth', '_lastWeekdayOfMonth'], { Date: ClockDate });
  assert.equal(domain.dates._advanceRecurring('2024-02-01T09:00', 'daily', domain.clock), '2024-02-02T09:00');
  assert.deepEqual(calls, ['step', 'clock']);
  calls.length = 0;
  assert.equal(domain.dates._advanceRecurring('2024-02-01T09:00', 'none', domain.clock), null);
  assert.deepEqual(calls, []);
});

test('archive failure restores position; undo failure rolls back reinsertion', async () => {
  const notes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }], errors = [];
  const stateOwner = createNotesState();
  stateOwner.replaceNotes(notes);
  const app = createNotesWorkflows({
    app: stateOwner,
    api: { patchNote: async () => { throw new Error('refused'); } },
    effects: { render: () => {}, showError: e => errors.push(e) },
  });
  assert.equal(app._archiveNoteById('b'), true);
  assert.deepEqual(notes.map(n => n.id), ['a', 'c']);
  await new Promise(setImmediate);
  assert.deepEqual(notes.map(n => n.id), ['a', 'b', 'c']);
  notes.splice(1, 1);
  app._popAndRunUndo();
  await new Promise(setImmediate);
  assert.deepEqual(notes.map(n => n.id), ['a', 'c']);
  assert.deepEqual(errors, ['Failed to archive', 'Undo failed']);
});
