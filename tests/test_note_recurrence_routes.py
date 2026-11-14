import sqlite3
from datetime import datetime, timezone

import pytest

from tests.helpers.database import disposable_database
from tests.test_notes_fail_closed_auth import _build_app, _client


@pytest.fixture
def recurrence_app(monkeypatch, tmp_path):
    from routes import note_routes
    from src import note_recurrence
    monkeypatch.setattr(note_recurrence, "note_now_utc", lambda: datetime(2026, 3, 7, 14, tzinfo=timezone.utc))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(note_routes, "SessionLocal", factory)
        yield _build_app(factory)


async def create(client):
    result = await client.post("/api/notes", headers={"x-test-user": "alice"}, json={
        "title": "Recurring fixture", "due_date": "2026-03-07T09:00", "repeat": "daily",
        "reminder_timezone": "America/New_York"})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.asyncio
async def test_zone_is_persisted_and_unrelated_updates_preserve_it(recurrence_app):
    async with _client(recurrence_app) as client:
        note = await create(client)
        assert note["reminder_schedule"]["due_at"] == "2026-03-07T14:00:00+00:00"
        response = await client.put("/api/notes/" + note["id"], headers={"x-test-user": "alice"},
                                    json={"title": "Rename"})
        assert response.status_code == 200
        assert response.json()["reminder_timezone"] == "America/New_York"
        bad = await client.put("/api/notes/" + note["id"], headers={"x-test-user": "alice"},
                               json={"reminder_timezone": "Invalid/Zone"})
        assert bad.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"due_date": "2027-01-01T12:00"}, {"repeat": "weekly:0"},
    {"reminder_timezone": "Europe/Berlin"}, {"archived": True},
])
async def test_stale_browser_advancement_returns_the_newer_schedule(recurrence_app, change):
    async with _client(recurrence_app) as client:
        note = await create(client)
        expected = {k: note[k] for k in ("due_date", "repeat", "reminder_timezone")}
        response = await client.put("/api/notes/" + note["id"], headers={"x-test-user": "alice"}, json=change)
        assert response.status_code == 200
        response = await client.put("/api/notes/" + note["id"], headers={"x-test-user": "alice"}, json={
            "due_date": "2026-03-08T09:00", "expected_reminder": expected})
        assert response.status_code == 200, response.text
        saved = response.json()
        assert all(saved[k] == v for k, v in change.items())
        if "due_date" not in change:
            assert saved["due_date"] == note["due_date"]


@pytest.mark.asyncio
async def test_browser_can_advance_the_matching_schedule_only_for_its_owner(recurrence_app):
    async with _client(recurrence_app) as client:
        note = await create(client)
        body = {"due_date": "2026-03-08T09:00", "expected_reminder": {
            k: note[k] for k in ("due_date", "repeat", "reminder_timezone")}}
        denied = await client.put("/api/notes/" + note["id"], headers={"x-test-user": "bob"}, json=body)
        assert denied.status_code == 404
        response = await client.put("/api/notes/" + note["id"], headers={"x-test-user": "alice"}, json=body)
        assert response.status_code == 200
        assert response.json()["due_date"] == body["due_date"]
        assert response.json()["reminder_timezone"] == note["reminder_timezone"]


@pytest.mark.asyncio
async def test_stale_fire_does_not_send_the_next_occurrence_early(recurrence_app, monkeypatch):
    from routes import note_routes
    calls = []

    async def dispatch(**kwargs):
        calls.append(kwargs)
        return {"webhook_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", dispatch)
    async with _client(recurrence_app) as client:
        note = await create(client)
        response = await client.post("/api/notes/fire-reminder", headers={"x-test-user": "alice"},
            json={"note_id": note["id"], "expected_due_date": "2026-03-06T09:00"})
        assert response.status_code == 200
        assert response.json()["skipped"] is True
        assert response.json()["schedule"]["due_date"] == note["due_date"]
        assert calls == []
        response = await client.post("/api/notes/fire-reminder", headers={"x-test-user": "alice"},
            json={"note_id": note["id"], "expected_due_date": note["due_date"]})
        assert response.status_code == 200
        assert calls[0]["due_date"] == note["due_date"]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["browser", "webhook", "ntfy", "email"])
async def test_successful_browser_dispatch_uses_the_server_calendar(recurrence_app, monkeypatch, channel):
    from routes import note_routes
    async def dispatch(**kwargs):
        return {"channel": channel, f"{channel}_sent": True}
    monkeypatch.setattr(note_routes, "dispatch_reminder", dispatch)
    async with _client(recurrence_app) as client:
        note = await create(client)
        response = await client.post("/api/notes/fire-reminder", headers={"x-test-user": "alice"},
            json={"note_id": note["id"], "expected_due_date": note["due_date"]})
        assert response.status_code == 200, response.text
        schedule = response.json()["schedule"]
        assert schedule["due_date"] == "2026-03-08T09:00"
        assert schedule["reminder_schedule"]["due_at"] == "2026-03-08T13:00:00+00:00"


