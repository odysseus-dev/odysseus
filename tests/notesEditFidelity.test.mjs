import test from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown } from './streaming/markdownHarness.mjs';
import { parseHtml } from './helpers/miniDom.mjs';
import { joinSegments, serializeNodes, splitMarkdownBlocks } from '../frontend/src/features/notes/domain/markdownSerializer.js';
import { blockBaseline, editedBlockMarkdown } from '../frontend/src/features/notes/ui/richBody.js';
import { alignText, displayed, patchSource, preserveSource } from '../frontend/src/features/notes/domain/sourceFidelity.js';

// Every part of a note is edited in place. An edited block is written back
// with only what was edited changed: everything the edit kept keeps the
// bytes the note stored, whatever the editor would have written for it.

const { mdToHtml } = await loadMarkdown();
const NOTES = { tasks: 'interactive', images: 'notes', dialect: 'notes' };
// The corpus tests run twice: with external images shown as links (the
// default) and as images (the viewer's setting on). What is saved must not
// depend on it.
const MODES = [['', false], [' (external images shown)', true]];
let externalImages = false;
const render = md => mdToHtml(md, { ...NOTES, externalImages });
const renderEditor = md => mdToHtml(md, { ...NOTES, sourceMap: true, externalImages });

// What the note window does, on a DOM the tests can edit: open renders each
// block, save writes the untouched ones back as stored and the edited ones
// through editedBlockMarkdown.
let opened = 0;
function openNote(content) {
  const key = `open-${opened++}`;
  return splitMarkdownBlocks(content).map(block => {
    const root = parseHtml(renderEditor(block.source));
    return { ...block, root, baseline: blockBaseline(block.source, root.childNodes, { key }), edited: false };
  });
}
function saveNote(blocks, warn = () => assert.fail('nothing in these notes may fall back to its source')) {
  return joinSegments(blocks.map((block, group) => (block.edited
    ? { group, lines: editedBlockMarkdown(block, block.root.childNodes, { render, warn }).split('\n') }
    : { group, source: block.source })));
}
const textNodes = node => node.childNodes.flatMap(child => (child.nodeType === 3 ? [child] : textNodes(child)));
const insideIsland = node => {
  for (let at = node.parentNode; at; at = at.parentNode) if (` ${at.getAttribute?.('class') || ''} `.includes(' md-island ')) return true;
  return false;
};
/** Type `text` at `offset` into the first text node holding `needle` (after it by default). */
function type(block, needle, text, { before = false } = {}) {
  const node = textNodes(block.root).find(n => !insideIsland(n) && n.nodeValue.includes(needle));
  assert.ok(node, `no text node holds ${JSON.stringify(needle)}`);
  const at = node.nodeValue.indexOf(needle) + (before ? 0 : needle.length);
  node.nodeValue = node.nodeValue.slice(0, at) + text + node.nodeValue.slice(at);
  block.edited = true;
}
function replace(block, from, to) {
  const node = textNodes(block.root).find(n => !insideIsland(n) && n.nodeValue.includes(from));
  assert.ok(node, `no text node holds ${JSON.stringify(from)}`);
  node.nodeValue = node.nodeValue.replace(from, to);
  block.edited = true;
}

