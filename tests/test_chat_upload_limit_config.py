import io

import pytest
from fastapi import HTTPException, UploadFile

from src.chat_helpers import validate_file_upload
from src.upload_handler import UploadHandler
from src.upload_limits import (
    DEFAULT_CHAT_UPLOAD_MAX_BYTES,
    get_chat_upload_max_bytes,
    read_byte_limit_env,
)


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


def test_chat_upload_limit_defaults_to_10mb(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_CHAT_UPLOAD_MAX_BYTES", raising=False)

    assert get_chat_upload_max_bytes() == DEFAULT_CHAT_UPLOAD_MAX_BYTES


def test_chat_upload_limit_uses_env_bytes(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_CHAT_UPLOAD_MAX_BYTES", "12345")

    assert get_chat_upload_max_bytes() == 12345


def test_chat_upload_limit_rejects_invalid_env(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_CHAT_UPLOAD_MAX_BYTES", "not-bytes")

    with pytest.raises(ValueError, match="ODYSSEUS_CHAT_UPLOAD_MAX_BYTES"):
        get_chat_upload_max_bytes()


def test_read_byte_limit_env_rejects_non_positive(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_CHAT_UPLOAD_MAX_BYTES", "0")

    with pytest.raises(ValueError, match="greater than 0"):
        read_byte_limit_env("ODYSSEUS_CHAT_UPLOAD_MAX_BYTES", 10)


def test_validate_file_upload_uses_configured_chat_limit(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_CHAT_UPLOAD_MAX_BYTES", "4")

    with pytest.raises(HTTPException) as exc:
        validate_file_upload(_upload("too-large.txt", b"abcde"))

    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "FILE_TOO_LARGE"
    assert exc.value.detail["message"] == "File size exceeds 4 bytes limit"


def test_upload_handler_uses_configured_chat_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_CHAT_UPLOAD_MAX_BYTES", "4")
    handler = UploadHandler(base_dir=str(tmp_path), upload_dir=str(tmp_path / "uploads"))

    with pytest.raises(HTTPException) as exc:
        handler.save_upload(_upload("too-large.txt", b"abcde"), client_ip="127.0.0.1")

    assert exc.value.status_code == 400
    assert exc.value.detail == "File size exceeds 4 bytes limit"
