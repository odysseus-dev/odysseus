"""Issue #800 — CalDAV write-back pushes local changes to the remote server.

Unit-tests the pure pieces against a fake caldav calendar (no network): the
iCalendar serialization, hash-based remote-calendar discovery, and the
create/update/delete orchestration.
"""

import asyncio
import sys
import types
from datetime import datetime

from src.caldav_writeback import (
    build_event_ical,
    find_remote_calendar,
    push_event,
    _stable_cal_id,
)

REMOTE_URL = "https://p69-caldav.icloud.com/123/calendars/home/"
CAL_ID = _stable_cal_id(REMOTE_URL)


class FakeEvent:
    def __init__(self):
        self.data = "OLD"
        self.saved = False
        self.deleted = False

    def save(self):
        self.saved = True

    def delete(self):
        self.deleted = True


class FakeCalendar:
    def __init__(self, url, existing=None):
        self.url = url
        self._existing = existing
        self.saved_ical = None

    def event_by_uid(self, uid):
        if self._existing is None:
            raise Exception("not found")
        return self._existing

    def save_event(self, ical):
        self.saved_ical = ical


def _ev(**over):
    base = dict(
        uid="evt-1", summary="Dentist", description="bring x-rays",
        location="Clinic", dtstart=datetime(2026, 6, 10, 14, 0),
        dtend=datetime(2026, 6, 10, 15, 0), all_day=False, is_utc=True, rrule="",
    )
    base.update(over)
    return base


def test_build_ical_timed_event_has_core_fields():
    ical = build_event_ical(_ev())
    assert "BEGIN:VEVENT" in ical and "END:VEVENT" in ical
    assert "UID:evt-1" in ical
    assert "SUMMARY:Dentist" in ical
    # is_utc -> UTC instant (Z suffix)
    assert "DTSTART:20260610T140000Z" in ical
    assert "DTEND:20260610T150000Z" in ical


def test_build_ical_all_day_uses_date_values():
    ical = build_event_ical(_ev(all_day=True, is_utc=False))
    assert "DTSTART;VALUE=DATE:20260610" in ical


def test_build_ical_includes_rrule():
    ical = build_event_ical(_ev(rrule="FREQ=WEEKLY;BYDAY=MO"))
    assert "RRULE:FREQ=WEEKLY" in ical


def test_find_remote_calendar_matches_by_hash():
    cals = [FakeCalendar("https://other/x/"), FakeCalendar(REMOTE_URL)]
    found = find_remote_calendar(cals, CAL_ID)
    assert found is cals[1]
    assert find_remote_calendar([FakeCalendar("https://nope/")], CAL_ID) is None


def test_push_create_calls_save_event():
    cal = FakeCalendar(REMOTE_URL, existing=None)  # event_by_uid raises -> create
    res = push_event([cal], CAL_ID, _ev(), delete=False)
    assert res["ok"] and res.get("created")
    assert cal.saved_ical and "UID:evt-1" in cal.saved_ical


def test_push_update_overwrites_existing():
    existing = FakeEvent()
    cal = FakeCalendar(REMOTE_URL, existing=existing)
    res = push_event([cal], CAL_ID, _ev(summary="Moved"), delete=False)
    assert res["ok"] and res.get("updated")
    assert existing.saved and "SUMMARY:Moved" in existing.data
    assert cal.saved_ical is None  # used update path, not create


def test_push_delete_removes_existing():
    existing = FakeEvent()
    cal = FakeCalendar(REMOTE_URL, existing=existing)
    res = push_event([cal], CAL_ID, _ev(), delete=True)
    assert res["ok"] and existing.deleted


def test_push_delete_absent_is_ok():
    cal = FakeCalendar(REMOTE_URL, existing=None)
    res = push_event([cal], CAL_ID, _ev(), delete=True)
    assert res["ok"] and "absent" in res.get("note", "")


def test_push_unknown_calendar_reports_not_found():
    cal = FakeCalendar("https://different/")
    res = push_event([cal], CAL_ID, _ev())
    assert res["ok"] is False and "not found" in res["error"]


def test_push_missing_uid_reports_input_error_before_remote_lookup():
    cal = FakeCalendar(REMOTE_URL, existing=FakeEvent())
    res = push_event([cal], CAL_ID, _ev(uid=""))
    assert res["ok"] is False and "uid" in res["error"]
    assert cal._existing.saved is False


def test_writeback_validates_saved_url_before_remote_call(monkeypatch):
    import src.caldav_sync as sync
    import src.caldav_writeback as wb

    prefs_mod = types.ModuleType("routes.prefs_routes")
    prefs_mod._load_for_user = lambda owner: {
        "caldav": {
            "url": " https://dav.example.com/calendars/home/ ",
            "username": owner,
            "password": "enc:pw",
        }
    }
    secret_mod = types.ModuleType("src.secret_storage")
    secret_mod.decrypt = lambda value: "plain-password"
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_mod)
    monkeypatch.setitem(sys.modules, "src.secret_storage", secret_mod)

    captured = {}

    def fake_validate(url):
        captured["validated_url"] = url
        return "https://dav.example.com/calendars/home"

    def fake_writeback_blocking(local_cal_id, ev, delete, url, username, password):
        captured.update(
            {
                "local_cal_id": local_cal_id,
                "delete": delete,
                "url": url,
                "username": username,
                "password": password,
            }
        )
        return {"ok": True}

    async def inline_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(sync, "validate_caldav_url", fake_validate)
    monkeypatch.setattr(wb, "_writeback_blocking", fake_writeback_blocking)
    monkeypatch.setattr(wb.asyncio, "to_thread", inline_to_thread)

    result = asyncio.run(
        wb.writeback_event("alice", "caldav", "caldav-123", {"uid": "evt-1"})
    )

    assert result == {"ok": True}
    assert captured == {
        "validated_url": "https://dav.example.com/calendars/home/",
        "local_cal_id": "caldav-123",
        "delete": False,
        "url": "https://dav.example.com/calendars/home",
        "username": "alice",
        "password": "plain-password",
    }


def test_writeback_rejects_unsafe_saved_url_before_remote_call(monkeypatch):
    import src.caldav_sync as sync
    import src.caldav_writeback as wb

    prefs_mod = types.ModuleType("routes.prefs_routes")
    prefs_mod._load_for_user = lambda owner: {
        "caldav": {
            "url": "http://evil.example/latest/meta-data",
            "username": owner,
            "password": "enc:pw",
        }
    }
    secret_mod = types.ModuleType("src.secret_storage")
    secret_mod.decrypt = lambda value: "plain-password"
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_mod)
    monkeypatch.setitem(sys.modules, "src.secret_storage", secret_mod)

    called = False

    def fake_validate(_url):
        raise ValueError("CalDAV URL host is not allowed")

    def fake_writeback_blocking(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    async def inline_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(sync, "validate_caldav_url", fake_validate)
    monkeypatch.setattr(wb, "_writeback_blocking", fake_writeback_blocking)
    monkeypatch.setattr(wb.asyncio, "to_thread", inline_to_thread)

    result = asyncio.run(
        wb.writeback_event("alice", "caldav", "caldav-123", {"uid": "evt-1"})
    )

    assert result == {"ok": False, "error": "CalDAV URL host is not allowed"}
    assert called is False