// One row per trigger of the old lock (richBody.js, before this change).
const CONSTRUCTS = [
  ['external image', '![tracker](https://images.invalid/md.png?q=1)'],
  ['external image with a size and no alt', '![](https://images.invalid/raw.png "w=33")'],
  ['link with a title', '[the docs](https://example.org/docs "The docs")'],
  ['mailto link', '[me](mailto:me@example.org)'],
  ['tel link', '[call](tel:+33123456789)'],
  ['link with a scheme the renderer does not show', '[vault](obsidian://open?vault=x&file=a%20b)'],
  ['link to a page on this site', '[board](/notes/board "Board")'],
  ['aligned table', '| Item | Qty | Note |\n|:-----|----:|:----:|\n| Milk |   2 | ok   |\n| Eggs |  12 |      |'],
  ['display math', '$$x^2 + y^2 = z^2$$'],
  ['inline math', 'area $\\pi r^2$ here'],
  ['bracket math', '\\[ \\int_0^1 x\\,dx \\]'],
  ['paren math', 'see \\(a_1 + b\\) now'],
  ['tilde fence', '~~~\ncode\n~~~'],
  ['fence with an info string', '```{.py title="a"}\nx = 1\n```'],
  ['fence with trailing spaces and tabs', '```\nx  \n\ty\t\n```'],
  ['fence with edge blank lines', '```\n\nfirst line after a blank\n\n```'],
  ['inline code with a backtick', 'run ``a`b`` now'],
  ['inline code over a line break', 'run `a\nb` now'],
  ['mermaid', '```mermaid\ngraph TD; A-->B\n```'],
  ['underline', 'an <u>underlined</u> word'],
  ['superscript and subscript', 'x<sup>2</sup> and H<sub>2</sub>O'],
  ['highlight', 'a <mark>highlighted **bold**</mark> bit'],
  ['raw HTML the dialect has no syntax for', 'press <kbd>Ctrl</kbd> <small>s</small> <ins>new</ins>'],
  ['details', '<details><summary>More</summary>hidden</details>'],
  ['media and frames', '<video src="https://v.invalid/a.mp4"></video> <iframe src="https://f.invalid"></iframe>'],
  ['raw svg', '<svg viewBox="0 0 2 2"><rect width="1" height="1"/></svg>'],
  ['markdown the renderer shows as text', '+ plus bullet\n+ another'],
  ['odd list start', '3) odd start'],
  ['escapes as written', '2 * 3 and \\*kept\\* and snake_case'],
  ['escaped tag', '\\<b>raw\\</b>'],
];

// The shapes of a real 21-note account, counts only: three external images,
// three links with a title or a mail/phone target, two aligned tables, one
// formula. No content of the account is in this file.
const ACCOUNT_SHAPED = [
  '# Trip\nPhotos from the host\n\n![](https://img.invalid/1.jpg)\n\n![view](https://img.invalid/2.jpg "w=66")\n\n- [ ] book\n- [x] pack',
  'Contacts\n[Front desk](tel:+3312345678)\n[Owner](mailto:owner@example.org "Owner")',
  '| Day | Km |\n|:--|--:|\n| Mon | 12 |\n\n| A | B | C |\n|:-:|:--|--:|\n| 1 | 2 | 3 |',
  'Budget $$\\sum_i c_i \\le 500$$ total\n\nSee [the plan](https://example.org/plan "Plan") and ![map](https://img.invalid/map.png)',
];

