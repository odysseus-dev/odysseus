"""Route-level coverage for the Microsoft 365 calendar connection.

Proves the HTTP surface behaves: writes to a Graph-backed calendar push
through the same write-back the CalDAV ones use, the OAuth callback refuses
anything it cannot verify, and disconnecting takes the synced calendars with
it.

Async route handlers are pulled straight off the router and called directly
rather than through Starlette's TestClient, matching the existing calendar
route tests.
"""

import tempfile
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
import routes.calendar_routes as croutes
import src.msgraph_calendar as mg
from core.database import CalendarCal, CalendarEvent
from routes.calendar_routes import EventCreate, EventUpdate, _merge_sync_results

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _use_test_db(monkeypatch):
    """Point the routes at this module's DB for the duration of each test.

    Scoped rather than assigned at import: another test module binds the same
    attribute to its own engine, and whichever imported last would otherwise
    win for the whole session.
    """
    monkeypatch.setattr(croutes, "SessionLocal", _TS)


def _req(host="localhost:7000"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user="tester"),
        url=SimpleNamespace(scheme="http"),
        headers={"host": host},
    )


def _endpoint(method, suffix):
    router = croutes.setup_calendar_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith(suffix) and method in getattr(r, "methods", set()):
            return r.endpoint
    raise RuntimeError(f"{method} *{suffix} not found")


def _make_cal(source="msgraph", account_id="acc1"):
    cid = f"{source}-{uuid.uuid4().hex[:10]}"
    db = _TS()
    try:
        db.add(CalendarCal(
            id=cid, owner="tester", name="Work", source=source,
            account_id=account_id, caldav_base_url="REMOTE_CAL",
        ))
        db.commit()
        return cid
    finally:
        db.close()


@pytest.fixture
def pushes(monkeypatch):
    """Capture every write-back the routes trigger."""
    recorded = []

    async def _record(action):
        async def _fn(owner, uid):
            recorded.append((action, uid))
            return {"ok": True}
        return _fn

    async def _create(owner, uid):
        recorded.append(("create", uid))
        return {"ok": True}

    async def _update(owner, uid):
        recorded.append(("update", uid))
        return {"ok": True}

    async def _delete(owner, uid):
        recorded.append(("delete", uid))
        return {"ok": True}

    monkeypatch.setattr(mg, "push_event_create", _create)
    monkeypatch.setattr(mg, "push_event_update", _update)
    monkeypatch.setattr(mg, "push_event_delete", _delete)
    return recorded


@pytest.fixture
def account_store(monkeypatch):
    store = {"accounts": []}
    monkeypatch.setattr(mg, "_load_msgraph_accounts", lambda owner: list(store["accounts"]))
    monkeypatch.setattr(mg, "_save_msgraph_accounts",
                        lambda owner, accounts: store.__setitem__("accounts", list(accounts)))
    return store


# ── Write-back wiring ─────────────────────────────────────────────

async def test_create_on_a_graph_calendar_pushes_upstream(pushes):
    create_event = _endpoint("POST", "/events")
    cal_id = _make_cal("msgraph")
    res = await create_event(_req(), EventCreate(
        summary="Standup", dtstart="2026-06-10T14:00:00Z", calendar_href=cal_id))
    assert res["ok"] is True
    assert pushes == [("create", res["uid"])]


async def test_update_on_a_graph_calendar_pushes_upstream(pushes):
    create_event = _endpoint("POST", "/events")
    update_event = _endpoint("PUT", "/events/{uid}")
    cal_id = _make_cal("msgraph")
    uid = (await create_event(_req(), EventCreate(
        summary="Standup", dtstart="2026-06-10T14:00:00Z", calendar_href=cal_id)))["uid"]
    pushes.clear()
    assert (await update_event(_req(), uid, EventUpdate(summary="Renamed")))["ok"] is True
    assert pushes == [("update", uid)]


