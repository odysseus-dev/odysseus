import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "js" / "galleryEditor.js").read_text()
CSS = (ROOT / "static" / "css" / "13-image-editor.css").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_toolbar_is_wrapped_for_an_overlay_chevron():
    assert "toolbarWrap.className = 'ge-toolbar-wrap';" in JS
    assert "toolbarMore.className = 'ge-tool-more';" in JS
    assert "toolbarMore.textContent = '\\u203A';" in JS
    assert "toolbarWrap.appendChild(toolbar);" in JS
    assert "toolbarWrap.appendChild(toolbarMore);" in JS
    assert "toolbar.scrollBy(" in JS


def test_chevron_visibility_is_synced_to_scroll():
    assert "const syncMore = () => {" in JS
    assert "toolbarMore.classList.toggle('is-visible'" in JS
    assert "toolbar.addEventListener('scroll', syncMore" in JS


def test_chevron_css():
    assert ".ge-toolbar-wrap { display: contents; }" in CSS
    assert ".ge-tool-more { display: none; }" in CSS
    assert ".ge-tool-more.is-visible { display: flex; }" in CSS
    assert "flex: 1 1 auto; min-width: 0;" in CSS


def test_sw_cache_bumped():
    m = re.search(r"CACHE_NAME = '(odysseus-v\d+-pr40-editor-precache)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))


def test_quick_edit_bar_is_clamped_off_the_tool_rail():
    assert "wrap.offsetHeight || 0" in JS
    assert "rail.getBoundingClientRect().bottom" in JS
    assert "const maxLift = Math.round(hostTop - 14 - barH" in JS
    assert "_settleMobileChrome" in JS
    assert "new MutationObserver(_settleMobileChrome)" in JS



def test_blanket_button_inflation_is_gone():
    # The 44px rule must not apply to every editor button (it inflated the top
    # menu row and clipped labels); it is scoped to the sheet footers instead.
    assert "body.ge-editor-open .ge-btn," not in CSS
    assert "body.ge-editor-open .ge-btn-sm {" not in CSS
    assert ".ge-layers-footer .ge-btn," in CSS
    assert ".ge-editor-footer .ge-btn," in CSS


def test_panels_button_is_last_in_the_header():
    assert "order: 1;" in CSS
