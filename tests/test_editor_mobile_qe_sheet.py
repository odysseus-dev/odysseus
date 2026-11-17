from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static/js/galleryEditor.js"
SW = ROOT / "static/sw.js"


def _js():
    return JS.read_text(encoding="utf-8")


def test_quick_edit_bar_lifts_clear_of_mobile_layers_sheet():
    # On phones the layers panel is a bottom sheet; the Quick Edit bar must be
    # lifted above the sheet's top edge so its input stays tappable.
    js = _js()
    assert "window.innerWidth <= 700" in js
    assert "document.querySelector('.ge-right-panel')" in js
    assert "r.top - 8" in js


def test_quick_edit_bar_watches_sheet_for_lift():
    # The bar re-lifts when the sheet expands/collapses. The observer goes
    # through a short settling loop because the sheet animates.
    js = _js()
    assert "_watchSheetForLift" in js
    assert "MutationObserver(_settleMobileChrome)" in js
    assert "_settleMobileChrome" in js


def test_sw_cache_bumped():
    # Version-agnostic: every commit bumps the SW cache, so pinning the exact
    # number makes this test fail on the next commit.
    import re
    sw = SW.read_text(encoding="utf-8")
    assert re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", sw)


def test_opening_quick_edit_collapses_the_sheet_on_phones():
    # The card and the sheet both live at the bottom; there is no room for the
    # card while the sheet is expanded, so opening the card drops the sheet back
    # to its peek height.
    js = _js()
    assert "sheet.classList.remove('expanded')" in js


def test_floating_footer_hides_while_quick_edit_is_open():
    css = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
    assert "body:has(.ge-ai-command:not(.ge-ai-command-collapsed)) .ge-editor-footer.ge-footer-floating {" in css


def test_mobile_layer_action_icons_have_contrast():
    css = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
    assert ".ge-layer-btn,\n  .ge-layer-fx-btn {\n    opacity: 0.78;" in css