for (const [suffix, shown] of MODES) {
test(`open, change nothing, save: every note comes back byte for byte, nothing locked${suffix}`, () => {
  externalImages = shown;
  for (const md of [...CONSTRUCTS.map(([, c]) => c), ...ACCOUNT_SHAPED, CONSTRUCTS.map(([, c]) => c).join('\n\n')]) {
    const blocks = openNote(md);
    assert.equal(saveNote(blocks), md, 'untouched');
    // The stricter version: every block treated as edited, with no change.
    for (const block of blocks) {
      assert.equal(block.baseline.lossy, false, `${block.source}: the editor can write this block`);
      block.edited = true;
    }
    assert.equal(saveNote(blocks), md, `${JSON.stringify(md)}: re-serialized`);
  }
});

test(`typing next to a construct changes only the typed text${suffix}`, () => {
  externalImages = shown;
  for (const [name, construct] of CONSTRUCTS) {
    const md = `Before words\n${construct}\nAfter words`;
    const blocks = openNote(md);
    type(blocks[0], 'Before', 'Typed ', { before: true });
    type(blocks[0], 'After words', ' typed');
    assert.equal(saveNote(blocks), `Typed Before words\n${construct}\nAfter words typed`, name);
  }
  // The same construct as its own block, the text in the next one.
  for (const [name, construct] of CONSTRUCTS) {
    const md = `${construct}\n\nNext block`;
    const blocks = openNote(md);
    type(blocks[blocks.length - 1], 'Next', ' typed');
    assert.equal(saveNote(blocks), `${construct}\n\nNext typed block`, `${name}: other block`);
  }
});

test(`editing inside a construct keeps the rest of it as written${suffix}`, () => {
  externalImages = shown;
  const cases = [
    ['an aligned table cell', '| Item | Qty |\n|:-----|----:|\n| Milk |   2 |\n| Eggs |  12 |', b => replace(b, 'Milk', 'Oat milk'), '| Item | Qty |\n|:-----|----:|\n| Oat milk |   2 |\n| Eggs |  12 |'],
    ['a mailto link label', 'Write [me](mailto:me@example.org) soon', b => type(b, 'me', 'ssage'), 'Write [message](mailto:me@example.org) soon'],
    ['a titled link label', '[the docs](https://example.org/docs "The docs")', b => replace(b, 'the docs', 'our docs'), '[our docs](https://example.org/docs "The docs")'],
    ['a custom-scheme link label', '[vault](obsidian://open?vault=x)', b => type(b, 'vault', ' note'), '[vault note](obsidian://open?vault=x)'],
    // Shown as an image, it has no text to type in: tests/notesExternalImages.test.mjs edits it from its menu.
    ...(shown ? [] : [['an external image description', '![tracker](https://images.invalid/md.png?q=1)', b => type(b, 'tracker', ' pixel'), '![tracker pixel](https://images.invalid/md.png?q=1)']]),
    ['a ~~~ fence body', '~~~python extra\nx = 1  \n~~~', b => replace(b, 'x = 1', 'x = 2'), '~~~python extra\nx = 2  \n~~~'],
    ['code that holds a backtick', 'run ``a`b`` now', b => replace(b, 'a`b', 'a`bc'), 'run ``a`bc`` now'],
    ['underlined text', 'an <u>underlined</u> word', b => type(b, 'underlined', ' still'), 'an <u>underlined still</u> word'],
    ['text with escapes', '2 * 3 and \\*kept\\*', b => type(b, '2', '0'), '20 * 3 and \\*kept\\*'],
  ];
  for (const [name, md, edit, expected] of cases) {
    const blocks = openNote(md);
    edit(blocks[0]);
    assert.equal(saveNote(blocks), expected, name);
  }
});

}

test('deleting a construct removes all of it', () => {
  externalImages = false;
  const md = 'Write [me](mailto:me@example.org) soon';
  const blocks = openNote(md);
  const p = blocks[0].root.childNodes[0];
  p.childNodes = p.childNodes.filter(n => n.nodeName !== 'A');
  blocks[0].edited = true;
  assert.equal(saveNote(blocks), 'Write  soon');
  const math = openNote('area $x^2$ here');
  const para = math[0].root.childNodes[0];
  para.childNodes = para.childNodes.filter(n => n.nodeType === 3);
  math[0].edited = true;
  assert.equal(saveNote(math), 'area  here');
});

