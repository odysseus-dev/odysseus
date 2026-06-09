"""Upload ids must satisfy UPLOAD_ID_RE for every accepted filename.

secure_filename keeps '_' and '-', so a filename whose final extension
contains them (e.g. "photo.jpg-1" — the suffix browsers add to duplicate
downloads, or "doc.v1_final") produced an id like "<hex>.jpg-1" that fails
is_valid_upload_id. Since every read path (download, resolve, vision)
validates the id first, the saved bytes became permanently unreachable.
"""
import pytest

from src.upload_handler import _build_upload_id, is_valid_upload_id


@pytest.mark.parametrize("name", [
    "photo.jpg-1",
    "doc.v1_final",
    "invoice.2024-01",
    "file.JPG_backup",
    "report.pdf",
    "image.png",
    "noextension",
    "",
])
def test_built_id_is_always_valid(name):
    fid = _build_upload_id(name)
    assert is_valid_upload_id(fid), (name, fid)


def test_normal_extension_is_preserved():
    assert _build_upload_id("photo.png").endswith(".png")
    assert _build_upload_id("doc.pdf").endswith(".pdf")


def test_problem_extension_is_sanitized_not_dropped_to_invalid():
    fid = _build_upload_id("photo.jpg-1")
    assert is_valid_upload_id(fid)
    assert fid.endswith(".jpg1")  # the '-' is stripped, alnum kept
