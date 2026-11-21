import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  inspectMarkdownVisualCompatibility,
  inspectVisualNodesCompatibility,
  markdownToVisualHtml,
  serializeVisualNodesToMarkdown,
  serializeVisualNodesWithFidelity,
} from '../frontend/src/features/documents/domain/markdownVisualCodec.js';
import { _htmlToPlainText, MARKDOWN_DOCUMENT_RENDER_OPTIONS } from '../frontend/src/features/documents/domain/documentKinds.js';
import { loadMarkdown } from './streaming/markdownHarness.mjs';

import { el, parseCodecHtml, txt } from './markdownCodecHtml.mjs';

const roundTrip = (md) => serializeVisualNodesToMarkdown(parseCodecHtml(markdownToVisualHtml(md)));
const paragraphOf = (text) => [el('p', text)];

describe('markdownVisualCodec', () => {
  describe('inspectMarkdownVisualCompatibility', () => {
    it('accepts supported Markdown features', () => {
      const md = [
        '# Heading 1',
        '',
        'Paragraph with **bold**, *italic*, ~~strike~~, `code`, and [link](https://example.com).',
        '',
        '- Bullet item',
        '  - Nested bullet',
        '',
        '1. First',
        '2. Second',
        '',
        '- [ ] Open task',
        '- [x] Done task',
        '',
        '> Quote line',
        '',
        '---',
        '',
        '```js',
        'const x = 1;',
        '```',
        '',
        '| Col A | Col B |',
        '| :--- | ---: |',
        '| one | two |',
      ].join('\n');

      const result = inspectMarkdownVisualCompatibility(md);
      assert.equal(result.supported, true);
      assert.deepEqual(result.reasons, []);
    });

    it('ignores special tokens inside inline code spans and regular code fences', () => {
      const md = [
        'Inline `$math$` and `<div>html</div>` and `[^1]` are literal.',
        '',
        '```html',
        '<div data-test="1">$not math$</div>',
        '```',
      ].join('\n');

      const result = inspectMarkdownVisualCompatibility(md);
      assert.equal(result.supported, true);
      assert.deepEqual(result.reasons, []);
    });

    it('detects unsupported and non-round-trippable constructs', () => {
      assert.equal(inspectMarkdownVisualCompatibility('---\ntitle: Doc\n---\n\n# Hi').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('<div>raw</div>').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('<!-- hidden -->').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('Value is $x^2 + y^2$.').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('$$\n\\int_0^1 x dx\n$$').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('```mermaid\ngraph TD;\nA-->B;\n```').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('See [docs][ref].\n\n[ref]: https://example.com').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('Claim[^1]\n\n[^1]: Source').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('Setext\n======').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('    indented code()').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('> > nested quote').supported, false);
      // Backslash escapes are supported (see round-trip fidelity), but `\[` and `\(`
      // are LaTeX delimiters in the application renderer.
      assert.equal(inspectMarkdownVisualCompatibility('Display \\[x^2\\] math').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('Inline \\(x\\) math').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('![img](https://example.com/a.png "Title")').supported, false);
      assert.equal(inspectMarkdownVisualCompatibility('```js\nunclosed()').supported, false);
    });
  });

  describe('markdownToVisualHtml', () => {
    it('renders GitHub alert markers as fixed labels and keeps alert bodies editable', () => {
      for (const type of ['NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION']) {
        const html = markdownToVisualHtml(`> [!${type}]\n> Keep this text`);
        assert.match(html, new RegExp(`data-alert="${type}"`));
        assert.match(html, new RegExp(`data-alert-label="${type}"`));
        assert.match(html, /contenteditable="false"/);
        assert.match(html, /<p>Keep this text<\/p>/);
        assert.doesNotMatch(html, /<p>\[!/);
      }
    });
    it('renders headings, inline styles, lists, checklists, quotes, code fences, hr, and tables', () => {
      const md = [
        '# Title',
        '',
        'Text **b** *i* ~~s~~ `c` [Odysseus](https://example.com)',
        '',
        '- [ ] Todo',
        '- [x] Done',
        '',
        '> Quoted text',
        '',
        '---',
        '',
        '```py',
        'print("<tag>")',
        '```',
        '',
        '| A | B |',
        '| :--- | :---: |',
        '| x | y |',
      ].join('\n');

      const html = markdownToVisualHtml(md);
      assert.match(html, /<h1>Title<\/h1>/);
      assert.match(html, /<strong>b<\/strong>/);
      assert.match(html, /<em>i<\/em>/);
      assert.match(html, /<del>s<\/del>/);
      assert.match(html, /<code>c<\/code>/);
      assert.match(html, /<a href="https:\/\/example\.com" rel="noopener noreferrer">Odysseus<\/a>/);
      assert.match(html, /<ul class="rich-checklist">/);
      assert.match(html, /<li data-checked="true" aria-checked="true">Done<\/li>/);
      assert.match(html, /<blockquote><p>Quoted text<\/p><\/blockquote>/);
      assert.match(html, /<hr>/);
      assert.match(html, /<pre><code class="language-py" data-lang="py">print\(&quot;&lt;tag&gt;&quot;\)<\/code><\/pre>/);
      assert.match(html, /<table><thead><tr><th style="text-align: left;">A<\/th><th style="text-align: center;">B<\/th><\/tr><\/thead>/);
    });

    it('blocks unsafe link and image URL schemes', () => {
      const md = '[bad](javascript:alert(1)) and ![xss](javascript:alert(2))';
      const html = markdownToVisualHtml(md);
      assert.equal(html.includes('javascript:'), false);
      assert.equal(html.includes('<a '), false);
      assert.equal(html.includes('<img '), false);
      assert.match(html, /bad/);
    });
  });

  describe('serializeVisualNodesToMarkdown', () => {
    it('serializes alert type and edited body while ignoring its fixed label', () => {
      const nodes = [el('blockquote', [
        el('p', [el('span', 'Warning', { attrs: { 'data-alert-label': 'WARNING' } })]),
        el('p', 'Edited body'),
      ], { attrs: { 'data-alert': 'WARNING' }, classes: ['markdown-alert', 'markdown-alert-warning'] })];
      assert.equal(serializeVisualNodesToMarkdown(nodes), '> [!WARNING]\n> Edited body');
    });
    it('serializes descriptor nodes cleanly back to canonical Markdown', () => {
      const nodes = [
        el('h1', 'Project Spec'),
        el('p', [
          txt('Hello '),
          el('strong', 'world'),
          txt(' and '),
          el('span', 'browser-italic', { style: { fontStyle: 'italic' } }),
          txt(' and '),
          el('span', 'inline()', { style: { fontFamily: 'OdysseusInlineCode, monospace' } }),
          txt(' '),
          el('a', 'Ref', { attrs: { href: 'https://example.com/a(1)' } }),
        ]),
        el('ul', [
          el('li', 'Item one', { attrs: { 'data-checked': 'false' } }),
          el('li', 'Item two', { attrs: { 'data-checked': 'true' } }),
        ], { classes: ['rich-checklist'] }),
        el('blockquote', [el('p', 'Note')]),
        el('pre', [el('code', 'const ok = "`code`";', { attrs: { 'data-lang': 'ts' } })]),
        el('table', [
          el('thead', [
            el('tr', [
              el('th', 'Name', { style: { textAlign: 'left' } }),
              el('th', 'Score', { style: { textAlign: 'right' } }),
            ]),
          ]),
          el('tbody', [
            el('tr', [
              el('td', 'a | b'),
              el('td', '100'),
            ]),
          ]),
        ]),
        el('hr'),
      ];

      const md = serializeVisualNodesToMarkdown(nodes);
      assert.equal(md, [
        '# Project Spec',
        '',
        'Hello **world** and *browser-italic* and `inline()` [Ref](https://example.com/a(1%29)',
        '',
        '- [ ] Item one',
        '- [x] Item two',
        '',
        '> Note',
        '',
        '```ts',
        'const ok = "`code`";',
        '```',
        '',
        '| Name | Score |',
        '| :--- | ---: |',
        '| a \\| b | 100 |',
        '',
        '---',
      ].join('\n'));
    });

    it('ignores trailing placeholder br elements and rejects unsafe hrefs during serialization', () => {
      const nodes = [
        el('p', [
          el('a', 'Click', { attrs: { href: 'javascript:alert(1)' } }),
          el('br'),
        ]),
        el('p', [el('br')]),
      ];
      assert.equal(serializeVisualNodesToMarkdown(nodes), 'Click');
    });
  });

  describe('round-trip fidelity', () => {
    it('keeps literal Markdown characters typed in the visual editor literal after reload', () => {
      const literals = [
        '*.js and *.py globs',
        'use `npm test` literally',
        '[not](a link)',
        '<b>not html</b>',
        '# not a heading',
        '1. not a list',
        '- not a list',
        '> not a quote',
        'a ~~not struck~~ word',
        'C:\\path\\*',
        '_leading underscore_',
        '---',
        'trailing backslash \\',
        'entity-like &amp; text',
      ];
      for (const literal of literals) {
        const md = serializeVisualNodesToMarkdown(paragraphOf(literal));
        const reparsed = parseCodecHtml(markdownToVisualHtml(md));
        assert.equal(reparsed.length, 1, `${JSON.stringify(literal)} -> ${JSON.stringify(md)} must stay one block`);
        assert.equal(reparsed[0].tag, 'p', `${JSON.stringify(literal)} -> ${JSON.stringify(md)} must stay a paragraph`);
        assert.deepEqual(reparsed[0].children, [txt(literal)], `${JSON.stringify(literal)} -> ${JSON.stringify(md)} must stay literal text`);
        assert.equal(inspectMarkdownVisualCompatibility(md).supported, true, `${JSON.stringify(md)} must stay visually editable`);
      }
    });

    it('does not add escapes to ordinary prose', () => {
      for (const prose of ['2 * 3 * 4 = 24', 'snake_case_name stays', "Don't panic.", 'Price: $5 and $6', 'A - B - C', 'Q&A section', '1.5 litres']) {
        assert.equal(serializeVisualNodesToMarkdown(paragraphOf(prose)), prose);
      }
    });

    it('renders backslash escapes literally and re-emits them', () => {
      const md = 'Escaped \\*stars\\* and \\_under\\_ stay literal';
      assert.equal(inspectMarkdownVisualCompatibility(md).supported, true);
      const html = markdownToVisualHtml(md);
      assert.equal(html, '<p>Escaped *stars* and _under_ stay literal</p>');
      assert.equal(roundTrip(md), md);
    });

    it('does not treat whitespace-flanked asterisks as emphasis', () => {
      assert.equal(markdownToVisualHtml('2 * 3 * 4'), '<p>2 * 3 * 4</p>');
    });

    it('preserves list nesting under ordered items and ordered start numbers', () => {
      assert.equal(roundTrip('1. Parent\n   - Child'), '1. Parent\n   - Child');
      assert.equal(roundTrip('10. Ten\n    - Child'), '10. Ten\n    - Child');
      assert.equal(roundTrip('- Parent\n  1. Child'), '- Parent\n  1. Child');
      assert.equal(roundTrip('3. Three\n4. Four'), '3. Three\n4. Four');
    });

    it('keeps multi-line blockquote paragraphs together', () => {
      assert.equal(roundTrip('> line one\n> line two'), '> line one\n> line two');
      assert.equal(roundTrip('> para one\n>\n> para two'), '> para one\n>\n> para two');
    });

    it('round-trips a realistic corpus without changing rendered meaning, idempotently', () => {
      const corpus = [
        '# Plan\n\nIntro with **bold**, *italic*, ~~old~~ and `code`.\n\n## Steps\n\n1. First\n2. Second\n   - Detail a\n   - Detail b\n3. Third\n\n- [ ] Open\n- [x] Done\n\n> Quoted line one\n> quoted line two\n\n---\n\n```js\nconst a = 1;\n```\n\n| Name | Value |\n| --- | ---: |\n| a | 1 |\n| b `x\\|y` | 2 |',
        '* star bullets\n* second\n\n+ plus bullets',
        '1) paren ordered\n2) second',
        '__underscore bold__ and _underscore italic_',
        'Line one\nline two of the same paragraph',
        '### Heading with closing hashes ###',
        'Link [docs](https://example.com/a_b) and image ![alt](/api/upload/x.png)',
        'Escaped \\*literal\\* and C:\\\\dir',
        '- item\n\n- separate list after blank',
        'Price $5 and 2 * 3 * 4.',
        '~~~py\nprint(1)\n~~~',
        '*foo***bar**',
        '**foo***bar*',
        '*foo***bar***baz*',
        '**foo***bar***baz**',
        '**foo *bar* baz**',
        '*foo **bar** baz*',
        '*foo*`bar` and `code`**bold**',
        '*foo*[link](https://example.com) and [link](https://example.com)**bold**',
        'foo**~~bar~~**',
        'foo*~~bar~~*',
        'foo~~**bar**~~',
        'foo~~*bar*~~',
        'foo.**~~bar~~**',
        '***~~bar~~***',
      ];
      for (const md of corpus) {
        assert.equal(inspectMarkdownVisualCompatibility(md).supported, true, JSON.stringify(md));
        const once = roundTrip(md);
        assert.equal(markdownToVisualHtml(once), markdownToVisualHtml(md), `render changed for ${JSON.stringify(md)} -> ${JSON.stringify(once)}`);
        assert.equal(roundTrip(once), once, `not idempotent for ${JSON.stringify(md)}`);
        assert.equal(inspectMarkdownVisualCompatibility(once).supported, true, `canonical form rejected: ${JSON.stringify(once)}`);
      }
    });

    it('preserves semantics and balanced DOM structure for adjacent and nested formatting', () => {
      const cases = [
        {
          name: 'italic immediately followed by bold',
          nodes: [el('p', [el('i', 'foo'), el('b', 'bar')])],
          expectedMd: '*foo***bar**',
          expectedHtml: '<p><em>foo</em><strong>bar</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed.length, 1);
            assert.equal(parsed[0].tag, 'p');
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('bar')]);
          },
        },
        {
          name: 'bold immediately followed by italic',
          nodes: [el('p', [el('b', 'foo'), el('i', 'bar')])],
          expectedMd: '**foo***bar*',
          expectedHtml: '<p><strong>foo</strong><em>bar</em></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'strong');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo')]);
            assert.equal(parsed[0].children[1].tag, 'em');
            assert.deepEqual(parsed[0].children[1].children, [txt('bar')]);
          },
        },
        {
          name: 'adjacent formatting in both directions: italic-bold-italic',
          nodes: [el('p', [el('i', 'foo'), el('b', 'bar'), el('i', 'baz')])],
          expectedMd: '*foo***bar***baz*',
          expectedHtml: '<p><em>foo</em><strong>bar</strong><em>baz</em></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('bar')]);
            assert.equal(parsed[0].children[2].tag, 'em');
            assert.deepEqual(parsed[0].children[2].children, [txt('baz')]);
          },
        },
        {
          name: 'adjacent formatting in both directions: bold-italic-bold',
          nodes: [el('p', [el('b', 'foo'), el('i', 'bar'), el('b', 'baz')])],
          expectedMd: '**foo***bar***baz**',
          expectedHtml: '<p><strong>foo</strong><em>bar</em><strong>baz</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'strong');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo')]);
            assert.equal(parsed[0].children[1].tag, 'em');
            assert.deepEqual(parsed[0].children[1].children, [txt('bar')]);
            assert.equal(parsed[0].children[2].tag, 'strong');
            assert.deepEqual(parsed[0].children[2].children, [txt('baz')]);
          },
        },
        {
          name: 'nested bold in italic',
          nodes: [el('p', [el('i', [txt('foo '), el('b', 'bar'), txt(' baz')])])],
          expectedMd: '*foo **bar** baz*',
          expectedHtml: '<p><em>foo <strong>bar</strong> baz</em></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            const emChildren = parsed[0].children[0].children;
            assert.equal(emChildren[0].text, 'foo ');
            assert.equal(emChildren[1].tag, 'strong');
            assert.deepEqual(emChildren[1].children, [txt('bar')]);
            assert.equal(emChildren[2].text, ' baz');
          },
        },
        {
          name: 'nested italic in bold',
          nodes: [el('p', [el('b', [txt('foo '), el('i', 'bar'), txt(' baz')])])],
          expectedMd: '**foo *bar* baz**',
          expectedHtml: '<p><strong>foo <em>bar</em> baz</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'strong');
            const strongChildren = parsed[0].children[0].children;
            assert.equal(strongChildren[0].text, 'foo ');
            assert.equal(strongChildren[1].tag, 'em');
            assert.deepEqual(strongChildren[1].children, [txt('bar')]);
            assert.equal(strongChildren[2].text, ' baz');
          },
        },
        {
          name: 'combined bold and italic',
          nodes: [el('p', [el('strong', [el('em', 'foobar')])])],
          expectedMd: '***foobar***',
          expectedHtml: '<p><strong><em>foobar</em></strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'strong');
            assert.equal(parsed[0].children[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children[0].children, [txt('foobar')]);
          },
        },
        {
          name: 'italic adjacent to inline code',
          nodes: [el('p', [el('i', 'foo'), el('code', 'bar')])],
          expectedMd: '*foo*`bar`',
          expectedHtml: '<p><em>foo</em><code>bar</code></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.equal(parsed[0].children[1].tag, 'code');
          },
        },
        {
          name: 'inline code adjacent to bold',
          nodes: [el('p', [el('code', 'foo'), el('b', 'bar')])],
          expectedMd: '`foo`**bar**',
          expectedHtml: '<p><code>foo</code><strong>bar</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'code');
            assert.equal(parsed[0].children[1].tag, 'strong');
          },
        },
        {
          name: 'italic adjacent to link',
          nodes: [el('p', [el('i', 'foo'), el('a', 'bar', { attrs: { href: 'https://example.com' } })])],
          expectedMd: '*foo*[bar](https://example.com)',
          expectedHtml: '<p><em>foo</em><a href="https://example.com" rel="noopener noreferrer">bar</a></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.equal(parsed[0].children[1].tag, 'a');
          },
        },
        {
          name: 'link adjacent to bold',
          nodes: [el('p', [el('a', 'foo', { attrs: { href: 'https://example.com' } }), el('b', 'bar')])],
          expectedMd: '[foo](https://example.com)**bar**',
          expectedHtml: '<p><a href="https://example.com" rel="noopener noreferrer">foo</a><strong>bar</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'a');
            assert.equal(parsed[0].children[1].tag, 'strong');
          },
        },
        {
          name: 'bold containing strikethrough after text',
          nodes: [el('p', [txt('foo'), el('b', [el('s', 'bar')])])],
          expectedMd: 'foo**~~bar~~**',
          expectedHtml: '<p>foo<strong><del>bar</del></strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].text, 'foo');
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.equal(parsed[0].children[1].children[0].tag, 'del');
            assert.deepEqual(parsed[0].children[1].children[0].children, [txt('bar')]);
          },
        },
        {
          name: 'italic containing strikethrough after text',
          nodes: [el('p', [txt('foo'), el('i', [el('s', 'bar')])])],
          expectedMd: 'foo*~~bar~~*',
          expectedHtml: '<p>foo<em><del>bar</del></em></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].text, 'foo');
            assert.equal(parsed[0].children[1].tag, 'em');
            assert.equal(parsed[0].children[1].children[0].tag, 'del');
            assert.deepEqual(parsed[0].children[1].children[0].children, [txt('bar')]);
          },
        },
        {
          name: 'strikethrough containing bold after text',
          nodes: [el('p', [txt('foo'), el('s', [el('b', 'bar')])])],
          expectedMd: 'foo~~**bar**~~',
          expectedHtml: '<p>foo<del><strong>bar</strong></del></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].text, 'foo');
            assert.equal(parsed[0].children[1].tag, 'del');
            assert.equal(parsed[0].children[1].children[0].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children[0].children, [txt('bar')]);
          },
        },
        {
          name: 'strikethrough containing italic after text',
          nodes: [el('p', [txt('foo'), el('s', [el('i', 'bar')])])],
          expectedMd: 'foo~~*bar*~~',
          expectedHtml: '<p>foo<del><em>bar</em></del></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].text, 'foo');
            assert.equal(parsed[0].children[1].tag, 'del');
            assert.equal(parsed[0].children[1].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[1].children[0].children, [txt('bar')]);
          },
        },
        {
          name: 'bold containing strikethrough after punctuation',
          nodes: [el('p', [txt('foo.'), el('b', [el('s', 'bar')])])],
          expectedMd: 'foo.**~~bar~~**',
          expectedHtml: '<p>foo.<strong><del>bar</del></strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].text, 'foo.');
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.equal(parsed[0].children[1].children[0].tag, 'del');
            assert.deepEqual(parsed[0].children[1].children[0].children, [txt('bar')]);
          },
        },
        {
          name: 'nested bold, italic and strikethrough',
          nodes: [el('p', [el('b', [el('i', [el('s', 'bar')])])])],
          expectedMd: '***~~bar~~***',
          expectedHtml: '<p><strong><em><del>bar</del></em></strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'strong');
            assert.equal(parsed[0].children[0].children[0].tag, 'em');
            assert.equal(parsed[0].children[0].children[0].children[0].tag, 'del');
            assert.deepEqual(parsed[0].children[0].children[0].children[0].children, [txt('bar')]);
          },
        },
        {
          name: 'italic ends with period immediately followed by bold',
          nodes: [el('p', [el('i', 'foo.'), el('b', 'bar')])],
          expectedMd: '*foo.***bar**',
          expectedHtml: '<p><em>foo.</em><strong>bar</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo.')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('bar')]);
          },
        },
        {
          name: 'bold ends with period immediately followed by italic',
          nodes: [el('p', [el('b', 'foo.'), el('i', 'bar')])],
          expectedMd: '**foo.***bar*',
          expectedHtml: '<p><strong>foo.</strong><em>bar</em></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'strong');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo.')]);
            assert.equal(parsed[0].children[1].tag, 'em');
            assert.deepEqual(parsed[0].children[1].children, [txt('bar')]);
          },
        },
        {
          name: 'italic immediately followed by bold starting with period',
          nodes: [el('p', [el('i', 'foo'), el('b', '.bar')])],
          expectedMd: '*foo***.bar**',
          expectedHtml: '<p><em>foo</em><strong>.bar</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('.bar')]);
          },
        },
        {
          name: 'bold immediately followed by italic starting with period',
          nodes: [el('p', [el('b', 'foo'), el('i', '.bar')])],
          expectedMd: '**foo***.bar*',
          expectedHtml: '<p><strong>foo</strong><em>.bar</em></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'strong');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo')]);
            assert.equal(parsed[0].children[1].tag, 'em');
            assert.deepEqual(parsed[0].children[1].children, [txt('.bar')]);
          },
        },
        {
          name: 'Unicode adjacent formatting: Latin accented',
          nodes: [el('p', [el('i', 'café'), el('b', 'über')])],
          expectedMd: '*café***über**',
          expectedHtml: '<p><em>café</em><strong>über</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children, [txt('café')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('über')]);
          },
        },
        {
          name: 'Unicode adjacent formatting: CJK',
          nodes: [el('p', [el('i', 'こんにちは'), el('b', '世界')])],
          expectedMd: '*こんにちは***世界**',
          expectedHtml: '<p><em>こんにちは</em><strong>世界</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children, [txt('こんにちは')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('世界')]);
          },
        },
        {
          name: 'adjacent code spans merged into single span',
          nodes: [el('p', [el('code', 'foo'), el('code', 'bar')])],
          expectedMd: '`foobar`',
          expectedHtml: '<p><code>foobar</code></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'code');
            assert.deepEqual(parsed[0].children[0].children, [txt('foobar')]);
          },
        },
        {
          name: 'code boundary with backtick adjacent to bold',
          nodes: [el('p', [el('code', 'foo`bar'), el('b', 'baz')])],
          expectedMd: '``foo`bar``**baz**',
          expectedHtml: '<p><code>foo`bar</code><strong>baz</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'code');
            assert.deepEqual(parsed[0].children[0].children, [txt('foo`bar')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('baz')]);
          },
        },
        {
          name: 'escaped punctuation at end of italic before bold',
          nodes: [el('p', [el('i', 'hello*'), el('b', 'world')])],
          expectedMd: '*hello\\****world**',
          expectedHtml: '<p><em>hello*</em><strong>world</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children, [txt('hello*')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('world')]);
          },
        },
        {
          name: 'escaped punctuation at start of bold after italic',
          nodes: [el('p', [el('i', 'hello'), el('b', '*world')])],
          expectedMd: '*hello***\\*world**',
          expectedHtml: '<p><em>hello</em><strong>*world</strong></p>',
          validateDOM: (parsed) => {
            assert.equal(parsed[0].children[0].tag, 'em');
            assert.deepEqual(parsed[0].children[0].children, [txt('hello')]);
            assert.equal(parsed[0].children[1].tag, 'strong');
            assert.deepEqual(parsed[0].children[1].children, [txt('*world')]);
          },
        },
      ];

      for (const tc of cases) {
        const md = serializeVisualNodesToMarkdown(tc.nodes);
        assert.equal(md, tc.expectedMd, `${tc.name}: serialized markdown mismatch`);
        const html = markdownToVisualHtml(md);
        assert.equal(html, tc.expectedHtml, `${tc.name}: rendered html mismatch`);
        const parsed = parseCodecHtml(html);
        tc.validateDOM(parsed);
        const roundTrippedMd = serializeVisualNodesToMarkdown(parsed);
        assert.equal(roundTrippedMd, tc.expectedMd, `${tc.name}: round-tripped markdown mismatch`);
        assert.equal(inspectMarkdownVisualCompatibility(md).supported, true, `${tc.name}: must be compatible`);
      }
    });

    it('rejects constructs the codec would restructure on the first edit', () => {
      const cases = {
        'lazy list continuation': '- item\ncontinued text',
        'multi-paragraph list item': '- item\n\n  second paragraph',
        'lazy blockquote continuation': '> quoted\nlazy line',
        'HTML entity': 'Fish &amp; chips',
        'titled link': '[t](https://example.com "Title")',
        'unsafe link URL': '[x](javascript:alert(1))',
        'unsafe image URL': '![x](javascript:alert(1))',
      };
      for (const [label, md] of Object.entries(cases)) {
        assert.equal(inspectMarkdownVisualCompatibility(md).supported, false, `${label}: ${JSON.stringify(md)}`);
      }
    });

    it('verifies visual node fidelity and rejects unrepresentable formatting combinations', () => {
      // 1. Supported nested formatting is reported lossless
      const supportedNodes = [
        el('p', [txt('foo'), el('b', [el('s', 'bar')])]),
      ];
      const supportedResult = serializeVisualNodesWithFidelity(supportedNodes);
      assert.equal(supportedResult.lossless, true);
      assert.equal(supportedResult.markdown, 'foo**~~bar~~**');
      assert.equal(inspectVisualNodesCompatibility(supportedNodes).supported, true);

      // 2. Unrepresentable formatting (nested links) is flagged as lossy and unsupported
      const unrepresentableNodes = [
        el('p', [
          el('a', [txt('outer '), el('a', 'inner', { attrs: { href: 'https://inner.com' } })], { attrs: { href: 'https://outer.com' } }),
        ]),
      ];
      const lossyResult = serializeVisualNodesWithFidelity(unrepresentableNodes);
      assert.equal(lossyResult.lossless, false);
      assert.ok(lossyResult.reasons.length > 0);
      const compat = inspectVisualNodesCompatibility(unrepresentableNodes);
      assert.equal(compat.supported, false);
      assert.ok(compat.reasons.length > 0);
    });

    it('produces semantically equivalent HTML in the Document Preview renderer (static/js/markdown.js)', async () => {
      const { mdToHtml } = await loadMarkdown();
      const testCases = [
        '*foo***bar**',
        '**foo***bar*',
        '*foo.***bar**',
        '**foo.***bar*',
        '*foo***.bar**',
        '**foo***.bar*',
        '**foo *bar* baz**',
        '*foo **bar** baz*',
        '***both***',
        '*café***über**',
        '*こんにちは***世界**',
        'foo**~~bar~~**',
        'foo*~~bar~~*',
        'foo~~**bar**~~',
        'foo~~*bar*~~',
      ];
      for (const md of testCases) {
        const codecHtml = markdownToVisualHtml(md);
        const previewHtml = mdToHtml(md, MARKDOWN_DOCUMENT_RENDER_OPTIONS);
        const codecText = _htmlToPlainText(codecHtml).trim();
        const previewText = _htmlToPlainText(previewHtml).trim();
        assert.equal(codecText, previewText, `text content mismatch for ${md}`);
        if (md.includes('foo') && md.includes('bar') && !md.includes('~~')) {
          assert.equal(previewHtml.includes('<em>') && previewHtml.includes('<strong>'), true, `previewHtml must have both tags for ${md}`);
        }
        if (md.includes('~~')) {
          assert.equal(previewHtml.includes('<del>'), true, `previewHtml must have del for ${md}`);
        }
      }
    });
  });
});

