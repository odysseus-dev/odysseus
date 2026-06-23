// Incremental streaming markdown support.
//
// Re-parsing + re-highlighting the WHOLE accumulated markdown on every streamed
// token is O(N²) and re-creates DOM, which makes code blocks flicker and the
// thread feel janky. The fix (ported from the original streamingSegmenter.js) is
// to FREEZE the leading part of the message that can no longer change and only
// re-render the growing tail.
//
// `splitFinalized(text)` answers: how many leading characters are safe to freeze?
// We drop the original's render-equivalence check (it needs the real renderer);
// instead we freeze at code-fence closes (always safe) and at blank-line block
// boundaries, with conservative guards so a still-streaming loose list / setext
// heading / blockquote is never split mid-construct. The finished message is
// re-rendered from source, so any transient split self-corrects.

const FENCE_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/
const LIST_ITEM_RE = /^ {0,3}([-*+]|\d+[.)])\s/
const SETEXT_RE = /^ {0,3}(=+|-+)\s*$/

type Boundary = { offset: number; afterClosedFence: boolean }

function findBoundaries(text: string, from: number): Boundary[] {
  const boundaries: Boundary[] = []
  const n = text.length
  let inFence = false
  let marker = ""
  let i = from
  while (i < n) {
    const nl = text.indexOf("\n", i)
    const lineEnd = nl === -1 ? n : nl
    const afterNl = nl === -1 ? n : nl + 1
    const line = text.slice(i, lineEnd)
    const fence = line.match(FENCE_RE)
    if (fence) {
      const m = fence[1]
      if (!inFence) { inFence = true; marker = m }
      else if (m[0] === marker[0] && m.length >= marker.length && fence[2].trim() === "") {
        inFence = false; marker = ""
        boundaries.push({ offset: afterNl, afterClosedFence: true })
      }
      i = afterNl
    } else if (!inFence && line.trim() === "") {
      // Consume the whole blank-line run; the boundary is the next non-blank line.
      let j = afterNl
      while (j < n) {
        const nl2 = text.indexOf("\n", j)
        const e2 = nl2 === -1 ? n : nl2
        if (text.slice(j, e2).trim() !== "") break
        if (nl2 === -1) { j = n; break }
        j = nl2 + 1
      }
      boundaries.push({ offset: j, afterClosedFence: false })
      i = j
    } else {
      i = afterNl
    }
  }
  return boundaries
}

function lastNonBlankLine(s: string): string {
  const lines = s.split("\n")
  for (let i = lines.length - 1; i >= 0; i--) if (lines[i].trim() !== "") return lines[i]
  return ""
}
function firstNonBlankLine(s: string): string {
  for (const l of s.split("\n")) if (l.trim() !== "") return l
  return ""
}

/**
 * Return how many leading characters of `text` can be safely frozen. The prefix
 * `text.slice(0, n)` will not change as more tokens arrive (modulo the documented
 * transient cases that the final from-source re-render corrects).
 */
export function splitFinalized(text: string, committedLen = 0): number {
  const boundaries = findBoundaries(text, committedLen)
  let best = committedLen
  let segStart = committedLen
  for (let k = 0; k < boundaries.length; k++) {
    const { offset, afterClosedFence } = boundaries[k]
    if (afterClosedFence) {
      best = offset // a completed code block — always safe to freeze through.
    } else {
      const nextOffset = k + 1 < boundaries.length ? boundaries[k + 1].offset : text.length
      const after = text.slice(offset, nextOffset)
      if (after.trim() !== "") {
        const prevLine = lastNonBlankLine(text.slice(segStart, offset))
        const nextLine = firstNonBlankLine(after)
        // Don't freeze where the construct may continue across the blank line.
        const risky =
          (LIST_ITEM_RE.test(prevLine) && LIST_ITEM_RE.test(nextLine)) ||
          SETEXT_RE.test(nextLine) ||
          (prevLine.trim().startsWith(">") && nextLine.trim().startsWith(">"))
        if (!risky) best = offset
      }
    }
    segStart = offset
  }
  return best
}
