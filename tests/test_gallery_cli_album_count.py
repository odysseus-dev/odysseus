import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
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


def test_album_image_count_handles_missing_relationship(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._album_image_count(SimpleNamespace(images=[1, 2])) == 2
    assert cli._album_image_count(SimpleNamespace(images=None)) == 0
    assert cli._album_image_count(SimpleNamespace(images=object())) == 0
