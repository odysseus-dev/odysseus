"""
Tests for do_list_sessions() and its extracted helpers.

Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from typing import Dict


# ---------------------------------------------------------------------------
# Relative-time helper
# ---------------------------------------------------------------------------

class TestSessionRelativeTime:
    """Tests for the _session_relative_time() helper."""

    def test_none_returns_never(self) -> None:
        from src.ai_interaction import _session_relative_time
        assert _session_relative_time(None) == "never"

    def test_just_now(self) -> None:
        from src.ai_interaction import _session_relative_time
        ts = datetime.now(timezone.utc) - timedelta(seconds=30)
        assert _session_relative_time(ts) == "just now"

    def test_minutes_ago(self) -> None:
        from src.ai_interaction import _session_relative_time
        ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = _session_relative_time(ts)
        assert "m ago" in result
        assert "5" in result

    def test_hours_ago(self) -> None:
        from src.ai_interaction import _session_relative_time
        ts = datetime.now(timezone.utc) - timedelta(hours=3)
        result = _session_relative_time(ts)
        assert "h ago" in result
        assert "3" in result

    def test_days_ago(self) -> None:
        from src.ai_interaction import _session_relative_time
        ts = datetime.now(timezone.utc) - timedelta(days=2)
        result = _session_relative_time(ts)
        assert "d ago" in result
        assert "2" in result

    def test_old_date_returns_formatted(self) -> None:
        from src.ai_interaction import _session_relative_time
        ts = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = _session_relative_time(ts)
        assert "2024-01-15" in result

    def test_naive_datetime_works(self) -> None:
        from src.ai_interaction import _session_relative_time
        # Naive datetime (no tzinfo) — treated as UTC internally
        ts = datetime(2024, 1, 1, 0, 0, 0)  # old fixed date, no tzinfo
        result = _session_relative_time(ts)
        # Should return a date string (more than 7 days ago)
        assert "2024-01-01" in result


# ---------------------------------------------------------------------------
# do_list_sessions
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_session_manager(monkeypatch):
    mgr = MagicMock()
    mgr.get_sessions_for_user.return_value = {}
    monkeypatch.setattr("src.ai_interaction._session_manager", mgr)
    return mgr


async def _list(content: str = "", owner: str = "user1") -> Dict:
    from src.ai_interaction import do_list_sessions
    return await do_list_sessions(content, owner=owner)


class TestListSessions:

    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self, monkeypatch) -> None:
        monkeypatch.setattr("src.ai_interaction._session_manager", None)
        result = await _list()
        assert "error" in result

    def _make_ctx(self, db_rows=None):
        """Build a get_db_session context manager mock returning the given rows."""
        from contextlib import contextmanager
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = db_rows or []

        @contextmanager
        def _ctx():
            yield mock_db

        return _ctx

    @pytest.mark.asyncio
    async def test_no_sessions_returns_results(self, mock_session_manager) -> None:
        mock_session_manager.get_sessions_for_user.return_value = {}
        with patch("src.ai_interaction.get_db_session", self._make_ctx()):
            result = await _list()
        assert "results" in result
        assert "No sessions" in result["results"]

    @pytest.mark.asyncio
    async def test_sessions_sorted_most_recent_first(self, mock_session_manager) -> None:
        older_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        newer_ts = datetime.now(timezone.utc) - timedelta(minutes=5)

        sess_old = MagicMock()
        sess_old.name = "Old Chat"
        sess_old.model = "gpt-4"
        sess_old.message_count = 5

        sess_new = MagicMock()
        sess_new.name = "New Chat"
        sess_new.model = "gpt-4"
        sess_new.message_count = 3

        mock_session_manager.get_sessions_for_user.return_value = {
            "old_id": sess_old,
            "new_id": sess_new,
        }

        db_old = MagicMock(id="old_id", last_accessed=older_ts, updated_at=None, created_at=None)
        db_new = MagicMock(id="new_id", last_accessed=newer_ts, updated_at=None, created_at=None)

        with patch("src.ai_interaction.get_db_session", self._make_ctx([db_old, db_new])):
            result = await _list()

        assert "results" in result
        new_pos = result["results"].find("New Chat")
        old_pos = result["results"].find("Old Chat")
        assert new_pos < old_pos, "Most-recent session should appear first"
        assert "← most recent" in result["results"]

    @pytest.mark.asyncio
    async def test_keyword_filter_applied(self, mock_session_manager) -> None:
        sess_python = MagicMock()
        sess_python.name = "Python Help"
        sess_python.model = "gpt-4"
        sess_python.message_count = 2

        sess_js = MagicMock()
        sess_js.name = "JavaScript Tips"
        sess_js.model = "gpt-4"
        sess_js.message_count = 1

        mock_session_manager.get_sessions_for_user.return_value = {
            "s1": sess_python,
            "s2": sess_js,
        }

        with patch("src.ai_interaction.get_db_session", self._make_ctx()):
            result = await _list("python")

        assert "Python Help" in result["results"]
        assert "JavaScript Tips" not in result["results"]

    @pytest.mark.asyncio
    async def test_result_contains_clickable_links(self, mock_session_manager) -> None:
        sess = MagicMock()
        sess.name = "My Chat"
        sess.model = "gpt-4"
        sess.message_count = 10

        mock_session_manager.get_sessions_for_user.return_value = {"abc123": sess}

        with patch("src.ai_interaction.get_db_session", self._make_ctx()):
            result = await _list()

        assert "#session-abc123" in result["results"]
