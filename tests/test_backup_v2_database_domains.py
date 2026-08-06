from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import (
    Base,
    CalendarCal,
    CalendarEvent,
    Note,
    ScheduledTask,
    Session,
    TaskRun,
)
from routes.backup_routes import BACKUP_VERSION, _restore_database_domains, _serialize_row


_SCOPE_PARAGRAPH = (
    "The Settings JSON export is a separate, mixed-scope portability format. Its version 2 shape includes memories, "
    "skills, presets, settings, preferences, calendars and events, scheduled tasks and run history, and notes. "
    "Memories, skills, preferences, calendars, tasks, and notes are selected for the current owner during export. "
    "Imported version 2 calendar, task, and note rows are stamped with the current owner, and primary-key collisions "
    "are remapped rather than overwriting another user's rows. Presets are shared across users, while settings and "
    "feature flags are instance-global; those sections remain shared/global rather than becoming owner-scoped. Task "
    "webhook tokens are intentionally excluded and must be regenerated after restore. References to chat/agent "
    "sessions, crew members, characters, and note upload images are detached because those domains are not part of "
    "the JSON format. Imported CalDAV calendars/events become local rows; reconnect and sync the account explicitly "
    "instead of reusing remote hrefs or pending-write markers from another instance."
)
_VERSION_1_PARAGRAPH = (
    "Version 1 JSON exports remain importable; they simply do not contain the newer calendar/task/note sections. "
    "The commands below describe full on-disk instance snapshots, which serve a different disaster-recovery use case."
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _assert_backup_docs_paragraph_layout(docs):
    lines = docs.splitlines()

    assert [line for line in lines if line.startswith("The Settings JSON export")] == [_SCOPE_PARAGRAPH]
    assert [line for line in lines if line.startswith("Version 1 JSON exports")] == [_VERSION_1_PARAGRAPH]


def test_backup_docs_describe_mixed_scope_contract():
    docs = Path("docs/backup-restore.md").read_text(encoding="utf-8")

    _assert_backup_docs_paragraph_layout(docs)


@pytest.mark.parametrize(
    ("paragraph", "late_sentence"),
    (
        (_SCOPE_PARAGRAPH, "Imported CalDAV calendars/events become local rows"),
        (_VERSION_1_PARAGRAPH, "The commands below describe full on-disk instance snapshots"),
    ),
)
def test_backup_docs_paragraph_layout_rejects_inserted_newline(paragraph, late_sentence):
    docs = Path("docs/backup-restore.md").read_text(encoding="utf-8")
    wrapped_docs = docs.replace(paragraph, paragraph.replace(late_sentence, f"\n{late_sentence}", 1), 1)

    with pytest.raises(AssertionError):
        _assert_backup_docs_paragraph_layout(wrapped_docs)


def test_v2_restore_remaps_cross_owner_ids_and_preserves_relationships(monkeypatch):
    db = _session()
    db.add(CalendarCal(id="calendar-1", owner="bob", name="Bob"))
    db.add(Note(id="note-1", owner="bob", title="Bob"))
    db.add(Session(
        id="bob-session",
        owner="bob",
        name="Bob chat",
        endpoint_url="http://localhost:11434/v1/chat/completions",
        model="bob-model",
    ))
    db.commit()
    generated_ids = iter(("alice-calendar", "alice-note"))
    monkeypatch.setattr(
        "routes.backup_routes._new_id",
        lambda: next(generated_ids),
    )

    body = {
        "version": BACKUP_VERSION,
        "calendar": {
            "calendars": [{
                "id": "calendar-1",
                "name": "Alice",
                "source": "caldav",
                "account_id": "missing-account",
                "caldav_base_url": "https://calendar.example/alice/",
            }],
            "events": [{
                "uid": "event-1",
                "calendar_id": "calendar-1",
                "summary": "Standup",
                "dtstart": "2026-08-06T08:00:00",
                "dtend": "2026-08-06T08:30:00",
                "origin": "caldav",
                "remote_href": "https://calendar.example/alice/event-1.ics",
                "caldav_sync_pending": "update",
            }],
        },
        "tasks": {
            "scheduled": [
                {
                    "id": "task-1",
                    "name": "First",
                    "then_task_id": "task-2",
                    "session_id": "bob-session",
                    "crew_member_id": "bob-crew",
                    "character_id": "bob-character",
                },
                {"id": "task-2", "name": "Second"},
            ],
            "runs": [{
                "id": "run-1",
                "task_id": "task-1",
                "started_at": "2026-08-06T09:00:00",
                "status": "success",
            }],
        },
        "notes": [{
            "id": "note-1",
            "title": "Alice",
            "content": "restored",
            "session_id": "bob-session",
            "agent_session_id": "bob-session",
            "image_url": "/api/uploads/bob-image",
        }],
    }

    restored = _restore_database_domains(db, body, "alice")
    db.commit()

    assert restored == ["1 calendars, 1 events", "2 tasks, 1 task runs", "1 notes"]
    alice_calendar = db.query(CalendarCal).filter(CalendarCal.owner == "alice").one()
    assert alice_calendar.id != "calendar-1"
    assert alice_calendar.source == "local"
    assert alice_calendar.account_id is None
    assert alice_calendar.caldav_base_url is None
    event = db.query(CalendarEvent).filter(CalendarEvent.uid == "event-1").one()
    assert event.calendar_id == alice_calendar.id
    assert event.origin is None
    assert event.remote_href is None
    assert event.caldav_sync_pending is None
    first = db.query(ScheduledTask).filter(ScheduledTask.id == "task-1").one()
    assert first.then_task_id == "task-2"
    assert first.webhook_token is None
    assert first.session_id is None
    assert first.crew_member_id is None
    assert first.character_id is None
    assert db.query(TaskRun).filter(TaskRun.id == "run-1").one().task_id == "task-1"
    alice_note = db.query(Note).filter(Note.owner == "alice").one()
    assert alice_note.id != "note-1"
    assert alice_note.session_id is None
    assert alice_note.agent_session_id is None
    assert alice_note.image_url is None
    assert db.query(Note).filter(Note.owner == "bob", Note.id == "note-1").one().title == "Bob"


def test_v2_serialization_emits_iso_datetimes():
    event = CalendarEvent(
        uid="event-1",
        calendar_id="calendar-1",
        summary="Standup",
        dtstart=datetime(2026, 8, 6, 8, 0),
        dtend=datetime(2026, 8, 6, 8, 30),
    )

    payload = _serialize_row(event, ("uid", "dtstart", "dtend"))

    assert payload == {
        "uid": "event-1",
        "dtstart": "2026-08-06T08:00:00",
        "dtend": "2026-08-06T08:30:00",
    }


def test_v2_restore_clears_existing_same_owner_task_webhook_token():
    db = _session()
    db.add(ScheduledTask(
        id="task-1",
        owner="alice",
        name="Existing webhook task",
        trigger_type="webhook",
        webhook_token="live-secret-path-token",
    ))
    db.commit()

    _restore_database_domains(db, {
        "version": BACKUP_VERSION,
        "tasks": {
            "scheduled": [{
                "id": "task-1",
                "name": "Restored webhook task",
                "trigger_type": "webhook",
            }],
            "runs": [],
        },
    }, "alice")
    db.commit()

    task = db.query(ScheduledTask).filter(ScheduledTask.id == "task-1").one()
    assert task.name == "Restored webhook task"
    assert task.webhook_token is None


def test_v2_restore_marks_offset_datetimes_as_normalized_utc():
    db = _session()

    _restore_database_domains(db, {
        "version": BACKUP_VERSION,
        "calendar": {
            "calendars": [{"id": "calendar-1", "name": "Alice"}],
            "events": [{
                "uid": "event-1",
                "calendar_id": "calendar-1",
                "summary": "Offset meeting",
                "dtstart": "2026-08-06T10:00:00+02:00",
                "dtend": "2026-08-06T10:30:00+02:00",
                "is_utc": False,
            }],
        },
    }, "alice")
    db.commit()

    event = db.query(CalendarEvent).filter(CalendarEvent.uid == "event-1").one()
    assert event.dtstart == datetime(2026, 8, 6, 8, 0)
    assert event.dtend == datetime(2026, 8, 6, 8, 30)
    assert event.is_utc is True
