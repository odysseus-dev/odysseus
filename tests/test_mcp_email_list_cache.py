import sqlite3

import mcp_servers.email_server as es


def _header(subject: str) -> bytes:
    return (
        f"Subject: {subject}\r\n"
        "From: Sender <sender@example.com>\r\n"
        "Date: Wed, 09 Sep 2026 03:00:00 +0000\r\n"
        "Message-ID: <message@example.com>\r\n\r\n"
    ).encode()


def test_list_emails_fetches_visible_headers_in_one_batch(monkeypatch):
    class Conn:
        def __init__(self):
            self.fetches = []

        def select(self, folder, readonly=False):
            return "OK", []

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"10 11"]
            self.fetches.append(args[0])
            return "OK", [
                (b"1 (UID 10 RFC822.HEADER {120}", _header("Older")),
                (b"2 (UID 11 RFC822.HEADER {120}", _header("Newest")),
            ]

        def logout(self):
            return None

    conn = Conn()
    monkeypatch.setattr(es, "_fixture_list_emails", lambda *args, **kwargs: None)
    monkeypatch.setattr(es, "_indexed_list_rows_by_uids", lambda *args, **kwargs: {})
    monkeypatch.setattr(es, "_imap_connect", lambda account=None: conn)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})

    results = es._list_emails(max_results=2)

    assert conn.fetches == [b"11,10"]
    assert [item["uid"] for item in results] == ["11", "10"]
    assert [item["subject"] for item in results] == ["Newest", "Older"]


def test_list_emails_reuses_indexed_headers_after_live_uid_search(monkeypatch):
    class Conn:
        def __init__(self):
            self.fetches = []

        def select(self, folder, readonly=False):
            return "OK", []

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"42"]
            self.fetches.append(args)
            raise AssertionError("indexed UID must not be fetched remotely")

        def logout(self):
            return None

    conn = Conn()
    monkeypatch.setattr(es, "_fixture_list_emails", lambda *args, **kwargs: None)
    monkeypatch.setattr(es, "_imap_connect", lambda account=None: conn)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})
    monkeypatch.setattr(
        es,
        "_indexed_list_rows_by_uids",
        lambda account, folder, uids: {
            "42": {
                "uid": "42",
                "message_id": "<42@example.com>",
                "subject": "Indexed latest",
                "from": "Sender",
                "from_address": "sender@example.com",
                "date": "Wed, 09 Sep 2026 03:00:00 +0000",
                "attachments": [],
            }
        },
    )

    results = es._list_emails(max_results=1)

    assert conn.fetches == []
    assert results[0]["subject"] == "Indexed latest"


def test_indexed_latest_emails_returns_newest_across_accounts(tmp_path, monkeypatch):
    db = tmp_path / "scheduled.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE email_message_index (
            owner TEXT, account_key TEXT, folder TEXT, uid TEXT,
            message_id TEXT, subject TEXT, from_name TEXT, from_address TEXT,
            date_iso TEXT, date_display TEXT, date_epoch REAL, flags TEXT,
            attachment_names TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO email_message_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("alice", "a1", "INBOX", "1", "m1", "Older", "A", "a@x", "", "old", 1, "", ""),
            ("alice", "b1", "INBOX", "2", "m2", "Newest", "B", "b@x", "", "new", 2, "", "file.pdf"),
        ],
    )
    conn.commit()
    conn.close()
    token = es._CURRENT_OWNER.set("alice")
    monkeypatch.setattr(es, "SCHEDULED_EMAILS_DB", str(db))
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})
    monkeypatch.setattr(
        es,
        "_list_accounts_raw",
        lambda: [
            {"id": "a1", "name": "A", "imap_user": "a@example.com"},
            {"id": "b1", "name": "B", "imap_user": "b@example.com"},
        ],
    )
    try:
        rows = es._indexed_latest_emails(max_results=2)
    finally:
        es._CURRENT_OWNER.reset(token)

    assert [row["subject"] for row in rows] == ["Newest", "Older"]
    assert rows[0]["_account"] == "B"
    assert rows[0]["attachments"] == [{"filename": "file.pdf"}]


def test_list_emails_across_accounts_uses_short_cache(monkeypatch):
    es._EMAIL_LIST_CACHE.clear()
    owner_token = es._CURRENT_OWNER.set("alice")
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(es, "_fixture_list_emails", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        es,
        "_list_accounts_raw",
        lambda: [
            {"id": "a1", "name": "A", "imap_user": "a@example.com"},
            {"id": "b1", "name": "B", "imap_user": "b@example.com"},
        ],
    )
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})

    def fake_list_emails(**kwargs):
        calls.append((kwargs["account"], es._current_owner()))
        return [
            {
                "uid": kwargs["account"],
                "subject": f"subject {kwargs['account']}",
                "from": "Sender",
                "from_address": "sender@example.com",
                "date": "Fri, 21 Aug 2026 03:15:46 +0900",
                "summary": "",
            }
        ]

    monkeypatch.setattr(es, "_list_emails", fake_list_emails)

    try:
        first, first_errors = es._list_emails_across_accounts(max_results=1)
        second, second_errors = es._list_emails_across_accounts(max_results=1)
    finally:
        es._CURRENT_OWNER.reset(owner_token)
        es._EMAIL_LIST_CACHE.clear()

    assert not first_errors
    assert not second_errors
    assert first == second
    assert sorted(calls) == [("a1", "alice"), ("b1", "alice")]


def test_clear_email_list_cache_drops_cached_entries():
    es._EMAIL_LIST_CACHE["x"] = {"created": 1, "results": [], "errors": []}

    es._clear_email_list_cache()

    assert es._EMAIL_LIST_CACHE == {}
