import test from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown } from './streaming/markdownHarness.mjs';
import { parseHtml } from './helpers/miniDom.mjs';
import { serializeNodes, joinSegments, splitMarkdownBlocks, sourceNeedsMarkdownMode, editLosesSource, escapeMarkdownText } from '../frontend/src/features/notes/domain/markdownSerializer.js';
import { MAX_INDENT } from '../frontend/src/features/notes/domain/markdownTasks.js';
import { normaliseLinkUrl } from '../frontend/src/features/notes/ui/richBody.js';

const { mdToHtml } = await loadMarkdown();
const NOTES = { tasks: 'interactive', images: 'notes', dialect: 'notes' };
const render = md => mdToHtml(md, NOTES);
const serializeHtml = html => serializeNodes(parseHtml(html).childNodes);
const roundTrip = md => joinSegments(splitMarkdownBlocks(md).map((block, group) => ({ group, lines: serializeHtml(render(block.source)).lines })));
const visibleText = html => parseHtml(html).textContent;
const normalise = markup => String(markup).replace(/>\s*\n\s*</g, '><').trim();
// What the note window writes when every block is edited: a block it locks
// (richBody.js renderBlock) comes back byte for byte, the rest re-serialized.
const editEveryBlock = md => joinSegments(splitMarkdownBlocks(md).map((block, group) => {
  const markup = render(block.source);
  const { lines, lossy } = serializeHtml(markup);
  const locked = lossy || sourceNeedsMarkdownMode(block.source) || editLosesSource(block.source, lines)
    || normalise(render(lines.join('\n'))) !== normalise(markup);
  return locked ? { group, source: block.source } : { group, lines };
}));

// Every construct the editor writes must come back as the same markdown, and
// the markdown must render to the same HTML the editor showed.
const VECTORS = [
  ['plain paragraph', 'Just a line of text'],
  ['lines in one block', 'Line one\nLine two'],
  ['blank line between paragraphs', 'Para one\n\nPara two'],
  ['headings', '# Title\nBody under it\n\n## Section'],
  ['inline marks', '**bold** *italic* ~~struck~~ `code`'],
  ['links and autolinks', 'See [the docs](https://example.org/docs) or https://example.org/raw'],
  ['link with a title', 'See [the docs](https://example.org/docs "The docs")'],
  ['mail and phone links', 'Write [me](mailto:me@example.org) or [call](tel:+33123456789)'],
  ['a link to a page on this site', 'Open [the board](/notes/board) or [this part](#later)'],
  ['bullets', '- one\n- two'],
  ['nested bullets', '- one\n  - two\n    - three\n- four'],
  ['ordered list', '1. first\n2. second\n3. third'],
  ['ordered list keeps its start', '3. third\n4. fourth'],
  ['tasks with nesting', '- [ ] open\n- [x] done\n  - [ ] nested\n    - [x] deeper'],
  ['task text keeps inline marks', '- [ ] call **Bob** about `deploy`'],
  ['fence with language', '```python\nx = 1\nprint(x)\n```'],
  ['fence containing a fence', '````\n```\ninner\n```\n````'],
  ['fence keeps a task sample', '```\n- [ ] not a task\n```'],
  ['fence keeps runs of blank lines', '```py\nx\n\n\n\ny\n```'],
  ['pipe table', '| Name | Qty |\n| --- | --- |\n| Milk | 2 |\n| Eggs | 12 |'],
  ['multi-line blockquote', '> first quoted line\n> second quoted line'],
  ['divider', 'Above\n\n---\n\nBelow'],
  ['emoji is kept', 'Ship it 😀 today 🎉'],
  ['upload image', '![receipt](/api/upload/abc123)'],
  ['mixed note', '# Groceries\nFor the weekend\n\n- [ ] milk\n- [x] eggs\n\n1. shop\n2. cook\n\n> remember the coupons'],
];

test('serializer round-trips every construct the editor writes', () => {
  for (const [name, md] of VECTORS) {
    assert.equal(roundTrip(md), md, name);
    assert.equal(render(roundTrip(md)), render(md), `${name}: renders the same`);
  }
});

test('nothing the renderer emits for these vectors is reported lossy', () => {
  for (const [name, md] of VECTORS) {
    for (const block of splitMarkdownBlocks(md)) assert.equal(serializeHtml(render(block.source)).lossy, false, `${name}: ${block.source}`);
  }
});

