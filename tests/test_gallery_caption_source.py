"""A gallery caption records who wrote it: the vision model or the user."""

from pathlib import Path

from src.caption_source import read_cache_source, user_source, vision_source, write_cache_source
from tests.helpers.database import isolated_session_database

ROOT = Path(__file__).resolve().parents[1]


def test_source_labels_and_cache_sidecar(tmp_path):
    assert vision_source("gemma4:q5c") == "vision:gemma4:q5c" and vision_source("") == ""
    assert user_source("admin") == "user:admin"
    cache = str(tmp_path / "upload.txt")
    assert read_cache_source(cache) == ""                 # older caches have no author
    write_cache_source(cache, user_source("admin"))
    assert read_cache_source(cache) == "user:admin"


def test_chat_vision_sync_stores_the_caption_and_its_author(tmp_path):
    from src import chat_handler
    with isolated_session_database(tmp_path) as (_manager, database):
        with database.SessionLocal() as db:
            db.add(database.GalleryImage(id="photo", filename="p.png", prompt="", file_hash="abc",
                                         owner="admin", is_active=True))
            db.commit()
        chat_handler._sync_upload_vision_to_gallery({"hash": "abc"}, "admin", "A toy dinosaur.",
                                                    vision_source("gemma4:q5c"))
        with database.SessionLocal() as db:
            img = db.get(database.GalleryImage, "photo")
            assert (img.caption, img.caption_source) == ("A toy dinosaur.", "vision:gemma4:q5c")
        from routes.gallery.gallery_helpers import _image_to_dict
        with database.SessionLocal() as db:
            assert _image_to_dict(db.get(database.GalleryImage, "photo"))["caption_source"] == "vision:gemma4:q5c"


def test_hand_edits_are_credited_to_the_user_and_the_gallery_shows_it():
    upload = (ROOT / "routes/upload_routes.py").read_text(encoding="utf-8")
    assert "source = user_source(current_user)" in upload
    assert "write_cache_source(cache_path, source)" in upload
    js = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
    assert "function _captionSourceLabel(source, esc)" in js
    assert "User: ${esc(who || 'unknown')}" in js and "Vision: ${esc(who.split('/').pop())}" in js
    assert "${_captionSourceLabel(img.caption_source, _esc)}" in js
