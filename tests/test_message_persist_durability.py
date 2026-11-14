"""A failed database write must be visible, retried and never duplicated or reordered."""
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(monkeypatch):
    data = tempfile.mkdtemp(prefix='ody-durability-')
    monkeypatch.setenv('ODYSSEUS_DATA_DIR', data)
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    from core.database import init_db
    init_db()
    import core.models as models
    import core.session_manager as sm_mod
    from core.session_manager import SessionManager
    manager = SessionManager()
    models.set_session_manager_instance(manager)
    manager._PERSIST_RETRY_DELAYS = (0, 0)
    session = manager.create_session('dur-' + uuid.uuid4().hex[:8], 'dur', 'http://127.0.0.1:1/v1', 'm', owner='t')
    yield manager, session, sm_mod
    models.set_session_manager_instance(None)


def rows(session_id):
    from core.database import SessionLocal, ChatMessage as Row
    db = SessionLocal()
    try:
        return [(r.content, r.timestamp) for r in db.query(Row).filter(Row.session_id == session_id).order_by(Row.timestamp).all()]
    finally:
        db.close()


def failing(sm_mod, times, error):
    real = sm_mod.SessionLocal
    state = {'left': times}

    def factory():
        db = real()
        if state['left'] > 0:
            def bad_commit():
                state['left'] -= 1
                raise error
            db.commit = bad_commit
        return db
    return real, factory


def test_a_lock_that_clears_within_the_retries_saves_the_message_once(env):
    from core.models import ChatMessage
    from sqlalchemy.exc import OperationalError
    manager, session, sm_mod = env
    real, factory = failing(sm_mod, 2, OperationalError('INSERT', {}, Exception('database is locked')))
    sm_mod.SessionLocal = factory
    try:
        assert session.add_message(ChatMessage('user', 'hello')) is True
    finally:
        sm_mod.SessionLocal = real
    assert [r[0] for r in rows(session.id)] == ['hello'] and manager.unsaved_messages(session.id) == 0


def test_a_lasting_failure_is_reported_flagged_and_kept_then_saved_in_order_with_the_next_save(env):
    from core.models import ChatMessage
    from sqlalchemy.exc import OperationalError
    manager, session, sm_mod = env
    real, factory = failing(sm_mod, 99, OperationalError('INSERT', {}, Exception('database is locked')))
    sm_mod.SessionLocal = factory
    lost = ChatMessage('assistant', 'reply that failed')
    try:
        assert session.add_message(lost) is False
    finally:
        sm_mod.SessionLocal = real
    assert lost.metadata.get('_persist_failed') is True and '_db_id' not in lost.metadata
    assert manager.unsaved_messages(session.id) == 1 and [m.content for m in session.history] == ['reply that failed']      # still in the live conversation
    assert rows(session.id) == []
    assert session.add_message(ChatMessage('user', 'next')) is True
    assert [r[0] for r in rows(session.id)] == ['reply that failed', 'next']                  # the failed one is saved first: original order kept
    assert manager.unsaved_messages(session.id) == 0 and '_persist_failed' not in lost.metadata and lost.metadata['_db_id']


def test_retrying_never_duplicates_a_committed_message(env):
    from core.models import ChatMessage
    manager, session, sm_mod = env
    message = ChatMessage('user', 'once')
    session.add_message(message)
    assert manager._persist_message(session.id, message) is True and manager._persist_message(session.id, message) is True
    assert [r[0] for r in rows(session.id)] == ['once']


def test_a_non_transient_error_is_not_retried_and_the_queue_is_bounded(env):
    from core.models import ChatMessage
    manager, session, sm_mod = env
    calls = {'n': 0}
    real = sm_mod.SessionLocal

    def factory():
        calls['n'] += 1
        raise RuntimeError('disk full')
    sm_mod.SessionLocal = factory
    try:
        assert session.add_message(ChatMessage('user', 'x')) is False
        assert calls['n'] == 1                                  # no pointless retries for a permanent error
        manager._UNSAVED_MAX = 3
        for i in range(6):
            session.add_message(ChatMessage('user', 'm%d' % i))
    finally:
        sm_mod.SessionLocal = real
    assert manager.unsaved_messages(session.id) == 3


def test_the_stream_tells_the_client_when_a_save_failed():
    from routes.chat_helpers import persist_failure_event

    class Manager:
        def unsaved_messages(self, sid):
            return 2 if sid == 'bad' else 0
    assert persist_failure_event(Manager(), 'bad') == {'type': 'message_save_failed', 'unsaved': 2}
    assert persist_failure_event(Manager(), 'ok') is None
    assert persist_failure_event(object(), 'x') is None
