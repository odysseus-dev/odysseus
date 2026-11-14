import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const picker = await readFile(new URL('../static/js/modelPicker.js', import.meta.url), 'utf8');
const start = picker.indexOf('export function getSelectedReasoningEffort()');
const end = picker.indexOf('try { window.__odysseusGetReasoningEffort');
assert.ok(start > 0 && end > start, 'getSelectedReasoningEffort source not found');
const source = picker.slice(start, end).replace('export function', 'function');

// Build the picker's send-time effort getter around injected state.
function makePicker({ sessionId, sessions, pending }) {
  const factory = new Function('state', `
    let _pendingReasoningEffort = state.pending;
    const _deps = { getCurrentSessionId: () => state.sessionId, getSessions: () => state.sessions };
    ${source}
    return { get: getSelectedReasoningEffort, pending: () => _pendingReasoningEffort };
  `);
  return factory({ sessionId, sessions, pending });
}

test('first message of a new chat sends the effort picked before its session existed', () => {
  // The session is materialized right before the send, listed with no effort.
  const session = { id: 's1', thinking_mode: 'off' };
  const p = makePicker({ sessionId: 's1', sessions: [session], pending: 'high' });
  assert.equal(p.get(), 'high');
  assert.equal(session.thinking_mode, 'effort:high');
  assert.equal(p.pending(), null);
});

test('the pending pick is handed to one session only', () => {
  const p = makePicker({ sessionId: 's1', sessions: [{ id: 's1', thinking_mode: 'off' }], pending: 'low' });
  assert.equal(p.get(), 'low');
  assert.equal(p.get(), 'low');
  const other = makePicker({ sessionId: 's2', sessions: [{ id: 's2', thinking_mode: 'off' }], pending: null });
  assert.equal(other.get(), null);
});

test('a stored effort wins over a stale pending pick', () => {
  const p = makePicker({ sessionId: 's1', sessions: [{ id: 's1', thinking_mode: 'effort:medium' }], pending: 'high' });
  assert.equal(p.get(), 'medium');
});

test('an existing chat on Default sends no effort', () => {
  const p = makePicker({ sessionId: 's1', sessions: [{ id: 's1', thinking_mode: 'off' }], pending: null });
  assert.equal(p.get(), null);
});

test('the first message works before the session list knows the new session', () => {
  const p = makePicker({ sessionId: 's9', sessions: [], pending: 'medium' });
  assert.equal(p.get(), 'medium');
  assert.equal(p.pending(), null);
  // Retry before session is listed preserves the transferred effort
  assert.equal(p.get(), 'medium');
});

test('an existing chat with messages does not inherit another chat\'s unsent pick', () => {
  const p = makePicker({ sessionId: 's1', sessions: [{ id: 's1', thinking_mode: 'off', message_count: 4 }], pending: 'high' });
  assert.equal(p.get(), null);
});

test('updating an existing session thinking_mode updates the returned effort', () => {
  const session = { id: 's1', thinking_mode: 'effort:low', message_count: 2 };
  const p = makePicker({ sessionId: 's1', sessions: [session], pending: null });
  assert.equal(p.get(), 'low');
  session.thinking_mode = 'effort:high';
  assert.equal(p.get(), 'high');
  session.thinking_mode = 'off';
  assert.equal(p.get(), null);
});

