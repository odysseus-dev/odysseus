"""Regression: list_sessions relative timestamps must not call datetime.utcnow() (#1116)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from core.database import utcnow_naive
from src.agent_tools import session_tools as st


class _FakeSession:
    def __init__(self, name="Test chat"):
        self.name = name
        self.model = "gpt-4"
        self.endpoint_url = "http://localhost"
        self.message_count = 0


@pytest.mark.asyncio
async def test_list_sessions_relative_time_uses_naive_utc(monkeypatch):
    sid = "abc12345"
    recent = utcnow_naive() - timedelta(minutes=3)

    fake_row = MagicMock(
        last_accessed=recent,
        updated_at=recent,
        created_at=recent,
    )

    db = MagicMock()
    db.query.return_value.all.return_value = [
        MagicMock(id=sid, **{"last_accessed": recent, "updated_at": recent, "created_at": recent}),
    ]
    # Match attribute access on the row object used in list_sessions.
    db_row = db.query.return_value.all.return_value[0]
    db_row.last_accessed = recent
    db_row.updated_at = recent
    db_row.created_at = recent

    sm = MagicMock()
    sm.get_sessions_for_user.return_value = {sid: _FakeSession()}

    monkeypatch.setattr(st, "get_session_manager", lambda: sm)

    class _FakeSessionLocal:
        def __call__(self):
            return self

        def query(self, *_args, **_kwargs):
            return self

        def all(self):
            return [db_row]

        def close(self):
            pass

    monkeypatch.setattr("core.database.SessionLocal", _FakeSessionLocal())

    result = await st.list_sessions("", owner="alice")
    assert "error" not in result
    body = result["results"]
    assert "3m ago" in body or "just now" in body


def test_list_sessions_does_not_reference_utcnow():
    import inspect

    source = inspect.getsource(st.list_sessions)
    assert "datetime.utcnow()" not in source