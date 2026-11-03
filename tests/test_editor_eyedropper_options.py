from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "static/js/editor/build/controls.js"
RIGHT_PANEL = ROOT / "static/js/editor/build/right-panel.js"
SLIDERS = ROOT / "static/js/editor/stroke-tool-sliders.js"
STATE = ROOT / "static/js/editor/state.js"
EYEDROPPER = ROOT / "static/js/editor/tools/eyedropper.js"


def test_eyedropper_options_has_sample_size_select():
    src = CONTROLS.read_text(encoding="utf-8")
    assert 'id="ge-eyedropper-sample-size"' in src
    # Photoshop's Sample Size presets: Point Sample plus N-by-N averages.
    assert "[1, 3, 5, 11, 31, 51]" in src
    assert "Point Sample" in src
    assert "by ${n} Average" in src
    # Seeded from live state, not a hardcoded selection.
    assert "eyedropperSampleSize = 1" in src
    assert "Number(eyedropperSampleSize) === n ? ' selected' : ''" in src


def test_eyedropper_sample_size_is_wired_to_state():
    src = SLIDERS.read_text(encoding="utf-8")
    assert "document.getElementById('ge-eyedropper-sample-size')?.addEventListener('change'" in src
    assert "state.eyedropperSampleSize = size;" in src
    assert "Math.max(1, Math.round(Number(e.target.value) || 1))" in src


def test_eyedropper_sample_size_seeded_from_state():
    rp = RIGHT_PANEL.read_text(encoding="utf-8")
    assert "eyedropperSampleSize: state.eyedropperSampleSize," in rp
    # Point Sample is the Photoshop default.
    assert "eyedropperSampleSize: 1," in STATE.read_text(encoding="utf-8")


def test_eyedropper_tool_averages_an_n_by_n_block():
    src = EYEDROPPER.read_text(encoding="utf-8")
    assert "function averageBlock(" in src
    assert "state.eyedropperSampleSize" in src
    assert "const size = Math.max(1, Math.round(Number(state.eyedropperSampleSize) || 1));" in src
    # Point Sample (size <= 1) keeps the exact single-pixel read.
    assert "if (size <= 1) {" in src
    # The average path stays inside the canvas bounds.
    assert "Math.min(width - 1, cx + half)" in src
    assert "Math.min(height - 1, cy + half)" in src
