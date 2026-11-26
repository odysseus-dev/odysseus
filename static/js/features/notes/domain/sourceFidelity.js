/**
 * Write an edited block back with only what was edited changed.
 *
 * The note editor turns a block's DOM back into markdown with the serializer,
 * which writes one canonical form: `*` escaped, a fence as backticks, a table
 * row with single spaces. The note may have said the same thing differently.
 * Three texts are compared:
 *  - `source`, the block as stored;
 *  - `baseline`, what the serializer made of the block when it was rendered;
 *  - `edited`, what it makes of the block now.
 * Every character the edit kept is written as the source wrote it, together
 * with the source's own characters around it (an escape, trailing spaces, a
 * `~~~` fence). Typed characters are written as the serializer writes them.
 */

// Past this many differences the texts are not one note edited a little;
// the canonical serialization is written instead.
const MAX_EDIT_DISTANCE = 1500;

/**
 * Myers' diff of two sequences. Returns the matched index pairs in order, or
 * null when they differ in more than `limit` places.
 */
function matches(a, b, limit) {
  const n = a.length;
  const m = b.length;
  const max = Math.min(n + m, limit);
  const offset = max + 1;
  const v = new Int32Array(2 * max + 3);
  const trace = [];
  let found = -1;
  for (let d = 0; d <= max && found < 0; d++) {
    trace.push(v.slice(offset - d - 1, offset + d + 2));
    for (let k = -d; k <= d; k += 2) {
      let x = (k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])) ? v[offset + k + 1] : v[offset + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) { x++; y++; }
      v[offset + k] = x;
      if (x >= n && y >= m) { found = d; break; }
    }
  }
  if (found < 0) return null;
  const pairs = [];
  let x = n;
  let y = m;
  for (let d = found; d > 0; d--) {
    const prev = trace[d];
    const at = k => prev[k + d + 1];
    const k = x - y;
    const down = k === -d || (k !== d && at(k - 1) < at(k + 1));
    const prevK = down ? k + 1 : k - 1;
    const prevX = at(prevK);
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) { x--; y--; pairs.push([x, y]); }
    x = prevX;
    y = prevY;
  }
  while (x > 0 && y > 0) { x--; y--; pairs.push([x, y]); }
  return pairs.reverse();
}

/** For each character of `a`, its match in `b` or -1. Lines first, then the characters of the lines that changed. */
export function alignText(a, b, limit = MAX_EDIT_DISTANCE) {
  const map = new Int32Array(a.length).fill(-1);
  if (a === b) { for (let i = 0; i < a.length; i++) map[i] = i; return map; }
  const linesA = a.split('\n');
  const linesB = b.split('\n');
  const startsA = [];
  const startsB = [];
  linesA.reduce((at, line) => { startsA.push(at); return at + line.length + 1; }, 0);
  linesB.reduce((at, line) => { startsB.push(at); return at + line.length + 1; }, 0);
  const pairs = matches(linesA, linesB, limit);
  if (!pairs) return null;
  // Matched lines map character for character, their newline included; the
  // lines between two matches are aligned by character.
  let lastA = -1;
  let lastB = -1;
  const hunk = (endA, endB) => {
    const fromA = lastA + 1 < linesA.length ? startsA[lastA + 1] : a.length;
    const fromB = lastB + 1 < linesB.length ? startsB[lastB + 1] : b.length;
    const toA = endA < linesA.length ? startsA[endA] : a.length;
    const toB = endB < linesB.length ? startsB[endB] : b.length;
    if (toA <= fromA || toB <= fromB) return true;
    const inner = matches(a.slice(fromA, toA), b.slice(fromB, toB), limit);
    if (!inner) return false;
    for (const [i, j] of inner) map[fromA + i] = fromB + j;
    return true;
  };
  for (const [i, j] of [...pairs, [linesA.length, linesB.length]]) {
    if (!hunk(i, j)) return null;
    if (i < linesA.length) {
      for (let c = 0; c <= linesA[i].length && startsA[i] + c < a.length && startsB[j] + c < b.length; c++) map[startsA[i] + c] = startsB[j] + c;
    }
    lastA = i;
    lastB = j;
  }
  return map;
}

/**
 * The source, with the edit from `baseline` to `edited` applied. Null when
 * the texts are too far apart to align.
 */
