"""Fugassa per-tool configuration (not Titan global settings)."""

from __future__ import annotations

import json
import os
from typing import Any

from titan.fugassa.paths import CONFIG_PATH, FUGASSA_ROOT

DEFAULT_CONFIG: dict[str, Any] = {
    "llm_enabled": True,
    "images_enabled": True,
    "image_style_default": "fantasy",
    "debug_ai_logging": False,
    "language": "cs",
    "hud_theme": "default",
}


def ensure_layout() -> None:
    os.makedirs(FUGASSA_ROOT, exist_ok=True)
    if not os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
            f.write("\n")


def load() -> dict[str, Any]:
    ensure_layout()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {**DEFAULT_CONFIG, **data}
    except (OSError, json.JSONDecodeError):
        pass
    return dict(DEFAULT_CONFIG)


def save(data: dict[str, Any]) -> dict[str, Any]:
    ensure_layout()
    merged = {**DEFAULT_CONFIG, **{k: v for k, v in data.items() if k in DEFAULT_CONFIG}}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return merged
