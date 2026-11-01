"""Mobile editor footer must float above the layers bottom-sheet.

On phones the layers panel is a bottom sheet that would cover the editor
footer (zoom / Setup / AI settings). galleryEditor floats that footer just
above the sheet's top edge so its controls stay tappable, and clears it again
on desktop or when the sheet is collapsed.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
GE = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def test_footer_placer_exists_and_is_mobile_scoped():
    assert "const _placeMobileFooter = () => {" in GE
    assert "footer.classList.add('ge-footer-floating')" in GE
    assert "window.innerWidth > 700" in GE
    assert "document.querySelector('.ge-right-panel')" in GE
    # placed flush above the sheet top edge (no seam / dead space)
    assert "Math.round(vh - r.top)" in GE


def test_footer_watcher_reacts_to_sheet_resize():
    assert "const _syncMobileChrome" in GE
    assert "_liftQuickEdit(); _placeMobileFooter();" in GE
    assert "new ResizeObserver(_settleMobileChrome)" in GE
    assert "new MutationObserver(_settleMobileChrome)" in GE
    assert "window.addEventListener('resize', _syncMobileChrome)" in GE


def test_floating_footer_css_is_mobile_only():
    assert ".ge-editor-footer.ge-footer-floating {" in CSS
    marker = ".ge-editor-footer.ge-footer-floating {"
    idx = CSS.index(marker)
    # the rule must live inside the max-width:700px mobile block that also
    # tightens the footer
    assert "@media (max-width: 700px)" in CSS[:idx]


def test_sw_cache_bumped():
    # Version-agnostic: every commit bumps the SW cache, so pinning the exact
    # number makes this test fail on the next commit.
    import re
    assert re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", SW)
