from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_search_popup_freezes_offset_before_mobile_keyboard_focus() -> None:
    source = (ROOT / "static/js/search-chat.js").read_text(encoding="utf-8")
    css = app_css()

    set_offset = source.index("--search-overlay-top")
    show_overlay = source.index("overlay.classList.remove('hidden')", set_offset)
    focus_input = source.index("input.focus()", show_overlay)

    assert set_offset < show_overlay < focus_input
    assert "padding-top: var(--search-overlay-top, 15vh);" in css
