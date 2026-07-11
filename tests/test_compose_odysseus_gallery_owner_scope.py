"""Cross-tenant access control for the email compose "attach from Odysseus" path.

`POST /api/email/compose-from-odysseus` (and its `-zip` sibling) stage a
Document or Gallery image as a compose upload via `_load_odysseus_attachment_source`.
The gallery branch of that helper guarded only `img.owner and img.owner != owner`,
so an owner-less image (owner NULL/"") skipped the check entirely — letting any
authenticated user stage, and download, another tenant's owner-less gallery image
just by guessing its id.

The gallery's own listing contract (`gallery_helpers._owner_filter`) scopes
authenticated callers to an exact `owner == user` match and never surfaces
owner-less rows to them, so this path was strictly more permissive than the
library it borrows from. The guard must fail closed, matching that contract and
the merged send_to_session / manage_tasks / email-account owner gates.
"""

from unittest import mock

import pytest
from fastapi import HTTPException


def _make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _add_image(Factory, image_id, owner):
    from core.database import GalleryImage
    db = Factory()
    db.add(GalleryImage(
        id=image_id,
        filename=f"{image_id}.png",
        prompt="",
        owner=owner,
        is_active=True,
    ))
    db.commit()
    db.close()


def _compose_endpoint():
    import routes.email_routes as email_routes
    router = email_routes.setup_email_routes()
    for route in router.routes:
        if route.path == "/api/email/compose-from-odysseus" and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("compose-from-odysseus route not found")


@pytest.fixture
def staging(tmp_path, monkeypatch):
    """Point the gallery file lookup and the compose staging dir at tmp files so an
    allowed attach actually stages, letting the tests tell a real owner-block
    (HTTP 404) apart from an incidental missing-file 404."""
    import routes.email_routes as email_routes
    import routes.gallery.gallery_routes as gallery_routes

    img_file = tmp_path / "gallery.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n secret-image-bytes")
    monkeypatch.setattr(gallery_routes, "_gallery_image_path", lambda filename: img_file)

    uploads = tmp_path / "compose_uploads"
    uploads.mkdir()
    monkeypatch.setattr(email_routes, "COMPOSE_UPLOADS_DIR", uploads)
    return img_file


@pytest.mark.asyncio
async def test_compose_gallery_blocks_ownerless_image_for_authenticated_caller(staging):
    """The core regression: a legacy owner-less image is NOT stageable by an
    authenticated caller, matching the gallery library's exact-owner contract."""
    Factory = _make_db()
    _add_image(Factory, "img-legacy", owner=None)  # created while auth was off
    endpoint = _compose_endpoint()
    with mock.patch("core.database.SessionLocal", Factory):
        with pytest.raises(HTTPException) as exc:
            await endpoint({"kind": "gallery", "id": "img-legacy"}, owner="alice")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_compose_gallery_blocks_cross_owner_image(staging):
    Factory = _make_db()
    _add_image(Factory, "img-bob", owner="bob")
    endpoint = _compose_endpoint()
    with mock.patch("core.database.SessionLocal", Factory):
        with pytest.raises(HTTPException) as exc:
            await endpoint({"kind": "gallery", "id": "img-bob"}, owner="alice")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_compose_gallery_allows_own_image(staging):
    Factory = _make_db()
    _add_image(Factory, "img-alice", owner="alice")
    endpoint = _compose_endpoint()
    with mock.patch("core.database.SessionLocal", Factory):
        result = await endpoint({"kind": "gallery", "id": "img-alice"}, owner="alice")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_compose_gallery_single_user_mode_unchanged(staging):
    """owner == "" (unconfigured / single-user mode) still resolves any image,
    including owner-less ones — the guard only tightens the authenticated case."""
    Factory = _make_db()
    _add_image(Factory, "img-legacy", owner=None)
    endpoint = _compose_endpoint()
    with mock.patch("core.database.SessionLocal", Factory):
        result = await endpoint({"kind": "gallery", "id": "img-legacy"}, owner="")
    assert result["success"] is True
