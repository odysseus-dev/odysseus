// What the Visual editor serializes must mean the same thing in the application's
// real Markdown Preview (static/js/markdown.js, the renderer Documents use).
//
// Comparing HTML strings is not enough: the two renderers emit different markup for
// the same document. Each rendering is reduced to its user-visible semantics (text,
// inline formatting, link targets, code spans, block and table structure) first.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { markdownToVisualHtml, serializeVisualNodesToMarkdown } from '../frontend/src/features/documents/domain/markdownVisualCodec.js';
import { MARKDOWN_DOCUMENT_RENDER_OPTIONS } from '../frontend/src/features/documents/domain/documentKinds.js';
import { decodeHtmlEntities, parseHtmlTokens } from '../frontend/src/features/documents/domain/sourceFidelity.js';
import { parseCodecHtml } from './markdownCodecHtml.mjs';
import { loadMarkdown } from './streaming/markdownHarness.mjs';

const markdown = await loadMarkdown();

const MARKERS = { strong: '**', b: '**', em: '*', i: '*', del: '~~', s: '~~', code: '`' };
const BLOCK_TAGS = new Set(['p', 'div', 'ul', 'ol', 'li', 'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']);

/** Reduce rendered HTML to a comparable description of what a reader sees. */
function semantics(html) {
  let out = '';
  let pre = 0;
  let cell = 0;
  for (const token of parseHtmlTokens(String(html))) {
    if (token.type === 'text') {
      const text = decodeHtmlEntities(token.text);
      out += pre ? text : text.replace(/\s+/g, ' ');
      continue;
    }
    const open = token.type === 'open';
    if (MARKERS[token.tag]) out += MARKERS[token.tag];
    else if (token.tag === 'a') out += open ? '[' : `](${token.attrs?.href ?? ''})`;
    else if (token.tag === 'br') { if (open && !cell) out += '\n'; } // a lone <br> only holds an empty cell open
    else if (token.tag === 'table' || token.tag === 'tr') out += open ? `\n⟦${token.tag}⟧` : '';
    else if (token.tag === 'th' || token.tag === 'td') { cell += open ? 1 : -1; out += open ? `⟦${token.tag}⟧` : ''; }
    else if (/^h[1-6]$/.test(token.tag)) out += open ? `\n⟦${token.tag}⟧` : '\n';
    else if (token.tag === 'li') out += open ? '\n⟦li⟧' : '';
    else if (token.tag === 'pre') { pre += open ? 1 : -1; out += open ? '\n⟦pre⟧' : '\n'; }
    else if (BLOCK_TAGS.has(token.tag)) out += '\n';
  }
  return out.replace(/[ \t]*\n[ \t]*/g, '\n').replace(/\n{2,}/g, '\n').trim();
}

const table = (head, ...rows) => `<table><thead><tr>${head.map(cell => `<th>${cell}</th>`).join('')}</tr></thead><tbody>${
  rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table>`;

// Visual documents, as the editor's DOM holds them after typing.
const VISUAL_DOCUMENTS = {
  'literal asterisks': '<p>2*3*4</p>',
  'windows path': '<p>C:\\temp\\new\\file.txt</p>',
  'unc path': '<p>\\\\server\\share\\dir</p>',
  'repeated backslashes': '<p>a\\\\b and \\\\\\\\ and trailing\\</p>',
  'escaped underscores': '<p>snake_case_name and _x_ and __dunder__</p>',
  'escaped brackets': '<p>[not a link] and [a](b) and ![img](x)</p>',
  'formatting next to escaped punctuation': '<p><strong>bold</strong>*star and <em>it</em>_under and <strong>a*b</strong> <em>c_d</em></p>',
  'block-start characters': '<p>1. not a list</p><p># not a heading</p><p>&gt; not a quote</p><p>- not a bullet</p>',
  'tildes and backticks': '<p>~~not struck~~ and `tick` and ``double``</p>',
  'entities as text': '<p>&lt;b&gt; &amp;amp; &amp;</p>',
  'code span with backslash': '<p><code>C:\\temp</code> then <code>a\\</code> then <code>x*y</code></p>',
  'heading with punctuation': '<h2>a*b_c [d]</h2>',
  'list item with punctuation': '<ul><li>a*b</li><li>C:\\dir</li></ul>',
  'link with punctuation': '<p><a href="https://example.com/a_b?x=1">label [x] *y*</a></p>',
  'alerts': ['note', 'tip', 'important', 'warning', 'caution'].map(kind => `<blockquote class="markdown-alert markdown-alert-${kind}" data-alert="${kind.toUpperCase()}"><p><span class="markdown-alert-label" contenteditable="false" data-alert-label="${kind.toUpperCase()}">${kind[0].toUpperCase()}${kind.slice(1)}</span></p><p>About ${kind}.</p></blockquote>`).join(''),
  'table with pipe in cell': table(['A', 'B'], ['x|y', '2']),
  'table with code and pipe': table(['A', 'B'], ['<code>x</code>|y', '2']),
  'table with escapes': table(['a*b', 'C:\\x'], ['_u_', '[v]']),
  'table with empty cell': table(['A', 'B', 'C'], ['1', '', '3']),
  'table with only a pipe': table(['A'], ['|']),
};

