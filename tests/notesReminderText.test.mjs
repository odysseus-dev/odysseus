import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { reminderBody, reminderText } from '../frontend/src/features/notes/domain/noteRules.js';

// The server's reminder builder wrote these bodies (core.notes_markdown.reminder_body);
// the browser's local notification must say the same.
const { reminder } = JSON.parse(fs.readFileSync(new URL('./fixtures/notes_reminder_vectors.json', import.meta.url), 'utf8'));

for (const vector of reminder) {
  test(`reminder body matches the server: ${vector.name}`, () => {
    assert.equal(reminderBody(vector.content), vector.body);
  });
}

test('reminder text drops images and keeps link text', () => {
  assert.equal(reminderText('see ![x](/api/upload/a "w=33") [the map](https://example.org)'), 'see  the map');
  assert.equal(reminderText(null), '');
});
