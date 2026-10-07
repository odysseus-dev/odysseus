from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compare_mode_is_transient_and_wired_to_topbar():
    state = (ROOT / "static/js/editor/state.js").read_text()
    topbar = (ROOT / "static/js/editor/build/topbar.js").read_text()
    wiring = (ROOT / "static/js/editor/wire-topbar.js").read_text()
    editor = (ROOT / "static/js/galleryEditor.js").read_text()

    assert "compareBaselineCanvas: null" in state
    assert "compareActive: false" in state
    assert 'id="ge-compare-btn"' in topbar
    assert "aria-pressed=\"false\"" in topbar
    assert "toggleCompare" in wiring
    assert "state.compareBaselineCanvas = document.createElement('canvas')" in editor
    assert "state.compareActive = !state.compareActive" in editor
    assert "state.compareActive && state.compareBaselineCanvas" in editor
    assert "state.compareBaselineCanvas = null" in editor
    assert "state.compareActive = false" in editor
