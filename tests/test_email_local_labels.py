import sqlite3

import pytest
from fastapi import HTTPException


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_email_label_tables_are_created(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)

    email_helpers._init_scheduled_db()

    conn = sqlite3.connect(db_path)
    try:
        defs = conn.execute("PRAGMA table_info(email_label_definitions)").fetchall()
        assigns = conn.execute("PRAGMA table_info(email_label_assignments)").fetchall()
    finally:
        conn.close()

    def_pk = [r[1] for r in sorted((r for r in defs if r[5]), key=lambda r: r[5])]
    assign_pk = [r[1] for r in sorted((r for r in assigns if r[5]), key=lambda r: r[5])]
    assert def_pk == ["owner", "account_id", "slug"]
    assert assign_pk == ["owner", "account_id", "message_key", "label_slug"]


@pytest.mark.asyncio
async def test_email_label_routes_are_owner_and_account_scoped(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "_assert_owns_account", lambda account_id, owner: None)
    email_helpers._init_scheduled_db()

    router = email_routes.setup_email_routes()
    create_label = _route_endpoint(router, "/api/email/labels", "POST")
    list_labels = _route_endpoint(router, "/api/email/labels", "GET")
    add_message_label = _route_endpoint(router, "/api/email/labels/message", "POST")
    remove_message_label = _route_endpoint(router, "/api/email/labels/message/{slug}", "DELETE")

    alice_label = await create_label(
        email_routes.EmailLabelCreateRequest(
            name="Client Work",
            color="#60a5fa",
            account_id="acct-a",
        ),
        owner="alice",
    )
    await create_label(
        email_routes.EmailLabelCreateRequest(
            name="Client Work",
            color="#4ade80",
            account_id="acct-a",
        ),
        owner="bob",
    )

    assert alice_label["label"]["slug"] == "client-work"
    alice_labels = await list_labels(account_id="acct-a", owner="alice")
    bob_labels = await list_labels(account_id="acct-a", owner="bob")
    other_account_labels = await list_labels(account_id="acct-b", owner="alice")
    assert [l["name"] for l in alice_labels["labels"]] == ["Client Work"]
    assert [l["name"] for l in bob_labels["labels"]] == ["Client Work"]
    assert other_account_labels["labels"] == []

    await add_message_label(
        email_routes.EmailLabelMessageRequest(
            label="client-work",
            uid="9",
            folder="INBOX",
            account_id="acct-a",
            message_id="<shared@example.com>",
            subject="Shared",
            sender="sender@example.com",
        ),
        owner="alice",
    )

    alice_emails = [{"uid": "9", "message_id": "<shared@example.com>"}]
    bob_emails = [{"uid": "9", "message_id": "<shared@example.com>"}]
    email_routes._attach_custom_email_labels("alice", "acct-a", "Archive", alice_emails)
    email_routes._attach_custom_email_labels("bob", "acct-a", "Archive", bob_emails)
    assert [l["slug"] for l in alice_emails[0]["labels"]] == ["client-work"]
    assert bob_emails[0]["labels"] == []

    mids, uids = email_routes._email_label_filter_matches("alice", "acct-a", "INBOX", "client-work")
    assert mids == ["<shared@example.com>"]
    assert uids == []

    removed = await remove_message_label(
        "client-work",
        uid="9",
        folder="INBOX",
        account_id="acct-a",
        message_id="<shared@example.com>",
        owner="alice",
    )
    assert removed["removed"] == 1
    alice_emails = [{"uid": "9", "message_id": "<shared@example.com>"}]
    email_routes._attach_custom_email_labels("alice", "acct-a", "INBOX", alice_emails)
    assert alice_emails[0]["labels"] == []


def test_email_label_names_reject_reserved_tags():
    import routes.email_routes as email_routes

    with pytest.raises(HTTPException):
        email_routes._email_label_slug_from_name("Urgent")


def test_email_label_message_key_prefers_message_id():
    import routes.email_routes as email_routes

    assert email_routes._email_label_message_key("INBOX", "9", "<m@example.com>") == "mid:<m@example.com>"
    assert email_routes._email_label_message_key("Archive", "9", "") == "uid:Archive:9"


def test_email_library_exposes_local_label_controls():
    text = open("static/js/emailLibrary.js", encoding="utf-8").read()

    assert "email-labels-manage-btn" in text
    assert "/api/email/labels/message" in text
    assert "filter:label:" in text
    assert "data-email-filter-label" in text
    assert "preserveOpenReader" in text
    assert "_refreshEmailLabelUi" in text
    assert "_refreshEmailCardTags" in text
    assert "_buildEmailCardTagWrap" in text
    assert "email-tags-more-count" in text
    assert "Collapse tags" in text
