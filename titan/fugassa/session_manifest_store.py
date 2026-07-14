"""Server-side session manifest backup (pairs with localStorage)."""

from __future__ import annotations

import json
import os
from typing import Any

from titan.fugassa.paths import SESSION_MANIFEST_PATH, FUGASSA_ROOT

DEFAULT_MANIFEST: dict[str, Any] = {
    "version": 1,
    "mode": "menu",
    "menuScreen": "home",
    "wizardStep": 0,
    "activeSaveId": None,
}


def ensure_layout() -> None:
    os.makedirs(FUGASSA_ROOT, exist_ok=True)


def load() -> dict[str, Any]:
    ensure_layout()
    try:
        with open(SESSION_MANIFEST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {**DEFAULT_MANIFEST, **data}
    except (OSError, json.JSONDecodeError):
        pass
    return dict(DEFAULT_MANIFEST)


def save(data: dict[str, Any]) -> dict[str, Any]:
    ensure_layout()
    merged = {**DEFAULT_MANIFEST, **{k: v for k, v in data.items() if k != "play" or isinstance(v, dict)}}
    if isinstance(data.get("play"), dict):
        merged["play"] = data["play"]
    with open(SESSION_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return merged