test('typed text that looks like markdown is escaped and renders as typed', () => {
  const typed = [
    ['2 * 3 * 4', '2 \\* 3 \\* 4'],
    ['# not a heading', '\\# not a heading'],
    ['- not a bullet', '\\- not a bullet'],
    ['1. not a list', '1\\. not a list'],
    ['> not a quote', '\\> not a quote'],
    ['--- not a rule', '\\--- not a rule'],
    ['[x](y) stays literal', '\\[x](y) stays literal'],
    ['- [ ] not a task', '\\- \\[ ] not a task'],
    ['a `tick` here', 'a \\`tick\\` here'],
    ['~~not struck~~', '\\~\\~not struck\\~\\~'],
    ['$x$ and $y$', '\\$x\\$ and \\$y\\$'],
    ['<b>raw</b>', '\\<b>raw\\</b>'],
    ['a | b | c', 'a \\| b \\| c'],
    ['C:\\temp\\*.txt', 'C:\\temp\\\\\\*.txt'],
  ];
  for (const [text, md] of typed) {
    const html = `<p>${text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>`;
    assert.equal(serializeHtml(html).lines.join('\n'), md, text);
    assert.equal(visibleText(render(md)), text, `${text}: renders as typed`);
  }
});

test('ordinary prose is left unescaped', () => {
  for (const text of ['snake_case and #tag', 'Costs $5 total', 'a ~ b', 'Use C:\\path', 'Done. Next: lunch', 'x > y and a-b']) {
    assert.equal(escapeMarkdownText(text), text);
    assert.equal(serializeHtml(`<p>${text.replace(/>/g, '&gt;')}</p>`).lines.join('\n'), text);
  }
});

test('emoji spans serialize to the emoji, never to nothing', () => {
  const html = render('Party 🎉 time');
  assert.match(html, /class="emoji"/);
  assert.equal(serializeHtml(html).lines.join('\n'), 'Party 🎉 time');
});

test('editor-produced markup serializes like the renderer output', () => {
  const cases = [
    ['<p><b>bold</b> and <i>it</i> and <strike>gone</strike></p>', '**bold** and *it* and ~~gone~~'],
    ['<p>one<br>two</p>', 'one\ntwo'],
    ['<div>typed line</div><div><br></div><div>after a gap</div>', 'typed line\n\nafter a gap'],
    ['<p><br></p>', ''],
    ['<ul><li>a</li><li data-indent="1">b</li></ul>', '- a\n  - b'],
    ['<ul><li>a<ul><li>nested by the browser</li></ul></li></ul>', '- a\n  - nested by the browser'],
    ['<ol><li>x</li><li>y</li></ol>', '1. x\n2. y'],
    ['<ul class="md-tasklist"><li class="md-task" data-done="false" data-indent="2"><span class="md-task-box" contenteditable="false"></span><span class="md-task-text">deep</span></li></ul>', '    - [ ] deep'],
    ['<ul class="md-tasklist"><li class="md-task" data-done="true"><span class="md-task-box"></span>text typed after the span was deleted</li></ul>', '- [x] text typed after the span was deleted'],
    ['<p><a href="https://example.org/x">label</a></p>', '[label](https://example.org/x)'],
    ['<p><b><b>double</b></b></p>', '**double**'],
    ['<p><span style="font-size: 14px;">pasted span</span></p>', 'pasted span'],
    ['<h1>Title</h1>', '# Title'],
    ['<pre><code data-lang="js">a()\nb()</code><button class="copy-code">x</button></pre>', '```js\na()\nb()\n```'],
    ['plain typed text', 'plain typed text'],
  ];
  for (const [html, md] of cases) assert.equal(serializeHtml(html).lines.join('\n'), md, html);
});

test('blocks the dialect cannot hold are reported, by DOM or by source', () => {
  for (const md of ['<details><summary>s</summary>hidden</details>', 'Inline $x^2$ math', '```mermaid\ngraph TD; A-->B\n```']) {
    assert.equal(serializeHtml(render(md)).lossy, true, md);
  }
  assert.equal(sourceNeedsMarkdownMode('| a | b |\n|:--|--:|\n| 1 | 2 |'), true, 'aligned table');
  assert.equal(sourceNeedsMarkdownMode('| a | b |\n| --- | --- |\n| 1 | 2 |'), false, 'plain table');
  assert.equal(sourceNeedsMarkdownMode('![x](https://elsewhere.example/p.png)'), true, 'external image');
  assert.equal(sourceNeedsMarkdownMode('![x](/api/upload/abc)'), false, 'upload');
});

