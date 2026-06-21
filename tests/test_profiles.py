"""Unit tests for services.profiles.profiles.

Tests:
- MAX profile has required fields with correct defaults
- DAILY profile has required fields with correct defaults
- CUSTOM profile falls back to defaults when profiles.json is absent
- list_profiles() returns three profiles in order
- get_profile() returns correct profile or None for unknown key
- save_custom() persists overrides and returns merged dict
"""

import json
import os
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

_REQUIRED_KEYS = {"key", "label", "description", "ttft_estimate",
                  "ctx_size", "gpu_layers", "flash_attn", "features", "is_builtin"}


def _import_module(monkeypatch, tmp_path):
    """Import the profiles module with DATA_DIR pointed at a tmp directory."""
    import importlib
    import sys

    # Redirect DATA_DIR so we don't touch the real data/profiles.json.
    constants_mod = type(sys)("core.constants")
    constants_mod.DATA_DIR = str(tmp_path)
    monkeypatch.setitem(sys.modules, "core.constants", constants_mod)

    # Force a fresh import so _PROFILES_FILE picks up the patched DATA_DIR.
    sys.modules.pop("services.profiles.profiles", None)
    sys.modules.pop("services.profiles", None)
    mod = importlib.import_module("services.profiles.profiles")
    return mod


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def profiles(monkeypatch, tmp_path):
    return _import_module(monkeypatch, tmp_path)


@pytest.fixture()
def profiles_with_custom(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch, tmp_path)
    # Pre-write a custom profile to disk.
    custom_data = {"custom": {"ctx_size": 12288, "ttft_estimate": "~5 s"}}
    (tmp_path / "profiles.json").write_text(json.dumps(custom_data), encoding="utf-8")
    return mod, tmp_path


# ── built-in profile structure ─────────────────────────────────────────────────

def test_max_profile_has_required_fields(profiles):
    """MAX profile contains all required keys."""
    p = profiles.get_profile("max")
    assert p is not None
    assert _REQUIRED_KEYS.issubset(p.keys()), f"Missing keys: {_REQUIRED_KEYS - p.keys()}"


def test_max_profile_defaults(profiles):
    """MAX profile: ctx=16384, gpu_layers=99, flash_attn=True, reasoning feature True."""
    p = profiles.get_profile("max")
    assert p["ctx_size"] == 16384
    assert p["gpu_layers"] == 99
    assert p["flash_attn"] is True
    assert p["features"]["reasoning"] is True
    assert p["is_builtin"] is True


def test_daily_profile_defaults(profiles):
    """DAILY profile: ctx=4096, reasoning feature False."""
    p = profiles.get_profile("daily")
    assert p is not None
    assert p["ctx_size"] == 4096
    assert p["features"]["reasoning"] is False
    assert p["is_builtin"] is True


def test_custom_profile_fallback_defaults(profiles):
    """CUSTOM profile returns DAILY-like defaults when profiles.json is absent."""
    p = profiles.get_profile("custom")
    assert p is not None
    assert p["key"] == "custom"
    assert p["is_builtin"] is False
    assert isinstance(p["ctx_size"], int) and p["ctx_size"] > 0


def test_get_profile_unknown_key_returns_none(profiles):
    """get_profile() returns None for an unknown key."""
    assert profiles.get_profile("nonexistent") is None
    assert profiles.get_profile("") is None


# ── list_profiles ──────────────────────────────────────────────────────────────

def test_list_profiles_returns_three_in_order(profiles):
    """list_profiles() returns [max, daily, custom] in that order."""
    lst = profiles.list_profiles()
    assert len(lst) == 3
    keys = [p["key"] for p in lst]
    assert keys == ["max", "daily", "custom"]


def test_list_profiles_all_have_required_fields(profiles):
    """Every profile returned by list_profiles() has all required keys."""
    for p in profiles.list_profiles():
        assert _REQUIRED_KEYS.issubset(p.keys()), f"{p['key']} missing keys"


# ── save_custom ────────────────────────────────────────────────────────────────

def test_save_custom_persists_overrides(profiles, tmp_path):
    """save_custom() writes overrides to profiles.json and returns merged dict."""
    result = profiles.save_custom({"ctx_size": 8192, "label": "My Profile"})
    assert result["ctx_size"] == 8192
    assert result["label"] == "My Profile"
    assert result["key"] == "custom"
    assert result["is_builtin"] is False

    # Verify on-disk persistence.
    stored = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert stored["custom"]["ctx_size"] == 8192


def test_save_custom_strips_builtin_key(profiles):
    """save_custom() always sets key='custom' and is_builtin=False."""
    result = profiles.save_custom({"key": "max", "is_builtin": True, "ctx_size": 1024})
    assert result["key"] == "custom"
    assert result["is_builtin"] is False


def test_custom_profile_loads_persisted_values(profiles_with_custom):
    """get_profile('custom') returns the persisted ctx_size after save."""
    mod, _ = profiles_with_custom
    p = mod.get_profile("custom")
    assert p["ctx_size"] == 12288


def test_custom_profile_merges_with_defaults(profiles_with_custom):
    """Partially overridden custom profile still has all required fields."""
    mod, _ = profiles_with_custom
    p = mod.get_profile("custom")
    assert _REQUIRED_KEYS.issubset(p.keys())
