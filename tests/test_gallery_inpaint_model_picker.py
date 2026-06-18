from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_MODELS_JS = (ROOT / "static" / "js" / "editor" / "ai-models.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
SW_JS = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def test_inpaint_model_picker_wraps_native_select_without_changing_payload_source():
    assert "const SERVE_IMAGE_MODEL_VALUE = '__serve_cookbook__';" in AI_MODELS_JS
    assert "function wireInpaintModelPicker" in AI_MODELS_JS
    assert "btn.id = 'ge-ai-inpaint-picker-btn';" in AI_MODELS_JS
    assert "search.id = 'ge-ai-inpaint-picker-search';" in AI_MODELS_JS
    assert "select.classList.add('ge-model-native-select');" in AI_MODELS_JS
    assert "select.insertAdjacentElement('afterend', wrap);" in AI_MODELS_JS
    assert "document.body.appendChild(menu);" in AI_MODELS_JS
    assert "select.dispatchEvent(new Event('change', { bubbles: true }));" in AI_MODELS_JS
    assert "openCookbookForImg2img();" in AI_MODELS_JS
    assert "refreshModels: refreshInpaintModels" in AI_MODELS_JS


def test_inpaint_model_picker_has_mobile_sheet_and_clipping_safe_menu_styles():
    assert ".ge-inpaint-model-picker-menu {" in STYLE_CSS
    assert "position: fixed;" in STYLE_CSS
    assert ".ge-inpaint-model-row #ge-ai-inpaint.ge-model-native-select" in STYLE_CSS
    assert ".ge-inpaint-model-picker-search" in STYLE_CSS
    assert ".ge-inpaint-model-option.is-selected" in STYLE_CSS
    mobile_block = STYLE_CSS[STYLE_CSS.index("@media (max-width: 700px) {"):]
    assert ".ge-inpaint-model-picker-menu" in mobile_block
    assert "bottom: max(8px, env(safe-area-inset-bottom)) !important;" in mobile_block
    assert "max-height: min(70dvh, 520px) !important;" in mobile_block


def test_gallery_editor_cache_bumped_for_android_assets():
    assert "const CACHE_NAME = 'odysseus-v388';" in SW_JS
