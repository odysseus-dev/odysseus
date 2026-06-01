"""Unit tests for the Google Calendar / Calendly calendar-sync helpers.

These cover the pure logic (id derivation, datetime normalisation, URL/feed
parsing, Calendly response shaping, multi-source result aggregation) without
hitting the network or a database — the parts most likely to regress silently.

The HTTP-boundary tests (Calendly pagination/read-only, secret non-leakage)
use fakes/TestClient rather than live accounts so they run in CI; real-source
validation against a live Google iCal feed and Calendly token is documented in
the PR thread.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

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


def test_gcal_sync_blocking_handles_recurring_duplicate_uids(monkeypatch):
    """A real Google feed lists recurring events as a master VEVENT plus one
    VEVENT per modified occurrence, all sharing the same UID. Plus feeds can
    repeat an identical UID outright. Because SessionLocal runs autoflush=False,
    the upsert can't dedupe pending inserts mid-loop — so without care these
    collide on the UID primary key and the whole commit fails (the bug that
    silently stored zero events). This must NOT raise and must not emit two
    rows for one key."""
    added = []
    monkeypatch.setattr(gcal_sync, "get_or_create_cal", lambda *a, **k: None)
    monkeypatch.setattr(gcal_sync, "prune_stale", lambda *a, **k: 0)
    monkeypatch.setattr(gcal_sync, "upsert_event", lambda db, cal_id, f: added.append(f["uid"]))

    class _FakeSession:
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    import core.database as db
    monkeypatch.setattr(db, "SessionLocal", lambda: _FakeSession())

    now = datetime.utcnow()
    d1 = now.strftime("%Y%m%dT%H%M%SZ")
    d2 = (now + timedelta(days=7)).strftime("%Y%m%dT%H%M%SZ")
    # Master (RRULE) + an override occurrence (same UID, RECURRENCE-ID) +
    # an exact-duplicate of the master UID.
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:Recurring\r\n"
        f"BEGIN:VEVENT\r\nUID:rec-1\r\nDTSTART:{d1}\r\nDTEND:{d1}\r\n"
        "RRULE:FREQ=WEEKLY\r\nSUMMARY:Weekly standup\r\nEND:VEVENT\r\n"
        f"BEGIN:VEVENT\r\nUID:rec-1\r\nRECURRENCE-ID:{d2}\r\nDTSTART:{d2}\r\n"
        f"DTEND:{d2}\r\nSUMMARY:Weekly standup (moved)\r\nEND:VEVENT\r\n"
        f"BEGIN:VEVENT\r\nUID:rec-1\r\nDTSTART:{d1}\r\nDTEND:{d1}\r\n"
        "SUMMARY:Weekly standup DUP\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode()

    result = gcal_sync._sync_blocking("owner@x", "https://cal/feed.ics", ics)

    assert not result["errors"]                 # no UNIQUE-violation crash
    assert len(added) == len(set(added))         # no duplicate keys emitted
    # Master and the RECURRENCE-ID override are distinct rows; the exact dup
    # of the master is collapsed.
    assert any(u.endswith(":rec-1") for u in added)
    assert any(":rec-1:" in u for u in added)    # the override row
    assert len(added) == 2


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


# ── secret masking ──

def test_mask_feed_url_omits_secret_path():
    from routes.calendar_routes import _mask_feed_url
    secret = (
        "https://calendar.google.com/calendar/ical/"
        "abc123SECRETtoken456%40group.calendar.google.com/private-DEADBEEF/basic.ics"
    )
    hint = _mask_feed_url(secret)
    # Host + trailing filename are fine to show; the opaque secret segments
    # that grant read access must not appear.
    assert hint == "calendar.google.com/…/basic.ics"
    assert "SECRET" not in hint
    assert "DEADBEEF" not in hint


def test_mask_feed_url_empty():
    from routes.calendar_routes import _mask_feed_url
    assert _mask_feed_url("") == ""
    assert _mask_feed_url(None) == ""


# ── Calendly: pagination + read-only (GET) at the HTTP boundary ──

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Records every request so the test can assert the sync only ever GETs
    (never mutates the remote) and follows pagination cursors."""

    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, params=None):
        _FakeAsyncClient.calls.append(("GET", url, params))
        if url.endswith("/users/me"):
            return _FakeResp({"resource": {"uri": "https://api.calendly.com/users/U", "name": "Amish"}})
        # First scheduled_events page carries a cursor; second ends it.
        if params is not None:  # first hop (original params, no cursor baked in)
            return _FakeResp({
                "collection": [{"uri": "https://api.calendly.com/scheduled_events/E1",
                                "name": "Call 1", "status": "active",
                                "start_time": "2026-06-01T10:00:00Z",
                                "end_time": "2026-06-01T10:30:00Z", "location": None}],
                "pagination": {"next_page": "https://api.calendly.com/scheduled_events?page=2"},
            })
        return _FakeResp({
            "collection": [{"uri": "https://api.calendly.com/scheduled_events/E2",
                            "name": "Call 2", "status": "active",
                            "start_time": "2026-06-02T10:00:00Z",
                            "end_time": "2026-06-02T10:30:00Z", "location": None}],
            "pagination": {"next_page": None},
        })


