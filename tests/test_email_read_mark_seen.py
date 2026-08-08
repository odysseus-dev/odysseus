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
    def __init__(self, store_status="OK"):
        self.store_status = store_status
        self.selects = []
        self.commands = []

    def select(self, mailbox, readonly=False):
        self.selects.append((mailbox, readonly))
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


def _install_fakes(monkeypatch, tmp_path, *, store_status="OK"):
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
        conn = FakeImap(store_status=store_status)
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
async def test_seen_store_failure_is_reported_and_not_cached_as_read(monkeypatch, tmp_path):
    email_routes, connections, indexed_updates = _install_fakes(
        monkeypatch, tmp_path, store_status="NO"
    )
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    result = await read_email(
        "42", folder="INBOX", account_id="acct-a", mark_seen=True, full=False, owner="alice"
    )

    assert result == {"error": "Mail operation failed"}
    assert len(connections) == 1
    assert [command[0] for command in connections[0].commands] == ["FETCH", "STORE"]
    assert indexed_updates == []


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
async def test_cached_seen_store_failure_is_reported(monkeypatch, tmp_path):
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
    assert second == {"error": "Failed to mark email read"}
    assert len(connections) == 2
    assert [command[0] for command in connections[0].commands] == ["FETCH"]
    assert [command[0] for command in connections[1].commands] == ["STORE"]
    assert indexed_updates == []
