"""Unit tests for the Google Calendar / Calendly calendar-sync helpers.

These cover the pure logic (id derivation, datetime normalisation, URL/feed
parsing, Calendly response shaping, multi-source result aggregation) without
hitting the network or a database — the parts most likely to regress silently.
"""

from datetime import date, datetime, timezone

import pytest

from src import calendar_sync_common as common
from src import calendly_sync, gcal_sync


# ── calendar_sync_common ──

def test_stable_cal_id_is_deterministic_and_namespaced():
    a = common.stable_cal_id("gcal", "https://example.com/feed.ics")
    b = common.stable_cal_id("gcal", "https://example.com/feed.ics")
    assert a == b                      # same input → same id across calls
    assert a.startswith("gcal-")
    # Same key, different provider prefix must not collide.
    c = common.stable_cal_id("calendly", "https://example.com/feed.ics")
    assert c != a and c.startswith("calendly-")


def test_to_utc_naive_tzaware_converts_to_utc():
    dt = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    out, all_day = common.to_utc_naive(dt)
    assert all_day is False
    assert out == datetime(2026, 5, 31, 12, 0)
    assert out.tzinfo is None          # DB column is naive


def test_to_utc_naive_naive_passthrough():
    dt = datetime(2026, 5, 31, 9, 30)
    out, all_day = common.to_utc_naive(dt)
    assert out == dt and all_day is False


def test_to_utc_naive_date_is_all_day():
    out, all_day = common.to_utc_naive(date(2026, 5, 31))
    assert all_day is True
    assert out == datetime(2026, 5, 31, 0, 0)


def test_empty_result_shape():
    r = common.empty_result("nope")
    assert r == {"calendars": 0, "events": 0, "deleted": 0, "errors": ["nope"]}


# ── gcal_sync ──

@pytest.mark.parametrize("raw,expected", [
    ("webcal://host/cal.ics", "https://host/cal.ics"),
    ("webcals://host/cal.ics", "https://host/cal.ics"),
    ("https://host/cal.ics", "https://host/cal.ics"),
    ("  https://host/cal.ics  ", "https://host/cal.ics"),
])
def test_gcal_normalize_url(raw, expected):
    assert gcal_sync._normalize_url(raw) == expected


def test_gcal_sync_blocking_parses_and_namespaces_uid(monkeypatch):
    """An ICS feed with one timed event upserts one event whose UID is
    namespaced by the calendar id (so identical UIDs across feeds don't
    collide on the shared primary key)."""
    captured = {}

    def fake_upsert(db, cal_id, fields):
        captured["cal_id"] = cal_id
        captured["fields"] = fields

    monkeypatch.setattr(gcal_sync, "get_or_create_cal", lambda *a, **k: None)
    monkeypatch.setattr(gcal_sync, "prune_stale", lambda *a, **k: 0)
    monkeypatch.setattr(gcal_sync, "upsert_event", fake_upsert)

    class _FakeSession:
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    import core.database as db
    monkeypatch.setattr(db, "SessionLocal", lambda: _FakeSession())

    # An event "now-ish" so it falls inside the 90d-back/365d-forward window.
    now = datetime.utcnow()
    dt = now.strftime("%Y%m%dT%H%M%SZ")
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:Work\r\n"
        "BEGIN:VEVENT\r\nUID:abc-123\r\n"
        f"DTSTART:{dt}\r\nDTEND:{dt}\r\nSUMMARY:Standup\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()

    result = gcal_sync._sync_blocking("owner@x", "https://cal/feed.ics", ics)

    assert result["events"] == 1
    assert result["calendars"] == 1
    assert captured["fields"]["summary"] == "Standup"
    # UID is "<cal_id>:<original-uid>"
    assert captured["fields"]["uid"] == f"{captured['cal_id']}:abc-123"
    assert captured["fields"]["is_utc"] is True


def test_gcal_sync_blocking_rejects_garbage_feed():
    result = gcal_sync._sync_blocking("owner@x", "https://cal/feed.ics", b"not a calendar")
    assert result["events"] == 0
    assert result["errors"]


# ── calendly_sync ──

@pytest.mark.parametrize("raw,expected", [
    ("2026-05-31T12:00:00Z", datetime(2026, 5, 31, 12, 0)),
    ("2026-05-31T12:00:00+00:00", datetime(2026, 5, 31, 12, 0)),
    ("2026-05-31T14:00:00+02:00", datetime(2026, 5, 31, 12, 0)),  # → UTC
])
def test_calendly_parse_rfc3339(raw, expected):
    assert calendly_sync._parse_rfc3339(raw) == expected


def test_calendly_parse_rfc3339_bad_input():
    assert calendly_sync._parse_rfc3339("") is None
    assert calendly_sync._parse_rfc3339("garbage") is None


@pytest.mark.parametrize("loc,expected", [
    ({"type": "zoom", "join_url": "https://zoom.us/j/1"}, "https://zoom.us/j/1"),
    ({"type": "physical", "location": "123 Main St"}, "123 Main St"),
    ({"type": "custom"}, "custom"),
    (None, ""),
    ("plain string", ""),
])
def test_calendly_location_str(loc, expected):
    assert calendly_sync._location_str(loc) == expected


def test_calendly_store_events_marks_canceled_and_namespaces_uid(monkeypatch):
    captured = []
    monkeypatch.setattr(calendly_sync, "get_or_create_cal", lambda *a, **k: None)
    monkeypatch.setattr(calendly_sync, "prune_stale", lambda *a, **k: 0)
    monkeypatch.setattr(calendly_sync, "upsert_event", lambda db, cal_id, f: captured.append((cal_id, f)))

    class _FakeSession:
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    import core.database as db
    monkeypatch.setattr(db, "SessionLocal", lambda: _FakeSession())

    now = datetime.utcnow().replace(microsecond=0)
    iso = now.isoformat() + "Z"
    events = [
        {"uri": "https://api.calendly.com/scheduled_events/E1", "name": "Intro call",
         "status": "active", "start_time": iso, "end_time": iso,
         "location": {"type": "zoom", "join_url": "https://zoom.us/j/9"}},
        {"uri": "https://api.calendly.com/scheduled_events/E2", "name": "Old call",
         "status": "canceled", "start_time": iso, "end_time": iso, "location": None},
    ]
    result = calendly_sync._store_events("owner@x", "Calendly", events)

    assert result["events"] == 2
    cal_id, f1 = captured[0]
    assert f1["uid"] == f"{cal_id}:E1"
    assert f1["location"] == "https://zoom.us/j/9"
    assert f1["is_utc"] is True
    _, f2 = captured[1]
    assert f2["summary"].startswith("(Canceled)")


# ── route aggregation ──

def test_aggregate_sync_results_sums_and_filters():
    from routes.calendar_routes import _aggregate_sync_results

    results = [
        {"calendars": 1, "events": 3, "deleted": 1, "errors": []},
        {"calendars": 1, "events": 2, "deleted": 0, "errors": ["Calendly is not configured"]},
        {"calendars": 0, "events": 0, "deleted": 0, "errors": ["real error"]},
        RuntimeError("boom"),
    ]
    agg = _aggregate_sync_results(results)
    assert agg["calendars"] == 2
    assert agg["events"] == 5
    assert agg["deleted"] == 1
    # "not configured" is dropped as noise; real error + exception are kept.
    assert "real error" in agg["errors"]
    assert any("boom" in e for e in agg["errors"])
    assert all("not configured" not in e for e in agg["errors"])
