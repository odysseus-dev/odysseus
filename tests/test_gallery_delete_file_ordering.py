"""Regression: deleting a gallery image must not remove the file before the DB
commit succeeds.

delete_gallery_image() removed the on-disk file first and only then set
is_active=False and committed. If that commit failed and rolled back, the record
stayed active but its file was already gone — a broken, unviewable image (data
loss). The file is now removed only after the soft-delete commit succeeds, and
best-effort so a missing/locked file can't fail an otherwise-successful delete.
"""
import asyncio

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, GalleryImage
import routes.gallery_routes as gallery_routes


def _delete_endpoint():
    router = gallery_routes.setup_gallery_routes()
    for route in router.routes:
        if getattr(route, "path", "") == "/api/gallery/{image_id}" and "DELETE" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("DELETE /api/gallery/{image_id} endpoint not found")


def _seed(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    db.add(GalleryImage(id="img-1", filename="x.png", owner="alice", is_active=True))
    db.commit()
    db.close()
    img_dir = tmp_path / "data" / "generated_images"
    img_dir.mkdir(parents=True)
    (img_dir / "x.png").write_bytes(b"image-bytes")
    return SessionLocal


def test_file_kept_when_commit_fails(tmp_path, monkeypatch):
    SessionLocal = _seed(tmp_path)
    # GALLERY_IMAGE_DIR is an absolute path fixed at import, so a chdir can't
    # redirect the delete; point the resolver at the seeded tmp dir directly.
    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", tmp_path / "data" / "generated_images")
    monkeypatch.setattr(gallery_routes, "get_current_user", lambda r: "alice")

    # A session whose commit always fails, to simulate a DB error mid-delete.
    sess = SessionLocal()

    def _boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(sess, "commit", _boom)
    monkeypatch.setattr(gallery_routes, "SessionLocal", lambda: sess)

    delete = _delete_endpoint()
    with pytest.raises(HTTPException):
        asyncio.run(delete(Request(scope={"type": "http"}), "img-1"))

    # File must survive a failed commit — the record is still active after rollback.
    assert (tmp_path / "data" / "generated_images" / "x.png").exists()
    check = SessionLocal()
    row = check.query(GalleryImage).filter(GalleryImage.id == "img-1").first()
    assert row.is_active is True
    check.close()


def test_file_removed_on_successful_delete(tmp_path, monkeypatch):
    SessionLocal = _seed(tmp_path)
    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", tmp_path / "data" / "generated_images")
    monkeypatch.setattr(gallery_routes, "get_current_user", lambda r: "alice")
    monkeypatch.setattr(gallery_routes, "SessionLocal", SessionLocal)

    delete = _delete_endpoint()
    result = asyncio.run(delete(Request(scope={"type": "http"}), "img-1"))

    assert result["status"] == "deleted"
    assert not (tmp_path / "data" / "generated_images" / "x.png").exists()
    check = SessionLocal()
    row = check.query(GalleryImage).filter(GalleryImage.id == "img-1").first()
    assert row.is_active is False
    check.close()
