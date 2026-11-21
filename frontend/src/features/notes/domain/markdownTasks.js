/** Task lines inside note markdown. Mirrors core/notes_markdown.py; both are pinned by tests/fixtures/notes_task_vectors.json. */
const TASK_RE = /^([ \t]*)([-*+])[ \t]+\[([ xX])\][ \t]?([^\n]*)$/;
const FENCE_RE = /^[ \t]*(`{3,}|~{3,})/;
const INDENT_UNIT = 2;
const MAX_INDENT = 3;

function splitLines(content) {
  const lines = String(content).replace(/\r\n/g, '\n').split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

function joinLines(lines, original) {
  const joined = lines.join('\n');
  return original.endsWith('\n') && lines.length ? joined + '\n' : joined;
}

function indentLevel(ws) {
  let width = 0;
  for (const ch of ws) width += ch === '\t' ? INDENT_UNIT : 1;
  return Math.floor(width / INDENT_UNIT);
}

function coerceIndent(value) {
  // Python's int() rejects "2.5" and "oops" alike; match it so legacy junk maps the same way.
  if (typeof value === 'string' && !/^\s*[+-]?\d+\s*$/.test(value)) return 0;
  const n = Math.trunc(Number(value || 0));
  return Number.isFinite(n) ? Math.min(MAX_INDENT, Math.max(0, n)) : 0;
}

function itemText(item) {
  if (!item || typeof item !== 'object') return '';
  return String(item.text || '').split(/\s+/).filter(Boolean).join(' ');
}

const itemDone = item => !!(item && typeof item === 'object' && (item.done || item.checked));
const cleanItems = items => (Array.isArray(items) ? items.filter(it => itemText(it)) : []);
const taskLine = (text, done, indent, bullet = '-', ws = null) =>
  `${ws ?? ' '.repeat(INDENT_UNIT * indent)}${bullet} [${done ? 'x' : ' '}] ${text}`;

/** The fence delimiter a line opens or closes with (`` ``` `` or `~~~`, 3+), or null. */
export function fenceDelimiter(raw) {
  const m = FENCE_RE.exec(raw);
  return m ? m[1] : null;
}

/** [lineno, raw] for every line outside fenced code (CommonMark open/close rules). */
export function linesOutsideFences(content) {
  const out = [];
  let fenceChar = null;
  let fenceLen = 0;
  splitLines(content || '').forEach((raw, lineno) => {
    const m = FENCE_RE.exec(raw);
    if (m) {
      const marker = m[1];
      if (fenceChar === null) { fenceChar = marker[0]; fenceLen = marker.length; return; }
      if (marker[0] === fenceChar && marker.length >= fenceLen) { fenceChar = null; fenceLen = 0; return; }
    }
    if (fenceChar !== null) return;
    out.push([lineno, raw]);
  });
  return out;
}

function taskMatches(content) {
  const out = [];
  for (const [lineno, raw] of linesOutsideFences(content)) {
    const m = TASK_RE.exec(raw);
    if (m) out.push([lineno, m]);
  }
  return out;
}

const toItem = (index, lineno, m) => ({ index, text: m[4].trim(), done: m[3].toLowerCase() === 'x', indent: indentLevel(m[1]), line: lineno });

export function parseTaskLines(content) {
  if (!content) return [];
  return taskMatches(content).map(([lineno, m], index) => toItem(index, lineno, m));
}

export const hasTasks = content => parseTaskLines(content).length > 0;

/** Flip the index-th task. Returns { content, item }; throws RangeError when there is no such task. */
export function toggleTask(content, index) {
  const text = content || '';
  const matches = taskMatches(text);
  if (!Number.isInteger(index) || index < 0 || index >= matches.length) throw new RangeError(`no task line at index ${index}`);
  const [lineno, m] = matches[index];
  const lines = splitLines(text);
  const raw = lines[lineno];
  const nowDone = m[3].toLowerCase() !== 'x';
  const boxAt = raw.indexOf('[', m[1].length + 1);
  lines[lineno] = raw.slice(0, boxAt) + (nowDone ? '[x]' : '[ ]') + raw.slice(boxAt + 3);
  return { content: joinLines(lines, text), item: { ...toItem(index, lineno, m), done: nowDone } };
}

export function itemsToMarkdown(items) {
  return cleanItems(items).map(it => taskLine(itemText(it), itemDone(it), coerceIndent(it.indent))).join('\n');
}

function appendBlock(content, block) {
  if (!block) return content;
  if (!content.trim()) return block;
  return content.replace(/\s+$/, '') + '\n\n' + block;
}

/** The delimiter that would close a fence still open at the end of content, or null. */
function openFenceAtEnd(content) {
  let fenceChar = null;
  let fenceLen = 0;
  for (const raw of splitLines(content)) {
    const marker = fenceDelimiter(raw);
    if (!marker) continue;
    if (fenceChar === null) { fenceChar = marker[0]; fenceLen = marker.length; }
    else if (marker[0] === fenceChar && marker.length >= fenceLen) { fenceChar = null; fenceLen = 0; }
  }
  return fenceChar ? fenceChar.repeat(fenceLen) : null;
}

// Appending closes a fence left open at the end, so the new lines are tasks and not code.
function insertTaskLines(content, newLines) {
  if (!newLines.length) return content;
  const matches = taskMatches(content);
  if (!matches.length) {
    const fence = openFenceAtEnd(content);
    return appendBlock(fence ? content.replace(/\s+$/, '') + '\n' + fence : content, newLines.join('\n'));
  }
  const last = matches[matches.length - 1][0];
  const lines = splitLines(content);
  lines.splice(last + 1, 0, ...newLines);
  return joinLines(lines, content);
}

/** Write an items array into content: same contract as apply_items_to_content in Python. */
export function applyItemsToContent(content, items) {
  let base = content || '';
  if (items == null) return base;
  const wanted = cleanItems(items);
  const matches = taskMatches(base);
  const lines = splitLines(base);
  const drop = new Set();
  matches.forEach(([lineno, m], n) => {
    if (n >= wanted.length) { drop.add(lineno); return; }
    const it = wanted[n];
    const text = itemText(it), done = itemDone(it), indent = coerceIndent(it.indent);
    const sameIndent = indentLevel(m[1]) === indent;
    if (text === m[4].trim() && done === (m[3].toLowerCase() === 'x') && sameIndent) return;
    lines[lineno] = taskLine(text, done, indent, m[2], sameIndent ? m[1] : null);
  });
  base = joinLines(drop.size ? lines.filter((_, lineno) => !drop.has(lineno)) : lines, base);
  return insertTaskLines(base, wanted.slice(matches.length).map(it => taskLine(itemText(it), itemDone(it), coerceIndent(it.indent))));
}

/** Add one open task after the last task line. Returns { content, item }. */
export function appendTaskLine(content, text, indent = 0) {
  const clean = String(text ?? '').split(/\s+/).filter(Boolean).join(' ');
  if (!clean) throw new RangeError('task text is empty');
  const level = coerceIndent(indent);
  const updated = insertTaskLines(content || '', [taskLine(clean, false, level)]);
  const tasks = parseTaskLines(updated);
  const item = [...tasks].reverse().find(t => t.text === clean && !t.done && t.indent === level) || tasks[tasks.length - 1];
  return { content: updated, item };
}

/** Content without its task lines: what a goal's description field shows. */
export function stripTaskLines(content) {
  if (!content) return '';
  const taskLines = new Set(taskMatches(content).map(([lineno]) => lineno));
  return splitLines(content).filter((_, lineno) => !taskLines.has(lineno)).join('\n').replace(/\n{3,}/g, '\n\n').trim();
}
