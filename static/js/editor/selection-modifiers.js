export function selectionModeForEvent(event, fallback = 'replace') {
  if (event.shiftKey && event.altKey) return 'intersect';
  if (event.shiftKey) return 'add';
  if (event.altKey) return 'subtract';
  return fallback;
}
