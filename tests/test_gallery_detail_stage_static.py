from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_gallery_detail_uses_fixed_media_stage_and_arrow_lanes():
    css = app_css()
    image_rule = css[css.index(".gallery-detail-image {"):]
    image_rule = image_rule[:image_rule.index("}")]

    assert "height: 100%" in image_rule
    assert "padding-inline: 56px" in image_rule
    assert "overflow: hidden" in image_rule
    assert '.gallery-modal-content:has(#gallery-detail[style*="flex"])' in css
    assert "height: 92vh" in css


def test_gallery_media_maximizes_inside_stage_without_resizing_it():
    css = app_css()

    assert css.count("max-height: 100%") >= 2
    assert "object-fit: contain" in css
    assert "height: clamp(280px, 58vh, 520px)" in css


def test_reuse_in_chat_attaches_the_gallery_image_and_prompt():
    gallery = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
    start = gallery.index("gallery-detail-reuse-prompt')?.addEventListener")
    handler = gallery[start:gallery.index("// Clickable tag chips", start)]

    assert "await fetch(img.url" in handler
    assert "new File([blob]" in handler
    assert "fileHandlerModule.addFiles([file])" in handler
    assert "input.value = img.prompt" in handler
    assert 'id="gallery-detail-copy-prompt"><svg' in gallery
    assert 'id="gallery-detail-reuse-prompt"><svg' in gallery
