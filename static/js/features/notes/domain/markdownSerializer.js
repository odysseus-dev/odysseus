/** Note editor DOM back to markdown, in the notes dialect of static/js/markdown.js. Works on any tree exposing nodeType, nodeName, nodeValue, childNodes and getAttribute. */
import { MAX_INDENT, fenceDelimiter, fencedCode, linesOutsideFences, parseTaskLines } from './markdownTasks.js';

const ELEMENT = 1;
const TEXT = 3;
const INLINE = new Set(['A', 'ABBR', 'B', 'BR', 'BUTTON', 'CODE', 'DEL', 'EM', 'FONT', 'I', 'IMG', 'INPUT', 'S', 'SPAN', 'STRIKE', 'STRONG', 'WBR']);
// Formatting the notes dialect has no syntax for. Seeing one means the block
// can only be edited as markdown source.
const UNREPRESENTABLE = new Set(['AUDIO', 'CANVAS', 'DETAILS', 'FIGURE', 'IFRAME', 'INS', 'KBD', 'MARK', 'OBJECT', 'SMALL', 'SUB', 'SUMMARY', 'SUP', 'SVG', 'U', 'VIDEO']);

const name = node => String(node.nodeName || '').toUpperCase();
const attr = (node, key) => (typeof node.getAttribute === 'function' ? node.getAttribute(key) : null);
const hasClass = (node, cls) => ` ${attr(node, 'class') || ''} `.includes(` ${cls} `);
const children = node => Array.from(node.childNodes || []);
const indentOf = node => Math.max(0, Math.min(MAX_INDENT, parseInt(attr(node, 'data-indent'), 10) || 0));
const pad = indent => '  '.repeat(indent);
const isInline = node => node.nodeType === TEXT || (node.nodeType === ELEMENT && INLINE.has(name(node)) && !hasClass(node, 'ody-math-pending'));

function textOf(node) {
  if (node.nodeType === TEXT) return node.nodeValue || '';
  if (node.nodeType !== ELEMENT || name(node) === 'BUTTON') return '';
  if (name(node) === 'BR') return '\n';
  return children(node).map(textOf).join('');
}

/** Escape text so the notes renderer shows it as typed. `]` stays bare: `\[ … \]` is display math. */
export function escapeMarkdownText(text) {
  // One pass: a backslash before a markdown character is doubled, and `, *
  // and [ get a backslash.
  let out = String(text).replace(/\\(?=[\\`*~#\-+.>|$<_!\[\]:])|[`*[]/g, '\\$&');
  out = out.replace(/~(?=~)|(?<=~)~/g, '\\~');
  if ((out.match(/(?<!\\)\$/g) || []).length > 1) out = out.replace(/(?<!\\)\$/g, '\\$');
  out = out.replace(/<(?=[A-Za-z/!?])/g, '\\<');
  if ((out.match(/(?<!\\)\|/g) || []).length > 1) out = out.replace(/(?<!\\)\|/g, '\\|');
  return out;
}

/** Escape what a line may not start with: heading, bullet, number, quote, rule. */
function escapeLineStart(text) {
  return String(text).replace(/^([ \t]*)(#{1,6}(?=[ \t]|$)|>|[-+](?=[ \t]|$)|(\d+)\.(?=[ \t]|$)|([-_])\4\4)/, (_m, ws, mark, digits) => {
    if (digits) return `${ws}${digits}\\.`;
    return `${ws}\\${mark}`;
  });
}

function createContext() {
  return { lineStart: true, lossy: false, marks: new Set() };
}

function wrapMark(node, ctx, mark) {
  if (ctx.marks.has(mark)) return inlineChildren(node, ctx);
  ctx.marks.add(mark);
  const startsLine = ctx.lineStart;
  ctx.lineStart = false;
  const inner = inlineChildren(node, ctx);
  ctx.marks.delete(mark);
  if (!inner.trim()) { ctx.lineStart = startsLine; return inner; }
  const [, lead, body, tail] = /^(\s*)([\s\S]*?)(\s*)$/.exec(inner);
  return `${lead}${mark}${body}${mark}${tail}`;
}

function imageMarkdown(node) {
  const src = attr(node, 'src') || '';
  if (!src) return '';
  const alt = (attr(node, 'alt') || '').replace(/[\]\n]/g, ' ');
  const title = attr(node, 'title');
  return title ? `![${alt}](${src} "${title.replace(/"/g, "'")}")` : `![${alt}](${src})`;
}

function linkMarkdown(node, ctx) {
  const href = attr(node, 'href') || '';
  const label = textOf(node);
  // Autolinked URLs render as <a>url</a>; write them back bare.
  if (href && (label === href || `${label}/` === href) && /^https?:\/\//i.test(label)) {
    ctx.lineStart = false;
    return label;
  }
  const startsLine = ctx.lineStart;
  ctx.lineStart = false;
  const inner = inlineChildren(node, ctx);
  if (!href) { ctx.lineStart = startsLine && !inner.trim(); return inner; }
  const title = attr(node, 'title');
  const dest = href.replace(/\)/g, '%29').replace(/\s/g, '%20');
  return title ? `[${inner}](${dest} "${title.replace(/"/g, "'")}")` : `[${inner}](${dest})`;
}

