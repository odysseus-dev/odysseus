"""mcp email server _decode_header must not inject spaces between parts.

email.header.decode_header returns plain-text runs WITH their surrounding
whitespace (e.g. (b"Re: ", None)), so joining parts with " " produced a
double space after "Re:" on every non-ASCII subject, a spurious space in
"Name <addr>" senders, and violated RFC 2047 6.2 which requires whitespace
between two adjacent encoded-words to be dropped.
"""
import json
import sqlite3

import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


@pytest.fixture(autouse=True)
def _clear_mcp_email_owner_env(monkeypatch):
    for key in es._OWNER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    es._ACCOUNT_CACHE.clear()
    yield
    es._ACCOUNT_CACHE.clear()


def _init_accounts_db(path, rows=None):
    rows = rows or [
        ("acct-alice", "alice", "Alice Mail", 1, "alice@example.com", "alice@example.com", "alice@example.com", "2026-01-01"),
        ("acct-bob", "bob", "Bob Mail", 1, "bob@example.com", "bob@example.com", "bob@example.com", "2026-01-02"),
    ]
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE email_accounts (
            id TEXT PRIMARY KEY,
            owner TEXT,
            name TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            imap_host TEXT,
            imap_port INTEGER,
            imap_user TEXT,
            imap_password TEXT,
            imap_starttls INTEGER,
            smtp_host TEXT,
            smtp_port INTEGER,
            smtp_security TEXT,
            smtp_user TEXT,
            smtp_password TEXT,
            from_address TEXT,
            created_at TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO email_accounts
        (id, owner, name, is_default, enabled, imap_host, imap_port, imap_user,
         imap_password, imap_starttls, smtp_host, smtp_port, smtp_security,
         smtp_user, smtp_password, from_address, created_at)
        VALUES (?, ?, ?, ?, 1, 'imap.example.com', 993, ?, '', 1,
                'smtp.example.com', 465, 'ssl', ?, '', ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def test_prefix_then_encoded_word_single_space():
    assert es._decode_header("Re: =?utf-8?b?SsOzc2U=?=") == "Re: J\u00f3se"


def test_encoded_word_then_plain_text():
    assert es._decode_header("=?utf-8?b?SsOzc2U=?= Smith") == "J\u00f3se Smith"


def test_adjacent_encoded_words_join_without_space():
    out = es._decode_header("=?iso-8859-1?q?Caf=E9?= =?utf-8?b?5pel5pys?=")
    assert out == "Caf\u00e9\u65e5\u672c"


def test_plain_ascii_header_unchanged():
    assert es._decode_header("Weekly report") == "Weekly report"


def test_empty_header():
    assert es._decode_header("") == ""


@pytest.mark.asyncio
async def test_mcp_email_accounts_are_filtered_by_hidden_owner(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    _init_accounts_db(db_path)
    monkeypatch.setattr(es, "APP_DB", str(db_path))
    es._ACCOUNT_CACHE.clear()

    out = await es.call_tool("list_email_accounts", {"_odysseus_owner": "alice"})
    text = out[0].text

    assert "Alice Mail" in text
    assert "Bob Mail" not in text


@pytest.mark.asyncio
async def test_mcp_email_requires_owner_when_multiple_account_owners_exist(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    _init_accounts_db(db_path)
    monkeypatch.setattr(es, "APP_DB", str(db_path))
    es._ACCOUNT_CACHE.clear()

    out = await es.call_tool("list_email_accounts", {})

    assert "requires an authenticated owner" in out[0].text


@pytest.mark.asyncio
async def test_mcp_email_requires_owner_when_any_account_owner_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    _init_accounts_db(
        db_path,
        rows=[
            ("acct-alice", "alice", "Alice Mail", 1, "alice@example.com", "alice@example.com", "alice@example.com", "2026-01-01"),
        ],
    )
    monkeypatch.setattr(es, "APP_DB", str(db_path))

    out = await es.call_tool("list_email_accounts", {})

    assert "requires an authenticated owner" in out[0].text
    assert "Alice Mail" not in out[0].text


@pytest.mark.asyncio
async def test_mcp_email_configured_owner_filters_accounts(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    _init_accounts_db(db_path)
    monkeypatch.setattr(es, "APP_DB", str(db_path))
    monkeypatch.setenv("ODYSSEUS_MCP_EMAIL_OWNER", "alice")

    out = await es.call_tool("list_email_accounts", {})
    text = out[0].text

    assert "Alice Mail" in text
    assert "Bob Mail" not in text


def test_mcp_email_scoped_owner_without_visible_account_skips_legacy_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    settings_path = tmp_path / "settings.json"
    _init_accounts_db(db_path)
    settings_path.write_text(
        json.dumps(
            {
                "imap_host": "legacy-imap.example.com",
                "imap_user": "legacy@example.com",
                "imap_password": "legacy-secret",
                "smtp_host": "legacy-smtp.example.com",
                "smtp_user": "legacy@example.com",
                "smtp_password": "legacy-secret",
                "from_address": "legacy@example.com",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(es, "APP_DB", str(db_path))
    monkeypatch.setattr(es, "_SETTINGS_FILE", str(settings_path))
    es._ACCOUNT_CACHE.clear()

    token = es._CURRENT_OWNER.set("charlie")
    try:
        with pytest.raises(ValueError, match="No email account is configured"):
            es._load_config()
    finally:
        es._CURRENT_OWNER.reset(token)
        es._ACCOUNT_CACHE.clear()


@pytest.mark.asyncio
async def test_mcp_email_owner_cannot_use_other_owner_account_for_list_read_send_or_draft(tmp_path, monkeypatch):
    import src.constants as constants

    db_path = tmp_path / "app.db"
    scheduled_path = tmp_path / "scheduled_emails.db"
    _init_accounts_db(db_path)
    monkeypatch.setattr(es, "APP_DB", str(db_path))
    monkeypatch.setattr(constants, "SCHEDULED_EMAILS_DB", str(scheduled_path))
    monkeypatch.setattr(es, "_read_agent_email_confirm_setting", lambda: True)

    calls = [
        ("list_emails", {"account": "Bob Mail"}),
        ("read_email", {"uid": "1", "account": "Bob Mail"}),
        ("send_email", {
            "to": "recipient@example.com",
            "subject": "Blocked",
            "body": "Do not stage.",
            "account": "Bob Mail",
        }),
        ("draft_email", {
            "to": "recipient@example.com",
            "subject": "Blocked",
            "body": "Do not draft.",
            "account": "Bob Mail",
        }),
    ]

    for tool_name, args in calls:
        out = await es.call_tool(tool_name, {**args, "_odysseus_owner": "alice"})
        assert "Email account not found for selector" in out[0].text, tool_name
        assert "Bob Mail" not in out[0].text or "Available accounts" in out[0].text

    assert not scheduled_path.exists()


@pytest.mark.asyncio
async def test_mcp_send_email_stages_with_visible_owner_account_id(tmp_path, monkeypatch):
    import src.constants as constants

    app_db_path = tmp_path / "app.db"
    scheduled_path = tmp_path / "scheduled_emails.db"
    _init_accounts_db(app_db_path)
    monkeypatch.setattr(es, "APP_DB", str(app_db_path))
    monkeypatch.setattr(constants, "SCHEDULED_EMAILS_DB", str(scheduled_path))
    monkeypatch.setattr(es, "_read_agent_email_confirm_setting", lambda: True)

    out = await es.call_tool(
        "send_email",
        {
            "to": "recipient@example.com",
            "subject": "Review",
            "body": "Please review.",
            "account": "Alice Mail",
            "_odysseus_owner": "alice",
        },
    )

    assert "Draft staged for approval" in out[0].text
    conn = sqlite3.connect(scheduled_path)
    try:
        row = conn.execute(
            "SELECT owner, status, account_id FROM scheduled_emails"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("alice", "agent_draft", "acct-alice")


@pytest.mark.asyncio
async def test_mcp_send_email_stages_owner_scoped_pending_draft(tmp_path, monkeypatch):
    import src.constants as constants

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(es, "APP_DB", str(tmp_path / "missing-app.db"))
    monkeypatch.setattr(constants, "SCHEDULED_EMAILS_DB", str(db_path))
    monkeypatch.setattr(es, "_read_agent_email_confirm_setting", lambda: True)

    out = await es.call_tool(
        "send_email",
        {
            "to": "recipient@example.com",
            "subject": "Review",
            "body": "Please review.",
            "_odysseus_owner": "alice",
        },
    )

    assert "Draft staged for approval" in out[0].text
    assert "Nothing has been sent yet" in out[0].text
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT owner, status, to_addr, subject FROM scheduled_emails"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("alice", "agent_draft", "recipient@example.com", "Review")


@pytest.mark.asyncio
async def test_mcp_draft_email_document_uses_hidden_owner(monkeypatch):
    import core.database as db_mod

    saved = []

    class FakeDocument:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeDocumentVersion:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeDb:
        def add(self, obj):
            saved.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(db_mod, "Document", FakeDocument)
    monkeypatch.setattr(db_mod, "DocumentVersion", FakeDocumentVersion)
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(
        es,
        "_load_config",
        lambda account=None: {"account_name": "Alice Mail", "account_id": "acct-alice"},
    )

    out = await es.call_tool(
        "draft_email",
        {
            "to": "recipient@example.com",
            "subject": "Draft subject",
            "body": "Draft body",
            "_odysseus_owner": "alice",
        },
    )

    assert "Created Odysseus email draft" in out[0].text
    docs = [obj for obj in saved if isinstance(obj, FakeDocument)]
    assert len(docs) == 1
    assert docs[0].owner == "alice"
