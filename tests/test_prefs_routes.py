"""Tests for prefs_routes.py — per-user preference storage helpers."""

import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub auth_helpers so get_current_user doesn't pull in the full auth stack.
if "src.auth_helpers" not in sys.modules:
    _ah = types.ModuleType("src.auth_helpers")
    _ah.get_current_user = MagicMock(return_value=None)
    sys.modules["src.auth_helpers"] = _ah

import routes.prefs_routes as prefs_routes  # noqa: E402
from routes.prefs_routes import _load_for_user, _save_for_user


# ── _load helpers ──

class TestLoadForUser:
    def test_legacy_flat_format_returned_as_is(self, monkeypatch):
        monkeypatch.setattr(prefs_routes, "_load", lambda: {"theme": "dark", "lang": "en"})
        assert _load_for_user("alice") == {"theme": "dark", "lang": "en"}

    def test_multi_user_returns_correct_slice(self, monkeypatch):
        data = {"_users": {"alice": {"theme": "dark"}, "bob": {"theme": "light"}}}
        monkeypatch.setattr(prefs_routes, "_load", lambda: data)
        assert _load_for_user("alice") == {"theme": "dark"}
        assert _load_for_user("bob") == {"theme": "light"}

    def test_unknown_user_returns_empty_dict(self, monkeypatch):
        data = {"_users": {"alice": {"theme": "dark"}}}
        monkeypatch.setattr(prefs_routes, "_load", lambda: data)
        assert _load_for_user("charlie") == {}

    def test_auth_disabled_returns_first_user_prefs(self, monkeypatch):
        data = {"_users": {"alice": {"x": 1}, "bob": {"x": 2}}}
        monkeypatch.setattr(prefs_routes, "_load", lambda: data)
        # user=None means auth disabled — return the first user's prefs
        result = _load_for_user(None)
        assert result == {"x": 1}

    def test_auth_disabled_empty_users_returns_empty(self, monkeypatch):
        monkeypatch.setattr(prefs_routes, "_load", lambda: {"_users": {}})
        assert _load_for_user(None) == {}

    def test_empty_file_returns_empty(self, monkeypatch):
        monkeypatch.setattr(prefs_routes, "_load", lambda: {})
        assert _load_for_user("alice") == {}

    def test_returns_copy_not_reference(self, monkeypatch):
        data = {"_users": {"alice": {"theme": "dark"}}}
        monkeypatch.setattr(prefs_routes, "_load", lambda: data)
        result = _load_for_user("alice")
        result["theme"] = "light"
        # The original data should be unaffected
        assert data["_users"]["alice"]["theme"] == "dark"


class TestLoadRaw:
    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(tmp_path / "nonexistent.json"))
        assert prefs_routes._load() == {}

    def test_malformed_json_returns_empty(self, tmp_path, monkeypatch):
        f = tmp_path / "prefs.json"
        f.write_text("{broken json", encoding="utf-8")
        monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))
        assert prefs_routes._load() == {}

    def test_valid_json_loaded(self, tmp_path, monkeypatch):
        f = tmp_path / "prefs.json"
        f.write_text(json.dumps({"key": "val"}), encoding="utf-8")
        monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))
        assert prefs_routes._load() == {"key": "val"}


# ── _save helpers ──

class TestSaveForUser:
    def test_auth_disabled_saves_flat(self, monkeypatch, tmp_path):
        f = tmp_path / "prefs.json"
        monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))
        _save_for_user(None, {"theme": "solarized"})
        assert json.loads(f.read_text()) == {"theme": "solarized"}

    def test_new_user_creates_users_namespace(self, monkeypatch, tmp_path):
        f = tmp_path / "prefs.json"
        monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))
        _save_for_user("alice", {"lang": "fr"})
        saved = json.loads(f.read_text())
        assert saved == {"_users": {"alice": {"lang": "fr"}}}

    def test_existing_user_overwritten(self, monkeypatch, tmp_path):
        f = tmp_path / "prefs.json"
        f.write_text(json.dumps({"_users": {"alice": {"lang": "en"}}}))
        monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))
        _save_for_user("alice", {"lang": "de"})
        saved = json.loads(f.read_text())
        assert saved["_users"]["alice"] == {"lang": "de"}

    def test_other_users_preserved_on_save(self, monkeypatch, tmp_path):
        f = tmp_path / "prefs.json"
        f.write_text(json.dumps({"_users": {"alice": {"a": 1}, "bob": {"b": 2}}}))
        monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))
        _save_for_user("alice", {"a": 99})
        saved = json.loads(f.read_text())
        assert saved["_users"]["bob"] == {"b": 2}
        assert saved["_users"]["alice"] == {"a": 99}

    def test_flat_format_upgraded_on_first_named_save(self, monkeypatch, tmp_path):
        f = tmp_path / "prefs.json"
        # Legacy flat format present
        f.write_text(json.dumps({"theme": "dark"}))
        monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))
        _save_for_user("alice", {"theme": "light"})
        saved = json.loads(f.read_text())
        assert "_users" in saved
        assert saved["_users"]["alice"] == {"theme": "light"}
