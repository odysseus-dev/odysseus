"""Scheduled emails with a TZ offset or Z suffix must fire on time.

POST /api/email/schedule validated send_at by parsing it (handling Z and
offsets) but stored the RAW client string. The poller selects due rows
with a lexicographic string compare against a naive UTC isoformat, so a
"17:01:00+02:00" schedule (15:01 UTC) did not fire until 17:01 UTC (~2h
late) and a "13:00:00-05:00" schedule (18:00 UTC) fired at 13:00 UTC (5h
early).
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


@pytest.fixture
def schedule(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()
    router = email_routes.setup_email_routes()
    endpoint = _route_endpoint(router, "/api/email/schedule", "POST")

    def _stored(sid):
        row = sqlite3.connect(db_path).execute(
            "SELECT send_at FROM scheduled_emails WHERE id = ?", (sid,)
        ).fetchone()
        return row[0]

    return endpoint, _stored


@pytest.mark.asyncio
async def test_positive_offset_stored_as_naive_utc(schedule):
    endpoint, stored = schedule
    local = datetime.now(timezone(timedelta(hours=2))) + timedelta(hours=1)
    res = await endpoint(
        {"to": "a@example.com", "body": "b", "send_at": local.isoformat()},
        owner="alice",
    )
    assert res["success"] is True
    expected = local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    value = stored(res["id"])
    assert value == expected
    # the poller's lexicographic dueness check now flips at the right time
    utc_due = local.astimezone(timezone.utc).replace(tzinfo=None)
    assert value <= (utc_due + timedelta(minutes=1)).isoformat()
    assert not value <= (utc_due - timedelta(minutes=1)).isoformat()


@pytest.mark.asyncio
async def test_negative_offset_does_not_fire_early(schedule):
    endpoint, stored = schedule
    local = datetime.now(timezone(timedelta(hours=-5))) + timedelta(hours=3)
    res = await endpoint(
        {"to": "a@example.com", "body": "b", "send_at": local.isoformat()},
        owner="alice",
    )
    assert res["success"] is True
    value = stored(res["id"])
    # on the old code the raw "-05:00" string compared as 3h+(-5h offset)
    # in the past and fired on the next poller tick
    assert not value <= datetime.utcnow().isoformat()


@pytest.mark.asyncio
async def test_z_suffix_stored_without_suffix(schedule):
    endpoint, stored = schedule
    utc = datetime.now(timezone.utc) + timedelta(hours=1)
    send_at = utc.replace(tzinfo=None).isoformat() + "Z"
    res = await endpoint(
        {"to": "a@example.com", "body": "b", "send_at": send_at},
        owner="alice",
    )
    assert res["success"] is True
    assert stored(res["id"]) == utc.replace(tzinfo=None).isoformat()


@pytest.mark.asyncio
async def test_naive_utc_send_at_unchanged(schedule):
    endpoint, stored = schedule
    naive = (datetime.utcnow() + timedelta(days=1)).isoformat()
    res = await endpoint(
        {"to": "a@example.com", "body": "b", "send_at": naive}, owner="alice"
    )
    assert res["success"] is True
    assert stored(res["id"]) == naive
