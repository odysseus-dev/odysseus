from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
SW_JS = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def _rule(selector: str, span: int = 700) -> str:
    start = STYLE_CSS.index(selector)
    return STYLE_CSS[start:start + span]


def test_chat_model_picker_stays_below_mobile_sidebar_layer():
    sidebar_rule = _rule("@media (max-width:768px){", 1800)
    assert ".sidebar {" in sidebar_rule
    assert "z-index: 200;" in sidebar_rule

    picker_rule = _rule(".chat-input-top > .model-picker-wrap {")
    assert "Composer-local chrome: above the input, below mobile sidebar/modals." in picker_rule
    assert "z-index: 60;" in picker_rule
    assert "z-index: 250;" not in picker_rule

    menu_rule = _rule(".model-picker-menu {")
    assert "z-index: 1;" in menu_rule
    assert "z-index: 250;" not in menu_rule


def test_model_picker_layer_cache_bumped():
    assert "const CACHE_NAME = 'odysseus-v407';" in SW_JS
