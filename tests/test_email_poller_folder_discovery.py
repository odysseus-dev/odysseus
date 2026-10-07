"""Exercise folder discovery and selection through a real background pass."""

import sqlite3

import pytest

from routes import email_helpers, email_pollers


SENT_FOLDER = "[GoogleMail]/Gesendet"


class FolderConnection:
    def __init__(self, *, reject_sent_scan=False, reject_sent_fetch=False,
                 reject_inbox_reset=False, list_status="OK"):
        self.reject_sent_scan = reject_sent_scan
        self.reject_sent_fetch = reject_sent_fetch
        self.reject_inbox_reset = reject_inbox_reset
        self.list_status = list_status
        self.selected = None
        self.select_counts = {}
        self.calls = []
        self.logout_calls = 0

    def list(self):
        self.calls.append(("LIST",))
        return self.list_status, [
            rb'(\HasNoChildren) "/" "INBOX"',
            rb'(\Noselect \HasChildren) "/" "[GoogleMail]"',
            rb'(\HasNoChildren \Sent) "/" "[GoogleMail]/Gesendet"',
            rb'() "/" "Sent invoices"',
        ]

    def select(self, mailbox, readonly=False):
        name = mailbox.strip('"')
        count = self.select_counts.get(name, 0) + 1
        self.select_counts[name] = count
        self.calls.append(("SELECT", name, mailbox, readonly))
        if name == SENT_FOLDER and (
            (count == 1 and self.reject_sent_scan)
            or (count > 1 and self.reject_sent_fetch)
        ):
            return "NO", [b"permission denied"]
        if mailbox == "INBOX" and self.reject_inbox_reset:
            return "NO", [b"permission denied"]
        assert name in {"INBOX", SENT_FOLDER}, f"invented mailbox selected: {name}"
        self.selected = name
        return "OK", []

    def uid(self, command, *args):
        self.calls.append((command, self.selected, *args))
        uid = b"11" if self.selected == "INBOX" else b"77"
        if command == "SEARCH":
            return "OK", [uid]
        if command == "FETCH":
            # Assert the UID is looked up in the mailbox in which it was found.
            assert args[0] == uid, f"UID from a different folder fetched in {self.selected}"
            raw = (
                b"From: Writer <writer@example.com>\r\n"
                b"To: Reader <reader@example.com>\r\n"
                b"Subject: Meeting confirmation\r\n"
                b"Message-ID: <message-" + uid + b"@example.com>\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
                b"We can meet next week."
            )
            return "OK", [(b"1 (RFC822)", raw)]
        raise AssertionError(f"unexpected command: {command}")

    def logout(self):
        self.logout_calls += 1


def configure_pass(monkeypatch, tmp_path, conn, *, cached=True):
    db_path = tmp_path / "scheduled.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_pollers, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()
    if cached:
        with sqlite3.connect(db_path) as db:
            for uid in ("11", "77"):
                db.execute(
                    "INSERT INTO email_calendar_extractions (message_id, owner, uid, created_at) VALUES (?, ?, ?, ?)",
                    (f"<message-{uid}@example.com>", "alice", uid, "2026-01-01T00:00:00"),
                )
    monkeypatch.setattr(email_pollers, "_load_settings", lambda: {"email_auto_calendar": True})
    monkeypatch.setattr(email_pollers, "_owner_for_email_account", lambda _account: "alice")

    def connect(account_id=None, owner=""):
        assert account_id == "account-a"
        assert owner == "alice"
        return conn

    monkeypatch.setattr(email_pollers, "_imap_connect", connect)
    monkeypatch.setattr(email_pollers, "_get_email_config", lambda *a, **k: {"from_address": "alice@example.com"})
    monkeypatch.setattr(email_pollers, "resolve_task_candidates", lambda **kwargs: [("http://unused.invalid", "model", {})])
    return db_path


