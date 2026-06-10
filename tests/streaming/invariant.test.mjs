// The centerpiece correctness test: stream every corpus sample in token-by-token,
// driving the segmenter exactly as the renderer will, and assert the freeze/tail
// split stays render-equivalent to a single full render at EVERY step.
//
//   finalized-html (accumulated from committed deltas) + render(live tail)  ===  render(prefix)
//
// This is run with no DOM and no safety net, so any segmenter bug fails here
// rather than reaching the browser.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown, normalizeRender } from './markdownHarness.mjs';
import { splitFinalized } from '../../static/js/streamingSegmenter.js';
import { CORPUS } from './corpus.mjs';

const md = await loadMarkdown();
const render = (t) => md.mdToHtml(t);

// The two render pipelines chat.js actually feeds streamed text through. BOTH wrap
// the source in squashOutsideCode; the main path additionally runs
// processWithThinking (which floats <think> blocks to the top — a non-local
// transform). Fuzzing the corpus through these — not just bare mdToHtml — closes
// the gap where a squashOutsideCode whitespace/fence edge could break the split.
const renderLiveReply = (t) => md.mdToHtml(md.squashOutsideCode(t)); // chat.js live-reply path
const renderMain = (t) => md.processWithThinking(md.squashOutsideCode(t)); // chat.js main path

// Reproduce the renderer's exact use of the segmenter over a sequence of prefixes.
function simulate(text, prefixLengths, renderFn = render) {
  let committed = 0;
  let finalizedHtml = '';
  for (const len of prefixLengths) {
    const prefix = text.slice(0, len);
    const next = splitFinalized(prefix, renderFn, committed);

    assert.ok(
      next >= committed && next <= prefix.length,
      `committed must stay monotonic and in range (${committed} -> ${next} at length ${len})`,
    );
    if (next > committed) {
      // The renderer renders each finalized delta once and never touches it again.
      finalizedHtml += renderFn(prefix.slice(committed, next));
      committed = next;
    }

    const got = normalizeRender(finalizedHtml + renderFn(prefix.slice(committed)));
    const want = normalizeRender(renderFn(prefix));
    assert.equal(got, want, `invariant broke at prefix length ${len} of ${JSON.stringify(text)}`);
  }
}

const everyPrefix = (t) => Array.from({ length: t.length + 1 }, (_, i) => i);
function chunkAtWhitespace(t) {
  const lens = [];
  for (let i = 1; i <= t.length; i++) {
    if (i === t.length || /\s/.test(t[i - 1])) lens.push(i);
  }
  return lens.length ? lens : [t.length];
}

const RENDERERS = [
  ['mdToHtml', render],
  ['mdToHtml∘squashOutsideCode (live-reply path)', renderLiveReply],
  ['processWithThinking∘squashOutsideCode (main path)', renderMain],
];

for (const [rname, renderFn] of RENDERERS) {
  for (const [name, text] of CORPUS) {
    test(`invariant — ${rname} — char-by-char — ${name}`, () => {
      simulate(text, everyPrefix(text), renderFn);
    });
    test(`invariant — ${rname} — whitespace-chunked — ${name}`, () => {
      simulate(text, chunkAtWhitespace(text), renderFn);
    });
  }
}

// These samples carry <think> blocks (the corpus above is think-free), so they
// specifically exercise the self-verifying local check refusing to finalize inside
// or across a think block that processWithThinking floats to the top.
const THINKING_CORPUS = [
  ['leading think then answer', '<think>Let me reason about it.</think>\n\nThe answer is 42.'],
  ['think with internal blank lines', '<think>Step one.\n\nStep two.\n\nStep three.</think>\n\nDone — the result follows.'],
  ['think then several paragraphs', '<thinking>analyzing the request</thinking>\n\nFirst point made here.\n\nSecond point made here.\n\nThird and final point.'],
  ['think then code block', '<think>I should show code.</think>\n\nHere:\n\n```python\nprint("hi")\n```\n\nThat is the snippet.'],
];
for (const [name, text] of THINKING_CORPUS) {
  test(`invariant (processWithThinking) — char-by-char — ${name}`, () => {
    simulate(text, everyPrefix(text), renderMain);
  });
}

// A final-output check independent of chunking: streaming to completion must equal
// a single full render.
test('streamed-to-completion output equals full render for whole corpus', () => {
  for (const [name, text] of CORPUS) {
    let committed = 0;
    let html = '';
    for (let len = 1; len <= text.length; len++) {
      const next = splitFinalized(text.slice(0, len), render, committed);
      if (next > committed) {
        html += render(text.slice(committed, next));
        committed = next;
      }
    }
    html += render(text.slice(committed));
    assert.equal(normalizeRender(html), normalizeRender(render(text)), `final mismatch for ${name}`);
  }
});
