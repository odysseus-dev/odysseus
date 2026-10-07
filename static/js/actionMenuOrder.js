/** Stable semantic ordering for item-level action menus. */
const COMMON_ACTION_ORDER = [
  { rank: 200, pattern: /^(rename|change name)\b/i },
  { rank: 650, pattern: /^select\b/i },
  { rank: 400, pattern: /^(favorite|unfavorite|favourite|unfavourite|pin|unpin)\b/i },
  { rank: 500, pattern: /^(copy|clone|duplicate)\b/i },
  { rank: 550, pattern: /^(export|download)\b/i },
  { rank: 900, pattern: /^(delete|remove|hide|dismiss|clear from list|move to trash|move to spam)\b/i },
  { rank: 700, pattern: /^(move to archive|archive|unarchive|restore)\b/i },
  { rank: 600, pattern: /^(move(?! to (?:archive|trash|spam)\b)|add to folder)\b/i },
  { rank: 1000, pattern: /^cancel\b/i },
];

// Canonical Select glyph for item-level menus. Toolbar toggles may use the
// smaller version, but dropdown rows should all use this exact SVG.
export const SELECT_MENU_ICON = '<svg class="memory-select-btn-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/></svg>';

export function actionMenuRank(item) {
  if (Number.isFinite(item?.menuOrder)) return item.menuOrder;
  const value = String(typeof item?.action === 'string' ? item.action : (item?.label || '')).trim();
  return COMMON_ACTION_ORDER.find(entry => entry.pattern.test(value))?.rank ?? 100;
}

export function orderActionMenuItems(items) {
  return items
    .map((item, index) => ({ item, index, rank: actionMenuRank(item) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map(entry => entry.item);
}
