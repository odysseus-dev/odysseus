from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
SW_JS = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
COMPARE_SELECTOR_JS = (ROOT / "static" / "js" / "compare" / "selector.js").read_text(encoding="utf-8")


def _rule(selector: str, span: int = 700) -> str:
    start = STYLE_CSS.index(selector)
    return STYLE_CSS[start:start + span]


def test_chat_model_picker_stays_below_mobile_sidebar_layer():
    sidebar_rule = _rule("@media (max-width:768px){", 1800)
    assert ".sidebar {" in sidebar_rule
    assert "z-index: 400;" in sidebar_rule

    picker_rule = _rule(".chat-input-top > .model-picker-wrap {")
    assert "Composer-local chrome: above the input, below mobile sidebar/modals." in picker_rule
    assert "z-index: 60;" in picker_rule
    assert "z-index: 250;" not in picker_rule

    menu_rule = _rule(".model-picker-menu {")
    assert "z-index: 1;" in menu_rule
    assert "z-index: 250;" not in menu_rule


def test_model_picker_layer_cache_bumped():
    assert "const CACHE_NAME = 'odysseus-v419';" in SW_JS


def test_compare_searchable_picker_uses_dropdown_trigger_not_search_textbox():
    assert "trigger.className = 'cmp-model-picker-trigger';" in COMPARE_SELECTOR_JS
    assert "searchInput.className = 'cmp-picker-search';" in COMPARE_SELECTOR_JS
    assert "closeBtn.className = 'cmp-picker-close';" in COMPARE_SELECTOR_JS
    assert "trigger.addEventListener('click'" in COMPARE_SELECTOR_JS
    assert "searchInput.addEventListener('input'" in COMPARE_SELECTOR_JS
    assert "dropdown.dataset.open = 'true';" in COMPARE_SELECTOR_JS
    assert "dropdown.dataset.open !== 'true'" in COMPARE_SELECTOR_JS
    assert "input.addEventListener('focus'" not in COMPARE_SELECTOR_JS


def test_compare_dropdown_does_not_use_dynamic_portal_z_index():
    assert "topPortalZ" not in COMPARE_SELECTOR_JS
    assert ".cmp-model-picker-trigger" in STYLE_CSS
    assert ".cmp-picker-search-row" in STYLE_CSS
    assert ".cmp-model-picker-trigger:focus-visible" in STYLE_CSS