function inlineNode(node, ctx) {
  if (node.nodeType === TEXT) {
    const raw = (node.nodeValue || '').replace(/ /g, ' ').replace(/\n/g, ' ');
    let out = escapeMarkdownText(raw);
    if (ctx.lineStart) {
      out = escapeLineStart(out);
      if (raw.trim()) ctx.lineStart = false;
    }
    return out;
  }
  if (node.nodeType !== ELEMENT) return '';
  const tag = name(node);
  if (tag === 'BR') { ctx.lineStart = true; return '\n'; }
  if (tag === 'BUTTON' || tag === 'INPUT' || hasClass(node, 'md-task-box')) return '';
  if (hasClass(node, 'emoji')) { ctx.lineStart = false; return attr(node, 'aria-label') || ''; }
  if (hasClass(node, 'ody-math-pending') || hasClass(node, 'katex') || UNREPRESENTABLE.has(tag)) {
    ctx.lossy = true;
    ctx.lineStart = false;
    return escapeMarkdownText(textOf(node));
  }
  if (tag === 'IMG') { ctx.lineStart = false; return imageMarkdown(node); }
  if (tag === 'STRONG' || tag === 'B') return wrapMark(node, ctx, '**');
  if (tag === 'EM' || tag === 'I') return wrapMark(node, ctx, '*');
  if (tag === 'DEL' || tag === 'S' || tag === 'STRIKE') return wrapMark(node, ctx, '~~');
  if (tag === 'CODE') {
    const code = textOf(node);
    if (!code) return '';
    if (code.includes('`') || code.includes('\n')) ctx.lossy = true;
    ctx.lineStart = false;
    return `\`${code}\``;
  }
  if (tag === 'A') return linkMarkdown(node, ctx);
  // Lists and tables inside an item are emitted by the block pass.
  if (tag === 'UL' || tag === 'OL' || tag === 'TABLE') return '';
  if (!INLINE.has(tag)) {
    // A block the browser nested inside an inline context (a <div> inside a
    // list item): keep its text on a new line.
    const inner = inlineChildren(node, ctx);
    return inner ? `\n${inner}` : '';
  }
  return inlineChildren(node, ctx);
}

function inlineChildren(node, ctx) {
  return children(node).map(child => inlineNode(child, ctx)).join('');
}

function inlineLines(node, ctx) {
  ctx.lineStart = true;
  const text = inlineChildren(node, ctx).replace(/\n$/, '');
  return text.split('\n').map(line => line.replace(/[ \t]+$/, ''));
}

const singleLine = (node, ctx) => inlineLines(node, ctx).join(' ').replace(/\s+/g, ' ').trim();

function listLines(list, ctx, baseIndent = 0) {
  const ordered = name(list) === 'OL';
  let number = parseInt(attr(list, 'start'), 10) || 1;
  const lines = [];
  for (const item of children(list)) {
    if (item.nodeType !== ELEMENT) continue;
    if (name(item) === 'UL' || name(item) === 'OL') { lines.push(...listLines(item, ctx, baseIndent + 1)); continue; }
    if (name(item) !== 'LI') { lines.push(...blockLines(item, ctx)); continue; }
    const indent = Math.min(MAX_INDENT, baseIndent + indentOf(item));
    const text = singleLine(item, ctx);
    if (hasClass(item, 'md-task')) {
      const done = attr(item, 'data-done') === 'true';
      lines.push(`${pad(indent)}- [${done ? 'x' : ' '}] ${text}`.replace(/[ \t]+$/, ''));
    } else {
      lines.push(`${ordered ? '' : pad(indent)}${ordered ? `${number}.` : '-'} ${text}`.replace(/[ \t]+$/, ''));
      number += 1;
    }
    for (const nested of children(item)) {
      if (nested.nodeType === ELEMENT && (name(nested) === 'UL' || name(nested) === 'OL')) lines.push(...listLines(nested, ctx, indent + 1));
    }
  }
  return lines;
}

