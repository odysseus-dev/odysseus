"""Regression tests: WebhookManager._deliver retries on transient errors.

conftest.py stubs src.database (SessionLocal/ModelEndpoint only, no Webhook), so
webhook_manager — which does `from src.database import SessionLocal, Webhook` —
cannot be imported naively under pytest. Follow the repo's established pattern
(see test_webhook_ssrf_resilience.py): drop the stub inside a preserve_import_state
block, pin DATABASE_URL to in-memory so importing the real core.database doesn't
run create_all() against a missing ./data dir, import the real module, then let
preserve_import_state restore sys.modules so siblings aren't affected.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

from tests.helpers.import_state import clear_module, preserve_import_state

with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}), \
        preserve_import_state("src.database", "core.database"):
    clear_module("src.database")
    _core_database = sys.modules.get("core.database")
    _core_database_all = (
        getattr(_core_database, "__all__", None) if _core_database is not None else None
    )
    if _core_database is not None and (
        not getattr(_core_database, "__file__", None)
        or (
            _core_database_all is not None
            and (
                not isinstance(_core_database_all, (list, tuple, set))
                or not all(isinstance(name, str) for name in _core_database_all)
            )
        )
    ):
        del sys.modules["core.database"]
    import src.webhook_manager as webhook_manager
    from src.webhook_manager import WebhookManager


def _make_response(status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def _mock_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.update.return_value = None
    db.commit.return_value = None
    db.close.return_value = None
    return db


async def _no_sleep(_seconds):
    pass


def test_deliver_retries_on_429(monkeypatch):
    """_deliver must retry at least once when the consumer returns 429."""
    call_count = [0]

    async def _fake_post(*a, **kw):
        call_count[0] += 1
        return _make_response(429 if call_count[0] < 3 else 200)

    monkeypatch.setattr(webhook_manager, "SessionLocal", _mock_db)
    monkeypatch.setattr(webhook_manager.asyncio, "sleep", _no_sleep)

    mgr = WebhookManager()
    mgr._client = MagicMock()
    mgr._client.post = _fake_post

    asyncio.run(mgr._deliver("wh1", "https://example.com/hook", None, "chat.completed", {"x": 1}))

    assert call_count[0] >= 2, f"Expected at least 2 HTTP calls (retry), got {call_count[0]}"


def test_deliver_succeeds_on_first_attempt(monkeypatch):
    """_deliver must not retry when the first attempt succeeds."""
    call_count = [0]

    async def _fake_post(*a, **kw):
        call_count[0] += 1
        return _make_response(200)

    monkeypatch.setattr(webhook_manager, "SessionLocal", _mock_db)

    mgr = WebhookManager()
    mgr._client = MagicMock()
    mgr._client.post = _fake_post

    asyncio.run(mgr._deliver("wh2", "https://example.com/hook", None, "chat.completed", {"x": 1}))

    assert call_count[0] == 1, f"Expected exactly 1 HTTP call (no retry needed), got {call_count[0]}"


def test_deliver_records_error_after_exhausting_retries(monkeypatch):
    """_deliver must record a terminal status/error to the DB when every attempt fails."""
    async def _fake_post(*a, **kw):
        return _make_response(503)

    update_calls = []
    db = _mock_db()
    db.query.return_value.filter.return_value.update.side_effect = lambda d: update_calls.append(d)
    monkeypatch.setattr(webhook_manager, "SessionLocal", lambda: db)
    monkeypatch.setattr(webhook_manager.asyncio, "sleep", _no_sleep)

    mgr = WebhookManager()
    mgr._client = MagicMock()
    mgr._client.post = _fake_post

    asyncio.run(mgr._deliver("wh3", "https://example.com/hook", None, "webhook.test", {}))

    assert update_calls, "Expected at least one DB update call"
    last_update = update_calls[-1]
    assert last_update.get("last_status_code") is not None or last_update.get("last_error") is not None, \
        f"Expected last_status_code or last_error in final update, got {last_update!r}"
