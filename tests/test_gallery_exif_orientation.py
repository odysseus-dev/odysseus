"""Gallery EXIF extraction must report display (EXIF-rotated) dimensions.

A phone photo with EXIF Orientation 6 or 8 is stored e.g. 400x300 but
displayed 300x400. _extract_exif read img.width/img.height from the raw
buffer, so the gallery recorded the wrong aspect ratio for rotated photos
while upload_handler (which applies ImageOps.exif_transpose) got it right.
"""

import importlib
import sys
import types
from io import BytesIO
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PIL")
from PIL import Image


@pytest.fixture
def extract_exif(monkeypatch):
    """Import routes.gallery_helpers under a core.database stub.

    _extract_exif never touches the DB, but the module imports GalleryImage
    at import time and the conftest sqlalchemy stubs make the real
    core.database unimportable in isolation.
    """

    class _DBStub(types.ModuleType):
        def __getattr__(self, name):
            return MagicMock()

    monkeypatch.setitem(sys.modules, "core.database", _DBStub("core.database"))
    monkeypatch.delitem(sys.modules, "routes.gallery_helpers", raising=False)
    mod = importlib.import_module("routes.gallery_helpers")
    return mod._extract_exif


def _jpeg(width, height, orientation=None, make=None):
    img = Image.new("RGB", (width, height), "blue")
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation  # Orientation
    if make is not None:
        exif[0x010F] = make  # Make
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_orientation_6_reports_display_dimensions(extract_exif):
    res = extract_exif(_jpeg(400, 300, orientation=6))
    assert (res["width"], res["height"]) == (300, 400)


def test_orientation_8_reports_display_dimensions(extract_exif):
    res = extract_exif(_jpeg(400, 300, orientation=8))
    assert (res["width"], res["height"]) == (300, 400)


def test_no_orientation_keeps_raw_dimensions(extract_exif):
    res = extract_exif(_jpeg(400, 300))
    assert (res["width"], res["height"]) == (400, 300)


def test_camera_fields_survive_the_transpose(extract_exif):
    # exif_transpose strips the EXIF view, so tags must be read before it
    res = extract_exif(_jpeg(400, 300, orientation=6, make="TestMake"))
    assert res["camera_make"] == "TestMake"
    assert (res["width"], res["height"]) == (300, 400)
