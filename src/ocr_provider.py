"""Optional first-party OCR provider integration.

This module keeps OCR behind settings/env controls. It never makes OCR output
authoritative: callers should treat returned text as extracted evidence that a
user may correct.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODE = "quality"
DEFAULT_PURPOSE = "odysseus.document"


def ocr_settings() -> dict:
    try:
        from src.settings import load_settings

        return load_settings()
    except Exception:
        return {}


def is_ocr_enabled() -> bool:
    settings = ocr_settings()
    return bool(settings.get("ocr_enabled", False))


def extract_ocr_text_sync(
    path: str,
    *,
    purpose: str = DEFAULT_PURPOSE,
    mode: str | None = None,
    timeout: float = 180.0,
) -> str:
    """Return OCR text from the configured OCR service, or "" on disabled/failure."""

    settings = ocr_settings()
    if not bool(settings.get("ocr_enabled", False)):
        return ""

    service_url = str(settings.get("ocr_service_url") or os.environ.get("ODYSSEUS_OCR_SERVICE_URL") or "").strip()
    if not service_url:
        logger.info("OCR enabled but no ocr_service_url is configured")
        return ""

    token = str(settings.get("ocr_api_key") or os.environ.get("ODYSSEUS_OCR_API_KEY") or "").strip()
    quality_mode = str(mode or settings.get("ocr_quality_mode") or DEFAULT_MODE)
    provider = str(settings.get("ocr_provider") or "skill-ocr")

    file_path = Path(path)
    if not file_path.is_file():
        return ""

    try:
        with file_path.open("rb") as handle:
            files = {"file": (file_path.name, handle, _guess_mime(file_path))}
            data = {
                "purpose": purpose,
                "mode": quality_mode,
                "retention": "none",
                "redaction": "strict",
            }
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = httpx.post(service_url, files=files, data=data, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        text = _extract_text(payload)
        if not text:
            logger.info("OCR provider %s returned no text for %s", provider, file_path.name)
        return text
    except Exception as exc:
        logger.warning("OCR provider %s failed for %s: %s", provider, file_path.name, type(exc).__name__)
        return ""


def _extract_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("chosenText", "chosenMarkdown", "text", "rawText", "extractedText"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    lines = payload.get("lines")
    if isinstance(lines, list):
        values = []
        for line in lines:
            if isinstance(line, str):
                values.append(line.strip())
            elif isinstance(line, dict) and isinstance(line.get("text"), str):
                values.append(line["text"].strip())
        return "\n".join(value for value in values if value).strip()
    return ""


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix in {".tif", ".tiff"}:
        return "image/tiff"
    return "image/png"
