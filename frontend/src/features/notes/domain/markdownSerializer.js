/** Note editor DOM back to markdown, in the notes dialect of static/js/markdown.js. Works on any tree exposing nodeType, nodeName, nodeValue, childNodes and getAttribute. */
import { MAX_INDENT, fenceDelimiter, linesOutsideFences, parseTaskLines } from './markdownTasks.js';

const ELEMENT = 1;
const TEXT = 3;
const INLINE = new Set(['A', 'ABBR', 'B', 'BR', 'BUTTON', 'CODE', 'DEL', 'EM', 'FONT', 'I', 'IMG', 'INPUT', 'MARK', 'S', 'SPAN', 'STRIKE', 'STRONG', 'SUB', 'SUP', 'U', 'WBR']);
// Formatting the notes dialect writes as its HTML tag.
const HTML_MARKS = new Set(['MARK', 'SUB', 'SUP', 'U']);
// Elements the notes dialect has no syntax for. The renderer only makes them
// inside an island (which is written back as its source), so meeting one
// anywhere else means the block cannot be written faithfully.
const UNREPRESENTABLE = new Set(['AUDIO', 'CANVAS', 'DETAILS', 'FIGURE', 'IFRAME', 'INS', 'KBD', 'OBJECT', 'SMALL', 'SUMMARY', 'SVG', 'VIDEO']);

const name = node => String(node.nodeName || '').toUpperCase();
const attr = (node, key) => (typeof node.getAttribute === 'function' ? node.getAttribute(key) : null);
const hasClass = (node, cls) => ` ${attr(node, 'class') || ''} `.includes(` ${cls} `);
const children = node => Array.from(node.childNodes || []);
const indentOf = node => Math.max(0, Math.min(MAX_INDENT, parseInt(attr(node, 'data-indent'), 10) || 0));
const pad = indent => '  '.repeat(indent);
// Editor chrome injected after render (the per-task agent button). Never markdown.
const isSkipped = node => node.nodeType === ELEMENT && attr(node, 'data-md-skip') !== null;
/** An island: rendered from markdown with no rich form, and written back as that markdown (`data-md-src`). */
const isIsland = node => node.nodeType === ELEMENT && hasClass(node, 'md-island') && attr(node, 'data-md-src') !== null;
const isInline = node => node.nodeType === TEXT || (node.nodeType === ELEMENT && INLINE.has(name(node)) && !hasClass(node, 'ody-math-pending'));

function textOf(node) {
  if (node.nodeType === TEXT) return node.nodeValue || '';
  if (node.nodeType !== ELEMENT || name(node) === 'BUTTON' || isSkipped(node)) return '';
  if (name(node) === 'BR') return '\n';
  return children(node).map(textOf).join('');
}

/**
 * Escape text so the notes renderer shows it as typed. `]` stays bare: `\[ … \]` is display math.
 * A typed `\(` or `\)` gets its backslash doubled, so `\(a+b\)` stays text and is not inline math.
 */
