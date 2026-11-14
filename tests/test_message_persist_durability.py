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


def _fail_all(sm_mod):
    real = sm_mod.SessionLocal

    def factory():
        raise RuntimeError('disk full')
    sm_mod.SessionLocal = factory
    return real


def _spool_lines(manager, session_id):
    path = manager._spool_path(session_id)
    return [line for line in open(path, encoding='utf-8').read().splitlines()] if os.path.exists(path) else []


def test_a_failed_save_is_spooled_to_disk_and_removed_once_it_is_saved(env):
    from core.models import ChatMessage
    manager, session, sm_mod = env
    real = _fail_all(sm_mod)
    try:
        assert session.add_message(ChatMessage('assistant', 'reply that failed')) is False
    finally:
        sm_mod.SessionLocal = real
    assert len(_spool_lines(manager, session.id)) == 1
    assert session.add_message(ChatMessage('user', 'next')) is True
    assert [r[0] for r in rows(session.id)] == ['reply that failed', 'next'] and _spool_lines(manager, session.id) == []


def test_after_a_process_restart_spooled_messages_are_recovered_in_order_exactly_once(env):
    from core.models import ChatMessage
    import core.models as models
    from core.session_manager import SessionManager
    manager, session, sm_mod = env
    real = _fail_all(sm_mod)
    try:
        for text in ('first', 'second', 'third'):
            session.add_message(ChatMessage('user', text))
    finally:
        sm_mod.SessionLocal = real
    assert rows(session.id) == []
    restarted = SessionManager()                                        # a new process: nothing in memory, only the spool on disk
    models.set_session_manager_instance(restarted)
    assert [r[0] for r in rows(session.id)] == ['first', 'second', 'third'] and _spool_lines(manager, session.id) == []
    assert restarted.recover_spooled() == {'recovered': 0, 'already_saved': 0, 'failed': 0}
    assert [r[0] for r in rows(session.id)] == ['first', 'second', 'third']          # running recovery again never duplicates


def test_a_message_saved_but_not_yet_removed_from_the_spool_is_not_saved_twice(env):
    from core.models import ChatMessage
    manager, session, sm_mod = env
    real = _fail_all(sm_mod)
    try:
        session.add_message(ChatMessage('user', 'crash window'))
    finally:
        sm_mod.SessionLocal = real
    line = _spool_lines(manager, session.id)[0]
    assert session.add_message(ChatMessage('user', 'later')) is True                  # saves it and removes it from the spool
    open(manager._spool_path(session.id), 'w', encoding='utf-8').write(line + '\n')    # as if the process died between the commit and the spool update
    assert manager.recover_spooled() == {'recovered': 0, 'already_saved': 1, 'failed': 0}
    assert [r[0] for r in rows(session.id)] == ['crash window', 'later'] and _spool_lines(manager, session.id) == []


def test_queue_overflow_loses_nothing_because_every_message_is_on_the_spool(env):
    from core.models import ChatMessage
    from core.session_manager import SessionManager
    import core.models as models
    manager, session, sm_mod = env
    manager._UNSAVED_MAX = 2
    real = _fail_all(sm_mod)
    try:
        for i in range(5):
            session.add_message(ChatMessage('user', 'm%d' % i))
    finally:
        sm_mod.SessionLocal = real
    assert manager.unsaved_messages(session.id) == 2 and len(_spool_lines(manager, session.id)) == 5
    models.set_session_manager_instance(SessionManager())
    assert [r[0] for r in rows(session.id)] == ['m0', 'm1', 'm2', 'm3', 'm4']


def test_a_full_spool_is_reported_not_silent_and_never_blocks_the_conversation(env):
    from core.models import ChatMessage
    manager, session, sm_mod = env
    manager._SPOOL_MAX_PER_SESSION = 1
    real = _fail_all(sm_mod)
    try:
        session.add_message(ChatMessage('user', 'a'))
        late = ChatMessage('user', 'b')
        assert session.add_message(late) is False
    finally:
        sm_mod.SessionLocal = real
    assert len(_spool_lines(manager, session.id)) == 1 and late.metadata.get('_spool_failed') is True and late.metadata.get('_persist_failed') is True
    assert [m.content for m in session.history] == ['a', 'b']


def test_a_busy_database_never_sleeps_on_the_event_loop(env, monkeypatch):
    import asyncio
    from core.models import ChatMessage
    from sqlalchemy.exc import OperationalError
    manager, session, sm_mod = env
    manager._PERSIST_RETRY_DELAYS = (0.05, 0.15)
    slept = []
    monkeypatch.setattr(sm_mod.time, 'sleep', lambda s: slept.append(s))
    real, factory = failing(sm_mod, 99, OperationalError('INSERT', {}, Exception('database is locked')))
    sm_mod.SessionLocal = factory

    async def inside():
        return session.add_message(ChatMessage('user', 'on the loop'))
    try:
        assert asyncio.run(inside()) is False and slept == []
        assert session.add_message(ChatMessage('user', 'on a worker thread')) is False and slept
    finally:
        sm_mod.SessionLocal = real
