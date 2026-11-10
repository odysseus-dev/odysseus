import sqlite3

import mcp_servers.email_server as es


def test_indexed_search_emails_uses_owner_visible_account_index(monkeypatch, tmp_path):
    db_path = tmp_path / "scheduled_emails.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE email_message_index (
            owner TEXT,
            account_key TEXT,
            folder TEXT,
            uid TEXT,
            message_id TEXT,
            subject TEXT,
            from_name TEXT,
            from_address TEXT,
            to_text TEXT,
            cc_text TEXT,
            date_iso TEXT,
            date_display TEXT,
            date_epoch REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO email_message_index VALUES (
            'alice', 'acct-1', 'INBOX', '42', '<m42>', 'Your Runpod receipt',
            'Runpod', 'support@runpod.io', 'alice@example.com', '',
            '2026-08-20T06:28:39+00:00', 'Thu, 20 Aug 2026 06:28:39 +0000', 10
        )
        """
    )
    conn.execute(
        """
        INSERT INTO email_message_index VALUES (
            'bob', 'acct-1', 'INBOX', '99', '<m99>', 'Your Runpod receipt',
            'Runpod', 'support@runpod.io', 'bob@example.com', '',
            '2026-08-20T06:28:39+00:00', 'Thu, 20 Aug 2026 06:28:39 +0000', 11
        )
        """
    )
    conn.commit()
    conn.close()

    owner_token = es._CURRENT_OWNER.set("alice")
    monkeypatch.setattr(es, "SCHEDULED_EMAILS_DB", str(db_path))
    monkeypatch.setattr(
        es,
        "_list_accounts_raw",
        lambda: [{"id": "acct-1", "name": "Work", "imap_user": "alice@example.com"}],
    )
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})
    try:
        hits = es._indexed_search_emails("Runpod", max_results=10)
    finally:
        es._CURRENT_OWNER.reset(owner_token)

    assert hits is not None
    assert [hit["uid"] for hit in hits] == ["42"]
    assert hits[0]["_account"] == "Work"
    assert hits[0]["_source"] == "index"


def test_search_emails_falls_back_when_index_misses(monkeypatch):
    monkeypatch.setattr(es, "_fixture_search_emails", lambda *args, **kwargs: None)
    monkeypatch.setattr(es, "_indexed_search_emails", lambda *args, **kwargs: [])
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})

    calls = []

    class Conn:
        def select(self, *_args, **_kwargs):
            return "OK", []

        def uid(self, *_args, **_kwargs):
            calls.append(_args)
            return "OK", [b""]

        def logout(self):
            pass

    monkeypatch.setattr(es, "_imap_connect", lambda account=None: Conn())

    assert es._search_emails("missing", folders=["INBOX"], max_results=1) == []
    assert calls


def test_fixture_account_selector_accepts_display_label_with_email(monkeypatch):
    row = {
        "account": "Research Mail",
        "account_email": "alex.research@rowan.studio",
        "account_id": "research-mail",
    }

    assert es._fixture_row_matches_account(row, "Research Mail (alex.research@rowan.studio)")


def test_fixture_account_selector_accepts_primary_and_default_aliases():
    row = {
        "account": "Primary Inbox",
        "account_email": "alex@example.com",
        "account_id": "primary-inbox",
    }

    assert es._fixture_row_matches_account(row, "primary")
    assert es._fixture_row_matches_account(row, "default")
