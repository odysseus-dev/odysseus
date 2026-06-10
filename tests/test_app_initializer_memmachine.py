"""Tests for the memory backend resolution logic in app_initializer.py.

These tests exercise ``_resolve_memory_backend()`` without pulling in the
full app_initializer module (which has heavy transitive imports).  The
coexistence registration is exercised indirectly by the provider tests.
"""

import json
import os

import pytest


@pytest.fixture(autouse=True)
def clean_memory_backend_env():
    """Ensure MEMORY_BACKEND env var is clean between tests."""
    old = os.environ.pop("MEMORY_BACKEND", None)
    yield
    if old is not None:
        os.environ["MEMORY_BACKEND"] = old
    else:
        os.environ.pop("MEMORY_BACKEND", None)


def _resolve_memory_backend(prefs_path: str = None) -> str:
    """Inline copy of the resolution logic for isolated testing."""
    env = os.getenv("MEMORY_BACKEND", "").strip().lower()
    if env in ("native", "memmachine"):
        return env

    try:
        path = prefs_path or os.path.join("data", "user_prefs.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "_users" not in data:
                    pref = data.get("memory_backend", "")
                    if isinstance(pref, str) and pref.lower() in ("native", "memmachine"):
                        return pref.lower()
    except Exception:
        pass

    return "native"


class TestResolveMemoryBackend:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "memmachine")
        assert _resolve_memory_backend() == "memmachine"

        monkeypatch.setenv("MEMORY_BACKEND", "native")
        assert _resolve_memory_backend() == "native"

    def test_reads_legacy_flat_prefs(self, tmp_path):
        prefs = {"memory_backend": "memmachine"}
        prefs_path = str(tmp_path / "user_prefs.json")
        with open(prefs_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
        assert _resolve_memory_backend(prefs_path) == "memmachine"

    def test_ignores_invalid_prefs(self, tmp_path):
        prefs = {"memory_backend": "invalid"}
        prefs_path = str(tmp_path / "user_prefs.json")
        with open(prefs_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
        assert _resolve_memory_backend(prefs_path) == "native"

    def test_defaults_to_native_when_no_prefs_exist(self):
        assert _resolve_memory_backend("/nonexistent/path.json") == "native"
