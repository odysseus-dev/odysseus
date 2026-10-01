"""Tests for the two-way Office 365 calendar sync (Microsoft Graph).

Exchange Online does not speak CalDAV, so `src/msgraph_calendar.py` is the
Graph-shaped sibling of `src/caldav_sync.py`. What is covered here:

- `_parse_graph_dt` — Graph's {dateTime, timeZone} shape, including the
  7-digit fractional seconds `datetime` cannot parse and the Windows zone
  names `zoneinfo` does not know.
- `graph_event_to_row` / `row_to_graph_event` — the field mapping in both
  directions, and the all-day and missing-end edge cases.
- `rrule_to_graph_recurrence` — RRULE shapes the UI can emit, and a refusal
  (None) for anything that would otherwise become the wrong series upstream.
- `_graph_request` — pinned to the Graph host, and non-2xx surfaced as
  GraphError rather than silently returning nothing.
- `_valid_access_token` / `_refresh_access_token` — cached while fresh,
  refreshed when stale, and Microsoft's *rotated* refresh token persisted.
- Write-back — 404 on update/delete treated as success, failures leaving a
  retry marker behind.
"""

import time
import unittest.mock as mock
from datetime import datetime

import pytest

from src import msgraph_calendar as mg


# ── Date parsing ──────────────────────────────────────────────────

def test_utc_datetime_parses_to_naive_utc():
    got = mg._parse_graph_dt({"dateTime": "2026-06-05T09:00:00.0000000", "timeZone": "UTC"})
    assert got == datetime(2026, 6, 5, 9, 0)


def test_seven_digit_fractional_seconds_are_truncated():
    """Graph sends 100ns precision; datetime.fromisoformat tops out at 6."""
    got = mg._parse_graph_dt({"dateTime": "2026-06-05T09:00:00.1234567", "timeZone": "UTC"})
    assert got == datetime(2026, 6, 5, 9, 0, 0, 123456)


def test_explicit_offset_is_converted_to_utc():
    got = mg._parse_graph_dt({"dateTime": "2026-06-05T09:00:00-03:00"})
    assert got == datetime(2026, 6, 5, 12, 0)


def test_named_iana_zone_is_converted():
    got = mg._parse_graph_dt({"dateTime": "2026-06-05T09:00:00", "timeZone": "America/Sao_Paulo"})
    assert got == datetime(2026, 6, 5, 12, 0)


def test_unknown_windows_zone_falls_back_to_utc_rather_than_dropping():
    got = mg._parse_graph_dt({"dateTime": "2026-06-05T09:00:00", "timeZone": "Tokelau Standard Time"})
    assert got == datetime(2026, 6, 5, 9, 0)


@pytest.mark.parametrize("node", [None, {}, {"dateTime": ""}, {"dateTime": "not-a-date"}, "nope"])
def test_unusable_values_return_none(node):
    assert mg._parse_graph_dt(node) is None


# ── Graph → local ─────────────────────────────────────────────────

def _graph_event(**over):
    base = {
        "id": "AAMkAD==", "iCalUId": "040000008200E", "changeKey": "CQAAABYA",
        "subject": "Planning", "bodyPreview": "Bring notes",
        "body": {"contentType": "html", "content": "<p>Bring notes</p>"},
        "location": {"displayName": "HQ"},
        "start": {"dateTime": "2026-06-05T09:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-06-05T10:00:00.0000000", "timeZone": "UTC"},
        "isAllDay": False, "isCancelled": False,
    }
    base.update(over)
    return base


def test_graph_event_maps_onto_the_event_columns():
    row = mg.graph_event_to_row(_graph_event())
    assert row["uid"] == "040000008200E"
    assert row["summary"] == "Planning"
    assert row["description"] == "Bring notes"
    assert row["location"] == "HQ"
    assert row["dtstart"] == datetime(2026, 6, 5, 9, 0)
    assert row["dtend"] == datetime(2026, 6, 5, 10, 0)
    assert row["remote_href"] == "AAMkAD=="
    assert row["remote_etag"] == "CQAAABYA"
    assert row["is_utc"] is True


