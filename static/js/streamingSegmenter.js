// streamingSegmenter.js
//
// Pure logic for incremental ("block-at-a-time") streaming markdown rendering.
//
// While an assistant message streams in, re-rendering the whole accumulated
// markdown on every token is wasteful (O(N^2)) and recreates DOM nodes, which
// makes code-block hover buttons flicker. The fix is to FREEZE the leading part
// of the message that can no longer change, and only re-render the growing tail.
//
// This module answers the one hard question that makes freezing safe:
//
//     Given the full markdown received so far, how many leading characters can
//     be finalized without changing the rendered output?
//
// The contract callers rely on (`render` is the canonical markdown renderer):
//
//     const n = splitFinalized(text, render);
//     render(text.slice(0, n)) + render(text.slice(n))  ===  render(text)
//
// The module is intentionally DOM-free and renderer-agnostic so it can be unit
// tested in isolation and reused for any markdown renderer with no long-range
// cross-block dependencies (no reference-style links / footnotes).
//
// Known limitations (both bounded by the same mitigation):
//   - cutIsRenderSafe proves only PRESENT-tense equivalence. If the renderer pairs
//     an inline delimiter across a blank line (e.g. markdown.js will turn
//     `*a\n\nb*` into emphasis spanning two paragraphs), a block frozen before the
//     closing delimiter arrives can disagree with the final full render.
//   - afterClosedFence boundaries are trusted without the equivalence check, so a
//     fence the real renderer parses differently (e.g. a stray 4-backtick line) can
//     be mis-detected as a close.
//   Both only occur for input the renderer itself handles oddly, and both are
//   transient: chat.js re-renders the finished message from source, so the settled
//   output is always canonical.

// A fenced-code delimiter line: up to 3 leading spaces, then >=3 backticks or
// tildes, then an optional info string.
const FENCE_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/;

/**
 * Scan `text` starting at `fromOffset` — which MUST be at top level (callers only
 * ever advance to a finalized boundary, never into a fence) — and collect the
 * candidate cut points.
 *
 * @returns {{ boundaries: Array<{offset:number, afterClosedFence:boolean}>, inFence:boolean }}
 *   - A blank-line run at top level yields a boundary at the start of the next
 *     non-blank line (`afterClosedFence: false`).
 *   - A fence close yields a boundary just past the closing fence line
 *     (`afterClosedFence: true`) — such a cut is unconditionally safe, since
 *     nothing can ever merge into a completed code block.
 */
function findBoundaries(text, fromOffset) {
  const boundaries = [];
  const n = text.length;
  let inFence = false;
  let fenceMarker = '';
  let i = fromOffset;

  while (i < n) {
    const nl = text.indexOf('\n', i);
    const lineEnd = nl === -1 ? n : nl;
    const afterNl = nl === -1 ? n : nl + 1;
    const line = text.slice(i, lineEnd);
    const fence = line.match(FENCE_RE);

    if (fence) {
      const marker = fence[1];
      if (!inFence) {
        inFence = true;
        fenceMarker = marker;
      } else if (
        marker[0] === fenceMarker[0] &&
        marker.length >= fenceMarker.length &&
        fence[2].trim() === '' // a closing fence carries no info string
      ) {
        inFence = false;
        fenceMarker = '';
        boundaries.push({ offset: afterNl, afterClosedFence: true });
      }
      i = afterNl;
    } else if (!inFence && line.trim() === '') {
      // Consume the entire run of blank lines; the boundary is the start of the
      // next non-blank line so the finalized side owns the separator and the tail
      // starts clean.
      let j = afterNl;
      while (j < n) {
        const nl2 = text.indexOf('\n', j);
        const lineEnd2 = nl2 === -1 ? n : nl2;
        if (text.slice(j, lineEnd2).trim() !== '') break;
        if (nl2 === -1) {
          j = n;
          break;
        }
        j = nl2 + 1;
      }
      boundaries.push({ offset: j, afterClosedFence: false });
      i = j;
    } else {
      i = afterNl;
    }
  }

  return { boundaries, inFence };
}

/**
 * Does cutting between `before` and `after` leave the rendered output unchanged?
 * This is the self-verifying safety check: it directly compares rendering the two
 * sides separately against rendering them joined, so constructs that span the cut
 * (loose lists, setext headings, lazy blockquote continuations, tables) are caught
 * with no hand-coded grammar rules.
 *
 * Renderer non-determinism (e.g. mermaid ids seeded with Date.now()) can only make
 * this return a false negative, never a false positive — so the bias is always
 * toward under-finalizing, which is the safe direction.
 */
function cutIsRenderSafe(before, after, render) {
  return render(before) + render(after) === render(before + after);
}

/**
 * Return how many leading characters of `text` can be safely finalized, scanning
 * forward from `committedLen` (the amount already finalized).
 *
 * Guarantees `render(text.slice(0, n)) + render(text.slice(n)) === render(text)`,
 * and `committedLen <= n <= text.length`.
 *
 * @param {string} text       Full markdown accumulated so far.
 * @param {(src:string)=>string} render  Canonical markdown renderer.
 * @param {number} [committedLen=0]  Characters already finalized (always a prior boundary).
 * @returns {number}
 */
export function splitFinalized(text, render, committedLen = 0) {
  const { boundaries } = findBoundaries(text, committedLen);

  let best = committedLen;
  let segStart = committedLen;

  for (let k = 0; k < boundaries.length; k++) {
    const { offset, afterClosedFence } = boundaries[k];

    if (afterClosedFence) {
      // A completed code block — always safe to freeze through here.
      best = offset;
    } else {
      // A prose/list/table boundary. We need a following block to compare
      // against (the last block must stay live, it can still grow), and the cut
      // must be render-equivalent locally.
      const nextOffset = k + 1 < boundaries.length ? boundaries[k + 1].offset : text.length;
      const before = text.slice(segStart, offset);
      const after = text.slice(offset, nextOffset);
      if (after.trim() !== '' && cutIsRenderSafe(before, after, render)) {
        best = offset;
      }
    }
    segStart = offset;
  }

  return best;
}

/**
 * If `text` begins with a fenced-code opener whose fence never closes, describe it
 * so the renderer can stream the code in append-mode instead of re-rendering it.
 * Returns `{ lang, contentStart }` (contentStart = offset of the first code char),
 * or null when `text` does not start with a still-open fence.
 *
 * The opener line must be complete (terminated by a newline) so the info string /
 * language is known before append-mode begins.
 */
export function describeOpenFence(text) {
  const open = text.match(/^( {0,3})(`{3,}|~{3,})([^\n]*)\n/);
  if (!open) return null;
  const marker = open[2];
  const contentStart = open[0].length;

  for (let i = contentStart; i < text.length; ) {
    const nl = text.indexOf('\n', i);
    const line = text.slice(i, nl === -1 ? text.length : nl);
    const close = line.match(/^ {0,3}(`{3,}|~{3,})\s*$/);
    if (close && close[1][0] === marker[0] && close[1].length >= marker.length) {
      return null; // the fence closes — let the normal finalize path handle it
    }
    if (nl === -1) break;
    i = nl + 1;
  }

  const lang = (open[3] || '').trim().split(/\s+/)[0] || '';
  return { lang, contentStart };
}
