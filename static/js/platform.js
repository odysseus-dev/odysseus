// ============================================
// Platform detection + AltGr-keystroke helper
// ============================================
// Shared by the keybind code: root keyboard-shortcuts.js, the editor's
// keyboard-shortcuts.js, and settings.js. Single source of truth so the three
// guards can't drift.

// AltGr (right Alt on AZERTY/QWERTZ and most non-US layouts, used to type
// @ # { } [ ] | \ and €) is reported by browsers as Ctrl+Alt. macOS is the
// exception: there the Option key — a normal part of Mac shortcuts — also sets
// the AltGraph modifier state, so it must NOT be treated as AltGr.
export const IS_MAC =
  /Mac|iPhone|iPad/.test((typeof navigator !== 'undefined' && navigator.platform) || '') ||
  /Mac/.test((typeof navigator !== 'undefined' && navigator.userAgent) || '');

// True when `e` is an AltGr keystroke we should ignore for Ctrl+Alt shortcut
// purposes. getModifierState('AltGraph') is true for AltGr but false for a
// genuine left Ctrl+Alt, so real shortcuts still work. Always false on macOS,
// where Option legitimately sets AltGraph.
//
// Trade-off: on Windows AltGr *is* Ctrl+right-Alt, so a deliberate
// Ctrl+Alt+<char> shortcut typed via AltGr is unreachable too — accepted; use
// the left Ctrl+Alt.
//
// NOTE: the AltGr -> AltGraph mapping is taken from the UI Events spec / MDN,
// not proven by our tests. Older Firefox and some Linux setups historically did
// not report AltGraph; where a browser sets ctrlKey+altKey without it this
// guard is simply a no-op (the pre-fix behaviour) rather than a regression.
export function isAltGrEvent(e, isMac = IS_MAC) {
  return !isMac && !!(e.getModifierState && e.getModifierState('AltGraph'));
}
