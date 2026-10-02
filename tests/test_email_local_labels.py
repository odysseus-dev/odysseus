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
    assert email_routes._email_label_message_key(" Archive ", "0009", "") == "uid:Archive:9"


@pytest.mark.parametrize(
    ("folder", "uid", "message_id"),
    [
        ("INBOX\rInjected", "9", "<m@example.com>"),
        ("INBOX", "9\n10", "<m@example.com>"),
        ("INBOX", "9", "<m@example.com>\x00extra"),
        ("INBOX", "9", "not-a-message-id"),
        ("INBOX", "0", ""),
        ("INBOX", "4294967296", ""),
    ],
)
def test_email_label_identity_rejects_unsafe_or_noncanonical_inputs(folder, uid, message_id):
    import routes.email_routes as email_routes

    with pytest.raises(HTTPException):
        email_routes._email_label_message_key(folder, uid, message_id)


def test_imap_search_quote_rejects_protocol_controls():
    import routes.email_routes as email_routes

    assert email_routes._imap_search_quote('<m@example.com>') == '"<m@example.com>"'
    for value in ("safe\rBAD", "safe\nBAD", "safe\x00BAD", "safe\x7fBAD"):
        with pytest.raises(HTTPException):
            email_routes._imap_search_quote(value)


def test_label_filter_skips_legacy_unsafe_message_identity(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "_assert_owns_account", lambda account_id, owner: None)
    email_helpers._init_scheduled_db()
    conn = sqlite3.connect(db_path)
    try:
        now = "2026-08-27T10:00:00Z"
        conn.execute(
            """
            INSERT INTO email_label_definitions
              (owner, account_id, slug, name, color, description, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("alice", "acct-a", "client-work", "Client Work", "", "", 1, now, now),
        )
        conn.execute(
            """
            INSERT INTO email_label_assignments
              (owner, account_id, folder, message_key, message_id, uid, label_slug, subject, sender, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "alice", "acct-a", "INBOX", "mid:legacy-unsafe",
                "<safe@example.com>\rBAD", "9", "client-work", "", "", now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert email_routes._email_label_filter_matches(
        "alice", "acct-a", "INBOX", "client-work",
    ) == ([], [])


@pytest.mark.asyncio
async def test_label_assignment_canonicalizes_persisted_identity(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "_assert_owns_account", lambda account_id, owner: None)
    email_helpers._init_scheduled_db()

    router = email_routes.setup_email_routes()
    create_label = _route_endpoint(router, "/api/email/labels", "POST")
    add_message_label = _route_endpoint(router, "/api/email/labels/message", "POST")
    await create_label(
        email_routes.EmailLabelCreateRequest(name="Client Work", account_id="acct-a"),
        owner="alice",
    )
    result = await add_message_label(
        email_routes.EmailLabelMessageRequest(
            label="client-work",
            uid="0009",
            folder=" Archive ",
            account_id="acct-a",
        ),
        owner="alice",
    )

    assert result["message_key"] == "uid:Archive:9"
    conn = sqlite3.connect(db_path)
    try:
        persisted = conn.execute(
            "SELECT folder, uid, message_id FROM email_label_assignments",
        ).fetchone()
    finally:
        conn.close()
    assert persisted == ("Archive", "9", "")


@pytest.mark.asyncio
async def test_custom_labels_enrich_index_cached_and_live_responses(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "_assert_owns_account", lambda account_id, owner: None)
    email_helpers._init_scheduled_db()

    router = email_routes.setup_email_routes()
    create_label = _route_endpoint(router, "/api/email/labels", "POST")
    add_message_label = _route_endpoint(router, "/api/email/labels/message", "POST")
    list_emails = _route_endpoint(router, "/api/email/list", "GET")
    search_emails = _route_endpoint(router, "/api/email/search", "GET")
    await create_label(
        email_routes.EmailLabelCreateRequest(name="Client Work", account_id="acct-a"),
        owner="alice",
    )
    await add_message_label(
        email_routes.EmailLabelMessageRequest(
            label="client-work",
            uid="9",
            folder="INBOX",
            account_id="acct-a",
            message_id="<needle@example.com>",
        ),
        owner="alice",
    )
    email_routes._email_index_upsert(
        "alice",
        "acct-a",
        "INBOX",
        [{
            "uid": "9",
            "message_id": "<needle@example.com>",
            "subject": "Needle project update",
            "from_name": "Sender",
            "from_address": "sender@example.com",
            "to": "alice@example.com",
            "cc": "",
            "date": "2026-08-27T10:00:00Z",
            "date_display": "Aug 27",
            "date_epoch": 1_777_000_000,
            "size": 100,
            "flags": "",
            "has_attachments": False,
        }],
    )

    indexed, _, _ = email_routes._email_index_list("alice", "acct-a", "INBOX", "all", 50, 0)
    searched, _, _ = email_routes._email_index_search("alice", "acct-a", "INBOX", "Needle", 50)
    assert [label["slug"] for label in indexed[0]["labels"]] == ["client-work"]
    assert [label["slug"] for label in searched[0]["labels"]] == ["client-work"]

    cached_result = await list_emails(
        folder="INBOX", limit=50, offset=0, filter="all", from_addr=None,
        account_id="acct-a", has_attachments=0, cached_only=1,
        cache_bust=None, owner="alice",
    )
    local_search = search_emails(
        q="Needle", folder="INBOX", limit=50, account_id="acct-a",
        local_only=True, scope="all", owner="alice",
    )
    assert cached_result["emails"][0]["labels"][0]["slug"] == "client-work"
    assert local_search["emails"][0]["labels"][0]["slug"] == "client-work"

    class FakeImap:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list(self):
            return "OK", []

        def select(self, *_args, **_kwargs):
            return "OK", [b"1"]

        def uid(self, command, *_args):
            if command == "SEARCH":
                return "OK", [b"9"]
            raise AssertionError(f"unexpected IMAP command: {command}")

        def logout(self):
            return "BYE", []

    monkeypatch.setattr(email_routes, "_imap_connect", lambda *_args, **_kwargs: FakeImap())
    normal_list = await list_emails(
        folder="INBOX", limit=50, offset=0, filter="all",
        from_addr="sender@example.com", account_id="acct-a",
        has_attachments=0, cached_only=0, cache_bust="test", owner="alice",
    )
    monkeypatch.setattr(email_routes, "_imap", lambda *_args, **_kwargs: FakeImap())
    normal_search = search_emails(
        q="Needle", folder="INBOX", limit=50, account_id="acct-a",
        local_only=False, scope="all", owner="alice",
    )
    assert normal_list["emails"][0]["labels"][0]["slug"] == "client-work"
    assert normal_search["emails"][0]["labels"][0]["slug"] == "client-work"


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