it('serialization preserves repeated blank lines inside fenced code', () => {
  const raw = 'first\n\n\nlast';
  const nodes = [{ type: 'element', tag: 'pre', children: [{ type: 'element', tag: 'code', children: [{ type: 'text', text: raw }] }] }];
  assert.equal(serializeVisualNodesToMarkdown(nodes), '```\n' + raw + '\n```');
});

it('ignores empty paragraph caret placeholders without ignoring actual breaks', () => {
  const paragraph = el('p', [el('strong', 'Bold'), txt(' and '), el('em', 'italic')]);
  const empty = el('div', [el('br')]);
  const emptyWithMarks = el('p', [el('b', [el('i', [el('br')])])]);
  assert.deepEqual(serializeVisualNodesWithFidelity([paragraph, empty, emptyWithMarks]), {
    markdown: '**Bold** and *italic*',
    blocks: ['**Bold** and *italic*'],
    lossless: true,
    reasons: [],
  });
  const quote = el('blockquote', [el('p', 'Quote'), empty]);
  assert.equal(serializeVisualNodesWithFidelity([quote]).markdown, '> Quote');
  assert.equal(serializeVisualNodesWithFidelity([quote]).lossless, true);

  for (const node of [
    el('p', [txt('Visible'), el('br')]),
    el('p', [txt('Visible'), el('br'), el('br')]),
    el('div', [el('br'), el('br')]),
    el('h1', [el('br')]),
    el('ul', [el('li', [el('br')])]),
    el('table', [el('tr', [el('td', [el('br'), el('br')])])]),
  ]) {
    assert.equal(serializeVisualNodesWithFidelity([node]).lossless, false);
  }
});

