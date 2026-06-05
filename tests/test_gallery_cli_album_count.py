from types import SimpleNamespace

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_album_image_count_handles_missing_relationship(monkeypatch):
    make_core_db_stub(monkeypatch, models=["GalleryImage", "GalleryAlbum"])
    cli = load_script("odysseus-gallery")

    assert cli._album_image_count(SimpleNamespace(images=[1, 2])) == 2
    assert cli._album_image_count(SimpleNamespace(images=None)) == 0
    assert cli._album_image_count(SimpleNamespace(images=object())) == 0
