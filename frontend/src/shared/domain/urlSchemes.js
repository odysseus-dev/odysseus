// Normalize for scheme checks only. Navigation and allowlists belong to callers.
export function compactUrlSchemeValue(value) {
  return String(value || '').replace(/[\u0000-\u0020\u007f-\u009f]+/g, '').toLowerCase();
}