export function patchSource(source, baseline, edited) {
  if (edited === baseline) return source;
  const toSource = alignText(baseline, source);
  const toEdited = alignText(baseline, edited);
  if (!toSource || !toEdited) return null;
  // The source's own characters before each baseline character it shares.
  const gaps = new Map();
  let last = -1;
  let lastShared = -1;
  for (let i = 0; i < baseline.length; i++) {
    if (toSource[i] < 0) continue;
    gaps.set(i, source.slice(last + 1, toSource[i]));
    last = toSource[i];
    lastShared = i;
  }
  if (lastShared < 0) return null;
  const tail = source.slice(last + 1);
  let out = '';
  let typed = 0;
  // Whether the last shared character was kept: a gap between two deleted
  // characters goes with them.
  let keptBefore = false;
  const emitTyped = upTo => { out += edited.slice(typed, upTo); typed = upTo; };
  for (let i = 0; i < baseline.length; i++) {
    const at = toEdited[i];
    if (at >= 0) { emitTyped(at); typed = at + 1; }
    if (toSource[i] < 0) continue;
    const kept = at >= 0;
    if (kept || keptBefore) out += gaps.get(i);
    if (kept) out += source[toSource[i]];
    keptBefore = kept;
  }
  emitTyped(edited.length);
  if (keptBefore) out += tail;
  return out;
}

/**
 * The markdown to store for an edited block: the patched source when it
 * renders exactly as the edit does, the canonical `edited` text otherwise.
 * `render` is the note renderer.
 */
// Markup compared the way it displays: outside <pre>, a run of whitespace is
// one space, a non-breaking space is a space, and a space just inside a
// mark (`** bold**`) is the same as one just outside it. Code keeps every
// character. A diagram's generated id never counts.
const INLINE_MARK = '(?:strong|em|del|u|sup|sub|mark|a|code)';
const OPEN_MARK_RE = new RegExp(`(<${INLINE_MARK}\\b[^>]*>)\\s+`, 'g');
const CLOSE_MARK_RE = new RegExp(`\\s+(</${INLINE_MARK}>)`, 'g');
const BLOCK_EDGE_RE = / ?(<\/?(?:p|li|ul|ol|h[1-6]|blockquote|table|thead|tbody|tr|td|th|div|hr|br)\b[^>]*>) ?/g;
// A line written right under a list, a table or a code block renders as bare
// text; written a blank line under it, as a paragraph. Both show the same
// line under the same block, so a paragraph's tags only count between two
// runs of text. `\u2029` stands for a paragraph edge while comparing.
const PARA = '\u2029';
const PARA_TAG_RE = /<\/?p\b[^>]*>/g;
const PARA_AT_BLOCK_RE = new RegExp(`${PARA}+(?=<\\/?(?:li|ul|ol|h[1-6]|blockquote|table|div|hr)\\b)|(<\\/?(?:li|ul|ol|h[1-6]|blockquote|table|div|hr)\\b[^>]*>)${PARA}+`, 'g');
const paragraphEdges = part => part
  .replace(PARA_TAG_RE, PARA)
  .replace(PARA_AT_BLOCK_RE, '$1')
  .replace(new RegExp(`^${PARA}+|${PARA}+$`, 'g'), '')
  .replace(new RegExp(`${PARA}+`, 'g'), PARA);
export function displayed(markup) {
  // A diagram's id and an external image's per-render mark change on every render.
  return String(markup).replace(/ id="mermaid-[^"]*"/g, '').replace(/ data-ody-external="[^"]*"/g, '').split(/(<pre\b[\s\S]*?<\/pre>)/).map((part, i) => (i % 2 ? part : paragraphEdges(part
    .replace(/&nbsp;|\u00a0/g, ' ')
    .replace(OPEN_MARK_RE, ' $1')
    .replace(CLOSE_MARK_RE, '$1 ')
    .replace(/\s+/g, ' ')
    .replace(BLOCK_EDGE_RE, '$1')))).join('').trim();
}

export function preserveSource({ source, baseline, edited, render }) {
  if (edited === baseline) return source;
  const patched = patchSource(source, baseline, edited);
  if (patched === null || patched === edited) return edited;
  return displayed(render(patched)) === displayed(render(edited)) ? patched : edited;
}
