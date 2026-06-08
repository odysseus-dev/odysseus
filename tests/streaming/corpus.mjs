// A spread of markdown samples exercising the constructs the renderer supports.
// Used by the streaming-invariant fuzz test (fed token-by-token) and the renderer
// integration test. Keep samples small but structurally varied — the fuzz test
// runs every prefix of every sample, so length is quadratic on cost.
export const CORPUS = [
  ['plain paragraph', 'Just a single sentence of text.'],
  ['two paragraphs', 'First paragraph here.\n\nSecond paragraph here.'],
  ['three paragraphs', 'Alpha block.\n\nBravo block.\n\nCharlie block.'],
  ['atx headings', '# Title\n\nIntro line.\n\n## Section\n\nBody text.'],
  ['setext heading', 'The Title\n=========\n\nA paragraph under it.'],
  ['inline formatting', 'Some **bold**, *italic*, `code`, and a [link](https://x.com).'],
  ['tight unordered list', '- one\n- two\n- three\n\ndone'],
  ['ordered list then text', 'Before\n\n1. first\n2. second\n3. third\n\nAfter'],
  ['loose list then paragraph', '- a\n\n- b\n\n- c\n\nClosing paragraph.'],
  ['nested list', '- top\n  - nested one\n  - nested two\n- back to top\n\nend'],
  ['blockquote', '> quoted line one\n> quoted line two\n\nplain after'],
  ['thematic break', 'above the line\n\n---\n\nbelow the line'],
  ['python code fence', 'Run this:\n\n```python\nprint("hi")\nfor i in range(3):\n    print(i)\n```\n\nThat prints numbers.'],
  ['fence with blank lines inside', '```js\nconst a = 1;\n\nconst b = 2;\n```\n\nafter the code'],
  ['two consecutive fences', '```\nfirst block\n```\n\n```\nsecond block\n```\n\ntail'],
  ['mermaid diagram', 'Diagram:\n\n```mermaid\ngraph TD\nA-->B\n```\n\nafter diagram'],
  ['gfm table', 'Data:\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\nafter table'],
  [
    'mixed document',
    '# Report\n\nIntro paragraph with a `symbol`.\n\n```python\nx = 1\n```\n\n- bullet one\n- bullet two\n\n> a quote\n\nFinal words.',
  ],
];
