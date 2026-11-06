// Pure progress parsing plus a small DOM updater for Cookbook download badges.
// Kept outside cookbookRunning.js so the behavior can be exercised under Node
// without loading the browser-only Cookbook modules.

export function downloadProgressPercent(progress) {
  const match = String(progress || '').match(/(\d+(?:\.\d+)?)%/);
  if (!match) return 0;
  return Math.min(100, Math.max(0, Number(match[1]) || 0));
}

export function setDownloadProgressBadge(badge, text, percent = null) {
  const progress = percent == null
    ? downloadProgressPercent(text)
    : Math.min(100, Math.max(0, Number(percent) || 0));
  badge.textContent = text;
  badge.className = 'cookbook-task-status cookbook-task-downloading';
  badge.style.setProperty('--download-progress', `${progress}%`);
}
