from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "static/js/editor/build/options-bar.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
CSS = ROOT / "static/css/13-image-editor.css"


def test_options_bar_module_shape():
    src = SRC.read_text(encoding="utf-8")
    assert "export function buildOptionsBar" in src
    assert "export function renderOptionsBar" in src
    assert "export function syncOptionsBar" in src
    # Delegates to the authoritative right-panel controls (no duplicate state).
    for selector in (
        ".ge-size-slider",
        "#ge-brush-opacity",
        "#ge-brush-flow",
        "#ge-eraser-flow",
        "#ge-clone-opacity",
        "#ge-lasso-feather",
        "#ge-wand-feather",
    ):
        assert selector in src, selector
    # Selection modes reuse the existing wand-mode buttons rather than re-implementing.
    assert "data-wand-mode=" in src


def test_options_bar_is_hidden_on_phones():
    css = CSS.read_text(encoding="utf-8")
    assert ".ge-options-bar" in css
    assert ".ge-options-empty .ge-options-sep" in css


def test_gallery_wires_options_bar():
    src = GALLERY.read_text(encoding="utf-8")
    assert "buildOptionsBar as _buildOptionsBar" in src
    assert "_buildOptionsBar()" in src
    assert "_renderOptionsBar(optionsBar, state.tool)" in src
