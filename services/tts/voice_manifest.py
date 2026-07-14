"""Supertonic voice manifest — dummy Hlas 1..10 labels per language."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.constants import TTS_VOICE_MANIFEST_FILE

logger = logging.getLogger(__name__)

_SUPPORTED_LANGS = ("en", "cs", "uk")
_LABEL_PREFIX = {"cs": "Hlas", "en": "Voice", "uk": "Голос"}


def _default_manifest() -> dict[str, Any]:
    speakers = []
    for sid in range(10):
        labels = {lang: f"{_LABEL_PREFIX[lang]} {sid + 1}" for lang in _SUPPORTED_LANGS}
        speakers.append({"id": sid, "labels": labels})
    return {"engine": "supertonic-3", "speakers": speakers}


def load_voice_manifest() -> dict[str, Any]:
    path = Path(TTS_VOICE_MANIFEST_FILE)
    if not path.is_file():
        logger.warning("TTS voice manifest missing at %s — using built-in defaults", path)
        return _default_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("speakers"), list):
            return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read TTS voice manifest: %s", exc)
    return _default_manifest()


def list_voices_for_lang(lang: str) -> list[dict[str, Any]]:
    lang = (lang or "cs").strip().lower()
    if lang not in _SUPPORTED_LANGS:
        lang = "cs"
    out: list[dict[str, Any]] = []
    for entry in load_voice_manifest().get("speakers") or []:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if not isinstance(sid, int) or sid < 0 or sid > 9:
            continue
        labels = entry.get("labels") if isinstance(entry.get("labels"), dict) else {}
        label = str(labels.get(lang) or labels.get("cs") or f"Hlas {sid + 1}")
        item: dict[str, Any] = {"id": sid, "label": label, "lang": lang}
        if entry.get("code"):
            item["code"] = str(entry["code"])
        if entry.get("gender"):
            item["gender"] = str(entry["gender"])
        out.append(item)
    out.sort(key=lambda x: x["id"])
    return out
