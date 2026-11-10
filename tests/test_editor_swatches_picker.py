from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANELS = ROOT / "static/js/editor/panels/panels.js"
CSS = ROOT / "static/css/13-image-editor.css"
GALLERY = ROOT / "static/js/galleryEditor.js"


def test_swatches_panel_has_inline_picker():
    src = PANELS.read_text(encoding="utf-8")
    # The SWATCHES panel exposes an always-visible SV square + hue strip instead
    # of only the click-to-open shared popover (never visible in a dock shot).
    assert "ge-inline-picker" in src
    assert "const sl = el('div', 'cp-sl');" in src
    assert "const hue = el('div', 'cp-hue');" in src
    # Local HSV<->hex math keeps the panel independent of colorPicker internals.
    assert "function hexToHsv(" in src
    assert "function hsvToHex(" in src
    # Dragging the SV square / hue writes through to the document colour.
    assert "startDrag('sl', sl)" in src
    assert "startDrag('hue', hue)" in src
    assert "_syncForeground(hex);" in src
    # The picker follows the document colour when it changes elsewhere.
    assert "setPickerFromColor(state.color || colorInput.value || '#000000');" in src


def test_inline_picker_css_reuses_shared_picker_classes():
    css = CSS.read_text(encoding="utf-8")
    assert ".ge-inline-picker {" in css
    assert ".ge-inline-picker .cp-sl {" in css
    # The swatch body wraps so the picker sits below the trigger + suggestions.
    assert ".ge-swatch-body { flex-wrap: wrap; }" in css


def test_color_group_is_tall_enough_for_the_picker():
    src = GALLERY.read_text(encoding="utf-8")
    # The dock group clips to groupSize; the Color group now fits the picker so
    # it is not scrolled out of view.
    assert "groupSize: 268" in src