async def test_delete_on_a_graph_calendar_pushes_upstream(pushes):
    create_event = _endpoint("POST", "/events")
    delete_event = _endpoint("DELETE", "/events/{uid}")
    cal_id = _make_cal("msgraph")
    uid = (await create_event(_req(), EventCreate(
        summary="Temp", dtstart="2026-06-10T14:00:00Z", calendar_href=cal_id)))["uid"]
    pushes.clear()
    assert (await delete_event(_req(), uid))["ok"] is True
    assert pushes == [("delete", uid)]


async def test_local_calendar_still_pushes_nothing(pushes):
    create_event = _endpoint("POST", "/events")
    cal_id = _make_cal("local", account_id=None)
    await create_event(_req(), EventCreate(
        summary="Private", dtstart="2026-06-10T14:00:00Z", calendar_href=cal_id))
    assert pushes == []


async def test_a_new_graph_event_is_marked_pending_before_the_push(monkeypatch):
    """If the push dies the marker is what makes /sync retry it later."""
    create_event = _endpoint("POST", "/events")
    cal_id = _make_cal("msgraph")

    seen = {}

    async def _capture(owner, uid):
        db = _TS()
        try:
            ev = db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first()
            seen["pending"] = ev.caldav_sync_pending
        finally:
            db.close()
        return {"ok": True}

    monkeypatch.setattr(mg, "push_event_create", _capture)
    await create_event(_req(), EventCreate(
        summary="Pending", dtstart="2026-06-10T14:00:00Z", calendar_href=cal_id))
    assert seen["pending"] == "create"


# ── OAuth callback ────────────────────────────────────────────────

async def test_callback_without_a_code_redirects_with_an_error():
    cb = _endpoint("GET", "/oauth/microsoft/callback")
    res = await cb(_req(), code=None, state=None)
    assert "calendar_oauth_error=missing_code" in res.headers["location"]


async def test_callback_with_a_forged_state_is_refused():
    cb = _endpoint("GET", "/oauth/microsoft/callback")
    res = await cb(_req(), code="c", state="tampered")
    assert "calendar_oauth_error=invalid_state" in res.headers["location"]


async def test_provider_refusal_surfaces_its_aadsts_code():
    """The AADSTS number is what turns "it failed" into a fix."""
    cb = _endpoint("GET", "/oauth/microsoft/callback")
    res = await cb(_req(), error="invalid_client",
                   error_description="AADSTS7000215: Invalid client secret provided.")
    location = res.headers["location"]
    assert "calendar_oauth_code=invalid_client" in location
    assert "calendar_oauth_aadsts=AADSTS7000215" in location


async def test_a_hostile_error_string_cannot_reach_the_redirect():
    """Callback input is attacker-reachable, so only allow-listed shapes pass."""
    cb = _endpoint("GET", "/oauth/microsoft/callback")
    res = await cb(_req(), error="<script>alert(1)</script>",
                   error_description="\r\nSet-Cookie: pwned=1")
    location = res.headers["location"]
    assert "<script>" not in location
    assert "\r" not in location and "\n" not in location
    assert "calendar_oauth_code" not in location


async def test_a_token_response_without_a_refresh_token_is_rejected(account_store, monkeypatch):
    """No offline_access consent means the link dies at the first expiry."""
    from routes.email_helpers import make_oauth_state

    cb = _endpoint("GET", "/oauth/microsoft/callback")
    resp = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"access_token": "at", "expires_in": 3600},
    )
    monkeypatch.setattr("httpx.post", lambda *a, **k: resp)
    res = await cb(_req(), code="c", state=make_oauth_state("acc1", "tester"))
    assert "calendar_oauth_error=no_refresh_token" in res.headers["location"]
    assert account_store["accounts"] == []


