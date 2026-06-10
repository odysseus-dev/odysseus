"""Pin the double-Shift detector that opens the command palette.

The decision logic is the pure exported helper `_shiftPulse(state, event, now)`
in static/js/keyboard-shortcuts.js, driven here through
`node --input-type=module` (same convention as test_keybind_altgr_js.py).
Skips when `node` is not installed.

False-positive matrix pinned here: repeat keys, alt/ctrl/meta-held shifts
(alt+shift+t TTS), IME composition, blur between presses, missing keyup of
the first Shift (held-Shift capitals), and intervening non-Shift keys must
all NOT open; only two clean Shift taps within the window do.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "keyboard-shortcuts.js"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _pulse_sequence(events):
    """Feed [(event_dict, now_ms), ...] through _shiftPulse; return [open, ...].

    'reset' as an event means the blur/visibilitychange handler ran (state
    cleared to null), mirroring the wiring in initKeyboardShortcuts.
    """
    js = f"""
    import {{ _shiftPulse }} from '{_HELPER.as_uri()}';
    const seq = {json.dumps(events)};
    let state = null;
    const opens = [];
    for (const [ev, now] of seq) {{
      if (ev === 'reset') {{ state = null; opens.push(false); continue; }}
      const r = _shiftPulse(state, ev, now);
      state = r.state;
      opens.push(r.open);
    }}
    console.log(JSON.stringify(opens));
    """
    return json.loads(_run(js))


def _sd(code="ShiftLeft", **kw):
    """Shift keydown event."""
    return {"type": "keydown", "code": code, "repeat": False,
            "altKey": False, "ctrlKey": False, "metaKey": False,
            "isComposing": False, **kw}


def _su(code="ShiftLeft"):
    """Shift keyup event."""
    return {"type": "keyup", "code": code}


def _kd(code="KeyA", **kw):
    """Non-shift keydown."""
    return {"type": "keydown", "code": code, "repeat": False,
            "altKey": False, "ctrlKey": False, "metaKey": False,
            "isComposing": False, **kw}


# --- the happy path -----------------------------------------------------------

def test_two_clean_taps_within_window_open():
    opens = _pulse_sequence([
        [_sd(), 0], [_su(), 50], [_sd(), 200],
    ])
    assert opens == [False, False, True]


def test_left_then_right_shift_opens():
    opens = _pulse_sequence([
        [_sd("ShiftLeft"), 0], [_su("ShiftLeft"), 50], [_sd("ShiftRight"), 200],
    ])
    assert opens[-1] is True


def test_state_resets_after_firing():
    # A third tap right after a successful open must NOT immediately re-open
    # (fresh cycle required: it only records a new first tap).
    opens = _pulse_sequence([
        [_sd(), 0], [_su(), 50], [_sd(), 200], [_su(), 250], [_sd(), 300],
    ])
    assert opens == [False, False, True, False, False]


# --- false-positive guard matrix -------------------------------------------------

def test_taps_outside_window_do_not_open():
    opens = _pulse_sequence([
        [_sd(), 0], [_su(), 50], [_sd(), 500],
    ])
    assert opens == [False, False, False]


def test_repeat_keydown_does_not_open():
    # Held Shift auto-repeats in some browsers; repeat events reset the cycle.
    opens = _pulse_sequence([
        [_sd(), 0], [_su(), 50], [_sd(repeat=True), 200],
    ])
    assert opens == [False, False, False]


def test_non_shift_key_between_taps_resets():
    opens = _pulse_sequence([
        [_sd(), 0], [_su(), 20], [_kd("KeyA"), 100], [_sd(), 200],
    ])
    assert opens == [False, False, False, False]


def test_alt_held_shift_does_not_open():
    # Protects alt+shift+t (TTS): shift keydown with alt held resets.
    opens = _pulse_sequence([
        [_sd(altKey=True), 0], [_su(), 50], [_sd(altKey=True), 200],
    ])
    assert opens == [False, False, False]


def test_ctrl_and_meta_held_shift_do_not_open():
    for mod in ("ctrlKey", "metaKey"):
        opens = _pulse_sequence([
            [_sd(**{mod: True}), 0], [_su(), 50], [_sd(**{mod: True}), 200],
        ])
        assert opens[-1] is False, mod


def test_ime_composition_does_not_open():
    opens = _pulse_sequence([
        [_sd(isComposing=True), 0], [_su(), 50], [_sd(isComposing=True), 200],
    ])
    assert opens == [False, False, False]


def test_blur_between_presses_resets():
    # Alt-tab between the two presses: blur/visibilitychange clears state.
    opens = _pulse_sequence([
        [_sd(), 0], [_su(), 50], ["reset", 100], [_sd(), 200],
    ])
    assert opens == [False, False, False, False]


def test_no_keyup_of_first_shift_does_not_open():
    # Shift held (capitals) + second shift key pressed: first never released.
    opens = _pulse_sequence([
        [_sd("ShiftLeft"), 0], [_sd("ShiftRight"), 200],
    ])
    assert opens == [False, False]


def test_shift_during_capital_typing_does_not_open():
    # Shift down, type 'A' (resets), shift up, then a single fresh tap later —
    # one tap alone never opens.
    opens = _pulse_sequence([
        [_sd(), 0], [_kd("KeyA"), 30], [_su(), 60], [_sd(), 150],
    ])
    assert opens == [False, False, False, False]
