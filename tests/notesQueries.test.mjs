import test from 'node:test';
import assert from 'node:assert/strict';
import { createQueries, searchTerms } from '../frontend/src/features/notes/application/queries.js';
import { emptySearchText } from '../frontend/src/features/notes/ui/cardRendering.js';

// The panel's filters, search and order (application/queries.js).
const NOTES = [
  { id: 'plain', title: 'Plain', content: 'Groceries', updated_at: '2026-11-01T10:00:00Z' },
  { id: 'tagged', title: 'Tagged', label: 'work #home', content: '', updated_at: '2026-11-02T10:00:00Z' },
  { id: 'remind', title: 'Call', content: '', due_date: '2026-11-20T09:00', updated_at: '2026-11-03T10:00:00Z' },
  { id: 'remind-early', title: 'Dentist', content: '', due_date: '2026-11-10T08:00', updated_at: '2026-11-03T09:00:00Z' },
  { id: 'dated', title: 'Date only', content: '', due_date: '2026-11-21', updated_at: '2026-10-01T10:00:00Z' },
  { id: 'goal', title: 'Run', note_type: 'goal', content: '- [x] 5k\n- [ ] 10k', items: [{ text: '5k', done: true }, { text: '10k', done: false }], updated_at: '2026-10-02T10:00:00Z' },
  { id: 'goal-done', title: 'Read', note_type: 'goal', content: '- [x] Book', items: [{ text: 'Book', done: true }], updated_at: '2026-10-03T10:00:00Z' },
  { id: 'pinned', title: 'Pinned', content: '', pinned: true, updated_at: '2026-09-01T10:00:00Z' },
  { id: 'cal', title: 'Meeting', source: 'calendar', label: 'calendar', content: '', updated_at: '2026-11-06T10:00:00Z' },
];

function view(state, active = () => false) {
  return createQueries({ _hasActiveReminder: active, state: { notes: NOTES, activeLabel: null, activeFilter: null, searchQuery: '', showingArchived: false, ...state } });
}
const ids = queries => queries._sortedVisibleNotes().map(n => n.id);

test('calendar reminder notes never show; pinned first, then newest', () => {
  const all = view({});
  assert.equal(all._userVisibleNotes().some(n => n.id === 'cal'), false);
  const order = ids(all);
  assert.equal(order[0], 'pinned');
  assert.deepEqual(order.slice(1, 4), ['remind', 'remind-early', 'tagged'], 'newest edit first among the rest');
});

test('an active reminder ranks under the pinned block; manual order beats recency', () => {
  const active = view({}, n => n.id === 'dated');
  assert.deepEqual(ids(active).slice(0, 2), ['pinned', 'dated']);
  const ordered = createQueries({ _hasActiveReminder: () => false, state: { notes: [
    { id: 'a', sort_order: 2, updated_at: '2026-11-09T00:00:00Z' }, { id: 'b', sort_order: 1, updated_at: '2026-11-01T00:00:00Z' },
  ], activeFilter: null, searchQuery: '' } });
  assert.deepEqual(ordered._sortedVisibleNotes().map(n => n.id), ['b', 'a']);
});

test('each filter chip keeps only its notes', () => {
  assert.deepEqual(ids(view({ activeFilter: 'reminders' })), ['remind-early', 'remind'], 'timed reminders only, soonest first');
  assert.equal(ids(view({ activeFilter: 'no-reminders' })).includes('remind'), false);
  assert.ok(ids(view({ activeFilter: 'no-reminders' })).includes('dated'), 'a date without a time is not a reminder');
  assert.deepEqual(ids(view({ activeFilter: 'default' })).includes('tagged'), false, 'untagged only');
  assert.deepEqual(ids(view({ activeFilter: 'goals' })).sort(), ['goal', 'goal-done']);
  assert.deepEqual(ids(view({ activeFilter: 'today' })), ['goal'], 'goals with a step left');
  assert.deepEqual(ids(view({ activeLabel: 'home' })), ['tagged'], 'a #tag matches without its hash');
});

test('search matches title, content, tag and task text, case-insensitively as typed lower-case', () => {
  assert.deepEqual(ids(view({ searchQuery: 'groceries' })), ['plain']);
  assert.deepEqual(ids(view({ searchQuery: 'work' })), ['tagged']);
  assert.deepEqual(ids(view({ searchQuery: '10k', activeFilter: 'goals' })), ['goal']);
  assert.deepEqual(ids(view({ searchQuery: 'nothing like this' })), []);
  // Items alone (a note whose content has not been folded yet) still match.
  const legacy = createQueries({ _hasActiveReminder: () => false, state: { notes: [{ id: 'x', items: [{ text: 'Oat milk' }, {}] }], searchQuery: 'oat' } });
  assert.deepEqual(legacy._sortedVisibleNotes().map(n => n.id), ['x']);
});

test('a multi-word search needs every word, in any order and across fields', () => {
  const notes = [
    { id: 'trip', title: 'Trip to Lisbon', content: 'Book the tram to Sintra' },
    { id: 'split', title: 'Lisbon', content: '', items: [{ text: 'Plan the trip' }] },
    { id: 'tagged', title: 'Packing', label: 'trip', content: 'Passport' },
    { id: 'other', title: 'Porto', content: 'A trip north' },
  ];
  const find = searchQuery => createQueries({ _hasActiveReminder: () => false, state: { notes, searchQuery } })
    ._sortedVisibleNotes().map(n => n.id).sort();
  assert.deepEqual(find('trip lisbon'), ['split', 'trip']);
  assert.deepEqual(find('lisbon trip'), ['split', 'trip'], 'word order does not matter');
  assert.deepEqual(find('trip   lisbon '), ['split', 'trip'], 'extra spaces are not part of a word');
  assert.deepEqual(find('trip passport'), ['tagged'], 'one word in a tag, one in the body');
  assert.deepEqual(find('trip madrid'), [], 'every word must match');
  assert.deepEqual(find('sintra'), ['trip'], 'a single word still matches as before');
});

test('an empty or blank search shows every note; a search with no match says so', () => {
  assert.deepEqual(searchTerms(''), []);
  assert.deepEqual(searchTerms('   '), []);
  assert.deepEqual(searchTerms(' Trip  LISBON '), ['trip', 'lisbon']);
  const all = ids(view({}));
  assert.equal(all.length, 8);
  assert.deepEqual(ids(view({ searchQuery: '   ' })), all, 'whitespace is no filter');
  assert.equal(emptySearchText('zzzz'), 'No notes match \u201czzzz\u201d');
  assert.doesNotMatch(emptySearchText('zzzz'), /No notes yet/);
});

test('the archived view lists the most recently changed first, ignoring pins and manual order', () => {
  const archived = createQueries({ _hasActiveReminder: () => false, state: { notes: [
    { id: 'old', pinned: true, sort_order: -5, updated_at: '2026-01-01T00:00:00Z' }, { id: 'new', updated_at: '2026-11-01T00:00:00Z' }, { id: 'none' },
  ], showingArchived: true, searchQuery: '' } });
  assert.deepEqual(archived._sortedVisibleNotes().map(n => n.id), ['new', 'old', 'none']);
});
