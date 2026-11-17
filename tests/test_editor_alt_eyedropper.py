"""Holding Alt over a paint tool borrows the eyedropper (Photoshop parity)."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EYE = (ROOT / "static/js/editor/tools/eyedropper.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_alt_eyedropper_tool_list_excludes_clone_and_heal():
    assert (
        "export const ALT_EYEDROPPER_TOOLS = new Set(['brush', 'pixel', 'eraser', 'smudge', 'dodge', 'burn', 'gradient', 'bucket']);"
        in EYE
    )
    listed = EYE.split("ALT_EYEDROPPER_TOOLS = new Set([")[1].split("]);")[0]
    # Alt already picks the clone source, so clone/heal must not borrow.
    assert "clone" not in listed
    assert "heal" not in listed


def test_begin_draw_borrows_the_eyedropper_on_alt():
    assert "ALT_EYEDROPPER_TOOLS as _altEyedropperTools" in EDITOR
    assert "if (e.altKey && _altEyedropperTools.has(state.tool)) {" in EDITOR
    assert "_eyedropperTool.pick(e);" in EDITOR


def test_alt_held_switches_the_cursor_and_hides_the_ring():
    assert "const _syncAltEyedropper = (on) => {" in EDITOR
    assert "document.body.classList.toggle('ge-alt-eyedropper', !!on && _altEyedropperTools.has(state.tool));" in EDITOR
    assert "document.addEventListener('keydown', (ev) => { if (state.editorOpen) _syncAltEyedropper(ev.altKey); });" in EDITOR
    assert "document.addEventListener('keyup', (ev) => { if (state.editorOpen) _syncAltEyedropper(ev.altKey); });" in EDITOR
    assert "window.addEventListener('blur', () => _syncAltEyedropper(false));" in EDITOR
    assert "body.ge-alt-eyedropper .ge-main-canvas {\n  cursor: url(\"data:image/svg+xml;utf8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22" in CSS
    assert "2 22, crosshair !important;" in CSS
    assert "body.ge-alt-eyedropper .ge-brush-cursor {\n  display: none !important;\n}" in CSS


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
