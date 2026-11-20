import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { loadMarkdown } from './streaming/markdownHarness.mjs';

const { mdToHtml } = await loadMarkdown();
const read = name => JSON.parse(fs.readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'));
const vectors = read('notes_task_vectors.json');
const golden = read('markdown_plan_mode_golden.json');
const notes = source => mdToHtml(source, { tasks: 'interactive', images: 'notes' });
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const TASK_LI = /<li class="md-task( md-task-done)?" data-task-index="(\d+)" data-done="(true|false)" data-indent="(\d+)"><span class="md-task-box" role="checkbox" aria-checked="(true|false)" tabindex="0"><\/span><span class="md-task-text">(.*?)<\/span><\/li>/g;
const renderedTasks = html => [...html.matchAll(TASK_LI)].map(m => ({
  index: Number(m[2]), done: m[3] === 'true', indent: Number(m[4]), text: m[6], classDone: !!m[1], ariaDone: m[5] === 'true',
}));

test('interactive renderer stamps the same task indices as the parser', () => {
  for (const v of vectors.parse) {
    const html = notes(v.content);
    const got = renderedTasks(html);
    assert.deepEqual(got.map(t => [t.index, t.done, t.text]), v.tasks.map(t => [t.index, t.done, esc(t.text)]), v.name);
    for (const t of got) assert.equal(t.classDone && t.ariaDone, t.done, v.name);
    assert.equal(html.includes('___MD_TASK_'), false, v.name);
    assert.equal(html.includes('task-item'), false, `${v.name}: no static task rows in notes mode`);
    assert.equal(/<p>\s*<ul/.test(html), false, `${v.name}: task list is not wrapped in a paragraph`);
  }
});

test('fenced task samples render as code in notes mode, tilde fences included', () => {
  const html = notes('~~~\n- [ ] tilde sample\n~~~\n````\n```\n- [ ] nested sample\n```\n````\n- [ ] real');
  assert.equal(renderedTasks(html).length, 1);
  assert.match(html, /<pre><code[^>]*>- \[ \] tilde sample<\/code>/);
  assert.match(html, /<pre><code[^>]*>```\n- \[ \] nested sample\n```<\/code>/);
  assert.match(html, /<ul class="md-tasklist"><li class="md-task" data-task-index="0"/);
});

test('a task line in inline code or inside <details> keeps its text and gets no checkbox', () => {
  const code = notes('`- [ ] code task`\n- [ ] real');
  assert.match(code, /<code>- \[ \] code task<\/code>/);
  assert.deepEqual(renderedTasks(code).map(t => [t.index, t.text]), [[0, 'real']]);
  // The parser still counts the line inside <details>, so the real task keeps
  // index 1 and a toggle from the card flips the right line.
  const details = notes('<details><summary>more</summary>\n\n- [ ] hidden task\n\n</details>\n\n- [ ] real');
  assert.match(details, /<details><summary>more<\/summary>[\s\S]*- \[ \] hidden task[\s\S]*<\/details>/);
  assert.deepEqual(renderedTasks(details).map(t => [t.index, t.text]), [[1, 'real']]);
});

test('without notes options task lines render exactly as on dev', () => {
  for (const c of golden.cases) assert.equal(mdToHtml(c.input), c.html, c.input);
  for (const c of golden.cases) assert.equal(mdToHtml(c.input, {}), c.html, c.input);
});

test('markdown images: default rendering is unchanged, notes allow this origin only', () => {
  const external = '![x](https://evil.example/p.png?q=secret)';
  assert.match(mdToHtml(external), /<img class="chat-inline-image" src="https:\/\/evil\.example\/p\.png\?q=secret"/);
  assert.match(mdToHtml(external, { tasks: 'interactive' }), /<img class="chat-inline-image" src="https:\/\/evil/, 'images stay opt-in per option');

  const html = notes(`${external}\n\n![up](/api/upload/abc123)\n\n![abs](http://localhost/api/upload/def)\n\n![other](http://localhost/static/x.png)\n\n![d](data:image/png;base64,iVBORw0KGgo=)\n\n![s](data:image/svg+xml;base64,PHN2Zz4=)\n\n![b](blob:http://localhost/1234)\n\n![bx](blob:https://evil.example/1234)`);
  assert.equal(html.includes('<img src="https://evil'), false);
  assert.match(html, /<a href="https:\/\/evil\.example\/p\.png\?q=secret" target="_blank" rel="noopener noreferrer">x<\/a>/);
  assert.match(html, /<img class="md-note-image" src="\/api\/upload\/abc123" alt="up"/);
  assert.match(html, /<img class="md-note-image" src="\/api\/upload\/def" alt="abs"/);
  assert.match(html, /<img class="md-note-image" src="data:image\/png;base64,iVBORw0KGgo="/);
  assert.match(html, /<img class="md-note-image" src="blob:http:\/\/localhost\/1234"/);
  assert.equal((html.match(/<img /g) || []).length, 4, html);
});

test('task text keeps inline markdown and stays a single row', () => {
  const html = notes('- [ ] **bold** `code` https://example.org\n- [x] 2 < 3');
  const [first, second] = renderedTasks(html);
  assert.match(first.text, /<strong>bold<\/strong> <code>code<\/code> <a href="https:\/\/example\.org\/"/);
  assert.equal(second.text, '2 &lt; 3');
});
