// static/js/markdown/tableRow.js
//
// Pure helper for splitting a markdown table row into cells. No DOM —
// safe to import anywhere and to unit-test under node.

// Split a "| a | b | c |" row into trimmed cell strings.
export function splitTableRow(row) {
  return (row || '')
    .split('|')
    .filter((cell) => cell.trim() !== '')
    .map((cell) => cell.trim());
}
