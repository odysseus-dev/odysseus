"""Cross-tenant disclosure regression: GET /api/email/contacts must owner-scope.

The from-sender autocomplete reads distinct `sender` values from the
owner-partitioned `email_tags` table. The query lacked an owner filter, so any
authenticated user received the email correspondents harvested from EVERY user's
classified inbox. It must scope to the caller's own rows (+ legacy null-owner),
exactly like every other email_tags read in routes/email_routes.py.
"""

import asyncio
import sqlite3


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE email_tags (message_id TEXT, owner TEXT, sender TEXT)")
    conn.executemany(
        "INSERT INTO email_tags (message_id, owner, sender) VALUES (?,?,?)",
        [
            ("m1", "alice", "Alice Contact <alice-contact@example.com>"),
            ("m2", "bob", "Bob Secret <bob-contact@example.com>"),
            ("m3", None, "Legacy Shared <legacy@example.com>"),
        ],
    )
    conn.commit()
    conn.close()


def _contacts_endpoint():
    from routes.email_routes import setup_email_routes
    router = setup_email_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith("/contacts") and "GET" in (getattr(r, "methods", set()) or set()):
            return r.endpoint
    raise AssertionError("/contacts route not found")


def test_contacts_is_owner_scoped(tmp_path, monkeypatch):
    db = tmp_path / "scheduled.db"
    _make_db(str(db))
    import routes.email_routes as er
    monkeypatch.setattr(er, "SCHEDULED_DB", str(db))
    # Avoid the real account-config DB lookup; alias resolves to the username.
    monkeypatch.setattr(er, "_email_tag_owner_aliases", lambda account_id, owner: [owner or ""])

    handler = _contacts_endpoint()
    result = asyncio.run(handler(q="", limit=20, owner="alice"))
    addrs = {c["address"] for c in result["contacts"]}

    assert "alice-contact@example.com" in addrs       # caller's own row
    assert "legacy@example.com" in addrs              # legacy null-owner shared row
    assert "bob-contact@example.com" not in addrs     # cross-owner MUST be hidden


def test_contacts_other_owner_only_returns_nothing_cross_tenant(tmp_path, monkeypatch):
    db = tmp_path / "scheduled.db"
    _make_db(str(db))
    import routes.email_routes as er
    monkeypatch.setattr(er, "SCHEDULED_DB", str(db))
    monkeypatch.setattr(er, "_email_tag_owner_aliases", lambda account_id, owner: [owner or ""])

    handler = _contacts_endpoint()
    # carol owns no tag rows → sees only the legacy shared row, never alice/bob.
    result = asyncio.run(handler(q="", limit=20, owner="carol"))
    addrs = {c["address"] for c in result["contacts"]}
    assert addrs == {"legacy@example.com"}