def test_expanded_occurrence_carries_no_rrule():
    """calendarView returns instances; keeping the rule would re-expand them."""
    row = mg.graph_event_to_row(_graph_event(recurrence={"pattern": {"type": "daily"}}))
    assert row["rrule"] == ""


def test_event_without_ical_uid_falls_back_to_the_graph_id():
    row = mg.graph_event_to_row(_graph_event(iCalUId=None))
    assert row["uid"] == "AAMkAD=="


def test_missing_end_gets_a_positive_duration():
    row = mg.graph_event_to_row(_graph_event(end=None))
    assert row["dtend"] == datetime(2026, 6, 5, 10, 0)


def test_end_before_start_is_clamped():
    row = mg.graph_event_to_row(_graph_event(
        end={"dateTime": "2026-06-05T08:00:00", "timeZone": "UTC"}))
    assert row["dtend"] > row["dtstart"]


def test_all_day_event_without_end_spans_a_day():
    row = mg.graph_event_to_row(_graph_event(isAllDay=True, end=None))
    assert row["all_day"] is True
    assert row["dtend"] == datetime(2026, 6, 6, 9, 0)
    assert row["is_utc"] is False


def test_cancelled_event_keeps_its_status():
    assert mg.graph_event_to_row(_graph_event(isCancelled=True))["status"] == "cancelled"


def test_event_without_a_start_is_skipped():
    assert mg.graph_event_to_row(_graph_event(start=None)) is None


def test_html_body_without_preview_is_stripped_to_text():
    row = mg.graph_event_to_row(_graph_event(
        bodyPreview="", body={"contentType": "html", "content": "<p>Bring <b>notes</b></p>"}))
    assert "<" not in row["description"]
    assert "Bring" in row["description"]


# ── Local → Graph ─────────────────────────────────────────────────

def _local_event(**over):
    base = {
        "uid": "u1", "summary": "Planning", "description": "Bring notes",
        "location": "HQ", "dtstart": datetime(2026, 6, 5, 9, 0),
        "dtend": datetime(2026, 6, 5, 10, 0), "all_day": False, "rrule": "",
    }
    base.update(over)
    return base


