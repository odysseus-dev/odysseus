"""Default calendar creation belongs to the caller's transaction.

Before this regression, ``_ensure_default_calendar`` committed independently.
If event persistence then failed, the event rolled back but a new ``Personal``
calendar remained (``calendar_count=1``, ``event_count=0``).
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb  # noqa: E402
import routes.calendar_routes as calendar_routes  # noqa: E402
from core.database import CalendarCal, CalendarEvent  # noqa: E402
from routes.calendar_routes import EventCreate  # noqa: E402


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
