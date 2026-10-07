from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parent.parent


def test_sidebar_chat_list_uses_content_height_when_collapsed():
    css = app_css()
    desktop = css.split("#sessions-section #session-list {", 1)[1].split("}", 1)[0]
    mobile = css.split("#sessions-section #session-list {", 2)[2].split("}", 1)[0]

    assert "\n      height: min(42vh, 420px);" not in desktop
    assert "flex: 0 0 auto;" in desktop
    assert "max-height: min(42vh, 420px);" in desktop
    assert "\n        height: min(34vh, 300px) !important;" not in mobile
    assert "flex: 0 0 auto !important;" in mobile
