"""A gallery caption records who wrote it: the vision model or the user."""

from pathlib import Path

from src.caption_source import (USER, VISION, cache_fallback, read_cache_kind, user_source,
                                vision_source, write_cache_kind)
from tests.helpers.database import isolated_session_database

ROOT = Path(__file__).resolve().parents[1]


def test_source_labels():
    assert vision_source("gemma4:q5c") == "vision:gemma4:q5c" and vision_source("") == ""
    assert user_source("admin") == "user:admin"


def test_cache_sidecar_holds_only_the_kind(tmp_path):
    cache = str(tmp_path / "upload.txt")
    assert read_cache_kind(cache) == ""                    # older caches have no author
    write_cache_kind(cache, USER)
    assert read_cache_kind(cache) == "user"
    assert Path(cache + ".source").read_text() == "user"   # no name is written to disk
    write_cache_kind(cache, "user:admin")                  # anything else is not stored
    assert read_cache_kind(cache) == ""
    assert cache_fallback(USER, "bob") == "user:bob" and cache_fallback(VISION, "bob") == ""


def _gallery_row(database, source=""):
    with database.SessionLocal() as db:
        db.add(database.GalleryImage(id="photo", filename="p.png", prompt="", file_hash="abc",
                                     owner="bob", is_active=True, caption="old", caption_source=source))
        db.commit()


def _row(database):
    with database.SessionLocal() as db:
        img = db.get(database.GalleryImage, "photo")
        return img.caption, img.caption_source


def test_chat_vision_sync_stores_the_caption_and_its_author(tmp_path):
    from src import chat_handler
    with isolated_session_database(tmp_path) as (_manager, database):
        _gallery_row(database)
        chat_handler._sync_upload_vision_to_gallery({"hash": "abc"}, "bob", "A toy dinosaur.",
                                                    vision_source("gemma4:q5c"))
        assert _row(database) == ("A toy dinosaur.", "vision:gemma4:q5c")
        from routes.gallery.gallery_helpers import _image_to_dict
        with database.SessionLocal() as db:
            assert _image_to_dict(db.get(database.GalleryImage, "photo"))["caption_source"] == "vision:gemma4:q5c"


def test_cache_sync_keeps_the_user_who_wrote_it_not_the_owner(tmp_path):
    # An admin corrected bob's caption: a later sync from the cache must keep
    # "user:admin", not relabel it as the file's owner.
    from src import chat_handler
    with isolated_session_database(tmp_path) as (_manager, database):
        _gallery_row(database, source="user:admin")
        chat_handler._sync_upload_vision_to_gallery({"hash": "abc"}, "bob", "Corrected text.",
                                                    None, cache_fallback(USER, "bob"))
        assert _row(database) == ("Corrected text.", "user:admin")


def test_cache_sync_fills_an_unknown_author_from_the_kind(tmp_path):
    from src import chat_handler
    with isolated_session_database(tmp_path) as (_manager, database):
        _gallery_row(database, source="")
        chat_handler._sync_upload_vision_to_gallery({"hash": "abc"}, "bob", "Hand-written.",
                                                    None, cache_fallback(USER, "bob"))
        assert _row(database) == ("Hand-written.", "user:bob")


def test_hand_edits_are_credited_to_their_author_and_the_gallery_shows_it():
    upload = (ROOT / "routes/upload_routes.py").read_text(encoding="utf-8")
    assert "write_cache_kind(_vision_cache_path(file_id), USER)" in upload
    assert "text, user_source(current_user))" in upload    # whoever saved the text
    js = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
    assert "function _captionSourceLabel(source, esc)" in js
    assert "User: ${esc(who || 'unknown')}" in js and "Vision: ${esc(who.split('/').pop())}" in js
    assert "${_captionSourceLabel(img.caption_source, _esc)}" in js
