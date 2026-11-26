import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { loadMarkdown } from './streaming/markdownHarness.mjs';
import { parseHtml } from './helpers/miniDom.mjs';

const { mdToHtml } = await loadMarkdown();
const read = name => JSON.parse(fs.readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'));
const vectors = read('notes_task_vectors.json');
const golden = read('markdown_plan_mode_golden.json');
const notes = source => mdToHtml(source, { tasks: 'interactive', images: 'notes' });
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const TASK_LI = /<li class="md-task( md-task-done)?" data-task-index="(\d+)" data-done="(true|false)" data-indent="(\d+)"><span class="md-task-box" role="checkbox" aria-checked="(true|false)" tabindex="0" aria-label="([^"]*)"><\/span><span class="md-task-text">(.*?)<\/span><\/li>/g;
const renderedTasks = html => [...html.matchAll(TASK_LI)].map(m => ({
  index: Number(m[2]), done: m[3] === 'true', indent: Number(m[4]), text: m[7], label: m[6], classDone: !!m[1], ariaDone: m[5] === 'true',
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

test('notes dialect: code spans and \\[ \\] / \\( \\) math keep their backslashes', () => {
  const dialect = source => mdToHtml(source, { tasks: 'interactive', images: 'notes', dialect: 'notes' });
  assert.match(dialect('a `C:\\path\\x` b'), /<code>C:\\path\\x<\/code>/);
  const math = dialect('math \\[ x \\* y \\] and \\( a \\_ b \\)');
  assert.match(math, /data-display="true">x \\\* y</);
  assert.match(math, /data-display="false">a \\_ b</);
  // Elsewhere a backslash escapes one markdown character, as the editor writes it.
  assert.equal(dialect('2 \\* 3').trim(), '<p>2 * 3</p>');
  assert.equal(mdToHtml('2 \\* 3').trim(), '<p>2 \\* 3</p>', 'chat and documents are unchanged');
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
  assert.match(html, /<img class="md-note-image" src="data:image\/svg\+xml;base64,PHN2Zz4="/);
  assert.match(html, /<img class="md-note-image" src="blob:http:\/\/localhost\/1234"/);
  assert.equal((html.match(/<img /g) || []).length, 5, html);

  const script = notes('![x](javascript:alert(1))\n\n![y](JaVaScRiPt:alert(1))\n\n![z](vbscript:msgbox)');
  assert.equal(script.includes('<img'), false, script);
  assert.equal(/href="\s*(javascript|vbscript):/i.test(script), false, script);
});

test('an upload written with another address of this server loads from here, and is written back as written', () => {
  const id = '0123456789abcdef0123456789abcdef';
  for (const host of ['http://leos-macbook-pro.local:7860', 'https://notes.example', 'http://192.168.1.20:7000']) {
    const html = notes(`![cat](${host}/api/upload/${id}.png "w=33")`);
    assert.match(html, new RegExp(`<img class="md-note-image" src="/api/upload/${id}\\.png" alt="cat" data-w="33" loading=`), html);
    assert.equal(html.includes('style='), false, 'the width is data-w, never an inline style');
    assert.equal(html.includes(host), false, 'the other address is never fetched');
    const mapped = mdToHtml(`![cat](${host}/api/upload/${id}.png "w=33")`, { images: 'notes', dialect: 'notes', sourceMap: true });
    assert.match(mapped, /data-md-image="" data-md-upload=""/, mapped);
    assert.match(mapped, new RegExp(`data-md-dest="${host.replace(/[.]/g, '\\.')}/api/upload/${id}\\.png &quot;w=33&quot;"`), mapped);
  }
  // A shared note loads it through the note, like any upload.
  assert.match(mdToHtml(`![](https://old.example/api/upload/${id})`, { images: 'notes', mediaNote: 'n1' }), new RegExp(`src="/api/notes/n1/media/${id}"`));
  // Only a real upload path: anything else from another site stays a link.
  for (const url of [`https://evil.example/api/upload/${id}/x`, `https://evil.example/api/upload/${id}?q=1`, 'https://evil.example/api/upload/short', `ftp://evil.example/api/upload/${id}`, `https://evil.example/x/api/upload/${id}`]) {
    const html = notes(`![x](${url})`);
    assert.equal(html.includes('<img'), false, `${url} -> ${html}`);
  }
});

test('note image widths: w=N sizes the image, clamped to 5..100; other titles stay titles', () => {
  const img = md => notes(md).match(/<img [^>]*>/)?.[0] || '';
  // The width rides on data-w (static/css/15-notes.css sizes it): notes keep no inline style.
  assert.match(img('![](/api/upload/a "w=33")'), /data-w="33"/);
  assert.equal(img('![](/api/upload/a "w=33")').includes('style='), false);
  assert.equal(img('![](/api/upload/a "w=33")').includes('title='), false);
  assert.match(img('![](/api/upload/a "w=1")'), /data-w="5"/);
  assert.equal(/data-w|style=/.test(img('![](/api/upload/a "w=100")')), false, 'full width has no attribute');
  assert.equal(/data-w|style=/.test(img('![](/api/upload/a "w=900")')), false, 'clamped to full width');
  assert.match(img('![](/api/upload/a "Receipt")'), /title="Receipt"/);
  assert.match(img('![](/api/upload/a "w=33%")'), /title="w=33%"/);
  assert.equal(/data-w|style=/.test(img('![](/api/upload/a "w=33%")')), false);
  assert.equal(mdToHtml('![](/api/upload/a "w=33")').trim(), '<p>/api/upload/a</p>', 'chat rendering is unchanged');
});

test('note images load lazily and decode off the main thread', () => {
  const imgs = notes('![a](/api/upload/abc)\n\n![b](/api/upload/def "w=50")').match(/<img [^>]*>/g) || [];
  assert.equal(imgs.length, 2);
  for (const img of imgs) {
    assert.match(img, /^<img class="md-note-image" /);
    assert.match(img, / loading="lazy"/);
    assert.match(img, / decoding="async"/);
  }
});

test('notes dialect: an ordered list keeps its start on the <ol>, a list from 1 has none', () => {
  const dialect = md => mdToHtml(md, { tasks: 'interactive', images: 'notes', dialect: 'notes' });
  assert.equal(dialect('3. three\n4. four'), '<ol start="3"><li>three</li>\n<li>four</li></ol>');
  assert.equal(dialect('1. one\n2. two'), '<ol><li>one</li>\n<li>two</li></ol>');
  const two = dialect('Intro\n\n7. seven\n8. eight\n\ntext\n\n1. a');
  assert.deepEqual(two.match(/<ol[^>]*>/g), ['<ol start="7">', '<ol>'], two);
  assert.equal(two.includes('___MD_OL_'), false);
  assert.equal(mdToHtml('3. three\n4. four').includes('start='), false, 'chat rendering is unchanged');
});

test('task text keeps inline markdown and stays a single row', () => {
  const html = notes('- [ ] **bold** `code` https://example.org\n- [x] 2 < 3');
  const [first, second] = renderedTasks(html);
  assert.match(first.text, /<strong>bold<\/strong> <code>code<\/code> <a href="https:\/\/example\.org\/"/);
  assert.equal(second.text, '2 &lt; 3');
});

test('a numbered list right under a bullet list is its own list, with no orphan <li>', () => {
  const dialect = md => mdToHtml(md, { tasks: 'interactive', images: 'notes', dialect: 'notes' });
  const lists = html => {
    const root = parseHtml(html);
    const all = node => node.childNodes.flatMap(child => (child.nodeType === 1 ? [child, ...all(child)] : []));
    const items = all(root).filter(node => node.nodeName === 'LI');
    return {
      top: root.childNodes.filter(node => node.nodeType === 1).map(node => node.nodeName),
      orphans: items.filter(li => !['UL', 'OL'].includes(li.parentNode?.nodeName)).length,
      items: items.map(li => li.textContent.trim()),
    };
  };
  assert.deepEqual(lists(dialect('- apples\n- pears\n1. wash\n2. peel')), { top: ['UL', 'OL'], orphans: 0, items: ['apples', 'pears', 'wash', 'peel'] });
  assert.deepEqual(lists(dialect('1. wash\n2. peel\n- apples')), { top: ['OL', 'UL'], orphans: 0, items: ['wash', 'peel', 'apples'] });
  assert.deepEqual(lists(dialect('Intro\n1. one\n2. two')), { top: ['P', 'OL'], orphans: 0, items: ['one', 'two'] });
  // A task above a numbered list: the list used to land inside the task's text, and was lost on save.
  assert.deepEqual(lists(dialect('- [ ] task\n1. step')), { top: ['UL', 'OL'], orphans: 0, items: ['task', 'step'] });
});

test('a fence the editor renders mid-line or inside a quote renders as it does on the card', () => {
  // The legacy fence pass hands replace() its callback: the match offset used
  // to arrive as the fence's source stamp, and the editor threw on any fence
  // that did not start the note.
  const card = md => mdToHtml(md, { tasks: 'interactive', images: 'notes', dialect: 'notes' });
  const editor = md => mdToHtml(md, { tasks: 'interactive', images: 'notes', dialect: 'notes', sourceMap: true });
  for (const md of ['see ```js\nfoo()``` here', '> ```js\n> x\n> ```', 'a\n\nb ```\nc```']) {
    const html = editor(md);
    assert.equal(html.replace(/ data-md-[a-z]+="[^"]*"/g, ''), card(md).replace(/ data-md-[a-z]+="[^"]*"/g, ''), md);
    // A fence in a quote is read as a fence now, so it
    // carries the window's fence stamps like any other.
    assert.match(html, /<pre(?: data-md-[a-z]+="[^"]*")*><code[^>]*>/, md);
  }
});

test('notes dialect: "*" bullets are a list, "*text*" is still emphasis', () => {
  const dialect = source => mdToHtml(source, { tasks: 'interactive', images: 'notes', dialect: 'notes' }).trim();
  assert.equal(dialect('* alpha\n* beta'), '<ul><li>alpha</li>\n<li>beta</li></ul>');
  assert.equal(dialect('*alpha*'), '<p><em>alpha</em></p>');
  assert.equal(dialect('* *alpha*'), '<ul><li><em>alpha</em></li></ul>');
  assert.equal(dialect('* a\n  * b'), '<ul><li>a</li>\n<li data-indent="1">b</li></ul>');
  // A "*" with a space on its inner side opens or closes nothing.
  assert.equal(dialect('2 * 3 * 4'), '<p>2 * 3 * 4</p>');
  assert.equal(dialect('a *b* c *d*'), '<p>a <em>b</em> c <em>d</em></p>');
});

test('a bare URL autolinked inside parentheses leaves the closing parenthesis out of the link', () => {
  const href = html => [...html.matchAll(/<a [^>]*href="([^"]*)"/g)].map(m => m[1]);
  for (const render of [notes, source => mdToHtml(source)]) {
    const html = render('Booking (see https://example.com/booking) for details.');
    assert.deepEqual(href(html), ['https://example.com/booking']);
    assert.match(html, /<\/a>\) for details\./);
    assert.deepEqual(href(render('(see https://example.com/a.)')), ['https://example.com/a']);
    // Balanced parentheses belong to the URL, as on Wikipedia.
    assert.deepEqual(href(render('See https://en.wikipedia.org/wiki/Mercury_(planet) now.')), ['https://en.wikipedia.org/wiki/Mercury_(planet)']);
    assert.deepEqual(href(render('(https://en.wikipedia.org/wiki/Mercury_(planet))')), ['https://en.wikipedia.org/wiki/Mercury_(planet)']);
  }
});

test('a task checkbox is named after the text it shows', () => {
  const source = '- [ ] Buy **milk** &amp; `eggs`\n- [x] Call [home](https://example.com) \\<now\\>\n- [ ] "quoted"  *twice*';
  const tasks = renderedTasks(mdToHtml(source, { tasks: 'interactive', images: 'notes', dialect: 'notes' }));
  assert.deepEqual(tasks.map(t => t.label), ['Buy milk &amp;amp; eggs', 'Call home &lt;now&gt;', '&quot;quoted&quot; twice']);
});
