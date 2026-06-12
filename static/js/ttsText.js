// static/js/ttsText.js
// Markdown → speakable text. Converts raw assistant markdown into plain prose
// that a TTS engine can read naturally: no "double star", no URLs, no LaTeX,
// and crucially no model reasoning ("thinking") read aloud.
// Mirrors services/tts/markdown_to_speech.py — keep the two in sync.

import { extractThinkingBlocks } from './markdown.js';

// Fallback for environments where markdown.js isn't usable. Handles the
// common raw forms: <think|thinking|thought ...> blocks (with attributes),
// unclosed openers, and orphan closers with no opening tag.
function stripThinkingFallback(text) {
  text = text.replace(/<(?:think(?:ing)?|thought)(?:\s+[^>]*)?>[\s\S]*?<\/(?:think(?:ing)?|thought)>/gi, '');
  text = text.replace(/<(?:think(?:ing)?|thought)(?:\s+[^>]*)?>[\s\S]*$/gi, '');
  // Orphan closer: everything before a lone </think> is leaked reasoning
  text = text.replace(/^[\s\S]*?<\/(?:think(?:ing)?|thought)>/i, '');
  text = text.replace(/<\/(?:think(?:ing)?|thought)>/gi, '');
  return text;
}

function stripThinking(text) {
  // Reuse the display pipeline's normalizer — it understands <thought> tags,
  // time="" attributes, Gemma channel markers, "Thinking:" prefixes, merged
  // and orphaned blocks. We speak only what the user sees as the answer.
  try {
    return extractThinkingBlocks(text).content;
  } catch {
    return stripThinkingFallback(text);
  }
}

// Abbreviations TTS engines tend to read literally ("ee gee", "etk").
// Conservative list — titles like Dr./Mr. are already handled well by engines.
const ABBREVIATIONS = [
  [/\be\.g\.,?/gi, 'for example,'],
  [/\bi\.e\.,?/gi, 'that is,'],
  [/\betc\./gi, 'etcetera.'],
  [/\bvs\./gi, 'versus'],
  [/\bet al\./gi, 'and others'],
  [/\bapprox\./gi, 'approximately'],
];

// Speech-friendly rewrites applied after markdown stripping: expand
// abbreviations and symbols engines mangle, smooth punctuation that is read
// awkwardly, and drop glyphs that should never be spoken.
function naturalize(text) {
  for (const [pattern, replacement] of ABBREVIATIONS) {
    text = text.replace(pattern, replacement);
  }

  // Common HTML entities that survive tag stripping
  text = text.replace(/&nbsp;/g, ' ').replace(/&amp;/g, ' & ');
  text = text.replace(/&lt;/g, ' less than ').replace(/&gt;/g, ' greater than ');

  // Symbols engines spell out poorly or skip entirely
  text = text.replace(/(\d)\s*%/g, '$1 percent');
  text = text.replace(/ & /g, ' and ');
  text = text.replace(/°\s*C\b/g, ' degrees Celsius');
  text = text.replace(/°\s*F\b/g, ' degrees Fahrenheit');
  text = text.replace(/(\d)°/g, '$1 degrees');
  text = text.replace(/~(?=\d)/g, 'about ');
  text = text.replace(/\s*(?:->|=>|→|⇒)\s*/g, ' to ');
  text = text.replace(/±/g, ' plus or minus ');

  // Punctuation smoothing
  text = text.replace(/…/g, '...');
  text = text.replace(/\s*—\s*/g, ', ');                 // em dash → spoken pause
  text = text.replace(/(\d)\s*–\s*(?=\d)/g, '$1 to ');   // numeric en-dash range
  text = text.replace(/\s*–\s*/g, ', ');
  text = text.replace(/([!?])\1+/g, '$1');               // "!!" / "??" → single
  text = text.replace(/,\s*,/g, ',');                    // artifacts of the above
  text = text.replace(/[\u201C\u201D]/g, '"').replace(/[\u2018\u2019]/g, "'");

  // snake_case identifiers read better as separate words
  text = text.replace(/(\w)_(?=\w)/g, '$1 ');

  // Emoji / pictographs / leftover arrows — never spoken
  text = text.replace(/[\u{1F000}-\u{1FAFF}\u{2190}-\u{21FF}\u{2300}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}]/gu, '');

  return text;
}

