from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static/css/13-image-editor.css"


def test_text_options_strip_lays_grids_out_as_rows():
    css = CSS.read_text(encoding="utf-8")
    # The vertical Text panel must become a horizontal strip in the options bar.
    assert ".ge-controls--in-options .ge-text-section .ge-text-grid" in css
    assert ".ge-controls--in-options .ge-text-section .ge-text-field" in css


def test_text_options_font_picker_keeps_its_width():
    css = CSS.read_text(encoding="utf-8")
    # Regression: the Font picker collapsed to ~14px and the Font/Size labels
    # piled into "FoSize". The font field must reserve real width.
    assert ".ge-controls--in-options .ge-text-section .ge-text-font-field { min-width: 116px; }" in css
    assert ".ge-controls--in-options .ge-text-section .ge-text-size-field { min-width: 56px; }" in css
    # And the pickers must fill their reserved field.
    assert ".ge-controls--in-options .ge-text-section .ge-text-field > select," in css
    assert ".ge-controls--in-options .ge-text-section .ge-text-field > input { width: 100%; }" in css


def test_text_options_textarea_sized_for_the_bar():
    css = CSS.read_text(encoding="utf-8")
    assert ".ge-controls--in-options .ge-text-section textarea" in css
    assert "width: 200px;" in css
