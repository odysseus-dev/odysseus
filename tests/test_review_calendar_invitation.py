from email.message import EmailMessage

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as cdb
from routes.email_pollers import _import_calendar_attachments


@pytest.fixture
def invitation_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / 'calendar.db'}")
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(cdb, "SessionLocal", factory)
    import routes.calendar_routes as calendar
    monkeypatch.setattr(calendar, "SessionLocal", factory)
    async def no_push(*args, **kwargs):
        return None
    monkeypatch.setattr(calendar, "_push_caldav_event_after_commit", no_push)
    yield factory
    engine.dispose()


def message(method="REQUEST", sequence=0, start="20261001T100000Z", source_uid="meeting-1", recurrence=None, rule=None):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"METHOD:{method}", "BEGIN:VEVENT",
             f"UID:{source_uid}", f"SEQUENCE:{sequence}", "DTSTAMP:20260916T100000Z",
             "SUMMARY:Meeting"]
    if start:
        lines.append(f"DTSTART:{start}")
    if method == "CANCEL":
        lines.append("STATUS:CANCELLED")
    if recurrence:
        lines.append(f"RECURRENCE-ID:{recurrence}")
    if rule:
        lines.append(f"RRULE:{rule}")
    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    msg = EmailMessage()
    msg.set_content("Invitation")
    msg.add_attachment("\r\n".join(lines).encode(), maintype="text", subtype="calendar", filename="invite.ics")
    return msg


async def apply(msg, owner="alice", sender="organizer@example.test"):
    return await _import_calendar_attachments(msg, owner=owner, sender=sender, subject="Meeting")


@pytest.mark.asyncio
async def test_reschedule_and_cancellation_target_same_event(invitation_db):
    uids, created = await apply(message())
    assert created == 1
    updated, created = await apply(message(sequence=1, start="20261002T100000Z"))
    assert updated == uids and created == 0
    with invitation_db() as db:
        events = db.query(cdb.CalendarEvent).all()
        assert len(events) == 1
        assert events[0].dtstart.day == 2
    await apply(message(method="CANCEL", sequence=2, start=None))
    await apply(message(sequence=1))
    with invitation_db() as db:
        event = db.get(cdb.CalendarEvent, uids[0])
        assert event.status == "cancelled"
        assert event.dtstart.day == 2


@pytest.mark.asyncio
async def test_cancellation_before_invite_does_not_create_event(invitation_db):
    assert await apply(message(method="CANCEL", sequence=2, start=None)) == ([], 0)
    assert await apply(message(sequence=1)) == ([], 0)
    with invitation_db() as db:
        assert db.query(cdb.CalendarEvent).count() == 0


@pytest.mark.asyncio
async def test_same_ics_uid_is_scoped_to_owner(invitation_db):
    alice, _ = await apply(message())
    bob, _ = await apply(message(), owner="bob")
    assert alice != bob
    await apply(message(method="CANCEL", sequence=2, start=None), owner="bob")
    with invitation_db() as db:
        assert db.get(cdb.CalendarEvent, alice[0]).status == "confirmed"
        assert db.get(cdb.CalendarEvent, bob[0]).status == "cancelled"


@pytest.mark.asyncio
async def test_attendee_reply_does_not_create_event(invitation_db):
    assert await apply(message(method="REPLY")) == ([], 0)


@pytest.mark.asyncio
async def test_overlapping_revisions_do_not_race(invitation_db, monkeypatch):
    import asyncio
    from src import tool_implementations
    original = tool_implementations.do_manage_calendar
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def delayed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(tool_implementations, "do_manage_calendar", delayed)
    first = asyncio.create_task(apply(message(sequence=1)))
    await asyncio.wait_for(entered.wait(), 2)
    later = asyncio.create_task(apply(message(sequence=2, start="20261003T100000Z")))
    await asyncio.sleep(0.05)
    assert calls == 1
    release.set()
    await asyncio.wait_for(asyncio.gather(first, later), 3)
    with invitation_db() as db:
        assert db.query(cdb.CalendarEvent).count() == 1
        assert db.query(cdb.CalendarEvent).one().dtstart.day == 3
        assert db.query(cdb.EmailCalendarInvitation).one().sequence == 2


