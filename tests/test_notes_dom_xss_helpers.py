"""Regression guards for Notes DOM rendering helpers."""

from pathlib import Path

from tests.helpers.notes_source import notes_source


_REPO = Path(__file__).resolve().parent.parent


def test_notes_image_src_guard_rejects_script_capable_data_images():
    renderer = (_REPO / "static/js/markdown.js").read_text(encoding="utf-8")
    assert "function noteImageSrc(rawUrl)" in renderer
    assert r"^data:image\/(?:png|jpe?g|gif|webp);base64," in renderer
    assert "_safeImgSrc: noteImageSrc" in notes_source("ui/text")


def test_notes_linkify_escapes_href_attribute():
    src = notes_source("ui/text", "ui/editor", "ui/drawing")

    assert "function _attrEsc(s)" in src
    assert 'href="${_attrEsc(href)}"' in src
    assert 'href="${href}"' not in src


def test_notes_edit_form_uses_safe_image_src_guard():
    src = notes_source("ui/text", "ui/editor", "ui/drawing")

    assert "let currentImageUrl = _safeImgSrc(note?.image_url || '');" in src
    assert "let _stashedDrawUrl = (type === 'draw') ? (_safeImgSrc(note?.image_url) || null) : null;" in src
    assert "_wireCanvas(bodyEl, _stashedDrawUrl || currentImageUrl || _safeImgSrc(note?.image_url) || null)" in src
    assert "_wireCanvas(form.querySelector('.note-form-body'), _safeImgSrc(note?.image_url) || null)" in src
    assert "const safeInitialImageUrl = _safeImgSrc(initialImageUrl);" in src
    assert "img.src = safeInitialImageUrl;" in src
    assert "img.src = initialImageUrl;" not in src
