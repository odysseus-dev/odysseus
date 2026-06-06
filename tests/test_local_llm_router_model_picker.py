"""Auto-stack model picker display helpers."""

from pathlib import Path


def test_model_picker_tracks_auto_resolved_display():
    source = Path("static/js/modelPicker.js").read_text(encoding="utf-8")
    assert "noteAutoResolvedModel" in source
    assert "\\u2192" in source or "→" in source
    assert "${AUTO_SELECT_LABEL}" in source
    assert "_lastAutoResolvedModel" in source
    assert "_lastAutoRouteReasons" in source


def test_chat_wires_note_auto_resolved():
    source = Path("static/js/chat.js").read_text(encoding="utf-8")
    assert "noteAutoResolvedModel" in source
    assert "route_reasons" in source
    assert "requested_model" in Path("routes/chat_routes.py").read_text(encoding="utf-8")
    assert "route_reasons" in Path("routes/chat_routes.py").read_text(encoding="utf-8")
