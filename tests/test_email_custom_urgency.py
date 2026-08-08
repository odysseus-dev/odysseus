import sqlite3

import pytest


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def _patch_scheduled_db(monkeypatch, tmp_path):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()
    return db_path, email_helpers, email_routes


def test_email_urgency_levels_are_owner_scoped(tmp_path, monkeypatch):
    _db_path, email_helpers, _email_routes = _patch_scheduled_db(monkeypatch, tmp_path)

    alice_defaults = email_helpers.list_email_urgency_levels("alice")
    assert [level["slug"] for level in alice_defaults] == ["urgent", "reply-soon", "info", "ignore"]

    custom = email_helpers.save_email_urgency_level(
        "alice",
        {
            "name": "Family critical",
            "description": "Family or school pickup needs immediate attention",
            "examples": ["School called", "Family member needs help"],
            "rank": 350,
            "color": "red",
            "notify": True,
        },
    )

    assert custom["slug"] == "family-critical"
    assert custom["notify"] is True
    assert "family-critical" in [level["slug"] for level in email_helpers.list_email_urgency_levels("alice")]
    assert "family-critical" not in [level["slug"] for level in email_helpers.list_email_urgency_levels("bob")]


def test_email_urgency_assignments_are_owner_and_account_scoped(tmp_path, monkeypatch):
    _db_path, email_helpers, _email_routes = _patch_scheduled_db(monkeypatch, tmp_path)

    email_helpers.upsert_email_urgency_assignment(
        owner="alice",
        account_id="acct-1",
        message_id="<shared@example.com>",
        uid="101",
        folder="INBOX",
        urgency_slug="urgent",
        reason="deadline today",
        confidence=0.9,
        source="manual",
        subject="Shared",
        sender="sender@example.com",
    )
    email_helpers.upsert_email_urgency_assignment(
        owner="bob",
        account_id="acct-1",
        message_id="<shared@example.com>",
        uid="201",
        folder="INBOX",
        urgency_slug="reply-soon",
        reason="answer tomorrow",
        confidence=0.5,
        source="classifier",
    )

    alice = email_helpers.load_email_urgency_assignments(
        owner="alice",
        account_id="acct-1",
        folder="INBOX",
        emails=[{"message_id": "<shared@example.com>", "uid": "101", "folder": "INBOX"}],
    )
    bob = email_helpers.load_email_urgency_assignments(
        owner="bob",
        account_id="acct-1",
        folder="INBOX",
        emails=[{"message_id": "<shared@example.com>", "uid": "201", "folder": "INBOX"}],
    )
    wrong_account = email_helpers.load_email_urgency_assignments(
        owner="alice",
        account_id="acct-2",
        folder="INBOX",
        emails=[{"message_id": "<shared@example.com>", "uid": "101", "folder": "INBOX"}],
    )

    assert list(alice.values())[0]["slug"] == "urgent"
    assert list(alice.values())[0]["reason"] == "deadline today"
    assert list(bob.values())[0]["slug"] == "reply-soon"
    assert wrong_account == {}

    email_helpers.clear_email_urgency_assignment(
        owner="alice",
        account_id="acct-1",
        message_id="<shared@example.com>",
        uid="101",
        folder="INBOX",
    )
    assert email_helpers.load_email_urgency_assignments(
        owner="alice",
        account_id="acct-1",
        folder="INBOX",
        emails=[{"message_id": "<shared@example.com>", "uid": "101", "folder": "INBOX"}],
    ) == {}
    assert email_helpers.load_email_urgency_assignments(
        owner="bob",
        account_id="acct-1",
        folder="INBOX",
        emails=[{"message_id": "<shared@example.com>", "uid": "201", "folder": "INBOX"}],
    )


