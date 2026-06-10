"""Pin the AltGr-safety of the shared keybind predicate and the matcher.

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same approach as test_compare_js.py /
test_reply_recipients_js.py). Skips when `node` is not installed rather than
failing.

Bug: browsers report the AltGr key (right Alt, essential on AZERTY/QWERTZ and
many non-US layouts to type @ # { } [ ] | \\ and €) as ctrlKey=true AND
altKey=true, so a user on a non-US layout typing a special character could
silently fire a destructive ctrl+alt+<letter> default (new_session,
delete_session, incognito, open_calendar). getModifierState('AltGraph') is true
for AltGr but false for a genuine left Ctrl+Alt — except on macOS, where the
Option key also sets it.

The guard now lives in ONE place — `isAltGrEvent` in static/js/platform.js — and
all three call sites (editor keyboard-shortcuts.js, root keyboard-shortcuts.js,
settings.js) route through it. So these tests pin the shared *predicate*
directly (both the isMac arg and the navigator-derived IS_MAC default), plus the
`_matchesCombo` integration. They do NOT prove that real browsers actually set
AltGraph for AltGr — that mapping is taken from the UI Events spec / MDN; older
Firefox and some Linux setups historically did not report it (the guard is a
no-op there, i.e. pre-fix behaviour, not a regression).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "keyboard-shortcuts.js"
_PLATFORM = _REPO / "static" / "js" / "platform.js"
_HAS_NODE = shutil.which("node") is not None

# Every test here shells out to `node`; skip the whole module when it is absent
# rather than repeating the mark per test (same convention as test_compare_js.py
# / test_reply_recipients_js.py).
pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _is_altgr(
    altgraph: bool,
    is_mac: bool = False,
    has_modifier_state: bool = True,
    ctrl: bool = True,
    alt: bool = True,
) -> bool:
    """Return isAltGrEvent(ev, is_mac) — the predicate every guard routes through."""
    modifier = (
        f"ev.getModifierState = (m) => m === 'AltGraph' ? {json.dumps(altgraph)} : false;"
        if has_modifier_state else "")
    js = f"""
    import {{ isAltGrEvent }} from '{_PLATFORM.as_uri()}';
    const ev = {{ ctrlKey: {json.dumps(ctrl)}, altKey: {json.dumps(alt)} }};
    {modifier}
    console.log(JSON.stringify(isAltGrEvent(ev, {json.dumps(is_mac)})));
    """
    return json.loads(_run(js))


def _is_mac_default(platform: str = "", user_agent: str = "") -> bool:
    """Return platform.js IS_MAC as derived from a stubbed navigator at import time."""
    # Node >=21 exposes a read-only global `navigator`, so assignment throws;
    # defineProperty (configurable) overrides it for the import-time read.
    js = f"""
    Object.defineProperty(globalThis, 'navigator', {{
      value: {{ platform: {json.dumps(platform)}, userAgent: {json.dumps(user_agent)} }},
      configurable: true,
    }});
    const {{ IS_MAC }} = await import('{_PLATFORM.as_uri()}');
    console.log(JSON.stringify(IS_MAC));
    """
    return json.loads(_run(js))


def _matches(event: dict, combo: str, altgraph: bool, is_mac: bool = False) -> bool:
    """Return _matchesCombo(event, combo, is_mac) with AltGraph active or not."""
    js = f"""
    import {{ _matchesCombo }} from '{_HELPER.as_uri()}';
    const ev = {json.dumps(event)};
    ev.getModifierState = (m) => m === 'AltGraph' ? {json.dumps(altgraph)} : false;
    console.log(JSON.stringify(_matchesCombo(ev, {json.dumps(combo)}, {json.dumps(is_mac)})));
    """
    return json.loads(_run(js))


# --- The shared predicate (covers all three guards) --------------------------

def test_isaltgr_true_for_altgr_keystroke_off_mac():
    # AZERTY/QWERTZ user holds AltGr: browser sets ctrlKey+altKey+AltGraph.
    assert _is_altgr(altgraph=True, is_mac=False) is True


def test_isaltgr_false_for_genuine_ctrl_alt():
    # A real left Ctrl+Alt press leaves AltGraph unset.
    assert _is_altgr(altgraph=False, is_mac=False) is False


def test_isaltgr_false_when_altgraph_set_but_not_ctrl_alt():
    # The collision we defend against is specifically "AltGr reported AS
    # Ctrl+Alt". An event that asserts AltGraph WITHOUT presenting as Ctrl+Alt
    # (e.g. a Linux ISO_Level3_Shift layout, or a stray modifier state) must NOT
    # be swallowed — only a genuine Ctrl+Alt-presenting AltGr keystroke is.
    assert _is_altgr(altgraph=True, ctrl=False, alt=False) is False
    assert _is_altgr(altgraph=True, ctrl=True, alt=False) is False
    assert _is_altgr(altgraph=True, ctrl=False, alt=True) is False


def test_isaltgr_false_on_mac_even_with_altgraph():
    # macOS reports AltGraph=true for the Option key, but Ctrl+Option / Cmd+Option
    # are legitimate Mac shortcuts, so the predicate must never swallow them.
    assert _is_altgr(altgraph=True, is_mac=True) is False


def test_isaltgr_false_when_getmodifierstate_missing():
    # Defensive: an event without getModifierState must not throw or report AltGr.
    assert _is_altgr(altgraph=False, is_mac=False, has_modifier_state=False) is False


# --- The navigator-derived IS_MAC default (dead in node without a stub) -------

def test_is_mac_from_navigator_platform():
    # navigator.platform reports "MacIntel" on EVERY Mac — Apple Silicon
    # (M1/M2/M3...) included; the string was frozen for compatibility, so there
    # is no "MacARM". The regex matches the "Mac" substring, not "Intel".
    assert _is_mac_default(platform="MacIntel") is True


def test_is_mac_apple_silicon_reports_macintel():
    # Pin the quirk explicitly: an Apple Silicon Mac's UA still says Macintosh
    # and its platform still says MacIntel, so the carve-out protects it too.
    assert _is_mac_default(
        platform="MacIntel",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    ) is True


def test_is_mac_from_user_agent_when_platform_blank():
    # iPadOS / some browsers report a Mac userAgent with an unhelpful platform.
    assert _is_mac_default(platform="", user_agent="Mozilla/5.0 (Macintosh; ...)") is True


def test_is_not_mac_on_windows():
    assert _is_mac_default(platform="Win32", user_agent="Mozilla/5.0 (Windows NT 10.0)") is False


# --- _matchesCombo integration (the matcher predicate, end to end) -----------

def test_altgr_keystroke_does_not_trigger_ctrl_alt_shortcut():
    # AZERTY/QWERTZ user holds AltGr over a key that yields 'n'. This must NOT
    # fire the destructive new_session combo.
    ev = {"ctrlKey": True, "altKey": True, "shiftKey": False, "key": "n"}
    assert _matches(ev, "ctrl+alt+n", altgraph=True, is_mac=False) is False


def test_genuine_ctrl_alt_still_matches():
    # A real left Ctrl+Alt press (AltGraph not set) must still work.
    ev = {"ctrlKey": True, "altKey": True, "shiftKey": False, "key": "n"}
    assert _matches(ev, "ctrl+alt+n", altgraph=False, is_mac=False) is True


def test_mac_option_combo_still_matches():
    # macOS reports AltGraph=true for the Option key, but Ctrl+Option / Cmd+Option
    # are legitimate Mac shortcuts. On macOS the guard must NOT swallow them.
    ev = {"ctrlKey": True, "altKey": True, "shiftKey": False, "key": "n"}
    assert _matches(ev, "ctrl+alt+n", altgraph=True, is_mac=True) is True


def test_plain_ctrl_shortcut_unaffected():
    # Non-alt combos were never AltGr-ambiguous and must keep matching.
    ev = {"ctrlKey": True, "altKey": False, "shiftKey": False, "key": "k"}
    assert _matches(ev, "ctrl+k", altgraph=False, is_mac=False) is True


# --- The remaining Ctrl/Cmd-key handlers route through the shared guard -------
#
# Follow-up to the original AltGr fix: four more keydown handlers gated only on
# `(e.ctrlKey || e.metaKey)` could false-fire on the no-glyph AltGr keystroke the
# same way. They live as inline, DOM-coupled listeners inside large modules
# (document.js / notes.js / calendar.js) with no `node` unit harness — so, exactly
# like test_document_deeplink.py, we pin the source-level invariant that each one
# is wired through `isAltGrEvent`. The PREDICATE's actual AltGr/macOS/no-AltGraph
# behaviour is what is proven against real JS by the node-driven tests above
# (_is_altgr / _matches); these pins only assert the four call sites delegate to
# it, so a guard can't be silently dropped or forgotten on one of the siblings.
# notes.js Ctrl+C is deliberately NOT rewired: its pre-existing `&& !e.altKey`
# already excludes AltGr, and swapping it would loosen Mac Cmd+Option+C — so we
# pin that it stays put.
#
# Matching is whitespace-normalised (see `_wired`): a cosmetic reflow — a
# line-wrap, an indent change, extra spaces around an operator — must not fail a
# pin while the guard is still in place. Reordering the operands WOULD break the
# match, which is intended: that is a semantic edit and the point where a
# maintainer should re-confirm the condition still does what the pin claims.

_DOCUMENT_JS = _REPO / "static" / "js" / "document.js"
_NOTES_JS = _REPO / "static" / "js" / "notes.js"
_CALENDAR_JS = _REPO / "static" / "js" / "calendar.js"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(s: str) -> str:
    """Collapse runs of whitespace to a single space so source pins survive a
    cosmetic reflow but still catch a real change to the guard expression."""
    return re.sub(r"\s+", " ", s).strip()


def _wired(path: Path, snippet: str) -> bool:
    return _norm(snippet) in _norm(_src(path))


def test_document_js_imports_shared_altgr_guard():
    assert _wired(_DOCUMENT_JS, "import { isAltGrEvent } from './platform.js';")


def test_notes_js_imports_shared_altgr_guard():
    assert _wired(_NOTES_JS, "import { isAltGrEvent } from './platform.js';")


def test_calendar_js_imports_shared_altgr_guard():
    assert _wired(_CALENDAR_JS, "import { isAltGrEvent } from './platform.js';")


def test_markdown_format_shortcut_guards_altgr():
    # Ctrl+B/I/K markdown formatting fires inside the doc-editor textarea while
    # typing; AltGr+b/i/k produce no glyph on common non-US layouts, so without
    # the guard prose typing would wrap the selection in markdown markup.
    assert _wired(
        _DOCUMENT_JS,
        "lang === 'markdown' && (e.ctrlKey || e.metaKey) && !isAltGrEvent(e)",
    )


def test_doc_find_shortcut_guards_altgr():
    # Ctrl+F find-bar; preventDefault+stopPropagation would otherwise eat the key.
    assert _wired(_DOCUMENT_JS, "(e.ctrlKey || e.metaKey) && !isAltGrEvent(e) && e.key === 'f'")


def test_notes_undo_shortcut_guards_altgr():
    # Ctrl+Z note-undo (the inField early-return already blunts most exposure,
    # but the guard keeps it consistent with its siblings).
    assert _wired(
        _NOTES_JS,
        "(e.ctrlKey || e.metaKey) && !isAltGrEvent(e) && (e.key === 'z' || e.key === 'Z') && !e.shiftKey",
    )


def test_calendar_undo_shortcut_guards_altgr():
    # Calendar Ctrl+Z undo — same document-level, inField-gated handler as notes.js
    # Ctrl+Z, but written as an early-return, so the guard sits in the bail clause.
    assert _wired(
        _CALENDAR_JS,
        "!(e.ctrlKey || e.metaKey) || isAltGrEvent(e) || e.key !== 'z' || e.shiftKey",
    )


def test_notes_copy_shortcut_keeps_existing_altkey_guard():
    # Intentionally untouched: !e.altKey already excludes the AltGr no-glyph case,
    # and switching to isAltGrEvent would re-enable Cmd+Option+C on macOS.
    assert _wired(
        _NOTES_JS,
        "(e.ctrlKey || e.metaKey) && (e.key === 'c' || e.key === 'C') && !e.shiftKey && !e.altKey",
    )
