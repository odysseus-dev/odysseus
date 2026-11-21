import test from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown } from './streaming/markdownHarness.mjs';
import { parseHtml } from './helpers/miniDom.mjs';
import { serializeNodes, joinSegments, splitMarkdownBlocks, escapeMarkdownText, codeSpan, imageWidthTitle, encodeImageSrc, clampImageWidth, IMAGE_WIDTH_MAX } from '../frontend/src/features/notes/domain/markdownSerializer.js';
import { MAX_INDENT } from '../frontend/src/features/notes/domain/markdownTasks.js';
import { normaliseLinkUrl } from '../frontend/src/features/notes/ui/richBody.js';
import { preserveSource } from '../frontend/src/features/notes/domain/sourceFidelity.js';

const { mdToHtml } = await loadMarkdown();
const NOTES = { tasks: 'interactive', images: 'notes', dialect: 'notes' };
const render = md => mdToHtml(md, NOTES);
// What the note window renders: the same, with the source stamps it reads.
const renderEditor = md => mdToHtml(md, { ...NOTES, sourceMap: true });
const serializeHtml = html => serializeNodes(parseHtml(html).childNodes);
const roundTrip = md => joinSegments(splitMarkdownBlocks(md).map((block, group) => ({ group, lines: serializeHtml(render(block.source)).lines })));
const visibleText = html => parseHtml(html).textContent;
const exactMarkup = markup => String(markup).replace(/ id="mermaid-[^"]*"/g, '').replace(/>\s*\n\s*</g, '><').replace(/<\/?p>/g, '').trim();
// What the note window writes when every block is edited and nothing in it
// changed (richBody.js editedBlockMarkdown): the serializer's reading of the
// stamped rendering, carried back onto the source.
const editEveryBlock = md => joinSegments(splitMarkdownBlocks(md).map((block, group) => {
  const { lines, lossy } = serializeHtml(renderEditor(block.source));
  assert.equal(lossy, false, block.source);
  const edited = lines.join('\n');
  // The canonical text alone renders as the source does: nothing is lost
  // even where the source's own spelling cannot be kept. Exact markup, except
  // for paragraph tags: a line right under a list renders as bare text, and
  // the canonical text puts a blank line before it, which makes it a paragraph.
  assert.equal(exactMarkup(render(edited)), exactMarkup(render(block.source)), `${block.source}: canonical form`);
  return { group, lines: preserveSource({ source: block.source, baseline: edited, edited, render }).split('\n') };
}));

