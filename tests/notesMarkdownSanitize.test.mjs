import test from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown } from './streaming/markdownHarness.mjs';
import { parseHtml, templateDocument } from './helpers/miniDom.mjs';

// The notes passes that sanitize on an inert <template>, run against a
// template that really parses. tests/notesMarkdownRender.test.mjs covers the
// rest of the notes renderer with the plain harness.
const { mdToHtml, noteLinkAllowed } = await loadMarkdown({ templates: templateDocument() });
const NOTES = { tasks: 'interactive', images: 'notes', dialect: 'notes' };
const render = md => mdToHtml(md, NOTES);
const elements = html => parseHtml(html).querySelectorAll('*');
const attrValues = (html, name) => elements(html).map(el => el.getAttribute(name)).filter(v => v != null);

test('link targets keep http(s), mailto, tel, paths and anchors, and refuse every other scheme', () => {
  for (const ok of ['https://example.org', 'http://x.test/a', 'mailto:a@b.org', 'tel:+3312', '/notes/1', '#top', 'relative/path', '']) {
    assert.equal(noteLinkAllowed(ok), true, ok);
  }
  for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', ' java\tscript:x', 'java\nscript:x', '\u0000javascript:x', 'data:text/html,x', 'vbscript:x', 'obsidian://open']) {
    assert.equal(noteLinkAllowed(bad), false, JSON.stringify(bad));
  }
});

test('a dialect escape cannot carry a script scheme past the sanitizer', () => {
  const payloads = [
    '<a href="javascript\\:alert(1)">x</a>',
    '<a href="JAVASCRIPT\\:alert(1)">x</a>',
    '<a href="java\tscript\\:alert(1)">x</a>',
    '<a href="javascript&colon;alert(1)">x</a>',
    '<a href="javascript&#58;alert(1)">x</a>',
    '<a href="javascript&#x3a;alert(1)">x</a>',
    '<a href="data\\:text/html,<b>x</b>">x</a>',
    '<a href="vbscript\\:x">x</a>',
    '<details><summary>s</summary><a href="javascript\\:alert(1)">x</a></details>',
    '- [ ] <a href="javascript\\:alert(1)">in a task</a>',
  ];
  for (const md of payloads) {
    const html = render(md);
    for (const href of attrValues(html, 'href')) {
      assert.equal(/^[\s\u0000-\u001f]*(j\s*a\s*v\s*a\s*s\s*c\s*r\s*i\s*p\s*t|vbscript|data)\s*:/i.test(href), false, `${md} -> ${html}`);
    }
  }
  // A safe link written with an escaped colon still works.
  assert.match(render('<a href="https\\://example.org">ok</a>'), /href="https:\/\/example\.org"/);
});

test('notes HTML keeps no inline handler after the escapes come back', () => {
  const html = render('<details><summary>s</summary><img src="/api/upload/0123456789abcdef0123456789abcdef" onerror="alert(1)"></details>');
  for (const el of elements(html)) for (const { name } of el.attributes) assert.equal(name.startsWith('on'), false, html);
});

test('a style that names a URL in any CSS form is dropped from note HTML', () => {
  const leak = 'https://tracker.example/p.png';
  const styles = [
    `background-image:url(${leak})`,
    `background-image:image-set('${leak}' 1x)`,
    `background-image:-webkit-image-set('${leak}' 1x)`,
    `background:cross-fade(url(${leak}),red 50%)`,
    `background-image:image('${leak}')`,
    `background-image:element(#x)`,
    `background:IMAGE-SET("${leak}" 1x)`,
    `background:image-set ('${leak}' 1x)`,
    `background:u\\72l(${leak})`,
    `background:\\75 rl(${leak})`,
    `background:ur/**/l(${leak})`,
    `background:url\n(${leak})`,
  ];
  for (const style of styles) {
    for (const md of [
      `<img src="/api/upload/0123456789abcdef0123456789abcdef" style="${style.replace(/"/g, '&quot;')}">`,
      `<details><summary>s</summary><div style="${style.replace(/"/g, '&quot;')}">x</div></details>`,
    ]) {
      const html = render(md);
      assert.equal(attrValues(html, 'style').length, 0, `${md} -> ${html}`);
    }
  }
  // Notes keep no inline style at all, even one with no URL in it.
  assert.deepEqual(attrValues(render('<details><summary>s</summary><div style="color:var(--red)">x</div></details>'), 'style'), []);
});

test("a task's checkbox is named after the task's text", () => {
  const html = render('- [ ] call **Bob** about `deploy`\n- [x] done & dusted');
  const boxes = elements(html).filter(el => el.getAttribute('role') === 'checkbox');
  assert.deepEqual(boxes.map(box => box.getAttribute('aria-label')), ['call Bob about deploy', 'done & dusted']);
  assert.deepEqual(boxes.map(box => box.getAttribute('aria-checked')), ['false', 'true']);
});
