from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.session_manager import SessionManager
import core.session_manager as SM


def _manager_with(sessions=None):
    manager = SessionManager.__new__(SessionManager)
    manager.sessions = dict(sessions or {})
    return manager


def test_cleanup_empty_sessions_archives_old_naive_last_accessed(monkeypatch):
    old_session = SimpleNamespace(
        id="old-chat",
        archived=False,
        last_accessed=datetime(2026, 5, 1, 12, 0, 0),
        message_count=3,
        is_important=False,
    )
    db = MagicMock()
    db.query.return_value.all.return_value = [old_session]

    monkeypatch.setattr(SM, "SessionLocal", lambda: db)
    monkeypatch.setattr(SM, "utcnow_naive", lambda: datetime(2026, 6, 4, 12, 0, 0))

    stats = _manager_with().cleanup_empty_sessions(auto_archive_days=30)

    assert old_session.archived is True
    assert stats == {"deleted_empty": 0, "archived_old": 1, "total_checked": 1}
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