it('preserves code trailing newlines and list soft breaks through serialization and rendering', () => {
  const code = [{ type: 'element', tag: 'pre', children: [{ type: 'element', tag: 'code', children: [{ type: 'text', text: 'last\n' }] }] }];
  assert.equal(serializeVisualNodesToMarkdown(code), '```\nlast\n\n```');
  const list = [el('ul', [el('li', [txt('One'), el('br'), txt('Two')])])];
  const result = serializeVisualNodesWithFidelity(list);
  assert.equal(result.lossless, true);
  assert.match(markdownToVisualHtml(result.markdown), /<li>One<br>Two<\/li>/);
  assert.equal(inspectMarkdownVisualCompatibility(result.markdown).supported, true);
});

it('refuses block structures whose meaning would be discarded by serialization', () => {
  const cases = [
    [el('h1', [txt('One'), el('br'), txt('Two')])],
    [el('table', [el('tr', [el('td', [txt('One'), el('br'), txt('Two')])])])],
    [el('ul', [el('li', [el('p', [txt('One')]), el('p', [txt('Two')])])])],
    [el('pre', [el('code', [el('strong', [txt('Rich code')])])])],
    [el('figure', [el('img', [], { attrs: { src: 'https://example.com/image.png' } }), el('figcaption', [txt('Caption')])])],
    [el('table', [el('tr', [el('td', [txt('Merged')], { attrs: { colspan: '2' } })])])],
  ];
  const results = cases.map(nodes => serializeVisualNodesWithFidelity(nodes));
  assert.deepEqual(results.map(result => result.lossless), cases.map(() => false));
  assert.ok(results.every(result => result.reasons.length));
});

