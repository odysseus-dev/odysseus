from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / "static/js/galleryEditor.js"


def test_fill_fgbg_names_the_colour_in_status():
    src = GALLERY.read_text(encoding="utf-8")
    # Edit ▸ Fill with an explicit foreground/background colour and no
    # selection fills the whole layer and reports the exact colour used.
    assert "const _fgBgLabel = /\\bbackground\\b/.test(p) ? 'background' : 'foreground';" in src
    assert "Filled the layer with the ${_fgBgLabel} colour (${color})." in src


def test_fill_fgbg_fills_the_whole_layer():
    src = GALLERY.read_text(encoding="utf-8")
    idx = src.index("const _fgBgLabel")
    block = src[idx - 400:idx]
    assert "selection.selectAll" in block
    assert "selection.fill" in block
    assert "selection.deselect" in block
