"""Ensure per-document raster gradient controls do not leak between projects."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gradient_stops_reset_when_editor_opens_and_closes():
    source = (ROOT / "static/js/galleryEditor.js").read_text()
    open_reset = source.index("state.gradientActive = false;", source.index("export function openEditor"))
    close_reset = source.index("state.gradientActive = false;", source.index("export function closeEditor"))

    assert "state.gradientStops = [];" in source[open_reset:open_reset + 120]
    assert "state.gradientStops = [];" in source[close_reset:close_reset + 120]


def test_gradient_history_is_deferred_until_a_real_drag():
    source = (ROOT / "static/js/editor/tools/gradient.js").read_text()

    begin = source.index("begin(e) {")
    drag = source.index("drag(e) {")
    end = source.index("end(e) {")
    assert "saveState('Gradient');" not in source[begin:drag]
    assert "if (!historySaved)" in source[end:end + 420]
    assert "historySaved = false;" in source[begin:drag]