def test_classifier_scan_does_not_replace_a_manual_urgency_assignment(tmp_path, monkeypatch):
    db_path, email_helpers, _email_routes = _patch_scheduled_db(monkeypatch, tmp_path)

    manual = email_helpers.upsert_email_urgency_assignment(
        owner="alice",
        account_id="acct-1",
        message_id="<manual-wins@example.com>",
        uid="301",
        folder="INBOX",
        urgency_slug="urgent",
        reason="user chose this level",
        confidence=1.0,
        source="manual",
        subject="Manual choice",
        sender="sender@example.com",
    )
    conn = sqlite3.connect(db_path)
    try:
        email_helpers._upsert_email_urgency_assignment_row(
            conn,
            owner="alice",
            account_id="acct-1",
            message_id="<manual-wins@example.com>",
            uid="301",
            folder="INBOX",
            urgency_slug="ignore",
            reason="scanner recommendation",
            confidence=0.2,
            source="classifier",
            subject="Manual choice",
            sender="sender@example.com",
        )
        conn.commit()
    finally:
        conn.close()

    assignments = email_helpers.load_email_urgency_assignments(
        owner="alice",
        account_id="acct-1",
        folder="INBOX",
        emails=[{"message_id": "<manual-wins@example.com>", "uid": "301", "folder": "INBOX"}],
    )
    classifier = list(assignments.values())[0]

    assert manual["slug"] == "urgent"
    assert classifier["slug"] == "urgent"
    assert classifier["source"] == "manual"
    assert classifier["reason"] == "user chose this level"

    reassigned = email_helpers.upsert_email_urgency_assignment(
        owner="alice",
        account_id="acct-1",
        message_id="<manual-wins@example.com>",
        uid="301",
        folder="INBOX",
        urgency_slug="reply-soon",
        reason="user changed their choice",
        source="manual",
    )
    assert reassigned["slug"] == "reply-soon"
    assert reassigned["source"] == "manual"
    assert reassigned["reason"] == "user changed their choice"


@pytest.mark.asyncio
async def test_email_urgency_routes_manage_levels_and_assignments(tmp_path, monkeypatch):
    db_path, _email_helpers, email_routes = _patch_scheduled_db(monkeypatch, tmp_path)
    router = email_routes.setup_email_routes()

    list_levels = _route_endpoint(router, "/api/email/urgency-levels", "GET")
    create_level = _route_endpoint(router, "/api/email/urgency-levels", "POST")
    update_level = _route_endpoint(router, "/api/email/urgency-levels/{slug}", "PUT")
    delete_level = _route_endpoint(router, "/api/email/urgency-levels/{slug}", "DELETE")
    set_assignment = _route_endpoint(router, "/api/email/urgency-assignment", "PUT")

    initial = await list_levels(owner="alice")
    assert [level["slug"] for level in initial["levels"]][:2] == ["urgent", "reply-soon"]

    created = await create_level(
        {"name": "Needs pickup", "description": "Family or school pickup", "rank": 360, "color": "orange"},
        owner="alice",
    )
    assert created["level"]["slug"] == "needs-pickup"

    updated = await update_level(
        "needs-pickup",
        {"name": "Family pickup", "description": "Family logistics", "rank": 365, "color": "red", "notify": True},
        owner="alice",
    )
    assert updated["level"]["slug"] == "family-pickup"
    assert updated["level"]["notify"] is True

    assigned = await set_assignment(
        {
            "account_id": "acct-1",
            "message_id": "<route@example.com>",
            "uid": "11",
            "folder": "INBOX",
            "urgency_slug": "family-pickup",
            "reason": "school pickup",
            "subject": "Pickup",
        },
        owner="alice",
    )
    assert assigned["urgency"]["slug"] == "family-pickup"

    cleared = await set_assignment(
        {
            "account_id": "acct-1",
            "message_id": "<route@example.com>",
            "uid": "11",
            "folder": "INBOX",
            "urgency_slug": "",
        },
        owner="alice",
    )
    assert cleared["cleared"] is True

    deleted = await delete_level("family-pickup", owner="alice")
    assert deleted["success"] is True
    conn = sqlite3.connect(db_path)
    try:
        active = conn.execute(
            "SELECT active FROM email_urgency_levels WHERE owner=? AND slug=?",
            ("alice", "family-pickup"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert active == 0