export function markdownToSpeech(src) {
  if (!src) return '';
  let text = stripThinking(String(src));

  // Fenced code blocks (``` and ~~~), incl. mermaid; drop unclosed trailing fence too
  text = text.replace(/^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*?\n[ \t]*\1[ \t]*$/gm, '');
  text = text.replace(/^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*$/m, '');

  // Block math: $$...$$ and \[...\]
  text = text.replace(/\$\$[\s\S]*?\$\$/g, '');
  text = text.replace(/\\\[[\s\S]*?\\\]/g, '');
  // Inline math: $...$ (single line, non-greedy) and \(...\)
  text = text.replace(/\\\([\s\S]*?\\\)/g, '');
  text = text.replace(/\$(?=\S)[^$\n]*?\S\$/g, '');

  // Images: drop entirely (alt text is rarely useful aloud)
  text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, '');

  // Links: keep the label only
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  // Autolinks / bare URLs
  text = text.replace(/<https?:\/\/[^>]+>/g, '');
  text = text.replace(/https?:\/\/\S+/g, '');

  // Remaining HTML tags
  text = text.replace(/<\/?[a-zA-Z][^>]*>/g, '');

  // Tables: separator rows out, data rows → comma-separated sentence
  text = text.replace(/^[ \t]*\|?[ \t:|-]+\|[ \t:|-]*$/gm, '');
  text = text.replace(/^[ \t]*\|(.+)\|[ \t]*$/gm, (m, inner) => {
    const cells = inner.split('|').map(c => c.trim()).filter(Boolean);
    return cells.length ? cells.join(', ') + '.' : '';
  });

  // Headings: keep the title, ensure a sentence-ending pause
  text = text.replace(/^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$/gm, (m, title) =>
    /[.!?:]$/.test(title) ? title : title + '.');

  // Blockquotes
  text = text.replace(/^[ \t]*>[ \t]?/gm, '');

  // Horizontal rules
  text = text.replace(/^[ \t]*([-*_])[ \t]*(?:\1[ \t]*){2,}$/gm, '');

  // Task list checkboxes (before list markers so "- [ ] Task" fully strips)
  text = text.replace(/^([ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+)\[[ xX]\][ \t]+/gm, '$1');

  // List markers (bulleted and numbered): keep the content, and end each
  // item with sentence punctuation so engines pause between items instead
  // of running the whole list together.
  const listItem = (m, content) => {
    content = content.replace(/\s+$/, '');
    return /[.!?:;,]$/.test(content) ? content : content + '.';
  };
  text = text.replace(/^[ \t]*[-*+][ \t]+(.+)$/gm, listItem);
  text = text.replace(/^[ \t]*\d{1,3}[.)][ \t]+(.+)$/gm, listItem);

  // Emphasis / strikethrough markers (keep the words)
  text = text.replace(/(\*\*\*|___)(?=\S)([\s\S]*?\S)\1/g, '$2');
  text = text.replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, '$2');
  text = text.replace(/(\*|_)(?=\S)([\s\S]*?\S)\1/g, '$2');
  text = text.replace(/~~(?=\S)([\s\S]*?\S)~~/g, '$1');

  // Inline code: strip the backticks, keep the content
  text = text.replace(/`([^`\n]+)`/g, '$1');
  // Stray backticks / leftover markers
  text = text.replace(/`+/g, '');

  // Footnote refs like [^1]
  text = text.replace(/\[\^[^\]]*\]/g, '');

  // Speech-friendly rewrites (abbreviations, symbols, punctuation)
  text = naturalize(text);

  // Whitespace cleanup
  text = text.replace(/[ \t]+/g, ' ');
  text = text.replace(/ *\n */g, '\n');
  text = text.replace(/\n{3,}/g, '\n\n');

  return text.trim();
}

const ttsTextModule = { markdownToSpeech };
export default ttsTextModule;
