import asyncio
from contextlib import contextmanager

import pytest


RAW_EMAIL = (
    b"From: Sender <sender@example.com>\r\n"
    b"To: Alice <alice@example.com>\r\n"
    b"Subject: Single authoritative open\r\n"
    b"Message-ID: <single-open@example.com>\r\n"
    b"Date: Tue, 04 Aug 2026 12:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Body"
)


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


class FakeImap:
    def __init__(self, store_status="OK", readonly_mailbox=False):
        self.store_status = store_status
        # Shared archives and some provider folders reject a read-write SELECT.
        self.readonly_mailbox = readonly_mailbox
        self.selects = []
        self.commands = []

    def select(self, mailbox, readonly=False):
        self.selects.append((mailbox, readonly))
        if self.readonly_mailbox and not readonly:
            raise OSError("[READ-ONLY] Mailbox is read-only")
        return "OK", [b"1"]

    def uid(self, command, uid, *args):
        self.commands.append((command, uid, *args))
        if command == "FETCH":
            header, body = RAW_EMAIL.split(b"\r\n\r\n", 1)
            return "OK", [
                (b"1 (UID 42 BODY[HEADER])", header + b"\r\n\r\n"),
                (b"1 (UID 42 BODY[TEXT]<0>)", body),
            ]
        if command == "STORE":
            # RFC 3501 STORE takes a parenthesized flag-list. GreenMail rejects
            # the formerly emitted bare ``\Seen`` atom with BAD, so keep the
            # fake strict enough to catch that provider-compatibility failure.
            if args != ("+FLAGS", "(\\Seen)"):
                return "BAD", [b"Expected:'(' found:'\\'"]
            return self.store_status, []
        raise AssertionError(f"unexpected IMAP command: {command}")


def _install_fakes(monkeypatch, tmp_path, *, store_status="OK", readonly_mailbox=False):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "email.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()

    connections = []
    indexed_updates = []

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        conn = FakeImap(store_status=store_status, readonly_mailbox=readonly_mailbox)
        connections.append(conn)
        yield conn

    monkeypatch.setattr(email_routes, "_start_poller", lambda: None)
    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    monkeypatch.setattr(email_routes, "_email_preview_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(email_routes, "_email_preview_cache_put", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(email_routes, "_email_attachment_meta_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(email_routes, "_email_attachment_meta_cache_put", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        email_routes,
        "_email_index_update_flags",
        lambda *args, **_kwargs: indexed_updates.append(args),
    )
    return email_routes, connections, indexed_updates


@pytest.mark.asyncio
@pytest.mark.parametrize("mark_seen", [True, False])
async def test_read_email_seen_contract_uses_one_imap_connection(monkeypatch, tmp_path, mark_seen):
    email_routes, connections, indexed_updates = _install_fakes(monkeypatch, tmp_path)
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    result = await read_email(
        "42",
        folder="INBOX",
        account_id="acct-a",
        mark_seen=mark_seen,
        full=False,
        owner="alice",
    )

    assert result["uid"] == "42"
    assert len(connections) == 1
    conn = connections[0]
    assert conn.selects == [(conn.selects[0][0], not mark_seen)]
    assert [command[0] for command in conn.commands] == (
        ["FETCH", "STORE"] if mark_seen else ["FETCH"]
    )
    assert "BODY.PEEK[HEADER]" in conn.commands[0][2]
    if mark_seen:
        assert conn.commands[1][2:] == ("+FLAGS", "(\\Seen)")
        assert indexed_updates == [("alice", "acct-a", "INBOX", "42", "\\Seen", True)]
    else:
        assert indexed_updates == []


@pytest.mark.asyncio
async def test_cached_read_awaits_one_seen_store_without_refetch(monkeypatch, tmp_path):
    email_routes, connections, indexed_updates = _install_fakes(monkeypatch, tmp_path)
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    first = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=False, full=False, owner="alice"
    )
    monkeypatch.setattr(
        asyncio,
        "create_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached mark-seen must be awaited, not scheduled")
        ),
    )
    second = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=True, full=False, owner="alice"
    )

    assert first["message_id"] == second["message_id"]
    assert len(connections) == 2
    assert [command[0] for command in connections[0].commands] == ["FETCH"]
    assert [command[0] for command in connections[1].commands] == ["STORE"]
    assert connections[1].commands[0][2:] == ("+FLAGS", "(\\Seen)")
    assert connections[1].selects[0][1] is False
    assert indexed_updates == [("alice", "acct-a", "INBOX", "42", "\\Seen", True)]