// Every construct the editor writes must come back as the same markdown, and
// the markdown must render to the same HTML the editor showed.
const VECTORS = [
  ['plain paragraph', 'Just a line of text'],
  ['lines in one block', 'Line one\nLine two'],
  ['blank line between paragraphs', 'Para one\n\nPara two'],
  ['headings', '# Title\n\nBody under it\n\n## Section'],
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
  ['small image', '![](/api/upload/abc123 "w=33")'],
  ['medium image with alt', '![receipt](/api/upload/abc123 "w=66")'],
  ['image keeps a plain title', '![receipt](/api/upload/abc123 "Lunch, Tuesday")'],
  ['data image', '![](data:image/png;base64,iVBORw0KGgo= "w=33")'],
  ['images between text and tasks', 'Before\n\n![](/api/upload/a1 "w=33")\n\n- [ ] after\n\n![](/api/upload/b2)'],
  ['mixed note', '# Groceries\n\nFor the weekend\n\n- [ ] milk\n- [x] eggs\n\n1. shop\n2. cook\n\n> remember the coupons'],
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

test('typed \\( \\) and \\[ \\] stay text; written in the source they stay math', () => {
  const MATH = /ody-math-pending|katex|md-island/;
  // Typed in the rich editor: a text node. Written so it reads back as typed, never as math.
  for (const text of ['LaTeX source: \\(a+b\\) and \\[c\\]', 'inline \\(x\\)', 'display \\[y\\]', 'open \\( only', 'close \\) only']) {
    const md = serializeHtml(`<p>${text}</p>`).lines.join('\n');
    const html = render(md);
    assert.doesNotMatch(html, MATH, `${text}: no math`);
    assert.equal(visibleText(html), text, `${text}: renders as typed`);
    // Opened again in the editor and edited, it stays the same text.
    assert.equal(serializeHtml(renderEditor(md)).lines.join('\n'), md, `${text}: stable through an edit`);
  }
  assert.equal(escapeMarkdownText('\\(a+b\\)'), '\\\\(a+b\\\\)');
  // Written in the source (markdown mode, or stored before): math, and an edit keeps it math.
  for (const md of ['Inline \\(a+b\\) math', 'Display \\[c\\] math', 'both \\(a\\) and \\[b\\]']) {
    assert.match(renderEditor(md), MATH, `${md}: math`);
    assert.equal(editEveryBlock(md), md, `${md}: written back as its source`);
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

test('what has no rich form is an island in the editor, and lossy without its stamp', () => {
  for (const md of ['<details><summary>s</summary>hidden</details>', 'Inline $x^2$ math', '```mermaid\ngraph TD; A-->B\n```']) {
    assert.equal(serializeHtml(render(md)).lossy, true, `${md}: a card rendering cannot be written back`);
    const { lines, lossy } = serializeHtml(renderEditor(md));
    assert.equal(lossy, false, md);
    assert.equal(lines.join('\n'), md, `${md}: the island is written as its source`);
  }
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

test('image sizes: L clears the title, out-of-range widths are clamped', () => {
  assert.equal(roundTrip('![](/api/upload/abc "w=100")'), '![](/api/upload/abc)', 'w=100 is written as no title');
  assert.equal(roundTrip('![](/api/upload/abc "w=2")'), '![](/api/upload/abc "w=5")');
  assert.equal(roundTrip('![](/api/upload/abc "w=250")'), '![](/api/upload/abc)');
  assert.deepEqual([33, 66, 100, '5', 'x'].map(imageWidthTitle), ['w=33', 'w=66', '', 'w=5', '']);
  // What the resize menu leaves in the DOM: data-w wins over any title.
  const resized = '<p><img class="md-note-image" src="/api/upload/abc" alt="r" data-w="66" style="width:66%"></p>';
  assert.equal(serializeHtml(resized).lines.join('\n'), '![r](/api/upload/abc "w=66")');
  const full = '<p><img class="md-note-image" src="/api/upload/abc" alt=""></p>';
  assert.equal(serializeHtml(full).lines.join('\n'), '![](/api/upload/abc)');
});

test('image URLs with ) or spaces are encoded so the destination does not end early', () => {
  assert.equal(encodeImageSrc('/api/upload/a)b c'), '/api/upload/a%29b%20c');
  const html = '<p><img src="https://example.org/a_(b).png" alt="x"></p>';
  const md = serializeHtml(html).lines.join('\n');
  assert.equal(md, '![x](https://example.org/a_(b%29.png)');
  assert.match(render('![x](/api/upload/a%29b)'), /<img class="md-note-image" src="\/api\/upload\/a%29b"/);
});

test('an image paragraph with text keeps both, one line each', () => {
  const md = 'Look at this\n![](/api/upload/abc "w=33")';
  assert.equal(roundTrip(md), md);
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

test('markdown the renderer shows as text is not escaped into text by an edit', () => {
  for (const md of [
    '- a\n  1. b',
    '> - [ ] quoted task',
    '> # quoted heading',
    '<details><summary>More</summary>\n\nbody\n\n</details>',
    '$$\na\n\nb\n$$',
    '[WIP] notes',
    'costs $5 and $6',
    'just text',
  ]) {
    assert.equal(editEveryBlock(md), md, JSON.stringify(md));
  }
});

test('the renderer and the editor clamp image widths and task depth the same way', () => {
  for (let width = 0; width <= 120; width += 1) {
    const img = render(`![](/api/upload/abc "w=${width}")`).match(/<img [^>]*>/)[0];
    const shown = Number((/data-w="(\d+)"/.exec(img) || [])[1] || IMAGE_WIDTH_MAX);
    assert.equal(shown, clampImageWidth(width), `w=${width}`);
    assert.equal(imageWidthTitle(shown), shown === IMAGE_WIDTH_MAX ? '' : `w=${shown}`);
  }
  const deep = render(Array.from({ length: MAX_INDENT + 3 }, (_, i) => `${'  '.repeat(i)}- [ ] level ${i}`).join('\n'));
  const depths = [...deep.matchAll(/data-indent="(\d+)"/g)].map(m => Number(m[1]));
  assert.equal(Math.max(...depths), MAX_INDENT);
});

test('code that holds backticks gets a longer fence, and renders back as the same code', () => {
  for (const [code, md] of [['plain', '`plain`'], ['a`b', '``a`b``'], ['``x', '``` ``x ```'], ['a\nb', '`a b`'], [' padded ', '`  padded  `']]) {
    assert.equal(codeSpan(code), md, JSON.stringify(code));
    // Mid-line: three backticks opening a line are a fence, as in CommonMark.
    assert.equal(parseHtml(render(`say ${md}`)).textContent, `say ${code.replace(/\n/g, ' ')}`, `${md}: renders the code`);
  }
});

test('underline, superscript, subscript and highlight are written as their tags', () => {
  for (const md of ['an <u>underlined</u> word', 'x<sup>2</sup> H<sub>2</sub>O', 'a <mark>hi **b**</mark> c', '<u><mark>both</mark></u>']) {
    assert.equal(roundTrip(md), md, md);
  }
  // Typed as text, the tags stay text.
  assert.equal(serializeHtml('<p>&lt;u&gt;x&lt;/u&gt;</p>').lines.join('\n'), '\\<u>x\\</u>');
});

test('lists of different kinds side by side are written apart, so they read back as two lists', () => {
  const cases = [
    // Chromium's Numbered list under a bullet list: it wraps the new list in a <div>.
    ['<ul><li>apples</li><li>pears</li></ul><div><ol><li>wash</li><li>peel</li></ol></div>', '- apples\n- pears\n\n1. wash\n2. peel', ['ul', 'ol']],
    ['<ol><li>wash</li></ol><ul><li>apples</li></ul>', '1. wash\n\n- apples', ['ol', 'ul']],
    ['<ul class="md-tasklist"><li class="md-task" data-done="false"><span class="md-task-box"></span><span class="md-task-text">task</span></li></ul><ol><li>step</li></ol>', '- [ ] task\n\n1. step', ['ul', 'ol']],
    ['<ul><li>a</li></ul>\n<ol><li>b</li></ol>', '- a\n\n1. b', ['ul', 'ol']],
    // Same kind: one list, nothing is added.
    ['<ul><li>a</li></ul><ul><li>b</li></ul>', '- a\n- b', ['ul']],
    // A block in between is a block of its own too.
    ['<ul><li>a</li></ul><h1>T</h1><ol><li>b</li></ol>', '- a\n\n# T\n\n1. b', ['ul', 'ol']],
  ];
  for (const [html, md, lists] of cases) {
    assert.equal(serializeHtml(html).lines.join('\n'), md, html);
    assert.deepEqual(render(md).match(/<(ul|ol)\b/g).map(tag => tag.slice(1)), lists, md);
  }
});

test('text the browser moved into a code block next to its <code> is written as code, not dropped', () => {
  // Chromium, Backspace at the start of the paragraph under a fence: the
  // paragraph's text lands inside the <pre>, after the <code>.
  const fence = '<pre data-md-open="```js" data-md-close="```"><code class="language-js" data-lang="js">const a = 1;</code>After the code</pre>';
  assert.equal(serializeHtml(fence).lines.join('\n'), '```js\nconst a = 1;After the code\n```');
  assert.equal(serializeHtml('<pre><code data-lang="js">a()</code><span>b()</span></pre>').lines.join('\n'), '```js\na()b()\n```');
});

// A block written right under another one, with no blank line between them
// (the table rendered inside the item or heading above it and
// an edit saved without it, no banner). Typing in any part of such a block
// either reaches the saved text or marks the block lossy, so the window keeps
// the stored text and says so.
const GLUED_ABOVE = ['- item', '1. item', '- [ ] task', '# Plan', 'Para line'];
const GLUED_BELOW = [
  ['table', '| A | B |\n|---|---|\n| 1 | 2 |'],
  ['quote', '> quoted'],
  ['details', '<details>\n<summary>S</summary>\nbody\n</details>'],
];
// Appends a marker to every text the user can type into (not an island: it
// is not editable and is written back from its source).
function typeEverywhere(nodes) {
  const markers = [];
  const visit = node => {
    if (node.nodeType === 3) {
      if (!node.nodeValue.trim()) return;
      const marker = `Typed${markers.length}x`;
      markers.push(marker);
      node.nodeValue += marker;
      return;
    }
    if (node.nodeType === 1 && / md-island /.test(` ${node.getAttribute('class') || ''} `)) return;
    (node.childNodes || []).forEach(visit);
  };
  nodes.forEach(visit);
  return markers;
}

test('a table, quote or details glued under an item, heading or line keeps every edit', () => {
  for (const above of GLUED_ABOVE) {
    for (const [kind, below] of GLUED_BELOW) {
      const md = `${above}\n${below}`;
      const html = renderEditor(md);
      if (kind === 'table') assert.doesNotMatch(html, /<(li|h\d)\b(?:(?!<\/\1>)[\s\S])*<table/, `${md}: the table renders inside the block above`);
      assert.equal(editEveryBlock(md), md, `${md}: an edit that changes nothing keeps the stored text`);
      const nodes = parseHtml(html).childNodes;
      const markers = typeEverywhere(nodes);
      const { lines, lossy } = serializeNodes(nodes);
      const written = lines.join('\n');
      if (lossy) continue;
      for (const marker of markers) assert.ok(written.includes(marker), `${md}: ${marker} dropped without a lossy mark: ${JSON.stringify(written)}`);
    }
  }
});

test('a block the browser put inside an item, a heading or a cell is lossy, never dropped', () => {
  const table = '<table><tr><th>A</th></tr><tbody><tr><td>1</td></tr></tbody></table>';
  for (const html of [
    `<ul><li>item${table}</li></ul>`,
    `<ol><li>item${table}</li></ol>`,
    `<ul class="md-tasklist"><li class="md-task" data-done="false"><span class="md-task-box"></span><span class="md-task-text">task${table}</span></li></ul>`,
    `<h1>Plan${table}</h1>`,
    '<h2>Plan<ul><li>x</li></ul></h2>',
    '<table><tr><th>A<ul><li>x</li></ul></th></tr></table>',
    '<ul class="md-tasklist"><li class="md-task" data-done="false"><span class="md-task-text">task<ul><li>x</li></ul></span></li></ul>',
  ]) {
    assert.equal(serializeHtml(html).lossy, true, html);
  }
  // A list nested in an item is written by the list pass: not lossy.
  assert.deepEqual(serializeHtml('<ul><li>a<ul><li>b</li></ul></li></ul>'), { lines: ['- a', '  - b'], lossy: false });
});

// Headings and items with a block right under them, as people type them.
// The canonical form puts a blank line between the two; an edit keeps the
// stored spelling.
test('a heading or an item with a block right under it keeps its spelling through an edit', () => {
  for (const md of [
    '# Title\nBody under it\n\n## Section',
    '# Groceries\nFor the weekend\n\n- [ ] milk\n- [x] eggs\n\n1. shop\n2. cook\n\n> remember the coupons',
    '# Plan\n- step one\n- step two',
    '## Log\n> quoted',
    '# Plan\n| A | B |\n|---|---|\n| 1 | 2 |',
    '- item\n| A | B |\n|---|---|\n| 1 | 2 |',
  ]) {
    assert.equal(editEveryBlock(md), md, JSON.stringify(md));
  }
});
