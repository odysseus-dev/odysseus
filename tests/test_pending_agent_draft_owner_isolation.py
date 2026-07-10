"""Issue #5224 — pending agent email drafts must be isolated per owner.

The pending agent-draft routes (list / approve / cancel) already filter by the
authenticated owner, but cross-owner isolation for two *authenticated* owners was
not pinned by a regression test (the existing owner-scope test only covered an
ownerless legacy row vs. a single owner). These tests seed ``agent_draft`` rows
for two distinct owners and assert that neither owner can see, approve, or cancel
the other owner's draft.
"""

import sqlite3

import pytest


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


@pytest.fixture
def _seeded_scheduled_db(tmp_path, monkeypatch):
    """Create a scheduled_emails DB with one agent_draft per owner."""
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()

    conn = sqlite3.connect(db_path)
    conn.executemany(
        """
        INSERT INTO scheduled_emails
        (id, to_addr, subject, body, attachments, send_at, created_at, status, account_id, owner)
        VALUES (?, ?, ?, ?, '[]', '9999-12-31T00:00:00', ?, 'agent_draft', ?, ?)
        """,
        [
            ("draft-alice", "alice@example.com", "Alice draft", "alice body",
             "2026-01-01", "acct-alice", "alice"),
            ("draft-bob", "bob@example.com", "Bob draft", "bob body",
             "2026-01-02", "acct-bob", "bob"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


async def test_list_only_returns_callers_own_drafts(_seeded_scheduled_db):
    import routes.email_routes as email_routes

    router = email_routes.setup_email_routes()
    list_pending = _route_endpoint(router, "/api/email/pending", "GET")

    alice_rows = await list_pending(owner="alice")
    bob_rows = await list_pending(owner="bob")

    assert [row["id"] for row in alice_rows["pending"]] == ["draft-alice"]
    assert [row["id"] for row in bob_rows["pending"]] == ["draft-bob"]
    # Each owner must not see the other's subject/body.
    assert "Bob draft" not in str(alice_rows)
    assert "Alice draft" not in str(bob_rows)


async def test_approve_is_scoped_to_owner(_seeded_scheduled_db):
    import routes.email_routes as email_routes

    router = email_routes.setup_email_routes()
    approve = _route_endpoint(router, "/api/email/pending/{sid}/approve", "POST")

    # Bob must not be able to approve Alice's draft.
    assert (await approve("draft-alice", owner="bob"))["success"] is False
    # And vice versa.
    assert (await approve("draft-bob", owner="alice"))["success"] is False

    # Neither draft changed status.
    conn = sqlite3.connect(_seeded_scheduled_db)
    try:
        statuses = dict(conn.execute(
            "SELECT id, status FROM scheduled_emails ORDER BY id"
        ).fetchall())
    finally:
        conn.close()
    assert statuses == {"draft-alice": "agent_draft", "draft-bob": "agent_draft"}

    # The rightful owner can approve their own draft.
    assert (await approve("draft-alice", owner="alice"))["success"] is True
    conn = sqlite3.connect(_seeded_scheduled_db)
    try:
        alice_status = conn.execute(
            "SELECT status FROM scheduled_emails WHERE id=?", ("draft-alice",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert alice_status == "pending"


async def test_cancel_is_scoped_to_owner(_seeded_scheduled_db):
    import routes.email_routes as email_routes

    router = email_routes.setup_email_routes()
    cancel = _route_endpoint(router, "/api/email/pending/{sid}", "DELETE")

    # Alice must not be able to cancel Bob's draft.
    assert (await cancel("draft-bob", owner="alice"))["success"] is False
    # And vice versa.
    assert (await cancel("draft-alice", owner="bob"))["success"] is False

    # Neither draft changed status.
    conn = sqlite3.connect(_seeded_scheduled_db)
    try:
        statuses = dict(conn.execute(
            "SELECT id, status FROM scheduled_emails ORDER BY id"
        ).fetchall())
    finally:
        conn.close()
    assert statuses == {"draft-alice": "agent_draft", "draft-bob": "agent_draft"}

    # The rightful owner can cancel their own draft.
    assert (await cancel("draft-bob", owner="bob"))["success"] is True
    conn = sqlite3.connect(_seeded_scheduled_db)
    try:
        bob_status = conn.execute(
            "SELECT status FROM scheduled_emails WHERE id=?", ("draft-bob",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert bob_status == "cancelled"
