// static/js/color/contrast.js
//
// WCAG contrast helpers and the primary-button colors derived from a theme
// accent. Pure — no DOM — so they can be unit-tested under node.

import { hexToRgb } from './hex.js';

const AA = 4.5;
const WHITE = '#ffffff';
const INK = '#111111';

function _luminance(hex) {
  const { r, g, b } = hexToRgb(hex) || { r: 0, g: 0, b: 0 };
  const [lr, lg, lb] = [r, g, b].map(v => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
}

export function contrastRatio(a, b) {
  const la = _luminance(a), lb = _luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// `weight` of `a` mixed with `b`, like color-mix(in srgb, a weight, b).
function _mix(a, b, weight) {
  const x = hexToRgb(a) || { r: 0, g: 0, b: 0 };
  const y = hexToRgb(b) || { r: 0, g: 0, b: 0 };
  const ch = k => Math.round(x[k] * weight + y[k] * (1 - weight)).toString(16).padStart(2, '0');
  return '#' + ch('r') + ch('g') + ch('b');
}

// Starting at `weight`, push the accent toward `away` until `ink` clears AA.
function _clearFill(accent, ink, away, weight) {
  for (let w = weight; w > 0; w -= 0.02) {
    const fill = _mix(accent, away, w);
    if (contrastRatio(fill, ink) >= AA) return fill;
  }
  return away;
}

// Text-bearing primary buttons: a white label on the accent darkened to 78%
// where that clears AA, otherwise (bright accents) a dark label on the plain
// accent. Hover moves further from the label, so it never loses contrast.
export function accentButtonColors(accent) {
  const white = contrastRatio(_mix(accent, '#000000', 0.78), WHITE) >= AA;
  const ink = white ? WHITE : INK;
  const away = white ? '#000000' : WHITE;
  return {
    ink,
    fill: _clearFill(accent, ink, away, white ? 0.78 : 1),
    hover: _clearFill(accent, ink, away, white ? 0.70 : 0.88),
  };
}
