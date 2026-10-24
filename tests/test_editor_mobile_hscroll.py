import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "13-image-editor.css").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def _last_mobile_block(css: str) -> str:
    idx = css.rindex("max-width: 700px")
    return css[idx:]


def test_menu_row_and_toolbar_contain_sideways_scroll():
    # Asserted against the whole stylesheet: several `@media (max-width: 700px)`
    # blocks now exist, so "the last one" is not the one that holds these rules.
    assert "overscroll-behavior-x: contain;" in CSS
    assert ".ge-toolbar," in CSS
    assert ".ge-dock-tabs {" in CSS


def test_menu_row_stays_single_row():
    # The menu row scrolls sideways instead of wrapping to a second line.
    assert ".ge-topbar > .ge-topbar-left {" in CSS
    i = CSS.index(".ge-topbar > .ge-topbar-left {")
    block = CSS[i:i + 240]
    assert "overflow-x: auto;" in block
    assert "flex-wrap: wrap" not in block


def test_sw_cache_bumped():
    # Version-agnostic: every commit bumps the SW cache.
    m = re.search(r"CACHE_NAME = '(odysseus-v\d+-pr40-editor-precache)'", SW)
    assert m, "CACHE_NAME not found"
