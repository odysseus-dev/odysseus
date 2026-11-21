// static/js/markdown.js serves three callers through explicit options: Chat (no
// options), Notes ({ tasks: 'interactive', images: 'notes' }) and Documents
// ({ commonmark: true }). Each option set must change only its own caller, and
// combining them must not leak internal placeholders.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown } from './streaming/markdownHarness.mjs';

const { mdToHtml } = await loadMarkdown();

const DOCUMENT = { shortcodes: false, commonmark: true };
const NOTES = { tasks: 'interactive', images: 'notes' };

const ESCAPES = 'a \\*b\\* \\| c `x\\y` 2\\*3';
const TASKS = '- [ ] a\n- [x] b';
const EXTERNAL_IMAGE = '![x](https://evil.example/p.png)';
const FENCES = '~~~js\nconst a = 1;\n~~~\n```\n- [ ] in fence\n```\n- [ ] real';

test('Documents do not change Chat: no options and commonmark:false render alike', () => {
  for (const source of [ESCAPES, TASKS, EXTERNAL_IMAGE, FENCES]) {
    assert.equal(mdToHtml(source, { commonmark: false }), mdToHtml(source), source);
    assert.equal(mdToHtml(source, {}), mdToHtml(source), source);
  }
  assert.equal(mdToHtml(ESCAPES), '<p>a \\<em>b\\</em> \\| c <code>x\\y</code> 2\\*3</p>');
});

test('Documents resolve escapes and code spans that Notes and Chat leave alone', () => {
  assert.equal(mdToHtml(ESCAPES, DOCUMENT), '<p>a *b* | c <code>x\\y</code> 2*3</p>');
  assert.equal(mdToHtml(ESCAPES, NOTES), mdToHtml(ESCAPES));
});

test('Documents keep static task rows and their images unrestricted by the Notes policy', () => {
  const tasks = mdToHtml(TASKS, DOCUMENT);
  assert.match(tasks, /<li class="task-item"><span class="task-check"/);
  assert.equal(tasks.includes('md-task'), false);
  assert.equal(tasks.includes('data-task-index'), false);
  assert.match(mdToHtml(EXTERNAL_IMAGE, DOCUMENT), /<img class="chat-inline-image" src="https:\/\/evil\.example\/p\.png"/);
});

test('Notes keep interactive tasks and same-origin images, and ignore the Document fence scanner', () => {
  const tasks = mdToHtml(TASKS, NOTES);
  assert.match(tasks, /<ul class="md-tasklist"><li class="md-task" data-task-index="0"/);
  assert.match(tasks, /<li class="md-task md-task-done" data-task-index="1"/);
  const external = mdToHtml(EXTERNAL_IMAGE, NOTES);
  assert.equal(external.includes('<img'), false);
  assert.match(external, /<a href="https:\/\/evil\.example\/p\.png"/);

  const fenced = mdToHtml(FENCES, NOTES);
  assert.match(fenced, /<code class="language-js" data-lang="js">const a = 1;<\/code>/);
  assert.match(fenced, /<code data-lang="">- \[ \] in fence<\/code>/);
  assert.equal([...fenced.matchAll(/data-task-index="(\d+)"/g)].map(m => m[1]).join(), '0');
});

test('Document fences keep their text exactly while Notes trim it as before', () => {
  assert.match(mdToHtml(FENCES, DOCUMENT), /<code class="language-js" data-lang="js">const a = 1;\n<\/code>/);
  assert.match(mdToHtml(FENCES, NOTES), /<code class="language-js" data-lang="js">const a = 1;<\/code>/);
});

test('combined options leak no placeholders and keep both contracts', () => {
  const source = '- [ ] a \\*b\\* `c\\d`\n\n![up](/api/upload/1)\n\n![ext](https://evil.example/p.png)\n\n```\n- [ ] fenced\n```\n\n- [x] done';
  const html = mdToHtml(source, { ...DOCUMENT, ...NOTES });
  for (const marker of ['___MD_TASK_', '___ESC', '___CODE_BLOCK_', '___INLINE_CODE_', '___ALLOWED_HTML_']) {
    assert.equal(html.includes(marker), false, marker);
  }
  assert.match(html, /<li class="md-task" data-task-index="0"[^>]*>.*<span class="md-task-text">a \*b\* <code>c\\d<\/code><\/span>/);
  assert.match(html, /<li class="md-task md-task-done" data-task-index="1"/);
  assert.match(html, /<img class="md-note-image" src="\/api\/upload\/1"/);
  assert.equal(html.includes('evil.example/p.png" alt'), false);
  // Notes extract fences while numbering tasks, so the fence text is trimmed as in Notes alone.
  assert.match(html, /<code data-lang="">- \[ \] fenced<\/code>/);
});

test('a source containing the escape marker text still renders literally in Documents and Notes', () => {
  const source = 'literal ___ESC_92___ and \\*x\\*';
  assert.equal(mdToHtml(source, DOCUMENT), '<p>literal ___ESC_92___ and *x*</p>');
  assert.equal(mdToHtml('- [ ] ___ESC_92___', { ...DOCUMENT, ...NOTES }).includes('___ESC_92___'), true);
});
