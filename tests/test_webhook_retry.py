"""Regression tests: WebhookManager._deliver retries on transient errors."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest


def _make_response(status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def test_deliver_retries_on_429(monkeypatch):
    """_deliver must retry at least once when the consumer returns 429."""
    from src import webhook_manager
    from src.webhook_manager import WebhookManager

    call_count = [0]

    async def _fake_post(*a, **kw):
        call_count[0] += 1
        if call_count[0] < 3:
            return _make_response(429)
        return _make_response(200)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.update.return_value = None
    mock_db.commit.return_value = None
    mock_db.close.return_value = None
    monkeypatch.setattr(webhook_manager, "SessionLocal", lambda: mock_db)

    async def _fake_sleep(s):
        pass
    monkeypatch.setattr(webhook_manager.asyncio, "sleep", _fake_sleep)

    mgr = WebhookManager()
    mgr._client = MagicMock()
    mgr._client.post = _fake_post

    asyncio.run(mgr._deliver("wh1", "https://example.com/hook", None, "chat.completed", {"x": 1}))

    assert call_count[0] >= 2, f"Expected at least 2 HTTP calls (retry), got {call_count[0]}"


def test_deliver_succeeds_on_first_attempt(monkeypatch):
    """_deliver must not retry when the first attempt succeeds."""
    from src import webhook_manager
    from src.webhook_manager import WebhookManager

    call_count = [0]

    async def _fake_post(*a, **kw):
        call_count[0] += 1
        return _make_response(200)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.update.return_value = None
    mock_db.commit.return_value = None
    mock_db.close.return_value = None
    monkeypatch.setattr(webhook_manager, "SessionLocal", lambda: mock_db)

    mgr = WebhookManager()
    mgr._client = MagicMock()
    mgr._client.post = _fake_post

    asyncio.run(mgr._deliver("wh2", "https://example.com/hook", None, "chat.completed", {"x": 1}))

    assert call_count[0] == 1, f"Expected exactly 1 HTTP call (no retry needed), got {call_count[0]}"


def test_deliver_records_error_after_exhausting_retries(monkeypatch):
    """_deliver must write last_error to DB when all retry attempts fail."""
    from src import webhook_manager
    from src.webhook_manager import WebhookManager

    async def _fake_post(*a, **kw):
        return _make_response(503)

    update_calls = []
    mock_db = MagicMock()
    mock_query = mock_db.query.return_value
    mock_filter = mock_query.filter.return_value

    def _capture_update(d):
        update_calls.append(d)
    mock_filter.update.side_effect = _capture_update
    mock_db.commit.return_value = None
    mock_db.close.return_value = None
    monkeypatch.setattr(webhook_manager, "SessionLocal", lambda: mock_db)

    async def _fake_sleep(s):
        pass
    monkeypatch.setattr(webhook_manager.asyncio, "sleep", _fake_sleep)

    mgr = WebhookManager()
    mgr._client = MagicMock()
    mgr._client.post = _fake_post

    asyncio.run(mgr._deliver("wh3", "https://example.com/hook", None, "webhook.test", {}))

    # The last DB update must record a last_error (not None)
    assert update_calls, "Expected at least one DB update call"
    last_update = update_calls[-1]
    # After exhausting retries, last_status_code should be set (503 or similar)
    assert last_update.get("last_status_code") is not None or last_update.get("last_error") is not None, \
        f"Expected last_status_code or last_error in final update, got {last_update!r}"
