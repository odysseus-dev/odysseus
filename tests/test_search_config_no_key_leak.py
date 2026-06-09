"""Regression guard for #1661 — GET /api/search/config must not leak API keys.

`get_search_config()` returned `SEARCH_CONFIG.copy()`, and `update_search_config()`
cached the decrypted Brave key into that shared global at startup
(`src/app_initializer.py`), so the unauthenticated `/api/search/config` route
exposed the operator's key. The key is read on demand via `_get_provider_key`
(`brave_search`), so the cache was dead weight. Now the secret is never cached in
the global, and `get_search_config` scrubs any credential field from its response
while preserving the `has_api_key` presence flag.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

from services.search import core


def test_update_search_config_does_not_cache_secret():
    core.update_search_config(api_key="SUPER_SECRET")
    assert "brave_api_key" not in core.SEARCH_CONFIG
    assert "SUPER_SECRET" not in core.SEARCH_CONFIG.values()


@pytest.fixture
def stub_settings(monkeypatch):
    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "brave"})
    monkeypatch.setattr(core, "_get_provider_key", lambda provider: "REAL_SECRET_KEY")
    monkeypatch.setattr(core, "_get_result_count", lambda: 10)


def test_get_search_config_never_returns_a_secret(stub_settings, monkeypatch):
    # Even if a secret somehow sits in the shared global, the response scrubs it.
    monkeypatch.setitem(core.SEARCH_CONFIG, "brave_api_key", "LEAKED_SECRET")

    cfg = core.get_search_config()

    assert "brave_api_key" not in cfg
    assert "LEAKED_SECRET" not in cfg.values()       # the cached secret
    assert "REAL_SECRET_KEY" not in cfg.values()     # the live provider key
    # Presence flag and non-secret fields are preserved.
    assert cfg["has_api_key"] is True
    assert cfg["active_provider"] == "brave"


def test_is_secret_key_keeps_presence_flag():
    # has_api_key matches the *_api_key suffix, but it is a bool — the isinstance
    # guard in get_search_config keeps it; only string-valued secrets are dropped.
    assert core._is_secret_key("brave_api_key") is True
    assert core._is_secret_key("has_api_key") is True
    assert core._is_secret_key("active_provider") is False
    assert core._is_secret_key("search_url") is False
