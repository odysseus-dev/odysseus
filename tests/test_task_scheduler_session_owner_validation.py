"""Regression tests for ScheduledTask session ownership validation."""

from types import SimpleNamespace
from core.database import SessionLocal, Session as DbSession
from src.task_scheduler import _validate_task_session_owner


def test_validate_task_session_owner_enforces_exact_owner_match():
    db = SessionLocal()
    try:
        alice_sess = DbSession(
            id="sess-alice-sched",
            owner="alice",
            name="Alice Chat",
            endpoint_url="http://localhost:11434/v1",
            model="test-model",
        )
        ownerless_sess = DbSession(
            id="sess-legacy-sched",
            owner=None,
            name="Legacy Chat",
            endpoint_url="http://localhost:11434/v1",
            model="test-model",
        )
        db.add_all([alice_sess, ownerless_sess])
        db.commit()

        # Task without session_id or targeting a not-yet-created session is allowed
        assert _validate_task_session_owner(SimpleNamespace(session_id=None, owner="alice"), db, DbSession) is True
        assert _validate_task_session_owner(SimpleNamespace(session_id="sess-new-sched", owner="alice"), db, DbSession) is True

        # Matching owner succeeds
        assert _validate_task_session_owner(SimpleNamespace(session_id="sess-alice-sched", owner="alice"), db, DbSession) is True
        assert _validate_task_session_owner(SimpleNamespace(session_id="sess-legacy-sched", owner=None), db, DbSession) is True

        # Cross-owner session reference fails closed
        assert _validate_task_session_owner(SimpleNamespace(session_id="sess-alice-sched", owner="bob"), db, DbSession) is False
        assert _validate_task_session_owner(SimpleNamespace(session_id="sess-legacy-sched", owner="alice"), db, DbSession) is False
        assert _validate_task_session_owner(SimpleNamespace(session_id="sess-alice-sched", owner=None), db, DbSession) is False
    finally:
        db.rollback()
        db.query(DbSession).filter(DbSession.id.in_(["sess-alice-sched", "sess-legacy-sched"])).delete(synchronize_session=False)
        db.commit()
        db.close()
