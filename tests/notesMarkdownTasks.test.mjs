import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as tasks from '../frontend/src/features/notes/domain/markdownTasks.js';

// Same vectors as tests/test_notes_markdown.py: the JS mirror must number,
// toggle and rewrite task lines exactly like core/notes_markdown.py.
const vectors = JSON.parse(fs.readFileSync(new URL('./fixtures/notes_task_vectors.json', import.meta.url), 'utf8'));

test('parseTaskLines matches the shared vectors', () => {
  for (const v of vectors.parse) {
    assert.deepEqual(tasks.parseTaskLines(v.content), v.tasks, v.name);
    assert.equal(tasks.hasTasks(v.content), v.has_tasks, v.name);
  }
});

test('toggleTask matches the shared vectors', () => {
  for (const v of vectors.toggle) {
    if (v.error) {
      assert.throws(() => tasks.toggleTask(v.content, v.index), RangeError, v.name);
      continue;
    }
    assert.deepEqual(tasks.toggleTask(v.content, v.index), { content: v.content_after, item: v.item }, v.name);
  }
});

test('applyItemsToContent matches the shared vectors', () => {
  for (const v of vectors.apply_items) {
    assert.equal(tasks.applyItemsToContent(v.content, v.items), v.content_after, v.name);
  }
  assert.equal(tasks.applyItemsToContent('- [ ] keep', null), '- [ ] keep');
});

test('appendTaskLine matches the shared vectors', () => {
  for (const v of vectors.append) {
    assert.deepEqual(tasks.appendTaskLine(v.content, v.text, v.indent), { content: v.content_after, item: v.item }, v.name);
  }
  assert.throws(() => tasks.appendTaskLine('x', '   '), RangeError);
});

test('stripTaskLines keeps prose and fenced samples', () => {
  assert.equal(tasks.stripTaskLines('Goal\n\n- [ ] a\n- [x] b\n\n```\n- [ ] sample\n```'), 'Goal\n\n```\n- [ ] sample\n```');
  assert.equal(tasks.itemsToMarkdown([{ text: 'a' }, { text: 'b', done: true, indent: 1 }]), '- [ ] a\n  - [x] b');
});
