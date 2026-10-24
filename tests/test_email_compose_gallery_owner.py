"""Attaching a gallery image to an email follows the gallery's owner rule (#5460).

POST /compose-from-odysseus skipped the owner check for owner-less gallery
rows, so any authenticated user could stage another tenant's image. It now
uses gallery_helpers._owner_filter: exact owner match, and owner-less rows
only in auth-disabled single-user mode.
"""
import asyncio
from unittest import mock

import pytest
from fastapi import HTTPException

from tests.helpers.database import disposable_database


@pytest.fixture
def compose(tmp_path, monkeypatch):
    import core.database as core_db
    import routes.gallery.gallery_routes as gallery_routes
    from routes import email_routes
    from routes.email import email_routes as email_routes_impl

    image = tmp_path / "pic.png"
    image.write_bytes(b"\x89PNG fake")
    uploads = tmp_path / "compose"
    uploads.mkdir()
    monkeypatch.setattr(email_routes_impl, "COMPOSE_UPLOADS_DIR", uploads)
    monkeypatch.setattr(gallery_routes, "_gallery_image_path", lambda filename: image)

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(core_db, "SessionLocal", factory)
        db = factory()
        db.add_all([
            core_db.GalleryImage(id="orphan", filename="orphan.png", owner=None, is_active=True),
            core_db.GalleryImage(id="alices", filename="alices.png", owner="alice", is_active=True),
        ])
        db.commit()
        db.close()

        with mock.patch.object(email_routes, "_start_poller"):
            router = email_routes.setup_email_routes()
        endpoint = next(
            r.endpoint for r in router.routes
            if getattr(r.endpoint, "__name__", "") == "compose_from_odysseus"
        )

        def call(image_id, owner):
            result = asyncio.run(endpoint({"kind": "gallery", "id": image_id}, owner=owner))
            assert (uploads / result["token"]).is_file()  # staged in the temp dir only
            return result

        yield call


def test_owner_less_image_is_not_attachable_by_another_user(compose, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as err:
        compose("orphan", "bob")
    assert err.value.status_code == 404


def test_owner_can_attach_their_own_image(compose, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    assert compose("alices", "alice")["success"] is True


def test_other_owners_image_stays_hidden(compose, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as err:
        compose("alices", "bob")
    assert err.value.status_code == 404


def test_single_user_mode_can_attach_owner_less_image(compose, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert compose("orphan", "")["success"] is True
