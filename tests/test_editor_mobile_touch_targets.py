"""Mobile editor touch targets must be at least 44x44 CSS px.

The mobile audit (2026-10-11) found 76 controls under 44px at 390px wide.
These rules (mobile only, scoped under @media (max-width:700px)) raise the
primary editor controls to a comfortable tap size without touching desktop.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def test_tool_buttons_are_44px():
    assert ".ge-toolbar .ge-tool-btn {" in CSS
    i = CSS.index(".ge-toolbar .ge-tool-btn {")
    block = CSS[i:i + 160]
    assert "min-width: 44px;" in block
    assert "min-height: 44px;" in block
    assert "height: 44px;" in block


def test_sheet_footer_buttons_are_44px():
    # The 44px box is scoped to the in-sheet controls (layers footer, editor
    # footer, panels overlay). Applying it to every editor button inflated the
    # top menu row and clipped its labels, so the topbar keeps natural sizing.
    for sel in (".ge-layers-footer .ge-btn,", ".ge-editor-footer .ge-btn,", ".ge-mobile-panels .ge-btn {"):
        assert sel in CSS
    i = CSS.index(".ge-layers-footer .ge-btn,")
    block = CSS[i:i + 220]
    assert "min-height: 44px;" in block
    assert "min-width: 44px;" in block


def test_layer_row_controls_are_44px():
    assert ".ge-layer-vis,\n  .ge-layer-btn," in CSS or ".ge-layer-vis," in CSS
    i = CSS.index(".ge-layer-vis,\n  .ge-layer-btn,") if ".ge-layer-vis,\n  .ge-layer-btn," in CSS else CSS.index(".ge-layer-vis,")
    block = CSS[i:i + 260]
    assert "width: 44px !important;" in block
    assert "min-width: 44px !important;" in block
    assert "height: 44px !important;" in block
    # grid tracks resized to match
    assert "grid-template-rows: 44px auto;" in CSS


def test_all_new_rules_are_mobile_scoped():
    # every 44px rule we rely on must sit inside a max-width:700px block
    for marker in (".ge-layers-footer .ge-btn,", ".ge-layer-vis,", ".ge-toolbar .ge-tool-btn {"):
        idx = CSS.rindex(marker)
        head = CSS[:idx]
        # the nearest preceding media query must be the mobile one
        last_media = head.rfind("@media")
        assert "@media (max-width: 700px)" in head[last_media:]


def test_sw_cache_bumped():
    # Version-agnostic: every commit bumps the SW cache.
    import re
    assert re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", SW)
