from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_keyboard_fades_welcome_without_moving_it() -> None:
    css = app_css()
    init_js = (ROOT / "static/js/init.js").read_text(encoding="utf-8")

    kb_rule = css.split("#welcome-screen.kb-hidden {", 1)[1].split("}", 1)[0]
    focus_rule = css.split(
        ".chat-container.welcome-active:has(.chat-input-bar:focus-within) #welcome-screen,",
        1,
    )[1].split("}", 1)[0]

    assert "transform: translate(-50%, -50%);" in kb_rule
    assert "scale(" not in kb_rule
    assert "transform: translate(-50%, -50%);" in focus_rule
    assert "translate(-50%, -58%)" not in focus_rule
    assert "welcome.style.transform" not in init_js


def test_nobody_label_collapses_by_width_not_keyboard_height() -> None:
    css = app_css()
    label_rule = css.rsplit(".incognito-btn .incognito-label {", 1)[0]
    media_header = label_rule.rsplit("@media", 1)[1].split("{", 1)[0]

    assert "max-width: 340px" in media_header
    assert "max-height" not in media_header