// A seeded generator: notes mixing the constructs with filler lines, one
// random edit in the filler. The constructs must come back byte for byte, the
// edit must land, and the result must render as the editor's canonical text.
function rng(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32; };
}
for (const [suffix, shown] of MODES) {
test(`fuzz: random notes, random edits in the text around the constructs${suffix}`, () => {
  externalImages = shown;
  const next = rng(20261109);
  const pick = list => list[Math.floor(next() * list.length)];
  const inline = CONSTRUCTS.filter(([, c]) => !c.includes('\n'));
  for (let round = 0; round < 400; round++) {
    const lines = [];
    const used = [];
    for (let i = 0; i < 2 + Math.floor(next() * 4); i++) {
      if (next() < 0.5) lines.push(`filler${round}x${i} words here`);
      else {
        const [, construct] = pick(next() < 0.7 ? inline : CONSTRUCTS);
        lines.push(construct);
        used.push(construct);
      }
    }
    lines.push(`last${round} line`);
    const md = lines.join(next() < 0.5 ? '\n' : '\n\n');
    const blocks = openNote(md);
    const target = blocks.find(b => b.source.includes(`last${round}`));
    const fillers = textNodes(target.root).filter(n => !insideIsland(n) && /(filler|last)\d+/.test(n.nodeValue));
    const node = pick(fillers);
    const word = node.nodeValue.match(/(filler|last)\d+(x\d+)?/)[0];
    const kind = next();
    if (kind < 0.4) node.nodeValue = node.nodeValue.replace(word, `${word} inserted`);
    else if (kind < 0.7) node.nodeValue = node.nodeValue.replace(word, `${word.slice(0, -1)}`);
    else node.nodeValue = node.nodeValue.replace(word, `X${word}`);
    target.edited = true;
    const saved = saveNote(blocks);
    for (const construct of used) assert.ok(saved.includes(construct), `round ${round}: ${JSON.stringify(construct)} kept in ${JSON.stringify(saved)}`);
    const edited = node.nodeValue.match(/\S*(filler|last)\d*\S*( inserted)?/)[0];
    assert.ok(saved.includes(edited), `round ${round}: the edit ${JSON.stringify(edited)} landed in ${JSON.stringify(saved)}`);
  }
});
}

test('the diff aligns by line, then by character', () => {
  externalImages = false;
  assert.deepEqual(Array.from(alignText('ab\ncd', 'ab\nxcd')), [0, 1, 2, 4, 5]);
  assert.equal(patchSource('~~~ py\ncode  \n~~~', '```py\ncode\n```', '```py\ncode!\n```'), '~~~ py\ncode!  \n~~~');
  assert.equal(patchSource('2 * 3', '2 \\* 3', '2 \\* 3 more'), '2 * 3 more');
  assert.equal(patchSource('a', '', 'b'), null, 'nothing shared: no patch');
});

test('whitespace HTML collapses does not make an edit fall back to the canonical text', () => {
  for (const [md, needle] of [['say ** bold** now', 'now'], ['**-- end --\u00a0**\n**-------**', '-------'], ['a  b   c', 'c']]) {
    const blocks = openNote(md);
    type(blocks[0], needle, ' typed');
    const i = md.lastIndexOf(needle) + needle.length;
    assert.equal(saveNote(blocks), md.slice(0, i) + ' typed' + md.slice(i), JSON.stringify(md));
  }
  // Code keeps every character: two renders that differ in code spacing differ.
  assert.notEqual(displayed(render('```\na  b\n```')), displayed(render('```\na b\n```')));
  assert.notEqual(displayed(render('a b')), displayed(render('ab')));
});

test('a block the editor still cannot write is kept as stored, with a warning', () => {
  const block = { source: 'kept as is', baseline: { text: 'kept as is', lossy: false } };
  const warnings = [];
  const nodes = parseHtml('<p>x <video src="a.mp4"></video></p>').childNodes;
  assert.equal(editedBlockMarkdown(block, nodes, { render, warn: (...args) => warnings.push(args) }), 'kept as is');
  assert.equal(warnings.length, 1);
});

test('the baseline is serialized once per source', () => {
  let calls = 0;
  const serialize = () => { calls += 1; return { lines: ['once'], lossy: false }; };
  for (let i = 0; i < 5; i++) blockBaseline('kept once', [], { key: 'baseline-test', serialize });
  assert.equal(calls, 1);
  // A shared note renders its images through the note, so it is its own key.
  blockBaseline('kept once', [], { key: 'baseline-test-shared', serialize });
  assert.equal(calls, 2);
});

test('an edit whose patched source would show differently is saved as the canonical text instead', () => {
  const source = 'Hello  world';   // two spaces, kept as written while they show the same
  const baseline = 'Hello world';  // what the editor's canonical serializer read at open
  const edited = 'Hello world!';
  const faithful = text => `<p>${text}</p>`;
  assert.equal(preserveSource({ source, baseline, edited, render: faithful }), 'Hello  world!', 'the stored bytes are kept around the edit');
  // A renderer for which the stored form no longer shows like the edit (the
  // real case: an un-fenced diagram the patch would turn into a broken table).
  const strict = text => `<p>${text.includes('  ') ? 'something else' : text}</p>`;
  assert.equal(preserveSource({ source, baseline, edited, render: strict }), edited);
  assert.equal(preserveSource({ source, baseline, edited: baseline, render: strict }), source, 'untouched: the source as stored');
});