describe('Empty Visual Markdown table cells', () => {
  it('saves inserted rows and columns with placeholder breaks', () => {
    const markdown = '| Stage |  |\n| :---: | --- |\n|  |  |';
    const nodes = parseCodecHtml(markdownToVisualHtml(markdown));
    assert.deepEqual(serializeVisualNodesWithFidelity(nodes), {
      markdown,
      blocks: [markdown],
      lossless: true,
      reasons: [],
    });
  });

  it('still rejects a user-authored line break in a table cell', () => {
    const nodes = [el('table', [el('tbody', [
      el('tr', [el('th', 'Column')]),
      el('tr', [el('td', [txt('Before'), el('br'), txt('After')])]),
    ])])];
    const result = serializeVisualNodesWithFidelity(nodes);
    assert.equal(result.lossless, false);
    assert.ok(result.reasons.includes('line breaks or paragraphs in table cells'));
  });
});

it('retains combined button formatting represented as style on an inline tag', () => {
  const nodes = [el('p', [
    el('b', 'Bold'), txt(' '), el('i', 'combined', { style: { fontWeight: 'bold' } }),
    txt(' '), el('strike', 'deprecated '), el('code', 'command'),
  ])];
  assert.deepEqual(serializeVisualNodesWithFidelity(nodes), {
    markdown: '**Bold** ***combined*** ~~deprecated~~ ' + String.fromCharCode(96) + 'command' + String.fromCharCode(96),
    blocks: ['**Bold** ***combined*** ~~deprecated~~ ' + String.fromCharCode(96) + 'command' + String.fromCharCode(96)],
    lossless: true,
    reasons: [],
  });
});

