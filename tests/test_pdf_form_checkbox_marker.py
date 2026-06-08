"""Unchecked AcroForm checkboxes must render as `[ ]`, not `[x]`.

PyMuPDF reports an unchecked checkbox's value as the reserved "Off" state — a
non-empty string. `_checkbox_marker` used a plain truthiness test, so every
unchecked box rendered as ticked and exported as checked (silent form-data
corruption).
"""
from src.pdf_form_doc import _checkbox_marker, _format_field_bullet


def test_off_states_render_unchecked():
    for off in ("Off", "/Off", "off", "", None):
        assert _checkbox_marker(off) == "[ ]", off


def test_on_states_render_checked():
    for on in ("Yes", "On", "/Yes", "Export", True):
        assert _checkbox_marker(on) == "[x]", on


def test_unchecked_checkbox_field_bullet():
    f = {"name": "agree", "label": "I agree", "type": "checkbox", "value": "Off"}
    line = _format_field_bullet(f)
    assert line.startswith("- [ ]")
    assert "type=checkbox" in line


def test_checked_checkbox_field_bullet():
    f = {"name": "agree", "label": "I agree", "type": "checkbox", "value": "Yes"}
    line = _format_field_bullet(f)
    assert line.startswith("- [x]")
