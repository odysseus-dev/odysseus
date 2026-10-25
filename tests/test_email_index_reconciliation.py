"""Live folder UID snapshots reconcile only the matching durable list cache."""
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class Mailbox:
    def __init__(self):
        self.search_status = "OK"
        self.search_data = [b"1 3"]
        self.select_status = "OK"
        self.on_search = lambda: None

    def select(self, folder, readonly=False):
        return self.select_status, [b"2"]

    def noop(self):
        return "OK", []

    def logout(self):
        pass

    def uid(self, command, *args):
        if command == "SEARCH":
            self.on_search()
            if isinstance(self.search_data, Exception):
                raise self.search_data
            return self.search_status, self.search_data
        assert command == "FETCH"
        uids = args[0].decode().split(",")
        return "OK", [
            (f"1 (UID {uid} FLAGS () RFC822.SIZE 120)".encode(),
             f"Subject: Message {uid}\r\nFrom: sender@example.com\r\nDate: Wed, 1 Jan 2020 00:00:00 +0000\r\nMessage-ID: <{uid}@example.com>\r\n\r\n".encode())
            for uid in uids if uid.isdigit() and not uid.startswith("0")
        ]


@pytest.fixture
def mailbox_cache(tmp_path, monkeypatch):
    from routes.email import email_helpers as helpers, email_routes as routes

    db_path = tmp_path / "mail.db"
    monkeypatch.setattr(helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(routes, "SCHEDULED_DB", db_path)
    helpers._init_scheduled_db()
    mailbox = Mailbox()
    monkeypatch.setattr(routes, "_imap_connect", lambda *args, **kwargs: mailbox)
    monkeypatch.setattr(routes, "_resolve_mail_folder", lambda conn, folder, **kwargs: folder)
    monkeypatch.setattr(routes, "_start_poller", lambda: None)
    monkeypatch.setattr(routes, "_record_email_received_events", lambda *args: None)
    monkeypatch.setattr(routes, "_email_tag_owner_aliases", lambda account_id, owner="": [owner])
    monkeypatch.setattr(routes, "_hide_unlinked_calendar_tags", lambda *args: None)
    monkeypatch.setattr(routes, "_POOL_HOOKS", {})
    monkeypatch.setattr(routes, "_CACHE_HOOKS", {})
    owner = ["alice"]
    app = FastAPI()
    app.include_router(routes.setup_email_routes())
    app.dependency_overrides[routes.require_owner] = lambda: owner[0]

    def put(uids, owner="alice", account="a", folder="INBOX", generation=None):
        routes._email_index_upsert(owner, account, folder, [
            {"uid": uid, "subject": f"Cached {uid}", "date_epoch": int(uid)} for uid in uids
        ], generation)

    put(["1", "2", "3"])
    put(["2"], owner="bob")
    put(["2"], account="b")
    put(["2"], folder="Archive")

    def cached_uids(owner="alice", account="a", folder="INBOX"):
        rows, _, _ = routes._email_index_list(owner, account, folder, "all", 200, 0)
        return {row["uid"] for row in rows}

    with TestClient(app) as client:
        def refresh(**params):
            response = client.get("/api/email/list", params={
                "account_id": "a", "refresh": 1, **params,
            })
            assert response.status_code == 200
            return response.json()

        yield SimpleNamespace(client=client, mailbox=mailbox, put=put,
                              cached_uids=cached_uids, refresh=refresh, routes=routes)


def test_successful_full_search_prunes_stale_rows_before_reopening(mailbox_cache):
    cache = mailbox_cache
    result = cache.refresh(limit=1)
    assert [row["uid"] for row in result["emails"]] == ["3"]
    assert cache.cached_uids() == {"1", "3"}  # UID 1 is outside the fetched page.
    reopened = cache.client.get("/api/email/list", params={"account_id": "a", "cached_only": 1})
    assert {row["uid"] for row in reopened.json()["emails"]} == {"1", "3"}
    assert cache.cached_uids(owner="bob") == {"2"}
    assert cache.cached_uids(account="b") == {"2"}
    assert cache.cached_uids(folder="Archive") == {"2"}


def test_successful_empty_folder_clears_only_its_cached_rows(mailbox_cache):
    cache = mailbox_cache
    cache.mailbox.search_data = [b""]
    assert cache.refresh()["emails"] == []
    assert cache.cached_uids() == set()
    assert cache.cached_uids(owner="bob") == {"2"}
    assert cache.cached_uids(account="b") == {"2"}
    assert cache.cached_uids(folder="Archive") == {"2"}


@pytest.mark.parametrize("params", [
    {"filter": "unread"}, {"filter": "favorites"}, {"filter": "unanswered"},
    {"from": "sender@example.com"}, {"date_from": "2020-01-01"},
    {"date_to": "2026-10-23"}, {"has_attachments": 1},
])
def test_filtered_search_never_treats_omitted_uids_as_deleted(mailbox_cache, params):
    mailbox_cache.refresh(**params)
    assert mailbox_cache.cached_uids() == {"1", "2", "3"}


@pytest.mark.parametrize("status,data", [
    ("NO", [b""]), ("BAD", [b""]), ("OK", []), ("OK", [None]),
    ("OK", [b"1 BAD"]), ("OK", [b"0"]), ("OK", [b"01"]),
    ("OK", RuntimeError("connection lost")),
])
def test_failed_or_malformed_search_preserves_cached_rows(mailbox_cache, status, data):
    mailbox_cache.mailbox.search_status = status
    mailbox_cache.mailbox.search_data = data
    mailbox_cache.refresh()
    assert mailbox_cache.cached_uids() == {"1", "2", "3"}


def test_failed_folder_selection_preserves_cached_rows(mailbox_cache):
    mailbox_cache.mailbox.select_status = "NO"
    assert "error" in mailbox_cache.refresh()
    assert mailbox_cache.cached_uids() == {"1", "2", "3"}


def test_rows_added_or_updated_during_search_are_preserved(mailbox_cache):
    cache = mailbox_cache
    def newer_observation():
        generation = cache.routes._email_index_begin_sync("alice", "a", "INBOX")
        cache.put(["2", "4"], generation=generation)
    cache.mailbox.on_search = newer_observation
    cache.refresh()
    assert cache.cached_uids() == {"1", "2", "3", "4"}
    # Once a later full search also omits them, normal reconciliation resumes.
    cache.mailbox.on_search = lambda: None
    cache.refresh()
    assert cache.cached_uids() == {"1", "3"}


def test_old_writer_cannot_restore_uids_removed_by_a_newer_snapshot(mailbox_cache):
    cache = mailbox_cache
    old = cache.routes._email_index_begin_sync("alice", "a", "INBOX")
    newer = cache.routes._email_index_begin_sync("alice", "a", "INBOX")
    cache.routes._email_index_reconcile("alice", "a", "INBOX", b"1 3", newer)
    cache.put(["2"], generation=old)
    assert cache.cached_uids() == {"1", "3"}


def test_old_snapshot_cannot_remove_newer_observations(mailbox_cache):
    cache = mailbox_cache
    old = cache.routes._email_index_begin_sync("alice", "a", "INBOX")
    newer = cache.routes._email_index_begin_sync("alice", "a", "INBOX")
    cache.put(["4"], generation=newer)
    cache.routes._email_index_reconcile("alice", "a", "INBOX", b"", old)
    assert cache.cached_uids() == {"4"}


def test_old_snapshot_cannot_override_a_newer_reconciliation(mailbox_cache):
    cache = mailbox_cache
    old = cache.routes._email_index_begin_sync("alice", "a", "INBOX")
    newer = cache.routes._email_index_begin_sync("alice", "a", "INBOX")
    cache.routes._email_index_reconcile("alice", "a", "INBOX", b"1 3", newer)
    cache.routes._email_index_reconcile("alice", "a", "INBOX", b"", old)
    assert cache.cached_uids() == {"1", "3"}


def test_cache_hits_in_a_newer_filtered_read_are_live_observations(mailbox_cache):
    cache = mailbox_cache
    old = cache.routes._email_index_begin_sync("alice", "a", "INBOX")
    result = cache.refresh(refresh=0, filter="unread")
    assert {row["uid"] for row in result["emails"]} == {"1", "3"}
    cache.routes._email_index_reconcile("alice", "a", "INBOX", b"", old)
    assert cache.cached_uids() == {"1", "3"}


def test_failed_generation_allocation_skips_cache_writes(mailbox_cache, monkeypatch):
    cache = mailbox_cache
    monkeypatch.setattr(cache.routes, "_email_index_begin_sync", lambda *args: None)
    result = cache.refresh()
    assert result["emails"][0]["subject"] == "Message 3"
    rows = cache.routes._email_index_rows("alice", "a", "INBOX", ["1", "2", "3"])
    assert rows["3"]["subject"] == "Cached 3"
    assert set(rows) == {"1", "2", "3"}


def test_large_folder_reconciliation_does_not_use_a_uid_sized_sql_parameter_list(mailbox_cache):
    cache = mailbox_cache
    cache.put([str(uid) for uid in range(1, 1501)])
    cache.mailbox.search_data = [b"1 1500"]
    cache.refresh(limit=1)
    assert cache.cached_uids() == {"1", "1500"}


def test_existing_index_migration_preserves_rows_and_is_idempotent(tmp_path, monkeypatch):
    from routes.email import email_helpers as helpers, email_routes as routes

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE email_message_index (
            owner TEXT NOT NULL DEFAULT '', account_key TEXT NOT NULL DEFAULT '',
            folder TEXT NOT NULL, uid TEXT NOT NULL, message_id TEXT, subject TEXT,
            from_name TEXT, from_address TEXT, to_text TEXT, cc_text TEXT,
            date_iso TEXT, date_display TEXT, date_epoch REAL DEFAULT 0,
            size INTEGER DEFAULT 0, flags TEXT DEFAULT '',
            has_attachments INTEGER DEFAULT 0, attachment_names TEXT DEFAULT '',
            updated_at TEXT NOT NULL, PRIMARY KEY(owner, account_key, folder, uid))""")
        conn.execute("""INSERT INTO email_message_index
            (owner, account_key, folder, uid, subject, updated_at)
            VALUES ('alice', 'a', 'INBOX', '1', 'Existing message', 'before')""")
    monkeypatch.setattr(helpers, "SCHEDULED_DB", path)
    monkeypatch.setattr(routes, "SCHEDULED_DB", path)
    helpers._init_scheduled_db()
    helpers._init_scheduled_db()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT subject, sync_generation FROM email_message_index").fetchall() == [
            ("Existing message", 0)
        ]
    assert routes._email_index_begin_sync("alice", "a", "INBOX") == 1
    assert routes._email_index_begin_sync("alice", "a", "INBOX") == 2
