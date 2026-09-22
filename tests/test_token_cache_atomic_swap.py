"""Regression test for token cache race condition.

Exercises the actual _refresh_token_cache() and _token_cache in app.py
to verify the atomic swap fix eliminates the race window.
"""
import os
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app_module(monkeypatch):
    """Import app.py with AUTH_ENABLED=true and minimal mocked deps.

    Sets up a real AuthManager user ('admin') so normalize_known_username
    resolves the token owner.  Replaces SessionLocal with a MagicMock so
    _refresh_token_cache() can run without a real DB.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    # Clear cached app module
    monkeypatch.delitem(sys.modules, "app", raising=False)

    import app as app_mod  # noqa: E402

    app_mod.SessionLocal = MagicMock()
    app_mod.logger = MagicMock()
    app_mod.auth_manager.setup("admin", "TestPass123!")

    return app_mod


def _seed(app_mod, rows):
    """Set up the mocked SessionLocal to return *rows* on next query."""
    app_mod.SessionLocal.return_value.query.return_value.filter.return_value.all.return_value = rows


def _row(prefix, tid="t1", th="h1", owner="admin", scopes="chat"):
    return SimpleNamespace(
        token_prefix=prefix, id=tid, token_hash=th,
        owner=owner, scopes=scopes, is_active=True,
    )


# ---------------------------------------------------------------------------
# Tests — all use the REAL app._refresh_token_cache and REAL app._token_cache
# ---------------------------------------------------------------------------

class TestRefreshPopulatesCache:
    """Single refresh call should populate _token_cache from DB rows."""

    def test_single_row(self, app_module):
        _seed(app_module, [_row("ody_abc")])
        app_module.app.state._token_cache_dirty = True
        app_module._refresh_token_cache()
        assert "ody_abc" in app_module._token_cache
        assert app_module._token_cache["ody_abc"][0][0] == "t1"

    def test_multiple_prefixes(self, app_module):
        _seed(app_module, [
            _row("ody_aaaa", "t1", "h1", "admin", "chat"),
            _row("ody_bbbb", "t2", "h2", "admin", "chat,tools"),
            _row("ody_aaaa", "t3", "h3", "admin", "memory"),
        ])
        app_module.app.state._token_cache_dirty = True
        app_module._refresh_token_cache()
        assert len(app_module._token_cache) == 2
        assert len(app_module._token_cache["ody_aaaa"]) == 2
        assert app_module._token_cache["ody_bbbb"][0][3] == ["chat", "tools"]

    def test_empty_db_clears_cache(self, app_module):
        app_module._token_cache["stale"] = [("x", "y", "z", ["chat"])]
        _seed(app_module, [])
        app_module.app.state._token_cache_dirty = True
        app_module._refresh_token_cache()
        assert len(app_module._token_cache) == 0


class TestAppStateSync:
    """app.state._token_cache must stay synchronized with _token_cache."""

    def test_state_ref_matches_after_refresh(self, app_module):
        _seed(app_module, [_row("ody_sync")])
        app_module.app.state._token_cache_dirty = True
        app_module._refresh_token_cache()
        assert app_module.app.state._token_cache is app_module._token_cache
        assert "ody_sync" in app_module.app.state._token_cache

    def test_state_dirty_cleared(self, app_module):
        _seed(app_module, [_row("ody_x")])
        app_module.app.state._token_cache_dirty = True
        app_module._refresh_token_cache()
        assert app_module.app.state._token_cache_dirty is False


class TestConcurrentReaders:
    """The core regression: concurrent readers must never see an empty cache."""

    def test_no_empty_reads_during_refresh(self, app_module):
        """4 reader threads + 100 refreshes on the real _token_cache global."""
        _seed(app_module, [_row("ody_race")])
        app_module.app.state._token_cache_dirty = True
        app_module._refresh_token_cache()

        stop = threading.Event()
        results = {"empty": 0, "ok": 0}

        def reader():
            while not stop.is_set():
                if len(app_module._token_cache) == 0:
                    results["empty"] += 1
                else:
                    results["ok"] += 1

        def churner():
            for _ in range(100):
                app_module._refresh_token_cache()

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
        for t in readers:
            t.start()

        churn = threading.Thread(target=churner)
        churn.start()

        time.sleep(0.1)
        churn.join(timeout=5)
        stop.set()
        for t in readers:
            t.join(timeout=2)

        assert results["empty"] == 0, (
            "Readers saw empty cache %d times (ok=%d)"
            % (results["empty"], results["ok"])
        )
        assert results["ok"] > 0

    def test_no_empty_reads_with_token_churn(self, app_module):
        """Simulate token create/revoke churn while reading."""
        _seed(app_module, [_row("ody_keep", "t1", "h1", "admin", "chat")])
        app_module.app.state._token_cache_dirty = True
        app_module._refresh_token_cache()

        stop = threading.Event()
        results = {"empty": 0, "ok": 0}

        def reader():
            while not stop.is_set():
                if len(app_module._token_cache) == 0:
                    results["empty"] += 1
                else:
                    results["ok"] += 1

        def churner():
            for i in range(50):
                _seed(app_module, [
                    _row("ody_keep", "t1", "h1", "admin", "chat"),
                    _row("ody_new_%d" % i, "t%d" % (i + 10), "h%d" % (i + 10), "admin", "chat"),
                ])
                app_module._refresh_token_cache()
                _seed(app_module, [_row("ody_keep", "t1", "h1", "admin", "chat")])
                app_module._refresh_token_cache()

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
        for t in readers:
            t.start()

        churn = threading.Thread(target=churner)
        churn.start()

        churn.join(timeout=10)
        stop.set()
        for t in readers:
            t.join(timeout=2)

        assert results["empty"] == 0, (
            "Readers saw empty cache %d times during churn (ok=%d)"
            % (results["empty"], results["ok"])
        )
        assert results["ok"] > 0
        assert "ody_keep" in app_module._token_cache
        assert len(app_module._token_cache) == 1
