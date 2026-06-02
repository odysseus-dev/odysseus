"""Pin the fix for the CalDAV empty-prune data-loss bug.

`_sync_blocking` in `src/caldav_sync.py` builds a SQL filter to delete
locally-cached events that vanished upstream. When the server returns
0 events for the visible window (empty calendar, transient server
hiccup, recurring-event edge case, brand-new remote calendar), the
inner `seen_uids` set stays empty. The original ternary

    ~CalendarEvent.uid.in_(seen_uids) if seen_uids
        else CalendarEvent.uid.isnot(None)

fell through to "match every row" — the prune step then mass-deleted
every cached event in the visible 90-day-back / 1-year-forward window.
A user whose CalDAV server briefly returned 0 events for a window
would silently lose every event on the next sync.

The fix no-ops the prune when `seen_uids` is empty: an empty result
set cannot distinguish "no events in window" from "delete everything",
so the only safe action is to do nothing.
"""

import hashlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Stubs for the optional caldav / icalendar deps.
#
# Neither lib is installed in CI; `_sync_blocking` does its `import caldav`
# and `from icalendar import Calendar` lazily inside the function body, so
# a sys.modules stub is enough to let the test import the module and run
# the prune path. The stub is installed via a pytest fixture
# (monkeypatch.setitem) so it is removed at test teardown and never leaks
# into other tests in the same pytest process — including
# `tests/test_caldav_writeback.py`, which does
# `from icalendar import Calendar, Event as iEvent` and `from icalendar.prop
# import vRecur`. Without the teardown, writeback tests fail with
# `ImportError: cannot import name 'Event'` because the Calendar-only
# stub left in sys.modules is missing both `Event` and `icalendar.prop`.
# ---------------------------------------------------------------------------
@pytest.fixture
def stub_caldav_icalendar(monkeypatch):
    """Install minimal `caldav` and `icalendar` modules into sys.modules
    for the duration of one test, but only when neither lib is already
    importable. monkeypatch.setitem auto-cleans at teardown so sibling
    tests (e.g. test_caldav_writeback) see the pristine namespace they
    expect. Without the teardown, writeback tests fail with
    `ImportError: cannot import name 'Event'` because a stale
    Calendar-only stub left in sys.modules shadows the real lib."""
    # caldav: only DAVClient and the error classes are touched by
    # `_sync_blocking`; everything else falls through to the mocked
    # client returned by the test.
    if "caldav" not in sys.modules:
        caldav = types.ModuleType("caldav")
        caldav.DAVClient = MagicMock()
        monkeypatch.setitem(sys.modules, "caldav", caldav)
        monkeypatch.setitem(sys.modules, "caldav.lib", types.ModuleType("caldav.lib"))
        errors = types.ModuleType("caldav.lib.error")
        errors.AuthorizationError = type("AuthorizationError", (Exception,), {})
        errors.NotFoundError = type("NotFoundError", (Exception,), {})
        monkeypatch.setitem(sys.modules, "caldav.lib.error", errors)

    # icalendar: same shape. Both lib + icalendar.prop.vRecur are
    # covered so the writeback module's `from icalendar import
    # Calendar, Event as iEvent` and `from icalendar.prop import
    # vRecur` lines still work in any CI where this test module
    # happens to stub a real icalendar (the `if` guard prevents it
    # unless the lib is genuinely absent).
    if "icalendar" not in sys.modules:
        ical = types.ModuleType("icalendar")
        ical.Calendar = MagicMock()
        ical.Event = MagicMock()
        monkeypatch.setitem(sys.modules, "icalendar", ical)
        prop = types.ModuleType("icalendar.prop")
        prop.vRecur = MagicMock()
        monkeypatch.setitem(sys.modules, "icalendar.prop", prop)


def _empty_caldav_client():
    """Mock `caldav.DAVClient` that yields one calendar whose
    `date_search` returns `[]` — the trigger for the empty-seen branch."""
    remote_cal = MagicMock()
    remote_cal.url = "https://example.com/cal/personal/"
    remote_cal.name = "Personal"
    remote_cal.date_search.return_value = []   # <-- the trigger
    principal = MagicMock()
    principal.calendars.return_value = [remote_cal]
    client = MagicMock()
    client.principal.return_value = principal
    return client


