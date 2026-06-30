"""Regression: gallery CLI image serialization must tolerate a non-string prompt.

`_serialize_image` did `(i.prompt or "")[:200]`. A non-string prompt is truthy,
so `123[:200]` raised TypeError. `_preview_text` coerces non-strings to "".
"""
from types import SimpleNamespace

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_preview_text_ignores_non_string(monkeypatch):
    make_core_db_stub(monkeypatch, models=["GalleryImage", "GalleryAlbum"])
    cli = load_script("odysseus-gallery")
    assert cli._preview_text(None) == ""
    assert cli._preview_text(123) == ""
    assert cli._preview_text("p" * 250) == "p" * 200
    assert cli._text_field("ok") == "ok"
    assert cli._text_field(123) == ""


def test_serialize_image_does_not_crash_on_non_string_prompt(monkeypatch):
    make_core_db_stub(monkeypatch, models=["GalleryImage", "GalleryAlbum"])
    cli = load_script("odysseus-gallery")
    img = SimpleNamespace(
        id="i1", filename=123, prompt=123, model=123, size=None, tags=["bad"],
        favorite=0, album_id=None, session_id=None, width=1, height=1, file_size=1,
        taken_at=None, camera_make=123, camera_model=None, created_at=None,
    )
    out = cli._serialize_image(img)
    assert out["prompt"] == ""
    assert out["filename"] == ""
    assert out["model"] == ""
    assert out["tags"] == ""
    assert out["id"] == "i1"
