"""The unread dot's minute poll must also pull new mail from IMAP in the
background, or mail that arrives while the mailbox is closed is never seen."""
import asyncio
import sqlite3
import threading

from routes.email import email_routes as er


def _endpoint(name):
    router = er.setup_email_routes()
    return router, next(r.endpoint for r in router.routes if getattr(r, "name", "") == name)


def _index_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE email_message_index (owner TEXT, account_key TEXT, folder TEXT, uid TEXT, flags TEXT, updated_at TEXT)")
    for account in ('hotmail', 'gmail'):
        conn.execute("INSERT INTO email_message_index VALUES ('jo', ?, 'INBOX', '5', '', '2026-11-03T00:00:00Z')", (account,))
    conn.commit()
    conn.close()


def test_poll_answers_from_the_index_and_fetches_in_the_background(monkeypatch, tmp_path):
    db = tmp_path / "index.db"
    _index_db(db)
    monkeypatch.setattr(er, "SCHEDULED_DB", str(db))
    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self.name = name

        def start(self):
            started.append(self.name)

    monkeypatch.setattr(threading, "Thread", FakeThread)
    router, unread_state = _endpoint("unread_state")

    result = asyncio.run(unread_state(folder="INBOX", account_id="hotmail", owner="jo"))
    assert result["unread_count"] == 1 and result["sync"]["source"] == "index"
    assert started == ["email-list-refresh"]

    # Skipped while the inbox list was just fetched (fresh list cache).
    pool = router._email_pool
    pool["list_cache_put"](pool["list_cache_key"]("gmail", "INBOX", "all", 100, 0) + (0, "", "", "jo"), {"emails": []})
    started.clear()
    asyncio.run(unread_state(folder="INBOX", account_id="gmail", owner="jo"))
    assert started == []