@pytest.mark.asyncio
async def test_invitation_lock_released_when_holder_cancelled(invitation_db):
    import asyncio
    from src.email_calendar_import import _invitation_lock
    entered = asyncio.Event()

    async def holder():
        async with _invitation_lock("alice", "sender@example.test", "meeting"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(holder())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async def reacquire():
        async with _invitation_lock("alice", "sender@example.test", "meeting"):
            return True
    assert await asyncio.wait_for(reacquire(), 2)


@pytest.mark.asyncio
async def test_invitation_lock_excludes_another_process(invitation_db, tmp_path):
    import asyncio
    import os
    import subprocess
    import sys
    if os.name == "nt":
        pytest.skip("POSIX cross-process probe; Windows uses msvcrt")
    from src.email_calendar_import import _invitation_lock
    probe = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(73)
finally:
    os.close(fd)
"""
    async with _invitation_lock("alice", "sender@example.test", "meeting"):
        lock_path = next((tmp_path / ".calendar-import-locks").glob("*.lock"))
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", probe, str(lock_path)], timeout=5)
        assert result.returncode == 73
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", probe, str(lock_path)], timeout=5)
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_same_title_time_does_not_link_different_senders(invitation_db):
    alice, _ = await apply(message(), sender="alice@example.test")
    bob, _ = await apply(message(), sender="bob@example.test")
    assert alice != bob
    await apply(message(method="CANCEL", sequence=2, start=None), sender="bob@example.test")
    with invitation_db() as db:
        assert db.get(cdb.CalendarEvent, alice[0]).status == "confirmed"


@pytest.mark.asyncio
async def test_occurrence_reschedule_excludes_original_without_moving_series(invitation_db):
    import json
    master, _ = await apply(message(rule="FREQ=DAILY;COUNT=3"))
    detached, _ = await apply(message(sequence=1, start="20261002T120000Z", recurrence="20261002T100000Z"))
    with invitation_db() as db:
        parent = db.get(cdb.CalendarEvent, master[0])
        child = db.get(cdb.CalendarEvent, detached[0])
        assert parent.dtstart.day == 1
        assert "2026-10-02T10:00" in json.loads(parent.recurrence_exdates)
        assert child.dtstart.hour == 12 and not child.rrule
    await apply(message(method="CANCEL", sequence=2, start=None, recurrence="20261002T100000Z"))
    with invitation_db() as db:
        assert db.get(cdb.CalendarEvent, master[0]).status == "confirmed"
        assert db.get(cdb.CalendarEvent, detached[0]).status == "cancelled"


@pytest.mark.asyncio
async def test_occurrence_cancellation_before_series_is_preserved(invitation_db):
    import json
    await apply(message(method="CANCEL", sequence=2, start=None, recurrence="20261002T100000Z"))
    master, _ = await apply(message(rule="FREQ=DAILY;COUNT=3"))
    with invitation_db() as db:
        assert "2026-10-02T10:00" in json.loads(db.get(cdb.CalendarEvent, master[0]).recurrence_exdates)


@pytest.mark.asyncio
async def test_series_cancellation_also_cancels_detached_events(invitation_db):
    master, _ = await apply(message(rule="FREQ=DAILY;COUNT=3"))
    detached, _ = await apply(message(sequence=1, start="20261002T120000Z", recurrence="20261002T100000Z"))
    await apply(message(method="CANCEL", sequence=2, start=None))
    with invitation_db() as db:
        assert db.get(cdb.CalendarEvent, master[0]).status == "cancelled"
        assert db.get(cdb.CalendarEvent, detached[0]).status == "cancelled"
