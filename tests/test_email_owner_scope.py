import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_email_tag_clause_excludes_legacy_owner_rows_for_authenticated_owner(monkeypatch):
    import routes.email_routes as email_routes

    monkeypatch.setattr(
        email_routes,
        "_email_tag_owner_aliases",
        lambda account_id, owner="": ["alice", "alice@example.com"],
    )

    clause, params = email_routes._email_tag_owner_clause("acct-alice", "alice")

    assert clause == "owner IN (?,?)"
    assert params == ["alice", "alice@example.com"]
    assert "owner IS NULL" not in clause


def test_email_tag_clause_keeps_legacy_rows_for_single_user_mode(monkeypatch):
    import routes.email_routes as email_routes

    monkeypatch.setattr(
        email_routes,
        "_email_tag_owner_aliases",
        lambda account_id, owner="": [""],
    )

    clause, params = email_routes._email_tag_owner_clause(None, "")

    assert clause == "(owner IN (?) OR owner IS NULL)"
    assert params == [""]


@pytest.mark.asyncio
async def test_scheduled_email_routes_are_owner_scoped(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()

    router = email_routes.setup_email_routes()
    schedule_email = _route_endpoint(router, "/api/email/schedule", "POST")
    list_scheduled = _route_endpoint(router, "/api/email/scheduled", "GET")
    cancel_scheduled = _route_endpoint(router, "/api/email/scheduled/{sid}", "DELETE")

    send_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    alice = await schedule_email(
        {"to": "a@example.com", "body": "alice body", "send_at": send_at},
        owner="alice",
    )
    bob = await schedule_email(
        {"to": "b@example.com", "body": "bob body", "send_at": send_at},
        owner="bob",
    )

    assert alice["success"] is True
    assert bob["success"] is True

    alice_rows = await list_scheduled(owner="alice")
    bob_rows = await list_scheduled(owner="bob")

    assert [row["id"] for row in alice_rows["scheduled"]] == [alice["id"]]
    assert [row["id"] for row in bob_rows["scheduled"]] == [bob["id"]]

    await cancel_scheduled(bob["id"], owner="alice")
    bob_rows = await list_scheduled(owner="bob")
    assert [row["id"] for row in bob_rows["scheduled"]] == [bob["id"]]

    await cancel_scheduled(alice["id"], owner="alice")
    alice_rows = await list_scheduled(owner="alice")
    assert alice_rows["scheduled"] == []


def test_scheduled_poller_resolves_config_with_row_owner(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_pollers as email_pollers

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_pollers, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO scheduled_emails
        (id, to_addr, subject, body, attachments, send_at, created_at, status, account_id, owner)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            "sched-1",
            "recipient@example.com",
            "Subject",
            "Body",
            "[]",
            "2000-01-01T00:00:00",
            "1999-12-31T00:00:00",
            "acct-alice",
            "alice",
        ),
    )
    conn.commit()
    conn.close()

    calls = []

    def fake_get_email_config(account_id=None, owner=""):
        calls.append(("config", account_id, owner))
        return {
            "from_address": "alice@example.com",
            "smtp_host": "smtp.example.com",
            "smtp_user": "alice@example.com",
            "smtp_password": "secret",
        }

    class FakeImap:
        def __init__(self, account_id=None, owner=""):
            calls.append(("imap", account_id, owner))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def append(self, folder, flags, date_time, message):
            calls.append(("append", folder))

    monkeypatch.setattr(email_pollers, "_get_email_config", fake_get_email_config)
    monkeypatch.setattr(email_pollers, "_send_smtp_message", lambda *args, **kwargs: calls.append(("send", args[1], args[2])))
    monkeypatch.setattr(email_pollers, "_imap", FakeImap)
    monkeypatch.setattr(email_pollers, "_detect_sent_folder", lambda imap: "Sent")
    monkeypatch.setattr(email_pollers, "_cleanup_compose_uploads", lambda attachments: calls.append(("cleanup", attachments)))

    result = email_pollers._scheduled_poll_once()

    assert result == {"sent": ["sched-1"], "failed": []}
    assert ("config", "acct-alice", "alice") in calls
    assert ("imap", "acct-alice", "alice") in calls
