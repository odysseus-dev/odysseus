from pathlib import Path
import re
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
OVERFLOW = (ROOT / "static/js/editor/wire-topbar-overflow.js").read_text(encoding="utf-8")
STYLE = app_css()


def test_primary_editor_text_actions_use_stacked_toolbar_contract():
    for control_id in (
        "ge-view-menu-btn",
        "ge-image-menu-btn",
        "ge-selection-menu-btn",
        "ge-filter-menu-btn",
        "ge-import-topbar",
        "ge-save-menu-btn",
    ):
        marker = f'id="{control_id}"'
        start = TOPBAR.rfind("<button", 0, TOPBAR.index(marker))
        end = TOPBAR.index("</button>", TOPBAR.index(marker))
        button = TOPBAR[start:end]
        assert "ge-stacked-btn" in button
        assert "ge-stacked-glyph" in button
        assert "ge-stacked-label" in button


def test_stacked_labels_share_one_size_and_position_rule():
    assert ".ge-stacked-btn .ge-stacked-label" in STYLE
    assert "font-size: 8px;" in STYLE
    assert "top: 2px;" in STYLE


def test_narrow_topbar_scrolls_essential_actions_and_hides_ai_group_first():
    assert "if (topbar.scrollWidth > topbar.clientWidth)" in OVERFLOW
    assert "aiGroup.forEach(el => { el.style.display = 'none'; });" in OVERFLOW
    assert "new ResizeObserver(() => syncOverflow())" in OVERFLOW
    mobile = STYLE[STYLE.index("/* Editor topbar — scrolls horizontally") :]
    topbar = mobile[mobile.index(".ge-topbar {") : mobile.index("}", mobile.index(".ge-topbar {"))]
    assert "overflow-x: auto;" in topbar
    assert "flex-wrap: nowrap;" in topbar
    assert "justify-content: flex-start;" in topbar


def test_desktop_tool_rail_keeps_long_tool_names_readable():
    toolbar_start = STYLE.index(".ge-toolbar {")
    toolbar_end = STYLE.index(".ge-toolbar::-webkit-scrollbar", toolbar_start)
    toolbar = STYLE[toolbar_start:toolbar_end]
    assert "width: 64px;" in toolbar
    label_rule = re.search(r"(?m)^\.ge-tool-label\s*\{([^}]*)\}", STYLE[toolbar_end:])
    assert label_rule
    assert "font-size: 10px;" in label_rule.group(1)
