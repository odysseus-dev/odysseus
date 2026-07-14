// static/js/calendar/utils.js
//
// Pure constants + zero-state helpers for the calendar UI.
// No DOM, no fetch, no global mutable state — safe to import anywhere.

export const WEEKDAYS     = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
export const WEEKDAYS_SUN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];


export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

export const MON_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const CAL_PALETTE = [
  'var(--accent)', '#5b8abf', '#bf6b5b', '#5bbf7a', '#bf9a5b',
  '#9a5bbf', '#5bbfb8', '#bf8a5b', '#7070c0', '#bf5b8a',
];

export const CAL_COLORS = [
  { name: 'default', hex: '' },
  // Pale/pastel palette — softer event tints.
  { name: 'red',     hex: '#f0b5ba' },
  { name: 'orange',  hex: '#e8ccb2' },
  { name: 'yellow',  hex: '#f2dfbd' },
  { name: 'green',   hex: '#cce0bc' },
  { name: 'blue',    hex: '#b0d7f7' },
  { name: 'purple',  hex: '#e2bcee' },
  { name: 'teal',    hex: '#abdbe0' },
  { name: 'pink',    hex: '#f0b5cc' },
  // Custom — mirrors the notes color picker. Clicking opens a file picker
  // and the chosen image URL is stored as a `bg:<url>` sentinel.
  { name: 'custom',  hex: 'custom' },
];

export const _CAL_CUSTOM_GRADIENT = 'conic-gradient(from 0deg, #e06c75, #d19a66, #e5c07b, #98c379, #61afef, #c678dd, #e06c75)';

// Per-event-type accent palette. Used by the colored dots in month/year
// grids and the chip stripe behind agenda rows.
export const _TYPE_PALETTE = {
  '!':      '#e5a33a',  // important — amber, less harsh than red
  work:     '#5b8abf',
  personal: '#a07ae0',
  health:   '#e06c75',
  travel:   '#e5a33a',
  meal:     '#d8b974',
  social:   '#82c882',
  admin:    '#888888',
  other:    '#6b9cb5',
  untagged: '#555',
};

// SVG icon literals reused across the calendar UI.
export const _trashIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
export const _moreIcon  = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
export const _bellIcon  = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';

// ── background CSS helpers ──

export function _isCalBgImage(c) {
  return typeof c === 'string' && c.startsWith('bg:');
}

export function _calBgImageUrl(c) {
  return _isCalBgImage(c) ? c.slice(3) : '';
}

// Returns a value safe to drop into `style="background:..."`. Falls back to
// the calendar default for bg-image events in spots where an image would be
// too small to render usefully (small grid dots, multi-day bars).
export function _calBgCss(c, fallback) {
  if (_isCalBgImage(c)) {
    const u = _calBgImageUrl(c);
    return u ? `center/cover no-repeat url('${u.replace(/'/g, "\\'")}')` : (fallback || 'var(--accent)');
  }
  return c || fallback || 'var(--accent)';
}

function _hexToRgb(c) {
  if (typeof c !== 'string') return null;
  const m = c.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!m) return null;
  const hex = m[1].length === 3
    ? m[1].split('').map(ch => ch + ch).join('')
    : m[1];
  return {
    r: parseInt(hex.slice(0, 2), 16),
    g: parseInt(hex.slice(2, 4), 16),
    b: parseInt(hex.slice(4, 6), 16),
  };
}

function _relativeLuminance({ r, g, b }) {
  return [r, g, b].map(v => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }).reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
}

function _contrastRatio(a, b) {
  const light = Math.max(a, b);
  const dark = Math.min(a, b);
  return (light + 0.05) / (dark + 0.05);
}

export function _calReadableTextColor(bg) {
  const rgb = _hexToRgb(bg);
  if (!rgb) return 'var(--fg)';
  const lum = _relativeLuminance(rgb);
  const white = _contrastRatio(lum, 1);
  const ink = _contrastRatio(lum, 0.006);
  return ink >= white ? '#111820' : '#ffffff';
}

// ── date helpers ──

// `YYYY-MM-DD` string from a Date.
export function _ds(d) {
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

export function _addDays(dateStr, n) {
  if (typeof dateStr !== 'string' || !dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  if (isNaN(d)) return '';
  d.setDate(d.getDate() + n);
  return _ds(d);
}

export function _shiftDT(iso, days) {
  if (typeof iso !== 'string' || !iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  d.setDate(d.getDate() + days);
  return _ds(d) + (iso.length > 10 ? 'T' + iso.slice(11) : '');
}

// Current user's UTC offset as `±HH:MM`. Used to stamp event payloads so
// the backend can interpret naive datetimes in the user's tz.
export function _tzOffset() {
  const o = -new Date().getTimezoneOffset();
  const sign = o >= 0 ? '+' : '-';
  const h = String(Math.floor(Math.abs(o) / 60)).padStart(2, '0');
  const m = String(Math.abs(o) % 60).padStart(2, '0');
  return `${sign}${h}:${m}`;
}

// For naive datetimes (no tz suffix), display the date portion as written —
// TimeTree and many sync tools store "local time" without an offset, so
// re-interpreting them via the user's tz would shift days.
//
// For tz-aware ISO (`Z` or `±HH:MM`), parse as an absolute instant and
// bucket by the USER's local date. Without this an event at
// "2026-05-13T22:00:00Z" (07:00 May 14 JST) would render on May 13.
export function _localDateOf(isoStr) {
  if (typeof isoStr !== 'string' || !isoStr) return '';
  if (isoStr.length === 10) return isoStr;
  if (/[Zz]$|[+\-]\d{2}:?\d{2}$/.test(isoStr)) {
    const d = new Date(isoStr);
    if (!isNaN(d)) {
      const y  = d.getFullYear();
      const m  = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      return `${y}-${m}-${dd}`;
    }
  }
  return isoStr.slice(0, 10);
}