function codeLines(pre, ctx) {
  if (hasClass(pre, 'mermaid')) ctx.lossy = true;
  const code = children(pre).find(child => child.nodeType === ELEMENT && name(child) === 'CODE');
  const lang = ((code && attr(code, 'data-lang')) || '').trim();
  // The whole <pre>, not only its <code>: a Backspace or Delete across the
  // fence's edge can leave typed text beside the <code>, and it is kept.
  const body = textOf(pre).replace(/\n$/, '');
  const longest = Math.max(0, ...(body.match(/`+/g) || []).map(run => run.length));
  const fence = '`'.repeat(Math.max(3, longest + 1));
  return [fence + lang, ...body.split('\n'), fence];
}

function tableLines(table, ctx) {
  const rows = [];
  const collect = node => children(node).forEach(child => {
    if (child.nodeType !== ELEMENT) return;
    if (name(child) === 'TR') rows.push(child);
    else collect(child);
  });
  collect(table);
  const lines = [];
  rows.forEach((row, index) => {
    const cells = children(row).filter(cell => cell.nodeType === ELEMENT && (name(cell) === 'TH' || name(cell) === 'TD'));
    if (!cells.length) return;
    lines.push(`| ${cells.map(cell => singleLine(cell, ctx).replace(/(?<!\\)\|/g, '\\|') || ' ').join(' | ')} |`);
    if (index === 0) lines.push(`| ${cells.map(() => '---').join(' | ')} |`);
  });
  return lines;
}

function blockLines(node, ctx) {
  if (node.nodeType !== ELEMENT) return [];
  const tag = name(node);
  if (/^H[1-6]$/.test(tag)) return [`${'#'.repeat(Number(tag[1]))} ${singleLine(node, ctx)}`.replace(/[ \t]+$/, '')];
  if (tag === 'UL' || tag === 'OL') return listLines(node, ctx);
  if (tag === 'PRE') return codeLines(node, ctx);
  if (tag === 'HR') return ['---'];
  if (tag === 'TABLE') return tableLines(node, ctx);
  if (tag === 'BLOCKQUOTE') return nodeLines(children(node), ctx).filter(line => line.trim()).map(line => `> ${line}`);
  if (tag === 'P' || tag === 'DIV' || tag === 'SECTION' || tag === 'ARTICLE') {
    if (hasClass(node, 'mermaid-container')) ctx.lossy = true;
    const kids = children(node);
    if (kids.some(child => child.nodeType === ELEMENT && !isInline(child))) return nodeLines(kids, ctx);
    return inlineLines(node, ctx);
  }
  ctx.lossy = true;
  return textOf(node).split('\n');
}

function runLines(run, ctx) {
  const lines = [];
  let current = '';
  ctx.lineStart = true;
  for (const node of run) {
    if (node.nodeType === TEXT && (node.nodeValue || '').includes('\n')) {
      // Between rendered inline elements, a newline is a source line break.
      const parts = (node.nodeValue || '').split('\n');
      parts.forEach((part, i) => {
        if (i > 0) { lines.push(current); current = ''; ctx.lineStart = true; }
        current += inlineNode({ nodeType: TEXT, nodeValue: part }, ctx);
      });
    } else {
      current += inlineNode(node, ctx);
    }
  }
  lines.push(current);
  const out = lines.flatMap(line => line.split('\n')).map(line => line.replace(/[ \t]+$/, ''));
  while (out.length && !out[0].trim()) out.shift();
  while (out.length && !out[out.length - 1].trim()) out.pop();
  return out;
}

function nodeLines(nodes, ctx) {
  const lines = [];
  let run = [];
  const flush = () => { if (run.length) lines.push(...runLines(run, ctx)); run = []; };
  for (const node of nodes) {
    if (isInline(node)) { run.push(node); continue; }
    flush();
    lines.push(...blockLines(node, ctx));
  }
  flush();
  return lines;
}

/** Markdown lines for a run of sibling nodes; `lossy` when something in them has no markdown form. */
export function serializeNodes(nodes) {
  const ctx = createContext();
  const lines = nodeLines(Array.from(nodes || []), ctx);
  return { lines, lossy: ctx.lossy };
}

/**
 * Join editor segments into one document. A segment is `{ lines }` or
 * `{ source }` (an untouched block, written back byte for byte) plus the
 * `group` of the source block it came from. Different groups are separated
 * by a blank line, as they were in the source.
 */
export function joinSegments(segments) {
  const lines = [];
  let previous;
  // Inside fenced code every blank line is code, so runs of them stay.
  let fence = null;
  for (const segment of segments) {
    const own = segment.source != null ? String(segment.source).split('\n') : (segment.lines || []);
    if (!own.length) continue;
    if (lines.length && segment.group !== previous && lines[lines.length - 1] !== '') lines.push('');
    for (const line of own) {
      if (segment.source == null && fence === null && line === '' && lines[lines.length - 1] === '') continue;
      lines.push(line);
      const marker = fenceDelimiter(line);
      if (!marker) continue;
      if (fence === null) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length) fence = null;
    }
    previous = segment.group;
  }
  while (lines.length && lines[0] === '') lines.shift();
  while (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines.join('\n');
}

/** Split note markdown into blank-line separated blocks, never inside a fence. Each keeps the number of task lines before it. */
export function splitMarkdownBlocks(content) {
  const lines = String(content || '').replace(/\r\n/g, '\n').split('\n');
  const outside = new Set(linesOutsideFences(content || '').map(([lineno]) => lineno));
  const blocks = [];
  let current = [];
  let taskOffset = 0;
  const flush = () => {
    if (!current.length) return;
    const source = current.join('\n');
    blocks.push({ source, taskOffset });
    taskOffset += parseTaskLines(source).length;
    current = [];
  };
  lines.forEach((line, lineno) => {
    if (outside.has(lineno) && !line.trim()) flush();
    else current.push(line);
  });
  flush();
  return blocks;
}

/** Blocks whose source says more than their rendering shows: aligned tables, external images. */
export function sourceNeedsMarkdownMode(source) {
  const text = String(source || '');
  if (/^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)+\|?[ \t]*$/m.test(text) && /^[ \t]*\|?[^\n]*:-|-:/m.test(text)) return true;
  return /!\[[^\]\n]*\]\((?![ \t]*(\/api\/upload\/|data:image\/|blob:))/.test(text);
}

// A markdown link outside code: what static/js/markdown.js reads as one.
const LINK_DEST_RE = /(?<![!\\])\[(?:\\.|[^\]\\\n])+\]\(([^)]+)\)/g;
const INLINE_CODE_RE = /`[^`\n]+`/g;

function linkDestinations(text) {
  const prose = linesOutsideFences(text).map(([, raw]) => raw.replace(INLINE_CODE_RE, '')).join('\n');
  return [...prose.matchAll(LINK_DEST_RE)].map(m => m[1].trim()
    .replace(/%20/gi, ' ').replace(/%29/gi, ')')
    .replace(/^(\S+?)\/?(\s+"[^"]*")?$/, '$1$2'));
}

/**
 * True when editing a block would write back less than its source says: the
 * rendering dropped something the serializer cannot recover. Such a block is
 * edited as markdown source instead. `lines` is what the serializer made of
 * the block's rendering.
 *  - a link whose target the renderer would not show (a custom scheme, say)
 *    comes back as bare text;
 *  - fenced code is shown with its trailing spaces and edge blank lines
 *    trimmed, a `~~~` fence as backticks, and only a word as its language:
 *    any of those would change the code.
 */
export function editLosesSource(source, lines) {
  const text = String(source || '');
  const serialized = lines.join('\n');
  const written = new Set(linkDestinations(serialized));
  if (linkDestinations(text).some(dest => !written.has(dest))) return true;
  const code = blocks => JSON.stringify(blocks.map(({ char, info, body }) => [char, info, body]));
  return code(fencedCode(text)) !== code(fencedCode(serialized));
}
