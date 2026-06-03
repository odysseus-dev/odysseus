"""Regression: gallery CLI image serialization must tolerate a non-string prompt.

`_serialize_image` did `(i.prompt or "")[:200]`. A non-string prompt is truthy,
so `123[:200]` raised TypeError. `_preview_text` coerces non-strings to "".
"""
import importlib.machinery
import importlib.util
import sys
import types
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.GalleryImage = MagicMock()
    db.GalleryAlbum = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db)
    path = ROOT / "scripts" / "odysseus-gallery"
    loader = importlib.machinery.SourceFileLoader("odysseus_gallery_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_preview_text_ignores_non_string(monkeypatch):
    cli = _load_cli(monkeypatch)
    assert cli._preview_text(None) == ""
    assert cli._preview_text(123) == ""
    assert cli._preview_text("p" * 250) == "p" * 200


def test_serialize_image_does_not_crash_on_non_string_prompt(monkeypatch):
    cli = _load_cli(monkeypatch)
    img = SimpleNamespace(
        id="i1", filename="a.png", prompt=123, model=None, size=None, tags=None,
        favorite=0, album_id=None, session_id=None, width=1, height=1, file_size=1,
        taken_at=None, camera_make=None, camera_model=None, created_at=None,
    )
    out = cli._serialize_image(img)
    assert out["prompt"] == ""
    assert out["id"] == "i1"
