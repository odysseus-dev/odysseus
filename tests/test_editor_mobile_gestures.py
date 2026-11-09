import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANVAS_EVENTS = (ROOT / 'static/js/editor/canvas-events.js').read_text()
GALLERY_EDITOR = (ROOT / 'static/js/galleryEditor.js').read_text()
POPUPS = (ROOT / 'static/js/editor/build/popups.js').read_text()
SHORTCUTS_POPOVER = (ROOT / 'static/js/editor/shortcuts-popover.js').read_text()
SW = (ROOT / 'static/sw.js').read_text()


def test_gesture_callbacks_are_wired_into_canvas_events():
    assert 'onTwoFingerTap' in CANVAS_EVENTS
    assert 'onThreeFingerTap' in CANVAS_EVENTS
    # The editor supplies undo/redo for the tap gestures.
    assert 'onTwoFingerTap: () => undo(),' in GALLERY_EDITOR
    assert 'onThreeFingerTap: () => redo(),' in GALLERY_EDITOR


def test_two_finger_tap_undo_and_three_finger_tap_redo():
    assert 'const TAP_MAX_MS = 300;' in CANVAS_EVENTS
    assert 'const TAP_MAX_MOVE = 12;' in CANVAS_EVENTS
    assert 'let gesturePeakTouches = 0;' in CANVAS_EVENTS
    # Decision is made when the last finger lifts and the move budget held.
    assert re.search(
        r"const quick = !gestureMoved && \(Date\.now\(\) - gestureStartAt\) <= TAP_MAX_MS;",
        CANVAS_EVENTS,
    )
    assert 'if (peak === 2) onTwoFingerTap?.();' in CANVAS_EVENTS
    assert 'else if (peak >= 3) onThreeFingerTap?.();' in CANVAS_EVENTS


def test_multi_finger_gesture_reverts_the_first_finger_dab():
    # A second finger must cancel (not commit) the accidental stroke so a
    # pinch or a tap never leaves a stray dab.
    assert 'if (!multiActive) cancelDraw?.();' in CANVAS_EVENTS
    assert 'if (!multiActive) endDraw();' not in CANVAS_EVENTS


def test_two_finger_gesture_movement_should_not_be_a_tap():
    assert 'gestureMoved = true;' in CANVAS_EVENTS
    assert 'if (e.touches.length === 0 && gesturePeakTouches >= 2) {' in CANVAS_EVENTS


def test_pinch_zoom_and_two_finger_pan_preserved():
    assert 'const newZoom = Math.max(0.1, Math.min(5, multiStartZoom * ratio));' in CANVAS_EVENTS
    assert 'applyCanvasOffset(multiStartPan.x + dx, multiStartPan.y + dy);' in CANVAS_EVENTS


def test_cheatsheet_lists_touch_gestures():
    assert '<h5>Touch</h5>' in POPUPS
    for line in ('Pinch → Zoom', 'Two-finger drag → Pan', 'Two-finger tap → Undo', 'Three-finger tap → Redo'):
        assert line in POPUPS, line


def test_shortcuts_popover_fits_a_phone_width():
    assert 'min-width:min(540px,92vw)' in SHORTCUTS_POPOVER


def test_sw_cache_name_bumped_for_this_change():
    assert re.search(r'odysseus-v\d+-pr40-editor-precache', SW)
