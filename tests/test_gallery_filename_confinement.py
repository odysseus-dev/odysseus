import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from core.database import Base, GalleryImage


def _gallery_module():
    import routes.gallery_routes as gallery_routes
    return gallery_routes


def test_gallery_image_path_allows_safe_filename(tmp_path, monkeypatch):
    gallery_routes = _gallery_module()
    image_dir = tmp_path / "generated_images"
    image_dir.mkdir()
    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", image_dir)

    path = gallery_routes._gallery_image_path("abc123.png")

    assert path == image_dir / "abc123.png"


def test_gallery_image_path_does_not_fallback_to_cwd_data_dir(tmp_path, monkeypatch):
    gallery_routes = _gallery_module()
    configured_dir = tmp_path / "configured" / "generated_images"
    cwd_root = tmp_path / "cwd"
    cwd_image_dir = cwd_root / "data" / "generated_images"
    cwd_image_dir.mkdir(parents=True)
    (cwd_image_dir / "abc123.png").write_bytes(b"wrong root")
    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", configured_dir)
    monkeypatch.chdir(cwd_root)

    path = gallery_routes._gallery_image_path("abc123.png")

    assert path == configured_dir / "abc123.png"
    assert path != cwd_image_dir / "abc123.png"


@pytest.mark.parametrize("filename", ["../../secret.png", "..\\secret.png", None, 12345])
def test_gallery_image_path_rejects_unsafe_stored_filenames(tmp_path, monkeypatch, filename):
    gallery_routes = _gallery_module()
    image_dir = tmp_path / "generated_images"
    image_dir.mkdir()
    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", image_dir)

    with pytest.raises(HTTPException) as exc:
        gallery_routes._gallery_image_path(filename)

    assert exc.value.status_code == 400


def test_gallery_image_path_rejects_symlink_escape(tmp_path, monkeypatch):
    gallery_routes = _gallery_module()
    image_dir = tmp_path / "generated_images"
    image_dir.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside image root")
    link = image_dir / "escape.png"
    try:
        os.symlink(outside, link)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", image_dir)

    with pytest.raises(HTTPException) as exc:
        gallery_routes._gallery_image_path("escape.png")

    assert exc.value.status_code == 400


def test_gallery_replace_rejects_symlink_escape(tmp_path, monkeypatch):
    gallery_routes = _gallery_module()
    image_dir = tmp_path / "generated_images"
    image_dir.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside image root")
    link = image_dir / "escape.png"
    try:
        os.symlink(outside, link)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    engine = create_engine(
        f"sqlite:///{tmp_path / 'gallery.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    try:
        db.add(
            GalleryImage(
                id="img-1",
                filename="escape.png",
                prompt="escape",
                owner="alice",
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(gallery_routes, "GALLERY_IMAGE_DIR", image_dir)
    monkeypatch.setattr(gallery_routes, "SessionLocal", SessionLocal)
    monkeypatch.setattr(gallery_routes, "get_current_user", lambda request: "alice")

    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    client = TestClient(app)

    response = client.post(
        "/api/gallery/img-1/replace",
        files={"image": ("replacement.png", b"replacement bytes", "image/png")},
    )

    assert response.status_code == 400
    assert outside.read_bytes() == b"outside image root"


def test_gallery_file_operations_use_confining_resolver():
    source = Path("routes/gallery/gallery_routes.py").read_text(encoding="utf-8")

    assert 'Path("data/generated_images") / img.filename' not in source
    assert 'os.path.join("data", "generated_images", img.filename)' not in source
    assert 'os.path.join("data", "generated_images", img_filename)' not in source
    assert source.count("_gallery_image_path(img.filename)") >= 3
    assert "_gallery_image_path(img_filename)" in source
