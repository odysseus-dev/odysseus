"""Source checks for the gallery Settings-tab image-model picker.

The Quick Edit model picker lives in the editor; HARNESS mirrors it into the
gallery Settings tab, fed by GET /api/editor/image-models and sharing the
same localStorage key (`ge-ai-command-model`) so the two stay in sync.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GALLERY_JS = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")


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
