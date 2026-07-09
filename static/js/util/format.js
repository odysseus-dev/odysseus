/**
 * Formats a large number into a compact "K" or "M" shorthand.
 *
 * Replaces `_fmtCtx(n)` function originating from chatRenderer.js.
 *             Use `formatCompactNumber(n)` instead.
 */
export function formatCompactNumber(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
  return Math.round(n / 1000) + 'K';
}

/**
 * Converts a value to a trimmed string. Nullish values become ''.
 *
 * Replaces `modelValue(name)` function originating from chatRenderer.js.
 *             Use `toTrimmedString(name)` instead.
 */
export function toTrimmedString(name) {
  if (name == null) return '';
  return String(name).trim();
}