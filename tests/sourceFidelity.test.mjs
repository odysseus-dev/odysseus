// tests/sourceFidelity.test.mjs
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  alignText,
  applyLocalizedEdits,
  compareSemanticHtml,
  computeSourceRevision,
  decodeHtmlEntities,
  matches,
  MAX_EDIT_DISTANCE,
  normalizeSemanticHtml,
  parseAttributes,
  parseHtmlTokens,
  patchSource,
  preserveBlockSource,
} from '../frontend/src/features/documents/domain/sourceFidelity.js';
import {
  detectLineEnding,
  extractBlockSegments,
  isBlank,
  isTableLine,
  isTableSeparatorLine,
  joinSegments,
  lineContent,
  lineEnding,
  parseListItemLine,
  segmentMarkdown,
  splitLinesExact,
} from '../frontend/src/features/documents/domain/sourceSegmentation.js';
import {
  inspectMarkdownVisualCompatibility,
  markdownToVisualHtml,
} from '../frontend/src/features/documents/domain/markdownVisualCodec.js';

describe('Shared Markdown Source-Fidelity Foundation', () => {

  describe('1. Exact source segmentation and round-trip invariants', () => {
    const invariantCorpus = {
      'LF line endings': 'First paragraph.\n\nSecond paragraph.\n',
      'CRLF line endings': 'First paragraph.\r\n\r\nSecond paragraph.\r\n',
      'Mixed line endings': 'Heading 1\r\nParagraph 1\n\r\nParagraph 2\n',
      'Lone CR line endings': 'First paragraph.\rSecond paragraph.\r',
      'Leading blank lines': '\r\n\n  \n# Title\n',
      'Trailing blank lines': '# Title\n\n\n\r\n',
      'Multiple consecutive blank lines': 'Block 1\n\n\n\n\nBlock 2\n',
      'Trailing spaces': 'Block 1  \nBlock 2   \n',
      'Tabs': '\t* Tabbed item\n\t  nested line\n',
      'UTF-8 BOM': '\uFEFF# Document with BOM\n\nBody paragraph.\n',
      'Unicode and emojis': '# 日本語 🚀\n\nCafé au lait (déjà vu ñoño) ✨\n',
      'Escaped Markdown': 'Here is \\*not bold\\* and \\[not math\\] and \\\\backslash.\n',
      'Lists (tight and nested)': '* Item 1\n* Item 2\n  * Nested 2a\n* Item 3\n',
      'Lists (+ bullet marker)': '+ Item 1\n+ Item 2\n',
      'Ordered lists starting at 42': '42. Item 42\n43. Item 43\n44. Item 44\n',
      'Ordered lists starting at 0': '0. Zero indexed\n1. One\n',
      'Loose lists with blank lines and paragraphs': '* Item 1\n\n  Item 1 paragraph 2\n* Item 2\n',
      'Lazy list continuations': '* Item 1\nlazy continuation of item 1\n* Item 2\n',
      'Checklists': '- [ ] Todo item\n- [x] Done item\n',
      'Tables with alignment': '| Name | Value |\n|:---|---:|\n| Alpha | 123 |\n| Beta | 456 |\n',
      'Headings ATX': '# H1\n## H2\n### H3\n#### H4\n##### H5\n###### H6\n',
      'Headings Setext H1': 'Setext Heading\n==============\n',
      'Headings Setext H2 multiline': 'Setext Line 1\nSetext Line 2\n-------------\n',
      'Code fences backticks': '```javascript\nconsole.log(1);\n```\n',
      'Code fences tildes': '~~~python\nprint(2)\n~~~\n',
      'Code fences containing blank lines': '```javascript\nfunction test() {\n\n  return 42;\n}\n```\n',
      'Indented code blocks': '    const a = 1;\n    const b = 2;\n',
      'Blockquotes simple': '> Quote line 1\n> Quote line 2\n',
      'Blockquotes with lazy continuation': '> Quote line 1\nlazy continuation line\n> Quote line 3\n',
      'Nested blockquotes': '> > nested quote\n> > line 2\n> outer quote\n',
      'Adjacent blocks without blank lines': '# Heading\nParagraph 1\n> Quote\n# Heading 2\n```\ncode\n```\nParagraph 2\n',
      'Single line without newline': 'Simple bare line',
      'Empty document': '',
      'Whitespace-only document': '  \n\r\n\t\n',
    };

    for (const [name, source] of Object.entries(invariantCorpus)) {
      it(`preserves exact identity for: ${name}`, () => {
        const segments = segmentMarkdown(source);
        const reconstructed = joinSegments(segments);
        assert.equal(reconstructed, source);
      });
    }

    it('splitLinesExact accounts for every character and line ending', () => {
      const src = 'a\r\nb\nc\r';
      const lines = splitLinesExact(src);
      assert.deepEqual(lines, ['a\r\n', 'b\n', 'c\r']);
      assert.equal(lines.join(''), src);
    });

    it('detectLineEnding identifies predominant line ending', () => {
      assert.equal(detectLineEnding('a\r\nb\r\nc\r\n'), '\r\n');
      assert.equal(detectLineEnding('a\nb\nc\n'), '\n');
      assert.equal(detectLineEnding('a\r\nb\nc\n'), '\n');
      assert.equal(detectLineEnding(''), '\n');
    });

    it('extractBlockSegments provides accurate metadata, ranges, and blockIds', () => {
      const src = '# H1\n\nP1\n\nP2\n';
      const segs = segmentMarkdown(src);
      const blocks = extractBlockSegments(segs);
      assert.equal(blocks.length, 3);
      assert.equal(blocks[0].raw, '# H1');
      assert.equal(blocks[0].index, 0);
      assert.equal(blocks[0].startOffset, 0);
      assert.equal(blocks[0].endOffset, 4);
      assert.equal(blocks[0].blockType, 'heading');
      assert.ok(blocks[0].blockId);

      assert.equal(blocks[1].raw, 'P1');
      assert.equal(blocks[1].index, 1);
      assert.equal(blocks[1].startOffset, 6);
      assert.equal(blocks[1].endOffset, 8);
      assert.equal(blocks[1].blockType, 'paragraph');

      assert.equal(blocks[2].raw, 'P2');
      assert.equal(blocks[2].index, 2);
      assert.equal(blocks[2].startOffset, 10);
      assert.equal(blocks[2].endOffset, 12);
    });
  });

  describe('2. Localized edit preservation and block isolation', () => {
    const fixtureDocument = `# Original heading

* First item
* Second item

|Name|Value|
|:---|---:|
|Alpha|123|

Original paragraph.

~~~javascript
console.log("unchanged");
~~~
`;

    it('edits only paragraph block 3 while keeping all other blocks byte-for-byte identical', () => {
      const result = applyLocalizedEdits(fixtureDocument, {
        3: {
          baseline: 'Original paragraph.',
          edited: 'Updated paragraph.',
        },
      }, {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.equal(result.reason, null);

      const expected = `# Original heading

* First item
* Second item

|Name|Value|
|:---|---:|
|Alpha|123|

Updated paragraph.

~~~javascript
console.log("unchanged");
~~~
`;
      assert.equal(result.markdown, expected);

      const originalSegments = segmentMarkdown(fixtureDocument);
      const updatedSegments = segmentMarkdown(result.markdown);
      assert.equal(updatedSegments[0].raw, originalSegments[0].raw); // Heading
      assert.equal(updatedSegments[1].raw, originalSegments[1].raw); // Separator
      assert.equal(updatedSegments[2].raw, originalSegments[2].raw); // List
      assert.equal(updatedSegments[3].raw, originalSegments[3].raw); // Separator
      assert.equal(updatedSegments[4].raw, originalSegments[4].raw); // Table
      assert.equal(updatedSegments[5].raw, originalSegments[5].raw); // Separator
      assert.equal(updatedSegments[6].raw, 'Updated paragraph.');    // Updated block
      assert.equal(updatedSegments[7].raw, originalSegments[7].raw); // Separator
      assert.equal(updatedSegments[8].raw, originalSegments[8].raw); // Code fence
    });

    it('edits list block 1 preserving non-canonical * markers', () => {
      const result = applyLocalizedEdits(fixtureDocument, {
        1: {
          baseline: '- First item\n- Second item',
          edited: '- First item\n- Second updated item',
        },
      }, {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.ok(result.markdown.includes('* First item\n* Second updated item'));
      assert.ok(result.markdown.includes('# Original heading'));
      assert.ok(result.markdown.includes('|Alpha|123|'));
      assert.ok(result.markdown.includes('~~~javascript'));
    });

    it('edits table block 2 preserving custom pipe spacing and alignment syntax', () => {
      const result = applyLocalizedEdits(fixtureDocument, {
        2: {
          baseline: '| Name | Value |\n|:---|---:|\n| Alpha | 123 |',
          edited: '| Name | Value |\n|:---|---:|\n| Beta | 123 |',
        },
      }, {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.ok(result.markdown.includes('|Beta|123|'));
      assert.ok(result.markdown.includes('|Name|Value|\n|:---|---:|'));
      assert.ok(result.markdown.includes('Original paragraph.'));
    });

    it('edits code fence block 4 preserving ~~~ delimiter syntax', () => {
      const result = applyLocalizedEdits(fixtureDocument, {
        4: {
          baseline: '```javascript\nconsole.log("unchanged");\n```',
          edited: '```javascript\nconsole.log("hello world");\n```',
        },
      }, {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.ok(result.markdown.includes('~~~javascript\nconsole.log("hello world");\n~~~'));
      assert.ok(result.markdown.includes('Original paragraph.'));
    });

    it('supports block deletion while leaving surrounding blocks intact', () => {
      const result = applyLocalizedEdits(fixtureDocument, [
        { op: 'delete', index: 3 }, // Delete paragraph
      ], {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.ok(!result.markdown.includes('Original paragraph.'));
      assert.ok(result.markdown.includes('# Original heading'));
      assert.ok(result.markdown.includes('* First item'));
      assert.ok(result.markdown.includes('|Name|Value|'));
      assert.ok(result.markdown.includes('~~~javascript'));
    });

    it('supports block insertion between existing blocks without boundary collision', () => {
      const result = applyLocalizedEdits(fixtureDocument, [
        { op: 'insert', afterIndex: 2, text: 'Newly inserted block.' },
      ], {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.ok(result.markdown.includes('Newly inserted block.'));
      assert.ok(result.markdown.includes('|Name|Value|'));
      assert.ok(result.markdown.includes('Original paragraph.'));
    });

    it('C3: correctly preserves insertion boundaries when inserting into adjacent blocks without blank lines', () => {
      const adjacentDoc = '# Heading\nbody';
      const result = applyLocalizedEdits(adjacentDoc, [
        { op: 'insert', afterIndex: 0, text: 'Middle' },
      ], {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.ok(result.markdown.includes('# Heading'));
      assert.ok(result.markdown.includes('Middle'));
      assert.ok(result.markdown.includes('body'));

      // Crucial: Middle and body must NOT be merged into Middlebody!
      const verifyBlocks = extractBlockSegments(segmentMarkdown(result.markdown));
      assert.equal(verifyBlocks.length, 3);
      assert.equal(verifyBlocks[0].raw, '# Heading');
      assert.equal(verifyBlocks[1].raw, 'Middle');
      assert.equal(verifyBlocks[2].raw, 'body');
    });

    it('edits adjacent blocks without blank lines cleanly', () => {
      const adjacentDoc = '# Heading\nParagraph 1\nParagraph 2\n';
      const result = applyLocalizedEdits(adjacentDoc, {
        0: { baseline: '# Heading', edited: '# New Heading' },
      }, {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.equal(result.markdown, '# New Heading\nParagraph 1\nParagraph 2\n');
    });
  });

  describe('3. Myers difference and 3-way source patching', () => {
    it('alignText handles identical strings in O(N)', () => {
      const str = 'This is a test string.';
      const map = alignText(str, str);
      assert.ok(map);
      assert.equal(map.length, str.length);
      for (let i = 0; i < str.length; i++) {
        assert.equal(map[i], i);
      }
    });

    it('matches finds longest common subsequence of index pairs', () => {
      const a = 'kitten';
      const b = 'sitting';
      const pairs = matches(a, b, 100);
      assert.ok(pairs);
      assert.ok(pairs.length > 0);
      for (const [x, y] of pairs) {
        assert.equal(a[x], b[y]);
      }
    });

    it('patchSource merges additions without rewriting unmodified parts', () => {
      const src = 'Hello brave new world';
      const base = 'Hello brave new world';
      const edit = 'Hello wonderful new world';
      const patched = patchSource(src, base, edit);
      assert.equal(patched, 'Hello wonderful new world');
    });

    it('patchSource merges deletions cleanly', () => {
      const src = 'The quick brown fox jumps';
      const base = 'The quick brown fox jumps';
      const edit = 'The brown fox jumps';
      const patched = patchSource(src, base, edit);
      assert.equal(patched, 'The brown fox jumps');
    });

    it('patchSource preserves surrounding markdown syntax in lists and tables', () => {
      const src = '* Alpha\n* Beta\n* Gamma';
      const base = '- Alpha\n- Beta\n- Gamma';
      const edit = '- Alpha\n- Beta (updated)\n- Gamma';
      const patched = patchSource(src, base, edit);
      assert.equal(patched, '* Alpha\n* Beta (updated)\n* Gamma');
    });
  });

  describe('4. Explicit source-fidelity contract outcomes (C1 & C2)', () => {
    it('returns preserved when edit retains original formatting and semantics', () => {
      const res = preserveBlockSource({
        source: '* Item 1\n* Item 2',
        baseline: '- Item 1\n- Item 2',
        edited: '- Item 1\n- Item 2 modified',
        render: markdownToVisualHtml,
      });

      assert.equal(res.status, 'preserved');
      assert.equal(res.markdown, '* Item 1\n* Item 2 modified');
      assert.equal(res.reason, null);
    });

    it('returns preserved for identity edit without requiring renderer', () => {
      const res = preserveBlockSource({
        source: 'Original text.',
        baseline: 'Original text.',
        edited: 'Original text.',
        render: null,
      });

      assert.equal(res.status, 'preserved');
      assert.equal(res.markdown, 'Original text.');
      assert.equal(res.reason, null);
    });

    it('C1: returns unsupported and preserves source when render is missing on modified block', () => {
      const res = preserveBlockSource({
        source: 'Original text.',
        baseline: 'Original text.',
        edited: 'Modified text.',
        render: null, // missing renderer
      });

      assert.equal(res.status, 'unsupported');
      assert.equal(res.markdown, 'Original text.');
      assert.equal(res.reason, 'missing-semantic-validator');
      assert.equal(res.draft, 'Modified text.');
    });

    it('C1: returns unsupported and preserves source when render throws an error', () => {
      const res = preserveBlockSource({
        source: 'Original text.',
        baseline: 'Original text.',
        edited: 'Modified text.',
        render: () => { throw new Error('Renderer crashed'); },
      });

      assert.equal(res.status, 'unsupported');
      assert.equal(res.markdown, 'Original text.');
      assert.equal(res.reason, 'render-error');
      assert.equal(res.draft, 'Modified text.');
    });

    it('C2: rejects stale block reference where baseline mismatches target block without mutating', () => {
      const src = '# Heading\n\nParagraph';
      const result = applyLocalizedEdits(src, {
        0: { baseline: 'Paragraph', edited: 'Updated' },
      }, {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'unsupported');
      assert.ok(result.reason.includes('baseline mismatch for block 0'));
      // The heading MUST NOT be modified!
      assert.equal(result.markdown, src);
    });

    it('C2: rejects mismatched range parameter safely', () => {
      const src = '# Heading\n\nParagraph';
      const result = applyLocalizedEdits(src, [
        { op: 'replace', index: 0, baseline: '# Heading', edited: '# New', range: [99, 105] },
      ], {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'unsupported');
      assert.ok(result.reason.includes('range mismatch for block 0'));
      assert.equal(result.markdown, src);
    });

    it('C2: rejects mismatched expectedRevision safely', () => {
      const src = '# Heading\n\nParagraph';
      const result = applyLocalizedEdits(src, {
        0: { baseline: '# Heading', edited: '# New' },
      }, {
        expectedRevision: 'stale-rev-123',
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'unsupported');
      assert.equal(result.reason, 'stale-source-revision');
      assert.equal(result.markdown, src);
    });

    it('C2: accepts matching expectedRevision and range', () => {
      const src = '# Heading\n\nParagraph';
      const rev = computeSourceRevision(src);
      const blocks = extractBlockSegments(segmentMarkdown(src));

      const result = applyLocalizedEdits(src, [
        {
          op: 'replace',
          index: 0,
          baseline: '# Heading',
          edited: '# New Heading',
          range: [blocks[0].startOffset, blocks[0].endOffset],
          blockId: blocks[0].blockId,
        },
      ], {
        expectedRevision: rev,
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.ok(result.markdown.startsWith('# New Heading'));
    });

    it('returns canonical-fallback when edit distance exceeds MAX_EDIT_DISTANCE', () => {
      const source = '* ' + 'a'.repeat(2000);
      const baseline = '- ' + 'a'.repeat(2000);
      const edited = '- ' + 'b'.repeat(2000);

      const res = preserveBlockSource({
        source,
        baseline,
        edited,
        render: markdownToVisualHtml,
        maxEditDistance: 50,
      });

      assert.equal(res.status, 'canonical-fallback');
      assert.equal(res.reason, 'diff-limit-exceeded');
      assert.equal(res.markdown, edited);
    });

    it('returns unsupported when inspector rejects edited construct', () => {
      const res = preserveBlockSource({
        source: 'Valid paragraph.',
        baseline: 'Valid paragraph.',
        edited: '```mermaid\ngraph TD; A-->B\n```',
        render: markdownToVisualHtml,
        inspectCompatibility: inspectMarkdownVisualCompatibility,
      });

      assert.equal(res.status, 'unsupported');
      assert.equal(res.markdown, 'Valid paragraph.');
      assert.ok(res.reason.includes('Mermaid diagram'));
    });

    it('rejects ambiguous block mappings safely without mutating source', () => {
      const src = '# H1\n\nP1\n';
      const res1 = applyLocalizedEdits(src, {
        99: { baseline: 'P1', edited: 'P2' },
      }, { render: markdownToVisualHtml });
      assert.equal(res1.status, 'unsupported');
      assert.ok(res1.reason.includes('ambiguous-block-mapping'));

      const res2 = applyLocalizedEdits(src, [
        { op: 'replace', index: 0, edited: '# New 1' },
        { op: 'replace', index: 0, edited: '# New 2' },
      ], { render: markdownToVisualHtml });
      assert.equal(res2.status, 'unsupported');
      assert.ok(res2.reason.includes('ambiguous-block-mapping: duplicate index'));
    });
  });

  describe('5. Semantic equivalence verification boundary (C5)', () => {
    const semanticEquivalenceCases = [
      ['Bold tags', '**bold text**', '__bold text__'],
      ['Italic tags', '*italic text*', '_italic text_'],
      ['Nested bold and italic', '***bold and italic***', '**_bold and italic_**'],
      ['Adjacent styles', '**bold** *italic*', '**bold** _italic_'],
      ['Strikethrough', '~~deleted~~', '~~deleted~~'],
      ['Links', '[Example](https://example.com)', '[Example](https://example.com)'],
      ['Images', '![Alt text](https://example.com/a.png)', '![Alt text](https://example.com/a.png)'],
      ['Inline code spaces', '`var x = 1;`', '`var x = 1;`'],
      ['Escaped delimiters', '2 \\* 3 = 6', '2 \\* 3 = 6'],
      ['Lists bullet types', '* Item 1\n* Item 2', '- Item 1\n- Item 2'],
      ['Tables column spacing', '| A | B |\n|:---|---:|\n| 1 | 2 |', '|A|B|\n|:---|---:|\n|1|2|'],
      ['Blockquotes', '> Quote line 1\n> Quote line 2', '> Quote line 1\n> Quote line 2'],
      ['Code fence syntax', '```js\nconsole.log(1);\n```', '~~~js\nconsole.log(1);\n~~~'],
    ];

    for (const [name, a, b] of semanticEquivalenceCases) {
      it(`verifies semantic visual equivalence for: ${name}`, () => {
        const hA = markdownToVisualHtml(a);
        const hB = markdownToVisualHtml(b);
        assert.ok(compareSemanticHtml(hA, hB), `Failed for ${name}: ${hA} vs ${hB}`);
      });
    }

    it('rejects semantically divergent content', () => {
      const h1 = markdownToVisualHtml('**bold text**');
      const h2 = markdownToVisualHtml('*italic text*');
      assert.equal(compareSemanticHtml(h1, h2), false);

      const l1 = markdownToVisualHtml('[Link 1](https://example.com/a)');
      const l2 = markdownToVisualHtml('[Link 1](https://example.com/b)');
      assert.equal(compareSemanticHtml(l1, l2), false);

      const c1 = markdownToVisualHtml('`codeA`');
      const c2 = markdownToVisualHtml('`codeB`');
      assert.equal(compareSemanticHtml(c1, c2), false);
    });

    it('C5: normalizeSemanticHtml preserves exact whitespace inside nested <pre><code> elements', () => {
      const htmlCode = '<pre><code class="language-js">function test() {\n    const x =   42;\n    return x;\n}</code></pre>';
      const normalized = normalizeSemanticHtml(htmlCode);
      assert.ok(normalized.includes('const x =   42;'));
      assert.ok(normalized.includes('\n    return x;'));
    });

    it('C5: normalizes unordered attributes and style declarations deterministically', () => {
      const html1 = '<div style="color: red; font-size: 14px;" class="foo bar" title="Greeting">Text</div>';
      const html2 = '<div title="Greeting" class="bar foo" style="font-size: 14px; color: red;">Text</div>';
      assert.equal(normalizeSemanticHtml(html1), normalizeSemanticHtml(html2));
    });

    it('C5: decodes entities and collapses insignificant whitespace outside code', () => {
      const html1 = '<p>Hello&nbsp;&nbsp;&nbsp;world&amp;friends</p>';
      const html2 = '<p>Hello world&friends</p>';
      assert.equal(normalizeSemanticHtml(html1), normalizeSemanticHtml(html2));
    });
  });

  describe('6. Edge cases, Unicode, repeated text, and large documents (C6)', () => {
    it('handles documents with repeated identical paragraphs', () => {
      const repeatedDoc = 'Repeated paragraph.\n\nRepeated paragraph.\n\nRepeated paragraph.\n';
      const result = applyLocalizedEdits(repeatedDoc, {
        1: { baseline: 'Repeated paragraph.', edited: 'Edited middle paragraph.' },
      }, {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      const expected = 'Repeated paragraph.\n\nEdited middle paragraph.\n\nRepeated paragraph.\n';
      assert.equal(result.markdown, expected);
    });

    it('handles full Unicode, surrogate pairs, and multi-byte scripts', () => {
      const unicodeDoc = '# 🚀 Launch plan\n\n- Task 1: 漢字とひらがな\n- Task 2: Éléphant café\n';
      const result = applyLocalizedEdits(unicodeDoc, {
        1: {
          baseline: '- Task 1: 漢字とひらがな\n- Task 2: Éléphant café',
          edited: '- Task 1: 漢字とひらがな (完了)\n- Task 2: Éléphant café',
        },
      }, {
        render: markdownToVisualHtml,
      });

      assert.equal(result.status, 'preserved');
      assert.ok(result.markdown.includes('漢字とひらがな (完了)'));
      assert.ok(result.markdown.includes('🚀 Launch plan'));
    });

    it('handles large documents efficiently with 1,000 blocks (>150 KB)', () => {
      const blocks = [];
      for (let i = 0; i < 1000; i++) {
        blocks.push(`## Section ${i}\n\nParagraph text for section ${i} containing some descriptive prose.`);
      }
      const largeDoc = blocks.join('\n\n') + '\n';
      assert.ok(largeDoc.length > 60000);

      const t0 = performance.now();
      const result = applyLocalizedEdits(largeDoc, {
        501: {
          baseline: 'Paragraph text for section 250 containing some descriptive prose.',
          edited: 'Updated prose text for section 250.',
        },
      }, {
        render: markdownToVisualHtml,
      });
      const elapsed = performance.now() - t0;

      assert.equal(result.status, 'preserved');
      assert.ok(result.markdown.includes('Updated prose text for section 250.'));
      assert.ok(result.markdown.includes('## Section 0'));
      assert.ok(result.markdown.includes('## Section 999'));
      assert.ok(elapsed < 200, `Localized edit on 1,000 blocks took ${elapsed.toFixed(2)}ms (expected < 200ms)`);
    });

    it('produces deterministic output across multiple repeated runs', () => {
      const doc = '# Title\n\n* List A\n* List B\n\nParagraph 1.\n';
      const run1 = applyLocalizedEdits(doc, { 2: { baseline: 'Paragraph 1.', edited: 'Paragraph 1 updated.' } }, { render: markdownToVisualHtml });
      const run2 = applyLocalizedEdits(doc, { 2: { baseline: 'Paragraph 1.', edited: 'Paragraph 1 updated.' } }, { render: markdownToVisualHtml });
      const run3 = applyLocalizedEdits(doc, { 2: { baseline: 'Paragraph 1.', edited: 'Paragraph 1 updated.' } }, { render: markdownToVisualHtml });

      assert.equal(run1.markdown, run2.markdown);
      assert.equal(run2.markdown, run3.markdown);
    });
  });
});

// Independent audit regressions: verify meaning and identity, not text presence.
describe('Independent source-fidelity regressions', () => {
  for (const afterIndex of [-1, 0, 1]) {
    it(`keeps ordered multi-block insertions after ${afterIndex}`, () => {
      const source = 'A\n\nZ';
      const result = applyLocalizedEdits(source, [
        { op: 'insert', afterIndex, text: 'B' },
        { op: 'insert', afterIndex, text: 'C' },
      ], { render: markdownToVisualHtml });
      const blocks = ['A', 'Z'];
      blocks.splice(afterIndex + 1, 0, 'B', 'C');
      assert.notEqual(result.status, 'unsupported');
      assert.equal(markdownToVisualHtml(result.markdown), markdownToVisualHtml(blocks.join('\n\n')));
    });
  }

  it('segments consecutive nested quote lines as one block', () => {
    const source = '> > nested\r\n> > next\n> outer';
    assert.deepEqual(extractBlockSegments(segmentMarkdown(source)).map(b => b.raw), [source]);
  });

  for (const render of [() => undefined, () => null, () => '', () => { throw Error('renderer failed'); }]) {
    it(`fails closed for invalid rendering: ${String(render)}`, () => {
      const block = preserveBlockSource({ source: '*A*', baseline: '_A_', edited: '_B_', render });
      assert.equal(block.status, 'unsupported');
      assert.equal(block.markdown, '*A*');
      for (const operation of [{ op: 'insert', afterIndex: 0, text: 'B' }, { op: 'delete', index: 0 }]) {
        const result = applyLocalizedEdits('A\n\nZ', [operation], { render });
        assert.equal(result.status, 'unsupported');
        assert.equal(result.markdown, 'A\n\nZ');
      }
    });
  }

  it('fails closed when the semantic comparator throws', () => {
    const result = preserveBlockSource({
      source: '*A*', baseline: '_A_', edited: '_B_', render: markdownToVisualHtml,
      compareSemantics() { throw Error('comparison failed'); },
    });
    assert.equal(result.status, 'unsupported');
    assert.equal(result.markdown, '*A*');
  });

  it('does not confuse literal encoded markup with actual formatting', () => {
    assert.equal(compareSemanticHtml('<p>&lt;strong&gt;A&lt;/strong&gt;</p>', '<p><strong>A</strong></p>'), false);
    assert.equal(compareSemanticHtml('<p>&amp;lt;</p>', '<p>&lt;</p>'), false);
  });

  it('preserves quoted tag boundaries and literal less-than text', async () => {
    const { spawnSync } = await import('node:child_process');
    const script = `import { parseHtmlTokens } from ${JSON.stringify(new URL('../frontend/src/features/documents/domain/sourceFidelity.js', import.meta.url).href)}; console.log(JSON.stringify(parseHtmlTokens('<')));`;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], { timeout: 2000, encoding: 'utf8' });
    assert.equal(result.status, 0, result.error?.message);
    assert.deepEqual(JSON.parse(result.stdout), [{ type: 'text', text: '<' }]);
    const tokens = parseHtmlTokens('<a title="1 > 0">A</a>');
    assert.equal(tokens[0].attrs.title, '1 > 0');
  });

  for (const index of [0, 1, 2]) {
    it(`deletes repeated block ${index} without changing adjacent source`, () => {
      const source = '\uFEFF*same*\r\n\r\n_same_\n\n*same*  \n';
      const blocks = extractBlockSegments(segmentMarkdown(source));
      const result = applyLocalizedEdits(source, [{ op: 'delete', index, range: [blocks[index].startOffset, blocks[index].endOffset], blockId: blocks[index].blockId }], { render: markdownToVisualHtml, expectedRevision: computeSourceRevision(source) });
      assert.notEqual(result.status, 'unsupported');
      for (let i = 0; i < blocks.length; i++) if (i !== index) assert.ok(result.markdown.includes(blocks[i].raw));
    });
  }
});