export function escapeMarkdownText(text) {
  let out = String(text).replace(/\\(?=[\\`*~#\-+.>|$<_!\[\]:()])/g, '\\\\');
  out = out.replace(/[`*[]/g, '\\$&');
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
  return { lineStart: true, lossy: false, marks: new Set(), last: null, gap: '', nested: new Set() };
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

/** A note image's width, in percent of the note. */
export const IMAGE_WIDTH_MIN = 5;
export const IMAGE_WIDTH_MAX = 100;
export const clampImageWidth = value => Math.max(IMAGE_WIDTH_MIN, Math.min(IMAGE_WIDTH_MAX, value));

/** `w=N` from a resized image's `data-w`; full width is written with no title. */
export function imageWidthTitle(width) {
  const value = parseInt(width, 10);
  if (!Number.isFinite(value)) return '';
  const clamped = clampImageWidth(value);
  return clamped === IMAGE_WIDTH_MAX ? '' : `w=${clamped}`;
}

/** An image URL as a markdown destination: `)` and whitespace would end it early. */
export function encodeImageSrc(src) {
  return String(src || '').trim().replace(/\)/g, '%29').replace(/\s/g, '%20');
}

/** A shared note renders uploads as `/api/notes/<note>/media/<id>`; the note itself stores `/api/upload/<id>`. */
export function storedImageSrc(src) {
  const media = /^\/api\/notes\/[^/?#]+\/media\/([0-9a-f]{32}(?:\.[a-z0-9]+)?)$/i.exec(String(src || '').trim());
  return media ? `/api/upload/${media[1]}` : src;
}

/** A backtick-quoted code span that can hold `code`: the fence is longer than any run inside (CommonMark). */
export function codeSpan(code) {
  const text = String(code).replace(/\r?\n/g, ' ');
  const longest = Math.max(0, ...(text.match(/`+/g) || []).map(run => run.length));
  const fence = '`'.repeat(longest + 1);
  const pad = /^`|`$/.test(text) || (/^ [\s\S]*[^ ][\s\S]* $/.test(text)) ? ' ' : '';
  return `${fence}${pad}${text}${pad}${fence}`;
}

function wrapHtml(node, ctx, tag) {
  const mark = `<${tag}>`;
  if (ctx.marks.has(mark)) return inlineChildren(node, ctx);
  ctx.marks.add(mark);
  const startsLine = ctx.lineStart;
  ctx.lineStart = false;
  const inner = inlineChildren(node, ctx);
  ctx.marks.delete(mark);
  if (!inner.trim()) { ctx.lineStart = startsLine; return inner; }
  const [, lead, body, tail] = /^(\s*)([\s\S]*?)(\s*)$/.exec(inner);
  return `${lead}<${tag}>${body}</${tag}>${tail}`;
}

function imageMarkdown(node) {
  // An image from elsewhere, shown because the viewer turned that on: it is
  // written back exactly as the note wrote it, like the link it would
  // otherwise show as.
  const written = attr(node, 'data-md-dest');
  if (attr(node, 'data-md-image') !== null && written) return `![${attr(node, 'data-md-alt') || ''}](${written})`;
  const src = encodeImageSrc(storedImageSrc(attr(node, 'src')));
  if (!src) return '';
  const alt = (attr(node, 'alt') || '').replace(/[\]\n]/g, ' ');
  const title = attr(node, 'data-w') != null ? imageWidthTitle(attr(node, 'data-w')) : attr(node, 'title');
  return title ? `![${alt}](${src} "${title.replace(/"/g, "'")}")` : `![${alt}](${src})`;
}

/** A link target as markdown: `)` and whitespace would end it early; a title is quoted. */
export function linkDestination(href, title = '') {
  const dest = String(href || '').trim().replace(/\)/g, '%29').replace(/\s/g, '%20');
  return title ? `${dest} "${String(title).replace(/"/g, "'")}"` : dest;
}

function linkMarkdown(node, ctx) {
  const href = attr(node, 'href') || '';
  // The target as the note wrote it (any scheme, a title), when the renderer
  // stamped it; the editor's link menu keeps it current.
  const written = attr(node, 'data-md-dest');
  const label = textOf(node);
  if (attr(node, 'data-md-image') !== null && written) {
    // An image from elsewhere, shown as a link: its text is the alt text.
    ctx.lineStart = false;
    const url = written.replace(/\s+"[^"]*"\s*$/, '');
    const alt = attr(node, 'data-md-alt') === '' && label === url ? '' : label.replace(/[\]\n]/g, ' ');
    return `![${alt}](${written})`;
  }
  // Autolinked URLs render as <a>url</a>; write them back bare.
  if (!written && href && (label === href || `${label}/` === href) && /^https?:\/\//i.test(label)) {
    ctx.lineStart = false;
    return label;
  }
  const startsLine = ctx.lineStart;
  ctx.lineStart = false;
  const inner = inlineChildren(node, ctx);
  if (!inner.trim()) { ctx.lineStart = startsLine; return inner; }
  if (written) return `[${inner}](${written})`;
  if (!href) { ctx.lineStart = startsLine && !inner.trim(); return inner; }
  return `[${inner}](${linkDestination(href, attr(node, 'title'))})`;
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
  if (tag === 'BUTTON' || tag === 'INPUT' || hasClass(node, 'md-task-box') || isSkipped(node)) return '';
  if (hasClass(node, 'emoji')) { ctx.lineStart = false; return attr(node, 'aria-label') || ''; }
  if (isIsland(node)) { ctx.lineStart = false; return attr(node, 'data-md-src'); }
  if (hasClass(node, 'ody-math-pending') || hasClass(node, 'katex') || UNREPRESENTABLE.has(tag)) {
    ctx.lossy = true;
    ctx.lineStart = false;
    return escapeMarkdownText(textOf(node));
  }
  if (tag === 'IMG') { ctx.lineStart = false; return imageMarkdown(node); }
  if (tag === 'STRONG' || tag === 'B') return wrapMark(node, ctx, '**');
  if (tag === 'EM' || tag === 'I') return wrapMark(node, ctx, '*');
  if (tag === 'DEL' || tag === 'S' || tag === 'STRIKE') return wrapMark(node, ctx, '~~');
  if (HTML_MARKS.has(tag)) return wrapHtml(node, ctx, tag.toLowerCase());
  if (tag === 'CODE') {
    const code = textOf(node);
    if (!code) return '';
    ctx.lineStart = false;
    return codeSpan(code);
  }
  if (tag === 'A') return linkMarkdown(node, ctx);
  // A list right inside an item is written by the list pass. Any other list
  // or table here (in a heading, a cell, an item's text) has no markdown form.
  if ((tag === 'UL' || tag === 'OL') && ctx.nested.has(node)) return '';
  if (tag === 'UL' || tag === 'OL' || tag === 'TABLE') {
    ctx.lossy = true;
    ctx.lineStart = false;
    return ` ${escapeMarkdownText(textOf(node).replace(/\s+/g, ' ').trim())}`;
  }
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
    if (item.nodeType !== ELEMENT || isSkipped(item)) continue;
    if (name(item) === 'UL' || name(item) === 'OL') { lines.push(...listLines(item, ctx, baseIndent + 1)); continue; }
    if (name(item) !== 'LI') { lines.push(...blockLines(item, ctx)); continue; }
    const indent = Math.min(MAX_INDENT, baseIndent + indentOf(item));
    const nested = children(item).filter(child => child.nodeType === ELEMENT && (name(child) === 'UL' || name(child) === 'OL'));
    nested.forEach(list => ctx.nested.add(list));
    const text = singleLine(item, ctx);
    if (hasClass(item, 'md-task')) {
      const done = attr(item, 'data-done') === 'true';
      lines.push(`${pad(indent)}- [${done ? 'x' : ' '}] ${text}`.replace(/[ \t]+$/, ''));
    } else {
      lines.push(`${ordered ? '' : pad(indent)}${ordered ? `${number}.` : '-'} ${text}`.replace(/[ \t]+$/, ''));
      number += 1;
    }
    for (const list of nested) lines.push(...listLines(list, ctx, indent + 1));
  }
  return lines;
}

function codeLines(pre, ctx) {
  if (hasClass(pre, 'mermaid')) ctx.lossy = true;
  const code = children(pre).find(child => child.nodeType === ELEMENT && name(child) === 'CODE');
  const lang = ((code && attr(code, 'data-lang')) || '').trim();
  // The whole <pre>, not only its <code>: a Backspace or Delete across the
  // fence's edge can leave typed text beside the <code>, and it is kept.
  const body = textOf(pre).replace(/\n$/, '').split('\n');
  // The fence as the note wrote it (`~~~`, the whole info string), unless a
  // line of the code would now close it.
  const open = attr(pre, 'data-md-open');
  const close = attr(pre, 'data-md-close');
  const opener = open ? fenceDelimiter(open) : null;
  if (opener && !body.some(line => {
    const marker = fenceDelimiter(line);
    return marker && marker[0] === opener[0] && marker.length >= opener.length;
  })) return [open, ...body, ...(close === null ? [] : [close])];
  const longest = Math.max(0, ...body.join('\n').match(/`+/g)?.map(run => run.length) || [0]);
  const fence = '`'.repeat(Math.max(3, longest + 1));
  return [fence + lang, ...body, fence];
}

const DELIMITER = { left: ':---', center: ':---:', right: '---:' };

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
    if (index === 0) lines.push(`| ${cells.map(cell => DELIMITER[attr(cell, 'data-align')] || '---').join(' | ')} |`);
  });
  return lines;
}

/**
 * A quote's lines, each behind `> `. Between its blocks a quote has no blank
 * line (the gap the editor marks them with included): it would end the quote.
 * Inside a fence every line is code, so a blank one is kept, as a bare `>`.
 */
function quoteLines(lines, ctx) {
  const out = [];
  let fence = null;
  for (const line of lines) {
    if (fence === null && (!line.trim() || line === ctx.gap)) continue;
    out.push(line ? `> ${line}` : '>');
    const marker = fenceDelimiter(line);
    if (!marker) continue;
    if (fence === null) fence = marker;
    else if (marker[0] === fence[0] && marker.length >= fence.length) fence = null;
  }
  return out;
}

function blockLines(node, ctx) {
  if (node.nodeType !== ELEMENT || isSkipped(node)) return [];
  if (isIsland(node)) return attr(node, 'data-md-src').split('\n');
  const tag = name(node);
  if (/^H[1-6]$/.test(tag)) return [`${'#'.repeat(Number(tag[1]))} ${singleLine(node, ctx)}`.replace(/[ \t]+$/, '')];
  if (tag === 'UL' || tag === 'OL') return listLines(node, ctx);
  if (tag === 'PRE') return codeLines(node, ctx);
  if (tag === 'HR') return ['---'];
  if (tag === 'TABLE') return tableLines(node, ctx);
  if (tag === 'BLOCKQUOTE') return quoteLines(nodeLines(children(node), ctx), ctx);
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

// Blocks are written apart by a blank line, as markdown has them, so any
// renderer reads them as separate blocks: without one, a line under a list
// is read as part of its last item, and a numbered list under a bullet list
// as more of the same list. Lines of text stay on adjacent lines (a line
// break in the notes dialect), and so do lists of the same kind (one list).
// The kind of what was written last is carried in the context: the browser
// wraps what it starts under another block in a <div>.
const TEXT_BLOCKS = new Set(['P', 'DIV', 'SECTION', 'ARTICLE']);
const isWrapper = node => node.nodeType === ELEMENT && TEXT_BLOCKS.has(name(node)) && !isIsland(node)
  && children(node).some(child => child.nodeType === ELEMENT && !isInline(child));

/** What a node writes, as far as the gap around it goes: `text`, `UL`, `OL` or `block`. */
function blockKind(node) {
  if (isInline(node)) return 'text';
  const tag = name(node);
  if (tag === 'UL' || tag === 'OL') return tag;
  if (TEXT_BLOCKS.has(tag) && !isIsland(node)) return 'text';
  return 'block';
}

/** The kind of the last block a node writes, looking into the <div> the browser wrapped it in. */
function lastKind(node) {
  while (node && isWrapper(node)) {
    const blocks = children(node).filter(child => child.nodeType === ELEMENT && !isSkipped(child));
    const last = blocks[blocks.length - 1];
    if (!last || isInline(last)) return 'text';
    node = last;
  }
  return node && node.nodeType === ELEMENT && !isSkipped(node) ? blockKind(node) : null;
}

const needsGap = (before, kind) => Boolean(before && kind && !(before === kind && (kind === 'text' || kind === 'UL' || kind === 'OL')));

function nodeLines(nodes, ctx) {
  const lines = [];
  let run = [];
  const add = (own, kind) => {
    if (!own.some(line => line.trim())) { lines.push(...own); return; }
    if (needsGap(ctx.last, kind) && lines[lines.length - 1] !== '' && lines[lines.length - 1] !== ctx.gap) lines.push(ctx.gap);
    lines.push(...own);
    ctx.last = kind;
  };
  const flush = () => {
    const text = run.length ? runLines(run, ctx) : [];
    if (text.length) add(text, 'text');
    run = [];
  };
  for (const node of nodes) {
    if (isInline(node)) { run.push(node); continue; }
    flush();
    // A wrapper writes its own gaps, from inside.
    if (isWrapper(node)) { lines.push(...blockLines(node, ctx)); continue; }
    const kind = blockKind(node);
    const before = ctx.last;
    const own = blockLines(node, ctx);
    if (!own.length) continue;
    ctx.last = before;
    add(own, kind);
  }
  flush();
  return lines;
}

/** Markdown lines for a run of sibling nodes; `lossy` when something in them has no markdown form. `after` is the node written just above them, if any; `gap` the line written between two blocks (a blank line). */
export function serializeNodes(nodes, { after = null, gap = '' } = {}) {
  const ctx = createContext();
  ctx.gap = gap;
  // What is already written above these nodes, so the first of them keeps its gap.
  ctx.last = lastKind(after);
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
