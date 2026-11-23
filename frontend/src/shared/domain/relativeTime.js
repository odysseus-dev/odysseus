// Format the common sub-day portion of past-age labels. Callers own older dates.
export function formatRecentAge(seconds, justNowSeconds = 60) {
  if (seconds < justNowSeconds) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return null;
}

// The conversation archive and library use minute precision and local dates after 30 days.
export function formatArchiveAge(value, now) {
  if (!value) return '';
  const date = new Date(value);
  const minutes = Math.floor((now - date.getTime()) / 60000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return date.toLocaleDateString();
}