describe('Escaped closing brackets and block-start safety', () => {
  it('escapes reference link definitions with multiple brackets', () => {
    const raw = '[ref]: https://example.com [extra] and [more]';
    const nodes = [el('p', [txt(raw)])];
    const md = serializeVisualNodesToMarkdown(nodes);
    assert.equal(md, '[ref\\]: https://example.com [extra\\] and [more\\]');
    const html = markdownToVisualHtml(md);
    assert.equal(_htmlToPlainText(html).trim(), raw);
  });

  it('escapes footnote definitions with multiple brackets and mixed escapes', () => {
    const raw = '[^note]: see [1] and [2] and \\] escaped';
    const nodes = [el('p', [txt(raw)])];
    const md = serializeVisualNodesToMarkdown(nodes);
    assert.equal(md, '[\\^note\\]: see [1\\] and [2\\] and \\\\\\] escaped');
    const html = markdownToVisualHtml(md);
    assert.equal(_htmlToPlainText(html).trim(), raw);
  });

  it('preserves even number of backslashes preceding bracket', () => {
    const raw = '[ref\\\\]: https://example.com';
    const nodes = [el('p', [txt(raw)])];
    const md = serializeVisualNodesToMarkdown(nodes);
    assert.equal(md, '[ref\\\\\\\\\\]: https://example.com');
    const html = markdownToVisualHtml(md);
    assert.equal(_htmlToPlainText(html).trim(), raw);
  });
});