test('blocks split on blank lines outside fences and carry task offsets', () => {
  const blocks = splitMarkdownBlocks('a\n- [ ] x\n\n```\n- [ ] fake\n\nstill code\n```\n\n\n- [ ] y\n- [x] z');
  assert.deepEqual(blocks, [
    { source: 'a\n- [ ] x', taskOffset: 0 },
    { source: '```\n- [ ] fake\n\nstill code\n```', taskOffset: 1 },
    { source: '- [ ] y\n- [x] z', taskOffset: 1 },
  ]);
});

test('joining keeps untouched source byte for byte and separates groups once', () => {
  const md = joinSegments([
    { group: 0, source: '# Kept  \n```\n\n\nraw\n```' },
    { group: 1, lines: ['edited', '', '', 'twice blank'] },
    { group: 1, lines: ['same group'] },
    { group: 2, lines: [''] },
    { group: 3, source: 'last' },
  ]);
  assert.equal(md, '# Kept  \n```\n\n\nraw\n```\n\nedited\n\ntwice blank\nsame group\n\nlast');
});

test('the editor link menu refuses script and other non-web schemes', () => {
  for (const bad of ['javascript:alert(1)', ' JaVaScRiPt:alert(1)', 'java\tscript:x', 'vbscript:msgbox', 'data:text/html,x', 'file:///etc/passwd', '//evil.example', '', '   ']) {
    assert.equal(normaliseLinkUrl(bad), '', JSON.stringify(bad));
  }
  assert.equal(normaliseLinkUrl('https://a.example/x'), 'https://a.example/x');
  assert.equal(normaliseLinkUrl('mailto:me@a.example'), 'mailto:me@a.example');
  assert.equal(normaliseLinkUrl('tel:+33123456789'), 'tel:+33123456789');
  assert.equal(normaliseLinkUrl('/api/notes'), '/api/notes');
  assert.equal(normaliseLinkUrl('#top'), '#top');
  assert.equal(normaliseLinkUrl('a.example/path'), 'https://a.example/path', 'a bare host gets https');
  // A script link that reaches the note some other way never renders as one.
  const html = render('[x](javascript:alert(1))\n\n[y](JaVaScRiPt:alert(1))');
  assert.equal(/href="\s*javascript:/i.test(html), false, html);
});

test('editing a block never drops a link target the renderer cannot show', () => {
  for (const md of [
    '[o](obsidian://open?vault=x)',
    'Read [the spec](ftp://example.org/spec.txt) first',
    '- [ ] ping [Bob](slack://user?id=1)',
    '[x](https://example.org "Title")',
    '[m](mailto:a@b.org) and [t](tel:+3312)',
    '[r](/notes/abc)',
  ]) {
    assert.equal(editEveryBlock(md).replace(/\.org\/ "/, '.org "'), md, md);
  }
});

test('editing a block keeps its fenced code exactly as written', () => {
  for (const md of [
    '```c++\nint x;\n```',
    '```{.py title="a"}\nx = 1\n```',
    '~~~\ncode\n~~~',
    '~~~python\nx = 1\n~~~',
    '```\nx  \n\ty\t\n```',
    '```\n\nfirst line after a blank\n```',
    'Text above\n```\na\n\n\nb\n```',
  ]) {
    assert.equal(editEveryBlock(md), md, JSON.stringify(md));
  }
});

test('joining edited lines keeps blank lines inside fenced code and collapses them outside', () => {
  assert.equal(
    joinSegments([{ group: 0, lines: ['a', '', '', 'b', '```', 'x', '', '', 'y', '```', '', '', 'c'] }]),
    'a\n\nb\n```\nx\n\n\ny\n```\n\nc',
  );
});

test('the renderer and the editor clamp task depth the same way', () => {
  const deep = render(Array.from({ length: MAX_INDENT + 3 }, (_, i) => `${'  '.repeat(i)}- [ ] level ${i}`).join('\n'));
  const depths = [...deep.matchAll(/data-indent="(\d+)"/g)].map(m => Number(m[1]));
  assert.equal(Math.max(...depths), MAX_INDENT);
});

test('text the browser moved into a code block next to its <code> is written as code, not dropped', () => {
  // Chromium, Backspace at the start of the paragraph under a fence: the
  // paragraph's text lands inside the <pre>, after the <code>.
  const fence = '<pre data-md-open="```js" data-md-close="```"><code class="language-js" data-lang="js">const a = 1;</code>After the code</pre>';
  assert.equal(serializeHtml(fence).lines.join('\n'), '```js\nconst a = 1;After the code\n```');
  assert.equal(serializeHtml('<pre><code data-lang="js">a()</code><span>b()</span></pre>').lines.join('\n'), '```js\na()b()\n```');
});