@pytest.mark.asyncio
async def test_browser_fallback_does_not_advance_a_failed_external_primary(recurrence_app, monkeypatch):
    from routes import note_routes
    async def dispatch(**kwargs):
        return {"channel": "webhook", "webhook_sent": False, "browser_sent": True}
    monkeypatch.setattr(note_routes, "dispatch_reminder", dispatch)
    async with _client(recurrence_app) as client:
        note = await create(client)
        response = await client.post("/api/notes/fire-reminder", headers={"x-test-user": "alice"},
            json={"note_id": note["id"], "expected_due_date": note["due_date"]})
        assert response.json()["schedule"]["due_date"] == note["due_date"]


@pytest.mark.asyncio
async def test_api_also_refuses_the_early_cross_zone_fire(recurrence_app, monkeypatch):
    from src import note_recurrence
    from routes import note_routes
    monkeypatch.setattr(note_recurrence, "note_now_utc", lambda: datetime(2026, 3, 7, 9, tzinfo=timezone.utc))
    async def dispatch(**kwargs):
        raise AssertionError("Future reminder reached dispatch")
    monkeypatch.setattr(note_routes, "dispatch_reminder", dispatch)
    async with _client(recurrence_app) as client:
        note = await create(client)
        response = await client.post("/api/notes/fire-reminder", headers={"x-test-user": "alice"},
            json={"note_id": note["id"], "expected_due_date": note["due_date"]})
        assert response.json()["skipped"] is True
        assert response.json()["schedule"]["due_date"] == note["due_date"]


@pytest.mark.asyncio
@pytest.mark.parametrize("minute,expected", [(1, "2026-03-07T09:00"), (2, "2026-03-08T09:00")])
async def test_catch_up_only_request_preserves_the_primary_retry_window(recurrence_app, monkeypatch, minute, expected):
    from src import note_recurrence
    from routes import note_routes
    monkeypatch.setattr(note_recurrence, "note_now_utc", lambda: datetime(2026, 3, 7, 14, minute, tzinfo=timezone.utc))
    async def dispatch(**kwargs):
        raise AssertionError("Catch-up-only request reached dispatch")
    monkeypatch.setattr(note_routes, "dispatch_reminder", dispatch)
    async with _client(recurrence_app) as client:
        note = await create(client)
        response = await client.post("/api/notes/fire-reminder", headers={"x-test-user": "alice"},
            json={"note_id": note["id"], "expected_due_date": note["due_date"], "advance_only": True})
        assert response.json()["schedule"]["due_date"] == expected


def test_existing_sqlite_notes_gain_zone_without_losing_rows(monkeypatch, tmp_path):
    from core import database
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO notes VALUES ('legacy', 'Keep me')")
    monkeypatch.setattr(database, "DATABASE_URL", "sqlite:///" + str(path))
    database._migrate_add_notes_sort_order()
    database._migrate_add_notes_sort_order()
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT id,title,reminder_timezone FROM notes").fetchone()
    assert row == ("legacy", "Keep me", None)


@pytest.mark.asyncio
async def test_projection_retains_the_zone_calendar_for_offset_bearing_dates(recurrence_app):
    async with _client(recurrence_app) as client:
        response = await client.post("/api/notes", headers={"x-test-user": "alice"}, json={
            "due_date": "2026-03-10T03:00:00Z", "repeat": "weekly", "reminder_timezone": "America/New_York"})
        assert response.status_code == 200
        assert response.json()["reminder_schedule"]["local_date"] == "2026-03-09T23:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"due_date": "2027-01-01T09:00"}, {"due_date": ""}, {"repeat": "none"},
    {"reminder_timezone": "Europe/Berlin"}, {"archived": True},
])
async def test_dispatch_does_not_overwrite_an_inflight_browser_edit(recurrence_app, monkeypatch, changes):
    from routes import note_routes
    async with _client(recurrence_app) as client:
        note = await create(client)
        async def dispatch(**kwargs):
            response = await client.put("/api/notes/" + note["id"], headers={"x-test-user": "alice"}, json=changes)
            assert response.status_code == 200
            return {"channel": "webhook", "webhook_sent": True}
        monkeypatch.setattr(note_routes, "dispatch_reminder", dispatch)
        response = await client.post("/api/notes/fire-reminder", headers={"x-test-user": "alice"},
            json={"note_id": note["id"], "expected_due_date": note["due_date"]})
        saved = response.json()["schedule"]
        assert all(saved[key] == value for key, value in changes.items())
        if "due_date" not in changes:
            assert saved["due_date"] == note["due_date"]
