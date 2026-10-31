"""Server-generated files (chat PDF export) stored as owner-scoped uploads."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from tests.test_upload_routes_owner_scope import _AuthManager, _Request, _upload_endpoints

PDF_BYTES = b"%PDF-1.4\n% generated test document\n%%EOF\n"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from src import constants
    from src.upload_handler import UploadHandler

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(constants, "UPLOAD_DIR", str(upload_dir))
    return UploadHandler(str(tmp_path), str(upload_dir)), upload_dir


def test_saved_file_is_indexed_for_its_owner(store):
    handler, upload_dir = store
    saved = handler.save_generated_file(PDF_BYTES, "Fix boot error.pdf", "application/pdf", owner="alice")

    assert handler.validate_upload_id(saved["id"])
    assert saved["id"].endswith(".pdf")
    assert saved["mime"] == "application/pdf"
    assert saved["size"] == len(PDF_BYTES)
    assert saved["owner"] == "alice"
    assert saved["original_name"] == "Fix boot error.pdf"
    assert saved["source"] == "generated"
    with open(saved["path"], "rb") as fh:
        assert fh.read() == PDF_BYTES
    index = json.loads((upload_dir / "uploads.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in index.values()] == [saved["id"]]


def test_owner_downloads_it_as_an_attachment(store, monkeypatch):
    handler, _ = store
    saved = handler.save_generated_file(PDF_BYTES, "Fix boot error.pdf", "application/pdf", owner="alice")
    download_file = _upload_endpoints(handler, monkeypatch)["download_file"]

    response = asyncio.run(download_file(_Request(user="alice", auth_manager=_AuthManager()), saved["id"]))

    assert response.media_type == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    # The route serves every upload under its sanitized name.
    assert 'filename="Fix_boot_error.pdf"' in disposition


def test_other_users_and_anonymous_cannot_download_it(store, monkeypatch):
    handler, _ = store
    saved = handler.save_generated_file(PDF_BYTES, "private.pdf", "application/pdf", owner="alice")
    download_file = _upload_endpoints(handler, monkeypatch)["download_file"]

    with pytest.raises(HTTPException) as other:
        asyncio.run(download_file(_Request(user="bob", auth_manager=_AuthManager()), saved["id"]))
    with pytest.raises(HTTPException) as anonymous:
        asyncio.run(download_file(_Request(auth_manager=_AuthManager()), saved["id"]))

    assert other.value.status_code == 404
    assert anonymous.value.status_code == 403


def test_identical_bytes_for_the_same_owner_reuse_one_upload(store):
    handler, upload_dir = store
    first = handler.save_generated_file(PDF_BYTES, "a.pdf", "application/pdf", owner="alice")
    second = handler.save_generated_file(PDF_BYTES, "a.pdf", "application/pdf", owner="alice")
    other_owner = handler.save_generated_file(PDF_BYTES, "a.pdf", "application/pdf", owner="bob")

    assert second["id"] == first["id"]
    assert other_owner["id"] != first["id"]
    assert len(json.loads((upload_dir / "uploads.json").read_text(encoding="utf-8"))) == 2


@pytest.mark.parametrize("data, filename, mime", [
    (b"", "empty.pdf", "application/pdf"),
    (b"MZ\x90\x00 executable", "tool.exe", "application/x-msdownload"),
])
def test_rejects_empty_or_unsafe_files(store, data, filename, mime):
    handler, _ = store
    with pytest.raises(ValueError):
        handler.save_generated_file(data, filename, mime, owner="alice")


def test_rejects_files_over_the_upload_limit(store):
    handler, _ = store
    handler.max_upload_size = 10
    with pytest.raises(ValueError):
        handler.save_generated_file(PDF_BYTES, "big.pdf", "application/pdf", owner="alice")
