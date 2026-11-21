"""A photo's description can be edited from the gallery's photo view."""

from pathlib import Path

from tests.helpers.database import isolated_session_database

ROOT = Path(__file__).resolve().parents[1]


def _gallery_row(database):
    with database.SessionLocal() as db:
        db.add(database.GalleryImage(id="photo", filename="p.png", prompt="", file_hash="abc",
                                     owner="bob", is_active=True, caption="old"))
        db.commit()


def _caption(database):
    with database.SessionLocal() as db:
        return db.get(database.GalleryImage, "photo").caption


def test_patch_saves_clears_and_limits_the_description(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.gallery import gallery_routes
    with isolated_session_database(tmp_path) as (_manager, database):
        _gallery_row(database)
        monkeypatch.setattr(gallery_routes, "SessionLocal", database.SessionLocal)
        monkeypatch.setattr(gallery_routes, "get_current_user", lambda request: "bob")
        app = FastAPI()
        app.include_router(gallery_routes.setup_gallery_routes())
        client = TestClient(app)

        assert client.patch("/api/gallery/photo", json={"caption": "  My cat on the couch.  "}).json()["caption"] == "My cat on the couch."
        assert _caption(database) == "My cat on the couch."

        assert client.patch("/api/gallery/photo", json={"caption": "x" * 20001}).status_code == 400
        assert _caption(database) == "My cat on the couch."

        assert client.patch("/api/gallery/photo", json={"favorite": True}).status_code == 200
        assert _caption(database) == "My cat on the couch."   # other edits leave it alone

        assert client.patch("/api/gallery/photo", json={"caption": ""}).json()["caption"] == ""

        monkeypatch.setattr(gallery_routes, "get_current_user", lambda request: "eve")
        assert client.patch("/api/gallery/photo", json={"caption": "mine now"}).status_code == 404
        assert _caption(database) == ""


def test_gallery_menu_offers_edit_description():
    js = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
    assert 'id="gallery-edit-caption-btn"' in js and "Edit description" in js
    assert "body: JSON.stringify({ caption: input.value })" in js
    assert '<div class="gallery-detail-section" id="gallery-caption-section"' in js


def test_escape_cancels_the_description_edit_before_closing_the_photo():
    # The gallery's capture-phase Escape handler would otherwise take Esc as
    # "Back" and close the photo with the edit open.
    js = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
    handler = js[js.index("_escHandler = (e) => {"):]
    assert handler.index("getElementById('gallery-caption-input')") < handler.index("__galleryCancelSelection")
    assert "document.getElementById('gallery-caption-cancel')?.click();" in handler


def test_textarea_style_does_not_join_the_focus_selector():
    css = (ROOT / "static/css/09-gallery.css").read_text(encoding="utf-8")
    assert ".gallery-tag-input:focus,\n.gallery-detail-name-input:focus {" in css
    assert "textarea.gallery-detail-name-input { resize: vertical;" in css