describe('Table cell backslash and delimiter escaping', () => {
  it('round-trips literal backslashes, pipes, and delimiters in table cells', () => {
    const cases = [
      'C:\\foo\\bar',
      'trailing \\',
      'cell with | pipe',
      'slash and \\| pipe',
      '\\\\double-slash\\\\',
    ];
    for (const text of cases) {
      const tableNodes = [el('table', [el('tbody', [
        el('tr', [el('th', 'Header')]),
        el('tr', [el('td', [txt(text)])]),
      ])])];
      const md = serializeVisualNodesToMarkdown(tableNodes);
      const html = markdownToVisualHtml(md);
      assert.ok(html.includes('<table>'));
      assert.equal(_htmlToPlainText(html).replace('Header', '').trim(), text);
    }
  });

  it('verifies complete matrix of zero to four backslashes before pipes, boundaries, and formatting', () => {
    const matrix = [
      { name: 'zero slashes before pipe', text: 'foo | bar', expectedPlain: 'foo | bar' },
      { name: 'one slash before pipe', text: 'foo \\| bar', expectedPlain: 'foo \\| bar' },
      { name: 'two slashes before pipe', text: 'foo \\\\| bar', expectedPlain: 'foo \\\\| bar' },
      { name: 'three slashes before pipe', text: 'foo \\\\\\| bar', expectedPlain: 'foo \\\\\\| bar' },
      { name: 'four slashes before pipe', text: 'foo \\\\\\\\| bar', expectedPlain: 'foo \\\\\\\\| bar' },
      { name: 'literal pipe without backslash', text: 'just a | pipe', expectedPlain: 'just a | pipe' },
      { name: 'leading pipe', text: '| leading pipe', expectedPlain: '| leading pipe' },
      { name: 'trailing pipe', text: 'trailing pipe |', expectedPlain: 'trailing pipe |' },
      { name: 'multiple pipes in cell', text: 'one | two | three', expectedPlain: 'one | two | three' },
      { name: 'slash before cell boundary', text: 'end with slash\\', expectedPlain: 'end with slash\\' },
      { name: 'two slashes at end of cell', text: 'end with two\\\\', expectedPlain: 'end with two\\\\' },
      { name: 'three slashes at end of cell', text: 'end with three\\\\\\', expectedPlain: 'end with three\\\\\\' },
      { name: 'four slashes at end of cell', text: 'end with four\\\\\\\\', expectedPlain: 'end with four\\\\\\\\' },
      { name: 'empty cell', text: '', expectedPlain: '' },
    ];

    for (const entry of matrix) {
      const tableNodes = [el('table', [el('tbody', [
        el('tr', [el('th', 'Header')]),
        el('tr', [el('td', entry.text === '' ? [el('br')] : [txt(entry.text)])]),
      ])])];

      const md1 = serializeVisualNodesToMarkdown(tableNodes);
      const html1 = markdownToVisualHtml(md1);
      assert.ok(html1.includes('<table>'), `${entry.name}: must render table`);
      assert.equal(_htmlToPlainText(html1).replace('Header', '').trim(), entry.expectedPlain, `${entry.name}: plain text mismatch`);

      const parsedNodes2 = parseCodecHtml(html1);
      const md2 = serializeVisualNodesToMarkdown(parsedNodes2);
      const html2 = markdownToVisualHtml(md2);
      assert.equal(html1, html2, `${entry.name}: HTML must be idempotent across cycles`);
      assert.equal(md1, md2, `${entry.name}: Markdown must be idempotent across cycles`);
    }
  });

  it('verifies inline code, formatting, and adjacent delimiters in table cells', () => {
    const formattedCases = [
      {
        name: 'inline code containing pipe',
        nodes: [txt('code '), el('code', 'a | b'), txt(' inside')],
        expectedPlain: 'code a | b inside',
      },
      {
        name: 'escaped markdown formatting characters',
        nodes: [txt('escaped *not bold*')],
        expectedPlain: 'escaped *not bold*',
      },
      {
        name: 'adjacent emphasis delimiters',
        nodes: [el('strong', 'bold'), el('em', 'italic')],
        expectedPlain: 'bolditalic',
      },
      {
        name: 'cells containing both formatting and literal backslashes',
        nodes: [el('strong', 'bold'), txt(' and C:\\path')],
        expectedPlain: 'bold and C:\\path',
      },
    ];

    for (const entry of formattedCases) {
      const tableNodes = [el('table', [el('tbody', [
        el('tr', [el('th', 'Col')]),
        el('tr', [el('td', entry.nodes)]),
      ])])];

      const md1 = serializeVisualNodesToMarkdown(tableNodes);
      const html1 = markdownToVisualHtml(md1);
      assert.ok(html1.includes('<table>'));
      assert.equal(_htmlToPlainText(html1).replace('Col', '').trim(), entry.expectedPlain);

      const parsedNodes2 = parseCodecHtml(html1);
      const md2 = serializeVisualNodesToMarkdown(parsedNodes2);
      const html2 = markdownToVisualHtml(md2);
      assert.equal(html1, html2, `${entry.name}: HTML must be idempotent across cycles`);
      assert.equal(md1, md2, `${entry.name}: Markdown must be idempotent across cycles`);
    }
  });
});

