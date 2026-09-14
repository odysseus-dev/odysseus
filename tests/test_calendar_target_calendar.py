"""create_event target-calendar resolution.

`do_manage_calendar` resolves `calendar_href` (and its `calendar` alias) by id,
id prefix or name. Without one it falls back to `_ensure_default_calendar()`,
which returns the owner's *first* calendar row — whichever that happens to be.

That fallback is the trap these tests pin down: when the first calendar is a
local one, the event is created with `caldav_sync_pending = None` and is never
pushed to any remote server, while the tool still reports success.
`caldav_sync_pending` is the signal that separates the two outcomes.
"""

import json
import sys
import uuid

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import CalendarCal, CalendarEvent

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


def _add_calendar(owner: str, name: str, source: str) -> str:
    cal_id = f"{source}-" + uuid.uuid4().hex[:12]
    db = _TS()
    db.add(CalendarCal(id=cal_id, owner=owner, name=name, source=source))
    db.commit()
    db.close()
    return cal_id


def _local_first_then_remote(owner: str):
    """Mirror the common real-world layout: a local calendar was created first
    (on first use of the calendar page), a CalDAV one was connected later."""
    local_id = _add_calendar(owner, "Personal", "local")
    remote_id = _add_calendar(owner, "work@example.com", "caldav")
    return local_id, remote_id


async def _create(owner: str, **args):
    from src.tool_implementations import do_manage_calendar

    args.setdefault("action", "create_event")
    args.setdefault("dtstart", "2026-06-09T09:00:00")
    return await do_manage_calendar(json.dumps(args), owner=owner)


def _placement(summary: str):
    """(calendar_id, caldav_sync_pending) of the event with this summary."""
    db = _TS()
    ev = db.query(CalendarEvent).filter(CalendarEvent.summary == summary).first()
    assert ev is not None, f"event {summary!r} was not created"
    out = (ev.calendar_id, ev.caldav_sync_pending)
    db.close()
    return out


async def test_target_calendar_by_name():
    owner = "tester-" + uuid.uuid4().hex[:6]
    _local, remote_id = _local_first_then_remote(owner)

    res = await _create(owner, summary="By name", calendar_href="work@example.com")
    assert res.get("exit_code") == 0, res
    assert _placement("By name") == (remote_id, "create")


async def test_target_calendar_by_id_prefix():
    owner = "tester-" + uuid.uuid4().hex[:6]
    _local, remote_id = _local_first_then_remote(owner)

    res = await _create(owner, summary="By prefix", calendar_href=remote_id[:10])
    assert res.get("exit_code") == 0, res
    assert _placement("By prefix") == (remote_id, "create")


async def test_calendar_is_accepted_as_alias_of_calendar_href():
    """The schema called `calendar` a list_events filter; create_event takes it too."""
    owner = "tester-" + uuid.uuid4().hex[:6]
    _local, remote_id = _local_first_then_remote(owner)

    res = await _create(owner, summary="By alias", calendar="work@example.com")
    assert res.get("exit_code") == 0, res
    assert _placement("By alias") == (remote_id, "create")


async def test_target_name_matching_is_case_insensitive():
    owner = "tester-" + uuid.uuid4().hex[:6]
    _local, remote_id = _local_first_then_remote(owner)

    res = await _create(owner, summary="Mixed case", calendar_href="WORK@example.COM")
    assert res.get("exit_code") == 0, res
    assert _placement("Mixed case") == (remote_id, "create")


async def test_without_a_target_the_first_calendar_wins():
    """The fallback. With a local calendar first, the event never syncs —
    `caldav_sync_pending` stays None, so writeback skips it."""
    owner = "tester-" + uuid.uuid4().hex[:6]
    local_id, _remote = _local_first_then_remote(owner)

    res = await _create(owner, summary="No target")
    assert res.get("exit_code") == 0, res
    assert _placement("No target") == (local_id, None)


async def test_unknown_target_silently_falls_back():
    """A name that matches nothing does not fail — it lands on the default.
    Worth knowing: a typo in the calendar name is indistinguishable from
    success at the tool boundary."""
    owner = "tester-" + uuid.uuid4().hex[:6]
    local_id, _remote = _local_first_then_remote(owner)

    res = await _create(owner, summary="Unknown target", calendar_href="does-not-exist")
    assert res.get("exit_code") == 0, res
    assert _placement("Unknown target") == (local_id, None)
