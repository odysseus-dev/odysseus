"""Regression guards for Notes DOM rendering helpers."""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent


def test_notes_image_src_guard_rejects_script_capable_data_images():
    src = (_REPO / "static" / "js" / "notes.js").read_text(encoding="utf-8")

    assert "function _safeImgSrc(s)" in src
    assert r"^data:image\/(?:png|jpe?g|gif|webp);base64," in src
    assert r"^data:image\/i.test(v)" not in src


def test_notes_linkify_escapes_href_attribute():
    src = (_REPO / "static" / "js" / "notes.js").read_text(encoding="utf-8")

    assert "function _attrEsc(s)" in src
    assert 'href="${_attrEsc(href)}"' in src
    assert 'href="${href}"' not in src


def test_notes_edit_form_uses_safe_image_src_guard():
    src = (_REPO / "static" / "js" / "notes.js").read_text(encoding="utf-8")

    assert "let currentImageUrl = _safeImgSrc(note?.image_url || '');" in src
    assert "let _stashedDrawUrl = (type === 'draw') ? (_safeImgSrc(note?.image_url) || null) : null;" in src
    assert "_wireCanvas(bodyEl, _stashedDrawUrl || currentImageUrl || _safeImgSrc(note?.image_url) || null)" in src
    assert "_wireCanvas(form.querySelector('.note-form-body'), _safeImgSrc(note?.image_url) || null)" in src
    assert "const safeInitialImageUrl = _safeImgSrc(initialImageUrl);" in src
    assert "img.src = safeInitialImageUrl;" in src
    assert "img.src = initialImageUrl;" not in src
