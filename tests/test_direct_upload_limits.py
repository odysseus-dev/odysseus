import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from src.upload_limits import format_byte_limit, read_upload_limited

REPO = Path(__file__).resolve().parent.parent


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


def _source(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


async def test_read_upload_limited_accepts_exact_limit():
    assert await read_upload_limited(_upload("ok.bin", b"abcd"), 4, "Test upload") == b"abcd"


async def test_read_upload_limited_rejects_oversized_upload():
    with pytest.raises(HTTPException) as exc:
        await read_upload_limited(_upload("too-big.bin", b"abcde"), 4, "Test upload")

    assert exc.value.status_code == 413
    assert exc.value.detail == "Test upload exceeds 4 bytes limit"


def test_upload_limit_formatting_is_human_readable():
    assert format_byte_limit(25 * 1024 * 1024) == "25 MB"
    assert format_byte_limit(512 * 1024) == "512 KB"
    assert format_byte_limit(7) == "7 bytes"


def test_direct_upload_routes_use_bounded_reads():
    expectations = {
        "routes/stt_routes.py": [
            "read_upload_limited(file, STT_MAX_AUDIO_BYTES",
        ],
        "routes/gallery_routes.py": [
            "read_upload_limited(file, GALLERY_UPLOAD_MAX_BYTES",
            "read_upload_limited(file, GALLERY_TRANSFORM_UPLOAD_MAX_BYTES",
        ],
        "routes/memory_routes.py": [
            "read_upload_limited(file, MEMORY_IMPORT_MAX_BYTES",
        ],
        "routes/calendar_routes.py": [
            "read_upload_limited(file, _ICS_MAX_BYTES",
        ],
        "routes/email_routes.py": [
            "read_upload_limited(file, EMAIL_COMPOSE_UPLOAD_MAX_BYTES",
        ],
    }

    for path, needles in expectations.items():
        text = _source(path)
        for needle in needles:
            assert needle in text
