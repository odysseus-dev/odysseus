"""Named user-intent serving profiles for llama.cpp.

Three built-in profiles map user intent to concrete llama-server flags:

- MAX:    highest quality — full context, reasoning-capable models run best,
          all layers on GPU. Long TTFT, best answers.
- DAILY:  everyday speed — short context, fast replies, all layers on GPU.
- CUSTOM: user-editable. Inherits DAILY defaults on first use; overrides
          persist in ``data/profiles.json`` (gitignored user data).

Built-ins are code constants and are never written to disk. The CUSTOM profile
reads from and writes to ``data/profiles.json``.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from core.constants import DATA_DIR

_PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")

# Canonical built-in profile definitions. Keys match what the API returns.
_BUILT_INS: dict[str, dict[str, Any]] = {
    "max": {
        "key": "max",
        "label": "Max",
        "description": (
            "Highest quality: 16 k context, all GPU layers, reasoning-capable. "
            "Best answers; slowest first token (~10–15 s TTFT on a 12 GB card)."
        ),
        "ttft_estimate": "~10–15 s",
        "ctx_size": 16384,
        "gpu_layers": 99,
        "flash_attn": True,
        "features": {"reasoning": True, "vision": True, "tools": True},
        "is_builtin": True,
    },
    "daily": {
        "key": "daily",
        "label": "Daily",
        "description": (
            "Everyday speed: 4 k context, all GPU layers, fast replies. "
            "Good for chat and quick tasks (~1–2 s TTFT)."
        ),
        "ttft_estimate": "~1–2 s",
        "ctx_size": 4096,
        "gpu_layers": 99,
        "flash_attn": True,
        "features": {"reasoning": False, "vision": True, "tools": True},
        "is_builtin": True,
    },
}

# CUSTOM defaults — used when data/profiles.json is absent or empty.
_CUSTOM_DEFAULTS: dict[str, Any] = {
    "key": "custom",
    "label": "Custom",
    "description": "User-editable preset. Adjust via the Cookbook serve panel.",
    "ttft_estimate": "varies",
    "ctx_size": 8192,
    "gpu_layers": 99,
    "flash_attn": True,
    "features": {"reasoning": False, "vision": True, "tools": True},
    "is_builtin": False,
}


def _load_custom() -> dict[str, Any]:
    """Load the CUSTOM profile from disk, falling back to defaults."""
    try:
        with open(_PROFILES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        custom = data.get("custom") if isinstance(data, dict) else None
        if isinstance(custom, dict) and custom:
            merged = deepcopy(_CUSTOM_DEFAULTS)
            merged.update(custom)
            merged["key"] = "custom"
            merged["is_builtin"] = False
            return merged
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return deepcopy(_CUSTOM_DEFAULTS)


def save_custom(fields: dict[str, Any]) -> dict[str, Any]:
    """Persist user-supplied CUSTOM profile fields to disk.

    Args:
        fields: Partial or full override dict. Unknown keys are accepted so
            future fields can be saved without a code change.

    Returns:
        The full merged CUSTOM profile dict after saving.
    """
    current = _load_custom()
    current.update(fields)
    current["key"] = "custom"
    current["is_builtin"] = False
    try:
        existing: dict[str, Any] = {}
        try:
            with open(_PROFILES_FILE, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        existing["custom"] = current
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_PROFILES_FILE, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)
    except OSError:
        pass
    return current


def list_profiles() -> list[dict[str, Any]]:
    """Return all profiles in display order: MAX, DAILY, CUSTOM.

    Returns:
        List of profile dicts, each with key, label, description, ttft_estimate,
        ctx_size, gpu_layers, flash_attn, features, is_builtin.
    """
    return [
        deepcopy(_BUILT_INS["max"]),
        deepcopy(_BUILT_INS["daily"]),
        _load_custom(),
    ]


def get_profile(key: str) -> dict[str, Any] | None:
    """Return a single profile by key, or None if not found.

    Args:
        key: Profile identifier — ``"max"``, ``"daily"``, or ``"custom"``.

    Returns:
        Profile dict or None.
    """
    key = (key or "").lower().strip()
    if key in _BUILT_INS:
        return deepcopy(_BUILT_INS[key])
    if key == "custom":
        return _load_custom()
    return None
