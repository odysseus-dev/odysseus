import os
from pathlib import Path

import pytest
from fastapi import HTTPException


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


def test_gallery_file_operations_use_confining_resolver():
    source = Path("routes/gallery_routes.py").read_text(encoding="utf-8")

    assert 'Path("data/generated_images") / img.filename' not in source
    assert 'os.path.join("data", "generated_images", img.filename)' not in source
    assert 'os.path.join("data", "generated_images", img_filename)' not in source
    assert source.count("_gallery_image_path(img.filename)") >= 3
    assert "_gallery_image_path(img_filename)" in source
