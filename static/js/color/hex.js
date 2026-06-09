// static/js/color/hex.js
//
// Parse a CSS hex color into {r, g, b}. Pure — no DOM — so it can be reused
// across modules and unit-tested under node.

// Accepts "#rgb", "#rrggbb" (with or without the leading '#'). Returns null
// for anything that isn't a valid 3- or 6-digit hex color.
export function hexToRgb(hex) {
  let h = String(hex || '').trim().replace(/^#/, '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  const n = parseInt(h, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}
