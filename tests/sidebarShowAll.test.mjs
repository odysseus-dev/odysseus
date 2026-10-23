import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../static/js/sessions.js', import.meta.url), 'utf8');

test('sidebar Show all state is restored from storage', () => {
  assert.match(source, /let _showAllSessions = Storage\.get\(SHOW_ALL_SESSIONS_KEY\) === '1';/);
});

test('every Show all / Show less toggle persists its state', () => {
  const toggles = source.match(/_showAllSessions = !_showAllSessions;/g) || [];
  const saves = source.match(/Storage\.set\(SHOW_ALL_SESSIONS_KEY, _showAllSessions \? '1' : '0'\);/g) || [];
  assert.equal(toggles.length, 2);
  assert.equal(saves.length, toggles.length);
});
