from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def _css_block(selector: str, css: str = STYLE_CSS) -> str:
    start = css.index(selector)
    brace = css.index("{", start)
    depth = 0
    for idx in range(brace, len(css)):
        char = css[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1:idx]
    raise AssertionError(f"CSS block not closed for {selector}")


def _mobile_gallery_block() -> str:
    marker = "Single-row toolbar on mobile: the row owns its sideways scroll"
    marker_pos = STYLE_CSS.index(marker)
    start = STYLE_CSS.rfind("@media (max-width: 600px)", 0, marker_pos)
    assert start != -1
    brace = STYLE_CSS.index("{", start)
    depth = 0
    for idx in range(brace, len(STYLE_CSS)):
        char = STYLE_CSS[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return STYLE_CSS[brace + 1:idx]
    raise AssertionError("mobile gallery CSS block not closed")


def _touch_landscape_fullscreen_gallery_block() -> str:
    marker = "#gallery-modal.modal-full-expanded .gallery-toolbar"
    marker_pos = STYLE_CSS.index(marker)
    start = STYLE_CSS.rfind(
        "@media (hover: none) and (pointer: coarse) and (orientation: landscape)",
        0,
        marker_pos,
    )
    assert start != -1
    brace = STYLE_CSS.index("{", start)
    depth = 0
    for idx in range(brace, len(STYLE_CSS)):
        char = STYLE_CSS[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return STYLE_CSS[brace + 1:idx]
    raise AssertionError("touch landscape fullscreen gallery CSS block not closed")


def test_gallery_toolbar_owns_horizontal_scroll() -> None:
    block = _css_block(".gallery-toolbar")

    assert "max-width: 100%;" in block
    assert "overflow-x: auto;" in block
    assert "overflow-y: hidden;" in block
    assert "overscroll-behavior-x: contain;" in block
    assert "-webkit-overflow-scrolling: touch;" in block
    assert "scrollbar-width: none;" in block


def test_mobile_gallery_toolbar_keeps_controls_scrollable_not_clipped() -> None:
    mobile = _mobile_gallery_block()

    toolbar_block = _css_block(".gallery-toolbar", mobile)
    assert "flex-wrap: nowrap;" in toolbar_block
    assert "gap: 10px;" in toolbar_block

    search_block = _css_block(".gallery-search-wrap", mobile)
    assert "flex: 0 0 min(220px, 54vw);" in search_block
    assert "min-width: 150px;" in search_block

    select_block = _css_block(".gallery-toolbar .gallery-select-btn", mobile)
    assert "flex: 0 0 auto;" in select_block
    assert "min-width: 68px;" in select_block
    assert "margin-left: 0;" in select_block
    assert "margin-left: auto;" not in select_block


def test_docked_gallery_toolbar_uses_compact_scroll_sizing() -> None:
    docked_selector = (
        "#gallery-modal:is(.modal-left-docked, .modal-right-docked):not(.modal-full-expanded)"
    )

    toolbar_block = _css_block(f"{docked_selector} .gallery-toolbar")
    assert "flex-wrap: nowrap;" in toolbar_block
    assert "gap: 10px;" in toolbar_block
    assert "padding: 0 10px 6px 2px;" in toolbar_block

    search_block = _css_block(f"{docked_selector} .gallery-search-wrap")
    assert "flex: 0 0 min(220px, 42vw);" in search_block
    assert "min-width: 150px;" in search_block

    hint_block = _css_block(f"{docked_selector} .gallery-search-enter-hint")
    assert "display: none;" in hint_block

    assert (
        f"{docked_selector} .gallery-model-filter {{\n"
        "  width: clamp(150px, 24vw, 260px);\n"
        "}"
    ) in STYLE_CSS

    select_block = _css_block(f"{docked_selector} .gallery-toolbar .gallery-select-btn")
    assert "flex: 0 0 auto;" in select_block
    assert "min-width: 68px;" in select_block
    assert "margin-left: 0;" in select_block


def test_touch_landscape_fullscreen_gallery_toolbar_uses_compact_sizing() -> None:
    media = _touch_landscape_fullscreen_gallery_block()
    selector = "#gallery-modal.modal-full-expanded"

    toolbar_block = _css_block(f"{selector} .gallery-toolbar", media)
    assert "flex-wrap: nowrap;" in toolbar_block
    assert "gap: 8px;" in toolbar_block
    assert "padding: 0 18px 6px 2px;" in toolbar_block
    assert "box-sizing: border-box;" in toolbar_block

    search_block = _css_block(f"{selector} .gallery-search-wrap", media)
    assert "flex: 0 0 clamp(190px, 28vw, 240px);" in search_block
    assert "min-width: 190px;" in search_block

    hint_block = _css_block(f"{selector} .gallery-search-enter-hint", media)
    assert "display: none;" in hint_block

    assert (
        f"{selector} .gallery-model-filter {{\n"
        "    width: clamp(140px, 20vw, 180px);\n"
        "  }"
    ) in media

    select_block = _css_block(f"{selector} .gallery-toolbar .gallery-select-btn", media)
    assert "flex: 0 0 auto;" in select_block
    assert "min-width: 62px;" in select_block
    assert "margin-left: 0;" in select_block
