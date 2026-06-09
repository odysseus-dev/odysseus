"""Small helpers for route-local upload size caps."""

import os

from fastapi import HTTPException, UploadFile

DEFAULT_CHAT_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
CHAT_UPLOAD_MAX_BYTES_ENV = "ODYSSEUS_CHAT_UPLOAD_MAX_BYTES"


def format_byte_limit(limit: int) -> str:
    if limit % (1024 * 1024) == 0:
        return f"{limit // (1024 * 1024)} MB"
    if limit % 1024 == 0:
        return f"{limit // 1024} KB"
    return f"{limit} bytes"


def read_byte_limit_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer byte count") from exc
    if limit < 1:
        raise ValueError(f"{name} must be greater than 0")
    return limit


def get_chat_upload_max_bytes() -> int:
    return read_byte_limit_env(CHAT_UPLOAD_MAX_BYTES_ENV, DEFAULT_CHAT_UPLOAD_MAX_BYTES)


# Per-route upload byte-limits, single-sourced here (issue #3364). Each is
# validated + env-overridable via read_byte_limit_env: set the matching
# ODYSSEUS_*_MAX_BYTES env var to an integer byte count to tune it; an invalid
# value fails fast at import rather than crashing mid-request. Defaults match
# the prior per-route values, so behavior is unchanged unless an env var is set.
GALLERY_UPLOAD_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_GALLERY_UPLOAD_MAX_BYTES", 100 * 1024 * 1024
)
GALLERY_TRANSFORM_UPLOAD_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_GALLERY_TRANSFORM_UPLOAD_MAX_BYTES", 25 * 1024 * 1024
)
MEMORY_IMPORT_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_MEMORY_IMPORT_MAX_BYTES", 10 * 1024 * 1024
)
PERSONAL_UPLOAD_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_PERSONAL_UPLOAD_MAX_BYTES", 25 * 1024 * 1024
)
EMAIL_COMPOSE_UPLOAD_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_EMAIL_COMPOSE_UPLOAD_MAX_BYTES", 25 * 1024 * 1024
)
STT_MAX_AUDIO_BYTES = read_byte_limit_env(
    "ODYSSEUS_STT_MAX_AUDIO_BYTES", 25 * 1024 * 1024
)
ICS_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_ICS_MAX_BYTES", 10 * 1024 * 1024
)


async def read_upload_limited(upload: UploadFile, limit: int, label: str = "Upload") -> bytes:
    """Read an UploadFile with a hard byte cap."""
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"{label} exceeds {format_byte_limit(limit)} limit",
        )
    return data