describe('URL scheme security policies for links and images', () => {
  it('disallows dangerous schemes for links', () => {
    for (const bad of [
      'javascript:alert(1)',
      'vbscript:msgbox(1)',
      'data:text/html;base64,PHNjcmlwdD4=',
      'file:///etc/passwd',
    ]) {
      const md = `[click](${bad})`;
      const html = markdownToVisualHtml(md);
      assert.equal(html.includes(`href="${bad}"`), false, `Must not admit link with scheme: ${bad}`);
    }
  });

  it('disallows dangerous and non-image schemes for images', () => {
    for (const bad of [
      'javascript:alert(1)',
      'vbscript:msgbox(1)',
      'data:image/svg+xml;base64,PHN2Zy8+',
      'file:///etc/shadow',
      'mailto:test@example.com',
      'tel:1234567890',
    ]) {
      const md = `![img](${bad})`;
      const html = markdownToVisualHtml(md);
      assert.equal(html.includes(`src="${bad}"`), false, `Must not admit image with scheme: ${bad}`);
    }
  });

  it('admits safe HTTP and HTTPS URLs for links and images', () => {
    const linkMd = '[safe](https://example.com/docs)';
    const imgMd = '![safe](/api/upload/image123.png)';
    assert.ok(markdownToVisualHtml(linkMd).includes('href="https://example.com/docs"'));
    assert.ok(markdownToVisualHtml(imgMd).includes('src="/api/upload/image123.png"'));
  });
});