// What the note window holds after the toolbar-only steps of an earlier bug report, as
// read from Chromium (body.innerHTML): Bullet list, "apples", Enter, "pears",
// Enter, Enter, Numbered list, "wash", Enter, "peel". Typed blocks carry no
// source group, so the window writes them with serializeNodes.
const TYPED_BULLETS_THEN_NUMBERS = '<ul><li>apples</li><li>pears</li></ul><div><ol><li>wash</li><li>peel</li></ol></div>';

test('the editor\'s own bullet-then-numbered list: written, reopened, edited, saved with the edit', () => {
  const typed = serializeNodes(parseHtml(TYPED_BULLETS_THEN_NUMBERS).childNodes);
  assert.equal(typed.lossy, false);
  const content = joinSegments([{ group: null, lines: typed.lines }]);
  // Same rendering as typed: two lists, every item inside one.
  assert.deepEqual(render(content).match(/<(ul|ol)\b/g), ['<ul', '<ol']);
  // Reopen, type before "apples", save: the edit is written, not the stored text.
  for (const stored of [content, '- apples\n- pears\n1. wash\n2. peel']) {
    const blocks = openNote(stored);
    for (const block of blocks) assert.equal(block.baseline.lossy, false, `${JSON.stringify(stored)}: the editor can write this block`);
    type(blocks[0], 'apples', 'green ', { before: true });
    const saved = saveNote(blocks);
    assert.match(saved, /^- green apples\n- pears\n\n?1\. wash\n2\. peel$/, JSON.stringify(stored));
    assert.deepEqual(render(saved).match(/<(ul|ol)\b/g), ['<ul', '<ol']);
  }
});

test('a block kept as stored hands back what was typed, so it is not dropped unseen', () => {
  const block = { source: 'kept as is', baseline: { text: 'kept as is', lossy: false } };
  const kept = [];
  const nodes = parseHtml('<p>kept as is TYPED <video src="a.mp4"></video></p>').childNodes;
  assert.equal(editedBlockMarkdown(block, nodes, { render, warn: () => {}, keep: text => kept.push(text) }), 'kept as is');
  assert.deepEqual(kept.map(text => text.trim()), ['kept as is TYPED']);
  // A block the editor can write never calls keep.
  editedBlockMarkdown({ source: 'a', baseline: { text: 'a', lossy: false } }, parseHtml('<p>ab</p>').childNodes, { render, keep: text => kept.push(text) });
  assert.equal(kept.length, 1);
});

test('a note with a fence mid-line or inside a quote opens in the editor and saves an edit next to it', () => {
  for (const content of ['see ```js\nfoo()``` here', '> ```js\n> x\n> ```\n\nafter']) {
    assert.equal(saveNote(openNote(content)), content, `${content}: untouched`);
  }
  const quoted = openNote('> ```js\n> x\n> ```\n\nafter');
  type(quoted.find(b => b.source === 'after'), 'after', '!');
  assert.equal(saveNote(quoted), '> ```js\n> x\n> ```\n\nafter!');
  // The fence has no source stamp here, so the edited line is written in the
  // editor's own form: the code moves to its own lines, nothing is lost.
  const inline = openNote('see ```js\nfoo()``` here');
  type(inline[0], 'see', 'n');
  const saved = saveNote(inline);
  assert.match(saved, /^seen\b/);
  assert.match(render(saved), /<pre><code class="language-js" data-lang="js">foo\(\)<\/code>/);
  assert.match(saved, /here$/);
});

