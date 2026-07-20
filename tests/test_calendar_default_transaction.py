"""Default calendar creation belongs to the caller's transaction.

Before this regression, ``_ensure_default_calendar`` committed independently.
If event persistence then failed, the event rolled back but a new ``Personal``
calendar remained (``calendar_count=1``, ``event_count=0``).
"""

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb  # noqa: E402
import routes.calendar_routes as calendar_routes  # noqa: E402
from core.database import CalendarCal, CalendarEvent  # noqa: E402
from routes.calendar_routes import EventCreate  # noqa: E402
from routes.calendar_routes import (  # noqa: E402
    _default_calendar_id,
    _ensure_default_calendar,
)


class _RejectEventCommit(Session):
    """Reproduce an event commit failure after default-calendar creation."""

    def commit(self):
        if any(isinstance(row, CalendarEvent) for row in self.new):
            raise RuntimeError("commit guard rejected event commit")
        return super().commit()


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'calendar.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        class_=_RejectEventCommit,
    )
    monkeypatch.setattr(cdb, "SessionLocal", factory)
    monkeypatch.setattr(calendar_routes, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def _request():
    return SimpleNamespace(state=SimpleNamespace(current_user="alice"))


def _endpoint(method, suffix):
    router = calendar_routes.setup_calendar_routes()
    for route in router.routes:
        if route.path.endswith(suffix) and method in route.methods:
            return route.endpoint
    raise RuntimeError(f"{method} *{suffix} not found")


def _counts(factory):
    db = factory()
    try:
        return db.query(CalendarCal).count(), db.query(CalendarEvent).count()
    finally:
        db.close()


async def test_route_event_failure_rolls_back_new_default_calendar(session_factory):
    create_event = _endpoint("POST", "/events")

    with pytest.raises(HTTPException) as caught:
        await create_event(
            _request(),
            EventCreate(summary="Planning", dtstart="2126-07-20T09:00:00Z"),
        )

    assert caught.value.status_code == 500
    assert _counts(session_factory) == (0, 0)


async def test_route_event_validation_failure_rolls_back_new_default_calendar(
    session_factory,
):
    create_event = _endpoint("POST", "/events")

    with pytest.raises(HTTPException) as caught:
        await create_event(
            _request(),
            EventCreate(summary="Planning", dtstart="not-a-datetime"),
        )

    assert caught.value.status_code == 500
    assert _counts(session_factory) == (0, 0)


async def test_tool_event_failure_rolls_back_new_default_calendar(session_factory):
    from src.tools.calendar import do_manage_calendar

    result = await do_manage_calendar(
        json.dumps({
            "action": "create_event",
            "summary": "Planning",
            "dtstart": "2126-07-20T09:00:00Z",
        }),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert "commit guard rejected event commit" in result["error"]
    assert _counts(session_factory) == (0, 0)


async def test_tool_event_validation_failure_rolls_back_new_default_calendar(
    session_factory,
):
    from src.tools.calendar import do_manage_calendar

    result = await do_manage_calendar(
        json.dumps({
            "action": "create_event",
            "summary": "Planning",
            "dtstart": "not-a-datetime",
        }),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert "Could not parse dtstart" in result["error"]
    assert _counts(session_factory) == (0, 0)


async def test_route_list_calendars_persists_lazy_default(session_factory):
    list_calendars = _endpoint("GET", "/calendars")

    result = await list_calendars(_request())

    assert [calendar["name"] for calendar in result["calendars"]] == ["Personal"]
    assert _counts(session_factory) == (1, 0)


async def test_tool_list_calendars_persists_lazy_default(session_factory):
    from src.tools.calendar import do_manage_calendar

    result = await do_manage_calendar(
        json.dumps({"action": "list_calendars"}),
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert [calendar["name"] for calendar in result["calendars"]] == ["Personal"]
    assert _counts(session_factory) == (1, 0)


def test_concurrent_first_use_creates_one_sqlite_default(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-calendar.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    first_staged = threading.Event()
    second_selected = threading.Event()
    errors = []

    @event.listens_for(engine, "after_cursor_execute")
    def observe_second_gap(conn, cursor, statement, parameters, context, executemany):
        if (
            threading.current_thread().name == "calendar-worker-second"
            and statement.lstrip().upper().startswith("SELECT")
            and "FROM calendars" in statement
        ):
            second_selected.set()

    def create_default(worker, hold=False):
        db = factory()
        try:
            if not hold:
                assert first_staged.wait(5)
            cal = _ensure_default_calendar(db, "alice")
            start = datetime(2126, 7, 20, 9 if hold else 10)
            db.add(CalendarEvent(
                uid=worker,
                calendar_id=cal.id,
                summary=f"Event {worker}",
                dtstart=start,
                dtend=start + timedelta(hours=1),
            ))
            if hold:
                first_staged.set()
                # The second session has observed the uncommitted gap before
                # this transaction releases its writer reservation.
                assert second_selected.wait(5)
            db.commit()
            assert cal.id == _default_calendar_id("alice")
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append((worker, exc))
            db.rollback()
        finally:
            db.close()

    first = threading.Thread(
        target=create_default,
        args=("first", True),
        name="calendar-worker-first",
    )
    second = threading.Thread(
        target=create_default,
        args=("second",),
        name="calendar-worker-second",
    )
    first.start()
    second.start()
    first.join(10)
    second.join(10)

    try:
        assert not first.is_alive() and not second.is_alive()
        assert errors == []
        db = factory()
        try:
            rows = db.query(CalendarCal).filter(CalendarCal.owner == "alice").all()
            assert [(row.id, row.name) for row in rows] == [
                (_default_calendar_id("alice"), "Personal")
            ]
            assert db.query(CalendarEvent).count() == 2
        finally:
            db.close()
    finally:
        engine.dispose()


def test_sqlite_default_stays_in_callers_transaction(session_factory):
    db = session_factory()
    try:
        cal = _ensure_default_calendar(db, "rollback-owner")
        assert cal.id == _default_calendar_id("rollback-owner")
        db.rollback()
    finally:
        db.close()

    verify = session_factory()
    try:
        assert (
            verify.query(CalendarCal)
            .filter(CalendarCal.owner == "rollback-owner")
            .count()
            == 0
        )
    finally:
        verify.close()


class _FakeDialect:
    name = "postgresql"


class _FakeBind:
    dialect = _FakeDialect()


class _FakeQuery:
    def __init__(self, session):
        self.session = session

    def filter(self, *conditions):
        return self

    def with_for_update(self):
        self.session.locking_read = True
        return self

    def first(self):
        self.session.query_count += 1
        if self.session.query_count == 1:
            return None
        return self.session.winner


class _GenericRaceSession:
    """Minimal non-SQLite session that loses the deterministic-ID race."""

    def __init__(self):
        self.query_count = 0
        self.nested_entries = 0
        self.locking_read = False
        self.candidate = None
        self.winner = CalendarCal(
            id=_default_calendar_id("alice"),
            owner="alice",
            name="Personal",
            source="local",
        )

    def get_bind(self):
        return _FakeBind()

    def query(self, model):
        assert model is CalendarCal
        return _FakeQuery(self)

    @contextmanager
    def begin_nested(self):
        self.nested_entries += 1
        yield

    def add(self, row):
        self.candidate = row

    def flush(self):
        raise IntegrityError("insert", {}, RuntimeError("duplicate primary key"))


def test_generic_backend_lost_race_recovers_inside_savepoint():
    db = _GenericRaceSession()

    winner = _ensure_default_calendar(db, "alice")

    assert winner is db.winner
    assert db.nested_entries == 1
    assert db.locking_read is True
    assert db.candidate.id == db.winner.id
