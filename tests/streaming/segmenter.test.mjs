// Tests for the pure streaming-markdown segmenter.
//
// The segmenter's one job: given the full accumulated markdown text so far,
// report how many leading characters are SAFE to finalize — i.e. freeze and
// never re-render. "Safe" means: rendering the finalized prefix and the live
// tail separately produces the same DOM as rendering the whole text at once.
//
// Invariant under test everywhere:  render(text[0:n]) + render(text[n:]) === render(text)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown, normalizeRender } from './markdownHarness.mjs';
import { splitFinalized } from '../../static/js/streamingSegmenter.js';

const md = await loadMarkdown();
const render = (t) => md.mdToHtml(t);
const splitOk = (text, n) =>
  normalizeRender(render(text.slice(0, n)) + render(text.slice(n))) === normalizeRender(render(text));

test('harness loads the real renderer', () => {
  assert.match(render('hi'), /<p>hi<\/p>/);
});

test('nothing is finalized while a single block is still streaming', () => {
  assert.equal(splitFinalized('an incomplete paragra', render), 0);
});

test('finalizes the first of two blank-line-separated paragraphs', () => {
  const text = 'para one\n\npara two';
  const n = splitFinalized(text, render);
  assert.equal(n, 'para one\n\n'.length);
  assert.ok(splitOk(text, n), 'split must be render-equivalent');
});

test('never finalizes the last (still-growing) block', () => {
  // The trailing paragraph could still gain more characters, so it stays live.
  const text = 'done\n\nstill going';
  const n = splitFinalized(text, render);
  assert.ok(n <= 'done\n\n'.length);
  assert.ok(splitOk(text, n));
});

test('a closed code fence is finalized immediately, even as the last block', () => {
  // This is the original flicker scenario: a completed code block must freeze
  // so its hover buttons stop being recreated on every later token.
  const text = 'Here:\n\n```python\nprint(1)\n```';
  const n = splitFinalized(text, render);
  assert.ok(n >= text.length - 1, `expected the whole closed fence finalized, got ${n} of ${text.length}`);
  assert.ok(splitOk(text, n));
});

test('does NOT finalize across an OPEN code fence', () => {
  const text = 'intro\n\n```python\nprint(1)\nprint(2)';
  const n = splitFinalized(text, render);
  // "intro" may finalize, but nothing inside the still-open fence may.
  assert.ok(n <= 'intro\n\n'.length, `must not finalize into an open fence, got ${n}`);
  assert.ok(splitOk(text, n));
});

test('does NOT split a loose list (blank line between items is not a boundary)', () => {
  const text = '- a\n\n- b\n\nafter';
  const n = splitFinalized(text, render);
  assert.ok(splitOk(text, n), 'a wrong split here would turn one <ul> into two');
  // The list must not be cut in the middle: either nothing or the whole list.
  assert.ok(n === 0 || n >= '- a\n\n- b\n\n'.length, `loose list was cut at ${n}`);
});