// A quote holding a fence: the code showed with its `> `,
// and every save wrote that `> ` into the code again, plus a quote line per
// gap. Three edits in a row change only what was typed.
const editThreeTimes = (stored, needle, text = 'Z') => {
  let content = stored;
  for (let round = 1; round <= 3; round++) {
    const blocks = openNote(content);
    const block = blocks.find(b => textNodes(b.root).some(n => !insideIsland(n) && n.nodeValue.includes(needle)));
    assert.ok(block, `${JSON.stringify(stored)}: round ${round}, no block holds ${JSON.stringify(needle)}`);
    type(block, needle, text);
    content = saveNote(blocks);
    assert.equal(content, stored.replace(needle, needle + text.repeat(round)), `${JSON.stringify(stored)}: round ${round}`);
  }
};

test('a quote holding a code block keeps its code byte for byte through three edits', () => {
  for (const [stored, needle] of [
    ['> quote\n> ```js\n> x = 1\n> ```\n> end', 'quote'],
    ['> quote\n> ```js\n> x = 1\n> ```\n> end', 'end'],
    ['> ```js\n> x = 1\n> ```\n> end', 'end'],
    ['> quote\n> ~~~python title\n> a\n>\n> b\n> ~~~\n> end', 'quote'],
    ['> quote\n> ```\n> - [ ] not a task\n> ```', 'quote'],
    ['before\n\n> quote\n> ```js\n> x = 1\n> ```\n\nafter', 'quote'],
  ]) {
    const quote = splitMarkdownBlocks(stored).find(b => b.source.startsWith('>')).source;
    const html = renderEditor(quote);
    assert.doesNotMatch(html, /<code[^>]*>&gt;/, `${JSON.stringify(stored)}: the code shows its quote prefix`);
    assert.equal(html.replace(/ data-md-[a-z]+="[^"]*"/g, ''), render(quote), `${JSON.stringify(stored)}: window and card differ`);
    assert.equal(saveNote(openNote(stored)), stored, `${JSON.stringify(stored)}: untouched`);
    editThreeTimes(stored, needle);
  }
  // Typing in the code itself reaches the code, inside the quote.
  editThreeTimes('> quote\n> ```js\n> x = 1\n> ```\n> end', 'x = 1');
});

test('a quoted code block leaves the task numbering of core/notes_markdown.py alone', () => {
  const html = render('> ```\n> - [ ] a\n> ```\n- [ ] b');
  assert.deepEqual([...html.matchAll(/data-task-index="(\d+)"[\s\S]*?md-task-text">([^<]*)/g)].map(m => [m[1], m[2]]), [['0', 'b']]);
});

test('a <details> holding a code block, with a line above it, keeps the code through three edits', () => {
  const stored = 'Intro line\n<details>\n<summary>S</summary>\n```js\nx = 1\n```\n</details>';
  const html = renderEditor(stored);
  assert.doesNotMatch(html, /___CODE_BLOCK_/, 'a code placeholder leaked');
  assert.doesNotMatch(html, /data-md-src="[^"]*<pre/, 'the island source holds the rendered code');
  assert.equal(saveNote(openNote(stored)), stored, 'untouched');
  editThreeTimes(stored, 'Intro line');
});

test('a note written with "*" bullets keeps its list and its "*" markers through an edit', () => {
  for (const [stored, needle, saved] of [
    ['* alpha\n* beta', 'alpha', '* alpha x\n* beta'],
    ['* *alpha*\n* beta', 'alpha', '* *alpha x*\n* beta'],
    ['*alpha*', 'alpha', '*alpha x*'],
    ['* a\n  * b', 'b', '* a\n  * b x'],
  ]) {
    const blocks = openNote(stored);
    assert.equal(saveNote(blocks), stored, `${stored}: untouched`);
    type(blocks[0], needle, ' x');
    assert.equal(saveNote(blocks), saved, stored);
  }
  assert.deepEqual(render('* alpha x\n* beta').match(/<(ul|li|em)\b/g), ['<ul', '<li', '<li']);
});

// A stored block holds no blank line outside a fence, so the
// blank lines the editor writes between blocks are never part of one: an
// edit at the end of a block leaves every other line of it as stored.
test('an edit at the end of a block leaves the lines it did not touch as stored', () => {
  for (const [stored, needle] of [
    ['1. Do X\n2. Do Y\n   - sub', 'sub'],
    ['- [ ] task one\n- [ ] task two\nafter the list', 'after the list'],
    ['# Title\nBody under it', 'Body under it'],
    ['> quoted\nafter the quote', 'after the quote'],
    ['| a | b |\n|---|---|\n| 1 | 2 |\nafter the table', 'after the table'],
    ['```\n\nfirst line after a blank\n\n```\nafter the code', 'after the code'],
    ['text\n- item\n1. step\nlast line', 'last line'],
  ]) {
    const blocks = openNote(stored);
    assert.equal(saveNote(blocks), stored, `${JSON.stringify(stored)}: untouched`);
    type(blocks[0], needle, 'ZQ');
    assert.equal(saveNote(blocks), `${stored}ZQ`, JSON.stringify(stored));
  }
});

// What the editor holds after: "Start", Enter, Checklist, two tasks, Enter
// twice, a line of text. Only "Start" came from the stored note.
const TYPED_UNDER_START = '<ul class="md-tasklist"><li class="md-task" data-done="false"><span class="md-task-box"></span><span class="md-task-text">task one</span></li><li class="md-task" data-done="false"><span class="md-task-box"></span><span class="md-task-text">task two</span></li></ul><p>after the list</p>';

test('blocks typed in the editor are written a blank line apart, so any renderer reads them as blocks', () => {
  const [start] = parseHtml('<p>Start</p>').childNodes;
  const typed = serializeNodes(parseHtml(TYPED_UNDER_START).childNodes, { after: start });
  const content = joinSegments([{ group: 0, source: 'Start' }, { group: 0, lines: typed.lines }]);
  assert.equal(content, 'Start\n\n- [ ] task one\n- [ ] task two\n\nafter the list');
  // Two blocks each side, by the rules of any markdown: the line under the
  // list is not a lazy continuation of "task two".
  assert.deepEqual(splitMarkdownBlocks(content).map(block => block.source), ['Start', '- [ ] task one\n- [ ] task two', 'after the list']);
  // Reopened and saved untouched, it is written back as it is.
  assert.equal(saveNote(openNote(content)), content);
  // Lines of text typed one under the other stay lines of one paragraph.
  const lines = serializeNodes(parseHtml('<p>second line</p><p>third line</p>').childNodes, { after: start });
  assert.deepEqual(lines.lines, ['second line', 'third line']);
});

test('an image upload refused by the 3-per-10-seconds limit says to wait', async () => {
  const { uploadFailureMessage } = await import('../frontend/src/features/notes/ui/richBody.js');
  assert.equal(uploadFailureMessage(2, 2), '');
  assert.match(uploadFailureMessage(1, 0, { rateLimited: true }), /^Image upload failed: too many uploads in a few seconds\. Wait a moment and try again\.$/);
  assert.equal(uploadFailureMessage(1, 0), 'Image upload failed');
  assert.equal(uploadFailureMessage(2, 1), 'Some images could not be uploaded');
});

test('note buttons draw their icons, not × or — glyphs', async () => {
  const { readFileSync, readdirSync } = await import('node:fs');
  const dir = new URL('../frontend/src/features/notes/ui/', import.meta.url);
  const glyph = /<(?:button|span)[^>]*>\s*(?:&times;|&mdash;|×|—)\s*<\/(?:button|span)>/;
  // reminderPicker.js's tag × predates the note window and is left as it is on dev.
  const found = readdirSync(dir).filter(name => name.endsWith('.js') && name !== 'reminderPicker.js' && glyph.test(readFileSync(new URL(name, dir), 'utf8')));
  assert.deepEqual(found, []);
  const { toolbarHtml } = await import('../frontend/src/features/notes/ui/richBody.js');
  assert.match(toolbarHtml(), /data-md="hr" title="Divider" aria-label="Divider"><svg /);
});
