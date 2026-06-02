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
//
// IS_MAC covers all Apple platforms, iPad/iPhone included: a Magic Keyboard's
// Option key sets AltGraph exactly like a Mac's, so they need the same carve-out
// — narrowing to macOS-only would re-break them. The name and the
// /Mac|iPhone|iPad/ test deliberately mirror the existing isMac checks in
// calendar.js and sessions.js; this is their single shared source of truth.
export const IS_MAC =
  /Mac|iPhone|iPad/.test((typeof navigator !== 'undefined' && navigator.platform) || '') ||
  /Mac/.test((typeof navigator !== 'undefined' && navigator.userAgent) || '');

// True when `e` is an AltGr keystroke we should ignore for Ctrl+Alt shortcut
// purposes. getModifierState('AltGraph') is true for AltGr but false for a
// genuine left Ctrl+Alt, so real shortcuts still work. Always false on macOS,
// where Option legitimately sets AltGraph.
//
// We also require ctrlKey+altKey: the collision we defend against is precisely
// "AltGr reported AS Ctrl+Alt", so an event that asserts AltGraph WITHOUT
// presenting as Ctrl+Alt (a Linux ISO_Level3_Shift layout, a stray modifier
// state) is left alone instead of being swallowed.
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
  return (
    !isMac &&
    !!e.ctrlKey &&
    !!e.altKey &&
    !!(e.getModifierState && e.getModifierState('AltGraph'))
  );
}