@pytest.mark.asyncio
async def test_seen_store_failure_returns_the_body_and_reports_the_failure(monkeypatch, tmp_path):
    """A failed STORE must not cost the reader the message.

    The body was fetched successfully before the flag update was attempted, so
    the response stays a normal read and carries `mark_seen_failed` for the
    client to roll its optimistic unread marker back.
    """
    email_routes, connections, indexed_updates = _install_fakes(
        monkeypatch, tmp_path, store_status="NO"
    )
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    result = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=True, full=False, owner="alice"
    )

    assert "error" not in result
    assert result["uid"] == "42"
    assert result["mark_seen_failed"] is True
    assert len(connections) == 1
    assert [command[0] for command in connections[0].commands] == ["FETCH", "STORE"]
    # The local index must not claim a transition the provider rejected.
    assert indexed_updates == []


@pytest.mark.asyncio
async def test_read_only_mailbox_serves_the_message_without_marking_seen(monkeypatch, tmp_path):
    """A mailbox that refuses a read-write SELECT is still readable.

    Opening the message is the user's actual goal; the \\Seen transition is a
    side effect of it. A folder that cannot accept flag changes must therefore
    fall back to a read-only selection rather than failing the open.
    """
    email_routes, connections, indexed_updates = _install_fakes(
        monkeypatch, tmp_path, readonly_mailbox=True
    )
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    result = await read_email(
        "42", folder="Archive", account_id="acct-a", mark_seen=True, full=False, owner="alice"
    )

    assert "error" not in result
    assert result["uid"] == "42"
    assert result["mark_seen_failed"] is True
    # Read-write attempt first, then the read-only retry on the same connection.
    assert [readonly for _mailbox, readonly in connections[0].selects] == [False, True]
    # No STORE is attempted once the mailbox is known to be read-only.
    assert [command[0] for command in connections[0].commands] == ["FETCH"]
    assert indexed_updates == []


@pytest.mark.asyncio
async def test_failed_seen_state_is_not_replayed_from_cache(monkeypatch, tmp_path):
    """`mark_seen_failed` describes one request, not the stored message.

    A second read that does not ask to mark seen must come back clean, or every
    later reader would inherit a STORE failure it never issued.
    """
    email_routes, connections, _ = _install_fakes(monkeypatch, tmp_path, store_status="NO")
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    failed = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=True, full=False, owner="alice"
    )
    replayed = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=False, full=False, owner="alice"
    )

    assert failed["mark_seen_failed"] is True
    assert replayed.get("mark_seen_failed", False) is False
    assert replayed["uid"] == "42"


@pytest.mark.asyncio
async def test_unparseable_read_does_not_mark_seen(monkeypatch, tmp_path):
    email_routes, connections, indexed_updates = _install_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(
        email_routes.email_mod,
        "message_from_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("malformed message")),
    )
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    result = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=True, full=False, owner="alice"
    )

    assert result == {"error": "Mail operation failed"}
    assert len(connections) == 1
    assert [command[0] for command in connections[0].commands] == ["FETCH"]
    assert indexed_updates == []


@pytest.mark.asyncio
async def test_cached_seen_store_failure_returns_the_cached_body(monkeypatch, tmp_path):
    """A cache hit already holds a complete message; a failed STORE cannot take it away.

    This is the path where withholding the body would be least defensible — the
    response is served from memory and needed no network at all.
    """
    email_routes, connections, indexed_updates = _install_fakes(
        monkeypatch, tmp_path, store_status="NO"
    )
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    first = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=False, full=False, owner="alice"
    )
    second = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=True, full=False, owner="alice"
    )

    assert first["uid"] == "42"
    assert "error" not in second
    assert second["uid"] == "42"
    assert second["body"] == first["body"]
    assert second["mark_seen_failed"] is True
    assert len(connections) == 2
    assert [command[0] for command in connections[0].commands] == ["FETCH"]
    assert [command[0] for command in connections[1].commands] == ["STORE"]
    assert indexed_updates == []
