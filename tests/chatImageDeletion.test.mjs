import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../static/js/sessions.js', import.meta.url), 'utf8');
const start = source.indexOf('const _sessionImageDeletionChoices = new Map();');
const end = source.indexOf('export async function deleteCurrentSessionFromTopMenu()', start);
function harness({ counts = [2], answer = true, ok = true } = {}) {
  const prompts = [], errors = [];
  const ui = { styledConfirm: async (...args) => { prompts.push(args); return answer; }, showError: text => errors.push(text) };
  let index = 0;
  const fetch = async () => ({ ok, json: async () => ({ image_count: counts[index++] }) });
  const api = new Function('fetch', 'uiModule', 'API_BASE', source.slice(start, end) + ';return {confirm: _confirmSessionDeletion, url: _sessionDeletionUrl};')(fetch, ui, '');
  return { ...api, prompts, errors };
}
test('chat images are kept by the primary confirmation choice', async () => {
  const h = harness();
  assert.equal(await h.confirm(['one']), true);
  assert.match(h.prompts[0][0], /images in Gallery/);
  assert.equal(h.prompts[0][1].confirmText, 'Delete chat only');
  assert.equal(h.url('one'), '/api/session/one?delete_images=false');
});
test('explicit image deletion applies to all selected chats once', async () => {
  const h = harness({ counts: [0, 3], answer: 'alternate' });
  assert.equal(await h.confirm(['one', 'two']), true);
  assert.equal(h.prompts.length, 1);
  assert.match(h.url('one'), /delete_images=true$/);
  assert.match(h.url('two'), /delete_images=true$/);
  assert.match(h.url('two'), /delete_images=false$/);
});
test('cancel and failed preview do not authorize deleting images', async () => {
  for (const options of [{ answer: false }, { ok: false }]) {
    const h = harness(options);
    assert.equal(await h.confirm(['one']), false);
    assert.match(h.url('one'), /delete_images=false$/);
    if (options.ok === false) { assert.equal(h.prompts.length, 0); assert.equal(h.errors.length, 1); }
  }
});
test('chats without images get the ordinary confirmation', async () => {
  const h = harness({ counts: [0] });
  assert.equal(await h.confirm(['one']), true);
  assert.equal(h.prompts[0][1].alternateText, undefined);
});
