"""Marker tests for the large-document filter busy indicator.

Heavy filters (e.g. on an 8000x6000 image) can take ~2s of synchronous work;
the editor now shows a busy veil while that final render runs.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/galleryEditor.js").read_text()
CSS = (ROOT / "static/css/13-image-editor.css").read_text()


def test_busy_helpers_exist():
    assert "function _showFilterBusy(label) {" in JS
    assert "async function _hideFilterBusy() {" in JS
    assert "veil.className = 'ge-busy-veil';" in JS


def test_heavy_filter_guards_the_final_render():
    assert "const _heavyFilter = (w * h) > 1500000;" in JS
    assert "_showFilterBusy(label);" in JS
    assert "if (_heavyFilter) await _hideFilterBusy();" in JS


def test_busy_veil_has_a_minimum_visible_and_idle_tail():
    # The heavy render is synchronous and blocks the main thread, so the veil
    # must stay up for a minimum wall-clock time and leave an idle tail after
    # the render returns, otherwise it is never repainted or observable.
    assert "const FILTER_BUSY_MIN_MS = 600;" in JS
    assert "const FILTER_BUSY_TAIL_MS = 200;" in JS
    assert "_filterBusyShownAt = (typeof performance !== 'undefined' ? performance.now() : Date.now());" in JS
    assert "Math.max(FILTER_BUSY_MIN_MS - (now - _filterBusyShownAt), FILTER_BUSY_TAIL_MS)" in JS


def test_busy_veil_css_present():
    assert ".ge-busy-veil {" in CSS
    assert ".ge-busy-veil.is-on { display: flex; }" in CSS
    assert ".ge-busy-spin {" in CSS
    assert "@keyframes ge-busy-spin" in CSS