async def test_a_successful_callback_stores_encrypted_tokens(account_store, monkeypatch):
    import base64
    import json

    from routes.email_helpers import make_oauth_state

    claims = base64.urlsafe_b64encode(
        json.dumps({"email": "procha@example.com"}).encode()).decode().rstrip("=")
    resp = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
            "id_token": f"h.{claims}.s",
        },
    )
    monkeypatch.setattr("httpx.post", lambda *a, **k: resp)
    cb = _endpoint("GET", "/oauth/microsoft/callback")
    res = await cb(_req(), code="c", state=make_oauth_state("acc1", "tester"))

    assert "calendar_oauth_success=1" in res.headers["location"]
    acc = account_store["accounts"][0]
    assert acc["email"] == "procha@example.com"
    # Encrypted at rest — the raw values must not be what we persisted.
    assert acc["refresh_token"] != "rt"
    assert acc["access_token"] != "at"


async def test_reconnecting_the_same_mailbox_replaces_its_account(account_store, monkeypatch):
    import base64
    import json

    from routes.email_helpers import make_oauth_state

    account_store["accounts"] = [{"id": "old", "email": "procha@example.com"}]
    claims = base64.urlsafe_b64encode(
        json.dumps({"email": "procha@example.com"}).encode()).decode().rstrip("=")
    resp = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
            "id_token": f"h.{claims}.s",
        },
    )
    monkeypatch.setattr("httpx.post", lambda *a, **k: resp)
    cb = _endpoint("GET", "/oauth/microsoft/callback")
    await cb(_req(), code="c", state=make_oauth_state("new", "tester"))
    assert len(account_store["accounts"]) == 1
    assert account_store["accounts"][0]["id"] == "new"


# ── Account management ────────────────────────────────────────────

async def test_listing_never_returns_tokens(account_store):
    account_store["accounts"] = [{
        "id": "a1", "label": "Work", "email": "p@x.com",
        "access_token": "enc-at", "refresh_token": "enc-rt",
    }]
    res = await _endpoint("GET", "/config/microsoft")(_req())
    account = res["accounts"][0]
    assert account == {"id": "a1", "label": "Work", "email": "p@x.com", "connected": True}


async def test_disconnecting_removes_the_synced_calendars(account_store):
    account_store["accounts"] = [{"id": "acc-del", "label": "Work"}]
    cal_id = _make_cal("msgraph", account_id="acc-del")

    res = await _endpoint("DELETE", "/config/microsoft/{account_id}")("acc-del", _req())
    assert res["ok"] is True
    assert account_store["accounts"] == []

    db = _TS()
    try:
        assert db.query(CalendarCal).filter(CalendarCal.id == cal_id).first() is None
    finally:
        db.close()


async def test_disconnecting_leaves_other_accounts_alone(account_store):
    account_store["accounts"] = [{"id": "keep"}, {"id": "drop"}]
    kept = _make_cal("msgraph", account_id="keep")
    await _endpoint("DELETE", "/config/microsoft/{account_id}")("drop", _req())

    db = _TS()
    try:
        assert db.query(CalendarCal).filter(CalendarCal.id == kept).first() is not None
    finally:
        db.close()


async def test_disconnecting_an_unknown_account_is_a_404(account_store):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await _endpoint("DELETE", "/config/microsoft/{account_id}")("nope", _req())
    assert excinfo.value.status_code == 404


# ── Sync result merging ───────────────────────────────────────────

def test_counts_from_both_backends_are_added_up():
    merged = _merge_sync_results(
        {"calendars": 1, "events": 5, "deleted": 0, "errors": ["a"]},
        {"calendars": 2, "events": 3, "deleted": 1, "errors": ["b"]},
    )
    assert merged == {"calendars": 3, "events": 8, "deleted": 1, "errors": ["a", "b"]}


def test_both_direction_results_merge_per_branch():
    merged = _merge_sync_results(
        {"push": {"events": 1, "errors": []}, "pull": {"events": 2, "errors": ["x"]}},
        {"push": {"events": 3, "errors": ["y"]}, "pull": {"events": 4, "errors": []}},
    )
    assert merged["push"] == {"events": 4, "errors": ["y"]}
    assert merged["pull"] == {"events": 6, "errors": ["x"]}


def test_merging_a_single_result_changes_nothing():
    one = {"calendars": 1, "events": 2, "deleted": 0, "errors": []}
    assert _merge_sync_results(one) == one
