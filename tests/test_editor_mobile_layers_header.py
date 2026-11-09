from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / "static/js/galleryEditor.js"
CSS = ROOT / "static/css/13-image-editor.css"


def test_mobile_relocates_footer_history_into_layers_header():
    src = GALLERY.read_text(encoding="utf-8")
    # On phones the footer's undo/redo/history pill was floating over the layer
    # sheet's Blend row; tuck it into the layer-sheet header instead.
    assert "if (window.innerWidth <= 700) {" in src
    assert "container.querySelector('.ge-footer-history')" in src
    assert "container.querySelector('.ge-layers-header')" in src
    assert "hdr.appendChild(fh)" in src


def test_mobile_layers_header_pill_style():
    css = CSS.read_text(encoding="utf-8")
    assert ".ge-layers-header .ge-footer-history {" in css
    idx = css.index(".ge-layers-header .ge-footer-history {")
    block = css[idx:idx + 220]
    assert "z-index: auto;" in block
    assert "margin-left: auto;" in block