@pytest.mark.asyncio
async def test_background_pass_discovers_localized_sent_and_marks_direction(monkeypatch, tmp_path):
    from core import database

    conn = FolderConnection()
    db_path = configure_pass(monkeypatch, tmp_path, conn, cached=False)
    monkeypatch.setattr(database, "get_upcoming_events", lambda *a, **k: [])
    prompts = []

    async def extract(**kwargs):
        assert kwargs["owner"] == "alice"
        prompts.append(kwargs["messages"][1]["content"])
        return "[]"

    monkeypatch.setattr(email_pollers, "task_llm_call_async", extract)
    result = await email_pollers._auto_summarize_pass_single(account_id="account-a")

    assert "processed 2 new" in result
    assert any(f"EMAIL_FOLDER: {SENT_FOLDER} (sent by user)" in prompt for prompt in prompts)
    assert any("EMAIL_FOLDER: INBOX (received)" in prompt for prompt in prompts)
    assert [(call[1], call[2]) for call in conn.calls if call[0] == "FETCH"] == [
        ("INBOX", b"11"), (SENT_FOLDER, b"77"),
    ]
    assert conn.logout_calls == 1
    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT uid, owner FROM email_calendar_extractions ORDER BY uid").fetchall()
    assert rows == [("11", "alice"), ("77", "alice")]


@pytest.mark.asyncio
async def test_rejected_sent_select_does_not_search_the_previous_mailbox(monkeypatch, tmp_path):
    conn = FolderConnection(reject_sent_scan=True)
    configure_pass(monkeypatch, tmp_path, conn)
    result = await email_pollers._auto_summarize_pass_single(account_id="account-a")

    assert "Scanned 1 email(s)" in result
    assert [call[1] for call in conn.calls if call[0] == "SEARCH"] == ["INBOX"]
    assert [call[1] for call in conn.calls if call[0] == "FETCH"] == ["INBOX"]
    assert conn.logout_calls == 1


@pytest.mark.asyncio
async def test_rejected_sent_reselect_does_not_fetch_uid_from_previous_mailbox(monkeypatch, tmp_path):
    conn = FolderConnection(reject_sent_fetch=True)
    configure_pass(monkeypatch, tmp_path, conn)
    await email_pollers._auto_summarize_pass_single(account_id="account-a")

    assert [call[1] for call in conn.calls if call[0] == "SEARCH"] == ["INBOX", SENT_FOLDER]
    assert [call[1] for call in conn.calls if call[0] == "FETCH"] == ["INBOX"]
    assert conn.logout_calls == 1


@pytest.mark.asyncio
async def test_first_fetch_reselects_inbox_after_sent_scan_even_if_reset_failed(monkeypatch, tmp_path):
    conn = FolderConnection(reject_inbox_reset=True)
    configure_pass(monkeypatch, tmp_path, conn)
    result = await email_pollers._auto_summarize_pass_single(account_id="account-a")

    assert "2 already cached" in result
    first_fetch = next(index for index, call in enumerate(conn.calls) if call[0] == "FETCH")
    assert conn.calls[first_fetch - 1][:3] == ("SELECT", "INBOX", '"INBOX"')
    assert conn.calls[first_fetch][1:3] == ("INBOX", b"11")
    assert conn.logout_calls == 1


@pytest.mark.asyncio
async def test_failed_list_scans_inbox_without_guessing_sent_names(monkeypatch, tmp_path):
    conn = FolderConnection(list_status="NO")
    configure_pass(monkeypatch, tmp_path, conn)
    result = await email_pollers._auto_summarize_pass_single(account_id="account-a")

    assert "Scanned 1 email(s)" in result
    assert all(call[1] == "INBOX" for call in conn.calls if call[0] == "SELECT")
    assert conn.logout_calls == 1


def test_latest_inbox_fallback_does_not_search_when_select_is_rejected():
    conn = FolderConnection(reject_inbox_reset=True)
    conn.selected = SENT_FOLDER
    fresh = FolderConnection()
    uids, _ = email_pollers._latest_inbox_fallback_uids(conn, lambda: fresh)

    assert uids == []
    assert not any(call[0] == "SEARCH" for call in conn.calls)
