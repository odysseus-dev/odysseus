// static/js/markdown/tableRow.js
//
// Pure helper for splitting a markdown table row into cells. No DOM —
// safe to import anywhere and to unit-test under node.

// Split a "| a | b | c |" row into trimmed cell strings.
//
// Strip only the optional leading/trailing pipe, then split — filtering out
// every empty cell (the old behaviour) dropped intentionally-empty interior
// cells too, so "| a |  | c |" collapsed to 2 columns and misaligned with the
// header.
export function splitTableRow(row) {
  return (row || '')
    .replace(/^\s*\|/, '')
    .replace(/\|\s*$/, '')
    .split('|')
    .map((cell) => cell.trim());
}
