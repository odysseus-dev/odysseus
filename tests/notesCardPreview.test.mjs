import test from 'node:test';
import assert from 'node:assert/strict';
import { previewSource, CARD_PREVIEW_MAX_LINES, CARD_PREVIEW_MAX_CHARS, CARD_CHECKLIST_MAX_TASKS } from '../frontend/src/features/notes/ui/cardRendering.js';
import { parseTaskLines } from '../frontend/src/features/notes/domain/markdownTasks.js';

// A card shows a clipped preview, so it renders only the start of the note.

test('a short note is rendered whole', () => {
  const source = 'Title line\n- [ ] one\n- [x] two\n\n```js\ncode\n```';
  assert.equal(previewSource(source), source);
  assert.equal(previewSource(source, { checklist: true }), source);
});

test('a long note is cut at a line, and the cut keeps every shown task index', () => {
  const lines = Array.from({ length: 5000 }, (_, i) => (i % 3 ? `line ${i}` : `- [ ] task ${i}`));
  const source = lines.join('\n');
  const preview = previewSource(source);
  const shown = preview.split('\n');
  assert.ok(shown.length <= CARD_PREVIEW_MAX_LINES + 2, `cut to about ${CARD_PREVIEW_MAX_LINES} lines, got ${shown.length}`);
  assert.ok(preview.length <= CARD_PREVIEW_MAX_CHARS + 10);
  assert.equal(shown[shown.length - 1], '…', 'the preview says it is cut');
  assert.ok(source.startsWith(preview.slice(0, preview.lastIndexOf('\n\n'))), 'a prefix of the note, cut between lines');
  const full = parseTaskLines(source);
  for (const task of parseTaskLines(preview)) assert.deepEqual(task.text, full[task.index].text);
});

test('a long line counts against the character budget', () => {
  const source = 'a'.repeat(1500) + '\n' + 'b'.repeat(1500) + '\nc';
  const preview = previewSource(source);
  assert.ok(preview.startsWith('a'.repeat(1500) + '\n\n'));
  assert.ok(!preview.includes('b'));
});

test('a cut inside a code fence closes the fence', () => {
  const source = 'Intro\n~~~~python\n' + Array.from({ length: 100 }, (_, i) => `x = ${i}`).join('\n') + '\n~~~~\nafter';
  const preview = previewSource(source).split('\n');
  const end = preview.lastIndexOf('…');
  assert.equal(preview[end - 2], '~~~~', 'the open fence is closed before the ellipsis');
  // A task line inside the fence is not counted as a task.
  const checklist = '```\n' + '- [ ] not a task\n'.repeat(300) + '```\n- [ ] real';
  assert.equal(previewSource(checklist, { checklist: true }), checklist);
});

test('a checklist card keeps its scrolling box, up to a task cap', () => {
  const tasks = Array.from({ length: 1500 }, (_, i) => `- [${i % 2 ? 'x' : ' '}] item ${i}`);
  const preview = previewSource(tasks.join('\n'), { checklist: true });
  const shown = parseTaskLines(preview);
  assert.equal(shown.length, CARD_CHECKLIST_MAX_TASKS);
  assert.deepEqual(shown.map(t => t.index), Array.from({ length: CARD_CHECKLIST_MAX_TASKS }, (_, i) => i));
  assert.ok(preview.endsWith('\n\n…'));
  // Under the cap, every task of a long checklist is still in the box.
  const fifty = tasks.slice(0, 50).join('\n');
  assert.equal(previewSource(fifty, { checklist: true }), fifty);
});

test('the first image stays available for the card thumbnail', () => {
  const source = Array.from({ length: 100 }, (_, i) => `line ${i}`).join('\n') + '\n![photo](/api/upload/' + 'a'.repeat(32) + '.png "w=50")\n![second](/api/upload/' + 'b'.repeat(32) + '.png)';
  const preview = previewSource(source);
  assert.ok(preview.endsWith('![photo](/api/upload/' + 'a'.repeat(32) + '.png "w=50")'));
  assert.ok(!preview.includes('b'.repeat(32)));
  // An image already inside the cut is not repeated.
  const early = '![first](/api/upload/' + 'c'.repeat(32) + '.png)\n' + Array.from({ length: 100 }, (_, i) => `line ${i}`).join('\n');
  assert.equal(previewSource(early).split('c'.repeat(32)).length, 2);
});