describe('Visual serialization and the application Preview agree', () => {
  for (const [name, html] of Object.entries(VISUAL_DOCUMENTS)) {
    it(name, () => {
      const source = serializeVisualNodesToMarkdown(parseCodecHtml(html));
      const intended = semantics(html);
      const visual = semantics(markdownToVisualHtml(source));
      const preview = semantics(markdown.mdToHtml(source, MARKDOWN_DOCUMENT_RENDER_OPTIONS));
      assert.equal(visual, intended, `Visual reopens what was typed\nsource: ${JSON.stringify(source)}`);
      assert.equal(preview, intended, `Preview shows what was typed\nsource: ${JSON.stringify(source)}`);
    });
  }

  it('shows no stray backslashes or extra columns for the maintainer examples', () => {
    const star = markdown.mdToHtml(serializeVisualNodesToMarkdown(parseCodecHtml('<p>2*3*4</p>')), MARKDOWN_DOCUMENT_RENDER_OPTIONS);
    assert.equal(star, '<p>2*3*4</p>');
    const path = markdown.mdToHtml(serializeVisualNodesToMarkdown(parseCodecHtml('<p>C:\\temp</p>')), MARKDOWN_DOCUMENT_RENDER_OPTIONS);
    assert.equal(path, '<p>C:\\temp</p>');
    const cells = markdown.mdToHtml('| A | B |\n| --- | --- |\n| x\\|y | 2 |', MARKDOWN_DOCUMENT_RENDER_OPTIONS);
    assert.equal((cells.match(/<td\b/g) || []).length, 2);
    assert.match(cells, />x\|y</);
  });

  it('keeps literal escape-marker text in prose, code spans and code fences', () => {
    const marker = '___ESC_42___';
    const source = `Record ${marker} and \\*\n\n\`${marker}\`\n\n~~~text\n${marker}\n~~~`;
    const preview = markdown.mdToHtml(source, MARKDOWN_DOCUMENT_RENDER_OPTIONS);
    assert.match(preview, /Record ___ESC_42___ and \*/);
    assert.match(preview, /<code>___ESC_42___<\/code>/);
    assert.match(preview, /<code[^>]+>___ESC_42___\n<\/code>/);
    assert.match(preview, /data-code="___ESC_42___\n"/);
  });

  it('leaves TeX escapes in dollar-delimited math for KaTeX', () => {
    const katex = window.katex;
    const formula = String.raw`\{x\}`;
    try {
      window.katex = globalThis.katex = { renderToString: (source) => {
        assert.equal(source, formula);
        return '<span class="katex">math</span>';
      } };
      const preview = markdown.mdToHtml(`$${formula}$`, MARKDOWN_DOCUMENT_RENDER_OPTIONS);
      assert.match(preview, /class="katex"/);
    } finally {
      window.katex = katex;
      delete globalThis.katex;
    }
  });
});

describe('Chat rendering is unchanged by document escape handling', () => {
  // Chat does not pass `commonmark`. These stay exactly as the renderer has
  // always produced them, including math delimiters that begin with a backslash.
  const chat = (source) => markdown.mdToHtml(source);
  it('keeps backslashes visible and escaped table pipes splitting', () => {
    assert.equal(chat('C:\\temp'), '<p>C:\\temp</p>');
    assert.equal((chat('| A | B |\n| --- | --- |\n| x\\|y | 2 |').match(/<td\b/g) || []).length, 3);
  });
  it('still renders \\( \\) and \\[ \\] math through KaTeX placeholders', () => {
    assert.doesNotMatch(chat('Inline \\(x+1\\) here'), /\\\(/);
    assert.doesNotMatch(chat('\\[x^2\\]'), /\\\[/);
  });
});
