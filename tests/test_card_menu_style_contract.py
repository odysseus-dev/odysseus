from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.js_modules import email_library_paths


ROOT = Path(__file__).resolve().parents[1]
STYLE = app_css()
LIBRARY = (ROOT / "static/js/documentLibrary.js").read_text(encoding="utf-8")


def test_card_kebab_menus_share_font_rows_and_hover_behavior():
    marker = "/* One visual contract for card kebab menus."
    contract = STYLE.split(marker, 1)[1].split(
        "/* Email modal title unread badge", 1
    )[0]

    for selector in (
        ".session-dropdown-menu",
        ".email-card-dropdown",
        ".memory-item-dropdown",
        ".task-dropdown",
        ".skill-kebab-menu",
        ".doclib-card-dropdown",
    ):
        assert selector in contract
    assert "font-family: inherit !important" in contract
    assert "font: inherit !important" in contract
    assert "transform: none" in contract


def test_library_chat_card_menu_uses_standard_anchor_gap():
    assert "dd.style.top = (rect.bottom + 4) + 'px'" in LIBRARY


def test_card_menus_use_the_same_anchor_gap():
    modules = [
        ROOT / relative_path
        for relative_path in (
            "static/js/sessions.js",
            "static/js/documentLibrary.js",
            "static/js/memory.js",
            "static/js/tasks.js",
            "static/js/skills.js",
        )
    ] + email_library_paths(include_wrapper=True)
    for module in modules:
        source = module.read_text(encoding="utf-8")
        assert "rect.bottom + 2" not in source, module
        assert "r.bottom + 2" not in source, module