def test_calendly_fetch_events_paginates_and_is_read_only(monkeypatch):
    import httpx
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    name, events = asyncio.run(calendly_sync._fetch_events("tok-123"))

    assert name == "Amish"
    assert [e["uri"].rsplit("/", 1)[-1] for e in events] == ["E1", "E2"]
    # Every HTTP call was a GET — the sync never mutates the Calendly account.
    assert all(method == "GET" for method, _u, _p in _FakeAsyncClient.calls)
    # The first scheduled_events hop constrained the window; the cursor hop
    # dropped the original params (cursor is baked into next_page URL).
    sched = [c for c in _FakeAsyncClient.calls if "scheduled_events" in c[1]]
    assert sched[0][2] and "min_start_time" in sched[0][2] and "max_start_time" in sched[0][2]
    assert sched[1][2] is None


# ── secrets are never returned to the client (config GET endpoints) ──

def _calendar_client(monkeypatch, prefs):
    """Mount the calendar router on a bare app with auth + prefs stubbed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.calendar_routes as cr
    from routes import prefs_routes

    monkeypatch.setattr(cr, "get_current_user", lambda request: "owner@test")
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda user=None: dict(prefs))
    monkeypatch.setattr(prefs_routes, "_save_for_user", lambda user, p: None)

    app = FastAPI()
    app.include_router(cr.setup_calendar_routes())
    return TestClient(app)


def test_gcal_config_get_never_leaks_secret_url(monkeypatch):
    secret = ("https://calendar.google.com/calendar/ical/"
              "TOTALLYsecretTOKEN%40group.calendar.google.com/private-CAFEBABE/basic.ics")
    client = _calendar_client(monkeypatch, {"gcal": {"ics_url": secret}})
    r = client.get("/api/calendar/gcal/config")
    assert r.status_code == 200
    body = r.json()
    assert body["has_url"] is True and body["configured"] is True
    # The raw secret URL / token must never appear in the response.
    blob = r.text
    assert "TOTALLYsecretTOKEN" not in blob
    assert "CAFEBABE" not in blob
    assert "ics_url" not in body  # field that used to carry the full secret


def test_calendly_config_get_never_leaks_token(monkeypatch):
    client = _calendar_client(monkeypatch, {"calendly": {"token": "SUPER-secret-PAT-xyz"}})
    r = client.get("/api/calendar/calendly/config")
    assert r.status_code == 200
    body = r.json()
    assert body["has_token"] is True and body["configured"] is True
    assert body["token"] == ""
    assert "SUPER-secret-PAT-xyz" not in r.text
