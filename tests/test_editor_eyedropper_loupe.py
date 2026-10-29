"""The eyedropper shows a floating loupe next to the pointer while sampling.

The options bar already carries a live pixel preview (#ge-eyedropper-loupe).
When the eyedropper is pressed the same preview should also pop up next to the
mouse, so you can see the exact pixel being sampled.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOOL = (ROOT / "static/js/editor/tools/eyedropper.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_eyedropper_passes_the_event_to_the_preview():
    assert "syncPreview?.(result?.color || null, result, e);" in TOOL
    assert "syncPreview?.(state.color, result, e);" in TOOL
    assert "syncPreview?.(null, null, null);" in TOOL


def test_editor_builds_a_floating_cursor_loupe():
    assert "el.className = 'ge-eyedropper-cursor-loupe';" in EDITOR
    assert "function _drawEyedropperLoupe(canvas, sample)" in EDITOR
    assert "_cursorLoupeEl.classList.toggle('is-visible', _cursorLoupeVisible);" in EDITOR


def test_cursor_loupe_shows_on_press_and_hides_on_release():
    assert "if (state.tool === 'eyedropper') { _setCursorLoupe(true, e); return _eyedropperTool.pick(e); }" in EDITOR
    assert "if (_cursorLoupeVisible) _setCursorLoupe(false);" in EDITOR


def test_cursor_loupe_css_is_a_pointer_tracking_circle():
    j = CSS.find(".ge-eyedropper-cursor-loupe {")
    assert j != -1
    block = CSS[j:j + 500]
    assert "position: fixed;" in block
    assert "pointer-events: none;" in block
    assert "border-radius: 50%;" in block
    assert ".ge-eyedropper-cursor-loupe.is-visible {" in CSS


def test_sw_version_is_current():
    m = re.search(r"CACHE_NAME = '(odysseus-v\d+-pr40-editor-precache)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