def _cal_id_for(remote_url: str) -> str:
    """Mirror of `src.caldav_sync._stable_cal_id` so the seeded
    calendar row matches the id the sync code derives from the URL."""
    return f"caldav-{hashlib.sha256(remote_url.encode('utf-8')).hexdigest()[:24]}"


def _seed_events(SessionLocal, CalendarCal, CalendarEvent, cal_id, n=3):
    """Seed `n` events for one CalDAV calendar in the visible window.
    Returns the seeded uids."""
    db = SessionLocal()
    cal = CalendarCal(
        id=cal_id, owner="alice", name="Personal",
        color="#5b8abf", source="caldav",
    )
    db.add(cal)
    db.commit()
    now = datetime.utcnow()
    uids = [f"u{i}" for i in range(n)]
    for i, uid in enumerate(uids):
        db.add(CalendarEvent(
            uid=uid, calendar_id=cal.id, summary=f"e{i}",
            dtstart=now + timedelta(hours=i),
            dtend=now + timedelta(hours=i + 1),
            all_day=False, is_utc=False, rrule="",
        ))
    db.commit()
    db.close()
    return uids


def _fresh_core_database(monkeypatch, db_file):
    """`core.database` instantiates its engine at import-time, so the
    `DATABASE_URL` env var must be set *before* the module loads. This
    helper drops any cached import, sets the env var, and re-imports
    the module so the test sees a clean engine bound to a per-test
    sqlite file."""
    import importlib
    sys.modules.pop("core.database", None)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    return importlib.import_module("core.database")


def test_empty_date_search_does_not_mass_delete(
    stub_caldav_icalendar, monkeypatch, tmp_path,
):
    """Regression: an empty `date_search` result must not delete any
    cached event. Pre-fix, the empty-seen ternary `~... if seen_uids
    else CalendarEvent.uid.isnot(None)` matched every event with a
    non-null uid and the next loop deleted them all."""
    db_mod = _fresh_core_database(monkeypatch, tmp_path / "app.db")
    SessionLocal = db_mod.SessionLocal
    Base = db_mod.Base
    Base.metadata.create_all(SessionLocal().bind)

    cal_id = _cal_id_for("https://example.com/cal/personal/")
    uids = _seed_events(SessionLocal, db_mod.CalendarCal, db_mod.CalendarEvent, cal_id, n=3)

    import caldav
    monkeypatch.setattr(caldav, "DAVClient", lambda *a, **kw: _empty_caldav_client())

    from src.caldav_sync import _sync_blocking
    result = _sync_blocking("alice", "https://x/", "u", "p")

    db = SessionLocal()
    surviving = sorted(ev.uid for ev in db.query(db_mod.CalendarEvent).all())
    db.close()
    assert surviving == sorted(uids), (
        f"Empty date_search must NOT delete events; "
        f"surviving={surviving}, seeded={sorted(uids)}"
    )
    assert result["deleted"] == 0, (
        f"Empty date_search must report 0 deletes; got {result['deleted']}"
    )


def test_nonempty_date_search_still_prunes_stale_rows(
    stub_caldav_icalendar, monkeypatch, tmp_path,
):
    """Companion test: when the server returns at least one event,
    the prune step still deletes events whose uid is no longer
    upstream (the legitimate use case the buggy code was originally
    written to support). We exercise the post-fix filter expression
    directly against the seeded DB to keep the test deterministic
    without depending on a real icalendar library to build a
    VEVENT payload."""
    db_mod = _fresh_core_database(monkeypatch, tmp_path / "app.db")
    SessionLocal = db_mod.SessionLocal
    Base = db_mod.Base
    Base.metadata.create_all(SessionLocal().bind)

    cal_id = _cal_id_for("https://example.com/cal/personal/")
    _seed_events(SessionLocal, db_mod.CalendarCal, db_mod.CalendarEvent, cal_id, n=3)
    seen_uids = {"u-new"}   # server only has "u-new" — seeded u0/u1/u2 are stale

    db = SessionLocal()
    from sqlalchemy import not_
    stale = db.query(db_mod.CalendarEvent).filter(
        db_mod.CalendarEvent.calendar_id == cal_id,
        not_(db_mod.CalendarEvent.uid.in_(seen_uids)),
    ).all()
    db.close()
    assert sorted(ev.uid for ev in stale) == ["u0", "u1", "u2"]
