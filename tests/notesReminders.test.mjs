import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createNotesReminders } from '../frontend/src/features/notes/application/reminders.js';
import { createNotesNotifications } from '../frontend/src/features/notes/infrastructure/notifications.js';
import { createNotesPersistence, REMINDER_DISMISSED_AT_KEY } from '../frontend/src/features/notes/infrastructure/storage.js';

function harness(notes = []) {
  const now = Date.parse('2024-03-10T09:00'), app = createNotesState();
  app.replaceNotes(notes);
  const values = new Map(), timeouts = new Map(), intervals = new Map(), fired = [], shown = [], highlighted = [], patches = [];
  let nextId = 0, response = Promise.resolve({ ok: true, json: async () => ({ synthesis: 'Synthesis' }) });
  const scheduler = {
    setTimeout: (fn, ms) => { const id = ++nextId; timeouts.set(id, { fn, ms }); return id; }, clearTimeout: id => timeouts.delete(id),
    setInterval: (fn, ms) => { const id = ++nextId; intervals.set(id, { fn, ms }); return id; }, clearInterval: id => intervals.delete(id),
  };
  const persistence = createNotesPersistence({ storage: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) } });
  const reminders = createNotesReminders({
    app, clock: { now: () => now }, scheduler, persistence, dismissedKey: REMINDER_DISMISSED_AT_KEY,
    notifications: { show: (...args) => shown.push(args) }, fetchNotes: async () => {},
    hasItems: note => ['todo', 'goal', 'checklist'].includes(note.note_type), isNoteFullyDone: () => false,
    api: { listNotes: async () => ({ ok: true, json: async () => ({ notes }) }), patchNote: async (...args) => patches.push(args), fireReminder: body => { fired.push(body); return response; } },
    effects: { badge: () => {}, highlight: id => highlighted.push(id) },
  });
  return { now, app, reminders, timeouts, intervals, fired, shown, highlighted, patches, persistence, setResponse: value => { response = value; } };
}

test('startup and loop are idempotent, and panel closure leaves background scheduling alive', async () => {
  const h = harness();
  h.reminders.start(); h.reminders.start();
  assert.deepEqual([...h.timeouts.values()].map(t => t.ms), [3000]);
  await [...h.timeouts.values()][0].fn();
  h.reminders._startReminderLoop();
  assert.deepEqual([...h.intervals.values()].map(t => t.ms), [30000]);
  h.app.setOpen(false);
  assert.equal(h.intervals.size, 1);
  h.reminders.dispose();
  assert.equal(h.intervals.size, 0);
  h.reminders._startReminderLoop();
  assert.equal(h.intervals.size, 0);
});

test('fresh reminders dispatch once, stale reminders mark silently, recurring reminders advance', async () => {
  const now = Date.parse('2024-03-10T09:00');
  const local = value => { const d = new Date(value); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:30`; };
  const h = harness([
    { id: 'fresh', due_date: local(now - 30000), content: 'Body' },
    { id: 'old', due_date: local(now - 180000) },
    { id: 'recurring', due_date: local(now - 180000), repeat: 'daily' },
    { id: 'date', due_date: '2024-03-10' },
    { id: 'archived', archived: true, due_date: local(now - 30000) },
  ]);
  h.reminders._checkReminders(); h.reminders._checkReminders();
  await new Promise(setImmediate);
  assert.deepEqual(h.fired.map(n => n.note_id), ['fresh']);
  assert.deepEqual([...h.persistence._loadFiredReminders()], ['fresh', 'old']);
  assert.equal(h.patches.length, 1);
  assert.equal(h.app.state.notes[2].due_date, '2024-03-11T08:57');
  assert.deepEqual(h.highlighted, ['fresh']);
  assert.equal(h.shown[0][1], 'Synthesis');
});

test('slow synthesis and rejected dispatch use the local fallback exactly once', async () => {
  const h = harness();
  let resolve;
  h.setResponse(new Promise(r => { resolve = r; }));
  h.reminders._fireReminder({ id: 'a', title: 'Title', content: 'Raw' });
  const timer = [...h.timeouts.values()].find(t => t.ms === 1500);
  timer.fn();
  resolve({ ok: true, json: async () => ({ synthesis: 'Too late' }) });
  await new Promise(setImmediate);
  assert.deepEqual(h.shown, [['Title', 'Raw', 'a']]);
  h.setResponse(Promise.reject(Error('offline')));
  h.reminders._fireReminder({ id: 'b', note_type: 'todo', items: [{ text: 'Task', done: false }] });
  await new Promise(setImmediate);
  assert.equal(h.shown.length, 2);
  assert.equal(h.fired[1].body, 'Pending (1):\n- Task');
});

test('notification denial, unsupported browsers and permission errors remain fail-safe', async () => {
  const toast = [];
  const absent = createNotesNotifications({ browser: {}, toast: title => toast.push(title) });
  assert.equal(await absent.ensurePermission(), false);
  absent.show('Title', 'Body', 'a');
  assert.deepEqual(toast, ['Title']);
  const denied = createNotesNotifications({ browser: { Notification: true }, notify: { permission: 'denied' } });
  assert.equal(await denied.ensurePermission(), false);
  const failed = createNotesNotifications({ browser: { Notification: true }, notify: { permission: 'default', requestPermission: async () => { throw Error('blocked'); } } });
  assert.equal(await failed.ensurePermission(), false);
});
