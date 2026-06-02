// static/js/docGutterSync.js
/**
 * Pure scroll mapping for the Documents editor line-number gutter.
 *
 * The gutter renders with `white-space: pre` (one row per logical line, never
 * wraps) while the textarea uses `white-space: pre-wrap` (long lines wrap onto
 * extra visual rows). So as soon as any line wraps, the textarea's scrollable
 * height exceeds the gutter's. Setting `gutter.scrollTop = textarea.scrollTop`
 * then pins the gutter at its own (smaller) maximum for the whole final
 * stretch of a long document — the numbers visibly freeze before the content
 * stops scrolling (issue #1496).
 *
 * Mapping the textarea's scroll *ratio* onto the gutter's own scrollable range
 * keeps the two aligned at both ends and moving together in between, no matter
 * how much the content wraps. When nothing wraps the two ranges are equal and
 * the mapping is the identity, so single-line files behave exactly as before.
 */
export function gutterScrollTop(taScrollTop, taScrollHeight, taClientHeight, gScrollHeight, gClientHeight) {
  const taMax = taScrollHeight - taClientHeight;
  const gMax = gScrollHeight - gClientHeight;
  if (taMax <= 0 || gMax <= 0) return 0;
  const ratio = Math.min(1, Math.max(0, taScrollTop / taMax));
  return ratio * gMax;
}
