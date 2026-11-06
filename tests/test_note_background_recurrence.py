"""Recurring reminders advance through the real dispatcher and ORM."""
import datetime
import json

import pytest

from tests.test_note_ping_delivery_retry import reminder_scan  # noqa: F401


def configure(scan, **changes):
    from core.database import Note
    with scan.factory() as db:
        note = db.get(Note, "note-1")
        for key, value in {"repeat": "daily", "reminder_timezone": "UTC", **changes}.items():
            setattr(note, key, value)
        db.commit()


def read(scan):
    from core.database import Note
    with scan.factory() as db:
        note = db.get(Note, "note-1")
        return {key: getattr(note, key) for key in ("due_date", "repeat", "reminder_timezone", "archived")}


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat,expected", [
    ("daily", "2026-10-08T12:00"), ("weekly", "2026-10-14T12:00"),
    ("weekly:0", "2026-10-11T12:00"), ("monthly", "2026-11-07T12:00"),
    ("monthly:day:31", "2026-11-30T12:00"),
    ("monthly:nth:1:3", "2026-11-04T12:00"), ("monthly:last:3", "2026-11-25T12:00"),
    ("yearly", "2027-10-07T12:00"),
])
async def test_successful_delivery_advances_every_rule(reminder_scan, repeat, expected):
    scan = reminder_scan
    configure(scan, repeat=repeat)
    scan.statuses[:] = [200]
    await scan.scan()
    assert read(scan)["due_date"] == expected
    receipt = json.loads(scan.path.read_text())["note-1"]
    assert receipt["channel"] == "webhook"
    assert receipt["due_date"] == scan.instant.isoformat()


@pytest.mark.asyncio
async def test_second_day_delivers_without_a_browser(reminder_scan):
    scan = reminder_scan
    configure(scan)
    scan.statuses[:] = [200, 200]
    await scan.scan()
    scan.set_time(scan.instant + datetime.timedelta(days=1))
    await scan.scan()
    assert len(scan.calls) == 2
    assert read(scan)["due_date"] == "2026-10-09T12:00"


@pytest.mark.asyncio
async def test_failed_primary_does_not_advance_and_can_retry(reminder_scan):
    from src.builtin_actions import TaskNoop
    scan = reminder_scan
    configure(scan)
    with pytest.raises(TaskNoop):
        await scan.scan()
    assert read(scan)["due_date"] == scan.instant.isoformat()
    await scan.scan()
    assert read(scan)["due_date"] == "2026-10-08T12:00"
    assert len(scan.calls) == 2 and len(scan.notices) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"due_date": "2027-01-01T12:00"}, {"due_date": None}, {"repeat": "none"},
    {"reminder_timezone": "America/New_York"}, {"archived": True},
])
async def test_inflight_delivery_does_not_overwrite_schedule_edits(reminder_scan, monkeypatch, changes):
    from routes import note_routes
    scan = reminder_scan
    configure(scan)
    scan.statuses[:] = [200]
    real_dispatch = note_routes.dispatch_reminder

    async def dispatch(**kwargs):
        result = await real_dispatch(**kwargs)
        configure(scan, **changes)
        return result

    monkeypatch.setattr(note_routes, "dispatch_reminder", dispatch)
    await scan.scan()
    saved = read(scan)
    assert all(saved[key] == value for key, value in changes.items())
    if "due_date" not in changes:
        assert saved["due_date"] == scan.instant.isoformat()


@pytest.mark.asyncio
async def test_existing_occurrence_receipt_recovers_failed_browser_date_update(reminder_scan):
    from src.builtin_actions import TaskNoop
    scan = reminder_scan
    configure(scan)
    scan.path.write_text(json.dumps({"note-1": {"at": scan.instant.isoformat(),
        "channel": "webhook", "due_date": scan.instant.isoformat()}}))
    with pytest.raises(TaskNoop):
        await scan.scan()
    assert scan.calls == []
    assert read(scan)["due_date"] == "2026-10-08T12:00"


@pytest.mark.asyncio
async def test_previous_occurrence_receipt_does_not_suppress_a_rescheduled_reminder(reminder_scan):
    scan = reminder_scan
    configure(scan)
    scan.statuses[:] = [200]
    scan.path.write_text(json.dumps({"note-1": {"at": scan.instant.isoformat(),
        "channel": "webhook", "due_date": "2026-10-06T12:00"}}))
    await scan.scan()
    assert len(scan.calls) == 1
    assert read(scan)["due_date"] == "2026-10-08T12:00"


@pytest.mark.asyncio
async def test_saved_zone_keeps_nine_am_after_spring_dst(reminder_scan):
    scan = reminder_scan
    configure(scan, due_date="2026-03-07T09:00", reminder_timezone="America/New_York")
    scan.statuses[:] = [200, 200]
    scan.set_time(datetime.datetime(2026, 3, 7, 14, tzinfo=datetime.timezone.utc))
    await scan.scan()
    assert read(scan)["due_date"] == "2026-03-08T09:00"
    scan.set_time(datetime.datetime(2026, 3, 8, 13, tzinfo=datetime.timezone.utc))
    await scan.scan()
    assert len(scan.calls) == 2
    assert read(scan)["due_date"] == "2026-03-09T09:00"


@pytest.mark.asyncio
async def test_missed_days_catch_up_to_the_current_occurrence_without_a_burst(reminder_scan):
    scan = reminder_scan
    configure(scan, due_date="2026-10-01T12:00")
    scan.statuses[:] = [200]
    await scan.scan()
    assert len(scan.calls) == 1
    assert read(scan)["due_date"] == "2026-10-08T12:00"


@pytest.mark.asyncio
async def test_missed_occurrences_outside_the_window_advance_without_delivery(reminder_scan):
    from src.builtin_actions import TaskNoop
    scan = reminder_scan
    configure(scan, due_date="2026-10-01T08:00")
    with pytest.raises(TaskNoop):
        await scan.scan()
    assert scan.calls == []
    assert read(scan)["due_date"] == "2026-10-08T08:00"