def test_local_event_maps_onto_a_graph_body():
    body = mg.row_to_graph_event(_local_event())
    assert body["subject"] == "Planning"
    assert body["start"] == {"dateTime": "2026-06-05T09:00:00", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2026-06-05T10:00:00", "timeZone": "UTC"}
    assert body["location"] == {"displayName": "HQ"}
    assert body["isAllDay"] is False
    assert "recurrence" not in body


def test_all_day_event_is_sent_midnight_to_midnight():
    """Graph rejects an all-day event whose bounds are not midnight."""
    body = mg.row_to_graph_event(_local_event(
        all_day=True, dtstart=datetime(2026, 6, 5, 9, 0), dtend=datetime(2026, 6, 5, 17, 0)))
    assert body["start"]["dateTime"] == "2026-06-05T00:00:00"
    assert body["end"]["dateTime"] == "2026-06-06T00:00:00"


def test_empty_location_is_omitted():
    assert "location" not in mg.row_to_graph_event(_local_event(location=""))


# ── RRULE translation ─────────────────────────────────────────────

def test_daily_with_interval():
    got = mg.rrule_to_graph_recurrence("FREQ=DAILY;INTERVAL=3", datetime(2026, 6, 5))
    assert got["pattern"] == {"type": "daily", "interval": 3}
    assert got["range"]["type"] == "noEnd"


def test_weekly_with_byday():
    got = mg.rrule_to_graph_recurrence("FREQ=WEEKLY;BYDAY=MO,WE", datetime(2026, 6, 5))
    assert got["pattern"]["type"] == "weekly"
    assert got["pattern"]["daysOfWeek"] == ["monday", "wednesday"]


def test_weekly_without_byday_uses_the_start_weekday():
    got = mg.rrule_to_graph_recurrence("FREQ=WEEKLY", datetime(2026, 6, 5))  # a Friday
    assert got["pattern"]["daysOfWeek"] == ["friday"]


def test_monthly_by_month_day():
    got = mg.rrule_to_graph_recurrence("FREQ=MONTHLY;BYMONTHDAY=15", datetime(2026, 6, 5))
    assert got["pattern"] == {"type": "absoluteMonthly", "interval": 1, "dayOfMonth": 15}


def test_monthly_by_weekday_ordinal():
    got = mg.rrule_to_graph_recurrence("FREQ=MONTHLY;BYDAY=2FR", datetime(2026, 6, 5))
    assert got["pattern"]["type"] == "relativeMonthly"
    assert got["pattern"]["index"] == "second"
    assert got["pattern"]["daysOfWeek"] == ["friday"]


def test_monthly_last_weekday():
    got = mg.rrule_to_graph_recurrence("FREQ=MONTHLY;BYDAY=-1SU", datetime(2026, 6, 5))
    assert got["pattern"]["index"] == "last"


def test_yearly_absolute():
    got = mg.rrule_to_graph_recurrence("FREQ=YEARLY;BYMONTH=6;BYMONTHDAY=5", datetime(2026, 6, 5))
    assert got["pattern"] == {"type": "absoluteYearly", "interval": 1, "month": 6, "dayOfMonth": 5}


def test_count_becomes_a_numbered_range():
    got = mg.rrule_to_graph_recurrence("FREQ=DAILY;COUNT=10", datetime(2026, 6, 5))
    assert got["range"] == {"type": "numbered", "startDate": "2026-06-05", "numberOfOccurrences": 10}


def test_until_becomes_an_end_date_range():
    got = mg.rrule_to_graph_recurrence("FREQ=DAILY;UNTIL=20260701T000000Z", datetime(2026, 6, 5))
    assert got["range"] == {"type": "endDate", "startDate": "2026-06-05", "endDate": "2026-07-01"}


def test_rrule_prefix_is_tolerated():
    assert mg.rrule_to_graph_recurrence("RRULE:FREQ=DAILY", datetime(2026, 6, 5)) is not None


@pytest.mark.parametrize("rrule", [
    "", "FREQ=HOURLY", "FREQ=MINUTELY", "nonsense",
    "FREQ=DAILY;UNTIL=not-a-date",
])
def test_untranslatable_rules_are_refused_rather_than_guessed(rrule):
    """A wrong series upstream is worse than a single event."""
    assert mg.rrule_to_graph_recurrence(rrule, datetime(2026, 6, 5)) is None


# ── HTTP layer ────────────────────────────────────────────────────

def _response(status=200, body=None, content=b"{}"):
    resp = mock.Mock()
    resp.status_code = status
    resp.content = content
    resp.reason_phrase = "Error"
    resp.json.return_value = body if body is not None else {}
    return resp


def test_request_targets_the_graph_host_with_a_bearer_token():
    with mock.patch("httpx.request", return_value=_response(body={"ok": 1})) as req:
        mg._graph_request("tok", "GET", "/me/events")
    args, kwargs = req.call_args
    assert args[1] == "https://graph.microsoft.com/v1.0/me/events"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["follow_redirects"] is False


def test_requests_ask_graph_to_answer_in_utc():
    """Without this the times come back in the mailbox's own zone."""
    with mock.patch("httpx.request", return_value=_response(body={})) as req:
        mg._graph_request("tok", "GET", "/me/events")
    assert req.call_args.kwargs["headers"]["Prefer"] == 'outlook.timezone="UTC"'


def test_an_absolute_url_off_the_graph_host_is_refused():
    """nextLink is server-supplied, so it must not be able to redirect us."""
    with mock.patch("httpx.request") as req:
        with pytest.raises(mg.GraphError, match="non-Graph URL"):
            mg._graph_request("tok", "GET", "https://evil.example.com/steal")
    req.assert_not_called()


def test_error_status_raises_with_the_graph_message():
    body = {"error": {"code": "ErrorItemNotFound", "message": "The item was not found."}}
    with mock.patch("httpx.request", return_value=_response(404, body)):
        with pytest.raises(mg.GraphError) as excinfo:
            mg._graph_request("tok", "GET", "/me/events/x")
    assert excinfo.value.status == 404
    assert "not found" in excinfo.value.message.lower()


def test_empty_204_is_not_an_error():
    with mock.patch("httpx.request", return_value=_response(204, content=b"")):
        assert mg._graph_request("tok", "DELETE", "/me/events/x") == {}


def test_pagination_follows_next_link_then_stops():
    pages = [
        {"value": [{"id": "a"}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"},
        {"value": [{"id": "b"}]},
    ]
    with mock.patch.object(mg, "_graph_request", side_effect=pages):
        assert [i["id"] for i in mg._graph_paged("tok", "/me/events")] == ["a", "b"]


# ── Tokens ────────────────────────────────────────────────────────

@pytest.fixture
def account_store(monkeypatch):
    """In-memory stand-in for the per-user prefs the accounts live in."""
    store = {"accounts": []}
    monkeypatch.setattr(mg, "_load_msgraph_accounts", lambda owner: list(store["accounts"]))
    monkeypatch.setattr(mg, "_save_msgraph_accounts",
                        lambda owner, accounts: store.__setitem__("accounts", list(accounts)))
    monkeypatch.setattr(mg, "_find_account",
                        lambda owner, aid: next((a for a in store["accounts"] if a["id"] == aid), None))
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET", "secret")
    return store


def test_fresh_token_is_reused_without_a_network_call(account_store, monkeypatch):
    account_store["accounts"] = [{
        "id": "a1", "access_token": "enc", "refresh_token": "encr",
        "token_expiry": str(int(time.time()) + 3600),
    }]
    monkeypatch.setattr("src.secret_storage.decrypt", lambda v: "live-token")
    with mock.patch("httpx.post") as post:
        assert mg._valid_access_token("o", "a1") == "live-token"
    post.assert_not_called()


def test_expired_token_triggers_a_refresh(account_store, monkeypatch):
    account_store["accounts"] = [{
        "id": "a1", "access_token": "enc", "refresh_token": "encr", "token_expiry": "1",
    }]
    monkeypatch.setattr("src.secret_storage.decrypt", lambda v: "old-refresh")
    monkeypatch.setattr("src.secret_storage.encrypt", lambda v: f"enc:{v}")
    resp = mock.Mock()
    resp.json.return_value = {"access_token": "new-access", "expires_in": 3600}
    resp.raise_for_status.return_value = None
    with mock.patch("httpx.post", return_value=resp):
        assert mg._valid_access_token("o", "a1") == "new-access"


def test_rotated_refresh_token_is_persisted(account_store, monkeypatch):
    """Microsoft spends the old refresh token; dropping the new one strands
    the connection at the next expiry."""
    account_store["accounts"] = [{
        "id": "a1", "access_token": "enc", "refresh_token": "encr", "token_expiry": "1",
    }]
    monkeypatch.setattr("src.secret_storage.decrypt", lambda v: "old-refresh")
    monkeypatch.setattr("src.secret_storage.encrypt", lambda v: f"enc:{v}")
    resp = mock.Mock()
    resp.json.return_value = {
        "access_token": "new-access", "refresh_token": "rotated", "expires_in": 3600,
    }
    resp.raise_for_status.return_value = None
    with mock.patch("httpx.post", return_value=resp):
        mg._valid_access_token("o", "a1")
    assert account_store["accounts"][0]["refresh_token"] == "enc:rotated"


def test_refresh_failure_reports_no_token_and_leaks_nothing(account_store, monkeypatch, caplog):
    account_store["accounts"] = [{
        "id": "a1", "access_token": "enc", "refresh_token": "encr", "token_expiry": "1",
    }]
    monkeypatch.setattr("src.secret_storage.decrypt", lambda v: "old-refresh")
    with mock.patch("httpx.post", side_effect=RuntimeError("boom old-refresh")):
        assert mg._valid_access_token("o", "a1") is None
    assert "old-refresh" not in caplog.text


def test_unknown_account_yields_no_token(account_store):
    assert mg._valid_access_token("o", "missing") is None


# ── Write-back ────────────────────────────────────────────────────

def test_event_already_gone_upstream_is_not_an_error(monkeypatch):
    """A 404 on delete means the state we wanted already holds."""
    monkeypatch.setattr(mg, "_load_event_for_writeback",
                        lambda o, u: ("a1", "cal1", {"remote_href": "rid"}))
    monkeypatch.setattr(mg, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mg, "_graph_request",
                        mock.Mock(side_effect=mg.GraphError(404, "not found")))
    assert mg._push_blocking("o", "u", "delete")["ok"] is True


def test_server_error_on_update_is_reported(monkeypatch):
    monkeypatch.setattr(mg, "_load_event_for_writeback",
                        lambda o, u: ("a1", "cal1", _local_event(remote_href="rid")))
    monkeypatch.setattr(mg, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mg, "_graph_request",
                        mock.Mock(side_effect=mg.GraphError(500, "server blew up")))
    out = mg._push_blocking("o", "u", "update")
    assert out["ok"] is False
    assert "server blew up" in out["error"]


def test_expired_sign_in_is_reported_not_retried_blindly(monkeypatch):
    monkeypatch.setattr(mg, "_load_event_for_writeback",
                        lambda o, u: ("a1", "cal1", _local_event()))
    monkeypatch.setattr(mg, "_valid_access_token", lambda o, a: None)
    out = mg._push_blocking("o", "u", "update")
    assert out["ok"] is False
    assert "reconnect" in out["error"]


def test_non_graph_event_is_skipped(monkeypatch):
    monkeypatch.setattr(mg, "_load_event_for_writeback", lambda o, u: None)
    out = mg._push_blocking("o", "u", "update")
    assert out["ok"] is True and out["skipped"]


def test_create_posts_into_the_events_collection(monkeypatch):
    monkeypatch.setattr(mg, "_load_event_for_writeback",
                        lambda o, u: ("a1", "CAL", _local_event()))
    monkeypatch.setattr(mg, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mg, "_persist_push_result", lambda *a, **k: None)
    request = mock.Mock(return_value={"id": "new", "changeKey": "ck"})
    monkeypatch.setattr(mg, "_graph_request", request)
    assert mg._push_blocking("o", "u", "create")["ok"] is True
    assert request.call_args.args[1:3] == ("POST", "/me/calendars/CAL/events")


def test_update_patches_the_existing_event(monkeypatch):
    monkeypatch.setattr(mg, "_load_event_for_writeback",
                        lambda o, u: ("a1", "CAL", _local_event(remote_href="rid")))
    monkeypatch.setattr(mg, "_valid_access_token", lambda o, a: "tok")
    monkeypatch.setattr(mg, "_persist_push_result", lambda *a, **k: None)
    request = mock.Mock(return_value={"id": "rid", "changeKey": "ck2"})
    monkeypatch.setattr(mg, "_graph_request", request)
    mg._push_blocking("o", "u", "update")
    assert request.call_args.args[1:3] == ("PATCH", "/me/events/rid")


# ── Sync direction ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_both_pushes_before_pulling(monkeypatch):
    """A local edit must reach Graph before the pull can overwrite it."""
    order = []
    async def _push(owner):
        order.append("push")
        return {"events": 1, "errors": []}
    async def _pull(owner):
        order.append("pull")
        return {"calendars": 1, "events": 2, "deleted": 0, "errors": []}
    monkeypatch.setattr(mg, "push_pending_events", _push)
    monkeypatch.setattr(mg, "sync_msgraph", _pull)
    await mg.sync_msgraph_direction("o", "both")
    assert order == ["push", "pull"]


@pytest.mark.asyncio
async def test_unknown_direction_is_refused():
    out = await mg.sync_msgraph_direction("o", "sideways")
    assert out["errors"] and "sideways" in out["errors"][0]


@pytest.mark.asyncio
async def test_sync_without_a_connected_account_says_so(monkeypatch):
    monkeypatch.setattr(mg, "_load_msgraph_accounts", lambda owner: [])
    out = await mg.sync_msgraph("o")
    assert out["errors"] == ["No Microsoft calendar is connected"]


# ── Calendar id scoping ───────────────────────────────────────────

def test_calendar_ids_are_scoped_per_owner_and_account():
    """Two users syncing the same shared calendar must not collide."""
    a = mg._stable_cal_id("REMOTE", owner="alice", account_id="acc1")
    b = mg._stable_cal_id("REMOTE", owner="bob", account_id="acc1")
    c = mg._stable_cal_id("REMOTE", owner="alice", account_id="acc2")
    assert len({a, b, c}) == 3
    assert mg._stable_cal_id("REMOTE", owner="alice", account_id="acc1") == a
