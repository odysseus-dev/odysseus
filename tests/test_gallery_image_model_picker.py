"""Source checks for the gallery Settings-tab image-model picker.

The Quick Edit model picker lives in the editor; HARNESS mirrors it into the
gallery Settings tab, fed by GET /api/editor/image-models and sharing the
same localStorage key (`ge-ai-command-model`) so the two stay in sync.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GALLERY_JS = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
AI_MODELS_JS = (ROOT / "static/js/editor/ai-models.js").read_text(encoding="utf-8")


def test_settings_tab_has_image_model_picker():
    assert 'id="gallery-image-model"' in GALLERY_JS
    assert "/api/editor/image-models" in GALLERY_JS
    assert "ge-ai-command-model" in GALLERY_JS


def test_image_model_picker_lives_in_the_settings_container():
    settings_idx = GALLERY_JS.index('id="gallery-settings-container"')
    picker_idx = GALLERY_JS.index('id="gallery-image-model"')
    assert picker_idx > settings_idx


def test_picker_persists_with_the_shared_localstorage_key():
    assert "localStorage.setItem(_GE_MODEL_KEY" in GALLERY_JS
    assert "localStorage.getItem(_GE_MODEL_KEY)" in GALLERY_JS


def test_cloud_image_models_are_listed_alongside_the_local_ones():
    """Gateway endpoints (OpenRouter) register as llm but carry image models.

    The picker must surface those ids too — otherwise the only selectable
    edit model is the local Qwen server.
    """
    routes = (ROOT / "routes/editor_agent_routes.py").read_text(encoding="utf-8")
    assert "from src.image_model_ids import looks_like_image_generation_model" in routes
    assert 'image_endpoint = getattr(ep, "model_type", None) == "image"' in routes
    assert "if not image_endpoint and not looks_like_image_generation_model(name):" in routes


def test_option_values_use_endpoint_id_not_base_url():
    """Neither gallery.js nor ai-models.js may expose private base URLs in option values."""
    assert "${m.base_url" not in GALLERY_JS
    assert "m.endpoint_id ? String(m.endpoint_id)" in GALLERY_JS
    assert "${m.base_url" not in AI_MODELS_JS
    assert "m.endpoint_id ? String(m.endpoint_id)" in AI_MODELS_JS


def test_two_way_event_and_storage_synchronization():
    """Gallery and Quick Edit must dispatch and listen to ge-ai-command-model-change and storage."""
    assert "new CustomEvent('ge-ai-command-model-change'" in GALLERY_JS
    assert "addEventListener('ge-ai-command-model-change'" in GALLERY_JS
    assert "addEventListener('storage'" in GALLERY_JS

    assert "new CustomEvent('ge-ai-command-model-change'" in AI_MODELS_JS
    assert "addEventListener('ge-ai-command-model-change'" in AI_MODELS_JS
    assert "addEventListener('storage'" in AI_MODELS_JS


def test_legacy_localstorage_migration_scrubs_urls():
    """Both pickers must detect and migrate legacy URLs without leaking them."""
    assert "saved.startsWith('http://')" in GALLERY_JS
    assert "saved.startsWith('http://')" in AI_MODELS_JS
