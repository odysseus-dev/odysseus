"""Calendar reminders belong to one event, not to every event with its title (#6587).

Reminders are stored as Notes rows. They used to be found by their generated
title ("Calendar reminder: <summary>") alone, so:

1. deleting one event deleted the reminders of every other event with the
   same summary, even on other days; and
2. renaming an event and clearing its reminder in the same update searched
   under the *new* title and left the original reminder note behind.

Reminders now carry ``Note.calendar_event_uid``. Rows created before that
column existed are matched conservatively (same title AND a due date inside
that event's reminder window), and the update paths look up the old reminder
before the summary changes.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb  # noqa: E402
import routes.calendar_routes as calendar_routes  # noqa: E402
from core.database import Note  # noqa: E402
from routes.calendar_routes import EventCreate, EventUpdate  # noqa: E402


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'calendar.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
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


def _reminders(factory):
    db = factory()
    try:
        return [
            (note.title, getattr(note, "calendar_event_uid", None), note.due_date)
            for note in db.query(Note).filter(Note.label == "calendar").order_by(Note.due_date)
        ]
    finally:
        db.close()


async def _create(summary, dtstart, reminder_minutes=None):
    create_event = _endpoint("POST", "/events")
    result = await create_event(
        _request(),
        EventCreate(summary=summary, dtstart=dtstart, reminder_minutes=reminder_minutes),
    )
    assert result["ok"], result
    return result["uid"]


async def test_new_reminders_are_linked_to_their_event(session_factory):
    uid = await _create("Team meeting", "2030-01-07T10:00:00Z", reminder_minutes=15)

    reminders = _reminders(session_factory)
    assert len(reminders) == 1
    assert reminders[0][1] == uid


async def test_deleting_event_keeps_same_title_reminder_of_other_event(session_factory):
    first = await _create("Team meeting", "2030-01-07T10:00:00Z", reminder_minutes=15)
    second = await _create("Team meeting", "2030-01-14T10:00:00Z", reminder_minutes=15)
    assert len(_reminders(session_factory)) == 2

    delete_event = _endpoint("DELETE", "/events/{uid}")
    await delete_event(_request(), first)

    reminders = _reminders(session_factory)
    assert len(reminders) == 1, "the second event's reminder must survive"
    assert reminders[0][1] == second


async def test_rename_and_disable_reminder_removes_original_note(session_factory):
    uid = await _create("Team meeting", "2030-01-07T10:00:00Z", reminder_minutes=15)

    update_event = _endpoint("PUT", "/events/{uid}")
    result = await update_event(
        _request(),
        uid,
        EventUpdate(summary="Renamed meeting", reminder_minutes=None),
    )

    assert result["ok"], result
    assert result["event"]["summary"] == "Renamed meeting"
    assert _reminders(session_factory) == [], "the original reminder note must be removed"


async def test_rename_and_change_reminder_replaces_original_note(session_factory):
    uid = await _create("Team meeting", "2030-01-07T10:00:00Z", reminder_minutes=15)

    update_event = _endpoint("PUT", "/events/{uid}")
    await update_event(_request(), uid, EventUpdate(summary="Renamed meeting", reminder_minutes=30))

    reminders = _reminders(session_factory)
    assert len(reminders) == 1
    assert reminders[0][0] == "Calendar reminder: Renamed meeting"
    assert reminders[0][1] == uid


async def test_legacy_title_only_reminders_are_matched_inside_event_window(session_factory):
    """Rows without calendar_event_uid only match the event whose window they sit in."""
    first = await _create("Standup", "2030-02-03T10:00:00Z")
    await _create("Standup", "2030-02-10T10:00:00Z")

    db = session_factory()
    try:
        for due in ("2030-02-03T09:45:00Z", "2030-02-10T09:45:00Z"):
            db.add(Note(
                id=f"legacy-{due}",
                owner=calendar_routes._require_user(_request()),
                title="Calendar reminder: Standup",
                items=json.dumps([{"text": "Standup", "done": False}]),
                note_type="todo",
                label="calendar",
                source="calendar",
                due_date=due,
            ))
        db.commit()
    finally:
        db.close()

    delete_event = _endpoint("DELETE", "/events/{uid}")
    await delete_event(_request(), first)

    reminders = _reminders(session_factory)
    assert [r[2] for r in reminders] == ["2030-02-10T09:45:00Z"]


async def test_tool_delete_keeps_same_title_reminder_of_other_event(session_factory):
    from src.tools.calendar import do_manage_calendar

    owner = calendar_routes._require_user(_request())
    created = []
    for day in ("2030-03-04", "2030-03-11"):
        result = await do_manage_calendar(json.dumps({
            "action": "create_event",
            "summary": "Dentist",
            "dtstart": f"{day}T10:00:00Z",
            "reminder_minutes": 30,
        }), owner=owner)
        assert result["exit_code"] == 0, result
        created.append(result["uid"])

    deleted = await do_manage_calendar(
        json.dumps({"action": "delete_event", "uid": created[0]}), owner=owner,
    )
    assert deleted["exit_code"] == 0, deleted

    reminders = _reminders(session_factory)
    assert len(reminders) == 1
    assert reminders[0][1] == created[1]


async def test_custom_note_reminders_stay_with_their_event(session_factory, monkeypatch):
    """The calendar form posts custom exact-time reminders straight to /api/notes."""
    from routes.note import note_routes

    monkeypatch.setattr(note_routes, "SessionLocal", session_factory)
    create_note = next(
        route.endpoint
        for route in note_routes.setup_note_routes().routes
        if route.path == "/api/notes" and "POST" in route.methods
    )
    first = await _create("Standup", "2030-04-01T10:00:00Z")
    second = await _create("Standup", "2030-04-02T10:00:00Z")
    for uid, due in ((first, "2030-04-01T09:00:00Z"), (second, "2030-04-02T09:00:00Z")):
        create_note(_request(), note_routes.NoteCreate(
            title="Calendar reminder: Standup",
            note_type="todo",
            items=[{"text": "Standup", "done": False}],
            label="calendar",
            due_date=due,
            source="calendar",
            calendar_event_uid=uid,
        ))

    delete_event = _endpoint("DELETE", "/events/{uid}")
    await delete_event(_request(), second)

    assert [r[2] for r in _reminders(session_factory)] == ["2030-04-01T09:00:00Z"]


def test_calendar_form_links_custom_reminders_to_their_event():
    root = Path(__file__).resolve().parents[1]
    source = (root / "static/js/calendar.js").read_text(encoding="utf-8")
    assert "calendar_event_uid: ev.uid ? String(ev.uid).split('::')[0] : undefined" in source


def _legacy_note(factory, due, title="Calendar reminder: Standup"):
    """A reminder row from before calendar_event_uid existed."""
    db = factory()
    try:
        db.add(Note(
            id=f"legacy-{due}",
            owner=calendar_routes._require_user(_request()),
            title=title,
            items=json.dumps([{"text": "Standup", "done": False}]),
            note_type="todo",
            label="calendar",
            source="calendar",
            due_date=due,
        ))
        db.commit()
    finally:
        db.close()


async def test_linked_reminder_does_not_hide_the_events_legacy_reminder(session_factory):
    uid = await _create("Standup", "2030-05-06T10:00:00Z", reminder_minutes=30)
    _legacy_note(session_factory, "2030-05-06T09:45:00Z")

    await _endpoint("DELETE", "/events/{uid}")(_request(), uid)

    assert _reminders(session_factory) == []




async def test_tool_case_only_rename_keeps_the_reminder(session_factory):
    from src.tools.calendar import do_manage_calendar

    owner = calendar_routes._require_user(_request())
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Team meeting",
        "dtstart": "2030-03-04T10:00:00Z",
        "reminder_minutes": 30,
    }), owner=owner)
    assert created["exit_code"] == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "summary": "TEAM MEETING",
        "reminder_minutes": 30,
    }), owner=owner)

    assert updated["exit_code"] == 0, updated
    assert len(_reminders(session_factory)) == 1


async def test_unrelated_event_leaves_a_legacy_reminder_outside_its_window(session_factory):
    """A row we can't tie to this event stays: an orphan beats deleting someone's reminder."""
    uid = await _create("Standup", "2030-03-01T10:00:00Z")
    await _create("Standup", "2030-09-01T10:00:00Z")
    _legacy_note(session_factory, "2030-07-01T09:45:00Z")

    await _endpoint("DELETE", "/events/{uid}")(_request(), uid)

    assert [r[2] for r in _reminders(session_factory)] == ["2030-07-01T09:45:00Z"]
