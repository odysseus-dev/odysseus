from pathlib import Path

from tests.helpers.css_loader import read_css_with_imports


ROOT = Path(__file__).resolve().parents[1]
STYLE_CSS = read_css_with_imports(ROOT / "static" / "style.css")
SW_JS = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
COMPARE_SELECTOR_JS = (ROOT / "static" / "js" / "compare" / "selector.js").read_text(encoding="utf-8")


def _rule(selector: str, span: int = 700) -> str:
    start = STYLE_CSS.index(selector)
    return STYLE_CSS[start:start + span]


def test_chat_model_picker_sits_above_minimized_dock_below_windows():
    sidebar_rule = _rule("@media (max-width:768px){", 1800)
    assert ".sidebar {" in sidebar_rule
    assert "z-index: 400;" in sidebar_rule

    dock_rule = _rule("#minimized-dock {")
    assert "z-index: 100;" in dock_rule
    assert "pointer-events: none;" in dock_rule

    dock_row_rule = _rule("#minimized-dock.dock-chatbar-row {")
    assert "pointer-events: none;" in dock_row_rule
    assert "z-index: 10020;" not in dock_row_rule

    active_picker_rule = _rule(".chat-input-bar:has(#model-picker-menu:not(.hidden)) {")
    assert "position: relative;" in active_picker_rule
    assert "z-index: 245;" in active_picker_rule

    modal_rule = _rule(".modal {")
    assert "z-index:250;" in modal_rule

    picker_rule = _rule(".chat-input-top > .model-picker-wrap {")
    assert "Composer-local chrome: above the input, below mobile sidebar/modals." in picker_rule
    assert "z-index: 60;" in picker_rule
    assert "z-index: 250;" not in picker_rule

    menu_rule = _rule(".model-picker-menu {")
    assert "z-index: 1;" in menu_rule
    assert "z-index: 250;" not in menu_rule


def test_model_picker_layer_cache_bumped():
    assert "const CACHE_NAME = 'odysseus-v429';" in SW_JS


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
