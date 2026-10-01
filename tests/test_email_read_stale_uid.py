"""A UID FETCH that matches nothing still returns tagged OK (RFC 3501) — the
server reports zero results via an empty/None untagged response, not an error
status. `_read_email_sync` and `list_attachments` must detect that case and
return a clear error instead of silently building a blank
"(no subject)"/"unknown" message, or crashing on `msg_data[0][1]` when
`msg_data[0]` is `None`.

This happens for UIDs that were valid when a message was first indexed but no
longer resolve to anything — moved, deleted, or the mailbox reindexed
(UIDVALIDITY changed) — which is routine on a large, long-lived mailbox.
"""
from contextlib import contextmanager

import pytest


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


class FakeImapNoMatch:
    """A UID FETCH for a stale/nonexistent UID: tagged OK, no matching data."""

    def select(self, mailbox, readonly=False):
        return "OK", [b"1"]

    def uid(self, command, uid, *args):
        if command == "FETCH":
            # This is exactly what imaplib hands back for a UID FETCH that
            # matches zero messages — confirmed against a live account.
            return "OK", [None]
        raise AssertionError(f"unexpected IMAP command: {command}")


def _install_fakes(monkeypatch, tmp_path, fake_conn):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "email.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield fake_conn

    monkeypatch.setattr(email_routes, "_start_poller", lambda: None)
    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    monkeypatch.setattr(email_routes, "_email_preview_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(email_routes, "_email_preview_cache_put", lambda *_a, **_k: None)
    monkeypatch.setattr(email_routes, "_email_attachment_meta_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(email_routes, "_email_attachment_meta_cache_put", lambda *_a, **_k: None)
    return email_routes


@pytest.mark.asyncio
async def test_stale_uid_read_returns_clear_error_not_blank_message(monkeypatch, tmp_path):
    email_routes = _install_fakes(monkeypatch, tmp_path, FakeImapNoMatch())
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    result = await read_email(
        "999999", folder="INBOX", account_id="acct-a", mark_seen=False, full=False, owner="alice"
    )

    assert result.get("not_found") is True
    assert "999999" in result["error"]
    # Must not silently look like a real, empty message.
    assert result.get("subject") != "(no subject)"
    assert "from_name" not in result


@pytest.mark.asyncio
async def test_stale_uid_full_read_returns_clear_error(monkeypatch, tmp_path):
    """The `full=True` path indexes msg_data[0] directly — same guard required."""
    email_routes = _install_fakes(monkeypatch, tmp_path, FakeImapNoMatch())
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    result = await read_email(
        "999999", folder="INBOX", account_id="acct-a", mark_seen=False, full=True, owner="alice"
    )

    assert result.get("not_found") is True
    assert "999999" in result["error"]


@pytest.mark.asyncio
async def test_stale_uid_attachments_returns_error_not_crash(monkeypatch, tmp_path):
    """Before the fix this raised `'NoneType' object is not subscriptable`
    from inside the route, caught by the outer except and logged as a server
    error — not a clean, predictable "not found" response."""
    email_routes = _install_fakes(monkeypatch, tmp_path, FakeImapNoMatch())
    router = email_routes.setup_email_routes()
    list_attachments = _route_endpoint(router, "/api/email/attachments/{uid}", "GET")

    result = await list_attachments(
        "999999", folder="INBOX", account_id="acct-a", owner="alice"
    )

    assert result == {"attachments": [], "error": "Email not found"}


@pytest.mark.asyncio
async def test_stale_uid_does_not_get_cached_as_a_real_message(monkeypatch, tmp_path):
    """An error result must never be persisted as if it were a valid preview —
    that is what let one bad fetch permanently poison a UID's cache entry."""
    import routes.email_routes as email_routes_mod

    email_routes = _install_fakes(monkeypatch, tmp_path, FakeImapNoMatch())
    put_calls = []
    monkeypatch.setattr(
        email_routes_mod,
        "_email_preview_cache_put",
        lambda *args, **kwargs: put_calls.append(args),
    )
    router = email_routes.setup_email_routes()
    read_email = _route_endpoint(router, "/api/email/read/{uid}", "GET")

    await read_email(
        "999999", folder="INBOX", account_id="acct-a", mark_seen=False, full=False, owner="alice"
    )

    assert put_calls == []
