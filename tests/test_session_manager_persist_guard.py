from types import SimpleNamespace
from unittest.mock import MagicMock

from core.models import ChatMessage
from core.session_manager import SessionManager
import core.session_manager as SM


def _manager_with(sessions):
    manager = SessionManager.__new__(SessionManager)
    manager.sessions = dict(sessions)
    return manager


def _session_local(parent_row):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = parent_row
    return MagicMock(return_value=db), db


def test_persist_message_drops_write_when_parent_session_is_gone(monkeypatch):
    session_local, db = _session_local(None)
    monkeypatch.setattr(SM, "SessionLocal", session_local)

    manager = _manager_with({"deleted": SimpleNamespace(history=[])})
    message = ChatMessage("assistant", "late token")

    manager._persist_message("deleted", message)

    assert "deleted" not in manager.sessions
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_persist_message_still_writes_when_parent_session_exists(monkeypatch):
    parent = SimpleNamespace(message_count=0, last_accessed=None, last_message_at=None)
    session_local, db = _session_local(parent)
    monkeypatch.setattr(SM, "SessionLocal", session_local)

    message = ChatMessage("user", "hello")
    manager = _manager_with({"sid": SimpleNamespace(history=[message])})

    manager._persist_message("sid", message)

    db.add.assert_called_once()
    db.commit.assert_called_once()
    assert parent.message_count == 1
    assert parent.last_accessed is not None
    assert parent.last_message_at is not None
    assert message.metadata["_db_id"]
    assert message.metadata["timestamp"].endswith("Z")
